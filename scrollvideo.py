#!/usr/bin/env python3
"""
scrollvideo.py - turn a video into a scroll-controlled website preview.

    Scroll down  ->  the video plays forward
    Scroll up    ->  the video plays backward

HOW TO USE
    1.  pip install opencv-python
    2.  python scrollvideo.py
    3.  In the page that opens:
          - drop your video and choose scroll length, smoothness and quality
          - press "Create scroll website"  -> your video opens, controlled by scrolling
          - press "Download frames" (top right) to save all extracted frames to a folder.

HOW THE NUMBER OF FRAMES IS DECIDED
    frames = scroll length (px) / px-per-frame  (px-per-frame comes from smoothness)
    ...but never more than
      * the frames your video actually has, and
      * what a browser can hold in memory at the chosen resolution.

COMMAND LINE (all optional)
    python scrollvideo.py my_video.mp4 --screens 8 --smoothness 4 --width 1920
    python scrollvideo.py --port 9000 --no-browser
"""

import argparse
import atexit
import gc
import http.server
import json
import os
import re
import shutil
import signal
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

try:
    import cv2
except ImportError:
    sys.exit("Missing dependency. Run:  pip install opencv-python")

try:
    from PIL import Image
except ImportError:
    Image = None

UPLOAD_DIR = Path(tempfile.gettempdir()) / "scrollvideo_upload"

# ---- planning constants (shared with the web page) -----------------------
# smoothness level -> (name, scroll pixels per frame, easing strength)
SMOOTH = [
    ("Light",    20, 0.30),
    ("Balanced", 14, 0.22),
    ("Smooth",   10, 0.16),
    ("Silky",     7, 0.11),
    ("Ultra",     5, 0.08),
]
REF_VH = 900                  # reference screen height used to turn "screens" into pixels
MEM_BUDGET = 3_000_000_000    # bytes of decoded images we allow a browser to hold
MAX_FRAMES = 600
MIN_FRAMES = 2
WIDTHS = (1280, 1920, 2560, 3840)
VIDEO_EXT = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v")
FRAME_RE = re.compile(r"frame_\d{4}\.(?:webp|jpg|jpeg|png|avif)")

DEFAULTS = {"screens": 20, "smoothness": 50, "width": 1920, "format": "webp",
            "compression": 82, "frames": 0, "fit": "cover"}

JOB = {"state": "idle", "pct": 0, "message": "", "result": None}      # building the preview
SAVE = {"state": "idle", "pct": 0, "message": "", "folder": None,      # downloading frames
        "count": 0, "mb": 0}
LOCK = threading.Lock()
PROBE = {}                    # info about the uploaded video
UPLOADED = {"path": None}
PREVIEW = {"dir": None, "cfg": None}     # temporary frames used by the preview (not saved anywhere else)
LAST_SAVED = {"folder": None}
PICK_LOCK = threading.Lock()


def set_job(**kw):
    with LOCK:
        JOB.update(kw)


def set_save(**kw):
    with LOCK:
        SAVE.update(kw)


def cleanup_all():
    """Wipe all temporary frames, upload folders, and free all RAM/disk storage."""
    with LOCK:
        shutil.rmtree(UPLOAD_DIR, ignore_errors=True)
        if PREVIEW.get("dir"):
            shutil.rmtree(PREVIEW["dir"], ignore_errors=True)
            PREVIEW["dir"] = None
            PREVIEW["cfg"] = None
        # Clean up any lingering scrollvideo_frames_* in tempdir
        temp_dir = Path(tempfile.gettempdir())
        for d in temp_dir.glob("scrollvideo_frames_*"):
            try:
                shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass
        PROBE.clear()
        UPLOADED["path"] = None
        JOB.update(state="idle", pct=0, message="", result=None)
        SAVE.update(state="idle", pct=0, message="", folder=None, count=0, mb=0)
        gc.collect()


def _sig_handler(signum, frame):
    cleanup_all()
    sys.exit(0)


try:
    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)
except Exception:
    pass

atexit.register(cleanup_all)

# Windows Console Close event handler (guarantees cleanup when CMD/PowerShell window is closed)
if sys.platform.startswith("win"):
    try:
        import ctypes
        HandlerRoutine = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong)
        def _console_ctrl_handler(ctrl_type):
            cleanup_all()
            return False
        _global_ctrl_handler = HandlerRoutine(_console_ctrl_handler)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_global_ctrl_handler, True)
    except Exception:
        pass


# --------------------------------------------------------------------------
# 1. Planning: how many frames, how big?   (same maths is mirrored in the page)
# --------------------------------------------------------------------------
def calc_smooth(val):
    """Interpolate ppf and ease from a smoothness value (1 to 100, or legacy 1 to 5)."""
    try:
        val = float(val)
    except (TypeError, ValueError):
        val = 50.0
    if val <= 5.0:
        L = min(5.0, max(1.0, val))
        val_pct = int(round(1 + (L - 1) * 99 / 4))
    else:
        val_pct = int(round(min(100.0, max(1.0, val))))
        L = 1.0 + (val_pct - 1) * 4.0 / 99.0

    points = [
        ("Light",    20.0, 0.30),
        ("Balanced", 14.0, 0.22),
        ("Smooth",   10.0, 0.16),
        ("Silky",     7.0, 0.11),
        ("Ultra",     5.0, 0.08),
    ]
    idx = int(L) - 1
    if idx >= 4:
        ppf = points[4][1]
        ease = points[4][2]
    else:
        frac = L - (idx + 1)
        ppf = points[idx][1] + frac * (points[idx + 1][1] - points[idx][1])
        ease = points[idx][2] + frac * (points[idx + 1][2] - points[idx][2])

    closest_idx = int(round(L)) - 1
    closest_idx = max(0, min(4, closest_idx))
    closest_name = points[closest_idx][0]
    return closest_name, ppf, ease, val_pct


def make_plan(probe, o):
    name, ppf, ease, smooth_pct = calc_smooth(o.get("smoothness", 50))
    out_w = min(o["width"], probe["width"])
    out_w -= out_w % 2
    out_h = int(probe["height"] * out_w / probe["width"] + 0.5)
    out_h -= out_h % 2

    scroll_px = o["screens"] * REF_VH
    want = o["frames"] if o["frames"] else int(scroll_px / ppf + 0.5)
    mem_cap = max(60, min(MAX_FRAMES, MEM_BUDGET // (out_w * out_h * 4)))

    count, notes = min(want, probe["frames"], mem_cap), []
    if mem_cap < min(want, probe["frames"]):          # memory is the binding limit
        notes.append(f"Optimized for smooth playback: Using {mem_cap} frames so the page stays fast and fluid.")
    elif probe["frames"] < want:                      # the video itself is the binding limit
        notes.append(f"Using all {probe['frames']} available frames from your video clip.")
    count = max(MIN_FRAMES, count)

    if o["format"] == "avif":
        bpp = 0.06 * (o["compression"] / 80)
    elif o["format"] == "webp":
        bpp = 0.09 * (o["compression"] / 80)
    elif o["format"] == "jpg":
        bpp = 0.13 * (o["compression"] / 80)
    else:  # png
        bpp = 0.50
    est_mb = count * out_w * out_h * bpp / 1_048_576
    return {"count": count, "width": out_w, "height": out_h, "ppf": scroll_px / count,
            "est_mb": est_mb, "notes": notes, "ease": ease, "smooth_name": name,
            "smooth_pct": smooth_pct}


def clean_opts(d):
    """Validate options coming from the web page / command line."""
    def num(key, cast, lo, hi):
        try:
            return min(hi, max(lo, cast(d.get(key, DEFAULTS[key]))))
        except (TypeError, ValueError):
            return DEFAULTS[key]

    o = dict(DEFAULTS)
    o["screens"] = num("screens", int, 2, 1000)
    o["smoothness"] = num("smoothness", float, 1.0, 100.0)
    w = num("width", int, 320, 3840)
    o["width"] = min(WIDTHS, key=lambda x: abs(x - w)) if w not in WIDTHS else w
    fmt = str(d.get("format", DEFAULTS["format"])).lower()
    o["format"] = fmt if fmt in ("webp", "jpg", "png", "avif") else DEFAULTS["format"]
    o["compression"] = num("compression", int, 40, 100)
    o["frames"] = 0 if not d.get("frames") else num("frames", int, MIN_FRAMES, 5000)
    o["fit"] = "contain" if d.get("fit") == "contain" else "cover"
    return o


# --------------------------------------------------------------------------
# 2. Video probing + frame extraction
# --------------------------------------------------------------------------
def probe_video(path):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError("Could not open this video. Try an MP4 (H.264) file.")
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    if fps < 1 or fps > 240:
        fps = 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if total <= 1 or total > 10_000_000:       # unreliable metadata: count by reading
        total = 0
        while cap.grab():
            total += 1
    cap.release()
    if total < 2 or w <= 0 or h <= 0:
        raise RuntimeError("The video looks empty or unreadable. Try converting it to MP4.")
    return {"width": w, "height": h, "fps": round(fps, 2), "frames": total,
            "duration": round(total / fps, 2)}


def write_single_frame(path, frame, ext, quality):
    """Write an image frame using OpenCV or Pillow for jpg/png/webp/avif."""
    if ext == "avif":
        if Image:
            try:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                Image.fromarray(rgb).save(str(path), format="AVIF", quality=quality)
                return True
            except Exception:
                pass
        return False
    elif ext == "png":
        comp = min(9, max(1, int((100 - quality) / 10 + 1)))
        ok = cv2.imwrite(str(path), frame, [cv2.IMWRITE_PNG_COMPRESSION, comp])
        if not ok and Image:
            try:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                Image.fromarray(rgb).save(str(path), format="PNG")
                ok = True
            except Exception:
                pass
        return ok
    elif ext == "webp":
        ok = False
        try:
            ok = cv2.imwrite(str(path), frame, [cv2.IMWRITE_WEBP_QUALITY, quality])
        except cv2.error:
            pass
        if not ok and Image:
            try:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                Image.fromarray(rgb).save(str(path), format="WEBP", quality=quality)
                ok = True
            except Exception:
                pass
        return ok
    else:  # jpg
        ok = False
        try:
            ok = cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        except cv2.error:
            pass
        if not ok and Image:
            try:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                Image.fromarray(rgb).save(str(path), format="JPEG", quality=quality)
                ok = True
            except Exception:
                pass
        return ok


def extract_frames(video_path, frames_dir, probe, plan, o):
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError("Could not open the video again. Please upload it once more.")

    total, count = probe["frames"], plan["count"]
    wanted = sorted({min(total - 1, int(i * (total - 1) / (count - 1) + 0.5)) for i in range(count)})
    wanted_set = set(wanted)

    ext = o["format"]

    saved, idx = 0, 0
    while True:
        if not cap.grab():
            break
        if idx in wanted_set:
            ok, frame = cap.retrieve()
            if not ok:
                idx += 1
                continue
            if plan["width"] != probe["width"]:
                frame = cv2.resize(frame, (plan["width"], plan["height"]), interpolation=cv2.INTER_AREA)
            target = frames_dir / f"frame_{saved + 1:04d}.{ext}"
            wrote = write_single_frame(target, frame, ext, o["compression"])

            # Fallback to jpg if the chosen format failed to write
            if not wrote and ext != "jpg":
                ext = "jpg"
                target = frames_dir / f"frame_{saved + 1:04d}.jpg"
                wrote = write_single_frame(target, frame, "jpg", o["compression"])

            if not wrote:
                raise RuntimeError("Could not process frames (disk full or write error).")
            saved += 1
            set_job(pct=int(saved / len(wanted) * 100),
                    message=f"Preparing frames… {saved} / {len(wanted)}")
            if saved == len(wanted):
                break
        idx += 1
    cap.release()
    if saved < 2:
        raise RuntimeError("Could not read frames from this video.")
    return saved, ext


# --------------------------------------------------------------------------
# 3. Build the preview (frames live in a temporary folder only)
# --------------------------------------------------------------------------
def build_preview(video_path, probe, o):
    new_dir = None
    try:
        set_job(state="processing", pct=0, message="Starting…", result=None)
        plan = make_plan(probe, o)
        new_dir = Path(tempfile.mkdtemp(prefix="scrollvideo_frames_"))
        saved, ext = extract_frames(video_path, new_dir, probe, plan, o)
        size = sum(f.stat().st_size for f in new_dir.glob("frame_*"))
        cfg = {"count": saved, "ext": ext, "prefix": "frame_", "pad": 4,
               "width": plan["width"], "height": plan["height"], "screens": o["screens"],
               "smoothness": o["smoothness"],
               "ease": plan["ease"], "fit": o["fit"], "v": int(time.time()),
               "mb": round(size / 1_048_576, 1)}
        old = PREVIEW["dir"]
        PREVIEW.update(dir=new_dir, cfg=cfg)
        if old:
            shutil.rmtree(old, ignore_errors=True)
        set_job(state="done", pct=100, message="Done!", result=cfg)
    except Exception as e:
        if new_dir:
            shutil.rmtree(new_dir, ignore_errors=True)
        set_job(state="error", message=str(e))


# --------------------------------------------------------------------------
# 4. "Download frames": native folder window -> copy frames there
# --------------------------------------------------------------------------
TK_SCRIPT = (
    "import tkinter as tk\n"
    "from tkinter import filedialog\n"
    "r = tk.Tk(); r.withdraw()\n"
    "try: r.attributes('-topmost', True)\n"
    "except Exception: pass\n"
    "print(filedialog.askdirectory(title='Choose where to save the frames', mustexist=True) or '')\n"
)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW)


def pick_folder():
    """Open the computer's own folder-chooser. Returns ("ok", path) | ("cancel", None) | ("unavailable", None)."""
    with PICK_LOCK:
        attempts = []
        is_frozen = getattr(sys, "frozen", False)
        if sys.platform.startswith("win"):
            # PowerShell folder dialog with TopMost window so it always pops up in the foreground
            ps_script = (
                "$ErrorActionPreference = 'Stop'; "
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
                "$f.Description = 'Choose where to save the frames'; "
                "$f.ShowNewFolderButton = $true; "
                "$top = New-Object System.Windows.Forms.Form; "
                "$top.TopMost = $true; "
                "if ($f.ShowDialog($top) -eq [System.Windows.Forms.DialogResult]::OK) { "
                "  [Console]::Out.WriteLine($f.SelectedPath) "
                "} else { [Console]::Out.WriteLine('') }"
            )
            attempts.append(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-STA", "-Command", ps_script])
            if not is_frozen:
                attempts.append([sys.executable, "-c", TK_SCRIPT])
        elif sys.platform == "darwin":
            attempts.append(["osascript", "-e",
                             'POSIX path of (choose folder with prompt "Choose where to save the frames")'])
        else:
            attempts.append(["zenity", "--file-selection", "--directory",
                             "--title=Choose where to save the frames"])
            attempts.append(["kdialog", "--getexistingdirectory", str(Path.home())])

        for cmd in attempts:
            try:
                r = _run(cmd)
            except (OSError, subprocess.SubprocessError):
                continue
            out = (r.stdout or "").strip()
            if r.returncode == 0:
                return ("ok", out) if out else ("cancel", None)
            if "-128" in (r.stderr or "") or (cmd[0] in ("zenity", "kdialog") and r.returncode == 1):
                return ("cancel", None)           # user pressed Cancel
        return ("unavailable", None)


def unique_target(base):
    t = base / "frames"
    n = 2
    while t.exists() and any(t.iterdir()):
        t = base / f"frames_{n}"
        n += 1
    return t


def download_frames_job():
    try:
        set_save(state="picking", pct=0, message="Choose a folder…", folder=None)
        status, path = pick_folder()
        if status == "cancel":
            return set_save(state="cancelled")
        if status == "unavailable":
            return set_save(state="unavailable")

        src = PREVIEW["dir"]
        files = sorted(src.glob("frame_*")) if src else []
        if not files:
            return set_save(state="error", message="There are no frames to save. Create the website again.")
        base = Path(path)
        if not base.is_dir():
            return set_save(state="error", message="That folder does not exist.")
        target = unique_target(base)
        target.mkdir(parents=True, exist_ok=True)

        total = 0
        for i, f in enumerate(files, 1):
            shutil.copyfile(f, target / f.name)
            total += f.stat().st_size
            set_save(state="saving", pct=int(i / len(files) * 100), message=f"Saving frames… {i} / {len(files)}",
                     count=len(files))
        LAST_SAVED["folder"] = str(target)
        set_save(state="done", pct=100, folder=str(target), count=len(files), mb=round(total / 1_048_576, 1))
    except OSError as e:
        set_save(state="error", message=f"Could not save the frames: {e.strerror or e}")
    except Exception as e:
        set_save(state="error", message=str(e))


def open_folder(path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))                              # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# 5. Pages
# --------------------------------------------------------------------------
UPLOAD_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8" /><meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Scroll Video Maker</title>
<style>
  :root { --bg:#0b0b0d; --fg:#f4f4f5; --card:#15151a; --line:#2a2a32; --mut:#9a9aa5; --acc:#fff; --green:#4ade80; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg) radial-gradient(900px 420px at 50% -8%, #1f1f33 0%, transparent 70%) no-repeat;
         color:var(--fg); font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
         min-height:100vh; display:grid; place-items:center; padding:28px 16px; }
  /* Fullscreen 3D Canvas Background */
  .bg-canvas {
    position: fixed;
    inset: 0;
    width: 100vw;
    height: 100vh;
    pointer-events: none;
    z-index: 0;
    opacity: 0.85;
  }
  main {
    position: relative;
    z-index: 1;
    width: min(640px, 100%);
  }
  
  /* Header Stage */
  .app-header {
    position: relative;
    padding: 24px 16px 18px;
    margin-bottom: 14px;
    text-align: center;
    perspective: 900px;
  }
  .header-content {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
  }

  /* 3D Holographic Floating Logo */
  .logo-3d-wrap {
    position: relative;
    width: 58px;
    height: 58px;
    margin-bottom: 14px;
    perspective: 600px;
    cursor: pointer;
  }
  .logo-glow {
    position: absolute;
    inset: -8px;
    border-radius: 20px;
    background: radial-gradient(circle, rgba(99, 102, 241, 0.6) 0%, rgba(168, 85, 247, 0.3) 50%, transparent 70%);
    filter: blur(12px);
    animation: pulseLogoGlow 3s ease-in-out infinite alternate;
  }
  @keyframes pulseLogoGlow {
    0% { transform: scale(0.9); opacity: 0.5; }
    100% { transform: scale(1.25); opacity: 0.95; }
  }
  .logo-box {
    position: relative;
    width: 100%;
    height: 100%;
    background: #ffffff;
    color: #0b0b0d;
    border-radius: 16px;
    display: grid;
    place-items: center;
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5), 0 0 20px rgba(255, 255, 255, 0.3);
    transform-style: preserve-3d;
    transition: transform 0.2s cubic-bezier(0.16, 1, 0.3, 1), box-shadow 0.2s;
    animation: floatLogo 4s ease-in-out infinite;
    overflow: hidden;
  }
  @keyframes floatLogo {
    0%, 100% { transform: translateY(0) rotateX(0deg) rotateY(0deg); }
    50% { transform: translateY(-6px) rotateX(6deg) rotateY(-6deg); }
  }
  .logo-sheen {
    position: absolute;
    top: 0; left: -100%; width: 100%; height: 100%;
    background: linear-gradient(90deg, transparent, rgba(255,255,255,0.8), transparent);
    transform: skewX(-25deg);
    animation: sheenPass 4s infinite 1.5s;
  }
  @keyframes sheenPass {
    0%, 75% { left: -100%; }
    100% { left: 200%; }
  }
  .logo-ring {
    position: absolute;
    inset: -4px;
    border-radius: 20px;
    border: 1.5px solid rgba(168, 85, 247, 0.4);
    opacity: 0.8;
    animation: spinRing 12s linear infinite;
    pointer-events: none;
  }
  @keyframes spinRing {
    0% { transform: rotate(0deg); }
    100% { transform: rotate(360deg); }
  }

  /* Badge */
  .hero-badge {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 4px 12px;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.12);
    box-shadow: 0 2px 10px rgba(0, 0, 0, 0.3);
    margin-bottom: 10px;
    backdrop-filter: blur(8px);
    transition: all 0.2s;
  }
  .hero-badge:hover {
    background: rgba(255, 255, 255, 0.09);
    border-color: rgba(168, 85, 247, 0.4);
    transform: translateY(-1px);
  }
  .live-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: #4ade80;
    box-shadow: 0 0 8px #4ade80;
    animation: blinkDot 2s ease-in-out infinite;
  }
  @keyframes blinkDot {
    0%, 100% { opacity: 0.6; transform: scale(0.9); }
    50% { opacity: 1; transform: scale(1.3); }
  }
  .badge-text {
    font-size: 0.76rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    color: #e4e4e7;
    text-transform: uppercase;
  }

  /* Title with iridescent shine */
  .hero-title {
    font-size: 2.25rem;
    font-weight: 800;
    letter-spacing: -0.035em;
    margin-bottom: 8px;
    background: linear-gradient(115deg, #ffffff 0%, #ffffff 35%, #a5b4fc 50%, #f472b6 65%, #ffffff 80%, #ffffff 100%);
    background-size: 250% 100%;
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    animation: titleShine 6s ease-in-out infinite;
    filter: drop-shadow(0 4px 20px rgba(99, 102, 241, 0.25));
  }
  @keyframes titleShine {
    0%, 100% { background-position: 0% 50%; }
    50% { background-position: 100% 50%; }
  }

  /* Subtitle */
  .hero-sub {
    color: var(--mut);
    font-size: 0.98rem;
    line-height: 1.5;
    max-width: 500px;
    margin: 0 auto;
  }

  .card { background:var(--card); border:1px solid var(--line); border-radius:18px; padding:22px; box-shadow:0 20px 50px rgba(0,0,0,.35); transition:transform .3s ease; }
  [hidden] { display:none !important; }

  #drop { border:2px dashed #34343f; border-radius:20px; padding:52px 20px; text-align:center;
          background:var(--card); cursor:pointer; transition:.2s; }
  #drop.over, #drop:hover { border-color:var(--acc); background:#1a1a22; transform:translateY(-1px); }
  #drop svg { width:46px; height:46px; margin-bottom:10px; opacity:.9; }
  #drop .t { font-weight:600; font-size:1.05rem; }
  #drop small { display:block; color:var(--mut); margin-top:6px; }

  .file { display:flex; justify-content:space-between; align-items:center; gap:14px; margin-bottom:8px; padding-bottom:16px; border-bottom:1px solid var(--line); flex-wrap:wrap; }
  .file-lead { display:flex; align-items:center; gap:12px; min-width:0; }
  .video-icon { width:40px; height:40px; border-radius:10px; background:#22222e; display:grid; place-items:center; color:#fff; flex-shrink:0; border:1px solid #363647; }
  .file b { word-break:break-all; font-size:1rem; }
  .finfo { color:var(--mut); font-size:.85rem; margin-top:3px; }
  
  .btn-change {
    display:inline-flex; align-items:center; gap:8px;
    background:#252533; border:1px solid #4a4a60; color:#ffffff;
    padding:9px 16px; border-radius:10px; font-weight:600; font-size:.88rem;
    cursor:pointer; white-space:nowrap; transition:all .18s ease;
    box-shadow:0 2px 8px rgba(0,0,0,.3);
  }
  .btn-change:hover {
    background:#333346; border-color:#818cf8; color:#fff;
    transform:translateY(-1px); box-shadow:0 4px 14px rgba(99,102,241,.25);
  }
  .btn-change:active { transform:scale(.98); }

  .field { margin-top:20px; }
  .field .row { display:flex; justify-content:space-between; align-items:center; gap:10px; }
  .field label { font-weight:600; font-size:.95rem; display:flex; align-items:center; gap:8px; }
  .field .val { color:var(--acc); font-size:.92rem; font-weight:600; }
  .field .hint { color:var(--mut); font-size:.8rem; margin-top:5px; line-height:1.4; }
  
  /* Seamless screens display right on the main background */
  .screens-display {
    display:flex; align-items:baseline; gap:5px;
    background:transparent; border:none; padding:0;
  }
  .seamless-num {
    background:transparent !important; border:none !important;
    border-radius:0 !important; color:#ffffff !important;
    font-size:1.15rem !important; font-weight:700 !important;
    text-align:right !important; width:auto !important; max-width:65px !important;
    padding:0 !important; margin:0 !important; outline:none !important;
    box-shadow:none !important; cursor:text;
  }
  .seamless-num:hover, .seamless-num:focus {
    text-shadow:0 0 10px rgba(255,255,255,0.4);
  }
  .unit-text { color:var(--mut); font-size:.92rem; font-weight:500; }

  .scroll-limit-tip {
    margin-top: 10px;
    padding: 10px 14px;
    border-radius: 10px;
    background: rgba(99, 102, 241, 0.22);
    border: 1px solid rgba(165, 180, 252, 0.55);
    color: #fff;
    font-size: 0.86rem;
    font-weight: 500;
    line-height: 1.45;
    display: flex;
    align-items: center;
    gap: 10px;
    box-shadow: 0 4px 18px rgba(99, 102, 241, 0.28);
    animation: fadeInTip 0.25s ease-out;
  }
  .scroll-limit-tip .tip-icon { font-size: 1.15rem; flex-shrink: 0; line-height: 1; }
  @keyframes fadeInTip {
    from { opacity: 0; transform: translateY(-4px); }
    to { opacity: 1; transform: translateY(0); }
  }

  input[type=range] { width:100%; accent-color:#fff; margin-top:10px; }
  
  /* Smoothness continuous scale */
  .smooth-scale {
    display: flex;
    justify-content: space-between;
    margin-top: 6px;
    padding: 0 4px;
    font-size: 0.76rem;
    color: var(--mut);
    user-select: none;
  }
  .smooth-scale .scale-label {
    cursor: pointer;
    transition: color .15s ease, transform .15s ease, text-shadow .15s ease;
    padding: 2px 4px;
    border-radius: 4px;
  }
  .smooth-scale .scale-label:hover {
    color: #fff;
  }
  .smooth-scale .scale-label.active {
    color: #fff;
    font-weight: 700;
    transform: translateY(-1px);
    text-shadow: 0 0 10px rgba(255, 255, 255, 0.6);
  }
  select, input[type=number] { width:100%; margin-top:8px; background:#0b0b0d; color:#fff;
         border:1px solid var(--line); border-radius:10px; padding:11px; font-size:.95rem; }
  select:focus, input:focus-visible, button:focus-visible { outline:2px solid #fff; outline-offset:2px; }
  .two { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
  @media (max-width:520px) { .two { grid-template-columns:1fr; } }
  
  details { margin-top:20px; border-top:1px solid var(--line); padding-top:14px; }
  summary { cursor:pointer; color:var(--mut); font-weight:500; font-size:.92rem; }
  summary:hover { color:#fff; }

  /* Info (i) Hover Button */
  .i-btn {
    width:20px; height:20px; border-radius:50%;
    border:1px solid rgba(255,255,255,0.22);
    background:rgba(255,255,255,0.08); color:#e4e4e7;
    font-size:12px; font-weight:700; font-style:italic; font-family:serif;
    display:inline-grid; place-items:center; cursor:help; flex-shrink:0;
    transition:all .15s ease;
  }
  .i-btn:hover { background:rgba(255,255,255,0.25); border-color:#fff; transform:scale(1.1); color:#fff; }
  
  /* Floating Popover Tooltip Dialog on the application */
  .floating-dialog {
    position:fixed; z-index:9999; width:min(330px, 92vw);
    background:rgba(20,20,28,0.95); border:1px solid rgba(255,255,255,0.22);
    border-radius:14px; padding:14px 16px;
    box-shadow:0 20px 50px rgba(0,0,0,0.8), 0 0 25px rgba(99,102,241,0.25);
    -webkit-backdrop-filter:blur(18px); backdrop-filter:blur(18px);
    color:#e4e4e7; font-size:.83rem; line-height:1.55;
    pointer-events:none; opacity:0;
    transform:translateY(6px) scale(0.97);
    transition:opacity .18s cubic-bezier(0.16,1,0.3,1), transform .18s cubic-bezier(0.16,1,0.3,1);
  }
  .floating-dialog.active { opacity:1; transform:translateY(0) scale(1); }
  .tip-header { display:flex; align-items:center; gap:8px; margin-bottom:6px; }
  .tip-ico {
    width:20px; height:20px; border-radius:50%;
    background:rgba(99,102,241,0.3); color:#a5b4fc;
    display:grid; place-items:center; font-size:11px; font-weight:800;
    font-family:serif; font-style:italic; flex-shrink:0;
  }
  .tip-heading { font-weight:700; font-size:.9rem; color:#fff; }
  .tip-content { color:#c9c9d5; font-size:.82rem; }

  #plan { margin-top:22px; background:#101015; border:1px solid var(--line); border-radius:14px; padding:16px; }
  #plan .grid { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; text-align:center; }
  #plan .grid b { display:block; font-size:1.3rem; }
  #plan .grid span { color:var(--mut); font-size:.75rem; }
  #plan .src { margin-top:12px; color:var(--mut); font-size:.78rem; text-align:center; }

  .note { margin-top:12px; line-height:1.45; border-radius:12px; }
  .note.green {
    background:rgba(34,197,94,0.12); border:1px solid rgba(34,197,94,0.32);
    color:var(--green); display:flex; align-items:center; justify-content:space-between;
    gap:12px; padding:12px 14px; text-align:left;
  }
  .note-main { display:flex; align-items:flex-start; gap:10px; }
  .note-badge {
    background:rgba(34,197,94,0.25); color:var(--green);
    width:20px; height:20px; border-radius:50%; display:grid; place-items:center;
    font-size:11px; font-weight:800; flex-shrink:0; margin-top:1px;
  }
  .note-title { font-weight:600; font-size:.9rem; color:#fff; }
  .note-desc { color:#c7f9cc; font-size:.83rem; margin-top:2px; font-weight:400; line-height:1.4; }
  .note.err { background:rgba(239,68,68,0.12); border:1px solid rgba(239,68,68,0.3); color:#f87171; padding:10px 14px; font-size:.85rem; }

  button.main { margin-top:22px; width:100%; padding:15px; font-size:1rem; font-weight:700; border:0;
           border-radius:12px; background:var(--acc); color:#000; cursor:pointer; transition:.15s; }
  button.main:hover { transform:translateY(-1px); box-shadow:0 8px 24px rgba(255,255,255,.18); }
  
  /* 3D Generation Stage & Loading Animation */
  .gen-stage { padding:36px 24px; text-align:center; }
  .stage-3d {
    width:140px; height:140px; margin:0 auto 20px;
    position:relative; perspective:750px;
    display:grid; place-items:center;
  }
  .ring-glow {
    position:absolute; width:125px; height:125px; border-radius:50%;
    background:radial-gradient(circle, rgba(99,102,241,0.4) 0%, rgba(168,85,247,0.18) 50%, transparent 70%);
    animation:pulseRing 2.4s ease-in-out infinite;
  }
  @keyframes pulseRing {
    0%, 100% { transform:scale(0.88); opacity:0.6; }
    50% { transform:scale(1.35); opacity:1; filter:drop-shadow(0 0 16px rgba(168,85,247,0.6)); }
  }
  .film-cube {
    width:64px; height:64px; position:relative;
    transform-style:preserve-3d;
    animation:rotateCube 7s linear infinite;
  }
  @keyframes rotateCube {
    0% { transform:rotateX(-18deg) rotateY(0deg); }
    100% { transform:rotateX(-18deg) rotateY(360deg); }
  }
  .face {
    position:absolute; width:64px; height:64px;
    background:rgba(26,26,38,0.8);
    border:1.5px solid rgba(168,85,247,0.55);
    border-radius:14px; display:grid; place-items:center; font-size:24px;
    box-shadow:inset 0 0 14px rgba(99,102,241,0.3), 0 0 16px rgba(168,85,247,0.35);
    -webkit-backdrop-filter:blur(8px); backdrop-filter:blur(8px);
  }
  .face.front  { transform:translateZ(32px); }
  .face.back   { transform:rotateY(180deg) translateZ(32px); }
  .face.right  { transform:rotateY(90deg) translateZ(32px); }
  .face.left   { transform:rotateY(-90deg) translateZ(32px); }
  .face.top    { transform:rotateX(90deg) translateZ(32px); background:rgba(99,102,241,0.25); }
  .face.bottom { transform:rotateX(-90deg) translateZ(32px); background:rgba(168,85,247,0.25); }

  .orbit-particle {
    position:absolute; width:8px; height:8px; border-radius:50%;
    background:#fff; box-shadow:0 0 10px #fff, 0 0 20px #818cf8;
  }
  .orbit-particle.p1 { animation:orbit1 3s linear infinite; }
  .orbit-particle.p2 { animation:orbit2 3.8s linear infinite reverse; }

  @keyframes orbit1 {
    0% { transform:rotate(0deg) translateX(58px) rotate(0deg); }
    100% { transform:rotate(360deg) translateX(58px) rotate(-360deg); }
  }
  @keyframes orbit2 {
    0% { transform:rotate(0deg) translateY(55px) rotate(0deg); }
    100% { transform:rotate(360deg) translateY(55px) rotate(-360deg); }
  }

  .gen-title { font-size:1.35rem; font-weight:700; letter-spacing:-.02em; margin-bottom:6px; color:#fff; }
  .gen-sub { color:var(--mut); font-size:.88rem; margin-bottom:16px; min-height:22px; }
  
  .pct-display {
    font-size:3.2rem; font-weight:900; letter-spacing:-.03em;
    background:linear-gradient(135deg, #fff 30%, #a5b4fc 70%, #c084fc 100%);
    -webkit-background-clip:text; -webkit-text-fill-color:transparent;
    line-height:1; margin-bottom:16px;
    filter:drop-shadow(0 4px 18px rgba(99,102,241,0.35));
  }
  .pct-symbol { font-size:1.6rem; font-weight:700; margin-left:2px; }
  
  .gen-bar-wrap { position:relative; width:100%; max-width:440px; margin:0 auto; }
  .bar.gen-bar {
    height:10px; background:#1c1c26; border-radius:6px; overflow:hidden;
    border:1px solid rgba(255,255,255,0.08); box-shadow:inset 0 2px 4px rgba(0,0,0,0.5);
  }
  .bar.gen-bar i {
    display:block; height:100%; width:0%;
    background:linear-gradient(90deg, #4f46e5, #7c3aed, #db2777, #6366f1);
    background-size:200% 100%;
    border-radius:5px; transition:width .25s ease-out;
    animation:barShine 2s linear infinite;
    box-shadow:0 0 12px rgba(124, 58, 237, 0.65);
  }
  @keyframes barShine { 0% { background-position:100% 0; } 100% { background-position:-100% 0; } }

  .gen-stats { margin-top:18px; }
  .gen-badge {
    display:inline-block; padding:6px 16px; border-radius:999px;
    background:#1e1e28; border:1px solid #333345;
    color:#e4e4e7; font-size:.84rem; font-weight:500;
  }
</style></head>
<body>
  <canvas id="bgCanvas" class="bg-canvas"></canvas>
  <main>
    <header class="app-header" id="appHeader">
      <div class="header-content">
        <div class="logo-3d-wrap" id="logo3d" title="Scroll Video Maker">
          <div class="logo-glow"></div>
          <div class="logo-box" id="logoBox">
            <div class="logo-sheen"></div>
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" class="logo-svg">
              <rect x="3" y="4" width="18" height="16" rx="3.5" />
              <path d="M10 9l5 3-5 3V9z" fill="currentColor" />
            </svg>
          </div>
          <div class="logo-ring"></div>
        </div>

        <div class="hero-badge">
          <span class="live-dot"></span>
          <span class="badge-text">Interactive 3D Scroll Engine</span>
        </div>

        <h1 class="hero-title">Scroll Video Maker</h1>
        <p class="hero-sub">Turn your video into an interactive scroll animation.</p>
      </div>
    </header>

  <!-- Floating Tooltip Dialog on the application -->
  <div id="tipBox" class="floating-dialog" hidden>
    <div class="tip-header">
      <div class="tip-ico">i</div>
      <div class="tip-heading" id="tipHeading"></div>
    </div>
    <div class="tip-content" id="tipContent"></div>
  </div>

  <!-- 1. choose file -->
  <div id="s1">
    <div id="drop" tabindex="0">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/></svg>
      <div class="t">Drag &amp; drop your video here</div>
      <small>or click to browse · MP4 recommended · best with 5–20 second clips</small>
      <input id="file" type="file" accept="video/*,.mkv,.mov" hidden />
    </div>
    <div id="upstat" hidden style="margin-top:16px"><div id="upmsg">Uploading…</div><div class="bar gen-bar"><i id="upfill"></i></div></div>
    <div id="err1" class="note err" hidden></div>
  </div>

  <!-- 2. settings -->
  <div id="s2" class="card" hidden>
    <div class="file">
      <div class="file-lead">
        <div class="video-icon">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="4" width="20" height="16" rx="3"/><path d="M10 9l5 3-5 3V9z" fill="currentColor"/></svg>
        </div>
        <div>
          <b id="fname"></b>
          <div id="finfo" class="finfo"></div>
        </div>
      </div>
      <button class="btn-change" id="change" type="button" title="Select a different video">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
        </svg>
        Change video
      </button>
    </div>

    <!-- Scroll length on main background -->
    <div class="field">
      <div class="row">
        <label for="screens">
          Scroll length
          <button type="button" class="i-btn" data-tip-title="Scroll Length" data-tip-body="How many screen heights you scroll to play the entire video from start to finish. For example, 20 screens means scrolling through 20 page heights. Higher numbers make playback slower and more cinematic; lower numbers make it play faster. You can drag the slider or click the number to type any value!">i</button>
        </label>
        <div class="screens-display">
          <input id="screensNum" type="number" min="2" max="1000" value="20" class="seamless-num" title="Drag slider below or click to type any number" />
          <span class="unit-text">screens</span>
        </div>
      </div>
      <input id="screens" type="range" min="2" max="100" value="20" />
      <div class="hint">How long you scroll to complete the video. Drag slider or type any number you want!</div>
      <div id="maxTip" class="scroll-limit-tip" hidden>
        <span class="tip-icon">💡</span> You can increase the scroll length and frames by typing any number above!
      </div>
    </div>

    <!-- Smoothness -->
    <div class="field">
      <div class="row">
        <label for="smooth">
          Smoothness
          <button type="button" class="i-btn" data-tip-title="Continuous Smoothness" data-tip-body="Fine-tune your exact scroll animation smoothness continuously from 1 to 100.<br><br>• <b>Left end (Light):</b> Fewer frames, fast and lightweight.<br>• <b>Right end (Ultra):</b> Maximum frames, silky butter-smooth motion.<br>• As you drag, the app smoothly interpolates the frame rate and highlights whichever mode is closest!">i</button>
        </label>
        <span class="val" id="vSmooth">Smooth</span>
      </div>
      <input id="smooth" type="range" min="1" max="100" value="50" />
      <div class="smooth-scale">
        <span class="scale-label" data-val="1">Light</span>
        <span class="scale-label" data-val="25">Balanced</span>
        <span class="scale-label active" data-val="50">Smooth</span>
        <span class="scale-label" data-val="75">Silky</span>
        <span class="scale-label" data-val="100">Ultra</span>
      </div>
      <div class="hint">Extreme left = Not smooth (Light) · Extreme right = Very smooth (Ultra). Drag anywhere to set your custom feel!</div>
    </div>

    <!-- Resolution & Format -->
    <div class="two">
      <div class="field">
        <div class="row">
          <label for="width">
            Resolution
            <button type="button" class="i-btn" data-tip-title="Resolution" data-tip-body="Frame dimensions. Full HD (1080p) is recommended for crisp desktop monitors. HD (720p) is lightweight and loads faster. 2K/4K delivers maximum detail on large displays.">i</button>
          </label>
        </div>
        <select id="width">
          <option value="1280">HD · 720p (Faster loading)</option>
          <option value="1920" selected>Full HD · 1080p (Recommended)</option>
          <option value="2560">2K · 1440p (High-res)</option>
          <option value="3840">4K · 2160p (Ultra HD)</option>
        </select>
      </div>
      <div class="field">
        <div class="row">
          <label for="format">
            Image format
            <button type="button" class="i-btn" data-tip-title="Image Format" data-tip-body="WebP: Recommended for small file size & high clarity.<br>JPG: Standard compatibility everywhere.<br>PNG: Lossless pixel perfection (sharpest).<br>AVIF: Next-gen ultra-compact compression.">i</button>
          </label>
        </div>
        <select id="format">
          <option value="webp" selected>WebP (Recommended · small & fast)</option>
          <option value="jpg">JPG (Universal compatibility)</option>
          <option value="png">PNG (Lossless · maximum sharpness)</option>
          <option value="avif">AVIF (Next-Gen · smallest size)</option>
        </select>
      </div>
    </div>

    <!-- Advanced Settings -->
    <details>
      <summary>Advanced settings (video fit, image quality, frame count)</summary>
      <div class="two">
        <div class="field">
          <div class="row">
            <label for="fit">
              Video fit
              <button type="button" class="i-btn" data-tip-title="Video Fit" data-tip-body="<b>Fill screen (Cover):</b> Fills 100% of the screen edge-to-edge with no black bars. Outer edges are softly cropped if screen shape differs.<br><br><b>Show whole video (Contain):</b> Shows every single pixel without cropping. Black letterbox bars appear if screen shape differs.">i</button>
            </label>
          </div>
          <select id="fit">
            <option value="cover" selected>Fill screen (Cover · fills entire screen)</option>
            <option value="contain">Show whole video (Contain · no edges cut off)</option>
          </select>
          <div class="hint">How video sizes to visitor screens.</div>
        </div>
        <div class="field">
          <div class="row">
            <label for="comp">
              Image quality
              <button type="button" class="i-btn" data-tip-title="Image Quality" data-tip-body="Controls compression balance (50–95). 80–85 gives high visual quality with reasonable file size. (PNG frames are always lossless).">i</button>
            </label>
            <span class="val" id="vComp">82</span>
          </div>
          <input id="comp" type="range" min="50" max="95" value="82" />
          <div class="hint" id="compHint">Balance between visual clarity and file size.</div>
        </div>
      </div>
      <div class="field">
        <div class="row">
          <label for="frames">
            Exact number of frames (optional)
            <button type="button" class="i-btn" data-tip-title="Exact Frame Count" data-tip-body="Override automatic calculation to specify an exact number of frames (e.g. 150). Leave blank on Auto for the recommended count.">i</button>
          </label>
        </div>
        <input id="frames" type="number" min="2" max="5000" placeholder="Auto (recommended)" />
        <div class="hint">Leave empty to calculate optimal frames automatically.</div>
      </div>
    </details>

    <!-- Plan calculation & Notes in Green -->
    <div id="plan">
      <div class="grid">
        <div><b id="pFrames">-</b><span>frames</span></div>
        <div><b id="pRes">-</b><span>resolution</span></div>
        <div><b id="pSize">-</b><span>total size</span></div>
      </div>
      <div class="src" id="pSrc"></div>
      <div id="pNotes"></div>
    </div>

    <div id="err2" class="note err" hidden></div>
    <button class="main" id="go">Create scroll website</button>
  </div>

  <!-- 3. progress with 3D animation -->
  <div id="s3" class="card center gen-stage" hidden>
    <div class="stage-3d">
      <div class="ring-glow"></div>
      <div class="film-cube">
        <div class="face front">🎬</div>
        <div class="face back">🎞</div>
        <div class="face right">✨</div>
        <div class="face left">⚡</div>
        <div class="face top"></div>
        <div class="face bottom"></div>
      </div>
      <div class="orbit-particle p1"></div>
      <div class="orbit-particle p2"></div>
    </div>
    
    <div class="gen-title">Creating Your Scroll Animation</div>
    <div class="gen-sub" id="genSub">Extracting frames and optimizing resolution…</div>
    
    <div class="pct-display">
      <span id="genPct">0</span><span class="pct-symbol">%</span>
    </div>

    <div class="gen-bar-wrap">
      <div class="bar gen-bar"><i id="fill"></i></div>
    </div>

    <div class="gen-stats">
      <div class="gen-badge" id="genMsg">Starting…</div>
    </div>
  </div>
</main>

<script>
const PLAN = __PLAN__;
const $ = id => document.getElementById(id);
const H = { "X-SV": "1" };
let probe = null;

function show(id) { ["s1","s2","s3"].forEach(s => $(s).hidden = s !== id); }

/* ---------- Fullscreen 3D Interactive Canvas Animation ---------- */
(function initFullscreen3D() {
  const canvas = $("bgCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const logoBox = $("logoBox");
  let w = 0, h = 0, dpr = 1;
  const mouse = { x: -9999, y: -9999, tx: -9999, ty: -9999, active: false };

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = window.innerWidth;
    h = window.innerHeight;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  window.addEventListener("resize", resize);
  resize();

  window.addEventListener("mousemove", e => {
    mouse.tx = e.clientX;
    mouse.ty = e.clientY;
    mouse.active = true;

    // 3D Interactive Logo Tilt
    if (logoBox) {
      const lRect = logoBox.getBoundingClientRect();
      const lx = (e.clientX - (lRect.left + lRect.width / 2)) / 22;
      const ly = (e.clientY - (lRect.top + lRect.height / 2)) / 22;
      const rotY = Math.max(-18, Math.min(18, lx * 1.8));
      const rotX = Math.max(-18, Math.min(18, -ly * 1.8));
      logoBox.style.transform = `rotateX(${rotX}deg) rotateY(${rotY}deg) scale(1.05)`;
    }
  });

  window.addEventListener("mouseleave", () => {
    mouse.active = false;
    if (logoBox) logoBox.style.transform = "";
  });

  const COLS = 34, ROWS = 20;
  let time = 0;

  function render() {
    time += 0.016;
    if (mouse.active) {
      mouse.x += (mouse.tx - mouse.x) * 0.08;
      mouse.y += (mouse.ty - mouse.y) * 0.08;
    }

    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    const stepX = w / (COLS - 1);
    const stepY = h / (ROWS - 1);
    const pts = [];

    for (let r = 0; r < ROWS; r++) {
      pts[r] = [];
      for (let c = 0; c < COLS; c++) {
        const bx = c * stepX;
        const by = r * stepY;

        // 3D perspective undulating sine wave
        const wave = Math.sin(c * 0.28 + time) * Math.cos(r * 0.32 + time * 0.85);
        let z = wave * 26;

        if (mouse.active) {
          const dx = bx - mouse.x;
          const dy = by - mouse.y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < 240) {
            const push = (1 - dist / 240) * 38;
            z += Math.sin(dist * 0.06 - time * 2) * push;
          }
        }

        const fov = 420;
        const scale = fov / (fov + z);
        const px = w / 2 + (bx - w / 2) * scale;
        const py = h / 2 + (by - h / 2) * scale;

        pts[r][c] = { x: px, y: py, z: scale };
      }
    }

    // Draw connecting 3D grid lines with luminous gradient
    ctx.lineWidth = 1;
    for (let r = 0; r < ROWS; r++) {
      for (let c = 0; c < COLS; c++) {
        const p = pts[r][c];

        if (c < COLS - 1) {
          const next = pts[r][c + 1];
          const alpha = Math.max(0.03, Math.min(0.28, (p.z - 0.85) * 0.7));
          ctx.strokeStyle = `rgba(129, 140, 248, ${alpha})`;
          ctx.beginPath();
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(next.x, next.y);
          ctx.stroke();
        }

        if (r < ROWS - 1) {
          const down = pts[r + 1][c];
          const alpha = Math.max(0.02, Math.min(0.22, (p.z - 0.85) * 0.5));
          ctx.strokeStyle = `rgba(168, 85, 247, ${alpha})`;
          ctx.beginPath();
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(down.x, down.y);
          ctx.stroke();
        }

        if ((r + c) % 2 === 0) {
          const rad = Math.max(1, p.z * 1.6);
          const pAlpha = Math.max(0.08, Math.min(0.75, (p.z - 0.85) * 1.2));
          ctx.fillStyle = `rgba(224, 231, 255, ${pAlpha})`;
          ctx.beginPath();
          ctx.arc(p.x, p.y, rad, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    }

    ctx.restore();
    requestAnimationFrame(render);
  }

  requestAnimationFrame(render);
})();

/* ---------- Floating Tooltip Dialog on Hover ---------- */
const tipBox = $("tipBox"), tipHeading = $("tipHeading"), tipContent = $("tipContent");
let tipTimer = null;

function showTipFor(el) {
  clearTimeout(tipTimer);
  const title = el.getAttribute("data-tip-title");
  const body = el.getAttribute("data-tip-body");
  if (!title || !body) return;
  tipHeading.textContent = title;
  tipContent.innerHTML = body;
  tipBox.hidden = false;
  
  const rect = el.getBoundingClientRect();
  const boxWidth = Math.min(330, window.innerWidth - 28);
  let left = rect.left + rect.width / 2 - boxWidth / 2;
  left = Math.max(14, Math.min(window.innerWidth - boxWidth - 14, left));
  
  tipBox.style.left = left + "px";
  
  requestAnimationFrame(() => {
    const h = tipBox.offsetHeight || 100;
    if (rect.top - h - 10 >= 10) {
      tipBox.style.top = (rect.top - h - 10) + "px";
    } else {
      tipBox.style.top = (rect.bottom + 10) + "px";
    }
    tipBox.classList.add("active");
  });
}

function hideTip() {
  tipBox.classList.remove("active");
  tipTimer = setTimeout(() => {
    if (!tipBox.classList.contains("active")) tipBox.hidden = true;
  }, 180);
}

document.addEventListener("mouseover", e => {
  const btn = e.target.closest(".i-btn");
  if (btn) showTipFor(btn);
});
document.addEventListener("mouseout", e => {
  const btn = e.target.closest(".i-btn");
  if (btn) hideTip();
});

/* ---------- 1. upload & edit-state initialization ---------- */
$("drop").onclick = () => $("file").click();
$("drop").onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("file").click(); } };
$("file").onchange = e => pick(e.target.files[0]);
["dragover","dragenter"].forEach(ev => $("drop").addEventListener(ev, e => { e.preventDefault(); $("drop").classList.add("over"); }));
["dragleave","drop"].forEach(ev => $("drop").addEventListener(ev, e => { e.preventDefault(); $("drop").classList.remove("over"); }));
$("drop").addEventListener("drop", e => pick(e.dataTransfer.files[0]));
$("change").onclick = () => {
  sessionStorage.setItem("sv_cleared", "1");
  fetch("/reset", { method: "POST", headers: H }).catch(() => {});
  $("file").value = "";
  $("err1").hidden = true;
  $("upstat").hidden = true;
  show("s1");
};

function err1(m) { $("err1").textContent = m; $("err1").hidden = false; $("upstat").hidden = true; }

function pick(f) {
  if (!f) return;
  if (!/\.(mp4|mov|mkv|avi|webm|m4v)$/i.test(f.name) && !f.type.startsWith("video/")) return err1("Please choose a video file.");
  sessionStorage.removeItem("sv_cleared");
  $("err1").hidden = true; $("upstat").hidden = false; $("upfill").style.width = "0%"; $("upmsg").textContent = "Uploading…";
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/upload?name=" + encodeURIComponent(f.name));
  xhr.setRequestHeader("X-SV", "1");
  xhr.upload.onprogress = e => {
    if (!e.lengthComputable) return;
    const p = Math.round(e.loaded / e.total * 100);
    $("upmsg").textContent = p < 100 ? "Uploading… " + p + "%" : "Reading video…";
    $("upfill").style.width = p + "%";
  };
  xhr.onerror = () => err1("Upload failed. Is the Python script still running?");
  xhr.onload = () => {
    let r = {}; try { r = JSON.parse(xhr.responseText); } catch (_) {}
    if (xhr.status !== 200) return err1(r.error || "Upload failed.");
    probe = r;
    $("fname").textContent = f.name;
    $("finfo").textContent = `${r.width}×${r.height} · ${r.duration}s · ${r.fps} fps · ${r.frames} frames`;
    $("err2").hidden = true;
    show("s2"); update();
  };
  xhr.send(f);
}

/* ---------- 2. settings + live plan ---------- */
let maxTipTimer = null;
function checkScreensMax(val, max) {
  const tip = $("maxTip");
  if (!tip) return;
  if (+val >= +max) {
    tip.hidden = false;
    clearTimeout(maxTipTimer);
    maxTipTimer = setTimeout(() => {
      tip.hidden = true;
    }, 3000);
  } else {
    clearTimeout(maxTipTimer);
    tip.hidden = true;
  }
}

$("screens").oninput = $("screens").onchange = () => {
  $("screensNum").value = $("screens").value;
  checkScreensMax($("screens").value, $("screens").max);
  update();
};
$("screensNum").oninput = () => {
  let val = parseInt($("screensNum").value, 10);
  if (isNaN(val) || val < 2) val = 2;
  if (val > +$("screens").max) $("screens").max = Math.max(100, Math.ceil(val / 20) * 20);
  $("screens").value = val;
  update();
};

function opts() {
  const sc = Math.max(2, +$("screensNum").value || +$("screens").value || 20);
  return { screens: sc, smoothness: +$("smooth").value, width: +$("width").value,
           format: $("format").value, compression: +$("comp").value, frames: +$("frames").value || 0,
           fit: $("fit").value };
}

function calcSmooth(val) {
  let v = +val;
  let L = (v <= 5) ? Math.min(5, Math.max(1, v)) : (1 + (Math.max(1, Math.min(100, v)) - 1) * 4 / 99);
  const points = [
    { name: "Light",    ppf: 20.0, ease: 0.30 },
    { name: "Balanced", ppf: 14.0, ease: 0.22 },
    { name: "Smooth",   ppf: 10.0, ease: 0.16 },
    { name: "Silky",    ppf: 7.0,  ease: 0.11 },
    { name: "Ultra",    ppf: 5.0,  ease: 0.08 }
  ];
  let idx = Math.floor(L) - 1;
  let ppf, ease;
  if (idx >= 4) {
    ppf = points[4].ppf;
    ease = points[4].ease;
  } else {
    let frac = L - (idx + 1);
    ppf = points[idx].ppf + frac * (points[idx + 1].ppf - points[idx].ppf);
    ease = points[idx].ease + frac * (points[idx + 1].ease - points[idx].ease);
  }
  let closestIdx = Math.max(0, Math.min(4, Math.round(L) - 1));
  return { name: points[closestIdx].name, ppf, ease, closestIdx };
}

function plan(p, o) {
  const S = calcSmooth(o.smoothness);
  let w = Math.min(o.width, p.width); w -= w % 2;
  let h = Math.round(p.height * w / p.width); h -= h % 2;
  const scrollPx = o.screens * PLAN.ref_vh;
  const want = o.frames || Math.round(scrollPx / S.ppf);
  const memCap = Math.max(60, Math.min(PLAN.max_frames, Math.floor(PLAN.mem / (w * h * 4))));
  const notes = [];
  let count = Math.min(want, p.frames, memCap);

  if (memCap < Math.min(want, p.frames)) {
    notes.push({
      kind: "green",
      title: "Optimized for smooth playback",
      text: `Set to ${memCap} frames so the page stays fast and fluid without lag.`,
      tip: "Web browsers can freeze or crash if they have to hold hundreds of heavy images in memory at once. The app automatically chooses the ideal frame count so your animation stays responsive and scrolls buttery smooth.<br><br>💡 <b>Tip:</b> If you want even more frames, simply choose a lower resolution (like 720p HD) in the Resolution dropdown above."
    });
  } else if (p.frames < want) {
    notes.push({
      kind: "green",
      title: "Using all video frames",
      text: `Using all ${p.frames} available frames from your video clip.`,
      tip: `Your original video file has ${p.frames} frames in total, so every frame is being utilized for the animation.`
    });
  }

  count = Math.max(2, count);
  let bpp = 0.09;
  if (o.format === "avif") bpp = 0.06;
  else if (o.format === "webp") bpp = 0.09;
  else if (o.format === "jpg") bpp = 0.13;
  else if (o.format === "png") bpp = 0.50;
  bpp *= (o.compression / 80);

  return { count, w, h, ppf: scrollPx / count, mb: count * w * h * bpp / 1048576, notes, name: S.name, closestIdx: S.closestIdx };
}

function update() {
  if (!probe) return;
  const o = opts(), pl = plan(probe, o);
  $("vSmooth").textContent = pl.name;

  document.querySelectorAll(".scale-label").forEach((el, i) => {
    el.classList.toggle("active", i === pl.closestIdx);
  });

  if (o.format === "png") {
    $("vComp").textContent = "Lossless (HQ)";
    if ($("compHint")) $("compHint").textContent = "PNG is lossless, keeping maximum pixel quality.";
  } else {
    $("vComp").textContent = o.compression;
    if ($("compHint")) $("compHint").textContent = "Balance between visual clarity and file size.";
  }

  $("pFrames").textContent = pl.count;
  $("pRes").textContent = pl.w + "×" + pl.h;
  $("pSize").textContent = "≈ " + (pl.mb < 10 ? pl.mb.toFixed(1) : Math.round(pl.mb)) + " MB";
  $("pSrc").textContent = `About ${pl.ppf.toFixed(1)} px of scrolling per frame · source video: ${probe.frames} frames`;

  $("pNotes").innerHTML = pl.notes.map(n => `
    <div class="note ${n.kind}">
      <div class="note-main">
        <span class="note-badge">✓</span>
        <div>
          <div class="note-title">${n.title}</div>
          <div class="note-desc">${n.text}</div>
        </div>
      </div>
      <button type="button" class="i-btn" data-tip-title="${n.title}" data-tip-body="${n.tip.replace(/"/g, '&quot;')}" aria-label="More info">i</button>
    </div>
  `).join("");
}

["smooth","width","format","comp","frames","fit"].forEach(id => {
  $(id).addEventListener("input", update); $(id).addEventListener("change", update);
});

document.querySelectorAll(".scale-label").forEach(el => {
  el.addEventListener("click", () => {
    $("smooth").value = el.getAttribute("data-val");
    update();
  });
});

/* ---------- Check for existing video (e.g. from 'Edit video') ---------- */
if (PLAN.probe && (location.search.includes("edit") || !sessionStorage.getItem("sv_cleared"))) {
  probe = PLAN.probe;
  $("fname").textContent = PLAN.filename || "Current Video";
  $("finfo").textContent = `${probe.width}×${probe.height} · ${probe.duration}s · ${probe.fps} fps · ${probe.frames} frames`;
  if (PLAN.cfg) {
    if (PLAN.cfg.screens) {
      const sc = PLAN.cfg.screens;
      if (sc > +$("screens").max) $("screens").max = Math.max(100, Math.ceil(sc / 20) * 20);
      $("screens").value = sc;
      $("screensNum").value = sc;
    }
    if (PLAN.cfg.smoothness != null) {
      let sm = +PLAN.cfg.smoothness;
      if (sm <= 5) sm = Math.round(1 + (sm - 1) * 99 / 4);
      $("smooth").value = sm;
    }
    if (PLAN.cfg.ext) $("format").value = PLAN.cfg.ext;
    if (PLAN.cfg.fit) $("fit").value = PLAN.cfg.fit;
    if (PLAN.cfg.width) $("width").value = String(PLAN.cfg.width);
  }
  show("s2");
  update();
}

/* ---------- 3. build with 3D animation ---------- */
function fail(m) { show("s2"); $("err2").textContent = m; $("err2").hidden = false; }
$("go").onclick = () => {
  $("err2").hidden = true; show("s3"); $("fill").style.width = "0%"; $("genPct").textContent = "0";
  $("genMsg").textContent = "Starting…"; $("genSub").textContent = "Extracting video frames…";
  fetch("/build", { method: "POST", headers: H, body: JSON.stringify(opts()) })
    .then(r => r.json().then(j => ({ ok: r.ok, j })))
    .then(({ ok, j }) => { if (!ok) throw new Error(j.error || "Could not start."); poll(); })
    .catch(e => fail(e.message));
};

function poll() {
  fetch("/status").then(r => r.json()).then(s => {
    if (s.state === "done") {
      $("genSub").textContent = "Opening your website…";
      $("genPct").textContent = "100";
      $("fill").style.width = "100%";
      return location.href = "/view/";
    }
    if (s.state === "error") return fail(s.message);
    const p = Math.max(0, Math.min(100, s.pct || 0));
    $("genPct").textContent = p;
    $("fill").style.width = p + "%";
    $("genMsg").textContent = s.message || "Preparing frames…";
    if (p < 30) {
      $("genSub").textContent = "Extracting video frames in high resolution…";
    } else if (p < 75) {
      $("genSub").textContent = "Encoding and optimizing image quality…";
    } else {
      $("genSub").textContent = "Finalizing interactive scroll timeline…";
    }
    setTimeout(poll, 250);
  }).catch(() => setTimeout(poll, 800));
}
</script></body></html>
"""

VIEWER_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Scroll Video</title>
<style>
  :root { --bg:#0b0b0d; --fg:#f4f4f5; }
  * { margin:0; padding:0; box-sizing:border-box; }
  html { background:#000; }
  body { background:#000; color:var(--fg); font-family:system-ui,-apple-system,"Segoe UI",sans-serif; }
  [hidden] { display:none !important; }

  #area { position:relative; }
  #stage { position:sticky; top:0; height:100vh; width:100%; overflow:hidden; background:#000; }
  canvas { width:100%; height:100%; display:block; }

  #progress { position:fixed; left:0; top:0; height:3px; width:0; background:#fff; z-index:5; opacity:.9; }

  /* top action navigation bar */
  .top-nav {
    position: fixed;
    top: 20px;
    right: 20px;
    z-index: 100;
    display: flex;
    align-items: center;
    gap: 12px;
  }

  /* floating glass buttons */
  .glass {
    position: relative;
    z-index: 1;
    display: inline-flex;
    align-items: center;
    gap: 10px;
    cursor: pointer;
    color: #fff;
    font: 600 14px/1 system-ui, -apple-system, "Segoe UI", sans-serif;
    padding: 12px 18px;
    background: rgba(20, 20, 28, 0.88);
    border: 1px solid rgba(255, 255, 255, 0.28);
    border-radius: 14px;
    -webkit-backdrop-filter: blur(16px) saturate(1.4);
    backdrop-filter: blur(16px) saturate(1.4);
    box-shadow: 0 10px 32px rgba(0, 0, 0, 0.6);
    transition: background .15s, transform .15s, border-color .15s, box-shadow .15s;
    user-select: none;
    -webkit-user-select: none;
    text-decoration: none;
  }
  .glass:hover {
    background: rgba(45, 45, 62, 0.98);
    border-color: rgba(255, 255, 255, 0.6);
    transform: translateY(-2px);
    box-shadow: 0 14px 40px rgba(0, 0, 0, 0.75), 0 0 20px rgba(99, 102, 241, 0.35);
  }
  .glass:active { transform: scale(0.97); }
  .glass:focus-visible, .btn:focus-visible { outline: 2px solid #fff; outline-offset: 2px; }
  .glass svg { width: 18px; height: 18px; flex: none; stroke: currentColor; }
  .glass small { font-weight: 500; opacity: .75; font-size: 12px; }

  #new {
    position: fixed;
    bottom: 20px;
    right: 20px;
    z-index: 100;
  }

  @media (max-width: 600px) {
    .top-nav { top: 12px; right: 12px; gap: 8px; }
    #new { bottom: 12px; right: 12px; }
    .glass { padding: 10px 14px; font-size: 13px; }
    #dl small { display: none; }
  }

  #hint { position:fixed; bottom:26px; left:50%; transform:translateX(-50%); z-index:6; font-size:.85rem;
          color:#fff; background:rgba(0,0,0,.45); padding:8px 14px; border-radius:999px; opacity:.85;
          transition:opacity .5s; pointer-events:none; animation:bob 1.6s ease-in-out infinite; }
  #hint.hide { opacity:0; }
  @keyframes bob { 50% { transform:translate(-50%,6px); } }

  #loader { position:fixed; inset:0; background:#0b0b0d; z-index:20; display:flex; flex-direction:column;
            gap:16px; align-items:center; justify-content:center; transition:opacity .6s; }
  #loader.hide { opacity:0; pointer-events:none; }
  .bar { width:min(320px,70vw); height:5px; background:#2a2a30; border-radius:3px; overflow:hidden; }
  .bar i { display:block; height:100%; width:0; background:#fff; transition:width .15s; }

  #toast { position:fixed; left:50%; bottom:80px; transform:translate(-50%,10px); z-index:30; background:#fff; color:#000;
           font-weight:600; font-size:.9rem; padding:12px 18px; border-radius:12px; opacity:0; pointer-events:none;
           transition:.25s; max-width:90vw; text-align:center; box-shadow:0 10px 30px rgba(0,0,0,.4); }
  #toast.on { opacity:1; transform:translate(-50%,0); }

  /* dialog */
  #modal { position:fixed; inset:0; z-index:40; display:grid; place-items:center; padding:20px;
           background:rgba(0,0,0,.62); -webkit-backdrop-filter:blur(6px); backdrop-filter:blur(6px);
           opacity:0; pointer-events:none; transition:opacity .2s; }
  #modal.on { opacity:1; pointer-events:auto; }
  .card { width:min(460px,100%); background:#15151a; border:1px solid #2c2c35; border-radius:20px; padding:28px 26px 22px;
          text-align:center; box-shadow:0 30px 80px rgba(0,0,0,.6); transform:translateY(10px) scale(.98); transition:transform .2s; }
  #modal.on .card { transform:none; }
  .ico { width:56px; height:56px; border-radius:50%; margin:0 auto 14px; display:grid; place-items:center; }
  .ico svg { width:28px; height:28px; }
  .ico.ok { background:rgba(123,227,160,.15); color:#7be3a0; }
  .ico.bad { background:rgba(255,143,143,.14); color:#ff8f8f; }
  .ico.busy { background:rgba(255,255,255,.08); color:#fff; }
  .card h2 { font-size:1.3rem; letter-spacing:-.01em; margin-bottom:8px; }
  .card p { color:#a6a6b2; line-height:1.5; font-size:.95rem; }
  .path { margin:16px 0 4px; text-align:left; font:.8rem/1.45 ui-monospace,Menlo,Consolas,monospace; color:#d7d7df;
          background:#0d0d11; border:1px solid #26262e; border-radius:10px; padding:11px 12px; word-break:break-all; }
  .card .bar { width:100%; margin-top:18px; }
  .row { display:flex; gap:10px; margin-top:22px; }
  .btn { flex:1; padding:13px 14px; font:700 .95rem system-ui,-apple-system,"Segoe UI",sans-serif; border-radius:12px; cursor:pointer;
         border:1px solid #34343f; background:transparent; color:#fff; transition:.15s; }
  .btn:hover { border-color:#fff; }
  .btn.primary { background:#fff; color:#000; border-color:#fff; flex:2; white-space:nowrap; }
  .btn.primary:hover { box-shadow:0 8px 22px rgba(255,255,255,.2); transform:translateY(-1px); }
  @media (max-width:420px) { .row { flex-direction:column; } }
</style>
</head>
<body>

<div id="loader">
  <div id="pct">Loading 0%</div>
  <div class="bar"><i id="fill"></i></div>
</div>
<div id="progress"></div>

<div class="top-nav">
  <button class="glass" id="fsBtn" title="Toggle Fullscreen mode (Esc to exit)">
    <svg id="fsIcoEnter" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>
    </svg>
    <svg id="fsIcoExit" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" hidden>
      <path d="M4 14h6m0 0v6m0-6L3 21m17-7h-6m0 0v6m0-6l7 7M14 4v6m0 0h6m-6 0L21 3M10 4v6m0 0H4m6 0L3 3"/>
    </svg>
    <span id="fsText">Fullscreen</span>
  </button>
  <button class="glass" id="edit" title="Edit scroll settings or change configuration">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
      <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
    </svg>
    Edit video
  </button>
  <button class="glass" id="dl" title="Save every frame to a folder you choose">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11m0 0l-4-4m4 4l4-4"/><path d="M4 19h16"/></svg>
    Download frames <small id="dlCount"></small>
  </button>
</div>
<button class="glass" id="new" title="Make another scroll video">
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>
  Create new
</button>
<div id="hint">Scroll ↓</div>
<div id="toast"></div>

<div id="area"><div id="stage"><canvas id="canvas"></canvas></div></div>

<div id="modal" role="dialog" aria-modal="true" aria-live="polite"><div class="card" id="card"></div></div>

<script>
const CFG = __CONFIG__;
const N = CFG.count;
const $ = id => document.getElementById(id);
const H = { "X-SV": "1" };

const canvas = $("canvas"), ctx = canvas.getContext("2d", { alpha: false });
const area = $("area"), loader = $("loader"), pct = $("pct"), fill = $("fill");
const progressBar = $("progress"), hint = $("hint");

const frames = new Array(N).fill(null);
let loadedCount = 0, current = 0, target = 0, lastImg = null, cw = 0, ch = 0;

$("dlCount").textContent = "(" + N + ")";
// scroll distance = CFG.screens screen-heights. Frame 1 at the very top, last frame at the very bottom.
area.style.height = (CFG.screens + 1) * 100 + "vh";

const url = i => `frames/${CFG.prefix}${String(i + 1).padStart(CFG.pad, "0")}.${CFG.ext}?v=${CFG.v}`;

/* ---------- loading: a coarse pass first, then fill in the gaps ---------- */
function loadFrame(i) {
  return new Promise(resolve => {
    if (frames[i]) return resolve();
    const img = new Image();
    img.src = url(i);
    const done = () => { frames[i] = img; loadedCount++; resolve(); };
    (img.decode ? img.decode() : Promise.reject()).then(done).catch(() => {
      img.onload = done; img.onerror = resolve;
      if (img.complete) done();
    });
  });
}
async function runQueue(list, workers, onStep) {
  let p = 0;
  async function worker() { while (p < list.length) { const i = list[p++]; await loadFrame(i); onStep && onStep(); } }
  await Promise.all(Array.from({ length: workers }, worker));
}
async function loadAll() {
  const stride = Math.max(1, Math.round(N / 40));
  const coarse = [], rest = [];
  for (let i = 0; i < N; i++) (i % stride === 0 || i === N - 1 ? coarse : rest).push(i);
  let done = 0;
  await runQueue(coarse, 6, () => {
    done++;
    const p = Math.round(done / coarse.length * 100);
    pct.textContent = "Loading " + p + "%"; fill.style.width = p + "%";
  });
  if (!frames.some(Boolean)) { pct.textContent = "Could not load the frames. Go back and create the website again."; return; }
  start();
  await runQueue(rest, 4);
}

/* ---------- drawing ---------- */
function nearestLoaded(i) {
  for (let d = 0; d < N; d++) {
    if (frames[i - d]) return frames[i - d];
    if (frames[i + d]) return frames[i + d];
  }
  return null;
}
function resize() {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = Math.round(canvas.clientWidth * dpr), h = Math.round(canvas.clientHeight * dpr);
  if (w === cw && h === ch) return;
  cw = canvas.width = w; ch = canvas.height = h; lastImg = null; onScroll();
}
function paint(img) {
  const s = CFG.fit === "cover" ? Math.max(cw / img.width, ch / img.height) : Math.min(cw / img.width, ch / img.height);
  const w = img.width * s, h = img.height * s;
  ctx.fillStyle = "#000"; ctx.fillRect(0, 0, cw, ch);
  ctx.drawImage(img, (cw - w) / 2, (ch - h) / 2, w, h);
}

/* ---------- scroll -> frame ---------- */
function onScroll() {
  const total = area.offsetHeight - window.innerHeight;
  const p = Math.min(1, Math.max(0, window.scrollY / total));
  target = p * (N - 1);
  progressBar.style.width = (p * 100) + "%";
  if (window.scrollY > 40) hint.classList.add("hide");
}
let lastT = performance.now();
function tick(t) {
  const dt = Math.min(0.1, (t - lastT) / 1000); lastT = t;
  const k = 1 - Math.pow(1 - CFG.ease, dt * 60);       // same feel on 60Hz and 144Hz screens
  current += (target - current) * k;
  if (Math.abs(target - current) < 0.005) current = target;
  const img = nearestLoaded(Math.round(current));
  if (img && img !== lastImg) { paint(img); lastImg = img; }
  requestAnimationFrame(tick);
}
function start() {
  resize(); onScroll(); current = target;
  loader.classList.add("hide");
  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", resize);
  requestAnimationFrame(tick);
}

/* ---------- toast + dialog ---------- */
let toastTimer;
function toast(msg, ms = 3200) {
  const t = $("toast"); t.textContent = msg; t.classList.add("on");
  clearTimeout(toastTimer); if (ms) toastTimer = setTimeout(() => t.classList.remove("on"), ms);
}
function hideToast() { clearTimeout(toastTimer); $("toast").classList.remove("on"); }

const ICONS = {
  ok:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  bad:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 7v6m0 4h.01"/></svg>',
  busy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11m0 0l-4-4m4 4l4-4"/><path d="M4 19h16"/></svg>'
};
let modalLocked = false;
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function showModal({ kind, title, text, path, progress, buttons = [], lock = false }) {
  const card = $("card"); card.textContent = "";
  const ico = el("div", "ico " + kind); ico.innerHTML = ICONS[kind]; card.append(ico);
  card.append(el("h2", "", title));
  if (text) card.append(el("p", "", text));
  if (path) card.append(el("div", "path", path));
  if (progress) { const b = el("div", "bar"); const i = el("i"); i.id = "mfill"; b.append(i); card.append(b); }
  if (buttons.length) {
    const row = el("div", "row");
    buttons.forEach(b => { const x = el("button", "btn" + (b.primary ? " primary" : ""), b.label); x.onclick = b.onClick; row.append(x); });
    card.append(row);
  }
  modalLocked = lock; $("modal").classList.add("on");
  const f = card.querySelector(".btn.primary") || card.querySelector(".btn"); if (f) f.focus();
}
function closeModal() { if (modalLocked) return; $("modal").classList.remove("on"); }
$("modal").addEventListener("mousedown", e => { if (e.target === $("modal")) closeModal(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });

/* ---------- Fullscreen (Esc to exit) ---------- */
function toggleFullscreen() {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen().catch(err => console.warn(err));
  } else {
    document.exitFullscreen().catch(err => console.warn(err));
  }
}
function updateFullscreenUi() {
  const isFs = !!document.fullscreenElement;
  if ($("fsIcoEnter")) $("fsIcoEnter").hidden = isFs;
  if ($("fsIcoExit")) $("fsIcoExit").hidden = !isFs;
  if ($("fsText")) $("fsText").textContent = isFs ? "Exit Fullscreen" : "Fullscreen";
}
$("fsBtn").onclick = toggleFullscreen;
document.addEventListener("fullscreenchange", updateFullscreenUi);

/* ---------- Download frames: Modern Native Picker + Server & ZIP Fallback ---------- */
let saving = false;

async function downloadFramesNativeBrowser() {
  if (window.showDirectoryPicker) {
    try {
      const handle = await window.showDirectoryPicker({ mode: "readwrite" });
      if (!handle) return false;
      saving = true;
      showModal({ kind: "busy", title: "Saving frames to folder…", text: `Writing ${N} frames to "${handle.name}/frames"…`, progress: true, lock: true });
      const framesDir = await handle.getDirectoryHandle("frames", { create: true });
      for (let i = 0; i < N; i++) {
        const res = await fetch(url(i));
        const blob = await res.blob();
        const fname = `${CFG.prefix}${String(i + 1).padStart(CFG.pad, "0")}.${CFG.ext}`;
        const fileHandle = await framesDir.getFileHandle(fname, { create: true });
        const writer = await fileHandle.createWritable();
        await writer.write(blob);
        await writer.close();
        const pct = Math.round(((i + 1) / N) * 100);
        if ($("mfill")) $("mfill").style.width = pct + "%";
        const pTag = $("card").querySelector("p");
        if (pTag) pTag.textContent = `Saved ${i + 1} of ${N} frames (${pct}%)…`;
      }
      saving = false;
      modalLocked = false;
      showModal({
        kind: "ok",
        title: "All frames saved!",
        text: `${N} frames were successfully saved into "${handle.name}/frames" on your computer.`,
        buttons: [
          { label: "Close", primary: true, onClick: closeModal },
          { label: "Download as ZIP too", onClick: () => { location.href = "/download.zip"; } }
        ]
      });
      return true;
    } catch (err) {
      saving = false;
      modalLocked = false;
      if (err.name === "AbortError") {
        toast("Folder selection cancelled.");
        return true;
      }
      console.warn("Native browser folder picker error:", err);
      return false;
    }
  }
  return false;
}

$("dl").onclick = async () => {
  if (saving) return;
  // 1. Try modern browser Directory Picker first (Chromium / Windows Explorer)
  const handled = await downloadFramesNativeBrowser();
  if (handled) return;

  // 2. Choice modal: Server-side Folder Window or Direct ZIP
  showModal({
    kind: "busy",
    title: "Download Frames",
    text: `Save all ${N} frames (${CFG.mb || 0} MB) to your computer:`,
    buttons: [
      {
        label: "📁 Select Folder on Computer",
        primary: true,
        onClick: () => {
          closeModal();
          startServerSave();
        }
      },
      {
        label: "📦 Download as ZIP file",
        onClick: () => {
          location.href = "/download.zip";
          closeModal();
          toast("Downloading ZIP archive…");
        }
      },
      { label: "Cancel", onClick: closeModal }
    ]
  });
};

function startServerSave() {
  if (saving) return;
  saving = true;
  fetch("/download-frames", { method: "POST", headers: H })
    .then(r => {
      if (!r.ok) throw new Error("Could not start folder picker.");
      pollSave();
    })
    .catch(e => {
      saving = false;
      showModal({
        kind: "bad",
        title: "Folder picker issue",
        text: "Could not open folder picker window. You can download all the frames as a ZIP file directly instead.",
        buttons: [
          { label: "Download ZIP", primary: true, onClick: () => { location.href = "/download.zip"; closeModal(); } },
          { label: "Close", onClick: closeModal }
        ]
      });
    });
}

function pollSave() {
  fetch("/save-status").then(r => r.json()).then(s => {
    if (s.state === "picking") { toast("Choose a folder in the window that opened…", 0); return setTimeout(pollSave, 400); }
    hideToast();
    if (s.state === "saving") {
      if (!$("mfill")) showModal({ kind: "busy", title: "Saving frames…", text: "Please wait, this only takes a moment.", progress: true, lock: true });
      $("mfill").style.width = s.pct + "%"; $("card").querySelector("p").textContent = s.message;
      return setTimeout(pollSave, 250);
    }
    saving = false; modalLocked = false;
    if (s.state === "done") {
      showModal({ kind: "ok", title: "Frames saved", text: `${s.count} frames (${s.mb} MB) were saved in:`, path: s.folder,
        buttons: [
          { label: "Open frames saved folder", primary: true, onClick: () =>
              fetch("/open-folder", { method: "POST", headers: H }).then(r => r.json()).then(j => { if (!j.ok) toast("Could not open it automatically. Open the folder shown above."); }) },
          { label: "Close", onClick: closeModal } ] });
    } else if (s.state === "cancelled") {
      $("modal").classList.remove("on"); toast("No folder chosen, so nothing was saved.");
    } else if (s.state === "unavailable") {
      showModal({ kind: "bad", title: "Can't open a folder window", text: "This computer has no folder picker available. You can download all the frames as a ZIP file instead.",
        buttons: [{ label: "Download ZIP", primary: true, onClick: () => { location.href = "/download.zip"; closeModal(); } }, { label: "Close", onClick: closeModal }] });
    } else {
      showModal({ kind: "bad", title: "Could not save the frames", text: s.message || "Something went wrong.", buttons: [{ label: "Download as ZIP instead", primary: true, onClick: () => { location.href = "/download.zip"; closeModal(); } }, { label: "Close", onClick: closeModal }] });
    }
  }).catch(() => setTimeout(pollSave, 700));
}

$("new").onclick = async () => {
  frames.length = 0;
  try { await fetch("/reset", { method: "POST", headers: H }); } catch (_) {}
  location.href = "/";
};
window.addEventListener("pagehide", () => {
  frames.length = 0;
});
$("edit").onclick = () => { location.href = "/?edit=1"; };

loadAll();
</script>
</body>
</html>
"""


def upload_page():
    consts = {"smooth": [[n, p, e] for n, p, e in SMOOTH], "ref_vh": REF_VH,
              "mem": MEM_BUDGET, "max_frames": MAX_FRAMES,
              "probe": dict(PROBE) if PROBE and UPLOADED["path"] else None,
              "cfg": dict(PREVIEW["cfg"]) if PREVIEW["cfg"] else None,
              "filename": Path(UPLOADED["path"]).name if UPLOADED["path"] else None}
    return UPLOAD_HTML.replace("__PLAN__", json.dumps(consts))


def viewer_page():
    return VIEWER_HTML.replace("__CONFIG__", json.dumps(PREVIEW["cfg"]))


# --------------------------------------------------------------------------
# 6. Server
# --------------------------------------------------------------------------
class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def _send(self, body, ctype, code=200, extra=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(json.dumps(obj), "application/json", code)

    def _redirect(self, where):
        self.send_response(302)
        self.send_header("Location", where)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _local_host(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in ("localhost", "127.0.0.1", "::1")

    def do_GET(self):
        if not self._local_host():
            return self._send("Forbidden", "text/plain", 403)
        u = urlparse(self.path)
        try:
            if u.path == "/":
                self._send(upload_page(), "text/html; charset=utf-8")
            elif u.path == "/status":
                with LOCK:
                    self._json(dict(JOB))
            elif u.path == "/save-status":
                with LOCK:
                    self._json(dict(SAVE))
            elif u.path in ("/view", "/view/"):
                if not PREVIEW["cfg"]:
                    return self._redirect("/")
                self._send(viewer_page(), "text/html; charset=utf-8")
            elif u.path.startswith("/view/frames/"):
                name = u.path[len("/view/frames/"):]
                f = PREVIEW["dir"] and FRAME_RE.fullmatch(name) and Path(PREVIEW["dir"]) / name
                if not f or not f.is_file():
                    return self._send("Not found", "text/plain", 404)
                if name.endswith(".webp"):
                    ctype = "image/webp"
                elif name.endswith(".png"):
                    ctype = "image/png"
                elif name.endswith(".avif"):
                    ctype = "image/avif"
                else:
                    ctype = "image/jpeg"
                self._send(f.read_bytes(), ctype)
            elif u.path == "/download.zip":
                self._zip()
            else:
                self._send("Not found", "text/plain", 404)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _zip(self):
        files = sorted(Path(PREVIEW["dir"]).glob("frame_*")) if PREVIEW["dir"] else []
        if not files:
            return self._send("Nothing to download yet.", "text/plain", 404)
        with tempfile.TemporaryFile() as tmp:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
                for f in files:
                    z.write(f, f"frames/{f.name}")
            size = tmp.tell()
            tmp.seek(0)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", 'attachment; filename="frames.zip"')
            self.send_header("Content-Length", str(size))
            self.end_headers()
            shutil.copyfileobj(tmp, self.wfile)

    def do_POST(self):
        # Blocks other websites from poking this local server (they cannot send this header).
        if not self._local_host() or self.headers.get("X-SV") != "1":
            return self._send("Forbidden", "text/plain", 403)
        u = urlparse(self.path)
        if u.path == "/upload":
            return self._upload(u)
        if u.path == "/build":
            return self._build()
        if u.path == "/download-frames":
            return self._download_frames()
        if u.path == "/reset":
            cleanup_all()
            return self._json({"ok": True})
        if u.path == "/open-folder":
            return self._json({"ok": bool(LAST_SAVED["folder"]) and open_folder(LAST_SAVED["folder"])})
        self._send("Not found", "text/plain", 404)

    def _busy(self):
        with LOCK:
            return JOB["state"] == "processing" or SAVE["state"] in ("picking", "saving")

    def _upload(self, u):
        if self._busy():
            return self._json({"error": "Something is still in progress. Please wait."}, 409)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length <= 0:
            return self._json({"error": "Empty upload."}, 400)

        suffix = Path(unquote(q.get("name", "video.mp4"))).suffix.lower()
        suffix = suffix if suffix in VIDEO_EXT else ".mp4"
        shutil.rmtree(UPLOAD_DIR, ignore_errors=True)
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        dest = UPLOAD_DIR / f"video{suffix}"

        remaining = length
        with open(dest, "wb") as f:
            while remaining > 0:
                chunk = self.rfile.read(min(1 << 20, remaining))
                if not chunk:
                    break
                f.write(chunk)
                remaining -= len(chunk)
        if remaining > 0:
            return self._json({"error": "Upload was interrupted. Please try again."}, 400)
        try:
            info = probe_video(dest)
        except Exception as e:
            return self._json({"error": str(e)}, 400)
        PROBE.clear()
        PROBE.update(info)
        UPLOADED["path"] = dest
        set_job(state="idle", pct=0, message="", result=None)
        self._json(info)

    def _build(self):
        if self._busy():
            return self._json({"error": "Already working on it."}, 409)
        if not PROBE or not UPLOADED["path"] or not Path(UPLOADED["path"]).exists():
            return self._json({"error": "Please upload a video first."}, 400)
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json({"error": "Bad settings."}, 400)
        opts = clean_opts(data)
        set_job(state="processing", pct=0, message="Starting…", result=None)
        threading.Thread(target=build_preview, args=(UPLOADED["path"], dict(PROBE), opts), daemon=True).start()
        self._json({"ok": True})

    def _download_frames(self):
        if self._busy():
            return self._json({"error": "Already working on it."}, 409)
        if not PREVIEW["dir"]:
            return self._json({"error": "No frames yet. Create the website first."}, 400)
        set_save(state="picking", pct=0, message="", folder=None)
        threading.Thread(target=download_frames_job, daemon=True).start()
        self._json({"ok": True})


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Turn a video into a scroll-controlled website.")
    ap.add_argument("video", nargs="?", help="optional: build straight from this video, skipping the upload page")
    ap.add_argument("--screens", type=int, default=DEFAULTS["screens"], help="scroll length in screens (2-1000)")
    ap.add_argument("--smoothness", type=float, default=DEFAULTS["smoothness"], help="continuous smoothness from 1 (light/not smooth) to 100 (ultra/very smooth), or 1..5")
    ap.add_argument("--width", type=int, default=DEFAULTS["width"], help="1280, 1920, 2560 or 3840")
    ap.add_argument("--format", choices=["webp", "jpg", "png", "avif"], default=DEFAULTS["format"])
    ap.add_argument("--quality", type=int, default=DEFAULTS["compression"], help="image quality 40-100")
    ap.add_argument("--frames", type=int, default=0, help="force an exact frame count (default: automatic)")
    ap.add_argument("--fit", choices=["cover", "contain"], default="cover")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    cleanup_all()  # Clean up any leftover temp files on startup

    start_path = "/"
    if a.video:
        p = Path(a.video)
        if not p.is_file():
            sys.exit(f"File not found: {a.video}")
        try:
            probe = probe_video(p)
        except RuntimeError as e:
            sys.exit(str(e))
        o = clean_opts({"screens": a.screens, "smoothness": a.smoothness, "width": a.width,
                        "format": a.format, "compression": a.quality, "frames": a.frames, "fit": a.fit})
        pl = make_plan(probe, o)
        print(f"Video: {probe['width']}x{probe['height']}, {probe['duration']}s, {probe['frames']} frames")
        print(f"Plan : {pl['count']} frames at {pl['width']}x{pl['height']}")
        for n in pl["notes"]:
            print("Note :", n)
        build_preview(p, probe, o)
        if JOB["state"] == "error":
            sys.exit(JOB["message"])
        PROBE.update(probe)
        start_path = "/view/"

    for port in range(a.port, a.port + 20):
        try:
            httpd = Server(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    else:
        sys.exit("No free port found. Try --port 9000")

    print(f"\nScroll Video Maker is running at  http://localhost:{port}/")
    print("Press Ctrl+C to stop.\n")
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(f"http://localhost:{port}{start_path}")).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        cleanup_all()
        print("\nCleaned up all temporary files and stopped.")


if __name__ == "__main__":
    main()

# 🎬 Scroll Video Maker

> **Turn any video into an interactive, high-performance scroll-driven web animation.**  
> Create smooth, cinematic Apple-style product showcases and interactive landing pages in seconds without writing a single line of frontend code.

---

## ⚡ Try the Live Demo Before Installing

Compare the original linear video with the generated interactive scroll website:

<div align="center">

### 🎥 Original Source Video
<video src="https://raw.githubusercontent.com/Harish-Huded-29/video-scroll-animation-project/main/video-and-frames/video.mp4" controls width="100%" style="max-width:720px; border-radius:12px; box-shadow:0 10px 30px rgba(0,0,0,0.5);">
  <a href="video-and-frames/video.mp4">Click to view source video (video-and-frames/video.mp4)</a>
</video>

<br><br>

### 🌐 Interactive Scroll Website (Generated)
*Scrolling down plays forward · Scrolling up plays backward · Includes Fullscreen mode & Esc key*

<br>

<a href="https://htmlpreview.github.io/?https://github.com/Harish-Huded-29/video-scroll-animation-project/blob/main/video-and-frames/index.html" target="_blank">
  <img src="https://img.shields.io/badge/▶%20CLICK%20TO%20OPEN%20LIVE%20SCROLL%20WEBSITE-6366f1?style=for-the-badge&logo=googlechrome&logoColor=white" alt="Open Live Demo Website" height="42">
</a>

<br><br>

> 💡 **Instant Local Test:** You can also double-click [`video-and-frames/index.html`](video-and-frames/index.html) or [`demo.html`](demo.html) right in your cloned folder to test scrolling immediately in your browser — zero installation needed!

</div>

---

## ✨ Key Features

- 🎞️ **Interactive Scroll Scrubber:** Scrolling down plays forward; scrolling up plays backward with fluid inertia.
- 🎚️ **Continuous Smoothness Slider (1–100):** Continuous fine-tuning between lightweight/fast-loading (`Light`) and butter-smooth motion (`Ultra`).
- 📏 **Customizable Scroll Length:** Drag the slider up to 100 screens, or type any number of screens you want (e.g. `20`, `75`, `250`).
- 🖥️ **Fullscreen Mode:** One-click full-screen toggle with native **`Esc`** key support.
- 🖼️ **Modern Image Formats:** Export in **WebP** (recommended, small & fast), **AVIF** (ultra-compact), **JPG** (universal compatibility), or **PNG** (lossless pixel perfection).
- 📐 **Multiple Resolutions:** Supports **HD 720p**, **Full HD 1080p**, **2K 1440p**, and **4K 2160p**.
- 📦 **Dual-Mode Frame Downloader:**
  - **Native Folder Picker:** In modern Chrome/Edge, saves all frames directly into a `frames/` subfolder on your computer with real-time percentage progress.
  - **1-Click ZIP Download:** Instant `.zip` archive download fallback for any device.
- 🧹 **Zero-Leak Memory & Storage Guarantee:** All temporary files and memory buffers are purged when exiting or resetting.
- ✏️ **Live In-App Editor:** Return to the settings screen anytime to re-tune length, smoothness, or resolution without re-uploading.

---

## 🚀 How to Use

Choose whichever option works best for you:

### 🌟 Option 1: Standalone Application (`ScrollVideoMaker.exe`) — *No Python Needed!*

*(Pre-packaged with Python, OpenCV, and all dependencies. Runs standalone on any Windows PC!)*

<div align="center">

<a href="https://github.com/Harish-Huded-29/video-scroll-animation-project/releases/download/v1.0.0/ScrollVideoMaker.exe">
  <img src="https://img.shields.io/badge/⬇%20DOWNLOAD%20ScrollVideoMaker.exe-v1.0.0%20(Windows)-22c55e?style=for-the-badge&logo=windows&logoColor=white" alt="Download Standalone EXE" height="50">
</a>

<br><br>
<small>Direct repository mirror: <a href="app/ScrollVideoMaker.exe"><b><code>app/ScrollVideoMaker.exe</code></b></a></small>

</div>

#### Quick Steps:
1. Click the green button above to download **`ScrollVideoMaker.exe`**.
2. Double-click **`ScrollVideoMaker.exe`** to launch.
3. Your browser will automatically open at `http://localhost:8000`.
4. Drag and drop any video file (`.mp4`, `.mov`, `.webm`, `.avi`, `.mkv`), adjust your settings, and click **Make scroll website**!

> [!TIP]
> ### 🛡️ Windows SmartScreen / Security Manager Prompt:
> Because this is a newly released open-source project without a commercial corporate certificate, Windows may show a standard blue prompt saying *"Windows protected your PC"*.
> 
> **To run the application:**
> 1. Click **"More info"** on the prompt.
> 2. Click **"Run anyway"**.
> 
> *(Or right-click `ScrollVideoMaker.exe` → **Properties** → check **Unblock** at the bottom → click **OK**).*

---

### 🐍 Option 2: Run Directly with Python (Using Terminal)

#### Step 1: Requirements
Make sure you have **Python 3.8+** installed ([Download Python](https://www.python.org/downloads/)).

#### Step 2: Download or Clone
```bash
git clone https://github.com/Harish-Huded-29/video-scroll-animation-project.git
cd video-scroll-animation-project
```
*(Or download the single file [`scrollvideo.py`](https://raw.githubusercontent.com/Harish-Huded-29/video-scroll-animation-project/main/scrollvideo.py))*

#### Step 3: Install Dependencies
```bash
pip install -r requirements.txt
```
*(Or install directly: `pip install opencv-python Pillow`)*

#### Step 4: Launch Web App
```bash
python scrollvideo.py
```
Open your browser at `http://localhost:8000`.

---

### 💻 Option 3: Command-Line (CLI) Direct Mode

You can also bypass the upload screen and build a scroll animation directly from your terminal:

```bash
# Basic usage
python scrollvideo.py my_video.mp4

# Advanced customization
python scrollvideo.py my_video.mp4 --screens 25 --smoothness 75 --width 1920 --format webp --fit cover
```

#### Available Terminal Flags

| Flag | Default | Description |
|---|:---:|---|
| `video` | *None* | Path to your video file (`.mp4`, `.mov`, `.webm`, `.mkv`, etc.) |
| `--screens` | `20` | Scroll length in page heights (2 to 1000) |
| `--smoothness` | `50` | Smoothness level from `1` (Light) to `100` (Ultra) or preset `1`..`5` |
| `--width` | `1920` | Resolution width: `1280` (720p), `1920` (1080p), `2560` (2K), or `3840` (4K) |
| `--format` | `webp` | Frame format: `webp`, `jpg`, `png`, or `avif` |
| `--quality` | `82` | Image compression quality (`40` to `100`) |
| `--fit` | `cover` | Sizing behavior: `cover` (fills screen) or `contain` (no cropping) |
| `--frames` | `0` (Auto) | Override and set an exact frame count |
| `--port` | `8000` | Local web server port |
| `--no-browser` | `False` | Start the server without opening the default browser |

---

## 🛠️ How to Build the `.exe` File

If you want to compile the `.exe` binary yourself from source:

1. Install PyInstaller:
   ```bash
   pip install pyinstaller
   ```

2. Build the signed binary:
   ```bash
   python -m PyInstaller --noconfirm --clean --onefile --version-file version_info.txt --distpath app --workpath build --name "ScrollVideoMaker" --collect-all cv2 --collect-all PIL scrollvideo.py
   ```

3. The compiled binary will be located in:
   ```
   app/ScrollVideoMaker.exe
   ```

---

## 📂 Project Structure

```
video-scroll-animation-project/
├── app/
│   └── ScrollVideoMaker.exe   # Prebuilt standalone Windows binary (no Python needed)
├── scrollvideo.py             # Complete Python application (web server + frame engine + UI)
├── demo.html                  # Quick interactive demo launcher
├── video-and-frames/
│   ├── video.mp4              # Sample input video
│   ├── index.html             # Standalone scroll demo website with Fullscreen & Esc
│   └── frames/                # 265 extracted WebP animation frames
├── version_info.txt           # Windows application metadata & manifest
├── requirements.txt           # Python dependencies (opencv-python, Pillow)
├── .gitignore                 # Git exclusion rules
└── README.md                  # Documentation & user guide
```

---

## 👤 Author

Developed by **[Harish Huded](https://github.com/Harish-Huded-29)**

Feel free to star ⭐ the repository if you find this tool helpful!

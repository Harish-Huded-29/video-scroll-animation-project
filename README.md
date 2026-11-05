# 🎬 Scroll Video Maker

> **Turn any video into an interactive, high-performance scroll-driven web animation.**  
> Create smooth, cinematic Apple-style product showcases and interactive landing pages in seconds without writing a single line of frontend code.

---

## ⚡ Try the Live Demo Before Installing

You can experience how the interactive scroll animation works right now — **no installation required!**

| 🎥 Source Video | 🌐 Generated Scroll Website |
|:---:|:---:|
| **Original 24fps Video** | **Interactive Scroll Website** |
| [`video-and-frames/video.mp4`](video-and-frames/video.mp4) | [`video-and-frames/index.html`](video-and-frames/index.html) *(or [`demo.html`](demo.html))* |
| Standard linear video playback | Controlled by your scroll wheel, trackpad, or touch gesture |

### How to test the demo locally:
1. Double-click **`video-and-frames/index.html`** (or **`demo.html`**) to open it in your web browser.
2. **Scroll down ↓** to play the video forward.
3. **Scroll up ↑** to play the video in reverse.
4. Click the **⛶ Fullscreen** button at the top-right (or press **`Esc`** to exit) for an immersive full-screen experience!

---

## ✨ Key Features

- 🎞️ **Interactive Scroll Scrubber:** Scrolling down plays forward; scrolling up plays backward with fluid inertia.
- 🎚️ **Continuous Smoothness Slider (1–100):** Continuous fine-tuning between lightweight/fast-loading (`Light`) and butter-smooth motion (`Ultra`).
- 📏 **Customizable Scroll Length:** Drag the slider up to 100 screens, or type any number of screens you want (e.g. `20`, `75`, `250`).
- 🖥️ **Fullscreen Mode:** One-click full-screen toggle with native **`Esc`** key support.
- 🖼️ **Modern Image Formats:** Export in **WebP** (recommended, small & fast), **AVIF** (ultra-compact), **JPG** (universal compatibility), or **PNG** (lossless pixel perfection).
- 📐 **Multiple Resolutions:** Supports **HD 720p**, **Full HD 1080p**, **2K 1440p**, and **4K 2160p**.
- 📦 **Dual-Mode Frame Downloader:**
  - **Native Folder Picker:** Pick any folder on your computer to save all frames directly into a `frames/` subfolder.
  - **1-Click ZIP Download:** Instant `.zip` archive download fallback for any device.
- ✏️ **Live In-App Editor:** Return to the settings screen anytime to re-tune length, smoothness, or resolution without re-uploading.
- 🌌 **Visual UI:** Fullscreen 3D particle background, interactive 3D logo physics, and a modern dark glassmorphism interface.

---

## 🚀 How to Use

Choose whichever option works best for you:

### 🌟 Option 1: Standalone Application (`ScrollVideoMaker.exe`) — *No Python Needed!*

*(Ideal for non-coders and quick usage without setting up any programming environment)*

1. Download **`ScrollVideoMaker.exe`** from this repository (or from [Releases](https://github.com/Harish-Huded-29/video-scroll-animation-project/releases)).
2. Double-click **`ScrollVideoMaker.exe`** to launch.
3. Your browser will automatically open at `http://localhost:8000`.
4. Drag and drop any video file (`.mp4`, `.mov`, `.webm`, `.avi`, `.mkv`), customize your settings, and click **Make scroll website**!

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

If you want to package `scrollvideo.py` into a single standalone `.exe` binary:

1. Install PyInstaller:
   ```bash
   pip install pyinstaller
   ```

2. Build the one-file binary:
   ```bash
   pyinstaller --onefile --name "ScrollVideoMaker" scrollvideo.py
   ```

3. The compiled binary will be generated at:
   ```
   dist/ScrollVideoMaker.exe
   ```

---

## 📂 Project Structure

```
video-scroll-animation-project/
├── scrollvideo.py             # Complete application (web server + frame engine + UI)
├── demo.html                  # Quick interactive demo launcher
├── video-and-frames/
│   ├── video.mp4              # Sample input video
│   ├── index.html             # Standalone scroll demo website with Fullscreen & Esc
│   └── frames/                # 265 extracted WebP animation frames
├── requirements.txt           # Python dependencies (opencv-python, Pillow)
├── .gitignore                 # Git exclusion rules (allows *.exe)
└── README.md                  # Documentation & user guide
```

---

## 👤 Author

Developed by **[Harish Huded](https://github.com/Harish-Huded-29)**

Feel free to star ⭐ the repository if you find this tool helpful!

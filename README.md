# 🎬 Scroll Video Maker

> **Turn any video into an interactive, high-performance scroll-driven web animation.**  
> Create smooth, cinematic Apple-style product showcases and interactive landing pages in seconds without writing a single line of frontend code.

---

## ✨ Features

- 🎞️ **Interactive Scroll Playback:** Scrolling down plays your video forward; scrolling up plays it in reverse.
- 🎚️ **Continuous Smoothness Tuning (1–100):** Continuous fine-tuning between lightweight/fast-loading (`Light`) and butter-smooth motion (`Ultra`).
- 📏 **Flexible Scroll Length:** Easily drag the slider or type any number of screen heights (e.g., 20, 75, 200+ screens).
- 🖼️ **Modern Image Formats:** Supports **WebP** (recommended, small & fast), **AVIF** (next-gen ultra-compact), **JPG** (universal compatibility), and **PNG** (lossless pixel perfection).
- 📐 **Multiple Resolutions:** Export in **HD 720p**, **Full HD 1080p**, **2K 1440p**, or **4K 2160p**.
- 🖥️ **Fit Modes:** Choose between **Cover** (fills entire screen edge-to-edge) or **Contain** (shows full uncropped video).
- 📦 **1-Click Frame Downloader:** Download all generated image sequence frames directly into any local folder on your computer.
- ✏️ **Built-in Editor:** Tweak and re-adjust scroll settings on the fly from the live preview screen.
- 🌌 **Visual Experience:** Fullscreen 3D wave background, interactive 3D logo physics, and a modern dark glassmorphism interface.

---

## 🚀 How to Use

Choose whichever method is easiest for you:

### 🌟 Option 1: Standalone Application (`.exe` for Windows) — *No Python Needed!*

*(Perfect for non-coders and quick usage without setting up any programming environment)*

1. Download `ScrollVideoMaker.exe` from the repository or [Releases](https://github.com/Harish-Huded-29/video-scroll-animation-project/releases).
2. Double-click **`ScrollVideoMaker.exe`** to run.
3. Your web browser will automatically open at `http://localhost:8000`.
4. Drag and drop any video file (`.mp4`, `.mov`, `.webm`, `.avi`, `.mkv`), customize your settings, and click **Make scroll website**!

---

### 🐍 Option 2: Run Directly with Python (Using Terminal)

#### Step 1: Requirements
Make sure you have **Python 3.8+** installed ([Download Python](https://www.python.org/downloads/)).

#### Step 2: Download or Clone
You can clone the repository:
```bash
git clone https://github.com/Harish-Huded-29/video-scroll-animation-project.git
cd video-scroll-animation-project
```

*Or simply download just the single file [`scrollvideo.py`](https://raw.githubusercontent.com/Harish-Huded-29/video-scroll-animation-project/main/scrollvideo.py).*

#### Step 3: Install Dependencies
Open your terminal (PowerShell, Command Prompt, or Bash) in the project folder and run:
```bash
pip install -r requirements.txt
```
*(Or install directly: `pip install opencv-python Pillow`)*

#### Step 4: Launch the Web App
Run the following command:
```bash
python scrollvideo.py
```
Your browser will open `http://localhost:8000` where you can upload and configure your video visually.

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

If you want to package `scrollvideo.py` into a single standalone `.exe` executable for Windows:

1. Install PyInstaller:
   ```bash
   pip install pyinstaller
   ```

2. Build the one-file binary:
   ```bash
   pyinstaller --onefile --name "ScrollVideoMaker" scrollvideo.py
   ```

3. The compiled executable will be located in the `dist/` directory:
   ```
   dist/ScrollVideoMaker.exe
   ```

You can share or commit `ScrollVideoMaker.exe` so anyone on Windows can use it with zero installation.

---

## 📂 Project Structure

```
video-scroll-animation-project/
├── scrollvideo.py      # Complete, standalone app (web server + frame engine + UI)
├── requirements.txt    # Python dependencies (opencv-python, Pillow)
├── .gitignore          # Git exclusion rules
└── README.md           # Documentation & user guide
```

---

## 💡 Tips & Best Practices

- **Recommended Settings:** For crisp web animations, **Full HD (1080p)** with **WebP format** and **Balanced/Smooth** mode offers the best balance of visual clarity and lightning-fast load times.
- **Scroll Length:** If you want a longer, more dramatic story, type a higher number like `50` or `100` in the *Scroll length* box.
- **Download Frames:** Once in the preview screen, click **"Download frames"** in the top-right corner to save every individual frame into any folder on your computer for use in external web projects (e.g., GSAP, Webflow, Framer, React).

---

## 👤 Author

Developed by **[Harish Huded](https://github.com/Harish-Huded-29)**

Feel free to star ⭐ the repository if you find this tool helpful!

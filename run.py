import os
import sys
import threading
import webbrowser
import time
from pathlib import Path

_STDIO_FALLBACK_HANDLES = []


def _ensure_standard_streams() -> None:
    """Provide file-like stdio streams in windowed/noconsole builds."""
    if sys.stdin is None:
        stdin_fallback = open(os.devnull, "r", encoding="utf-8", errors="replace")
        _STDIO_FALLBACK_HANDLES.append(stdin_fallback)
        sys.stdin = stdin_fallback
    if sys.stdout is None:
        stdout_fallback = open(os.devnull, "w", encoding="utf-8", errors="replace")
        _STDIO_FALLBACK_HANDLES.append(stdout_fallback)
        sys.stdout = stdout_fallback
    if sys.stderr is None:
        stderr_fallback = open(os.devnull, "w", encoding="utf-8", errors="replace")
        _STDIO_FALLBACK_HANDLES.append(stderr_fallback)
        sys.stderr = stderr_fallback


_ensure_standard_streams()

# Fix for PyInstaller paths
if hasattr(sys, '_MEIPASS'):
    # Running in a bundle
    BASE_DIR = Path(sys._MEIPASS)
    EXE_DIR = Path(sys.executable).parent
else:
    # Running in normal Python
    BASE_DIR = Path(__file__).parent / "backend"
    EXE_DIR = Path(__file__).parent

# Ensure we can import backend packages if not in bundle
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Download FFmpeg on start if missing
try:
    import config
    from ffmpeg_helper import start_background_download
    # Download into config.STORAGE_ROOT/bin (user-writable) rather than the
    # app/executable directory.
    start_background_download(config.STORAGE_ROOT / "bin")
except Exception as e:
    print(f"Failed to setup FFmpeg: {e}")

# Ensure ffmpeg and other bundled binaries are found in PATH
try:
    import config
    storage_bin = config.STORAGE_ROOT / "bin"
except Exception:
    storage_bin = None

path_parts = []
if storage_bin is not None:
    path_parts.append(str(storage_bin))
path_parts.append(str(EXE_DIR))
path_parts.append(os.environ.get("PATH", ""))
path_parts.append(str(BASE_DIR))
os.environ["PATH"] = os.pathsep.join(p for p in path_parts if p)


def open_browser(url):
    # Wait a bit for the server to start
    time.sleep(1.5)
    webbrowser.open(url)

if __name__ == "__main__":
    # Ensure frontend and uploads dirs are found
    os.chdir(BASE_DIR)
    
    # Path to the frontend folder
    # In a bundle, BASE_DIR/frontend should exist
    
    # Start server
    host = "127.0.0.1"
    port = 8002
    url = f"http://{host}:{port}"
    
    print(f"Starting AmicoScript at {url}...")
    
    # Start browser in a background thread
    if os.environ.get("AMICOSCRIPT_NO_BROWSER", "0") != "1":
        threading.Thread(target=open_browser, args=(url,), daemon=True).start()
    
    # Run uvicorn
    import main
    import uvicorn
    # In windowed/"noconsole" builds, stderr can be missing; avoid uvicorn's
    # default formatter setup that expects a TTY-backed stream.
    uvicorn.run(main.app, host=host, port=port, log_level="info", log_config=None)

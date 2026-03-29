import os
import sys
import threading
import uvicorn
import webbrowser
import time
from pathlib import Path

# Fix for PyInstaller paths
if hasattr(sys, '_MEIPASS'):
    # Running in a bundle
    BASE_DIR = Path(sys._MEIPASS)
    FFMPEG_DIR = Path(sys.executable).parent
else:
    # Running in normal Python
    BASE_DIR = Path(__file__).parent / "backend"
    FFMPEG_DIR = BASE_DIR

# Ensure we can import backend packages if not in bundle
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Download FFmpeg on start if missing
try:
    from ffmpeg_helper import get_ffmpeg_path
    get_ffmpeg_path(FFMPEG_DIR)
except Exception as e:
    print(f"Failed to setup FFmpeg: {e}")

# Ensure ffmpeg and other bundled binaries are found in PATH
os.environ["PATH"] = str(FFMPEG_DIR) + os.pathsep + os.environ.get("PATH", "") + os.pathsep + str(BASE_DIR)


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
    threading.Thread(target=open_browser, args=(url,), daemon=True).start()
    
    # Run uvicorn
    import main
    uvicorn.run(main.app, host=host, port=port, log_level="info")

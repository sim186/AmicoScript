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
else:
    # Running in normal Python
    BASE_DIR = Path(__file__).parent / "backend"

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

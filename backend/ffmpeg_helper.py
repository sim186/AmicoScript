import os
import sys
import platform
import urllib.request
import zipfile
import json
import shutil
import threading
from pathlib import Path
import config

_download_thread = None
_download_lock = threading.Lock()


def _exe_name() -> str:
    system = platform.system().lower()
    return "ffmpeg.exe" if system == "windows" else "ffmpeg"


def is_ffmpeg_available(base_dir: Path | None = None) -> bool:
    """Return True if ffmpeg is available in PATH or bundled in base_dir."""
    if shutil.which("ffmpeg"):
        return True
    if base_dir is None:
        base_dir = config.STORAGE_ROOT / "bin"
    exe = base_dir / _exe_name()
    return exe.exists()


def start_background_download(base_dir: Path | None = None) -> None:
    """Start a background thread to download ffmpeg if it's missing.

    Returns immediately; the worker will attempt to download and extract
    ffmpeg into `base_dir`.
    """
    global _download_thread
    if is_ffmpeg_available(base_dir):
        return
    if base_dir is None:
        base_dir = config.STORAGE_ROOT / "bin"
    base_dir.mkdir(parents=True, exist_ok=True)

    with _download_lock:
        if _download_thread is not None and _download_thread.is_alive():
            return
        _download_thread = threading.Thread(
            target=_download_worker, args=(base_dir,), daemon=True
        )
        _download_thread.start()


def _download_worker(base_dir: Path) -> None:
    try:
        get_ffmpeg_path(base_dir)
    except Exception as e:
        print(f"Background ffmpeg download failed: {e}")


def get_ffmpeg_path(base_dir: Path | None = None) -> Path:
    """Returns the path to the ffmpeg executable, downloading it if necessary."""
    # Determine OS and Arch
    system = platform.system().lower()
    machine = platform.machine().lower()

    exe_name = _exe_name()

    if base_dir is None:
        base_dir = config.STORAGE_ROOT / "bin"
    base_dir.mkdir(parents=True, exist_ok=True)

    # 1. Check if it's already in the base directory or on PATH
    local_ffmpeg = base_dir / exe_name
    if local_ffmpeg.exists() or shutil.which("ffmpeg"):
        return local_ffmpeg if local_ffmpeg.exists() else Path(shutil.which("ffmpeg"))
        
    # 2. Need to download it
    print(f"FFmpeg not found. Downloading for {system} {machine}...")
    
    # We use ffbinaries API to get the latest pre-built binary link
    api_url = "https://ffbinaries.com/api/v1/version/latest"
    try:
        import requests

        response = requests.get(api_url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # Map python platform to ffbinaries platform keys
        os_key = None
        if system == "windows":
            os_key = "windows-64"
        elif system == "darwin":
            # Prefer ARM64 builds when available; fall back to x86_64.
            if "arm64" in machine or "aarch64" in machine:
                os_key = "osx-arm64" if "osx-arm64" in data.get("bin", {}) else "osx-64"
            else:
                os_key = "osx-64"
        elif system == "linux":
            if "arm64" in machine or "aarch64" in machine:
                os_key = "linux-arm64"
            else:
                os_key = "linux-64"

        if not os_key or os_key not in data.get("bin", {}):
            print(f"Could not determine a download link for OS: {system} {machine}")
            return None
            
        download_url = data["bin"][os_key]["ffmpeg"]
        
        # Download the zip file
        zip_path = base_dir / "ffmpeg.zip"
        print(f"Downloading FFmpeg from {download_url}...")
        
        with requests.get(download_url, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(zip_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
                    
        print("Extracting FFmpeg...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            # Extract only the ffmpeg executable
            for info in zip_ref.infolist():
                if info.filename.endswith(exe_name):
                    info.filename = exe_name # Strip relative paths
                    zip_ref.extract(info, path=base_dir)
                    break
        
        # Cleanup zip file
        if zip_path.exists():
            os.remove(zip_path)
            
        # Make executable on Unix
        if system != "windows" and local_ffmpeg.exists():
            os.chmod(local_ffmpeg, 0o755)
            
        print("FFmpeg downloaded and extracted successfully!")
        return local_ffmpeg
        
    except Exception as e:
        print(f"Failed to download FFmpeg: {e}")
        return None

import os
import sys
import platform
import urllib.request
import zipfile
import json
from pathlib import Path

def get_ffmpeg_path(base_dir: Path) -> Path:
    """Returns the path to the ffmpeg executable, downloading it if necessary."""
    # Determine OS and Arch
    system = platform.system().lower()
    machine = platform.machine().lower()
    
    exe_name = "ffmpeg.exe" if system == "windows" else "ffmpeg"
    
    # 1. Check if it's already in the base directory
    local_ffmpeg = base_dir / exe_name
    if local_ffmpeg.exists():
        return local_ffmpeg
        
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
            os_key = "osx-64" # Rosetta or Universal usually works for ARM Macs
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
        if system != "windows":
            os.chmod(local_ffmpeg, 0o755)
            
        print("FFmpeg downloaded and extracted successfully!")
        return local_ffmpeg
        
    except Exception as e:
        print(f"Failed to download FFmpeg: {e}")
        return None

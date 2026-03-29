import os
import sys
import shutil
import PyInstaller.__main__

def build():
    # Detect OS
    is_windows = sys.platform.startswith('win')
    is_mac = sys.platform == 'darwin'
    
    # Define paths
    root = os.path.dirname(os.path.abspath(__file__))
    dist = os.path.join(root, "dist")
    build_dir = os.path.join(root, "build")
    
    # Clean up previous builds
    for d in [dist, build_dir]:
        if os.path.exists(d):
            print(f"Cleaning {d}...")
            shutil.rmtree(d)
            
    # PyInstaller arguments
    args = [
        'run.py',                          # Entry point
        '--name=AmicoScript',              # Output name
        '--onedir',                        # Better for large apps (faster launch/debug)
        '--noconsole',                     # No terminal window (if desired)
        '--add-data=frontend:frontend',    # Include frontend files
        '--hidden-import=faster_whisper',
        '--hidden-import=pyannote.audio',
        '--hidden-import=torch',
        '--hidden-import=torchaudio',
        '--hidden-import=uvicorn.streaming', # Hidden dependency
        '--hidden-import=sse_starlette.sse',
    ]

    if is_mac:
        args.append('--windowed') # Create .app
    
    print("Starting build with PyInstaller...")
    PyInstaller.__main__.run(args)
    
    print("\nDraft build complete!")
    print(f"Output available in: {dist}/AmicoScript")
    print("\nNote: You may need to manually bundle ffmpeg binaries in the dist folder if not in system path.")

if __name__ == "__main__":
    build()

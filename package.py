import os
import sys
import shutil
import PyInstaller.__main__

def build():
    # Detect OS
    is_windows = sys.platform.startswith('win')
    is_macos = sys.platform == 'darwin'

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
        '--paths=backend',                 # Make backend modules importable during analysis/runtime
        '--add-data=frontend:frontend',    # Include frontend files
        '--add-data=VERSION:.',            # Include VERSION at bundle root
        '--add-data=CHANGELOG.md:.',      # Include changelog
        '--hidden-import=main',            # backend/main.py imported dynamically in run.py
        '--hidden-import=ffmpeg_helper',   # backend/ffmpeg_helper.py imported dynamically in run.py
        '--hidden-import=faster_whisper',
        '--collect-data=faster_whisper',  # include VAD ONNX assets (silero_vad_v6.onnx)
        '--hidden-import=pyannote.audio',
        '--collect-data=pyannote.audio',  # include telemetry/config.yaml and other package assets
        '--hidden-import=torch',
        '--hidden-import=torchaudio',
        '--hidden-import=sse_starlette.sse',
    ]

    # Platform-specific UI flags
    if is_macos:
        # Create a macOS .app bundle. Provide a bundle identifier and optional icon.
        args.append('--windowed')
        # Set a bundle identifier (change to your reverse-domain identifier if desired)
        args.append('--osx-bundle-identifier=org.amico.AmicoScript')
        # Choose an .icns icon. Prefer images/AmicoScript.icns, otherwise pick any .icns in images/.
        icon_default = os.path.join(root, 'images', 'AmicoScript.icns')
        if os.path.exists(icon_default):
            icon_path = icon_default
        else:
            images_dir = os.path.join(root, 'images')
            icon_candidates = []
            if os.path.isdir(images_dir):
                for fn in os.listdir(images_dir):
                    if fn.lower().endswith('.icns'):
                        icon_candidates.append(os.path.join(images_dir, fn))
            icon_path = icon_candidates[0] if icon_candidates else None
        if icon_path:
            args.append(f'--icon={icon_path}')
    elif is_windows:
        # On Windows, avoid a console window and embed an .ico icon
        args.append('--noconsole')
        # Prefer images/AmicoScript.ico or the first .ico found in images/
        icon_default = os.path.join(root, 'images', 'AmicoScript.ico')
        if os.path.exists(icon_default):
            icon_path = icon_default
        else:
            images_dir = os.path.join(root, 'images')
            icon_candidates = []
            if os.path.isdir(images_dir):
                for fn in os.listdir(images_dir):
                    if fn.lower().endswith('.ico'):
                        icon_candidates.append(os.path.join(images_dir, fn))
            icon_path = icon_candidates[0] if icon_candidates else None
        if icon_path:
            args.append(f'--icon={icon_path}')

    if is_windows:
        version_file_path = None
        try:
            root_version = os.path.join(root, 'VERSION')
            if os.path.exists(root_version):
                ver_text = open(root_version, 'r', encoding='utf-8').read().strip()
            else:
                ver_text = '0.0.0'
            ver_nums = ver_text.split('.')
            while len(ver_nums) < 3:
                ver_nums.append('0')
            filevers = tuple(int(x) if x.isdigit() else 0 for x in (ver_nums + ['0'])[:4])

            build_meta_dir = os.path.join(root, 'buildmeta')
            os.makedirs(build_meta_dir, exist_ok=True)
            version_file_path = os.path.join(build_meta_dir, 'version_info.txt')
            with open(version_file_path, 'w', encoding='utf-8') as vf:
                vf.write('''# UTF-8
VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=%s,
        prodvers=%s,
        mask=0x3f,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0)
    ),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    '040904B0',
                    [
                        StringStruct('CompanyName', ''),
                        StringStruct('FileDescription', 'AmicoScript'),
                        StringStruct('FileVersion', '%s'),
                        StringStruct('InternalName', 'AmicoScript'),
                        StringStruct('LegalCopyright', ''),
                        StringStruct('OriginalFilename', 'AmicoScript.exe'),
                        StringStruct('ProductName', 'AmicoScript'),
                        StringStruct('ProductVersion', '%s')
                    ]
                )
            ]
        ),
        VarFileInfo([VarStruct('Translation', [1033, 1200])])
    ]
)
''' % (str(filevers), str(filevers), ver_text, ver_text))
            args.append('--version-file=%s' % version_file_path)
        except Exception:
            pass


    print("Starting build with PyInstaller...")
    PyInstaller.__main__.run(args)
    
    print("\nDraft build complete!")
    if is_macos:
        app_path = os.path.join(dist, 'AmicoScript.app')
        print(f"Output available in: {app_path}")

        # Ensure executables inside the .app are executable (fixes Finder 'prohibitory' icon)
        contents_mac_os = os.path.join(app_path, 'Contents', 'MacOS')
        if os.path.isdir(contents_mac_os):
            for fname in os.listdir(contents_mac_os):
                fpath = os.path.join(contents_mac_os, fname)
                try:
                    # make file executable
                    os.chmod(fpath, os.stat(fpath).st_mode | 0o111)
                except Exception:
                    pass
        # also mark ffmpeg or other bundled binaries if placed in Contents/MacOS
        # (PyInstaller may put binaries in Resources or MacOS depending on spec)
        resources_dir = os.path.join(app_path, 'Contents', 'Resources')
        if os.path.isdir(resources_dir):
            for root_dir, dirs, files in os.walk(resources_dir):
                for fn in files:
                    if fn.lower().startswith('ffmpeg') or fn.endswith('.so') or fn.endswith('.dylib'):
                        fpath = os.path.join(root_dir, fn)
                        try:
                            os.chmod(fpath, os.stat(fpath).st_mode | 0o111)
                        except Exception:
                            pass
    else:
        print(f"Output available in: {dist}/AmicoScript")
    print("\nNote: You may need to manually bundle ffmpeg binaries in the dist folder if not in system path.")

if __name__ == "__main__":
    build()

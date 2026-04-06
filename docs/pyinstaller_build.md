PyInstaller build

This repo ships desktop-style bundles built with PyInstaller.

Important: PyInstaller can only bundle modules that exist in the build
environment. If you want the shipped app to support diarization, you must
install `torch` + `pyannote.audio` (and the other backend runtime deps) in the
build venv.

Recommended (shipping) build — includes diarization

1) Create and activate a build venv

```bash
python3 -m venv .venv-build
source .venv-build/bin/activate
```

2) Install runtime deps + PyInstaller

```bash
pip install --upgrade pip
pip install -r backend/requirements.txt
pip install pyinstaller
```

3) Build

```bash
python package.py
```

Notes
- The packaged app downloads `ffmpeg` on first run (into `~/.amicoscript/data/bin/` by default).
- Whisper + pyannote model weights download on first use (requires internet and a writable home directory; pyannote also requires an HF token).
- Releases are currently unsigned, so users may see Gatekeeper/SmartScreen warnings.

Minimal build venv (packaging-only)

If you intentionally want a packaging environment that only has PyInstaller
(for experimenting with smaller bundles), you can install:

```bash
python3 -m venv .venv-pyinstaller
source .venv-pyinstaller/bin/activate
pip install --upgrade pip
pip install -r requirements-pyinstaller.txt
```

But: that venv will NOT produce a functional diarization-capable release build.
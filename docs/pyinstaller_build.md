PyInstaller build

This repo ships desktop-style bundles built with PyInstaller.

Important: PyInstaller can only bundle modules that exist in the build
environment — with one deliberate exception. `torch`, `torchaudio` and
`pyannote.audio` must **not** be installed in the build venv: they are
downloaded by the app on first use instead of being bundled, and a bundled copy
would win over the downloaded one. See [runtime-pack.md](runtime-pack.md).

There is one build. The `--gpu` flag is gone; the CPU/CUDA choice is made on the
user's machine, at first use, from what its driver reports.

Recommended (shipping) build

1) Create and activate a build venv

```bash
python3 -m venv .venv-build
source .venv-build/bin/activate
```

2) Install runtime deps + PyInstaller

```bash
pip install --upgrade pip
pip install -r backend/requirements.txt
pip install -r requirements-pyinstaller.txt
```

3) Record what the app should download for diarization

```bash
python scripts/generate_runtime_manifest.py
```

This resolves the PyTorch wheels — URLs and hashes, one set per flavour — into
`runtime_manifest.json` without downloading them. It must run before the build,
and it must run in this venv: it resolves against the versions installed here so
the downloaded half cannot disagree with the bundled half about a shared
dependency.

4) Build

```bash
python package.py
```

Notes
- The packaged app downloads `ffmpeg` on first run (into `~/.amicoscript/data/bin/` by default).
- Whisper + pyannote model weights download on first use (requires internet and a writable home directory; pyannote also requires an HF token).
- The PyTorch runtime downloads on the first job that needs it — the first diarization, or the first job on an NVIDIA machine. Skipping step 3 produces a build that transcribes but cannot diarize; `package.py` warns when it does.
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

But: that venv will NOT produce a working release build — it has no FastAPI, no
faster-whisper, and `generate_runtime_manifest.py` has nothing to resolve
against. Diarization is no longer what distinguishes it, since that is
downloaded either way.
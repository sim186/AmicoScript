# AmicoScript

[![Build and Release](https://github.com/sim186/amico-script/actions/workflows/release.yml/badge.svg)](https://github.com/sim186/amico-script/actions/workflows/release.yml)

Local-first audio transcription with optional speaker identification. Upload a recording and get a time-stamped, searchable transcript — all processing happens locally.

- Supports: MP3, WAV, M4A, OGG, FLAC
- Models: Whisper (tiny → large-v3)
- Export: JSON, SRT, TXT, Markdown

Screenshots

![AmicoScript UI](docs/images/amicoscript.png)

![AmicoScript diarization](docs/images/amicoscript-diarization.png)

![AmicoScript library](docs/images/amicoscript-library.png)

Quick start — Docker (recommended)

```bash
docker compose up --build
```

Open http://localhost:8002 in your browser.

Quick start — local

```bash
# Python 3.10+
pip install -r backend/requirements.txt
python run.py
```

`run.py` downloads a platform-specific `ffmpeg` on first run. The frontend is served by FastAPI from `frontend/index.html`.

Using a virtual environment (macOS / Linux)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
python run.py
```

Standalone executable (macOS / Windows)

Build with PyInstaller (from repo root):

```bash
cd backend
pip install -r requirements.txt
pip install pyinstaller
cd ..
python package.py
```

Speaker diarization (optional)

Speaker diarization uses `pyannote` and requires accepting gated model licenses on Hugging Face and supplying an `hf_` token. See `docs/doc.md` for full steps.

API reference (selected)

- `GET /api/models` — list available Whisper models
- `GET /api/settings` — read saved settings (HF token)
- `POST /api/settings` — save settings
- `POST /api/transcribe` — upload file, start job → `{job_id}`
- `GET /api/jobs/{id}/stream` — SSE progress stream
- `POST /api/jobs/{id}/cancel` — cancel job
- `GET /api/jobs/{id}/result` — full JSON result
- `GET /api/jobs/{id}/export/{fmt}` — download transcript (`json`, `srt`, `txt`, `md`)

Architecture (brief)

- Backend: Python + FastAPI; transcription runs in background threads for blocking model code.
- Frontend: single `index.html` served by FastAPI; no build step required.
- Storage: in-memory job state; temporary audio files cleaned up after 1 hour.

GPU acceleration

Change the `Dockerfile` base image to a CUDA-enabled `pytorch` image and enable GPU in compose for GPU support.


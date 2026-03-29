# AmicoScript

Local-first audio transcription with speaker identification. Upload a recording, get a time-stamped, searchable transcript — all processed on your machine, nothing sent to the cloud.

**Supports:** MP3, WAV, M4A, OGG, FLAC
**Models:** Whisper tiny → large-v3
**Export:** JSON, SRT, TXT, Markdown

![AmicoScript UI](amicoscript.png)

---

## Quick start — Docker (recommended)

```bash
docker compose up --build
```

Open [http://localhost:8002](http://localhost:8002).

On first use, the selected Whisper model is downloaded automatically and cached in a Docker volume — subsequent runs are instant.

> **Frontend hot-reload:** The `frontend/` directory is mounted read-only into the container. Edit `index.html` and refresh the browser — no rebuild needed.

---

## Quick start — local

```bash
# Python 3.10+
cd backend
pip install -r requirements.txt
uvicorn main:app --reload
```

The app runs at `http://localhost:8002`. The frontend is served directly by FastAPI from `frontend/index.html`.

---

## Standalone Executable (macOS / Windows)

You can build a standalone executable that doesn't require Docker or a Python environment using PyInstaller.

```bash
# Make sure you have the backend requirements and PyInstaller installed
cd backend
pip install -r requirements.txt
pip install pyinstaller
cd ..

# Run the packaging script
python package.py
```

This will create a `dist/AmicoScript` directory containing the compiled application. On macOS, it will also generate an `.app` bundle.

> **Note:** The standalone executable bundles the Python environment and its dependencies, but you may still need to ensure system libraries (like `ffmpeg`) are available on the target machine, or bundle them manually.

---

## Speaker diarization (optional)

Speaker identification requires a free HuggingFace account:

1. Create an account at [huggingface.co](https://huggingface.co)
2. Accept the model license at [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1)
3. Generate a token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
4. Enable diarization in the AmicoScript UI and paste your token

The token is saved in your browser's `localStorage` — it never leaves your machine.

---

## API reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/models` | Available Whisper models |
| `POST` | `/api/transcribe` | Upload file, start job → `{job_id}` |
| `GET` | `/api/jobs/{id}/stream` | SSE progress stream |
| `POST` | `/api/jobs/{id}/cancel` | Cancel running job |
| `GET` | `/api/audio/{id}` | Raw audio (for in-browser player) |
| `GET` | `/api/jobs/{id}/result` | Full JSON result |
| `GET` | `/api/jobs/{id}/export/{fmt}` | Download transcript (`json`, `srt`, `txt`, `md`) |

---

## Architecture

- **Backend:** Python + FastAPI. Each transcription runs in a background thread (not async) because `faster-whisper` and `pyannote` are blocking C/torch operations. Progress events flow via a per-job `asyncio.Queue` to the SSE endpoint.
- **Frontend:** Single `index.html` — Tailwind CSS (CDN), vanilla JS, no build step.
- **Storage:** No database. All job state lives in-memory; audio files are cleaned up after 1 hour.
- **Performance:** `int8` quantization on CPU gives 2-4× speedup over `float32` with minimal accuracy loss.

---

## GPU acceleration

The Docker image uses CPU by default. For GPU, switch the base image in `Dockerfile`:

```dockerfile
FROM pytorch/pytorch:2.3.0-cuda12.1-cudnn8-runtime
```

And ensure [nvidia-container-toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) is installed, then add to `docker-compose.yml`:

```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: 1
          capabilities: [gpu]
```

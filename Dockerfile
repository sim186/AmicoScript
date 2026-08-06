FROM python:3.11-slim

WORKDIR /app

# ffmpeg: required by faster-whisper to decode mp3/m4a/ogg/flac
# libsndfile1: required by pyannote.audio
# libavcodec-dev, libavformat-dev, libavutil-dev: shared libs for torchcodec
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    libavcodec-dev \
    libavformat-dev \
    libavutil-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /tmp/requirements.txt
COPY backend/requirements-diarization.txt /tmp/requirements-diarization.txt

# Install CPU-only PyTorch from the official CPU index
# to avoid pulling CUDA-linked wheels (libnppicc, libnvrtc etc.)
# torchcodec arrives as a pyannote dependency but its C extension is mocked at
# runtime in main.py (no CPU aarch64 wheel exists).
RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# The diarization stack, installed here rather than downloaded on first use as
# the packaged app does: a container should carry everything it needs, and
# anything written to the cache directory is gone on the next `docker run`.
RUN pip install --no-cache-dir -r /tmp/requirements-diarization.txt

# Preserve backend/ as a subdirectory so it is importable as the 'backend'
# package (matching the venv layout where run.py adds backend/ to sys.path).
COPY backend/ backend/
COPY frontend/ frontend/
COPY scripts/ scripts/
COPY VERSION /app/VERSION

# Temp staging area for uploaded files before they are moved to STORAGE_ROOT.
RUN mkdir -p backend/uploads

# Make every module inside backend/ importable by its short name (state, pipeline,
# etc.) exactly as run.py does by inserting backend/ into sys.path.
# /app is WORKDIR so 'from backend import X' also resolves via namespace package.
ENV PYTHONPATH=/app/backend

EXPOSE 8002

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]

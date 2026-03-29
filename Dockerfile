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

COPY backend/requirements.txt .

# Install CPU-only PyTorch from the official CPU index
# to avoid pulling CUDA-linked wheels (libnppicc, libnvrtc etc.)
# torchcodec is installed from PyPI via requirements.txt but its C extension
# is mocked at runtime in main.py (no CPU aarch64 wheel exists).
RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
COPY frontend/ ./frontend/

RUN mkdir -p uploads

EXPOSE 8002

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]

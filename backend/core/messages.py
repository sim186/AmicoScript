"""Shared worker/user-facing status messages."""

TRANSCRIPTION_LOADING_MODEL = "Loading model '{model}'..."
TRANSCRIPTION_STARTING = (
    "Starting transcription (first progress update may take time on long files/CPU)..."
)
TRANSCRIPTION_WAITING_FIRST_SEGMENT = "Still transcribing... waiting for first segment ({seconds}s)"
TRANSCRIPTION_TIMEOUT_FIRST_SEGMENT = (
    "Transcription timed out before first segment. Try a smaller model or split the audio."
)
TRANSCRIPTION_GPU_FALLBACK = "GPU runtime unavailable. Retrying on CPU..."
TRANSCRIPTION_CANCELLED = "Cancelled."
TRANSCRIPTION_COMPLETE = "Transcription complete."

COLAB_UPLOADING = "Uploading file to Google Colab..."

"""AmicoScript — managed file storage helpers.

Files uploaded by users are initially written to UPLOAD_DIR (a staging area).
ingest_file() moves them to their permanent location under RECORDINGS_DIR so
they survive the 1-hour cleanup TTL that applies to transient job files.

Permanent layout:
    RECORDINGS_DIR/
        {recording_id}/
            original.{ext}
"""
import shutil
from pathlib import Path

from config import RECORDINGS_DIR


def ingest_file(temp_path: Path, recording_id: str) -> Path:
    """Move *temp_path* to the permanent managed storage directory.

    Creates the per-recording subdirectory as needed.  Returns the new path.
    The original extension is preserved.
    """
    dest_dir = RECORDINGS_DIR / recording_id
    dest_dir.mkdir(parents=True, exist_ok=True)

    ext = temp_path.suffix.lower()
    dest = dest_dir / f"original{ext}"

    # shutil.move handles cross-device moves (copy + delete) transparently.
    shutil.move(str(temp_path), str(dest))
    return dest


def get_recording_audio_path(recording_id: str, file_path: str) -> Path:
    """Return the audio path for a recording, falling back to file_path from DB."""
    # Try the canonical managed storage location first.
    base = RECORDINGS_DIR / recording_id
    if base.exists():
        for candidate in base.iterdir():
            if candidate.stem == "original":
                return candidate
    # Fall back to the stored absolute path (covers legacy or portable moves).
    return Path(file_path)

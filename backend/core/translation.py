"""Transcript translation job helpers."""
import os
import shutil
import subprocess
import time
from pathlib import Path

import state
from core.job_helpers import _append_job_log, _handle_job_error, _push_event
from db import new_session
from models import Recording, Transcript
from sqlmodel import select


def _translate_audio_chunk(
    audio_path: str,
    start: float,
    end: float,
    model_name: str,
    job_id: str = "internal",
) -> str:
    """Extract a time range and translate it to English via Whisper."""
    from core.transcription import _get_whisper_model

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("ffmpeg not found; cannot perform audio translation")

    chunk_path = str(Path(audio_path).with_name(f"chunk_{int(time.time())}_{round(start, 2)}.wav"))
    duration = end - start
    cmd = [
        ffmpeg_bin,
        "-y",
        "-v",
        "error",
        "-ss",
        str(start),
        "-t",
        str(duration),
        "-i",
        audio_path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-sample_fmt",
        "s16",
        chunk_path,
    ]

    try:
        subprocess.run(cmd, check=True, timeout=30)
        model, _ = _get_whisper_model(model_name)
        segments, _ = model.transcribe(chunk_path, task="translate")
        return " ".join(s.text.strip() for s in segments).strip()
    except (subprocess.SubprocessError, OSError, RuntimeError, ValueError) as exc:
        return f"Translation error: {exc}"
    finally:
        if os.path.exists(chunk_path):
            os.remove(chunk_path)


def _process_translation_job(job_id: str) -> None:
    """Translate all transcript segments for a recording."""
    import json as _json

    job = state.jobs[job_id]
    recording_id = job["recording_id"]
    model_name = job["options"].get("model", "small")

    try:
        _append_job_log(job_id, "INFO", f"Translation worker started for recording {recording_id}")
        _push_event(job_id, "loading_model", 0.05, f"Loading model '{model_name}'...")

        with new_session() as session:
            rec = session.get(Recording, recording_id)
            tr = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
            if not rec or not tr:
                raise ValueError("Recording or Transcript not found")

            data = _json.loads(tr.json_data)
            segments = data.get("segments", [])
            total = len(segments)
            if total == 0:
                _push_event(job_id, "done", 1.0, "No segments to translate.")
                return

            _push_event(job_id, "translating", 0.1, f"Found {total} segments. Starting bulk translation...")

            from core.transcription import _get_whisper_model
            _get_whisper_model(model_name)

            translated_count = 0
            for idx, seg in enumerate(segments):
                if job["cancel_flag"].is_set():
                    _push_event(job_id, "cancelled", 0.0, "Translation cancelled by user.")
                    _append_job_log(job_id, "INFO", "Translation job cancelled")
                    return

                if not seg.get("edited") and not seg.get("translation"):
                    seg["translation"] = _translate_audio_chunk(
                        rec.file_path,
                        seg["start"],
                        seg["end"],
                        model_name,
                        job_id=job_id,
                    )
                    translated_count += 1

                prog = 0.1 + 0.8 * ((idx + 1) / total)
                _push_event(job_id, "translating", prog, f"Translated {idx + 1}/{total} segments...")

            tr.json_data = _json.dumps(data)
            tr.updated_at = time.time()
            session.add(tr)
            session.commit()

            _push_event(
                job_id,
                "done",
                1.0,
                f"Translation complete. {translated_count} new translations added.",
            )
            _append_job_log(job_id, "INFO", "Translation job finished successfully")
    except Exception as exc:
        _handle_job_error(job_id, exc)

"""Colab proxy job processing."""
import json
from pathlib import Path

import requests

import state
from core.job_helpers import append_job_log, handle_job_error, push_event, sync_job_to_db
from core.messages import COLAB_UPLOADING, TRANSCRIPTION_CANCELLED, TRANSCRIPTION_COMPLETE


COLAB_SAFE_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".mp4", ".mov", ".mkv", ".opus"}


def _colab_upload_filename(job: dict) -> str:
    """Build a filename acceptable by the remote Colab endpoint validation."""
    original = str(job.get("original_filename") or "").strip()
    original_ext = Path(original).suffix.lower()
    if original and original_ext in COLAB_SAFE_EXTENSIONS:
        return original

    file_ext = Path(str(job.get("file_path") or "")).suffix.lower()
    if file_ext in COLAB_SAFE_EXTENSIONS:
        return f"source{file_ext}"

    # Some providers produce .webm or other extensions rejected by strict remote validators.
    # Use an allowed container extension; ffmpeg can still probe the real media stream.
    return "source.mp4"


def _raise_with_response_detail(exc: requests.HTTPError) -> None:
    """Raise RuntimeError with upstream response detail when available."""
    response = exc.response
    if response is None:
        raise exc

    detail = ""
    try:
        payload = response.json()
        detail = payload.get("detail") if isinstance(payload, dict) else ""
    except ValueError:
        detail = response.text.strip()

    if detail:
        raise RuntimeError(f"Colab API {response.status_code}: {detail}") from exc
    raise RuntimeError(f"Colab API {response.status_code}: {response.reason}") from exc


def handle_colab_job(job_id: str) -> None:
    """Forward a job to a remote Colab engine and proxy its SSE progress."""
    job = state.jobs[job_id]
    opts = job["options"]
    file_path = job["file_path"]
    colab_url = (opts.get("colab_url") or "").rstrip("/")

    append_job_log(job_id, "INFO", f"Forwarding job to Colab engine at {colab_url}")
    push_event(job_id, "transcribing", 0.05, COLAB_UPLOADING)

    try:
        with open(file_path, "rb") as fh:
            files = {"file": (_colab_upload_filename(job), fh)}
            data = {
                "model": opts.get("model", "small"),
                "language": opts.get("language", ""),
                "diarize": "true" if opts.get("diarize") else "false",
                "hf_token": opts.get("hf_token", ""),
                "num_speakers": opts.get("num_speakers", "") or "",
                "min_speakers": opts.get("min_speakers", "") or "",
                "max_speakers": opts.get("max_speakers", "") or "",
                "compute_type": opts.get("compute_type", "int8"),
                "device": opts.get("device", "auto"),
                "device_index": str(opts.get("device_index", 0)),
                "vad_filter": "true" if opts.get("vad_filter", True) else "false",
                "word_timestamps": "true" if opts.get("word_timestamps") else "false",
                "beam_size": str(opts.get("beam_size", 5)),
                "best_of": str(opts.get("best_of", 5)),
                "force_normalize_audio": "true" if opts.get("force_normalize_audio") else "false",
            }
            resp = requests.post(
                f"{colab_url}/api/transcribe",
                files=files,
                data=data,
                timeout=(30, 3600),
            )
            try:
                resp.raise_for_status()
            except requests.HTTPError as exc:
                _raise_with_response_detail(exc)
            colab_job_id = resp.json()["job_id"]

        append_job_log(job_id, "INFO", f"Colab job created: {colab_job_id}")

        with requests.get(
            f"{colab_url}/api/jobs/{colab_job_id}/stream",
            stream=True,
            timeout=(30, 86400),
        ) as sse_resp:
            sse_resp.raise_for_status()
            for line in sse_resp.iter_lines():
                if job["cancel_flag"].is_set():
                    try:
                        requests.post(f"{colab_url}/api/jobs/{colab_job_id}/cancel", timeout=10)
                    except requests.RequestException:
                        append_job_log(job_id, "WARN", "Failed to cancel remote Colab job")
                    push_event(job_id, "cancelled", 0.0, TRANSCRIPTION_CANCELLED)
                    sync_job_to_db(job_id)
                    return

                if not line:
                    continue

                decoded = line.decode("utf-8")
                if not decoded.startswith("data: "):
                    continue

                event_data = json.loads(decoded[6:])
                if "heartbeat" in event_data:
                    continue

                st = event_data.get("status")
                pr = event_data.get("progress", 0.0)
                msg = event_data.get("message", "")

                if st == "done":
                    res_resp = requests.get(
                        f"{colab_url}/api/jobs/{colab_job_id}/result",
                        timeout=(10, 60),
                    )
                    res_resp.raise_for_status()
                    job["result"] = res_resp.json()
                    push_event(job_id, "done", 1.0, TRANSCRIPTION_COMPLETE, data=job["result"])
                    sync_job_to_db(job_id)
                    return

                if st in ("error", "cancelled"):
                    job["error"] = msg
                    push_event(job_id, st, pr, msg)
                    sync_job_to_db(job_id)
                    return

                push_event(job_id, st, pr, msg, data=event_data.get("data"))

    except (requests.RequestException, ValueError, KeyError, OSError, RuntimeError) as exc:
        append_job_log(job_id, "ERROR", f"Colab proxy failed: {exc}")
        handle_job_error(job_id, exc)

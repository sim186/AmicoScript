"""Colab proxy job processing."""
import json

import requests

import state
from core.job_helpers import _append_job_log, _handle_job_error, _push_event, _sync_job_to_db
from core.messages import COLAB_UPLOADING, TRANSCRIPTION_CANCELLED, TRANSCRIPTION_COMPLETE


def _handle_colab_job(job_id: str) -> None:
    """Forward a job to a remote Colab engine and proxy its SSE progress."""
    job = state.jobs[job_id]
    opts = job["options"]
    file_path = job["file_path"]
    colab_url = (opts.get("colab_url") or "").rstrip("/")

    _append_job_log(job_id, "INFO", f"Forwarding job to Colab engine at {colab_url}")
    _push_event(job_id, "transcribing", 0.05, COLAB_UPLOADING)

    try:
        with open(file_path, "rb") as fh:
            files = {"file": (job.get("original_filename", "audio.wav"), fh)}
            data = {
                "model": opts.get("model", "small"),
                "language": opts.get("language", ""),
                "diarize": "true" if opts.get("diarize") else "false",
                "hf_token": opts.get("hf_token", ""),
                "num_speakers": opts.get("num_speakers", "") or "",
                "min_speakers": opts.get("min_speakers", "") or "",
                "max_speakers": opts.get("max_speakers", "") or "",
            }
            resp = requests.post(
                f"{colab_url}/api/transcribe",
                files=files,
                data=data,
                timeout=(30, 3600),
            )
            resp.raise_for_status()
            colab_job_id = resp.json()["job_id"]

        _append_job_log(job_id, "INFO", f"Colab job created: {colab_job_id}")

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
                        _append_job_log(job_id, "WARN", "Failed to cancel remote Colab job")
                    _push_event(job_id, "cancelled", 0.0, TRANSCRIPTION_CANCELLED)
                    _sync_job_to_db(job_id)
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
                    _push_event(job_id, "done", 1.0, TRANSCRIPTION_COMPLETE, data=job["result"])
                    _sync_job_to_db(job_id)
                    return

                if st in ("error", "cancelled"):
                    job["error"] = msg
                    _push_event(job_id, st, pr, msg)
                    _sync_job_to_db(job_id)
                    return

                _push_event(job_id, st, pr, msg, data=event_data.get("data"))

    except (requests.RequestException, ValueError, KeyError, OSError) as exc:
        _append_job_log(job_id, "ERROR", f"Colab proxy failed: {exc}")
        _handle_job_error(job_id, exc)

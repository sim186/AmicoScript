"""Async HTTP client wrapping the AmicoScript backend.

Methods mirror the REST endpoints in backend/main.py. All return raw
dicts/lists decoded from JSON. Errors raise httpx.HTTPStatusError.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

import httpx


# Posted back for a secret the user did not edit. The server keeps whatever it
# already has rather than overwriting it with the placeholder shown on screen.
UNCHANGED = "__unchanged__"


def _auth_headers() -> dict[str, str]:
    """Bearer token for backends running with AMICOSCRIPT_AUTH=always.

    Loopback access needs no credentials in the default 'auto' mode, so this is
    empty unless the user opted into authenticating local clients too.
    """
    token = os.environ.get("AMICOSCRIPT_API_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


# Generous timeout — uploads of large audio files can take minutes; the
# server may also take time to load whisper models on first request.
DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=5.0, read=600.0)


class ApiClient:
    """Thin async wrapper around the backend REST API."""

    def __init__(self, base_url: str, timeout: httpx.Timeout | None = None):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout or DEFAULT_TIMEOUT,
            headers=_auth_headers(),
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    # --- generic helpers --------------------------------------------

    async def _get(self, path: str, **params: Any) -> Any:
        r = await self.client.get(path, params=_drop_none(params))
        r.raise_for_status()
        return r.json()

    async def _post(self, path: str, json: Any = None) -> Any:
        r = await self.client.post(path, json=json)
        r.raise_for_status()
        return r.json() if r.content else {}

    async def _post_form(self, path: str, data: Any = None) -> Any:
        r = await self.client.post(path, data=data)
        r.raise_for_status()
        return r.json() if r.content else {}

    async def _patch_form(self, path: str, data: Any = None) -> Any:
        r = await self.client.patch(path, data=data)
        r.raise_for_status()
        return r.json() if r.content else {}

    async def _delete(self, path: str, **params: Any) -> Any:
        r = await self.client.delete(path, params=_drop_none(params))
        r.raise_for_status()
        return r.json() if r.content else {}

    # --- version / health -------------------------------------------

    async def version(self) -> dict:
        return await self._get("/api/version")

    async def models(self) -> dict:
        return await self._get("/api/models")

    async def whisper_models(self) -> dict:
        return await self._get("/api/whisper/models")

    async def save_whisper_model(self, model: str) -> dict:
        return await self._post("/api/whisper/models", json={"model": model})

    async def latest_release(self) -> dict:
        return await self._get("/api/latest-release")

    # --- library / recordings ---------------------------------------

    async def library(
        self,
        folder_id: str | None = None,
        tag_id: str | None = None,
        status: str | None = None,
        sort: str = "created_at",
        order: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        return await self._get(
            "/api/library",
            folder_id=folder_id,
            tag_id=tag_id,
            status=status,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
        )

    async def recording(self, recording_id: str) -> dict:
        return await self._get(f"/api/recordings/{recording_id}")

    async def transcript(self, recording_id: str) -> dict:
        return await self._get(f"/api/recordings/{recording_id}/transcript")

    async def edit_segment(self, recording_id: str, segment_index: int, text: str) -> dict:
        return await self._patch_form(
            f"/api/recordings/{recording_id}/transcript/segments/{segment_index}",
            data={"text": text},
        )

    async def reset_segment(self, recording_id: str, segment_index: int) -> dict:
        return await self._post_form(
            f"/api/recordings/{recording_id}/transcript/segments/{segment_index}/reset"
        )

    async def rename_speaker(self, recording_id: str, old_name: str, new_name: str) -> dict:
        return await self._post_form(
            f"/api/recordings/{recording_id}/transcript/rename-speaker",
            data={"old_name": old_name, "new_name": new_name},
        )

    async def assign_speaker(self, recording_id: str, segment_indices: list[int], speaker_name: str) -> dict:
        return await self._post_form(
            f"/api/recordings/{recording_id}/transcript/assign-speaker",
            data={
                "segment_indices": ",".join(str(i) for i in segment_indices),
                "speaker_name": speaker_name,
            },
        )

    async def update_recording(self, recording_id: str, **fields: Any) -> dict:
        return await self._patch_form(f"/api/recordings/{recording_id}", data=fields)

    async def delete_recording(self, recording_id: str) -> dict:
        return await self._delete(f"/api/recordings/{recording_id}")

    async def export(
        self, recording_id: str, fmt: str, wikilinks: bool = False
    ) -> tuple[bytes, str | None]:
        """Return (body, filename) for a transcript export."""
        r = await self.client.get(
            f"/api/recordings/{recording_id}/export/{fmt}",
            params={"wikilinks": "true"} if wikilinks else None,
        )
        r.raise_for_status()
        filename = _filename_from_disposition(
            r.headers.get("content-disposition")
        )
        return r.content, filename

    async def bulk_export_md(
        self, ids: list[str], wikilinks: bool = False
    ) -> tuple[bytes, str | None]:
        """Return (body, filename) for a combined markdown export of several recordings."""
        r = await self.client.post(
            "/api/recordings/bulk-export/md",
            json={"ids": ids, "wikilinks": wikilinks},
        )
        r.raise_for_status()
        filename = _filename_from_disposition(
            r.headers.get("content-disposition")
        )
        return r.content, filename

    # --- folders / tags / search ------------------------------------

    async def folders(self) -> list[dict]:
        return await self._get("/api/folders")

    async def create_folder(
        self, name: str, parent_id: int | None = None, color_code: str | None = None
    ) -> dict:
        return await self._post_form(
            "/api/folders",
            data=_drop_none(
                {"name": name, "parent_id": parent_id, "color_code": color_code}
            ),
        )

    async def update_folder(self, folder_id: int, **fields: Any) -> dict:
        return await self._patch_form(f"/api/folders/{folder_id}", data=fields)

    async def delete_folder(
        self, folder_id: int, delete_recordings: bool = False
    ) -> dict:
        return await self._delete(
            f"/api/folders/{folder_id}", delete_recordings=delete_recordings
        )

    async def tags(self, folder_id: int | None = None) -> list[dict]:
        return await self._get("/api/tags", folder_id=folder_id)

    async def create_tag(self, name: str, color_code: str | None = None) -> dict:
        return await self._post_form(
            "/api/tags", data=_drop_none({"name": name, "color_code": color_code})
        )

    async def update_tag(self, tag_id: int, **fields: Any) -> dict:
        return await self._patch_form(f"/api/tags/{tag_id}", data=fields)

    async def delete_tag(self, tag_id: int) -> dict:
        return await self._delete(f"/api/tags/{tag_id}")

    async def add_tag(self, recording_id: str, tag_id: int) -> dict:
        return await self._post(
            f"/api/recordings/{recording_id}/tags/{tag_id}"
        )

    async def remove_tag(self, recording_id: str, tag_id: int) -> dict:
        return await self._delete(
            f"/api/recordings/{recording_id}/tags/{tag_id}"
        )

    async def suggest_tags(self, recording_id: str) -> dict:
        """Ask the LLM for tags. Suggests only — nothing is applied."""
        return await self._post(f"/api/recordings/{recording_id}/suggest-tags")

    async def search(self, q: str, limit: int = 50, offset: int = 0) -> list:
        return await self._get("/api/search", q=q, limit=limit, offset=offset)

    # --- jobs --------------------------------------------------------

    async def jobs(self) -> dict:
        return await self._get("/api/jobs")

    async def job_result(self, job_id: str) -> dict:
        return await self._get(f"/api/jobs/{job_id}/result")

    async def job_logs(self, job_id: str, limit: int = 200) -> dict:
        return await self._get(f"/api/jobs/{job_id}/logs", limit=limit)

    async def cancel_job(self, job_id: str) -> dict:
        return await self._post(f"/api/jobs/{job_id}/cancel")

    # --- transcribe --------------------------------------------------

    async def transcribe_url(self, url: str, **options: Any) -> dict:
        payload = {"source_url": url, **_drop_none(options)}
        return await self._post_form("/api/transcribe/url", data=payload)

    async def transcribe_file(
        self,
        path: Path,
        options: dict[str, Any] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict:
        """Upload a file to /api/transcribe with optional progress callback.

        on_progress(bytes_sent, total_bytes) is invoked as the file streams.
        """
        path = Path(path)
        total = path.stat().st_size

        class _ProgressFile:
            def __init__(self, filepath, on_progress_cb, total_size):
                self._f = open(filepath, "rb")
                self._on_progress = on_progress_cb
                self._total = total_size
                self._sent = 0

            def read(self, size=-1):
                chunk = self._f.read(size)
                if self._on_progress is not None:
                    self._sent += len(chunk)
                    self._on_progress(self._sent, self._total)
                return chunk

            def seek(self, *args):
                return self._f.seek(*args)

            def close(self):
                self._f.close()

        progress_file = _ProgressFile(path, on_progress, total)
        files = {"file": (path.name, progress_file, "application/octet-stream")}
        data = {k: str(v) for k, v in (options or {}).items() if v is not None}
        r = await self.client.post("/api/transcribe", data=data, files=files)
        r.raise_for_status()
        return r.json()

    # --- analyses / llm ---------------------------------------------

    async def analyses(self, recording_id: str) -> list[dict]:
        return await self._get(f"/api/recordings/{recording_id}/analyses")

    async def create_analysis(
        self, recording_id: str, analysis_type: str, **opts: Any
    ) -> dict:
        return await self._post_form(
            f"/api/recordings/{recording_id}/analyses",
            data={"analysis_type": analysis_type, **_drop_none(opts)},
        )

    async def llm_settings(self) -> dict:
        """LLM config. The server reports whether a key is stored, never its value."""
        raw = await self._get("/api/llm/settings")
        return {
            "provider": raw.get("llm_provider", "ollama"),
            "base_url": raw.get("llm_base_url", ""),
            "model_name": raw.get("llm_model_name", ""),
            "api_key_set": bool(raw.get("llm_api_key_set")),
            "api_key_requirement": raw.get("api_key_requirement", "optional"),
            "provider_is_cloud": bool(raw.get("provider_is_cloud")),
            "allow_cloud": bool(raw.get("llm_allow_cloud")),
            "context_tokens": raw.get("llm_context_tokens"),
            "max_output_tokens": raw.get("llm_max_output_tokens"),
        }

    async def save_llm_settings(
        self,
        base_url: str | None = None,
        model_name: str | None = None,
        api_key: str | None = None,
        provider: str | None = None,
        context_tokens: int | None = None,
        allow_cloud: bool | None = None,
    ) -> dict:
        """Persist LLM config.

        Pass ``api_key=UNCHANGED`` (the default of the settings form) to leave a
        stored key alone — the TUI never receives it, so sending back what is on
        screen would erase it.
        """
        return await self._post_form(
            "/api/llm/settings",
            data=_drop_none({
                "llm_provider": provider,
                "llm_base_url": base_url,
                "llm_model_name": model_name,
                "llm_api_key": api_key,
                "llm_context_tokens": str(context_tokens) if context_tokens else None,
                "llm_allow_cloud": None if allow_cloud is None else str(allow_cloud).lower(),
            }),
        )

    async def llm_providers(self) -> dict:
        """Preset catalog plus whether the server is running in a container."""
        return await self._get("/api/llm/providers")

    async def llm_detect(self) -> dict:
        """Scan the well-known ports for a local LLM server."""
        return await self._get("/api/llm/detect")

    async def llm_test_connection(self) -> dict:
        return await self._post("/api/llm/test-connection")

    async def llm_models(self, base_url: str | None = None) -> list:
        return await self._get("/api/llm/models", base_url=base_url)

    async def llm_pull_model(self, name: str) -> dict:
        return await self._post("/api/llm/models/pull", json={"model_name": name})

    # --- settings ---------------------------------------------------

    async def settings(self) -> dict:
        return await self._get("/api/settings")

    async def save_settings(
        self,
        hf_token: str | None = None,
        whisper_model: str | None = None,
        whisper_device: str | None = None,
        whisper_compute: str | None = None,
        auto_summarize_meetings: bool | None = None,
    ) -> dict:
        """Persist settings.

        ``hf_token`` defaults to None (not sent). Pass UNCHANGED explicitly for a
        field the user did not edit: the server masks the stored token, so
        echoing what the form shows would wipe it.
        """
        return await self._post_form(
            "/api/settings",
            data=_drop_none({
                "hf_token": hf_token,
                "whisper_model": whisper_model,
                "whisper_device": whisper_device,
                "whisper_compute": whisper_compute,
                "auto_summarize_meetings": (
                    None if auto_summarize_meetings is None
                    else str(auto_summarize_meetings).lower()
                ),
            }),
        )

    # --- recordings: retry ------------------------------------------

    async def retry_recording(self, recording_id: str) -> dict:
        """Queue an existing recording for transcription again."""
        return await self._post(f"/api/recordings/{recording_id}/retry")

    # --- library backup ---------------------------------------------

    async def export_library(self, destination: Path, include_audio: bool = True) -> Path:
        """Stream the library bundle to *destination*."""
        params = {"include_audio": str(include_audio).lower()}
        async with self.client.stream("GET", "/api/library/export", params=params) as r:
            r.raise_for_status()
            with open(destination, "wb") as fh:
                async for chunk in r.aiter_bytes():
                    fh.write(chunk)
        return destination

    async def import_library(self, source: Path, mode: str = "skip") -> dict:
        with open(source, "rb") as fh:
            r = await self.client.post(
                "/api/library/import",
                files={"file": (source.name, fh, "application/zip")},
                data={"mode": mode},
            )
        r.raise_for_status()
        return r.json()

    # --- meeting watcher ----------------------------------------------

    async def watcher_status(self) -> dict:
        return await self._get("/api/watcher/status")


# --- helpers --------------------------------------------------------


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def _filename_from_disposition(header: str | None) -> str | None:
    if not header:
        return None
    for part in header.split(";"):
        part = part.strip()
        if part.lower().startswith("filename="):
            return part.split("=", 1)[1].strip().strip('"')
    return None

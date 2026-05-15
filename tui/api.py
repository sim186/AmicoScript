"""Async HTTP client wrapping the AmicoScript backend.

Methods mirror the REST endpoints in backend/main.py. All return raw
dicts/lists decoded from JSON. Errors raise httpx.HTTPStatusError.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable

import httpx


# Generous timeout — uploads of large audio files can take minutes; the
# server may also take time to load whisper models on first request.
DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=5.0, read=600.0)


class ApiClient:
    """Thin async wrapper around the backend REST API."""

    def __init__(self, base_url: str, timeout: httpx.Timeout | None = None):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(
            base_url=self.base_url, timeout=timeout or DEFAULT_TIMEOUT
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

    async def _patch(self, path: str, json: Any = None) -> Any:
        r = await self.client.patch(path, json=json)
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

    async def update_recording(self, recording_id: str, **fields: Any) -> dict:
        return await self._patch(f"/api/recordings/{recording_id}", json=fields)

    async def delete_recording(self, recording_id: str) -> dict:
        return await self._delete(f"/api/recordings/{recording_id}")

    async def export(
        self, recording_id: str, fmt: str
    ) -> tuple[bytes, str | None]:
        """Return (body, filename) for a transcript export."""
        r = await self.client.get(
            f"/api/recordings/{recording_id}/export/{fmt}"
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
        return await self._post(
            "/api/folders",
            json=_drop_none(
                {"name": name, "parent_id": parent_id, "color_code": color_code}
            ),
        )

    async def update_folder(self, folder_id: int, **fields: Any) -> dict:
        return await self._patch(f"/api/folders/{folder_id}", json=fields)

    async def delete_folder(
        self, folder_id: int, delete_recordings: bool = False
    ) -> dict:
        return await self._delete(
            f"/api/folders/{folder_id}", delete_recordings=delete_recordings
        )

    async def tags(self, folder_id: int | None = None) -> list[dict]:
        return await self._get("/api/tags", folder_id=folder_id)

    async def create_tag(self, name: str, color_code: str | None = None) -> dict:
        return await self._post(
            "/api/tags", json=_drop_none({"name": name, "color_code": color_code})
        )

    async def add_tag(self, recording_id: str, tag_id: int) -> dict:
        return await self._post(
            f"/api/recordings/{recording_id}/tags/{tag_id}"
        )

    async def remove_tag(self, recording_id: str, tag_id: int) -> dict:
        return await self._delete(
            f"/api/recordings/{recording_id}/tags/{tag_id}"
        )

    async def search(self, q: str, limit: int = 50, offset: int = 0) -> dict:
        return await self._get("/api/search", q=q, limit=limit, offset=offset)

    # --- jobs --------------------------------------------------------

    async def job_result(self, job_id: str) -> dict:
        return await self._get(f"/api/jobs/{job_id}/result")

    async def job_logs(self, job_id: str, limit: int = 200) -> dict:
        return await self._get(f"/api/jobs/{job_id}/logs", limit=limit)

    async def cancel_job(self, job_id: str) -> dict:
        return await self._post(f"/api/jobs/{job_id}/cancel")

    # --- transcribe --------------------------------------------------

    async def transcribe_url(self, url: str, **options: Any) -> dict:
        payload = {"url": url, **_drop_none(options)}
        return await self._post("/api/transcribe/url", json=payload)

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
        sent = 0

        def reader() -> Iterable[bytes]:
            nonlocal sent
            with path.open("rb") as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    sent += len(chunk)
                    if on_progress is not None:
                        on_progress(sent, total)
                    yield chunk

        files = {"file": (path.name, reader(), "application/octet-stream")}
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
        return await self._post(
            f"/api/recordings/{recording_id}/analyses",
            json={"analysis_type": analysis_type, **_drop_none(opts)},
        )

    async def llm_settings(self) -> dict:
        return await self._get("/api/llm/settings")

    async def save_llm_settings(self, **fields: Any) -> dict:
        return await self._post("/api/llm/settings", json=_drop_none(fields))

    async def llm_test_connection(self) -> dict:
        return await self._post("/api/llm/test-connection")

    async def llm_models(self) -> dict:
        return await self._get("/api/llm/models")

    async def llm_pull_model(self, name: str) -> dict:
        return await self._post("/api/llm/models/pull", json={"name": name})

    # --- settings ---------------------------------------------------

    async def settings(self) -> dict:
        return await self._get("/api/settings")

    async def save_settings(self, hf_token: str | None = None) -> dict:
        return await self._post(
            "/api/settings", json=_drop_none({"hf_token": hf_token})
        )


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

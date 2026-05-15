"""SSE streaming helper for job progress events."""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx
from httpx_sse import aconnect_sse


async def stream_job(
    client: httpx.AsyncClient, base_url: str, job_id: str
) -> AsyncIterator[dict]:
    """Yield decoded JSON event payloads from /api/jobs/{id}/stream.

    Each event from the backend has a JSON body. Non-JSON or empty lines
    are skipped. Caller is responsible for cancellation via task cancel.
    """
    url = f"{base_url}/api/jobs/{job_id}/stream"
    async with aconnect_sse(client, "GET", url) as source:
        async for event in source.aiter_sse():
            data = event.data
            if not data:
                continue
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                yield {"raw": data}

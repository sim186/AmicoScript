"""Turning transport errors into something worth showing in a status line.

httpx raises with a URL and a status code; what a user needs is which thing
failed and what to do about it. The auth cases matter most: a TUI pointed at a
password-protected AmicoScript otherwise reports a bare "401".
"""
from __future__ import annotations

import httpx


def explain(exc: Exception, prefix: str = "") -> str:
    """Describe *exc* in one line, using the server's message where there is one."""
    message = _describe(exc)
    return f"{prefix}: {message}" if prefix else message


def _describe(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        detail = _detail(exc.response)

        if status == 401:
            return (
                "the server requires authentication — set AMICOSCRIPT_API_TOKEN "
                "to the token from its Security settings"
            )
        if status == 503 and "auth" in (detail or "").lower():
            return detail or "the server is refusing remote connections until a password is set"
        if status == 410:
            return "that job has expired; its transcript is in the library"
        if status == 409:
            return detail or "conflicts with something that already exists"
        if status == 404:
            return detail or "not found"
        return detail or f"HTTP {status}"

    if isinstance(exc, httpx.ConnectError):
        return "cannot reach the AmicoScript server — is it running?"
    if isinstance(exc, httpx.ReadTimeout):
        return "the server did not answer in time"
    return str(exc)


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except Exception:
        return ""
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict) and detail.get("message"):
        return str(detail["message"])
    return ""

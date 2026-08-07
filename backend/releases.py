"""GitHub release utilities for the AmicoScript update checker."""
import asyncio
import json
import os
import re
import urllib.error as _urlerr
import urllib.request as _urlreq
from typing import Optional

#: A release is not urgent news. Four hours between checks is often enough to
#: surface an update banner and rare enough not to look like traffic.
POLL_INTERVAL_SECONDS = 60 * 60 * 4


def fetch_latest_release(owner: str, repo: str, token: Optional[str] = None) -> dict:
    """Fetch the latest GitHub release metadata for owner/repo."""
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    req = _urlreq.Request(url, headers={"Accept": "application/vnd.github.v3+json"})
    if token:
        req.add_header("Authorization", f"token {token}")
    try:
        with _urlreq.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except _urlerr.HTTPError as e:
        try:
            body = e.read().decode("utf-8")
            return {"error": f"HTTP {e.code}", "body": body}
        except Exception:
            return {"error": f"HTTP {e.code}"}
    except Exception as exc:
        return {"error": str(exc)}


def is_version_newer(local: str, remote_tag: str) -> bool:
    """Return True if remote_tag represents a version strictly newer than local."""
    def parse(v: str) -> tuple:
        s = re.sub(r"[^0-9.]", "", v or "").strip(".")
        return tuple(int(p) for p in s.split(".") if p.isdigit()) if s else ()

    return parse(remote_tag) > parse(local)


def local_version() -> str:
    """The version this build reports, or "" if it cannot say."""
    from api.routes.releases import get_version

    try:
        return get_version().get("version", "") or ""
    except Exception:
        return ""


def _record(app, info: dict) -> None:
    """Store one poll result on app.state for the /api/releases routes to read."""
    if not info or info.get("error"):
        app.state.latest_release = {"error": (info or {}).get("error", "unknown")}
        return

    tag = info.get("tag_name", "")
    app.state.latest_release = {
        "tag_name": tag,
        "html_url": info.get("html_url", ""),
        "name": info.get("name", ""),
        "body": info.get("body", ""),
    }
    local = local_version()
    app.state.local_version = local
    app.state.update_available = is_version_newer(local, tag)


async def poll_latest_release(app) -> None:
    """Refresh app.state.latest_release every few hours, for as long as we run.

    Returns immediately when no repository is configured, which is also how the
    test suite keeps startup off the network.
    """
    owner = os.environ.get("GITHUB_OWNER", "sim186")
    repo = os.environ.get("GITHUB_REPO", "AmicoScript")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not owner or not repo:
        return

    app.state.latest_release = {"tag_name": "", "html_url": "", "name": "", "body": ""}

    while True:
        try:
            _record(app, fetch_latest_release(owner, repo, token or None))
        except Exception:
            # Nothing here is worth interrupting the poll for; the banner just
            # keeps showing whatever it last knew.
            pass
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

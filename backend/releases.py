"""GitHub release utilities for the AmicoScript update checker."""
import json
import re
import urllib.error as _urlerr
import urllib.request as _urlreq
from typing import Optional


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

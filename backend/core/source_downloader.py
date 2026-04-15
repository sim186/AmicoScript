"""Source URL helpers and yt-dlp-based audio downloads."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse


PLATFORM_HOSTS: dict[str, set[str]] = {
    "youtube": {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
        "www.youtu.be",
    },
    "tiktok": {
        "tiktok.com",
        "www.tiktok.com",
        "m.tiktok.com",
        "vm.tiktok.com",
    },
    "instagram": {
        "instagram.com",
        "www.instagram.com",
    },
    "facebook": {
        "facebook.com",
        "www.facebook.com",
        "fb.watch",
    },
    "x": {
        "x.com",
        "www.x.com",
        "twitter.com",
        "www.twitter.com",
    },
    "vimeo": {
        "vimeo.com",
        "www.vimeo.com",
        "player.vimeo.com",
    },
    "twitch": {
        "twitch.tv",
        "www.twitch.tv",
        "clips.twitch.tv",
    },
}


@dataclass
class DownloadCandidate:
    url: str
    title: str
    platform: str = "web"


ProgressCallback = Callable[[str, float, str], None]

AUTH_RETRY_PLATFORMS = {"instagram", "tiktok", "facebook", "x"}
AUTH_ERROR_MARKERS = (
    "login required",
    "cookies",
    "rate-limit",
    "rate limit",
    "requested content is not available",
    "not available",
)


def _get_yt_dlp_class():
    try:
        from yt_dlp import YoutubeDL  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "yt-dlp is not available. Install backend dependencies again to enable online imports."
        ) from exc
    return YoutubeDL


def _should_auto_cookies() -> bool:
    value = (os.environ.get("AMICO_YTDLP_AUTO_COOKIES", "1") or "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _cookie_browsers() -> list[str]:
    raw = os.environ.get("AMICO_YTDLP_COOKIE_BROWSERS", "chrome,firefox,safari,edge")
    browsers = [b.strip().lower() for b in raw.split(",") if b.strip()]
    return browsers or ["chrome", "firefox", "safari", "edge"]


def _is_auth_or_rate_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in AUTH_ERROR_MARKERS)


def _raise_with_helpful_message(source_url: str, platform: str, exc: Exception) -> None:
    message = str(exc)
    if platform in AUTH_RETRY_PLATFORMS and _is_auth_or_rate_limit_error(exc):
        raise RuntimeError(
            (
                f"{platform.title()} access is currently restricted. "
                "Please log into that platform in your browser and retry. "
                "If this persists, set AMICO_YTDLP_COOKIE_BROWSERS to your browser list "
                "(e.g. chrome,firefox,safari). "
                f"Original error: {message}"
            )
        ) from exc
    raise RuntimeError(message) from exc


def _extract_info_with_retries(source_url: str, base_opts: dict, download: bool) -> dict:
    """Run yt-dlp extraction and retry with browser cookies when platform requires auth."""
    YoutubeDL = _get_yt_dlp_class()
    platform = detect_source_platform(source_url)

    attempts: list[str | None] = [None]
    if _should_auto_cookies() and platform in AUTH_RETRY_PLATFORMS:
        attempts.extend(_cookie_browsers())

    last_error: Exception | None = None
    for browser in attempts:
        opts = dict(base_opts)
        if browser:
            opts["cookiesfrombrowser"] = (browser,)
        try:
            with YoutubeDL(opts) as ydl:
                return ydl.extract_info(source_url, download=download)
        except Exception as exc:
            last_error = exc
            if browser:
                continue
            if len(attempts) > 1 and _is_auth_or_rate_limit_error(exc):
                continue
            _raise_with_helpful_message(source_url, platform, exc)

    if last_error is not None:
        _raise_with_helpful_message(source_url, platform, last_error)
    raise RuntimeError("Unable to fetch source URL")


def detect_source_platform(url: str) -> str:
    """Infer source platform label from URL host."""
    try:
        parsed = urlparse((url or "").strip())
    except Exception:
        return "web"
    host = (parsed.netloc or "").lower().split(":")[0]
    for platform, hosts in PLATFORM_HOSTS.items():
        if host in hosts:
            return platform
    return "web"


def is_supported_source_url(url: str) -> bool:
    """Return True for HTTP(S) URLs that yt-dlp can attempt to resolve."""
    try:
        parsed = urlparse((url or "").strip())
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _candidate_url(entry: dict, fallback_host: str = "") -> str:
    value = str(entry.get("webpage_url") or "").strip()
    if value:
        return value

    value = str(entry.get("url") or "").strip()
    if value.startswith("http://") or value.startswith("https://"):
        return value

    vid = str(entry.get("id") or "").strip()
    if vid and fallback_host:
        return f"{fallback_host}{vid}"
    return ""


def resolve_source_candidates(source_url: str, include_playlist: bool = True) -> list[DownloadCandidate]:
    """Resolve one or more download candidates from a source URL."""
    if not is_supported_source_url(source_url):
        raise RuntimeError("Unsupported source URL")

    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "noplaylist": not include_playlist,
    }
    info = _extract_info_with_retries(source_url, opts, download=False)

    if not info:
        return []

    entries = info.get("entries") if isinstance(info, dict) else None
    if entries:
        candidates: list[DownloadCandidate] = []
        fallback_host = "https://www.youtube.com/watch?v=" if detect_source_platform(source_url) == "youtube" else ""
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            item_url = _candidate_url(entry, fallback_host=fallback_host)
            if not item_url:
                continue
            title = str(entry.get("title") or "").strip() or "YouTube item"
            candidates.append(
                DownloadCandidate(
                    url=item_url,
                    title=title,
                    platform=detect_source_platform(item_url),
                )
            )
        return candidates

    item_url = _candidate_url(info if isinstance(info, dict) else {}) or source_url
    title = "Online audio"
    if isinstance(info, dict):
        title = str(info.get("title") or "").strip() or title
    return [DownloadCandidate(url=item_url, title=title, platform=detect_source_platform(item_url))]


def download_source_audio(source_url: str, out_dir: Path, on_progress: ProgressCallback | None = None) -> tuple[Path, str]:
    """Download audio for a supported source URL and return local path and title."""
    if not is_supported_source_url(source_url):
        raise RuntimeError("Unsupported source URL")

    out_dir.mkdir(parents=True, exist_ok=True)

    def _emit(status: str, progress: float, message: str) -> None:
        if on_progress:
            on_progress(status, progress, message)

    def _hook(event: dict) -> None:
        status = event.get("status")
        if status == "downloading":
            total = event.get("total_bytes") or event.get("total_bytes_estimate") or 0
            done = event.get("downloaded_bytes") or 0
            pct = (float(done) / float(total)) if total else 0.0
            _emit("downloading", min(max(pct, 0.0), 1.0), "Downloading audio from source...")
        elif status == "finished":
            _emit("postprocessing", 1.0, "Download complete. Preparing transcription...")

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "bestaudio/best",
        "outtmpl": str(out_dir / "%(title).80s-%(id)s.%(ext)s"),
        "restrictfilenames": True,
        "windowsfilenames": True,
        "progress_hooks": [_hook],
    }

    info = _extract_info_with_retries(source_url, opts, download=True)
    requested = (info or {}).get("requested_downloads") if isinstance(info, dict) else None
    title = str((info or {}).get("title") or "Online audio") if isinstance(info, dict) else "Online audio"

    if isinstance(requested, list) and requested:
        path_str = requested[0].get("filepath")
        if path_str:
            return Path(path_str), title

    fallback_path = str((info or {}).get("_filename") or "") if isinstance(info, dict) else ""
    if fallback_path:
        return Path(fallback_path), title

    raise RuntimeError("Downloaded media path not returned by source extractor")

import os

from core import source_downloader


class _FakeYDL:
    def __init__(self, opts, calls):
        self.opts = opts
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def extract_info(self, _url, download=False):
        self.calls.append((download, self.opts.get("cookiesfrombrowser")))
        if self.opts.get("cookiesfrombrowser"):
            return {"title": "ok", "requested_downloads": [{"filepath": "/tmp/a.mp4"}]}
        raise RuntimeError("Requested content is not available, rate-limit reached or login required")


def test_extract_retries_with_browser_cookies(monkeypatch):
    calls = []

    def _factory(opts):
        return _FakeYDL(opts, calls)

    monkeypatch.setattr(source_downloader, "_get_yt_dlp_class", lambda: _factory)
    monkeypatch.setenv("AMICO_YTDLP_AUTO_COOKIES", "1")
    monkeypatch.setenv("AMICO_YTDLP_COOKIE_BROWSERS", "chrome")

    info = source_downloader._extract_info_with_retries(
        "https://www.instagram.com/reel/abc/",
        {"quiet": True},
        download=True,
    )

    assert info["title"] == "ok"
    assert calls[0][1] is None
    assert calls[1][1] == ("chrome",)


def test_extract_raises_helpful_message_when_auth_still_needed(monkeypatch):
    class _AlwaysFailYDL:
        def __init__(self, _opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=False):
            raise RuntimeError("login required; use --cookies")

    monkeypatch.setattr(source_downloader, "_get_yt_dlp_class", lambda: _AlwaysFailYDL)
    monkeypatch.setenv("AMICO_YTDLP_AUTO_COOKIES", "1")
    monkeypatch.setenv("AMICO_YTDLP_COOKIE_BROWSERS", "chrome")

    try:
        source_downloader._extract_info_with_retries(
            "https://www.tiktok.com/@user/video/1",
            {"quiet": True},
            download=False,
        )
        assert False, "Expected RuntimeError"
    except RuntimeError as exc:
        text = str(exc)
        assert "Please log into that platform in your browser" in text
        assert "Original error" in text

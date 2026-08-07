"""The environment knobs the worker reads.

They used to be bare os.environ.get calls with their defaults inlined at the
point of use, spread across four modules — undiscoverable, and each with its
own idea of what counts as "off".
"""
import pytest

from core import runtime_config as rc


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF", "False"])
def test_a_flag_is_off_for_any_of_the_usual_spellings(value, monkeypatch):
    monkeypatch.setenv("AMICOSCRIPT_RESUME_JOBS", value)
    assert rc.resume_interrupted_jobs() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "anything"])
def test_anything_else_is_on(value, monkeypatch):
    monkeypatch.setenv("AMICOSCRIPT_RESUME_JOBS", value)
    assert rc.resume_interrupted_jobs() is True


def test_unset_and_empty_both_fall_back_to_the_default(monkeypatch):
    monkeypatch.delenv("AMICOSCRIPT_RESUME_JOBS", raising=False)
    assert rc.resume_interrupted_jobs() is True
    # An empty value is how a shell exports a variable it did not set.
    monkeypatch.setenv("AMICOSCRIPT_RESUME_JOBS", "")
    assert rc.resume_interrupted_jobs() is True
    monkeypatch.setenv("AMICO_WORD_TIMESTAMPS", "")
    assert rc.word_timestamps_default() is False


@pytest.mark.parametrize(
    "raw,expected",
    [("5", 5), ("1", 1), ("0", 1), ("-3", 1), ("99", 8), ("nonsense", 2), ("", 2)],
)
def test_download_concurrency_is_clamped_not_validated(raw, expected, monkeypatch):
    """A nonsense value should change the speed, never stop the app starting."""
    monkeypatch.setenv("AMICOSCRIPT_DOWNLOAD_CONCURRENCY", raw)
    assert rc.download_concurrency() == expected


def test_download_concurrency_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("AMICOSCRIPT_DOWNLOAD_CONCURRENCY", raising=False)
    assert rc.download_concurrency() == rc.DEFAULT_DOWNLOAD_CONCURRENCY


def test_cookie_browsers_are_split_and_normalised(monkeypatch):
    monkeypatch.setenv("AMICO_YTDLP_COOKIE_BROWSERS", " Chrome , FIREFOX ,, ")
    assert rc.ytdlp_cookie_browsers() == ["chrome", "firefox"]


def test_an_empty_browser_list_falls_back_rather_than_disabling_cookies(monkeypatch):
    """An empty list would silently turn the cookie retry into a no-op."""
    monkeypatch.setenv("AMICO_YTDLP_COOKIE_BROWSERS", " , , ")
    assert rc.ytdlp_cookie_browsers() == rc.DEFAULT_COOKIE_BROWSERS


def test_the_browser_list_is_a_copy_callers_cannot_corrupt(monkeypatch):
    monkeypatch.delenv("AMICO_YTDLP_COOKIE_BROWSERS", raising=False)
    rc.ytdlp_cookie_browsers().append("netscape")
    assert "netscape" not in rc.ytdlp_cookie_browsers()


def test_cookie_retry_is_on_unless_turned_off(monkeypatch):
    monkeypatch.delenv("AMICO_YTDLP_AUTO_COOKIES", raising=False)
    assert rc.ytdlp_auto_cookies() is True
    monkeypatch.setenv("AMICO_YTDLP_AUTO_COOKIES", "off")
    assert rc.ytdlp_auto_cookies() is False

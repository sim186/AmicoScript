"""Tests for RFC 5987 Content-Disposition helper (issue #25)."""
from http_utils import content_disposition_attachment


def test_ascii_filename_roundtrips_plainly() -> None:
    v = content_disposition_attachment("notes.md")
    assert 'filename="notes.md"' in v
    assert "filename*=UTF-8''notes.md" in v
    v.encode("latin-1")  # header must be latin-1 encodable


def test_curly_apostrophe_filename_is_latin1_safe() -> None:
    # The exact filename from issue #25.
    name = "SEO - 3.3 - Les bases techniques d’écriture.mp4"
    v = content_disposition_attachment(name)
    # Must not raise — the original bug.
    encoded = v.encode("latin-1")
    # Percent-encoded UTF-8 must round-trip back to the original.
    assert "%E2%80%99" in v  # U+2019 RIGHT SINGLE QUOTATION MARK in UTF-8
    assert "%C3%A9" in v     # é
    # Fallback filename must be pure ASCII.
    assert b'filename="SEO - 3.3 - Les bases techniques d?' in encoded


def test_quote_in_filename_is_sanitized_in_fallback() -> None:
    v = content_disposition_attachment('weird"name.txt')
    # Bare quote in fallback would break the header — must be replaced.
    assert 'filename="weird_name.txt"' in v
    # Original quote is percent-encoded in RFC 5987 form.
    assert "%22" in v
    v.encode("latin-1")


def test_non_latin_script_filename() -> None:
    v = content_disposition_attachment("会议记录.txt")
    v.encode("latin-1")
    assert "filename*=UTF-8''" in v

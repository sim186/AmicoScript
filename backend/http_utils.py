"""Shared HTTP helpers."""
from urllib.parse import quote


def content_disposition_attachment(filename: str) -> str:
    """RFC 5987 Content-Disposition header value safe for non-latin-1 filenames."""
    ascii_fallback = filename.encode("ascii", "replace").decode("ascii").replace('"', "_")
    return (
        f'attachment; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )

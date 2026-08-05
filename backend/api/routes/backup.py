"""Library portability — export the whole library to a file, import it back.

Until now a library existed in exactly one place: the SQLite file under
``~/.amicoscript``. There was no backup, no way to move a library from the
Docker install to the desktop app, and nothing to restore from if the database
was lost. For a tool whose pitch is "keep your recordings on your own machine",
that made the user solely responsible for data they had no way to carry.

The bundle is an ordinary zip:

    manifest.json          format + version + counts
    data.json              folders, tags, recordings, transcripts, analyses
    audio/<rec-id>/<file>  the recordings themselves (optional)

Notably absent: ``settings.json``. It holds the Hugging Face token, the LLM API
key and the password hash, none of which should travel inside a file people
email to themselves or attach to a bug report.
"""
from __future__ import annotations

import json
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Iterable

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from db import get_session
from http_utils import content_disposition_attachment as _content_disposition
from models import Analysis, Folder, Recording, RecordingTag, Tag, Transcript

router = APIRouter()

BUNDLE_FORMAT = "amicoscript-library"
BUNDLE_FORMAT_VERSION = 1

# Import guards. A bundle is normally self-produced, but it arrives as an
# uploaded file, so it is treated as untrusted input.
MAX_MANIFEST_BYTES = 1 * 1024 * 1024
MAX_DATA_BYTES = 512 * 1024 * 1024
# Refuse absurd compression ratios (zip bombs); real audio barely compresses.
MAX_COMPRESSION_RATIO = 200


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def _rows_to_dicts(rows: Iterable) -> list[dict]:
    return [row.model_dump() for row in rows]


def _collect_library(session: Session, ids: list[str] | None) -> dict:
    recordings_stmt = select(Recording)
    if ids:
        recordings_stmt = recordings_stmt.where(Recording.id.in_(ids))
    recordings = session.exec(recordings_stmt).all()
    recording_ids = [r.id for r in recordings]

    if recording_ids:
        transcripts = session.exec(
            select(Transcript).where(Transcript.recording_id.in_(recording_ids))
        ).all()
        analyses = session.exec(
            select(Analysis).where(Analysis.recording_id.in_(recording_ids))
        ).all()
        links = session.exec(
            select(RecordingTag).where(RecordingTag.recording_id.in_(recording_ids))
        ).all()
    else:
        transcripts, analyses, links = [], [], []

    # Folders and tags travel whole: a partial export that dropped the folder a
    # recording points at would import as an orphan.
    folders = session.exec(select(Folder)).all()
    tags = session.exec(select(Tag)).all()

    return {
        "folders": _rows_to_dicts(folders),
        "tags": _rows_to_dicts(tags),
        "recordings": _rows_to_dicts(recordings),
        "recording_tags": _rows_to_dicts(links),
        "transcripts": _rows_to_dicts(transcripts),
        "analyses": _rows_to_dicts(analyses),
    }


@router.get("/api/library/export")
def export_library(
    background_tasks: BackgroundTasks,
    include_audio: bool = True,
    ids: str = "",
    session: Session = Depends(get_session),
):
    """Download the library as a zip bundle.

    ``ids`` — optional comma-separated recording ids, to export a subset.
    ``include_audio`` — set false for a metadata-only bundle (much smaller).
    """
    from storage import get_recording_audio_path

    id_list = [part.strip() for part in ids.split(",") if part.strip()] or None
    data = _collect_library(session, id_list)
    if not data["recordings"]:
        raise HTTPException(404, "No recordings to export")

    manifest = {
        "format": BUNDLE_FORMAT,
        "format_version": BUNDLE_FORMAT_VERSION,
        "exported_at": time.time(),
        "include_audio": bool(include_audio),
        "counts": {key: len(value) for key, value in data.items()},
    }
    try:
        from api.routes.releases import get_version
        manifest["app_version"] = get_version().get("version", "")
    except Exception:
        manifest["app_version"] = ""

    # Written to a temp file rather than streamed from memory: a library with
    # audio is routinely several GB.
    tmp = tempfile.NamedTemporaryFile(prefix="amicoscript-export-", suffix=".zip", delete=False)
    tmp.close()
    tmp_path = Path(tmp.name)

    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("manifest.json", json.dumps(manifest, indent=2))
            bundle.writestr("data.json", json.dumps(data, ensure_ascii=False, indent=2))

            if include_audio:
                for rec in data["recordings"]:
                    audio_path = get_recording_audio_path(rec["id"], rec.get("file_path") or "")
                    if not audio_path.exists() or not audio_path.is_file():
                        continue
                    # Audio is already compressed; storing it avoids a pointless
                    # second pass over gigabytes of data.
                    bundle.write(
                        audio_path,
                        arcname=f"audio/{rec['id']}/{audio_path.name}",
                        compress_type=zipfile.ZIP_STORED,
                    )
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    # FastAPI runs the declared BackgroundTasks once the file has been sent,
    # so the temp bundle is deleted after the download, not before it.
    background_tasks.add_task(tmp_path.unlink, missing_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return FileResponse(
        str(tmp_path),
        media_type="application/zip",
        headers={"Content-Disposition": _content_disposition(f"amicoscript-library-{stamp}.zip")},
    )


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def _safe_member_path(name: str) -> Path | None:
    """Return the relative path of a zip entry, or None if it is unsafe.

    Blocks absolute paths and any '..' traversal — a crafted bundle must not be
    able to write outside the recordings directory (zip-slip).
    """
    if not name or name.endswith("/"):
        return None
    pure = Path(name.replace("\\", "/"))
    if pure.is_absolute() or any(part == ".." for part in pure.parts):
        return None
    if pure.drive or name.startswith("/"):
        return None
    return pure


def _read_bundle_json(bundle: zipfile.ZipFile, name: str, max_bytes: int) -> dict:
    try:
        info = bundle.getinfo(name)
    except KeyError as exc:
        raise HTTPException(400, f"Bundle is missing {name}") from exc
    if info.file_size > max_bytes:
        raise HTTPException(400, f"{name} is implausibly large ({info.file_size} bytes)")
    if info.compress_size and info.file_size / max(info.compress_size, 1) > MAX_COMPRESSION_RATIO:
        raise HTTPException(400, f"{name} has a suspicious compression ratio")
    try:
        return json.loads(bundle.read(name).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(400, f"{name} is not valid JSON: {exc}") from exc


def _filter_columns(row: dict, model) -> dict:
    """Keep only keys the model actually has, so older bundles still import."""
    fields = set(model.model_fields.keys())
    return {k: v for k, v in row.items() if k in fields}


@router.post("/api/library/import")
async def import_library(
    file: UploadFile = File(...),
    mode: str = Form("skip"),
    session: Session = Depends(get_session),
) -> dict:
    """Import a bundle produced by /api/library/export.

    ``mode=skip`` (default) leaves existing rows untouched; ``mode=overwrite``
    replaces them. Rows are matched by primary key, so re-importing the same
    bundle twice is a no-op rather than a duplicate library.
    """
    from config import RECORDINGS_DIR

    if mode not in {"skip", "overwrite"}:
        raise HTTPException(400, "mode must be 'skip' or 'overwrite'")

    tmp = tempfile.NamedTemporaryFile(prefix="amicoscript-import-", suffix=".zip", delete=False)
    tmp_path = Path(tmp.name)
    try:
        while chunk := await file.read(1024 * 1024):
            tmp.write(chunk)
        tmp.close()

        if not zipfile.is_zipfile(tmp_path):
            raise HTTPException(400, "Not a valid AmicoScript bundle (expected a .zip file)")

        with zipfile.ZipFile(tmp_path) as bundle:
            manifest = _read_bundle_json(bundle, "manifest.json", MAX_MANIFEST_BYTES)
            if manifest.get("format") != BUNDLE_FORMAT:
                raise HTTPException(400, "This zip is not an AmicoScript library bundle")
            version = int(manifest.get("format_version", 0))
            if version > BUNDLE_FORMAT_VERSION:
                raise HTTPException(
                    400,
                    f"Bundle format v{version} is newer than this build supports "
                    f"(v{BUNDLE_FORMAT_VERSION}); update AmicoScript to import it",
                )

            data = _read_bundle_json(bundle, "data.json", MAX_DATA_BYTES)
            counts = _import_rows(session, data, mode)
            counts["audio"] = _restore_audio(bundle, session, RECORDINGS_DIR, mode)

        session.commit()
        return {"ok": True, "imported": counts, "mode": mode}
    finally:
        try:
            tmp.close()
        except Exception:
            pass
        tmp_path.unlink(missing_ok=True)


_IMPORT_ORDER = [
    ("folders", Folder, ("id",)),
    ("tags", Tag, ("id",)),
    ("recordings", Recording, ("id",)),
    ("transcripts", Transcript, ("id",)),
    ("analyses", Analysis, ("id",)),
    ("recording_tags", RecordingTag, ("recording_id", "tag_id")),
]


def _import_rows(session: Session, data: dict, mode: str) -> dict:
    counts: dict[str, int] = {}
    for key, model, pk_fields in _IMPORT_ORDER:
        rows = data.get(key) or []
        if not isinstance(rows, list):
            raise HTTPException(400, f"data.json: '{key}' must be a list")

        added = 0
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            payload = _filter_columns(raw, model)
            pk = tuple(payload.get(f) for f in pk_fields)
            if any(v is None for v in pk):
                continue

            existing = session.get(model, pk[0] if len(pk) == 1 else pk)
            if existing is not None:
                if mode != "overwrite":
                    continue
                for field, value in payload.items():
                    setattr(existing, field, value)
                session.add(existing)
                added += 1
                continue

            session.add(model(**payload))
            added += 1
        # Flush per table so foreign keys resolve in _IMPORT_ORDER sequence.
        session.flush()
        counts[key] = added
    return counts


def _restore_audio(
    bundle: zipfile.ZipFile, session: Session, recordings_dir: Path, mode: str
) -> int:
    """Extract audio/<recording-id>/<file> entries into managed storage."""
    restored = 0
    for info in bundle.infolist():
        if not info.filename.startswith("audio/"):
            continue
        rel = _safe_member_path(info.filename)
        if rel is None or len(rel.parts) != 3:
            continue
        if info.compress_size and info.file_size / max(info.compress_size, 1) > MAX_COMPRESSION_RATIO:
            continue

        recording_id = rel.parts[1]
        rec = session.get(Recording, recording_id)
        if rec is None:
            continue

        dest_dir = recordings_dir / recording_id
        dest = dest_dir / Path(rel.parts[2]).name
        if dest.exists() and mode != "overwrite":
            # Still repair a stale path (e.g. a bundle from another machine).
            if rec.file_path != str(dest):
                rec.file_path = str(dest)
                session.add(rec)
            continue

        dest_dir.mkdir(parents=True, exist_ok=True)
        with bundle.open(info) as src, open(dest, "wb") as out:
            while chunk := src.read(1024 * 1024):
                out.write(chunk)

        # The exporting machine's absolute path is meaningless here.
        rec.file_path = str(dest)
        session.add(rec)
        restored += 1

    return restored

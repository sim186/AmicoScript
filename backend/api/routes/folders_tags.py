"""Folder, tag, and search endpoints."""

import os
import threading
import time
from pathlib import Path

from db import get_session
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from models import Folder, Recording, RecordingTag, Tag, Transcript
from sqlalchemy import func
from sqlmodel import Session, select

router = APIRouter()

ALLOWED_COLORS = {
    "#6c63ff", "#f59e0b", "#10b981", "#f472b6", "#60a5fa",
    "#fb7185", "#a78bfa", "#fbbf24", "#16a34a", "#ef4444",
    "#ff0000", "#111111", "#1877f2", "#e1306c", "#25f4ee",
    "#1ab7ea", "#9146ff",
}


@router.get("/api/folders")
def list_folders(session: Session = Depends(get_session)) -> list:
    folders = session.exec(select(Folder)).all()
    counts = {}
    try:
        rows = session.exec(select(Recording.folder_id, func.count(Recording.id)).group_by(Recording.folder_id)).all()
        for r in rows:
            key = r[0]
            val = int(r[1])
            counts[key] = val
    except Exception:
        counts = {}

    return [
        {
            "id": f.id,
            "name": f.name,
            "parent_id": f.parent_id,
            "color_code": f.color_code,
            "created_at": f.created_at,
            "count": counts.get(f.id, 0),
        }
        for f in folders
    ]


@router.post("/api/folders")
async def create_folder(
    name: str = Form(...),
    parent_id: str = Form(""),
    color_code: str = Form("#6c63ff"),
    session: Session = Depends(get_session),
) -> dict:
    if color_code and color_code.lower() not in ALLOWED_COLORS:
        raise HTTPException(400, "Invalid color_code")
    folder = Folder(name=name, parent_id=parent_id or None, color_code=color_code or "#6c63ff")
    session.add(folder)
    session.commit()
    session.refresh(folder)
    return {
        "id": folder.id,
        "name": folder.name,
        "parent_id": folder.parent_id,
        "color_code": folder.color_code,
        "created_at": folder.created_at,
    }


@router.patch("/api/folders/{folder_id}")
async def update_folder(
    folder_id: str,
    name: str = Form(""),
    parent_id: str = Form("__unset__"),
    color_code: str = Form("__unset__"),
    session: Session = Depends(get_session),
) -> dict:
    folder = session.get(Folder, folder_id)
    if not folder:
        raise HTTPException(404, "Folder not found")
    if name:
        folder.name = name
    if parent_id != "__unset__":
        folder.parent_id = parent_id or None
    if color_code != "__unset__":
        if color_code and color_code.lower() not in ALLOWED_COLORS:
            raise HTTPException(400, "Invalid color_code")
        folder.color_code = color_code or "#6c63ff"
    session.add(folder)
    session.commit()
    session.refresh(folder)
    return {
        "id": folder.id,
        "name": folder.name,
        "parent_id": folder.parent_id,
        "color_code": folder.color_code,
        "created_at": folder.created_at,
    }


@router.delete("/api/folders/{folder_id}")
def delete_folder(folder_id: str, delete_recordings: bool = False, session: Session = Depends(get_session)) -> dict:
    folder = session.get(Folder, folder_id)
    if not folder:
        raise HTTPException(404, "Folder not found")

    all_folder_ids: list[str] = [folder_id]
    queue: list[str] = [folder_id]
    while queue:
        current = queue.pop()
        children = session.exec(select(Folder).where(Folder.parent_id == current)).all()
        for child in children:
            all_folder_ids.append(child.id)
            queue.append(child.id)

    for fid in all_folder_ids:
        recordings_in_folder = session.exec(select(Recording).where(Recording.folder_id == fid)).all()
        if delete_recordings:
            for rec in recordings_in_folder:
                try:
                    fp = Path(rec.file_path)
                    if fp.exists():
                        fp.unlink()
                    if fp.parent.exists() and not any(fp.parent.iterdir()):
                        fp.parent.rmdir()
                except OSError:
                    pass
                for link in session.exec(select(RecordingTag).where(RecordingTag.recording_id == rec.id)).all():
                    session.delete(link)
                for tr in session.exec(select(Transcript).where(Transcript.recording_id == rec.id)).all():
                    session.delete(tr)
                session.delete(rec)
        else:
            for rec in recordings_in_folder:
                rec.folder_id = None
                session.add(rec)

        if fid != folder_id:
            child_folder = session.get(Folder, fid)
            if child_folder:
                session.delete(child_folder)

    session.delete(folder)
    session.commit()
    return {"ok": True}


@router.get("/api/tags")
def list_tags(folder_id: str = "", session: Session = Depends(get_session)) -> list:
    tags = session.exec(select(Tag)).all()
    counts = {}
    try:
        if folder_id:
            rows = session.exec(
                select(RecordingTag.tag_id, func.count(RecordingTag.recording_id))
                .join(Recording, Recording.id == RecordingTag.recording_id)
                .where(Recording.folder_id == folder_id)
                .group_by(RecordingTag.tag_id)
            ).all()
        else:
            rows = session.exec(select(RecordingTag.tag_id, func.count(RecordingTag.recording_id)).group_by(RecordingTag.tag_id)).all()
        for r in rows:
            counts[r[0]] = int(r[1])
    except Exception:
        counts = {}

    return [{"id": t.id, "name": t.name, "color_code": t.color_code, "count": counts.get(t.id, 0)} for t in tags]


@router.post("/api/tags")
async def create_tag(name: str = Form(...), color_code: str = Form("#6c63ff"), session: Session = Depends(get_session)) -> dict:
    if color_code and color_code.lower() not in ALLOWED_COLORS:
        raise HTTPException(400, "Invalid color_code")
    tag = Tag(name=name, color_code=color_code)
    session.add(tag)
    session.commit()
    session.refresh(tag)
    return {"id": tag.id, "name": tag.name, "color_code": tag.color_code}


@router.patch("/api/tags/{tag_id}")
async def update_tag(
    tag_id: str,
    name: str = Form(""),
    color_code: str = Form(""),
    session: Session = Depends(get_session),
) -> dict:
    tag = session.get(Tag, tag_id)
    if not tag:
        raise HTTPException(404, "Tag not found")
    if name:
        tag.name = name
    if color_code:
        if color_code and color_code.lower() not in ALLOWED_COLORS:
            raise HTTPException(400, "Invalid color_code")
        tag.color_code = color_code
    session.add(tag)
    session.commit()
    session.refresh(tag)
    return {"id": tag.id, "name": tag.name, "color_code": tag.color_code}


@router.delete("/api/tags/{tag_id}")
def delete_tag(tag_id: str, session: Session = Depends(get_session)) -> dict:
    tag = session.get(Tag, tag_id)
    if not tag:
        raise HTTPException(404, "Tag not found")
    for link in session.exec(select(RecordingTag).where(RecordingTag.tag_id == tag_id)).all():
        session.delete(link)
    session.delete(tag)
    session.commit()
    return {"ok": True}


@router.post("/api/recordings/{recording_id}/tags/{tag_id}")
def add_recording_tag(recording_id: str, tag_id: str, session: Session = Depends(get_session)) -> dict:
    if not session.get(Recording, recording_id):
        raise HTTPException(404, "Recording not found")
    if not session.get(Tag, tag_id):
        raise HTTPException(404, "Tag not found")
    existing = session.get(RecordingTag, (recording_id, tag_id))
    if not existing:
        session.add(RecordingTag(recording_id=recording_id, tag_id=tag_id))
        session.commit()
    return {"ok": True}


@router.delete("/api/recordings/{recording_id}/tags/{tag_id}")
def remove_recording_tag(recording_id: str, tag_id: str, session: Session = Depends(get_session)) -> dict:
    link = session.get(RecordingTag, (recording_id, tag_id))
    if link:
        session.delete(link)
        session.commit()
    return {"ok": True}


@router.get("/api/search")
def search_library(q: str = "", limit: int = 20, offset: int = 0, session: Session = Depends(get_session)) -> list:
    if not q.strip():
        return []

    from sqlalchemy import text as _text
    from sqlalchemy.exc import OperationalError

    safe_limit = min(limit, 100)

    try:
        fts_rows = session.exec(
            _text(
                """
                SELECT t.recording_id,
                       snippet(transcript_fts, 0, '<mark>', '</mark>', '…', 20) AS snippet
                FROM transcript_fts
                JOIN transcript t ON transcript_fts.rowid = t.rowid
                WHERE transcript_fts MATCH :q
                ORDER BY rank
                LIMIT :lim OFFSET :off
                """
            ),
            params={"q": q, "lim": safe_limit, "off": offset},
        ).all()

        meta_rows = session.exec(
            _text(
                """
                SELECT DISTINCT r.id as recording_id,
                       CASE
                         WHEN f.name LIKE :ql THEN 'Folder: ' || f.name
                         WHEN t.name LIKE :ql THEN 'Tag: ' || t.name
                         ELSE 'Title: ' || r.filename
                       END as snippet
                FROM recording r
                LEFT JOIN folder f ON r.folder_id = f.id
                LEFT JOIN recordingtag rt ON r.id = rt.recording_id
                LEFT JOIN tag t ON rt.tag_id = t.id
                WHERE r.filename LIKE :ql
                   OR f.name LIKE :ql
                   OR t.name LIKE :ql
                ORDER BY r.filename
                LIMIT :lim OFFSET :off
                """
            ),
            params={"ql": f"%{q}%", "lim": safe_limit, "off": offset},
        ).all()

        fts_ids = {r.recording_id: r.snippet for r in fts_rows}
        ordered = list(fts_rows)
        for r in meta_rows:
            if r.recording_id not in fts_ids:
                ordered.append(r)
        rows = ordered[:safe_limit]
    except OperationalError:
        rows = session.exec(
            _text(
                """
                SELECT DISTINCT r.id AS recording_id,
                       CASE
                         WHEN f.name LIKE :ql THEN 'Folder: ' || f.name
                         WHEN t.name LIKE :ql THEN 'Tag: ' || t.name
                         WHEN r.filename LIKE :ql THEN 'Title: ' || r.filename
                         ELSE COALESCE(substr(tr.full_text, 1, 100), 'Metadata match')
                       END AS snippet
                FROM recording r
                LEFT JOIN transcript tr ON r.id = tr.recording_id
                LEFT JOIN folder f ON r.folder_id = f.id
                LEFT JOIN recordingtag rt ON r.id = rt.recording_id
                LEFT JOIN tag t ON rt.tag_id = t.id
                WHERE r.filename LIKE :ql
                   OR tr.full_text LIKE :ql
                   OR f.name LIKE :ql
                   OR t.name LIKE :ql
                ORDER BY r.filename
                LIMIT :lim OFFSET :off
                """
            ),
            params={"ql": f"%{q}%", "lim": safe_limit, "off": offset},
        ).all()

    results = []
    for row in rows:
        rec = session.get(Recording, row.recording_id)
        if rec:
            results.append(
                {
                    "recording_id": row.recording_id,
                    "filename": rec.filename,
                    "duration": rec.duration,
                    "snippet": row.snippet,
                }
            )
    return results


@router.post("/api/exit")
async def api_exit(request: Request):
    try:
        client_host = request.client.host if request.client else ""
    except Exception:
        client_host = ""

    if client_host not in ("127.0.0.1", "::1", "localhost"):
        return {"status": "ignored"}

    def _delayed_exit() -> None:
        time.sleep(0.1)
        os._exit(0)

    threading.Thread(target=_delayed_exit, daemon=True).start()
    return {"status": "ok"}

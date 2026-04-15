"""AmicoScript FastAPI application entrypoint.

This module wires app startup/background tasks, mounts static assets,
and includes API routers from backend/api/routes.
"""

import asyncio
import os
import sys
import time
from pathlib import Path

# Must be set before torch is imported anywhere (even transitively).
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

_STDIO_FALLBACK_HANDLES = []


def _ensure_standard_streams() -> None:
    if sys.stdin is None:
        h = open(os.devnull, "r", encoding="utf-8", errors="replace")
        _STDIO_FALLBACK_HANDLES.append(h)
        sys.stdin = h
    if sys.stdout is None:
        h = open(os.devnull, "w", encoding="utf-8", errors="replace")
        _STDIO_FALLBACK_HANDLES.append(h)
        sys.stdout = h
    if sys.stderr is None:
        h = open(os.devnull, "w", encoding="utf-8", errors="replace")
        _STDIO_FALLBACK_HANDLES.append(h)
        sys.stderr = h


_ensure_standard_streams()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import select

import state
from api.routes.analyses import router as analyses_router
from api.routes.folders_tags import router as folders_tags_router
from api.routes.library import router as library_router
from api.routes.llm import router as llm_router
from api.routes.releases import get_version
from api.routes.releases import router as releases_router
from api.routes.settings import router as settings_router
from api.routes.transcription import router as transcription_router
from core.job_helpers import _cleanup_job_temp_files
from core.transcription import _worker_loop_async
from db import init_db, new_session
from models import Recording
from releases import _fetch_latest_release, _is_version_newer

if hasattr(sys, "_MEIPASS"):
    BASE_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).parent

if (BASE_DIR / "frontend").exists():
    FRONTEND_DIR = BASE_DIR / "frontend"
else:
    FRONTEND_DIR = BASE_DIR.parent / "frontend"

if (BASE_DIR / "scripts").exists():
    SCRIPTS_DIR = BASE_DIR / "scripts"
else:
    SCRIPTS_DIR = BASE_DIR.parent / "scripts"

app = FastAPI(title="AmicoScript")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(settings_router)
app.include_router(llm_router)
app.include_router(analyses_router)
app.include_router(releases_router)
app.include_router(transcription_router)
app.include_router(library_router)
app.include_router(folders_tags_router)


@app.on_event("startup")
async def _startup() -> None:
    state.event_loop = asyncio.get_running_loop()
    init_db()
    _recover_interrupted_jobs()
    try:
        app.state.local_version = _get_local_version() or ""
    except Exception:
        app.state.local_version = ""
    asyncio.create_task(_worker_loop_async())
    asyncio.create_task(_cleanup_loop())
    try:
        asyncio.create_task(_release_poller_loop())
    except Exception:
        pass


def _recover_interrupted_jobs() -> None:
    try:
        with new_session() as session:
            interrupted = session.exec(
                select(Recording).where(Recording.status.in_(["queued", "transcribing", "diarizing"]))
            ).all()
            for rec in interrupted:
                rec.status = "error"
                session.add(rec)
            session.commit()
    except Exception:
        pass


def _get_local_version() -> str:
    try:
        return get_version().get("version", "") or ""
    except Exception:
        return ""


async def _release_poller_loop() -> None:
    owner = os.environ.get("GITHUB_OWNER", "sim186")
    repo = os.environ.get("GITHUB_REPO", "AmicoScript")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not owner or not repo:
        return

    app.state.latest_release = {"tag_name": "", "html_url": "", "name": "", "body": ""}

    while True:
        try:
            info = _fetch_latest_release(owner, repo, token or None)
            if info and not info.get("error"):
                tag = info.get("tag_name", "")
                app.state.latest_release = {
                    "tag_name": tag,
                    "html_url": info.get("html_url", ""),
                    "name": info.get("name", ""),
                    "body": info.get("body", ""),
                }
                local = _get_local_version()
                try:
                    app.state.update_available = _is_version_newer(local, tag)
                    app.state.local_version = local
                except Exception:
                    app.state.update_available = False
            else:
                app.state.latest_release = {"error": info.get("error", "unknown")}
        except Exception:
            pass
        await asyncio.sleep(60 * 60 * 4)


async def _cleanup_loop() -> None:
    from config import STORAGE_ROOT

    while True:
        await asyncio.sleep(3600)
        cutoff = time.time() - 3600
        for job_id in list(state.jobs.keys()):
            job = state.jobs[job_id]
            if job.get("created_at", 0) < cutoff:
                fp = job.get("file_path", "")
                if fp and os.path.exists(fp):
                    try:
                        if not Path(fp).is_relative_to(STORAGE_ROOT):
                            os.remove(fp)
                    except OSError:
                        pass
                _cleanup_job_temp_files(job)
                state.jobs.pop(job_id, None)


if SCRIPTS_DIR.exists():
    app.mount("/scripts", StaticFiles(directory=str(SCRIPTS_DIR)), name="scripts")

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

    changelog_path = BASE_DIR / "CHANGELOG.md"
    if changelog_path.exists():

        @app.get("/CHANGELOG.md")
        async def _serve_changelog():
            return FileResponse(str(changelog_path))

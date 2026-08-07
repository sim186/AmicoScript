"""AmicoScript FastAPI application entrypoint.

This module wires app startup/background tasks, mounts static assets,
and includes API routers from backend/api/routes.
"""

import asyncio
import json
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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import select

import auth
import state
from api.routes.analyses import router as analyses_router
from api.routes.auth import router as auth_router
from api.routes.backup import router as backup_router
from api.routes.benchmark import router as benchmark_router
from api.routes.folders_tags import router as folders_tags_router
from api.routes.library import router as library_router
from api.routes.library_chat import router as library_chat_router
from api.routes.llm import router as llm_router
from api.routes.releases import get_version
from api.routes.releases import router as releases_router
from api.routes.settings import router as settings_router
from api.routes.transcription import router as transcription_router
from core.job_helpers import _cleanup_job_temp_files
from core.job_status import ACTIVE as ACTIVE_STATUSES
from core.job_status import RESUMABLE as RESUMABLE_STATUSES
from core.job_status import JobStatus
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
    allow_origins=[
        "http://localhost:8002",
        "http://127.0.0.1:8002",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Paths reachable without a session. The auth routes handle their own rules,
# and the release/version endpoints carry nothing private but are read by the
# UI before login to render the update banner.
AUTH_EXEMPT_PATHS = {
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/password",
    "/api/auth/api-token",
    "/api/version",
}


@app.middleware("http")
async def _require_auth(request, call_next):
    """Gate the API on the rules in backend/auth.py.

    Static assets stay public — the frontend has to load in order to show a
    login form — but everything under /api and /scripts is checked.
    """
    path = request.url.path
    if not (path.startswith("/api") or path.startswith("/scripts")):
        return await call_next(request)
    if path in AUTH_EXEMPT_PATHS:
        return await call_next(request)

    decision = auth.evaluate_request(request)
    if not decision.allowed:
        return JSONResponse(
            {"detail": decision.message, "code": decision.code},
            status_code=decision.status_code,
        )
    return await call_next(request)


app.include_router(auth_router)
app.include_router(settings_router)
app.include_router(llm_router)
app.include_router(analyses_router)
app.include_router(releases_router)
app.include_router(transcription_router)
app.include_router(library_router)
app.include_router(library_chat_router)
app.include_router(folders_tags_router)
app.include_router(benchmark_router)
app.include_router(backup_router)


@app.on_event("startup")
async def _startup() -> None:
    import secrets
    from config import ensure_storage_dirs
    ensure_storage_dirs()
    state._init_queue()
    state.exit_token = secrets.token_hex(32)
    state.event_loop = asyncio.get_running_loop()
    init_db()
    _recover_interrupted_jobs()
    _warn_if_exposed_without_password()
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
    _maybe_start_embedded_watcher()


# Handle to the embedded meeting watcher (thread + module), set by
# _maybe_start_embedded_watcher so the shutdown hook can signal a clean stop
# and let an in-progress capture finalize instead of being lost with the
# daemon thread. None when the watcher isn't running (Docker / non-Windows).
_watcher_thread = None
_watcher_module = None


def _watcher_output_dir() -> Path:
    """User-writable folder for meeting captures (never Program Files)."""
    try:
        from config import STORAGE_ROOT
        return Path(STORAGE_ROOT) / "meetings"
    except Exception:
        return Path.home() / "AmicoScript" / "meetings"


def _maybe_start_external_watcher_task() -> None:
    """Start the installed per-user watcher task, if setup.bat registered it."""
    import platform
    import subprocess

    if platform.system() != "Windows":
        return
    task_name = os.environ.get("AMICOSCRIPT_WATCHER_TASK", "AmicoScript Meeting Watcher")
    try:
        proc = subprocess.run(
            ["schtasks", "/Run", "/TN", task_name],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception as exc:
        print(f"External meeting watcher task could not be started ({exc}).")
        return
    if proc.returncode == 0:
        print(f"External meeting watcher task started: {task_name}")
    else:
        msg = (proc.stderr or proc.stdout or "").strip()
        print(f"External meeting watcher task not available: {task_name}. {msg}")


def _maybe_start_embedded_watcher() -> None:
    """Run the meeting watcher in-process on the native Windows host.

    The watcher needs WASAPI/mic access, which only exists when AmicoScript runs
    directly on the host (the PyInstaller build), not inside the Linux Docker
    image. So we start it here only on Windows; Docker/other platforms fall back
    to the external watcher (scripts/meeting_watcher). Override with
    AMICOSCRIPT_EMBEDDED_WATCHER=on|off|auto (default auto).
    """
    import platform

    mode = os.environ.get("AMICOSCRIPT_EMBEDDED_WATCHER", "auto").lower()
    if mode in {"0", "off", "false", "no"}:
        _maybe_start_external_watcher_task()
        return
    if mode == "auto" and platform.system() != "Windows":
        return

    watcher_dir = SCRIPTS_DIR / "meeting_watcher"
    if watcher_dir.exists() and str(watcher_dir) not in sys.path:
        sys.path.insert(0, str(watcher_dir))
    os.environ.setdefault("AMICOSCRIPT_WATCHER_OUT", str(_watcher_output_dir()))
    os.environ.setdefault("AMICOSCRIPT_URL", "http://127.0.0.1:8002")

    def _run() -> None:
        global _watcher_module
        try:
            import watcher  # noqa: imports pyaudiowpatch/pycaw — Windows-only
        except Exception as exc:
            # Audio deps not bundled, or not on a host that supports them.
            print(f"Embedded meeting watcher unavailable ({exc}); "
                  "use the external watcher (scripts/meeting_watcher) instead.")
            if mode == "auto":
                _maybe_start_external_watcher_task()
            return
        _watcher_module = watcher
        print("Embedded meeting watcher started (enable via the UI toggle).")
        watcher.run_embedded(base_url="http://127.0.0.1:8002")

    import threading
    global _watcher_thread
    _watcher_thread = threading.Thread(target=_run, daemon=True, name="meeting-watcher")
    _watcher_thread.start()


@app.on_event("shutdown")
async def _shutdown() -> None:
    """Signal the embedded meeting watcher to stop so an in-progress capture
    is finalized (WAV saved + transcription queued) before the process exits."""
    mod = _watcher_module
    if mod is None or not hasattr(mod, "stop_embedded"):
        return
    try:
        mod.stop_embedded()
    except Exception:
        return
    # Give the watcher a moment to finalize a capture; don't block shutdown long.
    if _watcher_thread is not None:
        _watcher_thread.join(timeout=10)


def _warn_if_exposed_without_password() -> None:
    """Print a loud warning when the app is bound beyond loopback unprotected."""
    if not auth.is_enabled() or auth.password_is_set():
        return
    host = os.environ.get("AMICOSCRIPT_HOST", "127.0.0.1")
    if auth.is_loopback_host(host):
        return
    print(
        f"\n  ⚠  AmicoScript is bound to {host} with no password set.\n"
        "     Requests from outside this machine will be refused until you set\n"
        "     one (in the app's Security settings, or via AMICOSCRIPT_PASSWORD).\n"
    )


def _recover_interrupted_jobs() -> None:
    """Requeue work that a restart interrupted instead of failing it.

    The old behaviour flipped every in-flight recording to 'error' with no
    explanation, so a two-hour meeting that was 90% transcribed was simply
    lost and the user had to re-upload it with no idea why. Now anything whose
    audio is still on disk goes back into the queue, and anything that isn't
    resumable is marked 'interrupted' with a reason the UI can display.

    Set AMICOSCRIPT_RESUME_JOBS=0 to restore the previous fail-fast behaviour.
    """
    resume = os.environ.get("AMICOSCRIPT_RESUME_JOBS", "1").lower() not in ("0", "false", "no")
    requeued: list[tuple[str, str, str, dict]] = []

    try:
        with new_session() as session:
            interrupted = session.exec(
                select(Recording).where(Recording.status.in_(sorted(RESUMABLE_STATUSES)))
            ).all()
            for rec in interrupted:
                audio_exists = bool(rec.file_path) and os.path.exists(rec.file_path)
                if resume and audio_exists:
                    try:
                        opts = json.loads(rec.transcription_options or "{}")
                    except (TypeError, ValueError):
                        opts = {}
                    rec.status = JobStatus.QUEUED
                    rec.status_detail = "Requeued after the app restarted"
                    requeued.append((rec.id, rec.filename, rec.file_path, opts))
                else:
                    rec.status = JobStatus.INTERRUPTED
                    rec.status_detail = (
                        "Interrupted by an app restart before the audio was saved — "
                        "please import it again"
                        if not audio_exists
                        else "Interrupted by an app restart"
                    )
                session.add(rec)
            session.commit()
    except Exception as exc:
        print(f"Could not recover interrupted jobs: {exc}")
        return

    for recording_id, filename, file_path, opts in requeued:
        try:
            _requeue_recording(recording_id, filename, file_path, opts)
        except Exception as exc:
            print(f"Could not requeue recording {recording_id}: {exc}")

    if requeued:
        print(f"Requeued {len(requeued)} interrupted transcription job(s).")


def _requeue_recording(recording_id: str, filename: str, file_path: str, opts: dict) -> None:
    """Recreate the in-memory job for a recording and put it back on the queue."""
    from core.requeue import build_job

    job_id = build_job(recording_id, filename, file_path, opts, resumed=True)
    state.jobs[job_id]["message"] = "Requeued after restart"
    state.JOB_QUEUE.put_nowait(job_id)


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


def _should_expire(job: dict, cutoff: float) -> bool:
    """True when *job* is old enough to release and is no longer working.

    Age alone is not enough. A playlist import or a chunked analysis of a long
    meeting can easily outlive the hour, and expiring one of those mid-flight
    detaches the worker from its own SSE queue — see core/job_status.py.
    """
    if job.get("expired") or job.get("status") in ACTIVE_STATUSES:
        return False
    return job.get("created_at", 0) < cutoff


async def _cleanup_loop() -> None:
    from config import STORAGE_ROOT

    while True:
        await asyncio.sleep(3600)
        cutoff = time.time() - 3600
        for job_id in list(state.jobs.keys()):
            job = state.jobs[job_id]
            if not _should_expire(job, cutoff):
                continue
            fp = job.get("file_path", "")
            if fp and os.path.exists(fp):
                try:
                    if not Path(fp).is_relative_to(STORAGE_ROOT):
                        os.remove(fp)
                except OSError:
                    pass
            _cleanup_job_temp_files(job)
            _expire_job(job_id)


def _expire_job(job_id: str) -> None:
    """Release a finished job's memory but keep a tombstone.

    Dropping the entry outright made every /api/jobs/{id}/… route answer 404,
    which is indistinguishable from "that job never existed" — the UI could not
    tell the user their transcript was safe in the library. The tombstone keeps
    the status and the recording id so routes can redirect there instead.
    """
    job = state.jobs.get(job_id)
    if not job:
        return
    state.jobs[job_id] = {
        "id": job_id,
        "type": job.get("type", "transcribe"),
        "recording_id": job.get("recording_id"),
        "status": job.get("status", "done"),
        "progress": job.get("progress", 1.0),
        "message": job.get("message", ""),
        "original_filename": job.get("original_filename", ""),
        "created_at": job.get("created_at", 0.0),
        "expired": True,
        "result": None,
        "logs": [],
        "temp_files": [],
    }


if SCRIPTS_DIR.exists():
    app.mount("/scripts", StaticFiles(directory=str(SCRIPTS_DIR)), name="scripts")

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

    changelog_path = BASE_DIR / "CHANGELOG.md"
    if changelog_path.exists():

        @app.get("/CHANGELOG.md")
        async def _serve_changelog():
            return FileResponse(str(changelog_path))

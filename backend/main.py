"""AmicoScript FastAPI application entrypoint.

This module wires app startup/background tasks, mounts static assets,
and includes API routers from backend/api/routes.
"""

import asyncio
import os
import secrets
import sys
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

import auth
import config
import meeting_watcher_host
import releases
import state
from api.routes.analyses import router as analyses_router
from api.routes.auth import router as auth_router
from api.routes.backup import router as backup_router
from api.routes.benchmark import router as benchmark_router
from api.routes.folders_tags import router as folders_tags_router
from api.routes.library import router as library_router
from api.routes.library_chat import router as library_chat_router
from api.routes.llm import router as llm_router
from api.routes.releases import router as releases_router
from api.routes.search import router as search_router
from api.routes.settings import router as settings_router
from api.routes.transcription import router as transcription_router
from core.job_lifecycle import cleanup_loop, recover_interrupted_jobs
from core.transcription import worker_loop_async
from db import init_db

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
app.include_router(search_router)
app.include_router(benchmark_router)
app.include_router(backup_router)


@app.on_event("startup")
async def _startup() -> None:
    config.ensure_storage_dirs()
    state._init_queue()
    state.exit_token = secrets.token_hex(32)
    state.event_loop = asyncio.get_running_loop()
    init_db()
    recover_interrupted_jobs()
    _warn_if_exposed_without_password()
    app.state.local_version = releases.local_version()
    asyncio.create_task(worker_loop_async())
    asyncio.create_task(cleanup_loop())
    asyncio.create_task(releases.poll_latest_release(app))
    meeting_watcher_host.start(SCRIPTS_DIR)


@app.on_event("shutdown")
async def _shutdown() -> None:
    """Let an in-progress meeting capture finalize before the process exits."""
    meeting_watcher_host.stop()


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


if SCRIPTS_DIR.exists():
    app.mount("/scripts", StaticFiles(directory=str(SCRIPTS_DIR)), name="scripts")

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

    changelog_path = BASE_DIR / "CHANGELOG.md"
    if changelog_path.exists():

        @app.get("/CHANGELOG.md")
        async def _serve_changelog():
            return FileResponse(str(changelog_path))

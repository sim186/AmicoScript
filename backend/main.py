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

API_DESCRIPTION = """
The HTTP API behind the AmicoScript desktop app, web UI, and TUI. Everything the
interface can do — uploading audio, following a job, reading transcripts, running
an LLM analysis — goes through the endpoints below, so a script can drive the same
workflow the UI does.

The server runs on your own machine (or your own Docker host). There is no hosted
AmicoScript API: the base URL is wherever you started it, `http://localhost:8002`
by default.

### Authentication

AmicoScript is local-first, so what a request needs depends on where it comes from
and on the `AMICOSCRIPT_AUTH` mode (see `backend/auth.py`):

* **Loopback requests** (`127.0.0.1`, `::1`) are served without credentials in the
  default `auto` mode — the usual case for a desktop or local Docker install.
* **Everything else** needs a session cookie from `POST /api/auth/login`, or the
  API token in an `Authorization: Bearer <token>` header (`X-Amicoscript-Token` also
  works). Non-loopback requests are refused with `503` until a password is set, so
  exposing the app publicly fails closed rather than publishing your library.

Retrieve the token for headless clients with `GET /api/auth/api-token` from the
machine itself, or from the app's Security settings.

```bash
curl -H "Authorization: Bearer $AMICOSCRIPT_TOKEN" \\
  https://amicoscript.example.com/api/library
```

### Streaming endpoints

Job progress is delivered over Server-Sent Events rather than polling:
`GET /api/jobs/{job_id}/stream` emits a JSON event per update until the job reaches
`done`, `error`, or `cancelled`, with a heartbeat every 30s in between. OpenAPI has
no vocabulary for an SSE stream, so it is documented below as an ordinary `GET` —
read it with an EventSource/SSE client, not a single-shot request.
"""

OPENAPI_TAGS = [
    {"name": "Auth", "description": "Login, logout, session status, and the API token for headless clients."},
    {"name": "Settings", "description": "Whisper defaults, device selection, Hugging Face token, and other stored app settings."},
    {"name": "LLM", "description": "LLM provider configuration, connectivity tests, and model listing/pulling."},
    {"name": "Analyses", "description": "Summaries, action items, translations, and custom prompts run against a transcript."},
    {"name": "Releases", "description": "Version reporting and update checks against the GitHub releases feed."},
    {"name": "Transcription", "description": "Uploads, URL imports, job control, progress streams, and result downloads."},
    {"name": "Library", "description": "Stored recordings: listing, metadata, transcript edits, audio, and exports."},
    {"name": "Library chat", "description": "Retrieval-augmented chat across the transcripts in your library."},
    {"name": "Folders & tags", "description": "Organising recordings into folders and tags, including tag suggestions."},
    {"name": "Search", "description": "Full-text search across transcripts, backed by SQLite FTS5."},
    {"name": "Benchmark", "description": "Local speed benchmarks for the installed Whisper models and devices."},
    {"name": "Backup", "description": "Export and restore the library as a portable backup bundle."},
]

app = FastAPI(
    title="AmicoScript API",
    version=releases.local_version() or "0.0.0",
    description=API_DESCRIPTION,
    openapi_tags=OPENAPI_TAGS,
    license_info={"name": "MIT", "url": "https://github.com/sim186/AmicoScript/blob/main/LICENSE"},
    contact={"name": "AmicoScript on GitHub", "url": "https://github.com/sim186/AmicoScript"},
    servers=[{"url": "http://localhost:8002", "description": "Local install (default)"}],
)

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


# The tags are what group the endpoints in the generated reference
# (website/api.html); they mirror the names declared in OPENAPI_TAGS above.
app.include_router(auth_router, tags=["Auth"])
app.include_router(settings_router, tags=["Settings"])
app.include_router(llm_router, tags=["LLM"])
app.include_router(analyses_router, tags=["Analyses"])
app.include_router(releases_router, tags=["Releases"])
app.include_router(transcription_router, tags=["Transcription"])
app.include_router(library_router, tags=["Library"])
app.include_router(library_chat_router, tags=["Library chat"])
app.include_router(folders_tags_router, tags=["Folders & tags"])
app.include_router(search_router, tags=["Search"])
app.include_router(benchmark_router, tags=["Benchmark"])
app.include_router(backup_router, tags=["Backup"])


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

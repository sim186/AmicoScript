"""Version and release metadata endpoints."""

from pathlib import Path

from fastapi import APIRouter, Request

MODELS_META = [
    {"id": "tiny", "name": "Tiny", "params": "~39M", "ram": "~1 GB", "speed": 5, "accuracy": 1},
    {"id": "base", "name": "Base", "params": "~74M", "ram": "~1 GB", "speed": 4, "accuracy": 2},
    {"id": "small", "name": "Small", "params": "~244M", "ram": "~2 GB", "speed": 3, "accuracy": 3},
    {"id": "medium", "name": "Medium", "params": "~769M", "ram": "~5 GB", "speed": 2, "accuracy": 4},
    {"id": "large-v2", "name": "Large v2", "params": "~1.5B", "ram": "~10 GB", "speed": 1, "accuracy": 5},
    {"id": "large-v3", "name": "Large v3", "params": "~1.5B", "ram": "~10 GB", "speed": 1, "accuracy": 5},
]

router = APIRouter()


@router.get("/api/version")
def get_version() -> dict:
    try:
        root = Path(__file__).resolve().parents[3]
        candidate = root / "VERSION"
        ver = candidate.read_text(encoding="utf-8").strip() if candidate.exists() else ""
    except Exception:
        ver = ""
    return {"version": ver}


@router.get("/api/models")
def get_models() -> list:
    return MODELS_META


@router.get("/api/latest-release")
def api_latest_release(request: Request) -> dict:
    info = getattr(request.app.state, "latest_release", {}) or {}
    update = getattr(request.app.state, "update_available", False)
    local = getattr(request.app.state, "local_version", "") or ""
    if not str(local).strip():
        update = False
    return {"latest": info, "update_available": bool(update), "local_version": local}

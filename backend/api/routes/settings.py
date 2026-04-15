"""Settings endpoints."""

from fastapi import APIRouter, Form

from settings import _load_settings, _save_settings

router = APIRouter()


@router.get("/api/settings")
def get_settings() -> dict:
    settings = _load_settings()
    return {"hf_token": settings.get("hf_token", "")}


@router.post("/api/settings")
async def save_settings(hf_token: str = Form("")) -> dict:
    settings = _load_settings()
    settings["hf_token"] = hf_token
    _save_settings(settings)
    return {"ok": True}

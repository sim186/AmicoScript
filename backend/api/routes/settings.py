"""Settings endpoints."""

from fastapi import APIRouter, Form

from settings import _load_settings, _save_settings, _get_whisper_settings, _save_whisper_settings

router = APIRouter()


@router.get("/api/settings")
def get_settings() -> dict:
    import state
    settings = _load_settings()
    ws = _get_whisper_settings()
    return {
        "hf_token": settings.get("hf_token", ""),
        "exit_token": getattr(state, "exit_token", ""),
        "whisper_model": ws["whisper_model"],
        "whisper_device": ws["whisper_device"],
        "whisper_compute": ws["whisper_compute"],
    }


@router.post("/api/settings")
async def save_settings(
    hf_token: str = Form(""),
    whisper_model: str = Form(""),
    whisper_device: str = Form(""),
    whisper_compute: str = Form(""),
) -> dict:
    settings = _load_settings()
    settings["hf_token"] = hf_token
    _save_settings(settings)
    if whisper_model:
        _save_whisper_settings(whisper_model, whisper_device or "auto", whisper_compute or "float16")
    return {"ok": True}

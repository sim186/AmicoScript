"""LLM settings and utility endpoints."""

from fastapi import APIRouter, Form, HTTPException
from starlette.concurrency import run_in_threadpool

from settings import _get_llm_settings, _save_llm_settings

router = APIRouter()


@router.get("/api/llm/settings")
def get_llm_settings() -> dict:
    return _get_llm_settings()


@router.post("/api/llm/settings")
async def save_llm_settings(
    llm_base_url: str = Form("http://localhost:11434"),
    llm_model_name: str = Form(""),
    llm_api_key: str = Form(""),
) -> dict:
    _save_llm_settings(llm_base_url, llm_model_name, llm_api_key)
    return {"ok": True}


@router.post("/api/llm/test-connection")
async def test_llm_connection() -> dict:
    import requests as _req

    cfg = _get_llm_settings()
    base_url = cfg["llm_base_url"].rstrip("/")
    model = cfg["llm_model_name"]
    api_key = cfg["llm_api_key"]

    if not model:
        return {"ok": False, "error": "No model configured. Set it in AI Analysis settings.", "model_info": None}

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Say 'ok' in one word."}],
        "stream": False,
        "max_tokens": 5,
    }
    try:
        resp = await run_in_threadpool(
            lambda: _req.post(
                f"{base_url}/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=8,
            )
        )
        resp.raise_for_status()
        data = resp.json()
        reply = data["choices"][0]["message"]["content"]
        return {"ok": True, "error": None, "model_info": f"Model '{model}' responded: {reply[:80]}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "model_info": None}


@router.get("/api/llm/models")
async def list_llm_models() -> list:
    import requests as _req

    cfg = _get_llm_settings()
    base_url = cfg["llm_base_url"].rstrip("/")
    api_key = cfg["llm_api_key"]
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        resp = await run_in_threadpool(
            lambda: _req.get(f"{base_url}/v1/models", headers=headers, timeout=5)
        )
        resp.raise_for_status()
        return [{"id": m["id"], "name": m.get("name", m["id"])} for m in resp.json().get("data", [])]
    except Exception:
        return []


@router.post("/api/llm/models/pull")
async def pull_llm_model(body: dict) -> dict:
    import requests as _req

    model_name = (body.get("model_name") or "").strip()
    if not model_name:
        raise HTTPException(400, "model_name required")

    cfg = _get_llm_settings()
    base_url = cfg["llm_base_url"].rstrip("/")
    try:
        await run_in_threadpool(
            lambda: _req.post(
                f"{base_url}/api/pull",
                json={"model": model_name, "stream": False},
                timeout=600,
            )
        )
        return {"ok": True, "model": model_name}
    except Exception as exc:
        raise HTTPException(502, f"Pull failed: {exc}") from exc

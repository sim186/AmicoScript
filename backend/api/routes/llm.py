"""LLM settings, provider presets, server detection and connection testing."""

import requests as _req
from fastapi import APIRouter, Form, HTTPException
from starlette.concurrency import run_in_threadpool

from llm_providers import (
    DEFAULT_PROVIDER,
    build_headers,
    chat_url,
    container_host_alias,
    detection_targets,
    explain_connection_error,
    explain_http_status,
    get_provider,
    identify_provider,
    in_container,
    missing_api_key,
    models_url,
    normalize_base_url,
    normalize_models,
    provider_catalog,
)
from settings import _get_llm_settings, _save_llm_settings

router = APIRouter()

# Posted back by the UI for a masked key the user did not edit.
_UNCHANGED = "__unchanged__"


@router.get("/api/llm/providers")
def list_providers() -> dict:
    """Presets for the setup UI, plus what the runtime looks like from here."""
    return {
        "providers": provider_catalog(),
        "default": DEFAULT_PROVIDER,
        "in_container": in_container(),
        "container_host": container_host_alias() if in_container() else "",
    }


@router.get("/api/llm/settings")
def get_llm_settings() -> dict:
    """LLM config for the UI. The API key is reported as set/unset, never echoed."""
    cfg = _get_llm_settings()
    api_key = cfg.pop("llm_api_key", "")
    cfg["llm_api_key_set"] = bool(api_key)
    provider = get_provider(cfg.get("llm_provider", ""))
    cfg["llm_provider"] = provider.id
    cfg["provider_is_cloud"] = provider.cloud
    cfg["api_key_requirement"] = provider.api_key
    return cfg


@router.post("/api/llm/settings")
async def save_llm_settings(
    llm_base_url: str = Form(""),
    llm_model_name: str = Form(""),
    llm_api_key: str = Form(_UNCHANGED),
    llm_provider: str = Form(""),
    llm_context_tokens: str = Form(""),
    llm_max_output_tokens: str = Form(""),
    llm_allow_cloud: str = Form(""),
    llm_embedding_model: str = Form(_UNCHANGED),
) -> dict:
    """Persist LLM config.

    ``llm_api_key`` defaults to the "unchanged" sentinel so a UI that never
    received the real key cannot blank it out by saving the form.

    The base URL is normalized on the way in — a trailing ``/v1``, a pasted
    endpoint path, or ``localhost`` while running in a container are all fixed
    here — and the change is reported back so the UI can tell the user.
    """
    def _optional_int(value: str) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    current = _get_llm_settings()
    provider = get_provider(llm_provider or current.get("llm_provider", ""))
    switching = bool(llm_provider) and provider.id != current.get("llm_provider")

    # Switching to a provider with no preset URL and typing none would silently
    # keep the previous provider's address — confusing enough to refuse.
    if switching and not provider.base_url and not llm_base_url.strip():
        raise HTTPException(
            400, f"{provider.label} has no default address — enter the server's base URL."
        )

    raw_url = llm_base_url.strip() or provider.base_url or current["llm_base_url"]
    base_url, note = normalize_base_url(raw_url)
    if not base_url:
        raise HTTPException(400, "A base URL is required for this provider.")

    api_key = current["llm_api_key"] if llm_api_key == _UNCHANGED else llm_api_key
    allow_cloud = (
        current.get("llm_allow_cloud", False)
        if not llm_allow_cloud
        else llm_allow_cloud.strip().lower() in {"1", "true", "yes", "on"}
    )

    _save_llm_settings(
        base_url,
        llm_model_name,
        api_key,
        context_tokens=_optional_int(llm_context_tokens),
        max_output_tokens=_optional_int(llm_max_output_tokens),
        provider=provider.id,
        allow_cloud=allow_cloud,
        embedding_model=(
            None if llm_embedding_model == _UNCHANGED else llm_embedding_model
        ),
    )
    return {
        "ok": True,
        "llm_base_url": base_url,
        "normalized": base_url != raw_url.strip(),
        "note": note,
        "provider_is_cloud": provider.cloud,
        "needs_api_key": missing_api_key(provider.id, api_key),
    }


@router.post("/api/llm/test-connection")
async def test_llm_connection() -> dict:
    """Try a real completion and report something the user can act on."""
    cfg = _get_llm_settings()
    provider_id = cfg.get("llm_provider", DEFAULT_PROVIDER)
    provider = get_provider(provider_id)
    base_url = cfg["llm_base_url"]
    model = cfg["llm_model_name"]
    api_key = cfg["llm_api_key"]

    if not base_url:
        return {"ok": False, "error": "No server address configured.", "model_info": None}
    if missing_api_key(provider_id, api_key):
        return {
            "ok": False,
            "error": (
                f"{provider.label} requires an API key"
                + (f" ({provider.key_hint})" if provider.key_hint else "")
                + "."
            ),
            "model_info": None,
        }
    if not model:
        return {
            "ok": False,
            "error": "No model selected. Pick one from the model list.",
            "model_info": None,
        }
    if provider.cloud and not cfg.get("llm_allow_cloud"):
        return {
            "ok": False,
            "error": (
                f"{provider.label} is a hosted service, so your transcripts would "
                "leave this machine. Tick the box confirming that before using it."
            ),
            "model_info": None,
        }

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Say 'ok' in one word."}],
        "stream": False,
        "max_tokens": 5,
    }
    try:
        resp = await run_in_threadpool(
            lambda: _req.post(
                chat_url(base_url),
                json=payload,
                headers=build_headers(provider_id, api_key),
                timeout=15,
            )
        )
    except _req.RequestException as exc:
        return {
            "ok": False,
            "error": explain_connection_error(provider_id, base_url, exc),
            "model_info": None,
        }

    if resp.status_code >= 400:
        return {
            "ok": False,
            "error": explain_http_status(provider_id, resp.status_code, resp.text),
            "model_info": None,
        }

    try:
        reply = resp.json()["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError):
        return {
            "ok": False,
            "error": (
                "The server answered, but not in the OpenAI chat format "
                "AmicoScript expects. Check that the address points at an "
                "OpenAI-compatible endpoint."
            ),
            "model_info": None,
        }

    return {
        "ok": True,
        "error": None,
        "provider": provider.id,
        "model_info": f"{provider.label} · '{model}' replied: {reply.strip()[:80]}",
    }


@router.get("/api/llm/detect")
async def detect_llm_servers() -> dict:
    """Scan the well-known local ports for a server that is already running.

    Saves the user from knowing that LM Studio is 1234 and Unsloth is 8888, and
    from guessing the right host when AmicoScript runs in a container.
    """
    targets = detection_targets()
    found = await run_in_threadpool(_probe_all, targets)
    return {
        "servers": found,
        "scanned": targets,
        "in_container": in_container(),
        "container_host": container_host_alias() if in_container() else "",
    }


def _probe_all(targets: list[str]) -> list[dict]:
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(targets)))) as pool:
        results = list(pool.map(_probe_one, targets))
    return [r for r in results if r]


def _probe_one(base_url: str) -> dict | None:
    """One GET /v1/models with a short timeout. Silence means nothing is there."""
    try:
        resp = _req.get(models_url(base_url), timeout=1.5)
    except _req.RequestException:
        return None

    # A 401 still proves something is listening — it just wants a key, which is
    # exactly the situation worth telling the user about (Unsloth always does).
    if resp.status_code in (401, 403):
        provider_id = identify_provider(base_url, None, dict(resp.headers))
        return {
            "base_url": base_url,
            "provider": provider_id,
            "label": get_provider(provider_id).label,
            "models": [],
            "model_count": 0,
            "needs_api_key": True,
        }
    if resp.status_code >= 400:
        return None

    try:
        payload = resp.json()
    except ValueError:
        return None

    provider_id = identify_provider(base_url, payload, dict(resp.headers))
    models = normalize_models(payload)
    return {
        "base_url": base_url,
        "provider": provider_id,
        "label": get_provider(provider_id).label,
        "models": models[:100],
        "model_count": len(models),
        "needs_api_key": False,
    }


@router.get("/api/llm/models")
async def list_llm_models(base_url: str = "", provider: str = "") -> list:
    """Models offered by the configured server, or by *base_url* if given.

    Passing base_url lets the setup UI preview a server before saving it.
    """
    cfg = _get_llm_settings()
    provider_id = provider or cfg.get("llm_provider", DEFAULT_PROVIDER)
    target = base_url or cfg["llm_base_url"]
    if not target:
        return []

    try:
        resp = await run_in_threadpool(
            lambda: _req.get(
                models_url(target),
                headers=build_headers(provider_id, cfg["llm_api_key"]),
                timeout=10,
            )
        )
        resp.raise_for_status()
        return normalize_models(resp.json())
    except (_req.RequestException, ValueError):
        return []


@router.post("/api/llm/models/pull")
async def pull_llm_model(body: dict) -> dict:
    """Download a model. Only Ollama exposes this; others manage models in-app."""
    model_name = (body.get("model_name") or "").strip()
    if not model_name:
        raise HTTPException(400, "model_name required")

    cfg = _get_llm_settings()
    provider = get_provider(cfg.get("llm_provider", DEFAULT_PROVIDER))
    if not provider.supports_pull:
        raise HTTPException(
            400,
            f"{provider.label} does not support downloading models over the API — "
            "add the model in its own interface, then refresh the list here.",
        )

    base_url = normalize_base_url(cfg["llm_base_url"])[0]
    try:
        resp = await run_in_threadpool(
            lambda: _req.post(
                f"{base_url}/api/pull",
                json={"model": model_name, "stream": False},
                timeout=600,
            )
        )
        resp.raise_for_status()
        return {"ok": True, "model": model_name}
    except _req.RequestException as exc:
        raise HTTPException(
            502, explain_connection_error(provider.id, base_url, exc)
        ) from exc

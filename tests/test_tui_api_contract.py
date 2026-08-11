"""The TUI client must stay in step with the API it talks to.

The regression that motivated most of this: the server stopped returning secrets
and started reporting them as set/unset. The TUI kept reading the old fields, so
its settings form showed an empty token and saving it wrote that emptiness back —
silently erasing a stored Hugging Face token.
"""
import httpx
import pytest

from tui.api import UNCHANGED, ApiClient
from tui.errors import explain


@pytest.fixture()
def recorded():
    """An ApiClient whose transport records requests and replays canned replies."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        route = request.url.path
        if route == "/api/settings" and request.method == "GET":
            return httpx.Response(200, json={
                "hf_token_set": True,
                "hf_token_preview": "••••••••abcd",
                "whisper_model": "small",
                "auto_summarize_meetings": True,
            })
        if route == "/api/llm/settings" and request.method == "GET":
            return httpx.Response(200, json={
                "llm_provider": "lmstudio",
                "llm_base_url": "http://localhost:1234",
                "llm_model_name": "qwen",
                "llm_api_key_set": True,
                "api_key_requirement": "none",
                "provider_is_cloud": False,
                "llm_allow_cloud": False,
                "llm_context_tokens": 8192,
                "llm_max_output_tokens": 1024,
            })
        if route == "/api/llm/providers":
            return httpx.Response(200, json={
                "providers": [{"id": "ollama", "label": "Ollama", "base_url": "x",
                               "api_key": "none", "cloud": False}],
                "in_container": False,
                "container_host": "",
            })
        if route == "/api/llm/detect":
            return httpx.Response(200, json={
                "servers": [{"base_url": "http://localhost:1234", "provider": "lmstudio",
                             "label": "LM Studio", "models": [{"id": "qwen"}],
                             "model_count": 1, "needs_api_key": False}],
                "scanned": ["http://localhost:1234"],
            })
        if route.endswith("/retry"):
            return httpx.Response(200, json={"ok": True, "job_id": "job-1"})
        if route == "/api/library/import":
            return httpx.Response(200, json={"ok": True, "imported": {"recordings": 2, "audio": 2}})
        if route == "/api/library/export":
            return httpx.Response(200, content=b"PK\x03\x04bundle")
        return httpx.Response(200, json={"ok": True})

    client = ApiClient("http://test")
    client.client = httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(handler))
    client.calls = calls
    return client


def _form(request: httpx.Request) -> dict:
    from urllib.parse import parse_qsl
    body = request.content.decode()
    return dict(parse_qsl(body))


# --- the regression ---------------------------------------------------------


@pytest.mark.anyio
async def test_settings_expose_the_masked_token_not_an_empty_string(recorded):
    settings = await recorded.settings()
    assert settings["hf_token_set"] is True
    assert settings["hf_token_preview"].endswith("abcd")
    assert "hf_token" not in settings


@pytest.mark.anyio
async def test_saving_with_the_sentinel_leaves_the_token_alone(recorded):
    await recorded.save_settings(hf_token=UNCHANGED, whisper_model="small")
    posted = _form(recorded.calls[-1])
    assert posted["hf_token"] == UNCHANGED


@pytest.mark.anyio
async def test_omitting_the_token_does_not_send_it_at_all(recorded):
    await recorded.save_settings(whisper_model="medium")
    assert "hf_token" not in _form(recorded.calls[-1])


@pytest.mark.anyio
async def test_llm_settings_report_the_key_as_set_not_as_a_value(recorded):
    cfg = await recorded.llm_settings()
    assert cfg["api_key_set"] is True
    assert "api_key" not in cfg
    assert cfg["provider"] == "lmstudio"
    assert cfg["context_tokens"] == 8192


@pytest.mark.anyio
async def test_saving_llm_settings_can_preserve_the_key(recorded):
    await recorded.save_llm_settings(provider="lmstudio", base_url="http://x", api_key=UNCHANGED)
    posted = _form(recorded.calls[-1])
    assert posted["llm_api_key"] == UNCHANGED
    assert posted["llm_provider"] == "lmstudio"


# --- new capabilities -------------------------------------------------------


@pytest.mark.anyio
async def test_the_client_can_retry_a_recording(recorded):
    result = await recorded.retry_recording("rec-1")
    assert result["job_id"] == "job-1"
    assert recorded.calls[-1].url.path == "/api/recordings/rec-1/retry"
    assert recorded.calls[-1].method == "POST"


@pytest.mark.anyio
async def test_the_client_can_export_the_library(recorded, tmp_path):
    dest = tmp_path / "bundle.zip"
    await recorded.export_library(dest, include_audio=False)
    assert dest.read_bytes().startswith(b"PK")
    assert recorded.calls[-1].url.params["include_audio"] == "false"


@pytest.mark.anyio
async def test_the_client_can_import_a_library(recorded, tmp_path):
    bundle = tmp_path / "bundle.zip"
    bundle.write_bytes(b"PK\x03\x04")
    result = await recorded.import_library(bundle, mode="overwrite")
    assert result["imported"]["recordings"] == 2


@pytest.mark.anyio
async def test_the_client_lists_providers_and_detects_servers(recorded):
    assert {p["id"] for p in (await recorded.llm_providers())["providers"]} == {"ollama"}
    found = await recorded.llm_detect()
    assert found["servers"][0]["label"] == "LM Studio"


@pytest.mark.anyio
async def test_pull_sends_the_field_name_the_server_expects(recorded):
    """The server reads model_name; the TUI used to send 'name'."""
    import json

    await recorded.llm_pull_model("qwen3")
    assert json.loads(recorded.calls[-1].content) == {"model_name": "qwen3"}


@pytest.fixture()
def anyio_backend():
    return "asyncio"


# --- error messages ---------------------------------------------------------


def _status_error(status: int, body: dict | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://test/api/library")
    response = httpx.Response(status, json=body or {}, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


def test_a_401_explains_how_to_authenticate():
    message = explain(_status_error(401))
    assert "AMICOSCRIPT_API_TOKEN" in message


def test_a_410_points_at_the_library():
    assert "library" in explain(_status_error(410))


def test_a_409_uses_the_server_message():
    message = explain(_status_error(409, {"detail": "A tag called 'x' already exists."}))
    assert "already exists" in message


def test_a_connection_error_is_plain_english():
    message = explain(httpx.ConnectError("nope"))
    assert "is it running" in message


def test_a_prefix_is_kept():
    assert explain(httpx.ConnectError("nope"), "retry failed").startswith("retry failed:")

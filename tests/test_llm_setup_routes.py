"""Route tests for LLM provider setup, detection and connection testing."""
import pytest

pytestmark = pytest.mark.usefixtures("no_auth", "clean_settings")


@pytest.fixture(autouse=True)
def _outside_container(monkeypatch):
    monkeypatch.setenv("AMICOSCRIPT_IN_CONTAINER", "0")


def _save(client, **fields):
    payload = {"llm_base_url": "http://localhost:11434", "llm_model_name": "llama3"}
    payload.update(fields)
    return client.post("/api/llm/settings", data=payload)


# --- provider catalog -------------------------------------------------------


def test_providers_are_listed_for_the_ui(client):
    body = client.get("/api/llm/providers").json()
    ids = {p["id"] for p in body["providers"]}
    assert {"ollama", "lmstudio", "unsloth", "openrouter", "custom"} <= ids
    assert body["default"] == "ollama"
    assert body["in_container"] is False


def test_the_catalog_carries_setup_hints(client):
    providers = {p["id"]: p for p in client.get("/api/llm/providers").json()["providers"]}
    assert providers["lmstudio"]["base_url"] == "http://localhost:1234"
    assert providers["unsloth"]["api_key"] == "required"
    assert providers["openrouter"]["cloud"] is True
    assert providers["ollama"]["supports_pull"] is True
    assert providers["lmstudio"]["docs_url"].startswith("https://")


# --- saving settings --------------------------------------------------------


def test_saving_normalizes_a_pasted_v1_url(client):
    """LM Studio shows a /v1 URL; pasting it used to produce /v1/v1 and a 404."""
    resp = _save(client, llm_provider="lmstudio", llm_base_url="http://localhost:1234/v1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["llm_base_url"] == "http://localhost:1234"
    assert body["normalized"] is True
    assert "/v1" in body["note"]
    assert client.get("/api/llm/settings").json()["llm_base_url"] == "http://localhost:1234"


def test_saving_keeps_a_clean_url_unchanged(client):
    body = _save(client, llm_base_url="http://localhost:11434").json()
    assert body["normalized"] is False
    assert body["note"] == ""


def test_choosing_a_provider_without_a_url_uses_its_default(client):
    resp = client.post(
        "/api/llm/settings",
        data={"llm_provider": "lmstudio", "llm_model_name": "qwen", "llm_base_url": ""},
    )
    assert resp.json()["llm_base_url"] == "http://localhost:1234"


def test_the_provider_round_trips(client):
    _save(client, llm_provider="unsloth", llm_base_url="http://localhost:8888")
    cfg = client.get("/api/llm/settings").json()
    assert cfg["llm_provider"] == "unsloth"
    assert cfg["api_key_requirement"] == "required"
    assert cfg["provider_is_cloud"] is False


def test_saving_reports_a_missing_required_key(client):
    body = _save(client, llm_provider="unsloth", llm_api_key="").json()
    assert body["needs_api_key"] is True

    body = _save(client, llm_provider="unsloth", llm_api_key="sk-unsloth-abc").json()
    assert body["needs_api_key"] is False


def test_an_unknown_provider_falls_back_rather_than_erroring(client):
    resp = _save(client, llm_provider="not-a-provider")
    assert resp.status_code == 200
    assert client.get("/api/llm/settings").json()["llm_provider"] == "ollama"


def test_a_url_is_required_when_the_provider_has_no_default(client):
    resp = client.post(
        "/api/llm/settings",
        data={"llm_provider": "custom", "llm_base_url": "", "llm_model_name": "m"},
    )
    assert resp.status_code == 400


# --- cloud consent ----------------------------------------------------------


def test_cloud_consent_defaults_to_off(client):
    _save(client, llm_provider="openrouter", llm_api_key="sk-or-x")
    assert client.get("/api/llm/settings").json()["llm_allow_cloud"] is False


def test_cloud_consent_can_be_given_and_withdrawn(client):
    _save(client, llm_provider="openrouter", llm_api_key="sk-or-x", llm_allow_cloud="true")
    assert client.get("/api/llm/settings").json()["llm_allow_cloud"] is True

    _save(client, llm_provider="openrouter", llm_allow_cloud="false")
    assert client.get("/api/llm/settings").json()["llm_allow_cloud"] is False


def test_analysis_is_refused_for_a_cloud_provider_without_consent(
    client, make_recording, sample_segments
):
    rec_id = make_recording(segments=sample_segments)
    _save(client, llm_provider="openrouter", llm_model_name="x/y", llm_api_key="sk-or-x")

    resp = client.post(
        f"/api/recordings/{rec_id}/analyses", data={"analysis_type": "summary"}
    )
    assert resp.status_code == 400
    assert "hosted service" in resp.json()["detail"]


def test_analysis_is_allowed_once_consent_is_given(
    client, monkeypatch, make_recording, sample_segments
):
    from core import analysis

    monkeypatch.setattr(analysis, "run_completion", lambda *a, **k: ("SUMMARY", False))
    rec_id = make_recording(segments=sample_segments)
    _save(
        client,
        llm_provider="openrouter",
        llm_model_name="x/y",
        llm_api_key="sk-or-x",
        llm_allow_cloud="true",
    )

    resp = client.post(
        f"/api/recordings/{rec_id}/analyses", data={"analysis_type": "summary"}
    )
    assert resp.status_code == 200


def test_the_connection_test_refuses_a_cloud_provider_without_consent(client):
    _save(client, llm_provider="openrouter", llm_model_name="x/y", llm_api_key="sk-or-x")
    body = client.post("/api/llm/test-connection").json()
    assert body["ok"] is False
    assert "leave this machine" in body["error"]


# --- connection testing -----------------------------------------------------


def test_the_connection_test_asks_for_a_required_key_before_dialling(client):
    _save(client, llm_provider="unsloth", llm_base_url="http://localhost:8888", llm_api_key="")
    body = client.post("/api/llm/test-connection").json()
    assert body["ok"] is False
    assert "requires an API key" in body["error"]
    assert "sk-unsloth-" in body["error"]


def test_the_connection_test_asks_for_a_model(client):
    _save(client, llm_model_name="")
    body = client.post("/api/llm/test-connection").json()
    assert body["ok"] is False
    assert "No model selected" in body["error"]


def test_a_refused_connection_names_the_tool(client, monkeypatch):
    import requests

    from api.routes import llm as llm_routes

    def refuse(*args, **kwargs):
        raise requests.ConnectionError("Connection refused")

    monkeypatch.setattr(llm_routes._req, "post", refuse)
    _save(client, llm_provider="lmstudio", llm_base_url="http://localhost:1234")

    body = client.post("/api/llm/test-connection").json()
    assert body["ok"] is False
    assert "LM Studio" in body["error"]


def test_a_404_suggests_checking_the_base_url(client, monkeypatch):
    from api.routes import llm as llm_routes

    class _Resp:
        status_code = 404
        text = "not found"

    monkeypatch.setattr(llm_routes._req, "post", lambda *a, **k: _Resp())
    _save(client)

    body = client.post("/api/llm/test-connection").json()
    assert body["ok"] is False
    assert "/v1" in body["error"]


def test_a_successful_test_names_the_provider_and_model(client, monkeypatch):
    from api.routes import llm as llm_routes

    class _Resp:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(llm_routes._req, "post", lambda *a, **k: _Resp())
    _save(client, llm_provider="lmstudio", llm_base_url="http://localhost:1234", llm_model_name="qwen")

    body = client.post("/api/llm/test-connection").json()
    assert body["ok"] is True
    assert "LM Studio" in body["model_info"]
    assert "qwen" in body["model_info"]


def test_a_non_openai_response_is_reported_clearly(client, monkeypatch):
    from api.routes import llm as llm_routes

    class _Resp:
        status_code = 200
        text = "<html>hello</html>"

        @staticmethod
        def json():
            return {"unexpected": True}

    monkeypatch.setattr(llm_routes._req, "post", lambda *a, **k: _Resp())
    _save(client)

    body = client.post("/api/llm/test-connection").json()
    assert body["ok"] is False
    assert "OpenAI chat format" in body["error"]


# --- detection --------------------------------------------------------------


def test_detection_reports_a_server_that_answers(client, monkeypatch):
    from api.routes import llm as llm_routes

    class _Resp:
        status_code = 200
        headers = {}

        @staticmethod
        def json():
            return {"data": [{"id": "qwen3"}, {"id": "llama3"}]}

    def fake_get(url, **kwargs):
        if "1234" in url:
            return _Resp()
        raise __import__("requests").ConnectionError("refused")

    monkeypatch.setattr(llm_routes._req, "get", fake_get)

    body = client.get("/api/llm/detect").json()
    assert len(body["servers"]) == 1
    server = body["servers"][0]
    assert server["base_url"] == "http://localhost:1234"
    assert server["provider"] == "lmstudio"
    assert server["label"] == "LM Studio"
    assert [m["id"] for m in server["models"]] == ["llama3", "qwen3"]
    assert server["needs_api_key"] is False


def test_detection_reports_a_server_that_wants_a_key(client, monkeypatch):
    from api.routes import llm as llm_routes

    class _Resp:
        status_code = 401
        headers = {}

    def fake_get(url, **kwargs):
        if "8888" in url:
            return _Resp()
        raise __import__("requests").ConnectionError("refused")

    monkeypatch.setattr(llm_routes._req, "get", fake_get)

    servers = client.get("/api/llm/detect").json()["servers"]
    assert len(servers) == 1
    assert servers[0]["provider"] == "unsloth"
    assert servers[0]["needs_api_key"] is True


def test_detection_finds_nothing_when_nothing_runs(client, monkeypatch):
    import requests

    from api.routes import llm as llm_routes

    monkeypatch.setattr(
        llm_routes._req, "get",
        lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("refused")),
    )
    body = client.get("/api/llm/detect").json()
    assert body["servers"] == []
    assert body["scanned"], "the scan should still report which addresses it tried"


# --- model listing and pulling ----------------------------------------------


def test_models_can_be_previewed_for_an_unsaved_url(client, monkeypatch):
    from api.routes import llm as llm_routes

    seen = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"data": [{"id": "preview-model"}]}

    def fake_get(url, **kwargs):
        seen["url"] = url
        return _Resp()

    monkeypatch.setattr(llm_routes._req, "get", fake_get)
    models = client.get("/api/llm/models", params={"base_url": "http://localhost:1234/v1"}).json()

    assert [m["id"] for m in models] == ["preview-model"]
    assert seen["url"] == "http://localhost:1234/v1/models"


def test_pulling_is_refused_for_providers_that_cannot(client):
    _save(client, llm_provider="lmstudio", llm_base_url="http://localhost:1234")
    resp = client.post("/api/llm/models/pull", json={"model_name": "qwen3"})
    assert resp.status_code == 400
    assert "LM Studio" in resp.json()["detail"]


def test_pulling_requires_a_model_name(client):
    assert client.post("/api/llm/models/pull", json={}).status_code == 400

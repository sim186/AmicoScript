"""Tests for provider presets, URL normalization and server detection.

The URL cases here are the ones that actually cost people an evening: pasting
the address LM Studio shows you (which ends in /v1), or pointing a container at
localhost and getting a connection refused with no explanation.
"""
import pytest

import llm_providers as lp

pytestmark = pytest.mark.usefixtures("no_auth")


@pytest.fixture()
def not_in_container(monkeypatch):
    monkeypatch.setenv("AMICOSCRIPT_IN_CONTAINER", "0")


@pytest.fixture()
def in_container(monkeypatch):
    monkeypatch.setenv("AMICOSCRIPT_IN_CONTAINER", "1")


# --- registry ---------------------------------------------------------------


def test_the_tools_people_actually_run_are_present():
    ids = {p.id for p in lp.PROVIDERS}
    assert {"ollama", "lmstudio", "unsloth", "llamacpp", "vllm", "openrouter", "custom"} <= ids


def test_every_provider_has_the_fields_the_ui_needs():
    for entry in lp.provider_catalog():
        assert entry["id"] and entry["label"]
        assert entry["api_key"] in {"none", "optional", "required"}
        assert isinstance(entry["cloud"], bool)


def test_only_hosted_providers_are_marked_cloud():
    cloud = {p.id for p in lp.PROVIDERS if p.cloud}
    assert cloud == {"openrouter"}


def test_unsloth_requires_a_key_and_says_what_it_looks_like():
    unsloth = lp.get_provider("unsloth")
    assert unsloth.api_key == "required"
    assert "sk-unsloth-" in unsloth.key_hint
    assert lp.missing_api_key("unsloth", "")
    assert not lp.missing_api_key("unsloth", "sk-unsloth-abc")


def test_lm_studio_needs_no_key():
    assert lp.get_provider("lmstudio").api_key == "none"
    assert not lp.missing_api_key("lmstudio", "")


def test_only_ollama_advertises_model_pulling():
    assert {p.id for p in lp.PROVIDERS if p.supports_pull} == {"ollama"}


def test_an_unknown_provider_id_falls_back_to_the_default():
    assert lp.get_provider("nonsense").id == lp.DEFAULT_PROVIDER
    assert lp.get_provider("").id == lp.DEFAULT_PROVIDER


# --- URL normalization ------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # What each tool actually displays to the user.
        ("http://localhost:1234/v1", "http://localhost:1234"),
        ("http://localhost:11434", "http://localhost:11434"),
        ("http://127.0.0.1:8888/v1/", "http://127.0.0.1:8888"),
        ("https://openrouter.ai/api/v1", "https://openrouter.ai/api"),
        # Whole endpoints, pasted from a curl example.
        ("http://localhost:1234/v1/chat/completions", "http://localhost:1234"),
        ("http://localhost:8080/v1/completions", "http://localhost:8080"),
        # Sloppy but obvious input.
        ("localhost:1234", "http://localhost:1234"),
        ("  http://localhost:11434/  ", "http://localhost:11434"),
        ("", ""),
    ],
)
def test_base_urls_are_normalized(not_in_container, raw, expected):
    assert lp.normalize_base_url(raw)[0] == expected


def test_the_v1_suffix_is_explained_not_silently_removed(not_in_container):
    _, note = lp.normalize_base_url("http://localhost:1234/v1")
    assert "/v1" in note


def test_request_urls_never_double_the_v1(not_in_container):
    """The 404 that made a working server look broken."""
    for raw in ("http://localhost:1234", "http://localhost:1234/v1",
                "http://localhost:1234/v1/chat/completions"):
        assert lp.chat_url(raw) == "http://localhost:1234/v1/chat/completions"
        assert lp.models_url(raw) == "http://localhost:1234/v1/models"


def test_a_port_is_preserved(not_in_container):
    assert lp.normalize_base_url("http://192.168.1.50:11434")[0] == "http://192.168.1.50:11434"


def test_https_is_preserved(not_in_container):
    assert lp.normalize_base_url("https://llm.example.com/v1")[0] == "https://llm.example.com"


# --- container awareness ----------------------------------------------------


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "0.0.0.0"])
def test_localhost_is_rewritten_inside_a_container(in_container, host):
    url, note = lp.normalize_base_url(f"http://{host}:11434")
    assert url == "http://host.docker.internal:11434"
    assert "container" in note


def test_a_real_host_is_left_alone_inside_a_container(in_container):
    url, note = lp.normalize_base_url("http://192.168.1.50:11434")
    assert url == "http://192.168.1.50:11434"
    assert note == ""


def test_no_rewriting_outside_a_container(not_in_container):
    assert lp.normalize_base_url("http://localhost:11434")[0] == "http://localhost:11434"


def test_rewriting_can_be_suppressed(in_container):
    url, _ = lp.normalize_base_url("http://localhost:11434", rewrite_for_container=False)
    assert url == "http://localhost:11434"


def test_the_container_host_alias_is_configurable(in_container, monkeypatch):
    monkeypatch.setenv("AMICOSCRIPT_DOCKER_HOST", "gateway.internal")
    assert lp.normalize_base_url("http://localhost:1234")[0] == "http://gateway.internal:1234"


def test_detection_targets_follow_the_runtime(not_in_container):
    """Outside a container we probe localhost; inside, the host alias."""
    local = lp.detection_targets()
    assert all(t.startswith("http://localhost:") for t in local)
    assert len(set(local)) == len(local), "ports should be probed once each"


def test_detection_targets_point_at_the_host_from_inside(in_container):
    assert all("host.docker.internal" in t for t in lp.detection_targets())


def test_detection_covers_the_well_known_ports(not_in_container):
    ports = {int(t.rsplit(":", 1)[1]) for t in lp.detection_targets()}
    assert {11434, 1234, 8888, 8080, 8000, 1337} <= ports


# --- headers ----------------------------------------------------------------


def test_a_key_becomes_a_bearer_token():
    assert lp.build_headers("lmstudio", "abc")["Authorization"] == "Bearer abc"


def test_no_key_means_no_authorization_header():
    assert "Authorization" not in lp.build_headers("lmstudio", "")


def test_openrouter_gets_its_attribution_headers():
    headers = lp.build_headers("openrouter", "sk-or-x")
    assert headers["HTTP-Referer"]
    assert headers["X-Title"] == "AmicoScript"


def test_local_providers_get_no_extra_headers():
    assert set(lp.build_headers("ollama", "")) == {"Content-Type"}


# --- identification ---------------------------------------------------------


def test_a_server_identifies_itself_through_its_payload():
    payload = {"data": [{"id": "llama3", "owned_by": "ollama"}]}
    assert lp.identify_provider("http://x:9999", payload, {}) == "ollama"


def test_a_server_is_identified_by_port_when_the_payload_is_generic():
    payload = {"data": [{"id": "some-model", "object": "model"}]}
    assert lp.identify_provider("http://localhost:1234", payload, {}) == "lmstudio"
    assert lp.identify_provider("http://localhost:11434", payload, {}) == "ollama"


def test_an_unrecognised_server_is_labelled_custom():
    payload = {"data": [{"id": "m"}]}
    assert lp.identify_provider("http://localhost:9999", payload, {}) == "custom"


# --- model normalization ----------------------------------------------------


def test_a_local_models_response_is_flattened():
    models = lp.normalize_models({"data": [{"id": "qwen3"}, {"id": "llama3"}]})
    assert [m["id"] for m in models] == ["llama3", "qwen3"]  # sorted
    assert all(m["name"] and "free" in m for m in models)


def test_openrouter_extras_are_carried_through():
    models = lp.normalize_models({"data": [{
        "id": "meta-llama/llama-3-8b-instruct:free",
        "name": "Llama 3 8B Instruct (free)",
        "context_length": 8192,
        "pricing": {"prompt": "0", "completion": "0"},
    }]})
    assert models[0]["context_length"] == 8192
    assert models[0]["free"] is True
    assert models[0]["name"].startswith("Llama 3")


def test_a_paid_model_is_not_marked_free():
    models = lp.normalize_models({"data": [{
        "id": "openai/gpt-4o", "pricing": {"prompt": "0.000005", "completion": "0.000015"},
    }]})
    assert models[0]["free"] is False


def test_odd_payload_shapes_do_not_raise():
    assert lp.normalize_models({}) == []
    assert lp.normalize_models([]) == []
    assert lp.normalize_models({"data": [None, 42, {"no_id": True}]}) == []
    assert lp.normalize_models({"models": ["bare-string"]})[0]["id"] == "bare-string"


# --- error messages ---------------------------------------------------------


def test_connection_refused_names_the_tool(not_in_container):
    message = lp.explain_connection_error(
        "lmstudio", "http://localhost:1234", OSError("Connection refused"),
    )
    assert "LM Studio" in message
    assert "localhost:1234" in message


def test_connection_refused_mentions_containers_when_relevant(in_container):
    message = lp.explain_connection_error(
        "ollama", "http://host.docker.internal:11434", OSError("Connection refused"),
    )
    assert "container" in message
    assert "host.docker.internal" in message


def test_a_bad_key_says_so():
    message = lp.explain_http_status("unsloth", 401, "unauthorized")
    assert "API key" in message
    assert "sk-unsloth-" in message


def test_a_404_points_at_the_base_url():
    assert "/v1" in lp.explain_http_status("lmstudio", 404, "")


def test_a_rate_limit_is_named():
    assert "rate-limiting" in lp.explain_http_status("openrouter", 429, "")

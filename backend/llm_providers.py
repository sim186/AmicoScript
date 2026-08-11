"""Known LLM backends, and the plumbing needed to talk to any of them.

Configuring an LLM used to mean typing a base URL and hoping. Three things went
wrong often enough to be worth fixing in code rather than in documentation:

1. **The /v1 suffix.** Every one of these tools shows you a base URL ending in
   ``/v1`` — LM Studio literally prints ``http://localhost:1234/v1`` — but
   AmicoScript appends ``/v1/chat/completions`` itself, so pasting what the tool
   showed you produced ``/v1/v1/chat/completions`` and a 404 that looked like
   the server was broken.
2. **Docker.** Inside a container ``localhost`` is the container, so the default
   ``http://localhost:11434`` can never reach an Ollama running on the host.
3. **Which port?** Nobody remembers that LM Studio is 1234 and Unsloth is 8888.

So: presets for the tools people actually run, a normalizer that accepts
whatever they paste, container-aware host rewriting, and a probe that finds
servers already running.

Everything here speaks the OpenAI chat-completions dialect, which all of these
expose. Where a backend has extra native abilities (Ollama's model pull) that is
declared per provider rather than assumed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse, urlunparse

# --- provider registry ------------------------------------------------------


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    base_url: str
    # 'none'      — the server rejects nothing; sending a key is harmless
    # 'optional'  — may or may not be configured (llama.cpp with --api-key)
    # 'required'  — requests fail without one
    api_key: str = "none"
    key_hint: str = ""
    # True when requests leave the machine. AmicoScript is local-first, so these
    # are opt-in and warned about rather than offered as equals.
    cloud: bool = False
    docs_url: str = ""
    # Ports to probe when scanning for a server that is already running.
    detect_ports: tuple[int, ...] = ()
    # Substrings that identify this backend in a /v1/models response or in a
    # server header, used to name what the scan found.
    fingerprints: tuple[str, ...] = ()
    supports_pull: bool = False
    notes: str = ""
    extra_headers: dict = field(default_factory=dict)


PROVIDERS: tuple[Provider, ...] = (
    Provider(
        id="ollama",
        label="Ollama",
        base_url="http://localhost:11434",
        api_key="none",
        docs_url="https://ollama.com/download",
        detect_ports=(11434,),
        fingerprints=("ollama",),
        supports_pull=True,
        notes=(
            "Runs models from the command line or in the background. To reach it "
            "from Docker it must listen beyond loopback: set OLLAMA_HOST=0.0.0.0."
        ),
    ),
    Provider(
        id="lmstudio",
        label="LM Studio",
        base_url="http://localhost:1234",
        api_key="none",
        docs_url="https://lmstudio.ai/docs/developer/openai-compat",
        detect_ports=(1234,),
        fingerprints=("lmstudio", "lm studio", "lm-studio"),
        notes=(
            "Start the server from LM Studio's Developer tab. It binds to "
            "127.0.0.1 by default; enable 'Serve on Local Network' to reach it "
            "from Docker."
        ),
    ),
    Provider(
        id="unsloth",
        label="Unsloth Studio",
        base_url="http://localhost:8888",
        api_key="required",
        key_hint="sk-unsloth-…",
        docs_url="https://unsloth.ai/docs/basics/api",
        # Documented on 8888 after setup; 8000 shows up in some configurations.
        detect_ports=(8888, 8000),
        fingerprints=("unsloth",),
        notes=(
            "Serves GGUF and safetensors models through llama-server. It always "
            "requires a key — copy it from Unsloth Studio → Settings → API, or "
            "from the console when you start it (it begins with sk-unsloth-)."
        ),
    ),
    Provider(
        id="llamacpp",
        label="llama.cpp (llama-server)",
        base_url="http://localhost:8080",
        api_key="optional",
        docs_url="https://github.com/ggml-org/llama.cpp/tree/master/tools/server",
        detect_ports=(8080,),
        fingerprints=("llama.cpp", "llama-server", "llamacpp"),
        notes="A key is only needed if you started llama-server with --api-key.",
    ),
    Provider(
        id="vllm",
        label="vLLM",
        base_url="http://localhost:8000",
        api_key="optional",
        docs_url="https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html",
        detect_ports=(8000,),
        fingerprints=("vllm",),
        notes="A key is only needed if you started vLLM with --api-key.",
    ),
    Provider(
        id="jan",
        label="Jan",
        base_url="http://localhost:1337",
        api_key="none",
        docs_url="https://jan.ai/docs/local-api",
        detect_ports=(1337,),
        fingerprints=("jan",),
        notes="Enable the local API server in Jan's settings first.",
    ),
    Provider(
        id="localai",
        label="LocalAI",
        base_url="http://localhost:8080",
        api_key="optional",
        docs_url="https://localai.io/",
        detect_ports=(8080,),
        fingerprints=("localai", "local-ai"),
    ),
    Provider(
        id="openrouter",
        label="OpenRouter",
        base_url="https://openrouter.ai/api",
        api_key="required",
        key_hint="sk-or-…",
        cloud=True,
        docs_url="https://openrouter.ai/docs/quickstart",
        notes=(
            "One key for hundreds of hosted models, including free ones. Your "
            "transcripts are sent to OpenRouter and on to the model's provider."
        ),
        # Optional attribution headers OpenRouter documents for API clients.
        extra_headers={
            "HTTP-Referer": "https://github.com/sim186/AmicoScript",
            "X-Title": "AmicoScript",
        },
    ),
    Provider(
        id="custom",
        label="Other (OpenAI-compatible)",
        base_url="",
        api_key="optional",
        docs_url="",
        notes=(
            "Anything that speaks POST /v1/chat/completions — OpenAI itself, "
            "Groq, Together, Mistral, an office gateway, or a tool not listed "
            "here. Paste its base URL and a key if it needs one."
        ),
    ),
)

PROVIDERS_BY_ID = {p.id: p for p in PROVIDERS}
DEFAULT_PROVIDER = "ollama"

# Hosts that mean "this machine". Inside a container they mean the container,
# which is the single most common reason a Docker user cannot reach their LLM.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "[::1]"}


def get_provider(provider_id: str) -> Provider:
    return PROVIDERS_BY_ID.get(provider_id or "", PROVIDERS_BY_ID[DEFAULT_PROVIDER])


def provider_catalog() -> list[dict]:
    """The registry as plain dicts, for the settings UI."""
    return [
        {
            "id": p.id,
            "label": p.label,
            "base_url": p.base_url,
            "api_key": p.api_key,
            "key_hint": p.key_hint,
            "cloud": p.cloud,
            "docs_url": p.docs_url,
            "supports_pull": p.supports_pull,
            "notes": p.notes,
        }
        for p in PROVIDERS
    ]


# --- container awareness ----------------------------------------------------


def in_container() -> bool:
    """True when this process is running inside a container.

    ``/.dockerenv`` covers Docker; the cgroup scan also catches Podman and
    containerd. AMICOSCRIPT_IN_CONTAINER overrides both for testing and for
    runtimes that hide the usual markers.
    """
    override = os.environ.get("AMICOSCRIPT_IN_CONTAINER", "").strip().lower()
    if override in {"1", "true", "yes"}:
        return True
    if override in {"0", "false", "no"}:
        return False

    if Path("/.dockerenv").exists():
        return True
    try:
        cgroup = Path("/proc/self/cgroup").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(marker in cgroup for marker in ("docker", "kubepods", "containerd", "podman"))


def container_host_alias() -> str:
    """The name a container uses for its host."""
    return os.environ.get("AMICOSCRIPT_DOCKER_HOST", "host.docker.internal")


# --- URL normalization ------------------------------------------------------


def normalize_base_url(raw: str, *, rewrite_for_container: bool = True) -> tuple[str, str]:
    """Clean up a pasted base URL. Returns (url, human-readable note).

    Accepts what the tools actually display, including a trailing ``/v1`` or a
    full ``/v1/chat/completions`` path, a bare ``localhost:1234``, and trailing
    slashes. The note explains any change, so the UI can show the user what
    happened instead of silently editing their input.
    """
    url = (raw or "").strip()
    if not url:
        return "", ""

    notes: list[str] = []

    if "://" not in url:
        url = f"http://{url}"

    parsed = urlparse(url)
    path = parsed.path.rstrip("/")

    # Strip the endpoint if the whole thing was pasted.
    for suffix in ("/chat/completions", "/completions", "/responses", "/messages"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            notes.append("removed the endpoint path")
            break

    # Strip a trailing /v1 — it is re-added when a request is built, and
    # doubling it is the classic "why do I get a 404" mistake.
    if path.endswith("/v1"):
        path = path[:-3]
        notes.append("removed the trailing /v1 (AmicoScript adds it)")

    path = path.rstrip("/")
    hostname = (parsed.hostname or "").lower()

    if rewrite_for_container and hostname in _LOCAL_HOSTS and in_container():
        alias = container_host_alias()
        netloc = alias if parsed.port is None else f"{alias}:{parsed.port}"
        if parsed.username:
            credentials = parsed.username + (f":{parsed.password}" if parsed.password else "")
            netloc = f"{credentials}@{netloc}"
        parsed = parsed._replace(netloc=netloc)
        notes.append(
            f"rewrote {hostname} to {alias}, because AmicoScript is running in a "
            "container and localhost there means the container itself"
        )

    cleaned = urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))
    return cleaned, "; ".join(notes)


def chat_url(base_url: str) -> str:
    return f"{normalize_base_url(base_url)[0]}/v1/chat/completions"


def models_url(base_url: str) -> str:
    return f"{normalize_base_url(base_url)[0]}/v1/models"


def build_headers(provider_id: str, api_key: str) -> dict:
    """Authorization plus any provider-specific extras."""
    provider = get_provider(provider_id)
    headers = {"Content-Type": "application/json", **provider.extra_headers}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def missing_api_key(provider_id: str, api_key: str) -> bool:
    return get_provider(provider_id).api_key == "required" and not api_key


def refusal_reason(cfg: dict) -> str:
    """Why the LLM cannot be used for *cfg*, or "" when it can.

    Every feature that sends a transcript to a model has to make the same two
    checks — is one configured, and has the user agreed to a hosted one seeing
    their transcripts — and has to make them *before* starting work rather than
    failing halfway through. Returning the sentence instead of raising keeps
    this importable from core code that has no business knowing about HTTP.
    """
    if not cfg.get("llm_model_name"):
        return "No LLM model configured. Set it in AI Analysis settings."

    provider = get_provider(cfg.get("llm_provider", ""))
    if provider.cloud and not cfg.get("llm_allow_cloud"):
        return (
            f"{provider.label} is a hosted service, so this would send the "
            "transcript off this machine. Confirm that in AI Analysis settings first."
        )
    return ""


# --- identification and model normalization ---------------------------------


def identify_provider(base_url: str, payload: dict | None, headers: dict | None) -> str:
    """Best guess at which backend answered, for labelling scan results."""
    haystack = " ".join(
        [
            str(payload or "")[:4000],
            " ".join(f"{k}:{v}" for k, v in (headers or {}).items()),
            base_url or "",
        ]
    ).lower()

    for provider in PROVIDERS:
        if provider.id in ("custom",):
            continue
        if any(mark in haystack for mark in provider.fingerprints):
            return provider.id

    # Fall back to the port, which is a decent hint for the single-tenant ones.
    try:
        port = urlparse(base_url).port
    except ValueError:
        port = None
    if port is not None:
        for provider in PROVIDERS:
            if port in provider.detect_ports:
                return provider.id
    return "custom"


def normalize_models(payload: dict | list) -> list[dict]:
    """Flatten a /v1/models response into {id, name, context_length, free}.

    Local servers return a bare ``{id, object, owned_by}``; OpenRouter adds a
    display name, a context length and pricing. Both shapes end up the same.
    """
    if isinstance(payload, dict):
        rows = payload.get("data") or payload.get("models") or []
    else:
        rows = payload or []

    models: list[dict] = []
    for row in rows:
        if isinstance(row, str):
            models.append({"id": row, "name": row, "context_length": None, "free": False})
            continue
        if not isinstance(row, dict):
            continue
        model_id = row.get("id") or row.get("name") or row.get("model")
        if not model_id:
            continue

        pricing = row.get("pricing") or {}
        try:
            prompt_price = float(pricing.get("prompt", 0) or 0)
            completion_price = float(pricing.get("completion", 0) or 0)
            free = prompt_price == 0 and completion_price == 0 and bool(pricing)
        except (TypeError, ValueError):
            free = False

        models.append({
            "id": model_id,
            "name": row.get("name") or model_id,
            "context_length": row.get("context_length") or row.get("max_context_length"),
            "free": free or str(model_id).endswith(":free"),
        })

    models.sort(key=lambda m: str(m["id"]).lower())
    return models


# --- error messages ---------------------------------------------------------


def explain_connection_error(provider_id: str, base_url: str, exc: Exception) -> str:
    """Turn a transport exception into something the user can act on."""
    provider = get_provider(provider_id)
    text = str(exc)
    lowered = text.lower()

    if "connection refused" in lowered or "failed to establish" in lowered or "max retries" in lowered:
        hint = f"Nothing is listening at {base_url}."
        if provider.id != "custom":
            hint += f" Is {provider.label} running?"
        if in_container():
            hint += (
                " AmicoScript is running in a container, so the server must be "
                f"reachable at {container_host_alias()} and must listen on more "
                "than loopback."
            )
        return hint
    if "name or service not known" in lowered or "nodename nor servname" in lowered or "getaddrinfo" in lowered:
        return f"The host in {base_url} could not be resolved. Check the address for typos."
    if "timed out" in lowered or "timeout" in lowered:
        return f"{base_url} accepted the connection but did not answer in time."
    if "ssl" in lowered or "certificate" in lowered:
        return f"TLS failed talking to {base_url}: {text}"
    return text


def explain_http_status(provider_id: str, status: int, body: str) -> str:
    provider = get_provider(provider_id)
    snippet = (body or "").strip()[:200]

    if status in (401, 403):
        if provider.api_key == "required":
            return (
                f"{provider.label} rejected the API key. Copy a fresh one"
                + (f" ({provider.key_hint})" if provider.key_hint else "")
                + " and try again."
            )
        return f"The server rejected the request ({status}). It may need an API key. {snippet}"
    if status == 404:
        return (
            "The server answered, but not at that path. Check the base URL — it "
            "should be the server root, without /v1 on the end."
        )
    if status == 429:
        return f"{provider.label} is rate-limiting this key. Wait a moment and retry."
    if status >= 500:
        return f"{provider.label} returned a server error ({status}). {snippet}"
    return f"HTTP {status}. {snippet}"


# --- probing ----------------------------------------------------------------


def detection_targets() -> list[str]:
    """Base URLs worth probing when scanning for a running local server.

    Deduplicated across providers that share a port (llama.cpp and LocalAI both
    use 8080), and pointed at the container host when running in one.
    """
    host = container_host_alias() if in_container() else "localhost"
    ports: list[int] = []
    for provider in PROVIDERS:
        for port in provider.detect_ports:
            if port not in ports:
                ports.append(port)
    return [f"http://{host}:{port}" for port in ports]

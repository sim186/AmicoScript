"""AmicoScript — password authentication for non-local access.

AmicoScript is local-first: on a laptop it listens on loopback and there is
nobody else to keep out, so out of the box it asks for nothing. But the project
also documents a Traefik deployment on a public domain
(``docker-compose.prod.yml``), and every API route used to be wide open there —
anyone who found the hostname could read the library, download the audio, and
read the stored Hugging Face token straight out of ``GET /api/settings``.

The rule this module enforces:

* **loopback** requests behave exactly as before — no password, no cookie;
* **non-loopback** requests need a valid session, and if no password has been
  configured yet they are refused with an explanation rather than served.

So exposing the app without setting a password fails closed instead of silently
publishing your transcripts.

Modes (``AMICOSCRIPT_AUTH``):

``auto``   default, as described above.
``always`` every request needs a session, loopback included. Headless local
           clients (the TUI, the meeting watcher) authenticate with the API
           token instead of a cookie.
``off``    disable this module entirely. For deployments that put their own
           authentication in front (SSO proxy, Traefik basic-auth).

Credentials live in the same ``settings.json`` as the rest of the app config:
a PBKDF2-SHA256 hash + salt, an HMAC secret for signing session cookies, and a
long random API token for non-browser clients.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import time
from dataclasses import dataclass

from settings import load_settings, save_settings

# --- tunables ---------------------------------------------------------------

SESSION_COOKIE = "amicoscript_session"
SESSION_TTL = 30 * 24 * 3600  # 30 days
PBKDF2_ITERATIONS = 240_000
# Login throttling: after this many failures from one client, refuse further
# attempts for LOCKOUT_SECONDS. Keeps a public deployment from being a free
# password oracle without needing a rate-limiting dependency.
MAX_FAILED_ATTEMPTS = 8
LOCKOUT_SECONDS = 300

_failed_attempts: dict[str, list[float]] = {}


# --- password hashing -------------------------------------------------------


def hash_password(password: str, salt: str = "") -> tuple[str, str]:
    """Return (hash_hex, salt_hex) for *password*, generating a salt if needed."""
    salt_hex = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), PBKDF2_ITERATIONS
    )
    return digest.hex(), salt_hex


def verify_password(password: str, hash_hex: str, salt_hex: str) -> bool:
    if not password or not hash_hex or not salt_hex:
        return False
    try:
        candidate, _ = hash_password(password, salt_hex)
    except ValueError:
        return False
    return hmac.compare_digest(candidate, hash_hex)


# --- stored credentials -----------------------------------------------------


def _auth_mode() -> str:
    mode = os.environ.get("AMICOSCRIPT_AUTH", "auto").strip().lower()
    return mode if mode in {"auto", "always", "off"} else "auto"


def is_enabled() -> bool:
    return _auth_mode() != "off"


def password_is_set() -> bool:
    if os.environ.get("AMICOSCRIPT_PASSWORD", ""):
        return True
    s = load_settings()
    return bool(s.get("auth_password_hash") and s.get("auth_password_salt"))


def check_password(password: str) -> bool:
    """Verify against the env password if present, else the stored hash."""
    env_password = os.environ.get("AMICOSCRIPT_PASSWORD", "")
    if env_password:
        return hmac.compare_digest(password, env_password)
    s = load_settings()
    return verify_password(
        password, s.get("auth_password_hash", ""), s.get("auth_password_salt", "")
    )


def set_password(password: str) -> str:
    """Store a new password. Returns the API token for headless clients.

    Changing the password rotates the signing secret, which invalidates every
    existing session cookie — the expected behaviour after a suspected leak.
    """
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters long")
    digest, salt = hash_password(password)
    s = load_settings()
    s["auth_password_hash"] = digest
    s["auth_password_salt"] = salt
    s["auth_secret"] = secrets.token_hex(32)
    s.setdefault("auth_api_token", secrets.token_urlsafe(32))
    save_settings(s)
    return s["auth_api_token"]


def clear_password() -> None:
    s = load_settings()
    for key in ("auth_password_hash", "auth_password_salt", "auth_secret", "auth_api_token"):
        s.pop(key, None)
    save_settings(s)


def _signing_secret() -> str:
    s = load_settings()
    secret = s.get("auth_secret", "")
    if not secret:
        secret = secrets.token_hex(32)
        s["auth_secret"] = secret
        save_settings(s)
    return secret


def api_token() -> str:
    """Long-lived token for non-browser clients (TUI, meeting watcher, scripts)."""
    env_token = os.environ.get("AMICOSCRIPT_API_TOKEN", "")
    if env_token:
        return env_token
    s = load_settings()
    token = s.get("auth_api_token", "")
    if not token:
        token = secrets.token_urlsafe(32)
        s["auth_api_token"] = token
        save_settings(s)
    return token


# --- session tokens ---------------------------------------------------------


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def issue_session(ttl: int = SESSION_TTL) -> str:
    payload = json.dumps({"exp": int(time.time()) + ttl}, separators=(",", ":"))
    body = _b64e(payload.encode("utf-8"))
    sig = hmac.new(_signing_secret().encode("utf-8"), body.encode("ascii"), hashlib.sha256)
    return f"{body}.{_b64e(sig.digest())}"


def session_is_valid(token: str) -> bool:
    if not token or "." not in token:
        return False
    body, _, sig = token.partition(".")
    expected = hmac.new(
        _signing_secret().encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).digest()
    try:
        if not hmac.compare_digest(_b64d(sig), expected):
            return False
        payload = json.loads(_b64d(body))
    except (ValueError, TypeError, json.JSONDecodeError):
        return False
    return float(payload.get("exp", 0)) > time.time()


# --- request classification -------------------------------------------------


def is_loopback_host(host: str) -> bool:
    """True for 127.0.0.0/8, ::1 and the literal 'localhost'.

    Deliberately reads the *direct peer* address, never X-Forwarded-For: a
    reverse proxy sets the peer to itself (a container IP), so a public request
    can never masquerade as local by sending a header.
    """
    if not host:
        return False
    if host in ("localhost", "::1"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def request_is_local(request) -> bool:
    try:
        client = request.client
    except Exception:
        return False
    return bool(client) and is_loopback_host(client.host or "")


def _bearer_token(request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.headers.get("x-amicoscript-token", "").strip()


def request_is_authenticated(request) -> bool:
    """True when the request carries a valid session cookie or API token."""
    cookie = request.cookies.get(SESSION_COOKIE, "")
    if cookie and session_is_valid(cookie):
        return True
    token = _bearer_token(request)
    if token and password_is_set():
        return hmac.compare_digest(token, api_token())
    return False


@dataclass
class AccessDecision:
    allowed: bool
    status_code: int = 401
    code: str = ""
    message: str = ""


def evaluate_request(request) -> AccessDecision:
    """Decide whether *request* may reach an API route."""
    mode = _auth_mode()
    if mode == "off":
        return AccessDecision(True)

    if request_is_authenticated(request):
        return AccessDecision(True)

    local = request_is_local(request)

    if mode == "auto" and local:
        return AccessDecision(True)

    if not password_is_set():
        if local:
            # 'always' mode with no password yet — let the owner set one.
            return AccessDecision(True)
        return AccessDecision(
            False,
            503,
            "auth_setup_required",
            "AmicoScript is reachable from the network but no password is set. "
            "Set one from the app on the host machine, or start the server with "
            "AMICOSCRIPT_PASSWORD. To run without authentication (for example "
            "behind your own SSO proxy) set AMICOSCRIPT_AUTH=off.",
        )

    return AccessDecision(False, 401, "auth_required", "Authentication required.")


# --- login throttling -------------------------------------------------------


def register_failure(client_key: str) -> None:
    now = time.time()
    attempts = [t for t in _failed_attempts.get(client_key, []) if now - t < LOCKOUT_SECONDS]
    attempts.append(now)
    _failed_attempts[client_key] = attempts


def clear_failures(client_key: str) -> None:
    _failed_attempts.pop(client_key, None)


def seconds_until_unlock(client_key: str) -> int:
    now = time.time()
    attempts = [t for t in _failed_attempts.get(client_key, []) if now - t < LOCKOUT_SECONDS]
    _failed_attempts[client_key] = attempts
    if len(attempts) < MAX_FAILED_ATTEMPTS:
        return 0
    return max(0, int(LOCKOUT_SECONDS - (now - attempts[0])) + 1)


def reset_throttle_state() -> None:
    """Test hook — drops all recorded login failures."""
    _failed_attempts.clear()

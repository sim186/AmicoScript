"""Tests for the authentication layer.

The property that matters: a request arriving from off-machine must not be able
to read the library, and must not be able to read the stored credentials, until
someone has set a password.
"""
import pytest

pytestmark = pytest.mark.usefixtures("clean_settings")


@pytest.fixture(autouse=True)
def _reset_throttle():
    import auth

    auth.reset_throttle_state()
    yield
    auth.reset_throttle_state()


# --- password hashing -------------------------------------------------------


def test_password_round_trip():
    import auth

    digest, salt = auth.hash_password("correct horse battery")
    assert auth.verify_password("correct horse battery", digest, salt)
    assert not auth.verify_password("wrong", digest, salt)


def test_verify_rejects_empty_inputs():
    import auth

    assert not auth.verify_password("", "abc", "def")
    assert not auth.verify_password("pw", "", "")


def test_same_password_gets_a_different_salt():
    import auth

    first, salt_a = auth.hash_password("hunter22222")
    second, salt_b = auth.hash_password("hunter22222")
    assert salt_a != salt_b
    assert first != second


def test_set_password_rejects_short_passwords():
    import auth

    with pytest.raises(ValueError):
        auth.set_password("short")


# --- session tokens ---------------------------------------------------------


def test_session_token_round_trip():
    import auth

    token = auth.issue_session()
    assert auth.session_is_valid(token)


def test_tampered_session_token_is_rejected():
    import auth

    token = auth.issue_session()
    body, _, sig = token.partition(".")
    assert not auth.session_is_valid(f"{body}x.{sig}")
    assert not auth.session_is_valid(f"{body}.{sig[:-2]}ab")
    assert not auth.session_is_valid("garbage")
    assert not auth.session_is_valid("")


def test_expired_session_token_is_rejected():
    import auth

    assert not auth.session_is_valid(auth.issue_session(ttl=-10))


def test_changing_the_password_invalidates_existing_sessions():
    import auth

    auth.set_password("first-password")
    token = auth.issue_session()
    assert auth.session_is_valid(token)

    auth.set_password("second-password")
    assert not auth.session_is_valid(token)


# --- loopback classification ------------------------------------------------


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.5", "::1", "localhost"])
def test_loopback_hosts(host):
    import auth

    assert auth.is_loopback_host(host)


@pytest.mark.parametrize("host", ["203.0.113.7", "10.0.0.4", "", "example.com", "0.0.0.0"])
def test_non_loopback_hosts(host):
    import auth

    assert not auth.is_loopback_host(host)


# --- middleware behaviour ---------------------------------------------------


def test_local_requests_work_without_a_password(client, monkeypatch):
    monkeypatch.setenv("AMICOSCRIPT_AUTH", "auto")
    assert client.get("/api/library").status_code == 200


def test_remote_request_is_refused_when_no_password_is_set(remote_client, monkeypatch):
    """The headline fix: exposing the app unconfigured fails closed."""
    monkeypatch.setenv("AMICOSCRIPT_AUTH", "auto")

    resp = remote_client.get("/api/library")
    assert resp.status_code == 503
    assert resp.json()["code"] == "auth_setup_required"


def test_remote_request_cannot_read_the_stored_tokens(remote_client, monkeypatch):
    from settings import load_settings, save_settings

    monkeypatch.setenv("AMICOSCRIPT_AUTH", "auto")
    settings = load_settings()
    settings["hf_token"] = "hf_do_not_leak"
    save_settings(settings)

    resp = remote_client.get("/api/settings")
    assert resp.status_code == 503
    assert "hf_do_not_leak" not in resp.text


def test_remote_request_needs_a_session_once_a_password_exists(remote_client, monkeypatch):
    import auth

    monkeypatch.setenv("AMICOSCRIPT_AUTH", "auto")
    auth.set_password("a-good-password")

    resp = remote_client.get("/api/library")
    assert resp.status_code == 401
    assert resp.json()["code"] == "auth_required"


def test_login_grants_access_from_a_remote_client(remote_client, monkeypatch):
    import auth

    monkeypatch.setenv("AMICOSCRIPT_AUTH", "auto")
    auth.set_password("a-good-password")
    assert remote_client.get("/api/library").status_code == 401

    login = remote_client.post("/api/auth/login", data={"password": "a-good-password"})
    assert login.status_code == 200
    assert auth.SESSION_COOKIE in login.cookies

    # The cookie now rides along on the client's session.
    assert remote_client.get("/api/library").status_code == 200


def test_login_rejects_a_wrong_password(remote_client, monkeypatch):
    import auth

    monkeypatch.setenv("AMICOSCRIPT_AUTH", "auto")
    auth.set_password("a-good-password")

    assert remote_client.post("/api/auth/login", data={"password": "nope"}).status_code == 401
    assert remote_client.get("/api/library").status_code == 401


def test_repeated_failures_are_throttled(remote_client, monkeypatch):
    import auth

    monkeypatch.setenv("AMICOSCRIPT_AUTH", "auto")
    auth.set_password("a-good-password")

    codes = [
        remote_client.post("/api/auth/login", data={"password": "nope"}).status_code
        for _ in range(auth.MAX_FAILED_ATTEMPTS + 2)
    ]
    assert 429 in codes
    # Still locked out even with the right password, until the window passes.
    assert remote_client.post(
        "/api/auth/login", data={"password": "a-good-password"}
    ).status_code == 429


def test_api_token_authenticates_headless_clients(remote_client, monkeypatch):
    import auth

    monkeypatch.setenv("AMICOSCRIPT_AUTH", "auto")
    token = auth.set_password("a-good-password")

    resp = remote_client.get("/api/library", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_wrong_api_token_is_refused(remote_client, monkeypatch):
    import auth

    monkeypatch.setenv("AMICOSCRIPT_AUTH", "auto")
    auth.set_password("a-good-password")

    resp = remote_client.get("/api/library", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_always_mode_challenges_local_requests_too(client, monkeypatch):
    import auth

    auth.set_password("a-good-password")
    monkeypatch.setenv("AMICOSCRIPT_AUTH", "always")

    assert client.get("/api/library").status_code == 401


def test_off_mode_lets_everything_through(remote_client, monkeypatch):
    import auth

    auth.set_password("a-good-password")
    monkeypatch.setenv("AMICOSCRIPT_AUTH", "off")

    assert remote_client.get("/api/library").status_code == 200


def test_auth_status_is_reachable_without_credentials(remote_client, monkeypatch):
    monkeypatch.setenv("AMICOSCRIPT_AUTH", "auto")
    resp = remote_client.get("/api/auth/status")
    assert resp.status_code == 200
    assert resp.json()["password_set"] is False
    assert resp.json()["login_required"] is True


def test_static_assets_stay_public(remote_client, monkeypatch):
    """The login screen has to load before anyone can log in."""
    monkeypatch.setenv("AMICOSCRIPT_AUTH", "auto")
    resp = remote_client.get("/")
    assert resp.status_code == 200


def test_first_password_cannot_be_set_remotely(remote_client, monkeypatch):
    monkeypatch.setenv("AMICOSCRIPT_AUTH", "auto")
    resp = remote_client.post("/api/auth/password", data={"new_password": "hijacked123"})
    assert resp.status_code == 403


def test_changing_the_password_requires_the_current_one(client, monkeypatch):
    import auth

    monkeypatch.setenv("AMICOSCRIPT_AUTH", "auto")
    auth.set_password("original-password")

    bad = client.post(
        "/api/auth/password",
        data={"new_password": "replacement-password", "current_password": "guess"},
    )
    assert bad.status_code == 403

    good = client.post(
        "/api/auth/password",
        data={"new_password": "replacement-password", "current_password": "original-password"},
    )
    assert good.status_code == 200
    assert auth.check_password("replacement-password")


def test_env_password_takes_effect_without_settings(remote_client, monkeypatch):
    import auth

    monkeypatch.setenv("AMICOSCRIPT_AUTH", "auto")
    monkeypatch.setenv("AMICOSCRIPT_PASSWORD", "from-the-environment")

    assert auth.password_is_set()
    assert auth.check_password("from-the-environment")
    assert not auth.check_password("something-else")
    assert remote_client.get("/api/library").status_code == 401

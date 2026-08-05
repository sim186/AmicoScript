"""Authentication endpoints — login, logout, password management.

These routes are exempt from the auth middleware (see AUTH_EXEMPT_PATHS in
backend/main.py); each one enforces its own rules.
"""
from fastapi import APIRouter, Form, HTTPException, Request, Response

import auth

router = APIRouter()


def _client_key(request: Request) -> str:
    try:
        return (request.client.host if request.client else "") or "unknown"
    except Exception:
        return "unknown"


def _set_session_cookie(request: Request, response: Response) -> None:
    response.set_cookie(
        auth.SESSION_COOKIE,
        auth.issue_session(),
        max_age=auth.SESSION_TTL,
        httponly=True,
        samesite="lax",
        # Only mark Secure on HTTPS — a Secure cookie would be dropped by the
        # browser over plain http://localhost, locking local users out.
        secure=request.url.scheme == "https",
        path="/",
    )


@router.get("/api/auth/status")
def auth_status(request: Request) -> dict:
    """Tells the UI whether to show a login screen or a password-setup prompt."""
    return {
        "enabled": auth.is_enabled(),
        "mode": auth._auth_mode(),
        "password_set": auth.password_is_set(),
        "authenticated": auth.request_is_authenticated(request),
        "local": auth.request_is_local(request),
        # True when this request would be refused without logging in.
        "login_required": not auth.evaluate_request(request).allowed,
    }


@router.post("/api/auth/login")
def login(request: Request, response: Response, password: str = Form(...)) -> dict:
    key = _client_key(request)
    wait = auth.seconds_until_unlock(key)
    if wait:
        raise HTTPException(429, f"Too many failed attempts. Try again in {wait}s.")

    if not auth.password_is_set():
        raise HTTPException(400, "No password is configured.")
    if not auth.check_password(password):
        auth.register_failure(key)
        raise HTTPException(401, "Incorrect password.")

    auth.clear_failures(key)
    _set_session_cookie(request, response)
    return {"ok": True}


@router.post("/api/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"ok": True}


@router.post("/api/auth/password")
def set_password(
    request: Request,
    response: Response,
    new_password: str = Form(...),
    current_password: str = Form(""),
) -> dict:
    """Set or change the password.

    Changing an existing password requires the current one. Setting the first
    password requires either a local request or an already-valid session, so a
    stranger cannot claim an exposed instance before its owner does.
    """
    if auth.password_is_set():
        if not auth.check_password(current_password):
            raise HTTPException(403, "Current password is incorrect.")
    elif not (auth.request_is_local(request) or auth.request_is_authenticated(request)):
        raise HTTPException(403, "The first password must be set from the host machine.")

    try:
        token = auth.set_password(new_password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    _set_session_cookie(request, response)
    return {"ok": True, "api_token": token}


@router.delete("/api/auth/password")
def remove_password(request: Request, current_password: str = "") -> dict:
    """Remove the password. Local-only: it re-opens the instance."""
    if not auth.request_is_local(request):
        raise HTTPException(403, "Removing the password is only allowed from the host machine.")
    if auth.password_is_set() and not auth.check_password(current_password):
        raise HTTPException(403, "Current password is incorrect.")
    auth.clear_password()
    return {"ok": True}


@router.get("/api/auth/api-token")
def get_api_token(request: Request) -> dict:
    """Token for headless clients (TUI, meeting watcher) in `always` mode."""
    if not auth.password_is_set():
        raise HTTPException(400, "Set a password first.")
    if not (auth.request_is_local(request) or auth.request_is_authenticated(request)):
        raise HTTPException(403, "Not authorised.")
    return {"api_token": auth.api_token()}

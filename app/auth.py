"""Login til kontrolpanelet og adgangskontrol på API'et."""

from __future__ import annotations

import hmac
from typing import Optional

from fastapi import HTTPException, Request
from starlette.requests import HTTPConnection

from .config import Settings

CLIENT_HEADER = "x-talerstol-client"
SESSION_HEADER = "x-control-session"


def check_credentials(settings: Settings, username: str, password: str) -> bool:
    ok_user = hmac.compare_digest(username.strip().encode(), settings.admin_username.encode())
    ok_pass = hmac.compare_digest(password.encode(), settings.admin_password.encode())
    return ok_user and ok_pass


def current_user(conn: HTTPConnection) -> Optional[str]:
    try:
        user = conn.session.get("user")
    except AssertionError:  # ingen SessionMiddleware
        return None
    return user if isinstance(user, str) and user else None


def require_admin(request: Request) -> str:
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Du er ikke logget ind.")
    return user


def require_client_header(request: Request) -> None:
    """CSRF-beskyttelse: skrivekald skal sende en brugerdefineret header, som en
    fremmed hjemmeside ikke kan sætte uden CORS-tilladelse."""
    if request.headers.get(CLIENT_HEADER) != "1":
        raise HTTPException(status_code=400, detail="Manglende klient-header.")


def control_session_id(request: Request) -> Optional[str]:
    return request.headers.get(SESSION_HEADER) or None


def safe_next(url: str, default: str = "/admin") -> str:
    if not url or not url.startswith("/") or url.startswith("//") or "\\" in url:
        return default
    return url

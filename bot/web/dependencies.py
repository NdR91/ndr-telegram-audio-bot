"""
FastAPI dependencies for the web frontend.

Provides shared dependency functions that route modules use to access
application services, sessions, and authentication.
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import HTTPException, Request, Response

from bot.web.auth import (
    SESSION_MAX_AGE,
    decode_session,
    encode_session,
    generate_csrf_token,
)


def get_session(request: Request) -> Optional[dict]:
    """Read and return the current session from the signed cookie.

    Returns ``None`` when no session cookie is present or the cookie
    cannot be decoded (expired, tampered, …).
    """
    cookie = request.cookies.get("session")
    if not cookie:
        return None
    serialiser = request.app.state.serialiser
    return decode_session(serialiser, cookie)


def require_admin(request: Request) -> dict:
    """Dependency: require an authenticated admin session.

    Raises ``HTTPException(401)`` when the user is not logged in as
    an administrator.  Returns the session dict on success.
    """
    session = get_session(request)
    if session is None or not session.get("admin"):
        raise HTTPException(status_code=401)
    return session


def set_session_cookie(response: Response, data: dict, request: Request) -> None:
    """Sign *data* into a session cookie on *response*."""
    serialiser = request.app.state.serialiser
    cookie = encode_session(serialiser, data)
    response.set_cookie(
        key="session",
        value=cookie,
        max_age=int(SESSION_MAX_AGE),
        httponly=True,
        samesite="strict",
        secure=os.getenv("WEB_SECURE_COOKIE", "0") == "1",
    )

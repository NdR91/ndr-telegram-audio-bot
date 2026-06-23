"""
Authentication, session, and CSRF utilities for the web frontend.

Design
------
- Sessions are stored entirely in signed cookies (no server-side storage).
- The session cookie is ``HttpOnly``, ``Secure``, ``SameSite=Strict``.
- CSRF tokens are generated per-session and validated on state-changing
  requests via a hidden form field + Referer header check.
- Passwords are hashed with bcrypt and stored in ``setup_state``.
- The admin username is always ``"admin"`` in this iteration.
"""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from typing import Optional

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from passlib.hash import bcrypt

from bot.database import DatabaseManager

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

SESSION_MAX_AGE = timedelta(hours=24).total_seconds()
"""Session validity in seconds."""

_ADMIN_USERNAME = "admin"
_ADMIN_PASSWORD_KEY = "admin_password_hash"


# ------------------------------------------------------------------
# Session serialiser
# ------------------------------------------------------------------


def _make_serialiser(secret: str) -> URLSafeTimedSerializer:
    """Create a itsdangerous serializer with the given *secret*."""
    return URLSafeTimedSerializer(secret, salt="session")


def encode_session(
    serialiser: URLSafeTimedSerializer,
    data: dict,
) -> str:
    """Sign and serialise a session dict into a cookie value."""
    return serialiser.dumps(data)


def decode_session(
    serialiser: URLSafeTimedSerializer,
    cookie: str,
) -> Optional[dict]:
    """Verify and deserialise a session cookie.

    Returns ``None`` when the cookie is invalid or expired.
    """
    try:
        return serialiser.loads(cookie, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


# ------------------------------------------------------------------
# Password hashing
# ------------------------------------------------------------------


def hash_password(password: str) -> str:
    """Return a bcrypt hash of *password*."""
    return bcrypt.hash(password)


def verify_password(password: str, hash_val: str) -> bool:
    """Return ``True`` if *password* matches *hash_val*."""
    try:
        return bcrypt.verify(password, hash_val)
    except ValueError:
        return False  # malformed hash


# ------------------------------------------------------------------
# Database-backed admin operations
# ------------------------------------------------------------------


def has_admin(db: DatabaseManager) -> bool:
    """Return ``True`` if an admin password hash exists in the DB."""
    return bool(db.get_setup_state(_ADMIN_PASSWORD_KEY))


def set_admin_password(db: DatabaseManager, password: str) -> None:
    """Hash and store the admin password."""
    db.set_setup_state(_ADMIN_PASSWORD_KEY, hash_password(password))
    logger.info("Admin password set")


def verify_admin_password(db: DatabaseManager, password: str) -> bool:
    """Return ``True`` if *password* matches the stored hash."""
    stored = db.get_setup_state(_ADMIN_PASSWORD_KEY)
    if not stored:
        return False
    return verify_password(password, stored)


# ------------------------------------------------------------------
# CSRF
# ------------------------------------------------------------------


def generate_csrf_token() -> str:
    """Return a cryptographically random CSRF token."""
    return secrets.token_urlsafe(32)


def validate_csrf_token(session: dict, token: str) -> bool:
    """Return ``True`` if *token* matches the session CSRF token."""
    return secrets.compare_digest(session.get("csrf_token", ""), token)

"""Signed-session and password-hashing helpers shared by the V2 web app."""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from passlib.hash import bcrypt

SESSION_MAX_AGE = timedelta(hours=24).total_seconds()


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

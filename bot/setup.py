"""
First-run setup code generation and verification.

On a blank data volume a time-limited one-time setup code is generated,
hashed with SHA-256, and stored in the application database.  The
plaintext code is displayed in the container logs so the administrator
can use it during guided onboarding (W2).

Once the first administrator has been created the code is invalidated
and cannot be reused.

Design decisions
----------------
- The code is stored only as a hash — the plaintext is never persisted.
- Expiry is enforced server-side via a stored timestamp.
- The hash + expiry keys live in the ``setup_state`` table alongside
  other setup flags (``admin_created``, etc.).
- Code generation and validation use :mod:`secrets` and :mod:`hashlib`
  from the standard library with no additional dependencies.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

SETUP_CODE_LENGTH = 8
"""Number of alphanumeric characters in the generated code."""

SETUP_CODE_TTL_SECONDS = 1800
"""Code validity period in seconds (default: 30 minutes)."""

_SETUP_CODE_KEY = "setup_code_hash"
"""Database key for the SHA-256 hash of the setup code."""

_SETUP_CODE_EXPIRY_KEY = "setup_code_expires_at"
"""Database key for the Unix timestamp when the code expires."""


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


def generate_setup_code(db) -> str:
    """Generate a time-limited one-time setup code.

    Stores the SHA-256 hash and an expiry timestamp in the application
    database.

    Parameters
    ----------
    db:
        An initialised :class:`~bot.database.DatabaseManager`.

    Returns
    -------
    str
        The plaintext setup code.  Display this in container logs so
        the administrator can copy it.
    """
    code = _random_code()
    hash_val = _hash_code(code)
    expires_at = str(int(time.monotonic()) + SETUP_CODE_TTL_SECONDS)

    db.set_setup_state(_SETUP_CODE_KEY, hash_val)
    db.set_setup_state(_SETUP_CODE_EXPIRY_KEY, expires_at)

    logger.info(
        "One-time setup code generated (expires in %s seconds)",
        SETUP_CODE_TTL_SECONDS,
    )
    return code


def validate_setup_code(db, code: str) -> bool:
    """Validate *code* against the stored hash.

    Parameters
    ----------
    db:
        An initialised :class:`~bot.database.DatabaseManager`.
    code:
        Plaintext code to validate.

    Returns
    -------
    bool
        ``True`` when *code* matches the stored hash **and** has not
        expired.
    """
    stored_hash = db.get_setup_state(_SETUP_CODE_KEY)
    if not stored_hash:
        logger.warning("No setup code hash found — validation rejected")
        return False

    if _is_expired(db):
        logger.warning("Setup code has expired — validation rejected")
        return False

    return _hash_code(code) == stored_hash


def invalidate_setup_code(db) -> None:
    """Invalidate the setup code so it can no longer be used.

    Called after the first administrator has been created
    (``admin_created`` is set).
    """
    db.set_setup_state(_SETUP_CODE_KEY, "")
    db.set_setup_state(_SETUP_CODE_EXPIRY_KEY, "")
    logger.info("Setup code invalidated")


def get_setup_code_expiry(db) -> Optional[float]:
    """Return the Unix timestamp when the current code expires, or
    ``None`` if no code has been generated or the code has been
    invalidated."""
    raw = db.get_setup_state(_SETUP_CODE_EXPIRY_KEY)
    if raw:
        try:
            return float(raw)
        except (ValueError, TypeError):
            return None
    return None


def is_code_generated(db) -> bool:
    """Return ``True`` if a setup code hash exists in the database
    (regardless of expiry or validity)."""
    return bool(db.get_setup_state(_SETUP_CODE_KEY))


def is_first_run(db) -> bool:
    """Return ``True`` when no ``admin_created`` flag exists.

    This is the condition for triggering setup-code generation on
    startup.
    """
    return db.get_setup_state("admin_created") is None


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _random_code() -> str:
    """Generate a cryptographically random alphanumeric string.

    Uses a carefully chosen alphabet that avoids visually ambiguous
    characters (no ``0``/``O``, ``1``/``l``, etc.).
    """
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(SETUP_CODE_LENGTH))


def _hash_code(code: str) -> str:
    """Return the SHA-256 hex digest of *code*."""
    return hashlib.sha256(code.encode()).hexdigest()


def _is_expired(db) -> bool:
    """Return ``True`` if the stored expiry timestamp is in the past."""
    raw = db.get_setup_state(_SETUP_CODE_EXPIRY_KEY)
    if not raw:
        return True  # no expiry stored = treat as expired
    try:
        return time.monotonic() > float(raw)
    except (ValueError, TypeError):
        return True

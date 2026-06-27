"""
Recovery code generation and verification for admin password reset.

Generates a time-limited one-time recovery code (mirroring the setup-code
pattern from ``bot/setup.py``).  The code is displayed in container logs
so the administrator can reset the password without requiring Telegram
access or a running bot.

Design decisions
----------------
- Follows the same pattern as :mod:`bot.setup` (setup code) for consistency.
- The code is stored only as a SHA-256 hash — the plaintext is never persisted.
- Expiry is enforced server-side via a stored timestamp.
- The hash + expiry keys live in the ``setup_state`` table alongside
  other setup and onboarding flags.
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

RECOVERY_CODE_LENGTH = 8
"""Number of alphanumeric characters in the generated code."""

RECOVERY_CODE_TTL_SECONDS = 1800
"""Code validity period in seconds (default: 30 minutes)."""

_RECOVERY_CODE_KEY = "recovery_code_hash"
"""Database key for the SHA-256 hash of the recovery code."""

_RECOVERY_CODE_EXPIRY_KEY = "recovery_code_expires_at"
"""Database key for the Unix timestamp when the code expires."""


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


def generate_recovery_code(db) -> str:
    """Generate a time-limited one-time recovery code.

    Stores the SHA-256 hash and an expiry timestamp in the application
    database.

    Parameters
    ----------
    db:
        An initialised :class:`~bot.database.DatabaseManager`.

    Returns
    -------
    str
        The plaintext recovery code.  Display this in container logs so
        the administrator can copy it.
    """
    code = _random_code()
    hash_val = _hash_code(code)
    expires_at = str(int(time.monotonic()) + RECOVERY_CODE_TTL_SECONDS)

    db.set_setup_state(_RECOVERY_CODE_KEY, hash_val)
    db.set_setup_state(_RECOVERY_CODE_EXPIRY_KEY, expires_at)

    logger.info(
        "One-time recovery code generated (expires in %s seconds)",
        RECOVERY_CODE_TTL_SECONDS,
    )
    return code


def validate_recovery_code(db, code: str) -> bool:
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
    stored_hash = db.get_setup_state(_RECOVERY_CODE_KEY)
    if not stored_hash:
        logger.warning("No recovery code hash found — validation rejected")
        return False

    if _is_expired(db):
        logger.warning("Recovery code has expired — validation rejected")
        return False

    return _hash_code(code) == stored_hash


def invalidate_recovery_code(db) -> None:
    """Invalidate the recovery code so it can no longer be used.

    Called after the admin password has been successfully reset.
    """
    db.set_setup_state(_RECOVERY_CODE_KEY, "")
    db.set_setup_state(_RECOVERY_CODE_EXPIRY_KEY, "")
    logger.info("Recovery code invalidated")


def is_recovery_code_generated(db) -> bool:
    """Return ``True`` if a recovery code hash exists in the database
    (regardless of expiry or validity)."""
    return bool(db.get_setup_state(_RECOVERY_CODE_KEY))


def get_recovery_code_expiry(db) -> Optional[float]:
    """Return the Unix timestamp when the current code expires, or
    ``None`` if no code has been generated or the code has been
    invalidated."""
    raw = db.get_setup_state(_RECOVERY_CODE_EXPIRY_KEY)
    if raw:
        try:
            return float(raw)
        except (ValueError, TypeError):
            return None
    return None


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _random_code() -> str:
    """Generate a cryptographically random alphanumeric string.

    Uses a carefully chosen alphabet that avoids visually ambiguous
    characters (no ``0``/``O``, ``1``/``l``, etc.).
    """
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(RECOVERY_CODE_LENGTH))


def _hash_code(code: str) -> str:
    """Return the SHA-256 hex digest of *code*."""
    return hashlib.sha256(code.encode()).hexdigest()


def _is_expired(db) -> bool:
    """Return ``True`` if the stored expiry timestamp is in the past."""
    raw = db.get_setup_state(_RECOVERY_CODE_EXPIRY_KEY)
    if not raw:
        return True  # no expiry stored = treat as expired
    try:
        return time.monotonic() > float(raw)
    except (ValueError, TypeError):
        return True

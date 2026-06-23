"""
Tests for the web frontend authentication module (W1).

Covers session serialisation, password hashing, database-backed admin
operations, and CSRF token generation/validation.
"""

from __future__ import annotations

from itsdangerous import URLSafeTimedSerializer
from passlib.hash import bcrypt

from bot.database import DatabaseManager
from bot.web.auth import (
    SESSION_MAX_AGE,
    decode_session,
    encode_session,
    generate_csrf_token,
    has_admin,
    hash_password,
    set_admin_password,
    validate_csrf_token,
    verify_admin_password,
    verify_password,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _make_db(tmp_path) -> DatabaseManager:
    db = DatabaseManager(str(tmp_path / "app.sqlite3"))
    db.initialize()
    return db


def _make_serialiser(secret: str = "test-secret") -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt="session")


# ------------------------------------------------------------------
# Session encode / decode
# ------------------------------------------------------------------


def test_encode_decode_roundtrip():
    serialiser = _make_serialiser()
    data = {"admin": True, "csrf_token": "abc123"}
    cookie = encode_session(serialiser, data)
    assert isinstance(cookie, str)
    decoded = decode_session(serialiser, cookie)
    assert decoded == data


def test_decode_none_on_bad_signature():
    serialiser = _make_serialiser()
    assert decode_session(serialiser, "invalid-cookie") is None


def test_decode_none_on_empty_cookie():
    serialiser = _make_serialiser()
    assert decode_session(serialiser, "") is None


def test_decode_none_on_wrong_secret():
    s1 = _make_serialiser("secret-1")
    s2 = _make_serialiser("secret-2")
    cookie = encode_session(s1, {"admin": True})
    assert decode_session(s2, cookie) is None


# ------------------------------------------------------------------
# Password hashing / verification
# ------------------------------------------------------------------


def test_hash_password_returns_bcrypt_hash():
    hashed = hash_password("my-secure-password")
    assert hashed.startswith("$2b$") or hashed.startswith("$2a$")


def test_verify_password_correct():
    hashed = bcrypt.hash("correct-pw")
    assert verify_password("correct-pw", hashed) is True


def test_verify_password_incorrect():
    hashed = bcrypt.hash("correct-pw")
    assert verify_password("wrong-pw", hashed) is False


def test_verify_password_empty():
    hashed = bcrypt.hash("pw")
    assert verify_password("", hashed) is False
    assert verify_password("pw", "") is False


def test_verify_password_malformed_hash():
    assert verify_password("pw", "not-a-bcrypt-hash") is False


# ------------------------------------------------------------------
# Database-backed admin operations
# ------------------------------------------------------------------


def test_has_admin_false_on_empty_db(tmp_path):
    db = _make_db(tmp_path)
    assert has_admin(db) is False


def test_has_admin_true_after_set(tmp_path):
    db = _make_db(tmp_path)
    set_admin_password(db, "admin-password")
    assert has_admin(db) is True


def test_verify_admin_password_correct(tmp_path):
    db = _make_db(tmp_path)
    set_admin_password(db, "admin-password")
    assert verify_admin_password(db, "admin-password") is True


def test_verify_admin_password_incorrect(tmp_path):
    db = _make_db(tmp_path)
    set_admin_password(db, "admin-password")
    assert verify_admin_password(db, "wrong-password") is False


def test_verify_admin_password_no_hash(tmp_path):
    db = _make_db(tmp_path)
    assert verify_admin_password(db, "anything") is False


def test_set_admin_password_stores_hash(tmp_path):
    db = _make_db(tmp_path)
    set_admin_password(db, "admin-password")
    stored = db.get_setup_state("admin_password_hash")
    assert stored is not None
    assert stored.startswith("$2b$") or stored.startswith("$2a$")
    # Not plaintext
    assert stored != "admin-password"


# ------------------------------------------------------------------
# CSRF tokens
# ------------------------------------------------------------------


def test_generate_csrf_token_returns_string():
    token = generate_csrf_token()
    assert isinstance(token, str)
    assert len(token) > 16


def test_generate_csrf_token_unique():
    tokens = {generate_csrf_token() for _ in range(50)}
    assert len(tokens) == 50


def test_validate_csrf_token_correct():
    session = {"csrf_token": "secret-token"}
    assert validate_csrf_token(session, "secret-token") is True


def test_validate_csrf_token_incorrect():
    session = {"csrf_token": "secret-token"}
    assert validate_csrf_token(session, "wrong-token") is False


def test_validate_csrf_token_empty_session():
    assert validate_csrf_token({}, "token") is False


def test_validate_csrf_token_no_csrf_field():
    assert validate_csrf_token({"admin": True}, "token") is False

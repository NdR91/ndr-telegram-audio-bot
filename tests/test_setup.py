"""
Tests for the first-run setup code module (A6).

Covers code generation, hash storage, validation (correct, incorrect,
missing, expired), invalidation, and helper predicates.
"""

import time

import pytest

from bot.database import DatabaseManager
from bot.setup import (
    SETUP_CODE_LENGTH,
    _SETUP_CODE_EXPIRY_KEY,
    _SETUP_CODE_KEY,
    generate_setup_code,
    get_setup_code_expiry,
    invalidate_setup_code,
    is_code_generated,
    is_first_run,
    validate_setup_code,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _make_db(tmp_path) -> DatabaseManager:
    db = DatabaseManager(str(tmp_path / "app.sqlite3"))
    db.initialize()
    return db


# ------------------------------------------------------------------
# generate_setup_code
# ------------------------------------------------------------------


def test_generate_returns_code_of_expected_length(tmp_path):
    db = _make_db(tmp_path)
    code = generate_setup_code(db)
    assert len(code) == SETUP_CODE_LENGTH
    assert isinstance(code, str)


def test_generate_stores_hash_not_plaintext(tmp_path):
    db = _make_db(tmp_path)
    code = generate_setup_code(db)

    stored = db.get_setup_state(_SETUP_CODE_KEY)
    assert stored is not None
    assert stored != code  # must not be plaintext
    assert len(stored) == 64  # SHA-256 hex digest


def test_generate_stores_expiry(tmp_path):
    db = _make_db(tmp_path)
    generate_setup_code(db)

    expiry = get_setup_code_expiry(db)
    assert expiry is not None
    assert expiry > time.monotonic()  # should be in the future


def test_generate_is_idempotent(tmp_path):
    db = _make_db(tmp_path)
    code1 = generate_setup_code(db)
    code2 = generate_setup_code(db)

    # Second call overwrites — both are different random codes
    assert code1 != code2
    # Only the second hash is stored
    assert validate_setup_code(db, code1) is False
    assert validate_setup_code(db, code2) is True


# ------------------------------------------------------------------
# validate_setup_code
# ------------------------------------------------------------------


def test_validate_correct_code(tmp_path):
    db = _make_db(tmp_path)
    code = generate_setup_code(db)
    assert validate_setup_code(db, code) is True


def test_validate_incorrect_code(tmp_path):
    db = _make_db(tmp_path)
    generate_setup_code(db)
    assert validate_setup_code(db, "wrong-code") is False


def test_validate_empty_code(tmp_path):
    db = _make_db(tmp_path)
    generate_setup_code(db)
    assert validate_setup_code(db, "") is False


def test_validate_when_no_code_stored(tmp_path):
    db = _make_db(tmp_path)
    assert validate_setup_code(db, "anything") is False


def test_validate_after_invalidation(tmp_path):
    db = _make_db(tmp_path)
    code = generate_setup_code(db)
    invalidate_setup_code(db)
    assert validate_setup_code(db, code) is False


# ------------------------------------------------------------------
# invalidate_setup_code
# ------------------------------------------------------------------


def test_invalidate_clears_hash(tmp_path):
    db = _make_db(tmp_path)
    generate_setup_code(db)
    invalidate_setup_code(db)

    assert db.get_setup_state(_SETUP_CODE_KEY) == ""
    assert db.get_setup_state(_SETUP_CODE_EXPIRY_KEY) == ""
    assert is_code_generated(db) is False
    assert get_setup_code_expiry(db) is None


# ------------------------------------------------------------------
# is_code_generated
# ------------------------------------------------------------------


def test_is_code_generated_true_after_generate(tmp_path):
    db = _make_db(tmp_path)
    assert is_code_generated(db) is False
    generate_setup_code(db)
    assert is_code_generated(db) is True


def test_is_code_generated_false_after_invalidation(tmp_path):
    db = _make_db(tmp_path)
    generate_setup_code(db)
    invalidate_setup_code(db)
    assert is_code_generated(db) is False


# ------------------------------------------------------------------
# is_first_run
# ------------------------------------------------------------------


def test_is_first_run_true_when_admin_not_created(tmp_path):
    db = _make_db(tmp_path)
    assert is_first_run(db) is True


def test_is_first_run_false_when_admin_created(tmp_path):
    db = _make_db(tmp_path)
    db.set_setup_state("admin_created", "true")
    assert is_first_run(db) is False


# ------------------------------------------------------------------
# Expiry
# ------------------------------------------------------------------


def test_expired_code_rejected(tmp_path):
    db = _make_db(tmp_path)
    code = generate_setup_code(db)

    # Manually set the expiry to 1 second in the past
    past = str(time.monotonic() - 1)
    db.set_setup_state(_SETUP_CODE_EXPIRY_KEY, past)

    assert validate_setup_code(db, code) is False


def test_get_expiry_returns_none_after_invalidation(tmp_path):
    db = _make_db(tmp_path)
    assert get_setup_code_expiry(db) is None
    generate_setup_code(db)
    assert get_setup_code_expiry(db) is not None
    invalidate_setup_code(db)
    assert get_setup_code_expiry(db) is None


# ------------------------------------------------------------------
# Randomness
# ------------------------------------------------------------------


def test_consecutive_codes_are_different(tmp_path):
    """Verify that two generated codes are not equal (probabilistic)."""
    db = _make_db(tmp_path)
    codes = {generate_setup_code(db) for _ in range(10)}
    invalidate_setup_code(db)
    assert len(codes) == 10  # all unique

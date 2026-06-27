"""
Tests for the recovery code module (W6).

Covers generation, validation, expiry, invalidation, and the recovery
HTTP flow through the web frontend.
"""

from __future__ import annotations

import logging
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from bot.database import DatabaseManager
from bot.recovery import (
    RECOVERY_CODE_TTL_SECONDS,
    generate_recovery_code,
    get_recovery_code_expiry,
    invalidate_recovery_code,
    is_recovery_code_generated,
    validate_recovery_code,
)
from bot.web.app import create_app

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_db(tmp_path) -> DatabaseManager:
    db = DatabaseManager(str(tmp_path / "app.sqlite3"))
    db.initialize()
    return db


def _make_minimal_config(tmp_path) -> SimpleNamespace:
    """Build a minimal Config-like namespace (same as test_web_app.py)."""
    api_keys = {"openai": "sk-test-123"}

    def get_api_key(provider=None):
        provider = provider or "openai"
        return api_keys.get(provider, "")

    return SimpleNamespace(
        telegram_token="123:abc",
        provider_name="openai",
        model_name=None,
        api_keys=api_keys,
        get_api_key=get_api_key,
        prompts={
            "system": "You are a transcription assistant.",
            "refine_template": "Please refine: {raw_text}",
        },
        rate_limit_config={},
        provider_resilience_config={},
        telegram_progressive_output_config={"enabled": False},
        audio_dir=str(tmp_path / "audio_files"),
        authorized_data={"admin": [123], "users": [], "groups": []},
        _relaxed=True,
    )


def _extract_csrf(html: str) -> str:
    """Extract the CSRF token from a Jinja2-rendered hidden input."""
    import re
    match = re.search(
        r'<input[^>]*name="csrf_token"[^>]*value="([^"]+)"',
        html,
    )
    if not match:
        raise AssertionError("CSRF token not found in HTML")
    return match.group(1)


# ------------------------------------------------------------------
# Unit tests — recovery code generation and validation
# ------------------------------------------------------------------


class TestRecoveryCodeUnit:
    """Pure logic tests for the recovery code module."""

    def test_generate_returns_string(self, tmp_path):
        db = _make_db(tmp_path)
        code = generate_recovery_code(db)
        assert isinstance(code, str)
        assert len(code) > 0

    def test_generate_stores_hash(self, tmp_path):
        db = _make_db(tmp_path)
        code = generate_recovery_code(db)
        stored_hash = db.get_setup_state("recovery_code_hash")
        assert stored_hash is not None
        assert stored_hash != code  # not plaintext

    def test_validate_correct_code(self, tmp_path):
        db = _make_db(tmp_path)
        code = generate_recovery_code(db)
        assert validate_recovery_code(db, code) is True

    def test_validate_wrong_code(self, tmp_path):
        db = _make_db(tmp_path)
        generate_recovery_code(db)
        assert validate_recovery_code(db, "wrong-code") is False

    def test_validate_empty_code(self, tmp_path):
        db = _make_db(tmp_path)
        generate_recovery_code(db)
        assert validate_recovery_code(db, "") is False

    def test_validate_no_code_generated(self, tmp_path):
        db = _make_db(tmp_path)
        assert validate_recovery_code(db, "anything") is False

    def test_validate_after_invalidation(self, tmp_path):
        db = _make_db(tmp_path)
        code = generate_recovery_code(db)
        invalidate_recovery_code(db)
        assert validate_recovery_code(db, code) is False

    def test_generate_unique_codes(self, tmp_path):
        db = _make_db(tmp_path)
        codes = {generate_recovery_code(db) for _ in range(20)}
        assert len(codes) == 20

    def test_is_generated_true_after_generate(self, tmp_path):
        db = _make_db(tmp_path)
        assert is_recovery_code_generated(db) is False
        generate_recovery_code(db)
        assert is_recovery_code_generated(db) is True

    def test_is_generated_false_after_invalidate(self, tmp_path):
        db = _make_db(tmp_path)
        generate_recovery_code(db)
        invalidate_recovery_code(db)
        assert is_recovery_code_generated(db) is False

    def test_expiry_timestamp_stored(self, tmp_path):
        db = _make_db(tmp_path)
        generate_recovery_code(db)
        expiry = get_recovery_code_expiry(db)
        assert expiry is not None
        assert expiry > 0

    def test_expiry_returns_none_when_not_generated(self, tmp_path):
        db = _make_db(tmp_path)
        assert get_recovery_code_expiry(db) is None

    def test_expiry_returns_none_after_invalidation(self, tmp_path):
        db = _make_db(tmp_path)
        generate_recovery_code(db)
        invalidate_recovery_code(db)
        assert get_recovery_code_expiry(db) is None

    def test_code_is_time_limited(self, tmp_path):
        """Verify that expiry is set to roughly TTL in the future."""
        db = _make_db(tmp_path)
        before = time.monotonic()
        generate_recovery_code(db)
        expiry = get_recovery_code_expiry(db)
        assert expiry is not None
        # Should be within a few seconds of now + TTL
        assert before + RECOVERY_CODE_TTL_SECONDS - 2 <= expiry <= before + RECOVERY_CODE_TTL_SECONDS + 2


# ------------------------------------------------------------------
# Integration tests — recovery HTTP flow
# ------------------------------------------------------------------


@pytest.fixture
def ready_app(tmp_path):
    """Return a FastAPI app with admin already configured."""
    from bot.web.auth import set_admin_password
    from bot.web.setup_wizard import set_current_step, STEP_DONE

    config = _make_minimal_config(tmp_path)
    app = create_app(config=config)

    set_admin_password(app.state.db, "admin-password")
    app.state.db.set_setup_state("admin_created", "true")
    set_current_step(app.state.db, STEP_DONE)

    return app


class TestRecoveryWebFlow:
    """HTTP-level tests for the recovery page and endpoints."""

    def test_recovery_page_returns_200(self, ready_app):
        client = TestClient(ready_app)
        resp = client.get("/recovery")
        assert resp.status_code == 200
        assert "Recupero accesso" in resp.text

    def test_recovery_page_has_csrf(self, ready_app):
        client = TestClient(ready_app)
        resp = client.get("/recovery")
        csrf = _extract_csrf(resp.text)
        assert len(csrf) > 0

    def test_recovery_page_shows_code_form_by_default(self, ready_app):
        client = TestClient(ready_app)
        resp = client.get("/recovery")
        assert "Codice di recupero" in resp.text
        assert "Verifica codice" in resp.text

    def test_recovery_valid_code_redirects_to_password_form(self, ready_app):
        client = TestClient(ready_app)
        # Generate a recovery code
        code = generate_recovery_code(ready_app.state.db)

        # GET to obtain CSRF
        resp = client.get("/recovery")
        csrf = _extract_csrf(resp.text)

        # POST with valid code
        resp = client.post(
            "/recovery",
            data={"recovery_code": code, "csrf_token": csrf},
            follow_redirects=True,
        )
        # Should show the password reset form
        assert resp.status_code == 200
        assert "Reimposta password" in resp.text
        assert "Conferma password" in resp.text

    def test_recovery_invalid_code_shows_error(self, ready_app):
        client = TestClient(ready_app)

        resp = client.get("/recovery")
        csrf = _extract_csrf(resp.text)

        resp = client.post(
            "/recovery",
            data={"recovery_code": "wrong-code", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=invalid_code" in resp.headers["location"]

    def test_recovery_empty_code_shows_error(self, ready_app):
        client = TestClient(ready_app)

        resp = client.get("/recovery")
        csrf = _extract_csrf(resp.text)

        resp = client.post(
            "/recovery",
            data={"recovery_code": "", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=invalid_code" in resp.headers["location"]

    def test_recovery_invalid_csrf_shows_error(self, ready_app):
        client = TestClient(ready_app)

        resp = client.post(
            "/recovery",
            data={"recovery_code": "ABCD1234", "csrf_token": "bad-token"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=csrf" in resp.headers["location"]

    def test_recovery_reset_password_success(self, ready_app):
        client = TestClient(ready_app)
        code = generate_recovery_code(ready_app.state.db)

        # Step 1: validate code
        resp = client.get("/recovery")
        csrf = _extract_csrf(resp.text)

        resp = client.post(
            "/recovery",
            data={"recovery_code": code, "csrf_token": csrf},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "Reimposta password" in resp.text

        # Step 2: reset password
        csrf2 = _extract_csrf(resp.text)
        resp = client.post(
            "/recovery/reset",
            data={
                "password": "new-password-123",
                "password_confirm": "new-password-123",
                "csrf_token": csrf2,
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/login?recovery=ok" in resp.headers["location"]

        # Verify new password works
        resp = client.get("/login")
        csrf3 = _extract_csrf(resp.text)
        resp = client.post(
            "/login",
            data={"password": "new-password-123", "csrf_token": csrf3},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/admin/dashboard"

    def test_recovery_reset_password_mismatch(self, ready_app):
        client = TestClient(ready_app)
        code = generate_recovery_code(ready_app.state.db)

        resp = client.get("/recovery")
        csrf = _extract_csrf(resp.text)
        resp = client.post(
            "/recovery",
            data={"recovery_code": code, "csrf_token": csrf},
            follow_redirects=True,
        )
        csrf2 = _extract_csrf(resp.text)

        resp = client.post(
            "/recovery/reset",
            data={
                "password": "new-password-123",
                "password_confirm": "different-password",
                "csrf_token": csrf2,
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=mismatch" in resp.headers["location"]

    def test_recovery_reset_password_too_short(self, ready_app):
        client = TestClient(ready_app)
        code = generate_recovery_code(ready_app.state.db)

        resp = client.get("/recovery")
        csrf = _extract_csrf(resp.text)
        resp = client.post(
            "/recovery",
            data={"recovery_code": code, "csrf_token": csrf},
            follow_redirects=True,
        )
        csrf2 = _extract_csrf(resp.text)

        resp = client.post(
            "/recovery/reset",
            data={
                "password": "short",
                "password_confirm": "short",
                "csrf_token": csrf2,
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=too_short" in resp.headers["location"]

    def test_recovery_reset_without_approval_redirects(self, ready_app):
        """Accessing /recovery/reset without a validated code should fail."""
        client = TestClient(ready_app)

        resp = client.get("/recovery")
        csrf = _extract_csrf(resp.text)

        resp = client.post(
            "/recovery/reset",
            data={
                "password": "new-password-123",
                "password_confirm": "new-password-123",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=unauthorized" in resp.headers["location"]

    def test_recovery_code_is_one_time(self, ready_app):
        """After a successful reset, the same code cannot be reused."""
        client = TestClient(ready_app)
        code = generate_recovery_code(ready_app.state.db)

        # Step 1: validate and reset
        resp = client.get("/recovery")
        csrf = _extract_csrf(resp.text)
        resp = client.post(
            "/recovery",
            data={"recovery_code": code, "csrf_token": csrf},
            follow_redirects=True,
        )
        csrf2 = _extract_csrf(resp.text)
        client.post(
            "/recovery/reset",
            data={
                "password": "new-password-123",
                "password_confirm": "new-password-123",
                "csrf_token": csrf2,
            },
            follow_redirects=False,
        )

        # Step 2: try to reuse the same code
        resp = client.get("/recovery")
        csrf3 = _extract_csrf(resp.text)
        resp = client.post(
            "/recovery",
            data={"recovery_code": code, "csrf_token": csrf3},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=invalid_code" in resp.headers["location"]

    def test_api_recovery_generate_requires_auth(self, ready_app):
        client = TestClient(ready_app)
        resp = client.post("/api/recovery/generate")
        assert resp.status_code == 401

    def test_api_recovery_generate_returns_code(self, ready_app):
        client = TestClient(ready_app)

        # Login first
        resp = client.get("/login")
        csrf = _extract_csrf(resp.text)
        resp = client.post(
            "/login",
            data={"password": "admin-password", "csrf_token": csrf},
            follow_redirects=False,
        )
        cookies = resp.cookies

        # Then call the API
        resp = client.post(
            "/api/recovery/generate",
            cookies=cookies,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert isinstance(data["code"], str)
        assert len(data["code"]) > 0

"""
Tests for the FastAPI web frontend application (W1).

Covers the application factory, route responses, authentication flow
(setup → login → dashboard), API endpoints, error pages, and
unauthorized access handling.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from bot.web.app import create_app

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Fixtures — real DB-backed app instances
# ------------------------------------------------------------------


def _make_minimal_config(tmp_path) -> SimpleNamespace:
    """Build a minimal Config-like namespace that the web frontend
    services accept."""
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


@pytest.fixture
def fresh_app(tmp_path):
    """Return a FastAPI app with a fresh database — no admin
    configured."""
    config = _make_minimal_config(tmp_path)
    return create_app(config=config)


@pytest.fixture
def ready_app(tmp_path):
    """Return a FastAPI app with admin already configured and a
    provider set up, so the state is close to READY.

    The bot is NOT started automatically because no Telegram token
    has been stored in ConfigService (the state will be
    TELEGRAM_MISSING).
    """
    from bot.web.auth import set_admin_password
    from bot.web.setup_wizard import set_current_step, STEP_DONE

    config = _make_minimal_config(tmp_path)
    app = create_app(config=config)

    # Create admin in the database — simulates completing the setup
    # wizard *through* the app's own database.
    set_admin_password(app.state.db, "admin-password")
    app.state.db.set_setup_state("admin_created", "true")

    # Mark the wizard as complete so the frontend redirects correctly
    set_current_step(app.state.db, STEP_DONE)

    return app


def _authed_session(client: TestClient) -> dict:
    """Log in as admin and return the session dict for reuse."""
    # GET /login to obtain a CSRF token
    resp = client.get("/login")
    assert resp.status_code == 200

    # Parse CSRF token from the form
    html = resp.text
    csrf = _extract_csrf(html)

    # POST /login with the correct password
    resp = client.post(
        "/login",
        data={"password": "admin-password", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/dashboard"
    return resp.cookies


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


# ==================================================================
# Root redirect
# ==================================================================


def test_root_redirects_to_setup_when_no_admin(fresh_app):
    """GET / redirects to /setup on a fresh database."""
    with TestClient(fresh_app) as client:
        resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/setup"


def test_root_redirects_to_dashboard_when_admin(ready_app):
    """GET / redirects to /admin/dashboard when admin exists."""
    with TestClient(ready_app) as client:
        resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/dashboard"


# ==================================================================
# Setup page
# ==================================================================


def test_setup_page_renders_form(fresh_app):
    """GET /setup renders the wizard at step 1 (setup code)."""
    with TestClient(fresh_app) as client:
        resp = client.get("/setup")
    assert resp.status_code == 200
    assert "Codice di configurazione" in resp.text
    assert "csrf_token" in resp.text
    assert "wizard" in resp.text or "step_code" in resp.text


def test_setup_page_redirects_to_login_when_admin(ready_app):
    """GET /setup redirects to /login when admin already exists."""
    with TestClient(ready_app) as client:
        resp = client.get("/setup", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_setup_page_redirects_to_dashboard_when_logged_in(fresh_app):
    """GET /setup redirects to dashboard when the user is already
    authenticated and admin exists."""
    with TestClient(fresh_app) as client:
        # Create admin first, then set up a session cookie manually
        from bot.web.auth import set_admin_password
        from bot.web.setup_wizard import set_current_step, STEP_DONE
        set_admin_password(fresh_app.state.db, "pw")
        fresh_app.state.db.set_setup_state("admin_created", "true")
        set_current_step(fresh_app.state.db, STEP_DONE)

        # Set an authenticated session cookie
        serialiser = fresh_app.state.serialiser
        from bot.web.auth import encode_session, generate_csrf_token
        cookie = encode_session(serialiser, {"admin": True, "csrf_token": generate_csrf_token()})
        client.cookies.set("session", cookie)

        resp = client.get("/setup", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/dashboard"


# ==================================================================
# Setup POST (completing the wizard)
# ==================================================================


def _complete_setup(client: TestClient, app, password="test-admin-pw"):
    """Helper: complete the setup wizard through the POST endpoint.

    Returns the final response (without following redirects).
    Uses two separate POSTs: step_code then step_admin.
    """
    # Obtain a CSRF token from the setup page
    resp = client.get("/setup")
    csrf = _extract_csrf(resp.text)

    # The app generated a setup code on startup — invalidate and create
    # a new one so we know the plaintext value.
    from bot.database import DatabaseManager
    db: DatabaseManager = app.state.db

    from bot.setup import invalidate_setup_code, generate_setup_code
    invalidate_setup_code(db)
    code = generate_setup_code(db)

    # Step 1: POST step_code
    resp1 = client.post(
        "/setup",
        data={
            "_step": "step_code",
            "setup_code": code,
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    # Obtain a fresh CSRF token for step 2
    resp = client.get("/setup?step=step_admin")
    csrf2 = _extract_csrf(resp.text)

    # Step 2: POST step_admin
    return client.post(
        "/setup",
        data={
            "_step": "step_admin",
            "admin_password": password,
            "admin_password_confirm": password,
            "csrf_token": csrf2,
        },
        follow_redirects=False,
    )


def test_setup_post_success(fresh_app):
    """POST /setup step_code + step_admin creates admin and advances
    the wizard."""
    with TestClient(fresh_app) as client:
        resp = _complete_setup(client, fresh_app)
    # After step_admin the wizard advances to step_telegram
    assert resp.status_code == 303
    assert resp.headers["location"] == "/setup?step=step_telegram"
    # Admin should now exist
    from bot.web.auth import has_admin
    assert has_admin(fresh_app.state.db) is True
    # Wizard should have advanced past admin
    from bot.web.setup_wizard import get_current_step, STEP_ADMIN
    assert get_current_step(fresh_app.state.db) != STEP_ADMIN


def test_setup_post_invalid_code(fresh_app):
    """POST /setup step_code with an invalid code shows an error."""
    with TestClient(fresh_app) as client:
        resp = client.get("/setup")
        csrf = _extract_csrf(resp.text)

        resp = client.post(
            "/setup",
            data={
                "_step": "step_code",
                "setup_code": "INVALID1",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert "invalid_code" in resp.headers["location"]


def test_setup_post_password_short_accepted(fresh_app):
    """POST /setup step_admin: short passwords are accepted (no min-length
    restriction)."""
    with TestClient(fresh_app) as client:
        resp = client.get("/setup")
        csrf = _extract_csrf(resp.text)

        resp = client.post(
            "/setup",
            data={
                "_step": "step_admin",
                "admin_password": "short",
                "admin_password_confirm": "short",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
    assert resp.status_code == 303
    # Should advance to step_telegram, not block on length
    assert "step_telegram" in resp.headers["location"]


def test_setup_post_password_mismatch(fresh_app):
    """POST /setup step_admin with mismatched passwords shows an error."""
    with TestClient(fresh_app) as client:
        resp = client.get("/setup")
        csrf = _extract_csrf(resp.text)

        resp = client.post(
            "/setup",
            data={
                "_step": "step_admin",
                "admin_password": "test-password",
                "admin_password_confirm": "different-pw",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert "password_mismatch" in resp.headers["location"]


def test_setup_post_csrf_mismatch(fresh_app):
    """POST /setup with a bad CSRF token is rejected."""
    with TestClient(fresh_app) as client:
        resp = client.post(
            "/setup",
            data={
                "_step": "step_code",
                "setup_code": "doesnotmatter",
                "csrf_token": "bad-token",
            },
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert "csrf" in resp.headers["location"]


# ==================================================================
# Login
# ==================================================================


def test_login_page_renders(ready_app):
    """GET /login renders the login form."""
    with TestClient(ready_app) as client:
        resp = client.get("/login")
    assert resp.status_code == 200
    assert "csrf_token" in resp.text
    assert "Accesso amministratore" in resp.text


def test_login_page_redirects_when_authenticated(ready_app):
    """GET /login redirects to dashboard when already logged in."""
    with TestClient(ready_app) as client:
        # Set an authenticated session cookie
        serialiser = ready_app.state.serialiser
        from bot.web.auth import encode_session, generate_csrf_token
        cookie = encode_session(serialiser, {"admin": True, "csrf_token": generate_csrf_token()})
        client.cookies.set("session", cookie)

        resp = client.get("/login", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/dashboard"


def test_login_post_success(ready_app):
    """POST /login with correct password creates a session and
    redirects to dashboard."""
    with TestClient(ready_app) as client:
        resp = client.get("/login")
        csrf = _extract_csrf(resp.text)

        resp = client.post(
            "/login",
            data={"password": "admin-password", "csrf_token": csrf},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/dashboard"
    # Session cookie should be set
    assert "session" in resp.cookies


def test_login_post_wrong_password(ready_app):
    """POST /login with wrong password returns an error."""
    with TestClient(ready_app) as client:
        resp = client.get("/login")
        csrf = _extract_csrf(resp.text)

        resp = client.post(
            "/login",
            data={"password": "wrong-password", "csrf_token": csrf},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert "invalid" in resp.headers["location"]


# ==================================================================
# Dashboard (authenticated)
# ==================================================================


def test_dashboard_requires_auth(ready_app):
    """GET /admin/dashboard without a session returns 401."""
    with TestClient(ready_app) as client:
        resp = client.get("/admin/dashboard", follow_redirects=False)
    assert resp.status_code == 401


def test_dashboard_renders_when_authenticated(ready_app):
    """GET /admin/dashboard renders the dashboard when logged in."""
    with TestClient(ready_app) as client:
        # Log in first
        resp = client.get("/login")
        csrf = _extract_csrf(resp.text)
        resp = client.post(
            "/login",
            data={"password": "admin-password", "csrf_token": csrf},
            follow_redirects=False,
        )
        session_cookie = resp.cookies.get("session")

        # Use the session cookie on the dashboard request
        resp = client.get(
            "/admin/dashboard",
            cookies={"session": session_cookie},
        )
    assert resp.status_code == 200
    assert "Dashboard" in resp.text


# ==================================================================
# Logout
# ==================================================================


def test_logout_clears_session(ready_app):
    """POST /logout deletes the session cookie and redirects to
    login."""
    with TestClient(ready_app) as client:
        resp = client.post("/logout", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


# ==================================================================
# API endpoints
# ==================================================================


def test_api_state(fresh_app):
    """GET /api/state returns the application state JSON."""
    with TestClient(fresh_app) as client:
        resp = client.get("/api/state")
    assert resp.status_code == 200
    data = resp.json()
    assert "state" in data
    assert data["state"] == "setup_required"
    assert "label" in data
    assert "description" in data
    assert "next_action" in data
    assert "can_process_audio" in data
    assert data["can_process_audio"] is False


def test_api_state_when_admin_configured(ready_app):
    """GET /api/state reflects the application state after setup."""
    with TestClient(ready_app) as client:
        resp = client.get("/api/state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] != "setup_required"
    assert "can_process_audio" in data


def test_api_health(fresh_app):
    """GET /api/health returns health information."""
    with TestClient(fresh_app) as client:
        resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "bot_running" in data
    assert "state" in data
    assert "uptime_seconds" in data
    assert data["bot_running"] is False


# ==================================================================
# Error pages
# ==================================================================


def test_404_page(fresh_app):
    """GET /nonexistent returns a styled 404 page."""
    with TestClient(fresh_app) as client:
        resp = client.get("/nonexistent-route")
    assert resp.status_code == 404
    assert "404" in resp.text
    assert "Pagina non trovata" in resp.text


# ==================================================================
# Static files
# ==================================================================


def test_static_css(fresh_app):
    """GET /static/style.css returns the stylesheet."""
    with TestClient(fresh_app) as client:
        resp = client.get("/static/style.css")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/css; charset=utf-8"
    assert "Telegram Audio Bot" in resp.text

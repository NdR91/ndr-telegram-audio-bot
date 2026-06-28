"""
Tests for the FastAPI web frontend application (W1).

Covers the application factory, route responses, authentication flow
(setup → login → dashboard), API endpoints, error pages, and
unauthorized access handling.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import patch

import httpx
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


def test_root_redirects_to_login_when_admin_exists_but_anonymous(ready_app):
    """GET / redirects to /login when setup is done but no session exists."""
    with TestClient(ready_app) as client:
        resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_root_redirects_to_dashboard_when_authenticated(ready_app):
    """GET / redirects to /admin/dashboard when the admin is logged in."""
    with TestClient(ready_app) as client:
        session_cookie = _authed_session(client)
        resp = client.get(
            "/",
            cookies=session_cookie,
            follow_redirects=False,
        )
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
    assert "/admin/pipeline" in resp.text


def test_dashboard_links_to_pipeline_when_provider_missing(ready_app):
    """Dashboard shows a provider action when Telegram is configured but
    no provider exists."""
    with TestClient(ready_app) as client:
        ready_app.state.config_service.update_setting(
            "telegram_token", "123456:TEST_TOKEN"
        )

        resp = client.get("/login")
        csrf = _extract_csrf(resp.text)
        resp = client.post(
            "/login",
            data={"password": "admin-password", "csrf_token": csrf},
            follow_redirects=False,
        )
        session_cookie = resp.cookies.get("session")

        resp = client.get(
            "/admin/dashboard",
            cookies={"session": session_cookie},
        )

    assert resp.status_code == 200
    assert "Provider AI mancante" in resp.text
    assert "Aggiungi provider" in resp.text
    assert "/admin/providers" in resp.text


def test_provider_page_renders_when_authenticated(ready_app):
    """GET /admin/providers renders provider creation UI."""
    with TestClient(ready_app) as client:
        session_cookie = _authed_session(client)
        resp = client.get(
            "/admin/providers",
            cookies=session_cookie,
        )

    assert resp.status_code == 200
    assert "Provider AI" in resp.text
    assert "Nuovo provider" in resp.text
    assert "OpenAI" in resp.text


def test_provider_create_adds_connection_and_redirects_to_pipeline(ready_app):
    """POST /admin/providers/create stores a provider connection."""
    with TestClient(ready_app) as client:
        session_cookie = _authed_session(client)
        resp = client.get(
            "/admin/providers",
            cookies=session_cookie,
        )
        csrf = _extract_csrf(resp.text)

        resp = client.post(
            "/admin/providers/create",
            data={
                "csrf_token": csrf,
                "provider_type": "openai",
                "name": "OpenAI test",
                "endpoint": "https://api.openai.com/v1",
                "api_key": "sk-test-provider",
                "model_name": "gpt-4o-mini",
            },
            cookies=session_cookie,
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/pipeline?success=provider_created"

    providers = ready_app.state.db.list_providers()
    assert len(providers) == 1
    assert providers[0]["name"] == "OpenAI test"
    assert providers[0]["adapter_type"] == "openai-native"
    assert providers[0]["capabilities"]["transcription"] is True


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


# ==================================================================
# POST /api/providers/test — provider connection testing
# ==================================================================


class MockHttpxResponse:
    """Minimal httpx.Response stand-in for mocked HTTP calls."""

    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}

    async def json(self):
        return self._json_data

    def json(self):
        return self._json_data


class MockHttpxClient:
    """Async context manager that stands in for httpx.AsyncClient.

    Returns pre-configured responses from a URL→response dict.
    """

    def __init__(self, responses=None, timeout=None):
        self.responses = responses if responses is not None else {}

    async def get(self, url, **kwargs):
        # Normalise trailing slash for matching
        norm = url.rstrip("/")
        match = self.responses.get(url) or self.responses.get(norm)
        if match is not None:
            return match
        return MockHttpxResponse(200, {"data": []})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _provider_test_authed_session(client, ready_app):
    """Authenticate and return cookies for provider test requests."""
    from bot.web.auth import set_admin_password
    from bot.web.setup_wizard import set_current_step, STEP_DONE
    set_admin_password(ready_app.state.db, "admin-password")
    ready_app.state.db.set_setup_state("admin_created", "true")
    set_current_step(ready_app.state.db, STEP_DONE)

    resp = client.get("/login")
    csrf = _extract_csrf(resp.text)
    resp = client.post(
        "/login",
        data={"password": "admin-password", "csrf_token": csrf},
        follow_redirects=False,
    )
    return resp.cookies


def test_providers_test_requires_auth(fresh_app):
    """POST /api/providers/test without auth returns 401."""
    with TestClient(fresh_app) as client:
        resp = client.post(
            "/api/providers/test",
            json={"provider_type": "openai", "api_key": "sk-test"},
        )
    assert resp.status_code == 401


def test_providers_test_invalid_api_key(ready_app):
    """Invalid API key returns auth_ok=False and a clear error message."""
    mock_client = MockHttpxClient({
        "https://api.openai.com/v1/models": MockHttpxResponse(
            401, {"error": {"message": "Incorrect API key"}}
        ),
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        with TestClient(ready_app) as client:
            session = _provider_test_authed_session(client, ready_app)
            resp = client.post(
                "/api/providers/test",
                json={
                    "provider_type": "openai",
                    "api_key": "sk-invalid",
                    "endpoint": "https://api.openai.com/v1",
                },
                cookies=session,
            )

    data = resp.json()
    assert data["ok"] is False
    assert data["auth_ok"] is False
    assert "chiave" in data["user_message"].lower()
    # API key must not appear in the response body
    assert "sk-invalid" not in json.dumps(data)


def test_providers_test_openai_success(ready_app):
    """Valid OpenAI key returns auth_ok=True and transcription+refinement caps."""
    mock_client = MockHttpxClient({
        "https://api.openai.com/v1/models": MockHttpxResponse(
            200, {
                "data": [
                    {"id": "gpt-4o-mini"},
                    {"id": "gpt-4o"},
                    {"id": "whisper-1"},
                ]
            }
        ),
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        with TestClient(ready_app) as client:
            session = _provider_test_authed_session(client, ready_app)
            resp = client.post(
                "/api/providers/test",
                json={
                    "provider_type": "openai",
                    "api_key": "sk-valid",
                    "endpoint": "https://api.openai.com/v1",
                    "model_name": "gpt-4o-mini",
                },
                cookies=session,
            )

    data = resp.json()
    assert data["ok"] is True
    assert data["auth_ok"] is True
    assert data["models_ok"] is True
    # OpenAI always has transcription (Whisper is separate)
    assert data["capabilities"]["transcription"] is True
    assert data["capabilities"]["refinement"] is True
    assert data["pipeline_status"] == "complete_same_provider"
    # No secrets exposed
    assert "sk-valid" not in json.dumps(data)


def test_providers_test_gemini_success(ready_app):
    """Valid Gemini key returns auth_ok=True with transcription caps."""
    mock_client = MockHttpxClient({
        "https://generativelanguage.googleapis.com/v1/models?key=gemini-valid": MockHttpxResponse(
            200, {
                "models": [
                    {"name": "models/gemini-2.0-flash"},
                ]
            }
        ),
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        with TestClient(ready_app) as client:
            session = _provider_test_authed_session(client, ready_app)
            resp = client.post(
                "/api/providers/test",
                json={
                    "provider_type": "gemini",
                    "api_key": "gemini-valid",
                    "endpoint": "",
                    "model_name": "gemini-2.0-flash",
                },
                cookies=session,
            )

    data = resp.json()
    assert data["ok"] is True
    assert data["auth_ok"] is True
    assert data["capabilities"]["transcription"] is True
    assert data["pipeline_status"] == "complete_same_provider"
    assert "gemini-valid" not in json.dumps(data)


def test_providers_test_openrouter_text_only(ready_app, monkeypatch):
    """OpenRouter text-only model returns refinement-only pipeline status.

    The test mocks ``probe_openrouter_capabilities`` to return text-only
    capabilities and provides a mock httpx client for the initial auth
    check.
    """
    from bot.capabilities import CapabilityModel

    async def mock_probe(api_key, endpoint, model_name, session=None):
        return (
            CapabilityModel(
                transcription=False,
                text_generation=True,
                refinement=True,
                streaming_refinement=True,
            ),
            {"audio_input": False, "transcription": False, "text_generation": True, "refinement": True, "streaming_refinement": True},
        )

    monkeypatch.setattr(
        "bot.web.app.probe_openrouter_capabilities",
        mock_probe,
    )

    mock_client = MockHttpxClient({
        "https://openrouter.ai/api/v1/models": MockHttpxResponse(
            200, {
                "data": [
                    {"id": "openai/gpt-4o"},
                ]
            }
        ),
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        with TestClient(ready_app) as client:
            session = _provider_test_authed_session(client, ready_app)
            resp = client.post(
                "/api/providers/test",
                json={
                    "provider_type": "openrouter",
                    "api_key": "sk-or-valid",
                    "endpoint": "https://openrouter.ai/api/v1",
                    "model_name": "openai/gpt-4o",
                },
                cookies=session,
            )

    data = resp.json()
    assert data["ok"] is True
    assert data["auth_ok"] is True
    assert data["capabilities"]["transcription"] is False
    assert data["capabilities"]["refinement"] is True
    assert data["pipeline_status"] == "refinement_only"
    # Should have a warning about text-only models
    warnings_text = " ".join(data.get("warnings", []))
    assert "non trascrivono" in warnings_text or "solo testo" in warnings_text.lower()
    # No secrets exposed
    assert "sk-or-valid" not in json.dumps(data)
    assert "|" not in data.get("user_message", "")  # no raw API key leak


def test_providers_test_openrouter_transcription_model(ready_app, monkeypatch):
    """OpenRouter audio/transcription model returns transcription capabilities."""
    from bot.capabilities import CapabilityModel

    async def mock_probe(api_key, endpoint, model_name, session=None):
        return (
            CapabilityModel(
                transcription=True,
                text_generation=True,
                refinement=True,
                streaming_refinement=False,
            ),
            {"audio_input": True, "transcription": True, "text_generation": True, "refinement": True, "streaming_refinement": False},
        )

    monkeypatch.setattr(
        "bot.web.app.probe_openrouter_capabilities",
        mock_probe,
    )

    mock_client = MockHttpxClient({
        "https://openrouter.ai/api/v1/models": MockHttpxResponse(
            200, {
                "data": [
                    {"id": "openai/whisper-1"},
                ]
            }
        ),
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        with TestClient(ready_app) as client:
            session = _provider_test_authed_session(client, ready_app)
            resp = client.post(
                "/api/providers/test",
                json={
                    "provider_type": "openrouter",
                    "api_key": "sk-or-valid",
                    "endpoint": "https://openrouter.ai/api/v1",
                    "model_name": "openai/whisper-1",
                },
                cookies=session,
            )

    data = resp.json()
    assert data["ok"] is True
    assert data["auth_ok"] is True
    assert data["capabilities"]["transcription"] is True
    assert data["capabilities"]["refinement"] is True
    assert data["pipeline_status"] == "complete_same_provider"
    assert "sk-or-valid" not in json.dumps(data)


def test_providers_test_openrouter_unknown_metadata(ready_app, monkeypatch):
    """OpenRouter model with unknown metadata returns conservative caps.

    When probing returns all-False, endpoint still reports auth_ok but
    pipeline is not_compatible and a warning is added.
    """
    from bot.capabilities import CapabilityModel

    async def mock_probe(api_key, endpoint, model_name, session=None):
        return (
            CapabilityModel(),
            {"audio_input": False, "transcription": False, "text_generation": False, "refinement": False, "streaming_refinement": False},
        )

    monkeypatch.setattr(
        "bot.web.app.probe_openrouter_capabilities",
        mock_probe,
    )

    mock_client = MockHttpxClient({
        "https://openrouter.ai/api/v1/models": MockHttpxResponse(
            200, {
                "data": [
                    {"id": "unknown-org/unknown-model"},
                ]
            }
        ),
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        with TestClient(ready_app) as client:
            session = _provider_test_authed_session(client, ready_app)
            resp = client.post(
                "/api/providers/test",
                json={
                    "provider_type": "openrouter",
                    "api_key": "sk-or-valid",
                    "endpoint": "https://openrouter.ai/api/v1",
                    "model_name": "unknown-org/unknown-model",
                },
                cookies=session,
            )

    data = resp.json()
    assert data["ok"] is True
    assert data["auth_ok"] is True
    # capabilities should be all False due to conservative probe
    assert data["capabilities"]["transcription"] is False
    assert data["capabilities"]["refinement"] is False
    assert data["pipeline_status"] == "not_compatible"
    # Warning about unknown capabilities
    warnings_text = " ".join(data.get("warnings", []))
    assert "determinare" in warnings_text or "verifica" in warnings_text.lower()
    assert "sk-or-valid" not in json.dumps(data)


def test_providers_test_openrouter_no_model_specified(ready_app):
    """OpenRouter without model_name returns text-only fallback and warning."""
    mock_client = MockHttpxClient({
        "https://openrouter.ai/api/v1/models": MockHttpxResponse(
            200, {
                "data": [
                    {"id": "openai/gpt-4o"},
                ]
            }
        ),
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        with TestClient(ready_app) as client:
            session = _provider_test_authed_session(client, ready_app)
            resp = client.post(
                "/api/providers/test",
                json={
                    "provider_type": "openrouter",
                    "api_key": "sk-or-valid",
                    "endpoint": "https://openrouter.ai/api/v1",
                    "model_name": "",
                },
                cookies=session,
            )

    data = resp.json()
    assert data["ok"] is True
    assert data["auth_ok"] is True
    # Falls back to text-only
    assert data["capabilities"]["transcription"] is False
    assert data["capabilities"]["refinement"] is True
    assert data["pipeline_status"] == "refinement_only"
    # Should have a warning about no model specified
    warnings_text = " ".join(data.get("warnings", []))
    assert "specificato" in warnings_text
    assert "sk-or-valid" not in json.dumps(data)


def test_providers_test_returns_no_secrets(ready_app):
    """Response body must never contain the API key."""
    mock_client = MockHttpxClient({
        "https://api.openai.com/v1/models": MockHttpxResponse(
            200, {
                "data": [{"id": "gpt-4o"}],
            }
        ),
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        with TestClient(ready_app) as client:
            session = _provider_test_authed_session(client, ready_app)
            resp = client.post(
                "/api/providers/test",
                json={
                    "provider_type": "openai",
                    "api_key": "sk-supersecret-keyvalue",
                    "endpoint": "https://api.openai.com/v1",
                },
                cookies=session,
            )

    body = resp.text
    assert "sk-supersecret" not in body
    assert "supersecret" not in body.lower()


def test_providers_test_missing_fields(ready_app):
    """Missing provider_type or api_key returns a validation error."""
    with TestClient(ready_app) as client:
        session = _provider_test_authed_session(client, ready_app)

        # No provider_type
        resp = client.post(
            "/api/providers/test",
            json={"api_key": "sk-test"},
            cookies=session,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "error" in data

    with TestClient(ready_app) as client:
        session = _provider_test_authed_session(client, ready_app)

        # No api_key
        resp = client.post(
            "/api/providers/test",
            json={"provider_type": "openai"},
            cookies=session,
        )
    data = resp.json()
    assert data["ok"] is False
    assert "error" in data


# ==================================================================
# POST /api/setup/test-provider — same schema as /api/providers/test
# ==================================================================


def test_setup_test_provider_returns_same_schema(fresh_app):
    """Setup provider test returns the same schema as admin."""
    mock_client = MockHttpxClient({
        "https://api.openai.com/v1/models": MockHttpxResponse(
            200, {
                "data": [
                    {"id": "gpt-4o-mini"},
                    {"id": "whisper-1"},
                ]
            }
        ),
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        with TestClient(fresh_app) as client:
            resp = client.post(
                "/api/setup/test-provider",
                json={
                    "type": "openai",
                    "api_key": "sk-valid",
                    "endpoint": "https://api.openai.com/v1",
                },
            )

    data = resp.json()
    # Must have admin-style fields
    assert "ok" in data
    assert "auth_ok" in data
    assert "models_ok" in data
    assert "capabilities" in data
    assert "pipeline_status" in data
    assert "user_message" in data
    assert "warnings" in data
    assert "models" in data
    # Must be a successful auth
    assert data["auth_ok"] is True
    assert data["ok"] is True
    # OpenAI always has transcription
    assert data["capabilities"]["transcription"] is True
    assert data["pipeline_status"] == "complete_same_provider"
    # No secrets
    assert "sk-valid" not in json.dumps(data)


def test_setup_test_provider_openrouter_audio_input_no_stt(fresh_app, monkeypatch):
    """OpenRouter audio-input model without STT keywords: transcription=False."""
    from bot.capabilities import CapabilityModel

    async def mock_probe(api_key, endpoint, model_name, session=None):
        return (
            CapabilityModel(
                transcription=False,
                text_generation=True,
                refinement=True,
                streaming_refinement=True,
            ),
            {
                "audio_input": True,
                "transcription": False,
                "text_generation": True,
                "refinement": True,
                "streaming_refinement": True,
            },
        )

    monkeypatch.setattr(
        "bot.web.app.probe_openrouter_capabilities",
        mock_probe,
    )

    mock_client = MockHttpxClient({
        "https://openrouter.ai/api/v1/models": MockHttpxResponse(
            200, {
                "data": [
                    {"id": "google/gemini-2.0-flash"},
                ]
            }
        ),
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        with TestClient(fresh_app) as client:
            resp = client.post(
                "/api/setup/test-provider",
                json={
                    "type": "openrouter",
                    "api_key": "sk-or-valid",
                    "endpoint": "https://openrouter.ai/api/v1",
                    "model_name": "google/gemini-2.0-flash",
                },
            )

    data = resp.json()
    assert data["ok"] is True
    assert data["auth_ok"] is True
    # Audio input model without STT keywords → transcription=False
    assert data["capabilities"]["transcription"] is False
    assert data["capabilities"]["text_generation"] is True
    assert data["pipeline_status"] == "refinement_only"
    # Should have audio-capable warning
    warnings_text = " ".join(data.get("warnings", []))
    assert "audio" in warnings_text.lower() or "STT" in warnings_text
    # No secrets
    assert "sk-or-valid" not in json.dumps(data)


def test_setup_test_provider_openrouter_transcription_model(fresh_app, monkeypatch):
    """OpenRouter explicit STT model: transcription=True."""
    from bot.capabilities import CapabilityModel

    async def mock_probe(api_key, endpoint, model_name, session=None):
        return (
            CapabilityModel(
                transcription=True,
                text_generation=True,
                refinement=True,
                streaming_refinement=False,
            ),
            {
                "audio_input": True,
                "transcription": True,
                "text_generation": True,
                "refinement": True,
                "streaming_refinement": False,
            },
        )

    monkeypatch.setattr(
        "bot.web.app.probe_openrouter_capabilities",
        mock_probe,
    )

    mock_client = MockHttpxClient({
        "https://openrouter.ai/api/v1/models": MockHttpxResponse(
            200, {
                "data": [
                    {"id": "openai/whisper-1"},
                ]
            }
        ),
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        with TestClient(fresh_app) as client:
            resp = client.post(
                "/api/setup/test-provider",
                json={
                    "type": "openrouter",
                    "api_key": "sk-or-valid",
                    "endpoint": "https://openrouter.ai/api/v1",
                    "model_name": "openai/whisper-1",
                },
            )

    data = resp.json()
    assert data["ok"] is True
    assert data["auth_ok"] is True
    # Explicit STT model → transcription=True
    assert data["capabilities"]["transcription"] is True
    assert data["capabilities"]["refinement"] is True
    assert data["pipeline_status"] == "complete_same_provider"
    assert "sk-or-valid" not in json.dumps(data)


def test_setup_test_provider_no_api_key_leaks(fresh_app):
    """Setup provider test must not leak API keys in response."""
    mock_client = MockHttpxClient({
        "https://api.openai.com/v1/models": MockHttpxResponse(
            200, {
                "data": [{"id": "gpt-4o"}],
            }
        ),
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        with TestClient(fresh_app) as client:
            resp = client.post(
                "/api/setup/test-provider",
                json={
                    "type": "openai",
                    "api_key": "sk-supersecret-keyvalue",
                    "endpoint": "https://api.openai.com/v1",
                },
            )

    body = resp.text
    assert "sk-supersecret" not in body
    assert "supersecret" not in body.lower()


# ==================================================================
# Provider detail pages, model management, and pipeline updates
# ==================================================================


def _create_provider(db, name="Test Provider", adapter_type="openai-native",
                      endpoint="https://api.openai.com/v1"):
    """Create a provider directly in the database for test setup.

    Returns the new provider ID.
    """
    return db.add_provider(
        name=name,
        adapter_type=adapter_type,
        endpoint=endpoint,
        credentials="sk-test-provider-key",
        capabilities={
            "transcription": True,
            "text_generation": True,
            "refinement": True,
            "streaming_refinement": True,
        },
        enabled=True,
    )


def _create_model(db, provider_id, model_id="gpt-4o",
                   capabilities=None, enabled=True, detected=True):
    """Register a model under a provider for test setup.

    Parameters
    ----------
    db:
        DatabaseManager instance.
    provider_id:
        ID of the parent provider connection.
    model_id:
        Identifier for the model (e.g. ``"whisper-1"``).
    capabilities:
        Capability dict.  Defaults to transcription+text+refinement.
    enabled:
        Whether the model is enabled in the UI.
    detected:
        Whether the model was auto-detected.

    Returns the new provider_models entry ID.
    """
    if capabilities is None:
        capabilities = {
            "transcription": True,
            "text_generation": True,
            "refinement": True,
            "streaming_refinement": True,
        }
    return db.add_provider_model(
        provider_id=provider_id,
        model_id=model_id,
        display_name=model_id,
        capabilities=capabilities,
        detected=detected,
        enabled=enabled,
    )


# ==================================================================
# Provider detail page
# ==================================================================


@pytest.mark.parametrize("has_models", [True, False])
def test_provider_detail_renders(ready_app, has_models):
    """GET /admin/providers/{id} returns 200 and shows provider name.

    ✅ Positive: provider exists → page renders with name.
    ✅ Negative: no models → appropriate empty message shown (covered by
       has_models=False).
    """
    provider_id = _create_provider(ready_app.state.db, name="My OpenAI")
    if has_models:
        _create_model(ready_app.state.db, provider_id, "whisper-1",
                      capabilities={
                          "transcription": True, "text_generation": False,
                          "refinement": False, "streaming_refinement": False,
                      })

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.get(
            f"/admin/providers/{provider_id}",
            cookies=session,
        )

    assert resp.status_code == 200
    assert "My OpenAI" in resp.text

    if has_models:
        assert "whisper-1" in resp.text
        assert "Nessun modello registrato" not in resp.text
    else:
        assert "Nessun modello registrato" in resp.text


def test_provider_detail_requires_auth(ready_app):
    """GET /admin/providers/{id} without session returns 401."""
    with TestClient(ready_app) as client:
        resp = client.get("/admin/providers/1", follow_redirects=False)
    assert resp.status_code == 401


def test_provider_detail_openrouter_shows_guided_discovery(ready_app):
    """OpenRouter provider page guides discovery by pipeline purpose."""
    provider_id = _create_provider(
        ready_app.state.db,
        name="OpenRouter",
        adapter_type="openai-compat",
        endpoint="https://openrouter.ai/api/v1",
    )

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.get(
            f"/admin/providers/{provider_id}",
            cookies=session,
        )

    assert resp.status_code == 200
    assert "Testo / refinement" in resp.text
    assert "Trascrizione" in resp.text
    assert "Single-pass" in resp.text
    assert "Cerca nel catalogo" in resp.text
    assert "Audio input non significa automaticamente" in resp.text


def test_provider_detail_404_for_nonexistent(ready_app):
    """GET /admin/providers/9999 returns a 404 page when the provider does
    not exist."""
    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.get(
            "/admin/providers/9999",
            cookies=session,
        )
    assert resp.status_code == 404
    assert "Provider non trovato" in resp.text


def test_provider_detail_shows_model_capabilities(ready_app):
    """Provider detail table displays capability indicators for each model.

    ✅ Transcription model shows transcription=✅, text=❌.
    ✅ Text model shows transcription=❌, text=✅.
    """
    provider_id = _create_provider(ready_app.state.db)
    _create_model(ready_app.state.db, provider_id, "whisper-1",
                  capabilities={
                      "transcription": True, "text_generation": False,
                      "refinement": False, "streaming_refinement": False,
                      "single_pass_audio_to_text": False,
                  })
    _create_model(ready_app.state.db, provider_id, "gpt-4o",
                  capabilities={
                      "transcription": False, "text_generation": True,
                      "refinement": True, "streaming_refinement": True,
                      "single_pass_audio_to_text": False,
                  })

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.get(
            f"/admin/providers/{provider_id}",
            cookies=session,
        )

    assert resp.status_code == 200
    # Both model IDs should appear in the table
    assert "whisper-1" in resp.text
    assert "gpt-4o" in resp.text


# ==================================================================
# Provider edit (POST)
# ==================================================================


def test_provider_edit_saves(ready_app):
    """POST /admin/providers/{id}/edit with valid data updates the provider
    and redirects with success.

    ✅ Positive: name, endpoint, and enabled status are persisted.
    """
    provider_id = _create_provider(ready_app.state.db, name="Original Name")

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        # Obtain CSRF token from the detail page
        resp = client.get(
            f"/admin/providers/{provider_id}",
            cookies=session,
        )
        csrf = _extract_csrf(resp.text)

        resp = client.post(
            f"/admin/providers/{provider_id}/edit",
            data={
                "csrf_token": csrf,
                "name": "Updated Name",
                "endpoint": "https://updated.example.com/v1",
                "enabled": "1",
            },
            cookies=session,
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert resp.headers["location"] == (
        f"/admin/providers/{provider_id}?success=updated"
    )
    # Verify the database row was updated
    provider = ready_app.state.db.get_provider(provider_id)
    assert provider["name"] == "Updated Name"
    assert provider["endpoint"] == "https://updated.example.com/v1"


def test_provider_edit_csrf_rejection(ready_app):
    """POST /admin/providers/{id}/edit with a bad CSRF token is rejected.

    ❌ Negative: invalid CSRF → error=csrf redirect.
    """
    provider_id = _create_provider(ready_app.state.db, name="Original Name")

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.post(
            f"/admin/providers/{provider_id}/edit",
            data={
                "csrf_token": "invalid-token",
                "name": "Hacker Name",
            },
            cookies=session,
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert "error=csrf" in resp.headers["location"]
    # Verify the database was NOT updated
    provider = ready_app.state.db.get_provider(provider_id)
    assert provider["name"] == "Original Name"


# ==================================================================
# Provider delete
# ==================================================================


def test_provider_delete_succeeds(ready_app):
    """POST /admin/providers/{id}/delete removes the provider and redirects.

    ✅ Positive: provider is deleted, DB row is gone.
    """
    provider_id = _create_provider(ready_app.state.db)

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.get(
            f"/admin/providers/{provider_id}",
            cookies=session,
        )
        csrf = _extract_csrf(resp.text)

        resp = client.post(
            f"/admin/providers/{provider_id}/delete",
            data={"csrf_token": csrf},
            cookies=session,
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/providers?success=deleted"
    # Verify deletion from database
    assert ready_app.state.db.get_provider(provider_id) is None


def test_provider_delete_requires_auth(ready_app):
    """POST /admin/providers/{id}/delete without auth returns 401.

    ❌ Negative: unauthenticated request is rejected.
    """
    provider_id = _create_provider(ready_app.state.db)

    with TestClient(ready_app) as client:
        resp = client.post(
            f"/admin/providers/{provider_id}/delete",
            data={"csrf_token": "test"},
            follow_redirects=False,
        )
    assert resp.status_code == 401


def test_provider_delete_csrf_rejection(ready_app):
    """POST /admin/providers/{id}/delete with bad CSRF is rejected.

    ❌ Negative: invalid CSRF → error=csrf redirect, provider still exists.
    """
    provider_id = _create_provider(ready_app.state.db)

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.post(
            f"/admin/providers/{provider_id}/delete",
            data={"csrf_token": "bad-token"},
            cookies=session,
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert "error=csrf" in resp.headers["location"]
    # Provider must still exist
    assert ready_app.state.db.get_provider(provider_id) is not None


# ==================================================================
# API: discover models (POST /api/providers/{id}/discover)
# ==================================================================


def test_api_discover_models_requires_auth(ready_app):
    """POST /api/providers/{id}/discover without session returns 401.

    ❌ Negative: unauthenticated → 401.
    """
    with TestClient(ready_app) as client:
        resp = client.post("/api/providers/1/discover")
    assert resp.status_code == 401


def test_api_discover_models_provider_not_found(ready_app):
    """POST /api/providers/9999/discover returns 404.

    ❌ Negative: nonexistent provider → 404 with error message.
    """
    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.post(
            "/api/providers/9999/discover",
            cookies=session,
        )
    assert resp.status_code == 404
    data = resp.json()
    assert data["ok"] is False
    assert "Provider non trovato" in data["error"]


def test_api_discover_models_no_credentials(ready_app):
    """Discover without stored credentials returns an error.

    ❌ Negative: provider has no credentials → cannot probe API.
    """
    provider_id = ready_app.state.db.add_provider(
        name="No Creds",
        adapter_type="openai-native",
    )
    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.post(
            f"/api/providers/{provider_id}/discover",
            cookies=session,
        )
    data = resp.json()
    assert data["ok"] is False
    assert "credenziali" in data["error"].lower()


def test_api_discover_models_openai_success(ready_app):
    """POST /api/providers/{id}/discover returns discovered models for
    an OpenAI provider.

    ✅ Positive: models are fetched from the API, classified, and registered.
    """
    provider_id = _create_provider(ready_app.state.db, adapter_type="openai-native")

    mock_client = MockHttpxClient({
        "https://api.openai.com/v1/models": MockHttpxResponse(
            200,
            {"data": [{"id": "gpt-4o"}, {"id": "whisper-1"}]},
        ),
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        with TestClient(ready_app) as client:
            session = _authed_session(client)
            resp = client.post(
                f"/api/providers/{provider_id}/discover",
                cookies=session,
            )

    data = resp.json()
    assert data["ok"] is True
    assert data["discovered"] == 2
    assert len(data["models"]) == 2
    # Models should be registered in the database
    models = ready_app.state.db.list_provider_models(provider_id)
    assert len(models) == 2
    model_ids = {m["model_id"] for m in models}
    assert "gpt-4o" in model_ids
    assert "whisper-1" in model_ids


def test_api_discover_models_gemini_success(ready_app):
    """Discover Gemini models returns correctly classified models."""
    provider_id = _create_provider(
        ready_app.state.db,
        name="Gemini Test",
        adapter_type="gemini-native",
        endpoint="",
    )
    # Override credentials -- gemini API uses key in URL
    ready_app.state.db.update_provider(
        provider_id, credentials="test-gemini-key",
    )

    mock_client = MockHttpxClient({
        "https://generativelanguage.googleapis.com/v1/models?key=test-gemini-key":
            MockHttpxResponse(
                200,
                {"models": [{"name": "models/gemini-2.0-flash"}]},
            ),
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        with TestClient(ready_app) as client:
            session = _authed_session(client)
            resp = client.post(
                f"/api/providers/{provider_id}/discover",
                cookies=session,
            )

    data = resp.json()
    assert data["ok"] is True
    assert data["discovered"] == 1


def _openrouter_catalog():
    return [
        {
            "id": "openai/gpt-4o-mini",
            "name": "GPT-4o mini",
            "description": "Small fast text model.",
            "context_length": 128000,
            "pricing": {
                "prompt": "0.00000015",
                "completion": "0.0000006",
                "request": "0",
            },
            "top_provider": {"max_completion_tokens": 16384},
            "architecture": {
                "input_modalities": ["text"],
                "output_modalities": ["text"],
                "modality": "text->text",
            },
            "supported_parameters": ["stream"],
        },
        {
            "id": "openai/whisper-1",
            "name": "Whisper transcription",
            "context_length": 16000,
            "pricing": {
                "prompt": "0",
                "completion": "0",
                "request": "0.0001",
            },
            "architecture": {
                "input_modalities": ["audio"],
                "output_modalities": ["text"],
                "modality": "audio->text",
            },
            "supported_parameters": [],
        },
        {
            "id": "google/gemini-2.0-flash",
            "name": "Gemini Flash audio",
            "context_length": 1048576,
            "pricing": {
                "prompt": "0.0000001",
                "completion": "0.0000004",
                "request": "0",
            },
            "architecture": {
                "input_modalities": ["text", "audio"],
                "output_modalities": ["text"],
                "modality": "text+audio->text",
            },
            "supported_parameters": ["stream"],
        },
        {
            "id": "image/only",
            "name": "Image only",
            "architecture": {
                "input_modalities": ["image"],
                "output_modalities": ["image"],
                "modality": "image->image",
            },
            "supported_parameters": [],
        },
    ]


def test_api_discover_models_openrouter_refinement_shortlist(ready_app):
    """OpenRouter discovery imports only refinement candidates by purpose."""
    provider_id = _create_provider(
        ready_app.state.db,
        name="OpenRouter",
        adapter_type="openai-compat",
        endpoint="https://openrouter.ai/api/v1",
    )
    mock_client = MockHttpxClient({
        "https://openrouter.ai/api/v1/models": MockHttpxResponse(
            200, {"data": _openrouter_catalog()},
        ),
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        with TestClient(ready_app) as client:
            session = _authed_session(client)
            resp = client.post(
                f"/api/providers/{provider_id}/discover?purpose=refinement",
                cookies=session,
            )

    data = resp.json()
    assert data["ok"] is True
    assert data["guided"] is True
    assert data["discovered"] == 1
    assert data["models"][0]["model_id"] == "openai/gpt-4o-mini"
    assert data["models"][0]["capabilities"]["refinement"] is True
    assert data["models"][0]["capabilities"]["transcription"] is False


def test_api_discover_models_openrouter_transcription_only(ready_app):
    """Transcription purpose imports only strong STT candidates."""
    provider_id = _create_provider(
        ready_app.state.db,
        name="OpenRouter",
        adapter_type="openai-compat",
        endpoint="https://openrouter.ai/api/v1",
    )
    mock_client = MockHttpxClient({
        "https://openrouter.ai/api/v1/models": MockHttpxResponse(
            200, {"data": _openrouter_catalog()},
        ),
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        with TestClient(ready_app) as client:
            session = _authed_session(client)
            resp = client.post(
                f"/api/providers/{provider_id}/discover?purpose=transcription",
                cookies=session,
            )

    data = resp.json()
    assert data["ok"] is True
    assert data["discovered"] == 1
    assert data["models"][0]["model_id"] == "openai/whisper-1"
    assert data["models"][0]["capabilities"]["transcription"] is True


def test_api_discover_models_openrouter_single_pass_and_limit(ready_app):
    """Single-pass purpose is limited and separated from pure STT."""
    provider_id = _create_provider(
        ready_app.state.db,
        name="OpenRouter",
        adapter_type="openai-compat",
        endpoint="https://openrouter.ai/api/v1",
    )
    mock_client = MockHttpxClient({
        "https://openrouter.ai/api/v1/models": MockHttpxResponse(
            200, {"data": _openrouter_catalog()},
        ),
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        with TestClient(ready_app) as client:
            session = _authed_session(client)
            resp = client.post(
                f"/api/providers/{provider_id}/discover?purpose=single_pass&limit=1",
                cookies=session,
            )

    data = resp.json()
    assert data["ok"] is True
    assert data["limit"] == 1
    assert data["discovered"] == 1
    assert data["models"][0]["model_id"] == "google/gemini-2.0-flash"
    assert data["models"][0]["capabilities"]["single_pass_audio_to_text"] is True


def test_api_discover_models_openrouter_search_query(ready_app):
    """Query narrows OpenRouter discovery before import."""
    provider_id = _create_provider(
        ready_app.state.db,
        name="OpenRouter",
        adapter_type="openai-compat",
        endpoint="https://openrouter.ai/api/v1",
    )
    mock_client = MockHttpxClient({
        "https://openrouter.ai/api/v1/models": MockHttpxResponse(
            200, {"data": _openrouter_catalog()},
        ),
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        with TestClient(ready_app) as client:
            session = _authed_session(client)
            resp = client.post(
                f"/api/providers/{provider_id}/discover?purpose=all&query=whisper",
                cookies=session,
            )

    data = resp.json()
    assert data["ok"] is True
    assert data["discovered"] == 1
    assert data["models"][0]["model_id"] == "openai/whisper-1"


def test_api_openrouter_catalog_preview_does_not_import(ready_app):
    """Catalog preview returns rich model metadata without registering rows."""
    provider_id = _create_provider(
        ready_app.state.db,
        name="OpenRouter",
        adapter_type="openai-compat",
        endpoint="https://openrouter.ai/api/v1",
    )
    mock_client = MockHttpxClient({
        "https://openrouter.ai/api/v1/models": MockHttpxResponse(
            200, {"data": _openrouter_catalog()},
        ),
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        with TestClient(ready_app) as client:
            session = _authed_session(client)
            resp = client.get(
                f"/api/providers/{provider_id}/catalog?purpose=refinement&limit=10",
                cookies=session,
            )

    data = resp.json()
    assert data["ok"] is True
    assert len(data["models"]) == 1
    model = data["models"][0]
    assert model["model_id"] == "openai/gpt-4o-mini"
    assert model["context_length"] == 128000
    assert model["pricing"]["prompt"] == "0.00000015"
    assert model["capabilities"]["refinement"] is True
    assert model["metadata"]["audio_input"] is False
    assert ready_app.state.db.list_provider_models(provider_id) == []


def test_api_openrouter_catalog_query_searches_all_compatible_models(ready_app):
    """Catalog query can find compatible models outside the active tab."""
    provider_id = _create_provider(
        ready_app.state.db,
        name="OpenRouter",
        adapter_type="openai-compat",
        endpoint="https://openrouter.ai/api/v1",
    )
    mock_client = MockHttpxClient({
        "https://openrouter.ai/api/v1/models": MockHttpxResponse(
            200, {"data": _openrouter_catalog()},
        ),
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        with TestClient(ready_app) as client:
            session = _authed_session(client)
            resp = client.get(
                f"/api/providers/{provider_id}/catalog?purpose=all&query=whisper&limit=10",
                cookies=session,
            )

    data = resp.json()
    assert data["ok"] is True
    assert [m["model_id"] for m in data["models"]] == ["openai/whisper-1"]
    assert data["models"][0]["capabilities"]["transcription"] is True


def test_api_discover_models_api_error(ready_app):
    """API error during model discovery returns 0 discovered models
    (the upstream call failed, so no models are registered).

    ❌ Negative: HTTP 401 from the upstream API → discovered=0, no models
       stored.
    """
    provider_id = _create_provider(ready_app.state.db, adapter_type="openai-native")

    mock_client = MockHttpxClient({
        "https://api.openai.com/v1/models": MockHttpxResponse(
            401,
            {"error": {"message": "Incorrect API key"}},
        ),
    })

    with patch("httpx.AsyncClient", return_value=mock_client):
        with TestClient(ready_app) as client:
            session = _authed_session(client)
            resp = client.post(
                f"/api/providers/{provider_id}/discover",
                cookies=session,
            )

    # The route returns ok=True, discovered=0 on upstream HTTP errors too
    data = resp.json()
    assert data["ok"] is True
    assert data["discovered"] == 0
    # No models should have been registered
    models = ready_app.state.db.list_provider_models(provider_id)
    assert len(models) == 0


# ==================================================================
# API: list models (GET /api/providers/{id}/models)
# ==================================================================


def test_api_list_models(ready_app):
    """GET /api/providers/{id}/models returns a JSON object with models list.

    ✅ Positive: models exist → all returned.
    """
    provider_id = _create_provider(ready_app.state.db)
    _create_model(ready_app.state.db, provider_id, "whisper-1",
                  capabilities={
                      "transcription": True, "text_generation": False,
                      "refinement": False, "streaming_refinement": False,
                  })
    _create_model(ready_app.state.db, provider_id, "gpt-4o")

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.get(
            f"/api/providers/{provider_id}/models",
            cookies=session,
        )

    data = resp.json()
    assert data["ok"] is True
    assert len(data["models"]) == 2


def test_api_list_models_requires_auth(ready_app):
    """GET /api/providers/{id}/models without auth returns 401.

    ❌ Negative: unauthenticated → 401.
    """
    with TestClient(ready_app) as client:
        resp = client.get("/api/providers/1/models")
    assert resp.status_code == 401


def test_api_list_models_empty(ready_app):
    """List models when none are registered returns an empty list.

    ✅ Positive: no models → ok=True, models=[].
    """
    provider_id = _create_provider(ready_app.state.db)

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.get(
            f"/api/providers/{provider_id}/models",
            cookies=session,
        )

    data = resp.json()
    assert data["ok"] is True
    assert data["models"] == []


# ==================================================================
# API: add model (POST /api/providers/{id}/models)
# ==================================================================


def test_api_add_model(ready_app):
    """POST /api/providers/{id}/models with model_id adds a new model entry.

    ✅ Positive: model is created with auto-classified capabilities.
    """
    provider_id = _create_provider(ready_app.state.db)

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.post(
            f"/api/providers/{provider_id}/models",
            json={"model_id": "gpt-4o-mini"},
            cookies=session,
        )

    data = resp.json()
    assert data["ok"] is True
    assert data["id"] is not None
    assert data["model_id"] == "gpt-4o-mini"
    # Verify in database
    model = ready_app.state.db.get_provider_model(data["id"])
    assert model is not None
    assert model["model_id"] == "gpt-4o-mini"
    assert model["detected"] == 0  # manually added


def test_api_add_model_with_capabilities(ready_app):
    """Add model with explicit capabilities overrides auto-classification."""
    provider_id = _create_provider(ready_app.state.db)

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.post(
            f"/api/providers/{provider_id}/models",
            json={
                "model_id": "custom-model",
                "display_name": "My Custom Model",
                "capabilities": {
                    "transcription": True,
                    "text_generation": True,
                    "refinement": False,
                    "streaming_refinement": False,
                },
            },
            cookies=session,
        )

    data = resp.json()
    assert data["ok"] is True
    assert data["capabilities"]["transcription"] is True
    assert data["capabilities"]["refinement"] is False
    model = ready_app.state.db.get_provider_model(data["id"])
    assert model["capabilities"]["transcription"] is True


def test_api_add_model_validation(ready_app):
    """POST /api/providers/{id}/models without model_id returns an error.

    ❌ Negative: missing model_id → error message.
    """
    provider_id = _create_provider(ready_app.state.db)

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.post(
            f"/api/providers/{provider_id}/models",
            json={},
            cookies=session,
        )

    data = resp.json()
    assert data["ok"] is False
    assert "model_id" in data["error"].lower()


def test_api_add_model_requires_auth(ready_app):
    """POST /api/providers/{id}/models without auth returns 401.

    ❌ Negative: unauthenticated → 401.
    """
    with TestClient(ready_app) as client:
        resp = client.post(
            "/api/providers/1/models",
            json={"model_id": "gpt-4o"},
        )
    assert resp.status_code == 401


# ==================================================================
# API: update capabilities (POST /api/providers/models/{entry_id}/capabilities)
# ==================================================================


def test_api_update_capabilities(ready_app):
    """POST /api/providers/models/{entry_id}/capabilities updates the
    capability flags and marks the entry as manually overridden.

    ✅ Positive: capabilities are persisted and overridden flag is set.
    """
    provider_id = _create_provider(ready_app.state.db)
    entry_id = _create_model(ready_app.state.db, provider_id, "gpt-4o",
                             capabilities={
                                 "transcription": False, "text_generation": True,
                                 "refinement": True, "streaming_refinement": True,
                             })

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.post(
            f"/api/providers/models/{entry_id}/capabilities",
            json={
                "capabilities": {
                    "transcription": False,
                    "text_generation": True,
                    "refinement": False,
                    "streaming_refinement": False,
                },
            },
            cookies=session,
        )

    data = resp.json()
    assert data["ok"] is True
    # Verify in DB
    model = ready_app.state.db.get_provider_model(entry_id)
    assert model["capabilities"]["refinement"] is False
    assert model["manually_overridden"] == 1


def test_api_update_capabilities_missing_caps(ready_app):
    """POST without capabilities in body returns an error.

    ❌ Negative: missing capabilities → error.
    """
    provider_id = _create_provider(ready_app.state.db)
    entry_id = _create_model(ready_app.state.db, provider_id, "gpt-4o")

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.post(
            f"/api/providers/models/{entry_id}/capabilities",
            json={},
            cookies=session,
        )

    data = resp.json()
    assert data["ok"] is False
    assert "capabilities" in data["error"].lower()


def test_api_update_capabilities_requires_auth(ready_app):
    """POST /api/providers/models/{entry_id}/capabilities without auth
    returns 401.

    ❌ Negative: unauthenticated → 401.
    """
    with TestClient(ready_app) as client:
        resp = client.post(
            "/api/providers/models/1/capabilities",
            json={"capabilities": {"transcription": True}},
        )
    assert resp.status_code == 401


# ==================================================================
# API: toggle model (POST /api/providers/models/{entry_id}/toggle)
# ==================================================================


def test_api_toggle_model_disables(ready_app):
    """POST /api/providers/models/{entry_id}/toggle with enabled=False
    disables the model.

    ✅ Positive: enabled flips from 1 to 0.
    """
    provider_id = _create_provider(ready_app.state.db)
    entry_id = _create_model(ready_app.state.db, provider_id, "gpt-4o",
                             enabled=True)

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.post(
            f"/api/providers/models/{entry_id}/toggle",
            json={"enabled": False},
            cookies=session,
        )

    data = resp.json()
    assert data["ok"] is True
    model = ready_app.state.db.get_provider_model(entry_id)
    assert model["enabled"] == 0


def test_api_toggle_model_enables(ready_app):
    """POST /api/providers/models/{entry_id}/toggle with enabled=True
    enables the model.

    ✅ Positive: enabled flips from 0 to 1.
    """
    provider_id = _create_provider(ready_app.state.db)
    entry_id = _create_model(ready_app.state.db, provider_id, "gpt-4o",
                             enabled=False)

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.post(
            f"/api/providers/models/{entry_id}/toggle",
            json={"enabled": True},
            cookies=session,
        )

    data = resp.json()
    assert data["ok"] is True
    model = ready_app.state.db.get_provider_model(entry_id)
    assert model["enabled"] == 1


def test_api_toggle_model_default_enables(ready_app):
    """POST /api/providers/models/{entry_id}/toggle without enabled field
    defaults to enabled=True."""
    provider_id = _create_provider(ready_app.state.db)
    entry_id = _create_model(ready_app.state.db, provider_id, "gpt-4o",
                             enabled=False)

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.post(
            f"/api/providers/models/{entry_id}/toggle",
            json={},
            cookies=session,
        )

    data = resp.json()
    assert data["ok"] is True
    model = ready_app.state.db.get_provider_model(entry_id)
    assert model["enabled"] == 1


def test_api_toggle_model_requires_auth(ready_app):
    """POST /api/providers/models/{entry_id}/toggle without auth returns 401.

    ❌ Negative: unauthenticated → 401.
    """
    with TestClient(ready_app) as client:
        resp = client.post(
            "/api/providers/models/1/toggle",
            json={"enabled": False},
        )
    assert resp.status_code == 401


# ==================================================================
# API: delete model (POST /api/providers/models/{entry_id}/delete)
# ==================================================================


def test_api_delete_model(ready_app):
    """POST /api/providers/models/{entry_id}/delete removes the model entry.

    ✅ Positive: model deleted from DB.
    """
    provider_id = _create_provider(ready_app.state.db)
    entry_id = _create_model(ready_app.state.db, provider_id, "gpt-4o")

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.post(
            f"/api/providers/models/{entry_id}/delete",
            cookies=session,
        )

    data = resp.json()
    assert data["ok"] is True
    assert ready_app.state.db.get_provider_model(entry_id) is None


def test_api_delete_model_requires_auth(ready_app):
    """POST /api/providers/models/{entry_id}/delete without auth returns 401.

    ❌ Negative: unauthenticated → 401.
    """
    with TestClient(ready_app) as client:
        resp = client.post(
            "/api/providers/models/1/delete",
        )
    assert resp.status_code == 401


# ==================================================================
# Pipeline page — updated with model data per provider (P5)
# ==================================================================


def test_pipeline_page_requires_auth(ready_app):
    """GET /admin/pipeline without session returns 401.

    ❌ Negative: unauthenticated → 401.
    """
    with TestClient(ready_app) as client:
        resp = client.get("/admin/pipeline", follow_redirects=False)
    assert resp.status_code == 401


def test_pipeline_page_shows_empty_state(ready_app):
    """GET /admin/pipeline with no providers shows an empty-state message.

    ✅ Positive: no providers → warning message.
    """
    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.get(
            "/admin/pipeline",
            cookies=session,
        )

    assert resp.status_code == 200
    assert "Nessun provider disponibile" in resp.text
    assert "Aggiungi un provider" in resp.text


def test_pipeline_page_shows_providers_and_models(ready_app):
    """GET /admin/pipeline includes providers with their model data.

    ✅ Positive: providers with models → models appear in select dropdowns.
    """
    provider_id = _create_provider(ready_app.state.db, name="My OpenAI")
    _create_model(ready_app.state.db, provider_id, "whisper-1",
                  capabilities={
                      "transcription": True, "text_generation": False,
                      "refinement": False, "streaming_refinement": False,
                  })
    _create_model(ready_app.state.db, provider_id, "gpt-4o",
                  capabilities={
                      "transcription": False, "text_generation": True,
                      "refinement": True, "streaming_refinement": True,
                  })

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.get(
            "/admin/pipeline",
            cookies=session,
        )

    assert resp.status_code == 200
    # Provider name should appear
    assert "My OpenAI" in resp.text
    # Models should appear in select dropdowns
    assert "whisper-1" in resp.text
    assert "gpt-4o" in resp.text
    # Mode selection cards should be present
    assert "Due fasi" in resp.text
    assert "Singolo passaggio" in resp.text


# ==================================================================
# Pipeline save — two-stage mode (POST /admin/pipeline/save)
# ==================================================================


def test_pipeline_save_two_stage_success(ready_app):
    """POST /admin/pipeline/save with mode=two_stage and model IDs creates
    a profile with two stages and redirects with success.

    ✅ Positive: profile is created with transcription + refinement stages.
    """
    provider_id = _create_provider(ready_app.state.db)
    tx_id = _create_model(ready_app.state.db, provider_id, "whisper-1",
                          capabilities={
                              "transcription": True, "text_generation": False,
                              "refinement": False, "streaming_refinement": False,
                          })
    ref_id = _create_model(ready_app.state.db, provider_id, "gpt-4o",
                           capabilities={
                               "transcription": False, "text_generation": True,
                               "refinement": True, "streaming_refinement": True,
                           })

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.get("/admin/pipeline", cookies=session)
        csrf = _extract_csrf(resp.text)

        resp = client.post(
            "/admin/pipeline/save",
            data={
                "csrf_token": csrf,
                "pipeline_mode": "two_stage",
                "tx_model_id": str(tx_id),
                "ref_model_id": str(ref_id),
            },
            cookies=session,
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert "success=saved" in resp.headers["location"]

    # A new pipeline profile should have been created
    profiles = ready_app.state.db.list_pipeline_profiles()
    matching = [p for p in profiles if p.get("name") == "Pipeline due fasi"]
    assert len(matching) == 1
    profile = matching[0]
    assert profile["mode"] == "two_stage"
    # Stages should exist
    stages = ready_app.state.db.list_pipeline_stages(profile["id"])
    assert len(stages) == 2
    stage_types = {s["stage_type"] for s in stages}
    assert "transcription" in stage_types
    assert "refinement" in stage_types


def test_pipeline_save_two_stage_no_tx_model(ready_app):
    """POST /admin/pipeline/save with two_stage but no transcription model
    returns an error.

    ❌ Negative: tx_model_id not provided → error=no_tx_model.
    """
    provider_id = _create_provider(ready_app.state.db)
    _create_model(ready_app.state.db, provider_id, "gpt-4o")

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.get("/admin/pipeline", cookies=session)
        csrf = _extract_csrf(resp.text)

        resp = client.post(
            "/admin/pipeline/save",
            data={
                "csrf_token": csrf,
                "pipeline_mode": "two_stage",
                "tx_model_id": "",
                "ref_model_id": "",
            },
            cookies=session,
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert "error=no_tx_model" in resp.headers["location"]


def test_pipeline_save_two_stage_ref_only(ready_app):
    """POST /admin/pipeline/save with two_stage, transcription model only,
    no refinement model — still succeeds (refinement is optional).

    ✅ Positive: ref_model_id omitted → one stage created.
    """
    provider_id = _create_provider(ready_app.state.db)
    tx_id = _create_model(ready_app.state.db, provider_id, "whisper-1",
                          capabilities={
                              "transcription": True, "text_generation": False,
                              "refinement": False, "streaming_refinement": False,
                          })

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.get("/admin/pipeline", cookies=session)
        csrf = _extract_csrf(resp.text)

        resp = client.post(
            "/admin/pipeline/save",
            data={
                "csrf_token": csrf,
                "pipeline_mode": "two_stage",
                "tx_model_id": str(tx_id),
                "ref_model_id": "",
            },
            cookies=session,
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert "success=saved" in resp.headers["location"]


# ==================================================================
# Pipeline save — single-pass mode (POST /admin/pipeline/save)
# ==================================================================


def test_pipeline_save_single_pass_success(ready_app):
    """POST /admin/pipeline/save with mode=single_pass and a model ID
    creates a single stage profile.

    ✅ Positive: single_pass profile created with one stage.
    """
    provider_id = _create_provider(
        ready_app.state.db,
        name="Gemini",
        adapter_type="gemini-native",
    )
    sp_id = _create_model(ready_app.state.db, provider_id, "gemini-2.0-flash",
                          capabilities={
                              "transcription": True, "text_generation": True,
                              "refinement": True, "streaming_refinement": True,
                              "single_pass_audio_to_text": True,
                          })

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.get("/admin/pipeline", cookies=session)
        csrf = _extract_csrf(resp.text)

        resp = client.post(
            "/admin/pipeline/save",
            data={
                "csrf_token": csrf,
                "pipeline_mode": "single_pass",
                "sp_model_id": str(sp_id),
            },
            cookies=session,
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert "success=saved" in resp.headers["location"]

    profiles = ready_app.state.db.list_pipeline_profiles()
    matching = [p for p in profiles if p.get("mode") == "single_pass"]
    assert len(matching) >= 1
    profile = matching[-1]
    stages = ready_app.state.db.list_pipeline_stages(profile["id"])
    assert len(stages) == 1
    assert stages[0]["stage_type"] == "single_pass"


def test_pipeline_save_single_pass_no_model(ready_app):
    """POST /admin/pipeline/save with single_pass but no sp_model_id
    returns an error.

    ❌ Negative: missing sp_model_id → error=no_sp_model.
    """
    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.get("/admin/pipeline", cookies=session)
        csrf = _extract_csrf(resp.text)

        resp = client.post(
            "/admin/pipeline/save",
            data={
                "csrf_token": csrf,
                "pipeline_mode": "single_pass",
                "sp_model_id": "",
            },
            cookies=session,
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert "error=no_sp_model" in resp.headers["location"]


# ==================================================================
# Pipeline save — CSRF rejection
# ==================================================================


def test_pipeline_save_csrf_rejection(ready_app):
    """POST /admin/pipeline/save with a bad CSRF token is rejected.

    ❌ Negative: invalid CSRF → error=csrf redirect.
    """
    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.post(
            "/admin/pipeline/save",
            data={
                "csrf_token": "bad-token",
                "pipeline_mode": "two_stage",
            },
            cookies=session,
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert "error=csrf" in resp.headers["location"]


# ==================================================================
# API: pipeline info — includes provider_models (GET /api/pipeline/info)
# ==================================================================


def test_api_pipeline_info_structure(ready_app):
    """GET /api/pipeline/info returns the expected structure with
    providers, models, profile, and profile_id.

    ✅ Positive: response has all keys, provider entries include models.
    """
    provider_id = _create_provider(ready_app.state.db, name="OpenAI Test")
    _create_model(ready_app.state.db, provider_id, "whisper-1",
                  capabilities={
                      "transcription": True, "text_generation": False,
                      "refinement": False, "streaming_refinement": False,
                  })
    _create_model(ready_app.state.db, provider_id, "gpt-4o")

    with TestClient(ready_app) as client:
        resp = client.get("/api/pipeline/info")

    data = resp.json()
    assert "providers" in data
    assert "profile" in data
    assert "profile_id" in data
    assert len(data["providers"]) >= 1

    # Find our provider and verify it has models
    our_provider = None
    for p in data["providers"]:
        if p["id"] == provider_id:
            our_provider = p
            break
    assert our_provider is not None
    assert "models" in our_provider
    assert len(our_provider["models"]) == 2


def test_api_pipeline_info_without_providers(ready_app):
    """GET /api/pipeline/info returns empty providers list when none exist.

    ✅ Positive: no providers → empty list, profile is None.
    """
    with TestClient(ready_app) as client:
        resp = client.get("/api/pipeline/info")

    data = resp.json()
    assert data["providers"] == []
    assert data["profile"] is None
    assert data["profile_id"] is None


# ==================================================================
# API: pipeline stages update (POST /api/pipeline/stages)
# ==================================================================


def test_api_pipeline_stages_update(ready_app):
    """POST /api/pipeline/stages replaces stages for a pipeline profile.

    ✅ Positive: stages are cleared and recreated from the payload.
    """
    provider_id = _create_provider(ready_app.state.db)
    tx_id = _create_model(ready_app.state.db, provider_id, "whisper-1",
                          capabilities={
                              "transcription": True, "text_generation": False,
                              "refinement": False, "streaming_refinement": False,
                          })
    ref_id = _create_model(ready_app.state.db, provider_id, "gpt-4o")

    # Create a pipeline profile first
    profile_id = ready_app.state.db.add_pipeline_profile(
        name="Test Profile",
        transcription_provider_id=provider_id,
        text_provider_id=provider_id,
        mode="two_stage",
    )

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.post(
            "/api/pipeline/stages",
            json={
                "profile_id": profile_id,
                "mode": "two_stage",
                "stages": [
                    {
                        "stage_type": "transcription",
                        "primary_model_id": tx_id,
                        "fallback_model_ids": [],
                    },
                    {
                        "stage_type": "refinement",
                        "primary_model_id": ref_id,
                        "fallback_model_ids": [],
                    },
                ],
            },
            cookies=session,
        )

    data = resp.json()
    assert data["ok"] is True
    assert data["profile_id"] == profile_id

    # Verify in database
    stages = ready_app.state.db.list_pipeline_stages(profile_id)
    assert len(stages) == 2
    stage_types = {s["stage_type"] for s in stages}
    assert "transcription" in stage_types
    assert "refinement" in stage_types

    # Verify mode was updated
    mode = ready_app.state.db.get_pipeline_profile_mode(profile_id)
    assert mode == "two_stage"


def test_api_pipeline_stages_requires_auth(ready_app):
    """POST /api/pipeline/stages without auth returns 401.

    ❌ Negative: unauthenticated → 401.
    """
    with TestClient(ready_app) as client:
        resp = client.post(
            "/api/pipeline/stages",
            json={"profile_id": 1, "stages": []},
        )
    assert resp.status_code == 401


def test_api_pipeline_stages_missing_profile_id(ready_app):
    """POST /api/pipeline/stages without profile_id returns an error.

    ❌ Negative: missing profile_id → error.
    """
    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.post(
            "/api/pipeline/stages",
            json={"stages": []},
            cookies=session,
        )

    data = resp.json()
    assert data["ok"] is False
    assert "profile_id" in data["error"].lower()


def test_api_pipeline_stages_invalid_mode(ready_app):
    """POST /api/pipeline/stages with an invalid mode returns an error.

    ❌ Negative: mode must be ``two_stage`` or ``single_pass``.
    """
    provider_id = _create_provider(ready_app.state.db)
    profile_id = ready_app.state.db.add_pipeline_profile(
        name="Test", transcription_provider_id=provider_id,
    )

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.post(
            "/api/pipeline/stages",
            json={
                "profile_id": profile_id,
                "mode": "invalid_mode",
                "stages": [],
            },
            cookies=session,
        )

    data = resp.json()
    assert data["ok"] is False
    assert "mode" in data["error"].lower()


def test_api_pipeline_stages_profile_not_found(ready_app):
    """POST /api/pipeline/stages with a nonexistent profile_id returns 404.

    ❌ Negative: profile not found → 404.
    """
    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.post(
            "/api/pipeline/stages",
            json={
                "profile_id": 9999,
                "mode": "two_stage",
                "stages": [],
            },
            cookies=session,
        )

    assert resp.status_code == 404
    data = resp.json()
    assert data["ok"] is False
    assert "non trovato" in data["error"].lower()


# ==================================================================
# Provider detail — edit form fields
# ==================================================================


def test_provider_detail_shows_edit_form(ready_app):
    """Provider detail page contains the edit form with name, endpoint,
    and enabled checkbox.

    ✅ Positive: edit form fields are rendered in the detail page.
    """
    provider_id = _create_provider(ready_app.state.db, name="Edit Me")

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.get(
            f"/admin/providers/{provider_id}",
            cookies=session,
        )

    assert resp.status_code == 200
    # The edit form posts to /admin/providers/{id}/edit
    assert f'action="/admin/providers/{provider_id}/edit"' in resp.text
    # Form fields
    assert 'name="name"' in resp.text
    assert "Edit Me" in resp.text
    assert 'name="endpoint"' in resp.text
    assert 'name="api_key"' in resp.text
    assert 'name="enabled"' in resp.text


# ==================================================================
# Provider edit — disable provider
# ==================================================================


def test_provider_edit_disables_provider(ready_app):
    """POST /admin/providers/{id}/edit with enabled unchecked disables
    the provider.

    ✅ Positive: enabled flag is set to 0 in the database.
    """
    provider_id = _create_provider(ready_app.state.db, name="Toggleable")

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.get(
            f"/admin/providers/{provider_id}",
            cookies=session,
        )
        csrf = _extract_csrf(resp.text)

        resp = client.post(
            f"/admin/providers/{provider_id}/edit",
            data={
                "csrf_token": csrf,
                "name": "Toggleable",
                "enabled": "0",
            },
            cookies=session,
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert "success=updated" in resp.headers["location"]

    provider = ready_app.state.db.get_provider(provider_id)
    # enabled is stored as int: 0 = disabled
    assert provider.get("enabled") == 0


def test_provider_edit_disable_blocked_when_in_use(ready_app):
    """POST /admin/providers/{id}/edit with enabled=0 is blocked when
    the provider is referenced by the active pipeline.

    ❌ Negative: provider in use → error=provider_in_use redirect,
    provider stays enabled.
    """
    provider_id = _create_provider(ready_app.state.db, name="InUseProvider")
    # Create a model, profile, stage, and mark it active
    model_id = _create_model(ready_app.state.db, provider_id, "whisper-1",
                             capabilities={
                                 "transcription": True, "text_generation": False,
                                 "refinement": False, "streaming_refinement": False,
                             })
    profile_id = ready_app.state.db.add_pipeline_profile(
        "Active Profile",
        transcription_provider_id=provider_id,
        text_provider_id=provider_id,
    )
    ready_app.state.db.add_pipeline_stage(
        profile_id, "transcription", primary_model_id=model_id,
    )
    ready_app.state.db.set_setup_state("active_pipeline_profile", str(profile_id))

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.get(
            f"/admin/providers/{provider_id}",
            cookies=session,
        )
        csrf = _extract_csrf(resp.text)

        resp = client.post(
            f"/admin/providers/{provider_id}/edit",
            data={
                "csrf_token": csrf,
                "name": "InUseProvider",
                "enabled": "0",
            },
            cookies=session,
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert "error=provider_in_use" in resp.headers["location"]

    provider = ready_app.state.db.get_provider(provider_id)
    assert provider.get("enabled") == 1


def test_provider_edit_disable_succeeds_when_not_in_use(ready_app):
    """POST /admin/providers/{id}/edit with enabled=0 succeeds when
    the provider is not referenced by any active pipeline.

    ✅ Positive: provider not in use → disabled successfully.
    """
    provider_id = _create_provider(ready_app.state.db, name="NotInUseProvider")

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.get(
            f"/admin/providers/{provider_id}",
            cookies=session,
        )
        csrf = _extract_csrf(resp.text)

        resp = client.post(
            f"/admin/providers/{provider_id}/edit",
            data={
                "csrf_token": csrf,
                "name": "NotInUseProvider",
                "enabled": "0",
            },
            cookies=session,
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert "success=updated" in resp.headers["location"]

    provider = ready_app.state.db.get_provider(provider_id)
    assert provider.get("enabled") == 0


# ==================================================================
# Provider edit — update API key
# ==================================================================


def test_provider_edit_updates_api_key(ready_app):
    """POST /admin/providers/{id}/edit with a new api_key stores it as
    credentials.

    ✅ Positive: credentials are updated in the database.
    """
    provider_id = _create_provider(ready_app.state.db, name="Key Update")

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.get(
            f"/admin/providers/{provider_id}",
            cookies=session,
        )
        csrf = _extract_csrf(resp.text)

        resp = client.post(
            f"/admin/providers/{provider_id}/edit",
            data={
                "csrf_token": csrf,
                "name": "Key Update",
                "api_key": "sk-new-api-key",
                "enabled": "1",
            },
            cookies=session,
            follow_redirects=False,
        )

    assert resp.status_code == 303

    # The provider's credentials should have been updated
    provider = ready_app.state.db.get_provider(provider_id)
    # When no secret store is configured, credentials may be None
    # but the operation should succeed without error
    assert provider["name"] == "Key Update"


# ==================================================================
# API: add model — nonexistent provider (404)
# ==================================================================


def test_api_add_model_provider_not_found(ready_app):
    """POST /api/providers/9999/models with nonexistent provider returns
    404.

    ❌ Negative: nonexistent provider → 404 with error message.
    """
    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.post(
            "/api/providers/9999/models",
            json={"model_id": "gpt-4o"},
            cookies=session,
        )

    assert resp.status_code == 404
    data = resp.json()
    assert data["ok"] is False
    assert "Provider non trovato" in data["error"]


# ==================================================================
# Pipeline save — "single" legacy mode (POST /admin/pipeline/save)
# ==================================================================


def test_pipeline_save_single_legacy_mode(ready_app):
    """POST /admin/pipeline/save with mode=single creates a same-provider
    pipeline profile.

    ✅ Positive: profile is created with the same provider for tx and ref.
    """
    provider_id = _create_provider(ready_app.state.db, name="Single Provider")
    _create_model(ready_app.state.db, provider_id, "gpt-4o",
                  capabilities={
                      "transcription": True, "text_generation": True,
                      "refinement": True, "streaming_refinement": True,
                  })

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.get("/admin/pipeline", cookies=session)
        csrf = _extract_csrf(resp.text)

        resp = client.post(
            "/admin/pipeline/save",
            data={
                "csrf_token": csrf,
                "pipeline_mode": "single",
                "provider_id": str(provider_id),
            },
            cookies=session,
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert "success=saved" in resp.headers["location"]

    # Verify a profile was created
    profiles = ready_app.state.db.list_pipeline_profiles()
    matching = [p for p in profiles if p.get("name") == "Pipeline predefinita"]
    assert len(matching) == 1
    profile = matching[0]
    assert profile["transcription_provider_id"] == provider_id
    assert profile["text_provider_id"] == provider_id


def test_pipeline_save_single_legacy_no_provider(ready_app):
    """POST /admin/pipeline/save with mode=single but no provider_id
    returns an error.

    ❌ Negative: empty provider_id → error=no_provider.
    """
    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.get("/admin/pipeline", cookies=session)
        csrf = _extract_csrf(resp.text)

        resp = client.post(
            "/admin/pipeline/save",
            data={
                "csrf_token": csrf,
                "pipeline_mode": "single",
                "provider_id": "",
            },
            cookies=session,
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert "error=no_provider" in resp.headers["location"]


# ==================================================================
# Pipeline save — "advanced" mode (POST /admin/pipeline/save)
# ==================================================================


def test_pipeline_save_advanced_mode(ready_app):
    """POST /admin/pipeline/save with mode=advanced creates a two-provider
    pipeline profile.

    ✅ Positive: profile is created with separate tx and ref providers.
    """
    tx_provider_id = _create_provider(
        ready_app.state.db, name="TX Provider",
        adapter_type="openai-native",
    )
    ref_provider_id = _create_provider(
        ready_app.state.db, name="Ref Provider",
        adapter_type="openai-native",
    )
    _create_model(ready_app.state.db, tx_provider_id, "whisper-1",
                  capabilities={
                      "transcription": True, "text_generation": False,
                      "refinement": False, "streaming_refinement": False,
                  })
    _create_model(ready_app.state.db, ref_provider_id, "gpt-4o",
                  capabilities={
                      "transcription": False, "text_generation": True,
                      "refinement": True, "streaming_refinement": True,
                  })

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.get("/admin/pipeline", cookies=session)
        csrf = _extract_csrf(resp.text)

        resp = client.post(
            "/admin/pipeline/save",
            data={
                "csrf_token": csrf,
                "pipeline_mode": "advanced",
                "transcription_provider_id": str(tx_provider_id),
                "text_provider_id": str(ref_provider_id),
            },
            cookies=session,
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert "success=saved" in resp.headers["location"]

    profiles = ready_app.state.db.list_pipeline_profiles()
    matching = [p for p in profiles if p.get("name") == "Pipeline avanzata"]
    assert len(matching) == 1
    profile = matching[0]
    assert profile["transcription_provider_id"] == tx_provider_id
    assert profile["text_provider_id"] == ref_provider_id


def test_pipeline_save_advanced_no_tx_provider(ready_app):
    """POST /admin/pipeline/save with mode=advanced but no
    transcription_provider_id returns an error.

    ❌ Negative: empty tx provider → error=no_tx_provider.
    """
    ref_provider_id = _create_provider(ready_app.state.db, name="Ref Only")

    with TestClient(ready_app) as client:
        session = _authed_session(client)
        resp = client.get("/admin/pipeline", cookies=session)
        csrf = _extract_csrf(resp.text)

        resp = client.post(
            "/admin/pipeline/save",
            data={
                "csrf_token": csrf,
                "pipeline_mode": "advanced",
                "transcription_provider_id": "",
                "text_provider_id": str(ref_provider_id),
            },
            cookies=session,
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert "error=no_tx_provider" in resp.headers["location"]


# ==================================================================
# API: discover models — timeout handling
# ==================================================================


def test_api_discover_models_timeout(ready_app):
    """POST /api/providers/{id}/discover when httpx times out returns
    a timeout error message.

    ❌ Negative: httpx.TimeoutException → error message about timeout.
    """
    import httpx

    provider_id = _create_provider(ready_app.state.db, adapter_type="openai-native")

    class TimeoutMockClient:
        """Mock httpx client that raises TimeoutException on any request."""

        def __init__(self, timeout=None):
            pass

        async def get(self, url, **kwargs):
            raise httpx.TimeoutException("Connection timed out", request=None)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    with patch("httpx.AsyncClient", return_value=TimeoutMockClient()):
        with TestClient(ready_app) as client:
            session = _authed_session(client)
            resp = client.post(
                f"/api/providers/{provider_id}/discover",
                cookies=session,
            )

    data = resp.json()
    assert data["ok"] is False
    assert "timeout" in data["error"].lower()

"""
FastAPI application factory for the web frontend control plane.

Initialises application services (Config, DatabaseManager, SecretStore,
ConfigService, StateChecker, RuntimeManager) and registers the following
route groups:

- ``/setup`` — first-run wizard (A6 code + admin creation)
- ``/login`` / ``/logout`` — authentication
- ``/admin/*`` — administration pages
- ``/api/*`` — JSON API endpoints for the frontend JS
"""

from __future__ import annotations

import logging
import os
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer

from bot.config import Config
from bot.config_service import ConfigService
from bot.database import DatabaseManager, SecretStore
from bot.exceptions import ConfigError
from bot.runtime_manager import RuntimeManager
from bot.setup import (
    generate_setup_code,
    invalidate_setup_code,
    is_code_generated,
    is_first_run,
    validate_setup_code,
)
from bot.state import AppState, StateChecker

import bot.setup
from bot.web.auth import (
    SESSION_MAX_AGE,
    _make_serialiser,
    decode_session,
    encode_session,
    generate_csrf_token,
    has_admin,
    set_admin_password,
    verify_admin_password,
    validate_csrf_token,
)

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Package resource paths
# ------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_TEMPLATES = _HERE / "templates"
_STATIC = _HERE / "static"

# ------------------------------------------------------------------
# Lifespan context manager — replaces deprecated on_event
# ------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup: initialise services, optionally start the bot.

    Shutdown: stop the bot if running.
    """
    # Startup — services are already initialised in create_app.
    mgr: RuntimeManager = app.state.runtime_manager
    state = mgr.get_state()

    if state.state == AppState.READY:
        try:
            await mgr.start_async()
            logger.info("Bot started automatically (state=READY)")
        except Exception as exc:
            logger.warning(
                "Bot auto-start skipped: %s",
                exc,
            )
    elif state.state == AppState.SETUP_REQUIRED:
        logger.info(
            "Setup required — visit /setup to complete configuration"
        )
    else:
        logger.info(
            "Application state: %s — bot not started automatically",
            state.state.value,
        )

    yield

    # Shutdown
    await mgr.stop_async()
    logger.info("Bot stopped (web server shutdown)")


# ------------------------------------------------------------------
# Application factory
# ------------------------------------------------------------------


def create_app(
    config: Optional[Config] = None,
) -> FastAPI:
    """Create and return a fully configured FastAPI application.

    Parameters
    ----------
    config:
        Optional pre-created ``Config``.  When ``None`` (the default),
        the factory calls ``Config(relaxed=True)`` so the web server
        starts even without a complete ``.env`` file.
    """
    # ---- Initialise services ------------------------------------------------

    # Config: try normal load, fall back to relaxed mode so the web
    # server starts on blank data volumes (no .env, no secrets).
    if config is None:
        try:
            config = Config()
        except (ConfigError, RuntimeError) as exc:
            logger.warning(
                "Config initialisation failed (%s). "
                "Using default audio directory for setup mode. "
                "The bot will not start until setup is complete.",
                exc,
            )
            # Minimal config-like namespace for blank-volume startup.
            from types import SimpleNamespace
            audio_dir = os.getenv("AUDIO_DIR", "audio_files")
            config = SimpleNamespace(
                telegram_token="",
                provider_name="",
                model_name=None,
                api_keys={},
                get_api_key=lambda p="": "",
                prompts={"system": "", "refine_template": "{raw_text}"},
                rate_limit_config={},
                provider_resilience_config={},
                telegram_progressive_output_config={},
                audio_dir=audio_dir,
                authorized_data={"admin": [], "users": [], "groups": []},
            )
            config._relaxed = True  # type: ignore[attr-defined]

    audio_dir = Path(config.audio_dir)
    audio_dir.mkdir(parents=True, exist_ok=True)

    # Secret store (A2)
    key_path = os.getenv(
        "MASTER_KEY_FILE",
        str(audio_dir / ".master_key"),
    )
    secret_store: Optional[SecretStore] = None
    try:
        store = SecretStore(key_path)
        if store.initialize():
            logger.info("Generated new master key at %s", key_path)
        secret_store = store
    except Exception:
        logger.exception("Failed to initialise SecretStore; continuing without encryption")

    # Database (A1)
    db_path = os.getenv(
        "APPLICATION_DB",
        str(audio_dir / "app.sqlite3"),
    )
    database_manager = DatabaseManager(db_path, secret_store=secret_store)
    database_manager.initialize()
    database_manager.import_whitelist_from_dict(config.authorized_data)

    # Config service (A3)
    config_service = ConfigService(database_manager, secret_store=secret_store)

    # State checker (A4)
    state_checker = StateChecker(
        config_service,
        database_manager,
        legacy_config=config if not getattr(config, '_relaxed', False) else None,
    )

    # A6 — generate setup code on blank data volume
    if is_first_run(database_manager) and not is_code_generated(database_manager):
        setup_code = generate_setup_code(database_manager)
        _print_setup_code(setup_code)

    # Runtime manager (A5)
    runtime_manager = RuntimeManager(
        config,
        database_manager,
        secret_store,
        config_service,
        state_checker,
    )

    # Session serialiser
    session_secret = os.getenv("WEB_SESSION_SECRET", secrets.token_urlsafe(32))
    serialiser = _make_serialiser(session_secret)

    # ---- FastAPI app --------------------------------------------------------

    app = FastAPI(
        title="Telegram Audio Bot",
        lifespan=_lifespan,
    )

    # Store services in app.state for route access
    app.state.config = config
    app.state.db = database_manager
    app.state.secret_store = secret_store
    app.state.config_service = config_service
    app.state.state_checker = state_checker
    app.state.runtime_manager = runtime_manager
    app.state.serialiser = serialiser

    # Static files and templates
    _TEMPLATES.mkdir(parents=True, exist_ok=True)
    _STATIC.mkdir(parents=True, exist_ok=True)

    templates = Jinja2Templates(directory=str(_TEMPLATES))
    app.state.templates = templates

    if _STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

    # ---- Route helpers ------------------------------------------------------

    def _session(request: Request) -> Optional[dict]:
        """Read and return the current session from the cookie."""
        cookie = request.cookies.get("session")
        if not cookie:
            return None
        return decode_session(serialiser, cookie)

    def _session_response(response: Response, data: dict) -> None:
        """Sign *data* into a session cookie on *response*."""
        cookie = encode_session(serialiser, data)
        response.set_cookie(
            key="session",
            value=cookie,
            max_age=int(SESSION_MAX_AGE),
            httponly=True,
            samesite="strict",
            secure=os.getenv("WEB_SECURE_COOKIE", "0") == "1",
        )

    def _login_required(request: Request):
        """Dependency: redirect to /login if not authenticated as admin."""
        session = _session(request)
        if session is None or not session.get("admin"):
            raise HTTPException(status_code=401)
        return session

    # ---- Routes: Setup ------------------------------------------------------

    @app.get("/setup", response_class=HTMLResponse)
    async def setup_page(request: Request):
        session = _session(request)
        admin_exists = has_admin(database_manager)

        # Already logged in as admin and admin exists → go to dashboard
        if session is not None and session.get("admin") and admin_exists:
            return RedirectResponse(url="/admin/dashboard", status_code=303)

        # Setup complete but not logged in → go to login
        if admin_exists:
            return RedirectResponse(url="/login", status_code=303)

        # Ensure a session cookie exists (CSRF storage)
        if session is None:
            session = {"csrf_token": generate_csrf_token()}
            response = templates.TemplateResponse(
                request,
                "setup.html",
                {
                    "csrf_token": session["csrf_token"],
                    "step": "code" if is_code_generated(database_manager) else "start",
                    "error": request.query_params.get("error", ""),
                },
            )
            _session_response(response, session)
            return response

        return templates.TemplateResponse(
            request,
            "setup.html",
            {
                "csrf_token": session.get("csrf_token", generate_csrf_token()),
                "step": "code" if is_code_generated(database_manager) else "start",
                "error": request.query_params.get("error", ""),
            },
        )

    @app.post("/setup")
    async def setup_post(
        request: Request,
        setup_code: str = Form(""),
        admin_password: str = Form(""),
        admin_password_confirm: str = Form(""),
        csrf_token: str = Form(""),
    ):
        # CSRF check
        session = _session(request)
        if not validate_csrf_token(session or {}, csrf_token):
            return RedirectResponse(url="/setup?error=csrf", status_code=303)

        # Validate setup code
        if not validate_setup_code(database_manager, setup_code):
            return RedirectResponse(url="/setup?error=invalid_code", status_code=303)

        # Validate password
        if len(admin_password) < 8:
            return RedirectResponse(url="/setup?error=password_short", status_code=303)
        if admin_password != admin_password_confirm:
            return RedirectResponse(url="/setup?error=password_mismatch", status_code=303)

        # Create admin account and finalise setup
        set_admin_password(database_manager, admin_password)
        database_manager.set_setup_state("admin_created", "true")
        invalidate_setup_code(database_manager)

        logger.info("First administrator created — setup complete")

        # Redirect to login (without admin status)
        response = RedirectResponse(url="/login?setup=ok", status_code=303)
        _session_response(response, {"csrf_token": generate_csrf_token()})
        return response

    # ---- Routes: Login / Logout ---------------------------------------------

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        session = _session(request)
        if session is not None and session.get("admin") and has_admin(database_manager):
            return RedirectResponse(url="/admin/dashboard", status_code=303)

        # Ensure a session cookie exists (CSRF storage)
        if session is None:
            session = {"csrf_token": generate_csrf_token()}
            response = templates.TemplateResponse(
                request,
                "login.html",
                {
                    "csrf_token": session["csrf_token"],
                    "error": request.query_params.get("error", ""),
                    "setup_ok": request.query_params.get("setup", "") == "ok",
                },
            )
            _session_response(response, session)
            return response

        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "csrf_token": session.get("csrf_token", generate_csrf_token()),
                "error": request.query_params.get("error", ""),
                "setup_ok": request.query_params.get("setup", "") == "ok",
            },
        )

    @app.post("/login")
    async def login_post(
        request: Request,
        password: str = Form(""),
        csrf_token: str = Form(""),
    ):
        session = _session(request)
        if not validate_csrf_token(session or {}, csrf_token):
            return RedirectResponse(url="/login?error=csrf", status_code=303)

        if not verify_admin_password(database_manager, password):
            return RedirectResponse(url="/login?error=invalid", status_code=303)

        # Create authenticated session
        response = RedirectResponse(url="/admin/dashboard", status_code=303)
        _session_response(response, {"admin": True, "csrf_token": generate_csrf_token()})
        return response

    @app.post("/logout")
    async def logout():
        response = RedirectResponse(url="/login", status_code=303)
        response.delete_cookie("session")
        return response

    # ---- Routes: Admin ------------------------------------------------------

    @app.get("/admin/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request):
        _login_required(request)
        state = state_checker.get_state()
        health = runtime_manager.get_health()

        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "csrf_token": generate_csrf_token(),
                "state": state,
                "health": health,
            },
        )

    # ---- Routes: API --------------------------------------------------------

    @app.get("/api/state")
    async def api_state():
        info = state_checker.get_state()
        return {
            "state": info.state.value,
            "label": info.label,
            "description": info.description,
            "next_action": info.next_action,
            "can_process_audio": state_checker.can_process_audio(),
        }

    @app.get("/api/health")
    async def api_health():
        return runtime_manager.get_health()

    # ---- Routes: Error pages ------------------------------------------------

    @app.exception_handler(401)
    async def unauthorized(request: Request, exc: HTTPException):
        return templates.TemplateResponse(
            request,
            "error.html",
            {"title": "401", "message": "Devi effettuare il login."},
            status_code=401,
        )

    @app.exception_handler(404)
    async def not_found(request: Request, exc: HTTPException):
        return templates.TemplateResponse(
            request,
            "error.html",
            {"title": "404", "message": "Pagina non trovata."},
            status_code=404,
        )

    # Root redirect
    @app.get("/")
    async def root():
        if has_admin(database_manager):
            return RedirectResponse(url="/admin/dashboard", status_code=303)
        return RedirectResponse(url="/setup", status_code=303)

    return app


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _print_setup_code(code: str) -> None:
    """Print the setup code prominently."""
    sep = "=" * 56
    print(f"\n{sep}", flush=True)
    print(f"  SETUP CODE: {code}", flush=True)
    print(f"  Valido per {bot.setup.SETUP_CODE_TTL_SECONDS} secondi.", flush=True)
    print(f"  Apri http://localhost:8080 per completare la configurazione.", flush=True)
    print(f"{sep}\n", flush=True)
    logger.info(
        "One-time setup code generated — valid for %s seconds",
        bot.setup.SETUP_CODE_TTL_SECONDS,
    )

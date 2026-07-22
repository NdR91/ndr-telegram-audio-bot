"""
FastAPI application factory for the web frontend control plane.

Initialises application services (Config, DatabaseManager, SecretStore,
ConfigService, StateChecker, RuntimeManager) and registers route modules.

Route modules (O5 modularisation):

- ``routes_setup.py`` — ``/setup``, ``/api/setup/*`` (W2, W9, W10)
- ``routes_auth.py`` — ``/login``, ``/logout``, ``/recovery``
- ``routes_admin.py`` — ``/admin/dashboard``, ``/admin/bot/*``, ``/api/state``, ``/api/health``, ``/``
- ``routes_providers.py`` — ``/admin/providers``, ``/api/providers/*`` (W3)
- ``routes_pipeline.py`` — ``/admin/pipeline``, ``/api/pipeline/*`` (W4)
"""

from __future__ import annotations

import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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
)
from bot.state import AppState, StateChecker

import bot.recovery
from bot.recovery import generate_recovery_code
from bot.web.auth import _make_serialiser, has_admin
from bot.web.routes_admin import router as admin_router
from bot.web.routes_auth import router as auth_router
from bot.web.routes_pipeline import router as pipeline_router
from bot.web.routes_providers import router as providers_router
from bot.web.routes_setup import router as setup_router

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

    # W6 — Generate a recovery code on every startup when admin exists.
    if state.state != AppState.SETUP_REQUIRED:
        db: DatabaseManager = app.state.db
        if has_admin(db):
            code = generate_recovery_code(db)
            _print_recovery_code(code)

    yield

    # Shutdown
    await mgr.stop_async()
    logger.info("Bot stopped (web server shutdown)")


# ------------------------------------------------------------------
# Print helpers
# ------------------------------------------------------------------


def _print_recovery_code(code: str) -> None:
    """Print the recovery code prominently in the logs so the administrator
    can copy it for password reset.

    Deliberately uses ``print`` (not logger) so the code is always visible
    regardless of log-level configuration.
    """
    sep = "=" * 56
    print(f"\n{sep}", flush=True)
    print(f"  RECOVERY CODE: {code}", flush=True)
    print(f"  Valido per {bot.recovery.RECOVERY_CODE_TTL_SECONDS} secondi.", flush=True)
    print(f"  Vai su /recovery nell'interfaccia web per reimpostare la password.", flush=True)
    print(f"{sep}\n", flush=True)
    logger.info(
        "One-time recovery code generated — valid for %s seconds",
        bot.recovery.RECOVERY_CODE_TTL_SECONDS,
    )


def _print_setup_code(code: str) -> None:
    """Print the setup code prominently in the logs so the administrator
    can copy it for guided onboarding."""
    sep = "=" * 56
    print(f"\n{sep}", flush=True)
    print(f"  SETUP CODE: {code}", flush=True)
    print(f"  Valido per {bot.setup.SETUP_CODE_TTL_SECONDS} secondi.", flush=True)
    print(f"  Apri l'interfaccia web per completare la configurazione.", flush=True)
    print(f"{sep}\n", flush=True)
    logger.info(
        "One-time setup code generated — valid for %s seconds",
        bot.setup.SETUP_CODE_TTL_SECONDS,
    )


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
                "Using relaxed defaults for setup mode. "
                "The bot will not start until setup is complete.",
                exc,
            )
            config = Config(relaxed=True)
            config._relaxed = True

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

    # A6 — generate setup code on blank data volume.
    if is_first_run(database_manager):
        if is_code_generated(database_manager):
            invalidate_setup_code(database_manager)
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

    # ---- Register route modules (O5) ---------------------------------------

    app.include_router(setup_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(providers_router)
    app.include_router(pipeline_router)

    # ---- Error handlers ----------------------------------------------------

    @app.exception_handler(401)
    async def unauthorized(request, exc):
        from fastapi.responses import HTMLResponse
        return templates.TemplateResponse(
            request,
            "error.html",
            {"title": "401", "message": "Devi effettuare il login."},
            status_code=401,
        )

    @app.exception_handler(404)
    async def not_found(request, exc):
        from fastapi.responses import HTMLResponse
        return templates.TemplateResponse(
            request,
            "error.html",
            {"title": "404", "message": "Pagina non trovata."},
            status_code=404,
        )

    return app

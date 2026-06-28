"""
FastAPI application factory for the web frontend control plane.

Initialises application services (Config, DatabaseManager, SecretStore,
ConfigService, StateChecker, RuntimeManager) and registers the following
route groups:

- ``/setup`` — guided onboarding wizard (W2)
- ``/login`` / ``/logout`` — authentication
- ``/admin/*`` — administration pages
- ``/api/*`` — JSON API endpoints for the frontend JS
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer

from bot.config import Config
from bot.config_service import ConfigService
from bot.database import DatabaseManager, SecretStore
from bot.exceptions import ConfigError, ResourceInUseError
from bot.runtime_manager import RuntimeManager
from bot.setup import (
    generate_setup_code,
    invalidate_setup_code,
    is_code_generated,
    is_first_run,
    validate_setup_code,
)
from bot.state import AppState, StateChecker

from bot.capabilities import (
    CapabilityModel,
    _classify_openrouter_model,
    _classify_openrouter_metadata,
    detect_capabilities,
    probe_openrouter_capabilities,
)
import bot.recovery
import bot.setup
from bot.recovery import generate_recovery_code
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
from bot.web.setup_wizard import (
    PROVIDER_PRESETS,
    build_summary,
    create_pipeline_from_wizard,
    get_active_pipeline_profile_id,
    get_capabilities,
    get_current_step,
    get_next_step,
    get_pipeline_mode,
    get_provider_config,
    get_provider_model,
    get_step_meta,
    get_step_number,
    get_telegram_token,
    get_total_steps,
    is_wizard_complete,
    reset_wizard,
    save_capabilities,
    save_pipeline_mode,
    save_provider_config,
    save_provider_model,
    save_telegram_token,
    set_active_pipeline_profile_id,
    set_current_step,
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

    # W6 — Generate a recovery code on every startup when admin exists.
    # The code is printed in the logs so the administrator can always
    # recover access, even without Telegram or the frontend credentials.
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

    # ---- Routes: Setup wizard (W2) ------------------------------------------

    @app.get("/setup", response_class=HTMLResponse)
    async def setup_wizard(request: Request):
        """Render the guided onboarding wizard at the current step."""
        session = _session(request)
        admin_exists = has_admin(database_manager)
        current_step = get_current_step(database_manager)
        wizard_done = is_wizard_complete(database_manager)

        # Already logged in as admin and wizard done → go to dashboard
        if session is not None and session.get("admin") and admin_exists and wizard_done:
            return RedirectResponse(url="/admin/dashboard", status_code=303)

        # Wizard complete but not logged in → go to login
        if admin_exists and wizard_done:
            return RedirectResponse(url="/login", status_code=303)

        # Override step via query param (for resuming at a specific point)
        requested_step = request.query_params.get("step", "")
        if requested_step and requested_step in (
            "step_telegram", "step_provider", "step_capabilities",
            "step_pipeline", "step_verify",
        ):
            current_step = requested_step

        # Ensure a session cookie exists (CSRF storage)
        if session is None:
            session = {"csrf_token": generate_csrf_token()}
            response = templates.TemplateResponse(
                request,
                "setup.html",
                {
                    "csrf_token": session["csrf_token"],
                    "current_step": current_step,
                    "step_meta": get_step_meta(current_step),
                    "step_number": get_step_number(current_step),
                    "total_steps": get_total_steps(),
                    "admin_exists": admin_exists,
                    "code_generated": is_code_generated(database_manager),
                    "wizard_data": _build_wizard_context(database_manager, secret_store),
                    "provider_presets": PROVIDER_PRESETS,
                    "error": request.query_params.get("error", ""),
                    "success": "",
                },
            )
            _session_response(response, session)
            return response

        return templates.TemplateResponse(
            request,
            "setup.html",
            {
                "csrf_token": session.get("csrf_token", generate_csrf_token()),
                "current_step": current_step,
                "step_meta": get_step_meta(current_step),
                "step_number": get_step_number(current_step),
                "total_steps": get_total_steps(),
                "admin_exists": admin_exists,
                "code_generated": is_code_generated(database_manager),
                "wizard_data": _build_wizard_context(database_manager, secret_store),
                "provider_presets": PROVIDER_PRESETS,
                "error": request.query_params.get("error", ""),
                "success": request.query_params.get("success", ""),
            },
        )

    @app.post("/api/setup/step")
    async def api_setup_step(request: Request):
        """JSON API — process a wizard step and return next step or errors.

        Request body::

            {"step": "step_code", "data": {"setup_code": "ABC12345"}}
            {"step": "step_admin", "data": {"password": "...", "password_confirm": "..."}}
            {"step": "step_telegram", "data": {"token": "123:abc"}}
            {"step": "step_provider", "data": {"type": "openai", "api_key": "...", "endpoint": "..."}}
            {"step": "step_capabilities", "data": {"capabilities": {...}}}
            {"step": "step_pipeline", "data": {"mode": "single"}}
            {"step": "step_verify", "data": {}}
        """
        session = _session(request)
        if session is None:
            return JSONResponse(
                {"ok": False, "errors": ["Sessione non valida. Ricarica la pagina."]},
                status_code=400,
            )

        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return JSONResponse(
                {"ok": False, "errors": ["Richiesta JSON non valida."]},
                status_code=400,
            )

        step = body.get("step", "")
        data = body.get("data", {})

        result = _process_step(database_manager, secret_store, config_service,
                               runtime_manager, step, data)
        if result.get("ok"):
            # Advance to next step
            next_step = get_next_step(step)
            if next_step:
                set_current_step(database_manager, next_step)
                result["next_step"] = next_step
                result["next_step_meta"] = get_step_meta(next_step)
            else:
                result["next_step"] = None
        return JSONResponse(result)

    @app.post("/api/setup/test-telegram")
    async def api_test_telegram(request: Request):
        """Test a Telegram bot token and return bot info."""
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return JSONResponse({"ok": False, "error": "Richiesta JSON non valida."},
                                status_code=400)

        token = (body.get("token") or "").strip()
        if not token:
            return JSONResponse({"ok": False, "error": "Inserisci un token."})

        import httpx
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
                if resp.status_code == 200:
                    bot_info = resp.json().get("result", {})
                    return JSONResponse({
                        "ok": True,
                        "bot": {
                            "id": bot_info.get("id"),
                            "username": bot_info.get("username"),
                            "first_name": bot_info.get("first_name"),
                        },
                    })
                else:
                    error_data = resp.json().get("description", "Token non valido")
                    return JSONResponse({"ok": False, "error": error_data})
        except httpx.TimeoutException:
            return JSONResponse({"ok": False, "error": "Timeout: server Telegram non raggiungibile."})
        except httpx.RequestError as exc:
            return JSONResponse({"ok": False, "error": f"Errore di connessione: {exc}"})

    @app.post("/api/setup/test-provider")
    async def api_test_provider(request: Request):
        """Test an AI provider connection.

        Returns the same JSON schema as ``/api/providers/test``
        (``ok``, ``auth_ok``, ``models_ok``, ``capabilities``,
        ``pipeline_status``, ``user_message``, ``warnings``) plus a
        ``models`` list for the setup wizard's capability detection step.
        """
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return JSONResponse({"ok": False, "error": "Richiesta JSON non valida."},
                                status_code=400)

        provider_type = (body.get("type") or body.get("provider_type") or "").strip()
        api_key = (body.get("api_key") or "").strip()
        endpoint = (body.get("endpoint") or "").strip()
        model_name = (body.get("model_name") or body.get("model") or "").strip()

        if not provider_type:
            return JSONResponse({"ok": False, "error": "Seleziona un provider."})
        if not api_key:
            return JSONResponse({"ok": False, "error": "Inserisci una chiave API."})

        import httpx
        try:
            result = await _test_provider_connection(
                provider_type, api_key, endpoint, model_name,
            )
            return JSONResponse(result)
        except httpx.TimeoutException:
            return JSONResponse({
                "ok": False, "auth_ok": False, "models_ok": False,
                "capabilities": _BLANK_CAPS,
                "pipeline_status": "not_compatible",
                "user_message": "❌ Timeout: server non raggiungibile.",
                "warnings": ["Il server non ha risposto entro 15 secondi."],
                "models": [],
            })
        except httpx.RequestError as exc:
            return JSONResponse({
                "ok": False, "auth_ok": False, "models_ok": False,
                "capabilities": _BLANK_CAPS,
                "pipeline_status": "not_compatible",
                "user_message": f"❌ Errore di connessione: {exc}",
                "warnings": ["Verifica l'URL dell'endpoint e la connettività di rete."],
                "models": [],
            })

    @app.post("/api/setup/detect-capabilities")
    async def api_detect_capabilities(request: Request):
        """Detect capabilities from a provider's model list.

        Uses the typed :class:`CapabilityModel` from ``bot.capabilities``
        instead of inline heuristics (P2).

        For OpenRouter, probes model metadata from the Models API when
        provider credentials are available in the wizard state.
        """
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return JSONResponse({"ok": False, "error": "Richiesta JSON non valida."},
                                status_code=400)

        provider_type = (body.get("type") or "").strip()
        models = body.get("models", [])

        if not models:
            return JSONResponse({
                "ok": True,
                "capabilities": {"transcription": False, "text_generation": False,
                                 "refinement": False, "streaming_refinement": False,
                                 "models": [], "note": "Nessun modello rilevato."},
            })

        model_name = models[0] if models else ""

        # --- OpenRouter: probe model metadata ---
        if provider_type == "openrouter":
            saved = get_provider_config(database_manager, secret_store)
            api_key = saved.get("api_key", "")
            ep = saved.get("endpoint", "") or PROVIDER_PRESETS.get("openrouter", {}).get("default_endpoint", "")
            if api_key:
                caps, _ = await probe_openrouter_capabilities(api_key, ep, model_name)
            else:
                caps = CapabilityModel(text_generation=True, refinement=True)
        else:
            # Use the typed static detection for known adapter types.
            caps = detect_capabilities(provider_type, model_name)

        relevant_models = models[:10]

        # Provider-specific defaults for model list (keep as UX hint)
        if provider_type == "gemini" and not relevant_models:
            relevant_models = ["gemini-2.0-flash", "gemini-2.5-pro"]

        result = caps.to_dict()
        result["models"] = relevant_models
        return JSONResponse({"ok": True, "capabilities": result})

    # Non-JS fallback: form-based step processing
    @app.post("/setup")
    async def setup_post(request: Request):
        """Form-based step processing (fallback when JS is unavailable)."""
        form_data = await request.form()
        csrf_token_val = form_data.get("csrf_token", "")
        step = form_data.get("_step", "")

        session = _session(request)
        if not validate_csrf_token(session or {}, csrf_token_val):
            return RedirectResponse(url="/setup?error=csrf", status_code=303)

        error_param = ""

        if step == "step_code":
            setup_code = form_data.get("setup_code", "")
            if not validate_setup_code(database_manager, setup_code):
                error_param = "invalid_code"

        elif step == "step_admin":
            password = form_data.get("admin_password", "")
            confirm = form_data.get("admin_password_confirm", "")
            if password != confirm:
                error_param = "password_mismatch"
            if not error_param:
                result = _process_step(database_manager, secret_store, config_service,
                                       runtime_manager, step,
                                       {"password": password, "password_confirm": confirm})
                if not result.get("ok"):
                    error_param = "admin_failed"

        elif step == "step_telegram":
            token = form_data.get("telegram_token", "")
            if not token:
                error_param = "token_empty"
            else:
                result = _process_step(database_manager, secret_store, config_service,
                                       runtime_manager, step, {"token": token})
                if not result.get("ok"):
                    error_param = "token_invalid"

        elif step == "step_provider":
            ptype = form_data.get("provider_type", "")
            api_key = form_data.get("provider_api_key", "")
            endpoint = form_data.get("provider_endpoint", "")
            if not ptype:
                error_param = "provider_empty"
            elif not api_key:
                error_param = "api_key_empty"
            else:
                result = _process_step(database_manager, secret_store, config_service,
                                       runtime_manager, step,
                                       {"type": ptype, "api_key": api_key, "endpoint": endpoint})
                if not result.get("ok"):
                    error_param = "provider_invalid"

        elif step == "step_pipeline":
            mode = form_data.get("pipeline_mode", "single")
            model_name = form_data.get("provider_model", "")
            _process_step(database_manager, secret_store, config_service,
                          runtime_manager, step,
                          {"mode": mode, "model": model_name})

        elif step == "step_verify":
            _process_step(database_manager, secret_store, config_service,
                          runtime_manager, "step_verify", {})

        if error_param:
            return RedirectResponse(url=f"/setup?error={error_param}", status_code=303)

        # Determine next step (using wizard state after processing)
        next_step = get_next_step(step) if not error_param else step
        if next_step:
            set_current_step(database_manager, next_step)

        if next_step == "step_done" or step == "step_verify":
            return RedirectResponse(url="/login?setup=ok", status_code=303)

        return RedirectResponse(url=f"/setup?step={next_step}", status_code=303)

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
                    "recovery_ok": request.query_params.get("recovery", "") == "ok",
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
                "recovery_ok": request.query_params.get("recovery", "") == "ok",
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

    # ---- Routes: Recovery (W6) -----------------------------------------------

    @app.get("/recovery", response_class=HTMLResponse)
    async def recovery_page(request: Request):
        session = _session(request)
        recovery_approved = session is not None and session.get("recovery_approved", False)

        if session is None:
            session = {"csrf_token": generate_csrf_token()}
            response = templates.TemplateResponse(
                request,
                "recovery.html",
                {
                    "csrf_token": session["csrf_token"],
                    "recovery_approved": False,
                    "error": request.query_params.get("error", ""),
                    "success": request.query_params.get("success", ""),
                },
            )
            _session_response(response, session)
            return response

        return templates.TemplateResponse(
            request,
            "recovery.html",
            {
                "csrf_token": session.get("csrf_token", generate_csrf_token()),
                "recovery_approved": recovery_approved,
                "error": request.query_params.get("error", ""),
                "success": request.query_params.get("success", ""),
            },
        )

    @app.post("/recovery")
    async def recovery_post(
        request: Request,
        recovery_code: str = Form(""),
        csrf_token: str = Form(""),
    ):
        session = _session(request)
        if not validate_csrf_token(session or {}, csrf_token):
            return RedirectResponse(url="/recovery?error=csrf", status_code=303)

        if not bot.recovery.validate_recovery_code(database_manager, recovery_code):
            return RedirectResponse(url="/recovery?error=invalid_code", status_code=303)

        # Store approval in session — next step shows password form
        response = RedirectResponse(url="/recovery", status_code=303)
        session_data = session or {}
        session_data["recovery_approved"] = True
        session_data["csrf_token"] = generate_csrf_token()
        _session_response(response, session_data)
        return response

    @app.post("/recovery/reset")
    async def recovery_reset(
        request: Request,
        password: str = Form(""),
        password_confirm: str = Form(""),
        csrf_token: str = Form(""),
    ):
        session = _session(request)
        if session is None or not session.get("recovery_approved"):
            return RedirectResponse(url="/recovery?error=unauthorized", status_code=303)

        if not validate_csrf_token(session, csrf_token):
            return RedirectResponse(url="/recovery?error=csrf", status_code=303)

        if password != password_confirm:
            return RedirectResponse(url="/recovery?error=mismatch", status_code=303)

        if len(password) < 8:
            return RedirectResponse(url="/recovery?error=too_short", status_code=303)

        set_admin_password(database_manager, password)
        bot.recovery.invalidate_recovery_code(database_manager)

        # Clear session and redirect to login
        response = RedirectResponse(url="/login?recovery=ok", status_code=303)
        response.delete_cookie("session")
        return response

    @app.post("/api/recovery/generate")
    async def api_recovery_generate(request: Request):
        """Generate a new recovery code and return it.
        Requires admin authentication.
        """
        session = _session(request)
        if session is None or not session.get("admin"):
            return JSONResponse({"ok": False, "error": "Non autorizzato."}, status_code=401)

        code = bot.recovery.generate_recovery_code(database_manager)
        logger.info("Recovery code generated via API by admin")
        return JSONResponse({"ok": True, "code": code})

    # ---- Routes: Admin ------------------------------------------------------

    @app.get("/admin/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request):
        session = _login_required(request)
        state = state_checker.get_state()
        health = runtime_manager.get_health()

        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "csrf_token": session.get("csrf_token", generate_csrf_token()),
                "session": session,
                "state": state,
                "health": health,
            },
        )

    @app.post("/admin/bot/start")
    async def admin_bot_start(request: Request):
        _login_required(request)
        session = _session(request) or {}
        form_data = await request.form()
        csrf = form_data.get("csrf_token", "")
        if not validate_csrf_token(session, csrf):
            return RedirectResponse(url="/admin/dashboard?error=csrf", status_code=303)

        try:
            await runtime_manager.start_async()
            logger.info("Bot started from admin dashboard")
        except RuntimeError as exc:
            logger.warning("Bot start failed from dashboard: %s", exc)
            return RedirectResponse(url="/admin/dashboard?error=start_failed", status_code=303)

        return RedirectResponse(url="/admin/dashboard", status_code=303)

    @app.post("/admin/bot/stop")
    async def admin_bot_stop(request: Request):
        _login_required(request)
        session = _session(request) or {}
        form_data = await request.form()
        csrf = form_data.get("csrf_token", "")
        if not validate_csrf_token(session, csrf):
            return RedirectResponse(url="/admin/dashboard?error=csrf", status_code=303)

        await runtime_manager.stop_async()
        logger.info("Bot stopped from admin dashboard")
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    # ---- Routes: Admin — Provider management (W3 foundation) ----------------

    @app.get("/admin/providers", response_class=HTMLResponse)
    async def admin_providers(request: Request):
        """Provider connection management page."""
        session = _login_required(request)
        providers = database_manager.list_providers()

        return templates.TemplateResponse(
            request,
            "providers.html",
            {
                "csrf_token": session.get("csrf_token", generate_csrf_token()),
                "session": session,
                "providers": providers,
                "provider_presets": PROVIDER_PRESETS,
            },
        )

    @app.post("/admin/providers/create")
    async def admin_provider_create(request: Request):
        """Create a provider connection from the admin UI."""
        session = _login_required(request)
        form_data = await request.form()
        csrf = form_data.get("csrf_token", "")
        if not validate_csrf_token(session, csrf):
            return RedirectResponse(url="/admin/providers?error=csrf", status_code=303)

        provider_type = (form_data.get("provider_type") or "").strip()
        display_name = (form_data.get("name") or "").strip()
        endpoint = (form_data.get("endpoint") or "").strip()
        api_key = (form_data.get("api_key") or "").strip()
        model_name = (form_data.get("model_name") or "").strip()

        if provider_type not in PROVIDER_PRESETS:
            return RedirectResponse(
                url="/admin/providers?error=invalid_type",
                status_code=303,
            )

        adapter_type = _adapter_type_for_provider(provider_type)
        if not display_name:
            display_name = PROVIDER_PRESETS[provider_type]["label"]
        if not endpoint:
            endpoint = PROVIDER_PRESETS[provider_type].get("default_endpoint", "")

        if provider_type in {"openai", "gemini", "openrouter", "custom"} and not api_key:
            return RedirectResponse(
                url="/admin/providers?error=missing_key",
                status_code=303,
            )

        try:
            # For OpenRouter: probe model metadata for accurate capabilities.
            if provider_type == "openrouter":
                probed, _ = await probe_openrouter_capabilities(api_key, endpoint, model_name)
                capabilities = probed.to_dict()
            else:
                capabilities = detect_capabilities(adapter_type, model_name).to_dict()

            if model_name:
                capabilities["models"] = [model_name]

            provider_id = database_manager.add_provider(
                name=display_name,
                adapter_type=adapter_type,
                endpoint=endpoint or None,
                credentials=api_key or None,
                capabilities=capabilities,
                enabled=True,
            )
            logger.info(
                "Admin provider: created '%s' (id=%s, adapter=%s)",
                display_name,
                provider_id,
                adapter_type,
            )
        except Exception:
            logger.exception("Failed to create provider connection")
            return RedirectResponse(
                url="/admin/providers?error=create_failed",
                status_code=303,
            )

        return RedirectResponse(
            url="/admin/pipeline?success=provider_created",
            status_code=303,
        )

    # ---- Routes: Admin — Provider detail (model management) ------------------

    @app.get("/admin/providers/{provider_id}", response_class=HTMLResponse)
    async def admin_provider_detail(request: Request, provider_id: int):
        """Provider detail page with model discovery and management."""
        session = _login_required(request)
        provider = database_manager.get_provider(provider_id)
        if provider is None:
            return templates.TemplateResponse(
                request, "error.html",
                {"title": "404", "message": "Provider non trovato."},
                status_code=404,
            )

        models = database_manager.list_provider_models(provider_id)
        return templates.TemplateResponse(
            request,
            "provider_detail.html",
            {
                "csrf_token": session.get("csrf_token", generate_csrf_token()),
                "session": session,
                "provider": provider,
                "models": models,
                "provider_presets": PROVIDER_PRESETS,
            },
        )

    @app.post("/admin/providers/{provider_id}/edit")
    async def admin_provider_edit(request: Request, provider_id: int):
        """Edit a provider connection."""
        session = _login_required(request)
        form_data = await request.form()
        csrf = form_data.get("csrf_token", "")
        if not validate_csrf_token(session, csrf):
            return RedirectResponse(
                url=f"/admin/providers/{provider_id}?error=csrf",
                status_code=303,
            )

        name = (form_data.get("name") or "").strip()
        endpoint = (form_data.get("endpoint") or "").strip()
        api_key = (form_data.get("api_key") or "").strip()
        enabled = form_data.get("enabled", "1") == "1"

        updates: Dict[str, Any] = {}
        if name:
            updates["name"] = name
        if endpoint:
            updates["endpoint"] = endpoint
        if api_key:
            updates["credentials"] = api_key
        updates["enabled"] = enabled

        try:
            database_manager.update_provider(provider_id, **updates)  # type: ignore[arg-type]
            logger.info("Admin provider: updated id=%s", provider_id)
        except ResourceInUseError as exc:
            logger.warning("Provider disable blocked: %s", exc)
            return RedirectResponse(
                url=f"/admin/providers/{provider_id}?error=provider_in_use",
                status_code=303,
            )
        except Exception:
            logger.exception("Failed to update provider id=%s", provider_id)
            return RedirectResponse(
                url=f"/admin/providers/{provider_id}?error=update_failed",
                status_code=303,
            )

        return RedirectResponse(
            url=f"/admin/providers/{provider_id}?success=updated",
            status_code=303,
        )

    @app.post("/admin/providers/{provider_id}/delete")
    async def admin_provider_delete(request: Request, provider_id: int):
        """Delete a provider connection."""
        session = _login_required(request)
        form_data = await request.form()
        csrf = form_data.get("csrf_token", "")
        if not validate_csrf_token(session, csrf):
            return RedirectResponse(
                url="/admin/providers?error=csrf", status_code=303,
            )

        try:
            database_manager.delete_provider(provider_id)
            logger.info("Admin provider: deleted id=%s", provider_id)
        except ResourceInUseError as exc:
            logger.warning("Provider delete blocked: %s", exc)
            return RedirectResponse(
                url="/admin/providers?error=provider_in_use", status_code=303,
            )
        except Exception:
            logger.exception("Failed to delete provider id=%s", provider_id)
            return RedirectResponse(
                url="/admin/providers?error=delete_failed", status_code=303,
            )

        return RedirectResponse(
            url="/admin/providers?success=deleted", status_code=303,
        )

    # ---- Routes: Admin — Provider model management API ----------------------

    @app.post("/api/providers/{provider_id}/discover")
    async def api_provider_discover_models(request: Request, provider_id: int):
        """Discover models from a provider's API and register them under
        provider_models."""
        session = _session(request)
        if session is None or not session.get("admin"):
            return JSONResponse({"ok": False, "error": "Non autorizzato."},
                                status_code=401)

        provider = database_manager.get_provider(provider_id)
        if provider is None:
            return JSONResponse({"ok": False, "error": "Provider non trovato."},
                                status_code=404)

        adapter_type = provider.get("adapter_type", "")
        credentials = provider.get("credentials") or ""
        endpoint = provider.get("endpoint") or ""
        purpose = (request.query_params.get("purpose") or "all_recommended").strip()
        query = (request.query_params.get("query") or "").strip().lower()
        limit = _parse_discovery_limit(request.query_params.get("limit"))

        if not credentials:
            return JSONResponse({
                "ok": False,
                "error": "Provider senza credenziali. Impossibile "
                         "scoprire i modelli.",
            })

        import httpx
        models_discovered: list[Any] = []
        is_openrouter = adapter_type == "openai-compat" and "openrouter" in endpoint.lower()

        try:
            if is_openrouter:
                url = f"{endpoint.rstrip('/')}/models" if endpoint else "https://openrouter.ai/api/v1/models"
                headers = {"Authorization": f"Bearer {credentials}"}
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json().get("data", [])
                        raw_models = [m for m in data if isinstance(m, dict) and m.get("id")]
                        models_discovered = _select_openrouter_models(
                            raw_models,
                            purpose=purpose,
                            query=query,
                            limit=limit,
                        )
            elif adapter_type in ("openai-native", "openai", "openai-compat"):
                url = f"{endpoint.rstrip('/')}/models" if endpoint else "https://api.openai.com/v1/models"
                headers = {"Authorization": f"Bearer {credentials}"}
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json().get("data", [])
                        models_discovered = [m["id"] for m in data
                                             if isinstance(m, dict) and m.get("id")][:limit]

            elif adapter_type in ("gemini-native", "gemini"):
                url = f"https://generativelanguage.googleapis.com/v1/models?key={credentials}"
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        data = resp.json().get("models", [])
                        models_discovered = [
                            m["name"].replace("models/", "")
                            for m in data if isinstance(m, dict) and m.get("name")
                        ][:limit]

            # Classify and register each model
            registered: list[dict[str, Any]] = []
            counts = {
                "refinement": 0,
                "transcription": 0,
                "single_pass": 0,
                "not_recommended": 0,
            }
            for discovered in models_discovered:
                model_meta: dict[str, Any] | None = (
                    discovered if isinstance(discovered, dict) else None
                )
                model_id = (
                    model_meta.get("id")
                    if model_meta is not None
                    else str(discovered)
                )
                if not model_id:
                    continue
                # Classify capabilities
                if adapter_type in ("openai-native", "openai"):
                    # OpenAI: separate Whisper for STT, GPT for text
                    caps = _classify_openai_model(model_id)
                elif adapter_type in ("gemini-native", "gemini"):
                    caps = _classify_gemini_model(model_id)
                elif is_openrouter and model_meta is not None:
                    caps_model = _classify_openrouter_model(model_meta)
                    caps = caps_model.to_dict()
                    category = _openrouter_model_category(model_meta)
                    counts[category] = counts.get(category, 0) + 1
                elif adapter_type == "openai-compat":
                    caps = _classify_openai_compat_model(model_id)
                else:
                    caps = _classify_openai_compat_model(model_id)

                # Register in provider_models
                entry_id = database_manager.add_provider_model(
                    provider_id=provider_id,
                    model_id=model_id,
                    display_name=model_id,
                    capabilities=caps,
                    detected=True,
                    enabled=True,
                )
                registered.append({
                    "id": entry_id,
                    "model_id": model_id,
                    "capabilities": caps,
                    "category": (
                        _openrouter_model_category(model_meta)
                        if is_openrouter and model_meta is not None
                        else None
                    ),
                })

            return JSONResponse({
                "ok": True,
                "discovered": len(registered),
                "models": registered,
                "purpose": purpose,
                "limit": limit,
                "counts": counts,
                "guided": is_openrouter,
            })

        except httpx.TimeoutException:
            return JSONResponse({
                "ok": False,
                "error": "Timeout durante la connessione al provider.",
            })
        except httpx.RequestError as exc:
            return JSONResponse({
                "ok": False,
                "error": f"Errore di connessione: {exc}",
            })
        except Exception as exc:
            logger.exception("Model discovery failed for provider %s", provider_id)
            return JSONResponse({
                "ok": False,
                "error": f"Errore interno: {exc}",
            })

    @app.get("/api/providers/{provider_id}/catalog")
    async def api_provider_catalog(request: Request, provider_id: int):
        """Preview provider catalog entries without registering them."""
        session = _session(request)
        if session is None or not session.get("admin"):
            return JSONResponse({"ok": False, "error": "Non autorizzato."},
                                status_code=401)

        provider = database_manager.get_provider(provider_id)
        if provider is None:
            return JSONResponse({"ok": False, "error": "Provider non trovato."},
                                status_code=404)

        adapter_type = provider.get("adapter_type", "")
        credentials = provider.get("credentials") or ""
        endpoint = provider.get("endpoint") or ""
        purpose = (request.query_params.get("purpose") or "all_recommended").strip()
        query = (request.query_params.get("query") or "").strip().lower()
        limit = _parse_discovery_limit(request.query_params.get("limit"))
        is_openrouter = adapter_type == "openai-compat" and "openrouter" in endpoint.lower()

        if not is_openrouter:
            return JSONResponse({
                "ok": False,
                "error": "La ricerca catalogo è disponibile per OpenRouter.",
            }, status_code=400)

        if not credentials:
            return JSONResponse({
                "ok": False,
                "error": "Provider senza credenziali. Impossibile cercare i modelli.",
            })

        import httpx

        try:
            url = f"{endpoint.rstrip('/')}/models" if endpoint else "https://openrouter.ai/api/v1/models"
            headers = {"Authorization": f"Bearer {credentials}"}
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    return JSONResponse({
                        "ok": False,
                        "error": f"OpenRouter ha risposto con HTTP {resp.status_code}.",
                    })
                data = resp.json().get("data", [])

            raw_models = [m for m in data if isinstance(m, dict) and m.get("id")]
            selected = _select_openrouter_models(
                raw_models,
                purpose=purpose,
                query=query,
                limit=limit,
            )
            models = [_openrouter_catalog_item(model) for model in selected]
            counts: dict[str, int] = {
                "refinement": 0,
                "transcription": 0,
                "single_pass": 0,
                "not_recommended": 0,
            }
            for model in raw_models:
                category = _openrouter_model_category(model)
                counts[category] = counts.get(category, 0) + 1

            return JSONResponse({
                "ok": True,
                "models": models,
                "purpose": purpose,
                "query": query,
                "limit": limit,
                "counts": counts,
            })
        except httpx.TimeoutException:
            return JSONResponse({
                "ok": False,
                "error": "Timeout durante la connessione a OpenRouter.",
            })
        except httpx.RequestError as exc:
            return JSONResponse({
                "ok": False,
                "error": f"Errore di connessione: {exc}",
            })
        except Exception as exc:
            logger.exception("OpenRouter catalog search failed for provider %s", provider_id)
            return JSONResponse({
                "ok": False,
                "error": f"Errore interno: {exc}",
            })

    @app.post("/api/providers/{provider_id}/apply-curated")
    async def api_provider_apply_curated(request: Request, provider_id: int):
        """Register the curated OpenRouter shortlist for a provider.

        Fetches the OpenRouter catalog to get real capability metadata for each
        curated model.  Models not found in the catalog are registered with
        estimated capabilities.  Already-registered models are skipped.
        """
        session = _session(request)
        if session is None or not session.get("admin"):
            return JSONResponse({"ok": False, "error": "Non autorizzato."},
                                status_code=401)

        provider = database_manager.get_provider(provider_id)
        if provider is None:
            return JSONResponse({"ok": False, "error": "Provider non trovato."},
                                status_code=404)

        adapter_type = provider.get("adapter_type", "")
        credentials = provider.get("credentials") or ""
        endpoint = provider.get("endpoint") or ""
        is_openrouter = (
            adapter_type == "openai-compat"
            and "openrouter" in endpoint.lower()
        )
        if not is_openrouter:
            return JSONResponse(
                {"ok": False, "error": "La shortlist curata è disponibile solo per OpenRouter."},
                status_code=400,
            )
        if not credentials:
            return JSONResponse({
                "ok": False,
                "error": "Provider senza credenziali. Impossibile recuperare il catalogo.",
            })

        import httpx

        # Fetch the OpenRouter catalog once and build a lookup by model ID.
        catalog_by_id: dict[str, dict] = {}
        try:
            url = f"{endpoint.rstrip('/')}/models" if endpoint else "https://openrouter.ai/api/v1/models"
            headers = {"Authorization": f"Bearer {credentials}"}
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    for m in resp.json().get("data", []):
                        mid = m.get("id") if isinstance(m, dict) else None
                        if mid:
                            catalog_by_id[mid] = m
        except (httpx.TimeoutException, httpx.RequestError):
            pass  # Continue with estimated capabilities if the catalog is unreachable.

        existing_ids = {m["model_id"] for m in database_manager.list_provider_models(provider_id)}
        registered: list[dict] = []
        skipped: list[str] = []

        for model_id in OPENROUTER_CURATED_SHORTLIST:
            if model_id in existing_ids:
                skipped.append(model_id)
                continue
            catalog_entry = catalog_by_id.get(model_id)
            if catalog_entry:
                caps_model = _classify_openrouter_model(catalog_entry)
                caps = caps_model.to_dict()
                display_name = catalog_entry.get("name") or model_id
                detected = True
            else:
                caps = _classify_openai_compat_model(model_id)
                display_name = model_id
                detected = False
            try:
                entry_id = database_manager.add_provider_model(
                    provider_id=provider_id,
                    model_id=model_id,
                    display_name=display_name,
                    capabilities=caps,
                    detected=detected,
                    enabled=True,
                )
                registered.append({"id": entry_id, "model_id": model_id, "capabilities": caps})
            except Exception:
                logger.exception("Failed to register curated model %s", model_id)

        return JSONResponse({
            "ok": True,
            "registered": len(registered),
            "skipped": len(skipped),
            "models": registered,
        })

    @app.delete("/api/providers/{provider_id}/models/cleanup")
    async def api_provider_cleanup_models(request: Request, provider_id: int):
        """Remove all registered models that are not in the curated shortlist.

        Models referenced by the active pipeline are skipped and reported
        separately so they are never silently deleted.
        """
        session = _session(request)
        if session is None or not session.get("admin"):
            return JSONResponse({"ok": False, "error": "Non autorizzato."},
                                status_code=401)

        provider = database_manager.get_provider(provider_id)
        if provider is None:
            return JSONResponse({"ok": False, "error": "Provider non trovato."},
                                status_code=404)

        curated_ids = set(OPENROUTER_CURATED_SHORTLIST)
        all_models = database_manager.list_provider_models(provider_id)
        removed = 0
        skipped_in_use: list[str] = []

        for model in all_models:
            if model["model_id"] in curated_ids:
                continue
            try:
                database_manager.delete_provider_model(model["id"])
                removed += 1
            except Exception as exc:
                skipped_in_use.append(model["model_id"])
                logger.warning("Could not remove model %s: %s", model["model_id"], exc)

        return JSONResponse({
            "ok": True,
            "removed": removed,
            "skipped_in_use": skipped_in_use,
        })

    @app.get("/api/providers/{provider_id}/models")
    async def api_provider_list_models(request: Request, provider_id: int):
        """List all registered models for a provider."""
        session = _session(request)
        if session is None or not session.get("admin"):
            return JSONResponse({"ok": False, "error": "Non autorizzato."},
                                status_code=401)

        models = database_manager.list_provider_models(provider_id)
        return JSONResponse({"ok": True, "models": models})

    @app.post("/api/providers/{provider_id}/models")
    async def api_provider_add_model(request: Request, provider_id: int):
        """Manually add a model to a provider."""
        session = _session(request)
        if session is None or not session.get("admin"):
            return JSONResponse({"ok": False, "error": "Non autorizzato."},
                                status_code=401)

        provider = database_manager.get_provider(provider_id)
        if provider is None:
            return JSONResponse({"ok": False, "error": "Provider non trovato."},
                                status_code=404)

        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return JSONResponse({"ok": False, "error": "Richiesta JSON non valida."},
                                status_code=400)

        model_id = (body.get("model_id") or "").strip()
        display_name = (body.get("display_name") or model_id).strip()
        capabilities = body.get("capabilities")

        if not model_id:
            return JSONResponse({"ok": False, "error": "model_id è obbligatorio."})

        # Auto-classify capabilities if not provided
        if capabilities is None:
            adapter_type = provider.get("adapter_type", "")
            caps = _classify_openai_compat_model(model_id)
            if adapter_type in ("openai-native", "openai"):
                caps = _classify_openai_model(model_id)
            elif adapter_type in ("gemini-native", "gemini"):
                caps = _classify_gemini_model(model_id)
        else:
            caps = capabilities

        try:
            entry_id = database_manager.add_provider_model(
                provider_id=provider_id,
                model_id=model_id,
                display_name=display_name,
                capabilities=caps,
                detected=False,
                enabled=True,
            )
            return JSONResponse({
                "ok": True,
                "id": entry_id,
                "model_id": model_id,
                "capabilities": caps,
            })
        except Exception as exc:
            logger.exception("Failed to add model")
            return JSONResponse({"ok": False, "error": str(exc)})

    @app.post("/api/providers/models/{entry_id}/capabilities")
    async def api_provider_update_model_caps(request: Request, entry_id: int):
        """Update capabilities for a provider model entry."""
        session = _session(request)
        if session is None or not session.get("admin"):
            return JSONResponse({"ok": False, "error": "Non autorizzato."},
                                status_code=401)

        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return JSONResponse({"ok": False, "error": "Richiesta JSON non valida."},
                                status_code=400)

        capabilities = body.get("capabilities")
        if not capabilities:
            return JSONResponse({"ok": False, "error": "capabilities è obbligatorio."})

        try:
            database_manager.set_model_capabilities(
                entry_id, capabilities, mark_overridden=True,
            )
            return JSONResponse({"ok": True})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)})

    @app.post("/api/providers/models/{entry_id}/toggle")
    async def api_provider_toggle_model(request: Request, entry_id: int):
        """Enable or disable a provider model."""
        session = _session(request)
        if session is None or not session.get("admin"):
            return JSONResponse({"ok": False, "error": "Non autorizzato."},
                                status_code=401)

        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return JSONResponse({"ok": False, "error": "Richiesta JSON non valida."},
                                status_code=400)

        enabled = body.get("enabled", True)
        try:
            database_manager.update_provider_model(
                entry_id, enabled=enabled,
            )
            return JSONResponse({"ok": True})
        except ResourceInUseError as exc:
            return JSONResponse({"ok": False, "error": str(exc)})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)})

    @app.post("/api/providers/models/{entry_id}/delete")
    async def api_provider_delete_model(request: Request, entry_id: int):
        """Delete a provider model entry."""
        session = _session(request)
        if session is None or not session.get("admin"):
            return JSONResponse({"ok": False, "error": "Non autorizzato."},
                                status_code=401)

        try:
            database_manager.delete_provider_model(entry_id)
            return JSONResponse({"ok": True})
        except ResourceInUseError as exc:
            return JSONResponse({"ok": False, "error": str(exc)})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)})

    # ---- Routes: Pipeline stage management API --------------------------------

    @app.post("/api/pipeline/stages")
    async def api_pipeline_update_stages(request: Request):
        """Update pipeline stages for a profile.

        Accepts JSON::

            {
                "profile_id": 1,
                "mode": "two_stage",
                "stages": [
                    {
                        "stage_type": "transcription",
                        "primary_model_id": 5,
                        "fallback_model_ids": [6, 7]
                    },
                    {
                        "stage_type": "refinement",
                        "primary_model_id": 8,
                        "fallback_model_ids": [9]
                    }
                ]
            }
        """
        session = _session(request)
        if session is None or not session.get("admin"):
            return JSONResponse({"ok": False, "error": "Non autorizzato."},
                                status_code=401)

        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return JSONResponse({"ok": False, "error": "Richiesta JSON non valida."},
                                status_code=400)

        profile_id = body.get("profile_id")
        mode = body.get("mode", "two_stage")
        stages_data = body.get("stages", [])

        if profile_id is None:
            return JSONResponse({"ok": False, "error": "profile_id è obbligatorio."})

        if mode not in ("two_stage", "single_pass"):
            return JSONResponse({"ok": False, "error": "mode deve essere two_stage o single_pass."})

        profile = database_manager.get_pipeline_profile(profile_id)
        if profile is None:
            return JSONResponse({"ok": False, "error": "Profilo pipeline non trovato."},
                                status_code=404)

        try:
            # Set the mode on the profile
            database_manager.set_pipeline_profile_mode(profile_id, mode)

            # Delete existing stages for this profile and recreate
            existing = database_manager.list_pipeline_stages(profile_id)
            for stage in existing:
                database_manager.delete_pipeline_stage(stage["id"])

            # Create new stages
            for stage_data in stages_data:
                stage_type = stage_data.get("stage_type", "")
                primary_id = stage_data.get("primary_model_id")
                fallback_ids = stage_data.get("fallback_model_ids", [])

                stage_id = database_manager.add_pipeline_stage(
                    profile_id=profile_id,
                    stage_type=stage_type,
                    primary_model_id=primary_id,
                )

                # Set fallbacks
                if fallback_ids:
                    database_manager.reorder_stage_fallbacks(
                        stage_id, fallback_ids,
                    )

            return JSONResponse({"ok": True, "profile_id": profile_id})

        except Exception as exc:
            logger.exception("Failed to update pipeline stages")
            return JSONResponse({"ok": False, "error": str(exc)})

    # ---- Routes: Admin — Pipeline management (P5) ---------------------------

    @app.get("/admin/pipeline", response_class=HTMLResponse)
    async def admin_pipeline(request: Request):
        """Pipeline configuration page."""
        session = _login_required(request)

        providers = database_manager.list_providers()
        # Attach models to each provider for model selection
        providers_with_models = []
        for p in providers:
            p_models = database_manager.list_provider_models(p["id"])
            providers_with_models.append({**p, "models": p_models})

        profile_id = get_active_pipeline_profile_id(database_manager)
        profile = None
        if profile_id is not None:
            profile = database_manager.get_pipeline_profile(profile_id)
            # Attach model info to stages for display
            if profile and profile.get("stages"):
                for stage in profile["stages"]:
                    if stage.get("primary_model_id"):
                        model_entry = database_manager.get_provider_model(
                            stage["primary_model_id"]
                        )
                        stage["_primary_model"] = model_entry
                    if stage.get("fallbacks"):
                        for fb in stage["fallbacks"]:
                            fb_model = database_manager.get_provider_model(
                                fb["model_id"]
                            )
                            fb["_model"] = fb_model

        return templates.TemplateResponse(
            request,
            "pipeline.html",
            {
                "csrf_token": session.get("csrf_token", generate_csrf_token()),
                "session": session,
                "providers": providers_with_models,
                "profile": profile,
                "profile_id": profile_id,
            },
        )

    @app.post("/admin/pipeline/save")
    async def admin_pipeline_save(request: Request):
        """Save pipeline configuration (form-based)."""
        _login_required(request)
        session = _session(request) or {}
        form_data = await request.form()
        csrf = form_data.get("csrf_token", "")
        if not validate_csrf_token(session, csrf):
            return RedirectResponse(url="/admin/pipeline?error=csrf", status_code=303)

        mode = form_data.get("pipeline_mode", "single")
        provider_id = form_data.get("provider_id", "")

        try:
            if mode == "two_stage":
                tx_model_id_str = form_data.get("tx_model_id", "")
                ref_model_id_str = form_data.get("ref_model_id", "")
                refinement_optional = form_data.get("refinement_optional") == "1"
                tx_model_id = int(tx_model_id_str) if tx_model_id_str else None
                ref_model_id = int(ref_model_id_str) if ref_model_id_str else None

                if tx_model_id is None:
                    return RedirectResponse(
                        url="/admin/pipeline?error=no_tx_model",
                        status_code=303,
                    )

                # Log the optional refinement intent (no DB column needed —
                # absence of a refinement stage is the stored signal).
                if refinement_optional:
                    logger.debug(
                        "Admin pipeline: refinement marked optional for profile"
                    )

                # Find the provider IDs for the models
                tx_model = database_manager.get_provider_model(tx_model_id) if tx_model_id else None
                ref_model = database_manager.get_provider_model(ref_model_id) if ref_model_id else None

                tx_pid = tx_model["provider_id"] if tx_model else None
                ref_pid = ref_model["provider_id"] if ref_model else (tx_pid if tx_pid else None)

                new_id = database_manager.add_pipeline_profile(
                    name="Pipeline due fasi",
                    transcription_provider_id=tx_pid,
                    text_provider_id=ref_pid,
                    mode="two_stage",
                )

                # Create explicit stages
                if tx_model_id:
                    tx_stage_id = database_manager.add_pipeline_stage(
                        new_id, "transcription", tx_model_id,
                    )
                if ref_model_id:
                    ref_stage_id = database_manager.add_pipeline_stage(
                        new_id, "refinement", ref_model_id,
                    )

                set_active_pipeline_profile_id(database_manager, new_id)
                logger.info(
                    "Admin pipeline: saved two_stage profile id=%s "
                    "(tx_model=%s, ref_model=%s)",
                    new_id, tx_model_id, ref_model_id,
                )

            elif mode == "single_pass":
                model_id_str = form_data.get("sp_model_id", "")
                sp_model_id = int(model_id_str) if model_id_str else None

                if sp_model_id is None:
                    return RedirectResponse(
                        url="/admin/pipeline?error=no_sp_model",
                        status_code=303,
                    )

                sp_model = database_manager.get_provider_model(sp_model_id)
                sp_pid = sp_model["provider_id"] if sp_model else None

                new_id = database_manager.add_pipeline_profile(
                    name="Pipeline singolo passaggio",
                    transcription_provider_id=sp_pid,
                    text_provider_id=sp_pid,
                    mode="single_pass",
                )

                # Create stage
                database_manager.add_pipeline_stage(
                    new_id, "single_pass", sp_model_id,
                )

                set_active_pipeline_profile_id(database_manager, new_id)
                logger.info(
                    "Admin pipeline: saved single_pass profile id=%s "
                    "(model=%s)",
                    new_id, sp_model_id,
                )

            elif mode == "single":
                pid = int(provider_id) if provider_id else None
                if pid is None:
                    return RedirectResponse(
                        url="/admin/pipeline?error=no_provider",
                        status_code=303,
                    )

                new_id = database_manager.add_pipeline_profile(
                    name="Pipeline predefinita",
                    transcription_provider_id=pid,
                    text_provider_id=pid,
                    mode="two_stage",
                )

                set_active_pipeline_profile_id(database_manager, new_id)
                logger.info(
                    "Admin pipeline: saved same-provider profile id=%s "
                    "with provider=%s",
                    new_id,
                    pid,
                )

            elif mode == "advanced":
                tx_id = form_data.get("transcription_provider_id", "")
                ref_id = form_data.get("text_provider_id", "")
                tx_pid = int(tx_id) if tx_id else None
                ref_pid = int(ref_id) if ref_id else None

                if tx_pid is None:
                    return RedirectResponse(
                        url="/admin/pipeline?error=no_tx_provider",
                        status_code=303,
                    )

                new_id = database_manager.add_pipeline_profile(
                    name="Pipeline avanzata",
                    transcription_provider_id=tx_pid,
                    text_provider_id=ref_pid,
                    mode="two_stage",
                )
                set_active_pipeline_profile_id(database_manager, new_id)
                logger.info(
                    "Admin pipeline: saved advanced profile id=%s "
                    "(tx=%s, ref=%s)",
                    new_id,
                    tx_pid,
                    ref_pid,
                )

            return RedirectResponse(url="/admin/pipeline?success=saved", status_code=303)

        except Exception as exc:
            logger.exception("Failed to save pipeline configuration")
            return RedirectResponse(
                url="/admin/pipeline?error=save_failed",
                status_code=303,
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

    @app.get("/api/pipeline/info")
    async def api_pipeline_info():
        """Return the current pipeline configuration (for the admin page)."""
        providers = database_manager.list_providers()
        profile_id = get_active_pipeline_profile_id(database_manager)
        profile = (
            database_manager.get_pipeline_profile(profile_id)
            if profile_id is not None
            else None
        )
        # Attach models to each provider
        providers_with_models = []
        for p in providers:
            p_models = database_manager.list_provider_models(p["id"], only_enabled=True)
            providers_with_models.append({
                "id": p["id"],
                "name": p["name"],
                "adapter_type": p["adapter_type"],
                "enabled": bool(p.get("enabled")),
                "capabilities": p.get("capabilities"),
                "models": p_models,
            })
        return {
            "providers": providers_with_models,
            "profile": profile,
            "profile_id": profile_id,
        }

    @app.post("/api/providers/test")
    async def api_providers_test(request: Request):
        """Test a provider connection and classify capabilities.

        Accepts ``provider_type``, ``api_key``, ``endpoint``, and optional
        ``model_name``.  Returns JSON with authentication status, model
        availability, detected capabilities, a pipeline classification, a
        user-facing message, warnings, and a ``models`` list.

        Delegates to :func:`_test_provider_connection` for the core logic.
        """
        session = _session(request)
        if session is None or not session.get("admin"):
            return JSONResponse({"ok": False, "error": "Non autorizzato."},
                                status_code=401)

        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return JSONResponse({"ok": False, "error": "Richiesta JSON non valida."},
                                status_code=400)

        provider_type = (body.get("provider_type") or "").strip()
        api_key = (body.get("api_key") or "").strip()
        endpoint = (body.get("endpoint") or "").strip()
        model_name = (body.get("model_name") or "").strip()

        if not provider_type:
            return JSONResponse({"ok": False, "error": "Seleziona un provider."})
        if not api_key:
            return JSONResponse({"ok": False, "error": "Inserisci una chiave API."})

        import httpx
        try:
            result = await _test_provider_connection(
                provider_type, api_key, endpoint, model_name,
            )
            return JSONResponse(result)
        except httpx.TimeoutException:
            result = _blank_test_result()
            result["user_message"] = "❌ Timeout: server non raggiungibile. Verifica l'endpoint."
            result["warnings"].append("Il server non ha risposto entro 15 secondi.")
            return JSONResponse(result)
        except httpx.RequestError as exc:
            result = _blank_test_result()
            result["user_message"] = f"❌ Errore di connessione: {exc}"
            result["warnings"].append("Verifica l'URL dell'endpoint e la connettività di rete.")
            return JSONResponse(result)

    @app.get("/api/setup/summary")
    async def api_setup_summary():
        """Return the current wizard summary (for the verify step)."""
        return build_summary(database_manager, secret_store)

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
    async def root(request: Request):
        session = _session(request)
        if has_admin(database_manager) and session is not None and session.get("admin"):
            return RedirectResponse(url="/admin/dashboard", status_code=303)
        if has_admin(database_manager):
            return RedirectResponse(url="/login", status_code=303)

        return RedirectResponse(url="/setup", status_code=303)

    return app


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


# ------------------------------------------------------------------
# Shared provider test logic — used by both admin and setup endpoints
# ------------------------------------------------------------------

_BLANK_CAPS: dict[str, bool] = {
    "transcription": False,
    "text_generation": False,
    "refinement": False,
    "streaming_refinement": False,
}


def _blank_test_result() -> dict[str, Any]:
    """Return a blank result template for the provider test schema."""
    return {
        "ok": False,
        "auth_ok": False,
        "models_ok": False,
        "capabilities": dict(_BLANK_CAPS),
        "pipeline_status": "not_compatible",
        "user_message": "",
        "warnings": [],
        "models": [],
    }


_OPENROUTER_DISCOVERY_DEFAULT_LIMIT = 30
_OPENROUTER_DISCOVERY_MAX_LIMIT = 50

# Curated shortlist shown to admins on first setup instead of the full catalog.
# Each entry is an OpenRouter model ID. The actual capabilities are fetched
# from the OpenRouter /models endpoint so they stay accurate over time.
OPENROUTER_CURATED_SHORTLIST = [
    # Transcription
    "openai/whisper-1",
    # Refinement (text processing)
    "openai/gpt-4o-mini",
    "openai/gpt-4o",
    "anthropic/claude-3-5-haiku",
    "google/gemini-flash-1.5-8b",
    "meta-llama/llama-3.3-70b-instruct",
    # Single-pass (audio → final text in one step)
    "google/gemini-2.0-flash-001",
    "openai/gpt-4o-audio-preview",
]

_OPENROUTER_PREFERRED_TEXT = (
    "gpt-4o-mini",
    "gpt-4o",
    "claude",
    "gemini",
    "llama",
    "mistral",
    "qwen",
)


def _parse_discovery_limit(raw: str | None) -> int:
    """Return a bounded discovery limit suitable for provider catalog scans."""
    if raw is None or raw == "":
        return _OPENROUTER_DISCOVERY_DEFAULT_LIMIT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _OPENROUTER_DISCOVERY_DEFAULT_LIMIT
    return max(1, min(value, _OPENROUTER_DISCOVERY_MAX_LIMIT))


def _openrouter_model_category(model: dict[str, Any] | None) -> str:
    """Bucket an OpenRouter model by the pipeline role it can serve."""
    if model is None:
        return "not_recommended"
    meta = _classify_openrouter_metadata(model)
    if meta.get("transcription"):
        return "transcription"
    if meta.get("single_pass_audio_to_text"):
        return "single_pass"
    if meta.get("refinement"):
        return "refinement"
    return "not_recommended"


def _openrouter_matches_purpose(model: dict[str, Any], purpose: str) -> bool:
    category = _openrouter_model_category(model)
    if purpose == "refinement":
        return category == "refinement"
    if purpose == "transcription":
        return category == "transcription"
    if purpose == "single_pass":
        return category == "single_pass"
    if purpose == "all":
        return category != "not_recommended"
    return category in {"refinement", "transcription", "single_pass"}


def _openrouter_model_score(model: dict[str, Any]) -> tuple[int, str]:
    """Sort useful OpenRouter models before catalog long-tail entries."""
    mid = (model.get("id") or "").lower()
    name = (model.get("name") or "").lower()
    category = _openrouter_model_category(model)
    category_score = {
        "refinement": 0,
        "transcription": 1,
        "single_pass": 2,
        "not_recommended": 3,
    }.get(category, 3)
    preferred = 0 if any(token in mid or token in name for token in _OPENROUTER_PREFERRED_TEXT) else 1
    return (category_score * 10 + preferred, mid)


def _openrouter_catalog_item(model: dict[str, Any]) -> dict[str, Any]:
    """Return the compact model shape used by the OpenRouter catalog UI."""
    arch = model.get("architecture") or {}
    pricing = model.get("pricing") or {}
    top_provider = model.get("top_provider") or {}
    meta = _classify_openrouter_metadata(model)
    caps = _classify_openrouter_model(model).to_dict()
    return {
        "model_id": model.get("id") or "",
        "name": model.get("name") or model.get("id") or "",
        "description": model.get("description") or "",
        "category": _openrouter_model_category(model),
        "capabilities": caps,
        "metadata": meta,
        "context_length": model.get("context_length"),
        "max_completion_tokens": top_provider.get("max_completion_tokens"),
        "pricing": {
            "prompt": pricing.get("prompt"),
            "completion": pricing.get("completion"),
            "request": pricing.get("request"),
            "image": pricing.get("image"),
        },
        "input_modalities": arch.get("input_modalities") or [],
        "output_modalities": arch.get("output_modalities") or [],
        "supported_parameters": model.get("supported_parameters") or [],
    }


def _select_openrouter_models(
    models: list[dict[str, Any]],
    *,
    purpose: str,
    query: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Return a small, guided OpenRouter model shortlist."""
    allowed = {"refinement", "transcription", "single_pass", "all", "all_recommended"}
    if purpose not in allowed:
        purpose = "all_recommended"

    selected: list[dict[str, Any]] = []
    for model in models:
        mid = (model.get("id") or "").lower()
        name = (model.get("name") or "").lower()
        if query and query not in mid and query not in name:
            continue
        if not _openrouter_matches_purpose(model, purpose):
            continue
        selected.append(model)

    selected.sort(key=_openrouter_model_score)
    return selected[:limit]


async def _test_provider_connection(
    provider_type: str,
    api_key: str,
    endpoint: str,
    model_name: str = "",
) -> dict[str, Any]:
    """Test a provider connection and return full result dict.

    The returned dict matches the schema shared by
    ``/api/providers/test`` and ``/api/setup/test-provider``:

    ``ok``, ``auth_ok``, ``models_ok``, ``capabilities``,
    ``pipeline_status``, ``user_message``, ``warnings``, ``models``

    *models* is the list of discovered model IDs (first 20).
    """
    import httpx

    result = _blank_test_result()

    # ---- OpenAI-compatible endpoints ----
    if provider_type in ("openai", "openrouter", "custom", "ollama", "vllm"):
        url = f"{endpoint.rstrip('/')}/models" if endpoint else "https://api.openai.com/v1/models"
        headers = {"Authorization": f"Bearer {api_key}"}

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers)

            if resp.status_code == 200:
                result["auth_ok"] = True
                models_data = resp.json().get("data", [])
                model_ids = [m["id"] for m in models_data
                             if isinstance(m, dict) and m.get("id")]

                result["models"] = model_ids[:20]

                if model_ids:
                    result["models_ok"] = True

                # Capability detection
                if provider_type == "openrouter":
                    if model_name:
                        caps, meta = await probe_openrouter_capabilities(
                            api_key,
                            endpoint or "",
                            model_name,
                            session=client,
                        )
                        # If probe returned all-False we don't know
                        if caps == CapabilityModel() and model_ids:
                            result["models_ok"] = False
                            result["warnings"].append(
                                "Impossibile determinare le capacità "
                                "del modello. Verifica manualmente."
                            )
                        else:
                            # audio_input without transcription → warning
                            if meta.get("audio_input") and not caps.transcription:
                                result["warnings"].append(
                                    "Modello con capacità audio, ma la "
                                    "trascrizione (STT) deve essere "
                                    "verificata. I modelli che accettano "
                                    "audio in input non sempre eseguono "
                                    "speech-to-text."
                                )
                    else:
                        # No model specified — assume text-only
                        caps = CapabilityModel(
                            text_generation=True,
                            refinement=True,
                            streaming_refinement=True,
                        )
                        result["warnings"].append(
                            "Nessun modello specificato. Le capacità "
                            "rilevate sono stimate."
                        )

                    # Extra warnings for OpenRouter
                    if caps.transcription is False and caps.text_generation:
                        result["warnings"].append(
                            "I modelli chat/testo non trascrivono "
                            "automaticamente audio. Con OpenRouter "
                            "potresti aver bisogno di un modello "
                            "separato per la trascrizione (es. whisper-1) "
                            "e di un modello chat per il refinement."
                        )
                    elif caps.transcription is False:
                        result["warnings"].append(
                            "Le capacità di trascrizione non sono "
                            "state rilevate. Verifica che il modello "
                            "supporti input audio."
                        )
                    elif caps.transcription and caps.text_generation is False:
                        result["warnings"].append(
                            "Modello solo trascrizione: non può "
                            "eseguire refinement testuale."
                        )
                else:
                    # OpenAI / custom / ollama / vLLM — static detection
                    adapter_type = _adapter_type_for_provider(provider_type)
                    caps = detect_capabilities(adapter_type, model_name)

                result["capabilities"] = caps.to_dict()

            else:
                # Auth failed
                err_detail = ""
                try:
                    err_detail = resp.json().get("error", {}).get(
                        "message", "Chiave API non valida"
                    )
                except Exception:
                    err_detail = "Chiave API non valida"
                result["user_message"] = f"❌ Chiave API non valida: {err_detail}"
                result["warnings"].append(
                    "Verifica che la chiave API sia corretta e abbia "
                    "i permessi necessari."
                )

    # ---- Gemini ----
    elif provider_type == "gemini":
        url = (
            f"https://generativelanguage.googleapis.com/v1/"
            f"models?key={api_key}"
        )
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)

            if resp.status_code == 200:
                result["auth_ok"] = True
                models_data = resp.json().get("models", [])
                model_ids = [m["name"].replace("models/", "") for m in models_data
                             if isinstance(m, dict) and m.get("name")]
                result["models"] = model_ids[:20]

                if model_ids:
                    result["models_ok"] = True

                adapter_type = _adapter_type_for_provider(provider_type)
                caps = detect_capabilities(adapter_type, model_name)
                result["capabilities"] = caps.to_dict()
            else:
                err_detail = ""
                try:
                    err_detail = resp.json().get("error", {}).get(
                        "message", "Chiave API non valida"
                    )
                except Exception:
                    err_detail = "Chiave API non valida"
                result["user_message"] = f"❌ Chiave API non valida: {err_detail}"
                result["warnings"].append(
                    "Verifica la chiave API di Google Generative AI."
                )

    else:
        result["user_message"] = f"❌ Provider sconosciuto: {provider_type}"
        return result

    # ---- Classify pipeline status ----
    caps = CapabilityModel.from_dict(result["capabilities"])
    if caps.transcription and (caps.text_generation or caps.refinement):
        result["pipeline_status"] = "complete_same_provider"
    elif caps.transcription:
        result["pipeline_status"] = "transcription_only"
    elif caps.text_generation or caps.refinement:
        result["pipeline_status"] = "refinement_only"
    # else: stays "not_compatible"

    # ---- Build user_message when auth succeeded ----
    if result["auth_ok"]:
        if caps.transcription and caps.refinement:
            result["user_message"] = (
                "✅ Connessione riuscita! Il provider supporta sia la "
                "trascrizione audio che il refinement testuale."
            )
        elif caps.transcription and not caps.refinement:
            result["user_message"] = (
                "✅ Connessione riuscita. Il provider supporta la "
                "trascrizione audio ma non il refinement testuale."
            )
        elif caps.refinement and not caps.transcription:
            result["user_message"] = (
                "⚠️ Connessione riuscita. Il modello selezionato è "
                "solo testo: può raffinare trascrizioni ma non "
                "trascrivere audio. Per la trascrizione scegli un "
                "modello speech-to-text. Con OpenRouter potresti "
                "aver bisogno di un modello diverso dallo stesso provider."
            )
        elif caps.text_generation and not caps.transcription:
            result["user_message"] = (
                "⚠️ Connessione riuscita. Questo provider supporta "
                "solo generazione testo (refinement), non trascrizione "
                "audio. Aggiungi un modello speech-to-text separato."
            )
        else:
            result["user_message"] = (
                "⚠️ Connessione riuscita, ma non è stato possibile "
                "determinare le capacità del provider."
            )
        result["ok"] = True

    return result


def _build_wizard_context(
    database_manager: DatabaseManager,
    secret_store: SecretStore | None = None,
) -> Dict[str, Any]:
    """Build a context dict of saved wizard data for template rendering."""
    return {
        "telegram_token": get_telegram_token(database_manager, secret_store),
        "provider": get_provider_config(database_manager, secret_store),
        "pipeline_mode": get_pipeline_mode(database_manager),
        "capabilities": get_capabilities(database_manager),
        "summary": build_summary(database_manager, secret_store),
    }


def _adapter_type_for_provider(provider_type: str) -> str:
    """Map UI provider presets to adapter registry identifiers."""
    return {
        "openai": "openai-native",
        "gemini": "gemini-native",
        "openrouter": "openai-compat",
        "ollama": "openai-compat",
        "vllm": "openai-compat",
        "custom": "openai-compat",
    }.get(provider_type, provider_type)


def _process_step(
    db: DatabaseManager,
    secret_store: SecretStore | None,
    config_service: ConfigService,
    runtime_manager: RuntimeManager,
    step: str,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    """Process a single wizard step and return result dict.

    Returns ``{"ok": True}`` or ``{"ok": False, "errors": [...]}``.
    """
    try:
        if step == "step_code":
            code = (data.get("setup_code") or "").strip()
            if not code:
                return {"ok": False, "errors": ["Inserisci il codice di configurazione."]}
            if not validate_setup_code(db, code):
                return {"ok": False, "errors": ["Codice non valido o scaduto."]}
            # Step validated; actual admin creation happens in step_admin
            return {"ok": True}

        elif step == "step_admin":
            # Accept both form field names (JS sends admin_password,
            # non-JS fallback sends password via remapping)
            password = data.get("password") or data.get("admin_password") or ""
            confirm = data.get("password_confirm") or data.get("admin_password_confirm") or ""
            if password != confirm:
                return {"ok": False, "errors": ["Le password non coincidono."]}
            set_admin_password(db, password)
            db.set_setup_state("admin_created", "true")
            invalidate_setup_code(db)
            logger.info("Wizard step_admin: admin created")
            return {"ok": True}

        elif step == "step_telegram":
            token = (data.get("token") or "").strip()
            if not token:
                return {"ok": False, "errors": ["Inserisci il token del bot Telegram."]}
            save_telegram_token(db, token, secret_store)

            # Also save to ConfigService so state checker picks it up
            try:
                config_service.update_setting("telegram_token", token)
            except Exception:
                logger.warning("Could not save telegram_token to ConfigService; will retry later")

            logger.info("Wizard step_telegram: token saved")
            return {"ok": True, "token_saved": True}

        elif step == "step_provider":
            ptype = (data.get("type") or "").strip()
            api_key = (data.get("api_key") or "").strip()
            endpoint = (data.get("endpoint") or "").strip()

            if not ptype:
                return {"ok": False, "errors": ["Seleziona un provider."]}
            if not api_key:
                return {"ok": False, "errors": ["Inserisci la chiave API."]}

            save_provider_config(db, ptype, api_key, endpoint, secret_store)

            # Save to ConfigService as well
            try:
                config_service.update_setting("llm_provider", ptype)
            except Exception:
                logger.warning("Could not save llm_provider to ConfigService")

            logger.info("Wizard step_provider: %s configured", ptype)
            return {"ok": True}

        elif step == "step_capabilities":
            capabilities = data.get("capabilities", {})
            save_capabilities(db, capabilities)
            logger.info("Wizard step_capabilities: %d capabilities saved", len(capabilities))
            return {"ok": True}

        elif step == "step_pipeline":
            mode = data.get("mode", "single")
            model = data.get("model", "")
            save_pipeline_mode(db, mode)
            if model:
                save_provider_model(db, model)
            logger.info("Wizard step_pipeline: mode=%s, model=%s", mode, model or "default")
            return {"ok": True}

        elif step == "step_verify":
            # Create the provider connection and pipeline profile (P5).
            try:
                profile_id = create_pipeline_from_wizard(db, secret_store)
                logger.info(
                    "Wizard step_verify: created pipeline profile id=%s",
                    profile_id,
                )
            except ValueError as exc:
                logger.warning(
                    "Wizard step_verify: profile creation skipped (%s)",
                    exc,
                )
            except Exception as exc:
                logger.exception(
                    "Wizard step_verify: profile creation failed: %s",
                    exc,
                )

            summary = build_summary(db, secret_store)
            logger.info(
                "Wizard step_verify: pipeline verified, bot_ready=%s",
                summary.get("bot_ready"),
            )
            return {"ok": True, "summary": summary}

        else:
            return {"ok": False, "errors": [f"Step sconosciuto: {step}"]}

    except Exception as exc:
        logger.exception("Error processing wizard step %s", step)
        return {"ok": False, "errors": [f"Errore interno: {exc}"]}


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


# ------------------------------------------------------------------
# Model classification helpers (used by discovery API)
# ------------------------------------------------------------------


def _classify_openai_model(model_id: str) -> Dict[str, bool]:
    """Classify capabilities for an OpenAI model based on its ID."""
    mid = model_id.lower()
    is_whisper = "whisper" in mid
    is_gpt = mid.startswith("gpt-") or mid.startswith("o1") or mid.startswith("o3")
    return {
        "transcription": is_whisper,
        "text_generation": is_gpt,
        "refinement": is_gpt,
        "streaming_refinement": is_gpt,
        "single_pass_audio_to_text": False,
    }


def _classify_gemini_model(model_id: str) -> Dict[str, bool]:
    """Classify capabilities for a Gemini model based on its ID."""
    mid = model_id.lower()
    # Gemini models can handle audio input for transcription
    is_gemini = mid.startswith("gemini-")
    return {
        "transcription": is_gemini,
        "text_generation": is_gemini,
        "refinement": is_gemini,
        "streaming_refinement": is_gemini,
        "single_pass_audio_to_text": is_gemini,
    }


def _classify_openai_compat_model(model_id: str) -> Dict[str, bool]:
    """Classify an OpenAI-compatible model (including OpenRouter namespaced IDs).

    OpenRouter model IDs use ``provider/model-name`` notation.  We use the
    provider prefix and known model families to assign capabilities without an
    API call, falling back to conservative text-only defaults.
    """
    mid = model_id.lower()
    is_whisper = "whisper" in mid
    has_audio_kw = any(kw in mid for kw in ("audio", "stt", "speech"))

    # --- Known multimodal families that accept audio input via OpenRouter ----
    # These models can act as a single-pass transcriber + refiner.
    _MULTIMODAL_PREFIXES = (
        "google/gemini-",
        "google/gemma-4",       # Gemma 4 is natively multimodal
        "openai/gpt-4o-audio",
        "openai/gpt-4o-mini-audio",
    )
    is_multimodal = any(mid.startswith(p) for p in _MULTIMODAL_PREFIXES) or has_audio_kw

    # --- Known text-only families (no audio input) ---------------------------
    _TEXT_ONLY_PREFIXES = (
        "anthropic/",
        "meta-llama/",
        "mistralai/",
        "cohere/",
        "google/gemma-2",
        "google/gemma-3",
    )
    is_text_only = any(mid.startswith(p) for p in _TEXT_ONLY_PREFIXES)

    if is_whisper:
        return {
            "transcription": True,
            "text_generation": False,
            "refinement": False,
            "streaming_refinement": False,
            "single_pass_audio_to_text": False,
        }

    if is_multimodal and not is_text_only:
        return {
            "transcription": False,
            "text_generation": True,
            "refinement": True,
            "streaming_refinement": True,
            "single_pass_audio_to_text": True,
        }

    # Default: chat/refinement capable, no audio input
    is_chat = not is_whisper and not has_audio_kw
    return {
        "transcription": False,
        "text_generation": is_chat,
        "refinement": is_chat,
        "streaming_refinement": is_chat,
        "single_pass_audio_to_text": False,
    }

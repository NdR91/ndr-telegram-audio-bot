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
from bot.web.setup_wizard import (
    PROVIDER_PRESETS,
    build_summary,
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
        """Test an AI provider connection."""
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return JSONResponse({"ok": False, "error": "Richiesta JSON non valida."},
                                status_code=400)

        provider_type = (body.get("type") or "").strip()
        api_key = (body.get("api_key") or "").strip()
        endpoint = (body.get("endpoint") or "").strip()

        if not provider_type:
            return JSONResponse({"ok": False, "error": "Seleziona un provider."})
        if not api_key:
            return JSONResponse({"ok": False, "error": "Inserisci una chiave API."})

        import httpx
        try:
            if provider_type == "openai" or provider_type in ("openrouter", "custom", "ollama", "vllm"):
                url = f"{endpoint.rstrip('/')}/models" if endpoint else "https://api.openai.com/v1/models"
                headers = {"Authorization": f"Bearer {api_key}"}
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 200:
                        models_data = resp.json().get("data", [])
                        model_ids = [m["id"] for m in models_data if isinstance(m, dict) and m.get("id")]
                        return JSONResponse({
                            "ok": True,
                            "models": model_ids[:20],
                            "provider": provider_type,
                        })
                    else:
                        err = resp.json().get("error", {}).get("message", "Chiave API non valida")
                        return JSONResponse({"ok": False, "error": err})

            elif provider_type == "gemini":
                url = f"https://generativelanguage.googleapis.com/v1/models?key={api_key}"
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        models_data = resp.json().get("models", [])
                        model_ids = [m["name"].replace("models/", "") for m in models_data
                                     if isinstance(m, dict) and m.get("name")]
                        return JSONResponse({
                            "ok": True,
                            "models": model_ids[:20],
                            "provider": "gemini",
                        })
                    else:
                        err = resp.json().get("error", {}).get("message", "Chiave API non valida")
                        return JSONResponse({"ok": False, "error": err})

            return JSONResponse({"ok": False, "error": f"Provider sconosciuto: {provider_type}"})

        except httpx.TimeoutException:
            return JSONResponse({"ok": False, "error": "Timeout: server non raggiungibile."})
        except httpx.RequestError as exc:
            return JSONResponse({"ok": False, "error": f"Errore di connessione: {exc}"})

    @app.post("/api/setup/detect-capabilities")
    async def api_detect_capabilities(request: Request):
        """Detect capabilities from a provider's model list."""
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
                                 "models": [], "note": "Nessun modello rilevato."},
            })

        # Heuristic capability detection based on model names
        transcription_keywords = ("whisper", "audio", "voice", "speech")
        text_keywords = ("gpt", "gemini", "claude", "llama", "mistral", "command",
                         "text", "chat", "instruct", "turbo")

        can_transcribe = False
        can_generate = False
        relevant_models = []

        for model_id in models:
            mid = model_id.lower()
            if any(kw in mid for kw in transcription_keywords):
                can_transcribe = True
                relevant_models.append(model_id)
            if any(kw in mid for kw in text_keywords):
                can_generate = True
                if model_id not in relevant_models:
                    relevant_models.append(model_id)

        # Provider-specific defaults
        if provider_type == "openai" and not can_transcribe:
            # OpenAI always has Whisper available
            can_transcribe = True
        if provider_type == "gemini":
            can_transcribe = True
            can_generate = True
            if not relevant_models:
                relevant_models = ["gemini-2.0-flash", "gemini-2.5-pro"]

        return JSONResponse({
            "ok": True,
            "capabilities": {
                "transcription": can_transcribe,
                "text_generation": can_generate,
                "refinement": can_generate,
                "models": relevant_models[:10],
            },
        })

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
    async def root():
        if has_admin(database_manager):
            return RedirectResponse(url="/admin/dashboard", status_code=303)

        return RedirectResponse(url="/setup", status_code=303)

    return app


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


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
            summary = build_summary(db, secret_store)
            logger.info("Wizard step_verify: pipeline verified, bot_ready=%s", summary.get("bot_ready"))
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

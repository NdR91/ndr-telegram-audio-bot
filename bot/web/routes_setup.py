"""
Guided onboarding wizard routes extracted from ``app.py`` as a FastAPI APIRouter.

Routes
------
- ``GET /setup`` — wizard landing page (redirects to express when past step_provider)
- ``GET /setup/express`` — W9 single-screen provider/model setup flow
- ``POST /api/setup/step`` — process a wizard step (JSON API)
- ``POST /api/setup/test-telegram`` — test a Telegram bot token
- ``POST /api/setup/test-provider`` — test an AI provider connection
- ``POST /api/setup/detect-capabilities`` — detect provider capabilities
- ``POST /api/setup/model-picker`` — return smart model-picker cards
- ``GET /api/setup/manual-cards`` — return persisted manual model cards
- ``POST /api/setup/manual-cards`` — persist manual model cards
- ``POST /api/setup/express`` — complete provider/model/pipeline setup
- ``POST /api/setup/save-provider`` — save provider connection only
- ``POST /setup`` — form-based step processing fallback
- ``GET /api/setup/summary`` — return current wizard summary
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from bot.capabilities import (
    _classify_openrouter_metadata,
    detect_capabilities,
    probe_openrouter_capabilities,
)
from bot.setup import (
    invalidate_setup_code,
    is_code_generated,
    validate_setup_code,
)
from bot.web.auth import generate_csrf_token, has_admin, validate_csrf_token
from bot.web.dependencies import get_session, set_session_cookie
from bot.web.helpers import blank_test_result, test_provider_connection
from bot.web.pipeline_builder import (
    create_single_pass_profile,
    create_two_stage_profile,
)
from bot.web.setup_wizard import (
    PROVIDER_PRESETS,
    build_summary,
    create_express_pipeline_from_wizard,
    create_pipeline_from_wizard,
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
    save_capabilities,
    save_pipeline_mode,
    save_provider_config,
    save_provider_model,
    save_telegram_token,
    set_current_step,
    STEP_PROVIDER,
    STEP_CAPABILITIES,
    STEP_PIPELINE,
    STEP_VERIFY,
    STEP_DONE,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /setup  —  wizard landing page
# ---------------------------------------------------------------------------


@router.get("/setup", response_class=HTMLResponse)
async def setup_wizard(request: Request):
    """Render the guided onboarding wizard at the current step."""
    session = get_session(request)
    db = request.app.state.db
    secret_store = request.app.state.secret_store
    templates = request.app.state.templates

    admin_exists = has_admin(db)
    current_step = get_current_step(db)
    wizard_done = is_wizard_complete(db)

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

    if current_step in {STEP_PROVIDER, STEP_CAPABILITIES, STEP_PIPELINE, STEP_VERIFY}:
        return RedirectResponse(url="/setup/express", status_code=303)

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
                "code_generated": is_code_generated(db),
                "wizard_data": _build_wizard_context(db, secret_store),
                "provider_presets": PROVIDER_PRESETS,
                "error": request.query_params.get("error", ""),
                "success": "",
            },
        )
        set_session_cookie(response, session, request)
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
            "code_generated": is_code_generated(db),
            "wizard_data": _build_wizard_context(db, secret_store),
            "provider_presets": PROVIDER_PRESETS,
            "error": request.query_params.get("error", ""),
            "success": request.query_params.get("success", ""),
        },
    )


# ---------------------------------------------------------------------------
# GET /setup/express  —  W9 single-screen provider/model setup
# ---------------------------------------------------------------------------


@router.get("/setup/express", response_class=HTMLResponse)
async def setup_express(request: Request):
    """Render the W9 single-screen provider/model setup flow."""
    session = get_session(request)
    db = request.app.state.db
    secret_store = request.app.state.secret_store
    templates = request.app.state.templates

    admin_exists = has_admin(db)
    wizard_done = is_wizard_complete(db)

    if session is not None and session.get("admin") and admin_exists and wizard_done:
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    if admin_exists and wizard_done:
        return RedirectResponse(url="/login", status_code=303)

    if session is None:
        session = {"csrf_token": generate_csrf_token()}
        response = templates.TemplateResponse(
            request,
            "setup_express.html",
            {
                "csrf_token": session["csrf_token"],
                "provider_presets": PROVIDER_PRESETS,
                "wizard_data": _build_wizard_context(db, secret_store),
                "error": request.query_params.get("error", ""),
            },
        )
        set_session_cookie(response, session, request)
        return response

    return templates.TemplateResponse(
        request,
        "setup_express.html",
        {
            "csrf_token": session.get("csrf_token", generate_csrf_token()),
            "provider_presets": PROVIDER_PRESETS,
            "wizard_data": _build_wizard_context(db, secret_store),
            "error": request.query_params.get("error", ""),
        },
    )


# ---------------------------------------------------------------------------
# POST /api/setup/step  —  process wizard step (JSON API)
# ---------------------------------------------------------------------------


@router.post("/api/setup/step")
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
    session = get_session(request)
    if session is None:
        return JSONResponse(
            {"ok": False, "errors": ["Sessione non valida. Ricarica la pagina."]},
            status_code=400,
        )

    db = request.app.state.db
    secret_store = request.app.state.secret_store
    config_service = request.app.state.config_service
    runtime_manager = request.app.state.runtime_manager

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse(
            {"ok": False, "errors": ["Richiesta JSON non valida."]},
            status_code=400,
        )

    step = body.get("step", "")
    data = body.get("data", {})

    result = _process_step(db, secret_store, config_service,
                           runtime_manager, step, data)
    if result.get("ok"):
        # Advance to next step
        next_step = get_next_step(step)
        if next_step:
            set_current_step(db, next_step)
            if next_step == STEP_PROVIDER:
                result["redirect"] = "/setup/express"
            result["next_step"] = next_step
            result["next_step_meta"] = get_step_meta(next_step)
        else:
            result["next_step"] = None
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# POST /api/setup/test-telegram
# ---------------------------------------------------------------------------


@router.post("/api/setup/test-telegram")
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


# ---------------------------------------------------------------------------
# POST /api/setup/test-provider
# ---------------------------------------------------------------------------


@router.post("/api/setup/test-provider")
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
        result = await test_provider_connection(
            provider_type, api_key, endpoint, model_name,
        )
        return JSONResponse(result)
    except httpx.TimeoutException:
        result = blank_test_result()
        result["user_message"] = "❌ Timeout: server non raggiungibile."
        result["warnings"] = ["Il server non ha risposto entro 15 secondi."]
        return JSONResponse(result)
    except httpx.RequestError as exc:
        result = blank_test_result()
        result["user_message"] = f"❌ Errore di connessione: {exc}"
        result["warnings"] = ["Verifica l'URL dell'endpoint e la connettività di rete."]
        return JSONResponse(result)


# ---------------------------------------------------------------------------
# POST /api/setup/detect-capabilities
# ---------------------------------------------------------------------------


@router.post("/api/setup/detect-capabilities")
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

    db = request.app.state.db
    secret_store = request.app.state.secret_store

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

    from bot.capabilities import CapabilityModel

    # --- OpenRouter: probe model metadata ---
    if provider_type == "openrouter":
        saved = get_provider_config(db, secret_store)
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


# ---------------------------------------------------------------------------
# POST /api/setup/model-picker
# ---------------------------------------------------------------------------


@router.post("/api/setup/model-picker")
async def api_setup_model_picker(request: Request):
    """Return smart model-picker cards for the express setup flow.

    The endpoint does not persist credentials or selected models.  It only
    shapes provider catalog data into the W10 card schema.
    """
    session = get_session(request)
    if session is None:
        return JSONResponse(
            {"ok": False, "error": "Sessione non valida. Ricarica la pagina."},
            status_code=400,
        )

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse({"ok": False, "error": "Richiesta JSON non valida."},
                            status_code=400)

    from bot.model_picker import (
        build_openrouter_picker_cards,
        manual_model_card,
        openrouter_card_from_catalog_model,
        openrouter_counts,
        transcription_locked_card,
    )

    provider_type = (body.get("provider_type") or body.get("type") or "").strip()
    api_key = (body.get("api_key") or "").strip()
    endpoint = (body.get("endpoint") or "").strip()
    process_mode = (body.get("process_mode") or "two_stage").strip()
    query = (body.get("query") or "").strip()
    manual_model_id = (body.get("manual_model_id") or "").strip()
    limit = _parse_discovery_limit(str(body.get("limit") or "5"))
    purpose = "single_pass" if process_mode == "single_pass" else "refinement"

    if manual_model_id:
        if provider_type == "openrouter":
            if not api_key:
                return JSONResponse(
                    {"ok": False, "error": "Inserisci una chiave API OpenRouter per cercare il modello nel catalogo."},
                )
            import httpx
            catalog_endpoint = endpoint or PROVIDER_PRESETS["openrouter"]["default_endpoint"]
            url = f"{catalog_endpoint.rstrip('/')}/models"
            headers = {"Authorization": f"Bearer {api_key}"}
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code != 200:
                        return JSONResponse({
                            "ok": False,
                            "error": f"OpenRouter ha risposto con HTTP {resp.status_code} durante la ricerca del modello.",
                        })
                    data = resp.json().get("data", [])
                raw_models = [m for m in data if isinstance(m, dict) and m.get("id")]
                model_by_id = {m.get("id", "").lower(): m for m in raw_models}
                matched = model_by_id.get(manual_model_id.lower().strip())
                if matched is None:
                    if process_mode == "single_pass":
                        return JSONResponse({
                            "ok": False,
                            "error": f"Modello \"{manual_model_id}\" non trovato nel catalogo OpenRouter. "
                                     "In modalità single-pass è richiesto un modello con supporto audio. "
                                     "Verifica l'ID o passa alla modalità due fasi.",
                        })
                    card = manual_model_card(manual_model_id, _provider_label(provider_type), purpose)
                else:
                    if process_mode == "single_pass":
                        meta = _classify_openrouter_metadata(matched)
                        if not meta.get("single_pass_audio_to_text"):
                            return JSONResponse({
                                "ok": False,
                                "error": f"Modello \"{manual_model_id}\" non supporta l'audio in single-pass. "
                                         "Scegli un modello con input audio o passa alla modalità due fasi.",
                            })
                    card = openrouter_card_from_catalog_model(matched)
                return JSONResponse({
                    "ok": True,
                    "transcription": transcription_locked_card() if process_mode != "single_pass" else None,
                    "cards": [card],
                    "counts": {},
                    "source": "openrouter" if matched else "manual",
                })
            except httpx.TimeoutException:
                return JSONResponse({"ok": False, "error": "Timeout durante la connessione a OpenRouter."})
            except httpx.RequestError as exc:
                return JSONResponse({"ok": False, "error": f"Errore di connessione: {exc}"})
        return JSONResponse({
            "ok": True,
            "transcription": transcription_locked_card() if process_mode != "single_pass" else None,
            "cards": [manual_model_card(manual_model_id, _provider_label(provider_type), purpose)],
            "counts": {},
            "source": "manual",
        })

    if not provider_type:
        return JSONResponse({"ok": False, "error": "Seleziona un provider."})

    if provider_type != "openrouter":
        cards = _static_picker_cards(provider_type, purpose)
        return JSONResponse({
            "ok": True,
            "transcription": transcription_locked_card() if process_mode != "single_pass" else None,
            "cards": cards[:limit],
            "counts": {},
            "source": "static",
        })

    if not api_key:
        return JSONResponse({"ok": False, "error": "Inserisci una chiave API."})

    import httpx

    try:
        catalog_endpoint = endpoint or PROVIDER_PRESETS["openrouter"]["default_endpoint"]
        url = f"{catalog_endpoint.rstrip('/')}/models"
        headers = {"Authorization": f"Bearer {api_key}"}
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return JSONResponse({
                    "ok": False,
                    "error": f"OpenRouter ha risposto con HTTP {resp.status_code}.",
                })
            data = resp.json().get("data", [])

        raw_models = [m for m in data if isinstance(m, dict) and m.get("id")]
        cards = build_openrouter_picker_cards(
            raw_models,
            purpose=purpose,
            query=query,
            limit=limit,
        )
        return JSONResponse({
            "ok": True,
            "transcription": transcription_locked_card() if process_mode != "single_pass" else None,
            "cards": cards,
            "counts": openrouter_counts(raw_models),
            "source": "openrouter",
        })
    except httpx.TimeoutException:
        return JSONResponse({"ok": False, "error": "Timeout durante la connessione a OpenRouter."})
    except httpx.RequestError as exc:
        return JSONResponse({"ok": False, "error": f"Errore di connessione: {exc}"})


# ---------------------------------------------------------------------------
# GET /api/setup/manual-cards
# ---------------------------------------------------------------------------


@router.get("/api/setup/manual-cards")
async def api_get_manual_cards(request: Request):
    """Return persisted manual model cards for the express setup."""
    session = get_session(request)
    if session is None:
        return JSONResponse({"ok": False, "error": "Sessione non valida."}, status_code=400)
    db = request.app.state.db
    raw = db.get_setup_state("express_manual_cards")
    if raw is None:
        return JSONResponse({"ok": True, "cards": []})
    try:
        cards = json.loads(raw)
        if not isinstance(cards, list):
            cards = []
    except (json.JSONDecodeError, ValueError):
        cards = []
    return JSONResponse({"ok": True, "cards": cards})


# ---------------------------------------------------------------------------
# POST /api/setup/manual-cards
# ---------------------------------------------------------------------------


@router.post("/api/setup/manual-cards")
async def api_save_manual_cards(request: Request):
    """Persist manual model cards for the express setup across sessions."""
    session = get_session(request)
    if session is None:
        return JSONResponse({"ok": False, "error": "Sessione non valida."}, status_code=400)
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse({"ok": False, "error": "Richiesta JSON non valida."}, status_code=400)

    csrf = body.get("csrf_token", "")
    if not validate_csrf_token(session, csrf):
        return JSONResponse({"ok": False, "error": "CSRF token non valido."}, status_code=403)

    cards = body.get("cards", [])
    if not isinstance(cards, list):
        return JSONResponse({"ok": False, "error": "Il campo cards deve essere una lista."}, status_code=400)

    _SAFE_KEYS = {
        "kind", "model_id", "name", "provider", "description",
        "category", "process_mode", "speed", "quality", "recommended",
    }
    _SECRET_PATTERNS = ("key", "token", "secret", "password", "credential", "api_")

    safe_cards = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        safe_card = {k: card[k] for k in _SAFE_KEYS if k in card}
        safe_card.setdefault("kind", "manual")
        safe_card.setdefault("model_id", "")
        safe_card.setdefault("provider", "")
        safe_card.setdefault("category", "refinement")
        safe_card.setdefault("process_mode", "two_stage")

        source = card.get("source", "manual")
        safe_card["source"] = source if source in ("manual", "openrouter") else "manual"

        _CAPABILITY_KEYS = {
            "transcription", "text_generation", "refinement",
            "streaming_refinement", "single_pass_audio_to_text",
        }
        _PRICING_KEYS = {"input_per_million", "output_per_million", "currency"}

        raw_caps = card.get("capabilities")
        safe_card["capabilities"] = (
            {k: raw_caps[k] for k in _CAPABILITY_KEYS if k in raw_caps}
            if isinstance(raw_caps, dict)
            else {}
        )

        raw_pricing = card.get("pricing")
        safe_card["pricing"] = (
            {k: raw_pricing[k] for k in _PRICING_KEYS if k in raw_pricing}
            if isinstance(raw_pricing, dict)
            else {
                "input_per_million": None,
                "output_per_million": None,
                "currency": "USD",
            }
        )

        for key in list(safe_card.keys()):
            key_lower = key.lower()
            if any(pat in key_lower for pat in _SECRET_PATTERNS):
                del safe_card[key]

        safe_cards.append(safe_card)

    request.app.state.db.set_setup_state("express_manual_cards", json.dumps(safe_cards))
    return JSONResponse({"ok": True, "count": len(safe_cards)})


# ---------------------------------------------------------------------------
# POST /api/setup/express  —  complete setup from express screen
# ---------------------------------------------------------------------------


@router.post("/api/setup/express")
async def api_setup_express(request: Request):
    """Complete provider/model/pipeline setup from the W9 express screen."""
    session = get_session(request)
    if session is None:
        return JSONResponse(
            {"ok": False, "error": "Sessione non valida. Ricarica la pagina."},
            status_code=400,
        )

    db = request.app.state.db
    secret_store = request.app.state.secret_store
    config_service = request.app.state.config_service
    runtime_manager = request.app.state.runtime_manager

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse({"ok": False, "error": "Richiesta JSON non valida."},
                            status_code=400)

    csrf = body.get("csrf_token", "")
    if not validate_csrf_token(session, csrf):
        return JSONResponse({"ok": False, "error": "CSRF token non valido."},
                            status_code=403)

    provider_type = (body.get("provider_type") or "").strip()
    api_key = (body.get("api_key") or "").strip()
    endpoint = (body.get("endpoint") or "").strip()
    process_mode = (body.get("process_mode") or "two_stage").strip()
    selected_model = (body.get("selected_model") or "").strip()

    if process_mode not in {"two_stage", "single_pass"}:
        return JSONResponse({"ok": False, "error": "Modalità audio non valida."})
    if not provider_type:
        return JSONResponse({"ok": False, "error": "Seleziona un servizio AI."})
    if not api_key:
        return JSONResponse({"ok": False, "error": "Inserisci una chiave API."})
    if not selected_model:
        return JSONResponse({"ok": False, "error": "Seleziona un modello."})

    import httpx
    try:
        test_result = await test_provider_connection(
            provider_type,
            api_key,
            endpoint,
            selected_model,
        )
    except Exception as exc:
        logger.exception("Express setup provider validation failed")
        return JSONResponse({
            "ok": False,
            "error": f"Verifica provider fallita: {exc}",
        })

    if not test_result.get("ok") or not test_result.get("auth_ok"):
        return JSONResponse({
            "ok": False,
            "error": test_result.get("user_message")
            or test_result.get("error")
            or "Chiave API o endpoint non validi.",
            "warnings": test_result.get("warnings", []),
        })

    save_provider_config(
        db,
        provider_type,
        api_key,
        endpoint,
        secret_store,
    )
    save_pipeline_mode(db, process_mode)
    save_provider_model(db, selected_model)
    save_capabilities(db, test_result.get("capabilities", {}))

    try:
        config_service.update_setting("llm_provider", provider_type)
        config_service.update_setting("llm_model", selected_model)
    except Exception:
        logger.warning("Could not save express provider settings to ConfigService")

    try:
        profile_id = create_express_pipeline_from_wizard(
            db,
            secret_store,
            process_mode=process_mode,
            selected_model=selected_model,
        )
    except Exception as exc:
        logger.exception("Express setup profile creation failed")
        return JSONResponse({
            "ok": False,
            "error": f"Salvataggio pipeline fallito: {exc}",
        })

    set_current_step(db, STEP_DONE)
    start_error = ""
    started = False
    try:
        await runtime_manager.start_async()
        started = True
    except Exception as exc:
        start_error = str(exc)
        logger.warning("Express setup saved but bot start failed: %s", exc)

    return JSONResponse({
        "ok": True,
        "status": "started" if started else "saved_no_start",
        "profile_id": profile_id,
        "started": started,
        "start_error": start_error,
        "redirect": "/login?setup=ok",
        "warnings": test_result.get("warnings", []),
    })


# ---------------------------------------------------------------------------
# POST /api/setup/save-provider  —  save provider connection only
# ---------------------------------------------------------------------------


@router.post("/api/setup/save-provider")
async def api_setup_save_provider(request: Request):
    """Save just the provider connection from the express setup,
    without creating a pipeline profile or starting the bot."""
    db = request.app.state.db

    body = await request.json()
    provider_type = (body.get("provider_type") or "").strip()
    api_key = (body.get("api_key") or "").strip()
    endpoint = (body.get("endpoint") or "").strip()
    model_name = (body.get("model_name") or "").strip()

    if not provider_type:
        return JSONResponse({"ok": False, "error": "Seleziona un provider."})
    if not api_key:
        return JSONResponse({"ok": False, "error": "Inserisci una chiave API."})

    if provider_type not in PROVIDER_PRESETS:
        return JSONResponse({"ok": False, "error": "Provider non valido."})

    adapter_type = _adapter_type_for_provider(provider_type)
    display_name = PROVIDER_PRESETS[provider_type]["label"]
    if not endpoint:
        endpoint = PROVIDER_PRESETS[provider_type].get("default_endpoint", "")

    try:
        if provider_type == "openrouter":
            probed, _ = await probe_openrouter_capabilities(api_key, endpoint, model_name)
            capabilities = probed.to_dict()
        else:
            capabilities = detect_capabilities(adapter_type, model_name).to_dict()
        if model_name:
            capabilities["models"] = [model_name]

        provider_id = db.add_provider(
            name=display_name,
            adapter_type=adapter_type,
            endpoint=endpoint or None,
            credentials=api_key,
            capabilities=capabilities,
            enabled=True,
        )
        logger.info(
            "Setup wizard: saved provider '%s' (id=%s, adapter=%s)",
            display_name, provider_id, adapter_type,
        )
    except Exception as exc:
        logger.exception("Failed to save provider from setup wizard")
        return JSONResponse({"ok": False, "error": f"Salvataggio provider fallito: {exc}"})

    return JSONResponse({
        "ok": True,
        "provider_id": provider_id,
        "capabilities": capabilities,
    })


# ---------------------------------------------------------------------------
# POST /setup  —  form-based step processing fallback
# ---------------------------------------------------------------------------


@router.post("/setup")
async def setup_post(request: Request):
    """Form-based step processing (fallback when JS is unavailable)."""
    db = request.app.state.db
    secret_store = request.app.state.secret_store
    config_service = request.app.state.config_service
    runtime_manager = request.app.state.runtime_manager

    form_data = await request.form()
    csrf_token_val = form_data.get("csrf_token", "")
    step = form_data.get("_step", "")

    session = get_session(request)
    if not validate_csrf_token(session or {}, csrf_token_val):
        return RedirectResponse(url="/setup?error=csrf", status_code=303)

    error_param = ""

    if step == "step_code":
        setup_code = form_data.get("setup_code", "")
        if not validate_setup_code(db, setup_code):
            error_param = "invalid_code"

    elif step == "step_admin":
        password = form_data.get("admin_password", "")
        confirm = form_data.get("admin_password_confirm", "")
        if password != confirm:
            error_param = "password_mismatch"
        if not error_param:
            result = _process_step(db, secret_store, config_service,
                                   runtime_manager, step,
                                   {"password": password, "password_confirm": confirm})
            if not result.get("ok"):
                error_param = "admin_failed"

    elif step == "step_telegram":
        token = form_data.get("telegram_token", "")
        if not token:
            error_param = "token_empty"
        else:
            result = _process_step(db, secret_store, config_service,
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
            result = _process_step(db, secret_store, config_service,
                                   runtime_manager, step,
                                   {"type": ptype, "api_key": api_key, "endpoint": endpoint})
            if not result.get("ok"):
                error_param = "provider_invalid"

    elif step == "step_pipeline":
        mode = form_data.get("pipeline_mode", "single")
        model_name = form_data.get("provider_model", "")
        _process_step(db, secret_store, config_service,
                      runtime_manager, step,
                      {"mode": mode, "model": model_name})

    elif step == "step_verify":
        _process_step(db, secret_store, config_service,
                      runtime_manager, "step_verify", {})

    if error_param:
        return RedirectResponse(url=f"/setup?error={error_param}", status_code=303)

    # Determine next step (using wizard state after processing)
    next_step = get_next_step(step) if not error_param else step
    if next_step:
        set_current_step(db, next_step)

    if next_step == STEP_PROVIDER:
        return RedirectResponse(url="/setup/express", status_code=303)

    if next_step == "step_done" or step == "step_verify":
        return RedirectResponse(url="/login?setup=ok", status_code=303)

    return RedirectResponse(url=f"/setup?step={next_step}", status_code=303)


# ---------------------------------------------------------------------------
# GET /api/setup/summary
# ---------------------------------------------------------------------------


@router.get("/api/setup/summary")
async def api_setup_summary(request: Request):
    """Return the current wizard summary (for the verify step)."""
    return build_summary(request.app.state.db, request.app.state.secret_store)


# ===========================================================================
# Internal helpers
# ===========================================================================


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


def _provider_label(provider_type: str) -> str:
    """Return the display label for a provider type."""
    preset = PROVIDER_PRESETS.get(provider_type)
    if preset:
        return preset.get("label", provider_type)
    return provider_type or "Provider"


_OPENROUTER_DISCOVERY_DEFAULT_LIMIT = 30
_OPENROUTER_DISCOVERY_MAX_LIMIT = 50


def _parse_discovery_limit(raw: str | None) -> int:
    """Return a bounded discovery limit suitable for provider catalog scans."""
    if raw is None or raw == "":
        return _OPENROUTER_DISCOVERY_DEFAULT_LIMIT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _OPENROUTER_DISCOVERY_DEFAULT_LIMIT
    return max(1, min(value, _OPENROUTER_DISCOVERY_MAX_LIMIT))


def _static_picker_card(
    *,
    model_id: str,
    name: str,
    provider: str,
    purpose: str,
    speed: str,
    quality: str,
    recommended: bool,
) -> Dict[str, Any]:
    """Return a static model picker card dict for non-OpenRouter providers."""
    single_pass = purpose == "single_pass"
    return {
        "kind": "model",
        "model_id": model_id,
        "name": name,
        "provider": provider,
        "description": "",
        "category": purpose,
        "capabilities": {
            "transcription": single_pass,
            "text_generation": True,
            "refinement": True,
            "streaming_refinement": True,
            "single_pass_audio_to_text": single_pass,
        },
        "pricing": {
            "input_per_million": None,
            "output_per_million": None,
            "currency": "USD",
        },
        "speed": speed,
        "quality": quality,
        "recommended": recommended,
        "source": "static",
    }


def _static_picker_cards(provider_type: str, purpose: str) -> list[Dict[str, Any]]:
    """Return deterministic picker cards for non-OpenRouter providers."""
    if provider_type == "gemini":
        models = [
            ("gemini-2.0-flash", "Gemini 2.0 Flash", "fast", "medium"),
            ("gemini-2.5-flash", "Gemini 2.5 Flash", "fast", "high"),
            ("gemini-2.5-pro", "Gemini 2.5 Pro", "slow", "high"),
        ]
        return [
            _static_picker_card(
                model_id=model_id,
                name=name,
                provider="Google Gemini",
                purpose="single_pass" if purpose == "single_pass" else "refinement",
                speed=speed,
                quality=quality,
                recommended=idx == 1,
            )
            for idx, (model_id, name, speed, quality) in enumerate(models)
        ]

    if purpose == "single_pass":
        return []

    models = [
        ("gpt-4o-mini", "GPT-4o mini", "fast", "high"),
        ("gpt-4o", "GPT-4o", "medium", "high"),
    ]
    return [
        _static_picker_card(
            model_id=model_id,
            name=name,
            provider="OpenAI",
            purpose=purpose,
            speed=speed,
            quality=quality,
            recommended=idx == 0,
        )
        for idx, (model_id, name, speed, quality) in enumerate(models)
    ]


def _build_wizard_context(
    db: "DatabaseManager",
    secret_store: "SecretStore | None" = None,
) -> Dict[str, Any]:
    """Build a context dict of saved wizard data for template rendering."""
    return {
        "telegram_token": get_telegram_token(db, secret_store),
        "provider": get_provider_config(db, secret_store),
        "pipeline_mode": get_pipeline_mode(db),
        "capabilities": get_capabilities(db),
        "summary": build_summary(db, secret_store),
    }


def _process_step(
    db: "DatabaseManager",
    secret_store: "SecretStore | None",
    config_service: "ConfigService",
    runtime_manager: "RuntimeManager",
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
            return {"ok": True}

        elif step == "step_admin":
            from bot.web.auth import set_admin_password

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

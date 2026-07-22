"""
Pipeline management routes extracted from the web frontend application factory.

Routes
------
- ``POST /api/pipeline/stages`` — save pipeline stages for a profile
- ``GET /admin/pipeline`` — pipeline management HTML page
- ``POST /admin/pipeline/save`` — save pipeline profile (form-based)
- ``GET /api/pipeline/info`` — pipeline configuration JSON
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from bot.capabilities import CapabilityModel
from bot.database import DatabaseManager
from bot.exceptions import ResourceInUseError
from bot.web.auth import generate_csrf_token, validate_csrf_token
from bot.web.dependencies import get_session, require_admin
from bot.web.pipeline_builder import (
    create_advanced_provider_profile,
    create_same_provider_profile,
    create_single_pass_profile,
    create_two_stage_profile,
    set_active_pipeline_profile_id,
)
from bot.web.setup_wizard import (
    _adapter_type_for_provider_type,
    _display_name_for_provider_type,
    get_active_pipeline_profile_id,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---- Routes: Pipeline stage management API -----------------------------------


@router.post("/api/pipeline/stages")
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
    session = get_session(request)
    if session is None or not session.get("admin"):
        return JSONResponse({"ok": False, "error": "Non autorizzato."},
                            status_code=401)

    db = request.app.state.db

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

    profile = db.get_pipeline_profile(profile_id)
    if profile is None:
        return JSONResponse({"ok": False, "error": "Profilo pipeline non trovato."},
                            status_code=404)

    try:
        # Set the mode on the profile
        db.set_pipeline_profile_mode(profile_id, mode)

        # Delete existing stages for this profile and recreate
        existing = db.list_pipeline_stages(profile_id)
        for stage in existing:
            db.delete_pipeline_stage(stage["id"])

        # Create new stages
        for stage_data in stages_data:
            stage_type = stage_data.get("stage_type", "")
            primary_id = stage_data.get("primary_model_id")
            fallback_ids = stage_data.get("fallback_model_ids", [])

            stage_id = db.add_pipeline_stage(
                profile_id=profile_id,
                stage_type=stage_type,
                primary_model_id=primary_id,
            )

            # Set fallbacks
            if fallback_ids:
                db.reorder_stage_fallbacks(
                    stage_id, fallback_ids,
                )

        return JSONResponse({"ok": True, "profile_id": profile_id})

    except Exception as exc:
        logger.exception("Failed to update pipeline stages")
        return JSONResponse({"ok": False, "error": str(exc)})


# ---- Routes: Admin — Pipeline management (P5) -------------------------------


@router.get("/admin/pipeline", response_class=HTMLResponse)
async def admin_pipeline(request: Request):
    """Pipeline configuration page."""
    session = require_admin(request)

    db = request.app.state.db
    templates = request.app.state.templates

    providers = db.list_providers()
    # Attach models to each provider for model selection
    providers_with_models = []
    for p in providers:
        p_models = db.list_provider_models(p["id"])
        providers_with_models.append({**p, "models": p_models})

    profile_id = get_active_pipeline_profile_id(db)
    profile = None
    if profile_id is not None:
        profile = db.get_pipeline_profile(profile_id)
        # Attach model info to stages for display
        if profile and profile.get("stages"):
            for stage in profile["stages"]:
                if stage.get("primary_model_id"):
                    model_entry = db.get_provider_model(
                        stage["primary_model_id"]
                    )
                    stage["_primary_model"] = model_entry
                if stage.get("fallbacks"):
                    for fb in stage["fallbacks"]:
                        fb_model = db.get_provider_model(
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


@router.post("/admin/pipeline/save")
async def admin_pipeline_save(request: Request):
    """Save pipeline configuration (form-based)."""
    require_admin(request)
    session = get_session(request) or {}
    form_data = await request.form()
    csrf = form_data.get("csrf_token", "")
    if not validate_csrf_token(session, csrf):
        return RedirectResponse(url="/admin/pipeline?error=csrf", status_code=303)

    db = request.app.state.db

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

            new_id = create_two_stage_profile(
                db,
                tx_model_id=tx_model_id,
                ref_model_id=ref_model_id,
                name="Pipeline due fasi",
            )
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

            new_id = create_single_pass_profile(
                db,
                model_id=sp_model_id,
                name="Pipeline singolo passaggio",
            )
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

            new_id = create_same_provider_profile(
                db,
                provider_id=pid,
                name="Pipeline predefinita",
            )
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

            new_id = create_advanced_provider_profile(
                db,
                transcription_provider_id=tx_pid,
                text_provider_id=ref_pid,
                name="Pipeline avanzata",
            )
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


# ---- Routes: API ------------------------------------------------------------


@router.get("/api/pipeline/info")
async def api_pipeline_info(request: Request):
    """Return the current pipeline configuration (for the admin page)."""
    db = request.app.state.db

    providers = db.list_providers()
    profile_id = get_active_pipeline_profile_id(db)
    profile = (
        db.get_pipeline_profile(profile_id)
        if profile_id is not None
        else None
    )
    # Attach models to each provider
    providers_with_models = []
    for p in providers:
        p_models = db.list_provider_models(p["id"], only_enabled=True)
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

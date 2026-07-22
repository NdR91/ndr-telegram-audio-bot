"""
Provider management routes extracted from ``app.py`` as a FastAPI APIRouter.

Routes
------
- ``/admin/providers*`` — admin provider management pages
- ``/api/providers*`` — provider JSON API endpoints
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from bot.capabilities import (
    _classify_openrouter_model,
    detect_capabilities,
    probe_openrouter_capabilities,
)
from bot.exceptions import ResourceInUseError
from bot.web.auth import generate_csrf_token, validate_csrf_token
from bot.web.dependencies import get_session, require_admin
from bot.web.helpers import (
    OPENROUTER_CURATED_SHORTLIST,
    blank_test_result,
    openrouter_catalog_item_wrapper,
    openrouter_model_category_wrapper,
    parse_discovery_limit,
    select_openrouter_models_wrapper,
    test_provider_connection,
)
from bot.web.setup_wizard import PROVIDER_PRESETS

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Admin pages — provider management
# ---------------------------------------------------------------------------


@router.get("/admin/providers", response_class=HTMLResponse)
async def admin_providers(request: Request):
    """Provider connection management page."""
    session = require_admin(request)
    providers = request.app.state.db.list_providers()

    return request.app.state.templates.TemplateResponse(
        request,
        "providers.html",
        {
            "csrf_token": session.get("csrf_token", generate_csrf_token()),
            "session": session,
            "providers": providers,
            "provider_presets": PROVIDER_PRESETS,
        },
    )


@router.post("/admin/providers/create")
async def admin_provider_create(request: Request):
    """Create a provider connection from the admin UI."""
    session = require_admin(request)
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

    db = request.app.state.db
    try:
        # For OpenRouter: probe model metadata for accurate capabilities.
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


@router.get("/admin/providers/{provider_id}", response_class=HTMLResponse)
async def admin_provider_detail(request: Request, provider_id: int):
    """Provider detail page with model discovery and management."""
    session = require_admin(request)
    provider = request.app.state.db.get_provider(provider_id)
    if provider is None:
        return request.app.state.templates.TemplateResponse(
            request,
            "error.html",
            {"title": "404", "message": "Provider non trovato."},
            status_code=404,
        )

    models = request.app.state.db.list_provider_models(provider_id)
    return request.app.state.templates.TemplateResponse(
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


@router.post("/admin/providers/{provider_id}/edit")
async def admin_provider_edit(request: Request, provider_id: int):
    """Edit a provider connection."""
    session = require_admin(request)
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

    db = request.app.state.db
    try:
        db.update_provider(provider_id, **updates)  # type: ignore[arg-type]
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


@router.post("/admin/providers/{provider_id}/delete")
async def admin_provider_delete(request: Request, provider_id: int):
    """Delete a provider connection."""
    session = require_admin(request)
    form_data = await request.form()
    csrf = form_data.get("csrf_token", "")
    if not validate_csrf_token(session, csrf):
        return RedirectResponse(
            url="/admin/providers?error=csrf", status_code=303,
        )

    db = request.app.state.db
    try:
        db.delete_provider(provider_id)
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


# ---------------------------------------------------------------------------
# API — provider model discovery and management
# ---------------------------------------------------------------------------


@router.post("/api/providers/{provider_id}/discover")
async def api_provider_discover_models(request: Request, provider_id: int):
    """Discover models from a provider's API and register them under
    provider_models."""
    session = get_session(request)
    if session is None or not session.get("admin"):
        return JSONResponse(
            {"ok": False, "error": "Non autorizzato."},
            status_code=401,
        )

    db = request.app.state.db
    provider = db.get_provider(provider_id)
    if provider is None:
        return JSONResponse(
            {"ok": False, "error": "Provider non trovato."},
            status_code=404,
        )

    adapter_type = provider.get("adapter_type", "")
    credentials = provider.get("credentials") or ""
    endpoint = provider.get("endpoint") or ""
    purpose = (request.query_params.get("purpose") or "all_recommended").strip()
    query = (request.query_params.get("query") or "").strip().lower()
    limit = parse_discovery_limit(request.query_params.get("limit"))

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
            url = (
                f"{endpoint.rstrip('/')}/models"
                if endpoint
                else "https://openrouter.ai/api/v1/models"
            )
            headers = {"Authorization": f"Bearer {credentials}"}
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    raw_models = [m for m in data if isinstance(m, dict) and m.get("id")]
                    models_discovered = select_openrouter_models_wrapper(
                        raw_models,
                        purpose=purpose,
                        query=query,
                        limit=limit,
                    )
        elif adapter_type in ("openai-native", "openai", "openai-compat"):
            url = (
                f"{endpoint.rstrip('/')}/models"
                if endpoint
                else "https://api.openai.com/v1/models"
            )
            headers = {"Authorization": f"Bearer {credentials}"}
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    models_discovered = [
                        m["id"] for m in data
                        if isinstance(m, dict) and m.get("id")
                    ][:limit]

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
                caps = _classify_openai_model(model_id)
            elif adapter_type in ("gemini-native", "gemini"):
                caps = _classify_gemini_model(model_id)
            elif is_openrouter and model_meta is not None:
                caps_model = _classify_openrouter_model(model_meta)
                caps = caps_model.to_dict()
                category = openrouter_model_category_wrapper(model_meta)
                counts[category] = counts.get(category, 0) + 1
            elif adapter_type == "openai-compat":
                caps = _classify_openai_compat_model(model_id)
            else:
                caps = _classify_openai_compat_model(model_id)

            # Register in provider_models
            entry_id = db.add_provider_model(
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
                    openrouter_model_category_wrapper(model_meta)
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


@router.get("/api/providers/{provider_id}/catalog")
async def api_provider_catalog(request: Request, provider_id: int):
    """Preview provider catalog entries without registering them."""
    session = get_session(request)
    if session is None or not session.get("admin"):
        return JSONResponse(
            {"ok": False, "error": "Non autorizzato."},
            status_code=401,
        )

    db = request.app.state.db
    provider = db.get_provider(provider_id)
    if provider is None:
        return JSONResponse(
            {"ok": False, "error": "Provider non trovato."},
            status_code=404,
        )

    adapter_type = provider.get("adapter_type", "")
    credentials = provider.get("credentials") or ""
    endpoint = provider.get("endpoint") or ""
    purpose = (request.query_params.get("purpose") or "all_recommended").strip()
    query = (request.query_params.get("query") or "").strip().lower()
    limit = parse_discovery_limit(request.query_params.get("limit"))
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
        url = (
            f"{endpoint.rstrip('/')}/models"
            if endpoint
            else "https://openrouter.ai/api/v1/models"
        )
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
        selected = select_openrouter_models_wrapper(
            raw_models,
            purpose=purpose,
            query=query,
            limit=limit,
        )
        models = [openrouter_catalog_item_wrapper(model) for model in selected]
        counts: dict[str, int] = {
            "refinement": 0,
            "transcription": 0,
            "single_pass": 0,
            "not_recommended": 0,
        }
        for model in raw_models:
            category = openrouter_model_category_wrapper(model)
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


@router.post("/api/providers/{provider_id}/apply-curated")
async def api_provider_apply_curated(request: Request, provider_id: int):
    """Register the curated OpenRouter shortlist for a provider.

    Fetches the OpenRouter catalog to get real capability metadata for each
    curated model.  Models not found in the catalog are registered with
    estimated capabilities.  Already-registered models are skipped.
    """
    session = get_session(request)
    if session is None or not session.get("admin"):
        return JSONResponse(
            {"ok": False, "error": "Non autorizzato."},
            status_code=401,
        )

    db = request.app.state.db
    provider = db.get_provider(provider_id)
    if provider is None:
        return JSONResponse(
            {"ok": False, "error": "Provider non trovato."},
            status_code=404,
        )

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
        url = (
            f"{endpoint.rstrip('/')}/models"
            if endpoint
            else "https://openrouter.ai/api/v1/models"
        )
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

    existing_ids = {m["model_id"] for m in db.list_provider_models(provider_id)}
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
            entry_id = db.add_provider_model(
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


@router.get("/api/providers/{provider_id}/models")
async def api_provider_list_models(request: Request, provider_id: int):
    """List all registered models for a provider."""
    session = get_session(request)
    if session is None or not session.get("admin"):
        return JSONResponse(
            {"ok": False, "error": "Non autorizzato."},
            status_code=401,
        )

    models = request.app.state.db.list_provider_models(provider_id)
    return JSONResponse({"ok": True, "models": models})


@router.post("/api/providers/{provider_id}/models")
async def api_provider_add_model(request: Request, provider_id: int):
    """Manually add a model to a provider."""
    session = get_session(request)
    if session is None or not session.get("admin"):
        return JSONResponse(
            {"ok": False, "error": "Non autorizzato."},
            status_code=401,
        )

    db = request.app.state.db
    provider = db.get_provider(provider_id)
    if provider is None:
        return JSONResponse(
            {"ok": False, "error": "Provider non trovato."},
            status_code=404,
        )

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse(
            {"ok": False, "error": "Richiesta JSON non valida."},
            status_code=400,
        )

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
        entry_id = db.add_provider_model(
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


@router.post("/api/providers/models/{entry_id}/capabilities")
async def api_provider_update_model_caps(request: Request, entry_id: int):
    """Update capabilities for a provider model entry."""
    session = get_session(request)
    if session is None or not session.get("admin"):
        return JSONResponse(
            {"ok": False, "error": "Non autorizzato."},
            status_code=401,
        )

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse(
            {"ok": False, "error": "Richiesta JSON non valida."},
            status_code=400,
        )

    capabilities = body.get("capabilities")
    if not capabilities:
        return JSONResponse({"ok": False, "error": "capabilities è obbligatorio."})

    try:
        request.app.state.db.set_model_capabilities(
            entry_id, capabilities, mark_overridden=True,
        )
        return JSONResponse({"ok": True})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


@router.post("/api/providers/models/{entry_id}/toggle")
async def api_provider_toggle_model(request: Request, entry_id: int):
    """Enable or disable a provider model."""
    session = get_session(request)
    if session is None or not session.get("admin"):
        return JSONResponse(
            {"ok": False, "error": "Non autorizzato."},
            status_code=401,
        )

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse(
            {"ok": False, "error": "Richiesta JSON non valida."},
            status_code=400,
        )

    enabled = body.get("enabled", True)
    try:
        request.app.state.db.update_provider_model(
            entry_id, enabled=enabled,
        )
        return JSONResponse({"ok": True})
    except ResourceInUseError as exc:
        return JSONResponse({"ok": False, "error": str(exc)})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


@router.post("/api/providers/models/{entry_id}/delete")
async def api_provider_delete_model(request: Request, entry_id: int):
    """Delete a provider model entry."""
    session = get_session(request)
    if session is None or not session.get("admin"):
        return JSONResponse(
            {"ok": False, "error": "Non autorizzato."},
            status_code=401,
        )

    try:
        request.app.state.db.delete_provider_model(entry_id)
        return JSONResponse({"ok": True})
    except ResourceInUseError as exc:
        return JSONResponse({"ok": False, "error": str(exc)})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


@router.post("/api/providers/test")
async def api_providers_test(request: Request):
    """Test a provider connection and classify capabilities.

    Accepts ``provider_type``, ``api_key``, ``endpoint``, and optional
    ``model_name``.  Returns JSON with authentication status, model
    availability, detected capabilities, a pipeline classification, a
    user-facing message, warnings, and a ``models`` list.

    Delegates to :func:`test_provider_connection` for the core logic.
    """
    session = get_session(request)
    if session is None or not session.get("admin"):
        return JSONResponse(
            {"ok": False, "error": "Non autorizzato."},
            status_code=401,
        )

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse(
            {"ok": False, "error": "Richiesta JSON non valida."},
            status_code=400,
        )

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
        result = await test_provider_connection(
            provider_type, api_key, endpoint, model_name,
        )
        return JSONResponse(result)
    except httpx.TimeoutException:
        result = blank_test_result()
        result["user_message"] = "❌ Timeout: server non raggiungibile. Verifica l'endpoint."
        result["warnings"].append("Il server non ha risposto entro 15 secondi.")
        return JSONResponse(result)
    except httpx.RequestError as exc:
        result = blank_test_result()
        result["user_message"] = f"❌ Errore di connessione: {exc}"
        result["warnings"].append("Verifica l'URL dell'endpoint e la connettività di rete.")
        return JSONResponse(result)


# ---------------------------------------------------------------------------
# Internal helpers — model classification
# ---------------------------------------------------------------------------


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
    _MULTIMODAL_PREFIXES = (
        "google/gemini-",
        "google/gemma-4",
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

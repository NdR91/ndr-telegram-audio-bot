"""
Admin dashboard, bot controls, and state/health API endpoints.

Extracted from ``app.py`` as a standalone :class:`~fastapi.APIRouter`
during the O5 refactor.  All route closures that captured local variables
are replaced with ``request.app.state.*`` access and the dependency
functions from :mod:`bot.web.dependencies`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from bot.state import AppState
from bot.web.auth import generate_csrf_token, has_admin, validate_csrf_token
from bot.web.dependencies import get_session, require_admin

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------------


@router.get("/admin/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Render the admin dashboard page with current state and health."""
    session = require_admin(request)
    state = request.app.state.state_checker.get_state()
    health = request.app.state.runtime_manager.get_health()

    return request.app.state.templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "csrf_token": session.get("csrf_token", generate_csrf_token()),
            "session": session,
            "state": state,
            "health": health,
        },
    )


@router.post("/admin/bot/start")
async def admin_bot_start(request: Request):
    """Start the Telegram bot."""
    session = require_admin(request)
    form_data = await request.form()
    csrf = form_data.get("csrf_token", "")
    if not validate_csrf_token(session, csrf):
        return RedirectResponse(url="/admin/dashboard?error=csrf", status_code=303)

    try:
        await request.app.state.runtime_manager.start_async()
        logger.info("Bot started from admin dashboard")
    except RuntimeError as exc:
        logger.warning("Bot start failed from dashboard: %s", exc)
        return RedirectResponse(
            url="/admin/dashboard?error=start_failed", status_code=303,
        )

    return RedirectResponse(url="/admin/dashboard", status_code=303)


@router.post("/admin/bot/stop")
async def admin_bot_stop(request: Request):
    """Stop the Telegram bot."""
    session = require_admin(request)
    form_data = await request.form()
    csrf = form_data.get("csrf_token", "")
    if not validate_csrf_token(session, csrf):
        return RedirectResponse(url="/admin/dashboard?error=csrf", status_code=303)

    await request.app.state.runtime_manager.stop_async()
    logger.info("Bot stopped from admin dashboard")
    return RedirectResponse(url="/admin/dashboard", status_code=303)


# ---------------------------------------------------------------------------
# API — application state and health
# ---------------------------------------------------------------------------


@router.get("/api/state")
async def api_state(request: Request):
    """Return the current application state as JSON."""
    info = request.app.state.state_checker.get_state()
    return {
        "state": info.state.value,
        "label": info.label,
        "description": info.description,
        "next_action": info.next_action,
        "can_process_audio": request.app.state.state_checker.can_process_audio(),
    }


@router.get("/api/health")
async def api_health(request: Request):
    """Return the runtime health status as JSON."""
    return request.app.state.runtime_manager.get_health()


# ---------------------------------------------------------------------------
# Root redirect
# ---------------------------------------------------------------------------


@router.get("/")
async def root(request: Request):
    """Redirect to the most appropriate page based on auth and setup state."""
    session = get_session(request)
    db = request.app.state.db
    if has_admin(db) and session is not None and session.get("admin"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    if has_admin(db):
        return RedirectResponse(url="/login", status_code=303)
    return RedirectResponse(url="/setup", status_code=303)

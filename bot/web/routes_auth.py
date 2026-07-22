"""
Authentication routes for the web frontend.

Provides login, logout, and password recovery endpoints extracted from the
main application factory as a standalone FastAPI APIRouter.

Routes
------
- ``GET /login`` — login page
- ``POST /login`` — login form submission
- ``POST /logout`` — logout
- ``GET /recovery`` — password recovery page
- ``POST /recovery`` — recovery code entry
- ``POST /recovery/reset`` — password reset
- ``POST /api/recovery/generate`` — generate recovery code (admin only)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import bot.recovery
from bot.web.auth import (
    generate_csrf_token,
    has_admin,
    set_admin_password,
    verify_admin_password,
    validate_csrf_token,
)
from bot.web.dependencies import get_session, set_session_cookie

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /login
# ---------------------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    session = get_session(request)
    db = request.app.state.db
    templates = request.app.state.templates

    if session is not None and session.get("admin") and has_admin(db):
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
        set_session_cookie(response, session, request)
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


# ---------------------------------------------------------------------------
# POST /login
# ---------------------------------------------------------------------------


@router.post("/login")
async def login_post(
    request: Request,
    password: str = Form(""),
    csrf_token: str = Form(""),
):
    session = get_session(request)
    if not validate_csrf_token(session or {}, csrf_token):
        return RedirectResponse(url="/login?error=csrf", status_code=303)

    if not verify_admin_password(request.app.state.db, password):
        return RedirectResponse(url="/login?error=invalid", status_code=303)

    # Create authenticated session
    response = RedirectResponse(url="/admin/dashboard", status_code=303)
    set_session_cookie(response, {"admin": True, "csrf_token": generate_csrf_token()}, request)
    return response


# ---------------------------------------------------------------------------
# POST /logout
# ---------------------------------------------------------------------------


@router.post("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session")
    return response


# ---------------------------------------------------------------------------
# GET /recovery
# ---------------------------------------------------------------------------


@router.get("/recovery", response_class=HTMLResponse)
async def recovery_page(request: Request):
    session = get_session(request)
    templates = request.app.state.templates
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
        set_session_cookie(response, session, request)
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


# ---------------------------------------------------------------------------
# POST /recovery  —  validate recovery code
# ---------------------------------------------------------------------------


@router.post("/recovery")
async def recovery_post(
    request: Request,
    recovery_code: str = Form(""),
    csrf_token: str = Form(""),
):
    session = get_session(request)
    if not validate_csrf_token(session or {}, csrf_token):
        return RedirectResponse(url="/recovery?error=csrf", status_code=303)

    if not bot.recovery.validate_recovery_code(request.app.state.db, recovery_code):
        return RedirectResponse(url="/recovery?error=invalid_code", status_code=303)

    # Store approval in session — next step shows password form
    response = RedirectResponse(url="/recovery", status_code=303)
    session_data = session or {}
    session_data["recovery_approved"] = True
    session_data["csrf_token"] = generate_csrf_token()
    set_session_cookie(response, session_data, request)
    return response


# ---------------------------------------------------------------------------
# POST /recovery/reset  —  set new password
# ---------------------------------------------------------------------------


@router.post("/recovery/reset")
async def recovery_reset(
    request: Request,
    password: str = Form(""),
    password_confirm: str = Form(""),
    csrf_token: str = Form(""),
):
    session = get_session(request)
    if session is None or not session.get("recovery_approved"):
        return RedirectResponse(url="/recovery?error=unauthorized", status_code=303)

    if not validate_csrf_token(session, csrf_token):
        return RedirectResponse(url="/recovery?error=csrf", status_code=303)

    if password != password_confirm:
        return RedirectResponse(url="/recovery?error=mismatch", status_code=303)

    if len(password) < 8:
        return RedirectResponse(url="/recovery?error=too_short", status_code=303)

    set_admin_password(request.app.state.db, password)
    bot.recovery.invalidate_recovery_code(request.app.state.db)

    # Clear session and redirect to login
    response = RedirectResponse(url="/login?recovery=ok", status_code=303)
    response.delete_cookie("session")
    return response


# ---------------------------------------------------------------------------
# POST /api/recovery/generate
# ---------------------------------------------------------------------------


@router.post("/api/recovery/generate")
async def api_recovery_generate(request: Request):
    """Generate a new recovery code and return it.
    Requires admin authentication.
    """
    session = get_session(request)
    if session is None or not session.get("admin"):
        return JSONResponse({"ok": False, "error": "Non autorizzato."}, status_code=401)

    code = bot.recovery.generate_recovery_code(request.app.state.db)
    logger.info("Recovery code generated via API by admin")
    return JSONResponse({"ok": True, "code": code})

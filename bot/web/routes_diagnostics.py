"""Authenticated V2 overview and content-free diagnostic history."""

from datetime import timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from bot.web.auth import decode_session

router = APIRouter()
_templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def _admin(request: Request) -> bool:
    cookie = request.cookies.get("session")
    if not cookie:
        return False
    session = decode_session(request.app.state.serialiser, cookie)
    return bool(session and session.get("admin"))


def _events(request: Request) -> list[dict]:
    services = request.app.state.services
    result = []
    for event in services.diagnostics.recent():
        result.append({
            "at": event.at.astimezone(timezone.utc).isoformat(),
            "stage": event.stage,
            "status": event.status,
            "revision": event.revision,
            "models": event.models,
            "duration_ms": event.duration_ms,
            "error_code": event.error_code,
        })
    return result


@router.get("/admin/dashboard", response_class=HTMLResponse)
async def overview(request: Request):
    if not _admin(request):
        return RedirectResponse("/login", status_code=303)
    services = request.app.state.services
    return _templates.TemplateResponse(request, "diagnostics.html", {
        "overview": True,
        "health": services.diagnostics.health(),
        "events": [],
    })


@router.get("/admin/diagnostics", response_class=HTMLResponse)
async def diagnostics_page(request: Request):
    if not _admin(request):
        return RedirectResponse("/login", status_code=303)
    services = request.app.state.services
    return _templates.TemplateResponse(request, "diagnostics.html", {
        "overview": False,
        "health": services.diagnostics.health(),
        "events": _events(request),
    })


@router.get("/api/diagnostics")
async def diagnostics_api(request: Request):
    if not _admin(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    services = request.app.state.services
    return {"health": services.diagnostics.health(), "events": _events(request)}

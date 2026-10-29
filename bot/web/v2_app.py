"""V2 onboarding and authenticated pairing endpoints."""

import hashlib
import logging
import secrets
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from bot.deployment import DeploymentSettings
from bot.database.runtime_lock import RuntimeDatabaseLocked
from bot.diagnostics import DiagnosticEvent
from bot.services import AppServices, build_services
from bot.web.auth import _make_serialiser, decode_session, encode_session, hash_password, verify_password
from bot.web.auth_limits import AuthAttemptLimiter
from bot.web.routes_diagnostics import router as diagnostics_router
from bot.v2_recovery import reset_password
from bot.transcription.models import ConfigInput, RuntimeLimits
from bot.transcription.errors import (ConfigurationInvalid, PrerequisiteMissing,
                                      RevisionConflict, TestRequired)
from bot.transcription.catalog import CatalogService

_HERE = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)


class _UploadTooLarge(OSError):
    """Abort multipart parsing before an unbounded request can be spooled."""


def _password_input_error(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return "password_required"
    try:
        if len(value.encode("utf-8")) > 72:
            return "password_too_long"
    except UnicodeEncodeError:
        return "password_invalid"
    return None


def create_app(settings: DeploymentSettings | None = None,
               services: AppServices | None = None) -> FastAPI:
    settings = settings or DeploymentSettings.from_environment()
    services = services or build_services(settings)
    repo = services.repo
    auth_attempts = AuthAttemptLimiter(repo)
    secret_version = repo.get_setup_state("web_session_credential_version")
    if secret_version:
        try:
            session_secret = repo.read_credential("web_session", int(secret_version))
        except Exception:
            # The web shell remains available for recovery when encryption
            # is unavailable; existing signed sessions are invalidated.
            session_secret = secrets.token_urlsafe(48)
    else:
        session_secret = secrets.token_urlsafe(48)
        try:
            repo.put_credential_with_state("web_session", session_secret,
                                           "web_session_credential_version")
        except Exception:
            pass
    serialiser = _make_serialiser(session_secret)
    templates = Jinja2Templates(directory=str(_HERE / "templates"))

    @__import__("contextlib").asynccontextmanager
    async def lifespan(app):
        services.runtime._asgi_lifespan_entered = True
        try:
            try:
                await services.runtime.start_async()
            except RuntimeDatabaseLocked:
                raise
            except Exception:
                # Keep setup and recovery reachable when a saved Telegram token
                # cannot start polling; an authenticated operator can replace it.
                logger.warning("Telegram polling could not start; web setup remains available")
            yield
        finally:
            await services.runtime.stop_async(release_runtime_lock=True)
            await services.pool.aclose()
            repo.close()

    app = FastAPI(title="Telegram Audio Bot V2", lifespan=lifespan)
    app.state.services = services
    app.state.serialiser = serialiser
    app.state.templates = templates
    app.state.auth_attempts = auth_attempts
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
    app.include_router(diagnostics_router)

    def session(request: Request):
        cookie = request.cookies.get("session")
        value = decode_session(serialiser, cookie) if cookie else None
        return value if value and value.get("admin") else None

    def csrf_ok(current, request):
        token = request.headers.get("x-csrf-token")
        if not token:
            return False
        return secrets.compare_digest(str(current.get("csrf_token", "")), token)

    def rate_limited(endpoint, request):
        address = request.client.host if request.client else "unknown"
        retry_after = auth_attempts.consume(endpoint, address)
        if retry_after is None:
            return None, address
        return JSONResponse({"error": "rate_limited"}, status_code=429,
                            headers={"Retry-After": str(retry_after)}), address

    def clear_auth_attempts_best_effort(endpoint: str, address: str) -> None:
        try:
            auth_attempts.clear(endpoint, address)
        except Exception:
            logger.warning("Could not clear web authentication attempt counter")

    def page(request, template, context=None, status=200):
        values = dict(context or {})
        values["csrf_token"] = (session(request) or {}).get("csrf_token", "")
        values["admin"] = bool(session(request))
        return templates.TemplateResponse(request, f"v2_{template}", values, status_code=status)

    @app.get("/", response_class=HTMLResponse)
    async def root(request: Request):
        if session(request):
            if services.access.has_owner() and repo.get_setup_state("telegram_credential_version"):
                return RedirectResponse("/admin/transcription", status_code=303)
            return page(request, "setup.html", {"step": "owner", "owner": services.access.list_entries()})
        return RedirectResponse("/setup", status_code=303)

    @app.get("/setup", response_class=HTMLResponse)
    async def setup_page(request: Request):
        if repo.get_setup_state("web_admin_hash"):
            return RedirectResponse("/login", status_code=303)
        return page(request, "setup.html", {"step": "create_admin"})

    @app.post("/api/setup/admin")
    async def create_admin(request: Request):
        if repo.get_setup_state("web_admin_hash"):
            return JSONResponse({"error": "setup_complete"}, status_code=409)
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        limited, address = rate_limited("setup", request)
        if limited:
            return limited
        raw_code = body.get("code", "")
        code = raw_code if isinstance(raw_code, str) else ""
        password = body.get("password")
        password_error = _password_input_error(password)
        if password_error:
            return JSONResponse({"error": password_error}, status_code=422)
        digest = hashlib.sha256(code.encode()).hexdigest()
        with repo.read() as connection:
            candidate_rows = dict(connection.execute(
                "SELECT setup_key, setup_value FROM setup_state "
                "WHERE setup_key IN ('setup_code_hash', 'setup_code_expires_at')"
            ).fetchall())
        try:
            candidate_expiry = datetime.fromisoformat(candidate_rows.get("setup_code_expires_at", ""))
        except ValueError:
            candidate_expiry = datetime.min.replace(tzinfo=timezone.utc)
        candidate_hash = candidate_rows.get("setup_code_hash", "")
        if (candidate_expiry.tzinfo is None or datetime.now(timezone.utc) >= candidate_expiry
                or not secrets.compare_digest(digest, candidate_hash)):
            return JSONResponse({"error": "invalid_setup_code"}, status_code=401)
        password_hash = hash_password(password)
        with repo.transaction() as connection:
            rows = dict(connection.execute(
                "SELECT setup_key, setup_value FROM setup_state WHERE setup_key IN ('setup_code_hash', 'setup_code_expires_at')"
            ).fetchall())
            try:
                expires_at = datetime.fromisoformat(rows.get("setup_code_expires_at", ""))
            except ValueError:
                expires_at = datetime.min.replace(tzinfo=timezone.utc)
            if (expires_at.tzinfo is None or datetime.now(timezone.utc) >= expires_at
                    or not secrets.compare_digest(digest, rows.get("setup_code_hash", ""))):
                return JSONResponse({"error": "invalid_setup_code"}, status_code=401)
            connection.execute(
                "INSERT INTO setup_state(setup_key, setup_value) VALUES ('web_admin_hash', ?) ON CONFLICT(setup_key) DO UPDATE SET setup_value=excluded.setup_value",
                (password_hash,),
            )
            connection.execute("UPDATE setup_state SET setup_value='' WHERE setup_key IN ('setup_code_hash', 'setup_code_expires_at')")
        auth_attempts.clear("setup", address)
        current = {"admin": True, "csrf_token": secrets.token_urlsafe(32)}
        response = JSONResponse({"ok": True, "csrf_token": current["csrf_token"]})
        response.set_cookie("session", encode_session(serialiser, current), httponly=True,
                            secure=settings.secure_cookie, samesite="strict", max_age=86400)
        return response

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        return page(request, "login.html")

    @app.get("/recovery", response_class=HTMLResponse)
    async def recovery_page(request: Request):
        return page(request, "recovery.html")

    @app.post("/api/recovery/reset")
    async def recovery_reset(request: Request):
        nonlocal serialiser
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        limited, address = rate_limited("recovery", request)
        if limited:
            return limited
        raw_code = body.get("code", "")
        code = raw_code if isinstance(raw_code, str) else ""
        password = body.get("password")
        password_error = _password_input_error(password)
        if password_error:
            return JSONResponse({"error": password_error}, status_code=422)
        replacement_secret = secrets.token_urlsafe(48)
        try:
            success = reset_password(
                repo, code, password,
                new_session_secret=replacement_secret,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except Exception:
            return JSONResponse({"error": "password_update_unavailable"}, status_code=503)
        if not success:
            return JSONResponse({"error": "invalid_or_expired_code"}, status_code=401)
        serialiser = _make_serialiser(replacement_secret)
        app.state.serialiser = serialiser
        clear_auth_attempts_best_effort("recovery", address)
        return {"ok": True, "sessions_invalidated": True}

    @app.post("/api/login")
    async def login(request: Request):
        body = await request.json()
        limited, address = rate_limited("login", request)
        if limited:
            return limited
        password = body.get("password") if isinstance(body, dict) else None
        if not isinstance(password, str) or not verify_password(password, repo.get_setup_state("web_admin_hash") or ""):
            return JSONResponse({"error": "invalid_credentials"}, status_code=401)
        auth_attempts.clear("login", address)
        current = {"admin": True, "csrf_token": secrets.token_urlsafe(32)}
        response = JSONResponse({"ok": True, "csrf_token": current["csrf_token"]})
        response.set_cookie("session", encode_session(serialiser, current), httponly=True,
                            secure=settings.secure_cookie, samesite="strict", max_age=86400)
        return response

    @app.post("/api/password/change")
    async def change_password(request: Request):
        nonlocal serialiser
        current = session(request)
        if not current:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not csrf_ok(current, request):
            return JSONResponse({"error": "csrf"}, status_code=403)
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        current_password = body.get("current_password")
        new_password = body.get("new_password")
        confirmation = body.get("confirm_password")
        if not isinstance(current_password, str) or not current_password:
            return JSONResponse({"error": "current_password_required"}, status_code=422)
        password_error = _password_input_error(new_password)
        if password_error:
            return JSONResponse({"error": password_error}, status_code=422)
        if not isinstance(confirmation, str):
            return JSONResponse({"error": "password_confirmation_mismatch"}, status_code=422)
        if new_password != confirmation:
            return JSONResponse({"error": "password_confirmation_mismatch"}, status_code=422)

        limited, address = rate_limited("password_change", request)
        if limited:
            return limited
        previous_hash = repo.get_setup_state("web_admin_hash") or ""
        try:
            current_matches = verify_password(current_password, previous_hash)
        except Exception:
            current_matches = False
        if not current_matches:
            return JSONResponse({"error": "invalid_current_password"}, status_code=401)
        try:
            replacement_hash = hash_password(new_password)
            replacement_secret = secrets.token_urlsafe(48)
            changed = repo.rotate_web_admin_password(
                expected_password_hash=previous_hash,
                new_password_hash=replacement_hash,
                new_session_secret=replacement_secret,
            )
        except Exception:
            return JSONResponse({"error": "password_update_unavailable"}, status_code=503)
        if not changed:
            return JSONResponse({"error": "session_changed"}, status_code=409)
        serialiser = _make_serialiser(replacement_secret)
        app.state.serialiser = serialiser
        clear_auth_attempts_best_effort("password_change", address)
        renewed = {"admin": True, "csrf_token": secrets.token_urlsafe(32)}
        response = JSONResponse({"ok": True, "csrf_token": renewed["csrf_token"]})
        response.set_cookie("session", encode_session(serialiser, renewed), httponly=True,
                            secure=settings.secure_cookie, samesite="strict", max_age=86400)
        return response

    @app.post("/api/logout")
    async def logout(request: Request):
        current = session(request)
        if not current:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not csrf_ok(current, request):
            return JSONResponse({"error": "csrf"}, status_code=403)
        response = JSONResponse({"ok": True})
        response.delete_cookie("session")
        return response

    @app.post("/api/setup/telegram")
    async def connect_telegram(request: Request):
        current = session(request)
        if not current:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not csrf_ok(current, request):
            return JSONResponse({"error": "csrf"}, status_code=403)
        body = await request.json()
        try:
            identity = await services.runtime.connect(str(body.get("token", "")))
            await services.runtime.start_async()
        except Exception:
            return JSONResponse({"error": "telegram_connection_failed"}, status_code=422)
        return {"ok": True, "id": identity.id, "username": identity.username}

    @app.post("/api/access/invites")
    async def issue_invite(request: Request):
        current = session(request)
        if not current:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not csrf_ok(current, request):
            return JSONResponse({"error": "csrf"}, status_code=403)
        body = await request.json()
        try:
            kind = body.get("kind", "owner")
            if not isinstance(kind, str):
                raise ValueError("Pairing kind is invalid")
            invite = services.pairing.issue(kind)
        except ValueError:
            return JSONResponse({"error": "invalid_kind"}, status_code=422)
        return {"id": invite.id, "link": invite.link, "expires_at": invite.expires_at.isoformat()}

    @app.get("/api/access/invites/{invite_id}")
    async def get_invite(invite_id: str, request: Request):
        if not session(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            return services.pairing.get(invite_id)
        except ValueError:
            return JSONResponse({"error": "not_found"}, status_code=404)

    @app.post("/api/access/invites/{invite_id}/confirm")
    async def confirm_invite(invite_id: str, request: Request):
        current = session(request)
        if not current:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not csrf_ok(current, request):
            return JSONResponse({"error": "csrf"}, status_code=403)
        try:
            try:
                body = await request.json()
            except ValueError:
                body = {}
            candidate_id = body.get("candidate_id") if isinstance(body, dict) else None
            if candidate_id is not None:
                candidate = services.pairing.get(invite_id)
                if isinstance(candidate_id, bool) or str(candidate_id) != str(candidate["candidate_id"]):
                    return JSONResponse({"error": "candidate_mismatch"}, status_code=409)
            services.pairing.confirm(invite_id)
        except ValueError:
            return JSONResponse({"error": "invalid_invite"}, status_code=409)
        return {"ok": True}

    @app.get("/api/access")
    async def get_access(request: Request):
        _, error = admin_or_error(request)
        if error:
            return error
        entries = services.access.list_entries_with_status()
        current_bot_id = repo.get_setup_state("current_bot_id")
        return {"owner": entries["owner"], "users": entries["user"],
                "groups": entries["group"], "labels": services.access.labels(),
                "bot_connected": bool(current_bot_id)}

    @app.put("/api/access/entries/{kind}/{entry_id}/label")
    async def rename_access_entry(kind: str, entry_id: str, request: Request):
        current, error = admin_or_error(request)
        if error: return error
        if not csrf_ok(current, request): return JSONResponse({"error": "csrf"}, status_code=403)
        try:
            if not re.fullmatch(r'-?[0-9]+', entry_id):
                raise ValueError('ID non valido.')
            body = await request.json()
            services.access.rename(kind, int(entry_id), body.get('label'))
            return {"ok": True}
        except LookupError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except (ValueError, TypeError, AttributeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    @app.post("/api/access/entries")
    async def add_access_entry(request: Request):
        current, error = admin_or_error(request)
        if error:
            return error
        if not csrf_ok(current, request):
            return JSONResponse({"error": "csrf"}, status_code=403)
        try:
            body = await request.json()
            kind = body.get("kind")
            raw_id = body.get("entry_id")
            if not isinstance(kind, str) or isinstance(raw_id, bool):
                raise ValueError("Access entry is invalid")
            if kind == "owner":
                return JSONResponse({"error": "owner_requires_pairing"}, status_code=422)
            if isinstance(raw_id, int):
                entry_id = raw_id
            elif isinstance(raw_id, str) and re.fullmatch(r"-?[0-9]+", raw_id):
                entry_id = int(raw_id)
            else:
                raise ValueError("Access ID must be an integer")
            if kind not in {"owner", "user", "group"}:
                raise ValueError("Access kind is invalid")
            services.access.authorize(kind, entry_id)
        except (ValueError, TypeError, AttributeError):
            return JSONResponse({"error": "invalid_entry"}, status_code=422)
        return {"ok": True, "entries": services.access.list_entries_with_status()}

    @app.delete("/api/access/entries/{kind}/{entry_id}")
    async def remove_access_entry(kind: str, entry_id: str, request: Request):
        current, error = admin_or_error(request)
        if error:
            return error
        if not csrf_ok(current, request):
            return JSONResponse({"error": "csrf"}, status_code=403)
        if not re.fullmatch(r"-?[0-9]+", entry_id):
            return JSONResponse({"error": "invalid_entry"}, status_code=422)
        try:
            if kind not in {"owner", "user", "group"}:
                raise ValueError("Access kind is invalid")
            services.access.remove(kind, int(entry_id))
        except ValueError as exc:
            status = 409 if "At least one Telegram owner" in str(exc) else 422
            return JSONResponse({"error": str(exc)}, status_code=status)
        return {"ok": True, "entries": services.access.list_entries_with_status()}

    @app.post("/api/access/groups/{entry_id}/membership")
    async def check_group_membership(entry_id: str, request: Request):
        current, error = admin_or_error(request)
        if error:
            return error
        if not csrf_ok(current, request):
            return JSONResponse({"error": "csrf"}, status_code=403)
        if not re.fullmatch(r"-?[0-9]+", entry_id):
            return JSONResponse({"error": "invalid_entry"}, status_code=422)
        group_id = int(entry_id)
        if group_id not in services.access.list_entries()["group"]:
            return JSONResponse({"error": "group_not_authorized"}, status_code=404)
        app_instance = getattr(services.runtime, "_app", None)
        bot_id = repo.get_setup_state("current_bot_id")
        if app_instance is None or not bot_id:
            return {"membership": "unchecked"}
        try:
            member = await app_instance.bot.get_chat_member(group_id, int(bot_id))
            status = getattr(member, "status", "")
            if status == "restricted":
                membership = "member" if getattr(member, "is_member", False) else "not_member"
            elif status in {"member", "administrator", "creator"}:
                membership = status
            else:
                membership = "not_member"
            return {"membership": membership}
        except Exception:
            return {"membership": "unchecked"}

    @app.get("/admin/access", response_class=HTMLResponse)
    async def access_page(request: Request):
        if not session(request):
            return RedirectResponse("/login", status_code=303)
        return page(request, "access.html", {
            "access": {"owner": [], "users": [], "groups": [],
                       "bot_connected": bool(repo.get_setup_state("current_bot_id"))},
        })

    @app.get("/api/health")
    async def health():
        return services.runtime.get_health()

    def admin_or_error(request):
        current = session(request)
        if not current:
            return None, JSONResponse({"error": "unauthorized"}, status_code=401)
        return current, None

    def revision_payload(revision):
        if revision is None:
            return None
        values = revision.values
        return {"revision": revision.number, "service": values.service, "mode": values.mode,
                "audio_model": values.audio_model, "text_model": values.text_model,
                "credential_version": values.credential_version,
                "system_prompt": values.system_prompt, "refine_template": values.refine_template,
                "limits": values.limits.__dict__}

    def test_payload(view):
        result = view.result
        return {"id": view.id, "profile_id": getattr(view, "profile_id", None),
                "revision": view.revision, "status": view.status,
                "safe_error": view.safe_error,
                "diagnostics": list(view.diagnostics),
                "result": ({"text": result.text, "partial": result.partial,
                            "models": list(result.models), "duration_ms": result.duration_ms,
                            "cost": str(result.cost) if result.cost is not None else None,
                            "diagnostics": list(result.diagnostics)}
                           if result else None)}

    def profile_values(values):
        return {"service": values.service, "mode": values.mode,
                "audio_model": values.audio_model, "text_model": values.text_model,
                "credential_version": values.credential_version,
                "system_prompt": values.system_prompt,
                "refine_template": values.refine_template,
                "limits": values.limits.__dict__}

    def role_values(entry):
        revision = (services.profiles.get_revision(entry.profile_id, entry.role_revision)
                    if entry.role_revision is not None else None)
        if revision is None:
            return None
        values = revision.values
        return {"service": values.service, "mode": values.mode,
                "audio_model": values.audio_model, "text_model": values.text_model}

    def profile_state(entry, *, detail=True):
        revision_number = entry.current_revision or entry.role_revision
        revision = (services.profiles.get_revision(entry.profile_id, revision_number)
                    if revision_number is not None else None)
        role = "main" if entry.role == "primary" else entry.role
        verification = {"not_verified": "unverified", "retest": "retest_required"}.get(
            entry.verification_state, entry.verification_state
        )
        last_test = ({"success": entry.last_test_success, "finished_at": entry.last_tested_at}
                     if entry.last_tested_at else None)
        common = {"id": entry.profile_id, "name": entry.name,
                  "revision": revision_number, "generation": entry.generation,
                  "role": role, "verification": verification, "last_test": last_test}
        assigned_values = role_values(entry)
        if revision is None:
            return common | {"role_revision": entry.role_revision,
                             "role_values": assigned_values}
        values = profile_values(revision.values)
        if detail:
            return {**common, "role_revision": entry.role_revision,
                    "role_values": assigned_values, "values": values}
        role_verification = {"not_verified": "unverified", "retest": "retest_required"}.get(
            entry.role_verification_state, entry.role_verification_state
        )
        role_last_test = ({"success": entry.role_last_test_success,
                           "finished_at": entry.role_last_tested_at}
                          if entry.role_last_tested_at else None)
        catalog = {key: common[key] for key in ("id", "name", "revision", "generation", "role",
                                                "verification", "last_test")} | {
            "role_revision": entry.role_revision,
            "role_values": assigned_values,
            "role_verification": role_verification,
            "role_last_test": role_last_test,
        }
        return catalog | {key: values[key] for key in ("service", "mode", "audio_model", "text_model")}

    def profile_error(exc):
        if isinstance(exc, RevisionConflict):
            return JSONResponse({"error": str(exc)}, status_code=409)
        if isinstance(exc, ConfigurationInvalid):
            return JSONResponse({"error": str(exc)}, status_code=422)
        return JSONResponse({"error": "Richiesta non valida."}, status_code=422)

    def profile_input(body):
        values = body["values"]
        return ConfigInput(
            service=values["service"], mode=values["mode"],
            audio_model=str(values.get("audio_model", "")),
            text_model=values.get("text_model"),
            credential_version=int(values["credential_version"]),
            system_prompt=str(values.get("system_prompt", "")),
            refine_template=str(values.get("refine_template", "")),
            limits=RuntimeLimits(**values.get("limits", {})),
        )

    @app.get("/api/profiles")
    async def list_profiles(request: Request):
        _, error = admin_or_error(request)
        if error:
            return error
        entries = services.profiles.list_profiles()
        sample = services.transcription_tests.sample_info()
        sample_digest = hashlib.sha256(sample.path.read_bytes()).hexdigest() if sample.path.is_file() else None
        safe_sample = ({"expected_text": sample.expected_text,
                        "duration_seconds": sample.duration_seconds, "sha256": sample.sha256}
                       if sample_digest == sample.sha256 else None)
        return {"profiles": [profile_state(item, detail=False) for item in entries],
                "roles_generation": services.profiles.roles_generation(), "sample": safe_sample}

    @app.get("/api/profiles/{profile_id}")
    async def get_profile(profile_id: int, request: Request):
        _, error = admin_or_error(request)
        if error:
            return error
        try:
            entry = next(item for item in services.profiles.list_profiles()
                         if item.profile_id == profile_id)
            return {"profile": profile_state(entry)}
        except StopIteration:
            return JSONResponse({"error": "Profilo non trovato."}, status_code=404)
        except ConfigurationInvalid as exc:
            return profile_error(exc)

    @app.post("/api/profiles")
    async def create_profile(request: Request):
        current, error = admin_or_error(request)
        if error:
            return error
        if not csrf_ok(current, request):
            return JSONResponse({"error": "csrf"}, status_code=403)
        try:
            body = await request.json()
            revision = services.profiles.create(str(body["name"]), profile_input(body))
            entry = next(item for item in services.profiles.list_profiles()
                         if item.profile_id == revision.profile_id)
            return JSONResponse({"profile": profile_state(entry)}, status_code=201)
        except (ValueError, KeyError, TypeError, ConfigurationInvalid) as exc:
            return profile_error(exc) if isinstance(exc, ConfigurationInvalid) else JSONResponse(
                {"error": "Profilo non valido."}, status_code=422)

    @app.put("/api/profiles/{profile_id}")
    async def update_profile(profile_id: int, request: Request):
        current, error = admin_or_error(request)
        if error:
            return error
        if not csrf_ok(current, request):
            return JSONResponse({"error": "csrf"}, status_code=403)
        try:
            body = await request.json()
            revision = services.profiles.save_revision(
                profile_id, body.get("expected_revision"),
                expected_generation=int(body["expected_generation"]),
                name=str(body["name"]), values=profile_input(body),
            )
            entry = next(item for item in services.profiles.list_profiles()
                         if item.profile_id == profile_id)
            return {"profile": profile_state(entry)}
        except (ValueError, KeyError, TypeError, ConfigurationInvalid, RevisionConflict, TestRequired) as exc:
            if isinstance(exc, (ConfigurationInvalid, RevisionConflict)):
                return profile_error(exc)
            return JSONResponse({"error": "Profilo non valido."}, status_code=422)

    @app.post("/api/profiles/{profile_id}/duplicate")
    async def duplicate_profile(profile_id: int, request: Request):
        current, error = admin_or_error(request)
        if error:
            return error
        if not csrf_ok(current, request):
            return JSONResponse({"error": "csrf"}, status_code=403)
        try:
            body = await request.json()
            revision = services.profiles.duplicate(
                profile_id, str(body["name"]),
                expected_generation=int(body["expected_generation"]),
            )
            entry = next(item for item in services.profiles.list_profiles()
                         if item.profile_id == revision.profile_id)
            return JSONResponse({"profile": profile_state(entry)}, status_code=201)
        except (ValueError, KeyError, TypeError, ConfigurationInvalid, RevisionConflict) as exc:
            if isinstance(exc, (ConfigurationInvalid, RevisionConflict)):
                return profile_error(exc)
            return JSONResponse({"error": "Profilo non valido."}, status_code=422)

    @app.delete("/api/profiles/{profile_id}")
    async def delete_profile(profile_id: int, request: Request):
        current, error = admin_or_error(request)
        if error:
            return error
        if not csrf_ok(current, request):
            return JSONResponse({"error": "csrf"}, status_code=403)
        try:
            body = await request.json()
            services.profiles.delete(profile_id, expected_generation=int(body["expected_generation"]))
            return {"deleted": True, "id": profile_id}
        except (ValueError, KeyError, TypeError, ConfigurationInvalid, RevisionConflict) as exc:
            if isinstance(exc, (ConfigurationInvalid, RevisionConflict)):
                return JSONResponse({"error": str(exc)}, status_code=409)
            return JSONResponse({"error": "Richiesta non valida."}, status_code=422)

    @app.post("/api/profiles/{profile_id}/role")
    async def assign_profile_role(profile_id: int, request: Request):
        current, error = admin_or_error(request)
        if error:
            return error
        if not csrf_ok(current, request):
            return JSONResponse({"error": "csrf"}, status_code=403)
        try:
            body = await request.json()
            role = {"main": "primary", "fallback": "fallback"}.get(body.get("role"))
            if role is None:
                raise ValueError("Role is invalid")
            confirm_unverified = body.get("confirm_unverified")
            if not isinstance(confirm_unverified, bool):
                return JSONResponse({"error": "La conferma deve essere booleana."}, status_code=422)
            if role == "primary":
                prereq = services.readiness()
                if not (prereq.web_admin and prereq.telegram_valid and prereq.owner):
                    return JSONResponse({"error": "Completa prima la configurazione web e Telegram."}, status_code=409)
                draft = services.profiles.get_draft(profile_id)
                revision = draft.revision
                if revision is None:
                    return JSONResponse({"error": "Il profilo non ha una revisione salvata."}, status_code=422)
            else:
                draft = services.profiles.get_draft(profile_id)
                revision = draft.revision
                if revision is None:
                    return JSONResponse({"error": "Il profilo non ha una revisione salvata."}, status_code=422)
            services.profiles.assign_role(
                role, profile_id, revision,
                expected_generation=int(body["expected_generation"]),
                require_valid_receipt=(role == "primary" and not confirm_unverified),
            )
            entry = next(item for item in services.profiles.list_profiles()
                         if item.profile_id == profile_id)
            return {"profile": profile_state(entry),
                    "roles_generation": services.profiles.roles_generation()}
        except StopIteration:
            return JSONResponse({"error": "Profilo non trovato."}, status_code=404)
        except (ValueError, KeyError, TypeError, ConfigurationInvalid, RevisionConflict, TestRequired) as exc:
            if isinstance(exc, TestRequired):
                return JSONResponse({"error": str(exc)}, status_code=409)
            if isinstance(exc, (ConfigurationInvalid, RevisionConflict)):
                return profile_error(exc)
            return JSONResponse({"error": "Richiesta non valida."}, status_code=422)

    @app.post("/api/profiles/roles/fallback/clear")
    async def clear_fallback_role(request: Request):
        current, error = admin_or_error(request)
        if error:
            return error
        if not csrf_ok(current, request):
            return JSONResponse({"error": "csrf"}, status_code=403)
        try:
            body = await request.json()
            services.profiles.assign_role(
                "fallback", None, None,
                expected_generation=int(body["expected_generation"]),
            )
            return {"roles_generation": services.profiles.roles_generation()}
        except (ValueError, KeyError, TypeError) as exc:
            return JSONResponse({"error": "Richiesta non valida."}, status_code=422)
        except (ConfigurationInvalid, RevisionConflict) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)

    @app.post("/api/profiles/roles/primary/restore")
    async def restore_primary_role(request: Request):
        current, error = admin_or_error(request)
        if error:
            return error
        if not csrf_ok(current, request):
            return JSONResponse({"error": "csrf"}, status_code=403)
        prereq = services.readiness()
        if not (prereq.web_admin and prereq.telegram_valid and prereq.owner):
            return JSONResponse({"error": "Completa prima la configurazione web e Telegram."}, status_code=409)
        try:
            body = await request.json()
            previous = services.profiles.restore_previous_primary(
                expected_generation=int(body["expected_generation"]),
            )
            return {"restored": previous is not None,
                    "roles_generation": services.profiles.roles_generation()}
        except (ValueError, KeyError, TypeError) as exc:
            return JSONResponse({"error": "Richiesta non valida."}, status_code=422)
        except (ConfigurationInvalid, RevisionConflict) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)

    @app.post("/api/profiles/{profile_id}/test")
    async def test_profile(profile_id: int, request: Request):
        current, error = admin_or_error(request)
        if error:
            return error
        if not csrf_ok(current, request):
            return JSONResponse({"error": "csrf"}, status_code=403)
        request_limit = 20 * 1024 * 1024 + 256 * 1024
        try:
            if int(request.headers.get("content-length", "0")) > request_limit:
                return JSONResponse({"error": "Richiesta audio oltre il limite consentito."}, status_code=413)
        except ValueError:
            return JSONResponse({"error": "Richiesta non valida."}, status_code=422)
        original_receive = request._receive
        received = 0

        async def limited_receive():
            nonlocal received
            message = await original_receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > request_limit:
                    raise _UploadTooLarge("Multipart request exceeded the global upload limit")
            return message

        request._receive = limited_receive
        try:
            form = await request.form(max_files=1, max_fields=5)
        except _UploadTooLarge:
            return JSONResponse({"error": "Richiesta audio oltre il limite consentito."}, status_code=413)
        except Exception:
            return JSONResponse({"error": "Richiesta multipart non valida."}, status_code=422)
        finally:
            request._receive = original_receive
        path = None
        try:
            try:
                revision_number = int(form.get("revision", ""))
                source = form.get("source")
            except (ValueError, TypeError):
                return JSONResponse({"error": "Revisione non valida."}, status_code=422)
            try:
                draft = services.profiles.get_draft(profile_id)
            except ConfigurationInvalid:
                return JSONResponse({"error": "Profilo o revisione non trovato."}, status_code=404)
            if draft.revision != revision_number:
                return JSONResponse({"error": "Il profilo è cambiato. Ricaricalo prima della prova."}, status_code=409)
            try:
                revision = services.profiles.get_revision(profile_id, revision_number)
            except ConfigurationInvalid:
                return JSONResponse({"error": "Profilo o revisione non trovato."}, status_code=404)
            if source == "sample":
                try:
                    view = await services.transcription_tests.run_profile_bundled_sample(
                        profile_id, revision_number, settings.data_dir / "tmp"
                    )
                except RuntimeError:
                    return JSONResponse({"error": "È già in corso una prova."}, status_code=409)
                except RevisionConflict as exc:
                    return JSONResponse({"error": str(exc)}, status_code=409)
                except ConfigurationInvalid as exc:
                    return JSONResponse({"error": str(exc)}, status_code=422)
                return test_payload(view)
            if source != "upload":
                return JSONResponse({"error": "Sorgente di prova non valida."}, status_code=422)
            audio = form.get("file")
            if not audio or not hasattr(audio, "read") or not hasattr(audio, "filename"):
                return JSONResponse({"error": "Seleziona un file audio."}, status_code=422)
            maximum = min(revision.values.limits.max_file_size_mb, 20) * 1024 * 1024
            try:
                declared_size = int(request.headers.get("content-length", "0"))
            except ValueError:
                declared_size = maximum + 1
            if declared_size > maximum + 256 * 1024:
                return JSONResponse({"error": "File audio oltre il limite configurato."}, status_code=413)
            suffix = Path(audio.filename or "").suffix.lower()
            if suffix not in {".mp3", ".wav", ".m4a", ".ogg", ".oga", ".webm", ".mp4", ".flac"}:
                return JSONResponse({"error": "Formato audio non supportato."}, status_code=422)
            temp_dir = (settings.data_dir / "tmp").resolve()
            temp_dir.mkdir(parents=True, exist_ok=True)
            path = temp_dir / f"web-profile-test-{secrets.token_hex(16)}{suffix}"
            total = 0
            with path.open("xb") as output:
                while chunk := await audio.read(1024 * 1024):
                    total += len(chunk)
                    if total > maximum:
                        return JSONResponse({"error": "File audio oltre il limite configurato."}, status_code=413)
                    output.write(chunk)
            if total == 0:
                return JSONResponse({"error": "Il file è vuoto."}, status_code=422)
            try:
                view = await services.transcription_tests.run_profile(profile_id, revision_number, path)
            except RuntimeError:
                return JSONResponse({"error": "È già in corso una prova."}, status_code=409)
            except RevisionConflict as exc:
                return JSONResponse({"error": str(exc)}, status_code=409)
            return test_payload(view)
        finally:
            if path is not None:
                path.unlink(missing_ok=True)
            await form.close()

    def credential_status():
        result = {}
        with repo.read() as connection:
            for provider in ("openai", "gemini", "openrouter"):
                rows = connection.execute(
                    "SELECT c.version, s.setup_value FROM credentials c LEFT JOIN setup_state s "
                    "ON s.setup_key = ('saved_key:' || c.service || ':' || c.version) "
                    "WHERE c.service = ? ORDER BY c.version", (provider,),
                ).fetchall()
                keys = [{"version": int(row[0]), "label": row[1] or f"Chiave {row[0]}"} for row in rows]
                version = keys[-1]["version"] if keys else None
                result[provider] = {"configured": bool(keys), "credential_version": version, "keys": keys}
        return result

    @app.get("/api/transcription/draft")
    async def get_transcription_draft(request: Request):
        _, error = admin_or_error(request)
        if error:
            return error
        return JSONResponse({"error": "Usa il catalogo dei profili."}, status_code=410)

    @app.put("/api/transcription/draft")
    async def put_transcription_draft(request: Request):
        current, error = admin_or_error(request)
        if error:
            return error
        if not csrf_ok(current, request):
            return JSONResponse({"error": "csrf"}, status_code=403)
        return JSONResponse({"error": "Usa il catalogo dei profili."}, status_code=410)

    @app.post("/api/transcription/credentials")
    async def put_transcription_credential(request: Request):
        current, error = admin_or_error(request)
        if error: return error
        if not csrf_ok(current, request): return JSONResponse({"error": "csrf"}, status_code=403)
        try:
            body = await request.json()
            service = body.get("service")
            if service not in {"openai", "gemini", "openrouter"}:
                raise ValueError("Servizio non valido.")
            credential = body.get("credential", "")
            label = body.get("label", "")
            if not isinstance(credential, str) or not isinstance(label, str):
                raise ValueError("Chiave o nome non validi.")
            label = label.strip()
            if len(label) > 80 or (credential and credential in label):
                raise ValueError("Scegli un nome breve che non contenga la chiave.")
            version = repo.put_credential_with_state(service, credential,
                                                     f"{service}_credential_version", saved_label=label)
            return {"service": service, "credential_version": version, "configured": True,
                    "keys": credential_status()[service]["keys"]}
        except (ValueError, ConfigurationInvalid) as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    @app.put("/api/transcription/credentials/{service}/{version}/label")
    async def rename_transcription_credential(service: str, version: int, request: Request):
        current, error = admin_or_error(request)
        if error: return error
        if not csrf_ok(current, request): return JSONResponse({"error": "csrf"}, status_code=403)
        try:
            body = await request.json()
            label = body.get("label")
            if not isinstance(label, str):
                raise ValueError("Inserisci un nome valido.")
            repo.rename_saved_credential(service, version, label.strip())
            return {"service": service, "credential_version": version,
                    "keys": credential_status()[service]["keys"]}
        except LookupError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except (ValueError, TypeError, AttributeError, ConfigurationInvalid) as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    @app.get("/api/transcription/models")
    async def transcription_models(request: Request, service: str, role: str, credential_version: int):
        _, error = admin_or_error(request)
        if error: return error
        if service not in {"openai", "gemini", "openrouter"} or role not in {"multimodal", "transcription", "refinement"}:
            return JSONResponse({"error": "Selezione servizio o ruolo non valida."}, status_code=422)
        try:
            models = await CatalogService(services.pool).choices(service, credential_version, role)
            return {"models": [{"id": item.id, "label": item.label,
                                "pricing": item.pricing, "context_length": item.context_length}
                               for item in models]}
        except Exception:
            return JSONResponse({"error": "Catalogo non disponibile. Inserisci un ID modello manualmente."}, status_code=503)

    @app.post("/api/transcription/test")
    async def test_transcription(request: Request):
        current, error = admin_or_error(request)
        if error: return error
        if not csrf_ok(current, request): return JSONResponse({"error": "csrf"}, status_code=403)
        return JSONResponse({"error": "Usa il test legato a un profilo."}, status_code=410)
        draft = services.configuration.draft()
        if draft is None: return JSONResponse({"error": "Salva prima una bozza."}, status_code=409)
        maximum = min(draft.values.limits.max_file_size_mb, 20) * 1024 * 1024
        try:
            declared_size = int(request.headers.get("content-length", "0"))
        except ValueError:
            declared_size = maximum + 1
        if declared_size > maximum + 256 * 1024:
            return JSONResponse({"error": f"File oltre il limite di {maximum // (1024 * 1024)} MB."}, status_code=413)
        original_receive = request._receive
        received = 0
        body_limit = maximum + 256 * 1024

        async def limited_receive():
            nonlocal received
            message = await original_receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > body_limit:
                    raise _UploadTooLarge("Multipart request exceeded the configured audio limit")
            return message

        request._receive = limited_receive
        try:
            form = await request.form()
        except _UploadTooLarge:
            return JSONResponse({"error": f"File oltre il limite di {maximum // (1024 * 1024)} MB."}, status_code=413)
        finally:
            request._receive = original_receive
        try:
            try: revision_number = int(form.get("revision", ""))
            except (ValueError, TypeError): return JSONResponse({"error": "Revisione non valida."}, status_code=422)
            if revision_number != draft.number:
                return JSONResponse({"error": "La bozza è cambiata. Ricaricala prima della prova."}, status_code=409)
            audio = form.get("audio")
            if not audio or not hasattr(audio, "read") or not hasattr(audio, "filename"):
                return JSONResponse({"error": "Seleziona un file audio."}, status_code=422)
            suffix = Path(audio.filename or "").suffix.lower()
            if suffix not in {".mp3", ".wav", ".m4a", ".ogg", ".oga", ".webm", ".mp4", ".flac"}:
                return JSONResponse({"error": "Formato audio non supportato."}, status_code=422)
            temp_dir = (settings.data_dir / "tmp").resolve()
            temp_dir.mkdir(parents=True, exist_ok=True)
            path = temp_dir / f"web-test-{secrets.token_hex(16)}{suffix}"
            if path.parent != temp_dir:
                return JSONResponse({"error": "Percorso temporaneo non valido."}, status_code=500)
            total = 0
            try:
                with path.open("xb") as output:
                    while chunk := await audio.read(1024 * 1024):
                        total += len(chunk)
                        if total > maximum:
                            return JSONResponse({"error": f"File oltre il limite di {maximum // (1024 * 1024)} MB."}, status_code=413)
                        output.write(chunk)
                if total == 0: return JSONResponse({"error": "Il file è vuoto."}, status_code=422)
                try:
                    test_started = time.monotonic()
                    view = await services.transcription_tests.run(revision_number, path)
                except RuntimeError:
                    return JSONResponse({"error": "È già in corso una prova."}, status_code=409)
                except RevisionConflict as exc:
                    return JSONResponse({"error": str(exc)}, status_code=409)
                result = view.result
                try:
                    services.diagnostics.record(DiagnosticEvent(
                        datetime.now(timezone.utc), "configuration",
                        "success" if view.status == "succeeded" else "error",
                        revision_number, result.models if result else (),
                        int(max(0, time.monotonic() - test_started) * 1000),
                        None if view.status == "succeeded" else "provider",
                    ))
                except Exception:
                    logger.info("Could not persist a normalized V2 diagnostic event")
                return test_payload(view)
            finally:
                path.unlink(missing_ok=True)
        finally:
            await form.close()

    @app.get("/api/transcription/tests/{test_id}")
    async def get_transcription_test(test_id: str, request: Request):
        _, error = admin_or_error(request)
        if error: return error
        view = services.transcription_tests.result(test_id)
        return test_payload(view) if view else JSONResponse({"error": "Prova scaduta o non trovata."}, status_code=404)

    @app.post("/api/transcription/activate")
    async def activate_transcription(request: Request):
        current, error = admin_or_error(request)
        if error: return error
        if not csrf_ok(current, request): return JSONResponse({"error": "csrf"}, status_code=403)
        return JSONResponse({"error": "Assegna un profilo dal catalogo."}, status_code=410)

    @app.post("/api/transcription/restore")
    async def restore_transcription(request: Request):
        current, error = admin_or_error(request)
        if error: return error
        if not csrf_ok(current, request): return JSONResponse({"error": "csrf"}, status_code=403)
        return JSONResponse({"error": "Assegna un profilo dal catalogo."}, status_code=410)

    @app.get("/admin/transcription", response_class=HTMLResponse)
    async def transcription_page(request: Request):
        if not session(request): return RedirectResponse("/login", status_code=303)
        return page(request, "transcription.html", {"draft": None,
                    "active": None, "credentials": credential_status(),
                    "csrf_token": session(request)["csrf_token"]})

    @app.get("/admin/dashboard", response_class=HTMLResponse)
    async def admin_dashboard(request: Request):
        if not session(request): return RedirectResponse("/login", status_code=303)
        return RedirectResponse("/admin/transcription", status_code=303)

    return app

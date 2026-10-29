from datetime import datetime, timezone

import httpx

from bot.deployment import DeploymentSettings
from bot.services import build_services
from bot.web.auth import hash_password
from bot.web.v2_app import create_app
from bot.transcription.fallback import FallbackEvent


async def test_diagnostics_page_and_api_require_admin_and_show_safe_health(tmp_path):
    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-admin-password"))
    app = create_app(settings, services)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/admin/diagnostics")).status_code == 303
        assert (await client.get("/api/diagnostics")).status_code == 401
        await client.post("/api/login", json={"password": "synthetic-admin-password"})
        page = await client.get("/admin/diagnostics")
        assert page.status_code == 200
        assert "Configurazione attiva" in page.text
        api = await client.get("/api/diagnostics")
        assert api.status_code == 200
        assert api.json()["health"] == {
            "configured": False, "running": False, "verified": False, "receiving": False,
        }
        assert "synthetic-admin-password" not in api.text
    services.repo.close()


async def test_diagnostics_exposes_only_normalized_fallback_metadata(tmp_path):
    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-admin-password"))
    services.diagnostics.record_fallback(FallbackEvent(
        datetime.now(timezone.utc), 3, 8, "refinement", "provider_server_error", "failed",
    ))
    services.diagnostics.record_fallback(FallbackEvent(
        datetime.now(timezone.utc), 4, None, None, None, "primary_succeeded",
    ))
    app = create_app(settings, services)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/login", json={"password": "synthetic-admin-password"})
        page = await client.get("/admin/diagnostics")
        assert "Profilo principale: 4" in page.text
        assert "Esito: primary_succeeded" in page.text
        assert "Profilo principale: 3" in page.text
        assert "Profilo fallback: 8" in page.text
        assert "Fase: refinement" in page.text
        assert "Categoria: provider_server_error" in page.text
        assert "Esito: failed" in page.text
        api = await client.get("/api/diagnostics")
        events = api.json()["events"]
        primary_event = next(item for item in events if item["primary_profile_id"] == 4)
        assert primary_event["fallback_profile_id"] is None
        assert primary_event["outcome"] == "primary_succeeded"
        event = next(item for item in events if item["primary_profile_id"] == 3)
        assert event["primary_profile_id"] == 3
        assert event["fallback_profile_id"] == 8
        assert event["failure_stage"] == "refinement"
        assert event["failure_category"] == "provider_server_error"
        assert event["outcome"] == "failed"
        assert "synthetic-admin-password" not in page.text + api.text
        assert "system prompt" not in page.text + api.text
    services.repo.close()

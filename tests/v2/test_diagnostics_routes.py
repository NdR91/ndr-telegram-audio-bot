import httpx

from bot.deployment import DeploymentSettings
from bot.services import build_services
from bot.web.auth import hash_password
from bot.web.v2_app import create_app


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

"""Offline ASGI checks for V2 web startup and protected mutations."""

import httpx

from bot.deployment import DeploymentSettings
from bot.database.secret_store import SecretStore
from bot.services import build_services
from bot.web.auth import hash_password
from bot.web.v2_app import create_app


async def test_blank_install_starts_web_without_external_calls(tmp_path):
    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    app = create_app(settings, services)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/setup")
            assert response.status_code == 200
            assert "Telegram Audio Bot V2" in app.title
            assert services.repo.get_setup_state("web_admin_hash") is None
            assert services.runtime.is_running is False
            assert services.pool._entries == {}
    services.repo.close()


async def test_failed_saved_telegram_poller_does_not_block_web_lifespan(tmp_path, monkeypatch):
    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.put_credential_with_state("telegram", "synthetic-token", "telegram_credential_version")
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-admin-password"))

    async def fail_start():
        raise RuntimeError("synthetic polling startup failure")

    monkeypatch.setattr(services.runtime, "start_async", fail_start)
    app = create_app(settings, services)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/login")
            assert response.status_code == 200
            assert services.repo.get_setup_state("telegram_credential_version")
    services.repo.close()


async def test_login_and_mutations_require_session_and_csrf(tmp_path):
    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    app = create_app(settings, services)
    app.state.services.repo.set_setup_state("web_admin_hash", __import__("bot.web.auth", fromlist=["hash_password"]).hash_password("synthetic-password-123"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.post("/api/access/invites", json={"kind": "owner"})
        assert denied.status_code == 401
        response = await client.post("/api/login", json={"password": "synthetic-password-123"})
        assert response.status_code == 200
        denied = await client.post("/api/access/invites", json={"kind": "owner"})
        assert denied.status_code == 403
    services.repo.close()


async def test_login_attempts_are_limited_by_socket_ip_not_forwarded_headers(tmp_path, monkeypatch):
    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", "synthetic-hash")
    app = create_app(settings, services)
    monkeypatch.setattr("bot.web.v2_app.verify_password", lambda _password, _hash: False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for attempt in range(5):
            response = await client.post("/api/login", json={"password": "wrong"},
                                         headers={"x-forwarded-for": f"198.51.100.{attempt + 1}"})
            assert response.status_code == 401
        limited = await client.post("/api/login", json={"password": "wrong"},
                                    headers={"x-forwarded-for": "203.0.113.99"})
        assert limited.status_code == 429
        assert 1 <= int(limited.headers["retry-after"]) <= 900
    services.repo.close()


async def test_setup_code_is_checked_before_password_hashing_and_attempts_are_limited(tmp_path, monkeypatch):
    from bot.v2_setup_code import issue_setup_code

    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    issue_setup_code(services.repo)
    app = create_app(settings, services)

    def unexpected_hash(_password):
        raise AssertionError("invalid setup code must be rejected before bcrypt")

    monkeypatch.setattr("bot.web.v2_app.hash_password", unexpected_hash)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(5):
            response = await client.post("/api/setup/admin", json={"code": "wrong", "password": "synthetic-password"})
            assert response.status_code == 401
        limited = await client.post("/api/setup/admin", json={"code": "wrong", "password": "synthetic-password"})
        assert limited.status_code == 429
        assert 1 <= int(limited.headers["retry-after"]) <= 900
    services.repo.close()


async def test_recovery_code_is_checked_before_password_hashing_and_attempts_are_limited(tmp_path, monkeypatch):
    from bot.v2_recovery import issue_recovery_code

    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", "synthetic-admin-hash")
    issue_recovery_code(services.repo)
    app = create_app(settings, services)

    def unexpected_hash(_password):
        raise AssertionError("invalid recovery code must be rejected before bcrypt")

    monkeypatch.setattr("bot.v2_recovery.hash_password", unexpected_hash)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(5):
            response = await client.post("/api/recovery/reset", json={"code": "wrong", "password": "synthetic-password"})
            assert response.status_code == 401
        limited = await client.post("/api/recovery/reset", json={"code": "wrong", "password": "synthetic-password"})
        assert limited.status_code == 429
        assert 1 <= int(limited.headers["retry-after"]) <= 900
    services.repo.close()


async def test_missing_cipher_keeps_setup_web_available(tmp_path, monkeypatch):
    from bot.transcription.errors import ConfigurationInvalid

    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "missing.key")

    def fail_initialize(self):
        raise OSError("synthetic key failure")

    monkeypatch.setattr(SecretStore, "initialize", fail_initialize)
    services = build_services(settings)
    app = create_app(settings, services)
    try:
        services.repo.put_credential("telegram", "synthetic-token")
        raise AssertionError("credential write unexpectedly succeeded without encryption")
    except ConfigurationInvalid:
        pass
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/setup")
        assert response.status_code == 200
        response = await client.post("/api/setup/telegram", json={"token": "synthetic-token"})
        assert response.status_code == 401
    services.repo.close()


async def test_recovery_requires_a_valid_single_use_local_code(tmp_path):
    from bot.v2_recovery import issue_recovery_code
    from bot.web.auth import hash_password

    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-old-password"))
    code = issue_recovery_code(services.repo)
    app = create_app(settings, services)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first_login = await client.post("/api/login", json={"password": "synthetic-old-password"})
        assert first_login.status_code == 200
        denied = await client.post("/api/recovery/reset", json={"code": "wrong-code", "password": "synthetic-new-password"})
        assert denied.status_code == 401
        reset = await client.post("/api/recovery/reset", json={"code": code, "password": "synthetic-new-password"})
        assert reset.status_code == 200
        reused = await client.post("/api/recovery/reset", json={"code": code, "password": "synthetic-other-password"})
        assert reused.status_code == 401
        stale_session = await client.post("/api/access/invites", json={"kind": "owner"})
        assert stale_session.status_code == 401
        old_login = await client.post("/api/login", json={"password": "synthetic-old-password"})
        new_login = await client.post("/api/login", json={"password": "synthetic-new-password"})
        assert old_login.status_code == 401
        assert new_login.status_code == 200
    services.repo.close()


async def test_short_password_is_accepted_for_setup_and_recovery(tmp_path):
    from bot.v2_recovery import issue_recovery_code
    from bot.v2_setup_code import issue_setup_code

    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    code = issue_setup_code(services.repo)
    app = create_app(settings, services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        setup_page = await client.get('/setup')
        recovery_page = await client.get('/recovery')
        assert 'minlength="12"' not in setup_page.text
        assert 'minlength="12"' not in recovery_page.text
        empty = await client.post("/api/setup/admin", json={"code": code, "password": ""})
        assert empty.status_code == 422
        setup = await client.post("/api/setup/admin", json={"code": code, "password": "abc"})
        assert setup.status_code == 200
        recovery_code = issue_recovery_code(services.repo)
        reset = await client.post("/api/recovery/reset", json={"code": recovery_code, "password": "123"})
        assert reset.status_code == 200
        login = await client.post("/api/login", json={"password": "123"})
        assert login.status_code == 200
    services.repo.close()


async def test_setup_code_can_be_renewed_without_logging_and_is_single_use(tmp_path, caplog):
    from datetime import datetime, timedelta, timezone

    from bot.v2_setup_code import issue_setup_code

    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    now = datetime.now(timezone.utc)
    expired = issue_setup_code(services.repo, now - timedelta(minutes=31))
    app = create_app(settings, services)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/setup")
        assert page.status_code == 200
        assert "v2_setup_code" in page.text
        assert expired not in caplog.text
        failed = await client.post("/api/setup/admin", json={"code": expired, "password": "synthetic-password"})
        assert failed.status_code == 401
        renewed = issue_setup_code(services.repo, now)
        accepted = await client.post("/api/setup/admin", json={"code": renewed, "password": "synthetic-password"})
        assert accepted.status_code == 200
        replay = await client.post("/api/setup/admin", json={"code": renewed, "password": "another-synthetic"})
        assert replay.status_code == 409
    services.repo.close()


async def test_invite_candidate_needs_authenticated_csrf_confirmation(tmp_path):
    from bot.web.auth import hash_password

    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("current_bot_id", "1234")
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-admin-password"))
    invite = services.pairing.issue("owner", "setupbot")
    nonce = invite.link.split("pair_", 1)[1]
    services.pairing.observe(nonce, 987, 987, "private", "Synthetic Owner")
    app = create_app(settings, services)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        unauthorized = await client.get(f"/api/access/invites/{invite.id}")
        assert unauthorized.status_code == 401
        login = await client.post("/api/login", json={"password": "synthetic-admin-password"})
        csrf = login.json()["csrf_token"]
        proposed = await client.get(f"/api/access/invites/{invite.id}")
        assert proposed.json()["candidate_id"] == 987
        forbidden = await client.post(f"/api/access/invites/{invite.id}/confirm")
        assert forbidden.status_code == 403
        assert not services.access.has_owner()
        confirmed = await client.post(f"/api/access/invites/{invite.id}/confirm", headers={"x-csrf-token": csrf})
        assert confirmed.status_code == 200
        assert services.access.has_owner()
    services.repo.close()

"""Offline ASGI checks for V2 web startup and protected mutations."""

import httpx

from bot.deployment import DeploymentSettings
from bot.database.secret_store import SecretStore
from bot.services import build_services
from bot.web.auth import hash_password, verify_password
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


async def test_password_change_requires_session_csrf_and_current_password(tmp_path):
    from bot.web.auth import hash_password

    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    old_hash = hash_password("synthetic-old-password")
    services.repo.set_setup_state("web_admin_hash", old_hash)
    app = create_app(settings, services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        unauthenticated = await client.post("/api/password/change", json={
            "current_password": "synthetic-old-password",
            "new_password": "synthetic-new-password",
            "confirm_password": "synthetic-new-password",
        })
        assert unauthenticated.status_code == 401
        login = await client.post("/api/login", json={"password": "synthetic-old-password"})
        assert login.status_code == 200
        missing_csrf = await client.post("/api/password/change", json={
            "current_password": "synthetic-old-password",
            "new_password": "synthetic-new-password",
            "confirm_password": "synthetic-new-password",
        })
        assert missing_csrf.status_code == 403
        wrong_current = await client.post("/api/password/change", headers={
            "x-csrf-token": login.json()["csrf_token"],
        }, json={
            "current_password": "wrong-current-password",
            "new_password": "synthetic-new-password",
            "confirm_password": "synthetic-new-password",
        })
        assert wrong_current.status_code == 401
        assert services.repo.get_setup_state("web_admin_hash") == old_hash
    services.repo.close()


async def test_password_change_rotates_password_and_invalidates_old_session(tmp_path):
    from bot.web.auth import hash_password

    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-old-password"))
    app = create_app(settings, services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post("/api/login", json={"password": "synthetic-old-password"})
        old_cookie = client.cookies.get("session")
        changed = await client.post("/api/password/change", headers={
            "x-csrf-token": login.json()["csrf_token"],
        }, json={
            "current_password": "synthetic-old-password",
            "new_password": "synthetic-new-password",
            "confirm_password": "synthetic-new-password",
        })
        assert changed.status_code == 200
        assert changed.json()["ok"] is True
        assert changed.json()["csrf_token"]
        assert client.cookies.get("session") != old_cookie
        assert verify_password("synthetic-new-password", services.repo.get_setup_state("web_admin_hash"))
        assert not verify_password("synthetic-old-password", services.repo.get_setup_state("web_admin_hash"))
        client.cookies.set("session", old_cookie)
        stale = await client.get("/admin/access")
        assert stale.status_code == 303
        client.cookies.delete("session")
        old_login = await client.post("/api/login", json={"password": "synthetic-old-password"})
        new_login = await client.post("/api/login", json={"password": "synthetic-new-password"})
        assert old_login.status_code == 401
        assert new_login.status_code == 200
    services.repo.close()


async def test_password_change_keeps_password_and_session_when_secret_store_fails(tmp_path, monkeypatch):
    from bot.web.auth import hash_password

    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    old_hash = hash_password("synthetic-old-password")
    services.repo.set_setup_state("web_admin_hash", old_hash)
    app = create_app(settings, services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post("/api/login", json={"password": "synthetic-old-password"})
        old_cookie = client.cookies.get("session")
        old_version = services.repo.get_setup_state("web_session_credential_version")
        monkeypatch.setattr(services.repo._secret_store, "encrypt", lambda _secret: (_ for _ in ()).throw(RuntimeError("synthetic store failure")))
        failed = await client.post("/api/password/change", headers={
            "x-csrf-token": login.json()["csrf_token"],
        }, json={
            "current_password": "synthetic-old-password",
            "new_password": "synthetic-new-password",
            "confirm_password": "synthetic-new-password",
        })
        assert failed.status_code == 503
        assert failed.headers.get("set-cookie") is None
        assert client.cookies.get("session") == old_cookie
        assert services.repo.get_setup_state("web_admin_hash") == old_hash
        assert services.repo.get_setup_state("web_session_credential_version") == old_version
    services.repo.close()


async def test_password_change_confirmation_and_empty_password_are_rejected(tmp_path):
    from bot.web.auth import hash_password

    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-old-password"))
    app = create_app(settings, services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post("/api/login", json={"password": "synthetic-old-password"})
        headers = {"x-csrf-token": login.json()["csrf_token"]}
        mismatch = await client.post("/api/password/change", headers=headers, json={
            "current_password": "synthetic-old-password",
            "new_password": "synthetic-new-password",
            "confirm_password": "different-password",
        })
        empty = await client.post("/api/password/change", headers=headers, json={
            "current_password": "synthetic-old-password", "new_password": "", "confirm_password": "",
        })
        assert mismatch.status_code == 422
        assert empty.status_code == 422
        assert verify_password("synthetic-old-password", services.repo.get_setup_state("web_admin_hash"))
    services.repo.close()


async def test_password_change_uses_its_own_attempt_limit(tmp_path, monkeypatch):
    from bot.web.auth import hash_password

    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-old-password"))
    app = create_app(settings, services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post("/api/login", json={"password": "synthetic-old-password"})
        monkeypatch.setattr("bot.web.v2_app.verify_password", lambda _password, _hash: False)
        body = {"current_password": "wrong", "new_password": "new", "confirm_password": "new"}
        headers = {"x-csrf-token": login.json()["csrf_token"]}
        for _ in range(5):
            assert (await client.post("/api/password/change", headers=headers, json=body)).status_code == 401
        limited = await client.post("/api/password/change", headers=headers, json=body)
        assert limited.status_code == 429
        assert 1 <= int(limited.headers["retry-after"]) <= 900
    services.repo.close()


async def test_password_change_handles_malformed_stored_hash_without_details(tmp_path):
    from bot.web.auth import encode_session, hash_password

    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-original-password"))
    app = create_app(settings, services)
    cookie = encode_session(app.state.serialiser, {"admin": True, "csrf_token": "synthetic-csrf"})
    services.repo.set_setup_state("web_admin_hash", "malformed synthetic hash")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", cookies={"session": cookie}) as client:
        response = await client.post("/api/password/change", headers={"x-csrf-token": "synthetic-csrf"}, json={
            "current_password": "synthetic-original-password",
            "new_password": "synthetic-replacement-password",
            "confirm_password": "synthetic-replacement-password",
        })
        assert response.status_code == 401
        assert response.json() == {"error": "invalid_current_password"}
        assert services.repo.get_setup_state("web_admin_hash") == "malformed synthetic hash"
    services.repo.close()


async def test_password_change_rejects_non_string_and_overlong_passwords(tmp_path):
    from bot.web.auth import hash_password

    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-original-password"))
    app = create_app(settings, services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post("/api/login", json={"password": "synthetic-original-password"})
        headers = {"x-csrf-token": login.json()["csrf_token"]}
        for invalid in (None, 42):
            response = await client.post("/api/password/change", headers=headers, json={
                "current_password": "synthetic-original-password",
                "new_password": invalid,
                "confirm_password": invalid,
            })
            assert response.status_code == 422
        invalid_current = await client.post("/api/password/change", headers=headers, json={
            "current_password": None,
            "new_password": "synthetic-new-password",
            "confirm_password": "synthetic-new-password",
        })
        assert invalid_current.status_code == 422
        long_password = "x" * 73
        too_long = await client.post("/api/password/change", headers=headers, json={
            "current_password": "synthetic-original-password",
            "new_password": long_password,
            "confirm_password": long_password,
        })
        assert too_long.status_code == 422
        assert verify_password("synthetic-original-password", services.repo.get_setup_state("web_admin_hash"))
    services.repo.close()


async def test_login_does_not_coerce_non_string_json_passwords(tmp_path):
    from bot.web.auth import hash_password

    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", hash_password("None"))
    app = create_app(settings, services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for value in (None, 42):
            response = await client.post("/api/login", json={"password": value})
            assert response.status_code == 401
        valid = await client.post("/api/login", json={"password": "None"})
        assert valid.status_code == 200
    services.repo.close()


async def test_password_change_keeps_session_when_attempt_counter_clear_fails(tmp_path, monkeypatch):
    from bot.web.auth import hash_password

    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-old-password"))
    app = create_app(settings, services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post("/api/login", json={"password": "synthetic-old-password"})
        monkeypatch.setattr(app.state.auth_attempts, "clear", lambda *_args: (_ for _ in ()).throw(RuntimeError("synthetic limiter failure")))
        changed = await client.post("/api/password/change", headers={
            "x-csrf-token": login.json()["csrf_token"],
        }, json={
            "current_password": "synthetic-old-password",
            "new_password": "synthetic-new-password",
            "confirm_password": "synthetic-new-password",
        })
        assert changed.status_code == 200
        assert (await client.get("/api/diagnostics")).status_code == 200
        assert verify_password("synthetic-new-password", services.repo.get_setup_state("web_admin_hash"))
    services.repo.close()


async def test_recovery_rotates_signer_even_if_attempt_counter_clear_fails(tmp_path, monkeypatch):
    from bot.v2_recovery import issue_recovery_code
    from bot.web.auth import hash_password

    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-old-password"))
    recovery_code = issue_recovery_code(services.repo)
    app = create_app(settings, services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post("/api/login", json={"password": "synthetic-old-password"})
        old_cookie = client.cookies.get("session")
        clear_attempts = app.state.auth_attempts.clear
        def fail_recovery_clear(endpoint, address):
            if endpoint == "recovery":
                raise RuntimeError("synthetic limiter failure")
            return clear_attempts(endpoint, address)
        monkeypatch.setattr(app.state.auth_attempts, "clear", fail_recovery_clear)
        recovered = await client.post("/api/recovery/reset", json={
            "code": recovery_code, "password": "synthetic-recovered-password",
        })
        assert recovered.status_code == 200
        assert recovered.json()["sessions_invalidated"] is True
        assert client.cookies.get("session") == old_cookie
        assert (await client.get("/api/diagnostics")).status_code == 401
        new_login = await client.post("/api/login", json={"password": "synthetic-recovered-password"})
        assert new_login.status_code == 200
        assert (await client.get("/api/diagnostics")).status_code == 200
    services.repo.close()


async def test_access_page_renders_password_change_for_authenticated_admin(tmp_path):
    from bot.web.auth import hash_password

    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-admin-password"))
    app = create_app(settings, services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/admin/access")).status_code == 303
        login = await client.post("/api/login", json={"password": "synthetic-admin-password"})
        page = await client.get("/admin/access")
        assert page.status_code == 200
        assert 'id="password-change"' in page.text
        assert 'id="password-current"' in page.text
        assert 'id="password-new"' in page.text
        assert 'id="password-confirm"' in page.text
        assert "Le altre sessioni verranno chiuse" in page.text
        assert "/static/password_change.js" in page.text
    services.repo.close()


async def test_setup_and_recovery_load_localized_password_errors(tmp_path):
    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    app = create_app(settings, services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        setup = await client.get("/setup")
        recovery = await client.get("/recovery")
        assert '/static/password_errors.js' in setup.text
        assert '/static/password_errors.js' in recovery.text
        assert 'window.v2PasswordError(d.error)' in setup.text
        assert 'window.v2PasswordError(d.error)' in recovery.text
    services.repo.close()


def test_password_rotation_is_atomic_against_concurrent_changes(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from bot.web.auth import hash_password

    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    old_hash = hash_password("synthetic-old-password")
    services.repo.set_setup_state("web_admin_hash", old_hash)
    barrier = Barrier(2)

    def rotate(new_password):
        new_hash = hash_password(new_password)
        barrier.wait()
        return services.repo.rotate_web_admin_password(
            expected_password_hash=old_hash,
            new_password_hash=new_hash,
            new_session_secret=f"synthetic-session-secret-{new_password}",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(rotate, ("synthetic-password-one", "synthetic-password-two")))
    assert sorted(results) == [False, True]
    final_hash = services.repo.get_setup_state("web_admin_hash")
    assert sum(verify_password(value, final_hash) for value in (
        "synthetic-password-one", "synthetic-password-two",
    )) == 1
    services.repo.close()


async def test_recovery_store_failure_leaves_password_and_existing_session_unchanged(tmp_path, monkeypatch):
    from bot.v2_recovery import issue_recovery_code
    from bot.web.auth import hash_password

    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    old_hash = hash_password("synthetic-old-password")
    services.repo.set_setup_state("web_admin_hash", old_hash)
    recovery_code = issue_recovery_code(services.repo)
    app = create_app(settings, services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post("/api/login", json={"password": "synthetic-old-password"})
        old_cookie = client.cookies.get("session")
        old_version = services.repo.get_setup_state("web_session_credential_version")
        monkeypatch.setattr(services.repo._secret_store, "encrypt", lambda _secret: (_ for _ in ()).throw(RuntimeError("synthetic store failure")))
        failed = await client.post("/api/recovery/reset", json={
            "code": recovery_code, "password": "synthetic-new-password",
        })
        assert failed.status_code == 503
        assert services.repo.get_setup_state("web_admin_hash") == old_hash
        assert client.cookies.get("session") == old_cookie
        assert services.repo.get_setup_state("web_session_credential_version") == old_version
        assert (await client.get("/admin/access")).status_code == 200
    services.repo.close()

"""Offline checks for V2 access management and verified group reception."""

from datetime import datetime, timezone

import httpx
from types import SimpleNamespace

from bot.deployment import DeploymentSettings
from bot.services import build_services
from bot.web.auth import hash_password
from bot.web.v2_app import create_app


async def _client(tmp_path, raise_app_exceptions=True):
    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-admin-password"))
    services.repo.set_setup_state("current_bot_id", "1234")
    services.repo.set_setup_state("current_bot_username", "setupbot")
    app = create_app(settings, services)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions),
        base_url="http://test",
    )
    login = await client.post("/api/login", json={"password": "synthetic-admin-password"})
    return client, services, login.json()["csrf_token"]


async def test_access_labels_can_be_renamed_without_changing_authorization(tmp_path):
    client, services, csrf = await _client(tmp_path)
    try:
        services.access.authorize('owner', 11)
        services.access.authorize('user', 22)
        services.access.authorize('group', -33)
        headers = {'x-csrf-token': csrf}
        path = '/api/access/entries/user/22/label'
        anonymous = httpx.AsyncClient(transport=client._transport, base_url='http://test')
        try:
            assert (await anonymous.put(path, json={'label': 'Ada'})).status_code == 401
        finally:
            await anonymous.aclose()
        assert (await client.put(path, json={'label': 'Ada'})).status_code == 403
        assert (await client.put(path, headers=headers, json={'label': 'x' * 81})).status_code == 422
        assert (await client.put(path, headers=headers, json={'label': ''})).status_code == 422
        for kind, entry_id, label in [('owner', 11, 'Proprietario'), ('user', 22, 'Ada'), ('group', -33, 'Famiglia')]:
            response = await client.put(f'/api/access/entries/{kind}/{entry_id}/label', headers=headers, json={'label': label})
            assert response.status_code == 200
        assert (await client.put('/api/access/entries/user/99/label', headers=headers, json={'label': 'X'})).status_code == 404
        listing = (await client.get('/api/access')).json()
        assert listing['labels'] == {'owner': {'11': 'Proprietario'}, 'user': {'22': 'Ada'}, 'group': {'-33': 'Famiglia'}}
        assert services.access.allowed(22, 22, 'private')
        assert services.access.allowed(99, -33, 'supergroup')
        assert services.access.has_owner()
        services.access.remove('user', 22)
        services.access.authorize('user', 22)
        assert '22' not in (await client.get('/api/access')).json()['labels']['user']
    finally:
        await client.aclose()
        services.repo.close()


async def test_guided_and_manual_access_share_acl(tmp_path):
    client, services, csrf = await _client(tmp_path)
    try:
        invite = services.pairing.issue("user", "setupbot")
        nonce = invite.link.split("pair_", 1)[1]
        services.pairing.observe(nonce, 731, 731, "private", "Synthetic User")
        candidate = await client.get(f"/api/access/invites/{invite.id}")
        assert candidate.json()["candidate_id"] == 731
        confirmed = await client.post(f"/api/access/invites/{invite.id}/confirm",
                                      headers={"x-csrf-token": csrf})
        assert confirmed.status_code == 200
        manual = await client.post("/api/access/entries", json={"kind": "group", "entry_id": "-100734"},
                                   headers={"x-csrf-token": csrf})
        assert manual.status_code == 200
        listing = await client.get("/api/access")
        assert listing.json()["owner"] == []
        assert listing.json()["users"] == [731]
        assert listing.json()["groups"] == [{"id": -100734, "last_received": None,
                                               "membership": "unchecked"}]
        assert services.access.allowed(731, 731, "private")
        assert services.access.allowed(999, -100734, "supergroup")

        async def get_chat_member(_chat_id, _user_id):
            return SimpleNamespace(status="member")

        services.runtime._app = SimpleNamespace(bot=SimpleNamespace(get_chat_member=get_chat_member))
        checked = await client.post("/api/access/groups/-100734/membership",
                                    headers={"x-csrf-token": csrf})
        assert checked.json()["membership"] == "member"
        listing = await client.get("/api/access")
        assert listing.json()["groups"][0]["last_received"] is None
    finally:
        await client.aclose()
        services.repo.close()


async def test_web_and_commands_cannot_remove_last_owner(tmp_path):
    client, services, csrf = await _client(tmp_path)
    try:
        services.repo.set_setup_state("current_bot_id", "42")
        services.access.authorize("owner", 42)
        deleted = await client.delete("/api/access/entries/owner/42", headers={"x-csrf-token": csrf})
        assert deleted.status_code == 409
        assert services.access.list_entries()["owner"] == [42]
        from bot.handlers.v2_admin import addgroup

        replies = []

        async def reply_text(message):
            replies.append(message)

        update = SimpleNamespace(effective_message=SimpleNamespace(reply_text=reply_text),
                                 effective_user=SimpleNamespace(id=42),
                                 effective_chat=SimpleNamespace(type="private"))
        context = SimpleNamespace(application=SimpleNamespace(bot_data={"v2_services": services}),
                                  args=["-100420"])
        await addgroup(update, context)
        assert replies == ["Accesso group -100420 aggiunto."]
        assert services.access.allowed(99, -100420, "supergroup")
        page = await client.get("/admin/access")
        assert page.status_code == 200
        assert "Ricezione nei gruppi" in page.text
    finally:
        await client.aclose()
        services.repo.close()


async def test_access_commands_reject_non_owner(tmp_path):
    from bot.handlers.v2_admin import adduser

    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    replies = []

    async def reply_text(message):
        replies.append(message)

    update = SimpleNamespace(effective_message=SimpleNamespace(reply_text=reply_text),
                             effective_user=SimpleNamespace(id=72),
                             effective_chat=SimpleNamespace(type="private"))
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"v2_services": services}),
                              args=["73"])
    await adduser(update, context)
    assert services.access.list_entries()["user"] == []
    assert "Solo un proprietario" in replies[0]
    services.repo.close()


async def test_access_commands_reject_owner_bound_to_previous_bot(tmp_path):
    from bot.handlers.v2_admin import adduser

    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("current_bot_id", "100")
    services.access.authorize("owner", 72)
    services.repo.set_setup_state("current_bot_id", "200")
    replies = []

    async def reply_text(message):
        replies.append(message)

    update = SimpleNamespace(effective_message=SimpleNamespace(reply_text=reply_text),
                             effective_user=SimpleNamespace(id=72),
                             effective_chat=SimpleNamespace(type="private"))
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"v2_services": services}),
                              args=["73"])
    await adduser(update, context)
    assert services.access.list_entries()["user"] == []
    assert "Solo un proprietario" in replies[0]
    services.repo.close()


async def test_access_routes_reject_ids_outside_signed_sqlite_range(tmp_path):
    client, services, csrf = await _client(tmp_path, raise_app_exceptions=False)
    try:
        too_large = "9223372036854775808"
        added = await client.post("/api/access/entries",
                                  json={"kind": "group", "entry_id": too_large},
                                  headers={"x-csrf-token": csrf})
        assert added.status_code == 422
        removed = await client.delete(f"/api/access/entries/group/{too_large}",
                                      headers={"x-csrf-token": csrf})
        assert removed.status_code == 422
        assert services.access.list_entries()["group"] == []
    finally:
        await client.aclose()
        services.repo.close()


async def test_manual_owner_addition_requires_guided_pairing(tmp_path):
    client, services, csrf = await _client(tmp_path)
    try:
        response = await client.post("/api/access/entries",
                                     json={"kind": "owner", "entry_id": 731},
                                     headers={"x-csrf-token": csrf})
        assert response.status_code == 422
        assert services.access.list_entries()["owner"] == []
        page = await client.get("/admin/access")
        advanced = page.text.split('<select id="entry-kind">', 1)[1].split("</select>", 1)[0]
        assert 'value="owner"' not in advanced
    finally:
        await client.aclose()
        services.repo.close()


async def test_access_commands_report_ids_outside_signed_sqlite_range(tmp_path):
    from bot.handlers.v2_admin import addgroup

    client, services, _csrf = await _client(tmp_path)
    try:
        services.repo.set_setup_state("current_bot_id", "42")
        services.access.authorize("owner", 42)
        replies = []

        async def reply_text(message):
            replies.append(message)

        update = SimpleNamespace(effective_message=SimpleNamespace(reply_text=reply_text),
                                 effective_user=SimpleNamespace(id=42),
                                 effective_chat=SimpleNamespace(type="private"))
        context = SimpleNamespace(application=SimpleNamespace(bot_data={"v2_services": services}),
                                  args=["9223372036854775808"])
        await addgroup(update, context)
        assert services.access.list_entries()["group"] == []
        assert replies and "range" in replies[0].lower()
    finally:
        await client.aclose()
        services.repo.close()


async def test_manual_access_requires_auth_csrf_and_integer_id(tmp_path):
    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-admin-password"))
    app = create_app(settings, services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post("/api/access/entries", json={"kind": "user", "entry_id": 731})
        assert denied.status_code == 401
        login = await client.post("/api/login", json={"password": "synthetic-admin-password"})
        denied = await client.post("/api/access/entries", json={"kind": "user", "entry_id": 731})
        assert denied.status_code == 403
        invalid = await client.post("/api/access/entries", json={"kind": "user", "entry_id": "abc"},
                                    headers={"x-csrf-token": login.json()["csrf_token"]})
        assert invalid.status_code == 422
        negative_user = await client.post("/api/access/entries", json={"kind": "user", "entry_id": -7},
                                          headers={"x-csrf-token": login.json()["csrf_token"]})
        assert negative_user.status_code == 422
    services.repo.close()


async def test_invite_confirmation_rejects_csrf_and_wrong_candidate(tmp_path):
    client, services, csrf = await _client(tmp_path)
    try:
        invite = services.pairing.issue("group", "setupbot")
        nonce = invite.link.split("pair_", 1)[1]
        services.pairing.observe(nonce, 91, -10091, "supergroup", "Synthetic Group")
        wrong_id = await client.post(f"/api/access/invites/{invite.id}/confirm",
                                     headers={"x-csrf-token": csrf}, json={"candidate_id": -10092})
        assert wrong_id.status_code == 409
        assert services.access.list_entries()["group"] == []
        no_csrf = await client.post(f"/api/access/invites/{invite.id}/confirm", json={})
        assert no_csrf.status_code == 403
        confirmed = await client.post(f"/api/access/invites/{invite.id}/confirm",
                                      headers={"x-csrf-token": csrf})
        assert confirmed.status_code == 200
        assert services.access.list_entries()["group"] == [-10091]
    finally:
        await client.aclose()
        services.repo.close()


def test_membership_is_not_voice_reception(repo):
    from bot.access import AccessService

    access = AccessService(repo)
    access.authorize("group", -10090)
    listing = access.list_entries_with_status()
    assert listing["group"] == [{"id": -10090, "last_received": None,
                                 "membership": "unchecked"}]


def test_group_voice_marks_reception_without_content_history(repo):
    from bot.access import AccessService

    access = AccessService(repo)
    access.authorize("group", -10090)
    received_at = datetime(2026, 10, 29, 11, 30, tzinfo=timezone.utc)
    access.mark_received(-10090, received_at)
    listing = access.list_entries_with_status()
    assert listing["group"] == [{"id": -10090, "last_received": received_at.isoformat(),
                                 "membership": "unchecked"}]
    with repo.read() as connection:
        keys = {row[0] for row in connection.execute("SELECT setup_key FROM setup_state")}
    assert "access_group_received:-10090" in keys
    assert all("message" not in key and "text" not in key and "audio" not in key for key in keys)

"""Authenticated V2 configuration endpoints use synthetic data only."""

import httpx

from bot.deployment import DeploymentSettings
from bot.services import build_services
from bot.transcription.models import ProcessingResult
from bot.transcription.testing import TestView
from bot.web.auth import hash_password
from bot.web.v2_app import create_app


async def test_renaming_key_preserves_credential_and_configuration(tmp_path):
    from bot.transcription.models import ConfigInput, RuntimeLimits

    settings = DeploymentSettings(tmp_path, tmp_path / 'app-v2.sqlite3', tmp_path / 'key')
    services = build_services(settings)
    services.repo.set_setup_state('web_admin_hash', hash_password('synthetic-admin-password'))
    version = services.repo.put_credential_with_state('openai', 'synthetic-secret', 'openai_credential_version', saved_label='Prima')
    values = ConfigInput('openai', 'multimodal', 'gpt-audio-1.5', None, version, 'Faithful', '{raw_text}', RuntimeLimits())
    profile = services.profiles.create("Rename-safe profile", values)
    app = create_app(settings, services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        path = '/api/transcription/credentials/openai/1/label'
        assert (await client.put(path, json={'label': 'Lavoro'})).status_code == 401
        csrf = (await client.post('/api/login', json={'password': 'synthetic-admin-password'})).json()['csrf_token']
        assert (await client.put(path, json={'label': 'Lavoro'})).status_code == 403
        headers = {'x-csrf-token': csrf}
        assert (await client.put(path, headers=headers, json={'label': 'synthetic-secret'})).status_code == 422
        assert (await client.put(path, headers=headers, json={'label': '   '})).status_code == 422
        assert (await client.put('/api/transcription/credentials/gemini/1/label', headers=headers, json={'label': 'X'})).status_code == 404
        assert (await client.put('/api/transcription/credentials/openai/9223372036854775808/label', headers=headers, json={'label': 'X'})).status_code == 422
        renamed = await client.put(path, headers=headers, json={'label': 'Lavoro'})
        assert renamed.status_code == 200
        assert 'synthetic-secret' not in renamed.text
        assert renamed.json()['keys'] == [{'version': 1, 'label': 'Lavoro'}]
        assert services.repo.read_credential('openai', version) == 'synthetic-secret'
        assert services.profiles.get_revision(profile.profile_id, profile.revision).values == values
        assert services.profiles.list_profiles()[0].verification_state == "not_verified"
    services.repo.close()
    restarted = build_services(settings)
    try:
        assert restarted.repo.get_setup_state('saved_key:openai:1') == 'Lavoro'
    finally:
        restarted.repo.close()


async def test_multiple_saved_keys_are_selectable_without_exposing_secrets(tmp_path):
    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-admin-password"))
    app = create_app(settings, services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        csrf = (await client.post("/api/login", json={"password": "synthetic-admin-password"})).json()["csrf_token"]
        headers = {"x-csrf-token": csrf}
        for label, key in (("Personale", "synthetic-secret-one"), ("Lavoro", "synthetic-secret-two")):
            response = await client.post('/api/transcription/credentials', headers=headers,
                json={"service": "openai", "credential": key, "label": label})
            assert response.status_code == 200
            assert key not in response.text
        page = await client.get('/admin/transcription')
        assert 'Personale' in page.text and 'Lavoro' in page.text
        assert 'synthetic-secret' not in page.text
        services.repo.prune_credentials()
        assert services.repo.credential_versions('openai') == (1, 2)
        values = {"service": "openai", "mode": "multimodal", "audio_model": "gpt-audio-1.5",
                  "text_model": None, "credential_version": 1, "system_prompt": "Faithful",
                  "refine_template": "{raw_text}", "limits": {}}
        saved = await client.post('/api/profiles', json={"name": "Profile old key", "values": values}, headers=headers)
        assert saved.status_code == 201
        profile = saved.json()["profile"]
        assert profile["values"]["credential_version"] == 1
        updated_values = {**values, "credential_version": 2}
        updated = await client.put(f"/api/profiles/{profile['id']}", headers=headers, json={
            "expected_revision": profile["revision"], "expected_generation": profile["generation"],
            "name": profile["name"], "values": updated_values,
        })
        assert updated.status_code == 200
        assert updated.json()["profile"]["values"]["credential_version"] == 2
    services.repo.close()
    restarted = build_services(settings)
    try:
        assert restarted.repo.credential_versions('openai') == (1, 2)
        assert restarted.repo.get_setup_state('saved_key:openai:1') == 'Personale'
        assert restarted.repo.get_setup_state('saved_key:openai:2') == 'Lavoro'
    finally:
        restarted.repo.close()


async def test_legacy_singleton_routes_are_retired_with_auth_and_csrf_checks(tmp_path):
    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-admin-password"))
    services.repo.put_credential("openai", "synthetic-api-key")
    app = create_app(settings, services)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/profiles")).status_code == 401
        login = await client.post("/api/login", json={"password": "synthetic-admin-password"})
        csrf = login.json()["csrf_token"]
        denied = await client.put("/api/transcription/draft", json={})
        assert denied.status_code == 403
        body = {"service": "openai", "mode": "multimodal", "audio_model": "gpt-4o-mini-audio-preview",
                "text_model": None, "credential_version": 1, "system_prompt": "Faithful",
                "refine_template": "{raw_text}", "limits": {"max_file_size_mb": 2}, "expected_revision": None}
        saved = await client.put("/api/transcription/draft", json=body, headers={"x-csrf-token": csrf})
        assert saved.status_code == 410
        catalog = await client.get("/api/profiles")
        assert catalog.status_code == 200
        assert "synthetic-api-key" not in catalog.text
        cred = await client.post("/api/transcription/credentials", json={"service": "openai", "credential": "replacement-synthetic"}, headers={"x-csrf-token": csrf})
        assert cred.status_code == 200
        assert "replacement-synthetic" not in cred.text
        assert "credential" not in cred.json()
    services.repo.close()


async def test_audio_upload_is_limited_removed_and_result_is_session_only(tmp_path):
    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-admin-password"))
    services.repo.put_credential("openai", "synthetic-api-key")
    app = create_app(settings, services)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        csrf = (await client.post("/api/login", json={"password": "synthetic-admin-password"})).json()["csrf_token"]
        body = {"name": "Upload profile", "values": {"service": "openai", "mode": "multimodal", "audio_model": "synthetic-model",
                "text_model": None, "credential_version": 1, "system_prompt": "Faithful",
                "refine_template": "{raw_text}", "limits": {"max_file_size_mb": 1}}}
        profile = (await client.post("/api/profiles", json=body, headers={"x-csrf-token": csrf})).json()["profile"]

        async def fake_run(profile_id, revision, audio):
            assert audio.exists()
            return TestView("synthetic-id", revision, "succeeded",
                ProcessingResult("<script>synthetic</script>", False, ("synthetic-model",), 3),
                profile_id=profile_id)

        services.transcription_tests.run_profile = fake_run
        response = await client.post(f"/api/profiles/{profile['id']}/test",
            data={"revision": str(profile["revision"]), "source": "upload"},
            files={"file": ("sample.wav", b"synthetic audio", "audio/wav")}, headers={"x-csrf-token": csrf})
        assert response.status_code == 200
        assert response.json()["result"]["text"] == "<script>synthetic</script>"
        assert response.json()["profile_id"] == profile["id"]
        assert not list((tmp_path / "tmp").glob("web-profile-test-*"))
    services.repo.close()


async def test_model_catalog_accepts_refinement_role(tmp_path, monkeypatch):
    from bot.transcription.models import ModelChoice
    from bot.web.v2_app import CatalogService

    async def fake_choices(self, service, credential_version, role):
        assert (service, credential_version, role) == ("openai", 1, "refinement")
        return [ModelChoice("synthetic-text", "Synthetic text model", frozenset({"refinement"}), frozenset())]

    monkeypatch.setattr(CatalogService, "choices", fake_choices)
    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-admin-password"))
    app = create_app(settings, services)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/login", json={"password": "synthetic-admin-password"})
        response = await client.get("/api/transcription/models?service=openai&role=refinement&credential_version=1")
        assert response.status_code == 200
        assert response.json()["models"] == [{"id": "synthetic-text", "label": "Synthetic text model",
                                               "pricing": None, "context_length": None}]
    services.repo.close()


async def test_profile_upload_enforces_configured_file_limit(tmp_path, monkeypatch):
    from starlette.formparsers import MultiPartParser

    # Keep this focused test below Starlette's disk-spool threshold so it can
    # run under httpx's in-process ASGI transport without worker-thread I/O.
    monkeypatch.setattr(MultiPartParser, "spool_max_size", 2 * 1024 * 1024)
    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-admin-password"))
    services.repo.put_credential("openai", "synthetic-api-key")
    app = create_app(settings, services)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        csrf = (await client.post("/api/login", json={"password": "synthetic-admin-password"})).json()["csrf_token"]
        body = {"name": "Bounded profile", "values": {"service": "openai", "mode": "multimodal", "audio_model": "synthetic-model",
                "text_model": None, "credential_version": 1, "system_prompt": "Faithful",
                "refine_template": "{raw_text}", "limits": {"max_file_size_mb": 1}}}
        profile = (await client.post("/api/profiles", json=body, headers={"x-csrf-token": csrf})).json()["profile"]
        boundary = "synthetic-boundary"
        head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"revision\"\r\n\r\n"
                f"{profile['revision']}\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"source\"\r\n\r\n"
                f"upload\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"sample.wav\"\r\n"
                "Content-Type: audio/wav\r\n\r\n").encode()
        payload = head + (b"x" * (512 * 1024)) + (b"y" * (800 * 1024)) + f"\r\n--{boundary}--\r\n".encode()
        response = await client.post(f"/api/profiles/{profile['id']}/test", content=payload,
            headers={"content-type": f"multipart/form-data; boundary={boundary}", "x-csrf-token": csrf})
        assert "content-length" in response.request.headers
        assert response.status_code == 413
        assert not list((tmp_path / "tmp").glob("web-profile-test-*"))
    services.repo.close()


async def test_rejected_multipart_uploads_are_closed_on_validation_errors(tmp_path, monkeypatch):
    from starlette.datastructures import UploadFile

    closed = []
    original_close = UploadFile.close

    async def tracked_close(self):
        closed.append(self.filename)
        await original_close(self)

    monkeypatch.setattr(UploadFile, "close", tracked_close)
    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-admin-password"))
    services.repo.put_credential("openai", "synthetic-api-key")
    app = create_app(settings, services)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        csrf = (await client.post("/api/login", json={"password": "synthetic-admin-password"})).json()["csrf_token"]
        body = {"name": "Validation profile", "values": {"service": "openai", "mode": "multimodal", "audio_model": "synthetic-model",
                "text_model": None, "credential_version": 1, "system_prompt": "Faithful",
                "refine_template": "{raw_text}", "limits": {"max_file_size_mb": 1}}}
        profile = (await client.post("/api/profiles", json=body, headers={"x-csrf-token": csrf})).json()["profile"]
        stale = await client.post(f"/api/profiles/{profile['id']}/test",
            data={"revision": "999", "source": "upload"},
            files={"file": ("stale.wav", b"synthetic audio", "audio/wav")}, headers={"x-csrf-token": csrf})
        unsupported = await client.post(f"/api/profiles/{profile['id']}/test",
            data={"revision": str(profile["revision"]), "source": "upload"},
            files={"file": ("unsupported.exe", b"synthetic audio", "application/octet-stream")}, headers={"x-csrf-token": csrf})
        assert stale.status_code == 409
        assert unsupported.status_code == 422
        assert closed == ["stale.wav", "unsupported.exe"]
    services.repo.close()


async def test_profile_revision_can_be_updated_to_a_new_saved_credential(tmp_path):
    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-admin-password"))
    first_version = services.repo.put_credential("openai", "first-synthetic-key")
    app = create_app(settings, services)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        csrf = (await client.post("/api/login", json={"password": "synthetic-admin-password"})).json()["csrf_token"]
        values = {"service": "openai", "mode": "multimodal", "audio_model": "synthetic-model",
                  "text_model": None, "credential_version": first_version, "system_prompt": "Faithful",
                  "refine_template": "{raw_text}", "limits": {}}
        first = (await client.post("/api/profiles", json={"name": "Credential profile", "values": values},
                                   headers={"x-csrf-token": csrf})).json()["profile"]
        credential = await client.post("/api/transcription/credentials", json={
            "service": "openai", "credential": "replacement-synthetic-key",
        }, headers={"x-csrf-token": csrf})
        assert credential.status_code == 200
        page = await client.get("/admin/transcription")
        assert '"version": 2' in page.text
        updated = {**values, "credential_version": 2}
        saved = await client.put(f"/api/profiles/{first['id']}", headers={"x-csrf-token": csrf}, json={
            "expected_revision": first["revision"], "expected_generation": first["generation"],
            "name": first["name"], "values": updated,
        })
        assert saved.status_code == 200
        assert saved.json()["profile"]["values"]["credential_version"] == 2
    services.repo.close()

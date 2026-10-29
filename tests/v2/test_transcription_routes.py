"""Authenticated V2 configuration endpoints use synthetic data only."""

import httpx

from bot.deployment import DeploymentSettings
from bot.services import build_services
from bot.transcription.models import ProcessingResult
from bot.web.auth import hash_password
from bot.web.v2_app import create_app


async def test_routes_require_auth_csrf_and_expected_revision(tmp_path):
    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-admin-password"))
    services.repo.put_credential("openai", "synthetic-api-key")
    app = create_app(settings, services)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/transcription/draft")).status_code == 401
        login = await client.post("/api/login", json={"password": "synthetic-admin-password"})
        csrf = login.json()["csrf_token"]
        denied = await client.put("/api/transcription/draft", json={})
        assert denied.status_code == 403
        body = {"service": "openai", "mode": "multimodal", "audio_model": "gpt-4o-mini-audio-preview",
                "text_model": None, "credential_version": 1, "system_prompt": "Faithful",
                "refine_template": "{raw_text}", "limits": {"max_file_size_mb": 2}, "expected_revision": None}
        saved = await client.put("/api/transcription/draft", json=body, headers={"x-csrf-token": csrf})
        assert saved.status_code == 200
        status = await client.get("/api/transcription/draft")
        assert status.json()["credentials"]["openai"] == {"configured": True, "credential_version": 1}
        assert "synthetic-api-key" not in status.text
        stale = {**body, "expected_revision": 999}
        conflict = await client.put("/api/transcription/draft", json=stale, headers={"x-csrf-token": csrf})
        assert conflict.status_code == 409
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
        body = {"service": "openai", "mode": "multimodal", "audio_model": "synthetic-model",
                "text_model": None, "credential_version": 1, "system_prompt": "Faithful",
                "refine_template": "{raw_text}", "limits": {"max_file_size_mb": 1}, "expected_revision": None}
        draft = (await client.put("/api/transcription/draft", json=body, headers={"x-csrf-token": csrf})).json()["draft"]

        async def fake_run(revision, audio):
            assert audio.exists()
            return type("View", (), {"id": "synthetic-id", "revision": revision, "status": "succeeded",
                    "safe_error": None, "result": ProcessingResult("<script>synthetic</script>", False, ("synthetic-model",), 3)})()

        services.transcription_tests.run = fake_run
        response = await client.post("/api/transcription/test", data={"revision": str(draft["revision"])},
            files={"audio": ("sample.wav", b"synthetic audio", "audio/wav")}, headers={"x-csrf-token": csrf})
        assert response.status_code == 200
        assert response.json()["result"]["text"] == "<script>synthetic</script>"
        assert not list((tmp_path / "tmp").glob("web-test-*"))
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
        assert response.json()["models"] == [{"id": "synthetic-text", "label": "Synthetic text model"}]
    services.repo.close()


async def test_chunked_upload_is_rejected_before_multipart_spooling(tmp_path):
    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-admin-password"))
    services.repo.put_credential("openai", "synthetic-api-key")
    app = create_app(settings, services)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        csrf = (await client.post("/api/login", json={"password": "synthetic-admin-password"})).json()["csrf_token"]
        body = {"service": "openai", "mode": "multimodal", "audio_model": "synthetic-model",
                "text_model": None, "credential_version": 1, "system_prompt": "Faithful",
                "refine_template": "{raw_text}", "limits": {"max_file_size_mb": 1}, "expected_revision": None}
        draft = (await client.put("/api/transcription/draft", json=body, headers={"x-csrf-token": csrf})).json()["draft"]
        boundary = "synthetic-boundary"
        head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"revision\"\r\n\r\n"
                f"{draft['revision']}\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"sample.wav\"\r\n"
                "Content-Type: audio/wav\r\n\r\n").encode()
        consumed = []

        async def chunked_body():
            for part in (head, b"x" * (512 * 1024), b"y" * (800 * 1024),
                         f"\r\n--{boundary}--\r\n".encode()):
                consumed.append(len(part))
                yield part

        response = await client.post("/api/transcription/test", content=chunked_body(),
            headers={"content-type": f"multipart/form-data; boundary={boundary}", "x-csrf-token": csrf})
        assert "content-length" not in response.request.headers
        assert response.status_code == 413
        assert len(consumed) == 3
        assert not list((tmp_path / "tmp").glob("web-test-*"))
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
        body = {"service": "openai", "mode": "multimodal", "audio_model": "synthetic-model",
                "text_model": None, "credential_version": 1, "system_prompt": "Faithful",
                "refine_template": "{raw_text}", "limits": {"max_file_size_mb": 1}, "expected_revision": None}
        draft = (await client.put("/api/transcription/draft", json=body, headers={"x-csrf-token": csrf})).json()["draft"]
        stale = await client.post("/api/transcription/test", data={"revision": "999"},
            files={"audio": ("stale.wav", b"synthetic audio", "audio/wav")}, headers={"x-csrf-token": csrf})
        unsupported = await client.post("/api/transcription/test", data={"revision": str(draft["revision"])},
            files={"audio": ("unsupported.exe", b"synthetic audio", "application/octet-stream")}, headers={"x-csrf-token": csrf})
        assert stale.status_code == 409
        assert unsupported.status_code == 422
        assert closed == ["stale.wav", "unsupported.exe"]
    services.repo.close()


async def test_stale_credential_draft_can_be_saved_with_current_version(tmp_path):
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
                  "refine_template": "{raw_text}", "limits": {}, "expected_revision": None}
        first = (await client.put("/api/transcription/draft", json=values, headers={"x-csrf-token": csrf})).json()["draft"]
        await client.post("/api/transcription/credentials", json={"service": "openai", "credential": "replacement-synthetic-key"}, headers={"x-csrf-token": csrf})
        page = await client.get("/admin/transcription")
        assert '"credential_version": 2' in page.text
        updated = {**values, "expected_revision": first["revision"], "credential_version": 2}
        saved = await client.put("/api/transcription/draft", json=updated, headers={"x-csrf-token": csrf})
        assert saved.status_code == 200
        assert saved.json()["draft"]["credential_version"] == 2
    services.repo.close()

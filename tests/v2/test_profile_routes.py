import httpx
from datetime import datetime, timezone

from bot.deployment import DeploymentSettings
from bot.services import build_services
from bot.transcription.testing import TestView as ProfileTestView
from bot.transcription.configuration import VALIDATOR_VERSION
from bot.transcription.models import TestReceipt
from bot.web.auth import hash_password
from bot.web.v2_app import create_app


def _values(credential_version=1, model="audio-model"):
    return {
        "service": "openai",
        "mode": "multimodal",
        "audio_model": model,
        "text_model": None,
        "credential_version": credential_version,
        "system_prompt": "Keep the transcript faithful.",
        "refine_template": "Correct minimally: {raw_text}",
        "limits": {"max_file_size_mb": 2},
    }


async def _logged_in(tmp_path):
    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-admin-password"))
    services.repo.set_setup_state("telegram_credential_version", "1")
    services.repo.set_setup_state("current_bot_id", "123")
    services.repo.set_setup_state("owner_bot_id", "123")
    services.access.authorize("owner", 987)
    services.repo.put_credential("openai", "synthetic-provider-key")
    app = create_app(settings, services)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )
    response = await client.post("/api/login", json={"password": "synthetic-admin-password"})
    return services, client, {"x-csrf-token": response.json()["csrf_token"]}


async def test_profile_api_requires_auth_and_csrf_and_supports_catalog_roles_and_crud(tmp_path):
    services, client, headers = await _logged_in(tmp_path)
    try:
        assert (await client.get("/api/profiles")).status_code == 200
        await client.post("/api/logout", headers=headers)
        assert (await client.get("/api/profiles")).status_code == 401
        csrf = (await client.post("/api/login", json={"password": "synthetic-admin-password"})).json()["csrf_token"]
        headers = {"x-csrf-token": csrf, "content-type": "application/json"}
        assert (await client.post("/api/profiles", json={"name": "No CSRF", "values": _values()})).status_code == 403

        created = await client.post(
            "/api/profiles", headers=headers, json={"name": "Primario", "values": _values()}
        )
        assert created.status_code == 201
        profile = created.json()["profile"]
        assert profile["id"] > 0
        assert profile["revision"] > 0
        assert profile["verification"] == "unverified"
        assert "synthetic-provider-key" not in created.text

        detail = await client.get(f"/api/profiles/{profile['id']}")
        assert detail.status_code == 200
        assert detail.json()["profile"]["values"]["audio_model"] == "audio-model"

        unconfirmed = await client.post(
            f"/api/profiles/{profile['id']}/role", headers=headers,
            json={"role": "main", "expected_generation": 0, "confirm_unverified": False},
        )
        assert unconfirmed.status_code == 409
        false_string = await client.post(
            f"/api/profiles/{profile['id']}/role", headers=headers,
            json={"role": "main", "expected_generation": 0, "confirm_unverified": "false"},
        )
        assert false_string.status_code == 422
        assigned = await client.post(
            f"/api/profiles/{profile['id']}/role", headers=headers,
            json={"role": "main", "expected_generation": 0, "confirm_unverified": True},
        )
        assert assigned.status_code == 200
        catalog = (await client.get("/api/profiles")).json()
        assert catalog["roles_generation"] == 1
        assert catalog["profiles"][0]["role"] == "main"
        assert catalog["profiles"][0]["role_values"]["audio_model"] == "audio-model"

        duplicate = await client.post(
            f"/api/profiles/{profile['id']}/duplicate", headers=headers,
            json={"name": "Copia", "expected_generation": profile["generation"]},
        )
        assert duplicate.status_code == 201
        copy = duplicate.json()["profile"]
        assert copy["values"] == profile["values"]
        assert copy["role"] is None
        assert copy["verification"] == "unverified"
        duplicate_stale = await client.post(
            f"/api/profiles/{profile['id']}/duplicate", headers=headers,
            json={"name": "Copia stale", "expected_generation": profile["generation"] + 2},
        )
        assert duplicate_stale.status_code == 409

        updated = await client.put(
            f"/api/profiles/{profile['id']}", headers=headers,
            json={"expected_revision": profile["revision"],
                  "expected_generation": profile["generation"],
                  "name": "Primario rinominato", "values": _values(model="new-audio")},
        )
        assert updated.status_code == 200
        assert updated.json()["profile"]["revision"] > profile["revision"]
        detail = (await client.get(f"/api/profiles/{profile['id']}")).json()["profile"]
        assert detail["values"]["audio_model"] == "new-audio"
        assert detail["role_values"]["audio_model"] == "audio-model"
        catalog = next(
            item for item in (await client.get("/api/profiles")).json()["profiles"]
            if item["id"] == profile["id"]
        )
        assert catalog["audio_model"] == "new-audio"
        assert catalog["role_values"]["audio_model"] == "audio-model"
        stale = await client.put(
            f"/api/profiles/{profile['id']}", headers=headers,
            json={"expected_revision": profile["revision"],
                  "expected_generation": profile["generation"],
                  "name": "Stale", "values": _values()},
        )
        assert stale.status_code == 409
        blocked = await client.request(
            "DELETE", f"/api/profiles/{profile['id']}", headers=headers,
            json={"expected_generation": profile["generation"] + 1},
        )
        assert blocked.status_code == 409
    finally:
        await client.aclose()
        services.repo.close()


async def test_profile_test_route_binds_sample_test_to_profile_revision(tmp_path):
    services, client, headers = await _logged_in(tmp_path)
    try:
        created = await client.post(
            "/api/profiles", headers=headers,
            json={"name": "Test", "values": _values()},
        )
        profile = created.json()["profile"]
        calls = []

        async def run_sample(profile_id, revision, temporary_dir=None):
            calls.append((profile_id, revision, temporary_dir))
            return ProfileTestView("synthetic-test", revision, "succeeded", profile_id=profile_id)

        services.transcription_tests.run_profile_bundled_sample = run_sample
        response = await client.post(
            f"/api/profiles/{profile['id']}/test", headers={"x-csrf-token": headers["x-csrf-token"]},
            data={"revision": str(profile["revision"]), "source": "sample"},
        )
        assert response.status_code == 200
        assert response.json()["profile_id"] == profile["id"]
        assert response.json()["revision"] == profile["revision"]
        assert response.json()["status"] == "succeeded"
        assert calls == [(profile["id"], profile["revision"], services.settings.data_dir / "tmp")]

        stale = await client.post(
            f"/api/profiles/{profile['id']}/test", headers={"x-csrf-token": headers["x-csrf-token"]},
            data={"revision": "999", "source": "sample"},
        )
        assert stale.status_code == 409
        invalid_source = await client.post(
            f"/api/profiles/{profile['id']}/test", headers={"x-csrf-token": headers["x-csrf-token"]},
            data={"revision": str(profile["revision"]), "source": "remote"},
        )
        assert invalid_source.status_code == 422

        uploaded_paths = []

        async def run_upload(profile_id, revision, path):
            uploaded_paths.append(path)
            return ProfileTestView("synthetic-upload-test", revision, "succeeded", profile_id=profile_id)

        services.transcription_tests.run_profile = run_upload
        upload = await client.post(
            f"/api/profiles/{profile['id']}/test",
            headers={"x-csrf-token": headers["x-csrf-token"]},
            data={"revision": str(profile["revision"]), "source": "upload"},
            files={"file": ("synthetic.wav", b"synthetic audio", "audio/wav")},
        )
        assert upload.status_code == 200
        assert upload.json()["profile_id"] == profile["id"]
        assert len(uploaded_paths) == 1
        assert not uploaded_paths[0].exists()
    finally:
        await client.aclose()
        services.repo.close()


async def test_legacy_singleton_mutations_are_retired_and_health_uses_primary_profile(tmp_path):
    services, client, headers = await _logged_in(tmp_path)
    try:
        legacy = await client.put(
            "/api/transcription/draft", headers=headers,
            json={"service": "openai", "mode": "multimodal", "audio_model": "legacy",
                  "credential_version": 1, "system_prompt": "", "refine_template": "{raw_text}"},
        )
        assert legacy.status_code == 410

        created = await client.post(
            "/api/profiles", headers=headers, json={"name": "Primary", "values": _values()}
        )
        profile = created.json()["profile"]
        assigned = await client.post(
            f"/api/profiles/{profile['id']}/role", headers=headers,
            json={"role": "main", "expected_generation": 0, "confirm_unverified": True},
        )
        assert assigned.status_code == 200

        assert services.diagnostics.health()["configured"] is True
        assert services.runtime._configured_audio_limits().max_file_size_mb == 2
    finally:
        await client.aclose()
        services.repo.close()


async def test_main_assignment_rechecks_receipt_inside_role_transaction(tmp_path):
    services, client, headers = await _logged_in(tmp_path)
    try:
        created = await client.post(
            "/api/profiles", headers=headers,
            json={"name": "Verified", "values": _values()},
        )
        profile = created.json()["profile"]
        services.profiles.record_test(profile["id"], TestReceipt(
            "synthetic-valid-receipt", profile["revision"], 1,
            VALIDATOR_VERSION, True, ("synthetic-audio",), datetime.now(timezone.utc),
        ))
        original_assign = services.profiles.assign_role

        def receipt_race(*args, **kwargs):
            services.profiles.record_test(profile["id"], TestReceipt(
                "synthetic-invalidating-receipt", profile["revision"], 1,
                VALIDATOR_VERSION, False, (), datetime.now(timezone.utc),
            ))
            return original_assign(*args, **kwargs)

        services.profiles.assign_role = receipt_race
        response = await client.post(
            f"/api/profiles/{profile['id']}/role", headers=headers,
            json={"role": "main", "expected_generation": 0, "confirm_unverified": False},
        )
        assert response.status_code == 409
        assert (await client.get("/api/profiles")).json()["profiles"][0]["role"] is None
    finally:
        await client.aclose()
        services.repo.close()


async def test_fallback_clear_and_primary_restore_use_global_generation(tmp_path):
    services, client, headers = await _logged_in(tmp_path)
    try:
        async def create(name):
            response = await client.post(
                "/api/profiles", headers=headers,
                json={"name": name, "values": _values()},
            )
            assert response.status_code == 201
            return response.json()["profile"]

        first = await create("First main")
        second = await create("Second main")
        assert (await client.post(
            f"/api/profiles/{first['id']}/role", headers=headers,
            json={"role": "main", "expected_generation": 0, "confirm_unverified": True},
        )).status_code == 200
        assert (await client.post(
            f"/api/profiles/{second['id']}/role", headers=headers,
            json={"role": "main", "expected_generation": 1, "confirm_unverified": True},
        )).status_code == 200
        restored = await client.post(
            "/api/profiles/roles/primary/restore", headers=headers,
            json={"expected_generation": 2},
        )
        assert restored.status_code == 200
        assert restored.json()["restored"] is True
        assert restored.json()["roles_generation"] == 3

        services.profiles.record_test(second["id"], TestReceipt(
            "fallback-valid", second["revision"], 1, VALIDATOR_VERSION, True,
            ("synthetic-audio",), datetime.now(timezone.utc),
        ))
        assigned = await client.post(
            f"/api/profiles/{second['id']}/role", headers=headers,
            json={"role": "fallback", "expected_generation": 3, "confirm_unverified": False},
        )
        assert assigned.status_code == 200
        cleared = await client.post(
            "/api/profiles/roles/fallback/clear", headers=headers,
            json={"expected_generation": 4},
        )
        assert cleared.status_code == 200
        assert cleared.json()["roles_generation"] == 5
        stale = await client.post(
            "/api/profiles/roles/fallback/clear", headers=headers,
            json={"expected_generation": 4},
        )
        assert stale.status_code == 409
    finally:
        await client.aclose()
        services.repo.close()

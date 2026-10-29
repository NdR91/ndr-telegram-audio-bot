"""Synthetic end-to-end acceptance scenarios for the V2 release gate."""

import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from bot.database.secret_store import SecretStore
from bot.deployment import DeploymentSettings
from bot.migrate_v1 import migrate_v1
from bot.services import build_services
from bot.transcription.configuration import ConfigurationService
from bot.transcription.errors import ConfigurationInvalid
from bot.transcription.models import ConfigInput, Prerequisites, ProcessingResult, RuntimeLimits, StageResult, TestReceipt
from bot.transcription.runner import TranscriptionRunner
from bot.transcription.testing import ConfigurationTestService
from bot.web.auth import hash_password
from bot.web.v2_app import create_app
from tests.v2.legacy_fixtures import create_legacy_database


class _AudioAttachment:
    kind = "voice"
    file_size = 16
    file_name = None
    mime_type = "audio/ogg"

    async def get_file(self):
        return self

    async def download_to_drive(self, custom_path):
        Path(custom_path).write_bytes(b"synthetic ogg audio")


class _Runner:
    def __init__(self, text="Sintesi sintetica 42."):
        self.text = text
        self.snapshots = []

    async def run(self, snapshot, audio, *, allow_partial_refinement=True):
        self.snapshots.append((snapshot, audio))
        return ProcessingResult(self.text, False, (snapshot.values.audio_model,), 12)


class _TwoStageAdapter:
    def accepted_formats(self, _model, _role):
        return frozenset({"ogg"})

    async def transcribe(self, _audio, model):
        return StageResult("Testo grezzo con ehm.", model)

    async def refine(self, raw_text, model, _system_prompt, _template):
        assert raw_text == "Testo grezzo con ehm."
        return StageResult("Testo corretto.", model)


class _TwoStagePool:
    @asynccontextmanager
    async def lease(self, _snapshot):
        yield _TwoStageAdapter()


def _values(credential_version, *, model="synthetic-multimodal", mode="multimodal"):
    if mode == "two_stage" and model == "synthetic-multimodal":
        model = "synthetic-audio-model"
    return ConfigInput(
        service="openai",
        mode=mode,
        audio_model=model,
        text_model="synthetic-refinement-model" if mode == "two_stage" else None,
        credential_version=credential_version,
        system_prompt="Keep the transcript faithful.",
        refine_template="Refine minimally: {raw_text}",
        limits=RuntimeLimits(),
    )


def _message(tmp_path):
    replies = []

    async def reply_text(text):
        replies.append(text)

    message = SimpleNamespace(
        message_id=17,
        message_thread_id=None,
        chat=SimpleNamespace(id=71, type="private"),
        chat_id=71,
        from_user=SimpleNamespace(id=71, is_bot=False),
        sender_chat=None,
        voice=_AudioAttachment(),
        audio=None,
        document=None,
        effective_attachment=_AudioAttachment(),
        reply_text=reply_text,
        replies=replies,
    )
    return SimpleNamespace(message=message, edited_message=None)


class _Delivery:
    def __init__(self):
        self.texts = []

    async def send_final_response(self, _context, **kwargs):
        self.texts.append(kwargs["full_text"])


async def test_empty_install_to_first_voice(tmp_path, monkeypatch):
    from bot.handlers.v2_audio import handle_audio
    from bot.rate_limiter import RateLimiter
    from bot.v2_runtime import TelegramIdentity
    from bot.v2_setup_code import issue_setup_code

    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    setup_code = issue_setup_code(services.repo)
    app = create_app(settings, services)
    runner = _Runner()
    services.transcription_tests._runner = runner

    async def connect_synthetic_bot(token):
        assert token == "synthetic-telegram-token"
        services.repo.put_credential_with_state(
            "telegram", token, "telegram_credential_version",
            {"current_bot_id": "71", "current_bot_username": "synthetic_bot"},
        )
        return TelegramIdentity(71, "synthetic_bot")

    async def start_synthetic_bot():
        return None

    monkeypatch.setattr(services.runtime, "connect", connect_synthetic_bot)
    monkeypatch.setattr(services.runtime, "start_async", start_synthetic_bot)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/setup")).status_code == 200
        created = await client.post("/api/setup/admin", json={
            "code": setup_code, "password": "synthetic-admin-password",
        })
        assert created.status_code == 200
        csrf = created.json()["csrf_token"]
        headers = {"x-csrf-token": csrf}
        connected = await client.post("/api/setup/telegram", json={
            "token": "synthetic-telegram-token",
        }, headers=headers)
        assert connected.status_code == 200
        invite_response = await client.post(
            "/api/access/invites", json={"kind": "owner"}, headers=headers,
        )
        invite = invite_response.json()
        nonce = invite["link"].split("pair_", 1)[1]
        services.pairing.observe(nonce, 71, 71, "private", "Synthetic Owner")
        confirmed = await client.post(
            f"/api/access/invites/{invite['id']}/confirm",
            json={"candidate_id": 71}, headers=headers,
        )
        assert confirmed.status_code == 200

        credential = await client.post("/api/transcription/credentials", json={
            "service": "openai", "credential": "synthetic-provider-key",
        }, headers=headers)
        assert credential.status_code == 200
        profile_response = await client.post("/api/profiles", json={
            "name": "Primary",
            "values": {"service": "openai", "mode": "multimodal",
                "audio_model": "synthetic-multimodal", "text_model": None,
                "credential_version": 1, "system_prompt": "Faithful",
                "refine_template": "{raw_text}", "limits": {"max_file_size_mb": 1}},
        }, headers=headers)
        assert profile_response.status_code == 201
        profile = profile_response.json()["profile"]
        revision = profile["revision"]
        proof = await client.post(
            f"/api/profiles/{profile['id']}/test",
            data={"revision": str(revision), "source": "sample"},
            headers=headers,
        )
        assert proof.status_code == 200
        assert proof.json()["status"] == "succeeded"
        activated = await client.post(
            f"/api/profiles/{profile['id']}/role",
            json={"role": "main", "expected_generation": 0, "confirm_unverified": False},
            headers=headers,
        )
        assert activated.status_code == 200

    services.runner = runner
    services.fallback_processor._runner = runner
    services.settings = settings
    update = _message(tmp_path)
    delivery = _Delivery()
    context = SimpleNamespace(
        bot_data={"v2_services": services, "rate_limiter": RateLimiter(),
                  "audio_tmp_dir": tmp_path / "tmp", "delivery_adapter": delivery},
        bot=SimpleNamespace(send_message=lambda **kwargs: None),
    )
    await handle_audio(update, context)
    assert [snapshot.number for snapshot, _ in runner.snapshots] == [revision, revision]
    assert all(not path.exists() for _, path in runner.snapshots)
    assert delivery.texts == ["Sintesi sintetica 42."]
    services.repo.close()


def test_reboot_keeps_acl_and_active_revision(tmp_path):
    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    first = build_services(settings)
    first.repo.put_credential("openai", "synthetic-provider-key")
    first.access.authorize("user", 71)
    first.configuration = ConfigurationService(first.repo, lambda: Prerequisites(True, True, True))
    config = first.configuration
    draft = config.save_draft(None, _values(1))
    config.record_test(TestReceipt(
        "synthetic-receipt", draft.number, 1, "v2-1", True,
        ("synthetic-multimodal",), datetime.now(timezone.utc),
    ))
    config.activate(draft.number)
    first.repo.close()

    restarted = build_services(settings)
    restarted.configuration = ConfigurationService(
        restarted.repo, lambda: Prerequisites(True, True, True)
    )
    assert restarted.access.allowed(71, 71, "private")
    assert restarted.configuration.active() == draft
    restarted.repo.close()


def test_live_configuration_survives_failed_model_change(repo, config_values, ready_prerequisites):
    repo.put_credential("openai", "synthetic-provider-key")
    config = ConfigurationService(repo, lambda: ready_prerequisites)
    draft = config.save_draft(None, config_values)
    config.record_test(TestReceipt(
        "synthetic-receipt", draft.number, 1, "v2-1", True,
        ("whisper-1", "gpt-4o-mini"), datetime.now(timezone.utc),
    ))
    active = config.activate(draft.number)

    with pytest.raises(ConfigurationInvalid):
        config.save_draft(draft.number, _values(1, model=""))

    assert config.active() == active


def test_migration_reconfiguration_then_activation(tmp_path):
    source_db = tmp_path / "legacy-copy.sqlite3"
    target_db = tmp_path / "new-v2.sqlite3"
    source_key = tmp_path / "legacy-copy.key"
    target_key = tmp_path / "v2.key"
    source_store = SecretStore(str(source_key))
    source_store.initialize()
    create_legacy_database(source_db)
    connection = sqlite3.connect(source_db)
    connection.execute(
        "UPDATE app_settings SET setting_value=? WHERE setting_key='telegram_token'",
        (source_store.encrypt("synthetic-telegram-token"),),
    )
    connection.execute(
        "UPDATE provider_connections SET encrypted_credentials=? WHERE id=1",
        (source_store.encrypt("synthetic-provider-key"),),
    )
    connection.execute("UPDATE pipeline_profiles SET mode='single_pass' WHERE id=1")
    connection.commit()
    connection.close()

    report = migrate_v1(source_db, source_key, target_db, target_key)
    target_store = SecretStore(str(target_key))
    target_store.initialize()
    from bot.database.v2_repository import V2Repository

    repo = V2Repository(target_db, target_store)
    repo.initialize()
    try:
        assert report.needs_reconfiguration is True
        config = ConfigurationService(repo, lambda: Prerequisites(True, True, True))
        migrated = config.draft()
        assert migrated is None
        credential = repo.get_setup_state("openai_credential_version")
        assert credential is not None
        version = int(credential)
        replacement = config.save_draft(None, _values(version, mode="two_stage"))
        runner = TranscriptionRunner(_TwoStagePool(), tmp_path / "runner-tmp")
        proof = ConfigurationTestService(config, runner).run
        import asyncio

        audio = tmp_path / "synthetic.ogg"
        audio.write_bytes(b"synthetic audio")
        view = asyncio.run(proof(replacement.number, audio))
        assert view.status == "succeeded"
        assert view.result.models == ("synthetic-audio-model", "synthetic-refinement-model")
        assert view.result.text == "Testo corretto."
        assert config.activate(replacement.number) == replacement
    finally:
        repo.close()

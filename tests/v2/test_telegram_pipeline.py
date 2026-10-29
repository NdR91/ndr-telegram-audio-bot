import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.access import AccessService
from bot.transcription.models import ConfigRevision, ProcessingResult


class FakeAttachment:
    def __init__(self, kind, file_name=None, mime_type=None):
        self.kind = kind
        self.file_unique_id = f"synthetic-{kind}"
        self.file_size = 3
        self.file_name = file_name
        self.mime_type = mime_type
        self.downloads = 0

    async def get_file(self):
        self.downloads += 1
        return self

    async def download_to_drive(self, custom_path):
        Path(custom_path).write_bytes(b"synthetic audio")


class FakeRunner:
    def __init__(self):
        self.snapshots = []
        self.paths = []
        self.gate = None

    async def run(self, snapshot, audio):
        self.snapshots.append(snapshot)
        self.paths.append(audio)
        if self.gate:
            await self.gate.wait()
        return ProcessingResult("exact text", False, ("chosen-model",), 3)


def make_message(attachment, chat_type="private", chat_id=42, user_id=7, message_id=9):
    replies = []

    ack_messages = []

    async def reply_text(text):
        replies.append(text)
        ack = SimpleNamespace(message_id=100, deleted=False)
        async def delete():
            ack.deleted = True
        ack.delete = delete
        ack_messages.append(ack)
        return ack

    message = SimpleNamespace(
        message_id=message_id, message_thread_id=55,
        chat=SimpleNamespace(id=chat_id, type=chat_type), chat_id=chat_id,
        from_user=SimpleNamespace(id=user_id, is_bot=False), sender_chat=None,
        voice=attachment if attachment.kind == "voice" else None,
        audio=attachment if attachment.kind == "audio" else None,
        document=attachment if attachment.kind == "document" else None,
        effective_attachment=attachment, reply_text=reply_text, replies=replies, ack_messages=ack_messages,
    )
    return message


def make_context(services, limiter, tmp_path, delivery=None):
    sent_messages = []

    async def send_message(**kwargs):
        sent_messages.append(kwargs)
        return None

    return SimpleNamespace(sent_messages=sent_messages, bot_data={
        "v2_services": services, "rate_limiter": limiter,
        "delivery_adapter": delivery, "audio_tmp_dir": tmp_path / "tmp",
    }, bot=SimpleNamespace(send_message=send_message))


@pytest.mark.parametrize("kind", ["voice", "audio", "document"])
@pytest.mark.parametrize("chat_type,chat_id,acl_kind,acl_id", [
    ("private", 42, "user", 7), ("group", -1004, "group", -1004),
])
async def test_authorized_private_and_group_voice_replies(kind, chat_type, chat_id, acl_kind, acl_id, repo, config_values, ready_prerequisites, tmp_path):
    from bot.handlers.v2_audio import handle_audio
    from bot.rate_limiter import RateLimiter
    from bot.transcription.configuration import ConfigurationService

    access = AccessService(repo)
    access.authorize(acl_kind, acl_id)
    credential = repo.put_credential("openai", "synthetic-key")
    config = ConfigurationService(repo, lambda: ready_prerequisites)
    values = config_values.__class__(**{**config_values.__dict__, "credential_version": credential})
    saved = config.save_draft(None, values)
    config.record_test(__import__("bot.transcription.models", fromlist=["TestReceipt"]).TestReceipt(
        "receipt", saved.number, credential, "v2-1", True, ("chosen-model",),
        __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    ))
    snapshot = config.activate(saved.number)
    runner = FakeRunner()
    services = SimpleNamespace(
        access=access, configuration=config, runner=runner, repo=repo,
        settings=SimpleNamespace(data_dir=tmp_path),
    )
    attachment = FakeAttachment(kind, "memo.mp3", "audio/mpeg")
    message = make_message(attachment, chat_type, chat_id)
    context = make_context(services, RateLimiter(), tmp_path)
    update = SimpleNamespace(message=message, edited_message=None)

    await handle_audio(update, context)

    assert runner.snapshots == [snapshot]
    assert runner.paths[0].exists() is False
    delivered = [call for call in context.sent_messages if call["text"] == "exact text"]
    assert len(delivered) == 1
    assert delivered[0]["reply_to_message_id"] == 9
    assert delivered[0]["message_thread_id"] == 55
    if chat_type == "group" and kind == "voice":
        group = access.list_entries_with_status()["group"][0]
        assert group["id"] == chat_id
        assert group["last_received"] is not None
        assert group["membership"] == "unchecked"


async def test_private_acl_cannot_bypass_group_acl(repo, config_values, ready_prerequisites, tmp_path):
    from bot.handlers.v2_audio import handle_audio
    from bot.rate_limiter import RateLimiter

    access = AccessService(repo)
    access.authorize("user", 7)
    attachment = FakeAttachment("voice")
    message = make_message(attachment, "group", -1004, 7)
    services = SimpleNamespace(access=access, configuration=SimpleNamespace(active=lambda: None),
                               runner=FakeRunner(), repo=repo, settings=SimpleNamespace(data_dir=tmp_path))

    await handle_audio(SimpleNamespace(message=message, edited_message=None), make_context(services, RateLimiter(), tmp_path))

    assert attachment.downloads == 0
    assert services.runner.snapshots == []


async def test_anonymous_bot_edited_and_duplicate_updates_do_not_generate(repo, tmp_path):
    from bot.handlers.v2_audio import handle_audio
    from bot.rate_limiter import RateLimiter

    access = AccessService(repo)
    access.authorize("group", -1004)
    runner = FakeRunner()
    runner.gate = asyncio.Event()
    from bot.transcription.models import ConfigInput
    repo.put_credential("openai", "synthetic-key")
    values = ConfigInput("openai", "multimodal", "chosen-model", None, 1, "faithful", "{raw_text}", __import__("bot.transcription.models", fromlist=["RuntimeLimits"]).RuntimeLimits())
    services = SimpleNamespace(access=access, configuration=SimpleNamespace(active=lambda: ConfigRevision(1, values)),
                               runner=runner, repo=repo, settings=SimpleNamespace(data_dir=tmp_path))
    limiter = RateLimiter(max_global=3)
    attachment = FakeAttachment("voice")
    message = make_message(attachment, "group", -1004, 7)
    context = make_context(services, limiter, tmp_path)
    context.bot = SimpleNamespace(send_message=lambda **kwargs: asyncio.sleep(0))
    update = SimpleNamespace(message=message, edited_message=None)
    first = asyncio.create_task(handle_audio(update, context))
    await asyncio.sleep(0)
    await handle_audio(update, context)
    runner.gate.set()
    await first
    assert len(runner.snapshots) == 1

    message.from_user = None
    message.sender_chat = SimpleNamespace(id=-1004)
    await handle_audio(SimpleNamespace(message=message, edited_message=None), context)
    await handle_audio(SimpleNamespace(message=None, edited_message=message), context)
    assert len(runner.snapshots) == 1
    assert attachment.downloads == 1


async def test_queued_request_keeps_admitted_revision(repo, tmp_path):
    from bot.handlers.v2_audio import handle_audio
    from bot.rate_limiter import RateLimiter
    from bot.transcription.models import ConfigInput, RuntimeLimits

    access = AccessService(repo)
    access.authorize("group", -1004)
    repo.put_credential("openai", "synthetic-key")
    limits = RuntimeLimits(max_concurrent_global=1)
    values = ConfigInput("openai", "multimodal", "model-one", None, 1, "faithful", "{raw_text}", limits)
    first_snapshot = ConfigRevision(1, values)
    second_snapshot = ConfigRevision(2, ConfigInput("openai", "multimodal", "model-two", None, 1, "faithful", "{raw_text}", limits))
    active_snapshots = [first_snapshot, second_snapshot]
    services = SimpleNamespace(
        access=access, configuration=SimpleNamespace(active=lambda: active_snapshots.pop(0)),
        runner=FakeRunner(), repo=repo, settings=SimpleNamespace(data_dir=tmp_path),
    )
    services.runner.gate = asyncio.Event()
    limiter = RateLimiter(max_global=1, queue_enabled=True, max_queue_size=2)
    context = make_context(services, limiter, tmp_path)
    first_message = make_message(FakeAttachment("voice"), "group", -1004, message_id=9)
    second_message = make_message(FakeAttachment("voice"), "group", -1004, user_id=8, message_id=10)
    access.authorize("user", 8)
    first = asyncio.create_task(handle_audio(SimpleNamespace(message=first_message, edited_message=None), context))
    while not services.runner.snapshots:
        await asyncio.sleep(0)
    second = asyncio.create_task(handle_audio(SimpleNamespace(message=second_message, edited_message=None), context))
    while not limiter._wait_queue:
        await asyncio.sleep(0)
    services.runner.gate.set()
    await asyncio.gather(first, second)

    assert [snapshot.number for snapshot in services.runner.snapshots] == [1, 2]
    assert limiter._global_count == 0


@pytest.mark.parametrize("failure", ["timeout", "delivery"])
async def test_timeout_and_delivery_failure_cleanup_all_files(repo, tmp_path, failure):
    from bot.handlers.v2_audio import handle_audio
    from bot.rate_limiter import RateLimiter
    from bot.transcription.models import ConfigInput, RuntimeLimits

    access = AccessService(repo)
    access.authorize("user", 7)
    repo.put_credential("openai", "synthetic-key")
    values = ConfigInput("openai", "multimodal", "model", None, 1, "faithful", "{raw_text}", RuntimeLimits())
    runner = FakeRunner()
    if failure == "timeout":
        async def timeout(*args):
            raise asyncio.TimeoutError()
        runner.run = timeout
    services = SimpleNamespace(
        access=access, configuration=SimpleNamespace(active=lambda: ConfigRevision(1, values)),
        runner=runner, repo=repo, settings=SimpleNamespace(data_dir=tmp_path),
    )
    attachment = FakeAttachment("voice")
    message = make_message(attachment)
    limiter = RateLimiter()
    context = make_context(services, limiter, tmp_path)
    if failure == "delivery":
        class BrokenDelivery:
            async def send_final_response(self, *args, **kwargs):
                raise RuntimeError("synthetic Telegram send failure")
        context.bot_data["delivery_adapter"] = BrokenDelivery()

    await handle_audio(SimpleNamespace(message=message, edited_message=None), context)

    assert list((tmp_path / "tmp").glob("telegram-*") ) == []
    assert limiter._global_count == 0
    assert limiter._active_requests == {}


async def test_inflight_duplicate_survives_completed_update_cache_churn(repo, tmp_path):
    from bot.handlers.v2_audio import handle_audio
    from bot.rate_limiter import RateLimiter
    from bot.transcription.models import ConfigInput, RuntimeLimits

    access = AccessService(repo)
    access.authorize("group", -1004)
    repo.put_credential("openai", "synthetic-key")
    values = ConfigInput("openai", "multimodal", "model", None, 1, "faithful", "{raw_text}", RuntimeLimits())
    snapshot = ConfigRevision(1, values)
    active_calls = 0

    def active():
        nonlocal active_calls
        active_calls += 1
        return snapshot if active_calls in {1, 1002} else None

    runner = FakeRunner()
    runner.gate = asyncio.Event()
    services = SimpleNamespace(
        access=access, configuration=SimpleNamespace(active=active), runner=runner, repo=repo,
        settings=SimpleNamespace(data_dir=tmp_path),
    )
    context = make_context(services, RateLimiter(max_global=2), tmp_path)
    original = make_message(FakeAttachment("voice"), "group", -1004, message_id=9)
    first = asyncio.create_task(handle_audio(SimpleNamespace(message=original, edited_message=None), context))
    while not runner.snapshots:
        await asyncio.sleep(0)

    for message_id in range(1000, 2000):
        churn = make_message(FakeAttachment("voice"), "group", -1004, message_id=message_id)
        await handle_audio(SimpleNamespace(message=churn, edited_message=None), context)
    duplicate = asyncio.create_task(handle_audio(SimpleNamespace(message=original, edited_message=None), context))
    await asyncio.sleep(0)
    runner.gate.set()
    await asyncio.gather(first, duplicate)

    assert len(runner.snapshots) == 1


async def test_shutdown_cancellation_cleans_audio_file_and_releases_admission(repo, tmp_path):
    from bot.handlers.v2_audio import handle_audio
    from bot.rate_limiter import RateLimiter
    from bot.transcription.models import ConfigInput, RuntimeLimits
    from bot.v2_runtime import RuntimeManager

    access = AccessService(repo)
    access.authorize("user", 7)
    repo.put_credential("openai", "synthetic-key")
    values = ConfigInput("openai", "multimodal", "model", None, 1, "faithful", "{raw_text}", RuntimeLimits())
    snapshot = ConfigRevision(1, values)
    runner = FakeRunner()
    runner.gate = asyncio.Event()
    services = SimpleNamespace(
        access=access, configuration=SimpleNamespace(active=lambda: snapshot), runner=runner,
        repo=repo, settings=SimpleNamespace(data_dir=tmp_path),
    )
    limiter = RateLimiter(max_global=1)
    context = make_context(services, limiter, tmp_path)
    message = make_message(FakeAttachment("voice"))

    from test_v2_runtime import FakeApplication
    events = []
    app = FakeApplication("synthetic-token", events)
    app.running = True
    app.updater.running = True
    app.bot_data = context.bot_data
    manager = RuntimeManager(repo, lambda token: app, shutdown_seconds=0.1)
    manager._app = app
    manager._started = True

    task = asyncio.create_task(handle_audio(SimpleNamespace(message=message, edited_message=None), context))
    while not runner.paths:
        await asyncio.sleep(0)
    assert runner.paths[0].exists()

    await asyncio.wait_for(manager.stop_async(), timeout=0.3)
    assert task.done() and task.cancelled()
    assert not runner.paths[0].exists()
    assert limiter._global_count == 0
    assert ("stop", app.token) in events


async def test_cancelled_handler_deletes_ack_and_cleans_audio(repo, tmp_path):
    from bot.handlers.v2_audio import handle_audio
    from bot.rate_limiter import RateLimiter
    from bot.transcription.models import ConfigInput, RuntimeLimits

    access = AccessService(repo)
    access.authorize("user", 7)
    repo.put_credential("openai", "synthetic-key")
    snapshot = ConfigRevision(1, ConfigInput("openai", "multimodal", "model", None, 1, "faithful", "{raw_text}", RuntimeLimits()))
    runner = FakeRunner()
    runner.gate = asyncio.Event()
    services = SimpleNamespace(access=access, configuration=SimpleNamespace(active=lambda: snapshot),
                               runner=runner, repo=repo, settings=SimpleNamespace(data_dir=tmp_path))
    limiter = RateLimiter(max_global=1)
    context = make_context(services, limiter, tmp_path)
    message = make_message(FakeAttachment("voice"))
    task = asyncio.create_task(handle_audio(SimpleNamespace(message=message, edited_message=None), context))
    while not runner.paths:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert message.ack_messages and message.ack_messages[-1].deleted
    assert not runner.paths[0].exists()
    assert limiter._global_count == 0

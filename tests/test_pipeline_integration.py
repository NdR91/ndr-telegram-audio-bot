import asyncio
from types import SimpleNamespace

import pytest
from telegram.ext import CommandHandler, MessageHandler

from bot import constants as c
from bot.core.app import create_application
from bot.exceptions import TranscribeError
from bot.handlers.audio import handle_audio
from bot.rate_limiter import RateLimiter


class FakeAckMessage:
    def __init__(self, message_id=900):
        self.message_id = message_id
        self.chat = SimpleNamespace(type="private")
        self.edits = []
        self.deleted = False

    async def edit_text(self, text):
        self.edits.append(text)

    async def delete(self):
        self.deleted = True


class FakeTelegramFile:
    def __init__(self):
        self.downloads = []

    async def download_to_drive(self, file_path):
        self.downloads.append(file_path)


class FakeMessage:
    def __init__(self, user_id, chat_id, message_id, file_unique_id):
        self.from_user = SimpleNamespace(id=user_id)
        self.chat_id = chat_id
        self.message_id = message_id
        self.voice = SimpleNamespace(file_size=1024, get_file=self._get_file)
        self.audio = None
        self.document = None
        self.effective_attachment = SimpleNamespace(file_unique_id=file_unique_id)
        self.file = FakeTelegramFile()
        self.replies = []
        self.ack = FakeAckMessage(message_id=message_id + 1000)

    async def _get_file(self):
        return self.file

    async def reply_text(self, text):
        self.replies.append(text)
        return self.ack


class FakeBot:
    def __init__(self):
        self.actions = []
        self.edits = []
        self.sent = []

    async def send_chat_action(self, **kwargs):
        self.actions.append(kwargs)

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


class FakeProcessor:
    provider_name = "fake"

    @property
    def transcribe_accepted_formats(self) -> frozenset[str]:
        return frozenset({"mp3"})

    def __init__(self, *, fail_stage=None, started=None, release=None):
        self.provider = SimpleNamespace(
            model_name="fake-model",
            supports_refine_streaming=False,
        )
        self.fail_stage = fail_stage
        self.started = started
        self.release = release
        self.calls = []
        self.cleaned = []
        self.responses = []

    async def determine_file_type(self, message):
        self.calls.append("determine")
        return message.file, "ogg"

    def generate_file_paths(self, chat_id, message_id, unique_id, ext):
        return f"/tmp/{chat_id}_{message_id}_{unique_id}.{ext}", f"/tmp/{chat_id}_{message_id}_{unique_id}.mp3"

    async def download_audio(self, file_obj, file_path):
        self.calls.append("download")
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            await self.release.wait()

    async def convert_audio(self, source, target):
        self.calls.append("convert")

    async def transcribe_audio(self, file_path):
        self.calls.append("transcribe")
        if self.fail_stage == "transcribe":
            raise TranscribeError("provider failed", c.MSG_ERROR_TRANSCRIBE)
        return "raw transcript"

    async def refine_text(self, raw_text):
        self.calls.append("refine")
        return "refined transcript"

    def format_response(self, final_text):
        return f"result: {final_text}"

    async def send_response(self, context, chat_id, ack_msg, full_text):
        self.calls.append("send")
        self.responses.append(full_text)
        await ack_msg.edit_text(full_text)

    def cleanup_files(self, source, target):
        self.calls.append("cleanup")
        self.cleaned.append((source, target))


class FailingDeliveryProcessor(FakeProcessor):
    async def send_response(self, context, chat_id, ack_msg, full_text):
        self.calls.append("send")
        raise RuntimeError("telegram unavailable")


def build_update(message):
    return SimpleNamespace(
        message=message,
        effective_user=message.from_user,
        effective_chat=SimpleNamespace(id=message.chat_id),
    )


def build_context(processor, limiter):
    return SimpleNamespace(
        bot=FakeBot(),
        bot_data={
            "config": SimpleNamespace(
                authorized_data={"admin": [], "users": [1, 2], "groups": []}
            ),
            "audio_processor": processor,
            "delivery_adapter": SimpleNamespace(
                supports_live_refine_streaming=lambda context, ack: False
            ),
            "rate_limiter": limiter,
        },
    )


@pytest.mark.asyncio
async def test_complete_handler_pipeline_runs_through_decorators_and_releases_slot():
    processor = FakeProcessor()
    limiter = RateLimiter(max_per_user=1, max_global=1)
    message = FakeMessage(user_id=1, chat_id=10, message_id=20, file_unique_id="voice")
    context = build_context(processor, limiter)

    await handle_audio(build_update(message), context)

    assert processor.calls == [
        "determine",
        "download",
        "convert",
        "transcribe",
        "refine",
        "send",
        "cleanup",
    ]
    assert processor.responses == ["result: refined transcript"]
    assert processor.cleaned == [("/tmp/10_20_voice.ogg", "/tmp/10_20_voice.mp3")]
    assert limiter._global_count == 0
    assert limiter._active_requests == {}


@pytest.mark.asyncio
async def test_provider_error_is_reported_and_pipeline_resources_are_released():
    processor = FakeProcessor(fail_stage="transcribe")
    limiter = RateLimiter(max_per_user=1, max_global=1)
    message = FakeMessage(user_id=1, chat_id=10, message_id=21, file_unique_id="voice")
    context = build_context(processor, limiter)

    await handle_audio(build_update(message), context)

    assert message.ack.edits[-1] == c.MSG_ERROR_TRANSCRIBE
    assert processor.calls[-1] == "cleanup"
    assert limiter._global_count == 0
    assert limiter._active_requests == {}


@pytest.mark.asyncio
async def test_telegram_delivery_error_uses_safe_message_and_releases_resources():
    processor = FailingDeliveryProcessor()
    limiter = RateLimiter(max_per_user=1, max_global=1)
    message = FakeMessage(user_id=1, chat_id=10, message_id=22, file_unique_id="voice")
    context = build_context(processor, limiter)

    await handle_audio(build_update(message), context)

    assert message.ack.edits[-1] == c.MSG_ERROR_INTERNAL
    assert processor.calls[-1] == "cleanup"
    assert limiter._global_count == 0
    assert limiter._active_requests == {}


@pytest.mark.asyncio
async def test_decorated_handlers_handoff_global_queue_in_fifo_order():
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    first_processor = FakeProcessor(started=first_started, release=release_first)
    second_processor = FakeProcessor()
    limiter = RateLimiter(
        max_per_user=1,
        max_global=1,
        queue_enabled=True,
        max_queue_size=2,
    )
    first_message = FakeMessage(1, 10, 30, "first")
    second_message = FakeMessage(2, 10, 31, "second")
    first_context = build_context(first_processor, limiter)
    second_context = build_context(second_processor, limiter)

    first_task = asyncio.create_task(handle_audio(build_update(first_message), first_context))
    await first_started.wait()
    second_task = asyncio.create_task(handle_audio(build_update(second_message), second_context))
    await asyncio.sleep(0)

    assert second_message.replies == [c.MSG_QUEUE_ACCEPTED.format(position=1)]
    assert second_processor.calls == []

    release_first.set()
    await asyncio.gather(first_task, second_task)

    assert second_processor.calls[0] == "determine"
    assert second_processor.calls[-1] == "cleanup"
    assert limiter._global_count == 0
    assert limiter._active_requests == {}


def test_create_application_wires_services_handlers_and_cleanup_job(monkeypatch, tmp_path):
    processor = object()
    monkeypatch.setattr("bot.core.app.AudioProcessor", lambda config: processor)
    config = SimpleNamespace(
        authorized_db=str(tmp_path / "authorized.sqlite3"),
        authorized_data={"admin": [1], "users": [], "groups": []},
        telegram_progressive_output_config={"enabled": False},
        rate_limit_config={
            "max_per_user": 2,
            "cooldown_seconds": 30,
            "max_concurrent_global": 6,
            "max_file_size_mb": 20,
            "queue_enabled": True,
            "max_queue_size": 10,
            "max_queued_per_user": 1,
        },
    )

    application = create_application("123456:TEST_TOKEN", config)

    assert application.bot_data["config"] is config
    assert application.bot_data["audio_processor"] is processor
    assert application.bot_data["whitelist_manager"].authorized_data["admin"] == [1]
    assert isinstance(application.bot_data["rate_limiter"], RateLimiter)
    assert application.bot_data["delivery_adapter"].is_progressive_enabled() is False

    handlers = [handler for group in application.handlers.values() for handler in group]
    assert sum(isinstance(handler, CommandHandler) for handler in handlers) == 7
    assert sum(isinstance(handler, MessageHandler) for handler in handlers) == 1
    assert application.job_queue is not None
    assert len(application.job_queue.jobs()) == 1

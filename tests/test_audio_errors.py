import pytest
import asyncio
from unittest.mock import Mock
from types import SimpleNamespace

from bot import constants as c
from bot.decorators.timeout import execute_with_timeout
from bot.exceptions import ConvertError, DownloadError, DownloadTimeout, ProviderCircuitOpen, RefineError, TranscribeError
from bot.handlers.audio import AudioProcessor, _elapsed_ms
from bot.providers import RefineStreamEvent


@pytest.mark.asyncio
async def test_execute_with_timeout_raises_typed_download_timeout():
    async def slow():
        await asyncio.sleep(0.01)

    from bot import constants as c
    original_timeout = c.PROGRESS_TIMEOUTS["download"]
    c.PROGRESS_TIMEOUTS["download"] = 0

    try:
        with pytest.raises(DownloadTimeout) as exc_info:
            await execute_with_timeout("download", slow(), default_timeout=0)
    finally:
        c.PROGRESS_TIMEOUTS["download"] = original_timeout

    assert exc_info.value.user_message == c.MSG_TIMEOUT_DOWNLOAD


@pytest.mark.asyncio
async def test_download_audio_wraps_errors_in_download_error():
    processor = AudioProcessor.__new__(AudioProcessor)

    class FailingFile:
        async def download_to_drive(self, file_path):
            raise RuntimeError("boom")

    with pytest.raises(DownloadError) as exc_info:
        await processor.download_audio(FailingFile(), "file.ogg")

    assert exc_info.value.user_message == c.MSG_ERROR_DOWNLOAD


@pytest.mark.asyncio
async def test_convert_error_exposes_conversion_user_message(monkeypatch):
    async def fake_exec(*args, **kwargs):
        class Process:
            returncode = 1

            async def communicate(self):
                return b"", b"ffmpeg failed"

        return Process()

    monkeypatch.setattr("bot.utils.asyncio.create_subprocess_exec", fake_exec)

    from bot import utils

    with pytest.raises(ConvertError) as exc_info:
        await utils.convert_to_mp3("in.ogg", "out.mp3")

    assert exc_info.value.user_message == c.MSG_ERROR_CONVERT


def test_elapsed_ms_returns_non_negative_duration():
    assert _elapsed_ms(0.0) >= 0


@pytest.mark.asyncio
async def test_openai_provider_failure_logging_uses_safe_metadata(monkeypatch):
    from bot import providers

    provider = providers.OpenAIProvider.__new__(providers.OpenAIProvider)
    provider.model_name = "gpt-4o-mini"
    provider.prompts = {"system": "s", "refine_template": "{raw_text}"}

    class DummyClient:
        def with_options(self, **kwargs):
            class DummyAudio:
                class DummyTranscriptions:
                    def create(self, **kwargs):
                        raise RuntimeError("provider boom")

                transcriptions = DummyTranscriptions()

            class Wrapped:
                audio = DummyAudio()

            return Wrapped()

    provider.client = DummyClient()
    logger_mock = Mock()
    monkeypatch.setattr(providers, "logger", logger_mock)

    with pytest.raises(providers.TranscribeError):
        await provider.transcribe_audio(__file__)

    assert logger_mock.error.called


@pytest.mark.asyncio
async def test_openai_stream_refine_normalizes_responses_events(monkeypatch):
    from bot import providers

    provider = providers.OpenAIProvider.__new__(providers.OpenAIProvider)
    provider.model_name = "gpt-4o-mini"
    provider.prompts = {"system": "sys", "refine_template": "Prompt: {raw_text}"}

    class Event:
        def __init__(self, type, delta=None, text=None):
            self.type = type
            self.delta = delta
            self.text = text

    class Stream:
        def __aiter__(self):
            self._events = iter([
                Event("response.output_text.delta", delta="Hello"),
                Event("response.output_text.delta", delta=" world"),
                Event("response.output_text.done", text="Hello world"),
                Event("response.completed"),
            ])
            return self

        async def __anext__(self):
            try:
                return next(self._events)
            except StopIteration:
                raise StopAsyncIteration

    class DummyResponses:
        async def create(self, **kwargs):
            return Stream()

    class DummyAsyncClient:
        def with_options(self, **kwargs):
            return SimpleNamespace(responses=DummyResponses())

    provider.async_client = DummyAsyncClient()
    logger_mock = Mock()
    monkeypatch.setattr(providers, "logger", logger_mock)

    events = [event async for event in provider.stream_refine_text("hello")]

    assert events == [
        providers.RefineStreamEvent(type="delta", text="Hello"),
        providers.RefineStreamEvent(type="delta", text=" world"),
        providers.RefineStreamEvent(type="done", text="Hello world"),
    ]


@pytest.mark.asyncio
async def test_openai_stream_refine_raises_refine_error_on_error_event():
    from bot import providers

    provider = providers.OpenAIProvider.__new__(providers.OpenAIProvider)
    provider.model_name = "gpt-4o-mini"
    provider.prompts = {"system": "sys", "refine_template": "Prompt: {raw_text}"}

    class Event:
        def __init__(self, type):
            self.type = type

    class Stream:
        def __aiter__(self):
            self._events = iter([Event("error")])
            return self

        async def __anext__(self):
            try:
                return next(self._events)
            except StopIteration:
                raise StopAsyncIteration

    class DummyResponses:
        async def create(self, **kwargs):
            return Stream()

    class DummyAsyncClient:
        def with_options(self, **kwargs):
            return SimpleNamespace(responses=DummyResponses())

    provider.async_client = DummyAsyncClient()

    with pytest.raises(RefineError):
        async for _ in provider.stream_refine_text("hello"):
            pass


@pytest.mark.asyncio
async def test_gemini_stream_refine_normalizes_chunks(monkeypatch):
    from bot import providers

    provider = providers.GeminiProvider.__new__(providers.GeminiProvider)
    provider.model_name = "gemini-test"
    provider.prompts = {"system": "sys", "refine_template": "Prompt: {raw_text}"}

    class Chunk:
        def __init__(self, text):
            self.text = text

    class DummyModels:
        def generate_content_stream(self, **kwargs):
            return iter([Chunk("Hello"), Chunk(" world")])

    provider.client = SimpleNamespace(models=DummyModels())
    logger_mock = Mock()
    monkeypatch.setattr(providers, "logger", logger_mock)

    events = [event async for event in provider.stream_refine_text("hello")]

    assert events == [
        providers.RefineStreamEvent(type="delta", text="Hello"),
        providers.RefineStreamEvent(type="delta", text=" world"),
        providers.RefineStreamEvent(type="done", text="Hello world"),
    ]


@pytest.mark.asyncio
async def test_gemini_stream_refine_raises_refine_error_on_failure():
    from bot import providers

    provider = providers.GeminiProvider.__new__(providers.GeminiProvider)
    provider.model_name = "gemini-test"
    provider.prompts = {"system": "sys", "refine_template": "Prompt: {raw_text}"}

    class DummyModels:
        def generate_content_stream(self, **kwargs):
            raise RuntimeError("gemini boom")

    provider.client = SimpleNamespace(models=DummyModels())

    with pytest.raises(RefineError):
        async for _ in provider.stream_refine_text("hello"):
            pass


@pytest.mark.asyncio
async def test_resilient_provider_opens_circuit_after_threshold():
    from bot.providers import ResilientProvider

    class FailingProvider:
        model_name = "test"

        async def transcribe_audio(self, file_path: str) -> str:
            raise TranscribeError("boom", c.MSG_ERROR_TRANSCRIBE)

        async def refine_text(self, raw_text: str) -> str:
            return raw_text

    provider = ResilientProvider(FailingProvider(), provider_name="openai", failure_threshold=2, cooldown_seconds=60)

    with pytest.raises(TranscribeError):
        await provider.transcribe_audio("a")
    with pytest.raises(TranscribeError):
        await provider.transcribe_audio("a")
    with pytest.raises(ProviderCircuitOpen) as exc_info:
        await provider.transcribe_audio("a")

    assert exc_info.value.user_message == c.MSG_PROVIDER_TEMPORARILY_UNAVAILABLE


@pytest.mark.asyncio
async def test_gemini_remote_cleanup_uses_keyword_name(monkeypatch):
    from bot import providers

    deleted = []

    class DummyFiles:
        def upload(self, file):
            return SimpleNamespace(name="remote-file", state="ACTIVE")

        def delete(self, *, name):
            deleted.append(name)

    class DummyModels:
        def generate_content(self, **kwargs):
            return SimpleNamespace(text="transcribed text")

    provider = providers.GeminiProvider.__new__(providers.GeminiProvider)
    provider.client = SimpleNamespace(files=DummyFiles(), models=DummyModels())
    provider.model_name = "gemini-test"
    provider.prompts = {"system": "s", "refine_template": "{raw_text}"}

    result = await provider.transcribe_audio(__file__)

    assert result == "transcribed text"
    assert deleted == ["remote-file"]


@pytest.mark.asyncio
async def test_resilient_provider_stream_records_failure():
    from bot.providers import ResilientProvider

    class BrokenStreamingProvider:
        supports_refine_streaming = True
        model_name = "test"

        async def transcribe_audio(self, file_path: str) -> str:
            return "raw"

        async def refine_text(self, raw_text: str) -> str:
            return raw_text

        async def stream_refine_text(self, raw_text: str):
            raise RefineError("boom", c.MSG_ERROR_REFINE)
            yield

    provider = ResilientProvider(BrokenStreamingProvider(), provider_name="openai", failure_threshold=1, cooldown_seconds=60)

    with pytest.raises(RefineError):
        async for _ in provider.stream_refine_text("hello"):
            pass

    with pytest.raises(ProviderCircuitOpen):
        async for _ in provider.stream_refine_text("hello"):
            pass


@pytest.mark.asyncio
async def test_audio_processor_stream_refine_text_uses_delivery_adapter():
    processor = AudioProcessor.__new__(AudioProcessor)
    processor.config = SimpleNamespace(provider_name="openai")

    class StreamingProvider:
        model_name = "gpt-4o-mini"
        supports_refine_streaming = True

        async def stream_refine_text(self, raw_text: str):
            yield RefineStreamEvent(type="delta", text="Hello")
            yield RefineStreamEvent(type="done", text="Hello")

    processor.provider = StreamingProvider()
    adapter_calls = []

    class DummyAdapter:
        def start_progressive_response(self, context, chat_id, ack_msg):
            adapter_calls.append(("start", chat_id))
            return SimpleNamespace(accumulated_text="")

        async def push_progressive_delta(self, context, session, delta_text):
            adapter_calls.append(("delta", delta_text))
            session.accumulated_text += delta_text

        async def finalize_progressive_response(self, context, session, full_text):
            adapter_calls.append(("finalize", full_text))

    context = SimpleNamespace(bot_data={"delivery_adapter": DummyAdapter()})
    ack_msg = SimpleNamespace()

    final_text = await processor.stream_refine_text(context, 1, ack_msg, "raw")

    assert final_text == "Hello"
    assert adapter_calls == [
        ("start", 1),
        ("delta", "Hello"),
        ("finalize", "📝 Trascrizione Completata\n🤖 Modello: gpt-4o-mini\n\nHello"),
    ]


@pytest.mark.asyncio
async def test_audio_processor_stream_refine_text_falls_back_to_accumulated_text_when_done_missing():
    processor = AudioProcessor.__new__(AudioProcessor)
    processor.config = SimpleNamespace(provider_name="openai")

    class StreamingProvider:
        model_name = "gpt-4o-mini"
        supports_refine_streaming = True

        async def stream_refine_text(self, raw_text: str):
            yield RefineStreamEvent(type="delta", text="Hello")
            yield RefineStreamEvent(type="delta", text=" world")

    processor.provider = StreamingProvider()
    finalized = []

    class DummyAdapter:
        def start_progressive_response(self, context, chat_id, ack_msg):
            return SimpleNamespace(accumulated_text="")

        async def push_progressive_delta(self, context, session, delta_text):
            session.accumulated_text += delta_text

        async def finalize_progressive_response(self, context, session, full_text):
            finalized.append(full_text)

    context = SimpleNamespace(bot_data={"delivery_adapter": DummyAdapter()})

    final_text = await processor.stream_refine_text(context, 1, SimpleNamespace(), "raw")

    assert final_text == "Hello world"
    assert finalized == ["📝 Trascrizione Completata\n🤖 Modello: gpt-4o-mini\n\nHello world"]


@pytest.mark.asyncio
async def test_resilient_provider_stream_success_resets_circuit_state():
    from bot.providers import ResilientProvider

    class StreamingProvider:
        supports_refine_streaming = True
        model_name = "test"

        async def transcribe_audio(self, file_path: str) -> str:
            return "raw"

        async def refine_text(self, raw_text: str) -> str:
            return raw_text

        async def stream_refine_text(self, raw_text: str):
            yield RefineStreamEvent(type="delta", text="ok")
            yield RefineStreamEvent(type="done", text="ok")

    provider = ResilientProvider(StreamingProvider(), provider_name="openai", failure_threshold=1, cooldown_seconds=60)

    events = [event async for event in provider.stream_refine_text("hello")]

    assert events[-1].type == "done"
    assert provider._failure_count == 0
    assert provider._opened_at == 0.0

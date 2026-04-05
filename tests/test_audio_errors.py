import pytest
import asyncio
from unittest.mock import Mock

from bot import constants as c
from bot.decorators.timeout import execute_with_timeout
from bot.exceptions import ConvertError, DownloadError, DownloadTimeout, ProviderCircuitOpen, TranscribeError
from bot.handlers.audio import AudioProcessor, _elapsed_ms


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

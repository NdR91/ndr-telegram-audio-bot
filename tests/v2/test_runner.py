from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import sys
from types import ModuleType

import pytest

from bot.transcription.adapters.openai import OpenAIAdapter
from bot.transcription.errors import ServiceFailure
from bot.transcription.models import ConfigRevision, StageResult
from bot.transcription.runner import TranscriptionRunner


class Pool:
    def __init__(self, adapter): self.adapter = adapter
    @asynccontextmanager
    async def lease(self, snapshot): yield self.adapter


class Adapter:
    def __init__(self, refinement="edited"): self.calls=[]; self.refinement=refinement
    def accepted_formats(self, model, role): return frozenset({"mp3"})
    async def audio_to_text(self, audio, model, instructions):
        self.calls.append(("audio", model)); return StageResult("spoken", model)
    async def transcribe(self, audio, model):
        self.calls.append(("transcribe", model)); return StageResult("spoken", model)
    async def refine(self, text, model, system, template):
        self.calls.append(("refine", model, text, system, template));
        if self.refinement is None: raise RuntimeError("synthetic failure")
        return StageResult(self.refinement, model)


def _snapshot(config_values, mode="two_stage"):
    from dataclasses import replace
    return ConfigRevision(3, replace(config_values, mode=mode, audio_model="chosen-audio",
        text_model=None if mode == "multimodal" else "chosen-text"))


async def test_multimodal_makes_one_generation(tmp_path, config_values):
    adapter=Adapter(); audio=tmp_path / "sample.mp3"; audio.write_bytes(b"synthetic")
    result=await TranscriptionRunner(Pool(adapter), tmp_path).run(_snapshot(config_values,"multimodal"),audio)
    assert adapter.calls == [("audio", "chosen-audio")]
    assert result.text == "spoken" and result.models == ("chosen-audio",)


async def test_two_stage_uses_selected_models_and_prompts(tmp_path, config_values):
    adapter=Adapter(); audio=tmp_path / "sample.mp3"; audio.write_bytes(b"synthetic")
    result=await TranscriptionRunner(Pool(adapter), tmp_path).run(_snapshot(config_values),audio)
    assert adapter.calls == [("transcribe", "chosen-audio"),
        ("refine", "chosen-text", "spoken", config_values.system_prompt, config_values.refine_template)]
    assert result.text == "edited" and result.models == ("chosen-audio", "chosen-text")


async def test_inflight_snapshot_keeps_models_and_prompts(tmp_path, config_values):
    from dataclasses import replace
    entered, release=asyncio.Event(), asyncio.Event()
    class Delayed(Adapter):
        async def transcribe(self, audio, model):
            self.calls.append(("transcribe", model)); entered.set(); await release.wait()
            return StageResult("spoken", model)
    adapter=Delayed(); audio=tmp_path / "sample.mp3"; audio.write_bytes(b"synthetic")
    old=_snapshot(config_values)
    task=asyncio.create_task(TranscriptionRunner(Pool(adapter), tmp_path).run(old,audio))
    await entered.wait()
    newer=ConfigRevision(old.number + 1, replace(old.values,
        audio_model="new-audio", text_model="new-text", system_prompt="new prompt"))
    assert old.values.audio_model == "chosen-audio"
    assert newer.values.audio_model == "new-audio"
    release.set(); result=await task
    assert result.models == ("chosen-audio", "chosen-text")


async def test_refinement_failure_returns_partial_transcript(tmp_path, config_values):
    adapter=Adapter(refinement=None); audio=tmp_path / "sample.mp3"; audio.write_bytes(b"synthetic")
    result=await TranscriptionRunner(Pool(adapter), tmp_path).run(_snapshot(config_values),audio)
    assert result.partial and result.text == "spoken" and result.models == ("chosen-audio",)


async def test_empty_transcription_is_failure(tmp_path, config_values):
    class Empty(Adapter):
        async def transcribe(self, audio, model): return StageResult("  ", model)
    audio=tmp_path / "sample.mp3"; audio.write_bytes(b"synthetic")
    with pytest.raises(ServiceFailure):
        await TranscriptionRunner(Pool(Empty()), tmp_path).run(_snapshot(config_values),audio)


async def test_conversion_only_when_needed_and_cleanup_on_failure(tmp_path, config_values, monkeypatch):
    source=tmp_path / "sample.opus"; source.write_bytes(b"synthetic")
    converted=[]
    async def convert(src, dst):
        converted.append((src, dst)); Path(dst).write_bytes(b"synthetic mp3")
    fake_ffmpeg=ModuleType("bot.transcription.ffmpeg"); fake_ffmpeg.convert_to_mp3=convert
    monkeypatch.setitem(sys.modules, "bot.transcription.ffmpeg", fake_ffmpeg)
    class Failing(Adapter):
        def accepted_formats(self, model, role): return frozenset({"mp3"})
        async def transcribe(self, audio, model):
            assert audio.suffix == ".mp3" and audio.exists()
            raise RuntimeError("synthetic failure")
    with pytest.raises(RuntimeError):
        await TranscriptionRunner(Pool(Failing()), tmp_path / "work").run(_snapshot(config_values), source)
    assert len(converted) == 1 and source.exists()
    assert list((tmp_path / "work").iterdir()) == []


async def test_openai_converts_telegram_ogg_voice_before_transcription(tmp_path, config_values, monkeypatch):
    from types import SimpleNamespace

    source=tmp_path / "voice.ogg"; source.write_bytes(b"synthetic OGG voice")
    converted=[]
    async def convert(src, dst):
        converted.append((src, dst))
        Path(dst).write_bytes(b"converted MP3 voice")
    fake_ffmpeg=ModuleType("bot.transcription.ffmpeg"); fake_ffmpeg.convert_to_mp3=convert
    monkeypatch.setitem(sys.modules, "bot.transcription.ffmpeg", fake_ffmpeg)

    uploads=[]
    class Client:
        class Audio:
            class Transcriptions:
                async def create(self, **kwargs):
                    uploads.append(kwargs["file"])
                    return SimpleNamespace(text="spoken")
            transcriptions=Transcriptions()
        audio=Audio()
        class Chat:
            class Completions:
                async def create(self, **kwargs):
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content="edited")
                    )])
            completions=Completions()
        chat=Chat()

    adapter=OpenAIAdapter("synthetic", Client())
    result=await TranscriptionRunner(Pool(adapter), tmp_path / "work").run(
        _snapshot(config_values), source
    )

    assert len(converted) == 1
    assert uploads[0][0].endswith(".mp3")
    assert uploads[0][1] == b"converted MP3 voice"
    assert result.text == "edited"
    assert source.exists() and list((tmp_path / "work").iterdir()) == []


async def test_conversion_derivative_removed_after_cancellation(tmp_path, config_values, monkeypatch):
    source=tmp_path / "sample.opus"; source.write_bytes(b"synthetic")
    ready=asyncio.Event()
    async def convert(src, dst):
        Path(dst).write_bytes(b"partial mp3"); ready.set(); await asyncio.Event().wait()
    fake_ffmpeg=ModuleType("bot.transcription.ffmpeg"); fake_ffmpeg.convert_to_mp3=convert
    monkeypatch.setitem(sys.modules, "bot.transcription.ffmpeg", fake_ffmpeg)
    task=asyncio.create_task(TranscriptionRunner(Pool(Adapter()), tmp_path / "work").run(_snapshot(config_values), source))
    await ready.wait(); task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert source.exists() and list((tmp_path / "work").iterdir()) == []


async def test_conversion_derivative_removed_after_stage_timeout(tmp_path, config_values, monkeypatch):
    from dataclasses import replace
    source=tmp_path / "sample.opus"; source.write_bytes(b"synthetic")
    async def convert(src, dst): Path(dst).write_bytes(b"synthetic mp3")
    fake_ffmpeg=ModuleType("bot.transcription.ffmpeg"); fake_ffmpeg.convert_to_mp3=convert
    monkeypatch.setitem(sys.modules, "bot.transcription.ffmpeg", fake_ffmpeg)
    class Slow(Adapter):
        def accepted_formats(self, model, role): return frozenset({"mp3"})
        async def transcribe(self, audio, model): await asyncio.Event().wait()
    snapshot=_snapshot(config_values)
    snapshot=ConfigRevision(snapshot.number, replace(snapshot.values, limits=replace(snapshot.values.limits, transcribe_timeout=1)))
    with pytest.raises(asyncio.TimeoutError):
        await TranscriptionRunner(Pool(Slow()), tmp_path / "work").run(snapshot, source)
    assert source.exists() and list((tmp_path / "work").iterdir()) == []

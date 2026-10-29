"""Configuration proofs and transient test results remain revision-bound."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from types import SimpleNamespace

from bot.transcription.configuration import ConfigurationService
from bot.transcription.errors import RevisionConflict
from bot.transcription.models import ProcessingResult
from bot.transcription.testing import ConfigurationTestService


def test_test_uses_same_runner_and_exact_revision(repo, config_values, ready_prerequisites, tmp_path):
    repo.put_credential("openai", "synthetic-key")
    configuration = ConfigurationService(repo, lambda: ready_prerequisites)
    revision = configuration.save_draft(None, config_values)
    seen = []

    class Runner:
        async def run(self, snapshot, audio):
            seen.append((snapshot, audio))
            return ProcessingResult("Synthetic output", False, ("whisper-1", "gpt-4o-mini"), 21, Decimal("0.01"))

    test_service = ConfigurationTestService(configuration, Runner())
    audio = tmp_path / "synthetic.wav"
    view = asyncio.run(test_service.run(revision.number, audio))
    assert view.status == "succeeded"
    assert seen == [(revision, audio)]
    assert test_service.result(view.id).result.text == "Synthetic output"
    activated = configuration.activate(revision.number)
    assert activated == revision


def test_saved_older_key_remains_testable_after_adding_another(repo, config_values, ready_prerequisites, tmp_path):
    repo.put_credential_with_state('openai', 'synthetic-first', 'openai_credential_version', saved_label='First')
    repo.put_credential_with_state('openai', 'synthetic-second', 'openai_credential_version', saved_label='Second')
    configuration = ConfigurationService(repo, lambda: ready_prerequisites)
    draft = configuration.save_draft(None, config_values)

    class Runner:
        async def run(self, snapshot, audio):
            assert snapshot.values.credential_version == 1
            return ProcessingResult('Synthetic text', False, ('model',), 1)

    result = asyncio.run(ConfigurationTestService(configuration, Runner()).run(draft.number, tmp_path / 'sample.wav'))
    assert result.status == 'succeeded'
    assert configuration.activate(draft.number) == draft


def test_changed_draft_rejects_old_test_and_partial_result_cannot_activate(
    repo, config_values, ready_prerequisites, tmp_path
):
    repo.put_credential("openai", "synthetic-key")
    configuration = ConfigurationService(repo, lambda: ready_prerequisites)
    revision = configuration.save_draft(None, config_values)

    class PartialRunner:
        async def run(self, snapshot, audio):
            return ProcessingResult("Partial transcript", True, ("whisper-1",), 15)

    service = ConfigurationTestService(configuration, PartialRunner())
    view = asyncio.run(service.run(revision.number, tmp_path / "audio.wav"))
    assert view.status == "failed"
    assert view.result.partial is True
    try:
        configuration.activate(revision.number)
    except Exception as exc:
        assert type(exc).__name__ == "TestRequired"
    else:
        raise AssertionError("partial result unexpectedly enabled activation")


def test_late_success_does_not_validate_edited_draft(repo, config_values, ready_prerequisites, tmp_path):
    repo.put_credential("openai", "synthetic-key")
    configuration = ConfigurationService(repo, lambda: ready_prerequisites)
    revision = configuration.save_draft(None, config_values)

    class EditingRunner:
        async def run(self, snapshot, audio):
            changed = type(snapshot.values)(**{**snapshot.values.__dict__, "audio_model": "new-synthetic-model"})
            configuration.save_draft(snapshot.number, changed)
            return ProcessingResult("Late result", False, ("whisper-1", "gpt-4o-mini"), 1)

    service = ConfigurationTestService(configuration, EditingRunner())
    view = asyncio.run(service.run(revision.number, tmp_path / "audio.wav"))
    assert view.status == "failed"
    with repo.read() as connection:
        receipt_count = connection.execute("SELECT COUNT(*) FROM test_receipts").fetchone()[0]
    assert receipt_count == 0


def test_credential_replacement_during_test_invalidates_success(repo, config_values, ready_prerequisites, tmp_path):
    repo.put_credential("openai", "synthetic-key")
    configuration = ConfigurationService(repo, lambda: ready_prerequisites)
    revision = configuration.save_draft(None, config_values)

    class ReplacingRunner:
        async def run(self, snapshot, audio):
            repo.put_credential("openai", "replacement-synthetic-key")
            return ProcessingResult("Late result", False, ("whisper-1", "gpt-4o-mini"), 1)

    service = ConfigurationTestService(configuration, ReplacingRunner())
    view = asyncio.run(service.run(revision.number, tmp_path / "audio.wav"))
    assert view.status == "failed"
    with repo.read() as connection:
        receipt_count = connection.execute("SELECT COUNT(*) FROM test_receipts").fetchone()[0]
    assert receipt_count == 0


def test_credential_replacement_after_test_requires_new_proof(repo, config_values, ready_prerequisites, tmp_path):
    repo.put_credential("openai", "synthetic-key")
    configuration = ConfigurationService(repo, lambda: ready_prerequisites)

    class Runner:
        async def run(self, snapshot, audio):
            return ProcessingResult("Synthetic result", False, ("whisper-1", "gpt-4o-mini"), 1)

    draft = configuration.save_draft(None, config_values)
    service = ConfigurationTestService(configuration, Runner())
    view = asyncio.run(service.run(draft.number, tmp_path / "audio.wav"))
    assert view.status == "succeeded"
    repo.put_credential("openai", "replacement-synthetic-key")
    try:
        configuration.activate(draft.number)
    except Exception as exc:
        assert type(exc).__name__ == "TestRequired"
    else:
        raise AssertionError("proof for old credential unexpectedly enabled activation")


def test_result_expires_from_memory(repo, config_values, ready_prerequisites, tmp_path):
    repo.put_credential("openai", "synthetic-key")
    configuration = ConfigurationService(repo, lambda: ready_prerequisites)
    revision = configuration.save_draft(None, config_values)
    now = [0.0]

    class Runner:
        async def run(self, snapshot, audio):
            return ProcessingResult("Synthetic transcript", False, ("whisper-1", "gpt-4o-mini"), 1)

    service = ConfigurationTestService(configuration, Runner(), clock=lambda: now[0])
    view = asyncio.run(service.run(revision.number, tmp_path / "audio.wav"))
    now[0] = 600
    assert service.result(view.id) is None


def test_bundled_sample_metadata_matches_packaged_audio():
    from hashlib import sha256
    import wave

    sample = ConfigurationTestService.sample_info()
    assert sample.expected_text == "The blue bird sings at sunrise."
    assert 1 <= sample.duration_seconds <= 4
    assert sample.path.is_file()
    assert sha256(sample.path.read_bytes()).hexdigest() == sample.sha256
    with wave.open(str(sample.path), "rb") as audio:
        assert audio.getnchannels() == 1
        assert audio.getframerate() == 16000
        assert abs(audio.getnframes() / audio.getframerate() - sample.duration_seconds) < 0.001


def test_bundled_sample_runs_from_temporary_copy_and_cleans_after_success(
    repo, config_values, ready_prerequisites, tmp_path
):
    from hashlib import sha256

    repo.put_credential("openai", "synthetic-key")
    configuration = ConfigurationService(repo, lambda: ready_prerequisites)
    revision = configuration.save_draft(None, config_values)
    temporary_dir = tmp_path / "sample-work"
    seen = []

    class Runner:
        async def run(self, snapshot, audio):
            assert audio.exists()
            seen.append((snapshot, audio, sha256(audio.read_bytes()).hexdigest()))
            return ProcessingResult("Synthetic transcript", False, ("model",), 1)

    service = ConfigurationTestService(configuration, Runner())
    view = asyncio.run(service.run_bundled_sample(revision.number, temporary_dir))
    packaged = service.sample_info()

    assert view.status == "succeeded"
    assert seen[0][0] == revision
    assert seen[0][1] != packaged.path
    assert seen[0][2] == packaged.sha256
    assert list(temporary_dir.iterdir()) == []


def test_bundled_sample_cleans_temporary_copy_after_runner_failure(
    repo, config_values, ready_prerequisites, tmp_path
):
    repo.put_credential("openai", "synthetic-key")
    configuration = ConfigurationService(repo, lambda: ready_prerequisites)
    revision = configuration.save_draft(None, config_values)
    temporary_dir = tmp_path / "sample-work"

    class Runner:
        async def run(self, snapshot, audio):
            assert audio.exists()
            raise RuntimeError("synthetic failure")

    view = asyncio.run(ConfigurationTestService(configuration, Runner()).run_bundled_sample(
        revision.number, temporary_dir
    ))
    assert view.status == "failed"
    assert list(temporary_dir.iterdir()) == []


def test_bundled_sample_cleans_temporary_copy_after_cancellation(
    repo, config_values, ready_prerequisites, tmp_path
):
    repo.put_credential("openai", "synthetic-key")
    configuration = ConfigurationService(repo, lambda: ready_prerequisites)
    revision = configuration.save_draft(None, config_values)
    temporary_dir = tmp_path / "sample-work"
    started = asyncio.Event()

    class Runner:
        async def run(self, snapshot, audio):
            assert audio.exists()
            started.set()
            await asyncio.Event().wait()

    async def run_and_cancel():
        task = asyncio.create_task(ConfigurationTestService(configuration, Runner()).run_bundled_sample(
            revision.number, temporary_dir
        ))
        await started.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return
        raise AssertionError("sample test should propagate cancellation")

    asyncio.run(run_and_cancel())
    assert list(temporary_dir.iterdir()) == []


def test_bundled_sample_expected_text_is_not_written_to_database(
    repo, config_values, ready_prerequisites, tmp_path
):
    sample = ConfigurationTestService.sample_info()
    repo.put_credential("openai", "synthetic-key")
    configuration = ConfigurationService(repo, lambda: ready_prerequisites)
    revision = configuration.save_draft(None, config_values)

    class Runner:
        async def run(self, snapshot, audio):
            return ProcessingResult("Synthetic transcript", False, ("model",), 1)

    service = ConfigurationTestService(configuration, Runner())
    view = asyncio.run(service.run_bundled_sample(revision.number, tmp_path))
    with repo.read() as connection:
        rows = connection.execute("SELECT * FROM test_receipts").fetchall()
        stored = repr([tuple(row) for row in rows])
    assert view.status == "succeeded"
    assert sample.expected_text not in stored
    assert sample.expected_text not in repr(view)
    with repo.read() as connection:
        row = connection.execute("SELECT COUNT(*) FROM test_receipts").fetchone()
    assert row[0] == 1


def test_bundled_sample_uses_real_runner_and_openai_adapter_with_mock_client(
    repo, config_values, ready_prerequisites, tmp_path
):
    from bot.transcription.adapters.openai import OpenAIAdapter
    from bot.transcription.runner import TranscriptionRunner

    repo.put_credential("openai", "synthetic-key")
    configuration = ConfigurationService(repo, lambda: ready_prerequisites)
    revision = configuration.save_draft(None, config_values)
    uploads = []
    refinement_requests = []

    class MockClient:
        class Audio:
            class Transcriptions:
                async def create(self, **kwargs):
                    uploads.append(kwargs["file"])
                    return SimpleNamespace(text="synthetic recognized words")
            transcriptions = Transcriptions()
        audio = Audio()

        class Chat:
            class Completions:
                async def create(self, **kwargs):
                    refinement_requests.append(kwargs)
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content="synthetic refined words")
                    )])
            completions = Completions()
        chat = Chat()

    class Mp3Adapter(OpenAIAdapter):
        def accepted_formats(self, model, role):
            return frozenset({"mp3"})

    adapter = Mp3Adapter("synthetic-key", MockClient())

    class Pool:
        @asynccontextmanager
        async def lease(self, snapshot):
            yield adapter

    runner = TranscriptionRunner(Pool(), tmp_path / "runner-work")
    service = ConfigurationTestService(configuration, runner)
    sample = service.sample_info()
    view = asyncio.run(service.run_bundled_sample(revision.number, tmp_path / "sample-work"))

    assert view.status == "succeeded", f"sample test failed in {view.diagnostics!r}: {view.safe_error}"
    assert view.result.text == "synthetic refined words"
    assert uploads and uploads[0][0].endswith(".mp3")
    assert uploads[0][1].startswith(b"ID3")
    assert "synthetic recognized words" in refinement_requests[0]["messages"][1]["content"]
    assert sample.expected_text not in repr((uploads, refinement_requests))
    assert list((tmp_path / "runner-work").iterdir()) == []
    assert list((tmp_path / "sample-work").iterdir()) == []

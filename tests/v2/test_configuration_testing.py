"""Configuration proofs and transient test results remain revision-bound."""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
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
    with repo.read() as connection:
        row = connection.execute("SELECT COUNT(*) FROM test_receipts").fetchone()
    assert row[0] == 1

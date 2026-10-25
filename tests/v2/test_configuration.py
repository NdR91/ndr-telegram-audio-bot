from dataclasses import replace
from datetime import datetime, timezone
import threading

import pytest

from bot.transcription.configuration import ConfigurationService
from bot.transcription.errors import (
    ConfigurationInvalid,
    PrerequisiteMissing,
    RevisionConflict,
    TestRequired,
)
from bot.transcription.models import TestReceipt
from bot.transcription.prompts import render_refinement


VALIDATOR = "v2-1"


def make_service(repo, ready_prerequisites):
    return ConfigurationService(repo, lambda: ready_prerequisites)


def save_initial(service, repo, config_values):
    repo.put_credential(config_values.service, "fake-provider-key")
    return service.save_draft(None, config_values)


def receipt(revision, *, success=True, validator=VALIDATOR, credential_version=1, id=None):
    return TestReceipt(
        id=id or f"receipt-{revision}",
        revision=revision,
        credential_version=credential_version,
        validator_version=validator,
        success=success,
        models=("whisper-1", "gpt-4o-mini"),
        finished_at=datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc),
    )


def test_activate_requires_matching_successful_receipt(repo, config_values, ready_prerequisites):
    service = make_service(repo, ready_prerequisites)
    draft = save_initial(service, repo, config_values)

    with pytest.raises(TestRequired):
        service.activate(draft.number)

    service.record_test(receipt(draft.number))
    assert service.activate(draft.number) == draft

    next_draft = service.save_draft(
        draft.number, replace(config_values, audio_model="another-audio")
    )
    service.record_test(receipt(next_draft.number, id="next-success"))
    service.record_test(receipt(next_draft.number, success=False, id="later-failure"))
    with pytest.raises(TestRequired):
        service.activate(next_draft.number)


def test_stale_write_and_late_test_cannot_activate_new_draft(
    repo, config_values, ready_prerequisites
):
    service = make_service(repo, ready_prerequisites)
    first = save_initial(service, repo, config_values)
    second = service.save_draft(first.number, replace(config_values, audio_model="gpt-4o-audio"))

    with pytest.raises(RevisionConflict):
        service.save_draft(first.number, replace(config_values, audio_model="another-model"))

    service.record_test(receipt(first.number, id="late-old"))
    with pytest.raises(TestRequired):
        service.activate(second.number)
    service.record_test(receipt(second.number, validator="old-validator", id="old-validator"))
    with pytest.raises(TestRequired):
        service.activate(second.number)
    assert service.active() is None


@pytest.mark.parametrize(
    "change",
    [
        {"service": "gemini", "credential_version": 1},
        {"credential_version": 2},
        {"audio_model": "new-audio"},
        {"mode": "multimodal", "text_model": None},
        {"system_prompt": "new prompt"},
        {"refine_template": "Correct: {raw_text}"},
    ],
)
def test_each_material_change_invalidates_test(
    repo, config_values, ready_prerequisites, change
):
    service = make_service(repo, ready_prerequisites)
    first = save_initial(service, repo, config_values)
    service.record_test(receipt(first.number))
    if change.get("service") == "gemini":
        repo.put_credential("gemini", "fake-gemini-key")
    if change.get("credential_version") == 2:
        repo.put_credential("openai", "fake-provider-key-two")
    updated = service.save_draft(first.number, replace(config_values, **change))

    assert updated.number != first.number
    with pytest.raises(TestRequired):
        service.activate(updated.number)


def test_last_active_and_previous_survive_failed_activation(
    repo, config_values, ready_prerequisites
):
    service = make_service(repo, ready_prerequisites)
    first = save_initial(service, repo, config_values)
    service.record_test(receipt(first.number))
    service.activate(first.number)

    repo.put_credential("openai", "fake-provider-key-two")
    second = service.save_draft(
        first.number,
        replace(config_values, credential_version=2, audio_model="new-audio"),
    )
    service.record_test(
        receipt(second.number, success=False, credential_version=2, id="failed")
    )
    with pytest.raises(TestRequired):
        service.activate(second.number)

    assert service.active() == first
    assert service.restore_previous() is None


def test_restore_previous_swaps_versions_only_with_prerequisites(
    repo, config_values, ready_prerequisites
):
    service = make_service(repo, ready_prerequisites)
    first = save_initial(service, repo, config_values)
    service.record_test(receipt(first.number))
    service.activate(first.number)
    repo.put_credential("openai", "fake-provider-key-two")
    second = service.save_draft(
        first.number,
        replace(config_values, credential_version=2, audio_model="new-audio"),
    )
    service.record_test(receipt(second.number, credential_version=2))
    service.activate(second.number)

    assert service.restore_previous() == first
    assert service.active() == first
    assert service.restore_previous() == second

    unavailable = ConfigurationService(repo, lambda: replace(ready_prerequisites, owner=False))
    with pytest.raises(PrerequisiteMissing):
        unavailable.restore_previous()


def test_secret_gc_keeps_active_previous_draft_and_leases(
    repo, config_values, ready_prerequisites
):
    service = make_service(repo, ready_prerequisites)
    first = save_initial(service, repo, config_values)
    service.record_test(receipt(first.number))
    service.activate(first.number)
    repo.put_credential("openai", "fake-provider-key-two")
    second = service.save_draft(
        first.number,
        replace(config_values, credential_version=2, audio_model="new-audio"),
    )
    service.record_test(receipt(second.number, credential_version=2))
    service.activate(second.number)
    repo.put_credential("openai", "unreferenced-key")
    version3 = 3
    version4 = repo.put_credential("openai", "latest-unselected-key")
    assert version4 == 4

    with repo.credential_lease("openai", version3):
        assert repo.prune_credentials() == 0
        assert repo.read_credential("openai", version3) == "unreferenced-key"
    assert repo.prune_credentials() == 1
    assert repo.credential_versions("openai") == (1, 2, version4)


def test_literal_raw_text_substitution():
    assert render_refinement("Transcript: {raw_text}; literal {name}", "{nome}: no, 12") == (
        "Transcript: {nome}: no, 12; literal {name}"
    )
    with pytest.raises(ValueError):
        render_refinement("Missing transcript placeholder", "text")


def test_configuration_requires_role_compatible_models(repo, config_values):
    service = ConfigurationService(repo, lambda: None)
    repo.put_credential("openai", "fake-provider-key")

    with pytest.raises(ConfigurationInvalid):
        service.save_draft(None, replace(config_values, text_model=None))
    with pytest.raises(ConfigurationInvalid):
        service.save_draft(
            None,
            replace(config_values, mode="multimodal", text_model="gpt-4o-mini"),
        )


def test_superseded_drafts_and_receipts_are_removed(
    repo, config_values, ready_prerequisites
):
    service = make_service(repo, ready_prerequisites)
    current = save_initial(service, repo, config_values)
    for number in range(2, 7):
        service.record_test(receipt(current.number, id=f"receipt-{current.number}"))
        current = service.save_draft(
            current.number, replace(config_values, audio_model=f"audio-{number}")
        )
    service.record_test(receipt(current.number, id=f"receipt-{current.number}"))

    revisions = repo.connection.execute(
        "SELECT revision FROM config_revisions"
    ).fetchall()
    assert [row[0] for row in revisions] == [6]
    assert repo.connection.execute("SELECT COUNT(*) FROM test_receipts").fetchone()[0] == 1


def test_active_read_waits_until_transaction_finishes(
    repo, config_values, ready_prerequisites
):
    service = make_service(repo, ready_prerequisites)
    first = save_initial(service, repo, config_values)
    service.record_test(receipt(first.number))
    service.activate(first.number)
    second = service.save_draft(
        first.number, replace(config_values, audio_model="second-audio")
    )
    read_started = threading.Event()
    read_finished = threading.Event()
    observed = []

    def read_active():
        read_started.set()
        observed.append(service.active())
        read_finished.set()

    with pytest.raises(RuntimeError):
        with repo.transaction() as connection:
            connection.execute(
                "UPDATE config_references SET revision = ? WHERE reference_type = 'active'",
                (second.number,),
            )
            reader = threading.Thread(target=read_active)
            reader.start()
            assert read_started.wait(1)
            assert not read_finished.wait(0.05)
            raise RuntimeError("synthetic rollback")
    reader.join(timeout=1)

    assert observed == [first]

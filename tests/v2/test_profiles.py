from dataclasses import replace
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import pytest

from bot.transcription.errors import ConfigurationInvalid, RevisionConflict
from bot.transcription.models import TestReceipt
from bot.transcription.profiles import ProfileService


def _receipt(revision, credential_version=1, *, success=True, validator="v2-1", id="proof"):
    return TestReceipt(
        id=id,
        revision=revision,
        credential_version=credential_version,
        validator_version=validator,
        success=success,
        models=("audio-model",),
        finished_at=datetime(2026, 10, 29, tzinfo=timezone.utc),
    )


def test_profile_revision_roles_proofs_and_snapshot_leases(repo, config_values):
    repo.put_credential("openai", "synthetic-key")
    service = ProfileService(repo)
    primary = service.create("Primary", config_values)
    fallback = service.create("Fallback", replace(config_values, audio_model="fallback-audio"))

    service.assign_role("primary", primary.profile_id, primary.revision, expected_generation=0)
    with pytest.raises(ConfigurationInvalid):
        service.assign_role("fallback", fallback.profile_id, fallback.revision, expected_generation=1)

    service.record_test(fallback.profile_id, _receipt(fallback.revision))
    service.assign_role("fallback", fallback.profile_id, fallback.revision, expected_generation=1)
    assert service.has_valid_receipt(fallback.profile_id, fallback.revision, 1, "v2-1")
    assert not service.has_valid_receipt(fallback.profile_id, fallback.revision, 1, "v2-2")

    with service.lease_processing_snapshot() as snapshot:
        assert snapshot.primary.configuration.number == primary.revision
        assert snapshot.primary.configuration.values.audio_model == "whisper-1"
        assert snapshot.fallback.configuration.values.audio_model == "fallback-audio"
        with pytest.raises(ConfigurationInvalid):
            service.delete(primary.profile_id, expected_generation=1)

    service.record_test(
        fallback.profile_id,
        _receipt(fallback.revision, success=False, id="later-failure"),
    )
    with service.lease_processing_snapshot() as snapshot:
        assert snapshot.fallback is None

    changed = service.save_revision(
        primary.profile_id, primary.revision, expected_generation=1,
        name="Primary renamed", values=replace(config_values, audio_model="new-audio"),
    )
    with pytest.raises(RevisionConflict):
        service.save_revision(
            primary.profile_id, primary.revision, expected_generation=1,
            name="Stale", values=config_values,
        )
    assert changed.revision > primary.revision
    assert not service.has_valid_receipt(primary.profile_id, changed.revision, 1, "v2-1")


def test_snapshot_and_profile_lease_are_acquired_atomically(repo, config_values):
    repo.put_credential("openai", "synthetic-key")
    service = ProfileService(repo)
    profile = service.create("Primary", config_values)
    service.assign_role("primary", profile.profile_id, profile.revision, expected_generation=0)

    with service.lease_processing_snapshot() as snapshot:
        assert snapshot is not None
        assert repo._profile_leases[(profile.profile_id, profile.revision)] == 1
        assert repo._credential_leases[("openai", 1)] == 1
        with ThreadPoolExecutor(max_workers=1) as executor:
            deletion = executor.submit(
                service.delete, profile.profile_id, expected_generation=1
            )
            with pytest.raises(ConfigurationInvalid):
                deletion.result(timeout=2)
    assert not repo._profile_leases
    assert not repo._credential_leases


def test_stale_role_generation_does_not_change_assignment(repo, config_values):
    repo.put_credential("openai", "synthetic-key")
    service = ProfileService(repo)
    first = service.create("First", config_values)
    second = service.create("Second", replace(config_values, audio_model="other"))
    service.assign_role("primary", first.profile_id, first.revision, expected_generation=0)

    with pytest.raises(RevisionConflict):
        service.assign_role("primary", second.profile_id, second.revision, expected_generation=0)
    with service.lease_processing_snapshot() as snapshot:
        assert snapshot.primary.profile_id == first.profile_id


def test_role_assignment_rejects_a_revision_superseded_by_profile_edit(repo, config_values):
    repo.put_credential("openai", "synthetic-key")
    service = ProfileService(repo)
    profile = service.create("Primary", config_values)
    service.save_revision(
        profile.profile_id, profile.revision, expected_generation=1,
        name=profile.name, values=replace(config_values, audio_model="new-audio"),
    )

    with pytest.raises(RevisionConflict):
        service.assign_role(
            "primary", profile.profile_id, profile.revision, expected_generation=0
        )
    with service.lease_processing_snapshot() as snapshot:
        assert snapshot is None


def test_failed_receipt_does_not_make_profile_eligible_for_fallback(repo, config_values):
    repo.put_credential("openai", "synthetic-key")
    service = ProfileService(repo)
    profile = service.create("Fallback", config_values)
    service.record_test(profile.profile_id, _receipt(profile.revision, success=False))

    assert not service.has_valid_receipt(profile.profile_id, profile.revision, 1, "v2-1")
    with pytest.raises(ConfigurationInvalid):
        service.assign_role("fallback", profile.profile_id, profile.revision, expected_generation=0)

    with service.lease_processing_snapshot() as snapshot:
        assert snapshot is None


def test_duplicate_profile_names_are_case_insensitive(repo, config_values):
    repo.put_credential("openai", "synthetic-key")
    service = ProfileService(repo)
    service.create("Primary", config_values)

    with pytest.raises(ConfigurationInvalid):
        service.create("primary", config_values)


def test_profile_names_are_unique_after_unicode_casefold(repo, config_values):
    repo.put_credential("openai", "synthetic-key")
    service = ProfileService(repo)
    service.create("Straße", config_values)

    with pytest.raises(ConfigurationInvalid):
        service.create("STRASSE", config_values)


def test_catalog_duplicate_and_global_role_generation(repo, config_values):
    repo.put_credential("openai", "synthetic-key")
    service = ProfileService(repo)
    source = service.create("Source", config_values)
    service.record_test(source.profile_id, _receipt(source.revision))
    before = service.list_profiles()
    assert before[0].verification_state == "verified"
    assert before[0].last_test_success is True

    duplicate = service.duplicate(
        source.profile_id, "Copy", expected_generation=before[0].generation
    )
    assert duplicate.values == source.values
    assert duplicate.revision != source.revision
    assert not service.has_valid_receipt(duplicate.profile_id, duplicate.revision, 1)
    assert len(service.list_profiles()) == 2

    assert service.roles_generation() == 0
    service.assign_role("primary", source.profile_id, source.revision, expected_generation=0)
    assert service.roles_generation() == 1
    with pytest.raises(RevisionConflict):
        service.assign_role("primary", duplicate.profile_id, duplicate.revision, expected_generation=0)


def test_catalog_reports_active_revision_when_profile_has_no_draft(repo, config_values):
    repo.put_credential("openai", "synthetic-key")
    service = ProfileService(repo)
    profile = service.create("Primary", config_values)
    service.record_test(profile.profile_id, _receipt(profile.revision))
    service.assign_role("primary", profile.profile_id, profile.revision, expected_generation=0)
    with repo.transaction() as connection:
        connection.execute("DELETE FROM profile_drafts WHERE profile_id=?", (profile.profile_id,))

    entry = service.list_profiles()[0]
    assert entry.current_revision is None
    assert entry.role_revision == profile.revision
    assert entry.verification_state == "verified"


def test_catalog_reports_assigned_revision_proof_separately_from_current_draft(repo, config_values):
    repo.put_credential("openai", "synthetic-key")
    service = ProfileService(repo)
    profile = service.create("Fallback", config_values)
    service.record_test(profile.profile_id, _receipt(profile.revision))
    service.assign_role("fallback", profile.profile_id, profile.revision, expected_generation=0)
    service.record_test(
        profile.profile_id,
        _receipt(profile.revision, success=False, id="later-failure"),
    )
    latest = service.save_revision(
        profile.profile_id, profile.revision, expected_generation=1,
        name=profile.name, values=replace(config_values, audio_model="new-audio"),
    )
    service.record_test(profile.profile_id, _receipt(latest.revision, id="new-proof"))
    primary = service.create("Primary", replace(config_values, audio_model="primary-audio"))
    service.record_test(primary.profile_id, _receipt(primary.revision, id="primary-proof"))
    service.assign_role("primary", primary.profile_id, primary.revision, expected_generation=1)

    entry = service.list_profiles()[0]
    assert entry.verification_state == "verified"
    assert entry.role_revision == profile.revision
    assert entry.role_verification_state == "retest"
    with service.lease_processing_snapshot() as snapshot:
        assert snapshot is not None
        assert snapshot.fallback is None

import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bot.diagnostics import DiagnosticEvent, DiagnosticsService
from bot.transcription.fallback import FallbackEvent


def test_diagnostics_retains_at_most_100_events_and_7_days(repo):
    service = DiagnosticsService(repo)
    now = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    service._now = lambda: now
    service.record(DiagnosticEvent(now - timedelta(days=8), "telegram", "success", None, (), 4, None))
    for index in range(105):
        service.record(DiagnosticEvent(now - timedelta(seconds=index), "telegram", "success", None, (), index, None))
    recent = service.recent()
    assert len(recent) == 100
    assert all(now - event.at <= timedelta(days=7) for event in recent)


def test_error_body_text_and_credentials_never_persist(repo):
    service = DiagnosticsService(repo)
    service.record(DiagnosticEvent(
        datetime.now(timezone.utc), "provider", "error", 4,
        ("openai:gpt-4o-mini",), 123,
        "provider_error synthetic-provider response-body synthetic-api-key",
    ))
    with repo.read() as connection:
        row = dict(connection.execute("SELECT * FROM diagnostic_events").fetchone())
        raw = repr(row)
    assert "secret body" not in raw
    assert "synthetic-api-key" not in raw
    assert "provider_error" not in raw
    assert row["error_code"] is None


def test_fallback_diagnostic_persists_only_profile_ids_and_normalized_metadata(repo):
    service = DiagnosticsService(repo)
    service.record_fallback(FallbackEvent(
        datetime.now(timezone.utc), 12, 27, "refinement", "provider_rate_limited",
        "succeeded",
    ))
    with repo.read() as connection:
        row = dict(connection.execute("SELECT * FROM diagnostic_events").fetchone())
    assert row["primary_profile_id"] == 12
    assert row["fallback_profile_id"] == 27
    assert row["failure_stage"] == "refinement"
    assert row["failure_category"] == "provider_rate_limited"
    assert row["outcome"] == "succeeded"
    assert row["status"] == "success"
    assert row["models_json"] == "[]"
    event = service.recent()[0]
    assert event.primary_profile_id == 12
    assert event.fallback_profile_id == 27
    assert event.failure_stage == "refinement"
    assert event.failure_category == "provider_rate_limited"
    assert event.outcome == "succeeded"


def test_primary_processing_diagnostic_persists_normalized_metadata_without_content(repo):
    service = DiagnosticsService(repo)
    service.record_fallback(FallbackEvent(
        datetime.now(timezone.utc), 12, None, "transcription", "empty_response",
        "primary_failed", final_failure_stage="transcription",
        final_failure_code="empty_response",
    ))
    with repo.read() as connection:
        row = dict(connection.execute("SELECT * FROM diagnostic_events").fetchone())
    assert row["primary_profile_id"] == 12
    assert row["fallback_profile_id"] is None
    assert row["failure_stage"] == "transcription"
    assert row["failure_category"] == "empty_response"
    assert row["outcome"] == "primary_failed"
    assert row["status"] == "error"
    assert "synthetic transcript" not in repr(row)


def test_primary_success_diagnostic_has_profile_and_outcome(repo):
    service = DiagnosticsService(repo)
    service.record_fallback(FallbackEvent(
        datetime.now(timezone.utc), 12, None, None, None, "primary_succeeded",
    ))
    event = service.recent()[0]
    assert event.primary_profile_id == 12
    assert event.fallback_profile_id is None
    assert event.failure_stage is None
    assert event.failure_category is None
    assert event.outcome == "primary_succeeded"
    assert event.status == "success"


def test_notification_failure_diagnostic_contains_no_notice_payload(repo):
    service = DiagnosticsService(repo)
    at = datetime.now(timezone.utc)
    service.record_notice_failure(at)
    event = service.recent()[0]
    assert event.at == at
    assert event.stage == "telegram"
    assert event.status == "error"
    assert event.error_code == "telegram"
    assert event.primary_profile_id is None
    assert event.models == ()


def test_health_distinguishes_configured_running_and_verified_receiving(repo):
    class Configuration:
        current = None

        def active(self):
            return self.current

    class Runtime:
        is_running = False

    config = Configuration()
    runtime = Runtime()
    service = DiagnosticsService(repo, configuration=config, runtime=runtime)
    assert service.health() == {
        "configured": False, "running": False, "verified": False, "receiving": False,
    }
    repo.put_credential("openai", "synthetic-api-key")
    from bot.transcription.configuration import ConfigurationService
    from bot.transcription.models import ConfigInput, Prerequisites, RuntimeLimits
    config_service = ConfigurationService(
        repo, lambda: Prerequisites(web_admin=True, telegram_valid=True, owner=True)
    )
    revision = config_service.save_draft(None, ConfigInput(
        service="openai", mode="two_stage", audio_model="whisper-1",
        text_model="gpt-4o-mini", credential_version=1, system_prompt="",
        refine_template="{raw_text}", limits=RuntimeLimits(),
    ))
    from bot.transcription.configuration import VALIDATOR_VERSION
    from bot.transcription.models import TestReceipt
    config_service.record_test(TestReceipt(
        "test-id", revision.number, 1, VALIDATOR_VERSION, True,
        ("whisper-1", "gpt-4o-mini"), datetime.now(timezone.utc),
    ))
    config_service.activate(revision.number)
    config.current = config_service.active()
    runtime.is_running = True
    with repo.transaction() as connection:
        connection.execute("INSERT INTO acl_entries(kind, entry_id) VALUES ('group', -2001)")
        connection.execute(
            "INSERT INTO setup_state(setup_key, setup_value) VALUES ('access_group_received:-2001', ?) ",
            (datetime.now(timezone.utc).isoformat(),),
        )
    assert service.health() == {
        "configured": True, "running": True, "verified": True, "receiving": True,
    }
    with repo.transaction() as connection:
        connection.execute("DELETE FROM acl_entries WHERE kind='group' AND entry_id=-2001")
    assert service.health() == {
        "configured": True, "running": True, "verified": True, "receiving": False,
    }

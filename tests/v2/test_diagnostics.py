import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bot.diagnostics import DiagnosticEvent, DiagnosticsService


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

"""Bounded operational diagnostics containing normalized metadata only."""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from bot.database.v2_repository import V2Repository

_STAGES = {"admission", "configuration", "download", "conversion", "transcription",
           "refinement", "provider", "telegram"}
_STATUSES = {"success", "partial", "error"}
_ERROR_CODES = {"quota", "credential", "model", "format", "telegram", "temporary",
                "provider", "provider_error", "configuration", "unknown"}
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$")


@dataclass(frozen=True)
class DiagnosticEvent:
    at: datetime
    stage: str
    status: str
    revision: int | None
    models: tuple[str, ...]
    duration_ms: int | None
    error_code: str | None


class DiagnosticsService:
    def __init__(self, repo: V2Repository, configuration=None, runtime=None, now=None):
        self.repo = repo
        self.configuration = configuration
        self.runtime = runtime
        self._now = now or (lambda: datetime.now(timezone.utc))

    def record(self, event: DiagnosticEvent) -> None:
        at = _utc(event.at)
        if event.stage not in _STAGES or event.status not in _STATUSES:
            raise ValueError("Diagnostic event is invalid")
        if event.revision is not None and (isinstance(event.revision, bool) or event.revision <= 0):
            raise ValueError("Diagnostic revision is invalid")
        if event.duration_ms is not None and (isinstance(event.duration_ms, bool) or event.duration_ms < 0):
            raise ValueError("Diagnostic duration is invalid")
        models = tuple(model for model in event.models if isinstance(model, str) and _MODEL_ID.fullmatch(model))[:4]
        error = event.error_code if event.error_code in _ERROR_CODES else None
        with self.repo.transaction() as connection:
            self._prune(connection, _utc(self._now()))
            connection.execute(
                "INSERT INTO diagnostic_events(at, stage, status, revision, models_json, duration_ms, error_code) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (at.isoformat(), event.stage, event.status, event.revision,
                 json.dumps(models), event.duration_ms, error),
            )
            connection.execute(
                "DELETE FROM diagnostic_events WHERE event_id NOT IN "
                "(SELECT event_id FROM diagnostic_events ORDER BY at DESC, event_id DESC LIMIT 100)"
            )

    def recent(self) -> list[DiagnosticEvent]:
        cutoff = _utc(self._now()) - timedelta(days=7)
        with self.repo.transaction() as connection:
            self._prune(connection, _utc(self._now()))
            rows = connection.execute(
                "SELECT at, stage, status, revision, models_json, duration_ms, error_code "
                "FROM diagnostic_events WHERE at >= ? ORDER BY at DESC, event_id DESC LIMIT 100",
                (cutoff.isoformat(),),
            ).fetchall()
        result = []
        for row in rows:
            try:
                models = tuple(json.loads(row["models_json"]))
                at = datetime.fromisoformat(row["at"])
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
            result.append(DiagnosticEvent(at, row["stage"], row["status"], row["revision"],
                                          models, row["duration_ms"], row["error_code"]))
        return result

    def health(self) -> dict[str, bool]:
        active = self.configuration.active() if self.configuration is not None else None
        running = bool(self.runtime and getattr(self.runtime, "is_running", False))
        verified = False
        if active is not None:
            with self.repo.read() as connection:
                verified = connection.execute(
                    "SELECT 1 FROM test_receipts WHERE revision=? AND credential_version=? "
                    "AND success=1 LIMIT 1",
                    (active.number, active.values.credential_version),
                ).fetchone() is not None
                receiving = _has_current_group_receipt(connection)
        else:
            with self.repo.read() as connection:
                receiving = _has_current_group_receipt(connection)
        return {"configured": active is not None, "running": running,
                "verified": verified, "receiving": receiving}

    @staticmethod
    def _prune(connection, now: datetime) -> None:
        connection.execute("DELETE FROM diagnostic_events WHERE at < ?", ((now - timedelta(days=7)).isoformat(),))
        connection.execute(
            "DELETE FROM diagnostic_events WHERE event_id NOT IN "
            "(SELECT event_id FROM diagnostic_events ORDER BY at DESC, event_id DESC LIMIT 100)"
        )


def _has_current_group_receipt(connection) -> bool:
    groups = [str(row[0]) for row in connection.execute(
        "SELECT entry_id FROM acl_entries WHERE kind='group'"
    ).fetchall()]
    if not groups:
        return False
    keys = [f"access_group_received:{entry_id}" for entry_id in groups]
    placeholders = ",".join("?" for _ in keys)
    return connection.execute(
        f"SELECT 1 FROM setup_state WHERE setup_key IN ({placeholders}) "
        "AND setup_value IS NOT NULL AND setup_value != '' LIMIT 1",
        keys,
    ).fetchone() is not None


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Diagnostic timestamps must include a timezone")
    return value.astimezone(timezone.utc)

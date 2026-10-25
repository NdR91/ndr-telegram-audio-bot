"""Revision-checked drafts and atomic V2 configuration activation."""

import json
import sqlite3
from typing import Callable

from bot.database.v2_repository import V2Repository
from bot.transcription.errors import (
    ConfigurationInvalid,
    PrerequisiteMissing,
    RevisionConflict,
    TestRequired,
)
from bot.transcription.models import (
    ConfigInput,
    ConfigRevision,
    Prerequisites,
    RuntimeLimits,
    TestReceipt,
)

VALIDATOR_VERSION = "v2-1"
_SERVICES = {"openai", "gemini", "openrouter"}


class ConfigurationService:
    """Persist configuration revisions and require current proof to activate."""

    def __init__(
        self,
        repo: V2Repository,
        readiness: Callable[[], Prerequisites],
    ):
        self._repo = repo
        self._readiness = readiness

    def draft(self) -> ConfigRevision | None:
        with self._repo.read() as connection:
            row = connection.execute(
                "SELECT r.* FROM config_references x "
                "JOIN config_revisions r ON r.revision = x.revision "
                "WHERE x.reference_type = 'draft'"
            ).fetchone()
        return _revision_from_row(row) if row else None

    def active(self) -> ConfigRevision | None:
        with self._repo.read() as connection:
            row = connection.execute(
                "SELECT r.* FROM config_references x "
                "JOIN config_revisions r ON r.revision = x.revision "
                "WHERE x.reference_type = 'active'"
            ).fetchone()
        return _revision_from_row(row) if row else None

    def save_draft(
        self, expected_revision: int | None, values: ConfigInput
    ) -> ConfigRevision:
        _validate_config(values)
        with self._repo.transaction() as connection:
            current = connection.execute(
                "SELECT revision FROM config_references "
                "WHERE reference_type = 'draft'"
            ).fetchone()
            if current is None:
                current = connection.execute(
                    "SELECT revision FROM config_references "
                    "WHERE reference_type = 'active'"
                ).fetchone()
            current_revision = int(current[0]) if current else None
            if current_revision != expected_revision:
                raise RevisionConflict("Configuration changed; reload the latest revision")
            credential = connection.execute(
                "SELECT 1 FROM credentials WHERE service = ? AND version = ?",
                (values.service, values.credential_version),
            ).fetchone()
            if credential is None:
                raise ConfigurationInvalid("Selected credential version is unavailable")
            row = connection.execute(
                "SELECT next_revision FROM revision_sequence WHERE singleton = 1"
            ).fetchone()
            number = int(row[0])
            connection.execute(
                "INSERT INTO config_revisions "
                "(revision, service, mode, audio_model, text_model, credential_version, "
                "system_prompt, refine_template, limits_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    number,
                    values.service,
                    values.mode,
                    values.audio_model,
                    values.text_model,
                    values.credential_version,
                    values.system_prompt,
                    values.refine_template,
                    json.dumps(values.limits.__dict__, sort_keys=True),
                ),
            )
            connection.execute(
                "INSERT INTO config_references "
                "(reference_type, revision, credential_service, credential_version) "
                "VALUES ('draft', ?, ?, ?) ON CONFLICT(reference_type) DO UPDATE SET "
                "revision=excluded.revision, credential_service=excluded.credential_service, "
                "credential_version=excluded.credential_version",
                (number, values.service, values.credential_version),
            )
            connection.execute(
                "UPDATE revision_sequence SET next_revision = ? WHERE singleton = 1",
                (number + 1,),
            )
            _prune_unreferenced_revisions(connection)
        return ConfigRevision(number=number, values=values)

    def record_test(self, receipt: TestReceipt) -> None:
        with self._repo.transaction() as connection:
            row = connection.execute(
                "SELECT service, credential_version FROM config_revisions WHERE revision = ?",
                (receipt.revision,),
            ).fetchone()
            if row is None:
                # A test that finishes after its draft was superseded is stale.
                return
            if int(row["credential_version"]) != receipt.credential_version:
                raise ConfigurationInvalid("Test receipt does not match a configuration revision")
            connection.execute(
                "INSERT INTO test_receipts "
                "(receipt_id, revision, credential_version, validator_version, success, "
                "models_json, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    receipt.id,
                    receipt.revision,
                    receipt.credential_version,
                    receipt.validator_version,
                    int(receipt.success),
                    json.dumps(receipt.models),
                    receipt.finished_at.isoformat(),
                ),
            )

    def activate(self, expected_revision: int) -> ConfigRevision:
        with self._repo.transaction() as connection:
            draft = connection.execute(
                "SELECT r.* FROM config_references x "
                "JOIN config_revisions r ON r.revision = x.revision "
                "WHERE x.reference_type = 'draft'"
            ).fetchone()
            if draft is None or int(draft["revision"]) != expected_revision:
                raise RevisionConflict("Draft changed; reload before activation")
            if not _is_ready(self._readiness()):
                raise PrerequisiteMissing("Web access, Telegram and an owner must be ready")
            receipt = connection.execute(
                "SELECT success, credential_version, validator_version "
                "FROM test_receipts WHERE revision = ? "
                "ORDER BY recorded_order DESC LIMIT 1",
                (expected_revision,),
            ).fetchone()
            if (
                receipt is None
                or not receipt["success"]
                or int(receipt["credential_version"]) != int(draft["credential_version"])
                or receipt["validator_version"] != VALIDATOR_VERSION
            ):
                raise TestRequired("A successful test of this exact revision is required")
            active = connection.execute(
                "SELECT revision, credential_service, credential_version "
                "FROM config_references WHERE reference_type = 'active'"
            ).fetchone()
            if active:
                connection.execute(
                    "INSERT INTO config_references "
                    "(reference_type, revision, credential_service, credential_version) "
                    "VALUES ('previous', ?, ?, ?) ON CONFLICT(reference_type) DO UPDATE SET "
                    "revision=excluded.revision, credential_service=excluded.credential_service, "
                    "credential_version=excluded.credential_version",
                    (active["revision"], active["credential_service"], active["credential_version"]),
                )
            connection.execute("DELETE FROM config_references WHERE reference_type = 'active'")
            connection.execute(
                "UPDATE config_references SET reference_type = 'active' "
                "WHERE reference_type = 'draft'"
            )
            _prune_unreferenced_revisions(connection)
        return _revision_from_row(draft)

    def restore_previous(self) -> ConfigRevision | None:
        with self._repo.transaction() as connection:
            if not _is_ready(self._readiness()):
                raise PrerequisiteMissing("Web access, Telegram and an owner must be ready")
            active = connection.execute(
                "SELECT revision, credential_service, credential_version "
                "FROM config_references WHERE reference_type = 'active'"
            ).fetchone()
            previous = connection.execute(
                "SELECT revision, credential_service, credential_version "
                "FROM config_references WHERE reference_type = 'previous'"
            ).fetchone()
            if previous is None:
                return None
            previous_revision = connection.execute(
                "SELECT * FROM config_revisions WHERE revision = ?", (previous["revision"],)
            ).fetchone()
            connection.execute("DELETE FROM config_references WHERE reference_type IN ('active', 'previous')")
            connection.execute(
                "INSERT INTO config_references "
                "(reference_type, revision, credential_service, credential_version) "
                "VALUES ('active', ?, ?, ?)",
                (previous["revision"], previous["credential_service"], previous["credential_version"]),
            )
            if active:
                connection.execute(
                    "INSERT INTO config_references "
                    "(reference_type, revision, credential_service, credential_version) "
                    "VALUES ('previous', ?, ?, ?)",
                    (active["revision"], active["credential_service"], active["credential_version"]),
                )
        return _revision_from_row(previous_revision)


def _revision_from_row(row: sqlite3.Row) -> ConfigRevision:
    values = ConfigInput(
        service=row["service"],
        mode=row["mode"],
        audio_model=row["audio_model"],
        text_model=row["text_model"],
        credential_version=int(row["credential_version"]),
        system_prompt=row["system_prompt"],
        refine_template=row["refine_template"],
        limits=RuntimeLimits(**json.loads(row["limits_json"])),
    )
    return ConfigRevision(number=int(row["revision"]), values=values)


def _prune_unreferenced_revisions(connection: sqlite3.Connection) -> None:
    connection.execute(
        "DELETE FROM config_revisions WHERE revision NOT IN "
        "(SELECT revision FROM config_references)"
    )


def _validate_config(values: ConfigInput) -> None:
    if values.service not in _SERVICES:
        raise ConfigurationInvalid("Selected service is invalid")
    if values.mode not in ("multimodal", "two_stage"):
        raise ConfigurationInvalid("Selected mode is invalid")
    if not values.audio_model.strip():
        raise ConfigurationInvalid("Audio model is required")
    if values.mode == "two_stage" and not (values.text_model or "").strip():
        raise ConfigurationInvalid("A refinement model is required for two-stage mode")
    if values.mode == "multimodal" and values.text_model is not None:
        raise ConfigurationInvalid("Multimodal mode cannot select a refinement model")
    if not values.refine_template or "{raw_text}" not in values.refine_template:
        raise ConfigurationInvalid("Refinement template must contain {raw_text}")
    if values.credential_version < 1:
        raise ConfigurationInvalid("Credential version must be positive")


def _is_ready(prerequisites: Prerequisites) -> bool:
    return (
        prerequisites.web_admin
        and prerequisites.telegram_valid
        and prerequisites.owner
    )

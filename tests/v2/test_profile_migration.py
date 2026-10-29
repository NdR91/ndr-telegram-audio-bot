import sqlite3

import pytest

from bot.database.v2_repository import V2Repository
from bot.database import v2_repository as repository_module


def _create_v3_database(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')));
        INSERT INTO schema_version(version) VALUES (3);
        CREATE TABLE app_metadata (metadata_key TEXT PRIMARY KEY, metadata_value TEXT NOT NULL);
        INSERT INTO app_metadata VALUES ('database_kind', 'telegram-audio-bot-v2');
        CREATE TABLE credentials (service TEXT NOT NULL, version INTEGER NOT NULL, encrypted_value TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')), PRIMARY KEY(service, version));
        INSERT INTO credentials(service, version, encrypted_value) VALUES ('openai', 1, 'synthetic-ciphertext');
        CREATE TABLE credential_sequences (service TEXT PRIMARY KEY, next_version INTEGER NOT NULL);
        INSERT INTO credential_sequences VALUES ('openai', 2);
        CREATE TABLE revision_sequence (singleton INTEGER PRIMARY KEY CHECK(singleton=1), next_revision INTEGER NOT NULL);
        INSERT INTO revision_sequence VALUES (1, 12);
        CREATE TABLE config_revisions (revision INTEGER PRIMARY KEY, service TEXT NOT NULL, mode TEXT NOT NULL, audio_model TEXT NOT NULL, text_model TEXT, credential_version INTEGER NOT NULL, system_prompt TEXT NOT NULL, refine_template TEXT NOT NULL, limits_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')));
        INSERT INTO config_revisions VALUES (4, 'openai', 'two_stage', 'audio-old', 'text-old', 1, 'system', 'refine {raw_text}', '{}', '2026-10-25T00:00:00+00:00');
        INSERT INTO config_revisions VALUES (7, 'openai', 'two_stage', 'audio-active', 'text-active', 1, 'active system', 'active {raw_text}', '{}', '2026-10-26T00:00:00+00:00');
        INSERT INTO config_revisions VALUES (9, 'openai', 'multimodal', 'audio-draft', NULL, 1, 'draft system', 'draft {raw_text}', '{}', '2026-10-27T00:00:00+00:00');
        CREATE TABLE config_references (reference_type TEXT PRIMARY KEY CHECK(reference_type IN ('draft','active','previous')), revision INTEGER NOT NULL REFERENCES config_revisions(revision), credential_service TEXT NOT NULL, credential_version INTEGER NOT NULL);
        INSERT INTO config_references VALUES ('previous', 4, 'openai', 1);
        INSERT INTO config_references VALUES ('active', 7, 'openai', 1);
        INSERT INTO config_references VALUES ('draft', 9, 'openai', 1);
        CREATE TABLE test_receipts (recorded_order INTEGER PRIMARY KEY AUTOINCREMENT, receipt_id TEXT NOT NULL UNIQUE, revision INTEGER NOT NULL REFERENCES config_revisions(revision) ON DELETE CASCADE, credential_version INTEGER NOT NULL, validator_version TEXT NOT NULL, success INTEGER NOT NULL, models_json TEXT NOT NULL, finished_at TEXT NOT NULL);
        INSERT INTO test_receipts(receipt_id, revision, credential_version, validator_version, success, models_json, finished_at) VALUES ('synthetic-success', 7, 1, 'v2-1', 1, '["audio-active"]', '2026-10-26T00:00:00+00:00');
        INSERT INTO test_receipts(receipt_id, revision, credential_version, validator_version, success, models_json, finished_at) VALUES ('synthetic-failure', 4, 1, 'v2-1', 0, '["audio-old"]', '2026-10-25T00:00:00+00:00');
        CREATE TABLE pairing_invites (invite_id TEXT PRIMARY KEY, nonce_hash TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, bot_id INTEGER, expires_at TEXT NOT NULL, candidate_id INTEGER, candidate_name TEXT, candidate_chat_id INTEGER, candidate_chat_type TEXT, observed_at TEXT, confirmed_at TEXT);
        CREATE TABLE diagnostic_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, stage TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER, models_json TEXT NOT NULL, duration_ms INTEGER, error_code TEXT);
        """
    )
    connection.commit()
    connection.close()


def test_v3_migration_preserves_role_draft_revision_receipt_and_allocator(tmp_path, secret_store):
    path = tmp_path / "v3.sqlite3"
    _create_v3_database(path)
    repository = V2Repository(path, secret_store)
    repository.initialize()
    try:
        with repository.read() as connection:
            profile = connection.execute("SELECT profile_id, name FROM profiles").fetchone()
            assert profile[1] == "Configurazione predefinita"
            profile_id = int(profile[0])
            assert tuple(connection.execute(
                "SELECT profile_id, revision FROM profile_roles WHERE role='primary'"
            ).fetchone()) == (profile_id, 7)
            assert tuple(connection.execute(
                "SELECT profile_id, revision FROM profile_role_history WHERE singleton=1"
            ).fetchone()) == (profile_id, 4)
            assert tuple(connection.execute(
                "SELECT profile_id, revision FROM profile_drafts"
            ).fetchone()) == (profile_id, 9)
            receipts = connection.execute(
                "SELECT receipt_id, revision, credential_service, credential_version, success "
                "FROM profile_test_receipts ORDER BY recorded_order"
            ).fetchall()
            assert [tuple(row) for row in receipts] == [
                ("synthetic-success", 7, "openai", 1, 1),
                ("synthetic-failure", 4, "openai", 1, 0),
            ]
            assert connection.execute(
                "SELECT next_revision FROM revision_sequence WHERE singleton=1"
            ).fetchone()[0] == 12
            assert connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 5
            diagnostic_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(diagnostic_events)")
            }
            assert {"primary_profile_id", "fallback_profile_id", "failure_stage",
                    "failure_category", "outcome"} <= diagnostic_columns
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            # Legacy tables remain only during the staged runtime cutover.
            assert connection.execute("SELECT revision FROM config_references WHERE reference_type='active'").fetchone()[0] == 7
    finally:
        repository.close()

    reopened = V2Repository(path, secret_store)
    reopened.initialize()
    try:
        with reopened.read() as connection:
            assert connection.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM test_receipts").fetchone()[0] == 2
            assert connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 5
    finally:
        reopened.close()


def test_unconfigured_v3_database_migrates_without_inventing_a_profile(tmp_path, secret_store):
    path = tmp_path / "empty-v3.sqlite3"
    _create_v3_database(path)
    connection = sqlite3.connect(path)
    connection.execute("DELETE FROM test_receipts")
    connection.execute("DELETE FROM config_references")
    connection.execute("DELETE FROM config_revisions")
    connection.execute("UPDATE revision_sequence SET next_revision=1 WHERE singleton=1")
    connection.commit()
    connection.close()

    repository = V2Repository(path, secret_store)
    repository.initialize()
    try:
        with repository.read() as connection:
            assert connection.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM profile_roles").fetchone()[0] == 0
    finally:
        repository.close()


@pytest.mark.parametrize("version", [1, 2])
def test_older_v2_schemas_upgrade_in_order(tmp_path, secret_store, version):
    path = tmp_path / f"v{version}.sqlite3"
    _create_v3_database(path)
    connection = sqlite3.connect(path)
    connection.execute("DELETE FROM schema_version")
    connection.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))
    if version == 1:
        connection.execute("DROP TABLE pairing_invites")
        connection.execute("DROP TABLE diagnostic_events")
    else:
        connection.execute("DROP TABLE diagnostic_events")
    connection.commit()
    connection.close()

    repository = V2Repository(path, secret_store)
    repository.initialize()
    try:
        with repository.read() as connection:
            versions = [row[0] for row in connection.execute("SELECT version FROM schema_version ORDER BY version")]
            assert versions == list(range(version, 6))
            assert connection.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 1
            assert connection.execute("SELECT 1 FROM pairing_invites LIMIT 1").fetchone() is None
            assert connection.execute("SELECT 1 FROM diagnostic_events LIMIT 1").fetchone() is None
            diagnostic_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(diagnostic_events)")
            }
            assert {"primary_profile_id", "fallback_profile_id", "failure_stage",
                    "failure_category", "outcome"} <= diagnostic_columns
    finally:
        repository.close()


@pytest.mark.parametrize("version", [1, 2, 3])
def test_upgrade_failure_rolls_back_all_schema_changes(tmp_path, secret_store, monkeypatch, version):
    path = tmp_path / f"rollback-v{version}.sqlite3"
    _create_v3_database(path)
    connection = sqlite3.connect(path)
    connection.execute("DELETE FROM schema_version")
    connection.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))
    if version == 1:
        connection.execute("DROP TABLE pairing_invites")
        connection.execute("DROP TABLE diagnostic_events")
    elif version == 2:
        connection.execute("DROP TABLE diagnostic_events")
    connection.commit()
    connection.close()
    original = repository_module._migrate_v3_to_v4

    def fail_after_migration(connection):
        original(connection)
        raise RuntimeError("synthetic migration interruption")

    monkeypatch.setattr(repository_module, "_migrate_v3_to_v4", fail_after_migration)
    repository = V2Repository(path, secret_store)
    with pytest.raises(RuntimeError, match="synthetic migration interruption"):
        repository.initialize()

    connection = sqlite3.connect(path)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "profiles" not in tables
        assert "profile_revisions" not in tables
        assert [row[0] for row in connection.execute("SELECT version FROM schema_version ORDER BY version")] == [version]
        assert ("pairing_invites" in tables) is (version > 1)
        assert ("diagnostic_events" in tables) is (version > 2)
        assert connection.execute("SELECT revision FROM config_references WHERE reference_type='active'").fetchone()[0] == 7
    finally:
        connection.close()


def test_v4_to_v5_migration_adds_diagnostic_columns_atomically(tmp_path, secret_store):
    path = tmp_path / "v4.sqlite3"
    _create_v3_database(path)
    connection = sqlite3.connect(path)
    connection.execute("DELETE FROM schema_version")
    connection.execute("INSERT INTO schema_version(version) VALUES (4)")
    connection.commit()
    connection.close()

    repository = V2Repository(path, secret_store)
    repository.initialize()
    try:
        with repository.read() as connection:
            assert connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 5
            columns = {row[1] for row in connection.execute("PRAGMA table_info(diagnostic_events)")}
            assert {"primary_profile_id", "fallback_profile_id", "failure_stage",
                    "failure_category", "outcome"} <= columns
    finally:
        repository.close()


def test_v4_to_v5_failure_rolls_back_diagnostic_columns(tmp_path, secret_store, monkeypatch):
    path = tmp_path / "rollback-v4.sqlite3"
    _create_v3_database(path)
    connection = sqlite3.connect(path)
    connection.execute("DELETE FROM schema_version")
    connection.execute("INSERT INTO schema_version(version) VALUES (4)")
    connection.commit()
    connection.close()
    original = repository_module._migrate_v4_to_v5

    def fail_after_migration(connection):
        original(connection)
        raise RuntimeError("synthetic V5 migration interruption")

    monkeypatch.setattr(repository_module, "_migrate_v4_to_v5", fail_after_migration)
    repository = V2Repository(path, secret_store)
    with pytest.raises(RuntimeError, match="synthetic V5 migration interruption"):
        repository.initialize()

    connection = sqlite3.connect(path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(diagnostic_events)")}
        assert not {"primary_profile_id", "fallback_profile_id", "failure_stage",
                    "failure_category", "outcome"} & columns
        assert [row[0] for row in connection.execute(
            "SELECT version FROM schema_version ORDER BY version"
        )] == [4]
    finally:
        connection.close()

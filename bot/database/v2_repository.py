"""Persistence for the isolated, versioned V2 configuration database."""

import os
import hmac
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from bot.database.secret_store import SecretStore, SecretStoreError
from bot.database.v2_schema import DDL, PROFILE_DDL, V2_MARKER, V2_SCHEMA_VERSION
from bot.transcription.errors import ConfigurationInvalid
from bot.transcription.models import CredentialOwner


_PAIRING_DDL = "CREATE TABLE pairing_invites (invite_id TEXT PRIMARY KEY, nonce_hash TEXT NOT NULL UNIQUE, kind TEXT NOT NULL CHECK(kind IN ('owner', 'user', 'group')), bot_id INTEGER, expires_at TEXT NOT NULL, candidate_id INTEGER, candidate_name TEXT, candidate_chat_id INTEGER, candidate_chat_type TEXT, observed_at TEXT, confirmed_at TEXT)"
_DIAGNOSTIC_DDL = "CREATE TABLE diagnostic_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, stage TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER, models_json TEXT NOT NULL, duration_ms INTEGER, error_code TEXT)"


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _create_pairing_invites(connection: sqlite3.Connection) -> None:
    if not _table_exists(connection, "pairing_invites"):
        connection.execute(_PAIRING_DDL)


def _create_diagnostic_events(connection: sqlite3.Connection) -> None:
    if not _table_exists(connection, "diagnostic_events"):
        connection.execute(_DIAGNOSTIC_DDL)


def _migrate_v3_to_v4(connection: sqlite3.Connection) -> None:
    for statement in PROFILE_DDL:
        connection.execute(statement)

    legacy_receipt_count = int(
        connection.execute("SELECT COUNT(*) FROM test_receipts").fetchone()[0]
    )
    revisions = connection.execute(
        "SELECT * FROM config_revisions ORDER BY revision"
    ).fetchall()
    if not revisions:
        if legacy_receipt_count:
            raise ConfigurationInvalid("V2 test history has no configuration revision")
        return

    cursor = connection.execute(
        "INSERT INTO profiles(name, generation) VALUES (?, 1)",
        ("Configurazione predefinita",),
    )
    profile_id = int(cursor.lastrowid)
    for revision in revisions:
        connection.execute(
            "INSERT INTO profile_revisions(profile_id, revision, service, mode, "
            "audio_model, text_model, credential_version, system_prompt, "
            "refine_template, limits_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                profile_id,
                revision["revision"],
                revision["service"],
                revision["mode"],
                revision["audio_model"],
                revision["text_model"],
                revision["credential_version"],
                revision["system_prompt"],
                revision["refine_template"],
                revision["limits_json"],
                revision["created_at"],
            ),
        )

    references = {
        row["reference_type"]: row
        for row in connection.execute("SELECT * FROM config_references")
    }
    primary = references.get("active")
    if primary is not None:
        connection.execute(
            "INSERT INTO profile_roles(role, profile_id, revision, generation) "
            "VALUES ('primary', ?, ?, 1)",
            (profile_id, primary["revision"]),
        )
        connection.execute(
            "UPDATE profile_role_versions SET generation=1 WHERE role='primary'"
        )
        connection.execute(
            "UPDATE profile_roles_state SET generation=1 WHERE singleton=1"
        )
    previous = references.get("previous")
    if previous is not None:
        connection.execute(
            "INSERT INTO profile_role_history(singleton, profile_id, revision) VALUES (1, ?, ?)",
            (profile_id, previous["revision"]),
        )
    draft = references.get("draft")
    connection.execute(
        "INSERT INTO profile_drafts(profile_id, revision, generation) VALUES (?, ?, 1)",
        (profile_id, draft["revision"] if draft is not None else None),
    )

    connection.execute(
        "INSERT INTO profile_test_receipts(recorded_order, receipt_id, profile_id, "
        "revision, credential_service, credential_version, validator_version, success, "
        "models_json, finished_at) "
        "SELECT t.recorded_order, t.receipt_id, ?, t.revision, r.service, "
        "t.credential_version, t.validator_version, t.success, t.models_json, t.finished_at "
        "FROM test_receipts t JOIN config_revisions r ON r.revision=t.revision "
        "ORDER BY t.recorded_order",
        (profile_id,),
    )
    migrated_receipt_count = int(
        connection.execute("SELECT COUNT(*) FROM profile_test_receipts").fetchone()[0]
    )
    if migrated_receipt_count != legacy_receipt_count:
        raise ConfigurationInvalid("V2 test history could not be migrated completely")

    sequence = connection.execute(
        "SELECT next_revision FROM revision_sequence WHERE singleton=1"
    ).fetchone()
    next_revision = int(sequence[0]) if sequence is not None else 1
    next_revision = max(next_revision, int(revisions[-1]["revision"]) + 1)
    connection.execute(
        "INSERT INTO revision_sequence(singleton, next_revision) VALUES (1, ?) "
        "ON CONFLICT(singleton) DO UPDATE SET next_revision=excluded.next_revision",
        (next_revision,),
    )


def _migrate_v4_to_v5(connection: sqlite3.Connection) -> None:
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(diagnostic_events)")
    }
    additions = (
        ("primary_profile_id", "INTEGER"),
        ("fallback_profile_id", "INTEGER"),
        ("failure_stage", "TEXT"),
        ("failure_category", "TEXT"),
        ("outcome", "TEXT"),
    )
    for name, column_type in additions:
        if name not in columns:
            connection.execute(
                f"ALTER TABLE diagnostic_events ADD COLUMN {name} {column_type}"
            )


def _decrement_lease(leases: dict, key: tuple) -> None:
    remaining = leases[key] - 1
    if remaining:
        leases[key] = remaining
    else:
        del leases[key]


class V2Repository:
    """Own a separate SQLite database and its encrypted credentials."""

    def __init__(self, path: Path, secret_store: SecretStore):
        self.path = Path(path)
        self._secret_store = secret_store
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._credential_leases: dict[tuple[str, int], int] = {}
        self._profile_leases: dict[tuple[int, int], int] = {}

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("V2Repository has not been initialized")
        return self._connection

    def initialize(self) -> None:
        if self._connection is not None:
            return
        os.makedirs(self.path.parent, exist_ok=True)
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        existing = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if existing:
            marker_exists = "app_metadata" in existing
            marker = None
            if marker_exists:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(app_metadata)")
                }
                if {"metadata_key", "metadata_value"} <= columns:
                    row = connection.execute(
                        "SELECT metadata_value FROM app_metadata WHERE metadata_key = ?",
                        ("database_kind",),
                    ).fetchone()
                    marker = row[0] if row else None
            version_exists = "schema_version" in existing
            version = None
            if version_exists:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(schema_version)")
                }
                if "version" in columns:
                    row = connection.execute(
                        "SELECT MAX(version) FROM schema_version"
                    ).fetchone()
                    version = row[0]
            if marker != V2_MARKER or version not in (1, 2, 3, 4, V2_SCHEMA_VERSION):
                connection.close()
                raise ConfigurationInvalid("Database is not a compatible V2 database")
            try:
                connection.execute("BEGIN IMMEDIATE")
                while version < V2_SCHEMA_VERSION:
                    if version == 1:
                        _create_pairing_invites(connection)
                        connection.execute("INSERT INTO schema_version (version) VALUES (2)")
                    elif version == 2:
                        _create_diagnostic_events(connection)
                        connection.execute("INSERT INTO schema_version (version) VALUES (3)")
                    elif version == 3:
                        _migrate_v3_to_v4(connection)
                        connection.execute("INSERT INTO schema_version (version) VALUES (4)")
                    elif version == 4:
                        _migrate_v4_to_v5(connection)
                        connection.execute("INSERT INTO schema_version (version) VALUES (5)")
                    version += 1
                connection.commit()
            except BaseException:
                connection.rollback()
                connection.close()
                raise
        else:
            try:
                connection.execute("BEGIN IMMEDIATE")
                for statement in DDL:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO app_metadata (metadata_key, metadata_value) VALUES (?, ?)",
                    ("database_kind", V2_MARKER),
                )
                connection.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (V2_SCHEMA_VERSION,),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                connection.close()
                raise
        self._connection = connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a transaction with one commit boundary and automatic rollback."""
        with self._lock:
            connection = self.connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """Serialize reads with transactions on this shared SQLite connection."""
        with self._lock:
            yield self.connection

    def get_setup_state(self, key: str) -> str | None:
        with self.read() as connection:
            row = connection.execute(
                "SELECT setup_value FROM setup_state WHERE setup_key = ?", (key,)
            ).fetchone()
        return row[0] if row else None

    def set_setup_state(self, key: str, value: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO setup_state (setup_key, setup_value) VALUES (?, ?) "
                "ON CONFLICT(setup_key) DO UPDATE SET setup_value=excluded.setup_value, "
                "updated_at=datetime('now')",
                (key, value),
            )

    def rotate_web_admin_password(
        self,
        *,
        expected_password_hash: str,
        new_password_hash: str,
        new_session_secret: str,
        recovery_code_hash: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Atomically replace the administrator password and session signer.

        When a recovery hash is supplied, its expiry and single-use state are
        checked and consumed in the same transaction as the credential change.
        The expected hash is a compare-and-swap guard for concurrent changes.
        """
        try:
            encrypted = self._secret_store.encrypt(new_session_secret)
        except SecretStoreError as exc:
            raise ConfigurationInvalid("Session credential cannot be saved") from exc
        instant = now or datetime.now(timezone.utc)
        with self.transaction() as connection:
            current_hash = connection.execute(
                "SELECT setup_value FROM setup_state WHERE setup_key='web_admin_hash'"
            ).fetchone()
            if current_hash is None or not hmac.compare_digest(current_hash[0], expected_password_hash):
                return False
            if recovery_code_hash is not None:
                rows = dict(connection.execute(
                    "SELECT setup_key, setup_value FROM setup_state "
                    "WHERE setup_key IN ('v2_recovery_hash', 'v2_recovery_expires_at')"
                ).fetchall())
                try:
                    expires_at = datetime.fromisoformat(rows.get("v2_recovery_expires_at", ""))
                except ValueError:
                    return False
                stored_hash = rows.get("v2_recovery_hash", "")
                if (expires_at.tzinfo is None or instant >= expires_at
                        or not hmac.compare_digest(stored_hash, recovery_code_hash)):
                    return False

            connection.execute(
                "INSERT INTO credential_sequences(service, next_version) VALUES ('web_session', 1) "
                "ON CONFLICT(service) DO NOTHING"
            )
            version = int(connection.execute(
                "SELECT next_version FROM credential_sequences WHERE service='web_session'"
            ).fetchone()[0])
            connection.execute(
                "INSERT INTO credentials(service, version, encrypted_value) VALUES ('web_session', ?, ?)",
                (version, encrypted),
            )
            connection.execute(
                "UPDATE credential_sequences SET next_version=? WHERE service='web_session'",
                (version + 1,),
            )
            connection.executemany(
                "INSERT INTO setup_state(setup_key, setup_value) VALUES (?, ?) "
                "ON CONFLICT(setup_key) DO UPDATE SET setup_value=excluded.setup_value, "
                "updated_at=datetime('now')",
                (("web_admin_hash", new_password_hash),
                 ("web_session_credential_version", str(version))),
            )
            if recovery_code_hash is not None:
                connection.execute(
                    "UPDATE setup_state SET setup_value='', updated_at=datetime('now') "
                    "WHERE setup_key IN ('v2_recovery_hash', 'v2_recovery_expires_at')"
                )
        return True

    def get_setting(self, key: str) -> str | None:
        with self.read() as connection:
            row = connection.execute(
                "SELECT setting_value FROM app_settings WHERE setting_key = ?", (key,)
            ).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO app_settings (setting_key, setting_value) VALUES (?, ?) "
                "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value, "
                "updated_at=datetime('now')",
                (key, value),
            )

    def import_v1_payload(self, payload: dict) -> bool:
        """Atomically import one validated, already-decrypted V1 snapshot.

        The payload is deliberately accepted only at this migration boundary;
        a marker makes retries idempotent and keeps runtime code out of V1.
        """
        encrypted_credentials = []
        for service, plaintext in payload.get("credentials", []):
            try:
                encrypted_credentials.append((service, self._secret_store.encrypt(plaintext)))
            except SecretStoreError as exc:
                raise ConfigurationInvalid("Credential encryption is unavailable") from exc
        with self.transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM app_metadata WHERE metadata_key='v1_import_completed'"
            ).fetchone():
                return False
            existing = connection.execute(
                "SELECT COUNT(*) FROM acl_entries"
            ).fetchone()[0]
            if existing or connection.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]:
                raise ConfigurationInvalid("V2 target is not empty")
            for kind, entry_id in set(payload.get("acl", [])):
                connection.execute(
                    "INSERT INTO acl_entries(kind, entry_id) VALUES (?, ?)", (kind, entry_id)
                )
            connection.executemany(
                "INSERT INTO setup_state(setup_key, setup_value) VALUES (?, ?)",
                payload.get("setup_state", []),
            )
            for service, encrypted in encrypted_credentials:
                connection.execute(
                    "INSERT INTO credentials(service, version, encrypted_value) VALUES (?, 1, ?)",
                    (service, encrypted),
                )
                connection.execute(
                    "INSERT INTO credential_sequences(service, next_version) VALUES (?, 2)",
                    (service,),
                )
                connection.execute(
                    "INSERT INTO setup_state(setup_key, setup_value) VALUES (?, '1') "
                    "ON CONFLICT(setup_key) DO UPDATE SET setup_value=excluded.setup_value",
                    (f"{service}_credential_version",),
                )
            revision = payload.get("revision")
            if revision:
                values = revision["values"]
                import json
                connection.execute(
                    "INSERT INTO config_revisions(revision, service, mode, audio_model, text_model, credential_version, system_prompt, refine_template, limits_json) "
                    "VALUES (1, ?, ?, ?, ?, 1, ?, ?, ?)",
                    (values["service"], values["mode"], values["audio_model"], values["text_model"],
                     values["system_prompt"], values["refine_template"], json.dumps(values["limits"])),
                )
                connection.execute("UPDATE revision_sequence SET next_revision=2 WHERE singleton=1")
                connection.execute(
                    "INSERT INTO config_references(reference_type, revision, credential_service, credential_version) VALUES ('draft', 1, ?, 1)",
                    (values["service"],),
                )
            connection.execute(
                "INSERT INTO app_metadata(metadata_key, metadata_value) VALUES ('v1_import_completed', '1')"
            )
        return True

    def put_credential(self, service: CredentialOwner, plaintext: str) -> int:
        if service not in ("telegram", "web_session", "openai", "gemini", "openrouter"):
            raise ConfigurationInvalid("Credential service is invalid")
        if not plaintext:
            raise ConfigurationInvalid("Credential cannot be empty")
        try:
            encrypted = self._secret_store.encrypt(plaintext)
        except SecretStoreError as exc:
            raise ConfigurationInvalid("Credential encryption is unavailable") from exc
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO credential_sequences (service, next_version) VALUES (?, 1) "
                "ON CONFLICT(service) DO NOTHING",
                (service,),
            )
            row = connection.execute(
                "SELECT next_version FROM credential_sequences WHERE service = ?",
                (service,),
            ).fetchone()
            version = int(row[0])
            connection.execute(
                "INSERT INTO credentials (service, version, encrypted_value) VALUES (?, ?, ?)",
                (service, version, encrypted),
            )
            connection.execute(
                "UPDATE credential_sequences SET next_version = ? WHERE service = ?",
                (version + 1, service),
            )
        return version

    def put_credential_with_state(
        self, service: CredentialOwner, plaintext: str, version_key: str,
        state: dict[str, str] | None = None, saved_label: str | None = None,
    ) -> int:
        """Encrypt a credential and replace its setup-state reference atomically."""
        if service not in ("telegram", "web_session", "openai", "gemini", "openrouter"):
            raise ConfigurationInvalid("Credential service is invalid")
        if not plaintext:
            raise ConfigurationInvalid("Credential cannot be empty")
        try:
            encrypted = self._secret_store.encrypt(plaintext)
        except SecretStoreError as exc:
            raise ConfigurationInvalid("Credential encryption is unavailable") from exc
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO credential_sequences(service, next_version) VALUES (?, 1) ON CONFLICT(service) DO NOTHING",
                (service,),
            )
            version = int(connection.execute(
                "SELECT next_version FROM credential_sequences WHERE service=?", (service,)
            ).fetchone()[0])
            connection.execute(
                "INSERT INTO credentials(service, version, encrypted_value) VALUES (?, ?, ?)",
                (service, version, encrypted),
            )
            connection.execute(
                "UPDATE credential_sequences SET next_version=? WHERE service=?", (version + 1, service)
            )
            values = dict(state or {})
            values[version_key] = str(version)
            if saved_label is not None:
                for row in connection.execute('SELECT version FROM credentials WHERE service=?', (service,)):
                    old_version = int(row[0])
                    if old_version != version:
                        connection.execute(
                            'INSERT OR IGNORE INTO setup_state(setup_key, setup_value) VALUES (?, ?)',
                            (f'saved_key:{service}:{old_version}', f'Chiave {old_version}'),
                        )
                values[f'saved_key:{service}:{version}'] = saved_label or f'Chiave {version}'
            connection.executemany(
                "INSERT INTO setup_state(setup_key, setup_value) VALUES (?, ?) ON CONFLICT(setup_key) DO UPDATE SET setup_value=excluded.setup_value, updated_at=datetime('now')",
                values.items(),
            )
        return version

    def rename_saved_credential(self, service: str, version: int, label: str) -> None:
        """Rename a saved provider credential without changing its encrypted value."""
        if service not in ('openai', 'gemini', 'openrouter') or not 1 <= version <= 2**63 - 1:
            raise ConfigurationInvalid('Servizio o chiave non validi.')
        if not label or len(label) > 80:
            raise ConfigurationInvalid('Inserisci un nome da 1 a 80 caratteri.')
        with self.transaction() as connection:
            row = connection.execute(
                'SELECT encrypted_value FROM credentials WHERE service = ? AND version = ?',
                (service, version),
            ).fetchone()
            if row is None:
                raise LookupError('Chiave non trovata.')
            try:
                secret = self._secret_store.decrypt(row[0])
            except SecretStoreError as exc:
                raise ConfigurationInvalid('Impossibile verificare la chiave.') from exc
            if secret and secret in label:
                raise ConfigurationInvalid('Il nome non deve contenere la chiave.')
            connection.execute(
                'INSERT INTO setup_state(setup_key, setup_value) VALUES (?, ?) '
                'ON CONFLICT(setup_key) DO UPDATE SET setup_value=excluded.setup_value, updated_at=datetime(\'now\')',
                (f'saved_key:{service}:{version}', label),
            )

    def read_credential(self, service: CredentialOwner, version: int) -> str:
        with self.read() as connection:
            row = connection.execute(
                "SELECT encrypted_value FROM credentials WHERE service = ? AND version = ?",
                (service, version),
            ).fetchone()
        if row is None:
            raise ConfigurationInvalid("Credential version is unavailable")
        try:
            return self._secret_store.decrypt(row[0])
        except SecretStoreError as exc:
            raise ConfigurationInvalid("Credential cannot be decrypted") from exc

    def credential_versions(self, service: CredentialOwner) -> tuple[int, ...]:
        with self.read() as connection:
            rows = connection.execute(
                "SELECT version FROM credentials WHERE service = ? ORDER BY version", (service,)
            ).fetchall()
        return tuple(int(row[0]) for row in rows)

    @contextmanager
    def profile_lease(self, profile_id: int, revision: int) -> Iterator[None]:
        """Keep one saved profile revision and its credential from pruning."""
        with self._lock:
            row = self.connection.execute(
                "SELECT service, credential_version FROM profile_revisions "
                "WHERE profile_id=? AND revision=?",
                (profile_id, revision),
            ).fetchone()
            if row is None:
                raise ConfigurationInvalid("Profile revision is unavailable")
            profile_key = (profile_id, revision)
            credential_key = (row["service"], int(row["credential_version"]))
            self._profile_leases[profile_key] = self._profile_leases.get(profile_key, 0) + 1
            self._credential_leases[credential_key] = self._credential_leases.get(credential_key, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                _decrement_lease(self._profile_leases, profile_key)
                _decrement_lease(self._credential_leases, credential_key)

    @contextmanager
    def lease_processing_profile_rows(
        self, validator_version: str
    ) -> Iterator[tuple[sqlite3.Row, ...] | None]:
        """Atomically snapshot roles and lease their revisions and credentials."""
        with self._lock:
            rows = tuple(
                self.connection.execute(
                    "SELECT pr.role, p.profile_id, p.name, r.revision, r.service, r.mode, "
                    "r.audio_model, r.text_model, r.credential_version, r.system_prompt, "
                    "r.refine_template, r.limits_json, r.created_at "
                    "FROM profile_roles pr "
                    "JOIN profiles p ON p.profile_id=pr.profile_id "
                    "JOIN profile_revisions r ON r.profile_id=pr.profile_id AND r.revision=pr.revision "
                    "WHERE pr.role='primary' OR EXISTS ("
                    "SELECT 1 FROM profile_test_receipts t "
                    "WHERE t.profile_id=pr.profile_id AND t.revision=pr.revision "
                    "AND t.recorded_order=(SELECT MAX(latest.recorded_order) "
                    "FROM profile_test_receipts latest WHERE latest.profile_id=pr.profile_id "
                    "AND latest.revision=pr.revision) AND t.success=1 "
                    "AND t.credential_service=r.service "
                    "AND t.credential_version=r.credential_version "
                    "AND t.validator_version=?) "
                    "ORDER BY CASE pr.role WHEN 'primary' THEN 0 ELSE 1 END",
                    (validator_version,),
                ).fetchall()
            )
            if not any(row["role"] == "primary" for row in rows):
                rows = ()
            for row in rows:
                profile_key = (int(row["profile_id"]), int(row["revision"]))
                credential_key = (row["service"], int(row["credential_version"]))
                self._profile_leases[profile_key] = self._profile_leases.get(profile_key, 0) + 1
                self._credential_leases[credential_key] = self._credential_leases.get(credential_key, 0) + 1
        try:
            yield rows or None
        finally:
            with self._lock:
                for row in rows:
                    profile_key = (int(row["profile_id"]), int(row["revision"]))
                    credential_key = (row["service"], int(row["credential_version"]))
                    _decrement_lease(self._profile_leases, profile_key)
                    _decrement_lease(self._credential_leases, credential_key)

    @contextmanager
    def credential_lease(self, service: CredentialOwner, version: int) -> Iterator[None]:
        """Keep a credential version available while a request is using it."""
        key = (service, version)
        with self._lock:
            if self.connection.execute(
                "SELECT 1 FROM credentials WHERE service = ? AND version = ?", key
            ).fetchone() is None:
                raise ConfigurationInvalid("Credential version is unavailable")
            self._credential_leases[key] = self._credential_leases.get(key, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                remaining = self._credential_leases[key] - 1
                if remaining:
                    self._credential_leases[key] = remaining
                else:
                    del self._credential_leases[key]

    def prune_credentials(self) -> int:
        """Remove obsolete versions, retaining live and latest service credentials."""
        with self._lock, self.transaction() as connection:
            rows = connection.execute(
                "SELECT service, version FROM credentials ORDER BY service, version"
            ).fetchall()
            latest = {
                row["service"]: int(row["version"])
                for row in connection.execute(
                    "SELECT service, MAX(version) AS version FROM credentials GROUP BY service"
                )
            }
            setup_references = set()
            for row in connection.execute(
                "SELECT setup_key, setup_value FROM setup_state "
                "WHERE setup_key LIKE '%_credential_version'"
            ):
                service = row["setup_key"][: -len("_credential_version")]
                try:
                    setup_references.add((service, int(row["setup_value"])))
                except (TypeError, ValueError):
                    continue
            removable = [
                (row["service"], int(row["version"]))
                for row in rows
                if (row["service"], int(row["version"])) not in self._credential_leases
                and int(row["version"]) != latest[row["service"]]
                and (row["service"], int(row["version"])) not in setup_references
                and connection.execute(
                    'SELECT 1 FROM setup_state WHERE setup_key = ?',
                    (f'saved_key:{row["service"]}:{row["version"]}',),
                ).fetchone() is None
                and connection.execute(
                    "SELECT 1 FROM config_references "
                    "WHERE credential_service = ? AND credential_version = ?",
                    (row["service"], row["version"]),
                ).fetchone()
                is None
                and connection.execute(
                    "SELECT 1 FROM profile_revisions "
                    "WHERE service = ? AND credential_version = ?",
                    (row["service"], row["version"]),
                ).fetchone()
                is None
            ]
            connection.executemany(
                "DELETE FROM credentials WHERE service = ? AND version = ?", removable
            )
        return len(removable)

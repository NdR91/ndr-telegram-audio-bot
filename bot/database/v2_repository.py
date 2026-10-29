"""Persistence for the isolated, versioned V2 configuration database."""

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from bot.database.secret_store import SecretStore, SecretStoreError
from bot.database.v2_schema import DDL, V2_MARKER, V2_SCHEMA_VERSION
from bot.transcription.errors import ConfigurationInvalid
from bot.transcription.models import CredentialOwner


class V2Repository:
    """Own a separate SQLite database and its encrypted credentials."""

    def __init__(self, path: Path, secret_store: SecretStore):
        self.path = Path(path)
        self._secret_store = secret_store
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._credential_leases: dict[tuple[str, int], int] = {}

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
            if marker != V2_MARKER or version not in (1, 2, V2_SCHEMA_VERSION):
                connection.close()
                raise ConfigurationInvalid("Database is not a compatible V2 database")
            if version in (1, 2):
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    if version == 1:
                        connection.execute(
                            "CREATE TABLE pairing_invites (invite_id TEXT PRIMARY KEY, nonce_hash TEXT NOT NULL UNIQUE, kind TEXT NOT NULL CHECK(kind IN ('owner', 'user', 'group')), bot_id INTEGER, expires_at TEXT NOT NULL, candidate_id INTEGER, candidate_name TEXT, candidate_chat_id INTEGER, candidate_chat_type TEXT, observed_at TEXT, confirmed_at TEXT)"
                        )
                    connection.execute(
                        "CREATE TABLE diagnostic_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, stage TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER, models_json TEXT NOT NULL, duration_ms INTEGER, error_code TEXT)"
                    )
                    connection.execute("INSERT INTO schema_version (version) VALUES (?)", (V2_SCHEMA_VERSION,))
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
            ]
            connection.executemany(
                "DELETE FROM credentials WHERE service = ? AND version = ?", removable
            )
        return len(removable)

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
            if marker != V2_MARKER or version != V2_SCHEMA_VERSION:
                connection.close()
                raise ConfigurationInvalid("Database is not a compatible V2 database")
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

    def get_setup_state(self, key: str) -> str | None:
        row = self.connection.execute(
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
        row = self.connection.execute(
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

    def read_credential(self, service: CredentialOwner, version: int) -> str:
        row = self.connection.execute(
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
        rows = self.connection.execute(
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
        """Remove encrypted credentials unused by revisions or active requests."""
        with self._lock, self.transaction() as connection:
            rows = connection.execute(
                "SELECT service, version FROM credentials ORDER BY service, version"
            ).fetchall()
            removable = [
                (row["service"], int(row["version"]))
                for row in rows
                if (row["service"], int(row["version"])) not in self._credential_leases
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

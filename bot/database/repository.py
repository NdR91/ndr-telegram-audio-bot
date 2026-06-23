"""
Data-access layer for the unified application database.

``DatabaseManager`` is the single entry-point for reading and writing
configuration, access-control, provider, pipeline, preference, and audit data.

When a :class:`~bot.database.secret_store.SecretStore` is provided, provider
credentials are transparently encrypted at rest and decrypted on read.
"""

import json
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional

from bot.database.migrations import run_pending
from bot.database.secret_store import SecretStore

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages the application database lifecycle and provides data access."""

    def __init__(self, db_path: str, secret_store: SecretStore | None = None):
        """
        Parameters
        ----------
        db_path:
            Filesystem path to the SQLite database file.
        secret_store:
            Optional :class:`SecretStore` for encrypting sensitive fields at
            rest.  When set, provider credentials are transparently encrypted
            on write and decrypted on read.
        """
        self.db_path = db_path
        self._secret_store = secret_store
        self._conn: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Open or create the database and apply pending migrations."""
        parent = os.path.dirname(os.path.abspath(self.db_path))
        os.makedirs(parent, exist_ok=True)

        self._conn = self._connect()
        run_pending(self._conn)
        logger.info("Database initialized at %s", self.db_path)

    def close(self) -> None:
        """Close the database connection if open."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the open connection (raises if not initialized)."""
        if self._conn is None:
            raise RuntimeError(
                "DatabaseManager has not been initialized. "
                "Call initialize() first."
            )
        return self._conn

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _row_as_dict(row: sqlite3.Row) -> Dict[str, Any]:
        return dict(row)

    # ------------------------------------------------------------------
    # Whitelist compatibility (same interface as SQLiteWhitelistStore)
    # ------------------------------------------------------------------

    def load_authorized_data(self) -> Dict[str, List[int]]:
        """Load all whitelist entries as ``{category: [entry_id, ...]}``."""
        result: Dict[str, List[int]] = {
            "admin": [],
            "users": [],
            "groups": [],
        }
        conn = self.connection
        for row in conn.execute("SELECT entry_id FROM admin_users ORDER BY entry_id"):
            result["admin"].append(row["entry_id"])
        for row in conn.execute("SELECT entry_id FROM authorized_users ORDER BY entry_id"):
            result["users"].append(row["entry_id"])
        for row in conn.execute("SELECT entry_id FROM authorized_groups ORDER BY entry_id"):
            result["groups"].append(row["entry_id"])
        return result

    def replace_authorized_data(self, data: Dict[str, List[int]]) -> None:
        """Atomically replace all whitelist entries, rolling back on failure."""
        conn = self.connection
        conn.execute("BEGIN")
        try:
            conn.execute("DELETE FROM admin_users")
            conn.execute("DELETE FROM authorized_users")
            conn.execute("DELETE FROM authorized_groups")
            for entry_id in data.get("admin", []):
                conn.execute("INSERT INTO admin_users (entry_id) VALUES (?)", (int(entry_id),))
            for entry_id in data.get("users", []):
                conn.execute("INSERT INTO authorized_users (entry_id) VALUES (?)", (int(entry_id),))
            for entry_id in data.get("groups", []):
                conn.execute("INSERT INTO authorized_groups (entry_id) VALUES (?)", (int(entry_id),))
            conn.commit()
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    # ------------------------------------------------------------------
    # Setup state
    # ------------------------------------------------------------------

    def get_setup_state(self, key: str) -> Optional[str]:
        """Return the value for a setup-state key, or ``None``."""
        row = self.connection.execute(
            "SELECT setup_value FROM setup_state WHERE setup_key = ?", (key,)
        ).fetchone()
        return row["setup_value"] if row else None

    def set_setup_state(self, key: str, value: str) -> None:
        """Upsert a setup-state key/value pair."""
        self.connection.execute(
            "INSERT OR REPLACE INTO setup_state (setup_key, setup_value, updated_at) "
            "VALUES (?, ?, datetime('now'))",
            (key, value),
        )
        self.connection.commit()

    def get_all_setup_state(self) -> Dict[str, Optional[str]]:
        """Return all setup-state entries as a dict."""
        rows = self.connection.execute(
            "SELECT setup_key, setup_value FROM setup_state"
        ).fetchall()
        return {row["setup_key"]: row["setup_value"] for row in rows}

    # ------------------------------------------------------------------
    # Application settings
    # ------------------------------------------------------------------

    def get_setting(self, key: str) -> Optional[str]:
        """Return a setting value, or ``None``."""
        row = self.connection.execute(
            "SELECT setting_value FROM app_settings WHERE setting_key = ?", (key,)
        ).fetchone()
        return row["setting_value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        """Upsert a setting."""
        self.connection.execute(
            "INSERT OR REPLACE INTO app_settings (setting_key, setting_value, updated_at) "
            "VALUES (?, ?, datetime('now'))",
            (key, value),
        )
        self.connection.commit()

    def get_all_settings(self) -> Dict[str, Optional[str]]:
        """Return all settings as a dict."""
        rows = self.connection.execute(
            "SELECT setting_key, setting_value FROM app_settings"
        ).fetchall()
        return {row["setting_key"]: row["setting_value"] for row in rows}

    def set_settings(self, settings: Dict[str, str]) -> None:
        """Set multiple settings in a single transaction, rolling back on failure.

        Parameters
        ----------
        settings:
            ``{key: value}`` pairs to upsert.
        """
        conn = self.connection
        conn.execute("BEGIN")
        try:
            for key, value in settings.items():
                conn.execute(
                    "INSERT OR REPLACE INTO app_settings "
                    "(setting_key, setting_value, updated_at) "
                    "VALUES (?, ?, datetime('now'))",
                    (key, value),
                )
            conn.commit()
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    def delete_setting(self, key: str) -> None:
        """Remove a setting by key."""
        self.connection.execute(
            "DELETE FROM app_settings WHERE setting_key = ?", (key,)
        )
        self.connection.commit()

    # ------------------------------------------------------------------
    # Provider connections
    # ------------------------------------------------------------------

    def add_provider(
        self,
        name: str,
        adapter_type: str,
        endpoint: Optional[str] = None,
        credentials: Optional[str] = None,
        encrypted_credentials: Optional[str] = None,
        capabilities: Optional[Dict[str, Any]] = None,
        enabled: bool = True,
    ) -> int:
        """Insert a provider connection and return its new ID.

        When a :class:`SecretStore` is configured, *credentials* (plaintext)
        is encrypted automatically.  Callers may alternatively pass
        *encrypted_credentials* (pre-encrypted) directly.
        """
        stored_creds = self._resolve_credentials(credentials, encrypted_credentials)
        cur = self.connection.execute(
            "INSERT INTO provider_connections "
            "(name, adapter_type, endpoint, encrypted_credentials, capabilities, enabled) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                name,
                adapter_type,
                endpoint,
                stored_creds,
                json.dumps(capabilities) if capabilities else None,
                1 if enabled else 0,
            ),
        )
        self.connection.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_provider(self, provider_id: int) -> Optional[Dict[str, Any]]:
        """Return a provider connection by ID, or ``None``.

        If a :class:`SecretStore` is configured, the ``credentials`` key
        contains the decrypted plaintext value.
        """
        row = self.connection.execute(
            "SELECT * FROM provider_connections WHERE id = ?", (provider_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_with_decrypted_creds(row)

    def list_providers(self) -> List[Dict[str, Any]]:
        """Return all provider connections.

        If a :class:`SecretStore` is configured, each entry includes a
        ``credentials`` key with the decrypted plaintext value.
        """
        rows = self.connection.execute(
            "SELECT * FROM provider_connections ORDER BY id"
        ).fetchall()
        return [self._row_with_decrypted_creds(row) for row in rows]

    def update_provider(
        self,
        provider_id: int,
        *,
        name: Optional[str] = None,
        endpoint: Optional[str] = None,
        credentials: Optional[str] = None,
        encrypted_credentials: Optional[str] = None,
        capabilities: Optional[Dict[str, Any]] = None,
        enabled: Optional[bool] = None,
    ) -> bool:
        """Update fields on a provider connection.

        When a :class:`SecretStore` is configured, *credentials* (plaintext)
        is encrypted automatically.  Returns ``True`` if the row existed.
        """
        fields: List[str] = []
        params: List[Any] = []

        if name is not None:
            fields.append("name = ?")
            params.append(name)
        if endpoint is not None:
            fields.append("endpoint = ?")
            params.append(endpoint)
        if credentials is not None or encrypted_credentials is not None:
            fields.append("encrypted_credentials = ?")
            params.append(self._resolve_credentials(credentials, encrypted_credentials))
        if capabilities is not None:
            fields.append("capabilities = ?")
            params.append(json.dumps(capabilities))
        if enabled is not None:
            fields.append("enabled = ?")
            params.append(1 if enabled else 0)

        if not fields:
            return False

        fields.append("updated_at = datetime('now')")
        params.append(provider_id)

        cur = self.connection.execute(
            f"UPDATE provider_connections SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        self.connection.commit()
        return cur.rowcount > 0

    def delete_provider(self, provider_id: int) -> bool:
        """Delete a provider connection.  Returns ``True`` if the row existed."""
        cur = self.connection.execute(
            "DELETE FROM provider_connections WHERE id = ?", (provider_id,)
        )
        self.connection.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # SecretStore helpers
    # ------------------------------------------------------------------

    def _resolve_credentials(
        self,
        credentials: Optional[str],
        encrypted_credentials: Optional[str],
    ) -> Optional[str]:
        """Encrypt *credentials* if a secret store is available; otherwise
        fall back to *encrypted_credentials*.

        When neither encryption nor a pre-encrypted value is available the
        credential is **not** stored (returns ``None``).
        """
        if credentials is not None:
            if self._secret_store is not None:
                return self._secret_store.encrypt(credentials)
            logger.warning(
                "Plaintext credentials provided but no SecretStore configured; "
                "credential will not be stored"
            )
            return None
        return encrypted_credentials

    def _row_with_decrypted_creds(self, row: sqlite3.Row) -> Dict[str, Any]:
        """Convert a SQLite row to a dict, decrypting credentials if possible."""
        result = self._row_as_dict(row)
        if result.get("capabilities"):
            result["capabilities"] = json.loads(result["capabilities"])
        if result.get("encrypted_credentials") and self._secret_store is not None:
            try:
                result["credentials"] = self._secret_store.decrypt(
                    result["encrypted_credentials"]
                )
            except Exception:
                result["credentials"] = None
        return result

    # ------------------------------------------------------------------
    # Pipeline profiles
    # ------------------------------------------------------------------

    def add_pipeline_profile(
        self,
        name: str,
        transcription_provider_id: Optional[int] = None,
        text_provider_id: Optional[int] = None,
        system_prompt: Optional[str] = None,
        refine_template: Optional[str] = None,
        fallback_policy: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Insert a pipeline profile and return its new ID."""
        cur = self.connection.execute(
            "INSERT INTO pipeline_profiles "
            "(name, transcription_provider_id, text_provider_id, system_prompt, "
            " refine_template, fallback_policy) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                name,
                transcription_provider_id,
                text_provider_id,
                system_prompt,
                refine_template,
                json.dumps(fallback_policy) if fallback_policy else None,
            ),
        )
        self.connection.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_pipeline_profile(self, profile_id: int) -> Optional[Dict[str, Any]]:
        """Return a pipeline profile by ID, or ``None``."""
        row = self.connection.execute(
            "SELECT * FROM pipeline_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        if row is None:
            return None
        result = self._row_as_dict(row)
        if result.get("fallback_policy"):
            result["fallback_policy"] = json.loads(result["fallback_policy"])
        return result

    def list_pipeline_profiles(self) -> List[Dict[str, Any]]:
        """Return all pipeline profiles."""
        rows = self.connection.execute(
            "SELECT * FROM pipeline_profiles ORDER BY id"
        ).fetchall()
        results = []
        for row in rows:
            result = self._row_as_dict(row)
            if result.get("fallback_policy"):
                result["fallback_policy"] = json.loads(result["fallback_policy"])
            results.append(result)
        return results

    # ------------------------------------------------------------------
    # User preferences
    # ------------------------------------------------------------------

    def get_user_preference(self, user_id: int, key: str) -> Optional[str]:
        """Return a user preference, or ``None``."""
        row = self.connection.execute(
            "SELECT preference_value FROM user_preferences "
            "WHERE user_id = ? AND preference_key = ?",
            (user_id, key),
        ).fetchone()
        return row["preference_value"] if row else None

    def set_user_preference(self, user_id: int, key: str, value: str) -> None:
        """Upsert a user preference."""
        self.connection.execute(
            "INSERT OR REPLACE INTO user_preferences (user_id, preference_key, preference_value) "
            "VALUES (?, ?, ?)",
            (user_id, key, value),
        )
        self.connection.commit()

    def delete_user_preference(self, user_id: int, key: str) -> None:
        """Remove a user preference."""
        self.connection.execute(
            "DELETE FROM user_preferences WHERE user_id = ? AND preference_key = ?",
            (user_id, key),
        )
        self.connection.commit()

    def get_all_user_preferences(self, user_id: int) -> Dict[str, Optional[str]]:
        """Return all preferences for a user."""
        rows = self.connection.execute(
            "SELECT preference_key, preference_value FROM user_preferences "
            "WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        return {row["preference_key"]: row["preference_value"] for row in rows}

    # ------------------------------------------------------------------
    # Group preferences
    # ------------------------------------------------------------------

    def get_group_preference(self, group_id: int, key: str) -> Optional[str]:
        """Return a group preference, or ``None``."""
        row = self.connection.execute(
            "SELECT preference_value FROM group_preferences "
            "WHERE group_id = ? AND preference_key = ?",
            (group_id, key),
        ).fetchone()
        return row["preference_value"] if row else None

    def set_group_preference(self, group_id: int, key: str, value: str) -> None:
        """Upsert a group preference."""
        self.connection.execute(
            "INSERT OR REPLACE INTO group_preferences (group_id, preference_key, preference_value) "
            "VALUES (?, ?, ?)",
            (group_id, key, value),
        )
        self.connection.commit()

    def delete_group_preference(self, group_id: int, key: str) -> None:
        """Remove a group preference."""
        self.connection.execute(
            "DELETE FROM group_preferences WHERE group_id = ? AND preference_key = ?",
            (group_id, key),
        )
        self.connection.commit()

    # ------------------------------------------------------------------
    # Audit events
    # ------------------------------------------------------------------

    def add_audit_event(
        self,
        event_type: str,
        actor_id: Optional[int] = None,
        target_type: Optional[str] = None,
        target_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Record an audit event and return its ID."""
        cur = self.connection.execute(
            "INSERT INTO audit_events (event_type, actor_id, target_type, target_id, metadata) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                event_type,
                actor_id,
                target_type,
                target_id,
                json.dumps(metadata) if metadata else None,
            ),
        )
        self.connection.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_audit_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Return the most recent audit events."""
        rows = self.connection.execute(
            "SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        results = []
        for row in rows:
            result = self._row_as_dict(row)
            if result.get("metadata"):
                result["metadata"] = json.loads(result["metadata"])
            results.append(result)
        return results

    # ------------------------------------------------------------------
    # Legacy import helpers
    # ------------------------------------------------------------------

    def import_whitelist_from_dict(self, authorized_data: Dict[str, List[int]]) -> None:
        """Bootstrap whitelist tables from an ``authorized.json``-style dict.

        Only inserts rows when the corresponding table is empty (idempotent).
        """
        conn = self.connection
        if conn.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0] > 0:
            logger.info("Whitelist tables are not empty; skipping legacy import")
            return

        category_map = {
            "admin": "admin_users",
            "users": "authorized_users",
            "groups": "authorized_groups",
        }
        for category, table in category_map.items():
            for entry_id in authorized_data.get(category, []):
                conn.execute(
                    f"INSERT OR IGNORE INTO {table} (entry_id) VALUES (?)",
                    (int(entry_id),),
                )
        conn.commit()
        logger.info("Legacy whitelist data imported into unified database")

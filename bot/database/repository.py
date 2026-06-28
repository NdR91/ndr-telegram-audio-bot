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
from bot.exceptions import ResourceInUseError

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
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
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

        Raises :class:`ResourceInUseError` if disabling an enabled provider
        that is referenced by the active pipeline profile.
        """
        # Before disabling, check that the provider is not in use.
        if enabled is False:
            provider = self.get_provider(provider_id)
            if provider and provider.get("enabled"):
                self._check_provider_not_in_use(provider_id)

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

    def _get_active_pipeline_profile_id(self) -> Optional[int]:
        """Return the active pipeline profile ID from settings, or ``None``."""
        val = self.get_setup_state("active_pipeline_profile")
        return int(val) if val else None

    def _check_provider_not_in_use(self, provider_id: int) -> None:
        """Raise :class:`ResourceInUseError` if *provider_id* is referenced
        by the active pipeline profile."""
        active_id = self._get_active_pipeline_profile_id()
        if active_id is None:
            return
        profile = self.get_pipeline_profile(active_id)
        if profile is None:
            return
        # Check provider-level references on profile
        if profile.get("transcription_provider_id") == provider_id:
            raise ResourceInUseError(
                f"Provider id={provider_id} is used as transcription provider "
                f"in the active pipeline profile (id={active_id}). "
                f"Remove or change the pipeline first."
            )
        if profile.get("text_provider_id") == provider_id:
            raise ResourceInUseError(
                f"Provider id={provider_id} is used as text provider "
                f"in the active pipeline profile (id={active_id}). "
                f"Remove or change the pipeline first."
            )
        # Check model-level references — any model owned by this provider
        # used as primary or fallback in the active pipeline.
        models = self.list_provider_models(provider_id)
        model_ids = [m["id"] for m in models]
        if not model_ids:
            return
        stages = self.list_pipeline_stages(active_id)
        for stage in stages:
            if stage.get("primary_model_id") in model_ids:
                raise ResourceInUseError(
                    f"Provider id={provider_id} has model id={stage['primary_model_id']} "
                    f"used as primary model in pipeline stage '{stage['stage_type']}' "
                    f"of the active profile (id={active_id})."
                )
            for fb in stage.get("fallbacks", []):
                if fb["model_id"] in model_ids:
                    raise ResourceInUseError(
                        f"Provider id={provider_id} has model id={fb['model_id']} "
                        f"used as fallback in pipeline stage '{stage['stage_type']}' "
                        f"of the active profile (id={active_id})."
                    )

    def _check_model_not_in_use(self, model_entry_id: int) -> None:
        """Raise :class:`ResourceInUseError` if *model_entry_id* is
        referenced by the active pipeline profile."""
        active_id = self._get_active_pipeline_profile_id()
        if active_id is None:
            return
        stages = self.list_pipeline_stages(active_id)
        for stage in stages:
            if stage.get("primary_model_id") == model_entry_id:
                raise ResourceInUseError(
                    f"Model id={model_entry_id} is used as primary model "
                    f"in pipeline stage '{stage['stage_type']}' "
                    f"of the active profile (id={active_id})."
                )
            for fb in stage.get("fallbacks", []):
                if fb["model_id"] == model_entry_id:
                    raise ResourceInUseError(
                        f"Model id={model_entry_id} is used as fallback "
                        f"in pipeline stage '{stage['stage_type']}' "
                        f"of the active profile (id={active_id})."
                    )

    def delete_provider(self, provider_id: int) -> bool:
        """Delete a provider connection.  Returns ``True`` if the row existed.

        Raises :class:`ResourceInUseError` if the provider is referenced
        by the active pipeline profile.
        """
        self._check_provider_not_in_use(provider_id)
        cur = self.connection.execute(
            "DELETE FROM provider_connections WHERE id = ?", (provider_id,)
        )
        self.connection.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Provider models (per-connection model registry)
    # ------------------------------------------------------------------

    def add_provider_model(
        self,
        provider_id: int,
        model_id: str,
        display_name: Optional[str] = None,
        capabilities: Optional[Dict[str, Any]] = None,
        detected: bool = True,
        enabled: bool = True,
    ) -> int:
        """Register a model under a provider connection.

        Returns the new ``provider_models.id``.
        """
        cur = self.connection.execute(
            "INSERT OR REPLACE INTO provider_models "
            "(provider_id, model_id, display_name, capabilities, detected, "
            " manually_overridden, enabled, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, datetime('now'))",
            (
                provider_id,
                model_id,
                display_name or model_id,
                json.dumps(capabilities) if capabilities else None,
                1 if detected else 0,
                1 if enabled else 0,
            ),
        )
        self.connection.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_provider_model(self, model_entry_id: int) -> Optional[Dict[str, Any]]:
        """Return a single provider model entry by its ID, or ``None``."""
        row = self.connection.execute(
            "SELECT * FROM provider_models WHERE id = ?", (model_entry_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_with_model_caps(row)

    def list_provider_models(
        self,
        provider_id: Optional[int] = None,
        *,
        only_enabled: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return provider model entries, optionally filtered by provider.

        Results are ordered by ``model_id``.
        """
        q = "SELECT * FROM provider_models"
        params: list[Any] = []
        where: list[str] = []
        if provider_id is not None:
            where.append("provider_id = ?")
            params.append(provider_id)
        if only_enabled:
            where.append("enabled = 1")
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY model_id"
        rows = self.connection.execute(q, params).fetchall()
        return [self._row_with_model_caps(row) for row in rows]

    def update_provider_model(
        self,
        model_entry_id: int,
        *,
        display_name: Optional[str] = None,
        capabilities: Optional[Dict[str, Any]] = None,
        detected: Optional[bool] = None,
        manually_overridden: Optional[bool] = None,
        enabled: Optional[bool] = None,
    ) -> bool:
        """Update fields on a provider model entry.

        When disabling an enabled model (``enabled=False``), checks that
        the model is not referenced by the active pipeline profile.

        Returns ``True`` if the row existed.
        """
        # Before disabling, check that the model is not in use.
        if enabled is False:
            entry = self.get_provider_model(model_entry_id)
            if entry and entry.get("enabled"):
                self._check_model_not_in_use(model_entry_id)

        fields: List[str] = []
        params: List[Any] = []

        if display_name is not None:
            fields.append("display_name = ?")
            params.append(display_name)
        if capabilities is not None:
            fields.append("capabilities = ?")
            params.append(json.dumps(capabilities))
        if detected is not None:
            fields.append("detected = ?")
            params.append(1 if detected else 0)
        if manually_overridden is not None:
            fields.append("manually_overridden = ?")
            params.append(1 if manually_overridden else 0)
        if enabled is not None:
            fields.append("enabled = ?")
            params.append(1 if enabled else 0)

        if not fields:
            return False

        fields.append("updated_at = datetime('now')")
        params.append(model_entry_id)

        cur = self.connection.execute(
            f"UPDATE provider_models SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        self.connection.commit()
        return cur.rowcount > 0

    def delete_provider_model(self, model_entry_id: int) -> bool:
        """Delete a provider model entry.  Returns ``True`` if the row existed.

        Raises :class:`ResourceInUseError` if the model is referenced
        by the active pipeline profile.
        """
        self._check_model_not_in_use(model_entry_id)
        cur = self.connection.execute(
            "DELETE FROM provider_models WHERE id = ?", (model_entry_id,)
        )
        self.connection.commit()
        return cur.rowcount > 0

    def set_model_capabilities(
        self,
        model_entry_id: int,
        capabilities: Dict[str, Any],
        *,
        mark_overridden: bool = True,
    ) -> bool:
        """Set capabilities on a provider model, optionally marking it as
        manually overridden."""
        return self.update_provider_model(
            model_entry_id,
            capabilities=capabilities,
            manually_overridden=mark_overridden,
        )

    # ------------------------------------------------------------------
    # Pipeline stages
    # ------------------------------------------------------------------

    def add_pipeline_stage(
        self,
        profile_id: int,
        stage_type: str,
        primary_model_id: Optional[int] = None,
    ) -> int:
        """Add a stage to a pipeline profile.

        *stage_type* must be ``"transcription"``, ``"refinement"``, or
        ``"single_pass"``.
        """
        cur = self.connection.execute(
            "INSERT INTO pipeline_stages "
            "(profile_id, stage_type, primary_model_id) "
            "VALUES (?, ?, ?)",
            (profile_id, stage_type, primary_model_id),
        )
        self.connection.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_pipeline_stage(self, stage_id: int) -> Optional[Dict[str, Any]]:
        """Return a pipeline stage by ID, or ``None``."""
        row = self.connection.execute(
            "SELECT * FROM pipeline_stages WHERE id = ?", (stage_id,)
        ).fetchone()
        if row is None:
            return None
        result = self._row_as_dict(row)
        result["fallbacks"] = self.list_stage_fallbacks(stage_id)
        return result

    def list_pipeline_stages(
        self,
        profile_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return pipeline stages, optionally filtered by profile."""
        q = "SELECT * FROM pipeline_stages"
        params: list[Any] = []
        if profile_id is not None:
            q += " WHERE profile_id = ?"
            params.append(profile_id)
        q += " ORDER BY id"
        rows = self.connection.execute(q, params).fetchall()
        results = []
        for row in rows:
            result = self._row_as_dict(row)
            result["fallbacks"] = self.list_stage_fallbacks(row["id"])
            results.append(result)
        return results

    def update_pipeline_stage(
        self,
        stage_id: int,
        *,
        primary_model_id: Optional[int] = None,
    ) -> bool:
        """Update a pipeline stage's primary model.

        Returns ``True`` if the row existed.
        """
        cur = self.connection.execute(
            "UPDATE pipeline_stages SET primary_model_id = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (primary_model_id, stage_id),
        )
        self.connection.commit()
        return cur.rowcount > 0

    def delete_pipeline_stage(self, stage_id: int) -> bool:
        """Delete a pipeline stage.  Returns ``True`` if the row existed."""
        cur = self.connection.execute(
            "DELETE FROM pipeline_stages WHERE id = ?", (stage_id,)
        )
        self.connection.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Stage fallbacks (ordered fallback model chains)
    # ------------------------------------------------------------------

    def add_stage_fallback(
        self,
        stage_id: int,
        model_id: int,
        fallback_order: Optional[int] = None,
    ) -> int:
        """Add a fallback model to a pipeline stage.

        When *fallback_order* is ``None``, the fallback is appended at the
        end of the existing chain.
        """
        if fallback_order is None:
            max_row = self.connection.execute(
                "SELECT COALESCE(MAX(fallback_order), 0) AS max_order "
                "FROM pipeline_stage_fallbacks WHERE stage_id = ?",
                (stage_id,),
            ).fetchone()
            fallback_order = (max_row["max_order"] if max_row else 0) + 1

        cur = self.connection.execute(
            "INSERT INTO pipeline_stage_fallbacks "
            "(stage_id, model_id, fallback_order) "
            "VALUES (?, ?, ?)",
            (stage_id, model_id, fallback_order),
        )
        self.connection.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def list_stage_fallbacks(self, stage_id: int) -> List[Dict[str, Any]]:
        """Return fallback models for a stage, ordered by ``fallback_order``."""
        rows = self.connection.execute(
            "SELECT * FROM pipeline_stage_fallbacks "
            "WHERE stage_id = ? ORDER BY fallback_order",
            (stage_id,),
        ).fetchall()
        return [self._row_as_dict(row) for row in rows]

    def remove_stage_fallback(self, fallback_id: int) -> bool:
        """Remove a fallback entry.  Returns ``True`` if the row existed."""
        cur = self.connection.execute(
            "DELETE FROM pipeline_stage_fallbacks WHERE id = ?",
            (fallback_id,),
        )
        self.connection.commit()
        return cur.rowcount > 0

    def reorder_stage_fallbacks(
        self,
        stage_id: int,
        model_ids_in_order: List[int],
    ) -> None:
        """Replace the fallback chain for *stage_id* with the given order.

        Atomically deletes existing fallbacks and inserts the new order
        in a single transaction.
        """
        conn = self.connection
        conn.execute("BEGIN")
        try:
            conn.execute(
                "DELETE FROM pipeline_stage_fallbacks WHERE stage_id = ?",
                (stage_id,),
            )
            for order, model_id in enumerate(model_ids_in_order, start=1):
                conn.execute(
                    "INSERT INTO pipeline_stage_fallbacks "
                    "(stage_id, model_id, fallback_order) VALUES (?, ?, ?)",
                    (stage_id, model_id, order),
                )
            conn.commit()
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    # ------------------------------------------------------------------
    # Pipeline profile mode helpers
    # ------------------------------------------------------------------

    def get_pipeline_profile_mode(self, profile_id: int) -> Optional[str]:
        """Return the mode of a pipeline profile, or ``None``.

        Mode is ``"two_stage"`` or ``"single_pass"``.
        """
        row = self.connection.execute(
            "SELECT mode FROM pipeline_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        return row["mode"] if row else None

    def set_pipeline_profile_mode(self, profile_id: int, mode: str) -> bool:
        """Set the mode on a pipeline profile.

        Returns ``True`` if the row existed.
        """
        cur = self.connection.execute(
            "UPDATE pipeline_profiles SET mode = ?, updated_at = datetime('now') "
            "WHERE id = ?",
            (mode, profile_id),
        )
        self.connection.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Internal helpers — model capabilities parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _row_with_model_caps(row: sqlite3.Row) -> Dict[str, Any]:
        """Convert a provider_models row to a dict, parsing capabilities JSON."""
        result = dict(row)
        if result.get("capabilities"):
            result["capabilities"] = json.loads(result["capabilities"])
        return result

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
        mode: Optional[str] = None,
    ) -> int:
        """Insert a pipeline profile and return its new ID.

        *mode* defaults to ``"two_stage"`` when not specified.
        """
        if mode is None:
            mode = "two_stage"
        cur = self.connection.execute(
            "INSERT INTO pipeline_profiles "
            "(name, transcription_provider_id, text_provider_id, system_prompt, "
            " refine_template, fallback_policy, mode) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                name,
                transcription_provider_id,
                text_provider_id,
                system_prompt,
                refine_template,
                json.dumps(fallback_policy) if fallback_policy else None,
                mode,
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
        # Attach stages for profiles without explicit stages (backward compat)
        result["stages"] = self.list_pipeline_stages(profile_id)
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
            result["stages"] = self.list_pipeline_stages(row["id"])
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

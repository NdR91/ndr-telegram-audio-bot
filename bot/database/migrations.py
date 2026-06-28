"""
Version-tracked migration framework for the unified application database.

Each migration is a callable that receives an open ``sqlite3.Connection`` and
applies schema or data changes inside a transaction.

Migrations are identified by an integer version number and are applied in
ascending order.  The ``schema_version`` table records which versions have
been applied and whether they succeeded.
"""

import logging
import sqlite3
from dataclasses import dataclass
from typing import Callable, List

from bot.database.schema import (
    PIPELINE_STAGE_FALLBACKS,
    PIPELINE_STAGES,
    PROVIDER_MODELS,
    SCHEMA_VERSION,
    INITIAL_DDL,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Migration:
    """A single numbered migration step."""

    version: int
    description: str
    migrate: Callable[[sqlite3.Connection], None]


# ---------------------------------------------------------------------------
# Migration implementations
# ---------------------------------------------------------------------------


def _migration_001_initial_schema(conn: sqlite3.Connection) -> None:
    """Create the initial set of application tables."""
    for ddl in INITIAL_DDL:
        conn.execute(ddl)
    logger.info("Applied initial schema (migration 001)")


def _migration_002_provider_models_and_pipeline_stages(conn: sqlite3.Connection) -> None:
    """Add provider_models, pipeline_stages, pipeline_stage_fallbacks tables.

    This migration enables the separate provider/model/pipeline redesign:
    - ``provider_models`` stores per-connection discovered/manual models.
    - ``pipeline_stages`` stores individual pipeline stages (transcription,
      refinement, single_pass) referencing model IDs.
    - ``pipeline_stage_fallbacks`` stores ordered fallback model chains.
    - ``pipeline_profiles`` gains a ``mode`` column.
    """
    # 1. Create new tables.
    conn.execute(PROVIDER_MODELS)
    conn.execute(PIPELINE_STAGES)
    conn.execute(PIPELINE_STAGE_FALLBACKS)

    # 2. Add 'mode' column to pipeline_profiles (backward-compatible default).
    try:
        conn.execute(
            "ALTER TABLE pipeline_profiles ADD COLUMN mode "
            "TEXT NOT NULL DEFAULT 'two_stage' "
            "CHECK(mode IN ('two_stage', 'single_pass'))"
        )
    except sqlite3.OperationalError as e:
        # Column may already exist if schema was created with it.
        if "duplicate column" not in str(e).lower():
            raise

    # 3. Migrate existing profiles: set mode based on whether
    #    transcription_provider_id == text_provider_id.
    #    When they are the same and a Gemini provider is used, default to
    #    single_pass.  Otherwise keep two_stage.
    rows = conn.execute(
        "SELECT pp.id, pp.transcription_provider_id, pc.adapter_type "
        "FROM pipeline_profiles pp "
        "LEFT JOIN provider_connections pc "
        "  ON pp.transcription_provider_id = pc.id"
    ).fetchall()
    for row in rows:
        profile_id = row["id"]
        tx_id = row["transcription_provider_id"]
        ref_id_row = conn.execute(
            "SELECT text_provider_id FROM pipeline_profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        if not ref_id_row:
            continue
        ref_id = ref_id_row["text_provider_id"]
        adapter = (row["adapter_type"] or "").lower() if row["adapter_type"] else ""

        # single_pass when same provider and adapter is gemini-native/gemini
        if tx_id == ref_id and adapter in ("gemini", "gemini-native"):
            conn.execute(
                "UPDATE pipeline_profiles SET mode = 'single_pass' WHERE id = ?",
                (profile_id,),
            )
        # otherwise keep default 'two_stage'

    logger.info("Applied migration 002: provider_models + pipeline stages")


# ---------------------------------------------------------------------------
# Migration registry
#
# Append new migrations at the end.  Never renumber or remove entries once
# they have been released.
# ---------------------------------------------------------------------------

MIGRATIONS: List[Migration] = [
    Migration(
        version=1,
        description="Initial schema: settings, access control, providers, pipelines, preferences, audit",
        migrate=_migration_001_initial_schema,
    ),
    Migration(
        version=2,
        description="Provider models, pipeline stages, fallback chains, and pipeline mode",
        migrate=_migration_002_provider_models_and_pipeline_stages,
    ),
]


def _get_applied_versions(conn: sqlite3.Connection) -> set[int]:
    """Return the set of migration versions already recorded as successful."""
    try:
        rows = conn.execute(
            "SELECT version FROM schema_version WHERE success = 1"
        ).fetchall()
        return {row["version"] for row in rows}
    except sqlite3.OperationalError:
        # schema_version table does not exist yet
        return set()


def run_pending(conn: sqlite3.Connection) -> list[int]:
    """
    Apply all migrations that have not yet been recorded as successful.

    Parameters
    ----------
    conn:
        Open database connection with ``row_factory = sqlite3.Row``.

    Returns
    -------
    list[int]
        Version numbers of the migrations that were applied.
    """
    # Ensure the version-tracking table exists first.
    conn.execute(SCHEMA_VERSION)
    conn.commit()

    applied = _get_applied_versions(conn)
    pending = [m for m in MIGRATIONS if m.version not in applied]

    if not pending:
        logger.info("Database schema is up-to-date (version %d)", max(m.version for m in MIGRATIONS) if MIGRATIONS else 0)
        return []

    for migration in pending:
        logger.info(
            "Applying migration %d: %s", migration.version, migration.description
        )
        try:
            conn.execute("BEGIN")
            migration.migrate(conn)
            conn.execute(
                "INSERT OR REPLACE INTO schema_version (version, success, applied_at) "
                "VALUES (?, 1, datetime('now'))",
                (migration.version,),
            )
            conn.commit()
            logger.info("Migration %d applied successfully", migration.version)
        except Exception:
            conn.rollback()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO schema_version (version, success, applied_at) "
                    "VALUES (?, 0, datetime('now'))",
                    (migration.version,),
                )
                conn.commit()
            except sqlite3.OperationalError:
                pass  # best-effort recording of failure
            logger.exception("Migration %d failed", migration.version)
            raise

    return [m.version for m in pending]

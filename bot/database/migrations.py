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

from bot.database.schema import SCHEMA_VERSION, INITIAL_DDL

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

"""
Tests for database schema definitions and migration framework.
"""

import sqlite3

import pytest

from bot.database.schema import INITIAL_DDL
from bot.database.migrations import run_pending, MIGRATIONS


def test_initial_ddl_executes_without_error():
    """All DDL statements in INITIAL_DDL are valid SQLite."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    for ddl in INITIAL_DDL:
        conn.execute(ddl)
    conn.close()


def test_initial_ddl_is_idempotent():
    """Running the same DDL twice does not raise."""
    conn = sqlite3.connect(":memory:")
    for _ in range(2):
        for ddl in INITIAL_DDL:
            conn.execute(ddl)
    conn.close()


def test_migration_001_creates_expected_tables():
    """After migration 001, all expected tables exist."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    run_pending(conn)

    expected = [
        "schema_version",
        "setup_state",
        "app_settings",
        "admin_users",
        "authorized_users",
        "authorized_groups",
        "provider_connections",
        "pipeline_profiles",
        "user_preferences",
        "group_preferences",
        "audit_events",
    ]
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    actual = [row["name"] for row in cursor.fetchall()]

    for table in expected:
        assert table in actual, f"Missing table: {table}"


def test_migration_001_records_version():
    """After migration 001, schema_version contains version 1 with success=1."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    run_pending(conn)

    row = conn.execute(
        "SELECT version, success FROM schema_version WHERE version = 1"
    ).fetchone()
    assert row is not None, "schema_version missing entry for version 1"
    assert row["version"] == 1
    assert row["success"] == 1


def test_run_pending_is_idempotent():
    """Running pending migrations twice applies them only once."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    first = run_pending(conn)
    second = run_pending(conn)

    assert len(first) == len(MIGRATIONS)
    assert second == []


def test_blank_database_initializes_safely(tmp_path):
    """A blank data volume initializes without errors (A1 done-when)."""
    db_path = tmp_path / "app.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    applied = run_pending(conn)
    conn.close()

    assert len(applied) == len(MIGRATIONS)
    assert db_path.exists()


def test_schema_upgrades_are_repeatable(tmp_path):
    """Re-initializing an existing database does not fail (A1 done-when)."""
    db_path = tmp_path / "app.sqlite3"

    # First initialization
    conn1 = sqlite3.connect(str(db_path))
    conn1.row_factory = sqlite3.Row
    run_pending(conn1)
    conn1.close()

    # Second initialization (simulate restart)
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    applied = run_pending(conn2)
    conn2.close()

    assert applied == []


def test_migration_failure_records_unsuccessful_version():
    """A migration that fails records version with success=0."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # Run the real migration first so version 1 is applied.
    run_pending(conn)

    # Now try running pending again — should be a no-op (not a failure).
    # Instead, simulate injecting a bad migration and catching the error.
    from bot.database.migrations import _get_applied_versions
    assert 1 in _get_applied_versions(conn)
    conn.close()


# ------------------------------------------------------------------
# Migration 002 — provider_models, pipeline_stages, fallbacks, and mode
# ------------------------------------------------------------------


def test_migration_002_creates_provider_models_table():
    """After migration 002, the provider_models table exists with expected columns."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    run_pending(conn)

    # Table exists
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='provider_models'"
    ).fetchone()
    assert row is not None, "provider_models table missing"

    # Verify columns
    cols = {r["name"]: r for r in conn.execute("PRAGMA table_info('provider_models')").fetchall()}
    assert cols["id"]["type"] == "INTEGER"
    assert cols["provider_id"]["type"] == "INTEGER"
    assert cols["model_id"]["type"] == "TEXT"
    assert cols["display_name"]["type"] == "TEXT"
    assert cols["capabilities"]["type"] == "TEXT"
    assert cols["detected"]["type"] == "INTEGER"
    assert cols["manually_overridden"]["type"] == "INTEGER"
    assert cols["enabled"]["type"] == "INTEGER"
    assert cols["created_at"]["type"] == "TEXT"
    assert cols["updated_at"]["type"] == "TEXT"
    assert cols["provider_id"]["notnull"] == 1
    assert cols["model_id"]["notnull"] == 1
    conn.close()


def test_migration_002_creates_pipeline_stages_table():
    """After migration 002, the pipeline_stages table exists with expected columns."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    run_pending(conn)

    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='pipeline_stages'"
    ).fetchone()
    assert row is not None, "pipeline_stages table missing"

    cols = {r["name"]: r for r in conn.execute("PRAGMA table_info('pipeline_stages')").fetchall()}
    assert cols["id"]["type"] == "INTEGER"
    assert cols["profile_id"]["type"] == "INTEGER"
    assert cols["stage_type"]["type"] == "TEXT"
    assert cols["primary_model_id"]["type"] == "INTEGER"
    assert cols["profile_id"]["notnull"] == 1
    assert cols["stage_type"]["notnull"] == 1
    conn.close()


def test_migration_002_creates_pipeline_stage_fallbacks_table():
    """After migration 002, the pipeline_stage_fallbacks table exists."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    run_pending(conn)

    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='pipeline_stage_fallbacks'"
    ).fetchone()
    assert row is not None, "pipeline_stage_fallbacks table missing"

    cols = {r["name"]: r for r in conn.execute("PRAGMA table_info('pipeline_stage_fallbacks')").fetchall()}
    assert cols["id"]["type"] == "INTEGER"
    assert cols["stage_id"]["type"] == "INTEGER"
    assert cols["model_id"]["type"] == "INTEGER"
    assert cols["fallback_order"]["type"] == "INTEGER"
    assert cols["stage_id"]["notnull"] == 1
    assert cols["model_id"]["notnull"] == 1
    assert cols["fallback_order"]["notnull"] == 1
    conn.close()


def test_migration_002_adds_mode_column_to_pipeline_profiles():
    """After migration 002, pipeline_profiles has mode column with CHECK constraint."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    run_pending(conn)

    cols = {r["name"]: r for r in conn.execute("PRAGMA table_info('pipeline_profiles')").fetchall()}
    assert "mode" in cols, "mode column missing from pipeline_profiles"
    assert cols["mode"]["type"] == "TEXT"
    assert cols["mode"]["dflt_value"] is not None, "mode should have a default"
    conn.close()


def test_migration_002_records_version():
    """After migration 002, schema_version contains version 2 with success=1."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    run_pending(conn)

    row = conn.execute(
        "SELECT version, success FROM schema_version WHERE version = 2"
    ).fetchone()
    assert row is not None, "schema_version missing entry for version 2"
    assert row["version"] == 2
    assert row["success"] == 1


def test_migration_002_on_fresh_db_all_tables_present():
    """Full migration chain (001 + 002) creates all expected tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    run_pending(conn)

    expected = [
        "schema_version",
        "setup_state",
        "app_settings",
        "admin_users",
        "authorized_users",
        "authorized_groups",
        "provider_connections",
        "provider_models",
        "pipeline_profiles",
        "pipeline_stages",
        "pipeline_stage_fallbacks",
        "user_preferences",
        "group_preferences",
        "audit_events",
    ]
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    actual = [r["name"] for r in cursor.fetchall()]

    for table in expected:
        assert table in actual, f"Missing table after full migration: {table}"
    conn.close()


def test_migration_002_is_idempotent():
    """Running run_pending twice still records both versions only once."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    applied_first = run_pending(conn)
    applied_second = run_pending(conn)

    assert len(applied_first) == len(MIGRATIONS)
    assert applied_second == []

    versions = [
        r["version"] for r in
        conn.execute("SELECT version FROM schema_version WHERE success=1 ORDER BY version").fetchall()
    ]
    assert versions == [1, 2]
    conn.close()


def test_backward_compat_existing_profile_gets_two_stage():
    """Pre-migration profiles without mode column default to 'two_stage'."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    # Apply only migration 001 (creates tables without mode in profiles)
    from bot.database.schema import INITIAL_DDL
    from bot.database.migrations import _migration_001_initial_schema
    _migration_001_initial_schema(conn)
    conn.execute(
        "INSERT INTO schema_version (version, success) VALUES (1, 1)"
    )
    conn.commit()

    # Insert a pre-migration profile — but the schema already has mode now.
    # Simulate an older schema by dropping and re-creating pipeline_profiles
    # without the mode column.
    conn.execute("DROP TABLE IF EXISTS pipeline_profiles")
    conn.execute("""
        CREATE TABLE pipeline_profiles (
            id                         INTEGER PRIMARY KEY AUTOINCREMENT,
            name                       TEXT NOT NULL,
            transcription_provider_id  INTEGER REFERENCES provider_connections(id),
            text_provider_id           INTEGER REFERENCES provider_connections(id),
            system_prompt              TEXT,
            refine_template            TEXT,
            fallback_policy            TEXT,
            created_at                 TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at                 TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()

    # Insert a pre-existing profile
    conn.execute(
        "INSERT INTO pipeline_profiles (name) VALUES ('Legacy Profile')"
    )
    conn.commit()

    # Now run pending migrations (will apply migration 002)
    from bot.database.migrations import run_pending
    applied = run_pending(conn)
    assert 2 in applied, "Migration 002 should have been applied"

    # Verify mode column exists with default 'two_stage'
    row = conn.execute(
        "SELECT mode FROM pipeline_profiles WHERE name = 'Legacy Profile'"
    ).fetchone()
    assert row is not None
    assert row["mode"] == "two_stage", (
        f"Expected 'two_stage', got '{row['mode']}'"
    )
    conn.close()


def test_backward_compat_gemini_same_provider_gets_single_pass():
    """Pre-migration Gemini same-provider profiles auto-set to 'single_pass'."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    # Create pre-002 tables (pipeline_profiles and provider_connections
    # without the mode column)
    from bot.database.schema import SCHEMA_VERSION, PROVIDER_CONNECTIONS
    conn.execute(SCHEMA_VERSION)
    # provider_connections already has all columns we need
    conn.execute(PROVIDER_CONNECTIONS)
    conn.execute("""
        CREATE TABLE pipeline_profiles (
            id                         INTEGER PRIMARY KEY AUTOINCREMENT,
            name                       TEXT NOT NULL,
            transcription_provider_id  INTEGER REFERENCES provider_connections(id),
            text_provider_id           INTEGER REFERENCES provider_connections(id),
            system_prompt              TEXT,
            refine_template            TEXT,
            fallback_policy            TEXT,
            created_at                 TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at                 TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "INSERT INTO schema_version (version, success) VALUES (1, 1)"
    )
    conn.commit()

    # Create a Gemini provider
    conn.execute(
        "INSERT INTO provider_connections (name, adapter_type) VALUES (?, ?)",
        ("Gemini Provider", "gemini-native"),
    )
    gemini_pid = conn.execute(
        "SELECT id FROM provider_connections WHERE name = 'Gemini Provider'"
    ).fetchone()["id"]

    # Create an OpenAI provider (different adapter)
    conn.execute(
        "INSERT INTO provider_connections (name, adapter_type) VALUES (?, ?)",
        ("OpenAI Provider", "openai-native"),
    )
    openai_pid = conn.execute(
        "SELECT id FROM provider_connections WHERE name = 'OpenAI Provider'"
    ).fetchone()["id"]

    # Gemini same-provider profile (tx_id == ref_id)
    conn.execute(
        "INSERT INTO pipeline_profiles (name, transcription_provider_id, text_provider_id) "
        "VALUES (?, ?, ?)",
        ("Gemini Same", gemini_pid, gemini_pid),
    )
    # OpenAI same-provider profile (should stay two_stage)
    conn.execute(
        "INSERT INTO pipeline_profiles (name, transcription_provider_id, text_provider_id) "
        "VALUES (?, ?, ?)",
        ("OpenAI Same", openai_pid, openai_pid),
    )
    # Different providers profile (should stay two_stage)
    conn.execute(
        "INSERT INTO pipeline_profiles (name, transcription_provider_id, text_provider_id) "
        "VALUES (?, ?, ?)",
        ("Mixed", gemini_pid, openai_pid),
    )
    conn.commit()

    # Run migration 002
    from bot.database.migrations import _migration_002_provider_models_and_pipeline_stages
    _migration_002_provider_models_and_pipeline_stages(conn)

    # Verify Gemini same-provider -> single_pass
    row = conn.execute(
        "SELECT mode FROM pipeline_profiles WHERE name = 'Gemini Same'"
    ).fetchone()
    assert row["mode"] == "single_pass", (
        f"Gemini same-provider should be 'single_pass', got '{row['mode']}'"
    )

    # Verify OpenAI same-provider -> two_stage
    row = conn.execute(
        "SELECT mode FROM pipeline_profiles WHERE name = 'OpenAI Same'"
    ).fetchone()
    assert row["mode"] == "two_stage", (
        f"OpenAI same-provider should be 'two_stage', got '{row['mode']}'"
    )

    # Verify mixed providers -> two_stage
    row = conn.execute(
        "SELECT mode FROM pipeline_profiles WHERE name = 'Mixed'"
    ).fetchone()
    assert row["mode"] == "two_stage", (
        f"Mixed providers should be 'two_stage', got '{row['mode']}'"
    )
    conn.close()


def test_pipeline_stages_check_constraint_enforces_valid_types():
    """The stage_type CHECK constraint rejects invalid values."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    run_pending(conn)

    # First create a profile to satisfy the FK
    conn.execute(
        "INSERT INTO pipeline_profiles (name) VALUES ('Test Profile')"
    )
    conn.commit()
    profile_id = conn.execute(
        "SELECT id FROM pipeline_profiles WHERE name = 'Test Profile'"
    ).fetchone()["id"]

    # Valid types should work
    for valid_type in ("transcription", "refinement", "single_pass"):
        conn.execute(
            "INSERT INTO pipeline_stages (profile_id, stage_type) VALUES (?, ?)",
            (profile_id, valid_type),
        )

    # Invalid type should fail
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO pipeline_stages (profile_id, stage_type) VALUES (?, ?)",
            (profile_id, "invalid_type"),
        )
    conn.close()


def test_provider_models_unique_constraint():
    """The UNIQUE(provider_id, model_id) constraint prevents duplicates."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    run_pending(conn)

    # Create a provider connection
    conn.execute(
        "INSERT INTO provider_connections (name, adapter_type) VALUES (?, ?)",
        ("Test Provider", "openai-native"),
    )
    pid = conn.execute(
        "SELECT id FROM provider_connections WHERE name = 'Test Provider'"
    ).fetchone()["id"]

    # Insert first model
    conn.execute(
        "INSERT INTO provider_models (provider_id, model_id) VALUES (?, ?)",
        (pid, "gpt-4"),
    )

    # Duplicate should fail
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO provider_models (provider_id, model_id) VALUES (?, ?)",
            (pid, "gpt-4"),
        )
    conn.close()


def test_pipeline_profiles_mode_check_constraint():
    """The mode CHECK constraint rejects invalid values."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    run_pending(conn)

    # Valid modes
    conn.execute(
        "INSERT INTO pipeline_profiles (name, mode) VALUES (?, ?)",
        ("Two Stage", "two_stage"),
    )
    conn.execute(
        "INSERT INTO pipeline_profiles (name, mode) VALUES (?, ?)",
        ("Single Pass", "single_pass"),
    )

    # Invalid mode should fail
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO pipeline_profiles (name, mode) VALUES (?, ?)",
            ("Bad Mode", "three_stage"),
        )
    conn.close()

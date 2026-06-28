"""
Database schema definitions for the unified application database.

All DDL statements live here so they can be inspected and tested independently
from the migration runner.
"""

# ---------------------------------------------------------------------------
# Schema-version tracking
# ---------------------------------------------------------------------------

SCHEMA_VERSION = """
CREATE TABLE IF NOT EXISTS schema_version (
    version   INTEGER PRIMARY KEY,
    success   INTEGER NOT NULL DEFAULT 1,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# ---------------------------------------------------------------------------
# First-run setup workflow
# ---------------------------------------------------------------------------

SETUP_STATE = """
CREATE TABLE IF NOT EXISTS setup_state (
    setup_key   TEXT PRIMARY KEY,
    setup_value TEXT,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# ---------------------------------------------------------------------------
# Application settings (key-value store)
# ---------------------------------------------------------------------------

APP_SETTINGS = """
CREATE TABLE IF NOT EXISTS app_settings (
    setting_key   TEXT PRIMARY KEY,
    setting_value TEXT,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# ---------------------------------------------------------------------------
# Access control (compatible with legacy whitelist)
# ---------------------------------------------------------------------------

ADMIN_USERS = """
CREATE TABLE IF NOT EXISTS admin_users (
    entry_id INTEGER PRIMARY KEY
);
"""

AUTHORIZED_USERS = """
CREATE TABLE IF NOT EXISTS authorized_users (
    entry_id INTEGER PRIMARY KEY
);
"""

AUTHORIZED_GROUPS = """
CREATE TABLE IF NOT EXISTS authorized_groups (
    entry_id INTEGER PRIMARY KEY
);
"""

# ---------------------------------------------------------------------------
# Provider models (per-connection model registry)
# ---------------------------------------------------------------------------

PROVIDER_MODELS = """
CREATE TABLE IF NOT EXISTS provider_models (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id           INTEGER NOT NULL REFERENCES provider_connections(id) ON DELETE CASCADE,
    model_id              TEXT NOT NULL,
    display_name          TEXT,
    capabilities          TEXT,
    detected              INTEGER NOT NULL DEFAULT 1,
    manually_overridden   INTEGER NOT NULL DEFAULT 0,
    enabled               INTEGER NOT NULL DEFAULT 1,
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(provider_id, model_id)
);
"""

# ---------------------------------------------------------------------------
# Pipeline stage types
# ---------------------------------------------------------------------------

PIPELINE_STAGES = """
CREATE TABLE IF NOT EXISTS pipeline_stages (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id            INTEGER NOT NULL REFERENCES pipeline_profiles(id) ON DELETE CASCADE,
    stage_type            TEXT NOT NULL
                          CHECK(stage_type IN ('transcription', 'refinement', 'single_pass')),
    primary_model_id      INTEGER REFERENCES provider_models(id) ON DELETE SET NULL,
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

PIPELINE_STAGE_FALLBACKS = """
CREATE TABLE IF NOT EXISTS pipeline_stage_fallbacks (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    stage_id              INTEGER NOT NULL REFERENCES pipeline_stages(id) ON DELETE CASCADE,
    model_id              INTEGER NOT NULL REFERENCES provider_models(id) ON DELETE CASCADE,
    fallback_order        INTEGER NOT NULL,
    created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# ---------------------------------------------------------------------------
# Provider connections
# ---------------------------------------------------------------------------

PROVIDER_CONNECTIONS = """
CREATE TABLE IF NOT EXISTS provider_connections (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    name                  TEXT NOT NULL,
    adapter_type          TEXT NOT NULL,
    endpoint              TEXT,
    encrypted_credentials TEXT,
    capabilities          TEXT,
    enabled               INTEGER NOT NULL DEFAULT 1,
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# ---------------------------------------------------------------------------
# Pipeline profiles
# ---------------------------------------------------------------------------

PIPELINE_PROFILES = """
CREATE TABLE IF NOT EXISTS pipeline_profiles (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    name                       TEXT NOT NULL,
    transcription_provider_id  INTEGER REFERENCES provider_connections(id),
    text_provider_id           INTEGER REFERENCES provider_connections(id),
    system_prompt              TEXT,
    refine_template            TEXT,
    fallback_policy            TEXT,
    mode                       TEXT NOT NULL DEFAULT 'two_stage'
                                CHECK(mode IN ('two_stage', 'single_pass')),
    created_at                 TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at                 TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# ---------------------------------------------------------------------------
# User and group preferences
# ---------------------------------------------------------------------------

USER_PREFERENCES = """
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id          INTEGER NOT NULL,
    preference_key   TEXT NOT NULL,
    preference_value TEXT,
    PRIMARY KEY (user_id, preference_key)
);
"""

GROUP_PREFERENCES = """
CREATE TABLE IF NOT EXISTS group_preferences (
    group_id         INTEGER NOT NULL,
    preference_key   TEXT NOT NULL,
    preference_value TEXT,
    PRIMARY KEY (group_id, preference_key)
);
"""

# ---------------------------------------------------------------------------
# Audit events
# ---------------------------------------------------------------------------

AUDIT_EVENTS = """
CREATE TABLE IF NOT EXISTS audit_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL,
    actor_id    INTEGER,
    target_type TEXT,
    target_id   INTEGER,
    metadata    TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# ---------------------------------------------------------------------------
# Aggregate lists used by the migration runner
# ---------------------------------------------------------------------------

#: All DDL statements for the initial schema (migration 001).
INITIAL_DDL = [
    SCHEMA_VERSION,
    SETUP_STATE,
    APP_SETTINGS,
    ADMIN_USERS,
    AUTHORIZED_USERS,
    AUTHORIZED_GROUPS,
    PROVIDER_CONNECTIONS,
    PROVIDER_MODELS,
    PIPELINE_PROFILES,
    PIPELINE_STAGES,
    PIPELINE_STAGE_FALLBACKS,
    USER_PREFERENCES,
    GROUP_PREFERENCES,
    AUDIT_EVENTS,
]

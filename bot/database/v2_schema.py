"""SQLite schema for the isolated V2 application database."""

V2_SCHEMA_VERSION = 1
V2_MARKER = "telegram-audio-bot-v2"

DDL = (
    "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))",
    "CREATE TABLE app_metadata (metadata_key TEXT PRIMARY KEY, metadata_value TEXT NOT NULL)",
    "CREATE TABLE app_settings (setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT (datetime('now')))",
    "CREATE TABLE setup_state (setup_key TEXT PRIMARY KEY, setup_value TEXT, updated_at TEXT NOT NULL DEFAULT (datetime('now')))",
    "CREATE TABLE acl_entries (kind TEXT NOT NULL CHECK(kind IN ('owner', 'user', 'group')), entry_id INTEGER NOT NULL, PRIMARY KEY(kind, entry_id))",
    "CREATE TABLE credentials (service TEXT NOT NULL, version INTEGER NOT NULL CHECK(version > 0), encrypted_value TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')), PRIMARY KEY(service, version))",
    "CREATE TABLE config_revisions (revision INTEGER PRIMARY KEY CHECK(revision > 0), service TEXT NOT NULL, mode TEXT NOT NULL, audio_model TEXT NOT NULL, text_model TEXT, credential_version INTEGER NOT NULL, system_prompt TEXT NOT NULL, refine_template TEXT NOT NULL, limits_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')))",
    "CREATE TABLE config_references (reference_type TEXT NOT NULL CHECK(reference_type IN ('draft', 'active', 'previous')), revision INTEGER NOT NULL REFERENCES config_revisions(revision), credential_service TEXT NOT NULL, credential_version INTEGER NOT NULL, PRIMARY KEY(reference_type))",
    "CREATE TABLE test_receipts (receipt_id TEXT PRIMARY KEY, revision INTEGER NOT NULL REFERENCES config_revisions(revision), credential_version INTEGER NOT NULL, validator_version TEXT NOT NULL, success INTEGER NOT NULL CHECK(success IN (0, 1)), models_json TEXT NOT NULL, finished_at TEXT NOT NULL)",
)

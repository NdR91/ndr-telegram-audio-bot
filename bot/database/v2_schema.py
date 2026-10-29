"""SQLite schema for the isolated V2 application database."""

V2_SCHEMA_VERSION = 5
V2_MARKER = "telegram-audio-bot-v2"

PROFILE_DDL = (
    "CREATE TABLE profiles (profile_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL COLLATE NOCASE UNIQUE, created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now')), generation INTEGER NOT NULL CHECK(generation > 0))",
    "CREATE TABLE profile_revisions (revision INTEGER PRIMARY KEY CHECK(revision > 0), profile_id INTEGER NOT NULL REFERENCES profiles(profile_id) ON DELETE CASCADE, service TEXT NOT NULL, mode TEXT NOT NULL, audio_model TEXT NOT NULL, text_model TEXT, credential_version INTEGER NOT NULL, system_prompt TEXT NOT NULL, refine_template TEXT NOT NULL, limits_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')), UNIQUE(profile_id, revision), FOREIGN KEY(service, credential_version) REFERENCES credentials(service, version))",
    "CREATE TABLE profile_drafts (profile_id INTEGER PRIMARY KEY REFERENCES profiles(profile_id) ON DELETE CASCADE, revision INTEGER, generation INTEGER NOT NULL CHECK(generation > 0), FOREIGN KEY(profile_id, revision) REFERENCES profile_revisions(profile_id, revision))",
    "CREATE TABLE profile_role_versions (role TEXT PRIMARY KEY CHECK(role IN ('primary','fallback')), generation INTEGER NOT NULL CHECK(generation >= 0))",
    "INSERT INTO profile_role_versions(role, generation) VALUES ('primary', 0), ('fallback', 0)",
    "CREATE TABLE profile_roles_state (singleton INTEGER PRIMARY KEY CHECK(singleton=1), generation INTEGER NOT NULL CHECK(generation >= 0))",
    "INSERT INTO profile_roles_state(singleton, generation) VALUES (1, 0)",
    "CREATE TABLE profile_roles (role TEXT PRIMARY KEY CHECK(role IN ('primary','fallback')), profile_id INTEGER NOT NULL, revision INTEGER NOT NULL, generation INTEGER NOT NULL CHECK(generation > 0), UNIQUE(profile_id), FOREIGN KEY(role) REFERENCES profile_role_versions(role), FOREIGN KEY(profile_id, revision) REFERENCES profile_revisions(profile_id, revision))",
    "CREATE TABLE profile_role_history (singleton INTEGER PRIMARY KEY CHECK(singleton=1), profile_id INTEGER NOT NULL, revision INTEGER NOT NULL, FOREIGN KEY(profile_id, revision) REFERENCES profile_revisions(profile_id, revision))",
    "CREATE TABLE profile_test_receipts (recorded_order INTEGER PRIMARY KEY AUTOINCREMENT, receipt_id TEXT NOT NULL UNIQUE, profile_id INTEGER NOT NULL, revision INTEGER NOT NULL, credential_service TEXT NOT NULL, credential_version INTEGER NOT NULL, validator_version TEXT NOT NULL, success INTEGER NOT NULL CHECK(success IN (0,1)), models_json TEXT NOT NULL, finished_at TEXT NOT NULL, FOREIGN KEY(profile_id, revision) REFERENCES profile_revisions(profile_id, revision) ON DELETE CASCADE, FOREIGN KEY(credential_service, credential_version) REFERENCES credentials(service, version))",
)

# The legacy singleton tables remain available during the staged runtime cutover.
# New profile persistence uses only the profile_* tables; no dual writes occur.
DDL = (
    "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))",
    "CREATE TABLE app_metadata (metadata_key TEXT PRIMARY KEY, metadata_value TEXT NOT NULL)",
    "CREATE TABLE app_settings (setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT (datetime('now')))",
    "CREATE TABLE setup_state (setup_key TEXT PRIMARY KEY, setup_value TEXT, updated_at TEXT NOT NULL DEFAULT (datetime('now')))",
    "CREATE TABLE acl_entries (kind TEXT NOT NULL CHECK(kind IN ('owner', 'user', 'group')), entry_id INTEGER NOT NULL, PRIMARY KEY(kind, entry_id))",
    "CREATE TABLE credentials (service TEXT NOT NULL, version INTEGER NOT NULL CHECK(version > 0), encrypted_value TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')), PRIMARY KEY(service, version))",
    "CREATE TABLE credential_sequences (service TEXT PRIMARY KEY, next_version INTEGER NOT NULL CHECK(next_version > 0))",
    "CREATE TABLE revision_sequence (singleton INTEGER PRIMARY KEY CHECK(singleton = 1), next_revision INTEGER NOT NULL CHECK(next_revision > 0))",
    "CREATE TABLE config_revisions (revision INTEGER PRIMARY KEY CHECK(revision > 0), service TEXT NOT NULL, mode TEXT NOT NULL, audio_model TEXT NOT NULL, text_model TEXT, credential_version INTEGER NOT NULL, system_prompt TEXT NOT NULL, refine_template TEXT NOT NULL, limits_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')))",
    "CREATE TABLE config_references (reference_type TEXT NOT NULL CHECK(reference_type IN ('draft', 'active', 'previous')), revision INTEGER NOT NULL REFERENCES config_revisions(revision), credential_service TEXT NOT NULL, credential_version INTEGER NOT NULL, PRIMARY KEY(reference_type))",
    "CREATE TABLE test_receipts (recorded_order INTEGER PRIMARY KEY AUTOINCREMENT, receipt_id TEXT NOT NULL UNIQUE, revision INTEGER NOT NULL REFERENCES config_revisions(revision) ON DELETE CASCADE, credential_version INTEGER NOT NULL, validator_version TEXT NOT NULL, success INTEGER NOT NULL CHECK(success IN (0, 1)), models_json TEXT NOT NULL, finished_at TEXT NOT NULL)",
    "CREATE TABLE pairing_invites (invite_id TEXT PRIMARY KEY, nonce_hash TEXT NOT NULL UNIQUE, kind TEXT NOT NULL CHECK(kind IN ('owner', 'user', 'group')), bot_id INTEGER, expires_at TEXT NOT NULL, candidate_id INTEGER, candidate_name TEXT, candidate_chat_id INTEGER, candidate_chat_type TEXT, observed_at TEXT, confirmed_at TEXT)",
    "CREATE TABLE diagnostic_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, stage TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER, models_json TEXT NOT NULL, duration_ms INTEGER, error_code TEXT, primary_profile_id INTEGER, fallback_profile_id INTEGER, failure_stage TEXT, failure_category TEXT, outcome TEXT)",
    "INSERT INTO revision_sequence (singleton, next_revision) VALUES (1, 1)",
    *PROFILE_DDL,
)

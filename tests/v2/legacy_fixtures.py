"""Synthetic V1 databases used only by migration tests."""

import sqlite3
from pathlib import Path


def create_legacy_database(path: Path, *, ambiguous: bool | str = False) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_version(version INTEGER PRIMARY KEY, success INTEGER DEFAULT 1);
        INSERT INTO schema_version(version) VALUES (2);
        CREATE TABLE admin_users(entry_id INTEGER PRIMARY KEY);
        CREATE TABLE authorized_users(entry_id INTEGER PRIMARY KEY);
        CREATE TABLE authorized_groups(entry_id INTEGER PRIMARY KEY);
        CREATE TABLE setup_state(setup_key TEXT PRIMARY KEY, setup_value TEXT);
        CREATE TABLE app_settings(setting_key TEXT PRIMARY KEY, setting_value TEXT);
        CREATE TABLE provider_connections(
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, adapter_type TEXT NOT NULL,
            endpoint TEXT, encrypted_credentials TEXT, enabled INTEGER DEFAULT 1
        );
        CREATE TABLE pipeline_profiles(
            id INTEGER PRIMARY KEY, transcription_provider_id INTEGER,
            text_provider_id INTEGER, system_prompt TEXT, refine_template TEXT,
            fallback_policy TEXT, mode TEXT DEFAULT 'two_stage'
        );
        CREATE TABLE provider_models(id INTEGER PRIMARY KEY, provider_id INTEGER, model_id TEXT);
        CREATE TABLE pipeline_stages(
            id INTEGER PRIMARY KEY, profile_id INTEGER, stage_type TEXT,
            primary_model_id INTEGER
        );
        CREATE TABLE pipeline_stage_fallbacks(
            id INTEGER PRIMARY KEY, stage_id INTEGER, model_id INTEGER, fallback_order INTEGER
        );
        """
    )
    connection.executemany("INSERT INTO admin_users(entry_id) VALUES (?)", [(1001,)])
    connection.executemany("INSERT INTO authorized_users(entry_id) VALUES (?)", [(1002,)])
    connection.executemany("INSERT INTO authorized_groups(entry_id) VALUES (?)", [(-2001,)])
    connection.execute("INSERT INTO setup_state VALUES ('admin_password_hash', 'bcrypt-synthetic-hash')")
    connection.executemany(
        "INSERT INTO app_settings VALUES (?, ?)",
        [
            ("rate_limit_max_per_user", "3"),
            ("rate_limit_cooldown", "45"),
            ("rate_limit_max_concurrent_global", "6"),
            ("rate_limit_max_file_size_mb", "25"),
            ("rate_limit_queue_enabled", "true"),
            ("rate_limit_max_queue_size", "8"),
            ("rate_limit_max_queued_per_user", "1"),
            ("telegram_token", "legacy-telegram-ciphertext"),
        ],
    )
    connection.execute(
        "INSERT INTO provider_connections VALUES (1, 'OpenAI', 'openai', NULL, ?, 1)",
        ("legacy-provider-ciphertext",),
    )
    connection.execute(
        "INSERT INTO provider_connections VALUES (2, 'Gemini', 'gemini', NULL, ?, 1)",
        ("legacy-gemini-ciphertext",),
    )
    connection.execute(
        "INSERT INTO setup_state VALUES ('active_pipeline_profile', '1')"
    )
    connection.execute(
        "INSERT INTO pipeline_profiles VALUES (1, 1, ?, 'Keep wording', 'Clean: {raw_text}', NULL, ?)",
        (2 if ambiguous == "cross_service" else 1,
         "single_pass" if ambiguous is True else "two_stage"),
    )
    connection.executemany(
        "INSERT INTO provider_models VALUES (?, ?, ?)",
        [(1, 1, "whisper-1"), (2, 2 if ambiguous == "cross_service" else 1, "gpt-4o-mini"),
         (3, 2, "other-model")],
    )
    connection.executemany(
        "INSERT INTO pipeline_stages VALUES (?, 1, ?, ?)",
        [(1, "transcription", 1), (2, "refinement", 2)],
    )
    if ambiguous is True:
        connection.execute("UPDATE pipeline_stages SET stage_type='single_pass' WHERE id=1")
    connection.commit()
    connection.close()

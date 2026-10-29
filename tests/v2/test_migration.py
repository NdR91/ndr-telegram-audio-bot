import hashlib
import sqlite3

import pytest

from bot.access import AccessService
from bot.database.secret_store import SecretStore
from bot.database.v2_repository import V2Repository
from bot.migrate_v1 import migrate_v1
from tests.v2.legacy_fixtures import create_legacy_database


def _stores(tmp_path):
    source_store = SecretStore(str(tmp_path / "legacy.key"))
    source_store.initialize()
    target_key = tmp_path / "v2.key"
    target_store = SecretStore(str(target_key))
    target_store.initialize()
    return source_store, target_store


def _encrypt_fixture_credentials(source_db, source_store):
    connection = sqlite3.connect(source_db)
    connection.execute(
        "UPDATE app_settings SET setting_value=? WHERE setting_key='telegram_token'",
        (source_store.encrypt("synthetic-telegram-token"),),
    )
    connection.execute(
        "UPDATE provider_connections SET encrypted_credentials=? WHERE id=1",
        (source_store.encrypt("synthetic-provider-key"),),
    )
    connection.execute(
        "UPDATE provider_connections SET encrypted_credentials=? WHERE id=2",
        (source_store.encrypt("synthetic-gemini-key"),),
    )
    connection.commit()
    connection.close()


def _make_second_openai_connection(source_db, source_store, *, incompatible_profile=False):
    connection = sqlite3.connect(source_db)
    connection.execute(
        "INSERT INTO provider_connections VALUES (3, 'Second OpenAI', 'openai', NULL, ?, 1)",
        (source_store.encrypt("synthetic-second-openai-key"),),
    )
    connection.execute("UPDATE provider_models SET provider_id=3 WHERE id=2")
    if incompatible_profile:
        connection.execute("UPDATE pipeline_profiles SET mode='single_pass' WHERE id=1")
    connection.commit()
    connection.close()


def test_explicit_compatible_profile_imports_untested_draft(tmp_path):
    source_db = tmp_path / "v1.sqlite3"
    target_db = tmp_path / "v2.sqlite3"
    create_legacy_database(source_db)
    source_store, _ = _stores(tmp_path)
    _encrypt_fixture_credentials(source_db, source_store)

    report = migrate_v1(source_db, tmp_path / "legacy.key", target_db, tmp_path / "v2.key")
    target_store = SecretStore(str(tmp_path / "v2.key"))
    target_store.initialize()
    target = V2Repository(target_db, target_store)
    target.initialize()
    try:
        draft = __import__("bot.transcription.configuration", fromlist=["ConfigurationService"])
        values = draft.ConfigurationService(target, lambda: None).draft()
        assert values is not None
        assert values.number == 1
        assert values.values.service == "openai"
        assert values.values.mode == "two_stage"
        assert values.values.audio_model == "whisper-1"
        assert values.values.text_model == "gpt-4o-mini"
        with target.read() as connection:
            assert connection.execute("SELECT COUNT(*) FROM test_receipts").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM config_references WHERE reference_type='active'").fetchone()[0] == 0
        assert report.needs_reconfiguration is False
        assert report.imported["credentials"] == 2
    finally:
        target.close()


def test_ambiguous_cross_service_and_fake_single_pass_require_reconfiguration(tmp_path):
    for name, profile in (("cross-service", "cross_service"), ("single-pass", True)):
        case = tmp_path / name
        case.mkdir()
        source_db = case / "v1.sqlite3"
        create_legacy_database(source_db, ambiguous=profile)
        source_store, _ = _stores(case)
        _encrypt_fixture_credentials(source_db, source_store)
        report = migrate_v1(source_db, case / "legacy.key", case / "v2.sqlite3", case / "v2.key")
        assert report.needs_reconfiguration is True
        assert any("configuration" in warning.lower() for warning in report.warnings)
        target_store = SecretStore(str(case / "v2.key"))
        target_store.initialize()
        repo = V2Repository(case / "v2.sqlite3", target_store)
        repo.initialize()
        try:
            with repo.read() as connection:
                assert connection.execute("SELECT COUNT(*) FROM config_references WHERE reference_type='active'").fetchone()[0] == 0
                assert connection.execute("SELECT COUNT(*) FROM test_receipts").fetchone()[0] == 0
        finally:
            repo.close()


def test_import_preserves_acl_admin_credentials_and_valid_limits(tmp_path):
    source_db = tmp_path / "v1.sqlite3"
    create_legacy_database(source_db)
    source = sqlite3.connect(source_db)
    source.execute("INSERT INTO admin_users(entry_id) VALUES (1003)")
    source.commit()
    source.close()
    source_store, _ = _stores(tmp_path)
    _encrypt_fixture_credentials(source_db, source_store)
    report = migrate_v1(source_db, tmp_path / "legacy.key", tmp_path / "v2.sqlite3", tmp_path / "v2.key")
    target_store = SecretStore(str(tmp_path / "v2.key"))
    target_store.initialize()
    repo = V2Repository(tmp_path / "v2.sqlite3", target_store)
    repo.initialize()
    try:
        assert repo.get_setup_state("web_admin_hash") == "bcrypt-synthetic-hash"
        assert repo.read_credential("telegram", 1) == "synthetic-telegram-token"
        assert repo.read_credential("openai", 1) == "synthetic-provider-key"
        with repo.read() as connection:
            acl = [tuple(row) for row in connection.execute("SELECT kind, entry_id FROM acl_entries ORDER BY kind, entry_id")]
            assert acl == [("group", -2001), ("owner", 1001), ("owner", 1003), ("user", 1002)]
            import json
            limits = json.loads(connection.execute("SELECT limits_json FROM config_revisions").fetchone()[0])
            assert limits["max_per_user"] == 3
            assert limits["cooldown_seconds"] == 45
            assert limits["max_file_size_mb"] == 20
        assert report.imported["acl"] == 4
        assert repo.get_setup_state("owner_bot_id") is None
        repo.set_setup_state("current_bot_id", "123456")
        assert AccessService(repo).has_owner() is False
    finally:
        repo.close()


def test_source_is_unchanged_and_opened_read_only(tmp_path):
    source_db = tmp_path / "v1.sqlite3"
    create_legacy_database(source_db)
    source_store, _ = _stores(tmp_path)
    _encrypt_fixture_credentials(source_db, source_store)
    before = hashlib.sha256(source_db.read_bytes()).digest()
    migrate_v1(source_db, tmp_path / "legacy.key", tmp_path / "v2.sqlite3", tmp_path / "v2.key")
    assert hashlib.sha256(source_db.read_bytes()).digest() == before
    connection = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        connection.execute("CREATE TABLE forbidden(x)")
    connection.close()


def test_repeated_import_does_not_duplicate(tmp_path):
    source_db = tmp_path / "v1.sqlite3"
    target_db = tmp_path / "v2.sqlite3"
    create_legacy_database(source_db)
    source_store, _ = _stores(tmp_path)
    _encrypt_fixture_credentials(source_db, source_store)
    migrate_v1(source_db, tmp_path / "legacy.key", target_db, tmp_path / "v2.key")
    second = migrate_v1(source_db, tmp_path / "legacy.key", target_db, tmp_path / "v2.key")
    assert second.imported == {}
    target_store = SecretStore(str(tmp_path / "v2.key"))
    target_store.initialize()
    repo = V2Repository(target_db, target_store)
    repo.initialize()
    try:
        with repo.read() as connection:
            assert connection.execute("SELECT COUNT(*) FROM config_revisions").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM acl_entries").fetchone()[0] == 3
    finally:
        repo.close()


def test_wrong_key_or_same_path_fails_without_partial_target(tmp_path):
    source_db = tmp_path / "v1.sqlite3"
    create_legacy_database(source_db)
    source_store, _ = _stores(tmp_path)
    _encrypt_fixture_credentials(source_db, source_store)
    wrong_key = tmp_path / "wrong.key"
    wrong = SecretStore(str(wrong_key))
    wrong.initialize()
    target_db = tmp_path / "wrong-target.sqlite3"
    with pytest.raises(ValueError):
        migrate_v1(source_db, wrong_key, target_db, tmp_path / "v2.key")
    assert not target_db.exists()
    with pytest.raises(ValueError):
        migrate_v1(source_db, tmp_path / "legacy.key", source_db, tmp_path / "v2.key")
    aliased_target = tmp_path / "v2.sqlite3"
    with pytest.raises(ValueError):
        migrate_v1(source_db, tmp_path / "legacy.key", tmp_path / "v2.sqlite3",
                   aliased_target)
    assert not aliased_target.exists()


def test_same_service_profile_with_conflicting_credentials_requires_reconfiguration(tmp_path):
    source_db = tmp_path / "v1.sqlite3"
    create_legacy_database(source_db)
    source_store, _ = _stores(tmp_path)
    _encrypt_fixture_credentials(source_db, source_store)
    _make_second_openai_connection(source_db, source_store)

    report = migrate_v1(source_db, tmp_path / "legacy.key", tmp_path / "v2.sqlite3",
                        tmp_path / "v2.key")

    assert report.needs_reconfiguration is True
    assert report.imported["draft"] == 0
    assert report.imported["credentials"] == 1  # Telegram token only.
    target_store = SecretStore(str(tmp_path / "v2.key"))
    target_store.initialize()
    repo = V2Repository(tmp_path / "v2.sqlite3", target_store)
    repo.initialize()
    try:
        with repo.read() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM credentials WHERE service='openai'"
            ).fetchone()[0] == 0
    finally:
        repo.close()


def test_incompatible_profile_with_conflicting_same_service_credentials_does_not_duplicate(tmp_path):
    source_db = tmp_path / "v1.sqlite3"
    create_legacy_database(source_db)
    source_store, _ = _stores(tmp_path)
    _encrypt_fixture_credentials(source_db, source_store)
    _make_second_openai_connection(source_db, source_store, incompatible_profile=True)

    report = migrate_v1(source_db, tmp_path / "legacy.key", tmp_path / "v2.sqlite3",
                        tmp_path / "v2.key")

    assert report.needs_reconfiguration is True
    assert report.imported["credentials"] == 1
    target_store = SecretStore(str(tmp_path / "v2.key"))
    target_store.initialize()
    repo = V2Repository(tmp_path / "v2.sqlite3", target_store)
    repo.initialize()
    try:
        with repo.read() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM credentials WHERE service='openai'"
            ).fetchone()[0] == 0
        assert repo.read_credential("telegram", 1) == "synthetic-telegram-token"
    finally:
        repo.close()

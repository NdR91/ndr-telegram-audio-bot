import sqlite3

import pytest

from bot.database.secret_store import SecretStore
from bot.transcription.errors import ConfigurationInvalid
from bot.database.v2_repository import V2Repository


def test_reopen_preserves_configuration_and_encrypted_credentials(tmp_path, secret_store):
    path = tmp_path / "app-v2.sqlite3"
    first = V2Repository(path, secret_store)
    first.initialize()
    first.set_setup_state("owner_id", "12345")
    first.set_setting("locale", "it")
    version1 = first.put_credential("openai", "fake-key-one")
    version2 = first.put_credential("openai", "fake-key-two")

    stored = first.connection.execute(
        "SELECT encrypted_value FROM credentials ORDER BY version"
    ).fetchall()
    assert all("fake-key" not in row[0] for row in stored)
    first.close()

    second = V2Repository(path, secret_store)
    second.initialize()
    assert second.get_setup_state("owner_id") == "12345"
    assert second.get_setting("locale") == "it"
    assert second.read_credential("openai", version1) == "fake-key-one"
    assert second.read_credential("openai", version2) == "fake-key-two"
    second.close()


def test_unavailable_cipher_rejects_write(tmp_path):
    repository = V2Repository(tmp_path / "app-v2.sqlite3", SecretStore(str(tmp_path / "missing.key")))
    repository.initialize()
    with pytest.raises(ConfigurationInvalid):
        repository.put_credential("gemini", "fake-gemini-key")
    assert repository.connection.execute("SELECT COUNT(*) FROM credentials").fetchone()[0] == 0
    repository.close()


def test_v1_database_is_rejected_without_changes(tmp_path, secret_store):
    path = tmp_path / "v1.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
    connection.execute("INSERT INTO schema_version VALUES (2)")
    connection.execute("CREATE TABLE provider_connections (id INTEGER PRIMARY KEY)")
    connection.commit()
    before = connection.iterdump()
    before_dump = list(before)
    connection.close()

    repository = V2Repository(path, secret_store)
    with pytest.raises(ConfigurationInvalid):
        repository.initialize()

    connection = sqlite3.connect(path)
    assert list(connection.iterdump()) == before_dump
    connection.close()


def test_transaction_rolls_back(repo):
    with pytest.raises(RuntimeError):
        with repo.transaction() as connection:
            connection.execute(
                "INSERT INTO app_settings (setting_key, setting_value) VALUES (?, ?)",
                ("partial", "must roll back"),
            )
            raise RuntimeError("synthetic failure")
    assert repo.get_setting("partial") is None


def test_initialize_and_reopen_without_credentials(tmp_path, secret_store):
    path = tmp_path / "empty-v2.sqlite3"
    first = V2Repository(path, secret_store)
    first.initialize()
    first.close()

    reopened = V2Repository(path, secret_store)
    reopened.initialize()
    assert reopened.credential_versions("openai") == ()
    reopened.close()


def test_pruning_keeps_latest_credentials_for_each_service(repo):
    old_openai = repo.put_credential("openai", "old-openai-key")
    latest_openai = repo.put_credential("openai", "latest-openai-key")
    gemini = repo.put_credential("gemini", "saved-gemini-key")
    telegram = repo.put_credential("telegram", "saved-telegram-token")
    web_session = repo.put_credential("web_session", "saved-session-secret")

    assert repo.prune_credentials() == 1
    assert repo.credential_versions("openai") == (latest_openai,)
    assert repo.credential_versions("gemini") == (gemini,)
    assert repo.credential_versions("telegram") == (telegram,)
    assert repo.credential_versions("web_session") == (web_session,)
    with pytest.raises(ConfigurationInvalid):
        repo.read_credential("openai", old_openai)


def test_pruning_honors_setup_state_credential_versions(repo):
    telegram_v1 = repo.put_credential("telegram", "old-telegram-token")
    repo.put_credential("telegram", "new-telegram-token")
    web_session_v1 = repo.put_credential("web_session", "old-session-secret")
    repo.put_credential("web_session", "new-session-secret")
    repo.set_setup_state("telegram_credential_version", str(telegram_v1))
    repo.set_setup_state("web_session_credential_version", str(web_session_v1))

    assert repo.prune_credentials() == 0
    assert repo.read_credential("telegram", telegram_v1) == "old-telegram-token"
    assert repo.read_credential("web_session", web_session_v1) == "old-session-secret"

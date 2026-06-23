"""
Tests for the SecretStore local encryption layer.
"""

import os
import pytest

from bot.database.secret_store import SecretStore, SecretStoreError


# ------------------------------------------------------------------
# Initialization
# ------------------------------------------------------------------

def test_initialize_generates_key_file(tmp_path):
    key_path = tmp_path / ".master_key"
    store = SecretStore(str(key_path))
    is_new = store.initialize()

    assert is_new is True
    assert key_path.exists()
    assert store.key_available is True


def test_initialize_loads_existing_key(tmp_path):
    key_path = tmp_path / ".master_key"
    store1 = SecretStore(str(key_path))
    store1.initialize()
    store1.encrypt("test")  # verify store1 works

    store2 = SecretStore(str(key_path))
    is_new = store2.initialize()

    assert is_new is False
    assert store2.key_available is True


def test_initialize_sets_restrictive_permissions(tmp_path):
    key_path = tmp_path / ".master_key"
    store = SecretStore(str(key_path))
    store.initialize()

    st = os.stat(key_path)
    # Only owner should have access (no group/other bits)
    assert st.st_mode & 0o777 == 0o600


def test_initialize_is_idempotent(tmp_path):
    key_path = tmp_path / ".master_key"
    store = SecretStore(str(key_path))
    store.initialize()
    store.initialize()  # second call should not raise

    assert store.key_available is True


# ------------------------------------------------------------------
# Encrypt / Decrypt round-trip
# ------------------------------------------------------------------

def test_encrypt_decrypt_round_trip(tmp_path):
    key_path = tmp_path / ".master_key"
    store = SecretStore(str(key_path))
    store.initialize()

    plaintext = "sk-test-api-key-12345"
    token = store.encrypt(plaintext)
    assert token != plaintext
    assert isinstance(token, str)

    decrypted = store.decrypt(token)
    assert decrypted == plaintext


def test_encrypt_empty_string(tmp_path):
    key_path = tmp_path / ".master_key"
    store = SecretStore(str(key_path))
    store.initialize()

    token = store.encrypt("")
    assert store.decrypt(token) == ""


def test_decrypt_invalid_token(tmp_path):
    key_path = tmp_path / ".master_key"
    store = SecretStore(str(key_path))
    store.initialize()

    with pytest.raises(SecretStoreError, match="Decryption failed"):
        store.decrypt("not-a-valid-token")


def test_different_keys_produce_different_ciphertexts(tmp_path):
    store1 = SecretStore(str(tmp_path / "key1"))
    store1.initialize()
    store2 = SecretStore(str(tmp_path / "key2"))
    store2.initialize()

    t1 = store1.encrypt("secret")
    t2 = store2.encrypt("secret")
    assert t1 != t2


# ------------------------------------------------------------------
# Cross-instance compatibility (key persistence)
# ------------------------------------------------------------------

def test_decrypt_works_across_instances(tmp_path):
    """A token encrypted by one instance can be decrypted by another
    instance loading the same key file."""
    key_path = tmp_path / ".master_key"

    writer = SecretStore(str(key_path))
    writer.initialize()
    token = writer.encrypt("persistent-secret")

    reader = SecretStore(str(key_path))
    reader.initialize()
    assert reader.decrypt(token) == "persistent-secret"


# ------------------------------------------------------------------
# Key availability
# ------------------------------------------------------------------

def test_key_not_available_before_initialize(tmp_path):
    store = SecretStore(str(tmp_path / ".master_key"))
    assert store.key_available is False


def test_encrypt_raises_before_initialize(tmp_path):
    store = SecretStore(str(tmp_path / ".master_key"))
    with pytest.raises(SecretStoreError, match="not been initialized"):
        store.encrypt("test")


def test_decrypt_raises_before_initialize(tmp_path):
    store = SecretStore(str(tmp_path / ".master_key"))
    with pytest.raises(SecretStoreError, match="not been initialized"):
        store.decrypt("test")


# ------------------------------------------------------------------
# Key path property
# ------------------------------------------------------------------

def test_key_path_property(tmp_path):
    key_path = tmp_path / ".master_key"
    store = SecretStore(str(key_path))
    assert store.key_path == str(key_path)


# ------------------------------------------------------------------
# Integration with DatabaseManager — encryption of credentials
# ------------------------------------------------------------------

def test_add_provider_encrypts_credentials_with_secret_store(tmp_path):
    from bot.database import DatabaseManager, SecretStore

    db_path = tmp_path / "app.sqlite3"
    key_path = tmp_path / ".master_key"

    store = SecretStore(str(key_path))
    store.initialize()

    db = DatabaseManager(str(db_path), secret_store=store)
    db.initialize()

    pid = db.add_provider(
        "Test Provider", "openai-native",
        credentials="sk-test-plaintext",
    )
    provider = db.get_provider(pid)

    # The stored value should be encrypted, not plaintext
    assert provider["encrypted_credentials"] != "sk-test-plaintext"
    # But the decrypted credentials should match
    assert provider["credentials"] == "sk-test-plaintext"


def test_add_provider_credentials_field_decrypted(tmp_path):
    from bot.database import DatabaseManager, SecretStore

    db_path = tmp_path / "app.sqlite3"
    key_path = tmp_path / ".master_key"

    store = SecretStore(str(key_path))
    store.initialize()

    db = DatabaseManager(str(db_path), secret_store=store)
    db.initialize()

    pid = db.add_provider(
        "Test", "gemini-native",
        credentials="gemini-key-abc",
    )
    provider = db.get_provider(pid)
    assert provider["credentials"] == "gemini-key-abc"


def test_list_providers_decrypts_credentials(tmp_path):
    from bot.database import DatabaseManager, SecretStore

    db_path = tmp_path / "app.sqlite3"
    key_path = tmp_path / ".master_key"

    store = SecretStore(str(key_path))
    store.initialize()

    db = DatabaseManager(str(db_path), secret_store=store)
    db.initialize()

    db.add_provider("P1", "openai-native", credentials="key1")
    db.add_provider("P2", "gemini-native", credentials="key2")

    providers = db.list_providers()
    assert providers[0]["credentials"] == "key1"
    assert providers[1]["credentials"] == "key2"


def test_update_provider_encrypts_new_credentials(tmp_path):
    from bot.database import DatabaseManager, SecretStore

    db_path = tmp_path / "app.sqlite3"
    key_path = tmp_path / ".master_key"

    store = SecretStore(str(key_path))
    store.initialize()

    db = DatabaseManager(str(db_path), secret_store=store)
    db.initialize()

    pid = db.add_provider("P1", "openai-native", credentials="old-key")
    db.update_provider(pid, credentials="new-key")

    provider = db.get_provider(pid)
    assert provider["credentials"] == "new-key"
    assert provider["encrypted_credentials"] != "new-key"


def test_add_provider_without_secret_store_stores_plaintext(tmp_path):
    from bot.database import DatabaseManager

    db_path = tmp_path / "app.sqlite3"
    db = DatabaseManager(str(db_path))
    db.initialize()

    pid = db.add_provider("P1", "openai-native", encrypted_credentials="plain-secret")
    provider = db.get_provider(pid)

    # Without SecretStore, credentials field is absent, encrypted_credentials is as-is
    assert provider["encrypted_credentials"] == "plain-secret"
    assert "credentials" not in provider


def test_add_provider_without_secret_store_drops_credentials(tmp_path):
    """When no SecretStore is configured, credentials= is NOT stored because
    we refuse to persist plaintext without encryption."""
    from bot.database import DatabaseManager

    db_path = tmp_path / "app.sqlite3"
    db = DatabaseManager(str(db_path))
    db.initialize()

    pid = db.add_provider("P1", "openai-native", credentials="plain")
    provider = db.get_provider(pid)

    # Without encryption, the credential is dropped with a warning.
    assert provider["encrypted_credentials"] is None

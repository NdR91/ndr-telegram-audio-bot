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

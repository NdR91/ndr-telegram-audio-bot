"""
Tests for the DatabaseManager repository layer.
"""

from bot.database.repository import DatabaseManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

def _make_db(tmp_path) -> DatabaseManager:
    db = DatabaseManager(str(tmp_path / "app.sqlite3"))
    db.initialize()
    return db


# ------------------------------------------------------------------
# Whitelist compatibility
# ------------------------------------------------------------------

def test_load_authorized_data_returns_empty_on_fresh_db(tmp_path):
    db = _make_db(tmp_path)
    data = db.load_authorized_data()
    assert data == {"admin": [], "users": [], "groups": []}


def test_replace_authorized_data_stores_entries(tmp_path):
    db = _make_db(tmp_path)
    db.replace_authorized_data({"admin": [1, 2], "users": [3], "groups": [10]})

    data = db.load_authorized_data()
    assert data["admin"] == [1, 2]
    assert data["users"] == [3]
    assert data["groups"] == [10]


def test_replace_authorized_data_is_idempotent(tmp_path):
    db = _make_db(tmp_path)
    db.replace_authorized_data({"admin": [1], "users": [], "groups": []})
    db.replace_authorized_data({"admin": [1], "users": [], "groups": []})
    assert db.load_authorized_data()["admin"] == [1]


# ------------------------------------------------------------------
# Setup state
# ------------------------------------------------------------------

def test_setup_state_get_missing_key_returns_none(tmp_path):
    db = _make_db(tmp_path)
    assert db.get_setup_state("nonexistent") is None


def test_setup_state_set_and_get(tmp_path):
    db = _make_db(tmp_path)
    db.set_setup_state("admin_created", "true")
    assert db.get_setup_state("admin_created") == "true"


def test_setup_state_upsert_updates_value(tmp_path):
    db = _make_db(tmp_path)
    db.set_setup_state("code", "abc")
    db.set_setup_state("code", "def")
    assert db.get_setup_state("code") == "def"


def test_get_all_setup_state(tmp_path):
    db = _make_db(tmp_path)
    db.set_setup_state("a", "1")
    db.set_setup_state("b", "2")
    assert db.get_all_setup_state() == {"a": "1", "b": "2"}


# ------------------------------------------------------------------
# App settings
# ------------------------------------------------------------------

def test_setting_get_missing_key_returns_none(tmp_path):
    db = _make_db(tmp_path)
    assert db.get_setting("missing") is None


def test_setting_set_and_get(tmp_path):
    db = _make_db(tmp_path)
    db.set_setting("telegram_token", "123:abc")
    assert db.get_setting("telegram_token") == "123:abc"


def test_setting_delete_removes_key(tmp_path):
    db = _make_db(tmp_path)
    db.set_setting("temp", "value")
    db.delete_setting("temp")
    assert db.get_setting("temp") is None


def test_get_all_settings(tmp_path):
    db = _make_db(tmp_path)
    db.set_setting("key1", "val1")
    db.set_setting("key2", "val2")
    all_settings = db.get_all_settings()
    assert all_settings["key1"] == "val1"
    assert all_settings["key2"] == "val2"


def test_set_settings_bulk(tmp_path):
    db = _make_db(tmp_path)
    db.set_settings({"key1": "val1", "key2": "val2"})
    assert db.get_setting("key1") == "val1"
    assert db.get_setting("key2") == "val2"


def test_set_settings_updates_existing(tmp_path):
    db = _make_db(tmp_path)
    db.set_setting("key", "old")
    db.set_settings({"key": "new", "other": "val"})
    assert db.get_setting("key") == "new"
    assert db.get_setting("other") == "val"


# ------------------------------------------------------------------
# Provider connections
# ------------------------------------------------------------------

def test_add_provider_returns_id(tmp_path):
    db = _make_db(tmp_path)
    pid = db.add_provider("OpenAI", "openai-native", endpoint="https://api.openai.com")
    assert isinstance(pid, int)
    assert pid >= 1


def test_get_provider_returns_none_for_missing(tmp_path):
    db = _make_db(tmp_path)
    assert db.get_provider(999) is None


def test_get_provider_returns_inserted_data(tmp_path):
    db = _make_db(tmp_path)
    pid = db.add_provider(
        "My OpenAI", "openai-native",
        endpoint="https://api.openai.com",
        capabilities={"transcription": True, "refinement": True},
    )
    provider = db.get_provider(pid)
    assert provider is not None
    assert provider["name"] == "My OpenAI"
    assert provider["adapter_type"] == "openai-native"
    assert provider["endpoint"] == "https://api.openai.com"
    assert provider["capabilities"] == {"transcription": True, "refinement": True}
    assert provider["enabled"] == 1


def test_list_providers_returns_all(tmp_path):
    db = _make_db(tmp_path)
    db.add_provider("P1", "openai-native")
    db.add_provider("P2", "gemini-native")
    providers = db.list_providers()
    assert len(providers) == 2
    assert providers[0]["name"] == "P1"
    assert providers[1]["name"] == "P2"


def test_update_provider_updates_fields(tmp_path):
    db = _make_db(tmp_path)
    pid = db.add_provider("Old Name", "openai-native", enabled=True)
    updated = db.update_provider(pid, name="New Name", enabled=False)
    assert updated is True

    provider = db.get_provider(pid)
    assert provider["name"] == "New Name"
    assert provider["enabled"] == 0


def test_update_provider_returns_false_for_missing(tmp_path):
    db = _make_db(tmp_path)
    assert db.update_provider(999, name="Ghost") is False


def test_delete_provider_removes_row(tmp_path):
    db = _make_db(tmp_path)
    pid = db.add_provider("Temp", "openai-native")
    assert db.delete_provider(pid) is True
    assert db.get_provider(pid) is None


def test_delete_provider_returns_false_for_missing(tmp_path):
    db = _make_db(tmp_path)
    assert db.delete_provider(999) is False


# ------------------------------------------------------------------
# Pipeline profiles
# ------------------------------------------------------------------

def test_add_and_get_pipeline_profile(tmp_path):
    db = _make_db(tmp_path)
    pid = db.add_provider("P1", "openai-native")
    profile_id = db.add_pipeline_profile(
        "Default",
        transcription_provider_id=pid,
        system_prompt="You are an assistant.",
    )
    profile = db.get_pipeline_profile(profile_id)
    assert profile is not None
    assert profile["name"] == "Default"
    assert profile["transcription_provider_id"] == pid
    assert profile["system_prompt"] == "You are an assistant."


def test_list_pipeline_profiles(tmp_path):
    db = _make_db(tmp_path)
    db.add_pipeline_profile("Profile 1")
    db.add_pipeline_profile("Profile 2")
    profiles = db.list_pipeline_profiles()
    assert len(profiles) == 2


# ------------------------------------------------------------------
# User preferences
# ------------------------------------------------------------------

def test_user_preference_set_and_get(tmp_path):
    db = _make_db(tmp_path)
    db.set_user_preference(42, "output_mode", "summary")
    assert db.get_user_preference(42, "output_mode") == "summary"


def test_user_preference_defaults_to_none(tmp_path):
    db = _make_db(tmp_path)
    assert db.get_user_preference(42, "nonexistent") is None


def test_user_preference_delete(tmp_path):
    db = _make_db(tmp_path)
    db.set_user_preference(42, "lang", "it")
    db.delete_user_preference(42, "lang")
    assert db.get_user_preference(42, "lang") is None


def test_get_all_user_preferences(tmp_path):
    db = _make_db(tmp_path)
    db.set_user_preference(1, "a", "1")
    db.set_user_preference(1, "b", "2")
    prefs = db.get_all_user_preferences(1)
    assert prefs == {"a": "1", "b": "2"}


def test_user_preferences_are_scoped_by_user(tmp_path):
    db = _make_db(tmp_path)
    db.set_user_preference(1, "lang", "en")
    db.set_user_preference(2, "lang", "it")
    assert db.get_user_preference(1, "lang") == "en"
    assert db.get_user_preference(2, "lang") == "it"


# ------------------------------------------------------------------
# Group preferences
# ------------------------------------------------------------------

def test_group_preference_set_and_get(tmp_path):
    db = _make_db(tmp_path)
    db.set_group_preference(-100, "lang", "de")
    assert db.get_group_preference(-100, "lang") == "de"


def test_group_preference_delete(tmp_path):
    db = _make_db(tmp_path)
    db.set_group_preference(-100, "lang", "fr")
    db.delete_group_preference(-100, "lang")
    assert db.get_group_preference(-100, "lang") is None


# ------------------------------------------------------------------
# Audit events
# ------------------------------------------------------------------

def test_add_audit_event_returns_id(tmp_path):
    db = _make_db(tmp_path)
    eid = db.add_audit_event("admin.created", actor_id=1)
    assert isinstance(eid, int)
    assert eid >= 1


def test_get_audit_events_returns_most_recent_first(tmp_path):
    db = _make_db(tmp_path)
    db.add_audit_event("event.1", actor_id=1)
    db.add_audit_event("event.2", actor_id=2)

    events = db.get_audit_events(limit=10)
    assert len(events) >= 2
    assert events[0]["event_type"] == "event.2"
    assert events[1]["event_type"] == "event.1"


def test_audit_event_with_metadata(tmp_path):
    db = _make_db(tmp_path)
    db.add_audit_event("provider.added", actor_id=1, metadata={"provider_name": "OpenAI"})
    events = db.get_audit_events(limit=1)
    assert events[0]["metadata"] == {"provider_name": "OpenAI"}


# ------------------------------------------------------------------
# Legacy import
# ------------------------------------------------------------------

def test_import_whitelist_from_dict_populates_tables(tmp_path):
    db = _make_db(tmp_path)
    db.import_whitelist_from_dict({"admin": [1], "users": [2, 3], "groups": [-10]})

    data = db.load_authorized_data()
    assert data["admin"] == [1]
    assert data["users"] == [2, 3]
    assert data["groups"] == [-10]


def test_import_whitelist_is_idempotent(tmp_path):
    db = _make_db(tmp_path)
    db.import_whitelist_from_dict({"admin": [1], "users": [], "groups": []})
    db.import_whitelist_from_dict({"admin": [999], "users": [], "groups": []})
    # Second call should be skipped because table is not empty
    assert db.load_authorized_data()["admin"] == [1]


def test_import_whitelist_does_not_override_existing_data(tmp_path):
    db = _make_db(tmp_path)
    db.replace_authorized_data({"admin": [42], "users": [], "groups": []})
    db.import_whitelist_from_dict({"admin": [1], "users": [2], "groups": [3]})
    # Should not overwrite existing data
    assert db.load_authorized_data()["admin"] == [42]


# ------------------------------------------------------------------
# Lifecycle
# ------------------------------------------------------------------

def test_database_manager_initialize_creates_file(tmp_path):
    db_path = tmp_path / "app.sqlite3"
    db = DatabaseManager(str(db_path))
    db.initialize()
    assert db_path.exists()
    db.close()


def test_database_manager_initialize_is_idempotent(tmp_path):
    db = _make_db(tmp_path)
    # Second initialize should not raise
    db.initialize()
    db.close()


def test_connection_property_raises_before_initialize(tmp_path):
    db = DatabaseManager(str(tmp_path / "app.sqlite3"))
    import pytest
    with pytest.raises(RuntimeError, match="not been initialized"):
        _ = db.connection

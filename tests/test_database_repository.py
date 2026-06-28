"""
Tests for the DatabaseManager repository layer.
"""

import pytest

from bot.database.repository import DatabaseManager
from bot.exceptions import ResourceInUseError


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
    with pytest.raises(RuntimeError, match="not been initialized"):
        _ = db.connection


# ------------------------------------------------------------------
# Provider models (per-connection model registry)
# ------------------------------------------------------------------


class TestProviderModels:
    """Tests for provider_models CRUD operations."""

    def _add_provider(self, db, name="Test Provider", adapter="openai-native") -> int:
        return db.add_provider(name=name, adapter_type=adapter)

    # ---------- Create ----------

    def test_add_provider_model_returns_id(self, tmp_path):
        db = _make_db(tmp_path)
        pid = self._add_provider(db)
        model_id = db.add_provider_model(pid, "gpt-4", display_name="GPT-4")
        assert isinstance(model_id, int)
        assert model_id >= 1

    def test_add_provider_model_with_capabilities(self, tmp_path):
        db = _make_db(tmp_path)
        pid = self._add_provider(db)
        caps = {"transcription": True, "refinement": True}
        model_id = db.add_provider_model(
            pid, "gpt-4", display_name="GPT-4", capabilities=caps,
        )
        retrieved = db.get_provider_model(model_id)
        assert retrieved is not None
        assert retrieved["capabilities"] == caps
        assert retrieved["display_name"] == "GPT-4"

    def test_add_provider_model_defaults_display_name_to_model_id(self, tmp_path):
        db = _make_db(tmp_path)
        pid = self._add_provider(db)
        model_id = db.add_provider_model(pid, "whisper-1")
        retrieved = db.get_provider_model(model_id)
        assert retrieved is not None
        assert retrieved["display_name"] == "whisper-1"

    def test_add_provider_model_detected_false(self, tmp_path):
        db = _make_db(tmp_path)
        pid = self._add_provider(db)
        model_id = db.add_provider_model(pid, "custom-model", detected=False)
        retrieved = db.get_provider_model(model_id)
        assert retrieved is not None
        assert retrieved["detected"] == 0

    def test_add_provider_model_disabled(self, tmp_path):
        db = _make_db(tmp_path)
        pid = self._add_provider(db)
        model_id = db.add_provider_model(pid, "old-model", enabled=False)
        retrieved = db.get_provider_model(model_id)
        assert retrieved is not None
        assert retrieved["enabled"] == 0

    # ---------- Read ----------

    def test_get_provider_model_returns_none_for_missing(self, tmp_path):
        db = _make_db(tmp_path)
        assert db.get_provider_model(999) is None

    def test_get_provider_model_returns_parsed_capabilities(self, tmp_path):
        db = _make_db(tmp_path)
        pid = self._add_provider(db)
        caps = {"transcription": True}
        model_id = db.add_provider_model(pid, "gpt-4", capabilities=caps)
        retrieved = db.get_provider_model(model_id)
        assert retrieved is not None
        assert retrieved["model_id"] == "gpt-4"
        assert retrieved["provider_id"] == pid
        # capabilities should be a dict, not a JSON string
        assert isinstance(retrieved["capabilities"], dict)

    def test_get_provider_model_no_capabilities_is_none(self, tmp_path):
        db = _make_db(tmp_path)
        pid = self._add_provider(db)
        model_id = db.add_provider_model(pid, "whisper-1")
        retrieved = db.get_provider_model(model_id)
        assert retrieved is not None
        assert retrieved["capabilities"] is None

    # ---------- List ----------

    def test_list_provider_models_returns_models_for_provider(self, tmp_path):
        db = _make_db(tmp_path)
        pid1 = self._add_provider(db, name="Provider A")
        pid2 = self._add_provider(db, name="Provider B")
        db.add_provider_model(pid1, "gpt-4")
        db.add_provider_model(pid1, "gpt-4-turbo")
        db.add_provider_model(pid2, "claude-3")

        models_p1 = db.list_provider_models(pid1)
        assert len(models_p1) == 2
        assert all(m["provider_id"] == pid1 for m in models_p1)

        models_all = db.list_provider_models()
        assert len(models_all) == 3

    def test_list_provider_models_ordered_by_model_id(self, tmp_path):
        db = _make_db(tmp_path)
        pid = self._add_provider(db)
        db.add_provider_model(pid, "z-model")
        db.add_provider_model(pid, "a-model")
        db.add_provider_model(pid, "m-model")

        models = db.list_provider_models(pid)
        model_ids = [m["model_id"] for m in models]
        assert model_ids == sorted(model_ids)

    def test_list_provider_models_filters_enabled(self, tmp_path):
        db = _make_db(tmp_path)
        pid = self._add_provider(db)
        db.add_provider_model(pid, "enabled-model", enabled=True)
        db.add_provider_model(pid, "disabled-model", enabled=False)

        all_models = db.list_provider_models(pid)
        assert len(all_models) == 2

        enabled_only = db.list_provider_models(pid, only_enabled=True)
        assert len(enabled_only) == 1
        assert enabled_only[0]["model_id"] == "enabled-model"

    def test_list_provider_models_no_models_returns_empty(self, tmp_path):
        db = _make_db(tmp_path)
        pid = self._add_provider(db)
        assert db.list_provider_models(pid) == []

    # ---------- Update ----------

    def test_update_provider_model_updates_display_name(self, tmp_path):
        db = _make_db(tmp_path)
        pid = self._add_provider(db)
        model_id = db.add_provider_model(pid, "gpt-4", display_name="Old Name")
        updated = db.update_provider_model(model_id, display_name="New Name")
        assert updated is True

        retrieved = db.get_provider_model(model_id)
        assert retrieved["display_name"] == "New Name"

    def test_update_provider_model_updates_capabilities(self, tmp_path):
        db = _make_db(tmp_path)
        pid = self._add_provider(db)
        model_id = db.add_provider_model(pid, "gpt-4")
        caps = {"transcription": True, "refinement": True}
        updated = db.update_provider_model(model_id, capabilities=caps)
        assert updated is True

        retrieved = db.get_provider_model(model_id)
        assert retrieved["capabilities"] == caps

    def test_update_provider_model_detected_flag(self, tmp_path):
        db = _make_db(tmp_path)
        pid = self._add_provider(db)
        model_id = db.add_provider_model(pid, "gpt-4", detected=True)
        db.update_provider_model(model_id, detected=False)
        retrieved = db.get_provider_model(model_id)
        assert retrieved["detected"] == 0

    def test_update_provider_model_manually_overridden(self, tmp_path):
        db = _make_db(tmp_path)
        pid = self._add_provider(db)
        model_id = db.add_provider_model(pid, "gpt-4")
        db.update_provider_model(model_id, manually_overridden=True)
        retrieved = db.get_provider_model(model_id)
        assert retrieved["manually_overridden"] == 1

    def test_update_provider_model_toggle_enabled(self, tmp_path):
        db = _make_db(tmp_path)
        pid = self._add_provider(db)
        model_id = db.add_provider_model(pid, "gpt-4", enabled=True)
        db.update_provider_model(model_id, enabled=False)
        retrieved = db.get_provider_model(model_id)
        assert retrieved["enabled"] == 0

        db.update_provider_model(model_id, enabled=True)
        retrieved = db.get_provider_model(model_id)
        assert retrieved["enabled"] == 1

    def test_update_provider_model_returns_false_for_missing(self, tmp_path):
        db = _make_db(tmp_path)
        assert db.update_provider_model(999, display_name="Ghost") is False

    def test_update_provider_model_no_changes_returns_false(self, tmp_path):
        db = _make_db(tmp_path)
        pid = self._add_provider(db)
        model_id = db.add_provider_model(pid, "gpt-4")
        assert db.update_provider_model(model_id) is False

    # ---------- Delete ----------

    def test_delete_provider_model_removes_entry(self, tmp_path):
        db = _make_db(tmp_path)
        pid = self._add_provider(db)
        model_id = db.add_provider_model(pid, "gpt-4")
        assert db.delete_provider_model(model_id) is True
        assert db.get_provider_model(model_id) is None

    def test_delete_provider_model_returns_false_for_missing(self, tmp_path):
        db = _make_db(tmp_path)
        assert db.delete_provider_model(999) is False

    # ---------- set_model_capabilities ----------

    def test_set_model_capabilities_updates_and_marks_overridden(self, tmp_path):
        db = _make_db(tmp_path)
        pid = self._add_provider(db)
        model_id = db.add_provider_model(pid, "gpt-4")
        caps = {"transcription": False, "refinement": True}
        result = db.set_model_capabilities(model_id, caps, mark_overridden=True)
        assert result is True

        retrieved = db.get_provider_model(model_id)
        assert retrieved["capabilities"] == caps
        assert retrieved["manually_overridden"] == 1

    def test_set_model_capabilities_without_mark_overridden(self, tmp_path):
        db = _make_db(tmp_path)
        pid = self._add_provider(db)
        model_id = db.add_provider_model(pid, "gpt-4")
        caps = {"transcription": True}
        result = db.set_model_capabilities(model_id, caps, mark_overridden=False)
        assert result is True

        retrieved = db.get_provider_model(model_id)
        assert retrieved["capabilities"] == caps
        assert retrieved["manually_overridden"] == 0

    def test_set_model_capabilities_returns_false_for_missing(self, tmp_path):
        db = _make_db(tmp_path)
        assert db.set_model_capabilities(999, {}) is False

    # ---------- Foreign key cascade ----------

    def test_delete_provider_cascades_to_models(self, tmp_path):
        db = _make_db(tmp_path)
        pid = self._add_provider(db)
        db.add_provider_model(pid, "gpt-4")
        db.add_provider_model(pid, "gpt-4-turbo")
        assert len(db.list_provider_models(pid)) == 2

        db.delete_provider(pid)
        assert db.list_provider_models(pid) == []


# ------------------------------------------------------------------
# Pipeline stages
# ------------------------------------------------------------------


class TestPipelineStages:
    """Tests for pipeline stage CRUD operations."""

    def _setup_profile(self, db) -> int:
        """Create a provider and profile, return profile_id."""
        pid = db.add_provider("Test Provider", "openai-native")
        return db.add_pipeline_profile(
            "Test Profile",
            transcription_provider_id=pid,
            text_provider_id=pid,
        )

    # ---------- Create ----------

    def test_add_pipeline_stage_returns_id(self, tmp_path):
        db = _make_db(tmp_path)
        profile_id = self._setup_profile(db)
        stage_id = db.add_pipeline_stage(profile_id, "transcription")
        assert isinstance(stage_id, int)
        assert stage_id >= 1

    def test_add_pipeline_stage_with_primary_model(self, tmp_path):
        db = _make_db(tmp_path)
        profile_id = self._setup_profile(db)
        pid = db.add_provider("Model Provider", "openai-native")
        model_id = db.add_provider_model(pid, "whisper-1")
        stage_id = db.add_pipeline_stage(
            profile_id, "transcription", primary_model_id=model_id,
        )
        stage = db.get_pipeline_stage(stage_id)
        assert stage is not None
        assert stage["primary_model_id"] == model_id
        assert stage["stage_type"] == "transcription"

    def test_add_pipeline_stage_returns_stage_with_fallbacks(self, tmp_path):
        db = _make_db(tmp_path)
        profile_id = self._setup_profile(db)
        stage_id = db.add_pipeline_stage(profile_id, "refinement")
        stage = db.get_pipeline_stage(stage_id)
        assert stage is not None
        assert "fallbacks" in stage
        assert stage["fallbacks"] == []

    # ---------- Read ----------

    def test_get_pipeline_stage_returns_none_for_missing(self, tmp_path):
        db = _make_db(tmp_path)
        assert db.get_pipeline_stage(999) is None

    # ---------- List ----------

    def test_list_pipeline_stages_returns_stages_for_profile(self, tmp_path):
        db = _make_db(tmp_path)
        profile_id = self._setup_profile(db)
        db.add_pipeline_stage(profile_id, "transcription")
        db.add_pipeline_stage(profile_id, "refinement")

        stages = db.list_pipeline_stages(profile_id)
        assert len(stages) == 2
        assert stages[0]["stage_type"] == "transcription"
        assert stages[1]["stage_type"] == "refinement"

    def test_list_pipeline_stages_ordered_by_id(self, tmp_path):
        db = _make_db(tmp_path)
        profile_id = self._setup_profile(db)
        s1 = db.add_pipeline_stage(profile_id, "transcription")
        s2 = db.add_pipeline_stage(profile_id, "refinement")
        s3 = db.add_pipeline_stage(profile_id, "single_pass")

        stages = db.list_pipeline_stages(profile_id)
        assert [s["id"] for s in stages] == [s1, s2, s3]

    def test_list_pipeline_stages_without_filter(self, tmp_path):
        db = _make_db(tmp_path)
        profile1 = self._setup_profile(db)
        # Create a second profile
        pid = db.add_provider("P2", "openai-native")
        profile2 = db.add_pipeline_profile("P2", transcription_provider_id=pid)

        db.add_pipeline_stage(profile1, "transcription")
        db.add_pipeline_stage(profile2, "refinement")

        all_stages = db.list_pipeline_stages()
        assert len(all_stages) == 2

    def test_list_pipeline_stages_includes_fallbacks(self, tmp_path):
        db = _make_db(tmp_path)
        profile_id = self._setup_profile(db)
        stage_id = db.add_pipeline_stage(profile_id, "transcription")
        pid = db.add_provider("Model Provider", "openai-native")
        m1 = db.add_provider_model(pid, "model-a")
        m2 = db.add_provider_model(pid, "model-b")
        db.add_stage_fallback(stage_id, m1, fallback_order=1)
        db.add_stage_fallback(stage_id, m2, fallback_order=2)

        stages = db.list_pipeline_stages(profile_id)
        assert len(stages[0]["fallbacks"]) == 2

    # ---------- Update ----------

    def test_update_pipeline_stage_updates_primary_model(self, tmp_path):
        db = _make_db(tmp_path)
        profile_id = self._setup_profile(db)
        pid = db.add_provider("Model Provider", "openai-native")
        old_model = db.add_provider_model(pid, "old-model")
        new_model = db.add_provider_model(pid, "new-model")

        stage_id = db.add_pipeline_stage(profile_id, "refinement", primary_model_id=old_model)
        updated = db.update_pipeline_stage(stage_id, primary_model_id=new_model)
        assert updated is True

        stage = db.get_pipeline_stage(stage_id)
        assert stage["primary_model_id"] == new_model

    def test_update_pipeline_stage_returns_false_for_missing(self, tmp_path):
        db = _make_db(tmp_path)
        assert db.update_pipeline_stage(999, primary_model_id=None) is False

    # ---------- Delete ----------

    def test_delete_pipeline_stage_removes_stage(self, tmp_path):
        db = _make_db(tmp_path)
        profile_id = self._setup_profile(db)
        stage_id = db.add_pipeline_stage(profile_id, "transcription")
        assert db.delete_pipeline_stage(stage_id) is True
        assert db.get_pipeline_stage(stage_id) is None

    def test_delete_pipeline_stage_returns_false_for_missing(self, tmp_path):
        db = _make_db(tmp_path)
        assert db.delete_pipeline_stage(999) is False

    def test_delete_pipeline_stage_cascades_to_fallbacks(self, tmp_path):
        db = _make_db(tmp_path)
        profile_id = self._setup_profile(db)
        pid = db.add_provider("Model Provider", "openai-native")
        model_id = db.add_provider_model(pid, "gpt-4")
        stage_id = db.add_pipeline_stage(profile_id, "transcription", primary_model_id=model_id)
        fb_id = db.add_stage_fallback(stage_id, model_id)

        # Delete stage — fallback should cascade
        db.delete_pipeline_stage(stage_id)
        assert db.list_stage_fallbacks(stage_id) == []


# ------------------------------------------------------------------
# Stage fallbacks (ordered fallback model chains)
# ------------------------------------------------------------------


class TestStageFallbacks:
    """Tests for stage fallback management."""

    def _setup_stage(self, db) -> tuple[int, int]:
        """Create provider, profile, stage, model. Returns (stage_id, model_id)."""
        pid = db.add_provider("Test Provider", "openai-native")
        model_id = db.add_provider_model(pid, "gpt-4")
        profile_id = db.add_pipeline_profile(
            "Test Profile",
            transcription_provider_id=pid,
            text_provider_id=pid,
        )
        stage_id = db.add_pipeline_stage(profile_id, "transcription", primary_model_id=model_id)
        return stage_id, model_id

    def _add_model(self, db, model_str: str) -> int:
        """Add a model to the first provider."""
        providers = db.list_providers()
        return db.add_provider_model(providers[0]["id"], model_str)

    # ---------- Create ----------

    def test_add_stage_fallback_returns_id(self, tmp_path):
        db = _make_db(tmp_path)
        stage_id, model_id = self._setup_stage(db)
        fb_id = db.add_stage_fallback(stage_id, model_id)
        assert isinstance(fb_id, int)
        assert fb_id >= 1

    def test_add_stage_fallback_with_explicit_order(self, tmp_path):
        db = _make_db(tmp_path)
        stage_id, model_id = self._setup_stage(db)
        fb_id = db.add_stage_fallback(stage_id, model_id, fallback_order=5)
        fallbacks = db.list_stage_fallbacks(stage_id)
        assert fallbacks[0]["fallback_order"] == 5

    def test_add_stage_fallback_auto_increments_order(self, tmp_path):
        db = _make_db(tmp_path)
        stage_id, model_id = self._setup_stage(db)
        m2 = self._add_model(db, "gpt-4-turbo")
        m3 = self._add_model(db, "gpt-4o")

        fb1 = db.add_stage_fallback(stage_id, model_id)  # order 1
        fb2 = db.add_stage_fallback(stage_id, m2)         # order 2
        fb3 = db.add_stage_fallback(stage_id, m3)         # order 3

        fallbacks = db.list_stage_fallbacks(stage_id)
        assert len(fallbacks) == 3
        assert [f["fallback_order"] for f in fallbacks] == [1, 2, 3]

    # ---------- Read ----------

    def test_list_stage_fallbacks_returns_ordered(self, tmp_path):
        db = _make_db(tmp_path)
        stage_id, model_id = self._setup_stage(db)
        m2 = self._add_model(db, "model-b")
        m3 = self._add_model(db, "model-c")

        db.add_stage_fallback(stage_id, m3, fallback_order=3)
        db.add_stage_fallback(stage_id, model_id, fallback_order=1)
        db.add_stage_fallback(stage_id, m2, fallback_order=2)

        fallbacks = db.list_stage_fallbacks(stage_id)
        assert [f["fallback_order"] for f in fallbacks] == [1, 2, 3]
        assert [f["model_id"] for f in fallbacks] == [model_id, m2, m3]

    def test_list_stage_fallbacks_empty(self, tmp_path):
        db = _make_db(tmp_path)
        stage_id, _ = self._setup_stage(db)
        assert db.list_stage_fallbacks(stage_id) == []

    # ---------- Delete ----------

    def test_remove_stage_fallback_removes_entry(self, tmp_path):
        db = _make_db(tmp_path)
        stage_id, model_id = self._setup_stage(db)
        fb_id = db.add_stage_fallback(stage_id, model_id)
        assert db.remove_stage_fallback(fb_id) is True
        assert db.list_stage_fallbacks(stage_id) == []

    def test_remove_stage_fallback_returns_false_for_missing(self, tmp_path):
        db = _make_db(tmp_path)
        assert db.remove_stage_fallback(999) is False

    def test_remove_stage_fallback_does_not_affect_others(self, tmp_path):
        db = _make_db(tmp_path)
        stage_id, model_id = self._setup_stage(db)
        m2 = self._add_model(db, "model-b")
        m3 = self._add_model(db, "model-c")

        fb1 = db.add_stage_fallback(stage_id, model_id, fallback_order=1)
        fb2 = db.add_stage_fallback(stage_id, m2, fallback_order=2)
        fb3 = db.add_stage_fallback(stage_id, m3, fallback_order=3)

        db.remove_stage_fallback(fb2)
        remaining = db.list_stage_fallbacks(stage_id)
        assert len(remaining) == 2
        assert [f["id"] for f in remaining] == [fb1, fb3]

    # ---------- Reorder ----------

    def test_reorder_stage_fallbacks_replaces_chain(self, tmp_path):
        db = _make_db(tmp_path)
        stage_id, model_id = self._setup_stage(db)
        m2 = self._add_model(db, "model-b")
        m3 = self._add_model(db, "model-c")

        db.add_stage_fallback(stage_id, model_id, fallback_order=1)
        db.add_stage_fallback(stage_id, m2, fallback_order=2)
        db.add_stage_fallback(stage_id, m3, fallback_order=3)

        # Reorder: m3 first, model_id second, m2 third
        db.reorder_stage_fallbacks(stage_id, [m3, model_id, m2])

        fallbacks = db.list_stage_fallbacks(stage_id)
        assert len(fallbacks) == 3
        assert [f["model_id"] for f in fallbacks] == [m3, model_id, m2]
        assert [f["fallback_order"] for f in fallbacks] == [1, 2, 3]

    def test_reorder_stage_fallbacks_empty_list_clears(self, tmp_path):
        db = _make_db(tmp_path)
        stage_id, model_id = self._setup_stage(db)
        db.add_stage_fallback(stage_id, model_id)

        db.reorder_stage_fallbacks(stage_id, [])
        assert db.list_stage_fallbacks(stage_id) == []

    def test_reorder_stage_fallbacks_atomic_on_failure(self, tmp_path):
        """If an FK violation occurs mid-reorder, the whole transaction rolls back."""
        db = _make_db(tmp_path)
        stage_id, model_id = self._setup_stage(db)
        db.add_stage_fallback(stage_id, model_id, fallback_order=1)

        # Try reorder with a non-existent model_id — should fail and roll back
        with pytest.raises(Exception):
            db.reorder_stage_fallbacks(stage_id, [model_id, 99999])

        # Original fallback should still be there
        fallbacks = db.list_stage_fallbacks(stage_id)
        assert len(fallbacks) == 1


# ------------------------------------------------------------------
# Pipeline profile mode
# ------------------------------------------------------------------


class TestPipelineProfileMode:
    """Tests for pipeline profile mode get/set."""

    def test_default_mode_is_two_stage(self, tmp_path):
        db = _make_db(tmp_path)
        pid = db.add_provider("Test", "openai-native")
        profile_id = db.add_pipeline_profile(
            "Default Mode",
            transcription_provider_id=pid,
            text_provider_id=pid,
        )
        mode = db.get_pipeline_profile_mode(profile_id)
        assert mode == "two_stage", f"Expected 'two_stage', got '{mode}'"

    def test_set_pipeline_profile_mode_to_single_pass(self, tmp_path):
        db = _make_db(tmp_path)
        pid = db.add_provider("Test", "openai-native")
        profile_id = db.add_pipeline_profile(
            "Changeable",
            transcription_provider_id=pid,
            text_provider_id=pid,
        )
        result = db.set_pipeline_profile_mode(profile_id, "single_pass")
        assert result is True
        assert db.get_pipeline_profile_mode(profile_id) == "single_pass"

    def test_set_pipeline_profile_mode_back_to_two_stage(self, tmp_path):
        db = _make_db(tmp_path)
        pid = db.add_provider("Test", "openai-native")
        profile_id = db.add_pipeline_profile(
            "Toggle Mode",
            transcription_provider_id=pid,
            text_provider_id=pid,
            mode="single_pass",
        )
        db.set_pipeline_profile_mode(profile_id, "two_stage")
        assert db.get_pipeline_profile_mode(profile_id) == "two_stage"

    def test_get_pipeline_profile_mode_returns_none_for_missing(self, tmp_path):
        db = _make_db(tmp_path)
        assert db.get_pipeline_profile_mode(999) is None

    def test_set_pipeline_profile_mode_returns_false_for_missing(self, tmp_path):
        db = _make_db(tmp_path)
        assert db.set_pipeline_profile_mode(999, "single_pass") is False

    def test_mode_is_persisted_in_get_pipeline_profile(self, tmp_path):
        db = _make_db(tmp_path)
        pid = db.add_provider("Test", "openai-native")
        profile_id = db.add_pipeline_profile(
            "Mode Check",
            transcription_provider_id=pid,
            text_provider_id=pid,
        )
        db.set_pipeline_profile_mode(profile_id, "single_pass")
        profile = db.get_pipeline_profile(profile_id)
        assert profile["mode"] == "single_pass"

    def test_explicit_mode_in_add_pipeline_profile(self, tmp_path):
        db = _make_db(tmp_path)
        pid = db.add_provider("Test", "openai-native")
        profile_id = db.add_pipeline_profile(
            "Explicit Single",
            transcription_provider_id=pid,
            text_provider_id=pid,
            mode="single_pass",
        )
        assert db.get_pipeline_profile_mode(profile_id) == "single_pass"


# ------------------------------------------------------------------
# Integration: provider_models + pipeline_stages + fallbacks
# ------------------------------------------------------------------


class TestProviderModelStageIntegration:
    """Cross-entity integration tests."""

    def test_full_pipeline_with_models_and_fallbacks(self, tmp_path):
        """Create a provider, models, stages, and fallbacks end-to-end."""
        db = _make_db(tmp_path)

        # 1. Create provider
        pid = db.add_provider("OpenAI", "openai-native",
                              capabilities={"transcription": True, "refinement": True})

        # 2. Add models
        whisper = db.add_provider_model(pid, "whisper-1", capabilities={"transcription": True})
        gpt4 = db.add_provider_model(pid, "gpt-4", capabilities={"refinement": True})
        gpt4o = db.add_provider_model(pid, "gpt-4o", capabilities={"refinement": True})

        # 3. Create profile
        profile_id = db.add_pipeline_profile(
            "Full Pipeline",
            transcription_provider_id=pid,
            text_provider_id=pid,
        )
        assert db.get_pipeline_profile_mode(profile_id) == "two_stage"

        # 4. Create stages
        tx_stage = db.add_pipeline_stage(profile_id, "transcription", primary_model_id=whisper)
        ref_stage = db.add_pipeline_stage(profile_id, "refinement", primary_model_id=gpt4)

        # 5. Add fallbacks to refinement stage
        db.add_stage_fallback(ref_stage, gpt4o, fallback_order=1)
        db.add_stage_fallback(ref_stage, gpt4, fallback_order=2)

        # 6. Verify
        stages = db.list_pipeline_stages(profile_id)
        assert len(stages) == 2

        tx = db.get_pipeline_stage(tx_stage)
        assert tx["stage_type"] == "transcription"
        assert tx["primary_model_id"] == whisper

        ref = db.get_pipeline_stage(ref_stage)
        assert ref["stage_type"] == "refinement"
        assert ref["primary_model_id"] == gpt4
        assert len(ref["fallbacks"]) == 2
        assert ref["fallbacks"][0]["model_id"] == gpt4o
        assert ref["fallbacks"][1]["model_id"] == gpt4

        # 7. Profile includes stages
        profile = db.get_pipeline_profile(profile_id)
        assert len(profile["stages"]) == 2

    def test_delete_provider_cascades_cleanly(self, tmp_path):
        """Deleting a provider removes its models, which sets FK to NULL on stages.

        ``pipeline_profiles`` FK columns on `provider_connections` lack
        ON DELETE CASCADE, so those references must be cleared before the
        provider can be deleted.
        """
        db = _make_db(tmp_path)

        pid = db.add_provider("Temp", "openai-native")
        model_id = db.add_provider_model(pid, "temp-model")
        profile_id = db.add_pipeline_profile(
            "Temp Profile",
            transcription_provider_id=pid,
            text_provider_id=pid,
        )
        stage_id = db.add_pipeline_stage(profile_id, "transcription", primary_model_id=model_id)

        # Detach the profile from the provider (no CASCADE on these FKs).
        # Do NOT delete the profile — pipeline_stages.profile_id *does* have
        # ON DELETE CASCADE, which would wipe the stage we want to inspect.
        db.connection.execute(
            "UPDATE pipeline_profiles SET transcription_provider_id = NULL,"
            " text_provider_id = NULL WHERE id = ?",
            (profile_id,),
        )
        db.connection.commit()

        # Delete the provider — this cascades to provider_models
        # which sets pipeline_stages.primary_model_id = NULL
        db.delete_provider(pid)

        stage = db.get_pipeline_stage(stage_id)
        assert stage is not None
        assert stage["primary_model_id"] is None

    def test_profile_mode_survives_stage_operations(self, tmp_path):
        """Adding/removing stages does not affect profile mode."""
        db = _make_db(tmp_path)
        pid = db.add_provider("Test", "openai-native")
        profile_id = db.add_pipeline_profile(
            "Stable Mode",
            transcription_provider_id=pid,
            text_provider_id=pid,
            mode="single_pass",
        )

        db.add_pipeline_stage(profile_id, "single_pass")
        assert db.get_pipeline_profile_mode(profile_id) == "single_pass"

        stages = db.list_pipeline_stages(profile_id)
        for s in stages:
            db.delete_pipeline_stage(s["id"])

        assert db.get_pipeline_profile_mode(profile_id) == "single_pass"


# ------------------------------------------------------------------
# Delete/disable protection
# ------------------------------------------------------------------


class TestDeleteProtection:
    """Provider/model delete/disable must fail when referenced by
    the active pipeline."""

    def _setup_active_pipeline(
        self, db,
    ) -> tuple[int, int, int, int]:
        """Create a provider, model, profile, stage, and mark it active.
        Returns (pid, model_id, profile_id, stage_id)."""
        pid = db.add_provider("Test AI", "openai-native")
        model_id = db.add_provider_model(
            pid, "whisper-1",
            capabilities={"transcription": True},
        )
        profile_id = db.add_pipeline_profile(
            "Active Pipeline",
            transcription_provider_id=pid,
            text_provider_id=pid,
        )
        stage_id = db.add_pipeline_stage(
            profile_id, "transcription", primary_model_id=model_id,
        )
        db.set_setup_state("active_pipeline_profile", str(profile_id))
        return pid, model_id, profile_id, stage_id

    def test_delete_provider_blocked_when_referenced(self, tmp_path):
        """Deleting a provider referenced by the active pipeline fails."""
        db = _make_db(tmp_path)
        pid, _, _, _ = self._setup_active_pipeline(db)

        with pytest.raises(ResourceInUseError, match="used as transcription provider"):
            db.delete_provider(pid)

        # Provider still exists
        assert db.get_provider(pid) is not None

    def test_delete_provider_succeeds_when_not_active(self, tmp_path):
        """Deleting a provider not in the active pipeline succeeds."""
        db = _make_db(tmp_path)
        pid = db.add_provider("Standalone", "openai-native")
        assert db.delete_provider(pid) is True

    def test_delete_model_blocked_when_primary(self, tmp_path):
        """Deleting a model used as primary in active pipeline fails."""
        db = _make_db(tmp_path)
        _, model_id, _, _ = self._setup_active_pipeline(db)

        with pytest.raises(ResourceInUseError, match="used as primary model"):
            db.delete_provider_model(model_id)

        assert db.get_provider_model(model_id) is not None

    def test_disable_model_blocked_when_primary(self, tmp_path):
        """Disabling a model used as primary in active pipeline fails."""
        db = _make_db(tmp_path)
        _, model_id, _, _ = self._setup_active_pipeline(db)

        with pytest.raises(ResourceInUseError, match="used as primary model"):
            db.update_provider_model(model_id, enabled=False)

        # Model is still enabled
        assert db.get_provider_model(model_id)["enabled"] == 1

    def test_disable_model_succeeds_when_not_in_use(self, tmp_path):
        """Disabling a model not referenced by active pipeline succeeds."""
        db = _make_db(tmp_path)
        pid = db.add_provider("Test", "openai-native")
        model_id = db.add_provider_model(pid, "whisper-1", capabilities={
            "transcription": True,
        })
        db.update_provider_model(model_id, enabled=False)
        assert db.get_provider_model(model_id)["enabled"] == 0

    def test_delete_model_blocked_when_fallback(self, tmp_path):
        """Deleting a model used as fallback in active pipeline fails."""
        db = _make_db(tmp_path)
        pid = db.add_provider("Test AI", "openai-native")
        primary = db.add_provider_model(
            pid, "whisper-1",
            capabilities={"transcription": True},
        )
        fallback = db.add_provider_model(
            pid, "whisper-1-alt",
            capabilities={"transcription": True},
        )
        profile_id = db.add_pipeline_profile(
            "Active Pipeline",
            transcription_provider_id=pid,
            text_provider_id=pid,
        )
        stage_id = db.add_pipeline_stage(
            profile_id, "transcription", primary_model_id=primary,
        )
        db.add_stage_fallback(stage_id, fallback)
        db.set_setup_state("active_pipeline_profile", str(profile_id))

        with pytest.raises(ResourceInUseError, match="used as fallback"):
            db.delete_provider_model(fallback)

        assert db.get_provider_model(fallback) is not None

    def test_no_active_pipeline_allows_delete(self, tmp_path):
        """Without active pipeline, provider/model can be deleted freely."""
        db = _make_db(tmp_path)
        pid = db.add_provider("Free", "openai-native")
        model_id = db.add_provider_model(pid, "whisper-1")

        # No active pipeline set
        assert db.delete_provider_model(model_id) is True
        assert db.delete_provider(pid) is True

    def test_disable_provider_blocked_when_referenced(self, tmp_path):
        """Disabling a provider referenced by the active pipeline fails."""
        db = _make_db(tmp_path)
        pid, _, _, _ = self._setup_active_pipeline(db)

        with pytest.raises(ResourceInUseError, match="used as transcription provider"):
            db.update_provider(pid, enabled=False)

        # Provider is still enabled
        assert db.get_provider(pid)["enabled"] == 1

    def test_disable_provider_succeeds_when_not_active(self, tmp_path):
        """Disabling a provider not in the active pipeline succeeds."""
        db = _make_db(tmp_path)
        pid = db.add_provider("Standalone", "openai-native", enabled=True)
        db.update_provider(pid, enabled=False)
        assert db.get_provider(pid)["enabled"] == 0

    def test_disable_provider_succeeds_when_no_active_pipeline(self, tmp_path):
        """Disabling a provider when no active pipeline is set succeeds."""
        db = _make_db(tmp_path)
        pid = db.add_provider("Free", "openai-native", enabled=True)
        db.update_provider(pid, enabled=False)
        assert db.get_provider(pid)["enabled"] == 0

    def test_disable_provider_noop_when_already_disabled(self, tmp_path):
        """Disabling a provider that is already disabled does not raise."""
        db = _make_db(tmp_path)
        pid, _, _, _ = self._setup_active_pipeline(db)
        # First disable manually via SQL to bypass check
        db.connection.execute(
            "UPDATE provider_connections SET enabled = 0 WHERE id = ?", (pid,)
        )
        db.connection.commit()
        # Second disable should not raise (already disabled)
        result = db.update_provider(pid, enabled=False)
        assert result is True
        assert db.get_provider(pid)["enabled"] == 0

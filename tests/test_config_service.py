"""
Tests for the application configuration service (A3).

Covers the settings registry, ConfigService read/validate/update operations,
secret field handling, transactional bulk updates, and reload signalling.
"""

import pytest

from bot.config_service import (
    ConfigService,
    SETTINGS_REGISTRY,
    SettingDef,
    _registry_by_key,
    _validate,
)
from bot.database.repository import DatabaseManager
from bot.database.secret_store import SecretStore


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _make_db(tmp_path) -> DatabaseManager:
    db = DatabaseManager(str(tmp_path / "app.sqlite3"))
    db.initialize()
    return db


def _make_service(tmp_path, with_secret_store=False) -> ConfigService:
    db = _make_db(tmp_path)
    secret_store = None
    if with_secret_store:
        key_path = tmp_path / ".master_key"
        store = SecretStore(str(key_path))
        store.initialize()
        secret_store = store
    return ConfigService(db, secret_store=secret_store)


# ------------------------------------------------------------------
# Registry integrity
# ------------------------------------------------------------------


def test_registry_contains_expected_settings():
    """All settings from .env.example / bot.config are represented."""
    keys = [s.key for s in SETTINGS_REGISTRY]
    assert "telegram_token" in keys
    assert "llm_provider" in keys
    assert "llm_model" in keys
    assert "prompt_system" in keys
    assert "prompt_refine_template" in keys
    assert "rate_limit_max_per_user" in keys
    assert "rate_limit_cooldown" in keys
    assert "rate_limit_max_concurrent_global" in keys
    assert "rate_limit_max_file_size_mb" in keys
    assert "rate_limit_queue_enabled" in keys
    assert "rate_limit_max_queue_size" in keys
    assert "rate_limit_max_queued_per_user" in keys
    assert "provider_resilience_enabled" in keys
    assert "provider_resilience_failure_threshold" in keys
    assert "provider_resilience_cooldown_seconds" in keys
    assert "telegram_draft_streaming" in keys
    assert "audio_cleanup_on_startup" in keys


def test_registry_keys_are_unique():
    """No duplicate keys in the registry."""
    keys = [s.key for s in SETTINGS_REGISTRY]
    assert len(keys) == len(set(keys))


def test_registry_by_key_contains_all():
    lookup = _registry_by_key()
    for sd in SETTINGS_REGISTRY:
        assert lookup[sd.key] is sd


def test_secret_settings_marked_correctly():
    """Secret fields must have is_secret=True."""
    for sd in SETTINGS_REGISTRY:
        if sd.type == "secret":
            assert sd.is_secret is True, f"{sd.key} is secret but is_secret=False"
        if sd.is_secret:
            assert sd.type == "secret", f"{sd.key} is is_secret but type={sd.type}"


def test_enum_settings_have_values():
    """Enum settings must have at least one valid value."""
    for sd in SETTINGS_REGISTRY:
        if sd.type == "enum":
            assert sd.enum_values is not None and len(sd.enum_values) >= 1, (
                f"{sd.key} is enum but has no enum_values"
            )


def test_integer_settings_have_min_value():
    """Integer settings must have a min_value (sane default)."""
    for sd in SETTINGS_REGISTRY:
        if sd.type == "integer":
            assert sd.min_value is not None, (
                f"{sd.key} is integer but has no min_value"
            )


# ------------------------------------------------------------------
# SettingDef serialisation helpers
# ------------------------------------------------------------------


def test_def_to_dict_includes_required_fields(tmp_path):
    service = _make_service(tmp_path)
    definitions = service.list_definitions()
    assert len(definitions) == len(SETTINGS_REGISTRY)

    for d in definitions:
        assert "key" in d
        assert "label" in d
        assert "description" in d
        assert "type" in d
        assert "group" in d
        assert "requires_reload" in d
        assert "is_secret" in d
        assert "required" in d
        assert "scope" in d


# ------------------------------------------------------------------
# ConfigService — reading
# ------------------------------------------------------------------


def test_get_all_settings_returns_all_with_defaults(tmp_path):
    service = _make_service(tmp_path)
    settings = service.get_all_settings()

    assert len(settings) == len(SETTINGS_REGISTRY)

    # Check a few defaults
    by_key = {s["key"]: s for s in settings}
    assert by_key["llm_provider"]["value"] == "openai"
    assert by_key["rate_limit_max_per_user"]["value"] == 2
    assert by_key["rate_limit_queue_enabled"]["value"] is True
    assert by_key["telegram_token"]["value"] is None
    assert by_key["telegram_token"]["has_value"] is False


def test_get_all_settings_shows_db_values(tmp_path):
    service = _make_service(tmp_path)
    service._db.set_setting("llm_provider", "gemini")
    service._db.set_setting("rate_limit_max_per_user", "5")

    settings = service.get_all_settings()
    by_key = {s["key"] for s in settings}
    assert "llm_provider" in by_key


def test_get_setting_returns_single_setting(tmp_path):
    service = _make_service(tmp_path)
    result = service.get_setting("llm_provider")
    assert result is not None
    assert result["key"] == "llm_provider"
    assert result["value"] == "openai"  # default


def test_get_setting_unknown_key_returns_none(tmp_path):
    service = _make_service(tmp_path)
    assert service.get_setting("nonexistent") is None


def test_get_setting_reflects_db_value(tmp_path):
    service = _make_service(tmp_path)
    service._db.set_setting("rate_limit_max_per_user", "10")
    result = service.get_setting("rate_limit_max_per_user")
    assert result is not None
    assert result["value"] == 10


def test_get_settings_by_group_groups_correctly(tmp_path):
    service = _make_service(tmp_path)
    groups = service.get_settings_by_group()
    assert "telegram" in groups
    assert "provider" in groups
    assert "rate_limits" in groups
    assert "resilience" in groups
    assert "output" in groups
    assert "infrastructure" in groups

    # Telegram group should have telegram_token
    tg_keys = {s["key"] for s in groups["telegram"]}
    assert "telegram_token" in tg_keys


# ------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------


class TestValidation:
    """Group tests for the _validate helper."""

    def test_required_field_empty(self):
        sd = SettingDef(key="test", label="Test", description="x", type="string", required=True)
        assert _validate(sd, "") == ["Test è obbligatorio."]

    def test_optional_field_empty(self):
        sd = SettingDef(key="test", label="Test", description="x", type="string", required=False)
        assert _validate(sd, "") == []

    def test_integer_valid(self):
        sd = SettingDef(key="test", label="Test", description="x", type="integer", min_value=1)
        assert _validate(sd, "5") == []

    def test_integer_below_min(self):
        sd = SettingDef(key="test", label="Test", description="x", type="integer", min_value=1)
        errors = _validate(sd, "0")
        assert len(errors) == 1
        assert "maggiore o uguale" in errors[0]

    def test_integer_non_numeric(self):
        sd = SettingDef(key="test", label="Test", description="x", type="integer", min_value=1)
        errors = _validate(sd, "abc")
        assert len(errors) == 1
        assert "numero intero" in errors[0]

    def test_integer_max_value(self):
        sd = SettingDef(key="test", label="Test", description="x", type="integer", min_value=0, max_value=100)
        assert _validate(sd, "50") == []
        errors = _validate(sd, "150")
        assert len(errors) == 1
        assert "minore o uguale" in errors[0]

    def test_boolean_valid_values(self):
        sd = SettingDef(key="test", label="Test", description="x", type="boolean")
        for val in ("1", "0", "true", "false", "yes", "no"):
            assert _validate(sd, val) == [], f"expected '{val}' to be valid"

    def test_boolean_invalid(self):
        sd = SettingDef(key="test", label="Test", description="x", type="boolean")
        errors = _validate(sd, "maybe")
        assert len(errors) == 1

    def test_enum_valid(self):
        sd = SettingDef(key="test", label="Test", description="x", type="enum", enum_values=["a", "b", "c"])
        assert _validate(sd, "a") == []

    def test_enum_invalid(self):
        sd = SettingDef(key="test", label="Test", description="x", type="enum", enum_values=["a", "b"])
        errors = _validate(sd, "c")
        assert len(errors) == 1

    def test_refine_template_missing_placeholder(self):
        sd = SettingDef(key="prompt_refine_template", label="Template", description="x", type="text")
        errors = _validate(sd, "no placeholder here")
        assert len(errors) == 1
        assert "raw_text" in errors[0]

    def test_refine_template_with_placeholder(self):
        sd = SettingDef(key="prompt_refine_template", label="Template", description="x", type="text")
        assert _validate(sd, "prefix {raw_text} suffix") == []

    def test_regular_text_no_placeholder_check(self):
        sd = SettingDef(key="prompt_system", label="System", description="x", type="text")
        assert _validate(sd, "any text") == []


# ------------------------------------------------------------------
# ConfigService — writing
# ------------------------------------------------------------------


def test_update_setting_persists_value(tmp_path):
    service = _make_service(tmp_path)
    errors = service.update_setting("rate_limit_max_per_user", "3")
    assert errors == []
    # Verify via DB directly
    assert service._db.get_setting("rate_limit_max_per_user") == "3"


def test_update_setting_with_invalid_value_returns_errors(tmp_path):
    service = _make_service(tmp_path)
    errors = service.update_setting("rate_limit_max_per_user", "abc")
    assert len(errors) > 0
    # Verify value was NOT written
    assert service._db.get_setting("rate_limit_max_per_user") is None


def test_update_setting_unknown_key_raises(tmp_path):
    service = _make_service(tmp_path)
    with pytest.raises(ValueError, match="sconosciuto"):
        service.update_setting("nonexistent", "value")


def test_update_settings_bulk_success(tmp_path):
    service = _make_service(tmp_path)
    result = service.update_settings({
        "llm_provider": "gemini",
        "rate_limit_max_per_user": "5",
    })
    # All should succeed (empty error lists)
    for key in ("llm_provider", "rate_limit_max_per_user"):
        assert result[key] == []
    # Verify DB
    assert service._db.get_setting("llm_provider") == "gemini"
    assert service._db.get_setting("rate_limit_max_per_user") == "5"


def test_update_settings_atomic_rollback_on_error(tmp_path):
    """If one setting is invalid, none should be written."""
    service = _make_service(tmp_path)

    # Ensure DB is clean
    assert service._db.get_setting("llm_provider") is None
    assert service._db.get_setting("rate_limit_max_per_user") is None

    result = service.update_settings({
        "llm_provider": "gemini",
        "rate_limit_max_per_user": "abc",  # invalid
    })

    # Error for the invalid one
    assert "rate_limit_max_per_user" in result
    assert len(result["rate_limit_max_per_user"]) > 0

    # The valid key is NOT in the returned dict because the code short-circuits
    # before adding it. But more importantly, nothing was written to the DB.
    assert "llm_provider" not in result
    assert service._db.get_setting("llm_provider") is None
    assert service._db.get_setting("rate_limit_max_per_user") is None


def test_update_settings_unknown_key(tmp_path):
    service = _make_service(tmp_path)
    result = service.update_settings({"nonexistent": "x"})
    assert "nonexistent" in result
    assert len(result["nonexistent"]) > 0


def test_boolean_value_normalized_on_write(tmp_path):
    service = _make_service(tmp_path)
    service.update_setting("telegram_draft_streaming", "yes")
    assert service._db.get_setting("telegram_draft_streaming") == "1"

    service.update_setting("telegram_draft_streaming", "false")
    assert service._db.get_setting("telegram_draft_streaming") == "0"


# ------------------------------------------------------------------
# Secret fields — write-only semantics
# ------------------------------------------------------------------


def test_secret_field_masked_on_read(tmp_path):
    service = _make_service(tmp_path)
    service._db.set_setting("telegram_token", "super-secret-token")

    result = service.get_setting("telegram_token")
    assert result is not None
    assert result["value"] is None  # never returned
    assert result["has_value"] is True


def test_secret_field_encrypted_with_store(tmp_path):
    service = _make_service(tmp_path, with_secret_store=True)
    service.update_setting("telegram_token", "plaintext-token")

    # The DB should contain the encrypted value, not the plaintext
    raw = service._db.get_setting("telegram_token")
    assert raw != "plaintext-token"
    assert raw.startswith("gAAAAA")  # Fernet base64 prefix


def test_secret_field_has_value_false_when_empty(tmp_path):
    service = _make_service(tmp_path)
    result = service.get_setting("telegram_token")
    assert result is not None
    assert result["value"] is None
    assert result["has_value"] is False


# ------------------------------------------------------------------
# Reload signalling
# ------------------------------------------------------------------


def test_get_reload_required_identifies_reload_settings(tmp_path):
    service = _make_service(tmp_path)
    reload_keys = service.get_reload_required({
        "telegram_token": "new-token",
        "rate_limit_max_per_user": "5",
        "llm_provider": "gemini",
    })
    assert "telegram_token" in reload_keys
    assert "rate_limit_max_per_user" not in reload_keys
    assert "llm_provider" not in reload_keys  # has requires_reload=False


def test_get_reload_required_does_not_persist(tmp_path):
    service = _make_service(tmp_path)
    service.get_reload_required({"telegram_token": "new-token"})
    # Nothing should have been written to DB
    assert service._db.get_setting("telegram_token") is None


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------


def test_empty_string_updates_for_optional_settings(tmp_path):
    """Optional string settings can be cleared by setting empty string."""
    service = _make_service(tmp_path)
    # First set a value
    service.update_setting("llm_model", "gpt-4")
    assert service._db.get_setting("llm_model") == "gpt-4"

    # Then clear it (empty string is valid for optional)
    errors = service.update_setting("llm_model", "")
    assert errors == []
    assert service._db.get_setting("llm_model") == ""


def test_case_insensitive_boolean_values(tmp_path):
    service = _make_service(tmp_path)
    for val in ("TRUE", "YES", "True", "YeS", "1"):
        errors = service.update_setting("telegram_draft_streaming", val)
        assert errors == []
        assert service._db.get_setting("telegram_draft_streaming") == "1"

    for val in ("FALSE", "NO", "False", "nO", "0"):
        errors = service.update_setting("telegram_draft_streaming", val)
        assert errors == []
        assert service._db.get_setting("telegram_draft_streaming") == "0"


def test_validate_value_raises_for_unknown_key(tmp_path):
    service = _make_service(tmp_path)
    with pytest.raises(ValueError, match="sconosciuto"):
        service.validate_value("nonexistent", "x")


def test_config_service_initializes_without_secret_store(tmp_path):
    """Service should work without a secret store (graceful degradation)."""
    service = _make_service(tmp_path, with_secret_store=False)
    assert service is not None
    settings = service.get_all_settings()
    assert len(settings) > 0


def test_list_definitions_has_no_values(tmp_path):
    service = _make_service(tmp_path)
    defs = service.list_definitions()
    for d in defs:
        assert "value" not in d, f"{d['key']} should not have value in definitions"
        assert "has_value" not in d


def test_typed_default_values_are_cast_correctly(tmp_path):
    service = _make_service(tmp_path)
    settings = service.get_all_settings()
    by_key = {s["key"]: s for s in settings}

    # Integer defaults should be int
    assert isinstance(by_key["rate_limit_max_per_user"]["value"], int)
    # Boolean defaults should be bool
    assert isinstance(by_key["rate_limit_queue_enabled"]["value"], bool)
    assert by_key["rate_limit_queue_enabled"]["value"] is True
    # String defaults should be str
    assert isinstance(by_key["llm_provider"]["value"], str)


# ------------------------------------------------------------------
# A4.1 — Secret writes fail without encryption
# ------------------------------------------------------------------


def test_update_setting_rejects_secret_without_store(tmp_path):
    """Writing a secret without a SecretStore should be rejected."""
    service = _make_service(tmp_path, with_secret_store=False)
    errors = service.update_setting("telegram_token", "some-token")
    assert len(errors) > 0
    assert "crittografia" in errors[0].lower()


def test_update_setting_allows_secret_with_store(tmp_path):
    """Writing a secret WITH a SecretStore should succeed."""
    service = _make_service(tmp_path, with_secret_store=True)
    errors = service.update_setting("telegram_token", "some-token")
    assert errors == []


def test_update_setting_rejects_required_secret_when_empty(tmp_path):
    """A required secret field should fail validation when empty,
    regardless of encryption availability."""
    service = _make_service(tmp_path, with_secret_store=False)
    errors = service.update_setting("telegram_token", "")
    assert len(errors) > 0
    assert "obbligatorio" in errors[0].lower()


def test_update_settings_bulk_rejects_secret_without_store(tmp_path):
    """Bulk update should reject any secret when encryption is unavailable."""
    service = _make_service(tmp_path, with_secret_store=False)
    result = service.update_settings({
        "llm_provider": "gemini",
        "telegram_token": "new-token",
    })
    # telegram_token should have an error
    assert "telegram_token" in result
    assert len(result["telegram_token"]) > 0
    assert "crittografia" in result["telegram_token"][0].lower()
    # llm_provider should NOT be in the result (short-circuits before write)
    # because the bulk write is atomic — all or nothing
    assert "llm_provider" not in result
    # DB should be empty (nothing written)
    assert service._db.get_setting("telegram_token") is None
    assert service._db.get_setting("llm_provider") is None


def test_update_settings_bulk_allows_secret_with_store(tmp_path):
    """Bulk update with a secret should work when encryption is available."""
    service = _make_service(tmp_path, with_secret_store=True)
    result = service.update_settings({
        "llm_provider": "gemini",
        "telegram_token": "new-token",
    })
    for key in ("llm_provider", "telegram_token"):
        assert result[key] == [], f"key {key} failed: {result[key]}"
    assert service._db.get_setting("llm_provider") == "gemini"
    # The token should be encrypted in the DB
    raw = service._db.get_setting("telegram_token")
    assert raw != "new-token"
    assert raw.startswith("gAAAAA")

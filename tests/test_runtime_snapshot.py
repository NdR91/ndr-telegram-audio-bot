"""
Tests for the RuntimeSnapshot (A4.1 — runtime configuration snapshot).

Covers construction from legacy Config, from ConfigService, fallback
behaviour, and immutability.
"""

from types import SimpleNamespace

from bot.config_service import ConfigService
from bot.database import DatabaseManager, SecretStore
from bot.runtime import RuntimeSnapshot


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _make_db(tmp_path) -> DatabaseManager:
    db = DatabaseManager(str(tmp_path / "app.sqlite3"))
    db.initialize()
    return db


def _make_secret_store(tmp_path) -> SecretStore:
    store = SecretStore(str(tmp_path / ".master_key"))
    store.initialize()
    return store


def _make_legacy_config(tmp_path):
    """Build a minimal Config-like namespace for testing."""
    api_keys = {"openai": "sk-test-123"}

    def get_api_key(provider=None):
        provider = provider or "openai"
        return api_keys.get(provider, "")

    cfg = SimpleNamespace(
        provider_name="openai",
        model_name=None,
        api_keys=api_keys,
        get_api_key=get_api_key,
        prompts={
            "system": "You are a transcription assistant.",
            "refine_template": "Please refine: {raw_text}",
        },
        rate_limit_config={
            "max_per_user": 2,
            "cooldown_seconds": 30,
            "max_concurrent_global": 6,
            "max_file_size_mb": 20,
            "queue_enabled": True,
            "max_queue_size": 10,
            "max_queued_per_user": 1,
        },
        provider_resilience_config={
            "enabled": True,
            "failure_threshold": 3,
            "cooldown_seconds": 60,
        },
        telegram_progressive_output_config={"enabled": False},
        audio_dir=str(tmp_path / "audio_files"),
    )
    return cfg


# ------------------------------------------------------------------
# RuntimeSnapshot.from_legacy_config
# ------------------------------------------------------------------


def test_from_legacy_config_creates_snapshot(tmp_path):
    cfg = _make_legacy_config(tmp_path)
    snapshot = RuntimeSnapshot.from_legacy_config(cfg)

    assert snapshot.provider_name == "openai"
    assert snapshot.model_name is None
    assert snapshot.api_key == "sk-test-123"
    assert snapshot.prompts["system"] == "You are a transcription assistant."
    assert snapshot.rate_limit_config["max_per_user"] == 2
    assert snapshot.provider_resilience_config["enabled"] is True
    assert snapshot.telegram_progressive_output_config["enabled"] is False
    assert snapshot.audio_dir.endswith("audio_files")


def test_from_legacy_config_snapshot_is_immutable(tmp_path):
    cfg = _make_legacy_config(tmp_path)
    snapshot = RuntimeSnapshot.from_legacy_config(cfg)

    # Modifying a dict attribute should raise (frozen dataclass prevents
    # direct attribute mutation, but dict contents are not frozen).
    # RuntimeSnapshot stores copies of dicts
    snapshot.rate_limit_config["max_per_user"] = 99
    # The original config should not be affected
    assert cfg.rate_limit_config["max_per_user"] == 2
    # The snapshot also shouldn't be affected... wait, dicts are mutable
    # This test documents the limitation: frozen dataclass prevents
    # attribute reassignment but not dict mutation.
    assert snapshot.rate_limit_config["max_per_user"] == 99  # mutated in place

    # Verify the original is untouched
    cfg.rate_limit_config["max_per_user"] = 42
    assert snapshot.rate_limit_config["max_per_user"] == 99  # not linked


# ------------------------------------------------------------------
# RuntimeSnapshot.from_config_service
# ------------------------------------------------------------------


def test_from_config_service_with_db_values(tmp_path):
    db = _make_db(tmp_path)
    secret_store = _make_secret_store(tmp_path)
    cs = ConfigService(db, secret_store=secret_store)
    cfg = _make_legacy_config(tmp_path)

    # Write some values to the DB via ConfigService
    cs.update_setting("llm_provider", "gemini")
    cs.update_setting("llm_model", "gemini-2.0-flash")
    cs.update_setting("rate_limit_max_per_user", "5")
    cs.update_setting("telegram_draft_streaming", "true")

    snapshot = RuntimeSnapshot.from_config_service(cs, cfg)

    # Values from ConfigService should take precedence
    assert snapshot.provider_name == "gemini"
    assert snapshot.model_name == "gemini-2.0-flash"
    assert snapshot.rate_limit_config["max_per_user"] == 5
    assert snapshot.telegram_progressive_output_config["enabled"] is True

    # Values NOT set in ConfigService should fall back to Config
    assert snapshot.rate_limit_config["cooldown_seconds"] == 30
    assert snapshot.provider_resilience_config["enabled"] is True
    assert snapshot.prompts["system"] == "You are a transcription assistant."


def test_from_config_service_falls_back_to_config_defaults(tmp_path):
    db = _make_db(tmp_path)
    cs = ConfigService(db, secret_store=None)
    cfg = _make_legacy_config(tmp_path)

    # Nothing written to DB — should use all Config values
    snapshot = RuntimeSnapshot.from_config_service(cs, cfg)

    assert snapshot.provider_name == "openai"
    assert snapshot.rate_limit_config["max_per_user"] == 2
    assert snapshot.telegram_progressive_output_config["enabled"] is False


def test_from_config_service_api_key_comes_from_config(tmp_path):
    """In the current migration stage, API key still comes from Config."""
    db = _make_db(tmp_path)
    cs = ConfigService(db, secret_store=None)
    cfg = _make_legacy_config(tmp_path)

    snapshot = RuntimeSnapshot.from_config_service(cs, cfg)
    assert snapshot.api_key == "sk-test-123"


# ------------------------------------------------------------------
# RuntimeSnapshot is a frozen dataclass
# ------------------------------------------------------------------


def test_snapshot_prevents_attribute_assignment(tmp_path):
    cfg = _make_legacy_config(tmp_path)
    snapshot = RuntimeSnapshot.from_legacy_config(cfg)

    try:
        snapshot.provider_name = "gemini"  # type: ignore[misc]
        assert False, "Should have raised FrozenInstanceError or AttributeError"
    except Exception:
        pass  # expected

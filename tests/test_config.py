import json
import subprocess

import pytest

from bot.config import Config
from bot.exceptions import InvalidConfig


def configure_valid_environment(monkeypatch, tmp_path):
    authorized_file = tmp_path / "authorized.json"
    authorized_file.write_text(
        json.dumps({"admin": ["123"], "users": ["456"], "groups": []}),
        encoding="utf-8",
    )
    audio_dir = tmp_path / "audio_files"

    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("TELEGRAM_DRAFT_STREAMING", raising=False)
    monkeypatch.setenv("AUTHORIZED_FILE", str(authorized_file))
    monkeypatch.setenv("AUDIO_DIR", str(audio_dir))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="ok", stderr=""),
    )

    return audio_dir


def test_config_loads_defaults_and_normalizes_ids(monkeypatch, tmp_path):
    audio_dir = configure_valid_environment(monkeypatch, tmp_path)

    config = Config()

    assert config.provider_name == "openai"
    assert config.model_name is None
    assert config.authorized_data["admin"] == [123]
    assert config.authorized_data["users"] == [456]
    assert config.authorized_db == "audio_files/authorized.sqlite3"
    assert config.telegram_progressive_output_config["enabled"] is False
    assert audio_dir.exists()


def test_config_requires_raw_text_placeholder(monkeypatch, tmp_path):
    configure_valid_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("PROMPT_REFINE_TEMPLATE", "missing placeholder")

    with pytest.raises(InvalidConfig):
        Config()


@pytest.mark.parametrize(
    ("variable", "value", "expected_message"),
    [
        ("RATE_LIMIT_PER_USER", "many", "RATE_LIMIT_PER_USER must be an integer"),
        (
            "RATE_LIMIT_GLOBAL",
            "0",
            "RATE_LIMIT_GLOBAL must be greater than or equal to 1",
        ),
        (
            "RATE_LIMIT_COOLDOWN",
            "-1",
            "RATE_LIMIT_COOLDOWN must be greater than or equal to 0",
        ),
        (
            "RATE_LIMIT_FILE_SIZE",
            "0",
            "RATE_LIMIT_FILE_SIZE must be greater than or equal to 1",
        ),
        (
            "RATE_LIMIT_QUEUE_SIZE",
            "-1",
            "RATE_LIMIT_QUEUE_SIZE must be greater than or equal to 0",
        ),
        (
            "RATE_LIMIT_QUEUE_PER_USER",
            "0",
            "RATE_LIMIT_QUEUE_PER_USER must be greater than or equal to 1",
        ),
        (
            "PROVIDER_RESILIENCE_THRESHOLD",
            "0",
            "PROVIDER_RESILIENCE_THRESHOLD must be greater than or equal to 1",
        ),
        (
            "PROVIDER_RESILIENCE_COOLDOWN",
            "-1",
            "PROVIDER_RESILIENCE_COOLDOWN must be greater than or equal to 0",
        ),
    ],
)
def test_config_reports_invalid_numeric_variable(
    monkeypatch, tmp_path, variable, value, expected_message
):
    configure_valid_environment(monkeypatch, tmp_path)
    monkeypatch.setenv(variable, value)

    with pytest.raises(InvalidConfig, match=expected_message):
        Config()


@pytest.mark.parametrize(
    "variable",
    [
        "RATE_LIMIT_QUEUE_ENABLED",
        "PROVIDER_RESILIENCE_ENABLED",
        "TELEGRAM_DRAFT_STREAMING",
    ],
)
def test_config_rejects_ambiguous_boolean_values(monkeypatch, tmp_path, variable):
    configure_valid_environment(monkeypatch, tmp_path)
    monkeypatch.setenv(variable, "sometimes")

    with pytest.raises(InvalidConfig, match=variable):
        Config()


def test_config_accepts_explicit_boolean_values(monkeypatch, tmp_path):
    configure_valid_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("RATE_LIMIT_QUEUE_ENABLED", "no")
    monkeypatch.setenv("PROVIDER_RESILIENCE_ENABLED", "yes")
    monkeypatch.setenv("TELEGRAM_DRAFT_STREAMING", "true")

    config = Config()

    assert config.rate_limit_config["queue_enabled"] is False
    assert config.provider_resilience_config["enabled"] is True
    assert config.telegram_progressive_output_config["enabled"] is True


# ------------------------------------------------------------------
# A7 — Relaxed mode tests
# ------------------------------------------------------------------


def test_relaxed_config_with_no_env(monkeypatch, tmp_path):
    """Config(relaxed=True) succeeds with no env vars or authorized.json."""
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("AUTHORIZED_FILE", str(tmp_path / "nonexistent.json"))
    monkeypatch.setenv("AUDIO_DIR", str(tmp_path / "audio_files"))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="ok", stderr=""),
    )

    config = Config(relaxed=True)

    assert config._relaxed is True
    assert config.telegram_token == ""
    # LLM_PROVIDER defaults to "openai" when not set — valid provider name is kept.
    assert config.provider_name == "openai"
    assert config.api_keys == {}
    assert config.authorized_file == ""
    assert config.authorized_data == {"admin": [], "users": [], "groups": []}
    assert config.audio_dir == str(tmp_path / "audio_files")


def test_relaxed_config_get_api_key_returns_empty(monkeypatch, tmp_path):
    """Config(relaxed=True).get_api_key() returns empty string."""
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("AUTHORIZED_FILE", raising=False)
    monkeypatch.setenv("AUDIO_DIR", str(tmp_path / "audio_files"))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="ok", stderr=""),
    )

    config = Config(relaxed=True)
    assert config.get_api_key() == ""
    assert config.get_api_key("openai") == ""


def test_strict_config_still_fails_without_token(monkeypatch, tmp_path):
    """Config() (strict) still raises when TELEGRAM_TOKEN is missing."""
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AUTHORIZED_FILE", raising=False)
    monkeypatch.setenv("AUDIO_DIR", str(tmp_path / "audio_files"))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="ok", stderr=""),
    )

    from bot.exceptions import MissingRequiredConfig
    with pytest.raises(MissingRequiredConfig):
        Config()


def test_relaxed_config_audio_dir_created(monkeypatch, tmp_path):
    """Config(relaxed=True) creates the audio directory."""
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("AUTHORIZED_FILE", raising=False)
    audio_dir = tmp_path / "relaxed_audio"
    monkeypatch.setenv("AUDIO_DIR", str(audio_dir))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="ok", stderr=""),
    )

    config = Config(relaxed=True)
    assert audio_dir.exists()

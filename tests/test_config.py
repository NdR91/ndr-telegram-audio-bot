import json
import subprocess

import pytest

from bot.config import Config
from bot.exceptions import InvalidConfig


def test_config_loads_defaults_and_normalizes_ids(monkeypatch, tmp_path):
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
    monkeypatch.setenv("AUTHORIZED_FILE", str(authorized_file))
    monkeypatch.setenv("AUDIO_DIR", str(audio_dir))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="ok", stderr=""),
    )

    config = Config()

    assert config.provider_name == "openai"
    assert config.model_name is None
    assert config.authorized_data["admin"] == [123]
    assert config.authorized_data["users"] == [456]
    assert audio_dir.exists()


def test_config_requires_raw_text_placeholder(monkeypatch, tmp_path):
    authorized_file = tmp_path / "authorized.json"
    authorized_file.write_text(
        json.dumps({"admin": [123], "users": [], "groups": []}),
        encoding="utf-8",
    )
    audio_dir = tmp_path / "audio_files"

    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("AUTHORIZED_FILE", str(authorized_file))
    monkeypatch.setenv("AUDIO_DIR", str(audio_dir))
    monkeypatch.setenv("PROMPT_REFINE_TEMPLATE", "missing placeholder")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="ok", stderr=""),
    )

    with pytest.raises(InvalidConfig):
        Config()

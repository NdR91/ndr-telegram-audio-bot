import json
import os
import subprocess
import sys
from pathlib import Path

from bot.deployment import DeploymentSettings
from bot.database.v2_repository import V2Repository


def test_deployment_settings_are_infrastructure_only(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIO_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WEB_PORT", "8091")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-read")
    settings = DeploymentSettings.from_environment()
    assert settings.db_path == tmp_path / "data" / "app-v2.sqlite3"
    assert settings.key_path.name == ".master_key-v2"
    assert settings.port == 8091
    assert not hasattr(settings, "openai_api_key")


def test_non_v2_database_target_is_rejected(tmp_path):
    import sqlite3
    import pytest

    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE app_metadata (metadata_key TEXT PRIMARY KEY, metadata_value TEXT NOT NULL)")
    connection.execute("INSERT INTO app_metadata VALUES ('database_kind', 'v1')")
    connection.commit()
    connection.close()
    with pytest.raises(ValueError, match="migration is not automatic"):
        DeploymentSettings(tmp_path, path, tmp_path / "key").validate_database_target()


def test_atomic_credential_reference(repo):
    version = repo.put_credential_with_state("telegram", "synthetic-token", "telegram_credential_version",
                                             {"current_bot_id": "123"})
    assert repo.get_setup_state("telegram_credential_version") == str(version)
    assert repo.read_credential("telegram", version) == "synthetic-token"


def test_all_entrypoints_use_the_same_factory(monkeypatch):
    import bot.main
    import bot.web.main

    calls = []
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: calls.append((args, kwargs)))
    bot.main.main()
    bot.web.main.main()
    assert calls[0][0][0] == calls[1][0][0] == "bot.web.app:create_app"
    assert calls[0][1]["factory"] and calls[1][1]["factory"]


def test_direct_bot_main_script_bootstraps_package_path_without_runtime_data(tmp_path):
    repo_root = Path(__file__).parents[2]
    (tmp_path / "uvicorn.py").write_text(
        "import json, os\n"
        "def run(*args, **kwargs):\n"
        "    with open(os.environ['BOOT_RESULT'], 'w') as result:\n"
        "        json.dump({'args': args, 'kwargs': kwargs}, result)\n"
    )
    result_path = tmp_path / "boot-result.json"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(tmp_path),
        "AUDIO_DIR": str(tmp_path / "synthetic-data"),
        "APPLICATION_DB": str(tmp_path / "synthetic-data" / "app-v2.sqlite3"),
        "MASTER_KEY_FILE": str(tmp_path / "synthetic-data" / "synthetic.key"),
        "BOOT_RESULT": str(result_path),
    }
    completed = subprocess.run(
        [sys.executable, str(repo_root / "bot" / "main.py")],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=5,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(result_path.read_text())
    assert payload["args"] == ["bot.web.app:create_app"]
    assert payload["kwargs"]["factory"] is True
    assert not (tmp_path / "synthetic-data").exists()


def test_v2_app_factory_module_does_not_import_legacy_config(monkeypatch):
    import builtins
    import importlib
    import sys

    sys.modules.pop("bot.web.app", None)
    original_import = builtins.__import__
    imports = []

    def tracked_import(name, *args, **kwargs):
        if name == "bot.config" or name.startswith("bot.config."):
            imports.append(name)
            raise AssertionError("legacy configuration must not be imported by V2 web startup")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", tracked_import)
    module = importlib.import_module("bot.web.app")
    assert callable(module.create_app)
    assert imports == []


def test_new_v2_schema_includes_profile_fallback_diagnostics(repo):
    with repo.read() as connection:
        version = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        columns = {row[1] for row in connection.execute("PRAGMA table_info(diagnostic_events)")}
    assert version == 5
    assert {"primary_profile_id", "fallback_profile_id", "failure_stage",
            "failure_category", "outcome"} <= columns

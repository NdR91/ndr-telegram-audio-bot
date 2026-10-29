import subprocess
import sys
from pathlib import Path

from bot.deployment import DeploymentSettings
from bot.services import build_services
from bot.transcription.configuration import ConfigurationService
from bot.transcription.models import ConfigInput, Prerequisites, RuntimeLimits, TestReceipt
from bot.web.v2_app import create_app


def test_runtime_imports_no_legacy_resolution():
    script = """
from unittest.mock import patch
with patch('dotenv.load_dotenv', return_value=False):
    import bot.main, bot.web.app, bot.services, bot.v2_runtime, bot.transcription.runner
import sys
for name in ('bot.config', 'bot.database.repository', 'bot.pipeline_resolver',
             'bot.model_picker', 'bot.capabilities', 'bot.providers', 'bot.adapters',
             'bot.web.routes_setup', 'bot.web.routes_pipeline',
             'bot.web.routes_providers', 'bot.web.setup_wizard'):
    assert name not in sys.modules, name
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_v1_database_schema_reader_is_migration_only():
    bot_root = Path(__file__).parents[2] / "bot"
    legacy_tables = ("authorized_users", "authorized_groups", "pipeline_profiles",
                     "pipeline_stages", "pipeline_stage_fallbacks", "provider_connections")
    readers = []
    for source in bot_root.rglob("*.py"):
        if source.name.startswith("._") or source.name == "migrate_v1.py":
            continue
        contents = source.read_text()
        if any(table in contents for table in legacy_tables):
            readers.append(source.relative_to(bot_root).as_posix())
    assert readers == []


async def test_removed_provider_and_pipeline_routes_are_not_registered(tmp_path):
    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    app = create_app(settings, services)
    paths = set(app.openapi()["paths"])
    assert "/admin/pipeline" not in paths
    assert "/admin/providers" not in paths
    assert "/api/providers" not in paths
    assert "/admin/diagnostics" in paths
    assert "/admin/access" in paths
    services.repo.close()


def test_environment_cannot_override_active_models_or_prompts(tmp_path, monkeypatch):
    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", "synthetic-hash")
    services.repo.put_credential_with_state("telegram", "synthetic-telegram-token", "telegram_credential_version")
    services.access.authorize("owner", 1001)
    services.repo.set_setup_state("current_bot_id", "2001")
    services.repo.set_setup_state("owner_bot_id", "2001")
    credential = services.repo.put_credential("openai", "synthetic-provider-key")
    config = ConfigurationService(
        services.repo, lambda: Prerequisites(web_admin=True, telegram_valid=True, owner=True)
    )
    draft = config.save_draft(None, ConfigInput(
        "openai", "two_stage", "stored-audio-model", "stored-text-model", credential,
        "stored system prompt", "Stored: {raw_text}", RuntimeLimits(),
    ))
    config.record_test(TestReceipt(
        "synthetic-receipt", draft.number, credential, "v2-1", True,
        ("stored-audio-model", "stored-text-model"),
        __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    ))
    config.activate(draft.number)
    monkeypatch.setenv("OPENAI_API_KEY", "environment-secret-must-not-win")
    monkeypatch.setenv("PROMPT_SYSTEM", "environment-prompt-must-not-win")
    active = services.configuration.active()
    assert active.values.audio_model == "stored-audio-model"
    assert active.values.text_model == "stored-text-model"
    assert active.values.system_prompt == "stored system prompt"
    services.repo.close()

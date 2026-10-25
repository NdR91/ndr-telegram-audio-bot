from datetime import datetime, timezone
from pathlib import Path

import pytest

from bot.database.secret_store import SecretStore
from bot.database.v2_repository import V2Repository
from bot.transcription.models import ConfigInput, RuntimeLimits


@pytest.fixture
def secret_store(tmp_path: Path) -> SecretStore:
    store = SecretStore(str(tmp_path / "master.key"))
    store.initialize()
    return store


@pytest.fixture
def repo(tmp_path: Path, secret_store: SecretStore) -> V2Repository:
    repository = V2Repository(tmp_path / "app-v2.sqlite3", secret_store)
    repository.initialize()
    yield repository
    repository.close()


@pytest.fixture
def config_values() -> ConfigInput:
    return ConfigInput(
        service="openai",
        mode="two_stage",
        audio_model="whisper-1",
        text_model="gpt-4o-mini",
        credential_version=1,
        system_prompt="Keep the transcript faithful.",
        refine_template="Refine minimally: {raw_text}",
        limits=RuntimeLimits(),
    )


@pytest.fixture
def ready_prerequisites():
    from bot.transcription.models import Prerequisites

    return Prerequisites(web_admin=True, telegram_valid=True, owner=True)


@pytest.fixture
def clock():
    return lambda: datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)

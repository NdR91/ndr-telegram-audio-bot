"""
Tests for the runtime application state model (A4).

Covers all six states, state evaluation, can_process_audio gating, and
edge cases (missing capabilities, exception safety, etc.).
"""

import json

import pytest

from bot.config_service import ConfigService
from bot.database import DatabaseManager
from bot.database.secret_store import SecretStore
from bot.state import AppState, StateChecker, StateInfo


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _make_db(tmp_path) -> DatabaseManager:
    db = DatabaseManager(str(tmp_path / "app.sqlite3"))
    db.initialize()
    return db


def _make_checker(tmp_path) -> StateChecker:
    db = _make_db(tmp_path)
    cs = ConfigService(db)
    return StateChecker(cs, db)


def _prime_telegram_token(checker: StateChecker) -> None:
    """Set up a valid telegram token in the DB."""
    checker._config_service.update_setting("telegram_token", "123:abc")


def _prime_admin_created(checker: StateChecker) -> None:
    """Mark the setup as completed."""
    checker._db.set_setup_state("admin_created", "true")


def _prime_provider(
    checker: StateChecker,
    name: str = "OpenAI",
    adapter: str = "openai-native",
    capabilities: dict | None = None,
    enabled: bool = True,
) -> int:
    """Add a provider connection and return its ID."""
    return checker._db.add_provider(
        name=name,
        adapter_type=adapter,
        credentials="sk-test",
        capabilities=capabilities,
        enabled=enabled,
    )


def _fully_ready(tmp_path) -> StateChecker:
    """Return a checker in the READY state."""
    checker = _make_checker(tmp_path)
    _prime_admin_created(checker)
    _prime_telegram_token(checker)
    _prime_provider(checker, capabilities={"transcription": True, "refinement": True})
    return checker


# ------------------------------------------------------------------
# AppState enum
# ------------------------------------------------------------------


def test_appstate_values():
    """All six states are defined and string-comparable."""
    assert AppState.SETUP_REQUIRED.value == "setup_required"
    assert AppState.TELEGRAM_MISSING.value == "telegram_missing"
    assert AppState.PROVIDER_MISSING.value == "provider_missing"
    assert AppState.PIPELINE_INVALID.value == "pipeline_invalid"
    assert AppState.READY.value == "ready"
    assert AppState.DEGRADED.value == "degraded"


# ------------------------------------------------------------------
# StateInfo
# ------------------------------------------------------------------


def test_state_info_contains_all_fields():
    info = StateInfo(
        state=AppState.READY,
        label="Pronto",
        description="Tutto ok.",
        next_action="Invia audio.",
    )
    assert info.state == AppState.READY
    assert info.label == "Pronto"
    assert info.description == "Tutto ok."
    assert info.next_action == "Invia audio."


# ------------------------------------------------------------------
# State evaluation — each state in priority order
# ------------------------------------------------------------------


def test_setup_required_when_admin_not_created(tmp_path):
    checker = _make_checker(tmp_path)
    # No setup_state at all
    info = checker.get_state()
    assert info.state == AppState.SETUP_REQUIRED
    assert "Setup richiesto" in info.label


def test_telegram_missing_when_token_not_set(tmp_path):
    checker = _make_checker(tmp_path)
    _prime_admin_created(checker)
    # No telegram token in DB
    info = checker.get_state()
    assert info.state == AppState.TELEGRAM_MISSING
    assert "Token Telegram mancante" in info.label


def test_provider_missing_when_no_providers(tmp_path):
    checker = _make_checker(tmp_path)
    _prime_admin_created(checker)
    _prime_telegram_token(checker)
    # No providers added
    info = checker.get_state()
    assert info.state == AppState.PROVIDER_MISSING
    assert "Provider AI mancante" in info.label


def test_provider_missing_when_all_providers_disabled(tmp_path):
    checker = _make_checker(tmp_path)
    _prime_admin_created(checker)
    _prime_telegram_token(checker)
    _prime_provider(checker, enabled=False, capabilities={"transcription": True})
    info = checker.get_state()
    assert info.state == AppState.PROVIDER_MISSING


def test_pipeline_invalid_when_no_transcription_capability(tmp_path):
    checker = _make_checker(tmp_path)
    _prime_admin_created(checker)
    _prime_telegram_token(checker)
    # Provider exists but only supports refinement
    _prime_provider(checker, capabilities={"refinement": True})
    info = checker.get_state()
    assert info.state == AppState.PIPELINE_INVALID
    assert "Pipeline non valida" in info.label


def test_ready_when_everything_configured(tmp_path):
    checker = _fully_ready(tmp_path)
    info = checker.get_state()
    assert info.state == AppState.READY
    assert "Pronto" in info.label


# ------------------------------------------------------------------
# Legacy providers without capabilities (backward compat)
# ------------------------------------------------------------------


def test_legacy_provider_without_capabilities_assumed_capable(tmp_path):
    """A provider without capabilities should be treated as transcription-capable."""
    checker = _make_checker(tmp_path)
    _prime_admin_created(checker)
    _prime_telegram_token(checker)
    _prime_provider(checker, capabilities=None)  # legacy: no capabilities
    info = checker.get_state()
    assert info.state == AppState.READY


# ------------------------------------------------------------------
# can_process_audio gating
# ------------------------------------------------------------------


def test_can_process_audio_ready(tmp_path):
    checker = _fully_ready(tmp_path)
    assert checker.can_process_audio() is True


def test_cannot_process_audio_setup_required(tmp_path):
    checker = _make_checker(tmp_path)
    assert checker.can_process_audio() is False


def test_cannot_process_audio_telegram_missing(tmp_path):
    checker = _make_checker(tmp_path)
    _prime_admin_created(checker)
    assert checker.can_process_audio() is False


def test_cannot_process_audio_provider_missing(tmp_path):
    checker = _make_checker(tmp_path)
    _prime_admin_created(checker)
    _prime_telegram_token(checker)
    assert checker.can_process_audio() is False


def test_cannot_process_audio_pipeline_invalid(tmp_path):
    checker = _make_checker(tmp_path)
    _prime_admin_created(checker)
    _prime_telegram_token(checker)
    _prime_provider(checker, capabilities={"refinement": True})
    assert checker.can_process_audio() is False


# ------------------------------------------------------------------
# can_process_audio returns True for degraded
# ------------------------------------------------------------------


def test_can_process_audio_degraded(tmp_path):
    """Future: when DEGRADED is returned, audio should still be allowed."""
    checker = _make_checker(tmp_path)
    # Force a scenario that could be degraded — for now just verify the
    # method logic allows it when state is forced to DEGRADED.
    checker.get_state = lambda: StateInfo(
        state=AppState.DEGRADED,
        label="Degradato",
        description="Funzionamento degradato.",
        next_action="Controlla i log.",
    )
    assert checker.can_process_audio() is True


# ------------------------------------------------------------------
# Exception safety
# ------------------------------------------------------------------


def test_get_state_returns_degraded_on_exception(tmp_path):
    """If the evaluation raises, get_state should gracefully return DEGRADED."""
    checker = _make_checker(tmp_path)
    # Break the DB connection to force an exception
    checker._db._conn = None  # type: ignore[union-attr]

    info = checker.get_state()
    assert info.state == AppState.DEGRADED
    assert "degradato" in info.label.lower() or "degradato" in info.description.lower()


# ------------------------------------------------------------------
# Provider with transcription capability only
# ------------------------------------------------------------------


def test_provider_with_only_transcription_is_ready(tmp_path):
    checker = _make_checker(tmp_path)
    _prime_admin_created(checker)
    _prime_telegram_token(checker)
    _prime_provider(checker, capabilities={"transcription": True})
    info = checker.get_state()
    assert info.state == AppState.READY


# ------------------------------------------------------------------
# Multiple providers — one bad, one good
# ------------------------------------------------------------------


def test_ready_when_one_provider_can_transcribe(tmp_path):
    checker = _make_checker(tmp_path)
    _prime_admin_created(checker)
    _prime_telegram_token(checker)
    _prime_provider(checker, name="Bad", capabilities={"refinement": True})
    _prime_provider(checker, name="Good", capabilities={"transcription": True})
    info = checker.get_state()
    assert info.state == AppState.READY

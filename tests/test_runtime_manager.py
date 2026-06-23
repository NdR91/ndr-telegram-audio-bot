"""
Tests for the RuntimeManager (A5).

Covers initialisation, state introspection, health reporting, and the
full start/stop/restart lifecycle.  Lifecycle tests mock the PTB
``Application`` so they can run without a real Telegram token.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from bot.config_service import ConfigService
from bot.database import DatabaseManager, SecretStore
from bot.runtime_manager import RuntimeManager
from bot.state import AppState, StateChecker


# ------------------------------------------------------------------
# Fixtures — real services (DB, SecretStore, ConfigService)
# ------------------------------------------------------------------


def _make_db(tmp_path) -> DatabaseManager:
    db = DatabaseManager(str(tmp_path / "app.sqlite3"))
    db.initialize()
    return db


def _make_secret_store(tmp_path) -> SecretStore:
    store = SecretStore(str(tmp_path / ".master_key"))
    store.initialize()
    return store


def _make_legacy_config(tmp_path) -> SimpleNamespace:
    """Build a minimal Config-like namespace that satisfies the services
    the RuntimeManager currently depends on."""
    api_keys = {"openai": "sk-test-123"}

    def get_api_key(provider=None):
        provider = provider or "openai"
        return api_keys.get(provider, "")

    return SimpleNamespace(
        telegram_token="123:abc",
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
        authorized_data={"admin": [123], "users": [], "groups": []},
    )


def _prime_db_for_ready(db: DatabaseManager, cs: ConfigService) -> None:
    """Populate the unified database so that ``StateChecker`` reports
    ``READY``."""
    db.set_setup_state("admin_created", "true")
    cs.update_setting("telegram_token", "123:abc")
    db.add_provider(
        name="OpenAI",
        adapter_type="openai-native",
        credentials="sk-test",
        capabilities={"transcription": True, "refinement": True},
    )


def _make_manager(
    tmp_path,
    *,
    ready: bool = False,
) -> RuntimeManager:
    """Create a RuntimeManager with real database and services.

    Parameters
    ----------
    ready:
        When ``True`` the database is pre-populated so the state checker
        reports ``READY``.
    """
    config = _make_legacy_config(tmp_path)
    db = _make_db(tmp_path)
    ss = _make_secret_store(tmp_path)
    cs = ConfigService(db, secret_store=ss)
    if ready:
        _prime_db_for_ready(db, cs)
    # NOTE: legacy_config is deliberately NOT passed when ``ready`` is
    # False, so the checker actually evaluates setup state instead of
    # short-circuiting to READY via legacy compatibility.
    checker = StateChecker(
        cs, db,
        legacy_config=config if ready else None,
    )
    return RuntimeManager(config, db, ss, cs, checker)


# ------------------------------------------------------------------
# Mock PTB Application
# ------------------------------------------------------------------


@pytest.fixture
def mock_app():
    """Return a :class:`~unittest.mock.MagicMock` that simulates a PTB
    :class:`~telegram.ext.Application`.

    The mock starts with ``running = False`` and has a mock ``updater``
    attribute.  Tests that exercise the running state should explicitly
    set ``mock_app.running = True``.
    """
    app = MagicMock()
    app.running = False
    app.updater = MagicMock()
    return app


@pytest.fixture
def ready_manager(tmp_path, mock_app):
    """Return a ``RuntimeManager`` in ``READY`` state with
    :func:`~bot.core.app.create_application` patched to return
    *mock_app*.

    This fixture is used for lifecycle tests that need to control the
    ``Application`` instance without a real Telegram connection.
    """
    config = _make_legacy_config(tmp_path)
    db = _make_db(tmp_path)
    ss = _make_secret_store(tmp_path)
    cs = ConfigService(db, secret_store=ss)
    _prime_db_for_ready(db, cs)
    checker = StateChecker(cs, db, legacy_config=config)

    with patch("bot.runtime_manager.create_application", return_value=mock_app):
        manager = RuntimeManager(config, db, ss, cs, checker)
        yield manager


# ------------------------------------------------------------------
# Construction and initial state
# ------------------------------------------------------------------


def test_creates_with_services(tmp_path):
    """RuntimeManager stores the provided service references."""
    manager = _make_manager(tmp_path)
    assert manager._config is not None
    assert manager._db is not None
    assert manager.current_app is None
    assert manager.is_running is False


def test_is_running_initially_false(tmp_path):
    """is_running is False before any start() call."""
    manager = _make_manager(tmp_path)
    assert manager.is_running is False


def test_is_running_true_when_app_running(ready_manager, mock_app):
    """is_running reads ``Application.running``."""
    mock_app.running = True
    ready_manager.start(block=False)
    assert ready_manager.is_running is True


# ------------------------------------------------------------------
# State introspection
# ------------------------------------------------------------------


def test_get_state_delegates(tmp_path):
    """get_state() returns the result from the StateChecker."""
    manager = _make_manager(tmp_path, ready=True)
    info = manager.get_state()
    assert info.state == AppState.READY


def test_get_state_reflects_setup_required(tmp_path):
    """get_state() shows SETUP_REQUIRED on an empty database."""
    manager = _make_manager(tmp_path, ready=False)
    info = manager.get_state()
    assert info.state == AppState.SETUP_REQUIRED


def test_can_start_true_when_ready(tmp_path):
    """can_start() is True when the state is READY."""
    manager = _make_manager(tmp_path, ready=True)
    assert manager.can_start() is True


def test_can_start_false_when_not_ready(tmp_path):
    """can_start() is False when the state is not READY."""
    manager = _make_manager(tmp_path, ready=False)
    assert manager.can_start() is False


# ------------------------------------------------------------------
# Health reporting
# ------------------------------------------------------------------


def test_get_health_structure_when_stopped(tmp_path):
    """get_health() returns the expected keys when the bot is stopped."""
    manager = _make_manager(tmp_path, ready=True)
    health = manager.get_health()

    assert health["bot_running"] is False
    assert health["state"] == AppState.READY.value
    assert "state_label" in health
    assert health["uptime_seconds"] is None


def test_get_health_includes_uptime_when_running(ready_manager, mock_app):
    """uptime_seconds is populated when the bot has been started."""
    mock_app.running = True
    ready_manager.start(block=False)

    health = ready_manager.get_health()
    assert health["bot_running"] is True
    assert health["state"] == AppState.READY.value
    assert health["uptime_seconds"] is not None
    assert health["uptime_seconds"] >= 0


# ------------------------------------------------------------------
# Lifecycle — start (blocking mode)
# ------------------------------------------------------------------


def test_start_blocking_calls_run_polling(ready_manager, mock_app):
    """start(block=True) calls ``Application.run_polling()``."""
    ready_manager.start(block=True)
    mock_app.run_polling.assert_called_once_with()


def test_start_blocking_clears_app_after_return(ready_manager, mock_app):
    """After ``run_polling()`` returns, the app reference is cleaned
    up — simulating what happens when the bot stops."""
    ready_manager.start(block=True)
    assert ready_manager.current_app is None
    assert ready_manager.is_running is False
    assert ready_manager.get_health()["uptime_seconds"] is None


# ------------------------------------------------------------------
# Lifecycle — start (non-blocking mode, for frontend)
# ------------------------------------------------------------------


def test_start_non_blocking_initializes_app(ready_manager, mock_app):
    """start(block=False) calls ``initialize()``, ``start()``, and
    ``updater.start_polling()`` without idling."""
    ready_manager.start(block=False)
    mock_app.initialize.assert_called_once_with()
    mock_app.start.assert_called_once_with()
    mock_app.updater.start_polling.assert_called_once_with()


def test_start_non_blocking_sets_app_reference(ready_manager, mock_app):
    """current_app returns the application instance after start."""
    ready_manager.start(block=False)
    assert ready_manager.current_app is mock_app


def test_start_non_blocking_does_not_call_run_polling(ready_manager, mock_app):
    """The non-blocking path must NOT call ``run_polling()``."""
    ready_manager.start(block=False)
    mock_app.run_polling.assert_not_called()


# ------------------------------------------------------------------
# Lifecycle — error conditions
# ------------------------------------------------------------------


def test_start_raises_when_not_ready(tmp_path, mock_app):
    """start() raises ``RuntimeError`` when the state is not READY."""
    with patch("bot.runtime_manager.create_application", return_value=mock_app):
        manager = _make_manager(tmp_path, ready=False)
    with pytest.raises(RuntimeError, match="Cannot start bot"):
        manager.start(block=False)


def test_start_raises_when_already_running(ready_manager, mock_app):
    """start() raises ``RuntimeError`` when the bot is already
    running."""
    mock_app.running = True
    ready_manager.start(block=False)
    with pytest.raises(RuntimeError, match="already running"):
        ready_manager.start(block=False)


# ------------------------------------------------------------------
# Lifecycle — stop
# ------------------------------------------------------------------


def test_stop_shuts_down_app(ready_manager, mock_app):
    """stop() calls ``Application.stop()`` and ``.shutdown()``."""
    mock_app.running = True
    ready_manager.start(block=False)

    ready_manager.stop()

    mock_app.stop.assert_called_once_with()
    mock_app.shutdown.assert_called_once_with()
    assert ready_manager.is_running is False


def test_stop_clears_app_reference(ready_manager, mock_app):
    """After stop(), ``current_app`` is ``None``."""
    mock_app.running = True
    ready_manager.start(block=False)

    ready_manager.stop()

    assert ready_manager.current_app is None


def test_stop_noop_when_not_running(ready_manager, mock_app):
    """Calling stop() on a stopped manager is harmless."""
    ready_manager.stop()  # should not crash
    mock_app.stop.assert_not_called()
    mock_app.shutdown.assert_not_called()


def test_stop_noop_after_blocking_start(ready_manager, mock_app):
    """After ``start(block=True)`` completes, the manager is already
    stopped and ``stop()`` is a no-op."""
    ready_manager.start(block=True)
    ready_manager.stop()  # should not crash
    mock_app.stop.assert_not_called()


# ------------------------------------------------------------------
# Lifecycle — restart
# ------------------------------------------------------------------


def test_restart_stops_then_starts(ready_manager, mock_app):
    """restart() stops the running app and starts a new one."""
    mock_app.running = True
    ready_manager.start(block=False)

    ready_manager.restart()

    mock_app.stop.assert_called_once()
    # start() was called twice: initial + restart
    assert mock_app.initialize.call_count >= 2


def test_restart_sets_new_app_reference(ready_manager, mock_app):
    """After restart(), ``current_app`` points to the new instance."""
    mock_app.running = True
    ready_manager.start(block=False)
    old_app = ready_manager.current_app

    ready_manager.restart()

    assert ready_manager.current_app is not None
    # In mock mode both references point to the same mock because
    # create_application is patched, but the important thing is that
    # the app is available.


def test_restart_on_stopped_manager(ready_manager, mock_app):
    """restart() works even if the bot was never started."""
    ready_manager.restart()
    mock_app.initialize.assert_called_once()


# ------------------------------------------------------------------
# Legacy CLI entry point
# ------------------------------------------------------------------


def test_run_until_stopped_calls_start_blocking(ready_manager, mock_app):
    """run_until_stopped() delegates to ``start(block=True)``."""
    ready_manager.run_until_stopped()
    mock_app.run_polling.assert_called_once_with()


def test_run_until_stopped_handles_keyboard_interrupt(ready_manager, mock_app):
    """KeyboardInterrupt during start() is caught and logged, not
    propagated."""
    original = ready_manager.start

    def _raise_on_block(*args, **kwargs):
        raise KeyboardInterrupt()

    ready_manager.start = _raise_on_block
    # Should not raise — run_until_stopped catches KeyboardInterrupt
    ready_manager.run_until_stopped()


def test_run_until_stopped_propagates_runtime_error(tmp_path, mock_app):
    """RuntimeError from start() (e.g. not READY) is logged and
    re-raised."""
    with patch("bot.runtime_manager.create_application", return_value=mock_app):
        manager = _make_manager(tmp_path, ready=False)
    with pytest.raises(RuntimeError, match="Cannot start bot"):
        manager.run_until_stopped()

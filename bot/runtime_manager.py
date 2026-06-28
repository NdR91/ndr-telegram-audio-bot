"""
Runtime manager — Telegram bot lifecycle management.

Separates the Telegram bot lifecycle from the rest of the application so
that the frontend (Phase 2) can start, stop, and restart the bot
independently.

Responsibilities
----------------
- Start, stop, and restart Telegram polling.
- Verify prerequisites before starting (state must be READY).
- Expose health and state information for dashboards and health checks.
- Support both blocking (legacy CLI) and non-blocking (frontend) modes.

Thread safety
-------------
The ``_app`` reference is protected by a lock so that ``start()`` and
``stop()`` can be called from different threads.  This is relevant when
the web frontend calls the manager from a request handler.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Dict, Optional

from telegram.ext import Application

from bot.config import Config
from bot.config_service import ConfigService
from bot.core.app import create_application
from bot.database import DatabaseManager, SecretStore
from bot.state import AppState, StateChecker, StateInfo

logger = logging.getLogger(__name__)


class RuntimeManager:
    """Manages the Telegram bot lifecycle.

    Parameters
    ----------
    config:
        Optional legacy configuration object.  When ``None``, all
        configuration is resolved from the database via ``ConfigService``.
    db_manager:
        Initialised :class:`~bot.database.DatabaseManager`.
    secret_store:
        Optional :class:`~bot.database.secret_store.SecretStore`.
    config_service:
        Initialised :class:`~bot.config_service.ConfigService`.
    state_checker:
        Initialised :class:`~bot.state.StateChecker`.
    """

    def __init__(
        self,
        config: Config | None,
        db_manager: DatabaseManager,
        secret_store: SecretStore | None,
        config_service: ConfigService,
        state_checker: StateChecker,
    ) -> None:
        self._config = config
        self._db = db_manager
        self._secret_store = secret_store
        self._config_service = config_service
        self._state_checker = state_checker

        # Protected state
        self._lock = threading.Lock()
        self._app: Application | None = None
        self._start_time: float | None = None

    # ------------------------------------------------------------------
    # Public API — lifecycle
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """``True`` while the Telegram bot application is active and
        polling."""
        app = self._app
        if app is None:
            return False
        try:
            return app.running
        except Exception:
            return False

    @property
    def current_app(self) -> Application | None:
        """Return the active :class:`telegram.ext.Application`, or
        ``None`` if the bot is not running.

        This is primarily intended for tests and administrative commands
        that need direct access to the application instance (e.g.
        inspecting handler registrations).
        """
        return self._app

    def start(self, block: bool = True) -> None:
        """Start the Telegram bot.

        Parameters
        ----------
        block:
            When ``True`` (default), blocks the calling thread until the
            bot is stopped (legacy CLI mode).  When ``False``, starts
            polling in the background and returns immediately (frontend
            mode).

        Raises
        ------
        RuntimeError:
            If the bot is already running or the application state is
            not READY.
        """
        with self._lock:
            if self.is_running:
                raise RuntimeError("Telegram bot is already running")

            state = self._state_checker.get_state()
            if state.state != AppState.READY:
                raise RuntimeError(
                    f"Cannot start bot: {state.description} "
                    f"({state.next_action})"
                )

            self._app = self._build_app()
            self._start_time = time.monotonic()

        logger.info("RuntimeManager starting Telegram bot (block=%s)", block)

        # The blocking path delegates to run_polling() which handles
        # signals and blocks until shutdown.  The non-blocking path
        # initialises and starts the application without idling.
        if block:
            try:
                self._app.run_polling()
            finally:
                with self._lock:
                    self._app = None
                    self._start_time = None
        else:
            # Non-blocking — schedule async start in the current event
            # loop (used by the web frontend).
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # No running loop; fall back to synchronous mode.  This
                # can happen in test environments.
                try:
                    self._app.initialize()
                    self._app.start()
                    self._app.updater.start_polling()
                except Exception:
                    logger.exception(
                        "Failed to start bot in non-blocking (sync fallback) mode"
                    )
                    with self._lock:
                        self._app = None
                        self._start_time = None
                    raise
            else:
                loop.create_task(self._start_async_task())

    async def start_async(self) -> None:
        """Start the Telegram bot asynchronously.

        This is the primary method for the web frontend.  Unlike
        :meth:`start` with ``block=False``, this method is a coroutine
        that awaits the PTB ``initialize()``, ``start()``, and
        ``updater.start_polling()`` calls directly.

        Raises
        ------
        RuntimeError:
            If the bot is already running or the application state is
            not READY.
        """
        with self._lock:
            if self.is_running:
                raise RuntimeError("Telegram bot is already running")

            state = self._state_checker.get_state()
            if state.state != AppState.READY:
                raise RuntimeError(
                    f"Cannot start bot: {state.description} "
                    f"({state.next_action})"
                )

            self._app = self._build_app()
            self._start_time = time.monotonic()

        logger.info("RuntimeManager starting Telegram bot (async)")
        try:
            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling()
        except Exception:
            logger.exception("Failed to start bot in async mode")
            with self._lock:
                self._app = None
                self._start_time = None
            raise

    async def _start_async_task(self) -> None:
        """Internal wrapper that runs :meth:`start_async` and logs
        unhandled exceptions so the event loop doesn't swallow them."""
        try:
            await self.start_async()
        except RuntimeError:
            # start_async already raised; nothing extra to log.
            raise
        except Exception:
            logger.exception("Async bot start task failed unexpectedly")

    def stop(self) -> None:
        """Stop the Telegram bot gracefully.

        If the bot is not running this is a no-op.

        For the async case (web frontend), prefer :meth:`stop_async`
        which properly awaits the PTB shutdown coroutines.
        """
        with self._lock:
            app = self._app
            if app is None:
                logger.debug("RuntimeManager.stop() called but bot is not running")
                return
            self._app = None
            self._start_time = None

        logger.info("RuntimeManager stopping Telegram bot")

        if app.running:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # No running loop — call sync (mostly for tests).
                try:
                    app.stop()
                    app.shutdown()
                except Exception:
                    logger.exception("Error during bot shutdown (sync)")
            else:
                loop.create_task(self._stop_async_task(app))

    async def stop_async(self) -> None:
        """Stop the Telegram bot asynchronously.

        Properly awaits the PTB ``stop()`` and ``shutdown()`` coroutines.
        """
        with self._lock:
            app = self._app
            if app is None:
                logger.debug("RuntimeManager.stop_async() called but bot is not running")
                return
            self._app = None
            self._start_time = None

        logger.info("RuntimeManager stopping Telegram bot (async)")

        if app.running:
            try:
                await app.stop()
                await app.shutdown()
            except Exception:
                logger.exception("Error during bot shutdown (async)")

    async def _stop_async_task(self, app: Application) -> None:
        """Internal wrapper for :meth:`stop_async`."""
        try:
            await self._stop_async_inner(app)
        except Exception:
            logger.exception("Async bot stop task failed unexpectedly")

    async def _stop_async_inner(self, app: Application) -> None:
        """Stop the given *app* without touching ``self._lock``."""
        if app.running:
            await app.stop()
            await app.shutdown()

    def restart(self) -> None:
        """Stop the bot (if running) and start it again.

        Raises
        ------
        RuntimeError:
            If the application state has changed and is no longer READY.
        """
        logger.info("RuntimeManager restarting Telegram bot")
        was_running = self.is_running
        if was_running:
            self.stop()
        self.start(block=False)

    def run_until_stopped(self) -> None:
        """Legacy CLI entry point.

        Starts the bot in blocking mode and handles ``KeyboardInterrupt``
        gracefully.  This is the entry point used by ``bot/main.py``
        during the migration period.

        Behaviour is identical to the current ``main()`` — the bot polls
        indefinitely until the process receives SIGINT/SIGTERM.
        """
        try:
            self.start(block=True)
        except RuntimeError as exc:
            logger.error("Cannot start bot: %s", exc)
            raise
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")

    # ------------------------------------------------------------------
    # Public API — introspection
    # ------------------------------------------------------------------

    def get_state(self) -> StateInfo:
        """Return the current application :class:`~bot.state.StateInfo`."""
        return self._state_checker.get_state()

    def get_health(self) -> Dict[str, Any]:
        """Return a health-report dictionary.

        Keys
        ----
        bot_running:
            ``True`` when the bot application is active and polling.
        state:
            Machine-readable :class:`AppState` value.
        state_label:
            Human-readable Italian label for the current state.
        uptime_seconds:
            Seconds since the bot was started, or ``None``.
        """
        state = self.get_state()
        uptime: float | None = None
        if self._start_time is not None:
            uptime = time.monotonic() - self._start_time

        return {
            "bot_running": self.is_running,
            "state": state.state.value,
            "state_label": state.label,
            "uptime_seconds": uptime,
        }

    def can_start(self) -> bool:
        """Return ``True`` when the bot can be started (state is READY).

        This is a convenience over calling ``get_state().state`` directly
        and is used by the frontend to decide whether to show the "Start
        bot" button.
        """
        return self._state_checker.get_state().state == AppState.READY

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_app(self) -> Application:
        """Build a new :class:`telegram.ext.Application` from the current
        configuration.

        Uses the same :func:`~bot.core.app.create_application` factory
        that the legacy code path uses, ensuring consistency between
        legacy and frontend-managed modes.
        """
        token = ""
        if self._config is not None:
            token = getattr(self._config, "telegram_token", "")

        # When token is empty (relaxed mode), resolve from database.
        if not token and self._config_service is not None:
            token = self._resolve_token_from_db()

        return create_application(
            token,
            self._config,
            database_manager=self._db,
            secret_store=self._secret_store,
            config_service=self._config_service,
            state_checker=self._state_checker,
        )

    def _resolve_token_from_db(self) -> str:
        """Decrypt and return the Telegram token stored in the database."""
        encrypted = self._config_service._db.get_setting("telegram_token")
        if not encrypted:
            return ""
        if self._secret_store is not None and self._secret_store.key_available:
            try:
                return self._secret_store.decrypt(encrypted)
            except Exception:
                logger.exception("Failed to decrypt telegram_token from database")
        return ""

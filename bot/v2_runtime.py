"""One serialized Telegram poller for V2 setup and informational commands."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable

from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters

from bot.handlers.v2_audio import handle_audio
from bot.rate_limiter import RATE_LIMITER_CLEANUP_INTERVAL_SECONDS, RateLimiter
from bot.transcription.models import RuntimeLimits
from bot.ui.delivery import TelegramDeliveryAdapter

from bot.database.v2_repository import V2Repository
from bot.handlers import v2_admin
from bot.handlers.pairing import start_or_pair, whoami

logger = logging.getLogger(__name__)


async def _await_until(awaitable, deadline: float, description: str) -> bool:
    """Run one shutdown step without waiting past the shared deadline.

    A task that suppresses cancellation cannot be forcibly stopped by asyncio.
    In that case it remains best-effort cleanup and is reported to the logs.
    """
    task = asyncio.ensure_future(awaitable)
    remaining = max(0.0, deadline - asyncio.get_running_loop().time())
    done, _ = await asyncio.wait((task,), timeout=remaining)
    if task not in done:
        task.cancel()
        task.add_done_callback(_consume_task_result)
        logger.warning("Telegram %s exceeded the shutdown deadline; continuing best-effort", description)
        return False
    try:
        task.result()
    except asyncio.CancelledError:
        logger.warning("Telegram %s was cancelled during shutdown", description)
        return False
    except Exception:
        logger.exception("Telegram %s failed during shutdown", description)
        return False
    return True


def _consume_task_result(task: asyncio.Future) -> None:
    try:
        task.result()
    except BaseException:
        pass


async def _cleanup_rate_limiter(limiter: RateLimiter) -> None:
    """Bound retained per-user admission timestamps during the poller lifetime."""
    while True:
        await asyncio.sleep(RATE_LIMITER_CLEANUP_INTERVAL_SECONDS)
        try:
            await limiter.cleanup_expired_async()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("V2 rate limiter cleanup failed")


@dataclass(frozen=True)
class TelegramIdentity:
    id: int
    username: str


class RuntimeManager:
    def __init__(self, repo: V2Repository, application_factory: Callable[[str], Application] | None = None,
                 shutdown_seconds: int = 30):
        self.repo = repo
        self.factory = application_factory or self._build_application
        self.shutdown_seconds = shutdown_seconds
        self._app: Application | None = None
        self._lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._started = False
        self._lifecycle_generation = 0
        self._shutdown_deadline = 0.0

    @staticmethod
    def _build_application(token: str) -> Application:
        app = ApplicationBuilder().token(token).concurrent_updates(True).build()
        app.add_handler(CommandHandler("start", start_or_pair))
        app.add_handler(CommandHandler("whoami", whoami))
        app.add_handler(CommandHandler("help", v2_admin.help_command))
        app.add_handler(CommandHandler("adduser", v2_admin.adduser))
        app.add_handler(CommandHandler("removeuser", v2_admin.removeuser))
        app.add_handler(CommandHandler("addgroup", v2_admin.addgroup))
        app.add_handler(CommandHandler("removegroup", v2_admin.removegroup))
        return app

    async def connect(self, token: str) -> TelegramIdentity:
        async with self._connect_lock:
            candidate = self.factory(token)
            try:
                await candidate.initialize()
                bot = await candidate.bot.get_me()
                identity = TelegramIdentity(int(bot.id), bot.username or "")
            finally:
                if candidate is not self._app:
                    try:
                        await candidate.shutdown()
                    except Exception:
                        pass
            old = self.repo.get_setup_state("current_bot_id")
            # Persist first so an encryption failure leaves the existing
            # poller and credential intact, then replace the sole poller.
            self.repo.put_credential_with_state(
                "telegram", token, "telegram_credential_version",
                {"current_bot_id": str(identity.id), "current_bot_username": identity.username},
            )
            await self.stop_async()
            if old and old != str(identity.id):
                with self.repo.transaction() as connection:
                    connection.execute("DELETE FROM pairing_invites WHERE confirmed_at IS NULL")
            return identity

    async def start_async(self) -> None:
        generation = self._lifecycle_generation
        async with self._lock:
            if self._started:
                return
            version = self.repo.get_setup_state("telegram_credential_version")
            try:
                token = self.repo.read_credential("telegram", int(version)) if version else None
            except Exception:
                # Do not let unavailable encryption take down the web setup.
                logger.warning("Telegram polling skipped because the saved credential is unavailable")
                return
            if token is None:
                return
            app = self.factory(token)
            initialized = started = False
            try:
                await app.initialize()
                initialized = True
                if generation != self._lifecycle_generation:
                    await self._cleanup_stale_start(app)
                    return
                app.bot_data["v2_services"] = getattr(self, "services", None)
                self._configure_audio_runtime(app)
                await app.start()
                started = True
                if generation != self._lifecycle_generation:
                    await self._cleanup_stale_start(app)
                    return
                if app.updater is None:
                    raise RuntimeError("Telegram polling is unavailable")
                await app.updater.start_polling()
                limiter = app.bot_data.get("rate_limiter")
                if limiter is not None:
                    app.bot_data["rate_limiter_cleanup_task"] = asyncio.create_task(
                        _cleanup_rate_limiter(limiter), name="v2-rate-limiter-cleanup"
                    )
                if generation != self._lifecycle_generation:
                    await self._cleanup_stale_start(app)
                    return
                self._app, self._started = app, True
            except BaseException:
                if app.updater is not None and app.updater.running:
                    try:
                        await app.updater.stop()
                    except Exception:
                        logger.exception("Failed to stop Telegram updater after startup failure")
                if started and app.running:
                    try:
                        await app.stop()
                    except Exception:
                        logger.exception("Failed to stop Telegram application after startup failure")
                if initialized:
                    try:
                        await app.shutdown()
                    except Exception:
                        logger.exception("Failed to shut down Telegram application after startup failure")
                raise

    async def _cleanup_stale_start(self, app: Application) -> None:
        """Best-effort cleanup for a startup invalidated by a stop request."""
        deadline = self._shutdown_deadline
        await self._stop_rate_limiter_cleanup(app, deadline)
        if app.updater is not None and app.updater.running:
            await _await_until(app.updater.stop(), deadline, "late poller cleanup")
        if app.running:
            await _await_until(app.stop(), deadline, "late application cleanup")
        await _await_until(app.shutdown(), deadline, "late application shutdown")

    @staticmethod
    async def _stop_rate_limiter_cleanup(app: Application, deadline: float) -> None:
        task = app.bot_data.pop("rate_limiter_cleanup_task", None)
        if task is None:
            return
        if task.done():
            _consume_task_result(task)
            return
        task.cancel()
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        done, _ = await asyncio.wait((task,), timeout=remaining)
        if not done:
            task.add_done_callback(_consume_task_result)
            logger.warning("V2 rate limiter cleanup exceeded the shutdown deadline")
            return
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("V2 rate limiter cleanup stopped with an error")

    def _configure_audio_runtime(self, app: Application) -> None:
        services = getattr(self, "services", None)
        if services is None:
            return
        active = services.configuration.active()
        limits = active.values.limits if active is not None else RuntimeLimits()
        app.bot_data["rate_limiter"] = RateLimiter(
            max_per_user=limits.max_per_user,
            cooldown=limits.cooldown_seconds,
            max_global=limits.max_concurrent_global,
            max_file_size_mb=limits.max_file_size_mb,
            queue_enabled=limits.queue_enabled,
            max_queue_size=limits.max_queue_size,
            max_queued_per_user=limits.max_queued_per_user,
        )
        app.bot_data["delivery_adapter"] = TelegramDeliveryAdapter()
        app.bot_data["audio_tmp_dir"] = services.settings.data_dir / "tmp"
        app.add_handler(MessageHandler(
            filters.VOICE | filters.AUDIO | filters.Document.AUDIO,
            handle_audio,
        ))

    async def stop_async(self) -> None:
        loop = asyncio.get_running_loop()
        # Bound lock acquisition too, so a concurrent lifecycle operation
        # cannot make shutdown wait beyond the configured total budget.
        services = getattr(self, "services", None)
        active = services.configuration.active() if services is not None else None
        initial_budget = max(
            0.0,
            float(active.values.limits.shutdown_seconds if active is not None else self.shutdown_seconds),
        )
        deadline = loop.time() + initial_budget
        self._lifecycle_generation += 1
        self._shutdown_deadline = deadline
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=max(0.0, deadline - loop.time()))
        except asyncio.TimeoutError:
            logger.warning("Telegram runtime shutdown could not acquire its lifecycle lock before deadline")
            return
        try:
            app, self._app = self._app, None
            if app is None:
                self._started = False
                return
            try:
                limiter = app.bot_data.get("rate_limiter")
                shutdown_seconds = initial_budget
                app.bot_data["v2_shutdown_deadline"] = deadline
                cleanup_reserve = min(shutdown_seconds * 0.10, max(0.0, deadline - loop.time()))
                stop_reserve = min(shutdown_seconds * 0.10, max(0.0, deadline - loop.time() - cleanup_reserve))
                work_deadline = max(loop.time(), deadline - cleanup_reserve - stop_reserve)
                cleanup_deadline = max(loop.time(), deadline - stop_reserve)
                await self._stop_rate_limiter_cleanup(app, cleanup_deadline)
                if limiter is not None:
                    await _await_until(limiter.stop_admission(), work_deadline, "admission stop")
                if app.updater and app.updater.running:
                    await _await_until(app.updater.stop(), work_deadline, "poller stop")
                if limiter is not None:
                    drained = await limiter.drain(max(0.0, work_deadline - loop.time()))
                    if not drained:
                        logger.warning("Telegram audio work exceeded the shutdown drain deadline")
                audio_tasks = tuple(
                    task for task in app.bot_data.get("v2_audio_tasks", ())
                    if task is not asyncio.current_task() and not task.done()
                )
                if audio_tasks:
                    remaining = max(0.0, work_deadline - loop.time())
                    _, pending = await asyncio.wait(audio_tasks, timeout=remaining)
                    if pending:
                        logger.warning(
                            "Cancelling %s Telegram audio task(s) at the shutdown deadline",
                            len(pending),
                        )
                        for task in pending:
                            task.cancel()
                        _, still_pending = await asyncio.wait(
                            pending, timeout=max(0.0, cleanup_deadline - loop.time())
                        )
                        for task in still_pending:
                            task.add_done_callback(_consume_task_result)
                        if still_pending:
                            logger.warning(
                                "%s Telegram audio task(s) ignored cancellation; their cleanup is best-effort",
                                len(still_pending),
                            )
                if limiter is not None and not await limiter.drain(0):
                    logger.warning("Telegram audio admission slots remain after task cancellation")
                if app.running:
                    await _await_until(app.stop(), max(loop.time(), deadline - stop_reserve), "application stop")
            finally:
                await _await_until(app.shutdown(), deadline, "application shutdown")
                self._started = False
        finally:
            self._lock.release()

    @property
    def is_running(self) -> bool:
        return self._started

    def get_health(self) -> dict:
        services = getattr(self, "services", None)
        return {
            "telegram_running": self.is_running,
            "setup_only": services is None,
            "audio_enabled": services is not None,
        }

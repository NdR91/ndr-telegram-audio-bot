import asyncio
from types import SimpleNamespace

from bot.v2_runtime import RuntimeManager


class FakeApplication:
    def __init__(self, token, events, fail_poll=False):
        self.token, self.events = token, events
        self.fail_poll = fail_poll
        self.running = False
        self.bot_data = {}
        self.updater = SimpleNamespace(running=False)
        self.updater.start_polling = self._start_polling
        self.updater.stop = self._stop_polling
        self.bot = SimpleNamespace(get_me=self._get_me)

    async def initialize(self):
        self.events.append(("initialize", self.token))

    async def start(self):
        self.running = True
        self.events.append(("start", self.token))

    async def stop(self):
        self.running = False
        self.events.append(("stop", self.token))

    async def shutdown(self):
        self.events.append(("shutdown", self.token))

    async def _start_polling(self):
        self.updater.running = True
        self.events.append(("poll", self.token))
        if self.fail_poll:
            raise RuntimeError("synthetic polling failure")

    async def _stop_polling(self):
        self.updater.running = False
        self.events.append(("unpoll", self.token))

    async def _get_me(self):
        return SimpleNamespace(id=1234, username="setupbot")


def test_concurrent_starts_create_one_poller(repo):
    events = []
    apps = []

    def factory(token):
        app = FakeApplication(token, events)
        apps.append(app)
        return app

    repo.put_credential_with_state("telegram", "synthetic-token", "telegram_credential_version")
    manager = RuntimeManager(repo, factory)

    async def run():
        await asyncio.gather(manager.start_async(), manager.start_async())

    asyncio.run(run())
    assert len(apps) == 1
    assert events.count(("poll", "synthetic-token")) == 1


def test_connect_verifies_then_replaces_single_poller(repo):
    events = []
    apps = []

    def factory(token):
        app = FakeApplication(token, events)
        apps.append(app)
        return app

    repo.put_credential_with_state("telegram", "old-token", "telegram_credential_version")
    manager = RuntimeManager(repo, factory)

    async def run():
        await manager.start_async()
        identity = await manager.connect("new-synthetic-token")
        await manager.start_async()
        return identity

    identity = asyncio.run(run())
    assert identity.id == 1234
    assert repo.get_setup_state("current_bot_username") == "setupbot"
    assert repo.read_credential("telegram", int(repo.get_setup_state("telegram_credential_version"))) == "new-synthetic-token"
    assert events.count(("poll", "old-token")) == 1
    assert events.count(("unpoll", "old-token")) == 1
    assert events.count(("poll", "new-synthetic-token")) == 1


def test_setup_commands_never_register_audio_runner():
    from telegram.ext import CommandHandler, MessageHandler

    from bot.v2_runtime import RuntimeManager

    app = RuntimeManager._build_application("123:synthetic-token")
    handlers = [handler for group in app.handlers.values() for handler in group]
    commands = {command for handler in handlers if isinstance(handler, CommandHandler) for command in handler.commands}
    assert commands == {"start", "whoami", "help", "adduser", "removeuser", "addgroup", "removegroup"}
    assert not any(isinstance(handler, MessageHandler) for handler in handlers)


async def test_access_commands_are_registered_and_owner_only_in_private(repo):
    from telegram.ext import CommandHandler

    from bot.access import AccessService
    from bot.handlers.v2_admin import adduser
    from bot.v2_runtime import RuntimeManager

    app = RuntimeManager._build_application("123:synthetic-token")
    handlers = [handler for group in app.handlers.values() for handler in group]
    callbacks = {
        next(iter(handler.commands)): handler.callback
        for handler in handlers if isinstance(handler, CommandHandler)
    }
    assert set(callbacks) >= {"help", "adduser", "removeuser", "addgroup", "removegroup"}

    access = AccessService(repo)
    repo.set_setup_state("current_bot_id", "1234")
    access.authorize("owner", 10)
    assert access.has_owner()
    replies = []

    async def reply_text(text):
        replies.append(text)

    async def invoke(user_id, chat_type):
        update = SimpleNamespace(
            effective_message=SimpleNamespace(reply_text=reply_text),
            effective_user=SimpleNamespace(id=user_id),
            effective_chat=SimpleNamespace(type=chat_type),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={
                "v2_services": SimpleNamespace(access=access),
            }),
            args=["20"],
        )
        await adduser(update, context)

    await invoke(99, "private")
    assert 20 not in access.list_entries()["user"]
    assert "Solo un proprietario" in replies[-1]

    await invoke(10, "group")
    assert 20 not in access.list_entries()["user"]
    assert "Solo un proprietario" in replies[-1]

    await invoke(10, "private")
    assert 20 in access.list_entries()["user"]


def test_polling_start_failure_shuts_down_application_and_remains_retryable(repo):
    events = []
    made = []

    def factory(token):
        app = FakeApplication(token, events, fail_poll=True)
        made.append(app)
        return app

    repo.put_credential_with_state("telegram", "synthetic-token", "telegram_credential_version")
    manager = RuntimeManager(repo, factory)

    async def run():
        try:
            await manager.start_async()
        except RuntimeError as exc:
            assert "synthetic polling failure" in str(exc)
        else:
            raise AssertionError("polling startup unexpectedly succeeded")

    asyncio.run(run())
    assert not manager.is_running
    assert made[0].updater.running is False
    assert made[0].running is False
    assert events.count(("unpoll", "synthetic-token")) == 1
    assert events.count(("stop", "synthetic-token")) == 1


async def test_stop_request_during_initialize_prevents_late_poller_start(repo):
    events = []
    initialize_entered = asyncio.Event()
    finish_initialize = asyncio.Event()

    class SlowInitializeApplication(FakeApplication):
        async def initialize(self):
            initialize_entered.set()
            await finish_initialize.wait()
            events.append(("initialize", self.token))

    app = SlowInitializeApplication("synthetic-token", events)
    repo.put_credential_with_state("telegram", "synthetic-token", "telegram_credential_version")
    manager = RuntimeManager(repo, lambda token: app, shutdown_seconds=0.05)
    starting = asyncio.create_task(manager.start_async())
    await initialize_entered.wait()

    await asyncio.wait_for(manager.stop_async(), timeout=0.1)
    assert not manager.is_running
    finish_initialize.set()
    await asyncio.wait_for(starting, timeout=0.1)

    assert ("start", app.token) not in events
    assert ("poll", app.token) not in events
    assert not app.running
    assert events.count(("shutdown", "synthetic-token")) == 1


def test_started_runtime_registers_v2_audio_with_shared_services(repo, tmp_path):
    from telegram.ext import MessageHandler

    events = []
    app = FakeApplication("unused", events)
    app.handlers = []
    app.add_handler = app.handlers.append
    repo.put_credential_with_state("telegram", "synthetic-token", "telegram_credential_version")
    manager = RuntimeManager(repo, lambda token: app)
    services = SimpleNamespace(
        configuration=SimpleNamespace(active=lambda: None),
        settings=SimpleNamespace(data_dir=tmp_path), runner=object(),
        access=object(), repo=repo,
    )
    manager.services = services

    asyncio.run(manager.start_async())

    assert app.bot_data["v2_services"] is services
    assert manager.get_health()["audio_enabled"] is True
    assert manager.get_health()["setup_only"] is False
    assert app.bot_data["rate_limiter"] is not None
    assert app.bot_data["delivery_adapter"] is not None
    assert any(isinstance(handler, MessageHandler) for handler in app.handlers)


async def test_runtime_runs_limiter_cleanup_and_cancels_task_on_shutdown(repo, tmp_path, monkeypatch):
    from bot.rate_limiter import RateLimiter

    events = []
    app = FakeApplication("unused", events)
    app.handlers = []
    app.add_handler = app.handlers.append
    repo.put_credential_with_state("telegram", "synthetic-token", "telegram_credential_version")
    manager = RuntimeManager(repo, lambda token: app, shutdown_seconds=1)
    manager.services = SimpleNamespace(
        configuration=SimpleNamespace(active=lambda: None),
        settings=SimpleNamespace(data_dir=tmp_path), runner=object(),
        access=object(), repo=repo,
    )
    monkeypatch.setattr("bot.v2_runtime.RATE_LIMITER_CLEANUP_INTERVAL_SECONDS", 0.001)
    cleanup_called = asyncio.Event()

    async def cleanup(_self, max_age_seconds=3600):
        cleanup_called.set()

    monkeypatch.setattr(RateLimiter, "cleanup_expired_async", cleanup)
    await manager.start_async()
    cleanup_task = app.bot_data["rate_limiter_cleanup_task"]
    await asyncio.wait_for(cleanup_called.wait(), timeout=0.2)

    await manager.stop_async()

    assert cleanup_task.done() and cleanup_task.cancelled()
    assert "rate_limiter_cleanup_task" not in app.bot_data


async def test_shutdown_timeout_cancels_audio_handler_before_stopping_application(repo):
    from bot.rate_limiter import RateLimiter

    events = []
    app = FakeApplication("synthetic-token", events)
    app.updater.running = True
    app.running = True
    limiter = RateLimiter(max_global=1)
    await limiter.request_admission(73, 1)
    cancelled = asyncio.Event()
    never = asyncio.Event()

    async def blocked_handler():
        try:
            await never.wait()
        finally:
            cancelled.set()
            await limiter.release_async(73)

    task = asyncio.create_task(blocked_handler())
    app.bot_data["rate_limiter"] = limiter
    app.bot_data["v2_audio_tasks"] = {task}
    original_stop = app.stop

    async def stop_waiting_for_handlers():
        events.append(("stop_wait_handlers", app.token))
        await asyncio.gather(*app.bot_data["v2_audio_tasks"], return_exceptions=True)
        await original_stop()

    app.stop = stop_waiting_for_handlers
    manager = RuntimeManager(repo, lambda token: app, shutdown_seconds=0.1)
    manager._app = app
    manager._started = True

    try:
        await asyncio.wait_for(manager.stop_async(), timeout=0.3)
        assert cancelled.is_set()
        assert task.done()
        assert limiter._global_count == 0
        assert ("stop", app.token) in events
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_shutdown_deadline_does_not_wait_for_handler_that_ignores_cancel(repo):
    from bot.rate_limiter import RateLimiter

    events = []
    app = FakeApplication("synthetic-token", events)
    app.running = True
    limiter = RateLimiter(max_global=1)
    await limiter.request_admission(81, 1)
    release = asyncio.Event()
    cancellations = []

    async def ignores_cancel():
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancellations.append(True)
        await limiter.release_async(81)

    task = asyncio.create_task(ignores_cancel())
    app.bot_data["rate_limiter"] = limiter
    app.bot_data["v2_audio_tasks"] = {task}
    manager = RuntimeManager(repo, lambda token: app, shutdown_seconds=0.1)
    manager._app = app
    manager._started = True

    try:
        await asyncio.wait_for(manager.stop_async(), timeout=0.25)
        assert cancellations
        assert not task.done()
    finally:
        release.set()
        if not task.done():
            await asyncio.wait_for(task, timeout=0.1)


async def test_shutdown_deadline_bounds_application_stop(repo):
    events = []
    app = FakeApplication("synthetic-token", events)
    app.running = True
    blocking_stop = asyncio.Event()
    original_stop = app.stop

    async def never_finishes_stop():
        events.append(("stop_enter", app.token))
        await blocking_stop.wait()
        await original_stop()

    app.stop = never_finishes_stop
    manager = RuntimeManager(repo, lambda token: app, shutdown_seconds=0.1)
    manager._app = app
    manager._started = True

    await asyncio.wait_for(manager.stop_async(), timeout=0.25)
    assert ("stop_enter", app.token) in events
    assert ("shutdown", app.token) in events
    assert not manager.is_running
    # A cancellation-resistant stop coroutine cannot be forcibly terminated;
    # the runtime returns on deadline and records best-effort cleanup.
    assert app.running

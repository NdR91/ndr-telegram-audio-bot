import asyncio
import multiprocessing
from pathlib import Path

from bot.database.runtime_lock import RuntimeDatabaseLock, RuntimeDatabaseLocked


def _attempt_lock(path, result_queue):
    lock = RuntimeDatabaseLock(Path(path))
    try:
        lock.acquire()
    except RuntimeDatabaseLocked:
        result_queue.put("locked")
    else:
        result_queue.put("acquired")
        lock.release()


def test_runtime_database_lock_excludes_another_process_and_releases(tmp_path):
    database = tmp_path / "app.sqlite3"
    owner = RuntimeDatabaseLock(database)
    owner.acquire()
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    contender = context.Process(target=_attempt_lock, args=(str(database), results))
    contender.start()
    contender.join(timeout=10)
    assert contender.exitcode == 0
    assert results.get(timeout=1) == "locked"

    owner.release()
    retry = context.Process(target=_attempt_lock, args=(str(database), results))
    retry.start()
    retry.join(timeout=10)
    assert retry.exitcode == 0
    assert results.get(timeout=1) == "acquired"


def test_runtime_manager_holds_lock_until_application_shutdown(repo):
    from bot.v2_runtime import RuntimeManager

    manager = RuntimeManager(repo)
    asyncio.run(manager.start_async())
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    contender = context.Process(target=_attempt_lock, args=(str(repo.path), results))
    contender.start()
    contender.join(timeout=10)
    assert contender.exitcode == 0
    assert results.get(timeout=1) == "locked"

    asyncio.run(manager.stop_async(release_runtime_lock=True))
    retry = context.Process(target=_attempt_lock, args=(str(repo.path), results))
    retry.start()
    retry.join(timeout=10)
    assert retry.exitcode == 0
    assert results.get(timeout=1) == "acquired"


def test_service_composition_locks_database_before_repository_initialization(tmp_path, monkeypatch):
    from bot.database.v2_repository import V2Repository
    from bot.deployment import DeploymentSettings
    from bot.services import build_services

    settings = DeploymentSettings(tmp_path, tmp_path / "composition.sqlite3", tmp_path / "key")
    original_initialize = V2Repository.initialize
    checked = []

    def initialize_after_lock_check(repository):
        context = multiprocessing.get_context("spawn")
        results = context.Queue()
        contender = context.Process(
            target=_attempt_lock, args=(str(settings.db_path), results)
        )
        contender.start()
        contender.join(timeout=10)
        assert contender.exitcode == 0
        assert results.get(timeout=1) == "locked"
        checked.append(True)
        original_initialize(repository)

    monkeypatch.setattr(V2Repository, "initialize", initialize_after_lock_check)
    services = build_services(settings)
    assert checked == [True]
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    contender = context.Process(
        target=_attempt_lock, args=(str(settings.db_path), results)
    )
    contender.start()
    contender.join(timeout=10)
    assert contender.exitcode == 0
    assert results.get(timeout=1) == "locked"

    services.repo.close()
    retry = context.Process(target=_attempt_lock, args=(str(settings.db_path), results))
    retry.start()
    retry.join(timeout=10)
    assert retry.exitcode == 0
    assert results.get(timeout=1) == "acquired"


def test_service_composition_releases_database_lock_when_initialization_fails(
    tmp_path, monkeypatch
):
    from bot.database.v2_repository import V2Repository
    from bot.deployment import DeploymentSettings
    from bot.services import build_services

    settings = DeploymentSettings(tmp_path, tmp_path / "failed.sqlite3", tmp_path / "key")

    def fail_initialize(self):
        raise RuntimeError("synthetic initialization failure")

    monkeypatch.setattr(V2Repository, "initialize", fail_initialize)
    try:
        build_services(settings)
    except RuntimeError as exc:
        assert str(exc) == "synthetic initialization failure"
    else:
        raise AssertionError("expected synthetic initialization failure")

    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    contender = context.Process(
        target=_attempt_lock, args=(str(settings.db_path), results)
    )
    contender.start()
    contender.join(timeout=10)
    assert contender.exitcode == 0
    assert results.get(timeout=1) == "acquired"


def test_shutdown_keeps_database_lock_until_blocked_startup_finishes(repo):
    from types import SimpleNamespace

    from bot.v2_runtime import RuntimeManager

    entered = asyncio.Event()
    finish = asyncio.Event()

    class SlowApplication:
        running = False
        updater = SimpleNamespace(running=False)

        def __init__(self):
            self.bot_data = {}

        async def initialize(self):
            entered.set()
            await finish.wait()

        async def shutdown(self):
            pass

    repo.put_credential_with_state("telegram", "synthetic-token", "telegram_credential_version")
    manager = RuntimeManager(repo, lambda _: SlowApplication(), shutdown_seconds=0.03)

    async def exercise():
        start = asyncio.create_task(manager.start_async())
        await entered.wait()
        await manager.stop_async(release_runtime_lock=True)

        context = multiprocessing.get_context("spawn")
        results = context.Queue()
        contender = context.Process(target=_attempt_lock, args=(str(repo.path), results))
        contender.start()
        contender.join(timeout=10)
        assert contender.exitcode == 0
        assert results.get(timeout=1) == "locked"

        finish.set()
        await asyncio.wait_for(start, timeout=2)
        await asyncio.wait_for(manager._deferred_lock_release, timeout=2)
        retry = context.Process(target=_attempt_lock, args=(str(repo.path), results))
        retry.start()
        retry.join(timeout=10)
        assert retry.exitcode == 0
        assert results.get(timeout=1) == "acquired"

    asyncio.run(exercise())


def test_shutdown_keeps_database_lock_until_cancel_resistant_audio_task_finishes(repo):
    from types import SimpleNamespace

    from bot.v2_runtime import RuntimeManager

    release = asyncio.Event()

    async def stubborn_audio_task():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    class RunningApplication:
        running = True
        updater = SimpleNamespace(running=True)

        def __init__(self):
            self.bot_data = {"v2_audio_tasks": [asyncio.create_task(stubborn_audio_task())]}
            async def stop_updater():
                self.updater.running = False
            self.updater.stop = stop_updater

        async def stop(self):
            self.running = False

        async def shutdown(self):
            pass

    async def exercise():
        manager = RuntimeManager(repo, shutdown_seconds=1.0)
        manager._runtime_database_lock.acquire()
        app = RunningApplication()
        manager._app = app
        manager._started = True
        try:
            await manager.stop_async(release_runtime_lock=True)
            assert manager._unsafe_shutdown_tasks

            context = multiprocessing.get_context("spawn")
            results = context.Queue()
            contender = context.Process(target=_attempt_lock, args=(str(repo.path), results))
            contender.start()
            contender.join(timeout=10)
            assert contender.exitcode == 0
            assert results.get(timeout=1) == "locked"

            release.set()
            await asyncio.wait_for(
                asyncio.gather(*tuple(manager._unsafe_shutdown_tasks), return_exceptions=True), timeout=2
            )
            await asyncio.wait_for(manager._deferred_lock_release, timeout=2)
            retry = context.Process(target=_attempt_lock, args=(str(repo.path), results))
            retry.start()
            retry.join(timeout=10)
            assert retry.exitcode == 0
            assert results.get(timeout=1) == "acquired"
        finally:
            release.set()

    asyncio.run(exercise())

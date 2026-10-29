"""Composition root for the V2 application services."""

from dataclasses import dataclass
from typing import Callable

from bot.access import AccessService
from bot.diagnostics import DiagnosticsService
from bot.database.secret_store import SecretStore
from bot.database.runtime_lock import RuntimeDatabaseLock
from bot.database.v2_repository import V2Repository
from bot.deployment import DeploymentSettings
from bot.pairing import PairingService
from bot.transcription.configuration import ConfigurationService
from bot.transcription.fallback import FallbackProcessor
from bot.transcription.models import Prerequisites
from bot.transcription.pool import ProviderPool
from bot.transcription.profiles import ProfileService
from bot.transcription.runner import TranscriptionRunner
from bot.transcription.testing import ConfigurationTestService
from bot.v2_runtime import RuntimeManager


@dataclass
class AppServices:
    repo: V2Repository
    configuration: ConfigurationService
    pool: ProviderPool
    runner: TranscriptionRunner
    transcription_tests: ConfigurationTestService
    runtime: RuntimeManager
    access: AccessService
    pairing: PairingService
    settings: DeploymentSettings
    secret_store: SecretStore
    diagnostics: DiagnosticsService
    profiles: ProfileService
    fallback_processor: FallbackProcessor
    readiness: Callable[[], Prerequisites]


def build_services(settings: DeploymentSettings) -> AppServices:
    settings.validate_database_target()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    runtime_lock = RuntimeDatabaseLock(settings.db_path)
    runtime_lock.acquire()
    try:
        return _build_services_locked(settings, runtime_lock)
    except BaseException:
        runtime_lock.release()
        raise


def _build_services_locked(settings: DeploymentSettings,
                           runtime_lock: RuntimeDatabaseLock) -> AppServices:
    secret_store = SecretStore(str(settings.key_path))
    try:
        secret_store.initialize()
    except Exception:
        # Keep setup/login reachable; credential writes will fail closed.
        import logging
        logging.getLogger(__name__).exception("V2 encryption key is unavailable")
    repo = V2Repository(settings.db_path, secret_store)
    repo.initialize()
    repository_close = repo.close

    def close_repository() -> None:
        try:
            repository_close()
        finally:
            if not getattr(runtime, "_asgi_lifespan_entered", False):
                runtime_lock.release()

    # Test/operator-created service containers may be closed without entering
    # the ASGI lifespan. Tie their lock lease to the repository lifetime too.
    repo.close = close_repository
    access = AccessService(repo)
    readiness = lambda: Prerequisites(
        web_admin=bool(repo.get_setup_state("web_admin_hash")),
        telegram_valid=bool(repo.get_setup_state("telegram_credential_version")),
        owner=access.has_owner(),
    )
    configuration = ConfigurationService(repo, readiness)
    profiles = ProfileService(repo)
    pool = ProviderPool(repo)
    runner = TranscriptionRunner(pool, settings.data_dir / "tmp")
    transcription_tests = ConfigurationTestService(configuration, runner, profiles=profiles)
    pairing = PairingService(repo, access)
    runtime = RuntimeManager(repo, runtime_database_lock=runtime_lock)
    diagnostics = DiagnosticsService(repo, configuration, runtime, profiles=profiles)
    fallback_processor = FallbackProcessor(
        runner,
        record_event=diagnostics.record_fallback,
        record_notice_failure=diagnostics.record_notice_failure,
    )
    services = AppServices(
        repo=repo,
        configuration=configuration,
        pool=pool,
        runner=runner,
        transcription_tests=transcription_tests,
        runtime=runtime,
        access=access,
        pairing=pairing,
        settings=settings,
        secret_store=secret_store,
        diagnostics=diagnostics,
        profiles=profiles,
        fallback_processor=fallback_processor,
        readiness=readiness,
    )
    runtime.services = services
    return services

"""Composition root for the V2 application services."""

from dataclasses import dataclass

from bot.access import AccessService
from bot.diagnostics import DiagnosticsService
from bot.database.secret_store import SecretStore
from bot.database.v2_repository import V2Repository
from bot.deployment import DeploymentSettings
from bot.pairing import PairingService
from bot.transcription.configuration import ConfigurationService
from bot.transcription.pool import ProviderPool
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


def build_services(settings: DeploymentSettings) -> AppServices:
    settings.validate_database_target()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    secret_store = SecretStore(str(settings.key_path))
    try:
        secret_store.initialize()
    except Exception:
        # Keep setup/login reachable; credential writes will fail closed.
        import logging
        logging.getLogger(__name__).exception("V2 encryption key is unavailable")
    repo = V2Repository(settings.db_path, secret_store)
    repo.initialize()
    access = AccessService(repo)
    configuration = ConfigurationService(repo, lambda: __import__(
        "bot.transcription.models", fromlist=["Prerequisites"]
    ).Prerequisites(
        web_admin=bool(repo.get_setup_state("web_admin_hash")),
        telegram_valid=bool(repo.get_setup_state("telegram_credential_version")),
        owner=access.has_owner(),
    ))
    pool = ProviderPool(repo)
    runner = TranscriptionRunner(pool, settings.data_dir / "tmp")
    transcription_tests = ConfigurationTestService(configuration, runner)
    pairing = PairingService(repo, access)
    runtime = RuntimeManager(repo)
    diagnostics = DiagnosticsService(repo, configuration, runtime)
    services = AppServices(repo, configuration, pool, runner, transcription_tests, runtime, access, pairing,
                           settings, secret_store, diagnostics)
    runtime.services = services
    return services

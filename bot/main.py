#!/usr/bin/env python3
"""
Telegram Audio Transcriber Bot - Main Entry Point

A modular Telegram bot that transcribes audio files using AI providers.
Supports OpenAI (Whisper + GPT) and Google Gemini for transcription and text refinement.
"""

import sys
import os
import logging

# Add project root to Python path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.insert(0, project_root)

import bot.recovery
import bot.setup
from bot.config import Config
from bot.config_service import ConfigService
from bot.database import DatabaseManager, SecretStore, SecretStoreError
from bot.exceptions import ConfigError
from bot.runtime_manager import RuntimeManager
from bot.setup import generate_setup_code, is_code_generated
from bot.state import AppState, StateChecker
from bot import utils

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)


def _warn_if_sensitive_logging_enabled() -> None:
    enabled = os.getenv("LOG_SENSITIVE_TEXT", "0").strip().lower() in {"1", "true", "yes"}
    if enabled:
        logger.warning(
            "Sensitive transcript logging is enabled via LOG_SENSITIVE_TEXT. "
            "Transcribed and refined text may be written to DEBUG logs."
        )


def initialize_configuration() -> Config:
    """
    Initialize and validate bot configuration.
    
    Returns:
        Validated configuration object
        
    Raises:
        ConfigError: If configuration is invalid or missing
        RuntimeError: If configuration fails to load
    """
    try:
        logger.info("Loading bot configuration...")
        config = Config()
        logger.info("Configuration loaded successfully")
        logger.info(f"Provider: {config.provider_name}, Model: {config.model_name or 'default'}")
        _warn_if_sensitive_logging_enabled()
        return config
        
    except (ConfigError, RuntimeError) as e:
        logger.error(f"Configuration error: {e}")
        raise


def _get_database_path(config) -> str:
    """Return the unified database path (default: ``<audio_dir>/app.sqlite3``)."""
    return os.getenv(
        "APPLICATION_DB",
        os.path.join(config.audio_dir, "app.sqlite3"),
    )


def _get_master_key_path(config) -> str:
    """Return the master key path.

    Respects ``MASTER_KEY_FILE`` when set; otherwise defaults to
    ``<audio_dir>/.master_key``.
    """
    return os.getenv(
        "MASTER_KEY_FILE",
        os.path.join(config.audio_dir, ".master_key"),
    )


def _init_database(
    db_path: str,
    config,
    secret_store: SecretStore | None = None,
) -> DatabaseManager:
    """Initialize the unified database and import legacy whitelist data."""
    db = DatabaseManager(db_path, secret_store=secret_store)
    db.initialize()
    logger.info("Unified application database ready at %s", db_path)

    # Bootstrap whitelist from legacy data if the tables are empty.
    db.import_whitelist_from_dict(config.authorized_data)

    return db


def _init_secret_store(key_path: str) -> SecretStore | None:
    """Initialize the local secret store.

    Returns ``None`` when the store cannot be initialized (e.g. permission
    error), allowing the application to continue without at-rest encryption.
    """
    try:
        store = SecretStore(key_path)
        is_new = store.initialize()
        if is_new:
            logger.info("Generated new master key at %s (first run)", key_path)
        else:
            logger.debug("Loaded master key from %s", key_path)
        return store
    except SecretStoreError:
        logger.exception("Failed to initialize SecretStore; continuing without at-rest encryption")
        return None
    except PermissionError:
        logger.exception(
            "Permission denied while initializing SecretStore at %s; "
            "continuing without at-rest encryption",
            key_path,
        )
        return None


def _print_setup_code(code: str) -> None:
    """Print the setup code prominently in the logs so the administrator
    can copy it for guided onboarding."""
    # Deliberately uses print (not logger) so the code is always visible
    # regardless of log-level configuration.
    sep = "=" * 56
    print(f"\n{sep}", flush=True)
    print(f"  SETUP CODE: {code}", flush=True)
    print(f"  Valido per {bot.setup.SETUP_CODE_TTL_SECONDS} secondi.", flush=True)
    print(f"  Apri l'interfaccia web per completare la configurazione.", flush=True)
    print(f"{sep}\n", flush=True)
    logger.info(
        "One-time setup code generated — valid for %s seconds",
        bot.setup.SETUP_CODE_TTL_SECONDS,
    )


def _print_recovery_code(code: str) -> None:
    """Print the recovery code prominently in the logs so the administrator
    can copy it for password reset."""
    sep = "=" * 56
    print(f"\n{sep}", flush=True)
    print(f"  RECOVERY CODE: {code}", flush=True)
    print(f"  Valido per {bot.recovery.RECOVERY_CODE_TTL_SECONDS} secondi.", flush=True)
    print(f"  Vai su /recovery nell'interfaccia web per reimpostare la password.", flush=True)
    print(f"{sep}\n", flush=True)
    logger.info(
        "One-time recovery code generated — valid for %s seconds",
        bot.recovery.RECOVERY_CODE_TTL_SECONDS,
    )


def main() -> None:
    """
    Main entry point for Telegram bot.
    
    Initializes configuration, creates application, and starts polling.
    """
    try:
        # Initialize configuration
        config = initialize_configuration()

        # Initialize local secret store for at-rest encryption (A2)
        key_path = _get_master_key_path(config)
        secret_store = _init_secret_store(key_path)

        # Initialize unified application database (A1) with optional
        # secret store for transparent credential encryption.
        db_path = _get_database_path(config)
        database_manager = _init_database(db_path, config, secret_store)

        # Build the configuration service (A3) on top of the database.
        config_service = ConfigService(database_manager, secret_store=secret_store)

        # Build the runtime state checker (A4) with legacy config for
        # backward compatibility (A4.1).  When the unified database has
        # not yet recorded admin_created, the state checker will treat
        # the legacy .env + authorized.json deployment as READY.
        state_checker = StateChecker(
            config_service, database_manager, legacy_config=config,
        )

        # Cleanup temporary audio files from previous runs
        utils.cleanup_audio_directory(config.audio_dir)

        # A6 — First-run setup mode.
        # On a blank data volume (state == SETUP_REQUIRED) generate a
        # time-limited one-time setup code and print it prominently in
        # the logs.  The code is stored only as a SHA-256 hash.
        # NOTE: Currently this is gated by the legacy-config shortcut:
        # when a valid .env exists, the state checker returns READY and
        # no code is generated.  Once A7 removes mandatory .env, this
        # path will activate automatically on first start.
        app_state = state_checker.get_state()
        if app_state.state == AppState.SETUP_REQUIRED:
            if not is_code_generated(database_manager):
                setup_code = generate_setup_code(database_manager)
                _print_setup_code(setup_code)

        # Create RuntimeManager (A5) — owns the Telegram bot lifecycle.
        # In the current migration stage the manager runs in blocking
        # mode; the frontend (Phase 2) will use non-blocking start/stop.
        manager = RuntimeManager(
            config,
            database_manager,
            secret_store,
            config_service,
            state_checker,
        )
        logger.info(
            "RuntimeManager initialised, state=%s",
            manager.get_state().state.value,
        )

        # W6 — Generate a recovery code on every startup when admin exists.
        # The code is printed in the logs so the administrator can always
        # recover access, even without Telegram or the frontend credentials.
        if app_state.state != AppState.SETUP_REQUIRED:
            from bot.recovery import generate_recovery_code, is_recovery_code_generated
            if not is_recovery_code_generated(database_manager):
                recovery_code = generate_recovery_code(database_manager)
                _print_recovery_code(recovery_code)

        # Start bot (blocking — legacy CLI mode)
        logger.info("Starting Telegram bot polling...")
        manager.run_until_stopped()
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        sys.exit(0)
        
    except (ConfigError, RuntimeError) as e:
        logger.error(f"Fatal configuration error: {e}")
        sys.exit(1)
        
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()

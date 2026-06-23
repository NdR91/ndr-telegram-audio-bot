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

from bot.config import Config
from bot.database import DatabaseManager, SecretStore, SecretStoreError
from bot.exceptions import ConfigError
from bot.core.app import create_application, run_application
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

        # Cleanup temporary audio files from previous runs
        utils.cleanup_audio_directory(config.audio_dir)

        # Create and setup application
        logger.info("Creating Telegram application...")
        app = create_application(
            config.telegram_token,
            config,
            database_manager=database_manager,
            secret_store=secret_store,
        )

        # Start bot
        logger.info("Starting Telegram bot polling...")
        run_application(app)
        
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

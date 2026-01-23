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
from bot.exceptions import ConfigError
from bot.core.app import create_application, run_application
from bot import utils

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)


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
        return config
        
    except (ConfigError, RuntimeError) as e:
        logger.error(f"Configuration error: {e}")
        raise


def main() -> None:
    """
    Main entry point for Telegram bot.
    
    Initializes configuration, creates application, and starts polling.
    """
    try:
        # Initialize configuration
        config = initialize_configuration()
        
        # Cleanup temporary audio files from previous runs
        utils.cleanup_audio_directory(config.audio_dir)
        
        # Create and setup application
        logger.info("Creating Telegram application...")
        app = create_application(config.telegram_token, config)
        
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
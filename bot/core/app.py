"""
Core application setup and configuration.
"""

import sys
import os
import logging
from typing import List

from telegram import BotCommand
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

from bot.handlers.commands import start, whoami, help_command
from bot.handlers.admin import WhitelistManager, adduser, removeuser, addgroup, removegroup
from bot.handlers.audio import AudioProcessor, handle_audio
from bot.rate_limiter import RateLimiter
from bot.ui.streaming import TelegramDeliveryAdapter

logger = logging.getLogger(__name__)


async def cleanup_rate_limiter_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Background job to clean up expired rate limit entries."""
    try:
        limiter = context.application.bot_data.get('rate_limiter')
        if limiter is None:
            raise RuntimeError("RateLimiter not initialized")
        await limiter.cleanup_expired_async()
        logger.debug("Rate limiter cleanup completed")
    except Exception as e:
        logger.error(f"Error in rate limiter cleanup job: {e}")


def create_application(token: str, config) -> Application:
    """
    Create and configure the Telegram application.
    
    Args:
        token: Telegram bot token
        config: Bot configuration object
        
    Returns:
        Configured Application instance
    """
    async def _post_init(application: Application) -> None:
        """Set bot commands menu during application startup."""
        commands: List[BotCommand] = [
            BotCommand("start", "Messaggio di benvenuto"),
            BotCommand("whoami", "Mostra user_id e chat_id"),
            BotCommand("help", "Mostra la lista dei comandi"),
            BotCommand("adduser", "Aggiunge un utente (admin only)"),
            BotCommand("removeuser", "Rimuove un utente (admin only)"),
            BotCommand("addgroup", "Autorizza un gruppo (admin only)"),
            BotCommand("removegroup", "Rimuove un gruppo (admin only)"),
        ]

        try:
            await application.bot.set_my_commands(commands)
            logger.info("Bot commands menu setup completed")
        except Exception as e:
            logger.error(f"Failed to setup bot commands: {e}")

    # Build application
    # Enable concurrent updates to allow parallel processing of messages
    app = (
        ApplicationBuilder()
        .token(token)
        .concurrent_updates(True)
        .post_init(_post_init)
        .build()
    )
    
    # Store config in bot_data for global access (singleton pattern)
    app.bot_data['config'] = config
    app.bot_data['whitelist_manager'] = WhitelistManager(config)
    app.bot_data['audio_processor'] = AudioProcessor(config)
    app.bot_data['delivery_adapter'] = TelegramDeliveryAdapter(
        progressive_enabled=config.telegram_progressive_output_config["enabled"],
    )
    app.bot_data['rate_limiter'] = RateLimiter(
        max_per_user=config.rate_limit_config["max_per_user"],
        cooldown=config.rate_limit_config["cooldown_seconds"],
        max_global=config.rate_limit_config["max_concurrent_global"],
        max_file_size_mb=config.rate_limit_config["max_file_size_mb"],
        queue_enabled=config.rate_limit_config["queue_enabled"],
        max_queue_size=config.rate_limit_config["max_queue_size"],
        max_queued_per_user=config.rate_limit_config["max_queued_per_user"],
    )
    
    # Register handlers
    register_handlers(app)
    
    # Setup background jobs
    if app.job_queue:
        # Run cleanup every hour (3600s), starting after 1 minute (60s)
        app.job_queue.run_repeating(cleanup_rate_limiter_job, interval=3600, first=60)
        logger.info("Rate limiter cleanup job scheduled")
    
    return app


def register_handlers(app: Application) -> None:
    """
    Register all command and message handlers.
    
    Args:
        app: Application instance
    """
    # Command handlers
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('whoami', whoami))
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(CommandHandler('adduser', adduser))
    app.add_handler(CommandHandler('removeuser', removeuser))
    app.add_handler(CommandHandler('addgroup', addgroup))
    app.add_handler(CommandHandler('removegroup', removegroup))
    
    # Message handlers
    app.add_handler(MessageHandler(
        filters.VOICE | filters.AUDIO | filters.Document.AUDIO, 
        handle_audio
    ))


def run_application(app: Application) -> None:
    """
    Run the Telegram application with polling.
    
    Args:
        app: Application instance
    """
    logger.info("Starting bot polling...")
    app.run_polling()

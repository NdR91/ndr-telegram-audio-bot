"""
Core application setup and configuration.
"""

import sys
import os
import logging
import asyncio
from typing import List

from telegram import BotCommand
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters

from bot.handlers.commands import start, whoami, help_command
from bot.handlers.admin import adduser, removeuser, addgroup, removegroup
from bot.handlers.audio import handle_audio

logger = logging.getLogger(__name__)


def create_application(token: str, config) -> Application:
    """
    Create and configure the Telegram application.
    
    Args:
        token: Telegram bot token
        config: Bot configuration object
        
    Returns:
        Configured Application instance
    """
    # Initialize global managers
    from bot.handlers.admin import init_whitelist_manager
    from bot.handlers.audio import init_audio_processor, init_rate_limiter
    
    init_whitelist_manager(config)
    init_audio_processor(config)
    init_rate_limiter(config)
    
    # Build application
    app = ApplicationBuilder().token(token).build()
    
    # Store config in bot_data for global access (singleton pattern)
    app.bot_data['config'] = config
    
    # Register handlers
    register_handlers(app)
    
    # Setup bot commands menu
    setup_bot_commands(app, token)
    
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


def setup_bot_commands(app: Application, token: str) -> None:
    """
    Setup bot commands menu in Telegram client.
    
    Args:
        app: Application instance
        token: Bot token for commands setup
    """
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
        # Run the coroutine synchronously to set commands
        asyncio.get_event_loop().run_until_complete(
            app.bot.set_my_commands(commands)
        )
        logger.info("Bot commands menu setup completed")
    except Exception as e:
        logger.error(f"Failed to setup bot commands: {e}")


def run_application(app: Application) -> None:
    """
    Run the Telegram application with polling.
    
    Args:
        app: Application instance
    """
    logger.info("Starting bot polling...")
    app.run_polling()
"""
Basic command handlers for Telegram bot.
"""

import sys
import os
import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot import constants as c

logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /start command - Welcome message.
    
    Args:
        update: Telegram update object
        context: Telegram context object
    """
    logger.info(f"Start command from user {update.effective_user.id}")
    await update.message.reply_text(c.MSG_START)


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /whoami command - Show user and chat IDs.
    
    Args:
        update: Telegram update object
        context: Telegram context object
    """
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    logger.info(f"Whoami command from user {user_id} in chat {chat_id}")
    await update.message.reply_text(f"🔍 user_id: {user_id}\n🔍 chat_id: {chat_id}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /help command - Show available commands.
    
    Args:
        update: Telegram update object
        context: Telegram context object
    """
    logger.info(f"Help command from user {update.effective_user.id}")
    await update.message.reply_text(c.MSG_HELP)
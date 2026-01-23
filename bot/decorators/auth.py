"""
Authentication and authorization decorators for Telegram bot handlers.
"""

import logging
from functools import wraps
from typing import Callable, Any

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


def restricted(func: Callable) -> Callable:
    """
    Decorator to restrict access to authorized users, groups, and admins.
    
    Args:
        func: The async function to wrap
        
    Returns:
        Wrapped function that checks authorization before execution
    """
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        # Import here to avoid circular imports
        from bot import constants as c
        
        # Get user and chat IDs
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        
        # Get config from bot_data (injected in create_application)
        config = context.bot_data['config']
        
        # Check authorization
        if (user_id in config.authorized_data.get('admin', []) or
            user_id in config.authorized_data.get('users', []) or
            chat_id in config.authorized_data.get('groups', [])):
            return await func(update, context, *args, **kwargs)
        
        # User not authorized
        logger.warning(f"Unauthorized access attempt - User: {user_id}, Chat: {chat_id}")
        await update.message.reply_text(c.MSG_UNAUTHORIZED)
    
    return wrapped


def admin_only(func: Callable) -> Callable:
    """
    Decorator to restrict access to admins only.
    
    Args:
        func: The async function to wrap
        
    Returns:
        Wrapped function that checks admin authorization before execution
    """
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        # Import here to avoid circular imports
        from bot import constants as c
        
        # Get user ID
        user_id = update.effective_user.id
        
        # Get config from bot_data
        config = context.bot_data['config']
        
        # Check admin authorization
        if user_id in config.authorized_data.get('admin', []):
            return await func(update, context, *args, **kwargs)
        
        # User not authorized as admin
        logger.warning(f"Unauthorized admin access attempt - User: {user_id}")
        await update.message.reply_text(c.MSG_ONLY_ADMIN)
    
    return wrapped
"""
Progress UI components for Telegram bot.
"""

import logging
from typing import Optional, Dict

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# Cache to store last progress message for each chat:message combination
# This prevents duplicate message updates that cause Telegram API warnings
_progress_cache: Dict[str, str] = {}


async def update_progress(context: ContextTypes.DEFAULT_TYPE, 
                         chat_id: int, 
                         message_id: int, 
                         status_text: str) -> None:
    """
    Updates progress message with typing indicator.
    Includes deduplication to prevent Telegram API warnings.
    
    Args:
        context: Telegram bot context
        chat_id: Chat ID to send updates to
        message_id: Message ID to edit
        status_text: Status text to display
    """
    # Generate cache key for this specific message
    cache_key = f"{chat_id}:{message_id}"
    
    # Check if this is a duplicate update
    if _progress_cache.get(cache_key) == status_text:
        logger.debug(f"Skipping duplicate progress update for {cache_key}")
        return
    
    try:
        # Show typing action
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        
        # Update progress message
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id, 
            text=status_text
        )
        
        # Cache this message content to prevent future duplicates
        _progress_cache[cache_key] = status_text
        
        logger.debug(f"Progress updated for chat {chat_id}: {status_text}")
        
    except Exception as e:
        logger.warning(f"Failed to update progress: {e}")


def get_progress_message(stage: str, stage_num: int, total_stages: int, 
                         bar_length: int = 8) -> str:
    """
    Generates progress message with visual progress bar.
    
    Args:
        stage: Current stage description
        stage_num: Current stage number (1-based)
        total_stages: Total number of stages
        bar_length: Length of progress bar (default: 8)
        
    Returns:
        Formatted progress message with bar
    """
    # Calculate progress bar
    filled = int(bar_length * stage_num // total_stages)
    bar = "⚫" * filled + "⚪" * (bar_length - filled)
    
    return f"{stage}\nProgress: {bar}\nStep: {stage_num}/{total_stages}"


def clear_progress_cache(chat_id: Optional[int] = None, message_id: Optional[int] = None) -> None:
    """
    Clear progress cache for specific chat/message or all entries.
    
    Args:
        chat_id: Specific chat ID to clear (optional)
        message_id: Specific message ID to clear (optional)
    """
    global _progress_cache
    
    if chat_id is not None and message_id is not None:
        # Clear specific message
        cache_key = f"{chat_id}:{message_id}"
        _progress_cache.pop(cache_key, None)
        logger.debug(f"Cleared progress cache for {cache_key}")
    elif chat_id is not None:
        # Clear all messages for a specific chat
        keys_to_remove = [k for k in _progress_cache.keys() if k.startswith(f"{chat_id}:")]
        for key in keys_to_remove:
            _progress_cache.pop(key, None)
        logger.debug(f"Cleared progress cache for chat {chat_id}")
    else:
        # Clear all cache
        _progress_cache.clear()
        logger.debug("Cleared all progress cache")
"""
Administrative command handlers for whitelist management.
"""

import json
import os
import logging
import asyncio
import tempfile
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from bot.decorators.auth import admin_only
from bot import constants as c

logger = logging.getLogger(__name__)


def get_whitelist_manager(context: ContextTypes.DEFAULT_TYPE) -> "WhitelistManager":
    """Get the application-scoped whitelist manager instance."""
    manager = context.bot_data.get('whitelist_manager')
    if manager is None:
        raise RuntimeError("WhitelistManager not initialized")
    return manager


class WhitelistManager:
    """
    Manages user and group whitelist operations.
    """
    
    def __init__(self, config):
        """
        Initialize whitelist manager with configuration.
        
        Args:
            config: Bot configuration object
        """
        self.config = config
        self.authorized_data = config.authorized_data
        self.authorized_file = config.authorized_file
        self._lock = asyncio.Lock()
    
    def parse_user_id(self, args) -> Optional[int]:
        """
        Parse and validate user ID from command arguments.
        
        Args:
            args: Command arguments list
            
        Returns:
            Parsed user ID or None if invalid
        """
        if not args:
            return None
            
        try:
            return int(args[0])
        except (ValueError, TypeError, IndexError):
            return None
    
    def add_to_whitelist(self, target_type: str, target_id: int) -> tuple[bool, str]:
        """
        Add user or group to whitelist.
        
        Args:
            target_type: 'users' or 'groups'
            target_id: ID to add
            
        Returns:
            Tuple of (success, message)
        """
        # Check if already in whitelist
        if target_id in self.authorized_data.get(target_type, []):
            if target_type == 'users':
                return False, c.MSG_USER_ALREADY_WHITELISTED
            else:
                return False, c.MSG_GROUP_ALREADY_AUTH
        
        # Add to whitelist
        self.authorized_data.setdefault(target_type, []).append(target_id)
        return True, f"Added {target_id} to {target_type}"
    
    def remove_from_whitelist(self, target_type: str, target_id: int) -> tuple[bool, str]:
        """
        Remove user or group from whitelist.
        
        Args:
            target_type: 'users' or 'groups'
            target_id: ID to remove
            
        Returns:
            Tuple of (success, message)
        """
        # Check if exists in whitelist
        if target_id not in self.authorized_data.get(target_type, []):
            if target_type == 'users':
                return False, c.MSG_USER_NOT_WHITELISTED
            else:
                return False, c.MSG_GROUP_NOT_AUTH
        
        # Remove from whitelist
        self.authorized_data[target_type].remove(target_id)
        return True, f"Removed {target_id} from {target_type}"
    
    def save_changes(self) -> None:
        """Save whitelist changes to file atomically."""
        directory = os.path.dirname(os.path.abspath(self.authorized_file)) or '.'
        temp_path = None

        try:
            with tempfile.NamedTemporaryFile(
                'w',
                dir=directory,
                delete=False,
                encoding='utf-8'
            ) as f:
                temp_path = f.name
                json.dump(self.authorized_data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            os.replace(temp_path, self.authorized_file)
            logger.info("Whitelist changes saved successfully")
        except Exception as e:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    logger.warning(f"Failed to remove temporary whitelist file: {temp_path}")
            logger.error(f"Failed to save whitelist changes: {e}")
            raise RuntimeError("Failed to save changes")

    async def apply_whitelist_change(
        self,
        action: str,
        target_type: str,
        target_id: int,
    ) -> tuple[bool, str]:
        """Apply and persist a whitelist change under a shared lock."""
        async with self._lock:
            if action == 'add':
                success, message = self.add_to_whitelist(target_type, target_id)
            else:
                success, message = self.remove_from_whitelist(target_type, target_id)

            if not success:
                return success, message

            self.save_changes()
            return success, message


@admin_only
async def whitelist_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                  action: str, target_type: str) -> None:
    """
    Unified handler for whitelist management commands.
    
    Args:
        update: Telegram update object
        context: Telegram context object
        action: 'add' or 'remove'
        target_type: 'users' or 'groups'
    """
    # Initialize manager
    manager = get_whitelist_manager(context)
    user_id = update.effective_user.id
    
    # Parse target ID
    target_id = manager.parse_user_id(context.args)
    if target_id is None:
        # Show appropriate usage message
        if target_type == 'users':
            usage_msg = c.MSG_USAGE_ADDUSER if action == 'add' else c.MSG_USAGE_REMOVEUSER
        else:
            usage_msg = c.MSG_USAGE_ADDGROUP if action == 'add' else c.MSG_USAGE_REMOVEGROUP
        
        await update.message.reply_text(usage_msg)
        return
    
    # Perform operation
    try:
        success, message = await manager.apply_whitelist_change(
            action,
            target_type,
            target_id,
        )
        
        if not success:
            await update.message.reply_text(message)
            return

        # Send appropriate success message
        if target_type == 'users':
            if action == 'add':
                response_msg = c.msg_user_added(target_id)
            else:
                response_msg = c.msg_user_removed(target_id)
        else:  # groups
            if action == 'add':
                response_msg = c.msg_group_added(target_id)
            else:
                response_msg = c.msg_group_removed(target_id)
        
        await update.message.reply_text(response_msg)
        logger.info(f"Admin {user_id} {action}ed {target_type} {target_id}")
        
    except Exception as e:
        logger.error(f"Error in whitelist operation: {e}")
        await update.message.reply_text(c.MSG_ERROR_INTERNAL)


# Command wrapper functions
async def adduser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /adduser command."""
    await whitelist_command_handler(update, context, 'add', 'users')


async def removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /removeuser command."""
    await whitelist_command_handler(update, context, 'remove', 'users')


async def addgroup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /addgroup command."""
    await whitelist_command_handler(update, context, 'add', 'groups')


async def removegroup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /removegroup command."""
    await whitelist_command_handler(update, context, 'remove', 'groups')

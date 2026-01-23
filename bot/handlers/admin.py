"""
Administrative command handlers for whitelist management.
"""

import json
import sys
import os
import logging
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from bot.decorators.auth import restricted, admin_only
from bot import constants as c

logger = logging.getLogger(__name__)


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
        """Save whitelist changes to file."""
        try:
            with open(self.authorized_file, 'w') as f:
                json.dump(self.authorized_data, f, indent=2)
            logger.info("Whitelist changes saved successfully")
        except Exception as e:
            logger.error(f"Failed to save whitelist changes: {e}")
            raise RuntimeError("Failed to save changes")


# Global whitelist manager instance (will be initialized in main.py)
_whitelist_manager = None


def get_whitelist_manager() -> WhitelistManager:
    """Get the global whitelist manager instance."""
    global _whitelist_manager
    if _whitelist_manager is None:
        raise RuntimeError("WhitelistManager not initialized")
    return _whitelist_manager


def init_whitelist_manager(config) -> None:
    """Initialize the global whitelist manager."""
    global _whitelist_manager
    _whitelist_manager = WhitelistManager(config)


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
    manager = get_whitelist_manager()
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
        if action == 'add':
            success, message = manager.add_to_whitelist(target_type, target_id)
        else:  # remove
            success, message = manager.remove_from_whitelist(target_type, target_id)
        
        if not success:
            await update.message.reply_text(message)
            return
        
        # Save changes and respond
        manager.save_changes()
        
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
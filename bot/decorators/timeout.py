"""
Timeout handling decorators for async operations.
"""

import asyncio
import logging
from functools import wraps
from typing import Callable, Any, TypeVar, Awaitable

from bot import constants as c
from bot.exceptions import ConvertTimeout, DownloadTimeout, RefineTimeout, TranscribeTimeout

logger = logging.getLogger(__name__)

T = TypeVar('T')

TIMEOUT_EXCEPTIONS = {
    "download": DownloadTimeout,
    "convert": ConvertTimeout,
    "transcribe": TranscribeTimeout,
    "refine": RefineTimeout,
}


def _get_timeout_exception(stage_name: str):
    return TIMEOUT_EXCEPTIONS.get(stage_name, RefineTimeout)


def execute_with_timeout(stage_name: str, awaitable: Awaitable[T], default_timeout: int = 60) -> Awaitable[T]:
    """
    Execute an awaitable with stage-specific timeout.
    
    Args:
        stage_name: Name of the stage for timeout lookup in constants
        awaitable: The coroutine to await
        default_timeout: Default timeout in seconds if not found
        
    Returns:
        Result of the awaitable
        
    Raises:
        TimeoutError: If execution exceeds timeout
    """
    # Get timeout for this stage
    timeout_seconds = c.PROGRESS_TIMEOUTS.get(stage_name, default_timeout)
    
    async def _runner():
        try:
            logger.debug(f"Starting {stage_name} with timeout: {timeout_seconds}s")
            return await asyncio.wait_for(awaitable, timeout=timeout_seconds)
        except asyncio.TimeoutError:
            logger.error("Stage timeout | stage=%s timeout_seconds=%s", stage_name, timeout_seconds)
            raise _get_timeout_exception(stage_name)(
                f"Timeout in {stage_name}",
                getattr(c, f"MSG_TIMEOUT_{stage_name.upper()}", c.MSG_ERROR_INTERNAL),
            )
            
    return _runner()

def timeout_handler(stage_name: str, default_timeout: int = 60) -> Callable:
    """
    Decorator for timeout handling with stage-specific timeouts.
    
    Args:
        stage_name: Name of the stage for timeout lookup in constants
        default_timeout: Default timeout in seconds if not found in constants
        
    Returns:
        Wrapped function with timeout protection
    """
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            # Get timeout for this stage
            timeout_seconds = c.PROGRESS_TIMEOUTS.get(stage_name, default_timeout)
            
            try:
                logger.debug(f"Starting {stage_name} with timeout: {timeout_seconds}s")
                return await asyncio.wait_for(func(*args, **kwargs), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                logger.error("Stage timeout | stage=%s timeout_seconds=%s", stage_name, timeout_seconds)
                raise _get_timeout_exception(stage_name)(
                    f"Timeout in {stage_name}",
                    getattr(c, f"MSG_TIMEOUT_{stage_name.upper()}", c.MSG_ERROR_INTERNAL),
                )
        
        return wrapper
    return decorator

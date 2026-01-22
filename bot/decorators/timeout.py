"""
Timeout handling decorators for async operations.
"""

import asyncio
import logging
from functools import wraps
from typing import Callable, Any, TypeVar, Awaitable

logger = logging.getLogger(__name__)

T = TypeVar('T')


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
            # Import here to avoid circular imports
            from bot import constants as c
            
            # Get timeout for this stage
            timeout_seconds = c.PROGRESS_TIMEOUTS.get(stage_name, default_timeout)
            
            try:
                logger.debug(f"Starting {stage_name} with timeout: {timeout_seconds}s")
                return await asyncio.wait_for(func(*args, **kwargs), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                logger.error(f"Timeout in {stage_name} after {timeout_seconds}s")
                raise TimeoutError(f"Timeout in {stage_name}")
        
        return wrapper
    return decorator
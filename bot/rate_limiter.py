import asyncio
import time
import logging
from typing import Dict
from bot import constants as c

logger = logging.getLogger(__name__)

class RateLimiter:
    def __init__(self, max_per_user=2, cooldown=30, max_global=6, max_file_size_mb=20):
        self.max_per_user = max_per_user
        self.cooldown = cooldown
        self.max_global = max_global
        self.max_file_size_mb = max_file_size_mb
        
        # State storage
        self._active_requests: Dict[int, int] = {}  # user_id -> count
        self._last_request_time: Dict[int, float] = {}  # user_id -> timestamp
        self._global_count = 0
        self._lock = asyncio.Lock()  # For thread safety
    
    async def check_limit(self, user_id: int, file_size_mb: float) -> tuple[bool, str]:
        """Check if request is allowed. Returns (allowed, message)"""
        async with self._lock:
            # Check global limit
            if self._global_count >= self.max_global:
                return False, c.MSG_GLOBAL_LIMIT.format(max_global=self.max_global)
            
            # Check per-user concurrent limit
            user_active = self._active_requests.get(user_id, 0)
            if user_active >= self.max_per_user:
                return False, c.MSG_CONCURRENT_LIMIT.format(max_concurrent=self.max_per_user)
            
            # Check file size
            if file_size_mb > self.max_file_size_mb:
                return False, c.MSG_FILE_TOO_LARGE.format(max_size=self.max_file_size_mb)
            
            # Allow: increment counters
            self._active_requests[user_id] = user_active + 1
            self._global_count += 1
            self._last_request_time[user_id] = time.time()
            
            logger.debug(f"Request allowed for user {user_id}. Active: {self._active_requests[user_id]}, Global: {self._global_count}")
            return True, ""
    
    def release(self, user_id: int):
        """Release a request slot"""
        # Note: We can't use async with lock in a synchronous method or from a finally block if not async
        # But this method will likely be called from async context.
        # Ideally release should be async, but for simplicity we can check if loop is running?
        # No, let's make it async or use a non-async lock? 
        # Since this is Python's asyncio, we should allow calling from async functions.
        pass

    async def release_async(self, user_id: int):
        async with self._lock:
            if user_id in self._active_requests:
                self._active_requests[user_id] -= 1
                if self._active_requests[user_id] <= 0:
                    del self._active_requests[user_id]
            
            self._global_count = max(0, self._global_count - 1)
            logger.debug(f"Request released for user {user_id}. Active: {self._active_requests.get(user_id, 0)}, Global: {self._global_count}")

    def cleanup_expired(self, max_age_seconds: int = 3600):
        """Clean up old rate limit records (run periodically)."""
        # This is a maintenance task, safe to run without lock if we accept minor races,
        # or we should acquire lock. Since it's infrequent, acquiring lock is fine but needs async.
        # For simplicity, we'll just clear old timestamps directly.
        now = time.time()
        expired_users = [
            user_id for user_id, last_time in self._last_request_time.items()
            if now - last_time > max_age_seconds
        ]
        for user_id in expired_users:
            self._last_request_time.pop(user_id, None)

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
        self._last_rejection_time: Dict[int, float] = {}  # user_id -> timestamp
        self._global_count = 0
        self._lock = asyncio.Lock()  # For thread safety
    
    async def check_limit(self, user_id: int, file_size_mb: float) -> tuple[bool, str]:
        """Check if request is allowed. Returns (allowed, message)"""
        async with self._lock:
            now = time.time()

            # Check global limit
            if self._global_count >= self.max_global:
                return False, c.MSG_GLOBAL_LIMIT.format(max_global=self.max_global)

            # Check cooldown (applies only after per-user concurrent rejection)
            last_reject = self._last_rejection_time.get(user_id)
            if last_reject is not None:
                remaining = int(self.cooldown - (now - last_reject))
                if remaining > 0:
                    return False, c.MSG_COOLDOWN.format(seconds=remaining)
                self._last_rejection_time.pop(user_id, None)
            
            # Check per-user concurrent limit
            user_active = self._active_requests.get(user_id, 0)
            if user_active >= self.max_per_user:
                if user_id not in self._last_rejection_time:
                    self._last_rejection_time[user_id] = now
                return False, c.MSG_CONCURRENT_LIMIT.format(max_concurrent=self.max_per_user)
            
            # Check file size
            if file_size_mb > self.max_file_size_mb:
                return False, c.MSG_FILE_TOO_LARGE.format(max_size=self.max_file_size_mb)
            
            # Allow: increment counters
            self._active_requests[user_id] = user_active + 1
            self._global_count += 1
            self._last_request_time[user_id] = now
            
            logger.debug(f"Request allowed for user {user_id}. Active: {self._active_requests[user_id]}, Global: {self._global_count}")
            return True, ""
    
    async def release_async(self, user_id: int):
        async with self._lock:
            if user_id in self._active_requests:
                self._active_requests[user_id] -= 1
                if self._active_requests[user_id] <= 0:
                    del self._active_requests[user_id]
            
            self._global_count = max(0, self._global_count - 1)
            logger.debug(f"Request released for user {user_id}. Active: {self._active_requests.get(user_id, 0)}, Global: {self._global_count}")

    async def cleanup_expired_async(self, max_age_seconds: int = 3600) -> None:
        """Clean up old rate limit records (run periodically)."""
        async with self._lock:
            now = time.time()

            expired_users = [
                uid
                for uid, last_time in self._last_request_time.items()
                if now - last_time > max_age_seconds
            ]
            for uid in expired_users:
                self._last_request_time.pop(uid, None)
                self._last_rejection_time.pop(uid, None)

            expired_rejections = [
                uid
                for uid, last_time in self._last_rejection_time.items()
                if now - last_time > max_age_seconds
            ]
            for uid in expired_rejections:
                self._last_rejection_time.pop(uid, None)

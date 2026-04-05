import asyncio
import time
import logging
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict
from bot import constants as c

logger = logging.getLogger(__name__)


@dataclass
class QueueEntry:
    user_id: int
    event: asyncio.Event
    position: int
    granted: bool = False
    activated: bool = False


@dataclass
class AdmissionResult:
    allowed: bool
    message: str = ""
    queued: bool = False
    queue_entry: QueueEntry | None = None

class RateLimiter:
    def __init__(self, max_per_user=2, cooldown=30, max_global=6, max_file_size_mb=20, queue_enabled=True, max_queue_size=10, max_queued_per_user=1):
        self.max_per_user = max_per_user
        self.cooldown = cooldown
        self.max_global = max_global
        self.max_file_size_mb = max_file_size_mb
        self.queue_enabled = queue_enabled
        self.max_queue_size = max_queue_size
        self.max_queued_per_user = max_queued_per_user
        
        # State storage
        self._active_requests: Dict[int, int] = {}  # user_id -> count
        self._last_request_time: Dict[int, float] = {}  # user_id -> timestamp
        self._last_rejection_time: Dict[int, float] = {}  # user_id -> timestamp
        self._queued_requests: Dict[int, int] = {}  # user_id -> queued count
        self._wait_queue: Deque[QueueEntry] = deque()
        self._global_count = 0
        self._lock = asyncio.Lock()  # For thread safety

    def _activate_request_locked(self, user_id: int, now: float, increment_global: bool) -> None:
        self._active_requests[user_id] = self._active_requests.get(user_id, 0) + 1
        if increment_global:
            self._global_count += 1
        self._last_request_time[user_id] = now

    def _pop_next_queue_entry_locked(self) -> QueueEntry | None:
        while self._wait_queue:
            entry = self._wait_queue.popleft()
            queued_count = self._queued_requests.get(entry.user_id, 0)
            if queued_count > 0:
                self._queued_requests[entry.user_id] = queued_count - 1
                if self._queued_requests[entry.user_id] <= 0:
                    self._queued_requests.pop(entry.user_id, None)
                entry.granted = True
                return entry
        return None

    def _remove_queue_entry_locked(self, target: QueueEntry) -> bool:
        removed = False
        remaining: Deque[QueueEntry] = deque()
        while self._wait_queue:
            entry = self._wait_queue.popleft()
            if entry is target and not removed:
                removed = True
                queued_count = self._queued_requests.get(entry.user_id, 0)
                if queued_count > 0:
                    self._queued_requests[entry.user_id] = queued_count - 1
                    if self._queued_requests[entry.user_id] <= 0:
                        self._queued_requests.pop(entry.user_id, None)
                continue
            remaining.append(entry)
        self._wait_queue = remaining
        return removed
    
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

    async def request_admission(self, user_id: int, file_size_mb: float) -> AdmissionResult:
        async with self._lock:
            now = time.time()

            last_reject = self._last_rejection_time.get(user_id)
            if last_reject is not None:
                remaining = int(self.cooldown - (now - last_reject))
                if remaining > 0:
                    return AdmissionResult(False, c.MSG_COOLDOWN.format(seconds=remaining))
                self._last_rejection_time.pop(user_id, None)

            if file_size_mb > self.max_file_size_mb:
                return AdmissionResult(False, c.MSG_FILE_TOO_LARGE.format(max_size=self.max_file_size_mb))

            user_active = self._active_requests.get(user_id, 0)
            if user_active >= self.max_per_user:
                if user_id not in self._last_rejection_time:
                    self._last_rejection_time[user_id] = now
                return AdmissionResult(False, c.MSG_CONCURRENT_LIMIT.format(max_concurrent=self.max_per_user))

            if self._global_count < self.max_global:
                self._activate_request_locked(user_id, now, increment_global=True)
                logger.debug(f"Request allowed for user {user_id}. Active: {self._active_requests[user_id]}, Global: {self._global_count}")
                return AdmissionResult(True)

            if not self.queue_enabled:
                return AdmissionResult(False, c.MSG_GLOBAL_LIMIT.format(max_global=self.max_global))

            if self._queued_requests.get(user_id, 0) >= self.max_queued_per_user:
                return AdmissionResult(False, c.MSG_ALREADY_QUEUED)

            if len(self._wait_queue) >= self.max_queue_size:
                return AdmissionResult(False, c.MSG_QUEUE_FULL)

            position = len(self._wait_queue) + 1
            entry = QueueEntry(user_id=user_id, event=asyncio.Event(), position=position)
            self._wait_queue.append(entry)
            self._queued_requests[user_id] = self._queued_requests.get(user_id, 0) + 1
            logger.info(f"Request queued for user {user_id}. Position: {position}")
            return AdmissionResult(True, c.MSG_QUEUE_ACCEPTED.format(position=position), queued=True, queue_entry=entry)

    async def wait_for_queue_turn(self, entry: QueueEntry) -> None:
        try:
            await entry.event.wait()
            async with self._lock:
                entry.activated = True
                self._activate_request_locked(entry.user_id, time.time(), increment_global=False)
                logger.debug(
                    f"Queued request activated for user {entry.user_id}. "
                    f"Active: {self._active_requests[entry.user_id]}, Global: {self._global_count}"
                )
        except asyncio.CancelledError:
            async with self._lock:
                removed = self._remove_queue_entry_locked(entry)
                if not removed and entry.granted and not entry.activated:
                    next_entry = self._pop_next_queue_entry_locked()
                    if next_entry is not None:
                        next_entry.event.set()
                    else:
                        self._global_count = max(0, self._global_count - 1)
            raise
    
    async def release_async(self, user_id: int):
        async with self._lock:
            if user_id in self._active_requests:
                self._active_requests[user_id] -= 1
                if self._active_requests[user_id] <= 0:
                    del self._active_requests[user_id]

            next_entry = self._pop_next_queue_entry_locked()
            if next_entry is not None:
                next_entry.event.set()
            else:
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

"""Persistent rate limits for the small set of credential endpoints."""

import hashlib
import math
import time

from bot.database.v2_repository import V2Repository


class AuthAttemptLimiter:
    """Allow five attempts per endpoint and socket address every 15 minutes."""

    MAX_ATTEMPTS = 5
    WINDOW_SECONDS = 15 * 60
    _PREFIX = "v2_auth_attempt_"

    def __init__(self, repo: V2Repository, clock=time.time):
        self.repo = repo
        self.clock = clock

    def consume(self, endpoint: str, address: str) -> int | None:
        """Consume a try, or return the number of seconds until retry."""
        key = self._key(endpoint, address)
        now = int(self.clock())
        with self.repo.transaction() as connection:
            connection.execute(
                "DELETE FROM setup_state WHERE setup_key LIKE ? "
                "AND updated_at <= datetime('now', '-15 minutes')",
                (self._PREFIX + "%",),
            )
            row = connection.execute(
                "SELECT setup_value FROM setup_state WHERE setup_key=?", (key,)
            ).fetchone()
            try:
                start, attempts = (int(part) for part in row[0].split(":", 1)) if row else (now, 0)
            except (TypeError, ValueError):
                start, attempts = now, 0
            if now >= start + self.WINDOW_SECONDS:
                start, attempts = now, 0
            if attempts >= self.MAX_ATTEMPTS:
                return max(1, math.ceil(start + self.WINDOW_SECONDS - now))
            connection.execute(
                "INSERT INTO setup_state(setup_key, setup_value) VALUES (?, ?) "
                "ON CONFLICT(setup_key) DO UPDATE SET setup_value=excluded.setup_value, "
                "updated_at=datetime('now')",
                (key, f"{start}:{attempts + 1}"),
            )
        return None

    def clear(self, endpoint: str, address: str) -> None:
        with self.repo.transaction() as connection:
            connection.execute("DELETE FROM setup_state WHERE setup_key=?",
                               (self._key(endpoint, address),))

    @classmethod
    def _key(cls, endpoint: str, address: str) -> str:
        digest = hashlib.sha256(f"{endpoint}\0{address}".encode()).hexdigest()
        return f"{cls._PREFIX}{digest}"

"""Per-credential circuit breaker state shared by requests."""

import time

from bot.transcription.errors import ServiceFailure


class CircuitBreaker:
    def __init__(self, threshold: int, cooldown_seconds: int):
        self.threshold = max(1, threshold)
        self.cooldown_seconds = max(1, cooldown_seconds)
        self.failures = 0
        self.opened_at = 0.0

    async def call(self, operation, *args, **kwargs):
        if self.opened_at:
            if time.monotonic() - self.opened_at < self.cooldown_seconds:
                raise ServiceFailure("circuit_open", "provider", "Il servizio AI è temporaneamente non disponibile.")
            self.opened_at = 0.0
        try:
            result = await operation(*args, **kwargs)
        except Exception:
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = time.monotonic()
            raise
        self.failures = 0
        self.opened_at = 0.0
        return result

"""One-shot profile fallback and best-effort owner notice."""

import asyncio
import inspect
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

from bot.transcription.errors import ConfigurationInvalid, ServiceFailure, classify_provider_failure
from bot.transcription.models import ProcessingResult

_SAFE_FAILURE_CODES = frozenset({
    "provider_timeout",
    "provider_network_error",
    "provider_rate_limited",
    "provider_server_error",
    "provider_error",
    "circuit_open",
    "conversion_failed",
    "empty_response",
    "invalid_mode",
    "configuration_invalid",
})
_SAFE_STAGES = frozenset({
    "provider", "multimodal", "transcription", "refinement", "conversion", "configuration",
})
_FALLBACK_MESSAGE = "Il servizio AI non ha completato la richiesta."


@dataclass(frozen=True)
class FallbackEvent:
    """Content-free processing metadata for one primary/fallback outcome."""

    at: datetime
    primary_profile_id: int
    fallback_profile_id: int | None
    failure_stage: str | None
    failure_code: str | None
    outcome: Literal[
        "primary_succeeded", "primary_partial", "primary_failed",
        "succeeded", "partial", "failed", "timed_out",
    ]
    final_failure_stage: str | None = None
    final_failure_code: str | None = None


@dataclass(frozen=True)
class FallbackNotice:
    """Safe fields an owner-message adapter can render deterministically."""

    at: datetime
    primary_name: str
    fallback_name: str
    failure_stage: str
    failure_code: str
    final_result: Literal["succeeded", "partial", "failed"]


def format_owner_notice(notice: FallbackNotice) -> str:
    """Render a bounded Telegram notice containing only normalized metadata."""
    result = {
        "succeeded": "riuscita",
        "partial": "parziale",
        "failed": "fallita",
    }[notice.final_result]
    return (
        "Fallback usato per la trascrizione.\n"
        f"Data e ora: {notice.at.isoformat()}\n"
        f"Profilo principale: {_safe_name(notice.primary_name)}\n"
        f"Profilo fallback: {_safe_name(notice.fallback_name)}\n"
        f"Fase: {_safe_stage(notice.failure_stage)}; categoria: {_safe_failure_code(notice.failure_code)}\n"
        f"Esito finale: {result}"
    )


class FallbackProcessor:
    """Run the primary profile and, for approved failures, one full fallback.

    The caller acquires the processing snapshot before admission, queueing, or
    download and keeps its profile/credential lease alive through this call.
    The profile service guarantees that any fallback in the snapshot has a
    successful current test receipt.
    """

    def __init__(
        self,
        runner,
        *,
        record_event: Callable[[FallbackEvent], object] | None = None,
        record_notice_failure: Callable[[datetime], object] | None = None,
        send_owner_notice: Callable[[FallbackNotice], object] | None = None,
        total_timeout: float | None = None,
        notice_timeout: float = 2.0,
        cleanup_timeout: float = 0.1,
        notice_cooldown: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        if total_timeout is not None and total_timeout <= 0:
            raise ValueError("total_timeout must be positive")
        if notice_timeout <= 0:
            raise ValueError("notice_timeout must be positive")
        if cleanup_timeout <= 0:
            raise ValueError("cleanup_timeout must be positive")
        if notice_cooldown < 0:
            raise ValueError("notice_cooldown cannot be negative")
        self._runner = runner
        self._record_event = record_event
        self._record_notice_failure = record_notice_failure
        self._send_owner_notice = send_owner_notice
        self._total_timeout = total_timeout
        self._notice_timeout = notice_timeout
        self._cleanup_timeout = cleanup_timeout
        self._notice_cooldown = notice_cooldown
        self._clock = clock
        self._now = now
        self._notice_lock = asyncio.Lock()
        self._last_notice_at: float | None = None
        self._draining_tasks: set[asyncio.Task] = set()

    async def run(
        self,
        snapshot,
        audio: Path,
        *,
        send_owner_notice: Callable[[FallbackNotice], object] | None = None,
    ) -> ProcessingResult:
        """Process one audio snapshot, trying the fallback at most once."""
        primary = snapshot.primary
        fallback = snapshot.fallback
        notice_callback = send_owner_notice or self._send_owner_notice
        timeout = self._total_timeout
        if timeout is None:
            timeout = _default_total_timeout(primary, fallback)
        deadline = self._clock() + timeout

        try:
            result = await self._attempt(
                primary.configuration, audio, deadline,
                allow_partial_refinement=fallback is None,
            )
            await self._report_primary_success(snapshot, "primary_partial" if result.partial else "primary_succeeded")
            return result
        except _TotalDeadlineExceeded:
            failure = ServiceFailure("provider_timeout", "provider", _FALLBACK_MESSAGE)
            await self._report_primary_failure(snapshot, failure)
            raise failure from None
        except Exception as primary_error:
            failure = _as_provider_failure(primary_error)
            if fallback is None or not isinstance(failure, ServiceFailure) or not failure.fallback_eligible:
                if isinstance(failure, ServiceFailure):
                    diagnostic_failure = failure
                elif isinstance(failure, ConfigurationInvalid):
                    diagnostic_failure = ServiceFailure(
                        "configuration_invalid", "configuration", _FALLBACK_MESSAGE
                    )
                else:
                    diagnostic_failure = ServiceFailure("provider_error", "provider", _FALLBACK_MESSAGE)
                await self._report_primary_failure(snapshot, diagnostic_failure)
                if failure is primary_error:
                    raise
                raise failure from primary_error

        try:
            result = await self._attempt(
                fallback.configuration, audio, deadline, allow_partial_refinement=True
            )
        except _TotalDeadlineExceeded:
            final_failure = ServiceFailure("provider_timeout", "provider", _FALLBACK_MESSAGE)
            await self._report(snapshot, failure, "timed_out", final_failure, notice_callback)
            raise final_failure from None
        except Exception as fallback_error:
            final_failure = _as_provider_failure(fallback_error)
            diagnostic_failure = final_failure if isinstance(final_failure, ServiceFailure) else ServiceFailure(
                "provider_error", "provider", _FALLBACK_MESSAGE
            )
            await self._report(snapshot, failure, "failed", diagnostic_failure, notice_callback)
            if final_failure is fallback_error:
                raise
            raise final_failure from fallback_error

        outcome = "partial" if result.partial else "succeeded"
        await self._report(snapshot, failure, outcome, None, notice_callback)
        return result

    async def _attempt(
        self, configuration, audio: Path, deadline: float, *, allow_partial_refinement: bool
    ) -> ProcessingResult:
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise _TotalDeadlineExceeded

        task = asyncio.create_task(self._runner.run(
            configuration, audio, allow_partial_refinement=allow_partial_refinement
        ))
        try:
            done, _ = await asyncio.wait({task}, timeout=remaining)
        except BaseException:
            if not task.done():
                await self._cancel_and_drain(task)
            raise
        if not done:
            await self._cancel_and_drain(task)
            raise _TotalDeadlineExceeded
        return task.result()

    async def _report(
        self, snapshot, failure: ServiceFailure, outcome, final_failure, notice_callback
    ) -> None:
        at = self._timestamp()
        primary = snapshot.primary
        fallback = snapshot.fallback
        event = FallbackEvent(
            at=at,
            primary_profile_id=_safe_identifier(primary.profile_id),
            fallback_profile_id=_safe_identifier(fallback.profile_id),
            failure_stage=_safe_stage(failure.stage),
            failure_code=_safe_failure_code(failure.code),
            outcome=outcome,
            final_failure_stage=_safe_stage(final_failure.stage) if final_failure else None,
            final_failure_code=_safe_failure_code(final_failure.code) if final_failure else None,
        )
        await self._best_effort(self._record_event, event)

        if await self._reserve_notice(notice_callback):
            notice = FallbackNotice(
                at=at,
                primary_name=_safe_name(primary.name),
                fallback_name=_safe_name(fallback.name),
                failure_stage=_safe_stage(failure.stage),
                failure_code=_safe_failure_code(failure.code),
                final_result=outcome if outcome in {"succeeded", "partial"} else "failed",
            )
            delivered = await self._best_effort(notice_callback, notice)
            if not delivered:
                await self._best_effort(self._record_notice_failure, at)

    async def _report_primary_success(self, snapshot, outcome) -> None:
        primary = snapshot.primary
        await self._best_effort(self._record_event, FallbackEvent(
            at=self._timestamp(),
            primary_profile_id=_safe_identifier(primary.profile_id),
            fallback_profile_id=None,
            failure_stage=None,
            failure_code=None,
            outcome=outcome,
        ))

    async def _report_primary_failure(self, snapshot, failure: ServiceFailure) -> None:
        primary = snapshot.primary
        await self._best_effort(self._record_event, FallbackEvent(
            at=self._timestamp(),
            primary_profile_id=_safe_identifier(primary.profile_id),
            fallback_profile_id=None,
            failure_stage=_safe_stage(failure.stage),
            failure_code=_safe_failure_code(failure.code),
            outcome="primary_failed",
            final_failure_stage=_safe_stage(failure.stage),
            final_failure_code=_safe_failure_code(failure.code),
        ))

    async def _reserve_notice(self, callback) -> bool:
        if callback is None:
            return False
        async with self._notice_lock:
            now = self._clock()
            if self._last_notice_at is not None and now - self._last_notice_at < self._notice_cooldown:
                return False
            self._last_notice_at = now
            return True

    async def _best_effort(self, callback, value) -> bool:
        if callback is None:
            return False
        task = None
        try:
            result = callback(value)
            if inspect.isawaitable(result):
                task = asyncio.ensure_future(result)
                done, _ = await asyncio.wait({task}, timeout=self._notice_timeout)
                if not done:
                    await self._cancel_and_drain(task)
                    return False
                task.result()
            return True
        except Exception:
            # Diagnostics and owner notices must never change the transcription outcome.
            if task is not None and not task.done():
                await self._cancel_and_drain(task)
            return False

    async def _cancel_and_drain(self, task: asyncio.Task) -> None:
        if task.done():
            _consume_task(task)
            return
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=self._cleanup_timeout)
        if done:
            _consume_task(task)
            return
        self._draining_tasks.add(task)
        task.add_done_callback(self._draining_tasks.discard)

    def _timestamp(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class _TotalDeadlineExceeded(Exception):
    """The combined primary/fallback processing budget expired."""


def _consume_task(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        return


def _as_provider_failure(exc: Exception) -> Exception:
    if isinstance(exc, ServiceFailure):
        return exc
    if isinstance(exc, TimeoutError):
        return classify_provider_failure(exc, "provider")
    return exc


def _default_total_timeout(primary, fallback) -> float:
    try:
        return sum(_profile_timeout(profile) for profile in (primary, fallback) if profile is not None)
    except AttributeError:
        return 420.0


def _profile_timeout(profile) -> float:
    values = profile.configuration.values
    limits = values.limits
    return (
        limits.convert_timeout
        + limits.transcribe_timeout
        + (limits.refine_timeout if values.mode == "two_stage" else 0)
    )


def _safe_identifier(value: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _safe_name(value: str) -> str:
    if not isinstance(value, str):
        return "Profilo"
    cleaned = " ".join(value.split())
    return cleaned[:80] or "Profilo"


def _safe_stage(value: str) -> str:
    return value if value in _SAFE_STAGES else "provider"


def _safe_failure_code(value: str) -> str:
    return value if value in _SAFE_FAILURE_CODES else "provider_error"

import asyncio
import time

import pytest

from bot.rate_limiter import RateLimiter


async def test_shutdown_stops_admission_and_drains_with_deadline():
    limiter = RateLimiter(max_global=1, queue_enabled=False)
    admitted = await limiter.request_admission(10, 1)
    assert admitted.allowed

    await limiter.stop_admission()
    rejected = await limiter.request_admission(11, 1)
    assert not rejected.allowed

    draining = asyncio.create_task(limiter.drain(timeout=0.2))
    await asyncio.sleep(0)
    await limiter.release_async(10)
    assert await draining is True
    assert limiter._global_count == 0
    assert await limiter.drain(0) is True


async def test_queue_cancel_does_not_leak_capacity():
    limiter = RateLimiter(max_global=1, queue_enabled=True, max_queue_size=2)
    first = await limiter.request_admission(10, 1)
    queued = await limiter.request_admission(11, 1)
    assert first.allowed and queued.queued

    waiter = asyncio.create_task(limiter.wait_for_queue_turn(queued.queue_entry))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    await limiter.release_async(10)
    assert limiter._global_count == 0
    assert not limiter._wait_queue


async def test_new_requests_use_latest_limits_without_dropping_active_slots():
    from bot.transcription.models import RuntimeLimits

    limiter = RateLimiter(max_global=2, queue_enabled=True, max_queue_size=3)
    first = await limiter.request_admission(10, 1)
    second = await limiter.request_admission(11, 1)
    assert first.allowed and second.allowed

    reduced = RuntimeLimits(max_concurrent_global=1, queue_enabled=True, max_queue_size=3)
    queued = await limiter.request_admission(12, 1, limits=reduced)
    assert queued.queued
    assert limiter._global_count == 2

    await limiter.release_async(10)
    assert limiter._global_count == 1
    assert limiter._wait_queue

    await limiter.release_async(11)
    await limiter.wait_for_queue_turn(queued.queue_entry)
    assert limiter._global_count == 1
    await limiter.release_async(12)
    assert limiter._global_count == 0


async def test_expired_per_user_limiter_records_are_removed():
    limiter = RateLimiter()
    now = time.time()
    limiter._last_request_time.update({10: now - 3601, 11: now})
    limiter._last_rejection_time.update({10: now - 3601, 11: now})

    await limiter.cleanup_expired_async(max_age_seconds=3600)

    assert 10 not in limiter._last_request_time
    assert 10 not in limiter._last_rejection_time
    assert 11 in limiter._last_request_time
    assert 11 in limiter._last_rejection_time

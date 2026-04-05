import asyncio

import pytest

from bot import constants as c
from bot.rate_limiter import RateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_blocks_concurrent_user_requests():
    limiter = RateLimiter(max_per_user=1, cooldown=30, max_global=3, max_file_size_mb=20)

    allowed, message = await limiter.check_limit(user_id=10, file_size_mb=1)
    blocked, blocked_message = await limiter.check_limit(user_id=10, file_size_mb=1)

    assert allowed is True
    assert message == ""
    assert blocked is False
    assert blocked_message == c.MSG_CONCURRENT_LIMIT.format(max_concurrent=1)


@pytest.mark.asyncio
async def test_rate_limiter_enforces_cooldown_after_rejection(monkeypatch):
    limiter = RateLimiter(max_per_user=1, cooldown=30, max_global=3, max_file_size_mb=20)
    now = iter([100.0, 101.0, 110.0, 140.0])
    monkeypatch.setattr("bot.rate_limiter.time.time", lambda: next(now))

    first_allowed, _ = await limiter.check_limit(user_id=5, file_size_mb=1)
    second_allowed, second_message = await limiter.check_limit(user_id=5, file_size_mb=1)
    third_allowed, third_message = await limiter.check_limit(user_id=5, file_size_mb=1)
    await limiter.release_async(5)
    fourth_allowed, fourth_message = await limiter.check_limit(user_id=5, file_size_mb=1)

    assert first_allowed is True
    assert second_allowed is False
    assert second_message == c.MSG_CONCURRENT_LIMIT.format(max_concurrent=1)
    assert third_allowed is False
    assert third_message == c.MSG_COOLDOWN.format(seconds=21)
    assert fourth_allowed is True
    assert fourth_message == ""


@pytest.mark.asyncio
async def test_cleanup_expired_async_removes_old_records(monkeypatch):
    limiter = RateLimiter()
    limiter._last_request_time = {1: 10.0, 2: 100.0}
    limiter._last_rejection_time = {1: 15.0, 3: 20.0}
    monkeypatch.setattr("bot.rate_limiter.time.time", lambda: 5000.0)

    await limiter.cleanup_expired_async(max_age_seconds=100)

    assert limiter._last_request_time == {}
    assert limiter._last_rejection_time == {}


@pytest.mark.asyncio
async def test_rate_limiter_queues_when_global_limit_is_full():
    limiter = RateLimiter(max_per_user=2, cooldown=30, max_global=1, max_file_size_mb=20, queue_enabled=True, max_queue_size=5)

    first = await limiter.request_admission(user_id=1, file_size_mb=1)
    queued = await limiter.request_admission(user_id=2, file_size_mb=1)

    assert first.allowed is True
    assert first.queued is False
    assert queued.allowed is True
    assert queued.queued is True
    assert queued.message == c.MSG_QUEUE_ACCEPTED.format(position=1)

    wait_task = asyncio.create_task(limiter.wait_for_queue_turn(queued.queue_entry))
    await asyncio.sleep(0)
    await limiter.release_async(1)
    await wait_task

    assert limiter._active_requests[2] == 1
    assert limiter._global_count == 1


@pytest.mark.asyncio
async def test_rate_limiter_rejects_when_queue_is_full():
    limiter = RateLimiter(max_per_user=2, cooldown=30, max_global=1, max_file_size_mb=20, queue_enabled=True, max_queue_size=1)

    await limiter.request_admission(user_id=1, file_size_mb=1)
    first_queued = await limiter.request_admission(user_id=2, file_size_mb=1)
    second_queued = await limiter.request_admission(user_id=3, file_size_mb=1)

    assert first_queued.queued is True
    assert second_queued.allowed is False
    assert second_queued.message == c.MSG_QUEUE_FULL


@pytest.mark.asyncio
async def test_rate_limiter_rejects_duplicate_queued_user():
    limiter = RateLimiter(max_per_user=2, cooldown=30, max_global=1, max_file_size_mb=20, queue_enabled=True, max_queue_size=5, max_queued_per_user=1)

    await limiter.request_admission(user_id=1, file_size_mb=1)
    first_queued = await limiter.request_admission(user_id=2, file_size_mb=1)
    second_queued = await limiter.request_admission(user_id=2, file_size_mb=1)

    assert first_queued.queued is True
    assert second_queued.allowed is False
    assert second_queued.message == c.MSG_ALREADY_QUEUED

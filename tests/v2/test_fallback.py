import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.transcription.errors import ConfigurationInvalid, ServiceFailure
from bot.transcription.fallback import FallbackProcessor, format_owner_notice
from bot.transcription.models import ProcessingResult


@dataclass(frozen=True)
class ProfileSnapshot:
    profile_id: str
    name: str
    configuration: SimpleNamespace


@dataclass(frozen=True)
class ProcessingSnapshot:
    primary: ProfileSnapshot
    fallback: ProfileSnapshot | None


def _snapshot(*, fallback=True):
    primary = ProfileSnapshot(1, "Primary profile", SimpleNamespace(number=11))
    backup = (
        ProfileSnapshot(2, "Fallback profile", SimpleNamespace(number=22))
        if fallback else None
    )
    return ProcessingSnapshot(primary, backup)


def _result(text="synthetic transcript"):
    return ProcessingResult(text, False, ("synthetic-model",), 12)


class SequenceRunner:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def run(self, configuration, audio, *, allow_partial_refinement=True):
        self.calls.append((configuration.number, Path(audio)))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


async def test_primary_success_skips_fallback_and_owner_notice(tmp_path):
    result = _result()
    runner = SequenceRunner([result])
    notices = []
    events = []
    processor = FallbackProcessor(
        runner,
        record_event=events.append,
        send_owner_notice=lambda notice: notices.append(notice),
    )

    assert await processor.run(_snapshot(), tmp_path / "synthetic-audio.wav") == result
    assert [call[0] for call in runner.calls] == [11]
    assert notices == []
    assert len(events) == 1
    assert events[0].primary_profile_id == 1
    assert events[0].fallback_profile_id is None
    assert events[0].failure_stage is None
    assert events[0].failure_code is None
    assert events[0].outcome == "primary_succeeded"


async def test_eligible_failure_runs_one_fallback_and_emits_safe_metadata(tmp_path):
    result = _result()
    runner = SequenceRunner([
        ServiceFailure("provider_rate_limited", "transcription", "safe provider error"),
        result,
    ])
    events, notices = [], []
    processor = FallbackProcessor(
        runner,
        record_event=events.append,
        send_owner_notice=notices.append,
        now=lambda: datetime(2026, 10, 29, tzinfo=timezone.utc),
    )

    actual = await processor.run(_snapshot(), tmp_path / "synthetic-audio.wav")
    assert actual == result
    assert [call[0] for call in runner.calls] == [11, 22]
    assert len(events) == len(notices) == 1
    assert events[0].primary_profile_id == 1
    assert events[0].fallback_profile_id == 2
    assert events[0].failure_code == "provider_rate_limited"
    assert events[0].failure_stage == "transcription"
    assert events[0].outcome == "succeeded"
    assert notices[0].primary_name == "Primary profile"
    assert notices[0].fallback_name == "Fallback profile"
    assert notices[0].failure_code == "provider_rate_limited"
    serialized = repr((events[0], notices[0]))
    assert "synthetic transcript" not in serialized
    assert "synthetic-audio" not in serialized
    assert "safe provider error" not in serialized


async def test_partial_fallback_is_reported_as_partial(tmp_path):
    partial = ProcessingResult("synthetic partial", True, ("synthetic-model",), 12)
    runner = SequenceRunner([
        ServiceFailure("provider_server_error", "transcription", "safe primary error"),
        partial,
    ])
    events, notices = [], []
    processor = FallbackProcessor(runner, record_event=events.append, send_owner_notice=notices.append)

    result = await processor.run(_snapshot(), tmp_path / "synthetic-audio.wav")

    assert result.partial is True
    assert events[0].outcome == "partial"
    assert notices[0].final_result == "partial"
    assert "Esito finale: parziale" in format_owner_notice(notices[0])


async def test_ineligible_failure_does_not_attempt_fallback(tmp_path):
    runner = SequenceRunner([
        ServiceFailure("empty_response", "transcription", "Il servizio AI non ha restituito testo."),
        _result(),
    ])
    events, notices = [], []
    processor = FallbackProcessor(runner, record_event=events.append, send_owner_notice=notices.append)

    with pytest.raises(ServiceFailure) as captured:
        await processor.run(_snapshot(), tmp_path / "synthetic-audio.wav")
    assert captured.value.code == "empty_response"
    assert [call[0] for call in runner.calls] == [11]
    assert len(events) == 1
    assert events[0].primary_profile_id == 1
    assert events[0].fallback_profile_id is None
    assert events[0].failure_stage == "transcription"
    assert events[0].failure_code == "empty_response"
    assert events[0].outcome == "primary_failed"
    assert notices == []


async def test_configuration_error_is_diagnosed_without_exposing_its_message(tmp_path):
    configuration_error = ConfigurationInvalid("synthetic credential details")
    events = []
    processor = FallbackProcessor(
        SequenceRunner([configuration_error]), record_event=events.append
    )

    with pytest.raises(ConfigurationInvalid) as captured:
        await processor.run(_snapshot(), tmp_path / "synthetic-audio.wav")

    assert captured.value is configuration_error
    assert events[0].failure_stage == "configuration"
    assert events[0].failure_code == "configuration_invalid"
    assert events[0].outcome == "primary_failed"
    assert "synthetic credential details" not in repr(events[0])


async def test_absent_fallback_preserves_primary_failure(tmp_path):
    failure = ServiceFailure("provider_server_error", "refinement", "safe provider error")
    runner = SequenceRunner([failure])
    notices = []
    events = []
    processor = FallbackProcessor(
        runner, record_event=events.append, send_owner_notice=notices.append
    )

    with pytest.raises(ServiceFailure) as captured:
        await processor.run(_snapshot(fallback=False), tmp_path / "synthetic-audio.wav")
    assert captured.value is failure
    assert [call[0] for call in runner.calls] == [11]
    assert notices == []
    assert len(events) == 1
    assert events[0].primary_profile_id == 1
    assert events[0].fallback_profile_id is None
    assert events[0].failure_stage == "refinement"
    assert events[0].failure_code == "provider_server_error"
    assert events[0].outcome == "primary_failed"


async def test_primary_partial_success_is_recorded_without_fallback(tmp_path):
    partial = ProcessingResult("synthetic partial", True, ("synthetic-model",), 12)
    events = []
    processor = FallbackProcessor(SequenceRunner([partial]), record_event=events.append)

    assert await processor.run(_snapshot(fallback=False), tmp_path / "synthetic-audio.wav") == partial
    assert events[0].outcome == "primary_partial"


async def test_fallback_failure_is_not_retried_and_is_reported(tmp_path):
    runner = SequenceRunner([
        ServiceFailure("provider_network_error", "transcription", "safe primary error"),
        ServiceFailure("provider_server_error", "transcription", "safe fallback error"),
        _result(),
    ])
    events, notices = [], []
    processor = FallbackProcessor(runner, record_event=events.append, send_owner_notice=notices.append)

    with pytest.raises(ServiceFailure) as captured:
        await processor.run(_snapshot(), tmp_path / "synthetic-audio.wav")
    assert captured.value.code == "provider_server_error"
    assert [call[0] for call in runner.calls] == [11, 22]
    assert events[0].outcome == "failed"
    assert notices[0].final_result == "failed"


async def test_unexpected_fallback_error_is_preserved_when_reporting(tmp_path):
    fallback_error = RuntimeError("synthetic provider body")
    runner = SequenceRunner([
        ServiceFailure("provider_network_error", "transcription", "safe primary error"),
        fallback_error,
    ])
    events = []
    processor = FallbackProcessor(runner, record_event=events.append)

    with pytest.raises(RuntimeError) as captured:
        await processor.run(_snapshot(), tmp_path / "synthetic-audio.wav")

    assert captured.value is fallback_error
    assert events[0].outcome == "failed"
    assert events[0].final_failure_stage == "provider"
    assert events[0].final_failure_code == "provider_error"
    assert "synthetic provider body" not in repr(events[0])


async def test_total_timeout_cancels_fallback_without_leaving_task_pending(tmp_path):
    canceled = asyncio.Event()

    class Runner:
        def __init__(self):
            self.calls = []
            self.fallback_task = None

        async def run(self, configuration, audio, *, allow_partial_refinement=True):
            self.calls.append(configuration.number)
            if configuration.number == 11:
                raise ServiceFailure("provider_timeout", "transcription", "safe timeout")
            self.fallback_task = asyncio.current_task()
            try:
                await asyncio.Event().wait()
            finally:
                canceled.set()

    runner = Runner()
    processor = FallbackProcessor(runner, total_timeout=0.02)

    with pytest.raises(ServiceFailure) as captured:
        await processor.run(_snapshot(), tmp_path / "synthetic-audio.wav")
    assert captured.value.code == "provider_timeout"
    assert runner.calls == [11, 22]
    assert canceled.is_set()
    assert runner.fallback_task.done()


async def test_notification_timeout_does_not_change_success_or_leave_task_pending(tmp_path):
    canceled = asyncio.Event()

    async def notify(notice):
        try:
            await asyncio.Event().wait()
        finally:
            canceled.set()

    runner = SequenceRunner([
        ServiceFailure("provider_server_error", "transcription", "safe provider error"),
        _result(),
    ])
    processor = FallbackProcessor(runner, send_owner_notice=notify, notice_timeout=0.01)

    result = await processor.run(_snapshot(), tmp_path / "synthetic-audio.wav")
    assert result.text == "synthetic transcript"
    assert canceled.is_set()


async def test_failed_notification_is_recorded_without_changing_fallback_result(tmp_path):
    failures = []
    runner = SequenceRunner([
        ServiceFailure("provider_server_error", "transcription", "safe provider error"),
        _result(),
    ])

    async def notify(notice):
        raise RuntimeError("synthetic Telegram failure body")

    processor = FallbackProcessor(
        runner, send_owner_notice=notify,
        record_notice_failure=failures.append,
    )
    result = await processor.run(_snapshot(), tmp_path / "synthetic-audio.wav")

    assert result.text == "synthetic transcript"
    assert len(failures) == 1
    assert isinstance(failures[0], datetime)


async def test_timed_out_notification_is_recorded_without_changing_fallback_result(tmp_path):
    failures = []
    runner = SequenceRunner([
        ServiceFailure("provider_server_error", "transcription", "safe provider error"),
        _result(),
    ])

    async def notify(notice):
        await asyncio.Event().wait()

    processor = FallbackProcessor(
        runner, send_owner_notice=notify, record_notice_failure=failures.append,
        notice_timeout=0.01,
    )
    result = await processor.run(_snapshot(), tmp_path / "synthetic-audio.wav")

    assert result.text == "synthetic transcript"
    assert len(failures) == 1


async def test_notification_is_rate_limited_for_repeated_same_failure(tmp_path):
    notices = []
    events = []

    runner = SequenceRunner([
        ServiceFailure("provider_rate_limited", "transcription", "safe provider error"),
        _result(),
        ServiceFailure("provider_rate_limited", "transcription", "safe provider error"),
        _result(),
    ])

    processor = FallbackProcessor(
        runner,
        record_event=events.append,
        send_owner_notice=notices.append,
        notice_cooldown=60,
        clock=lambda: 100.0,
    )
    await processor.run(_snapshot(), tmp_path / "synthetic-first.wav")
    await processor.run(_snapshot(), tmp_path / "synthetic-second.wav")
    assert len(events) == 2
    assert len(notices) == 1


async def test_per_call_notice_callback_can_use_request_context(tmp_path):
    notices = []
    runner = SequenceRunner([
        ServiceFailure("provider_server_error", "transcription", "safe provider error"),
        _result(),
    ])
    processor = FallbackProcessor(runner)

    await processor.run(
        _snapshot(), tmp_path / "synthetic-audio.wav",
        send_owner_notice=notices.append,
    )

    assert len(notices) == 1
    assert notices[0].fallback_name == "Fallback profile"


async def test_runner_timeout_is_classified_as_provider_timeout_and_falls_back(tmp_path):
    runner = SequenceRunner([TimeoutError("synthetic timeout"), _result()])
    processor = FallbackProcessor(runner)

    result = await processor.run(_snapshot(), tmp_path / "synthetic-audio.wav")
    assert result.text == "synthetic transcript"
    assert [call[0] for call in runner.calls] == [11, 22]


async def test_runner_that_suppresses_cancel_does_not_hold_request_open(tmp_path):
    release = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.call_later(0.15, release.set)

    class Runner:
        async def run(self, configuration, audio, *, allow_partial_refinement=True):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
                return _result()

    processor = FallbackProcessor(Runner(), total_timeout=0.01, cleanup_timeout=0.02)
    started = loop.time()
    with pytest.raises(ServiceFailure):
        await processor.run(_snapshot(fallback=False), tmp_path / "synthetic-audio.wav")
    elapsed = loop.time() - started
    assert elapsed < 0.08
    assert processor._draining_tasks
    await asyncio.gather(*processor._draining_tasks)
    assert not processor._draining_tasks

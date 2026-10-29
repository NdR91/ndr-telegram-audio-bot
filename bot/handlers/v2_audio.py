"""Telegram audio path backed by the immutable V2 active revision."""

import asyncio
import logging
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

from bot.transcription.errors import ConfigurationInvalid, ServiceFailure
from bot.diagnostics import DiagnosticEvent
from bot.ui.delivery import TelegramDeliveryAdapter

logger = logging.getLogger(__name__)
_MAX_REMEMBERED_UPDATES = 1000


def _attachment(message):
    if getattr(message, "voice", None) is not None:
        return message.voice, ".ogg"
    audio = getattr(message, "audio", None)
    if audio is not None:
        return audio, _safe_extension(getattr(audio, "file_name", None))
    document = getattr(message, "document", None)
    if document is not None and (getattr(document, "mime_type", "") or "").startswith("audio/"):
        return document, _safe_extension(getattr(document, "file_name", None))
    return None, None


def _safe_extension(file_name: str | None) -> str:
    extension = Path(file_name or "audio.mp3").suffix.lower()
    return extension if extension and len(extension) <= 12 else ".mp3"


async def _reply(message, text: str) -> None:
    try:
        await message.reply_text(text)
    except Exception:
        logger.info("Could not send Telegram processing notice")


def _update_registry(context):
    lock = context.bot_data.setdefault("v2_update_lock", asyncio.Lock())
    in_flight = context.bot_data.setdefault("v2_inflight_updates", set())
    completed = context.bot_data.setdefault("v2_completed_updates", OrderedDict())
    return lock, in_flight, completed


async def handle_audio(update, context) -> None:
    """Track the PTB handler task so shutdown can cancel and drain it safely."""
    task = asyncio.current_task()
    tasks = context.bot_data.setdefault("v2_audio_tasks", set())
    if task is not None:
        tasks.add(task)
    try:
        await _handle_audio(update, context)
    finally:
        if task is not None:
            tasks.discard(task)


async def _handle_audio(update, context) -> None:
    if getattr(update, "edited_message", None) is not None:
        return
    message = getattr(update, "message", None)
    if message is None:
        return
    user = getattr(message, "from_user", None)
    if user is None or getattr(user, "is_bot", False):
        return
    attachment, extension = _attachment(message)
    if attachment is None:
        return

    services = context.bot_data.get("v2_services")
    if services is None:
        return
    chat = message.chat
    user_id = int(user.id)
    chat_id = int(chat.id)
    if not services.access.allowed(user_id, chat_id, chat.type):
        return
    if chat.type in {"group", "supergroup"} and getattr(message, "voice", None) is not None:
        services.access.mark_received(chat_id, datetime.now(timezone.utc))

    key = (chat_id, int(message.message_id))
    lock, in_flight, completed = _update_registry(context)
    async with lock:
        if key in in_flight or key in completed:
            return
        in_flight.add(key)

    try:
        await _process_authorized_audio(message, attachment, extension, user_id, chat_id, services, context)
    finally:
        async with lock:
            in_flight.discard(key)
            completed[key] = None
            completed.move_to_end(key)
            while len(completed) > _MAX_REMEMBERED_UPDATES:
                completed.popitem(last=False)


async def _process_authorized_audio(message, attachment, extension, user_id, chat_id, services, context):
    started = time.monotonic()
    snapshot = services.configuration.active()
    if snapshot is None:
        _record_diagnostic(services, None, "error", (), 0, "configuration")
        await _reply(message, "La trascrizione non è ancora configurata.")
        return

    file_size = getattr(attachment, "file_size", None) or 0
    size_mb = file_size / (1024 * 1024)
    limiter = context.bot_data.get("rate_limiter")
    if limiter is None:
        _record_diagnostic(services, snapshot, "error", (), 0, "temporary")
        await _reply(message, "Il servizio di trascrizione non è disponibile.")
        return

    temp_dir = Path(services.settings.data_dir) / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    audio_path = temp_dir / f"telegram-{chat_id}-{message.message_id}-{uuid.uuid4().hex}{extension}"
    admitted = False
    ack_message = None
    cancelled = False
    try:
        # Keep the selected provider key alive for the entire queue and run.
        with services.repo.credential_lease(snapshot.values.service, snapshot.values.credential_version):
            admission = await limiter.request_admission(
                user_id, size_mb, limits=snapshot.values.limits
            )
            if not admission.allowed:
                _record_diagnostic(services, snapshot, "error", (),
                                   int((time.monotonic() - started) * 1000), "quota")
                await _reply(message, admission.message)
                return
            if admission.queued:
                await _reply(message, admission.message)
                await limiter.wait_for_queue_turn(admission.queue_entry)
            admitted = True
            ack_message = await message.reply_text("🔄 Audio ricevuto, sto elaborando…")
            telegram_file = await asyncio.wait_for(
                attachment.get_file(), timeout=snapshot.values.limits.download_timeout
            )
            await asyncio.wait_for(
                telegram_file.download_to_drive(custom_path=str(audio_path)),
                timeout=snapshot.values.limits.download_timeout,
            )
            if audio_path.stat().st_size > snapshot.values.limits.max_file_size_mb * 1024 * 1024:
                _record_diagnostic(services, snapshot, "error", (),
                                   int((time.monotonic() - started) * 1000), "quota")
                await _reply(message, "Il file supera il limite configurato.")
                return
            result = await services.runner.run(snapshot, audio_path)
            delivery = context.bot_data.get("delivery_adapter") or TelegramDeliveryAdapter()
            await delivery.send_final_response(
                context,
                chat_id=chat_id,
                ack_msg=ack_message,
                full_text=result.text,
                reply_to_message_id=int(message.message_id),
                message_thread_id=getattr(message, "message_thread_id", None),
                partial=result.partial,
            )
            _record_diagnostic(
                services, snapshot, "partial" if result.partial else "success",
                result.models, int((time.monotonic() - started) * 1000), None,
            )
    except asyncio.CancelledError:
        cancelled = True
        raise
    except asyncio.TimeoutError:
        _record_diagnostic(services, snapshot, "error", (),
                           int((time.monotonic() - started) * 1000), "temporary")
        await _reply(message, "⏰ Download audio scaduto. Riprova con un file più piccolo.")
    except ServiceFailure as exc:
        _record_diagnostic(services, snapshot, "error", (),
                           int((time.monotonic() - started) * 1000), _diagnostic_code(exc.code))
        await _reply(message, exc.safe_message)
    except ConfigurationInvalid:
        _record_diagnostic(services, snapshot, "error", (),
                           int((time.monotonic() - started) * 1000), "credential")
        await _reply(message, "Non è stato possibile usare la configurazione salvata.")
    except Exception as exc:
        logger.warning("V2 Telegram audio request failed (%s)", type(exc).__name__)
        _record_diagnostic(services, snapshot, "error", (),
                           int((time.monotonic() - started) * 1000), "telegram")
        await _reply(message, "Non è stato possibile elaborare questo audio.")
    finally:
        try:
            audio_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove temporary Telegram audio file")
        if admitted:
            deadline = context.bot_data.get("v2_shutdown_deadline")
            cleanup_deadline = deadline if deadline is not None else asyncio.get_running_loop().time() + 0.2
            released = await _best_effort_await(
                limiter.release_async(user_id), cleanup_deadline, "admission release"
            )
            if not released:
                logger.warning("Could not release a Telegram audio admission slot before cleanup deadline")
        if cancelled and ack_message is not None:
            deadline = context.bot_data.get("v2_shutdown_deadline")
            if deadline is None:
                deadline = asyncio.get_running_loop().time() + 0.2
            deleted = await _best_effort_await(ack_message.delete(), deadline, "processing acknowledgement cleanup")
            if not deleted:
                logger.info("Could not remove the Telegram processing acknowledgement during cancellation")


def _diagnostic_code(code: str) -> str:
    return {
        "provider_error": "provider_error",
        "circuit_open": "temporary",
        "conversion_failed": "format",
        "invalid_mode": "configuration",
        "empty_response": "provider",
    }.get(code, "unknown")


def _record_diagnostic(services, snapshot, status, models, duration_ms, error_code) -> None:
    try:
        services.diagnostics.record(DiagnosticEvent(
            datetime.now(timezone.utc), "telegram", status,
            snapshot.number if snapshot is not None else None,
            tuple(models), max(0, duration_ms), error_code,
        ))
    except Exception:
        logger.info("Could not persist a normalized V2 diagnostic event")


async def _best_effort_await(awaitable, deadline: float, label: str) -> bool:
    """Bound cleanup without waiting for a coroutine that suppresses cancellation."""
    task = asyncio.ensure_future(awaitable)
    done, _ = await asyncio.wait(
        (task,), timeout=max(0.0, deadline - asyncio.get_running_loop().time())
    )
    if task not in done:
        task.cancel()
        task.add_done_callback(_consume_task_result)
        logger.debug("Best-effort Telegram %s exceeded its cleanup deadline", label)
        return False
    try:
        task.result()
    except BaseException:
        return False
    return True


def _consume_task_result(task: asyncio.Future) -> None:
    try:
        task.result()
    except BaseException:
        pass

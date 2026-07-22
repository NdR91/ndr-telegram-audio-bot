"""
Audio processing handler for Telegram bot.
"""

import os
import sys
import logging
import asyncio
import time
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from bot.capabilities import CapabilityModel
from bot.decorators.auth import restricted
from bot.decorators.timeout import execute_with_timeout
from bot.decorators.rate_limit import rate_limited
from bot.exceptions import (
    AudioPipelineError,
    AudioPipelineStageError,
    AudioPipelineTimeout,
    DownloadError,
    PipelineResolutionError,
)
from bot.pipeline_resolver import PipelineRequest, RequestMode
from bot.providers import RefineStreamEvent, TextProcessor, Transcriber, TranscriptionResult
from bot.ui.progress import update_progress, get_progress_message, clear_progress_cache, remember_progress_message
from bot import utils
from bot import constants as c
logger = logging.getLogger(__name__)


def _elapsed_ms(start_time: float) -> int:
    return int((time.monotonic() - start_time) * 1000)


def _log_stage_success(user_id: int, stage_name: str, start_time: float) -> None:
    logger.info(
        "Audio stage completed | user_id=%s stage=%s duration_ms=%s",
        user_id,
        stage_name,
        _elapsed_ms(start_time),
    )


def _log_pipeline_summary(user_id: int, provider_name: str, total_start_time: float, status: str) -> None:
    logger.info(
        "Audio pipeline finished | user_id=%s provider=%s status=%s duration_ms=%s",
        user_id,
        provider_name,
        status,
        _elapsed_ms(total_start_time),
    )


def get_audio_processor(context: ContextTypes.DEFAULT_TYPE) -> "AudioProcessor":
    """Get the application-scoped audio processor instance."""
    processor = context.bot_data.get('audio_processor')
    if processor is None:
        raise RuntimeError("AudioProcessor not initialized")
    return processor


def get_delivery_adapter(context: ContextTypes.DEFAULT_TYPE):
    adapter = context.bot_data.get('delivery_adapter')
    if adapter is None:
        raise RuntimeError("TelegramDeliveryAdapter not initialized")
    return adapter


def get_state_checker(context: ContextTypes.DEFAULT_TYPE):
    """Get the application-scoped state checker instance."""
    checker = context.bot_data.get('state_checker')
    if checker is None:
        raise RuntimeError("StateChecker not initialized")
    return checker


class AudioProcessor:
    """
    Handles audio file processing pipeline.

    Accepts optional :class:`Transcriber` and :class:`TextProcessor`
    instances (P1).  When not provided, falls back to the combined
    :class:`LLMProvider` created by :func:`utils.create_provider`.
    """

    def __init__(
        self,
        config,
        transcriber: Transcriber | None = None,
        text_processor: TextProcessor | None = None,
        provider_name: str | None = None,
        model_name: str | None = None,
    ):
        """Initialize audio processor with configuration.

        Parameters
        ----------
        config:
            Bot configuration object.
        transcriber:
            Optional P1 :class:`Transcriber` instance.  When ``None``,
            the combined provider created by ``utils.create_provider``
            is used for all operations (backward-compatible mode).
        text_processor:
            Optional P1 :class:`TextProcessor` instance.
        provider_name:
            Provider name for logging / display (required when *transcriber*
            is provided).
        model_name:
            Model name for response formatting (required when *text_processor*
            is provided).
        """
        self.config = config
        self._transcriber = transcriber
        self._text_processor = text_processor
        self._model_name_override = model_name

        if transcriber is None:
            # Legacy mode: create the combined provider.
            self.provider = utils.create_provider(config)
            self._provider_name = config.provider_name
        else:
            # P1 mode: use separate transcriber / text_processor.
            self.provider = object()  # sentinel for backward-compat checks
            self._provider_name = provider_name or "unknown"

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def capabilities(self) -> CapabilityModel:
        """Return the resolved :class:`CapabilityModel` for this processor."""
        if self._text_processor is not None:
            return self._text_processor.get_capabilities()
        provider = self.provider
        if isinstance(provider, object) and type(provider).__name__ == "object":
            # Sentinel — no capability information available.
            return CapabilityModel()
        caps = getattr(provider, "get_capabilities", None)
        if callable(caps):
            return caps()
        return CapabilityModel(transcription=True)

    @property
    def transcribe_accepted_formats(self) -> frozenset[str]:
        """Return the set of file extensions the transcriber accepts natively.

        When the source file extension is in this set, FFmpeg conversion
        can be skipped and the file passed directly to ``transcribe_audio``.
        """
        if self._transcriber is not None:
            return self._transcriber.accepted_formats()
        provider = self.provider
        fn = getattr(provider, "accepted_formats", None)
        if callable(fn):
            return fn()
        return frozenset({"mp3"})

    @property
    def supports_refine_streaming(self) -> bool:
        """Return ``True`` when the text processor supports streaming.

        Delegates to :meth:`capabilities` (P2).
        """
        return self.capabilities.streaming_refinement

    async def determine_file_type(self, message) -> tuple[Optional[str], Optional[str]]:
        """
        Determine file type and get file object from message.
        
        Args:
            message: Telegram message object
            
        Returns:
            Tuple of (file_object, file_extension) or (None, None) if unsupported
        """
        if message.voice:
            return await message.voice.get_file(), 'ogg'
        elif message.audio:
            file_name = message.audio.file_name or 'audio.mp3'
            ext = os.path.splitext(file_name)[1].lstrip('.') or 'mp3'
            return await message.audio.get_file(), ext
        elif message.document and message.document.mime_type.startswith('audio/'):
            file_name = message.document.file_name or 'audio.mp3'
            ext = os.path.splitext(file_name)[1].lstrip('.') or 'mp3'
            return await message.document.get_file(), ext
        
        return None, None
    
    def generate_file_paths(
        self, chat_id: int, message_id: int, unique_id: str, ext: str
    ) -> tuple[str, str]:
        """
        Generate file paths for temporary audio files.
        
        Args:
            unique_id: Unique ID from Telegram
            ext: File extension
            
        Returns:
            Tuple of (ogg_path, mp3_path)
        """
        prefix = f"{chat_id}_{message_id}_{unique_id}"
        ogg_path = os.path.join(self.config.audio_dir, f"{prefix}.{ext}")
        mp3_path = os.path.join(self.config.audio_dir, f"{prefix}.mp3")
        return ogg_path, mp3_path
    
    async def download_audio(self, file_obj, file_path: str) -> None:
        """Download audio file with timeout protection."""
        try:
            await execute_with_timeout(
                "download",
                file_obj.download_to_drive(file_path)
            )
        except AudioPipelineTimeout:
            raise
        except Exception as e:
            raise DownloadError(f"Download failed: {e}", c.MSG_ERROR_DOWNLOAD) from e
    
    async def convert_audio(self, ogg_path: str, mp3_path: str) -> None:
        """Convert audio to MP3 with timeout protection."""
        await execute_with_timeout(
            "convert",
            utils.convert_to_mp3(ogg_path, mp3_path)
        )
    
    async def transcribe_audio(self, mp3_path: str) -> str:
        """Transcribe audio with timeout protection."""
        if self._transcriber is not None:
            result = await execute_with_timeout(
                "transcribe",
                self._transcriber.transcribe(mp3_path),
            )
            return result.text
        return await execute_with_timeout(
            "transcribe",
            self.provider.transcribe_audio(mp3_path),
        )

    async def refine_text(self, raw_text: str) -> str:
        """Refine transcribed text with timeout protection."""
        if self._text_processor is not None:
            return await execute_with_timeout(
                "refine",
                self._text_processor.process(raw_text),
            )
        return await execute_with_timeout(
            "refine",
            self.provider.refine_text(raw_text),
        )

    async def stream_refine_text(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        ack_msg,
        raw_text: str,
    ) -> str:
        delivery_adapter = get_delivery_adapter(context)
        session = delivery_adapter.start_progressive_response(context, chat_id, ack_msg)
        final_text = ""

        stream = (
            self._text_processor.stream_process(raw_text)
            if self._text_processor is not None
            else self.provider.stream_refine_text(raw_text)
        )

        async for event in stream:
            if event.type == "delta":
                await delivery_adapter.push_progressive_delta(context, session, event.text)
            elif event.type == "done":
                final_text = event.text

        if not final_text:
            final_text = session.accumulated_text

        full_text = self.format_response(final_text)
        await delivery_adapter.finalize_progressive_response(context, session, full_text)
        return final_text
    
    def format_response(self, final_text: str) -> str:
        """Format final response text with header."""
        try:
            if self._model_name_override:
                model_name = self._model_name_override
            elif not isinstance(self.provider, object):
                model_name = getattr(self.provider, "model_name", "unknown")
            else:
                model_name = "unknown"
        except Exception:
            model_name = "unknown"

        header = c.MSG_COMPLETION_HEADER.format(model_name=model_name)
        return f"{header}\n\n{final_text}"
    
    async def send_response(self, context: ContextTypes.DEFAULT_TYPE, 
                          chat_id: int, ack_msg, full_text: str) -> None:
        """Send response, handling message length limits."""
        delivery_adapter = get_delivery_adapter(context)
        await delivery_adapter.send_final_response(context, chat_id, ack_msg, full_text)
    
    def cleanup_files(self, ogg_path: str, mp3_path: str) -> None:
        """Clean up temporary audio files."""
        for file_path in [ogg_path, mp3_path]:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.debug(f"Cleaned up temporary file: {file_path}")
                except Exception as e:
                    logger.warning(f"Failed to cleanup {file_path}: {e}")


@restricted
@rate_limited
async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle audio messages and process them through the transcription pipeline.
    
    Args:
        update: Telegram update object
        context: Telegram context object
    """
    message = update.message

    # Gate: reject audio when the pipeline is not ready.
    try:
        checker = get_state_checker(context)
        if not checker.can_process_audio():
            info = checker.get_state()
            await message.reply_text(
                f"⚠️ {info.description}\n\n{info.next_action}"
            )
            return
    except RuntimeError:
        logger.warning("StateChecker not available; allowing audio processing")

    user_id = message.from_user.id
    chat_id = message.chat_id

    # P4 — Resolve the pipeline for this specific request.
    resolver = context.bot_data.get('pipeline_resolver')
    if resolver is not None:
        try:
            request = PipelineRequest(
                mode=RequestMode.FULL,
                user_id=user_id,
                chat_id=chat_id,
            )
            plan = resolver.resolve(request)
            processor = AudioProcessor(
                context.bot_data.get('config'),
                transcriber=plan.transcriber,
                text_processor=plan.text_processor,
                provider_name=plan.provider_name,
                model_name=plan.model_name,
            )
            logger.info(
                "Pipeline resolved for user=%s: %s",
                user_id,
                "; ".join(plan.resolution_log),
            )
        except PipelineResolutionError as e:
            await message.reply_text(f"⚠️ {e.user_message}")
            return
        except Exception as e:
            logger.error("Pipeline resolution failed: %s", e)
            processor = get_audio_processor(context)
    else:
        # No resolver — fall back to the statically configured processor.
        processor = get_audio_processor(context)

    total_start_time = time.monotonic()
    streamed_refine_delivery = False
    
    # Determine file type and get file object
    file_obj, ext = await processor.determine_file_type(message)
    if not file_obj:
        await message.reply_text(c.MSG_UNSUPPORTED_TYPE)
        return
    
    # Generate file paths
    unique_id = message.effective_attachment.file_unique_id
    ogg_path, mp3_path = processor.generate_file_paths(
        message.chat_id, message.message_id, unique_id, ext
    )
    
    # Initial progress message
    total_stages = len(c.PROGRESS_STAGES)
    initial_progress = get_progress_message(c.MSG_PROGRESS_DOWNLOAD, 1, total_stages)
    ack_msg = await message.reply_text(initial_progress)
    remember_progress_message(message.chat_id, ack_msg.message_id, initial_progress)
    
    try:
        # Stage 1: Download
        await update_progress(
            context, message.chat_id, ack_msg.message_id,
            get_progress_message(c.MSG_PROGRESS_DOWNLOAD, 1, total_stages)
        )
        stage_start_time = time.monotonic()
        await processor.download_audio(file_obj, ogg_path)
        _log_stage_success(user_id, "download", stage_start_time)
        
        # Stage 2: Convert to MP3 when the transcriber does not accept the
        #          original format natively (P7 — capability-aware audio prep).
        ext_lower = ext.lower()
        if ext_lower not in processor.transcribe_accepted_formats:
            await update_progress(
                context, message.chat_id, ack_msg.message_id,
                get_progress_message(c.MSG_PROGRESS_CONVERT, 2, total_stages)
            )
            stage_start_time = time.monotonic()
            await processor.convert_audio(ogg_path, mp3_path)
            _log_stage_success(user_id, "convert", stage_start_time)
            transcribe_path = mp3_path
        else:
            logger.info(
                "Skipping conversion: format .%s accepted by transcriber",
                ext_lower,
            )
            transcribe_path = ogg_path

        # Stage 3: Transcribe
        await update_progress(
            context, message.chat_id, ack_msg.message_id,
            get_progress_message(c.MSG_PROGRESS_TRANSCRIBE, 3, total_stages)
        )
        stage_start_time = time.monotonic()
        raw_text = await processor.transcribe_audio(transcribe_path)
        _log_stage_success(user_id, "transcribe", stage_start_time)
        
        # Stage 4: Refine text
        await update_progress(
            context, message.chat_id, ack_msg.message_id,
            get_progress_message(c.MSG_PROGRESS_REFINE, 4, total_stages)
        )
        stage_start_time = time.monotonic()
        delivery_adapter = get_delivery_adapter(context)
        if getattr(processor, "supports_refine_streaming", False) and delivery_adapter.supports_live_refine_streaming(context, ack_msg):
            final_text = await processor.stream_refine_text(context, message.chat_id, ack_msg, raw_text)
            streamed_refine_delivery = True
        else:
            final_text = await processor.refine_text(raw_text)
        _log_stage_success(user_id, "refine", stage_start_time)

        if not streamed_refine_delivery:
            # Final: Send response
            await update_progress(
                context, message.chat_id, ack_msg.message_id,
                get_progress_message(c.MSG_PROGRESS_FINALIZING, 4, total_stages)
            )

            full_text = processor.format_response(final_text)
            stage_start_time = time.monotonic()
            await processor.send_response(context, message.chat_id, ack_msg, full_text)
            _log_stage_success(user_id, "send_response", stage_start_time)
        
        _log_pipeline_summary(user_id, processor.provider_name, total_start_time, "success")
        
    except AudioPipelineTimeout as e:
        logger.error(
            "Audio pipeline timeout | user_id=%s provider=%s error=%s duration_ms=%s",
            user_id,
            processor.provider_name,
            e.__class__.__name__,
            _elapsed_ms(total_start_time),
        )
        if not streamed_refine_delivery:
            await ack_msg.edit_text(e.user_message)
        _log_pipeline_summary(user_id, processor.provider_name, total_start_time, "timeout")
        
    except AudioPipelineStageError as e:
        logger.error(
            "Audio pipeline stage error | user_id=%s provider=%s error=%s duration_ms=%s",
            user_id,
            processor.provider_name,
            e.__class__.__name__,
            _elapsed_ms(total_start_time),
        )
        if not streamed_refine_delivery:
            await ack_msg.edit_text(e.user_message)
        _log_pipeline_summary(user_id, processor.provider_name, total_start_time, "stage_error")

    except Exception as e:
        logger.error(
            "Audio pipeline unexpected error | user_id=%s provider=%s error=%s duration_ms=%s",
            user_id,
            processor.provider_name,
            e.__class__.__name__,
            _elapsed_ms(total_start_time),
        )
        if not streamed_refine_delivery:
            await ack_msg.edit_text(c.MSG_ERROR_INTERNAL)
        _log_pipeline_summary(user_id, processor.provider_name, total_start_time, "unexpected_error")
        
    finally:
        # Always cleanup temporary files
        processor.cleanup_files(ogg_path, mp3_path)
        
        # Clean up progress cache for this message
        clear_progress_cache(message.chat_id, ack_msg.message_id)

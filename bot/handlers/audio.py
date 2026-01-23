"""
Audio processing handler for Telegram bot.
"""

import os
import sys
import logging
import asyncio
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from bot.decorators.auth import restricted
from bot.decorators.timeout import execute_with_timeout
from bot.decorators.rate_limit import rate_limited
from bot.ui.progress import update_progress, get_progress_message, clear_progress_cache
from bot import utils
from bot import constants as c
from bot.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class AudioProcessor:
    """
    Handles audio file processing pipeline.
    """
    
    def __init__(self, config):
        """Initialize audio processor with configuration."""
        self.config = config
        # Initialize provider once at startup (Dependency Injection / Singleton)
        self.provider = utils.create_provider(config)
    
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
    
    def generate_file_paths(self, unique_id: str, ext: str) -> tuple[str, str]:
        """
        Generate file paths for temporary audio files.
        
        Args:
            unique_id: Unique ID from Telegram
            ext: File extension
            
        Returns:
            Tuple of (ogg_path, mp3_path)
        """
        ogg_path = os.path.join(self.config.audio_dir, f"{unique_id}.{ext}")
        mp3_path = os.path.join(self.config.audio_dir, f"{unique_id}.mp3")
        return ogg_path, mp3_path
    
    async def download_audio(self, file_obj, file_path: str) -> None:
        """Download audio file with timeout protection."""
        await execute_with_timeout(
            "download", 
            file_obj.download_to_drive(file_path)
        )
    
    async def convert_audio(self, ogg_path: str, mp3_path: str) -> None:
        """Convert audio to MP3 with timeout protection."""
        # Run blocking conversion in thread to allow timeout to work
        await execute_with_timeout(
            "convert",
            asyncio.to_thread(utils.convert_to_mp3, ogg_path, mp3_path)
        )
    
    async def transcribe_audio(self, mp3_path: str) -> str:
        """Transcribe audio with timeout protection."""
        return await execute_with_timeout(
            "transcribe",
            self.provider.transcribe_audio(mp3_path)
        )
    
    async def refine_text(self, raw_text: str) -> str:
        """Refine transcribed text with timeout protection."""
        return await execute_with_timeout(
            "refine",
            self.provider.refine_text(raw_text)
        )
    
    def format_response(self, final_text: str) -> str:
        """Format final response text with header."""
        try:
            model_name = self.provider.model_name if self.provider else "unknown"
        except Exception:
            model_name = "unknown"
        
        header = c.MSG_COMPLETION_HEADER.format(model_name=model_name)
        return f"{header}\n\n{final_text}"
    
    async def send_response(self, context: ContextTypes.DEFAULT_TYPE, 
                          chat_id: int, ack_msg, full_text: str) -> None:
        """Send response, handling message length limits."""
        if len(full_text) <= c.MAX_MESSAGE_LENGTH:
            await ack_msg.edit_text(full_text, parse_mode="Markdown")
        else:
            # Split into chunks
            chunks = [full_text[i:i+c.MAX_MESSAGE_LENGTH] 
                     for i in range(0, len(full_text), c.MAX_MESSAGE_LENGTH)]
            
            # Edit original message with first chunk
            await ack_msg.edit_text(chunks[0], parse_mode="Markdown")
            
            # Send remaining chunks as new messages
            for chunk in chunks[1:]:
                await context.bot.send_message(
                    chat_id=chat_id, 
                    text=chunk, 
                    parse_mode="Markdown"
                )
    
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
    processor = get_audio_processor()
    
    # Determine file type and get file object
    file_obj, ext = await processor.determine_file_type(message)
    if not file_obj:
        await message.reply_text(c.MSG_UNSUPPORTED_TYPE)
        return
    
    # Generate file paths
    unique_id = message.effective_attachment.file_unique_id
    ogg_path, mp3_path = processor.generate_file_paths(unique_id, ext)
    
    # Initial progress message
    total_stages = len(c.PROGRESS_STAGES)
    initial_progress = get_progress_message(c.MSG_PROGRESS_DOWNLOAD, 1, total_stages)
    ack_msg = await message.reply_text(initial_progress)
    
    try:
        # Stage 1: Download
        await update_progress(
            context, message.chat_id, ack_msg.message_id,
            get_progress_message(c.MSG_PROGRESS_DOWNLOAD, 1, total_stages)
        )
        await processor.download_audio(file_obj, ogg_path)
        
        # Stage 2: Convert to MP3
        await update_progress(
            context, message.chat_id, ack_msg.message_id,
            get_progress_message(c.MSG_PROGRESS_CONVERT, 2, total_stages)
        )
        await processor.convert_audio(ogg_path, mp3_path)
        
        # Stage 3: Transcribe
        await update_progress(
            context, message.chat_id, ack_msg.message_id,
            get_progress_message(c.MSG_PROGRESS_TRANSCRIBE, 3, total_stages)
        )
        raw_text = await processor.transcribe_audio(mp3_path)
        
        # Stage 4: Refine text
        await update_progress(
            context, message.chat_id, ack_msg.message_id,
            get_progress_message(c.MSG_PROGRESS_REFINE, 4, total_stages)
        )
        final_text = await processor.refine_text(raw_text)
        
        # Final: Send response
        await update_progress(
            context, message.chat_id, ack_msg.message_id,
            get_progress_message(c.MSG_PROGRESS_FINALIZING, 4, total_stages)
        )
        
        full_text = processor.format_response(final_text)
        await processor.send_response(context, message.chat_id, ack_msg, full_text)
        
        logger.info(f"Audio processing completed for user {message.from_user.id}")
        
    except TimeoutError as e:
        logger.error(f"Timeout during processing: {e}")
        error_msg = _get_timeout_message(str(e))
        await ack_msg.edit_text(error_msg)
        
    except Exception as e:
        logger.error(f"Error in audio processing pipeline: {e}")
        error_msg = _get_error_message(str(e))
        await ack_msg.edit_text(error_msg)
        
    finally:
        # Always cleanup temporary files
        processor.cleanup_files(ogg_path, mp3_path)
        
        # Clean up progress cache for this message
        clear_progress_cache(message.chat_id, ack_msg.message_id)


def _get_timeout_message(error_str: str) -> str:
    """Get appropriate timeout error message based on error content."""
    if "download" in error_str.lower():
        return c.MSG_TIMEOUT_DOWNLOAD
    elif "convert" in error_str.lower():
        return c.MSG_TIMEOUT_CONVERT
    elif "transcribe" in error_str.lower():
        return c.MSG_TIMEOUT_TRANSCRIBE
    elif "refine" in error_str.lower():
        return c.MSG_TIMEOUT_REFINE
    else:
        return c.MSG_ERROR_INTERNAL


def _get_error_message(error_str: str) -> str:
    """Get appropriate error message based on error content."""
    error_str_lower = error_str.lower()
    
    if "download" in error_str_lower:
        return c.MSG_ERROR_DOWNLOAD
    elif "ffmpeg" in error_str_lower or "convert" in error_str_lower:
        return c.MSG_ERROR_CONVERT
    elif "transcri" in error_str_lower:
        return c.MSG_ERROR_TRANSCRIBE
    elif "refine" in error_str_lower:
        return c.MSG_ERROR_REFINE
    else:
        return c.MSG_ERROR_INTERNAL


# Global processor instance (will be initialized in main.py)
_audio_processor = None


def get_audio_processor() -> AudioProcessor:
    """Get the global audio processor instance."""
    global _audio_processor
    if _audio_processor is None:
        raise RuntimeError("AudioProcessor not initialized")
    return _audio_processor


def init_audio_processor(config) -> None:
    """Initialize the global audio processor."""
    global _audio_processor
    _audio_processor = AudioProcessor(config)


# Global rate limiter instance
_rate_limiter = None


def get_rate_limiter() -> RateLimiter:
    """Get the global rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        raise RuntimeError("RateLimiter not initialized")
    return _rate_limiter


def init_rate_limiter(config) -> None:
    """Initialize the global rate limiter."""
    global _rate_limiter
    _rate_limiter = RateLimiter(
        max_per_user=config.rate_limit_config["max_per_user"],
        cooldown=config.rate_limit_config["cooldown_seconds"],
        max_global=config.rate_limit_config["max_concurrent_global"],
        max_file_size_mb=config.rate_limit_config["max_file_size_mb"]
    )



import logging
import os
import glob
import asyncio
from asyncio.subprocess import PIPE
from typing import Iterable

from bot.providers import OpenAIProvider, GeminiProvider, LLMProvider

logger = logging.getLogger(__name__)

async def convert_to_mp3(src_path: str, dst_path: str) -> None:
    logger.info(f"Convert {src_path} -> {dst_path}")

    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i",
        src_path,
        "-vn",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-b:a",
        "192k",
        dst_path,
        stdout=PIPE,
        stderr=PIPE,
    )

    try:
        stdout, stderr = await process.communicate()
    except asyncio.CancelledError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            await process.communicate()
        except Exception:
            pass
        raise

    if process.returncode != 0:
        err = stderr.decode("utf-8", errors="replace") if stderr else ""
        logger.error(f"FFmpeg error: {err}")
        raise RuntimeError("Errore conversione audio")

def create_provider(config) -> LLMProvider:
    """Factory function to create the configured LLM provider."""
    provider_name = config.provider_name
    model_name = config.model_name
    api_key = config.get_api_key(provider_name)
    prompts = config.prompts
    
    if provider_name == 'openai':
        logger.info(f"Initializing OpenAI Provider (model: {model_name or 'default'})")
        return OpenAIProvider(api_key, model_name, prompts)
    elif provider_name == 'gemini':
        logger.info(f"Initializing Gemini Provider (model: {model_name or 'default'})")
        return GeminiProvider(api_key, model_name, prompts)
    else:
        raise ValueError(f"Provider sconosciuto: {provider_name}")

def cleanup_audio_directory(dir_path: str) -> None:
    """
    Clean up all files in the audio directory on startup.
    This ensures no leftover files from previous crashed runs consume disk space.
    """
    if os.getenv("AUDIO_CLEANUP_ON_STARTUP", "1").strip().lower() in {"0", "false", "no"}:
        logger.info("Startup audio cleanup disabled (AUDIO_CLEANUP_ON_STARTUP=0)")
        return

    if not os.path.exists(dir_path):
        return

    abs_dir_path = os.path.abspath(dir_path)
    if abs_dir_path in {"/", os.path.expanduser("~")}:
        logger.warning(f"Refusing to cleanup dangerous audio directory: {abs_dir_path}")
        return

    if os.path.basename(abs_dir_path) != "audio_files":
        logger.warning(
            "Refusing to cleanup audio directory with unexpected basename: "
            f"{abs_dir_path} (expected basename: audio_files)"
        )
        return

    logger.info(f"Cleaning up audio directory: {abs_dir_path}")

    allowed_exts: set[str] = {
        ".aac",
        ".flac",
        ".m4a",
        ".mp3",
        ".mp4",
        ".ogg",
        ".opus",
        ".wav",
        ".webm",
    }

    try:
        # Remove only known audio file types in the directory
        files: Iterable[str] = glob.glob(os.path.join(abs_dir_path, "*"))
        count = 0
        for f in files:
            if os.path.isfile(f) and not os.path.islink(f):
                try:
                    ext = os.path.splitext(f)[1].lower()
                    if ext in allowed_exts:
                        os.remove(f)
                        count += 1
                except Exception as e:
                    logger.warning(f"Failed to delete {f}: {e}")
        
        if count > 0:
            logger.info(f"Cleaned up {count} leftover files")
            
    except Exception as e:
        logger.error(f"Error cleaning audio directory: {e}")

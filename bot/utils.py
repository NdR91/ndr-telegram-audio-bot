"""
Utility functions for the Telegram Audio Bot.

P3: Adapter factories now use the :mod:`bot.adapters.registry` instead
of ``if/elif`` chains.
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
from asyncio.subprocess import PIPE
from dataclasses import dataclass
from typing import Iterable

from bot import constants as c
from bot.adapters import text_processor_registry, transcriber_registry
from bot.exceptions import ConvertError
from bot.providers import (
    GeminiProvider,
    LLMProvider,
    OpenAIProvider,
    ResilientProvider,
    ResilientTextProcessor,
    ResilientTranscriber,
    TextProcessor,
    Transcriber,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Combined-provider constructors (for backward-compatible create_provider)
# ---------------------------------------------------------------------------
# These map short provider names to the combined class that implements
# LLMProvider + Transcriber + TextProcessor.

_DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.0-flash",
}


def _get_combined_provider_class(
    provider_name: str,
) -> type[LLMProvider]:
    """Return the combined provider class for *provider_name*.

    Exposed as a separate function so tests can monkeypatch it.
    """
    mapping = {
        "openai": OpenAIProvider,
        "gemini": GeminiProvider,
    }
    cls = mapping.get(provider_name)
    if cls is None:
        raise ValueError(f"Provider sconosciuto: {provider_name}")
    return cls


async def convert_to_mp3(src_path: str, dst_path: str) -> None:
    """Convert audio file to MP3 using FFmpeg."""
    logger.info("Convert %s -> %s", src_path, dst_path)

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
        logger.error("FFmpeg error: %s", err)
        raise ConvertError("Errore conversione audio", c.MSG_ERROR_CONVERT)


def create_provider(config) -> LLMProvider:
    """Factory function to create the configured LLM provider.

    Returns a combined :class:`LLMProvider` that implements both
    :class:`Transcriber` and :class:`TextProcessor` for backward
    compatibility.

    For new code that needs separate references, use
    :func:`create_provider_components`.
    """
    provider_name = config.provider_name
    api_key = config.get_api_key(provider_name)
    prompts = config.prompts
    model_name = config.model_name or _DEFAULT_MODELS.get(provider_name, "")

    provider_cls = _get_combined_provider_class(provider_name)
    logger.info("Initializing %s (model: %s)", provider_cls.__name__, model_name)
    provider = provider_cls(api_key, model_name, prompts)

    resilience = getattr(config, "provider_resilience_config", {})
    if resilience.get("enabled", True):
        return ResilientProvider(
            provider,
            provider_name=provider_name,
            failure_threshold=resilience.get("failure_threshold", 3),
            cooldown_seconds=resilience.get("cooldown_seconds", 60),
        )
    return provider


@dataclass
class ProviderComponents:
    """Separated provider components (P1)."""

    transcriber: Transcriber
    text_processor: TextProcessor | None
    provider_name: str
    model_name: str


def create_provider_components(config) -> ProviderComponents:
    """Factory to create separate Transcriber and TextProcessor instances.

    Uses the global :data:`~bot.adapters.registry.transcriber_registry`
    and :data:`~bot.adapters.registry.text_processor_registry` to resolve
    adapters by name (P3).

    Returns a :class:`ProviderComponents` with a :class:`Transcriber` and
    optional :class:`TextProcessor`, each independently wrapped with a
    circuit breaker.

    This is the P1 entry-point for new code.
    """
    provider_name = config.provider_name
    api_key = config.get_api_key(provider_name)
    prompts = config.prompts
    model_name = config.model_name

    # Resolve default model name when missing.
    if not model_name:
        model_name = _DEFAULT_MODELS.get(provider_name, "")

    logger.info("Creating components for %s (model: %s)", provider_name, model_name)

    try:
        transcriber = transcriber_registry.create(
            provider_name,
            api_key=api_key,
            model_name=model_name,
            prompts=prompts,
        )
        text_processor = text_processor_registry.create(
            provider_name,
            api_key=api_key,
            model_name=model_name,
            prompts=prompts,
        )
    except ValueError as exc:
        raise ValueError(
            f"Provider sconosciuto o non supportato: {provider_name}"
        ) from exc

    resilience = getattr(config, "provider_resilience_config", {})
    if resilience.get("enabled", True):
        ft = resilience.get("failure_threshold", 3)
        cd = resilience.get("cooldown_seconds", 60)
        transcriber = ResilientTranscriber(
            transcriber,
            provider_name=provider_name,
            failure_threshold=ft,
            cooldown_seconds=cd,
        )
        text_processor = ResilientTextProcessor(
            text_processor,
            provider_name=provider_name,
            failure_threshold=ft,
            cooldown_seconds=cd,
        )

    return ProviderComponents(
        transcriber=transcriber,
        text_processor=text_processor,
        provider_name=provider_name,
        model_name=model_name or "",
    )


def cleanup_audio_directory(dir_path: str) -> None:
    """Clean up all files in the audio directory on startup.

    This ensures no leftover files from previous crashed runs consume
    disk space.
    """
    if os.getenv("AUDIO_CLEANUP_ON_STARTUP", "1").strip().lower() in {"0", "false", "no"}:
        logger.info("Startup audio cleanup disabled (AUDIO_CLEANUP_ON_STARTUP=0)")
        return

    if not os.path.exists(dir_path):
        return

    abs_dir_path = os.path.abspath(dir_path)
    if abs_dir_path in {"/", os.path.expanduser("~")}:
        logger.warning("Refusing to cleanup dangerous audio directory: %s", abs_dir_path)
        return

    if os.path.basename(abs_dir_path) != "audio_files":
        logger.warning(
            "Refusing to cleanup audio directory with unexpected basename: "
            "%s (expected basename: audio_files)",
            abs_dir_path,
        )
        return

    logger.info("Cleaning up audio directory: %s", abs_dir_path)

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
                    logger.warning("Failed to delete %s: %s", f, e)

        if count > 0:
            logger.info("Cleaned up %d leftover files", count)

    except Exception as e:
        logger.error("Error cleaning audio directory: %s", e)

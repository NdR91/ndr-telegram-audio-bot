"""
Default adapter registrations (P3).

Registers the built-in transcriber and text-processor factories so they
can be created by adapter type name through the global registries.

Adapter types registered
------------------------
=============== ========================= ===============================
Key             Transcriber               Text processor
=============== ========================= ===============================
``openai``      ``OpenAIWhisperTranscriber``  alias for ``openai-native``
``openai-native``  same                      same
``gemini``      ``GeminiTranscriber``         alias for ``gemini-native``
``gemini-native``  same                      same
``openai-compat``  ``OpenAICompatTranscriber``  ``OpenAICompatTextProcessor``
=============== ========================= ===============================
"""

from __future__ import annotations

import logging
from typing import Optional

from bot.adapters.openai_compat import (
    OpenAICompatTextProcessor,
    OpenAICompatTranscriber,
)
from bot.adapters.registry import text_processor_registry, transcriber_registry
from bot.providers import (
    GeminiTextProcessor,
    GeminiTranscriber,
    OpenAITextProcessor,
    OpenAIWhisperTranscriber,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def _openai_native_transcriber(
    api_key: str,
    **kwargs,  # noqa: ARG001
) -> OpenAIWhisperTranscriber:
    return OpenAIWhisperTranscriber(api_key=api_key)


def _openai_native_processor(
    api_key: str,
    model_name: str = "gpt-4o-mini",
    prompts: Optional[dict] = None,
    **kwargs,  # noqa: ARG001
) -> OpenAITextProcessor:
    return OpenAITextProcessor(
        api_key=api_key,
        model_name=model_name,
        prompts=prompts,
    )


def _gemini_native_transcriber(
    api_key: str,
    model_name: str = "gemini-2.0-flash",
    **kwargs,  # noqa: ARG001
) -> GeminiTranscriber:
    return GeminiTranscriber(api_key=api_key, model_name=model_name)


def _gemini_native_processor(
    api_key: str,
    model_name: str = "gemini-2.0-flash",
    prompts: Optional[dict] = None,
    **kwargs,  # noqa: ARG001
) -> GeminiTextProcessor:
    return GeminiTextProcessor(
        api_key=api_key,
        model_name=model_name,
        prompts=prompts,
    )


def _openai_compat_transcriber(
    api_key: str,
    endpoint: str = "",
    model_name: str = "whisper-1",
    **kwargs,  # noqa: ARG001
) -> OpenAICompatTranscriber:
    return OpenAICompatTranscriber(
        api_key=api_key, endpoint=endpoint, model_name=model_name,
    )


def _openai_compat_processor(
    api_key: str,
    model_name: str = "gpt-4o-mini",
    endpoint: str = "",
    prompts: Optional[dict] = None,
    **kwargs,  # noqa: ARG001
) -> OpenAICompatTextProcessor:
    return OpenAICompatTextProcessor(
        api_key=api_key,
        model_name=model_name,
        endpoint=endpoint,
        prompts=prompts,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_defaults() -> None:
    """Register all built-in adapter factories.

    Idempotent — safe to call multiple times (subsequent calls are no-ops).
    """
    # --- OpenAI native ---
    if not transcriber_registry.has_type("openai-native"):
        transcriber_registry.register("openai-native", _openai_native_transcriber)
        text_processor_registry.register("openai-native", _openai_native_processor)

        # Short alias for backward compatibility with the legacy Config class
        # whose ``provider_name`` is ``"openai"`` (not ``"openai-native"``).
        transcriber_registry.register("openai", _openai_native_transcriber)
        text_processor_registry.register("openai", _openai_native_processor)

        logger.debug("Registered OpenAI native adapters")

    # --- Gemini native ---
    if not transcriber_registry.has_type("gemini-native"):
        transcriber_registry.register("gemini-native", _gemini_native_transcriber)
        text_processor_registry.register("gemini-native", _gemini_native_processor)

        # Short alias for backward compatibility.
        transcriber_registry.register("gemini", _gemini_native_transcriber)
        text_processor_registry.register("gemini", _gemini_native_processor)

        logger.debug("Registered Gemini native adapters")

    # --- OpenAI-compatible (OpenRouter, Ollama, vLLM, custom) ---
    if not transcriber_registry.has_type("openai-compat"):
        transcriber_registry.register("openai-compat", _openai_compat_transcriber)
        text_processor_registry.register("openai-compat", _openai_compat_processor)

        logger.debug("Registered OpenAI-compatible adapters")

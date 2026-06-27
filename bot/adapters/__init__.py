"""
Adapter package (P3).

Provides explicit registries for :class:`~bot.providers.Transcriber` and
:class:`~bot.providers.TextProcessor` adapters, replacing ``if/elif``
factory chains.

On import, this package registers the built-in adapter factories
(``openai-native``, ``gemini-native``, ``openai-compat``) with the global
registries so they can be created by adapter type name.
"""

from __future__ import annotations

from bot.adapters.defaults import register_defaults
from bot.adapters.openai_compat import OpenAICompatTextProcessor, OpenAICompatTranscriber
from bot.adapters.registry import (
    TextProcessorRegistry,
    TranscriberRegistry,
    text_processor_registry,
    transcriber_registry,
)

# Register built-in adapters on import.
register_defaults()

__all__ = [
    "OpenAICompatTextProcessor",
    "OpenAICompatTranscriber",
    "TextProcessorRegistry",
    "TranscriberRegistry",
    "text_processor_registry",
    "transcriber_registry",
]

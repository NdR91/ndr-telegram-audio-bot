"""
Adapter registries (P3).

Replaces ``if/elif`` factory chains with explicit registries for
:class:`~bot.providers.Transcriber` and :class:`~bot.providers.TextProcessor`
adapters.  New adapter types register themselves and can then be created
by name through the registry.

Usage
-----

::

    from bot.adapters.registry import transcriber_registry, text_processor_registry

    # Direct registration
    transcriber_registry.register("my-adapter", MyTranscriberFactory)
    text_processor_registry.register("my-adapter", MyTextProcessorFactory)

    # Decorator registration
    @transcriber_registry.register("another-adapter")
    def _(api_key, **kwargs):
        return AnotherTranscriber(api_key=api_key)

    # Creation
    t = transcriber_registry.create("my-adapter", api_key="...")
    p = text_processor_registry.create("my-adapter", api_key="...", model_name="...")
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from bot.providers import TextProcessor, Transcriber

# Type aliases for the factory callables.
# Each factory receives keyword arguments extracted from configuration
# (e.g. ``api_key``, ``model_name``, ``endpoint``, ``prompts``) and must
# return an instance of the requested adapter type.
TranscriberFactory = Callable[..., Transcriber]
TextProcessorFactory = Callable[..., TextProcessor]


# ---------------------------------------------------------------------------
# Transcriber registry
# ---------------------------------------------------------------------------


class TranscriberRegistry:
    """Registry of transcriber adapter factories keyed by adapter type.

    Each adapter type is a string such as ``"openai-native"``,
    ``"gemini-native"``, or ``"openai-compat"``.
    """

    def __init__(self) -> None:
        self._registry: Dict[str, TranscriberFactory] = {}

    def register(
        self,
        adapter_type: str,
        factory: Optional[TranscriberFactory] = None,
    ) -> TranscriberFactory:
        """Register a transcriber factory for *adapter_type*.

        Can be used as a direct call or a decorator::

            transcriber_registry.register("my-type", MyFactory)

            @transcriber_registry.register("my-type")
            def my_factory(**kwargs):
                ...
        """
        if factory is not None:
            self._registry[adapter_type] = factory
            return factory

        def decorator(fn: TranscriberFactory) -> TranscriberFactory:
            self._registry[adapter_type] = fn
            return fn

        return decorator

    def create(self, adapter_type: str, **kwargs: Any) -> Transcriber:
        """Create a :class:`~bot.providers.Transcriber` for *adapter_type*.

        Raises
        ------
        ValueError:
            If *adapter_type* is not registered.
        """
        factory = self._registry.get(adapter_type)
        if factory is None:
            registered = ", ".join(sorted(self._registry))
            raise ValueError(
                f"Unknown transcriber adapter type: '{adapter_type}'. "
                f"Registered types: {registered}"
            )
        return factory(**kwargs)

    def known_types(self) -> set[str]:
        """Return the set of registered adapter type keys."""
        return set(self._registry)

    def has_type(self, adapter_type: str) -> bool:
        """Return ``True`` when *adapter_type* is registered."""
        return adapter_type in self._registry


# ---------------------------------------------------------------------------
# Text processor registry
# ---------------------------------------------------------------------------


class TextProcessorRegistry:
    """Registry of text-processor adapter factories keyed by adapter type."""

    def __init__(self) -> None:
        self._registry: Dict[str, TextProcessorFactory] = {}

    def register(
        self,
        adapter_type: str,
        factory: Optional[TextProcessorFactory] = None,
    ) -> TextProcessorFactory:
        if factory is not None:
            self._registry[adapter_type] = factory
            return factory

        def decorator(fn: TextProcessorFactory) -> TextProcessorFactory:
            self._registry[adapter_type] = fn
            return fn

        return decorator

    def create(self, adapter_type: str, **kwargs: Any) -> TextProcessor:
        """Create a :class:`~bot.providers.TextProcessor` for *adapter_type*.

        Raises
        ------
        ValueError:
            If *adapter_type* is not registered.
        """
        factory = self._registry.get(adapter_type)
        if factory is None:
            registered = ", ".join(sorted(self._registry))
            raise ValueError(
                f"Unknown text processor adapter type: '{adapter_type}'. "
                f"Registered types: {registered}"
            )
        return factory(**kwargs)

    def known_types(self) -> set[str]:
        return set(self._registry)

    def has_type(self, adapter_type: str) -> bool:
        return adapter_type in self._registry


# ---------------------------------------------------------------------------
# Singleton instances
# ---------------------------------------------------------------------------

transcriber_registry = TranscriberRegistry()
"""Global singleton transcriber registry."""

text_processor_registry = TextProcessorRegistry()
"""Global singleton text-processor registry."""

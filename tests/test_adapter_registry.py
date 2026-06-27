"""
Tests for the adapter registries (P3).

Covers:
- :class:`TranscriberRegistry` and :class:`TextProcessorRegistry` operations
- Default registrations (``openai-native``, ``gemini-native``, ``openai-compat``)
- Factory invocation with keyword arguments
- Error handling for unknown adapter types
- Backward-compatible short aliases (``openai``, ``gemini``)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bot.adapters.registry import (
    TextProcessorRegistry,
    TranscriberRegistry,
    text_processor_registry,
    transcriber_registry,
)
from bot.providers import TextProcessor, Transcriber


# ===================================================================
# TranscriberRegistry
# ===================================================================


class TestTranscriberRegistry:
    def test_register_and_create(self):
        registry = TranscriberRegistry()
        dummy = MagicMock(spec=Transcriber)

        registry.register("test-type", lambda **kwargs: dummy)
        result = registry.create("test-type")

        assert result is dummy

    def test_create_unknown_type(self):
        registry = TranscriberRegistry()
        with pytest.raises(ValueError, match="Unknown transcriber"):
            registry.create("nonexistent")

    def test_known_types(self):
        registry = TranscriberRegistry()
        registry.register("a", lambda **kwargs: MagicMock(spec=Transcriber))
        registry.register("b", lambda **kwargs: MagicMock(spec=Transcriber))
        assert registry.known_types() == {"a", "b"}

    def test_has_type(self):
        registry = TranscriberRegistry()
        registry.register("x", lambda **kwargs: MagicMock(spec=Transcriber))
        assert registry.has_type("x") is True
        assert registry.has_type("y") is False

    def test_decorator_registration(self):
        registry = TranscriberRegistry()

        @registry.register("decorated")
        def factory(**kwargs):
            return MagicMock(spec=Transcriber)

        assert registry.has_type("decorated")
        assert isinstance(registry.create("decorated"), MagicMock)

    def test_factory_receives_kwargs(self):
        registry = TranscriberRegistry()
        captured = {}

        def factory(**kwargs):
            captured.update(kwargs)
            return MagicMock(spec=Transcriber)

        registry.register("kw-test", factory)
        registry.create("kw-test", api_key="key-123", endpoint="https://example.com")

        assert captured["api_key"] == "key-123"
        assert captured["endpoint"] == "https://example.com"

    def test_idempotent_reregister(self):
        registry = TranscriberRegistry()
        dummy_a = MagicMock(spec=Transcriber)
        dummy_b = MagicMock(spec=Transcriber)

        registry.register("same", lambda **kwargs: dummy_a)
        registry.register("same", lambda **kwargs: dummy_b)

        assert registry.create("same") is dummy_b


# ===================================================================
# TextProcessorRegistry
# ===================================================================


class TestTextProcessorRegistry:
    def test_register_and_create(self):
        registry = TextProcessorRegistry()
        dummy = MagicMock(spec=TextProcessor)

        registry.register("test-type", lambda **kwargs: dummy)
        result = registry.create("test-type")

        assert result is dummy

    def test_create_unknown_type(self):
        registry = TextProcessorRegistry()
        with pytest.raises(ValueError, match="Unknown text processor"):
            registry.create("nonexistent")

    def test_known_types(self):
        registry = TextProcessorRegistry()
        registry.register("a", lambda **kwargs: MagicMock(spec=TextProcessor))
        registry.register("b", lambda **kwargs: MagicMock(spec=TextProcessor))
        assert registry.known_types() == {"a", "b"}

    def test_has_type(self):
        registry = TextProcessorRegistry()
        registry.register("x", lambda **kwargs: MagicMock(spec=TextProcessor))
        assert registry.has_type("x") is True
        assert registry.has_type("y") is False


# ===================================================================
# Default registrations
# ===================================================================


class TestDefaultRegistrations:
    """Verify that :func:`~bot.adapters.defaults.register_defaults`
    registers all expected adapter types."""

    def test_openai_native_registered(self):
        assert transcriber_registry.has_type("openai-native")
        assert text_processor_registry.has_type("openai-native")

    def test_openai_alias_registered(self):
        assert transcriber_registry.has_type("openai")
        assert text_processor_registry.has_type("openai")

    def test_gemini_native_registered(self):
        assert transcriber_registry.has_type("gemini-native")
        assert text_processor_registry.has_type("gemini-native")

    def test_gemini_alias_registered(self):
        assert transcriber_registry.has_type("gemini")
        assert text_processor_registry.has_type("gemini")

    def test_openai_compat_registered(self):
        assert transcriber_registry.has_type("openai-compat")
        assert text_processor_registry.has_type("openai-compat")

    def test_known_types_contains_all(self):
        types = transcriber_registry.known_types()
        for expected in ("openai", "openai-native", "gemini", "gemini-native", "openai-compat"):
            assert expected in types, f"Missing transcriber type: {expected}"

        types = text_processor_registry.known_types()
        for expected in ("openai", "openai-native", "gemini", "gemini-native", "openai-compat"):
            assert expected in types, f"Missing text processor type: {expected}"


# ===================================================================
# Registry integration with create_provider_components
# ===================================================================


class TestRegistryIntegration:
    """Verify that :func:`bot.utils.create_provider_components`
    creates adapters through the registry (not if/elif)."""

    def test_create_openai_components(self, monkeypatch):
        """OpenAI provider creates WhisperTranscriber + OpenAITextProcessor."""
        captured = {"types": []}

        original_create = transcriber_registry.create

        def tracking_transcriber(adapter_type, **kwargs):
            captured["types"].append(("transcriber", adapter_type))
            return original_create(adapter_type, **kwargs)

        monkeypatch.setattr(transcriber_registry, "create", tracking_transcriber)

        from types import SimpleNamespace
        from bot.utils import create_provider_components

        config = SimpleNamespace(
            provider_name="openai",
            model_name=None,
            prompts={"system": "s", "refine_template": "{raw_text}"},
            get_api_key=lambda provider: "sk-test",
            provider_resilience_config={"enabled": False},
        )
        components = create_provider_components(config)
        assert components.provider_name == "openai"
        assert components.transcriber is not None
        assert components.text_processor is not None

    def test_create_gemini_components(self, monkeypatch):
        """Gemini provider creates GeminiTranscriber + GeminiTextProcessor."""
        from types import SimpleNamespace
        from bot.utils import create_provider_components

        config = SimpleNamespace(
            provider_name="gemini",
            model_name=None,
            prompts={"system": "s", "refine_template": "{raw_text}"},
            get_api_key=lambda provider: "gem-key",
            provider_resilience_config={"enabled": False},
        )
        components = create_provider_components(config)
        assert components.provider_name == "gemini"
        assert components.transcriber is not None
        assert components.text_processor is not None

    def test_unknown_provider_raises(self):
        """An unregistered provider name raises ValueError."""
        from types import SimpleNamespace
        from bot.utils import create_provider_components

        config = SimpleNamespace(
            provider_name="imaginary-provider",
            model_name=None,
            prompts={},
            get_api_key=lambda provider: "key",
            provider_resilience_config={"enabled": False},
        )
        with pytest.raises(ValueError, match="sconosciuto|Unknown|imaginary"):
            create_provider_components(config)

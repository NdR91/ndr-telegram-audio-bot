"""
Tests for the capability model (P2).

Covers:
- :class:`CapabilityModel` construction, equality, to_dict, from_dict, merge
- :func:`detect_capabilities` for known and unknown adapter types
- :func:`default_for_adapter`
- :func:`get_capabilities` on every adapter class in ``bot.providers``
- :meth:`AudioProcessor.capabilities` (new P2 property)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bot.capabilities import CapabilityModel, default_for_adapter, detect_capabilities, merge_capabilities


# ===================================================================
# CapabilityModel basic ops
# ===================================================================


class TestCapabilityModel:
    def test_all_default_to_false(self):
        m = CapabilityModel()
        assert m.transcription is False
        assert m.text_generation is False
        assert m.refinement is False
        assert m.streaming_refinement is False

    def test_construct_with_some_true(self):
        m = CapabilityModel(transcription=True, refinement=True)
        assert m.transcription is True
        assert m.text_generation is False
        assert m.refinement is True
        assert m.streaming_refinement is False

    def test_is_frozen(self):
        m = CapabilityModel()
        with pytest.raises(AttributeError):
            m.transcription = True  # type: ignore[misc]

    def test_to_dict_returns_bools(self):
        m = CapabilityModel(transcription=True, streaming_refinement=True)
        d = m.to_dict()
        assert d == {
            "transcription": True,
            "text_generation": False,
            "refinement": False,
            "streaming_refinement": True,
        }
        assert all(isinstance(v, bool) for v in d.values())

    def test_from_dict_full(self):
        m = CapabilityModel.from_dict({
            "transcription": True,
            "text_generation": True,
            "refinement": False,
            "streaming_refinement": True,
        })
        assert m.transcription is True
        assert m.text_generation is True
        assert m.refinement is False
        assert m.streaming_refinement is True

    def test_from_dict_partial(self):
        m = CapabilityModel.from_dict({"transcription": True})
        assert m.transcription is True
        assert m.text_generation is False
        assert m.refinement is False
        assert m.streaming_refinement is False

    def test_from_dict_none(self):
        m = CapabilityModel.from_dict(None)
        assert m == CapabilityModel()

    def test_from_dict_empty(self):
        m = CapabilityModel.from_dict({})
        assert m == CapabilityModel()

    def test_from_dict_ignores_extra_keys(self):
        m = CapabilityModel.from_dict({"transcription": True, "unknown_key": 42, "foo": "bar"})
        assert m.transcription is True
        assert m.text_generation is False


# ===================================================================
# default_for_adapter
# ===================================================================


class TestDefaultForAdapter:
    def test_openai_default(self):
        caps = default_for_adapter("openai")
        assert caps.transcription is True
        assert caps.refinement is True
        assert caps.streaming_refinement is True

    def test_gemini_default(self):
        caps = default_for_adapter("gemini")
        assert caps.transcription is True
        assert caps.refinement is True
        assert caps.streaming_refinement is True

    def test_unknown_adapter_all_false(self):
        caps = default_for_adapter("ollama")
        assert caps == CapabilityModel()


# ===================================================================
# detect_capabilities
# ===================================================================


class TestDetectCapabilities:
    def test_openai_no_model(self):
        caps = detect_capabilities("openai", "")
        assert caps.transcription is True
        assert caps.refinement is True
        assert caps.streaming_refinement is True

    def test_openai_with_model(self):
        caps = detect_capabilities("openai", "gpt-4o")
        assert caps.transcription is True  # Whisper is separate
        assert caps.refinement is True
        assert caps.streaming_refinement is True

    def test_gemini_default(self):
        caps = detect_capabilities("gemini", "gemini-2.0-flash")
        assert caps.transcription is True
        assert caps.refinement is True
        assert caps.streaming_refinement is True

    def test_unknown_adapter(self):
        caps = detect_capabilities("ollama", "llama3")
        assert caps == CapabilityModel()

    def test_unknown_adapter_no_model(self):
        caps = detect_capabilities("ollama")
        assert caps == CapabilityModel()


# ===================================================================
# merge_capabilities
# ===================================================================


class TestMergeCapabilities:
    def test_merge_none_overrides(self):
        detected = CapabilityModel(transcription=True)
        merged = merge_capabilities(detected, None)
        assert merged == detected

    def test_merge_empty_overrides(self):
        detected = CapabilityModel(transcription=True, refinement=True)
        merged = merge_capabilities(detected, {})
        assert merged == detected

    def test_merge_partial_overrides(self):
        detected = CapabilityModel(transcription=True, refinement=True)
        merged = merge_capabilities(detected, {"transcription": False})
        assert merged.transcription is False  # overridden
        assert merged.refinement is True      # unchanged

    def test_merge_overrides_all(self):
        detected = CapabilityModel(transcription=True, refinement=True)
        merged = merge_capabilities(detected, {
            "transcription": False,
            "text_generation": True,
            "refinement": False,
            "streaming_refinement": True,
        })
        assert merged.transcription is False
        assert merged.text_generation is True
        assert merged.refinement is False
        assert merged.streaming_refinement is True


# ===================================================================
# get_capabilities() on provider adapters
# ===================================================================


class TestAdapterCapabilities:
    """Verify each adapter exposes the expected capabilities."""

    def test_openai_whisper_transcriber(self):
        from bot.providers import OpenAIWhisperTranscriber
        adapter = OpenAIWhisperTranscriber(api_key="sk-test")
        caps = adapter.get_capabilities()
        assert caps.transcription is True
        assert caps.refinement is False

    def test_openai_text_processor(self):
        from bot.providers import OpenAITextProcessor
        adapter = OpenAITextProcessor(api_key="sk-test", model_name="gpt-4o-mini")
        caps = adapter.get_capabilities()
        assert caps.transcription is False
        assert caps.refinement is True
        assert caps.streaming_refinement is True

    def test_openai_provider(self):
        from bot.providers import OpenAIProvider
        provider = OpenAIProvider(api_key="sk-test", model_name="gpt-4o-mini")
        caps = provider.get_capabilities()
        assert caps.transcription is True
        assert caps.refinement is True
        assert caps.streaming_refinement is True

    def test_gemini_transcriber(self):
        from bot.providers import GeminiTranscriber
        adapter = GeminiTranscriber(api_key="test-key", model_name="gemini-2.0-flash")
        caps = adapter.get_capabilities()
        assert caps.transcription is True
        assert caps.refinement is False

    def test_gemini_text_processor(self):
        from bot.providers import GeminiTextProcessor
        adapter = GeminiTextProcessor(api_key="test-key", model_name="gemini-2.0-flash")
        caps = adapter.get_capabilities()
        assert caps.transcription is False
        assert caps.refinement is True
        assert caps.streaming_refinement is True

    def test_gemini_provider(self):
        from bot.providers import GeminiProvider
        provider = GeminiProvider(api_key="test-key", model_name="gemini-2.0-flash")
        caps = provider.get_capabilities()
        assert caps.transcription is True
        assert caps.refinement is True
        assert caps.streaming_refinement is True

    def test_resilient_transcriber_delegates(self):
        from bot.providers import OpenAIWhisperTranscriber, ResilientTranscriber
        inner = OpenAIWhisperTranscriber(api_key="sk-test")
        wrapper = ResilientTranscriber(inner)
        assert wrapper.get_capabilities() == inner.get_capabilities()

    def test_resilient_text_processor_delegates(self):
        from bot.providers import OpenAITextProcessor, ResilientTextProcessor
        inner = OpenAITextProcessor(api_key="sk-test", model_name="gpt-4o-mini")
        wrapper = ResilientTextProcessor(inner)
        assert wrapper.get_capabilities() == inner.get_capabilities()


# ===================================================================
# AudioProcessor.capabilities (P2 new property)
# ===================================================================


class TestAudioProcessorCapabilities:
    """Verify the new ``capabilities`` property on AudioProcessor."""

    def test_legacy_mode_with_mock_provider(self):
        from bot.handlers.audio import AudioProcessor

        config = MagicMock()
        config.provider_name = "test"

        mock_provider = MagicMock()
        mock_provider.get_capabilities.return_value = CapabilityModel(
            transcription=True, refinement=True, streaming_refinement=True
        )

        with patch("bot.handlers.audio.utils.create_provider", return_value=mock_provider):
            proc = AudioProcessor(config)

        caps = proc.capabilities
        assert caps.transcription is True
        assert caps.refinement is True
        assert caps.streaming_refinement is True

    def test_legacy_mode_provider_get_capabilities_not_callable(self):
        from bot.handlers.audio import AudioProcessor

        config = MagicMock()
        config.provider_name = "test"

        mock_provider = MagicMock(spec=[])  # no get_capabilities at all
        # Ensure hasattr(mock_provider, "get_capabilities") returns False
        # by not setting the attribute at all on a spec'd object.

        with patch("bot.handlers.audio.utils.create_provider", return_value=mock_provider):
            proc = AudioProcessor(config)

        # Fallback: assume transcription=True
        assert proc.capabilities.transcription is True

    def test_p1_mode_with_text_processor(self):
        from bot.handlers.audio import AudioProcessor

        config = MagicMock()
        mock_tp = MagicMock()
        mock_tp.get_capabilities.return_value = CapabilityModel(
            text_generation=True, refinement=True, streaming_refinement=True
        )

        proc = AudioProcessor(
            config,
            transcriber=MagicMock(),
            text_processor=mock_tp,
            provider_name="test",
        )
        caps = proc.capabilities
        assert caps.refinement is True
        assert caps.streaming_refinement is True


# ===================================================================
# state.py uses CapabilityModel
# ===================================================================


class TestStateCheckerCapabilities:
    def test_legacy_provider_no_capabilities_assumed_transcribe(self):
        from bot.state import StateChecker

        providers = [{"name": "legacy", "enabled": True}]
        assert StateChecker._any_can_transcribe(providers) is True

    def test_provider_with_transcription_capability(self):
        from bot.state import StateChecker

        providers = [{
            "name": "openai",
            "enabled": True,
            "capabilities": {"transcription": True, "refinement": True},
        }]
        assert StateChecker._any_can_transcribe(providers) is True

    def test_provider_without_transcription_capability(self):
        from bot.state import StateChecker

        providers = [{
            "name": "text-only",
            "enabled": True,
            "capabilities": {"transcription": False, "refinement": True},
        }]
        assert StateChecker._any_can_transcribe(providers) is False

    def test_multiple_providers_any_transcribes(self):
        from bot.state import StateChecker

        providers = [
            {"name": "p1", "enabled": True, "capabilities": {"transcription": False}},
            {"name": "p2", "enabled": True, "capabilities": {"transcription": True}},
        ]
        assert StateChecker._any_can_transcribe(providers) is True

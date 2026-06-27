"""
Tests for OpenAI-compatible adapters (P3).

Covers:
- :class:`OpenAICompatTranscriber` construction and ``get_capabilities``
- :class:`OpenAICompatTextProcessor` construction and ``get_capabilities``
- Registry-based creation through ``openai-compat`` adapter type
- ``_normalise_endpoint`` URL logic
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.adapters.openai_compat import (
    OpenAICompatTextProcessor,
    OpenAICompatTranscriber,
    _normalise_endpoint,
)
from bot.adapters.registry import text_processor_registry, transcriber_registry
from bot.capabilities import CapabilityModel


# ===================================================================
# _normalise_endpoint
# ===================================================================


class TestNormaliseEndpoint:
    def test_empty_defaults_to_openai(self):
        assert _normalise_endpoint("") == "https://api.openai.com/v1"

    def test_appends_v1(self):
        assert _normalise_endpoint("https://openrouter.ai/api") == "https://openrouter.ai/api/v1"

    def test_preserves_v1_suffix(self):
        assert _normalise_endpoint("https://openrouter.ai/api/v1") == "https://openrouter.ai/api/v1"

    def test_strips_trailing_slash(self):
        assert _normalise_endpoint("http://localhost:11434/v1/") == "http://localhost:11434/v1"

    def test_localhost_ollama(self):
        assert _normalise_endpoint("http://localhost:11434") == "http://localhost:11434/v1"

    def test_localhost_vllm(self):
        assert _normalise_endpoint("http://localhost:8000") == "http://localhost:8000/v1"


# ===================================================================
# OpenAICompatTranscriber
# ===================================================================


class TestOpenAICompatTranscriber:
    def test_constructor(self):
        t = OpenAICompatTranscriber(api_key="sk-test", endpoint="http://localhost:11434")
        assert t.client is not None

    def test_constructor_default_endpoint(self):
        t = OpenAICompatTranscriber(api_key="sk-test")
        assert t.client is not None

    def test_get_capabilities(self):
        t = OpenAICompatTranscriber(api_key="sk-test")
        caps = t.get_capabilities()
        assert caps.transcription is True
        assert caps.text_generation is False
        assert caps.refinement is False


# ===================================================================
# OpenAICompatTextProcessor
# ===================================================================


class TestOpenAICompatTextProcessor:
    def test_constructor_defaults(self):
        p = OpenAICompatTextProcessor(api_key="sk-test")
        assert p.model_name == "gpt-4o-mini"
        assert p.client is not None
        assert p.async_client is not None

    def test_constructor_custom_model(self):
        p = OpenAICompatTextProcessor(api_key="sk-test", model_name="gpt-4")
        assert p.model_name == "gpt-4"

    def test_constructor_with_endpoint(self):
        p = OpenAICompatTextProcessor(
            api_key="sk-test",
            endpoint="http://localhost:11434",
        )
        assert p.client is not None

    def test_get_capabilities(self):
        p = OpenAICompatTextProcessor(api_key="sk-test")
        caps = p.get_capabilities()
        assert caps.text_generation is True
        assert caps.refinement is True
        assert caps.streaming_refinement is True
        assert caps.transcription is False

    def test_supports_refine_streaming(self):
        p = OpenAICompatTextProcessor(api_key="sk-test")
        assert p.supports_refine_streaming is True

    def test_default_prompts_contain_placeholder(self):
        p = OpenAICompatTextProcessor(api_key="sk-test")
        assert "{raw_text}" in p.prompts["refine_template"]

    def test_custom_prompts(self):
        prompts = {
            "system": "You are a helpful assistant.",
            "refine_template": "Fix this: {raw_text}",
        }
        p = OpenAICompatTextProcessor(api_key="sk-test", prompts=prompts)
        assert p.prompts["system"] == "You are a helpful assistant."
        assert p.prompts["refine_template"] == "Fix this: {raw_text}"


# ===================================================================
# Registry integration
# ===================================================================


class TestOpenAICompatRegistry:
    def test_create_from_registry(self):
        t = transcriber_registry.create(
            "openai-compat",
            api_key="sk-test",
            endpoint="http://localhost:11434",
        )
        assert isinstance(t, OpenAICompatTranscriber)

        p = text_processor_registry.create(
            "openai-compat",
            api_key="sk-test",
            model_name="llama3",
            endpoint="http://localhost:11434",
        )
        assert isinstance(p, OpenAICompatTextProcessor)
        assert p.model_name == "llama3"

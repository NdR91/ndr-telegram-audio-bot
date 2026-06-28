"""
Tests for OpenRouter capability probing (P2 extension).

Covers:
- :func:`_find_openrouter_model` exact and substring matching.
- :func:`_classify_openrouter_model` for text-only, audio, and unknown models.
- :func:`probe_openrouter_capabilities` with mocked HTTP responses.
- Error handling (network failure, model not found).
- Provider creation stores detected capabilities correctly.
- Setup wizard capability detection uses probe for OpenRouter.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from bot.capabilities import (
    CapabilityModel,
    _classify_openrouter_model,
    _classify_openrouter_metadata,
    _find_openrouter_model,
    probe_openrouter_capabilities,
)


# ===================================================================
# Fixtures — sample OpenRouter model responses
# ===================================================================

TEXT_ONLY_MODEL = {
    "id": "openai/gpt-4o",
    "canonical_slug": "openai/gpt-4o",
    "name": "GPT-4o",
    "architecture": {
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "modality": "text->text",
        "tokenizer": "GPT",
        "instruct_type": "chatml",
    },
    "supported_parameters": [
        "temperature", "top_p", "max_tokens", "stream",
        "frequency_penalty", "presence_penalty", "seed",
    ],
}

AUDIO_MODEL = {
    "id": "openai/whisper-1",
    "canonical_slug": "openai/whisper-1",
    "name": "Whisper-1",
    "architecture": {
        "input_modalities": ["audio"],
        "output_modalities": ["text"],
        "modality": "audio->text",
        "tokenizer": "Whisper",
        "instruct_type": None,
    },
    "supported_parameters": [],
}

GEMINI_AUDIO_MODEL = {
    "id": "google/gemini-2.0-flash",
    "canonical_slug": "google/gemini-2.0-flash",
    "name": "Gemini 2.0 Flash",
    "architecture": {
        "input_modalities": ["text", "image", "file", "audio", "video"],
        "output_modalities": ["text"],
        "modality": "multimodal->text",
        "tokenizer": "Gemini",
        "instruct_type": "chatml",
    },
    "supported_parameters": [
        "temperature", "top_p", "max_tokens", "stream",
    ],
}

IMAGE_ONLY_MODEL = {
    "id": "black-forest-labs/flux-schnell",
    "canonical_slug": "black-forest-labs/flux-schnell",
    "name": "FLUX Schnell",
    "architecture": {
        "input_modalities": ["text"],
        "output_modalities": ["image"],
        "modality": "text->image",
        "tokenizer": None,
        "instruct_type": None,
    },
    "supported_parameters": [],
}

MODEL_LIST = [TEXT_ONLY_MODEL, AUDIO_MODEL, GEMINI_AUDIO_MODEL, IMAGE_ONLY_MODEL]


# ===================================================================
# _find_openrouter_model
# ===================================================================


class TestFindOpenRouterModel:
    """Match model entries by id, slug, or name."""

    def test_exact_match_by_id(self):
        result = _find_openrouter_model(MODEL_LIST, "openai/gpt-4o")
        assert result is not None
        assert result["id"] == "openai/gpt-4o"

    def test_exact_match_by_canonical_slug(self):
        result = _find_openrouter_model(MODEL_LIST, "openai/whisper-1")
        assert result is not None
        assert result["id"] == "openai/whisper-1"

    def test_substring_match(self):
        result = _find_openrouter_model(MODEL_LIST, "gpt-4o")
        assert result is not None
        assert result["id"] == "openai/gpt-4o"

    def test_no_match(self):
        result = _find_openrouter_model(MODEL_LIST, "nonexistent-model")
        assert result is None

    def test_empty_query(self):
        result = _find_openrouter_model(MODEL_LIST, "")
        assert result is None

    def test_empty_list(self):
        result = _find_openrouter_model([], "gpt-4o")
        assert result is None

    def test_case_insensitive(self):
        result = _find_openrouter_model(MODEL_LIST, "OpenAI/GPT-4o")
        assert result is not None
        assert result["id"] == "openai/gpt-4o"


# ===================================================================
# _classify_openrouter_model
# ===================================================================


class TestClassifyOpenRouterModel:
    """Classification from model metadata."""

    def test_text_only_model(self):
        """Text-only model: no transcription, text+refinement+streaming."""
        caps = _classify_openrouter_model(TEXT_ONLY_MODEL)
        assert caps.transcription is False
        assert caps.text_generation is True
        assert caps.refinement is True
        assert caps.streaming_refinement is True

    def test_audio_model(self):
        """Audio transcription model: transcription=True, no streaming."""
        caps = _classify_openrouter_model(AUDIO_MODEL)
        assert caps.transcription is True
        assert caps.text_generation is True
        assert caps.refinement is True
        assert caps.streaming_refinement is False  # no "stream" in supported_params

    def test_multimodal_model(self):
        """Multimodal model with audio input: audio_input=True,
        transcription=False (no explicit STT keywords)."""
        caps = _classify_openrouter_model(GEMINI_AUDIO_MODEL)
        # Gemini on OpenRouter has audio input but no explicit STT keywords
        # — transcription is conservative False.
        assert caps.transcription is False
        assert caps.text_generation is True
        assert caps.refinement is True
        assert caps.streaming_refinement is True

    def test_image_only_model(self):
        """Image-only model: no text_generation, conservative."""
        caps = _classify_openrouter_model(IMAGE_ONLY_MODEL)
        assert caps.transcription is False
        assert caps.text_generation is False  # output_modalities=["image"]
        assert caps.refinement is False
        assert caps.streaming_refinement is False

    def test_whisper_keyword_detection(self):
        """Model with 'whisper' in id but no audio modality."""
        model = {
            "id": "other-provider/whisper-custom",
            "name": "Custom Whisper",
            "architecture": {
                "input_modalities": ["text"],
                "output_modalities": ["text"],
                "modality": "text->text",
            },
            "supported_parameters": [],
        }
        caps = _classify_openrouter_model(model)
        assert caps.transcription is True  # "whisper" in id — strong STT indicator

    def test_transcribe_keyword_in_name(self):
        """Model with 'transcribe' in name."""
        model = {
            "id": "some-org/some-model",
            "name": "Audio Transcriber Pro",
            "architecture": {
                "input_modalities": ["text"],
                "output_modalities": ["text"],
                "modality": "text->text",
            },
            "supported_parameters": [],
        }
        caps = _classify_openrouter_model(model)
        # "transcriber" contains "transcribe" which is a strong STT keyword
        assert caps.transcription is True
        assert caps.text_generation is True

    def test_unknown_modality_empty(self):
        """Empty output_modalities → conservative text=False."""
        model = {
            "id": "org/model",
            "name": "Model",
            "architecture": {"input_modalities": [], "output_modalities": [], "modality": None},
            "supported_parameters": [],
        }
        caps = _classify_openrouter_model(model)
        # No audio keywords → no transcription
        assert caps.transcription is False
        # Empty output_modalities list means the model doesn't produce text
        assert caps.text_generation is False
        assert caps.refinement is False
        assert caps.streaming_refinement is False

    def test_null_architecture(self):
        """Null architecture → all conservative False."""
        model = {
            "id": "org/model",
            "name": "Model",
            "architecture": None,
            "supported_parameters": [],
        }
        caps = _classify_openrouter_model(model)
        assert caps == CapabilityModel()


# ===================================================================
# _classify_openrouter_metadata
# ===================================================================


class TestClassifyOpenRouterMetadata:
    """Metadata classification separates audio_input from transcription."""

    def test_text_only_metadata(self):
        """Text-only model: audio_input=False, transcription=False."""
        from bot.capabilities import _classify_openrouter_metadata
        meta = _classify_openrouter_metadata(TEXT_ONLY_MODEL)
        assert meta["audio_input"] is False
        assert meta["transcription"] is False
        assert meta["text_generation"] is True
        assert meta["refinement"] is True
        assert meta["streaming_refinement"] is True

    def test_audio_stt_model_metadata(self):
        """Whisper model: audio_input=True, transcription=True."""
        from bot.capabilities import _classify_openrouter_metadata
        meta = _classify_openrouter_metadata(AUDIO_MODEL)
        assert meta["audio_input"] is True
        assert meta["transcription"] is True  # whisper in id
        assert meta["text_generation"] is True

    def test_audio_input_no_stt_metadata(self):
        """Gemini multimodal: audio_input=True, transcription=False."""
        from bot.capabilities import _classify_openrouter_metadata
        meta = _classify_openrouter_metadata(GEMINI_AUDIO_MODEL)
        # Has audio in input_modalities
        assert meta["audio_input"] is True
        # But no explicit STT keywords in id/name
        assert meta["transcription"] is False
        # Can generate text
        assert meta["text_generation"] is True

    def test_audio_input_only_no_stt(self):
        """Model with audio input but no STT keywords: audio_input=True, transcription=False."""
        from bot.capabilities import _classify_openrouter_metadata
        model = {
            "id": "some-org/some-model",
            "name": "Multimodal Model",
            "architecture": {
                "input_modalities": ["text", "audio"],
                "output_modalities": ["text"],
                "modality": "multimodal->text",
            },
            "supported_parameters": ["stream"],
        }
        meta = _classify_openrouter_metadata(model)
        assert meta["audio_input"] is True
        assert meta["transcription"] is False
        assert meta["text_generation"] is True

    def test_transcription_keyword_in_id(self):
        """Model with 'transcription' in id: transcription=True."""
        from bot.capabilities import _classify_openrouter_metadata
        model = {
            "id": "org/whisper-transcription-model",
            "name": "STT Model",
            "architecture": {
                "input_modalities": ["audio"],
                "output_modalities": ["text"],
                "modality": "audio->text",
            },
            "supported_parameters": [],
        }
        meta = _classify_openrouter_metadata(model)
        assert meta["audio_input"] is True
        assert meta["transcription"] is True  # "transcription" in id

    def test_null_architecture_metadata(self):
        """Null architecture → all False."""
        from bot.capabilities import _classify_openrouter_metadata
        model = {"id": "org/model", "name": "Model", "architecture": None}
        meta = _classify_openrouter_metadata(model)
        assert all(v is False for v in meta.values())


# ===================================================================
# probe_openrouter_capabilities — mocked HTTP
# ===================================================================


class TestProbeOpenRouterCapabilities:
    """End-to-end probe with mocked HTTP responses."""

    @pytest.mark.asyncio
    async def test_text_only_model_from_api(self):
        """Text-only model returns refinement-only capabilities."""

        async def mock_get(url, **kwargs):
            return httpx.Response(200, json={"data": [TEXT_ONLY_MODEL]})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = mock_get

        caps, meta = await probe_openrouter_capabilities(
            "sk-test", "https://openrouter.ai/api/v1", "gpt-4o",
            session=mock_client,
        )
        assert caps.transcription is False
        assert caps.text_generation is True
        assert caps.refinement is True
        assert caps.streaming_refinement is True
        assert meta["audio_input"] is False

    @pytest.mark.asyncio
    async def test_audio_model_from_api(self):
        """Audio transcription model returns transcription=True."""

        async def mock_get(url, **kwargs):
            return httpx.Response(200, json={"data": [AUDIO_MODEL]})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = mock_get

        caps, meta = await probe_openrouter_capabilities(
            "sk-test", "https://openrouter.ai/api/v1", "whisper-1",
            session=mock_client,
        )
        assert caps.transcription is True
        assert caps.text_generation is True
        # Whisper-1 has audio input and explicit transcription
        assert meta["audio_input"] is True

    @pytest.mark.asyncio
    async def test_model_not_found_returns_conservative(self):
        """Model not in returned list → all-False."""

        async def mock_get(url, **kwargs):
            return httpx.Response(200, json={"data": [TEXT_ONLY_MODEL]})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = mock_get

        caps, meta = await probe_openrouter_capabilities(
            "sk-test", "https://openrouter.ai/api/v1", "unknown-model",
            session=mock_client,
        )
        assert caps == CapabilityModel()
        assert meta["audio_input"] is False

    @pytest.mark.asyncio
    async def test_http_error_returns_conservative(self):
        """HTTP error → all-False."""

        async def mock_get(url, **kwargs):
            return httpx.Response(403, json={"error": {"message": "Forbidden"}})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = mock_get

        caps, meta = await probe_openrouter_capabilities(
            "sk-test", "https://openrouter.ai/api/v1", "gpt-4o",
            session=mock_client,
        )
        assert caps == CapabilityModel()
        assert meta["audio_input"] is False

    @pytest.mark.asyncio
    async def test_network_error_returns_conservative(self):
        """Network error → all-False."""

        async def mock_get(url, **kwargs):
            raise httpx.RequestError("Connection refused")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = mock_get

        caps, meta = await probe_openrouter_capabilities(
            "sk-test", "https://openrouter.ai/api/v1", "gpt-4o",
            session=mock_client,
        )
        assert caps == CapabilityModel()
        assert meta["audio_input"] is False

    @pytest.mark.asyncio
    async def test_empty_model_name_returns_conservative(self):
        """Empty model_name → model not found → all-False."""

        async def mock_get(url, **kwargs):
            return httpx.Response(200, json={"data": [TEXT_ONLY_MODEL]})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = mock_get

        caps, meta = await probe_openrouter_capabilities(
            "sk-test", "https://openrouter.ai/api/v1", "",
            session=mock_client,
        )
        assert caps == CapabilityModel()
        assert meta["audio_input"] is False

    @pytest.mark.asyncio
    async def test_multimodal_model_from_api(self):
        """Gemini multimodal model: audio_input=True, transcription=False
        (no explicit STT keywords), text_generation=True."""
        async def mock_get(url, **kwargs):
            return httpx.Response(200, json={"data": [GEMINI_AUDIO_MODEL]})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = mock_get

        caps, meta = await probe_openrouter_capabilities(
            "sk-test", "https://openrouter.ai/api/v1", "gemini-2.0-flash",
            session=mock_client,
        )
        # Gemini on OpenRouter has audio input but no explicit STT keywords
        # in its id/name — transcription is False (conservative).
        assert meta["audio_input"] is True
        assert caps.transcription is False
        # It can still generate text
        assert caps.text_generation is True
        assert caps.streaming_refinement is True

    @pytest.mark.asyncio
    async def test_empty_model_list_returns_conservative(self):
        """Empty data array → model not found → all-False."""

        async def mock_get(url, **kwargs):
            return httpx.Response(200, json={"data": []})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = mock_get

        caps, meta = await probe_openrouter_capabilities(
            "sk-test", "https://openrouter.ai/api/v1", "gpt-4o",
            session=mock_client,
        )
        assert caps == CapabilityModel()
        assert meta["audio_input"] is False

    @pytest.mark.asyncio
    async def test_defaults_to_openrouter_endpoint(self):
        """Empty endpoint defaults to OpenRouter public API."""

        async def mock_get(url, **kwargs):
            assert "openrouter.ai" in str(url)
            return httpx.Response(200, json={"data": [TEXT_ONLY_MODEL]})

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = mock_get

        caps, meta = await probe_openrouter_capabilities(
            "sk-test", "", "gpt-4o",
            session=mock_client,
        )
        assert caps.transcription is False
        assert meta["audio_input"] is False


# ===================================================================
# Integration: provider creation stores probed capabilities
# ===================================================================


class TestAdminProviderCreateOpenRouter:
    """Admin provider creation stores correct capabilities for OpenRouter."""

    def test_openrouter_provider_create_with_text_model(self, ready_app, monkeypatch):
        """Creating an OpenRouter text-only provider stores refinement-only caps."""
        from bot.web.app import create_app
        from bot.web.auth import set_admin_password
        from bot.web.setup_wizard import set_current_step, STEP_DONE

        # Use ready_app fixture directly
        app = ready_app

        async def mock_probe(api_key, endpoint, model_name, session=None):
            return (
                CapabilityModel(
                    transcription=False,
                    text_generation=True,
                    refinement=True,
                    streaming_refinement=True,
                ),
                {"audio_input": False, "transcription": False, "text_generation": True, "refinement": True, "streaming_refinement": True},
            )

        monkeypatch.setattr(
            "bot.web.app.probe_openrouter_capabilities",
            mock_probe,
        )

        with TestClient(app) as client:
            from tests.test_web_app import _authed_session, _extract_csrf

            session_cookie = _authed_session(client)
            resp = client.get("/admin/providers", cookies=session_cookie)
            csrf = _extract_csrf(resp.text)

            resp = client.post(
                "/admin/providers/create",
                data={
                    "csrf_token": csrf,
                    "provider_type": "openrouter",
                    "name": "OpenRouter test",
                    "endpoint": "https://openrouter.ai/api/v1",
                    "api_key": "sk-or-test",
                    "model_name": "openai/gpt-4o",
                },
                cookies=session_cookie,
                follow_redirects=False,
            )

        assert resp.status_code == 303
        assert resp.headers["location"] == "/admin/pipeline?success=provider_created"

        providers = app.state.db.list_providers()
        assert len(providers) == 1
        assert providers[0]["name"] == "OpenRouter test"
        assert providers[0]["adapter_type"] == "openai-compat"
        caps = providers[0]["capabilities"]
        # Text-only model: no transcription
        assert caps["transcription"] is False
        assert caps["text_generation"] is True
        assert caps["refinement"] is True
        assert caps["streaming_refinement"] is True

    def test_openrouter_provider_create_with_audio_model(self, ready_app, monkeypatch):
        """Creating an OpenRouter audio provider stores transcription cap."""
        app = ready_app

        async def mock_probe(api_key, endpoint, model_name, session=None):
            return (
                CapabilityModel(
                    transcription=True,
                    text_generation=True,
                    refinement=True,
                    streaming_refinement=False,
                ),
                {"audio_input": True, "transcription": True, "text_generation": True, "refinement": True, "streaming_refinement": False},
            )

        monkeypatch.setattr(
            "bot.web.app.probe_openrouter_capabilities",
            mock_probe,
        )

        with TestClient(app) as client:
            from tests.test_web_app import _authed_session, _extract_csrf

            session_cookie = _authed_session(client)
            resp = client.get("/admin/providers", cookies=session_cookie)
            csrf = _extract_csrf(resp.text)

            resp = client.post(
                "/admin/providers/create",
                data={
                    "csrf_token": csrf,
                    "provider_type": "openrouter",
                    "name": "OpenRouter Whisper",
                    "endpoint": "https://openrouter.ai/api/v1",
                    "api_key": "sk-or-test",
                    "model_name": "openai/whisper-1",
                },
                cookies=session_cookie,
                follow_redirects=False,
            )

        assert resp.status_code == 303

        providers = app.state.db.list_providers()
        openrouter_providers = [p for p in providers if p["name"] == "OpenRouter Whisper"]
        assert len(openrouter_providers) == 1
        caps = openrouter_providers[0]["capabilities"]
        assert caps["transcription"] is True
        assert caps["streaming_refinement"] is False

    def test_openrouter_provider_create_with_probe_failure(self, ready_app, monkeypatch):
        """When probe fails, capabilities should be conservative (all-False)."""
        app = ready_app

        async def mock_probe(api_key, endpoint, model_name, session=None):
            return (
                CapabilityModel(),
                {"audio_input": False, "transcription": False, "text_generation": False, "refinement": False, "streaming_refinement": False},
            )

        monkeypatch.setattr(
            "bot.web.app.probe_openrouter_capabilities",
            mock_probe,
        )

        with TestClient(app) as client:
            from tests.test_web_app import _authed_session, _extract_csrf

            session_cookie = _authed_session(client)
            resp = client.get("/admin/providers", cookies=session_cookie)
            csrf = _extract_csrf(resp.text)

            resp = client.post(
                "/admin/providers/create",
                data={
                    "csrf_token": csrf,
                    "provider_type": "openrouter",
                    "name": "OR failed",
                    "endpoint": "https://openrouter.ai/api/v1",
                    "api_key": "sk-or-bad",
                    "model_name": "unknown-model",
                },
                cookies=session_cookie,
                follow_redirects=False,
            )

        assert resp.status_code == 303

        providers = app.state.db.list_providers()
        or_providers = [p for p in providers if p["name"] == "OR failed"]
        assert len(or_providers) == 1
        caps = or_providers[0]["capabilities"]
        assert caps["transcription"] is False
        assert caps["text_generation"] is False
        assert caps["refinement"] is False
        assert caps["streaming_refinement"] is False

    def test_openai_provider_unchanged(self, ready_app, monkeypatch):
        """OpenAI provider creation still uses static detection."""
        app = ready_app

        # Ensure probe is never called for OpenAI
        probe_spy = AsyncMock()
        monkeypatch.setattr(
            "bot.web.app.probe_openrouter_capabilities",
            probe_spy,
        )

        with TestClient(app) as client:
            from tests.test_web_app import _authed_session, _extract_csrf

            session_cookie = _authed_session(client)
            resp = client.get("/admin/providers", cookies=session_cookie)
            csrf = _extract_csrf(resp.text)

            resp = client.post(
                "/admin/providers/create",
                data={
                    "csrf_token": csrf,
                    "provider_type": "openai",
                    "name": "OpenAI unchanged",
                    "endpoint": "https://api.openai.com/v1",
                    "api_key": "sk-test-provider",
                    "model_name": "gpt-4o-mini",
                },
                cookies=session_cookie,
                follow_redirects=False,
            )

        assert resp.status_code == 303
        # probe should NOT have been called for OpenAI provider
        probe_spy.assert_not_awaited()

        providers = app.state.db.list_providers()
        openai_providers = [p for p in providers if p["name"] == "OpenAI unchanged"]
        assert len(openai_providers) == 1
        caps = openai_providers[0]["capabilities"]
        # OpenAI always transcription=True (Whisper is separate service)
        assert caps["transcription"] is True

    def test_gemini_provider_unchanged(self, ready_app, monkeypatch):
        """Gemini provider creation still uses static detection."""
        app = ready_app

        probe_spy = AsyncMock()
        monkeypatch.setattr(
            "bot.web.app.probe_openrouter_capabilities",
            probe_spy,
        )

        with TestClient(app) as client:
            from tests.test_web_app import _authed_session, _extract_csrf

            session_cookie = _authed_session(client)
            resp = client.get("/admin/providers", cookies=session_cookie)
            csrf = _extract_csrf(resp.text)

            resp = client.post(
                "/admin/providers/create",
                data={
                    "csrf_token": csrf,
                    "provider_type": "gemini",
                    "name": "Gemini unchanged",
                    "endpoint": "",
                    "api_key": "test-key",
                    "model_name": "gemini-2.0-flash",
                },
                cookies=session_cookie,
                follow_redirects=False,
            )

        assert resp.status_code == 303
        probe_spy.assert_not_awaited()

        providers = app.state.db.list_providers()
        gemini_providers = [p for p in providers if p["name"] == "Gemini unchanged"]
        assert len(gemini_providers) == 1
        caps = gemini_providers[0]["capabilities"]
        assert caps["transcription"] is True


# ===================================================================
# Integration: setup wizard detect-capabilities for OpenRouter
# ===================================================================


class TestSetupWizardDetectOpenRouter:
    """Setup wizard detect-capabilities endpoint uses probe for OpenRouter."""

    def test_detect_capabilities_with_openrouter_probes(self, fresh_app, monkeypatch):
        """detect-capabilities probes OpenRouter when type=openrouter."""
        app = fresh_app

        # Store provider config so the route has API key
        from bot.web.setup_wizard import save_provider_config
        save_provider_config(app.state.db, "openrouter", "sk-or-test",
                             "https://openrouter.ai/api/v1", None)

        async def mock_probe(api_key, endpoint, model_name, session=None):
            assert api_key == "sk-or-test"
            return (
                CapabilityModel(
                    transcription=False,
                    text_generation=True,
                    refinement=True,
                    streaming_refinement=True,
                ),
                {"audio_input": False, "transcription": False, "text_generation": True, "refinement": True, "streaming_refinement": True},
            )

        monkeypatch.setattr(
            "bot.web.app.probe_openrouter_capabilities",
            mock_probe,
        )

        with TestClient(app) as client:
            resp = client.post(
                "/api/setup/detect-capabilities",
                json={
                    "type": "openrouter",
                    "models": ["openai/gpt-4o", "openai/gpt-4-turbo"],
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        caps = data["capabilities"]
        assert caps["transcription"] is False
        assert caps["text_generation"] is True
        assert caps["refinement"] is True
        assert caps["streaming_refinement"] is True
        # Should include models list
        assert "models" in caps

    def test_detect_capabilities_openrouter_no_api_key(self, fresh_app, monkeypatch):
        """Without saved API key, falls back to text-only caps."""
        app = fresh_app
        # Don't save provider config — no API key available

        probe_spy = AsyncMock()
        monkeypatch.setattr(
            "bot.web.app.probe_openrouter_capabilities",
            probe_spy,
        )

        with TestClient(app) as client:
            resp = client.post(
                "/api/setup/detect-capabilities",
                json={
                    "type": "openrouter",
                    "models": ["openai/gpt-4o"],
                },
            )

        assert resp.status_code == 200
        # probe should NOT be called (no API key saved)
        probe_spy.assert_not_awaited()
        data = resp.json()
        caps = data["capabilities"]
        # Falls back to text+refinement
        assert caps["transcription"] is False
        assert caps["text_generation"] is True
        assert caps["refinement"] is True


# ===================================================================
# Conftest helpers needed by integration tests
# ===================================================================


@pytest.fixture
def ready_app(tmp_path):
    """Return a FastAPI app with admin already configured."""
    from bot.web.app import create_app
    from bot.web.auth import set_admin_password
    from bot.web.setup_wizard import set_current_step, STEP_DONE
    from tests.test_web_app import _make_minimal_config

    config = _make_minimal_config(tmp_path)
    app = create_app(config=config)
    set_admin_password(app.state.db, "admin-password")
    app.state.db.set_setup_state("admin_created", "true")
    set_current_step(app.state.db, STEP_DONE)
    return app


@pytest.fixture
def fresh_app(tmp_path):
    """Return a FastAPI app with a fresh database."""
    from bot.web.app import create_app
    from tests.test_web_app import _make_minimal_config

    config = _make_minimal_config(tmp_path)
    return create_app(config=config)

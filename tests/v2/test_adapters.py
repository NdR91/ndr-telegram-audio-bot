from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from decimal import Decimal

from bot.transcription.adapters.openai import OpenAIAdapter
from bot.transcription.adapters.openrouter import OpenRouterAdapter
from bot.transcription.adapters.gemini import GeminiAdapter


async def test_openai_catalog_offers_current_models_without_unrelated_roles():
    class Models:
        async def list(self):
            return SimpleNamespace(data=[SimpleNamespace(id=name) for name in
                                         ("tts-1", "omni-moderation-latest", "gpt-6-luna")])
    adapter = OpenAIAdapter("synthetic", SimpleNamespace(models=Models()))
    transcription = await adapter.list_models("transcription")
    refinement = await adapter.list_models("refinement")
    assert transcription[0].id == "gpt-transcribe"
    assert "gpt-6-luna" in {model.id for model in refinement}
    assert not {"tts-1", "omni-moderation-latest"} & {model.id for model in refinement}


async def test_gemini_catalog_excludes_unverified_models():
    class Models:
        async def list(self):
            async def items():
                for name in ("gemini-3.6-flash", "gemini-3.5-flash-tts", "unknown"):
                    yield SimpleNamespace(name="models/" + name, supported_actions=["generateContent"])
            return items()
    adapter = GeminiAdapter("synthetic", SimpleNamespace(aio=SimpleNamespace(models=Models())))
    models = await adapter.list_models("multimodal")
    assert [model.id for model in models] == ["gemini-3.6-flash"]


async def test_openrouter_refinement_excludes_non_text_outputs():
    def handler(request):
        return httpx.Response(200, json={"data": [
            {"id": "speech", "architecture": {"input_modalities": ["text"], "output_modalities": ["audio"]}},
            {"id": "editor", "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
        ]})
    client = httpx.AsyncClient(base_url="https://openrouter.test/api/v1", transport=httpx.MockTransport(handler))
    try:
        models = await OpenRouterAdapter("synthetic", client).list_models("refinement")
    finally:
        await client.aclose()
    assert [model.id for model in models] == ["editor"]


async def test_openrouter_catalog_preserves_separate_price_units():
    def handler(request):
        return httpx.Response(200, json={"data": [{
            "id": "audio-editor", "name": "Audio editor",
            "architecture": {"input_modalities": ["audio", "text"], "output_modalities": ["text"]},
            "pricing": {"prompt": "0.000001", "completion": "0.000002",
                        "input_audio": "0.000004", "request": "0.01"},
        }]})
    client = httpx.AsyncClient(base_url="https://openrouter.test/api/v1", transport=httpx.MockTransport(handler))
    try:
        models = await OpenRouterAdapter("synthetic", client).list_models("refinement")
    finally:
        await client.aclose()
    assert models[0].pricing == {
        "prompt": "0.000001", "completion": "0.000002",
        "input_audio": "0.000004", "request": "0.01",
    }


@pytest.mark.parametrize("factory,model", [(OpenAIAdapter, "stt-choice"), (GeminiAdapter, "gemini-choice")])
async def test_selected_model_is_sent_to_native_endpoint(factory, model, tmp_path):
    calls = []
    if factory is OpenAIAdapter:
        class Client:
            class Audio:
                class Transcriptions:
                    async def create(self, **kwargs):
                        calls.append(kwargs["model"])
                        return SimpleNamespace(text="ok")
                transcriptions = Transcriptions()
            audio = Audio()
            async def close(self): pass
        adapter = factory("synthetic", Client())
        await adapter.transcribe(_audio(tmp_path), model)
    else:
        class Files:
            async def upload(self, **kwargs): return SimpleNamespace(name="upload")
            async def delete(self, **kwargs): pass
        class Models:
            async def generate_content(self, **kwargs):
                calls.append(kwargs["model"])
                return SimpleNamespace(text="ok")
        class Aio:
            files, models = Files(), Models()
        adapter = factory("synthetic", SimpleNamespace(aio=Aio()))
        await adapter.transcribe(_audio(tmp_path), model)
    assert calls == [model]


async def test_openai_multimodal_uses_selected_model_and_prompt(tmp_path):
    calls=[]
    class Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="spoken"))])
    class Chat: completions=Completions()
    class Client:
        chat=Chat()
        async def close(self): pass
    adapter=OpenAIAdapter("synthetic", Client())
    audio=Path(tmp_path) / "sample.wav"; audio.write_bytes(b"synthetic")
    result=await adapter.audio_to_text(audio,"chosen-audio","keep the speech faithful")
    assert result.text == "spoken"
    assert calls[0]["model"] == "chosen-audio"
    assert calls[0]["messages"][0]["content"] == "keep the speech faithful"
    assert calls[0]["messages"][1]["content"][1]["input_audio"]["format"] == "wav"


def test_catalog_filters_by_protocol_and_role():
    # Model capabilities are server metadata; adapter never infers them from IDs.
    choice = __import__("bot.transcription.models", fromlist=["ModelChoice"]).ModelChoice(
        "audio-x", "Audio X", frozenset({"multimodal"}), frozenset({"mp3"})
    )
    assert "multimodal" in choice.roles and "refinement" not in choice.roles


def test_provider_errors_are_sanitized():
    from bot.transcription.adapters.openai import _failure
    error = _failure("transcription")
    assert "secret" not in str(error)
    assert error.stage == "transcription"


@pytest.mark.parametrize("status,code,eligible", [
    (408, "provider_timeout", True),
    (429, "provider_rate_limited", True),
    (500, "provider_server_error", True),
    (503, "provider_server_error", True),
    (400, "provider_error", False),
    (401, "provider_error", False),
])
def test_provider_http_status_classification(status, code, eligible):
    from bot.transcription.errors import classify_provider_failure

    error = classify_provider_failure(
        httpx.HTTPStatusError(
            "synthetic provider body",
            request=httpx.Request("POST", "https://provider.test"),
            response=httpx.Response(status),
        ),
        "transcription",
    )
    assert error.code == code
    assert error.fallback_eligible is eligible
    assert "synthetic provider body" not in str(error)


@pytest.mark.parametrize("exception,code", [
    (httpx.ConnectError("synthetic network detail"), "provider_network_error"),
    (httpx.ReadTimeout("synthetic timeout detail"), "provider_timeout"),
    (TimeoutError("synthetic timeout detail"), "provider_timeout"),
])
def test_typed_network_causes_are_classified_without_exception_text(exception, code):
    from bot.transcription.errors import classify_provider_failure

    error = classify_provider_failure(exception, "refinement")
    assert error.code == code
    assert error.fallback_eligible is True
    assert "synthetic" not in str(error)


def test_non_provider_failures_and_empty_output_are_not_fallback_eligible():
    from bot.transcription.errors import ServiceFailure, classify_provider_failure

    provider = classify_provider_failure(ValueError("synthetic parse detail"), "catalog")
    empty = ServiceFailure("empty_response", "transcription", "Il servizio AI non ha restituito testo.")
    assert provider.code == "provider_error"
    assert provider.fallback_eligible is False
    assert empty.fallback_eligible is False


async def test_openai_adapter_classifies_sdk_timeout_without_exposing_details(tmp_path):
    from openai import APITimeoutError
    from bot.transcription.errors import ServiceFailure

    class Transcriptions:
        async def create(self, **kwargs):
            raise APITimeoutError(request=httpx.Request("POST", "https://api.openai.test"))
    class Audio: transcriptions = Transcriptions()
    class Client:
        audio = Audio()
        async def close(self): pass

    with pytest.raises(ServiceFailure) as captured:
        await OpenAIAdapter("synthetic", Client()).transcribe(_audio(tmp_path), "model")
    assert captured.value.code == "provider_timeout"
    assert captured.value.fallback_eligible is True


async def test_gemini_adapter_classifies_typed_server_status():
    from google.genai.errors import APIError
    from bot.transcription.errors import ServiceFailure

    class Models:
        async def generate_content(self, **kwargs):
            raise APIError(503, {"message": "synthetic private provider detail"})
    class Aio: models = Models()
    adapter = GeminiAdapter("synthetic", SimpleNamespace(aio=Aio()))

    with pytest.raises(ServiceFailure) as captured:
        await adapter.refine("synthetic input", "model", "system", "{raw_text}")
    assert captured.value.code == "provider_server_error"
    assert captured.value.fallback_eligible is True
    assert "synthetic private provider detail" not in str(captured.value)


async def test_openrouter_adapter_classifies_rate_limit_status(tmp_path):
    from bot.transcription.errors import ServiceFailure

    def handler(request):
        return httpx.Response(429, text="synthetic private provider detail")
    client = httpx.AsyncClient(
        base_url="https://openrouter.test/api/v1", transport=httpx.MockTransport(handler)
    )
    adapter = OpenRouterAdapter("synthetic", client)

    with pytest.raises(ServiceFailure) as captured:
        await adapter.refine("synthetic input", "model", "system", "{raw_text}")
    assert captured.value.code == "provider_rate_limited"
    assert captured.value.fallback_eligible is True
    assert "synthetic private provider detail" not in str(captured.value)
    await client.aclose()


async def test_openrouter_sends_selected_model_and_catalog_role():
    requests=[]
    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "chosen-stt", "name": "Chosen",
                "architecture": {"input_modalities": ["audio"], "output_modalities": ["transcription"]}}]})
        return httpx.Response(200, json={"text": "spoken", "usage": {"cost": 0.02}})
    client=httpx.AsyncClient(base_url="https://openrouter.test/api/v1", transport=httpx.MockTransport(handler))
    adapter=OpenRouterAdapter("synthetic", client)
    path=Path("/tmp/synthetic-s2-audio.mp3"); path.write_bytes(b"synthetic audio")
    try:
        choices=await adapter.list_models("transcription")
        result=await adapter.transcribe(path, "chosen-stt")
    finally:
        path.unlink(missing_ok=True)
        await client.aclose()
    assert [choice.id for choice in choices] == ["chosen-stt"]
    assert requests[0].url.params.get("output_modalities") == "transcription"
    assert requests[1].url.path.endswith("/audio/transcriptions")
    assert requests[1].read().find(b"chosen-stt") >= 0
    assert result.text == "spoken" and result.cost == Decimal("0.02")


async def test_gemini_remote_upload_is_deleted_after_generation_failure(tmp_path):
    actions=[]
    class Files:
        async def upload(self, **kwargs): actions.append("upload"); return SimpleNamespace(name="synthetic-upload")
        async def delete(self, **kwargs): actions.append(("delete", kwargs["name"]))
    class Models:
        async def generate_content(self, **kwargs): actions.append(("generate", kwargs["model"])); raise RuntimeError("synthetic")
    class Aio:
        files, models = Files(), Models()
    adapter=GeminiAdapter("synthetic", SimpleNamespace(aio=Aio()))
    with pytest.raises(Exception, match="non ha completato"):
        await adapter.audio_to_text(_audio(tmp_path), "chosen-gemini", "faithful")
    assert actions == ["upload", ("generate", "chosen-gemini"), ("delete", "synthetic-upload")]


async def test_gemini_remote_upload_is_deleted_after_cancellation(tmp_path):
    import asyncio
    actions=[]; entered=asyncio.Event()
    class Files:
        async def upload(self, **kwargs): return SimpleNamespace(name="synthetic-upload")
        async def delete(self, **kwargs): actions.append("delete")
    class Models:
        async def generate_content(self, **kwargs): entered.set(); await asyncio.Event().wait()
    class Aio: files, models = Files(), Models()
    adapter=GeminiAdapter("synthetic", SimpleNamespace(aio=Aio()))
    task=asyncio.create_task(adapter.audio_to_text(_audio(tmp_path), "chosen-gemini", "faithful"))
    await entered.wait(); task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert actions == ["delete"]


async def test_gemini_catalog_uses_async_model_list_metadata():
    class Model:
        name="models/gemini-3.6-flash"; supported_actions=["generateContent"]
    class Models:
        async def list(self):
            async def items(): yield Model()
            return items()
    class Aio: models=Models()
    choices=await GeminiAdapter("synthetic", SimpleNamespace(aio=Aio())).list_models("multimodal")
    assert [choice.id for choice in choices] == ["gemini-3.6-flash"]


def _audio(tmp_path):
    path = Path(tmp_path) / "fixture.mp3"
    path.write_bytes(b"synthetic audio")
    return path

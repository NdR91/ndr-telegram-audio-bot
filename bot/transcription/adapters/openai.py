"""OpenAI V2 adapter using Chat Completions and Audio Transcriptions."""

import base64
from pathlib import Path

from bot.transcription.errors import ServiceFailure
from bot.transcription.models import ModelChoice, ModelRole, StageResult
from bot.transcription.prompts import render_refinement

_TRANSCRIPTION_FORMATS = frozenset({"mp3", "mp4", "mpeg", "mpga", "m4a", "wav", "webm"})
_MULTIMODAL_FORMATS = frozenset({"wav"})
_REFINEMENT_MODELS = (
    ("gpt-6-luna", "GPT-6 Luna"),
    ("gpt-6.1-terra", "GPT-6.1 Terra"),
    ("gpt-6.1-sol", "GPT-6.1 Sol"),
    ("gpt-6-astra", "GPT-6 Astra"),
)


class OpenAIAdapter:
    def __init__(self, api_key: str, client=None):
        if client is None:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=api_key)
        self._client = client

    def accepted_formats(self, model: str, role: ModelRole) -> frozenset[str]:
        if role == "multimodal":
            return _MULTIMODAL_FORMATS
        return _TRANSCRIPTION_FORMATS if role == "transcription" else frozenset()

    async def list_models(self, role: ModelRole) -> list[ModelChoice]:
        if role == "multimodal":
            return [ModelChoice("gpt-audio-1.5", "GPT Audio 1.5", frozenset({role}), _MULTIMODAL_FORMATS)]
        if role == "transcription":
            return [ModelChoice("gpt-transcribe", "GPT Transcribe", frozenset({role}), _TRANSCRIPTION_FORMATS)]
        return [ModelChoice(model_id, label, frozenset({"refinement"}), frozenset())
                for model_id, label in _REFINEMENT_MODELS]

    async def audio_to_text(self, audio: Path, model: str, instructions: str) -> StageResult:
        try:
            data = base64.b64encode(audio.read_bytes()).decode("ascii")
            fmt = audio.suffix.lower().lstrip(".")
            response = await self._client.chat.completions.create(
                model=model,
                modalities=["text"],
                messages=[
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": [
                        {"type": "text", "text": "Transcribe the speech faithfully."},
                        {"type": "input_audio", "input_audio": {"data": data, "format": fmt}},
                    ]},
                ],
            )
            content = response.choices[0].message.content
            return StageResult(str(content or ""), model)
        except Exception as exc:
            raise _failure("multimodal") from exc

    async def transcribe(self, audio: Path, model: str) -> StageResult:
        try:
            response = await self._client.audio.transcriptions.create(
                model=model, file=(audio.name, audio.read_bytes())
            )
            return StageResult(str(response.text or ""), model)
        except Exception as exc:
            raise _failure("transcription") from exc

    async def refine(self, text: str, model: str, system: str, template: str) -> StageResult:
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": render_refinement(template, text)},
                ],
            )
            return StageResult(str(response.choices[0].message.content or ""), model)
        except Exception as exc:
            raise _failure("refinement") from exc

    async def aclose(self) -> None:
        await self._client.close()


def _failure(stage: str) -> ServiceFailure:
    return ServiceFailure("provider_error", stage, "Il servizio AI non ha completato la richiesta.")

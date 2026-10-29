"""OpenRouter adapter over its OpenAI-compatible HTTP API."""

import base64
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

from bot.transcription.errors import ServiceFailure
from bot.transcription.models import ModelChoice, ModelRole, StageResult
from bot.transcription.prompts import render_refinement

_TRANSCRIPTION_FORMATS = frozenset({"mp3", "wav", "m4a", "ogg", "webm", "flac", "aac"})
_MULTIMODAL_FORMATS = frozenset({"mp3", "wav"})


class OpenRouterAdapter:
    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            headers={"Authorization": f"Bearer {api_key}"}, timeout=120,
        )
        self._owns_client = client is None

    def accepted_formats(self, model: str, role: ModelRole) -> frozenset[str]:
        if role == "multimodal":
            return _MULTIMODAL_FORMATS
        return _TRANSCRIPTION_FORMATS if role == "transcription" else frozenset()

    async def list_models(self, role: ModelRole) -> list[ModelChoice]:
        try:
            modalities = "audio" if role == "multimodal" else "text"
            params = {"output_modalities": "transcription"} if role == "transcription" else {"input_modalities": modalities}
            response = await self._client.get("/models", params=params)
            response.raise_for_status()
            choices = []
            for item in response.json().get("data", []):
                modalities = set(item.get("architecture", {}).get("input_modalities", []))
                outputs = set(item.get("architecture", {}).get("output_modalities", []))
                roles = set()
                if "audio" in modalities and "text" in outputs: roles.add("multimodal")
                if "audio" in modalities and "transcription" in outputs:
                    roles.add("transcription")
                if "text" in modalities and "text" in outputs: roles.add("refinement")
                if role in roles:
                    formats = _MULTIMODAL_FORMATS if "multimodal" in roles else _TRANSCRIPTION_FORMATS
                    choices.append(ModelChoice(item["id"], item.get("name") or item["id"], frozenset(roles), formats))
            return choices
        except Exception as exc:
            raise _failure("catalog") from exc

    async def audio_to_text(self, audio: Path, model: str, instructions: str) -> StageResult:
        data = base64.b64encode(audio.read_bytes()).decode("ascii")
        fmt = audio.suffix.lower().lstrip(".")
        return await self._chat(model, [{"type": "text", "text": instructions}, {"type": "input_audio", "input_audio": {"data": data, "format": fmt}}], "multimodal")

    async def transcribe(self, audio: Path, model: str) -> StageResult:
        try:
            with audio.open("rb") as handle:
                response = await self._client.post("/audio/transcriptions", data={"model": model}, files={"file": (audio.name, handle)})
            response.raise_for_status()
            payload = response.json()
            return StageResult(str(payload.get("text") or ""), model, _reported_cost(payload))
        except Exception as exc:
            raise _failure("transcription") from exc

    async def refine(self, text: str, model: str, system: str, template: str) -> StageResult:
        user = render_refinement(template, text)
        return await self._chat(model, [{"type": "text", "text": user}], "refinement", system)

    async def _chat(self, model: str, content: list[dict], stage: str, system: str = "") -> StageResult:
        try:
            messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
            response = await self._client.post("/chat/completions", json={"model": model, "messages": messages})
            response.raise_for_status()
            text = response.json()["choices"][0]["message"].get("content") or ""
            return StageResult(str(text), model, _reported_cost(response.json()))
        except Exception as exc:
            raise _failure(stage) from exc

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _failure(stage: str) -> ServiceFailure:
    return ServiceFailure("provider_error", stage, "Il servizio AI non ha completato la richiesta.")


def _reported_cost(payload):
    usage = payload.get("usage") or {}
    value = usage.get("cost")
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None

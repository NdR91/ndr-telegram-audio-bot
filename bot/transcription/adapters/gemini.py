"""Gemini adapter using generate_content and the Files API for audio."""

from pathlib import Path

from bot.transcription.errors import ServiceFailure
from bot.transcription.models import ModelChoice, ModelRole, StageResult
from bot.transcription.prompts import render_refinement

_AUDIO_FORMATS = frozenset({"mp3", "wav", "m4a", "ogg", "webm", "flac", "aac", "opus"})


class GeminiAdapter:
    def __init__(self, api_key: str, client=None):
        if client is None:
            from google import genai
            client = genai.Client(api_key=api_key)
        self._client = client

    def accepted_formats(self, model: str, role: ModelRole) -> frozenset[str]:
        return _AUDIO_FORMATS if role in ("multimodal", "transcription") else frozenset()

    async def list_models(self, role: ModelRole) -> list[ModelChoice]:
        try:
            response = await self._client.aio.models.list()
            choices = []
            for item in response:
                name = item.name.removeprefix("models/")
                methods = getattr(item, "supported_actions", ()) or ()
                if "generateContent" in methods:
                    roles = frozenset({"multimodal", "transcription", "refinement"})
                    choices.append(ModelChoice(name, name, roles, _AUDIO_FORMATS))
            return [choice for choice in choices if role in choice.roles]
        except Exception as exc:
            raise _failure("catalog") from exc

    async def audio_to_text(self, audio: Path, model: str, instructions: str) -> StageResult:
        return await self._audio_generate(audio, model, instructions)

    async def transcribe(self, audio: Path, model: str) -> StageResult:
        return await self._audio_generate(audio, model, "Transcribe the speech faithfully.")

    async def _audio_generate(self, audio: Path, model: str, prompt: str) -> StageResult:
        uploaded = None
        try:
            uploaded = await self._client.aio.files.upload(file=audio)
            response = await self._client.aio.models.generate_content(
                model=model, contents=[prompt, uploaded]
            )
            return StageResult(str(response.text or ""), model)
        except Exception as exc:
            raise _failure("multimodal") from exc
        finally:
            if uploaded is not None:
                try:
                    await self._client.aio.files.delete(name=uploaded.name)
                except Exception:
                    pass

    async def refine(self, text: str, model: str, system: str, template: str) -> StageResult:
        try:
            response = await self._client.aio.models.generate_content(
                model=model,
                contents=f"{system}\n\n{render_refinement(template, text)}",
            )
            return StageResult(str(response.text or ""), model)
        except Exception as exc:
            raise _failure("refinement") from exc

    async def aclose(self) -> None:
        close = getattr(self._client, "aclose", None)
        if close:
            await close()


def _failure(stage: str) -> ServiceFailure:
    return ServiceFailure("provider_error", stage, "Il servizio AI non ha completato la richiesta.")

"""
OpenAI-compatible transcription and text-processing adapters (P3).

Works with any OpenAI-compatible API endpoint, including:

- OpenRouter (``https://openrouter.ai/api/v1``)
- Ollama (``http://localhost:11434/v1``)
- vLLM (``http://localhost:8000/v1``)
- Custom / self-hosted endpoints

These adapters do **not** duplicate the OpenAI-native adapter logic —
they use the same OpenAI client library with a configurable ``base_url``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Optional

import openai
from openai import AsyncOpenAI, OpenAI

from bot import constants as c
from bot.capabilities import CapabilityModel
from bot.exceptions import RefineError, RefineTimeout, TranscribeError, TranscribeTimeout
from bot.providers import (
    RefineStreamEvent,
    TextProcessor,
    Transcriber,
    TranscriptionResult,
    _log_provider_failure,
    _log_text_preview,
)

logger = logging.getLogger(__name__)


def _normalise_endpoint(endpoint: str) -> str:
    """Return a ``base_url``-compatible string from *endpoint*.

    If *endpoint* is empty, defaults to the OpenAI public API.
    Otherwise strips trailing slashes and appends ``/v1`` when the
    path does not already end with it.
    """
    if not endpoint:
        return "https://api.openai.com/v1"
    base = endpoint.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base


# ---------------------------------------------------------------------------
# Transcriber
# ---------------------------------------------------------------------------


class OpenAICompatTranscriber(Transcriber):
    """OpenAI-compatible transcription adapter.

    Uses the ``/v1/audio/transcriptions`` endpoint of any OpenAI-compatible
    API.  The caller supplies the *endpoint* URL (e.g.
    ``https://openrouter.ai/api/v1``); the adapter appends ``/v1`` if
    needed.
    """

    def __init__(self, api_key: str, endpoint: str = "") -> None:
        base_url = _normalise_endpoint(endpoint)
        self.client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0)

    def get_capabilities(self) -> CapabilityModel:
        return CapabilityModel(transcription=True)

    async def transcribe(self, file_path: str) -> TranscriptionResult:
        logger.info("Transcribe %s with OpenAI-compatible endpoint", file_path)

        def _sync() -> str:
            client = self.client.with_options(
                timeout=c.PROGRESS_TIMEOUTS.get("transcribe", 120),
                max_retries=0,
            )
            return client.audio.transcriptions.create(
                model="whisper-1",
                file=open(file_path, "rb"),
                temperature=0,
            )

        try:
            result = await asyncio.to_thread(_sync)
        except openai.APITimeoutError as e:
            _log_provider_failure("openai-compat", "transcribe", e)
            raise TranscribeTimeout("Timeout in transcribe", c.MSG_TIMEOUT_TRANSCRIBE) from e
        except Exception as e:
            _log_provider_failure("openai-compat", "transcribe", e)
            raise TranscribeError(
                f"OpenAI-compatible transcription failed: {e}",
                c.MSG_ERROR_TRANSCRIBE,
            ) from e

        text = result.text
        _log_text_preview("Raw text", text)
        return TranscriptionResult(text=text)


# ---------------------------------------------------------------------------
# Text processor
# ---------------------------------------------------------------------------


class OpenAICompatTextProcessor(TextProcessor):
    """OpenAI-compatible text refinement adapter.

    Uses the Chat Completions (or Responses) API at any OpenAI-compatible
    endpoint with a configurable *model_name*.
    """

    supports_refine_streaming = True

    def __init__(
        self,
        api_key: str,
        model_name: str = "gpt-4o-mini",
        endpoint: str = "",
        prompts: Optional[dict] = None,
    ) -> None:
        base_url = _normalise_endpoint(endpoint)
        self.client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0)
        self.async_client = AsyncOpenAI(
            api_key=api_key, base_url=base_url, max_retries=0,
        )
        self.model_name = model_name
        self.prompts = prompts or {
            "system": (
                "Sei un esperto di trascrizione audio. Correggi errori automatici, "
                "aggiungi punteggiatura, mantieni il significato originale e "
                "restituisci SOLO il testo corretto senza commenti."
            ),
            "refine_template": (
                "Questo è un testo generato da una trascrizione automatica. "
                "Correggilo da eventuali errori, aggiungi la punteggiatura, "
                "riformula se ti rendi conto che la trascrizione è inaccurate, "
                "ma rimani il più aderente possibile al testo originale. "
                "Considera la presenza di eventuali esitazioni e ripetizioni, "
                "rendile adatte ad un testo scritto.\n"
                "IMPORTANTE: Restituisci SOLO il testo rielaborato. NON aggiungere "
                "commenti introduttivi, premese o saluti.\n\n"
                "Testo originale:\n{raw_text}\n\nTesto rielaborato:\n"
            ),
        }

    def get_capabilities(self) -> CapabilityModel:
        return CapabilityModel(
            text_generation=True,
            refinement=True,
            streaming_refinement=True,
        )

    async def process(self, raw_text: str) -> str:
        logger.info("Refine text with OpenAI-compatible endpoint")
        prompt = self.prompts["refine_template"].format(raw_text=raw_text)

        def _sync() -> str:
            client = self.client.with_options(
                timeout=c.PROGRESS_TIMEOUTS.get("refine", 90),
                max_retries=0,
            )
            return client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": self.prompts["system"]},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=4096,
                temperature=0.7,
            )

        try:
            resp = await asyncio.to_thread(_sync)
        except openai.APITimeoutError as e:
            _log_provider_failure("openai-compat", "refine", e)
            raise RefineTimeout("Timeout in refine", c.MSG_TIMEOUT_REFINE) from e
        except Exception as e:
            _log_provider_failure("openai-compat", "refine", e)
            raise RefineError(
                f"OpenAI-compatible refinement failed: {e}",
                c.MSG_ERROR_REFINE,
            ) from e

        content = resp.choices[0].message.content
        out = content.strip() if content else ""
        _log_text_preview("Refined text", out)
        return out

    async def stream_process(self, raw_text: str) -> AsyncIterator[RefineStreamEvent]:
        logger.info("Stream refine text with OpenAI-compatible endpoint")
        prompt = self.prompts["refine_template"].format(raw_text=raw_text)
        accumulated: list[str] = []
        finalized_text: str | None = None

        try:
            client = self.async_client.with_options(
                timeout=c.PROGRESS_TIMEOUTS.get("refine", 90),
                max_retries=0,
            )
            stream = await client.responses.create(
                model=self.model_name,
                instructions=self.prompts["system"],
                input=prompt,
                stream=True,
            )

            async for event in stream:
                if event.type == "response.output_text.delta":
                    accumulated.append(event.delta)
                    yield RefineStreamEvent(type="delta", text=event.delta)
                elif event.type == "response.output_text.done":
                    finalized_text = event.text
                elif event.type == "error":
                    raise RefineError(
                        "OpenAI-compatible streaming refinement failed",
                        c.MSG_ERROR_REFINE,
                    )
                elif event.type == "response.completed":
                    completed = finalized_text if finalized_text is not None else "".join(
                        accumulated
                    )
                    _log_text_preview("Refined text", completed)
                    yield RefineStreamEvent(type="done", text=completed)
                    return
        except openai.APITimeoutError as e:
            _log_provider_failure("openai-compat", "stream_refine", e)
            raise RefineTimeout("Timeout in refine", c.MSG_TIMEOUT_REFINE) from e
        except RefineError:
            raise
        except Exception as e:
            _log_provider_failure("openai-compat", "stream_refine", e)
            raise RefineError(
                f"OpenAI-compatible streaming refinement failed: {e}",
                c.MSG_ERROR_REFINE,
            ) from e

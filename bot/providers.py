"""
Provider abstractions for the Telegram Audio Bot.

Architecture (P1 — Phase 3)
---------------------------
Transcriber  ──>  TranscriptionResult  (audio → text)
TextProcessor ──>  str                 (text → refined text)

Legacy :class:`LLMProvider` is retained for backward compatibility.
New code should depend on :class:`Transcriber` and :class:`TextProcessor`
independently so that a connection can support only transcription, only
text processing, or both.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

import google.genai as genai
import openai
from openai import AsyncOpenAI, OpenAI

from bot import constants as c
from bot.exceptions import (
    ProviderCircuitOpen,
    RefineError,
    RefineTimeout,
    TranscribeError,
    TranscribeTimeout,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RefineStreamEvent:
    """Normalized provider-agnostic refine streaming event."""

    type: str  # "delta" | "done"
    text: str


@dataclass(frozen=True)
class TranscriptionResult:
    """Normalized result from a transcriber.

    Preserves optional metadata without requiring the Telegram UI to
    expose it.  Fields beyond *text* are populated only when the
    underlying adapter and model provide them.
    """

    text: str
    language: Optional[str] = None
    duration_seconds: Optional[float] = None
    segments: Optional[list[dict[str, Any]]] = None


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------


def _log_provider_failure(provider_name: str, operation: str, error: Exception) -> None:
    logger.error(
        "Provider operation failed | provider=%s operation=%s error=%s",
        provider_name,
        operation,
        error.__class__.__name__,
    )


def _allow_sensitive_logging() -> bool:
    return os.getenv("LOG_SENSITIVE_TEXT", "0").strip().lower() in {"1", "true", "yes"}


def _log_text_preview(label: str, text: str | None) -> None:
    if text is None:
        logger.debug("%s: <none>", label)
        return

    text_len = len(text)
    if _allow_sensitive_logging():
        logger.debug("%s (%s chars): %s", label, text_len, text)
        return

    logger.debug("%s (%s chars): <hidden>", label, text_len)


# ---------------------------------------------------------------------------
# Circuit-breaker helpers (shared by ResilientTranscriber & ResilientTextProcessor)
# ---------------------------------------------------------------------------


class _CircuitBreaker:
    """Lightweight circuit-breaker state machine."""

    def __init__(self, failure_threshold: int, cooldown_seconds: int):
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(1, cooldown_seconds)
        self._failure_count = 0
        self._opened_at = 0.0

    def check(self) -> None:
        if self._opened_at <= 0:
            return
        elapsed = time.monotonic() - self._opened_at
        if elapsed >= self.cooldown_seconds:
            self._opened_at = 0.0
            return
        raise ProviderCircuitOpen(
            "Provider circuit open",
            c.MSG_PROVIDER_TEMPORARILY_UNAVAILABLE,
        )

    def record_success(self) -> None:
        self._failure_count = 0
        self._opened_at = 0.0

    def record_failure(self) -> None:
        self._failure_count += 1
        if self._failure_count >= self.failure_threshold:
            self._opened_at = time.monotonic()
            logger.warning(
                "Circuit opened | failure_count=%s cooldown_seconds=%s",
                self._failure_count,
                self.cooldown_seconds,
            )

    async def call(self, operation_name: str, operation, *args):
        self.check()
        try:
            result = await operation(*args)
        except ProviderCircuitOpen:
            raise
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result


# ===================================================================
# NEW (P1) — Transcriber & TextProcessor interfaces
# ===================================================================


class Transcriber(ABC):
    """Audio transcription interface.

    Implementations convert an audio file at *file_path* into a
    :class:`TranscriptionResult`.
    """

    @abstractmethod
    async def transcribe(self, file_path: str) -> TranscriptionResult:
        """Transcribe *file_path* and return a normalized result."""
        ...

    def get_capabilities(self) -> CapabilityModel:
        """Return the capabilities this transcriber provides.

        Override in subclasses that know their capabilities statically.
        """
        from bot.capabilities import CapabilityModel  # avoid circular import in module scope
        return CapabilityModel(transcription=True)

    def accepted_formats(self) -> frozenset[str]:
        """Return the set of file extensions this transcriber accepts natively.

        When the source file's extension is in this set, the pipeline can
        skip FFmpeg conversion and pass the file directly to
        :meth:`transcribe`.

        The default returns ``frozenset({'mp3'})`` for backward
        compatibility.  Subclasses that support additional formats should
        override this method.
        """
        return frozenset({"mp3"})


class TextProcessor(ABC):
    """Text refinement interface.

    Implementations refine a raw transcription into clean text.
    """

    supports_refine_streaming = False

    @abstractmethod
    async def process(self, raw_text: str) -> str:
        """Refine *raw_text* and return the cleaned result."""
        ...

    async def stream_process(self, raw_text: str) -> AsyncIterator[RefineStreamEvent]:
        """Yield refine stream events.

        Default fallback: single delta followed by done.
        """
        result = await self.process(raw_text)
        yield RefineStreamEvent(type="delta", text=result)
        yield RefineStreamEvent(type="done", text=result)

    def get_capabilities(self) -> CapabilityModel:
        """Return the capabilities this text processor provides.

        Override in subclasses that know their capabilities statically.
        """
        from bot.capabilities import CapabilityModel
        return CapabilityModel(
            text_generation=True,
            refinement=True,
            streaming_refinement=self.supports_refine_streaming,
        )


# ===================================================================
# NEW (P1) — Resilience wrappers for the split interfaces
# ===================================================================


class ResilientTranscriber(Transcriber):
    """Circuit-breaker wrapper around a :class:`Transcriber`."""

    def __init__(
        self,
        transcriber: Transcriber,
        provider_name: str = "",
        failure_threshold: int = 3,
        cooldown_seconds: int = 60,
    ):
        self._inner = transcriber
        self.provider_name = provider_name
        self._cb = _CircuitBreaker(failure_threshold, cooldown_seconds)

    def get_capabilities(self) -> CapabilityModel:
        """Delegate to inner transcriber."""
        return self._inner.get_capabilities()

    def accepted_formats(self) -> frozenset[str]:
        """Delegate to inner transcriber."""
        return self._inner.accepted_formats()

    async def transcribe(self, file_path: str) -> TranscriptionResult:
        return await self._cb.call("transcribe", self._inner.transcribe, file_path)


class ResilientTextProcessor(TextProcessor):
    """Circuit-breaker wrapper around a :class:`TextProcessor`."""

    def __init__(
        self,
        processor: TextProcessor,
        provider_name: str = "",
        failure_threshold: int = 3,
        cooldown_seconds: int = 60,
    ):
        self._inner = processor
        self.provider_name = provider_name
        self._cb = _CircuitBreaker(failure_threshold, cooldown_seconds)

    @property
    def supports_refine_streaming(self) -> bool:
        return getattr(self._inner, "supports_refine_streaming", False)

    def get_capabilities(self) -> CapabilityModel:
        """Delegate to inner text processor."""
        return self._inner.get_capabilities()

    async def process(self, raw_text: str) -> str:
        return await self._cb.call("refine", self._inner.process, raw_text)

    async def stream_process(self, raw_text: str) -> AsyncIterator[RefineStreamEvent]:
        self._cb.check()
        try:
            async for event in self._inner.stream_process(raw_text):
                yield event
        except ProviderCircuitOpen:
            raise
        except Exception:
            self._cb.record_failure()
            raise
        self._cb.record_success()


# ===================================================================
# LEGACY — LLMProvider (kept for backward compatibility)
# ===================================================================


class LLMProvider(ABC):
    """Abstract Base Class for LLM Providers.

    .. deprecated::
        Prefer :class:`Transcriber` and :class:`TextProcessor` for new code.
    """

    supports_refine_streaming = False

    @abstractmethod
    async def transcribe_audio(self, file_path: str) -> str:
        """Transcribes an audio file to text."""
        ...

    @abstractmethod
    async def refine_text(self, raw_text: str) -> str:
        """Refines the text using an LLM."""
        ...

    async def stream_refine_text(self, raw_text: str):
        """Yield provider-agnostic refine stream events.

        Default behavior is a compatibility fallback that emits the final
        result as a single delta followed by done.
        """
        refined_text = await self.refine_text(raw_text)
        yield RefineStreamEvent(type="delta", text=refined_text)
        yield RefineStreamEvent(type="done", text=refined_text)

    def get_capabilities(self) -> CapabilityModel:
        """Return capabilities for this legacy provider.

        Base implementation assumes transcription is available and
        refinement follows ``supports_refine_streaming``.
        """
        from bot.capabilities import CapabilityModel
        return CapabilityModel(
            transcription=True,
            refinement=self.supports_refine_streaming,
            streaming_refinement=self.supports_refine_streaming,
        )

    # ---- New interface delegation (optional override) ----

    async def transcribe(self, file_path: str) -> TranscriptionResult:
        """P1 interface.  Default: wraps :meth:`transcribe_audio`."""
        text = await self.transcribe_audio(file_path)
        return TranscriptionResult(text=text)

    def accepted_formats(self) -> frozenset[str]:
        """Return the accepted formats for this legacy provider.

        Subclasses that know their supported formats should override.
        Default is ``frozenset({'mp3'})``.
        """
        return frozenset({"mp3"})

    async def process(self, raw_text: str) -> str:
        """P1 interface.  Default: delegates to :meth:`refine_text`."""
        return await self.refine_text(raw_text)

    async def stream_process(self, raw_text: str) -> AsyncIterator[RefineStreamEvent]:
        """P1 interface.  Default: delegates to :meth:`stream_refine_text`."""
        async for event in self.stream_refine_text(raw_text):
            yield event


class ResilientProvider(LLMProvider):
    """Circuit-breaker wrapper around an LLM provider.

    .. deprecated::
        Prefer :class:`ResilientTranscriber` and :class:`ResilientTextProcessor`
        for new code.
    """

    def __init__(
        self,
        provider: LLMProvider,
        provider_name: str,
        failure_threshold: int,
        cooldown_seconds: int,
    ):
        self.provider = provider
        self.provider_name = provider_name
        self._cb = _CircuitBreaker(failure_threshold, cooldown_seconds)

    @property
    def model_name(self) -> str:
        return getattr(self.provider, "model_name", "unknown")

    @property
    def supports_refine_streaming(self) -> bool:
        return getattr(self.provider, "supports_refine_streaming", False)

    def get_capabilities(self) -> CapabilityModel:
        """Build from the wrapped provider."""
        caps = getattr(self.provider, "get_capabilities", None)
        if callable(caps):
            return caps()
        from bot.capabilities import CapabilityModel
        return CapabilityModel(
            transcription=True,  # assume legacy providers can transcribe
            refinement=self.supports_refine_streaming,
            streaming_refinement=self.supports_refine_streaming,
        )

    def accepted_formats(self) -> frozenset[str]:
        """Delegate to the wrapped provider when available."""
        fn = getattr(self.provider, "accepted_formats", None)
        if callable(fn):
            return fn()
        return frozenset({"mp3"})

    async def transcribe_audio(self, file_path: str) -> str:
        return await self._cb.call("transcribe", self.provider.transcribe_audio, file_path)

    async def refine_text(self, raw_text: str) -> str:
        return await self._cb.call("refine", self.provider.refine_text, raw_text)

    async def stream_refine_text(self, raw_text: str):
        self._cb.check()
        try:
            async for event in self.provider.stream_refine_text(raw_text):
                yield event
        except ProviderCircuitOpen:
            raise
        except Exception:
            self._cb.record_failure()
            raise
        self._cb.record_success()

    # ---- Transcriber / TextProcessor bridge ----

    async def transcribe(self, file_path: str) -> TranscriptionResult:
        # Prefer the new interface when the wrapped provider supports it.
        if hasattr(self.provider, "transcribe"):
            return await self._cb.call("transcribe", self.provider.transcribe, file_path)
        text = await self._cb.call("transcribe", self.provider.transcribe_audio, file_path)
        return TranscriptionResult(text=text)

    async def process(self, raw_text: str) -> str:
        if hasattr(self.provider, "process"):
            return await self._cb.call("refine", self.provider.process, raw_text)
        return await self._cb.call("refine", self.provider.refine_text, raw_text)

    async def stream_process(self, raw_text: str) -> AsyncIterator[RefineStreamEvent]:
        self._cb.check()
        try:
            stream = self.provider.stream_process(raw_text) if hasattr(self.provider, "stream_process") else None
            if stream is None:
                stream = self.provider.stream_refine_text(raw_text)
            async for event in stream:
                yield event
        except ProviderCircuitOpen:
            raise
        except Exception:
            self._cb.record_failure()
            raise
        self._cb.record_success()


# ===================================================================
# ADAPTERS — OpenAI
# ===================================================================


class OpenAIWhisperTranscriber(Transcriber):
    """OpenAI Whisper transcription adapter."""

    def __init__(self, api_key: str):
        self.client = OpenAI(api_key=api_key, max_retries=0)

    def get_capabilities(self) -> CapabilityModel:
        from bot.capabilities import CapabilityModel
        return CapabilityModel(transcription=True)

    def accepted_formats(self) -> frozenset[str]:
        return frozenset({
            "flac", "m4a", "mp3", "mp4", "mpeg", "mpga",
            "oga", "ogg", "wav", "webm",
        })

    async def transcribe(self, file_path: str) -> TranscriptionResult:
        logger.info("Transcribe %s with Whisper v1 (P1 adapter)", file_path)

        def _sync():
            from bot import constants as c

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
            _log_provider_failure("openai", "transcribe", e)
            raise TranscribeTimeout("Timeout in transcribe", c.MSG_TIMEOUT_TRANSCRIBE) from e
        except Exception as e:
            _log_provider_failure("openai", "transcribe", e)
            raise TranscribeError(f"OpenAI transcription failed: {e}", c.MSG_ERROR_TRANSCRIBE) from e

        text = result.text
        _log_text_preview("Raw text", text)
        return TranscriptionResult(
            text=text,
            language=None,  # whisper-1 returns language only in verbose_json mode
            duration_seconds=None,
        )


class OpenAITextProcessor(TextProcessor):
    """OpenAI text refinement adapter (Chat Completions + Responses API)."""

    supports_refine_streaming = True

    def __init__(
        self,
        api_key: str,
        model_name: str = "gpt-4o-mini",
        prompts: dict | None = None,
    ):
        self.client = OpenAI(api_key=api_key, max_retries=0)
        self.async_client = AsyncOpenAI(api_key=api_key, max_retries=0)

    def get_capabilities(self) -> CapabilityModel:
        from bot.capabilities import CapabilityModel
        return CapabilityModel(
            text_generation=True,
            refinement=True,
            streaming_refinement=True,
        )
        self.async_client = AsyncOpenAI(api_key=api_key, max_retries=0)
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

    async def process(self, raw_text: str) -> str:
        logger.info("Refine text with ChatCompletion (P1 adapter)")
        prompt = self.prompts["refine_template"].format(raw_text=raw_text)

        def _sync():
            from bot import constants as c

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
            _log_provider_failure("openai", "refine", e)
            raise RefineTimeout("Timeout in refine", c.MSG_TIMEOUT_REFINE) from e
        except Exception as e:
            _log_provider_failure("openai", "refine", e)
            raise RefineError(f"OpenAI refinement failed: {e}", c.MSG_ERROR_REFINE) from e

        content = resp.choices[0].message.content
        out = content.strip() if content else ""
        _log_text_preview("Refined text", out)
        return out

    async def stream_process(self, raw_text: str) -> AsyncIterator[RefineStreamEvent]:
        logger.info("Stream refine text with OpenAI Responses API (P1 adapter)")
        prompt = self.prompts["refine_template"].format(raw_text=raw_text)
        accumulated: list[str] = []
        finalized_text: str | None = None

        from bot import constants as c

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
                    raise RefineError("OpenAI streaming refinement failed", c.MSG_ERROR_REFINE)
                elif event.type == "response.completed":
                    completed = finalized_text if finalized_text is not None else "".join(accumulated)
                    _log_text_preview("Refined text", completed)
                    yield RefineStreamEvent(type="done", text=completed)
                    return
        except openai.APITimeoutError as e:
            _log_provider_failure("openai", "stream_refine", e)
            raise RefineTimeout("Timeout in refine", c.MSG_TIMEOUT_REFINE) from e
        except RefineError:
            raise
        except Exception as e:
            _log_provider_failure("openai", "stream_refine", e)
            raise RefineError(f"OpenAI streaming refinement failed: {e}", c.MSG_ERROR_REFINE) from e


class OpenAIProvider(LLMProvider, Transcriber, TextProcessor):
    """OpenAI Implementation.

    Implements both the legacy :class:`LLMProvider` interface and the
    new :class:`Transcriber` / :class:`TextProcessor` interfaces by
    delegating to dedicated adapters.
    """

    supports_refine_streaming = True

    def __init__(
        self,
        api_key: str,
        model_name: str = "gpt-4o-mini",
        prompts: dict | None = None,
    ):
        self._transcriber = OpenAIWhisperTranscriber(api_key)
        self._processor = OpenAITextProcessor(api_key, model_name, prompts)
        self.model_name = model_name
        self.prompts = prompts or {}

    def get_capabilities(self) -> CapabilityModel:
        """Combine transcriber + text processor capabilities."""
        from bot.capabilities import CapabilityModel
        t = self._transcriber.get_capabilities()
        p = self._processor.get_capabilities()
        return CapabilityModel(
            transcription=t.transcription,
            text_generation=p.text_generation,
            refinement=p.refinement,
            streaming_refinement=p.streaming_refinement,
        )

    # ---- Legacy LLMProvider interface ----

    async def transcribe_audio(self, file_path: str) -> str:
        result = await self._transcriber.transcribe(file_path)
        return result.text

    async def refine_text(self, raw_text: str) -> str:
        return await self._processor.process(raw_text)

    async def stream_refine_text(self, raw_text: str):
        async for event in self._processor.stream_process(raw_text):
            yield event

    # ---- New Transcriber interface ----

    async def transcribe(self, file_path: str) -> TranscriptionResult:
        return await self._transcriber.transcribe(file_path)

    def accepted_formats(self) -> frozenset[str]:
        """Delegate to the internal Whisper transcriber."""
        return self._transcriber.accepted_formats()

    # ---- New TextProcessor interface ----

    async def process(self, raw_text: str) -> str:
        return await self._processor.process(raw_text)

    async def stream_process(self, raw_text: str) -> AsyncIterator[RefineStreamEvent]:
        async for event in self._processor.stream_process(raw_text):
            yield event


# ===================================================================
# ADAPTERS — Gemini
# ===================================================================


class GeminiTranscriber(Transcriber):
    """Google Gemini transcription adapter."""

    def __init__(self, api_key: str, model_name: str = "gemini-2.0-flash"):
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name

    def get_capabilities(self) -> CapabilityModel:
        from bot.capabilities import CapabilityModel
        return CapabilityModel(transcription=True)

    def accepted_formats(self) -> frozenset[str]:
        return frozenset({
            "wav", "mp3", "aiff", "aac", "ogg", "flac",
        })

    async def transcribe(self, file_path: str) -> TranscriptionResult:
        logger.info("Transcribe %s with Gemini (P1 adapter)", file_path)

        from bot import constants as c

        stage_timeout = c.PROGRESS_TIMEOUTS.get("transcribe", 120)
        upload_timeout = min(30, stage_timeout)
        poll_timeout = min(10, stage_timeout)
        generate_timeout = stage_timeout
        cleanup_timeout = min(10, stage_timeout)

        async def _call(fn, timeout_seconds: int, *args, **kwargs):
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(fn, *args, **kwargs),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError as e:
                raise TranscribeTimeout("Timeout in transcribe", c.MSG_TIMEOUT_TRANSCRIBE) from e

        audio_file = None
        try:
            # Upload
            try:
                audio_file = await _call(self.client.files.upload, upload_timeout, file=file_path)
            except TranscribeTimeout:
                raise
            except Exception as e:
                _log_provider_failure("gemini", "upload_audio", e)
                raise TranscribeError(f"Google AI File Upload failed: {e}", c.MSG_ERROR_TRANSCRIBE) from e

            # Wait for processing
            while audio_file.state == "PROCESSING":
                await asyncio.sleep(1)
                try:
                    audio_file = await _call(self.client.files.get, poll_timeout, audio_file.name)
                except TranscribeTimeout:
                    raise
                except Exception as e:
                    _log_provider_failure("gemini", "check_upload_status", e)
                    raise TranscribeError(f"Failed to check file status: {e}", c.MSG_ERROR_TRANSCRIBE) from e

            if audio_file.state == "FAILED":
                raise TranscribeError("Google AI File Upload failed.", c.MSG_ERROR_TRANSCRIBE)

            # Transcribe
            prompt = "Transcribe this audio file accurately. Output only text."
            try:
                response = await _call(
                    self.client.models.generate_content,
                    generate_timeout,
                    model=self.model_name,
                    contents=[prompt, audio_file],
                )
            except TranscribeTimeout:
                raise
            except Exception as e:
                _log_provider_failure("gemini", "transcribe", e)
                raise TranscribeError(f"Google AI Transcription failed: {e}", c.MSG_ERROR_TRANSCRIBE) from e

            text = response.text
            _log_text_preview("Gemini Raw text", text)
            return TranscriptionResult(text=text)

        finally:
            if audio_file:
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(self.client.files.delete, name=audio_file.name),
                        timeout=cleanup_timeout,
                    )
                    logger.debug("Remote file %s deleted successfully", audio_file.name)
                except Exception as e:
                    logger.warning("Failed to cleanup remote file: %s", e)


class GeminiTextProcessor(TextProcessor):
    """Google Gemini text refinement adapter."""

    supports_refine_streaming = True

    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-2.0-flash",
        prompts: dict | None = None,
    ):
        self.client = genai.Client(api_key=api_key)
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
        from bot.capabilities import CapabilityModel
        return CapabilityModel(
            text_generation=True,
            refinement=True,
            streaming_refinement=True,
        )

    async def process(self, raw_text: str) -> str:
        logger.info("Refine text with Gemini (P1 adapter)")

        from bot import constants as c

        refine_timeout = c.PROGRESS_TIMEOUTS.get("refine", 90)
        full_prompt = (
            f"{self.prompts['system']}\n\n"
            f"{self.prompts['refine_template'].format(raw_text=raw_text)}"
        )

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    self.client.models.generate_content,
                    model=self.model_name,
                    contents=full_prompt,
                ),
                timeout=refine_timeout,
            )
        except asyncio.TimeoutError as e:
            _log_provider_failure("gemini", "refine", e)
            raise RefineTimeout("Timeout in refine", c.MSG_TIMEOUT_REFINE) from e
        except Exception as e:
            _log_provider_failure("gemini", "refine", e)
            raise RefineError(f"Google AI Refinement failed: {e}", c.MSG_ERROR_REFINE) from e

        out = response.text.strip()
        _log_text_preview("Gemini Refined text", out)
        return out

    async def stream_process(self, raw_text: str) -> AsyncIterator[RefineStreamEvent]:
        logger.info("Stream refine text with Gemini (P1 adapter)")

        from bot import constants as c

        refine_timeout = c.PROGRESS_TIMEOUTS.get("refine", 90)
        full_prompt = (
            f"{self.prompts['system']}\n\n"
            f"{self.prompts['refine_template'].format(raw_text=raw_text)}"
        )

        def _sync_stream():
            return self.client.models.generate_content_stream(
                model=self.model_name,
                contents=full_prompt,
            )

        accumulated: list[str] = []
        try:
            stream = await asyncio.wait_for(asyncio.to_thread(_sync_stream), timeout=refine_timeout)

            for chunk in stream:
                chunk_text = getattr(chunk, "text", None) or ""
                if not chunk_text:
                    continue
                accumulated.append(chunk_text)
                yield RefineStreamEvent(type="delta", text=chunk_text)

            completed = "".join(accumulated).strip()
            _log_text_preview("Gemini Refined text", completed)
            yield RefineStreamEvent(type="done", text=completed)
        except asyncio.TimeoutError as e:
            _log_provider_failure("gemini", "stream_refine", e)
            raise RefineTimeout("Timeout in refine", c.MSG_TIMEOUT_REFINE) from e
        except Exception as e:
            _log_provider_failure("gemini", "stream_refine", e)
            raise RefineError(f"Google AI Streaming Refinement failed: {e}", c.MSG_ERROR_REFINE) from e


class GeminiProvider(LLMProvider, Transcriber, TextProcessor):
    """Google Gemini Implementation.

    Implements both the legacy :class:`LLMProvider` interface and the
    new :class:`Transcriber` / :class:`TextProcessor` interfaces by
    delegating to dedicated adapters.
    """

    supports_refine_streaming = True

    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-2.0-flash",
        prompts: dict | None = None,
    ):
        self._transcriber = GeminiTranscriber(api_key, model_name)
        self._processor = GeminiTextProcessor(api_key, model_name, prompts)
        self.model_name = model_name
        self.prompts = prompts or {}

    def get_capabilities(self) -> CapabilityModel:
        """Combine transcriber + text processor capabilities."""
        from bot.capabilities import CapabilityModel
        t = self._transcriber.get_capabilities()
        p = self._processor.get_capabilities()
        return CapabilityModel(
            transcription=t.transcription,
            text_generation=p.text_generation,
            refinement=p.refinement,
            streaming_refinement=p.streaming_refinement,
        )

    # ---- Legacy LLMProvider interface ----

    async def transcribe_audio(self, file_path: str) -> str:
        result = await self._transcriber.transcribe(file_path)
        return result.text

    async def refine_text(self, raw_text: str) -> str:
        return await self._processor.process(raw_text)

    async def stream_refine_text(self, raw_text: str):
        async for event in self._processor.stream_process(raw_text):
            yield event

    # ---- New Transcriber interface ----

    async def transcribe(self, file_path: str) -> TranscriptionResult:
        return await self._transcriber.transcribe(file_path)

    def accepted_formats(self) -> frozenset[str]:
        """Delegate to the internal Gemini transcriber."""
        return self._transcriber.accepted_formats()

    # ---- New TextProcessor interface ----

    async def process(self, raw_text: str) -> str:
        return await self._processor.process(raw_text)

    async def stream_process(self, raw_text: str) -> AsyncIterator[RefineStreamEvent]:
        async for event in self._processor.stream_process(raw_text):
            yield event

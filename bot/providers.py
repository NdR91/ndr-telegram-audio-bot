import logging
import asyncio
from abc import ABC, abstractmethod
import os
import time
import openai
from openai import OpenAI
import google.genai as genai

from bot import constants as c
from bot.exceptions import ProviderCircuitOpen, RefineError, RefineTimeout, TranscribeError, TranscribeTimeout

logger = logging.getLogger(__name__)


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
        logger.debug(f"{label}: <none>")
        return

    text_len = len(text)
    if _allow_sensitive_logging():
        logger.debug(f"{label} ({text_len} chars): {text}")
        return

    logger.debug(f"{label} ({text_len} chars): <hidden>")

class LLMProvider(ABC):
    """Abstract Base Class for LLM Providers."""

    @abstractmethod
    async def transcribe_audio(self, file_path: str) -> str:
        """Transcribes an audio file to text."""
        pass

    @abstractmethod
    async def refine_text(self, raw_text: str) -> str:
        """Refines the text using an LLM."""
        pass


class ResilientProvider(LLMProvider):
    """Circuit-breaker wrapper around an LLM provider."""

    def __init__(self, provider: LLMProvider, provider_name: str, failure_threshold: int, cooldown_seconds: int):
        self.provider = provider
        self.provider_name = provider_name
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(1, cooldown_seconds)
        self._failure_count = 0
        self._opened_at = 0.0

    @property
    def model_name(self) -> str:
        return getattr(self.provider, "model_name", "unknown")

    def _check_circuit(self) -> None:
        if self._opened_at <= 0:
            return
        elapsed = time.monotonic() - self._opened_at
        if elapsed >= self.cooldown_seconds:
            self._opened_at = 0.0
            return
        raise ProviderCircuitOpen(
            f"Provider circuit open for {self.provider_name}",
            c.MSG_PROVIDER_TEMPORARILY_UNAVAILABLE,
        )

    def _record_success(self) -> None:
        self._failure_count = 0
        self._opened_at = 0.0

    def _record_failure(self) -> None:
        self._failure_count += 1
        if self._failure_count >= self.failure_threshold:
            self._opened_at = time.monotonic()
            logger.warning(
                "Provider circuit opened | provider=%s failure_count=%s cooldown_seconds=%s",
                self.provider_name,
                self._failure_count,
                self.cooldown_seconds,
            )

    async def _call(self, operation_name: str, operation, *args):
        self._check_circuit()
        try:
            result = await operation(*args)
        except ProviderCircuitOpen:
            raise
        except Exception:
            self._record_failure()
            raise
        self._record_success()
        return result

    async def transcribe_audio(self, file_path: str) -> str:
        return await self._call("transcribe", self.provider.transcribe_audio, file_path)

    async def refine_text(self, raw_text: str) -> str:
        return await self._call("refine", self.provider.refine_text, raw_text)

class OpenAIProvider(LLMProvider):
    """OpenAI Implementation."""

    def __init__(self, api_key: str, model_name: str = "gpt-4o-mini", prompts: dict | None = None):
        self.client = OpenAI(api_key=api_key, max_retries=0)
        self.model_name = model_name
        self.prompts = prompts or {
            'system': "Sei un esperto di trascrizione audio. Correggi errori automatici, aggiungi punteggiatura, mantieni il significato originale e restituisci SOLO il testo corretto senza commenti.",
            'refine_template': "Questo è un testo generato da una trascrizione automatica. Correggilo da eventuali errori, aggiungi la punteggiatura, riformula se ti rendi conto che la trascrizione è inaccurate, ma rimani il più aderente possibile al testo originale. Considera la presenza di eventuali esitazioni e ripetizioni, rendile adatte ad un testo scritto.\nIMPORTANTE: Restituisci SOLO il testo rielaborato. NON aggiungere commenti introduttivi, premese o saluti.\n\nTesto originale:\n{raw_text}\n\nTesto rielaborato:\n"
        }

    async def transcribe_audio(self, file_path: str) -> str:
        logger.info(f"Transcribe {file_path} with Whisper v1")
        
        def _sync_transcribe():
            from bot import constants as c

            with open(file_path, 'rb') as audio:
                client = self.client.with_options(
                    timeout=c.PROGRESS_TIMEOUTS.get("transcribe", 120),
                    max_retries=0,
                )
                return client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio,
                    temperature=0,
                )

        try:
            transcription = await asyncio.to_thread(_sync_transcribe)
        except openai.APITimeoutError as e:
            _log_provider_failure("openai", "transcribe", e)
            raise TranscribeTimeout("Timeout in transcribe", c.MSG_TIMEOUT_TRANSCRIBE) from e
        except Exception as e:
            _log_provider_failure("openai", "transcribe", e)
            raise TranscribeError(f"OpenAI transcription failed: {e}", c.MSG_ERROR_TRANSCRIBE) from e
        text = transcription.text
        _log_text_preview("Raw text", text)
        return text

    async def refine_text(self, raw_text: str) -> str:
        logger.info("Refine text with ChatCompletion")
        prompt = self.prompts['refine_template'].format(raw_text=raw_text)

        def _sync_completion():
            from bot import constants as c

            client = self.client.with_options(
                timeout=c.PROGRESS_TIMEOUTS.get("refine", 90),
                max_retries=0,
            )
            return client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": self.prompts['system']},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=4096,
                temperature=0.7,
            )

        try:
            resp = await asyncio.to_thread(_sync_completion)
        except openai.APITimeoutError as e:
            _log_provider_failure("openai", "refine", e)
            raise RefineTimeout("Timeout in refine", c.MSG_TIMEOUT_REFINE) from e
        except Exception as e:
            _log_provider_failure("openai", "refine", e)
            raise RefineError(f"OpenAI refinement failed: {e}", c.MSG_ERROR_REFINE) from e
        response_content = resp.choices[0].message.content
        out = response_content.strip() if response_content else ""
        _log_text_preview("Refined text", out)
        return out

class GeminiProvider(LLMProvider):
    """Google Gemini Implementation using new google-genai SDK."""

    def __init__(self, api_key: str, model_name: str = "gemini-2.0-flash", prompts: dict | None = None):
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name
        self.prompts = prompts or {
            'system': "Sei un esperto di trascrizione audio. Correggi errori automatici, aggiungi punteggiatura, mantieni il significato originale e restituisci SOLO il testo corretto senza commenti.",
            'refine_template': "Questo è un testo generato da una trascrizione automatica. Correggilo da eventuali errori, aggiungi la punteggiatura, riformula se ti rendi conto che la trascrizione è inaccurate, ma rimani il più aderente possibile al testo originale. Considera la presenza di eventuali esitazioni e ripetizioni, rendile adatte ad un testo scritto.\nIMPORTANTE: Restituisci SOLO il testo rielaborato. NON aggiungere commenti introduttivi, premese o saluti.\n\nTesto originale:\n{raw_text}\n\nTesto rielaborato:\n"
        }

    async def transcribe_audio(self, file_path: str) -> str:
        logger.info(f"Transcribe {file_path} with Gemini (new SDK)")

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
            # Carica il file su Google AI Studio con nuovo SDK
            try:
                audio_file = await _call(self.client.files.upload, upload_timeout, file=file_path)
            except TranscribeTimeout:
                raise
            except Exception as e:
                _log_provider_failure("gemini", "upload_audio", e)
                raise TranscribeError(f"Google AI File Upload failed: {e}", c.MSG_ERROR_TRANSCRIBE)
            
            # Attendi che il file sia processato (stato ACTIVE)
            while audio_file.state == "PROCESSING":
                await asyncio.sleep(1)
                try:
                    audio_file = await _call(self.client.files.get, poll_timeout, audio_file.name)
                except TranscribeTimeout:
                    raise
                except Exception as e:
                    _log_provider_failure("gemini", "check_upload_status", e)
                    raise TranscribeError(f"Failed to check file status: {e}", c.MSG_ERROR_TRANSCRIBE)

            if audio_file.state == "FAILED":
                raise TranscribeError("Google AI File Upload failed.", c.MSG_ERROR_TRANSCRIBE)

            # Richiedi la trascrizione con nuovo SDK
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
                raise TranscribeError(f"Google AI Transcription failed: {e}", c.MSG_ERROR_TRANSCRIBE)
            
            text = response.text
            _log_text_preview("Gemini Raw text", text)
            return text

        finally:
            # Cleanup garantito del file remoto
            if audio_file:
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(self.client.files.delete, name=audio_file.name),
                        timeout=cleanup_timeout,
                    )
                    logger.debug(f"Remote file {audio_file.name} deleted successfully")
                except Exception as e:
                    logger.warning(f"Failed to cleanup remote file: {e}")

    async def refine_text(self, raw_text: str) -> str:
        logger.info("Refine text with Gemini (new SDK)")

        from bot import constants as c
        refine_timeout = c.PROGRESS_TIMEOUTS.get("refine", 90)
        # Combina system prompt e user prompt perché Gemini usa un array di content
        full_prompt = f"{self.prompts['system']}\n\n{self.prompts['refine_template'].format(raw_text=raw_text)}"
        
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
            raise RefineError(f"Google AI Refinement failed: {e}", c.MSG_ERROR_REFINE)
        
        out = response.text.strip()
        _log_text_preview("Gemini Refined text", out)
        return out

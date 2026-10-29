"""Execute one of the two supported transcription paths."""

import asyncio
import time
import uuid
from pathlib import Path

from bot.transcription.errors import ServiceFailure, classify_provider_failure
from bot.transcription.models import ConfigRevision, ProcessingResult


class TranscriptionRunner:
    def __init__(self, pool, work_dir: Path):
        self._pool = pool
        self._work_dir = Path(work_dir)
        self._work_dir.mkdir(parents=True, exist_ok=True)

    async def run(
        self,
        snapshot: ConfigRevision,
        audio: Path,
        *,
        allow_partial_refinement: bool = True,
    ) -> ProcessingResult:
        started = time.monotonic()
        values = snapshot.values
        derivative = None
        diagnostics = []
        try:
            async with self._pool.lease(snapshot) as adapter:
                path = Path(audio)
                source_format = path.suffix.lower().lstrip(".") or "sconosciuto"
                fmt = source_format
                role = "multimodal" if values.mode == "multimodal" else "transcription"
                if fmt not in adapter.accepted_formats(values.audio_model, role):
                    diagnostics.append(f"Conversione audio: {source_format} → mp3 con FFmpeg")
                    derivative = await self._convert(path, values.limits.convert_timeout)
                    path = derivative
                    fmt = path.suffix.lower().lstrip(".")
                else:
                    diagnostics.append(f"Conversione non necessaria: {source_format} supportato")
                diagnostics.append(f"Formato inviato al servizio: {fmt}")
                if values.mode == "multimodal":
                    diagnostics.append("Invio audio al modello multimodale")
                    stage = await asyncio.wait_for(
                        adapter.audio_to_text(path, values.audio_model, values.system_prompt),
                        timeout=values.limits.transcribe_timeout,
                    )
                    self._require_text(stage.text, "multimodal")
                    stages, partial = [stage], False
                    diagnostics.append("Risposta audio ricevuta")
                elif values.mode == "two_stage":
                    diagnostics.append("Invio audio al servizio di trascrizione")
                    first = await asyncio.wait_for(
                        adapter.transcribe(path, values.audio_model),
                        timeout=values.limits.transcribe_timeout,
                    )
                    self._require_text(first.text, "transcription")
                    diagnostics.append("Trascrizione ricevuta")
                    try:
                        diagnostics.append("Invio trascrizione alla fase di revisione")
                        final = await asyncio.wait_for(
                            adapter.refine(first.text, values.text_model, values.system_prompt, values.refine_template),
                            timeout=values.limits.refine_timeout,
                        )
                        self._require_text(final.text, "refinement")
                        stages, partial = [first, final], False
                        diagnostics.append("Revisione completata")
                    except Exception as exc:
                        if not allow_partial_refinement:
                            failure = (
                                exc if isinstance(exc, ServiceFailure)
                                else classify_provider_failure(exc, "refinement")
                            )
                            if failure.fallback_eligible:
                                if failure is exc:
                                    raise
                                raise failure from exc
                        # A failed second stage leaves the valid transcription usable.
                        stages, partial = [first], True
                        diagnostics.append("Revisione non riuscita; conservata la trascrizione iniziale")
                else:
                    raise ServiceFailure("invalid_mode", "configuration", "La modalità audio non è valida.")
            total_cost = sum((item.cost for item in stages if item.cost is not None), start=0)
            if not any(item.cost is not None for item in stages):
                total_cost = None
            return ProcessingResult(
                text=stages[-1].text, partial=partial,
                models=tuple(item.model for item in stages),
                duration_ms=max(0, int((time.monotonic() - started) * 1000)),
                cost=total_cost,
                diagnostics=tuple(diagnostics),
            )
        finally:
            if derivative is not None:
                derivative.unlink(missing_ok=True)

    async def _convert(self, source: Path, timeout: int) -> Path:
        from bot.transcription.ffmpeg import convert_to_mp3

        destination = self._work_dir / f"v2-{uuid.uuid4().hex}.mp3"
        try:
            await asyncio.wait_for(convert_to_mp3(str(source), str(destination)), timeout=timeout)
            return destination
        except asyncio.CancelledError:
            destination.unlink(missing_ok=True)
            raise
        except Exception as exc:
            destination.unlink(missing_ok=True)
            raise ServiceFailure("conversion_failed", "conversion", "Non è stato possibile preparare il file audio.") from exc

    @staticmethod
    def _require_text(text: str, stage: str) -> None:
        if not text or not text.strip():
            raise ServiceFailure("empty_response", stage, "Il servizio AI non ha restituito testo.")

"""
Automatic pipeline resolver (P4).

Resolves the simplest valid pipeline from:

- request mode (full pipeline vs. transcription-only);
- user/group preferences (pipeline profile selection);
- selected pipeline profile (or default);
- provider and model capabilities;
- system policy (refinement enabled/disabled globally).

The resolver explains invalid configurations in user-facing terms and
produces an immutable :class:`ExecutionPlan` for each accepted request.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from bot.adapters import text_processor_registry, transcriber_registry
from bot.capabilities import CapabilityModel, detect_capabilities, merge_capabilities
from bot.database import DatabaseManager
from bot.exceptions import PipelineResolutionError
from bot.providers import (
    RefineError,
    RefineStreamEvent,
    ResilientTextProcessor,
    ResilientTranscriber,
    TextProcessor,
    TranscribeError,
    Transcriber,
    TranscriptionResult,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "openai-native": "gpt-4o-mini",
    "gemini": "gemini-2.0-flash",
    "gemini-native": "gemini-2.0-flash",
    "openai-compat": "gpt-4o-mini",
}

_RESILIENCE_DEFAULTS = {
    "enabled": True,
    "failure_threshold": 3,
    "cooldown_seconds": 60,
}

# ---------------------------------------------------------------------------
# Fallback wrappers — runtime fallback execution
# ---------------------------------------------------------------------------


class FallbackTranscriber(Transcriber):
    """Wrapper that tries a primary transcriber then fallbacks in order.

    Logs which model was used on success.  If all models fail, raises
    the last exception with a user-facing message.
    """

    def __init__(self, primary: Transcriber, fallbacks: list[Transcriber]):
        self._primary = primary
        self._fallbacks = fallbacks

    async def transcribe(self, file_path: str) -> TranscriptionResult:
        first_name = getattr(self._primary, "provider_name", "primary")
        try:
            result = await self._primary.transcribe(file_path)
            logger.info("Transcription succeeded | model=%s", first_name)
            return result
        except Exception as exc:
            logger.warning(
                "Transcription primary failed | model=%s error=%s",
                first_name, exc.__class__.__name__,
            )
        for i, fb in enumerate(self._fallbacks):
            fb_name = getattr(fb, "provider_name", f"fallback-{i}")
            try:
                result = await fb.transcribe(file_path)
                logger.info("Transcription succeeded | model=%s", fb_name)
                return result
            except Exception as exc:
                logger.warning(
                    "Transcription fallback %s failed | model=%s error=%s",
                    i + 1, fb_name, exc.__class__.__name__,
                )
        raise TranscribeError(
            "All transcription models failed",
            "Nessun modello di trascrizione disponibile. "
            "Tutti i modelli configurati hanno fallito. "
            "Riprova più tardi o contatta l'amministratore.",
        )

    def get_capabilities(self):
        return self._primary.get_capabilities()

    def accepted_formats(self) -> frozenset[str]:
        """Delegate to the primary transcriber."""
        return self._primary.accepted_formats()


class FallbackTextProcessor(TextProcessor):
    """Wrapper that tries a primary text processor then fallbacks in order.

    Logs which model was used on success.  If all models fail, raises
    the last exception with a user-facing message.
    """

    def __init__(self, primary: TextProcessor, fallbacks: list[TextProcessor]):
        self._primary = primary
        self._fallbacks = fallbacks

    async def process(self, raw_text: str) -> str:
        first_name = getattr(self._primary, "provider_name", "primary")
        try:
            result = await self._primary.process(raw_text)
            logger.info("Refinement succeeded | model=%s", first_name)
            return result
        except Exception as exc:
            logger.warning(
                "Refinement primary failed | model=%s error=%s",
                first_name, exc.__class__.__name__,
            )
        for i, fb in enumerate(self._fallbacks):
            fb_name = getattr(fb, "provider_name", f"fallback-{i}")
            try:
                result = await fb.process(raw_text)
                logger.info("Refinement succeeded | model=%s", fb_name)
                return result
            except Exception as exc:
                logger.warning(
                    "Refinement fallback %s failed | model=%s error=%s",
                    i + 1, fb_name, exc.__class__.__name__,
                )
        raise RefineError(
            "All refinement models failed",
            "Nessun modello di refinement disponibile. "
            "Tutti i modelli configurati hanno fallito. "
            "Riprova più tardi o contatta l'amministratore.",
        )

    async def stream_process(self, raw_text: str):
        first_name = getattr(self._primary, "provider_name", "primary")
        try:
            async for event in self._primary.stream_process(raw_text):
                yield event
            logger.info("Refinement streaming succeeded | model=%s", first_name)
            return
        except Exception as exc:
            logger.warning(
                "Refinement streaming primary failed | model=%s error=%s",
                first_name, exc.__class__.__name__,
            )
        for i, fb in enumerate(self._fallbacks):
            fb_name = getattr(fb, "provider_name", f"fallback-{i}")
            try:
                async for event in fb.stream_process(raw_text):
                    yield event
                logger.info(
                    "Refinement streaming succeeded | model=%s", fb_name,
                )
                return
            except Exception as exc:
                logger.warning(
                    "Refinement streaming fallback %s failed | model=%s error=%s",
                    i + 1, fb_name, exc.__class__.__name__,
                )
        raise RefineError(
            "All refinement streaming models failed",
            "Nessun modello di refinement disponibile. "
            "Tutti i modelli configurati hanno fallito. "
            "Riprova più tardi o contatta l'amministratore.",
        )

    @property
    def supports_refine_streaming(self) -> bool:
        return getattr(self._primary, "supports_refine_streaming", False) or any(
            getattr(fb, "supports_refine_streaming", False) for fb in self._fallbacks
        )

    def get_capabilities(self):
        return self._primary.get_capabilities()


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class RequestMode(str, Enum):
    """Processing mode for an incoming audio request."""

    FULL = "full"
    """Transcribe and refine (default)."""

    TRANSCRIPTION_ONLY = "transcription_only"
    """Transcribe only — skip refinement."""


@dataclass(frozen=True)
class ModelRef:
    """Reference to a resolved model entry with its fallback chain.

    Parameters
    ----------
    provider_id:
        The provider connection ID that owns this model.
    adapter_type:
        The adapter type of the provider (e.g. ``"openai-native"``).
    model_entry_id:
        The ``provider_models.id`` (may be ``None`` for legacy paths).
    model_id:
        The actual model identifier (e.g. ``"gpt-4o"``, ``"whisper-1"``).
    capabilities:
        The effective capabilities of this model.
    fallback_model_ids:
        Ordered list of model identifiers to try if the primary fails.
    fallback_entry_ids:
        Ordered list of ``provider_models.id`` for the fallbacks.
    """

    provider_id: int
    adapter_type: str
    model_entry_id: int | None
    model_id: str
    capabilities: CapabilityModel
    fallback_model_ids: List[str] = field(default_factory=list)
    fallback_entry_ids: List[int] = field(default_factory=list)


@dataclass(frozen=True)
class PipelineRequest:
    """Input to the pipeline resolver describing the incoming request.

    Parameters
    ----------
    mode:
        Processing mode.  Defaults to ``FULL``.
    user_id:
        Telegram user ID who sent the audio.
    chat_id:
        Telegram chat ID where the audio was sent.
    """

    mode: RequestMode = RequestMode.FULL
    user_id: int | None = None
    chat_id: int | None = None


@dataclass(frozen=True)
class ExecutionPlan:
    """Immutable resolved execution plan for a single request.

    Parameters
    ----------
    transcriber:
        Resolved :class:`~bot.providers.Transcriber` instance to use.
    text_processor:
        Resolved :class:`~bot.providers.TextProcessor` instance, or
        ``None`` when refinement is disabled / transcription-only mode.
    provider_name:
        Human-readable provider name for display in responses.
    model_name:
        Model name for display in responses.
    transcript_model:
        Resolved :class:`ModelRef` for the transcription stage.
    refine_model:
        Resolved :class:`ModelRef` for the refinement stage, or ``None``.
    resolution_log:
        Ordered list of human-readable steps the resolver took to
        reach this plan (for debugging and auditing).
    """

    transcriber: Transcriber
    text_processor: TextProcessor | None
    provider_name: str
    model_name: str
    transcript_model: ModelRef | None = None
    refine_model: ModelRef | None = None
    resolution_log: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _adapters_support(
    adapter_type: str,
    needs_transcription: bool,
    needs_refinement: bool,
) -> bool:
    """Return ``True`` when the given adapter type can satisfy all
    required capabilities based on its static defaults.

    Uses :func:`~bot.capabilities.detect_capabilities` so unknown
    adapter types return all-``False``.
    """
    caps = detect_capabilities(adapter_type)
    if needs_transcription and not caps.transcription:
        return False
    if needs_refinement and not caps.refinement:
        return False
    return True


def _default_model_for(adapter_type: str) -> str:
    """Return the default model name for *adapter_type*."""
    return _DEFAULT_MODELS.get(adapter_type, "")


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


class PipelineResolver:
    """Automatic pipeline resolver.

    Resolves the best available provider(s) for each incoming request
    based on the current database state.

    Parameters
    ----------
    db_manager:
        Initialised :class:`~bot.database.DatabaseManager`.
    """

    def __init__(self, db_manager: DatabaseManager):
        self._db = db_manager

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve_from_profile(
        self,
        profile_id: int,
        request: PipelineRequest | None = None,
        *,
        refinement_globally_disabled: bool = False,
    ) -> ExecutionPlan:
        """Resolve a pipeline from a saved pipeline profile.

        Loads the profile from the database, retrieves the referenced
        provider connections and models, and builds an :class:`ExecutionPlan`.

        Supports both ``two_stage`` and ``single_pass`` pipeline modes.

        Parameters
        ----------
        profile_id:
            ID of the pipeline profile in the database.
        request:
            Optional :class:`PipelineRequest` (currently used only
            for mode and logging).
        refinement_globally_disabled:
            When ``True``, refinement is skipped even for full-mode
            profiles.

        Returns
        -------
        ExecutionPlan
            An immutable plan with resolved provider instances.

        Raises
        ------
        PipelineResolutionError
            When the profile or its referenced providers cannot be
            loaded, or when no valid model configuration exists.
        """
        log: list[str] = []
        req_mode = (request or PipelineRequest()).mode

        # 1. Load the pipeline profile.
        profile = self._db.get_pipeline_profile(profile_id)
        if profile is None:
            raise PipelineResolutionError(
                "Profilo pipeline non trovato",
                "Il profilo pipeline selezionato non esiste. "
                "Contatta l'amministratore.",
            )
        log.append(f"Loaded pipeline profile '{profile['name']}' (id={profile_id})")

        # 2. Determine pipeline mode and refinement needs.
        pipeline_mode = profile.get("mode", "two_stage")
        needs_refinement = (
            req_mode == RequestMode.FULL and not refinement_globally_disabled
        )
        log.append(
            f"Pipeline mode: {pipeline_mode}, "
            f"refinement={'enabled' if needs_refinement else 'disabled'}"
        )

        # 3. Resolve based on pipeline mode.
        if pipeline_mode == "single_pass":
            return self._resolve_single_pass(profile, log)
        else:
            return self._resolve_two_stage(profile, needs_refinement, log)

    def _resolve_single_pass(
        self,
        profile: Dict[str, Any],
        log: list[str],
    ) -> ExecutionPlan:
        """Resolve a single-pass pipeline: one model that does both
        transcription and refinement in a single API call.

        Uses the transcription_provider and finds a model with
        ``single_pass_audio_to_text`` capability.
        """
        # 1. Check if profile has explicit stages.
        stages = profile.get("stages", [])
        single_stages = [s for s in stages if s["stage_type"] == "single_pass"]

        if single_stages:
            # Use the explicit stage configuration.
            stage = single_stages[0]
            primary_id = stage.get("primary_model_id")
            fallbacks = stage.get("fallbacks", [])

            model_entry = self._db.get_provider_model(primary_id) if primary_id else None
            if model_entry is None or not model_entry.get("enabled"):
                raise PipelineResolutionError(
                    "Modello single-pass non disponibile",
                    "Il modello configurato per la pipeline single-pass "
                    "non esiste o è disabilitato.",
                )
            provider = self._db.get_provider(model_entry["provider_id"])
            if provider is None or not provider.get("enabled"):
                raise PipelineResolutionError(
                    "Provider non disponibile",
                    "Il provider del modello single-pass configurato "
                    "non esiste o è disabilitato.",
                )

            caps = CapabilityModel.from_dict(model_entry.get("capabilities"))
            if not caps.single_pass_audio_to_text:
                raise PipelineResolutionError(
                    "Modello senza capacità single-pass",
                    f"Il modello '{model_entry['model_id']}' non supporta "
                    f"la modalità single-pass (trascrizione + refinement "
                    f"in unico passaggio).",
                )

            # Build fallback chain
            fallback_ids = []
            fallback_entry_ids = []
            for fb in fallbacks:
                fb_model = self._db.get_provider_model(fb["model_id"])
                if fb_model and fb_model.get("enabled"):
                    fallback_ids.append(fb_model["model_id"])
                    fallback_entry_ids.append(fb["model_id"])

            model_ref = ModelRef(
                provider_id=provider["id"],
                adapter_type=provider["adapter_type"],
                model_entry_id=model_entry["id"],
                model_id=model_entry["model_id"],
                capabilities=caps,
                fallback_model_ids=fallback_ids,
                fallback_entry_ids=fallback_entry_ids,
            )

            log.append(
                f"Single-pass model: '{model_entry['model_id']}' "
                f"from '{provider['name']}' "
                f"({len(fallback_ids)} fallback(s))"
            )

            return self._build_plan_from_model_ref(
                model_ref,
                provider,
                model_ref,
                provider,
                needs_refinement=True,
                log=log,
            )

        # 2. Fallback to legacy provider-level resolution.
        tx_id = profile.get("transcription_provider_id")
        if tx_id is None:
            raise PipelineResolutionError(
                "Nessun provider configurato",
                "Il profilo pipeline non ha un provider configurato.",
            )
        provider = self._db.get_provider(tx_id)
        if provider is None or not provider.get("enabled"):
            raise PipelineResolutionError(
                "Provider non disponibile",
                "Il provider referenziato non esiste o è disabilitato.",
            )

        # Check if the provider adapter supports single-pass.
        adapter = provider.get("adapter_type", "")
        caps = detect_capabilities(adapter, provider.get("model_name", ""))
        overrides = provider.get("capabilities")
        effective = merge_capabilities(caps, overrides)

        if not effective.single_pass_audio_to_text:
            log.append(
                f"Provider '{provider['name']}' does not advertise "
                f"single-pass capability; falling back to two-stage"
            )
            return self._resolve_two_stage(profile, True, log)

        log.append(
            f"Single-pass: using provider '{provider['name']}' "
            f"(adapter: {adapter})"
        )
        return self._build_plan(provider, needs_refinement=True, log=log)

    def _resolve_two_stage(
        self,
        profile: Dict[str, Any],
        needs_refinement: bool,
        log: list[str],
    ) -> ExecutionPlan:
        """Resolve a two-stage pipeline: separate transcription and
        refinement (optional) stages.

        Uses explicit pipeline stages when available, otherwise falls
        back to provider-level references.
        """
        stages = profile.get("stages", [])

        # --- Transcription ---
        tx_stages = [s for s in stages if s["stage_type"] == "transcription"]
        if tx_stages:
            tx_model_ref = self._resolve_stage_model(tx_stages[0], "transcription", log)
        else:
            # Fallback: use transcription_provider_id
            tx_model_ref = self._resolve_provider_model(
                profile, "transcription_provider_id", "transcription", log
            )

        # --- Refinement ---
        ref_model_ref: ModelRef | None = None
        if needs_refinement:
            ref_stages = [s for s in stages if s["stage_type"] == "refinement"]
            if ref_stages:
                ref_model_ref = self._resolve_stage_model(
                    ref_stages[0], "refinement", log
                )
            else:
                # Fallback: use text_provider_id
                ref_model_ref = self._resolve_provider_model(
                    profile, "text_provider_id", "refinement", log
                )

        if not needs_refinement:
            log.append("Transcription only — refinement disabled")

        # --- Build plan ---
        if tx_model_ref is None:
            # Check if transcription provider exists but is disabled.
            tx_provider_id = profile.get("transcription_provider_id")
            if tx_provider_id is not None:
                disabled_provider = self._db.get_provider(tx_provider_id)
                if disabled_provider is not None and not disabled_provider.get("enabled"):
                    raise PipelineResolutionError(
                        "Provider disabilitato",
                        f"Il provider '{disabled_provider['name']}' è disabilitato. "
                        f"Abilitalo per usarlo nella pipeline.",
                    )
            raise PipelineResolutionError(
                "Nessun modello di trascrizione",
                "Non è stato possibile risolvere un modello per la "
                "trascrizione audio.",
            )

        tx_provider = self._db.get_provider(tx_model_ref.provider_id)
        ref_provider = tx_provider
        if ref_model_ref is not None:
            ref_provider = self._db.get_provider(ref_model_ref.provider_id)

        if tx_provider is None:
            raise PipelineResolutionError(
                "Provider non trovato",
                "Il provider del modello di trascrizione non esiste.",
            )

        # --- Validate transcription capability ---
        if not tx_model_ref.capabilities.transcription:
            raise PipelineResolutionError(
                "Modello senza capacità di trascrizione",
                f"Il modello '{tx_model_ref.model_id}' non supporta "
                f"la trascrizione audio. Scegli un modello con "
                f"questa capacità.",
            )

        # --- Validate refinement provider ---
        if needs_refinement:
            if ref_model_ref is None:
                # Check if a text provider is configured but disabled.
                ref_provider_id = profile.get("text_provider_id")
                if ref_provider_id is not None:
                    disabled_ref = self._db.get_provider(ref_provider_id)
                    if disabled_ref is not None and not disabled_ref.get("enabled"):
                        raise PipelineResolutionError(
                            "Provider di refinement disabilitato",
                            f"Il provider '{disabled_ref['name']}' è disabilitato. "
                            f"Abilitalo per usarlo nella pipeline.",
                        )
                raise PipelineResolutionError(
                    "Nessun modello di refinement",
                    "La pipeline richiede il refinement ma non è configurato "
                    "un provider o un modello per questa fase.",
                )
            # Validate refinement capability
            if not ref_model_ref.capabilities.refinement:
                raise PipelineResolutionError(
                    "Modello senza capacità di refinement",
                    f"Il modello '{ref_model_ref.model_id}' non supporta "
                    f"il refinement. Scegli un modello con questa capacità.",
                )

        # Determine display info
        if ref_model_ref is not None and ref_model_ref.provider_id != tx_model_ref.provider_id:
            provider_name = f"{tx_provider['name']} + {ref_provider['name']}"
            model_name = f"{tx_model_ref.model_id} / {ref_model_ref.model_id}"
        else:
            provider_name = tx_provider["name"]
            model_name = tx_model_ref.model_id
            if ref_model_ref is not None:
                model_name = f"{tx_model_ref.model_id} + {ref_model_ref.model_id}"

        # Create adapter instances
        transcriber = self._create_fallback_chain_tx(tx_model_ref, tx_provider)

        text_processor: TextProcessor | None = None
        if ref_model_ref is not None and needs_refinement:
            text_processor = self._create_fallback_chain_tp(
                ref_model_ref, ref_provider,
            )

        return ExecutionPlan(
            transcriber=transcriber,
            text_processor=text_processor,
            provider_name=provider_name,
            model_name=model_name,
            transcript_model=tx_model_ref,
            refine_model=ref_model_ref,
            resolution_log=log,
        )

    def _resolve_stage_model(
        self,
        stage: Dict[str, Any],
        stage_type: str,
        log: list[str],
    ) -> ModelRef | None:
        """Resolve a :class:`ModelRef` from a pipeline stage entry.

        Returns ``None`` when the stage has no primary model.
        """
        primary_id = stage.get("primary_model_id")
        if primary_id is None:
            return None

        model_entry = self._db.get_provider_model(primary_id)
        if model_entry is None or not model_entry.get("enabled"):
            log.append(
                f"Stage '{stage_type}': primary model (id={primary_id}) "
                f"not found or disabled — skipping"
            )
            return None

        provider = self._db.get_provider(model_entry["provider_id"])
        if provider is None or not provider.get("enabled"):
            log.append(
                f"Stage '{stage_type}': provider for model "
                f"'{model_entry['model_id']}' not found or disabled"
            )
            return None

        caps = CapabilityModel.from_dict(model_entry.get("capabilities"))

        # Build fallback chain
        fallbacks = stage.get("fallbacks", [])
        fallback_ids: list[str] = []
        fallback_entry_ids: list[int] = []
        for fb in fallbacks:
            fb_model = self._db.get_provider_model(fb["model_id"])
            if fb_model and fb_model.get("enabled"):
                fb_provider = self._db.get_provider(fb_model["provider_id"])
                if fb_provider and fb_provider.get("enabled"):
                    fallback_ids.append(fb_model["model_id"])
                    fallback_entry_ids.append(fb["model_id"])

        log.append(
            f"Stage '{stage_type}': model '{model_entry['model_id']}' "
            f"from '{provider['name']}' "
            f"({len(fallback_ids)} fallback(s))"
        )

        return ModelRef(
            provider_id=provider["id"],
            adapter_type=provider["adapter_type"],
            model_entry_id=model_entry["id"],
            model_id=model_entry["model_id"],
            capabilities=caps,
            fallback_model_ids=fallback_ids,
            fallback_entry_ids=fallback_entry_ids,
        )

    def _resolve_provider_model(
        self,
        profile: Dict[str, Any],
        provider_id_key: str,
        stage_type: str,
        log: list[str],
    ) -> ModelRef | None:
        """Fallback resolution using provider-level references (legacy).

        Returns ``None`` when no provider reference exists.
        """
        pid = profile.get(provider_id_key)
        if pid is None:
            return None

        provider = self._db.get_provider(pid)
        if provider is None or not provider.get("enabled"):
            log.append(
                f"Provider (id={pid}) for '{stage_type}' not found or disabled"
            )
            return None

        # Try to find a registered model for this provider.
        models = self._db.list_provider_models(pid, only_enabled=True)
        if models:
            # Use the first enabled model.
            m = models[0]
            caps = CapabilityModel.from_dict(m.get("capabilities"))
            model_ref = ModelRef(
                provider_id=pid,
                adapter_type=provider["adapter_type"],
                model_entry_id=m["id"],
                model_id=m["model_id"],
                capabilities=caps,
            )
            log.append(
                f"Stage '{stage_type}': using model '{m['model_id']}' "
                f"from provider '{provider['name']}'"
            )
            return model_ref

        # No registered models — use provider-level capabilities.
        model_name = provider.get("model_name") or _default_model_for(
            provider.get("adapter_type", "")
        )
        caps = detect_capabilities(
            provider.get("adapter_type", ""),
            model_name,
        )
        overrides = provider.get("capabilities")
        effective = merge_capabilities(caps, overrides)

        log.append(
            f"Stage '{stage_type}': using provider-level default "
            f"'{provider['name']}' (model: {model_name})"
        )

        return ModelRef(
            provider_id=pid,
            adapter_type=provider["adapter_type"],
            model_entry_id=None,
            model_id=model_name,
            capabilities=effective,
        )

    def _build_plan_from_model_ref(
        self,
        tx_ref: ModelRef,
        tx_provider: Dict[str, Any],
        ref_ref: ModelRef | None,
        ref_provider: Dict[str, Any] | None,
        needs_refinement: bool,
        log: list[str],
    ) -> ExecutionPlan:
        """Build an execution plan from resolved ModelRef instances."""
        transcriber = self._create_fallback_chain_tx(tx_ref, tx_provider)

        text_processor: TextProcessor | None = None
        if needs_refinement and ref_ref is not None and ref_provider is not None:
            text_processor = self._create_fallback_chain_tp(
                ref_ref, ref_provider,
            )

        if ref_ref is not None and tx_ref.provider_id != ref_ref.provider_id:
            provider_name = f"{tx_provider['name']} + {ref_provider['name']}"
            model_name = f"{tx_ref.model_id} / {ref_ref.model_id}"
        else:
            provider_name = tx_provider["name"]
            model_name = tx_ref.model_id
            if ref_ref is not None:
                model_name = f"{tx_ref.model_id} + {ref_ref.model_id}"

        return ExecutionPlan(
            transcriber=transcriber,
            text_processor=text_processor,
            provider_name=provider_name,
            model_name=model_name,
            transcript_model=tx_ref,
            refine_model=ref_ref,
            resolution_log=log,
        )

    def resolve(
        self,
        request: PipelineRequest | None = None,
        *,
        refinement_globally_disabled: bool = False,
    ) -> ExecutionPlan:
        """Resolve the simplest valid pipeline and return an immutable
        execution plan.

        Parameters
        ----------
        request:
            Optional :class:`PipelineRequest` describing the incoming
            request.  When ``None``, defaults to ``FULL`` mode with no
            user/group context.
        refinement_globally_disabled:
            When ``True``, refinement is skipped even in ``FULL`` mode.
            This mirrors a global system policy toggle.

        Returns
        -------
        ExecutionPlan
            An immutable plan with resolved provider instances.

        Raises
        ------
        PipelineResolutionError
            When no valid pipeline can be resolved (e.g. no provider
            supports transcription).
        """
        log: list[str] = []
        mode = (request or PipelineRequest()).mode

        # 0. Prefer an active pipeline profile when one exists.
        #    Pipeline profiles carry explicit model stages (e.g. whisper-1
        #    for transcription, a separate model for refinement) which are
        #    lost by the simple provider-level path below.
        active_id = self._db.get_setup_state("active_pipeline_profile")
        if active_id:
            try:
                profile_id = int(active_id)
            except (ValueError, TypeError):
                pass
            else:
                log.append(
                    f"Found active pipeline profile (id={profile_id}) — "
                    f"delegating to resolve_from_profile"
                )
                return self.resolve_from_profile(
                    profile_id,
                    request,
                    refinement_globally_disabled=refinement_globally_disabled,
                )

        # 1. Load enabled providers from the database.
        providers = self._db.list_providers()
        enabled = [p for p in providers if p.get("enabled")]
        log.append(f"Found {len(enabled)} enabled provider(s) in DB")

        if not enabled:
            raise PipelineResolutionError(
                "Nessun provider configurato",
                "Nessun provider AI è stato configurato. "
                "Contatta l'amministratore.",
            )

        # 2. Determine if refinement is needed.
        needs_refinement = (
            mode == RequestMode.FULL and not refinement_globally_disabled
        )
        log.append(
            f"Request mode: {mode.value}, "
            f"refinement={'enabled' if needs_refinement else 'disabled'}"
        )

        # 3. Build capability profiles for each provider.
        profiles: list[tuple[dict[str, Any], CapabilityModel]] = []
        for p in enabled:
            detected = detect_capabilities(
                p.get("adapter_type", ""),
                p.get("model_name", ""),
            )
            overrides = p.get("capabilities")
            effective = merge_capabilities(detected, overrides)
            profiles.append((p, effective))

        # 4. Try to find a single provider that can do everything.
        single = self._find_single_provider(profiles, needs_refinement)
        if single is not None:
            provider, caps = single
            log.append(
                f"Selected single provider '{provider['name']}' "
                f"(adapter: {provider['adapter_type']}) "
                f"for all pipeline stages"
            )
            return self._build_plan(provider, needs_refinement, log)

        # 5. Could not find a single provider. Try separate providers.
        if needs_refinement:
            separate = self._find_separate_providers(profiles)
            if separate is not None:
                tx_provider, ref_provider = separate
                log.append(
                    f"Using separate providers: "
                    f"transcription={tx_provider['name']}, "
                    f"refinement={ref_provider['name']}"
                )
                return self._build_plan_with_separate(
                    tx_provider, ref_provider, log
                )

        # 6. Nothing valid — raise with a clear message.
        has_transcription = any(c.transcription for _, c in profiles)
        has_refinement = any(c.refinement for _, c in profiles)

        if not has_transcription:
            raise PipelineResolutionError(
                "Nessun provider disponibile per la trascrizione",
                "Nessun provider configurato supporta la trascrizione "
                "audio. Aggiungi un provider con capacità di "
                "trascrizione (es. OpenAI, Gemini).",
            )
        if needs_refinement and not has_refinement:
            raise PipelineResolutionError(
                "Nessun provider disponibile per il refinement",
                "Nessun provider configurato supporta il refinement "
                "del testo. Il refinement è richiesto ma nessun "
                "provider lo supporta.",
            )

        raise PipelineResolutionError(
            "Configurazione pipeline non valida",
            "La configurazione dei provider non consente di creare "
            "una pipeline valida. Verifica le capacità dei provider.",
        )

    # ------------------------------------------------------------------
    # Internal resolution helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_single_provider(
        profiles: list[tuple[dict[str, Any], CapabilityModel]],
        needs_refinement: bool,
    ) -> tuple[dict[str, Any], CapabilityModel] | None:
        """Return the first provider that satisfies all required
        capabilities, or ``None``.

        Prefers providers with both transcription + refinement when
        refinement is needed.  Otherwise returns the first provider
        with at least transcription.
        """
        for provider, caps in profiles:
            if caps.transcription:
                if not needs_refinement or caps.refinement:
                    return provider, caps
        return None

    @staticmethod
    def _find_separate_providers(
        profiles: list[tuple[dict[str, Any], CapabilityModel]],
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """Return ``(transcription_provider, refinement_provider)`` when
        two different providers can satisfy each stage, or ``None``.
        """
        tx_provider: dict[str, Any] | None = None
        ref_provider: dict[str, Any] | None = None

        for provider, caps in profiles:
            if caps.transcription and tx_provider is None:
                tx_provider = provider
            if caps.refinement and ref_provider is None:
                ref_provider = provider

        if tx_provider is not None and ref_provider is not None:
            if tx_provider["id"] != ref_provider["id"]:
                return tx_provider, ref_provider

        return None

    # ------------------------------------------------------------------
    # Instance creation
    # ------------------------------------------------------------------

    def _build_plan(
        self,
        provider: dict[str, Any],
        needs_refinement: bool,
        log: list[str],
    ) -> ExecutionPlan:
        """Build an execution plan using a single provider for all stages."""
        adapter_type = provider["adapter_type"]
        credentials = provider.get("credentials") or ""
        endpoint = provider.get("endpoint") or ""
        model_name = provider.get("model_name") or _default_model_for(adapter_type)

        caps = detect_capabilities(adapter_type, model_name)
        overrides = provider.get("capabilities")
        effective = merge_capabilities(caps, overrides)

        model_ref = ModelRef(
            provider_id=provider["id"],
            adapter_type=adapter_type,
            model_entry_id=None,
            model_id=model_name,
            capabilities=effective,
        )

        transcriber = self._create_transcriber(
            adapter_type,
            credentials,
            endpoint,
            model_name,
        )

        text_processor: TextProcessor | None = None
        if needs_refinement:
            text_processor = self._create_text_processor(
                adapter_type,
                credentials,
                endpoint,
                model_name,
            )

        return ExecutionPlan(
            transcriber=transcriber,
            text_processor=text_processor,
            provider_name=provider["name"],
            model_name=model_name,
            transcript_model=model_ref,
            refine_model=model_ref if needs_refinement else None,
            resolution_log=log,
        )

    def _build_plan_with_separate(
        self,
        tx_provider: dict[str, Any],
        ref_provider: dict[str, Any],
        log: list[str],
    ) -> ExecutionPlan:
        """Build an execution plan using separate providers for
        transcription and refinement."""
        tx_type = tx_provider["adapter_type"]
        tx_creds = tx_provider.get("credentials") or ""
        tx_endpoint = tx_provider.get("endpoint") or ""
        tx_model = tx_provider.get("model_name") or _default_model_for(tx_type)

        ref_type = ref_provider["adapter_type"]
        ref_creds = ref_provider.get("credentials") or ""
        ref_endpoint = ref_provider.get("endpoint") or ""
        ref_model = ref_provider.get("model_name") or _default_model_for(ref_type)

        tx_caps = detect_capabilities(tx_type, tx_model)
        tx_overrides = tx_provider.get("capabilities")
        tx_effective = merge_capabilities(tx_caps, tx_overrides)

        ref_caps = detect_capabilities(ref_type, ref_model)
        ref_overrides = ref_provider.get("capabilities")
        ref_effective = merge_capabilities(ref_caps, ref_overrides)

        tx_model_ref = ModelRef(
            provider_id=tx_provider["id"],
            adapter_type=tx_type,
            model_entry_id=None,
            model_id=tx_model,
            capabilities=tx_effective,
        )
        ref_model_ref = ModelRef(
            provider_id=ref_provider["id"],
            adapter_type=ref_type,
            model_entry_id=None,
            model_id=ref_model,
            capabilities=ref_effective,
        )

        transcriber = self._create_transcriber(tx_type, tx_creds, tx_endpoint, tx_model)
        text_processor = self._create_text_processor(
            ref_type, ref_creds, ref_endpoint, ref_model
        )

        return ExecutionPlan(
            transcriber=transcriber,
            text_processor=text_processor,
            provider_name=f"{tx_provider['name']} + {ref_provider['name']}",
            model_name=f"{tx_model} / {ref_model}",
            transcript_model=tx_model_ref,
            refine_model=ref_model_ref,
            resolution_log=log,
        )

    def _create_transcriber(
        self,
        adapter_type: str,
        credentials: str,
        endpoint: str,
        model_name: str,
    ) -> Transcriber:
        """Create a :class:`~bot.providers.Transcriber` instance for
        *adapter_type* with the given parameters, wrapped in a circuit
        breaker by default."""
        if not transcriber_registry.has_type(adapter_type):
            raise PipelineResolutionError(
                f"Adapter sconosciuto: {adapter_type}",
                f"Il provider configurato con adapter '{adapter_type}' "
                f"non è supportato.",
            )

        inner = transcriber_registry.create(
            adapter_type,
            api_key=credentials,
            endpoint=endpoint,
            model_name=model_name,
        )

        return ResilientTranscriber(
            inner,
            provider_name=adapter_type,
            failure_threshold=_RESILIENCE_DEFAULTS["failure_threshold"],
            cooldown_seconds=_RESILIENCE_DEFAULTS["cooldown_seconds"],
        )

    def _create_text_processor(
        self,
        adapter_type: str,
        credentials: str,
        endpoint: str,
        model_name: str,
    ) -> TextProcessor:
        """Create a :class:`~bot.providers.TextProcessor` instance for
        *adapter_type* with the given parameters, wrapped in a circuit
        breaker by default."""
        if not text_processor_registry.has_type(adapter_type):
            raise PipelineResolutionError(
                f"Adapter sconosciuto: {adapter_type}",
                f"Il provider configurato con adapter '{adapter_type}' "
                f"non supporta l'elaborazione testo.",
            )

        inner = text_processor_registry.create(
            adapter_type,
            api_key=credentials,
            endpoint=endpoint,
            model_name=model_name,
        )

        return ResilientTextProcessor(
            inner,
            provider_name=adapter_type,
            failure_threshold=_RESILIENCE_DEFAULTS["failure_threshold"],
            cooldown_seconds=_RESILIENCE_DEFAULTS["cooldown_seconds"],
        )

    def _create_fallback_chain_tx(
        self,
        primary_ref: ModelRef,
        provider: Dict[str, Any],
    ) -> Transcriber:
        """Create a :class:`Transcriber` with runtime fallback support.

        Returns a :class:`FallbackTranscriber` when the ref has fallbacks,
        otherwise a plain :class:`ResilientTranscriber`.
        """
        primary = self._create_transcriber(
            primary_ref.adapter_type,
            provider.get("credentials") or "",
            provider.get("endpoint") or "",
            primary_ref.model_id,
        )
        if not primary_ref.fallback_entry_ids:
            return primary

        fallback_list: list[Transcriber] = []
        for fb_entry_id in primary_ref.fallback_entry_ids:
            fb_entry = self._db.get_provider_model(fb_entry_id)
            if fb_entry is None or not fb_entry.get("enabled"):
                continue
            fb_provider = self._db.get_provider(fb_entry["provider_id"])
            if fb_provider is None or not fb_provider.get("enabled"):
                continue
            fb_instance = self._create_transcriber(
                fb_provider.get("adapter_type", primary_ref.adapter_type),
                fb_provider.get("credentials") or "",
                fb_provider.get("endpoint") or "",
                fb_entry["model_id"],
            )
            fallback_list.append(fb_instance)

        if not fallback_list:
            return primary

        return FallbackTranscriber(primary, fallback_list)

    def _create_fallback_chain_tp(
        self,
        primary_ref: ModelRef,
        provider: Dict[str, Any],
    ) -> TextProcessor:
        """Create a :class:`TextProcessor` with runtime fallback support.

        Returns a :class:`FallbackTextProcessor` when the ref has fallbacks,
        otherwise a plain :class:`ResilientTextProcessor`.
        """
        primary = self._create_text_processor(
            primary_ref.adapter_type,
            provider.get("credentials") or "",
            provider.get("endpoint") or "",
            primary_ref.model_id,
        )
        if not primary_ref.fallback_entry_ids:
            return primary

        fallback_list: list[TextProcessor] = []
        for fb_entry_id in primary_ref.fallback_entry_ids:
            fb_entry = self._db.get_provider_model(fb_entry_id)
            if fb_entry is None or not fb_entry.get("enabled"):
                continue
            fb_provider = self._db.get_provider(fb_entry["provider_id"])
            if fb_provider is None or not fb_provider.get("enabled"):
                continue
            fb_instance = self._create_text_processor(
                fb_provider.get("adapter_type", primary_ref.adapter_type),
                fb_provider.get("credentials") or "",
                fb_provider.get("endpoint") or "",
                fb_entry["model_id"],
            )
            fallback_list.append(fb_instance)

        if not fallback_list:
            return primary

        return FallbackTextProcessor(primary, fallback_list)

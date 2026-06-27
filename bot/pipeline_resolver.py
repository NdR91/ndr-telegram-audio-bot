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
    ResilientTextProcessor,
    ResilientTranscriber,
    TextProcessor,
    Transcriber,
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
# Public types
# ---------------------------------------------------------------------------


class RequestMode(str, Enum):
    """Processing mode for an incoming audio request."""

    FULL = "full"
    """Transcribe and refine (default)."""

    TRANSCRIPTION_ONLY = "transcription_only"
    """Transcribe only — skip refinement."""


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
    resolution_log:
        Ordered list of human-readable steps the resolver took to
        reach this plan (for debugging and auditing).
    """

    transcriber: Transcriber
    text_processor: TextProcessor | None
    provider_name: str
    model_name: str
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
        provider connections, and builds an :class:`ExecutionPlan`.

        When the profile uses the same provider for transcription and
        text processing, that provider is used for all stages
        (same-provider default).

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
            loaded, or when no provider supports transcription.
        """
        log: list[str] = []
        mode = (request or PipelineRequest()).mode

        # 1. Load the pipeline profile.
        profile = self._db.get_pipeline_profile(profile_id)
        if profile is None:
            raise PipelineResolutionError(
                "Profilo pipeline non trovato",
                "Il profilo pipeline selezionato non esiste. "
                "Contatta l'amministratore.",
            )
        log.append(f"Loaded pipeline profile '{profile['name']}' (id={profile_id})")

        # 2. Determine if refinement is needed.
        needs_refinement = (
            mode == RequestMode.FULL and not refinement_globally_disabled
        )
        log.append(
            f"Request mode: {mode.value}, "
            f"refinement={'enabled' if needs_refinement else 'disabled'}"
        )

        # 3. Load the transcription provider.
        tx_id = profile.get("transcription_provider_id")
        if tx_id is None:
            raise PipelineResolutionError(
                "Nessun provider di trascrizione",
                "Il profilo pipeline non ha un provider di trascrizione "
                "configurato.",
            )
        tx_provider = self._db.get_provider(tx_id)
        if tx_provider is None:
            raise PipelineResolutionError(
                "Provider di trascrizione non trovato",
                "Il provider di trascrizione referenziato dal profilo "
                "non esiste più.",
            )
        if not tx_provider.get("enabled"):
            raise PipelineResolutionError(
                "Provider di trascrizione disabilitato",
                "Il provider di trascrizione configurato è stato "
                "disabilitato.",
            )
        log.append(
            f"Transcription provider: '{tx_provider['name']}' "
            f"(adapter: {tx_provider['adapter_type']})"
        )

        # 4. Check transcription capability.
        tx_detected = detect_capabilities(
            tx_provider.get("adapter_type", ""),
            tx_provider.get("model_name", ""),
        )
        tx_overrides = tx_provider.get("capabilities")
        tx_effective = merge_capabilities(tx_detected, tx_overrides)
        if not tx_effective.transcription:
            raise PipelineResolutionError(
                "Provider senza capacità di trascrizione",
                f"Il provider '{tx_provider['name']}' non supporta "
                f"la trascrizione audio.",
            )

        # 5. Load the text processor provider (may differ from transcription).
        ref_id = profile.get("text_provider_id")
        same_provider = ref_id is not None and ref_id == tx_id

        if needs_refinement:
            if ref_id is None:
                raise PipelineResolutionError(
                    "Nessun provider di refinement",
                    "Il profilo pipeline non ha un provider di "
                    "refinement configurato.",
                )
            ref_provider = self._db.get_provider(ref_id)
            if ref_provider is None:
                raise PipelineResolutionError(
                    "Provider di refinement non trovato",
                    "Il provider di refinement referenziato dal "
                    "profilo non esiste più.",
                )
            if not ref_provider.get("enabled"):
                raise PipelineResolutionError(
                    "Provider di refinement disabilitato",
                    "Il provider di refinement configurato è stato "
                    "disabilitato.",
                )

            # Check refinement capability.
            ref_detected = detect_capabilities(
                ref_provider.get("adapter_type", ""),
                ref_provider.get("model_name", ""),
            )
            ref_overrides = ref_provider.get("capabilities")
            ref_effective = merge_capabilities(ref_detected, ref_overrides)
            if not ref_effective.refinement:
                raise PipelineResolutionError(
                    "Provider senza capacità di refinement",
                    f"Il provider '{ref_provider['name']}' non "
                    f"supporta il refinement del testo.",
                )

            if same_provider:
                log.append(
                    f"Same-provider default: using '{tx_provider['name']}' "
                    f"for both transcription and refinement"
                )
                return self._build_plan(tx_provider, needs_refinement, log)
            else:
                log.append(
                    f"Using separate providers: "
                    f"transcription={tx_provider['name']}, "
                    f"refinement={ref_provider['name']}"
                )
                return self._build_plan_with_separate(
                    tx_provider, ref_provider, log
                )
        else:
            # Refinement not needed — use the transcription provider only.
            log.append(
                f"Transcription only: using '{tx_provider['name']}'"
            )
            return self._build_plan(tx_provider, needs_refinement=False, log=log)

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
        #    A provider's effective capabilities = detected defaults +
        #    overrides stored in the DB.
        profiles: list[tuple[dict[str, Any], CapabilityModel]] = []
        for p in enabled:
            detected = detect_capabilities(
                p.get("adapter_type", ""),
                p.get("model_name", ""),
            )
            overrides = p.get("capabilities")  # already parsed JSON dict
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

        # Should not reach here, but defensively:
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
            # If they are the same provider, _find_single_provider would
            # already have handled it, so require different IDs here.
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

        # Create the transcriber.
        transcriber = self._create_transcriber(
            adapter_type,
            credentials,
            endpoint,
            model_name,
        )

        # Create the text processor (if needed).
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
        # Transcription provider
        tx_type = tx_provider["adapter_type"]
        tx_creds = tx_provider.get("credentials") or ""
        tx_endpoint = tx_provider.get("endpoint") or ""
        tx_model = tx_provider.get("model_name") or _default_model_for(tx_type)

        transcriber = self._create_transcriber(
            tx_type,
            tx_creds,
            tx_endpoint,
            tx_model,
        )

        # Refinement provider
        ref_type = ref_provider["adapter_type"]
        ref_creds = ref_provider.get("credentials") or ""
        ref_endpoint = ref_provider.get("endpoint") or ""
        ref_model = ref_provider.get("model_name") or _default_model_for(ref_type)

        text_processor = self._create_text_processor(
            ref_type,
            ref_creds,
            ref_endpoint,
            ref_model,
        )

        return ExecutionPlan(
            transcriber=transcriber,
            text_processor=text_processor,
            provider_name=f"{tx_provider['name']} + {ref_provider['name']}",
            model_name=f"{tx_model} / {ref_model}",
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

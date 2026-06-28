"""
Capability model for provider connections (P2).

Defines what a provider endpoint and model can do, decoupling capability
checks from provider name heuristics.

Design
------
- :class:`CapabilityModel` is a frozen dataclass with one bool flag per
  capability.  Unknown / absent flags default to ``False``.
- :func:`detect_capabilities` returns a *detected* model based on the
  adapter type and model name.  It does **not** call any external API.
- :func:`merge_capabilities` combines *detected* and *overridden* values
  so the two sources remain distinguishable.
- Each adapter class exposes a ``default_capabilities()`` classmethod so
  the rest of the system can query what an adapter *should* be able to do.
- :func:`probe_openrouter_capabilities` performs an HTTP request to an
  OpenRouter Models API endpoint and returns a conservative classification
  based on model metadata (``input_modalities``, ``output_modalities``,
  ``supported_parameters``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapabilityModel:
    """Typed capability set for a provider endpoint + model pair.

    All fields default to ``False`` so that unknown or legacy entries
    are treated conservatively.
    """

    transcription: bool = False
    """Can transcribe audio (Whisper, Gemini upload + generate, …)."""

    text_generation: bool = False
    """Can generate or complete text (GPT, Gemini, Claude, …)."""

    refinement: bool = False
    """Can refine/improve an existing transcript (subset of text_generation)."""

    streaming_refinement: bool = False
    """Can stream refined text incrementally."""

    single_pass_audio_to_text: bool = False
    """Can process audio directly to refined text in a single pass
    (e.g. Gemini multimodal, GPT-4o audio).

    A model with this capability can be used in ``single_pass`` pipeline
    mode: the audio is sent to the model which both transcribes and
    refines in one request.
    """

    def to_dict(self) -> dict[str, bool]:
        """Return a JSON-safe dict (``True``/``False`` only)."""
        return {
            "transcription": self.transcription,
            "text_generation": self.text_generation,
            "refinement": self.refinement,
            "streaming_refinement": self.streaming_refinement,
            "single_pass_audio_to_text": self.single_pass_audio_to_text,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> CapabilityModel:
        """Reconstruct from a dict (e.g. deserialised JSON).

        Missing keys default to ``False``.
        """
        if not data:
            return cls()
        return cls(
            transcription=bool(data.get("transcription", False)),
            text_generation=bool(data.get("text_generation", False)),
            refinement=bool(data.get("refinement", False)),
            streaming_refinement=bool(data.get("streaming_refinement", False)),
            single_pass_audio_to_text=bool(data.get("single_pass_audio_to_text", False)),
        )


# ---------------------------------------------------------------------------
# Defaults per adapter type
# ---------------------------------------------------------------------------

# Keys match the ``adapter_type`` column in ``provider_connections``.
_ADAPTER_DEFAULTS: dict[str, CapabilityModel] = {
    "openai": CapabilityModel(
        transcription=True,
        text_generation=True,
        refinement=True,
        streaming_refinement=True,
        single_pass_audio_to_text=False,
    ),
    "openai-native": CapabilityModel(
        transcription=True,
        text_generation=True,
        refinement=True,
        streaming_refinement=True,
        single_pass_audio_to_text=False,
    ),
    "gemini": CapabilityModel(
        transcription=True,
        text_generation=True,
        refinement=True,
        streaming_refinement=True,
        single_pass_audio_to_text=True,  # Gemini multimodal can do single-pass
    ),
    "gemini-native": CapabilityModel(
        transcription=True,
        text_generation=True,
        refinement=True,
        streaming_refinement=True,
        single_pass_audio_to_text=True,  # Gemini multimodal can do single-pass
    ),
    "openai-compat": CapabilityModel(
        transcription=False,  # conservative — not every compat endpoint has audio
        text_generation=True,
        refinement=True,
        streaming_refinement=True,
        single_pass_audio_to_text=False,
    ),
}

# Models whose *only* capability is text generation (no audio).
_TEXT_ONLY_MODELS: set[str] = set()

# Models known to support audio transcription.
_TRANSCRIPTION_MODELS: set[str] = {
    "whisper-1",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
}


def default_for_adapter(adapter_type: str) -> CapabilityModel:
    """Return the default capability model for *adapter_type*.

    Falls back to an all-``False`` model for unknown types.
    """
    return _ADAPTER_DEFAULTS.get(adapter_type, CapabilityModel())


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def detect_capabilities(
    adapter_type: str,
    model_name: str = "",
) -> CapabilityModel:
    """Detect capabilities for a given adapter type and model name.

    Uses:
    1. Adapter-known defaults as the base.
    2. Model-name heuristics to override specific capabilities.

    This is a **static** detection — no external API is called.
    """
    base = default_for_adapter(adapter_type)
    if not model_name:
        return base

    mid = model_name.lower()
    has_audio_keywords = any(kw in mid for kw in ("whisper", "audio"))
    is_known_transcription = mid in {m.lower() for m in _TRANSCRIPTION_MODELS}

    # Transcription capability per adapter type.
    if adapter_type in ("openai", "openai-native"):
        # OpenAI has a separate Whisper service — transcription always available
        # regardless of which model is used for chat.
        transcription = True
    elif adapter_type in ("gemini", "gemini-native"):
        # Gemini uses the same model for everything; transcription requires
        # a known transcription-capable model.
        transcription = has_audio_keywords or is_known_transcription
    elif adapter_type == "openai-compat":
        # OpenAI-compat: transcription only when the model name clearly
        # indicates audio support.  Most openai-compat endpoints are
        # text-only, so we are conservative here.
        transcription = has_audio_keywords or is_known_transcription
    else:
        # Unknown adapter: keep the base default (typically False).
        transcription = base.transcription

    # single_pass_audio_to_text: requires both audio input AND text generation.
    # For Gemini, single-pass is the default.  For other adapters, it matches
    # the base default unless transcription is available without text gen.
    if adapter_type in ("gemini", "gemini-native"):
        single_pass = base.single_pass_audio_to_text and transcription
    elif adapter_type in ("openai", "openai-native", "openai-compat"):
        # OpenAI has separate Whisper (STT) and GPT (text) — not single-pass
        single_pass = False
    else:
        single_pass = base.single_pass_audio_to_text

    return CapabilityModel(
        transcription=transcription,
        text_generation=base.text_generation,
        refinement=base.refinement,
        streaming_refinement=_detect_streaming(model_name) if base.streaming_refinement else False,
        single_pass_audio_to_text=single_pass,
    )


def _detect_streaming(model_name: str) -> bool:
    """Return ``True`` when *model_name* likely supports streaming.

    Most modern LLM APIs support streaming.  We only disable it for
    known non-streaming models.
    """
    mid = model_name.lower()
    non_streaming = {"gpt-3.5-turbo-instruct", "babbage-002", "davinci-002"}
    return mid not in non_streaming


def merge_capabilities(
    detected: CapabilityModel,
    overrides: Optional[dict[str, bool]],
) -> CapabilityModel:
    """Merge *detected* with an *overrides* dict, with overrides taking
    precedence on any key that is present.

    When *overrides* is ``None`` or empty the detected model is returned
    as-is.  This matches the DB pattern where stored overrides are a
    sparse JSON dict.
    """
    if not overrides:
        return detected
    return CapabilityModel(
        transcription=overrides.get("transcription", detected.transcription),
        text_generation=overrides.get("text_generation", detected.text_generation),
        refinement=overrides.get("refinement", detected.refinement),
        streaming_refinement=overrides.get("streaming_refinement", detected.streaming_refinement),
        single_pass_audio_to_text=overrides.get("single_pass_audio_to_text", detected.single_pass_audio_to_text),
    )


# ===================================================================
# OpenRouter capability probing
# ===================================================================
# OpenRouter exposes model metadata through its Models API, including
# input_modalities, output_modalities, and supported_parameters.  We use
# these to classify capabilities conservatively instead of assuming that
# every openai-compat endpoint supports transcription.

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]


def _openrouter_normalise_endpoint(endpoint: str) -> str:
    """Return a ``/v1/models``-compatible base URL for *endpoint*."""
    if not endpoint:
        return "https://openrouter.ai/api/v1"
    base = endpoint.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base


def _find_openrouter_model(
    models: list[dict],
    query: str,
) -> dict | None:
    """Find the model entry that best matches *query*.

    Matching is case-insensitive.  Tries exact match on ``id`` /
    ``canonical_slug`` / ``name`` first, then substring match.
    """
    if not query:
        return None

    query_lower = query.lower().strip()

    # Exact match.
    for m in models:
        mid = (m.get("id") or "").lower()
        slug = (m.get("canonical_slug") or "").lower()
        name = (m.get("name") or "").lower()
        if mid == query_lower or slug == query_lower or name == query_lower:
            return m

    # Substring match.
    for m in models:
        mid = (m.get("id") or "").lower()
        slug = (m.get("canonical_slug") or "").lower()
        name = (m.get("name") or "").lower()
        if query_lower in mid or query_lower in slug or query_lower in name:
            return m

    return None


# Strong STT (speech-to-text) indicators — models whose primary purpose
# is audio transcription.
_STT_KEYWORDS: set[str] = {
    "whisper", "transcribe", "transcription",
    "speech-to-text", "speech2text",
}


def _classify_openrouter_metadata(model: dict) -> dict:
    """Classify OpenRouter model metadata into separate capability fields.

    Returns a dict with keys ``audio_input``, ``transcription``,
    ``text_generation``, ``refinement``, and ``streaming_refinement``
    so callers can distinguish "model accepts audio" from "model can
    perform speech-to-text".

    *audio_input*
        ``True`` when ``architecture.input_modalities`` includes ``"audio"``.

    *transcription*
        ``True`` only for explicit STT/transcription models:
        - ``id`` or ``name`` contains known STT keywords
          (``whisper``, ``transcribe``, ``transcription``,
          ``speech-to-text``, ``speech2text``).
        - ``architecture.modality`` starts with ``"audio"``
          (e.g. ``"audio->text"``).
        - ``architecture.output_modalities`` includes ``"transcription"``.

    *text_generation*
        ``True`` when ``architecture.output_modalities`` includes ``"text"``.

    *refinement*
        Follows *text_generation*.

    *streaming_refinement*
        ``True`` when ``supported_parameters`` includes ``"stream"``.

    *single_pass_audio_to_text*
        ``True`` when the model has audio input AND text output AND is
        not a pure STT model.  Such models can transcribe and refine in
        a single API call.
    """
    arch = model.get("architecture")
    if arch is None:
        return {
            "audio_input": False,
            "transcription": False,
            "text_generation": False,
            "refinement": False,
            "streaming_refinement": False,
            "single_pass_audio_to_text": False,
        }

    input_mods = set(arch.get("input_modalities") or [])
    output_mods = set(arch.get("output_modalities") or [])
    has_output_modalities = "output_modalities" in arch and arch["output_modalities"] is not None
    supported_params = set(model.get("supported_parameters") or [])
    model_id_lower = (model.get("id") or "").lower()
    model_name_lower = (model.get("name") or "").lower()
    modality = (arch.get("modality") or "").lower()

    has_audio_input = "audio" in input_mods

    # --- transcription (STT) — only explicit indicators ---
    is_stt_model_id = any(kw in model_id_lower for kw in _STT_KEYWORDS)
    is_stt_model_name = any(kw in model_name_lower for kw in _STT_KEYWORDS)
    modality_starts_audio = modality.startswith("audio")
    output_is_transcription = "transcription" in output_mods
    transcription = is_stt_model_id or is_stt_model_name or modality_starts_audio or output_is_transcription

    # --- text_generation ---
    text_generation = has_output_modalities and "text" in output_mods

    # --- streaming ---
    streaming = "stream" in supported_params

    # --- single_pass_audio_to_text ---
    # A model qualifies for single_pass_audio_to_text when it has audio input
    # AND text output AND is NOT a pure STT model.  Pure STT models (whisper)
    # transcribe only; they don't refine.  Single-pass implies the model can
    # both transcribe AND refine in one call.
    is_pure_stt = transcription and not text_generation
    single_pass_audio_to_text = has_audio_input and text_generation and not is_pure_stt

    return {
        "audio_input": has_audio_input,
        "transcription": transcription,
        "text_generation": text_generation,
        "refinement": text_generation,
        "streaming_refinement": streaming,
        "single_pass_audio_to_text": single_pass_audio_to_text,
    }


def _classify_openrouter_model(model: dict) -> CapabilityModel:
    """Classify capabilities based on OpenRouter model metadata.

    Uses :func:`_classify_openrouter_metadata` internally and maps the
    result to a :class:`CapabilityModel`.  The ``audio_input`` flag is
    **not** propagated to ``transcription`` — only explicit STT
    indicators produce ``transcription=True``.
    """
    meta = _classify_openrouter_metadata(model)
    return CapabilityModel(
        transcription=meta["transcription"],
        text_generation=meta["text_generation"],
        refinement=meta["refinement"],
        streaming_refinement=meta["streaming_refinement"],
        single_pass_audio_to_text=meta["single_pass_audio_to_text"],
    )


async def probe_openrouter_capabilities(
    api_key: str,
    endpoint: str,
    model_name: str,
    session: httpx.AsyncClient | None = None,
) -> tuple[CapabilityModel, dict]:
    """Probe OpenRouter model metadata for capability detection.

    Fetches the model list from the OpenRouter Models API
    (``GET /v1/models?output_modalities=all``), finds the entry matching
    *model_name*, and returns a :class:`CapabilityModel` together with
    the full metadata dict from :func:`_classify_openrouter_metadata`.

    Returns an all-``False`` :class:`CapabilityModel` and an all-``False``
    metadata dict when the API is unreachable, the model is not found, or
    any error occurs.

    Parameters
    ----------
    api_key:
        OpenRouter API key (``Bearer`` auth).
    endpoint:
        Base URL of the OpenAI-compatible endpoint
        (e.g. ``https://openrouter.ai/api/v1``).
    model_name:
        The model identifier to probe (e.g. ``"openai/gpt-4o"``,
        ``"gpt-4o"``).  Used for fuzzy matching.
    session:
        Optional ``httpx.AsyncClient``.  When ``None``, a short-lived
        client is created and closed automatically.

    Returns
    -------
    tuple[CapabilityModel, dict]
        Detected capabilities and full metadata.  Both are conservative
        (all-``False``) when probing fails.
    """
    _FALLBACK_META = {
        "audio_input": False,
        "transcription": False,
        "text_generation": False,
        "refinement": False,
        "streaming_refinement": False,
        "single_pass_audio_to_text": False,
    }

    if httpx is None:  # pragma: no cover
        logger.warning("httpx not available — cannot probe OpenRouter capabilities")
        return CapabilityModel(), _FALLBACK_META

    base_url = _openrouter_normalise_endpoint(endpoint)
    headers = {"Authorization": f"Bearer {api_key}"}
    url = f"{base_url}/models"
    params = {"output_modalities": "all"}

    close_session = session is None
    if session is None:
        session = httpx.AsyncClient(timeout=15)

    try:
        resp = await session.get(url, headers=headers, params=params)
        if resp.status_code != 200:
            logger.warning(
                "OpenRouter API returned status %s for model '%s'",
                resp.status_code,
                model_name,
            )
            return CapabilityModel(), _FALLBACK_META
        data = resp.json().get("data", [])

        matched = _find_openrouter_model(data, model_name)

        if matched is None:
            logger.info(
                "OpenRouter model '%s' not found in model list "
                "(total %d models returned) — returning conservative caps",
                model_name,
                len(data),
            )
            return CapabilityModel(), _FALLBACK_META

        meta = _classify_openrouter_metadata(matched)
        caps = CapabilityModel(
            transcription=meta["transcription"],
            text_generation=meta["text_generation"],
            refinement=meta["refinement"],
            streaming_refinement=meta["streaming_refinement"],
        )
        logger.debug(
            "OpenRouter probe for %s: audio_input=%s transcription=%s text=%s stream=%s",
            matched.get("id", model_name),
            meta["audio_input"],
            caps.transcription,
            caps.text_generation,
            caps.streaming_refinement,
        )
        return caps, meta

    except Exception:
        logger.exception(
            "OpenRouter capability probe failed for model '%s'",
            model_name,
        )
        return CapabilityModel(), _FALLBACK_META
    finally:
        if close_session:
            await session.aclose()

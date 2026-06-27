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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


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

    def to_dict(self) -> dict[str, bool]:
        """Return a JSON-safe dict (``True``/``False`` only)."""
        return {
            "transcription": self.transcription,
            "text_generation": self.text_generation,
            "refinement": self.refinement,
            "streaming_refinement": self.streaming_refinement,
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
    ),
    "openai-native": CapabilityModel(
        transcription=True,
        text_generation=True,
        refinement=True,
        streaming_refinement=True,
    ),
    "gemini": CapabilityModel(
        transcription=True,
        text_generation=True,
        refinement=True,
        streaming_refinement=True,
    ),
    "gemini-native": CapabilityModel(
        transcription=True,
        text_generation=True,
        refinement=True,
        streaming_refinement=True,
    ),
    "openai-compat": CapabilityModel(
        transcription=True,
        text_generation=True,
        refinement=True,
        streaming_refinement=True,
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

    # Transcription requires an audio-capable model.
    if base.transcription and not any(kw in mid for kw in ("whisper", "audio")):
        # Some adapter types (e.g. OpenAI) have a separate transcription
        # service (Whisper) from the model.  For those, the detection
        # model is not the transcription model.
        if adapter_type in ("openai", "openai-native", "openai-compat"):
            pass  # Whisper is a separate service — keep transcription=True.
        elif mid not in {m.lower() for m in _TRANSCRIPTION_MODELS}:
            pass  # keep the adapter default

    return CapabilityModel(
        transcription=base.transcription,
        text_generation=base.text_generation,
        refinement=base.refinement,
        streaming_refinement=_detect_streaming(model_name) if base.streaming_refinement else False,
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
    )

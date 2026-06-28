"""
Smart model picker helpers for guided setup (W10).

The web UI needs a compact, stable representation of model tradeoffs.  This
module keeps that shaping logic outside FastAPI routes so setup, provider
detail pages, and tests can share the same catalog behavior.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from bot.capabilities import _classify_openrouter_metadata, _classify_openrouter_model

OPENROUTER_PURPOSES = {
    "refinement",
    "transcription",
    "single_pass",
    "all",
    "all_recommended",
}

_PREFERRED_TEXT_FAMILIES = (
    "gpt-4o-mini",
    "gpt-4.1-mini",
    "gemini-2.0-flash",
    "gemini-2.5-flash",
    "claude-3.5-haiku",
    "claude-3-haiku",
    "llama-3.1",
    "llama-3.3",
    "mistral",
    "qwen",
)

_FAST_TOKENS = ("mini", "flash", "haiku", "fast", "turbo", "small", "lite")
_SLOW_TOKENS = ("opus", "pro", "large", "reasoning", "o1", "o3")
_HIGH_QUALITY_TOKENS = (
    "gpt-4o",
    "gpt-4.1",
    "claude-3.5",
    "claude-3.7",
    "claude-sonnet",
    "gemini-2.5-pro",
    "o3",
)
_ECONOMY_TOKENS = ("mini", "flash", "haiku", "llama", "mistral", "qwen")


def transcription_locked_card() -> dict[str, Any]:
    """Return the locked Whisper card required by W10."""
    return {
        "kind": "locked",
        "model_id": "whisper-1",
        "name": "Whisper",
        "provider": "OpenAI",
        "description": "Whisper - standard industriale per la trascrizione vocale",
        "category": "transcription",
        "capabilities": {
            "transcription": True,
            "text_generation": False,
            "refinement": False,
            "streaming_refinement": False,
            "single_pass_audio_to_text": False,
        },
        "locked": True,
        "recommended": True,
    }


def openrouter_model_category(model: dict[str, Any] | None) -> str:
    """Bucket an OpenRouter model by the pipeline role it can serve."""
    if model is None:
        return "not_recommended"
    meta = _classify_openrouter_metadata(model)
    if meta.get("transcription"):
        return "transcription"
    if meta.get("single_pass_audio_to_text"):
        return "single_pass"
    if meta.get("refinement"):
        return "refinement"
    return "not_recommended"


def openrouter_matches_purpose(model: dict[str, Any], purpose: str) -> bool:
    """Return whether *model* belongs in the requested picker purpose."""
    purpose = purpose if purpose in OPENROUTER_PURPOSES else "all_recommended"
    category = openrouter_model_category(model)
    if purpose == "refinement":
        return category == "refinement"
    if purpose == "transcription":
        return category == "transcription"
    if purpose == "single_pass":
        return category == "single_pass"
    if purpose == "all":
        return category != "not_recommended"
    return category in {"refinement", "transcription", "single_pass"}


def select_openrouter_models(
    models: list[dict[str, Any]],
    *,
    purpose: str,
    query: str = "",
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Return a guided OpenRouter shortlist for a picker carousel."""
    purpose = purpose if purpose in OPENROUTER_PURPOSES else "all_recommended"
    query_lower = query.lower().strip()
    selected: list[dict[str, Any]] = []
    for model in models:
        mid = (model.get("id") or "").lower()
        name = (model.get("name") or "").lower()
        if query_lower and query_lower not in mid and query_lower not in name:
            continue
        if openrouter_matches_purpose(model, purpose):
            selected.append(model)
    selected.sort(key=openrouter_model_score)
    return selected[: max(1, limit)]


def openrouter_catalog_item(model: dict[str, Any]) -> dict[str, Any]:
    """Return rich model metadata for advanced catalog tables."""
    arch = model.get("architecture") or {}
    pricing = model.get("pricing") or {}
    top_provider = model.get("top_provider") or {}
    meta = _classify_openrouter_metadata(model)
    caps = _classify_openrouter_model(model).to_dict()
    return {
        "model_id": model.get("id") or "",
        "name": model.get("name") or model.get("id") or "",
        "description": model.get("description") or "",
        "category": openrouter_model_category(model),
        "capabilities": caps,
        "metadata": meta,
        "context_length": model.get("context_length"),
        "max_completion_tokens": top_provider.get("max_completion_tokens"),
        "pricing": {
            "prompt": pricing.get("prompt"),
            "completion": pricing.get("completion"),
            "request": pricing.get("request"),
            "image": pricing.get("image"),
        },
        "input_modalities": arch.get("input_modalities") or [],
        "output_modalities": arch.get("output_modalities") or [],
        "supported_parameters": model.get("supported_parameters") or [],
    }


def build_openrouter_picker_cards(
    models: list[dict[str, Any]],
    *,
    purpose: str = "refinement",
    query: str = "",
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Build card-carousel data from OpenRouter model metadata."""
    selected = select_openrouter_models(
        models,
        purpose=purpose,
        query=query,
        limit=limit,
    )
    cards = [_openrouter_picker_card(model) for model in selected]
    _mark_recommended(cards, purpose=purpose)
    return cards


def manual_model_card(model_id: str, provider: str, purpose: str = "refinement") -> dict[str, Any]:
    """Return a conservative card for a manually entered model ID."""
    model_id = model_id.strip()
    return {
        "kind": "manual",
        "model_id": model_id,
        "name": model_id,
        "provider": provider,
        "description": "Modello inserito manualmente. Verifica le capacità prima di usarlo.",
        "category": purpose if purpose in {"refinement", "transcription", "single_pass"} else "refinement",
        "capabilities": {},
        "pricing": {
            "input_per_million": None,
            "output_per_million": None,
            "currency": "USD",
        },
        "speed": "unknown",
        "quality": "unknown",
        "recommended": False,
        "source": "manual",
    }


def openrouter_counts(models: list[dict[str, Any]]) -> dict[str, int]:
    """Count catalog models by picker category."""
    counts = {
        "refinement": 0,
        "transcription": 0,
        "single_pass": 0,
        "not_recommended": 0,
    }
    for model in models:
        category = openrouter_model_category(model)
        counts[category] = counts.get(category, 0) + 1
    return counts


def openrouter_model_score(model: dict[str, Any]) -> tuple[int, int, Decimal, str]:
    """Sort useful OpenRouter models before catalog long-tail entries."""
    mid = (model.get("id") or "").lower()
    name = (model.get("name") or "").lower()
    category = openrouter_model_category(model)
    category_score = {
        "refinement": 0,
        "transcription": 1,
        "single_pass": 2,
        "not_recommended": 3,
    }.get(category, 3)
    preferred = 0 if any(token in mid or token in name for token in _PREFERRED_TEXT_FAMILIES) else 1
    price = _pricing_per_million((model.get("pricing") or {}).get("prompt"))
    return (category_score, preferred, price if price is not None else Decimal("999999"), mid)


def _openrouter_picker_card(model: dict[str, Any]) -> dict[str, Any]:
    pricing = model.get("pricing") or {}
    caps = _classify_openrouter_model(model).to_dict()
    category = openrouter_model_category(model)
    model_id = model.get("id") or ""
    name = model.get("name") or model_id
    return {
        "kind": "model",
        "model_id": model_id,
        "name": name,
        "provider": _provider_from_model_id(model_id),
        "description": model.get("description") or "",
        "category": category,
        "capabilities": caps,
        "pricing": {
            "input_per_million": _decimal_to_float(_pricing_per_million(pricing.get("prompt"))),
            "output_per_million": _decimal_to_float(_pricing_per_million(pricing.get("completion"))),
            "currency": "USD",
        },
        "speed": _speed_indicator(model_id, name),
        "quality": _quality_indicator(model_id, name),
        "recommended": False,
        "source": "openrouter",
    }


def _mark_recommended(cards: list[dict[str, Any]], *, purpose: str) -> None:
    if not cards:
        return
    preferred_index = 0
    if purpose == "refinement":
        for idx, card in enumerate(cards):
            if card["speed"] in {"fast", "medium"} and card["quality"] in {"medium", "high"}:
                preferred_index = idx
                break
    elif purpose == "single_pass":
        for idx, card in enumerate(cards):
            if card["quality"] in {"medium", "high"}:
                preferred_index = idx
                break
    cards[preferred_index]["recommended"] = True


def _pricing_per_million(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)) * Decimal("1000000")
    except (InvalidOperation, ValueError):
        return None


def _decimal_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)


def _provider_from_model_id(model_id: str) -> str:
    if "/" not in model_id:
        return "OpenRouter"
    prefix = model_id.split("/", 1)[0]
    return {
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "google": "Google",
        "meta-llama": "Meta",
        "mistralai": "Mistral",
        "qwen": "Qwen",
    }.get(prefix, prefix.replace("-", " ").title())


def _speed_indicator(model_id: str, name: str) -> str:
    text = f"{model_id} {name}".lower()
    if any(token in text for token in _FAST_TOKENS):
        return "fast"
    if any(token in text for token in _SLOW_TOKENS):
        return "slow"
    return "medium"


def _quality_indicator(model_id: str, name: str) -> str:
    text = f"{model_id} {name}".lower()
    if any(token in text for token in _HIGH_QUALITY_TOKENS):
        return "high"
    if any(token in text for token in _ECONOMY_TOKENS):
        return "medium"
    return "medium"

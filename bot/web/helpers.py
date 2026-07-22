"""
Shared helper functions for web route modules.

These were extracted from ``bot/web/app.py`` during the O5 modularisation
to keep route definitions focused on request handling.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from bot.capabilities import (
    CapabilityModel,
    _classify_openrouter_metadata,
    detect_capabilities,
    probe_openrouter_capabilities,
)
from bot.model_picker import (
    openrouter_catalog_item,
    openrouter_matches_purpose,
    openrouter_model_category,
    openrouter_model_score,
    select_openrouter_models,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BLANK_CAPS = {
    "transcription": False,
    "text_generation": False,
    "refinement": False,
    "streaming_refinement": False,
    "single_pass_audio_to_text": False,
}

_OPENROUTER_DISCOVERY_DEFAULT_LIMIT = 30
_OPENROUTER_DISCOVERY_MAX_LIMIT = 50

# Curated shortlist shown to admins on first setup instead of the full catalog.
OPENROUTER_CURATED_SHORTLIST = [
    "openai/whisper-1",
    "openai/gpt-4o-mini",
    "openai/gpt-4o",
    "anthropic/claude-3-5-haiku",
    "google/gemini-flash-1.5-8b",
    "meta-llama/llama-3.3-70b-instruct",
    "google/gemini-2.0-flash-001",
    "openai/gpt-4o-audio-preview",
]

# ---------------------------------------------------------------------------
# Sanitisation
# ---------------------------------------------------------------------------


def sanitize_error_detail(raw: str) -> str:
    """Remove API-key patterns from error detail strings.

    OpenAI error messages can contain ``sk-...`` prefix when auth fails
    (e.g. ``"Incorrect API key provided: sk-proj-xxxxxxxxxxxx"``).
    Strip any ``sk-`` prefixed token to avoid leaking the key prefix.
    """
    return re.sub(r"\bsk-[A-Za-z0-9_-]+", "[API key hidden]", raw)


# ---------------------------------------------------------------------------
# Blank test result factory
# ---------------------------------------------------------------------------


def blank_test_result() -> dict[str, Any]:
    """Return a blank result template for the provider test schema."""
    return {
        "ok": False,
        "auth_ok": False,
        "models_ok": False,
        "capabilities": dict(_BLANK_CAPS),
        "pipeline_status": "not_compatible",
        "user_message": "",
        "warnings": [],
        "models": [],
    }


# ---------------------------------------------------------------------------
# OpenRouter discovery helpers
# ---------------------------------------------------------------------------


def parse_discovery_limit(raw: str | None) -> int:
    """Return a bounded discovery limit suitable for provider catalog scans."""
    if raw is None or raw == "":
        return _OPENROUTER_DISCOVERY_DEFAULT_LIMIT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _OPENROUTER_DISCOVERY_DEFAULT_LIMIT
    return max(1, min(value, _OPENROUTER_DISCOVERY_MAX_LIMIT))


def openrouter_model_category_wrapper(model: dict[str, Any] | None) -> str:
    """Bucket an OpenRouter model by the pipeline role it can serve."""
    return openrouter_model_category(model)


def openrouter_matches_purpose_wrapper(model: dict[str, Any], purpose: str) -> bool:
    return openrouter_matches_purpose(model, purpose)


def openrouter_model_score_wrapper(model: dict[str, Any]) -> tuple[int, str]:
    """Sort useful OpenRouter models before catalog long-tail entries."""
    score = openrouter_model_score(model)
    return (score[0] * 10 + score[1], score[3])


def openrouter_catalog_item_wrapper(model: dict[str, Any]) -> dict[str, Any]:
    """Return the compact model shape used by the OpenRouter catalog UI."""
    return openrouter_catalog_item(model)


def select_openrouter_models_wrapper(
    models: list[dict[str, Any]],
    *,
    purpose: str,
    query: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Return a small, guided OpenRouter model shortlist."""
    return select_openrouter_models(
        models,
        purpose=purpose,
        query=query,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Provider connection test
# ---------------------------------------------------------------------------


def _adapter_type_for_provider(provider_type: str) -> str:
    """Map a UI provider type to the internal adapter type."""
    mapping = {
        "openai": "openai-native",
        "gemini": "gemini-native",
        "openrouter": "openai-compat",
        "ollama": "openai-compat",
        "vllm": "openai-compat",
        "custom": "openai-compat",
    }
    return mapping.get(provider_type, "openai-compat")


async def test_provider_connection(
    provider_type: str,
    api_key: str,
    endpoint: str,
    model_name: str = "",
) -> dict[str, Any]:
    """Test a provider connection and return full result dict.

    The returned dict matches the schema shared by
    ``/api/providers/test`` and ``/api/setup/test-provider``:

    ``ok``, ``auth_ok``, ``models_ok``, ``capabilities``,
    ``pipeline_status``, ``user_message``, ``warnings``, ``models``

    *models* is the list of discovered model IDs (first 20).
    """
    import httpx

    result = blank_test_result()

    # ---- OpenAI-compatible endpoints ----
    if provider_type in ("openai", "openrouter", "custom", "ollama", "vllm"):
        url = f"{endpoint.rstrip('/')}/models" if endpoint else "https://api.openai.com/v1/models"
        headers = {"Authorization": f"Bearer {api_key}"}

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers)

            if resp.status_code == 200:
                result["auth_ok"] = True
                models_data = resp.json().get("data", [])
                model_ids = [m["id"] for m in models_data
                             if isinstance(m, dict) and m.get("id")]

                result["models"] = model_ids[:20]

                if model_ids:
                    result["models_ok"] = True

                # Capability detection
                if provider_type == "openrouter":
                    if model_name:
                        caps, meta = await probe_openrouter_capabilities(
                            api_key,
                            endpoint or "",
                            model_name,
                            session=client,
                        )
                        # If probe returned all-False we don't know
                        if caps == CapabilityModel() and model_ids:
                            result["models_ok"] = False
                            result["warnings"].append(
                                "Impossibile determinare le capacità "
                                "del modello. Verifica manualmente."
                            )
                        else:
                            # audio_input without transcription → warning
                            if meta.get("audio_input") and not caps.transcription:
                                result["warnings"].append(
                                    "Modello con capacità audio, ma la "
                                    "trascrizione (STT) deve essere "
                                    "verificata. I modelli che accettano "
                                    "audio in input non sempre eseguono "
                                    "speech-to-text."
                                )
                    else:
                        # No model specified — assume text-only
                        caps = CapabilityModel(
                            text_generation=True,
                            refinement=True,
                            streaming_refinement=True,
                        )
                        result["warnings"].append(
                            "Nessun modello specificato. Le capacità "
                            "rilevate sono stimate."
                        )

                    # Extra warnings for OpenRouter
                    if caps.transcription is False and caps.text_generation:
                        result["warnings"].append(
                            "I modelli chat/testo non trascrivono "
                            "automaticamente audio. Con OpenRouter "
                            "potresti aver bisogno di un modello "
                            "separato per la trascrizione (es. whisper-1) "
                            "e di un modello chat per il refinement."
                        )
                    elif caps.transcription is False:
                        result["warnings"].append(
                            "Le capacità di trascrizione non sono "
                            "state rilevate. Verifica che il modello "
                            "supporti input audio."
                        )
                    elif caps.transcription and caps.text_generation is False:
                        result["warnings"].append(
                            "Modello solo trascrizione: non può "
                            "eseguire refinement testuale."
                        )
                else:
                    # OpenAI / custom / ollama / vLLM — static detection
                    adapter_type = _adapter_type_for_provider(provider_type)
                    caps = detect_capabilities(adapter_type, model_name)

                result["capabilities"] = caps.to_dict()

            else:
                # Auth failed
                err_detail = ""
                try:
                    err_detail = resp.json().get("error", {}).get(
                        "message", "Chiave API non valida"
                    )
                except Exception:
                    err_detail = "Chiave API non valida"
                err_detail = sanitize_error_detail(err_detail)
                result["user_message"] = f"❌ Chiave API non valida: {err_detail}"
                result["warnings"].append(
                    "Verifica che la chiave API sia corretta e abbia "
                    "i permessi necessari."
                )

    # ---- Gemini ----
    elif provider_type == "gemini":
        url = (
            f"https://generativelanguage.googleapis.com/v1/"
            f"models?key={api_key}"
        )
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)

            if resp.status_code == 200:
                result["auth_ok"] = True
                models_data = resp.json().get("models", [])
                model_ids = [m["name"].replace("models/", "") for m in models_data
                             if isinstance(m, dict) and m.get("name")]
                result["models"] = model_ids[:20]

                if model_ids:
                    result["models_ok"] = True

                adapter_type = _adapter_type_for_provider(provider_type)
                caps = detect_capabilities(adapter_type, model_name)
                result["capabilities"] = caps.to_dict()
            else:
                err_detail = ""
                try:
                    err_detail = resp.json().get("error", {}).get(
                        "message", "Chiave API non valida"
                    )
                except Exception:
                    err_detail = "Chiave API non valida"
                err_detail = sanitize_error_detail(err_detail)
                result["user_message"] = f"❌ Chiave API non valida: {err_detail}"
                result["warnings"].append(
                    "Verifica la chiave API di Google Generative AI."
                )

    else:
        result["user_message"] = f"❌ Provider sconosciuto: {provider_type}"
        return result

    # ---- Classify pipeline status ----
    caps = CapabilityModel.from_dict(result["capabilities"])
    if caps.transcription and (caps.text_generation or caps.refinement):
        result["pipeline_status"] = "complete_same_provider"
    elif caps.transcription:
        result["pipeline_status"] = "transcription_only"
    elif caps.text_generation or caps.refinement:
        result["pipeline_status"] = "refinement_only"

    # ---- Build user_message when auth succeeded ----
    if result["auth_ok"]:
        if caps.transcription and caps.refinement:
            result["user_message"] = (
                "✅ Connessione riuscita! Il provider supporta sia la "
                "trascrizione audio che il refinement testuale."
            )
        elif caps.transcription and not caps.refinement:
            result["user_message"] = (
                "✅ Connessione riuscita. Il provider supporta la "
                "trascrizione audio ma non il refinement testuale."
            )
        elif caps.refinement and not caps.transcription:
            result["user_message"] = (
                "⚠️ Connessione riuscita. Il modello selezionato è "
                "solo testo: può raffinare trascrizioni ma non "
                "trascrivere audio. Per la trascrizione scegli un "
                "modello speech-to-text. Con OpenRouter potresti "
                "aver bisogno di un modello diverso dallo stesso provider."
            )
        elif caps.text_generation and not caps.transcription:
            result["user_message"] = (
                "⚠️ Connessione riuscita. Questo provider supporta "
                "solo generazione testo (refinement), non trascrizione "
                "audio. Aggiungi un modello speech-to-text separato."
            )
        else:
            result["user_message"] = (
                "⚠️ Connessione riuscita, ma non è stato possibile "
                "determinare le capacità del provider."
            )
        result["ok"] = True

    return result

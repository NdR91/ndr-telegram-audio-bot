"""
W2 — Guided onboarding wizard step tracking and orchestration.

Stores wizard progress in the ``setup_state`` database table so users can
resume incomplete setups.

Steps
-----
1. ``step_code``       — Redeem one-time setup code (existing A6)
2. ``step_admin``      — Create first administrator password (existing A6)
3. ``step_telegram``   — Enter and verify Telegram bot token
4. ``step_provider``   — Connect first AI service (OpenAI / Gemini / custom)
5. ``step_capabilities`` — Auto-detect models and capabilities
6. ``step_pipeline``   — Choose "use this provider for everything" (default)
7. ``step_verify``     — Review resolved pipeline
8. ``step_done``       — Start the bot and redirect to dashboard
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from bot.database import DatabaseManager, SecretStore, SecretStoreError
from bot.capabilities import detect_capabilities
from bot.web.pipeline_builder import (
    create_single_pass_profile,
    create_two_stage_profile,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Step identifier constants
# ---------------------------------------------------------------------------

STEP_CODE = "step_code"
STEP_ADMIN = "step_admin"
STEP_TELEGRAM = "step_telegram"
STEP_PROVIDER = "step_provider"
STEP_CAPABILITIES = "step_capabilities"
STEP_PIPELINE = "step_pipeline"
STEP_VERIFY = "step_verify"
STEP_DONE = "step_done"

# Ordered list for step progression
_STEP_ORDER = [
    STEP_CODE,
    STEP_ADMIN,
    STEP_TELEGRAM,
    STEP_PROVIDER,
    STEP_CAPABILITIES,
    STEP_PIPELINE,
    STEP_VERIFY,
    STEP_DONE,
]

# Label, description, and icon for each step (Italian)
_STEP_META: Dict[str, Dict[str, str]] = {
    STEP_CODE: {
        "number": "1",
        "label": "Codice di configurazione",
        "description": "Inserisci il codice unico generato all'avvio",
        "icon": "🔑",
    },
    STEP_ADMIN: {
        "number": "2",
        "label": "Password amministratore",
        "description": "Crea la password per l'accesso alla dashboard",
        "icon": "👤",
    },
    STEP_TELEGRAM: {
        "number": "3",
        "label": "Token Telegram",
        "description": "Configura il token del bot Telegram",
        "icon": "✈️",
    },
    STEP_PROVIDER: {
        "number": "4",
        "label": "Provider AI",
        "description": "Connetti un servizio di intelligenza artificiale",
        "icon": "🤖",
    },
    STEP_CAPABILITIES: {
        "number": "5",
        "label": "Rilevamento modelli",
        "description": "Analisi delle capacità del provider",
        "icon": "🔍",
    },
    STEP_PIPELINE: {
        "number": "6",
        "label": "Modalità pipeline",
        "description": "Scegli come usare il provider",
        "icon": "⚙️",
    },
    STEP_VERIFY: {
        "number": "7",
        "label": "Verifica pipeline",
        "description": "Conferma la configurazione finale",
        "icon": "✅",
    },
    STEP_DONE: {
        "number": "8",
        "label": "Completato",
        "description": "Configurazione completata",
        "icon": "🎉",
    },
}

# ---------------------------------------------------------------------------
# Database key constants
# ---------------------------------------------------------------------------

_WIZARD_STEP = "wizard_step"
_WIZARD_TELEGRAM_TOKEN = "wizard_telegram_token"
_WIZARD_PROVIDER_TYPE = "wizard_provider_type"
_WIZARD_PROVIDER_API_KEY = "wizard_provider_api_key"
_WIZARD_PROVIDER_ENDPOINT = "wizard_provider_endpoint"
_WIZARD_PROVIDER_MODEL = "wizard_provider_model"
_WIZARD_CAPABILITIES = "wizard_capabilities"
_WIZARD_PIPELINE_MODE = "wizard_pipeline_mode"

# ---------------------------------------------------------------------------
# Public helpers — step metadata
# ---------------------------------------------------------------------------


def get_step_meta(step: str) -> Dict[str, str]:
    """Return metadata dict for a step identifier."""
    return _STEP_META.get(step, {"number": "?", "label": step, "description": "", "icon": "❓"})


def get_step_number(step: str) -> int:
    """Return the 1-based index of *step* in the wizard sequence."""
    try:
        return _STEP_ORDER.index(step) + 1
    except ValueError:
        return 0


def get_step_by_number(n: int) -> Optional[str]:
    """Return the step identifier at position *n* (1‑based), or ``None``."""
    if 1 <= n <= len(_STEP_ORDER):
        return _STEP_ORDER[n - 1]
    return None


def get_total_steps() -> int:
    """Return the total number of wizard steps."""
    return len(_STEP_ORDER)


def get_next_step(current: str) -> Optional[str]:
    """Return the step that follows *current*, or ``None``."""
    try:
        idx = _STEP_ORDER.index(current)
        if idx + 1 < len(_STEP_ORDER):
            return _STEP_ORDER[idx + 1]
        return None
    except ValueError:
        return None


def get_prev_step(current: str) -> Optional[str]:
    """Return the step preceding *current*, or ``None``."""
    try:
        idx = _STEP_ORDER.index(current)
        if idx > 0:
            return _STEP_ORDER[idx - 1]
        return None
    except ValueError:
        return None


def is_valid_step(step: str) -> bool:
    """Return ``True`` if *step* is a recognised wizard step."""
    return step in _STEP_ORDER


# ---------------------------------------------------------------------------
# Wizard progress — stored in ``setup_state`` table
# ---------------------------------------------------------------------------


def get_current_step(db: DatabaseManager) -> str:
    """Return the current wizard step identifier.

    Reads from the database; defaults to ``STEP_CODE`` when no progress
    has been saved.
    """
    step = db.get_setup_state(_WIZARD_STEP)
    if step and is_valid_step(step):
        return step
    return STEP_CODE


def set_current_step(db: DatabaseManager, step: str) -> None:
    """Persist the current wizard step."""
    if not is_valid_step(step):
        raise ValueError(f"Invalid wizard step: {step}")
    db.set_setup_state(_WIZARD_STEP, step)
    logger.debug("Wizard advanced to step %s", step)


def is_wizard_complete(db: DatabaseManager) -> bool:
    """Return ``True`` when the wizard has reached ``STEP_DONE``."""
    return get_current_step(db) == STEP_DONE


def reset_wizard(db: DatabaseManager) -> None:
    """Clear all wizard progress (for testing or re-setup)."""
    keys = [
        _WIZARD_STEP,
        _WIZARD_TELEGRAM_TOKEN,
        _WIZARD_PROVIDER_TYPE,
        _WIZARD_PROVIDER_API_KEY,
        _WIZARD_PROVIDER_ENDPOINT,
        _WIZARD_PROVIDER_MODEL,
        _WIZARD_CAPABILITIES,
        _WIZARD_PIPELINE_MODE,
    ]
    for key in keys:
        db.set_setup_state(key, "")
    logger.info("Wizard progress reset")


# ---------------------------------------------------------------------------
# Step data — individual field getters/setters
# ---------------------------------------------------------------------------


def save_telegram_token(db: DatabaseManager, token: str, secret_store: SecretStore | None) -> str:
    """Save the Telegram token (encrypted when a secret store is available).

    Returns the stored value (encrypted or plaintext).
    """
    stored = _encrypt_value(token, secret_store) if token else ""
    db.set_setup_state(_WIZARD_TELEGRAM_TOKEN, stored)
    return stored


def get_telegram_token(db: DatabaseManager, secret_store: SecretStore | None) -> str:
    """Return the saved Telegram token (decrypted when encrypted)."""
    raw = db.get_setup_state(_WIZARD_TELEGRAM_TOKEN) or ""
    return _decrypt_value(raw, secret_store) if raw else ""


def save_provider_config(
    db: DatabaseManager,
    provider_type: str,
    api_key: str,
    endpoint: str,
    secret_store: SecretStore | None,
) -> Dict[str, str]:
    """Save provider connection details.

    The API key is encrypted when a secret store is available.
    """
    stored_key = _encrypt_value(api_key, secret_store) if api_key else ""
    db.set_setup_state(_WIZARD_PROVIDER_TYPE, provider_type)
    db.set_setup_state(_WIZARD_PROVIDER_API_KEY, stored_key)
    db.set_setup_state(_WIZARD_PROVIDER_ENDPOINT, endpoint)
    return {"provider_type": provider_type, "endpoint": endpoint}


def get_provider_config(db: DatabaseManager, secret_store: SecretStore | None) -> Dict[str, str]:
    """Return saved provider configuration."""
    raw_key = db.get_setup_state(_WIZARD_PROVIDER_API_KEY) or ""
    return {
        "provider_type": db.get_setup_state(_WIZARD_PROVIDER_TYPE) or "",
        "api_key": _decrypt_value(raw_key, secret_store) if raw_key else "",
        "endpoint": db.get_setup_state(_WIZARD_PROVIDER_ENDPOINT) or "",
    }


def save_capabilities(db: DatabaseManager, capabilities: Dict[str, Any]) -> None:
    """Save detected provider capabilities as JSON."""
    db.set_setup_state(_WIZARD_CAPABILITIES, json.dumps(capabilities))


def get_capabilities(db: DatabaseManager) -> Dict[str, Any]:
    """Return saved capabilities, or an empty dict."""
    raw = db.get_setup_state(_WIZARD_CAPABILITIES)
    if raw:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def save_pipeline_mode(db: DatabaseManager, mode: str) -> None:
    """Save the pipeline mode (``"single"`` or ``"advanced"``)."""
    db.set_setup_state(_WIZARD_PIPELINE_MODE, mode)


def get_pipeline_mode(db: DatabaseManager) -> str:
    """Return the saved pipeline mode, defaulting to ``"single"``."""
    return db.get_setup_state(_WIZARD_PIPELINE_MODE) or "single"


def save_provider_model(db: DatabaseManager, model: str) -> None:
    """Save the selected model name."""
    db.set_setup_state(_WIZARD_PROVIDER_MODEL, model)


def get_provider_model(db: DatabaseManager) -> str:
    """Return the saved model name."""
    return db.get_setup_state(_WIZARD_PROVIDER_MODEL) or ""


# ---------------------------------------------------------------------------
# Wizard summary — build a summary dict from saved data
# ---------------------------------------------------------------------------


def build_summary(db: DatabaseManager, secret_store: SecretStore | None) -> Dict[str, Any]:
    """Build a summary dict of all wizard choices for the verify step."""
    provider_config = get_provider_config(db, secret_store)
    capabilities = get_capabilities(db)
    pipeline_mode = get_pipeline_mode(db)

    # Detect which capabilities are available
    can_transcribe = capabilities.get("transcription", False)
    can_refine = capabilities.get("text_generation", False) or capabilities.get("refinement", False)
    models = capabilities.get("models", [])

    # Active pipeline profile
    active_profile_id = get_active_pipeline_profile_id(db)

    # Pipeline description
    if pipeline_mode == "single":
        if can_transcribe and can_refine:
            pipeline_desc = "Trascrizione + Refinement automatico (stesso provider)"
        elif can_transcribe:
            pipeline_desc = "Solo trascrizione (refinement disabilitato)"
        else:
            pipeline_desc = "Pipeline non valida — il provider non supporta la trascrizione"
    else:
        pipeline_desc = "Modalità avanzata — selezione manuale dei provider per ogni fase"

    return {
        "telegram_token_set": bool(get_telegram_token(db, secret_store)),
        "provider": {
            "type": provider_config.get("provider_type", ""),
            "endpoint": provider_config.get("endpoint", ""),
            "model": get_provider_model(db),
        },
        "capabilities": {
            "transcription": can_transcribe,
            "refinement": can_refine,
            "models": models,
        },
        "pipeline_mode": pipeline_mode,
        "pipeline_description": pipeline_desc,
        "bot_ready": bool(get_telegram_token(db, secret_store)) and can_transcribe,
        "active_profile_id": active_profile_id,
    }


# ---------------------------------------------------------------------------
# Pipeline profile creation from wizard data (P5)
# ---------------------------------------------------------------------------

_ACTIVE_PROFILE_KEY = "active_pipeline_profile"


def get_active_pipeline_profile_id(db: DatabaseManager) -> int | None:
    """Return the active pipeline profile ID, or ``None``."""
    raw = db.get_setup_state(_ACTIVE_PROFILE_KEY)
    if raw:
        try:
            return int(raw)
        except (ValueError, TypeError):
            return None
    return None


def set_active_pipeline_profile_id(db: DatabaseManager, profile_id: int) -> None:
    """Persist the active pipeline profile ID."""
    db.set_setup_state(_ACTIVE_PROFILE_KEY, str(profile_id))
    logger.debug("Active pipeline profile set to id=%s", profile_id)


def create_pipeline_from_wizard(
    db: DatabaseManager,
    secret_store: SecretStore | None,
) -> int:
    """Create a provider connection and pipeline profile from wizard data.

    Reads the saved wizard configuration from ``setup_state``, creates a
    permanent provider connection in the database, and creates a pipeline
    profile that uses the same provider for both transcription and text
    processing (same-provider default).

    Returns the new pipeline profile ID.

    Raises
    ------
    ValueError
        When the wizard has incomplete provider data.
    """
    provider_config = get_provider_config(db, secret_store)
    ptype = provider_config.get("provider_type", "")
    api_key = provider_config.get("api_key", "")
    endpoint = provider_config.get("endpoint", "")
    model_name = get_provider_model(db)
    capabilities = get_capabilities(db)

    if not ptype or not api_key:
        raise ValueError(
            "Impossibile creare il profilo pipeline: dati del provider "
            "incompleti. Completa la configurazione del provider."
        )

    # Map wizard provider types to adapter types.
    _ADAPTER_MAP: dict[str, str] = {
        "openai": "openai-native",
        "gemini": "gemini-native",
        "openrouter": "openai-compat",
        "ollama": "openai-compat",
        "vllm": "openai-compat",
        "custom": "openai-compat",
    }
    adapter_type = _ADAPTER_MAP.get(ptype, ptype)

    # Determine provider display name.
    display_name = {
        "openai": "OpenAI (onboarding)",
        "gemini": "Gemini (onboarding)",
        "openrouter": "OpenRouter (onboarding)",
        "ollama": "Ollama (onboarding)",
        "vllm": "vLLM (onboarding)",
        "custom": "Endpoint personalizzato (onboarding)",
    }.get(ptype, f"{ptype} (onboarding)")

    # 1. Create the provider connection.
    provider_id = db.add_provider(
        name=display_name,
        adapter_type=adapter_type,
        endpoint=endpoint or None,
        credentials=api_key,
        capabilities={
            "transcription": capabilities.get("transcription", False),
            "refinement": capabilities.get("refinement", False)
            or capabilities.get("text_generation", False),
            "text_generation": capabilities.get("text_generation", False),
            "streaming_refinement": capabilities.get(
                "streaming_refinement", False
            ),
            "models": capabilities.get("models", []),
        },
        enabled=True,
    )
    logger.info(
        "Created provider connection '%s' (id=%s) from wizard",
        display_name,
        provider_id,
    )

    # 2. Create a same-provider default pipeline profile.
    pipeline_mode = get_pipeline_mode(db)
    profile_name = "Default (onboarding)"

    profile_id = db.add_pipeline_profile(
        name=profile_name,
        transcription_provider_id=provider_id,
        text_provider_id=provider_id,
    )
    logger.info(
        "Created pipeline profile '%s' (id=%s) from wizard "
        "(same-provider default)",
        profile_name,
        profile_id,
    )

    # 3. Save as the active profile.
    set_active_pipeline_profile_id(db, profile_id)

    return profile_id


def create_express_pipeline_from_wizard(
    db: DatabaseManager,
    secret_store: SecretStore | None,
    *,
    process_mode: str,
    selected_model: str,
    transcription_model: str = "whisper-1",
) -> int:
    """Create provider, model rows, and active profile for express setup.

    Unlike the legacy wizard helper, this creates explicit ``provider_models``
    and ``pipeline_stages`` so the resulting database state matches the
    advanced pipeline form.
    """
    provider_config = get_provider_config(db, secret_store)
    ptype = provider_config.get("provider_type", "")
    api_key = provider_config.get("api_key", "")
    endpoint = provider_config.get("endpoint", "")

    if not ptype or not api_key:
        raise ValueError(
            "Impossibile creare il profilo express: dati del provider "
            "incompleti."
        )
    if not selected_model:
        raise ValueError("Seleziona un modello per completare il setup express.")

    adapter_type = _adapter_type_for_provider_type(ptype)
    provider_id = db.add_provider(
        name=_display_name_for_provider_type(ptype),
        adapter_type=adapter_type,
        endpoint=endpoint or None,
        credentials=api_key,
        capabilities=get_capabilities(db) or detect_capabilities(
            adapter_type,
            selected_model,
        ).to_dict(),
        enabled=True,
    )

    if process_mode == "single_pass":
        model_entry_id = db.add_provider_model(
            provider_id=provider_id,
            model_id=selected_model,
            display_name=selected_model,
            capabilities=_model_capabilities(
                adapter_type,
                selected_model,
                force_single_pass=True,
            ),
            detected=True,
            enabled=True,
        )
        return create_single_pass_profile(
            db,
            model_id=model_entry_id,
            name="Express setup - singolo passaggio",
        )

    tx_entry_id = db.add_provider_model(
        provider_id=provider_id,
        model_id=transcription_model,
        display_name="Whisper",
        capabilities={
            "transcription": True,
            "text_generation": False,
            "refinement": False,
            "streaming_refinement": False,
            "single_pass_audio_to_text": False,
        },
        detected=True,
        enabled=True,
    )
    ref_entry_id = db.add_provider_model(
        provider_id=provider_id,
        model_id=selected_model,
        display_name=selected_model,
        capabilities=_model_capabilities(adapter_type, selected_model),
        detected=True,
        enabled=True,
    )
    return create_two_stage_profile(
        db,
        tx_model_id=tx_entry_id,
        ref_model_id=ref_entry_id,
        name="Express setup - due fasi",
    )


def _adapter_type_for_provider_type(provider_type: str) -> str:
    return {
        "openai": "openai-native",
        "gemini": "gemini-native",
        "openrouter": "openai-compat",
        "ollama": "openai-compat",
        "vllm": "openai-compat",
        "custom": "openai-compat",
    }.get(provider_type, provider_type)


def _display_name_for_provider_type(provider_type: str) -> str:
    return {
        "openai": "OpenAI (express setup)",
        "gemini": "Gemini (express setup)",
        "openrouter": "OpenRouter (express setup)",
        "ollama": "Ollama (express setup)",
        "vllm": "vLLM (express setup)",
        "custom": "Endpoint personalizzato (express setup)",
    }.get(provider_type, f"{provider_type} (express setup)")


def _model_capabilities(
    adapter_type: str,
    model_name: str,
    *,
    force_single_pass: bool = False,
) -> dict[str, bool]:
    caps = detect_capabilities(adapter_type, model_name).to_dict()
    if force_single_pass:
        caps.update({
            "transcription": True,
            "text_generation": True,
            "refinement": True,
            "single_pass_audio_to_text": True,
        })
    elif not caps.get("refinement") and not caps.get("text_generation"):
        caps.update({
            "text_generation": True,
            "refinement": True,
            "streaming_refinement": True,
        })
    return caps


# ---------------------------------------------------------------------------
# Internal helpers — encryption
# ---------------------------------------------------------------------------


def _encrypt_value(plaintext: str, secret_store: SecretStore | None) -> str:
    """Encrypt *plaintext* when a secret store is available."""
    if secret_store is not None and secret_store.key_available:
        try:
            return secret_store.encrypt(plaintext)
        except SecretStoreError:
            logger.exception("Encryption failed; storing plaintext")
    return plaintext


def _decrypt_value(ciphertext: str, secret_store: SecretStore | None) -> str:
    """Decrypt *ciphertext* when a secret store is available."""
    if secret_store is not None and secret_store.key_available:
        try:
            return secret_store.decrypt(ciphertext)
        except (SecretStoreError, Exception):
            logger.exception("Decryption failed; returning raw value")
    return ciphertext


# ---------------------------------------------------------------------------
# Provider endpoint defaults
# ---------------------------------------------------------------------------

PROVIDER_PRESETS: Dict[str, Dict[str, str]] = {
    "openai": {
        "label": "OpenAI",
        "default_endpoint": "https://api.openai.com/v1",
        "description": "Whisper per trascrizione + GPT per refinement",
    },
    "gemini": {
        "label": "Google Gemini",
        "default_endpoint": "",
        "description": "Gemini multimodale (trascrizione + refinement in unico passaggio)",
    },
    "openrouter": {
        "label": "OpenRouter",
        "default_endpoint": "https://openrouter.ai/api/v1",
        "description": "Accesso a modelli multipli tramite API unificata. "
                      "Attenzione: i modelli chat/testo non trascrivono audio. "
                      "Per trascrizione serve un modello speech-to-text (es. whisper-1).",
    },
    "ollama": {
        "label": "Ollama (locale)",
        "default_endpoint": "http://localhost:11434/v1",
        "description": "Modelli locali via Ollama",
    },
    "vllm": {
        "label": "vLLM (locale)",
        "default_endpoint": "http://localhost:8000/v1",
        "description": "Server di inferenza vLLM locale",
    },
    "custom": {
        "label": "Endpoint personalizzato",
        "default_endpoint": "",
        "description": "Endpoint compatibile con API OpenAI",
    },
}

"""
Runtime application state model.

Represents the application's readiness as one of six explicit states that
the frontend, audio handler, and runtime manager can query.

State machine (checked in priority order)::

    setup_required → telegram_missing → provider_missing
    → pipeline_invalid → degraded → ready

Usage::

    from bot.state import AppState, StateChecker

    checker = StateChecker(config_service, db_manager)
    info = checker.get_state()
    if not checker.can_process_audio():
        # reject the request with info.description
        ...
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from bot.capabilities import CapabilityModel
from bot.config import Config
from bot.config_service import ConfigService
from bot.database import DatabaseManager


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State enumeration
# ---------------------------------------------------------------------------


class AppState(str, Enum):
    """Explicit application readiness states."""

    SETUP_REQUIRED = "setup_required"
    TELEGRAM_MISSING = "telegram_missing"
    PROVIDER_MISSING = "provider_missing"
    PIPELINE_INVALID = "pipeline_invalid"
    READY = "ready"
    DEGRADED = "degraded"


# ---------------------------------------------------------------------------
# State information container
# ---------------------------------------------------------------------------


@dataclass
class StateInfo:
    """Full state description returned to consumers (frontend, CLI, etc.)."""

    state: AppState
    """Machine-readable state identifier."""

    label: str
    """Short human-readable label (Italian)."""

    description: str
    """Explanation of the current state in plain language."""

    next_action: str
    """Suggested next step for the user."""


# ---------------------------------------------------------------------------
# State description catalog (one entry per state)
# ---------------------------------------------------------------------------

_STATE_DESCRIPTIONS: Dict[AppState, Dict[str, str]] = {
    AppState.SETUP_REQUIRED: {
        "label": "Setup richiesto",
        "description": (
            "L'applicazione non è stata ancora configurata. "
            "Usa il codice di configurazione generato all'avvio "
            "per completare la procedura guidata."
        ),
        "next_action": (
            "Controlla i log del container per il codice di setup "
            "monouso, quindi apri l'interfaccia web per completare "
            "la configurazione."
        ),
    },
    AppState.TELEGRAM_MISSING: {
        "label": "Token Telegram mancante",
        "description": (
            "Il token del bot Telegram non è stato configurato. "
            "Il bot non può connettersi a Telegram senza un token valido."
        ),
        "next_action": (
            "Inserisci il token del bot Telegram ottenuto da @BotFather "
            "nella sezione delle impostazioni."
        ),
    },
    AppState.PROVIDER_MISSING: {
        "label": "Provider AI mancante",
        "description": (
            "Nessun provider AI è stato configurato. "
            "È necessario almeno un provider per la trascrizione audio."
        ),
        "next_action": (
            "Aggiungi un provider AI (OpenAI o Gemini) nella sezione "
            "delle connessioni."
        ),
    },
    AppState.PIPELINE_INVALID: {
        "label": "Pipeline non valida",
        "description": (
            "I provider configurati non supportano la trascrizione audio. "
            "La pipeline di elaborazione non può essere completata."
        ),
        "next_action": (
            "Verifica le capacità dei provider configurati o "
            "aggiungi un provider che supporti la trascrizione."
        ),
    },
    AppState.READY: {
        "label": "Pronto",
        "description": (
            "L'applicazione è configurata correttamente e pronta "
            "a ricevere messaggi audio."
        ),
        "next_action": (
            "Invia un messaggio audio per avviare la trascrizione."
        ),
    },
    AppState.DEGRADED: {
        "label": "Funzionamento degradato",
        "description": (
            "L'applicazione è in esecuzione ma alcune funzionalità "
            "potrebbero non essere disponibili. Verifica lo stato "
            "dei provider e delle connessioni."
        ),
        "next_action": (
            "Controlla il registro eventi per identificare "
            "eventuali problemi."
        ),
    },
}


# ---------------------------------------------------------------------------
# State checker
# ---------------------------------------------------------------------------


class StateChecker:
    """Evaluates the current application state by querying the database
    and configuration service.

    Parameters
    ----------
    config_service:
        Initialised :class:`~bot.config_service.ConfigService`.
    db_manager:
        Initialised :class:`~bot.database.DatabaseManager`.
    legacy_config:
        Optional legacy :class:`~bot.config.Config` object.  When provided
        **and** ``admin_created`` is absent from the unified database, the
        checker assumes a legacy ``.env`` + ``authorized.json`` deployment
        and reports ``READY`` instead of blocking audio processing.
    """

    def __init__(
        self,
        config_service: ConfigService,
        db_manager: DatabaseManager,
        legacy_config: Config | None = None,
    ):
        self._config_service = config_service
        self._db = db_manager
        self._legacy_config = legacy_config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_state(self) -> StateInfo:
        """Evaluate and return the current application state."""
        try:
            return self._evaluate()
        except Exception:
            logger.exception("Failed to evaluate application state")
            return self._build_info(
                AppState.DEGRADED,
            )

    def can_process_audio(self) -> bool:
        """Return ``True`` when the bot should accept audio messages.

        Audio is only accepted when the state is ``ready`` or, at most,
        ``degraded``.
        """
        state = self.get_state()
        return state.state in (AppState.READY, AppState.DEGRADED)

    # ------------------------------------------------------------------
    # Internal evaluation
    # ------------------------------------------------------------------

    def _evaluate(self) -> StateInfo:
        # 0. Legacy compatibility mode
        # When a legacy Config is available AND the unified database has not
        # yet recorded admin_created, we are running in legacy .env mode.
        # The legacy Config has already validated presence of Telegram token,
        # provider, and authorized.json — treat as READY.
        if self._legacy_config is not None:
            admin_created = self._db.get_setup_state("admin_created")
            if admin_created is None:
                return self._build_info(AppState.READY)

        # 1. Setup required
        admin_created = self._db.get_setup_state("admin_created")
        if admin_created is None:
            return self._build_info(AppState.SETUP_REQUIRED)

        # 2. Telegram token missing
        token_setting = self._config_service.get_setting("telegram_token")
        if token_setting is None or not token_setting.get("has_value"):
            return self._build_info(AppState.TELEGRAM_MISSING)

        # 3. Provider missing
        providers = self._db.list_providers()
        enabled_providers = [p for p in providers if p.get("enabled")]
        if not enabled_providers:
            return self._build_info(AppState.PROVIDER_MISSING)

        # 4. Pipeline invalid (no transcription capability)
        if not self._any_can_transcribe(enabled_providers):
            return self._build_info(AppState.PIPELINE_INVALID)

        # 5. (future) Check for degraded conditions

        return self._build_info(AppState.READY)

    @staticmethod
    def _any_can_transcribe(
        providers: List[Dict[str, Any]],
    ) -> bool:
        """Return ``True`` if at least one provider can transcribe audio.

        When a provider has no ``capabilities`` field (legacy entry) it is
        assumed to support transcription for backward compatibility.
        """
        for p in providers:
            caps_raw = p.get("capabilities")
            if caps_raw is None:
                return True  # assume transcription capable (legacy)
            model = CapabilityModel.from_dict(caps_raw)
            if model.transcription:
                return True
        return False

    @staticmethod
    def _build_info(state: AppState) -> StateInfo:
        desc = _STATE_DESCRIPTIONS.get(state, _STATE_DESCRIPTIONS[AppState.DEGRADED])
        return StateInfo(
            state=state,
            label=desc["label"],
            description=desc["description"],
            next_action=desc["next_action"],
        )

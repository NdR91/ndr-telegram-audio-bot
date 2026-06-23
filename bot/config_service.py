"""
Application configuration service.

Provides the single API for reading, validating, and updating application
settings.  Wraps the raw key-value database store with metadata, validation,
and secret masking.

Intended consumers
------------------
- Web frontend (settings administration page)
- Telegram admin commands
- Runtime manager (settings that require reload)
- CLI recovery / maintenance tools

Design decisions
----------------
- Every known setting is declared in ``SETTINGS_REGISTRY`` with metadata
  (key, label, type, default, scope, group, requires_reload, validation
  rules, secret flag).
- The service reads from the database and falls back to the built-in
  default when no DB value exists.
- Secret fields are **write-only**: the service never returns the actual
  plaintext value, only a ``has_value`` boolean.
- Bulk updates are validated first, then applied in a single database
  transaction.

Usage::

    from bot.config_service import ConfigService

    service = ConfigService(db_manager, secret_store)
    all_settings = service.get_all_settings()
    errors = service.update_setting("rate_limit_max_per_user", "3")
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from bot.database import DatabaseManager, SecretStore, SecretStoreError

# ---------------------------------------------------------------------------
# Setting metadata
# ---------------------------------------------------------------------------


@dataclass
class SettingDef:
    """Metadata definition for a single application setting."""

    # Identity
    key: str
    label: str
    description: str

    # Typing
    type: str  # "string" | "integer" | "boolean" | "secret" | "text" | "enum"
    default: Any = None

    # Classification
    scope: str = "application"  # "application" | "infrastructure"
    group: str = "general"

    # Behaviour
    requires_reload: bool = False
    is_secret: bool = False
    required: bool = False

    # Validation (type-specific)
    enum_values: Optional[List[str]] = None
    min_value: Optional[int] = None
    max_value: Optional[int] = None

    # UI hint
    placeholder: str = ""


# ---------------------------------------------------------------------------
# Settings registry
# ---------------------------------------------------------------------------

SETTINGS_REGISTRY: List[SettingDef] = [
    # ------ Telegram ------
    SettingDef(
        key="telegram_token",
        label="Telegram Bot Token",
        description="Il token del bot Telegram, ottenuto da @BotFather.",
        type="secret",
        group="telegram",
        requires_reload=True,
        is_secret=True,
        required=True,
        placeholder="Inserisci il token del bot…",
    ),
    # ------ Provider ------
    SettingDef(
        key="llm_provider",
        label="Provider AI",
        description="Provider per trascrizione e refinement del testo.",
        type="enum",
        default="openai",
        enum_values=["openai", "gemini"],
        group="provider",
    ),
    SettingDef(
        key="llm_model",
        label="Modello",
        description=(
            "Nome del modello da utilizzare (lasciare vuoto per usare il "
            "default del provider selezionato)."
        ),
        type="string",
        default=None,
        group="provider",
    ),
    # ------ Prompts ------
    SettingDef(
        key="prompt_system",
        label="Prompt di sistema",
        description=(
            "Prompt iniziale per il refinement. Il modello legge questo "
            "testo prima di elaborare la richiesta."
        ),
        type="text",
        default=(
            "Sei un esperto di trascrizione audio. Correggi errori automatici, "
            "aggiungi punteggiatura, mantieni il significato originale e "
            "restituisci SOLO il testo corretto senza commenti."
        ),
        group="prompts",
    ),
    SettingDef(
        key="prompt_refine_template",
        label="Template di refinement",
        description=(
            "Template per il refinement. Deve contenere il placeholder "
            "{raw_text} che verrà sostituito con la trascrizione grezza."
        ),
        type="text",
        default=(
            "Questo è un testo generato da una trascrizione automatica. "
            "Correggilo da eventuali errori, aggiungi la punteggiatura, "
            "riformula se ti rendi conto che la trascrizione è inaccurata, "
            "ma rimani il più aderente possibile al testo originale. "
            "Considera la presenza di eventuali esitazioni e ripetizioni, "
            "rendile adatte ad un testo scritto.\n"
            "IMPORTANTE: Restituisci SOLO il testo rielaborato. NON aggiungere "
            "commenti introduttivi, premesse o saluti.\n\n"
            "Testo originale:\n{raw_text}\n\nTesto rielaborato:\n"
        ),
        group="prompts",
    ),
    # ------ Rate limits ------
    SettingDef(
        key="rate_limit_max_per_user",
        label="Max richieste per utente",
        description="Numero massimo di elaborazioni simultanee per singolo utente.",
        type="integer",
        default=2,
        min_value=1,
        group="rate_limits",
    ),
    SettingDef(
        key="rate_limit_cooldown",
        label="Cooldown (secondi)",
        description="Tempo di attesa dopo che un utente ha raggiunto il limite.",
        type="integer",
        default=30,
        min_value=0,
        group="rate_limits",
    ),
    SettingDef(
        key="rate_limit_max_concurrent_global",
        label="Max richieste globali",
        description="Numero massimo di elaborazioni simultanee in tutto il bot.",
        type="integer",
        default=6,
        min_value=1,
        group="rate_limits",
    ),
    SettingDef(
        key="rate_limit_max_file_size_mb",
        label="Max dimensione file (MB)",
        description="Dimensione massima consentita per i file audio in megabyte.",
        type="integer",
        default=20,
        min_value=1,
        group="rate_limits",
    ),
    SettingDef(
        key="rate_limit_queue_enabled",
        label="Coda richieste",
        description=(
            "Accoda le richieste quando tutti gli slot globali sono "
            "occupati, invece di rifiutarle."
        ),
        type="boolean",
        default=True,
        group="rate_limits",
    ),
    SettingDef(
        key="rate_limit_max_queue_size",
        label="Max lunghezza coda",
        description="Numero massimo di richieste accodabili globalmente.",
        type="integer",
        default=10,
        min_value=0,
        group="rate_limits",
    ),
    SettingDef(
        key="rate_limit_max_queued_per_user",
        label="Max richieste in coda per utente",
        description="Numero massimo di richieste accodabili per singolo utente.",
        type="integer",
        default=1,
        min_value=1,
        group="rate_limits",
    ),
    # ------ Provider resilience ------
    SettingDef(
        key="provider_resilience_enabled",
        label="Resilienza provider",
        description=(
            "Attiva il circuit breaker per i provider API. Quando il "
            "circuito è aperto le richieste vengono rifiutate "
            "immediatamente invece di attendere un timeout."
        ),
        type="boolean",
        default=True,
        group="resilience",
    ),
    SettingDef(
        key="provider_resilience_failure_threshold",
        label="Soglia errori",
        description=(
            "Numero di errori consecutivi prima che il circuit breaker "
            "apra il circuito."
        ),
        type="integer",
        default=3,
        min_value=1,
        group="resilience",
    ),
    SettingDef(
        key="provider_resilience_cooldown_seconds",
        label="Cooldown resilienza (secondi)",
        description=(
            "Tempo di attesa prima di tentare una nuova richiesta dopo "
            "l'apertura del circuito."
        ),
        type="integer",
        default=60,
        min_value=0,
        group="resilience",
    ),
    # ------ Output ------
    SettingDef(
        key="telegram_draft_streaming",
        label="Streaming bozze Telegram",
        description=(
            "Invia i delta del refinement come bozze (solo chat private "
            "supportate). La risposta finale viene sempre inviata come "
            "messaggio normale."
        ),
        type="boolean",
        default=False,
        group="output",
    ),
    # ------ Infrastructure ------
    SettingDef(
        key="audio_cleanup_on_startup",
        label="Pulizia audio all'avvio",
        description="Rimuove i file audio temporanei all'avvio del bot.",
        type="boolean",
        default=True,
        scope="infrastructure",
        group="infrastructure",
    ),
]


def _registry_by_key() -> Dict[str, SettingDef]:
    """Build a lookup dict from ``SETTINGS_REGISTRY``."""
    return {s.key: s for s in SETTINGS_REGISTRY}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _def_to_dict(sd: SettingDef) -> Dict[str, Any]:
    """Convert a ``SettingDef`` to a plain dict suitable for serialisation."""
    d: Dict[str, Any] = {
        "key": sd.key,
        "label": sd.label,
        "description": sd.description,
        "type": sd.type,
        "default": sd.default,
        "scope": sd.scope,
        "group": sd.group,
        "requires_reload": sd.requires_reload,
        "is_secret": sd.is_secret,
        "required": sd.required,
    }
    if sd.enum_values is not None:
        d["enum_values"] = sd.enum_values
    if sd.min_value is not None:
        d["min_value"] = sd.min_value
    if sd.max_value is not None:
        d["max_value"] = sd.max_value
    if sd.placeholder:
        d["placeholder"] = sd.placeholder
    return d


def _cast_value(raw: Any, type_str: str) -> Any:
    """Cast a raw DB or default value to the proper Python type."""
    if raw is None:
        return None
    if type_str == "integer":
        return raw if isinstance(raw, int) else int(raw)
    if type_str == "boolean":
        if isinstance(raw, bool):
            return raw
        return raw.lower() in ("1", "true", "yes")
    if type_str in ("string", "secret", "text", "enum"):
        return str(raw)
    return raw


def _apply_value(entry: Dict[str, Any], sd: SettingDef, raw_value: Optional[str]) -> None:
    """Set the current value (or placeholder) on a setting entry dict.

    For secret fields the actual value is **never** returned: only a
    ``has_value`` boolean is set.
    """
    if sd.is_secret:
        entry["value"] = None
        entry["has_value"] = raw_value is not None and raw_value != ""
    else:
        actual = raw_value if raw_value is not None else sd.default
        entry["value"] = _cast_value(actual, sd.type)


def _validate(sd: SettingDef, value: str) -> List[str]:
    """Validate a value against the setting's rules.

    Returns a list of error messages.  An empty list means the value is
    valid.
    """
    errors: List[str] = []

    # Required check — short-circuit when empty.
    if sd.required and not value:
        errors.append(f"{sd.label} è obbligatorio.")
        return errors

    if not value and not sd.required:
        return errors  # empty optional = always valid

    # Type-specific validation.
    if sd.type == "integer":
        try:
            int_val = int(value)
        except (ValueError, TypeError):
            errors.append(f"{sd.label} deve essere un numero intero.")
            return errors
        if sd.min_value is not None and int_val < sd.min_value:
            errors.append(
                f"{sd.label} deve essere maggiore o uguale a {sd.min_value}."
            )
        if sd.max_value is not None and int_val > sd.max_value:
            errors.append(
                f"{sd.label} deve essere minore o uguale a {sd.max_value}."
            )

    elif sd.type == "boolean":
        if value.lower() not in ("1", "0", "true", "false", "yes", "no"):
            errors.append(
                f"{sd.label} deve essere uno di: 1, 0, true, false, yes, no."
            )

    elif sd.type == "enum":
        if value not in (sd.enum_values or []):
            valid = ", ".join(sd.enum_values or [])
            errors.append(f"{sd.label} deve essere uno di: {valid}.")

    elif sd.type == "text":
        if sd.key == "prompt_refine_template" and "{raw_text}" not in value:
            errors.append(
                f"{sd.label} deve contenere il placeholder {{{{raw_text}}}}."
            )

    return errors


def _prepare_stored_value(
    sd: SettingDef, value: str, secret_store: Optional[SecretStore]
) -> str:
    """Prepare a value for database storage.

    * Secret fields are encrypted when a ``secret_store`` is available.
    * Booleans are normalised to ``"1"`` / ``"0"``.
    """
    if sd.is_secret:
        if value:
            if secret_store is not None and secret_store.key_available:
                return secret_store.encrypt(value)
            # Callers must check via _check_secret_write before calling this.
            # If we reach here, encryption is unavailable for a non-empty secret.
            raise SecretStoreError(
                f"Cannot persist {sd.key}: encryption unavailable. "
                "Call _check_secret_write before _prepare_stored_value."
            )
        return value  # empty value: clear the stored secret

    if sd.type == "boolean":
        return "1" if value.lower() in ("1", "true", "yes") else "0"

    return value


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class ConfigService:
    """Single application API for reading, validating, and updating settings.

    Parameters
    ----------
    db_manager:
        Initialised :class:`~bot.database.DatabaseManager` instance.
    secret_store:
        Optional :class:`~bot.database.SecretStore` for encrypting secret
        fields at rest.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        secret_store: Optional[SecretStore] = None,
    ):
        self._db = db_manager
        self._secret_store = secret_store
        self._registry = _registry_by_key()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_def(self, key: str) -> Optional[SettingDef]:
        return self._registry.get(key)

    def _require_def(self, key: str) -> SettingDef:
        sd = self._get_def(key)
        if sd is None:
            raise ValueError(f"Setting sconosciuto: {key}")
        return sd

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def list_definitions(self) -> List[Dict[str, Any]]:
        """Return all setting definitions (metadata only, no values)."""
        return [_def_to_dict(sd) for sd in SETTINGS_REGISTRY]

    def get_all_settings(self) -> List[Dict[str, Any]]:
        """Return all settings with metadata and current values.

        Secret field values are masked (``value`` is always ``None``;
        check ``has_value`` to know whether one is stored).
        """
        db_values = self._db.get_all_settings() or {}
        result: List[Dict[str, Any]] = []
        for sd in SETTINGS_REGISTRY:
            entry = _def_to_dict(sd)
            _apply_value(entry, sd, db_values.get(sd.key))
            result.append(entry)
        return result

    def get_setting(self, key: str) -> Optional[Dict[str, Any]]:
        """Return a single setting with metadata and current value.

        Returns ``None`` when *key* is not in the registry.
        """
        sd = self._get_def(key)
        if sd is None:
            return None
        entry = _def_to_dict(sd)
        raw = self._db.get_setting(key)
        _apply_value(entry, sd, raw)
        return entry

    def get_settings_by_group(self) -> Dict[str, List[Dict[str, Any]]]:
        """Return settings grouped by their ``group`` field."""
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for setting in self.get_all_settings():
            group = setting.get("group", "general")
            groups.setdefault(group, []).append(setting)
        return groups

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_value(self, key: str, value: str) -> List[str]:
        """Validate a value against the setting's rules without saving.

        Returns a list of error messages.  An empty list means the value
        is valid.

        Raises ``ValueError`` when *key* is unknown.
        """
        sd = self._require_def(key)
        return _validate(sd, value)

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def update_setting(self, key: str, value: str) -> List[str]:
        """Validate and persist a single setting.

        Returns a list of error messages.  An empty list means the update
        succeeded.

        Raises ``ValueError`` when *key* is unknown.
        """
        errors = self.validate_value(key, value)
        if errors:
            return errors
        sd = self._require_def(key)

        # Reject secret writes when encryption is unavailable
        secret_err = self._check_secret_write(sd, value, self._secret_store)
        if secret_err:
            return [secret_err]

        stored = _prepare_stored_value(sd, value, self._secret_store)
        self._db.set_setting(key, stored)
        return []

    @staticmethod
    def _check_secret_write(
        sd: SettingDef, value: str, secret_store: SecretStore | None = None
    ) -> str | None:
        """Return an error message if a secret value cannot be safely persisted.

        Returns ``None`` when the write is safe (no secret, or encryption is
        available, or the value is empty).
        """
        if not sd.is_secret or not value:
            return None
        if secret_store is not None and secret_store.key_available:
            return None
        return (
            f"Impossibile salvare {sd.label}: la crittografia non è disponibile. "
            "Assicurati che il SecretStore sia configurato correttamente."
        )

    def update_settings(self, updates: Dict[str, str]) -> Dict[str, List[str]]:
        """Validate and persist multiple settings **transactionally**.

        All values are validated **first**.  If any value is invalid,
        **none** of the settings are written.

        Returns ``{key: [error_messages]}``.  An empty list per key means
        that setting was updated successfully.
        """
        # Phase 1 — validate everything.
        all_errors: Dict[str, List[str]] = {}
        sd_map: Dict[str, SettingDef] = {}
        for key, value in updates.items():
            try:
                sd = self._require_def(key)
            except ValueError as exc:
                all_errors[key] = [str(exc)]
                continue
            errors = _validate(sd, value)
            sd_map[key] = sd
            if errors:
                all_errors[key] = errors

        # Short-circuit on any validation error.
        if any(errors for errors in all_errors.values()):
            return all_errors

        # Phase 2 — reject secret writes when encryption is unavailable.
        for key, value in updates.items():
            sd = sd_map[key]
            secret_err = self._check_secret_write(sd, value, self._secret_store)
            if secret_err:
                all_errors[key] = [secret_err]

        if any(errors for errors in all_errors.values()):
            return all_errors

        # Phase 3 — prepare and persist in one transaction.
        stored: Dict[str, str] = {}
        for key, value in updates.items():
            sd = sd_map[key]
            stored[key] = _prepare_stored_value(sd, value, self._secret_store)

        self._db.set_settings(stored)

        return {key: [] for key in updates}

    # ------------------------------------------------------------------
    # Reload signalling
    # ------------------------------------------------------------------

    def get_reload_required(self, updates: Dict[str, str]) -> List[str]:
        """Return the keys of proposed changes that require a runtime reload.

        This is a dry-run check that does **not** persist anything.  The
        frontend can use it to warn the user before applying changes.
        """
        return [
            key
            for key in updates
            if (sd := self._get_def(key)) is not None and sd.requires_reload
        ]

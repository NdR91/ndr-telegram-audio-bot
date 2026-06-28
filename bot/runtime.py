"""
Runtime configuration snapshot and state management.

Provides :class:`RuntimeSnapshot` — an immutable snapshot of the resolved
runtime configuration, built from either the legacy ``Config`` object
(legacy mode) or the ``ConfigService`` (new control plane).

The snapshot ensures that in-flight requests keep using the configuration
they started with, even if settings are changed during processing.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from bot.config import Config
from bot.config_service import ConfigService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeSnapshot:
    """Immutable snapshot of the resolved runtime configuration.

    Parameters
    ----------
    provider_name:
        Active LLM provider name (e.g. ``"openai"``, ``"gemini"``).
    model_name:
        Optional model override (``None`` = provider default).
    api_key:
        API key for the active provider.
    prompts:
        ``{"system": ..., "refine_template": ...}`` prompt texts.
    rate_limit_config:
        Resolved rate-limit parameters matching the ``RateLimiter`` constructor.
    provider_resilience_config:
        Resolved provider-circuit-breaker parameters.
    telegram_progressive_output_config:
        Resolved Telegram progressive-delivery feature flags.
    audio_dir:
        Path to the temporary audio file directory.
    """

    provider_name: str
    model_name: Optional[str]
    api_key: str
    prompts: Dict[str, str]
    rate_limit_config: Dict[str, Any]
    provider_resilience_config: Dict[str, Any]
    telegram_progressive_output_config: Dict[str, Any]
    audio_dir: str

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_legacy_config(cls, config: Config) -> RuntimeSnapshot:
        """Build a snapshot from the legacy ``Config`` object (reads ``.env``).

        This is the default path during the migration period, where
        ``.env`` and ``authorized.json`` remain the primary configuration
        source.
        """
        return cls(
            provider_name=config.provider_name,
            model_name=config.model_name,
            api_key=config.get_api_key(config.provider_name),
            prompts=dict(config.prompts),
            rate_limit_config=dict(config.rate_limit_config),
            provider_resilience_config=dict(config.provider_resilience_config),
            telegram_progressive_output_config=dict(
                config.telegram_progressive_output_config
            ),
            audio_dir=config.audio_dir,
        )

    @classmethod
    def from_config_service(
        cls,
        config_service: ConfigService,
        config: Config | None = None,
    ) -> RuntimeSnapshot:
        """Build a snapshot from the ``ConfigService``, falling back to
        ``Config`` for values not yet managed by the new control plane.

        This is the path used once the new control plane is active and
        ``.env`` is no longer required.
        """
        db_provider = config_service._db.get_setting("llm_provider")
        provider_name = (
            db_provider
            if db_provider
            else (config.provider_name if config else "")
        )

        # API key — prefer DB-stored value (provider_connections), then Config.
        api_key = ""
        if provider_name:
            # Try provider_connections table first (credentials stored during setup).
            providers = config_service._db.list_providers()
            enabled = [p for p in providers if p.get("enabled") and p.get("credentials")]
            if enabled:
                api_key = enabled[0].get("credentials", "")
        if not api_key and config is not None:
            try:
                api_key = config.get_api_key(provider_name)
            except Exception:
                pass

        db_model = config_service._db.get_setting("llm_model")
        model_name = (
            db_model
            if db_model is not None
            else (config.model_name if config else None)
        )

        # Prompts — prefer DB-stored value, fall back to Config
        prompts: Dict[str, str] = {}
        for setting_key, prompt_key in [
            ("prompt_system", "system"),
            ("prompt_refine_template", "refine_template"),
        ]:
            db_value = config_service._db.get_setting(setting_key)
            if db_value is not None:
                prompts[prompt_key] = db_value
            elif config is not None:
                prompts[prompt_key] = config.prompts.get(prompt_key, "")

        # Rate limits
        rate_limits = cls._resolve_rate_limits(config_service, config)

        # Resilience
        resilience = cls._resolve_resilience(config_service, config)

        # Telegram progressive output — prefer DB value over Config
        db_streaming = config_service._db.get_setting("telegram_draft_streaming")
        if db_streaming is not None:
            streaming_enabled = db_streaming.lower() in ("1", "true", "yes")
        elif config is not None:
            streaming_enabled = config.telegram_progressive_output_config.get(
                "enabled", False
            )
        else:
            streaming_enabled = False
        telegram_progressive_output = {"enabled": streaming_enabled}

        # Audio dir — prefer Config, fallback to env/default
        if config is not None:
            audio_dir = config.audio_dir
        else:
            audio_dir = os.getenv("AUDIO_DIR", "audio_files")

        return cls(
            provider_name=provider_name,
            model_name=model_name,
            api_key=api_key,
            prompts=prompts,
            rate_limit_config=rate_limits,
            provider_resilience_config=resilience,
            telegram_progressive_output_config=telegram_progressive_output,
            audio_dir=audio_dir,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_rate_limits(
        config_service: ConfigService,
        config: Config | None = None,
    ) -> Dict[str, Any]:
        """Resolve rate-limit settings from ConfigService or Config."""
        result: Dict[str, Any] = {}

        int_keys = [
            ("rate_limit_max_per_user", "max_per_user", 2),
            ("rate_limit_cooldown", "cooldown_seconds", 30),
            ("rate_limit_max_concurrent_global", "max_concurrent_global", 6),
            ("rate_limit_max_file_size_mb", "max_file_size_mb", 20),
            ("rate_limit_max_queue_size", "max_queue_size", 10),
            ("rate_limit_max_queued_per_user", "max_queued_per_user", 1),
        ]
        for key, attr, default in int_keys:
            db_val = config_service._db.get_setting(key)
            if db_val is not None:
                result[attr] = int(db_val)
            elif config is not None:
                result[attr] = config.rate_limit_config.get(attr, default)
            else:
                result[attr] = default

        bool_keys = [
            ("rate_limit_queue_enabled", "queue_enabled", True),
        ]
        for key, attr, default in bool_keys:
            db_val = config_service._db.get_setting(key)
            if db_val is not None:
                result[attr] = db_val.lower() in ("1", "true", "yes")
            elif config is not None:
                result[attr] = config.rate_limit_config.get(attr, default)
            else:
                result[attr] = default

        return result

    @staticmethod
    def _resolve_resilience(
        config_service: ConfigService,
        config: Config | None = None,
    ) -> Dict[str, Any]:
        """Resolve provider-resilience settings from ConfigService or Config."""
        result: Dict[str, Any] = {}

        bool_keys = [("provider_resilience_enabled", "enabled", True)]
        for key, attr, default in bool_keys:
            db_val = config_service._db.get_setting(key)
            if db_val is not None:
                result[attr] = db_val.lower() in ("1", "true", "yes")
            elif config is not None:
                result[attr] = config.provider_resilience_config.get(attr, default)
            else:
                result[attr] = default

        int_keys = [
            ("provider_resilience_failure_threshold", "failure_threshold", 3),
            ("provider_resilience_cooldown_seconds", "cooldown_seconds", 60),
        ]
        for key, attr, default in int_keys:
            db_val = config_service._db.get_setting(key)
            if db_val is not None:
                result[attr] = int(db_val)
            elif config is not None:
                result[attr] = config.provider_resilience_config.get(attr, default)
            else:
                result[attr] = default

        return result

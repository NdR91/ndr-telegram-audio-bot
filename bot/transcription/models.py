"""Immutable value objects shared by V2 application services."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal


ServiceId = Literal["openai", "gemini", "openrouter"]
CredentialOwner = Literal[
    "telegram", "web_session", "openai", "gemini", "openrouter"
]
Mode = Literal["multimodal", "two_stage"]
ModelRole = Literal["multimodal", "transcription", "refinement"]


@dataclass(frozen=True)
class RuntimeLimits:
    max_per_user: int = 2
    cooldown_seconds: int = 30
    max_concurrent_global: int = 6
    max_file_size_mb: int = 20
    queue_enabled: bool = True
    max_queue_size: int = 10
    max_queued_per_user: int = 1
    download_timeout: int = 30
    convert_timeout: int = 60
    transcribe_timeout: int = 120
    refine_timeout: int = 90
    resilience_enabled: bool = True
    failure_threshold: int = 3
    breaker_cooldown_seconds: int = 60
    shutdown_seconds: int = 30


@dataclass(frozen=True)
class ConfigInput:
    service: ServiceId
    mode: Mode
    audio_model: str
    text_model: str | None
    credential_version: int
    system_prompt: str
    refine_template: str
    limits: RuntimeLimits


@dataclass(frozen=True)
class ConfigRevision:
    number: int
    values: ConfigInput


@dataclass(frozen=True)
class Prerequisites:
    web_admin: bool
    telegram_valid: bool
    owner: bool


@dataclass(frozen=True)
class StageResult:
    text: str
    model: str
    cost: Decimal | None = None


@dataclass(frozen=True)
class ProcessingResult:
    text: str
    partial: bool
    models: tuple[str, ...]
    duration_ms: int
    cost: Decimal | None = None


@dataclass(frozen=True)
class TestReceipt:
    id: str
    revision: int
    credential_version: int
    validator_version: str
    success: bool
    models: tuple[str, ...]
    finished_at: datetime


@dataclass(frozen=True)
class ModelChoice:
    id: str
    label: str
    roles: frozenset[ModelRole]
    formats: frozenset[str]

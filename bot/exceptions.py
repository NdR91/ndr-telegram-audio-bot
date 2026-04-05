"""
Custom exceptions for the Telegram Audio Bot.

This module defines specific exception types for different categories of errors
that can occur during bot operation and configuration.
"""


class ConfigError(Exception):
    """Base configuration error for the bot."""
    pass


class MissingRequiredConfig(ConfigError):
    """Raised when a required configuration parameter is missing."""
    pass


class InvalidConfig(ConfigError):
    """Raised when a configuration parameter has an invalid value."""
    pass


class ExternalDependencyError(ConfigError):
    """Raised when an external dependency (like FFmpeg) is not available."""
    pass


class APIProviderError(ConfigError):
    """Raised when there's an issue with API provider configuration."""
    pass


class AudioPipelineError(Exception):
    """Base exception for audio pipeline failures."""

    def __init__(self, message: str, user_message: str):
        super().__init__(message)
        self.user_message = user_message


class AudioPipelineTimeout(AudioPipelineError):
    """Raised when a pipeline stage exceeds its timeout."""


class AudioPipelineStageError(AudioPipelineError):
    """Raised when a pipeline stage fails for a non-timeout reason."""


class DownloadTimeout(AudioPipelineTimeout):
    """Raised when audio download times out."""


class ConvertTimeout(AudioPipelineTimeout):
    """Raised when audio conversion times out."""


class TranscribeTimeout(AudioPipelineTimeout):
    """Raised when transcription times out."""


class RefineTimeout(AudioPipelineTimeout):
    """Raised when text refinement times out."""


class DownloadError(AudioPipelineStageError):
    """Raised when audio download fails."""


class ConvertError(AudioPipelineStageError):
    """Raised when audio conversion fails."""


class TranscribeError(AudioPipelineStageError):
    """Raised when transcription fails."""


class RefineError(AudioPipelineStageError):
    """Raised when refinement fails."""

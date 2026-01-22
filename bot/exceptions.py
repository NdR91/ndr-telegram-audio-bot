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
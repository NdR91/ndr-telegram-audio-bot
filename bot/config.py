"""
Centralized configuration management for the Telegram Audio Bot.

This module handles loading and validating all configuration parameters
required for bot operation, including API keys, paths, and prompts.
"""

import os
import json
import logging
import subprocess
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()
from bot.exceptions import (
    ConfigError,
    MissingRequiredConfig,
    InvalidConfig,
    ExternalDependencyError,
    APIProviderError
)

logger = logging.getLogger(__name__)


class Config:
    """Centralized configuration class with comprehensive validation."""
    
    def __init__(self):
        """Initialize and validate all configuration parameters."""
        self.telegram_token = self._validate_telegram_token()
        self.provider_name = self._validate_provider_name()
        self.api_keys = self._validate_api_keys()
        self.model_name = self._get_model_name()
        self.authorized_file = self._validate_authorized_file_path()
        self.audio_dir = self._validate_audio_dir()
        self.rate_limit_config = self._load_rate_limit_config()
        self.prompts = self._load_prompts()
        self.authorized_data = self._load_authorized_data()
        self._validate_ffmpeg()
        
        logger.info(f"Configuration loaded successfully for provider: {self.provider_name}")
    
    def _validate_telegram_token(self) -> str:
        """Validate Telegram bot token is present."""
        token = os.getenv('TELEGRAM_TOKEN')
        if not token:
            raise MissingRequiredConfig(
                "TELEGRAM_TOKEN is required. Set it in your .env file. "
                "Get your token from @BotFather on Telegram."
            )
        return token
    
    def _validate_provider_name(self) -> str:
        """Validate LLM provider name."""
        provider = os.getenv('LLM_PROVIDER', 'openai').lower()
        valid_providers = ['openai', 'gemini']
        
        if provider not in valid_providers:
            raise InvalidConfig(
                f"LLM_PROVIDER must be one of {valid_providers}. "
                f"Got: '{provider}'"
            )
        return provider
    
    def _validate_api_keys(self) -> Dict[str, str]:
        """Validate API key for the selected provider is present."""
        if self.provider_name == 'openai':
            api_key = os.getenv('OPENAI_API_KEY')
            if not api_key:
                raise MissingRequiredConfig(
                    "OPENAI_API_KEY is required when LLM_PROVIDER is 'openai'. "
                    "Get your key from https://platform.openai.com/api-keys"
                )
            return {'openai': api_key}
        
        elif self.provider_name == 'gemini':
            api_key = os.getenv('GEMINI_API_KEY')
            if not api_key:
                raise MissingRequiredConfig(
                    "GEMINI_API_KEY is required when LLM_PROVIDER is 'gemini'. "
                    "Get your key from https://makersuite.google.com/app/apikey"
                )
            return {'gemini': api_key}
        
        else:
            # This should never happen due to _validate_provider_name
            raise InvalidConfig(f"Unknown provider: {self.provider_name}")
    
    def _get_model_name(self) -> Optional[str]:
        """Get optional model name from environment."""
        model_name = os.getenv('LLM_MODEL')
        if model_name:
            logger.info(f"Using custom model: {model_name}")
        return model_name
    
    def _validate_authorized_file_path(self) -> str:
        """Validate authorized.json file path and existence."""
        file_path = os.getenv('AUTHORIZED_FILE', 'authorized.json')
        
        if not os.path.exists(file_path):
            raise MissingRequiredConfig(
                f"Authorized file '{file_path}' not found. "
                "Create it with at least one admin user ID."
            )
        
        return file_path
    
    def _validate_audio_dir(self) -> str:
        """Validate audio directory exists or can be created."""
        dir_path = os.getenv('AUDIO_DIR', 'audio_files')
        
        if not os.path.exists(dir_path):
            try:
                os.makedirs(dir_path, exist_ok=True)
                logger.info(f"Created audio directory: {dir_path}")
            except OSError as e:
                raise ConfigError(
                    f"Cannot create audio directory '{dir_path}': {e}"
                )
        
        # Test write permissions
        if not os.access(dir_path, os.W_OK):
            raise ConfigError(
                f"Audio directory '{dir_path}' is not writable"
            )
        
        return dir_path
    
    def _load_rate_limit_config(self) -> Dict[str, int]:
        """Load rate limit configuration from env or defaults."""
        from bot import constants as c
        defaults = c.RATE_LIMIT_DEFAULTS
        
        return {
            "max_per_user": int(os.getenv('RATE_LIMIT_PER_USER', str(defaults["max_per_user"]))),
            "cooldown_seconds": int(os.getenv('RATE_LIMIT_COOLDOWN', str(defaults["cooldown_seconds"]))),
            "max_concurrent_global": int(os.getenv('RATE_LIMIT_GLOBAL', str(defaults["max_concurrent_global"]))),
            "max_file_size_mb": int(os.getenv('RATE_LIMIT_FILE_SIZE', str(defaults["max_file_size_mb"])))
        }
    
    def _load_prompts(self) -> Dict[str, str]:
        """Load and validate prompt templates."""
        default_system = (
            "Sei un esperto di trascrizione audio. Correggi errori automatici, aggiungi punteggiatura, "
            "mantieni il significato originale e restituisci SOLO il testo corretto senza commenti."
        )
        
        default_refine_template = (
            "Questo è un testo generato da una trascrizione automatica. Correggilo da eventuali errori, "
            "aggiungi la punteggiatura, riformula se ti rendi conto che la trascrizione è inaccurata, "
            "ma rimani il più aderente possibile al testo originale. Considera la presenza di eventuali "
            "esitazioni e ripetizioni, rendile adatte ad un testo scritto.\n"
            "IMPORTANTE: Restituisci SOLO il testo rielaborato. NON aggiungere commenti introduttivi, "
            "premese o saluti.\n\n"
            "Testo originale:\n{raw_text}\n\nTesto rielaborato:\n"
        )
        
        system_prompt = os.getenv('PROMPT_SYSTEM', default_system)
        refine_template = os.getenv('PROMPT_REFINE_TEMPLATE', default_refine_template)
        
        # Validate that refine template contains the required placeholder
        if '{raw_text}' not in refine_template:
            raise InvalidConfig(
                "PROMPT_REFINE_TEMPLATE must contain '{raw_text}' placeholder. "
                "Current template is missing this required placeholder."
            )
        
        return {
            'system': system_prompt,
            'refine_template': refine_template
        }
    
    def _load_authorized_data(self) -> Dict[str, Any]:
        """Load and validate authorized.json structure."""
        try:
            with open(self.authorized_file, 'r') as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise InvalidConfig(
                f"Authorized file '{self.authorized_file}' contains invalid JSON: {e}"
            )
        except Exception as e:
            raise ConfigError(
                f"Error reading authorized file '{self.authorized_file}': {e}"
            )
        
        # Validate structure
        if not isinstance(data, dict):
            raise InvalidConfig(
                "Authorized file must contain a JSON object with 'admin', 'users', and 'groups' arrays"
            )
        
        # Ensure required sections exist
        for key in ['admin', 'users', 'groups']:
            if key not in data:
                data[key] = []
            elif not isinstance(data[key], list):
                raise InvalidConfig(
                    f"'{key}' in authorized file must be an array"
                )
        
        # Validate that at least one admin is configured
        if not data['admin']:
            logger.warning(
                "No admin users configured. You won't be able to manage user permissions. "
                "Add at least one admin user ID to the 'admin' array in authorized.json"
            )
        
        # Validate that all IDs are integers
        for category in ['admin', 'users', 'groups']:
            for i, user_id in enumerate(data[category]):
                if not isinstance(user_id, int):
                    try:
                        # Try to convert string to int
                        data[category][i] = int(user_id)
                    except (ValueError, TypeError):
                        raise InvalidConfig(
                            f"Invalid user ID '{user_id}' in '{category}' array. "
                            "User IDs must be integers."
                        )
        
        return data
    
    def _validate_ffmpeg(self) -> None:
        """Validate that FFmpeg is available on the system PATH."""
        try:
            result = subprocess.run(
                ['ffmpeg', '-version'],
                capture_output=True,
                text=True,
                timeout=10  # 10 second timeout
            )
            if result.returncode != 0:
                raise ExternalDependencyError(
                    "FFmpeg is not working correctly. Please ensure it's properly installed."
                )
            logger.debug("FFmpeg validation passed")
        except subprocess.TimeoutExpired:
            raise ExternalDependencyError(
                "FFmpeg command timed out. Please check your system configuration."
            )
        except FileNotFoundError:
            raise ExternalDependencyError(
                "FFmpeg is not installed or not in PATH. "
                "Install it with: apt-get install ffmpeg (Ubuntu/Debian) or brew install ffmpeg (macOS)"
            )
        except Exception as e:
            raise ExternalDependencyError(
                f"Unexpected error validating FFmpeg: {e}"
            )
    
    def get_api_key(self, provider: str = None) -> str:
        """Get API key for specified provider or current provider."""
        provider = provider or self.provider_name
        if provider not in self.api_keys:
            raise APIProviderError(f"No API key available for provider: {provider}")
        return self.api_keys[provider]
import subprocess
import logging
from bot.providers import OpenAIProvider, GeminiProvider, LLMProvider

logger = logging.getLogger(__name__)

def convert_to_mp3(src_path: str, dst_path: str) -> None:
    logger.info(f"Convert {src_path} → {dst_path}")
    try:
        subprocess.run(
            ['ffmpeg','-y','-i',src_path,'-vn','-ar','44100','-ac','2','-b:a','192k',dst_path],
            check=True, capture_output=True, text=True
        )
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg error: {e.stderr}")
        raise RuntimeError("Errore conversione audio")

_provider_instance = None

def get_provider(config) -> LLMProvider:
    """Factory function to get the configured LLM provider."""
    global _provider_instance
    if _provider_instance:
        return _provider_instance

    provider_name = config.provider_name
    model_name = config.model_name
    api_key = config.get_api_key(provider_name)
    prompts = config.prompts
    
    if provider_name == 'openai':
        logger.info(f"Initializing OpenAI Provider (model: {model_name or 'default'})")
        _provider_instance = OpenAIProvider(api_key, model_name, prompts)
    elif provider_name == 'gemini':
        logger.info(f"Initializing Gemini Provider (model: {model_name or 'default'})")
        _provider_instance = GeminiProvider(api_key, model_name, prompts)
    else:
        raise ValueError(f"Provider sconosciuto: {provider_name}")
    
    return _provider_instance
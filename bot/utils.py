import os
import subprocess
import logging
from providers import OpenAIProvider, GeminiProvider, LLMProvider

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

def get_provider() -> LLMProvider:
    """Factory function to get the configured LLM provider."""
    global _provider_instance
    if _provider_instance:
        return _provider_instance

    provider_name = os.getenv('LLM_PROVIDER', 'openai').lower()
    model_name = os.getenv('LLM_MODEL') # Default gestito dai provider se None
    
    if provider_name == 'openai':
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY configurata ma mancante.")
        logger.info(f"Initializing OpenAI Provider (model: {model_name or 'default'})")
        # Se model_name è None, usa il default della classe
        if model_name:
             _provider_instance = OpenAIProvider(api_key, model_name)
        else:
             _provider_instance = OpenAIProvider(api_key)

    elif provider_name == 'gemini':
        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY configurata ma mancante.")
        logger.info(f"Initializing Gemini Provider (model: {model_name or 'default'})")
        if model_name:
            _provider_instance = GeminiProvider(api_key, model_name)
        else:
            _provider_instance = GeminiProvider(api_key)
    else:
        raise ValueError(f"Provider sconosciuto: {provider_name}")
    
    return _provider_instance
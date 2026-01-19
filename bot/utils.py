import os
import subprocess
import logging
from providers import OpenAIProvider, LLMProvider

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
    
    if provider_name == 'openai':
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY configurata ma mancante.")
        logger.info("Initializing OpenAI Provider")
        _provider_instance = OpenAIProvider(api_key)
    else:
        raise ValueError(f"Provider sconosciuto: {provider_name}")
    
    return _provider_instance
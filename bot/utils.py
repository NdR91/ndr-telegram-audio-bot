import subprocess
import logging
import os
import glob
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

def create_provider(config) -> LLMProvider:
    """Factory function to create the configured LLM provider."""
    provider_name = config.provider_name
    model_name = config.model_name
    api_key = config.get_api_key(provider_name)
    prompts = config.prompts
    
    if provider_name == 'openai':
        logger.info(f"Initializing OpenAI Provider (model: {model_name or 'default'})")
        return OpenAIProvider(api_key, model_name, prompts)
    elif provider_name == 'gemini':
        logger.info(f"Initializing Gemini Provider (model: {model_name or 'default'})")
        return GeminiProvider(api_key, model_name, prompts)
    else:
        raise ValueError(f"Provider sconosciuto: {provider_name}")

def cleanup_audio_directory(dir_path: str) -> None:
    """
    Clean up all files in the audio directory on startup.
    This ensures no leftover files from previous crashed runs consume disk space.
    """
    if not os.path.exists(dir_path):
        return
        
    logger.info(f"Cleaning up audio directory: {dir_path}")
    try:
        # Remove all files in the directory
        files = glob.glob(os.path.join(dir_path, "*"))
        count = 0
        for f in files:
            if os.path.isfile(f):
                try:
                    os.remove(f)
                    count += 1
                except Exception as e:
                    logger.warning(f"Failed to delete {f}: {e}")
        
        if count > 0:
            logger.info(f"Cleaned up {count} leftover files")
            
    except Exception as e:
        logger.error(f"Error cleaning audio directory: {e}")
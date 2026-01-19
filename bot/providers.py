import os
import logging
from abc import ABC, abstractmethod
from openai import OpenAI
import constants as c

logger = logging.getLogger(__name__)

class LLMProvider(ABC):
    """Abstract Base Class for LLM Providers."""

    @abstractmethod
    def transcribe_audio(self, file_path: str) -> str:
        """Transcribes an audio file to text."""
        pass

    @abstractmethod
    def refine_text(self, raw_text: str) -> str:
        """Refines the text using an LLM."""
        pass

class OpenAIProvider(LLMProvider):
    """OpenAI Implementation."""

    def __init__(self, api_key: str):
        self.client = OpenAI(api_key=api_key)

    def transcribe_audio(self, file_path: str) -> str:
        logger.info(f"Transcribe {file_path} with Whisper v1")
        with open(file_path, 'rb') as audio:
            transcription = self.client.audio.transcriptions.create(
                model="whisper-1",
                file=audio,
                temperature=0
            )
        text = transcription.text
        logger.debug(f"Raw text: {text}")
        return text

    def refine_text(self, raw_text: str) -> str:
        logger.info("Refine text with ChatCompletion")
        prompt = c.PROMPT_REFINE_TEMPLATE.format(raw_text=raw_text)

        resp = self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": c.PROMPT_SYSTEM},
                {"role": "user", "content": prompt}
            ],
            max_tokens=4096, 
            temperature=0.7
        )
        out = resp.choices[0].message.content.strip()
        logger.debug(f"Refined text: {out}")
        return out

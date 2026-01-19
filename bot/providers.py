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

    def __init__(self, api_key: str, model_name: str = "gpt-4o-mini"):
        self.client = OpenAI(api_key=api_key)
        self.model_name = model_name

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
            model=self.model_name,
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

import google.generativeai as genai
import time

class GeminiProvider(LLMProvider):
    """Google Gemini Implementation."""

    def __init__(self, api_key: str, model_name: str = "gemini-1.5-flash"):
        genai.configure(api_key=api_key)
        self.model_name = model_name
        self.model = genai.GenerativeModel(self.model_name)

    def transcribe_audio(self, file_path: str) -> str:
        logger.info(f"Transcribe {file_path} with Gemini")
        
        # Carica il file su Google AI Studio
        audio_file = genai.upload_file(path=file_path)
        
        # Attendi che il file sia processato (stato ACTIVE)
        while audio_file.state.name == "PROCESSING":
            time.sleep(1)
            audio_file = genai.get_file(audio_file.name)

        if audio_file.state.name == "FAILED":
            raise RuntimeError("Google AI File Upload failed.")

        # Richiedi la trascrizione
        prompt = "Transcribe this audio file accurately. Output only the text."
        response = self.model.generate_content([prompt, audio_file])
        
        # Cleanup file remoto (opzionale ma consigliato per non intasare lo storage)
        # genai.delete_file(audio_file.name) 
        # Nota: genai.delete_file non è sempre esposto direttamente o necessario se si usa il tier free, 
        # ma è buona norma. Per ora lo lasciamo commentato per sicurezza API.
        
        text = response.text
        logger.debug(f"Gemini Raw text: {text}")
        return text

    def refine_text(self, raw_text: str) -> str:
        logger.info("Refine text with Gemini")
        # Combina system prompt e user prompt perché Gemini usa un array di content
        full_prompt = f"{c.PROMPT_SYSTEM}\n\n{c.PROMPT_REFINE_TEMPLATE.format(raw_text=raw_text)}"
        
        response = self.model.generate_content(full_prompt)
        out = response.text.strip()
        logger.debug(f"Gemini Refined text: {out}")
        return out

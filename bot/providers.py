import logging
import asyncio
from abc import ABC, abstractmethod
from openai import OpenAI
import google.genai as genai

logger = logging.getLogger(__name__)

class LLMProvider(ABC):
    """Abstract Base Class for LLM Providers."""

    @abstractmethod
    async def transcribe_audio(self, file_path: str) -> str:
        """Transcribes an audio file to text."""
        pass

    @abstractmethod
    async def refine_text(self, raw_text: str) -> str:
        """Refines the text using an LLM."""
        pass

class OpenAIProvider(LLMProvider):
    """OpenAI Implementation."""

    def __init__(self, api_key: str, model_name: str = "gpt-4o-mini", prompts: dict | None = None):
        self.client = OpenAI(api_key=api_key)
        self.model_name = model_name
        self.prompts = prompts or {
            'system': "Sei un esperto di trascrizione audio. Correggi errori automatici, aggiungi punteggiatura, mantieni il significato originale e restituisci SOLO il testo corretto senza commenti.",
            'refine_template': "Questo è un testo generato da una trascrizione automatica. Correggilo da eventuali errori, aggiungi la punteggiatura, riformula se ti rendi conto che la trascrizione è inaccurate, ma rimani il più aderente possibile al testo originale. Considera la presenza di eventuali esitazioni e ripetizioni, rendile adatte ad un testo scritto.\nIMPORTANTE: Restituisci SOLO il testo rielaborato. NON aggiungere commenti introduttivi, premese o saluti.\n\nTesto originale:\n{raw_text}\n\nTesto rielaborato:\n"
        }

    async def transcribe_audio(self, file_path: str) -> str:
        logger.info(f"Transcribe {file_path} with Whisper v1")
        
        def _sync_transcribe():
            with open(file_path, 'rb') as audio:
                return self.client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio,
                    temperature=0
                )

        transcription = await asyncio.to_thread(_sync_transcribe)
        text = transcription.text
        logger.debug(f"Raw text: {text}")
        return text

    async def refine_text(self, raw_text: str) -> str:
        logger.info("Refine text with ChatCompletion")
        prompt = self.prompts['refine_template'].format(raw_text=raw_text)

        def _sync_completion():
            return self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": self.prompts['system']},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=4096, 
                temperature=0.7
            )

        resp = await asyncio.to_thread(_sync_completion)
        response_content = resp.choices[0].message.content
        out = response_content.strip() if response_content else ""
        logger.debug(f"Refined text: {out}")
        return out

class GeminiProvider(LLMProvider):
    """Google Gemini Implementation using new google-genai SDK."""

    def __init__(self, api_key: str, model_name: str = "gemini-2.0-flash", prompts: dict | None = None):
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name
        self.prompts = prompts or {
            'system': "Sei un esperto di trascrizione audio. Correggi errori automatici, aggiungi punteggiatura, mantieni il significato originale e restituisci SOLO il testo corretto senza commenti.",
            'refine_template': "Questo è un testo generato da una trascrizione automatica. Correggilo da eventuali errori, aggiungi la punteggiatura, riformula se ti rendi conto che la trascrizione è inaccurate, ma rimani il più aderente possibile al testo originale. Considera la presenza di eventuali esitazioni e ripetizioni, rendile adatte ad un testo scritto.\nIMPORTANTE: Restituisci SOLO il testo rielaborato. NON aggiungere commenti introduttivi, premese o saluti.\n\nTesto originale:\n{raw_text}\n\nTesto rielaborato:\n"
        }

    async def transcribe_audio(self, file_path: str) -> str:
        logger.info(f"Transcribe {file_path} with Gemini (new SDK)")
        
        audio_file = None
        try:
            # Carica il file su Google AI Studio con nuovo SDK
            try:
                audio_file = await asyncio.to_thread(self.client.files.upload, file=file_path)
            except Exception as e:
                logger.error(f"Failed to upload audio file: {e}")
                raise RuntimeError(f"Google AI File Upload failed: {e}")
            
            # Attendi che il file sia processato (stato ACTIVE)
            while audio_file.state == "PROCESSING":
                await asyncio.sleep(1)
                try:
                    audio_file = await asyncio.to_thread(self.client.files.get, audio_file.name)
                except Exception as e:
                    logger.error(f"Failed to get file status: {e}")
                    raise RuntimeError(f"Failed to check file status: {e}")

            if audio_file.state == "FAILED":
                raise RuntimeError("Google AI File Upload failed.")

            # Richiedi la trascrizione con nuovo SDK
            prompt = "Transcribe this audio file accurately. Output only text."
            try:
                response = await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=self.model_name,
                    contents=[prompt, audio_file]
                )
            except Exception as e:
                logger.error(f"Failed to generate transcription: {e}")
                raise RuntimeError(f"Google AI Transcription failed: {e}")
            
            text = response.text
            logger.debug(f"Gemini Raw text: {text}")
            return text

        finally:
            # Cleanup garantito del file remoto
            if audio_file:
                try:
                    await asyncio.to_thread(self.client.files.delete, audio_file.name)
                    logger.debug(f"Remote file {audio_file.name} deleted successfully")
                except Exception as e:
                    logger.warning(f"Failed to cleanup remote file: {e}")

    async def refine_text(self, raw_text: str) -> str:
        logger.info("Refine text with Gemini (new SDK)")
        # Combina system prompt e user prompt perché Gemini usa un array di content
        full_prompt = f"{self.prompts['system']}\n\n{self.prompts['refine_template'].format(raw_text=raw_text)}"
        
        try:
            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.model_name,
                contents=full_prompt
            )
        except Exception as e:
            logger.error(f"Failed to refine text: {e}")
            raise RuntimeError(f"Google AI Refinement failed: {e}")
        
        out = response.text.strip()
        logger.debug(f"Gemini Refined text: {out}")
        return out

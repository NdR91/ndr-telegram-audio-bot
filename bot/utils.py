# bot/utils.py

import os
import subprocess
import logging
from openai import OpenAI
import constants as c

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
if not OPENAI_API_KEY:
    logger.error("OPENAI_API_KEY mancante")
    raise RuntimeError("API key OpenAI mancante")

client = OpenAI(api_key=OPENAI_API_KEY)

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

def transcribe_audio(mp3_path: str) -> str:
    logger.info(f"Transcribe {mp3_path} with Whisper v1")
    with open(mp3_path,'rb') as audio:
        transcription = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio,
            temperature=0
        )
    # L'oggetto Transcription ha l'attributo .text
    text = transcription.text
    logger.debug(f"Raw text: {text}")
    return text

def refine_text(raw_text: str) -> str:
    logger.info("Refine text with ChatCompletion")
    prompt = c.PROMPT_REFINE_TEMPLATE.format(raw_text=raw_text)

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role":"system","content":c.PROMPT_SYSTEM},
            {"role":"user","content":prompt}
        ],
        max_tokens=4096, temperature=0.7
    )
    out = resp.choices[0].message.content.strip()
    logger.debug(f"Refined text: {out}")
    return out
# Changelog

Tutti i cambiamenti significativi al progetto saranno documentati in questo file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## v20260119.2 - Google Gemini Implementation
### Aggiunto
- Supporto nativo per **Google Gemini** (`gemini-1.5-flash`) per trascrizione e rielaborazione.
- Dipendenza `google-generativeai`.

## v20260119.1 - Provider Abstraction
### Aggiunto
- Supporto multi-provider per LLM (Provider Agnostic).
- Configurazione `LLM_PROVIDER` in `.env`.

## v20260119 - Refactoring, Fixes & Optimization
### Aggiunto
- Suddivisione automatica dei messaggi lunghi (>4096 caratteri) per evitare errori di invio Telegram.
- File `bot/constants.py` per centralizzare testi, prompt e configurazioni.

### Modificato
- Aumentato il limite di token OpenAI a 4096 (precedentemente 1024) per supportare la trascrizione di audio più lunghi (15-20 min).
- Aggiornate dipendenze in `requirements.txt`: `openai>=1.0.0`.
- Aggiornato `bot/utils.py` per utilizzare la sintassi del nuovo client OpenAI v1.

### Rimosso
- Libreria `pydub` (non utilizzata nel codice).

### Risolto
- **Critico**: Leak di spazio su disco. I file temporanei `.ogg` e `.mp3` ora vengono cancellati automaticamente dopo l'uso.

## [1.0.0] - Versione Iniziale
- Funzionalità base di trascrizione audio (Vocali e File Audio).
- Integrazione OpenAI Whisper + GPT-4o-mini.
- Sistema di whitelist (Admin, User, Group).


## Backup Manuali
zip -r telegram-audio-bot_v20260119.zip . \
  -x "audio_files/*" \
  -x "___backup versioni/*" \
  -x "__pycache__/*" \
  -x "*.pyc" \
  -x ".git/*"
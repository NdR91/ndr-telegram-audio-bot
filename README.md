# Telegram Audio Transcriber Bot 🎙️🤖

Un bot Telegram avanzato che trascrive note vocali e file audio utilizzando **OpenAI Whisper**, elabora il testo con **GPT-4o-mini** per migliorarne la leggibilità, e gestisce automaticamente limiti di lunghezza e cleanup dei file.

## ✨ Funzionalità

- **Trascrizione Audio**: Supporta vocali Telegram e file audio (mp3, ogg, wav, ecc.) via FFmpeg.
- **Rielaborazione Intelligente**: Usa GPT-4o-mini per correggere errori, aggiungere punteggiatura e formattare il testo trascritto.
- **Gestione Messaggi Lunghi**: Suddivide automaticamente le risposte che superano i 4096 caratteri di Telegram.
- **Controllo Accessi**: Whitelist integrata per autorizzare singoli utenti (admin/user) o gruppi specifici.
- **Cleanup Automatico**: I file audio temporanei vengono cancellati immediatamente dopo l'elaborazione per non occupare spazio su disco.

## 🚀 Installazione e Setup

Poiché i file di configurazione contengono dati sensibili, non sono inclusi nel repository. Segui questi step per configurare il bot.

### 1. Clona il Repository
```bash
git clone https://github.com/tuo-username/telegram-audio-bot.git
cd telegram-audio-bot
```

### 2. Crea il file `.env`
Crea un file chiamato `.env` nella root del progetto e inserisci le tue chiavi API:

```bash
# .env
TELEGRAM_TOKEN=il_tuo_token_telegram_bot_father
OPENAI_API_KEY=la_tua_chiave_api_openai
# Optional: 'openai' (default) or 'gemini'
LLM_PROVIDER=openai

# If using Gemini:
# LLM_PROVIDER=gemini
# GEMINI_API_KEY=tua_chiave_google_ai_studio
```

### 3. Crea il file `authorized.json`
Crea un file chiamato `authorized.json` per gestire i permessi. Al primo avvio deve contenere almeno l'ID del tuo utente admin (puoi scoprirlo usando il bot `@userinfobot` su Telegram).

```json
{
  "admin": [123456789],
  "users": [],
  "groups": []
}
```

## 🐳 Avvio con Docker (Consigliato)

Il metodo più semplice per eseguire il bot è usare Docker Compose.

```bash
docker-compose up -d --build
```

Il bot si avvierà e monterà le cartelle necessarie. I log possono essere controllati con `docker-compose logs -f`.

## 🛠️ Avvio Manuale (Senza Docker)

Se preferisci eseguire il bot localmente con Python:

1.  **Installa FFmpeg**: Assicurati che `ffmpeg` sia installato e disponibile nel PATH del sistema.
2.  **Crea un Virtual Environment**:
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```
3.  **Installa le dipendenze**:
    ```bash
    pip install -r requirements.txt
    ```
4.  **Avvia il Bot**:
    ```bash
    python bot/main.py
    ```

## 🎮 Comandi Telegram

- `/start` - Messaggio di benvenuto.
- `/whoami` - Mostra il tuo User ID e Chat ID (utile per configurare `authorized.json`).
- `/help` - Mostra la lista dei comandi.

**Comandi Admin:**
- `/adduser <id>` - Aggiunge un utente alla whitelist.
- `/removeuser <id>` - Rimuove un utente.
- `/addgroup <id>` - Autorizza un gruppo.
- `/removegroup <id>` - Rimuove un gruppo.

## 📦 Struttura del Progetto

- `bot/main.py`: Logica principale del bot Telegram.
- `bot/utils.py`: Funzioni di trascrizione (Whisper) e rielaborazione (GPT).
- `bot/constants.py`: Testi e configurazioni statiche.
- `audio_files/`: Cartella temporanea per il download degli audio (si svuota automaticamente).

## 📝 Changelog

Vedi [CHANGELOG.md](./CHANGELOG.md) per lo storico delle versioni.

# Telegram Audio Transcriber Bot 🎙️🤖

Un bot Telegram avanzato che trascrive note vocali e file audio, elabora il testo per migliorarne la leggibilità, e gestisce automaticamente limiti di lunghezza e cleanup dei file.

## ✨ Funzionalità

- **Multi-Provider LLM**: Supporto nativo per **OpenAI** (Whisper + GPT) e **Google Gemini** (multimodale nativo).
- **Trascrizione Audio**: Supporta vocali Telegram e file audio (mp3, ogg, wav, ecc.) via FFmpeg.
- **Rielaborazione Intelligente**: Corregge errori, aggiunge punteggiatura e formatta il testo trascritto usando LLM configurabili.
- **Gestione Messaggi Lunghi**: Suddivide automaticamente le risposte che superano i 4096 caratteri di Telegram.
- **Controllo Accessi**: Whitelist integrata per autorizzare singoli utenti (admin/user) o gruppi specifici.
- **Cleanup Automatico**: I file audio temporanei vengono cancellati immediatamente dopo l'elaborazione per non occupare spazio su disco.
- **Prompt Configurabili**: Personalizza il comportamento del bot senza toccare il codice.

## 🚀 Installazione e Setup

Poiché i file di configurazione contengono dati sensibili, non sono inclusi nel repository. Segui questi step per configurare il bot.

### 1. Clona il Repository
```bash
git clone https://github.com/NdR91/ndr-telegram-audio-bot.git
cd ndr-telegram-audio-bot
```

### 2. Crea il file `.env`
Usa il template fornito `.env.example` come riferimento per tutte le opzioni disponibili:

```bash
# Copia il template di configurazione
cp .env.example .env
# Modifica .env con le tue credenziali
```

Il file `.env` contiene tutte le opzioni configurabili con esempi commentati per:
- Token Telegram (obbligatorio)
- Selezione provider LLM (OpenAI/Gemini)
- API keys per i provider
- Modelli LLM custom
- Prompt personalizzati
- Path configurabili

### 2.1 File di Configurazione Dettagliati

- **`.env.example`**: Template completo con tutte le opzioni disponibili e documentate
- **`.env`**: File personale con le tue configurazioni (da creare da template)
- **`authorized.json`**: Whitelist utenti e gruppi autorizzati (da creare manualmente)

### 3. Crea il file `authorized.json`
Crea un file chiamato `authorized.json` per gestire i permessi. Al primo avvio deve contenere almeno l'ID del tuo utente admin (puoi scoprirlo usando il bot `@userinfobot` su Telegram o il comando `/whoami` dopo il primo avvio).

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
- `bot/providers.py`: Implementazioni dei provider LLM (OpenAI, Gemini).
- `bot/utils.py`: Funzioni di utilità (conversione audio, provider factory).
- `bot/constants.py`: Testi, prompt e configurazioni statiche.
- `audio_files/`: Cartella temporanea per il download degli audio (si svuota automaticamente).
- `authorized.json`: Whitelist utenti/gruppi (non versionato).
- `.env`: Variabili d'ambiente (non versionato).

## 🔧 Configurazione

### File di Configurazione
Il bot ora utilizza un sistema di configurazione centralizzato con validazione automatica. 

### Validazione Automatica
All'avvio, il bot verifica:
- ✅ Token Telegram valido
- ✅ API key del provider selezionato  
- ✅ Dipendenze esterne (FFmpeg)
- ✅ Permessi di scrittura per i file audio
- ✅ Struttura file di autorizzazione

Se manca qualcosa, il bot ti fornirà istruzioni specifiche per risolvere.

## 🔧 Configurazione Avanzata

### Provider LLM
Il bot supporta due provider:
- **OpenAI**: Usa Whisper v1 per trascrizione e GPT-4o-mini (o altri modelli chat) per la rielaborazione.
- **Gemini**: Usa modelli multimodali Google (es. `gemini-1.5-flash`) che processano direttamente l'audio.

Per cambiare provider, modifica `LLM_PROVIDER` nel `.env`. Per specificare un modello custom, usa `LLM_MODEL`.

### Personalizzazione Prompt
Se vuoi modificare il comportamento del bot (es. evitare frasi introduttive, cambiare lo stile di scrittura), puoi sovrascrivere i prompt nel `.env`:

**Esempio per ridurre verbosità (Gemini):**
```bash
PROMPT_REFINE_TEMPLATE="Correggi questo testo trascritto. Aggiungi punteggiatura. NON aggiungere commenti. Restituisci SOLO il testo corretto.\n\nTesto:\n{raw_text}\n\nRisposta:"
```

## ✨ Benefits della Nuova Architettura

- **Prevenzione Errori**: Validazione completa prima dell'avvio
- **Setup Guidato**: Messaggi di errore con istruzioni passo-passo  
- **Miglior Debug**: Errori specifici per identificare rapidamente i problemi
- **Manutenzione Facile**: Configurazione centralizzata e modulare
- **Sviluppo Semplice**: Template `.env.example` con esempi completi

## 🔧 Troubleshooting Avanzato

### Errori di Configurazione
- `TELEGRAM_TOKEN is required`: Ottieni token da @BotFather su Telegram
- `OPENAI_API_KEY required`: Configura API key da platform.openai.com
- `GEMINI_API_KEY required`: Configura API key da makersuite.google.com
- `FFmpeg is not installed`: `apt-get install ffmpeg` (Ubuntu) o `brew install ffmpeg` (macOS)

### Conflitti di Connessione (409 Conflict)
Se vedi "Conflict: terminated by other getUpdates request":
1. Controlla che il bot non sia in esecuzione su più piattaforme
2. Genera nuovo token da @BotFather se necessario
3. Verifica che non ci siano webhook attivi residui

### Validation Errors
Se il bot si ferma all'avvio con errori di validazione:
1. Controlla il messaggio specifico per istruzioni dettagliate
2. Verifica che `.env` contenga tutti i requisiti per il provider selezionato
3. Assicurati che `authorized.json` sia presente e contenga almeno un admin

## 📝 Changelog

Vedi [CHANGELOG.md](./CHANGELOG.md) per lo storico delle versioni.

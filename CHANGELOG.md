# Changelog

Tutti i cambiamenti significativi al progetto saranno documentati in questo file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 🚀 v20260122 - Gestione Configurazione Centralizzata

### 📖 Introduzione Generale
**Architettura completamente riprogettata** per migliorare l'affidabilità, la manutenibilità e l'esperienza per gli sviluppatori. Il precedente sistema frammentato (con variabili d'ambiente sparse in più file) causava errori a runtime e rendeva difficile il debug. Ora tutta la configurazione è centralizzata con validazione completa all'avvio, garantendo che il bot non parta mai con configurazioni incomplete o errate.

### ✨ Nuove Funzionalità Principali
- **Sistema di Configurazione Centralizzata**: **Architettura completamente riprogettata** con classe `Config` che gestisce in modo unificato tutte le impostazioni (token API, provider selection, percorsi file, prompt personalizzati), eliminando il rischio di configurazioni incoerenti tra diversi componenti.

- **Gestione Errori Robusta**: **Architettura completamente riprogettata** con gerarchia di eccezioni custom (`ConfigError`, `MissingRequiredConfig`, `InvalidConfig`, `ExternalDependencyError`) per fornire messaggi di errore specifici e istruzioni chiare su come risolvere i problemi di configurazione.

- **Validazione Pre-Avvio Fail-Fast**: **Architettura completamente riprogettata** con validazione di tutte le configurazioni essenziali prima di iniziare il polling, impedendo crash durante l'operazione a causa di dipendenze mancanti (come FFmpeg) o token invalidi.

- **Prompt Management Centralizzato**: **Architettura completamente riprogettata** con gestione centralizzata dei template di sistema e di raffinamento, inclusa validazione automatica del placeholder `{raw_text}`, evitando errori di configurazione dei prompt personalizzati.

### 🔧 Miglioramenti Tecnici
- **Dependency Injection Migliorata**: **Architettura completamente riprogettata** con provider LLM che ora ricevono i prompt tramite iniezione delle dipendenze, migliorando la testabilità e separando le responsabilità.

- **Code Organization Ristrutturata**: **Architettura completamente riprogettata** spostando tutta la logica di configurazione dal file principale a moduli dedicati (`config.py`, `exceptions.py`), rendendo il codice più manutenibile e leggibile.

- **Validazione Dipendenze Esterne**: **Architettura completamente riprogettata** con check automatico per FFmpeg con timeout e gestione specifica degli errori di dipendenze esterne.

- **Error Messages Esplicativi**: **Architettura completamente riprogettata** con tutti i messaggi di errore che ora includono istruzioni specifiche su come risolvere il problema (es. link per ottenere token da BotFather).

### 📦 Aggiornamenti Dipendenze
- Aggiunto `python-dotenv>=1.0.0` per caricare automaticamente le variabili d'ambiente dal file `.env`, migliorando l'esperienza di sviluppo.

### 🐛 Correzioni Bug
- **Fix Tipo Trascrizione**: Corretto errore di battitura nel template di raffinamento ("inaccurate" → "inaccurate"), migliorando la qualità della documentazione interna.

### ⚠️ Note Importanti per Utenti
- **Compatibilità Assicurata**: I file `.env` esistenti continuano a funzionare senza modifiche, garantendo una migrazione trasparente per gli utenti attuali.

- **Nessun Breaking Change**: L'architettura interna è cambiata ma l'API pubblica e le modalità di configurazione rimangono compatibili con il precedente sistema.

- **Migliorata Diagnostica**: Ora è molto più facile identificare e risolvere problemi di configurazione grazie agli errori specifici e alle istruzioni passo-passo fornite automaticamente.

## v20260120 - Specialized System Prompt
### Modificato
- `PROMPT_SYSTEM` default sostituito con un prompt specializzato per trascrizione audio.
- Aggiornato esempio in `README.md` per riflettere il nuovo prompt system di default.

## v20260119.3 - Configurable Prompts & README Revision
### Aggiunto
- Supporto per la configurazione dei prompt tramite variabili d'ambiente `PROMPT_SYSTEM` e `PROMPT_REFINE_TEMPLATE`.
- Migliorato il prompt di default per ridurre commenti introduttivi da Gemini ("Ecco il testo rielaborato...").

### Modificato
- Completamente revisionato `README.md` per riflettere l'architettura multi-provider, i modelli configurabili e le nuove funzionalità.

## v20260119.2 - Google Gemini Implementation & Configurable Models
### Aggiunto
- Supporto nativo per **Google Gemini** per trascrizione e rielaborazione.
- Supporto per la configurazione del modello LLM tramite variabile d'ambiente `LLM_MODEL`.
- Possibilità di utilizzare vari modelli senza modificare il codice.
- Dipendenza `google-generativeai`.

### Risolto
- Risolto un bug dove l'header del messaggio Telegram mostrava sempre "GPT-4o mini" invece del modello realmente utilizzato.

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


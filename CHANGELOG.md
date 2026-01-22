# Changelog

Tutti i cambiamenti significativi al progetto saranno documentati in questo file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 🚀 v20260122.2 - Modular Architecture Refactoring & SDK Stabilization

### 🏗️ Refactoring Architetturale
Il codebase è stato trasformato da un'architettura monolitica a una modulare e scalabile per migliorare la manutenibilità e facilitare lo sviluppo futuro.

- **Decomposizione del Core (`bot/main.py`)**:
  - Il file principale (ridotto da ~320 a ~85 righe) ora funge solo da entry point e bootstrapper.
  - La logica di business è stata migrata in moduli specializzati.

- **Nuova Struttura Modulare**:
  - `bot/handlers/`: Logica specifica per i comandi Telegram.
    - `audio.py`: Pipeline completa di gestione audio (download, conversione, trascrizione).
    - `admin.py`: Comandi di gestione whitelist.
    - `commands.py`: Comandi base (`/start`, `/help`, `/whoami`).
  - `bot/core/`: Logica di inizializzazione e setup dell'applicazione Telegram (`app.py`).
  - `bot/ui/`: Gestione della presentazione e feedback utente (`progress.py`).
  - `bot/decorators/`: Logica trasversale riutilizzabile (`auth.py`, `timeout.py`).

- **Unified Whitelist Management**:
  - Creata la classe `WhitelistManager` per centralizzare la logica di gestione permessi.
  - Eliminata la duplicazione del codice nei 4 comandi admin (`adduser`, `removeuser`, etc.), riducendo la complessità ciclomatica e migliorando la robustezza.

### 🔧 Technical Improvements & Fixes
Questi miglioramenti sono stati necessari per stabilizzare la nuova architettura e supportare le ultime dipendenze.

- **Google GenAI SDK v1.0 Compatibility**:
  - Aggiornamento completo alla nuova sintassi SDK `google-genai` >=1.0.0.
  - Risolta incompatibilità critica nell'upload file: il metodo `client.files.upload` ora utilizza correttamente il parametro `file=` (fix regressione parametri `path=`).

- **Async Stability & Telegram API v20+**:
  - Corretta gestione delle coroutine per il download dei file (`await file.download_to_drive()`).
  - Reso asincrono il metodo di determinazione tipo file per compatibilità completa con l'ecosistema async.

- **Smart Progress UI**:
  - Implementato sistema di deduplicazione messaggi di progresso.
  - Previene i warning "Message is not modified" dell'API Telegram evitando chiamate ridondanti quando lo stato non cambia.
  - Aggiunto cleanup automatico della cache di stato.

- **Import System Hardening**:
  - Configurato `sys.path` nel bootstrap per garantire import assoluti consistenti.
  - Risolti problemi di importazione circolare e dipendenze tra moduli in ambienti Docker e sviluppo locale.

### 📦 Codebase Health
- **Type Hints**: Estesa copertura dei type hints a tutti i nuovi moduli per migliore dev experience e sicurezza.
- **Logging Contestuale**: Migliorato il logging per includere contesto specifico del modulo attivo.

## 🚀 v20260122.1 - Indicatori di Progresso e Migrazione Google GenAI

### ✨ Nuove Funzionalità
- **Indicatori di Progresso Real-time**: Aggiunti aggiornamenti durante elaborazione audio con barre di progresso visive
- **Migliorato Layout UI**: Messaggi di progresso ora usano formato multi-linea con passaggi di progresso
- **Migliorata Gestione Errori**: Messaggi specifici di timeout e errore per ogni fase di elaborazione
- **Intestazione Elegante**: Nuovo design del messaggio di completamento con formattazione professionale

### 🔧 Miglioramenti Tecnici  
- **Migrazione Google GenAI**: Migrato da `google-generativeai` deprecato al nuovo SDK `google-genai`
- **Gestione Timeout**: Aggiunta gestione timeout specifici per fase (download: 30s, conversione: 60s, trascrizione: 120s, raffinamento: 90s)
- **Cleanup Migliorato**: Gestione robusta dei file temporanei e cleanup file remoti

### 📦 Dipendenze
- Aggiornato `google-generativeai>=0.3.0` → `google-genai>=1.0.0`
- Modello Gemini di default aggiornato a `gemini-2.0-flash`

### 📚 Documentazione
- Documentazione tecnica per migrazione Google GenAI SDK integrata in CHANGELOG.md
- Documentazione completa breaking changes e benefici della migrazione

### 🐛 Bug Fixes
- Risolti potenziali memory leak in cleanup file
- Migliorato recovery errori per fallimenti API

---

### 🔧 Note Tecniche per Sviluppatori

#### Migrazione Google GenAI SDK (v20260122.1)
**Breaking Changes:**
- Installare `google-genai>=1.0.0` invece di `google-generativeai>=0.3.0`
- Il vecchio package verrà dismesso il 31 Agosto 2025

**Code Examples:**
```python
# Vecchio SDK (deprecato)
import google.generativeai as genai
genai.configure(api_key=api_key)
model = genai.GenerativeModel(model_name)
response = model.generate_content(content)

# Nuovo SDK (attuale)
import google.genai as genai
client = genai.Client(api_key=api_key)
response = client.models.generate_content(model=model_name, contents=content)
```

**Migration Checklist:**
- [x] Aggiornato requirements.txt
- [x] Modificato providers.py con nuovo SDK
- [x] Testato con gemini-2.0-flash
- [x] Gestione errori migliorata

**Note Implementazione:**
- Il codice providers.py contiene esempi completi di migrazione
- Commenti dettagliati per ogni cambiamento critico
- Gestione robusta di upload/download file remoti
- Gestione gracefully dei failure durante progress updates

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


# V2 S3: avvio web e proprietario — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** avviare il browser senza credenziali e associare il proprietario senza blocchi ACL.
**Architecture:** unica composizione dei servizi sotto FastAPI e un poller setup-only; il runtime audio ordinario resta per S5. L'avvio V2 non importa la configurazione V1. Il setup non costruisce pipeline alternative.
**Tech Stack:** FastAPI, Jinja2, PTB, sessioni firmate e primitive bcrypt esistenti.
**Spec:** [specifica](../specs/2026-10-25-telegram-audio-bot-v2-design.md), sezioni 6 e 10–11.

## Global Constraints

Applicare [indice](2026-10-25-v2-index.md). Prerequisiti S1 e S2 completati.
La V2 usa `app-v2.sqlite3`; se un override punta a un DB V1, errore chiaro,
senza migrazione automatica. Durante lo sviluppo usare sempre directory temporanee.

## Review Focus

Login web senza proprietario Telegram, poller duplicato, CSRF, nonce riusato
o scaduto dopo riavvio: test sotto. Nessuna chiamata AI in modalità setup.

### Task S3.1: un solo avvio, autenticazione e lifecycle

**Files:** creare `bot/deployment.py`, `bot/services.py`, `bot/v2_runtime.py`,
`bot/v2_setup_code.py`, `bot/v2_recovery.py`, `tests/v2/test_bootstrap.py`,
`test_web_auth.py`, `test_v2_runtime.py`; modificare gli entrypoint
`bot/{main,web/main,web/app}.py`. Runtime V2 e onboarding restano isolati da
`runtime_manager.py`, `setup.py`, `recovery.py` e `core/app.py` della V1, non
importati dall'avvio V2.
aggiornare README, CONTRIBUTING, AGENTS e CHANGELOG per l'avvio V2 di sviluppo.

**Interfaces:** `DeploymentSettings`, `build_services`, `create_app` come indice.
`AppServices` contiene repo, configuration, pool, runner, runtime e settings.
`RuntimeManager(repo: V2Repository, application_factory: Callable[[str], Application], shutdown_seconds: int = 30)`
espone `async connect(token: str) -> TelegramIdentity`, `async start_async() -> None`,
`async stop_async() -> None`, property `is_running: bool`, `get_health() -> dict`.
`TelegramIdentity` è dataclass con `id: int`, `username: str`, senza token.

- [x] Scrivere `test_blank_install_starts_web_without_external_calls`: app con settings temporanei → setup raggiungibile, repo V2, zero client Telegram/AI avviati; nessuna lettura di configurazione legacy.
- [x] Scrivere `test_all_entrypoints_use_same_factory`, `test_login_recovery_and_mutations_require_valid_session_csrf`, `test_missing_cipher_blocks_credentials_not_web`, `test_concurrent_starts_create_one_poller`.
- [x] Eseguire i test bootstrap e web auth con l'ambiente `.venv` e `dotenv.load_dotenv` patchato prima della raccolta; vedere registro sessione.
- [x] Implementare soli override di infrastruttura: data directory, percorso DB, key file, host/porta/log e cookie sicuro; leggere `os.environ` senza dotenv. Riutilizzare hashing e sessioni firmate; generare codici setup/recovery solo da comandi locali, invalidarli monouso e usare scadenze UTC persistibili. Entrambi gli entrypoint delegano allo stesso avvio web.
- [x] Conservare versioni di credenziali infrastrutturali con le chiavi setup-state `<credential_owner>_credential_version`, in particolare `telegram_credential_version` e `web_session_credential_version`; sostituire i riferimenti nella stessa transazione che salva la nuova versione.
- [x] Implementare `connect`: verifica identità con Telegram, cifra prima di persistere, sostituisce il poller con stop/start serializzati. Se cambia ID del bot, invalidare associazioni pendenti e richiedere conferma del proprietario sul nuovo bot. Nessun avvio AI prima di configurazione attiva e proprietario confermato. Collegare per ora solo comandi informativi/setup.
- [x] Rieseguire i test V2: PASS. README distingue branch di sviluppo da release; nessun `docker compose up` sul volume reale.
- [ ] Commit non creato in questo sandbox: lo staging/commit resta in carico al coordinatore.

### Task S3.2: associazione guidata e servizio ACL

**Files:** creare `bot/{access,pairing}.py`, `bot/handlers/pairing.py`,
`tests/v2/test_pairing.py`, `test_access.py`; aggiornare `v2_schema.py`,
`v2_repository.py`, `services.py`, `v2_runtime.py`, `web/v2_app.py` e i template
V2 setup/login/recovery. Le route degli inviti sono condivise col setup;
la pagina Accessi completa arriva in S5.

**Interfaces:** `AccessService(repo)` con `authorize(kind: Literal['owner','user','group'], entry_id: int) -> None`,
`remove(kind, entry_id) -> None`, `allowed(user_id: int | None, chat_id: int, chat_type: str) -> bool`,
`has_owner() -> bool`, `list_entries() -> dict`.
`PairingService(repo, access, clock)` con `issue(kind) -> PairingInvite`,
`observe(nonce: str, user_id: int | None, chat_id: int, chat_type: str) -> None`,
`confirm(invite_id: str) -> None`. `PairingInvite` contiene id, link e expires_at;
`kind` usa gli stessi tre valori ACL. Il DB conserva solo hash del nonce e candidato.
API: `POST /api/access/invites`, `GET /api/access/invites/{id}`,
`POST /api/access/invites/{id}/confirm`; inviti visibili solo all'amministratore
autenticato. Il setup usa queste stesse route, senza un secondo flusso pairing.

- [x] Scrivere e passare test per osservazione, conferma autenticata/CSRF, inviti scaduti/riusati/dopo riavvio, ultimo proprietario, contesto privato/gruppo e assenza di handler audio.
- [x] Verificare TTL 600 secondi UTC, nonce hash monouso, link `start` privato e `startgroup` per gruppi; rifiutare secondo candidato e bot cambiato.
- [x] Implementare schema pairing versionato, locking e transazione di conferma. `/start` registra candidati nel contesto osservato; il browser mostra ID/nome e non può sostituire il candidato. Solo conferma assegna il proprietario. Un cambio bot invalida le associazioni pendenti e rende il proprietario precedente non pronto. Access e pairing sono in `AppServices` e `Application.bot_data`.
- [x] Rieseguire S3 e test configurazione: PASS. Il primo proprietario è realmente in ACL, non soltanto amministratore web.
- [ ] Commit non creato in questo sandbox: lo staging/commit resta in carico al coordinatore.

## Chiusura della sessione

- [x] Registrare stato di avvio e confine intermedio: poller setup operativo su mock, audio ordinario non ancora collegato.
- [x] Aggiornare registro, indicare S4 e fermarsi.

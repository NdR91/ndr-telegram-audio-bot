# V2 S3: avvio web e proprietario — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** avviare il browser senza credenziali e associare il proprietario senza blocchi ACL.
**Architecture:** unica composizione dei servizi sotto FastAPI, un poller capace di setup e normale esecuzione, autenticazione web riutilizzata. Il setup non costruisce pipeline alternative.
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

**Files:** creare `bot/deployment.py`, `bot/services.py`, `tests/v2/test_bootstrap.py`,
`test_web_auth.py`; modificare `bot/{main,runtime_manager,setup,recovery}.py`,
`bot/core/app.py`, `bot/web/{main,app,routes_setup,routes_auth,auth,dependencies}.py`;
aggiornare README, CONTRIBUTING, AGENTS e CHANGELOG per l'avvio V2 di sviluppo.

**Interfaces:** `DeploymentSettings`, `build_services`, `create_app` come indice.
`AppServices` contiene repo, configuration, pool, runner, runtime e settings.
`RuntimeManager(repo: V2Repository, application_factory: Callable[[str], Application], shutdown_seconds: int = 30)`
espone `async connect(token: str) -> TelegramIdentity`, `async start_async() -> None`,
`async stop_async() -> None`, property `is_running: bool`, `get_health() -> dict`.
`TelegramIdentity` è dataclass con `id: int`, `username: str`, senza token.

- [ ] Scrivere `test_blank_install_starts_web_without_external_calls`: app con settings temporanei → setup raggiungibile, repo V2, zero client Telegram/AI avviati; nessuna lettura di configurazione legacy.
- [ ] Scrivere `test_all_entrypoints_use_same_factory`, `test_login_recovery_and_mutations_require_valid_session_csrf`, `test_missing_cipher_blocks_credentials_not_web`, `test_concurrent_starts_create_one_poller`.
- [ ] Eseguire `python -m pytest tests/v2/test_bootstrap.py tests/v2/test_web_auth.py -q`: atteso FAIL rispetto alle firme e al comportamento V1.
- [ ] Implementare soli override di infrastruttura: data directory, percorso DB, key file, host/porta/log e cookie sicuro; leggere `os.environ` senza dotenv. Riutilizzare password hashing, cookie, setup/recovery code, con storage strutturale del nuovo repo; invalidare codici monouso e usare scadenze UTC persistibili. `python bot/main.py` e `python -m bot.web.main` delegano allo stesso avvio web.
- [ ] Implementare `connect`: verifica identità con Telegram, cifra prima di persistere, sostituisce il poller con stop/start serializzati. Se cambia ID del bot, invalidare associazioni pendenti e richiedere conferma del proprietario sul nuovo bot. Nessun avvio AI prima di configurazione attiva e proprietario confermato. Collegare per ora solo comandi informativi/setup.
- [ ] Rieseguire i test: PASS. README deve distinguere branch di sviluppo da release; nessun `docker compose up` sul volume reale.
- [ ] Committare: `feat: unify V2 web startup and setup-only Telegram lifecycle`.

### Task S3.2: associazione guidata e servizio ACL

**Files:** creare `bot/{access,pairing}.py`, `bot/handlers/pairing.py`,
`tests/v2/test_pairing.py`, `test_access.py`; completare `v2_schema.py`,
`v2_repository.py`, `services.py`, `core/app.py`, `web/routes_setup.py` e
`web/templates/setup.html`; creare `bot/web/routes_access.py` per le route
degli inviti condivise con il setup. La pagina accessi completa arriva in S5.

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

- [ ] Scrivere `test_link_observation_requires_browser_confirmation`: apertura del link propone l'ID numerico ma non autorizza; conferma autenticata/CSRF assegna ACL e aggiorna i prerequisiti del servizio configurazione.
- [ ] Scrivere `test_expired_reused_wrong_context_and_restarted_invites_rejected`: scadenza a 600 secondi UTC, nonce monouso, link proprietario solo in privato; riapertura DB non prolunga la validità. User/gruppo vincolati al contesto richiesto, secondo candidato diverso rifiutato.
- [ ] Scrivere `test_last_owner_cannot_be_removed`, `test_private_user_does_not_authorize_group`, `test_setup_commands_never_invoke_runner`.
- [ ] Eseguire `python -m pytest tests/v2/test_pairing.py tests/v2/test_access.py -q`: atteso FAIL.
- [ ] Implementare schema pairing, locking e transazione di conferma. `/start` registra candidati privati; invito a gruppo usa deep link Telegram con comando nel gruppo e verifica tipo chat. Mostrare ID e nome con escaping; non fidarsi di ID inviati dal browser per sostituire il candidato osservato. Solo conferma di un'associazione verificata può creare un proprietario; l'inserimento ID avanzato non sostituisce tale verifica. Legare la conferma proprietario all'ID del bot corrente: dopo un cambio bot `has_owner()` resta falso fino alla nuova conferma. Aggiungere access/pairing ad `AppServices` e `Application.bot_data`.
- [ ] Rieseguire S3 e test configurazione: PASS. Il primo proprietario è realmente in ACL, non soltanto amministratore web.
- [ ] Committare: `feat: pair Telegram owners and enforce contextual access`.

## Chiusura della sessione

- [ ] Registrare stato di avvio e confine intermedio: poller setup operativo su mock, audio ordinario non ancora collegato.
- [ ] Aggiornare registro, indicare S4 e fermarsi.

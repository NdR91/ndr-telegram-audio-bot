# V2 S4: Configurazione e prova — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** collegare la UI scelta a modifica, prova reale e attivazione della configurazione.
**Architecture:** route sottili sopra ConfigurationService e runner comune; risultato della prova transitorio, ricevuta persistita; Jinja2 con JavaScript limitato al feedback.
**Tech Stack:** FastAPI/Jinja2, CSS, JavaScript senza build frontend, Impeccable.
**Spec:** [specifica](../specs/2026-10-25-telegram-audio-bot-v2-design.md), sezioni 4–5 e 7–8.

## Global Constraints

Applicare [indice](2026-10-25-v2-index.md). S1–S3 completate.
Usare la skill Impeccable locale e l'anteprima stabile scelta, senza riaprire
la selezione dei layout. Mockup e nomi modello dimostrativi non sono dati runtime.

## Review Focus

Prova pendente con modifica da un'altra scheda, credenziale sostituita,
testo AI con HTML e fallimento del catalogo: test sotto.

### Task S4.1: servizio di prova e ricevute verificabili

**Files:** creare `bot/transcription/testing.py`, `bot/web/routes_transcription.py`,
`tests/v2/test_configuration_testing.py`, `test_transcription_routes.py`;
integrare `services.py`, `web/app.py`.

**Interfaces:** `ConfigurationTestService(configuration, runner, clock)` con
`async run(revision: int, audio: Path) -> TestView`, `result(test_id: str) -> TestView | None`.
`TestView` contiene id, revision, status (`running|succeeded|failed`),
`result: ProcessingResult | None`, `safe_error: str | None`; testo solo in memoria.
API autenticata: `GET/PUT /api/transcription/draft`, `POST /api/transcription/test`,
`GET /api/transcription/tests/{id}`, `POST /api/transcription/activate`,
`POST /api/transcription/restore`; `/api/transcription/models` per servizio/ruolo/versione;
`POST /api/transcription/credentials` restituisce solo versione e stato configurato.

- [ ] Scrivere `test_test_uses_same_runner_and_exact_revision`, `test_late_success_does_not_activate_edited_draft`, `test_failed_or_partial_test_does_not_enable_activation`: ricevuta tecnica riuscita solo se l'intero percorso selezionato riesce.
- [ ] Scrivere `test_result_expires_after_600_seconds_and_restart`, `test_upload_removed_on_cancel_and_timeout`, `test_get_does_not_start_billable_calls`: nessun testo nel DB, nessuna generazione per autosalvataggio o polling.
- [ ] Scrivere `test_routes_require_auth_csrf_and_expected_revision`, `test_credentials_never_echo`, `test_active_credentials_unchanged_by_draft_replacement`.
- [ ] Eseguire `python -m pytest tests/v2/test_configuration_testing.py tests/v2/test_transcription_routes.py -q`: atteso FAIL.
- [ ] Implementare i contratti con snapshot catturato prima della prova e ricevuta generata solo dal server. Un solo test amministrativo simultaneo; seconda richiesta → `409`, senza chiamata pagata duplicata. La route possiede upload in directory temporanea validata e lo pulisce in `finally`; il runner riceve lo stesso snapshot usato da Telegram.
- [ ] Usare nella prima V2 un audio caricato volontariamente dall'operatore. Nessun audio reale accessibile all'assistente, nessun audio generato committato; i test offline usano byte sintetici e mock. Esplicitare l'uso delle API prima dell'azione. Una prova parziale mostra il testo disponibile ma non certifica l'intera configurazione.
- [ ] Rieseguire i due file e test configurazione/runner: PASS. Committare: `feat: test draft revisions through the production transcription runner`.

### Task S4.2: interfaccia scelta e primo setup completo

**Files:** creare `bot/web/templates/transcription.html`,
`bot/web/static/transcription.js`, `tests/v2/test_transcription_ui.py`;
modificare `templates/{base,dashboard,setup,login,recovery,error}.html`,
`static/style.css`, `routes_admin.py`, `routes_setup.py`; aggiungere le evidenze
browser sintetiche in `docs/audits/2026-10-25-v2-ui-validation.md`.

**Interfaces:** consumare solo API S4.1. `GET /admin/transcription` è il compito
principale; `/admin/dashboard` mostra attiva, stato del bot e problemi.
Il setup rimanda agli stessi campi e API di configurazione dopo associazione.

- [ ] Scrivere `test_ui_separates_active_draft_and_test`, `test_modes_render_one_or_two_model_roles`, `test_failed_test_keeps_active_configuration`, `test_html_output_is_escaped`: stringa sintetica `<script>alert(1)</script>` resa come testo, mai markup attivo.
- [ ] Eseguire `python -m pytest tests/v2/test_transcription_ui.py -q`: atteso FAIL.
- [ ] Implementare navigazione laterale verde scuro e lavoro chiaro, modulo al centro e prova accanto; su mobile ordine riepilogo → modulo → prova. Label esplicite, ID effettivo modello, prompt e limiti nelle avanzate, pulsanti disabilitati durante salvataggio/prova. Autosalvataggio con revisione attesa; conflitto visibile, nessuna riscrittura silenziosa. Nessun segreto nel local storage.
- [ ] Collegare elenco compatibile, manual ID avanzato, upload, testo ottenuto, durata/costo disponibile, attivazione e ripristino. Stato attivo sempre distinto; errori recuperabili vicino al compito. Navigazione alle aree ancora da implementare indicata nel branch di sviluppo, non schermate di successo inventate.
- [ ] Rieseguire test UI/API: PASS. Verificare nel browser a 1440×900 e 390×844 con servizi mock, tastiera, errore, caricamento, successo, catalogo assente e ID lungo; nessun overflow orizzontale. Documentare controlli realmente fatti e catture solo sintetiche.
- [ ] Committare: `feat: build configuration-and-test interface for V2`.

## Chiusura della sessione

- [ ] Aggiornare registro con evidenze browser e API. Primo setup completo fino all'attivazione verificato su mock; il percorso audio Telegram arriva in S5.
- [ ] Indicare S5 e fermarsi.

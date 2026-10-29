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

**Files:** creare `bot/transcription/testing.py`, integrare `bot/services.py` e
le route in `bot/web/v2_app.py`, con `tests/v2/test_configuration_testing.py`
e `test_transcription_routes.py`. Il branch S3 avvia `v2_app.py`; non usare il
wiring V1 `web/app.py`.

**Interfaces:** `ConfigurationTestService(configuration, runner, clock)` con
`async run(revision: int, audio: Path) -> TestView`, `result(test_id: str) -> TestView | None`.
`TestView` contiene id, revision, status (`running|succeeded|failed`),
`result: ProcessingResult | None`, `safe_error: str | None`; testo solo in memoria.
API autenticata: `GET/PUT /api/transcription/draft`, `POST /api/transcription/test`,
`GET /api/transcription/tests/{id}`, `POST /api/transcription/activate`,
`POST /api/transcription/restore`; `/api/transcription/models` per servizio/ruolo/versione;
`POST /api/transcription/credentials` restituisce solo versione e stato configurato.

- [x] Scrivere test su runner/snapshot esatto, bozza cambiata durante la prova, prova parziale, credenziale sostituita durante e dopo la prova, scadenza memoria, auth/CSRF/conflitto revisioni, credenziali mai restituite, cleanup upload e testo XSS reso inerte.
- [x] Implementare i contratti con snapshot prima della prova, ricevuta solo dal server e soltanto per percorso completo. Una sola prova può essere in corso; upload limitato a `min(max_file_size_mb, 20)` e salvato sotto `AUDIO_DIR/tmp`, rimosso in `finally`. Il runner condiviso riceve lo snapshot immutabile.
- [x] La prova parte solo su azione esplicita; il testo resta in memoria per 600 secondi e il DB conserva solo ricevuta tecnica. Esiti parziali mostrano il testo ma non abilitano l'attivazione.
- [x] Verifiche mirate con bootstrap dotenv e suite V2 completa: PASS (vedi registro). Commit non creato: `.git` è read-only; gestione al coordinatore.

### Task S4.2: interfaccia scelta e primo setup completo

**Files:** creare `bot/web/templates/v2_transcription.html`,
`bot/web/static/transcription.js`, `tests/v2/test_transcription_ui.py`;
integrare la route in `v2_app.py` e gli stili V2 in `static/style.css`; aggiungere le evidenze
browser sintetiche in `docs/audits/2026-10-25-v2-ui-validation.md`.

**Interfaces:** consumare solo API S4.1. `GET /admin/transcription` è il compito
principale; `/admin/dashboard` mostra attiva, stato del bot e problemi.
Il setup rimanda agli stessi campi e API di configurazione dopo associazione.

- [x] Test UI separano attivo/bozza/prova, espongono entrambe le modalità e controllano che l'output sintetico sia inserito con `textContent`.
- [x] Implementare navigazione verde scuro e area di lavoro chiara, riepilogo attivo, bozza e prova affiancati; su mobile ordine riepilogo → modulo → prova. Prompt e limiti in avanzate; ID modello mostrato e modificabile; nessun segreto in storage browser.
- [x] Collegare catalogo compatibile, ID manuale, upload limitato, risultato, modelli/durata/costo, attivazione e ripristino. Stati attivo e bozza restano distinti; azioni disabilitate durante operazioni e conflitti visibili.
- [ ] Verifica visuale reale a 1440×900 e 390×844, stati tastiera e catture: non eseguita, non è disponibile Chromium/Firefox né Playwright/Selenium nel workspace. Test route/template ASGI passati; evidenze e limite registrati in `docs/audits/2026-10-29-v2-ui-validation.md`.
- [ ] Commit: `.git` read-only in questo ambiente; in carico al coordinatore.

## Chiusura della sessione

- [x] Aggiornare registro con evidenze API e limite browser. Il setup fino all'attivazione è coperto da test di servizio; il percorso audio Telegram arriva in S5.
- [x] Indicare S5 come prossimo passo.

## Estensione amendment 29 ottobre — lavoro futuro, storico S4 invariato

S4.1/S4.2 e le evidenze di UI e API restano riferite alla configurazione singola realizzata. L'amendment richiede un nuovo catalogo; le checkbox aggiunte qui sono aperte e non trasformano la verifica browser sintetica esistente in verifica del nuovo catalogo.

### Task S4.3: catalogo profili, prova e assegnazione ruoli

**Dipendenze:** S1.3 (contratti/repository multi-profilo) e S6.4 (migrazione SQLite V2); integrare la revisione di pagina solo dopo che i nuovi servizi sono stabili.

**Files:** estendere template, route e JavaScript V2 di trascrizione; aggiungere test API/template e aggiornare la direzione UI effettivamente costruita. Conservare il percorso corrente fino alla migrazione compatibile.

**Interfaces:** catalogo amministrativo iniziale con card per nome, provider, modalità, modelli, ruolo, stato di verifica e ultima prova. Azioni CRUD con conferme e conflitti espliciti. La prova accetta upload o campione incluso e genera ricevuta solo per il completamento valido della revisione e credenziale esatte. “Attiva senza prova” richiede conferma esplicita, non crea ricevuta e mostra **Non verificato**. Modifiche alla configurazione invalidano la verifica della revisione nuova.

- [x] Aggiungere scenari sintetici per crea, rinomina, modifica, duplica, cancella, conflitti e divieto di eliminare principale/fallback assegnati; non duplicare segreti, ricevute o stato di verifica.
- [x] Implementare selezione atomica del principale e fallback opzionale, mostrando con chiarezza i ruoli e lo stato non verificato; nessun cambio implicito di ruolo alla creazione.
- [x] Integrare prova di upload e prova del campione incluso con lo stesso runner e adapter effettivi, senza inviare al provider il testo atteso. Scegliere un campione piccolo con provenienza/licenza documentata e checksum; garantire cleanup temporaneo.
- [x] Verificare che upload e campione non persistano audio o testo oltre ai limiti V2 e che la ricevuta sia valida solo per profilo/revisione/credenziale/validator corrispondenti.
- [ ] Verificare tastiera, focus, messaggi di errore/conferma e layout responsive con dati sintetici; distinguere test automatici, verifica browser reale e gate S7 già pendenti.
- [ ] Aggiornare il registro come nuova attività amendment completata solo dopo migrazione e verifiche pertinenti; lasciare intatto lo stato storico S4.1/S4.2.

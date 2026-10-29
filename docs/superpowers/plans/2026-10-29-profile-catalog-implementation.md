# Catalogo profili, prove e fallback — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** permettere all’operatore di conservare configurazioni complete in un catalogo, assegnare un principale e un fallback, e osservare l’uso dei profili senza perdere le garanzie V2.

**Architecture:** sostituire il riferimento globale draft/active/previous con identità profilo, revisioni immutabili e ruoli globali; ogni richiesta acquisisce uno snapshot immutabile. Catalogo e prove usano le API V2 e il runner esistente. Il fallback runtime applica la policy confermata: timeout/rete/429/5xx, fallback con prova valida, output vuoto/non valido escluso.

**Tech Stack:** Python 3.10+, SQLite, FastAPI/Jinja2, JavaScript leggero, pytest, runner/adapter/provider già presenti.

**Spec:** [amendment catalogo profili e fallback](../specs/2026-10-29-profile-catalog-and-fallback-amendment.md) e [specifica V2 approvata](../specs/2026-10-25-telegram-audio-bot-v2-design.md).

## Global Constraints

- I profili sono globali all’installazione; supportare solo OpenAI, Gemini, OpenRouter e i due percorsi audio esistenti.
- Conservare al massimo un ruolo principale e uno fallback; nessun profilo può ricoprire entrambi.
- Non serializzare segreti nelle revisioni; memorizzare solo riferimenti a credenziali versionate.
- Prove legate a profilo, revisione, versione credenziale e validator; il testo del risultato resta in memoria per la normale finestra breve.
- La prova campione usa il runner reale, non chiama provider reali nei test, e non persiste audio o testo atteso.
- Diagnostica e notifiche contengono solo metadati normalizzati; mai audio, trascrizioni, segreti, prompt privati o eccezioni grezze.
- Preservare ACL, cleanup temporaneo, lease, limiti complessivi, privacy, runtime Docker non-root e isolamento V1/V2.
- Policy fallback confermata dall’utente: un solo tentativo per timeout/rete/429/5xx; il fallback richiede prova valida; output vuoto/non valido non attiva fallback.
- La funzionalità non chiude né sostituisce i gate S7 di `docs/V2-VALIDATION.md`; la release non è dichiarabile pronta per il solo completamento del catalogo.

## Review Focus

1. Due schede salvano in concorrenza lo stesso profilo: conflitto esplicito e nessuna revisione sovrascritta (Sessione 2).
2. Una richiesta in corso attraversa un cambio di ruoli o eliminazione: snapshot/lease iniziali restano validi e la cancellazione è rifiutata o differita (Sessioni 2 e 5).
3. Una credenziale ruota mentre si testa una revisione: nessuna ricevuta vecchia verifica la nuova versione (Sessione 2).
4. Il campione fallisce in conversione, runner o timeout: file temporanei puliti e nessuna ricevuta riuscita (Sessione 3).
5. Invio o deduplica di un avviso fallisce: trascrizione e consegna non cambiano, diagnostica resta priva di contenuti (Sessione 5).

## Sequenza delle sessioni

Una sessione per chat. Il piano di persistenza/migrazione è prerequisito tecnico e documentale della Sessione 1. Prima di modificare codice, aggiornare l’indice V2 e riconciliare i piani S1/S4/S5/S6/S7 e i contratti condivisi con questa amendment; non ripetere discovery o ridefinire la UI già approvata. Aggiornare `docs/V2-PROGRESS.md` con prove sintetiche a ogni sessione.

| Sessione | Dipendenze | Risultato verificabile |
| --- | --- | --- |
| 0 — Riconciliazione | Amendment + piano persistence | Indice, contratti e piani esistenti indicano il nuovo modello; migration separata e S7 gate esplicito |
| 1 — Schema e migration | Sessione 0; `2026-10-29-profile-persistence-plan.md` | DB temporaneo esistente migra in modo atomico e conserva configurazione/receipts compatibili |
| 2 — Dominio catalogo e ruoli | Sessione 1 | CRUD, revisioni, prove, verifica derivata, ruoli atomici e lease testati su SQLite temporaneo |
| 3 — Catalogo web e campione | Sessione 2 | UI responsive/accessibile; CRUD/ruoli; prova upload o sample tramite runner reale e cleanup |
| 4 — Diagnostica profili | Sessione 2 | Eventi distinguono profilo/revisione primari e fallback ed esito, senza dati contenuto |
| 5 — Policy fallback runtime | Sessioni 2 e 4 | Un solo tentativo secondo policy confermata; notifica Telegram best-effort e deduplicata |
| 6 — Documentazione e gate S7 | Sessioni 1–5 | README, CHANGELOG, AGENTS, validazione e piano S7 aggiornati; gate reali S7 ancora esplicitamente aperti se non eseguiti |

Le Sessioni 0–4 preparano i contratti necessari alla Sessione 5. Le tre policy sono state confermate e registrate nella specifica e nel piano S5 durante la preparazione. Implementare categorie tipizzate, mai inferite da error text.

## Contratti condivisi

- `ProfileSummary`: `id`, `name`, `created_at`, `updated_at`, `role` (`main`/`fallback`/`None`), `current_revision`, `service`, `mode`, `audio_model`, `text_model`, `verification_status`, `last_test_at`, `last_test_success`.
- `ProfileRevision`: identificatore immutabile globale, `profile_id`, `ConfigInput`, versione credenziale e timestamp; nessuna credenziale in chiaro.
- `ProfileRoleSnapshot`: revisione principale obbligatoria e revisione fallback facoltativa, entrambe acquisite in una singola lettura transazionale all’inizio dell’handler.
- `ProfileRestoreState`: puntatore al principale precedente, aggiornato nella stessa transazione dell’assegnazione main e usato dal ripristino sicuro; non rappresenta un terzo ruolo corrente.
- `ProfileService` in `bot/transcription/profiles.py`: `list_profiles()`, `get_profile(profile_id)`, `create_profile(name, values)`, `save_revision(profile_id, expected_revision, name, values)`, `duplicate_profile(profile_id, name)`, `delete_profile(profile_id)`, `assign_role(role, profile_id | None, expected_role_revision)`, `snapshot_roles()`.
- `ConfigurationTestService.run(profile_id, revision_id, audio: Path) -> TestView`: rifiuta revisioni non correnti e receipts stantie; produce receipt solo per risultato completo e credenziale invariata.
- Sessione 5: funzione che riceve `ProfileRoleSnapshot` e `ServiceFailure` classificato; al massimo una chiamata runner al fallback, nessun rimbalzo/ricorsione. Il fallback è eleggibile solo con receipt valida per revisione/credenziale/validator correnti.

I nomi sono contratti del piano, da mantenere in tutte le sessioni o correggere esplicitamente in questo file prima di implementare i consumatori.

## Mappa dei file

- `docs/superpowers/plans/2026-10-29-profile-persistence-plan.md`: piano separato per schema, migration e compatibilità.
- `bot/transcription/models.py`, `errors.py`: tipi snapshot/esito ed errori categorizzati secondo la policy confermata.
- `bot/transcription/profiles.py`: catalogo, revisioni, ruoli, concorrenza e snapshot.
- `bot/database/v2_schema.py`, `v2_repository.py`: DDL/migration come dettagliati nel piano persistence; riferimenti e lease.
- `bot/transcription/configuration.py`, `testing.py`: mantenere le validazioni esistenti, collegare receipts a revisioni profilo e supportare prove senza richiedere attivazione.
- `bot/transcription/runner.py`, `adapters/{base,openai,gemini,openrouter}.py`, `pool.py`, `resilience.py`: nessuna modifica per catalogo base; classificazione tipizzata nella Sessione 5.
- `bot/services.py`, `bot/handlers/v2_audio.py`: wiring del servizio; snapshot e lease su entrambe le revisioni e credenziali acquisiti atomicamente all’inizio richiesta nella Sessione 5.
- `bot/web/v2_app.py`, creare o adattare `bot/web/routes_transcription.py`; `v2_transcription.html`; `transcription.js` e CSS: endpoint autenticati/CSRF e UI catalogo.
- Creare `bot/web/static/profile_catalog.js` solo se separare il flusso evita di estendere ulteriormente lo script esistente; mantenere gli endpoint sotto router autenticato V2.
- Creare `bot/transcription/assets/sample.*` solo dopo verifica compatibilità/licenza; documentare provenienza/licenza/checksum nel README o in un audit dedicato.
- Test assegnati per sessione sotto `tests/v2/`; usare `tmp_path`, fixture sintetiche e mock, mai database o provider reali.
- README, CHANGELOG, AGENTS, indice e `docs/V2-PROGRESS.md` si aggiornano nelle sessioni proprietarie.

## Sessione 0 — Riconciliare piani e contratti

**Files:** `docs/superpowers/plans/2026-10-25-v2-index.md`, piani S1/S4/S5/S6/S7, `docs/V2-HANDOFF.md`, `docs/V2-PROGRESS.md`, `bot/transcription/models.py` solo per documentare il contratto futuro (nessuna implementazione).

- [ ] Segnare nella mappa V2 che il modello single-config storico è sostituito solo nell’ambito amendment; esplicitare dipendenza dal piano migration separato.
- [ ] Aggiornare S1 e S6 per puntare alla migration SQLite del piano persistence; S4 e S5 per catalogo e snapshot ruoli; S7 per mantenere tutti i gate di rilascio.
- [ ] Scrivere contratti condivisi in questo piano e nei piani proprietari, includendo campi identity/revision/credential e stato verificato derivato.
- [ ] Aggiornare l’amendment e il piano S5 per registrare la policy utente confermata; verificare con `rg` che nessun documento la lasci aperta o dichiari fallback/release già implementati.

## Sessione 1 — Schema e migration

**Files:** seguire esclusivamente `2026-10-29-profile-persistence-plan.md`.

- [ ] Completare ogni task e test del piano migration su DB temporanei: nuovo DB, v3 con active/draft/previous, receipts compatibili, rollback e ripetizione idempotente.
- [ ] Rieseguire i test repository/configuration/migration pertinenti col bootstrap sicuro pytest descritto in `CONTRIBUTING.md`; nessun database operativo.
- [ ] Salvare stato e output sintetico in `docs/V2-PROGRESS.md`; non avviare UI finché schema e query di lettura profili non sono stabili.

## Sessione 2 — Catalogo, revisioni e ruoli

**Files:** creare `bot/transcription/profiles.py`; modificare `models.py`, `configuration.py`, `v2_repository.py` solo per integrazione API e `tests/v2/test_profiles.py` (nuovo), `test_configuration.py`, `test_repository.py`.

- [ ] Scrivere test `test_create_profile_does_not_change_main_role`, `test_revision_save_preserves_history_and_invalidates_verification`, `test_receipt_is_bound_to_profile_revision_credential_and_validator`, `test_assign_main_and_fallback_atomically_rejects_same_profile`, `test_main_can_be_assigned_unverified_only_with_explicit_confirmation`, `test_restore_main_swaps_previous_role_atomically`, `test_delete_requires_unassigned_role`, `test_duplicate_copies_values_without_roles_receipts_or_secret`, `test_stale_profile_write_conflicts`, `test_snapshot_roles_is_one_consistent_pair`, `test_inflight_lease_prevents_pruning_referenced_revision`.
- [ ] Verificare che i nuovi test falliscano per interfacce assenti o comportamento single-profile.
- [ ] Implementare `ProfileService` con transazioni SQLite; nome univoco normalizzato; modifiche/rinomine come nuove revisioni immutabili; duplicazione senza ruoli né receipts e solo riferimento credenziale compatibile, altrimenti errore esplicito che richiede selezione.
- [ ] Implementare verifica derivata da receipt esatta e validator attuale; nessun booleano mutabile di stato verificato; distinguere `verified`, `unverified`, `retest_required`.
- [ ] Implementare ruolo main/fallback atomico con `expected_role_revision`; main senza prova richiede `allow_unverified=True` da chiamante esplicito e non crea receipt. Il ruolo fallback può essere assegnato, ma il runtime lo considera eleggibile solo con receipt valida per revisione/credenziale/validator correnti.
- [ ] Implementare lease/reference count per profili/revisioni richiesti; delete di ruolo assegnato rifiutato; delete di revisioni in uso differito; pruning conserva tutti i profili salvati.
- [ ] Verificare i test del file nuovo e i test di configurazione/repository interessati; aggiornare contratti e progress.

## Sessione 3 — Catalogo web e campione audio

**Files:** `bot/web/v2_app.py` o nuovo `bot/web/routes_transcription.py`, `v2_transcription.html`, `transcription.js` (eventuale `profile_catalog.js`), CSS esistente, `bot/transcription/testing.py`; creare sample/licenza/checksum; `tests/v2/test_profile_routes.py` e `test_profile_catalog_ui.py`.

- [ ] Test route: tutte le API CRUD/ruolo richiedono login e CSRF per mutazioni; conflitti restituiscono 409; risposta non contiene credenziali o testo prova se non nell’endpoint breve protetto esistente.
- [ ] Test UI: card distinguono ruolo, provider/percorso/modelli, stato e ultima prova; azioni disponibili da tastiera/mobile; conferme accessibili per delete e main senza prova; conflitto conserva il lavoro locale e propone reload.
- [ ] Implementare catalogo iniziale con crea, rinomina/modifica, duplica, elimina, assegna main/fallback e conferma esplicita per “Attiva senza prova”; mostrare stato non verificato nella card e nell’area attiva.
- [ ] Creare sample breve di 1–4 secondi, provenienza/licenza documentata e checksum registrato; nessun fetch runtime. Non inserire il testo atteso nell’audio inviato al provider.
- [ ] Estendere test service per ricevere `profile_id` e `revision_id`; implementare scelta esplicita sample/upload nello stesso percorso runner. Garantire cleanup in `finally` anche per errore/timeout/cancellazione e tenere risultato in memoria fino a dieci minuti.
- [ ] Test `test_bundled_sample_uses_production_runner`, `test_sample_path_is_cleaned_after_failure_or_cancel`, `test_sample_expected_text_never_sent_or_persisted`, `test_unverified_activation_has_no_receipt`, `test_prove_and_activate_requires_exact_revision_credential_validator` con runner/provider mock e tmp path.
- [ ] Eseguire test di route/UI/test service e una verifica visuale desktop/mobile secondo direzione già approvata; aggiornare README/CHANGELOG/AGENTS per nuove operazioni utente.

## Sessione 4 — Diagnostica profili

**Files:** `bot/diagnostics.py`, repository/schema secondo il piano persistence se richiede colonne nuove, `bot/web/routes_diagnostics.py`, template diagnostica, `tests/v2/test_diagnostics.py`, `test_diagnostics_routes.py`.

- [ ] Test eventi primary-only, fallback-selected-success, fallback-failed: campi normalizzati includono ID/nome snapshot, revisioni, fase, categoria e risultato; nessun body, audio, trascrizione, prompt o token.
- [ ] Test pruning mantiene massimo 100 eventi/sette giorni anche con i nuovi campi; i nomi sono valori configurati validati/limitati e non testo provider.
- [ ] Aggiornare `DiagnosticEvent`/API/UI e health per stato main e fallback; la verifica UI deriva da receipt corretta e non da una receipt storica di altro profilo.
- [ ] Eseguire test diagnostici e acceptance; documentare gli eventi e gli invarianti privacy in `docs/V2-VALIDATION.md` senza dichiarare gate reali eseguiti.

## Sessione 5 — Fallback runtime secondo policy confermata

**Policy confermata:** un solo tentativo automatico per timeout/rete/429/5xx; fallback utilizzabile solo se ha una prova valida per revisione, credenziale e validator correnti; output vuoto/non valido non attiva fallback. Nessun retry per credenziali/configurazione invalide, formati non supportati, 4xx permanenti, errori funzionali o qualità dubbia.

**Files:** `bot/transcription/errors.py`, adapter provider, `resilience.py`/`runner.py`, `bot/handlers/v2_audio.py`, `bot/services.py`, `bot/diagnostics.py`, notifiche Telegram e test relativi.

- [ ] Testare prima la classificazione tipizzata per timeout, rete, 429, 5xx, credenziale/configurazione, 4xx permanente, formato, output vuoto/non valido e risultato parziale; solo timeout/rete/429/5xx producono categoria fallback-eligible, nessun test basa il comportamento su stringhe eccezione.
- [ ] Testare acquisizione atomica dello snapshot primario/fallback e dei lease di entrambe le revisioni e credenziali attraverso admission/run; cambio ruolo durante richiesta non cambia gli snapshot.
- [ ] Implementare un solo tentativo sul profilo fallback completo solo per timeout/rete/429/5xx e solo se la sua receipt è valida per revisione, credenziale e validator correnti. Fallback assente/non eleggibile/fallito termina col messaggio sicuro; non combinare fasi/profili, non ricorsivo, timeout complessivo rispettato.
- [ ] Testare che i casi esclusi non invocano fallback, che l’errore del fallback non crea un secondo tentativo e che il fallback riceve la coppia revisione/credenziale catturata all’inizio.
- [ ] Inviare al proprietario notifica deterministica best-effort dopo selezione effettiva: timestamp, nomi profili, fase/categoria normalizzata, esito. Deduplicare/rate-limitare; testare fallimento o ritardo dell’invio senza cambiare trascrizione/delivery.
- [ ] Testare diagnostica e notifiche per assenza di contenuti/credenziali/body error e comportamento concorrente; testare cleanup locale/remoto su tutti i rami.
- [ ] Aggiornare README, CHANGELOG, AGENTS, `docs/V2-VALIDATION.md` e piano S5 con la policy confermata e i limiti di retry/costo.

## Sessione 6 — Documentazione e riesame S7

**Files:** README, CHANGELOG, AGENTS, `docs/V2-VALIDATION.md`, piani S1/S4/S5/S6/S7, `docs/V2-HANDOFF.md`, `docs/V2-PROGRESS.md`.

- [ ] Aggiornare istruzioni installazione, catalogo, sample, attivazione non verificata, fallback e privacy secondo ciò che è stato effettivamente implementato.
- [ ] Eseguire suite V2 mirata e suite completa usando il bootstrap di `CONTRIBUTING.md`, oltre a `compileall` e `git diff --check`; riportare risultati senza invocare provider/Telegram reali.
- [ ] Riesaminare ogni gate elencato in S7 e `docs/V2-VALIDATION.md`; marcare solo quelli con evidenza effettiva. Il catalogo non chiude browser operativo, provider reale, Telegram reale, deployment/rollback o altri gate S7.
- [ ] Aggiornare progress con prove, limiti e prossima azione precisa; fermarsi dopo la sessione e lasciare S7 non completo se restano gate.

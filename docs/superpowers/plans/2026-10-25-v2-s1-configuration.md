# V2 S1: configurazione persistita — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** costruire la fonte unica di configurazione, verificabile senza Telegram o API AI.
**Architecture:** repository SQLite V2 distinto, dataclass immutabili e servizio con transazioni e revisione attesa. Riutilizzare Fernet di `SecretStore`, senza inizializzare il repository V1.
**Tech Stack:** Python 3.10+, sqlite3, cryptography, pytest.
**Spec:** [specifica](../specs/2026-10-25-telegram-audio-bot-v2-design.md), sezioni 7 e 11.

## Global Constraints

Applicare tutti i [vincoli e contratti comuni](2026-10-25-v2-index.md).
Solo database e chiavi temporanei. La V1 continua a usare il suo avvio fino a S3.

## Review Focus

Template con parentesi graffe e concorrenza fra schede: task S1.2.
Cifratura indisponibile e conservazione dei riferimenti: task S1.1.

### Task S1.1: schema minimo e credenziali versionate

**Files:** creare `bot/transcription/__init__.py`, `models.py`, `errors.py`,
`bot/database/v2_schema.py`, `v2_repository.py`, `tests/v2/conftest.py`,
`tests/v2/test_repository.py`; leggere `bot/database/secret_store.py` per riuso.

**Interfaces:** produrre i tipi comuni e `V2Repository` dell'indice. Il repository
espone anche `transaction() -> ContextManager[sqlite3.Connection]`; nessun commit
intermedio dentro una transazione di configurazione. Lo schema distingue settings,
setup state, ACL, credenziali, revisioni, riferimenti e ricevute; pairing e diagnostica
arrivano nei task proprietari, senza tabelle placeholder.

- [ ] Scrivere `test_reopen_preserves_configuration_and_encrypted_credentials`: riaprire il DB conserva i dati; nei campi memorizzati manca la chiave fittizia in chiaro; versione 1 e 2 rimangono leggibili separatamente dal solo repository.
- [ ] Scrivere `test_unavailable_cipher_rejects_write`, `test_v1_database_is_rejected_without_changes` e `test_transaction_rolls_back`: nessun downgrade, nessuna modifica allo schema V1 sintetico, nessuna scrittura parziale.
- [ ] Eseguire `python -m pytest tests/v2/test_repository.py -q` col bootstrap sicuro dell'indice. Atteso: fallimento per contratti assenti, non per accesso a file reali.
- [ ] Implementare tipi e schema versionato idempotente. Revisioni e credenziali hanno identificativi monotoni; DB V2 con marker proprio; tutte le query parametrizzate. `put_credential` cifra prima di scrivere; `read_credential` è interno e non entra nelle rappresentazioni pubbliche.
- [ ] Implementare fixture `repo`, `secret_store`, `config_values`, `ready_prerequisites` e clock controllabile: solo `tmp_path`, credenziali fittizie, nessuna costruzione implicita di `Config` legacy.
- [ ] Rieseguire il file: tutti PASS. Verificare inizializzazione e riapertura anche senza credenziali.
- [ ] Committare solo i file del task: `feat: add V2 configuration repository and encrypted credential versions`.

### Task S1.2: bozza, invalidazione e attivazione atomica

**Files:** creare `bot/transcription/configuration.py`, `prompts.py`,
`tests/v2/test_configuration.py`; modificare `v2_repository.py` per operazioni
atomiche; aggiornare AGENTS e CHANGELOG per i nuovi moduli, senza dichiararli attivi.

**Interfaces:** consumare S1.1; produrre `ConfigurationService` dell'indice e
`render_refinement(template: str, raw_text: str) -> str` in `prompts.py`.
Validator corrente: `VALIDATOR_VERSION = 'v2-1'`. `restore_previous` scambia
attiva/precedente solo con prerequisiti validi e una precedente disponibile.

- [ ] Scrivere `test_activate_requires_matching_successful_receipt`: prima senza ricevuta → `TestRequired`; ricevuta giusta → attiva; successiva prova fallita della stessa revisione impedisce una nuova attivazione.
- [ ] Scrivere `test_stale_write_and_late_test_cannot_activate_new_draft`: due scritture con la stessa revisione attesa → una sola riesce; esito vecchio o validator diverso non attiva la nuova bozza.
- [ ] Scrivere `test_each_material_change_invalidates_test` parametrizzato per servizio, chiave, modello, modalità e prompt; `test_last_active_and_previous_survive_failed_activation`; `test_secret_gc_keeps_active_previous_draft_and_leases` usando riferimenti simulati alle richieste in corso.
- [ ] Scrivere `test_literal_raw_text_substitution`: testo `'{nome}: no, 12'` conserva le graffe; template senza `{raw_text}` è rifiutato; nessun altro frammento è interpretato come campo di formattazione.
- [ ] Eseguire `python -m pytest tests/v2/test_configuration.py -q`: atteso FAIL per servizio assente.
- [ ] Implementare firme dell'indice, validazione dei ruoli e modello testo obbligatorio solo in `two_stage`. Un'unica transazione verifica revisione, versione credenziale, validator e prerequisiti letti dal servizio; poi sposta i riferimenti. Nessuna prova fornita dal browser come “successo”. Pruning conserva anche le versioni in uso tramite lease, la cui integrazione arriva in S2.
- [ ] Rieseguire `tests/v2/test_repository.py` e `tests/v2/test_configuration.py`: PASS; nessuna modifica al percorso V1.
- [ ] Committare: `feat: add revision-checked draft testing and activation`.

## Chiusura della sessione

- [ ] Aggiornare registro con commit, comandi ed esiti. S1 completa solo quando riapertura, conflitti e attivazione sono verificati.
- [ ] Indicare come prossima sessione S2; fermarsi senza iniziare gli adapter.

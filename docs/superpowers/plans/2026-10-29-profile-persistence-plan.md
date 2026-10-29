# Persistenza catalogo profili e migrazione SQLite — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ] ) syntax for tracking.

**Goal:** migrare in modo atomico lo schema V2 single-config a una persistenza multi-profilo, mantenendo i valori, le credenziali referenziate, le prove compatibili e una strada di rollback verificabile.

**Architecture:** aggiungere schema V2 versionato e un migratore transazionale v3→v4 che proietta le revisioni/config references esistenti in un profilo predefinito. Conservare le tabelle legacy necessarie fino al superamento dei gate S7 e definire un mapping esplicito per ripristino; tutte le prove usano repository SQLite temporanei.

**Tech Stack:** Python 3.10+, SQLite (sqlite3), pytest.

**Spec:** amendment catalogo profili e fallback, sezione “Persistenza e migrazione”; specifica V2 approvata.

## Global Constraints

- Non aprire o migrare database/deployment reali durante sviluppo; usare solo fixture sintetiche e tmp_path.
- Migrazione transazionale, idempotente e con failure rollback senza scritture parziali.
- Conservare segreti cifrati solo nello store esistente; profili/revisioni conservano riferimenti servizio+versione, mai valori segreti.
- Conservare identità e configurazioni salvate anche quando non sono main/fallback; non prunare revisioni referenziate da profili, prove mantenute, ruoli o richieste in corso.
- Mantenere l’isolamento V1/V2; nessun import automatico da file V1 o authorized.json.
- Non alterare né dichiarare superati i gate S7 con la sola migration.

## Review Focus

1. DB v3 con active, draft e previous che condividono o divergono per versione credenziale: migrazione mantiene i riferimenti corretti e main punta all’active (Task 2).
2. DB incompleto o receipt orfana/senza validator compatibile: migration non inventa prove e rollback non lascia schema mezzo aggiornato (Task 2).
3. Interruzione tra DDL, copia dati e marker versione: transaction rollback completo, ripetizione senza duplicati (Task 2).
4. Profilo migrato successivamente modificato o duplicato: mapping di rollback non confonde nuova revisione con il valore migrato originale (Task 3).
5. Lease attivo durante pruning/migration: nessuna revisione o credenziale richiesta in corso diventa indisponibile (Task 3).

## Contratti schema

- profiles: profile_id stabile (UUID/ULID testuale creato localmente), nome visualizzato e chiave nome univoca normalizzata, created_at, updated_at, current_revision_id nullable solo durante creazione/migration.
- profile_revisions: ID immutabile, profile_id FK, sequence monotona per profilo, tutti i valori ConfigInput, credenziale servizio/versione e timestamp. Segreto mai incluso.
- profile_roles: chiave ruolo limitata a main o fallback, FK univoca a profilo e contatore/versione ruolo per optimistic concurrency; constraint impedisce lo stesso profilo in entrambi i ruoli.
- profile_restore_state: puntatore al precedente principale (profilo e revisione) aggiornato atomicamente al cambio ruolo main, separato dai due ruoli correnti.
- profile_test_receipts: receipt ID, profile/revision FK, credential service/version, validator, esito, modelli normalizzati e timestamp; unique receipt ID e indici per ricerca ultima prova.
- profile_revision_leases o meccanismo repository equivalente: riferimenti temporanei per richieste in corso, rilasciati in finally; non basare la retention solo sui ruoli correnti.
- profile_migration_map: mapping dei riferimenti/revisioni legacy a profilo/revisione e snapshot necessari al rollback; nessun dato segreto.
- schema_version: passaggio da 3 a 4 eseguito una sola volta sotto BEGIN IMMEDIATE.

Le ricevute attive sono valide solo se service, credential version, profile revision e validator corrispondono. Stato verificato è query derivata, non colonna mutabile. La migration conserva le ricevute storiche compatibili, senza trasformare esiti mancanti/vecchi in successi.

## Mappatura v3 → profilo predefinito

- Creare un profilo “Configurazione predefinita” con ID stabile deterministico per il migration marker, senza cambiare configurazione attiva.
- Copiare ciascuna revisione ancora presente e referenziata da active/draft/previous in profile_revisions, conservando valori, sequence/source revision e credenziale service/version. Riutilizzare la credenziale cifrata già nello store.
- Mappare active al ruolo main. fallback resta assente. Mantenere previous in profile_restore_state come puntatore di ripristino al precedente main, senza assegnargli il ruolo fallback.
- Conservare la bozza come revisione salvata sul profilo senza renderla main; la UI catalogo potrà modificarla solo creando una nuova revisione. Se non esiste active, non assegnare un main fittizio.
- Copiare receipts solo quando la riga sorgente e la versione credenziale corrispondono alla revisione mappata; preservare validator/esito/timestamp e receipt ID. Una prova storica non trasferibile resta nel mapping legacy, non rende verified la nuova revisione.
- Registrare marker con version source, profile ID, source revision IDs e checksum logico dei riferimenti copiati; una seconda apertura v4 non ripete il copy.

## Task 1 — Schema v4 e migrazione dei DB vuoti

**Files:** bot/database/v2_schema.py, bot/database/v2_repository.py, tests/v2/test_repository.py.

- [ ] Scrivere test_new_database_initializes_profile_schema_v4: tabelle, FK, uniqueness main/fallback e marker schema sono presenti; nessun profilo o receipt bootstrap creato.
- [ ] Scrivere test_profile_schema_rejects_same_profile_in_two_roles e test_profile_name_normalized_unique; verificare i vincoli SQLite, non solo i validator Python.
- [ ] Scrivere test_profile_schema_forbids_secret_columns: introspezione schema mostra solo servizio/versione credenziale e nessun campo plaintext/secret.
- [ ] Implementare DDL v4 aggiuntivo; non riusare chiavi di unicità globali come reference_type per revisioni o ruoli multiprofilo.
- [ ] Eseguire python -m pytest tests/v2/test_repository.py -q nel bootstrap sicuro indicato da CONTRIBUTING.md; atteso PASS e test v3 esistenti ancora PASS.

## Task 2 — Conversione atomica v3 → v4

**Files:** bot/database/v2_repository.py, bot/database/v2_schema.py, tests/v2/test_repository.py, creare/estendere tests/v2/test_migration.py.

- [ ] Creare fixture helper per DB v3 sintetico con credentials cifrate sintetiche, due service/versioni e riferimenti active/draft/previous diversi; non usare database presenti in data_dir.
- [ ] Scrivere test_v3_upgrade_preserves_active_draft_previous_values_and_credentials: controlla ogni campo ConfigInput, mapping e ruolo main; fallback assente.
- [ ] Scrivere test_v3_upgrade_preserves_only_compatible_receipts: receipt successful/failed e validator storico restano tali; mismatch credential/revision non crea receipt valida.
- [ ] Scrivere test_v3_upgrade_is_idempotent: initialize, close, reopen e initialize non duplica profilo/revisioni/receipts né cambia ruoli.
- [ ] Scrivere test_v3_upgrade_rolls_back_ddl_data_and_version_on_failure: iniettare errore tra copia e marker; dopo errore il DB è esattamente v3 e può essere riaperto con migration retry.
- [ ] Scrivere test_unrecognized_or_partial_v3_schema_fails_closed: schema incompatibile non viene “riparato” in modo speculativo; nessuna tabella/dato mutato.
- [ ] Implementare _migrate_v3_to_v4(connection) chiamato solo da V2Repository.initialize() per marker V2/version 3, sotto singola transaction; proteggere tablename/colonne attese e commit marker per ultimo.
- [ ] Garantire la compatibilità v1/v2: percorso di upgrade concatenato a v3 e test esistente rimane PASS; se necessario migrare prima 1/2→3 e poi 3→4, ciascun passaggio transazionale e ripetibile.
- [ ] Eseguire python -m pytest tests/v2/test_repository.py tests/v2/test_migration.py -q con bootstrap sicuro; verificare anche test esistente su rifiuto DB V1 e import copy-only.

## Task 3 — Rollback controllato, retention e import legacy

**Files:** bot/database/v2_repository.py, bot/transcription/profiles.py (coordinato col piano catalogo), tests/v2/test_repository.py, tests/v2/test_profiles.py, docs/V2-MIGRATION.md solo nella sessione documentazione proprietaria.

- [ ] Definire rollback dei dati applicativi al vecchio modello: mapping conserva active/draft/previous originari; fallback non è reversibile in una sola active e quindi il rollback deve rifiutare o richiedere scelta esplicita, senza perderlo. Non eliminare strutture v3 durante questa milestone.
- [ ] Scrivere test_rollback_mapping_restores_original_single_config_refs prima di implementare il mapping; esito atteso conserva valori originali e non duplica secrets.
- [ ] Scrivere test_rollback_refuses_ambiguous_fallback_or_post_migration_changes: il rollback non sceglie silenziosamente quale profilo mantenere e non cancella profili/revisioni aggiunti.
- [ ] Scrivere test_pruning_preserves_profiles_roles_receipts_and_leases: delete di un profilo elimina solo dati non più referenziati; un lease attivo blocca il pruning; rilascio permette GC sicuro.
- [ ] Integrare la retention credenziali già presente in V2Repository.prune_credentials() con ogni riferimento a revisione profilo, receipt utile secondo retention e lease.
- [ ] Confermare che import_v1_payload() resti boundary esplicito e che la migrazione schema non chiami il lettore V1, non legge file V1 e non importa ACL/configurazioni V1 di propria iniziativa.
- [ ] Eseguire test repository, migration, configuration e profiles; aggiornare evidenza sintetica in docs/V2-PROGRESS.md. La documentazione utente/rollback completa è aggiornata nella sessione 6 del piano implementativo.

## Criterio di uscita

La persistenza è pronta per il catalogo quando: DB nuovo e DB v3 sintetico aprono v4; main riflette l’active migrato; fallback è vuoto; draft/previous e receipts compatibili sono preservati; conversione è idempotente e rollback transazionale testato; credenziali restano cifrate/referenziate; nessuna revisione in uso o profilo salvato viene potata. Questo criterio non dichiara il rilascio S7 pronto.

# V2 S6: migrazione e semplificazione — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** offrire un passaggio verificabile dalla V1 e rimuovere le fonti di complessità superate.
**Architecture:** importatore monouso fuori dal lifecycle, diagnostica a metadati limitati,
eliminazione dei vecchi flussi dopo che i sostituti sono coperti.
**Tech Stack:** SQLite, SecretStore esistente, FastAPI/Jinja2, pytest.
**Spec:** [specifica](../specs/2026-10-25-telegram-audio-bot-v2-design.md), sezioni 3, 11–12.

## Global Constraints

Applicare [indice](2026-10-25-v2-index.md). S1–S5 completate.
La migrazione si sviluppa e verifica solo su fixture; quella operativa viene
eseguita dall'utente sul proprio deployment. Nessun accesso dell'assistente a DB o chiavi reali.

## Review Focus

Sorgente uguale a destinazione, DB ambiguo, cifratura errata, attivazione implicita
e log contenenti body provider: test di proprietà sotto.

### Task S6.1: importazione monouso e rollback documentato

**Files:** creare `bot/migrate_v1.py`, `tests/v2/test_migration.py`,
`tests/v2/legacy_fixtures.py`, `docs/V2-MIGRATION.md`; leggere schemi e repository
V1 di sorgente, senza aprire dati reali. Modificare il repository V2 solo per
transazione d'importazione e marker di completamento.

**Interfaces:** `migrate_v1(source_db: Path, source_key: Path, target_db: Path, target_key: Path) -> MigrationReport`;
`MigrationReport(imported: dict[str, int], needs_reconfiguration: bool, warnings: tuple[str, ...])`.
CLI `python -m bot.migrate_v1 --source-db PATH --source-key PATH --target-db PATH --target-key PATH`.
Argomenti solo percorsi, nessun segreto nella command line; report senza valori sensibili.

- [ ] Scrivere `test_explicit_compatible_profile_imports_untested_draft`, `test_ambiguous_cross_service_and_fake_single_pass_require_reconfiguration`: mai configurazione attiva o ricevuta inventata.
- [ ] Scrivere `test_import_preserves_acl_admin_credentials_and_valid_limits`, `test_source_is_unchanged_and_opened_read_only`, `test_repeated_import_does_not_duplicate`, `test_wrong_key_or_same_path_fails_without_partial_target`.
- [ ] Eseguire `python -m pytest tests/v2/test_migration.py -q`: atteso FAIL.
- [ ] Implementare sorgente SQLite in sola lettura senza `DatabaseManager.initialize`; destinazione distinta e vuota, marker monouso. Trasferire ACL, hash amministrativo, credenziali ricifrate e limiti validi. Copiare solo stato necessario: niente inviti, cookie, codici scaduti, audit legacy o storico. Rifiutare override di percorso verso sorgente o destinazione già operativa; nessuna sincronizzazione inversa.
- [ ] Documentare arresto V1, backup coerente di volume/chiave/immagine, comando su copie scelto dall'operatore, avvio V2 e prova esplicita. Rollback: stop V2, immagine e volume V1 conservati, modifiche V2 non riportate indietro. Non leggere o importare file `.env`/JSON nell'assistente; non creare un nuovo bootstrap vivo.
- [ ] Rieseguire test migrazione: PASS, confronto byte della sorgente sintetica prima/dopo invariato. Committare: `feat: migrate V1 copies into an untested V2 draft`.

### Task S6.2: diagnostica utile senza contenuti

**Files:** creare `bot/diagnostics.py`, `bot/web/routes_diagnostics.py`,
`templates/diagnostics.html`, `tests/v2/test_diagnostics.py`; completare schema/repo
V2, `services.py`, runner, handler, `routes_admin.py`, dashboard e wiring web.

**Interfaces:** `DiagnosticEvent(at: datetime, stage: str, status: str, revision: int | None,
models: tuple[str, ...], duration_ms: int | None, error_code: str | None)`;
`DiagnosticsService.record(event: DiagnosticEvent) -> None`, `recent() -> list[DiagnosticEvent]`.
`GET /admin/diagnostics` e `GET /api/diagnostics`, solo amministratore.

- [ ] Scrivere `test_diagnostics_retains_at_most_100_events_and_7_days`, `test_error_body_text_and_credentials_never_persist`, `test_health_distinguishes_configured_running_and_verified_receiving`.
- [ ] Eseguire `python -m pytest tests/v2/test_diagnostics.py -q`: atteso FAIL.
- [ ] Implementare pruning alla scrittura e lettura, allowlist di campi e codici sicuri, azione utile per quota, credenziale, modello, formato, Telegram e problema temporaneo. Non mostrare “pronto” soltanto perché esistono righe di setup. Completare Panoramica e Diagnostica nella grammatica UI scelta.
- [ ] Rieseguire il file e test UI correlati: PASS; nessun contenuto nel DB o log catturati. Committare: `feat: expose bounded content-free V2 diagnostics`.

### Task S6.3: ritirare configurazione e UI V1 concorrenti

**Files:** ispezionare dipendenze e rimuovere, quando senza consumatori runtime,
`bot/{pipeline_resolver,model_picker,capabilities,config_service,runtime,state}.py`,
`bot/adapters/`, `web/{pipeline_builder,setup_wizard,routes_pipeline,routes_providers}.py`,
template pipeline/provider/setup_express. Ridurre `providers.py` e le factory
di `utils.py` ai soli helper ancora necessari oppure eliminarli se inutilizzati.
Aggiornare `database/__init__.py`, `auth_store.py`, `config.py`, test e documentazione
in base al grafo reale; creare `tests/v2/test_runtime_boundary.py`.

**Interfaces:** da qui il runtime usa solo V2Repository, ConfigurationService,
AccessService e runner. L'unico lettore dello schema V1 è `migrate_v1.py`.

- [ ] Scrivere `test_runtime_imports_no_legacy_resolution`, `test_removed_provider_and_pipeline_routes_are_not_registered`, `test_environment_cannot_override_active_models_or_prompts`; nessuna inizializzazione V1 durante avvio V2.
- [ ] Eseguire il nuovo file: atteso FAIL per vecchi riferimenti ancora presenti.
- [ ] Cercare con `rg` ogni consumatore prima di eliminare file. Spostare eventuali primitive utili in moduli focalizzati; non mantenere shim invisibili che risolvano ancora modelli dalla V1. I test di vecchie feature eliminate possono essere ritirati; portare invece i contratti utili di ACL, cleanup, limiti e segreti nei test V2. Non eliminare test solo per renderli verdi.
- [ ] Aggiornare AGENTS, README, CONTRIBUTING, CHANGELOG e roadmap distinguendo comportamento implementato da verifica reale ancora pendente. Nessun accesso a `.env.example` finché vige il divieto corrente AGENTS.
- [ ] Eseguire `python -m pytest tests/v2/test_runtime_boundary.py -q` e test dei componenti modificati: PASS. `rg` conferma nessun resolver legacy importato dai nuovi entrypoint. Committare: `refactor: retire legacy pipeline configuration from V2 runtime`.

## Chiusura della sessione

- [ ] Registrare cosa è stato rimosso, migrazioni verificate su fixture e limiti del rollback. Nessuna migrazione reale effettuata.
- [ ] Aggiornare registro, indicare S7 e fermarsi.

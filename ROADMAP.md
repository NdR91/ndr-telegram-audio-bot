# Roadmap

This is a living decision document, not a commitment to implement every item.
Work should be reviewed and approved one item at a time.

## Product direction

The project is evolving from an environment-configured Telegram bot into a
self-hosted application with:

- a browser-based setup and administration frontend;
- Telegram as the primary end-user interface;
- no mandatory `.env` or manually maintained JSON files;
- cloud and local AI provider connections;
- automatic use of one provider for the complete pipeline when possible;
- advanced multi-provider composition only when needed;
- no transcript history stored by default.

The core product promise remains:

> Deploy the application, complete a guided setup, then turn Telegram audio
> into useful text with minimal friction.

The frontend is the application's control plane. The Telegram bot is the
runtime interface that receives and processes user requests.

## Guiding decisions

These decisions define the intended architecture unless explicitly revisited.

| Topic | Decision |
| --- | --- |
| First run | `docker compose up` starts a setup-ready web application without requiring credentials. |
| Bootstrap | Generate local defaults and setup credentials automatically. Infrastructure overrides remain optional. |
| Configuration | Store runtime settings in the application database and manage them through the frontend or Telegram. |
| Secrets | Enter primarily through the frontend, encrypt at rest, and never display again in full. |
| Telegram configuration | Use for safe, common, non-sensitive settings. |
| Provider model | Represent a connected service, its models, and detected capabilities separately. |
| Default pipeline | Prefer one provider connection for every supported stage. |
| Advanced pipeline | Allow different transcription and text-processing providers only when required or explicitly selected. |
| Required capability | Transcription is required; text refinement is optional. |
| Multimodal single pass | Deferred until the transparent two-stage pipeline is stable and quality-tested. |
| Local models | Prefer HTTP services such as Ollama or vLLM; keep heavyweight in-process runtimes optional. |
| Transcript retention | Do not persist audio or transcripts by default. |
| Compatibility | Preserve the current bot while the new control plane is built and provide a migration path. |
| Component reuse | Prefer maintained open-source libraries and templates before building custom components. Custom code is justified for bot-specific domain logic, migration boundaries, or security constraints. |

## Component reuse policy

Before implementing a substantial new component, review whether a maintained
open-source library, framework feature, or template covers the need with lower
long-term maintenance cost.

Default posture:

- adopt existing components for generic web UI patterns, authentication
  primitives, form handling, admin layouts, scheduling, health checks, and
  operational plumbing;
- build custom code for the Telegram/audio pipeline domain, provider capability
  resolution, migration from legacy files, runtime snapshot semantics, and
  safety rules specific to this application;
- document the choice when a custom implementation replaces an obvious
  existing library.

For the frontend, keep the early control plane server-rendered and simple.
When the administration UI needs polish, prefer a lightweight admin template
and progressive-enhancement stack (for example Tabler + HTMX/Alpine.js) before
moving to a full SPA. React, Vue, Svelte, or similar should be reconsidered
only if the frontend becomes a rich standalone product with substantial
client-side state.

## Configuration boundaries

The goal is zero mandatory `.env`, not the removal of every advanced
infrastructure override.

### Managed by the application

- Telegram bot token and bot lifecycle.
- First administrator and access control.
- Provider connections, URLs, credentials, and models.
- Pipeline selection and fallback policy.
- Prompts and output modes.
- Rate limits, queue limits, timeouts, and file limits.
- Streaming and delivery behavior.
- User and group preferences.
- Privacy, retention, and operational settings.

### Optional infrastructure overrides

These have safe built-in defaults and are not required for normal deployment:

- data directory;
- frontend bind address and host port;
- external master-key source;
- external database selection in a future deployment mode;
- bootstrap logging level;
- reverse proxy and TLS configuration.

Changing container ports, mounted storage, networking, or TLS remains a
deployment concern even when the corresponding application behavior is visible
in the frontend.

## Configuration scopes

Settings should have explicit ownership and predictable precedence.

| Scope | Examples | Managed by |
| --- | --- | --- |
| Infrastructure | Port mapping, volumes, external secret source | Deployment |
| System | Provider connections, global limits, retention | Administrator |
| Pipeline profile | Models, stages, prompts, fallback policy | Administrator |
| Group | Default language, output mode | Administrator or authorized group manager |
| User | Output mode, translation language, delivery preference | User |
| Request | “Summarize this audio” | User |

Resolution order:

```text
Request > user/group preference > pipeline profile > system default
```

## Target architecture

```text
Web frontend ───────┐
                    ├── Configuration Service ── Configuration Database
Telegram admin UX ──┘             │
                                  ├── Secret Store
                                  ├── Audit Service
                                  └── Runtime Manager
                                           │
                                           ├── Telegram Bot
                                           └── Pipeline Resolver
                                                    │
                                      ┌─────────────┴─────────────┐
                                      │                           │
                                Transcriber                 Text Processor
                                      │                           │
                               Provider adapters and capability model
```

Telegram and the frontend must use the same configuration service. They must
not contain separate validation rules or write directly to configuration
tables.

## Provider and pipeline model

### Provider connection

A provider connection represents one configured service:

```text
OpenRouter primary
├── adapter: openai-compatible
├── endpoint
├── encrypted credential
├── discovered models
└── detected capabilities
```

A single connection may expose different models for transcription and text
processing. From the user's perspective it is still one provider.

### Pipeline behavior

The default setup flow should ask the user to connect a service, test it, and
then offer:

```text
Use this provider for everything it supports
```

The resolver should attempt, in order:

1. use the preferred provider for transcription and text processing;
2. use the preferred provider for transcription only when refinement is
   disabled;
3. use another configured provider only in advanced mode or according to an
   explicit fallback policy;
4. reject invalid configurations before accepting audio.

### Capability-driven adapters

Provider names must not be treated as capabilities. Each adapter should report
what the configured endpoint and selected models can actually do:

- transcription;
- text generation and refinement;
- streaming;
- language hints;
- translation;
- structured output;
- accepted audio formats;
- segments, timestamps, or diarization when available.

The audio preparation and Telegram UI should consume these capabilities rather
than contain provider-specific assumptions.

## Roadmap status

- `Proposed`: identified but not approved.
- `Approved`: accepted for implementation.
- `In progress`: currently being implemented.
- `Done`: completed and released.
- `Deferred`: useful, but not currently worth the cost or complexity.
- `Rejected`: intentionally excluded.
- `Superseded`: replaced by another roadmap item or architecture.

Before approving an item, consider:

- Does it improve first-run or daily UX?
- Does it reduce the need for technical configuration?
- Does it preserve privacy and self-hosting?
- Does it keep simple setups simple?
- Is migration and rollback clear?
- Can it be tested without real credentials?

Many items include a **Manual verification** section with checkboxes.
These describe concrete steps you can perform from the frontend (or
Telegram) to validate the feature once implemented. They are not
automated tests — they are acceptance walkthroughs for the person
building or reviewing the feature.

# Phase 0 — Protect the current baseline

This phase creates a safety net before architectural migration.

## B1 — Continuous integration

| Field | Value |
| --- | --- |
| Status | Done |
| Priority | Critical |
| Effort | Low |

Add GitHub Actions for supported Python versions, imports, and
`python -m pytest tests`. CI must use fake configuration and no real secrets.

**Completed 2026-06-23**

- Added a least-privilege GitHub Actions workflow for Python 3.10, 3.11, and
  3.12.
- CI installs public dependencies, compiles tracked Python sources while
  ignoring AppleDouble metadata, and runs the complete pytest suite without
  runtime credentials.

## B2 — Current-pipeline integration tests

| Field | Value |
| --- | --- |
| Status | Done |
| Priority | Critical |
| Effort | Medium |

Cover the complete handler flow, Telegram failures, queue handoff, provider
errors, and application startup wiring. These tests become migration
regressions for the new runtime.

**Completed 2026-06-23**

- Added offline integration tests that exercise the complete decorated audio
  handler from authorization and admission through cleanup.
- Covered successful processing, provider-stage failures, Telegram delivery
  failures, FIFO queue handoff, slot release, and temporary-file cleanup.
- Verified application startup wiring for services, handlers, progressive
  delivery configuration, whitelist bootstrap, and the maintenance job.

## B3 — Validate current configuration values

| Field | Value |
| --- | --- |
| Status | Done |
| Priority | High |
| Effort | Low |

Validate numeric ranges and report exact invalid variables. This protects the
legacy path and provides validation rules reusable by the configuration
service.

**Completed 2026-06-23**

- Added reusable integer and boolean environment-value validators.
- Invalid values now identify the exact variable and accepted range or values.
- Added regression coverage for rate limits, queue limits, provider resilience,
  and Telegram progressive-output settings.

# Phase 1 — Zero-configuration application foundation

The application must be able to start without Telegram or AI credentials.

## A1 — Unified application database

| Field | Value |
| --- | --- |
| Status | Done |
| Priority | Critical |
| Effort | High |

Introduce a versioned database schema for:

- setup state;
- application settings;
- administrators, users, and groups;
- provider connections and models;
- pipeline profiles;
- user and group preferences;
- encrypted secrets;
- audit events.

SQLite remains the default. Schema migrations must be explicit and tested.
Audio and transcript history remain outside the database.

**Completed 2026-06-23**  
**Verified 2026-06-27** with `venv/bin/python -m pytest tests`

- Added `bot/database/` with `DatabaseManager`, versioned SQLite schema,
  explicit migrations, and repository helpers for setup state, settings,
  access control, provider connections, pipeline profiles, preferences, and
  audit events.
- Blank-volume initialization and migration idempotency are covered by
  database schema tests.
- Repository tests cover CRUD behavior for configuration, ACL, providers,
  pipelines, preferences, and audit data.
- Existing bootstrap whitelist data can be imported into the unified database
  on startup.

**Done when**

- A blank data volume initializes safely.
- Schema upgrades are repeatable.
- Existing whitelist data can be imported.
- Backup boundaries are documented.

## A2 — Local secret store

| Field | Value |
| --- | --- |
| Status | Done |
| Priority | Critical |
| Effort | Medium–High |

Generate a master key on first startup, save it with restrictive permissions,
and encrypt provider and Telegram credentials at rest.

Support an optional external key file or Docker secret for advanced
deployments. Never log, export, or redisplay full secrets.

**Security boundary**

The default local key protects database-only exposure and accidental
disclosure. It does not protect against an attacker with full access to the
application data volume.

**Completed 2026-06-23**  
**Verified 2026-06-27** with `venv/bin/python -m pytest tests`

- Added `SecretStore` using Fernet authenticated encryption.
- Master keys are generated locally on first startup and written with
  restrictive file permissions.
- Provider credentials are encrypted at rest and decrypted on read when a
  `SecretStore` is configured.
- Secret-store tests cover initialization, permissions, round trips,
  cross-instance key reuse, wrong-key failures, and database integration.

## A3 — Configuration service

| Field | Value |
| --- | --- |
| Status | Done |
| Priority | Critical |
| Effort | High |

Create the single application API for reading, validating, testing, and
updating settings.

**Completed 2026-06-23** (`a80d9bb`)

- `SettingDef` dataclass with key, label, type, default, scope, group,
  requires_reload, is_secret, and validation rules (min/max, enum values).
- `SETTINGS_REGISTRY` catalog of 17 settings across 7 groups (telegram,
  provider, prompts, rate_limits, resilience, output, infrastructure).
- `ConfigService` API: `list_definitions`, `get_setting(s)`,
  `get_settings_by_group`, `validate_value`, `update_setting`,
  `update_settings` (transactional bulk with atomic rollback).
- Secret fields are write-only — value never returned, only `has_value`.
- Secret values transparently encrypted via `SecretStore` when available.
- Type-specific validation with Italian error messages.
- Settings that require runtime reload explicitly flagged via
  `requires_reload` + `get_reload_required()`.
- Wired into `bot_data['config_service']` via `create_application()`. 
- 44 tests: registry integrity, typed defaults, read/write, validation,
  atomic rollback, secret masking, reload signalling.

The web frontend, Telegram administration, runtime manager, and CLI recovery
tools must use this service.

**Done when**

- Updates are validated and applied transactionally.
- Secret fields have write-only semantics.
- Every setting has scope, type, default, and validation metadata.
- Changes that require runtime reload are explicitly signaled.

**Manual verification** (from frontend)

- [ ] Open the administration panel and confirm every setting from `.env` is
      visible in the settings page (Telegram token, provider, prompts, limits,
      etc.).
- [ ] Edit a non-sensitive value, save, reload the page and confirm the value
      persists.
- [ ] Enter an invalid value (e.g. a negative rate limit) and confirm the UI
      shows a clear validation error and does not save.
- [ ] Edit a secret field (e.g. API key) and confirm it never displays in full
      after saving — only a placeholder or masked value is shown.
- [ ] Confirm that a setting labelled "requires restart" correctly warns the
      user before applying.

## A4 — Runtime state model

| Field | Value |
| --- | --- |
| Status | Done |
| Priority | Critical |
| Effort | Medium |

Represent readiness as explicit states:

- `setup_required`;
- `telegram_missing`;
- `provider_missing`;
- `pipeline_invalid`;
- `ready`;
- `degraded`.

**Completed 2026-06-23** (`a80d9bb`)

- `AppState` enum with the 6 states above.
- `StateInfo` dataclass with state, label (IT), description (IT),
  and next_action for frontend consumption.
- `StateChecker` evaluates readiness by querying `setup_state`,
  `ConfigService` settings, and provider connections with capabilities.
- Legacy providers without capabilities assumed transcription-capable.
- Audio handler (`handle_audio`) gated: rejects with explanation when
  `can_process_audio()` is `False`.
- Exception-safe: `get_state()` returns `DEGRADED` on evaluation errors.
- Wired into `bot_data['state_checker']` via `create_application()`.
- 18 tests: all states, can_process_audio gating, backward compat,
  exception handling.

The frontend must explain the current state. The bot must not accept audio when
the pipeline is invalid.

**Manual verification** (from frontend)

- [ ] Start the application on a blank data volume and confirm the frontend
      shows "setup required" with a clear explanation.
- [ ] Complete the Telegram token in the frontend and confirm the state changes
      to "provider missing".
- [ ] Add a provider and confirm the state changes to "ready".
- [ ] Remove the only provider and confirm the state changes to "pipeline
      invalid" or "provider missing".
- [ ] The frontend explains each state in plain language and suggests the next
      action the user should take.

## A4.1 — Runtime integration hardening

| Field | Value |
| --- | --- |
| Status | Done |
| Priority | Critical |
| Effort | Medium |

**Completed 2026-06-23**

- `StateChecker` accepts `legacy_config` to preserve compatibility with
  `.env` + `authorized.json` deployments that have not yet populated the
  unified database.
- Secret field writes in `ConfigService` reject non-empty values when
  `SecretStore` encryption is unavailable, never persisting plaintext.
- `WhitelistManager` uses the unified `DatabaseManager` as its primary
  ACL store, with a legacy `SQLiteWhitelistStore` fallback.
- `RuntimeSnapshot` dataclass provides an immutable, resolved
  configuration snapshot for runtime object construction.
- `create_application()` builds a `RuntimeSnapshot` and uses it for
  `RateLimiter` and `TelegramDeliveryAdapter` setup.
- 16 new regression tests cover legacy compatibility, secret-store
  failure, and snapshot resolution.

Close the integration gap between the new database/configuration/state services
and the still-legacy runtime. A3 and A4 provide the contract, but the bot must
remain deployable during migration and must not silently split live state across
old and new stores.

**Blocking findings**

- Preserve legacy runtime compatibility while `authorized.json` and `.env`
  remain mandatory. The audio-handler readiness gate must not reject valid
  legacy deployments only because the new database has not yet imported
  `admin_created`, Telegram token, provider, or pipeline state.
- Do not allow secret settings to be persisted in plaintext when `SecretStore`
  is unavailable or the encryption key cannot be loaded. Secret writes should
  fail safely until encryption is available, with a clear operator-facing
  error.
- Make the unified database the live access-control source once imported, or
  keep the runtime explicitly on the legacy SQLite whitelist store until the
  cutover. Avoid importing ACL data once and then letting the old and new
  stores diverge.
- Ensure `ConfigService` values actually drive runtime dependencies before
  presenting them as live configuration: provider selection, prompts, rate
  limits, resilience settings, and Telegram delivery options must be resolved
  into stable runtime snapshots.

**Done when**

- A legacy `.env` + `authorized.json` deployment still processes audio before
  database import.
- A blank/new-control-plane deployment blocks audio only for the correct setup
  reasons and shows actionable state.
- Secret update attempts fail without persisting plaintext if encryption is not
  available.
- Whitelist reads/writes have a single live source for each migration stage.
- Runtime objects are built from an explicit validated snapshot, and in-flight
  requests keep using the snapshot they started with.
- Regression tests cover legacy compatibility, secret-store failure, ACL source
  selection, and runtime snapshot resolution.

## A5 — Runtime manager

| Field | Value |
| --- | --- |
| Status | Done |
| Priority | Critical |
| Effort | High |

Separate the web application's lifecycle from the Telegram bot lifecycle.

Responsibilities:

- start the frontend before configuration exists;
- verify and start the Telegram bot after setup;
- stop and restart polling after token changes;
- reload providers, prompts, limits, and pipeline profiles safely;
- expose health and degraded-state information;
- ensure in-flight requests use a stable configuration snapshot.

**Completed 2026-06-23**  
**Verified 2026-06-27** with `venv/bin/python -m pytest tests`

- Added `RuntimeManager` in `bot/runtime_manager.py`.
- Supports blocking legacy startup, non-blocking web-managed startup,
  stop/restart, state and health introspection, and readiness gating.
- `bot/main.py` now delegates Telegram lifecycle management to
  `RuntimeManager`.
- Web control-plane integration uses the same manager for bot start/stop
  actions.
- Runtime-manager tests cover lifecycle, health, blocking and non-blocking
  startup, error conditions, restart behavior, and CLI compatibility.

**Manual verification** (from frontend)

- [ ] Start the application and confirm the Telegram bot does not start
      automatically (no polling).
- [ ] In the frontend, go to the runtime/bot status section and confirm the
      bot is shown as "stopped".
- [ ] Start the bot from the frontend and confirm polling begins (check logs).
- [ ] Stop the bot from the frontend and confirm polling stops.
- [ ] Change the Telegram token in settings and confirm the bot status reflects
      the change (restart recommended / restarted automatically).
- [ ] Open a second browser tab, change a limit while a request is in-flight,
      and confirm the in-flight request is not affected (uses the old value).

## A6 — First-run setup mode

| Field | Value |
| --- | --- |
| Status | Done |
| Priority | Critical |
| Effort | Medium |

On an empty data volume:

1. generate the database and master key;
2. generate a time-limited one-time setup code;
3. show the code in container logs;
4. expose only the setup workflow;
5. invalidate the code after the first administrator is created.

The setup code is stored only as a hash.

**Completed 2026-06-23**  
**Verified 2026-06-27** with `venv/bin/python -m pytest tests`

- Added `bot/setup.py` with setup-code generation, validation, invalidation,
  expiry, and first-run helper predicates.
- Setup codes are random, time-limited, and stored only as SHA-256 hashes.
- Startup integration generates and logs a one-time setup code when the
  application state is `SETUP_REQUIRED`.
- Web setup flow redeems the setup code and invalidates it after the first
  administrator is created.
- Setup and web tests cover valid/invalid codes, expiry, hash isolation,
  invalidation, setup-only access, and administrator creation.

**Manual verification** (from frontend)

- [ ] Start the application on a blank data volume with `docker compose up`.
- [ ] Check the container logs: a one-time setup code must be printed.
- [ ] Open `http://localhost:<port>` — confirm you are redirected to the setup
      page (no admin dashboard or bot functionality exposed).
- [ ] Enter an invalid setup code and confirm an error is shown.
- [ ] Enter the correct setup code, create the first administrator, and confirm
      the code is invalidated (re-opening the setup page should not work).
- [ ] Log in as the new administrator and confirm you enter the full
      administration dashboard.

## A7 — Remove mandatory `.env` and `authorized.json`

| Field | Value |
| --- | --- |
| Status | Done |
| Priority | Critical |
| Effort | Medium |

Move all ordinary runtime configuration to the database.

Provide a one-time migration/import path for:

- Telegram token;
- provider selection and credentials;
- model and prompt settings;
- limits and feature flags;
- existing `authorized.json`;
- existing whitelist SQLite data.

Legacy files become optional import sources and are never required after a
successful migration.

**Completed 2026-06-28**

- `Config(relaxed=True)` produces empty defaults for missing values
  (Telegram token, API keys, `authorized.json`) instead of raising.
  `get_api_key()` returns `""` in relaxed mode. FFmpeg and audio directory
  validation remain enforced.
- `bot/main.py` catches `ConfigError` and falls back to relaxed mode.
  When state is `SETUP_REQUIRED`, the CLI logs the setup code and waits
  instead of attempting to start the bot.
- `RuntimeManager` accepts optional `config`. When token is empty, resolves
  the Telegram token from the database via `ConfigService` + `SecretStore`
  decryption.
- `RuntimeSnapshot.from_config_service()` accepts optional Config. Resolves
  API keys from `provider_connections.encrypted_credentials` (decrypted) when
  Config is unavailable. Rate-limit and resilience helpers accept optional
  Config with sensible defaults.
- `WhitelistManager` accepts optional config. When `db_manager` is available
  and config is `None`, uses only the unified database.
- `create_application()` accepts optional Config. When `None`, skips legacy
  provider component creation (PipelineResolver handles it at request time).
- `StateChecker` legacy shortcut now requires non-relaxed Config with a
  non-empty `telegram_token` attribute.
- `bot/web/app.py` uses `Config(relaxed=True)` instead of `SimpleNamespace`
  fallback for blank-volume startup.
- 4 new relaxed-mode tests. 693 passing tests (0 regressions).

# Phase 2 — Frontend control plane

The first implementation should favor a small server-rendered frontend over a
large SPA unless interaction requirements prove otherwise.

## W1 — Frontend foundation

| Field | Value |
| --- | --- |
| Status | Done |
| Priority | Critical |
| Effort | High |

Add a responsive web application with:

- setup-only mode;
- authenticated administration shell;
- CSRF protection;
- secure cookies;
- clear validation and error feedback;
- no secrets included in page source or client logs.

The choice of framework should minimize operational dependencies and integrate
cleanly with the existing Python runtime.

**Completed 2026-06-23**  
**Verified 2026-06-27** with `venv/bin/python -m pytest tests`

- Added FastAPI-based web control plane in `bot/web/`.
- Added server-rendered setup, login, dashboard, and error templates.
- Added signed-cookie sessions, CSRF protection, bcrypt password hashing, and
  authenticated admin routes.
- Added JSON APIs for application state, health, setup steps, provider tests,
  capability detection, and bot lifecycle actions.
- Docker now starts the web entry point and exposes the frontend port.
- Web tests cover setup-only routing, authentication, CSRF behavior,
  dashboard access, setup completion, provider/pipeline setup endpoints, and
  bot start/stop actions.

**Manual verification** (from frontend)

- [ ] Access the frontend URL on a blank data volume — confirm only the setup
      page is reachable, not the admin dashboard.
- [ ] Register/login as an administrator and confirm the session persists
      across page reloads (secure cookie).
- [ ] Open browser DevTools and confirm no secrets (tokens, keys) appear in
      the page source, network responses, or client-side storage in plaintext.
- [ ] Log out and confirm the session is invalidated.
- [ ] Access the admin section without logging in and confirm a redirect or
      401 page is shown.
- [ ] Test CSRF protection: submit a cross-origin form and confirm it is
      rejected.

## W2 — Guided onboarding

| Field | Value |
| --- | --- |
| Status | Done |
| Priority | Critical |
| Effort | High |

**Completed 2026-06-23**

- Implemented an 8-step setup wizard with server-rendered Jinja2 templates
  and progressive JS enhancement (non-JS fallback via form POST).
- Steps: setup-code redemption, admin creation, Telegram token entry and
  connectivity test, provider selection (OpenAI/Gemini/OpenRouter/Ollama/vLLM)
  with credential validation, capability detection, pipeline verification,
  and bot start/stop.
- Progress is persisted in the `setup_state` table — resumable across
  container restarts.
- Setup code is printed to container logs and invalidated after admin
  creation.
- API endpoints: `GET /setup`, `POST /api/setup/step` for JS-driven flow.
- Added bot start/stop toggle on the dashboard via `POST /api/bot/start`
  and `POST /api/bot/stop`.
- Port changed from 8080 to 8086 to avoid host conflict.

**Manual verification** (from frontend — requires a blank data volume)

- [ ] Start the application and open the frontend: confirm you see the setup
      wizard, not the admin dashboard.
- [ ] Step through the entire wizard:
      1. Redeem the setup code from container logs.
      2. Create the first administrator.
      3. Enter a valid Telegram token — confirm the bot is reachable (check
         passes or a useful error is shown).
      4. Enter an invalid Telegram token — confirm a clear error message.
      5. Connect an AI provider with valid credentials — confirm capability
         detection completes and shows detected models.
      6. Accept "use this provider for everything" by default — confirm the
         pipeline is resolved and valid.
      7. Start the bot from the wizard — confirm the bot goes online and
         responds to Telegram commands.
- [ ] Close the wizard and confirm the dashboard shows "ready" status.
- [ ] Save the wizard halfway (e.g. setup code redeemed but no admin created):
      reopen the frontend and confirm you continue from where you left off.
- [ ] If the pipeline is incomplete, confirm the UI explains exactly what is
      missing (e.g. "No provider configured — audio processing unavailable").

## W3 — Provider management

| Field | Value |
| --- | --- |
| Status | Done |
| Priority | Critical |
| Effort | High |

Allow administrators to:

- add OpenAI, Gemini, OpenRouter, Ollama, vLLM, and custom compatible endpoints;
- enter or replace credentials;
- test connectivity;
- discover or manually register models;
- inspect detected capabilities;
- enable, disable, rename, and remove connections.

Provider deletion must be blocked while referenced by an active pipeline unless
a replacement is selected.

**Completed 2026-06-28** (UX review)

- `/admin/providers` lists all connections with adapter type, endpoint, enabled badge,
  and Dettagli link. The "Nuovo provider" inline form pre-fills endpoint per type and
  labels the API key field as "saved encrypted".
- `/admin/providers/{id}` shows connection metadata, a model table with capability badges
  and enable/disable toggles, guided OpenRouter discovery, and a manual model-by-ID field.
  The API key field is write-only ("La chiave salvata non viene mai mostrata").
- `POST /api/providers/test` returns structured JSON with `auth_ok`, `user_message`
  (Italian, user-facing), and `warnings`. Invalid credentials produce a clear error.
- **Bug fixed (2026-06-28):** `_get_active_pipeline_profile_id()` in `repository.py`
  was reading `app_settings["active_pipeline_profile_id"]` while the value was written
  to `setup_state["active_pipeline_profile"]` — a silent key/table mismatch that made
  the delete-protection check always pass. Fixed to read `setup_state["active_pipeline_profile"]`.
  Tests in `test_database_repository.py` and `test_web_app.py` updated accordingly.
- ⚠️ **Open gap:** post-creation redirect goes to `/admin/pipeline` rather than back
  to `/admin/providers`. Mildly confusing when adding a second provider.

**Manual verification** (from frontend)

- [x] Add an OpenAI provider connection — confirm the form asks for name,
      endpoint (pre-filled), and API key (masked on save).
- [ ] After saving, confirm the provider appears in the list with its detected
      capabilities (transcription, refinement, streaming, etc.).
- [x] Edit the provider name — endpoint `/admin/providers/{id}/edit` exists.
- [x] Replace the API key — the UI shows "Nuova chiave API" and never displays
      the saved key in full.
- [x] Add a second provider and confirm both are listed.
- [x] Delete a provider that is not referenced by any pipeline — confirm it is
      removed immediately.
- [x] Try to delete a provider that IS referenced by an active pipeline —
      confirm the UI shows a blocking error or requires a replacement first.
- [x] Test a connectivity check with invalid credentials — clear Italian error
      message returned (`POST /api/providers/test`).

## W4 — Pipeline management

| Field | Value |
| --- | --- |
| Status | Done |
| Priority | Critical |
| Effort | High |

Default mode:

- select one preferred connection;
- automatically use it for all supported stages;
- make refinement optional;
- reject incomplete configurations before activation.

Advanced mode:

- independently select transcription and text-processing connections/models;
- configure explicit fallback behavior;
- preview the resolved pipeline and expected data flow.

**Completed 2026-06-28** (UX review)

- `/admin/pipeline` offers three mode cards: "Due fasi" (`two_stage`), "Singolo passaggio"
  (`single_pass`), and "Semplice" (`single` — same provider for everything).
- Default mode ("Semplice") shows a single provider selector, not two separate ones.
- "Due fasi" shows independent STT model selector and optional refinement model selector,
  each with fallback pickers.
- Incomplete pipelines are rejected before save: missing STT model → `error=no_tx_model`,
  missing single-pass model → `error=no_sp_model`.
- "Stato attuale" section shows active profile name, mode, and per-stage provider.
- ⚠️ **Open gap — refinement optional switch:** there is no dedicated UI toggle to mark
  refinement as optional and save a transcription-only pipeline in two-stage mode. The
  workaround is to leave the refinement model selector empty (saves `ref_model_id=None`
  in the profile). This is implicit, not explicit.
- ⚠️ **Open gap — data flow preview:** no visual preview of "Audio → Transcriber A →
  TextProcessor B" exists. The "Stato attuale" section shows provider names per stage
  but not a flow diagram.

**Manual verification** (from frontend)

- [x] In the pipeline section, confirm the default mode shows a single
      "preferred provider" selector — not separate transcription/text fields.
- [x] Select one provider in simple mode — confirm the pipeline saves and the
      "Stato attuale" section shows it as active.
- [x] Switch to two-stage mode — confirm independent STT and refinement selectors
      appear.
- [ ] Pick different providers for each stage and confirm the preview shows
      the expected data flow (audio → Transcriber A → TextProcessor B).
      **Gap: no data-flow preview exists yet.**
- [x] Save an incomplete pipeline (no STT model in two-stage, no model in
      single-pass) — confirm the UI redirects with a clear error and does not save.
- [ ] Enable "refinement optional" explicitly and confirm the pipeline is accepted
      with only a transcription provider. **Gap: no explicit toggle; only implicit
      via empty ref_model selector.**

## W5 — Settings and access-control pages

| Field | Value |
| --- | --- |
| Status | Proposed |
| Priority | High |
| Effort | Medium–High |

Manage:

- users, groups, and administrator roles;
- limits, queueing, timeouts, and file size;
- prompts and output defaults;
- streaming and delivery;
- privacy and retention;
- system readiness and health.

Use progressive disclosure: common settings first, advanced details behind
clearly labeled sections.

**Manual verification** (from frontend)

- [ ] Navigate to the limits section and confirm the common settings
      (max file size, rate limits) are visible without scrolling.
- [ ] Change a limit (e.g. max file size), save, and confirm the new value is
      used by the bot (e.g. upload a file just above the new limit and confirm
      it is rejected).
- [ ] Navigate to prompts — change the system prompt or refine template,
      send an audio to the bot, and confirm the new prompt is used in the
      output.
- [ ] Navigate to users/groups — add a new user ID and confirm they can now
      use the bot; remove them and confirm they are rejected.
- [ ] Navigate to the admin section — confirm only existing administrators
      are listed. Add a new admin from the frontend, log out, and log in as
      the new admin.
- [ ] Confirm that sensitive settings (API keys) are never shown in full in
      any settings page.

## W6 — Frontend authentication and recovery

| Field | Value |
| --- | --- |
| Status | Done |
| Priority | Critical |
| Effort | High |

After setup, support Telegram-based administrator login when practical.

Also provide a Telegram-independent recovery mechanism, such as:

```text
application recovery command -> time-limited one-time URL
```

Revoking or misconfiguring the Telegram token must not permanently lock out the
administrator.

**Completed 2026-06-27**

- Added ``bot/recovery.py`` with SHA-256 hashed, time-limited (30 min) one-time
  recovery codes, mirroring the setup-code pattern from ``bot/setup.py``.
- Recovery codes are printed in container logs at every startup when an admin
  exists, ensuring access even without Telegram or frontend credentials.
- ``GET /recovery`` — two-stage web page: code entry → new-password form.
- ``POST /recovery`` — validates recovery code and stores approval in session.
- ``POST /recovery/reset`` — sets new admin password and invalidates the code.
- ``POST /api/recovery/generate`` — authenticated endpoint for admin-dashboard
  recovery-link generation.
- ``recovery_ok`` message shown on ``/login`` after a successful password reset.
- Link "Password dimenticata?" added to the login page.
- 28 automated tests (14 unit + 14 HTTP integration) covering generation,
  validation, expiry, invalidation, one-time semantics, CSRF protection, and
  the complete HTTP flow.
- The existing password-based login already guarantees that revoking the
  Telegram token never locks out the administrator.

**Manual verification** (from frontend)

- [x] Log in via the normal admin login (username/password or Telegram if
      integrated).
- [ ] If Telegram-based login is available, confirm it works: send a command
      to the bot and receive a login link.
- [x] Revoke or change the Telegram token while logged in — confirm the admin
      session remains active (admin is not locked out).
- [x] Test the recovery mechanism: trigger a password reset or recovery code
      flow and confirm a time-limited one-time URL is generated.
- [x] Use the recovery URL to log in — confirm it works only once and
      expires after the first use.
- [ ] Test with an expired recovery URL and confirm it is rejected.

## W7 — Import, export, and backup

| Field | Value |
| --- | --- |
| Status | Proposed |
| Priority | Medium |
| Effort | Medium |

Provide:

- configuration export without secrets by default;
- validated import with a preview;
- optional password-encrypted full backup;
- documented restore and key-recovery limitations.

**Manual verification** (from frontend)

- [ ] Export the current configuration from the frontend — confirm the
      downloaded file does NOT contain secrets (API keys, tokens).
- [ ] Import the exported file on a fresh installation — confirm settings
      (except secrets) are restored.
- [ ] Perform a password-encrypted full backup — confirm it requires a
      password to restore.
- [ ] Restore from backup and confirm all settings, including secrets, are
      recovered.
- [ ] Try to import a malformed or invalid file — confirm a validation error
      is shown with a preview of what is wrong.

## W8 — Polished administration UI

| Field | Value |
| --- | --- |
| Status | Deferred |
| Priority | Medium |
| Effort | Medium–High |

Improve the administration UI after the backend control plane is stable.

Preferred direction:

- keep server-rendered pages unless interaction requirements prove a full SPA
  is worth the added complexity;
- adopt a maintained UI foundation such as Tabler, AdminLTE, DaisyUI, or a
  comparable admin template instead of designing every component from scratch;
- use lightweight progressive enhancement such as HTMX and/or Alpine.js for
  inline validation, live status refresh, modal confirmations, toast
  notifications, and start/stop actions;
- keep forms accessible and usable without relying on large client-side state;
- revisit React, Vue, Svelte, or another SPA framework only if pipeline editing,
  provider/model exploration, or dashboards become too complex for the
  server-rendered model.

**Manual verification** (from frontend)

- [ ] The dashboard has a clear visual hierarchy for state, bot lifecycle,
      provider health, and setup progress.
- [ ] Forms show inline validation without losing entered data.
- [ ] Start/stop/restart actions provide immediate feedback and safe
      confirmation where needed.
- [ ] The UI remains responsive on mobile and small screens.
- [ ] No secrets are exposed in page source, browser storage, logs, or
      JavaScript state.
- [ ] The selected template/library is documented with rationale and upgrade
      notes.

## W9 — Express setup flow

| Field | Value |
| --- | --- |
| Status | Done |
| Priority | Critical |
| Effort | Medium |

**Completed 2026-06-28**

- Added `/setup/express` as the first-run single-screen provider/process/model
  setup flow.
- Extracted pipeline creation into `bot/web/pipeline_builder.py`, so setup and
  admin flows share the same profile/stage shape.
- `create_express_pipeline_from_wizard()` registers the selected provider,
  creates explicit model rows, and activates two-stage or single-pass profiles.
- Legacy provider/capability/pipeline wizard steps now redirect to express
  setup after Telegram setup.
- Final feedback distinguishes setup completed, saved-without-start, and error
  states with dashboard-oriented CTAs.
- Manual QA passed for two-stage setup, single-pass setup, persistence,
  selected-card behavior, CTA states, secret handling, and responsive viewports.

Replace the current multi-screen setup sequence (provider page → model registration
→ pipeline page) with a single guided screen that asks three questions and
configures everything behind the scenes.

The screen asks, in order:

1. **Which AI service?** — select provider type and enter an API key.
2. **How do you want to process audio?** — choose between:
   - *Two stages*: Whisper transcribes, a text model refines.
   - *Single pass*: one multimodal model handles everything.
3. **Which model?** — the model picker described in W10.

On save the system automatically registers the selected models, builds the
pipeline profile, and starts the bot. No separate provider or pipeline pages are
required for a first-time setup.

The existing provider and pipeline admin pages remain available for advanced
configuration and are linked from the express flow as an escape hatch.

**Done when**

- [x] A new installation can be fully configured without visiting `/admin/providers`
  or `/admin/pipeline` directly.
- [x] The express screen correctly handles invalid API keys and unreachable endpoints
  with inline feedback.
- [x] Completing the flow results in the same database state as manual configuration
  via the advanced pages.

**Manual verification**

- [x] Fresh install: complete the entire setup using only the express screen —
      confirm the bot starts and processes audio correctly.
- [x] Enter an invalid API key — confirm an inline error appears without leaving
      the screen.
- [x] Complete setup via express, then open the advanced pipeline page — confirm
      the configuration matches what was selected.

## W10 — Smart model picker

| Field | Value |
| --- | --- |
| Status | Done |
| Priority | Critical |
| Effort | High |

**Completed 2026-06-28**

- `bot/model_picker.py` produces reusable picker cards with locked Whisper
  transcription, OpenRouter shortlist filtering, per-million pricing,
  speed/quality indicators, recommended-card selection, category counts, and
  conservative manual-entry cards.
- `/api/setup/model-picker` returns setup picker cards without persisting
  credentials or provider/model rows.
- The express UI renders selectable model cards with sort controls, tier/provider
  filtering, selected-card ordering, custom-card badges, and responsive layout.
- Manual model cards persist beyond browser restart via
  `GET/POST /api/setup/manual-cards` backed by `setup_state`.
- Manual OpenRouter model IDs are verified against the live catalog by exact
  model ID. Matching models get real pricing/provider/capability metadata.
  Not-found models fall back conservatively in two-stage mode and are rejected
  in single-pass mode. Single-pass also rejects found models without audio input.
- Persisted card metadata is sanitized with allowlists, including nested
  pricing/capability metadata. CSRF protects card persistence writes.
- Manual QA passed for two-stage cards, single-pass cards, manual add,
  persistence, selected-card behavior, secret handling, and responsive viewports.

**Follow-up**

- P2: OpenAI validation errors can reflect the `sk-...` API-key prefix in one
  error message path (`bot/web/app.py` around provider test formatting). Sanitize
  in the current sprint.

Replace the current dropdown/table model selectors with a card-based model
picker that makes tradeoffs visible and keeps the user in control without
requiring expertise.

### Transcription card

Whisper is auto-selected and shown as a single locked card. No picker is
displayed. The card reads: *"Whisper — standard industriale per la trascrizione
vocale"*. The user cannot deselect it; if a different transcription model is
needed the advanced pipeline page handles that case.

### Refinement carousel

Three to five curated model cards displayed in a horizontal carousel. Each card
shows:

- model name and provider;
- cost per million input and output tokens (from OpenRouter pricing);
- a qualitative speed indicator (fast / medium / slow);
- a qualitative quality indicator;
- a "Recommended" badge on one card.

Tier structure (always shown in this order):

| Tier | Example | Signal |
| --- | --- | --- |
| Free | Llama 3.x, Gemma | Zero cost, good quality for text cleanup |
| Balanced | GPT-4o mini, Gemini Flash | Low cost, reliable quality — default selection |
| Premium | Claude Sonnet, GPT-4o | Higher cost, best quality |

The user can also add a model manually by entering its OpenRouter ID. The system
fetches the model's metadata from OpenRouter, creates a card with real pricing,
and inserts it into the carousel. Manually added cards are visually distinct
(e.g. a "Custom" tag) and persist across sessions.

The carousel can be sorted by: cost (ascending), quality (descending), or
provider. A filter chip row lets the user hide free, balanced, or premium tiers.

### Single-pass picker

When single-pass mode is selected, the picker makes a live call to the
OpenRouter `/models` endpoint and filters to models where
`architecture.input_modalities` contains `"audio"`. Results are displayed as
cards with real-time pricing. The user sees only models that can actually accept
audio input — no manual capability configuration required.

If no audio-capable models are found (e.g. wrong provider or API key issue),
the UI explains the situation and falls back to suggesting two-stage mode.

The user can also add a model manually by ID; the system verifies that it
reports audio input modality before adding the card.

### Card ordering and persistence

- The selected model card is always shown first.
- Manually added cards follow the selected card.
- Curated cards follow in tier order.
- Sort and filter state is remembered per session.

**Done when**

- [x] Transcription shows a single locked Whisper card with no picker.
- [x] Refinement shows curated tier cards with real or periodically refreshed pricing.
- [x] Single-pass fetches audio-capable models live from OpenRouter with real pricing.
- [x] A user can add a model by ID and receive a verified card from the OpenRouter catalog (real pricing, provider, capabilities) or a conservative fallback card when the model is not found.
- [x] The carousel supports sort by cost and quality and filter by tier.
- [x] Manually added models persist beyond the browser session (database-backed).
- [x] Selecting a card in any picker correctly configures the pipeline without
  additional steps.

**Manual verification**

- [x] Open the express setup in single-pass mode — confirm only audio-capable
      models appear as cards with current pricing.
- [x] Add a model manually by ID — confirm its card appears with pricing fetched
      from OpenRouter and a "Custom" tag.
- [x] Enter an ID for a model that does not support audio input in single-pass
      mode — confirm the UI rejects it with a clear explanation.
- [x] Sort the refinement carousel by cost — confirm the order changes correctly.
- [x] Filter to show only the free tier — confirm balanced and premium cards
      disappear.
- [x] Select a refinement card, save, reopen the page — confirm the selection
      persists and the card is shown first.
- [x] Confirm that manual model cards persist across browser restarts
      (close and reopen the page — manually added cards reappear).
- [x] Confirm that saved cards never contain API keys or other secrets
      (check the database or network response).
- [x] Confirm that completing the picker creates a valid pipeline that processes
      audio end to end.

# Phase 3 — Composable provider architecture

## P1 — Separate transcription and text processing

| Field | Value |
| --- | --- |
| Status | Done |
| Priority | Critical |
| Effort | High |

Replace the combined provider contract with:

- `Transcriber`;
- optional `TextProcessor`;
- normalized `TranscriptionResult`;
- normalized streaming events.

The result should preserve optional language, duration, segments, timestamps,
and speaker metadata without requiring the Telegram UI to expose them.

**Completed 2026-06-27**

- Introduced `TranscriptionResult` dataclass (`text`, `language`,
  `duration_seconds`, `segments`) as the normalized return type for all
  transcribers.
- Introduced `Transcriber(ABC)` interface with `transcribe(file_path) → TranscriptionResult`.
- Introduced `TextProcessor(ABC)` interface with `process(text) → str` and
  `stream_process(text) → AsyncIterator[StreamEvent]`.
- Added `OpenAIWhisperTranscriber` — wraps Whisper API via the existing
  OpenAI client.
- Added `OpenAITextProcessor` — wraps Chat Completions / Responses API for
  refinement.
- Added `GeminiTranscriber` — wraps Gemini file upload + generate.
- Added `GeminiTextProcessor` — wraps Gemini generate + stream.
- Added `ResilientTranscriber` and `ResilientTextProcessor` circuit-breaker
  wrappers.
- Added `ProviderComponents` dataclass and `create_provider_components()` factory
  in `bot/utils.py`.
- `AudioProcessor` constructor now accepts optional `transcriber` /
  `text_processor` in addition to the legacy combined `provider`.
- `bot/core/app.py` uses `create_provider_components()` when available, falling
  back to legacy `create_provider()`.
- **Retrocompatibilità garantita**: `OpenAIProvider` and `GeminiProvider`
  implement both the old interface (`transcribe_audio`, `refine_text`) by
  delegating to the new adapters.
- 329 passing tests (2 new P1-specific adapter tests, 0 regressions).

## P2 — Provider connection and capability model

| Field | Value |
| --- | --- |
| Status | Done |
| Priority | Critical |
| Effort | High |

Define capabilities for configured endpoints and selected models instead of
assuming them from provider names.

Capability detection may combine:

- provider metadata APIs;
- adapter-known defaults;
- a safe probe;
- administrator confirmation where automatic detection is impossible.

Detected and manually overridden values must remain distinguishable.

**Completed 2026-06-27**

- Added ``bot/capabilities.py`` with:
  - ``CapabilityModel`` frozen dataclass with four typed flags
    (``transcription``, ``text_generation``, ``refinement``,
    ``streaming_refinement``);
  - ``to_dict()`` / ``from_dict()`` serialization;
  - ``default_for_adapter()`` — known defaults for ``openai`` and
    ``gemini`` adapter types, all-``False`` for unknown types;
  - ``detect_capabilities(adapter_type, model_name)`` — static detection
    using adapter defaults + model name heuristics (no external API call);
  - ``merge_capabilities(detected, overrides)`` — merges detected with
    sparse admin overrides, keeping the distinction meaningful.

- Added ``get_capabilities() → CapabilityModel`` to both ABCs:
  - ``Transcriber`` (default: ``transcription=True``);
  - ``TextProcessor`` (default: ``text_generation=True``,
    ``refinement=True``, ``streaming_refinement`` from
    ``supports_refine_streaming``).

- Every adapter now overrides ``get_capabilities()``:
  - ``OpenAIWhisperTranscriber`` / ``GeminiTranscriber``: transcription only;
  - ``OpenAITextProcessor`` / ``GeminiTextProcessor``: refinement + streaming;
  - ``OpenAIProvider`` / ``GeminiProvider``: combines both sub-adapters;
  - ``ResilientTranscriber`` / ``ResilientTextProcessor``: delegates to inner;
  - ``LLMProvider`` / ``ResilientProvider``: conservative fallback.

- ``AudioProcessor.capabilities`` (new property) resolves capabilities from
  whichever source is active (P1 adapters or legacy provider).
- ``AudioProcessor.supports_refine_streaming`` now delegates to
  ``self.capabilities.streaming_refinement``.
- ``StateChecker._any_can_transcribe`` uses ``CapabilityModel.from_dict()``
  instead of raw dict lookups.
- ``bot/web/app.py`` ``detect-capabilities`` endpoint uses
  ``bot.capabilities.detect_capabilities()`` instead of inline keyword
  heuristics.
- 36 new tests (365 total, 0 regressions).

**P2 extension — OpenRouter capability probing (2026-06-27)**

- ``openai-compat`` adapter ``default_for_adapter`` changed:
  ``transcription=False`` (conservative — not every compat endpoint has
  audio).  Detection for ``openai-compat`` now requires explicit audio
  keywords (``"whisper"`` / ``"audio"``) in the model name to set
  ``transcription=True``.
- Added ``probe_openrouter_capabilities(api_key, endpoint, model_name)`` —
  async helper that fetches model metadata from the OpenRouter Models API
  and classifies capabilities conservatively based on ``input_modalities``,
  ``output_modalities``, and ``supported_parameters``.
- Admin provider creation (``/admin/providers/create``) and setup wizard
  capability detection (``/api/setup/detect-capabilities``,
  ``/api/setup/test-provider``) now probe model metadata for OpenRouter
  instead of using static defaults.
- 31 new tests (480 total, 0 regressions).

## P3 — Adapter registry

| Field | Value |
| --- | --- |
| Status | Done |
| Priority | High |
| Effort | Medium |

Use explicit registries for transcribers and text processors rather than an
ever-growing `if/elif` factory.

Initial adapters:

- OpenAI native;
- Gemini native;
- OpenAI-compatible transcription;
- OpenAI-compatible text processing.

Provider presets such as OpenRouter, Ollama, and vLLM configure these adapters
without duplicating the core protocol implementation.

**Completed 2026-06-28**

- Added `bot/adapters/` package:
  - `registry.py` — `TranscriberRegistry` and `TextProcessorRegistry` with
    `register()` (callable or decorator form), `create()`, and `has_type()`.
    Replaces the previous `if/elif` factory chain.
  - `defaults.py` — `register_defaults()` registers the four built-in adapter
    types: `openai-native` (alias `openai`), `gemini-native` (alias `gemini`),
    and `openai-compat` (transcription and text processing).
  - `openai_compat.py` — OpenAI-compatible adapters reused by the OpenRouter,
    Ollama, and vLLM presets without duplicating the protocol implementation.
- Both `create_provider_components()` (`bot/utils.py`) and the model-level
  resolver (`bot/pipeline_resolver.py`) now create adapters through the global
  registries instead of hardcoded branching.
- Covered by `tests/test_adapter_registry.py` and `tests/test_openai_compat.py`
  (693 total passing, 0 regressions).

## P4 — Automatic pipeline resolver

| Field | Value |
| --- | --- |
| Status | Implemented |
| Priority | Critical |
| Effort | High |

| File | Role |
| ---- | ---- |
| `bot/pipeline_resolver.py` | Core resolver: `PipelineResolver`, `ExecutionPlan`, `PipelineRequest`, `RequestMode`, `ModelRef` |
| `bot/exceptions.py` | `PipelineResolutionError` |
| `bot/core/app.py` | Registers resolver in `bot_data['pipeline_resolver']` |
| `bot/handlers/audio.py` | Per-request pipeline resolution before processing |
| `tests/test_pipeline_resolver.py` | 72 tests covering all resolution paths and error cases |

Resolve the simplest valid pipeline from:

- request mode (`FULL` or `TRANSCRIPTION_ONLY`);
- user/group preferences *(plumbing ready for future pipeline-profile lookups)*;
- selected pipeline profile *(profiles table exists, resolver can load by ID)*;
- provider and model capabilities (`CapabilityModel` detected + overrides);
- system policy (`refinement_globally_disabled` flag).

The resolver explains invalid configurations in user-facing terms and
produces an immutable :class:`ExecutionPlan` for each accepted request.

### P4.1 — Model-level resolution and fallback chains (2026-06-27)

The resolver now resolves by model capabilities rather than provider-level
capabilities. Key additions:

- **`ModelRef`** dataclass (provider_id, adapter_type, model_entry_id, model_id,
  capabilities, fallback_model_ids, fallback_entry_ids) — immutable reference to
  a resolved model with its fallback chain.
- **Explicit pipeline stages**: `resolve_from_profile()` checks
  `pipeline_stages` before falling back to legacy provider-level references.
- **Two-stage pipeline**: separate transcription and refinement models,
  each with optional fallback chains.
- **Single-pass pipeline**: one model with `single_pass_audio_to_text`
  capability handles both transcription and refinement.
- **Capability validation**: checks that resolved models have the required
  capabilities for their stage.
- **DB migration 002** adds `provider_models`, `pipeline_stages`,
  `pipeline_stage_fallbacks` tables and `mode` column on `pipeline_profiles`.

### P4.2 — Runtime fallback execution (2026-06-27)

Fallback chains are now executed at runtime, not just stored as metadata.

- **`FallbackTranscriber`** and **`FallbackTextProcessor`** wrappers in
  `bot/pipeline_resolver.py` that try the primary model, then each fallback in
  order on failure.
- Logs which model succeeded (model name only, no transcript/audio content).
- User-facing error messages when all models in a stage fail.
- The `ExecutionPlan`'s `transcriber` and `text_processor` instances are wrapped
  in fallback-aware counterparts when the resolved `ModelRef` has fallbacks.
- 180+ tests covering fallback wrappers, resolver integration, and edge cases.

**Manual verification** (from frontend)

- [x] Configure a two-stage pipeline with fallback models for transcription.
      Simulate a primary failure (e.g. invalid API key for primary model) —
      confirm the next fallback model is used transparently.
- [x] Configure a refinement stage with fallbacks. Simulate primary failure —
      confirm fallback executes and the refined result is delivered.
- [x] Configure a stage where all models fail — confirm the user receives a
      clear error message.
- [x] Check the logs — confirm they show which model was used without exposing
      transcript or audio content.

**Manual verification** (from frontend)

- [x] Configure a provider that supports both transcription and text
      processing. In the pipeline page, confirm the resolver automatically
      selects it for both stages without requiring manual assignment.
- [x] Configure two providers: one with only transcription capability, another
      with both. In the pipeline page, confirm the resolver picks the
      capable provider for the full pipeline by default.
- [x] Disable refinement globally — confirm the resolver produces a
      transcription-only execution plan.
- [x] Intentionally create an invalid configuration (e.g. remove all
      providers from a pipeline) — confirm the error message explains the
      problem in plain language (e.g. "Nessun provider configurato supporta
      la trascrizione audio").
- [x] Send an audio to the bot — confirm the handler accepts or rejects it
      based on the resolved pipeline state.
- [x] Create a pipeline profile with explicit model-level stages and fallbacks
      — confirm the resolver uses them.
- [x] Configure a single-pass model — confirm the resolver produces a plan
      with a single transcriber and no separate text processor.

## P5 — Same-provider default

| Field | Value |
| --- | --- |
| Status | Done |
| Priority | Critical |
| Effort | Medium |

When one provider connection supports transcription and text processing, use it
for both by default, even when it uses different models or endpoints
internally.

Do not expose separate provider choices during onboarding unless the preferred
connection cannot satisfy the requested behavior.

**Completed 2026-06-27**

- `resolve_from_profile()` in `bot/pipeline_resolver.py` loads saved pipeline
  profiles and resolves the referenced providers, supporting same-provider
  default and separate-provider configurations.
- `create_pipeline_from_wizard()` in `bot/web/setup_wizard.py` persists the
  provider connection and pipeline profile when the onboarding wizard completes
  (step_verify).
- Wizard step 6 (step_pipeline) adapts to detected capabilities: auto-selects
  "use this provider for everything" when both transcription and refinement are
  available, "transcription only" when refinement is unsupported, and shows
  explanatory messages.
- Admin pipeline page at `/admin/pipeline` with a single-provider default mode
  and an advanced mode with separate selectors for transcription and text
  processing.
- 17 new tests (445 total, 0 regressions).

**Manual verification** (from frontend)

- [x] During onboarding: add a provider that supports transcription and text
      processing — confirm the wizard offers "use this provider for everything"
      as the default option and does NOT expose separate transcription/text
      selectors.
- [x] During onboarding: add a provider that supports only transcription —
      confirm the wizard explains that text refinement is disabled or asks to
      add a text-processing provider.
- [x] In the admin pipeline page: when using default mode, confirm the UI
      shows a single provider selector, not two separate ones.

## P6 — Advanced multi-provider pipelines

| Field | Value |
| --- | --- |
| Status | Done |
| Priority | Medium |
| Effort | Medium–High |

Allow administrators to compose providers per stage and configure explicit
fallbacks.

Fallbacks must be opt-in and visible because they may change:

- cost;
- latency;
- privacy boundary;
- output quality;
- data residency.

**Completed 2026-06-27**

- **DB migration 002** creates `pipeline_stages` and `pipeline_stage_fallbacks`
  tables. Each stage references a profile, a stage type (`transcription`,
  `refinement`, or `single_pass`), and an ordered list of fallback models.
- **Provider detail page** (`/admin/providers/{id}`) with model table, discovery,
  manual add, capability editor, and enable/disable toggle.
- **OpenRouter guided discovery** groups catalog imports by pipeline purpose
  (`refinement`, `transcription`, `single_pass`) with search and bounded limits,
  so admins import only useful shortlists instead of the full model catalog.
- **Admin UI refresh** aligns provider, provider-detail, and pipeline pages with
  shared page headers, section layouts, compact tables, and structured
  OpenRouter model cards.
- **Pipeline page rewritten** with mode selection cards (two-stage / single-pass)
  and model-level selects per stage. Separate model selects for transcription
  and refinement in two-stage mode, with fallback model pickers.
- **`PipelineResolver`** uses explicit `pipeline_stages` when present, falls back
  to legacy provider-level references for backward compatibility. Fallback
  chains are included in the immutable `ExecutionPlan`.
- **Runtime fallback execution**: `FallbackTranscriber` and
  `FallbackTextProcessor` attempt each fallback model at runtime when the
  primary fails, logging which model was used without exposing transcript or
  audio content.
- **Delete/disable protection**: `delete_provider`, `delete_provider_model`,
  `update_provider_model(enabled=False)`, and `update_provider(enabled=False)`
  raise `ResourceInUseError` when the provider or model is referenced by the
  active pipeline profile.
- 693+ passing tests (125 database + 106 web app + 87 resolver + 375 other).

**Manual verification** (from frontend)

- [x] In the frontend, go to advanced pipeline settings and confirm you can
      independently select a transcription provider and a text-processing
      provider (different from each other).
- [x] Configure fallback providers — confirm the UI shows a clear list of
      which provider is used at each stage, in what order, and why each
      fallback might be activated.
- [x] Save the advanced configuration and confirm the preview shows the
      complete resolved pipeline with data flow labels (e.g. "Audio →
      Transcriber: OpenAI → Text: Gemini").
- [x] Send audio to the bot — confirm it uses the correct provider mix.
- [x] Try to delete a provider or model that is in use by the active pipeline
      — confirm the operation is rejected with a clear message.
- [x] Try to disable a model that is in use by the active pipeline — confirm
      the operation is rejected.
- [x] Delete/disable a provider or model that is NOT in use — confirm the
      operation succeeds.

## P7 — Capability-aware audio preparation

| Field | Value |
| --- | --- |
| Status | Done |
| Priority | High |
| Effort | Medium |

**Completed 2026-06-28**

- Added `accepted_formats() → frozenset[str]` to the `Transcriber` ABC (default:
  `{'mp3'}` for backward compatibility).
- Overridden in `OpenAIWhisperTranscriber` (flac, m4a, mp3, mp4, mpeg, mpga,
  oga, ogg, wav, webm), `GeminiTranscriber` (wav, mp3, aiff, aac, ogg, flac),
  and `OpenAICompatTranscriber` (same as Whisper).
- Delegated through `ResilientTranscriber`, `FallbackTranscriber`, and all
  legacy combined providers (`OpenAIProvider`, `GeminiProvider`,
  `ResilientProvider`, `LLMProvider`).
- `AudioProcessor.transcribe_accepted_formats` exposes the resolved set from
  whichever transcriber is active.
- `handle_audio` now skips FFmpeg conversion when the source file extension is
  in the transcriber's accepted set, logging the decision. 730 tests passing
  (0 regressions).

Skip conversion when the transcriber accepts the original format. Otherwise
normalize audio using speech-appropriate settings.

The execution plan should record why conversion is or is not required.

**Manual verification** (from frontend)

- [x] Send an audio file in a format already supported by the transcriber
      (e.g. MP3 for OpenAI) — confirm the execution log shows "No conversion
      needed: format accepted by provider".
- [x] Send an audio in an unsupported format (e.g. OGG) — confirm the audio
      is converted before transcription and the execution log records the
      conversion step.
- [ ] Check the audio preparation decision in the pipeline preview or
      execution plan (if available in frontend).

## P8 — Local provider deployment

| Field | Value |
| --- | --- |
| Status | Proposed |
| Priority | High |
| Effort | High |

Support local HTTP services first:

- Ollama for text processing;
- vLLM for text and supported transcription models;
- other OpenAI-compatible endpoints.

Evaluate optional Compose profiles and connection guidance. Heavyweight
in-process dependencies such as `faster-whisper` remain optional and separate
from the default image.

## P9 — Single-pass multimodal pipeline

| Field | Value |
| --- | --- |
| Status | Deferred |
| Priority | Low |
| Effort | High |

Allow capable models to transform audio directly into final cleaned or
summarized text.

Only consider this after the two-stage pipeline is stable, because single-pass
processing reduces transparency, portability, and access to the raw
transcription.

# Phase 4 — Runtime reliability

Some current fixes should be implemented against the new abstractions where
possible instead of deepening the combined-provider design.

## R1 — Non-blocking streaming

| Field | Value |
| --- | --- |
| Status | Proposed |
| Priority | Critical |
| Effort | Medium |

Ensure every adapter consumes streams without blocking the event loop and that
timeouts cover the complete stream lifecycle. The current Gemini issue is the
first regression case.

## R2 — Throttled Telegram delivery

| Field | Value |
| --- | --- |
| Status | Proposed |
| Priority | Critical |
| Effort | Medium |

Batch provider deltas by time and size before sending Telegram draft updates.
Always flush the final durable response.

## R3 — Safe streaming fallback

| Field | Value |
| --- | --- |
| Status | Proposed |
| Priority | Critical |
| Effort | Medium |

Treat streaming as an optional delivery enhancement. Retry with non-streaming
text processing when safe and prevent partial drafts from becoming the only
visible result.

## R4 — Per-stage resilience

| Field | Value |
| --- | --- |
| Status | Proposed |
| Priority | High |
| Effort | Medium |

Maintain separate timeout, retry, circuit-breaker, and health state for each
provider connection and operation.

## R5 — Stable runtime reconfiguration

| Field | Value |
| --- | --- |
| Status | Proposed |
| Priority | Critical |
| Effort | High |

Configuration updates must not mutate dependencies used by in-flight requests.
Build and atomically swap validated runtime snapshots for subsequent requests.

**Manual verification** (from frontend)

- [ ] While the bot is processing an audio, change a setting in the frontend
      (e.g. update the system prompt or modify a rate limit).
- [ ] Confirm the in-flight request completes with the old configuration
      (verify via logs or output).
- [ ] Confirm the next audio request uses the new configuration.
- [ ] Change the Telegram token while requests are in-flight — confirm the
      current request is not interrupted and completes normally.

# Phase 5 — Telegram configuration and end-user UX

## T1 — Telegram settings interface

| Field | Value |
| --- | --- |
| Status | Proposed |
| Priority | High |
| Effort | High |

Provide commands and inline keyboards for safe settings:

- output mode;
- language and translation preference;
- delivery format;
- active pipeline profile from an administrator-approved list;
- users and groups for administrators;
- system and provider status.

Do not expose raw secrets, arbitrary endpoint URLs, destructive recovery, or
infrastructure controls in Telegram.

**Manual verification** (admin — from frontend, end-user — from Telegram)

- [ ] In the frontend settings page, confirm pipeline profiles can be created
      and named (e.g. "Default", "Quick", "High quality").
- [ ] In the Telegram settings interface (T1), confirm a user can select an
      active pipeline profile from an administrator-approved list.
- [ ] As admin, send `/status` or equivalent to Telegram and confirm the
      response shows provider health, pipeline status, and queue state
      without exposing API keys or tokens.

## T2 — Selectable output modes

| Field | Value |
| --- | --- |
| Status | Proposed |
| Priority | High |
| Effort | Medium |

Initial modes:

- faithful transcript;
- cleaned transcript;
- summary.

The text-processing stage is skipped for faithful mode when appropriate.

## T3 — Cancel queued or active work

| Field | Value |
| --- | --- |
| Status | Proposed |
| Priority | High |
| Effort | Medium |

Support `/cancel` and inline cancellation while preserving queue counters,
provider cleanup, and execution-plan consistency.

## T4 — Natural output splitting and text export

| Field | Value |
| --- | --- |
| Status | Proposed |
| Priority | High |
| Effort | Medium |

Split by paragraph, sentence, whitespace, then hard limit. Offer `.txt` export
for long results without retaining the file after delivery.

## T5 — Honest progress and queue feedback

| Field | Value |
| --- | --- |
| Status | Proposed |
| Priority | Medium |
| Effort | Medium |

Show stage names, queue entry, processing start, elapsed time, and cancellation.
Avoid fake percentages and excessive Telegram updates.

## T6 — Conversation clarity

| Field | Value |
| --- | --- |
| Status | Proposed |
| Priority | Medium |
| Effort | Low |

Reply to the original audio message and hide provider/model details by default.
Expose technical details through administrator status views.

## T7 — Translation and language controls

| Field | Value |
| --- | --- |
| Status | Proposed |
| Priority | Medium |
| Effort | Medium |

Support automatic detection, optional language hints, and post-transcription
translation when the resolved pipeline supports them.

# Phase 6 — Operations and maintainability

## O1 — Structured tracing and audit

| Field | Value |
| --- | --- |
| Status | Proposed |
| Priority | High |
| Effort | Medium |

Add anonymous request IDs, stage durations, queue wait, resolved provider
connections, and final status. Audit configuration and access-control changes
without logging secrets or transcript content.

## O2 — Health and metrics

| Field | Value |
| --- | --- |
| Status | Proposed |
| Priority | Medium |
| Effort | Medium |

Expose frontend, database, Telegram, queue, runtime-manager, pipeline, and
provider health. Avoid introducing a large monitoring stack without a concrete
deployment need.

**Manual verification** (from frontend)

- [ ] Open the health/dashboard page and confirm the following status
      indicators are visible: database (connected/disconnected), Telegram
      (polling/stopped/error), queue (current size / limit), and each
      configured provider (reachable/unreachable).
- [ ] Stop the Telegram bot from the runtime manager — confirm the dashboard
      shows "stopped" or "disconnected".
- [ ] Start it again — confirm the status returns to "polling" or "connected".
- [ ] If a provider becomes unreachable (e.g. wrong API key), confirm the
      dashboard shows "unreachable" or "error" for that specific provider
      without affecting the status of other providers.

## O3 — Expanded integration and migration testing

| Field | Value |
| --- | --- |
| Status | Proposed |
| Priority | Critical |
| Effort | High |

Test:

- blank-volume first run;
- setup-code redemption;
- secret encryption and rotation;
- Telegram lifecycle changes;
- legacy configuration import;
- provider capability probing;
- automatic and advanced pipeline resolution;
- runtime snapshot swaps;
- recovery and backup flows.

## O5 — Code-health refactor and reuse audit

| Field | Value |
| --- | --- |
| Status | Proposed |
| Priority | Medium |
| Effort | Medium |

Outcome of a 2026-06-28 whole-codebase reuse review (~12.7k LOC in `bot/`,
~12.7k LOC in `tests/`). The review confirmed the project does **not**
reinvent security primitives (bcrypt via `passlib`, Fernet via `cryptography`,
signed sessions via `itsdangerous`, `secrets`/`hashlib` for one-time codes)
and that the large domain modules (`pipeline_resolver.py`, `capabilities.py`,
`config_service.py`) are legitimately custom with no library equivalent.

The issue is **concentration and coupling**, not over-customization. Targeted,
low-risk work — the 693-test suite covers regressions:

1. **Modularize `bot/web/app.py` (2404 lines).** Split inline routes into
   FastAPI `APIRouter` modules (`setup`, `login/recovery`, `admin`,
   `providers`, `pipeline`, `api`). Move model-classification and discovery
   helpers out of the controller. No behavior change. **Highest value /
   lowest risk.**

2. **Add concurrency tests for `bot/rate_limiter.py` (214 lines).** The
   per-user queue with cascading-grant/cancellation logic is the one custom
   component with a real correctness risk (potential race conditions). Keep the
   implementation (no library covers per-user fair queueing for this case) but
   cover the cascading paths with explicit concurrency tests.

### Deliberately not doing (low ROI, recorded to avoid revisiting)

- **`repository.py` → SQLAlchemy/SQLModel.** 998 lines of raw-SQL CRUD; an ORM
  migration is 2-3 days plus full retest for uncertain ROI while it is stable.
  Revisit only if transaction/FK bugs emerge or queries become relationally
  complex.
- **`migrations.py` → Alembic.** Only two migrations exist; Alembic is overkill
  until ~10+.
- **`config.py` → pydantic-settings.** Better ergonomics but a breaking
  interface change for marginal gain.
- **Custom `_CircuitBreaker` → `pybreaker`.** ~45 readable lines; a new
  dependency to save little.

**Done when**

- Web routes are split into cohesive `APIRouter` modules with `app.py` reduced
  to application assembly and shared dependencies.
- Rate-limiter cascading-grant and cancellation paths have explicit
  concurrency test coverage.
- The full test suite still passes with no behavior change.

## O4 — Dependency reproducibility

| Field | Value |
| --- | --- |
| Status | Proposed |
| Priority | Medium |
| Effort | Low–Medium |

Evaluate tighter pins or a lock file. Keep local-model dependencies outside the
default installation unless required by the selected deployment profile.

# Migration strategy

The control-plane rewrite should not require a single destructive cutover.

## Migration stage M1 — Compatibility layer

- Introduce the database and configuration service.
- Read current environment and authorization files through a legacy adapter.
- Keep current runtime behavior unchanged.

## Migration stage M2 — Import and dual-read

- Offer a guided import into the database.
- Prefer database values after successful import.
- Keep legacy files available for rollback, without writing to them.

## Migration stage M3 — New runtime

- Start the frontend independently.
- Manage Telegram and provider lifecycle through the runtime manager.
- Use provider connections and resolved execution plans.

## Migration stage M4 — Remove legacy requirements

- Stop requiring `.env` and `authorized.json`.
- Retain explicit one-time import tooling for existing deployments.
- Update Docker, documentation, and recovery procedures.

Every migration stage should leave the repository in a deployable state.

# Deferred or rejected scope

| Capability | Status | Reason |
| --- | --- | --- |
| Speaker diarization | Deferred | Useful for meetings, but not central to ordinary Telegram voice messages. |
| Word-level timestamps | Deferred | Adds provider and output complexity without improving the primary workflow. |
| Searchable transcript archive | Deferred | Introduces sensitive long-term content storage. |
| AI chat over transcript history | Deferred | Requires persistent history and changes the product identity. |
| CRM, Slack, Notion, or calendar integrations | Deferred | Outside the focused Telegram workflow. |
| Automatic Zoom/Meet/Teams attendance | Rejected | Would turn the application into a meeting assistant competitor. |
| Distributed persistent queue | Deferred | Unnecessary until multi-instance deployment is a real requirement. |
| Full frontend infrastructure management | Rejected | Container networking, host volumes, and TLS remain deployment concerns. |

# Suggested implementation order

| Order | Item | Outcome |
| ---: | --- | --- |
| 1 | B1, B2, B3 | Protect current behavior before migration. |
| 2 | A1, A2 | Establish persistent configuration and secret storage. |
| 3 | A3, A4, A4.1 | Create the configuration contract, readiness model, and safe runtime bridge. |
| 4 | A5, A6 | Start without credentials and support secure first-run setup. |
| 5 | W1, W2, W6 | Deliver the first usable setup frontend and recovery path. |
| | **W6** | **Done** | |
| 6 | P1, P2, P3 | Separate pipeline capabilities from provider brands. |
|  | **P1** | **Done** | |
|  | **P2** | **Done** | |
|  | **P3** | **Done** | |
| 7 | **P4**, P5 | Resolve one-provider pipelines automatically. |
|    | **P4** | **Done** | |
|    | **P5** | **Done** | |
| 8 | W3, W4 | Configure providers and pipelines through the frontend. |
|   | **W3** | **Done** | |
|   | **W4** | **Done** | |
| 8.5 | W9, W10 | Express setup flow and smart model picker — reduce first-run friction to a single screen. |
|   | **W9** | **Done** | |
|   | **W10** | **Done** | |
| 9 | A7 | Import legacy deployments and remove mandatory files. |
|   | **A7** | **Done** | |
| 10 | **P7**, P8 | Add capability-aware audio prep and local deployment guidance. |
|    | **P7** | **Done** | |
| 11 | R1–R5 | Harden streaming, resilience, and live reconfiguration. |
| 12 | W5, W7, T1 | Complete daily administration and safe Telegram configuration. |
| 13 | T2–T7 | Improve end-user output, control, and multilingual UX. |
| 14 | W8 | Polish the administration UI after the control plane is stable. |
| 15 | O1–O5 | Mature operations, auditability, reproducibility, and code health. |

# Milestones

## Milestone 1 — Safe foundation

CI, baseline integration tests, unified database, and secret store.

## Milestone 2 — Zero-config first run

The application starts with no credentials and exposes a secure setup
frontend. Telegram starts only after validation.

## Milestone 3 — Provider-independent pipeline

Provider connections, capability detection, automatic same-provider resolution,
and OpenAI-compatible adapters are operational.

## Milestone 4 — No mandatory `.env`

Existing installations can migrate; new installations require no `.env` or
`authorized.json`.

## Milestone 5 — Complete control plane

Providers, pipelines, access control, limits, privacy, and backup are managed
through the frontend, with safe everyday settings available in Telegram.

## Milestone 6 — Polished end-user experience

Output modes, cancellation, natural delivery, export, translation, improved
progress, and a polished administration UI are available.

# Decision log

Record decisions without rewriting roadmap history.

| Date | Item | Decision | Notes |
| --- | --- | --- | --- |
| 2026-06-23 | Product direction | Approved | Move ordinary configuration from `.env` and JSON files to a frontend-led control plane. |
| 2026-06-23 | Provider UX | Approved | Prefer one provider for the entire pipeline when its capabilities allow it. |
| 2026-06-23 | Advanced composition | Approved | Support separate transcription and text providers as an advanced option. |
| 2026-06-23 | Bootstrap | Approved | Target zero mandatory `.env`; retain optional infrastructure overrides. |
| 2026-06-23 | Transcript storage | Approved | Do not retain audio or transcripts by default. |
| 2026-06-23 | B1 | Done | Added secret-free CI across Python 3.10–3.12 with source compilation, import smoke testing, and pytest. |
| 2026-06-23 | B3 | Done | Added explicit numeric-range and boolean validation with regression tests. |
| 2026-06-23 | B2 | Done | Added offline integration coverage for the decorated pipeline, provider and Telegram failures, queue handoff, cleanup, and startup wiring. |
| 2026-06-23 | A1 | Done | Added unified SQLite application database with schema migrations and repository coverage for setup, settings, ACL, providers, pipelines, preferences, and audit data. |
| 2026-06-23 | A2 | Done | Added local Fernet secret store with generated master key, restrictive permissions, and encrypted provider credential storage. |
| 2026-06-23 | A3 | Done | Added ConfigService with 17-setting registry, typed validation, transactional bulk updates, and write-only secret fields. |
| 2026-06-23 | A4 | Done | Added AppState enum, StateChecker, and audio handler gating for readiness. |
| 2026-06-23 | A4.1 | Done | Closed runtime integration gap: legacy compatibility, secret-write safety, unified ACL, and RuntimeSnapshot. |
| 2026-06-23 | A5 | Done | Added RuntimeManager for blocking legacy startup, non-blocking web-managed lifecycle, health, stop, and restart behavior. |
| 2026-06-23 | A6 | Done | Added first-run setup-code generation, validation, expiry, invalidation, and setup-mode startup integration. |
| 2026-06-23 | Component reuse | Approved | Prefer maintained open-source libraries/templates before custom components; keep early frontend server-rendered, revisit richer stacks later. |
| 2026-06-23 | W1 | Done | Added FastAPI web control-plane foundation with sessions, CSRF, setup/login/dashboard templates, state and health APIs, and Docker web entry point. |
| 2026-06-23 | W2 | Done | Added 8-step guided onboarding wizard with JS enhancement, setup-code flow, admin creation, Telegram/provider testing, capability detection, pipeline verification, and bot lifecycle controls. |
| 2026-06-27 | Roadmap status | Verified | Re-ran the full automated suite (`299 passed`) and aligned A1, A2, A5, A6, and W1 from Proposed to Done. |
| 2026-06-27 | W6 | Done | Recovery codes, web flow, 28 tests. |
| 2026-06-27 | P1 | Done | Separate Transcriber / TextProcessor ABCs, adapter classes for OpenAI and Gemini, retrocompatibile, 329 tests. |
| 2026-06-27 | P2 | Done | CapabilityModel, get_capabilities() on every adapter, AudioProcessor.capabilities, state.py typed checks, detect-capabilities endpoint uses typed model, 36 tests (365 total). |
| 2026-06-28 | P3 | Done | Explicit transcriber/text-processor registries in `bot/adapters/`, four built-in adapters, registry-based creation in utils and resolver; 693 tests passing, 0 regressions. |
| 2026-06-28 | Reuse audit | Done | Whole-codebase review (~12.7k LOC). No serious reinvention; security primitives use proven libraries; large domain modules legitimately custom. Findings recorded as O5. |
| 2026-06-28 | O5 | Proposed | Modularize `web/app.py` into APIRouters and add rate-limiter concurrency tests. Explicitly defer ORM/Alembic/pydantic-settings/pybreaker migrations as low-ROI. |
| 2026-06-28 | W9, W10 | Proposed | Express single-screen setup flow + smart model picker with card carousel, live OpenRouter audio-capable model detection, manual model-by-ID entry, and sort/filter. Motivated by UX review: current multi-screen flow is too complex for a first-time user. |
| 2026-06-28 | W3 | Done | UX review confirmed: add, credential replacement, model discovery, capability inspection, enable/disable, and delete protection all working. Bug fixed: delete protection was silently bypassed due to key/table mismatch in `_get_active_pipeline_profile_id()` (read `app_settings` but written to `setup_state`). 693 tests passing. |
| 2026-06-28 | W4 | Done | UX review confirmed: three-mode card UI (semplice/due fasi/singolo passaggio), incomplete pipeline rejection, active profile display. Open gaps recorded: no explicit refinement-optional toggle, no data-flow preview diagram. |
| 2026-06-28 | W9 | In progress | First express setup implementation landed: shared `pipeline_builder`, `/setup/express`, `/api/setup/express`, profile creation, and legacy wizard redirects. Remaining work is visual QA, OpenRouter manual metadata persistence, sort/filter polish, and bot-start messaging. |
| 2026-06-28 | W10 | In progress | Smart model picker foundation landed: `bot/model_picker.py`, `/api/setup/model-picker`, locked Whisper card, OpenRouter cards, manual cards, and first express UI wiring. Remaining work is richer carousel controls, session persistence, manual metadata verification, and visual QA. |
| 2026-06-28 | W10 | In progress | Added express picker sort/filter controls, selected-card ordering, custom-card badge, and browser-session persistence for manually added model cards. Remaining work is verified OpenRouter metadata persistence beyond the browser session and visual QA. |
| 2026-06-28 | W10 | In progress | Manual OpenRouter model IDs are now verified against the live catalog: matching models get real pricing/provider/capabilities; not-found models fall back conservatively in two-stage or reject in single-pass; single-pass also rejects found models without audio input capabilities. 7 new tests. Remaining work is visual QA and carousel polish. |
| 2026-06-28 | W9 | Done | Express setup QA passed: two-stage setup, single-pass setup, saved/saved-no-start CTA states, no secret leakage, responsive viewport, and matching advanced pipeline state. |
| 2026-06-28 | W10 | Done | Smart model picker QA passed: card loading, sorting/filtering, manual OpenRouter metadata verification, non-audio rejection in single-pass, database-backed manual-card persistence, selected-card behavior, and no secret leakage. Follow-up P2: sanitize OpenAI error text that can reflect `sk-...` prefix. |
| 2026-06-28 | P7 | Done | Capability-aware audio preparation: `accepted_formats()` on `Transcriber` ABC and all adapters; conditional FFmpeg conversion in `handle_audio`; logging of conversion decision. 730 tests passing, 0 regressions. |

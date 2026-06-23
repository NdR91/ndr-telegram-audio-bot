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
| Status | Proposed |
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

**Done when**

- A blank data volume initializes safely.
- Schema upgrades are repeatable.
- Existing whitelist data can be imported.
- Backup boundaries are documented.

## A2 — Local secret store

| Field | Value |
| --- | --- |
| Status | Proposed |
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
| Status | Proposed |
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
| Status | Proposed |
| Priority | Critical |
| Effort | Medium |

On an empty data volume:

1. generate the database and master key;
2. generate a time-limited one-time setup code;
3. show the code in container logs;
4. expose only the setup workflow;
5. invalidate the code after the first administrator is created.

The setup code is stored only as a hash.

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
| Status | Proposed |
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

# Phase 2 — Frontend control plane

The first implementation should favor a small server-rendered frontend over a
large SPA unless interaction requirements prove otherwise.

## W1 — Frontend foundation

| Field | Value |
| --- | --- |
| Status | Proposed |
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
| Status | Proposed |
| Priority | Critical |
| Effort | High |

Wizard steps:

1. redeem setup code;
2. create the first administrator;
3. enter and verify the Telegram token;
4. connect the first AI service;
5. detect capabilities and models;
6. choose “use this service for everything” by default;
7. verify the resulting pipeline;
8. start the bot.

Users may save incomplete setup, but the UI must clearly show why audio
processing is unavailable.

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
| Status | Proposed |
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

**Manual verification** (from frontend)

- [ ] Add an OpenAI provider connection — confirm the form asks for name,
      endpoint (pre-filled), and API key (masked on save).
- [ ] After saving, confirm the provider appears in the list with its detected
      capabilities (transcription, refinement, streaming, etc.).
- [ ] Edit the provider name and confirm the change persists.
- [ ] Replace the API key — confirm the UI asks for a new key (does not show
      the old one in full) and the updated key works.
- [ ] Add a Gemini provider and confirm both are listed.
- [ ] Delete a provider that is not referenced by any pipeline — confirm it is
      removed immediately.
- [ ] Try to delete a provider that IS referenced by an active pipeline —
      confirm the UI shows a blocking error or requires a replacement first.
- [ ] Test a connectivity check on a provider with valid and invalid
      credentials — confirm success/failure feedback.

## W4 — Pipeline management

| Field | Value |
| --- | --- |
| Status | Proposed |
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

**Manual verification** (from frontend)

- [ ] In the pipeline section, confirm the default mode shows a single
      "preferred provider" selector — not separate transcription/text fields.
- [ ] Select one provider that supports both transcription and refinement —
      confirm the pipeline shows as complete.
- [ ] Switch to advanced mode — confirm the UI now shows independent
      selectors for transcription and text processing.
- [ ] Pick different providers for each stage and confirm the preview shows
      the expected data flow (audio → Transcriber A → TextProcessor B).
- [ ] Save an incomplete pipeline (e.g. transcription provider set but no
      text processor) — confirm the UI warns that the pipeline is incomplete
      and audio processing will not work.
- [ ] Enable "refinement optional" and confirm the pipeline is accepted with
      only a transcription provider.

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
| Status | Proposed |
| Priority | Critical |
| Effort | High |

After setup, support Telegram-based administrator login when practical.

Also provide a Telegram-independent recovery mechanism, such as:

```text
application recovery command -> time-limited one-time URL
```

Revoking or misconfiguring the Telegram token must not permanently lock out the
administrator.

**Manual verification** (from frontend)

- [ ] Log in via the normal admin login (username/password or Telegram if
      integrated).
- [ ] If Telegram-based login is available, confirm it works: send a command
      to the bot and receive a login link.
- [ ] Revoke or change the Telegram token while logged in — confirm the admin
      session remains active (admin is not locked out).
- [ ] Test the recovery mechanism: trigger a password reset or recovery code
      flow and confirm a time-limited one-time URL is generated.
- [ ] Use the recovery URL to log in — confirm it works only once and
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

# Phase 3 — Composable provider architecture

## P1 — Separate transcription and text processing

| Field | Value |
| --- | --- |
| Status | Proposed |
| Priority | Critical |
| Effort | High |

Replace the combined provider contract with:

- `Transcriber`;
- optional `TextProcessor`;
- normalized `TranscriptionResult`;
- normalized streaming events.

The result should preserve optional language, duration, segments, timestamps,
and speaker metadata without requiring the Telegram UI to expose them.

## P2 — Provider connection and capability model

| Field | Value |
| --- | --- |
| Status | Proposed |
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

## P3 — Adapter registry

| Field | Value |
| --- | --- |
| Status | Proposed |
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

## P4 — Automatic pipeline resolver

| Field | Value |
| --- | --- |
| Status | Proposed |
| Priority | Critical |
| Effort | High |

Resolve the simplest valid pipeline from:

- request mode;
- user/group preferences;
- selected pipeline profile;
- provider and model capabilities;
- system policy.

The resolver must explain invalid configurations in user-facing terms and
produce an immutable execution plan for each accepted request.

**Manual verification** (from frontend)

- [ ] Configure a provider that supports both transcription and text
      processing. In the pipeline page, confirm the resolver automatically
      selects it for both stages without requiring manual assignment.
- [ ] Configure two providers: one with only transcription capability, another
      with both. In the pipeline page, confirm the resolver picks the
      capable provider for the full pipeline by default.
- [ ] Disable refinement globally — confirm the resolver produces a
      transcription-only execution plan.
- [ ] Intentionally create an invalid configuration (e.g. remove all
      providers from a pipeline) — confirm the error message explains the
      problem in plain language (e.g. "No provider available for
      transcription").
- [ ] Send an audio to the bot — confirm the handler accepts or rejects it
      based on the resolved pipeline state.

## P5 — Same-provider default

| Field | Value |
| --- | --- |
| Status | Proposed |
| Priority | Critical |
| Effort | Medium |

When one provider connection supports transcription and text processing, use it
for both by default, even when it uses different models or endpoints
internally.

Do not expose separate provider choices during onboarding unless the preferred
connection cannot satisfy the requested behavior.

**Manual verification** (from frontend)

- [ ] During onboarding: add a provider that supports transcription and text
      processing — confirm the wizard offers "use this provider for everything"
      as the default option and does NOT expose separate transcription/text
      selectors.
- [ ] During onboarding: add a provider that supports only transcription —
      confirm the wizard explains that text refinement is disabled or asks to
      add a text-processing provider.
- [ ] In the admin pipeline page: when using default mode, confirm the UI
      shows a single provider selector, not two separate ones.

## P6 — Advanced multi-provider pipelines

| Field | Value |
| --- | --- |
| Status | Proposed |
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

**Manual verification** (from frontend)

- [ ] In the frontend, go to advanced pipeline settings and confirm you can
      independently select a transcription provider and a text-processing
      provider (different from each other).
- [ ] Configure fallback providers — confirm the UI shows a clear list of
      which provider is used at each stage, in what order, and why each
      fallback might be activated.
- [ ] Save the advanced configuration and confirm the preview shows the
      complete resolved pipeline with data flow labels (e.g. "Audio →
      Transcriber: OpenAI → Text: Gemini").
- [ ] Send audio to the bot — confirm it uses the correct provider mix.

## P7 — Capability-aware audio preparation

| Field | Value |
| --- | --- |
| Status | Proposed |
| Priority | High |
| Effort | Medium |

Skip conversion when the transcriber accepts the original format. Otherwise
normalize audio using speech-appropriate settings.

The execution plan should record why conversion is or is not required.

**Manual verification** (from frontend)

- [ ] Send an audio file in a format already supported by the transcriber
      (e.g. MP3 for OpenAI) — confirm the execution log shows "No conversion
      needed: format accepted by provider".
- [ ] Send an audio in an unsupported format (e.g. OGG) — confirm the audio
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
| 6 | P1, P2, P3 | Separate pipeline capabilities from provider brands. |
| 7 | P4, P5 | Resolve one-provider pipelines automatically. |
| 8 | W3, W4 | Configure providers and pipelines through the frontend. |
| 9 | A7 | Import legacy deployments and remove mandatory files. |
| 10 | R1–R5 | Harden streaming, resilience, and live reconfiguration. |
| 11 | P6–P8 | Add advanced composition, OpenRouter, Ollama, vLLM, and local deployment paths. |
| 12 | W5, W7, T1 | Complete daily administration and safe Telegram configuration. |
| 13 | T2–T7 | Improve end-user output, control, and multilingual UX. |
| 14 | O1–O4 | Mature operations, auditability, and reproducibility. |

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

Output modes, cancellation, natural delivery, export, translation, and improved
progress are available.

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
| 2026-06-23 | A3 | Done | Added ConfigService with 17-setting registry, typed validation, transactional bulk updates, and write-only secret fields. |
| 2026-06-23 | A4 | Done | Added AppState enum, StateChecker, and audio handler gating for readiness. |
| 2026-06-23 | A4.1 | Done | Closed runtime integration gap: legacy compatibility, secret-write safety, unified ACL, and RuntimeSnapshot. |

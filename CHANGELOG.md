# Changelog

All significant changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Added

- **Smart model picker foundation (W10)**: Added `bot/model_picker.py` with
  reusable card shaping for the express setup model picker: locked Whisper
  transcription card, OpenRouter shortlist filtering, per-million token pricing,
  qualitative speed/quality indicators, recommended-card selection, category
  counts, and conservative manual model cards. Added `/api/setup/model-picker`
  to return picker cards without persisting credentials or models.
- **Remove mandatory `.env` and `authorized.json` (A7)**: The application
  now starts without any environment files. `Config(relaxed=True)` provides
  empty defaults for all missing values (Telegram token, API keys,
  `authorized.json`). The CLI path (`bot/main.py`) catches `ConfigError`
  and falls back to relaxed mode. `RuntimeManager` resolves the Telegram
  token from the database via `ConfigService` + `SecretStore` decryption
  when the Config is relaxed. `RuntimeSnapshot.from_config_service()` accepts
  an optional Config parameter and resolves API keys from
  `provider_connections` when Config is unavailable. `WhitelistManager` and
  `create_application()` accept optional Config. The `StateChecker` legacy
  shortcut now requires a non-relaxed Config with a non-empty Telegram
  token. 693 passing tests (4 new, 0 regressions).
- **Runtime fallback execution (P4.2)**: `FallbackTranscriber` and
  `FallbackTextProcessor` wrappers in `bot/pipeline_resolver.py` that try
  the primary model then each fallback in order at runtime. Logs which model
  was used without exposing transcript/audio content. Raises clear user-facing
  errors when all models in a stage fail.
- **Delete/disable protection**: `delete_provider`, `delete_provider_model`,
  `update_provider_model(enabled=False)`, and `update_provider(enabled=False)`
  in the repository layer now raise `ResourceInUseError` when the provider or
  model is referenced by the active pipeline profile. Web endpoints handle the
  error and return a clear message. New `ResourceInUseError` exception in
  `bot/exceptions.py`.
- **Runtime fallback tests**: 12 new tests covering
  `FallbackTranscriber`/`FallbackTextProcessor` wrappers (primary succeeds,
  primary fails+fallback succeeds, all fail, no fallbacks) and resolver
  integration (fallback wrapper type checks).
- **Delete/disable protection tests**: 13 new tests covering provider deletion
  block, model deletion block, model disable block, fallback model deletion
  block, provider disable block, provider disable success paths, and UI
  integration for provider disable protection.
- **ROADMAP.md**: P4.2 section documenting runtime fallback execution. P6
  section updated with runtime fallback and delete/disable protection status.
  Test counts updated.
- **693 passing tests** (0 regressions).

- **Provider model management** (P3): New DB migration 002 creates
  `provider_models`, `pipeline_stages`, and `pipeline_stage_fallbacks` tables.
  `pipeline_profiles` gains a `mode` column (`two_stage` | `single_pass`).
  Existing profiles get `mode='two_stage'`; Gemini same-provider profiles
  auto-set to `single_pass`.
- **Repository methods**: `add/list/get/update/delete_provider_model`,
  `set_model_capabilities`, `toggle_model`, `add/list/update/delete_pipeline_stage`,
  `add/list/remove/reorder_stage_fallback`, `set/get_pipeline_profile_mode`.
- **Provider detail page** at `/admin/providers/{id}` with model table, discovery
  button, manual add, capability editor, and enable/disable toggle.
- **Pipeline page rewritten** with mode selection cards (two-stage / single-pass)
  and model-level selects per stage instead of provider-level references.
- **PipelineResolver rewritten** to use `ModelRef` with model-level capabilities,
  explicit pipeline stage resolution, fallback chain support, and single-pass
  pipeline mode.
- **CapabilityModel**: new `single_pass_audio_to_text` field for models that can
  transcribe and refine in a single API call.
- **OpenRouter metadata classification** (`_classify_openrouter_metadata`):
  new `single_pass_audio_to_text` field in returned dict.
- **OpenRouter guided model discovery**: provider model discovery now supports
  purpose-based filtering (`refinement`, `transcription`, `single_pass`,
  `all_recommended`, `all`) plus search and bounded limits. The admin UI imports
  small, role-specific shortlists instead of the full OpenRouter catalog.
- **OpenRouter catalog preview**: provider detail pages now support live
  OpenRouter catalog search before import, showing compact model candidates with
  capabilities, pricing, context length, and explicit per-model import actions.
- **Admin UI refresh**: provider, provider-detail, and pipeline pages now share
  a consistent page header, section layout, compact operational tables, and
  structured OpenRouter catalog cards for model discovery.

### Changed

- **Pipeline resolver (`resolve_from_profile`)** now resolves by model
  capabilities rather than provider-level capabilities. Supports explicit
  `pipeline_stages` with fallback chains. Falls back to legacy provider-level
  references when no stages exist.
- **Admin pipeline page** POST handler expects `model_entry_id` values instead
  of provider IDs for two-stage and single-pass modes.
- **Setup wizard** updated to create provider model entries alongside provider
  connections and pipeline profiles.
- **OpenRouter discovery UX** now guides administrators by pipeline role and
  explains that audio input is not the same as verified speech-to-text. The
  OpenRouter flow now previews a short searchable catalog before registering
  models, instead of making "search" immediately mutate the local model list.

- **Setup provider test & admin provider test now share exact same response
  schema** (`_test_provider_connection` in `bot/web/app.py`): both return
  `ok`, `auth_ok`, `models_ok`, `capabilities`, `pipeline_status`,
  `user_message`, `warnings`, and `models`.
- **OpenRouter metadata classification** (`_classify_openrouter_metadata`) now
  returned alongside `CapabilityModel` from `probe_openrouter_capabilities`,
  enabling callers to distinguish `audio_input` (audio input modality) from
  `transcription` (explicit STT). Audio-input-only models (e.g. Gemini on
  OpenRouter) no longer auto-set `transcription=True`.
- Setup UI: pipeline status badge, inline warnings, streaming badge, and
  disabled "Continue" for `not_compatible` / warning-enabled for
  `refinement_only`.

### Added

- New test suite `TestClassifyOpenRouterMetadata` (6 tests) covering
  audio-input vs transcription distinction.
- New integration tests for setup provider test: schema parity,
  audio-input-no-STT warning, transcription model flow, API key leak check.
- `_blank_test_result()` factory for consistent error response shape across
  provider test endpoints.

### Changed

- **OpenRouter capability detection made reliable (P2 extension)**:
  ``openai-compat`` adapter default ``transcription`` changed from ``True``
  to ``False`` (conservative), since not every OpenAI-compatible endpoint
  supports audio transcription. Existing provider connections with stored
  capability overrides are unaffected; the change only applies to the static
  default and to newly created providers that do not probe metadata.

- Docker Compose no longer requires local `.env` or `authorized.json` files
  for first-run setup. `.env` is now an optional override file, and the web
  setup wizard creates the first administrator instead of relying on a
  bind-mounted bootstrap ACL file.

### Added

- **OpenRouter capability probe** (``bot.capabilities.probe_openrouter_capabilities``):
  New async helper that fetches model metadata from the OpenRouter Models API
  (``GET /v1/models?output_modalities=all``) and classifies capabilities
  conservatively based on ``input_modalities``, ``output_modalities``,
  ``supported_parameters``, and model id/name heuristics.

  - ``transcription=True`` only when ``input_modalities`` includes ``"audio"``
    or the model id/name contains ``"whisper"`` / ``"audio"``.
  - ``text_generation=True`` / ``refinement=True`` when
    ``output_modalities`` explicitly includes ``"text"``.
  - ``streaming_refinement=True`` when ``supported_parameters`` includes
    ``"stream"`` (``False`` if absent or unknown).
  - Returns an all-``False`` model on API errors or unknown models.

- **Admin provider creation uses probe for OpenRouter**:
  ``/admin/providers/create`` now calls ``probe_openrouter_capabilities``
  when ``provider_type`` is ``"openrouter"``, storing accurate detected
  capabilities instead of optimistic static defaults.

- **Setup wizard capability detection probes OpenRouter**:
  ``/api/setup/detect-capabilities`` and ``/api/setup/test-provider``
  now probe model metadata for OpenRouter when provider credentials are
  available in the wizard state.

- **``detect_capabilities`` updated for ``openai-compat``**:
  Static detection for ``openai-compat`` now requires explicit audio
  keywords (``"whisper"``, ``"audio"``) in the model name to set
  ``transcription=True``, matching the conservative default.

- **31 new tests** (480 total, 0 regressions) covering:
  - ``_find_openrouter_model`` exact/substring/no-match logic.
  - ``_classify_openrouter_model`` for text-only, audio, multimodal,
    image-only, null-architecture, and keyword-detection scenarios.
  - ``probe_openrouter_capabilities`` with mocked HTTP (success, 403,
    network error, empty model list, empty model name).
  - Provider creation stores probed capabilities for text-only, audio,
    and probe-failure cases.
  - OpenAI and Gemini provider creation remains unchanged.
  - Setup wizard detect-capabilities probes OpenRouter correctly.

- **Provider management foundation (W3)**: Added an authenticated
  `/admin/providers` page for creating provider connections from the web UI,
  linked from the dashboard and pipeline pages. The initial form supports
  provider presets, endpoint/API-key/model fields, static capability
  detection, encrypted credential storage through `SecretStore`, and redirect
  back to pipeline activation.

- **Same-provider default (P5)**: Automatic same-provider pipeline
  resolution with profile-based configuration and simplified onboarding.

  - **`bot/pipeline_resolver.py`**: New `resolve_from_profile()` method
    that loads a saved pipeline profile from the database, retrieves the
    referenced provider connections, and builds an `ExecutionPlan`.
    Supports same-provider (single provider for all stages) and
    separate-provider profiles. Validates provider existence, enabled
    state, and capability requirements at resolution time.
  - **`bot/web/setup_wizard.py`**: New `create_pipeline_from_wizard()`
    function that reads saved wizard data from `setup_state`, creates a
    permanent `provider_connections` record, and creates a same-provider
    default `pipeline_profiles` record on wizard completion. New
    `get_active_pipeline_profile_id()` / `set_active_pipeline_profile_id()`
    helpers. `build_summary()` now includes `active_profile_id`.
  - **`bot/web/app.py`**: Wizard `step_verify` now calls
    `create_pipeline_from_wizard()` to persist the provider and profile.
    New `/admin/pipeline` page with default (single provider selector)
    and advanced (separate transcription/text selectors) modes. New
    `/api/pipeline/info` JSON endpoint. New `/admin/pipeline/save` form
    endpoint.
  - **`bot/web/templates/pipeline.html`**: New admin pipeline management
    page with mode toggle (default/advanced), provider selectors, and
    current status display.
  - **`bot/web/templates/setup.html`**: Step 6 (step_pipeline) is now
    adaptive to detected capabilities — auto-selects "use this provider
    for everything" when both transcription and refinement are supported,
    "transcription only" when refinement is unavailable, and shows
    capability badges and explanatory messages.
  - **17 new tests** (445 total, 0 regressions) covering
    `resolve_from_profile()` (same provider, separate providers, error
    cases), `create_pipeline_from_wizard()` (full wizard data, partial
    data, adapter type mapping).

- **Adapter registry (P3)**: Explicit registries for transcribers and
  text processors, replacing `if/elif` factory chains.

  - **`bot/adapters/`** package with registry module
    (`TranscriberRegistry`, `TextProcessorRegistry`) that supports
    direct and decorator-based registration.
  - **`OpenAICompatTranscriber`** and **`OpenAICompatTextProcessor`**
    adapters for OpenAI-compatible endpoints (OpenRouter, Ollama,
    vLLM, custom), configurable via `endpoint` parameter.
  - Default registrations for `openai-native`, `gemini-native`,
    `openai-compat` (plus backward-compatible short aliases `openai`
    and `gemini`).
  - `bot/utils.py` factories now delegate to the registry instead of
    `if/elif` chains; `bot/capabilities.py` extended with new
    adapter type defaults.
  - **37 new tests** (402 total, 0 regressions).

- **Automatic pipeline resolver (P4)**: Per-request pipeline resolution
  based on database provider connections and capabilities, replacing
  static `.env`-driven provider selection.

  - **`bot/pipeline_resolver.py`** module with :class:`PipelineResolver`,
    :class:`ExecutionPlan`, :class:`PipelineRequest`, and :class:`RequestMode`.
  - Resolver selects the simplest valid pipeline: prefers a single
    provider with both transcription and refinement; falls back to
    separate providers when needed.
  - User-facing error messages for invalid configurations (no providers,
    missing capabilities, unknown adapter types).
  - Immutable :class:`ExecutionPlan` with resolved `Transcriber` and
    `TextProcessor` instances, provider/model names, and a resolution log.
  - Integration in `bot/handlers/audio.py`: pipeline is resolved per-
    request; on resolution failure the user receives a clear explanation.
  - Resolver registered in `bot_data['pipeline_resolver']` via
    `bot/core/app.py` when a `DatabaseManager` is available.
  - `PipelineResolutionError` added to `bot/exceptions.py`.
  - **26 new tests** (428 total, 0 regressions).

### Added

- **First-run setup mode (A6)**: Time-limited one-time setup code for
  blank data volumes, preparing the application for guided onboarding
  (Phase 2 frontend).

  - **`bot/setup.py`** module provides ``generate_setup_code()``,
    ``validate_setup_code()``, ``invalidate_setup_code()``, and helper
    predicates (``is_first_run``, ``is_code_generated``).
  - Codes are cryptographically random alphanumeric strings (8 chars)
    stored only as SHA-256 hashes — never persisted in plaintext.
  - Expiry enforced server-side via a stored monotonic timestamp
    (default 30 minutes, configurable via ``SETUP_CODE_TTL_SECONDS``).
  - **Startup integration** in ``bot/main.py``: when the application
    state is ``SETUP_REQUIRED``, a code is generated and printed
    prominently in the container logs.
  - **State description** updated: the ``SETUP_REQUIRED`` message now
    references the setup code and guides the user to check logs.
  - **15 tests** covering generation, validation, invalidation, hash
    isolation, expiry rejection, and helper predicates.

- **Runtime manager (A5)**: Separated the Telegram bot lifecycle from
  the main application entry point in preparation for Phase 2 (web
  frontend).
  
  - **`RuntimeManager`** class (`bot/runtime_manager.py`) centralises
    bot lifecycle: `start(block=True|False)`, `stop()`, `restart()`,
    `run_until_stopped()` (legacy CLI), `get_state()`, `get_health()`,
    and `can_start()`.
  - **Blocking mode** (default): calls `Application.run_polling()`,
    preserving the current CLI behaviour and signal handling.
  - **Non-blocking mode** (`block=False`): calls `initialize()`,
    `start()`, and `updater.start_polling()` without idling, so the
    frontend can manage the bot alongside its own event loop.
  - **State gate**: `start()` raises `RuntimeError` unless the
    application state is `READY`.
  - **Health reporting**: `get_health()` returns bot status, state,
    and uptime for dashboards and health checks.
  - **Thread-safe**: `_app` reference protected by a lock so the
    frontend can start/stop from request handlers.
  - **Refactored `bot/main.py`**: uses `RuntimeManager.run_until_stopped()`
    instead of directly calling `create_application` + `run_application`.
  - **21 new tests** covering initialisation, state/health introspection,
    blocking and non-blocking start, stop, restart, error conditions, and
    legacy CLI entry point.

- **Web frontend foundation (W1)**: FastAPI-based control plane for the
  Telegram Audio Bot, providing setup wizard, authentication, dashboard,
  and API endpoints.

  - **`bot/web/` package** with modular structure: ``auth.py`` (session,
    CSRF, password), ``app.py`` (FastAPI factory), ``main.py`` (uvicorn
    entry point).
  - **Session-based authentication** using signed cookies
    (itsdangerous) with 24-hour expiry, ``HttpOnly``, ``SameSite=Strict``.
  - **CSRF protection** via per-session tokens validated on all
    state-changing requests.
  - **Admin password hashing** with bcrypt, stored in ``setup_state``.
  - **Setup wizard** (``/setup``): validates the one-time setup code
    (A6) and creates the first administrator account.
  - **Login/logout** (``/login``, ``/logout``) with bcrypt password
    verification.
  - **Dashboard** (``/admin/dashboard``): displays application state and
    bot health status.
  - **JSON API** (``/api/state``, ``/api/health``) for frontend
    JavaScript.
  - **Auto-start**: the bot starts automatically in the lifespan
    handler when the application state is ``READY``; setup mode logs
    the setup code URL.
  - **Docker integration**: updated Dockerfile CMD to use the web
    entry point, exposed port 8080 in ``docker-compose.yml``.
  - **7 Jinja2 templates** (base, setup, login, dashboard, error) with
    responsive CSS.
  - **Testable via TestClient**: ``create_app(config=...)`` accepts a
    mock config for isolated testing.

- **Runtime integration hardening (A4.1)**: Closed the gap between new
  database-backed services and the still-legacy runtime.
  
  - **Legacy compatibility**: `StateChecker` now accepts an optional
    `legacy_config` parameter. When a legacy `.env` + `authorized.json`
    deployment is detected (unified DB has no `admin_created`), the
    checker reports `READY` instead of blocking audio processing
    ([#1](https://github.com/nickdurantes/telegram-audio-bot/issues/1)).
  
  - **Secret write safety**: `ConfigService.update_setting()` and
    `update_settings()` now reject non-empty secret field writes when
    the `SecretStore` is unavailable or the encryption key is not loaded.
    Plaintext secrets are never persisted.
  
  - **Unified ACL source**: `WhitelistManager` now accepts an optional
    `DatabaseManager` parameter. When provided, all whitelist reads and
    writes go through the unified application database instead of the
    legacy `SQLiteWhitelistStore`, preventing the two stores from
    diverging.
  
  - **RuntimeSnapshot**: Added an immutable runtime-configuration
    snapshot (`bot/runtime.py`) built from either the legacy `Config`
    or the `ConfigService` with fallback. The snapshot is stored in
    `bot_data['runtime_snapshot']` and used to construct `RateLimiter`
    and `TelegramDeliveryAdapter` in `create_application()`.
  
  - **16 new regression tests** covering legacy compatibility (5 tests),
    secret-store rejection (5 tests), and `RuntimeSnapshot` construction
    and immutability (6 tests).

- Added unified application database (A1) with versioned SQLite schema,
  migration framework, and repository layer for configuration, access control,
  provider connections, pipeline profiles, preferences, and audit events.
- Added local secret store (A2) using Fernet authenticated encryption for
  at-rest credential protection, with automatic master-key generation on first
  startup and restrictive file permissions (600).
- Provider credentials are transparently encrypted at rest and decrypted on
  read when a SecretStore is configured on the DatabaseManager.
- Added `bot/database/` package containing `DatabaseManager`, `SecretStore`,
  schema definitions, and a repeatable migration system.
- Added 61 tests (45 for A1 schema/repository, 16 for A2 secret store)
  covering schema creation, migration idempotency, CRUD operations,
  encrypt/decrypt round-trips, cross-instance key compatibility, and
  transparent credential encryption in the provider repository.
- Added `APPLICATION_DB` environment variable to configure the unified database
  path (default: `<audio_dir>/app.sqlite3`).
- Added `MASTER_KEY_FILE` environment variable to override the master key
  path (default: `<audio_dir>/.master_key`).
- Added `cryptography~=38.0` to `requirements.txt` for Fernet encryption.
- Whitelist data from `authorized.json` is automatically imported into the
  unified database on first startup (idempotent — empty tables only).
- The database manager and secret store are exposed via
  `Application.bot_data['database_manager']` and
  `Application.bot_data['secret_store']` for use by future configuration and
  runtime services.
- Added least-privilege GitHub Actions CI for Python 3.10, 3.11, and 3.12,
  including source compilation, an import smoke test, and the complete pytest
  suite without runtime credentials.
- Added offline integration tests for the complete decorated audio pipeline,
  provider and Telegram failures, FIFO queue handoff, resource cleanup, and
  application startup wiring.

### Changed

- Added explicit range validation for rate-limit, queue, and provider
  resilience values, with errors naming the invalid environment variable.
- Boolean configuration now accepts only documented `1`/`0`,
  `true`/`false`, and `yes`/`no` values.

### Documentation

- Added a living `ROADMAP.md` with individually reviewable reliability, UX,
  feature, and operational improvements.
- Expanded the roadmap with a frontend-led zero-configuration control plane,
  encrypted configuration storage, provider capability resolution, local and
  cloud provider support, and a staged migration away from mandatory `.env`
  and `authorized.json` files.
- Removed the repository-specific `.opencode/` context bundle and the
  standalone OpenAgents installer script from the public repository.
- Aligned `README.md`, `AGENTS.md`, and `.env.example` with the current test
  suite, SQLite authorization persistence, provider streaming behavior, Docker
  workflow, and supported configuration variables.
- Added `CONTRIBUTING.md` with a reproducible local development and test
  workflow.
- Added `SECURITY.md` with responsible disclosure and deployment guidance for
  the public GitHub repository.
- Expanded `.gitignore` coverage for environment backups, SQLite databases,
  pytest caches, local agent context, and temporary workspace data.

### Technical Improvements
- **Refine-streaming hardening and rollout docs added** (`README.md`, `.env.example`, `tests/test_audio_errors.py`, `tests/test_streaming.py`)
  - *Issue*: The new refine streaming path still needed stronger edge-case coverage and explicit operator-facing guidance.
  - *Fix*: Added tests for missing `done` events, draft/fallback finalization behavior, and circuit-reset semantics; documented the current multi-provider refine streaming status and rollout expectations.
  - *Impact*: Safer production rollout and clearer operator understanding of what is live versus gated.

- **Gemini refine streaming added** (`bot/providers.py`, `tests/test_audio_errors.py`)
  - *Issue*: The refine streaming architecture remained OpenAI-first until a second provider implementation was added.
  - *Fix*: Added Gemini refine streaming under the same `RefineStreamEvent` contract, preserving provider-agnostic orchestration and fallback behavior.
  - *Impact*: The repository now supports true refine streaming across both current providers.

- **True refine-stream orchestration added** (`bot/handlers/audio.py`, `bot/ui/streaming.py`, `tests/test_streaming.py`, `tests/test_audio_errors.py`)
  - *Issue*: OpenAI refine streaming existed at the provider layer, but the audio pipeline and Telegram adapter still assumed precomputed final text.
  - *Fix*: Added delta-driven refine orchestration in the audio pipeline and extended the delivery adapter with progressive-response session methods for true provider-fed streaming.
  - *Impact*: The repository can now consume real provider refine deltas end-to-end instead of only simulating streaming from a full final result.

- **OpenAI Responses API refine streaming added** (`bot/providers.py`, `tests/test_audio_errors.py`)
  - *Issue*: The provider layer was structurally ready for refine streaming, but no real provider implementation existed yet.
  - *Fix*: Added OpenAI refine streaming using the Responses API with normalized `RefineStreamEvent` output, timeout/error mapping, and completion handling.
  - *Impact*: The repository now has its first true provider-level refine streaming implementation while keeping the architecture multi-provider.

- **Provider-agnostic refine streaming contract introduced** (`bot/providers.py`, `tests/test_utils.py`, `tests/test_audio_errors.py`)
  - *Issue*: Providers only exposed full-result refine methods, which blocked true provider-level streaming work.
  - *Fix*: Added a normalized `RefineStreamEvent`, explicit refine-streaming capability signaling, and a default fallback streaming interface that preserves compatibility for non-streaming providers.
  - *Impact*: The provider layer is now structurally ready for true refine streaming without becoming OpenAI-only.

- **PTB upgraded for future draft streaming support** (`requirements.txt`, `README.md`)
  - *Issue*: The repository was pinned to PTB 20.x, which does not expose `send_message_draft()` and did not include the `job-queue` extra by default.
  - *Fix*: Upgraded to `python-telegram-bot[job-queue]~=22.7` and documented the bundled job-queue support.
  - *Impact*: Removes the runtime JobQueue warning and prepares the codebase for Telegram draft streaming implementation.

- **Telegram delivery adapter introduced** (`bot/ui/streaming.py`, `bot/core/app.py`, `bot/handlers/audio.py`)
  - *Issue*: Final response delivery was embedded directly in the audio handler, leaving no clean boundary for future `sendMessageDraft` support.
  - *Fix*: Added an application-scoped delivery adapter that centralizes final response delivery and draft capability checks while preserving current fallback behavior.
  - *Impact*: Streaming work can now evolve behind a dedicated adapter instead of being spread across the audio pipeline.

- **Draft streaming feature flag added** (`bot/config.py`, `bot/ui/streaming.py`, `.env.example`, `README.md`)
  - *Issue*: The new progressive-output path needs a safe rollout and fast kill-switch before it is enabled in production.
  - *Fix*: Added `TELEGRAM_DRAFT_STREAMING` as an explicit feature flag, defaulting to off.
  - *Impact*: Future streaming rollout can be enabled deliberately and disabled quickly if needed.

- **Progressive final-text delivery added** (`bot/ui/streaming.py`)
  - *Issue*: The adapter existed, but final responses still used only the classic edit/send fallback path.
  - *Fix*: When the feature flag is enabled, the bot supports drafts, the chat is private, and the response fits a single message, the adapter now streams cumulative draft updates before finalizing the durable message.
  - *Impact*: First usable progressive Telegram UX is available with a safe fallback for all unsupported cases.

- **Streaming integration refined and expanded test coverage** (`bot/ui/streaming.py`, `tests/test_streaming.py`)
  - *Issue*: The first draft-streaming pass still needed clearer behavioral guarantees around long messages, final ack replacement, and multi-update draft flows.
  - *Fix*: Documented the adapter's final-message replacement behavior, kept long texts on the fallback path, and expanded tests for realistic draft/fallback cases.
  - *Impact*: Safer integration between the current progress UI and the new progressive-output path.

- **Streaming rollout/operator docs finalized** (`README.md`, `.env.example`, streaming roadmap)
  - *Issue*: The streaming implementation needed explicit documentation about what is already supported versus what is deferred.
  - *Fix*: Documented the private-chat/single-message constraints, fallback behavior, rollout guidance, and clarified that current streaming is progressive final-text delivery rather than provider token streaming.
  - *Impact*: Clearer operational expectations and easier rollout/testing.

## 🚀 v20260405 - Production Hardening, Queueing & Resilience

### Bug Fixes
- **Provider default model fallback** (`bot/utils.py`)
  - *Issue*: `create_provider()` passed `config.model_name` even when unset, overriding provider constructor defaults with `None`.
  - *Fix*: The provider factory now applies the correct OpenAI/Gemini default model when `LLM_MODEL` is absent.
  - *Impact*: Provider initialization now matches documented behavior and avoids invalid model configuration.

- **Docker image no longer copies `authorized.json`** (`Dockerfile`, `README.md`)
  - *Issue*: The Docker build baked runtime authorization data into the image with `COPY authorized.json .`.
  - *Fix*: `authorized.json` is now expected only as a runtime-mounted file; the image no longer copies it during build.
  - *Impact*: Better secret/config hygiene and more portable images.

- **Whitelist updates are now serialized and saved atomically** (`bot/handlers/admin.py`)
  - *Issue*: Admin whitelist changes could race under concurrent updates, and direct file writes risked partial/corrupt `authorized.json` contents.
  - *Fix*: Added a shared async lock around whitelist mutations and switched persistence to temp-file + `os.replace()` atomic writes.
  - *Impact*: Concurrent admin commands are safer and authorization data is more resilient to interrupted writes.

- **AppleDouble repository artifacts cleaned up** (`._*`, `.__*`)
  - *Issue*: macOS metadata files polluted the repository tree and context files, creating noise and confusion during review/navigation.
  - *Fix*: Removed AppleDouble artifacts from the tracked workspace while preserving the real project and `.opencode` files.
  - *Impact*: Cleaner repository state and less tooling/review noise.

- **Transcript logging privacy hardened** (`bot/providers.py`, `bot/main.py`, `.env.example`, `README.md`)
  - *Issue*: Default debug logging still included transcript/refined text previews, which could leak user content into logs.
  - *Fix*: Logs now hide transcript/refined content by default and emit only metadata unless `LOG_SENSITIVE_TEXT=1` is explicitly enabled; startup also warns when sensitive logging is enabled.
  - *Impact*: Better privacy by default while preserving an explicit opt-in path for deep debugging.

- **Minimal pytest suite added for core logic** (`tests/`, `requirements.txt`, `README.md`)
  - *Issue*: The repository had no automated regression checks for configuration, rate limiting, whitelist persistence, or provider factory defaults.
  - *Fix*: Added focused pytest coverage for `Config`, `RateLimiter`, `WhitelistManager`, and provider factory behavior, plus documented how to run the suite.
  - *Impact*: Higher confidence in critical local logic and a foundation for future test coverage.

- **Audio pipeline now uses typed stage exceptions** (`bot/exceptions.py`, `bot/decorators/timeout.py`, `bot/handlers/audio.py`, `bot/providers.py`, `bot/utils.py`)
  - *Issue*: Handler error responses depended on parsing exception strings, which was fragile and easy to break during refactors.
  - *Fix*: Introduced typed timeout/stage exceptions with user-facing messages attached, and updated the pipeline to catch those directly instead of string matching.
  - *Impact*: More reliable error handling and safer future refactors of the audio pipeline.

- **Application services moved off module globals** (`bot/core/app.py`, `bot/handlers/audio.py`, `bot/handlers/admin.py`, `bot/decorators/rate_limit.py`)
  - *Issue*: Audio processor, rate limiter, and whitelist manager were stored in module-level globals, making tests and lifecycle wiring more fragile.
  - *Fix*: These services are now created in `create_application()` and stored in `app.bot_data`, with handlers/decorators reading them from context instead of module globals.
  - *Impact*: Cleaner dependency injection, less hidden state, and easier testing.

- **Docker/runtime hardening improved** (`Dockerfile`, `docker-compose.yml`, `.dockerignore`, `README.md`)
  - *Issue*: The container still ran as root, build context included unnecessary local artifacts, and runtime mounts lacked extra hardening.
  - *Fix*: The image now runs as a non-root user, Compose mounts `authorized.json` read-only with `no-new-privileges`, and `.dockerignore` excludes secrets, temp files, tests, and local tooling context.
  - *Impact*: Smaller/cleaner build context and safer default container runtime posture.

- **Operational observability improved** (`bot/handlers/audio.py`, `bot/providers.py`, `bot/decorators/timeout.py`, `tests/test_audio_errors.py`)
  - *Issue*: Pipeline logs made it hard to understand per-stage latency and provider-specific failures in production.
  - *Fix*: Added stage duration logs, pipeline summary logs, and provider/stage failure metadata without exposing transcript content.
  - *Impact*: Easier production diagnosis for slow requests and provider issues.

- **Global-limit request queue added** (`bot/rate_limiter.py`, `bot/decorators/rate_limit.py`, `bot/config.py`, `bot/constants.py`)
  - *Issue*: Requests beyond the global concurrency limit were rejected immediately, creating unnecessary retry churn for users.
  - *Fix*: Added an optional FIFO queue with bounded size and per-user queue caps; queued requests now wait for the next available global slot instead of being dropped immediately.
  - *Impact*: Better UX under load while preserving per-user concurrency protection and queue safety bounds.

- **Whitelist persistence moved to SQLite** (`bot/auth_store.py`, `bot/handlers/admin.py`, `bot/decorators/auth.py`, `bot/config.py`)
  - *Issue*: `authorized.json` was fragile as the live persistence layer and conflicted with hardened read-only Docker mounts.
  - *Fix*: Added a SQLite-backed whitelist store, using `authorized.json` only as bootstrap input; admin changes now persist to `AUTHORIZED_DB`.
  - *Impact*: More robust persistence, safer container semantics, and cleaner separation between bootstrap config and mutable runtime state.

- **Provider circuit breaker added** (`bot/providers.py`, `bot/utils.py`, `bot/config.py`, `bot/exceptions.py`)
  - *Issue*: Repeated provider failures could keep hammering an unhealthy upstream and produce noisy repeated failures for users.
  - *Fix*: Added an optional circuit-breaker wrapper with failure threshold and cooldown settings; when open, it fails fast with a user-safe message.
  - *Impact*: Better resilience during provider incidents and less wasted upstream traffic.

## 🚀 v20250126 - Hardening & Operational Safety

### 🐛 Bug Fixes

#### Critical Fixes
1. **Temporary File Collision Under Concurrency** (`bot/handlers/audio.py:59-72`, `bot/handlers/audio.py:165-168`)
   - *Issue*: Temporary files were named only with `file_unique_id`, so re-sending the same Telegram file (or concurrent processing) could overwrite in-flight files.
   - *Fix*: File names now include `chat_id` + `message_id` + `file_unique_id` to guarantee per-request uniqueness.
   - *Impact*: Eliminates cross-request overwrites and cleanup races.

2. **FFmpeg Timeout Did Not Stop Conversion** (`bot/utils.py:9-45`, `bot/handlers/audio.py:81-88`)
   - *Issue*: Conversion ran in a thread and `asyncio.wait_for()` only cancelled the await; FFmpeg could keep running.
   - *Fix*: Conversion now uses `asyncio.create_subprocess_exec()` and kills FFmpeg on cancellation.
   - *Impact*: Prevents runaway FFmpeg processes and reduces resource leaks.

3. **Telegram Markdown Send Failures** (`bot/handlers/audio.py:113-132`, `bot/constants.py:70`)
   - *Issue*: `parse_mode="Markdown"` could break when LLM output contained special characters.
   - *Fix*: Responses are sent as plain text (no parse mode); completion header is no longer Markdown.
   - *Impact*: More robust delivery of arbitrary LLM text.

4. **Rate Limit Cooldown Not Enforced + Cleanup Race** (`bot/rate_limiter.py:9-77`, `bot/core/app.py:21-30`)
   - *Issue*: Cooldown config existed but wasn't applied; periodic cleanup mutated state without a lock.
   - *Fix*: Added per-user rejection cooldown tracking, async cleanup under lock, and updated the scheduled job to `await` cleanup.
   - *Impact*: Rate limiting now matches documented behavior and is safer under concurrency.

### 🔧 Technical Improvements
- **Bot Command Setup (Async-Safe)** (`bot/core/app.py`) - moved command registration to `ApplicationBuilder().post_init(...)` instead of `run_until_complete`.
- **Provider Timeouts** (`bot/providers.py`) - OpenAI uses SDK timeouts (`with_options(timeout=...)`, `max_retries=0`); Gemini calls are wrapped with `asyncio.wait_for()`.
- **Safer Startup Cleanup** (`bot/utils.py`, `.env.example`, `README.md`) - cleanup is guarded (refuses dangerous paths, only deletes known audio extensions) and can be disabled with `AUDIO_CLEANUP_ON_STARTUP=0`.
- **Sensitive Logging Guardrails** (`bot/providers.py`, `.env.example`) - logs show only preview+length; full text only with `LOG_SENSITIVE_TEXT=1` (DEBUG).

### 📦 Dependencies
- **Pinned Dependencies** (`requirements.txt`) - switched to compatible pins (`~=`) to reduce dependency drift.

---

## 🚀 v20260124.1 - Rate Limiting & Admin Security Fixes

### 🐛 Bug Fixes

#### Critical Fixes
1. **Rate Limiter Memory Leak** (`bot/core/app.py:21-30`)
   - *Issue*: The `cleanup_expired()` method existed but was never called, causing `_active_requests`, `_last_request_time`, and `_global_count` maps to grow indefinitely, leading to memory exhaustion over time.
   - *Fix*: Added background job `cleanup_rate_limiter_job()` that executes `cleanup_expired()` every hour (3600s), starting 60 seconds after bot launch. The job uses Telegram's `JobQueue` to run periodically.
   - *Impact*: Prevents memory leaks in long-running bot instances.

2. **Authorization Logic Duplication & Redundancy** (`bot/handlers/admin.py:127-141`)
   - *Issue*: Admin commands used both `@restricted` decorator AND redundant `validate_admin_access()` check inside the handler, creating:
     - Duplicate authorization logic (violating DRY principle)
     - Potential for future bugs if the two checks diverge
     - Unnecessary complexity
   - *Fix*: 
     - Added new `@admin_only` decorator in `bot/decorators/auth.py:50-79` that specifically checks admin access
     - Applied `@admin_only` to `whitelist_command_handler()` instead of `@restricted`
     - Removed `validate_admin_access()` call from the handler (now the decorator handles everything)
   - *Impact*: Cleaner, more maintainable authorization code. Eliminates risk of authorization logic divergence.

### 🔧 Technical Improvements
- **Background Job Scheduling**: Proper use of Telegram's `JobQueue` for periodic maintenance tasks
- **Authorization Simplification**: Single source of truth for admin access control

### 📦 Codebase Health
- **Reduced Complexity**: Eliminated duplicate authorization checks
- **Prevented Memory Leaks**: Added proper cleanup for rate limiter state
- **Improved Maintainability**: Clearer separation of concerns between general authorization (`@restricted`) and admin-only access (`@admin_only`)

---

## 🚀 v20260124 - Concurrent Processing Enabled

### ⚡ Concurrency & Performance
**Real Concurrency** (`bot/core/app.py:42`):
Added `concurrent_updates=True` to Telegram ApplicationBuilder. This enables
true parallel message processing using a thread pool, allowing multiple users
to process audio simultaneously.

**How It Works**:
- **Before** (`concurrent_updates=False`, default): Messages processed sequentially (1 at a time)
- **After** (`concurrent_updates=True`): Messages processed in parallel via thread pool
- **Rate Limiting Impact**: Rate limiter now enforces real concurrency limits (not just delays)

**Technical Details**:
- Telegram Bot Framework uses default sequential processing (`concurrent_updates=False`)
- This means messages are queued internally and processed one-by-one
- Rate limiter existed but was only delaying messages, not preventing concurrency
- With `concurrent_updates=True`, bot processes N messages simultaneously (N = thread pool size)
- Rate limiter now actually enforces the limits (2 per-user, 6 global)

**Example Scenario**:
```
Pre-v20260124 (Sequential):
User A: [Audio 1...90s] → [Audio 2...90s] → [Audio 3...90s]
Result: Always 1 request at a time

Post-v20260124 (Concurrent):
┌─────────────────────────────────────────────────────────────┐
│                    RATE LIMITER STATE                        │
├─────────────────────────────────────────────────────────────┤
│ Global Counter: [█][█][█][ ] [ ] [ ]  (3/6 used)            │
│ Per-User A:     [█][█]        (2/2 used)                    │
│ Per-User B:     [█]           (1/2 used)                    │
│ Per-User C:     [█]           (1/2 used)                    │
└─────────────────────────────────────────────────────────────┘

- User A: Audio 1 + Audio 2 → In processing (2/2 max)
- User B: Audio 3 → In processing (1/2)
- User C: Audio 4 → BLOCKED (User A has max, but global still has 3 slots free)
- Result: Real concurrency, rate limiter effective
```

### ⚠️ Important Notes for Users
- **Performance**: Higher throughput for multi-user scenarios
- **Resources**: Slightly increased CPU/memory usage (thread pool overhead)
- **Upgrade Path**: Restart required (no config changes)

---

## 🚀 v20260123 - Code Hardening & Stability Improvements

### 🔧 Code Hardening
Comprehensive code hardening and stability improvements focusing on reliability,
async consistency, and error handling. No new features, only strengthening of the
existing architecture.

#### Error Handling & Reliability
- **Provider Factory Refactoring**: Eliminated potentially thread-unsafe singleton pattern.
  *Improved thread-safety and reduced factory complexity.*

- **Exception Safety**: Completed guaranteed cleanup for Google Gemini remote files.
  *All error paths now execute cleanup via `finally` blocks.*

- **Async Stability**: Replaced blocking `time.sleep()` with `await asyncio.sleep()` in Gemini wait loop.
  *Resolved race conditions and improved async throughput.*

#### Resource Management
- **Memory Leak Prevention**: Each operation now has guaranteed provider and rate limiter lifetime.
  *No accumulation of instances or persistent state in memory.*

- **Rate Limiting Integration**: Completed rate limiter implementation with:
  * Max 2 concurrent requests per user
  * Global limit of 6 concurrent requests
  * File size limit of 20MB
  * 30-second cooldown period

### 🐛 Bug Fixes

#### Critical Bugs (Fixed)
1. **Infinite Loop in Gemini Upload** (`bot/providers.py:94-100`)
   - *Issue*: When `client.files.get()` failed, code used `break` but continued loop with old PROCESSING state → infinite loop.
   - *Fix*: Added `raise RuntimeError` instead of `break`.

2. **Resource Leak in Gemini Transcription** (`bot/providers.py:121-128`)
   - *Issue*: Google AI Studio remote files were not deleted on error.
   - *Fix*: Guaranteed cleanup with `finally` block in `GeminiProvider.transcribe_audio()`.

3. **Async Blocking in OpenAI** (`bot/providers.py:33-47`)
   - *Issue*: Synchronous OpenAI calls blocked the async event loop.
   - *Fix*: Wrapped all API calls with `await asyncio.to_thread()`.

4. **Auth Decorator Config Reload** (`bot/decorators/auth.py:35`)
   - *Issue*: Reloaded `Config()` from disk on every call (race condition, overhead).
   - *Fix*: Uses `context.bot_data['config']` (singleton injection).

5. **Import Path Hack** (`bot/handlers/audio.py:17-18`)
   - *Issue*: `sys.path.insert()` was fragile and non-idiomatic.
   - *Fix*: Correct imports `from bot import utils` and `from bot import constants`.

6. **Provider Factory Race Condition** (`bot/utils.py:18-32`)
   - *Issue*: Global singleton `_provider_instance` without thread-safety.
   - *Solution*: Removed singleton, factory creates new provider on each call.

7. **Missing Rate Limiter Initialization** (`bot/handlers/audio.py:291-299`)
   - *Issue*: `@rate_limited` decorator called `get_rate_limiter()` but initialization was missing.
   - *Fix*: Added `init_rate_limiter()` and config integration.

8. **Exception in format_response** (`bot/handlers/audio.py:105`)
   - *Issue*: If `get_provider()` failed, uncaught crash in `format_response`.
   - *Fix*: Provider initialized in `__init__` (always available).

#### Minor Bugs (Fixed)
- **No Rate Limiting**: Implemented complete rate limiter with config-based settings.
- **Crash Cleanup**: Added startup cleanup watchdog for residual files.
- **Memory Management**: Improved provider lifecycle management.

### 📦 Codebase Health
- **Reduced Technical Debt**: 10 critical issues identified, 8 fixed, 2 minor.
- **Enhanced Type Safety**: Improved type hints in providers and rate limiter.
- **Improved Logging**: Added specific logging for rate limiting and error handling.
- **Async Best Practices**: All code now async-compliant (no blocking calls).

### ⚠️ Important Notes for Users
- **No Breaking Changes**: All modifications are backward compatible.
- **Enhanced Configuration**: `.env.example` and `README.md` updated with rate limiting options (optional, defaults apply if not configured).
- **No Migration Required**: Existing `.env` files continue to work without changes.
- **Performance**: Stability improvements, no performance changes.
- **Upgrade Path**: Pull and restart, no migration required.

---

### 📚 Developer Notes

#### Code Hardening Principles Applied
1. **Fail Fast**: Early validation, specific error messages
2. **Resource Cleanup**: Guaranteed via try/finally blocks
3. **Async First**: All I/O operations async, no blocking calls
4. **Thread Safety**: Lock-free patterns where possible, immutability
5. **Defensive Programming**: Null checks, exception catching

#### Areas for Future Improvement
- **Circuit Breaker**: For API failure recovery
- **Metrics & Monitoring**: Prometheus counters for rate limits
- **Persistent State**: Redis for distributed rate limiting (multi-instance)
- **Request Queue**: Priority queue for urgent requests

## 🚀 v20260122.2 - Modular Architecture Refactoring & SDK Stabilization

### 🏗️ Architectural Refactoring
The codebase has been transformed from a monolithic architecture to a modular and scalable one to improve maintainability and facilitate future development.

- **Core Decomposition (`bot/main.py`)**:
  - The main file (reduced from ~320 to ~85 lines) now serves only as an entry point and bootstrapper.
  - Business logic has been migrated to specialized modules.

- **New Modular Structure**:
  - `bot/handlers/`: Specific logic for Telegram commands.
    - `audio.py`: Complete audio management pipeline (download, conversion, transcription).
    - `admin.py`: Whitelist management commands.
    - `commands.py`: Base commands (`/start`, `/help`, `/whoami`).
  - `bot/core/`: Application initialization and setup logic (`app.py`).
  - `bot/ui/`: User presentation and feedback management (`progress.py`).
  - `bot/decorators/`: Reusable cross-cutting logic (`auth.py`, `timeout.py`).

- **Unified Whitelist Management**:
  - Created `WhitelistManager` class to centralize permission management logic.
  - Eliminated code duplication in the 4 admin commands (`adduser`, `removeuser`, etc.), reducing cyclomatic complexity and improving robustness.

### 🔧 Technical Improvements & Fixes
These improvements were necessary to stabilize the new architecture and support the latest dependencies.

- **Google GenAI SDK v1.0 Compatibility**:
  - Complete update to the new SDK syntax `google-genai` >=1.0.0.
  - Fixed critical incompatibility in file upload: `client.files.upload` method now correctly uses the `file=` parameter (fixed `path=` parameter regression).

- **Async Stability & Telegram API v20+**:
  - Corrected coroutine handling for file downloading (`await file.download_to_drive()`).
  - Made the file type determination method asynchronous for full compatibility with the async ecosystem.

- **Smart Progress UI**:
  - Implemented progress message deduplication system.
  - Prevents "Message is not modified" warnings from Telegram API by avoiding redundant calls when status hasn't changed.
  - Added automatic state cache cleanup.

- **Import System Hardening**:
  - Configured `sys.path` in bootstrap to ensure consistent absolute imports.
  - Resolved circular import issues and dependencies between modules in Docker and local development environments.

### 📦 Codebase Health
- **Type Hints**: Extended type hint coverage to all new modules for better dev experience and safety.
- **Contextual Logging**: Improved logging to include specific context of the active module.

## 🚀 v20260122.1 - Real-time Progress Indicators & Google GenAI Migration

### ✨ New Features
- **Real-time Progress Indicators**: Added updates during audio processing with visual progress bars.
- **Improved UI Layout**: Progress messages now use a multi-line format with progress steps.
- **Improved Error Handling**: Specific timeout and error messages for each processing phase.
- **Elegant Header**: New design for the completion message with professional formatting.

### 🔧 Technical Improvements  
- **Google GenAI Migration**: Migrated from deprecated `google-generativeai` to the new `google-genai` SDK.
- **Timeout Management**: Added phase-specific timeout management (download: 30s, conversion: 60s, transcription: 120s, refinement: 90s).
- **Improved Cleanup**: Robust management of temporary files and remote file cleanup.

### 📦 Dependencies
- Updated `google-generativeai>=0.3.0` → `google-genai>=1.0.0`
- Default Gemini model updated to `gemini-2.0-flash`

### 📚 Documentation
- Technical documentation for Google GenAI SDK migration integrated into CHANGELOG.md.
- Complete documentation of breaking changes and migration benefits.

### 🐛 Bug Fixes
- Resolved potential memory leaks in file cleanup.
- Improved error recovery for API failures.

---

### 🔧 Technical Notes for Developers

#### Google GenAI SDK Migration (v20260122.1)
**Breaking Changes:**
- Install `google-genai>=1.0.0` instead of `google-generativeai>=0.3.0`
- The old package will be decommissioned on August 31, 2025

**Code Examples:**
```python
# Old SDK (deprecated)
import google.generativeai as genai
genai.configure(api_key=api_key)
model = genai.GenerativeModel(model_name)
response = model.generate_content(content)

# New SDK (current)
import google.genai as genai
client = genai.Client(api_key=api_key)
response = client.models.generate_content(model=model_name, contents=content)
```

**Migration Checklist:**
- [x] Updated requirements.txt
- [x] Modified providers.py with new SDK
- [x] Tested with gemini-2.0-flash
- [x] Improved error handling

**Implementation Notes:**
- `providers.py` contains full migration examples
- Detailed comments for every critical change
- Robust handling of remote file upload/download
- Graceful failure handling during progress updates

## 🚀 v20260122 - Centralized Configuration Management

### 📖 General Introduction
**Completely redesigned architecture** to improve reliability, maintainability, and developer experience. The previous fragmented system (with environment variables scattered across multiple files) caused runtime errors and made debugging difficult. Now all configuration is centralized with full startup validation, ensuring the bot never starts with incomplete or incorrect configurations.

### ✨ Key New Features
- **Centralized Configuration System**: **Completely redesigned architecture** with `Config` class unifiedly managing all settings (API tokens, provider selection, file paths, custom prompts), eliminating the risk of inconsistent configurations between different components.

- **Robust Error Handling**: **Completely redesigned architecture** with custom exception hierarchy (`ConfigError`, `MissingRequiredConfig`, `InvalidConfig`, `ExternalDependencyError`) to provide specific error messages and clear instructions on how to resolve configuration issues.

- **Fail-Fast Pre-Startup Validation**: **Completely redesigned architecture** with validation of all essential configurations before starting polling, preventing crashes during operation due to missing dependencies (like FFmpeg) or invalid tokens.

- **Centralized Prompt Management**: **Completely redesigned architecture** with centralized management of system and refinement templates, including automatic validation of the `{raw_text}` placeholder, avoiding custom prompt configuration errors.

### 🔧 Technical Improvements
- **Improved Dependency Injection**: **Completely redesigned architecture** with LLM providers now receiving prompts via dependency injection, improving testability and separating responsibilities.

- **Restructured Code Organization**: **Completely redesigned architecture** by moving all configuration logic from the main file to dedicated modules (`config.py`, `exceptions.py`), making the code more maintainable and readable.

- **External Dependency Validation**: **Completely redesigned architecture** with automatic check for FFmpeg with timeout and specific external dependency error handling.

- **Explanatory Error Messages**: **Completely redesigned architecture** with all error messages now including specific instructions on how to resolve the problem (e.g., link to get token from BotFather).

### 📦 Dependency Updates
- Added `python-dotenv>=1.0.0` to automatically load environment variables from the `.env` file, improving the development experience.

### 🐛 Bug Fixes
- **Transcription Type Fix**: Corrected typo in refinement template, improving internal documentation quality.

### ⚠️ Important Notes for Users
- **Assured Compatibility**: Existing `.env` files continue to work without changes, ensuring transparent migration for current users.

- **No Breaking Changes**: Internal architecture has changed but the public API and configuration methods remain compatible with the previous system.

- **Improved Diagnostics**: It is now much easier to identify and resolve configuration issues thanks to specific errors and step-by-step instructions provided automatically.

## v20260120 - Specialized System Prompt
### Changed
- Default `PROMPT_SYSTEM` replaced with a specialized prompt for audio transcription.
- Updated example in `README.md` to reflect the new default system prompt.

## v20260119.3 - Configurable Prompts & README Revision
### Added
- Support for prompt configuration via environment variables `PROMPT_SYSTEM` and `PROMPT_REFINE_TEMPLATE`.
- Improved default prompt to reduce introductory comments from Gemini ("Here is the reworked text...").

### Changed
- Completely revised `README.md` to reflect multi-provider architecture, configurable models, and new features.

## v20260119.2 - Google Gemini Implementation & Configurable Models
### Added
- Native support for **Google Gemini** for transcription and refinement.
- Support for LLM model configuration via `LLM_MODEL` environment variable.
- Ability to use various models without modifying code.
- Dependency `google-generativeai`.

### Fixed
- Fixed a bug where the Telegram message header always showed "GPT-4o mini" instead of the actually used model.

## v20260119.1 - Provider Abstraction
### Added
- Multi-provider support for LLM (Provider Agnostic).
- `LLM_PROVIDER` configuration in `.env`.

## v20260119 - Refactoring, Fixes & Optimization
### Added
- Automatic splitting of long messages (>4096 characters) to avoid Telegram sending errors.
- `bot/constants.py` file to centralize texts, prompts, and configurations.

### Changed
- Increased OpenAI token limit to 4096 (previously 1024) to support transcription of longer audio (15-20 min).
- Updated dependencies in `requirements.txt`: `openai>=1.0.0`.
- Updated `bot/utils.py` to use new OpenAI v1 client syntax.

### Removed
- `pydub` library (unused in code).

### Fixed
- **Critical**: Disk space leak. Temporary `.ogg` and `.mp3` files are now automatically deleted after use.

## [1.0.0] - Initial Version
- Basic audio transcription functionality (Voice Notes and Audio Files).
- OpenAI Whisper + GPT-4o-mini integration.
- Whitelist system (Admin, User, Group).

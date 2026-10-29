# V2 S6 retirement record

S6 makes the V2 application the only runtime shipped by this checkout. The old
V1 bot and its web configuration path remain available only in the operator's
preserved V1 image for rollback. `bot/migrate_v1.py` is the only code that reads
the V1 database schema; it accepts operator-created copies and is not called
during startup.

The retired source modules are the legacy provider adapters and factories
(`bot/adapters/`, `bot/providers.py`, `bot/utils.py`, and
`bot/pipeline_resolver.py`); V1 settings and runtime (`bot/config.py`,
`bot/config_service.py`, `bot/runtime.py`, `bot/runtime_manager.py`,
`bot/state.py`, `bot/setup.py`, and `bot/recovery.py`); V1 database and ACL
access (`bot/database/schema.py`, `migrations.py`, `repository.py`,
`bot/auth_store.py`); and their Telegram/web integrations (`bot/core/`,
`bot/decorators/`, `bot/handlers/admin.py`, `audio.py`, `commands.py`,
`bot/ui/progress.py`, `bot/ui/streaming.py`, and the V1 routes, helpers,
pipeline builder, setup wizard, and templates under `bot/web/`). V2 keeps the
shared secret store, the V2 repository, the admission controller used by its
Telegram poller, and plain-text Telegram delivery with chunk splitting.

The following root-level tests were retired because they exercised those
removed V1-only flows:

- Adapter/provider discovery and behavior: `test_adapter_registry.py`,
  `test_capabilities.py`, `test_model_picker.py`, `test_openai_compat.py`,
  `test_openrouter_probe.py`.
- V1 settings, repositories, pipeline and lifecycle: `test_config.py`,
  `test_config_service.py`, `test_database_repository.py`,
  `test_database_schema.py`, `test_pipeline_integration.py`,
  `test_pipeline_resolver.py`, `test_runtime_manager.py`,
  `test_runtime_snapshot.py`, `test_setup.py`, `test_state.py`.
- V1 Telegram, web, and feature wrappers: `test_admin.py`,
  `test_app_dependencies.py`, `test_audio_errors.py`,
  `test_pipeline_builder.py`, `test_rate_limiter.py`, `test_recovery.py`,
  `test_streaming.py`, `test_utils.py`, `test_web_app.py`, `test_web_auth.py`.

The V2 suite retains offline coverage for provider adapters and the role-aware
catalog, access and pairing rules, admission limits and cancellation, audio
cleanup on success/failure/timeout/shutdown, authenticated web routes, and
configuration testing/activation. `tests/test_secret_store.py` remains for the
shared encryption primitive; its old `DatabaseManager` integration cases were
removed with the V1 repository. `tests/v2/legacy_fixtures.py` builds only
synthetic SQLite schemas for migration tests.

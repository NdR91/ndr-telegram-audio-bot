# Changelog

All significant changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

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

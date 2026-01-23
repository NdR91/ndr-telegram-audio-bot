# Changelog

All significant changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

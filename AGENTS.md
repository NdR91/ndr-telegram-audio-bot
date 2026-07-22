# AGENTS.md

This file documents how agentic coding assistants should work in this
repository. Keep it aligned with the current codebase, `README.md`, and
`CONTRIBUTING.md`.

No Cursor rules or Copilot instructions are currently present in:

- `.cursor/rules/`
- `.cursorrules`
- `.github/copilot-instructions.md`

## Project summary

- Python Telegram bot that downloads audio, converts it with FFmpeg,
  transcribes it through an LLM provider, refines the transcript, and returns
  the result to Telegram.
- Providers: OpenAI Whisper plus GPT refinement, Google Gemini multimodal
  transcription/refinement, and OpenAI-compatible endpoints such as OpenRouter
  through the web-managed provider model.
- Entry point: `bot/main.py`.
- Runtime configuration is stored in the unified SQLite database.
- `authorized.json` is an optional bootstrap input for initial whitelist import.
- Runtime access-control changes are persisted in SQLite.

## Build, run, and operations

### Docker (recommended)

- Build and run: `docker compose up -d --build`
- View logs: `docker compose logs -f`
- Stop: `docker compose down`

The legacy `docker-compose` command may also work where Docker Compose v1 is
installed, but new documentation should use `docker compose`.

### Local run

- Create virtual environment: `python3 -m venv venv`
- Activate it: `source venv/bin/activate`
- Install dependencies: `python -m pip install -r requirements.txt`
- Run the bot: `python bot/main.py`

### Runtime prerequisites

- Python 3.10 or newer.
- FFmpeg available on `PATH`.
- FFmpeg and a writable data directory are enough for first startup.
- Telegram token, provider credentials, first administrator, and pipeline are
  configured through the web setup flow.
- `authorized.json` is optional legacy bootstrap input. When present, it should
  contain `admin`, `users`, and `groups` arrays; runtime whitelist state is
  managed in SQLite.

## Tests

An automated pytest suite is present under `tests/`.

- Full suite: `python -m pytest tests`
- Single file: `python -m pytest tests/test_config.py`
- Single test:
  `python -m pytest tests/test_config.py::test_config_loads_defaults_and_normalizes_ids`

Use `python -m pytest` so the repository root is consistently available on the
Python import path.

Before running the full suite, check whether the user has already run it
recently. If they say the suite is green, do not repeat it automatically for
documentation-only or narrow follow-up work; ask when they want it rerun. Use
targeted tests only when they are directly useful for the change in progress,
and report clearly when tests were not run because the user already verified
them.

## Linting and formatting

No dedicated linter or formatter is currently configured. Match the existing
style:

- 4-space indentation.
- Standard library imports, then third-party imports, then local imports.
- Explicit imports only.
- Type hints where they improve public interfaces.
- Implicit string concatenation for long prompt and message strings.
- Minimal comments, reserved for non-obvious behavior.

## Architecture and conventions

### Application lifecycle

- `bot/main.py` loads and validates configuration.
- `bot/core/app.py` builds the Telegram application and registers handlers.
- Application-scoped services are stored in `Application.bot_data`.
- Avoid introducing mutable module-level service singletons.

### Providers and prompts

- Provider interfaces and implementations live in `bot/providers.py`.
- Provider construction and provider-agnostic helpers live in `bot/utils.py`.
- Pipeline resolution (per-request provider selection from DB) lives in
  `bot/pipeline_resolver.py`.
- Prompt defaults are loaded by `bot/config.py`; user overrides come from
  `PROMPT_SYSTEM` and `PROMPT_REFINE_TEMPLATE`.
- Every refine template must contain `{raw_text}`.
- OpenAI uses Whisper for transcription and either Chat Completions or the
  Responses API for refinement.
- Gemini combines the system and refinement prompts into one request.

### Async and Telegram handlers

- Handlers are `async def` and await Telegram operations.
- Use decorators from `bot.decorators` for authorization, rate limiting, and
  timeout behavior.
- Keep command handlers small and keep pipeline logic in focused classes or
  helpers.
- Telegram responses must be split at `MAX_MESSAGE_LENGTH`.

### Error handling and cleanup

- Use custom exceptions from `bot.exceptions` for pipeline stages.
- Attach safe user-facing messages to pipeline exceptions.
- Log technical failures without logging transcript contents by default.
- Always clean up temporary local files in `finally`.
- Gemini uploads must also be cleaned up remotely.

### Access control

- `authorized.json` is bootstrap input and is not mutated at runtime.
- Live whitelist state is persisted in the SQLite file configured by
  `AUTHORIZED_DB`.
- Validate user and group IDs as integers.
- Preserve locking around concurrent whitelist changes.

## Documentation responsibilities

When behavior changes:

- Update `README.md` for user-facing setup, operation, or configuration.
- Update `.env.example` for environment variables.
- Update `CONTRIBUTING.md` for development workflow changes.
- Add an entry under `CHANGELOG.md` → `Unreleased`.
- Keep this file synchronized with repository commands and architecture.

## Security and repository hygiene

- Never read, edit, log, or commit `.env`, `.env.*`, `authorized.json`, API
  keys, tokens, transcripts, or the SQLite authorization database.
- Do not commit generated audio, virtual environments, caches, or AppleDouble
  `._*` files.
- Preserve the non-root Docker runtime and read-only `authorized.json` mount.
- Do not weaken file cleanup path validation.

## Repository layout

- `bot/main.py`: entry point and startup.
- `bot/core/app.py`: application construction and handler registration.
- `bot/handlers/`: commands, administration, and audio pipeline handlers.
- `bot/providers.py`: LLM provider implementations and resilience wrapper.
- `bot/config.py`: environment loading and validation.
- `bot/auth_store.py`: SQLite whitelist persistence (legacy).
- `bot/database/`: unified application database (schema, migrations, repository, secret store).
- `bot/runtime.py`: runtime configuration snapshot (A4.1) for immutable resolved config.
- `bot/rate_limiter.py`: concurrency admission and queueing.
- `bot/ui/`: progress and Telegram delivery adapters.
- `bot/utils.py`: FFmpeg conversion, cleanup, and provider factory.
- `bot/pipeline_resolver.py`: automatic pipeline resolution (P4).
- `tests/`: automated test suite.
- `audio_files/`: untracked runtime storage.

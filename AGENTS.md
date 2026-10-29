# AGENTS.md

This file documents how agentic coding assistants should work in this
repository. Keep it aligned with the current codebase, `README.md`, and
`CONTRIBUTING.md`.

## Approved V2 work across sessions

The user approved the V2 product, UX, and architecture design on 2026-10-25
and requested implementation across separate chats. Start with
`docs/V2-HANDOFF.md`, read the approved specification and plan index linked
there, and use `docs/V2-PROGRESS.md` to find the first incomplete session.
Implement one session per chat; update its checkboxes and progress evidence
before handing off. Do not restart product discovery or layout selection.
The V2 specification supersedes conflicting historical roadmap proposals.
S1–S6 are implemented in this development checkout; S7 remains before release.
Preserve all security and repository hygiene rules in this file.

No Cursor rules or Copilot instructions are currently present in:

- `.cursor/rules/`
- `.cursorrules`
- `.github/copilot-instructions.md`

## Project summary

- Python Telegram bot that downloads audio, converts it with FFmpeg,
  transcribes it through an LLM provider, refines the transcript, and returns
  the result to Telegram.
- Providers: OpenAI, Gemini, and OpenRouter, selected by an active V2
  configuration revision.
- Entry point: `bot/main.py`.
- Runtime configuration is stored in the unified SQLite database.
- V2 does not read `authorized.json`; legacy authorization data can enter only
  through the explicit migration command on operator-created copies.
- Runtime access-control changes are persisted in the V2 SQLite database.

## Build, run, and operations

### Docker (recommended)

- Build and run: `docker compose up -d --build`
- Optional legacy file mount, only when `authorized.json` already exists:
  `docker compose -f docker-compose.yml -f docker-compose.legacy.yml up -d --build`
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
  configured through the web setup flow. Generate the one-time V2 web setup
  code locally with `python -m bot.v2_setup_code`; never put it in app logs.
- `authorized.json` is not read by the V2 runtime. Never create a new bootstrap
  from it; see `docs/V2-MIGRATION.md` for the operator-run V1 copy import.

## Tests

An automated offline pytest suite is present under `tests/`.

- Full suite: use `.venv/bin/python` with `dotenv.load_dotenv` patched before
  collection as shown in `CONTRIBUTING.md`.
- V2 suite: `tests/v2/`, using temporary databases and synthetic data.
- Single file: `python -m pytest tests/v2/test_configuration.py`

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

- `bot/main.py` and `bot.web.main` run the same V2 FastAPI application.
- `bot/services.py` builds application-scoped services; the Telegram poller
  receives them through `Application.bot_data`.
- V2 runtime code must not import V1 pipeline resolution, configuration, or DB
  repository modules. `bot/migrate_v1.py` is the isolated V1 DB reader.

### Providers and prompts

- Provider interfaces and implementations live in `bot/transcription/`.
- Provider construction and provider-agnostic helpers live in
  `bot/transcription/pool.py` and `bot/transcription/runner.py`.
- The active immutable revision comes from `ConfigurationService`; environment
  values cannot override models, prompts, credentials, or runtime limits.
- Every refine template must contain `{raw_text}`.
- OpenAI uses Whisper for transcription and either Chat Completions or the
  Responses API for refinement.
- Gemini combines the system and refinement prompts into one request.

### Async and Telegram handlers

- Handlers are `async def` and await Telegram operations.
- Keep V2 authorization in `bot.access`, admission in `bot.rate_limiter`, and
  stage timeouts in `bot.transcription.runner`.
- Keep command handlers small and keep pipeline logic in focused classes or
  helpers.
- Telegram responses must be split at `MAX_MESSAGE_LENGTH`.

### Error handling and cleanup

- Use `ServiceFailure` from `bot.transcription.errors` for safe pipeline errors.
- Keep user-facing messages separate from provider exception bodies.
- Log technical failures without logging transcript contents by default.
- Always clean up temporary local files in `finally`.
- Gemini uploads must also be cleaned up remotely.

### Access control

- `authorized.json` is never read by V2. V1 ACL data is imported only by the
  copy-only `bot.migrate_v1` command.
- Live access state is persisted in the V2 application SQLite database.
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
- Preserve the non-root Docker runtime. The opt-in
  `docker-compose.legacy.yml` mount of `authorized.json` must remain read-only
  and must not create the host path when absent; default V2 startup works
  without that file.
- Do not weaken file cleanup path validation.

## Repository layout

- `bot/main.py`: unified V2 web entry point; `python bot/main.py` and
  `python -m bot.web.main` run the same FastAPI application.
- `bot/deployment.py`, `bot/services.py`: infrastructure-only V2 settings
  and application-scoped service composition.
- `bot/v2_runtime.py`: serialized Telegram poller, bot identity verification,
  and V2 audio handler lifecycle.
- `bot/access.py`, `bot/pairing.py`: contextual V2 ACL and one-time Telegram
  invitations that require browser confirmation.
- `bot/handlers/v2_admin.py`: V2 owner access commands backed by the shared ACL.
- `bot/v2_setup_code.py`, `bot/v2_recovery.py`: local one-time setup and
  recovery codes with UTC expiry; generated code values are printed only by
  explicit operator commands and are never written to application logs.
- `bot/handlers/v2_audio.py`, `v2_admin.py`, `pairing.py`: V2 Telegram
  processing, access administration, and pairing handlers.
- `bot/diagnostics.py`, `bot/web/routes_diagnostics.py`: bounded metadata-only
  events, health state, and authenticated diagnostics UI/API.
- `bot/migrate_v1.py`: explicit one-time import from copied V1 SQLite/key files;
  never called by runtime startup.
- `bot/database/v2_schema.py`, `v2_repository.py`: isolated V2 SQLite schema,
  migrations, transactional persistence, and import marker. The V1 schema is
  read only inside `bot/migrate_v1.py`.
- `bot/transcription/`: immutable contracts/configuration, provider adapters/catalog,
  credential-scoped pool, resilience, FFmpeg conversion, and two fixed audio paths.
- `bot/transcription/testing.py`: explicit revision-bound configuration tests
  through the shared runner; transcript output is memory-only and short-lived.
- `bot/web/v2_app.py`, `bot/web/templates/v2_transcription.html`, `bot/web/static/transcription.js`: authenticated V2 configuration, upload test, activation, and restore UI.
- `bot/web/templates/v2_access.html`, `bot/web/static/access.js`: authenticated V2 access management, guided pairing confirmation, advanced IDs and separate group membership/receipt status.
- `bot/web/templates/diagnostics.html`: overview and bounded diagnostic history.
- `bot/rate_limiter.py`: concurrency admission and queueing.
- `bot/ui/delivery.py`: plain-text Telegram delivery and message splitting.
- `docs/V2-MIGRATION.md`: operator-run migration and rollback instructions.
- `docs/V2-VALIDATION.md`: acceptance evidence, safe offline verification, and
  operator-only model, browser, and live release checks.
- `tests/`: automated test suite.
- `tests/v2/`: offline V2 tests with temporary SQLite databases and synthetic credentials.
- `audio_files/`: untracked runtime storage.

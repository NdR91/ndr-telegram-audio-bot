# AGENTS.md

This file documents how to work in this repository as an agentic coding assistant.
It focuses on build/run/test commands and the local coding conventions inferred
from the current codebase.

No Cursor rules or Copilot instructions were found in:
- `.cursor/rules/`
- `.cursorrules`
- `.github/copilot-instructions.md`

## Project summary
- Python Telegram bot that downloads audio, converts it with FFmpeg, transcribes
  it with an LLM provider, and returns refined text.
- Providers: OpenAI Whisper + chat completions, or Google Gemini multimodal.
- Entry point: `bot/main.py`.
- Config is via `.env` and `authorized.json` (both untracked).

## Build, run, and ops commands

### Docker (recommended)
- Build and run: `docker-compose up -d --build`
- View logs: `docker-compose logs -f`

### Local run (no Docker)
- Create venv: `python3 -m venv venv`
- Activate venv: `source venv/bin/activate`
- Install deps: `pip install -r requirements.txt`
- Run bot: `python bot/main.py`

### Runtime prerequisites
- FFmpeg must be installed and available on PATH.
- Valid Telegram token in `.env` (`TELEGRAM_TOKEN`).
- Provider API key in `.env` (`OPENAI_API_KEY` or `GEMINI_API_KEY`).
- Authorized user/group IDs in `authorized.json`.

## Tests
- No automated test suite is present in the repository.
- There are no test commands in README or scripts.
- If you add tests later, prefer `pytest` and document single-test usage as:
  `pytest path/to/test_file.py::test_name`.

## Linting and formatting
- No linting or formatting tools are configured.
- Keep formatting consistent with the existing code:
  - 4-space indents
  - Line breaks for long strings using implicit string concatenation
  - Minimal inline comments, only for non-obvious behavior

## Code style guidelines

### Imports
- Standard library imports first, then third-party, then local modules.
- Use explicit imports (no star imports).
- Keep import blocks compact with a single blank line between groups.

### Naming
- Modules: snake_case (`providers.py`, `constants.py`).
- Classes: PascalCase (`OpenAIProvider`, `GeminiProvider`).
- Functions: snake_case (`handle_audio`, `convert_to_mp3`).
- Constants: UPPER_SNAKE_CASE (`PROMPT_SYSTEM`, `MAX_MESSAGE_LENGTH`).

### Types and annotations
- Use type hints when they clarify public interfaces or factory functions.
- The codebase is partially typed; do not force types everywhere.

### Formatting
- Keep strings in ASCII when possible.
- For long prompt strings, use parentheses and implicit concatenation.
- Avoid trailing whitespace and keep files Unix-style.

### Logging
- Use the standard `logging` module.
- Log important steps in pipelines (download, conversion, provider calls).
- Use `logger.error` for failures and include exceptions where helpful.

### Error handling
- Fail fast on missing env variables at startup in `bot/main.py`.
- Raise `RuntimeError` for unrecoverable failures in helpers.
- For handler flows, catch exceptions and send a user-facing error message.
- Always clean up temporary files in a `finally` block.

### Async and Telegram handlers
- Handlers are `async def` and use `await` for Telegram operations.
- Use `ApplicationBuilder` and register handlers in `main()`.
- Keep command handlers small and focused on request/response.

### Prompts and LLM usage
- Prompts are defined in `bot/constants.py`.
- `PROMPT_SYSTEM` and `PROMPT_REFINE_TEMPLATE` can be overridden via `.env`.
- OpenAI uses a system+user chat format.
- Gemini combines the system prompt and refine template into one string.
- Always include `{raw_text}` in the refine template.

### Configuration files
- `.env` and `authorized.json` are required at runtime and must not be committed.
- Avoid logging secrets or tokens.

## Repo layout
- `bot/main.py`: Telegram bot entry point and handlers.
- `bot/providers.py`: LLM providers for transcription and refinement.
- `bot/utils.py`: FFmpeg conversion and provider factory.
- `bot/constants.py`: Messages, prompts, and config constants.
- `audio_files/`: Temporary storage for downloaded audio files.

## When adding new functionality
- Keep provider-agnostic logic in `bot/utils.py` and `bot/providers.py`.
- Add new constants to `bot/constants.py` and wire through env vars.
- Update `README.md` if you add or change configuration.
- Update `CHANGELOG.md` with a new dated version section.

## Security and safety
- Never commit `.env`, `authorized.json`, or any API keys.
- Validate IDs as integers before writing to `authorized.json`.
- Ensure cleanup of temporary audio files to avoid disk growth.

## Common pitfalls
- Telegram message length limit: split responses over 4000 chars.
- FFmpeg errors should surface as `RuntimeError` in helpers.
- Gemini file uploads can be asynchronous; wait for ACTIVE state.

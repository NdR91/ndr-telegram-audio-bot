# Contributing

Thanks for considering a contribution. This is a small project, so the most
useful contributions are focused fixes, clear tests, and documentation that
matches actual runtime behavior.

## Development setup

Requirements:

- Python 3.10 or newer.
- FFmpeg on `PATH`.

Create a local environment:

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
```

Do not commit local runtime configuration. The current V2 development startup
uses only infrastructure overrides (`AUDIO_DIR`, `APPLICATION_DB`,
`MASTER_KEY_FILE`, `WEB_HOST`, `WEB_PORT`, `WEB_LOG_LEVEL`, and
`WEB_SECURE_COOKIE`) and does not load `.env` or `authorized.json`.

The default Docker Compose configuration also works without `authorized.json`.
Only when that file already exists and a legacy tool needs it, opt into the
read-only mount with:

```bash
docker compose -f docker-compose.yml -f docker-compose.legacy.yml up -d --build
```

The override refuses to create a missing host path. V2 itself never reads this
file.

Run the web application with `python bot/main.py` or
`python -m bot.web.main`. Keep tests on temporary V2 databases, synthetic
credentials, and mocked Telegram/provider clients. Ordinary Telegram audio
uses the active V2 revision; tests must not contact Telegram or a live AI
service.

## Running tests

Run the complete suite:

```bash
.venv/bin/python - <<'PYTEST'
from unittest.mock import patch
import pytest
with patch('dotenv.load_dotenv', return_value=False):
    raise SystemExit(pytest.main(['-q', '-p', 'no:cacheprovider', 'tests/']))
PYTEST
```

For an isolated V2 run in environments containing local dotenv files, use the
development interpreter and patch `dotenv.load_dotenv` before pytest collection:

```bash
.venv/bin/python - <<'PY'
from unittest.mock import patch
import pytest
with patch('dotenv.load_dotenv', return_value=False):
    raise SystemExit(pytest.main(['-q', '-p', 'no:cacheprovider', 'tests/v2/']))
PY
```

Run a single test:

```bash
python -m pytest tests/v2/test_configuration.py
```

Use `python -m pytest` rather than the standalone `pytest` executable to keep
the repository root on the import path consistently.

V2 tests use synthetic settings and temporary SQLite files. Do not add tests
that depend on a contributor's `.env`, authorization database, or API
credentials.

GitHub Actions runs the suite on Python 3.10, 3.11, and 3.12. The workflow must
remain independent of repository secrets and real service credentials.
The current workflow also compiles Python sources and imports the `bot`
package before running the suite. These are configured CI jobs; do not describe
the matrix as passed unless the corresponding remote run is available.

## Making changes

- Keep provider-specific API behavior in `bot/transcription/adapters/`.
- Keep provider construction in `bot/transcription/pool.py` and audio
  orchestration in `bot/transcription/runner.py`.
- Keep `bot/migrate_v1.py` isolated from startup; use only operator-created
  copies of a legacy database and its key.
- Store application-scoped services in `Application.bot_data`.
- Use `ServiceFailure` with safe user-facing messages for pipeline failures.
- Preserve cleanup in `finally` blocks.
- Do not expose transcript contents in logs by default.
- Add or update tests for behavior changes.

There is currently no enforced formatter or linter. Follow the existing Python
style: 4-space indentation, grouped imports, explicit names, and focused
functions.

## Documentation

Update the relevant public documentation whenever behavior changes:

- `README.md` for setup, configuration, commands, and operation.
- infrastructure variable documentation if it changes; do not put provider
  settings back in environment configuration.
- `CHANGELOG.md` under `Unreleased`.
- `AGENTS.md` for repository workflow or architecture conventions.
- `docs/V2-VALIDATION.md` when acceptance evidence or operator release checks
  change; keep offline mock results separate from browser/live evidence.

## Pull requests

A good pull request:

- explains the user-visible or operational problem;
- keeps unrelated changes out of scope;
- includes tests where practical;
- confirms `python -m pytest tests` passes;
- does not contain credentials, runtime data, or generated audio.

## Security and privacy

Never include Telegram tokens, provider keys, `.env` files,
`authorized.json`, authorization databases, user audio, or transcripts in an
issue, commit, test fixture, or pull request.

For vulnerabilities, follow [SECURITY.md](SECURITY.md).

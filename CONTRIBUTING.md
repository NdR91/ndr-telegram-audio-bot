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

Do not commit local runtime configuration. Create `.env` and `authorized.json`
only when you need to run the complete bot.

## Running tests

Run the complete suite:

```bash
python -m pytest tests
```

Run a single test:

```bash
python -m pytest tests/test_config.py::test_config_loads_defaults_and_normalizes_ids
```

Use `python -m pytest` rather than the standalone `pytest` executable to keep
the repository root on the import path consistently.

Tests that instantiate `Config` mock external configuration and FFmpeg checks;
they must not depend on a contributor's real `.env` or API credentials.

## Making changes

- Keep provider-specific API behavior in `bot/providers.py`.
- Keep provider construction and shared media helpers in `bot/utils.py`.
- Store application-scoped services in `Application.bot_data`.
- Use typed pipeline exceptions with safe user-facing messages.
- Preserve cleanup in `finally` blocks.
- Do not expose transcript contents in logs by default.
- Add or update tests for behavior changes.

There is currently no enforced formatter or linter. Follow the existing Python
style: 4-space indentation, grouped imports, explicit names, and focused
functions.

## Documentation

Update the relevant public documentation whenever behavior changes:

- `README.md` for setup, configuration, commands, and operation.
- `.env.example` for environment variables.
- `CHANGELOG.md` under `Unreleased`.
- `AGENTS.md` for repository workflow or architecture conventions.

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


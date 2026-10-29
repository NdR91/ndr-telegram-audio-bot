# Telegram Audio Transcriber Bot

A self-hosted Telegram bot that turns voice notes and audio files into faithful
text. Configure OpenAI, Gemini, or OpenRouter from the browser, test a draft,
and activate it before Telegram uses it.

This repository is also a practical experiment in agent-assisted software
development. The project began as a learning exercise, but the bot is used in
practice and is maintained as a small, real-world application.

This checkout contains the V2 implementation through S6 and is still under
development, not a production release. V2 provides browser setup, authenticated
configuration and access pages, explicit audio tests, Telegram audio handling,
bounded metadata-only diagnostics, and an operator-run V1 migration. No real
database migration or external Telegram/provider call was performed during
development.

## Features

- OpenAI, Gemini, and OpenRouter through a role-aware model catalog.
- Two fixed audio paths: multimodal, or transcription and refinement on the
  same service.
- Private voice/audio processing and automatic processing in authorized groups.
- A browser workbench with distinct active and draft revisions; activation
  requires an explicit successful test of the current draft.
- Authenticated owner, private-user, and group access management.
- A dashboard and diagnostics page showing runtime, test, and group-receipt
  state without storing transcripts or provider response bodies.
- Bounded concurrency, queue, timeout, file-size, and provider-resilience limits.
- Temporary-file and remote-upload cleanup, long-message splitting, and a
  non-root Docker runtime.

## How it works

```text
Telegram audio
      |
      v
Download -> FFmpeg MP3 conversion -> Provider transcription
      -> LLM refinement -> Telegram delivery
```

The selected provider and models come from the active SQLite configuration.
Environment variables cannot override models, prompts, provider credentials,
or processing limits. A test result stays in process memory briefly; the
database stores only the proof metadata required for activation.

## Requirements

- Docker with Docker Compose, or Python 3.10+.
- FFmpeg when running without Docker.
- A Telegram bot token from [@BotFather](https://t.me/BotFather).
- A credential for OpenAI, Gemini, or OpenRouter.

## Quick start with Docker

1. Clone the repository:

   ```bash
   git clone https://github.com/NdR91/ndr-telegram-audio-bot.git
   cd ndr-telegram-audio-bot
   ```

2. Start the application:

   ```bash
   docker compose up -d --build
   docker compose logs -f
   ```

3. Generate a local one-time setup code with
   `docker compose exec bot python -m bot.v2_setup_code`, open the web UI at
   `http://localhost:8086`, and create the web administrator. Then connect
   the Telegram bot and confirm the proposed owner from the browser. The web
   password may be any nonempty value; no length or character-class rules apply.

Manage private users and authorized groups at `/admin/access`. Guided invites
require confirmation of the identity observed by the bot; operators can also
enter a known Telegram ID directly. Private authorization does not grant access
in groups. In an authorized group, new voice messages from human members are
handled automatically. To receive ordinary group messages, Telegram requires
the bot to be an administrator or to have privacy mode disabled in @BotFather;
after changing privacy mode, remove and re-add the bot. The Access page keeps
bot membership and the most recent observed voice receipt as separate states.
It does not retrieve group history.

This checkout's V2 implementation is not a production release. A browser test
uses the configured provider only after the operator uploads audio and presses
“Avvia prova”; its temporary file is removed after processing. Telegram uses
only the active revision, and the bot removes its downloaded audio after the
request completes. The diagnostics page retains at most 100 metadata events for
seven days.

For local administrator recovery, run
`docker compose exec bot python -m bot.v2_recovery` (or
`python -m bot.v2_recovery` for a local install). It
prints a one-time code that expires after 30 minutes; enter it at `/recovery`.
The code is generated only when requested and is never written to application
logs. If the initial setup code expires, rerun `python -m bot.v2_setup_code`.

Login, initial setup, and recovery each allow five attempts per client socket
address in a 15-minute window; further requests receive `429` with a
`Retry-After` header. Forwarding headers such as `X-Forwarded-For` are ignored,
so clients reaching the app through the same proxy share that limit.

Docker stores the application database, encrypted secrets, and temporary audio
files under the writable `audio_files/` volume. Compose may pass values from an
optional `.env` file into the container, but V2 reads only the infrastructure
overrides documented in the Local installation section. The local V2 startup
does not load `.env`. The default Compose setup does not mount
`authorized.json`, and V2 never reads it. If the legacy file already exists
and an operator needs to expose it to a legacy tool, use this opt-in read-only
override:

```bash
docker compose -f docker-compose.yml -f docker-compose.legacy.yml up -d --build
```

The override mounts `./authorized.json` at `/app/authorized.json` read-only and
refuses to create the host path when it is missing. Use the default
`docker compose up -d --build` command for empty V2 setup.
Back up the database and `.master_key-v2` together from this volume and keep the
backup private; the encrypted credentials cannot be recovered from the database
without the matching key. Do not use a running database file copy as a backup.

## Local installation

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
python -m bot.v2_setup_code
python bot/main.py
```

Both `python bot/main.py` and `python -m bot.web.main` start the same web
application. The V2 first run uses `audio_files/app-v2.sqlite3` and
`audio_files/.master_key-v2`; it does not read legacy bot settings or
credentials. A legacy database override is rejected and is never migrated
automatically. Infrastructure overrides are `AUDIO_DIR`, `APPLICATION_DB`,
`MASTER_KEY_FILE`, `WEB_HOST`, `WEB_PORT`, `WEB_LOG_LEVEL`, and
`WEB_SECURE_COOKIE` (0 or 1). Do not set `APPLICATION_DB` to the V1 database.

FFmpeg must be available on `PATH` for the later transcription runtime. On Debian or Ubuntu:

```bash
sudo apt-get install ffmpeg
```

On macOS with Homebrew:

```bash
brew install ffmpeg
```

## Configuration

V2 settings, credentials, ACL, active and draft revisions are stored in the
separate application database. The V2 runtime ignores legacy model and prompt
environment variables and never imports `authorized.json`. Use the browser to
configure and test a draft before activation.

The authenticated overview and `/admin/diagnostics` page show whether a
configuration is active, Telegram is running, a matching test succeeded, and a
group voice was received. The database retains at most 100 diagnostic events
for seven days; transcripts, audio, credentials, and provider response bodies
are excluded.

For an existing V1 install, follow [V2 migration and rollback instructions](docs/V2-MIGRATION.md).
The migration command is manual, reads only operator-created copies, and never
activates or certifies an imported profile. No real migration is performed by
the application or by this development work.

After setup, choose one provider and either its multimodal path or its
transcription-plus-refinement path. Save a draft, upload a synthetic or
authorized audio sample, and press **Avvia prova**. Only a successful test of
the unchanged draft and credential version enables **Attiva**. A later edit
requires another test. Provider/model catalog entries are candidates, not
quality recommendations. Use [V2 validation](docs/V2-VALIDATION.md) for the
operator's model comparison and release checks.

To recover from a failed update, stop the current container and restore the
matching application database and master key from the same backup. For a V1
rollback, follow the migration instructions and restart the preserved V1
image with its original volume; V2 changes do not sync back to V1.

The superseded V1 runtime modules and tests retired in S6 are listed in
[the S6 retirement record](docs/V2-S6-RETIREMENT.md), with the V2 coverage that
replaces them.

V2 reads only the infrastructure overrides documented in the Local
installation section. Provider models, credentials, prompts, access, and runtime
limits are managed through the authenticated browser UI and stored in its V2
database. The former V1 environment-based configuration and Telegram admin
commands have been retired from this checkout; V1 installs can follow the
copy-only migration procedure above or roll back using their preserved V1
image and volume.

## Tests

Install the dependencies first, then run:

```bash
.venv/bin/python - <<'PYTEST'
from unittest.mock import patch
import pytest
with patch('dotenv.load_dotenv', return_value=False):
    raise SystemExit(pytest.main(['-q', '-p', 'no:cacheprovider', 'tests/']))
PYTEST
```

Run one V2 test with `python -m pytest tests/v2/test_configuration.py`.
The complete suite uses temporary databases, synthetic credentials, and
mocked Telegram/provider clients; it makes no live service calls.

## Troubleshooting

- Setup code rejected: generate a fresh local code with
  `python -m bot.v2_setup_code` and use it before its expiry.
- Provider key rejected, quota reached, or model unavailable: check the
  provider account and current model catalog, replace the credential if needed,
  save the draft, run a new test, then activate it.
- V2 cannot open its database: check that `AUDIO_DIR` is writable and that
  `APPLICATION_DB` and `MASTER_KEY_FILE` refer to the V2 files.
- Telegram `409 Conflict`: another process may be polling with the same bot
  token; stop the other poller before retrying.
- Audio rejected: check the active revision's file-size limit and review
  `/admin/diagnostics` for a safe stage and error code.
- Group voice not processed: confirm that the group is authorized and that
  Telegram privacy mode allows ordinary group messages to reach the bot.
- Provider temporarily unavailable: review the diagnostics event and retry
  after the upstream service recovers.

## Project structure

```text
.
├── bot/
│   ├── main.py                  # Unified V2 web entry point
│   ├── deployment.py, services.py # Infrastructure and app-scoped services
│   ├── access.py, pairing.py    # Contextual access and guided invitations
│   ├── diagnostics.py           # Bounded metadata-only diagnostic events
│   ├── migrate_v1.py            # Explicit, copy-only V1 importer
│   ├── database/                # V2 schema, repository, and secret store
│   ├── transcription/           # Immutable config, adapters, and audio runner
│   ├── handlers/                # V2 access and Telegram audio handlers
│   └── web/                     # Authenticated V2 pages, routes, and assets
├── tests/v2/                    # Offline tests and synthetic migration fixtures
├── docs/V2-MIGRATION.md         # Operator migration and rollback procedure
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

## Contributing and security

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the local
workflow, [ROADMAP.md](ROADMAP.md) for proposed evolution, and
[SECURITY.md](SECURITY.md) for responsible vulnerability reporting.

Never commit `.env`, `authorized.json`, SQLite authorization data, API keys,
Telegram tokens, or user audio/transcripts.

## License

Released under the [MIT License](LICENSE).

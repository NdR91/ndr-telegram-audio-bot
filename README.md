# Telegram Audio Transcriber Bot

A self-hosted Telegram bot that turns voice notes and audio files into polished
text. It downloads the media, converts it with FFmpeg, transcribes it with
OpenAI or Google Gemini, refines the transcript, and safely delivers long
results back to Telegram.

This repository is also a practical experiment in agent-assisted software
development. The project began as a learning exercise, but the bot is used in
practice and is maintained as a small, real-world application.

## Features

- OpenAI and Google Gemini provider implementations.
- Voice note, audio, and audio-document support.
- FFmpeg conversion with stage-specific timeouts.
- Configurable transcript-refinement prompts.
- Provider-level refinement streaming for OpenAI and Gemini.
- Optional Telegram draft delivery in supported private chats.
- Safe fallback to normal Telegram messages and automatic long-text splitting.
- Per-user and global concurrency limits with an optional bounded queue.
- Provider circuit breaker for temporary upstream failures.
- SQLite-backed runtime access control, bootstrapped from `authorized.json`.
- Privacy-conscious logging and automatic temporary-file cleanup.
- Non-root Docker image and automated pytest coverage.

## How it works

```text
Telegram audio
      |
      v
Download -> FFmpeg MP3 conversion -> Provider transcription
      -> LLM refinement -> Telegram delivery
```

OpenAI uses Whisper for transcription and the configured GPT model for
refinement. Gemini processes the uploaded audio directly and uses the same
configured model for refinement.

## Requirements

- Docker with Docker Compose, or Python 3.10+.
- FFmpeg when running without Docker.
- A Telegram bot token from [@BotFather](https://t.me/BotFather).
- An OpenAI API key or Google Gemini API key.

## Quick start with Docker

1. Clone the repository:

   ```bash
   git clone https://github.com/NdR91/ndr-telegram-audio-bot.git
   cd ndr-telegram-audio-bot
   ```

2. Create the environment file:

   ```bash
   cp .env.example .env
   ```

   Set `TELEGRAM_TOKEN`, select `LLM_PROVIDER`, and provide the corresponding
   API key.

3. Create `authorized.json`:

   ```json
   {
     "admin": [123456789],
     "users": [],
     "groups": []
   }
   ```

4. Start the bot:

   ```bash
   docker compose up -d --build
   docker compose logs -f
   ```

Docker mounts `authorized.json` read-only and stores the mutable whitelist
database under the writable `audio_files/` volume.

## Local installation

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
python bot/main.py
```

FFmpeg must be available on `PATH`. On Debian or Ubuntu:

```bash
sudo apt-get install ffmpeg
```

On macOS with Homebrew:

```bash
brew install ffmpeg
```

## Configuration

Copy `.env.example` to `.env`. The following variables are supported.

### Required settings

| Variable | Description |
| --- | --- |
| `TELEGRAM_TOKEN` | Telegram bot token. |
| `LLM_PROVIDER` | `openai` or `gemini`; defaults to `openai`. |
| `OPENAI_API_KEY` | Required when using OpenAI. |
| `GEMINI_API_KEY` | Required when using Gemini. |

### Provider and prompt settings

| Variable | Default | Description |
| --- | --- | --- |
| `LLM_MODEL` | Provider-specific | Refinement model. OpenAI defaults to `gpt-4o-mini`; Gemini defaults to `gemini-2.0-flash`. |
| `PROMPT_SYSTEM` | Built in | System instruction used during refinement. |
| `PROMPT_REFINE_TEMPLATE` | Built in | Refinement template; it must contain `{raw_text}`. |

OpenAI transcription always uses `whisper-1`; `LLM_MODEL` controls the
refinement model.

### Paths and persistence

| Variable | Default | Description |
| --- | --- | --- |
| `AUTHORIZED_FILE` | `authorized.json` | Bootstrap access-control file. |
| `AUTHORIZED_DB` | `audio_files/authorized.sqlite3` | Mutable SQLite whitelist database. |
| `AUDIO_DIR` | `audio_files` | Temporary audio directory. |
| `AUDIO_CLEANUP_ON_STARTUP` | `1` | Remove known temporary audio formats at startup. |

`authorized.json` is read at every startup for validation, but it only seeds
the SQLite database when that database is empty. Admin commands subsequently
modify SQLite, not the JSON file. To intentionally re-bootstrap access control,
stop the bot and remove or relocate the SQLite database before restarting.

### Rate limiting and queueing

| Variable | Default | Description |
| --- | --- | --- |
| `RATE_LIMIT_PER_USER` | `2` | Concurrent requests per user. |
| `RATE_LIMIT_COOLDOWN` | `30` | Cooldown after a per-user concurrency rejection, in seconds. |
| `RATE_LIMIT_GLOBAL` | `6` | Global concurrent requests. |
| `RATE_LIMIT_FILE_SIZE` | `20` | Maximum accepted Telegram file size in MB. |
| `RATE_LIMIT_QUEUE_ENABLED` | `1` | Queue requests when global capacity is full. |
| `RATE_LIMIT_QUEUE_SIZE` | `10` | Maximum global queue size. |
| `RATE_LIMIT_QUEUE_PER_USER` | `1` | Maximum queued requests per user. |

Concurrency limits, file size, and per-user queue capacity must be at least
`1`. Cooldowns and the global queue size may be `0`. Invalid values stop
startup and report the exact environment variable.

### Provider resilience

| Variable | Default | Description |
| --- | --- | --- |
| `PROVIDER_RESILIENCE_ENABLED` | `1` | Enable the provider circuit breaker. |
| `PROVIDER_RESILIENCE_THRESHOLD` | `3` | Consecutive failures before opening the circuit. |
| `PROVIDER_RESILIENCE_COOLDOWN` | `60` | Open-circuit cooldown in seconds. |

The threshold must be at least `1`; the cooldown may be `0`.
Boolean settings accept `1`, `0`, `true`, `false`, `yes`, or `no`
case-insensitively.

### Telegram progressive output

`TELEGRAM_DRAFT_STREAMING=0` is the default and recommended initial setting.

When enabled, live refinement deltas can be shown through Telegram drafts when:

- the chat is private;
- the installed Telegram SDK exposes draft support;
- the runtime call succeeds.

The durable final answer is still sent as a normal Telegram message. Group
chats and unsupported runtimes use the normal edit/send flow. Long final
answers are split into chunks of at most 4,000 characters.

Provider streaming errors are reported through the normal pipeline error
handling; they do not currently retry through the non-streaming refinement
method.

### Logging privacy

`LOG_SENSITIVE_TEXT=0` hides transcript and refined-text contents from logs.
Only enable it temporarily during controlled debugging because user content may
be sensitive.

## Access control

The bootstrap file must contain arrays named `admin`, `users`, and `groups`:

```json
{
  "admin": [123456789],
  "users": [987654321],
  "groups": [-100123456789]
}
```

- Admins can use the bot and manage users and groups.
- Authorized users can use the bot in direct chats.
- An authorized group allows its members to use the bot in that chat.

Use `/whoami` to display the current Telegram user and chat IDs.

## Commands

| Command | Access | Description |
| --- | --- | --- |
| `/start` | Everyone | Welcome message. |
| `/whoami` | Everyone | Show user and chat IDs. |
| `/help` | Everyone | Show command help. |
| `/adduser <id>` | Admin | Authorize a user. |
| `/removeuser <id>` | Admin | Remove a user. |
| `/addgroup <id>` | Admin | Authorize a group. |
| `/removegroup <id>` | Admin | Remove a group. |

Audio processing itself remains restricted to authorized users, admins, and
authorized groups.

## Tests

Install the dependencies first, then run:

```bash
python -m pytest tests
```

Run one test with:

```bash
python -m pytest tests/test_config.py::test_config_loads_defaults_and_normalizes_ids
```

Using `python -m pytest` avoids import-path differences between pytest
installations.

GitHub Actions compiles the Python sources, smoke-tests package imports, and
runs the complete suite on Python 3.10, 3.11, and 3.12 without requiring
Telegram or provider credentials.

## Troubleshooting

- `FFmpeg is not installed`: install FFmpeg and confirm `ffmpeg -version`.
- `TELEGRAM_TOKEN is required`: create `.env` and set a valid token.
- `authorized.json not found`: create the bootstrap file before startup.
- Telegram `409 Conflict`: another process is polling with the same bot token.
- `File troppo grande`: the file exceeds `RATE_LIMIT_FILE_SIZE`.
- `Richiesta accodata`: all global slots are active and the request is waiting.
- Provider temporarily unavailable: the circuit breaker is open; retry after
  its cooldown.
- Admin changes appear to ignore JSON edits: SQLite is the live source after
  bootstrap; see the persistence section above.

## Project structure

```text
.
├── bot/
│   ├── core/             # Telegram application construction
│   ├── decorators/       # Authorization, rate limiting, and timeouts
│   ├── handlers/         # Command, admin, and audio handlers
│   ├── ui/               # Progress and delivery adapters
│   ├── auth_store.py     # SQLite whitelist persistence
│   ├── config.py         # Configuration loading and validation
│   ├── constants.py      # Messages, defaults, and timeouts
│   ├── main.py           # Application entry point
│   ├── providers.py      # OpenAI/Gemini providers and resilience
│   ├── rate_limiter.py   # Admission control and queueing
│   └── utils.py          # FFmpeg and provider helpers
├── tests/                # Automated pytest suite
├── .env.example          # Public configuration template
├── authorized.json       # Local bootstrap ACL; never committed
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

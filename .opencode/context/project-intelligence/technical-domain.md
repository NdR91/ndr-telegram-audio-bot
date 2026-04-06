<!-- Context: project-intelligence/technical | Priority: high | Version: 2.0 | Updated: 2026-04-06 -->

# Technical Domain

> Technical overview of the Telegram audio bot, its runtime model, and the architectural choices that matter in day-to-day maintenance.

## Quick Reference

- **Primary language**: Python
- **Primary runtime**: `python-telegram-bot` polling bot
- **Core external tools**: FFmpeg, OpenAI API, Google Gemini API, Docker Compose
- **Current architecture**: Single-process bot with provider abstraction and app-scoped services

## Primary Stack

| Layer | Technology | Notes |
|-------|------------|-------|
| Language | Python 3 | Main implementation language |
| Bot framework | `python-telegram-bot[job-queue]~=22.7` | Async Telegram handlers, polling, scheduled jobs |
| Audio conversion | FFmpeg | Converts Telegram audio to MP3 when needed |
| LLM providers | OpenAI, Google Gemini | Multi-provider transcription + refinement |
| Persistence | SQLite + bootstrap JSON | Runtime whitelist in SQLite, bootstrap from `authorized.json` |
| Container runtime | Docker / Docker Compose | Recommended deployment path |
| Tests | `pytest`, `pytest-asyncio` | Focused regression coverage for core logic |

## Architecture Pattern

```text
Type: Single-service bot
Pattern: Async pipeline with provider abstraction + delivery adapter
```

### Why this architecture?

- The project only needs one runtime service: receive Telegram messages, process audio, and return text.
- A provider abstraction keeps OpenAI and Gemini behind the same high-level interface.
- A Telegram delivery adapter isolates messaging UX from the audio/refine pipeline.
- App-scoped dependencies in `app.bot_data` avoid hidden module-level globals.

## Runtime Flow

```text
Telegram update
  → auth/rate-limit checks
  → download file
  → convert with FFmpeg
  → transcribe audio
  → refine text
  → deliver final output to Telegram
```

### Current delivery behavior
- Classic progress messages for technical stages
- Optional Telegram progressive delivery via drafts
- True provider-level refine streaming supported for OpenAI and Gemini
- Safe fallback to classic final-message delivery remains available

## Project Structure

```text
bot/
├── core/         # App creation and wiring
├── decorators/   # Auth, rate limiting, timeouts
├── handlers/     # Audio flow, commands, admin commands
├── ui/           # Progress UI and Telegram delivery adapter
├── auth_store.py # SQLite whitelist persistence
├── config.py     # Environment loading and validation
├── constants.py  # Messages, defaults, prompts
├── providers.py  # Provider abstraction + implementations
├── rate_limiter.py
└── utils.py      # FFmpeg and provider factory helpers
```

## Key Technical Decisions

| Decision | Rationale | Impact |
|----------|-----------|--------|
| Provider abstraction | Avoid vendor lock-in | OpenAI and Gemini share one orchestration path |
| SQLite whitelist persistence | JSON is weak as mutable runtime state | More robust auth persistence and Docker compatibility |
| Delivery adapter | Telegram UX should be isolated from provider logic | Progressive delivery can evolve without rewriting handlers |
| Typed pipeline exceptions | Avoid brittle string parsing | Safer error handling and cleaner stage boundaries |
| Queue + circuit breaker | Improve resilience under load/provider incidents | Better UX and safer external API usage |

## Integration Points

| System | Purpose | Direction |
|--------|---------|-----------|
| Telegram Bot API | Receive commands/audio, send progress/output | Inbound + outbound |
| OpenAI API | Transcription and refine support | Outbound |
| Gemini API | Transcription and refine support | Outbound |
| FFmpeg | Audio conversion | Local process |
| SQLite | Whitelist persistence | Local file |

## Technical Constraints

- Telegram message size limits still matter for final delivery behavior
- Draft streaming works best in private chats and under specific Telegram constraints
- Audio comes from Telegram as completed files, not live audio streams
- True transcription streaming is intentionally out of scope today
- Docker deployment expects `authorized.json` as bootstrap input and writable `audio_files/`

## Development Environment

- Local env: `python3 -m venv venv && source venv/bin/activate`
- Install: `pip install -r requirements.txt`
- Run: `python bot/main.py`
- Test: `pytest tests`

## Deployment

- Recommended runtime: Docker Compose
- Main command: `docker compose up -d --build`
- Logs: `docker compose logs -f`
- Container posture: non-root, read-only bootstrap auth file, hardened build context

## Related Files

- `business-domain.md` - Why this bot exists and who uses it
- `business-tech-bridge.md` - How technical choices map to user value
- `decisions-log.md` - Persistent architectural decisions

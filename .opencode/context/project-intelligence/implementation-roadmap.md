<!-- Context: project-intelligence/implementation-roadmap | Priority: high | Version: 2.2 | Updated: 2026-04-05 -->

# Implementation Roadmap

> Persistent delivery roadmap for this repository. Update statuses as work lands so progress survives across sessions.

## Quick Reference

- **Purpose**: Track planned, in-progress, and completed fixes across sessions
- **Update When**: A task is started, completed, deferred, or reprioritized
- **Status Values**: Planned | In Progress | Done | Deferred

## Current Priorities

| ID | Priority | Item | Status | Notes |
|----|----------|------|--------|-------|
| P0-1 | P0 | Fix provider default model fallback | Done | `create_provider()` now applies provider defaults when `LLM_MODEL` is unset |
| P0-2 | P0 | Add locking for whitelist updates | Done | Whitelist mutations now run under a shared async lock |
| P0-3 | P0 | Make `authorized.json` writes atomic | Done | Saves now use temp-file + `os.replace()` |
| P0-4 | P0 | Remove `authorized.json` from Docker image build | Done | Docker build no longer copies auth data; README clarifies runtime mount |
| P1-1 | P1 | Remove `._*` AppleDouble artifacts | Done | Cleaned workspace artifacts in root, `bot/`, and `.opencode/` |
| P1-2 | P1 | Harden logging/privacy for transcript text | Done | Transcript/refined text is hidden by default unless explicitly enabled |
| P1-3 | P1 | Add minimal automated test suite | Done | Added pytest coverage for config, limiter, admin, and utils |
| P1-4 | P1 | Investigate/plan Telegram progressive output adoption | Done | Research completed: PTB 20.x lacks support; target PTB 22.7+ with adapter + fallback |
| P2-1 | P2 | Replace fragile string-based error mapping | Done | Audio pipeline now uses typed stage/timeout exceptions |
| P2-2 | P2 | Reduce module-level global state | Done | Audio processor, limiter, and whitelist manager now live in app bot_data |
| P2-3 | P2 | Improve Docker/runtime hardening | Done | Added non-root container, .dockerignore, read-only auth mount, and runtime docs |
| P2-4 | P2 | Add operational observability | Done | Added stage timings, pipeline summaries, and provider failure metadata logs |
| P3-1 | P3 | Evaluate queue beyond global concurrency limit | Done | Added bounded FIFO queue with per-user queue caps and config toggles |
| P3-2 | P3 | Evaluate stronger whitelist persistence | Deferred | Consider SQLite if complexity grows |
| P3-3 | P3 | Add provider resilience/circuit breaker | Deferred | Future reliability enhancement |

## Work Packages

### P0 — Safety and Correctness
- **P0-1** Fix provider creation so provider defaults are actually used when `LLM_MODEL` is unset
- **P0-2** Serialize admin whitelist mutations with a lock
- **P0-3** Save whitelist changes through temp-file + atomic rename
- **P0-4** Remove `COPY authorized.json .` from `Dockerfile`

### P1 — Stability and Verification
- **P1-1** Delete AppleDouble files and prevent reintroduction
- **P1-2** Review `LOG_SENSITIVE_TEXT` behavior and transcript preview logging
- **P1-3** Add `pytest` coverage for critical local logic
- **P1-4** Plan migration path to Telegram `sendMessageDraft` with fallback to edits

### P2 — Maintainability and Operations
- **P2-1** Introduce typed exceptions for pipeline stages
- **P2-2** Move processor/limiter/whitelist state away from globals where practical
- **P2-3** Harden container/runtime configuration and docs
- **P2-4** Add simple metrics/log structure for pipeline stages

## Telegram Progressive Output Notes

- **Verified**: Telegram Bot API now supports progressive text output through `sendMessageDraft`
- **Changelog milestones**:
  - Bot API 9.3 (2025-12-31): introduced partial streamed message support
  - Bot API 9.5 (2026-03-01): allowed all bots to use `sendMessageDraft`
- **Repository implication**: current `python-telegram-bot~=20.0` is too old for first-class wrapper support
- **Recommended rollout**:
  1. Upgrade PTB to **22.7+** after compatibility review
  2. Add a small adapter around progressive delivery (`send_message_draft` when available)
  3. Keep fallback to current `edit_message_text` flow
  4. Guard rollout with feature flag (e.g. `TELEGRAM_DRAFT_STREAMING=1`)

## P1-4 Research Outcome

- **Official Bot API status**: `sendMessageDraft` is official and available to all bots since Bot API 9.5
- **PTB compatibility**:
  - PTB 20.x: no direct `send_message_draft()` support
  - PTB 22.7: full Bot API 9.5 support, including `send_message_draft()`
- **Current repo constraint**: providers return complete strings, not incremental tokens, so the first implementation should focus on progressive message delivery around existing full-text outputs rather than true token streaming
- **Recommended implementation shape**:
  - create a delivery adapter boundary near `bot/handlers/audio.py` / `bot/ui/progress.py`
  - use native `send_message_draft()` when available
  - otherwise fallback to current progress edits and final send/edit flow
  - feature-flag the new behavior for safe rollout

## Update Rules

- When a task starts: mark `In Progress`
- When finished: mark `Done` and add a short note
- When scope changes: update priority or note rationale in `decisions-log.md`

## Related Files

- `living-notes.md` - Current risks and active focus
- `decisions-log.md` - Why roadmap direction or priorities changed
- `technical-domain.md` - Update when roadmap items materially change architecture

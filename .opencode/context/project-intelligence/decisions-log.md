<!-- Context: project-intelligence/decisions | Priority: high | Version: 1.2 | Updated: 2026-04-05 -->

# Decisions Log

> Record major architectural and delivery decisions with enough context to survive multiple sessions.

## Quick Reference

- **Purpose**: Document decisions so future sessions understand why priorities exist
- **Status**: Decided | Pending | Under Review | Deprecated
- **Roadmap Companion**: `implementation-roadmap.md`

## Decision: Persist roadmap in project intelligence

**Date**: 2026-04-05
**Status**: Decided
**Owner**: Maintainer

### Context
The repository is maintained across multiple agent sessions. Roadmap and status were previously conversational only, making priorities easy to lose between sessions.

### Decision
Use `implementation-roadmap.md` as the canonical persistent roadmap, with `living-notes.md` storing current risks and state.

### Rationale
Project-intelligence files are explicitly designed for durable context. A dedicated roadmap file keeps delivery tracking separate from general notes while remaining easy to load.

### Alternatives Considered
| Alternative | Pros | Cons | Why Rejected? |
|-------------|------|------|---------------|
| Keep roadmap only in chat history | Zero file maintenance | Context lost across sessions | Not durable enough |
| Store roadmap in `.tmp/` files | Good for active execution | Temporary by design | Not persistent |
| Put everything only in `living-notes.md` | Fewer files | Harder to scan delivery status | Less clear |

### Impact
- **Positive**: Durable planning memory and easier handoff across sessions
- **Negative**: Requires explicit maintenance as work progresses
- **Risk**: Roadmap may drift if updates are skipped

## Decision: Evaluate Telegram progressive output after core hardening

**Date**: 2026-04-05
**Status**: Under Review
**Owner**: Maintainer

### Context
Telegram Bot API now supports progressive text output through `sendMessageDraft`, which is relevant for LLM-style UX. The repository also has unresolved P0 safety issues.

### Decision
Treat `sendMessageDraft` adoption as planned work, but sequence it after current P0 correctness and persistence fixes.

### Rationale
The feature is valuable, but adding new UX behavior before fixing concurrency and persistence issues would increase complexity on an unstable base.

### Alternatives Considered
| Alternative | Pros | Cons | Why Rejected? |
|-------------|------|------|---------------|
| Implement `sendMessageDraft` immediately | Faster UX improvement | Adds complexity before hardening | Too risky now |
| Ignore the new Telegram capability | Zero migration effort | Misses meaningful UX improvement | Too conservative |

### Impact
- **Positive**: Keeps priority order clear while preserving the enhancement path
- **Negative**: Streaming UX improvement is delayed
- **Risk**: PTB upgrade may introduce separate breaking-change work

## Decision: Adopt Telegram draft streaming only through an adapter and PTB upgrade path

**Date**: 2026-04-05
**Status**: Decided
**Owner**: Maintainer

### Context
Research confirmed that `sendMessageDraft` is officially available in Telegram Bot API 9.5, but the repository is pinned to `python-telegram-bot~=20.0`, which does not expose `send_message_draft()`.

### Decision
Treat draft streaming as a future implementation project that should:
1. target **PTB 22.7+**,
2. use a small delivery adapter,
3. preserve fallback to current `edit_message_text` behavior,
4. ship behind a feature flag.

### Rationale
This keeps the repository compatible today while defining a clean path to adopt the newer Telegram capability without entangling the audio pipeline directly with SDK/version conditionals.

### Alternatives Considered
| Alternative | Pros | Cons | Why Rejected? |
|-------------|------|------|---------------|
| Direct raw HTTP call on PTB 20.x only | Faster initial experiment | Bypasses SDK ergonomics and increases maintenance risk | Weaker long-term path |
| Immediate hard switch to PTB 22.7+ with no fallback | Clean modern API | Higher migration risk | Too abrupt |
| Ignore `sendMessageDraft` entirely | No migration cost | Misses better UX | Too conservative |

### Impact
- **Positive**: Clear migration target; safer rollout; explicit fallback behavior
- **Negative**: Requires a separate PTB upgrade effort before implementation
- **Risk**: PTB 20→22 upgrade may touch more than message delivery code

## Related Files

- `implementation-roadmap.md`
- `living-notes.md`
- `.tmp/external-context/telegram-bot-api/send-message-draft-status.md`
- `.tmp/external-context/python-telegram-bot/ptb-send-message-draft-compatibility.md`

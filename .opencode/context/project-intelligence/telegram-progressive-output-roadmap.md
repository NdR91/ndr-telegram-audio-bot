<!-- Context: project-intelligence/telegram-progressive-output-roadmap | Priority: high | Version: 1.6 | Updated: 2026-04-05 -->

# Telegram Progressive Output Roadmap

> Persistent implementation plan for Telegram progressive output / draft streaming in this repository.

## Quick Reference

- **Purpose**: Track the implementation plan for `sendMessageDraft` adoption across sessions
- **Status Values**: Planned | In Progress | Done | Deferred
- **Primary Constraint**: Current repo uses `python-telegram-bot~=20.x`; target path is PTB 22.7+

## Goal

Deliver a user experience where the final response appears progressively in Telegram, using native `sendMessageDraft` when available and falling back safely to the current edit-based flow.

## Current Architecture Constraints

- Telegram Bot API supports `sendMessageDraft`
- PTB 20.x does not expose `send_message_draft()`
- PTB 22.7+ is the recommended SDK target
- Current providers return full text, not streaming tokens
- First milestone should stream the **final generated text progressively**, not true token-by-token provider output

## Work Items

| ID | Priority | Item | Status | Notes |
|----|----------|------|--------|-------|
| TG-1 | P1 | Upgrade PTB to 22.7+ | Done | Upgraded to `python-telegram-bot[job-queue]~=22.7` after compatibility review |
| TG-2 | P1 | Add delivery adapter for progressive output | Done | Added application-scoped delivery adapter with draft capability checks and current fallback delivery |
| TG-3 | P1 | Add feature flag for progressive delivery | Done | Added `TELEGRAM_DRAFT_STREAMING`, default off |
| TG-4 | P1 | Implement progressive delivery for final text | Done | Added cumulative draft streaming for private chats with safe fallback |
| TG-5 | P2 | Integrate adapter into audio pipeline output step | Done | Confirmed final-output-only integration with existing progress UX preserved |
| TG-6 | P2 | Add tests for adapter, fallback, and chunking | Done | Added realistic tests for long-text fallback and multi-update draft flows |
| TG-7 | P2 | Update README/changelog/operator docs | Done | Documented constraints, fallback behavior, and rollout guidance |
| TG-8 | P3 | Explore true incremental provider streaming later | Deferred | Separate from first rollout |

## Implementation Phases

### Phase 1 — SDK and Delivery Boundary
- **TG-1** Upgrade `python-telegram-bot` to 22.7+
- **TG-2** Introduce a dedicated delivery adapter (for example `bot/ui/streaming.py`)
- **TG-3** Add env flag such as `TELEGRAM_DRAFT_STREAMING=1`

## TG-1 Outcome

- PTB upgraded to `22.7+`
- `job-queue` extra is now bundled in `requirements.txt`
- Current code already uses the async `ApplicationBuilder` path, so no source-level compatibility changes were immediately required
- This clears the dependency prerequisite for `send_message_draft()` work

## TG-2 Outcome

- Added `bot/ui/streaming.py` as the dedicated Telegram delivery boundary
- The adapter is injected through `app.bot_data`
- Current final-response behavior now flows through the adapter
- Native draft support is detected centrally via `supports_native_drafts()`
- Actual draft-based progressive delivery remains for TG-3/TG-4

## TG-3 Outcome

- Added `TELEGRAM_DRAFT_STREAMING` as the rollout kill switch
- Default remains off for safer production rollout
- Draft capability detection now requires both feature flag enabled and bot support present

## TG-4 Outcome

- Final response delivery can now stream cumulative draft updates
- Streaming is used only when all safety conditions are met:
  - feature flag on
  - private chat
  - native draft support available
  - response fits a single message
- All other cases continue to use the existing edit/send fallback path

## TG-5/TG-6 Outcome

- Confirmed the adapter changes only the final output step, not the technical stage-progress UX
- Long responses still use the classic split/send fallback path
- Added stronger tests for:
  - long-message fallback
  - cumulative multi-update drafts
  - final ack replacement behavior

## TG-7 Outcome

- Documented what the current streaming implementation actually does
- Clarified that current UX is progressive final-text delivery, not provider token streaming
- Added rollout guidance and explicit fallback expectations for operators

### Phase 2 — First Useful UX
- **TG-4** Deliver final response progressively in chunks
- Use `sendMessageDraft` when available
- Fallback to `edit_message_text` when draft support is unavailable or fails

### Phase 3 — Pipeline Integration
- **TG-5** Integrate only at the final output stage in `bot/handlers/audio.py`
- Keep existing progress messages for download/convert/transcribe/refine
- Switch to progressive output only when final text is ready

### Phase 4 — Validation and Rollout
- **TG-6** Add tests
- **TG-7** Update operator docs
- Roll out with the feature flag off first, then enable after validation

## Recommended First Milestone

The first implementation should **not** attempt provider-level token streaming.

Instead:
- generate the final text as today
- reveal it progressively in Telegram chunks
- use native draft messaging where available

Why this is preferred:
- lower risk
- no provider protocol changes
- no coupling to model-specific token streaming APIs
- still produces a much better perceived UX

## Risks

- PTB 20.x → 22.7+ upgrade may require handler/runtime adjustments
- Telegram rate limits if updates are too frequent
- Long message splitting must remain correct during progressive output
- Draft API support may differ between wrapper and raw API behavior

## Done Criteria

- PTB upgraded successfully
- Progressive delivery isolated behind an adapter
- Feature flag available
- `sendMessageDraft` used when supported
- Fallback path always works
- Tests pass
- README updated

## Related Files

- `implementation-roadmap.md` - Completed hardening roadmap and prior streaming research note
- `living-notes.md` - Active open questions and migration risks
- `decisions-log.md` - Why adapter + PTB upgrade was chosen

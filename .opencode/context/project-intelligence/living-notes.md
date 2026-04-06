<!-- Context: project-intelligence/notes | Priority: high | Version: 2.7 | Updated: 2026-04-05 -->

# Living Notes

> Current risks, technical debt, and active focus areas for the Telegram audio bot.

## Quick Reference

- **Purpose**: Capture current state, problems, and open questions
- **Update**: When roadmap status changes or new risks are discovered
- **Delivery Plan**: See `implementation-roadmap.md` and `telegram-progressive-output-roadmap.md`

## Current State

- Bot is in active daily use and considered functionally useful
- No automated test suite is present yet
- Main technical risk area is the async audio pipeline plus shared mutable state
- Telegram progressive output is now a realistic enhancement path via `sendMessageDraft`

## Technical Debt

| Item | Impact | Priority | Mitigation |
|------|--------|----------|------------|

## Open Questions

| Question | Stakeholders | Status | Next Action |
|----------|--------------|--------|-------------|
| How should progressive output coexist with the current progress-message UX? | Maintainer | Open | Design TG-2 adapter boundary before implementation |

## Known Issues

| Issue | Severity | Workaround | Status |
|-------|----------|------------|--------|

## Insights & Lessons Learned

### What Works Well
- Modular split across config, providers, handlers, and UI keeps the codebase navigable
- Cleanup of local audio temp files in `finally` blocks is a strong operational safeguard

### What Could Be Better
- Global state and missing tests increase risk as concurrency features expand

### Lessons Learned
- Enabling concurrency requires explicit persistence safety around every shared mutable resource
- Useful small bots still need persistent project memory, otherwise priorities get lost across sessions

## Active Projects

| Project | Goal | Owner | Timeline |
|---------|------|-------|----------|
| Core hardening | Close P0 safety/correctness gaps | Maintainer + agents | Next working sessions |
| Progressive Telegram UX | Implement `sendMessageDraft` path with adapter and fallback | Maintainer + agents | Next feature project |

## Archive (Resolved Items)

### Resolved: Provider default model fallback
- **Resolved**: 2026-04-05
- **Resolution**: `bot/utils.py` now applies explicit provider defaults when `LLM_MODEL` is absent, preventing `None` from reaching OpenAI/Gemini model configuration.
- **Learnings**: Optional config values should not silently bypass constructor defaults when factories pass arguments through.

### Resolved: Docker image copied `authorized.json`
- **Resolved**: 2026-04-05
- **Resolution**: `Dockerfile` no longer copies `authorized.json`; runtime mounting remains the supported deployment model and README now states that explicitly.
- **Learnings**: Operational auth/config data should stay outside the image and be injected only at runtime.

### Resolved: Whitelist concurrency and persistence safety
- **Resolved**: 2026-04-05
- **Resolution**: `bot/handlers/admin.py` now serializes whitelist mutations with an async lock and writes `authorized.json` atomically through a temporary file plus `os.replace()`.
- **Learnings**: Shared mutable state plus concurrent update handling requires explicit mutation serialization and durable file-write semantics.

### Resolved: AppleDouble repository artifacts
- **Resolved**: 2026-04-05
- **Resolution**: Removed `._*` / `.__*` AppleDouble artifacts from the workspace in root, `bot/`, and `.opencode/`, while preserving the real project files.
- **Learnings**: macOS metadata noise can pollute both code and context trees; cleanup is safe when targeted strictly at AppleDouble shadow files.

### Resolved: Transcript logging privacy hardening
- **Resolved**: 2026-04-05
- **Resolution**: `bot/providers.py` now hides transcript/refined text content by default and logs only metadata unless `LOG_SENSITIVE_TEXT=1` is explicitly enabled; startup warns when sensitive logging is active.
- **Learnings**: For user-generated content, debug-friendly previews are still a privacy leak; safe defaults should hide content entirely and require explicit operator opt-in.

### Resolved: Minimal automated test suite
- **Resolved**: 2026-04-05
- **Resolution**: Added initial `pytest` coverage for configuration loading, rate limiting behavior, whitelist persistence logic, and provider factory defaults, plus documented suite usage.
- **Learnings**: Even a small deterministic test suite gives immediate protection around the highest-risk local logic and makes future refactors safer.

### Resolved: Telegram progressive output investigation
- **Resolved**: 2026-04-05
- **Resolution**: Confirmed `sendMessageDraft` is officially available to all bots, but the current `python-telegram-bot~=20.0` dependency does not support it directly. Recommended path is PTB 22.7+ plus a small adapter with fallback to current message-edit behavior.
- **Learnings**: Bot API support and SDK support move at different speeds; progressive-output adoption should be treated as a dependency-upgrade project, not a one-line feature toggle.

### Resolved: Typed audio pipeline exceptions
- **Resolved**: 2026-04-05
- **Resolution**: Replaced string-based timeout/error classification in the audio pipeline with typed exceptions carrying user-facing messages, improving handler reliability.
- **Learnings**: Error-message parsing is brittle glue; explicit exception types make stage boundaries clearer and safer to evolve.

### Resolved: Application-scoped service dependencies
- **Resolved**: 2026-04-05
- **Resolution**: Moved audio processor, rate limiter, and whitelist manager from module-level globals into `app.bot_data`, reducing hidden shared state and making dependency access explicit via context.
- **Learnings**: PTB already provides an application-scoped dependency container; using it is cleaner and safer than ad-hoc globals.

### Resolved: Docker/runtime hardening
- **Resolved**: 2026-04-05
- **Resolution**: Added `.dockerignore`, switched the image to a non-root runtime user, mounted `authorized.json` read-only in Compose, and documented the hardened runtime expectations.
- **Learnings**: Even simple single-service bots benefit from least-privilege defaults and a clean build context; these are low-cost safety wins.

### Resolved: Operational observability
- **Resolved**: 2026-04-05
- **Resolution**: Added concise stage timing logs, pipeline completion summaries, and provider/stage failure metadata so production diagnosis is easier without exposing sensitive user text.
- **Learnings**: Small, structured timing logs provide high operational value without needing a full metrics stack.

### Resolved: Global concurrency queue
- **Resolved**: 2026-04-05
- **Resolution**: Added a bounded FIFO queue for requests that arrive while all global processing slots are busy, with per-user queue caps and config toggles.
- **Learnings**: Queueing improves UX under load, but it needs explicit size limits and handoff rules to avoid turning overload into unbounded latency.

### Resolved: SQLite whitelist persistence
- **Resolved**: 2026-04-05
- **Resolution**: Migrated mutable whitelist persistence from `authorized.json` to SQLite, keeping the JSON file only as bootstrap input and storing runtime changes in `AUTHORIZED_DB`.
- **Learnings**: Read-only bootstrap config plus writable runtime state is a cleaner ops model than trying to mutate a mounted JSON file in place.

### Resolved: Provider circuit breaker
- **Resolved**: 2026-04-05
- **Resolution**: Added a configurable circuit-breaker wrapper around the provider layer so repeated failures open the circuit temporarily and fail fast with a safe user-facing message.
- **Learnings**: A small circuit breaker adds meaningful resilience without invasive retry logic, as long as thresholds and cooldowns are configurable.

## Related Files

- `implementation-roadmap.md` - Persistent roadmap and fix status
- `telegram-progressive-output-roadmap.md` - Dedicated roadmap for future Telegram streaming work
- `decisions-log.md` - Past decisions that inform current state
- `technical-domain.md` - Technical context for current state

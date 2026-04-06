<!-- Context: project-intelligence/bridge | Priority: high | Version: 2.0 | Updated: 2026-04-06 -->

# Business ↔ Tech Bridge

> How user value maps to the current technical design of the Telegram audio bot.

## Core Mapping

| Business Need | Technical Solution | Why This Mapping Matters | User/Project Value |
|---------------|-------------------|--------------------------|--------------------|
| Turn voice notes into text quickly | Telegram audio pipeline + provider abstraction | Users want text, not infrastructure details | Faster consumption and reuse of audio content |
| Improve readability of raw transcripts | Dedicated refine step with configurable prompts | Raw transcripts are often noisy | Cleaner, more usable output |
| Keep the bot reliable in daily use | Rate limiting, bounded queue, typed errors, circuit breaker | AI providers and Telegram flows are imperfect | Fewer broken experiences under load or outage |
| Avoid fragile auth/runtime behavior | SQLite whitelist persistence + bootstrap JSON | Mutable JSON files are weak runtime storage | Safer permissions management |
| Improve UX for long AI operations | Progress UI + Telegram delivery adapter + progressive delivery | Users need visible progress during multi-stage work | Better perceived responsiveness |
| Preserve future flexibility | Provider-agnostic interfaces and delivery abstraction | The project should not become vendor-locked | Easier future provider expansion |

## Important Feature Mappings

### Feature: Multi-Provider Support

**Business Context**
- Users and the maintainer should not be blocked by a single AI vendor
- Different providers may offer different trade-offs in quality, latency, or cost

**Technical Implementation**
- Shared `LLMProvider` abstraction
- OpenAI and Gemini implementations behind one orchestration path

**Connection**
- This allows the bot to evolve or switch providers without rewriting the whole application flow.

### Feature: Progressive Output UX

**Business Context**
- AI operations feel slow if the user sees nothing happening

**Technical Implementation**
- Progress messages for technical stages
- Telegram draft delivery and refine streaming for compatible cases

**Connection**
- Users perceive the bot as faster and more responsive even when upstream AI work still takes time.

### Feature: Runtime Hardening

**Business Context**
- A bot used daily must behave predictably under errors, retries, and concurrency

**Technical Implementation**
- Queue after global concurrency limit
- Provider circuit breaker
- Docker hardening
- Typed pipeline errors

**Connection**
- Reliability improvements directly translate into trust and lower maintenance effort.

## Key Trade-Offs

| Situation | Trade-Off | Decision | Why |
|-----------|-----------|----------|-----|
| Simplicity vs flexibility | One provider is easier, multiple providers are more future-proof | Keep multi-provider design | The project values provider independence |
| Fancy UX vs operational safety | Draft streaming is attractive but can be fragile | Use feature flag + fallback | Safer rollout and easier recovery |
| Minimal persistence vs robust auth state | JSON is simpler, SQLite is safer | Use SQLite runtime persistence | Better operational behavior without major complexity |
| Full realtime ambition vs pragmatic delivery | True transcription streaming is more complex | Do refine streaming first | Better ROI for effort |

## Why the Current Technical Direction Makes Sense

- The bot solves a real day-to-day need, so reliability is not optional
- The codebase is intentionally small enough to remain understandable
- Architectural abstractions are justified when they protect future provider choice or operational stability
- Streaming was introduced in stages so UX could improve without destabilizing the bot

## Related Files

- `business-domain.md` - Project purpose and user needs
- `technical-domain.md` - Technical implementation details
- `decisions-log.md` - Persistent rationale for the major trade-offs

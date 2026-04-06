<!-- Context: project-intelligence/refine-streaming-roadmap | Priority: high | Version: 1.5 | Updated: 2026-04-06 -->

# Refine Streaming Roadmap

> Persistent technical roadmap for implementing **true provider-level streaming of the refine step** while preserving the repository's multi-provider architecture.

## Quick Reference

- **Scope**: Stream only the **refine** phase, not audio transcription
- **Goal**: Keep Telegram delivery multi-provider and provider-agnostic
- **Status Values**: Planned | In Progress | Done | Deferred
- **Non-Goal**: Provider token streaming for audio transcription (`TG-8` remains separate)

## Executive Summary

The repository is now ready for delivery-layer streaming, but **not yet ready for true provider-level streaming** because `LLMProvider` exposes only full-result methods:

- `transcribe_audio(file_path) -> str`
- `refine_text(raw_text) -> str`

To support true refine streaming, the provider layer must evolve to expose a provider-agnostic streaming contract while preserving fallback compatibility for providers that do not support streaming natively.

## Current Assessment

### What the architecture already supports well
- Telegram delivery adapter exists and is app-scoped (`bot/ui/streaming.py`)
- Audio pipeline stages are explicit and isolated (`bot/handlers/audio.py`)
- Provider creation is centralized (`bot/utils.py`)
- Feature flags, fallbacks, tests, and resilience patterns are already in place

### What is currently missing
- No `stream_refine_text(...)` capability in `LLMProvider`
- No common event model for streaming output
- No orchestration path in `audio.py` for provider deltas
- No distinction between “provider supports streaming” vs “provider only supports final result”

## Provider Evaluation

### OpenAI
- **Current repo usage**: `whisper-1` + Chat Completions
- **Assessment**: Still supported, but somewhat legacy for the new goal
- **Best current path for refine streaming**: **Responses API with streaming**
- **Assessment for refine streaming**: **Best first provider to implement**

#### OpenAI Recommendation
- Keep transcription separate from this roadmap
- Migrate refine flow from Chat Completions to Responses API for the streaming path
- Treat current non-streaming refine path as fallback until migration is complete

### Gemini
- **Current repo usage**: Files API + `generate_content` batch flow
- **Assessment**: Modern enough for batch processing, but not optimized for true incremental refine streaming in current implementation
- **Best current path for refine streaming**: Gemini text streaming (`streamGenerateContent` / SDK streaming equivalent)
- **Assessment for refine streaming**: **Should remain in scope**, but can follow OpenAI implementation

#### Gemini Recommendation
- Keep current batch refine path as stable fallback
- Add a dedicated streaming refine implementation behind the same provider contract
- Do not redesign the audio transcription path just to unlock refine streaming

## Architectural Decision

Design refine streaming as a **provider capability**, not an OpenAI-specific feature.

### Bad direction
- `if provider == "openai"` logic scattered through handlers
- Telegram streaming behavior tightly coupled to one vendor API

### Good direction
- provider-agnostic interface
- explicit capability detection
- centralized Telegram delivery adapter
- graceful fallback for providers without streaming support

## Recommended Interface Evolution

### Step 1 — Keep existing contract
- `transcribe_audio(file_path) -> str`
- `refine_text(raw_text) -> str`

### Step 2 — Add streaming contract
Preferred shape:

```python
@dataclass
class RefineStreamEvent:
    type: str  # delta | done | error
    text: str
```

```python
class LLMProvider(ABC):
    async def stream_refine_text(self, raw_text: str) -> AsyncIterator[RefineStreamEvent]:
        ...
```

### Why this is preferred
- Allows provider-specific delta formats to be normalized
- Supports future metadata (finish reason, usage, safety interruption, etc.)
- Makes fallback providers easy to implement

## Capability Model

Add explicit capability signaling, for example:

```python
class LLMProvider(ABC):
    supports_refine_streaming: bool = False
```

or a method:

```python
def supports_refine_streaming(self) -> bool:
    return False
```

### Why this matters
- OpenAI can support true refine streaming first
- Gemini can be added later without changing the handler contract
- Future providers can degrade gracefully to non-streaming behavior

## Recommended Implementation Plan

| ID | Priority | Item | Status | Notes |
|----|----------|------|--------|-------|
| RS-1 | P1 | Add provider-agnostic refine streaming interface | Done | `LLMProvider` now exposes `stream_refine_text()` with compatibility fallback |
| RS-2 | P1 | Add `RefineStreamEvent` model and capability signaling | Done | Added normalized event model and `supports_refine_streaming` capability |
| RS-3 | P1 | Implement OpenAI refine streaming via Responses API | Done | Added normalized OpenAI Responses streaming implementation |
| RS-4 | P1 | Add audio-pipeline orchestration for streaming refine | Done | Audio pipeline now consumes provider refine stream events when supported |
| RS-5 | P1 | Extend Telegram delivery adapter for true provider deltas | Done | Added progressive-response session methods for delta-driven delivery |
| RS-6 | P2 | Add Gemini refine streaming implementation | Done | Added Gemini refine streaming under the same event contract |
| RS-7 | P2 | Expand tests for provider deltas, fallback, interruption, and finalize behavior | Done | Added edge-case tests for missing done, finalization, and circuit reset behavior |
| RS-8 | P2 | Add rollout docs and operator guidance | Done | Documented refine-streaming status and rollout expectations |
| RS-9 | P3 | Revisit transcription streaming separately | Deferred | Out of scope for this roadmap |

## Phase Plan

### Phase A — Contract and Orchestration
- **RS-1** Add streaming interface to providers
- **RS-2** Add event model + capability signaling
- **RS-4** Update `bot/handlers/audio.py` to choose between:
  - full refine path
  - streaming refine path

## RS-1 / RS-2 Outcome

- Added provider-agnostic `RefineStreamEvent`
- Added `supports_refine_streaming` capability signaling
- Added `stream_refine_text()` to `LLMProvider`
- Default fallback behavior now emits a single `delta` followed by `done`
- `ResilientProvider` now wraps streaming refine calls as well as full-result calls

### Phase B — First Real Streaming Provider
- **RS-3** Implement OpenAI refine streaming with Responses API
- **RS-5** Update Telegram delivery adapter to accept provider deltas

## RS-3 Outcome

- OpenAI now has a real refine streaming implementation via Responses API
- Streaming deltas are normalized into `RefineStreamEvent`
- Completion is emitted as a final `done` event
- Timeout and error conditions remain mapped to existing typed exceptions

## RS-4 / RS-5 Outcome

- `bot/handlers/audio.py` now chooses the refine streaming path when both provider and Telegram delivery support it
- `bot/ui/streaming.py` now supports progressive-response sessions for true delta consumption
- Overflow beyond Telegram single-message size stops draft updates while still allowing durable final-message fallback
- Existing non-streaming refine path remains the stable fallback

### Phase C — Multi-Provider Completion
- **RS-6** Implement Gemini refine streaming
- **RS-7** Expand test suite
- **RS-8** Update docs and rollout instructions

## RS-7 / RS-8 Outcome

- Added edge-case tests for:
  - missing `done` event fallback
  - non-draft finalization
  - draft-session long-text finalization
  - circuit reset after successful streaming
- Documented the current refine-streaming status for both providers
- Clarified rollout expectations and the relationship between provider streaming and Telegram-side gating

## RS-6 Outcome

- Gemini now exposes `stream_refine_text()` under the same `RefineStreamEvent` contract
- The handler/orchestrator remains provider-agnostic
- Both current providers can now participate in true refine streaming without OpenAI-specific branching in handlers

## Delivery Adapter Impact

Current adapter is good for:
- final-text progressive reveal

It is **not yet the final abstraction** for true refine streaming because it assumes full text is already available.

For RS work it should grow methods like:
- `start_progressive_response(...)`
- `push_progressive_delta(...)`
- `finalize_progressive_response(...)`

This should stay fully decoupled from provider specifics.

## Handler Impact

`bot/handlers/audio.py` should eventually do:

1. transcribe normally
2. if provider supports refine streaming and Telegram progressive delivery is enabled:
   - start streaming refine output
3. else:
   - use current full-result refine flow

### Important rule
The existing non-streaming path must remain the stable fallback.

## Risks

- Provider APIs expose different streaming semantics
- Telegram draft flow is temporary UI, not the final durable message
- Streaming interruption needs deterministic finalize/fallback behavior
- Responses API migration for OpenAI may change prompt structure slightly
- Gemini streaming path may differ significantly from the current batch API ergonomics

## Testing Strategy

### Critical tests
- provider emits streaming deltas in normalized format
- handler switches correctly between streaming and non-streaming paths
- Telegram adapter finalizes durable message correctly
- provider interruption mid-stream triggers safe fallback/finalization
- providers without streaming capability still work unchanged

### Additional tests
- long output fallback behavior
- empty delta / repeated delta handling
- circuit breaker interaction with streaming path

## Recommended Order of Work

1. **RS-1 / RS-2** — define interface and event model
2. **RS-3** — implement OpenAI refine streaming first
3. **RS-4 / RS-5** — wire handler + adapter to consume real deltas
4. **RS-7** — harden tests
5. **RS-6** — add Gemini streaming refine path
6. **RS-8** — finalize docs

## Recommendation

### Best practical path
- Start with **OpenAI refine streaming first**
- Keep Gemini in the architecture from day one
- Add Gemini streaming as the second provider implementation

### Why
- OpenAI is currently the clearest path for text refinement streaming
- This does **not** require becoming OpenAI-only
- It reduces initial complexity while preserving a clean multi-provider design

## Done Criteria

- Provider interface supports refine streaming
- OpenAI refine streaming works end-to-end
- Telegram delivery consumes real provider deltas
- Non-streaming fallback remains stable
- Gemini remains supported, with either fallback or native streaming path
- Tests pass
- Docs updated

## Related Files

- `telegram-progressive-output-roadmap.md` - Current delivery-layer streaming roadmap
- `living-notes.md` - Active streaming questions and risks
- `decisions-log.md` - Record provider-architecture decisions here

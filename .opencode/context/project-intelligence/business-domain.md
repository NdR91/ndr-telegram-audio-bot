<!-- Context: project-intelligence/business | Priority: high | Version: 2.0 | Updated: 2026-04-06 -->

# Business Domain

> Business and user context for the Telegram audio bot.

## Quick Reference

- **Project**: Telegram audio transcription and text-refinement bot
- **Primary value**: Turn messy voice notes into readable written text quickly
- **Current usage model**: Small-scale real daily usage by the maintainer and trusted users/groups

## Project Identity

```text
Project Name: Telegram Audio Transcriber Bot
Tagline: Turn Telegram audio into readable text with multi-provider AI and safe operational controls.
Problem Statement: Voice messages are useful, but they are harder to skim, search, quote, and reuse than text.
Solution: Accept Telegram audio, transcribe it, refine it, and send back readable text with strong operational safeguards.
```

## Target Users

| User Segment | Who They Are | What They Need | Pain Points |
|--------------|--------------|----------------|-------------|
| Primary | Individual Telegram users and small trusted groups | Fast voice-to-text conversion | Audio is slower to process mentally and harder to reuse |
| Secondary | Technically curious maintainers / agent-tool users | A useful real-world bot and a compact codebase to evolve | Need a project that is simple enough to manage but real enough to validate design choices |

## Value Proposition

### For Users
- Convert voice notes into readable text quickly
- Improve punctuation and readability without manual cleanup
- Keep using Telegram as the primary interaction surface

### For the Maintainer
- A genuinely useful daily bot
- A real but manageable codebase for evaluating agentic development workflows
- A practical sandbox for testing architecture, reliability, and UX decisions around AI tooling

## Current Product Focus

- Stable multi-provider audio transcription and refinement
- Safer runtime operation (queueing, circuit breaker, persistence, Docker hardening)
- Better UX through progressive Telegram delivery and refine streaming

## Long-Term Direction

- Expand provider capabilities without locking into one vendor
- Continue improving UX around long-running AI operations
- Potentially explore true transcription streaming later, but only if the complexity is justified by user value

## Business Constraints

- Small-scale project: simplicity still matters
- Runtime cost and operational complexity should stay proportional to actual usage
- The bot must remain reliable enough for daily use, not just as an experiment
- Privacy and safe handling of user-generated audio/text matter more than flashy features

## Success Signals

- Daily usage remains smooth and reliable
- Users receive readable text with minimal manual cleanup
- Operational incidents stay low even under provider issues or moderate concurrency
- New features do not compromise the simplicity of deployment and maintenance

## Related Files

- `technical-domain.md` - How the bot is built
- `business-tech-bridge.md` - Why specific technical choices matter to user value
- `living-notes.md` - Current status, risks, and open questions

# Product

## Promise

A self-hosted Telegram bot that turns authorized voice messages into faithful,
readable text. It adds punctuation, removes fillers, and makes minimal
corrections while preserving meaning, numbers, negations, and significant
repetitions.

## Audience

An operator runs one installation and configures it in a browser using their
own Telegram bot and AI-provider credentials. Telegram users do not receive
provider accounts or independent configuration profiles.

## Product shape

- Supported services: OpenAI, Gemini, and OpenRouter.
- One service and one immutable active revision at a time.
- Two audio paths: one multimodal generation, or transcription followed by
  refinement on the same service.
- A browser setup, an authenticated configuration workbench, contextual access
  management, and bounded diagnostics.
- Private users and authorized groups have separate access rules. New voice
  messages in an authorized group are processed automatically.
- Draft changes require an explicit successful audio test before activation.
- Audio and transcript text are temporary; the database stores configuration,
  access state, test proof metadata, and bounded operational metadata.

## UX direction

The approved direction is “Configurazione e prova”: edit a draft beside an
explicit test action, with the active revision visible as separate state. The
other operating areas are Panoramica, Accessi, and Diagnostica. Setup, login,
and local recovery are part of the operator journey.

## Product boundaries

The application does not summarize or translate by default, compose multiple
providers, select a fallback service, maintain per-chat profiles, or retain an
audio/transcript archive. Models in the catalog are candidates. The project
does not claim a model recommendation until an operator has measured quality,
latency, and available cost in their own environment.

## Release evidence

The V2 checkout has offline tests using synthetic data and mocked services.
Live Telegram/provider behavior and visual browser checks require the operator
and remain separate release gates. See [V2 validation](docs/V2-VALIDATION.md).

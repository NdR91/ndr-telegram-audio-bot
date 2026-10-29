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
- A saved catalog of complete profiles, with at most one global main profile
  and one optional global fallback profile. Each Telegram request keeps the
  role/revision snapshot it acquired when processing began.
- Two audio paths: one multimodal generation, or transcription followed by
  refinement on the same service.
- A browser setup, an authenticated configuration workbench, contextual access
  management, and bounded diagnostics.
- Private users and authorized groups have separate access rules. New voice
  messages in an authorized group are processed automatically.
- A main profile can be assigned without a successful test only after explicit
  confirmation and remains visibly unverified. A fallback is eligible only
  with a successful test for its current revision and credential.
- Fallback runs once only for typed timeout, network, 429, and 5xx provider
  failures. Empty or invalid output does not trigger it. A second provider may
  charge for its request even if the primary provider already billed; the app
  does not report a combined cross-provider cost total.
- Audio and transcript text are temporary; the database stores configuration,
  access state, test proof metadata, and bounded operational metadata.

## UX direction

The approved direction is “Configurazione e prova”: choose a saved profile,
edit its draft revision beside an explicit test action, and keep the main and
fallback roles and their verification state visible. The other operating areas
are Panoramica, Accessi, and Diagnostica. Setup, login, and local recovery are
part of the operator journey.

## Product boundaries

The application does not summarize or translate by default, compose arbitrary
multi-stage provider pipelines, maintain per-chat profiles, or retain an
audio/transcript archive. It supports one configured fallback profile under
the typed policy above. Models in the catalog are candidates. The project
does not claim a model recommendation until an operator has measured quality,
latency, and available cost in their own environment.

## Release evidence

The V2 checkout has offline tests using synthetic data and mocked services.
Live Telegram/provider behavior and visual browser checks require the operator
and remain separate release gates. See [V2 validation](docs/V2-VALIDATION.md).

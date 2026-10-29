---
name: Telegram Audio Bot Admin
description: Self-hosted admin UI for configuring Telegram audio transcription
colors:
  bg: "oklch(0.97 0.006 170)"
  surface: "oklch(0.995 0.003 170)"
  surface-raised: "oklch(0.95 0.012 170)"
  ink: "oklch(0.22 0.025 178)"
  muted: "oklch(0.46 0.025 225)"
  border: "oklch(0.87 0.018 170)"
  primary: "oklch(0.61 0.16 43)"
  accent: "oklch(0.37 0.08 205)"
  error: "oklch(0.60 0.22 25)"
  success: "oklch(0.35 0.105 155)"
  nav: "rgb(5, 32, 26)"
  nav-selected: "rgb(18, 50, 43)"
  nav-muted: "rgb(188, 203, 198)"
  nav-brand: "rgb(250, 248, 242)"
  step-text: "rgb(38, 52, 43)"
  step-border: "oklch(0.74 0.045 155)"
  draft-tag-text: "rgb(0, 66, 74)"
  draft-tag-bg: "rgb(209, 243, 246)"
  upload-border: "oklch(0.63 0.045 155)"
  input-border: "oklch(0.79 0.018 225)"
  modal-border: "oklch(0.78 0.006 80)"
  modal-ink: "rgb(22, 19, 18)"
  modal-muted: "rgb(71, 65, 64)"
  action-ink: "rgb(16, 7, 5)"
typography:
  ui: "Inter, system-ui, sans-serif"
  technical: "JetBrains Mono, Fira Code, Cascadia Code, monospace"
shape:
  control-radius: "6px"
  panel-radius: "10px"
---

# Design system: operator workbench

The interface is a server-rendered operator tool, not a general-purpose SaaS
dashboard. “Configurazione e prova” starts with saved profile cards, including
role and verification state, and opens the selected profile in the existing
editor and explicit test workbench. The other areas—Panoramica,
Accessi, and Diagnostica—focus on runtime state, contextual permissions, and
bounded operational evidence.

## Visual language

The shared stylesheet defines dark and light themes. The administration area
uses a dark green top navigation and a cool, green-tinted neutral workspace,
with orange primary actions and distinct colors for status. The editor is grouped
into service, key and model decisions, alongside the test and role actions.
The model catalog uses a searchable dialog with readable rows, explicit
filters and separate price units. Navigation and forms reflow on mobile.
Inter is the interface font; monospace is used
for technical values. These tokens are implemented in
`bot/web/static/style.css`; this document describes the code, not a completed
visual browser audit.

## Interaction and accessibility

Forms use visible labels and explicit status regions. Focus states use a visible
outline or ring, and motion-sensitive status/wizard animations have
`prefers-reduced-motion` rules. Long technical values and test output wrap
within their containers. Error and success states include text in addition to
color. The responsive stylesheet changes navigation and page layouts at small
viewports.

The previous synthetic Chromium review covered the earlier V2 screens, not the
new profile catalog, fallback confirmation, restore, and sample states. These
catalog changes have automated DOM/API-flow tests, but still need a real browser
pass at 1440×900 and 390×844 across setup, login, recovery, and the four
operating areas. Focus order, contrast,
overflow, long names, and each loading/error/success state have not yet been
visually confirmed. Track that work in [V2 validation](docs/V2-VALIDATION.md).

## Content rules

- Distinguish profile, saved revision, test state, main role, and fallback role.
- Explain that a fallback request may send audio to and incur charges from a
  second provider.
- Explain that tests send audio only after an explicit operator action.
- Never render provider error bodies or credentials as interface content.
- Keep private-user authorization distinct from group authorization.
- Show group membership and observed receipt as separate facts.
- Use literal text output; do not interpret transcript markup as HTML.
- Keep primary action hover text at WCAG AA contrast against the orange fill.
- Keep functional navigation text at or above 11px on narrow screens.

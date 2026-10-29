---
name: Telegram Audio Bot Admin
description: Self-hosted admin UI for configuring Telegram audio transcription
colors:
  bg: "oklch(0.16 0 0)"
  surface: "oklch(0.20 0 0)"
  surface-raised: "oklch(0.25 0 0)"
  ink: "oklch(0.96 0.004 30)"
  muted: "oklch(0.52 0.006 30)"
  border: "oklch(0.32 0 0)"
  primary: "oklch(0.68 0.17 38)"
  accent: "oklch(0.72 0.13 195)"
  error: "oklch(0.60 0.22 25)"
  success: "oklch(0.68 0.16 155)"
typography:
  ui: "Inter, system-ui, sans-serif"
  technical: "JetBrains Mono, Fira Code, Cascadia Code, monospace"
shape:
  control-radius: "6px"
  panel-radius: "10px"
---

# Design system: operator workbench

The interface is a server-rendered operator tool, not a general-purpose SaaS
dashboard. “Configurazione e prova” keeps the active revision visible beside
the editable draft and its explicit test action. The other areas—Panoramica,
Accessi, and Diagnostica—focus on runtime state, contextual permissions, and
bounded operational evidence.

## Visual language

The shared stylesheet defines dark and light themes. The administration area
uses a dark green top navigation and a warm neutral workspace, with orange
primary actions and distinct colors for status. The draft is grouped into
service, key and model decisions, alongside the test and activation actions.
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

The UI review still needs a real browser pass at 1440×900 and 390×844 across
setup, login, recovery, and the four operating areas. Focus order, contrast,
overflow, long names, and each loading/error/success state have not yet been
visually confirmed. Track that work in [V2 validation](docs/V2-VALIDATION.md).

## Content rules

- Distinguish the active configuration from a draft.
- Explain that tests upload audio only after an explicit operator action.
- Never render provider error bodies or credentials as interface content.
- Keep private-user authorization distinct from group authorization.
- Show group membership and observed receipt as separate facts.
- Use literal text output; do not interpret transcript markup as HTML.

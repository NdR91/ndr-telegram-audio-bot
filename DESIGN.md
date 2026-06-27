---
name: Telegram Audio Bot Admin
description: Self-hosted admin UI for a Telegram audio transcription bot
colors:
  bg: "oklch(0.11 0.000 0)"
  surface: "oklch(0.16 0.000 0)"
  surface-raised: "oklch(0.21 0.000 0)"
  ink: "oklch(0.96 0.004 30)"
  muted: "oklch(0.52 0.006 30)"
  border: "oklch(0.26 0.000 0)"
  primary: "oklch(0.68 0.17 38)"
  primary-deep: "oklch(0.58 0.18 35)"
  accent: "oklch(0.72 0.13 195)"
  error: "oklch(0.60 0.22 25)"
  success: "oklch(0.68 0.16 155)"
  warn: "oklch(0.72 0.13 60)"
typography:
  display:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "clamp(1.75rem, 3vw, 2.25rem)"
    fontWeight: 700
    lineHeight: 1.15
    letterSpacing: "-0.02em"
  headline:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  title:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 600
    lineHeight: 1.4
  body:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.6
  label:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "0.04em"
  mono:
    fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  xs: "3px"
  sm: "4px"
  md: "6px"
  lg: "10px"
  full: "9999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "40px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "10px 20px"
  button-primary-hover:
    backgroundColor: "{colors.primary-deep}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "10px 20px"
  button-secondary:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "10px 20px"
  card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "20px"
  input:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "10px 12px"
---

# Design System: Telegram Audio Bot Admin

## 1. Overview

**Creative North Star: "The Control Room"**

This is an admin interface for operators — people who self-hosted this bot because they wanted control, not convenience. Every screen should feel like a well-designed instrument panel: dense where density serves scanning, minimal where focus matters, and never wasting a pixel on decoration that doesn't carry operational information.

The surface is dark by design — not as a stylistic choice but as a consequence of use. An admin who checks bot health at 11pm on a dark screen needs fast legibility and low eyestrain. Amber-orange as the primary accent is a deliberate break from generic-SaaS blue: it reads as operational (traffic lights, cockpit readouts, terminal prompts), signals importance without aggression, and pairs well with the dark field.

Typography leans on Inter's precision and a monospace accent for technical values (API keys, model IDs, token counts). No serif sentimentality — this is a tool, not a publication.

**Key Characteristics:**
- Dark field with high-contrast ink — operational, not decorative
- Amber-orange primary accent: confident, distinctive, no blue-SaaS DNA
- Teal/cyan for status OK states — color-coded at a glance
- Monospace accents for technical values: IDs, keys, model names
- Gently curved corners (6px) — structured but not harsh
- Motion only for state transitions: no scroll reveals, no section entrances

## 2. Colors: The Control Room Palette

A dark-field palette anchored by amber-orange and teal, with pure neutral surfaces.

### Primary
- **Operational Amber** (oklch(0.68 0.17 38)): The primary action color. Used on primary buttons, active nav states, wizard progress dots, and form focus rings. Reads as operational — cockpit readout, not corporate branding. All text on Operational Amber uses ink (near-white).

### Secondary
- **Signal Teal** (oklch(0.72 0.13 195)): Status success indicators, active pipeline state badges, OK health dots, accent links. Distinct from amber in both hue and lightness; together they form a legible operational pair.

### Tertiary
- **Alert Amber** (oklch(0.72 0.13 60)): Warning states only. Close to primary in hue but lighter and less saturated — clearly contextual, not a competing brand color.

### Neutral
- **Void** (oklch(0.11 0.000 0)): Page background. Near-pure-black with no tint — the field that makes accent colors read as lit, not muddied.
- **Panel** (oklch(0.16 0.000 0)): Card and section backgrounds. Perceptibly lifted from Void.
- **Raised Surface** (oklch(0.21 0.000 0)): Form inputs, code blocks, secondary panels. One tier above Panel.
- **Rule** (oklch(0.26 0.000 0)): Borders and dividers. Subtle but present.
- **Ink** (oklch(0.96 0.004 30)): Primary text. Near-white with barely-perceptible warmth toward the amber hue.
- **Muted** (oklch(0.52 0.006 30)): Secondary text, placeholders, helper labels. Must meet 3.5:1 vs Void.
- **Error Red** (oklch(0.60 0.22 25)): Error states only. Close to the seed crimson — a fired-clay red, not cartoon emergency.
- **Status Green** (oklch(0.68 0.16 155)): Bot running, provider healthy, pipeline valid.

### Named Rules
**The One Amber Rule.** Operational Amber appears only on interactive elements that demand action (primary button, active nav item, focused input ring) and on progress/step indicators. It never fills a background panel or decorates a heading. Its scarcity is what makes it legible as a signal.

**The Semantic Color Rule.** Teal means healthy. Red means broken. Amber means warn or act. Never use these colors decoratively — if it's teal, something is running; if it's red, something failed.

## 3. Typography

**Display / Headline / Body Font:** Inter (system-ui, sans-serif fallback)
**Monospace Font:** JetBrains Mono (Fira Code → Cascadia Code fallback)

**Character:** Inter's tight metrics and optical precision make it feel engineered, not designed. Paired with a monospaced face for technical values, the system communicates accuracy without ever feeling editorial. No decorative contrast between the two faces — the mono is a functional switch, not a flourish.

### Hierarchy
- **Display** (700, clamp(1.75rem → 2.25rem), lh 1.15, ls -0.02em): Page titles. One per page. Never used for section headers.
- **Headline** (600, 1.25rem, lh 1.3, ls -0.01em): Section headers, card titles, wizard step names.
- **Title** (600, 1rem, lh 1.4): Form group labels, table column headers, status card labels.
- **Body** (400, 0.9375rem, lh 1.6): All prose, helper text, descriptions. Max line length 70ch.
- **Label** (600, 0.75rem, lh 1.2, ls 0.04em, uppercase): Status badges, table headers, metadata chips. Uppercase only in these two contexts — never as eyebrow decoration on regular content.
- **Mono** (400, 0.8125rem, lh 1.5): API keys (truncated), model IDs, pipeline role identifiers, code snippets.

### Named Rules
**The No-Eyebrow Rule.** Uppercase tracked labels are reserved for status badges and table column headers. Never above a section heading as a decorative kicker. If the content isn't a status or a data column, set it in Title weight, not an uppercase label.

## 4. Elevation

This system uses **tonal layering** exclusively — no decorative drop shadows. Depth is conveyed by surface lightness: Void → Panel → Raised Surface → (focus ring / outline). The only exception is focus rings, which use an amber glow (`box-shadow: 0 0 0 3px oklch(0.68 0.17 38 / 0.3)`) — this is a state signal, not decoration.

### Shadow Vocabulary
- **Focus Glow** (`box-shadow: 0 0 0 3px oklch(0.68 0.17 38 / 0.3)`): Keyboard/click focus on interactive elements.
- **Elevated Panel** (`box-shadow: 0 4px 16px oklch(0 0 0 / 0.5)`): Used only on floating panels (dropdowns, tooltips, dialogs) that must visually separate from the dark surface.

### Named Rules
**The Tonal-First Rule.** Before reaching for a drop shadow, ask whether a surface lightness step achieves the same hierarchy. On a dark field, shadows can disappear entirely — tonal steps never do.

## 5. Components

### Buttons
- **Shape:** Gently curved (6px radius). Not pill, not square.
- **Primary:** Operational Amber fill, ink text. 10px vertical / 20px horizontal padding. Semibold (600). Transition: background 150ms ease-out, transform 120ms ease-out.
- **Hover / Focus:** Shifts to Primary-Deep (oklch(0.58 0.18 35)), translateY(-1px). Focus adds amber glow ring.
- **Secondary:** Raised Surface fill, ink text, Rule border. Hover: Rule color shifts to Muted.
- **Ghost / Link:** No background, muted text. Hover: ink text. No underline in nav contexts.
- **Destructive:** Error Red fill, ink text. Same shape and padding as Primary.

### Status Indicators
The operational heartbeat of the UI. Each status indicator combines a 10px dot, a weight-600 label, and an optional description.
- **OK:** Status Green dot + text.
- **Warn:** Alert Amber dot + text.
- **Error / Stopped:** Error Red dot + text.
- Dots are circles (`border-radius: 50%`), never squares or icons.

### Cards / Surfaces
- **Corner Style:** 10px radius for cards, 6px for smaller surface sections.
- **Background:** Panel (oklch(0.16 0 0)).
- **Shadow Strategy:** None at rest — tonal layering only. Elevation via surface steps.
- **Border:** Rule color (oklch(0.26 0 0)) — present but subtle. Never colored.
- **Internal Padding:** 20px standard, 16px compact for dense layouts.

### Inputs / Fields
- **Style:** Raised Surface background, Rule border (1px), 6px radius.
- **Focus:** Border shifts to Primary (Operational Amber), focus glow ring. No outline none — always show focus state.
- **Error:** Border shifts to Error Red, glow ring in error red at 30% opacity.
- **Disabled:** Opacity 0.45, no cursor pointer.
- **Monospace inputs** (API key, model ID fields): use mono font face.

### Navigation
- **Style:** Dark surface navbar with Rule bottom border. Compact (44px height).
- **Brand mark:** Semibold, ink text. No logo imagery needed.
- **Nav links:** Muted text at rest, ink text on hover. Active route: Operational Amber text.
- **Mobile:** Links collapse; brand mark stays.

### Wizard Progress Indicator
Horizontal step track used during onboarding. Steps are 48px circles with emoji icons. Completed steps use Status Green fill; active step uses Primary Amber fill with focus glow ring; future steps at 40% opacity. Track line in Rule color.

### Badges / Chips
- **Status badges:** Uppercase label type (0.75rem, 600, tracked). Background: a 15% tint of the semantic color, text: the semantic color at full L.
- **Provider/model chips:** Mono font, Raised Surface bg, no border. Used inline in pipeline config and provider lists.

## 6. Do's and Don'ts

### Do:
- **Do** use Operational Amber exclusively for interactive elements that demand an action or represent the active/focus state.
- **Do** communicate bot health via the semantic color trio — teal (running), red (broken), amber (warn) — consistently across every status surface.
- **Do** use JetBrains Mono for all technical values: API keys, model IDs, pipeline role identifiers.
- **Do** use tonal surface steps (Void → Panel → Raised Surface) to create hierarchy before reaching for shadows.
- **Do** keep uppercase tracked text to status badges and table column headers only.
- **Do** show focus rings on every interactive element — this is an admin tool, keyboard navigation is expected.

### Don't:
- **Don't** use cream, sand, warm-neutral, or any near-white warm-tinted background. The bg is dark. Warmth lives in the amber accent, not the surface.
- **Don't** use the generic SaaS blue (#2563eb or equivalents) as a primary color. It has been explicitly replaced.
- **Don't** place uppercase tracked labels as decorative eyebrows above section headings. Prohibited.
- **Don't** use drop shadows on cards or panels at rest. Tonal layering only; shadows are reserved for floating overlays.
- **Don't** use gradient text (`background-clip: text`) — never.
- **Don't** use `border-left` greater than 1px as a colored accent stripe on cards or callouts. Use background tint instead.
- **Don't** build identical card grids where every card is the same size, same structure, same icon-heading-text layout. Differentiate by content density, not decoration.
- **Don't** use colored border fills or glassmorphism for alert callouts. Use semantic background tints at 12-15% opacity.
- **Don't** add motion to section entrances or page loads. Animation is for state changes (wizard step, status update, button press) only.
- **Don't** let the interface look like a weekend-project: missing error states, empty state copy that says "No data", unformatted code values, or unstyled form validation are prohibited.

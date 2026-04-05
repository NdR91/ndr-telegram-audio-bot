<!-- Context: project-intelligence/nav | Priority: high | Version: 1.1 | Updated: 2026-04-05 -->

# Project Intelligence

> Start here for quick project understanding and persistent cross-session memory.

## Structure

```text
.opencode/context/project-intelligence/
├── navigation.md              # This file - quick overview
├── business-domain.md         # Business context and problem statement
├── technical-domain.md        # Stack, architecture, technical decisions
├── business-tech-bridge.md    # How business needs map to solutions
├── decisions-log.md           # Major decisions with rationale
├── living-notes.md            # Active issues, debt, open questions
└── implementation-roadmap.md  # Persistent roadmap and delivery status
```

## Quick Routes

| What You Need | File | Description |
|---------------|------|-------------|
| Understand the "why" | `business-domain.md` | Problem, users, value proposition |
| Understand the "how" | `technical-domain.md` | Stack, architecture, integrations |
| See the connection | `business-tech-bridge.md` | Business → technical mapping |
| Know the context | `decisions-log.md` | Why decisions were made |
| Current state | `living-notes.md` | Active issues and open questions |
| Delivery plan | `implementation-roadmap.md` | Persistent roadmap, priorities, status |
| All of the above | Read all files in order | Full project intelligence |

## Usage

**New Team Member / Agent**:
1. Start with `navigation.md`
2. Read `implementation-roadmap.md` and `living-notes.md`
3. Read remaining files as needed for wider context

**Quick Reference**:
- Business focus → `business-domain.md`
- Technical focus → `technical-domain.md`
- Decision context → `decisions-log.md`
- Delivery status → `implementation-roadmap.md`

## Maintenance

Keep this folder current:
- Update roadmap status when work starts/completes
- Document important prioritization or architecture choices in `decisions-log.md`
- Review `living-notes.md` when new risks or issues emerge

## Related Files

- `.opencode/context/core/standards/project-intelligence.md`
- `.opencode/context/core/standards/project-intelligence-management.md`

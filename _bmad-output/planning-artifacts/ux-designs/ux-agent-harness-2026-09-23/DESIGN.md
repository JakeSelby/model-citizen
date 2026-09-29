---
title: Model Citizen design contract
status: final
created: 2026-09-23
updated: 2026-09-25
supersedes: ../ux-agent-harness-2026-09-19/DESIGN.md
colors:
  status: semantic-names-not-fixed-terminal-colors
typography:
  prose: system-sans
  code: system-monospace
rounded: none-required
spacing: compact-readable
components:
  - status-line
  - command-block
  - evidence-callout
  - usage-table
  - review-card
  - answer-card
  - drift-line (planned)
  - mismatch-line (planned)
  - selection-report (planned)
sources:
  - ../../prds/prd-agent-harness-2026-09-23/prd.md
  - ../../architecture-spines/architecture-agent-harness-2026-09-23/ARCHITECTURE-SPINE.md
  - ../../research/competitive-configurability-and-selection-models-2026-09-23/research.md
---

> Current delivery assignments: [Delivery annotations — 2026-09-28](../../roadmap-2026-09-28.md). Earlier dates below remain historical.

# Brand and style

Model Citizen is direct, inspectable and calm. It leads with what a developer can find out or do, and then
states its limits without promotional inflation. This contract governs three surfaces:
- the terminal;
- the agent's own answers and plans, which the `voice` stance and the Review Card shape;
- the landing copy generated from `product.json`.

The mark is a dial pointer, meaning stances as a setting. It never counts anything, runtimes or
providers, because a count goes stale. The name is a plain category name. A name collision is resolved
with the full repository path, not by renaming.

Components marked *(planned)* describe the target for a planned PRD requirement, not current output.

# Colors

No meaning depends on colour. Every status is a text word from a closed set, and colour, where a
terminal supports it, only reinforces the word. The sets follow the code's vocabulary (architecture spine,
Consistency Conventions):
- **Catalog states:** `qualified`, `unqualified`, `planned`, `unsupported`. Documentation says "preview"
  for an unqualified surface.
- **Capability modes:** `instruction`, `instruction-and-hook`, `instruction-and-setting`.
- **Evidence results:** `passed`, `failed`, `unverified`.
- **Measurement:** known, partial, unavailable, failed.
- **Decision-provider stages:** `off`, `shadow`, `advise`, `act`.

# Typography

- Prose uses the host system's sans-serif.
- Commands, paths, ids and figures use monospace.
- Operational surfaces use no decorative display type.
- Brand assets ship as rendered images. Font subsets do not ship until their licence and provenance are
  cleared.

# Layout and spacing

- **The first line is the answer:** a verdict, a count or the next command.
- **Caveats sit next to the claim they limit.** A figure is never separated from its sample size or status.
- **Dense evidence sits behind a path or a flag,** never above the first action.
- **Chat output, plans and hook notices read on a phone.** They use no tables, and every line stays within
  80 columns.
- **Known gap:** the CLI's fixed-column usage tables are wider than 80 columns, at 113 and 147. A narrow
  layout for them is a design goal, not current output.

# Components

- **Status line.** The CLI prints `subject: state`, for example `codex-cli-macos: unqualified`. Where
  evidence scope matters, it follows in parentheses.
- **Command block.** One complete, copyable command, with no hidden prerequisite.
- **Evidence callout.**
  - It states the observed behaviour, the exact surface and version, and the limitation.
  - It names whether the claim rests on generated configuration, a hook, or native evidence.
- **Usage table (`citizen usage`).**
  - One row per group, with fixed columns and one grouping per run.
  - Partial data is reported in the header and in the `unpriced` footer.
  - Dollars are list-price equivalents, and the documentation says so beside the `usd` column.
  - `--by role` marks any role with fewer than 30 samples.
- **Review Card.** The plan's first screen: verdict, at a glance, a text diagram, steps with exit tests,
  decisions and risks.
  - The plan hook enforces an 85-line cap, no tables, and the decisions heading.
  - The 70-line target, the diagram limits (a `text` fence of at most 12 nodes and 80 columns) and the
    rule that Mermaid appears only below the card's rule are authoring rules in the plan-authoring skill.
- **Answer card.** The answer on the first line, then the why, the catch, the alternatives, and what is
  needed from the reader. Status words are literal.
- **Drift line (planned, v0.17.0, #692, FR-50).** It shows the declared value, the measured value and the
  evidence rows. The change it proposes is only applied when the developer applies it.
- **Mismatch line (planned, v0.14.0, FR-20).** A rule that is switched on but never fired in the window,
  shown with its detector and the window length. Today, `--rules` is keyed by detector and marks
  `unobserved`.
- **Selection report (planned, v0.14.0, FR-16).**
  - Each key's effective value, with the layer it came from.
  - Mode keys that were applied, listed apart from user values that were kept.
  - Each shadowed key, named.
  - For a headless session, `headless` shown beside the mode.

# Dos and don'ts

**Do:**
- Keep generated configuration, implemented policy and observed native behaviour apart, every time.
- Quote a measured figure with its status, or quote no figure.
- Credit the projects the harness learns from. Do not frame them as competition.

**Don't:**
- Use visual polish to imply qualification, savings or capability that has not been measured.
- Use em dashes in published copy.
- Claim universal compatibility, identical behaviour across runtimes, or being first.

## Delivery annotations — 2026-09-28

Visual identity and component design remain unchanged. Current delivery assignments and later-engine Studio additions follow the roadmap amendment; planned stance-drift/proposal surfaces belong to 0.18. The existing Studio design effort under #961 is preserved.

See [the authoritative roadmap amendment](../../roadmap-2026-09-28.md) and #1061 for the issue-level moves, scope splits and added integration stories. These changes remain planned, not shipped.

---
name: Model Citizen
description: A direct, inspectable harness with a clean, bright operational Studio.
status: final
created: 2026-09-23
updated: 2026-09-27
supersedes: ../ux-agent-harness-2026-09-19/DESIGN.md
sources:
  - ../../prds/prd-agent-harness-2026-09-23/prd.md
  - ../../architecture-spines/architecture-agent-harness-2026-09-23/ARCHITECTURE-SPINE.md
  - ../../research/competitive-studio-field-check-edit-eval-compare-uis-2026-09-26/research.md
colors:
  surface-canvas: '#F5F8F8'
  surface-raised: '#FFFFFF'
  ink-primary: '#182B2D'
  ink-secondary: '#4E6265'
  border-default: '#CBD8DA'
  control-border: '#73888B'
  primary: '#14635E'
  primary-wash: '#E8F4F1'
  success: '#176443'
  success-wash: '#EAF6EE'
  warning: '#805008'
  warning-wash: '#FFF4DE'
  danger: '#B42318'
  danger-wash: '#FEE4E2'
  surface-canvas-dark: '#101B1E'
  surface-raised-dark: '#18272A'
  ink-primary-dark: '#EDF5F5'
  ink-secondary-dark: '#B0C2C5'
  border-default-dark: '#3C5459'
  control-border-dark: '#83989D'
  primary-dark: '#7CDDD0'
  primary-wash-dark: '#213F3D'
  success-dark: '#94DDB0'
  success-wash-dark: '#213A2B'
  warning-dark: '#F1CA86'
  warning-wash-dark: '#43351F'
  danger-dark: '#FDA29B'
  danger-wash-dark: '#4A1D1D'
typography:
  display:
    fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif'
    fontSize: 38px
    fontWeight: '650'
    lineHeight: '1.2'
    letterSpacing: -0.025em
  heading:
    fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif'
    fontSize: 20px
    fontWeight: '650'
    lineHeight: '1.35'
  body:
    fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif'
    fontSize: 14px
    fontWeight: '400'
    lineHeight: '1.5'
  label:
    fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif'
    fontSize: 12px
    fontWeight: '650'
    lineHeight: '1.4'
  meta:
    fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif'
    fontSize: 12px
    fontWeight: '400'
    lineHeight: '1.4'
  code:
    fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace'
    fontSize: 12px
    fontWeight: '400'
    lineHeight: '1.5'
rounded:
  sm: 6px
  md: 10px
  lg: 12px
  full: 9999px
spacing:
  '1': 4px
  '2': 8px
  '3': 12px
  '4': 16px
  '5': 20px
  '6': 24px
  '7': 28px
  '8': 32px
  page-x: 36px
  section: 28px
components:
  status-line:
    text: '{colors.ink-primary}'
    meta: '{colors.ink-secondary}'
  command-block:
    background: '{colors.surface-raised}'
    foreground: '{colors.ink-primary}'
    border: '{colors.border-default}'
    radius: '{rounded.sm}'
  evidence-callout:
    background: '{colors.primary-wash}'
    foreground: '{colors.ink-primary}'
    border: '{colors.border-default}'
    radius: '{rounded.md}'
  usage-table:
    background: '{colors.surface-raised}'
    foreground: '{colors.ink-primary}'
    divider: '{colors.border-default}'
  review-card:
    background: '{colors.surface-raised}'
    foreground: '{colors.ink-primary}'
    border: '{colors.border-default}'
    radius: '{rounded.md}'
  answer-card:
    background: '{colors.surface-raised}'
    foreground: '{colors.ink-primary}'
  drift-line:
    foreground: '{colors.ink-primary}'
    meta: '{colors.ink-secondary}'
  mismatch-line:
    foreground: '{colors.warning}'
    background: '{colors.warning-wash}'
  selection-report:
    background: '{colors.surface-raised}'
    foreground: '{colors.ink-primary}'
    divider: '{colors.border-default}'
  studio-navigation:
    background: '{colors.surface-raised}'
    foreground: '{colors.ink-secondary}'
    active: '{colors.primary}'
    active-background: '{colors.primary-wash}'
  studio-panel:
    background: '{colors.surface-raised}'
    foreground: '{colors.ink-primary}'
    border: '{colors.border-default}'
    radius: '{rounded.lg}'
  status-badge:
    radius: '{rounded.sm}'
    neutral-background: '{colors.surface-canvas}'
    neutral-foreground: '{colors.ink-secondary}'
  report-card:
    background: '{colors.surface-raised}'
    foreground: '{colors.ink-primary}'
    border: '{colors.border-default}'
    radius: '{rounded.lg}'
  ai-overview-card:
    background: '{colors.surface-raised}'
    foreground: '{colors.ink-primary}'
    stale-background: '{colors.warning-wash}'
    stale-foreground: '{colors.warning}'
    radius: '{rounded.lg}'
  ai-settings-form:
    background: '{colors.surface-raised}'
    foreground: '{colors.ink-primary}'
    border: '{colors.border-default}'
    radius: '{rounded.md}'
  investigation-row:
    foreground: '{colors.ink-primary}'
    meta: '{colors.ink-secondary}'
    divider: '{colors.border-default}'
  notification-row:
    foreground: '{colors.ink-primary}'
    meta: '{colors.ink-secondary}'
    divider: '{colors.border-default}'
  draft-workspace:
    background: '{colors.surface-canvas}'
    foreground: '{colors.ink-primary}'
  module-editor:
    background: '{colors.surface-raised}'
    foreground: '{colors.ink-primary}'
    border: '{colors.border-default}'
    radius: '{rounded.md}'
  governed-action-review:
    background: '{colors.surface-raised}'
    foreground: '{colors.ink-primary}'
    warning: '{colors.warning}'
    border: '{colors.border-default}'
    radius: '{rounded.lg}'
  evidence-chart:
    foreground: '{colors.ink-primary}'
    axis: '{colors.ink-secondary}'
    grid: '{colors.border-default}'
    accent: '{colors.primary}'
  run-console:
    background: '{colors.surface-raised}'
    foreground: '{colors.ink-primary}'
    border: '{colors.border-default}'
    radius: '{rounded.lg}'
  progress-indicator:
    track: '{colors.border-default}'
    fill: '{colors.primary}'
    foreground: '{colors.ink-primary}'
  comparison-report:
    background: '{colors.surface-raised}'
    foreground: '{colors.ink-primary}'
    inconclusive: '{colors.warning}'
    divider: '{colors.border-default}'
  proposal-card:
    background: '{colors.primary-wash}'
    foreground: '{colors.ink-primary}'
    border: '{colors.border-default}'
    radius: '{rounded.md}'
  activity-row:
    foreground: '{colors.ink-primary}'
    meta: '{colors.ink-secondary}'
    divider: '{colors.border-default}'
  studio-dialog:
    background: '{colors.surface-raised}'
    foreground: '{colors.ink-primary}'
    border: '{colors.border-default}'
    radius: '{rounded.lg}'
---

> Current delivery assignments: [Delivery annotations — 2026-09-28](../../roadmap-2026-09-28.md). Earlier dates below remain historical.

# Model Citizen design contract

## Brand & Style

Model Citizen is direct, inspectable and calm. It leads with what a developer can find out or do, then
states its limits without promotional inflation. This contract governs four surfaces:

- the terminal;
- the agent's own answers and plans, which the `voice` stance and Review Card shape;
- the landing copy generated from `product.json`;
- the proposed Studio, a local operational surface for the same harness core.

The Studio is a clean, bright operational briefing, not a wall of gauges. Its hierarchy starts with the
meaning of recent evidence, then exposes the report, draft, run or configuration behind every claim. It
uses the chosen **Briefing** direction and **Clear** theme: airy composition, quiet teal, cool neutrals,
and restrained semantic color.

The Studio inherits UI primitives from `@mantine/core` and `@mantine/hooks` **9.6.3**. Mantine owns base
buttons, links, navigation controls, badges, alerts, cards, paper, skeletons, form controls, tabs, tables,
scroll areas, modals, drawers, tooltips, notifications, loaders, progress, focus trapping and visually
hidden text. This contract owns only the semantic theme overrides and local evidence components listed
below. The approved packages are MIT; implementation still waits for the exact dependency lockfile,
notices, bundle inspection, accessibility checks and deterministic rebuild gate.

The mark is a dial pointer, meaning stances as a setting. It never counts anything, runtimes or providers,
because a count goes stale. The name is a plain category name. A name collision is resolved with the full
repository path, not by renaming.

Everything described for the Studio is proposed, not implemented or validated. Components marked
*(planned)* elsewhere retain that same meaning. The approved
[Briefing/Clear Hub mockup](mockups/studio-hub-briefing-clear.html) illustrates the Hub only; this
spine wins on conflict.

## Colors

No meaning depends on color. Every status is a text word from a closed set, and color only reinforces the
word. Terminal output keeps host colors rather than attempting to reproduce the Studio palette.

The Studio's Clear palette uses `{colors.surface-canvas}` behind
`{colors.surface-raised}` panels. `{colors.ink-primary}` carries decisions and measures;
`{colors.ink-secondary}` carries provenance and supporting detail. `{colors.primary}` is for navigation,
links, focus and primary actions, never for decoration. Matching `-dark` tokens preserve the same roles in
dark mode. On first open the surface follows the system theme; an explicit light/dark choice persists.

Semantic combinations are fixed:

- success: `{colors.success}` on `{colors.success-wash}`;
- warning, stale or inconclusive: `{colors.warning}` on `{colors.warning-wash}`;
- destructive or failed: `{colors.danger}` on `{colors.danger-wash}`.

Primary text on base and raised surfaces, semantic text on its wash, control borders, and focus indicators
must meet WCAG 2.2 AA. Interactive boundaries use `{colors.control-border}`, which is distinct from the
quieter `{colors.border-default}`.

The source vocabularies remain literal:

- **Catalog states:** `qualified`, `unqualified`, `planned`, `unsupported`. Documentation says `preview`
  for an unqualified surface.
- **Capability modes:** `instruction`, `instruction-and-hook`, `instruction-and-setting`.
- **Evidence results:** `passed`, `failed`, `unverified`.
- **Operations:** `proposed`, `applied`, `unchanged`, `conflicted`, `skipped`, `failed`, `refused`.
- **Measurement:** `known`, `partial`, `unavailable`, `failed`, `unknown`.
- **Freshness:** `current`, `stale`.
- **Comparison verdicts:** `improved`, `flat`, `regressed`, `inconclusive`, `unverified`.
- **Provenance classes:** `authoritative-source`, `deterministic-derived`, `imported-evidence`,
  `generated-advisory`.
- **Decision-provider stages:** `off`, `shadow`, `advise`, `act`.

WCAG 2.2 AA is not claimed from token intent alone. Implementation evidence must record the measured
contrast ratio for every light/dark semantic foreground-background pair, text size/weight, control
boundary and focus indicator against both adjacent colors. Required floors are 4.5:1 for normal text and
3:1 for large text, UI components and focus indicators. A failing or missing ratio blocks qualification.

## Typography

The Studio and prose use the host system sans-serif. Commands, paths, identifiers, figures and logs use
`{typography.code.fontFamily}`. The Studio reserves `{typography.display}` for the Hub briefing headline
and meaningful empty-state headlines. Operational detail uses body, label and meta roles; no decorative
display face is introduced.

Brand assets ship as rendered images. Font subsets do not ship until their license and provenance are
cleared.

## Layout & Spacing

- **The first line is the answer:** a verdict, count, condition or next command.
- **Caveats sit next to the claim they limit.** A figure is never separated from its sample size, interval
  or status.
- **Dense evidence sits one drill-down away,** never above the first action.
- **Chat output, plans and hook notices read on a phone.** They use no tables, and every line stays within
  80 columns.
- **Known gap:** the CLI's fixed-column usage tables are wider than 80 columns, at 113 and 147. A narrow
  layout for them is a design goal, not current output.

Studio pages use a maximum-width canvas with `{spacing.page-x}` desktop gutters. The Hub uses a primary
briefing column and a smaller two-by-two report-card region, followed by drafts/runs and alerts. Major
regions separate by `{spacing.section}`. Cards use `{spacing.6}` to `{spacing.7}` internal padding; dense
rows use `{spacing.3}` to `{spacing.4}`. At narrower widths, regions stack in reading order without
changing the five-area navigation vocabulary.

## Elevation & Depth

Hierarchy comes from spacing, tone and borders. `{colors.surface-raised}` panels sit on
`{colors.surface-canvas}` with a one-pixel `{colors.border-default}` boundary. Shadows are optional,
subtle and never the only indication that a surface is interactive. Hover may strengthen the border to
`{colors.primary}`; focus always uses a visible ring.

## Shapes

The Studio reads as a tool with quiet softness. Controls use `{rounded.sm}`, editors use `{rounded.md}`,
and major panels use `{rounded.lg}`. `{rounded.full}` is reserved for circular progress or avatar-like
primitives, not status pills. Terminal, chat and published-copy surfaces do not acquire a shape language.

## Components

Visual specifications only; behavior lives in `EXPERIENCE.md` under Component Patterns.

### Mantine inheritance and local deltas

- **Theme:** map the Clear semantic colors, system typography, radii and spacing from this frontmatter;
  add no decorative font or gradient.
- **Button, Anchor and NavLink:** primary actions and active navigation use `{colors.primary}` and
  `{colors.primary-wash}`; links remain underlined on hover and focus.
- **Card and Paper:** use `{colors.surface-raised}` with a one-pixel `{colors.border-default}` boundary;
  no default elevation for hierarchy.
- **Badge, Alert and Notification:** use literal state text and the success/warning/danger
  foreground-wash pairs; never a color-only dot.
- **Skeleton and Loader:** use neutral canvas/border tones and an adjacent loading label; never resemble a
  real zero or completed value.
- **Input family:** use `{colors.control-border}` and Mantine's error relationship; editor-specific
  summaries and line links are local behavior.
- **Table and ScrollArea:** keep ruled evidence rows and sticky context labels; horizontal overflow is
  limited to genuine tabular or log content.
- **Modal, Drawer and FocusTrap:** use `{rounded.lg}` and the standard raised surface; naming, initial focus,
  inert background and restoration are required behavioral deltas.
- **Progress:** use `{colors.primary}` on `{colors.border-default}` with visible text for count and state;
  semantics are never inferred from the bar.

`evidence-chart`, `run-console`, `comparison-report`, `module-editor`, `draft-workspace` and the AI
components are local compositions. `evidence-chart` is a local accessible SVG plus equivalent data table;
it does not add an unapproved chart package.

| Component | Visual contract |
|---|---|
| status-line | Plain `subject: state` text. State word remains visible without color. |
| command-block | Monospace, copyable command on a quiet bordered surface. No hidden prerequisite. |
| evidence-callout | Tonal callout that keeps claim, evidence scope, version and limitation together. |
| usage-table | Dense ruled rows; figures align, partial and unpriced labels stay adjacent to totals. |
| review-card | First-screen hierarchy for verdict, bullets, text diagram, steps, decisions and risks. |
| answer-card | First line carries the answer; following lines carry why, catch and next action. |
| drift-line *(planned, v0.17.0, #692, FR-50)* | Declared value, measured value and evidence rows share one horizontal or stacked unit. |
| mismatch-line *(planned, v0.14.0, FR-20)* | Warning wash and literal `unobserved` or mismatch language, never color alone. |
| selection-report *(planned, v0.14.0, FR-16)* | Ruled list with effective value, source layer, applied mode keys, kept user values and shadowed keys. |
| studio-navigation *(proposed, v0.18.0)* | Horizontal Hub, Configure, Experiments, Reports, Activity. Active area uses primary wash and text. |
| studio-panel *(proposed, v0.18.0)* | Raised bordered container for one operational concern; heading and provenance remain inside it. |
| status-badge *(proposed, v0.18.0)* | Compact text label with semantic foreground and wash; short radius, never a color-only dot. |
| report-card *(proposed, v0.18.0)* | Label, measure, unit, observed/eligible/failed/unknown counts, window, literal status, provenance class, snapshot ID and drill-down affordance. |
| ai-overview-card *(planned, v0.18.0, FR-85)* | Largest Hub panel. `Generated advisory`, assessment time, freshness, provider/model/tool/pricing provenance, spend coverage and claim-level evidence links stay visible. |
| ai-settings-form *(planned, v0.18.0, FR-85)* | Mantine form controls grouped under opt-in, execution, provenance, budget and refresh; active, unsaved, stale, validating, failed and cap-reached values are visibly distinct. |
| investigation-row *(planned, v0.18.0, FR-85)* | `Generated suggestion`, number, action-led finding, bounded evidence statement and immutable deep link; separated by hairlines and distinct from alerts. |
| notification-row *(proposed, v0.18.0)* | Event title, occurrence count, first/last time, subject, read/dismiss affordances and immutable Activity link; alert styling is not reused. |
| draft-workspace *(proposed, v0.18.0)* | Persistent draft identity, base, checkpoint and `Nothing applied` status frame all editing surfaces. |
| module-editor *(proposed, v0.18.0)* | Form or monospace editor paired with lint, context budget and projection preview; error summary precedes linked field or line diagnostics, with first failure focused. |
| governed-action-review *(proposed, v0.18.0)* | Diff, checks, exact `citizen` commands, ownership effects and rollback path precede the primary action. |
| evidence-chart *(proposed, v0.18.0)* | Restrained line/bar/interval marks with labeled axes, no decorative fill, and a visible link to the equivalent evidence table. |
| run-console *(proposed, v0.18.0)* | Target, suite/case, isolation, spend, semantic progress, Pause/Resume, log and cancel occupy one bounded operational panel. |
| progress-indicator *(proposed, v0.18.0)* | Determinate bar plus visible completed/eligible count, or literal indeterminate state; color never carries completion alone. |
| comparison-report *(proposed, v0.18.0)* | Evidence verdict, metric/no-effect value, delta, paired interval, snapshot identity, grouped cases, grader explanation and matched-rerun action. |
| proposal-card *(proposed, v0.18.0)* | Evidence-backed stance proposal in a primary wash; `Try in draft` is visually distinct from apply. |
| activity-row *(proposed, v0.18.0)* | Timestamp, actor/surface, literal action state and link to the decision, journal or native result. |
| studio-dialog *(proposed, v0.18.0)* | Mantine Modal/Drawer shell with a visible title, restrained border, one primary action and an always-visible close or cancel action. |

## Do's and Don'ts

| Do | Don't |
|---|---|
| Keep generated configuration, implemented policy and observed native behavior apart | Use polish to imply qualification, savings or capability that has not been measured |
| Put sample size, interval, window and freshness beside each reported figure | Show an unexplained score, delta or alert count |
| Let every Hub aggregate and AI claim drill into its report and evidence | Make the Hub or AI overview a new system of record |
| Show provenance class, immutable snapshot identity and evidence status with operational claims | Link a claim only to a mutable latest-report route |
| Preserve source status words and use color only as reinforcement | Replace `failed`, `stale`, `unverified` or `inconclusive` with colored dots |
| Keep configuration changes inside a visibly named draft | Make an edit control look as though it changes live state |
| Use quiet teal for focus and restrained semantic colors for state | Fill the dashboard with saturated charts, gradients or decorative gauges |
| Credit the projects the harness learns from | Frame the field as competition or claim universal compatibility |
| Use no em dashes in published copy | Let incidental mock copy override this contract |

## Delivery annotations — 2026-09-28

Visual identity and component design remain unchanged. Current delivery assignments and later-engine Studio additions follow the roadmap amendment; planned stance-drift/proposal surfaces belong to 0.18. The existing Studio design effort under #961 is preserved.

See [the authoritative roadmap amendment](../../roadmap-2026-09-28.md) and #1061 for the issue-level moves, scope splits and added integration stories. These changes remain planned, not shipped.

## Amendment — 2026-10-06: the operational direction

This amendment supersedes the "Briefing" direction and the "Clear" palette for the delivered Studio. The sections above stay as the record of the 2026-09-23 design; where they disagree with this one, this one governs. Chosen 2026-10-06 as option A of five rendered studies: [the target study](studies/2026-10-06-operational.html) and [its rendering](studies/2026-10-06-operational.png). The brief: clean, white, operational, data first, with nothing outside the metrics and information to catch the eye. Briefing read card-heavy, with too much margin.

Only the look changes. Routes, data, workflows and product copy are as they were.

### Shell

- A Mantine `AppShell` in static mode: a 220 px white navbar with a hairline border on its right, and a 52 px header holding the page title, the Studio version and workspace context, and the CLI command the page's evidence comes from, beside the live, theme and drafts controls.
- A white canvas. Below 48em the shell is one column: the navbar wraps into a strip above the header, and every grid row stacks.

### Pages

- No cards and no bordered papers. A page is a heading, then sections separated by single hairlines; an item inside a section is a hairline row.
- The Hub opens on one number strip split by vertical dividers (installed system, doctor checks, projection drift, recent runs), then the release line, doctor checks, projection drift, recent runs, the AI health overview and the report links, each a hairline section of compact rows.
- Tables and rows are compact: about 30 px a row, 13 px text, 12 px column labels in secondary ink. Monospace only for ids, commands and numbers that must align; tabular figures everywhere else.
- Configure, Library, Experiments, Activity, Reports (trends and rule health) and Setup use the same density.

### Colour

One muted teal accent, `#087F5B` on `#E6FCF5`, for the active navigation item, links, primary buttons and the focus ring. Red, amber and green carry status only, as a dot beside the status word; the word stays in body ink. Where a status colour is text, it uses a shade that keeps 4.5:1 on its canvas.

| Token | Light | Dark |
|---|---|---|
| Canvas and raised surface | `#FFFFFF` | `#16181B` |
| Subtle surface (code, hover) | `#F8F9FA` | `#1F2226` |
| Ink | `#212529` | `#E9ECEF` |
| Secondary ink | `#646C73` | `#A6A7AB` |
| Hairline | `#E9ECEF` | `#2C2F34` |
| Control border | `#868E96` | `#7C8189` |
| Accent / wash | `#087F5B` / `#E6FCF5` | `#63E6BE` / `#0F2A22` |
| Success text / dot / wash | `#237032` / `#2B8A3E` / `#EBFBEE` | `#8CE99A` / `#51CF66` / `#17301D` |
| Warning text / dot / wash | `#A85500` / `#E67700` / `#FFF4E6` | `#FFC078` / `#FF922B` / `#33260F` |
| Danger text / dot / wash | `#C92A2A` / `#C92A2A` / `#FFF5F5` | `#FF8787` / `#FF6B6B` / `#3A1A1A` |

The study's secondary ink, `#868E96`, is 3.3:1 on white, under the AA floor for 13 px text; the Studio darkens it to `#646C73` (5.3:1) and keeps `#868E96` for control borders, which need 3:1. The study's amber `#E67700` and green `#2B8A3E` are kept for the dots; their text shades are darkened the same way.

### Type

Inter and JetBrains Mono are named first in their stacks, with system fallbacks. The Studio makes no network requests and bundles no font file, so a machine without them renders the system face. Bundling either would need a licensing review first. The page heading is 20 px, section headings 14 px, body 14 px, rows 13 px.

### Implementation

`studio/src/theme.ts` carries the accent, the type scale and the tighter spacing scale; `studio/src/styles.css` carries the tokens above, the shell, the hairline sections and the compact rows. The accessibility suite (#999) and the bundle budgets (#1000) hold unchanged.

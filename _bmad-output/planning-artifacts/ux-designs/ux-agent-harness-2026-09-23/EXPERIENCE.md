---
name: Model Citizen
status: final
created: 2026-09-23
updated: 2026-09-27
supersedes: ../ux-agent-harness-2026-09-19/EXPERIENCE.md
sources:
  - DESIGN.md
  - ../../prds/prd-agent-harness-2026-09-23/prd.md
  - ../../architecture-spines/architecture-agent-harness-2026-09-23/ARCHITECTURE-SPINE.md
  - ../../epics.md
  - ../../research/competitive-studio-field-check-edit-eval-compare-uis-2026-09-26/research.md
---

# Model Citizen developer experience contract

## Foundation

The harness has four operational surfaces:

- **The local CLI and its reports.** The command is `citizen`; `harness` stays a supported alias.
- **The agent's own session:** answers shaped by the `voice` stance, plans in the runtime's native plan
  pane, the live usage feed, and hook notices.
- **Landing and README copy,** generated from `product.json`.
- **The proposed Studio,** a local responsive web UI opened by `citizen studio` and backed by the same
  core, ledgers, checks and native evidence as the CLI.

Native runtimes consume the projections but keep authority over permissions and behavior. `DESIGN.md` is
the visual identity reference; this spine owns information architecture, behavior and journeys.
Operational truth comes from CLI state, local ledgers, decision records and native result files.

The Studio inherits primitives from `@mantine/core` and `@mantine/hooks` 9.6.3. `DESIGN.md` enumerates
Mantine's ownership and every local visual delta; this spine specifies behavioral deltas only. The exact
lockfile, notices, bundle inspection, accessibility checks and deterministic rebuild are implementation
gates under AD-21 and AD-28, not open UX choices. AD-25 keeps the CLI and Studio as two faces on one core;
under PRD §7 the bundled loopback HTTP API is private and the `citizen` CLI remains the public agent
surface. AD-26, AD-27 and AD-31 govern live transport, launcher security and recoverable lifecycle;
AD-29 and AD-30 govern drafts and evidence. All Studio capabilities in FR-75 to FR-85 and epics AH-E022
to AH-E026 are planned for v0.18.0, not implemented or validated. FR-85 and AD-32 bind the optional AI
health overview, its read-only execution, provenance, refresh and budget states.

## Information Architecture

The existing developer loop remains:

1. **Measure.** The standalone instrument shows which rules fire before anything is installed (UJ-3).
2. **Understand.** The README leads with that question, then shows a real dry-run capture, capability
   groups and release status.
3. **Select.** `citizen init` names runtimes, a preset and identity. Stances select behavior, and modes
   will too *(planned, v0.14.0)*.
4. **Preview.** A dry run lists links, rendered files, settings and conflicts before ownership changes.
5. **Apply.** Sync records what the harness owns and preserves unrelated state.
6. **Inspect.** `doctor`, `compatibility`, `stances`, `stances --json` and `usage` keep their concerns
   separate.
7. **Recover.** Repeat sync, drift repair, upgrade, rollback and uninstall explain preserved conflicts
   and restored values.

The Studio adds five stable navigation areas. Drafts are a global working state, not a sixth area.

| Area or surface | Purpose | Journey that lands here |
|---|---|---|
| Hub | Operational briefing: health, effective selection, alerts, notifications, report aggregates, active drafts/runs and the optional AI overview | Studio Flow 1; Studio Flow 6 |
| Configure | Effective selection with source layers, module library, manifests, projections, context cost and draft entry points | Studio Flow 1; Studio Flow 2 |
| Draft workspace | Global draft state: base, changes, checkpoint, checks, diff, projection preview and `Nothing applied` | Studio Flow 2; Studio Flow 3 |
| Governed action review | Exact `citizen` commands, ownership effects, checks, apply and rollback path | Studio Flow 3 |
| Experiments | Suite catalog, targets, launch guard, running and historical runs | Studio Flow 4 |
| Run detail | Live log, case progress, spend, caps, cancel and preserved native result | Studio Flow 4 |
| Reports | System health, hook performance, efficacy, rule health, usage, trends, proposals and imported evidence | Studio Flow 1; Studio Flow 5 |
| Report detail | Verdict, intervals, groups, cases, grader explanations and proof set | Studio Flow 5 |
| Activity | What the harness decided and changed, with journal and native-evidence links | Studio Flow 1; Studio Flow 3 |
| AI settings *(planned, FR-85)* | Opt-in, provider/model provenance, automatic refresh, daily cap and current spend | Studio Flow 1; Studio Flow 6 |
| First-run guide | Install health, selection orientation, first draft, local run, comparison and governed apply | Studio Flow 6 |

The Hub follows the approved
[Briefing/Clear mockup](mockups/studio-hub-briefing-clear.html). Each report aggregate is a link to
its underlying report.
Alerts and AI investigation items deep-link to the relevant report or Configure location. These working
studies illustrate composition only; the spines win on conflict.

### Studio requirement closure

| Source-spec name | Surface and journey |
|---|---|
| FR-75: One command opens the Studio | Hub; Studio Flow 1 and Studio Flow 6 |
| FR-76: A local surface locked to its launcher | Launcher/session states across all areas; Studio Flow 1 failure |
| FR-77: See the whole harness, live | Hub, Configure, Reports, Activity; Studio Flow 1 |
| FR-78: Tune in drafts | Configure, Draft workspace; Studio Flow 2 |
| FR-79: Apply and roll back through the governed path | Governed action review, Activity; Studio Flow 3 |
| FR-80: Run any suite or single test against any target | Experiments, Run detail; Studio Flow 4 |
| FR-81: Spend shown and capped before it happens | Run launch guard and Run detail; Studio Flow 4 |
| FR-82: Compare runs and judge a change | Reports, Report detail; Studio Flow 5 |
| FR-83: Rule health, usage and proposals | Reports, proposal-card, Draft workspace; Studio Flow 5 |
| FR-84: The same loop for agents and newcomers | First-run guide plus visible `citizen` equivalents; Studio Flow 6 |
| FR-85: An advisory AI health overview | Hub, AI settings, immutable report snapshots and generated investigation queue; Studio Flow 1 and Studio Flow 6 |

AH-E022 maps to Studio Flow 1, AH-E023 to Studio Flows 2 and 3, AH-E024 to Studio Flow 4,
AH-E025 to Studio Flow 5, and AH-E026 to Studio Flow 6.

This UX contract directly closes the PRD's UJ-1 to UJ-9 and Studio FR-75 to FR-85. FR-1 to FR-74 remain
upstream product, CLI, policy and evidence requirements; they are outside this Studio UX closure unless an
existing journey or component names them explicitly. Their omission from the Studio table is deliberate
scope, not an assertion that this document satisfies them.

## Voice and Tone

Microcopy stays short, literal and evidence-bounded. Brand voice and aesthetic posture live in
`DESIGN.md`.

| Do | Don't |
|---|---|
| `Inconclusive: the 95% interval crosses zero.` | `The draft probably helped.` |
| `Nothing applied.` | `Your changes are safe!` |
| `Stale: the draft changed after this comparison.` | `Results may be outdated.` |
| `6 checks passed. 1 alert needs attention.` | `System health: 86.` |
| `Couldn't save. The live checkout is unchanged.` | `Something went wrong.` |
| `AI assessment, generated at 14:32 from 4 reports.` | `AI says your harness is unhealthy.` |

Status language uses the closed sets in `DESIGN.md`. Errors say what was protected, then give the next
inspection or retry action. Every figure stays with its status, sample size, interval or observation
window. Published copy follows the PRD vocabulary, uses no em dashes, claims no unmeasured saving and
credits the field.

## Component Patterns

Behavioral rules only. Visual rules live in `DESIGN.md` Components.

| Component | Behavioral contract |
|---|---|
| status-line | Prints `subject: state`; adds evidence scope in parentheses when scope changes meaning. |
| command-block | Contains one complete copyable command; copy exposes exactly the command shown. |
| evidence-callout | Names provenance class, source/version, evidence status and immutable source ID/digest/observation time beside the claim. |
| usage-table | Groups once per run; reports partial data in header and footer; marks roles below 30 samples. |
| review-card | Keeps verdict, at-a-glance bullets, text diagram, steps with exit tests, decisions and risks in the first screen. |
| answer-card | Leads with answer, then why, catch, alternatives and any required user action. |
| drift-line | Shows declared value, measured value and evidence rows; applying the proposal remains a separate developer action. |
| mismatch-line | Names rule, detector and observation window; `unobserved` never means absent. |
| selection-report | Separates effective values and source layers, applied mode keys, kept user values, shadowed keys and `headless`. |
| studio-navigation | Keeps Hub, Configure, Experiments, Reports and Activity in that order; active area is programmatically exposed. |
| studio-panel | Owns one concern and its provenance; never nests another full panel hierarchy. |
| status-badge | Pairs semantic color with literal text; never acts as the only link or control. |
| report-card | Opens an immutable report snapshot ID, not a mutable latest route; preserves window/target and shows observed, eligible, failed and unknown denominators. |
| ai-overview-card | Shows opt-in, advisory status, generation time, freshness, immutable provider/model/tool and evidence provenance, actual/estimated spend, coverage and cap; never mutates configuration. |
| ai-settings-form | Validates provider/tool/model and pricing provenance before enable; shows active versus unsaved values, save failure, provider unavailable and cap states. |
| investigation-row | Is labeled `Generated suggestion`, cites claim-level immutable evidence, and deep-links to the report snapshot or Configure target; it cannot set alert severity or state. |
| notification-row | Persists across restarts, deduplicates repeated events, supports read/dismiss, and always links to immutable Activity evidence that dismissal cannot remove. |
| draft-workspace | Keeps draft name, base, checkpoint and live-state status visible; every edit and test targets that draft explicitly. |
| module-editor | Structured forms run a debounced checked checkpoint 750 ms after the last change; text editors checkpoint only on Save or Cmd/Ctrl-S. Errors retain input, associate to fields/lines and focus the first failure through a summary. |
| governed-action-review | Shows diff, checks, exact CLI equivalents, ownership effects and rollback before Apply; applying requires explicit confirmation. |
| evidence-chart | Exposes the same measures, intervals, samples and windows in an adjacent accessible table; no claim exists only in the graphic. |
| run-console | Shows suite/case, target, isolated profile, progress, logs, spend and cap; Pause stops visual/live announcements but not recording, Resume catches up, and Cancel keeps completed cases and the record. |
| progress-indicator | Uses native progress or `role=progressbar` with name, value/min/max and textual completed/eligible counts; unknown totals are explicitly indeterminate. |
| comparison-report | Leads with evidence-standard verdict and metric-specific no-effect value; keys the paired task-clustered interval to immutable base/candidate runs, groups by target/suite, and offers a prefilled matched rerun when parameters differ. |
| proposal-card | Shows evidence and limitations; `Try in draft` creates or updates a draft and never applies live. |
| activity-row | Is immutable evidence linked to its decision log, ownership journal or native result; keeps per-item failures distinct from empty results even when a notification is dismissed. |
| studio-dialog | Uses a visible accessible name, initial focus on the least destructive useful control, inert background, trapped tab order, Escape/cancel, and focus restoration to the invoker. |

## State Patterns

### Cross-surface states

| State | Treatment |
|---|---|
| Cold load | Render the page skeleton and a literal `Loading local state` label. Do not show zeroes as placeholders. |
| Live update | Update within two seconds, preserve focus, and never replace editor content. A persistent Pause control freezes visual updates and announcements while recording continues; Resume applies queued updates and states their count. |
| Local server lost | Keep the last rendered state read-only, label it stale, and offer reconnect. Never imply the command completed. |
| Unauthorized (401) | Show `Studio session expired` and route back through the launcher exchange. No protected detail remains. |
| Forbidden Host/Origin/CSRF (403) | Show a generic refused-request page with no secret, host detail or bypass action. |
| Partial data | Name failed and unknown sources; show `observed / eligible` plus failed and unknown counts. If eligibility itself is unknown, say `eligible denominator unknown`. Unknown is not empty. |
| Empty | Explain what is absent and link to the journey that creates the first record. |
| Focus | Visible focus ring, stable focus order and no focus loss during background refresh. |

### Surface states

| Surface | Required states |
|---|---|
| Hub | loading; healthy with no alerts; authoritative alerts present; generated suggestions present; no reports yet; active draft/run; source data stale; partial source failure; updates paused |
| Configure | loading; no user modules; validation refused; dependency conflict; external file change; source layer unavailable |
| Draft workspace | new/unchanged; form checkpoint pending; text dirty/unsaved; checking; checkpoint saved; save refused; verdict current; verdict stale; discarded; applied; rolled back |
| Governed action review | checks pending; checks passed; checks failed; lock busy; apply confirmed; apply failed safely; rollback available; rollback complete; rollback conflict refused with later user edit preserved |
| Experiments | no suites; no run history; target unavailable; estimate loading; confirm required; launching; running; cancelling; stopped; completed; failed; usage limit reached |
| Run detail | queued; live log connected; updates paused/resuming; log reconnecting; determinate/indeterminate progress; per-case failure; cap reached with finished cases preserved; result reindexed |
| Reports | no evidence; importing; partial import; current; stale; `passed`; `failed`; `unverified`; measurement `known`, `partial`, `unavailable`, `failed`, `unknown`; source `refused` |
| Report detail | improved; flat; regressed; inconclusive because the interval includes the metric's no-effect value; unmatched parameters with prefilled rerun; snapshot missing; proof-set link unavailable |
| Activity | empty; loading older entries; reindexing; per-item failure; journal unavailable |
| First-run guide | not started; in progress; interrupted; blocked by check; abandoned with nothing live changed; complete |
| AI settings *(planned, FR-85)* | opted out/disabled; enabling/validating; enabled; unsaved changes; active value stale after external change; saving; save failed with active values retained; provider/tool/model unavailable; pricing stale; daily cap reached; disabled after prior use |
| AI overview *(planned, FR-85 / AD-32)* | no last-good assessment; coalescing evidence for 60 seconds; attempt reservation pending/refused/settled; queued; single-flight joined; refreshing with last-good marked stale; retry 1/3 to 3/3; retry reservation refused; cancelled; timed out; terminal refresh failed; provider unavailable; usage-limit refused; current; evidence partial |
| Notifications | unread; read; dismissed from Hub; repeated event deduplicated with occurrence count/last seen; restored after restart; immutable Activity evidence unavailable |

## Interaction Primitives

- Commands are explicit and composable. Every Studio action shows its `citizen` equivalent.
- Mutating operations have previews. Repeated CLI application remains safe.
- Studio editing begins by creating or selecting a draft. No form control writes live configuration.
- Structured forms debounce for 750 ms, then run CLI checks and create a checkpoint. Text editors never
  autosave; Save and Cmd/Ctrl-S run the same checks and create one checkpoint. A refused save keeps input,
  names the CLI reason, focuses the error summary, and links each diagnostic to its field or source line.
- Apply and rollback use explicit confirmation in governed-action-review. Destructive confirmation names the
  exact draft or applied change. Rollback restores still-owned fields/files to their recorded prior
  semantic values and becomes byte-identical only where no later user edit intervened.
- Report cards, alerts, notifications and investigation rows use ordinary links with meaningful labels.
- Report links pin an immutable snapshot identity. `View latest` is a separate action and never silently
  changes the evidence behind the current page.
- Tables support keyboard row navigation only when rows are interactive; otherwise native document
  navigation wins.
- `Esc` closes the topmost temporary layer. Modal stacks never exceed one level; opening a modal makes the
  background inert, traps focus, and restores focus to the invoker on close.
- Background refresh never moves focus or replaces an editor buffer. Pause freezes visible changes and
  announcements while collection continues; Resume applies queued state and announces the queued count.
- Run cancellation is available while a run can still stop. Finished cases remain visible.
- Approval, verification and task state never transfer implicitly between sessions or runtimes.
- A headless session becomes a declared fact in the selection, and waiting stances then proceed *(planned,
  v0.14.0)*.

## Operational Truth & Provenance

Every displayed datum carries all four fields below, either inline or in its immediately reachable
evidence disclosure:

1. **Provenance class:** `authoritative-source`, `deterministic-derived`, `imported-evidence`, or
   `generated-advisory`.
2. **Source identity:** owning source, immutable record/snapshot ID, source version and content digest.
3. **Evidence status:** `known`, `partial`, `unavailable`, `failed`, or `unknown`, plus observation time.
4. **Coverage:** observed and eligible denominators, with failed and unknown counts kept separate. If the
   eligible denominator is not known, the UI says so instead of calculating a percentage.

### Report snapshots

Each report snapshot has an immutable snapshot ID, report type/schema version, generated-at time, query,
filters/window, target, source record IDs/digests and computation version. Hub cards, notifications,
alerts, AI claims and shared links pin that snapshot ID. A partial snapshot remains inspectable and names
the successful, failed and unknown inputs. Reindexing may create a new snapshot but never rewrites the old
one.

### Comparison identity and invalidation

A matched comparison records:

- base and candidate run IDs/digests and immutable target revisions or draft checkpoint digests;
- suite/version, case-set or proof-set digest, case IDs and pairing key;
- runtime, adapter, provider, model and development-tool versions;
- rules, hooks, settings and isolated-profile digest for each target;
- grader/comparator versions, parameters, seeds, repetitions, platform/architecture and environment class;
- metric definition, units, direction and metric-specific no-effect value.

The verdict headline starts with `Improved`, `Flat`, `Regressed`, `Inconclusive` or `Unverified`, then the
metric, delta, no-effect value, paired interval and observed/eligible pair counts. A change to any source
run, draft checkpoint, case/proof set, pairing key, grader/comparator, metric definition, model/tool,
parameters or environment dependency invalidates the verdict and marks it `stale`. Unmatched parameters
offer a prefilled matched rerun that locks equal fields and highlights the intended target difference.

### Model, tool, pricing and cost provenance

AI and paid-run disclosures show immutable provider identifier, model identifier/snapshot, adapter and
development-tool name/version/digest, pricing source/version/effective date, currency and billing unit.
Cost is labeled `estimated` or `actual`; coverage states priced units over total units and names unpriced
usage. Daily-cap progress uses the same units and pricing snapshot as the estimate. A stale or missing
price cannot be presented as actual cost.

## Accessibility Floor

- WCAG 2.2 AA at every supported width, including phone width.
- At 320 CSS pixels, and at 400% browser zoom in a 1280 CSS-pixel viewport, content reflows to one column
  with no two-dimensional page scroll. Only genuine evidence tables, source code and logs may scroll on
  one axis inside a named region. At 200% text-only zoom, controls, labels, errors and status remain
  visible without clipping or overlap.
- All meaning appears in text. Status, trend and selected state never depend on color or chart shape.
- Landmarks and headings expose the five stable areas and the current page. Page title updates on navigation.
- Keyboard access reaches every operational action. `Tab` order follows reading order; skip navigation
  reaches main content; `Esc` closes the topmost temporary layer.
- Focus uses `{colors.control-border}` and `{colors.primary}` from `DESIGN.md` and remains visible in both themes.
- Modal/Drawer content has a visible and programmatic name, initial focus on the least destructive useful
  control, an inert background, trapped tab order, Escape/cancel, and focus restoration to its invoker.
- Run progress announcements are throttled to at most once every two seconds and only when the completed
  case count changes; streaming log lines are never live-region announcements. Save, completion, failure,
  cancellation and usage-cap stop announce once. AI refresh announces queued, current or terminal failure,
  not every retry.
- Determinate run progress uses native `<progress>` or `role="progressbar"` with accessible name,
  `aria-valuemin`, `aria-valuemax` and `aria-valuenow`, plus visible completed/eligible text. Unknown totals
  are explicitly indeterminate and never expose a fabricated percentage.
- Charts have adjacent text summaries and accessible tables containing the same measures, intervals,
  samples and windows.
- Logs expose pause-follow behavior, selectable text and a non-streaming preserved result after completion.
- Every pointer target is at least 44 by 44 CSS pixels at every width, including navigation, icon actions,
  dismiss controls and table-row actions. Hover-only actions are prohibited.
- Form errors are associated with their labels using the platform relationship, summarized above the
  form, announced once, and linked to the invalid field. Text-editor diagnostics include line/column,
  focus the first failing line from the summary, retain unsaved input and never rely on underline color.
- Live regions, logs and data refresh expose Pause/Resume. Pause stops visible replacement and
  announcements while evidence recording continues; Resume states how many updates were queued.
- Reduced Motion removes progress interpolation, card motion and refresh transitions without hiding state.
- Qualification records measured AA contrast ratios for every semantic light/dark token pair, interactive
  boundary and focus indicator against both adjacent colors as required by `DESIGN.md`; missing evidence
  is a failure, not `passed`.
- Command examples remain copyable. Dense data can scroll horizontally without trapping keyboard focus.

## Responsive & Platform

The Studio is a desktop-first responsive local web surface. Phone-width support is required by FR-77 and
AH-S315; reaching the loopback-only Studio from a separate phone remains the unresolved spike AH-SP018
(#1003), not an assumption of this UX.

| Width | Behavior |
|---|---|
| 1024px and wider | Horizontal navigation; Hub briefing and four report cards share the first row; drafts/runs and alerts share the next row. |
| 768px to 1023px | Briefing spans the width; report cards form a four-card row when space permits, then stack; lower panels become one column. |
| 481px to 767px | Navigation remains all five named areas and wraps into touch-safe rows; content is one column; report cards may use two columns only when each card preserves its content and 44px targets. |
| 320px to 480px | Every region and every report card is one column. Navigation may wrap but keeps all five labels. No page-level horizontal scroll. |

Comparison groups stack by target and suite on narrow screens; the verdict and interval stay above the
cases. Configuration metadata moves above its editor. Logs may scroll horizontally, but page chrome does
not. On first open, theme follows the operating-system preference. An explicit light or dark choice is
then persisted and overrides subsequent system changes until the developer returns to `Use system theme`.

## AI Health Overview (FR-85 / AD-32)

FR-85 defines the user-visible requirement and AD-32 defines its provider-neutral, read-only execution
boundary. The overview remains planned v0.18.0 behavior and never becomes an authority surface.

- **Opt-in:** no model call occurs before the developer enables the overview and accepts its cost policy.
- **Provider-neutral:** the Studio uses a replaceable adapter to the developer's configured development
  model or tool. It does not require a Studio-specific AI account and does not store provider credentials.
- **Read-only execution:** assessment runs as the AD-32 child in a scrubbed environment with no
  mutation-capable credentials, shell, Studio write route or action tool; an isolated working directory
  and filesystem; one allowlisted executable; immutable evidence on stdin; and bounded stdout, stderr,
  time and output size. It cannot delegate. Reusing a development model or tool does not reuse its powers.
- **Automatic refresh:** eligible evidence is a new immutable system-health, hook-performance, comparison,
  rule-health or usage snapshot. Changes coalesce for 60 seconds into one generation. The last-good
  assessment stays visible as `Stale: refreshing` until replacement. Polling is not the trigger.
- **Recovery:** transient failure retries at most three times with backoff within the adapter timeout. A
  retry stops when its new cap reservation cannot be made. Permanent failure, timeout, a refused retry
  reservation or the third failed retry ends as `Refresh failed`; the last-good assessment stays stale
  with the literal reason. The developer can cancel an active generation. Manual refresh is always
  visible; at the cap it returns `refused` without a model call.
- **Single flight and cap:** the flight identity includes integration and account ID, provider,
  adapter/executable/model versions, prompt-template and output-schema versions, generation parameters
  and the immutable evidence digest. Concurrent automatic or manual callers with that identity join the
  same flight. Before every initial attempt and every retry, the system atomically reserves that attempt's
  worst-case usage or spend in the authoritative integration/day cap ledger. Afterward it settles actual
  usage or spend and releases the remainder. Reservations are never shared across attempts; a retry does
  not launch when its reservation cannot be made.
- **Budget:** AI settings show daily cap and model/tool/pricing provenance. Spend is `estimated` or `actual`
  with currency, units, priced coverage and unpriced usage. At the cap or a usage limit, no automatic call
  starts and the prior assessment remains stale with the reason.
- **Claim-level provenance:** every generated claim links to immutable report snapshot ID and field path,
  then to native result or ledger IDs, digests and observation times. Provider/model/tool versions,
  prompt-template and output-schema versions/digests, generation time and evidence window apply to the
  assessment; partial/failed/unknown evidence applies to each affected claim.
- **Authority:** the overview is advisory. It cannot apply a proposal, change a stance, dismiss an alert or
  rewrite evidence. Investigation rows are labeled `Generated suggestion`, visually and semantically
  distinct from authoritative alerts, and cannot set alert severity or state. They only deep-link into
  Reports or Configure.

The same single-flight and cap gate applies to manual refresh. At a cap, usage limit or unavailable
provider no call starts; the last-good assessment remains stale with the literal reason.

## Alerts and Notifications

Alerts mean a current condition needs attention; notifications report that an event completed or state
changed. Both appear on the Hub with timestamps and direct links to the underlying report, draft, run or
activity record. The deterministic source state decides whether an alert exists; the AI overview may
explain an alert but may not create, clear or grade authoritative alert state. AI investigation rows are
`Generated suggestion`, not alerts.

Notifications persist across Studio restarts. Repeated notifications with the same event type, subject
identity and evidence snapshot deduplicate into one row with occurrence count and first/last-seen times.
Read and dismiss are persisted per notification. Dismiss removes the row from the active Hub list but
never removes or mutates its immutable Activity event or source evidence. A genuinely new evidence
snapshot creates a new notification rather than silently reopening the dismissed one.

## Inspiration & Anti-patterns

- **Chosen direction, Briefing:** lead with interpretation and evidence, use horizontal navigation, and
  keep the Hub spacious enough for deliberate reading.
- **Chosen theme, Clear:** quiet teal, cool neutrals and restrained status color in matched light and dark
  themes; first open follows the system, then an explicit choice persists.
- **UI system, Mantine 9.6.3:** inherit `@mantine/core` and `@mantine/hooks`; the exact local deltas are
  enumerated in `DESIGN.md`, while lockfile and deterministic-build checks remain implementation gates.
- **Borrow from `claude plugin eval`:** verdict line, summary measures, then case runs with grader pass or
  fail and explanation.
- **Borrow from Harbor:** group comparison rows and columns by target and suite.
- **Add beyond the field:** pair every delta with its interval and say when it crosses zero.
- **Reject generic dashboard scoring:** no composite health score without a defined evidence standard.
- **Reject hosted-control-plane assumptions:** no remote telemetry backend or cloud account as a condition
  of using the Studio.
- **Reject AI authority:** the generated overview is an advisory reading of preserved evidence, never a
  decision record or automatic tuner.
- **Reject live editing:** editing the effective value directly without a named draft violates the product.

## Key Flows

The established journeys retain the PRD's UJ-1 to UJ-9 names. Studio flows use the source epic and FR names
verbatim.

### UJ-1. Riley evaluates the harness without surrendering their configuration.

1. Riley runs `citizen init`, selects both runtimes and leaves editor management off.
2. Riley runs `citizen sync --dry-run`.
3. The preview lists each link, render, path, native setting and conflict.
4. Riley inspects the one file they already own and chooses not to adopt it.
5. Riley applies the remaining safe actions.
6. **Climax:** `doctor` reports local activation and drift while `citizen compatibility` separately reports published qualification.

Failure: a conflict names its current owner and stops that action; adoption is never the default. The known
past-tense dry-run wording defect remains tracked under #634.

### UJ-2. Morgan changes one preference across both runtimes.

1. Morgan changes the `delegation` stance in user configuration.
2. Morgan runs `citizen stances` and inspects the resolved value.
3. Morgan syncs.
4. Morgan opens `citizen stances --json`.
5. **Climax:** one authored choice appears in both native formats with each adapter's `instruction` or `instruction-and-hook` coverage.

Failure: linked stance text remains user-level. When a project variant differs, the session-start hook
injects its text, or an explicit pointer when it does not fit the always-loaded budget; output never
claims the linked text itself changed.

### UJ-3. Sam finds out which of their rules actually fire.

1. Sam runs `ruleprobe` over transcripts with `--rules` pointing at their rule directory.
2. The report separates `measured`, `dark` and `unmeasured` rules.
3. Sam inspects hit rates and adds declarative detectors for two important unmeasured rules.
4. Sam runs the report again.
5. **Climax:** rules measured as never firing become evidence-backed deletion candidates, and the standing prefix can shrink.

Failure: a swallowed transcript or detector error is counted separately and leaves the rule unknown, not absent.

### UJ-4. Dana runs a parallel build under a cost posture.

1. Dana starts a parallel build whose briefs carry soft budgets.
2. The live usage feed prints each subagent's spend against its budget.
3. A one-time fresh-session nudge appears when the latest turn crosses the configured threshold.
4. Dana runs `citizen usage --by role`, then `--by day`.
5. **Climax:** Dana can attribute list-price-equivalent spend while unpriced rows remain counted in the footer.

Failure: partial pricing is labeled in the header and footer; no missing price is silently treated as zero.

### UJ-5. Casey contributes a fix.

1. Casey opens the story file linked from the issue Planning block.
2. Casey works in a managed worktree and updates the typed story evidence.
3. Casey runs the repository gate.
4. Casey opens one pull request that closes the issue.
5. **Climax:** `issue-ownership` passes because every required typed-story section is filled.

Failure: legacy stubs remain visible as incomplete until the #616 enrichment batches finish; a missing
section fails rather than being inferred.

### UJ-6. Lee layers the harness under a skill library (planned 0.16).

1. Lee selects the `superpowers` mode.
2. Lee previews the selection report.
3. The report separates mode keys, kept user values and shadowed keys.
4. Lee applies the selection.
5. **Climax:** enforcement hooks, cost routing and measurement remain on while process belongs to the library.

Failure: a conflicting floor is refused with the scope that set it if floor semantics are adopted.

### UJ-7. Jordan, the maintainer, cuts a release.

1. Jordan freezes the candidate on a release branch.
2. Jordan runs smoke, qualifies required targets once, and works the five release surfaces in order.
3. Jordan runs release preflight in a clean checkout.
4. Preflight compares catalog, evidence, `VERSION`, projection drift and GitHub About when authenticated.
5. **Climax:** each release surface is reported as done, skipped or unverified against one frozen commit.

Failure: missing authentication warns and leaves GitHub About unverified; it does not become passed.

### UJ-8. Kai keeps remote sessions alive through restarts.

1. Kai runs `citizen remote-control status`.
2. Status reports each host's launchd state, log path and heal state.
3. Kai sees active but disconnected sessions and the exact reconnect command.
4. Kai runs heal after a restart.
5. **Climax:** the host log proves reuse of the prior environment and heal reconnects recoverable sessions.

Failure: an already archived session stays out of recoverable status; heal never claims it.

### UJ-9. Ari tries a decision provider without risking a decision (planned 0.16).

1. Ari enables a provider at one decision point in `shadow`.
2. Ari runs `citizen decisions eval`.
3. The report shows agreement, calibration, cost and latency against the written criterion.
4. Ari inspects the evidence without changing the live decision path.
5. **Climax:** the point advances only if it meets its explicit criterion.

Failure: incomplete evidence leaves the stage at `shadow` and names what could not be measured.

### Studio Flow 1. AH-E022: Open the Studio: one local UI that shows the whole harness, live (Riley, returning developer)

1. Riley runs `citizen studio`; the launcher prints the loopback URL and opens it.
2. The launcher exchanges its one-time token for the protected Studio session.
3. Hub loads deterministic health, installed version, effective selection, report aggregates, active work,
   alerts and notifications from local sources.
4. Riley opens the effective selection link and sees each value's source layer in Configure.
5. Riley opens the hook-latency aggregate and reaches its pinned report snapshot, source IDs/digests and observation times.
6. Riley opens Activity and confirms what the harness decided and changed.
7. **Climax:** Riley can move from a Hub claim to the source layer, report and journal that prove it, with no model summary required.

Failure: anonymous API access receives 401; foreign Host or Origin receives 403; a lost local server leaves
the last screen read-only and stale. If the optional AI overview is disabled, Hub offers opt-in without
making a model call.

### Studio Flow 2. AH-E023: Tune in the Studio: drafts for every change, applied or rolled back through the governed path (Morgan, tuning a draft)

1. Morgan opens Configure and inspects the effective testing value and its repository layer.
2. Morgan selects `Change in draft`; Studio creates a named managed-worktree draft from the current base.
3. Morgan changes a stance variant in the structured form; 750 ms after the last change, Studio runs CLI checks and checkpoints it.
4. Morgan edits one rule in the text module-editor and inspects live lint, context budget and projection preview.
5. Morgan presses Cmd/Ctrl-S; Studio runs the same CLI checks and records the text checkpoint.
6. **Climax:** draft-workspace shows both checkpoints, three changes and `Nothing applied`, while live config, checkout and projections remain byte-for-byte unchanged.

Failure: a CLI-refused change is refused for the same reason; the error summary focuses the first failing
field or source line, and Morgan's editor content remains available to correct or discard.

### Studio Flow 3. AH-E023: Tune in the Studio: drafts for every change, applied or rolled back through the governed path (Morgan, applying and rolling back)

1. Morgan opens governed-action-review from the draft.
2. The review shows the diff, passing checks, source ownership effects, exact `citizen` commands and rollback path.
3. Morgan confirms Apply.
4. Studio runs the CLI locks, checks, ownership journal and decision log, then refreshes Configure and Activity.
5. Morgan sees an unexpected downstream effect and opens the applied change from Activity.
6. Morgan confirms one-step rollback.
7. **Climax:** every still-owned field/file returns to its recorded prior semantic value, byte-identical where no later user edit intervened, with apply and rollback both preserved in Activity.

Failure: a lock or check failure stops before mutation and says what remained unchanged. A core module is
forked into the developer's root or contribution branch and is never edited in place. If a current value
no longer matches what the harness applied, rollback refuses that conflict and preserves the later edit.

### Studio Flow 4. AH-E024: Run from the Studio: any suite or single test against the installed version, a release or a draft (Dana, measuring hook performance)

1. Dana opens Experiments, selects the hook performance suite and narrows it to one case.
2. Dana chooses the draft target; Studio shows the exact isolated profile built from that target.
3. For a paid run, the launch guard shows estimate and caps and waits for Dana's confirmation. A free local run says `No model usage`.
4. Dana starts the allowlisted run and watches semantic completed/eligible progress and the live log in run-console.
5. Dana pauses live updates while reading a case, then resumes and receives the queued-update count.
6. Dana cancels after enough evidence has accumulated.
7. **Climax:** the kept run record contains every finished case, spend, target provenance and native result file, while the live home is unchanged.

Failure: a cap or usage limit stops cleanly and keeps finished cases. A result-store rebuild reindexes the
native result files rather than inventing a new record.

### Studio Flow 5. AH-E025: Judge in the Studio: whether a change helped, against its baseline and its history (Sam, deciding whether a draft helped)

1. Sam opens Reports and chooses the draft run and its matched base run.
2. comparison-report verifies the match key and leads with the evidence-standard verdict plus metric-specific no-effect value.
3. Sam reads the delta beside its paired task-clustered interval and observed/eligible pair counts.
4. Sam groups cases by target and suite, then inspects each grader's pass/fail and explanation.
5. Sam opens the trend beside the proof set, then checks rule health, adherence, context cost and spend.
6. Sam opens an evidence-backed stance proposal and chooses `Try in draft`.
7. **Climax:** Sam judges the current comparison as `inconclusive` because the interval crosses zero, and the proposal changes only a draft.

Failure: if the draft, run, case set, grader, comparator, metric, tool/model, parameters or environment key
changes, the verdict becomes stale and identifies its snapshot. Unmatched parameters block a causal
verdict and offer a prefilled rerun with matched fields locked.

### Studio Flow 6. AH-E026: Adopt the Studio: from install to a tuned harness, for developers and their agents (Casey, new install)

1. Casey completes install and runs `citizen studio`.
2. First-run guide explains installed health, effective selection and the five stable Studio areas.
3. Casey creates a draft from a proposed preference change and sees the exact `citizen` equivalent.
4. Casey runs one free local case against the draft and its base.
5. Casey opens comparison-report and reviews the evidence and limitations.
6. Casey opens governed-action-review, sees checks and rollback, and confirms Apply.
7. **Climax:** Hub shows the applied selection, Activity shows the governed decision, and Casey can copy the same CLI sequence for an agent to run headless.

Failure: abandoning any step changes nothing live. A blocked check preserves the draft and offers the same
inspection path an agent receives from the CLI.

## Deferred (Non-blocking)

- **Separate-phone reach:** AH-SP018 (#1003) decides whether a phone can reach the loopback-only Studio.
  FR-77 still requires responsive phone-width behavior; this UX contract does not decide the network
  reach model, and the spike does not block the validated browser experience.

---
title: Model Citizen developer experience contract
status: final
created: 2026-09-23
updated: 2026-09-25
supersedes: ../ux-agent-harness-2026-09-19/EXPERIENCE.md
sources:
  - DESIGN.md
  - ../../prds/prd-agent-harness-2026-09-23/prd.md
---

> Current delivery assignments: [Flow availability — 2026-09-28](../../roadmap-2026-09-28.md). Earlier dates below remain historical.

# Foundation

The harness has three operational surfaces:
- **The local CLI and its reports.** The command is `citizen`; `harness` stays a supported alias.
- **The agent's own session:**
  - answers shaped by the `voice` stance;
  - plans in the runtime's native plan pane;
  - the live usage feed;
  - hook notices.
- **Landing and README copy,** generated from `product.json`.

Native runtimes consume the projections but keep their own authority over permissions and behaviour. Visual
presentation follows `DESIGN.md`. Operational truth comes from the CLI's state, the ledgers and the evidence
records. Anything marked *(planned)* describes a planned PRD requirement, not current behaviour.

# Information architecture

1. **Measure.** The standalone instrument shows which of a developer's rules fire, before anything is
   installed (UJ-3).
2. **Understand.** The README leads with that question. It then shows a real dry-run capture and the
   capability groups, with release status below install.
3. **Select.**
   - `citizen init` names the runtimes, a preset and the identity.
   - Stances select behaviour, and modes will too *(planned, v0.14.0)*.
4. **Preview.** A dry run lists every link, rendered file, setting and conflict before ownership changes.
5. **Apply.** Sync records what the harness owns and preserves unrelated state.
6. **Inspect.** Each command keeps one concern separate:
   - `doctor` reports local installation, activation and drift;
   - `compatibility` reports published qualification;
   - `stances` reports resolved policy, and `stances --json` reports each adapter's coverage;
   - `usage` reports measured behaviour.
7. **Recover.** Repeat sync, drift repair, upgrade, rollback and uninstall each explain which conflicts they
   preserved and which prior values they restored.

# Key flows

The journeys mirror PRD UJ-1 to UJ-9.

## UJ-1. Riley evaluates without surrendering configuration

Riley runs `citizen init`, selects both runtimes, leaves editor management off, and runs `citizen sync
--dry-run`. The dry run lists each proposed action by kind (link, render, path, native setting) and stops
at one file Riley owns.

**Confirmation:**
- The conflict is named with its current owner, and adoption is never the default.
- After applying, `doctor` reports local activation and drift, and `citizen compatibility` reports
  published qualification.

**Known gap:** under a dry run, some summary lines are worded in the past tense ("sync complete"). That
wording is a defect, tracked under #634.

## UJ-2. Morgan changes one preference across both runtimes

Morgan changes the `delegation` stance in the user configuration, reads `citizen stances`, and syncs.

**Confirmation:**
- One authored choice reaches both native formats.
- `citizen stances --json` shows each adapter's coverage: `instruction` or `instruction-and-hook`.

**Scope note:** a project-level selection changes what the hooks and `citizen stances` resolve, but sync
links user-level stance text only. Carrying a project selection into the linked text is open (#276).

## UJ-3. Sam finds out which rules fire

Sam runs `ruleprobe` over their transcripts with `--rules` pointing at their rule directory. The report
counts rules in three states:
- **measured:** the rule has a detector;
- **dark:** the rule has a recorded reason why no transcript can decide it;
- **unmeasured:** the rule has neither.

It also shows the hit rate of each measured rule. Sam adds a declarative detector for two important
unmeasured rules and runs the report again.

**Confirmation:** the rules measured as never firing are the obvious deletions, and the standing prefix
shrinks when they go.

## UJ-4. Dana runs a parallel build under a cost posture

Every brief carries its soft budget. The live usage feed prints each subagent's spend against its budget.
When the latest turn's context crosses a threshold, a one-time nudge suggests a fresh session.

**Confirmation:** `citizen usage --by role`, then `--by day`, shows where the spend went, as list-price
equivalents, with unpriced rows counted in the footer.

## UJ-5. Casey contributes a fix

Casey opens the story file linked from the issue's Planning block, and works in a managed worktree. Casey
runs the gate and opens one pull request that closes the issue and brings the story file up to date.

**Confirmation:** for a story in the typed format, the `issue-ownership` check passes only when the
required sections are filled.

**Current state:** the typed format and the check landed in #629. Most existing stories are still legacy
stubs until the enrichment batches under #616 finish.

## UJ-6. Lee layers the harness under a skill library (planned, v0.16.0)

Lee selects the `superpowers` mode. The selection report lists the mode keys applied and Lee's own values
kept, and names each shadowed key.

**Confirmation:** enforcement hooks, cost routing and measurement stay on, and process belongs to the
library.

## UJ-7. Jordan cuts a release

Jordan freezes the candidate on a release branch, runs the smoke tier, qualifies the required targets once,
then works the five release surfaces in order.

**Confirmation:** preflight passes only when all of the following hold:
- the compatibility catalog, its evidence and `VERSION` agree;
- the checkout is clean, with no projection drift;
- GitHub About matches `product.json`, if `gh` is authenticated (otherwise preflight warns).

Each release surface is reported as done, skipped or unverified.

## UJ-8. Kai keeps remote sessions alive

`citizen remote-control status` reports, for each workspace host:
- its launchd state and log path;
- the heal state;
- any sessions that are active but disconnected, with the command to reconnect them.

**Confirmation:** after a restart, the host's log says it is reusing its prior environment, and heal
reconnects dropped sessions. A session that the server has already archived is past recovery, so status
leaves it out and heal never claims it.

## UJ-9. Ari tries a decision provider in shadow (planned, v0.16.0)

Ari enables a provider at one decision point in the `shadow` stage, and runs `citizen decisions eval`.

**Confirmation:** the report shows agreement, calibration, cost and latency against the point's written
criterion. The point advances only when it meets that criterion.

# Voice and tone

- **Status language.** Short and literal, from the sets in `DESIGN.md`. Never collapse unknown into absent,
  or generated into verified.
- **Errors.** Say what was protected, then give the next inspection command.
- **Figures.** Every figure comes with its status and sample size.
- **Published copy.**
  - Follow the PRD's positioning, with the approved vocabulary.
  - Use no em dashes.
  - Claim no unmeasured saving.
  - Credit the field.

# Component patterns

- **The dry-run preview** lists each proposed action by kind, and names conflicts with their owner.
- **Conflict output** identifies the current owner and never recommends adoption as the default.
- **Compatibility output** prints each target with its catalog state, and each stance with its
  qualification state. The capability mode is in `stances --json`.
- **Usage output.**
  - Partial data is reported in the header and the footer.
  - Dollars are list-price equivalents.
  - `--by role` warns below 30 samples.
- **Hook notices.**
  - Each notice names the hook, the decision and the reason, on one line.
  - A cost notice informs and never blocks.
- **Planning links** use stable BMad IDs. The issue is the delivery authority, and the story file is the
  design record.
- **A refused relaxation** of a floor names the scope that set the floor *(planned, only if floor semantics
  are adopted; PRD §11 Q6)*.

# State patterns

- **Operations:** proposed, applied, unchanged, conflicted, skipped, failed. This is the target vocabulary.
  Today the dry run prints action kinds rather than these states.
- **Evidence:** `passed`, `failed`, `unverified`.
- **Measurement:** known, partial, unavailable, failed.
- **Decision stages:** `off`, `shadow`, `advise`, `act`.

Per-item failures are counted separately from empty results.

# Interaction primitives

- Commands are explicit and composable.
- Mutating operations have dry-run equivalents where practical.
- Repeated application is safe.
- Approval, verification and task state never transfer implicitly between sessions or runtimes.
- A headless session becomes a declared fact in the selection, and waiting stances then proceed *(planned,
  v0.14.0)*.

# Accessibility floor

- All meaning is present in text.
- Command examples remain copyable.
- Headings are navigable.
- Output works without animation or pointer interaction.
- Chat, plans and hook notices read on a narrow screen. The wide usage tables are the known exception
  (`DESIGN.md`).
- Future graphical surfaces must keep keyboard and screen-reader access to every operational action and
  status.

## Flow availability — 2026-09-28

Independent Studio flows arrive in 0.16 after the Measured engines land; Superpowers/composition and judge journeys formerly marked 0.16 move to 0.17; field/Codex and stance-proposal flows formerly marked 0.17 move to 0.18. Four-arm/layer-swap Studio launch is #989 in 0.17; factorial launch is #1065 and proposals #996 in 0.18. No visual or interaction redesign occurs here. #1062 records the future reconciliation of sampling, evidence labels, comparisons and stale states with landed Measured behavior.

See [the authoritative roadmap amendment](../../roadmap-2026-09-28.md) and #1061 for the issue-level moves, scope splits and added integration stories. These changes remain planned, not shipped.

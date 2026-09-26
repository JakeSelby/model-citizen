---
title: 'competitive research: Studio field check: edit, eval, compare UIs'
type: 'competitive'
topic: 'Studio field check: edit, eval, compare UIs'
decision: 'Is the Studio loop (edit an agent configuration in a local UI, run evals against the draft, compare with a baseline) differentiated, and what should the UX specification (#961) and the architecture decisions (#962) borrow or watch?'
source: 'process'
status: complete
preset: 'standard'
validation: 'normal'
claims_verified: 3
claims_unverified: 4
sources: 24
created: '2026-09-26'
updated: '2026-09-26'
---

# competitive research: Studio field check: edit, eval, compare UIs

**Decision this research serves:** whether the Studio's loop (#955 to #959) is differentiated,
and what the UX specification (#961) and the architecture decisions (#962) should borrow from
the nearest tools or watch.

**Material:** three notes produced on 2026-09-26 by three read-only research subagents in one
Claude Code session, reading primary sources (GitHub API, official docs) with about 11 web
searches [1][2][3]. The load-bearing claims were spot-checked against primary sources the same
day [4][5][6][7][8].

## Executive summary

**What to do.** Build the loop as planned. No tool checked ships all three legs for a coding
agent's own configuration, and the leg the Studio plans to do differently, paired intervals in
comparison, is one nobody in the eval field shows.

**Findings behind that answer.**

1. **Every tool has at most two of the three legs.** Coding-agent GUIs edit configuration and
   show cost, with no evals [2][12][13]. Eval platforms version, run and compare prompts, not an
   agent's rules and hooks [3]. The closest single pieces are Anthropic's `claude plugin eval`,
   which measures a plugin against a no-plugin baseline from the CLI [4], Harbor, which compares
   runs of real coding agents in a local viewer [6], and Agenta, which versions Claude Code and
   Codex agent configurations [7].
2. **`claude plugin eval` is the reference design for the judge leg, and it cannot measure a
   harness as installed.** Its sandbox loads no user settings, hooks or `CLAUDE.md` [4], so a
   change to them is invisible to it unless repackaged as a plugin.
3. **No comparison view checked shows uncertainty.** promptfoo, Harbor and `claude plugin eval`
   show point scores and deltas [4][6][8]; the other eval UIs document none [3]. Inspect carries
   standard errors in its metrics, but its viewer does not compare runs [15].

**The biggest caveat.** "Nothing ships the loop" and "nobody shows intervals" are absence claims.
They rest on documentation and READMEs, one search per claim found no counterexample, and both
stay unverified in the ledger.

## 1. What the nearest tools ship, leg by leg

**Harness class: dashboards, not a loop.** Burnd shows a local usage and waste dashboard [9].
claude-code-templates ships local analytics, chat and plugin views, with no rule editing and no
evals [10]. claude-flow's MetaHarness diffs two audits, from the CLI and MCP only [11]. The
remaining fourteen projects in the class show no UI in their READMEs [1].

**Coding-agent GUIs: edit and watch, never judge.**

- opcode edits `CLAUDE.md`, custom agents and MCP servers and charts cost; it keeps session
  checkpoints with diffs, not evals [12].
- CloudCLI writes MCP, permissions and project settings straight into the user's Claude Code
  directory, for Claude Code, Cursor CLI and Codex [13].
- Kiro hooks and Conductor run scripts can run tests on events, but neither scores or compares
  configurations [21][22].
- Goose's `goose bench` once ran evals over several configurations; its docs page is gone and
  its crate is absent from the current tree, so it is likely removed (unverified) [14].
- A one-star dashboard edits `CLAUDE.md` and hooks and benchmarks memory retrieval, not
  configuration efficacy [23].

**Eval UIs: the loop exists, for prompts.** Langfuse, Phoenix and Opik version prompts in the
UI, run them over datasets and compare experiments [16][19][20]. promptfoo edits and re-runs
from a local viewer and diffs two evals [8]. Braintrust aligns an experiment to a baseline with
per-row deltas and regressions [17]; LangSmith is similar, with a local Studio for LangGraph
agents only [24].

**The three pieces closest to the Studio.**

| Tool | Edit | Run | Compare | Gap |
|---|---|---|---|---|
| `claude plugin eval` [4] | none; cases are files | CLI, cases times runs, two arms | `WITH`, `W/OUT`, `Δ`, HTML report | No UI; plugin scope; user config absent |
| Harbor [5][6] | none; job configs | CLI | local viewer, jobs grouped by agent and model | No editor, no launch from the viewer |
| Agenta [7] | versioned agent configurations (`AGENTS.md`, skills, MCP) | its own sandboxes | "compare changes" | Eval linkage unconfirmed; hosts the agent rather than tuning a local install |

## 2. How comparison is reported

- **Deltas, not intervals.** `claude plugin eval` reports a with-minus-without `Δ` per case and a
  mean delta, and the delta never changes the exit code [4]. Harbor's compare route groups jobs
  with no uncertainty terms [6]. promptfoo diffs two evals [8]. Braintrust colours regressions
  green and red with no significance [17].
- **Noise is acknowledged but not quantified.** The plugin-eval docs warn that a single run is
  noisy and default to three runs per arm [4]; no view computes how much of a delta is noise.
- **Inspect is the exception in metrics, not in UI.** Its metrics carry standard errors, and its
  viewer neither launches nor compares [15].

## 3. Field movement to watch

- **Anthropic has a native with-and-without eval** [4]. If its scope grows from plugins to user
  configuration, it covers the judge leg natively.
- **Agenta has repositioned around coding-agent harnesses** [7]. An eval tab over its
  configuration versions would make it the nearest competitor.
- **Hosted eval surfaces are closing.** OpenAI's Evals dashboard goes read-only on 2026-10-31 and
  shuts down on 2026-11-30 (unverified) [18]; Crystal, Letta's ADE and AutoGen Studio are
  deprecated or in maintenance [2][3].
- **Licences.** Phoenix is Elastic License 2.0; promptfoo, Inspect and Langfuse outside `ee/` are
  MIT; Opik and Harbor are Apache-2.0 [3]. Any code borrowed rather than an idea needs a licence
  review first.

## Cross-dimension insights

- **The gap is the join, not any single leg.** Editing, running and comparing each exist
  somewhere [4][6][7][12]; what is missing is one local surface whose edit is the thing
  measured. That argues the Studio's differentiator is the draft-to-verdict path (#973, #991),
  not any one screen.
- **Isolation cuts both ways.** Plugin eval gets clean baselines by loading nothing of the user's
  [4]; the Studio's targets and isolated profiles (#984) must load exactly the draft under test,
  or its comparison inherits the same blind spot.

## Contrary evidence

The red-team pass was off at `normal` validation. The strongest counter from the sources: Agenta
already versions coding-agent configurations and has a documented evaluation feature for its
earlier product [3], though its README does not connect the two [7]. If the two meet, a hosted team tool would cover most of the loop, and the
Studio's remaining edge would be local-first operation and the governed apply path.

## Recommendations

1. **UX specification (#961): borrow `claude plugin eval`'s report shape.** A verdict line
   ("+N points against baseline, improved, flat, regressed"), summary tiles, then per-case runs
   with each grader's pass or fail and its explanation [4]. Confidence: high.
2. **UX specification (#961): show the interval next to every delta, and say when it crosses
   zero.** This is the one comparison feature the field lacks [3][4][6][8]. Confidence: medium,
   since the absence rests partly on one import.
3. **UX specification (#961): borrow Harbor's row and column grouping for run comparison**, by
   target and by suite [6]. Confidence: high.
4. **Architecture decisions (#962): record that a draft is measured as installed.** A target must
   load the draft's rules, hooks and settings, the opposite of plugin eval's sandbox [4].
   Confidence: high.
5. **Architecture decisions (#962): keep a machine-readable result file per run**, versioned like
   `aggregate-result.json` [4], so an agent and CI read the same verdict the UI shows (#998).
   Confidence: high.
6. **Watch:** Anthropic's plugin eval scope and Agenta's eval linkage, re-checked at the staleness
   date below [4][7]. Confidence: medium.

## Open questions

- **Does Agenta evaluate its agent configuration versions?** Its README does not say [7]; one
  read of its evaluation docs against the agent product would settle it.
- **Does any hosted eval UI compute intervals behind a paid tier?** Only public docs were read
  [3]; a trial account would settle it for Braintrust and LangSmith.
- **Is `goose bench` removed or moved?** [14]; one read of the Goose changelog would settle it.
- **Can `claude plugin eval` target a user configuration packaged as a plugin?** The docs cover
  plugins and skills [4]; one native test would say whether the Studio could delegate to it.

## Source appendix

| # | Supports | Publisher | Pub date | Accessed | Confidence |
|---|---|---|---|---|---|
| [1] | Harness-class survey; no loop in the class | Model Citizen field check, imports/harness-class.md | 2026-09-26 | 2026-09-26 | medium |
| [2] | Coding-agent GUI survey; deprecations | Model Citizen field check, imports/agent-guis.md | 2026-09-26 | 2026-09-26 | medium |
| [3] | Eval UI survey; no intervals; licences | Model Citizen field check, imports/eval-uis.md | 2026-09-26 | 2026-09-26 | medium |
| [4] | With and without arms, report shape, sandbox, noise warning | [Anthropic plugin-evals docs](https://code.claude.com/docs/en/plugin-evals) | 2026-09-26 | 2026-09-26 | high |
| [5] | Installed coding agents, `harbor view` | [Harbor repository](https://github.com/harbor-framework/harbor) | 2026-09-26 | 2026-09-26 | high |
| [6] | Compare route, grouping by agent and model, no uncertainty terms | [Harbor `compare.tsx`](https://github.com/harbor-framework/harbor/blob/main/apps/viewer/app/routes/compare.tsx) | 2026-09-26 | 2026-09-26 | high |
| [7] | Agent configuration versions, harnesses, no eval mention | [Agenta README](https://github.com/agenta-ai/agenta) | 2026-09-26 | 2026-09-26 | high |
| [8] | Diff against another eval, no intervals | [promptfoo web viewer docs](https://github.com/promptfoo/promptfoo/blob/main/site/docs/usage/web-ui.md) | 2026-09-26 | 2026-09-26 | high |
| [9] | Burnd dashboard | [Burnd README](https://github.com/garvitsurana271/burnd) | 2026-09-26 | 2026-09-26 | medium |
| [10] | claude-code-templates local views | [claude-code-templates README](https://github.com/davila7/claude-code-templates) | 2026-09-26 | 2026-09-26 | medium |
| [11] | MetaHarness audit diff | [claude-flow](https://github.com/ruvnet/claude-flow) | 2026-09-26 | 2026-09-26 | medium |
| [12] | opcode editing and cost | [opcode README](https://github.com/getAsterisk/opcode) | 2026-09-26 | 2026-09-26 | medium |
| [13] | CloudCLI writes settings | [CloudCLI README](https://github.com/siteboon/claudecodeui) | 2026-09-26 | 2026-09-26 | medium |
| [14] | `goose bench` likely removed | [Goose docs](https://block.github.io/goose) | 2026-09-26 | 2026-09-26 | low |
| [15] | Inspect stderr, viewer does not compare | [Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai) | 2026-09-26 | 2026-09-26 | medium |
| [16] | Langfuse prompt experiments | [Langfuse](https://github.com/langfuse/langfuse) | 2026-09-26 | 2026-09-26 | medium |
| [17] | Braintrust baseline deltas | [Braintrust](https://www.braintrust.dev) | 2026-09-26 | 2026-09-26 | medium |
| [18] | Evals dashboard shutdown dates | OpenAI docs, URL not recorded in imports/eval-uis.md | 2026-09-26 | 2026-09-26 | medium |
| [19] | Phoenix experiments | [Arize Phoenix](https://github.com/Arize-ai/phoenix) | 2026-09-26 | 2026-09-26 | medium |
| [20] | Opik prompt library and playground | [Opik](https://github.com/comet-ml/opik) | 2026-09-26 | 2026-09-26 | medium |
| [21] | Kiro hooks run tests | [Kiro hooks docs](https://kiro.dev/docs/hooks) | 2026-09-26 | 2026-09-26 | medium |
| [22] | Conductor run scripts (search snippets) | [Conductor docs](https://docs.conductor.build) | 2026-09-26 | 2026-09-26 | low |
| [23] | claude-dashboard memory benchmark | [claude-dashboard](https://github.com/bunlongheng/claude-dashboard) | 2026-09-26 | 2026-09-26 | medium |
| [24] | LangSmith compare view, local Studio | [LangSmith docs](https://docs.langchain.com) | 2026-09-26 | 2026-09-26 | medium |

## Staleness map

Computed with `recon_kit.py staleness`, using the pack's windows: capability 3 months,
lifecycle 3, licensing 6. No claim is stale today.

- **2026-12-26:** every capability claim (the loop, Harbor, Agenta, plugin eval, intervals,
  `goose bench`) and the OpenAI shutdown dates.
- **2027-03-26:** the eval-UI licences.

The earliest re-check is **2026-12-26**. Re-check plugin eval and Agenta sooner if either ships
a release that touches user configuration or evaluation.

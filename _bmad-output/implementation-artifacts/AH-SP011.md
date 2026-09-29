---
bmad_id: "AH-SP011"
type: "spike"
title: "Spike: one-week trial of an earlier auto-compact window"
lifecycle: "active"
provenance: "authored"
github_issue: 751
github_issue_url: "https://github.com/JakeSelby/agent-harness/issues/751"
parent_bmad_id: "AH-E018"
parent_github_issue: 745
updated: "2026-09-23"
---

# AH-SP011 — Spike: one-week trial of an earlier auto-compact window

<!-- bmad-sync:begin -->
- **GitHub issue:** [#751](https://github.com/JakeSelby/agent-harness/issues/751)
- **Primary parent:** [AH-E018](https://github.com/JakeSelby/agent-harness/issues/745)
- **State:** active

The issue carries the summary, discussion and acceptance evidence; this file carries the design.

This work item was authored as part of the repository's committed BMad planning system.
<!-- bmad-sync:end -->

## Delivery amendment — 2026-09-28 (#1061)

Current delivery: **v0.18.0**. Earlier milestone references below are historical and superseded by [the roadmap amendment](../planning-artifacts/roadmap-2026-09-28.md). Capability status is unchanged.


## Question

Does compacting at 400,000 tokens, instead of at the model's context limit, cut spend per merged pull
request without hurting the work?

What waits on it: whether the harness recommends an earlier auto-compact window, in a cost variant or in
its docs. The premise: a rebuild re-writes roughly the whole context and every call re-reads it, so a
smaller context makes both cheaper. The price is more compactions, each a rewrite of its own, and the
detail a summary drops ([#745](https://github.com/JakeSelby/agent-harness/issues/745)).

## Experiment

1. **Precondition.** Confirm the sessions run with a context window above 400,000 tokens. Claude Code caps
   the effective window at the model's own, so below that the variable changes nothing, and the spike
   ends there.
2. **Baseline.** Before changing anything, save the output of `harness usage --by rebuild --days 7` and
   list the pull requests merged in those seven days. The report takes only a trailing window, so the
   week before the trial cannot be reported once the trial has run.
3. Set `CLAUDE_CODE_AUTO_COMPACT_WINDOW=400000` in the `env` block of the user-scope Claude Code settings
   for seven days. It takes a plain integer from 100,000 to 1,000,000, and wins over the `/autocompact`
   command, the `--autocompact` flag and the `autoCompactWindow` setting
   [Source: https://code.claude.com/docs/en/env-vars].
4. At the end of the seven days, run `harness usage --by rebuild --days 7` again and compare it, and spend
   per merged pull request, with the saved baseline.
5. The maintainer notes any session that lost the thread after a compaction.
6. Keep the cold-resume guard's switch unchanged across both windows, so that two changes are not
   measured as one.

The two weeks also differ in workload. Spend per merged pull request corrects for that only in part, so
the result is read as a direction, not a precise saving.

## Exit criterion

Fixed before the run: keep the setting if spend per merged pull request falls by **10% or more** against
the prior seven days, with no regression the maintainer reports. Otherwise revert it and record why.
`[ASSUMPTION: spend per merged pull request is computed from harness usage and the pull requests merged in the same window; no command reports it today]`

## Result

Not yet run.

## Decision

Open. Running the trial changes settings on the maintainer's machine, so it starts only on the
maintainer's go. A keep opens a follow-up on whether a cost variant should carry the window; a revert
closes the question, with the reason on #751.

## Dev notes

- **Bound requirements:** FR-28 Cost postures, for any recommendation that follows.
- **Bound decisions:** AD-7 What a hook may and may not do: no design may trigger compaction from a hook,
  so a setting is the only lever. AD-12 Measurement invariants: one variable changes between the windows.
- **Needs:** AH-S227's report, for step 3.
- **Where the setting goes:** the user-scope settings file, which `harness sync` also writes.
  `[ASSUMPTION: sync leaves an env key it does not own in place; check before the trial starts]`
- **References:**
  - [Source: https://code.claude.com/docs/en/env-vars]
  - [Source: https://code.claude.com/docs/en/model-config]
  - [Source: https://github.com/JakeSelby/agent-harness/issues/751]

## Change log

- 2026-09-23: written at filing from the approved plan and the environment-variable reference.
- 2026-09-25: the baseline report is saved before the setting changes, because `harness usage --by rebuild` takes only a trailing window (review finding on #752).

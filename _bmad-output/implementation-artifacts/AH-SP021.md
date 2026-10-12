---
bmad_id: "AH-SP021"
type: "spike"
title: "Spike: derive setting periods from usage rows"
lifecycle: "active"
provenance: "authored"
github_issue: 1319
github_issue_url: "https://github.com/JakeSelby/model-citizen/issues/1319"
parent_bmad_id: "AH-E029"
parent_github_issue: 1318
updated: "2026-10-12"
---

# AH-SP021 — Spike: derive setting periods from usage rows

<!-- bmad-sync:begin -->
- **GitHub issue:** [#1319](https://github.com/JakeSelby/model-citizen/issues/1319)
- **Primary parent:** [AH-E029](https://github.com/JakeSelby/model-citizen/issues/1318)
- **State:** active

The issue carries the summary, discussion and acceptance evidence; this file carries the design.

This work item was authored as part of the repository's committed BMad planning system.
<!-- bmad-sync:end -->

## Question

Can usage rows' `stances` and `profile_fingerprint` (`policy/hooks/usage-log.py:1284-1288`), joined with `applies.jsonl` (`lib/harness_core/studio/apply.py:742`), give each Harness item a list of periods with the setting in effect and its dates?

## Experiment

- Merge `origin/main` into `feature/v0.18.0-studio` in the `studio-build` worktree first and run the gate.
- A scratch script computes periods over 30 days of `usage.jsonl` on the maintainer's Mac.
- Record the run time and, per surface (Rules, Stances, Hooks, Roles, Workflows, Sources), the boundaries found and the setting each one attributes.

## Exit criterion

Under 2 s, and real boundaries on at least three of Rules, Stances, Hooks, Roles, Workflows and Sources. A fingerprint change with no attributable setting counts as noise, not as a boundary.

If it fails: fall back to `applies.jsonl` plus `git log` of the config, and Efficacy shows one period with a note.

## Result

Not run.

## Decision

Pending the result. A pass unblocks AH-S369; a fail switches AH-S369 to the fallback.

## Dev notes

- Exploratory and on the host profile by design: it measures data availability, not cost or efficacy, so it is never cited as evidence.
- [Source: policy/hooks/usage-log.py#L1284-L1288]
- [Source: lib/harness_core/studio/apply.py#L742] (on `feature/v0.18.0-studio`)
- [Source: .agent-harness/plans/studio-redesign.md#step-2--sync-and-periods-spike]

## Change log

- 2026-10-12: written at filing (#1317).

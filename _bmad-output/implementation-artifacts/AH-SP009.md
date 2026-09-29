---
bmad_id: "AH-SP009"
type: "spike"
title: "Does a headless superpowers session finish a task beside the harness?"
lifecycle: "active"
provenance: "authored"
github_issue: 553
github_issue_url: "https://github.com/JakeSelby/agent-harness/issues/553"
parent_bmad_id: "AH-E011"
parent_github_issue: 552
updated: "2026-09-23"
---

# AH-SP009 — Does a headless superpowers session finish a task beside the harness?

<!-- bmad-sync:begin -->
- **GitHub issue:** [#553](https://github.com/JakeSelby/agent-harness/issues/553)
- **Primary parent:** [AH-E011](https://github.com/JakeSelby/agent-harness/issues/552)
- **State:** active

The issue carries the summary, discussion and acceptance evidence; this file carries the design.

This work item was authored as part of the repository's committed BMad planning system.
<!-- bmad-sync:end -->

## Delivery amendment — 2026-09-28 (#1061)

Current delivery: **v0.17.0**. Earlier milestone references below are historical and superseded by [the roadmap amendment](../planning-artifacts/roadmap-2026-09-28.md). Capability status is unchanged.

Scheduled first in v0.17.0 Composable, before #800/#558/#1018. At implementation, replace the historical disposable-host-profile experiment with the current containerized evidence protocol and licensing checks. This planning update runs no experiment.


## Question

Does a headless superpowers session finish a pinned task beside the harness, or does it stall at its
own brainstorm gate when no user is present?

What waits on it, as the issue records: it blocks #558 (the `delegated` stance variants, the
`superpowers` mode and detection) and the live set (#560), and "the preamble decision feeds the arms
story" (#559, replay arms as built profiles). The epic #552 lists it first in dependency order [Source: #552].
[Source: https://github.com/JakeSelby/agent-harness/issues/553]

## Experiment

As written in the issue:

1. Make one throwaway `CLAUDE_CONFIG_DIR`, copied from the benchmark seed profile.
2. Run `bin/harness sync` under it.
3. Install `superpowers@claude-plugins-official` at user scope, non-interactively.
4. Run one pinned task from `benchmarks/tasks.json` headless, with stream-json output, a
   `--max-budget-usd 2` cap and `--strict-mcp-config`.
5. Run once as written. If it ends on a question, run once more with a one-sentence preamble stating
   that no user is present and that the agent must choose and proceed.

The baseline is the same run without the preamble; the variable is the preamble alone.
[ASSUMPTION: the issue names no separate baseline arm; the first, unmodified run serves as one.]

## Exit criterion

The issue fixes a scored result read from the stream, not an impression. Record from the run:

- the `init` event's `skills` (expected: `superpowers:*`) and `agents` (expected: the band workers);
- a PreToolUse row in the decision log for that session;
- the final message;
- the superpowers version as the plugin list reports it, the CLI version, the model and the machine.

The spike passes its acceptance when:

1. **Given** both profiles installed, **when** the session starts, **then** both the plugin's skills and
   the harness agents appear in `init`, and a harness hook decision is logged.
2. **Given** the run, **when** it ends, **then** either it ends with a result, or the stall is
   reproduced twice and the preamble's effect is recorded.
3. **Given** the findings, **when** they are written, **then** they are kept with the harness's local
   spike records, and the preamble decision feeds the arms story.

The criterion is qualitative (ends with a result or not) rather than a numeric threshold; the issue
records no number.

## Result

Not yet run. No pull request or comment on the issue records a run.

## Decision

Open: the run's outcome decides whether the replay arms (#559) and the live set (#560) carry the
no-user preamble, and informs how #558 makes "no user present" a session fact. The PRD's FR-16 already
states the planned behaviour: in a headless session, `plan-ceremony` and the `delegated` variants
proceed instead of waiting for a go-ahead. [ASSUMPTION: that FR-16 consequence is the design this
spike was meant to inform; the PRD does not cite the spike.]

## Dev notes

- **Bound requirements:** FR-16, Modes (the `superpowers` mode and headless "no user present"
  behaviour); FR-58, Per-rule regression and the four-arm benchmark (the arms the result feeds).
- **Bound decisions:** AD-12, Measurement invariants: arms differ only by environment, and each fence is
  proved before scoring; a throwaway config directory is the fence this spike uses.
- **References:**
  - [Source: https://github.com/JakeSelby/agent-harness/issues/553]
  - [Source: https://github.com/JakeSelby/agent-harness/issues/552]
  - [Source: benchmarks/tasks.json]
  - [Source: _bmad-output/planning-artifacts/prds/prd-agent-harness-2026-09-23/prd.md#FR-16]

## Change log

- 2026-09-23: written from the issue and pull-request record.
- 2026-09-23: corrected after sample review: explicit sources for claims taken from the epic or a sibling
  issue, and the fit of each binding and current-state claim rechecked against the record.

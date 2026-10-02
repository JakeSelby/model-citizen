---
bmad_id: "AH-SP010"
type: "spike"
title: "Spike: does a UserPromptSubmit block hold the prompt in the CLI and VS Code?"
lifecycle: "active"
provenance: "authored"
github_issue: 747
github_issue_url: "https://github.com/JakeSelby/agent-harness/issues/747"
parent_bmad_id: "AH-E018"
parent_github_issue: 745
updated: "2026-09-23"
---

# AH-SP010 — Spike: does a UserPromptSubmit block hold the prompt in the CLI and VS Code?

<!-- bmad-sync:begin -->
- **GitHub issue:** [#747](https://github.com/JakeSelby/agent-harness/issues/747)
- **Primary parent:** [AH-E018](https://github.com/JakeSelby/agent-harness/issues/745)
- **State:** active

The issue carries the summary, discussion and acceptance evidence; this file carries the design.

This work item was authored as part of the repository's committed BMad planning system.
<!-- bmad-sync:end -->

## Delivery amendment — 2026-09-28 (#1061)

Current delivery: **v0.18.0**. Earlier milestone references below are historical and superseded by [the roadmap amendment](../planning-artifacts/roadmap-2026-09-28.md). Capability status is unchanged.


## Question

Does a `UserPromptSubmit` hook that returns `{"decision": "block", "reason": ...}` stop the prompt before
any request reaches the model, in both the Claude Code CLI and the VS Code extension?

What waits on it: the cold-resume guard, AH-S229 (#750), sets hold or warn per client from the answer.
Idle rebuilds were 10.7% of long-session spend over the 30 days to 2026-09-23, and most long-session
spend runs in the VS Code extension, so the extension's answer matters most
([#745](https://github.com/JakeSelby/agent-harness/issues/745)).

**Documented, not verified here.** The hooks reference says a block "prevents the prompt from being
processed and erases it from context", and that the block message shown to the user carries the original
prompt unless `suppressOriginalPrompt` is set [Source: https://code.claude.com/docs/en/hooks]. The
harness has never probed a prompt-submit block in either client, and AD-7 lets the event table change
only after such a probe.

## Experiment

A throwaway hook that blocks once, in a scratch project on a machine with the harness installed:

1. Register a project-scope `UserPromptSubmit` command hook. While a marker file is absent, it writes the
   marker, appends the block decision it is about to print to a log file, and prints
   `{"decision": "block", "reason": "spike: held once"}`; once the marker exists, it prints nothing.
2. **Blocked session, CLI.** Start a session, send one short prompt, record what the client shows and
   whether the prompt text is offered back, then exit without resending.
3. **Baseline session, CLI.** With the marker present, start a session, send the same prompt and exit.
4. Delete the marker and repeat steps 2 and 3 in the VS Code extension.
5. For each session, read its transcript for an assistant entry and `harness usage` for its row.
6. Record the client versions, the machine and the date.

Each prompt runs in its own session because the usage ledger writes one row per session, at SessionEnd.
The baseline sessions must show an assistant turn and a usage row; otherwise the absence of both in a
blocked session proves nothing.

## Exit criterion

Fixed before the run, per client: the blocked session has **zero** assistant entries and **no** usage row
carrying model tokens, and the baseline session has at least one of each. A blocked session counts only
when the marker and the log entry show that the hook ran and emitted the block; without both, the run is
void and repeats, and is never scored as a failure. A client that passes gets the hold; a client that fails
gets a warn-only guard in AH-S229.

Whether the prompt text is offered back is recorded but does not decide the exit: AH-S229 saves the held
prompt for the next session either way.

## Result

Not yet run.

## Decision

Open. The result sets AH-S229's behaviour per client. A client that does not hold is declared in the
`limitations` of `adapters/claude-code/capabilities.json`, the way the Codex adapter declares its
uncovered feed events (AD-7).

## Dev notes

- **Bound decisions:** AD-7 What a hook may and may not do, for the probe and the declared gap. AD-12
  Measurement invariants: the scratch hook stays in a throwaway project, never in the harness's own
  settings.
- **Bound requirements:** FR-32 Live usage feed and fresh-session nudge; the prompt-submit event is the one
  the guard extends.
- **Setup:** no harness change. The usage row comes from the harness's SessionEnd hook, so the probe runs
  where the harness is installed.
- **Cost:** one model turn per client, in the baseline sessions.
- **References:**
  - [Source: https://code.claude.com/docs/en/hooks]
  - [Source: https://github.com/JakeSelby/agent-harness/issues/747]
  - [Source: lib/harness_core/lifecycle.py]
  - [Source: adapters/codex/capabilities.json]
  - [Source: policy/hooks/usage-log.py]

## Change log

- 2026-09-23: written at filing from the approved plan and the hooks reference.
- 2026-09-25: the hook now logs the block it emits, and a blocked session counts only when the marker and the log show the hook ran, so a client that never invoked it is not scored as failing (review finding on #752).

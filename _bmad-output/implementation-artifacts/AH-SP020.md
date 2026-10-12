---
bmad_id: "AH-SP020"
type: "spike"
title: "Spike: native auto-compaction at a lowered window, headless, with PreCompact and SessionStart(compact)"
lifecycle: "active"
provenance: "authored"
github_issue: 1291
github_issue_url: "https://github.com/JakeSelby/model-citizen/issues/1291"
parent_bmad_id: "AH-E028"
parent_github_issue: 1290
updated: "2026-10-12"
---

# AH-SP020 — Spike: native auto-compaction at a lowered window, headless, with PreCompact and SessionStart(compact)

<!-- bmad-sync:begin -->
- **GitHub issue:** [#1291](https://github.com/JakeSelby/model-citizen/issues/1291)
- **Primary parent:** [AH-E028](https://github.com/JakeSelby/model-citizen/issues/1290)
- **State:** active

The issue carries the summary, discussion and acceptance evidence; this file carries the design.

This work item was authored as part of the repository's committed BMad planning system.
<!-- bmad-sync:end -->

## Question

Does Claude Code's native auto-compaction, set to a lowered window, fire headless with PreCompact and SessionStart(`compact`) hooks and let the same `claude -p` turn continue? The whole epic's design waits on it.

## Experiment

The spike contract: Claude Code CLI as in the qualification targets, one throwaway container with an empty profile and the harness synced.

- Settings `env`: `CLAUDE_CODE_AUTO_COMPACT_WINDOW=100000`, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=40`, and `HARNESS_HANDOFF_BLOCK=on`, because headless runs skip the block today (`handoff_enabled()` in `policy/hooks/usage-feed.py`).
- Install a logging-only PreCompact hook and a SessionStart hook that echoes its `source` and emits `additionalContext` naming a marker string.
- Task: a ten-item checklist whose items each read a large fixture file, so context passes 40k by item four.
- Record whether: (a) compaction fires and at what context size; (b) PreCompact receives `trigger: auto`; (c) SessionStart fires with `source: compact` and the marker reaches the model, which it shows by quoting it; (d) the agent finishes the checklist in the same `-p` invocation.
- Also record whether the env var is read from settings `env` or only from the process environment, and the observed default trigger percentage.

## Exit criterion

All four of (a) to (d) hold. If (d) fails, compaction does not continue headless and the plan returns for review. If (c) fails, the reseed moves to the Stop hook's re-entry.

## Result

Run on 2026-10-12, exploratory, on the maintainer's Mac in the `model-citizen-arm-bare` image (Claude Code 2.1.280, model `claude-sonnet-5`), headless `claude -p` with an empty profile. The settings `env` held `CLAUDE_CODE_AUTO_COMPACT_WINDOW=100000` and `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70`, with logging-only PreCompact and SessionStart hooks. The task was a twenty-item checklist, each item reading a file of about 4,000 tokens.

- **(a) Compaction fires, and settings `env` is honoured:** yes. It fired six times, at 56,353 to 57,924 tokens, each falling to 2,801 to 4,197. The trigger is about the percentage of the window less a reserve of roughly 19,000 tokens (0.70 × (100,000 − 19,300) ≈ 56,500), not the percentage of the window. The projection has to account for that reserve.
- **(b) PreCompact receives `trigger: auto`:** yes, all six times. Its input keys: `custom_instructions`, `cwd`, `hook_event_name`, `prompt_id`, `session_id`, `transcript_path`, `trigger`.
- **(c) SessionStart fires with `source: compact`, and its `additionalContext` reaches the model:** yes, six times. The model wrote the reseed marker to its output file.
- **(d) The agent finishes in the same invocation:** yes. All twenty items were written once each, with no duplicates and no skips, and the run ended `DONE` after 48 turns at $1.70.

Exit criterion met. Three failure modes seen in the earlier attempts belong in the eval:

- Claude Code stops a run with "Autocompact is thrashing" when the context refills to the limit within three turns of a compaction three times in a row. This happened with files of about 17,000 tokens against a 40,000 point.
- Once, the compaction turn treated its own summary instruction as an injected instruction, and the run then ended on a model refusal.
- A fixture of random code words drew a model refusal before any compaction, so eval fixtures use plain prose.

## Decision

Pending the result. A pass unblocks AH-S363 to AH-S365 and fixes which env pair AH-S363 projects on a 1M model.

## Dev notes

- PreCompact can block with exit 2 but cannot supply the summary, so the harness reseeds through SessionStart instead of shaping the summary.
- Runs follow the controlled-experiments rule: fresh container, each arm exactly its named components.
- [Source: https://code.claude.com/docs/en/hooks] (PreCompact and SessionStart sections)
- [Source: https://code.claude.com/docs/en/env-vars]
- [Source: policy/hooks/usage-feed.py#handoff_enabled]
- [Source: .agent-harness/plans/2026-10-12-context-reset-and-continue.md#step-1--spike-the-native-mechanics]

## Change log

- 2026-10-12: written at filing (#1289).

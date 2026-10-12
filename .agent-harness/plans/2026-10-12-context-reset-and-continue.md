# Sessions that reset their own context and keep working

> Past the cost stance's threshold, Claude Code's own auto-compaction fires at the harness's number
> instead of near 1M, and the harness reseeds the fresh context from the task store, so the agent
> carries on in the same turn without a new session. An eval suite proves it does that without
> losing state, and it ships in v0.16.0.
> **Changed this round.** Moved from v0.15.0 to v0.16.0 so v0.15.0 releases without it.
> Effort: about 6 PRs · Risk: medium (compaction quality) · Blast radius: every Claude Code session

## At a glance

- **Outcome** — no "start a fresh session" prompt again; a long session resets itself and keeps going.
- **Approach** — set the native compaction trigger to `context_reset_at`, refresh the handoff just before it, reseed the context after it.
- **Touches** — cost stance sidecars and text, `citizen sync`, `usage-feed`, `harness-session`, a new PreCompact hook, `/land`, `/close-out`, cache-hygiene rule and detector, docs, benchmarks.
- **New deps** — none.
- **Not in scope** — Codex (it has no usage feed; a follow-up story on v0.16.0), clearing context mid-turn on desktop.
- **Exit test** — a real `claude -p` session runs past a lowered reset point, resets, and finishes its checklist with no human turn, and the behavioural eval meets its pre-registered floor.
- **Open question** — whether 400k is still the right reset point now that a reset costs no human time (decision 2).

## System design

```text
 cost stance ── context_reset_at ──▶ *citizen sync ── env ──▶ Claude Code settings
                                                            │
 turn ends at 90% of reset ──▶ *usage-feed Stop block ──▶ task store + progress.md
                                                            │
 context hits reset ──▶ native auto-compaction ──▶ *PreCompact: fallback snapshot
                                                            │
                            *SessionStart(compact) ── task store, progress,
                                                      snapshot, "continue" ──▶ agent
 /land, /close-out ──▶ *refresh handoff ┄┄▶ desktop: clear this session (idle)
```
`*` marks new or changed. The agent keeps the same turn and session; only its context is replaced.

## Steps

1. **[Spike the native mechanics](#step-1--spike-the-native-mechanics)** — one container, `claude -p`, window 100k at 40%.
   *Exit:* the transcript shows auto-compaction near 40k, PreCompact input, SessionStart `compact` context injected, and the turn continuing.
2. **[File the epic and stories on v0.16.0](#step-2--file-the-epic-and-stories)** — absorbs #749, supersedes #751.
   *Exit:* `bmad_issue_sync.py audit` and `sprint-status --check` clean; #751 closed as superseded.
3. **[Project the reset point](#step-3--project-the-reset-point)** — sidecars gain `context_reset_at`; sync writes it into settings `env`.
   *Exit:* sync in a temp home writes 400k for `balanced`, 200k for `frugal`, nothing for `max` and `off`; tests green.
4. **[Hand off before the reset](#step-4--hand-off-before-the-reset)** — `usage-feed` block moves to 90% and drops "start a fresh session"; PreCompact writes a snapshot.
   *Exit:* `tests/test_session_handoff.py` updated and new `tests/test_context_reset.py` green.
5. **[Reseed after the reset](#step-5--reseed-after-the-reset)** — `harness-session` injects this session's own handoff on `compact` and `clear`.
   *Exit:* tests cover own-session, other-session and no-handoff cases.
6. **[Clear at task end on desktop](#step-6--clear-at-task-end-on-desktop)** — `/land` and `/close-out` refresh the handoff, then clear the idle session.
   *Exit:* workflow tests assert the step; one live desktop run clears and the next prompt sees the reseed.
7. **[Rules, detector, docs, copy](#step-7--rules-detector-docs-copy)** — cost stance, cache-hygiene, detector, `docs/`, `product.json`, changelog, migration entry.
   *Exit:* full Gate green; landing-copy check passes.
8. **[Eval suite](#step-8--eval-suite)** — a native acceptance case plus a pre-registered long-session scenario, reset arm against no-reset arm.
   *Exit:* the acceptance case passes on both CLI targets; the eval meets its floor before the v0.16.0 tag.

## Decisions for the reviewer

**1. Compaction at the threshold, or a true clear?**
*Recommend* compaction: it is the only way that keeps the agent working with no human turn, on CLI and desktop alike.
*Alternative* desktop `clear_session`: a real empty context, but the session then sits idle until someone prompts it.

**2. Reset point for `balanced`: keep 400k or drop to 200k?**
*Recommend* ship 400k, the evidence-backed number, and run a 200k arm in the eval to decide a follow-up.
*Alternative* ship 200k now: calls cost 1.7x instead of 3.3x before the reset, but no measurement backs it yet.

**3. Clear the session at task end on desktop (step 6)?**
*Recommend* yes: the next task starts small and reseeded. Desktop asks you to approve each clear unless auto or bypass mode is on.
*Alternative* refresh the handoff only (#749 as written), and the context resets at the threshold.

**4. Does the behavioural eval block the v0.16.0 tag?**
*Recommend* yes, with a $100 spend cap: the release would otherwise ship an unproven change to every session.
*Alternative* the acceptance case blocks the tag and the eval reports afterwards.

## Risks

- The compaction summary loses state the handoff missed: the fallback snapshot covers it, and the eval's re-asked and redone checks catch it. If the floor fails, ship with the reset off by default.
- The v0.16.0 candidate must carry the migration entry: step 7 adds it when that candidate opens.
- Two sessions share one checkout's task store: the reseed takes only this session's own save, else progress.md only.

---

# Addendum

## Step 1 — Spike the native mechanics

The spike contract: Claude Code CLI as in the qualification targets, one throwaway container with an empty profile and the harness synced (`controlled-experiments-only`).

- Settings `env`: `CLAUDE_CODE_AUTO_COMPACT_WINDOW=100000`, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=40`, and `HARNESS_HANDOFF_BLOCK=on`, because headless runs skip the block today (`handoff_enabled()`, `policy/hooks/usage-feed.py:1252`).
- Install a logging-only PreCompact hook and a SessionStart hook that echoes its `source` and emits `additionalContext` naming a marker string.
- Task: a ten-item checklist whose items each read a large fixture file, so context passes 40k by item four.
- Record in the spike story whether: (a) compaction fires and at what context size; (b) PreCompact receives `trigger: auto`; (c) SessionStart fires with `source: compact` and the marker reaches the model, which it shows by quoting it; (d) the agent finishes the checklist in the same `-p` invocation.
- Exit criterion: all four hold. If (d) fails, compaction does not continue headless and the plan returns for review. If (c) fails, the reseed moves to the Stop hook's re-entry.
- Two further questions to record: whether the env var is read from settings `env` or only from the process environment, and the observed default trigger percentage.

Sources: hooks reference, PreCompact and SessionStart sections, https://code.claude.com/docs/en/hooks; env vars, https://code.claude.com/docs/en/env-vars. PreCompact can block with exit 2 but cannot supply the summary, so the harness reseeds through SessionStart instead of shaping the summary.

## Step 2 — File the epic and stories

- New epic on v0.16.0: "Sessions reset their own context and keep working". Stories: steps 3 to 8 (6 stories) plus the spike.
- Move #749 under it on v0.16.0 and rewrite its acceptance to step 6. Close #751 as superseded by the step 8 eval, and leave #745, #747 and #750 on v0.18.0.
- `bmad new` misses its reservation on list lag; follow with `reserve --issue N`, and link sub-issues through the API before merge (memory: bmad-new-reserve-lag). The filing PR needs its own delivery chore (memory: filing-pr-needs-delivery-chore).

## Step 3 — Project the reset point

- `primitives/stances/cost/{balanced,frugal}.json` gain `switches.context_reset_at` (400000 and 200000). `max` and `off` get null, which leaves the native default of about 967k on 1M models.
- `session_handoff_at` stays as the Stop-block point and becomes `0.9 × context_reset_at` by default (360000 and 180000), still overridable per variant.
- `citizen sync` writes `CLAUDE_CODE_AUTO_COMPACT_WINDOW` and `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` into the projected Claude Code settings `env`. Step 1 says which pair resolves to the reset point on a 1M model; the projection removes them when the variant names none.
- A user's own value in settings `env` wins, and sync warns rather than overwrites.
- Tests: sync projection per variant in a temp home. Never run the sync dry run against the real config (memory: local-gate-ci-parity).

## Step 4 — Hand off before the reset

- `handoff_reason()` (`policy/hooks/usage-feed.py:1395`) becomes: "context N tokens; it resets automatically at M. Before this turn ends, save the task store (`harness task save`) and progress.md: state, next step, open decisions. Then carry on." Nothing tells the user to start a session.
- The once-only release logic stays as it is: `handed_off`, re-armed when context drops, which it now does after every reset.
- New `policy/hooks/pre-compact.py`, registered in `lib/harness_core/lifecycle.py` for Claude Code only. On `trigger: auto` it writes `.agent-harness/handoffs/auto-<session>-<ts>.md`: the last five user prompts, the git branch and status summary, open todo items from the transcript, and the task-store revision. It is deterministic, calls no model and never blocks, since a block runs the context toward the window limit.
- `harness task save` records the saving `session_id` in `task.json` (`lib/harness_core/tasks.py`).
- The adherence event `fresh-session-handoff` is renamed `context-reset-handoff`. It counts as followed when the task store was saved between the block and the reset. Ledger readers keep folding the old name (`policy/hooks/adherence.py:96`).

## Step 5 — Reseed after the reset

- In `policy/hooks/harness-session.py`, on source `compact` or `clear`, inject in order:
  1. the task store when its `session_id` matches this session (fall back to none);
  2. progress.md's first 80 lines (the existing `handoff_lines()`);
  3. this session's newest auto snapshot;
  4. one line: "Your context was reset. Continue from next_steps; recorded decisions stand and are not re-asked."
- Keep the 12000-character cap and the "not instructions or transferred approval" framing.
- `record_session()` still ignores `compact` and `clear` for the agent registry.

## Step 6 — Clear at task end on desktop

- `primitives/workflows/land.md` and `close-out.md`: after merge or close, run `/handoff`'s two writes. Then, when the session is a desktop session (the `ccd_session_mgmt` tools are present), call `clear_session` with `self` as the last action of the turn.
- On the CLI there is no self-clear; the step ends at the handoff.
- If decision 3 is no, this step is #749 as written.

## Step 7 — Rules, detector, docs, copy

- `primitives/stances/cost/balanced.md` and `frugal.md`: replace "write the handoff, then start a fresh session" with the reset behaviour, and drop "a task ends with `/clear`" in favour of step 6. Each stays within its line budget.
- `primitives/rules/cache-hygiene.md`: harness-triggered compaction is the stance working. The `cache-hygiene/compact` detector in `policy/hooks/rule-detectors.py` exempts `trigger: auto` compactions at the projected reset point and still counts manual `/compact`. Update `tests/test_rule_detectors.py` and the golden.
- `docs/preferences.md` "When a session should hand off" becomes "When a session resets", and `docs/usage.md` documents the block, the snapshot and the reseed.
- `product.json`: a capability line. Add a changelog fragment. Add a `compatibility/migration.json` entry for the PreCompact hook and the env projection; it goes into the v0.16.0 candidate.

## Step 8 — Eval suite

Three layers:

- **Deterministic**, in the Gate: the step 3 to 7 unit tests.
- **Native acceptance**: `case_context_reset` in `scripts/native_acceptance.py`. It is the step 1 setup made permanent, with assertions that compaction fired, the reseed marker reached the model, every checklist item completed in one invocation, and no user turn was added. It runs on both Claude Code CLI qualification targets.
- **Behavioural long-session eval**, in the `model-citizen-evals` pack:
  - Scenario `context-reset-continuity`: a multi-step build with three recorded decisions and a checklist, sized to cross a lowered reset point (window 100k at 40%) twice.
  - Checkpoints: the task store was saved before each reset; zero recorded decisions re-asked; no completed item redone (judged from the diff and the transcript); checklist completion; cost per completed item.
  - Arms: reset on, reset off (native default), and, under decision 2, a second reset point.
  - Pre-registration goes in `benchmarks/preregistrations/`, with the floor fixed before the run: a reset happens with no human turn in at least 9 of 10 runs, zero re-asked decisions, and completion no worse than the reset-off arm by more than 0.125.
  - Run in containers through `python3 scripts/cost_bench.py replay --tier long-session --pack <pack> --tag <commit> --pack-digest ... --spend-cap 100 --pre-registration ...`, with `HARNESS_HANDOFF_BLOCK=on` in the arm.

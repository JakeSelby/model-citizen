# Context-reset continuity (#1297)

Filled from [the pre-registration template](../../docs/pre-registration-template.md). This run asks
whether a session whose context Claude Code resets automatically at a lowered point, with the
harness's snapshot and reseed in place, carries on with its work on its own, without asking the
user anything and without finishing less than the same session under Claude Code's native default
reset point. It is a floor check that gates the v0.16.0 tag, not a Cost-of-Pass comparison, so
SM-2's defaults are replaced where they cannot apply; each replacement says why. The protocol is
the [evidence standard](../../docs/evidence-standard.md).

**Status: exploratory** until every condition under [Conditions](#conditions-before-it-counts)
holds. The floor below is fixed now, before any run, and does not change when they are met.

---

## Run

- **Question:** with the reset point lowered, does a session that must cross it at least twice
  inside one autonomous turn carry on after at least two resets in at least 90% of its sessions,
  put no question to the user, and complete its checklist no worse than the native-default arm, by
  at most 0.125?
- **Author role:** maintainer
- **Date registered:** 2026-10-12
- **Task manifest:** evaluator pack `model-citizen-evals` version 1.4.0, set `context-reset`, scenario
  `context-reset-continuity`, which is `autonomous`: one kickoff turn states the 28-item checklist
  and the three decisions in full, and one wrap-up turn only asks for a count and changes nothing.
  The workspace holds 28 plain-prose stock ledgers of about 4,800 tokens each, about 134,000 tokens
  of reading; the kickoff turn may take 150 agent turns. One checkpoint, `cp1-continuity`, reads the
  whole session after the wrap-up. Its tag, commit and digest are recorded here, as
  `replay --dry-run` prints them, before the first trial.
- **Arms:** the ablation manifest [`benchmarks/ablations/context-reset.json`](../ablations/context-reset.json),
  five arms from one full commit containing #1292, #1293, #1295 and #1297. Each declared arm is its
  own image: the harness synced at that commit, then the arm's settings `env` merged into Claude
  Code's settings, recorded in the arm's declaration and digest and read back from the image before
  admission. Every declared arm sets `HARNESS_HANDOFF_BLOCK=on`.
  - `reset-on`: `CLAUDE_CODE_AUTO_COMPACT_WINDOW=160000`, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70`.
  - `reset-early`: the same window at `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=35`, the 200k-equivalent
    point, half of `reset-on`'s.
  - `reset-off`: both variables taken out of settings `env`, so Claude Code resets at its own
    native default point for the model. It still resets when the context reaches that point; this
    session's reading is sized so that, on a model whose default sits near 167,000 tokens, it does
    so at most once.
  - `harness` (control, the shipped balanced projection) and `bare`, implicit in every ablation
    run, are reported and outside the floor.
- **Model, CLI and effort:** open: the maintainer fixes the model, `claude-sonnet-5-5` or
  `claude-haiku-5-5`, its session cap and its reps from the table below, in a dated entry before
  the first trial, as `$MODEL`, `$RUN_CAP` and `$REPS` in the command; compaction quality depends
  on the model, so it is not chosen here. The set pins none. The arm images' Claude Code version is
  recorded at the first trial; effort `high`.

### Reset points

The spike (#1291) measured that auto-compaction fires near pct/100 x (window - 19,300), not at
pct/100 x window. With one window, the two lowered arms differ only in the percentage:

| Arm | Window | Percentage | Trigger, tokens | Room above a 30,000-token standing context |
|---|---|---|---|---|
| `reset-on` | 160,000 | 70 | 0.70 x 140,700 = 98,490 | about 68,000, fourteen ledgers |
| `reset-early` | 160,000 | 35 | 0.35 x 140,700 = 49,245 | about 19,000, four ledgers |

`reset-early` fires at 49,245, at least 45,000, and `reset-on` at exactly twice that. After a reset
the context falls to the standing context plus the summary, about 34,000 tokens, so `reset-early`
refills by about 15,000 tokens, three ledgers or about nine calls, before it fires again. Claude Code
stops a run as thrashing only when the context refills within three turns of a compaction three
times in a row, so neither arm sits where the standing context can thrash it unless that context is
above about 40,000 tokens; the first call's context in the rows says whether it was. `reset-on`
needs about 64,000 tokens of reading to cross its point again from 34,000, so 28 ledgers cross it
twice.

### Expected cost, reps and ceiling, by model

From a per-call model of the session: 28 ledgers at three calls each and one reply per user turn,
300 output tokens a call, 4,000 tokens of summary after each reset, a warm cache, and the native
default taken as a trigger near 167,000. Rates are the published per-million-token prices on
2026-10-12 (platform.claude.com/docs/en/about-claude/pricing): `claude-sonnet-5-5` 2.00 input, 2.50
cache write, 0.10 cache read, 10.00 output; `claude-haiku-5-5` 0.10, 0.125, 0.01 and 0.50 for a
prompt up to 100,000 tokens and five times that above it.

| Arm | Resets | `claude-sonnet-5-5`, USD per session | `claude-haiku-5-5`, USD per session |
|---|---|---|---|
| `reset-on` | 2 | 1.51 | 0.14 |
| `reset-early` | 9 | 2.15 | 0.13 |
| `reset-off` | 1 | 1.64 | 0.39 |
| `harness` | 1 | 1.64 | 0.39 |
| `bare` | 1 | 1.55 | 0.39 |
| Per rep of five arms | | 8.49 | 1.44 |

The ceiling counts every session at its cap, plus the 0.01 USD minimum budget the last turn gets,
plus one call past the cap (the CLI checks `--max-budget-usd` only between API calls; a compaction
call at the largest context is the dearest, about 0.05 USD on `claude-sonnet-5-5` and 0.01 on
`claude-haiku-5-5`), plus five preflights at 0.25 USD:

| | `claude-sonnet-5-5` | `claude-haiku-5-5` |
|---|---|---|
| `$RUN_CAP`, per session | 2.70 (26% over the dearest estimate) | 0.60 (54% over) |
| `$REPS` | 7 | 10 |
| Sessions | 35 | 50 |
| Expected | 59 USD | 14 USD |
| Ceiling | 35 x (2.70 + 0.01 + 0.05) + 1.25 = 97.85 USD | 50 x (0.60 + 0.01 + 0.01) + 1.25 = 32.25 USD |

Both ceilings sit inside the 100 USD whole-run stop, so the stop binds only on a run that overshoots
every cap. Ten reps on `claude-sonnet-5-5` would need a cap at or below 1.97 USD, under its dearest
estimate, so it runs seven. `policy/prices.json` has no `claude-haiku-5-5` row and prices
`claude-sonnet-5-5` cache reads at 0.20, not the published 0.10; the table must be current before
the run, or the rows' costs are unpriced or overstated.

## Hypotheses

- **Primary:** in `reset-on`, at least 90% of sessions have two or more continued resets, none puts
  a question to the user, and its mean checklist completion is at most 0.125 below `reset-off`'s.
- **Secondary:** cost per completed item and items redone, per arm; reported with intervals, no
  test.
- **Exploratory:** `reset-early` against `reset-on` on every metric (plan decision 2); resets per
  session; resets not preceded by a task-store save; the main thread's peak context; control and
  bare on every metric. None supports a claim.

## Primary metric

- **Metric:** from `cp1-continuity`, which reads the whole session as one segment:
  - **Continued resets:** `continued_resets`, the automatic `compact_boundary` events after which
    the main thread made at least one tool call before the next scripted human turn's result event
    or the end of the stream. A reset after which the turn simply ended is not continued, and a
    `null` is none.
  - **Re-asks:** `decisions_reasked`, every question the assistant put to the user after the
    kickoff: a sentence ending in `?` that addresses the user, outside code, inline code,
    quotations and quoted lines, and every `AskUserQuestion` call. The task is fully specified, so
    any such question counts.
  - **Completion:** `checklist_completion`, the share of the 28 items whose summary file exists,
    follows all three decisions and carries the ledger's audit code, tally and date.
- **Interval:** a Wilson 95% interval on the share of sessions with two or more continued resets; a
  percentile bootstrap 95% interval on the completion difference, 10,000 resamples. Both
  descriptive.
- **Undefined case:** a session whose stream is unreadable has `null` stream metrics: it fails the
  continued-reset and re-ask conditions, and its completion is still read from the tree.

## Guardrails

- **Contamination control:** `installed-checkout-oracle-and-transcript-v1`, run by the dry run at
  the arms' commit; a refused scenario stops the run.
- **Pass rate:** the completion floor above is the guardrail.
- **Fallback rate:** compromised if more than 10% of sessions report a model other than `$MODEL`.
- **Spend:** `$RUN_CAP` per session, at most the pack's 3.20 USD (`max_cost_usd_hint`), 0.25 USD
  per preflight, a whole-run stop at 100 USD reported spend; the ceilings are above.
- **Other:** a session that stops on "Autocompact is thrashing" or a refusal is a failed session in
  its arm, never excluded. The hand-off block's point comes from the cost variant, balanced's
  360,000, which these sessions never reach, so a task-store save before a reset is the agent's own
  and `resets_without_save` is exploratory; the snapshot and the reseed act at every reset.

## Sample size

- **Tasks:** 1 scenario, long by design: 28 ledgers, so `reset-on` crosses its point twice.
- **Long tasks:** the one scenario; absorbed calls are not this run's question.
- **Trials per task and arm:** `$REPS`: 10 on `claude-haiku-5-5`, 7 on `claude-sonnet-5-5`, the most
  that fit the 100 USD stop at each model's cap.
- **α and power:** none. The primary is a fixed floor, not a test: 90% of sessions is 9 of 10 at ten
  reps and 7 of 7 at seven.
- **Claim power:** none; no saving is claimed.
- **Minimum detectable effect:** none estimated. An arm that truly continues cleanly in 95% of
  sessions meets 9 of 10 with probability 0.91 and 7 of 7 with probability 0.70.
- **Variance source:** none needed.
- **Power calculation:** none; the reps are the floor's own count, bounded by the spend stop.

## Stopping rule

- **Fixed sample:** `$REPS` sessions per arm, and no result is read before then.
- **Early stop for harm or cost:** the runner stops before any session that could take reported
  spend past 100 USD. A session that ends on its own cap (`stopped: cap`) or never runs because the
  whole-run stop was reached is **inconclusive**, never a failure.
- **Stop condition for the claim:** the floor is read only when every `reset-on` and `reset-off`
  session completed without reaching its cap. With any of them inconclusive, the floor reads
  **inconclusive**, never "not met".

## Multiplicity

- **Decision rule:** the three primary conditions form one joint floor with no significance test
  and need no correction.
- **Further confirmatory tests:** none.
- **Everything else:** exploratory, labelled so, and supports no claim.

## Decision rule

The floor is met only when all three hold for `reset-on`, read from the registered rows:

1. at least 90% of its sessions (9 of 10, or 7 of 7) have a `continued_resets` of 2 or more: each
   such reset an automatic `compact_boundary` followed by at least one main-thread tool call before
   the next scripted human turn or the end;
2. `decisions_reasked` is 0 in every one of its sessions, with no `null`;
3. its mean `checklist_completion` minus `reset-off`'s is at least -0.125.

If the floor is met, v0.16.0 ships with the reset on by default. If it is not met, v0.16.0 ships
with the reset off by default: every shipped cost variant's `context_reset_at` null, and the
snapshot and reseed left in place. If it is inconclusive, v0.16.0 also ships with the reset off,
and the result is labelled inconclusive, not as a failed floor. The result is recorded on #1297
with its counts and intervals, whatever it shows. Command:

```sh
python3 scripts/cost_bench.py replay --tier long-session --pack ../model-citizen-evals \
    --pack-ref v1.4.0 --pack-set context-reset --pack-digest "$PACK_DIGEST" --tag "$COMMIT" \
    --ablations benchmarks/ablations/context-reset.json --model "$MODEL" --run-cap "$RUN_CAP" \
    --reps "$REPS" --spend-cap 100 --raw "$RESET_RAW" \
    --pre-registration benchmarks/preregistrations/2026-10-12-context-reset-continuity.md
```

`$RESET_RAW` is a fresh directory outside the repository; every other variable is fixed before the
first trial, as the conditions below require.

## Conditions before it counts

The run is exploratory, and cited nowhere as evidence, until each of these holds and is recorded in
a dated deviation entry before the first trial:

- **The code merges:** #1292, #1293, #1295 and #1297 are merged, and `$COMMIT` is a full commit that
  contains them.
- **The pack is tagged:** `v1.4.0` of `model-citizen-evals` is tagged, and its commit and
  `$PACK_DIGEST` are recorded.
- **The model and its cap are recorded:** `$MODEL`, `$RUN_CAP` and `$REPS`, one row of the table
  above, are written into the entry before the first trial; a run whose rows name any other is
  not this registration's.
- **A bare arm is present:** every ablation run includes `bare`; the dry run must list it.

## Open questions

- **Which model, at which cap and reps?** `claude-sonnet-5-5` at 2.70 USD and 7 reps, or
  `claude-haiku-5-5` at 0.60 USD and 10 reps; the maintainer's call, recorded before the first
  trial.
- **Is `context-reset` a required qualification case?** The native acceptance case is optional
  today: runnable by name on a Claude Code target and left out of `all`. Making it required adds it
  to the catalog's required cases for every target, Codex and desktop included, and changes what
  qualification reuse compares. That is the maintainer's call, made with the release that requires
  it.

## Exclusions

- **Analysis population:** every assigned session, crashes, timeouts, thrash stops and refusals
  included, scored from whatever its stream and tree show; a session stopped on its cap is
  inconclusive, as the stopping rule says.
- **Pre-stated exclusions:** none.

## Deviation log

Append a dated entry for every change after the first trial: what changed, why, and which figures
it touches. Never edit an entry.

- None yet.

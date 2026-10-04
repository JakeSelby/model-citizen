# Delegation-nudge spawn check (#513 acceptance criterion 4, delivered by #1101)

Filled from [the pre-registration template](../../docs/pre-registration-template.md). This run asks
whether the PostToolUse delegation nudge (#513, merged in #1090) makes the harness arm spawn on a
task above break-even and stay out of a two-file task below it. It is a fire check on the micro
tier, not a Cost-of-Pass comparison, so SM-2's defaults are replaced where they cannot apply; each
replacement says why. The protocol is the [evidence standard](../../docs/evidence-standard.md).

---

## Run

- **Question:** under the evaluator pack's `delegation-nudge` set, does the harness arm spawn in at
  least 4 of 5 runs of the above-break-even task and in none of 5 runs of the two-file control?
- **Author role:** maintainer
- **Date registered:** 2026-10-01
- **Task manifest:** evaluator pack `model-citizen-evals` version 1.0.0 (tag `v1.0.0`) at commit
  `24093c25ef9d63433dedb9ade344cda61e76a174`, digest
  `6399bd95f99e76c61e448f1c5f99303a1dc4f3a7fea36f3857c5927daf2199b0`, set `delegation-nudge`, as
  `replay --dry-run` and `replay --verify-tasks` printed them on 2026-10-01
- **Arms:** harness at full commit `59520dd339b340a0864d6cef6ae657c49c9b5b2e`, which contains the
  nudge (#1090), and bare
- **Model, CLI and effort:** `claude-haiku-4-5-20251001` (the set's pinned model), Claude Code
  2.1.280 (the arm images' `CLAUDE_CODE_VERSION`), effort `high`

## Hypotheses

- **Primary:** with the nudge, the harness arm spawns in at least 4 of 5 runs of
  `micro-hook-inventory` (above break-even) and in 0 of 5 runs of `micro-two-file` (below it). This
  is #513's acceptance criterion 4, restated from "three of four" to five runs per task, the
  evidence standard's minimum (point 7): 4 of 5 is the smallest count at or above AC4's 75% share.
  It replaces SM-2's Cost-of-Pass hypothesis, as the template allows, because the question is
  whether delegation fires, not what it costs.
- **Secondary:** the bare arm, which carries no nudge, spawns less often than the harness arm on
  `micro-hook-inventory`; reported as counts, with no test. It is the control point 5 of the
  evidence standard asks for.
- **Exploratory:** `absorbed_calls` per spawning run; mean cost of spawning against non-spawning
  runs (descriptive, never causal); pass rate and cost per arm; the `summarise` delegation verdict
  per task. None supports a claim.

## Primary metric

- **Metric:** per task, the number of harness-arm trials, out of five, whose `spawns` is above zero:
  spawn calls whose result is not an error, on the main thread or inside a spawned subagent, never
  inside a `Workflow` agent. `spawns` of `null` is unknown: it never counts toward the four spawns
  on the long task, and on the control it fails the "none" condition.
- **Interval:** a Wilson 95% interval on each task's harness-arm spawn share, descriptive only.
- **Undefined case:** a task whose five harness trials are all `null` is reported as unknown, and
  AC4 is not met.

## Guardrails

- **Contamination control:** `installed-checkout-oracle-and-transcript-v1`. The dry run on
  2026-10-01 reported both tasks clean at the harness commit above; none is excluded. Any scored
  transcript with a tool-input path under `/opt/model-citizen` fails that attempt. The transcript
  check is not proof against unknown symlinks, relative traversal or copies.
- **Pass rate:** not a guardrail; this run makes no cost or non-inferiority claim. Reported.
- **Fallback rate:** the run is reported as compromised if more than 2 of its 20 scored trials
  (10%) report a model other than `claude-haiku-4-5-20251001`.
- **Spend:** 0.10 USD per run (soft), 0.05 USD per arm preflight, and a whole-run stop of 3.10 USD
  reported spend. The dry run prints a 2.10 USD ceiling if every run and preflight reaches its cap.
- **Other:** break-even is confirmed, not assumed. `micro-hook-inventory` counts as above
  break-even for AC4 only when this run's bare-arm median `gather_calls` is above 7.6;
  `micro-two-file` counts as below it only when that median is at most 7.6.

## Sample size

- **Tasks:** 2, of which 1 is a long multi-turn task (`micro-hook-inventory`, expected absorbed
  calls 13; `micro-two-file` expects 2).
- **Long tasks:** a task is long when its absorbed-call size is above 7.6, FR-34's upper
  break-even. Its absorbed-call size is the median, over its clean bare-arm runs, of the row's
  `gather_calls`: `Read`, `Grep` and `Glob` calls in every thread. The pack's mark is pilot
  pending; this run's own bare rows confirm or refute it, as the guardrail above states.
- **Trials per task and arm:** 5
- **α and power:** none. AC4 is a fixed count threshold on five trials per task, not a two-arm
  test, so SM-2's joint-power default does not apply.
- **Claim power:** none; this run makes no saving claim.
- **Minimum detectable effect:** none; there is no effect estimate. A harness arm that truly spawns
  in 80% of runs meets the 4-of-5 bar with probability 0.74.
- **Variance source:** none needed.
- **Power calculation:** none. Five runs per task is AC4's registered count and the evidence
  standard's minimum; `scripts/replay_power.py` sizes the production set, not a fire check.

## Stopping rule

- **Fixed sample:** the run stops when both tasks have 5 trials per arm (20 scored runs), and no
  result is read before then.
- **Early stop for harm or cost:** the runner stops before any launch that could take reported
  spend past 3.10 USD and records a partial set. A red preflight refuses the run before any
  scored trial. An observed effort that differs from the pinned one stops the set.
- **Stop condition for the claim:** AC4 is read only from a complete set; a partial set reports
  its counts and AC4 reads not met.

## Multiplicity

- **Decision rule:** both conditions must hold, so they form one joint criterion with no
  significance test and need no correction.
- **Further confirmatory tests:** none.
- **Everything else:** exploratory, labelled so, and supports no claim.

## Decision rule

AC4 is met only when all of these hold, read from the registered rows:

- the harness arm spawns in at least 4 of its 5 `micro-hook-inventory` trials;
- the harness arm spawns in 0 of its 5 `micro-two-file` trials, with no `null` among them;
- the bare-arm median `gather_calls` is above 7.6 for `micro-hook-inventory` and at most 7.6 for
  `micro-two-file`.

Otherwise AC4 is not met, and the report names which condition failed. The result is recorded on
#513 and #1101 with its counts and intervals, whatever it shows. Command:

```sh
python3 scripts/cost_bench.py replay --tier micro --pack ../model-citizen-evals --pack-ref v1.0.0 \
    --pack-set delegation-nudge \
    --pack-digest 6399bd95f99e76c61e448f1c5f99303a1dc4f3a7fea36f3857c5927daf2199b0 \
    --tag 59520dd339b340a0864d6cef6ae657c49c9b5b2e --reps 5 --run-cap 0.10 --spend-cap 3.10 \
    --raw "$NUDGE_RAW" --pre-registration benchmarks/preregistrations/2026-10-01-delegation-nudge.md
```

`$NUDGE_RAW` is a fresh directory outside the repository, named in the results comment. The pack
path is wherever the `v1.0.0` tag is checked out; the run reads that tag, never a working tree.

## Exclusions

- **Analysis population:** every assigned trial, crashes, timeouts and fallbacks included. An
  errored harness trial counts toward its task's five, scored from whatever its stream shows.
- **Pre-stated exclusions:** none for the primary metric. The break-even median uses clean bare
  runs only, as the delegation verdict defines them: rows carrying `error` are left out of it and
  counted.

## Deviation log

Append a dated entry for every change after the first trial: what changed, why, and which figures
it touches. Never edit an entry.

- None yet.

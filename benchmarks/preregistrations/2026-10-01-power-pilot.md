# Power pilot on the evaluator pack's production set (#796, delivered by #1144)

Filled from [the pre-registration template](../../docs/pre-registration-template.md). The pilot
estimates the variances SM-2's power analysis needs and confirms each task's long mark; it makes no
Cost-of-Pass claim. Its output sizes the #560 proof run, whose own pre-registration follows it.
Pilot rows are never proof-set rows. The protocol is the
[evidence standard](../../docs/evidence-standard.md) and the
[evaluator pack](../../docs/benchmarks.md#evaluator-pack) section of the benchmarks doc.

---

## Run

- **Question:** what k tasks, n long tasks and m trials per task and arm give SM-2 joint power 0.8
  at a minimum detectable effect of 15%, and does the pack's production set of 7 tasks, 4 of them
  marked long, meet that at five trials?
- **Author role:** maintainer
- **Date registered:** 2026-10-01
- **Task manifest:** evaluator pack `model-citizen-evals` version 1.0.0 (tag `v1.0.0`) at commit
  `24093c25ef9d63433dedb9ade344cda61e76a174`, digest
  `6399bd95f99e76c61e448f1c5f99303a1dc4f3a7fea36f3857c5927daf2199b0`, set `production`, as
  `replay --dry-run` and `replay --verify-tasks` printed them on 2026-10-01
- **Arms:** harness at full commit `59520dd339b340a0864d6cef6ae657c49c9b5b2e`, and bare
- **Model, CLI and effort:** `claude-sonnet-5`, Claude Code 2.1.280 (the arm images'
  `CLAUDE_CODE_VERSION`), effort `high`. The #560 proof run must use the same model and effort, or
  these variances do not size it.

## Hypotheses

- **Primary:** estimation, not a test. From the pilot rows, `scripts/replay_power.py` estimates the
  between-task variance of the log Cost-of-Pass ratio (`tau2`), the within-cell squared coefficient
  of variation of cost (`cv2`), the pooled pass rate, the between-task variance of the pass-rate
  difference and the long tasks' own `tau2`, and from them the smallest design meeting joint power
  0.8 at an effect of 15%. This replaces SM-2's Cost-of-Pass hypothesis, as the template allows,
  because a pilot sizes the proof run and must not pre-empt it.
- **Secondary:** each task's `long` mark in the pack agrees with its measured absorbed-call size;
  the pack marks `plugin-registry` (expected 18), `store-migration` (12), `rename-quantity` (14)
  and `audit-handlers` (12) long, and `money-rounding` (2), `csv-export` (3) and `cli-json` (2)
  short.
- **Exploratory:** the SM-2 verdict, Cost-of-Pass ratio and pass-rate difference that `summarise`
  prints from these rows; the per-task delegation verdict; cache-normalised cost and
  `cache_miss_ratio`; wall time. All are reported labelled "pilot" and none supports a claim.

## Primary metric

- **Metric:** the design `python3 scripts/replay_power.py --pilot "$PILOT_ROWS" --have 7 4 5`
  prints (k, n, m, decision power and claim power) and its `--have` verdict for the current set.
  The SM-2 Cost-of-Pass ratio is computed by `summarise` but is exploratory here.
- **Interval:** none on the primary output, which is a point design from the estimated variances
  under the normal approximation in `replay_power.py`'s docstring. `summarise`'s paired,
  task-clustered percentile bootstrap (seed 795, 10,000 resamples) is printed for the exploratory
  ratio.
- **Undefined case:** if either arm passes nothing, or no long task has enough clean rows for
  `long_tau2`, the power command reports claim power unavailable and exits 1; that is reported as
  "set not sizable from this pilot", with the reason.

## Guardrails

- **Contamination control:** `installed-checkout-oracle-and-transcript-v1`. The dry run on
  2026-10-01 reported all seven tasks clean at the harness commit above; none is excluded. Any
  scored transcript with a tool-input path under `/opt/model-citizen` fails that attempt. The
  transcript check is not proof against unknown symlinks, relative traversal or copies.
- **Pass rate:** non-inferiority margin δ = 0.125, reported for the exploratory SM-2 reading only.
- **Fallback rate:** the pilot is reported as compromised if more than 7 of its 70 scored trials
  (10%) report a model other than `claude-sonnet-5`.
- **Spend:** 2 USD per run (soft, `--max-budget-usd`), 0.25 USD per arm preflight, and a whole-run
  stop of 140.50 USD reported spend: 7 tasks x 5 trials x 2 arms x 2 USD, plus two preflights.
- **Other:** none.

## Sample size

- **Tasks:** 7, of which 4 are long multi-turn tasks (pack marks; pilot pending).
- **Long tasks:** a task is long when its absorbed-call size is above 7.6, FR-34's upper
  break-even. Its absorbed-call size is the median, over its clean bare-arm runs, of the row's
  `gather_calls`: `Read`, `Grep` and `Glob` calls in every thread, the calls a subagent could
  absorb. The bare arm never delegates, so that count is all the gathering a subagent could have
  taken. This pilot is the run that confirms each mark: pilot pending.
- **Trials per task and arm:** 5
- **α and power:** α 0.05 two-sided; the power command targets joint power 0.8 on both tests of
  the decision rule, assuming a true ratio of 0.85 and equal pass rates. The pilot itself is not
  powered for a claim.
- **Claim power:** none for this run, which makes no claim. The power command reports the claim
  power #560 would have at the design it prints.
- **Minimum detectable effect:** 15%, the effect the power command sizes #560 for.
- **Variance source:** this pilot's rows. None exists before it: `benchmarks/tasks.json` holds no
  live task, and no earlier run used this pack.
- **Power calculation:** depends on this pilot. After the last trial,
  `python3 scripts/replay_power.py --pilot "$PILOT_ROWS" --have 7 4 5` prints k, n and m for joint
  decision and claim power 0.8 at an effect of 15% (at least five trials per task and arm, at most
  20 trials and 200 tasks, its defaults) and whether 7 tasks, 4 long, at 5 trials meets it. Its
  output is posted on #560 and sizes #560's pre-registration and cap.

## Stopping rule

- **Fixed sample:** the run stops when every task has 5 trials per arm (70 scored runs), and no
  result is read before then.
- **Early stop for harm or cost:** the runner stops before any launch that could take reported
  spend past 140.50 USD and records a partial set. A red preflight refuses the run before any
  scored trial. An observed effort that differs from the pinned one stops the set.
- **Stop condition for the claim:** none; the pilot makes no saving claim. A partial set's power
  output is labelled partial, and sizing #560 from it needs a deviation-log entry first.

## Multiplicity

- **Decision rule:** the pilot's decision is a sizing outcome, not a significance test, so no
  correction applies.
- **Further confirmatory tests:** none.
- **Everything else:** exploratory, labelled so, and supports no claim.

## Decision rule

The pilot is read in two parts, whatever they show, and both are posted on #560:

- **Sizing:** "set sufficient" when the power command's `--have 7 4 5` exits 0; "set
  insufficient" when it exits 1 and prints a design within its limits, the gap stated as the extra
  tasks, long tasks or trials #560 needs, and the pack grows in a new version before #560 runs;
  "not sizable" when no design within the limits reaches 0.8, or claim power is unavailable.
- **Long marks:** each task's mark is confirmed when its median `gather_calls` over clean bare runs
  is on the same side of 7.6 as the mark, and refuted otherwise; a refuted mark is corrected in a
  new pack version before #560. With fewer than three clean bare runs a task's size reads unknown
  and its mark stays pending.

Command:

```sh
python3 scripts/cost_bench.py replay --pack ../model-citizen-evals --pack-ref v1.0.0 \
    --pack-digest 6399bd95f99e76c61e448f1c5f99303a1dc4f3a7fea36f3857c5927daf2199b0 \
    --tag 59520dd339b340a0864d6cef6ae657c49c9b5b2e --model claude-sonnet-5 --reps 5 \
    --spend-cap 140.50 --bucket pilot --change-note "#796 power pilot; no claim" \
    --out "$PILOT_OUT" --raw "$PILOT_RAW" \
    --pre-registration benchmarks/preregistrations/2026-10-01-power-pilot.md
python3 scripts/cost_bench.py summarise --results "$PILOT_ROWS"
python3 scripts/replay_power.py --pilot "$PILOT_ROWS" --have 7 4 5
```

`$PILOT_OUT` and `$PILOT_RAW` are fresh directories outside the repository, named in the results
comment. The runner writes one subdirectory per tag, so the pilot rows are `$PILOT_ROWS`, which is
`$PILOT_OUT/59520dd339b340a0864d6cef6ae657c49c9b5b2e`. The pack path is wherever the `v1.0.0` tag
is checked out; the run reads that tag, never a working tree.

## Exclusions

- **Analysis population:** every assigned trial, crashes, timeouts and fallbacks included, for
  cost, pass rate and the variances the power command estimates.
- **Pre-stated exclusions:** none for the variance estimates. The absorbed-call median uses clean
  bare runs only: a row carrying `error`, or whose `gather_calls` is `null`, is left out of the
  median and counted.

## Deviation log

Append a dated entry for every change after the first trial: what changed, why, and which figures
it touches. Never edit an entry.

- None yet.

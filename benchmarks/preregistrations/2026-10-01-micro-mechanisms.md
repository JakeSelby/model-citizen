# Micro-tier mechanism campaign (#512, delivered by #1131)

Filled from [the pre-registration template](../../docs/pre-registration-template.md). This run asks
whether a mechanism fires, not what the harness costs, so SM-2's Cost-of-Pass defaults are replaced
where the micro tier cannot answer them; each replacement says so and why. The protocol is the
[evidence standard](../../docs/evidence-standard.md) and the micro tier in
[benchmarks](../../docs/benchmarks.md#micro-tier).

---

## Run

- **Question:** does each mechanism the micro tier targets (delegation, the stop gate and the
  output style) fire in the harness arm on the pinned small model, in the evaluator pack's micro set?
- **Author role:** maintainer
- **Date registered:** 2026-10-01
- **Task manifest:** evaluator pack `model-citizen-evals` version 1.0.0 (tag `v1.0.0`) at commit
  `24093c25ef9d63433dedb9ade344cda61e76a174`, digest
  `6399bd95f99e76c61e448f1c5f99303a1dc4f3a7fea36f3857c5927daf2199b0`, set `micro`, as
  `replay --dry-run` and `replay --verify-tasks` printed them on 2026-10-01
- **Arms:** harness at full commit `59520dd339b340a0864d6cef6ae657c49c9b5b2e`, and bare
- **Model, CLI and effort:** `claude-haiku-4-5-20251001` (the set's pinned model), Claude Code
  2.1.280 (the arm images' `CLAUDE_CODE_VERSION`), effort `high`

## Hypotheses

- **Primary:** in the harness arm, each of the three mechanisms fires on its own task: a spawn on
  `micro-delegation`, a Stop-hook block on `micro-stop-gate`, and no output-style detector hit on
  `micro-output-style`. This replaces SM-2's Cost-of-Pass hypothesis, as the template allows,
  because the micro tier measures whether a mechanism fires and its rows never carry a ratio or a
  verdict (docs/benchmarks.md, Micro tier).
- **Secondary:** (1) the bare arm, which has no harness hooks or rules, fires each mechanism less
  often than the harness arm; reported as counts beside the harness arm's, with no test. (2) The
  small model accepts `--effort high`: every launch, the preflights included, starts without an
  effort error. For scored trials this is recorded from the rows' `effort`, `observed_effort` and
  `error` fields. A red preflight exits before any row is written, so a preflight outcome is
  recorded from the saved raw preflight output (`preflight-<arm>.json` under `--raw`) and the
  run's stderr and exit status, all three named in the results comment.
- **Exploratory:** wall time per run and for the whole set (the pace #1131 asks for); pass rate and
  cost per arm; the raw Stop-hook input kept by `--raw`, for #1100's recorded native Stop payload.
  None of these supports a claim.

## Primary metric

- **Metric:** per task, the number of harness-arm trials, out of five, whose `mechanism_fired` is
  `true`. `mechanism_fired` is read as the micro tier defines it: `spawns` above zero for
  delegation, `hook_blocks` above zero for the stop gate, and no hit from any of the manifest's
  four voice detectors for the output style. A `null` is unknown and is never counted as fired.
- **Interval:** a Wilson 95% interval on each task's harness-arm fire share, descriptive only.
- **Undefined case:** a task whose five harness trials are all `null` is reported as unknown for
  that mechanism, never as not firing.

## Guardrails

- **Contamination control:** `installed-checkout-oracle-and-transcript-v1`. The dry run on
  2026-10-01 reported all three tasks clean at the harness commit above; none is excluded. Any
  scored transcript with a tool-input path under `/opt/model-citizen` fails that attempt. The
  transcript check is not proof against unknown symlinks, relative traversal or copies.
- **Pass rate:** not a guardrail here; pass rate is exploratory, since the micro tier makes no
  cost or non-inferiority claim. Recorded and reported.
- **Fallback rate:** the run is reported as compromised if more than 3 of its 30 scored trials
  (10%) report a model other than `claude-haiku-4-5-20251001`.
- **Spend:** 0.10 USD per run (soft, `--max-budget-usd`), 0.05 USD per arm preflight, and a whole-run
  stop of 4.55 USD reported spend. The dry run prints a 3.10 USD ceiling if every run and preflight
  reaches its cap; the stop is the binding limit.
- **Other:** `--raw` is required, because the output-style detectors score from the saved streams;
  a missing stream reads unknown.

## Sample size

- **Tasks:** 3, of which 1 is a long multi-turn task (`micro-delegation`, expected absorbed calls
  11; `micro-stop-gate` and `micro-output-style` are short at 1 each).
- **Long tasks:** a task is long when its absorbed-call size is above 7.6, FR-34's upper
  break-even. Its absorbed-call size is the median, over its clean bare-arm runs, of the row's
  `gather_calls`: `Read`, `Grep` and `Glob` calls in every thread. `micro-delegation`'s mark is
  the pack's stated expectation, pilot pending; this run's bare rows report its median.
- **Trials per task and arm:** 5
- **α and power:** none. The primary metric is a descriptive threshold on five trials per task, not
  a two-arm test, so SM-2's joint-power default does not apply.
- **Claim power:** none; this run makes no saving claim.
- **Minimum detectable effect:** none; there is no effect estimate. At five trials the threshold of
  four fires is met by a mechanism that fires 80% of the time with probability 0.74.
- **Variance source:** none needed; no power calculation sizes this run.
- **Power calculation:** none. Five trials per task and arm is the evidence standard's minimum
  (point 7) and the micro tier's protocol; `scripts/replay_power.py` sizes the production set, not
  a fire check.

## Stopping rule

- **Fixed sample:** the run stops when every task has 5 trials per arm (30 scored runs), and no
  result is read before then.
- **Early stop for harm or cost:** the runner stops before any launch that could take reported
  spend past 4.55 USD, and records a partial set. A red preflight, including one that fails because
  the model refuses `--effort`, refuses the run before any scored trial; that refusal is the
  answer to secondary hypothesis 2, and a rerun needs a deviation-log entry and a new approval.
  An observed effort that differs from the pinned one stops the set, as the runner does.
- **Stop condition for the claim:** a mechanism is reported as firing only from a complete set of
  five harness trials on its task. A partial set reports its counts and makes no firing claim.

## Multiplicity

- **Decision rule:** three mechanisms, each read on its own task against a fixed threshold, with no
  significance test, so no correction applies.
- **Further confirmatory tests:** none.
- **Everything else:** exploratory, labelled so, and supports no claim.

## Decision rule

For each mechanism, read from the harness arm's rows on its own task:

- **fires** when `mechanism_fired` is `true` in at least 4 of the 5 trials, the 75% share the
  delegation verdict uses, rounded up at five trials;
- **does not fire** when it is `true` in at most 1 of the 5 trials and no more than one is `null`;
- **inconclusive** otherwise, with the counts of `true`, `false` and `null`.

The bare arm's counts are printed beside each verdict. Every verdict is published on #512 with its
counts and interval, whatever it shows. Command:

```sh
python3 scripts/cost_bench.py replay --tier micro --pack ../model-citizen-evals --pack-ref v1.0.0 \
    --pack-digest 6399bd95f99e76c61e448f1c5f99303a1dc4f3a7fea36f3857c5927daf2199b0 \
    --tag 59520dd339b340a0864d6cef6ae657c49c9b5b2e --reps 5 --run-cap 0.10 --spend-cap 4.55 \
    --raw "$MICRO_RAW" --pre-registration benchmarks/preregistrations/2026-10-01-micro-mechanisms.md
```

`$MICRO_RAW` is a fresh directory outside the repository, named in the results comment. The pack
path is wherever the `v1.0.0` tag is checked out; the run reads that tag, never a working tree.

## Exclusions

- **Analysis population:** every assigned trial, crashes, timeouts and fallbacks included.
- **Pre-stated exclusions:** none. An errored trial stays in its task's five; its
  `mechanism_fired` is whatever the stream shows, and `null` when the stream cannot show it.

## Deviation log

Append a dated entry for every change after the first trial: what changed, why, and which figures
it touches. Never edit an entry.

- None yet.
- 2026-10-02: The first launch was refused at preflight, before any scored trial, having reported
  0.07 USD across both preflights. The harness arm's preflight ended `error_max_budget_usd` at
  0.0556 USD on its first turn, before its tool result came back, against the 0.05 USD preflight
  cap; the bare arm's finished both turns at 0.0188 USD. That first turn is the harness arm's
  cold instruction prefix on `claude-haiku-4-5` at effort high, so the 0.10 USD run cap would
  have left it about 0.04 USD for a task against the bare arm's 0.08, truncating harness runs
  before the Stop hook `micro-stop-gate` measures and biasing every verdict against it (#1165).
  Changed: the run cap from 0.10 to 0.25 USD, the preflight cap from 0.05 to 0.15 USD, the
  ceiling from 3.10 to 7.80 USD and the stop from 4.55 to 7.80 USD; these replace the figures in
  the Spend guardrail, the Stopping rule and the command. No scored trial had run,
  so no outcome was seen before the change. Amended command, the same flags with new caps:
  `python3 scripts/cost_bench.py replay --tier micro --pack ../model-citizen-evals --pack-ref
  v1.0.0 --pack-digest 6399bd95f99e76c61e448f1c5f99303a1dc4f3a7fea36f3857c5927daf2199b0 --tag
  59520dd339b340a0864d6cef6ae657c49c9b5b2e --reps 5 --run-cap 0.25 --spend-cap 7.80 --raw
  "$MICRO_RAW" --pre-registration benchmarks/preregistrations/2026-10-01-micro-mechanisms.md`
- 2026-10-05: `--raw` keeps each run's stream-json output, which reports a Stop hook's start and
  response but never the input the hook received, so the Exploratory item's assumption that the
  kept streams would yield #1100's native Stop payload was wrong; #1100 recorded it instead from a
  separate one-turn bare-arm run with a capturing Stop hook.
- 2026-10-07: `micro-stop-gate`'s pass rate in pack 1.0.0 is not interpretable as task success
  (#1170). Its prompt forbade editing `tests/test_loader.py` and its check required that file
  unchanged, yet the rename leaves that test red, so the only edit the stop gate accepts failed
  the check. The harness arm's 0/5 passes on that task followed from the gate firing, the
  Primary hypothesis, which this entry does not change; the bare arm's 5/5 passes left the
  tests red. No figure is recomputed. Evaluator pack 1.2.0 lets the agent update that test and
  requires the visible tests to pass, and this repository's own manifest of the task does the
  same. Any re-run of this campaign is registered anew or amended here first, naming the pack
  version and digest it reads.

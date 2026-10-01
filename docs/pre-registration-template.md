# Pre-registration template

Copy this file for every run that is not exploratory, whether a proof run, a pilot or an ad hoc
eval, fill every section, and commit the copy before the run's first trial. A plan lives in
`benchmarks/preregistrations/`, named `<YYYY-MM-DD>-<slug>.md` for the date it was registered and
the question, for example `benchmarks/preregistrations/2026-10-01-harness-vs-bare.md`; its **Date
registered** field carries the same date. Merge its pull request before the first trial, so the
plan's publication time is one GitHub records rather than a date you set. This is protocol point 1
and item 1 of the [evidence standard](evidence-standard.md), which says how the order is checked.

Name the plan to the run: `scripts/cost_bench.py replay --pre-registration <path>`. The run refuses
to start unless the file is inside this repository, committed with no uncommitted change, named
with its date, and has these fields filled, with no `<...>` left in them: **Question** and **Date
registered** under Run, **Primary** under Hypotheses, **Metric** under Primary metric, **Tasks** and
**Trials per task and arm** under Sample size, and the Decision rule section. Every row then names
the plan and the commit that first added the filled plan. A later committed deviation-log entry
does not move that identity. A run without a plan must pass `--exploratory`; its
rows are labelled exploratory, it writes no history row, and it is never cited as evidence. The gate
checks those fields only; every other section is still required by the evidence standard.

Replace each `<...>` with a value. A section that does not apply says "none" and why; a blank or a
leftover `<...>` fails the check. SM-2's values are filled in as defaults; change one only by
recording the change and its reason here, before the first trial.

After the first trial the sections above the deviation log are frozen. A change after that point is
an appended entry in the deviation log, never an edit above it.

---

## Run

- **Question:** <one sentence: what this run decides>
- **Author role:** <maintainer, contributor>
- **Date registered:** <YYYY-MM-DD>
- **Task manifest:** `benchmarks/tasks.json` at commit `<sha>`; or, for an evaluator pack, `<name>`
  version `<version>` at commit `<sha>`, digest `<sha256>`, set `<set>`, as `replay --dry-run`
  prints them
- **Arms:** <harness at tag or commit, bare>
- **Model, CLI and effort:** `<exact model ID>`, `<CLI version>`, `<effort>`

## Hypotheses

- **Primary:** the harness lowers Cost-of-Pass against bare, with an expected ratio of 0.85, and does
  not lower the pass rate by more than the non-inferiority margin δ.
- **Secondary:** <each further hypothesis, with its direction, or "none">
- **Exploratory:** <each analysis that will be reported but supports no claim, or "none">

## Primary metric

- **Metric:** Cost-of-Pass ratio, harness over bare, pooled across the set: the total cost of every
  attempt divided by the total number of passes, per arm.
- **Interval:** paired, task-clustered 95% interval, by <task-clustered paired bootstrap with N
  resamples and seed S, or the delta method>.
- **Undefined case:** if either arm passes nothing, the result is reported as a pass-rate result only.

## Guardrails

- **Contamination control:** `installed-checkout-oracle-and-transcript-v1`; every task must pass the
  prelaunch installed-checkout check, and any scored transcript with an observed tool-input path
  that normalizes under `/opt/model-citizen` fails that attempt. List every task excluded by this
  control and its manifest reason; do not treat the transcript check as proof against unknown
  symlinks, relative traversal or copies.
- **Pass rate:** non-inferiority margin δ = 0.125 on the paired, task-clustered pass-rate difference
  (harness minus bare).
- **Fallback rate:** <the share of trials on an unpinned model above which the run is reported as
  compromised>
- **Spend:** <the per-trial budget and the whole-run cap>
- **Other:** <each further guardrail metric and its bound, or "none">

## Sample size

- **Tasks:** <k>, of which <n> are long multi-turn tasks.
- **Long tasks:** a task is long when its absorbed-call size is above 7.6, FR-34's upper
  break-even. Its absorbed-call size is the median, over its clean bare-arm runs, of the row's
  `gather_calls`: `Read`, `Grep` and `Glob` calls in every thread, the calls a subagent could
  absorb. Name the run whose rows confirmed each long task, or "pilot pending" for a pilot.
- **Trials per task and arm:** <m>, five or more.
- **α and power:** α 0.05 two-sided, joint power 0.8 on both tests of the decision rule, assuming a
  true ratio of 0.85 and equal pass rates.
- **Claim power:** <the joint power, at this k, n and m, of all three conditions a saving claim
  needs: both tests of the decision rule and the long-task subset's interval win. Size for 0.8, or
  state the lower figure and why.>
- **Minimum detectable effect:** <at most 15%>
- **Variance source:** <the pilot rows or earlier run the power analysis used, with its
  intra-cluster correlation>
- **Power calculation:** <the command that produced k, n and m for the decision rule and for the
  claim, and its output: `python3 scripts/replay_power.py --pilot <results dir> --have <k> <n> <m>`,
  or the formula used instead>

## Stopping rule

- **Fixed sample:** the run stops when every task has <m> trials per arm, and no result is read
  before then. <Or: the sequential design, its looks and its spending function.>
- **Early stop for harm or cost:** <the condition, or "none">
- **Stop condition for the claim:** a saving claim needs the hypothesis supported on the whole set
  and a win on the long-task subset, a win meaning the subset's ratio interval lies wholly below 1.0.
  Without that win the instrument findings are published as the result.

## Multiplicity

- **Decision rule:** both conditions must hold, so the two tests form one joint test and need no
  correction.
- **Further confirmatory tests:** <each one, with its correction, such as Holm across the family, or
  "none">
- **Everything else:** exploratory, labelled so, and supports no claim.

## Decision rule

The hypothesis is supported only when both hold:

- the paired, task-clustered 95% interval on the Cost-of-Pass ratio lies wholly below 1.0;
- the lower bound of the paired, task-clustered 95% interval on the pass-rate difference (harness
  minus bare) is above −δ.

The result is published with its intervals, whatever it shows.

## Exclusions

- **Analysis population:** every assigned trial, crashes, timeouts and fallbacks included.
- **Pre-stated exclusions:** <each rule that removes a trial, decided now, or "none">

## Deviation log

Append a dated entry for every change after the first trial: what changed, why, and which figures
it touches. Never edit an entry.

- <YYYY-MM-DD: none yet>

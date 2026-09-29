# Evidence standard

Model Citizen is a diagnostic tool: every claim it makes, about an agent or about itself, is only as
good as the experiment behind it. So every benchmark, pilot, replay, A/B, ad hoc eval, or test of
cost, efficacy or a change to the system follows the experiment protocol below, as a controlled
experiment in fresh containerized installs. This applies to an ad hoc eval in a session as much as
to a release.

A published result is a claim about what the harness does, measured against Claude Code with no
harness at all. After the protocol, this page lists the twelve things every published proof set
carries, and for each one what satisfies it. A proof set that misses an item makes no claim on that item's ground. An item
that genuinely does not apply is marked not applicable with its reason; a blank is a miss.

The cost comparison is defined by SM-2 in the product requirements. Where SM-2 defines a term, this
page quotes it rather than restating it, and the quotation governs. The replay that produces the rows
is described in [cost benchmarks](benchmarks.md). Individual spikes follow the lighter record in
[spikes](spikes/README.md) for what they write down, but a spike that measures cost, efficacy or a
change to the system runs under the protocol or is labelled exploratory.

## The experiment protocol

An arm contains exactly its named components and nothing else: "Model Citizen only" against "Model
Citizen plus Superpowers" means those two installs and no other layer, profile or host state.
Containers are mandatory. Nothing runs from a host profile, a live checkout or a disposable home: a
temporary `HOME` on the host is still the host.

1. **Question and hypothesis first.** Before any run: the question, the hypothesis, the primary
   metric, the decision rule and the sample size, pre-registered from the
   [pre-registration template](pre-registration-template.md) and committed with a date. A run with
   no pre-registration is exploratory and is never cited as evidence.
2. **A controlled environment.** Each arm is a fresh container built from pinned inputs: base image
   digest, CLI version, harness tag or commit, and every added component with its version. Nothing
   comes from the host: no host `HOME`, profile, environment, hooks or settings, and network egress
   only to the model API.
3. **Exactly the named components.** Each arm declares its components; the build writes a manifest
   of every installed file, plugin, hook, rule and setting, hashed, and the run refuses to start
   unless the manifest equals the declaration.
4. **One variable at a time.** Arms differ only in the declared treatment. Model, effort, CLI
   version, prompts, task set and snapshots, tool permissions, budget caps, price table and run
   window are held constant and recorded. A multi-factor design (two-by-two, factorial) declares
   every factor.
5. **Controls.** A bare arm with no harness, and a pinned baseline tag, in every comparison.
6. **Randomization and counterbalancing.** Arm and task order come from a recorded seed, with trials
   interleaved so time-of-day and API drift fall on both arms.
7. **Replication and power.** Five or more trials per task and arm, or the pre-registered power
   analysis if larger; intervals as SM-2 defines them; no claim from a single run.
8. **Blind scoring.** Deterministic checks where possible; a judge sees no arm label, and a judged
   metric needs its measured agreement (Cohen's kappa) against human labels before it counts.
9. **Idempotent and reproducible.** Rebuilding an arm from its recorded inputs yields the same
   manifest; every row records the digests, seeds, versions and command lines needed to replay it;
   raw outputs are kept and results are append-only.
10. **Intention to treat.** Every attempt counts, crashes and timeouts as failures with their cost;
    no post-hoc exclusion; a departure from the pre-registration is reported as a deviation.
11. **Report whatever it shows.** Results publish with their intervals and a "what we do not claim"
    section; null and negative results are results.

A check that cannot meet the protocol is labelled exploratory and is never cited as evidence, in a
result, a release note, a pull request or a decision. The eval entry points enforce this.
`scripts/cost_bench.py replay` refuses to start without `--pre-registration <path>`, a committed,
dated plan with its required fields filled, unless it is given `--exploratory`, in which case every
row it writes is labelled exploratory and it writes no history row. Each arm is a container image
built from pinned inputs, and the replay refuses to launch one whose manifest differs from its
declaration, which holds settings, hooks, rules, skills, agents, plugins or instruction files no
declared component supplies, or which names your home directory, your profile or the checkout the
run was started from in its recorded inputs, a mount or a variable. [Cost benchmarks](benchmarks.md)
describes the arms, their manifests and `arms check`, which builds each arm twice and compares the
manifests.

## How a proof set is checked

- Every item below is present, or marked not applicable with a reason this page allows.
- The run met every point of the protocol above, and the rows record how.
- The pre-registration (item 1) was committed before the first trial, and the history shows it.
- Every published figure can be re-derived from the proof set's own rows without calling a model.

A verifier that checks these mechanically is planned for proof set 1 (#799). Until it ships, the
check is done by a reviewer against this page.

## The twelve items

### 1. A pre-registered plan

**What it is:** hypotheses, primary metric, guardrails, sample size, stopping rule and multiplicity,
written down before the run.

**Satisfied by:** a filled copy of the [pre-registration template](pre-registration-template.md),
committed to this repository before the first trial of the run it governs. Every section of the
template has content or an explicit "none" with a reason. The order is checkable: the committer date
of the commit that adds the filled plan precedes the start time of the earliest trial in the proof
set's rows, and that commit is an ancestor of the commit the run was taken at. Those two checks order
the commits, but whoever commits sets the committer date, so they do not prove the plan was public
before the run. Merge the plan's pull request before the first trial: the check also compares the
merge time GitHub records for that pull request with the start of the earliest trial. An edit after
the first trial does not change the plan; it is an appended, dated entry in the plan's deviation log,
and the published result lists every deviation.

SM-2 fixes the defaults the plan starts from. Its hypothesis:

> **Pre-registered hypothesis:** the harness lowers Cost-of-Pass against bare, with an expected ratio of
> 0.85, and does not lower the pass rate by more than the non-inferiority margin δ. δ is fixed at 0.125,
> one task's share of the original eight-task set, and does not shrink as the set grows.

Its power statement, which the sample-size section of the plan answers:

> **Power:** five or more trials per task and arm, and α 0.05 two-sided. The task set and the trial
> count are sized for a joint power of 0.8 on both tests of the decision rule, assuming a true ratio of
> 0.85 and equal pass rates. The minimum detectable effect is stated before the run and is at most 15%.
> The set includes long multi-turn tasks.

Its decision rule:

> **Decision rule:** the hypothesis is supported only when both conditions hold:
> - the paired, task-clustered 95% interval on the Cost-of-Pass ratio lies wholly below 1.0;
> - pass-rate non-inferiority holds: the lower bound of the paired, task-clustered 95% interval on the
>   pass-rate difference (harness minus bare) is above −δ.

The two conditions are one joint test, since both must hold, so they need no correction for
multiplicity. Any further test that could support a claim, such as the long-task subset or a
per-module figure, is named in the plan with its correction, or is labelled exploratory and supports
no claim.

### 2. A frozen task set with a reference solution per task

**What it is:** the tasks are fixed before the run, each has a solution known to pass, and the set is
audited for task validity and outcome validity.

**Satisfied by:** a task manifest pinned by commit, which the plan names. In this repository that is
`benchmarks/tasks.json`, where each task names the commit it starts from and a `good_sha` that solves
it. Every task passes three audits, recorded in the proof set:

- **Task validity:** the prompt is enough to do the task, and the reference solution is reachable
  from the starting commit with the tools and access both arms have.
- **Outcome validity:** the hidden check passes on the reference solution and fails on the unchanged
  starting commit. `python3 scripts/cost_bench.py replay --verify-tasks` proves both for every task
  without calling a model.
- **No change after the plan:** the manifest the run used is byte-identical to the one the plan pinned.

### 3. Pinned model, CLI, effort, date, container, seeds and fallback rate

**What it is:** everything that would change the result if it drifted.

**Satisfied by:** per trial, the exact model ID (never an alias), the CLI version, the effort setting,
the date and time the trial started, the container image digest, and every seed the harness controls,
such as task order and the bootstrap. Model sampling is not seedable, so the record says so rather than
implying it is. The fallback rate is the share of trials that ran on a model other than the pinned one,
read from each trial's own transcript; those trials stay in the rows and are counted, never dropped.
Every trial ran in a container, as protocol point 2 requires, and records the image digest, the
harness tag or commit and the arm's manifest hash; a trial without one is exploratory and is not
part of a proof set.

### 4. A dated price table

**What it is:** the prices every cost figure was computed with.

**Satisfied by:** a table in the proof set with the price per million tokens for input, output, cache
writes and cache reads, per model, with the date it was read and the page it was read from. Cost
figures are computed from token counts and this table, so a later price change is a re-derivation, not
a rewrite of the rows.

### 5. Paired per-task results with intervals and the intra-cluster correlation

**What it is:** every task's figures for both arms side by side, the headline intervals, and how
strongly trials of the same task agree.

**Satisfied by:** a row per task and arm with its trials, passes and cost, and the headline intervals
SM-2 defines:

> The paired, task-clustered 95% intervals on the Cost-of-Pass ratio and on the pass-rate difference
> govern the comparison. They come from a task-clustered paired bootstrap, which resamples tasks and
> keeps both arms' trials of a task together, or from the delta method.

> Wilson or Bayesian intervals describe each arm's own pass rate. They are descriptive only, as are
> the per-task figures reported beside the headline.

The intra-cluster correlation of pass and of cost within a task is reported beside them, with the
design effect it implies, `1 + (m − 1) × ICC` for `m` trials per task, so a reader can see how much
the trials per task added.

### 6. A cost-effectiveness view

**What it is:** cost and pass rate shown together, so a cheaper arm that passes less is visible as
such.

**Satisfied by:** a chart or table placing each arm, and each configuration when there are more than
two, by pass rate against mean cost per attempt, with the Pareto frontier marked and any dominated
configuration named. The ratio it sits beside is SM-2's:

> Each arm's Cost-of-Pass pools the set: the total cost of every attempt divided by the total number
> of passes. The ratio divides the harness figure by bare's. If either arm passes nothing, the ratio
> is undefined and the result is reported as a pass-rate result only.

### 7. Trajectories and a command that reproduces the run

**What it is:** what each agent actually did, and a way to do it again.

**Satisfied by:** every trial's transcript in the proof set, failed, timed-out and fallback trials
included, redacted of credentials and personal paths but not shortened. One stated command re-runs
the whole schedule from the pinned commit and manifest, and one re-derives every published figure from
the rows without calling a model.

### 8. Judge agreement

**What it is:** evidence that any judged outcome is judged the way a person would judge it.

**Satisfied by:** for every outcome decided by a model or a person rather than a deterministic check,
Cohen's κ against hand labels on a sample the plan sized, the confusion matrix, and bias audits for
position or order, length, arm identity (the judge cannot tell which arm produced the output) and
self-preference (a judge from the same model family as an arm). When every outcome is a deterministic
check, as the replay's hidden checks are, the item is marked not applicable with that reason.

### 9. A contamination check

**What it is:** evidence that neither arm could see the answer, and that the two arms had the same
access.

**Satisfied by:** a record that the reference solutions were unreachable from inside a trial (no
later history in the task checkout, no known-good commit or already-passing oracle in the harness
arm's installed checkout, no network path to the solution), that scored transcripts name no read
from the installed checkout, that both arms had identical web and network access, and the task
dates beside the model's stated training cutoff. The installed-checkout check runs before any model
call; a detected literal-root transcript read fails that attempt. This transcript check is not a
complete filesystem-read audit because aliases and copies can hide the source path, so it supports
rather than replaces prelaunch exclusion. SM-2 records why this item exists:

> The 23 Sep eight-task runs were unscored and ran with unequal web access between the arms, so they
> are not a result.

Tasks exposed through the installed checkout are excluded with their full task and reason retained
in `benchmarks/tasks.json`. The runner repeats the control at the exact harness commit, so an older
tag or a later task-manifest change fails closed instead of relying on the retirement record.

### 10. Estimand labels

**What it is:** each figure says which effect it estimates.

**Satisfied by:** a label on every published figure:

- **Intention to treat:** every trial as assigned, including crashes, timeouts and fallbacks. This is
  the headline.
- **Adherence:** only trials in which the treatment actually happened, such as the mechanism under
  test firing. SM-4 governs credit: "No saving is credited to a mechanism unless the ledger rows show
  it fired."
- **Complier effect:** the effect among trials where the mechanism would fire, estimated from
  assignment rather than by selecting those trials after the fact.
- **Hypothetical:** any figure that is computed rather than measured, such as a what-if price or a
  projected subset, is marked hypothetical where it appears.

### 11. Field checks

**What it is:** checks that matter once a comparison runs on real sessions rather than a replay.
Real sessions run on a host rather than in the protocol's containers, so such a comparison is
exploratory and supports no claim; these checks still describe it.

**Satisfied by:**

- **Sample ratio:** the observed count per arm is tested against the assigned split, and a mismatch
  at p below 0.001 stops the analysis until it is explained. A replay applies this to planned against
  completed trials per arm.
- **Novelty:** the effect is reported by exposure period, so an early effect that fades is visible.
- **CUPED:** when a pre-period covariate is used to reduce variance, the adjusted and the raw figures
  are both reported, and the covariate is named in the plan.
- **Dilution:** the share of sessions the treatment could not have affected is reported, with the
  effect both across all sessions and across the triggered ones.

A replay-only proof set marks novelty, CUPED and dilution not applicable, with that reason.

### 12. What we do not claim

**What it is:** a section in the published result that names the claims its data does not support.

**Satisfied by:** a section headed "What we do not claim" that at least names the models, runtimes,
task kinds and magnitudes the result does not cover, every mechanism that is not credited, and the
outcome of SM-2's stop condition when it applies. SM-2 bounds the magnitude:

> The result is published with its intervals, whatever it shows. A magnitude is claimed only as far as
> the interval supports it: "at least 15% cheaper" needs the interval's upper bound at or below 0.85.

## Sources

SM-2, SM-4 and NFR-15 in the product requirements, and the sources SM-2 cites for Cost-of-Pass,
clustered and paired intervals, the delta method and small-sample pass-rate intervals.

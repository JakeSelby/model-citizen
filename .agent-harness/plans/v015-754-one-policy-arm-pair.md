# One-policy replay pair: parity refusal and per-arm cost with decision calls

> This adds a pair mode to the container replay. One harness image runs twice, as a reference arm and a treatment arm, and the two differ in exactly one declared session selection. The bare arm runs beside them, and a parity check refuses any other difference between the two harness arms. Each arm's summary reports passes, pooled cost per passed task, wall time and re-spawns on a stronger model class, and adds the arm's own decision-provider calls from the usage ledger copied out of its container, joined by session id, with unpriced calls named.
> **Effort** medium, seven steps, no spend · **Risk** medium: the two-arm assumption runs through `cost_bench.py` and `replay_stats.py` · **Blast radius** the replay runner, its docs and tests; the two-arm path stays as it is

## At a glance

- **Outcome** — story ACs 1 to 4 met on fixtures and fake launchers, plus two proposed ACs (bare arm in every pair, paired intervals on the pair), so #797 can extend the pair into its two-by-two.
- **Approach** — a committed ablation manifest (AD-12) names the tag and the one factor. `replay --pair` builds one harness image and runs it with each factor value, set by value. `summarise` then joins each run's copied decision rows by session id.
- **Touches** — `scripts/cost_bench.py`, `scripts/replay_arms.py`, `scripts/replay_stats.py`, new `scripts/replay_pair.py` and `tests/test_replay_pair.py`, `docs/benchmarks.md`, the story file, one PRD line.
- **New deps** — none. Standard library only; `decisions/ledger.py` and `policy/hooks/pricing.py` are read, not changed.
- **Not in scope** — any live pair run (none is registered, and `benchmarks/tasks.json` is empty); promoting #145; four-arm or superpowers parity (#558); pairs that differ at build time.
- **Exit test** — `python3 -m unittest discover -s tests -v` passes with the new pair tests, and a dry run on a fixture task file prints a three-arm schedule.
- **Open question** — it is unknown whether a decision point's stage can be set per session. If it cannot, #145's pair needs a build-time selection, which decision 1 leaves out.

## System design

```text
*ablation manifest ── tag, factor, two values ──▶ *replay --pair
                                                       │ two arm specs
                                             *parity check (refuses)
          ┌───────────────────┬────────────────────────┤
          ▼                   ▼                        ▼
      bare arm         *reference arm           *treatment arm
          └────── rows ───────┴── rows, copied ledger ─┘
                              ▼
      *decision join ── session id, dated price table
                              ▼
      *pair summary ── passes, cost per pass, wall time, re-spawns
```

`*` marks new or changed parts. Both harness arms run one image, with the factor set by value. After the run, parity also compares #482's loaded-surface record trial by trial.

## Steps

1. **[Read spawns and session ids from the stream](#step-1--read-spawns-and-session-ids-from-the-stream)** — `parse_result` in `cost_bench.py` adds `session_ids` and a list of spawns (brief fingerprint and thread model). Also read #482's record fields on main.
   *Exit:* on a stream fixture where one brief is spawned on haiku and then on sonnet, a test finds one re-spawn at a higher class and the stream's session id.
2. **[Ablation manifest and parity refusal](#step-2--ablation-manifest-and-parity-refusal)** — new `scripts/replay_pair.py` loads and validates the pair file, builds each harness arm's spec and refuses any difference except the factor.
   *Exit:* tests show that a pair differing only in the factor passes, and that two differing keys, another image id, a disallowed factor or equal values are each refused by name.
3. **[Three-arm pair schedule](#step-3--three-arm-pair-schedule)** — `replay --pair` runs bare, reference and treatment, rotating the lead arm. Rows carry the pair, the factor and its value. `--spend-cap` is required, and no history row is written.
   *Exit:* a dry run on a fixture task file prints tasks × reps × 3 lines, and the existing replay tests pass unchanged.
4. **[Copy the ledger out of each harness container](#step-4--copy-the-ledger-out-of-each-harness-container)** — per decision 3, the container is kept after exit, the usage ledger is copied out, then the container is removed. A ledger that cannot be read is recorded as unknown, not empty.
   *Exit:* a fake-launcher test sees run, copy and remove in that order, including the timeout path, and no copy is attempted from the bare arm.
5. **[Decision-call roll-up](#step-5--decision-call-roll-up)** — decision rows join to the arm's rows by session id and are priced with `pricing.row_cost` against the runner's dated table. Their latency is reported as a share of wall time.
   *Exit:* on fake rows, priced cost adds to the arm; a `partial` row is named and leaves the with-decisions figure undefined; a foreign session id is counted and named unmatched.
6. **[Per-arm pair summary and post-run parity](#step-6--per-arm-pair-summary-and-post-run-parity)** — `summarise` reads pair rows and reports, per arm, passes, cost per passed task (workers alone and with decisions), wall time and re-spawns at a higher class. Decision 5's intervals are included.
   *Exit:* on fixture results, `summarise` prints every field for each arm, and a pair whose #482 records differ is refused with the trial named.
7. **[Docs, story, amendments and gates](#step-7--docs-story-amendments-and-gates)** — a Pairs section in `docs/benchmarks.md`, the story's design filled in, dated PRD and AD-12 notes, then CI's commands.
   *Exit:* `bin/harness lint` reports 0 findings, the unittest suite is green and `bmad_issue_sync.py audit` is clean.

## Decisions for the reviewer

> **1. What may the one policy be?**
> *Recommend* one session-scoped selection variable (`HARNESS_STANCE_<dimension>` or `HARNESS_MODE`) on one shared harness image. Parity is exact by construction, and no new image variant is built.
> *Alternative* a declared build-time selection, meaning two images that differ in one input. #797's minimal profile and any switch that exists only in config need this, but parity would then have to account for a difference in the manifest.

> **2. Does every pair run include the bare arm?**
> *Recommend* yes, three arms per trial. Point 5 of the evidence standard requires a bare control in every comparison, and #797's four cells include bare anyway.
> *Alternative* two arms, labelled exploratory or covered by an amendment to point 5 for within-harness pairs. Each trial then costs a third less.

> **3. How do the ledger rows leave the container?**
> *Recommend* run without `--rm`, `docker cp` the usage ledger after exit, then remove the container. The one-mount rule stays true, and the agent cannot write to the host.
> *Alternative* mount a fresh, empty directory per run over the ledger's folder in every arm. This is simpler and survives a kill, but it adds a writable host mount to the fence.

> **4. Open the arms' egress to a remote decision provider now?**
> *Recommend* no. The allowlist stays the model API alone, and a remote call cannot complete and is reported unpriced, never free. Opening egress waits for the first registered pair that needs it (#145, backlog), with its own protocol amendment.
> *Alternative* add a declared provider host, and its credential passed by name, to both harness arms now, and amend protocol point 2 in this change.

> **5. Should the pair get #795's paired intervals now, or leave them to #797?**
> *Recommend* now. A cost per passed task without an interval cannot back a promotion criterion (FR-49), and #797's AC2 needs `replay_stats` to accept any pair of arms.
> *Alternative* keep the story to its four ACs and let #797 generalise the statistics. This change then stays smaller.

## Risks

- **#482's record, as merged, lacks a field parity needs:** compare what it records, write the gap into the story, and invent no field.
- **A failed remote provider call writes no ledger row** (not verified): report the arm's decision cost as unknown whenever its configuration names a remote provider, never as zero.
- **The pair rewiring breaks the two-arm path that #560's proof run relies on:** pair mode is a separate path, and any edit to an existing replay test stops the build for review.

---

# Addendum

## Acceptance criteria as built, and proposed changes

- **AC1** (parity refusal) — step 2 compares specs before launch, and step 6 compares #482's loaded surface after the run.
- **AC2** (passes, cost per passed task, wall time, re-spawns at a higher band) — steps 1 and 6. "Cost per passed task" is SM-2's pooled Cost-of-Pass (`replay_stats.cost_of_pass`), the unit #795 made the headline. "Higher band" is read as a stronger model class, since the delegation skill uses "band" for work and "class" for models.
- **AC3** (decision calls counted, unpriced calls named) — steps 4 and 5. Proposed rewording, so latency is not counted twice: "their ledger cost adds to the arm's cost, and their latency is reported as the share of the arm's wall time it already contains".
- **AC4** (fake-launcher tests) — every test lives in `tests/test_replay_pair.py`, reusing `Launch`, `options`, `result` and `TASK` from `tests/test_cost_bench.py`.
- **Proposed AC5**, if decision 2 is taken: **Given** a pair, **when** it is replayed, **then** the schedule holds bare, reference and treatment, with the lead arm rotating across all three.
- **Proposed AC6**, if decision 5 is taken: **Given** a finished pair, **then** the treatment-over-reference Cost-of-Pass ratio and the pass-rate difference carry #795's paired, task-clustered 95% intervals, and each harness arm's ratio to bare is reported beside them. No SM-2 verdict is printed, because SM-2's decision rule is defined for harness against bare only.

## Dependencies on other issues

- **#559 (completed).** It built the container arms and deferred pair parity to a four-arm follow-up blocked by #558 (AH-S202, "Not here"). So this story builds parity for pairs of harness arms itself. The story's notes "once #559 lands" and "not on `main` yet" are stale, and step 7 rewrites them.
- **#482.** The brief says it is merged. It is not in the checkout this plan was read from, where its story file is an unfilled stub. Step 1 reads its field names on main.
- **#795 (merged).** Provides `scripts/replay_stats.py`; decision 5 applies.
- **#797 builds on this story.** It reuses the ablation manifest, the parity check and the N-arm schedule, and adds a fourth cell and a second factor. If decision 1 takes the recommendation, #797 also owns build-time selection.
- **#796 and the task set.** Any live pair run waits for a contamination-safe set. The v0.15.0 evidence-run plan lists #754 runs as out of scope, so a pair run needs its own pre-registration and a new entry in that sequence. Its cap formula follows the runner's defaults: k tasks × m trials × 3 arms × 2 USD, plus 3 × 0.25 USD of preflights. k and m are unknown.
- **#145 (backlog).** This is the consumer the story names. Its pair needs the recommendation's stage to be selectable per session (unknown) and needs a remote provider's egress (decision 4).
- **#799.** No dependency. Pair rows are not proof-set rows.

## Amendments this plan flags

- **PRD §9, the 2026-09-24 amendment, v0.15.0 entry (`prd.md:1746`).** It reads "the two-by-two unit design (#754)". The sprint change proposal says "#754, the one-policy pair, extended to the 2×2" and gives the two-by-two its own item (N10), and #797 now owns the two-by-two. Step 7 appends a dated correction under the amendment rather than rewriting the line: "the one-policy pair (#754) and the two-by-two unit design (#797)".
- **Architecture spine, AD-12.** It already says ablations are declared in an ablation manifest that lists arms. No rule changes. Step 7 appends a dated note naming `benchmarks/ablations/` and schema 1.
- **Evidence standard.** No change if decisions 2 and 4 take the recommendations. Decision 2's alternative amends point 5, and decision 4's alternative amends point 2.
- **`docs/benchmarks.md`.** The bullet "A run is `docker run --rm` with one mount" changes for pair harness runs under decision 3. The mount count stays one.

## Step 1 — Read spawns and session ids from the stream

- **Session ids.** Stream-json messages carry `session_id` (see the `hook()` fixture in `tests/test_replay_parity.py`). `parse_result` collects the distinct values, in order, as `session_ids`.
- **Spawns.** For each assistant `tool_use` block whose name is in `SPAWN_TOOLS`, record `{id, fingerprint, requested_model}`:
  - `fingerprint` is `harness_core.lifecycle.fingerprint(input["prompt"])`, the same reduction the spawn guard uses;
  - `requested_model` is `input.get("model")`.
  The thread's model is the `message.model` of the first assistant message whose `parent_tool_use_id` is that id. `parse_result` already walks those messages for `first_turns`.
- **Class order, a default taken:** `light < standard < strong < frontier`, from `tiers` in `adapters/claude-code/bindings.json` in the runner's checkout. A model id is matched to a tier by family name (haiku, sonnet, opus, fable). One table serves every arm, so ranking cannot differ between arms. A model that matches no tier, or a spawn whose thread produced no assistant message, is `unranked`. It is counted and named, never ranked higher or lower.
- **Re-spawn at a higher class:** a later spawn in the same run whose fingerprint is `lifecycle.same_work` with an earlier spawn's, and whose class ranks above it. The row gains `respawns_up` and `spawns_unranked`. Both are `None` when the output is not stream-json, never 0.
- **#482.** On main, read the field names #482 writes on each replay row, and whether it refuses before launch on plugin drift. Record them in the story's Dev notes before step 6.
- **Tests:** build the stream in the test, as `stream()` in `tests/test_replay_parity.py` does. Cover:
  - same brief on haiku, then sonnet: counts 1;
  - same brief on the same model: counts 0;
  - different briefs: count 0;
  - an unknown model: counts as unranked;
  - older single-document output: gives `None`.

## Step 2 — Ablation manifest and parity refusal

- **File:** `benchmarks/ablations/<name>.json`. This story commits no real one; examples are test fixtures.

```json
{"schema": 1, "name": "delegation-off", "tag": "<release tag or full sha>",
 "factor": "HARNESS_STANCE_DELEGATION", "reference": null, "treatment": "off"}
```

- **Validation, in `replay_pair.load(path)`:**
  - `factor` matches `^HARNESS_STANCE_[A-Z_]+$` or is exactly `HARNESS_MODE`;
  - `reference` and `treatment` are strings or `null`, where `null` leaves the tag's default;
  - the two values differ;
  - `tag` is one ref.
- **Effective difference.** `cost_bench.arm_profile` is resolved for both envs. If the two fingerprints are equal, the factor selects nothing the resolver sees, so the pair is refused. If either is `None`, the pair is refused as unknown.
- **Spec per harness arm:** image id, declaration and manifest digests, harness commit, model, run cap, the `arm_command` argv with the prompt replaced by a placeholder, the env by value (`arm_env`), network, proxy URL, credential name, the protocol stamp, and digests of the task file and the price table.
- **Parity.** `replay_pair.parity(reference, treatment, factor)` returns one reason for each differing key, and one for each env variable other than `factor` that differs. It also refuses when `factor` does not differ.
- **When it runs.** In `_replay`, after `arms.admit` and before `probe_workdirs`, so a refusal comes before any spend.
- **Flags refused with `--pair`:** `--stance-cost` (the factor is the only override); more than one `--tag`; a `--tag` different from the manifest's.

## Step 3 — Three-arm pair schedule

- **Rotation.** `schedule(tasks, reps, arms=ARMS)` rotates the lead arm: for rep r, the order is `arms[i:] + arms[:i]` with `i = (r - 1) % len(arms)`. For the two default arms this gives exactly today's alternation. Existing tests prove it.
- **Arm names in pair rows:** `bare`, `reference` and `treatment`, plus `pair` (name and file sha256), `factor` and `factor_value`. `opts["arms"]` maps `reference` and `treatment` to the one harness record.
- **Env.** `replay_arms.arm_env` gains a `selection` dict, applied only to non-bare arms and passed by value, so `host_path_reason` still guards it. `cost_bench.arm_env` passes the arm's factor value through it.
- **Loops.** `preflight` and `probe_workdirs` loop over the arm names in `opts`, not over `ARMS`.
- **Outputs.** A pair writes `results.jsonl` and no history row. `history.jsonl` is the harness-over-bare series. `--pre-registration` or `--exploratory` is still required.
- **Spend cap.** `--spend-cap` is required with `--pair`. The 140.50 USD default is sized for two arms (`docs/benchmarks.md`, the defaults bullet), and a three-arm default would be a figure for a set that does not exist.
- **Contamination.** The check runs once, for the shared commit.
- **Test:** write a one-task fixture file from `TASK` into a temp dir. Run `BENCH.main(["replay", "--tasks", f, "--pair", p, "--model", "m", "--reps", "2", "--exploratory", "--dry-run"])` and assert 6 schedule lines and the bare, reference, treatment rotation.

## Step 4 — Copy the ledger out of each harness container

- **Sequence, for pair harness arms only.** The two-arm path is not changed.
  - `docker run --name <n> ...` without `--rm`;
  - `docker cp` the usage ledger from the stopped container to `<run tmp>/usage.jsonl`. Its in-container path is `usage_path()` in `policy/hooks/usage-log.py` under the image user's home directory (AH-S202's completion notes name that user `agent`); confirm the path in the image;
  - `docker rm --force <n>`.
- **On timeout:** `docker kill <n>`, then copy, then remove, where `kill_command` removes by name today.
- **Row field `decision_ledger`:**
  - `read`;
  - `absent`, for the bare arm, which is never asked for a ledger;
  - `unknown: <reason>`, when the copy fails or the file is missing. The harness arm's usage-log hook writes session rows, so a missing file means something failed, not that no call was made.
- **Kept rows.** Only `kind: "decision"` rows are kept, read with `ledger.rows(path)`, and written to `<out>/decisions/<task>-<arm>-<rep>.jsonl`. Those rows carry no prompt, path or prose, according to ledger.py. Session rows are discarded. The saved files are the re-derivation input for step 6.
- **Tests:** the `Launch` fake records the calls. Assert the order; the timeout order; no `cp` for bare; `unknown` when the fake `cp` fails.

## Step 5 — Decision-call roll-up

- **Function.** `replay_pair.decision_rollup(rows, decisions, table)` returns, per arm: `calls`, `priced_usd`, `unpriced` (task, rep, point, model and reason for each), `unmatched` and `decision_seconds`.
- **Pricing.** `pricing.row_cost(row, table)` from `policy/hooks/pricing.py`, loaded by path as the scripts load their siblings. The table is `policy/prices.json` `models`, the same one the replay stamps. `None` means unpriced, with the reason `partial` or `no price for <model>`.
- **Joining.** A decision row's `session_id` is matched against the attempt row's `session_ids`. Every row in a container came from that arm's run, so an unmatched row still counts in the arm and is named `unmatched`. Whether a subagent's hook calls carry the parent's session id is unknown, and naming such rows makes that visible.
- **Arm cost with decisions** is the worker cost plus the priced decision cost. It is `null` when any decision row is unpriced or any row has `decision_ledger: unknown`, and the cause is named. The worker-only figure is always shown beside it.
- **Latency.** Calls run inside hooks during the run, so `ms` is already inside `wall_seconds`. Report `decision_seconds` and its share of wall time, and never add it on top.
- **Tests:**
  - two priced rows add their exact cost;
  - one `partial` row nulls the figure and names the row;
  - a model missing from the table nulls the figure with its reason;
  - a foreign session id is counted and named;
  - latency is never added to wall time.

## Step 6 — Per-arm pair summary and post-run parity

- **Detection.** `cmd_summarise` treats the rows as a pair when their arm names are exactly `{bare, reference, treatment}` and calls `replay_pair.summarise(rows, decisions_dir, table)`. Otherwise the current path runs.
- **Per arm:**
  - attempts, passes, and pass rate with a Wilson interval (`replay_stats.wilson`);
  - Cost-of-Pass, workers alone and with decisions;
  - total wall seconds and the mean per attempt;
  - `respawns_up` and `spawns_unranked`, as totals and per attempt;
  - the roll-up from step 5.
- **Decision 5.** Wherever `replay_stats` uses its `ARMS` constant, it takes an `arms=(reference, treatment)` argument instead, defaulting to `("bare", "harness")`, so every existing test is unchanged. The pair summary calls `analyse` for treatment against reference and for each harness arm against bare, and prints intervals without a verdict. `pareto` takes the same argument, which gives the three-arm view.
- **Post-run parity.** For every task and rep, the reference row's #482 record must equal the treatment row's. Any mismatch makes `summarise` exit 1 and list each trial. `--json` gives `parity: {ok, reasons}`. The rows stay as they are.

## Step 7 — Docs, story, amendments and gates

- **`docs/benchmarks.md`.** A "Pairs" subsection under Live replay: the manifest, the allowed factors, three arms, parity before and after launch, the ledger copy, the costing rules, no history row, and the required spend cap.
  - Keep `tests/test_replay_parity.py` green: its `RUN_LINE` check reads every `# N tasks x N arms x N reps` comment in `docs/*.md` against `len(ARMS)`. Describe the pair schedule in words, never in that comment form.
- **Other files:**
  - `changelog.d/754.added.md`;
  - `_bmad-output/implementation-artifacts/AH-S230.md`: Design, Tasks and Files to touch filled from the approved plan, and a change-log line;
  - the dated notes on the PRD and AD-12 listed above.
- **Gates, from `.github/workflows/ci.yml`:**

```sh
python3 bin/harness lint                          # expect: 0 finding(s)
python3 -m unittest discover -s tests -v
python3 scripts/cost_bench.py static --check
python3 scripts/bmad_issue_sync.py audit
python3 scripts/smoke_tier.py --skip credentials
python3 scripts/detector_corpus.py --floor 0.9
"$FLOOR_PYTHON" -m unittest tests.test_python_floor -v
```

- **Floor.** Use no Python API newer than the floor interpreter CI names.

## What was checked, and what was not

- **Checked in this worktree:**
  - `ARMS` is hard-coded as `("bare", "harness")` in `cost_bench.py` and in `replay_stats.py:19`;
  - `run_command` gives `--rm` and a single mount (`replay_arms.py:492`), and the egress allowlist is `api.anthropic.com` alone (`replay_arms.py:50`);
  - Jev's endpoint `https://api.typesafe.ai/v1/systemone` (`jev.py:50`) is outside that allowlist;
  - the usage ledger path is fixed under home (`usage-log.py:208`);
  - decision rows carry `session_id`, `ms` and `partial` (`ledger.py:84-139`), and `row_cost` returns `None` for `partial` (`pricing.py:257`);
  - `spawns` is a count with no model (`cost_bench.py:671`);
  - AH-S202 deferred pair parity;
  - AD-12 requires an ablation manifest;
  - PRD line 1746 conflicts with the sprint change proposal's lines 155 and 156 and N10;
  - AH-S057 AC5 names this pair as the basis for #145's criterion;
  - #145 is in the backlog, per the sprint change proposal.
- **Not checked:**
  - #482's merged code, which is absent here;
  - whether a subagent's hook calls carry the parent's session id;
  - whether a failed remote provider call writes a ledger row;
  - whether a decision point's stage has a per-session override;
  - the image user's home path beyond AH-S202's note;
  - the live text of issue #754, since no shell was available. The story file's quoted criteria were used.
## Decisions taken

- 2026-09-29, at build: the recommended option of each decision: 1, one session-scoped selection on one image; 2, every pair includes the bare arm; 3, ledger rows leave by `docker cp` after exit; 4, no egress to a remote decision provider now; 5, #795's paired intervals now. The pair lands before #514's declared-arms runner, with a row schema (`ablation`, `selection`) that runner can generalise.

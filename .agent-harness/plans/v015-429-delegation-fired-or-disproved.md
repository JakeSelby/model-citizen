# Delegation firing verdict in the replay report, and the #429 close-out

> **Verdict.** This adds a per-task delegation verdict to the replay's rows and summary. Each task gets one of five verdicts: fired, declined below break-even, missed above break-even, spawn tool not offered, or unknown. With it, the registered #513 AC4 and #560 runs can close #429 as fixed or disproved without a paid run of #429's own. The verdict is built from three things the kept stream-json already holds, compared against FR-34's break-even: the init message's tool list, gather calls counted by thread, and spawns.
> **Effort** medium: one instrument PR, then a close-out that waits on two registered runs · **Risk** medium: the verdict is only as good as the absorbable-call definition · **Blast radius** replay rows gain fields and `summarise` and history output change; no hook, stance or SM-2 figure changes

## At a glance

- **Outcome** — #429 closes on registered rows in one of two ways. Fixed: the harness arm spawns on tasks above break-even, and the spawn's cost effect is shown beside it. Disproved: a recorded finding with per-task verdicts and the size at which delegation should fire.
- **Approach** — parse spawn-tool-offered, gather calls by thread and Workflow launches from kept output. Classify each task and arm, and print the result under the SM-2 lines, labelled adherence.
- **Touches** — `scripts/cost_bench.py`, a new `scripts/delegation_verdict.py`, two test files, `docs/benchmarks.md`, `docs/caught-in-the-act.md` and the AH-B095 story. At close-out, a dated FR-34 amendment in the PRD.
- **New deps** — none.
- **Not in scope** — the nudge itself (#513). Any paid or native run: every live trial comes from the evidence-runs plan, steps 6 and 8. Trimming or retiring the stance if the result is disproved, which becomes a follow-up issue.
- **Exit test** — `python3 -m unittest discover -s tests -v` passes with the new fixture tests, and `summarise` on the step-6 rows prints a verdict for every task.
- **Open question** — what counts as "fixed": FR-34's single spawn, or a rate (decision 2).

## System design

```text
claude -p stream-json, kept by --raw
      │ init tools · tool_use blocks per thread
      ▼
*parse_result ── spawn_offered, gather/absorbed calls, workflow ──▶ row
                                                                   │
break-even (pre-registration; FR-34's 7.6) ──▶ *delegation_verdict ◀┘
                                                     │ per task and arm
                        ┌────────────────────────────┴──────┐
                        ▼                                   ▼
            *summarise and history row           #799 bundle items 10, 12
                        │
                        ▼
              #429 close-out ──▶ *FR-34 amendment
```

`*` marks something new or changed: the parser fields, the verdict module, the report block and the FR-34 amendment. Nothing in this diagram calls a model.

## Steps

1. **[Record whether the spawn tool was offered](#step-1--record-whether-the-spawn-tool-was-offered)** — `parse_result` reads the `tools` list in the init message, and backfill applies it to #512's kept raw output at $0.
   *Exit:* three init fixtures give true, false and None; `backfill` on #512's raw output sets the field on every row.
2. **[Count gather calls by thread, and treat unknown as unknown](#step-2--count-gather-calls-by-thread-and-treat-unknown-as-unknown)** — `parse_result` and `STREAM_FIELDS`.
   *Exit:* a fixture with main-thread Reads, subagent Greps, a nested spawn and a Workflow call gives exact counts; output with only a result gives None, not 0.
3. **[Classify each task and arm](#step-3--classify-each-task-and-arm)** — `scripts/delegation_verdict.py`, a pure function over rows and a break-even figure.
   *Exit:* `python3 -m unittest tests.test_delegation_verdict` covers all five verdicts; a cell with a missing field reads unknown, never declined.
4. **[Report it beside SM-2](#step-4--report-it-beside-sm-2)** — `summarise`, `history_row` and `render_history`, plus a spawning-versus-non-spawning cost split labelled adherence.
   *Exit:* `summarise` on a fixture `results.jsonl` prints the block; the SM-2 result is identical to its output before the change.
5. **[Docs, story and gates](#step-5--docs-story-and-gates)** — `docs/benchmarks.md` and `docs/caught-in-the-act.md`; AH-B095 filled in from this plan.
   *Exit:* every CI command in the addendum passes on `HEAD`; this ends the instrument PR.
6. **[Name the verdict in two pre-registrations](#step-6--name-the-verdict-in-two-pre-registrations)** — the delegation-nudge and harness-vs-bare drafts (evidence-runs steps 6 and 8).
   *Exit:* `grep -l delegation_verdict benchmarks/preregistrations/*.md` lists both, and both are merged before their first trial.
7. **[Close #429 from registered rows](#step-7--close-429-from-registered-rows)** — read the verdicts once those runs finish, then amend FR-34, update the write-up, and draft the closing comment.
   *Exit:* `summarise --results <dir>` shows a verdict for every task in both runs, and the FR-34 amendment cites those rows.

## Decisions for the reviewer

> **1. What counts as an absorbable call?**
> *Recommend* Read, Grep and Glob only. The count is deterministic and does not depend on the grader, and because it undercounts, a "missed" verdict is conservative.
> *Alternative* also count Bash commands the shell grader rates grade 0. This is closer to how agents actually search, but it ties the verdict to the grader's version.

> **2. What counts as "fixed"?**
> *Recommend* a rate: the harness arm spawns in at least the share of runs that #513 AC4 pre-registers for five runs, on every task above break-even. FR-34 would be amended to match. One spawn in 25 or more runs would credit a mechanism that almost never runs.
> *Alternative* FR-34 as written, at least one spawn. It needs no PRD change and is exactly what SM-4 asks.

> **3. Does #429 need a registered run of its own?**
> *Recommend* no. Read the #513 AC4 and #560 runs, which are already sequenced and capped, so the verdict adds no spend.
> *Alternative* a registered forced-delegation arm across task sizes. It would measure the break-even instead of citing it, but it is a new arm type and new spend.

> **4. Does a Workflow-tool launch count as a spawn?**
> *Recommend* report it beside spawns but do not count it until #915 routes Workflow agents to a band. An unrouted launch is not what the tiered stance does (#578).
> *Alternative* count it now. It still removes work from the thread, and that is the saving FR-34 is about.

> **5. Where does the break-even figure come from?**
> *Recommend* FR-34's 7.6 as a pre-registered constant labelled hypothetical, with FR-34 amended to name its source. Its method is in unpublished notes, and a second model would be unvalidated.
> *Alternative* an estimator in code built from row fields, so that "the size at which it should fire" can be re-derived from the proof set's own rows.

## Risks

- **Harness rows show `spawn_offered` false.** The arm cannot spawn under `claude -p`. Stop, file an arm defect, and fix it before evidence-runs step 6 starts.
- **#513 or #796 slips out of 0.15.** Ship the instrument PR (steps 1 to 5) anyway, leave #429 open, and have the release notes say "unresolved", not "disproved".
- **Step 6's tasks are below break-even.** Their verdict, declined below break-even, settles nothing, so the close-out waits for #560's long tasks.

---

# Addendum

## Proposed acceptance criteria for AH-B095

The issue gives two ways to close but no numbered criteria, and the story file is a blank shell. These go into the story's Acceptance criteria section:

1. **Given** a stream whose init message lists `Agent` or `Task`, lists neither, or has no init message or `tools` list, **when** `parse_result` runs, **then** `spawn_offered` is true, false or None respectively.
2. **Given** a stream with no assistant message, **when** `parse_result` runs, **then** `spawns`, `gather_calls`, `absorbed_calls` and `workflow_launches` are None, never 0 (AD-12: unknown values are never zero).
3. **Given** rows for a task and a break-even figure, **when** the verdict runs, **then** the harness cell reads exactly one of `fired`, `declined-below-break-even`, `missed-above-break-even`, `not-offered` or `unknown`. Task size comes from the bare arm.
4. **Given** saved rows, **when** `summarise` runs, **then** the delegation block prints per task, labelled adherence, and SM-2's result is unchanged.
5. **Given** the registered #513 AC4 and #560 rows, **when** the close-out reads them, **then** #429 closes in one of two ways. Fixed: under decision 2's criterion, with the spawn cost split. Disproved: with per-task verdicts and the break-even used. Either way, FR-34 carries a dated amendment.

**Regression test**, for the story's section:
- A result-only stream now gives `spawns` None. Today it gives 0, and the existing assertion at `tests/test_cost_bench.py:360` changes to match.
- A fixture shaped like the 2026-09-22 finding (spawn tool offered, 18 or more main-thread Reads, no Agent block) gives `missed-above-break-even`. Before this change there is no verdict at all.

## Step 1 — Record whether the spawn tool was offered

- In `parse_result` ([scripts/cost_bench.py](scripts/cost_bench.py), around line 612), find the first message with `type == "system"` and `subtype == "init"`.
  - `spawn_offered` is `any(name in SPAWN_TOOLS for name in tools)`.
  - It is None when there is no init message, or when `tools` is not a list.
- The init shape is already relied on in `tests/test_native_acceptance_role_writes.py:22`.
- Add the field to `STREAM_FIELDS` (line 103), so that `backfill_rows` derives it and a missing raw file sets it to None.
- Tests go beside `test_every_tool_use_block_is_counted_and_spawns_are_the_subagent_share` (`tests/test_cost_bench.py:354`), using the file's own `call()` and `result()` helpers.
- Once #512's run has kept raw output (evidence-runs step 2), run this at $0:

```sh
python3 scripts/cost_bench.py backfill --results <512-results-dir> --raw <512-raw-dir>
```

- This step is first because a false value there invalidates everything after it: the harness arm could not spawn at all.

## Step 2 — Count gather calls by thread, and treat unknown as unknown

- **Gather tools:** `GATHER_TOOLS = ("Read", "Grep", "Glob")`, per decision 1. If the alternative is chosen, add Bash inputs the shell grader rates 0; the import path for that grader still needs confirming.
- **Per run:**
  - `gather_calls` counts gather `tool_use` blocks in every thread.
  - `absorbed_calls` counts those in threads where `parent_tool_use_id` is not null.
  - `workflow_launches` counts `tool_use` blocks named `Workflow`. Unknown: whether that is the exact block name in stream-json. Confirm it from a recorded stream before merging.
- **`SPAWN_TOOLS` stays unchanged**, per decision 4.
- **When a stream has no assistant message** (the older single-document output), all four count fields are None.
  - Today `tools` stays `{}` and `spawns` reads 0.
  - `tool_counts` keeps `{}` so existing readers still work.
- Add every new field to `STREAM_FIELDS`.

## Step 3 — Classify each task and arm

- **New module** `scripts/delegation_verdict.py`, standard library only, with `verdict(rows, break_even, fired_rule)`.
- **Task size** is the median over the bare arm's runs of `gather_calls`.
  - The bare arm is used because the harness arm's own main-thread count falls when it delegates, which would bias its size.
  - If no bare run is known, the size is unknown.
- **Harness cell, first match wins:**
  - any run with `spawn_offered` false: `not-offered`;
  - size unknown, or every run's `spawns` None: `unknown`;
  - enough runs with `spawns ≥ 1` under decision 2's rule: `fired`;
  - size at or below break-even: `declined-below-break-even`;
  - size above break-even: `missed-above-break-even`.
- **A fire below break-even** is reported as `fired` with its size shown, because it may cost more than it saves.
- **Bare cell:** the same counts, with no verdict. It is protocol point 5's control, so a bare arm that spawns at the same rate shows up.
- **Cost split:** each harness cell reports mean `cost_usd` for spawning and non-spawning runs, labelled "adherence, descriptive, not causal" (evidence standard item 10). Either figure is None when its group is empty.
- **Break-even input:** `BREAK_EVEN_CALLS = 7.6`, with a docstring citing FR-34. It can be overridden with `--break-even` on both `replay` and `summarise`. The output prints the figure it used and labels it hypothetical.
- **Tests:** `tests/test_delegation_verdict.py`, rows only, no launcher.

## Step 4 — Report it beside SM-2

- `history_row` gains a `"delegation"` key. Per AD-11, readers ignore fields they do not know, so old history lines still parse.
- `render_history` prints one indented `delegation:` line per task under the per-task lines, in the same plain-text style.
- `cmd_summarise` prints the block after `replay_stats.render(result)`, and under `--json` adds it as a separate top-level key. `replay_stats.analyse` is not touched.
- **Runner test:** drive `BENCH.replay` with the file's fake `Launch` class and fixture streams, and assert the rows carry the new fields. No model is called.

## Step 5 — Docs, story and gates

- **`docs/benchmarks.md`:** describe the new row fields and the delegation block, and say that the verdict is adherence, not the headline.
- **`docs/caught-in-the-act.md`:** replace the `grep -o '"name": *"Agent"'` reproduction with `summarise`. "What was done about it" is updated only at step 7.
- **AH-B095:** fill in Reproduction, Root cause (still unknown until step 7), the acceptance criteria above, Design, Regression test and Tasks.
- **CI's commands**, from `.github/workflows/ci.yml`:

```sh
python3 bin/harness lint
python3 scripts/smoke_tier.py --skip credentials
python3 scripts/detector_corpus.py --floor 0.9
python3 -m unittest discover -s tests -v
python3 scripts/cost_bench.py static --check
python3 scripts/bmad_issue_sync.py audit
```

- **Python floor:** CI also runs `tests.test_python_floor` on its floor interpreter, so use no API newer than that floor.

## Step 6 — Name the verdict in two pre-registrations

- **Files:** `<date>-delegation-nudge.md` and `<date>-harness-vs-bare.md`, which the evidence-runs plan drafts. Add to each:
  - a secondary outcome, "delegation_verdict per task";
  - the break-even figure and its source (decision 5);
  - the absorbable-call definition (decision 1);
  - the rule for "fired" (decision 2).
- **Label:** the outcome is an adherence check. It supports no saving claim, so it needs no multiplicity correction (evidence standard item 1).
- **Consistency:** this must agree with evidence-runs decisions 2 and 4 (long-task threshold and the #513 AC4 tasks). If those are answered differently, restate here.

## Step 7 — Close #429 from registered rows

```sh
python3 scripts/cost_bench.py summarise --results <step-6-dir>
python3 scripts/cost_bench.py summarise --results <step-8-dir> --json
```

- **Fixed:** decision 2's criterion is met on every task above break-even in a registered run. Report the spawn cost split alongside.
- **Disproved:** no task above break-even fired. Record per-task verdicts, sizes and the break-even used. SM-4 then credits no delegation saving, and #799's "What we do not claim" names delegation.
- **Mixed or unknown:** #429 stays open, and the release notes say it is unresolved.
- **Writes:**
  - a dated FR-34 amendment appended in the PRD, not a rewrite;
  - the "What was done about it" paragraph in `caught-in-the-act.md`;
  - the AH-B095 change log.
- **Outward-facing:** draft the closing comment and any follow-up issue (trimming or retiring the tiered stance's standing line if the result is disproved) for the maintainer's approval. Post nothing unapproved.

## Dependencies on other 0.15 issues

- **#513 nudge** — must merge before evidence-runs step 6. Its branch is not in this worktree and its story file is blank.
- **#512 micro tier** — provides step 1's $0 backfill input and step 6's `--tier micro`.
- **#796 task set** — the long tasks above break-even that the #560 reading needs.
- **#560 and #799** — the proof run's rows, and the bundle that must carry the verdict under items 10 and 12.
- **#915 Workflow routing** — decides whether decision 4 flips.
- **#482** — would let rows confirm the stance was actually loaded. Useful, but not blocking.

## PRD and architecture flags

- **PRD FR-34, amendment needed:**
  - how absorbed calls are counted;
  - the source of 4.8 to 7.6;
  - the "fired" criterion, if decision 2 takes the rate;
  - its status at close.
- **PRD SM-4, wording:** it says "ledger rows". Replay evidence comes from `results.jsonl` rows, so amend it to "run rows", or say explicitly that replay rows count.
- **Architecture:** no amendment. AD-12 (unknown values never zero; estimand labels) and AD-14 (nudges are information) already govern this change.

## Evidence and verification

- **Checked in the workspace:**
  - `SPAWN_TOOLS` and the tool counting at `cost_bench.py:101` and `641-671`;
  - result-only output giving `tool_counts == {}`, per the test at `test_cost_bench.py:360`;
  - `arm_command` using `stream-json --verbose` with `bypassPermissions`;
  - rows carrying `task_long`, and `DEFAULT_STANCES` giving `delegation: tiered, cost: balanced`;
  - FR-34 at `prd.md:781-787` and SM-4 at `prd.md:1849`;
  - `tasks.json` having no eligible tasks;
  - the CI commands.
- **Not checked:** `gh` could not be run, so the issue text was read from `docs/caught-in-the-act.md` and the research digest.
- **Unknown:**
  - whether the 19- and 40-run raw outputs still exist; if they do, `backfill` re-classifies them at $0;
  - the break-even method, which is only in unpublished notes;
  - whether `claude -p` offers the Agent tool in the harness image;
  - the Workflow block name.
## Decisions taken

- 2026-09-29: the recommended option on all five: absorbable calls are Read, Grep and Glob only; "fixed" is #513's registered spawn rate on every task above break-even, with FR-34 amended; no paid run of #429's own; Workflow launches reported beside spawns but not counted until #915; break-even is FR-34's 7.6 absorbed calls, pre-registered and labelled hypothetical, with FR-34 amended to name its source.

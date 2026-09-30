# Adherence rate and if-followed soft-estimate report (#798)

> **Verdict.** This builds `citizen usage --by adherence`, a read-only report that gives each recommendation kind two figures: its adherence rate with a Wilson interval, labelled Measured, and an if-followed saving, labelled Soft estimate. The saving comes from a named estimator, carried-context reprice. It reprices each not-followed session's calls after the nudge as if the context had been reset there, using #748's call records and rebuild causes and the dated price table, and a labelled-figure type refuses any total that mixes labels.
> **Effort** medium · **Risk** medium: #748 is unmerged and its call-record shape is not final · **Blast radius** one new usage view and one moved Wilson function; no hook, ledger or benchmark change

## At a glance

- **Outcome** — `citizen usage --by adherence` prints, per recommendation kind, the rate with a Wilson interval (Measured) and the if-followed saving (Soft estimate, with the estimator named). No figure prints without a label.
- **Approach** — read-only over `adherence.jsonl` and main-session transcripts. Carried-context reprice prices each not-followed session's later calls as if the session had restarted at the nudge.
- **Touches** — new `lib/harness_core/soft_estimates.py` and `intervals.py`, the `bin/harness` usage dispatch, `scripts/replay_stats.py`, tests and `docs/usage.md`. The PRD is amended through `bmad-prd` (AC4). In the architecture spine only the capability-map row changes; no AD changes.
- **New deps** — none; standard library only. No paid or live run: host sessions are exploratory under the evidence standard, so nothing is registered in `benchmarks/`.
- **Not in scope** — registering the observation entry point (#867), `--json` (#909), new recommendation kinds, use in the proof bundle (#799) or Studio (#1062), and calling `adherence.settle`.
- **Exit test** — the AGENTS.md gate passes, and a fixture home prints both sections with their labels, a Wilson interval and a signed saving.
- **Open question** — until #867 registers observation, every live emission is answered `unknown`, so the live rate and estimate come out empty. Whether #867 lands in 0.15 is unknown (see decision 2).

## System design

```text
adherence.jsonl (#792) ── emitted, response ──▶ *rate + Wilson
   │ not_followed                                   │ Measured
   ▼                                                ▼
*if-followed estimator ── Soft estimate ──▶ *labelled figures
   ▲ breaks,    ▲ prices                            │ never summed
   │ causes     │                                   ▼ across labels
rebuilds.py   pricing.py               *citizen usage --by adherence
   ▲ calls
transcripts
```

`*` marks what this story builds. `rebuilds.py` is #748's and the ledger is #792's. Every figure passes through the labelled-figure type before it prints.

## Steps

1. **[Anchor spike](#step-1--anchor-spike)** — on #748's branch, check that an emission's session and turn map to one call in a transcript fixture, and read that call's context size.
   *Exit:* a test finds turn 7's call in a 12-prompt fixture, or the missing field is raised on #748 before step 5.
2. **[PRD text of record](#step-2--prd-text-of-record)** — the `bmad-prd` update intent names carried-context reprice, with its inputs and its assumption, where decision 3 puts it (AC4).
   *Exit:* the PRD names the estimator, and `python3 scripts/bmad_issue_sync.py audit` passes.
3. **[Labelled figures and the adherence rate](#step-3--labelled-figures-and-the-adherence-rate)** — `soft_estimates.py` adds figures that carry their label and a total that refuses mixed labels. The rate is computed per kind and fingerprint, with `wilson` from `intervals.py` (AC1, AC3).
   *Exit:* Measured plus Soft estimate raises; 7 of 10 matches `replay_stats.wilson`; no judged emissions prints no rate rather than 0%.
4. **[If-followed estimator](#step-4--if-followed-estimator)** — a pure function over call lists: it reprices reads and #748 breaks without the carried context, adds one cold start, and stops at a compaction (AC2).
   *Exit:* hand-computed fixtures pass: a positive saving, a negative one kept signed, a compaction stop, and an unpriced model counted apart.
5. **[Wire to recorded transcripts](#step-5--wire-to-recorded-transcripts)** — after #748 merges, find each session's main transcript and build its calls through `rebuilds.py`. A missing transcript is counted as unknown.
   *Exit:* an end-to-end test in a temporary `HARNESS_HOME`, with a fixture ledger and transcript, reproduces step 4's figure.
6. **[Report, docs and gate](#step-6--report-docs-and-gate)** — add `--by adherence` beside `prefix` with two labelled sections and an exploratory footer, plus docs, `product.json`, the changelog and the story file.
   *Exit:* the CLI test matches the expected lines, `--rules` and `--stance` exit 2, and the four gate commands pass on HEAD.

## Decisions for the reviewer

> **1. What handoff size does the counterfactual fresh session start from?**
> *Recommend* none: it starts from the session's own first-call context, and the figure reads "at most". Nothing measures a handoff's size, so any fixed number would be invented.
> *Alternative* measure it from followed emissions, as the next session's first call in the same project. That gives a real figure, but it needs #867's live rows and a time-window join across sessions.

> **2. Which emissions does the estimator price?**
> *Recommend* only those `adherence.py` answers `not_followed`. That keeps one definition of following (AD-23), at the cost of an empty live estimate until #867 ships.
> *Alternative* also price emissions whose transcript shows the session running past the window. That gives live figures in 0.15, but adds a second reading of "followed" that can disagree with the ledger.

> **3. Where does the PRD name the estimator (AC4)?**
> *Recommend* a new FR-85, "Soft-estimate report", in §4.5, with testable consequences and a v0.15.0 status. The report covers every recommendation kind, not only the nudge.
> *Alternative* a consequence under FR-32, the nudge's own requirement. It is a smaller edit, but the next recommendation kind would force the text to move.

> **4. Should the evidence standard get the Soft estimate label?**
> *Recommend* one sentence in item 10: a soft estimate is a hypothetical figure and never enters a proof set. Without it, AD-12 and the standard use two label sets with nothing connecting them.
> *Alternative* leave the standard alone. It governs proof sets, and this report is a local usage view whose footer already says it is exploratory.

## Risks

- **#748 merges with no prompt boundary in its call records.** Step 1 checks this on its branch. If the boundary is missing, `soft_estimates.py` counts prompts from the transcript itself, and the gap is raised on #748 before step 5.
- **A session's transcript has been deleted.** Claude Code removes transcripts after `cleanupPeriodDays`, 30 by default. The emission is counted as "transcript missing" and is never priced at $0.
- **#867 misses 0.15.** The report ships with empty live figures and says why. Switching to decision 2's alternative needs fresh approval.

---

# Addendum

## What satisfies each acceptance criterion

- **AC1, the rate per kind with a Wilson interval**
  - There is one row per recommendation kind and profile fingerprint.
  - Each row shows emitted, followed, not followed, unknown and pending counts.
  - The rate is followed over judged (followed plus not followed), with a 95% Wilson interval.
  - The label is Measured; rows with no fingerprint also carry the Unattributed qualifier (AD-12).
  - Zero judged emissions prints "no judged emissions", never a rate of 0.
- **AC2, the if-followed estimate per kind**
  - Each row is labelled `Soft estimate` and names the estimator ("carried-context reprice") and its assumption line.
  - Each row shows the number of emissions priced, the number unpriced and the number with a missing transcript, then the total and the mean saving per priced emission.
  - A kind in `adherence.KINDS` with no estimator prints `Unmeasured`, "no estimator". That is AD-12's label for a figure with no instrument.
- **AC3, a label on every figure and no mixed sums**
  - Every printed number comes from a `Figure` that carries its label.
  - The renderer refuses a bare number.
  - `total()` raises when its inputs carry different labels. No grand total spans the two sections.
- **AC4, the PRD names the estimator** — step 2, through `bmad-prd`'s update intent, placed where decision 3 says.

## Dependencies on other 0.15 issues

- **#792, adherence events** (merged). This provides `policy/hooks/adherence.py`: `KINDS`, `read_rows`, `responses` and `rates`, which the report reads as they are.
- **#748, cache-rebuild causes** (in flight). `lib/harness_core/rebuilds.py` is not on main. Its function names and call-record fields are unknown until it merges. Step 5 blocks on it; steps 1 to 4 do not.
- **#867, opt-in registration of the observation entry point.** Its milestone is not confirmed here. Without it, live emissions settle as `unknown`/`unobserved`, and the live report is empty by design.
- **#795, SM-2 analysis** (merged). This is the source of `wilson`, which moves to `lib/harness_core/intervals.py` and is re-imported by `scripts/replay_stats.py`, so every existing caller keeps working.
- **#909, `citizen usage --json`**. Not required here. If it merges first, add the JSON shape for this view in the same PR; otherwise leave it to a follow-up.
- **The v0.15 evidence-runs plan** lists no run for #798, and this plan adds none. `benchmarks/tasks.json` is untouched.

## Amendments this needs

- **PRD** — the estimator named through `bmad-prd` (AC4, step 2). This is required.
- **Architecture spine** — no AD changes. AD-12 already requires a soft estimate to name its estimator. The capability-map row "Ledgers, pricing, telemetry" adds `lib/harness_core/soft_estimates.py`, the same kind of edit #792 made to the kernel-library list.
- **Evidence standard** — one sentence in item 10, only if decision 4 is accepted.

## Step 1 — Anchor spike

- Read #748's branch; its PR number is not known here. Find the call-extraction function in `lib/harness_core/rebuilds.py` and the fixtures under `tests/fixtures/transcripts/`.
- The question: can the builder tell, for each call, which user prompt it follows? The rule must match the one `usage-feed.py` uses to count turns (around lines 436–446): a user entry counts as a prompt unless it is a tool result, `isMeta` or `isCompactSummary`.
- The context size at the nudge must use the feed's own `_context(usage)` (`usage-feed.py`, called at line 452). Load it through `load_hook_module("usage-feed")` rather than restating the formula.
- Write `tests/test_soft_estimates.py::AnchorTests` against a 12-prompt fixture transcript. Emission turn 7 must map to the last call at or before prompt 7.
- If the call records carry no prompt ordinal, count prompts directly from the transcript in `soft_estimates.py` and note the gap on #748.

## Step 2 — PRD text of record

- Run the `bmad-prd` workflow with its update intent on `_bmad-output/planning-artifacts/prds/prd-agent-harness-2026-09-23/prd.md`. `_bmad/custom/bmad-prd.toml` loads `docs/bmad-governance.md` as persistent facts.
- The text to add is carried-context reprice as defined in step 4, in these parts:
  - the inputs: the adherence ledger, main-session transcripts, #748's breaks and causes, and `pricing.py`;
  - the assumption: the same later turns, in a session reset at the nudge;
  - the label: Soft estimate, never pooled (AD-12);
  - the status: planned, v0.15.0, #798.
- Put it where decision 3 says. Record the change in the PRD's `.memlog.md`, as earlier amendments did.

## Step 3 — Labelled figures and the adherence rate

- **Labels and figures.** `lib/harness_core/soft_estimates.py` holds the label constants `MEASURED`, `SOFT_ESTIMATE`, `UNMEASURED` and the qualifier `UNATTRIBUTED`.
  - A test asserts `SOFT_ESTIMATE == posture.SOFT_ESTIMATE` (`policy/hooks/posture.py:1330`), so the two spellings cannot drift.
  - A `Figure` carries value, label, n, interval, the fingerprint it covers (for a measured figure) and the estimator name (for a soft one).
- **Wilson.** Move `wilson` and `Z95` from `scripts/replay_stats.py` to `lib/harness_core/intervals.py`. `replay_stats.py` imports both back, since `cost_bench.py` already puts `lib` on `sys.path`, and `tests/test_replay_stats.py` stays unchanged.
- **The rate.** Load `adherence` with `load_hook_module("adherence")` (`bin/harness:4654`) and read its rows with `read_rows(path(env))`.
  - Group emitted rows by (`recommendation`, `profile_fingerprint`) and join them with `responses`.
  - Keep the same counts `rates()` computes. Add a test that the per-kind totals equal `adherence.rates()`.
  - The report never calls `settle`: it is read-only, and pending emissions stay pending.
- **Tests**
  - A fixture of 7 followed, 3 not followed and 2 unknown gives rate 0.7 with the same interval as `replay_stats.wilson(7, 10)`.
  - An all-unknown fixture prints no rate.
  - Mixing labels in `total()` raises.

## Step 4 — If-followed estimator

Carried-context reprice runs once per emission that gets priced (decision 2), in session S at turn t:

1. **Calls.** Take S's main-session calls in order, each with its prompt ordinal, model, input, cache read, cache write with its 5-minute and 1-hour split, output, and #748's break flag and cause.
2. **Carried context.**
   - `C` is the context size of the last call at or before prompt t.
   - `B` is the context size of S's first call.
   - `K = max(0, C − B − H)`, where `H` is 0 under decision 1.
3. **Horizon.** The calls after prompt t, up to the first break #748 attributes to compaction, or to the end of the session. After a compaction, both paths hold a similar context.
4. **Actual cost `A`.** The sum of `pricing.tokens_cost(tokens, pricing.price_for(table, model))` over the horizon.
5. **Counterfactual `F`.** Keep output tokens and uncached input unchanged, and reprice:
   - **the first horizon call** is a cold start: it writes what it would have read, less `K` (`write + max(0, read − K)`), and reads nothing;
   - **a later call that is not a break** reads `max(0, read − K)`;
   - **a #748 break** writes `write − min(K, rewritten)`, keeping its tier split.
6. **Saving.** `A − F`, signed. It is never clipped at zero; a short remainder can cost more than it saves.

Handling of the edge cases:
- An unpriced model anywhere in the horizon makes that emission unpriced, counted apart, and never priced at $0 (FR-24).
- There is no interval. The main uncertainty is the assumption, not sampling, and AD-12 asks for an interval only on measured estimates.
- The assumption line printed with the figure is: "assumes the same later turns in a session reset at the nudge, with no handoff; a saving of at most this".

Tests go in `tests/test_soft_estimates.py::EstimatorTests`. Each case uses fake call lists and a two-model price table, with the expected dollars worked out in the test's docstring.

## Step 5 — Wire to recorded transcripts

- **Blocked on #748 merging.** Build call lists with `rebuilds.py`'s own extraction: one read per real path, deduplicated by request id, with sidechains skipped. Do not write a second parser.
- **Finding the transcript.** A session's main transcript is `<session_id>.jsonl` under the Claude Code projects directory, found the way `rebuilds.py` finds it.
  - A transcript that is missing, or has no call at or before prompt t, counts as "transcript missing".
  - Such an emission is neither dropped nor priced.
- **Test.** Build a temporary `HARNESS_HOME` containing `adherence.jsonl` with emitted and response rows, and a fixture transcript under the projects directory. The per-kind figure must equal step 4's hand-computed value.

## Step 6 — Report, docs and gate

- **`bin/harness`**
  - Add `adherence` to the `--by` choices (line 5722).
  - Refuse `--rules` and `--stance` with exit 2, worded like the `prefix` refusal (lines 5063–5069).
  - Dispatch before `load_prices` is reached the way `prefix` is (line 5091): `soft_estimates.report(...)`, with the price table passed in.
- **What the report prints**
  - Section "Adherence (Measured)" and section "If followed (Soft estimate: carried-context reprice)".
  - Each figure carries its label and n.
  - The footer says that host sessions are exploratory under `docs/evidence-standard.md` item 11 and cannot be cited as evidence.
- **Docs and bookkeeping**
  - `docs/usage.md`: extend "Adherence events" (line 447) with the report and the estimator, and add the command to the `--by` list near line 668.
  - `product.json`: a feature line with no savings figure.
  - `changelog.d/798.added.md`.
  - The spine's capability-map row.
  - The evidence-standard sentence, if decision 4 is accepted.
- **Story file.** Fill `_bmad-output/implementation-artifacts/AH-S242.md` as follows:
  - **Approach** takes this card's verdict and step 4.
  - **Decisions and alternatives** takes the four answered decisions.
  - **Do not implement** takes the "Not in scope" bullet.
  - **Tasks** follow the steps, mapped to the ACs.
  - **Bound decisions** are AD-12, AD-23, AD-11, FR-24 and FR-32.
  - **Testing** lists the test classes named in steps 1 to 5.
- **Gate.** Run these on HEAD in the checkout that will be pushed:

```sh
python3 bin/harness lint
python3 scripts/bmad_issue_sync.py sprint-status --check
python3 scripts/bmad_issue_sync.py audit
python3 -m unittest discover -s tests
```

## What was checked, and what was not

- **Checked in the workspace**
  - `adherence.py` defines one kind, `fresh-session` (window 3, followed by `SessionEnd`), and `rates()` reports followed over judged.
  - AH-S236 records that live emissions answer `unknown`/`unobserved` until #867.
  - AD-12's four labels are in the architecture spine at lines 310–321.
  - `posture.SOFT_ESTIMATE` is at line 1330.
  - `replay_stats.wilson` is at line 101.
  - `pricing.price_for` and `pricing.tokens_cost` handle the tier split.
  - The `--by` choices and the `prefix` dispatch are in `bin/harness`.
  - The PRD's last FR is FR-84.
  - The 2026-09-28 roadmap keeps #798 in 0.15.
  - The v0.15 evidence-runs plan lists no run for #798.
- **Not checked**
  - The issue text on GitHub. `gh` was not available to this worker, so the ACs come from the story file.
  - #748's branch and its function names.
  - #867's milestone and state.
  - Whether FR-85 is still free at build time.
## Decisions taken

- 2026-09-29: build took the recommended option of all four: decision 1 first-call context and "at most", decision 2 `not_followed` only, decision 3 new FR-85 in §4.5, decision 4 one sentence in evidence-standard item 10.

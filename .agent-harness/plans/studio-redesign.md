# Studio redesign around the user's questions

> **Verdict.** Rebuilds the Studio's navigation and pages around the user's questions: Home, Sessions, seven Harness pages with Efficacy and Settings tabs, Routines, Changes and Evals. Every new signal is computed when the page loads, from logs the harness already writes, and each efficacy row is stamped with the setting in effect at the time.
> **Effort** about three build sessions; Phase A is tonight · **Risk** med, because the setting periods depend on usage rows · **Blast radius** the Studio only, on its feature branch; no hook or runtime change

## At a glance

- **Outcome** — The Studio is organized by question, not by the harness's parts. Jake tests the Harness half on his own data tonight.
- **Approach** — Rebuild the nav and pages on `feature/v0.18.0-studio`. A new periods module stamps efficacy rows. Pages are built by parallel builders.
- **Touches** — `studio/src` nav and pages; `lib/harness_core/studio` routes plus new periods, transcript-index and routines modules
- **New deps** — None. Routines use launchd, which remote control already uses.
- **Not in scope** — The team Steward view, a chat or Ask surface, beginner presets, any new hook-side recording
- **Exit test** — Studio CI suites green on the branch, and every page shows real data with period-stamped efficacy
- **Open question** — Do the five-user IA sessions gate Phase B? (Decision 5)

## System design

```text
  usage.jsonl ─────┐
  decisions.jsonl ─┼─▶ *periods ── rows stamped ──▶ *Harness ×7
  applies.jsonl ───┘   (setting in effect)          Efficacy | Settings
                            └── before / after ───▶ *Evals
  transcripts ─▶ *transcript index ── ctx, cost ──▶ *Sessions
                            └── skill calls ──────▶ *Skills page
  drafts, applies ── draft list, rollbacks ───────▶ *Changes ──▶ apply
  *launchd routine ─▶ RunSupervisor ── runs ──────▶ *Routines
  all of the above ── one action each ────────────▶ *Home queue
```

`*` marks new or rebuilt parts. Everything is read when the page loads, so no hook changes.

## Steps

### Phase A: tonight, the new shape on data the Studio already reads
1. **[File the epic, spike and stories](#step-1--file-the-epic-spike-and-stories)** — `bmad_issue_sync.py new` in a worktree off main, then a filing PR, like #1298.
   *Exit:* `audit` is clean, every child is a native sub-issue, the PR is merged and the round-3 canvas is republished.
2. **[Sync the branch and spike the setting periods](#step-2--sync-and-periods-spike)** — Merge main into the feature branch, then prototype `periods.py` over 30 days of usage rows.
   *Exit:* the gate is green after the merge, and periods compute in under 2 s with real boundaries on at least three surfaces.
3. **[IA shell and the two-tab Harness frame](#step-3--ia-shell-and-harness-frame)** — `navigation.ts`, `StudioApp.tsx`, redirects from the old routes, mock tokens moved into the theme.
   *Exit:* `npm --prefix studio test` and `test_e2e_studio_flows` pass, and every old route lands on its new page.
4. **[Six Harness pages in parallel](#step-4--six-harness-pages)** — Rules, Stances, Hooks with liveness, Roles, Workflows, Sources. Four builders.
   *Exit:* each page's API test asserts period-stamped rows, and design-judge scores at least 7 against its round-3 artboard.
5. **[Tonight's review build](#step-5--review-build)** — Merge the passing stories into a local `studio-redesign` worktree and run `citizen studio`.
   *Exit:* Jake walks the pages on his own data. Each finding is filed against its story.

### Phase B: operate
6. **[Sessions, Skills, Changes, Home](#step-6--operate)** — The transcript index, draft-list and rollback-history routes, and the attention queue.
   *Exit:* the Sessions median context matches round 2's 256k for the same window, and a draft goes list → test → apply → roll back.

### Phase C: measure, run, ship
7. **[Evals and Baseline, plus five IA sessions](#step-7--evals-and-baseline)** — Experiments, Reports and trends re-homed; preregistrations listed; before and after per period.
   *Exit:* Evals shows a before-and-after for one real boundary, and five sessions are logged with findings filed.
8. **[Routines, retire the old pages, ship](#step-8--routines-and-ship)** — A launchd scheduler with two routines; old pages removed; `product.json` updated.
   *Exit:* a routine fires on schedule and shows in Routines and on Home, and the full gate plus the Studio workflows pass.

## Decisions for the reviewer

> **1. Which milestone carries the redesign?**
> *Recommend* v0.16.0, with the Studio. The Studio has not shipped, so releasing the old IA would make users learn it twice.
> *Alternative* v0.17.0. v0.16.0 ships the Studio as built and the redesign follows.

> **2. Where do the pull requests land?**
> *Recommend* one PR per story into `feature/v0.18.0-studio`, plus a local integration worktree for tonight. The Studio is not on main.
> *Alternative* merge the Studio branch to main first and redesign on main. That is cleaner, but nothing would be testable tonight.

> **3. Build order: Harness pages first?**
> *Recommend* yes. They reuse data the Studio already reads, so they can be tested tonight.
> *Alternative* Sessions first. It answers the cost question sooner but needs the transcript index built first.

> **4. Where do Routines run?**
> *Recommend* a local launchd agent run by the Studio. Transcripts stay on the machine, and the pattern exists in `remote_control.py`.
> *Alternative* Claude Code cloud routines. They need no local daemon, but the session data has to leave the machine.

> **5. Do the five-user IA sessions gate later phases?**
> *Recommend* run them on tonight's build alongside Phase B, without gating it. Real data is a better test than mocks.
> *Alternative* hold Phase B until all five are done. That is safer if the IA is wrong, but it costs about a week.

## Risks

- **The committed `studio/dist` conflicts across parallel PRs** — rebuild dist after each merge; never resolve a conflict in it by hand.
- **Usage rows can't separate setting changes** — step 2's spike fails fast. Fall back to `applies.jsonl` plus config git history, and show one period with a note.
- **Phase A runs past tonight** — stories merge on their own, so step 5 tests whichever pages passed their gate.

---

# Addendum

Supersedes the handoff draft
[cuddly-percolating-hammock.md](cuddly-percolating-hammock.md). Its discovery state, data-source map, Jake's
direction and the three mock rounds are the source for this plan and are not repeated here.
Mocks: `.agent-harness/studio-mocks/` (`build.py`, `pages/`, `canvas/project/*.dc.html`,
`caps/`). Canvas: https://claude.ai/artifact/1E7qfhwGai1EoUFihTQQwV.

## Working rules for this build

- The Studio lives only on `feature/v0.18.0-studio` (head `2f1e413d`, 248 commits ahead of main,
  24 behind). All code worktrees branch from it, via `citizen worktree create`.
- Skills, in order per story: `/build` (builder in its own worktree), `design-loop` for the page
  visuals with design-judge through `bin/harness role run design-judge`, `/review`, then `/land`.
- Builder briefs: no `citizen intent claim` (#993); one scratch folder per builder; at most four
  builders at once (cost stance caps fan-out at six, and parallel suites are capped at three).
- Gate per story PR: the AGENTS.md gate block, plus the Studio workflows' commands:
  `npm ci --prefix studio && npm --prefix studio run lint && npm --prefix studio test && npm --prefix studio run typecheck`,
  `python3 scripts/studio_budgets.py bundle`,
  `python3 -m unittest test_e2e_studio_flows test_e2e_studio_budgets test_studio_accessibility_browser`
  (from `tests/`), `python3 scripts/studio_bundle_manifest.py` verify.
  Run the full unittest suite in the background and read the exit code from a log.
- CodeRabbit reviews only PRs into `main`, so story PRs into the feature branch rely on `/review`
  in two fresh contexts. Request `@coderabbitai review` on each anyway; if it skips a non-main base,
  the full CodeRabbit pass comes when the feature branch's PR to `main` opens.
- `studio/dist` is committed and reproducibility-checked. Rebuild it on the branch tip after each
  merge (`npm --prefix studio run build`), never hand-merge it.
- Every story adds tests: API shape and error cases for each new route, node tests for each new
  component, and an e2e flow per page.

## Step 1 — File the epic, spike and stories

Worktree `studio-redesign-filing` off main. Milestone v0.16.0 is number 21 (Decision 1 may change
it). For each item: `python3 scripts/bmad_issue_sync.py new --title T --kind K --body-file F
--parent <epic> --milestone 21`, then `reserve --issue N --advance` (the `new` reservation lags the
issue list). Link each child to the epic as a native sub-issue through the API, fill the story
sections (Story, Context and value, Given/when/then criteria), run `sprint-status`, then `audit`.
The filing PR needs its own chore issue (issue-ownership), and uses the planning-only exception.

Items (titles final at filing):
- **Chore:** file the Studio redesign epic, spike and stories.
- **Epic:** Organize the Studio around the user's questions.
- **Spike:** derive setting periods from usage rows (step 2).
- **S1** IA shell: new nav, old-route redirects, the mock visual system in the theme.
- **S2** Setting periods API: `periods.py` and `/api/harness/periods`.
- **S3** Two-tab Harness frame: Efficacy (default) and Settings; "measuring, day N of 7".
- **S4** Rules page. **S5** Hooks page with liveness. **S6** Stances page.
- **S7** Roles and Workflows pages. **S8** Sources page with source labels.
- **S9** Transcript index: per-call context and cost, reset and reseed events, skill calls.
- **S10** Sessions page. **S11** Skills page (loaded versus invoked).
- **S12** Changes inbox: draft-list and rollback-history routes.
- **S13** Home: daily digest and attention queue.
- **S14** Evals page. **S15** Baseline ("start from what you have").
- **S16** Routines: launchd scheduler, definitions, run history, two routines.
- **S17** Retire the old pages and ship (redirects removed, `product.json`, docs).
- **Task:** five IA sessions with solo and small-team developers (Decision 5).

Also republish the round-3 canvas (Artifact publish to the canvas URL above, root a copy of
`canvas/project/`) so Jake reviews the target beside the build tonight.

## Step 2 — Sync and periods spike

In the `studio-build` worktree: `git merge origin/main`, regenerate sprint status if it
conflicts, run the gate, push the feature branch. Then the spike, as a scratch script first:

- **Question:** can usage rows' `stances` and `profile_fingerprint`
  (`policy/hooks/usage-log.py:1284-1288`), joined with `applies.jsonl` (`studio/apply.py:742`),
  give each Harness item a list of periods with the setting in effect and its dates?
- **Experiment:** compute periods over 30 days of `usage.jsonl` on this Mac.
- **Exit:** under 2 s, and real boundaries on at least three of Rules, Stances, Hooks, Roles,
  Workflows and Sources. A fingerprint change with no attributable setting counts as noise, not as
  a boundary.
- **Fail:** fall back to `applies.jsonl` plus `git log` of the config; Efficacy shows one period.

## Step 3 — IA shell and Harness frame

- Nav (`studio/src/navigation.ts`, `StudioApp.tsx:102-158`): Home, Sessions; Harness: Rules,
  Stances, Skills, Hooks, Roles, Workflows, Sources; Operate: Routines, Changes; Measure: Evals.
- Redirects (kept until S17): **Hub → Home**, **Configure → the Settings tab of the matching
  Harness page**, **Library → Sources**, **Experiments → Evals**, **Reports/rules → Rules**,
  **Reports/usage → Sessions**, **Reports/trends → Evals**, **Activity → Hooks**,
  **Setup → Baseline**. The CLI command shown per page (`navigation.ts:21-30`) moves with it.
- Frame component: Efficacy tab rows carry setting, period dates and status. A change within 7
  days splits into before and "measuring, day N of 7", linked to Evals. The Settings tab shows
  configuration with "last changed", and saving creates a draft routed to Changes (existing
  `/api/configure/*` save paths).
- Tokens from `studio-mocks/base.css` go into the Mantine theme; no CSS copied page by page.

## Step 4 — Six Harness pages

Each page is one story and one builder, with its target artboards `<Name>.dc.html` and `<Name>Settings.dc.html`:
- **Rules:** `rule_health.report` (`rule_health.py:344`) per period, `try_without`, selection.
- **Hooks:** `decisions.jsonl` (`policy/hooks/decisions.py:171`), grouped by `module` and `point`;
  liveness = last decision, error outcomes, and "silent" for an enabled hook with no decision in
  7 days. Hooks that never write decisions are labelled "not observable", not silent.
  The Safety log folds in here, sorted worst first.
- **Stances:** selection plus `selection_editing`; one row per variant period.
- **Roles and Workflows:** usage subagent and worker rows (`usage-log.py:770-800, 1784-1808`) for
  spawns, tier, effort, cost and the `workflow` field; tier rewrites come from the
  `tier-agent-spawns` decision point.
- **Sources:** `module_library.inventory` core versus `primitive_roots`, plus enabled plugins
  (`bin/harness:2005,2072`). Labels: Core, Personal, Team, Third-party.

## Step 5 — Review build

Worktree `studio-redesign` off the feature branch; merge each story branch that passed its gate,
rebuild `studio/dist`, then `python3 bin/harness studio --no-open --port 4317` and hand Jake the
URL. Add a `.claude/launch.json` entry for it so the browser pane can open it.

## Step 6 — Operate

- **Transcript index (S9):** reads Claude Code transcripts at request time, with an mtime-keyed
  cache under the Studio state directory. Per assistant message it records input, cache and
  output tokens priced via `pricing.row_cost` (`policy/hooks/pricing.py:277`),
  `compact_boundary` markers, and the reseed from `harness-session.py:420-460`. Skill tool calls
  give the invoked list; the session's skill listing gives the loaded list.
- **Sessions:** cost per turn, context per call against the cost stance's 160k and 400k lines,
  auto-trim events, top-cost sessions.
- **Changes:** add routes over `drafts.list` (new; `drafts.py:228,320`) and rollback history
  (`rollback.py:65`); reuse review, test, apply and recover as they are.
- **Home:** the digest and a queue of at most five items, each with one action: a rule gone
  silent, a hook gone silent, a draft waiting, a context overage, drift from `/api/overview`.

## Step 7 — Evals and Baseline

Evals re-homes Experiments, the runs pages and trends; it lists `benchmarks/preregistrations/`,
reads `compatibility/evidence/` through the compatibility reader, and shows before and after for
each period boundary from S2. The auto-trim eval compares context per call before and after the
reset point's projection. Baseline replaces Setup: it reads the current config and plugins and
records a baseline period. IA sessions use the review build, last 30 minutes each, with a fixed
task script; findings are filed against stories.

## Step 8 — Routines and ship

The scheduler writes a per-user launchd agent, following `remote_control.py:127-359`, that runs
`citizen studio routine run <name>` through `RunSupervisor` (`runs.py:445`). Definitions and run
history go under the Studio state directory. The first two routines are a rule adherence audit
(`rule-detectors.py`, offline) and hook liveness. Install and remove run from the Studio and
need presence confirmation like apply. The ship step removes the redirects and old pages,
updates `product.json` and the Studio docs, and runs the full gate and the Studio workflows.

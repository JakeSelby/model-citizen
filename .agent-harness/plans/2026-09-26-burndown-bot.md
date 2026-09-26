# Burndown bot: epic and nine child issues on v0.15.0

> **Verdict.** Files the burndown bot as one epic and nine child issues on v0.15.0, with PRD requirements and filled story records, so development can start cold at the `@codex` spike.
> The bot fixes owner-labelled bugs in Claude and Codex lanes under its own GitHub App, works CodeRabbit to merge, sends scanned vulns to draft advisories and randomises fixes into the v0.17.0 study.
> **Effort** one session, about two hours · **Risk** low — filing only; a sibling filing can conflict on the issue map · **Blast radius** issues and planning records; nothing that runs

## At a glance

- **Outcome** — An epic and nine children on v0.15.0, in dev order: S1 `@codex` spike · S2 benchmark hygiene · S3 App identity and owner gate · S4 budget and stop · S5 pre-registration and attempt record · S6 Claude lane · S7 CodeRabbit to merge · S8 Codex lane · S9 vuln lane
- **Approach** — `bmad-prd` adds FR-71 to FR-74, `bmad-create-epics-and-stories` adds the epic, `bmad_issue_sync.py new` files each issue; a chore issue owns the one filing PR
- **Touches** — 11 issues and their sub-issue links; `_bmad-output/` (PRD, `epics.md`, issue map, sprint status, 11 story files); this plan
- **New deps** — None today; the stories bring claude-code-action and zizmor (MIT) and create-github-app-token, each through licensing review
- **Not in scope** — Any workflow, App, secret, label or code · the study's analysis (#803, v0.17.0) · release automation and PRs to `stable`
- **Exit test** — `gh issue list --milestone v0.15.0` lists all 11; `audit` reports 0 findings; the filing PR is merged
- **Open question** — Codex's share, merge authority, and who applies vuln fixes — decisions 1 to 3

## System design

```text
owner-labelled bug ── random draw ─┬─▶ *Claude lane: harness | bare [S6]
                                   │     └─ patch ─▶ *writer job, App [S3] ─┐
                                   └─▶ *Codex lane, @codex [S8] ────────────┤
                                                                            ▼
*merge to main [S7] ◀── *review loop, ≤3 rounds [S7] ◀── CodeRabbit ◀───── PR
*budget gate [S4] ┄┄ pause, caps, off-hours, limit stop ┄┄▶ every lane
every attempt ── lane, arm, outcome ──▶ *study record [S5]
*weekly scan [S9] ── finding + proposed patch ──▶ draft advisory
```

`*` = new, built by the story in brackets. S1 picks the Codex trigger path, S2 keeps bot PRs out of benchmark mining, and the study record feeds #803.

## Steps

1. **Start from a clean baseline** — `/build`'s worktree off `main`; the Gate's lint and `audit` before any change.
   *Exit:* `audit: N issue(s), 0 finding(s)` on the fresh worktree.
2. **[Record the scope](#step-2--record-the-scope)** — `bmad-prd` update adds FR-71 to FR-74; `bmad-create-epics-and-stories` adds the epic to `epics.md`.
   *Exit:* the PRD diff holds only the four requirements, one roadmap line and a memlog entry; lint clean.
3. **[File the eleven issues](#step-3--file-the-issues)** — `bmad_issue_sync.py new --milestone 5`: the chore, the epic, then S1 to S9 with `--parent`.
   *Exit:* all 11 on v0.15.0 with their `type::*` label, each mapped in `issue-map.json`.
4. **[Link the children](#step-4--link-the-children)** — one sub-issue POST per child, S1 to S9 under the epic.
   *Exit:* the epic's sub-issue list shows nine.
5. **[Fill the story files](#step-5--fill-the-story-files)** — S1 to S9 from [The issues](#the-issues); the chore's story as the delivery record.
   *Exit:* `sprint-status --check` prints nothing and `audit` reports 0 findings.
6. **[Gate, PR, CodeRabbit, land](#step-6--gate-and-landing)** — the Gate block, a PR closing the chore, review per `AGENTS.md`, then `/land`.
   *Exit:* merged with every required check green, and each issue shows its Planning block.

## Decisions for the reviewer

> **1. What share of bugs goes to the Codex lane?**
> *Recommend* a third, drawn at random per bug — harness, bare and Codex each get a third, and the daily caps, not the split, protect your Claude limits.
> *Alternative* half — halves the Claude lane's load, but each Claude arm gets only a quarter of the bugs.

> **2. May the bot merge its own pull requests from the first one?**
> *Recommend* a merge switch that starts off, so it stops at "ready to merge" until you flip it; the autonomy stance bars a loop from graduating itself.
> *Alternative* merge from day one, as first asked; the ruleset (0 approvals, required checks, resolved threads) and the `/land` conditions already bind it.

> **3. Who applies a vuln fix in the advisory's private fork?**
> *Recommend* the bot drafts the advisory with a proposed patch and a session under your identity applies it on your go; creating the fork needs Administration write, which would let a hijacked bot edit its own rulesets.
> *Alternative* a second App with Administration write fixes unattended; faster, but an admin-capable key sits in the repository's secrets.

## Risks

- **A sibling session files issues mid-build** — merge `main` in (never rebase), re-apply the map entries through the tool's writer, rerun `audit`.
- **Auto mode refuses a GitHub write or the merge** — stop and ask once with the exact command; no workaround script.
- **Too few eligible bugs to power the study** — bugs close in a median of 0 days; S5's pre-registration sets a minimum sample and a stop date, and you label bugs for the bot as you file them.

---

# Addendum

Plan labels S1 to S9 are for this file only; the tool assigns BMad IDs at filing. Issue bodies and
requirement text below are pasted as written, so they carry no em dashes.

## Step 2 — Record the scope

**PRD** (`_bmad-output/planning-artifacts/prds/prd-agent-harness-2026-09-23/prd.md`), through
`bmad-prd` update intent, with an entry in the folder's memlog. FR-70 is the highest in use. Add a
requirement group "Unattended maintenance" in §4 holding:

- **FR-71: Unattended bug-fix lanes.** The project must fix owner-labelled bug issues without a live
  session. A Claude lane and a Codex lane each open one pull request per bug, to `main` only, under
  the bot's own GitHub App, which holds no ruleset bypass, administration or workflow permission.
  The bot runs inside a hard budget. Status: proposed for 0.15. Consequences (testable): an issue
  labelled by anyone but the owner gets no action; a patch touching `.github/` or aimed at any base
  but `main` is refused; with the pause switch set or a cap reached no agent starts, and an
  unexpected stop defers the bug to the next run.
- **FR-72: Review through to merge.** A bot pull request must be worked through CodeRabbit, three
  rounds at most, and merge only when every condition an agent-landed pull request needs holds; a
  switch holds merges for the owner. Status: proposed for 0.15. Consequences: a human comment or a
  fourth round stops the bot and labels the pull request for the owner; a skipped review or an
  empty thread-reply review never counts as a pass.
- **FR-73: Private vulnerability lane.** Scheduled scans must file each finding as a draft
  repository security advisory with a proposed patch. Nothing about an unfixed finding may appear in
  a public issue, pull request, run log, step summary or artifact. Status: proposed for 0.15.
  Consequences: a seeded finding creates one draft advisory and no public trace; a repeated finding
  creates no second advisory.
- **FR-74: Randomised real-work stream.** Every bug-lane attempt must be assigned before its first
  turn (its lane by a fixed share, and harness or bare within the Claude lane) and recorded as
  assigned, so the v0.17.0 field experiment can analyse it by intention to treat. Bot-authored work
  must never enter the benchmark task set, and a live benchmark run must name the harness ref it
  measures. Status: proposed for 0.15. Consequences: the pre-registration merges before the first
  assignment; a deferred or crashed attempt appears in the attempt table as assigned; a bot-authored
  mined task fails validation; `cost_bench.py replay` without `--tag` exits non-zero.

Roadmap (the v0.15.0 block, amended 2026-09-24): add "the burndown bot (FR-71 to FR-74), whose
randomised stream feeds v0.17.0's field experiment".

**`epics.md`**, through `bmad-create-epics-and-stories`: an epic entry in the existing format with
`- **Covers:** FR-71 to FR-74` and the nine story lines, and a coverage-map row mapping
"Unattended maintenance (FR-71 to FR-74)" to the new epic. The story lines take their issue numbers
after step 3.

## Step 3 — File the issues

From the worktree root, body files in `/tmp/burndown-filing/`, never in the live checkout.
Milestone 5 is v0.15.0; `new` takes the number, not the title. Every `gh` call names
`--repo JakeSelby/model-citizen`, because filters on the old slug silently return nothing.

```sh
python3 scripts/bmad_issue_sync.py new --title "<title>" --kind chore --body-file /tmp/burndown-filing/chore.md --milestone 5
python3 scripts/bmad_issue_sync.py new --title "<title>" --kind epic  --body-file /tmp/burndown-filing/epic.md  --milestone 5
python3 scripts/bmad_issue_sync.py new --title "<title>" --kind spike --body-file /tmp/burndown-filing/s1.md --parent <epic> --milestone 5
python3 scripts/bmad_issue_sync.py new --title "<title>" --kind story --body-file /tmp/burndown-filing/s2.md --parent <epic> --milestone 5
# ...s3 to s9 the same way. On "filed but not reserved" (list lag), immediately:
python3 scripts/bmad_issue_sync.py reserve --issue <N> --kind <kind> --parent <epic>
```

Each body is the blockquote under its heading in [The issues](#the-issues), without the `> `
prefix; the Design lines under a body go to its story file, not the issue.
`new` applies only the `type::*` label. Add `security` to S3 and S9 with `gh issue edit`. Replace each
`#<S1>`-style placeholder in the bodies with the real number as issues are filed, in order.

## Step 4 — Link the children

`new --parent` records the parent in the map only; `apply` projects it after merge. Link now so the
epic shows its children before then:

```sh
id=$(gh api repos/JakeSelby/model-citizen/issues/<child> --jq .id)
gh api --method POST repos/JakeSelby/model-citizen/issues/<epic>/sub_issues -F sub_issue_id="$id"
```

## Step 5 — Fill the story files

Fill each child's Story, Context and value, Acceptance criteria and Design sections (spike: Question,
Experiment, Exit criterion) from [The issues](#the-issues) and the [design reference](#design-reference),
to the corpus's enrichment standard. Tasks come from each story's task list; the dev agent record
stays empty. Name no model in a story file. The chore's story is the delivery record: distill this
plan into its Design and Dev notes. Then `python3 scripts/bmad_issue_sync.py sprint-status` and
`audit`.

## Step 6 — Gate and landing

- The Gate block from `AGENTS.md`. The unit suite takes 10 to 16 minutes: run it in the background
  and read `Ran N tests` and `OK` from the log.
- Rename this file to `2026-09-26-burndown-bot.md`; `/build` commits it.
- PR title `docs(bmad): file the burndown bot epic and stories`; body `Closes #<chore>` and no other
  issue after a closing keyword; `Landing copy: planning records only, no capability change`.
- CodeRabbit skips `_bmad-output/**`, so its pass covers the plan file. Request it with
  `@coderabbitai review` if automatic review skips the PR, then `/land` once the `AGENTS.md`
  conditions hold. If `main` moves, merge it in and re-apply map entries through the tool's writer.

## The issues

### Chore: File the burndown bot epic and its stories

Kind `chore`, no parent. Body:

> Files the burndown bot epic and its nine children on v0.15.0 with filled story records, adds
> FR-71 to FR-74 to the PRD, and commits the approved plan.
>
> Acceptance:
> 1. Every child is a sub-issue of the epic, carries its `type::*` label and the v0.15.0 milestone, and is mapped in `issue-map.json`.
> 2. The PRD, `epics.md` and sprint status are current, and `audit` reports no finding.
> 3. No workflow, App, secret, label or code changes.

### Epic: Run a burndown bot for owner-labelled bugs and scanned vulnerabilities

Kind `epic`. Body:

> Owner-labelled bugs get fixed without a live session, and scanned vulnerabilities reach a private
> draft advisory before anyone else sees them. A Claude lane (GitHub Actions running
> claude-code-action on the subscription token) and a Codex lane (`@codex`, on the ChatGPT plan)
> share the bugs; each fix is one pull request to `main`, worked through CodeRabbit to merge under
> the bot's own GitHub App. Every attempt is randomised and recorded, so the bot's work is also a
> stream of the v0.17.0 field experiment (#803).
>
> Guard rails: the bot acts only on issues the owner labelled; its App holds no ruleset bypass, no
> administration and no workflow permission; a hard budget (turns per run, attempts per day and week,
> an off-hours window, a pause switch, a clean stop on a limit error) keeps it inside the
> subscription's limits; it never opens a pull request to `stable`.
>
> Children, in development order: #<S1> to #<S9>. Covers FR-71 to FR-74.

### S1. Spike: which identity and surface can trigger `@codex`

Kind `spike`. Body:

> The Codex lane depends on it. The Codex pricing page lists GitHub issue and PR delegation with
> `@codex` on Plus and above, but no page says who may trigger it or whether a GitHub App's comment
> counts.
>
> Question: of the four combinations (the bot's GitHub App or the owner's account, commenting on an
> issue or on a pull request), which start a Codex cloud task? Who authors the resulting commits and
> pull request, and does Codex follow `AGENTS.md` (the closing line, the story file, the Landing copy
> line) without being told?
>
> Experiment: a private scratch repository with the Codex connector and the bot's GitHub App
> installed (registered here, with S3's permission list), seeded with one failing unit test per
> cell. Post one `@codex` request per cell and record each outcome with a link.
>
> Exit criterion: all four cells answered with a link within one working day (eight hours).
>
> Decision rule: the App works on issues, so the lane comments on the issue; the App works on pull
> requests only, so the lane opens a draft pull request and then comments; only the owner's account
> works, so the lane posts through an owner token limited to this repository's issues and pull
> requests; nothing works, so the Codex lane is dropped and the split goes back to the owner.
>
> Record the ChatGPT plan tier and the date it was measured on. Blocks #<S8>. Traces to FR-71.

### S2. Keep bot work out of benchmark task mining and require an explicit `--tag`

Kind `story`. Body:

> Five of the seven replay tasks come from this repository's issue and pull request pairs. A bot fix
> would make the model under test the author of a future reference solution. Bot merges also move
> the installed harness daily, so a live run on the default `candidate` tag would measure whatever
> landed last.
>
> Acceptance:
> 1. Given a mined task recorded as bot-authored, when the task set is validated, then validation fails and names the task.
> 2. Given `cost_bench.py replay` without `--tag`, then it exits non-zero and names the flag; `--tag candidate` still works when given.
> 3. `docs/benchmarks.md` states both rules and names the bot's label.
>
> Merges before the bot's first pull request does. Traces to FR-74.

Design: record `source_author` for each mined task in `benchmarks/tasks.json` and validate it
against the App's login, so validation needs no network; `--tag` loses its default in
`scripts/cost_bench.py` (argparse at :1697). Do not change the task set itself.

### S3. Give the burndown bot its own GitHub App identity and an owner-label gate

Kind `story`, label `security`. Body:

> The `main-review` ruleset lets the admin role bypass it, so a bot acting with the owner's token is
> bound by nothing. A GitHub App with no bypass is held to the required checks and thread resolution
> like any contributor. The repository is public, so anyone can file a bug; the bot acts only on
> what the owner labelled.
>
> Acceptance:
> 1. The App holds contents, pull requests and issues write, and checks and statuses read; no administration, no workflows, no ruleset bypass. The owner's setup steps are in `docs/burndown-bot.md`.
> 2. Given an issue labelled `bot::ready` by anyone but the owner, then the bot takes no action and says why in the run summary.
> 3. Given an eligible issue, then the agent job runs with a read-only token and no App token, and a separate job with no agent validates the patch and pushes it.
> 4. Given a patch that touches `.github/` or targets any base but `main`, then the writer job refuses it.
> 5. Given comments from anyone but the owner, then the agent never receives them; the issue body and owner comments reach it as quoted data.
>
> Traces to FR-71.

Design: see [identity and gates](#identity-and-gates). The two-job boundary is a new invariant, so
the delivery PR amends the architecture spine through `bmad-architecture`.

### S4. Hold the burndown bot to a hard budget with a pause switch and a clean stop

Kind `story`. Body:

> On the subscription token the bot draws on the same five-hour and weekly limits as the owner's own
> sessions, so an unbounded bot can lock the owner out for the rest of a week. The turn cap stops
> gracefully; usage-limit behaviour is undocumented, so the bot treats any other unexpected stop as
> a limit.
>
> Acceptance:
> 1. Given the pause switch set, then no workflow starts an agent.
> 2. Given the daily or weekly attempt cap reached, then the run exits before starting an agent.
> 3. Every agent run passes the same `--max-turns` value, in every arm.
> 4. Scheduled runs start only inside an off-hours window in America/New_York, one at a time.
> 5. Given an agent result that is neither success nor the turn cap, then the issue gets `bot::deferred`, the run exits 0 and the next scheduled run retries it; three in a row stop attempts until the owner clears them.
> 6. `docs/burndown-bot.md` says when to pause the bot, including benchmark and qualification rounds.
>
> Defaults: two attempts a night, eight a week; the turn cap is fixed in the pre-registration
> (#<S5>). All three are variables, tunable without a code change. Traces to FR-71.

### S5. Pre-register the burndown bot's randomised stream and record every attempt as assigned

Kind `story`. Body:

> Randomising from the first attempt makes the bot's work evidence for the v0.17.0 field experiment
> (#803). Without it the data is biased by which bugs the bot happens to take, and the backlog it
> leaves gets harder.
>
> Acceptance:
> 1. A pre-registration from `docs/pre-registration-template.md` merges before the first assignment: hypotheses, primary metric, guardrails, sample size, stopping rule and exclusions for the bot stream.
> 2. Given an eligible bug, then its lane and, in the Claude lane, its arm (harness or bare, 1:1) are drawn at claim time, after the owner's label, and written to the issue before the first turn.
> 3. Every attempt is recorded as assigned, crashes, timeouts, deferrals and caps included, with lane, arm, harness tag, turns, tokens, wall time, CodeRabbit rounds, human commits and outcome.
> 4. Given the recorded assignments, then a sample-ratio check runs before any effect is read, and p < 0.001 stops the analysis.
> 5. A harvest command rebuilds the attempt table from GitHub, tested on recorded fixtures.
>
> The analysis stays with #803. Traces to FR-74.

Design: see [study record](#study-record). The evidence standard's items 10 (intention to treat)
and 11 (field checks) in `docs/evidence-standard.md` set the bar.

### S6. Claude lane: fix an owner-labelled bug in its assigned arm and open one pull request

Kind `story`. Body:

> The Claude lane runs claude-code-action in GitHub Actions on the subscription token, so it costs
> nothing new and runs while the owner's machine sleeps.
>
> Acceptance:
> 1. Given a bug labelled `bot::ready` by the owner, mapped in `issue-map.json` on `main` and drawn for the Claude lane, then the scheduled run claims it with `bot::claimed` and records its arm before the first turn.
> 2. The harness arm runs the latest release, installed at its tag into its own config directory; the bare arm runs a signed-in empty profile; the command line, prompt, turn cap and tools are otherwise identical.
> 3. The pull request goes to `main` from `burndown/<issue>`, carries `bot::authored`, closes exactly its bug, fills the bug's story file and passes `issue-ownership` and `landing-copy`.
> 4. `/build` stops on an issue labelled `bot::claimed` unless told to take it over.
> 5. One run in each arm is observed end to end on a labelled test bug.
>
> Depends on #<S2>, #<S3>, #<S4> and #<S5>. Traces to FR-71 and FR-74.

Design: see [the arms](#the-arms).

### S7. Work CodeRabbit through to merge on the burndown bot's pull requests

Kind `story`. Body:

> The bot finishes what it starts: each CodeRabbit finding is fixed or answered, and the pull request
> merges only when every condition an agent-landed pull request needs holds.
>
> Acceptance:
> 1. Given a completed CodeRabbit pass on a Claude-lane pull request, then the same arm fixes or answers each finding, resolves its thread and requests `@coderabbitai review`.
> 2. After three rounds, or on any human comment, the pull request gets `bot::needs-owner` and the bot stops.
> 3. Given every required check green on the head commit, a completed pass after the last change, every thread resolved and nothing waiting on the owner, then the bot merges with `--match-head-commit`, bringing in `main` first when behind.
> 4. Given the merge switch off, then the bot labels the pull request `bot::ready-to-merge` instead of merging.
> 5. The `Review skipped` status, and the empty reviews that thread replies create, never count as a pass.
>
> Depends on #<S6>. Traces to FR-72.

Design: trigger on the `CodeRabbit` commit status reading `Review completed` on `burndown/*` heads,
with a scheduled sweep as the fallback. The agent's structured result declares any default it took
on the owner's behalf; a declared default means `bot::needs-owner`. The merge switch's starting
value follows decision 2.

### S8. Codex lane: send a fixed share of eligible bugs to `@codex`

Kind `story`. Body:

> A second lane on the ChatGPT plan spreads the load across two subscriptions, so neither runs dry.
> Codex cloud cannot carry the user-level harness, so its fixes are a stratum of their own, not a
> test arm.
>
> Acceptance:
> 1. Given a bug drawn for the Codex lane, then the lane starts a Codex task by the path the spike (#<S1>) chose, and records the attempt as the Codex stratum before it starts.
> 2. The pull request meets the Claude lane's rules: base `main`, `bot::authored`, one closed bug, a filled story.
> 3. CodeRabbit findings on a Codex pull request go back to `@codex`, under the same three-round cap, and merge through #<S7>'s merge job.
> 4. The pause switch and the daily and weekly caps apply to this lane too.
>
> Depends on #<S1> and #<S7>. Traces to FR-71.

Design: Codex reads `AGENTS.md` natively, so both lanes follow the same repository rules. The
owner connects the repository in Codex's settings and sets up its environment; if the spike finds
only the owner's account triggers, the owner adds a token limited to this repository's issues and
pull requests.

### S9. Vuln lane: scan weekly and file each finding as a draft security advisory

Kind `story`, label `security`. Body:

> The repository's scanners are clean (CodeQL, Dependabot and secret scanning show no open alert),
> so the risk that matters is logic flaws in the hooks and governance code, which only an audit that
> reads the code finds. A public issue or pull request for a hook bypass would disclose it before
> installed checkouts update.
>
> Acceptance:
> 1. A weekly run audits the hooks, governance and CLI code and runs zizmor on the workflows, inside the bot's budget.
> 2. Given a finding, then it becomes a draft repository security advisory with a proposed patch, and nothing about it appears in an issue, pull request, run log, step summary or artifact.
> 3. Given a finding that matches an open advisory, then no second advisory is created.
> 4. The fix is applied in the advisory's temporary private fork, with the Gate run there by hand because status checks do not run in temporary forks, and ships as a patch release under `docs/releasing.md`.
> 5. The scan's agent never holds a token that can push code or change settings.
>
> Depends on #<S3> and #<S4>. Traces to FR-73.

Design: see [vuln flow](#vuln-flow). AC4's actor follows decision 3.

## Design reference

### Identity and gates

- **Owner-only setup,** handed over as exact steps in `docs/burndown-bot.md`, never run by an
  agent: register the App and install it on this repository only (and S1's scratch repository);
  store its ID and private key as repository secrets; run `claude setup-token` and store the token
  as `CLAUDE_CODE_OAUTH_TOKEN`; connect Codex (S8).
- **App permissions:** contents, pull requests and issues write; checks, commit statuses and
  metadata read; repository security advisories write for S9. No administration, and no workflows,
  so a push touching `.github/workflows/` is rejected by GitHub itself. Never a ruleset bypass actor.
- **Labels:** `bot::ready` (owner: eligible), `bot::claimed`, `bot::deferred`, `bot::needs-owner`,
  `bot::ready-to-merge`, and `bot::authored` on every bot pull request.
- **Variables:** a pause switch, a merge switch, daily and weekly attempt caps, the turn cap.
- **Two jobs:** the agent job gets a read-only `GITHUB_TOKEN` and no App token, and emits a patch
  and a structured result. A job with no agent mints the App token (create-github-app-token),
  checks the patch against a path allowlist and the base, pushes `burndown/<issue>` and opens the
  pull request. This is the split gh-aw uses for its safe outputs.
- **Triggers:** schedule, `workflow_dispatch` and CodeRabbit's status on `burndown/*` heads. Never
  `pull_request_target`.
- **Trust:** the owner gate checks the `labeled` event's actor, not just the label; the agent sees
  the issue body and the owner's comments inside a quoted data block, nothing else.

### The arms

- **Harness:** the latest release tag installed with `scripts/install.sh` into its own
  `CLAUDE_CONFIG_DIR`, so the bot never runs the rules it is editing. The tag is recorded per attempt.
- **Bare:** a signed-in empty profile through `CLAUDE_CONFIG_DIR`, as `docs/benchmarks.md` defines it.
- **Equal otherwise:** same prompt ("fix #N under `AGENTS.md`: fill its story, run the Gate, open
  the pull request with its closing and Landing copy lines"), turn cap, tools, web access and
  runner. Both arms load the repository's own `AGENTS.md` and project `.claude/` settings; list what
  those give each arm, so the contrast is the user-level install alone. The six unequal-arm
  findings in `docs/benchmarks.md` (task count, turn cap, stop gate, web access, usage prices, hook
  capture) are the checklist.
- **Runner:** `ubuntu-latest` first; if the harness does not install there, a macOS runner, also
  free on public repositories.

### Study record

- **Assignment** comment on the issue before the first turn: a fenced machine-readable block with
  the draw, lane, arm, harness tag and time. The draw happens at claim time, after the owner's
  label, so labelling cannot select for an arm.
- **Outcome** comment when the attempt ends: result, turns, tokens (from the action's execution
  output; unknown for Codex), wall time, CodeRabbit rounds, human commits on the pull request, the
  pull request, and a revert within 14 days.
- **Harvest** rebuilds the table from these comments through the API; tests use recorded fixtures,
  never the live service.
- **Pre-registration** follows the template's headings (run, hypotheses, primary metric,
  guardrails, sample size, stopping rule, multiplicity, decision rule, exclusions, deviation log).

### Vuln flow

- **Scan:** weekly, off-hours, counted against the weekly cap. claude-code-action with
  `show_full_output` left false; zizmor's output goes to a file the agent reads, never to the log;
  nothing in the step summary; no uploaded artifact.
- **File:** `POST /repos/JakeSelby/model-citizen/security-advisories` as a draft, the finding and a
  proposed patch in the private description, and a fingerprint so a repeat is recognised. First
  task: confirm an installation token may create one, since the REST page also requires a security
  manager or administrator.
- **Fix:** per decision 3. A temporary private fork needs Administration write to create; status
  checks do not run in it and no protection rules apply, so whoever fixes runs the Gate by hand, and
  the advisory merges the fork's pull requests.

## Context and background

Requested 2026-09-26: a bot that sweeps the repository for filed bugs and security issues, opens
pull requests and works CodeRabbit through to merge, without new spend; a similar lane that scans
for vulnerabilities; and the ramifications for benchmarking. The research compared hosted and paid
options; nothing runs claim, fix, review rounds and merge out of the box, and only claude-code-action
on the subscription token and `@codex` on the ChatGPT plan run on existing subscriptions.

Decisions approved on 2026-09-26:

1. The Claude lane is GitHub Actions with claude-code-action on the subscription token; the Codex
   lane is `@codex`, on the ChatGPT plan; a fixed split of bugs goes between them. Step one is a
   one-day spike on what can trigger `@codex`. The bot shares the subscription's limits, so it gets
   a hard budget: turns per run, issues per day, an off-hours schedule, a pause switch and a clean
   stop on a limit error.
2. One trunk, no pull requests to `stable`: `stable` only fast-forwards to release tags, installs
   update with `merge --ff-only` (`scripts/install.sh:49`), and `advance_stable.py` checks ancestry.
3. Vuln findings go to draft security advisories fixed in private forks.
4. Harness against bare is randomised within the Claude lane from day one, so the bot doubles as
   the v0.17.0 real-work study; Codex fixes are their own stratum.

The backlog is fresh rather than stale: 28 bugs were open, all under six days old, with a median
of 0 days to close. Merges land one at a time, since branches must be up to date and merge queue
needs an organization-owned repository.

## Evidence and verification

Vendor claims were rechecked on 2026-09-26 with `curl` and `grep` against primary pages.

- **claude-code-action:** the OAuth token path is documented for Pro and Max (`docs/setup.md`);
  turns are capped with `claude_args: --max-turns`, and "Claude will stop execution gracefully" at
  the limit (`docs/configuration.md`); `show_full_output` defaults to false because run logs are
  public (`action.yml`); a custom App is wired with create-github-app-token and `github_token`.
  What happens on a usage limit is not documented, hence S4's rule. Source:
  https://github.com/anthropics/claude-code-action
- **Codex:** "GitHub issue and PR delegation with `@codex`" is available on Plus, Pro, Business and
  Enterprise and not with an API key (https://developers.openai.com/codex/pricing). Who may trigger
  it, and whether an App's comment counts, is undocumented: S1.
- **Security advisories:** creating one needs "Repository security advisories" write and lists
  installation tokens, but the REST page also requires a security manager or administrator
  (unverified for installation tokens: S9's first task). A temporary private fork needs
  Administration write; "status checks do not run on pull requests in temporary private forks",
  GitHub enforces no protection rules there, and the advisory merges its pull requests.
  https://docs.github.com/en/rest/security-advisories/repository-advisories
- **Rulesets:** GitHub Apps are eligible bypass actors, so the App must stay off the list. The
  "additional approval for unattributed Copilot pull requests" option on `main-review` has no
  effect when zero approvals are required.
  https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets
- **Repository state:** `main-review` is squash-only with 0 approvals, required thread resolution,
  strict required checks (lint, test, issue-ownership, landing-copy) and an admin-role bypass.
  Workflow tokens default to read and cannot approve reviews. No repository secrets or variables
  exist. Private vulnerability reporting, CodeQL default setup, Dependabot security updates, secret
  scanning and push protection are on. The owner is the only collaborator, so only the owner can
  label. Milestone v0.15.0 is number 5; the latest release is v0.13.1.
- **Benchmarks:** `--tag` is repeatable and defaults to `candidate` (`scripts/cost_bench.py:1697`);
  five of seven tasks carry an issue and pull request `source` in `benchmarks/tasks.json`.
- **Precedent:** the cache-rebuild filing PR (#752) wrote the epic's and stories' story files, the
  issue map, sprint status and `epics.md`, with no PRD change because its requirements existed.
  This epic's do not, hence step 2.

## Deferred, and why

- **Pull requests to `stable` and automated patch releases:** decision 2; a patch release still
  needs the hand-run qualification steps in `docs/releasing.md`.
- **gh-aw and codex-action:** their Claude and Codex paths take API keys only, so usage is billed
  per token.
- **Copilot's coding agent and other hosted agents:** paid plans, and they run their own loop
  instead of the harness.
- **Claude Code routines:** the daily run cap is unpublished and the supported GitHub events are
  unconfirmed; revisit if Actions proves awkward.
- **CodeRabbit Autofix:** free on public repositories, but it would add a third author to the
  study's outcomes.

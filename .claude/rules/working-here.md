---
paths:
  - "**/*"
---

# Working in Model Citizen

## Commands

```sh
bin/harness lint                          # personal strings and secret patterns; must be clean
python3 -m unittest discover tests        # merge, link, config, lint logic
bin/harness sync --dry-run                # what a sync would do from this checkout
bin/harness doctor                        # versions, logins, links, drift
```

**Expected clean-tree output:** `lint: 0 finding(s) in …`, `audit: N issue(s), 0 finding(s)`
and `OK` from unittest with no skipped tests; `sprint-status --check` prints nothing when the
file is current, and `python3 scripts/bmad_issue_sync.py sprint-status` regenerates it. Tests
run under the system Python 3.9 and under a current Python; keep the code free of syntax newer
than 3.9.

## BMad planning

BMad Method 6.12.0 with BMM, Claude Code and Codex projections, and compatibility shims is the
repository's public planning system. Its authored corpus stays in `_bmad-output`; do not redirect
it to another repository. The complete pinned install command and version-control boundary are in
`docs/bmad.md`. Run planning workflows from the shared checkout and implementation from a managed
worktree.

Every SDLC step routes to its BMad skill, and every PR keeps the corpus current: the issue keeps a
summary, its story file carries the design. The routing map, the currency rule and the story-file
contract are in `docs/bmad-governance.md`, which every BMad workflow loads.

## How the checkout is used

- **This checkout is live.** `harness sync` symlinks `claude/rules`, each `claude/skills/*`,
  `claude/hooks` and the output style into `~/.claude`. An edit here is in effect in the next
  session with no further step; a half-finished rule is live too. Work in a worktree branched
  off `main`, so the live checkout only ever carries merged content.
- **Every change lands through a pull request.** The `main` ruleset requires green `lint` and
  `test` checks on an up-to-date branch and a squash merge; there is no direct push.
- **One delivery issue per PR, one PR per delivery issue.** Create or select a dedicated issue
  before changing files, including docs — file it with `scripts/bmad_issue_sync.py new`, or
  `reserve` an existing one, because `issue-ownership` also fails a PR whose issue has no BMad ID
  in `_bmad-output/issue-map.json`. Add `Closes #N` to the PR. Split separately delivered
  work into child issues; contextual references do not establish ownership. Reuse is allowed
  only when a previous PR was closed without merging. The `issue-ownership` check enforces
  current GitHub closing links; verify ownership again immediately before merging.
- **Code changes** (`bin/harness`, `claude/hooks/*.py`, `tests/`) carry a test with every
  change. Content changes (rules, stances, skill text, docs) are gated by the lint and review.
- **Every test of cost, efficacy or a change to the system, ad hoc evals included, follows the
  protocol in `docs/evidence-standard.md`:** pre-registered, in fresh containers, never from a host
  profile, live checkout or disposable home. Anything else is exploratory and never cited as evidence.
- Nothing personal, nothing project-specific, nothing copyleft. The lint enforces the first;
  review enforces the rest.

## CodeRabbit review and merging

CodeRabbit reviews pull requests into `main`, as `.coderabbit.yaml` configures it. On every pull
request, work through its review with `/build` step 6 and without asking first: push fixes to the
branch, reply in its threads and resolve them, and request each further pass with
`@coderabbitai review`, since a push never starts one. Request the first pass the same way when
automatic review skips the pull request, as it does drafts, `chore(release)` titles and Dependabot.
A pass has finished when the `CodeRabbit` commit status reads `success: Review completed`, seven to
eleven minutes after it starts, so allow fifteen. The `Review skipped` status it posts on every push
is not a pass, and neither is the empty review each of its thread replies creates. Comments in the
review body, outside the diff or marked as nitpicks, are findings too: fix them, or answer them in a
pull request comment.

**These conditions are the go-ahead `/land` asks for.** Merge a pull request from a branch of this
repository, never from a fork, without asking once all three hold:

1. **Nothing waits on the maintainer:** no question to them is open, no default you took on their
   behalf awaits their confirmation, and the diff does what the issue asks and no more.
2. **It is tested:** the Gate block passed before your last push, and every required check is green
   on the head commit.
3. **The review is worked through:** a pass completed after your last change to a file CodeRabbit
   reviews, each of its findings is fixed or answered with the reason, every thread is resolved,
   and no human's thread is open. Bringing in `main` needs no new pass.

Short of all three, report what remains with the pull request link and wait. A release, a tag and
`sync_about.py --apply` keep their own approvals.

## Layout

- `primitives/` — the shared authoring authority. `policy/` — shared lifecycle policy.
- `adapters/` — runtime bindings; `compatibility/` — native qualification evidence and status.
- `claude/` — compatibility projections and native settings; what gets linked into `~/.claude`: `CLAUDE.md`, `rules/`, `stances/`, `skills/`,
  `hooks/`, `output-styles/`, plus `settings.template.json` and `OWNERSHIP.json`.
- `bin/harness` — the CLI. `tests/` — its unit tests.
- `vscode/`, `codex/`, `templates/repo/` — the other surfaces the harness manages.
- `docs/` — how it works; the only place project provenance names are allowed.

# Cost benchmarks

What the harness costs you, measured against Claude Code with no harness at all. The static figure
below exists today. The live replay that compares whole tasks has a runner and no published
result yet, so nothing here claims a saving.

A result published from these runs must meet the [evidence standard](evidence-standard.md), and
its plan is a filled [pre-registration template](pre-registration-template.md) committed before
the first trial.

## Static context figure

Every session the harness manages starts with its global instructions, rules, selected stances and
output style already in context, plus one listed description for each agent, skill and command. A
bare Claude Code session loads none of that, so the whole count is the harness's standing overhead.

```sh
python3 scripts/cost_bench.py static            # print the figure for this checkout
python3 scripts/cost_bench.py static --check    # what CI runs
python3 scripts/cost_bench.py static --write    # refresh benchmarks/static.json at a release
```

`benchmarks/static.json` is the committed figure for the last release. It records files, lines,
characters, an estimated token count for the default stance selection and for the longest variant
of every dimension, the five largest files, and what that many tokens cost per model. Its
`scopes` block names the set each count is over, because the caps `citizen lint` prints are
over a narrower one.

- **Tokens are an estimate:** characters divided by four. It is there to show the trend between
  versions with no tokenizer, network call or API key. It is not a billing figure.
- **Dollars come from `policy/prices.json`.** `session_start` prices the layer as one cache write,
  `later_turn` as one cache read. A session that outlives the cache pays the write again.
- **CI fails when the estimate grows more than 5% over the committed figure.** Trim the growth, or
  add an entry to `benchmarks/allow.json` naming `harness_version`, the new `est_tokens` and a
  `reason`. The entry stops matching as soon as the figure moves again.
- **Every counted file is priced on its own.** The `files` map gives each file's characters,
  estimated tokens and dollars, and sums to the totals within rounding. `--check` prints one line
  per file whose estimate moved since the committed figure, priced on the model with the highest
  cache-read rate, so a change to one rule reads as that file's delta rather than a moved total.
  The 5% gate stays on the total.
- **The caps in `citizen lint` are separate.** They bound the worst case — the longest variant of
  every stance — in tokens and in lines, over instructions, rules and stances only; this tracks the
  default selection, output styles and listings included, in tokens and dollars, version by
  version. Both use the same characters-over-four estimate. Which cap binds, and why:
  [how-it-works](how-it-works.md#context-discipline).

### Recorded trims

One row per change that set out to shrink the static figure, before and after, from
`scripts/cost_bench.py static` run on that change's branch. `benchmarks/history.jsonl` cannot hold
these: its rows are replay ratios against a bare arm on one model and one day, and a static trim
has no arm. Read the rows in order; none of them is a live measurement.

| Change | Always-loaded | Listings | Total |
| --- | --- | --- | --- |
| Baseline at 0.12.0 | 5,198 | 2,326 | 7,524 |
| After shortening skill and agent descriptions (#430) | 5,198 | 1,980 | 7,185 |
| After trimming the output style (#430) | 4,539 | 1,980 | 6,519 |

## Live replay

`scripts/cost_bench.py replay` runs the pinned tasks in `benchmarks/tasks.json` headlessly in two
fresh containers, one with Claude Code and nothing else and one with the harness at a pinned ref,
and scores each run with a check the agent never sees. It calls a model and spends real usage, so
it is run by hand on a release candidate and never in CI. It needs Docker, and
`CLAUDE_CODE_OAUTH_TOKEN` set as for the Linux qualification target.

```sh
python3 scripts/cost_bench.py replay --verify-tasks                  # prove every check in a container; calls no model
python3 scripts/cost_bench.py replay --model <id> --tag v0.13.1 --dry-run   # the arms and the schedule
python3 scripts/cost_bench.py replay --model <id> --tag v0.13.1      # 7 tasks x 2 arms x 2 reps
python3 scripts/cost_bench.py replay --model <id> --tag v0.12.0 --tag v0.13.0   # two versions, one run
python3 scripts/cost_bench.py arms check --tag v0.13.1 --dry-run     # the two-build check, shown
python3 scripts/cost_bench.py arms check --tag v0.13.1               # build each arm twice, compare
python3 scripts/cost_bench.py arms probe-egress --image <arm image>  # prove the egress rule
```

- **Each arm is a fresh image from pinned inputs, and nothing from your machine reaches it.**
  Both are built by `scripts/replay-arm.Dockerfile` from the Linux qualification image's pinned
  base digest and `CLAUDE_CODE_VERSION`, read out of `scripts/linux-target.Dockerfile` so the two
  cannot drift. `bare` is that base plus Claude Code, less the Codex client the base template
  ships, so no other agent client is on the path. `harness@<ref>` adds this repository at the
  ref's full commit, cloned into the build context and synced for the image's own user with no
  configuration, so the arm loads the ref's defaults. Your home directory, profile, personal layer,
  environment, hooks and settings are never mounted, copied or read, and there is no fallback to
  them. The installed harness is no longer an arm: name a release tag, or a full commit for a
  pre-release candidate.
- **`--tag` is repeatable.** Each tag is its own image, schedule, results folder and history row,
  stamped with the version and commit of the ref that ran; the bare image is built once. Every ref
  resolves before anything is built, so a typo costs nothing, and `--spend-cap` applies to each
  tag's schedule on its own.
- **Each build writes a declaration and a manifest beside the image.** The declaration is the
  inputs: base digest, Claude Code version, harness ref and commit or none, and the hashes of the
  Dockerfile and the lister. The manifest is every file, link and directory under the image user's
  home, Claude Code's managed settings and the harness checkout, each file by mode, size and
  sha256 and each link by its target, with a summary of settings, hooks, rules, skills, agents and
  plugins, and every global npm package by name and version, where agent clients live. It is listed by `scripts/arm_manifest.py` in a fresh container with no network and no
  mount. Left out, and named in the manifest: npm's cache and logs, tool caches, and the
  checkout's `.git`, whose pack layout differs between clones. The harness sync's `synced_at` is
  normalised before hashing. The build writes no Python bytecode, which would carry its build time.
- **Two builds must agree.** `arms check` builds each arm twice with no cache, lists both, compares
  them entry by entry, prints every difference and removes both images; it exits 1 on any
  difference, and `--dry-run` prints every command without building. `replay --dry-run` prints the
  image each arm will be.
- **Every row records the container it ran in**: `arm_image`, `arm_image_id`,
  `arm_base_image`, `arm_declaration_sha256`, `arm_manifest_sha256`, and `harness_ref` and
  `harness_commit`, both `null` for the bare arm. The history row carries each arm's image id,
  manifest digest, ref and commit.
- **A run is `docker run --rm` with one mount.** The task's snapshot is mounted at `/work`, which
  has no instruction file above it; nothing else from your machine is mounted. The credential is
  passed by name, so its value is on no command line and in no row. The container runs with
  `no-new-privileges` and no capabilities, and one command line serves both arms: the same
  `--model`, `--strict-mcp-config`, `--max-budget-usd 2`, the task's own `max_turns` as
  `--max-turns`, `--permission-mode bypassPermissions`, since the container is the fence and a
  headless run cannot answer a prompt, and settings that deny `WebFetch` and `WebSearch`.
- **The one way out is the model API.** Both arms sit on an internal Docker network with no route
  out. The only other container on it is `scripts/egress_proxy.py`, a standard-library CONNECT
  proxy run from the bare image and also joined to the default bridge, which opens a tunnel to
  `api.anthropic.com:443` and answers everything else `403`. The arms get `HTTPS_PROXY` pointing at
  it and `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`. `arms probe-egress` proves the rule from an
  arm container: the model API answers through the proxy, `example.com` is refused, and the model
  API without the proxy does not resolve.
- **No check runs on your machine.** The held-back test files are written into the snapshot from
  this repository's history, then the check runs in a fresh container of the bare image with the
  snapshot as its only mount, the image's own HOME, no network and no credential; an oracle is
  sent on stdin. `--verify-tasks` runs each task's gate and both of its checks the same way,
  building the bare arm first unless `--check-image` names one: on your machine an older
  snapshot's code reads your live configuration through HOME and goes red for that.
- **The stop gate can fire.** The stop-gate hook runs a gate only in a trusted root, so the harness
  image trusts `/work`, where every snapshot is mounted, when it is built. No run writes a trust
  file anywhere.
- **Every run is captured as `stream-json` with hook events**, the one format that carries the
  Stop hook's decisions, so each row records `stop_hooks`, how often the hook ran, and
  `hook_blocks`, how often it refused the stop. A raw file kept in the older single-document form
  still reads, with both fields `null`.
- **Each arm is proved before anything is scored.** One capped `-p` run per arm runs
  `bin/harness lint` in that arm's own container; an arm whose lint is not clean, or whose run has
  a read refused, refuses the whole replay with exit 2 before any scored run launches, and its
  cost counts against `--spend-cap`. The bar is lint rather than the full suite because the suite
  is profile-dependent at older snapshot commits. Every scored row records `preflight`.
  `--skip-preflight` bypasses the check and stamps the rows `skipped`.
- **Every run starts in a throwaway snapshot.** The snapshot holds one commit and its ancestors, so
  the change that solved a task is not reachable from it, and it is removed after scoring. The
  harness arm's image does hold the harness checkout at its ref, which may postdate a task; that
  was as true of the profile arms this replaced, and it is recorded rather than hidden.
- **Cost is the CLI's own `total_cost_usd`**, a list-price equivalent and not money charged under a
  plan sign-in. Run order changes it, because a later run finds its prefix already cached, so each
  row also carries a cache-normalised cost that reprices every thread's first-turn cache reads as
  cache writes. It is empty when the CLI output does not carry per-turn usage.
- **Beside it, `cache_miss_ratio`: how much of its prefix the run re-bought.**
  `cache_write / (cache_read + cache_write)` summed over every turn the run opened, subagent
  threads included, because a fan-out's fresh prefix is part of what the run cost. The
  arithmetic is `citizen usage --by prefix`'s, imported from that module rather than restated,
  but the two are not the same number: the session figure subtracts a subagent's tokens, so a
  run that fanned out reads higher here, by design. A candidate that buys fewer tokens by
  re-writing its prefix more often is otherwise invisible in the history, so `history.jsonl` and
  `history.md` carry each arm's mean of it beside the cache-normalised ratio. A run whose output
  carries no per-turn cache figures, any one of whose turns reports usage without them, or whose
  turns report neither reads nor writes, is `null` and is left out of the arm's mean; so is an
  errored run, whose turns are not the spend it would have had. Never zero: zero is a run that
  served its whole prefix.
- **An errored run is an error, never a failure.** It sits outside both cost per passed task and
  the pass count, and is counted beside them. The per-run cap is soft, so the runner also stops
  before any launch that could take reported spend past `--spend-cap`.
- **`benchmarks/history.jsonl` holds one row per harness version per run day**, stored as a ratio to
  bare on the same day and model; `benchmarks/history.md` is rendered from it. Compare ratios across
  days, never dollars. The publishable threshold is fixed in the script: the harness costs at most
  85% of bare per passed task while passing no fewer than bare minus one, mean of reps.
- **What is faked:** single-shot prompts stand in for interactive sessions, 2 of the 7 tasks are
  synthetic, and a tagged run measures the tag's default configuration rather than a configured
  one. The arms run on Linux; a macOS arm is not built.
- **A task that cannot be passed honestly leaves the set** and moves to the manifest's `retired`
  list with its reason and date. `usage-prices` left on 2026-09-25: its held-back tests pin live
  prices and helper names its prompt never gives, and no arm can reach the web to confirm a price.

**Status.** The live tier has produced one uncontaminated result: 1.052 on a four-task set, above
the 0.85 threshold, so no cost claim is published. Two earlier figures in either direction were
artifacts of the runner's sandbox and of a test-suite defect, both since fixed. A review on
2026-09-24 found six more ways the arms were unequal or a task unfair: the task count above, the
turn cap, the stop gate, web access, `usage-prices` and hook capture. Each is fixed as described
above, and no result has been taken since. The arms have since moved from host profiles to
containers, which starts a new series. Treat this tier as an instrument whose methodology is under review, not as a result; the static tier above is the
figure to rely on today.

## Limits

- Claude Code only. Codex instructions are rendered at sync time and are not counted.
- Your own `CLAUDE.personal.md`, memory files, MCP servers and hook output are not counted. They
  are yours, not the harness's, and MCP tool definitions alone can outweigh everything measured
  here.
- Full agent and skill bodies load only when used, so only their descriptions are counted.

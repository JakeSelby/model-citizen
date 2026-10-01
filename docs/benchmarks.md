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
fresh containers, one with Claude Code plus the shared observer and one with those plus the harness at a pinned ref,
and scores each run with a check the agent never sees. It calls a model and spends real usage, so
it is run by hand on a release candidate and never in CI. It needs Docker, and
`CLAUDE_CODE_OAUTH_TOKEN` set as for the Linux qualification target.

```sh
python3 scripts/cost_bench.py replay --verify-tasks                  # prove every check in a container; calls no model
python3 scripts/cost_bench.py replay --model <id> --tag v0.13.1 --exploratory --dry-run   # the arms and the schedule
python3 scripts/cost_bench.py replay --model <id> --tag v0.13.1 --pre-registration <plan>  # 0 tasks x 2 arms x 5 reps; refuses until #796
python3 scripts/cost_bench.py replay --model <id> --tag v0.12.0 --tag v0.13.0 \
    --pre-registration <plan>                                           # two versions, one run
python3 scripts/cost_bench.py summarise --results <dir> --plot <dir>/pareto.svg                # SM-2's verdict from the saved rows; calls no model
python3 scripts/cost_bench.py replay --model <id> --pair benchmarks/ablations/<name>.json \
    --exploratory --dry-run                                            # a one-policy pair's three arms and schedule
python3 scripts/cost_bench.py detect --raw <dir>                     # which rules fired in each saved stream; calls no model
python3 scripts/cost_bench.py detect --backfill <root>               # the same beside every results.jsonl under root
python3 scripts/cost_bench.py arms check --tag v0.13.1 --dry-run     # the two-build check, shown
python3 scripts/cost_bench.py arms check --tag v0.13.1               # build each arm twice, compare
python3 scripts/cost_bench.py arms probe-egress --image <arm image>  # prove the egress rule
```

- **A run is pre-registered or exploratory.** `--pre-registration` names a committed, dated plan
  filled from the [pre-registration template](pre-registration-template.md); without one the run
  needs `--exploratory`, labels every row exploratory and writes no history row. The protocol is in
  the [evidence standard](evidence-standard.md).
- **Each arm is a fresh image from pinned inputs, and nothing from your machine reaches it.**
  Both are built by `scripts/replay-arm.Dockerfile` from the Linux qualification image's pinned
  base digest and `CLAUDE_CODE_VERSION`, read out of `scripts/linux-target.Dockerfile` so the two
  cannot drift. `bare` is that base plus Claude Code and the observation-only recorder, less the Codex client the base template
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
- **The defaults nominally size one full set.** `--reps` is five trials per task and arm, SM-2's
  minimum. `--spend-cap` defaults to 140.50 USD: 7 tasks x 5 trials x 2 arms at the 2 USD per-run
  cap, plus each arm's 0.25 USD preflight. The per-run cap is soft, so an overrun can exhaust that
  total before the last trial. The runner stops before the next launch, records a partial set and
  publishes no history row or claim.
- **Each build writes a declaration and a manifest beside the image.** The declaration is the
  inputs: base digest, Claude Code version, harness ref and commit or none, and the hashes of the
  Dockerfile, the lister and the exact observer hook settings. The manifest is every file, link and directory under the image user's
  home, Claude Code's managed settings, the observer install and the harness checkout, each file by mode, size and
  sha256 and each link by its target, with a summary of settings, hooks, rules, skills, agents and
  plugins, and every global npm package by name and version, where agent clients live. It is listed by `scripts/arm_manifest.py` in a fresh container with no network and no
  mount. Left out, and named in the manifest: npm's cache and logs, tool caches, and the
  checkout's `.git`, whose pack layout differs between clones. The harness sync's `synced_at` is
  normalised before hashing. The build writes no Python bytecode, which would carry its build time.
- **Two builds must agree.** `arms check` builds each arm twice with no cache, lists both, compares
  them entry by entry, prints every difference and removes both images; it exits 1 on any
  difference, and `--dry-run` prints every command without building. `replay --dry-run` prints the
  image each arm will be.
- **An arm outside the protocol never launches.** Before the first launch, `replay_arms.admit`
  recomputes the declaration and manifest digests and requires their known schemas. It refuses an
  arm whose components are missing, duplicated or malformed; whose installed observer does not
  match its declared sha256; whose manifest holds a Claude Code version, agent client or harness commit other than its declaration names; whose settings, hooks,
  rules, skills, agents, plugins or instruction files are not the declared harness's; whose
  recorded inputs name your home directory, this checkout or an ambient `CLAUDE_CONFIG_DIR`; whose
  declaration pins no reasoning effort; or whose run is neither pre-registered nor exploratory.
  A refusal prints every admission defect found. Every `docker run` is refused the same way when a
  mount or a variable names one of those paths.
- **The two arms differ by the harness and nothing else.** After each arm is admitted,
  `replay_arms.admit_pair` compares the pair and refuses the replay, printing every difference,
  when their declarations differ in anything but the harness component (base digest, Claude Code
  version, Dockerfile, lister or effort), when their manifests differ in Claude Code, agent
  clients or roots, or when any manifest entry outside the harness component is not identical in
  both. The harness treatment is its checkout, links whose targets are inside that checkout, the
  exact regular files its sync and trust commands generate, and only the parent directories needed
  to reach those entries. Unrelated files and links remain part of pair parity even when they sit
  under `.claude`, `.codex` or `.local/bin`.
- **Every run pins its reasoning effort.** `--effort` (`low`, `medium`, `high`, `xhigh` or `max`;
  default `high`) is recorded in each arm's declaration and passed to Claude Code as `--effort` on
  every launch, the pre-flight's included, so no arm takes its model's default, which differs by
  model. Every row records it as `effort`. The effort is a launch input, so it changes the
  declaration's digest but not the image. `CLAUDE_CODE_EFFORT_LEVEL` outranks `--effort`, so the
  image manifest records whether it was baked into the image, and admission refuses it there or in
  the run environment. Each row also records `observed_effort`, the
  level the `init` event reports; a run whose observed effort differs from the pinned one is an
  errored row and stops the set, with no override. The pre-flight applies the same check and keeps
  both the pinned and observed values in its verdict. Claude Code sends that field only to Remote
  Control clients, so on a headless run it is normally `null` and the pin rests on the flag.
- **Every row records the container it ran in**: `arm_image`, `arm_image_id`,
  `arm_base_image`, `arm_declaration_sha256`, `arm_manifest_sha256`, and `harness_ref` and
  `harness_commit`, both `null` for the bare arm. The history row carries each arm's image id,
  manifest digest, ref and commit.
- **A run is `docker run --rm` with two scoped mounts**, except a pair's harness arms, which are
  kept until their ledger is copied out (see [Pairs](#pairs)). The task's snapshot is mounted at `/work`, which
  has no instruction file above it. A fresh run-owned `observations/` directory is mounted at
  `/observations`; it is the only other host path. Each native session gets a different empty
  directory outside protected host paths, with one ledger and error file. The host then retains
  those streams under the tag's output directory, which is never mounted into an arm. Every snapshot is
  made writable for any user, since the image's user id may differ from yours, and the images
  trust `/work` for git. Before anything is measured, each arm's container must write a mounted
  snapshot and have git read it, or the replay is refused with the reason. Every run, check and
  gate container is named, and one that times out is removed by name. The credential is
  passed by name, so its value is on no command line and in no row. The container runs with
  `no-new-privileges` and no capabilities, and one command line serves both arms: the same
  `--model`, the pinned `--effort`, `--strict-mcp-config`, `--max-budget-usd 2`, the task's own `max_turns` as
  `--max-turns`, `--permission-mode bypassPermissions`, since the container is the fence and a
  headless run cannot answer a prompt, and settings that deny `WebFetch` and `WebSearch` and
  register the same observation-only command in both arms.
- **Native observations belong to the replay.** The fixed observer component is declared by its
  source sha256, installed at the same path and compared by manifest parity in both arms. Each
  preflight and scored launch receives container-only ledger, error and profile values; no host
  home, profile or ordinary state path enters the container. Result rows name the ledger relative
  to the tag output and record `observation_rows` and `observation_errors`. A missing ledger row or
  any collector error makes the attempt an error without erasing its measured cost; the same
  condition makes a preflight red. The recorder stays silent and rows remain identifier-only.
- **The one way out is the model API.** Both arms sit on an internal Docker network with no route
  out. The only other container on it is `scripts/egress_proxy.py`, a standard-library CONNECT
  proxy run from the bare image. It listens only on its own address on that network, and is
  joined to the default bridge only once it has reported that address; it opens a tunnel to
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
- **Every row records the loaded surface, as the CLI reports it.** The stream's `init` event lists
  what the session loaded, so each row carries `init_skills`, `init_agents`,
  `init_slash_commands`, `init_tools`, `init_mcp_servers` and `init_memory_paths`, the length of
  each list, plus an `init_<name>_sha256` over each key's canonical reported members and
  `init_surface_source: cli-init`. The hashes detect a member replacement that leaves the count
  unchanged without claiming identities the event did not report. All fields are `null` for a
  stream with no `init` event, and one count and hash are `null` when the event lacks its key.
  Within a set, each run is compared with its own arm's first run that reported a surface, including
  its reported effort. A difference is written on the row as `surface_drift`, one
  `field: before -> after` line each, and stops the set once that row is written.
  `--allow-surface-drift` runs on instead and stamps every row of the set
  `surface_drift_allowed: true`. Beside `first_call_cache_write`, each row carries
  `first_call_context`, the first call's input, cache write and cache read together: the write
  alone moves with how warm the cache was and the total does not, so compare runs on the total.
  `backfill` derives all of these for sets already on disk; missing or unreadable raw output never
  replaces diagnostic evidence already present on a row.
- **Contamination is refused before either arm spends.** While the harness image contains a checkout
  of this repository, every issue task mined from the same repository is refused: commit ancestry
  cannot prove the files lack a cherry-picked, squashed or equivalent fix. The synthetic tasks are
  excluded too: the installed checkout contains their held-back oracle source and its `solve()`
  reference implementation, while an unrelated oracle failure would not prove the answer absent.
  An exposed task is refused before the writable-worktree probe or preflight. During a scored run,
  an observed tool input whose normalized path names `/opt/model-citizen` fails that attempt as
  `installed-checkout-read`; every row records the
  `installed-checkout-oracle-and-transcript-v1` control. The transcript check is evidence for the
  paths visible in tool inputs, not a complete filesystem-read audit: an unknown symlink, relative
  traversal or copied file may evade it. Prelaunch exclusion is the primary control. The installed
  runtime remains intact.
- **Which rules fired is read from the saved streams, with no model call.** `detect --raw <dir>`
  runs every detector in `policy/hooks/rule-detectors.py` over each `<task>-<arm>-<rep>.json`
  in the directory, a one-policy pair's `reference` and `treatment` arms included, and writes `detections.jsonl` there: one row per run per detector, with the
  detector, its rule, `count` and `turns`, the turn of each firing. A turn is the run's model
  call, counted from 1, and a tool result takes the turn of the call that asked for it. A
  subagent's own messages are not the run's, though its return is. Every detector runs in both
  arms whatever its stance gate says, since the bare arm has no stances to gate on. A stream
  with no model call, a stream that cannot be found, one found twice, and a detector that raised
  are rows with `count` null and the reason in `error`: unknown, never zero. An existing
  `detections.jsonl` is replaced only with `--overwrite`. With `--raw`, the replay does the same
  after each tag's set, before the next tag's runs overwrite the streams, reading each run only
  from the stream that run saved and still unchanged: stream names carry no tag, so a run that
  saved none, a timeout among them, gets error rows rather than an earlier tag's stream. It
  writes `detections.jsonl` beside that set's `results.jsonl`; its history row then carries
  `mechanisms`, which detectors fired in the harness arm per task and in how many of its runs,
  printed in `history.md` under the task lines. The mechanism record keeps each task's total run
  count, each firing detector's measured-run denominator, and detector errors with their reasons,
  so an unreadable stream cannot silently disappear from attribution. `detect --backfill <root>`
  does it for sets already on disk: it finds every `results.jsonl` under the root, looks for each
  row's stream in the same directory, a sibling `transcripts` or `raw` directory and their
  subdirectories other than a nested set's, and writes `detections.jsonl` beside it, leaving an
  existing one alone without `--overwrite`. A stream in a directory another set also searches,
  as sibling tag sets sharing one `../raw` do, could be either set's, so both get error rows.
  It reads `results.jsonl` and never writes it. The backfill of the evidence sets already on
  disk is an owner-run step and has not been run yet.
- **Each arm is proved before anything is scored.** One capped `-p` run per arm runs
  `bin/harness lint` in that arm's own container; an arm whose lint is not clean, or whose run has
  a read refused, refuses the whole replay with exit 2 before any scored run launches, and its
  cost counts against `--spend-cap`. The bar is lint rather than the full suite because the suite
  is profile-dependent at older snapshot commits. Every scored row records `preflight`.
  `--skip-preflight` bypasses the check and stamps the rows `skipped`.
- **Every run starts in a throwaway snapshot.** The snapshot holds one commit and its ancestors, so
  the change that solved a task is not reachable from it, and it is removed after scoring. The
  harness arm's installed checkout is checked separately by the contamination control above.
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
- **Every attempt counts.** An errored, crashed or timed-out run is a failed attempt whose cost is
  in the arm's cost per passed task, as the evidence standard's intention to treat requires; its
  `error` field keeps it countable apart and its `outcome` is `fail`. A timed-out run with no
  readable cost is charged at the per-run cap; another error with no readable cost leaves the
  figure undefined rather than cheaper. The per-run cap is soft, so the runner also stops
  before any launch that could take reported spend past `--spend-cap`.
- **SM-2 decides the result, from the saved rows alone.** Every row names its `task`, `arm`, trial
  (`rep`), `outcome` (`pass` or `fail`), `cost_usd` and `task_long`, so `summarise` re-derives
  every figure from `results.jsonl` without calling a model; rows that saved no pass or fail, as
  the 2026-09-23 runs did, are refused rather than scored. Duplicate trials, arm trial-set
  mismatches across arms or tasks, contradictory outcomes, inconsistent long-task markers, boolean
  costs and non-finite pooled costs or ratios are refused too. A replay exclusively creates its
  results file before probes or model calls; even an existing empty file is refused, so concurrent
  runs cannot mix cohorts. After an interrupted run, choose a fresh output path. It reports, per arm, Cost-of-Pass (the
  total cost of every attempt over total passes, pooled across the set) and the pass rate with a
  Wilson 95% interval, which is descriptive only. The harness-over-bare ratio and the pass-rate
  difference (harness minus bare) carry paired, task-clustered 95% intervals from a percentile
  bootstrap that resamples tasks and keeps both arms' trials of a task together; it prints its seed
  and resample count (`--seed`, `--resamples`, default 795 and 10,000) and gives the same interval
  for the same seed. The ratio is undefined when either arm passes nothing or a run has no
  readable cost, and an interval bound that lands on a resample where an arm passed nothing is
  undefined too; those sentinels never masquerade as zero or infinity. A real zero ratio remains
  valid when the harness arm has zero cost and both arms passed.
- **The verdict is supported, not supported or inconclusive, whatever it shows.** Supported needs
  the ratio's interval wholly below 1.0 and the difference's lower bound above -0.125. Not
  supported means the data rule that out: the ratio's interval wholly at or above 1.0, or the
  difference's wholly below -0.125. Anything else is inconclusive, with the reason. "At least 15%
  cheaper" is claimed only when the ratio's upper bound is at or below 0.85. A task marked
  `"long": true` in `benchmarks/tasks.json` joins the long-task subset, whose ratio interval is
  reported beside the whole set's, and a saving is claimed only when it too lies wholly below 1.0.
  With no marked long-task subset, no saving is claimed. Fewer than five paired trials per task and
  arm remain available as an exploratory diagnostic, but are explicitly ineligible for an SM-2
  proof or saving claim. No task is marked yet.
- **A Pareto view sits beside it:** `summarise --plot <file.svg>` writes a standalone cost-versus-pass-rate plot; unpriced arms have no plotted coordinate. The text report also gives a table of each arm's mean cost per attempt against its pass
  rate, naming the arm on the frontier and any arm another dominates.
- **Whether delegation fired is reported under SM-2, as adherence, not as the result.** Each row
  records `spawn_offered` (the `init` event listed `Agent` or `Task`), `spawns` (spawn calls whose
  result is not an error, made on the main thread or inside a spawned subagent, never inside a
  `Workflow` agent), `gather_calls` (`Read`, `Grep` and `Glob` calls in every thread),
  `absorbed_calls` (those inside a counted spawn's thread) and `workflow_launches`, which are
  reported beside spawns and never counted as one.
  Output that cannot show a call, such as a lone result, leaves all four counts `null`, never 0.
  `summarise` then prints one verdict per task, and under `--json` adds it as a `delegation` key:
  `fired`, `declined-below-break-even`, `missed-above-break-even`, `not-offered` or `unknown`.
  Rows carrying `error`, such as a timeout or an effort mismatch, are left out and counted. A
  task's size is the median of the clean bare runs' gather calls, unknown unless each reported one.
  Fired means a spawn in at least 75% of the clean harness runs, the share #513 registers, and
  fewer than four clean harness runs read `unknown`. The break-even is FR-34's 7.6 absorbed calls,
  hypothetical; `--break-even` overrides it and the report labels it an override. Missing data
  reads `unknown`, never a decline. The block's `registered` is true only when every row came from
  a run that named a pre-registration; otherwise each verdict prints as exploratory. The registered
  reading that closes #429 is #1104. The mean cost of spawning and non-spawning runs is shown beside the verdict and is
  descriptive, not causal. The same block is in each history row under `delegation`. The rules
  are in `scripts/delegation_verdict.py`.
- **`benchmarks/history.jsonl` holds one row per harness version per run day**, stored as a ratio to
  bare on the same day and model; `benchmarks/history.md` is rendered from it. Compare ratios across
  days, never dollars. Each row carries the SM-2 result under `sm2`, printed under its ledger line.
  The older mean-of-reps `status` (at most 85% of bare per passed task, passing no fewer than bare
  minus one) is kept beside it so earlier rows stay comparable; SM-2's verdict is the one that
  decides a claim.
- **What is faked:** 0 of the 0 tasks are synthetic because no live replay task is currently
  eligible. The retired synthetic tasks used single-shot prompts in place of interactive sessions,
  and a tagged run measures the tag's default configuration rather than a configured one. The arms
  run on Linux; a macOS arm is not built.
- **A task that cannot be passed honestly leaves the set** and moves to the manifest's `retired`
  list with its reason and date. The five issue-derived tasks left on 2026-09-28 because the
  installed harness checkout can reach their known-good commits. `usage-prices` had already left
  on 2026-09-25 because its held-back tests pin live prices and helper names its prompt never gives.

**Status.** The current manifest has no eligible replay tasks, so the runner refuses before probes,
preflight or model calls. The replacement set is the evaluator pack below, whose answers the
installed harness cannot carry (#796); a powered run of it waits on its pilot. The earlier live tier
produced one result of 1.052 on a four-task set, above
the 0.85 threshold, so no cost claim is published. Two earlier figures in either direction were
artifacts of the runner's sandbox and of a test-suite defect, both since fixed. A review on
2026-09-24 found six more ways the arms were unequal or a task unfair: the task count above, the
turn cap, the stop gate, web access, `usage-prices` and hook capture. Each is fixed as described
above, and no result has been taken since. The arms have since moved from host profiles to
containers, which starts a new series. Treat this tier as an instrument whose methodology is under review, not as a result; the static tier above is the
figure to rely on today.

### Evaluator pack

`--pack` runs tasks kept outside this repository, so the harness arm's installed checkout cannot
hold a task's check or answer. The pack is its own git repository, `model-citizen-evals`: a
`pack.json`, starting workspaces, and per task a `task.json`, a held-back `check.py` and a reference
`solution.py`. Its README gives the layout.

```sh
python3 scripts/cost_bench.py replay --pack <pack repo> --verify-tasks                # every check fails, then passes once solved
python3 scripts/cost_bench.py replay --pack <pack repo> --tag <full commit> --exploratory --dry-run  # schedule and contamination per task
python3 scripts/cost_bench.py replay --pack <pack repo> --pack-ref v1.0.0 --pack-digest <digest> \
    --model <id> --tag <full commit> --pre-registration <plan>
python3 scripts/cost_bench.py replay --tier micro --pack <pack repo> --pack-set delegation-nudge ...
python3 scripts/replay_power.py --pilot <results dir>                   # k, n and m for SM-2
```

- **It is read from a pinned commit, never a working tree.** `--pack-ref` (default `HEAD`) is
  extracted with `git archive`; the digest is the sha256 of every archived path and its bytes. A
  registered run must name `--pack-digest`, the run refuses any other, and every row carries
  `pack`, `pack_version`, `pack_commit` and `pack_digest`. Any change to the pack bumps its
  version, so a frozen set is a version and a digest. A pack inside this repository, or containing
  it, is refused.
- **An arm sees only the task's workspace.** It is copied into a fresh git repository with one
  commit; no check, solution or pack file goes with it. The check is sent on stdin to the scorer, a
  fresh container of the bare image with no network and no credential that mounts only the agent's
  tree, as every synthetic check is. `--verify-tasks` runs each workspace's own gate, then proves
  the check fails on the workspace and passes after the reference solution.
- **The contamination control checks every pack task at the exact harness commit,** before any
  model call and in `--dry-run`, which prints one line per task and exits 2 when any is refused.
  A task is refused when any commit in the installed checkout's history holds the exact bytes of
  its check or solution, or when the checkout or its history carries the pack's canary, a string
  every check and solution includes. The transcript check on `/opt/model-citizen` still applies.
- **A set is chosen by tier.** `production` runs by default; `--tier micro` runs the `micro` set, or
  the set `--pack-set` names, at the model that set pins. `--pair` and `--tasks` are refused with
  `--pack`; `--ablations` takes one, as [Ablation runs](#ablation-runs) describes.
- **Long tasks and absorbed calls.** A task's absorbed-call size is the median, over clean bare-arm
  runs, of its `gather_calls`: the `Read`, `Grep` and `Glob` calls in every thread. The bare arm
  never delegates, so that count is all the gathering a subagent could have absorbed. A task is
  long when the median is above 7.6, FR-34's upper break-even. Each pack task states
  `expected_absorbed_calls`, the files a correct solution must read plus one search, and the pack
  refuses a `long` mark that disagrees with it; the pilot's rows confirm each mark or a new pack
  version removes it.
- **The power command sizes the set.** `scripts/replay_power.py` takes pilot rows, or the
  variances stated directly, and prints the design with the fewest trials per arm whose decision
  power (the ratio test and the pass-rate test) and claim power (those and the long subset's ratio
  test) both reach 0.8 at α 0.05 and an effect of at most 15%, with at least five trials per task
  and arm. `--have K N M` says whether a given set meets it. Its model and its approximations are
  in its docstring.

### Pairs

`replay --pair <manifest>` judges one policy change on what the whole task costs. It runs one
harness image twice, as a reference arm and a treatment arm that differ in one session-scoped
selection, with the bare arm beside them in every trial.

- **An ablation manifest declares the pair.** `benchmarks/ablations/<name>.json`, schema 1, holds
  `name`, `tag` (one release tag or full commit), `factor`, and the `reference` and `treatment`
  values. The factor is a `HARNESS_STANCE_<DIMENSION>` variable or `HARNESS_MODE`, and each harness
  arm is given its value by value; `null` leaves the tag's default. The two values must differ. A
  change that exists only at build time cannot be a pair factor.
- **Three arms, the lead rotating.** Every trial runs bare, reference and treatment, and the arm
  that goes first rotates through all three across trials, so none always runs on another's warm
  cache. Each row names its `arm`, the `ablation` it answers (name, digest, schema and its
  `factors`, a list) and the `selection` its arm ran with, so a design of more factors writes the
  same row keys. `summarise` recognises a pair by its `ablation`, so a pair stopped at its spend
  cap before every arm ran is still reported as a pair, each unfinished trial named.
- **Parity is checked before launch and after the run.** Before anything is spent, the two
  harness arms' launch specs (image id, declaration and manifest digests, commit, model, run cap,
  command line, environment by value, network, proxy, credential name, protocol stamp, and the
  task file and price table digests) must differ in the factor and nothing else, and this
  checkout's resolver must give the two selections different profile fingerprints; either
  refusal names every difference. `replay --pair` launches both harness arms from one image
  record with `--stance-cost` refused, so the spec check holds by construction there; it guards
  arm records assembled any other way, such as ones read back from the arms directory. After the run, `summarise` compares each trial's loaded surface
  between reference and treatment, and exits 1 naming each trial that differs.
- **Decision calls are copied out, never mounted in.** A harness arm's container is kept after it
  exits; its usage ledger is copied out with `docker cp`, then the container is removed by name,
  on a timeout too. With observation on, the session's native streams are archived after that
  removal, so both the ledger copy and the observation archive run on every outcome. Only `kind: "decision"` rows are kept, in `decisions/` beside the results.
  Every row records `decision_ledger`: `read`, `absent` for the bare arm, or `unknown: <reason>`
  when the copy failed, which is never read as no calls. The egress rule is unchanged, so a call
  to a remote decision provider cannot complete from an arm; its row, if any, is unpriced, and the
  report labels it `blocked by egress` when the row records a network failure.
- **Costing.** Per arm, `summarise` reports attempts, passes with a Wilson interval, pooled
  Cost-of-Pass for workers alone and with the arm's decision calls, total and per-attempt wall
  time, and `respawns_up` (a brief spawned again on a stronger model class, ranked by the classes
  in `adapters/claude-code/bindings.json`) with `spawns_unranked`. A decision row joins its attempt
  by session id; one from another session still counts in its arm and is named unmatched. Each is
  priced from `policy/prices.json`; an unpriced call or an unread ledger leaves the with-decisions
  figure undefined and is named, never zero. Decision latency ran inside the arm's wall time, so it
  is reported as a share of it and never added.
- **Intervals, no verdict.** Treatment over reference, and each harness arm over bare, carry the
  paired, task-clustered intervals SM-2 uses, on worker cost and on cost with decisions, beside a
  three-arm Pareto view. No SM-2 verdict is printed: its decision rule is defined for harness
  against bare.
- **A pair writes `results.jsonl` and no history row**, whose series is harness against bare.
  `--spend-cap` is required for a live pair, since the default is sized for two arms;
  `--stance-cost` is refused, and `--tag`, if given, must be the manifest's.

### Ablation runs

`replay --ablations benchmarks/ablations.json` attributes cost to single entries of the harness:
bare, control (the harness at the tag) and one arm per entry the manifest toggles, each reported
against control. A schema-1 pair file given to `--ablations` runs exactly as `--pair` does.

```sh
python3 scripts/cost_bench.py replay --tasks tests/fixtures/ablation-tasks.json \
    --ablations benchmarks/ablations.json --tag <full commit> --model <exact id> --exploratory --dry-run
python3 scripts/cost_bench.py replay --pack <pack repo> --pack-ref v1.0.0 --pack-digest <digest> \
    --ablations benchmarks/ablations.json --tag <full commit> --model <exact id> --run-cap 0.10 --dry-run --exploratory
python3 scripts/cost_bench.py summarise --results <dir> [--correction bonferroni]
```

- **The manifest is schema 2.** It holds `name`, `planning` (`cv`, the assumed per-attempt
  coefficient of variation, and its `source`) and `arms`, each an `id` and exactly one of
  `removes: "<kind>/<unit>"`, which switches a module off, or `sets: {"<kind>/<unit>": "<variant>"}`,
  which gives a variant kind another variant. Bare and control are implicit, so N arms run N + 2.
- **Each arm's selection is in its image, not its environment.** The arm is declared with its
  selection in the user-config shape (`{"rules": {"secrets": "off"}}`); the image installs it as
  the agent user's configuration before the sync, which then withholds what it switches off.
  Admission accepts that file only when its sha256 is the declared `selection` component, and
  refuses a configuration nobody declared. Pairs keep their by-value factor; the two paths do not
  mix.
- **Refused before any spend:** an id the tag's default selection does not hold switched on, a
  variant the tag does not ship, a selection the tag's resolver refuses, more than one `--tag` or
  `--stance-cost`; then, once the images are built, an arm whose declaration differs from
  control's in anything but its selection, or whose selection resolves to control's profile.
- **On an evaluator pack, the contamination control runs at control's commit,** the one `--tag`
  resolves to and every arm installs, before any image is built or arm launched: `--dry-run`
  prints one line per task and exits 2 when any is refused, and a real run stops with exit 2. A
  registered run must pin `--pack-digest`, and every row carries `pack`, `pack_version`,
  `pack_commit` and `pack_digest`. The micro tier and `--pair` stay refused with ablations.
- **The worst-case cost is printed before the schedule:** every run at the run's `--run-cap`, or
  the named default when none is given, and every preflight at its cap.
- **The minimum detectable effect is printed before the schedule**, from the manifest's `cv` at
  80% power and 95% two-sided, alone and with Bonferroni over the arms. It is a planning figure
  from an assumption, not a measurement; an effect below it reads `inconclusive`.
- **The order is drawn from a recorded seed.** The leading arm rotates with the rep, as for any
  replay, and the arms after it follow a permutation from `--schedule-seed`, which defaults to the
  manifest's digest. Every row records `schedule_seed`, its `ablation` (name, digest, schema 2,
  the arm ids), the entry its arm removed (`ablation_removes`) or set (`ablation_sets`) and its
  `selection`; its `context_attribution` is resolved with that selection, so a removed module's
  key is absent from it.
- **Each arm is reported against control on six measures, separately:** cost, output tokens, the
  standing prefix, turns, tool calls and pass rate, then Cost-of-Pass last, never as the headline.
  Each carries n, its spread (SD, and the worst per-task max over min) and a paired interval from
  the task-clustered bootstrap SM-2 uses. An interval spanning no effect reads `inconclusive`;
  fewer than five paired trials per task, or several arms with no `--correction`, reads
  exploratory. Arms are ranked by the size of their cost effect.
- **The prefix is the first call's whole prompt.** `first_call_context` sums the first call's
  input, cache-write and cache-read tokens, so a cold and a warm run of one prompt agree, and a
  missing field makes it None, never a smaller sum. An arm's prefix is compared only when every run
  of it and of control lies within 2% of its (task, date) median; otherwise its summed
  `context_attribution` stands in, labelled a soft estimate.
- **Parity after the run.** `summarise` exits 1 when an arm loaded a surface that differs from
  control's beyond the fields its entry may move (a skill or workflow its skill and command
  listings, a role the agent listing, a rule or stance the memory paths), or when a removed entry
  is still in a row's attribution. A pair's treatment declares no surface change, so any
  difference still refuses it.
- **No history row**, as for a pair; `citizen scorecard --results <dir>` reads the rows to fill each
  module's measured effect. One-at-a-time toggling finds main effects only: two entries that matter
  only together read as two inconclusive results. A sweep over a profile of your own is a local
  diagnostic, not a publishable figure.

### Micro tier

`replay --tier micro` asks a cheaper question than the production set: does a mechanism fire at
all. It runs the three tasks in `benchmarks/micro/tasks.json`, each built to give one mechanism the
chance to fire, on the small model that manifest pins, in the bare and harness arms.

```sh
python3 scripts/cost_bench.py replay --tier micro --verify-tasks
python3 scripts/cost_bench.py replay --tier micro --tag <release or full commit> --exploratory --dry-run
python3 scripts/cost_bench.py replay --tier micro --tag <release or full commit> --raw <scratch dir> \
    --pre-registration <plan>
```

- **Its protocol is five reps, 0.10 USD per run, 0.05 USD per preflight and a 4.55 USD stop.**
  Thirty scored runs and two preflights report 3.10 USD if every one reaches its cap; the per-run
  cap is soft, so the stop is the binding limit. `--reps`, `--run-cap` and `--spend-cap` may change
  them; `--model` may not, and `--pair` is refused. The dry run prints the set's ceiling.
- **Each run reports pass or fail, whether its mechanism fired, and its cost.** The oracle scores
  pass or fail as for any synthetic task. `mechanism_fired` on each row is `true`, `false` or
  `null`: delegation reads the row's `spawns`, the stop gate its Stop-hook `hook_blocks`, and the
  output style the offline detectors named in the manifest, which must all report no hit. A missing
  stream or a detector row without a count is unknown, never "no", so a real run needs `--raw`.
- **Its rows never meet production rows.** They carry `tier: micro`, seed their own series and go
  to `benchmarks/micro/micro-history.jsonl` and `micro-history.md`; `upsert_history` refuses to
  write one tier's row into a file holding the other's, whichever `--history-dir` is named. The
  micro history carries no ratio and no verdict.
- **Its manifest here is refused by the contamination control, as every same-repository task is.**
  Its oracles are in this repository, so the harness arm's installed checkout exposes them. The
  evaluator pack's `micro` set re-homes the three mechanisms in a workspace of their own, with its
  checks outside this repository; run it with `--tier micro --pack <pack repo>`. Its
  `delegation-nudge` set holds the delegation task above break-even and its two-file control.
- **What it can claim:** that a mechanism can fire, and did, on the pinned small model in these
  tasks. **What it cannot:** that it fires on the production model, how often it would, or
  anything about what the harness costs or saves. A small model's behaviour is not the production
  model's; the production set stays the release calibration.

## Limits

- Claude Code only. Codex instructions are rendered at sync time and are not counted.
- Your own instruction files, memory, MCP servers and hooks are not counted here; `citizen usage
  --surface` lists them locally beside the harness's modules (see [usage](usage.md#the-loaded-instruction-surface)),
  and they never enter an arm.
- Full agent and skill bodies load only when used, so only their descriptions are counted.

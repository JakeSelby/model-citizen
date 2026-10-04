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
  exact regular files its sync and trust commands generate (the global git ignore file, the sync's
  ownership record and its lock file, only while that is empty, among them), the empty plans
  directory the sync makes, and only the parent directories needed to reach those entries. A
  treatment file the bare arm holds is refused too. Unrelated files and links remain part of pair
  parity even when they sit under `.claude`, `.codex` or `.local/bin`.
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
  snapshot mounted, plus the scored run's session stream read-only when the run supplies one, and
  nothing else; the image's own HOME, no network and no credential; an oracle is sent on stdin.
  `--verify-tasks` runs each task's gate and both of its checks the same way, building the bare
  arm first unless `--check-image` names one: on your machine an older snapshot's code reads your
  live configuration through HOME and goes red for that.
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
  subagent's own messages are not the run's, though its return is. A detector's stance gate is
  its applicability: a gated detector scores a run only when the selection its arm ran with
  enables it, so the concise voice's `voice/scaffold-leak` never scores the default `scannable`
  arm, and no gated detector scores the bare arm, which has no stances. The selection is the
  row's `arm_config`, a pair row's `selection`, or the defaults for the harness arm; an arm whose
  selection no row records leaves its gated detectors unknown. Such a row is `not_applicable`,
  with `count` null: never a hit, never clean, and skipped by the all-rules-at-once rate. A stream
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
- **Every trial starts cold, and the report says so.** Reps of one task and arm send the same
  prompt, so within the cache lifetime a later rep read the earlier one's session segment, the
  first message after the system prompt that holds the memory files and the prompt, instead of
  writing it. That favoured the arm with the larger segment: in the 2026-10-02 pilot's saved
  streams every rep read the 23.6k-token system prompt and tools from cache, rep 1 wrote the rest
  (3.9k bare, 14.4k harness) and reps 2 to 5 read it all, which put the Cost-of-Pass ratio at
  1.42 against about 1.66 with every session paying its own write (#1174). Each trial now mounts
  a one-line file holding its own nonce as Claude Code's managed memory file,
  `/etc/claude-code/CLAUDE.md`, the first memory it loads, so the nonce opens the session segment
  and every trial writes it, while the system prompt stays as cached as a real session finds it.
  A nonce in the task prompt would not do: the prompt comes after the memory files. Both arms get
  the file, so they still differ by image alone. Each row records `cache_nonce` and
  `cache_basis: cold`; `summarise` states the basis on its first line and as `cache_basis` in
  JSON, and reads rows without a distinct nonce on every row, any from before this change, as
  `shared`.
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
- **Reliability and joint rule compliance follow, under `--json` as a `reliability` key.**
  `pass_k` gives, per task and arm, whether every trial passed and the unbiased pass^k estimate
  C(c, k) / C(n, k) from its n trials and c passes, with k the fewest trials any cell ran; per arm
  it gives the share of tasks whose every trial passed, the mean estimate and pass^1. `joint` is
  the all-rules-at-once rate: the share of runs in which no rule-violation detector fired, read
  from the `detections.jsonl` beside the rows, with a Wilson interval and each detector's own rate
  beside it. A run no detector could read is `unknown` and left out of the rate, never counted as
  clean, and a `not_applicable` row is skipped; `joint` is `null` when the set has no detections. The rules are in
  `scripts/replay_reliability.py`.
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
python3 scripts/equivalence.py <results dir> --plan <plan>          # equivalence verdicts
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
  tree and, for a scored run, its session stream read-only, as every synthetic check is.
  `--verify-tasks` runs each workspace's own gate, then proves the check fails on the workspace
  and passes after the reference solution.
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
  in its docstring. A pilot that passes everything or nothing is sized only with the
  pre-registered `--assumed-pass-rate`, which the output names as an assumption; a pilot with fewer
  than two tasks passing in both arms, the floor included, also needs the pre-registered `--tau2`.
  `--mde` sets the minimum detectable effect.
- **A null has bounds.** `scripts/equivalence.py <results dir> --plan <plan>` reads each metric's
  task-clustered interval against the plan's Equivalence margins and prints equivalent, not
  equivalent or inconclusive; the rule is in the
  [pre-registration template](pre-registration-template.md#decision-rule). A verdict is labelled
  exploratory unless every row records that plan as its pre-registration, each task and arm has
  five trials, and a behaviour score has five known values in each.

### Oracle metrics

A task's check may return named numbers beside its pass or fail, so behaviour such as correctness,
conciseness or format adherence lands on the same rows without changing how a run is scored.

- **The task declares them.** `"metrics": {"<name>": "higher" | "lower"}` in a pack's `task.json`,
  or a task of `benchmarks/tasks.json`, names each metric in lower snake case and which way it
  improves. Loading refuses an empty or `null` declaration, any other direction and any other
  name, and any declaration on an `issue` task, whose unit tests report no metrics.
- **The check returns them.** `check(root)` returns the original verdict, a list of error strings
  that passes when empty, or `{"pass": <bool>, "metrics": {"<name>": <number or null>},
  "errors": [<str>]}`, with `metrics` and `errors` optional. Either form keeps working; a task that
  declares no metrics writes rows byte-identical to before.
- **A check may read the session.** A check written `check(root, stream=None)`, or any check
  taking a second positional argument, is called with the path of the scored run's whole
  stream-json, subagent messages included; a one-argument `check(root)` is called as before. The
  runner reads the signature from the check's source without running it, so `check` must be a
  top-level `def`; nothing the check prints can change that call or `metric_stream`. The stream is
  written beside the run's tree
  and mounted there read-only at `/session-stream.jsonl`, whether or not `--raw` keeps a copy.
  `--verify-tasks` passes no stream. A declaring task's rows carry `metric_stream`, true only when
  the stream reached the check. Without it, `metric_errors` says so and the check's stream metrics
  are `null`; with it, the check's own errors beginning `stream metrics unknown` are copied into
  `metric_errors`, or, when it gave none, `stream metrics unknown: the check gave no reason for null`
  and the metric names, so a `null` stream metric always carries its reason.
- **A broken check is a check error, never a fail.** A `pass` that is not a boolean, an unknown
  key, `metrics` that is not an object, or a metric the task does not declare makes the attempt
  `error: true` with `error_kind` `check: ValueError`, as any check that cannot run does.
- **A bad value is unknown, never 0.** Each row of a declaring task carries `metrics`, every
  declared name to a number or `null`, `metric_directions` and `metric_errors`. A metric that is
  not reported, not a number or not finite is `null` with its reason in `metric_errors`; an explicit
  `null` is the check saying it could not measure, with no reason recorded. A run that errored
  before it was scored has every metric `null`.
- **`summarise` reports each metric per arm and per task:** its mean over known values, how many
  were known and how many unknown. The harness-minus-bare difference uses the tasks with a known
  value in both arms and carries the paired, task-clustered percentile interval SM-2 uses, with the
  same seed and resamples. It reads `better` or `worse` by the declared direction when the interval
  excludes zero, `inconclusive` otherwise, and never enters SM-2's verdict; a difference that is not
  a finite number leaves it `unavailable`. Under `--json` it is the
  `metrics` key, absent when no row carries metrics. Pair, ablation and two-by-two reports do not
  read metrics yet.

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

#### The layer sweep and its justification rule

`benchmarks/ablations.json` holds one removal arm for every layer that costs tokens or turns: each
rule, each stance with an `off` variant, the hooks that act in a headless session (the delegation
nudge's `tier-agent-spawns`, `filter-output`, `usage-feed`, `stop-gate`, `grade-bash` and five
more) and the skill, agent and command listings, each as a whole.

```sh
python3 scripts/ablations.py plan --pack <pack repo> --model claude-haiku-4-5 --model claude-sonnet-5
python3 scripts/ablations.py justify --manifest benchmarks/ablations.json --results <dir>/results.jsonl [...]
```

- **Each arm says what it removes, and the plan says how that is proven.** An arm names its
  `layer` and `what` it removes in words. A listing's `removes` is a list: every unit of its kind,
  plus any unit of another kind that the module manifest binds to it, such as the two design roles
  that cannot stay on without the `design-loop` skill. `check_entries` refuses a listing that leaves
  a unit on or carries an unbound one. A core hook's arm declares `core_switches_acknowledged` in
  its selection, as a user must. The plan prints each arm's verification: its declared selection,
  the admission check that its declaration differs from control's only there, the surface fields it
  may move (none for a hook) and the entries its attribution may not hold.
- **Each arm runs its own tasks and the outcome subset.** `tasks` are pack v1.3.0 `rules` tasks
  that target the layer, `long_session` the long-session scenarios where the layer is a cost
  control, and `outcome.tasks` the ten tasks every arm also runs: the seven production tasks and
  three rule tasks. In a replay, a task the manifest names runs on bare, control and the arms that
  map it; a task it does not name runs on every arm. The worst case is that plan's run count at the
  run cap.
- **The justification rule is pre-registered in the manifest.** Each arm's `scores` pair a metric,
  either a pack metric or `Cost-of-Pass ratio`, with its equivalence margin, read on its own tasks
  or on the outcome subset when it has none. `outcome` holds the pass-rate margin. `justify` reads
  each interval as `scripts/equivalence.py` does, from the arm's paired rows against control. A layer
  is **keep** when removing it worsens a score or the outcome beyond the margin. It is **trim** when
  each is equivalent within its margin or better beyond it. Otherwise it is **no evidence**. Its
  marginal cost, control's mean cost per attempt minus the arm's, is reported beside the verdict and
  never decides it. Fewer than five paired trials per task label it exploratory.
  `verdicts_by_entry` keys the verdicts as a scorecard row is.
- **The price is a ceiling, not an estimate.** Each task run is its `max_turns` at the manifest's
  per-turn token envelope, at the model's rates in `policy/prices.json`, never above the run cap.
  Each long-session run is its scenario's own cost cap. The envelope is an assumption, labelled with
  its source, until a sweep's rows replace it. The plan refuses a score that none of its tasks
  declares.
- **Not every layer is removable yet.** `unbuilt` names `CLAUDE.md`, which no selection switch
  withholds, and the autonomy and plan-ceremony stances, which ship no `off` variant. `excluded`
  names the hooks a headless replay never fires. The plan prints both, so a gap is never silent.
  Long-session rows are planned and priced but `justify` does not read them yet.

### Arm configs

The shipped default is not the only configuration worth measuring. `replay --arm-config
NAME=PATH`, repeatable, adds one harness arm per config beside bare and harness, each built from
the tag with a named stance selection declared into its own image.

```sh
python3 scripts/cost_bench.py replay --tasks <tasks> --tag <full commit> --model <exact id> \
    --arm-config maintainer=benchmarks/arms/maintainer.json \
    --arm-config frugal=benchmarks/arms/frugal.json --exploratory --dry-run
```

- **A config names stance dimension to variant.** `{"schema": 1, "description": "...",
  "stances": {"voice": "concise"}}`; any other key is refused. `benchmarks/arms/maintainer.json`
  is the maintainer's configuration, the default stances with the concise voice in place of
  scannable; `benchmarks/arms/frugal.json` is the frugal cost tiering, which routes gathering to
  the light class, measured against the balanced default.
- **Checked against the tag before anything is planned.** Each config is read against a clone of
  the tag's commit, through its own resolver with an empty home: a dimension the tag does not
  ship, a variant it does not ship, a selection its resolver refuses, or one that is the tag's
  default throughout is refused, naming what exists. The arm name must be lower-case letters,
  digits and hyphens, and none a run already uses (`bare`, `harness`, `control`, `reference`,
  `treatment`).
- **Declared, digested and admitted like any harness arm.** The selection is the arm's
  `selection` component, so its image name moves with it. Before any spend the arm passes the
  pair check against bare that every harness arm passes, its declaration must equal the harness
  arm's less its selection, and its selection must resolve to a profile other than the harness
  arm's.
- **Rows record the config.** Every row carries `arm_config`: `null` for bare and harness, and for
  a config arm its name, schema, `stances` and `sha256`, the digest of the config's canonical
  JSON, so reformatting the file never moves it. The dry run lists each config arm with that
  digest and its image, and schedules it with the leading arm rotating.
- **Limits.** `--spend-cap` is required, since the default is sized for two arms; `--stance-cost`,
  `--pair`, `--ablations` and `--design` are refused beside it. A run with config arms writes
  `results.jsonl` and no history row. `summarise` does not report config arms yet: it refuses
  their rows as an unknown arm.

### Unit evals: the two-by-two

`replay --design unit-economy --unit <kind>.<id>` measures one rule, skill, role, workflow or hook
in a minimal profile, alone and with the economy concern switched on, so a unit eval never pays
for the full context. It separates the unit's effect from the economy concern's and measures how
the two interact.

```sh
python3 scripts/cost_bench.py replay --tasks <manifest> --design unit-economy --unit rules.secrets \
    --tag <full commit> --model <exact id> --exploratory --dry-run
python3 scripts/cost_bench.py summarise --results tests/fixtures/unit-economy [--json]
```

- **Four cells from one base, plus bare.** The cells are `base` (nothing added), `unit` (the unit
  alone), `economy` (the economy concern alone) and `both`. Bare, with no harness, runs as a fifth
  arm outside the factor analysis, and each cell's Cost-of-Pass ratio to bare is descriptive.
- **The base is derived from the tag's catalog, not hand-listed.** Every rule, skill, role,
  workflow and non-core hook is off, the four core hooks stay on, every stance is at `off` or its
  smallest variant, and the economy members are at their off values. The unit's declared
  dependencies are on in every cell.
- **The economy concern is declared in `benchmarks/unit-economy.json`.** It lists each member with
  its off and on values: `cost` from `off` to `balanced`, `delegation` from `session-model` to
  `tiered`, and the `tier-agent-spawns` and `usage-feed` hooks from off to on. `brief-guard` is
  core, so it is on in every cell.
- **Refused before any spend.** The tag's own resolver reads all four selections. Each edge of the
  square must then differ in exactly its factor, and the diagonal in exactly both. Also refused:
  a unit that is a member of the economy concern or depends on one, a core hook, a stance, a unit
  in a dependency cycle, and any resolver refusal. Once the images are built, a cell whose
  declaration differs from the others' beyond its selection is refused, as are two cells that
  resolve to one profile.
- **Every cell is its own declared-selection image**, as for an ablation arm. The schedule is the
  ablation's: the leading arm rotates with the rep, so at five reps each arm leads once per task.
  The rest follow `--schedule-seed`, which defaults to the manifest's digest. A real run needs
  `--spend-cap`, because the default is sized for two arms, and `--raw`. The dry run prints the
  nominal cost, which is every run at its cap.
- **Rule adherence comes from the unit's own detectors.** After the set, each `detector:`
  instrument in the unit's manifest is run over every saved stream, ungated, so a cell that does
  not load the unit is measured on the same behaviour. A run is `hit` when any detector fired,
  `compliant` when none did, and `unknown` when a stream could not be read and none fired. A unit
  with no detector is `unmeasured` on every run, never zero.
- **The report gives three simple effects and their interaction on three metrics:** the unit
  alone (`unit` against `base`), the unit with economy on (`both` against `economy`), and economy
  alone (`economy` against `base`). For Cost-of-Pass each is a ratio, and the interaction is the
  ratio of the two unit ratios. For pass rate and rule adherence each is a difference, and the
  interaction is the difference of the two unit differences. One task-clustered paired bootstrap
  computes every figure from the same task draws. A resample with an infinite cost on both sides
  is indeterminate: it is counted, and it can only widen an interval.
- **One primary contrast.** SM-2's decision rule applies only to the contrast the pre-registration
  names, which defaults to the unit alone on Cost-of-Pass with pass-rate non-inferiority. Every
  other figure is descriptive. Every effect is intention to treat.
- **Result schema 1** (`summarise --json`) holds `schema`, `design` (`unit-economy-2x2`), `unit`
  (kind, id, instruments), `economy.members`, `base_selection_sha256` and `cells`. Each cell gives
  its attempts, passes, errors, cost, Cost-of-Pass, a pass rate with a descriptive Wilson interval,
  and `rule_adherence` (scored, compliant, unknown, rate, interval) or `unmeasured`. The schema
  also holds `bare`, `effects.<metric>.<contrast>` (value, interval, undefined reason), `primary`,
  `verdict`, `reason`, `claim`, `sm2_eligible`, `limitation`, `estimand`, `method`, `seed`,
  `resamples`, `indeterminate_resamples`, the post-run `parity` and `cache_basis`.
  `tests/fixtures/unit-economy/result.v1.json` is the committed example.
- **Parity after the run.** `summarise` exits 1 when the rows hold two values of the model, Claude
  Code version, commit, effort, schedule seed or design record. It does the same when a row's
  factor levels contradict its arm, or a cell loaded a surface that differs from `base`'s beyond
  its factors' entries. A grid writes no history row.

### Strata: several models in one run

```sh
python3 scripts/cost_bench.py replay --model <id-a>,<id-b> --tag <full commit> --pre-registration <plan> --dry-run
python3 scripts/cost_bench.py summarise --results benchmarks/<version>/<tag> [--json] [--pool]
```

- **Each model is its own stratum.** `--model` takes a comma list or repeats. The run goes through
  once per model, in the order named, and each stratum gets its own schedule, run and spend caps,
  preflight, arm builds, series and history row. Its rows carry `stratum` (the model id) and
  `strata`, and land in `<tag>/<model>/results.jsonl`. One model writes no stratum, as before.
  The micro tier pins its model and takes no strata.
- **A failed stratum stops the run.** A refusal, an error or a stop at its spend cap ends the run at
  that stratum with its exit status: the strata after it never start, the ones before it keep their
  results, and the run prints what it has spent so far. A dry run spends nothing, so it lists every
  stratum and exits with the worst status.
- **The dry run lists and prices each stratum:** its schedule, and its worst case if every run and
  preflight reaches its cap.
- **`summarise` reports every section per stratum**, given a results file or the tag folder that
  holds the strata. With `--json` the reports nest under `strata`. SM-2 refuses rows from two strata,
  so a verdict is always one model's.
- **Pooling is pre-registered or refused.** `--pool` adds a pooled report only when the plan every
  row names fills **Pooled analysis** under Run with something other than "none"
  ([template](pre-registration-template.md)); exploratory rows are refused. The pooled rows keep each
  task-and-model pair as its own cluster, so no task is paired across models.
- **An evidence bundle holds one stratum.** Its `design.strata` names every model of the run, and
  every row's `stratum` must be the design's model ([evidence bundles](evidence-bundles.md)).

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

- **Its protocol is five reps, 0.25 USD per run, 0.15 USD per preflight and a 7.80 USD stop.**
  Thirty scored runs and two preflights report 7.80 USD if every one reaches its cap; the per-run
  cap is soft, so the stop is the binding limit. The caps are sized from the harness arm's measured
  cold first turn, 0.0556 USD, so a truncated run does not bias a verdict against it; the arithmetic
  sits beside the constants in `scripts/replay_micro.py`. `--reps`, `--run-cap` and `--spend-cap`
  may change them; `--model` may not, and `--pair` is refused. The dry run prints the set's ceiling.
- **A preflight its own budget stops is reported as a budget stop,** naming the arm, the
  reported cost and the cap, and refuses the replay like any red preflight.
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

### Long-session tier

`replay --tier long-session` runs a pack's scripted multi-turn scenarios, where the harness's cost
case lives: context that grows over twenty to sixty turns, gathering that piles up, repeated test
runs. A fixed script of user turns stands in for the user, never a model, so runs are comparable.
The scenario format is in the pack's README; `scripts/replay_session.py` drives a session.

```sh
python3 scripts/cost_bench.py replay --tier long-session --pack <pack repo> --tag <full commit> \
    --exploratory --dry-run                                         # every planned session and the ceiling
python3 scripts/cost_bench.py replay --tier long-session --pack <pack repo> --pack-digest <digest> \
    --tag <full commit> --ablations <manifest> --spend-cap <usd> --pre-registration <plan>
python3 scripts/cost_bench.py summarise --results <results dir>
```

- **A set lists `scenarios`, not `tasks`, at tier `long-session`.** Loading validates each
  `scenario.json` (caps, turns, branches only on an earlier checkpoint, each declared checkpoint run
  by exactly one turn, its metrics) and refuses a check or solution without the canary, as for a
  task. Only the workspace is copied for an arm; the contamination control checks every
  checkpoint's check and solution. `--verify-tasks` does not read scenarios: the pack's
  `tools/verify_scenarios.py` proves them. The set's pinned model is the model, reps default to 3,
  and `--pair` and `--design` are refused.
- **One session per scenario, arm and rep, resumed turn by turn.** Each user turn is a fresh
  container of the arm on the same tree. Turn 1 starts the CLI session with `--session-id`, and
  every later turn continues it with `--resume`, its transcript kept in a host directory created
  for that session and mounted as the image user's CLI projects folder (`replay_arms.SESSION_STORE`).
  One cold-cache nonce (#1174) opens the session and is mounted unchanged on every turn, so later
  turns read the session's own cache as a real session does.
- **Branches, caps and the spend stop.** A `branch` turn sends its `pass` or `fail` prompt by that
  earlier checkpoint's verdict. At most `max_user_turns` turns are sent; each turn is launched with
  `--max-turns` set to `max_agent_turns_per_user_turn`, and a turn that reaches it ends while the
  session goes on. The session cap is the scenario's `max_cost_usd_hint`, or `--run-cap` when that
  is lower; each turn gets the rest of the cap as `--max-budget-usd`, and the session stops before
  a turn once its reported spend reaches the cap. A checkpoint never reached is not passed and its
  metrics are null. A turn that times out counts at the rest of the cap and errors the session.
- **Each checkpoint is scored on the tree and its segment.** The check runs as a pack task's does,
  in a fresh container with no network, with the stream of every turn since the previous checkpoint
  mounted read-only at `/session-stream.jsonl`.
- **The dry run prices the tier:** each scenario's turns, checkpoints and session cap, the ceiling
  if every session and preflight reaches its cap, then every planned session. Three scenarios,
  three arms and three reps on `claude-sonnet-5` at the 1.3.0 pack's caps is 378.75 USD.

**Rows.** A long-session set writes two kinds of row to `results.jsonl`, and no history row. Every
row carries the run's stamp, `tier: long-session`, `scenario` (also as `task`), `arm`, `rep`,
`session_id` (the CLI session's id) and `row_kind`. A row of the other tiers is identified by
`(task, arm, rep)`; a long-session row by `(scenario, arm, rep, row_kind, checkpoint_index)`, so a
reader of these rows, the Studio included, must key on `row_kind` before reading one as a run.

- `row_kind: checkpoint`, one per checkpoint in turn order: `checkpoint`, `checkpoint_index`
  (from 1), `reached`, `turn`, `passed`, `outcome`, the check's `metrics`, `metric_directions`,
  `metric_errors` and `metric_stream`, `segment_turns` (first and last turn of the segment), and
  over the segment `cost_usd`, `input_tokens`, `cache_creation_input_tokens`,
  `cache_read_input_tokens`, `output_tokens`, `main_peak_context_tokens` (the largest main-thread
  call's input, cache write plus cache read) and `cost_by_tier` (model class to USD, from each
  result's per-model cost), with `cumulative_cost_usd` at the checkpoint.
- `row_kind: session`, one per session, `checkpoint` and `checkpoint_index` null: the session's
  totals of the same cost, token, context and tier fields, `main_mean_context_tokens`,
  `user_turns_planned`, `user_turns_run`, `stopped` (`cap`, `max_user_turns`, `error` or null),
  `error`, `error_kind`, `session_cap_usd`, `agent_turn_cap_hits`, `checkpoints_passed` of
  `checkpoints_total`, and the curves as arrays, one entry per turn run: `cost_per_turn`,
  `cumulative_cost_usd`, `main_peak_context_per_turn`, `agent_turns_per_turn`, with
  `cost_per_turn_slope` (least squares on turn number) and the `branches` taken.

The per-turn cost is the turn's own `total_cost_usd`; that a resumed `-p` run reports its own spend,
not the session's to date, is how the pack's segment metrics read it too, and the first paid pilot
should confirm it.

**`summarise`** reads a long-session set and reports, per arm, the checkpoint pass rate, cost per
session, the cost-per-turn slope, the main thread's peak context and the share of cost on model
classes cheaper than the main model's, each with a percentile interval that resamples scenarios as
clusters; the pass rate per checkpoint and the mean cost-per-turn curve per scenario; and each
other arm against `bare` on the cost-per-session ratio and the pass-rate difference, paired by
scenario. With three scenarios the intervals are wide by construction: a session gives many
per-turn observations, but the clusters are the scenarios.

### Diff-quality judge

The oracles say whether a task passed and the detectors which rules fired. Neither can say which of
two runs kept to its scope, made the better design, or ended on the clearer and more correct
reply. `scripts/replay_judge.py` asks a pinned model those three questions, blind and pairwise, and
a dimension's answers count only once they agree with the maintainer's hand labels.

```sh
python3 scripts/replay_judge.py export --results <set>/results.jsonl --tasks <manifest> --out <dir>
python3 scripts/replay_judge.py run --pairs <dir>/pairs.json --image <built arm image> --out <dir>/verdicts.jsonl \
    --pre-registration <plan>
python3 scripts/replay_judge.py calibrate --verdicts <dir>/verdicts.jsonl --labels <labels.json> --out <dir>/calibration.json
python3 scripts/replay_judge.py report --verdicts <verdicts.jsonl> --key <dir>/pairs.key.json --calibration <calibration.json>
```

- **The judge is pinned.** `benchmarks/judge/judge.json` names the model, its effort, the rubric
  prompt in `benchmarks/judge/rubric.md` and that file's sha256; a rubric that differs from its
  digest is refused, so changing the question is a deliberate re-pin. The model is asked through
  the replay's own path: a fresh container of a built arm image, nothing mounted, no tool allowed,
  one turn, the credential passed by name and the egress proxy as its one way out. `run` needs
  `--pre-registration` or `--exploratory`, as a replay does; `--dry-run` prints the command line.
- **It is blind twice over.** `export` takes pairs of one task's runs, one per arm, matched by rep
  and with neither errored, and writes each pair's two runs in a seeded random order as `first`
  and `second`, with nothing naming the arm or the run. Which arm is which goes to
  `pairs.key.json`, which neither the labeller nor the judge reads. The judge then sees each pair
  twice, as response 1 and response 2, in a seeded random order and then swapped.
- **An answer that changes with the order is a tie.** Each dimension whose two answers disagree is
  counted as a tie and flagged inconsistent, and the share of such pairs is reported. An answer
  that cannot be read is an error and left out, never a tie.
- **What a run is judged on.** The final reply comes from the run's saved stream, so the set needs
  `--raw`. The diff is a `<task>-<arm>-<rep>.diff` beside the stream when one exists; otherwise it
  is the file edits the stream's Edit, MultiEdit and Write calls record, which miss any change a
  shell command made. Each side is clipped at the pinned `max_chars`, with the remainder counted.
- **Calibration.** `export` takes the pinned 40 pairs by default, spread round-robin over tasks
  (`--all` takes every pair, for a judged evaluation), and writes `label.html`, a local form over
  the same blind pairs whose **Save labels** button downloads `labels.json`. `calibrate` reports per
  dimension Cohen's kappa between judge and labeller, their raw agreement and confusion, the
  judge's position bias (how often a decided answer chose the response shown first, with a Wilson
  interval, flagged when it excludes one half), its length bias (how often it preferred the longer
  response, beside how often the labeller did on the same pairs) and the order-swap inconsistency.
- **A dimension is admitted only when kappa reaches the floor,** 0.6 unless the pre-registration
  sets another with `--kappa-floor`; an undefined kappa is never admitted.
- **The summary section.** `judge_section(verdicts, key, calibration)` returns `(section, text)`:
  per arm pair, the treatment's win rate over the reference on each admitted dimension, a tie
  counting one half, with a task-clustered percentile bootstrap interval, and the dimensions left
  out. `summarise` does not call it yet; the one line that adds it is
  `out["judge"], text = replay_judge.judge_section(verdicts, key, calibration)`.

### Layer scorecard

`scripts/layer_scorecard.py` builds one report from the result directories a definitive evaluation
leaves, each passed by its role. It reruns nothing and calls no model, and it writes
`scorecard.json` and `scorecard.md` when given `--out`.

```sh
python3 scripts/layer_scorecard.py --production <dir> [...] --rules <dir> --long-session <dir> \
  --sweep <dir> --judge <judge dir> [--plan <pre-registration>] [--allow-exploratory] --out <dir>
```

Each role is optional. A result directory is searched for every `results.jsonl` under it. A judge
directory holds the `verdicts.jsonl`, `pairs.key.json` and `calibration.json` that `replay_judge.py`
writes.

- **Registered rows only, unless you say otherwise.** A row, or a judge verdict, whose `evidence`
  is not `pre-registered` with a plan named is refused, and the error names each source. With
  `--allow-exploratory`, the scorecard scores them anyway. It sets `exploratory: true`, lists the
  reasons, opens the Markdown with **Exploratory: not evidence**, and labels every equivalence
  verdict exploratory. A set passed twice is refused, because its rows would count twice.
- **The headline is harness against bare, per stratum.** A row's `stratum` decides its stratum.
  Rows from before strata existed are grouped by `model`, and the report says so. For each stratum
  it gives:
  - the Cost-of-Pass ratio and pass-rate difference, from `replay_stats.analyse`
  - pass^k and the all-rules-at-once rate from `replay_reliability`, each as the mean per-task
    difference with a task-clustered interval
  - the judge's win rate on each admitted dimension, read as its excess over one half
  - the mean long-session cost, harness over bare, with scenarios as the clusters

  Each reading carries an `equivalence` verdict. The margins are the pre-registration template's
  defaults: 0.85 to 1.1765 for the ratio, and ±0.125 for each difference and for the win rate's
  excess over one half. A `--plan`'s Equivalence margins field overrides any metric it names, under
  the names `pass^k difference`, `All-rules rate difference` and `Judge win rate over one half`.
- **One card per layer.** The layers are every sweep arm, every `unbuilt` and `excluded` entry, and
  each output style in `benchmarks/static.json`. Each card holds:
  - **Prefix tokens and USD per run, by model.** The tokens are the median over harness rows of the
    layer's entries in `context_attribution`, or the static figure where no row records them. A hook
    holds none. The USD is the static figure's split, one cache write and a cache read on every
    later turn, at `policy/prices.json` rates and the rows' mean turns.
  - **Behaviour against bare and against the layer's own removal.** Both use the score reader
    `justify` uses, on the layer's own tasks, and both read as the removed arm minus the harness.
    Bare is the whole harness removed.
  - **The outcome effect.** The removal's pass-rate difference on the outcome subset.
  - **The cost effect, for a cost-control layer.** A cost-control layer is one scored on the
    Cost-of-Pass ratio or run on long-session scenarios. The card gives the sweep's marginal cost
    and the removal's mean session cost over the harness's on the arm's scenarios.
  - **The verdict.** `justify`'s keep, trim or no evidence, run per stratum. It names the deciding
    score, with its margin and interval, every score it rested on, and the sources, arms, tasks and
    row count it came from. A layer with no arm reads no evidence, with the manifest's reason.
- **What is absent is said, never zeroed.** A role not supplied, a long-session set whose rows
  carry no `row_kind`, a production set with no `detections.jsonl` beside it, and a stratum with no
  judge result are each reported as `not measured`, with the reason. A stratum whose rows SM-2
  refuses reports that refusal.
- **Deterministic.** The same rows, manifest, static figure, prices and seed give byte-identical
  files. Sources are named by role, position and path inside their directory, never by an absolute
  path. An evidence bundle may carry the scorecard (docs/evidence-bundles.md).

## Limits

- Claude Code only. Codex instructions are rendered at sync time and are not counted.
- Your own instruction files, memory, MCP servers and hooks are not counted here; `citizen usage
  --surface` lists them locally beside the harness's modules (see [usage](usage.md#the-loaded-instruction-surface)),
  and they never enter an arm.
- Full agent and skill bodies load only when used, so only their descriptions are counted.

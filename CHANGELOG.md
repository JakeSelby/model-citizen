# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.14.2] — 2026-09-28

### Added

- Added a privacy policy for the Claude Directory plugin and linked it from the manifest and README. (#1052)

### Changed

- Documented the external services that Claude Directory workflows may contact when users request them. (#1054)

### Fixed

- Package the Claude Directory distribution from a minimal regular-file bundle so repository history,
  planning artifacts, installer code and symlinks no longer trigger unrelated directory review holds. (#1056)

## [0.14.1] — 2026-09-28

### Changed

- Documentation-only patch releases can now carry unchanged native evidence from an immutable prior
  release. The validator pins immutable tag commits, preserves evidence provenance across chained
  patches and fails closed on unknown or changed claims, evidence, limitations, ancestry or any
  ineligible release path, with a one-release v0.14.1 exception for the reviewed validator and
  release-reporting files. (#1036)

- Living documentation now uses Model Citizen, canonical repository links and `citizen` commands
  while preserving compatibility identifiers and dated history. (#1040)

### Fixed

- Recognize model-written framework briefs that refer back to a declared prompt file with a qualified instruction phrase, so the spawn stays confined to its routed review role. (#739)

- Made Model Citizen discoverable by registries that require standard YAML and readable plugin component files. (#1037)

## [0.14.0] — 2026-09-27

### Added

- The static context figure now prices every always-loaded rule, selected stance variant and listed
  description on its own, in a `files` map in `benchmarks/static.json` that sums to the totals within
  rounding. `scripts/cost_bench.py static --check` prints the token and dollar delta of every file
  that moved since the committed figure, so a pull request that grows one rule names that rule. The
  5% growth gate and `benchmarks/allow.json` still apply to the total. (#509)

- Added a write-intent ledger for parallel writers. `harness intent claim` records the paths and
  globs a session will write, planned new files included, and `harness intent release` drops them;
  a claim whose process has died is ignored and swept. On Claude Code, an Edit, Write, MultiEdit or
  NotebookEdit to a path a live sibling session in the same repository has claimed is warned the
  first time and denied the second; `coordination.repeat_overlap: "warn"` in the config never denies.
  Codex raises no edit event, so its builders run `harness intent check` before committing. Every
  warning and denial is an `intent-overlap` row in the decision log, `harness intent merge` records
  whether a landing branch merged cleanly, and `harness usage --conflicts` reports both per week. The
  check is the `intent-overlap` hook id, so `citizen config set hooks.intent-overlap off`
  switches it off. (#542)

- Added one selection document and one resolver for it. Stances, rules, hooks, skills, workflows and
  roles are all kinds in one JSON shape, read from a mode file, the user configuration, the project
  file `HARNESS_PROJECT_CONFIG` names, and a new session file named by `HARNESS_SESSION_CONFIG`, in
  that precedence below `HARNESS_MODE` and `HARNESS_STANCE_*`. `harness selection` lists every unit
  of every kind with its value and the layer that set it, and its JSON reads back unchanged as a
  session file. A project or session file may now select any kind, and one carrying identity,
  permissions or another non-selection key is refused with the key named. Switch kinds are resolved
  and reported here; sync does not yet act on an `off` rule, skill, workflow, role or hook. (#554)

- Added hook ids and the `hooks` switch kind. Every policy module that answers a lifecycle event has
  an id, its basename under `policy/hooks/`, and `harness config set hooks.<id> off` stops the
  dispatcher loading it from the next event on both Claude Code and Codex, with no sync. Inline
  logic follows its owner: Bash grading and its ask to `grade-bash`, read-only and plan-mode allows
  to `allow-readonly-bash`, band routing and the integration notice to `tier-agent-spawns`. Role
  confinement, by name, marker, framework mapping or evasion, and the Workflow launch guard have no
  id and stay on with every hook off. The four core ids,
  `grade-bash`, `stop-gate`, `brief-guard` and `neutralize-tool-output`, may be `off` only while the
  user configuration sets `core_switches_acknowledged` true; `config set`, `sync` and `harness
  selection` refuse otherwise before writing anything, and a hook meeting an unacknowledged `off`
  keeps running. `harness catalog` lists the ids with kind `hooks`, and each declares its manifest
  in `policy/hooks/manifests.json`. (#555)

- Added switches for rules, skills, workflows and roles that sync acts on. A unit set `off`, for
  example with `harness config set rules.decisions-and-plans off`, is absent from both runtimes: no
  rule link or Codex `AGENTS.md` section, no skill link in either skills home, no command or
  generated Codex skill, no agent definition in either home, and the spawn hook does not route to
  an `off` band worker. A projection an earlier sync made is retired, and `harness diff` reports a
  switch changed since the last sync. The repository's rules are now linked one file at a time into
  a real `~/.claude/rules/harness/` directory; the directory link earlier releases made there is
  migrated on the next sync and journaled, and uninstall removes the directory. `harness selection`
  prints the always-loaded line count of the selection in force; the lint cap is unchanged. (#556)

- Added modes: named selection bundles in `primitives/modes/<name>.json`, selected with
  `harness config set mode <name>` or `HARNESS_MODE` for one session. Two ship: `full`, the
  defaults, and `minimal`, which keeps every hook and the cost, delegation and autonomy stances,
  turns the other stances off or light and switches every workflow off. A mode sits below every key
  you typed and above the stances `harness init` wrote as defaults, which `config.json` now records
  under `init_defaults`; `harness selection` names each mode key a higher layer shadowed. An unknown
  mode, a mode file naming an uninstalled unit or a non-selection key, a mode name two roots both
  define, and a mode switching a core hook off without `core_switches_acknowledged` are refused
  before sync writes anything. See `docs/modes.md`. (#557)

- Qualification evidence is now invalidated per acceptance case inside each target. The catalog's
  versioned `case_paths` map names the source paths each required case depends on, so a change
  under a mapped path makes only the cases that name it stale, and a rerun of those cases alone
  completes the claim. A change under a path no case maps still invalidates the whole record. Each
  new evidence record states the map version and digest it assumed, and a record without them keeps
  the whole-target rule. The first map assigns both runtimes' `worker.py` to the four cases that run
  a role worker. (#582)

- Added `--codex-session-login` to `scripts/native_acceptance.py`. With it, each case's disposable
  `CODEX_HOME` gets an `auth.json` symlink to the operator's Codex session login, read from
  `CODEX_HOME` or `~/.codex`, the way the Codex worker adapter links it, so a Codex target can be
  driven by the runner instead of by hand. The flag is off by default, refused for a Claude Code
  target and when no login file exists, and every string in the login file is redacted from each
  observation, the progress log and the record. Codex verdicts stay `unverified` until an operator
  compares a runner-driven case against a hand run and passes `--home-confirmed`. (#612)

- Every row the usage ledger and the decision log write now names its `schema_version`, and every
  reader carries fields and versions it does not know instead of refusing them, so a ledger written
  by a newer release reads without error in an older one. A renamed field ships a fold map in the
  writing module and is read under its new name, and no existing row is rewritten to get there.
  The rule is documented under "Ledger schema" in docs/usage.md. (#644)

- Added a manifest for every switchable module. Each shipped rule, skill, role and workflow declares
  its claims, surface, instruments, slot, dependencies and conflicts in `primitives/manifests.json`,
  and hooks without switch ids remain outside `policy/hooks/manifests.json`; hooks with switch ids
  already declare theirs there. The
  selection resolver refuses a shipped module with no manifest or a missing field, a switched-on
  module whose dependency is off, two switched-on modules that conflict, and two that claim one slot
  unless one cedes it, naming each module involved. `harness selection` shows each switch unit's
  instruments, or `unmeasured`. A module from your own primitive root may omit its manifest and keeps
  resolving. (#788)

- Every new row in the usage ledger and the decision log now names the `profile_fingerprint` of the
  profile that wrote it: a digest of each switched-on module's content, the stance variants, the
  configuration that reaches the model or a hook, and the harness version. A row whose profile
  could not be resolved carries `null`. Role workers and replay rows carry it too, with `bare` for
  the replay arm that loads no harness. The field ships under schema version 1, which this release
  is the first to carry. A row written before this reads as unattributed and is never given a
  guessed fingerprint, and `harness usage --by profile` tells two profiles apart by their rows alone. (#789)

- A session row in the usage ledger now carries `context_attribution`: the tokens each switched-on
  rule, skill, role and workflow and each stance put into context before the first prompt, labelled
  a soft estimate and naming its method, four characters a token over the module's resident text.
  A skill, role or workflow counts its listing entry, never the body it loads on demand. Replay rows
  carry it too, with no module for the bare arm, and a rescan never attributes a past session to
  today's selection. Every decision-log row now names the `module` whose hook made the decision, as
  `hooks/<id>`, or `null` for a point no hook owns. (#790)

- An observation-only hook entry point, `adapters/<runtime>/observe.py`, sits beside `hook.py` and
  outside its dispatcher. It appends one identifier-only row per hook event to
  `~/.local/state/agent-harness/observation.jsonl`, never a message body, and fails open and silent:
  exit 0, no output, errors to a local error log. Its registration reads the same event table as
  `hook.py`, and `harness_core.observation.bare_install` writes a bare arm's whole footprint, the
  entry point and its registration. A zero-footprint test drives a scripted session against a stub
  model through every installed hook event and proves each model request byte-identical with
  observation on and off, in the bare arm and the harness arm. `harness sync` does not register the
  entry point yet. (#791)

- Adherence events. When the usage feed says the fresh-session line, it now appends an `emitted` row
  to `~/.local/state/agent-harness/adherence.jsonl` naming the recommendation, the emitting module,
  the session and the turn. `policy/hooks/adherence.py` answers each emission from the observation
  ledger as `followed`, `not_followed` or `unknown`, and computes a rate per recommendation from
  identifiers alone. Recording never changes what the feed says. The observation entry point is not
  registered in live sessions yet, so each session start answers a live emission `unknown` once
  it is a day old. (#792)

- Added an evidence standard for published results, `docs/evidence-standard.md`, which lists the
  twelve things every proof set must carry and what satisfies each, quoting SM-2 where it defines
  the terms. Added `docs/pre-registration-template.md`, the plan a proof run fills in and commits
  before its first trial, so the order of plan and trial can be checked from the history. (#794)

- Added a `concise` voice variant. It picks each reply's shape by what the reply is for (a one-line
  done message, a short answer, a report, a decision, a brief or a deep dive) and holds every reply
  to seven rules. On Claude Code it also selects the built-in Concise output style, verified present
  in Claude Code 2.1.280, with earlier versions unverified; Codex receives the same stance text
  through AGENTS.md. The default voice is unchanged. The What-changed reply template no longer
  appears in the always-loaded rules, and two new detectors count template labels and heading-first
  replies under this voice. The always-loaded caps rise to 225 lines and by the stance's 620 tokens
  to pay for it. (#811)

- Branches that each reserve a BMad ID now merge without an issue-map conflict. A git merge driver
  keeps main's map, adds the branch's entries and the larger `next_ids`, and lets main's entry win
  when both sides mapped one issue; a second driver makes the merge commit carry a freshly rendered
  sprint status. `citizen worktree create` registers both where the repository ships them, and a
  genuine duplicate still conflicts and fails the audit. (#819)

- The `local` decision provider now reads a user-level `governance.json` beside `config.json` under
  the repository's `.agent-harness/governance.json`: the repository file wins for defaults and pair
  entries, and caps combine by the lower value. A `repo:<name>` pair applies to every branch of that
  repository, after an exact `repo:<name>/<branch>` pair and before the class default, and
  `coding.pr_merge` is a new action class. Every answer names the file that supplied its level, and
  `harness decide` and `harness doctor` list the policy files read. (#874)

- Added the `citizen` command; `harness` remains a supported alias. (#877)

- Bound the decision provider into Bash command grading. When `governance.provider` is not `none`,
  `grade-bash` classifies each simple command as `coding.git_push`, `coding.git_commit`,
  `coding.pr_merge`, `coding.deploy` or `coding.shell_exec`, derives its `repo:<name>/<branch>`
  counterparty from the directory it runs in (`git -C <dir>` and an earlier `cd <dir>` included, and
  the repository's name rather than a worktree directory's), and asks the provider about every
  command the autonomy stance lets through. The provider only tightens: its `ask` is an ask in a
  prompting mode and a deny with an approval code in auto mode, and a configured provider that
  raises asks, naming the error, instead of allowing. An agent write to a governance policy file or to the
  user `config.json`, by Bash or a file tool, and any `harness config set governance...`, is always
  asked about, and each governed decision is one `governance` row in
  the decision log. Under provider `none` the hook's output is unchanged. (#884)

- Added the workspace map: set `workspaces_dir` to the folder holding your `.code-workspace` files
  and they become the only definition of which folders belong together, worked out on every call
  with an optional `overrides.json` for shared folders. `citizen workspace list` shows each
  workspace's members and instruction size and how every shared folder resolves, and
  `citizen workspace open NAME` launches Claude Code, or Codex with `--codex`, in the first folder
  with the others as `--add-dir`. See `docs/workspaces.md`. (#935)

- A session opened in any member folder of a workspace now learns, at start-up, the workspace's
  other folders and their instructions: a new `workspace-session` hook, registered as its own
  SessionStart entry on Claude Code and Codex so it has its own output cap, supplies each member's
  `CLAUDE.md` or `AGENTS.md`, local file and unscoped rules, skipping on Claude Code the members the
  running session already loads through `--add-dir`. Past 9,000 characters the instructions go to a
  file under `~/.local/state/agent-harness/workspaces/` that the session is told to read first, and
  in the desktop app Claude is told to request a member folder on first use. While `workspaces_dir`
  is set, `sync` also owns `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1`, by the native telemetry
  rules. See `docs/workspaces.md`. (#936)

### Changed

- A project or session stance selection that differs from the synced one now reaches the model as
  that variant's text at session start, counted against the always-loaded token budget `citizen
  lint` enforces, less what the sync already loads; a variant that does not fit is named with a
  pointer to its file. A session whose selection matches the synced one gets nothing injected, and
  the `custom-stance` qualification case now observes a session selection natively beside the
  project one. (#276)

- `scripts/cost_bench.py replay` runs its two arms as fresh Docker containers instead of profile
  directories, so nothing from your machine reaches either one: the bare arm is the Linux
  qualification image's pinned base plus Claude Code, with the base's own Codex client removed, and
  the harness arm adds this repository at the full commit a `--tag` names. The installed harness is no
  longer an arm, and `--bare-config`, `--harness-config`, `--harness-repo` and `--claude` are gone.
  Each build writes a declaration of its inputs and a hashed manifest of everything the image holds;
  `cost_bench.py arms check` builds each arm twice with no cache and fails if the manifests differ. A
  run mounts only the task snapshot, passes `CLAUDE_CODE_OAUTH_TOKEN` by name, reaches only the model
  API through an allowlist proxy (`arms probe-egress` proves it), and scores the agent's work in a
  fresh container. `--verify-tasks` runs each task's gate and checks in that container too, where they
  no longer read your live configuration through HOME and go red for it. Every row records the image,
  its manifest and declaration digests, and the harness ref and commit. This closes the personal-layer
  leak in #428. (#559)

- CI now holds the Python 3.9 floor on every pull request and merge queue run. The required `test`
  check parses every tracked Python source under a real Python 3.9, so newer syntax fails it, and a new
  `test-py39` job runs the whole suite under Python 3.9 to catch newer standard-library use. That job
  is not yet a required check. (#640)

- The "on the way" list in `product.json` and the README now names only planned work the roadmap
  backs, each entry tied to an open issue on a milestone: proof set 1 against bare Claude Code (#799)
  and the superpowers mode (#558). The README carries no em dash, including the compatibility note
  generated into it, and a test keeps its "on the way" list equal to `product.json`. (#648)

- A red smoke tier now stops the qualification round: `scripts/qualification_round.py` launches no
  target when the tier fails or times out, records the tier's result, why the round stopped and the
  targets it did not run in `round.json`, and exits non-zero. This enforces FR-52, which says a round
  passes the model-free tier before any native case runs; `--skip-smoke` still records the tier as
  `skipped` and runs every target. (#688)

- Five native case drivers now observe what their qualification procedure step asks for.
  `cost-posture` checks that every role the cost variant leaves unchanged is still the same link
  rather than an identical copy, and that the band worker's brief ends with its row's budget
  sentence. `spawn-confinement` judges its routed review run a second time with the run's worker
  state moved aside and requires that judgement to fail. `hook-composition` requires the harness's
  own `PostToolUse` notice in the write turn's transcript beside the user's hook. `migration-uninstall`
  reads the hand edit as drift from `harness diff` before uninstalling. The procedure now states the
  narrower claims no run shows: on Claude Code a multi-file patch is one turn's file-tool writes,
  and the auto posture is claimed as a mode, not as a restriction. (#742)

- `/build` gains a step that answers a review bot's threads before a pull request goes up for
  approval: it fixes or replies to each one and resolves it, asks the bot for one more review after a
  fix, and leaves a human's thread alone. `/land` stops on an unresolved review thread and names it,
  instead of learning about it when a branch rule refuses the merge. This repository now runs
  CodeRabbit on its own pull requests, from a `.coderabbit.yaml` tuned to it. (#785)

- The repository's `## Gate` block now runs `scripts/bmad_issue_sync.py sprint-status --check` and
  `scripts/bmad_issue_sync.py audit` before the unit tests, as CI's `test` job does, so a story
  whose filled sections change its derived status fails the local gate and the `stop-gate` hook
  instead of only the pull request's `test` check. (#820)

- The `plan-authoring` skill now has you run the repository's named lint on a plan before posting it
  and after each revision, since `/build` commits the approved plan into its pull request and a plan
  reworded at build time is no longer the one that was approved. For the harness lint, copy the plan
  alone into an empty directory and run the checkout's `bin/citizen lint` on that directory. (#821)

- `/build` step 6 now says what a finished bot review looks like: the bot's commit status on the
  head commit completing, such as CodeRabbit's `success: Review completed`. The empty review a bot's
  thread reply creates no longer reads as a pass, a `Review skipped` status or a review that never
  starts now prompts a request with the bot's review command, and the wait for a pass rises from ten
  minutes to fifteen, since a requested re-review can take close to nine. (#822)

- `bmad-code-review` in this repository now runs straight through when an approved plan invokes it,
  or when it is invoked with `unattended`: it proceeds past the context summary, applies every patch,
  records every finding in the story and ends with only the decision-needed findings as numbered
  questions. Invoked any other way it still halts at each checkpoint, and blocking halts stop it in
  both modes. (#823)

- `/land` now gates only on required checks, and on an issue-ownership or closing-link check where
  the repository runs one. It names every other check or commit status with its state but never waits
  on it, so a review bot's status that sits pending for minutes no longer reads as a reason to stop. (#824)

- The plugin is now `model-citizen@model-citizen`. An existing `agent-harness@agent-harness` install
  keeps its ID and fails to load once Claude Code updates its copy of the marketplace, so move it:
  with a checkout installed, run `citizen upgrade` (or `citizen upgrade --dry-run` to see the
  commands first). It uninstalls `agent-harness@agent-harness`, removes the `agent-harness`
  marketplace, adds the marketplace again from its recorded source and installs
  `model-citizen@model-citizen`, in that order, through the `claude plugin` commands. A plugin-only
  install runs the same four steps with `/plugin` in a session; adding the marketplace again before
  removing the old one does nothing. Skills move from `/agent-harness:<name>` to
  `/model-citizen:<name>`. `citizen doctor` recognizes either ID, and it and `citizen sync` name
  `citizen upgrade` while the old ID is enabled. (#878)

- Renamed the product to Model Citizen. (#879)

- Links now point at the renamed repository, `JakeSelby/model-citizen`, and the new site,
  https://model-citizen.dev: the installer, the release notes, the issue sync tool's `bootstrap`
  default, the issue template, README, CONTRIBUTING, the install and release docs, and the plugin
  manifest's `homepage` and `repository`. The landing copy names the site as the repository homepage
  and trades the `coding-agent` topic for `model-citizen`. A new `retired-name` rule in
  `citizen lint` fails a line in this repository that names the old brand, repository slug or site
  host, outside the dated areas that keep them on purpose and the "Formerly" lines. Existing links
  keep working, since GitHub and the old host redirect. (#880)

- The always-loaded set is ten lines and about 160 tokens lighter, down to 213 of its 225-line cap and
  about 4,204 of 4,822 tokens, so the next rule or stance line fits without raising the cap. The cuts
  remove only restatement and detail a pointed-to skill or document already carries: the concise
  voice's closing summary of every shape's opener and one rationale clause, the decision block's shape
  (in `plan-authoring`), the credentials bullet folded into one line that keeps its sources and bans,
  the `brief-guard` hook's behaviour, and the global instructions' note on why the cap exists (in
  `harness-authoring`). (#933)

- Every test of cost, efficacy or a change to the system now follows an eleven-point experiment
  protocol in `docs/evidence-standard.md`: pre-registered, run in fresh containers holding exactly the
  named components, and never from a host profile, a live checkout or a disposable home. Section 3 no
  longer allows a run without a container. `scripts/cost_bench.py replay` refuses to start without
  `--pre-registration <path>`, a committed, dated plan with its question, hypothesis, primary metric,
  decision rule and sample size filled; `--exploratory` runs without one, labels every row
  exploratory and writes no history row. Before any launch the replay also refuses an arm whose
  manifest differs from its declaration, which carries settings, hooks or other configuration no
  declared component supplies, or which names your home directory, your profile or this checkout in
  its recorded inputs, a mount or a variable. An errored, crashed or timed-out run now counts as a
  failed attempt with its cost in the summary and verdict, and stays countable apart by its `error`
  field. (#1010)

### Fixed

- The `cost=max` stance allowed compaction while the always-loaded `cache-hygiene.md` rule still said
  never to compact and the `cache-hygiene/compact` detector counted every compaction as a miss. The
  rule now names the exception, the detector no longer counts a compaction under a `cost` variant
  whose `compaction` switch is `compact-allowed`, and `docs/preferences.md` documents that the switch
  decides and that a session override lifts the rule for that session only. (#117)

- The preferences guide no longer says a sync links a `HARNESS_STANCE_*` value. Such a variable is a
  session selection: `citizen stances` and the hooks resolve it and the session-start hook injects its
  text, while a sync keeps linking your user-level variant. `citizen diff` now says, when a project or
  session selection changes a resolved stance, that it compares against your user-level selection
  and does not count that selection as drift. (#294)

- A Claude Code `Workflow` script could run a constrained role such as `reviewer` in session,
  unconfined, because a script's `agent()` calls never reach the spawn hooks. The pre-tool hook now
  reads the launch: it refuses a script that names a constrained role in `agentType` or carries a
  `harness-role:` marker for one, with the same `harness role run` instruction a native spawn gets,
  refuses a script file too long to read in full and every launch under `delegation: off`, and
  writes a `workflow-launch` row to the decision log. A script's other agents are still not routed
  to a band. (#576)

- `harness usage --by role` now reports Workflow-tool agents in their own `(workflow)` bucket
  instead of under the role their agent definition is named for, so they no longer move that
  role's percentiles. The usage ledger marks such a row `unconfined: true` and records its soft
  budget as `null`, because the tool launched it without a spawn hook or a budgeted brief. (#577)

- Qualification provisioning now excludes its `.harness-round-clone` marker in the frozen clone's
  own `.git/info/exclude` and refuses to finish if the clone is still dirty, so a freshly
  provisioned round runs without hand edits instead of failing every case on the runner's
  clean-checkout check. (#677)

- Under the `delegation: off` stance, the `tier-agent-spawns` hook asked for confirmation of every
  spawn while the stance said a spawn is denied outright. The lifecycle already denies the spawn
  before this hook runs, which is why the engine was seen denying it; the hook now denies too, with
  the lifecycle's reason, and its header comment says so, so a direct run of the hook agrees with the
  stance. (#687)

- A resumed qualification round ran every case again. `scripts/native_acceptance.py` now skips each
  case its durable log already holds a `passed` or `failed` verdict for at the same source commit,
  client version and routing, and says so on stderr. A failure is kept rather than rerun so its
  evidence survives the resume; an `unverified` case runs again and its new result supersedes the
  old one. (#689)

- A framework review layer's spawn whose brief the model wrote in its own words is now refused when
  it tells the subagent to follow or apply one of the layer's declared prompt files, in any wording.
  A brief that edits, updates or rewrites that file, or reads it for another reason, still runs.
  Step 9 of the qualification procedure again requires that refusal, and the `spawn-confinement`
  case fails when the model's own brief runs, so the catalog's lexical-recognition limitation is
  dropped. (#739)

- `harness role run` launched from inside a Claude Code session logged in with
  `CLAUDE_CODE_OAUTH_TOKEN` alone started a Claude worker that failed with "Not logged in", because
  the client strips the token from its tool subprocesses. Inside a session the Claude adapter now
  runs `claude auth status` under the worker's environment first and refuses before launch, naming
  the missing token and the two fixes, when no login is confirmed. The token is never written
  anywhere. (#759)

- A native spawn refused because it names a constrained role, by `subagent_type` or by a
  `harness-role:` line in its brief, now writes a `role-confinement` deny row to the decision log
  naming the role and what named it, as framework-spawn and evasion refusals already did. The usage
  ledger no longer counts a refused `Agent` call, one whose result is an error and which left no
  subagent transcript, in a session's `subagents` figure; a session file in the older format, with
  subagent turns written in as sidechain lines, still counts every call. (#760)

- The refusal a natively spawned constrained role gets now names the CLI by the absolute path of the
  checkout's `bin/harness` instead of a bare `harness`, which no documented install puts on `PATH`.
  A test runs the command the refusal names with no `harness` on `PATH`. (#761)

- Qualification provisioning with `--bmad` now installs BMad with the exact command `docs/bmad.md`
  gives the optional integration suite, `--shims` included, so `harness integration apply bmad` on
  a freshly provisioned framework root exits 0 instead of reporting legacy review-name drift. (#762)

- `harness role run` accepted a `--read-dir` as broad as `/tmp`, so a review worker could read every
  other run's files, including an earlier report naming a blind review's planted defects. It now
  refuses `/`, the home directory, each system temporary root and any directory above one of them,
  naming the directory and a dedicated `mktemp -d` subdirectory to grant instead. (#772)

- A release pull request that assembles the changelog fragments into a new version section now passes the lint's fragment rule without adding a waiver, so no waiver outlives the release. (#773)

- The live replay's two arms are comparable again. Each scored run passes its task's `max_turns` to
  the CLI, denies `WebFetch` and `WebSearch` to both arms whatever their profile allows, trusts its
  snapshot for the stop-gate hook for that run only, and is captured as `stream-json` with hook
  events, so every row records how often the Stop hook ran and blocked. `usage-prices` leaves the
  task set for the manifest's new `retired` list, since no arm could pass it honestly, and a test
  now fails when the benchmark docs state a task count the manifest does not hold. (#793)

- The Scannable output style, the default voice, now declares `keep-coding-instructions: true`, so
  selecting it keeps Claude Code's default coding instructions in the system prompt instead of
  replacing them. A test now requires the key on every output style the harness ships. (#810)

- `/close-out` now asks once, before it lands anything, for a go-ahead that names its follow-ups,
  the merges and any pull request the filing needs. When filing writes tracked files, such as an
  issue map or story files, it runs from a new worktree off the updated default branch and puts what
  it wrote in its own pull request, which the close-out lands itself, and it no longer archives until
  that pull request has merged. Before, nothing landed the filing's changes, so they were left unmerged
  when the session archived. (#816)

- Files an agent sends with `SendUserFile` in a Remote Control session now open in the app. Claude
  Code serves a sent file only from under the session's working directory, so a new
  `stage-user-files` policy copies a file from anywhere else — a temp or scratch directory, a task
  worktree, the main checkout seen from a worktree — into `.agent-harness/outbox/` there, which git
  ignores, and sends the copy. (#817)

- Changing a stance variant no longer leaves the old variant in place when its link was made through
  `claude/stances`, the alias of `primitives/stances`. Sync used to report the harness's own link as
  a user redirect, exit 2, and refuse to relink even with `--adopt`; a link that resolves to the
  recorded file is now relinked, and a link to a file outside the stance sources is still preserved. (#828)

- Files an agent sends from a session a Remote Control host started now open in the app. Claude Code
  gives such a session no upload route, so every file arrived marked "not delivered" and the iOS
  app greyed its card out. Every host `harness remote-control install` writes now sets
  `CLAUDE_CODE_BRIEF_UPLOAD=1`, an undocumented Claude Code variable its sessions inherit, so the
  file is uploaded with the signed-in account; a folder's `env` can set it to an empty string to turn
  it off. Re-run `harness remote-control install` to apply it to running hosts. (#858)

- In `auto` mode the classifier refused the `HARNESS_CONFIRMED=1` marker as a bypass of a safety
  hook, so a command `grade-bash` gated could never run, whatever the user said. The auto-mode deny
  now names an approval code; the user replies `approve <code>` as the whole message, a new
  `approvals` UserPromptSubmit hook records it (a prompt with any other text records nothing, so a
  notification turn carrying agent-controlled text cannot approve anything), and the same command, with no marker, passes once in that session within thirty
  minutes. A Bash write to the approvals store grades 3 and a file-tool write to it is denied. The
  marker still confirms in `bypassPermissions`, and prompting modes still ask. (#859)

- The read-only Bash grammar read an operator written directly after a closing parenthesis as part
  of the preceding token, so `(true);ls` was split as one command. Separators directly after a
  closing parenthesis or brace, a separator directly before an opening parenthesis, and a redirect
  directly after a closing parenthesis are now split as they are with spaces around them, in both
  `allow-readonly-bash` and `grade-bash`. (#896)

- With a decision provider configured, `grade-bash` no longer treats issue and pull request text
  that mentions a policy path as a policy-file write: a quoted `--body` or `--title` value, or a
  quoted here-document fed to `--body-file -`, on a single `gh issue` or `gh pr` create, comment,
  edit or review command with nothing else on the line. Every other line that names a policy path
  is still a level-1 write, because almost any command may run code that writes a path it only
  names, and redirects, `tee`, `sed -i`, `cp` and `mv` targets are still judged as before. (#898)

- The `grade-bash` hook graded a segment made only of a redirect, as after a subshell in
  `(ls) > out.txt` or `(ls)>out.txt`, at 0, so the `ask` and `confirm-writes` stances let the file
  write through without a prompt. A redirect that writes a file now grades at least 1 whatever the
  segment around it, and a `/dev/null` target still grades 0. (#899)

- `grade-bash` told the user that `gh repo rename` locks the repository read-only for everyone,
  because the rename shared the archive branch and so printed the archive's reason. The rename
  still grades 3, but its reason now says it moves the repository to a new name and that the old
  URLs redirect only while no repository takes the old name. (#900)

- Governance attributed a piped command after a literal `cd` to `repo:unknown/local`, so
  `cd <repo> && git push origin HEAD 2>&1 | tail -1` was refused where the same push without the
  pipe was allowed. The directory walk in `grade-bash` now follows Bash precedence: every element of
  a pipeline starts in the directory in effect when the pipeline begins, and only a `cd` inside a
  pipeline element, a subshell or a background job is confined to it. (#903)

- `scripts/bmad_issue_sync.py` sent every GitHub API call to the issue map's stored slug, which still
  names the repository's old name. GitHub answers a write to an old slug with a 307 that `gh api` does
  not follow, so after the rename `new` failed before filing and `apply` could not update issues. API
  calls now go to the repository's current slug, and the planning-block links keep the slug the map
  stores. (#904)

- Two edits at once from one session and worktree could both read an intent-overlap counter of zero
  and both be warned, so the repeat overlap was never denied. The counter's read-modify-write now
  holds an exclusive lock on the hit-counter directory, so the second concurrent edit is denied. (#930)

- The governance check on `harness config set governance` no longer gates issue and pull request
  text that only mentions the command. It takes the same one exemption as the policy-file guard,
  judged by the same lexer: quoted text passed to gh's built-in issue and pull request create,
  comment, edit and review subcommands. A real invocation stays a level-1 action wherever it sits,
  including after a separator or a pipe, in a subshell, or under `eval`, a shell or an interpreter. (#931)

- The worker context-budget test that proves a renamed skill directory refuses the spawn no longer
  rewrites `primitives/roles/design-judge.md` in the checkout while it runs. It breaks a copy of the
  role tree in a temporary root instead, so a sync, a live spawn or a second test run reading the
  checkout at the same moment never sees a role that names an unknown skill. (#932)

- Adherence emissions are now answered in a live session. Nothing outside the tests called
  `adherence.settle`, so the ledger held `emitted` rows that never got a `response`. Each session
  start now settles every emission that is due, writing one response row per emission, holding no
  message text and adding nothing to what the session is told. (#1013)

- `citizen config unset workspaces_dir` switches workspace support off from the CLI. Before it,
  the key could be removed only by editing the user configuration by hand, since `config set` refuses
  an empty value. The new `config unset` removes only keys whose absence is a documented state, so
  it cannot bypass the checks `config set` runs on switches, acknowledgements and governance keys. (#1014)

- Recognize a framework layer prompt through an explanatory em-dash clause, so a later directive to
  follow those instructions cannot run the layer as an unconfined native subagent. (#1031)

## [0.13.1] — 2026-09-24

### Changed

- The landing copy now leads with the harness as the control plane under your coding agents, rather
  than with the rule-detector loop, and the spend capability is retitled so it no longer claims
  savings the cost benchmark does not show. (#764)

### Fixed

- The grade-bash timing test no longer fails under the Python 3.9 floor on a loaded machine. It
  takes the best of five runs rather than one, bounds a 100 KB command by a tenth of the
  PreToolUse timeout `harness sync` registers rather than a fixed 0.2 seconds, and adds a growth
  check against a quarter-size command so a quadratic scan still fails. (#743)

## [0.13.0] — 2026-09-23

### Added

- The acceptance runner passes `CLAUDE_CODE_OAUTH_TOKEN` into each case's disposable home and the
  smoke tier counts it as a reachable credential, so an operator on a Claude subscription runs
  a Claude Code round on the token `claude setup-token` mints instead of on-demand credit (#672).

- Where the qualification targets run and how each is provisioned is now written down, because a
  release session had to rediscover it every time. `docs/compatibility.md` names the binary
  source, the login and the host for each of the four CLI targets: every round to date ran on one
  Mac, Codex on macOS is the binary bundled in the ChatGPT desktop app logged in with a ChatGPT
  session, and both Linux targets run in a container on that Mac built from the new
  `scripts/linux-target.Dockerfile`, which pins both clients. The runbook's new target-hosts
  section gives the commands in order. The smoke tier's `credentials` check now takes
  `--targets`, which a qualification round passes through, and fails at once, naming the target,
  when a client is off `PATH`, the Codex login is missing or the Docker daemon a Linux target
  needs does not answer; with no targets named it checks what this host can run and reports a
  macOS target on a Linux host as skipped. The runbook also records that the acceptance runner
  does not yet carry a Codex session login into its disposable home, so Codex evidence can only
  be produced by hand until it does. (#408)
- `scripts/cost_bench.py replay --tag` runs a pinned git ref of this repository, and is repeatable,
  so `--tag v0.12.0 --tag v0.13.0 --model <id>` measures two harness versions against bare in one
  invocation and writes a history row for each, labelled with the version and commit of the ref it
  actually ran rather than of whatever harness happens to be installed. Each ref is checked out
  with its history intact and projected by its own `bin/harness sync` into a config directory of
  its own, run with a temporary HOME as well as an explicit `CLAUDE_CONFIG_DIR`: the profile the
  owner runs under is neither read nor written, and the owner's identity and stance selection —
  which a sync renders out of `~/.config/agent-harness/config.json` — stay out of the measurement,
  so two tags are asked the same question. Every ref is resolved before the first launch, a ref
  that does not resolve is a named error rather than a quietly missing row, and both temporary
  directories go even when a run in the middle of a tag's schedule raises. The harness arm's fence
  admits the pinned checkout its profile links into, and `--dry-run` prints the schedule per tag
  and syncs nothing. `--spend-cap` applies to each tag's schedule on its own. A profile's
  credential is keyed on its absolute path, so a temporary one is not signed in: name a signed-in
  `--harness-config` for the tag to be synced into when the run is meant to spend. That profile
  must start with no harness files in it, none of the names the sync writes may be a link out of
  it, and it cannot serve `candidate` in the same run; after each tag the sync is taken back out
  of it — exactly what its own manifest records is removed and `settings.json` is put back
  atomically from a copy — while anything else written during the run stays and credential files
  are never copied, rewritten or deleted, and the profile is checked afterwards so a tag whose
  sync recorded elsewhere stops the run with the leftovers named rather than stranding the next
  one; the pinned checkout is readable, never writable, from the arm, and every refusal about the
  target is decided before the first launch (#599).

- A qualification round names two capability classes per target rather than one: an execution
  class, `standard` by default, for the worker that runs the scripted cases and writes the
  findings, and an assessment class, `strong` by default and a floor rather than a preference,
  for the reader that assesses the observations. Now that the required cases are scripts,
  running them is reading JSON and writing a file, which is not work the strong tier is needed
  for; assessing what they observed is, and the published procedure requires a reviewer. Both
  classes are written into the evidence record and the round record, so the evidence says which
  class produced an observation and which class read it, and either can be moved per target with
  `--execution-class TARGET=CLASS`. Three pairs are refused before any client is launched: an
  assessment class weaker than `strong` or one the adapter does not map at all, and a cheap
  execution class that resolves to the assessment class's own model, whether because the table
  maps both to one identifier, spells one model two ways, or maps the cheap class not at all so
  that it resolves upward — in each case the worker that produced the evidence would be its only
  reader. The two identifiers are compared through the usage ledger's own normalisation rather
  than as strings, so a date-stamped id and a bare alias of one model are one model. An unmapped
  execution class beside a mapped assessor is disclosed in the record rather than guessed at.
  The routing is written to the durable log before the first case and to the round record before
  the smoke tier, so a killed round still says which classes were running, and `--from-progress`
  refuses a log that mixes two routings rather than merging them. The resolution reads the
  adapter's table and not a personal `tiers` override, because a round runs from a frozen clone. The
  saving is the issue's estimate and not a measurement: 230K–590K output tokens per round, most
  of it authoring rather than judgement (#338).
- Every required acceptance case now has a driver in `scripts/native_acceptance.py`, so
  `--dry-plan` no longer says of any case that it is not automated yet. Nine of them are new here;
  most were prose in an evidence record that a worker re-implemented by hand each round, which is
  where most of a round's orchestrator tokens went. They include `spawn-confinement`, which #291
  added to the required set: it spawns a framework's review work with no `subagent_type` at all,
  carrying only the sentences the integration descriptor itself declares — read from the
  descriptor rather than restated, so a driver cannot keep passing after the descriptor stopped
  naming them — and reads the refusal beside an ordinary spawn that must still run, because a
  guard that refuses everything would otherwise read as a pass. Each new case emits the
  observation string a reviewer assesses, and each returns `unverified` rather than a pass when
  the behaviour it is about was not observed: a runtime that writes no subagent record is a named
  gap, and a model that declined a turn is not a control. `gate-invalidation` delivers its Stop
  events to the runtime's own coordinator rather than paying for eleven client turns, and says so
  in its observation. The provisioning and driver that used to live only in each round's scratch
  copy are committed as `scripts/qualification_provision.py` and `scripts/qualification_round.py`;
  the clone comes from this repository's own object store, and the pinned BMad installer, for the
  optional integration suite a minor release runs by hand, is the only step that reaches the
  network. The runner also gained a Codex configuration home — its own
  `CODEX_HOME`, the `codex exec --json` invocation and the rollout layout — derived from
  `adapters/codex/worker.py`, `policy/hooks/usage-log.py` and the documentation. No Codex round
  has been driven through it, so every Codex verdict is reported `unverified` with its
  observation kept until an operator compares one against a hand run and passes
  `--home-confirmed`, which is the discipline `permission-controls` already owes. Confirmation is
  per target, because one surface agreeing with a hand run says nothing about another, and on an
  unconfirmed surface an assertion that did not hold is `unverified` too rather than `failed`:
  what is in question there is the reading, not the harness (#336).
- Every decision-provider call now leaves a `kind: "decision"` row in the usage ledger beside the
  session rows, carrying the decision point, the mode, the status, the requested and the returned
  model id, the pack and request hashes, the judgment and severity labels, the deterministic
  outcome and the one an `act` mode would have reached, the token counts, the latency and the
  session that asked — and none of the state it sent, no prompt, no file path and no environment
  value, because the row is built key by key from that list and reads nothing else. The
  counterparty is part of what goes out in the request and could be a path, so a row keeps it only
  when it matches the `repo:<name>/<branch>` slug the ledger already derives, within a bounded
  length, and keeps a short digest of anything else. Exported over OTLP the row travels under
  `harness.decision.*`, its price included, because its `input` and `usd` in the columns a
  session's land in would have a backend counting the harness's question as session spend. A
  judgment costs tokens and holds up a turn, and until now neither figure was anywhere: `harness
  usage --by provider` prices the calls from `policy/prices.json` like any other row and reports
  the latency distribution beside the statuses, which are separate columns rather than a success
  rate — an answer the provider abstained from is not the same event as no answer at all. A call
  whose usage nobody reported is `partial`, so it is named in the unpriced footer rather than read
  as free, and the rows are counted on that report alone: their tokens were spent asking a
  question, not by the session, so adding them to a day or a repo would charge a session for a
  bill it did not run up. The write is an append under the ledger lock rather than the rewrite a
  session record does, so a call inside a hook's budget does not re-read and rewrite the whole
  file. `harness doctor` now also names the model every request pins and what the last call
  returned, since a provider may answer on a model the harness did not ask for and a report priced
  at the requested one would then be priced at the wrong rate. `telemetry.decisions` is one switch
  over both ledgers: off, neither row is written (#139).
- A labelled corpus for the eleven detectors this repository writes itself, and a `corpus` job
  beside `test` that scores it. `tests/fixtures/detector-corpus/` holds thirteen synthetic
  transcripts and the labels over them, five positives and five near-misses per detector bar the one whose positive costs two hundred
  searches, written
  by `build_sessions.py` beside them; `scripts/detector_corpus.py --floor 0.9` runs both that
  corpus and the one inside the vendored `ruleprobe` wheel through the whole registry and exits
  non-zero when a detector's precision or recall falls under the floor, or when a detector has no
  labelled example at all. Every row `harness usage --rules` prints now has a measured precision
  and recall rather than a hit count of unknown quality. Two detectors measure 0.83
  precision: any basename holding `id_rsa` is a hit for `secrets/git-add-secret-file`, so a runbook
  named after a key is one, and `autonomy/denied-by-grade` matches the grade hook's signature
  anywhere in a Bash result, so a grep that prints it is one. The floor stays where it is and each
  miss is recorded in the corpus with the score and the floor it was measured against, so an
  improvement or a regression both fail the job until the record is updated, while a run at a
  lower floor leaves the record dormant rather than stale (#522).
- `harness integration check|apply <name>` is the surface for a declared framework integration.
  It reads the template directory, the install destination, the presence probe and the skill
  surface from `policy/integrations/<name>.json`, so the CLI holds no framework name, and the
  session-start drift notice probes that descriptor's `detect` path instead of a directory
  literal in the hook. Its `detect`, `templates`, `destination` and `skill_roots` must be
  relative paths with no `..` segment, because `apply` writes under one of them.
  `harness bmad check|apply` is kept as an alias, and installed override files are
  unaffected either way (#349).

- Every session row carries `raw_vs_deduped`, the measured size of the usage deduplication: the
  per-line sum of the four token fields over the deduplicated total the row reports, taken over
  the same records — the session's own and those of the subagent files folded into it. The
  totals are corrected once per message id at that id's largest figure, and the raw sum used to
  be discarded, so a session whose transcript repeated every response and one that repeated none
  reported the same figure with no way to tell them apart. `harness usage` prints the
  ratio for the window as a footer figure beside `unpriced` — each row's ratio weighted by the
  deduplicated tokens it contributed to the columns above, so the figure is the window's raw sum
  over its counted sum — and the OTLP export carries the row's own as a `raw_vs_deduped`
  attribute. A Codex session row, whose runtime reports cumulative snapshots rather than a figure
  per record, reads `unknown` rather than `1.0`, which would claim a measurement nobody made; a
  subagent row, a worker row and a row written before this release carry no such key, which the
  report reads as unknown and counts in the footer (#518).
- `harness decisions eval` replays the labelled rows of the decision log through a question
  pack and reports how closely the judgment tracked them, so a provider can be measured before
  it is trusted. The evaluated set is the real log — the input a hook judged, the deterministic
  answer it gave and the outcome the session later showed — replayed in `shadow` mode against a
  recorded fixture, so an ordinary run opens no socket, writes no ledger row and needs no
  credential. Question packs are versioned: a pack carries an id, a `major.minor.patch` version
  and the hash of its content, is frozen at construction so nothing holding one can rewrite a
  criterion between the hash being taken and the request being built, and the provider puts the
  id and version on every ledger row beside the request hash. The dev and held-out split is
  seeded by the hash of the decision point and the capped input a request actually carries,
  rather than by `random` or by the row's hash of the uncapped text, so a re-run reproduces it,
  new rows do not reshuffle the old ones, and one identical request cannot sit on both sides —
  which is refused outright rather than reported. Thresholds are fitted per decision point on
  the dev split and only the held-out block is evidence; there is no global default, because a
  cutoff optimal on one workload does not transfer, and a point with no labelled dev case is
  reported unfitted rather than given the shipped 0.8 as though it had been measured. The fit is
  scored under the provider's own semantics — below the threshold the deterministic answer is
  what is compared against the label, since that is what the harness would have done, and
  scoring an abstention as a miss would drive every fit to the lowest confidence in the set. The
  report names accuracy, the deterministic answer's own accuracy as the baseline to beat, how
  many cases the provider could have changed at all, the confusion by label, agreement with the
  deterministic answer, a calibration table over the confidence with its expected calibration
  error and a bootstrap interval drawn from hashed indices, the flip rate over repeated passes,
  tokens, cost per 1,000 decisions where a price was given, the returned model ids, and an
  `unusable` block in which an unavailable call, an error and an abstention are each counted and
  none is ever a pass. It carries no clock, no absolute path and no input text, so two runs over
  one log are byte-identical and the file can be sent on. What the labels do not prove is in the report itself: `grade-bash`
  records a row only where the harness asked or denied, `ran` is a user approving something they
  were asked about rather than proof the prompt was unneeded, and `not_run` does not tell a
  refusal from an interrupted turn. `--live` is the opt-in path and needs an explicit request
  ceiling; `--budget-usd` needs a price beside it, because none is published here and a dollar
  ceiling nobody can convert is not a ceiling; `--replay` and `--live` together are refused
  rather than silently ordered; and a live run under an empty `governance.jev.state_fields` is
  refused as pointless, since every request would then differ in nothing (#138).
- The `jev` decision provider is opt-in per decision point, and sends only what a configuration
  lists. `governance.jev.mode` sets a default and `governance.jev.modes.<point>` overrides it for
  one of the points the decision ledger already names: `off` calls nothing, `shadow` calls and
  writes the ledger row where neither the model nor the user sees it, `advise` adds the judgment
  and says what acting on it would have done, and `act` may turn an `allow` into an `ask` and
  nothing else. Every mode defaults to `off`, so a configuration written before this existed
  makes no request, and an unknown mode, decision point or field is refused at
  `harness config set` rather than at the first call. Two controls sit outside the modes:
  `~/.local/state/agent-harness/jev-disabled` disables every call while it exists, read per
  decision so the switch needs no restart or configuration change, and the allowlist
  `governance.jev.state_fields` is empty by default and covers `command` and `summary` alone:
  a file path, a prompt, an environment value, tool output or assistant prose has no field to go in
  and is never built into a request. A listed field whose text matches one of the shared secret
  shapes is dropped whole rather than masked, and free text is withheld entirely when that
  pattern list cannot be loaded; redaction recognises the shapes it knows, which is why the
  allowlist is two fields rather than a free vocabulary. The request timeout defaults to two
  seconds inside the hook budget, and a request and token ceiling bound the session rather than
  the process: a hook is a new process per event, so the counters are kept in the state directory
  keyed by session id under the existing lock, and a spend file that cannot be read or written
  leaves the in-process count standing rather than failing a decision. Each call writes one
  ledger row carrying the mode, the judgment label, the severity level, the deterministic outcome
  and the outcome acting on it would have reached, so a `shadow` answer can be compared against
  the decision it did not change; labels only, never the state. Every failure path
  still fails open to the deterministic decision, and `harness doctor` prints the mode per point,
  the allowlist, where the kill switch lives and whether a credential variable is set — by name,
  never its value (#137).
- One Bash command in twenty that the harness allows is now kept in the decision log as a
  sampled negative: a `grade-bash` row with `deterministic_answer: allow`, `sampled: true` and
  the `sample_rate` it was drawn at. The graded rows are all prompts, so a check that may only
  tighten an allow into an ask had nothing to measure its false alarms against. Only an allow
  the harness itself gave is sampled — a command it left to the runtime may still be prompted on
  or refused, and on Codex an approval is dropped from the hook output — and a confirmed command
  belongs to the prompt it answered. Which commands are kept is each command's own hash rather
  than a random draw, so the same corpus samples the same commands on every machine and a
  measurement over these rows is reproducible; the sample is therefore of distinct commands, not
  of invocations, and the row count cannot be weighted by the rate to estimate how many commands
  ran. The rows carry no outcome and no match key, are passed by at SessionEnd rather than closed
  as `not_run`, and `harness usage --by decision` counts them on a `grade-bash (sampled)` line of
  their own, so a point's outcome rates and unlabelled share are unchanged. The text is redacted
  before it is capped — assignment values, credential flags, every secret shape the rule
  detectors match and the home directory as `~` — and the row's hash is over the redacted text,
  so a short secret cannot be recovered from the hash beside it. `telemetry.allow_sample_rate`
  sets the rate and `0` turns it off, as does `telemetry.decisions: false` (#386).
- A spike record measures what the Claude Code Workflow tool does to the delegation guards. A
  script's `agent()` calls produce no `Agent` tool call, so band routing, the brief guard and the
  constrained-role refusal never see them, and a script can run a read-only harness role in session
  by naming it in `agentType`; the usage ledger does record every workflow agent, but those rows
  carry no tool use id, so the reroute join is empty for all of them. The record names the two
  changes the code needs. `docs/spikes/2026-09-22-workflow-tool-band-routing-and-ledger.md` (#540).

- A framework integration descriptor, `policy/integrations/<id>.json`, names a framework, the
  release it is pinned to, how its spawns are recognised, which harness role each spawn maps to,
  and the input roots a confined worker needs; BMad Method 6.12.0 is the first tenant. The spawn
  hook now classifies a native spawn against those descriptors instead of trusting the
  `subagent_type` the model wrote, so a review layer re-issued as an unnamed subagent is refused
  with the same isolated-worker instruction a named `reviewer` spawn gets, and the refusal names
  the read roots that worker needs. The `harness-role:` line in a routed brief goes back to being
  an optimisation rather than the thing confinement depends on. Recognition is corroborated: one
  of the framework's own layer names as the spawn type is enough, a path out of its routed text
  counts only with one of its sentences beside it, and two of those sentences are enough on their
  own, so the fix-up brief after a review, a brief that edits the override templates and a brief
  that quotes a single line of them all still run. A classified refusal is deliberately not
  remembered for the session, because that memory matches by prefix and similarity and one wrong
  classification would go on refusing the corrected brief. A descriptor that will not parse or
  will not validate is announced once per session and logged rather than skipped in silence.
  `spawn-confinement` joins the required qualification cases, with a false-positive check, and the
  catalog records what a descriptor still cannot recognise (#291).

- `scripts/native_acceptance.py` drives the `permission-controls` case, which was qualified by
  hand every round: it syncs the manual, unacknowledged bypass, acknowledged bypass and auto
  postures, reads the permission mode each one wrote into the client's own settings, and asks for
  the same one-command file write under each, pre-approving no tool so that the posture is what
  decides the call. The acknowledged bypass is judged by `bypass_verdict`, landed uncalled in
  0.12.0, against the mode the client reported for that turn, so the three outcomes stay apart: a
  turn the policy blocked, a turn the model declined on its own judgement, and a turn that
  completed. A decline is `unverified` rather than a failure, which was the defect the driver had
  to be written around, and each posture's reading is kept as it is made, so a later posture that
  cannot be observed reports the earlier ones rather than erasing them. Three real client turns
  recorded under those modes — result, transcript record and the sentinel state each left — are
  the tests' fixtures, so the suite still launches no client, and `docs/releasing.md` records the
  comparison against a hand run that the first live round owes before this verdict is trusted
  (#404).
- A session that was already running when `harness sync` installed the band workers starts
  routing unnamed spawns as soon as it can resolve them, instead of waiting for a new session.
  Claude Code announces a reload to the session it happened in, as an `agent_listing_delta`
  attachment on the transcript, so the spawn hook reads that over a bounded tail, keeps what it
  found in the session record so routing survives the delta scrolling out of that read, and routes
  to a worker the session's start-time record predates when a later delta names it. The record
  stays the floor: an absent or unreadable transcript, a listing never seen in either place, and a
  session that reloaded nothing all route exactly as they did before, so a reroute still never
  turns a spawn that would have worked into one that fails. The pricing hook reads the same
  answer, and `docs/spikes/2026-09-22-registry-reload.md` records the eight sessions this was
  measured in, including the headless ones that never reload (#263).
- `scripts/smoke_tier.py` runs the repository's deterministic pre-qualification checks as one
  command that spends no model turn: the acceptance runner's self-tests against recorded
  transcripts under `tests/fixtures/transcripts/`, the documentation-link check, the
  credential-reachability probe, and the disposable-home sync, projection-drift and lifecycle
  checks. Three of the four defects the 0.11.0 qualification round recorded were deterministic
  plumbing faults of exactly this kind, each found part-way through a paid round that then had to
  be run again. Every check is bounded by a timeout, so an unauthenticatable environment is
  reported as an error rather than as a three-hundred-second hang, and a check that could not run
  is `unverified` and never a pass. The tier is additive and never qualification: it observes no
  client, and the run fails if anything it ran wrote under `compatibility/evidence/` or into the
  catalog. CI runs it as a `smoke` job that the branch ruleset does not require, and
  `docs/releasing.md` records that it is advisory until it is decided whether a red tier may
  block a freeze (#334).

- A `jev` decision provider answers the `decide`/`record`/`learn` contract over the network, in
  the standard library alone, because the vendor SDK needs Python 3.10 and five packages where
  this repository's floor is 3.9. It validates a question pack of `choice`, `boolean` and `score`
  answers before anything is sent, refusing a `choice` question that offers no explicit `unknown`
  option: the service cannot abstain, so a pack without one leaves a model that cannot answer no
  way to say so but to guess. A request is bounded at 64k tokens, and its state plus the longest
  question at 32k; a response that is malformed, incomplete or carries a field nobody asked for is
  an error and never a judgment with the bad parts dropped; a budget of requests and tokens is
  checked before each call and charged after it. A judgment may turn an `allow` into an `ask` and
  may never widen a decision, and every path with no usable answer — no key, a timeout, an
  exhausted budget, an unparseable body, an unexpected exception — returns the deterministic
  provider's decision unchanged with the reason in `rule_matches`. Each call records the status,
  the requested and returned model ids, the pack hash, the request hash, the usage and the latency
  to the decision ledger, and never the state. Answers are not deterministic across identical
  requests, so nothing here promises otherwise. The endpoint must be `https` and the opener holds
  no handler for any other scheme, because a bearer key goes out with every request; a request is
  charged to its budget as it is sent rather than when it succeeds, so a refusing endpoint cannot
  be retried without limit; and `harness decide` suppresses the ledger row, because a reporting
  command changes nothing. The client is inert unless a caller constructs it with `live=True`; the
  opt-in configuration, per-decision-point modes and the sentinel file are #137. The endpoint, the
  default model id, the token ceilings, the response shape and the HTTP status mapping are taken
  from the vendor's documentation and have not been verified against the live service from this
  repository, which is what the one opt-in live request in the acceptance criteria is for (#136).

- The compatibility matrix carries a `tier restriction` row saying, per client surface, whether
  the delegation stance's model-tier ceiling is enforced, advisory or absent, and names the file
  behind each state. It is derived from a `tier_restriction` entry in
  `adapters/<runtime>/capabilities.json` rather than written into the rendered docs: the Claude
  Code CLI and VS Code surfaces read `enforced`, because `claude/hooks/tier-agent-spawns.py`
  rewrites a spawn asking for the strongest class by name. Every Codex surface reads `advisory`:
  the coordinator runs there and a Codex `Agent` call still passes the role, marker, evasion and
  brief checks, but the tier rewrite sits behind a `runtime == "claude-code"` gate in
  `lib/harness_core/lifecycle.py`. The plugin-marketplace install reads `advisory` because it
  installs no hooks at all. The generated note states what `enforced` does not cover — the
  session's own model, which the harness never writes; a `delegation` variant other than
  `tiered`; and a class table mapping fewer than two models — and the `delegation-tiering` skill
  now links to the row instead of restating it (#520).
- `scripts/smoke_tier.py` runs the repository's deterministic pre-qualification checks as one
  command that spends no model turn: the acceptance runner's self-tests against recorded
  transcripts under `tests/fixtures/transcripts/`, the documentation-link check, the
  credential-reachability probe, and the disposable-home sync, projection-drift and lifecycle
  checks. Three of the four defects the 0.11.0 qualification round recorded were deterministic
  plumbing faults of exactly this kind, each found part-way through a paid round that then had to
  be run again. Every check is bounded by a timeout, so an unauthenticatable environment is
  reported as an error rather than as a three-hundred-second hang, and a check that could not run
  is `unverified` and never a pass. The tier is additive and never qualification: it observes no
  client, and the run fails if anything it ran wrote under `compatibility/evidence/` or into the
  catalog. CI runs it as a `smoke` job that the branch ruleset does not require, and
  `docs/releasing.md` records that it is advisory until it is decided whether a red tier may
  block a freeze (#334).

- BMad story files now carry the design. `scripts/bmad_issue_sync.py` writes each new work item
  from a typed template for its kind, with the issue link, parent and state in a managed block that
  `refresh` rewrites while keeping the rest of the file byte for byte. A new `upgrade` subcommand
  converts legacy stubs without loss, carrying amendment sections over verbatim, and
  `audit --delivery N`, run by the required `issue-ownership` check, fails a pull request whose own
  story leaves a required section unfilled. Legacy stubs pass that check with a notice until they are
  upgraded. (#620)

- BMad's sprint and build workflows now have a status surface that nobody maintains by hand.
  `python3 scripts/bmad_issue_sync.py sprint-status` renders
  `_bmad-output/implementation-artifacts/sprint-status.yaml` from the issue map and the story files,
  deriving each item's status from its GitHub state and its story's depth check. `new`, `reserve`,
  `refresh` and `upgrade` regenerate it whenever they write, `audit` fails while it drifts, and the
  `bmad-build` and `bmad-sprint-planning` customizations point at the command instead of editing it. (#623)

### Changed

- Pull requests to `main` land through a merge queue, and changelog entries after 0.13.0 are
  one fragment file per change under `changelog.d/`. Every pull request workflow also runs on
  `merge_group`, and the issue-ownership and landing-copy checks read the queued pull request's
  number from the queue branch, since a queued entry whose checks never report stalls the queue.
  `scripts/release_notes.py --changelog <version>` assembles the fragments into a version section
  in a stable order, and `bin/harness lint` fails a branch that changes `bin/`, `lib/`,
  `adapters/`, `primitives/`, `policy/`, `docs/` or `scripts/` without a fragment or a
  `<number>.none.md` waiver. Every branch used to edit the same Unreleased section, so any two in
  flight conflicted there, and the up-to-date requirement cost a rebase and a full CI rerun per
  landing. (#337)
- An isolated role worker is no longer handed the harness checkout as a read root and pointed at
  the whole skill corpus. It carries the shared policy as its system text and is mounted only
  what that policy tells it to open: the skills the resolved rules and stances name, and copies
  of the `docs/*.md` files they cite. The set is derived from the text itself, so a stance that
  stops citing a skill stops paying for it, and a worker is never told to obey a rule whose
  reference it cannot reach. A role adds what its body assumes but the shared text never names
  with a `skills:` line — `design-loop` for `design-judge`, `all` for `planner`, whose body tells
  it to read the skills the plan will name — and a name that resolves to no shipped skill fails
  the run rather than quietly removing that authority. Measured as what is mounted rather than
  what a run reads, a review layer went from the whole checkout, about 1,073,900 estimated tokens
  of text, to about 30,800, and the corpus it was offered as skill authority from all 31,600
  tokens to the 26,600 the policy cites. Each run records the figure under `context` in
  `status.json` — policy, reference and total against a 50,000-token budget, counted with the
  approximation `harness lint` applies to always-loaded context — recorded and not enforced,
  because what a worker is shown is fixed by its contract before any brief is read. On Claude
  Code the narrowing is enforced by the restricted file tools; under Codex's read-only sandbox it
  is instruction text, as that runtime's declared input roots already were (#335).
- The BMad override templates ask each review layer to launch only once the previous layer's
  worker has exited. This is correctness before economy: a role worker that is still running
  publishes no token count, so a round with four layers in flight cannot be held under a cap it
  cannot measure, which is how an observed 450,000-token cap became 985,000 (#335).
- A release no longer runs a third-party framework's own workflow. `bmad-workflow` leaves
  `required_cases` and is replaced by `framework-spawn-routing`, a generic case that builds a
  fixture recipe out of whatever `policy/integrations/` declares and drives the spawn hook with
  it: a recipe layer is refused whether it is spawned unnamed, as a generic subagent or as a band
  worker, the refusal offers exactly the descriptor's declared input roots as the isolated
  worker's read roots, and one cheap turn confirms that an unnamed spawn still routes to the cost
  variant's default band worker at that row's class and effort with the budget sentence, and that
  a null variant rewrites nothing. The BMad run becomes an optional, non-gating suite run once per
  minor release on one target, documented in `docs/bmad.md` and `docs/releasing.md`; the offline
  template and surface tests stay in CI, because they are what catches an upstream rename. The
  catalog now carries two limitations in place of one: that no framework's workflow is exercised
  natively in a qualification round, and what descriptor-driven confinement still cannot
  recognise. Generic task continuation moves off the framework page to
  `docs/task-continuation.md`, and the framework-named asides in the design-loop skill, the
  handoff workflow, the delegation-tiering skill and a spawn-hook test are gone (#349).

- `claude/settings.template.json` no longer carries a hooks block. Dispatch has been
  single-coordinator for some time — a sync registers one command per lifecycle event and
  `runtime_template()` takes that registration from `lib/harness_core/lifecycle.py` — so the
  eleven per-policy entries the file still listed were replaced unread at every sync, and an
  entry added there by hand would have been silently discarded. `docs/how-it-works.md` now
  describes the model: why one process per event rather than one per policy, where precedence is
  decided, and how the coordinator fails closed. Two places that still described registration as
  conditional are corrected with it: `docs/preferences.md` said the `plan-ceremony` stance decides
  whether the plan-card validator is registered, where it decides whether the validator runs, and
  `claude/OWNERSHIP.json` now says in the manifest itself that a hook id's `stance` and `variant`
  name when a policy acts, never whether it is registered. No installed settings file changes,
  because what sync wrote was already the coordinator registration (#521).

- The standing context every session loads is 1,005 estimated tokens smaller, 7,524 to 6,519 on
  `scripts/cost_bench.py static`. The `scannable` output style keeps all nine of its rules and
  loses the worked examples and the register table, 1,528 tokens to 862; the fifteen skill and
  eleven agent descriptions lose their capability restatements and keep every condition and
  literal user phrasing a session selects on, 2,326 tokens of listings to 1,980. Because a
  description is the trigger mechanism, those phrases are now frozen in
  `tests/test_description_trigger_phrases.py`, which fails when one is dropped rather than
  reworded, and a new skill or role must declare its own. Before-and-after rows are in
  `docs/benchmarks.md`; the third change the issue names, deferring action-gated rule text behind
  the hooks that fire on the act, is framed as an unrun spike in
  `docs/spikes/2026-09-22-deferred-rule-text.md` and nothing about it is implemented (#430).
- `/plan` now enters plan mode, writes its Review Card into the plan file the runtime designates,
  and finishes through `ExitPlanMode`, so the native plan pane is the review surface and the
  native approval is the gate. The typed `build` reply was a convention no tooling could observe,
  and a plan written straight to disk reached no plan view at all. Because the runtime fixes the
  filename before any content exists and plan mode permits no other write, the naming waits for
  approval: `/plan` then renames the file to a topic slug — refusing to overwrite an existing
  name — and invokes `/build` with that path, which the builder commits into its worktree so the
  plan reaches the pull request. `/build` works from the path or the
  issue number it is given and never searches for a plan, because a plan found by modification
  date is as likely to be a stale one a checkout touched. `/plan` asks before entering plan mode,
  since entering it is the user's call, and a runtime with no plan mode — or a user who declines
  it — keeps the previous behaviour as an explicit branch: write the file named for the topic,
  open it for the reviewer, close on the build line (#439).
- Native qualification evidence is now invalidated per target rather than per repository. Each
  client's evidence is checked against the shared runtime source plus its own runtime's adapter
  directory, so a fix confined to `adapters/codex` no longer costs the Claude Code targets of a
  qualification round, and the reverse holds; a shared-source change, an unmapped runtime or a
  record that states no scope still invalidates everything, so the narrowing fails closed. The
  catalog declares the per-runtime directories and each evidence record states the path set it
  was validated under. The narrowing stops at the files shared code reads whatever the runtime —
  `bindings.json`, `capabilities.json` and `worker.py`, which `harness tiers`, stance coverage and
  `harness role run --runtime` reach for either adapter — so those are declared in the catalog and
  still invalidate every target; only `hook.py` is private to its runtime. A new test parses the
  runtime source and fails when a loader reaches an adapter file the declaration does not cover.
  Per-case scoping is not included: it changes a v1 stable interface and waits on an owner
  decision (#333).

- The delegation rule now states that subagents never message a peer, and the builder role says
  what a blocked builder does instead: stop, finish what does not depend on the answer, and return
  the question under **Deviations** for the caller. A delivered message bills as a typed prompt on
  the receiver and again on the sender when the reply lands, and turn count is what the delegation
  arithmetic is sensitive to, while the measured coordination wins in the literature all come from
  mediating writes at write time rather than from agents conversing; `delegation-tiering` carries
  that reasoning with the three papers cited. Session-to-session `SendMessage` between
  human-facing sessions is unchanged (#539).

- The BMad governance text that every planning workflow loads now splits authority between the issue and its
  story file, routes each SDLC step to its BMad skill, and requires every pull request to keep the planning
  corpus current. The build workflow treats a work item's story file as its spec and writes the design back
  into it. (#621)

- The field scan credits claude-md-doctor and RuleReceipt, which now bind a check to each rule and report from Claude Code transcripts whether it was followed, and narrows the measured-rules uniqueness claim to what they do not do: the lint that refuses an unmeasured rule, detectors scored against a labelled corpus with a precision floor in CI, per-variant grouping, and Codex as well as Claude Code. The research run for the scan carries both sources as a dated amendment. (#710)

- Step 8 of the qualification procedure now says what a headless client can show about a session
  started before the band workers were installed: its session record never widens to them and its
  spawn still succeeds. Such a session is continued by resuming it, and the resumed process loads
  the workers from disk and announces them, so routing its spawn to an announced worker is correct;
  a reroute to a worker that neither its record nor that announcement named is still a defect. The
  old sentence asked for a session that was never rerouted, which no headless turn can show. (#733)

### Fixed

- The stop gate releases a turn after eight consecutive red blocks in each session, even when
  another session is stopping in the same checkout. It kept one count per checkout and reset it
  whenever a different session stopped, so a Remote Control host session and an interactive one
  in the same checkout zeroed each other's count and the gate never let either go. The count is
  now kept per session, and a session silent for a day is forgotten (#611).

- `scripts/cost_bench.py replay --tag` no longer counts the CLI's own synced skill packs
  under `skills/synced/` as leftovers of a tag's sync, so a run with two tags into one named
  profile reaches its second tag instead of stopping after the first (#649).

- `harness lint` no longer reads untracked files under `.agent-harness/`. That directory stays
  unignored so a handoff can read its plans, so any session's local note naming a project outside
  `docs/` turned the lint red, and the stop gate with it, for every other session in the same
  checkout. Tracked files there are still linted, and staging a note brings it back into the lint
  before it can be committed (#605).
- Remote Control hosts managed by `harness remote-control` now reuse their environment across a
  restart. Claude Code 2.1.280 reads the folder's bridge pointer only when `createSessionInDir`
  is on, and every host was launched with `--no-create-session-in-dir`, so each restart
  registered a fresh environment and each `SIGTERM` archived the host's sessions. The flag is
  gone; heal's pointer rewrite keeps 2.1.280's two `parkedProjectThreadSessionIds` keys; `install`
  on a changed agent adopts the running host's environment, writing the pointer and stopping the
  host with `SIGKILL` so its archive path never runs; `heal` re-queues this Mac's disconnected
  sessions through `bridge/reconnect`, at most once per session every ten minutes; and a
  `remote_control.folders` entry may be an object with its own `spawn` and `env`, so each
  workspace root gets a host that loads its own `CLAUDE.md`, skills and hooks (#603).

- The `delegation` stance, the shared role descriptions and the refusal a native `gatherer` or
  `reviewer` spawn receives now carry one sentence word for word: a read-only role runs through
  `harness role run <role>`, because confinement is read roots and return shape rather than the
  absence of write tools, which is also why `builder` is exempt and spawns natively. A session
  that followed the stance used to spend a refused call discovering a rule none of the three
  texts stated, and the refusal's reason for exempting the write-capable role was nowhere. A test
  holds the three copies together, so the sentence cannot drift in one of them (#304).

- The credential probe answers for a variable holding something that is not a path, where asking
  the filesystem about it used to raise and carry the value into the error's own message — a
  service account document pasted into `GOOGLE_APPLICATION_CREDENTIALS` printed its private key.
  An unusable value is now treated as a file that is not there, and the reason names the variable
  and never the value (#334).

- The usage feed tells the orchestrator when its own session has grown past the posture's
  fresh-session threshold. A long session's cost is mostly the context every further turn
  re-reads, and the turn line, which reports output tokens, showed none of it. A new
  `session_nudge_at` switch in the cost sidecar lists context sizes in whole tokens, resolved
  over `extends` like every other switch; on `UserPromptSubmit` the feed reads the newest
  response's input tokens plus its cached prefix and, at a crossing, adds one line naming the size,
  the threshold and the advice to finish the task, write the handoff and start fresh. It is said
  once per threshold rather than once per turn: the thresholds already said are kept in the
  session's state file, and survive the reader starting over on a transcript whose identity
  changed, so a resume does not repeat them. A context that falls back under a threshold, which
  is what an in-place compaction does, arms that threshold again. Nothing is blocked. `frugal` ships
  80,000 and 120,000, `balanced` 120,000 and 160,000, and `max` nothing at all; those are
  starting points chosen against a 200,000-token window rather than measured figures, and the
  follow-up to this issue replaces them with sizes read out of the ledger. Codex raises no
  `UserPromptSubmit` event and declares the nudge uncovered (#321).
- `docs/spikes/` records the measurements a decision was taken on, starting with the in-run budget
  nudge: whether a running subagent should be told mid-run how its spend compares with its soft
  budget. Measured on one machine's ledger, 2 of 89 budgeted subagent runs overran, the excess was
  4.5% of subagent output, and in the larger sample of runs whose brief carried no budget the
  median overrun was discovered with one tool call left — too late to act on — so nothing is built
  and the record says which numbers would change the answer (#322).
- A `jev` decision provider answers the `decide`/`record`/`learn` contract over the network, in
  the standard library alone, because the vendor SDK needs Python 3.10 and five packages where
  this repository's floor is 3.9. It validates a question pack of `choice`, `boolean` and `score`
  answers before anything is sent, refusing a `choice` question that offers no explicit `unknown`
  option: the service cannot abstain, so a pack without one leaves a model that cannot answer no
  way to say so but to guess. A request is bounded at 64k tokens, and its state plus the longest
  question at 32k; a response that is malformed, incomplete or carries a field nobody asked for is
  an error and never a judgment with the bad parts dropped; a budget of requests and tokens is
  checked before each call and charged after it. A judgment may turn an `allow` into an `ask` and
  may never widen a decision, and every path with no usable answer — no key, a timeout, an
  exhausted budget, an unparseable body, an unexpected exception — returns the deterministic
  provider's decision unchanged with the reason in `rule_matches`. Each call records the status,
  the requested and returned model ids, the pack hash, the request hash, the usage and the latency
  to the decision ledger, and never the state. Answers are not deterministic across identical
  requests, so nothing here promises otherwise. The endpoint must be `https` and the opener holds
  no handler for any other scheme, because a bearer key goes out with every request; a request is
  charged to its budget as it is sent rather than when it succeeds, so a refusing endpoint cannot
  be retried without limit; and `harness decide` suppresses the ledger row, because a reporting
  command changes nothing. The client is inert unless a caller constructs it with `live=True`; the
  opt-in configuration, per-decision-point modes and the sentinel file are #137. The endpoint, the
  default model id, the token ceilings, the response shape and the HTTP status mapping are taken
  from the vendor's documentation and have not been verified against the live service from this
  repository, which is what the one opt-in live request in the acceptance criteria is for (#136).
- `telemetry.completion_claim`, off by default, records the agent's completion claim on a
  `stop-gate` decision row: the last 2 KiB of the turn's final assistant message, read from the
  transcript at Stop because the Stop payload carries no assistant text, with the hash over the
  uncapped message. Verifying what an agent said it had done against the gate result needs the
  two on one row, and until now the row held only the gate. It is its own switch, and off,
  because it is the only field in the decision log that holds assistant prose; with it off the
  row is byte for byte what it was. The claim is the turn's own: the scan stops at the user
  prompt that opened it, so a turn that ended in a tool call claims nothing rather than
  repeating the previous turn's words. The read is a bounded tail, so it costs the same on a
  transcript of any size, and where there is no claim the row carries a null one beside a
  `completion_claim_miss` naming why — a runtime that supplied no path reads differently from
  evidence that is gone (#387).
- Every replay-benchmark row records `cache_miss_ratio` beside its cache-normalised cost: the
  share of the run's prefix the provider re-wrote rather than served, summed over every turn the
  run opened. The arithmetic is the one `harness usage --by prefix` applies to a ledger row, and
  is imported from that module rather than restated, but the two figures answer different
  questions and a fan-out run will differ: the replay counts a subagent thread's fresh prefix as
  part of what the run cost, where the session figure subtracts it. `benchmarks/history.jsonl`
  and `history.md` carry each arm's mean of it, so a candidate that buys fewer tokens by
  re-writing its prefix more often is visible in the history rather than hidden inside the
  dollars. A run whose CLI output carries no per-turn cache figures, or any one of whose turns
  reports its usage without them, is `null`, never zero, since zero is a run that held its whole
  prefix (#497).

- Three faults in the qualification scripts, each of which would have cost a paid round to find.
  The round's clone passed `--shared=false` to `git clone`, an option that takes no value, so
  every provision exited; `bidirectional-handoff` sent the same `--revision` twice expecting a
  refusal second, where `lib/harness_core/tasks.py` refuses only a revision that has been spent,
  and it sent the writing runtime as a contract field the same module rejects, so the case could
  never have passed; and the round driver left a
  previous target record in place, so a runner that exited before writing `--out` reported the
  older round's passes as this round's. A previous record is now moved aside first and an absent
  one is every case `unverified`, a target or smoke tier that runs past the deadline is recorded
  and carried rather than raised, `--print-env` quotes every value it prints, a round directory
  inside any checkout or worktree of this repository is refused, and a `clone` directory this
  script did not create is refused rather than deleted. `hook-composition` now matches text only
  the `grade-bash` hook writes rather than the stance name, which a model can echo without any
  hook having decided, and `custom-stance` names the resolved variant by its bytes where a
  surface copies it instead of linking it (#336).
- The credential probe answers for a variable holding something that is not a path, where asking
  the filesystem about it used to raise and carry the value into the error's own message — a
  service account document pasted into `GOOGLE_APPLICATION_CREDENTIALS` printed its private key.
  An unusable value is now treated as a file that is not there, and the reason names the variable
  and never the value (#334).

- An assistant transcript record whose `message` is not an object is skipped rather than read as
  one. Such a record holds no usage, no model and no content blocks, and reading it aborted the
  scan of the whole transcript, so one malformed line cost the session its entire row; the
  subagent reader beside it has always skipped the same shape (#518).
- An assistant transcript record that carries no message id is deduplicated by its `requestId`
  rather than counted once per line. Every such record used to open a slot of its own, so a
  runtime or version that writes one id-less response several times — as the streaming lines of
  one API call — inflated the session total and its turn count without any bound. A request id
  names one call, so it deduplicates unscoped by file: the same call written into both a session
  file and a subagent file is one response, and a call whose other records do carry a message id
  joins their slot, in whichever order the files are read, instead of being billed twice. A
  record with neither id is unknown rather than a duplicate and is left undeduplicated; session
  and subagent rows carry `idless_records` counting how many such records their totals include,
  the session's own and those of the subagent files folded into them, so a row without the field
  is known to have been deduplicated whole. Rows from transcripts whose records all carry message
  ids are unchanged (#519).
- The Remote Control sessions read checks that its capped page arrived newest-first. The endpoint
  takes no sort parameter, and its page is ordered by `last_event_at` rather than `updated_at`, so
  the order is asserted on arrival and a page that is not descending is refused: under the
  fifty-row cap the rows such a page dropped are unknown rather than merely old, and the session a
  host lost minutes ago is exactly the one another order would hide. `harness remote-control
  status` prints `not checked (page order unknown)` for a refusal, which no longer reads like an
  account with nothing lost, and both it and `doctor` now say `in the newest 50` when the account
  has more sessions than one page (#526).
- The repository's own copy states the figures its code holds. The landing copy said nineteen
  detectors where the registry holds seventeen, six from the vendored engine and eleven written
  for these rules, and a new test derives that count from the rule pack and fails when `README.md`
  or `product.json` drifts from it. `docs/field-scan.md` and `docs/caught-in-the-act.md` no longer
  say the conflict engine is empty (four constraints ship and lint fails on them), that the
  always-loaded cap cites no source (it quotes the memory documentation and the measured standing
  context) or that there is no labelled corpus: the vendored wheel ships one, it scores six of the
  seventeen detectors, and the remaining eleven and the precision floor in CI are #522.
  `benchmarks/static.json` and the `harness lint` context line each name the set they count, which
  differ, and `benchmarks/oracles/hook_ids.py` says its module count is the count at the task's
  pinned `parent_sha` rather than at HEAD. The always-loaded line ratchet is now the line cap
  itself: the binding cap is tokens, the line cap is the secondary guard, and a third budget four
  lines below it only obscured which one binds (#516).

- A model id the price table does not list is unpriced, where an unlisted variant of a listed
  family used to inherit that family's rate. Inheritance under-bills a premium variant by a
  multiple and prints a known-low figure as a known one — `gpt-5.5-pro` is $30/$180 where
  `gpt-5.5` is $5/$30 — which was the one place the ledger broke its own rule that an unknown is
  never zero. Ids now resolve by exact match after normalisation, which additionally drops a
  release suffix (a date stamp, a reseller's `-v1:0`, an `@date`), so every dated and cloud
  reseller spelling still reaches its family entry and no recorded row changes price. To price a
  variant, add it to `policy/prices.json` or override it under `prices` in `config.json`; until
  then it is counted in the `unpriced` footer and carries no `harness.usd` attribute (#517).

- The `spawn-confinement` qualification case now reads the harness decision log for the refused spawn's
  session, so a framework spawn the hook refused is observed even when a headless client does not repeat the
  refusal in its answer. The client's text is kept as a second signal, and when it carries the refusal its
  wording is still checked. (#702)

- Three scripted native-acceptance cases now drive `bin/harness` with the contract it actually has.
  The role-confinement case passes the client's own `--runtime` to `harness role run`, the
  bidirectional-handoff case writes its task contract to a file and passes that path to
  `harness task save --input`, whose help now says it takes a path, and the migration-uninstall case
  changes a harness-owned setting by hand before uninstalling, so its expected exit 2 and "user
  changes preserved" are what it observes. A new test drives each case's call through the real CLI. (#703)

- The `gate-invalidation` acceptance case now reads the stop-gate hook's state record at the
  repository's resolved path, where the hook writes it, so a disposable home under a symlinked
  temporary directory such as macOS's no longer reports the gate as never recorded. Its probe
  repository also ignores the gate's own run counter, which had made every green run look like a
  tree changed during the gate. (#704)

- The `cost-posture` and `framework-spawn-routing` qualification cases no longer fail when a
  headless resume reroutes an unnamed spawn to a band worker. Headless `claude -p --resume` runs
  as a new process that loads its agent registry and announces the restored workers, so the
  spawn hook routes on that announcement as designed. The case now reads the session record and
  the transcript's agent listing. It fails only when the record widened on resume, when a
  reroute went to a worker nothing named, or when the spawn did not run. (#705)

- The native acceptance runner now refuses a target whose platform is not the host's, naming both,
  before any case runs; it used to stamp the record's platform from the target spec, so a Linux round
  driven on a Mac wrote the Mac's outcome as a Linux record. The round driver's plan says where each
  target runs, and a round skips a target meant for another host, reporting it as not run here. (#708)

- Three native qualification cases now observe what happened instead of reporting `unverified`.
  `role-confinement` parses the JSON worker record `harness role run` prints, and reads the result
  from its `result_path`. `bidirectional-handoff` reads the writing runtime from the task record
  itself and asks the reading session for it by name. `spawn-confinement` reads the session
  transcript to tell a refused spawn from an allowed one and from one the model never attempted,
  and says which. (#716)

- The `spawn-confinement` native case now makes the model attempt the spawn it classifies. Its brief
  quoted nothing and gave the descriptor's "read that file" no referent, so a model asked which file
  was meant instead of calling the Agent tool. The spawn prompt now opens with a sentence naming the
  file, keeps the descriptor's own sentences verbatim and still names no `subagent_type`, and the
  brief says the quoted text is the subagent's. (#718)

- A qualification round given no `--model` now passes each target its routed execution model, so
  the cases run on the model the round and evidence records declare; they used to run on the
  runner's default (`haiku`) while the records named the routed model. An operator's `--model`
  still wins and the routing says so with `model_source: operator` and the routed model beside it.
  Every evidence record and per-case row now carries `model_run`, the model passed to the client. (#721)

- A hook you add beside the harness's own in `~/.claude/settings.json` or Codex's `hooks.json` is no longer reported by `harness sync` as a changed owned field, is not drift for `harness doctor`, and stays in place at `harness uninstall`, which removes only the harness's entries. Before, one such hook froze the harness's entries for that event. (#725)

- A native evidence record now pairs every observation with the case it belongs to. The record is
  written with sorted keys, which reordered `cases` alphabetically and left `observations` in the
  order the cases ran, so pairing by position put most observations against the wrong case. Each
  case observation now opens with its case name, the list follows the record's case order with one
  entry per case, and a record rebuilt from an older durable log with `--from-progress` gains the
  same pairing. (#728)

- The `role-confinement` native case now has roles attempt the writes step 4 of the qualification
  procedure names. An isolated read-only `gatherer` is told to create a file in its workspace, and
  the planner, run with a valid `--artifact`, is told to write inside the workspace and above it.
  What stopped each write is read from the worker run's own event stream, its init event's tool set
  or the refused call, and a file that lands fails the case; a role that held a write tool and never
  used it is unverified. The `--artifact` path refusal it read before is kept, reported as the
  command-line check it is. (#729)

- The `hook-composition` native case now reads step 4 from turns rather than from the merged settings
  table. One headless turn writes two files with the client's Write tool in an untrusted repository
  that has a `## Gate` block, and the case reads the user-owned hook's own log line for each file
  and the stop gate's logged `untrusted` verdict for that session. The `grade-bash` deny under an
  acknowledged bypass is read from the deny turn's permission denials and attributed to the hook by
  its decision-log row. A turn that never uses the file tool, or writes only one file, is unverified. (#730)

- The `stance-switch` native case now switches a communication stance beside the delegation one:
  it cycles `voice` from `scannable` to `answer-card`, reads the resolved voice text and output style,
  and asks the same comparison prompt under each, passing only when the reply carries a table under
  `scannable` and none under `answer-card`. The `custom-stance` case now also applies a project
  override in a disposable repository and reads that a turn inside it follows the override, a turn
  outside it follows the global selection, and the global link does not move. An `off`
  turn that never attempts the spawn is now `unverified` rather than failed. (#731)

- The `spawn-confinement` native case now observes all of step 9 of the qualification procedure,
  and step 9 now states the claim it can prove. A review layer's spawn carrying the framework's own
  spawn text and naming no role must be refused, and the refusal, tied to its decision-log row by
  the brief's fingerprint, must name the framework, the layer and `harness role run <role>`.
  Recognition is lexical, so a brief the model rewrites in its own words is run and recorded as the
  claim's observed limit rather than passed or failed (#739). The same layer then runs through
  `harness role run`, and a run that leaves no isolated worker state or returns no findings fails
  the case. Two ordinary spawns, one whose brief mentions review, a diff and findings in passing and
  one that edits a file under the framework's input roots, must still run unrefused. (#732)

## [0.12.0] — 2026-09-22

### Added

- Subagent rows in the usage ledger now record whether the return carried a path a reader can
  open and whether it stayed inside the word cap its brief stated. `return_path` is
  `"resolvable"`, `"unresolvable"` or `"none"` — a return that named no path carries none, which
  is a fact about the return and not a failure — over paths written in a fence, in backticks, or
  bare in prose with a path's own shape, so `pass/fail` and `2026/09/22` stay prose and a URL
  names nobody's file here. `return_over_budget` compares the return's words against the cap
  `rule-detectors` reads from the brief, or the default `brief-guard` appends to a brief that
  states none; an empty brief and a spawn whose requested type carries its cap in its own
  definition are left unmeasured, and a result the scan kept only the head of records
  `return_measured: "truncated"` rather than a figure taken over part of it. Both are a string
  match and an `os.path.exists` taken when the row is written, never a model judgment, so the
  ledger holds labelled input rather than an opinion. `harness usage --by role` reports the share
  of returns that named a path whose path resolved, and the share measured against a cap that
  ran past it, printing `-` where neither applies. Codex joins no return to a subagent row, so
  its rows record `null` and its capabilities file names the gap. (#416)

- `harness remote-control` supervises each host through Claude Code's ten-minute give-up: `heal`
  keeps the bridge pointer naming the live environment, stops a host with one `SIGTERM` at nine
  minutes unreachable so launchd's relaunch takes the preserving path, and recreates the session
  worktrees a give-up already deleted. `status` lists sessions left active but disconnected on
  this Mac's environments and prints the manual reattach command for each — it never runs one,
  because a `--session-id` host registers the lost environment a second time and the client then
  routes new chats to it. `doctor` reports each host's process, environment, pointer freshness and
  disconnected sessions. Workspace trust is now read for the exact folder, as Claude Code reads
  it, and an untrusted folder fails `install` with the fix instead of looping launchd (#483).

- `harness remote-control install|status|uninstall` keeps one Claude Code Remote Control server
  per `remote_control.folders` entry running under launchd on macOS, so a new session can be
  started from the mobile app with no terminal open. A folder whose workspace trust was never
  accepted is reported and skipped, never trusted on the user's behalf.

- `harness usage --by prefix` reports whether each session held its cached prefix: the cache-miss
  ratio `cache_write / (cache_read + cache_write)` from the ledger rows it already has, with each
  subagent row's counts subtracted from its parent because the runtime folds them in, and the
  slice at which the ratio rose most sharply. A session whose subagent rows are missing, a row with
  no cache fields, and a runtime that never reports cache writes all report `unknown` rather than a
  figure. It measures and does not enforce; Codex exports no per-turn cache figures, which its
  capabilities file now records (#415).

- A `/close-out` workflow ends a finished session in one invocation: it sweeps for work still
  open — dirty checkouts, `harness worktree audit`, this session's pull requests and their
  checks, running background work, parked decisions — delegates the merge to `/land` and the
  progress file to `/handoff`, batches the follow-ups it found for filing on an explicit
  go-ahead, messages the sessions whose work this one changed where the client can reach
  them, and archives only when the invocation asked it to. It never clears or compacts
  first, because archiving ends the session and both only spend context the close-out is
  still using (#426).

- Qualification evidence is written per case as the case finishes, so a round killed part-way costs
  one case rather than the whole round. `scripts/native_acceptance.py` appends each finished case to
  a durable log outside the checkout, `--progress` names that log and `--from-progress` rebuilds a
  record from it without launching a client. The evidence schema already unions cases across several
  records per client and already rejects a linked `failed` or `unverified`, so a partial record stays
  a valid input and neither behaviour changes (#339).

- The landing copy now names the decision log: the `Live usage feed` feature in `product.json` and
  the matching README line say that a decision log on this machine records what a hook decided and
  what settled it, and that only `harness usage` reads it. A new `landing-copy` check enforces the
  rule that produced the gap: a pull request touching `bin/`, `lib/`, `adapters/`, `primitives/` or
  `policy/` must also change `product.json`, or carry a `Landing copy:` line in its body saying why
  no copy change is needed. A docs-only, tests-only or CI-only pull request never fires it (#390).
- `audit --live` reports a parent only GitHub records as `run refresh` rather than `run apply`, so a
  sub-issue added after its mapping cannot be detached by the next projection; `refresh` adopts it (#388).

- The `issue-ownership` check now refuses a pull request whose delivery issue has no BMad ID in the
  issue map, and `scripts/bmad_issue_sync.py new` files an issue and reserves its ID in one step (#378).

- `scripts/bmad_issue_sync.py audit --live` compares the committed issue map with GitHub, read-only:
  title and open/closed drift, a missing Planning block, and an accepted issue that has no BMad ID.
  `refresh` copies GitHub's title and state back into the map, and a `bmad traceability` workflow
  runs the audit daily and on issue events (#218).

- Release upkeep is written down and checked. `AGENTS.md` and `docs/releasing.md` now say when a
  release is proposed (cut by milestone, or when a user-visible unreleased change is seven days
  old, with a regression fix released at once as a patch), how it is numbered by what changed
  rather than by changelog section, and the exact `gh api` commands that close the released
  milestone and open the next. The seven release surfaces now mark the reference site and the
  personal-site card as verify only, because each repository deploys itself. A new
  `scripts/sync_about.py` compares the GitHub About panel with `product.json`: `--check` names
  every differing field and exits non-zero, `--apply` writes them through `gh repo edit`, and
  topics compare as a set. `--apply` stays a local step needing the owner's approval, because
  editing repository settings needs administration access and no such scope exists for a
  workflow's `GITHUB_TOKEN`. `scripts/release_preflight.py` runs that comparison and looks up
  every `on_the_way` entry that names an issue, blocking the release with "promote or remove"
  when one has closed. Both checks sit behind one `gh auth status` probe: without an
  authenticated `gh`, which is the case in the tag workflow, they are skipped with a named
  warning rather than silently, and a `gh` failure after a good probe blocks the release.

- A local, append-only decision log, `~/.local/state/agent-harness/decisions.jsonl`, beside the
  usage ledger: one record each time a hook makes a judgment, and a second record when the
  session settles it. `grade-bash` records the permission answer it gave a command and joins
  `ran` when the command's PostToolUse arrives, or `not_run` when the session ends without one —
  not "denied", because a refusal, an interrupt and a crash look the same from a hook. `stop-gate`
  records `blocked`, `released` or `skipped` with the gate's own result; `tier-agent-spawns`
  records the band an unnamed spawn was routed to, `brief-guard` what it wrote into the brief,
  and `evasion_deny` a refused re-spawn. A row holds the text the hook judged, capped at 2 KiB,
  with the hash taken over the uncapped text; it holds no tool output and no assistant prose.
  Nothing is exported, nothing is model-visible, and a write that fails is counted and swallowed
  rather than allowed to change a decision. `telemetry.decisions: false` turns it off entirely.
  Read it with `harness usage --by decision`, which prints counts, the outcomes seen per point
  and the unlabelled share. Band routing happens on Claude Code alone, so Codex writes no
  `tier-agent-spawns` row and its capabilities file names the gap.

- Subagent rows in the usage ledger now carry `budget_output_tokens` and `budget_tool_calls`,
  the soft budget the role they ran as carries, so an overrun is a subtraction on one row rather
  than a join against whatever the cost table says today. A role nothing prices records `null`,
  because a zero would say the spawn was budgeted nothing.

- Plan mode now investigates at the permission posture you selected instead of below it. Under
  `bypass` or `auto` in Claude Code, the PreToolUse coordinator approves the commands native plan
  mode prompts on — a script run, a `python3 -c`, a scratch redirect, a test run, anything graded
  0 or 1 — and asks about grade 2, because a push or a mutating API call is execution rather than
  planning. Grade 3, the confirm marker, `manual`, `inherit` and Codex are all unchanged, and a
  stricter autonomy stance still wins. A new config key, `plan_allow_tools`, lists tool-name globs
  (such as `mcp__notes__read_*`) approved in plan mode under the same posture gate; it is empty
  by default, because a hook payload carries no read-only hint for an MCP tool and nothing is
  inferred.

- `product.json` now holds the landing copy as validated data: a `hero` of title, subtitle and proof
  line, six `capabilities` groups of a pitch and three to six features each with a repository
  relative `doc` path, and an `on_the_way` list of at most five items, each naming an issue, a
  client the compatibility catalog calls `planned`, or a document. The hero title is the existing
  `headline`, which `github_description` already leads with, so the page, the README and the GitHub
  About description share one source. `tests/test_release.py` fails when a `doc` path is missing, a group
  or feature count leaves its range, a feature line runs past 170 characters, any string carries an
  em dash, or the README and the data disagree.

- Every usage row names the `harness_version` that wrote it, read from the same `VERSION` file
  `harness --version` prints, so a change in spend can be read against a release. A rescanned
  row carries `null` rather than today's version, and a role-run worker's row carries the
  version stamped into its `status.json` when the run started.

- A session row records the `effort` that covered the most output tokens and an `effort_source`
  naming where it was read: `transcript` for Claude Code, which writes `effort` on every
  assistant record, and `turn_context` for Codex. Effort changes mid-session — 14 of 112 Claude
  Code transcripts and 4 of 44 Codex rollouts on one machine — so the row weighs it by output
  rather than taking the first value seen, and records `null` when the transcript names none.

- A session row carries per-day slices in `days`: four token figures and a turn count per UTC
  date, cut from the same deduplicated map the row's totals are summed over and dropped whole
  if they do not add up to it. `harness usage --by day` sums the slices when a row has them and
  falls back to its end date when it does not, and `--days` then windows on the slice date, so
  a session that ran for a fortnight contributes only its in-window days instead of landing on
  the day it ended. Five such sessions were 68% of all output tokens on the machine measured.

- `harness usage --by stance --stance <dimension>` groups tokens by that dimension's variant.
  Rows with no recorded stance, and rows a rescan stamped, are counted under `(unknown)` rather
  than dropped. `--rules --by stance` keeps the hit report unchanged, and `--by stance` with
  neither is refused with a usage error rather than guessed at.

- `harness usage` reports dollars. `policy/prices.json` lists USD per million tokens for input,
  output, cache read and cache write per model id, each entry carrying the `as_of` date it was
  read and the provider pricing page it was read from; a model whose price could not be
  confirmed from a primary source is absent rather than guessed. Ids resolve by longest prefix
  after normalisation, so a dated, Bedrock-style or `[1m]` id reaches its family. Every token
  grouping gains a `usd` column and an `unpriced` footer, `--by role` gains p50 and p75 dollars,
  and a row with an unknown model, two models or partial tokens is unpriced rather than $0. A
  `prices` block in `config.json` merges over the file per model id. Checked against a recorded
  Claude Code session whose runtime reported `total_cost_usd = 0.60097775`: the table reproduces
  it exactly.

- A session row records a per-model token breakdown in `by_model`, checked against its own
  totals before it is written and dropped whole if it disagrees: cut from the deduplicated
  message map for Claude Code, and from the snapshot deltas under each `turn_context.model` for
  Codex. A row that carries one is priced model by model, which is the only way the sessions
  that switched model — the largest ones — can be priced at all. On a 60-day rescan, unpriced
  runs went from 26 of 143 to 0, and the share of output tokens that was unpriced from 73.7% to
  0%.

- A usage row records the cache-write tier split Claude Code reports — `cache_write_5m` and
  `cache_write_1h` beside the `cache_write` total — because Anthropic prices a 5-minute write at
  1.25x base input and a 1-hour write at 2x. The keys are additive, so an existing row stays
  readable and is charged whole at the 5-minute rate.

- `harness doctor` names the newest `as_of` in the price table and warns when it is over 90 days
  old, since prices go stale silently while the report keeps printing dollars.

- Ledger rows can be exported to any OTLP/HTTP endpoint, off by default. A `telemetry` block in
  `config.json` turns it on; with it off no network code runs and the hook behaves as before.
  Each row becomes one OTLP/JSON log record on `POST <endpoint>/v1/logs`, sent from the detached
  `SessionEnd` worker after the row is already in the ledger: one attempt, a two-second timeout,
  no retry, and a failure recorded in `usage.errors.jsonl` rather than in the session's exit
  status. The body is the row; the attributes are its flat scalars plus a stable
  `harness.row_key`, the harness version and one `harness.<dimension>` per recorded stance.

- Request headers are read from a named environment variable or a file outside every git work
  tree that no other user can read; a header value written into `config.json` is refused by
  name, and no value is ever printed, logged or written to an error record — a failure names
  the endpoint's scheme and host only.

- `harness usage export --since <date> [--until] [--dry-run]` replays a window of rows in
  batches, prints what was sent and what failed, and exits non-zero if any batch failed.
  Delivery is at-least-once, so [docs/telemetry.md](docs/telemetry.md) gives the
  de-duplication query on `harness.row_key` and states the model: the ledger is the record, a
  backend is a rebuildable copy, and replay is the recovery path when a backend's retention
  expires or it is rebuilt.

- `harness doctor` names the export mode, the endpoint's scheme and host, and the names — never
  the values — of the headers it resolved.

- A `/land` workflow picks up where `/build` stops. It verifies the required checks — and the
  issue-ownership check where a repository runs one — on the head that will actually merge,
  squash-merges with the remote branch deleted, fast-forwards the shared checkout, removes the
  managed worktree, deletes the local branch with `git branch -d`, audits for stale checkouts,
  then reads the repository's own release rule and either says no release is due or posts a
  release card for approval. It never forces a removal, never uses `git branch -D`, and stops on
  dirty or unmerged state with the reason; merging stays approval-gated and it never tags or
  deploys. Projections for both runtimes are generated from the shared source as usual.

- `"native": true` in the `telemetry` block makes `harness sync` turn on each runtime's **own**
  OpenTelemetry export to the same endpoint, off by default. Claude Code gets the telemetry
  switch, both OTLP exporters, `http/protobuf`, the endpoint and an `OTEL_RESOURCE_ATTRIBUTES`
  carrying `harness.version` and one `harness.<dimension>` per resolved stance, plus an
  `otelHeadersHelper` pointing at a harness-owned script that reads the configured header source
  at run time — so no header value is written into a settings file. Codex gets `[otel]` with
  both `exporter` and an explicit `metrics_exporter`, because its default metrics sink drops
  token, cost, tool and API metrics client-side; it is given no header, since `[otel]` takes a
  literal header map, and [docs/telemetry.md](docs/telemetry.md) states that gap rather than
  papering over it.

- Ownership is per variable: a variable you set in `env` is never read, changed or removed, a
  managed key already holding a value the harness did not write is reported and left alone, and
  turning the key off restores what each key held before. Labels are frozen at sync time, so a
  stance switched without a re-sync mislabels native data until the next one — the ledger row
  stays authoritative. `harness doctor` reports the state, the endpoint's host, whether the
  labels are current, and that both runtimes attach their own user and organization identifiers.

- [docs/telemetry.md](docs/telemetry.md) ends with a reference recipe for one backend that was
  set up and measured end to end — the ClickStack all-in-one image — as a worked example of "any
  OTLP/HTTP endpoint" rather than a requirement: the run command with its three persistent
  volumes, the two manual steps before a single record is accepted (create the first user, then
  send the ingestion key as a bare `authorization` header), the 30-day TTL every table ships
  with and the `ALTER TABLE … MODIFY TTL` that raises it, and why Codex cannot reach a backend
  that needs a header. It contains no command that creates an account, stores a password or
  removes a container or a volume, and it names the licence of every part of the image.

- A dashboard definition ships beside it as
  [docs/telemetry/clickstack-dashboard-native-cost.json](docs/telemetry/clickstack-dashboard-native-cost.json):
  ten tiles of this repository's own SQL over the standard OpenTelemetry tables, reading the
  native Claude Code cost and token metrics down to agent, model, effort and stance variant. The
  instance-specific connection id is a placeholder, with the one-line lookup beside it.

- `scripts/cost_bench.py replay` runs a pinned task set headlessly against two profiles — a
  signed-in, otherwise empty Claude Code profile and the installed harness — on one shared command
  line and a scrubbed environment, and scores each run with a check the agent never sees.
  `benchmarks/tasks.json` pins the tasks, `benchmarks/oracles/` holds the held-back checks, and
  `--verify-tasks` proves every check and refuses a fixture whose own gate is red or whose solving
  commit is still reachable. It calls a model and spends real usage, so it is run by hand on a
  release candidate and never in CI, and no result is published with this release: the runner
  ships, the number does not. Reading and limits: `docs/benchmarks.md`.

- `scripts/cost_bench.py static` measures what the harness adds to every Claude Code session against
  a bare one: files, lines, characters, an estimated token count and its price per model from
  `policy/prices.json`. `benchmarks/static.json` holds the figure for the last release and CI fails
  when the estimate grows more than 5% over it without an entry in `benchmarks/allow.json`. No model
  is called. Reading and limits: `docs/benchmarks.md`.

- An exported ledger row carries `harness.usd` and `harness.price_as_of`, so a dashboard reads
  dollars instead of re-implementing the price table in its own query language. The figure is a
  list-price API equivalent fixed at export time, computed by the code `harness usage` prices
  with: the rates, the id normalisation and the session/subagent join moved to
  `policy/hooks/pricing.py`, which the CLI and the standalone export hook each load rather than
  either one holding a second copy. A session row's figure already includes its subagents,
  exactly as the report totals them. An unpriced row carries neither attribute — never a zero —
  and a missing price file or a malformed `prices` override costs an export its dollars and
  nothing else.

- Qualification freezes on a release branch instead of on `main`. `compatibility/freeze.json` names
  the frozen commit and branch, and a new `harness freeze` reports drift between that commit and a
  given ref over the runtime source paths, exits non-zero on drift, and refuses a merge that would
  change those paths on the frozen branch. `docs/releasing.md` gains the freeze procedure and the
  triage rule that a round runs to completion and collects defects rather than fixing one mid-round,
  because any source-path change invalidates every evidence record for the round (#332).

- Two deterministic pre-qualification checks that spend no model turns. A documentation-link check
  resolves every relative path and heading anchor across the repository's Markdown, and a credential
  probe names the variable a client would authenticate with or fails immediately with the reason,
  instead of the 300-second hang a session-credentials-only home used to produce. Both are additive
  and neither writes to `compatibility/evidence/` or appears in a catalog record, because a green
  deterministic check is not native qualification (#401).

### Changed

- The credential probe's docstring records why its green line is a fixed string that never
  names the variable it found: a clean CodeQL scan with no dismissals was judged worth more than
  the name on stdout, and a caller that needs it calls `reachable` directly (#409).

- `telemetry.native` takes `true`, `false` or a list of runtime names, so a collector that
  authenticates can be fed natively from Claude Code while Codex, which takes header values only
  as literals in its configuration file, is left alone. `true` keeps its meaning, an unknown name
  stops `sync`, and `docs/telemetry.md` says which runtime can reach an authenticated endpoint (#405).

- The README's install command clones the `stable` branch, so a new install starts from the latest
  release instead of the development trunk.

- The README's first screen is the headline, a terminal capture of `harness sync --dry-run` on a
  fresh home, the description and the six capability groups, one linked line per feature, plus the
  "On the way" list. Release status and the generated compatibility block now follow the install
  section, so a first-time reader meets what the harness does before which clients are qualified.

- `harness usage --by role` marks a role with fewer than 30 runs `n<30` in a new `sample`
  column. A p90 over eight runs is the second-largest of eight, and the budget re-seeding
  procedure in `docs/usage.md` now says not to re-seed from a marked row.

- The fan-out warning in [docs/usage.md](docs/usage.md) now carries what was measured against
  it. Across 137 sessions on one machine the cache hit rate was 97.0%, 97.2%, 97.3% and 97.1%
  at 0, 1–6, 7–50 and 51-or-more subagents, so the falling hit rate it describes is a thing to
  check in your own data rather than an expectation.

### Fixed

- The test suite no longer writes the harness into a real profile. `claude_dir()` honours
  `CLAUDE_CONFIG_DIR`, so a suite run in a shell that had it set installed links and rendered
  files into whatever profile it named instead of the run's own temporary home. Every test now
  clears it through one shared isolation helper, and the sandbox fence that hid the leak is no
  longer what keeps a caller's profile intact.

- The usage feed says `spend unknown` once and names the agent it could not read, feeds a
  cumulative line for every round of an agent resumed with a follow-up message rather than only
  its first, and states once per session what its token figure measures so it cannot be read as
  the task notification's `subagent_tokens` (#306).
- `harness sync` installs the Claude Code output style from the `voice` stance instead of
  unconditionally: `scannable` installs `Scannable`, `answer-card` and `off` install none, a style
  you chose yourself survives, and a harness-installed style is removed when the variant changes.
  Ownership comes from the reconciliation journal rather than the style's name, so a style you
  picked before installing is left alone even when the harness ships one by that name. The Codex
  projection is unchanged; `scannable` was already the only variant it gave presentation material
  to, so the two runtimes now mean the same thing by each variant (#394).
- `harness usage` no longer prints the partial-totals warning above a USD report whose own
  footer already counts those runs as unpriced; a report with no unpriced run still warns that
  a column is short (#407).
- `.agent-harness/evidence/`, `task.json` and `sync.lock` are ignored repository-wide, so captured
  replay transcripts can never be staged or reach the lint, and the stop gate no longer goes red in
  a checkout whose own change is clean; a test pins the rules (#432).

- Three defects in the native acceptance runner, recorded during 0.11.0 qualification and none of
  them in the harness runtime. `orchestrator_text()` now reads a session's transcript whether or not
  a subagent directory was written, so a session that spawned none no longer returns an empty string
  and lets a `cost-posture` assertion pass vacuously. The runner's cited qualification runbook is
  written rather than a dead link. The three AWS session variables — the key id, the secret key and
  the session token — now reach the client, so a container holding only session credentials can
  authenticate. The bypass classification a `permission-controls` driver needs — telling a model's
  own refusal apart from a policy block by reading the recorded denials and the permission mode —
  is added and tested, but no such driver exists in the runner, so nothing calls it yet (#309).
- `harness worktree remove` finishes the cleanup after a squash merge. The quality gate has to run
  before a push and writes `__pycache__`, which used to leave the worktree unremovable and reported
  as dirty by `harness worktree audit`; removal no longer counts the regenerable caches it knows
  (`__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `node_modules`, `.astro`, plus a
  top-level directory named with `--also-clear NAME`) as work, git deletes them with the checkout,
  and audit reports a checkout holding only those as clean. Modified,
  untracked and other ignored entries still refuse, naming the first few. With `--merged` the
  local branch is deleted too, but only once `gh` reports a merged pull request whose head commit
  is the branch tip: after a squash the branch's commits never reach the default branch, so
  ancestry cannot answer and `git branch -d` refuses work that did land. A missing or failing
  `gh`, no merged pull request from this repository, a tip the merged head does not match, or the
  default branch refuses with the reason and deletes nothing. `/land` now hands both steps to one command.

- The documented de-duplication query runs as written. Every exported record now carries
  `harness.exported_at`, the export time as a fixed-width RFC 3339 UTC string, and the example in
  `docs/telemetry.md` orders on it instead of `ObservedTimestamp` — a column the OpenTelemetry
  ClickHouse exporter's `otel_logs` table does not have, since the observed time is dropped on
  ingest and `Timestamp` is the row's own end time, identical across replays. Attributes land in
  that table as a `Map(String, String)`, so the stamp is fixed width for lexical order to equal
  time order, and the example casts `harness.usd` with `toFloat64OrNull` before summing it. A
  test checks the example names no column outside the real schema.

- Each stance reaches a backend once, as `harness.<dimension>`. The exported body no longer
  carries the `stances` map, which a backend that parses a JSON body flattened into a second
  dotted copy of every stance beside the attributes; every other field still travels in the body.

- A client launched under a substituted `HOME` no longer raises the macOS "A keychain cannot be
  found" dialog. The acceptance runner already gave its disposable homes a keychain, but two other
  launches did not: every role worker runs its client in a private home that had none, and
  `harness doctor` ran `claude doctor` in whatever `HOME` it was given, including a throwaway one
  an agent built to test a config. A role worker's home now carries its own throwaway keychain, and
  a worker whose keychain cannot be created fails instead of launching; `harness doctor` skips the
  client's doctor, and says so, when `HOME` has no default keychain. `harness keychain <home>` is the
  same guard for a home you build by hand. Other hosts are unchanged.

- The `gatherer` role no longer declares web tools its only execution path cannot give it. The
  role listed `WebFetch` and `WebSearch`, the spawn guard refuses a native `gatherer` in favour of
  `harness role run gatherer`, and that isolated worker is launched with `Read`, `Grep` and `Glob`
  under a read-only sandbox with hosted search disabled — so a web dimension of `/research` had
  nowhere to run. The confinement stays: a worker that can both read a workspace and fetch can
  carry what it read back out, and a fetched page is untrusted input inside a confined process.
  Instead the declaration now matches the launch, the role says it is offline and that online
  evidence arrives as files granted with `--read-dir`, the refusal that points at `harness role
  run` adds where a web dimension goes instead, and `/research` routes by where the evidence
  lives — files and repositories to the isolated worker, the live web to an in-session band
  worker. A test asserts the command line a `gatherer` worker is actually launched with.

- `transcript-hygiene/brief-without-cap` is now `transcript-hygiene/model-wrote-no-cap`, because
  that is what it always measured. A transcript records an `Agent` call as the model wrote it,
  and a `PreToolUse` hook's `updatedInput` is written to a separate `attachment` line the scan
  never reads — so `brief-guard` capping a brief could not move the number, and the rate was
  unchanged before, during and after the hook shipped. The detector's behaviour is unchanged and
  the rename makes `promote?` on it mean something: the orchestrator writes no bounds and the hook
  is carrying the rule. `rule-detectors.RENAMED` names the successor and `usage --rules` folds it
  as it reads — by rule, by repo and by stance — so a row written under the old id reports under
  the new one, with no rewrite of the ledger file and no split in the series.

- A Codex subagent thread is recorded as a `kind: "subagent"` row joined to the thread that
  spawned it, with its depth, nickname, model, effort and tool-call count, instead of as a
  session of its own. On the machine this was measured on, 307 of 438 rollouts were subagent
  threads and every one of them was a session row. A thread that inherited its parent's history
  writes the parent's `session_meta` further down its own file; only the first one is read now,
  which is what 36 of those 307 turned on. `harness usage --rescan` migrates the misclassified
  rows, deleting the stale keys and copying the ledger to `usage.jsonl.bak` first.

- The rescan reads `~/.codex/archived_sessions/` as well as `~/.codex/sessions/`. Codex moves a
  rollout there unchanged, and 96 of the 131 top-level rollouts on that machine lived only in the
  archive, so most Codex sessions never reached the ledger at all. Codex capture is rescan-driven:
  whether the runtime's `SessionEnd` payload names the rollout file is not established, so the
  hook now accepts `rollout_path` and `session_path` beside `transcript_path` and `docs/usage.md`
  says the rescan is the path known to work.

- A Codex session whose snapshot carries `total_tokens` alone — 85 of 107 top-level Codex Desktop
  rollouts — is recorded as `partial` with a `total` and unknown typed fields, rather than summed
  as a session that spent nothing.

- `harness usage` sums Codex subagent rows and still skips Claude Code ones: a Codex thread's
  total counts that thread alone. Four of the 21 measurable parent threads report fewer tokens
  than their own children sum to, which a total including them could not do. `--by model` also
  reads the single `model` a subagent or worker row names instead of grouping it as unknown.

## [0.11.1] — 2026-09-21

### Added

- A `stable` branch that always points at the latest release. The release workflow fast-forwards
  it to the tag's commit after publishing, `scripts/advance_stable.py --check` verifies it, and the
  branch never moves backward. `main` stays the trunk.

### Changed

- The `builder` role's report closes two gaps a downstream soak found. A hand-edited fixture,
  golden file or pinned value must now name the generator or the command that produced it —
  "hand-typed, copied from run X" answers it, silence does not — and where a generator exists the
  builder regenerates instead of hand-editing. The gate's result is read from the test command's
  own exit status, captured with `PIPESTATUS`, `pipestatus` or no pipe, rather than from whatever
  `tail` returned. The fixed report gains one item for the edited fixtures and what produced them.
- Qualify the Claude Code and Codex CLIs on macOS and Linux for this source with version-pinned
  native evidence across all eleven acceptance cases, and record the limitations those runs
  established in the compatibility catalog.

### Fixed

- The Review Card's diagram is a plain-text drawing in a `text` fence. The `plan-authoring` skill,
  its template and example, and the `planner` role defaulted to a mermaid `flowchart`, which the
  plan-mode pane and the chat sidebar show as raw source — so the card's one diagram was unreadable
  where the card is reviewed. New or changed nodes carry a `*`; mermaid stays for the addendum and
  for docs read on GitHub.
- The `delegation: off` stance said a hook asks before any spawn, where the lifecycle denies the
  spawn outright and never reaches that hook. The stance now says a spawn under it is denied and
  that changing the selected stance is the way to delegate, and it no longer names a hook — the
  name it used, `tier-spawns`, was not the file doing the refusing either.
- The spawn guard no longer refuses a constrained role only by the name a spawn chose. Refusing a
  native `reviewer` spawn moved the work rather than stopping it: the client re-issued the same
  brief as an unnamed subagent and it ran unconfined. A refusal is now remembered for the session,
  and a later spawn that names no constrained role but carries the same brief — normalised, or a
  near-identical rewording — is refused with the same `harness role run` instruction and told that
  dropping the role name changed nothing. Independently, a brief whose own first line reads
  `harness-role: <role>` may only run as that role's isolated worker, whatever `subagent_type` the
  spawn names or omits. The BMad review layers now carry that line. Session state that cannot be
  read or written leaves the guard exactly as it was, and `delegation: off` is untouched.

## [0.11.0] — 2026-09-21

### Added

- Add the `designer` role: one pass of visual design work toward a locked target, validated and
  captured, never self-scored. It declares the `frontier` class, which is the only way a spawn
  reaches the strongest model now that the hook refuses it by request. The design loop hands it
  the build and fix steps; `design-judge` still scores from a fresh context.
- A usage feed tells the orchestrator what it is spending while the session runs: a turn line on
  `UserPromptSubmit`, a line for each subagent as it returns, a line at the next prompt for every
  background spawn that finished meanwhile, and a note when more agents are running than the
  variant's `max_parallel`. A subagent's figure is summed from its own transcript, because a tool
  response reports only that agent's last response. The behaviour is `turn_feed`, `nudge_at` and
  `max_parallel` in the active `cost` variant; `off` injects nothing and writes nothing. Per-session
  state is an append-only journal plus a `flock`-guarded reader file, because these hooks run
  concurrently and nothing slow runs under that lock; a figure that could not be summed inside
  the hook's budget is reported as `(partial)` or `spend unknown`, never as zero. Stale files are
  swept after a fortnight and `harness uninstall` removes them.
  Codex raises none of the three events and declares the feed uncovered.
- `harness tiers check` compares the Codex class table with the model catalog Codex fetches from
  its provider, offline, and fails on a mapped model that is gone, superseded or ranked out of
  order. A missing catalog reports *unverified*, not a pass.
- `tiers.<runtime>.<class>` in the configuration remaps a capability class for every role that
  names it, without a harness release.
- The usage log records one row per subagent and one per `harness role run` worker beside the
  session row, each naming its `kind`, agent type, model, effort, token counts, tool calls and
  spawn depth. Rows are upserted by `(session_id, runtime, kind, agent_id)` and hold counts
  only: no prompt text and no command text.
- Each `cost` variant carries a JSON sidecar beside its `.md` holding its switches and its
  model class, effort and soft budgets per role and per band. A variant resolves over its
  `extends` chain, a variant with no sidecar resolves to `balanced`'s, and an unknown key is a
  warning rather than an error so a later release cannot break a variant you wrote.
- `posture: fixed` in a role's frontmatter, set on `reviewer`, `spec-reviewer`, `design-judge`
  and `log-compressor`: a cost variant may budget the role but never change its class or effort.
- `harness stances --json` carries the resolved cost table — switches, rows with base and scaled
  budgets, default band, the `extends` chain with each sidecar's path, and warnings. Lint
  validates shipped sidecars against the schema and against their own prose.
- Three band worker roles, `worker-a`, `worker-b` and `worker-c`, carrying the A/B/C bands'
  class and effort into a native spawn. Their descriptions hold the band rule, so an
  orchestrator chooses a band by spawning one of them by name. They name no tool list, so a
  rerouted spawn keeps every tool it had as `general-purpose`, MCP tools included; an adapter
  role entry may now carry `disallowed_tools` instead, which is how they give back the one
  tool a role with `delegation: none` must not hold.
- `harness usage --by role` reports, per agent type, the number of runs and the p50, p75 and p90
  of output tokens and of tool calls over the window — the distribution a per-role budget has to
  be set against. A run whose runtime reported no counts is named, never averaged in as a zero.
- Every brief states the spend the cost variant expects of it: the row's output tokens and tool
  calls in one sentence, soft — finish if close, otherwise return what you have — because a
  subagent cannot see the variant that priced it. A spawn that named a role is priced by that
  role and one that named none by the band worker it is about to be routed to, computed by the
  same function that routes it — and only on a runtime that reroutes, so a spawn Codex will run
  as written is priced by its role or by nothing. A brief that already prices itself, an
  unbudgeted role and a table that will not build are all left exactly as before.

### Changed

- Claude Code's native agent definitions follow the resolved cost variant. A role the selected
  posture does not move keeps its symlink to the committed projection, so a default install is
  exactly what earlier releases wrote; a role it does move is rendered and written as a managed
  file, which is how a variant's class and effort finally reach a native agent. The class its row
  names resolves through the adapter's `tiers` table. Precedence is the role's own tier and the
  adapter's effort, then the variant's row, then `role_bindings.<runtime>.<role>`, which still
  wins; a `posture: fixed` role takes neither cell. Effort and model are therefore sync-scoped: a
  session `HARNESS_STANCE_COST` does not move them until the next sync. Roles move between link
  and file in both directions as the posture changes, and a definition you edited or a link you
  redirected is preserved and reported, never replaced. `sync --dry-run` names each role whose
  rendering has moved, with its class, model and effort.
- Stances resolve in one place for the dispatcher and every policy hook alike: built-in defaults,
  the user configuration, the file `HARNESS_PROJECT_CONFIG` names, then `HARNESS_STANCE_*`. The
  hooks therefore honour `HARNESS_HOME` and a project configuration, which they ignored before,
  so a disposable home or a per-repository selection now reaches the spawn, brief, grading and
  usage hooks rather than only the CLI; both are environment variables the user sets, at the
  same trust level as the `HARNESS_STANCE_*` the hooks already honoured. The grading hook is
  the exception that fails closed: a stance it cannot resolve is graded under the strictest
  variant, named as unresolved in the prompt.
- A spawn that names no agent definition, or names `general-purpose`, is rewritten to the cost
  variant's default band worker and runs on that band's class — the only way the posture's
  effort reaches it, because the `Agent` tool has no effort input. A model the caller named is
  kept, a request for the top class is refused and the band's class applies in its place, and a
  variant with no `default_band`, an unreadable table, a machine whose worker definitions are not
  installed and a session whose agent registry predates them all leave the spawn exactly as the
  previous release did. A repository that ships its own `.claude/agents/worker-<band>.md` is never
  routed to, because a project definition outranks the user's. The cost table is read only for a
  spawn that named nothing, so naming a role costs nothing.
- Subagent usage rows carry `requested_type` and set `rerouted` when the type the parent
  recorded differs from the one the subagent ran as, joined on the tool use id. The reroute is
  measured from the transcript rather than reported by the hook that made it.
- The spawn hook reads its model ladder from the adapter's `bindings.json` `tiers` table instead
  of a list written in the hook; a table it cannot read leaves the spawn as written and says so.
- The spawn hook says so when the session's model is not on its ladder, instead of leaving the
  subagent on the session model without a word.
- Shared roles name a provider-neutral capability class (`tier:` — `frontier`, `strong`, `standard`,
  `light`) and each adapter's `bindings.json` maps classes to native models in a `tiers` table.
  `reviewer` and `planner` run on `strong`, `spec-reviewer` on `standard` and `design-judge` on
  `frontier` instead of inheriting the session model, so their cost no longer follows whatever
  the session happens to run. An unmapped class resolves upward or inherits, never downward.
- Codex roles gain model tiering: its table maps the four classes to `gpt-6-astra`, `gpt-5.6-sol`,
  `gpt-5.6-terra` and `gpt-5.6-luna`. Codex roles previously inherited the session model.
- The spawn hook tiers a planning-framework repository like any other, and refuses the top class
  by request: an unnamed spawn asking for it runs on the band it is routed to, or one class below
  the session where nothing routes it, and a named agent falls back to its definition. The
  `session-model` stance is unchanged and remains the opt-out.
- `harness role run`, the constrained-role refusal message and the BMad override templates no
  longer tell the caller to pass the parent session's model; they name it only where the adapter
  maps none. Role effort above `high` is rejected.
- Native qualification now requires an eleventh case, `cost-posture`, so a client cannot be
  qualified without the cost posture layer having run natively: the roles a variant moves and only
  those, an unnamed spawn routed to the default band worker at its row's model and effort, the
  budget sentence in its brief, the feed and `harness usage` rows against that budget, a session
  that predates the workers left alone, and the priced-nothing variant doing none of it. Evidence
  is scoped to the harness version it records, so 0.9.0 and 0.10.0 records stay valid history.
- Qualify the Claude Code and Codex CLIs on macOS and Linux for this source with version-pinned
  native evidence across all eleven acceptance cases, and record the limitations those runs
  established in the compatibility catalog.

### Fixed

- Nearly every spawn raised a permission-hook notice about a rewrite that is the ordinary case:
  the budget sentence `brief-guard` appends now carries no notice at all, and the line naming the
  band an unnamed spawn is routed to is said once a session. The return-bound notice, the refusal
  of the strongest class by request and the repository-supplied-worker refusal are unchanged,
  because each reports something the caller asked for being changed or refused.
- An unnamed spawn was rerouted to a band worker whenever the definition existed on disk, which
  failed every such spawn in a session that was already running when `harness sync` installed the
  workers: the tool loads its agent registry once, at process start, and rejected the type. The
  SessionStart policy now records what each session's registry held, and a reroute requires the
  worker to be in that record; without one the spawn keeps the previous one-rung behaviour and the
  session is told once to start a new one. Only a session's own start may widen that record: a
  resume narrows it to what is still on disk and creates none, and any failure to answer the
  question at all leaves the spawn unrouted rather than raising into a refusal. Records are
  private to their owner, refreshed while a session is in use, swept after a fortnight, and
  removed by `harness uninstall`.
- A session whose transcript carried both a subagent's sidechain lines and that subagent's own
  file counted every delegated token twice. The session's totals are now taken over one map of
  message ids that both reads fill, so a message recorded in two places is one message. Agents
  nested under `subagents/workflows/wf_<id>/`, which the Workflow tool writes and a flat walk
  missed entirely, are counted too and name their workflow.
- Output tokens were undercounted, by a factor of several on a long response. The log took each
  message id's usage from the first transcript record carrying it, and the early records of one
  streamed response carry a partial `output_tokens` — 7,126 against the response's real 40,868
  on a measured subagent transcript. Each message id now counts at the largest figure it ever
  reported, so a reordered or truncated tail cannot lower it either. `harness usage --rescan`
  corrects the recorded history.
- Every session total was short by whatever its delegation cost. A subagent's tokens live in its
  own transcript, which the log never read, so a session that fanned out reported only the
  orchestrator's own spend. Session totals now include their subagents'; `harness usage --rescan`
  backfills the history, and the token groupings sum session rows alone so nothing is counted
  twice.
- A managed link that reaches its file through an alias of the checkout, such as `claude/stances`
  for `primitives/stances`, is no longer reported as redirected. `harness uninstall` and the
  retirement of a removed link treated the same link as the user's and left it behind; they now
  remove it. A link pointed at a different file is still reported and still preserved.
- A named agent asked onto the top class now gets the model its definition names, or the class
  below when there is none to read. The hook used to remove the request, and the lifecycle
  coordinator only carries rewrites, so the request reached the spawn unchanged.
- The lifecycle coordinator relays a hook's notice on Claude Code instead of dropping it, so a
  tiered spawn and a session model the ladder does not know are both reported.
- A role worker whose runner died now reports as `orphaned` instead of `running` forever. The
  status record kept nothing that could tell a live run from an abandoned one, so a killed session
  left `status: running` with no result and no error, and `harness role status` could not separate
  it from work in flight. A run now records the pid supervising it and that process's start time,
  and status reports a worker whose process is gone with no result written as the terminal
  `orphaned`, writing that state back into `status.json` alone. The start time guards a recycled
  pid; a record from a release that stored no pid, or a platform that will not report a start
  time, still reads as `running`.
- `harness uninstall` now removes the empty directories the sync created for its own files —
  `~/.claude/rules/harness-stances` and each `~/.agents/skills/harness-*` — instead of leaving
  them behind. A directory that still holds anything is kept untouched.
- A stale `harness task save --revision` prints one line on stderr naming the remedy and exits 1,
  where it raised an uncaught `ValueError` and printed a traceback carrying the checkout path.
  The guard itself is unchanged: a save against a revision that is no longer current is refused.
- `harness doctor` reports a client that is `not on PATH` rather than `not installed`, and says so
  explicitly when Codex credentials are present with no `codex` the shell can reach.
- A permitted Codex tool call no longer reports `hook: PreToolUse Failed`. The PreToolUse envelope
  carried `permissionDecision: "allow"` on every non-gated call; a client that lists `allow` as
  unsupported discards the whole hook output, so every allowed call showed a failure and a real one
  was indistinguishable. Codex now hears nothing where its own default already allows, and `allow`
  is sent only with an `updatedInput` rewrite, which that runtime applies under no other decision.
  Denials, the ask-to-deny narrowing and Claude Code's envelope are unchanged.
- An isolated role worker now follows the selected cost variant. `harness role run` bound a role
  from its `tier:` alone, so under `frugal` a `gatherer` worker ran on the role's own class while
  the same sync in the same home rendered that role one class lower — and the constrained roles
  are denied as native spawns, so neither the posture nor the soft budget ever reached the roles
  that carry measured budgets. A worker now resolves its row through the same function and the
  same precedence the sync path renders a definition with — role defaults, the variant's row
  (class only under a tiered `delegation`, never for a `posture: fixed` role), `role_bindings`,
  then `--model` — on both runtimes and through the whole stance ladder, so a session-scoped
  `HARNESS_STANCE_COST` reaches it. Its brief ends with the same `Expected spend` sentence a
  native brief gets, from one function shared with the brief guard, unless the row prices nothing
  or the brief already states a budget; `status.json` records the variant, the resolved class,
  where model and effort each came from, and the figures appended. A variant with no row for the
  role, or a table that will not build, leaves the worker exactly as it was.

- The `auto` permission posture now gives Codex the automatic approval review it promises. Sync
  wrote `approval_reviewer`, and Codex names the field `approvals_reviewer`: codex-cli
  0.154.0-alpha.6.2, 0.155.0-alpha.9, 0.155.1 and 0.156.0-alpha.9 all reject the old spelling
  under `--strict-config` and drop it in silence otherwise, so the posture resolved in the client
  as review by the user with no warning. Sync now asks the installed client which name it accepts
  — from its own emitted protocol schema, or a `--strict-config` probe in a throwaway
  configuration home, neither of which starts a model turn — writes that one, and takes the stale
  spelling back out. Both spellings are harness-owned, so a key the harness wrote is removed or
  restored on re-sync and uninstall while a key of the same name that you set yourself is left
  alone. With no client installed, the name the newest supported version accepts is written; a
  client that accepts neither gets no reviewer key, a sync notice and a `harness doctor` finding.

- The usage feed reports a finished subagent's actual spend instead of `spend unknown`. A
  `SubagentStop` summed the agent's transcript the instant it fired, and at that instant the
  transcript can hold only the `user` and `attachment` records the parent wrote into it — so the
  stop was journalled with null totals and the reporter printed them, while replaying the same
  payload a moment later yielded 297. A stop that carries no figure, or one read out of a
  response still being written, is now summed again on the line that names it: before the lock,
  with the bounded settle wait, inside one wall-clock budget shared by every agent that event
  reports. `spend unknown` now means a transcript that is not there; a transcript that is there
  and holds no response yet says `spend not yet recorded`, and the figure it gains later raises
  the session totals without the agent being named a second time.
- The usage feed's line for a synchronous subagent return no longer stops short of that agent's
  last response. Claude Code writes one API response as several records, and the return could
  fire between a partial streaming count and the record that ends the response — 143 output
  tokens reported live for an agent a later scan put at 278. The return now waits a bounded
  moment (at most a second, over the transcript's tail) for the response to end, says `(so far)`
  when it never does, and raises the session totals from the settled figure the journal brings
  afterwards without naming the agent a second time.
- A usage row names a subagent's model one way. A routed spawn's row carried the alias the spawn
  hook asked for and a directly spawned agent's the full id its transcript records, so one model
  appeared under two names. A subagent row now records what its own transcript reports — the most
  frequent model across its assistant records — and falls back to the requested alias only when
  it recorded none; `harness usage --rescan` normalises rows already on file. Worker rows still
  record what the worker reported, which is the only thing that knows.
- The native acceptance runner no longer raises a macOS keychain dialog on every client turn.
  macOS resolves the default keychain under `HOME`, a disposable home had none, and a client that
  stores an item then prompts "A keychain cannot be found" — once per launch across a whole
  matrix, with a destructive **Reset To Defaults** button. Each disposable home now carries its
  own throwaway keychain at the default path, so the store succeeds silently and never touches
  the operator's login keychain; a home whose keychain cannot be created reports the case
  `unverified` instead of launching a client. Other hosts are unchanged. The test suite raised
  the same dialog twice a run: two reviewer-key tests called `doctor` in a temporary home without
  hiding the installed client, so the real `claude doctor` ran there. They now hide it, and a
  tripwire test fails if a doctor call in a temporary home ever launches it again (#282).

### Migration

- A `role_bindings` override of `model` still wins over the class, so existing overrides keep
  working. A fork that added a role gives it a `tier:` line.
- A Codex install on a provider without these model ids sets `model` to `inherit` for each role
  under `role_bindings.codex`, which restores the previous behavior.
- Re-run `harness bmad apply <framework-root>` to pick up the revised templates.

## [0.10.0] — 2026-09-19

### Added

- Add the shared architecture-viewer profile, lifecycle and external process adapter. The preview
  works with a separately installed protocol 1 viewer and does not bundle or publish that viewer.
- Add bidirectional BMad-to-GitHub issue traceability, deterministic mapping audits and safe
  fallbacks for repositories that cannot project every native issue type or hierarchy.
- Add a versioned compatibility and migration contract, lifecycle acceptance runner and immutable
  release-source pinning.

### Changed

- Qualify the Claude Code and Codex CLIs on macOS and Linux with version-pinned native evidence.
  Keep the VS Code surfaces and Codex Desktop as unqualified previews; keep Cursor and Grok planned.
- Preserve released qualification evidence until runtime source changes, then fail the release
  check rather than silently changing a published support claim.

## [0.9.0] — 2026-09-19

### Provider-agnostic harness

- Lead with your working style, extensible custom primitives and switchable personal stances.
- Keep one shared authority for rules, stances, skills, roles, workflows and presentation;
  project it through Claude Code and Codex adapters.
- Add safe native configuration ownership, structural TOML updates, recovery journals, custom
  homes, drift diagnostics and conflict-preserving uninstall.
- Compose lifecycle policies, strengthen gate invalidation, and normalize usage observations
  while preserving unknown metrics and detector failures.
- Add custom stance authoring, semantic BMad roles and versioned bidirectional task handoffs.
- Add a native compatibility catalog and release gate. Claude Code and Codex are qualified on the
  required CLI, VS Code and desktop surfaces; Cursor and Grok, hosted agents, native memory merging
  and the UML viewer are deferred.
- Coordinate reference-site and personal-site positioning around the same user-aligned primitive
  model and immutable release identity.

### Changed

- Require a dedicated delivery issue for every PR, with a CI ownership check rejecting missing,
  multiple, foreign and reused closing issues. Document replacement PRs and the cross-PR race limit.

- User-authored drafts now defer to an untracked personal voice profile before applying the
  selected reply-layout stance, and the harness documents where that profile belongs. Accidental
  typos are explicitly excluded from imitation.

## [0.8.0] — 2026-09-17

### Added

- `docs/getting-started.md`: zero to a first useful session for someone who has not used a coding
  agent before. What the harness is and is not, the prerequisites as a table with a way to check
  each, install, `init`, a first session with three things to actually type, what changed about the
  answers, the five commands with which of them need a code project, what a session costs, and the
  three commands that diagnose a broken install. The README links it above the install block and
  from the docs list; `SUPPORT.md` now opens with it and says plainly that everything else there
  needs a GitHub account and is public. (#82)

- A **Before you start** block on the README's first screen: a Claude account on a plan that
  includes Claude Code — stated, rather than discovered after `install` has already run — the
  supported platforms, `git` and Python 3.9, and which of the rest are optional. (#82)

- A **What a session costs** section in `docs/preferences.md`, naming the rate-limit window, the
  commands that fan out, and the `cost` stance as the dial. Nothing previously said that a
  `/research` run costs several times a plain turn. (#82)

- `identity.expertise`, `expert` or `beginner`, selecting one paragraph of the personal file. The
  line telling the agent to communicate at expert level and skip fundamentals was hardcoded in
  `CLAUDE.personal.template.md` and reached every user, including one who had never written code —
  instructing the agent to withhold exactly the explanation they needed. `expert` is the default,
  so a config predating the field resolves to the behaviour it had. (#83)

- Stance presets, asked by `init`: `software` is the shipped defaults, `general` turns off the
  commit, test, licensing and build-vs-buy ceremony and lightens plans, for work that is not
  shipping software. Escaping the professional-SDLC defaults previously meant discovering five
  separate opt-outs. A preset only supplies the defaults for the questions that follow, so every
  stance is still asked. `delegation`, `autonomy` and `cost` are untouched by either: how work is
  spread, how far it runs unattended and what it costs are the same questions whatever the work
  is. (#83)

- A `voice` stance dimension, closing the last always-loaded rule that was a pure preference (#68).
  `scannable` defers to the output style as before and is the default, so nothing changes for an
  existing install. `answer-card` is for reading on a phone: the answer in the first line, then why,
  the catch, and the alternatives, about 150 words, no tables, with the reasoning left in the file
  it links rather than re-argued in the message. `off` imposes no shape at all. The two `voice/`
  detectors are gated on the dimension, so a voice nobody selected is not measured as a violation.

### Changed

- The three rules written entirely about code work say so in their headings — `verification`,
  `secrets` and `conciseness` — and one line of the always-loaded preamble states that a rule
  about repositories, tests or pull requests does not apply elsewhere. Rules link as a directory
  rather than per file, so they cannot be deselected; the fix is for them to read as inapplicable
  instead of as instructions about work the reader is not doing. Length-neutral apart from that
  one line. (#83)

- `voice-and-format.md` drops from thirteen lines to six, keeping only what no variant changes:
  a subagent inherits no voice, so its brief has to carry the output shape itself. Always-loaded
  context moves 193 to 195 of the 196-line budget.

- A `decisions/no-alternatives` detector, and `decisions-and-plans` leaves `OPT_OUT`. The rule
  asks for "the alternatives with their honest case", and nothing measured whether a decision
  block carried one: the opt-out reasoned from the Review Card and the chooser, and left the
  clause that does the work unobserved. The detector fires on a final message whose batched
  `Decisions` block or recommendation line names no other course, reading the markers out of the
  raw text so an alternative named inside a quote still counts. A recommendation in running prose is not a
  decision block and does not fire. (#78)

- `harness init`: a first-run wizard that writes `config.json` by asking for identity one field
  at a time and offering each stance's variants with the default in brackets, so configuring the
  harness no longer means hand-editing JSON. It detects the timezone from `/etc/localtime` and the
  GitHub handle from `gh` when it is logged in, refuses to clobber an existing config without
  `--force`, and names `config set` when there is no terminal to ask in. (#81)

- `harness config set KEY VALUE`, which validates before it writes: an unknown stance variant,
  stance, identity field, permission posture or top-level key is refused with the options named,
  rather than being written and failing at the next sync. (#81)

- `sync` and `doctor` report identity fields still carrying the example file's value. `load_config`
  backfills from `config.example.json` and `render_personal` writes the result into always-loaded
  context, so an unedited config had the agent address you as the placeholder name with nothing on
  screen to say so. Reported, never fatal: a dry-run sync against the example config still
  succeeds. (#81)

### Fixed

- `install` and `sync` refuse to run on Windows and name WSL2, rather than half-working: the
  harness links into `~/.claude` with symlinks and every hook is a POSIX command. Nothing in the
  README or the docs had ever said which platforms are supported. `_run` also treats an absolute
  path that does not exist as a missing tool, so `/bin/bash` being absent reports rather than
  raises. (#82)

- `doctor` now reports whether each registered hook can actually run, instead of printing an
  executable bit that never mattered. It resolves the interpreter and the script path of every
  hook command in the live settings and names what is missing. Claude Code treats a hook that
  fails to start as non-blocking, so a machine without `python3` on PATH, or an install that was
  never synced, turned all eleven hooks into silent no-ops — the command grader that asks before
  something irreversible and the stop gate that runs the repository's checks among them. Both
  guards disappeared with nothing on screen to say so. (#85)

- The two hook messages a user actually sees are written for a reader now. The grade-bash denial
  said "No prompt exists in this mode" and told them to re-run with a marker; it now says the
  command was refused because nothing can prompt, and what to say before running it again. Every
  grade carries its meaning in words — "this cannot be undone" — alongside the label. The stop
  gate says where the failing command came from, and the untrusted-folder notice names the command
  that fixes it. (#85)

- The four commands that assume a git repository now check for one before they start, and name a
  fallback instead of stopping dead. `/build` says up front that it needs a repository, a remote
  and `gh`, offers to make the change in place when there is no repository, and stops at the local
  commit when there is no remote — rather than failing at `gh pr create` with the work already
  done. `/review` says there is no diff to review outside a repository, before it spawns either
  pass, and offers named files or a pasted patch. `/plan` and `/handoff` write beside the work in
  the current directory when there is no repository root, and `/plan` now says outright that it
  needs neither a repository nor code. (#84)

- `bin/harness install` no longer ends in a traceback on a machine that lacks `gh` or `npm`.
  `subprocess.run` raises `FileNotFoundError` when argv[0] does not exist and `check=False`
  suppresses only a non-zero exit, so the unguarded `gh auth status` at the end of every install
  and the `npm install -g @openai/codex` step both crashed rather than reported — reachable with
  `--no-brew` on macOS and on every Linux run. Every external call now goes through one helper
  that resolves the executable first, names it when it is missing, and carries on. The lookup also
  searches the keg-only `node@22` bin directory, which Homebrew does not link into its prefix, so
  `npm` is found after a plain `brew install` on a machine with no other node. (#80)

## [0.7.0] — 2026-09-17

### Added

- A `code-quality-instruments` skill: branch coverage over line coverage, mutation score as the
  only instrument here that measures assertions rather than execution, complexity joined to
  coverage to rank the risky functions, and duplication as a refactor signal that is never a gate.
  Carries per-language instruments for Python, TypeScript, Rust and Go, and the operating rules
  that keep them usable: mutate the diff rather than the tree, run one instrument at a time, bound
  the workers. The `testing: required` variant gains a one-line pointer, which is the whole
  always-loaded cost. Adapted from `unclebob/swarm-forge`, whose engineering article pins real
  instruments per language where our stance only asked that tests exist. (#73)

- An eleventh hook, `brief-guard`: PreToolUse on `Agent`, it appends a 400-word return bound to
  a subagent brief that states none, rather than asking the orchestrator to write one.
  `transcript-hygiene/brief-without-cap` fired 536 times across 30 percent of sessions, so the
  prose was not working. What counts as a bound and which agents are exempt are imported from
  `rule-detectors.py` rather than copied, and a test asserts the appended text satisfies the
  detector, since a bound the detector cannot see would never move the number. Adapted from
  `unclebob/swarm-forge`, whose handoff helper fills the commit SHA so the agent never types
  one. No-op under `delegation: off`, where `tier-agent-spawns` already gates the spawn. (#72)

- A `commit-msg` hook in `templates/repo/hooks/`, installed per repository, refusing a subject
  that is not a Conventional Commit. It reads the `commits` stance and does nothing under `off`
  or `as-you-go`; under `conventional-attributed` it also warns on a missing `Co-Authored-By:`
  trailer, which it cannot add because the model name is session state a git hook cannot see.
  Adapted from `unclebob/swarm-forge`, which appends its byline the same way. A git hook was the
  right surface because it sees the final message however it was written, where the
  `commits/non-conventional` detector parses only `-m` and undercounts. (#71)

## [0.6.1] — 2026-09-17

### Fixed

- The instruction to gather with subagents unasked no longer loads under every stance. It sat in
  `claude/CLAUDE.md` and `claude/rules/delegation.md`, both always-loaded, and contradicted the
  `delegation: off` variant outright, so the selection did not decide the behaviour it named. It
  now lives in the `tiered` and `session-model` variants that mean it, and a regression test
  asserts that no always-loaded file carries one. Always-loaded context drops from 195 to 192
  lines. (#67)

### Changed

- The README, `docs/how-it-works.md` and `docs/preferences.md` say which layer is switchable and
  which is the floor, name the test a stance has to pass, and list the rules that do not pass it
  yet instead of leaving them implicit. (#67, #68)

## [0.6.0] — 2026-09-17

### Added

- A rule-detector registry (`claude/hooks/rule-detectors.py`): fourteen deterministic
  detectors over a session transcript, one or more per always-loaded rule, with an explicit
  opt-out list for the three rules nothing in a transcript can decide. `harness lint` now fails
  on a rule with neither, and reads its secret patterns from the same module. (#58)
- A tenth hook, `grade-bash`: every Bash command is graded 0–3 on the read-only grammar's
  decomposition (read-only, local, remote-mutating, irreversible) and the autonomy stance sets
  the gate: `execute` on grade 3, `confirm-writes` on 2 and up, `ask` on 1 and up. Prompting
  modes get `ask` with a one-line reason; `auto` and `bypassPermissions`, where a hook's `ask`
  is ignored, get `deny` with the same reason and a chat-confirmed re-run prefixed
  `HARNESS_CONFIRMED=1`. (#60)
- The session-end worker runs the detector registry and records `rules`, `counts` and `stances`
  per session; `harness usage --rules [--by rule|repo|stance]` reports hits, sessions and share per
  detector, marks `promote?` above 30 percent of at least 20 sessions and `unobserved` at zero over
  at least 20, and `--rescan` backfills from existing transcripts. (#59)

### Changed

- The README leads with the positioning line; the docs explain command grades, the ask-versus-deny
  split per permission mode with the hooks-reference quotes, and rule telemetry as the signal for
  pruning the always-loaded rules. (#61)

## [0.5.1] — 2026-09-16

### Changed

- Provenance credits a year of coding agents, most of it in Cursor and the last two months in
  Claude Code, rather than a year in `~/.claude`.
- The README and the comparison no longer claim the read-only hook is what stops plan mode
  prompting: Claude Code's own classifier does that by default in auto mode. The hook's value
  is deterministic, no-round-trip approval that also covers Manual mode and API, Bedrock and
  Vertex sessions.

## [0.5.0] — 2026-09-16

### Added

- A `tier-agent-spawns` hook (`PreToolUse` on `Agent`) applies the `delegation` stance to every
  subagent spawn that names no agent definition and no model, which is what a planning
  framework's or a plugin's "launch a subagent" produces: one tier below the session under
  `tiered`, untouched under `session-model`, a prompt under `off`. Frontmatter and explicit
  model choices are left alone. Inside a repository that carries a framework runtime
  (`_bmad/scripts/` or `_bmad/core/`), a bare spawn keeps the session model: the framework's
  lenses are judgment work its overrides cannot rename, and its own rule is same capability.
- `templates/bmad/custom/` carries override files in BMad's own format for `bmad-build`,
  `bmad-build-auto` and `bmad-code-review` that run their review layers as the `reviewer` and
  `spec-reviewer` agents and the implementation handoff on the builder's tier. `harness bmad
  apply` installs them into a repository's `_bmad/custom/`, skipping a template whose keys or
  layer ids the installed skill no longer declares, and `harness bmad check` reports that drift.
  The `harness-session` hook runs `apply` for the repository a session starts in and says so
  once when it wrote, kept or skipped a file.
- `docs/bmad.md` records how the two pieces keep the delegation stance in charge of a
  framework's spawns, and why the implementation handoff is not routed to `builder`.

## [0.4.1] — 2026-09-16

### Security

- The `readonly-bash` hook approved any command placed after a `#` comment and a newline: the
  lines were joined with `;` before tokenizing, so shlex's comment handling swallowed the rest
  and only the benign prefix was judged. Each line now loses its own comment first and a `#`
  inside a word stays part of the word, as in bash. Found by an independent review of 0.4.0.

### Fixed

- A bare `date MMDDhhmm` operand, which sets the system clock, falls through to the prompt.
- `harness uninstall` removes the PATH line it added to `~/.zprofile` and the ignore block it
  added to `~/.config/git/ignore`, leaving every other line in place.

### Changed

- CI runs `harness sync --dry-run` against the example config, as CONTRIBUTING said it did.
- The README points at `--help` for the full command surface and names `config get`;
  `docs/preferences.md` documents the `vscode.manage` and `codex.manage` keys.

## [0.4.0] — 2026-09-16

### Added

- `harness trust <path>` records a repository root whose `## Gate` block the stop gate may
  run, and `--remove` forgets it; the list lives in `~/.config/agent-harness/trusted.txt`.

### Security

- The `stop-gate` hook runs a repository's `## Gate` block only in a folder whose trust dialog
  has been accepted in Claude Code, or whose root is listed by the new `harness trust <path>`
  command, so a freshly cloned repository can no longer run commands on the first Stop. Until
  then it skips with a note on stderr.
- The `session` hook frames the handoff file it injects at session start as repository
  content on both sides, to be treated as data rather than instruction, the way the
  `neutralize` hook frames tool output.

### Fixed

- The `readonly-bash` hook approved several commands that write or execute: a second command
  after a newline, `awk` scripts calling `system()` or redirecting output, `env` with a program
  argument, `fd -x`, `rg --pre`, `sed -n` scripts using `w` or `e`, `sort -o`, `tree -o`,
  `yq -i`, `git grep --open-files-in-pager`, a `PATH=` or `GIT_*=` prefix, any binary run by
  absolute path with a read-only name, and the `>|`, `&>>`, `>&` and `<>` redirections. Each now
  falls through to the permission prompt. `tests/test_allow_readonly_bash.py` pins the corpus.
- The settings template no longer carries the allow rules that granted the same primitives
  without the hook: `Bash(awk *)`, `Bash(sort *)`, `Bash(sed -n *)`, `Bash(fd *)`, `Bash(rg *)`,
  `Bash(tree *)`, `Bash(file *)`, `Bash(date *)` and the leading-wildcard `Bash(* --version)`.
  The hook approves the safe invocations of every one of them.

### Changed

- `harness sync` removes an allow rule that an earlier template added and the current one has
  dropped, while still keeping rules the user added themselves.

## [0.3.0] — 2026-09-16

### Added

- A `plan-webfetch` PreToolUse hook that approves `WebFetch` while in plan mode, for
  `http`/`https` URLs only, so gathering context for a plan no longer prompts for every
  documentation URL. Every other permission mode is unchanged, and the `neutralize` hook still
  scans the fetched text (#37).

### Changed

- The `readonly-bash` hook now decomposes compound commands — `;`/`&&`/pipelines,
  `for`/`while`/`until`/`if` blocks, subshell `( )` and group `{ }`, and command substitutions
  `$(...)`, backticks and `<(...)` — and approves the whole only when every command inside is
  read-only, so plan mode stops prompting for read-only loops and substitutions. A write
  anywhere still falls through, and a reserved word used as an argument, such as `grep -q done`,
  is treated as data rather than syntax (#37).

## [0.2.0] — 2026-09-16

### Added

- `permissions.deny` rules in the settings template for credential files and generated
  directories, so the file tools and the Bash commands that read files are both covered.
- Three subagent definitions — `gatherer`, `reviewer` and `log-compressor` — carrying their
  model, effort level and tool list, so the delegation tiers hold without a retyped brief.
- A `sandbox` skill: container or built-in sandbox posture for an unattended loop.
- A `filter-output` PreToolUse hook that pipes a test, build, lint or type-check run through a
  line filter, keeping failures, summaries and the tail while preserving the exit status.
- A `cost` stance with `frugal`, `balanced` and `max` variants, and a `cache-hygiene` rule.
- A `neutralize` PostToolUse hook that flags instruction-shaped text in `Bash`, `WebFetch` and
  `Read` output, advisory only, never blocking or rewriting a result.
- A `stop-gate` Stop hook that runs the fenced `## Gate` block of a repository's `AGENTS.md`
  and refuses to end the turn while it is red, bounded by a block count and a time budget.
- Four slash commands — `/research`, `/plan`, `/build` and `/review` — each composing skills
  the harness already ships.
- A `usage-log` SessionEnd hook and `harness usage`, reporting per-session tokens and cache hit
  rate from a local file; see `docs/usage.md`.
- A `/handoff` command that writes `.claude/progress.md` and promotes durable learnings into a
  dated file under `docs/solutions/`, and a SessionStart hook that reads that progress file and
  the last five commits back as context. The progress file is added to the global git ignore.
- A `builder` agent carrying the standing implementation brief — a worktree of its own, tests
  with every change, the repository's gate, one local Conventional Commit and no push — so
  `/build` spawns it and verifies the gate itself instead of retyping the steps.
- A `spec-reviewer` agent that reports scope deviations only, and a two-stage `/review` that runs
  it before the quality `reviewer`, each in a context that has not seen the other's findings.
- A `planner` agent carrying the Review Card contract, so `/plan` can hand the file-writing to a
  fresh context and keep its own for the review conversation.
- A `design-judge` agent carrying the design loop's scored critique and its hard gates, so the
  independent judge is a fixed definition the skill names rather than a rubric pasted each round.

### Fixed

- The agent sync tests restore the environment they change, so a later test in the same run is
  no longer affected by them.

### Changed

- Always-loaded context (`claude/CLAUDE.md`, every rule, and the longest variant of each stance) is
  capped at 200 lines and `harness lint` fails with a per-group breakdown when it is exceeded. The
  rules keep their operative lines and point at the skill holding the reasoning; the rationale,
  examples and evidence moved verbatim into `delegation-tiering`, `plan-authoring`,
  `harness-authoring`, the new `transcript-hygiene` and `api-verification` skills,
  `docs/how-it-works.md` and `docs/preferences.md`. 583 lines before, 185 after.

## [0.1.1] — 2026-09-16

The repository was re-created with a fresh history for this release. The 0.1.0 tag and its
history are gone: the lint in that release embedded a denylist of the maintainer's own
identifiers, which is a disclosure of the very values it existed to catch.

### Changed

- The lint carries no list of real values. It matches personal-data shapes (12-digit account
  ids, email addresses, home-directory paths, cloud ARNs, hosted-zone ids, identity-provider
  tenants, private IPs) and secret patterns everywhere in the tree with no file exempt, derives
  the maintainer's name from `LICENSE` and `CODEOWNERS`, and reads personal terms from the
  untracked `~/.config/agent-harness/lint-terms.txt` (see `lint-terms.example.txt`).
- `harness lint --staged` and a `.githooks/pre-commit` hook, installed by `harness sync`, lint
  every commit in the checkout before it is made.
- `permissions: bypass` is refused unless `permissions_bypass_acknowledged` is `true` in the
  config file, and is documented as unsuitable for any machine that touches regulated data.
- Test fixtures assemble identifier-shaped strings at run time, so the tests are linted like
  everything else; a regression test asserts no file in the tree carries an identifier shape.

## [0.1.0] — 2026-09-16 (withdrawn)

### Added

- Nine core rules, seven stances with twenty variants, ten skills, three hooks and the
  Scannable output style, extracted from a working harness and rewritten in second person.
- `bin/harness` with `install`, `sync`, `diff`, `doctor`, `uninstall`, `lint`, `workspace
  create` and `config get`; standard library only.
- Symlink-based live sync into `~/.claude`, ownership-scoped settings merge, identity rendered
  from `~/.config/agent-harness/config.json`, per-session `HARNESS_*` overrides.
- VS Code owned settings and extension lists; generated Codex `AGENTS.md`; repo starter
  templates.
- Community files, issue and PR templates, CI with lint and tests, Dependabot for actions.

[Unreleased]: https://github.com/JakeSelby/model-citizen/compare/v0.14.0...HEAD
[0.14.0]: https://github.com/JakeSelby/model-citizen/releases/tag/v0.14.0
[0.8.0]: https://github.com/JakeSelby/model-citizen/releases/tag/v0.8.0
[0.7.0]: https://github.com/JakeSelby/model-citizen/releases/tag/v0.7.0
[0.6.1]: https://github.com/JakeSelby/model-citizen/releases/tag/v0.6.1
[0.6.0]: https://github.com/JakeSelby/model-citizen/releases/tag/v0.6.0
[0.5.1]: https://github.com/JakeSelby/model-citizen/releases/tag/v0.5.1
[0.5.0]: https://github.com/JakeSelby/model-citizen/releases/tag/v0.5.0
[0.4.1]: https://github.com/JakeSelby/model-citizen/releases/tag/v0.4.1
[0.4.0]: https://github.com/JakeSelby/model-citizen/releases/tag/v0.4.0
[0.3.0]: https://github.com/JakeSelby/model-citizen/releases/tag/v0.3.0
[0.2.0]: https://github.com/JakeSelby/model-citizen/releases/tag/v0.2.0
[0.1.1]: https://github.com/JakeSelby/model-citizen/releases/tag/v0.1.1

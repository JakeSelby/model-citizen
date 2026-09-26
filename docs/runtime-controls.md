# Runtime controls

One shared policy engine (`lib/harness_core/lifecycle.py`) consumes normalized events. Native
adapters register one coordinator per lifecycle event and translate decisions. A deny wins over
an allow or rewrite; independent rewrites compose. Codex cannot pause with Claude's hook `ask`
decision, so a request requiring confirmation is denied with its reason. Output filtering never
grants an otherwise unapproved shell command merely to rewrite it.

Hook registration is not activation. Codex requires native trust for the current hooks content;
accept it in the client. `citizen trust` separately authorizes running a repository's gate and
does not manufacture native hook trust. Native permission restrictions always take precedence.
Hosted search and continuation through an already running shell are not universally intercepted.
Hooks assist workflow policy; they are not a substitute for the runtime sandbox.

The stop gate hashes HEAD, staged and unstaged binary differences, untracked file contents,
repository identity, and gate definition. Commands share a shell so `cd` and `export` persist.
A gate that changes the tree, times out, or exhausts its retry budget is unverified, never green.
The retry budget is counted per session, so sessions sharing a checkout do not reset each other.
Unexpected gate errors block. State writes are atomic.

Constrained roles use [isolated CLI workers](role-workers.md), with shared role/stance resolution
and fixed native tool controls. The harness validates and publishes planner content to a new
approved artifact path. Direct native role defaults are not confinement: Codex can reapply parent
permission overrides. Active lifecycle hooks route constrained role launches to the worker
command. Native qualification is still required; do not treat a projection or unit test as certification.

Native agent definitions are resolved at sync from the cost variant in force. A role the selected
posture does not move stays a symlink to the committed projection, exactly as before; a role it
moves is rendered into the Claude home, so the class and effort it runs with are written into the
file on disk rather than decided per session. A
session-scoped `HARNESS_STANCE_COST`, like any `HARNESS_STANCE_*`, stays in that session and does
not move them until the next `citizen sync`; an isolated [role worker](role-workers.md) resolves
its class, effort and soft budget per run, from the same table and the same precedence, so it
follows that session selection and a constrained role cannot run one way as a worker and another
as a definition. A `role_bindings.<runtime>.<role>` entry still wins over the variant's row.
Routing of spawns that name no agent definition turns itself off in a workspace that ships its own
`.claude/agents/worker-*.md`, since a project definition outranks the user's and routing to it
would put that repository's instructions on every unnamed spawn; the spawn runs as written and the
hook says so.
Before downgrading to a release that only links these definitions, either select `balanced` with
no role bindings and sync once, which restores the links, or run `citizen uninstall`. Syncing a
home back with the older release is not enough on its own: role files the older release does not
ship stay in `~/.claude/agents` and `~/.codex/agents`, and its `citizen diff` reports no drift,
because code that never knew those roles cannot miss them. `citizen uninstall` before the
downgrade is the remedy; it removes what the newer release wrote.

Routing is session-scoped for the same reason that effort is sync-scoped: what a native agent is
comes from files read at a moment, not from a live lookup. Session start records which definitions
the session's registry held, and an unnamed spawn is routed only to a worker that session can
resolve. A `startup` writes the record, a `resume` may only narrow an existing one, and `clear` and
`compact` leave it alone. A session that reloads a definition installed after it started is told
so by the runtime, on its own transcript, and routes to that worker from the turn it hears it;
a session that is told nothing — a headless one never reloads — keeps the previous behaviour and
is asked once to start a new session, which is still the certain remedy after a sync.
Any failure to answer the routing question leaves the spawn unrouted and silent, never refused.

Usage records identify the runtime and available runtime version. Codex cumulative token snapshots
are counted once; missing measurements remain null and reports label partial totals. Detector
failures are reported separately and excluded from clean-session denominators. Lock contention
refuses an overwrite; detached worker failures go to `usage.errors.jsonl` beside the usage ledger.
Transcript adapters cannot observe nested tool calls absent from the transcript and do not prove
that a stance caused a behavior.

A cap on a multi-item read is a budget: ask the source for newest-first where it can be asked,
check the order that actually arrives, refuse the page when it is not descending, and record at
the declaration which end is dropped — when several sources compete for one budget, drop the
least authoritative first. The Remote Control sessions page (`remote_control.fetch_sessions`) and
the usage feed's `OPEN_TAIL` both keep the newest, because a correction that arrives last is the
one a trimmed history must not lose. The sessions endpoint takes no sort parameter and is ordered
by `last_event_at`, so that read asserts the order rather than requesting it, and a refusal is
reported as not checked, never as nothing found. A count over a page that carries a cursor names
the page it counted rather than the account.

## Hook ids

Every policy module that answers a lifecycle event has an id, its basename under `policy/hooks/`,
and each id is a unit of the `hooks` switch kind in the [selection document](preferences.md#the-selection-document):

| Id | Answers |
| --- | --- |
| `allow-plan-webfetch` | PreToolUse on WebFetch in plan mode |
| `allow-readonly-bash` | the read-only Bash allow, plan-mode allows and `plan_allow_tools` |
| `approvals` | UserPromptSubmit: records a prompt that is only `approve <code>` tokens, for `grade-bash` in auto mode, Claude Code only |
| `brief-guard` (core) | PreToolUse on a spawn |
| `filter-output` | PreToolUse on Bash |
| `grade-bash` (core) | Bash grading, its ask or deny, consuming an approval in auto mode, guarding the approvals store, and the decision log's Bash rows |
| `harness-session` | SessionStart |
| `intent-overlap` | PreToolUse on Edit, Write, MultiEdit and NotebookEdit: a live sibling's claim on the path |
| `neutralize-tool-output` (core) | PostToolUse |
| `stage-user-files` | PreToolUse on SendUserFile, Claude Code only |
| `stop-gate` (core) | Stop |
| `tier-agent-spawns` | band routing of a spawn, and the integration descriptor notice |
| `usage-feed` | UserPromptSubmit, SubagentStart, SubagentStop and PostToolUse on a spawn, Claude Code only |
| `usage-log` | SessionEnd |
| `validate-plan-card` | PostToolUse on a plan file |
| `workspace-session` | SessionStart, from its own entry: the workspace's other folders and their instructions ([workspaces](workspaces.md#the-session-start-hook)) |

`citizen config set hooks.<id> off` switches one off, and it applies from the next event with no
sync: the dispatcher resolves the selection at each event and neither loads nor runs a module
whose id is `off`. The libraries those modules load (`decisions`, `posture`, `pricing`,
`telemetry`, `rule-detectors`, `otel-headers`, `filter-lines`) have no id and no switch. Denying
every spawn under `delegation: off` is the stance's own answer and stays with any id off.
Role confinement has no id either: the constrained-role, `harness-role:` marker, framework and
evasion denials and the Workflow launch guard run with every hook off, so switching
`tier-agent-spawns` off stops band routing and never lets a constrained role run in session.

The four core ids enforce rather than assist. A layer may switch one off only when the user
configuration sets `"core_switches_acknowledged": true`; `config set`, `sync` and
`citizen selection` refuse it otherwise, before anything is written, and so does withdrawing the
acknowledgement while a core hook is off. The acknowledgement is read from the user configuration
alone, because a project, session or mode file may carry selection keys only. A hook that meets an
unacknowledged `off` keeps running, and so does every hook when the selection will not resolve.

Codex's adapter dispatches through the same `lifecycle.py` and reads the same id map. There is no
Codex-only id; an id whose event Codex does not raise, such as `usage-feed`, simply never runs
there. `citizen catalog` lists each id with kind `hooks`, its source and whether it is core.

## Decision providers

`lib/harness_core/decision.py` holds one transport-agnostic contract for the question "may this
action proceed, and how": `decide(action, counterparty, context)` returns a `Decision` carrying an
outcome of `allow`, `ask` or `deny`, an autonomy level of 1 to 3, the provider name, a reason, and
an `injected_cognition` block of rule matches and optional agent and user messages;
`record(action_outcome)` notes how an action turned out; `learn(approval_stream)` takes past
approvals and may be a no-op. The shape deliberately mirrors the `decide`/`record`/`learn` surface
of an external control plane, so a hosted provider can be added later without a second contract.
An action names a class — `coding.shell_exec`, `coding.git_commit`, `coding.git_push`,
`coding.deploy`, `coding.file_write`, `coding.pr_merge` — and the command grade where one is known. A counterparty is
a `repo:<name>/<branch>` slug. `<name>` is the repository's, read from its common git directory, so
a linked worktree in a directory named for its task names the repository it belongs to; the usage
ledger's `repo` field keeps the worktree directory's name.

Two providers ship. `none` is the default: every action is allowed at level 3 with the reason
`governance: none`, and no policy file is read. `local` reads two policy files in one schema
(`defaults`, `pairs`, `caps`): a user-level `governance.json` beside `config.json`
(`~/.config/agent-harness/governance.json`, under `HARNESS_HOME` when that is set), and
`.agent-harness/governance.json` in the repository. It merges them before resolving anything.
The repository file wins for a class default and for each class inside a pair; caps combine by the
lower value, so neither file can lift a ceiling the other set. The user file holds levels that apply
everywhere, and levels for repositories that carry no file of their own.

```json
{"defaults": {"coding.git_push": 2},
 "pairs": {"repo:agent-harness": {"coding.pr_merge": 3},
           "repo:agent-harness/main": {"coding.git_push": 1}},
 "caps": {"coding.deploy": 2}}
```

A level is then resolved in this order: the exact `repo:<name>/<branch>` pair, then the
whole-repository `repo:<name>` pair, then the action class default, then the level the autonomy
stance implies (`execute` 3, `confirm-writes` 2, `ask` 1, and 1 when nothing resolves). The
repository name in a slug ends at the first `/`, so a branch such as `feat/x` still reaches
`repo:<name>`. A cap is a ceiling the resolved level never exceeds; `coding.deploy` carries a
built-in cap of 2 that a policy file may lower and may not raise, and `coding.pr_merge` has no
built-in cap. The reason and each rule match name the file that supplied the level or the cap, or
say `autonomy stance` or `built-in`. Level 3 allows every grade, level 2 asks at grade 2 and up, level 1 asks at grade 1 and up,
and an unknown grade is judged as 1. A policy file at either level that cannot be honoured as
written is an error naming that file, never a silent "no policy". Both providers write to the existing
`decisions.jsonl` ledger and neither reaches the network.

A third provider, `jev`, lives in `lib/harness_core/decisions/jev.py` and answers over the
network. It asks a validated question pack — a `choice` judgment and a `score` severity — about
the action class, the counterparty, the command grade and at most a command string and a summary,
and it carries the deterministic `local` provider underneath. The service has no abstention
outcome, so every `choice` question must offer an explicit `unknown` option and a pack without one
is refused before anything is sent; `unknown` and an answer below the confidence threshold both
mean "use the deterministic answer". A judgment may turn an `allow` into an `ask` and may never
widen a decision or produce a `deny`. Every other outcome fails open to the deterministic
decision: no key, a timeout, an exhausted budget, a malformed response, an unexpected exception.
Each call writes one `event` row carrying the status, the requested and returned model ids, the
pack and request hashes, the usage and the latency, and never the state; `citizen decide`
suppresses that row, because a reporting command changes nothing. The endpoint must be `https`
and the opener can reach no other scheme, since a bearer key goes with every request, and a
request is charged to its budget as it is sent rather than when it succeeds, so a failing
endpoint cannot be retried without limit. The token ceilings, the endpoint, the response shape
and the status mapping come from the vendor's documentation and have not been checked against
the live service from this repository. Answers are not
deterministic across identical requests, so nothing promises a repeated request answers the same
way — only that the same request hashes the same. Credentials come from `TYPESAFE_API_KEY` or
`JEV_API_KEY` in the environment; no key file is ever read.

## What a provider that leaves the machine may do, and send

Selecting `jev` is not consent to a request. `lib/harness_core/decisions/controls.py` resolves
three separate questions per decision, and all three must agree before anything is sent.

**How far this point may be judged.** `governance.jev.mode` sets the default and
`governance.jev.modes.<point>` overrides it for one of the decision points the ledger already
names — `grade-bash`, `stop-gate`, `tier-agent-spawns`, `brief-guard`, `evasion-deny`. `off`
calls nothing. `shadow` calls, writes the ledger row and returns the deterministic decision
untouched, so an answer can be measured before it is trusted: nothing reaches the model or the
user. `advise` puts the judgment in `rule_matches`, says what `act` would have done, and changes
no outcome. `act` lets a judgment turn an `allow` into an `ask`, and nothing else. Every mode
defaults to `off`, so a configuration written before this existed makes no request; a mode, a
point or a field the harness does not know fails at `citizen config set`, not at the first call,
and a point name this harness does not know reads `off` rather than the default. Each call
writes one ledger row carrying the mode, the judgment label, the severity level, the
deterministic outcome and the outcome acting on the judgment would have reached, so a `shadow`
answer can be compared against the decision it did not change. Labels only: never the state.

**Whether anything may go out at all.** `~/.local/state/agent-harness/jev-disabled` is the kill
switch: while that file exists every mode reads `off`, with no configuration change and no
restart, because the sentinel is read per decision rather than at construction.
`governance.jev.sentinel` moves it, absolute or resolved against the state directory, never
against the working directory. A session that was running when the file appeared stops calling,
and starts again when it is removed, with no restart and no edit. A live request also needs a
key in the environment; without one the call fails open to the deterministic answer like any
other failure.

**What may leave.** `governance.jev.state_fields` is an allowlist, empty by default, over
exactly two fields: `command` and `summary`. Everything else in a caller's context — a file
path, a prompt, an environment value, tool output, assistant prose — has no field to travel in
and is never built into the request, which carries the action class, the counterparty, the grade
and the grade scale besides. A listed field whose text matches one of the shared secret shapes
is dropped whole rather than masked, and if that pattern list cannot be loaded no free text is
sent at all. Redaction recognises the shapes it knows; a credential that reads like ordinary
prose still travels, which is why the allowlist is two fields and not a free vocabulary.

`governance.jev.timeout` (2 seconds by default, inside the hook budget),
`governance.jev.max_requests` and `governance.jev.max_tokens` bound the rest, and they bound a
session rather than a process: a hook is a new process per event, so the counters live in
`~/.local/state/agent-harness/jev-spend.json` keyed by session id, under the lock, read before
each check and added to as each request is charged. A spend file that cannot be read or written
leaves the in-process count standing rather than failing a decision. `citizen doctor`
prints the mode per point, the allowlist, where the kill switch lives, the model every request
pins, what the last call returned, and whether a credential variable is set — by name, never its
value.

**What each call cost.** Every call also writes one `kind: "decision"` row to the usage ledger:
the point, the mode, the status, the model ids, the pack and request hashes, the judgment and
severity labels, the deterministic outcome and the one an `act` mode would have reached, the
tokens, the latency and the session that asked — and none of the state it sent. `citizen usage
--by provider` prices those rows from `policy/prices.json` like any other. A mode of `shadow` is
measurable for exactly this reason: the row exists, priced and labelled, before anything the
provider says can change an answer. Both rows stop when `telemetry.decisions` is `false`; see
[usage telemetry](usage.md).

## Measuring a provider before trusting it

A typed answer is not evidence that it was the right one. `citizen decisions eval` replays the
labelled rows of the [decision log](usage.md) — real inputs, the answer the deterministic hook
gave, and the outcome the session later showed — through a question pack in `shadow` mode, and
writes a report to `~/.local/state/agent-harness/jev-eval.json` or wherever `--out` says.

```sh
bin/harness decisions eval --replay tests/fixtures/jev/eval/responses.json   # no socket
bin/harness decisions eval --point grade-bash --split heldout --out report.json
bin/harness decisions eval --live --max-requests 50 --usd-per-mtok 3 --budget-usd 2
```

A **pack is versioned**. `lib/harness_core/decisions/packs.py` holds each one as an id, a
`major.minor.patch` version and the hash of its content, frozen at construction so nothing that
holds a pack can rewrite a criterion between the hash being taken and the request being built.
The provider puts the id and the version on every ledger row beside the request hash, so a row
resolves to the words that were asked. A threshold fitted against one version says nothing
about another, and `Pack.verify` refuses the mismatch rather than carrying the number across.

The **split is seeded by content**: a case lands in `dev` or `heldout` by the hash of the
decision point and the capped input a request would actually carry — not the row's
`input_sha256`, which is over the uncapped text and would put one identical request on both
sides — never by `random` and never by position, so a re-run reproduces the split and adding
rows does not reshuffle the old ones. A request hash found on both sides is a refusal, not a
warning. Thresholds are fitted on `dev` alone; **only the held-out block is evidence**, and the
dev block is in the file so a reader can see how far the fitted split flatters the fit.
`--split dev` prints it with a line saying as much. There is no global default, and a point
with no labelled dev case is reported **unfitted** rather than given the shipped 0.8 as though
it had been measured.

The fit is scored under the provider's own semantics: below the threshold, and for `unknown`
or no answer at all, the deterministic answer is what is compared against the label, because
that is what the harness would have done. Scoring an abstention as a miss would drive every fit
to the lowest confidence in the set. The report carries, per point and per split: accuracy, the
same rate for the deterministic answer alone as the baseline to beat, how many cases the
provider could have changed at all (it may tighten an allow into an ask and never widen one),
the confusion by label, agreement with the deterministic answer over the labelled cases, a
calibration table over the confidence with its expected calibration error and a bootstrap
interval drawn from hashed indices rather than a linear congruential generator, input and
output tokens, cost per 1,000 decisions where a price was given, the returned model ids, and an
`unusable` block counting unavailable calls, errors and abstentions — none of which is ever
counted as a pass. The report holds no clock and no absolute path, and no input text, so two
runs over one log are byte-identical and the file can be sent on.

What the labels do **not** prove is in the report's own `caveats`, and is the first thing to
read. `grade-bash` logs a row only where the harness said `ask` or `deny`, so the set is the
prompts and never the commands allowed through without one; `ran` is a user approving something
they were asked about, which is evidence the prompt was unnecessary and not proof; `not_run`
does not separate a refusal from an interrupted turn. A flip rate over `--repeat` passes is
zero by construction under `--replay`, because a recorded response cannot disagree with itself,
and latency under `--replay` is the runner's and is reported as unmeasured rather than as a
number whose name claims the service produced it.

Ordinary runs need `--replay` and open no socket at all, which is also why a replay ignores the
kill switch: the switch gates requests and a replay makes none. `--replay` and `--live` together
are refused rather than silently ordered. `--live` needs `--max-requests`; `--budget-usd` needs
`--usd-per-mtok` beside it, because no price for this provider is published here and a dollar
ceiling nobody can convert is not a ceiling; and a live run with an empty
`governance.jev.state_fields` is refused as pointless, since every request would carry the same
four base fields and differ in nothing. A live run sends under the user's own allowlist, so an
evaluation cannot send a field a hook is not allowed to send.

`governance.provider` selects one; the default is `none`. `citizen decide --action <class>
[--grade N] [--counterparty <slug>] [--json]` prints the decision for the current repository and
the policy files it read, each marked present or absent; `citizen doctor` lists the same files
under the governance provider.

### How command grading consults the provider

`grade-bash` consults the selected provider for every Bash command the autonomy stance lets
through, and only when `governance.provider` is not `none`. Under `none` it imports nothing and
its output is exactly what the stance alone gives.

- **Classification.** Each simple command in the line is classified: `git push` is
  `coding.git_push`, `git commit` is `coding.git_commit`, `gh pr merge` is `coding.pr_merge`, a
  deploy verb the grader knows (its `deploy` family, `vercel deploy`, `netlify deploy`,
  `cdk deploy`) is `coding.deploy`, and anything else is `coding.shell_exec`. Wrappers, runners,
  `sudo` and a shell's `-c` text are looked through as the grader looks through them. A
  read-only command, grade 0, is not put to the provider, since no level asks at grade 0.
- **Counterparty.** Each command's counterparty is the repository and branch of the directory it
  runs in: a `git -C <dir>` moves it for that command, and a `cd <dir>` or `pushd <dir>` earlier
  in the line moves it for the commands after it, substitutions included. The repository policy
  read is that repository's own `.agent-harness/governance.json`. A directory is trusted only when
  every change before the command is a literal path; after `cd -`, `cd "$X"`, `popd`, a `cd` in a
  subshell, substitution, pipeline or background job, `env -C` or `--git-dir`, the counterparty
  is `repo:unknown/local`, which no pair names, so the class default governs.
- **Tighten-only.** The provider is asked with the command's grade, and the strictest answer
  across the segments stands. It is never asked about a command the stance already gates, so it
  can add a prompt and never remove one. An `ask` is an ask in a prompting mode and, in `auto` and
  `bypassPermissions`, a deny through the same channel the grader uses: an approval code in
  `auto`, which the user's `approve <code>` reply lets through once, and the confirm marker in
  `bypassPermissions`. A provider `deny` is a deny in every mode. The reason names the class, the
  counterparty, the level and the rule or file that supplied it.
- **Fail closed.** A configured provider that cannot answer — an unknown provider name, a policy
  file that raises `PolicyError`, any other exception — makes the command ask, naming the error.
  It never falls back to allow.
- **Policy files.** An agent write to the user-level `governance.json` or to any
  `.agent-harness/governance.json` is a level-1 action: always asked about. A Bash command that is
  not read-only and names either file, or writes one through a redirect, `tee`, `sed -i`,
  `cp`, `mv` or a similar path writer, asks; a `Write`, `Edit`, `MultiEdit`, `NotebookEdit` or
  `apply_patch` to one asks too, and in `auto` mode is refused with an approval code covering that
  exact edit once. The user's `config.json` is guarded the same way, by Bash write target and by
  file tool, and so is any `harness config set governance...` command: the configuration selects
  the provider, so without the guard an agent could switch governance off instead of editing a
  policy.
- **Rows.** Each decision the hook asks for is one `governance` row in `decisions.jsonl`, owned by
  `hooks/grade-bash`, whose `input` records the action class, counterparty, level, grade, outcome
  and provider, and never the command text. `telemetry.decisions: false` stops them with the rest.

Session start checks a declared integration's configuration without installing it. Installation
remains explicit. See [task continuation](task-continuation.md), the [BMad integration](bmad.md),
[installation ownership](runtime-installation.md),
and the compatibility catalog for qualification evidence.

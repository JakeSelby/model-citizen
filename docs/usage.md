# Usage telemetry

Measurements identify their runtime. Claude and Codex transcript adapters share detectors;
Codex cumulative token snapshots are counted once, unavailable metrics remain unknown, and
reports label partial totals. Detector failures are excluded from clean-session denominators.
See [runtime controls](runtime-controls.md) for limits. The Claude transcript details below
remain specific to that adapter.

The `usage-log` hook runs on `SessionEnd` and keeps one record per session in
`~/.local/state/agent-harness/usage.jsonl`. It is a local file and nothing else: no network
call, no service, no account, and nothing beyond the session id, the repository directory name,
the branch, model ids and token counts. Sending those rows to an observability backend is
opt-in, off by default and described in [telemetry.md](telemetry.md); the ledger stays the
record and the backend is a copy that `bin/citizen usage export --since` can rebuild.

## Machine-readable reports

Add `--json` to any local usage report to receive the report's own aggregates as one JSON
document. The document has `schema_version: 1`, names its `report`, `by` grouping and `days`
window, and carries one object per displayed group in `groups`. Numeric fields are JSON numbers;
an unavailable percentile, ratio or price is `null`, never zero or a non-standard `NaN` value.

```sh
bin/citizen usage --json
bin/citizen usage --by role --json
bin/citizen usage --rules --by repo --json
bin/citizen usage --conflicts --json
```

The JSON and text renderers share the aggregation that assigns Workflow-tool runs to
`(workflow)`, rows without a profile fingerprint to `(unattributed)` and rescanned stance rows to
`(unknown)`. An empty window succeeds with `groups: []`. Invalid option combinations fail on
stderr without printing a success document. `usage export` is a separate OTLP action and rejects
`--json` rather than silently ignoring it.

## Which rules fired

The same report that sums the tokens scores the rules. `bin/citizen usage --rules` counts
detector hits per rule over the window instead of tokens, and `citizen --help` lists it beside
the token groupings.

```sh
bin/citizen usage --rules                      # hits per detector over the last 30 days
bin/citizen usage --rules --by repo            # sessions, hits and the top three per repo
bin/citizen usage --rules --by stance          # the same, per dimension=variant
```

Three groupings and no more: `rule`, the default, one line per registry id; `repo`, one line
per repository directory name; `stance`, one line per `dimension=variant` in force. `--by
model` is refused rather than quietly regrouped, since a session's hits belong to no one of its
models.

Two annotations come from the numbers alone, and their thresholds are `RULE_PROMOTE_SHARE` and
`RULE_MIN_SESSIONS` in `bin/harness`. `promote?` marks a detector that hit in more than 30
percent (`RULE_PROMOTE_SHARE = 0.30`) of the sessions in the window; `unobserved` marks one
that hit in none of them. Neither is printed below 20 measured sessions
(`RULE_MIN_SESSIONS = 20`), because a share over three sessions says little. Only a record
carrying a `rules` map counts toward either, so the denominator is measured sessions and not
rows.

Under the table, `--rules` lists every rule in your loaded instruction surface: this
repository's rules less any you switched off, the rules of each registered primitive root, and
each stance at its selected variant, named by its dimension. Each is one of three states, with
the share measured on the first line:

```text
rules: 10 measured, 2 dark, 7 unmeasured (52% measured)
  measured   cache-hygiene               primitives/rules/cache-hygiene.md
  dark       conciseness                 primitives/rules/conciseness.md: a comment's redundancy is ...
  unmeasured cost                        primitives/stances/cost/balanced.md: no detector names it and it has no opt-out
```

- **measured**: a detector that runs under your stances names the rule;
- **dark**: nothing measures it on purpose, and the reason is printed: an `OPT_OUT` entry in
  the registry, or `opt_out: <reason>` in the rule file's front matter;
- **unmeasured**: neither, with the reason, such as a detector gated off by your stances.

The share counts dark rules in its denominator and is floored, so one gap never reads 100
percent. A detector names a rule, not a file, so when two files share a name, as the
`delegation` rule and stance do, the first holds its detectors and the second is listed
unmeasured, saying why.

**Your own detectors** go in `.ruleprobe/detectors.yaml` at your repository's root, in the
format standalone [ruleprobe](https://github.com/JakeSelby/ruleprobe) reads, through the same
vendored engine, so one file serves both. The session hook runs them over every session in that
repository, the report counts their rules as measured and lists a detector with no hit as a
zero line, all without a code change. A bad entry is skipped and printed with its file and line
under `findings:`, and the rest of the file still loads. Two differences from `ruleprobe`: the
per-user `~/.config/ruleprobe/detectors.yaml` is not read, and a `detector:` block in a rule
file's front matter is not run, so that rule is reported unmeasured rather than measured by a
detector that never fires.

What each detector looks for, how a rename folds and why a rescanned session is excluded from
the stance grouping are under [rule telemetry](#rule-telemetry) below. Running the measurement
without the rest of the harness is [standalone measurement](standalone-measurement.md).

## What is recorded

Every row names its `kind`: `session`, `subagent` or `worker`. A row written before the field
existed is read as a session, which is all there was to record, and `--rescan` upgrades it.

Every row also names the `harness_version` that wrote it, read from the same `VERSION` file
`citizen --version` prints, so a change in spend can be read against a release. **A rescanned
row carries `null`**: the version that ran a past session is not recoverable from its
transcript, and stamping today's would make the whole history look like this release.

Every new row, in this ledger and in the decision log, also names
the **`profile_fingerprint`** of the profile that wrote it: the sha256 of each switched-on
module's content, the stance variants, the configuration keys that reach the model or a hook
(`identity`, `permissions`, `permissions_bypass_acknowledged`, `plan_allow_tools`, `telemetry`,
`governance`) and the harness version. `posture.profile()` builds the document it digests, and
`posture.fingerprint()` is the one definition. Identical profiles match on any machine, since no
path reaches the digest; one module, stance or setting apart, they differ. A role worker's row
carries the profile its run started under, and a replay row carries its arm's, or `bare` for the
arm that loads no harness. A row from before the field, a rescanned session the ledger did not
already hold, and a row whose profile could not be resolved carry none or `null`, and read as
**unattributed**: nothing is ever given a guessed fingerprint.

A session row also carries **`context_attribution`**: which module put how many tokens into the
session's context. Context is shared, so this is an estimate, and the field says so:

```json
"context_attribution": {"estimand": "soft estimate",
  "method": "chars/4 of resident text: a rule or stance variant whole; a skill, role or workflow its name and description",
  "modules": {"rules/secrets": 222, "stances/voice": 106, "skills/sandbox": 65, "roles/builder": 60}}
```

Each key is a selection reference, `kind/unit`, for every switched-on module and every stance
with text resident before the first prompt. A skill, role or workflow is resident as its listing
entry, and its body loads on demand, so only the entry counts. A hook's context arrives per event
and is not estimated here; the decision log attributes what a hook decided instead.
`posture.context_attribution()` is the one definition, so one module switched off removes that
module's entry and changes no other, while the fingerprint changes with it. A replay row carries
its arm's attribution, with no module for the bare arm. A rescanned session keeps what the ledger
already held and otherwise carries no field, never this minute's selection.

**`kind: "session"`** — `session_id`, `repo`, `branch`, `models`, `started`, `ended`, `input`,
`output`, `cache_read`, `cache_write`, `subagents`, `turns`, `effort`, `effort_source`,
`days`, `raw_vs_deduped` and, when the row has any, `idless_records`.
The source is the transcript
Claude Code already writes under `~/.claude/projects/`. The worker streams it and sums the four
token fields over assistant messages **once per message id, at that id's largest figure**: one
API response is written as several transcript entries, so counting per line inflates every
total — but those entries do not repeat one `usage` object. The early ones carry a partial
streaming `output_tokens` and the last carries the response's true figure, so taking the first
undercounts it. The field-wise maximum is the final figure, and a reordered or truncated tail
cannot lower it. A record that carries no message id is keyed on its `requestId` instead, which
names one API call: the same call written into both a session file and a subagent file is one
response, and a call whose other records do carry a message id joins their slot rather than
opening a second one. A record with neither id is unknown rather than a duplicate, so it is not
deduplicated at all — it is summed as written, and `idless_records` counts how many such records
the row's totals include, the session's own and those of the subagent files folded into them.
A row without the field was deduplicated whole. `subagents` counts `Agent` tool calls, less
any a hook refused: a call whose result is an error and which left no subagent transcript. A
session file holding sidechain lines is the older format, where a spawn that ran has no file of
its own, so there every call is counted.

`raw_vs_deduped` is **the measured size of that inflation**: the per-line sum of the four token
fields over the deduplicated total the row carries, across the same records — the session's own
and those of the subagent files folded into it. `1.0` says the transcript held nothing to
remove; `2.4` says counting every line would have billed this session for two and a half times
what it spent. One ratio rather than one per field, because the fields are deduplicated by the
same slots and the row already carries each of them for a reader who wants them apart. A Codex
session row, whose runtime reports cumulative snapshots rather than a figure per record, carries
the string `"unknown"` rather than `1.0`, which would claim a measurement nobody made. A
subagent row, a worker row and a row written before this release carry **no such key at all**,
and a reader — `bin/citizen usage` included — reads that absence as unknown for the same reason.
The footer figure `bin/citizen usage` prints is the window's raw sum over its counted sum: each
row's ratio weighted by the deduplicated tokens that row contributed to the columns above it,
which under `--by day` are its in-window slices and not its whole total. The OTLP export carries
a row's own value as the `raw_vs_deduped` attribute, and a row without the key exports none. The token totals **include the
session's subagents**, because their tokens are the session's bill — counted once over one map
of message ids, never as a sum of two files. Older Claude Code wrote a subagent's turns into
the session file as sidechain lines and newer Claude Code writes them to the agent's own file;
a transcript carrying both would otherwise pay for every delegated token twice.

### Session effort

`effort` is the reasoning effort the session mostly ran at, and `effort_source` says where it
was read: `transcript` for Claude Code, which writes `effort` and `perTurnEffort` on every
assistant record, and `turn_context` for Codex, which records it per turn and admits values up
to `ultra`. Effort changes mid-session — 14 of 112 Claude Code transcripts and 4 of 44 Codex
rollouts measured on one machine — so the row records **the value that covered the most output
tokens**, not the first or the last. For Claude Code that weight is the session's own messages
only; a subagent's effort belongs to the spawn, not to the session. For Codex it is the
difference between consecutive cumulative snapshots. A transcript that records no effort at all
leaves both fields `null`, which the report then has nothing to group by.

### Per-day slices

`days` maps a UTC date to `input`, `output`, `cache_read`, `cache_write` and `turns` for that
date. A session that runs for a fortnight ends on one date and spends on fourteen, and
attributing it whole to its end date is what made five long sessions 68% of all output tokens
on one machine.

The slices are cut from the same deduplicated message-id map the row's totals are summed over,
so **a Claude Code day's slice includes that day's subagent tokens**, exactly as the session
total includes them: the session row means one thing, and a slice that excluded them could not
add up to it. The first date a message id is seen under is the one that holds, so a response
written across midnight belongs to one day. Codex has no per-message figure, only cumulative
snapshots, so a Codex slice is the difference between consecutive ones, attributed to the date
of the snapshot that closed it; a `total_tokens`-only row gets no slices at all.

The slices are checked against the row's own totals before they are written, field by field. A
map that does not add up is dropped rather than recorded, so a `days` map on a row is always
consistent with the row.

**`kind: "subagent"`** — one row per `agent-<id>.jsonl` anywhere under `<session>/subagents/`,
the tree Claude Code writes beside the session's own file. The walk is recursive because a
Workflow-tool agent lives a level deeper, at `subagents/workflows/wf_<id>/`, and its `workflow`
field names that directory. `agent_id`, `agent_type` and `spawn_depth` come from the sibling
`.meta.json`, and an agent written without one is recorded as `agent_type: "unknown"` rather
than dropped. Then `model` — the id the agent's own transcript reports, most frequent across its
assistant records, falling back to the alias the spawn asked for only when it recorded none, so
a routed spawn and a direct one on the same model group under one name — `effort`, the four
token fields, `tool_calls` and, when the agent's transcript held any, `idless_records`: the
records in this row's totals that neither a message id nor a request id identified. `tool_use_id` is
the parent call this row belongs to, `requested_type` is the agent type that call asked for, and
`rerouted` is the two disagreeing — the measure of how often a spawn hook moved a spawn. A
requested type is kept only when it is a name the tool could have resolved; anything else is
recorded as `"other"`. `budget_output_tokens` and `budget_tool_calls` are the **soft budget the
role carries** — the same figures `brief-guard` writes into a brief — so an overrun is a
subtraction on one row rather than a join against whatever the cost table says today. They are
read from the table at the moment the row is written, not from the brief, which no scan can
see; a role nothing prices, a table that will not build and a Codex subagent all record `null`,
because a zero would say the spawn was budgeted nothing. A Workflow-tool agent records `null` too,
whatever role it is named for, and carries `unconfined: true`: the tool launched it, so no spawn
hook routed it and no brief budgeted it. `return_path` and
`return_over_budget` measure the return this row's spawn handed back, joined to the parent's
`Agent` call on the same `tool_use_id`: whether it named a path that existed under the worktree
or the scratchpad at the moment the row was written (`"resolvable"`, `"unresolvable"`, or
`"none"` for a return that named no path at all, which is a fact and not a failure), and
whether its word count passed the cap its brief stated — the number beside the word `words` in
the cap `rule-detectors` reads, or the 400-word default `brief-guard` appends to a brief that
states none. A path counts when it is quoted, in a fence or in backticks, or when bare prose
gives it a path's own shape: a root, a relative prefix, or an extension on its last segment, so
`pass/fail` and `2026/09/22` are prose and a URL is nobody's file here. Both fields are `null`
when the scan could not measure them: no parent call to join on, an empty return, an empty
brief, a spawn whose `requested_type` carries its cap in its own definition, or a Codex row,
whose runtime joins no return at all. A result the scan kept only the first 64 KB of records
`return_measured: "truncated"` instead, because a word count over the head of a return is not a
word count of the return. The match is a string match and the resolution an `os.path.exists`;
no model judges the return here. These rows
carry the same tokens a second time, attributed, which is why no grouping sums both them and
their session.

**`kind: "worker"`** — one row per completed `bin/citizen role run` worker, with the role name as
`agent_type`. A worker is an isolated CLI session; its runtime reports what the run cost in the
envelope or event stream the adapter already reads, and `workers.py` writes those totals into
its `status.json`. A runtime that reports none leaves the fields unknown rather than zero.
Neither runtime reports a worker's tool-call count, so `tool_calls` is unknown for workers. A
run that timed out or failed is not recorded: its total compares to nothing. A worker is
launched by name, so its `requested_type` and `tool_use_id` are null.

No row holds prompt text, command text or a brief: counts, and the identifiers `tool_use_id`
and `requested_type`, which are a tool call's id and an agent name the tool could have resolved.

`SessionEnd` hooks share a 1.5-second budget, so the hook spawns a detached worker and returns
at once. Rows are upserted by `(session_id, runtime, kind, agent_id)`, so re-reading a
transcript never duplicates one, and a subagent transcript is only ever read from its session.

### Codex rollouts

Codex is read from `~/.codex/sessions/` and `~/.codex/archived_sessions/` — `CODEX_HOME`
moves both — and it writes a subagent to a rollout file of its own rather than beside its
parent's. The `session_meta` is what tells the two apart: a top-level rollout's
`payload.source` is a string naming the front end, a spawned thread's is the object
`{"subagent": {"thread_spawn": {…}}}` carrying the parent thread id, the depth, the agent path
and a nickname. The row takes `agent_role` as its `agent_type` and falls back to
`agent_nickname`, which is what the fallback actually does today: Codex leaves the role null
and names each thread, so `--by role` groups Codex threads by nickname and the groups are
small. Only the **first** `session_meta` is this rollout's own — a thread that inherited its
parent's history carries the parent's further down the file.

**A Codex parent's tokens do not include its children's**, which is the opposite of the Claude
Code rule above, so `bin/citizen usage` sums Codex subagent rows and skips Claude Code ones. The
evidence is the corpus of 438 rollouts this was built from: of the 21 parent threads with both
a typed total and children with one, four report fewer tokens than their own children sum to,
2.0M against 30.6M in the widest case. A total that included its children could not be smaller
than them.

`input_tokens` is reported inclusive of `cached_input_tokens`, `total_tokens` is input plus
output, and `reasoning_output_tokens` is part of `output_tokens` rather than beside it — no
exception in the 349 rollouts carrying a typed split. Codex Desktop often writes a snapshot
with `total_tokens` alone and every typed field zero (85 of 107 top-level Desktop rollouts
here). That row keeps `total`, is marked `partial`, and leaves the typed fields unknown, so the
report excludes it rather than reading a real session as free.

Codex capture travels through `bin/citizen usage --rescan` rather than through the hook. The
lifecycle coordinator does register `SessionEnd`, but whether the payload Codex sends names the
rollout file has not been observed here — no Codex CLI was installed on the machine this was
measured on, and nothing in the rollouts or `~/.codex/logs_*.sqlite` records a hook payload.
The hook accepts `rollout_path` and `session_path` beside Claude Code's `transcript_path` on
that chance; the rescan is the path known to work. Run it after a stretch of Codex work.

### Ledger schema

The usage ledger and the decision log are read by releases other than the one that wrote them,
so both grow compatibly:

- **Every row names its `schema_version`**, from version 1 on. A row without it was written
  before the field existed and reads as version 0; no old row is rewritten to add it.
- **Changes are additive.** A new field is added; nothing is removed or retyped in place.
- **Readers tolerate what they do not know.** A field, a schema version or a value this release
  has never seen is carried through, never refused, so a newer writer's row reads without
  error.
- **A rename ships a fold.** `FIELD_FOLDS` in `usage-log.py` and in `decisions.py` maps each
  old field name to its new one, and every reader folds on read: an old row's value appears
  under the new name, and a row carrying both keeps the new one.

`SCHEMA_VERSION` in each of those modules is bumped with any change to what a row carries.
Version 1 is first released in v0.14.0 and carries every field that release adds,
`profile_fingerprint`, `context_attribution` and `module` among them.

## Usage feed

The usage log is read after the fact. The feed is the same measurement while the session is
still running: the `usage-feed` hook injects one or two lines of context so the orchestrator
sees what it is spending before it delegates again.

- On **`UserPromptSubmit`**, one line with the last turn's output tokens and tool calls and the
  session's own — but only when there is a turn behind it and its figures are not the ones
  already printed, because a background agent's completion arrives as a prompt of its own and
  several in a row otherwise repeat one turn — followed by one line per subagent that has
  finished since the previous prompt.
  That second part is how a background spawn is reported at all: its `PostToolUse` fires at
  launch, before the agent has spent anything. At most five agents are listed, then `… and n more`.
- On **`PostToolUse`** for a synchronous `Agent` return, one line for that subagent, on the spot.
  Any `Agent` call, a background launch included, also carries a line when more agents are
  running than `max_parallel`: `usage-feed: 7 subagents running against a posture width of 6`.
  It is a note and never a decision — the feed has no deny path and writes no permission field.
- On **`SubagentStart`** and **`SubagentStop`**, nothing is injected — a `SubagentStop` context
  would reach the agent that has just finished — but the start and the cost are recorded for the
  lines above. Running means started and not yet stopped.

A subagent's figure is summed from its own transcript, never from the tool response, which
reports only the agent's **last** response: measured at 3,143 output tokens against 10,575
actually spent. Each line names the agent type, what it spent and, when its row carries budgets,
the larger of the two ratios against them, prefixed `over budget` past a `nudge_at` multiple.

The first line that carries a figure is followed, once per session, by what the figures are:
output tokens and tool calls summed from each agent's own transcript, which is not the task
notification's `subagent_tokens`. Measured live, one agent's line said 31,121 output tokens
beside a notification's `subagent_tokens 102398`; both were right about different things.

An agent resumed with a follow-up message stops once per round, against one agent id and one
transcript. Every round is fed a line — `gatherer finished round 2 at 800 output tokens and
2 tool calls (cumulative)` — because the transcript is the agent's whole life and a later
round's figure covers the earlier ones. It stays one subagent in the session count, and only
the rise reaches the session totals.

A resumed agent's stop can fire before that round's responses are flushed, and the transcript
ends on the previous round's finished response either way — so nothing about the file says the
round is incomplete. What says it is the figure: a sum that has not passed the one already
reported is a round that has not landed. Such a round is re-summed at each following event and
nothing is said about it meanwhile, rather than a line repeating the previous round's number;
after three tries, or a transcript that has gone, it is dropped unsaid.

That sum is capped at 8 MiB from the end of the agent's transcript and at four seconds, because
it runs inside a hook's timeout. When a cap bites, the line says `(partial)`; when the sum could
not be made at all, it says `spend unknown` rather than reporting the agent at zero. Either way
the stop is recorded, because an agent whose stop went missing would count as running for the
rest of the session. A start whose stop never arrives is forgotten after three hours.

The sum is made when the agent is **reported**, not when it stops. A `SubagentStop` fires the
instant the agent ends, which can be before one of its responses has been flushed to its
transcript, and it waits for nothing because a prompt may be queued behind it — so a stop with no
figure in it, or one read out of a response still being written, is journalled as not yet summed
and is summed again on the line that names it, before the lock is taken and inside one wall-clock
budget shared by every agent that event reports. Agents that budget does not reach keep their
place and are summed at the next event. `spend unknown` therefore means a transcript that is not
there; a transcript that is there and still holds no response says `spend not yet recorded`, and
the figure it gains later reaches the session totals without the agent being named twice.

`spend unknown` names the agent it is about — `unknown finished, spend unknown, no transcript
found for agent a1b2c3` — and is fed once a session for that agent. It carries no figure and
nothing will ever reconcile it, so repeating it turn after turn, which a session whose reader
state was rebuilt used to do, only spends the orchestrator's context on a fact it has read.

A synchronous return can also arrive before the agent's last response is on disk: one API
response is written as several records, the early ones carrying a partial streaming count and
the last one a `stop_reason`. So the return polls the transcript's tail for up to a second,
waiting for that record, and the line says `(so far)` when it never comes. Whatever figure was
printed, the settled one the stop records afterwards raises the session totals — the agent is
never named a second time, and the session total is never below the sum of the final figures.

A prompt also carries one line about the session itself when its context has grown past a size
the posture calls a full session: `usage-feed: session context 120,000 tokens, past the
fresh-session threshold of 100,000 — finish the task, write the handoff, start a fresh session`.
The size is the newest response's input tokens plus the prefix it read from the cache and the
prefix it wrote into it, which is what every further turn re-reads and what a long session mostly
costs; the turn line shows none of that.

It is said once per threshold and not once per turn: a session that stays above one is silent
until it reaches the next, and a resume — or a transcript whose identity changed, which makes the
reader start over — reads the thresholds already said back out of the state file. A context that
falls back under a threshold, which is what an in-place compaction does, arms that threshold
again, because crossing it a second time is a crossing nobody has been told about. The line names
the highest threshold newly crossed, never one already fed. A context no response has reported
yet is no crossing, so nothing is said rather than a size of zero being invented. Like every
other line here it is soft: nothing is blocked.

Five settings in the active `cost` variant's sidecar govern all of it, and the hook holds no
number of its own:

- `turn_feed: "off"` — nothing is injected anywhere and no file is written.
- `turn_feed: "thresholds"` — no turn line; only subagents at or over the smallest `nudge_at`.
  An agent nothing was said about stays unreported, so a later threshold crossing can still name it.
- `turn_feed: "every-turn"` — the turn line and every finished subagent. `balanced` and `frugal`
  ship this.
- `nudge_at` — the multiples that mark a return as over budget. An empty list, which `max` ships,
  means never.
- `session_nudge_at` — the context sizes, in whole tokens, smallest first and none repeating,
  that the fresh-session line is said at. `frugal` ships 80,000 and 120,000, `balanced` 120,000 and 160,000, and `max` an empty list, which
  means never. Those figures are starting points chosen against a 200,000-token window, not
  measured ones: the follow-up to #321 replaces them with sizes read out of the ledger.
- `max_parallel` — the width the running-agent note measures against. `null`, which `max` ships,
  means the note never appears.

### State, and why it is two files

These hooks are separate processes that run at the same time: tool calls go out in parallel and
several agents finish at once. So the state is split, both files 0600 in a 0700 directory under
`~/.local/state/agent-harness/feed/`.

- `<session-id>.events.jsonl` is append-only. A subagent starting or finishing is one line under
  4 KB written with a single `os.write` on an `O_APPEND` descriptor — an atomic append no handler
  ever rewrites, so no record can be lost to a concurrent one.
- `<session-id>.json` is the main thread's reader state: the transcript offset, the journal
  offset, the running totals, the open message ids, the agents still in flight and the finished
  ones not yet named. Everything that reads and then writes it does so under an exclusive
  `flock` on `<session-id>.lock` with a two-second bound. No lock, no write, and nothing said.

Nothing slow ever happens while that lock is held. `SubagentStart` and `SubagentStop` never take
it — they append and exit — and the two main-thread events sum a subagent's transcript before
acquiring it. A four-second sum under the lock would starve the prompt waiting behind it, and
that prompt would lose its line in silence.

Both files hold counts and agent type names only — no prompt text, no command text, no agent
output — and an agent type that is not a plain name is recorded as `other`. A session's files
are swept once a day, together and only when the newest of them has gone a fortnight untouched,
never the running session's; `bin/citizen uninstall` removes the directory.

The two offsets are what keep the hot path cheap: each prompt reads the transcript and the
journal from where it left off, so a long session's subagent total can only ever grow.
It is trusted only while the file is the same file, which the inode and a hash of the first
record decide, so a transcript replaced by a *larger* one resets exactly as a truncated one does.
With no usable state the read starts 8 MiB from the end rather than at byte zero, because a
resumed session's transcript runs to hundreds of megabytes and a hook killed at its timeout
would stall every prompt after it. Any read that skipped content, or that ran past its
three-second budget, marks the totals `(partial)`.

Codex raises none of `UserPromptSubmit`, `SubagentStart` or `SubagentStop`, so the feed is
declared uncovered there in `adapters/codex/capabilities.json`; posture still reaches Codex
through role-run workers.

### The session registry

One more directory sits beside the feed's, `~/.local/state/agent-harness/sessions/`, written by
session start and by a spawn that learns of a reload rather than by any measurement: one small
file per session naming the agent
definitions that session's registry held, which is the floor under whether an unnamed spawn can be
routed to a band worker — see [runtime controls](runtime-controls.md). It holds agent names and a
timestamp, nothing about the work; the files are owner-only in an owner-only directory, swept
after a fortnight of not being used, and removed by `bin/citizen uninstall`.

### Adherence events

The fresh-session line is a recommendation, so saying it also appends an `emitted` row to
`~/.local/state/agent-harness/adherence.jsonl`: the recommendation, the module that said it
(`hooks/usage-feed`), the session id, the turn it was said on and the profile fingerprint. A
`response` row joined to it by `adherence_id` later says `followed`, `not_followed` or
`unknown`, and `policy/hooks/adherence.py` computes a rate per recommendation from the two. No
row holds a prompt, a tool call or the line's own text, and recording never changes what the feed
says: a ledger it cannot write is skipped in silence.

The response is read from the observation ledger (`observation.jsonl`). A session that ends
within three prompts of the line followed it; one that carries on past them did not. Until the
observation entry point is registered in live sessions, that ledger holds no rows, so every
emission is answered `unknown` with reason `unobserved` once it is a day old. Each session start
writes the answers that are due, one per emission, and says nothing about them.

## The decision log

`~/.local/state/agent-harness/decisions.jsonl`, beside the ledger and written by the same
hooks, holds **one record per judgment a hook makes** and a second record per judgment the
session later settled. The ledger says what a run cost; this says what the harness decided and
whether the decision held. It exists so that replacing a heuristic — with a better pattern, a
classifier, anything — is measured against labels the harness already produces and used to
throw away. Nothing here is exported, nothing here is model-visible, and the file never grows
a context token.

```json
{"kind": "decision", "decision_id": "e38a…", "point": "grade-bash", "session_id": "s-1",
 "ts": "2026-09-21T19:41:05Z", "input_sha256": "d20c…", "input": "git push --force origin main",
 "deterministic_answer": "ask", "outcome": null, "runtime": "claude-code",
 "harness_version": "0.12.0", "profile_fingerprint": "5f1c…", "module": "hooks/grade-bash",
 "schema_version": 1}
{"kind": "outcome", "decision_id": "e38a…", "point": "grade-bash", "session_id": "s-1",
 "ts": "2026-09-21T19:41:22Z", "outcome": "ran", "harness_version": "0.12.0",
 "profile_fingerprint": "5f1c…", "module": "hooks/grade-bash", "schema_version": 1}
```

`module` names the hook that owns the decision, as `hooks/<id>`: `grade-bash`, `stop-gate` and
`brief-guard` their own, and the band routing row and the `integration-descriptor` notice
`hooks/tier-agent-spawns`. Role confinement, `framework-spawn` and evasion refusals and the Workflow
launch guard name `null`, because no hook id switches them off, and so does any other point no
hook owns, such as `decision-provider`.
`POINT_MODULES` in `decisions.py` is the map.

The file is **append-only**: an outcome is its own record, joined to its decision by
`decision_id` when the report reads it, and no line is ever rewritten. `input` is the text the
hook judged, capped at 2 KiB; `input_sha256` is over the **uncapped** text, so the cap loses
evidence and never identity. No tool output and no assistant prose reaches either field; the
one field that holds prose is [the completion claim](#the-completion-claim), which is off.

| point | the judgment | the outcome, when there is one |
| --- | --- | --- |
| `grade-bash` | the permission answer, `ask` or `deny` | `ran` when the command's PostToolUse arrives, `not_run` when the session ends without one |
| `stop-gate` | `blocked`, `released` or `skipped` | the gate's own result: `passed`, `failed`, `timeout`, `unverified`, `untrusted` |
| `tier-agent-spawns` | the band worker an unnamed spawn was routed to | not labelled yet |
| `brief-guard` | what was appended: `cap`, `budget` or `cap+budget` | not labelled yet |
| `evasion-deny` | `deny`, on a re-spawn of already-refused work | not labelled yet |
| `role-confinement` | `deny`, on a native spawn naming a constrained role, by `subagent_type` or a `harness-role:` line; `input` leads with the role and which of the two named it | not labelled yet |
| `framework-spawn` | `deny`, when a framework descriptor maps a spawn to a constrained role | not labelled yet |
| `integration-descriptor` | `ignored`, when an integration descriptor cannot be loaded; recorded with the session's notice | not labelled yet |
| `governance` | the governance permission answer, `allow`, `ask` or `deny`; protected configuration writes and unavailable providers produce `ask` | not labelled yet |
| `workflow-launch` | `allow` or `deny`, on every `Workflow` tool launch | not labelled yet |

An approved Bash command is not *graded*. The harness answers the permission question on a small
minority of calls, and "it ran" says nothing about whether declining to interrupt was right; a
prompt or a refusal is the judgment a label can grade. A sample of the approvals is kept all the
same, as [sampled allows](#sampled-allows) below, which carry no outcome. `not_run` is
deliberately not called "denied": a user who refused, a user who interrupted the turn and a
session that crashed all look the same from a hook, and naming one of them would put a label in
the file that nobody measured.

Both runtimes write, for the events both raise. Band routing happens on Claude Code alone, so
Codex records no `tier-agent-spawns` row; `adapters/codex/capabilities.json` names that gap.

A write that fails is counted and swallowed — a log that can change a permission answer is
worse than no log — and `telemetry.decisions: false` in `config.json` turns the whole thing off,
after which no row, no file and no directory is written. See
[telemetry.md](telemetry.md#the-decision-log-switch).

### Sampled allows

One Bash command in twenty that the harness **allowed** is written as a `grade-bash` row of its
own:

```json
{"kind": "decision", "point": "grade-bash", "deterministic_answer": "allow", "sampled": true,
 "sample_rate": 20, "input": "cargo test --release", "outcome": null}
```

They exist because the graded rows are all prompts: a check that may only tighten an allow into
an ask has nothing to measure its false alarms against without the commands nobody was asked
about. They are **negatives, not judgments** — `sampled: true`, never an outcome, passed by at
SessionEnd rather than closed as `not_run`, and counted by `usage --by decision` on a
`grade-bash (sampled)` line of their own so they cannot dilute the outcome rates of the graded
rows.

Only an allow the harness actually gave is sampled. A command it answered nothing about is the
runtime's own to decide and may still be prompted on or refused, so it is no evidence of an
allow and no row: that covers a grade-1 command under the `execute` stance, and every command
on Codex, where a plain approval is dropped from the hook output and the client's own default
stands. A confirmed command — one re-run with the confirmation marker after a prompt — is not
sampled either; it belongs to the `ask` row that prompted it.

Which commands are sampled is the **command text's own hash**, not a random draw, so the same
corpus samples the same commands and a measurement over these rows is reproducible. That makes
the sample one of **distinct commands, not of invocations**: a command in the sample is logged
every time it runs and one outside it never is, so the row count says how often those particular
commands ran and multiplying it by `sample_rate` estimates nothing. Read it as a corpus to
replay a candidate check over, which is what it is for.

The `input` of a sampled row is **redacted**, unlike the text of a prompt the user was shown:
the value of every assignment and every credential flag, quoted or not (`FOO=…`, `--password=…`,
`--token …`, `-p…`), every secret shape the [rule detectors](#rule-telemetry) match, and the home
directory written `~` so no username reaches the row. `input_sha256` is over the **redacted**
text on these rows and over the original on every other row: the hash of an original next to the
redacted text would put a short secret within reach of a dictionary attack.

`telemetry.allow_sample_rate` sets the rate and `0` stops it;
[telemetry.md](telemetry.md#the-allowed-command-sample) has the switch, and `decisions: false`
turns it off with everything else.

### The completion claim

One optional pair of fields is the exception, on a `stop-gate` row alone:

```json
{"kind": "decision", "point": "stop-gate", "deterministic_answer": "blocked",
 "completion_claim": "…the suite is green and the change is ready to land.",
 "completion_claim_sha256": "9f21…"}
```

`completion_claim` is the **last 2 KiB of the turn's final assistant message**, in bytes and cut
back to a character boundary, read from the transcript the Stop event names because a Stop
payload carries no assistant text of its own. It is the turn's own message: the scan stops at
the user prompt that opened the turn, so a turn that ended in a tool call rather than a reply
claims nothing instead of borrowing the previous turn's words. `completion_claim_sha256` is over
the uncapped message, on the same rule as `input_sha256`. The two fields exist so that a stop
claim can be read against the gate evidence sitting on the same row.

It is **off by default** — `telemetry.completion_claim` in `config.json`,
[telemetry.md](telemetry.md#the-completion-claim-switch) — and with it off the row is exactly
the row above, with none of these fields present.

With it on the row always says something. Where there is no claim to record, it carries
`"completion_claim": null` and a `completion_claim_miss` naming why, and no hash:

| `completion_claim_miss` | what happened |
| --- | --- |
| `no_transcript_path` | the Stop event named no file — the runtime's gap, not the session's |
| `unreadable` | the file was named and could not be opened |
| `oversized` | the file is past 256 MiB, which this hook will not seek into |
| `no_claim` | the turn was read and ended without assistant prose |
| `error` | the reader raised; the decision is still recorded |

The read is the last 256 KiB of the file, so it costs the same on a transcript of any size — a
claim older than that window reads as `no_claim` rather than as the wrong turn's words.

```sh
bin/citizen usage --by decision        # counts, outcome rates and the unlabelled share per point
```

The **unlabelled share** is the column to read first: an outcome rate over the two decisions
that happened to be labelled is not evidence about the point.

An `intent-overlap` row is an edit the write-intent check warned on or denied, and `bin/citizen intent
merge` writes one row per landing saying whether bringing in the base branch conflicted.
`coordination.repeat_overlap` in `config.json` chooses whether a repeated overlap is denied
(`deny`, the default) or only warned (`warn`); `bin/citizen intent --help` has the commands.

```sh
bin/citizen usage --conflicts          # landing merge conflicts and intent overlaps per week
```

`bin/citizen decisions eval` replays the labelled rows of this file through a question pack and
reports how closely the judgment tracked them, with a threshold fitted per decision point. What
it measures, what it writes and what its labels do not prove are in
[runtime controls](runtime-controls.md).

## What a decision provider cost

A provider that leaves the machine spends tokens and wall-clock time, so each call it makes
writes one `kind: "decision"` row into the usage ledger beside the session rows, and the report
prices it from the same table:

```sh
bin/citizen usage --by provider        # calls, statuses, tokens, dollars and latency per point
```

The row names the decision point, the mode it ran under, the status, the requested and returned
model ids, the pack and request hashes, the token counts, the latency in `ms` and the session
that asked. It never carries the outbound state, an answer's prose, a prompt, a file path or an
environment value: `lib/harness_core/decisions/ledger.py` builds it key by key from that list and
reads nothing else. The counterparty is kept only when it matches the `repo:<name>/<branch>` slug
the ledger already derives, within a bounded length, and as a short digest otherwise — it is a
caller's string and part of what went out, so a path must not survive in a row kept for months.
A row that could not be written at all is recorded in `usage.errors.jsonl` beside the ledger,
by exception type and never by message. What may leave the machine at all is a separate
question, answered in [runtime controls](runtime-controls.md).

Four things are worth reading off it:

- **The statuses are separate columns, not a success rate.** `unknown` is an answer the provider
  gave and abstained from; `unavailable` is no answer at all. A rate that mixed them would say
  the provider was working when it was unreachable.
- **A call whose usage nobody reported is `partial`,** counted in the `unpriced` footer and
  contributing nothing to the dollar column. It was not free; nothing knows what it cost.
- **A model the price table does not name is unpriced too.** No price for the Jev models has been
  read from a vendor pricing page, so the dollar column is empty until one is added to
  `policy/prices.json` or overridden under `prices` in `config.json`.
- **The returned model id may differ from the requested one,** which is why both are on the row:
  a report priced at the model the harness asked for would be priced at the wrong rate.
  `bin/citizen doctor` prints the pinned model and what answered last.

These rows are counted on this report alone. Their tokens were spent by the harness asking a
question rather than by the session, so adding them to a day, a repo or a model grouping would
charge a session for a bill it did not run up.

## Reading it

```sh
bin/citizen usage                      # last 30 days, grouped by day
bin/citizen usage --days 7 --by repo
bin/citizen usage --by model           # a session using two models groups under both, joined
bin/citizen usage --by role            # per agent type: runs, p50/p75/p90 output, p50/p75 usd
bin/citizen usage --by stance --stance cost   # tokens per variant of one stance dimension
bin/citizen usage --by profile         # tokens per profile fingerprint; older rows unattributed
bin/citizen usage --by decision        # hook decisions and their outcomes, above
bin/citizen usage --by provider        # decision-provider calls, priced, above
bin/citizen usage --rescan             # re-read transcripts in the window first, then report
```

`--by role` reads the subagent and worker rows. A row with a `workflow` directory is grouped under
`(workflow)`, printed last and named in the footer, rather than under its `agent_type`, including
a row written before the ledger marked such rows `unconfined`. Spend per delegated task is a distribution, not
a mean, so it prints three points on the curve; `unmeasured` counts the runs whose runtime
reported no tool-call figure, which are named there rather than averaged in as a zero. A role
with fewer than 30 runs is marked `n<30` in the `sample` column: a p90 over eight runs is the
second-largest of eight, and a budget re-seeded from it is a guess wearing a number.

`path` and `over` are the returns themselves: of the returns that named a path, the share whose
path resolved; and of the returns measured against a cap, the share that ran past it. A return
that named no path is in neither figure, so a role whose returns are all one-line verdicts
prints `-` for `path` rather than `0%`, which would read as a role that wrote paths and got
them all wrong. A row written before the measurement existed, a Codex row and a return nothing
could be joined to print `-` too.

`--by day` reads a row's `days` slices when it carries them and falls back to its end date when
it does not, so a session that ran for a fortnight is spread over the days it spent on. The
`--days` window then applies to the **slice** date, and a long session contributes only its
in-window days. The `runs` column still counts sessions, not session-days: a row is counted
once, on the day it ended, so a session whose end date is outside the window contributes its
in-window tokens and no run.

`--by stance --stance <dimension>` groups tokens by that dimension's variant — `cost=balanced`
against `cost=frugal`. A row with no recorded stance, and a row whose stances a rescan stamped,
group under `(unknown)` and are **counted there** rather than dropped: leaving them out would
make a newly stamped variant look like the whole history of the ledger. `--rules --by stance`
is the hit report below and is unchanged. `--by stance` with neither is refused, since it names
two different reports and guessing between them would be worse than asking.

`--by profile` groups tokens by `profile_fingerprint`, so two profiles are told apart by their
rows alone. A row that carries none groups under `(unattributed)` and is counted there. The
fingerprint is 64 characters and the label column 34, which still tells profiles apart.

The token groupings — `day`, `repo`, `model`, `stance`, `profile` — sum session and worker rows and never a
subagent's. A subagent's tokens are already inside its session's total; a role-run worker has
no session row at all, so leaving it out would hide its spend in every report there is.

A session that crashes or is killed never fires `SessionEnd` and so is never recorded live;
`--rescan` walks every transcript touched inside `--days` and upserts it, which is how you fill
those gaps.

## What it cost

Tokens mislead as a measure of spend. Cache reads dominate the count and cost a fraction of base
input, and a change that routes work to a cheaper model can spend more tokens and fewer dollars.
So every token grouping carries a `usd` column, and `--by role` carries p50 and p75 dollars
beside its output percentiles.

These figures are list-price API equivalents computed from token counts, not an invoice: a
subscription plan pays differently, and fast-mode and data-residency multipliers are not
modelled.

The rates are in [`policy/prices.json`](../policy/prices.json): USD per million tokens for input,
output, cache read and cache write, per model id, each entry carrying the `as_of` date it was
read and the provider pricing page it was read from. Nothing in the file is written from memory,
and a model whose price could not be confirmed from a primary source is absent rather than
guessed. Override or extend it under `prices` in `config.json` — see
[preferences.md](preferences.md).

- **Ids resolve by exact match** after normalisation, which lower-cases, drops a cloud vendor
  prefix, drops a context-window suffix and drops a release suffix — a date stamp, a reseller's
  `-v1:0`, an `@date`. So `claude-haiku-4-5`, `anthropic.claude-haiku-4-5-20251001-v1:0` and
  `claude-opus-5[1m]` all reach one family entry. Long context is not a separate rate: Anthropic
  prices the full 1M-token window at the standard rate for Claude 4.6 and later. Nothing
  resolves by prefix: a variant the table does not name is unpriced, not billed at its family's
  rate — `gpt-5.5-pro` is $30/$180 where `gpt-5.5` is $5/$30. To price one, add it to
  [`policy/prices.json`](../policy/prices.json) or override it under `prices` in `config.json`.
- **Cache writes are priced by TTL.** Anthropic charges 1.25x base input for a 5-minute write
  and 2x for a 1-hour one, and Claude Code reports the split under `cache_creation`, so a row
  records `cache_write_5m` and `cache_write_1h` beside its `cache_write` total and each tier is
  charged at its own rate. Both keys are additive: a row written before they existed carries
  neither and is charged whole at the 5-minute rate, which **understates** it wherever the
  session's writes were 1-hour — which the main thread's are.
- **Codex is counted once.** `input_tokens` is inclusive of `cached_input_tokens`, so the ledger
  row already holds the difference and the cached part is charged at the cache rate alone;
  `reasoning_output_tokens` is inside `output_tokens` rather than beside it, so it is never
  added again. OpenAI publishes no cache-write rate, so that column prices at zero for a `gpt`
  entry.
- **A subagent is priced at its own model.** A Claude Code session's totals already include its
  subagents', which ran on other models at other rates, so their tokens come off the parent's
  totals, each is priced at its own model and the two are added. A session whose totals do not
  cover its children — a row written before subagent capture landed — is priced alone, exactly
  as its tokens are reported alone.
- **A session that switched models carries a breakdown.** The largest sessions are the ones
  that changed model, and their totals alone name several rates with no split between them, so
  a row records `by_model`: token counts per model id, cut from the same map the totals are
  summed over for Claude Code and from the snapshot deltas under each `turn_context.model` for
  Codex, and dropped whole if it does not add up to the row. A row that carries one is priced
  from it and from nothing else; a multi-model row written before the map existed stays
  unpriced. A part named for a harness-generated turn — `<synthetic>` — is skipped when it spent
  nothing and unprices the row when it did not.
- **Unpriced is not free.** A row with an unknown model, a multi-model row with no breakdown,
  and a row marked `partial` are all counted in the `unpriced` footer and contribute nothing to
  the column. An understated dollar figure is worse than an absent one, because nothing on the
  line says it is short. A role-run worker whose row names a model alias rather than an id —
  `opus`, `fable` — is unpriced for the same reason, and so is a variant of a listed family
  that the price table does not name.
- **A day slice holds tokens and no model**, so a multi-day session's cost is allocated across
  its days by each day's share of its tokens. For a single-day session, which is nearly all of
  them, the share is one and the allocation is exact; for a long one it is an allocation and not
  a measurement.

The table was checked against a runtime that reports its own figure: a recorded Claude Code
session and its subagent, whose CLI-reported `total_cost_usd` was $0.60097775, prices to
$0.60097775 — a 0.000% deviation, against the 2% the report is held to.
`tests/test_usage_prices.py` holds that session's token shape as a fixture.

The same figures ride on an exported row as `harness.usd`, computed by the same code — the CLI
and the export hook both load `policy/hooks/pricing.py` rather than either one holding a second
copy of the rates. What an exported dollar figure means is in [telemetry.md](telemetry.md).

Prices go stale silently while the report keeps printing dollars, so `bin/citizen doctor` names the
newest `as_of` in the table and warns when it is over 90 days old. Re-read each entry's `source`
and update the file; that is the whole maintenance cost, and it names a real failure mode.

### Re-seeding budgets

A cost variant's per-role budgets are measured, not guessed, so they go stale as roles change.
Run `bin/citizen usage --rescan --by role` over a window wide enough to hold a few dozen runs,
read the p75 column for the role — the shipped figures are that point on the curve — and write it
into your variant's row as `budget_output_tokens` and `budget_tool_calls`. A role still marked
`n<30` has not earned a re-seed; widen the window or leave the figure where it is.

## What the hit rate tells you

`hit` is `cache_read / (input + cache_read + cache_write)` — the share of the prompt served from
cache rather than paid at base input rate. `claude/skills/delegation-tiering/SKILL.md` puts
caching in Gate 0: it is an orchestrator lever, not a subagent one, because a subagent starts a
fresh prefix sharing no cache with its parent and parallel fan-outs with identical prefixes each
pay full price. So a repo whose hit rate falls as its `subagents` count rises is paying for
delegation twice; check the return caps before you reach for a different tier. That fall did not
show up in the only corpus it has been measured against: across 137 sessions on one machine the
hit rate was 97.0% at zero subagents, 97.2% at 1–6, 97.3% at 7–50 and 97.1% at 51 or more — so
treat the sentence above as a thing to check in your own data rather than as an expectation.

### Whether the prefix held

`hit` says how much of the prompt was served from cache. It does not say whether the cached
prefix survived the session, and that is the thing `primitives/rules/cache-hygiene.md` actually
asks for: a mid-task change to the tool set, the MCP server list, the model or the effort dial
turns the next turn's cache reads into cache writes, and the only visible symptom is a larger
bill. `bin/citizen usage --by prefix` reports the miss ratio per session:

```
miss = cache_write / (cache_read + cache_write)
```

The share of the prefix the provider had to re-write rather than serve, computed from the two
fields every row already carries. No new hook, no new event, nothing recorded that was not
recorded before. A low ratio is a session that kept one prefix; a high one is a session that
bought its context again.

**What the figure excludes: its subagents.** A Claude Code session row folds its subagents'
tokens into its own, and every spawn writes a fresh prefix that shares nothing with its parent,
so a session that held its context perfectly across six fan-outs would read as one that re-bought
a quarter of it. The subagent rows' own `cache_read` and `cache_write` are subtracted from the
session's before the ratio is taken, and the report's columns are headed `own_read` and
`own_write` for that reason. A session whose `subagents` count is higher than the subagent rows
found for it, or whose subtraction goes negative, reports `unknown`: the remainder would not be
its prefix. A Codex session folds nothing in and nothing is subtracted.

The report also names where the ratio stepped. A row's `days` slices carry the same cache
fields and that day's turn count, so the ratio is recomputed per slice and the first turn of a
slice that rose by twenty points or more is printed as `turn 7 (2026-09-19): 2% -> 80%`. A
session that stepped twice reports the sharpest rise, not the first. A slice that cannot state a
ratio breaks the chain rather than being compared through, so `before -> after` is always one
slice against the slice before it. That is the finest index the ledger can support honestly:
turn-level cache figures are not recorded, and inventing an event to record them was out of
scope. A session with a single day of slices, or none, reports its ratio and no step.

**A session that spawned anything reports no step at all**, printed as `not measurable
(subagents)`. Its slices fold the same subagent tokens in per day, a subagent row carries no
`days` map to subtract, and the only step those slices could show is the fan-out day.

**It measures, it does not enforce.** Nothing denies, warns on or blocks a prefix change, here or
anywhere else in the harness; the figure is retrospective and read-only, and what to do about a
step is the session's call.

A session whose rows carry no cache fields reports `unknown`, never zero, and so does every
Codex session: that runtime reports a cached-read figure and no cache-write figure at all
(`adapters/codex/capabilities.json`), so no ratio over its pair means anything. A zero would read
as a perfectly held prefix, which is the opposite of what the row knows. On a runtime that does
report writes, cached reads against zero writes are not unknown but the best case there is: a day
that served its whole prefix. The footer counts the unknown sessions separately.

### Which calls rebuilt the prefix

`bin/citizen usage --by rebuild --days 30` reads local Claude Code main-session transcripts and
attributes each prompt-cache rebuild to its first matching observed cause: model switches,
compaction, idle expiry, client-version changes, transcript events, slash commands or an
unexplained remainder. A call is a rebuild when its cache read is at least 20,000 tokens short of
the preceding call's input, cache read and cache write total. The report shows both all sessions
and long sessions with at least 200 calls, with breaks, rewritten tokens, known spend share and
cost per break.

This report reads transcripts directly; it does not add a hook or write per-call data to the usage
ledger, so `--rescan`, `--rules` and `--stance` are refused with it. Main-session files reached
through more than one path are read once. Subagent, role-worker and Codex sessions are excluded,
and so are client-generated `<synthetic>` turns, which made no request: the next real call is
compared with the last real one. Unpriced models, malformed lines, unreadable files, duplicate
requests, synthetic turns, valid untimed records and calls outside the window are counted
explicitly instead of being treated as zero. A cause with any break on an unpriced model has no
dollar figure: its excess, share and cost per break are `null` in `--json` and read `unpriced` in
the table, with the count of its unpriced breaks beside them. A call whose usage reports no
one-hour/five-minute split is priced whole at the base write rate, as ledger rows are, and is
never attributed to the five-minute TTL; an idle gap of 5 to 60 minutes then reads as an event
cause or `idle 5-60 min, no event`.

Attribution depends on undocumented Claude Code transcript markers. The fixtures that pin them are
synthetic transcripts stamped with client version 2.0.20 (2.0.21 on the far side of the version
change); no other client version is covered. If a client changes or removes a marker, the break
remains in the report as `unexplained`; the report does not infer a cause that the transcript did
not record.

## Rule telemetry

The engine underneath — the event schema, the shell decomposition, the registry and the six
generic detectors — is [ruleprobe](https://github.com/JakeSelby/ruleprobe), vendored as a wheel
in `lib/vendor` beside `tomlkit`; `claude/hooks/rule-detectors.py` is this repository's rule
pack over it, holding the detectors that are about these rules and the opt-outs.

The same pass that sums the tokens builds the event list `claude/hooks/rule-detectors.py`
documents and runs every detector over it, so the record carries three more fields:

- `rules` — `{detector id: hits}`, detectors with no hit omitted. A hit is a count, never a
  snippet: `usage.jsonl` holds no command text and no message text.
- `counts` — `web_search`, `agent` and `ask_user` tool calls, recorded whether or not a
  detector fires, because the capped ones are worth watching below their cap.
- `stances` — the resolved `dimension: variant` map, so a hit can be read against the stance
  that was in force. The ladder is the CLI's: built-in defaults, then
  `~/.config/agent-harness/config.json`, then `HARNESS_STANCE_<DIMENSION>`, which every hook
  inherits from the session.

A registry that is missing, broken or a version apart costs the record its `rules` and `counts`
and nothing more: the record is written with `rules_error` naming the failure. A record with no
`rules` map is a gap, not a session without hits — it is left out of every count and out of the
share's denominator, and the report says how many there were.

A rescan cannot know the stances a past session ran under, only this minute's. It leaves a
record's `stances` alone when it has them and otherwise stamps the current ones with
`"stances_source": "rescan"`, which `--rules --by stance` then excludes by name and the token
report groups under `(unknown)`. The stamped stances still drive the detectors, so hit counts
do backfill.

### What each detector looks for

| Detector | Rule | Hit on |
| --- | --- | --- |
| `transcript-hygiene/whole-file-cat` | transcript-hygiene | a lone `cat <one path>`: no pipe, no filter, no heredoc, no redirect |
| `transcript-hygiene/unfiltered-find` | transcript-hygiene | `find <dir>` with no filtering predicate and nothing consuming its output |
| `transcript-hygiene/model-wrote-no-cap` | transcript-hygiene | an `Agent` brief the model wrote with no word cap, for an agent whose definition carries none |
| `delegation/executed-from-summary` | delegation | a Bash command whose first appearance in the session was inside an `Agent` return |
| `verification/no-verify` | verification | a commit or push that walks past the repository's own hooks |
| `secrets/secret-in-write` | secrets | a secret-shaped string written to a file or into a heredoc body |
| `secrets/git-add-secret-file` | secrets | `git add` of a path whose name says it holds a credential |
| `research/search-over-cap` | research-and-verification | the web search that takes the session past the per-session cap |
| `cache-hygiene/model-switch` | cache-hygiene | a model change mid-session, which rebuilds the cached prefix |
| `cache-hygiene/compact` | cache-hygiene | each compaction boundary in the transcript, except under a `cost` variant that allows compaction (`max`) |
| `voice/banned-opener` | voice-and-format | a final message opening with a phrase the output style bans, or closing with one |
| `voice/second-table` | voice-and-format | two or more table blocks in one final message |
| `voice/scaffold-leak` | voice-and-format | under the `concise` voice, a final message wearing a reply template's section labels |
| `voice/heading-first` | voice-and-format | under the `concise` voice, a final message whose first line is a markdown heading |
| `decisions/no-alternatives` | decisions-and-plans | a final message whose recommendation line names no other course |
| `autonomy/confirmed-irreversible` | autonomy | a command re-run behind the `HARNESS_CONFIRMED=1` marker |
| `autonomy/denied-by-grade` | autonomy | a Bash result carrying the grade hook's deny signature |
| `commits/non-conventional` | commits | a commit subject that is not a Conventional Commit line |
| `commits/missing-trailer` | commits | a commit message with no `Co-Authored-By:` line |

A rule with nothing a transcript can decide opts out by name in `OPT_OUT`, with the reason;
`bin/citizen lint` fails on a rule file that has neither a detector nor an opt-out.

Every detector reads a tool call as the model wrote it. A transcript records the model's
`tool_use` input, while a `PreToolUse` hook's `updatedInput` is written to a separate
`attachment` line the scan does not read, so no detector can see what a hook delivered.
`transcript-hygiene/model-wrote-no-cap` is named for that: it counts briefs `brief-guard` went
on to cap, and a hit is the orchestrator's omission and not an uncapped brief reaching a
subagent. It was called `transcript-hygiene/brief-without-cap`, which read as the second thing;
`rule-detectors.RENAMED` maps the old id to the new one. The ledger file is never rewritten for a
rename — `--rules` folds that map as it reads, in all three groupings — so a record written under
the old id reports under the new one and the series does not split.

### Reading the report

```sh
bin/citizen usage --rules                      # hits per detector over the last 30 days
bin/citizen usage --rules --by repo            # sessions, hits and the top three per repo
bin/citizen usage --rules --by stance          # the same, per dimension=variant
bin/citizen usage --rescan --days 30 --rules   # backfill from the transcripts, then report
```

The groupings and the two annotations are in [which rules fired](#which-rules-fired) above.
Every registry id gets a line, including the ones with no hit, so an unobserved rule is visible
rather than absent.

Two shipped features this loop caught — a stance measured as never firing and a hook whose metric
did not move — are written up with their figures and commands in
[caught in the act](caught-in-the-act.md).

Those two are the ends of one ladder. A rule that trips in most sessions is prose that failed:
the agent read it and walked past it anyway, so it wants to be a hook, where the decision is
made for it rather than asked of it. A rule unobserved for a month is either kept honestly by
the model or unobservable from here, and either way it can leave the 200 always-loaded lines
and live in the skill that explains it.

# Run a shared role with constrained authority

Read-only and planner roles run as separate native CLI processes. They use the same definitions
under `primitives/roles/`, selected stances and runtime model bindings. They do not appear as
native subagent threads. Native role projections remain discoverable, but the lifecycle adapter
rejects direct native launches of constrained harness roles when its hooks are active.

That rejection does not depend on the name a spawn chose. When the guard refuses a spawn that
named a constrained role, it remembers the role and a normalised fingerprint of the brief in the
session's record (the newest 32); a later spawn in the same session that names no constrained role
but carries the same brief — identical, containing its first 400 normalised characters, or 85
percent similar — is refused too, and told that dropping the role name changed nothing. Separately,
a brief may declare its own role with a line of the exact form `harness-role: <role>`, standing
alone, naming a role under `primitives/roles/` with read-only or artifact-write authority. A spawn
whose prompt carries such a line is refused whatever `subagent_type` it names or omits, and
`citizen role run` accepts the line in a `--prompt-file` unchanged. A marker naming anything else
is ignored. Both guards are best effort: session state that cannot be read or written means no new
refusal, never a failed hook, and `delegation: off` keeps its own single refusal.

A Claude Code `Workflow` script's `agent()` calls never reach the spawn hooks, so the guard reads
the launch instead: the script sent inline, the file at `scriptPath`, or a named workflow under
`.claude/workflows/` in the working directory or the home directory. A script that names a
constrained role as a quoted `agentType`, carries a `harness-role:` marker for one, or computes
`agentType` beside a string literal naming one is refused with the same instruction, and so is a
script file longer than the 1 MiB the guard reads; `delegation: off` refuses every launch.

The guard cannot route a script's other agents to a band, so it reads the `model` and `effort`
their calls name. Under `delegation: tiered`, where a model maps to a class through the adapter's
model table, a literal model of the `frontier` class is refused, the one cost refusal a hook
makes. Anything else above the active cost variant's ceiling (the strongest class and the highest
effort any of its rows grants, never `frontier` and never above `high`) is let through, and the
row reads `over-ceiling`: an effort such as `xhigh` or `max`, or a class above a custom variant's
strongest. A computed value, a shorthand property, a model the table does not hold or an effort
outside `low` to `max` cannot be judged, so the launch goes ahead and its row reads `unresolved`.
Each launch is one `workflow-launch` row in the decision log. A built-in
workflow or a resumed run carries no script for the guard to read.

## Run and inspect

Write a bounded brief naming the input files, required result shape and allowed scope, then run:

```sh
citizen role run reviewer --runtime codex \
  --workspace /path/to/worktree --prompt-file /path/to/brief.md
citizen role run planner --runtime claude-code \
  --workspace /path/to/worktree --prompt-file /path/to/brief.md --artifact proposal.md
citizen role status
citizen role status <worker-id>
```

`--prompt-file -` reads the brief from stdin. `--read-dir /path/to/artifacts` grants access to
additional input directories, such as the framework checkout or review artifacts outside the
implementation worktree. It never grants writes, and it refuses `/`, the home directory, a system
temporary root such as `/tmp`, and any directory above one of them, because each holds other
runs' files; grant a dedicated subdirectory, such as one made by `mktemp -d`, instead. Input
prompts and results are capped at 1 MiB.
These are declared input roots, not a confidentiality boundary: Codex's read-only sandbox can
read other native-permitted paths. Claude's restricted file tools use the supplied directories.
The default deadline is 300 seconds; `--timeout` accepts 1–3600 seconds. Interrupting the runner
or reaching its deadline terminates its process group and prevents artifact publication.

## What a worker loads

A worker carries the shared instructions, the rules, the resolved stances and its own role body
as its system text — about 4,200 estimated tokens for a review role. Beside that it is mounted
only what the policy it was just given tells it to open: the skill directories that text names,
and copies of the `docs/*.md` files it cites, taken from the resolved text itself so a stance
that stops citing a skill stops paying for it. A role may add what its body assumes but the
shared text never names, with a `skills:` line in its contract — `design-loop` for
`design-judge`, and `all` for `planner`, whose body tells it to read the skills the plan will
name. A `skills:` name that resolves to no shipped skill fails the run.

The harness checkout itself is no longer one of those roots. How much that is worth depends on
the runtime, in the sense the compatibility table's tier-restriction row uses:

- **Claude Code — enforced.** The restricted `Read`, `Grep` and `Glob` tools resolve against the
  supplied `--add-dir` roots, and the checkout is not among them.
- **Codex — advisory.** Its read-only sandbox can read any native-permitted path, so the narrowing
  is instruction text, as the declared input roots above already are.

`status.json` records the estimate under `context`: policy tokens, reference tokens and their
total against a 50,000-token budget, counted with the same characters-per-token approximation
`citizen lint` uses on always-loaded context. A review role resolves at about 30,800 and the
planner, which may read any skill, at about 43,800. The budget is recorded, not enforced: what a
worker is shown is fixed by its contract and the policy's own pointers before any brief is read.

Every figure here measures what is **mounted**, not what a run reads; a worker opens what its
brief needs and usually far less. On that measure a review role went from the whole checkout —
about 1,073,900 tokens of text, since `--add-dir` took the repository root — to about 30,800, and
the skill corpus it was pointed at as authority went from all 31,600 tokens of it to the 26,600
the policy actually cites.

A role's class (`tier:` in its contract) resolves through the adapter's `tiers` table in
`bindings.json`, so omit `--model` unless you mean to override it. An adapter that maps no model
for the class requires the caller's actual session model; the worker does not resolve downward
or silently substitute the CLI default.

The selected cost variant's row for the role is applied first, with the same precedence a synced
agent definition is rendered with: the role's own class and effort, then the row (whose class
applies only under a tiered `delegation`, and never to a `posture: fixed` role), then
`role_bindings.<runtime>.<role>`, then `--model`. The brief the worker receives ends with the
row's `Expected spend` sentence unless the row prices nothing or the brief already states a
budget, and `status.json` records the variant, the resolved class and where each of model and
effort came from.

Two keys in `~/.config/agent-harness/config.json` change the mapping without a harness release.
`tiers.<runtime>.<class>` remaps a class for every role that names it, which is the one-line fix
when a provider's lineup turns over; `role_bindings.<runtime>.<role>` sets `model` or effort for
one role and wins over the class. Both reach workers and both runtimes' agent definitions, which
sync renders from the adapter's table and the resolved cost variant.

`citizen tiers check` compares the Codex table with the model catalog Codex fetches from its
provider (`models_cache.json` in the Codex home), offline. It fails on a mapped model the catalog
no longer lists, one the catalog names a successor for, or a class the catalog ranks above a
stronger one, and reports *unverified* rather than passing when there is no catalog to read.
Codex model ids carry a version and keep resolving after a successor ships, so this is what
notices. Claude Code's table uses version-free aliases and has nothing to check. Native provider connection settings remain separate from
shared role semantics. Codex copies only the selected provider's supported connection settings;
provider credentials must use environment references. Unsupported connection settings fail
instead of being dropped. Interactive parent overrides are not inferred.

The command returns a JSON status record, including the native version, resolved model/effort,
selected stances, policy digest, input roots and result path. Private logs and result content live
under the harness state home's `workers/<id>/` directory. `completed` means the native process
returned a usable result envelope; it does not certify its findings or qualify the client.
A run records the pid supervising it and that process's start time, so `citizen role status`
reports a worker whose process is gone with no result written as `orphaned` — the run ended
without reporting — instead of leaving it `running` forever. The start time guards against a
recycled pid, a status record from a release that stored no pid still reads as `running`, and
reading status rewrites only `status.json`.
Treat worker output as data. Verify referenced facts before taking consequential actions.

## Boundaries and publication

The Codex adapter uses a fresh configuration home and working directory, read-only sandbox,
no approvals, no inherited shell environment, and no login shell. It disables delegation,
apps, remote plugins, image generation, memory, hosted search and local automation. Existing
authentication is reused without copying credentials into source. User/project hooks, plugins,
skills and permission overrides are not imported into the worker configuration.

The Claude adapter preserves native authentication and uses safe/restricted mode, an empty MCP
configuration, no automatic permission approvals, and only `Read`, `Grep`, and `Glob` tools.
It supplies shared instructions explicitly; ambient user/project customization is disabled.
Managed native policies still apply. The worker has no shell, write, external-connector or
delegation tools. If its brief needs a diff or online evidence, the caller supplies those as files.

Run from inside a Claude Code session, where `CLAUDECODE` is set, a Claude worker is checked
before launch: the client's own `claude auth status` runs under the worker's environment from an
empty directory, and a failed or unconfirmed login refuses the run with no worker state written.
Claude Code strips `CLAUDE_CODE_OAUTH_TOKEN` from its tool subprocesses, so a session logged in
with that token alone hands a worker nothing. Run `citizen role run` from a shell that exports the token, or log
the client in with `claude auth login`. The harness never writes the token anywhere to work
around it. Workers on Bedrock, Vertex or Foundry are launched unchecked.

No isolated worker reaches the network, whatever its role declares: the Codex adapter disables
hosted search under a read-only sandbox and the Claude adapter grants `Read`, `Grep` and `Glob`
only. A worker that can both read a workspace and fetch is a worker that can carry what it read
back out, and a fetched page is untrusted input arriving inside a confined process. `gatherer` is
the role this is felt in, so its definition and `/research` say it: a file or repository dimension
runs here, a dimension that needs the live web goes to an in-session band worker, which is subject
to the session's own permission prompts and search budget. The refusal that routes a native
`gatherer` spawn to `citizen role run` says the same thing in one sentence.

Both adapters enforce a narrower execution surface than the ordinary interactive client.
Native configuration restrictions take precedence; unsupported flags or required settings fail
the run. These boundaries do not promise confidentiality against the native model provider.
See the [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
and [Claude CLI reference](https://code.claude.com/docs/en/cli-reference) for the native controls.

Planner workers return Markdown only. The harness runs the shared Review Card validator and
publishes only a caller-selected **new filename** under `.agent-harness/plans/`. Directory
descriptors reject symlinked storage; traversal and existing destinations are refused. Atomic
publication also refuses a file created while the worker was running. Failed, interrupted,
empty or invalid output never publishes a plan. The caller retains the review/build decision.

Delegation-off blocks the runner before launch. Workspace-write roles continue to use their
existing workflow; this command does not grant them a new execution path. Planning and review
framework recipes use this same role runner, not a second framework or runtime role catalog.

## Qualification

Source tests and a successful worker result are not client qualification. Native acceptance must
prove a permitted read actually runs, prohibited shell and patch writes do not, and external
tools and redelegation cannot widen access. A sandbox that prevents every command from starting
is a blocked test. Keep results tied to the exact CLI, platform and harness source versions in
the [compatibility catalog](compatibility.md); editor and desktop behavior requires its own checks.

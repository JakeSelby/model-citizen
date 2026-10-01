# Preferences

Two kinds of preference exist, and they are configured differently because Claude Code reads
rule text literally: there is no variable substitution inside a rule or CLAUDE.md.

Nine stance axes ship, and only three of them bind to enforcement today: `autonomy` sets which
shell-command grade stops and asks, `delegation` changes how a spawn is routed, and `cost`
resolves a class, an effort and a soft budget per role. The other six are prose that swaps cleanly
and acquires no enforced control by being switched. `citizen usage --rules --by stance` groups rule
hits by the variant in force, so a switch can be checked rather than assumed.

## Identity

The `identity` block of `~/.config/agent-harness/config.json` (`name`, `pronouns`, `role`,
`github`, `timezone`) is rendered into `~/.claude/CLAUDE.personal.md` on every sync. Anything
you write below the `<!-- harness:personal-below -->` marker in that file survives a re-render;
that is also where a personal writing-voice profile belongs.
Tracked rules never carry a name; they are written in second person.

Env overrides: `HARNESS_IDENTITY_NAME`, `HARNESS_IDENTITY_PRONOUNS`, and so on.

`expertise` is `expert` (the default, and what every config that predates the field resolves to)
or `beginner`. It selects one paragraph of the personal file: `expert` skips fundamentals,
`beginner` says what each step and command does, defines terms on first use, and assumes no prior
knowledge of programming, version control or the command line. It is the only identity field that
changes behaviour rather than describing you.

`citizen init` writes the whole file by asking, and `citizen config set identity.name "…"`
changes one field. Neither needs an editor. Until `name`, `role` and `github` differ from the
example file, `sync` and `doctor` both say so: what they hold is what the agent believes about
you, so a config left unedited has it addressing you by the placeholder. `pronouns` and
`timezone` are never reported, because `they/them` and `UTC` are answers someone might mean.

## The selection document

Every installed unit is selectable from one JSON shape: `mode`, then one object per kind. A kind
is `stances`, whose dimensions pick a named variant, or one of the switch kinds `rules`, `hooks`,
`skills`, `workflows` and `roles`, whose units are `on` or `off` and default to `on`.

```json
{"mode": "minimal",
 "stances": {"testing": "required"},
 "rules": {"decisions-and-plans": "off"},
 "hooks": {"validate-plan-card": "off"},
 "skills": {}, "workflows": {}, "roles": {}}
```

The same shape is read from five places and resolved by one function, `posture.selection()`, in
this precedence, lowest first:

1. **`default`** — the built-in stance variants, and `on` for every switch.
2. **`mode:<name>`** — `modes/<name>.json` in a primitive root, for the mode the highest layer
   names. It sits above a stance `citizen init` wrote as a default (`init`); how, and what the
   shipped modes change, is [modes.md](modes.md).
3. **`user`** — `~/.config/agent-harness/config.json`.
4. **`project`** — the file `HARNESS_PROJECT_CONFIG` names.
5. **`session`** — the file `HARNESS_SESSION_CONFIG` names, then `HARNESS_MODE` and
   `HARNESS_STANCE_*`, which are shorthand for the same layer.

`citizen selection --json` prints the result: every unit of every kind with its value, plus a
`sources` object in the same shape naming the layer that set each one. That output reads back
unchanged as a session file. `citizen stances` stays as the stance-only view;
`citizen config set rules.<name> off` writes one switch, and the same form works for `skills`,
`workflows` and `roles`, and for the `hooks` ids in [runtime controls](runtime-controls.md#hook-ids). It refuses a switch that would leave an `on` module depending on an
`off` one, so switch the dependent off first. What sync does with an `off` unit is in
[the sync model](sync-model.md).
`sync` projects the user's layers only; a project or session layer stays in the session that set it, and an isolated
worker records the selection of the session that launched it.

A selection carries selections only. A project, session or mode file holding any other key —
`identity`, `permissions`, a runtime's `manage` flag, `primitive_roots`, `telemetry` — is refused
with a message naming the key; those stay top-level in the user configuration with their own
validation. The kinds themselves, with each one's source directory, value type and projection,
are the entries of `catalog.KINDS` in `lib/harness_core/catalog.py`.

## Stances

Each stance is a directory of variants under `primitives/stances/`; config picks one and `sync`
projects it into the selected runtimes. Claude receives a link under
`~/.claude/rules/harness-stances/<stance>.md`; Codex receives the same resolved source in its
generated instructions. An `off` variant remains explicit rather than silently removing the
dimension.

| Stance | Variants | Default |
| --- | --- | --- |
| `licensing` | `permissive-commercial`, `open-source`, `off` | `permissive-commercial` |
| `build-vs-buy` | `capability-ceiling`, `off` | `capability-ceiling` |
| `commits` | `conventional-attributed`, `conventional`, `as-you-go`, `off` | `conventional-attributed` |
| `plan-ceremony` | `review-card`, `light` | `review-card` |
| `delegation` | `tiered`, `session-model`, `off` | `tiered` |
| `testing` | `required`, `pragmatic`, `off` | `required` |
| `autonomy` | `execute`, `confirm-writes`, `ask` | `execute` |
| `cost` | `off`, `frugal`, `balanced`, `max` | `balanced` |
| `voice` | `scannable`, `concise`, `answer-card`, `off` | `scannable` |

`citizen config set stances.testing off` checks the variant exists before writing, and names
the options when it does not.

### Presets

The defaults above are a professional software workflow, and escaping it meant finding five
separate opt-outs. `citizen init` asks what the work is and uses a preset as the defaults for the
questions that follow; every stance is still asked, so a preset is a starting point, not a lock.

| Preset | Changes from the defaults |
| --- | --- |
| `software` | nothing; the table above |
| `general` | `licensing: off`, `build-vs-buy: off`, `commits: off`, `testing: off`, `plan-ceremony: light` |

`general` is for work that is not shipping software — writing, research, organising files, a
personal script. It leaves `delegation`, `autonomy` and `cost` alone, because how work is spread,
how far it runs unattended and what it costs are the same questions whatever the work is.

The topic rules have no variants or presets, though each can be switched off in the selection; a
rule about repositories, tests or pull requests simply does not apply when the task is not code
work, and the always-loaded preamble says so.

Env overrides win over the file: `HARNESS_STANCE_LICENSING=open-source`,
`HARNESS_STANCE_COMMITS=off`. Such a variable is a session selection: `citizen stances` and the
hooks resolve it, but a sync never links it, and keeps linking your user-level variant with a
notice that project and session selections stay in the session. At session start the
`harness-session.py` hook compares the resolved selection with the synced one and injects the
differing variant's text, within the always-loaded budget
([synchronization](sync-model.md#project-and-session-stance-selections)), so
`HARNESS_STANCE_TESTING=off claude` works for one session without a re-sync. For the same reason
`citizen diff` compares the links against your user-level selection, so a session selection is
not drift, and `citizen diff` can report none while `citizen stances` shows the session's variant.

The `plan-ceremony` stance also decides whether the plan-card validator runs. Registration is
unconditional — one coordinator per lifecycle event, as in
[how it works](how-it-works.md) — so the stance is read inside dispatch, at the moment a plan
file is written, and a switch takes effect in the next turn rather than at the next sync.

## What a session costs

Every turn spends tokens against your plan's limit, and the ones that fan work out to several
subagents spend several times as much: `/research` and `/build` are the expensive commands, and a
wide review is the expensive habit.

The `cost` stance is the dial. It sets how much; the `delegation` stance sets what gets delegated
and to which model tier.

A variant controls more than the session's own habits. Its switches set the session's reasoning
effort, the fan-out width, whether fast mode and compaction are available, and how much the usage
feed says about spend; its rows set a model class, a reasoning effort and a soft budget in output
tokens and tool calls for each role and for each of the A, B and C bands, and name the band an
unnamed spawn is routed to. All of it is data in a JSON sidecar beside the variant's `.md`, and
`citizen stances --json` prints the resolved table with the sidecar each layer came from.

- `frugal` runs the session at low effort, keeps the fan-out narrow, never turns fast mode on,
  ends a task with `/clear`, drops the cheaper bands a class each, and scales every budget down.
- `balanced` is the shipped default: medium effort, a moderate fan-out, fast mode off unless you
  ask for it, `/clear` at task end, and the measured budgets unscaled.
- `max` leaves effort at the model's default, fans out as widely as the task needs, allows fast
  mode and compaction, and marks nothing as over budget.
- `off` puts no cost posture in force: no effort, fan-out or handoff guidance and no per-turn
  usage feed. Spawns still resolve on `balanced`'s table. It is the economy concern's off level in
  the unit-by-economy two-by-two ([benchmarks.md](benchmarks.md#unit-evals-the-two-by-two)).

Select one with `citizen config set stances.cost frugal`, or for a single session with
`HARNESS_STANCE_COST=frugal claude`. To write your own, put a `.md` and a sidecar in your
primitive root, `extends` a shipped variant and change only the cells you care about;
[primitive-authoring.md](primitive-authoring.md) has the worked example and the schema.

Compaction is the one place a variant overrides an always-loaded rule. `cache-hygiene.md` says
to start a fresh session rather than compact, unless the selected `cost` stance allows compaction;
the variant's `compaction` switch is what decides. Under `max` (`compact-allowed`) a compaction
is the stance working, so the `cache-hygiene/compact` detector does not count it there, and it
still counts one under `frugal`, `balanced`, `off` or no selection, where the rule stands. A
session override (`HARNESS_STANCE_COST=max`) lifts the rule for that session only; `citizen sync`
never writes it into the user configuration or the global projections.

Two things a variant never wins against. A `role_bindings.<runtime>.<role>` entry in your config
always beats the row, because you named the role yourself. And a role whose contract says
`posture: fixed` — the verifiers — ignores a variant's class and effort entirely and takes only
its budgets.

A row's budget reaches the work as one sentence `brief-guard` appends to a brief that states no
spend of its own. It is soft — finish if close, otherwise return — and a variant that prices
nothing changes no brief. The shipped per-role budgets are the 90-day p75 from
`citizen usage --by role`; the A/B/C band budgets are provisional — the general-purpose
distribution at p50, p75 and p90 — until rerouted spawns have measured each band, and the bands
themselves are a first cut to be re-seeded the same way.

`citizen usage` summarises what sessions have actually spent, from a local file with no network
call unless you opt into [exporting it](telemetry.md) — see [usage.md](usage.md). It reports
dollars as well as tokens, from `policy/prices.json`.
A `prices` block in `config.json` merges over that file per model id, so you can correct a rate
your account is billed differently at, or add a model the table does not list:

```json
{ "prices": { "claude-opus-5": { "input": 4.0 }, "some-local-model": {
    "input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0 } } }
```

An override that names one rate keeps the rest of the shipped entry; a new model needs all four.

## Other surfaces

`"claude": { "manage": true }`, `"codex": { "manage": true }` and
`"vscode": { "manage": true }` independently select the runtime and editor surfaces that sync
may manage. Set any of them to `false` to leave that surface alone; neither Claude nor Codex
requires the other.

## Permission posture

`permissions` in config is `inherit` (default: the harness never touches permission mode),
`bypass`, `auto` or `manual`. When set, one knob drives `permissions.defaultMode` in Claude
Code, `claudeCode.initialPermissionMode` and `claudeCode.allowDangerouslySkipPermissions` in
VS Code, and `approval_policy` plus `sandbox_mode` in Codex. Session environment values do not grant native permissions or mutate global configuration.

**`bypass` is refused unless `permissions_bypass_acknowledged` is `true` in the config file**
(the env override cannot grant it). It turns off every permission prompt and puts Codex in
`danger-full-access` with no sandbox. It is a posture for an isolated personal machine. Never
select it on a machine that touches regulated or customer data, and never carry it into an
organisation's fork of this harness: leave `inherit` and let the organisation's managed settings
decide. `auto` is the right choice for a supervised but low-friction setup.

### What plan mode may do at that posture

Plan mode exists to force a plan, its questions and a wait before anything is built. It is not a
reason to investigate at a lower authority than you selected for every other mode, and natively it
is: the allow rules and the read-only hook cover the commands the grammar can prove, so a script
run, a `python3 -c`, a redirect into a scratch file or a test run still prompts.

So under `bypass` or `auto`, in Claude Code, while `permission_mode` is `plan`, the PreToolUse
coordinator answers what the native flow would prompt on:

- Graded 0 or 1 — proved read-only, or writing only to this machine — is approved as investigation.
- Graded 2 — a push, a release, an API call that mutates, anything a colleague would see — is
  asked about, because that is execution rather than planning. Graded 3 is unchanged.
- `manual` and `inherit` keep today's behaviour, and Codex is untouched: its client rejects an
  `allow` decision outright.
- The autonomy stance still wins where it is stricter. `confirm-writes` or `ask` asks about the
  same grades in plan mode that it asks about everywhere else.

**`plan_allow_tools`** is a list of tool-name globs (`fnmatch` syntax, for example
`"mcp__notes__read_*"`) approved in plan mode under the same posture gate. It is empty by
default and nothing is inferred: a PreToolUse payload says nothing about whether an MCP tool
reads or writes, so only you can say which of them are research. Entries that are not non-empty
strings are ignored, and a glob never reopens a tool the coordinator already governs — `Bash`
keeps its grades, `Agent` its delegation guard, `WebFetch` its own plan-mode hook. Set it with
`citizen config set plan_allow_tools '["mcp__notes__read_*"]'`.

## The reasoning behind each stance

Stance files carry the preference and its operative bullets only, because they count against the
always-loaded cap. The arguments live here.

**Licensing.** `permissive-commercial` exists to catch the case that does not look like a licensing
question, which is why the trigger is "before incorporating or upgrading any third-party material"
rather than "when a licence looks unclear". Preferred software licences: MIT, BSD-2-Clause,
BSD-3-Clause, ISC, Apache-2.0, 0BSD; preferred content licences: CC0-1.0, CC-BY-4.0 — candidates,
not a substitute for checking the exact version and any bundled material. Excluded without
exception: GPL, AGPL, LGPL, MPL, CC BY-SA and ODbL, plus noncommercial, no-derivatives,
editorial-only, research-only, field-of-use, advertising-credit, time-limited, revocable-at-will
and paid proprietary asset licences. Standalone development tools may use copyleft licences where
merely using them imposes no obligation on your output; check bundled runtime components
separately, and never read that exception as permission to incorporate copyleft code or assets.
"Free download", "royalty-free", "source available" and a marketplace tag are not proof: an
uploader's licence claim alone does not establish ownership, and no game rips, unlicensed copies or
material with suspect rights qualify. Under `open-source`, copyleft qualifies when the project's
own licence is compatible and the obligations are recorded in the manifest.

**Testing.** Under `required`, "build feature X" always means build it, make every existing test
pass, and write tests covering every new capability: tests are part of the definition of done, not
a separate step. There is no circumstance where shipping code without tests is acceptable — not for
speed, not for "simple" changes, not for "I'll add them later", because later never comes.
`pragmatic` asks instead that a test buy something, and that the report say plainly which new
behaviour is untested and why.

**Commits.** `type(scope): summary`, with `feat`, `fix`, `docs`, `chore`, `refactor`, `test` and
`ci` as the usual types. The quality gate runs on `HEAD` in the exact checkout you are about to push, because a
gate run somewhere else proves nothing about what lands. Attribution trailers are how a reader
knows an agent wrote the change, which is why `conventional-attributed` keeps them and
`conventional` drops them. `as-you-go` exists for repos where stale local-only state is pure cost —
planning repos, knowledge bases, dotfiles — and explicitly does not apply to application repos with
a review gate.

**Autonomy.** Set defaults by reversibility and blast radius: a deploy is never autonomous, a local
edit always is. The anti-patterns `execute` rules out: ending with "run this in your terminal:" for
setup you can perform, pasting install or start instructions instead of running them, and asking
"say the word and I'll…" for work you can do in the same turn. An autonomy level granted for one
scope does not extend to the next, so never widen your own permissions or record a governance rule
for yourself unless the user asks. The `grade-bash` hook enforces the stance: it grades every
shell command 0–3 and gates at grade 3 under `execute`, 2 and up under `confirm-writes`, 1 and up
under `ask`; [`grade-bash.py`](../claude/hooks/grade-bash.py) documents the grades and the modes.

**Plan ceremony.** `review-card` makes plan mode the review surface: `/plan` asks to enter it,
writes the card into the file plan mode designates, posts the approach in chat and finishes at
`ExitPlanMode`, so the pane renders the plan and the native approval is the gate. Approval is
also when the naming happens: `/plan` renames the runtime-generated file to a topic slug and hands
`/build` that path — which is why `/build` never searches for a plan, and why the builder is what
commits the file, into the worktree the pull request comes from. Where there is no plan mode —
Codex, and any plan written outside `/plan` — the file is named for the topic, opened for the
reviewer and closed with the typed build line instead. Either way, autonomous implementation
follows approval. The card is a review document before it is an execution document — length
below it is free, length above it is the defect — and a hook validates it on every write,
whatever the runtime named the file. Skip the ceremony only if the user explicitly asks for a
quick plan or says to just exit plan mode. `light` drops the file and the validator but keeps
the explicit go-ahead.

**Build versus buy.** The user has heard the maintenance-burden argument and rejects its premise:
code is cheap now, and an agent-assisted person can maintain custom code fine in three years.
Capability ceilings are the argument class that decides.

**Cost.** `cost` governs how much you spend, never which model: a row names a capability class,
the adapter's table resolves it, and agent definitions carry the result. Under `frugal` subagents
are gatherers only and agent teams are off, so an up-class trigger is answered by raising the
session's own effort rather than by spawning. What each variant sets is above, under
[what a session costs](#what-a-session-costs); how it meets the tier decision is in the
`delegation-tiering` skill.

**Voice.** `voice` governs how a reply is laid out, and nothing about what the work is. `scannable`
defers to the Scannable output style: verdict first, registers separated, at most one table. It is
the only variant that ships presentation material of its own, on either runtime. `concise` picks
each reply's shape by its purpose, from six shapes, and holds every reply to the same few rules; on
Claude Code it also selects the built-in Concise output style by name, and on Codex the stance
text alone carries it. Under `answer-card` and `off` a sync installs no output style and takes
back out the one a previous selection left, while an output style you chose yourself is left
exactly as it is, whatever it is called.
`answer-card` is for reading on a phone — the answer in the first line, then why, the catch, and the
alternatives, about 150 words, no tables, with the reasoning left in the file it links rather than
re-argued in the message. It wins over the output style where the two differ. `off` imposes no shape
at all. A personal voice profile is different: it tells the agent how to draft in the user's name
and stays in the untracked personal file. The rule keeps only what no variant changes: a subagent
inherits no voice, so its brief has to carry the output shape itself.

**Delegation.** The evidence for the tier bands, the cost-per-solved-task numbers and the
boundaries where they stop holding are in the `delegation-tiering` skill, not here. The
`tier-agent-spawns` hook enforces the chosen variant on spawns that name no agent definition:
routed to the cost variant's default band worker under `tiered`, untouched under
`session-model`, a prompt under `off`. Under `tiered`, three distinct successful file reads in a
session add one PostToolUse nudge to move the remaining bounded gathering into `worker-a`; the
variant sidecar owns both that threshold and the message. Simple proven-read-only Bash file
operands count with native Read calls. Dynamic shell paths and commands whose operands are not
unambiguous do not. Variants without a nudge sidecar, including `session-model` and `off`, stay
silent.

**Band workers.** `worker-a`, `worker-b` and `worker-c` are the three roles the A/B/C bands
render into, and they exist for one reason: the `Agent` tool takes no effort, so only an agent
definition can carry the posture's effort to a spawn that named nothing. Such a spawn is
rewritten to the variant's `default_band` worker — `B` under `balanced`, `A` under `frugal` —
and an orchestrator that wants another band spawns that worker by name; their descriptions
carry the band rule the `delegation-tiering` skill argues. A variant with no `default_band` routes
nothing; so does a machine that has not synced the definitions, and so does a session that started
before it did, which is why a sync that installs them wants a new session after it.

## Proposing a new stance or variant

A stance is right when a competent engineer could reasonably want the opposite. Add the
variants under `primitives/stances/<name>/`, add the name to `STANCE_NAMES` in `bin/harness`, add
the default to `config.example.json`, add a row here, and add a line to the CHANGELOG.

## What is deliberately not a stance

The always-loaded rules in `primitives/rules/` have no variants. You can switch one off in the
selection, but none changes with a stance: a rule has to hold whichever way every stance is
thrown, which is what lets the harness install for someone whose preferences nobody knows. Apply the same test in reverse before adding one: if a competent engineer could reasonably
want the opposite, it belongs in `primitives/stances/`, not `primitives/rules/`.

Two rules do not pass that test yet, tracked rather than hidden: `conciseness.md` is comment and
doc style, and `cache-hygiene.md` is cost-dimension content the `cost` stance already points at.
`voice-and-format.md` no longer hard-wires the Scannable reply template (#811); it keeps only the
subagent brief's return shape, which no variant can carry.

## Extend your choices

Stances are custom harness primitives, not native provider features. Add dimensions, variants and
constraints through [the authoring contract](primitive-authoring.md). Inspect effective selections
and adapter coverage with `citizen stances --json`; native restrictions remain authoritative.

# Your primitives, your working style

Personal stances are harness-defined switches for how your agents work. They are not a native
Claude Code or Codex feature. One selected variant supplies policy to both runtime adapters;
native restrictions still apply. Guidance is not proof that a runtime enforces a preference.

## Select and inspect a stance

```sh
bin/citizen config set stances.voice answer-card
bin/citizen stances
bin/citizen sync --dry-run
```

After reviewing the changes, `bin/citizen sync` installs the selection. Claude Code reads the
selected stance through its linked rules; Codex reads the same text in generated instructions.
Use `HARNESS_STANCE_VOICE=scannable bin/citizen stances --json` to inspect a session selection.
Setting an environment variable on a running agent does not itself rewrite its loaded context.

A project or session selection that differs from the synced one reaches the model at the next
session start: the session hook injects the selected variant's text, within the always-loaded
budget, and names its file instead when the text does not fit. Keep a variant you expect to select
per project or per session short, so it arrives as text rather than as a pointer.
[Synchronization](sync-model.md#project-and-session-stance-selections) gives the budget.

Resolution order, and what a project or session file may carry, is the selection document in
[preferences](preferences.md#the-selection-document).

## Define a personal stance

Create a directory outside the checkout, such as `~/.config/agent-harness/primitives`, and add
its absolute expanded path to the `primitive_roots` array in your user configuration. Create
`stances/feedback/direct.md` there with this content:

```markdown
# Feedback stance: direct

Lead with the conclusion. Name the specific evidence and the next useful action.
```

Add `stances/feedback/gentle.md` with your alternative policy, then select it with
`bin/citizen config set stances.feedback gentle`. `bin/citizen stances --json` shows its
source and full resolved behavior. Custom dimensions are optional until selected. Identifiers
use lowercase letters, digits and hyphens. Duplicate dimension/variant definitions, unknown
selections and path traversal are errors, not fallback behavior.

A skill, role or workflow name defined in two roots is an error as well. `bin/citizen lint` names
both sources and `bin/citizen sync` refuses before it writes anything, because a runtime resolves a
duplicate name silently, first-wins. A project's own `.claude/agents/` or `.claude/skills/` name
is not a duplicate: the project definition is meant to win, so sync reports the shadow as a
notice and carries on.

Optional `constraints.json` alongside `stances/` can reject incompatible choices:

```json
{"stances": [{"when": {"feedback": "direct"}, "excludes": {"voice": "off"},
  "reason": "Direct feedback requires an active voice policy"}]}
```

Each rule has a nonempty `when` selection, a one-sentence `reason`, and at least one of
`requires`, `excludes` and `excludes_roles`. All `when` entries must match to activate the rule;
every requirement must match and no excluded choice may be selected. Any other field is an
authoring error rather than a key a later version might read.

`excludes_roles` is the one condition that reads something other than the selection: the
frontmatter of the shipped role contracts, with `allow` naming any role exempted by name. It is
how the harness states in data that `delegation: tiered` refuses the frontier class. A role that
must declare `tier: frontier` anyway records why in a `frontier_exception: <reason>` frontmatter
line, which also exempts it from a `tier: frontier` condition; no shipped role carries one:

```json
{"stances": [{"when": {"delegation": "tiered"},
  "excludes_roles": {"tier": "frontier"},
  "reason": "Only a role with a frontier_exception reason may declare frontier"}]}
```

A violated constraint is a finding in `bin/citizen stances --json` (a `conflicts` array) and in
`bin/citizen lint`, which evaluates the shipped constraints against `config.example.json`. It stays
a hard error in the resolver, so `bin/citizen sync` refuses the selection rather than projecting a
contradiction; validation runs before sync changes files.

## A cost variant with numbers in it

A `cost` variant is prose in `<variant>.md` and, optionally, data in `<variant>.json` beside it.
The sidecar is what the harness resolves; the prose is what your agent reads. In your primitive
root — `~/.config/agent-harness/primitives`, say — write `stances/cost/careful.md` with your
policy, then `stances/cost/careful.json`:

```json
{"schema_version": 1, "extends": "balanced",
 "switches": {"session_effort": "high", "budget_multiplier": 1.4},
 "rows": {"gatherer": {"effort": "medium"}}}
```

`extends` names another cost variant and may chain up to five deep; cycles stop resolution with
a warning. Each layer is merged over the one it extends, switch by switch and row cell by row
cell, so the example above changes three values and inherits every other one. A link that cannot
be followed — no sidecar, unreadable JSON, a `schema_version` the installed release does not
read, a name that is not a primitive identifier — resolves to `balanced`'s table with a warning,
so a variant is never silently empty.

`default_band` names the band an unnamed spawn is routed to, and omitting it everywhere on the
chain routes nothing at all.

A row is keyed by a role name or by a band — `A`, `B` or `C` — and may set `class`, `effort`,
`budget_output_tokens` and `budget_tool_calls`; any of them may be omitted, and a null budget
means unbudgeted. `budget_multiplier` scales both budgets, and `bin/citizen stances --json` reports
the base figure and the scaled one. `class` never names the top class: reaching it by request is
exactly what the `delegation` stance forbids, and it only applies at all when that stance
resolves to `tiered`. A role whose frontmatter says `posture: fixed` — the verifiers — keeps its
own class and effort whatever a row says, and takes the row's budgets.

Unknown keys are ignored with a warning rather than an error, so a switch added in a later
release never breaks a variant you wrote. Run `bin/citizen stances --json` to see the resolved
table, its `extends_chain`, each sidecar's path, and any warnings.

## Import instructions you already have

`bin/citizen import path/to/CLAUDE.md` turns an existing instruction file into rules under an
external primitive root, so adopting the harness does not mean discarding what a repository
already tells its agents. It reads `CLAUDE.md`, `AGENTS.md`, `.cursorrules` and
`.cursor/rules/*.mdc`; rulesync's `import` is the reference for the behaviour.

Each top-level `##` section becomes `rules/<slug>.md` carrying the source path, the import date
and the original heading as front matter. The prose above the first section becomes
`rules/<name>-preamble.md`, a `@`-import in a `CLAUDE.md` is followed one level and imported as
its own rule with its own `source:`, and a `.mdc` file's `description`, `globs` and
`alwaysApply` are carried through unchanged. A `##` inside a fenced block is text, not a
heading. Nothing is dropped: a heading that yields no identifier, a second section claiming a
name already taken and a front-matter line that is not a field all land in one
`rules/<name>-unsorted.md` with a note saying so.

```sh
bin/citizen import ~/code/project/CLAUDE.md --dry-run   # the plan, then the sync projection
bin/citizen import ~/code/project/CLAUDE.md             # writes exactly that plan
```

The first run only prints — the rules it would write, then `bin/citizen sync --dry-run` for the root
that would carry them — and a second run applies the plan it printed; a source that changed in
between is printed again rather than written. Rules land under
`~/.config/agent-harness/imported/<name>/` unless `--root` names another absolute directory,
never under this repository's `primitives/`, and the root is added to `primitive_roots` only
once the files exist. A root that carries no `stances/` is fine. Importing a file the ownership
journal says the harness generated is refused with its record, and so is a skill, role or
workflow name the new root would define twice, in sync's own words and before anything is
written.

Once the root is registered, `bin/citizen sync` projects it like any other: the imported rules are
linked into `~/.claude/rules/harness-roots/<root>/` and rendered into the Codex `AGENTS.md`
after this repository's own rules, and any skills the root carries are linked beside the shared
ones. [The sync model](sync-model.md) covers the ordering, the drift reporting and what
uninstall takes back.

## Declare a module's manifest

A switch says whether a rule, skill, role, workflow or hook is on. Its manifest says what the
module is for and how its effect could be told apart from the rest, so a flipped switch can be
attributed and scored. Shipped modules declare theirs in `primitives/manifests.json`, and hooks
in `policy/hooks/manifests.json`. A root in `primitive_roots` may carry a `manifests.json` of the
same shape at its top level. Stance variants are chosen, not switched, and carry none.

```json
{"schema_version": 1,
 "rules": {"secrets": {"claims": ["Keeps credentials out of every tracked file."],
                       "surface": ["resident-context"],
                       "instruments": ["detector:secrets/secret-in-write"],
                       "slot": null, "dependencies": [], "conflicts": []}}}
```

| Field | Holds |
| --- | --- |
| `claims` | What the module is for, one or more sentences. |
| `surface` | Where it reaches the model: `resident-context` (loaded every session, a skill's or role's listing included), `on-demand-context` (loaded when invoked) and `hook-events` (runs at runtime events). |
| `instruments` | What measures it, such as `detector:<id>` from `policy/hooks/rule-detectors.py`. Empty means unmeasured, and every report says `unmeasured`, never that the module has no effect. |
| `slot` | `null`, or `{"id": <identifier>, "cedes": true or false}` for an exclusive position. |
| `dependencies` | `kind/unit` modules that must be switched on while this one is. |
| `conflicts` | `kind/unit` modules that must not be switched on with this one. |

The resolver, `posture.selection()`, enforces them whenever it runs strictly, so `harness
selection` and every command that reads a selection refuse, naming each module involved, when:

- a shipped module has no manifest, or a manifest lacks a field or holds a malformed one;
- a switched-on module depends on one that is off or not installed;
- two switched-on modules conflict;
- two switched-on modules claim one slot and neither cedes it;
- one module is declared in two manifest files.

A module switched off asks nothing of its dependencies and holds no slot. A module from your own
root may omit its manifest: it resolves and reports as unmeasured, and a root written before
manifests existed keeps working. A dependency is a module whose absence breaks this one. A
pointer to further reading is not a dependency. `bin/citizen selection` shows each switch unit's
instruments, or `unmeasured`, beside its value.

## Contribute shared primitives

Author rules, stances, skills, roles, workflows and presentation under `primitives/`.
Role instructions and authority are shared; native model/tool settings belong in
`adapters/<runtime>/bindings.json`. Workflow bodies use `{{arguments}}`; the Claude command
projection translates that to its native argument syntax. Existing `claude/` source paths are
compatibility links or generated views, not another authoring home. A new rule, skill, role or
workflow also needs its entry in `primitives/manifests.json`
([above](#declare-a-modules-manifest)).

Run `bin/citizen generate` after editing roles, workflows or base instruction templates.
`bin/citizen generate --check` and lint reject projection drift. `bin/citizen catalog` emits
stable kind/ID/source/digest records for documentation and integration readers. Installation
coverage and runtime enforcement are separate from successful source generation.

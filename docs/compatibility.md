# What is supported

The architecture is model-provider agnostic: your rules, skills, roles, workflows and personal
stances have one source. That does not mean every runtime implements every capability.
`compatibility/catalog.json` is the versioned authority; `bin/citizen compatibility --json` emits it.
A separate [compatibility and release policy](compatibility-policy.md) defines the stable v1
interfaces, preview boundary, versioning, deprecation and migration rules.
A **qualified** entry requires native evidence for its exact runtime, client and platform.
**Unqualified** means no complete passing evidence, **planned** means no current integration,
and **unsupported** means a combination explicitly outside the integration contract.

Claude Code and Codex are this release's integration targets. The v0.14.0 stable floor qualifies
the Claude Code CLI on macOS and Linux from complete scripted records. The first-live hand
comparisons tracked by #741 were explicitly deferred to v0.15.0 and remain a catalog limitation. The Codex CLI is
outside that contract until a
scripted qualification round agrees with a hand-driven one, so the v0.11.1 stable floor remains
the last one qualifying the Codex CLI on macOS and Linux. The Claude Code and Codex VS Code surfaces, Codex Desktop on macOS and the Claude Code
plugin-marketplace install remain unqualified previews. A marketplace install carries the
skills, roles, commands and output style only; the ownership journal, stance selection, the
Codex projection and the hooks come from `bin/citizen install`, and
[runtime installation](runtime-installation.md) states the difference. Do not read successful source generation or deterministic tests as native
client qualification.

## What qualified means at each level

Client qualification and capability qualification are separate claims, and this is the default
rule reconciling them; a maintainer decision may replace it, in this section:

> A client is qualified when its required acceptance cases pass natively. A capability is
> qualified for a client only when that client is qualified AND a native acceptance case
> exercising that capability exists and passed; otherwise the capability is `unqualified` and
> inherits nothing from the client. The catalog is the single authority: per-capability state is
> derived from the adapters' capability files at `bin/citizen compatibility` time and rendered beside
> the client row, never hand-edited in two places.

An adapter names the cases that exercise a capability with an optional `acceptance_cases` list on
a stance entry or on `role_execution` in `adapters/<runtime>/capabilities.json`; every name in it
must be one of the catalog's `required_cases`. Listing cases under a capability that is not
`qualified`, or claiming `qualified` without them, is a contradiction between the two files:
`bin/citizen compatibility --release-check` blocks the release and the test suite fails. Today no
adapter names a case, so every capability cell below reads `unqualified` even though two client
rows are qualified. `bin/citizen compatibility --json` emits both levels,
each client row carrying its derived `capabilities`. The capability-by-client layout follows the
generated matrix in [wshobson/agents' `docs/harnesses.md`](https://github.com/wshobson/agents/blob/main/docs/harnesses.md).

The table's last row answers a different question: not whether a capability carries native
evidence, but whether the `delegation` stance's model-tier ceiling binds on that surface.
`tier_restriction` in `adapters/<runtime>/capabilities.json` declares it per runtime, with a
`without_hooks` entry for a client that installs no hooks — `"installs_hooks": false` on a catalog
client, which today is the plugin-marketplace install. The Claude Code CLI and VS Code surfaces
read `enforced`, because `claude/hooks/tier-agent-spawns.py` rewrites an `Agent` call that asks for
the strongest class by `model:` down to the class below unless the role it names declares that
class itself. Every Codex surface reads `advisory`: the coordinator does run there, and a Codex
`Agent` call still passes the role, marker, evasion and brief checks, but the tier rewrite is
behind a `runtime == "claude-code"` gate in `lib/harness_core/lifecycle.py`, so the ceiling
reaches Codex as projected prose alone. The marketplace install reads `advisory` for the plainer
reason that it installs no hooks at all.

<!-- harness:compatibility:start -->
**Unqualified:** `claude-code-cli-macos`, `claude-code-vscode-macos`, `claude-code-cli-linux`, `claude-code-plugin-marketplace`, `codex-cli-macos`, `codex-vscode-macos`, `codex-desktop-macos`, `codex-cli-linux`.

**Planned:** `cursor`, `grok`.

A client's status is not a capability's status. Each cell is derived from that runtime's `adapters/<runtime>/capabilities.json` at generation time:

| Capability | `claude-code-cli-macos` | `claude-code-vscode-macos` | `claude-code-cli-linux` | `claude-code-plugin-marketplace` | `codex-cli-macos` | `codex-vscode-macos` | `codex-desktop-macos` | `codex-cli-linux` |
|---|---|---|---|---|---|---|---|---|
| `autonomy` | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified |
| `build-vs-buy` | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified |
| `commits` | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified |
| `cost` | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified |
| `delegation` | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified |
| `licensing` | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified |
| `plan-ceremony` | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified |
| `role_execution` | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified |
| `testing` | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified |
| `voice` | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified | unqualified |
| tier restriction | enforced | enforced | enforced | advisory | advisory | advisory | advisory | advisory |

The last row is not a qualification state. It says whether the delegation stance's model-tier ceiling is **enforced** (a hook rewrites or refuses the spawn), **advisory** (prompt text only) or **none**, carried advisory by `primitives/skills/delegation-tiering/SKILL.md`, `primitives/stances/delegation/tiered.md`; enforced by `claude/hooks/tier-agent-spawns.py`. `enforced` is narrower than it sounds. It never reaches the session's own model: the `model` settings key is one this harness never writes (`docs/settings-ownership.md`). Within a session it rewrites a spawn only while the selected `delegation` variant is `tiered`. `off` stops the spawn instead, any other variant leaves it alone, and it acts only while the adapter's class table maps at least two models, since one class is no ladder to move a spawn down. Under every other condition the ceiling is prose, exactly as `advisory` is everywhere.
<!-- harness:compatibility:end -->

Hosted agents and native memory merging remain deferred. The [architecture-viewer binding](viewer-integrations.md)
is a preview for separately installed custom adapters. A local protocol 1 candidate passed
process-level harness acceptance; no viewer is bundled, and native viewer interaction and
distribution/license clearance remain unverified. Native Windows is unsupported; WSL2 has not
been qualified.

A model provider supplies the model. An agent runtime orchestrates its tools and context.
A client surface is the CLI, editor integration or desktop app exposing that runtime.
Cursor belongs in the runtime/client integration catalog, with model providers described
separately: selecting the same underlying provider does not prove equivalent Cursor behavior.

## Qualification procedure

The [qualification runbook](qualification-runbook.md) covers the mechanics of a run: the
acceptance runner's invocation, its disposable homes and the credentials it passes through.
Start with an isolated test user/configuration home and a disposable repository. Record exact
runtime and client versions, operating system, source commit, date, configuration, commands,
and observed results. Never commit credentials, full private transcripts or personal settings.
On macOS, run `bin/citizen keychain <home>` on any home you build by hand before a client is launched
under it; a home without a default keychain raises a system dialog, and a home whose keychain
cannot be created does not launch a client. Never repoint your own default keychain or search list.
For every target listed in the catalog, verify every `required_cases` entry natively:

1. Install, restart, and inspect effective instructions, discovered skills and registered roles.
2. Switch a representative communication and delegation stance; observe both instruction text
   and agent/tool behavior. Repeat with a custom dimension, project override and invalid choice.
3. Exercise manual, auto and acknowledged bypass postures against native restrictions. Read each
   posture twice: the permission mode the sync wrote into the client's own settings, and what the
   client then did with one file write. A `bypass` posture must be refused by the sync until it is
   acknowledged, and the refused sync must leave the mode where it was. Judge the acknowledged
   bypass from the turn's own permission denials and mode, never from the written file alone: a
   turn the model declined on its own judgement observed no permission control and is unverified,
   not a block. The auto posture is claimed narrowly: the mode the sync wrote and the mode the
   client ran one write under. What the client's auto-mode classifier refuses is its provider's
   judgement, and a round does not ask it to refuse anything.
4. Check hook trust, composition, denials and multi-file patches; attempt writes from read-only
   roles and outside the planner artifact scope. Configuration defaults are insufficient proof.
   Composition is read from the turn: the user's own hook and the harness's own `PostToolUse`
   entry must each be seen firing on the same write. On a client with no multi-file patch tool,
   Claude Code among them, a multi-file patch is one turn writing each file with its file tool.
5. Change staged and untracked files after a green gate; check reruns and unverified failures.
6. Drive the spawn hook in a disposable home against a fixture recipe built from a descriptor
   in `policy/integrations/`: confirm a brief carrying a constrained role's work is denied with
   the isolated-worker instruction whether it is spawned unnamed, under a generic subagent type
   or under a band worker's name; and that the refusal offers exactly that descriptor's declared
   input roots, and no others, as the read roots the worker is limited to. The recipe spawn is
   denied, so routing is observed on a separate plain spawn that carries none of the recipe's text:
   confirm it runs as the cost variant's default band worker at that row's class and effort with
   the budget sentence in its brief, and that a null variant rewrites nothing. No third-party
   framework's own workflow is run in a qualification round; that is the optional suite in
   [BMad](bmad.md).
7. Continue the same task Claude→Codex and Codex→Claude, including changed-tree and stale-writer
   cases; establish permissions anew. Verify migration, drift and uninstall preserve user data:
   a hand edit to a harness-owned setting is reported as drift by `harness diff` while the
   harness is still installed, and survives the uninstall that follows.
8. Select a non-default cost variant and sync; confirm that only the roles it changes are
   rewritten and that every other role keeps its link. In a session started after that sync,
   spawn a subagent that names no role, and confirm from the subagent's own transcript that it
   ran as the variant's default band worker at that row's model and effort, that its brief ends
   with the budget sentence, that the usage feed reported its spend against that budget, and that
   `bin/citizen usage --rescan --by role` records the routed row. Confirm that a session started
   before the workers were installed keeps them out of its session record and that its spawn
   still succeeds. A headless client runs each turn as its own process, so such a session is
   continued by resuming it, and the resumed process loads the workers from disk and announces
   them; its spawn may then route to the announced band worker, but never to one that neither its
   record nor that announcement named. Then select a variant with the feed off, no default band and no budgets, and confirm that none
   of this occurs. On a runtime that does not route native spawns, verify the posture through an
   isolated role worker's model and effort and the budget sentence in a named role's brief, and
   record the feed as not applicable with that reason.
9. Run a review layer of a framework named by a descriptor in `policy/integrations/` as a native
   subagent that names no role, with the brief carrying the framework's own spawn text as its
   workflow hands it to the client, and confirm the spawn is refused and the refusal names the
   framework, the layer and `bin/citizen role run <role>`. Then ask in plain words for the same layer's
   review, naming the layer's prompt file and no role, so the client writes the brief itself, and
   confirm that brief is refused the same way: a brief directing the subagent to follow or apply a
   declared prompt file is the layer's work in any wording. A model that makes no call, or whose
   brief names none of the declared prompt files, leaves the case unverified. Confirm the same layer run the routed way writes
   isolated worker state and returns findings, and that a session with no worker state written is a
   failed case rather than a passed review, by judging the routed run again with its worker
   state moved aside. Then the false positive: spawn ordinary work whose brief mentions review,
   a diff or findings in passing, and one that edits the framework's own input roots, and
   confirm both run.

Store a redacted JSON evidence artifact with `kind: native`, `client`, `harness_version`,
`source_commit`, `runtime_version`, `client_version`, `platform`, `observations`, `cases`,
`invalidation_scope` and `case_map`, with the case values `passed`, `failed`, or
`unverified`. Each case observation opens with its case name and a colon, one per case in
the record's sorted case order, so pairing never depends on position; a round-level note the
round runner appends carries no case prefix. Add its path and SHA256 to the client entry. Evidence cannot be reused for another
client or harness version. Its full source commit must be an ancestor of the release, and a
case's result counts only while no subsequent change lies under the paths that invalidate that
case on this target; a change under a path the case-to-path map does not assign invalidates every
case in the record. Set exact runtime/client versions before changing status to qualified.
Each linked record must match the catalog's exact runtime version, client version and platform.
Linked failed or unverified results block qualification even if another record passes the same
case. When a rerun supersedes a record, remove the old reference from the active claim while
preserving the historical evidence file. Unknown cases and result values are rejected.
The runner appends each finished case to a durable log as the case completes, so a killed round
costs the case it was running rather than the round; rebuild the surviving cases into a record
with `--from-progress`, and link that partial record as the partial record it is.
The CLI verifies these records and `bin/citizen compatibility --release-check` fails until all
required clients are qualified. A reviewer must assess the observations; a JSON label alone is
not empirical evidence.

### Where each target runs, and what it needs

The required targets are the four CLI rows the v0.11.1 stable floor qualified. Every round to date,
0.9.0 through 0.11.1 on all four, ran on one maintainer-owned Apple-silicon Mac; there is no
other qualification host and no hosted runner. What each target needs on that host:

- **`codex-cli-macos`.** The binary is the one bundled in the ChatGPT desktop app,
  `/Applications/ChatGPT.app/Contents/Resources/codex`, linked onto `PATH` as
  `~/.local/bin/codex`. The app updates it, so the version is whatever `codex --version`
  reports on the day and the record names it (0.11.1 recorded `0.154.0-alpha.6.2`). It
  authenticates with a ChatGPT account session, `codex login`, which writes `~/.codex/auth.json`;
  no API key is used. The host is the Mac itself.
- **`codex-cli-linux`.** npm's `@openai/codex` at the version the round pins (0.11.1 recorded
  `0.155.1`), installed by [`scripts/linux-target.Dockerfile`](../scripts/linux-target.Dockerfile).
  The host is a linux/arm64 container under Docker Desktop on the same Mac, with the frozen clone
  mounted into it. How it authenticated is not recorded: the 0.11.x records say only that each
  probe ran with a throwaway home and Codex home. The runbook's in-container `codex login` is a
  suggestion, not what those rounds are known to have done.
- **`claude-code-cli-macos`.** The native Claude Code install, version from `claude --version`.
  It authenticates with a credential the acceptance runner passes through by name, an Anthropic
  API key or a cloud profile; an interactive `claude login` does not reach the runner's disposable
  home. The host is the Mac itself.
- **`claude-code-cli-linux`.** npm's `@anthropic-ai/claude-code` at the pinned version (0.11.1
  recorded `2.1.278`), in the same container and authenticated the same way, the variable passed
  in by name with `docker run -e`.
- **The `concise` voice's output style.** `concise` selects Claude Code's built-in `Concise`
  output style by name. It was verified present in Claude Code 2.1.280; earlier versions,
  including `2.1.278`, the Linux client pinned before 0.14.0, are unverified, and so is whether bridge and
  Agent SDK sessions apply `outputStyle`. Where the style is missing, the stance text still carries
  the reply shapes.

The [runbook](qualification-runbook.md#target-hosts) has the commands that establish each of
these before a round, and `python3 scripts/smoke_tier.py --targets <ids>` refuses the round at
once when a client, a Codex login or the Docker daemon is missing. A Codex login is a
session, not a key, and the acceptance runner's disposable `CODEX_HOME` does not yet carry it, so
a Codex target's evidence can only be produced by hand until it does; no Codex target is
qualified for the current source.

If a release cannot qualify the Codex or the Linux targets, it narrows the support floor the
last qualified release set. That narrowing is stated in this page's opening section as a
decision, as v0.12.0's is, and is never a target silently left out of a round.

### Which source change invalidates which evidence

Evidence is invalidated per target, not per repository. A target's path set is the shared runtime
source — `VERSION`, `bin`, `lib`, `adapters`, `primitives`, `policy`, `templates`,
`config.example.json` — minus every *other* runtime's adapter directory, as the catalog's
`evidence_invalidation` block maps them, **except for the files inside such a directory that
shared code reads whatever runtime is running**. Those are carved back into the shared set and
invalidate every target. The block names them: today `bindings.json` (`bin/citizen tiers` checks both
adapters' class tables in one command), `capabilities.json` (stance coverage and catalog
reconciliation read every runtime's) and `worker.py` (`bin/citizen role run --runtime` chooses the
adapter by flag, so either runtime's worker is reachable from either session). Only `hook.py` and
the observation entry point `observe.py` are private to their runtime: a client executes its own
runtime's hooks, and `bin/citizen sync` writes the other runtime's `hook.py` path into a config file
without reading it.

So a fix confined to `adapters/codex/hook.py` leaves the Claude Code targets of a round standing,
and the reverse holds. A change to shared source, to a carved-out file in any adapter directory,
or to a file under `adapters/` that no runtime owns, still invalidates every target.

The scope fails closed. A runtime the catalog does not map is excluded from nothing and keeps the
whole-source rule; a declared path that is not that runtime's own `adapters/<runtime>` directory,
or that names a runtime no client runs, is rejected; a declaration that carves out no shared file
at all is rejected rather than trusted; exclusions are emitted as literal pathspecs so no glob or
`..` can widen them; and a record that carries no `invalidation_scope`, or one whose scope is
malformed or differs from what the catalog grants, is checked against the whole source or refused.
Each record states the scope it was validated under, so a reviewer reads the assumption from the
artifact instead of recomputing it.

`tests/test_adapter_directory_isolation.py` holds the declaration to the source: it parses every
tracked Python file under the shared paths — failing on one it cannot parse, rather than skipping
it — asserts it still finds the loaders it is meant to cover, and fails when shared code builds a
path to an adapter file the block does not declare, names another runtime's directory outright, or
reaches an adapter through a symlink. What it cannot prove is the `runtime_files` half: that
`hook.py` is only ever loaded for the runtime whose session is running is a maintainer's reading of
the call sites, and a wrong entry there is coupling this scope would miss.

#### Per case, inside the target

Within a target's path set, evidence is also invalidated per acceptance case. The block's
`case_paths` map names, for every required case, the source paths whose change can alter what that
case observes:

```json
"case_paths": {
  "version": 1,
  "cases": {
    "installation": [],
    "role-confinement": ["adapters/claude-code/worker.py", "adapters/codex/worker.py"]
  }
}
```

A path is a literal file or directory under one of the runtime source paths, with no glob, no
`.` or `..` segment and no leading or trailing slash; a directory covers every file under it.
Every required case is a key, so adding a case forces a claim about it, and an empty list claims
the case depends on none of the mapped paths. When the source changes after a record's commit, the
changed files inside the target's path set decide what survives:

- a changed file under a path some case names makes each case that names it **stale**, and leaves
  the record's other cases standing;
- a changed file under no case's paths invalidates the whole record, so the map fails closed:
  shared source such as `bin`, `lib`, `primitives` and `policy` is mapped to no case today, and
  any change to it still costs the round.

A stale result neither passes nor blocks. It describes source that no longer exists, so the claim
needs that case rerun, and a record carrying only the rerun cases, linked beside the older one,
completes it. `bin/citizen compatibility` names a stale case that nothing answers, with the files that
made it stale.

Each new evidence record carries `case_map`, the map's `version` and the SHA-256 of its cases, as
the acceptance runner found them in the catalog. A record whose `case_map` is missing, or differs
from the catalog's, is treated as whole-target scoped: any change in its target's path set
invalidates all of it. So the map's version is what a reviewer reads to see what a record assumed,
and changing the map, whether a new entry or a narrower one, bumps it; the digest makes an edit
that forgets the bump fail closed instead of silently re-scoping older records.

The map is a maintainer's claim about coupling, and the risk is stated plainly: a change that
alters what a case observes through a path the map does not assign to it passes unnoticed for that
case. An entry is added only with its argument here, and the argument names the call sites. Today
there is one:

- **`adapters/<runtime>/worker.py`** is loaded only by `bin/citizen role run`, through
  `harness_core.workers`, and no hook or session start loads it. The runner drives `role run` in
  `role-confinement` and `spawn-confinement`; `cost-posture` verifies a runtime that does not
  route native spawns through a role worker, and `framework-spawn-routing` runs the cost-posture
  turn. Those four cases name both runtimes' workers, since either is reachable from either
  session; the other eight stand when only a worker changes.

`tests/test_evidence_case_scope.py` holds the mechanism to this rule, and checks that every mapped
path exists. See [#582](https://github.com/JakeSelby/model-citizen/issues/582).

A released catalog pins the exact source commit its evidence qualifies. Later development does
not rewrite or invalidate that historical release record, but any change under the runtime-source
paths makes `bin/citizen compatibility --release-check` fail until the new source has its own candidate
catalog and native evidence.

[Runtime controls](runtime-controls.md) records current enforcement gaps. Adapter coverage in
`bin/citizen stances --json` distinguishes instruction policy, hooks and settings, including custom
stances which are advisory by default. No preference overrides a native restriction.

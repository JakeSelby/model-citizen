---
title: Model Citizen product requirements
status: final
created: 2026-09-23
updated: 2026-09-26
supersedes: ../prd-agent-harness-2026-09-19/prd.md
sources:
  - ../../source-ledger.md
  - ../../product-briefs/brief-agent-harness-2026-09-23/brief.md
  - ../../research/
  - addendum.md
---

# PRD: Model Citizen

## 0. Document purpose

This PRD says what Model Citizen must do. It covers what shipped from 0.1 to 0.12, the work merged on
`main` since the 0.12.0 tag, and the roadmap through 1.0.0. It is written for three readers: contributors, the maintainer, and the BMad workflows downstream of
it (the UX specification, the architecture spine, and the epics and story files).

It supersedes the 2026-09-19 PRD, which described only the 0.9 core. FR-1 to FR-12 keep the meaning of that
document's FR1 to FR12, so existing references stay valid. New requirements start at FR-13.

**How to read it**
- Features are grouped in §4, with their functional requirements (FRs) nested under each feature.
- Every FR carries a **Status** that leads with one of four states:
  - `implemented (0.x)`: in the named release tag, as `git tag --contains` reports it. The 0.12.0 release
    notes omit work merged between #465 and #494; the tag, not the notes, decides.
  - `unreleased`: merged to `main` after the 0.12.0 tag, not yet in a release.
  - `partial`: part of the requirement exists and part does not. The line says which part is missing and
    the milestone that carries it.
  - `planned (vX)`: filed against a milestone. `planned (unscheduled, #N)` means filed with no milestone.
  - `planned (backlog)`: accepted, but not scheduled to a milestone before 1.0.
- These states map onto the governance labels. `implemented` and `unreleased` are implemented claims.
  Evidence in the compatibility catalog makes a claim validated. `planned` is proposed. Superseded material
  is historical. Anything else is unknown.
- FRs that bind this repository's maintainers, rather than the CLI a developer installs, say
  **Scope: repository process**.
- Terms are defined once, in §3, and used exactly as defined.
- Rationale, rejected alternatives and sizing data live in [addendum.md](addendum.md). Evidence lives in
  the research artifacts under `../../research/`.
- Inferred points are tagged inline `[ASSUMPTION: ...]` and indexed in §12. Points the maintainer must
  settle are tagged `[NOTE FOR PM]`.

## 1. Vision

Model Citizen is a user-owned layer over the coding-agent runtimes a developer already runs, today Claude
Code and Codex. The developer states a working style once, as rules, skills, roles, workflows and
switchable stances. The harness then:
- projects that style into each runtime's native configuration;
- enforces it with hooks wherever a hook can decide;
- measures whether each rule actually fires.

The harness does not serve models, choose a provider's endpoint or replace a runtime. It maps capability
classes to models the runtime already offers. It turns the instruction files every
developer already writes into policy they can inspect, switch and measure. The rest of the field is
open-loop: an author writes an instruction and hopes. Model Citizen closes the measurement half of that
loop today:
- Every rule names a deterministic detector over the agent's own transcripts, or states in one line why
  none can decide it.
- Lint refuses a rule that does neither.
- `harness usage --rules` reports which rules fired, by repository and by the stance variant that was
  selected.

Acting on that measurement, by proposing stance changes from evidence, is the next half (FR-50). The same
ledger that measures rules also counts tokens, dollars and delegation. A claim about cost is therefore
either a measured figure against a bare runtime, or it is not made.

The harness is opinionated, but every opinion is configurable and optional. It owns only what it chooses
to own, lightly and surgically. Everything else can be switched off or ceded to another framework layered
on top, such as a methodology skill library.

Three commitments make this credible:
- **The developer owns every setting.** Sync previews each change, records what it owns and restores what
  it replaced.
- **Compatibility claims come from native evidence.** Generated files alone never count as support.
- **Planning is public.** The requirements, the architecture and the design of every work item live in
  this repository beside the code they describe.

### 1.1 Why now

- **Developers switch agents weekly, and the surfaces differ.** Each runtime has its own surface for
  instructions, skills, hooks and settings. Copying `CLAUDE.md` into `AGENTS.md` does not carry hooks,
  permissions or lifecycle events. Shared intent needs native adapters and real qualification.
- **Always-loaded instructions grow without measurement.** The standing prefix is the largest single cost
  on short tasks: 12,607 tokens on every call in the first live replay. No project in the field could say
  which rules ever fired.
- **A saving has to be proven.** Token budgets are tightening while fan-out grows. A layer that routes
  unnamed spawns to right-sized workers, and states a budget in every brief, pays for itself only if it
  can prove it. That proof needs a bare comparison arm.
- **Nobody else measures rules.** The 2026-09-21 field scan covered cross-runtime configuration
  compilers, skill packs, context linters and observability platforms. It found no other project that
  binds a detector to each rule and reports per-rule hit rates. The scan is dated, and will be re-checked
  by 2026-12-01.

### 1.2 Positioning

- **Category:** a user-owned agent harness. Technically, a stance-driven, cross-runtime working-style
  layer.
- **Front door:** "Find out which of your agent rules actually fire." Measured rules lead. Portable working
  preferences and switchable stances are the frame.
- **Canonical line:** "the user-owned, stance-driven agent harness: define your working style once, switch
  personal stances independently, and project them into Claude Code and Codex through native adapters."
- **Bounded novelty claim:** the only project we know of that binds a detector to each rule file, fails
  lint on an unmeasured rule, and reports per-rule hit rate by repository and by preference variant.
- **Terms not used as the lead:**
  - "multimodal", which means text, image and audio;
  - "multi-model", which implies routing or serving models;
  - "model-agnostic", which is accurate but crowded.
- **Claims never made:** universal compatibility, identical behaviour across runtimes, being first, or an
  unmeasured saving.
- **Amended 2026-09-24:** the layered, configurable, measurable framing is in
  [the vision amendment](#amendment-2026-09-24-a-layered-configurable-measurable-harness).

### Amendment 2026-09-24: a layered, configurable, measurable harness

Added after the original run; sections 1 to 1.2 above are unchanged and dated 2026-09-23. No requirement
ID, success metric or other section changes with this amendment. The same amendment is recorded in the
[product brief](../../product-briefs/brief-agent-harness-2026-09-23/brief.md#amendment-2026-09-24-a-layered-configurable-measurable-harness).

**What the product is.** It is a lightweight harness you layer alongside whatever agent runtime you use
(Claude Code, Codex and others), however you orchestrate it: native subagents, workflow scripts, or
methodologies such as BMad or Superpowers. It is built from independent layers. Every layer, and every
module within one, can be switched on, off or configured. A user keeps the layers they like and turns ours
off where they already have their own. The harness then cedes that whole area to the other tool rather than
trimming inside it. The north star is running beside a methodology such as Superpowers, with the user
choosing which layer comes from which source.

**The layers.**
- **Observation:** telemetry, meaning ledger, OTel export and transcripts. It must add nothing to the
  model's context.
- **Instruction:** what the model reads. Rules, stances, repository instructions, the session prompt and
  the output style.
- **Capability:** on-demand skills, agent roles and commands.
- **Enforcement:** hooks and guards at tool and turn boundaries.
- **Orchestration:** delegation, choosing a model class and effort for each role inside the host, and
  the context lifecycle (trimming, compaction, clearing, handoff). It never selects a provider endpoint or
  serves a model.
- **Advisory:** recommendations to the human, such as starting a fresh session, clearing context or
  reviewing a plan.
- **Economy** is a concern rather than a layer. Cost posture, budgets and prices run through the
  instruction, enforcement and orchestration layers, and each can be switched on its own. By default
  nothing is denied for cost: budgets inform. A spend cap is an opt-in module that denies only when the
  user switches it on.
- A control plane of profiles, toggles and configuration composes the layers, and an evaluation plane
  measures them.

**Measurable by construction.** Every module declares three things: what it claims to improve, what it
costs to carry, and the instrument that measures it. A module with no instrument is reported as
unmeasured, never as working. Telemetry runs in every arm, including a bare runtime with nothing of ours
loaded, so the harness can measure the baseline it is compared against.

**How it is evaluated.** There are four comparisons, and each runs at the cheapest tier that can answer
it:
1. The whole harness against the bare runtime.
2. One rule, skill or hook in a minimal profile: first alone, then with the economy concern switched on.
   A unit eval never pays for the full context.
3. One of our layers against an external equivalent in the same slot, with everything else held fixed.
4. Permutations of switches within and across layers: screened with fractional factorial designs, then
   tested in combination.

Every comparison is measured on three axes: adherence (did the agent follow it), effectiveness (did the
outcome improve) and efficiency (what it cost). Advisory-layer effects are reported separately, as
estimates that depend on the user following the advice.

**Why.** Agent tooling mostly ships as monoliths that bundle methodology, instructions and enforcement. A
user cannot tell which part helps, or combine the best parts of several. The harness sits beneath them as
a substrate: the control plane, telemetry and evaluation that make any combination configurable and
measurable.

## 2. Target user

### 2.1 Jobs to be done

- **Functional:**
  - Keep one working style across Claude Code and Codex, without maintaining two sets of instruction files
    that drift apart.
  - Find out which instructions an agent actually follows, prune the dead ones, and stop paying context
    tokens for rules that never fire.
  - Spend less on agent work without capping the agent: right-sized models for routine spawns, budgets the
    agent can pace against, and a live view of spend by role and day.
  - Give agents room to act, with guardrails wherever a deterministic check is cheap: irreversible commands
    graded, no turn ending on a red gate, tool output treated as data.
  - Read answers and plans quickly, on any screen.
- **Emotional:** trust that a tool managing configuration never silently overwrites a hand edit, and can be
  removed cleanly.
- **Social:** point colleagues and readers at measured evidence, not adjectives.
- **Contextual:** run many agent sessions at once, locally and from a phone, across repositories and
  worktrees, without the sessions corrupting each other.

### 2.2 Non-users (v1)

- **Teams that want a hosted control plane or shared organisation policy.** The multi-user surface is the
  post-1.0 ceiling, not a commitment.
- **Developers on native Windows.** It is unsupported, and WSL2 is unqualified.
- **Anyone looking for a model router, an API gateway or a replacement agent runtime.**
- **Anyone who wants a configuration nobody inspects.** Every managed change is previewed, and every claim
  carries its status.

### 2.3 Key user journeys

- **UJ-1. Riley evaluates the harness without surrendering their configuration.**
  - **Persona and context:** Riley has tuned Claude Code and Codex setups and distrusts tools that rewrite
    them.
  - **Entry state:** a clone of the `stable` branch, with no harness state yet.
  - **Path:**
    1. Riley runs `harness init`, selects both runtimes and leaves editor management off.
    2. Riley runs `harness sync --dry-run` and reads the grouped preview.
    3. The run stops on one conflicting file that Riley owns.
  - **Climax:** the preview names every link, rendered file and setting, and refuses to adopt the conflict
    by default.
  - **Resolution:** Riley applies. `harness doctor` reports local activation and drift, and `harness
    compatibility` reports published qualification. `harness uninstall` would restore what sync replaced.
  - **Edge case:** Riley has hand-edited a managed setting since the last sync. Uninstall leaves that edit
    and reports it, instead of restoring the older value.
- **UJ-2. Morgan changes one preference across both runtimes.**
  - **Persona and context:** Morgan wants subagents to run on right-sized models.
  - **Path:**
    1. Morgan switches the `delegation` stance in the user configuration.
    2. Morgan inspects `harness stances`, which shows the effective selection and its source.
       `harness stances --json` also shows each adapter's coverage.
    3. Morgan syncs.
  - **Climax:** one authored choice reaches both native formats, and the coverage shows where a hook backs
    it and where it is an instruction only.
  - **Edge case:** a project-level selection changes what the hooks resolve. The linked stance text stays
    user-level until #276 is decided.
    *Amended 2026-09-26:* #276 is decided. The linked text stays user-level, and the session-start hook
    injects the differing variant's text, or a pointer to it when it does not fit the always-loaded budget.
- **UJ-3. Sam finds out which of their rules actually fire.**
  - **Persona and context:** Sam keeps a 300-line instruction file and suspects most of it is dead weight.
  - **Path:**
    1. Sam runs the standalone instrument, `ruleprobe`, over their own recent transcripts. Nothing is
       installed into the runtime.
    2. With `--rules` pointed at their rule directory, Sam sees each rule's state (measured, dark or
       unmeasured) and the hit rate of every measured rule.
    3. Sam writes a declarative detector for two important rules that are unmeasured.
  - **Climax:** Sam deletes the rules measured as never firing, and watches the standing prefix shrink.
- **UJ-4. Dana runs a parallel build under a cost posture.**
  - **Persona and context:** Dana orchestrates six builders and reviewers from one session.
  - **Path:**
    1. Every brief carries a soft budget, and unnamed spawns land on band workers.
    2. The live usage feed shows each subagent's spend against its budget.
    3. `harness usage --by role`, then `--by day`, shows where the money went.
  - **Climax:** the fresh-session nudge suggests handing off before re-reading context dominates the cost.
  - **Edge case:** a subagent overruns its budget. Nothing is killed. The overrun lands in the usage ledger
    for the next posture review.
- **UJ-5. Casey contributes a fix.**
  - **Persona and context:** Casey is an outside contributor who has never talked to the maintainer.
  - **Path:**
    1. Casey starts from a GitHub issue and opens its story file, which holds the design, acceptance
       criteria, tasks and cited dev notes.
    2. Casey works in a managed worktree and runs the exact gate CI runs.
    3. Casey opens one pull request that closes the issue and brings the story file up to date.
  - **Climax:** a reviewer follows issue, story file, diff and evidence without a private transcript.
- **UJ-6. Lee layers the harness under a skill library (planned 0.16).**
  - **Persona and context:** Lee already uses a methodology skill library, and does not want two opinions
    fighting over the same step.
  - **Path:**
    1. Lee selects the `superpowers` mode.
    2. The harness keeps its enforcement hooks, cost routing and measurement.
    3. It cedes plan ceremony, testing and workflow process to the library.
  - **Climax:** the four-arm benchmark shows the combined setup's cost against each one alone.
- **UJ-7. Jordan, the maintainer, cuts a release.**
  - **Persona and context:** Jordan merges work from several agent sessions a day and releases by
    milestone.
  - **Path:**
    1. The milestone empties, and Jordan freezes the candidate on a release branch.
    2. Jordan runs the model-free smoke tier, then one native qualification round per required target.
    3. Jordan works the five release surfaces in order.
  - **Climax:** the tag, compatibility catalog, changelog, `stable` branch and GitHub About all name the
    same revision and status.
  - **Edge case:** a change under one runtime's adapter directory invalidates only that runtime's targets.
    Jordan re-runs two targets, not four. A change to shared source still invalidates every target.
- **UJ-8. Kai keeps remote sessions alive through restarts.**
  - **Persona and context:** Kai drives agents from a phone through Remote Control hosts.
  - **Path:** the machine sleeps, the network drops, and another agent restarts a host.
  - **Climax:** `harness remote-control heal` keeps each host on its environment and reconnects dropped
    sessions. Kai never sees a detached workspace for a session the harness can still reach.
  - **Edge case:** a session the server has already archived is past recovery. `status` leaves it out, and
    heal never reports it as healed.
- **UJ-9. Ari tries a decision provider without risking a decision (planned 0.16).**
  - **Persona and context:** Ari wants a second opinion on stop claims, but will not let a model relax a
    guardrail.
  - **Path:**
    1. Ari enables the `local` provider at the stop-claim decision point, in the `shadow` stage.
    2. Ari runs `harness decisions eval` against the held-out question pack.
  - **Climax:** the eval shows agreement, calibration and cost, and the point moves to `advise` only when
    it meets its written criterion.

## 3. Glossary

- **Runtime:** the agent program that holds the loop, tools and orchestration, such as Claude Code, Codex
  or Cursor. It is distinct from the **model provider** that serves the model, and from the **client
  surface** (CLI, editor, desktop app) through which the runtime is used.
- **Primitive:** an authored unit of working style: a rule, skill, role, workflow or stance. Primitives are
  provider-neutral.
- **Primitive catalog:** the one provider-neutral collection of primitives in the checkout. External
  **primitive roots** add to it.
- **Stance:** a switchable preference, with named **variants** under a **dimension**. For example,
  `autonomy` has `ask`, `confirm-writes` and `execute`. A stance is either:
  - a **prose stance**, which changes instruction text; or
  - an **enforced stance**, which also changes hooks or rendered settings.
- **Setting:** a validated configuration value the policy engine reads, such as the `telemetry` block. A
  setting is not a stance.
- **Preset:** a named bundle of stance defaults that `harness init` offers (`general`, `software`).
- **Selection:** the variant chosen per dimension. It resolves by precedence: defaults, then user, then
  project, then session.
- **Invariant:** behaviour no stance can switch off. The invariants are truthfulness, secret protection,
  authorization boundaries and native restrictions.
- **Adapter:** the runtime-specific code that turns resolved policy into native configuration. Its output is
  a **projection**.
- **Sync:** the operation that applies projections to a runtime's configuration. It records its changes in
  the ownership journal.
- **Ownership journal:** the record of each managed field's prior and applied values. Uninstall and rollback
  use it.
- **Hook:** a runtime event handler the harness installs, which enforces a check at the moment of action.
- **Permission mode:** the runtime's own permission setting, such as default, plan, auto or bypass. The
  `autonomy` stance decides what the harness's hooks allow within that mode.
- **Command grade:** the risk class of a shell command, from 0 (read-only) to 3 (irreversible). The
  `autonomy` stance sets the threshold above which a command is gated. The permission mode decides whether
  a gated command asks or is denied.
- **Gate:** the repository's own lint and test commands, named in its agent instructions.
- **Stop gate:** the hook that refuses to end a turn while the gate is red in a trusted checkout.
- **Detector:** a deterministic check over transcripts that decides whether a rule fired. A **declarative
  detector** is written as data, not code.
- **Measured rule:** a rule with a detector, or with a one-line reason why no transcript can decide it.
- **Usage ledger:** the local, append-only record of sessions, subagents and workers: tokens, dollars,
  rules and decisions. It is the system of record for every measurement.
- **Decision log:** the local `decisions.jsonl` file of hook decisions: every ask and deny, a sample of
  allowed shell commands, and their outcomes. It is separate from the usage ledger, and it is not part of
  export. Provider calls are logged as `decision` rows in the usage ledger instead.
- **Cost posture:** the resolved `cost` stance variant. It sets a capability class, an effort level and a
  soft budget per role.
- **Capability class:** the provider-neutral model tier a role needs: `frontier`, `strong`, `standard` or
  `light`. An adapter's bindings map each class to a native model.
- **Band worker:** the worker (`worker-a`, `worker-b` or `worker-c`) that an unnamed spawn is routed to.
- **Soft budget:** the expected output tokens and tool calls stated in a brief. It informs, and never stops
  the agent.
- **Brief:** the prompt an orchestrator sends a subagent.
- **Role worker:** a constrained role run as a separate, isolated CLI process, with restricted tools and
  input roots.
- **Decision point:** a place where the harness makes a deterministic decision, such as a stop claim, the
  ask band of command grading, or a spawn route.
- **Decision provider:** an optional judge consulted at a decision point:
  - `none`;
  - `local`;
  - `jev`, a remote third-party provider that is opt-in.

  Each decision point runs its provider at one **stage**: `off`, `shadow`, `advise` or `act`.
- **Integration:** a third-party framework or tool the harness governs, declared by an **integration
  descriptor**, which is data, not code.
- **Target:** a runtime, client surface and operating system combination tracked by the compatibility
  catalog.
- **Compatibility catalog:** the versioned record of targets, their required cases and their evidence.
- **Qualification:** native evidence that a target passes its required cases for an exact source revision.
- **Evidence record:** the stored result of a qualification case, pinned to a commit.
- **Source paths:** the runtime source directories whose change invalidates qualification evidence:
  `VERSION`, `bin`, `lib`, `adapters`, `primitives`, `policy`, `templates` and `config.example.json`.
- **Release surface:** one of the five places a release must reach:
  1. source and evidence;
  2. release metadata;
  3. tag and GitHub release;
  4. GitHub About;
  5. development resumes.
- **Always-loaded context:** the instruction text a runtime loads into every session. Its size is the
  **standing prefix**. The **static estimate** is counted from files. The **live prefix** is measured
  from paired transcripts.
- **Bare arm:** a benchmark run of the runtime with no harness and no user layer, under the isolation
  invariants.
- **Same-day ratio:** harness cost per passed task divided by bare cost per passed task, both measured the
  same day on the same model.
- **Release task set:** the eight benchmark tasks the live replay runs per release.
- **Work item:** a GitHub issue with a BMad ID. Its **story file** is the design record: context,
  acceptance criteria, design, tasks, dev notes, dev record and review findings. A pull request's
  **delivery issue** is the work item it closes. Its story file is the **delivery story**.
- **Selection model (planned 0.14, #554):** one document that switches every rule, hook, skill, workflow and role
  on or off. A **mode** is a named bundle of switches.

## 4. Features

### 4.1 Shared primitives and native projection

**Description:** A developer authors rules, skills, roles, workflows and stances once, in the primitive
catalog. Adapters for Claude Code and Codex translate them into native formats. Where a runtime lacks a
capability, its adapter says so rather than faking parity. A developer who already has instruction files
imports them instead of rewriting them. This feature realizes UJ-1 and UJ-2.

#### FR-1: One provider-neutral catalog
The system must author rules, skills, roles, workflows and stances once, in the primitive catalog.
**Status:** implemented (0.9).

**Consequences (testable):**
- `citizen catalog` lists every primitive exactly once, with no runtime-specific copy of its meaning.
- Adding a runtime adapter requires no second authoritative copy of any primitive.

#### FR-3: Native projections without claimed parity
Claude Code and Codex adapters must translate the resolved policy into each runtime's native, supported
formats. Each adapter must also publish, per capability, how its runtime carries it. **Status:** implemented
(0.9); the per-capability matrix is in 0.12 (#472).

**Consequences (testable):**
- `citizen generate --check` reports no drift between the primitive catalog and the generated projections.
- `adapters/<runtime>/capabilities.json` gives each capability a mode, `instruction` or
  `instruction-and-hook`, and a qualification state. The tier restriction is recorded as `enforced`,
  `advisory` or `none`.

#### FR-10: Extensible without a second authority
Contributors must be able to add or replace primitives, including stance dimensions, without modifying a
runtime-specific authority. **Status:** implemented (0.9).

**Consequences (testable):**
- A new stance dimension is a folder of Markdown files, and `citizen stances` lists it after sync.
- An external primitive root can supply primitives without editing the checkout.
- A custom stance never inherits a built-in variant's execution semantics. It is prose unless it declares
  what it enforces.

#### FR-13: Import existing instruction files
A developer can import an existing `CLAUDE.md`, `AGENTS.md` or `.cursorrules` into an external primitive
root, and sync projects it alongside the primitive catalog. **Status:** implemented (0.12, #478, #484).

**Consequences (testable):**
- `citizen import` writes primitives under an external root and never edits the source file.
- Sync refuses a primitive whose name collides with another root's, and names both files.
- The detector-or-reason lint gate (FR-19) covers the primitive catalog, not external roots. Reporting
  imported rules as unmeasured is planned with FR-20.

### 4.2 Stances and selection

**Description:** A preference becomes a stance when a competent developer could reasonably choose the
opposite. Invariants are never stances. Selection resolves once into one effective policy, and every
consumer reads that policy: hooks, rendered files, telemetry and workers. This feature realizes UJ-2 and
UJ-6.

#### FR-2: One effective selection
The system must resolve user, project and session selections into one explicit effective policy before any
projection. **Status:** implemented (0.9); from 0.14.0 the session-start hook injects a differing project
or session selection (#276), and sync projects user-level selections only, by design (#294).

**Consequences (testable):**
- `citizen stances` shows each dimension's effective variant, the layer it came from, and each adapter's
  coverage.
- A session override never rewrites the user's global selection.
- Every hook reads the same effective policy that `citizen stances` prints.
- Project configuration may select stances only. Identity, targets and permissions stay user-owned.
  *Amended 2026-09-25:* a project file may carry any selection key, so it may also switch rules,
  hooks, skills, workflows and roles and name a mode (#554, FR-15). Identity, targets, permissions,
  runtime flags, `primitive_roots` and telemetry stay user-owned; a project file that sets one is
  refused with a message naming the key.
- *Amended 2026-09-26 (#276):* a project or session stance selection that differs from the synced one
  reaches the model at session start as that variant's text, counted against the always-loaded token cap
  less what the sync already loads; a variant that does not fit is named with a pointer to its file. A
  session whose selection matches the synced one gets nothing injected.

**Out of scope:** stances that weaken an invariant.

#### FR-14: Stance constraints
The system must refuse a selection that violates a declared constraint between dimensions, or between a
dimension and a role binding. **Status:** implemented (0.12, four constraints, #473).

**Consequences (testable):**
- `sync` refuses a selection that violates `primitives/constraints.json` and names the constraint.
  `citizen stances` prints the conflict and still exits 0, so the selection can be inspected.

#### FR-15: Selection model with switches for every primitive
A developer must be able to switch every rule, hook, skill, workflow and role on or off by id, in one
selection document with one resolver. **Status:** planned (v0.14.0, #554, #555, #556).

**Consequences (testable):**
- Each primitive kind has a switch kind, and a disabled primitive is absent from every projection.
- Core hooks (command grading and the stop gate) can be disabled only when
  `core_switches_acknowledged: true` is set (#552).

#### FR-16: Modes
A developer must be able to select a named mode for a session or a repository. **Status:** planned
(v0.14.0, #557; v0.16.0, #558, the `superpowers` mode).

**Consequences (testable):**
- `HARNESS_MODE` selects a mode for one session without changing stored selections.
- A mode takes effect for a developer who ran `citizen init`. After a default `init`, selecting the
  `superpowers` mode changes `plan-ceremony`.
- `citizen selection` names every mode key that a user key shadowed.
- The `superpowers` mode keeps every enforcement hook and cedes process primitives to the layered library.
  Its detection reports when that library is present.
- `delegated` variants of `plan-ceremony` and `testing` keep those dimensions explicit when a mode cedes
  them.
- "No user present" is a session fact in the selection. In a headless session, `plan-ceremony` and the
  `delegated` variants proceed instead of waiting for a go-ahead.

**Out of scope:** trimming a layered library's own skills. The harness switches its own units and cedes
whole areas; it cannot edit the library. Evidence:
`../../research/competitive-configurability-and-selection-models-2026-09-23/`.

### 4.3 Configuration lifecycle

**Description:** Install, sync, repair, upgrade and uninstall preserve everything the harness does not own.
The ownership journal makes every change reversible, and ambiguous conflicts fail visibly. This feature
realizes UJ-1.

#### FR-4: Preview before apply
A developer must be able to preview every managed change before applying it. **Status:** implemented
(0.3).

**Consequences (testable):**
- `citizen sync --dry-run` lists every link, rendered file, setting and conflict, grouped by runtime and
  owner, and changes nothing.

#### FR-5: Ownership journal
Applied state must record prior and managed values. Uninstall and rollback then restore only what the
harness owns and what still matches what it applied. **Status:** implemented (0.9).

**Consequences (testable):**
- Uninstall restores each managed field whose current value equals the applied value, and reports each
  field that has changed since.
- Sync owns each native setting at the field level. For example, each Claude Code `env` variable is owned
  individually, never the whole map.

#### FR-17: Install and update channels
A developer must be able to install from a one-line installer, or from a clone of the `stable` branch,
which tracks the latest release. **Status:** implemented (0.11.1, `stable`; 0.12, the installer, #479).

**Consequences (testable):**
- `stable` fast-forwards to each release tag after publication, and never moves backward.
- The installer clones `stable`, runs `citizen init --yes`, then `citizen install --dry-run`. It applies
  nothing until the developer runs the install for real.

#### FR-18: Plugin listing as a sampler
The system must publish a Claude Code plugin listing that exposes the primitive catalog without its hooks.
Plugin hooks would merge with user hooks and fire twice, so the plugin never replaces `citizen sync`.
**Status:** partial: implemented (0.12, #475). The plugin's constrained roles run as native agents with no
hook to refuse their native spawn, and the manifest's version lags `VERSION`. Both are planned for
correction (unscheduled).

**Consequences (testable):**
- The plugin manifest declares no hooks, and its version equals `VERSION`.
- The docs say that full enforcement, including role confinement (FR-40, FR-41), needs `citizen sync`.

#### FR-66: Upgrade, rollback and migration
Upgrading between releases must migrate configuration forward, carry the ownership journal, and keep the
usage ledger readable. Rolling back to the prior release must restore the prior managed state without
losing unrelated files. From 1.0, `docs/compatibility-policy.md` governs every change to the public surface
(§7). **Status:** partial:
- implemented (0.10): the lifecycle acceptance runner and the compatibility, deprecation, migration and
  rollback policy (#208);
- planned (v1.0.0, #209): the proof against the 1.0 candidate.

**Consequences (testable):**
- The lifecycle runner passes on the reference environments, covering:
  - clean install;
  - repeat sync;
  - upgrade from the prior baseline;
  - rollback;
  - conflict preservation;
  - uninstall.
- Rollback comparison is semantic: JSON documents are compared as data, and shell lines are normalized. It
  is never a byte snapshot.
- A configuration key renamed across versions is migrated on upgrade, and its old name is accepted for at
  least one release.

#### FR-69: Initialize and configure
A developer must be able to create a first configuration interactively or in one scripted command, and to
read or set individual keys. **Status:** implemented (0.8).

**Consequences (testable):**
- `citizen init --yes --preset software` writes a configuration without asking anything.
- `citizen config get|set` accepts only identity, stances, permissions, runtime booleans, primitive
  roots, and the `integrations.*` and `governance.*` keys.
- `citizen init` refuses to replace an existing configuration without `--force`.

#### FR-70: Machine setup, editor settings and templates
The system must set up a machine for the harness with `citizen install`: tools, apps, and VS Code settings
and extensions. It must also ship a Codex configuration example and a repository template. The editor
surfaces are labelled preview. **Status:** implemented (0.9, preview).

**Consequences (testable):**
- `citizen install --dry-run` lists every package, app, setting and extension it would install, and
  changes nothing.
- Editor management is off unless the developer selects it, and its projections appear in the dry-run
  preview like any other.
- `templates/repo/` carries the agent instructions a new repository needs to adopt the harness's
  conventions.

### 4.4 Measured rules

**Description:** This is the product's lead capability. Every rule names a deterministic detector over the
agent's own transcripts, or a one-line reason why nothing in a transcript can decide it. The instrument
reports how often each rule fired. It runs standalone, so a developer can measure their own rules before
adopting anything. This feature realizes UJ-3.

#### FR-19: Detector or reason for every rule
Lint must fail when a rule in the primitive catalog names neither a detector nor a one-line reason for
having none. **Status:** implemented (0.6, the lint gate; 0.12, the engine vendored from `ruleprobe`, #488).

**Consequences (testable):**
- Removing a rule's detector binding fails `citizen lint`, and the failure names the rule.
- The detector registry holds 17 detectors: 6 generic ones from the vendored engine, and 11 specific to
  this repository.

#### FR-20: Per-rule hit rate
`citizen usage --rules` must report each rule's hit rate by repository, and by the stance variant selected
when the session ran. **Status:** partial: implemented (0.12), with the report keyed by detector.
Planned for v0.15.0, with the module scorecard: listing each unmeasured rule with its reason, and the share
of rules measured.
Planned for v0.14.0: grouping by mode and by switch state.

**Consequences (testable):**
- A detector with zero hits over the reporting window is listed, not omitted.
- Rules with no detector are listed as unmeasured, with their reasons. (Planned, v0.15.0.)
- A rule that is switched on but never fires appears as a mismatch line. (Planned, v0.14.0.)

#### FR-21: Standalone instrument
A developer must be able to run the measurement over their own transcripts without installing the
harness's way of working. **Status:** implemented (0.12; `ruleprobe` 0.1.0 is published on PyPI).

**Consequences (testable):**
- `ruleprobe` runs under `uvx` against a transcript directory, and reports per-rule hits for the generic
  and declarative detectors. Given `--rules <dir>`, it classes each rule as measured, dark or unmeasured.
- The harness vendors a pinned `ruleprobe` wheel, so a `ruleprobe` release means a harness version bump.

#### FR-67: Detectors for a developer's own rules
A developer must be able to bind a detector to any of their own rules without writing code, and to see what
share of their rules is measured. **Status:** partial:
- implemented (0.12): declarative detectors in standalone `ruleprobe` 0.1.
- planned (unscheduled: the 2026-09-24 roadmap does not place it): loading them in `citizen usage --rules`.
- planned (unscheduled: the 2026-09-24 roadmap does not place it): proposing a detector from a rule's
  prose.

**Consequences (testable):**
- A declarative detector in `.ruleprobe/detectors.yaml` is picked up by `ruleprobe` without a code change,
  and by `citizen usage --rules` once loading lands.
- A report run with the developer's rule directory states the share of rules measured and lists the
  unmeasured rules.

#### FR-22: Detector validity
The system must score detectors against a labelled corpus in CI, and fail when a detector falls below the
precision floor. **Status:** partial: unreleased (#601). Two detectors sit under the floor (#602,
v0.15.0).

**Consequences (testable):**
- CI runs the corpus score and fails when a detector falls below 0.9 precision.
- The docs say that agreement with the corpus is not field accuracy.

### 4.5 Usage ledger, pricing and telemetry

**Description:** One local ledger records every session, subagent and worker on both runtimes, and it is
the system of record. Pricing turns tokens into list-price-equivalent dollars. Export copies the ledger to
any OpenTelemetry backend; that backend is a replica, rebuilt by replay. Nothing requires a remote service.
This feature realizes UJ-3 and UJ-4.

#### FR-11: Local-first observability
Local measurements must distinguish known, partial, unavailable and failed data, and must not require
remote telemetry. **Status:** implemented (0.9).

**Consequences (testable):**
- With no export configured, every `citizen usage` view works from local files.
- A row built from total-only data is marked `partial`, never presented as complete.

#### FR-23: Cross-runtime usage ledger
The usage ledger must record Claude Code and Codex sessions, subagents and role workers, with per-day
slices, the harness version and the session effort. **Status:** implemented (0.12); the deduplication ratio is unreleased (#587).

**Consequences (testable):**
- Codex subagent threads are linked to their parents, and archived sessions are read.
- `citizen usage` supports `--by day`, `repo`, `model`, `role`, `rule`, `stance`, `decision`, `provider`
  and `prefix`, and reports the deduplication ratio.
- `--by role` marks any role with fewer than 30 samples.

#### FR-24: Honest pricing
Dollars must come from a dated, overridable price table, as list-price equivalents, and never be presented
as an invoice. **Status:** implemented (0.12); exact-match pricing is unreleased (#549).

**Consequences (testable):**
- A model missing from the price table is reported as unpriced. It is never priced at $0, and never at a
  family rate.
- `citizen doctor` warns when the price table is more than 90 days old.

#### FR-25: OTLP export with replay
The system must export usage ledger rows as OTLP log records to any endpoint the developer configures.
Export is off by default, uses only the standard library, never blocks a hook, and can be replayed from the
ledger. **Status:** implemented (0.12).

**Consequences (testable):**
- Delivery is at least once. `citizen usage export --since` can replay the ledger at any time.
  Readers deduplicate on `harness.row_key`, keeping the greatest `harness.exported_at`, as the documented
  query does.
- Export settings live in the `telemetry` setting, not a stance. Their secret comes from the environment or
  a headers file.
- Exported rows follow the documented ledger schema. A backend needs only the documented deduplication
  query, not a harness-specific adapter. `[ASSUMPTION: schema stability before 1.0 is best-effort]`

#### FR-26: Native telemetry pass-through
Sync must be able to switch on each runtime's native OpenTelemetry output, stamped with harness labels, for
a configured list of runtimes. **Status:** implemented (0.12, including the runtime list, #492).

**Consequences (testable):**
- A conflicting existing value for a native variable is reported and left alone.
- The docs warn that Claude Code's native export carries account identity fields.

#### FR-27: Decision log
Hooks must append to the local decision log:
- each ask and deny decision;
- a one-in-twenty sample of allowed shell commands;
- the observed outcomes.

The log is controlled by a setting, and its `input` field is capped. **Status:** implemented (0.12); the
sampling and completion claims are unreleased (#574, #593).

**Consequences (testable):**
- `citizen usage --by decision` reports decisions by decision point, answer and outcome.
- Each row's `input` is capped at 2 KiB. The log stays local, is not part of export, and stops when
  `telemetry.decisions` is off.

### 4.6 Cost posture and delegation

**Description:** The `cost` stance resolves to a cost posture: a capability class, an effort level and a
soft budget for each role. Unnamed spawns go to band workers. Every brief states its budget, and the
orchestrator sees live spend. Hooks do the deterministic part: routing, and refusing an undeclared frontier
request. Everything else is information the agent paces itself with: soft, never a cap. This feature
realizes UJ-4.

#### FR-28: Cost postures
The system must provide the `frugal`, `balanced` and `max` postures, plus custom postures through
`extends`. Each resolves the class, effort and soft budget per role. **Status:** implemented (0.11).

**Consequences (testable):**
- Agent definitions render from the resolved posture, and they change after sync when the posture changes.

#### FR-29: Capability classes
Roles must request a capability class. Adapter bindings map classes to native models, and `frontier` is
available only by explicit declaration. A developer's `role_bindings` may override one role's model and
effort, and nothing else. **Status:** implemented (0.11).

**Consequences (testable):**
- `citizen tiers check` fails when an adapter's class-to-model table names a model its runtime does not
  offer.
- A spawn that requests `frontier` without a declaration is refused on runtimes that enforce the ceiling.
  The compatibility matrix says which runtimes those are.

#### FR-30: Band routing for unnamed spawns
A spawn that names no role must be routed to the cost posture's default band worker. An orchestrator picks
a different band by naming `worker-a`, `worker-b` or `worker-c`. Routing must resume after a mid-session
change to the runtime's agent registry. **Status:** partial:
- implemented (0.11);
- routing resume is unreleased (#584);
- the Workflow tool's `agent()` calls bypass routing (#576, v0.14.0).

**Consequences (testable):**
- An unnamed spawn is rewritten to the posture's default band, and the usage ledger records the band.
- After the runtime's agent listing changes mid-session, the next unnamed spawn is still routed (#584).

#### FR-31: Soft budgets in every brief
Every brief must state its expected output tokens and tool calls. The budget informs the agent and never
stops it. **Status:** implemented (0.11).

**Consequences (testable):**
- The brief guard adds a budget to a brief that lacks one. The `model-wrote-no-cap` detector counts how
  often the model omitted it.
- No hook denies an action for cost.

#### FR-32: Live usage feed and fresh-session nudge
The orchestrator must see each turn's and each subagent's spend against its budget. It must also be told,
once per threshold, when context size makes a fresh session cheaper. **Status:** implemented (0.11, feed);
the nudge is unreleased (#580), with starting thresholds. `[ASSUMPTION: the thresholds are recalibrated from ledger
data before 0.15]`

**Consequences (testable):**
- The nudge measures the latest turn's context size, not cumulative output, and prints once per threshold.
- On a runtime version with no prompt-submit event, the nudge is declared uncovered. For Codex this is
  decided per client version by a probe, because recorded evidence and current documentation disagree
  (§11 Q9).

#### FR-33: Checks bind subagents
A check that binds a session must also bind its subagents. Each level decides what it can and hands the
rest up, and only the top session asks the user. **Status:** implemented (0.11).

**Consequences (testable):**
- A subagent that hits a gated action returns it as pending, instead of asking the user.

#### FR-34: Delegation that fires
The delegation stance must produce subagent spawns wherever the work pays for them. The measured
break-even is 4.8 to 7.6 absorbed calls. **Status:** partial: there were zero spawns in 19 headless runs,
then zero in 40 (#429). A PostToolUse nudge is planned (v0.15.0, #513).

**Consequences (testable):**
- On a benchmark task above the break-even, the harness arm spawns at least once under `cost=balanced`.

### 4.7 Guardrails

**Description:** Guardrails sit wherever a deterministic check is cheap, and they leave the rest to
judgment. They are the part of the harness that refuses. This feature realizes UJ-1 and UJ-4.

#### FR-35: Graded shell commands
Every shell command must be graded from 0 (read-only) to 3 (irreversible). The `autonomy` stance sets the
threshold above which a command is gated. A gated command asks in a prompting permission mode, and is
denied in `auto` or `bypass`. **Status:** implemented (0.6).

**Consequences (testable):**
- Force-push, branch deletion and destructive checkouts are grade 3, and are gated under every autonomy
  variant.
- A command prefixed with `HARNESS_CONFIRMED=1` records the developer's explicit confirmation, and passes.
- A read-only command on the allowlist runs without asking, under every variant.

#### FR-36: Stop gate in trusted checkouts
In a checkout the developer has trusted, a turn must not end while the gate is red. **Status:**
implemented (0.4). The fix for interleaved sessions in one checkout resetting each other's block count is
implemented (0.13, #611).

**Consequences (testable):**
- The stop gate blocks the turn from ending while the gate fails. After eight consecutive blocks, it
  releases the turn and says why.
- Gate results are keyed to repository content, including staged and untracked changes.
- A timeout or an error never yields a clean result.

#### FR-37: Untrusted tool output and output filters
Text that comes back from a tool must be treated as data. Instruction-shaped content is neutralised and
flagged; this covers control tags, directives addressed to the agent, and settings or permission JSON.
Noisy test and build output is filtered before it reaches the model. **Status:** partial: implemented
(0.10), but output-filter rewrites never reach Codex (#292, v0.17.0).

**Consequences (testable):**
- A tool result that contains an instruction-shaped pattern reaches the agent with a data-only notice.
- On Claude Code, only a filtered run's failures, summary and tail enter the transcript. The full output
  is not retained, so a developer who needs it re-runs the command unfiltered.

#### FR-38: Secret, personal-data and context-budget lint
Lint must refuse the following in tracked files:
- secrets;
- personal strings;
- private paths.

It must also refuse always-loaded context over 200 lines, or over 4,202 tokens. **Status:** implemented
(0.1; the token cap in 0.12, #474).

**Consequences (testable):**
- `citizen lint` fails on a credential pattern.
- `citizen lint` fails when always-loaded context passes either cap.
- Untracked scratch under `.agent-harness/` is skipped. Tracked files there are linted.

#### FR-39: Plan mode investigates under the developer's permission mode
In the runtime's plan mode, read-only investigation must run under the developer's selected permission
mode. Plan mode must still force a plan, questions, and a wait for approval. **Status:** implemented (0.12),
Claude Code only; Codex rejects `allow`.

**Consequences (testable):**
- Under a `bypass` or `auto` permission mode, grade 0–1 commands are allowed in plan mode, grade 2 asks,
  and grade 3 is unchanged.
- In plan mode, web fetches are allowed without a prompt (`allow-plan-webfetch`).

### 4.8 Roles, role workers and the delivery loop

**Description:** Roles name the capability class their work needs. Constrained roles (gatherer, planner,
reviewer, spec-reviewer) run as role workers, because a native subagent inherits its parent's permissions.
The delivery loop is a set of runtime-neutral workflows. Plans open with a Review Card in the runtime's
native plan pane, and answers follow the voice the developer selected. This feature realizes UJ-5.

#### FR-40: Isolated role workers
Constrained roles must run as role workers with restricted tools, declared input roots, deadlines and
private logs. A planner returns content, and the harness validates it and publishes it to a new path.
**Status:** implemented (0.9); per-role skill loading is unreleased (#594).

**Consequences (testable):**
- The gatherer runs offline. Web questions go to in-session band workers, because a worker that can both
  read a workspace and fetch pages could exfiltrate it.
- A dead worker's status reads `orphaned`.
- A planner artifact that targets an existing file, a traversal path or a symlinked directory is rejected.

#### FR-41: Confinement however the spawn is named
A constrained role's work must be refused as a native spawn, whatever name the spawn carries. This includes
framework review layers. **Status:** partial: implemented (0.11.1, spawn guards); the session-level signal
is unreleased (#585). The Workflow tool's launch is guarded (#576, unreleased); a script's `agent()` calls
are still not band-routed.

**Consequences (testable):**
- While a routed review is in flight, an unnamed spawn carrying review work is refused.

#### FR-42: Delivery-loop workflows
The system must provide `/research`, `/plan`, `/build`, `/review`, `/land`, `/handoff` and `/close-out`.
Their text is runtime-neutral, so Codex projects them as skills. **Status:** implemented (0.9 to 0.12).

**Consequences (testable):**
- `/land` merges only after proving the merge, then removes the worktree and deletes the branch.
- `/build` opens a pull request whose body follows the repository template: what and why, where it lands,
  and a checklist that includes the test summary.

#### FR-43: Review Card plans in the native plan pane
`/plan` must enter the runtime's plan mode and write a Review Card, which the plan hook validates. Approval
comes through the runtime's native approval step. **Status:** unreleased (#590).

**Consequences (testable):**
- The plan hook rejects a card longer than 85 lines, a table, or a missing `## Decisions for the reviewer`
  heading.
- The 70-line target and the diagram limits (a `text` fence of at most 12 nodes and 80 columns; Mermaid
  only below the card's `---`) are authoring rules in the plan-authoring skill. The hook does not check
  them.

#### FR-44: A worktree per agent
The system must create, audit and remove task worktrees outside permanent repositories. Removal must be
safe after a squash merge. **Status:** partial: implemented (0.9), but a checkout that holds a submodule
cannot be removed; the fix is planned (backlog, #413).

**Consequences (testable):**
- `citizen worktree audit` lists each worktree with its repository identity, merged state and unique
  commits.
- `citizen worktree remove` refuses a tree with unique unmerged commits. It deletes a branch only once its
  pull request is merged at that tip.

#### FR-68: Readable answers and plans
Answers must follow the developer's `voice` stance. Plans must follow the plan-ceremony stance. Subagent
returns must be bounded. **Status:** implemented (0.8), with voice-conditional output styles in 0.12
(#494).

**Consequences (testable):**
- The installed output style follows the `voice` stance on every runtime. A developer's own output style is
  never deleted, even when its name matches a harness style.
- A brief states its return shape and word cap, and the return comes back as a verdict, findings and a
  path, not the full detail.
- Drafts written in the developer's name follow a private voice profile when the developer keeps one. The
  public rule only points at that profile.

### 4.9 Framework integrations

**Description:** An external framework, such as a planning method, a review pipeline or a viewer, emits
only intents. Policy binds at the harness's own hooks. Each integration is a descriptor, so a new framework
needs no bespoke CLI. BMad and the architecture viewer are the first two tenants, which keeps the contract
from being quietly shaped around either one.

#### FR-45: Declared integrations
An integration must be declared as a descriptor that states:
- the framework and its version pin;
- how its spawns are recognized;
- the role each spawn maps to;
- the input roots it may read.

**Status:** unreleased (#585, #591).

**Consequences (testable):**
- `citizen integration check <name>` reports renamed keys or layer ids after an upstream release.
- `citizen integration apply <name>` installs the overrides. `citizen bmad` remains an alias for one
  release. `[ASSUMPTION: removed in 0.14]`
- A framework's review layers are confined at the spawn hook by the descriptor's mapping, not by prompt
  text.

#### FR-46: Architecture-viewer integration
The system must expose an architecture-viewer capability with a builtin or custom selector and an external
adapter. **Status:** partial: implemented (0.10) as a preview. The mailbox adapter is gated on an upstream
seam (unscheduled).

**Consequences (testable):**
- Viewer paths stay under the project root, unless the developer pre-authorizes another root.
- No viewer is bundled while its renderer's licence falls outside the permissive-only policy.

### 4.10 Decision providers

**Description:** An optional decision provider judges narrow, typed questions at the harness's own decision
points. The deterministic checks stay authoritative. Each decision point moves through three stages:
- `shadow`: logged only.
- `advise`: shown.
- `act`: may only tighten a decision.

Every provider path fails open to the deterministic answer. This feature realizes UJ-9.

#### FR-47: Decision-provider contract
The system must offer the providers `none`, `local` and `jev` behind one contract of `decide`, `record` and
`learn`. An allowlist governs outbound data, and a kill switch disables the provider. **Status:**
- implemented (0.12, #486): the contract, with `none` and `local`.
- unreleased (#575, #592, #596, #597): the `jev` provider, stages, the kill switch, question packs, the
  eval runner and ledger rows.

`citizen decide` asks the configured provider about an action from the CLI. No hook consults a provider
yet.

**Consequences (testable):**
- With the kill switch set, no provider call is made, and every decision is the deterministic one.
- The provider is off by default. Enabling it sends only allowlisted fields.
- For a third-party provider whose terms bar training on its output, `learn` stores no provider output. It
  records only a count of learn events.

#### FR-48: Stages that only tighten
Each decision point must carry its own stage. At `act`, a provider may turn an allow into an ask, and
nothing else: it never denies and never widens. The developer's answer to that ask is the authorization,
so tightening is not authorization, and failing open stays coherent. On a runtime whose dispatcher cannot
answer `ask`, `act` is unsupported, and the point stays at `advise`: Codex turns an ask into a deny.
**Status:** unreleased (#592). The
`act` consumers are planned (backlog, #141, #372): the stop-claim check first, then the ask band.
Skill-shortlist ranking stays in `shadow` (#143).

**Consequences (testable):**
- No provider answer can relax a deny, turn an allow or an ask into a deny, or allow a grade 2 or 3
  command.
- A provider error or timeout yields the deterministic decision.

#### FR-49: Evidence gates for each stage
A decision point moves through its stages only by passing each stage's gate.
- **Into `shadow`:** the point is bound last in the decision chain, it meets its written latency bar, and
  one live request has been verified.
- **Into `advise`:** the point meets its written held-out criterion with a fitted threshold, as reported by
  `citizen decisions eval`. The report covers agreement, calibration (ECE), flip rate, cost per thousand
  decisions and p95 latency, all against versioned question packs.
- **Into `act`:** the point's false-alarm rate against sampled allows has also been measured.
- **No criterion:** a point without a criterion stays in `shadow`.

Thresholds are keyed to the pinned model id and the question-pack version. **Status:** unreleased (#596, the eval
runner and packs); per-point criteria are planned (v0.16.0). Hand-labelled fixtures (#377,
v0.16.0) are the critical path.

**Consequences (testable):**
- If the provider returns a model id other than the pinned one, the fitted threshold is void, and the point
  falls back to the deterministic decision.
- Every `ask` row records its cause. The completion claim can be switched on, so stop-claim checks have
  inputs.
- The static estimate shows zero added context tokens while a point runs in `shadow`.
- Evaluation figures that measure a third-party provider's model stay local unless its terms permit
  publication. Published figures cover the harness's own decision points, the `local` provider and label
  yields.

Evidence for this group: `../../research/technical-decision-layer-evidence-2026-09-23/`.

#### FR-50: Evidence-driven stance proposals
The system must compare each declared stance with its measured behaviour and report the drift. When the
evidence passes a variant's threshold, it must propose a promotion or a demotion. The developer applies a
proposal; the system never applies one itself. **Status:** planned (v0.17.0, #692; the #135 epic is in the
backlog).

**Consequences (testable):**
- For each stance, the drift report names the declared variant and the effective variant measured from the
  decision log.
- Given ledger rows past a variant's threshold, the report proposes the change and names those rows. No
  stored selection changes until the developer applies it.

### 4.11 Compatibility, qualification and release

**Description:** A target is supported only when it has native evidence for the exact source revision. A
release costs one qualification round: invalidation is scoped, and routine checks run without a model.
Every public surface names the same revision and status. This feature realizes UJ-7.

#### FR-6: Diagnostics
The CLI must distinguish published qualification from local installation, activation and drift. It must
also report runtime versions, logins, links, providers and hosts. **Status:** implemented (0.9).

**Consequences (testable):**
- `citizen doctor` names each problem it finds together with the command to run next, for example
  `drift: 1 item(s), run citizen diff`.
- On macOS, `citizen doctor` skips the runtime's own doctor when HOME has no keychain.

#### FR-7: Evidence-backed compatibility
Compatibility claims must reference versioned native evidence for an exact runtime, client surface,
operating system and source revision. **Status:** implemented (0.9).

**Consequences (testable):**
- The validator parses exactly the bytes it hashed.
- A passing record cannot hide a failure or a mismatched client version.
- An assessor at class `strong` or above records each observation, and a reviewer reads every observation
  before the record is accepted. (Unreleased, #598.)

#### FR-12: Release integrity
Every release and public support statement must identify the same immutable source revision, evidence and
compatibility status. Before 1.0, a release may ship without a qualified target only when its
compatibility status says so. **Status:** partial:
- integrity is implemented (0.9);
- 0.12.0 shipped with no qualified target;
- the minimum required target is implemented (0.13): both Claude Code CLI targets carry native evidence
  for 0.13.0 and 0.13.1 (#533), and the Codex CLI part is planned (v0.14.0, #612; v1.0.0, #686);
- an explicit waiver field in the compatibility catalog is planned (unscheduled).

**Consequences (testable):**
- `scripts/release_preflight.py` fails when the compatibility catalog, its evidence and `VERSION`
  disagree, when the checkout is dirty, or when projections have drifted. It also fails when GitHub About
  differs from `product.json` (if `gh` is authenticated).
- A minor release marks at least `claude-code-cli-macos` as required for release.
- Codex targets stay non-required until one scripted round passes with `--home-confirmed` and agrees with
  a hand-driven round on every case outcome.
- A runtime release that changes a behaviour the harness relies on triggers a patch to the compatibility
  catalog, under the external-runtime clause of `docs/compatibility-policy.md`.

Evidence: `../../research/technical-runtime-platform-limits-and-qualification-2026-09-23/`.

#### FR-51: Scoped evidence invalidation
A change must invalidate qualification evidence only where its paths reach. **Status:** unreleased (#583,
scoped per runtime; #582, scoped per case through a versioned case-to-path map, decided 2026-09-26).

**Consequences (testable):**
- A change outside the source paths leaves all evidence valid. This covers docs, tests, scripts and
  `product.json`.
- A change under one runtime's adapter directory invalidates that runtime's targets only, unless it
  touches a file shared code reads for every runtime.
- A change to shared source invalidates every target.
- Within a target, a change under a path the case-to-path map assigns invalidates only the cases that
  name it, and a change under a path no case names invalidates every case. A record that states no map
  version, or another one, keeps the whole-target rule.

#### FR-52: One qualification round per release
A release must be frozen on a release branch, and it must pass a model-free smoke tier before any native
case runs. Per-case evidence must be recorded as it completes. **Status:**
- implemented (0.12): the freeze and per-case progress (#396, #397);
- unreleased: the smoke tier (#581), and drivers for all eleven native cases (#336);
- unreleased: the round runner stops before any target runs when the tier fails or times out (#688).

**Consequences (testable):**
- A smoke-tier failure stops the round before any model turn is spent.
- No fix lands in the source paths on the release branch during a round. A change there invalidates that
  target.
- Each case's evidence is written when the case completes, and a resumed round skips completed cases.

#### FR-53: Release surfaces
A release must work its five release surfaces in order, and report each as done, skipped or unverified.
Releases are cut by milestone, and a regression fix releases at once as a patch. **Status:** implemented
(0.11). **Scope:** repository process.

**Consequences (testable):**
- `scripts/advance_stable.py --check` finds `stable` at the tag after publication.
- `scripts/sync_about.py --check` finds GitHub About equal to `product.json`.

#### FR-54: Landing copy as data
All landing, README and About copy must come from `product.json`. Published copy must follow the §1.2
positioning. **Status:** implemented (0.11); `landing-copy` check (0.12). **Scope:** repository process.

**Consequences (testable):**
- The `landing-copy` check fails a pull request that changes a path under `bin/`, `lib/`, `adapters/`,
  `primitives/` or `policy/` without either updating `product.json` or stating why no update is needed.
- No published copy says "cheaper" until a result supports SM-2's pre-registered hypothesis (FR-56).
- Published copy uses no em dashes. (Partial: the README still has some, and no test checks copy for
  them.)
- Public documents credit the projects they compare against rather than framing them as competition.

### 4.12 Cost benchmarks

**Description:** The harness measures its own value against a bare runtime, per version, and publishes
failures as failures. A static tier runs free on every pull request. A live tier runs per release, under the
isolation invariants.

#### FR-55: Static context budget in CI
CI must compute the static estimate on every pull request, and fail when it grows by more than 5% without
a recorded reason. **Status:** implemented (0.12).

**Consequences (testable):**
- `scripts/cost_bench.py static` reports the static estimate. The live prefix is reported separately, as
  the truer figure.

#### FR-56: Live replay against a bare arm
The system must replay the release task set in bare and harness arms, and report cost per passed task as a
same-day ratio, with the pass rate beside it. The 0.85 figure is the expected effect of a pre-registered
hypothesis tested under SM-2: the harness lowers Cost-of-Pass against bare, and does not lower the pass
rate by more than a fixed non-inferiority margin δ of 0.125, one task's share of the original eight-task
set. Every result is published with its interval, whatever it shows. **Status:** implemented (0.12,
the runner). No result supports the hypothesis yet. The one clean result, 1.052 on the earlier four-task
set, is history: a point ratio with no interval, so it is not a test of the hypothesis. The replay defects found on 2026-09-24 are planned for v0.14.0: the docs and the task manifest
disagree on the task count, the turn cap is never passed, the stop gate is inert in snapshots, the arms have
different web access, the usage-prices task needs rework, and hook events need `stream-json`.

**Superseded 2026-09-24:** "The publish bar is fixed in advance: at most 0.85 of bare, while passing at
least as many tasks as bare minus one."

**Consequences (testable):**
- Both arms run from a clone outside `$HOME`, with a scrubbed environment, strict MCP configuration, one
  explicit model and a per-run budget.
- Arms differ only by environment. Each arm's fence is proved before scoring, and no tagged sync touches a
  live profile.
- An errored run is recorded as an error, never as a failed pass.
- Contaminated sets stay in history, labelled as contaminated.
- Every row reports its counter-figures beside the ratio: pass rate, turns and tool calls, cache-miss
  ratio, errored runs, and unpriced rows.

Evidence: `../../research/technical-cost-context-and-benchmark-measurement-2026-09-23/`.

#### FR-57: Per-release tag replay
The system must replay an older release tag through a signed-in profile, then undo exactly what that sync
recorded. **Status:** unreleased (#609).

**Consequences (testable):**
- After the undo, every file that the tag's sync created is gone, and every file it merged is back to its
  prior bytes.
- The undo never touches credential-named files.
- A residue check runs before the next tag spends.

#### FR-58: Per-rule regression and the four-arm benchmark
The system must attribute cost to each loaded instruction source, including the developer's own rules. It
must also run bare, library, harness and combined arms from built profiles. **Status:** planned:
per-rule attribution and the two-arm path (v0.15.0: #514, #559, #560); four arms (v0.16.0: #559, #560,
#561).

**Consequences (testable):**
- A per-rule report lists each loaded instruction source with its token cost, including sources outside
  the harness.
- Arms are declared in `benchmarks/ablations.json`. Each metric is reported against control, with n and
  spread.

### 4.13 Session operations

**Description:** The harness keeps concurrent sessions safe on one machine and reachable from elsewhere.
Continuity moves work between runtimes without moving approvals. This feature realizes UJ-8.

#### FR-8: Task continuity
Work may move between supported runtimes through neutral task state that is not authoritative. Approvals
and verification claims never transfer. **Status:** implemented (0.9).

**Consequences (testable):**
- A changed shared plan invalidates prior verification, even when the plan file is git-ignored.

#### FR-59: Session lifecycle workflows
`/handoff` must write the next session's handoff and record durable learnings. `/close-out` must:
- compose `/land` and `/handoff`;
- message dependent sessions;
- archive the session only when asked.

**Status:** implemented (0.12).

**Consequences (testable):**
- `/close-out` stays within its 35-line body cap, and never clears or compacts before archiving.
- Where the runtime offers the agent no archive action, `/close-out` ends by asking the developer to
  archive.
- A cross-session message that the runtime holds is not lost. The session falls back to an issue comment
  or a handoff file.

#### FR-60: Remote Control hosts
On macOS, the system must:
- run one launchd host per workspace;
- keep each host on its environment across restarts;
- reconnect dropped sessions automatically.

**Status:** implemented (0.12: the supervisor and heal, #515). Environment reuse and automatic reconnect
are unreleased (#603).

**Consequences (testable):**
- A relaunched host logs that it is reusing its prior environment, and keeps its environment id.
- `citizen remote-control heal` runs every 60 seconds and reconnects a disconnected session on an
  environment the machine owns.
- A server-archived session is past recovery. It is left out of `status`, and it is never reported as
  healed.

#### FR-61: Workspaces
A developer must be able to define a multi-root workspace whose session history and memory follow the
workspace, whichever folder is first. The `.code-workspace` files in one configured folder must be the only
definition of which folders belong together, and a session must be launchable across a workspace's folders.
**Status:** implemented: the shared session store (0.12); the workspace map, `workspace list` and
`workspace open` (0.14, #935); member instructions supplied at session start (0.14, #936).

**Consequences (testable):**
- `citizen workspace create` points every folder's project key at one store, so every folder shows the same
  session history.
- A project key that already holds real history is left alone and reported, never overwritten.
- With `workspaces_dir` unset, no workspace command or hook attaches anything, and `workspace list` says how
  to set it and exits 1.
- The map is worked out from the files on every call and never stored. A folder in several workspaces
  resolves by launch facts, then an override, then single membership, then a unique first position; any
  other case attaches nothing and lists the candidates.
- `workspace list` shows each workspace's members in order with their instruction size or `missing`, how
  every shared folder resolves and by which rule, and each ignored override.
- `workspace open NAME` runs the runtime in the first existing member with every other existing member as
  `--add-dir` and `HARNESS_WORKSPACE=NAME` set; arguments after `--` come before the `--add-dir` flags.
- A session started in a member folder receives the other members' paths and instructions from its own
  SessionStart hook entry: inline when the block fits in 9,000 characters, otherwise as one bundle file it is
  told to read first.
- On Claude Code a member is left to native loading only when it is an `--add-dir` argument of the session,
  `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1` is set and it has a `CLAUDE.md`; on Codex every member
  is supplied.

#### FR-62: Disposable homes on macOS
Every runtime launched under a substituted HOME must get a throwaway keychain. If it cannot, the case fails
closed as unverified. **Status:** implemented (0.11).

**Consequences (testable):**
- No test or worker launch raises a system keychain dialog.

#### FR-63: Coordination for parallel agents
Parallel builders must declare the paths they will write, including new paths they plan to create. A
pre-write check must warn when an edit overlaps a live sibling's claim. Sessions must be archived across
runtimes for later search. **Status:** partial: path claims and the pre-write check are implemented
(0.14, #542); the session archive is planned (backlog, #543, after the #541 spike).

**Consequences (testable):**
- Every overlap lands in the decision log.
- Whether a repeated overlap is denied is a policy variant. It is chosen from decision-log counts of denials
  against abandoned turns.
- The archive is local and not authoritative. It stores neutral turn rows keyed to the harness session,
  searchable by full text.
- The archive stores message and tool-result text only when the developer explicitly opts in through
  `telemetry.sessions_bodies`, which is off by default. Stored text is redacted for secret patterns at
  write, and `retention_days` applies to it (#685).
- Every archive search is logged with a usefulness signal, so the #546 reopen criteria can be evaluated.
- On runtimes without a pre-write hook, the adapter's capabilities state the gap.

**Out of scope:**
- peer-to-peer agent chat;
- push-injected memory;
- semantic retrieval, until the #546 reopen criteria are met.

Evidence: `../../research/academic-lit-agent-coordination-and-memory-2026-09-23/`.

### 4.14 Public planning and traceability

**Description:** Planning lives in this repository, in BMad form, linked both ways to GitHub. The work item
on GitHub carries the summary, the discussion and the acceptance evidence. The story file carries the
design. Every operation keeps the corpus current, in the same pull request as the change it describes. This
feature realizes UJ-5.

#### FR-9: Traceable work items
Every managed work item must have an immutable BMad ID, a GitHub issue, and links in both directions before
implementation begins. **Status:** implemented (0.9); the worktree-aware `reserve` is unreleased (#441).
**Scope:** repository process.

**Consequences (testable):**
- The `issue-ownership` check fails a pull request whose delivery issue is unmapped, or is shared with
  another pull request.
- `reserve` refuses an ID that any reachable branch or worktree already holds.

#### FR-64: Story files as design records
Each work item's story file must carry the design for its kind. The sync tool must preserve that content.
**Status:** implemented (0.13, #620). **Scope:** repository process.

What each kind carries:
- **Story:** context, acceptance criteria, design, tasks and cited dev notes.
- **Bug:** reproduction and root cause.
- **Spike:** question, experiment, exit criterion and result.
- **Decision:** options and consequences.

**Consequences (testable):**
- `refresh` rewrites only the frontmatter and the managed issue block, and leaves the story body
  byte-identical.
- The `issue-ownership` check fails a pull request whose delivery story still holds template placeholders
  in its required sections.
- Acceptance criteria state outcomes, not a narrative of the work done.

#### FR-65: A current corpus
Every SDLC operation must route through its BMad skill. Every change must update the part of the corpus it
makes stale, in the same pull request:
- new work gets a work item and a story file;
- a changed requirement amends this PRD;
- a changed invariant amends the architecture spine;
- sprint status is derived, not hand-maintained.

**Status:** implemented (0.13: #621, #623). **Scope:** repository process.

**Consequences (testable):**
- `audit` reports zero drift between `issue-map.json` and `sprint-status.yaml`.
- The BMad governance text that every workflow loads states the routing map and the currency rule.
- Every required check runs on each pull request and in the merge queue. Each change under a governed root
  carries a changelog fragment (#610). A daily traceability audit reports drift between the issue map and
  live GitHub.

### 4.15 Unattended maintenance

**Description:** A burndown bot fixes owner-labelled bugs and files scanned vulnerabilities privately,
without a live session, on the subscriptions the project already holds. Its randomised attempts are a
stream of the v0.17.0 field experiment. The epic is #941.

#### FR-71: Unattended bug-fix lanes
The project must fix owner-labelled bug issues without a live session. A Claude lane and a Codex lane each
open one pull request per bug, to `main` only, under the bot's own GitHub App, which holds no ruleset
bypass, administration or workflow permission. The bot runs inside a hard budget. **Status:** planned
(v0.15.0, #941). **Scope:** repository process.

**Consequences (testable):**
- An issue labelled by anyone but the owner gets no action.
- A patch touching `.github/` or aimed at any base but `main` is refused.
- With the pause switch set or a cap reached no agent starts, and an unexpected stop defers the bug to the
  next run.

#### FR-72: Review through to merge
A bot pull request must be worked through CodeRabbit, three rounds at most, and merge only when every
condition an agent-landed pull request needs holds; a switch holds merges for the owner. **Status:**
planned (v0.15.0, #949). **Scope:** repository process.

**Consequences (testable):**
- A human comment or a fourth round stops the bot and labels the pull request for the owner.
- A skipped review or an empty thread-reply review never counts as a pass.

#### FR-73: Private vulnerability lane
Scheduled scans must file each finding as a draft repository security advisory with a proposed patch.
Nothing about an unfixed finding may appear in a public issue, pull request, run log, step summary or
artifact. **Status:** planned (v0.15.0, #951). **Scope:** repository process.

**Consequences (testable):**
- A seeded finding creates one draft advisory and no public trace.
- A repeated finding creates no second advisory.

#### FR-74: Randomised real-work stream
Every bug-lane attempt must be assigned before its first turn (its lane by a fixed share, and harness or
bare within the Claude lane) and recorded as assigned, so the v0.17.0 field experiment can analyse it by
intention to treat. Bot-authored work must never enter the benchmark task set, and a live benchmark run
must name the harness ref it measures. **Status:** planned (v0.15.0, #944, #947). **Scope:** repository
process.

**Consequences (testable):**
- The pre-registration merges before the first assignment.
- A deferred or crashed attempt appears in the attempt table as assigned.
- A bot-authored mined task fails validation.
- `cost_bench.py replay` without `--tag` exits non-zero.

## 5. Cross-cutting non-functional requirements

- **NFR-1 Preservation:**
  - Install, sync, drift repair, upgrade and uninstall preserve unrelated settings and user edits.
  - Ambiguous conflicts fail visibly.
- **NFR-2 Reproducibility:**
  - Generation and tests run on Python 3.9 and on the newest Python the release lifecycle runs cover.
  - Code uses no syntax newer than 3.9.
  - CI runs the runner's system Python only. The 3.9 floor is proved locally and by the release lifecycle
    runs. A 3.9 check on every pull request is a gap.
  - **Default taken: amendment 2026-09-25 (#640).** Added after the original run; the lines above are
    unchanged and dated 2026-09-23. The required `test` check runs `tests/test_python_floor.py` under a real
    Python 3.9, parsing every tracked Python source, so #640 closes the gap for syntax on every pull
    request. The new `test-py39` CI job runs the suite under Python 3.9 on every pull request and in the
    merge queue, and it blocks a merge only once the owner adds it to the branch ruleset's required checks.
- **NFR-3 Security:**
  - Secrets and private paths never enter tracked code, evidence or planning artifacts.
  - No stance weakens authorization.
  - Tool output and fetched content are data.
  - The isolated gatherer never has network access.
- **NFR-4 Portability:**
  - Stable support covers macOS and Linux.
  - Native Windows is unsupported. WSL2 is unqualified until evidence says otherwise.
- **NFR-5 Auditability:**
  - A support or release claim traces to exact evidence bytes, and cannot hide a contradictory active
    failure.
  - Contaminated measurements stay recorded and labelled.
- **NFR-6 Reversibility:**
  - Configuration, ownership-journal, issue-map and story-file writes each go through a temporary file and
    an atomic replace, keeping the file's mode.
  - A batch operation, such as `upgrade` or sync across runtimes, restores what it already wrote when a
    later write fails.
  - Repeated application is safe.
- **NFR-7 Maintainability:**
  - Generated projections are reproducible outputs, not parallel sources.
  - A decision is explained once, at its authority, and every other place points to it.
- **NFR-8 Accessibility:**
  - Terminal output and documentation carry every meaning in text, never in colour alone.
  - They use literal status words.
- **NFR-9 Standing-context budget:**
  - Always-loaded context stays within 200 lines and 4,202 tokens. A rule whose action a hook can gate is a
    candidate to defer behind that hook.
  - `[NOTE FOR PM]` The token cap binds, and the line cap is the secondary guard: about 3,858 of 4,202
    tokens and 199 of 200 lines are in use. The next always-loaded rule therefore needs a deferral or a
    trade. The #430 deferral spike decides which rules can move behind their hooks. Raising a cap needs a
    recorded reason and a static-estimate measurement.
- **NFR-10 Concurrency safety:**
  - Many sessions and worktrees operate on one machine without corrupting shared state. Tests prove each
    of:
    - two reservations in parallel worktrees never take the same ID;
    - untracked scratch is never linted as tracked content;
    - two syncs never interleave writes, because they hold the `sync.lock`.
  - Per-session counters are the target, and the stop gate's counter still has the #611 gap.
- **NFR-11 Licensing:**
  - Shipped and bundled material permits commercial use, modification and closed-source redistribution.
  - Copyleft, source-available and unlicensed components stay user-installed, never bundled.
- **NFR-12 Local-first data:**
  - The usage ledger, decision log and evidence stay on the machine unless the developer configures export.
  - Only redacted figures reach git.
- **NFR-13 Claims discipline:**
  - Every public claim is labelled implemented, validated, proposed, historical or unknown, following the
    mapping in §0.
  - No copy asserts an unmeasured saving or a first-in-field claim that the field scan has not checked.
- **NFR-14 Release cost:**
  - A release costs one qualification round.
  - The baseline to beat is about 60 minutes and about 1M orchestrator tokens per round.
  - Each round records its actual figures.
- **NFR-15 Experiment discipline:**
  - Every spike and mechanism change carries a numeric exit criterion, written before the run.
  - The criterion is never adjusted after the result.
  - Every published result meets the evidence standard (planned 0.14, #794), which
    `docs/evidence-standard.md` states with what satisfies each item. It carries twelve items:
    1. a pre-registered plan (hypotheses, primary metric, guardrails, sample size, stopping rule and
       multiplicity), committed before the first trial from `docs/pre-registration-template.md`;
    2. a frozen task set with a reference solution per task, audited for task and outcome validity;
    3. the pinned model, CLI, effort, date, container digest, seeds and fallback rate;
    4. a dated price table;
    5. paired per-task results with clustered or Wilson intervals, and the intra-cluster correlation;
    6. a cost-effectiveness view, such as a Pareto frontier;
    7. trajectories and a command that reproduces the run;
    8. judge agreement: κ, a confusion matrix and bias audits;
    9. a contamination check;
    10. estimand labels: intention to treat, adherence, complier effects, and hypotheticals marked as
        such;
    11. field checks: sample ratio, novelty, CUPED and dilution;
    12. a "what we do not claim" section.

    Where SM-2 defines a term the standard uses, SM-2 governs.
- **NFR-16 Hook overhead:**
  - Each hook's p95 wall time per call is measured and reported with the static estimate.
  - No hook exceeds 250 ms p95 on the reference machines. `[ASSUMPTION: bound to be confirmed by the first
    measurement]`

## 6. Constraints and guardrails

- **Safety:**
  - Invariants sit outside every switch.
  - A decision provider may only tighten.
  - Core hooks can be switched off only with an explicit acknowledgement (planned 0.14, #552).
  - No stance changes without the developer applying it.
- **Privacy:**
  - Native telemetry export can carry account identity fields, and the docs say so before a developer points
    it at a hosted backend.
  - Decision rows may hold command text. Their `input` is capped at 2 KiB, and the decision log is not
    part of export.
- **Cost of measurement:**
  - Routine upkeep costs pennies a day.
  - An automated benchmark costs at most a dollar or two per release in API terms.
  - The live set runs under a subscription plan, capped per run and per set.
- **Cost to users:**
  - Budgets are soft, and nothing is denied for cost.
  - Every dollar figure is a list-price equivalent.
- **Claims and copy:**
  - Published copy follows §1.2 and is written in a human voice, with no em dashes.
  - It quotes a measured figure or no figure.
  - It credits the field instead of framing it as competition.

## 7. Public surface, versioning and dependencies

- **Public surface:**
  - the `harness` CLI subcommands and their flags;
  - the configuration schema (`config.json` and its `telemetry` setting);
  - primitive file formats;
  - the stance dimension format;
  - declarative detector files;
  - integration descriptors;
  - the ledger schema, including exported attribute names;
  - `product.json`.
- **Versioning:**
  - Semantic versioning, with minor releases continuing before 1.0.
  - From 1.0, `docs/compatibility-policy.md` binds every change to the public surface, covering
    compatibility, deprecation, migration and rollback (FR-66).
  - An alias for a renamed command survives at least one release.
- **Dependencies:**
  - Standard library first.
  - Vendored wheels only for components the project owns or has cleared under the licensing policy.
  - Python 3.9 is the floor.
  - No new runtime dependency without a licensing review.

## 8. Non-goals

- **Not a model router, gateway or serving layer.** It never selects a provider's endpoint or serves a model.
  Mapping a capability class to a model the runtime already offers is configuration, not routing.
- **Not an agent runtime.** It does not replace Claude Code or Codex.
- **Not hosted.** No feature requires a remote service.
- **Never caps spend.** Budgets inform, and nothing is denied for cost.
  *Amended 2026-09-24:* this stays the default. An opt-in cap module may deny for cost when the user
  switches it on; see the [§1 amendment](#amendment-2026-09-24-a-layered-configurable-measurable-harness).
- **Never relaxes a decision.** No decision provider relaxes a decision or denies one. Semantic
  auto-authorization is out of scope.
- **No distillation.** The harness never trains a model on a third-party provider's output. Any local model
  trains only on the harness's own labels.
- **No automatic stance changes.** Evidence proposes, and the developer applies.
- **No vendor-specific telemetry plugin.** The OTLP collector is the adapter.
- **No bundled backend.** ClickStack is a reference recipe only.
- **No peer chat between agents.** Coordination runs through shared, recorded state.
- **No push-injected memory,** and no merging with a runtime's native memory. Recall is pull-only.
- **No semantic retrieval over sessions** until full-text search demonstrably misses (#546).
- **No decorative MCP endpoint.** The harness is not an MCP server.
- **No trimming of a layered library.** The harness switches its own units and cedes whole areas.
- **No runtimes added for breadth alone.** A runtime is added only at the same depth as the existing ones.

## 9. Release scope and roadmap

### 9.1 The v1.0.0 stable contract

- **Supported targets:** Claude Code CLI and Codex CLI.
  - macOS on Apple silicon, with zsh.
  - Ubuntu 24.04 on x86_64, with bash.
  - Python 3.9 and the newest stable Python.
- **Candidate:** one frozen commit, with an archive digest, pinned client versions and a lifecycle proof
  (FR-66).
- **Policy:**
  - a published compatibility, deprecation and migration policy;
  - an independent audit;
  - one publication of the audited bytes.
  - These are tracked under #206, through #207, #209, #210 and #211.
- **Automation:** provider-backed end-to-end tests on inexpensive models (#134).
- **Evaluation gate:** a candidate ships only with a verified proof bundle. See the evaluation gate in the
  2026-09-24 amendment at the end of this section.
- `[NOTE FOR PM]` Codex CLI's place in the 1.0 contract is conditional. Its native cases now have drivers
  (#336), but one scripted round must still agree with a hand-driven round (FR-12).

### 9.2 Milestones before 1.0

The v0.14.0 to v0.16.0 entries below are superseded by the 2026-09-24 amendment at the end of this section.
They are kept as the plan of record at 2026-09-23.

- **v0.12.1:** restore the qualified release floor (#533), plus planning hygiene and small fixes. Closed on
  2026-09-24 without a release, superseded by 0.13.0 and 0.13.1.
- **v0.13.0:** the work merged since the 0.12.0 tag.
  - the decision-provider foundation: the `jev` provider, stages, packs and eval;
  - release-cost reductions: the smoke tier, per-runtime invalidation and drivers for every case;
  - integration descriptors and review confinement;
  - the plan-mode review surface;
  - Remote Control environment reuse;
  - tag replay;
  - the detector corpus in CI.

  The adoption work first planned for 0.13 already shipped in the 0.12.0 tag. That covers measured rules
  as the front door, `ruleprobe`, import, the installer, the plugin listing and the field scan. The 0.12.0
  release notes omit it.
- **v0.14.0, the selection model:**
  - the switch document;
  - modes and the `superpowers` mode;
  - the four-arm benchmark;
  - per-rule cost attribution;
  - Workflow-tool guards;
  - rich story files and a current BMad corpus (#616).
- **v0.15.0, close the loop:**
  - decision-provider consumers;
  - stance drift and proposals;
  - the `none` capability class;
  - cache-leverage measurement;
  - the session archive and write-intent claims;
  - a published live cost result, pass or fail.
- **v0.16.0, credibility breadth:**
  - record and replay beneath the live benchmark;
  - session recall;
  - measured workflow-script arms;
  - additional runtimes, each at equal depth.

### 9.3 Out of scope for 1.0

- stable editor and desktop client support (#216);
- Cursor, Gemini, Grok and other runtimes as stable targets;
- a team or organisation surface;
- a bundled architecture viewer;
- hosted execution.

### Amendment 2026-09-24: the measurable-harness roadmap

Added after the original run. It supersedes the v0.14.0 to v0.16.0 entries in §9.2, which stay above as the
plan of record at 2026-09-23. The trigger and the impact analysis are in the
[sprint change proposal](../../sprint-change-proposal-2026-09-24.md).

**The MVP claim.** Every harness module is switchable, attributable and measured. A defensible proof set
shows the harness's effect on instruction adherence, output effectiveness and cost against bare Claude
Code.

**Milestones.** Work is ordered by what the claim cannot ship without: the control plane, then
observation, then evaluation, then proof. Each entry names what the milestone newly delivers.
- **v0.14.0, Switchable:**
  - one selection document drives every module kind (#552, #554 to #557), and every module declares a
    manifest;
  - every ledger row carries a profile fingerprint and per-module attribution (#482);
  - the bare arm is observed like the harness arm, with nothing added to model context;
  - the replay defects found on 2026-09-24 are fixed;
  - an evidence standard and a pre-registration template (#794, NFR-15).
- **v0.15.0, Measured:**
  - evaluation tiers (#510, #511, #512);
  - harness against bare at five or more trials, with confidence intervals (#559, #560);
  - the two-by-two unit design (#754) and per-rule attribution (#514);
  - a scorecard, and soft estimates labelled as such;
  - delegation fixed or disproved (#429, #513);
  - the burndown bot (FR-71 to FR-74, #941), whose randomised stream feeds v0.17.0's field experiment;
  - proof set 1;
  - the coexistence spike with a methodology library (#553).
- **The MVP line falls after v0.15.0.** The claim ships when `harness evidence verify` passes on proof set 1,
  whatever it shows, with its "what we do not claim" section.
- **v0.16.0, Composable:**
  - the slot model and the adapter contract;
  - the `superpowers` mode (#558);
  - four arms and layer swaps (#559, #560, #561, and #562's four-arm part);
  - a compliance judge calibrated on hand labels (#140, #377);
  - a measured context trim (#430).
- **v0.17.0, Real work:**
  - randomised real sessions, with intention-to-treat effects;
  - Codex evaluation parity (#292, #293);
  - an external task set, after a licensing review;
  - factorial screening;
  - context-lifecycle treatments (#123, #745) and cache-leverage measurement (#524);
  - stance drift and proposals (#692);
  - the adoption cohort (#212, #213, #214).
- **v1.0.0, Stable:** the §9.1 contract, the audit and the publication (#206, #207, #209, #210, #211),
  plus the evaluation gate below.
- **Backlog:** decision-provider consumers (#135), policy preferences, the session archive and
  write-intent claims (#541, #542, #543), and editor and desktop clients (#216, #217).

**The evaluation gate in the 1.0 contract.** A candidate ships only with a verified proof bundle:
- `harness evidence verify` re-derives the published proof set;
- every module in the selection document has a scorecard row, measured or marked unmeasured.

## 10. Success metrics

**Primary**
- **SM-1 Sixty-second instrument:**
  - At least four of five tester-cohort members (#214) see which of their rules fired within a minute of
    invoking `ruleprobe`, timed by observation in the cohort session.
  - They run it with `--rules` on their own rule directory and on up to 30 days of their own transcripts.
  - The report states how many of their rules are measured, dark and unmeasured.
  - Validates FR-19, FR-20, FR-21 and FR-67.
- **SM-2 Measured cost claim:**
  - **Target:** Cost-of-Pass and pass rate, harness against bare, on the release task set.
    - Each arm's Cost-of-Pass pools the set: the total cost of every attempt divided by the total number
      of passes. The ratio divides the harness figure by bare's. If either arm passes nothing, the ratio
      is undefined and the result is reported as a pass-rate result only.
    - The paired, task-clustered 95% intervals on the Cost-of-Pass ratio and on the pass-rate difference
      govern the comparison. They come from a task-clustered paired bootstrap, which resamples tasks and
      keeps both arms' trials of a task together, or from the delta method.
    - Wilson or Bayesian intervals describe each arm's own pass rate. They are descriptive only, as are
      the per-task figures reported beside the headline.
  - **Pre-registered hypothesis:** the harness lowers Cost-of-Pass against bare, with an expected ratio of
    0.85, and does not lower the pass rate by more than the non-inferiority margin δ. δ is fixed at 0.125,
    one task's share of the original eight-task set, and does not shrink as the set grows.
  - **Power:** five or more trials per task and arm, and α 0.05 two-sided. The task set and the trial
    count are sized for a joint power of 0.8 on both tests of the decision rule, assuming a true ratio of
    0.85 and equal pass rates. The minimum detectable effect is stated before the run and is at most 15%.
    The set includes long multi-turn tasks.
  - **Decision rule:** the hypothesis is supported only when both conditions hold:
    - the paired, task-clustered 95% interval on the Cost-of-Pass ratio lies wholly below 1.0;
    - pass-rate non-inferiority holds: the lower bound of the paired, task-clustered 95% interval on the
      pass-rate difference (harness minus bare) is above −δ.

    The result is published with its intervals, whatever it shows. A magnitude is claimed only as far as
    the interval supports it: "at least 15% cheaper" needs the interval's upper bound at or below 0.85.
  - **Sources:**
    - Cost-of-Pass: Erol et al., ICLR 2026, https://arxiv.org/pdf/2504.13359.
    - Clustered and paired standard errors, and the power formula: Miller, "Adding Error Bars to Evals",
      https://arxiv.org/html/2411.00640.
    - The delta method for a clustered ratio: Deng, Knoblich and Lu, KDD 2018,
      https://arxiv.org/pdf/1803.06336.
    - Small-n pass-rate intervals: Bowyer et al., ICML 2025, https://arxiv.org/pdf/2503.01747.
  - **Current:** 1.052 on the earlier four-task set, measured before the hypothesis was registered. It was
    a point ratio with no interval, so it is history, not a test of the hypothesis. No claim is made. The
    eight-task release task set has no clean result yet. The 23 Sep eight-task runs were unscored and ran
    with unequal web access between the arms, so they are not a result.
  - **Where the saving must come from:** turns and thrash, not context. The standing prefix is about 20%
    of a run's cost, and trimming can remove about a quarter of it, so about 5% of the run. The earlier
    1.052 point ratio would have needed a cut of about 19% to reach 0.85.
  - **Stop condition (decided):** a saving claim needs the hypothesis supported on the whole set, and a win
    on the pre-registered long-task subset. That win means the subset's ratio interval lies wholly below
    1.0. If the long-task subset shows no win on clean snapshots, the instrument findings are published as
    the result and the saving claim is retired, whatever the whole-set result. §4.6 then stands on routing
    control and legibility, not on savings.
  - **Superseded 2026-09-24:** the target read "on the release task set, a same-day ratio of 0.85 or less,
    with the pass rate at least bare minus one". The power line read "a claim is published only from three
    or more reps, with a stated minimum detectable effect and the per-task figures beside the headline".
    The stop condition read "if the release task set on clean snapshots shows no harness win on long
    tasks, the instrument findings are published as the result and the saving claim is retired". The
    amended text keeps that trigger and defines a win as an interval lying wholly below 1.0.
  - Validates FR-28 to FR-34 and FR-56.
- **SM-3 Standing prefix:**
  - The static estimate holds or falls release over release. Growth over 5% carries a recorded reason.
  - The lint caps hold.
  - The live prefix is reported beside it.
  - Context gets a cap, not a savings target.
  - Validates FR-55 and NFR-9.
- **SM-4 Mechanism fired:**
  - Measures spawns per run on tasks above the break-even.
  - No saving is credited to a mechanism unless the ledger rows show it fired.
  - Validates FR-34.
- **SM-5 Lifecycle integrity:**
  - Every supported target passes install, upgrade, repeat sync, rollback and uninstall with zero loss of
    unrelated files.
  - Validates FR-4, FR-5, FR-66 and NFR-1.
- **SM-6 One authored change, two runtimes:**
  - On every qualified target, one authored stance change reaches both runtimes with zero hand edits.
  - Every capability row carries a mode and a qualification state.
  - Validates FR-1, FR-2 and FR-3.

**Secondary**
- **SM-7 Release cost:**
  - A release costs one qualification round: at most one freeze, one smoke pass and one native pass per
    required target.
  - Validates FR-51 to FR-53 and NFR-14.
- **SM-8 Retained adoption:**
  - Before any launch post: ten installs, five retained users and three actionable reports from the direct
    tester cohort.
  - "Retained" means used again in a second week, as reported by the cohort, since nothing phones home.
  - Validates FR-17, FR-21 and the adoption program (#212).
- **SM-9 Rules pruned:**
  - At least three of five cohort members report changing or deleting one or more rules within seven days
    of their first report. This is self-reported, since nothing records rule edits.
  - Validates FR-20 and FR-21.
- **SM-10 Evidence-driven proposals:**
  - At least one stance variant is proposed from ledger evidence and applied by its developer.
  - Validates FR-50.
- **SM-11 Corpus currency:**
  - Every merged pull request's delivery story passes the depth check.
  - The BMad audit reports zero drift.
  - Validates FR-64 and FR-65.

**Counter-metrics (do not optimize)**
- **SM-C1 Stars and clone counts:**
  - Clone statistics are unreliable: 226 "unique cloners" against 244 CI runs in the same window.
  - Stars reward attention, not use.
  - Counterbalances SM-8.
- **SM-C2 Raw token reduction:**
  - In a published study, cutting tool-output tokens by 38.4% raised billed cost by 6.8%, because it broke
    the cached prefix.
  - Optimize billed cost per passed task instead.
  - Counterbalances SM-2 and SM-3.
- **SM-C3 Rule hit rate as a target:**
  - A rule that fires often is not a better rule. Hit rate informs pruning.
  - Counterbalances SM-1 and SM-9.
- **SM-C4 Spawn count:**
  - Delegation that does not pay back its prefix is waste.
  - Counterbalances SM-4.
- **SM-C5 Runtime count:**
  - Each shallow runtime dilutes the depth claim.
  - Counterbalances the v0.17.0 Codex parity work and any later runtime work, per the §9 amendment.

## 11. Open questions

Every question is owned by the maintainer. Each one names what it blocks and the milestone that needs its
answer.

1. **Per-case or per-target invalidation?** Should qualification evidence be invalidated per case, or only
   per target (#582)?
   - Answered 2026-09-26: per case, through a versioned case-to-path map in the catalog; a change under
     an unmapped path invalidates every case (#582).
2. **Keeping the issue map current.** How does the issue map's lifecycle stay current without a pull request
   after every merge (#420)? Does the derived sprint status (FR-65) change the answer?
   - Blocks: the issue map's lifecycle field (#420). FR-65's derived sprint status (0.13) does not settle
     it.
   - Needed by: backlog (#420).
3. **The unattended sub-issue delete.** Command grading refuses a sub-issue delete that the sync tool's
   `apply` performs unattended (#421). Which side changes?
   - Blocks: FR-9 and FR-35.
   - Needed by: backlog (#421).
4. **Selections a native client cannot read.** How do project and session selections reach a native client
   that reads only user-level configuration (#276)?
   - Blocks: FR-2.
   - Needed by: v0.14.0.
   - *Resolved 2026-09-26:* the owner chose session-start injection of the resolved stance text when it
     differs from the synced one, bounded by the always-loaded budget (FR-2). #294 settled that sync keeps
     projecting user-level selections only.
5. **How modes take effect after `init`.** Should `harness init` write only keys that differ from the
   defaults, or should a mode overlay the base user configuration (#557)?
   - Blocks: FR-16.
   - Needed by: v0.14.0.
6. **Floor or override for core hooks.** Should a project-scope selection be a floor that a session can
   only tighten for the core hook ids, or a plain override as today?
   - Blocks: FR-15 and FR-16.
   - Needed by: v0.14.0.
7. **Codex's `ultra` effort level.** How does it map under the delegation stance's `high` ceiling? Should
   built-in runtime agents fall under the cost posture?
   - Blocks: FR-28 and FR-29.
   - Needed by: v0.17.0, with Codex parity.
8. **Decision-log default for adopters.** Should the decision log default to on for new adopters, given
   that rows can hold command text?
   - Blocks: FR-27.
   - Needed by: v0.17.0, with the adoption cohort (#212).
9. **Codex hook events.** Which hook events does the current Codex client raise? The recorded evidence
   (Codex 0.154 to 0.155) and the current documentation disagree about:
   - `UserPromptSubmit`;
   - `SubagentStart` and `SubagentStop`;
   - `allow` with an input rewrite.

   One probe settles it.
   - Blocks: FR-32, FR-37 and Codex parity in FR-3.
   - Needed by: v0.17.0, with Codex parity.
10. **Qualifying one capability.** Is capability-level qualification in scope before 1.0? Today no
    capability can be qualified on its own.
    - Blocks: FR-7.
    - Needed by: v1.0.0.
11. **Release duration.** Can a round fall from about an hour to minutes, and what is the target?
    - Blocks: NFR-14.
    - Needed by: v0.14.0.
12. **A runtime's own memory.** What is the harness's position on a runtime's own memory store next to its
    handoff rules?
    - Blocks: FR-8 and the §8 memory non-goal.
    - Needed by: backlog, with the session archive (#541, #543).
13. **Live instruction changes.** A merged rule reaches running Codex sessions mid-task and rewrites their
    instruction prefix. Should merges be batched, or should the effect be documented only?
    - Blocks: NFR-9.
    - Needed by: v0.14.0.
14. **A web mode for the gatherer.** Should the isolated gatherer get an opt-in web mode, given the
    exfiltration boundary?
    - Blocks: FR-40.
    - Needed by: unscheduled: the 2026-09-24 roadmap does not place it.

## 12. Assumptions index

- §4.4 FR-67: detector proposal from rule prose is unscheduled: the 2026-09-24 roadmap does not place it.
- §4.5 FR-25: schema stability for exported rows before 1.0 is best-effort.
- §4.6 FR-32: the nudge's starting thresholds are recalibrated from ledger data before 0.15.
- §4.9 FR-45: the `harness bmad` alias is removed in 0.14.
- §5 NFR-16: 250 ms p95 per hook is a starting bound, to be confirmed by the first measurement.

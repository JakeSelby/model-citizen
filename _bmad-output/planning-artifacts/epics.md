---
title: Agent Harness epics
status: active
created: 2026-09-19
updated: 2026-09-28
inputDocuments:
  - prds/prd-agent-harness-2026-09-23/prd.md
  - architecture-spines/architecture-agent-harness-2026-09-23/ARCHITECTURE-SPINE.md
  - ux-designs/ux-agent-harness-2026-09-23/DESIGN.md
  - ux-designs/ux-agent-harness-2026-09-23/EXPERIENCE.md
---

# Agent Harness epic map

## Current delivery order

[The September 28 roadmap amendment](roadmap-2026-09-28.md) governs current assignments: Measured 0.15, Studio 0.16 after Measured lands, Composable 0.17, Real work 0.18. Later engine-dependent Studio children do not gate the initial Studio release. Issue milestone fields and dated delivery amendments carry each story's assignment.

## Overview

This map breaks the 2026-09-23 PRD into epics. Each epic is a GitHub issue with a BMad ID, and each
story is a GitHub issue whose story file under `../implementation-artifacts/` carries its design. The
map replaces the 2026-09-19 program list, and its history is in git. It is kept in step with
`../issue-map.json`: a change that moves the tree updates this file in the same pull request.

How to read it:
- **Epics** carry their milestone window, their goal and the requirements they cover.
- **Stories** are listed under their GitHub parent, with their state: open, done, or not planned (closed
  without delivery). A story's own child issues are nested under it.
- **Completed work with no epic** is listed by issue range at the end. It is history, and it keeps its
  record in its story file.

## Requirement coverage

| Requirement group (PRD §4) | Epics |
| --- | --- |
| Shared primitives and projection (FR-1, FR-3, FR-10, FR-13) | AH-E001, AH-E008 |
| Stances and selection (FR-2, FR-14 to FR-16) | AH-E002, AH-E011, AH-E017 |
| Configuration lifecycle (FR-4, FR-5, FR-17, FR-18, FR-66, FR-69, FR-70) | AH-E001, AH-E005, AH-E008, AH-E015, AH-E020 |
| Measured rules (FR-19 to FR-22, FR-67) | AH-E009, AH-E013, AH-E003 |
| Ledger, pricing and telemetry (FR-11, FR-23 to FR-27) | delivered without an epic (0.12); AH-E017 for the schema rule; AH-E018 for rebuild attribution |
| Cost posture and delegation (FR-28 to FR-34) | delivered without an epic (0.11); AH-E013, AH-E015, AH-E018 |
| Guardrails (FR-35 to FR-39) | AH-E015 |
| Role workers and the delivery loop (FR-40 to FR-44, FR-68) | AH-E001, AH-E015 |
| Framework integrations (FR-45, FR-46) | delivered without an epic (#585, #591); viewer under AH-E002 |
| Decision providers (FR-47 to FR-50) | AH-E003 |
| Compatibility, qualification and release (FR-6, FR-7, FR-12, FR-51 to FR-54) | AH-E005, AH-E014, AH-E006, AH-E020 |
| Cost benchmarks (FR-55 to FR-58) | AH-E013, AH-E011 |
| Session operations (FR-8, FR-59 to FR-63) | AH-E001, AH-E002, AH-E015, AH-E017, AH-E018 |
| Public planning (FR-9, FR-64, FR-65) | AH-E004, AH-E012, AH-E016 |
| Unattended maintenance (FR-71 to FR-74) | AH-E021 |
| Studio (FR-75 to FR-84) | AH-E022, AH-E023, AH-E024, AH-E025, AH-E026 |

## Epics

### AH-E001: Deliver a user-aligned, provider-agnostic harness built on shared primitives ([#93](https://github.com/JakeSelby/agent-harness/issues/93), done)

- **Milestones:** 0.9 (completed)
- **Goal:** One shared primitive catalog, native adapters for Claude Code and Codex, reversible ownership, task continuation and native evidence.
- **Covers:** FR-1, FR-3 to FR-8, FR-10 to FR-12
- **Stories:**
  - AH-S027 [#94](https://github.com/JakeSelby/agent-harness/issues/94): Unify primitive contracts, custom stances and runtime projections (done)
    - AH-S067 [#163](https://github.com/JakeSelby/agent-harness/issues/163): Unify policy sources and personal stance switches (done)
    - AH-S072 [#168](https://github.com/JakeSelby/agent-harness/issues/168): Keep session choices out of global projections (done)
    - AH-S075 [#171](https://github.com/JakeSelby/agent-harness/issues/171): Align plan storage and qualify native guidance (done)
  - AH-S028 [#95](https://github.com/JakeSelby/agent-harness/issues/95): Repair installation, configuration ownership and reversible migration (done)
    - AH-S068 [#164](https://github.com/JakeSelby/agent-harness/issues/164): Reconcile both runtimes with reversible ownership (done)
    - AH-S074 [#170](https://github.com/JakeSelby/agent-harness/issues/170): Preserve recovery intent and redirected user links (done)
  - AH-S029 [#96](https://github.com/JakeSelby/agent-harness/issues/96): Deliver runtime policy enforcement, roles, gates and observability (done)
    - AH-S069 [#165](https://github.com/JakeSelby/agent-harness/issues/165): Compose shared lifecycle policies for both adapters (done)
    - AH-S077 [#173](https://github.com/JakeSelby/agent-harness/issues/173): Inspect hooks for both managed runtimes (done)
    - AH-S081 [#177](https://github.com/JakeSelby/agent-harness/issues/177): Isolate constrained shared role workers (done)
    - AH-B006 [#181](https://github.com/JakeSelby/agent-harness/issues/181): Preserve plan feedback for native Codex multi-file patch results (done)
  - AH-S030 [#97](https://github.com/JakeSelby/agent-harness/issues/97): Integrate BMad and preserve task context across Claude and Codex (done)
    - AH-S070 [#166](https://github.com/JakeSelby/agent-harness/issues/166): Share task continuation and semantic BMad roles (done)
    - AH-S078 [#174](https://github.com/JakeSelby/agent-harness/issues/174): Detect missing review dependencies and divergent mirrors (done)
    - AH-S079 [#175](https://github.com/JakeSelby/agent-harness/issues/175): Invalidate handoffs when shared artifacts change (done)
    - AH-S080 [#176](https://github.com/JakeSelby/agent-harness/issues/176): Use shared authority and label runtime coverage (done)
    - AH-S083 [#179](https://github.com/JakeSelby/agent-harness/issues/179): Clarify explicit installation and shim recovery (done)
    - AH-S084 [#180](https://github.com/JakeSelby/agent-harness/issues/180): Honor workflow findings contracts (done)
  - AH-S031 [#98](https://github.com/JakeSelby/agent-harness/issues/98): Qualify native clients and publish a versioned compatibility catalog (done)
    - AH-S071 [#167](https://github.com/JakeSelby/agent-harness/issues/167): Require native evidence for support claims (done)
    - AH-S082 [#178](https://github.com/JakeSelby/agent-harness/issues/178): Reject contradictory qualification evidence (done)
    - AH-S086 [#185](https://github.com/JakeSelby/agent-harness/issues/185): Qualify native Codex CLI on macOS and Linux for 0.9.0 (done)
    - AH-S087 [#187](https://github.com/JakeSelby/agent-harness/issues/187): Qualify Claude Code clients for 0.9.0 (done)
    - AH-S088 [#188](https://github.com/JakeSelby/agent-harness/issues/188): Qualify Codex VS Code and desktop for 0.9.0 (done)
    - AH-B007 [#200](https://github.com/JakeSelby/agent-harness/issues/200): Preserve released qualification while blocking changed source (done)
  - AH-S032 [#99](https://github.com/JakeSelby/agent-harness/issues/99): Reposition product messaging around personal stances and shared primitives (done)
    - AH-S073 [#169](https://github.com/JakeSelby/agent-harness/issues/169): Lead with user-aligned primitives and personal stances (done)
    - AH-S085 [#183](https://github.com/JakeSelby/agent-harness/issues/183): Improve root README for first-time developer adoption (done)
  - AH-S033 [#100](https://github.com/JakeSelby/agent-harness/issues/100): Coordinate the verified release, metadata and website deployments (done)
    - AH-S076 [#172](https://github.com/JakeSelby/agent-harness/issues/172): Gate coordinated publication on native qualification (done)
  - AH-E005 [#206](https://github.com/JakeSelby/agent-harness/issues/206): Ship a defensible v1.0.0 stable release (open)

### AH-E002: Complete the harness stance and settings contract ([#116](https://github.com/JakeSelby/agent-harness/issues/116), open)

- **Milestones:** unscheduled; parts on v0.14.0 to v0.18.0
- **Goal:** Every stance and setting switch takes effect at every policy layer, and coordination and archive work for parallel agents.
- **Covers:** FR-2, FR-14, FR-61, FR-63
- **Stories:**
  - AH-S034 [#117](https://github.com/JakeSelby/agent-harness/issues/117): Make existing stance selections effective across every policy layer (done)
  - AH-S035 [#118](https://github.com/JakeSelby/agent-harness/issues/118): Separate verification policy from test-writing philosophy (open)
  - AH-S036 [#119](https://github.com/JakeSelby/agent-harness/issues/119): Make external-action approval configurable within explicit authorization (open)
  - AH-S037 [#120](https://github.com/JakeSelby/agent-harness/issues/120): Make delegation topology configurable without weakening role confinement (open)
  - AH-S038 [#121](https://github.com/JakeSelby/agent-harness/issues/121): Add configurable decision interaction and separate approval from presentation (open)
  - AH-S039 [#122](https://github.com/JakeSelby/agent-harness/issues/122): Make documentation style and document revision policy selectable (open)
  - AH-S040 [#123](https://github.com/JakeSelby/agent-harness/issues/123): Separate context lifecycle and numeric budgets from fixed rules (open)
  - AH-S041 [#124](https://github.com/JakeSelby/agent-harness/issues/124): Add review depth and independence policies (open)
  - AH-S042 [#125](https://github.com/JakeSelby/agent-harness/issues/125): Add positive alternatives to the build-versus-buy stance (open)
  - AH-S043 [#126](https://github.com/JakeSelby/agent-harness/issues/126): Add explicit change-scope preferences (open)
  - AH-S044 [#127](https://github.com/JakeSelby/agent-harness/issues/127): Add research-depth preferences with explicit stopping criteria (open)
  - AH-S045 [#128](https://github.com/JakeSelby/agent-harness/issues/128): Add local telemetry controls and retention (open)
  - AH-S046 [#129](https://github.com/JakeSelby/agent-harness/issues/129): Make gate blocking and retry budgets configurable (open)
  - AH-S047 [#130](https://github.com/JakeSelby/agent-harness/issues/130): Document invariants and enforce end-to-end switch consistency (open)
  - AH-S089 [#199](https://github.com/JakeSelby/agent-harness/issues/199): Add a shared architecture-viewer capability and adapter contract (done)
  - AH-D002 [#276](https://github.com/JakeSelby/agent-harness/issues/276): Decide how project and session stance selections reach a native client (done)
  - AH-S169 [#464](https://github.com/JakeSelby/agent-harness/issues/464): Ship the first stance constraints; the delegation/frontier contradiction is the fixture (done)
  - AH-C045 [#539](https://github.com/JakeSelby/agent-harness/issues/539): Say once that subagents never message each other; a blocked builder returns the question (done)
  - AH-SP008 [#541](https://github.com/JakeSelby/agent-harness/issues/541): Choose the neutral session-turn row: NirSession, agentsview's schema, or our own (open)
  - AH-S190 [#542](https://github.com/JakeSelby/agent-harness/issues/542): Write-intent ledger: builders claim paths and a PreToolUse check stops a live sibling's overlap (done)
  - AH-S191 [#543](https://github.com/JakeSelby/agent-harness/issues/543): harness sessions: a durable cross-runtime session archive in SQLite with FTS5 (open)
  - AH-S192 [#544](https://github.com/JakeSelby/agent-harness/issues/544): session-recall: a pull-based tool over the session archive that returns pointers, never pastes (open)
  - AH-D007 [#546](https://github.com/JakeSelby/agent-harness/issues/546): Hold semantic retrieval over the session archive until FTS misses and a measured arm justify it (open)
  - AH-C046 [#547](https://github.com/JakeSelby/agent-harness/issues/547): Reserve BMad IDs for the orchestration, conflict-mediation and session-archive tickets (done)
  - AH-D009 [#685](https://github.com/JakeSelby/agent-harness/issues/685): Decide whether the session archive may store the message bodies #128 forbids collecting (done)
  - AH-S219 [#691](https://github.com/JakeSelby/agent-harness/issues/691): Architecture viewer: the mailbox adapter behind the viewer seam (FR-46) (open)
  - AH-S221 [#693](https://github.com/JakeSelby/agent-harness/issues/693): Log every session-archive search with a usefulness signal (FR-63) (open)
  - AH-S271 [#935](https://github.com/JakeSelby/model-citizen/issues/935): Workspace map from .code-workspace files, with workspace list and open (done)

### AH-E003: Close the loop: Jev as the controller between measured rules and autonomy ([#135](https://github.com/JakeSelby/agent-harness/issues/135), open)

- **Milestones:** unscheduled; parts on v0.15.0 to v0.18.0
- **Goal:** Decision providers as the controller between measured rules and autonomy: evidence gates per stage, tighten-only act, stance proposals.
- **Covers:** FR-20, FR-47 to FR-50, FR-67
- **Stories:**
  - AH-S049 [#136](https://github.com/JakeSelby/agent-harness/issues/136): Jev: Add a shared decision-provider interface and Jev adapter (done)
  - AH-S050 [#137](https://github.com/JakeSelby/agent-harness/issues/137): Jev: Add opt-in Jev configuration and outbound-data controls (done)
  - AH-S051 [#138](https://github.com/JakeSelby/agent-harness/issues/138): Jev: Add versioned Jev question packs and a reproducible evaluation runner (done)
  - AH-S052 [#139](https://github.com/JakeSelby/agent-harness/issues/139): Jev: Add Jev diagnostics and decision telemetry (done)
  - AH-S053 [#140](https://github.com/JakeSelby/agent-harness/issues/140): Jev: Connect Jev checks to shared Claude Code and Codex runtime events (open)
  - AH-SP001 [#141](https://github.com/JakeSelby/agent-harness/issues/141): Jev: Verify stop claims against gate evidence (open)
  - AH-S054 [#142](https://github.com/JakeSelby/agent-harness/issues/142): Jev: Check response manner and explicit local constraints (done)
  - AH-S055 [#143](https://github.com/JakeSelby/agent-harness/issues/143): Jev: Rank bounded skill and context shortlists (open)
  - AH-S056 [#144](https://github.com/JakeSelby/agent-harness/issues/144): Jev: Flag repeated attempts and repeated questions in bounded exchanges (done)
  - AH-S057 [#145](https://github.com/JakeSelby/agent-harness/issues/145): Jev: Recommend delegation roles from bounded briefs (open)
  - AH-SP002 [#146](https://github.com/JakeSelby/agent-harness/issues/146): Jev: Evaluate advisory action-risk and tool-output screening with Jev (done)
  - AH-S058 [#147](https://github.com/JakeSelby/agent-harness/issues/147): Jev: Document and release the optional Jev adapter (open)
  - AH-S061 [#156](https://github.com/JakeSelby/agent-harness/issues/156): Jev: Check delegation-brief quality (open)
  - AH-S062 [#157](https://github.com/JakeSelby/agent-harness/issues/157): Jev: Check subagent return compliance (open)
  - AH-S063 [#158](https://github.com/JakeSelby/agent-harness/issues/158): Jev: Check review-card usefulness (done)
  - AH-SP003 [#159](https://github.com/JakeSelby/agent-harness/issues/159): Jev: Evaluate short source-to-output fidelity (done)
  - AH-S064 [#160](https://github.com/JakeSelby/agent-harness/issues/160): Jev: Check review-finding actionability (done)
  - AH-S065 [#161](https://github.com/JakeSelby/agent-harness/issues/161): Jev: Check internal consistency of short handoffs (done)
  - AH-S066 [#162](https://github.com/JakeSelby/agent-harness/issues/162): Jev: Check whether progress updates add information (done)
  - AH-S140 [#372](https://github.com/JakeSelby/agent-harness/issues/372): Jev: Gate the ask band of command grading, tighten-only (open)
  - AH-SP005 [#373](https://github.com/JakeSelby/agent-harness/issues/373): Jev: Evaluate deletion-only compaction against task success (open)
  - AH-SP006 [#374](https://github.com/JakeSelby/agent-harness/issues/374): Jev: Evaluate a typed same-work check for evasion_deny (open)
  - AH-S141 [#377](https://github.com/JakeSelby/agent-harness/issues/377): Jev: Hand-label seed fixtures for stop claims and the ask band (open)
  - AH-S172 [#485](https://github.com/JakeSelby/agent-harness/issues/485): Add the decision-provider contract with none and local providers (done)
  - AH-S175 [#523](https://github.com/JakeSelby/agent-harness/issues/523): Add a tier-0 class with no model to the cost sidecar schema (open)
  - AH-S176 [#524](https://github.com/JakeSelby/agent-harness/issues/524): Measure prompt-cache leverage: a cache axis in the replay, dispatch overhead, and the recorded TTL position (open)
  - AH-SP007 [#540](https://github.com/JakeSelby/agent-harness/issues/540): Does the Claude Code Workflow tool bypass band routing, confinement and the ledger? (done)
  - AH-S193 [#545](https://github.com/JakeSelby/agent-harness/issues/545): Run /review and /research as Workflow scripts in a measured arm against the prose forms (open)
  - AH-S213 [#647](https://github.com/JakeSelby/agent-harness/issues/647): harness usage --rules: list every rule as measured, dark or unmeasured, and load declarative detectors (open)
  - AH-C058 [#690](https://github.com/JakeSelby/agent-harness/issues/690): Restate #545's confined-role route: a Workflow script has no shell to run harness role run (open)
  - AH-S220 [#692](https://github.com/JakeSelby/agent-harness/issues/692): Report stance drift and propose promotions or demotions from the decision log (FR-50) (open)
  - AH-SP012 [#753](https://github.com/JakeSelby/agent-harness/issues/753): Jev: can a self-hosted Laya model serve the decision packs as a local provider? (open)
  - AH-S230 [#754](https://github.com/JakeSelby/agent-harness/issues/754): Replay: judge a one-policy arm pair on cost per passed task, decision calls included (open)
  - AH-C068 [#755](https://github.com/JakeSelby/agent-harness/issues/755): Jev: price its calls in the usage ledger (open)
  - AH-C069 [#756](https://github.com/JakeSelby/agent-harness/issues/756): Record four decision-layer follow-ups in the BMad corpus (done)
  - AH-S245 [#801](https://github.com/JakeSelby/agent-harness/issues/801): Jev: compliance packs calibrated on hand labels, with agreement and bias audits (open)

### AH-E004: Commit BMad as the repository’s public planning system ([#189](https://github.com/JakeSelby/agent-harness/issues/189), done)

- **Milestones:** completed
- **Goal:** BMad committed as the public planning system, with typed IDs and two-way links.
- **Covers:** FR-9
- **Stories:**
  - AH-C004 [#190](https://github.com/JakeSelby/agent-harness/issues/190): Commit the reproducible BMad installation boundary (done)
  - AH-C005 [#191](https://github.com/JakeSelby/agent-harness/issues/191): Publish the reconstructed public BMad product corpus (done)
  - AH-C006 [#192](https://github.com/JakeSelby/agent-harness/issues/192): Migrate GitHub issues to bidirectional BMad traceability (done)
  - AH-C007 [#193](https://github.com/JakeSelby/agent-harness/issues/193): Define the defensible v1.0.0 release program (done)
  - AH-B008 [#203](https://github.com/JakeSelby/agent-harness/issues/203): Correct traceability for personal-repository issue type limits (done)
  - AH-B009 [#218](https://github.com/JakeSelby/agent-harness/issues/218): Detect live title and lifecycle drift in BMad mappings (done)
  - AH-C027 [#375](https://github.com/JakeSelby/agent-harness/issues/375): Backfill BMad IDs for issues created after the 9/19 migration (done)
  - AH-S142 [#378](https://github.com/JakeSelby/agent-harness/issues/378): Refuse delivery work on an issue that has no BMad ID (done)
  - AH-B039 [#388](https://github.com/JakeSelby/agent-harness/issues/388): Live parent drift is reported in the direction that destroys the newer parent (done)

### AH-E005: Ship a defensible v1.0.0 stable release ([#206](https://github.com/JakeSelby/agent-harness/issues/206), open)

- **Milestones:** v1.0.0
- **Goal:** A defensible v1.0.0: frozen candidate, lifecycle proof, independent audit, publication of the audited bytes.
- **Covers:** FR-12, FR-66, NFR-5
- **Stories:**
  - AH-S048 [#134](https://github.com/JakeSelby/agent-harness/issues/134): Add bounded provider-backed end-to-end tests with inexpensive models (open)
  - AH-S090 [#207](https://github.com/JakeSelby/agent-harness/issues/207): Freeze the v1 stable support contract and candidate matrix (open)
  - AH-S091 [#208](https://github.com/JakeSelby/agent-harness/issues/208): Publish the v1 SemVer, deprecation, and migration contract (done)
  - AH-S092 [#209](https://github.com/JakeSelby/agent-harness/issues/209): Prove the v1 install, upgrade, rollback, and uninstall lifecycle (open)
  - AH-S093 [#210](https://github.com/JakeSelby/agent-harness/issues/210): Run an independent v1 release audit (open)
  - AH-S094 [#211](https://github.com/JakeSelby/agent-harness/issues/211): Cut and publish v1.0.0 from the audited candidate (open)
  - AH-C059 [#694](https://github.com/JakeSelby/agent-harness/issues/694): Repin the v1 lifecycle baseline and update the stale release references in #207, #209, #210 and #211 (open)
  - AH-S251 [#807](https://github.com/JakeSelby/agent-harness/issues/807): Add the evaluation gate to the v1.0 contract (open)

### AH-E006: Broaden developer adoption through evidence and feedback ([#212](https://github.com/JakeSelby/agent-harness/issues/212), open)

- **Milestones:** v0.18.0; children from v0.14.0 to v1.0.0
- **Goal:** Adoption through evidence: a proof, a direct tester cohort, launch staging, and copy that follows the PRD.
- **Covers:** FR-17, FR-18, FR-21, FR-54, SM-7
- **Stories:**
  - AH-S095 [#213](https://github.com/JakeSelby/agent-harness/issues/213): Publish a two-runtime proof and reproducible first-run demo (open)
  - AH-S096 [#214](https://github.com/JakeSelby/agent-harness/issues/214): Run a focused external developer feedback cohort (open)
  - AH-S097 [#215](https://github.com/JakeSelby/agent-harness/issues/215): Stage the public launch after onboarding feedback closes (open)
  - AH-E008 [#442](https://github.com/JakeSelby/agent-harness/issues/442): Field parity: install like everything else in the field (done)
  - AH-E009 [#443](https://github.com/JakeSelby/agent-harness/issues/443): The instrument: extract the rule-measurement engine, ship it, import it back (done)
  - AH-E010 [#444](https://github.com/JakeSelby/agent-harness/issues/444): Position on the evidence (done)
  - AH-C036 [#516](https://github.com/JakeSelby/agent-harness/issues/516): Fix the stale claims the enterprise-review intake found in our own docs (done)
  - AH-S173 [#517](https://github.com/JakeSelby/agent-harness/issues/517): Unlisted model variants are unpriced instead of inheriting the family rate (done)
  - AH-S174 [#518](https://github.com/JakeSelby/agent-harness/issues/518): Report the usage deduplication ratio beside the corrected figure (done)
  - AH-B054 [#519](https://github.com/JakeSelby/agent-harness/issues/519): Id-less assistant usage records are never deduplicated (done)
  - AH-C037 [#520](https://github.com/JakeSelby/agent-harness/issues/520): State per runtime whether the model-tier restriction is enforced or advisory (done)
  - AH-C038 [#521](https://github.com/JakeSelby/agent-harness/issues/521): Document the single-coordinator dispatch model and drop the dead hook entries from the template (done)
  - AH-C039 [#522](https://github.com/JakeSelby/agent-harness/issues/522): Run the detector corpus in CI and label the repository-specific detectors (done)
  - AH-S177 [#525](https://github.com/JakeSelby/agent-harness/issues/525): Record and replay model responses beneath the live cost benchmark (open)
  - AH-C040 [#526](https://github.com/JakeSelby/agent-harness/issues/526): Capped reads ask for newest-first and assert the order (done)
  - AH-C041 [#527](https://github.com/JakeSelby/agent-harness/issues/527): Log the enterprise-review roadmap intake (done)
  - AH-E011 [#552](https://github.com/JakeSelby/agent-harness/issues/552): A configurable layer under your skill library: one selection model, modes, superpowers, measured (open)
  - AH-C055 [#648](https://github.com/JakeSelby/agent-harness/issues/648): Align product.json on-the-way entries with the PRD and drop em dashes from README copy (done)
  - AH-S247 [#803](https://github.com/JakeSelby/agent-harness/issues/803): The field experiment: randomised real sessions, analysed by intention to treat (open)

### AH-E007: Graduate editor and desktop clients beyond preview ([#216](https://github.com/JakeSelby/agent-harness/issues/216), open)

- **Milestones:** unscheduled
- **Goal:** Editor and desktop clients graduate beyond preview only through independent evidence.
- **Covers:** FR-7, FR-70
- **Stories:**
  - AH-S098 [#217](https://github.com/JakeSelby/agent-harness/issues/217): Define and execute stable client graduation gates (open)

### AH-E008: Field parity: install like everything else in the field ([#442](https://github.com/JakeSelby/agent-harness/issues/442), done)

- **Milestones:** 0.12 (completed)
- **Goal:** Install like the rest of the field: installer, plugin listing, import, collision checks.
- **Covers:** FR-13, FR-17, FR-18
- **Stories:**
  - AH-S170 [#446](https://github.com/JakeSelby/agent-harness/issues/446): Ship a Claude Code plugin marketplace manifest (done)
  - AH-S152 [#447](https://github.com/JakeSelby/agent-harness/issues/447): Add a packaged install path beside the clone (done)
  - AH-S153 [#448](https://github.com/JakeSelby/agent-harness/issues/448): harness import: read an existing CLAUDE.md, AGENTS.md or .cursorrules into primitives (done)
  - AH-S154 [#449](https://github.com/JakeSelby/agent-harness/issues/449): Refuse skill and agent name collisions at sync (done)
  - AH-S155 [#450](https://github.com/JakeSelby/agent-harness/issues/450): Source the always-loaded cap or replace it with the measured figure (done)
  - AH-S171 [#477](https://github.com/JakeSelby/agent-harness/issues/477): Project rules and skills from external primitive roots at sync (done)

### AH-E009: The instrument: extract the rule-measurement engine, ship it, import it back ([#443](https://github.com/JakeSelby/agent-harness/issues/443), done)

- **Milestones:** 0.12 (completed)
- **Goal:** The instrument extracted as `ruleprobe`, published and vendored back.
- **Covers:** FR-19, FR-21, FR-22
- **Stories:**
  - AH-S156 [#451](https://github.com/JakeSelby/agent-harness/issues/451): Carve the rule-measurement engine into its own package (done)
  - AH-S157 [#452](https://github.com/JakeSelby/agent-harness/issues/452): Declarative detector format so a stranger can measure their own rules (done)
  - AH-S158 [#453](https://github.com/JakeSelby/agent-harness/issues/453): Publish 0.1.0 and pass the sixty-second test on a clean machine (done)
  - AH-S159 [#454](https://github.com/JakeSelby/agent-harness/issues/454): Vendor the package back; rule-detectors.py becomes the harness rule pack (done)
  - AH-S160 [#455](https://github.com/JakeSelby/agent-harness/issues/455): Labelled corpus for detector precision and recall (done)
  - AH-S161 [#456](https://github.com/JakeSelby/agent-harness/issues/456): Document the standalone path and list usage --rules in harness --help (done)

### AH-E010: Position on the evidence ([#444](https://github.com/JakeSelby/agent-harness/issues/444), done)

- **Milestones:** 0.12 (completed)
- **Goal:** Positioning on the evidence: measured rules lead, field scan published.
- **Covers:** FR-54, §1.2
- **Stories:**
  - AH-S162 [#457](https://github.com/JakeSelby/agent-harness/issues/457): Amend #230: stances become supporting, measured rules lead (done)
  - AH-S163 [#458](https://github.com/JakeSelby/agent-harness/issues/458): Rewrite product.json, the README opening and the capability group order (done)
  - AH-S164 [#459](https://github.com/JakeSelby/agent-harness/issues/459): Publish docs/field-scan.md from the logged research run; retire comparison.md (done)
  - AH-S165 [#460](https://github.com/JakeSelby/agent-harness/issues/460): Reconcile catalog.json qualified against capabilities.json unqualified (done)
  - AH-S166 [#461](https://github.com/JakeSelby/agent-harness/issues/461): Write up #429 and #324 as the measurement loop catching shipped features (done)
  - AH-S167 [#462](https://github.com/JakeSelby/agent-harness/issues/462): Docs follow the copy: how-it-works, preferences, stance-demo, getting-started (done)
  - AH-S168 [#463](https://github.com/JakeSelby/agent-harness/issues/463): Record the 0.13 release plan and roadmap arc under planning-artifacts (done)

### AH-E011: A configurable layer under your skill library: one selection model, modes, superpowers, measured ([#552](https://github.com/JakeSelby/agent-harness/issues/552), open)

- **Milestones:** v0.14.0 to v0.17.0
- **Goal:** One selection model: switches for every primitive, modes, the superpowers mode, the four-arm benchmark.
- **Covers:** FR-15, FR-16, FR-20, FR-58
- **Stories:**
  - AH-SP009 [#553](https://github.com/JakeSelby/agent-harness/issues/553): Does a headless superpowers session finish a task beside the harness? (open)
  - AH-S197 [#554](https://github.com/JakeSelby/agent-harness/issues/554): One selection document and one resolver (done)
  - AH-S198 [#555](https://github.com/JakeSelby/agent-harness/issues/555): Hook ids and the hooks switch kind (done)
  - AH-S199 [#556](https://github.com/JakeSelby/agent-harness/issues/556): Rules, skills, workflows and roles as switch kinds, with per-file links (done)
  - AH-S200 [#557](https://github.com/JakeSelby/agent-harness/issues/557): Modes: named selection bundles (done)
  - AH-S201 [#558](https://github.com/JakeSelby/agent-harness/issues/558): delegated stance variants, the superpowers mode, and detection (open)
  - AH-S202 [#559](https://github.com/JakeSelby/agent-harness/issues/559): Replay arms as built profiles, with pair parity (done)
  - AH-S203 [#560](https://github.com/JakeSelby/agent-harness/issues/560): Record the powered two-arm proof set for Measured (open)
  - AH-S204 [#561](https://github.com/JakeSelby/agent-harness/issues/561): Copy: the layer under your agents' skills (open)
  - AH-C048 [#562](https://github.com/JakeSelby/agent-harness/issues/562): Docs for the selection model, modes and the four arms (open)
  - AH-C049 [#563](https://github.com/JakeSelby/agent-harness/issues/563): Qualify and tag the release (done)
  - AH-C050 [#569](https://github.com/JakeSelby/agent-harness/issues/569): Reserve BMad IDs for the v0.14.0 release issues (done)
  - AH-S232 [#788](https://github.com/JakeSelby/agent-harness/issues/788): Module manifest: claims, surface, instruments, slot, dependencies and conflicts (done)
  - AH-S244 [#800](https://github.com/JakeSelby/agent-harness/issues/800): The slot model and the adapter contract (open)
  - AH-S246 [#802](https://github.com/JakeSelby/agent-harness/issues/802): The four-arm bench and layer swaps (open)

### AH-E012: Re-baseline the BMad corpus and make story files the design record ([#616](https://github.com/JakeSelby/agent-harness/issues/616), done)

- **Milestones:** completed
- **Goal:** The BMad corpus re-baselined and story files made the design record.
- **Covers:** FR-9, FR-64, FR-65
- **Stories:**
  - AH-T009 [#617](https://github.com/JakeSelby/agent-harness/issues/617): Promote existing research into BMad research artifacts (done)
  - AH-T010 [#618](https://github.com/JakeSelby/agent-harness/issues/618): Re-baseline the product brief and PRD (done)
  - AH-T011 [#619](https://github.com/JakeSelby/agent-harness/issues/619): Re-baseline the UX specification and architecture spine (done)
  - AH-S206 [#620](https://github.com/JakeSelby/agent-harness/issues/620): Rich story files: typed templates, managed issue block, lossless upgrade, depth check (done)
  - AH-S207 [#621](https://github.com/JakeSelby/agent-harness/issues/621): Repo rule: route every operation through BMad and keep its corpus current (done)
  - AH-T012 [#622](https://github.com/JakeSelby/agent-harness/issues/622): Re-derive epics and reparent the open issue tree (done)
  - AH-S208 [#623](https://github.com/JakeSelby/agent-harness/issues/623): Implementation readiness and a derived sprint status (done)
  - AH-T014 [#652](https://github.com/JakeSelby/agent-harness/issues/652): Enrich story files: the seventeen epics and the re-baseline tree (done)
  - AH-T015 [#653](https://github.com/JakeSelby/agent-harness/issues/653): Enrich story files: the stance and settings contract (#116) (done)
  - AH-T016 [#654](https://github.com/JakeSelby/agent-harness/issues/654): Enrich story files: close the loop and the decision layer (#135) (done)
  - AH-T017 [#655](https://github.com/JakeSelby/agent-harness/issues/655): Enrich story files: the selection model, measurement and architecture conformance (#552, #632, #636) (done)
  - AH-T018 [#656](https://github.com/JakeSelby/agent-harness/issues/656): Enrich story files: qualification, guardrails and traceability (#633, #634, #635) (done)
  - AH-T019 [#657](https://github.com/JakeSelby/agent-harness/issues/657): Enrich story files: the provider-agnostic rebuild, BMad commit, v1 and client graduation (#93, #189, #206, #216) (done)
  - AH-T020 [#658](https://github.com/JakeSelby/agent-harness/issues/658): Enrich story files: adoption and the 0.13 adoption epics (#212, #442, #443, #444) (done)
  - AH-T021 [#659](https://github.com/JakeSelby/agent-harness/issues/659): Enrich story files: delivered work without an epic, issues #1 to #120 (done)
  - AH-T022 [#660](https://github.com/JakeSelby/agent-harness/issues/660): Enrich story files: delivered work without an epic, issues #121 to #300 (done)
  - AH-T023 [#661](https://github.com/JakeSelby/agent-harness/issues/661): Enrich story files: delivered work without an epic, issues #301 to #360 (done)
  - AH-T024 [#662](https://github.com/JakeSelby/agent-harness/issues/662): Enrich story files: delivered work without an epic, issues #361 to #420 (done)
  - AH-T025 [#663](https://github.com/JakeSelby/agent-harness/issues/663): Enrich story files: delivered work without an epic, issues #421 onward (done)
  - AH-B071 [#666](https://github.com/JakeSelby/agent-harness/issues/666): The real-corpus upgrade test fails once the last amended stub is upgraded (done)
  - AH-C057 [#684](https://github.com/JakeSelby/agent-harness/issues/684): Reserve IDs and write story files for the re-baseline follow-ups (done)

### AH-E013: Measurement: the cost benchmark, detector precision and the evaluation pyramid ([#632](https://github.com/JakeSelby/agent-harness/issues/632), open)

- **Milestones:** v0.14.0 to v0.18.0
- **Goal:** Measurement: the cost benchmark on the release task set, detector precision, the evaluation pyramid.
- **Covers:** FR-22, FR-34, FR-55 to FR-58, SM-2
- **Stories:**
  - AH-S138 [#369](https://github.com/JakeSelby/agent-harness/issues/369): Record the first live cost replay results (done)
  - AH-S252 [#428](https://github.com/JakeSelby/agent-harness/issues/428): Replay charges the user's personal layer to the harness (done)
  - AH-B095 [#429](https://github.com/JakeSelby/agent-harness/issues/429): The delegation stance never fires: zero subagent spawns in 19 headless runs (open)
  - AH-S194 [#430](https://github.com/JakeSelby/agent-harness/issues/430): Trim the harness's 12,607-token standing context (open)
  - AH-S253 [#482](https://github.com/JakeSelby/agent-harness/issues/482): Record the CLI's own account of the loaded surface on every replay row, and refuse to launch on plugin drift (open)
  - AH-S254 [#509](https://github.com/JakeSelby/agent-harness/issues/509): Price every rule, stance variant, skill and agent description individually in the static tier, and report a change's token delta in CI (done)
  - AH-S255 [#510](https://github.com/JakeSelby/agent-harness/issues/510): Replay stored transcripts through the rule-detector registry offline, one row per rule per run, and backfill every evidence set (open)
  - AH-S256 [#511](https://github.com/JakeSelby/agent-harness/issues/511): Run every hook against recorded tool calls under each stance variant as a deterministic permutation matrix (open)
  - AH-S257 [#512](https://github.com/JakeSelby/agent-harness/issues/512): Add a micro-task tier on a cheap model that reports whether each claimed mechanism fires (open)
  - AH-S258 [#513](https://github.com/JakeSelby/agent-harness/issues/513): Nudge delegation from the existing detector through PostToolUse context, since the spawn hook only fires once a spawn is attempted (open)
  - AH-S259 [#514](https://github.com/JakeSelby/agent-harness/issues/514): Per-rule cost attribution across the whole loaded instruction surface (open)
  - AH-S215 [#602](https://github.com/JakeSelby/agent-harness/issues/602): Tighten the two detectors the corpus records under the precision floor (open)
  - AH-S233 [#789](https://github.com/JakeSelby/agent-harness/issues/789): Stamp the profile fingerprint on every ledger row (done)
  - AH-S234 [#790](https://github.com/JakeSelby/agent-harness/issues/790): Attribute context tokens and hook decisions to the module that caused them (done)
  - AH-S235 [#791](https://github.com/JakeSelby/agent-harness/issues/791): Zero-footprint observation: watching the bare arm changes no model request (done)
  - AH-S236 [#792](https://github.com/JakeSelby/agent-harness/issues/792): Adherence events: record each recommendation and whether the user followed it (done)
  - AH-S237 [#793](https://github.com/JakeSelby/agent-harness/issues/793): Replay: fix the six defects that keep the two arms from being comparable (done)
  - AH-S238 [#794](https://github.com/JakeSelby/agent-harness/issues/794): An evidence standard and a pre-registration template for published results (done)
  - AH-S239 [#795](https://github.com/JakeSelby/agent-harness/issues/795): Replay: Cost-of-Pass and pass rate with paired, task-clustered intervals (open)
  - AH-S240 [#796](https://github.com/JakeSelby/agent-harness/issues/796): Replay: a task set sized by power analysis, long tasks included, at five or more trials (open)
  - AH-S241 [#797](https://github.com/JakeSelby/agent-harness/issues/797): Unit evals: the two-by-two of one rule and the economy concern (open)
  - AH-S242 [#798](https://github.com/JakeSelby/agent-harness/issues/798): The soft-estimate report: adherence rate and the if-followed estimate (open)
  - AH-S249 [#805](https://github.com/JakeSelby/agent-harness/issues/805): A factorial screening runner for switch permutations (open)
  - AH-S250 [#806](https://github.com/JakeSelby/agent-harness/issues/806): An external task set from Harbor, after a licensing review (open)

### AH-E014: Qualify a release in one round, and let its claims match its evidence ([#633](https://github.com/JakeSelby/agent-harness/issues/633), open)

- **Milestones:** v0.14.0 to v1.0.0
- **Goal:** A release in one qualification round, with claims that match evidence.
- **Covers:** FR-6, FR-7, FR-12, FR-51 to FR-53, NFR-2, NFR-14
- **Stories:**
  - AH-C043 [#533](https://github.com/JakeSelby/agent-harness/issues/533): Qualify the required CLI targets and restore the release floor (done)
  - AH-D008 [#582](https://github.com/JakeSelby/agent-harness/issues/582): Decide whether qualification evidence is invalidated per case (#333 part 1b) (done)
  - AH-S214 [#612](https://github.com/JakeSelby/agent-harness/issues/612): Acceptance runner: use the Codex session login in its disposable home (done)
  - AH-C052 [#627](https://github.com/JakeSelby/agent-harness/issues/627): Open the 0.13.0 candidate (done)
  - AH-T013 [#638](https://github.com/JakeSelby/agent-harness/issues/638): Amend the 0.12.0 release notes with the work merged between #465 and #494 (done)
  - AH-S209 [#639](https://github.com/JakeSelby/agent-harness/issues/639): Compatibility catalog: an explicit waiver for a release with no required target (open)
  - AH-C053 [#640](https://github.com/JakeSelby/agent-harness/issues/640): CI: check the Python 3.9 floor on every pull request (done)
  - AH-B096 [#677](https://github.com/JakeSelby/agent-harness/issues/677): Provisioning leaves an untracked marker in the frozen clone, and the runner then refuses the clone as dirty (done)
  - AH-D010 [#686](https://github.com/JakeSelby/agent-harness/issues/686): Decide whether a release may require Codex CLI before FR-12's agreement round (open)
  - AH-D011 [#688](https://github.com/JakeSelby/agent-harness/issues/688): Decide whether a red smoke tier blocks the qualification round, as FR-52 says (done)
  - AH-B073 [#689](https://github.com/JakeSelby/agent-harness/issues/689): A resumed qualification round reruns completed cases; FR-52 says it skips them (done)
  - AH-B097 [#707](https://github.com/JakeSelby/agent-harness/issues/707): CLI ergonomics found by the first live qualification round: task save runtime default, role run --runtime check, task --input traceback (open)
  - AH-S243 [#799](https://github.com/JakeSelby/agent-harness/issues/799): Proof set 1: the proof bundle, harness evidence verify, and claims gated on it (open)

### AH-E015: Guardrails and runtime parity: close the gaps between runtimes and between docs and code ([#634](https://github.com/JakeSelby/agent-harness/issues/634), open)

- **Milestones:** v0.14.0 to v0.18.0
- **Goal:** Guardrails and runtime parity: close the gaps between runtimes and between docs and code.
- **Covers:** FR-2, FR-4, FR-18, FR-30, FR-35 to FR-41, FR-44
- **Stories:**
  - AH-B022 [#292](https://github.com/JakeSelby/agent-harness/issues/292): Output-filter rewrites never reach Codex (open)
  - AH-B023 [#293](https://github.com/JakeSelby/agent-harness/issues/293): Isolated role workers stall without network under Codex auto permissions (open)
  - AH-B024 [#294](https://github.com/JakeSelby/agent-harness/issues/294): harness sync ignores HARNESS_STANCE_ variables that harness stances honours (done)
  - AH-B047 [#406](https://github.com/JakeSelby/agent-harness/issues/406): harness task save writes an absolute path its own lint rejects (open)
  - AH-B049 [#413](https://github.com/JakeSelby/agent-harness/issues/413): harness worktree remove cannot finish a checkout that holds a submodule (open)
  - AH-B068 [#538](https://github.com/JakeSelby/agent-harness/issues/538): Hide an installed Codex client from the reviewer-key detection test (done)
  - AH-S217 [#576](https://github.com/JakeSelby/agent-harness/issues/576): Guard the Workflow tool launch: log it, honour delegation off, refuse constrained roles in agentType (done)
  - AH-S216 [#577](https://github.com/JakeSelby/agent-harness/issues/577): harness usage: separate workflow rows and stop pricing them against a confined role budget (done)
  - AH-B067 [#611](https://github.com/JakeSelby/agent-harness/issues/611): stop-gate: interleaved sessions in one checkout reset each other's block count, so the gate never releases (done)
  - AH-B064 [#641](https://github.com/JakeSelby/agent-harness/issues/641): Plugin channel: constrained roles install unconfined, and the manifest version lags VERSION (open)
  - AH-B070 [#650](https://github.com/JakeSelby/agent-harness/issues/650): sync --dry-run words its summary lines as if it had applied the changes (open)
  - AH-B072 [#687](https://github.com/JakeSelby/agent-harness/issues/687): delegation off: the spawn hook emits ask while the stance and the engine deny (done)
  - AH-C060 [#695](https://github.com/JakeSelby/agent-harness/issues/695): docs: the standalone-measurement page still calls the published ruleprobe package planned (open)
  - AH-S248 [#804](https://github.com/JakeSelby/agent-harness/issues/804): Codex parity: ingest Codex telemetry and add a Codex arm (open)

### AH-E016: Harden BMad traceability for many sessions filing at once ([#635](https://github.com/JakeSelby/agent-harness/issues/635), open)

- **Milestones:** unscheduled
- **Goal:** BMad traceability safe for many sessions filing at once.
- **Covers:** FR-9, FR-64
- **Stories:**
  - AH-B040 [#392](https://github.com/JakeSelby/agent-harness/issues/392): new files an issue the list endpoint does not yet return, so reserve fails (done)
  - AH-B042 [#410](https://github.com/JakeSelby/agent-harness/issues/410): reserve allocates an ID without checking what the default branch already holds (open)
  - AH-B043 [#411](https://github.com/JakeSelby/agent-harness/issues/411): issue-ownership does not say which issues it found, so a keyword in prose is invisible (open)
  - AH-B044 [#412](https://github.com/JakeSelby/agent-harness/issues/412): the issue number is keyed github_number in the map and issue in planned actions (open)
  - AH-D005 [#420](https://github.com/JakeSelby/agent-harness/issues/420): Decide how map lifecycle stays current without a pull request after every merge (open)
  - AH-D006 [#421](https://github.com/JakeSelby/agent-harness/issues/421): grade-bash refuses a sub-issue delete that bmad_issue_sync apply performs unattended (open)
  - AH-B069 [#537](https://github.com/JakeSelby/agent-harness/issues/537): Reserve a BMad ID in the same invocation that files the issue (done)
  - AH-B065 [#642](https://github.com/JakeSelby/agent-harness/issues/642): bmad_issue_sync apply: verify artifact frontmatter on main, not only presence (open)
  - AH-C061 [#696](https://github.com/JakeSelby/agent-harness/issues/696): Close duplicate and superseded open issues: #392, #433 and #537; #538 and #606; #369 (done)
  - AH-C062 [#697](https://github.com/JakeSelby/agent-harness/issues/697): Track or waive the undelivered acceptance items on closed issues (open)
  - AH-S222 [#698](https://github.com/JakeSelby/agent-harness/issues/698): Carry GitHub's stateReason into the issue map so not-planned items stop reading as completed (open)
  - AH-C063 [#699](https://github.com/JakeSelby/agent-harness/issues/699): Give the decision and epic story templates a Change log section (open)

### AH-E017: Bring the code into line with the 2026-09-23 architecture spine ([#636](https://github.com/JakeSelby/agent-harness/issues/636), open)

- **Milestones:** unscheduled; parts on v0.14.0
- **Goal:** The code brought in line with the 2026-09-23 architecture spine's migration notes.
- **Covers:** FR-2, FR-11, FR-60, NFR-6
- **Stories:**
  - AH-S210 [#643](https://github.com/JakeSelby/agent-harness/issues/643): Resolve stances only through posture.py and retire the CLI ladder (done)
  - AH-S211 [#644](https://github.com/JakeSelby/agent-harness/issues/644): Ledger rows carry a schema version and readers tolerate unknown fields (done)
  - AH-C054 [#645](https://github.com/JakeSelby/agent-harness/issues/645): One kernel helper for atomic whole-file rewrites that keeps file mode (open)
  - AH-S212 [#646](https://github.com/JakeSelby/agent-harness/issues/646): Remote Control: pin the verified client versions and warn in doctor outside them (open)

### AH-E018: Cut cache-rebuild spend in long interactive sessions ([#745](https://github.com/JakeSelby/agent-harness/issues/745), open)

- **Milestones:** v0.15.0 to v0.18.0
- **Goal:** Idle-over-an-hour cache rebuilds below 5% of long-session spend, from 10.7%, with rebuilds measured by cause.
- **Covers:** FR-23, FR-24, FR-32, FR-59
- **Stories:**
  - AH-C067 [#746](https://github.com/JakeSelby/agent-harness/issues/746): Reserve IDs and write story files for the cache-rebuild epic (done)
  - AH-SP010 [#747](https://github.com/JakeSelby/agent-harness/issues/747): Spike: does a UserPromptSubmit block hold the prompt in the CLI and VS Code? (open)
  - AH-S227 [#748](https://github.com/JakeSelby/agent-harness/issues/748): harness usage --by rebuild: attribute cache rebuilds to causes (open)
  - AH-S228 [#749](https://github.com/JakeSelby/agent-harness/issues/749): Refresh the handoff at task boundaries in /land and /close-out (open)
  - AH-S229 [#750](https://github.com/JakeSelby/agent-harness/issues/750): Cold-resume guard: hold one prompt after a long idle on a large context (open)
  - AH-SP011 [#751](https://github.com/JakeSelby/agent-harness/issues/751): Spike: one-week trial of an earlier auto-compact window (open)

### AH-E020: Rename the product to Model Citizen ([#875](https://github.com/JakeSelby/agent-harness/issues/875), open)

- **Milestones:** v0.14.0
- **Goal:** Every living surface says Model Citizen, `citizen` runs everything `harness` does, and old links still resolve, while on-disk names, `AH-` IDs, telemetry and dated records keep `agent-harness` (AD-24).
- **Covers:** FR-9, FR-17, FR-18, FR-53, FR-54, FR-66
- **Stories:**
  - AH-C078 [#876](https://github.com/JakeSelby/agent-harness/issues/876): Record the Model Citizen rename's sprint change and reserve its stories (done)
  - AH-S267 [#877](https://github.com/JakeSelby/agent-harness/issues/877): Add the citizen command and keep harness as an alias (done)
  - AH-S268 [#878](https://github.com/JakeSelby/agent-harness/issues/878): Rename the plugin to model-citizen and keep recognizing the old ID (done)
  - AH-S269 [#879](https://github.com/JakeSelby/agent-harness/issues/879): Rename the product copy to Model Citizen (done)
  - AH-C079 [#880](https://github.com/JakeSelby/agent-harness/issues/880): Point hardcoded links at the new repo and domain, and add the retired-name lint rule (done)

### AH-E021: Run a burndown bot for owner-labelled bugs and scanned vulnerabilities ([#941](https://github.com/JakeSelby/model-citizen/issues/941), open)

- **Milestones:** v0.18.0
- **Goal:** Owner-labelled bugs are fixed without a live session, in a Claude lane and a Codex lane under the bot's own GitHub App and worked through CodeRabbit to merge; scanned vulnerabilities reach a private draft advisory first; every attempt is randomised and recorded for the v0.18.0 field experiment (#803).
- **Covers:** FR-71, FR-72, FR-73, FR-74
- **Stories:**
  - AH-SP016 [#943](https://github.com/JakeSelby/model-citizen/issues/943): Spike: which identity and surface can trigger `@codex` (open)
  - AH-S273 [#944](https://github.com/JakeSelby/model-citizen/issues/944): Keep bot work out of benchmark task mining and require an explicit `--tag` (open)
  - AH-S274 [#945](https://github.com/JakeSelby/model-citizen/issues/945): Give the burndown bot its own GitHub App identity and an owner-label gate (open)
  - AH-S275 [#946](https://github.com/JakeSelby/model-citizen/issues/946): Hold the burndown bot to a hard budget with a pause switch and a clean stop (open)
  - AH-S276 [#947](https://github.com/JakeSelby/model-citizen/issues/947): Pre-register the burndown bot's randomised stream and record every attempt as assigned (open)
  - AH-S277 [#948](https://github.com/JakeSelby/model-citizen/issues/948): Claude lane: fix an owner-labelled bug in its assigned arm and open one pull request (open)
  - AH-S278 [#949](https://github.com/JakeSelby/model-citizen/issues/949): Work CodeRabbit through to merge on the burndown bot's pull requests (open)
  - AH-S279 [#950](https://github.com/JakeSelby/model-citizen/issues/950): Codex lane: send a fixed share of eligible bugs to `@codex` (open)
  - AH-S280 [#951](https://github.com/JakeSelby/model-citizen/issues/951): Vuln lane: scan weekly and file each finding as a draft security advisory (open)

### AH-E022: Open the Studio: one local UI that shows the whole harness, live ([#955](https://github.com/JakeSelby/model-citizen/issues/955), open)

- **Milestones:** v0.16.0
- **Goal:** `citizen studio` opens a local UI, locked to its launcher and needing no Node at runtime, that shows the installed version and health, the effective selection with each value's layer, every module with its cost, and what the harness decided, live within two seconds.
- **Covers:** FR-75, FR-76, FR-77
- **Stories:**
  - AH-C082 [#960](https://github.com/JakeSelby/model-citizen/issues/960): File the Studio epics and stories (done)
  - AH-C083 [#1005](https://github.com/JakeSelby/model-citizen/issues/1005): Record the Studio field check as a research digest (done)
  - AH-T035 [#961](https://github.com/JakeSelby/model-citizen/issues/961): Write the Studio's UX specification (open)
  - AH-T036 [#962](https://github.com/JakeSelby/model-citizen/issues/962): Record the Studio's architecture decisions in the spine (open)
  - AH-SP017 [#963](https://github.com/JakeSelby/model-citizen/issues/963): Spike: can the committed UI bundle be rebuilt byte for byte on Linux CI? (open)
  - AH-S281 [#964](https://github.com/JakeSelby/model-citizen/issues/964): `citizen studio`: start, open, detach, check and stop a loopback-only local server (open)
  - AH-S282 [#965](https://github.com/JakeSelby/model-citizen/issues/965): Lock the Studio to its launcher: token, host and origin checks, CSRF, CSP and redaction (open)
  - AH-S283 [#966](https://github.com/JakeSelby/model-citizen/issues/966): The Studio's front-end workspace, committed bundle and CI build check (open)
  - AH-S284 [#967](https://github.com/JakeSelby/model-citizen/issues/967): Studio shell, design tokens and component kit (open)
  - AH-S285 [#968](https://github.com/JakeSelby/model-citizen/issues/968): Overview: the installed version, updates, doctor checks and drift at a glance (open)
  - AH-S286 [#969](https://github.com/JakeSelby/model-citizen/issues/969): Effective selection, with the layer each value comes from (open)
  - AH-S287 [#970](https://github.com/JakeSelby/model-citizen/issues/970): Library: every module with its manifest, projection and context cost (open)
  - AH-S288 [#971](https://github.com/JakeSelby/model-citizen/issues/971): Activity: what the harness decided and what changed (open)
  - AH-S289 [#972](https://github.com/JakeSelby/model-citizen/issues/972): Live updates: a change from the CLI, an agent or an editor shows within two seconds (open)

### AH-E023: Tune in the Studio: drafts for every change, applied or rolled back through the governed path ([#956](https://github.com/JakeSelby/model-citizen/issues/956), open)

- **Milestones:** v0.16.0 initial release; later children in v0.17.0 and v0.18.0
- **Goal:** Every Studio edit lands in a draft, a managed worktree, and is applied through the CLI's own locks, checks and journal or rolled back in one step; nothing live changes before the developer applies it.
- **Covers:** FR-78, FR-79
- **Stories:**
  - AH-S290 [#973](https://github.com/JakeSelby/model-citizen/issues/973): Drafts: create, list, diff and discard a draft from the Studio or `citizen draft` (open)
  - AH-S291 [#974](https://github.com/JakeSelby/model-citizen/issues/974): Change mode, stance variants and switches in a draft, with previews and dependency checks (open)
  - AH-S292 [#975](https://github.com/JakeSelby/model-citizen/issues/975): Edit identity, preferences and integrations in a draft through schema-driven forms (open)
  - AH-S293 [#976](https://github.com/JakeSelby/model-citizen/issues/976): Hand-edit a rule, skill or stance in a draft, with live lint, budget and projection preview (open)
  - AH-S294 [#977](https://github.com/JakeSelby/model-citizen/issues/977): Add your own module from a template, or fork a core module into your root (open)
  - AH-S295 [#978](https://github.com/JakeSelby/model-citizen/issues/978): Review and apply a draft through the governed path (open)
  - AH-S296 [#979](https://github.com/JakeSelby/model-citizen/issues/979): Roll back an applied draft (open)
  - AH-S325 [#1063](https://github.com/JakeSelby/model-citizen/issues/1063): Configure composition slots and Superpowers in Studio (open; v0.17.0)

### AH-E024: Run from the Studio: any suite or single test against the installed version, a release or a draft ([#957](https://github.com/JakeSelby/model-citizen/issues/957), open)

- **Milestones:** v0.16.0 initial release; later children in v0.17.0 and v0.18.0
- **Goal:** Any suite or single test runs from the Studio against the installed version, a release, a branch, a worktree or a draft, in an isolated profile, with live logs, cancel, a kept record and spend confirmed first.
- **Covers:** FR-80, FR-81
- **Stories:**
  - AH-S297 [#980](https://github.com/JakeSelby/model-citizen/issues/980): Suite catalog and run supervisor, with `citizen runs` (open)
  - AH-S298 [#981](https://github.com/JakeSelby/model-citizen/issues/981): Run store: every run recorded, with benchmark and acceptance history imported (open)
  - AH-S299 [#982](https://github.com/JakeSelby/model-citizen/issues/982): Launch, watch and stop the free local suites from the Studio, down to a single unit test (open)
  - AH-S300 [#983](https://github.com/JakeSelby/model-citizen/issues/983): Run history and run detail, with flaky-test marks (open)
  - AH-S301 [#984](https://github.com/JakeSelby/model-citizen/issues/984): Targets: run against the installed version, a release, a branch, a worktree or a draft, in an isolated profile (open)
  - AH-S302 [#985](https://github.com/JakeSelby/model-citizen/issues/985): Spend guard: estimate, confirm and cap a run that spends usage (open)
  - AH-S303 [#986](https://github.com/JakeSelby/model-citizen/issues/986): Run native acceptance from the Studio, case by case (open)
  - AH-S304 [#987](https://github.com/JakeSelby/model-citizen/issues/987): Run the live replay benchmark from the Studio against two targets (open)
  - AH-S305 [#988](https://github.com/JakeSelby/model-citizen/issues/988): Run the evaluation tiers and unit evals from the Studio (open)
  - AH-S306 [#989](https://github.com/JakeSelby/model-citizen/issues/989): Run the four-arm bench and layer swaps from Studio (open; v0.17.0)
  - AH-S319 [#1009](https://github.com/JakeSelby/model-citizen/issues/1009): Import `claude plugin eval` reports into the run store (open)
  - AH-S324 [#1062](https://github.com/JakeSelby/model-citizen/issues/1062): Adapt Studio to the landed Measured evaluation contracts (open; v0.16.0)
  - AH-S327 [#1065](https://github.com/JakeSelby/model-citizen/issues/1065): Run factorial switch screens from Studio (open; v0.18.0)

### AH-E025: Judge in the Studio: whether a change helped, against its baseline and its history ([#958](https://github.com/JakeSelby/model-citizen/issues/958), open)

- **Milestones:** v0.16.0 initial release; later children in v0.17.0 and v0.18.0
- **Goal:** Every result sits beside its baseline and its history: paired comparisons with intervals, a verdict for a draft against its base, trends beside the proof set, rule health, spend, and evidence-backed proposals the developer applies.
- **Covers:** FR-82, FR-83
- **Stories:**
  - AH-S307 [#990](https://github.com/JakeSelby/model-citizen/issues/990): Compare two runs, with paired intervals (open)
  - AH-S308 [#991](https://github.com/JakeSelby/model-citizen/issues/991): Test a draft: run it against its base and report whether it helped (open)
  - AH-S309 [#992](https://github.com/JakeSelby/model-citizen/issues/992): Trends across versions and dates, beside the project's proof set (open)
  - AH-S310 [#994](https://github.com/JakeSelby/model-citizen/issues/994): Rule health: measurement status, adherence and context cost for every rule (open)
  - AH-S311 [#995](https://github.com/JakeSelby/model-citizen/issues/995): Spend and usage from the local ledger (open)
  - AH-S312 [#996](https://github.com/JakeSelby/model-citizen/issues/996): Proposals: review evidence-backed stance proposals and try one in a draft (open; v0.18.0)
  - AH-S326 [#1064](https://github.com/JakeSelby/model-citizen/issues/1064): Show calibrated compliance judge evidence in Studio (open; v0.17.0)
  - AH-S328 [#1066](https://github.com/JakeSelby/model-citizen/issues/1066): Report field and Codex evaluation evidence in Studio (open; v0.18.0)

### AH-E026: Adopt the Studio: from install to a tuned harness, for developers and their agents ([#959](https://github.com/JakeSelby/model-citizen/issues/959), open)

- **Milestones:** v0.16.0
- **Goal:** A new install reaches a tuned harness through a guided first run, agents run the same loop from the CLI, and the Studio is accessible, checked end to end, documented and qualified in every release.
- **Covers:** FR-84, FR-75, FR-77
- **Stories:**
  - AH-S313 [#997](https://github.com/JakeSelby/model-citizen/issues/997): First run in the Studio: from install to an applied draft (open)
  - AH-S314 [#998](https://github.com/JakeSelby/model-citizen/issues/998): Agents drive the Studio loop from the CLI, with a skill and a parity check (open)
  - AH-S315 [#999](https://github.com/JakeSelby/model-citizen/issues/999): Accessibility and phone-width layouts to WCAG 2.2 AA (open)
  - AH-S316 [#1000](https://github.com/JakeSelby/model-citizen/issues/1000): End-to-end and performance checks for the Studio in CI (open)
  - AH-S317 [#1001](https://github.com/JakeSelby/model-citizen/issues/1001): Document the Studio and add it to the product copy (open)
  - AH-S318 [#1002](https://github.com/JakeSelby/model-citizen/issues/1002): Qualify the Studio in every release (open)
  - AH-SP018 [#1003](https://github.com/JakeSelby/model-citizen/issues/1003): Spike: reach the Studio from a phone without opening it to the network (open)

## Completed work with no epic

Listed by issue range, which is also how the story-enrichment batches B08 to B12 (#659 to #663) take them.

### Issues #1 to #120 (35)

- AH-S001 [#1](https://github.com/JakeSelby/agent-harness/issues/1): feat(hook): Stop hook runs the repo gate and blocks completion while red
- AH-C001 [#2](https://github.com/JakeSelby/agent-harness/issues/2): refactor(rules): cap always-loaded context at 200 lines, move rationale to skills
- AH-S002 [#3](https://github.com/JakeSelby/agent-harness/issues/3): feat(agents): ship gatherer, reviewer and log-compressor subagent definitions
- AH-S003 [#4](https://github.com/JakeSelby/agent-harness/issues/4): feat(stance): add cost stance and cache-hygiene rule
- AH-S004 [#5](https://github.com/JakeSelby/agent-harness/issues/5): feat(hook): PreToolUse filter for test, build and log output
- AH-S005 [#6](https://github.com/JakeSelby/agent-harness/issues/6): feat(skill): progress file, /handoff command and learnings capture
- AH-S006 [#7](https://github.com/JakeSelby/agent-harness/issues/7): feat(cli): usage telemetry hook and harness usage report
- AH-S007 [#8](https://github.com/JakeSelby/agent-harness/issues/8): feat(commands): /research, /plan, /build, /review
- AH-S008 [#9](https://github.com/JakeSelby/agent-harness/issues/9): feat(template): permissions.deny rules for secrets and generated directories
- AH-S009 [#10](https://github.com/JakeSelby/agent-harness/issues/10): feat(stance): container or sandbox posture for autonomous loops
- AH-S010 [#11](https://github.com/JakeSelby/agent-harness/issues/11): feat(hook): neutralize instruction-shaped text in Bash, fetch and read output
- AH-S011 [#24](https://github.com/JakeSelby/agent-harness/issues/24): feat(agents): builder agent carrying the implementation brief
- AH-S012 [#25](https://github.com/JakeSelby/agent-harness/issues/25): feat(agents): spec-reviewer agent and two-stage /review
- AH-S013 [#26](https://github.com/JakeSelby/agent-harness/issues/26): feat(agents): planner agent carrying the Review Card contract
- AH-S014 [#27](https://github.com/JakeSelby/agent-harness/issues/27): feat(agents): design-judge agent for the design loop
- AH-B001 [#37](https://github.com/JakeSelby/agent-harness/issues/37): Plan mode prompts for read-only work it can't parse (loops, $(...), WebFetch)
- AH-S015 [#48](https://github.com/JakeSelby/agent-harness/issues/48): feat: keep the delegation stance in charge when a framework spawns subagents
- AH-S016 [#50](https://github.com/JakeSelby/agent-harness/issues/50): feat(hook): keep bare spawns on the session model inside a framework repository
- AH-S017 [#51](https://github.com/JakeSelby/agent-harness/issues/51): feat(cli): install the BMad overrides at session start and refuse drifted templates
- AH-D001 [#52](https://github.com/JakeSelby/agent-harness/issues/52): docs(bmad): decide the builder-routing follow-up
- AH-S018 [#58](https://github.com/JakeSelby/agent-harness/issues/58): feat(hooks): rule detector registry with corpus and lint coverage check
- AH-S019 [#59](https://github.com/JakeSelby/agent-harness/issues/59): feat(usage): run rule detectors at session end and report with harness usage --rules
- AH-S020 [#60](https://github.com/JakeSelby/agent-harness/issues/60): feat(hooks): grade-bash gates irreversible and remote-mutating commands per the autonomy stance
- AH-C002 [#61](https://github.com/JakeSelby/agent-harness/issues/61): docs: positioning line, comparison rows and command-grade docs for 0.6.0
- AH-B002 [#67](https://github.com/JakeSelby/agent-harness/issues/67): fix(rules): the always-loaded delegation instruction contradicts the delegation "off" stance
- AH-S021 [#68](https://github.com/JakeSelby/agent-harness/issues/68): feat(stances): make the output voice a switch instead of an always-loaded rule
- AH-S022 [#71](https://github.com/JakeSelby/agent-harness/issues/71): feat(templates): commit-msg hook that enforces Conventional Commits instead of asking for them
- AH-S023 [#72](https://github.com/JakeSelby/agent-harness/issues/72): feat(hooks): brief-guard appends the return bound to a subagent spawn instead of asking for one
- AH-S024 [#73](https://github.com/JakeSelby/agent-harness/issues/73): feat(skill): code-quality instruments, so the testing stance measures quality instead of only asking for tests
- AH-B003 [#80](https://github.com/JakeSelby/agent-harness/issues/80): fix(installer): install crashes when gh or npm is missing
- AH-S025 [#81](https://github.com/JakeSelby/agent-harness/issues/81): feat(installer): configuring the harness requires hand-edited JSON, and placeholders ship silently
- AH-C003 [#82](https://github.com/JakeSelby/agent-harness/issues/82): docs: no prerequisites, no platform statement, and no first session
- AH-S026 [#83](https://github.com/JakeSelby/agent-harness/issues/83): feat(stances): the harness assumes its user is a professional software engineer
- AH-B004 [#84](https://github.com/JakeSelby/agent-harness/issues/84): fix(commands): four of five commands are dead outside a git repository
- AH-B005 [#85](https://github.com/JakeSelby/agent-harness/issues/85): fix(hooks): hooks fail silently without python3, and their messages are unreadable

### Issues #121 to #300 (43)

- AH-S059 [#152](https://github.com/JakeSelby/agent-harness/issues/152): Support private personal writing profiles
- AH-S060 [#153](https://github.com/JakeSelby/agent-harness/issues/153): Require one dedicated issue per pull request
- AH-C008 [#220](https://github.com/JakeSelby/agent-harness/issues/220): Release v0.10.0 with architecture-viewer preview support
- AH-S099 [#222](https://github.com/JakeSelby/agent-harness/issues/222): Add deterministic v1 lifecycle acceptance runner
- AH-C009 [#224](https://github.com/JakeSelby/agent-harness/issues/224): Freeze the v0.10.0 candidate and preview support contract
- AH-C010 [#225](https://github.com/JakeSelby/agent-harness/issues/225): Qualify the frozen v0.10.0 CLI and lifecycle matrix
- AH-C011 [#226](https://github.com/JakeSelby/agent-harness/issues/226): Publish v0.10.0 and update the public surfaces
- AH-S100 [#230](https://github.com/JakeSelby/agent-harness/issues/230): Establish user-owned, stance-driven product positioning
- AH-B010 [#231](https://github.com/JakeSelby/agent-harness/issues/231): Let the suite pass when runtime source changes after a release
- AH-S101 [#233](https://github.com/JakeSelby/agent-harness/issues/233): Bind judgment roles to capability classes instead of the session model
- AH-S102 [#235](https://github.com/JakeSelby/agent-harness/issues/235): Carry the spawn hook's top-tier fallback and notices through the lifecycle coordinator
- AH-B011 [#237](https://github.com/JakeSelby/agent-harness/issues/237): Stop reporting a managed link as redirected when it reaches its file through an alias
- AH-S103 [#239](https://github.com/JakeSelby/agent-harness/issues/239): Always-on Remote Control servers per configured folder
- AH-S104 [#241](https://github.com/JakeSelby/agent-harness/issues/241): Count subagent and worker tokens, and report usage per role
- AH-S105 [#243](https://github.com/JakeSelby/agent-harness/issues/243): Resolve stances and the model ladder in one place
- AH-S106 [#245](https://github.com/JakeSelby/agent-harness/issues/245): Add a designer role that declares the frontier class
- AH-S107 [#247](https://github.com/JakeSelby/agent-harness/issues/247): Give cost variants a machine-readable table with extends and fixed roles
- AH-S108 [#249](https://github.com/JakeSelby/agent-harness/issues/249): Render native agent definitions from the selected cost variant at sync
- AH-S109 [#251](https://github.com/JakeSelby/agent-harness/issues/251): Route unnamed spawns to band workers that carry the posture's effort
- AH-S110 [#253](https://github.com/JakeSelby/agent-harness/issues/253): State the soft budget in every subagent brief
- AH-S111 [#255](https://github.com/JakeSelby/agent-harness/issues/255): Feed turn and subagent spend back to the orchestrating session
- AH-B012 [#257](https://github.com/JakeSelby/agent-harness/issues/257): Route unnamed spawns only to workers the running session can resolve
- AH-C012 [#259](https://github.com/JakeSelby/agent-harness/issues/259): Document the cost posture layer and the delegation strategy
- AH-B013 [#261](https://github.com/JakeSelby/agent-harness/issues/261): Report a synchronous subagent's full spend in the usage feed
- AH-B014 [#262](https://github.com/JakeSelby/agent-harness/issues/262): Record one model name per subagent in usage rows
- AH-S112 [#263](https://github.com/JakeSelby/agent-harness/issues/263): Let a long-running session start routing once it can resolve the band workers
- AH-C013 [#264](https://github.com/JakeSelby/agent-harness/issues/264): Show a subagent spawn before and after the cost posture layer
- AH-C014 [#266](https://github.com/JakeSelby/agent-harness/issues/266): Open the 0.11.0 release candidate
- AH-C015 [#268](https://github.com/JakeSelby/agent-harness/issues/268): Qualify the frozen 0.11.0 matrix, including the cost posture layer
- AH-S113 [#269](https://github.com/JakeSelby/agent-harness/issues/269): Require a cost posture case for native qualification
- AH-B015 [#272](https://github.com/JakeSelby/agent-harness/issues/272): Sum a finished subagent's spend when it is reported, not the instant it stops
- AH-S114 [#273](https://github.com/JakeSelby/agent-harness/issues/273): Add a native acceptance runner with the cost posture case
- AH-B016 [#277](https://github.com/JakeSelby/agent-harness/issues/277): Make isolated role workers follow the selected cost variant
- AH-B017 [#278](https://github.com/JakeSelby/agent-harness/issues/278): Write the approval-reviewer key Codex actually accepts
- AH-C016 [#279](https://github.com/JakeSelby/agent-harness/issues/279): Tidy uninstall leftovers, the stale-save traceback and doctor's Codex lines
- AH-B018 [#282](https://github.com/JakeSelby/agent-harness/issues/282): Disposable homes raise macOS keychain dialogs: the native acceptance runner and the test suite
- AH-S115 [#284](https://github.com/JakeSelby/agent-harness/issues/284): Checks that bind a session must bind its subagents
- AH-B019 [#285](https://github.com/JakeSelby/agent-harness/issues/285): Codex rejects the PreToolUse hook's allow decision
- AH-B020 [#286](https://github.com/JakeSelby/agent-harness/issues/286): A role worker whose process died stays running
- AH-B021 [#291](https://github.com/JakeSelby/agent-harness/issues/291): BMad review confinement is requested in a prompt, not enforced at the spawn hook
- AH-C017 [#296](https://github.com/JakeSelby/agent-harness/issues/296): Publish v0.11.0 release metadata
- AH-C018 [#298](https://github.com/JakeSelby/agent-harness/issues/298): Let development resume after the v0.11.0 release
- AH-B025 [#300](https://github.com/JakeSelby/agent-harness/issues/300): A refused constrained-role spawn can be re-issued unnamed and run unconfined

### Issues #301 to #360 (38)

- AH-C019 [#302](https://github.com/JakeSelby/agent-harness/issues/302): Name every release surface in the repository instructions
- AH-B026 [#304](https://github.com/JakeSelby/agent-harness/issues/304): Read-only roles are refused as native spawns while builder runs natively
- AH-C020 [#305](https://github.com/JakeSelby/agent-harness/issues/305): Builder report: name the generator of an edited fixture, and report the command's own exit code
- AH-B027 [#306](https://github.com/JakeSelby/agent-harness/issues/306): usage-feed: a line that never clears, silent resumed agents, and an unlabelled figure
- AH-C021 [#307](https://github.com/JakeSelby/agent-harness/issues/307): The delegation off stance says the hook asks; the engine denies
- AH-B028 [#308](https://github.com/JakeSelby/agent-harness/issues/308): Give every disposable macOS home a keychain, not only the acceptance runner's
- AH-B029 [#309](https://github.com/JakeSelby/agent-harness/issues/309): Native acceptance runner: refusal read as a block, empty orchestrator text, missing runbook, AWS session variables
- AH-B030 [#310](https://github.com/JakeSelby/agent-harness/issues/310): Plan mode prompts for investigative commands the read-only grammar cannot prove
- AH-C022 [#313](https://github.com/JakeSelby/agent-harness/issues/313): Open the 0.11.1 candidate
- AH-B031 [#315](https://github.com/JakeSelby/agent-harness/issues/315): Review Card diagrams must render in plan mode
- AH-S116 [#318](https://github.com/JakeSelby/agent-harness/issues/318): Hold the landing copy as validated data in product.json, and lead the README with it
- AH-S117 [#319](https://github.com/JakeSelby/agent-harness/issues/319): Add a /land workflow: merge, clean up, then check whether a release is due
- AH-S118 [#320](https://github.com/JakeSelby/agent-harness/issues/320): Codify issue, milestone and release cadence rules, and check About and On-the-way at preflight
- AH-S119 [#321](https://github.com/JakeSelby/agent-harness/issues/321): usage-feed: tell the orchestrator when its session has grown past the posture's fresh-session threshold
- AH-S120 [#322](https://github.com/JakeSelby/agent-harness/issues/322): Nudge a running subagent about its budget, not only in its brief
- AH-B032 [#323](https://github.com/JakeSelby/agent-harness/issues/323): usage-log: Codex subagent threads are recorded as sessions
- AH-B033 [#324](https://github.com/JakeSelby/agent-harness/issues/324): brief-without-cap rate did not move after brief-guard shipped
- AH-B034 [#325](https://github.com/JakeSelby/agent-harness/issues/325): Isolated gatherer worker has no web tools
- AH-S121 [#331](https://github.com/JakeSelby/agent-harness/issues/331): usage: rows lack harness version, session effort and per-day slices; no token view by stance
- AH-S122 [#332](https://github.com/JakeSelby/agent-harness/issues/332): Freeze qualification on a release branch, and fix no defect mid-round
- AH-S123 [#333](https://github.com/JakeSelby/agent-harness/issues/333): Scope evidence invalidation by target, then decide on scoping it by case
- AH-S124 [#334](https://github.com/JakeSelby/agent-harness/issues/334): Add a pre-qualification smoke tier that spends no model turns
  - AH-S143 [#401](https://github.com/JakeSelby/agent-harness/issues/401): Land the documentation-link check and the credential probe from the smoke tier
- AH-S125 [#335](https://github.com/JakeSelby/agent-harness/issues/335): Cut the cost of the bmad-workflow qualification case
- AH-S126 [#336](https://github.com/JakeSelby/agent-harness/issues/336): Commit the qualification case scripts, drive Codex in the runner, automate all eleven cases
- AH-S127 [#337](https://github.com/JakeSelby/agent-harness/issues/337): Stop sequential CI waits and changelog conflicts: merge queue and changelog fragments
- AH-S128 [#338](https://github.com/JakeSelby/agent-harness/issues/338): Run scripted qualification cases on a cheaper orchestrator tier
- AH-S129 [#339](https://github.com/JakeSelby/agent-harness/issues/339): Write qualification evidence incrementally so a killed round costs one case
- AH-S130 [#340](https://github.com/JakeSelby/agent-harness/issues/340): Make a release cost one qualification round
- AH-S131 [#341](https://github.com/JakeSelby/agent-harness/issues/341): Keep a stable branch at the latest release
- AH-S132 [#344](https://github.com/JakeSelby/agent-harness/issues/344): usage: report dollars from a dated, overridable price table
- AH-C023 [#345](https://github.com/JakeSelby/agent-harness/issues/345): Qualify the frozen 0.11.1 matrix
- AH-C024 [#346](https://github.com/JakeSelby/agent-harness/issues/346): Publish v0.11.1 release metadata
- AH-S133 [#349](https://github.com/JakeSelby/agent-harness/issues/349): Decouple the harness from BMad: generic layering, BMad as an optional integration test
- AH-C025 [#350](https://github.com/JakeSelby/agent-harness/issues/350): Point the README install command at the stable branch
- AH-S134 [#353](https://github.com/JakeSelby/agent-harness/issues/353): Optional OTLP export of ledger rows, with replay
- AH-S135 [#355](https://github.com/JakeSelby/agent-harness/issues/355): sync: opt-in native OpenTelemetry for Claude Code and Codex, stamped with harness labels
- AH-B035 [#358](https://github.com/JakeSelby/agent-harness/issues/358): Exported ledger rows carry no dollar figure, and stances arrive twice
- AH-C026 [#359](https://github.com/JakeSelby/agent-harness/issues/359): docs: a ClickStack reference recipe for telemetry, and the fan-out claim in usage.md

### Issues #361 to #420 (23)

- AH-S136 [#361](https://github.com/JakeSelby/agent-harness/issues/361): Static context-budget check per release
- AH-B036 [#363](https://github.com/JakeSelby/agent-harness/issues/363): /land cannot finish its cleanup after a squash merge: dirty worktree, blocked cache delete, refused branch delete
- AH-B037 [#365](https://github.com/JakeSelby/agent-harness/issues/365): telemetry: the de-duplication example orders by a column the ClickHouse OTel schema does not have
- AH-S137 [#367](https://github.com/JakeSelby/agent-harness/issues/367): Add the live cost replay runner and its task manifest
- AH-S139 [#370](https://github.com/JakeSelby/agent-harness/issues/370): Log hook decisions and their observed outcomes
- AH-SP004 [#371](https://github.com/JakeSelby/agent-harness/issues/371): Jev: Count the labelled decisions existing sessions already hold
- AH-B038 [#380](https://github.com/JakeSelby/agent-harness/issues/380): test_bmad_repository counts personal *.user.toml overrides as team workflows
- AH-S144 [#386](https://github.com/JakeSelby/agent-harness/issues/386): Sample allowed commands into the decision log
- AH-S145 [#387](https://github.com/JakeSelby/agent-harness/issues/387): Record the completion claim on stop-gate decision rows
- AH-C028 [#390](https://github.com/JakeSelby/agent-harness/issues/390): Add the decision log to the landing copy
- AH-C029 [#391](https://github.com/JakeSelby/agent-harness/issues/391): Reserve BMad IDs for the issues filed after the #376 backfill
- AH-B041 [#394](https://github.com/JakeSelby/agent-harness/issues/394): sync installs the Scannable output style whatever the voice stance says
- AH-B045 [#399](https://github.com/JakeSelby/agent-harness/issues/399): Replay snapshots lose the git history the gate reads, so both arms pay for a red fixture
- AH-S146 [#404](https://github.com/JakeSelby/agent-harness/issues/404): Write the permission-controls qualification driver the classification now supports
- AH-B046 [#405](https://github.com/JakeSelby/agent-harness/issues/405): Let telemetry.native name the runtimes it applies to
- AH-B048 [#407](https://github.com/JakeSelby/agent-harness/issues/407): usage prints a partial-totals line above a report that already counts those runs
- AH-S147 [#408](https://github.com/JakeSelby/agent-harness/issues/408): Write down how Codex and Linux qualification targets are provisioned and where they run
- AH-D003 [#409](https://github.com/JakeSelby/agent-harness/issues/409): Decide whether the credential probe names the variable it found
- AH-C031 [#414](https://github.com/JakeSelby/agent-harness/issues/414): Make the landing-copy check required on main
- AH-S148 [#415](https://github.com/JakeSelby/agent-harness/issues/415): Report cache-prefix stability as a first-class figure in the usage ledger and the replay benchmark
- AH-S149 [#416](https://github.com/JakeSelby/agent-harness/issues/416): Measure whether subagent returns carry a path instead of the payload
- AH-D004 [#417](https://github.com/JakeSelby/agent-harness/issues/417): Decide whether repeated agent pairs get an amortized return schema, and whether fan-outs get a shared workspace
- AH-C030 [#418](https://github.com/JakeSelby/agent-harness/issues/418): Reserve BMad IDs for the traceability-gap issues

### Issues #421 onward (32)

- AH-C032 [#422](https://github.com/JakeSelby/agent-harness/issues/422): Reserve BMad IDs for the traceability close-out issues
- AH-C033 [#424](https://github.com/JakeSelby/agent-harness/issues/424): Reserve BMad IDs for the comms-gap issues filed into v0.12.0
- AH-S150 [#426](https://github.com/JakeSelby/agent-harness/issues/426): Add a /close-out workflow for ending a finished session
- AH-T006 [#431](https://github.com/JakeSelby/agent-harness/issues/431): Replay task set sits below the harness's break-even size
- AH-C034 [#432](https://github.com/JakeSelby/agent-harness/issues/432): Ignore .agent-harness/evidence/ repository-wide so captured transcripts cannot redden another session's gate
- AH-B051 [#436](https://github.com/JakeSelby/agent-harness/issues/436): A plan file reaches no native plan view, so the reviewer is asked to approve a document they cannot open
- AH-T001 [#437](https://github.com/JakeSelby/agent-harness/issues/437): Give each replay arm its own profile, and attribute history rows to a bucket
- AH-S195 [#439](https://github.com/JakeSelby/agent-harness/issues/439): Let plan mode own the plan review surface, so the native pane and the native approval replace the typed build gate
- AH-B059 [#440](https://github.com/JakeSelby/agent-harness/issues/440): reserve hands out a BMad ID another worktree already took, because it trusts a per-checkout counter
- AH-S151 [#445](https://github.com/JakeSelby/agent-harness/issues/445): Make the repository findable: widen About topics, reword the description, add a plugin manifest and the mark
- AH-T002 [#480](https://github.com/JakeSelby/agent-harness/issues/480): Record the fields a cost-replay regression needs to be diagnosed from the ledger alone
- AH-B053 [#483](https://github.com/JakeSelby/agent-harness/issues/483): Remote Control host gives up after 10 min offline, kills every session, and the launchd relaunch does not re-adopt them
- AH-T003 [#487](https://github.com/JakeSelby/agent-harness/issues/487): Let the replay fence admit each arm's own profile directory, and prove the gate passes under it before spending
- AH-S196 [#497](https://github.com/JakeSelby/agent-harness/issues/497): Record the cache-prefix figure per replay run beside normalised_cost
- AH-B052 [#498](https://github.com/JakeSelby/agent-harness/issues/498): claude_dir() lets CLAUDE_CONFIG_DIR override a set HARNESS_HOME, so the test suite writes into the real profile
- AH-T004 [#499](https://github.com/JakeSelby/agent-harness/issues/499): Make the replay tasks' success bar profile-independent, and pre-flight that bar
- AH-C035 [#501](https://github.com/JakeSelby/agent-harness/issues/501): Record how a pull request is landed under the main ruleset
- AH-T005 [#507](https://github.com/JakeSelby/agent-harness/issues/507): State the live benchmark tier's status in docs/benchmarks.md before 0.12
- AH-B098 [#531](https://github.com/JakeSelby/agent-harness/issues/531): grade-bash timing test fails on wall clock under Python 3.9 on a loaded machine
- AH-C042 [#532](https://github.com/JakeSelby/agent-harness/issues/532): Open the 0.12.0 candidate
- AH-C044 [#534](https://github.com/JakeSelby/agent-harness/issues/534): Publish 0.12.0 with a narrowed support contract
- AH-C047 [#550](https://github.com/JakeSelby/agent-harness/issues/550): Reserve BMad IDs for the three unmapped v0.13.0 issues
- AH-S205 [#588](https://github.com/JakeSelby/agent-harness/issues/588): Trim the output style and the harness descriptions (#430 changes 2 and 3)
- AH-T007 [#599](https://github.com/JakeSelby/agent-harness/issues/599): Replay a pinned older tag into a temporary config directory, not only the installed candidate
- AH-B060 [#603](https://github.com/JakeSelby/agent-harness/issues/603): fix(remote-control): hosts never reuse their environment, so restarts archive sessions
- AH-B061 [#605](https://github.com/JakeSelby/agent-harness/issues/605): fix(lint): skip untracked files under .agent-harness/ so local plans cannot redden the gate
- AH-B062 [#606](https://github.com/JakeSelby/agent-harness/issues/606): test(codex): make the no-client detection test hermetic against an installed client
- AH-T008 [#607](https://github.com/JakeSelby/agent-harness/issues/607): Rule: recognize "spawn a new chat" as a cloud-run request
- AH-C051 [#625](https://github.com/JakeSelby/agent-harness/issues/625): docs(changelog): restore five Unreleased entries the #598 merge dropped
- AH-B066 [#649](https://github.com/JakeSelby/agent-harness/issues/649): Replay counts the CLI's synced skill packs as sync leftovers and stops after the first tag
- AH-S218 [#672](https://github.com/JakeSelby/agent-harness/issues/672): Pass CLAUDE_CODE_OAUTH_TOKEN into the disposable home so a subscription can run a Claude Code round
- AH-C064 [#700](https://github.com/JakeSelby/agent-harness/issues/700): 0.13.0 requires only the two Claude Code CLI targets; Codex CLI defers to 0.14.0 per FR-12

# Epic 24 Context: Run from the Studio

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Let a developer launch any cataloged suite or single case from the local Studio against an explicit target, watch and cancel it without losing completed evidence, and retain authoritative results with target and spend provenance. Free local checks must remain one-click and model-free; usage-spending work adds an estimate, confirmation and cap before launch.

## Stories

- AH-S297: Suite catalog and run supervisor, with `citizen runs`
- AH-S298: Run store and imported benchmark and acceptance history
- AH-S299: Launch, watch and stop the free local suites down to one unit test
- AH-S300: Run history and detail with flaky-test marks
- AH-S301: Targets for installed versions, releases, branches, worktrees and drafts
- AH-S302: Spend estimate, confirmation and cap
- AH-S303: Native acceptance case runs
- AH-S304: Two-target live replay benchmark
- AH-S305: Evaluation tiers and unit evaluations
- AH-S306: Four-arm bench, layer swaps and factorial screens
- AH-S319: Import native plugin evaluation reports

## Requirements & Constraints

- Every launch comes from a versioned allowlist; user input may fill declared argument positions but may never choose an executable or invoke a shell.
- A suite can run whole or at one cataloged case. The run exposes live logs, semantic completed/eligible progress, cancellation and a kept record.
- Free local runs display `No model usage` and start without a spend confirmation. Usage-spending runs cannot start until the exact estimate and caps are confirmed.
- Native result files remain authoritative. Every Studio launch also appends schema-versioned lifecycle and case evidence under a stable run UUID; the rebuildable index cannot replace or silently rewrite that evidence.
- Completed cases survive cancellation, limits and caps. Missing, partial, failed and unknown evidence stay distinct.
- Run controls remain usable by keyboard and at phone width. Progress is either determinate with visible counts or explicitly indeterminate. Log lines are selectable and are not announced as a live region.

## Technical Decisions

- CLI and Studio are two transports over one core command/query/result contract. Every domain route names and displays its exact `citizen` equivalent; route handlers do not reimplement run logic.
- The Python 3.9 standard-library loopback server owns JSON request/response and bounded SSE. One mutation executor serializes run lifecycle changes; request threads do not mutate shared state directly.
- The supervisor owns at most three concurrent process groups, queues the rest FIFO, launches with no shell in an isolated profile, records PID start identity and terminates the exact owned group on cancel or timeout.
- One recoverable Studio instance owns the run supervisor and reconciles interrupted work on restart. Files and append-only sidecars are authoritative; SQLite is only a rebuildable index.
- The committed React and Mantine bundle is built from its exact lockfile and must reproduce byte for byte in CI. Node remains build-time only.

## UX & Interaction Patterns

Experiments holds the suite catalog, target choice and launch guard; Run detail holds semantic progress, selectable logs, Pause/Resume and Cancel. The run console always shows suite or case, target, isolated profile, exact command, cost class and current lifecycle state. Pausing freezes visible updates while recording continues; Resume states how many updates were queued. Cancel preserves finished evidence and announces once.

## Cross-Story Dependencies

AH-S299 consumes the catalog and crash-recoverable supervisor from AH-S297 and the authoritative sidecar/index from AH-S298. Later target, spend, history, native acceptance and comparison stories extend the same run identity and transport rather than defining parallel launch paths.

---
bmad_id: "AH-SP017"
type: "spike"
title: "Spike: can the committed UI bundle be rebuilt byte for byte on Linux CI?"
lifecycle: "active"
provenance: "authored"
github_issue: 963
github_issue_url: "https://github.com/JakeSelby/model-citizen/issues/963"
parent_bmad_id: "AH-E022"
parent_github_issue: 955
updated: "2026-09-26"
---

# AH-SP017 — Spike: can the committed UI bundle be rebuilt byte for byte on Linux CI?

<!-- bmad-sync:begin -->
- **GitHub issue:** [#963](https://github.com/JakeSelby/model-citizen/issues/963)
- **Primary parent:** [AH-E022](https://github.com/JakeSelby/model-citizen/issues/955)
- **State:** active

The issue carries the summary, discussion and acceptance evidence; this file carries the design.

This work item was authored as part of the repository's committed BMad planning system.
<!-- bmad-sync:end -->

## Question

Does `npm ci && npm run build` for a React, TypeScript and Vite app produce a byte-identical bundle on a developer's macOS machine and on the Linux CI runner, from the same lockfile and Node version? The committed-bundle decision depends on CI proving that the bundle matches its source.

## Experiment

A scratch app on the candidate stack (React, TypeScript, Vite, a router, a query library, CodeMirror 6 and a chart library), pinned by lockfile and one Node version file. Build three times on macOS arm64 and three times on `ubuntu-latest`, and compare the SHA-256 of every emitted file.

## Exit criterion

All six builds compared within half a working day, with the digests and every differing file recorded.

Decision rule: identical, so CI rebuilds and fails on a digest mismatch; differing only in non-determinism a build setting removes, so set it and retest; still differing, so the release workflow builds and commits the bundle at tag time, and pull requests check only that the source builds.

## Result

<!-- fill: the measured numbers, where the raw evidence lives, and which side of the threshold they fall. -->

## Decision

<!-- fill: what the result decides, and the follow-up work it opens or closes. -->

## Dev notes

Traces to FR-75.

## Change log

- 2026-09-26: Filed by #960 with the Studio epics. The sections still marked to fill are written when the work starts.

---
bmad_id: "AH-SP017"
type: "spike"
title: "Spike: can the committed UI bundle be rebuilt byte for byte on Linux CI?"
lifecycle: "done"
provenance: "authored"
github_issue: 963
github_issue_url: "https://github.com/JakeSelby/model-citizen/issues/963"
parent_bmad_id: "AH-E022"
parent_github_issue: 955
updated: "2026-09-27"
---

# AH-SP017 — Spike: can the committed UI bundle be rebuilt byte for byte on Linux CI?

<!-- bmad-sync:begin -->
- **GitHub issue:** [#963](https://github.com/JakeSelby/model-citizen/issues/963)
- **Primary parent:** [AH-E022](https://github.com/JakeSelby/model-citizen/issues/955)
- **State:** done

The issue carries the summary, discussion and acceptance evidence; this file carries the design.

This work item was authored as part of the repository's committed BMad planning system.
<!-- bmad-sync:end -->

## Question

Does `npm ci && npm run build` for a React, TypeScript and Vite app produce a byte-identical bundle on a developer's macOS machine and on the Linux CI runner, from the same lockfile and Node version? The committed-bundle decision depends on CI proving that the bundle matches its source.

## Experiment

A scratch app genuinely imports the exact approved React, router, query, CodeMirror, chart and
Mantine packages. The lockfile pins every direct and transitive package, `.node-version` pins Node
22.22.3, and package metadata pins npm 10&#46;9&#46;8. Vite 8.3.1 was rejected before incorporation
because its exact resolution contained MPL-2.0 packages; Vite 7.3.6 and plugin-react 5.1.4 replaced
only that build-tool pair.

The experiment archives the source once and extracts it into three separate paths. Each run uses a
unique npm cache and executes unmodified `npm ci` and `npm run build` with `TZ=UTC`, `LANG=C`,
`LC_ALL=C`, and `CI=1`. The manifest tool rejects symlinks and escaping paths, sorts emitted paths,
hashes every regular file with SHA-256, and reports missing, extra and differing files. Each run
enforces and records its actual Node/npm versions and OS/architecture separately from file equality.

## Exit criterion

All six builds compared within half a working day, with the digests and every differing file recorded.

Decision rule: identical, so CI rebuilds and fails on a digest mismatch; differing only in non-determinism a build setting removes, so set it and retest; still differing, so the release workflow builds and commits the bundle at tag time, and pull requests check only that the source builds.

## Result

All six fresh builds emitted the same five files with identical paths, sizes and SHA-256 values:
three on macOS arm64 and three on GitHub's `ubuntu-latest` x64 runner. All nine cross-platform pairs
had zero missing, extra or differing files. Raw manifests and comparison reports are in
`../../studio/reproducibility/`; the workflow evidence is
[run 36328768988](https://github.com/JakeSelby/model-citizen/actions/runs/36328768988), and the
synthesized record is `../../docs/spikes/2026-09-27-studio-bundle-reproducibility.md`.

The final resolution contains 180 lockfile entries: 149 MIT, 22 Apache-2.0, five ISC, one
BSD-3-Clause, and one each of 0BSD, CC-BY-4.0 and `MIT OR CC0-1.0`. Recipient
notices and the full package inventory are mandatory repository licensing support artifacts copied
into the built bundle. The six-run exit criterion is met.

## Decision

Use the committed-source plus deterministic CI-rebuild design. CI rebuilds from the pinned lockfile
and toolchain, regenerates the legal evidence, and fails when any emitted file differs from the
committed bundle evidence. Release-time-only generation is unnecessary for this stack.

## Dev notes

Traces to FR-75.

- **Source layout:** `studio/` is an experiment fixture, not the shipped Studio implementation.
- **Evidence:** `scripts/studio_bundle_manifest.py` owns canonical manifests, comparisons, safe
  source extraction, isolated runs, and dependency inventory/notices. Unit tests cover stable sort,
  hash mismatches, missing/extra files, symlinks, path escapes, rejected licenses, absent notice
  text, and exact external-evidence hashes.
- **Generated records:** npm generated `studio/package-lock.json`; its one dependency engine string
  uses a semantic-preserving JSON Unicode escape so the repository's private-address lint does not
  misread a public npm version range. The notice generator embeds upstream license/notice bodies
  byte for byte and refuses packages without bundled text unless an exact committed evidence record
  supplies a hash-verified verbatim file, commit and URL. The only external record is for
  `react-remove-scroll-bar` 2.3.8: its package metadata and bundled README establish MIT, while the
  canonical upstream file supplies the omitted notice. Lint exempts only the email-address shape on
  the generated notice and exact evidence file; every other scanner still runs on both.
- **CI:** `.github/workflows/studio-bundle-repro.yml` triggers only on pushes to
  `studio-bundle-spike` and `feature/v0.18.0-studio`, runs three Ubuntu builds, publishes evidence
  with a full-SHA-pinned action, records source/lock/tree digests, and compares all three Linux
  manifests with all three committed macOS manifests. It also regenerates and byte-compares the
  licensing artifacts and runs the dependency audit before building.
- **Security:** the Vite 7.3.6 tree resolves the prior Vite/esbuild advisories; the pinned local run
  on 2026-09-27 observed zero advisories, and CI continuously checks that state.

## Dev agent record

- **Model:** Codex builder subagent.
- **File list:** `.node-version`, `.github/workflows/studio-bundle-repro.yml`, `studio/`,
  `scripts/studio_bundle_manifest.py`, `tests/test_studio_bundle_manifest.py`,
  `docs/spikes/2026-09-27-studio-bundle-reproducibility.md`, `THIRD_PARTY_NOTICES.md`,
  `changelog.d/963.changed.md`, and this story.
- **Linux evidence:** [run 36328768988](https://github.com/JakeSelby/model-citizen/actions/runs/36328768988)
  produced three identical Linux manifests and a clean nine-pair cross-platform comparison.

## Change log

- 2026-09-26: Filed by #960 with the Studio epics. The sections still marked to fill are written when the work starts.
- 2026-09-27: Implemented the pinned fixture, license inventory, deterministic manifest tool,
  three-run macOS evidence and push-only Linux/cross-platform evidence workflow.
- 2026-09-27: Run 36328768988 proved all three Linux builds and all nine cross-platform pairs
  byte-identical; selected deterministic CI rebuild and closed the spike.

# Spike: can the Studio bundle be rebuilt byte for byte on Linux CI?

> **Result: all six fresh builds are byte-identical.** Three macOS arm64 builds, three Linux x64
> builds and all nine cross-platform pairs emitted the same five files with the same SHA-256 values.

## Question

Does `npm ci && npm run build` produce the same browser bundle from the same exact lockfile and
Node version on repeated macOS arm64 builds and on `ubuntu-latest`?

## Candidate stack

The scratch app exercises React 19.3.0, React DOM 19.3.0, React Router DOM 7.18.4, TanStack React
Query 5.104.0, CodeMirror state 6.7.6, view 6.43.13 and JSON 6.0.2, Chart.js 4.5.1,
react-chartjs-2 5.3.1, and Mantine core/hooks 9.6.3. It builds with TypeScript 7.0.2, Vite 7.3.6
and plugin-react 5.1.4 on Node
22.22.3 and npm 10&#46;9&#46;8.

Vite 8.3.1 was rejected before incorporation because its exact dependency tree included
MPL-2.0-licensed `lightningcss`. The final tree resolves 180 lockfile entries: 149 MIT, 22
Apache-2.0, five ISC, one BSD-3-Clause, and one each of 0BSD, CC-BY-4.0 and `MIT OR CC0-1.0`.
The complete machine-readable inventory and recipient notices are required support artifacts under
the repository's licensing policy and ship in the bundle.

Every runtime package must contain its license text or match exact committed external evidence;
there is no synthesized-license fallback. `react-remove-scroll-bar` 2.3.8 declares MIT in package
metadata and its bundled README but omits the text, so its record pins the verbatim upstream file,
source commit, URL and SHA-256. Any missing, stale, mismatched or unused record fails generation.
The reviewed MIT tarballs are pinned at SHA-256
`f540d98468457ac7a0aabb32006dfb066297e096c5ea063a5d80aa973d1c337a` for Chart.js and
`703a563f1cece83ddffa12d1eef05676a1ddf05e2b61e2d941c5c70b9edc59df` for react-chartjs-2.

## Experiment

`scripts/studio_bundle_manifest.py experiment` archives the source once, rejects symlinks and path
escapes, extracts it into three separate paths, gives every run a unique npm cache, and executes
unmodified `npm ci` and `npm run build` under `TZ=UTC`, `LANG=C`, `LC_ALL=C`, and `CI=1`. It hashes
every emitted regular file by path and compares missing, extra and differing files. No build output
is post-processed. Before every build it requires the pinned Node and npm versions, then records the
actual versions and OS/architecture beside that run's file hashes; environment evidence is excluded
from file-tree equality.

The push-only `studio-bundle-repro` workflow repeats the three runs on `ubuntu-latest`, publishes
all manifests and the comparison report, records the source commit, lockfile digest and source-tree
digest in the job summary, compares every Linux manifest with every committed macOS manifest, then
fails if either the Linux builds or any cross-platform pair differ. Before building, it regenerates
the package inventory and verbatim upstream notices to temporary paths, byte-compares both with the
committed artifacts, and runs the dependency audit.

## Measured on macOS arm64

All three fresh builds emitted the same five paths and SHA-256 values:

- `index.html`: `5711fd68fb2eb0ece9a29f15c937860dd7a82693602456a456dc54236cbe803a`
- `assets/index-83nrFCSQ.js`: `b3716bd48b90459136480c2b1f4515a1ff218dbe13474f0aa877323d138666e6`
- `assets/index-DCw64SHH.css`: `49c1025d60906934c124225c6b35fcfe38f67ca14103d74825aa06d64171c79f`
- `third-party.json`: `249441b5c4b0351f9e01d59152c657a8b5eda611f6ffd34f7905e131ac42c82f`
- `THIRD_PARTY_NOTICES.txt`: `7cff08e4aef4e48a4c4e20c625731adf06d494ae6f9c7dc1479d555b30d7d1fe`

Raw manifests and the empty-difference report are under
`studio/reproducibility/macos-arm64/`. The pinned lockfile SHA-256 for this run is
`7185fa600ccb8ae30a93928adc5a9825a2bec5f2f95bfb1e360c5ca1da993b50`.

## Measured on Linux x64 and across platforms

[GitHub Actions run 36328768988](https://github.com/JakeSelby/model-citizen/actions/runs/36328768988)
completed successfully on `ubuntu-latest`. Its three isolated Linux builds matched one another, and
each matched each committed macOS manifest: nine comparisons, zero missing paths, zero extra paths
and zero differing hashes. The downloaded manifests and reports are committed under
`studio/reproducibility/linux-x64/` and `studio/reproducibility/cross-platform-comparison.json`.

## Decision

The build is repeatable on the measured macOS and Linux hosts. Use committed source and bundle
evidence with a deterministic CI rebuild that fails on any path, size or SHA-256 mismatch; the
release workflow does not need to generate the bundle only at tag time.

The exact Vite 7.3.6 build-tool tree reported zero advisories in `npm audit --audit-level=low` on
the pinned local run dated 2026-09-27; the Linux workflow rechecks that observed state on every run.

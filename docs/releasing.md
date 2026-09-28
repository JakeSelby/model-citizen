# Coordinate a harness release and its public surfaces

A release is a verified source tag and the GitHub release published from it. A successful build is
not native qualification. `scripts/release_preflight.py` refuses publication until every required
client in the compatibility catalog carries native evidence. Every release follows the
[compatibility policy](compatibility-policy.md); generated notes link it and state the migration
review or exact versioned migration action. The reference site at
[model-citizen.dev](https://model-citizen.dev) follows releases from its own
repository; nothing in this procedure deploys it.

## When a release is proposed, and what it is numbered

Releases are cut by milestone. Every issue meant for the next release carries the `v<next>`
milestone, work merges freely, and a release is proposed when that milestone empties or when a
user-visible unreleased change is seven days old, whichever comes first. A regression fix does not
wait for either: it releases at once as a patch.

The number follows what changed, not where the entry landed in the changelog. Fixes only is a
patch. Added or changed user-visible behaviour is a minor. A major is decided by the
[compatibility policy](compatibility-policy.md), which also defines what counts as a breaking
change. What the release must carry before it can be tagged is the next section.

## Source and qualification

### Carrying native evidence through a presentation-only patch

A released patch may carry the preceding release's native evidence only when
`compatibility/catalog.json` declares `qualification_reuse`. The declaration names the prior
version, its immutable `v<version>` tag, the actual `evidence_version`, the exact
`qualification_source_commit`, whether the chain includes the v0.14.1 bootstrap exception, and the
validator's exact kind-specific no-rerun limitation. The candidate keeps every prior client, case,
evidence path and digest, support state, invalidation claim and limitation byte-for-byte, then
appends that one disclosure. Evidence files are never rewritten to name the patch.

Preflight resolves `refs/tags/v<prior>^{commit}` once, uses that pinned commit for every read and
comparison, proves it is an ancestor of the candidate, and requires a later patch in the same
release line. Ordinary carry-forward permits changes only to `VERSION`, `README.md`, `CHANGELOG.md`,
`product.json`, the three release records `compatibility/catalog.json`,
`compatibility/freeze.json` and `compatibility/migration.json`, and files under `docs/`,
`changelog.d/` and `_bmad-output/`. It classifies the whole release delta, so an installer,
workflow, script or other executable change fails even when qualified runtime paths are unchanged.
Any unavailable ref, changed claim, different digest, malformed limitation, unknown catalog field
or ineligible path fails closed. When the prior release itself carried older evidence, preflight
validates that earlier reuse, retains the version the evidence actually observed and carries the
bootstrap disclosure through the chain. Release notes and preflight identify the evidence as
carried rather than newly produced. After publication, validation uses the immutable current tag;
later runtime edits remain readable as release drift and require new evidence.

The `v0.14.1-bootstrap` kind is a one-release exception approved on 2026-09-27. In addition to the
ordinary paths above, it permits only `lib/harness_core/compatibility.py`,
`scripts/release_preflight.py`, `scripts/release_notes.py`, and their three existing test modules.
It reuses v0.14.0 evidence for unchanged client-facing behavior; it does not call v0.14.1
byte-identical or newly qualified, and it is invalid for every other version. Roll back a failed
attempt by removing the reuse declaration and disclosure and leaving the previous tag and evidence
untouched; do not move or replace either tag.

1. Complete each native acceptance case in [compatibility](compatibility.md). Keep exact runtime,
   client and platform versions, source commit, observations and evidence digests. Resolve failed
   controls or record a deliberately narrower support contract before calling a client qualified.
   `permission-controls` gained a driver after 0.12.0 and has not run live yet: on the first round
   that runs it, qualify the target by hand as well and compare the two results before the
   automated verdict is trusted. Record that comparison with the round's observations, and replace
   the recorded decline in `tests/fixtures/permission-controls/` if the round produces a real one.
   Every other case gained a driver at the same time, and `scripts/qualification_provision.py`
   and `scripts/qualification_round.py` provision and drive the round; the
   [runbook](qualification-runbook.md) is the mechanics. The same hand comparison is owed once
   per case on its first live round. A Codex target reports `unverified` whatever it observed
   until `--home-confirmed` says its configuration home was compared against a hand run.
   For v0.14.0 only, the owner approved qualifying the Claude Code CLI on macOS and Linux from
   complete scripted records while deferring the independent hand comparisons to v0.15.0; the
   catalog records the resulting case-specific limitations. This exception applies to no other
   release, runtime or client surface.
   The scripted cases are executed at the `standard` capability class and their observations are
   assessed at `strong`; both classes are recorded in the evidence record and in the round
   record, and a round whose cheap executor would be the only reader of its own evidence is
   refused rather than run. The classes, the per-target overrides and the refusals are in the
   [runbook](qualification-runbook.md). The expected saving is #338's estimate rather than a
   measurement: 230K–590K output tokens per round, most of it authoring rather than judgement.
2. On a minor release, run the optional BMad integration suite once, on one target, before the
   tag: the native four-layer review described in [BMad](bmad.md). It gates nothing — a red result
   is an issue to file, not a blocked release — and its outcome is recorded in the release pull
   request with the harness version, the framework version and the client it ran on. A patch
   release skips it. No framework workflow is a `required_cases` entry.
3. Merge reviewed changes through the repository's PR gate. Keep stacked PR bases current without
   overwriting other contributors' history. Preserve personal configuration and the live checkout.
4. Set `VERSION`, `compatibility/catalog.json` and `compatibility/migration.json` to the same
   release. Record exact migration actions and recovery even when the only action is reviewing a
   dry run. Regenerate projections and release notes. Fold the changelog into the version section:
   for 0.13.0, by hand from `## [Unreleased]` as before, and that release pull request is the
   cut-over that ends the lint's Unreleased exemption; from the next release on, with
   `python3 scripts/release_notes.py --changelog <version>`, which assembles the `changelog.d/`
   fragments in a stable order and deletes them (see
   [`changelog.d/README.md`](../changelog.d/README.md)). That assembly satisfies the lint's
   fragment rule, so the release pull request adds no `.none.md` waiver. Run the CI commands and the Python floor
   suite on committed HEAD; then run:

   ```sh
   python3 scripts/release_preflight.py
   python3 scripts/release_notes.py
   ```

   The preflight also compares the GitHub About panel with `product.json` and looks up every
   `on_the_way` entry that names an issue; an entry whose issue has closed blocks the release
   until it is promoted or removed. Both read GitHub through `gh`, so both run only behind a
   `gh auth status` probe. Without an authenticated `gh` the preflight prints
   `release warning: About and On-the-way checks skipped, gh is not authenticated` and does not
   fail, which is what happens in the tag workflow: its preflight step is passed no token by
   design, so those two checks are expected to warn there and must be run locally before tagging.
   A `gh` call that fails after the probe passed is a blocked release, not a skip.

   Refresh the static context figure for the new version with
   `python3 scripts/cost_bench.py static --write` and commit it; see [benchmarks](benchmarks.md).

   Before freezing the candidate, run `python3 scripts/lifecycle_acceptance.py` under every Python
   and reference operating system named by the candidate record. The runner uses disposable homes,
   verifies the immutable v0.9.0 archive pin, and covers clean install, repeated sync, upgrade,
   rollback, conflicts and uninstall. Store its JSON output with the candidate evidence. This is a
   filesystem/configuration lifecycle check; it does not qualify a native client.

   Run `python3 scripts/studio_lifecycle_acceptance.py --output
   compatibility/evidence/studio-<platform>-<version>.json` on macOS and Linux at the frozen source
   commit. Commit both records. Each must name current stable Chrome and pass every lifecycle,
   security and browser-flow case; the qualification round's smoke tier runs the same command and
   stops the round before any paid target when it fails. Release preflight also rebuilds `studio/`
   from the exact lockfile and refuses any path, size or digest that differs from `studio/dist/`.

5. Tag the verified commit with the matching immutable `v<version>` tag and push that tag.
   The release workflow repeats qualification and source gates before publishing. Never move an
   existing tag to repair a failed release; fix the source and use a new version.
6. The workflow's `advance-stable` job, which cannot fail the run, fast-forwards the `stable`
   branch to the tag's commit, so `stable` is always the latest release while `main` is the trunk.
   Nothing else pushes to `stable`, and it never moves backward. Confirm it, and repair it from a
   checkout that has the tag if it is stale:

   ```sh
   python3 scripts/advance_stable.py --check
   python3 scripts/advance_stable.py
   ```

   GitHub refuses a workflow token that moves a branch across a change to `.github/workflows/`,
   so a release that edits a workflow can need the second command run by hand.
7. Close the released milestone and open the next one. This is a hand-run step rather than a
   workflow job, so that a published release never depends on it. Read the milestone numbers,
   close the released one, and create the next if it does not exist:

   ```sh
   gh api repos/{owner}/{repo}/milestones \
     --jq '.[] | "\(.number) \(.title) open:\(.open_issues)"'
   gh api -X PATCH repos/{owner}/{repo}/milestones/<number> -f state=closed
   gh api repos/{owner}/{repo}/milestones -f title=v<next> -f state=open
   ```

   Move any issue still open on the closed milestone to the new one first, so the closed
   milestone records what the release actually carried.

## Freeze the qualification branch

Cut `release/v<version>` at the commit the round qualifies and record it in
`compatibility/freeze.json` as `state: frozen` with that branch and full commit, then run every
target on that branch so `main` keeps merging without invalidating evidence. `citizen freeze`
prints the drift between the frozen commit and `origin/main` under the runtime source paths —
`VERSION`, `bin`, `lib`, `adapters`, `primitives`, `policy`, `templates`, `config.example.json` —
and `citizen freeze --merge-check <ref>` refuses a merge into the frozen branch that changes any of
them, because such a change costs part of the round again. How much of it is scoped per target: a
change under one runtime's adapter directory invalidates only that runtime's targets, unless it
touches a file shared code reads for every runtime, and a change to shared source invalidates them
all. The carve-out and its limits are in [compatibility](compatibility.md).
Return `state` to `open` after the tag. The evidence commit must stay an ancestor of the
qualification source commit, which `evidence_errors` enforces, so a diverged release branch fails
closed rather than publishing an unqualified source.

Before the round starts, and again at the freeze commit, run the deterministic smoke tier. It
spends no model turn and costs about two minutes:

```sh
python3 scripts/smoke_tier.py            # --list names each check, running none
```

It runs the acceptance runner's self-tests against recorded transcripts, the documentation-link
check, the precondition probe for each target's client, login and Docker daemon on the host that
will run the round (the [runbook](qualification-runbook.md#target-hosts) establishes them), and the
disposable-home sync, projection-drift and lifecycle checks. Each defect it catches is one that
would otherwise be found part-way through a round and cost the whole round again. A green tier is
**not** qualification: it observes no client, writes nothing under `compatibility/evidence/` and
appears in no catalog record, and the run fails if any check touches either. **A red tier blocks
the round:** `scripts/qualification_round.py` runs it first and, when it fails or times out,
launches no target and records why in `round.json`. `--skip-smoke` is for a tier you already ran
green at this commit; the round records it as `skipped`. CI also runs the tier as a `smoke` job,
which the branch ruleset does not require, so a red job warns about a merge but blocks only the
round.

**Fix no defect mid-round.** A round runs all four required targets to completion and collects
their defects; a fix landed between targets invalidates the targets already observed and forces a
re-run of each. Land the collected fixes together on `main` afterwards, cut a new freeze commit,
and re-qualify once.

## GitHub About

8. Bring GitHub About's description, topics and homepage to `product.json` with the commands
   below. Run the check every release, even when nothing changed, and record its result.

`product.json` is the source of the landing copy, the README grid and the About panel.
`scripts/sync_about.py` compares its `github_description` and `topics` with
`gh repo view --json description,repositoryTopics,homepageUrl`, treating topics as a set because
GitHub returns them in its own order. A `homepage` key in `product.json` is compared too; while
the file names none, the live homepage URL is left alone.

```sh
python3 scripts/sync_about.py --check    # names each differing field, exits non-zero on drift
python3 scripts/sync_about.py --apply    # writes them through `gh repo edit`
```

Run both locally. `--apply` changes the repository's public metadata, so it needs the owner's
approval on each run, and it cannot be moved into the release workflow: editing repository
settings needs administration access, which is not among the permission scopes available to the
workflow's `GITHUB_TOKEN` ([workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#permissions)).
The `--check` side of it runs inside `scripts/release_preflight.py`, which warns rather than fails
when `gh` is unauthenticated.

`scripts/sync_about.py --apply` writes whatever `product.json` the *current checkout* holds. Run it
only from a worktree fast-forwarded to `origin/main` (`bin/harness worktree create main-sync <repo>`,
then `git merge --ff-only origin/main`); a stale checkout once reverted the About panel.

## Rollback

Keep the previous harness tag and the release artifact's identity before publishing. A regression
gets a revert PR and a new harness release; do not retarget the old tag, and leave `stable` where it
is until that release advances it. Restore GitHub About by running `scripts/sync_about.py --apply`
from a checkout of the previous tag, with the owner's approval. Reconcile local configuration
through its ownership journal, preserving conflicts and adopted backups. Verify release identity
again.

## Release status

The 0.14.0 release qualifies Claude Code CLI on macOS and Linux from complete scripted
records; #741's independent hand comparisons were explicitly deferred to v0.15.0 and remain a
catalog limitation. Codex CLI is not in its
contract: it is admitted once a scripted qualification round agrees with a hand-driven one, so
0.11.1 remains the last release qualifying it. The VS Code surfaces and Codex Desktop are unqualified previews. The architecture-viewer
integration is also a preview for a separately installed implementation, with no bundled viewer
or distribution-clearance claim. The release is identified by the exact commit carrying the
immutable `v0.14.0` tag. Never bypass the release preflight.

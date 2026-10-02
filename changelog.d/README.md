# Changelog fragments

One file per change, so branches in flight never edit the same section of `CHANGELOG.md`.
Changes under the governed source and documentation paths require a new fragment or a waiver.
Editing `## [Unreleased]` does not satisfy the rule. Release assembly consumes fragments as
described in [the release procedure](../docs/releasing.md).

- **Name:** `<issue-or-pr>.<kind>.md`, where the number is the delivery issue (or the pull
  request when there is no issue) and the kind is `added`, `changed`, `removed` or `fixed`. Any
  other `.md` in this directory, apart from this file, fails `bin/harness lint`, and so does a
  subdirectory; dotfiles, editor swap files and other non-`.md` files are ignored. Only a
  fragment the branch creates counts, not an edit to one already on `main`.
- **Body:** the entry itself, in the changelog's prose style: full sentences, what changed and
  why. Wrap it as you would in `CHANGELOG.md`; the bullet and a trailing `(#N)` are added at
  assembly, the reference only when the body does not already contain it.
- **Waiver:** a change under `bin/`, `lib/`, `adapters/`, `primitives/`, `policy/`, `docs/` or
  `scripts/` with nothing to announce adds `<issue-or-pr>.none.md` whose body says, in at least
  three words, why. It is a file rather than a pull request body line so that a local lint, a
  pull request run and a merge queue run all see the same answer. Waivers are never rendered.

At release time, `python3 scripts/release_notes.py --changelog <version>` inserts a
`## [<version>] — <date>` section under an empty `## [Unreleased]`, with Added, Changed, Removed
and Fixed blocks in that order and entries in ascending number within each, then deletes the
fragments it consumed. `--dry-run` prints the result and changes nothing. It refuses an empty
set, a malformed name, a version that already has a section, and an Unreleased section that still
holds hand-written entries. The release pull request needs no fragment or waiver of its own: a
branch whose `CHANGELOG.md` gains a `## [<version>]` section the base lacks, and which deletes
fragments the base carried, satisfies the lint because it consumes them. A waiver added there
anyway would outlive the release.

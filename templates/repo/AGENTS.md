# <project name>

One paragraph: what this repo is, who it is for, and the one architectural fact an agent must
not violate. Link the design corpus if it lives elsewhere.

Global agent rules load from the user's harness; **do not restate them here — link them.** This
file carries only what is true of this repo.

## Commands

```sh
<install>            # e.g. pnpm install
<quality gate>       # the exact command CI runs; agents run this on HEAD before every push
<test>               # the fast local loop
<run>                # start the thing
```

**Expected clean-tree output** of the quality gate on an unmodified checkout, so a failure is
attributable: `<paste the final line, e.g. "All checks passed!", and the test count>`.

## Gate

```sh
<quality gate>       # e.g. ruff check .
<test>               # the fast suite, not the full integration run
```

The `stop-gate` hook runs this block when the tree has changed since its last green run,
blocks the turn while it is red, and releases after eight consecutive blocks. When this block
takes longer than four minutes, add a `## Stop gate` block with a faster subset: the hook runs
that one instead, and this block stays the gate run before a push.

## Conventions

- Toolchain and framework versions are pinned in `<file>`; upgrade on this project's schedule.
- Work in a worktree branched off `main`, never in the shared checkout (see the
  `worktree-per-agent` skill), unless this repo says otherwise here.
- `decisions.md` is append-only; on a merge conflict keep both sides in date order.
- Generated index files are rebuilt once at merge, never on both sides.
- Feature work ends with the pre-PR checklist below, not with "done".

## Pre-PR checklist

- Quality gate green on `HEAD` in this checkout.
- Every file in the diff traces to the declared issue or story.
- No planning-framework footers, internal ticket references or private-context notes in
  shipped code.
- Docs updated in the same PR when behaviour changed.

## Path-scoped rules

Rules that apply only to some files live in `.claude/rules/<topic>.md` with a `paths:` glob in
their frontmatter, so they load only when those files are touched. Keep this file short.

## `AGENTS.md` and `CLAUDE.md` are one file

`CLAUDE.md` is a symlink to this file (`ln -s AGENTS.md CLAUDE.md`), so every agent reads the
same instructions. Edit `AGENTS.md`.

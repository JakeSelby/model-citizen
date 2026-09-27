# Model Citizen

A user-aligned, model-provider-agnostic harness with shared custom primitives and switchable
personal stances. `bin/harness` projects one policy authority into Claude Code and Codex. The global
rules an agent runs under in this repo come from the harness itself, so this file carries only
what is true of this repository.

## For other repositories

- This is the Model Citizen harness. Its checkout is live: a merged change is in effect in the next session.
- Changes land only through a pull request with its own delivery issue, a green Gate block and CodeRabbit's review
  worked through. Never push to `main`.
- `product.json` here is the single source of the landing copy the sites render; change copy here, not in a site.

Before changing anything here from a session started in another folder, read
`.claude/rules/working-here.md`. Claude Code loads it by itself only in sessions started in this
repository, on their first file read; every other session and tool must read it.

## Gate

```sh
python3 bin/harness lint
python3 scripts/bmad_issue_sync.py sprint-status --check
python3 scripts/bmad_issue_sync.py audit
python3 -m unittest discover -s tests
```

The `stop-gate` hook runs this block when the tree has changed since its last green run,
blocks the turn while it is red, and releases after eight consecutive blocks. It runs only
once this checkout is trusted: accept Claude Code's folder dialog or run `bin/harness trust .`.

## Issues, milestones and releases

- **Every issue carries one `type::*` label**, and the `v<next>` milestone when it is meant for the
  next release. Create that milestone when the first issue is filed against it.
- **A pull request that adds or changes a user-visible capability updates `product.json` in the
  same pull request**: a new feature line, or an `on_the_way` entry promoted into `capabilities`.
  That file is the one source of the landing copy, the README grid and the GitHub About
  description. Planned work worth advertising goes in `on_the_way`, capped at five entries, and
  each entry names the issue, planned client or document it stands for. The `landing-copy` check
  fails a pull request that changes `bin/`, `lib/`, `adapters/`, `primitives/` or `policy/` without
  `product.json`, unless the body carries a `Landing copy:` line saying why none is needed.
- **Releases are cut by milestone.** Merge freely; propose a release when the milestone empties or
  when a user-visible unreleased change is seven days old. A regression fix releases at once as a
  patch.
- **Number by what changed**, not by where it landed in the changelog: fixes only is a patch;
  added or changed user-visible behaviour is a minor; `docs/compatibility-policy.md` decides a
  major. What a release must carry before it is tagged is the "Source and qualification" section
  of `docs/releasing.md`, which also holds the milestone close-and-open commands.
- **After every merge run `/land`**, and after every tag work the five surfaces below.

## A release is not done at the tag

A release has five surfaces, and each one goes stale on its own. Work them in this order and
report each as done, skipped or unverified — never infer one from another. The procedure, the
commands and the rollback are in `docs/releasing.md`; this list exists so none is forgotten.

1. **Source and evidence** — every fix merged; native qualification and lifecycle records for the
   frozen commit in `compatibility/evidence/`, the catalog `qualified` with digests and limitations.
2. **Release metadata** — catalog `released` and pinned, the changelog's Unreleased entries folded
   into the version section, status prose in `README.md`, `docs/compatibility.md` and
   `docs/releasing.md`.
3. **Tag and GitHub release** — `scripts/release_preflight.py` clean in a fresh clone, then the
   annotated `v<version>` tag; confirm the release workflow ran, the release is published and
   `scripts/advance_stable.py --check` finds `stable` at the tag.
4. **GitHub About** — description, topics and homepage must equal `product.json`. Run
   `python3 scripts/sync_about.py --check` every release even when nothing changed, and say so.
5. **Development resumes** — the first runtime-source change after a release makes the catalog
   report drift by design; the next version needs a candidate opened before it can be qualified.

Step 4 with `--apply` changes public repository metadata, so it needs its own explicit approval
each time it is run.

## `AGENTS.md` and `CLAUDE.md` are one file

`CLAUDE.md` is a symlink to this file. Edit `AGENTS.md`.

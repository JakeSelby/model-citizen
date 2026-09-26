# Workspaces

This page covers multi-root workspaces: which repositories belong together, how a session is
launched across them, and Claude Code's per-workspace session store. It does not merge Codex
native history or memory. Use [shared task continuation](task-continuation.md) to carry work between runtimes.

Nothing in the harness needs to be the root of a workspace. Everything installs at user level
and loads in every session.

## What the first folder decides

- **Project settings** (`.claude/settings.json`) load from the first folder, the session's
  working directory, only.
- **Instructions** (`CLAUDE.md`, `CLAUDE.local.md`, `.claude/rules/`) load from the first folder.
  A later folder, which Claude Code receives as `--add-dir`, contributes its `.claude/skills/`,
  `.claude/commands/` and `.claude/agents/`, and its instructions too only when
  `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1` is set in the environment. Claude Code never
  loads an `AGENTS.md` from an added folder, and Codex loads only its own working directory's.
- **Session history and auto memory** are keyed by the first folder's path under
  `~/.claude/projects/`.

So pick as first the repo whose project configuration you want, and never reorder a
workspace's folders casually: the session store would appear to vanish.

## The workspace map

Point `workspaces_dir` at the folder that holds your `.code-workspace` files:

```sh
citizen config set workspaces_dir ~/path/to/workspaces
```

Those files are then the only definition of which folders belong together. Nothing else is
stored: every command reads them again, so editing a file in VS Code is the whole change. A
workspace's name is its file stem, and its members are its `folders[].path` entries in order,
resolved against the file's own folder. Comments and trailing commas are accepted, as VS Code
accepts them; a `uri`-only folder is skipped, and a folder that no longer exists is reported and
left out. With the key absent the feature is off everywhere; remove it from
`~/.config/agent-harness/config.json` to turn it off again.

A session belongs to the member folder that contains its working directory, the longest match
winning, and a linked git worktree counts as its main checkout. When that folder is in more than
one workspace, the first rule that decides wins:

1. `HARNESS_WORKSPACE` names one of them; `workspace open` sets it.
2. Exactly one of them has, as its other members, the folders the session was launched with as
   `--add-dir`, which is how a VS Code window reveals the workspace it opened.
3. The overrides file names the folder.
4. The folder is in only one workspace.
5. The folder is the first member of exactly one of them.

Otherwise nothing is attached and the candidates are listed.

### The overrides file

An optional `overrides.json` beside the workspace files pins a shared folder to one workspace,
or with `null` to none, which opts the folder out entirely. Folder paths resolve against
`workspaces_dir`, and comments are accepted:

```jsonc
{
  "~/repos/shared-lib": "billing",   // shared-lib is in three workspaces; use this one
  "../scratch": null                 // never attach a workspace here
}
```

An override naming a workspace that does not exist, or one that does not contain the folder, is
ignored, and `workspace list` reports it. The file is edited by hand.

## `citizen workspace list`

Prints each workspace with its members in order, each marked with the characters of instructions
it carries (`CLAUDE.md`, else `AGENTS.md`, plus `CLAUDE.local.md`, unscoped `.claude/rules/` and
their `@` imports) or `missing`. Then every folder in more than one workspace, with the workspace
it resolves to and the rule that decided, or its candidates; then ignored overrides and any file
that could not be read. With `workspaces_dir` unset it says how to set it and exits 1.

## `citizen workspace open`

```sh
citizen workspace open NAME [--codex] [--dry-run] [-- ARGS...]
```

Launches Claude Code in the workspace's first existing folder with every other existing folder as
`--add-dir`, and `HARNESS_WORKSPACE=NAME` in its environment. Arguments after `--` go to the
runtime, before the `--add-dir` flags: Claude's `--add-dir` takes several folders, so a prompt
placed after it would be read as one more folder.

- `--codex` launches `codex -C <first> --add-dir <each other>` instead.
- `--dry-run` prints the `cd` and the command, with the environment variable, and runs nothing.

The added folders' `CLAUDE.md` files load natively because `sync` sets
`CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1` while `workspaces_dir` is set; see
[the environment variable](#the-environment-variable).

## The session-start hook

While `workspaces_dir` is set, a session opened in any member folder is told its workspace at
start-up: the workspace's name and the rule that attached it, each other member's path marked
`loaded natively`, `supplied by this hook` or `missing`, and the instructions of every supplied
member. Instructions are the member's `CLAUDE.md` and `.claude/CLAUDE.md`, else its `AGENTS.md`, then `CLAUDE.local.md`
and each `.claude/rules/*.md` without `paths:` frontmatter, with their `@` imports; a path-scoped
rule is listed by path only. A folder in several workspaces that no rule decides gets one line
naming the candidates and the overrides file, and a folder in none, or with the key unset, gets
nothing.

On Claude Code a member counts as loaded natively, and is not supplied twice, only when the
variable is set in the hook's environment, the member is an `--add-dir` argument of the running
Claude process, read exactly from `/proc` on Linux or `sysctl` on macOS (elsewhere no member
counts as native), and it has a `CLAUDE.md` or `.claude/CLAUDE.md`. A member with only an `AGENTS.md` is always supplied.
On Codex every member is supplied.

The hook is `workspace-session`, registered as its own SessionStart entry beside the start-up
block's, because each hook's output is capped at 10,000 characters and that block already uses
most of one. A block of at most 9,000 characters goes inline. A longer one is written to
`~/.local/state/agent-harness/workspaces/<name>-<hash>.md`, named for the workspace and a hash of
its content so two sessions never overwrite each other's, and only the member list and that path
are inlined, with an instruction to read the file before answering. The template's `Read(~/**)`
allow rule already covers it, and bundles older than seven days are removed on the next write. If
the file cannot be written, the block lists each member's instruction file to read instead. Each
instruction file is read up to 64 KiB. `citizen config set hooks.workspace-session off` switches it off from the next
session. Any failure leaves the block out; it never stops a session starting.

What each surface gets:

- **CLI and `workspace open`:** the members arrive as `--add-dir`, so members with a
  `CLAUDE.md` load natively and the hook supplies the rest.
- **VS Code:** the extension passes the window's other folders as `--add-dir`, which also tells
  the hook which workspace the window opened.
- **Desktop app:** a new chat has no added folders, so the hook supplies every member and asks
  Claude to request a member folder through the app's folder-grant tool on first use, not all at
  start; a granted folder arrives as `--add-dir` from the chat's next launch.
- **Codex:** it loads no `AGENTS.md` from an added folder, so the hook supplies every member.
  Codex runs a user hook only once its trust is accepted in the client, and `sync` registers this
  entry as a new one, so trust has to be accepted again after the first sync that adds it.
- **Role workers:** none run it. Claude workers load no user settings and Codex workers get a
  fresh `CODEX_HOME`.

## The environment variable

`sync` owns `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD` in `~/.claude/settings.json` while
`workspaces_dir` is set, by the same rules as the native telemetry variables. It writes `1`. A
value you already set to `1` is taken over silently and journaled; a different value is left
alone and reported. When `workspaces_dir` is unset, `sync` puts the key back to what it held
before the harness first wrote it: removed if it was absent, and your own `1` left in place if
that was what it adopted. A key the journal does not hold is never touched.

## One store per workspace

`citizen workspace create <name> <folder…>` writes `<name>.code-workspace` with the folders in
the order given (first is root) and the harness checkout appended as a non-root folder unless
`--no-harness`. It then creates `~/.claude/workspaces/<name>/` and symlinks each folder's
project key under `~/.claude/projects/` to that store, so the same history and memory follow
the workspace whichever folder happens to be first.

If a project key already holds real history, the command leaves it alone and says so. To merge
by hand: move the existing key aside, create the symlink, copy the old contents into the store
without overwriting (`rsync --ignore-existing`), then delete the moved-aside copy. Undo is
removing the symlink and moving the directory back.

## Adding the harness checkout to an existing workspace

Optional, for editing convenience only. Add it as a later folder. Its skills live under
`claude/skills/`, not `.claude/skills/`, so it adds none as an added folder; its `CLAUDE.md`,
which is its `AGENTS.md`, loads there only with the environment variable above set, and the
session-start hook supplies it otherwise.

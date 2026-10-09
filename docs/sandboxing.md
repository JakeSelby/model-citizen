# Sandboxing

Native sandbox settings are runtime-specific. The Claude configuration examples below do not
configure Codex. Consult [runtime controls](runtime-controls.md) and [compatibility](compatibility.md)
for enforcement gaps before relying on a role or permission boundary.

Permission modes answer "may this run?". They are the wrong question for an agent left alone: by
the time a loop is unattended, nobody is there to answer. Isolation answers a different question —
"what can this reach once it is already running?" — and the operating system enforces the
answer on every child process, whatever the model decided to run.

That makes it a posture, not a rule. How much isolation you want depends on what you are running
and who wrote it, and the two useful settings sit far apart.

## The two shapes

- **The built-in sandbox.** A `sandbox` block in `~/.claude/settings.json` fences Bash commands at
  the OS level: writes confined to the working directory, network denied except an allowlist,
  the SSH, AWS and GitHub CLI credential directories and the GitHub token variables denied (AWS
  keys exported as variables still reach a command). Every tool keeps working, so it is cheap
  enough to leave on for daily work.
- **A container.** The agent runs inside it, with only the worktree bind-mounted and the host
  config mounted read-only. A harder boundary bought with coarser tooling: no network means no web
  tools, no MCP over the network, no package installs, and no model API unless you allow that one
  host. Right for loops that run while you sleep and for anything parsing untrusted input.

The `sandbox` skill carries the exact keys, the quoted defaults, the `docker run` line and the
failure modes of each. Neither shape isolates branches; `worktree-per-agent` does that, and the two
compose.

## What the harness writes, and only when you ask

The settings template carries a `sandbox` block that turns the built-in sandbox on, and `sync`
writes it only for a user who opts in: `bin/citizen config set sandbox.enabled true`, then
`bin/citizen sync`. `sync` derives `filesystem.allowWrite` from the workspace each time it runs,
and `sandbox.strict` closes the retry outside the boundary. Until you opt in, nothing changes.
The task-worktree root is in `allowWrite` so a sandboxed session can create and work in its own
worktree; the cost is that a command in one task can also write a sibling task's worktree.
Run each task in a container when tasks must not reach one another.

`sandbox` is user-level configuration with a real blast radius: a sync that widened
`allowedDomains` or dropped a `denyRead` entry would quietly undo a boundary you set. So the key
is owned the way the native telemetry keys are. A `sandbox` block you wrote yourself is reported
and left exactly as it is, and switching `sandbox.enabled` off restores what the key held before
the harness first wrote it. To tune your own block, leave the switch off and configure the keys
yourself, in your settings file or per session with `--settings`; the skill tells you which.

The sandbox is one of three layers under a shell command, with `grade-bash` as the accident guard
and snapshots that make a discard recoverable: [runtime controls](runtime-controls.md#three-layers-under-a-shell-command)
describes all three and what each one does not cover.

# Installing shared primitives into agent runtimes

Select targets independently in your user configuration:

```json
{"claude": {"manage": false}, "codex": {"manage": true}, "vscode": {"manage": false}}
```

`bin/citizen config set claude.manage false` makes the same change. A Codex-only sync creates
its configuration home even on a fresh machine; it does not require a Claude installation.
`CLAUDE_CONFIG_DIR` and `CODEX_HOME` select nondefault runtime homes. `HARNESS_HOME` isolates
the harness's own configuration and state for fixtures. It is not a native runtime setting.

Codex receives shared instructions and identity, the skill catalog under `~/.agents/skills`,
seven generated role configurations, and five `harness-*` workflow skills. Source generation
and installation do not establish native hook activation, role confinement, or client support.
Runtime qualification is reported separately.

The permission choices express intent through different native controls. Codex `manual` uses
`on-request`, `read-only`, and the user reviewer. `auto` uses `on-request`, `workspace-write`,
and automatic approval review. `bypass` uses `never` with full access and requires the existing
explicit acknowledgement. `inherit` preserves native choices. Codex renamed the reviewer field to
`approvals_reviewer`, and a client drops a spelling it does not know without a word, so sync asks
the installed client which name it accepts — from its own protocol schema, or `--strict-config`,
neither of which starts a model turn — writes that one, and removes the other. A client that
accepts neither gets no reviewer key, a sync notice and a `bin/citizen doctor` finding. Native
requirements and live
permission overrides can restrict or supersede defaults; these mappings are not an assertion
that Claude and Codex permission modes are equivalent.

Configuration changes own fields, not whole files. A protected ownership ledger records prior
and last-applied values before replacement. TOML editing preserves unrelated tables/comments;
TOMLKit is bundled unmodified with its MIT notice, so no global Python package install is
required. JSON-with-comments editor files are left unchanged with an explicit diagnostic.

`bin/citizen diff` detects modified generated content and owned settings. Uninstall restores prior
values only when they still match the harness's last write; intervening user changes remain
with a conflict report and recoverable ownership state. It never deletes a redirected link.
Concurrent sync/uninstall operations refuse a second writer. Interrupted generated/config
writes retain an intent record that the next sync can reconcile.

Unmanaged instructions and skills require explicit adoption. Adopted Codex instruction text
is included in subsequent projections and restored on uninstall. Existing user-owned files,
MCP/plugin settings, selected model and credentials are not replaced by a default config.

`sync` installs user defaults only. Project and session stance overrides are resolved by the
lifecycle adapter in that invocation; they never repoint global links used by another session.
`HARNESS_PERMISSIONS` is not a way to grant native permissions to a running client. Set a durable
posture through user configuration and sync, or use that client's own permission controls.
Custom Claude configuration homes receive an instruction file importing their own personal file.

Link and adoption intent is journaled before filesystem changes, so an interrupted sync retains
its recovery path. Malformed native JSON/TOML is rejected during preflight. Uninstall preserves
redirected links and occupied restoration destinations, returning a conflict status and retaining
the recovery manifest. It does not overwrite even a dangling user symlink to restore a backup.

## The one-line installer

`scripts/install.sh` is POSIX `sh`, collapses the first eight commands of the clone path into one,
and is safe to run twice. Its shape is borrowed from pmstack's `install.sh`.

```sh
curl -fsSL https://raw.githubusercontent.com/JakeSelby/model-citizen/stable/scripts/install.sh | sh
```

1. **Requirements.** `git`, `python3` 3.9 or newer, macOS or Linux. A missing one is a single line
   naming what to install, and nothing else runs.
2. **Checkout.** Clones `--branch stable` into `~/repos/agent-harness`. An existing checkout there
   is fetched and fast-forwarded instead of re-cloned; one that cannot fast-forward, and a
   destination that is occupied by something that is not a git checkout, both stop the script
   rather than being reconciled for you.
3. **Configuration.** `bin/citizen init --yes` writes `~/.config/agent-harness/config.json` from
   the example, taking the name from `git config user.name`, the handle from a signed-in `gh` and
   the timezone from the system. Any field it cannot answer keeps its example value and is listed
   on the way out for `bin/citizen config set`. An existing config is never rewritten.
4. **Preview.** `bin/citizen install --dry-run`, which writes nothing.
5. **Next command.** It prints `bin/citizen install` and `bin/citizen uninstall` and stops. The
   script never runs `install` without `--dry-run`.

Any failure exits non-zero with one line naming the step. `HARNESS_CHECKOUT` moves the checkout,
`HARNESS_BRANCH` tracks another branch, `HARNESS_INSTALL_NO_HOMEBREW=1` and
`HARNESS_INSTALL_NO_APPS=1` pass `--no-brew` and `--no-apps` to the preview, and anything after
`sh -s --` is passed to it too.

There is no PyPI package, and one is not planned. The harness runs *from its checkout*: every hook
in `~/.claude/settings.json` runs a script under `~/.claude/hooks/harness`, which is a link into
the checkout; the rules, stances and skills in `~/.claude` are links into it too; and the vendored
TOML parser is imported relative to it. A copy installed into a `site-packages` directory would
have to become that checkout, so the script clones one instead.
[The sync model](sync-model.md) has the detail.

## Install from the plugin marketplace

Claude Code can load the projected primitives without a checkout. In a session:

```
/plugin marketplace add JakeSelby/model-citizen
/plugin install model-citizen@model-citizen
```

`.claude-plugin/marketplace.json` lists one plugin whose source is the repository root, so the
install reads `.claude-plugin/plugin.json` and nothing is duplicated between the two manifests.
That manifest carries the skills, the eleven subagent roles, the slash commands and the output
style. Claude Code namespaces them: a plugin skill is `/model-citizen:<name>`.

### Moving an `agent-harness` plugin install to `model-citizen`

The plugin was published as `agent-harness@agent-harness` before the rename. Claude Code keeps that
ID when its copy of the marketplace updates, and the plugin then fails to load, because the
marketplace no longer lists a plugin by that name. Adding the same repository again does nothing
while the old marketplace is registered, so the old plugin and marketplace go first.

With a checkout installed, run:

```sh
bin/citizen upgrade --dry-run   # print the four `claude plugin` commands
bin/citizen upgrade             # run them
```

It reads the old install's scope and the marketplace's recorded source from Claude Code's plugin
state, then uninstalls `agent-harness@agent-harness`, removes the `agent-harness` marketplace, adds
the marketplace again from the same source and installs `model-citizen@model-citizen`, in that
order. A failed step stops the run and prints the steps that did not run. Without the `claude`
CLI on your PATH it prints the in-session steps instead.

A plugin-only install has no `citizen` command, so run the steps in a session. The `add` line
takes the source you first added the marketplace from; if that was a fork or a local path, add
that instead of `JakeSelby/model-citizen`:

```
/plugin uninstall agent-harness@agent-harness
/plugin marketplace remove agent-harness
/plugin marketplace add JakeSelby/model-citizen
/plugin install model-citizen@model-citizen
```

Skills move from `/agent-harness:<name>` to `/model-citizen:<name>`. `bin/citizen doctor` recognizes
either ID, names `bin/citizen upgrade` while the old one is enabled, and warns while both are enabled,
because every skill would then load twice. `bin/citizen sync` prints the same pointer. The synced home
itself is unaffected by the plugin rename.

A marketplace install is a strict subset of `bin/citizen install`. It does not give you:

- the ownership journal, `bin/citizen diff`, or a restoring `bin/citizen uninstall`;
- stance selection — no rules, no `CLAUDE.md` projection, no personal file;
- the Codex projection under `~/.agents/skills` and `~/.codex`;
- hooks, so command grading, the stop gate and the usage feed are all off.

The marketplace path is its own client surface in
[the compatibility catalog](compatibility.md) and is **unqualified**: no native evidence has been
recorded for it. `bin/citizen doctor` reports which of the two paths is active.

### The Codex plugin

`.codex-plugin/plugin.json` packages the same skills for Codex, in the compatibility manifest
format of [OpenAI's plugin packaging guide](https://developers.openai.com/plugins/build/plugins).
Codex reads that file before `.claude-plugin/plugin.json` at a plugin root, so a Codex install of
this repository loads the skills under `primitives/skills/` and nothing else: no subagent roles,
slash commands or output style, and no hooks. Its name, version, description, author, licence,
homepage, repository and keywords match the Claude Code manifest, and a release bumps both
versions together. Like the marketplace path above, it is unqualified.

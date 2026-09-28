# Start with your way of working

Model Citizen is a harness of shared custom primitives for the two runtimes it supports today,
Claude Code and Codex. Your personal stances are switches for behavior: communication, delegation,
testing, autonomy and decision-making. The same selection resolves in both runtimes, and three of
the nine axes — autonomy, delegation and cost — bind to enforcement rather than to prose.

## Choose a runtime and inspect support

Claude Code and Codex are integration targets. Review [compatibility](compatibility.md) before
choosing a client; candidate projections are not proof of native support. Cursor and Grok are
planned. You need your own runtime account and billing, Git and Python 3.9+. Native Windows is
unsupported; WSL2 is unqualified. The harness does not sell or supply model access.

## Install

One command clones the `stable` branch to `~/repos/agent-harness`, writes a default configuration
and previews the install without performing it:

```sh
curl -fsSL https://raw.githubusercontent.com/JakeSelby/model-citizen/stable/scripts/install.sh | sh
```

Read the preview it prints, then select which runtimes to manage and install:

```sh
cd ~/repos/agent-harness
bin/citizen config set claude.manage true
bin/citizen config set codex.manage true
bin/citizen install --dry-run
bin/citizen install
```

To clone by hand instead, or to see what each step of the script does, read
[installation ownership](runtime-installation.md#the-one-line-installer).

You can set either runtime to false; a Codex-only setup needs no Claude configuration. Install
adds the selected missing runtime tools and synchronizes their configuration. On macOS it can
also install Homebrew packages and VS Code; review `install --help` to skip those operations.
When the tools are already installed, use `sync --dry-run` then `sync` instead.

Complete each selected client's own sign-in. `bin/citizen doctor` reports files and configuration,
not proof that a login or native hook is active. Start a new session and accept native hook trust
where the client requires it. Use `bin/citizen trust <repo>` separately for the repository gate.

## Make your first switch

```sh
bin/citizen config set stances.voice answer-card
bin/citizen stances --json
bin/citizen sync
```

The same selected policy reaches both adapters. Restart the client to load changed global
instructions. [See resolved behavior before and after a switch](stance-demo.md), then
[author your own stance](primitive-authoring.md). Project/session overrides stay scoped to
that invocation; syncing does not persist them into everyone else's global files.

## Use workflows and continue work

Shared workflows compose roles and skills. Claude exposes command projections; Codex exposes
`harness-<workflow>` skill projections. Both use the same source. The handoff workflow writes
shared task data; [task continuation](task-continuation.md) explains revision checks and verification.

Run `bin/citizen diff` for drift, `bin/citizen usage` for recorded measurements, and
`bin/citizen compatibility` for qualification. [Installation ownership](runtime-installation.md)
covers custom configuration homes, adoption, uninstall and conflicts. Never replace an unmanaged
file just to make sync quiet.

## Your first session

From the repository you want to work in, start either installed runtime:

```sh
cd ~/some-folder
claude
# Or start Codex in that folder:
codex
```

Ask the agent to explain its effective stance choices, then try a small task. Research and
planning can work without a code repository. Build and review workflows need repository context;
opening a pull request also needs a remote and a signed-in GitHub account.

Then close the loop and ask which of your rules actually fired:

```sh
bin/citizen usage --rules
```

It prints one line per detector over the last 30 days — hits, the sessions that saw them and the
share — with `--by repo` and `--by stance` regrouping the same hits by repository and by the
preference variant in force. On a fresh home it says `no measured sessions in the last 30 day(s)`
until a session or two has been recorded; `--rescan` backfills from transcripts you already have.
[Usage](usage.md) explains the annotations and what the figures do not yet support.

## What it costs

Model access is billed by your provider or covered by your subscription; rate-limit windows and
context usage still matter. The harness supplies policy, not an AI model or paid access.
[Usage](usage.md) shows observed measurements without inventing missing values. The
[preferences guide](preferences.md) explains the cost stance. [How it works](how-it-works.md)
and [sandboxing](sandboxing.md) explain the controls. `bin/citizen uninstall` restores owned settings
where possible and reports conflicts rather than overwriting your edits.

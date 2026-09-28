# Privacy policy

This policy applies to the Model Citizen plugin distributed through the Claude Directory. Model
Citizen is an open-source plugin that runs inside Claude Code. Jake Selby does not operate a
Model Citizen service, and the plugin does not send personal data or conversation content to a
service operated by Model Citizen or its maintainer.

## Data the plugin can access

When you ask Claude Code to use a Model Citizen skill, command or agent, Claude Code may read the
local files, repository metadata, command output and current task context needed to do that work.
The plugin does not include a remote MCP server, analytics service or account system. It does not
independently collect or retain that data.

Claude Code and any third-party tool or service that you deliberately invoke remain governed by
their own terms and privacy policies. Model Citizen does not change the permissions or controls
that those products apply.

## The separately installed harness

The full Model Citizen harness is a separate local installation. It can keep local usage and
decision ledgers on your machine. Export from the usage ledger is off by default and requires an
explicit configuration; decision-log records are never exported by that feature. See
[Exporting the ledger](telemetry.md) for the data, controls and retention model. Installing the
Claude Directory plugin alone does not install those hooks or ledgers.

## Retention and deletion

Because Model Citizen operates no service that receives plugin data, the maintainer has no plugin
data to retain or delete. Files and command history held by Claude Code or another tool are subject
to that product's controls.

## Contact

For privacy questions, open a [GitHub issue](https://github.com/JakeSelby/model-citizen/issues).
Report a security issue privately through the repository's **Security** tab, as described in the
[security policy](../SECURITY.md).

Last updated: September 28, 2026.

# Model Citizen for Claude Code

Model Citizen is a set of reusable skills, subagent roles, slash commands and an output style for
Claude Code. This directory bundle is the small, standalone plugin distribution from the
[Model Citizen repository](https://github.com/JakeSelby/model-citizen).

## What is included

- 15 skills for planning, verification, delegation, licensing, migration safety and delivery
- 11 scoped subagent roles
- 7 slash commands
- the scannable output style

The bundle does not install Model Citizen's hooks, global rules, stances, local ledgers or Codex
projection. Those belong to the separately installed full harness.

## Data and external services in the Claude Directory plugin

The plugin runs inside Claude Code and can read local files, repository metadata, command output
and task context when you ask it to work with them. It has no Model Citizen account, analytics,
remote MCP server or hosted service, and the maintainer does not receive or retain that data.

Some included workflows can use tools you configure or approve:

- delivery workflows can run `git` and the GitHub CLI, sending repository content and metadata to
  GitHub or another configured Git remote;
- research and review workflows can invoke Claude Code's WebFetch and WebSearch tools, sending a
  URL, query and relevant request context to the services behind those tools; and
- commands, agents and skills can read or write local project files and run local programs under
  Claude Code's native permissions and approval controls.

Model Citizen does not add credentials or bypass those controls. Each external product remains
governed by its own terms, privacy policy and retention settings. See the bundled
[privacy policy](docs/privacy.md) for details.

## Support

Read the [documentation](https://model-citizen.dev/) or report a problem in
[GitHub Issues](https://github.com/JakeSelby/model-citizen/issues).

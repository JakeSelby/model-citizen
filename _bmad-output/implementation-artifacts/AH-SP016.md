---
bmad_id: "AH-SP016"
type: "spike"
title: "Spike: which identity and surface can trigger `@codex`"
lifecycle: "active"
provenance: "authored"
github_issue: 943
github_issue_url: "https://github.com/JakeSelby/model-citizen/issues/943"
parent_bmad_id: "AH-E021"
parent_github_issue: 941
updated: "2026-09-26"
---

# AH-SP016 — Spike: which identity and surface can trigger `@codex`

<!-- bmad-sync:begin -->
- **GitHub issue:** [#943](https://github.com/JakeSelby/model-citizen/issues/943)
- **Primary parent:** [AH-E021](https://github.com/JakeSelby/model-citizen/issues/941)
- **State:** active

The issue carries the summary, discussion and acceptance evidence; this file carries the design.

This work item was authored as part of the repository's committed BMad planning system.
<!-- bmad-sync:end -->

## Question

Of the four combinations (the bot's GitHub App or the owner's account, commenting on an issue or on a
pull request), which start a Codex cloud task? Who authors the resulting commits and pull request, and
does Codex follow `AGENTS.md` (the closing line, the story file, the Landing copy line) without being
told?

The Codex lane (#950) waits on the answer. The Codex pricing page lists "GitHub issue and PR delegation
with `@codex`" on Plus and above and not with an API key
[Source: https://developers.openai.com/codex/pricing], but no page says who may trigger it or whether a
GitHub App's comment counts.

## Experiment

- A private scratch repository with the Codex connector and the bot's GitHub App installed. The App is
  registered here, with the permission list in #945.
- Seed the repository with one failing unit test per cell, four in all.
- Post one `@codex` request per cell: App on an issue, App on a pull request, owner on an issue, owner on
  a pull request.
- Record per cell: whether a task started, the link, the author of the commits and the pull request, and
  whether the pull request followed `AGENTS.md` unprompted.
- Record the ChatGPT plan tier and the date it was measured on.

## Exit criterion

All four cells answered with a link within one working day (eight hours).

## Result

Not yet run.

## Decision

Open. The decision rule, fixed before the experiment runs:

- the App works on issues, so the lane comments on the issue;
- the App works on pull requests only, so the lane opens a draft pull request and then comments;
- only the owner's account works, so the lane posts through an owner token limited to this repository's
  issues and pull requests;
- nothing works, so the Codex lane is dropped and the split goes back to the owner.

The result sets #950's trigger path. Traces to FR-71.

## Dev notes

- **Bound requirements:** FR-71 Unattended bug-fix lanes.
- **Needs:** the App registered by the owner with #945's permission list; the owner's ChatGPT plan with
  the Codex connector.
- **Owner-only setup:** creating the scratch repository, installing the connector and the App, and any
  owner token are the owner's steps.
- **References:**
  - [Source: https://developers.openai.com/codex/pricing]
  - [Source: https://github.com/JakeSelby/model-citizen/issues/943]
  - [Source: https://github.com/JakeSelby/model-citizen/issues/950]

## Change log

- 2026-09-26: written at filing from the approved burndown bot plan.

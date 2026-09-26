---
bmad_id: "AH-SP018"
type: "spike"
title: "Spike: reach the Studio from a phone without opening it to the network"
lifecycle: "active"
provenance: "authored"
github_issue: 1003
github_issue_url: "https://github.com/JakeSelby/model-citizen/issues/1003"
parent_bmad_id: "AH-E026"
parent_github_issue: 959
updated: "2026-09-26"
---

# AH-SP018 — Spike: reach the Studio from a phone without opening it to the network

<!-- bmad-sync:begin -->
- **GitHub issue:** [#1003](https://github.com/JakeSelby/model-citizen/issues/1003)
- **Primary parent:** [AH-E026](https://github.com/JakeSelby/model-citizen/issues/959)
- **State:** active

The issue carries the summary, discussion and acceptance evidence; this file carries the design.

This work item was authored as part of the repository's committed BMad planning system.
<!-- bmad-sync:end -->

## Question

Can a developer open the Studio on their phone, the way they already drive sessions through Remote Control, without the server leaving loopback or losing its token and host checks? Candidates: an SSH port forward from the phone, Tailscale Serve in front of the loopback port, and a Remote Control extension if one exists.

## Experiment

For each candidate, open the Studio from iOS Safari on another network, change a stance in a draft and apply it, then try the same URL from a machine outside the tunnel or tailnet.

## Exit criterion

Each candidate recorded as working or not, with the security checks' results, within one working day.

Decision rule: one candidate works with every check intact and needs no hosted service, so document it; only a hosted relay works, so phone access stays out of scope, since no feature may require a remote service; none works, so drop phone access from this milestone.

## Result

<!-- fill: the measured numbers, where the raw evidence lives, and which side of the threshold they fall. -->

## Decision

<!-- fill: what the result decides, and the follow-up work it opens or closes. -->

## Dev notes

Blocked by #965. Traces to FR-76.

## Change log

- 2026-09-26: Filed by #960 with the Studio epics. The sections still marked to fill are written when the work starts.

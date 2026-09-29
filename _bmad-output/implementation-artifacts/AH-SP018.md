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
updated: "2026-09-29"
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

Every candidate was recorded within the day; none works with every check intact. Five runs of
`tests/test_studio_phone_access_probe.py` passed all four cases, taking 3.85 to 4.28 s (mean
4.03 s). A byte tunnel standing in for `ssh -L` kept Host, Origin, CSRF and cookie checks intact
only when the phone used the Studio's own port and `<hex>.localhost` name. Any other port was
refused 403 before bootstrap. Tailscale Serve's pass-through Host, and a `127.0.0.1` Host rewrite,
were both refused 403, and the refusal left the bootstrap token unspent. The routable address
refused the connection. Remote Control relays session messages through the Anthropic API and
forwards no HTTP, so it is a hosted relay. The SSH path still fails: the one-shot bootstrap token
reaches a browser only through a launcher file on the Mac or the control credential, and iOS
Safari can use neither. No phone was used; iOS `*.localhost` resolution is unverified and not
decision-relevant. Machine: Apple M5 Pro, macOS 26.5. Record:
`../../docs/spikes/2026-09-29-studio-phone-access.md`.

## Decision

None works, so phone access is dropped from this milestone and the Studio stays loopback-only
and launcher-bootstrapped. Loosening the Host or Origin check for a reverse proxy is ruled out.
If phone access returns, the next experiment is a pairing bootstrap for the SSH-forward path,
such as a one-shot QR code; it changes the #965 boundary and needs its own design review. The probe
stays as a regression test for proxy-shaped requests.

## Dev notes

Blocked by #965. Traces to FR-76.

## Change log

- 2026-09-26: Filed by #960 with the Studio epics. The sections still marked to fill are written when the work starts.
- 2026-09-29: Ran the loopback transport probe, recorded every candidate, and dropped phone access
  from v0.18.0.

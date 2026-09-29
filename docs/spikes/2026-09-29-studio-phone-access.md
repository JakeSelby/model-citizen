# Spike: can a phone reach the Studio without it leaving loopback?

> **Result: no candidate works with every check intact, so phone access leaves the v0.18.0
> milestone.** An SSH local forward keeps the Host, Origin, CSRF and cookie checks intact, but the
> phone has no way to receive the one-shot bootstrap token. Tailscale Serve is refused by the Host
> check. Remote Control carries session messages, not HTTP, and any extension of it would be a
> hosted relay.

## Question

Can a developer open the Studio on their phone, without the server leaving `127.0.0.1` or losing
its token, Host, Origin and CSRF checks? Candidates: an SSH port forward from the phone, Tailscale
Serve in front of the loopback port, and a Remote Control extension.

## Exit criterion

Written before the run: each candidate recorded as working or not, with the security checks'
results, within one working day. Decision rule: one candidate works with every check intact and
needs no hosted service, so document it; only a hosted relay works, so phone access stays out of
scope; none works, so drop phone access from this milestone.

## Experiment

The planned experiment was to open the Studio from iOS Safari on another network, apply a change,
then try the same URL from outside the tunnel. That needs a phone, an SSH server or Tailscale on
the Mac, and a route in from another network. The run was confined to loopback with no system
software installed and no network, firewall or sharing setting changed, so it replayed each
transport's request shape against a real Studio instead:

- **What is faked:** the phone and the transport. `ByteTunnel` in
  `tests/test_studio_phone_access_probe.py` is a loopback relay that forwards bytes untouched,
  as `ssh -L` does. The Tailscale Serve arm sends the headers its proxy sets. According to
  `ipn/ipnlocal/serve.go` at tailscale commit `3d62394a`, lines 976 to 1125, Serve passes the
  incoming Host through (`r.Out.Host = r.In.Host`). It also adds `X-Forwarded-Host` and the
  `Tailscale-User-*` identity headers. A Unix-socket target instead gets the target's own host.
- **What is real:** a Studio started by `harness studio --detach` under a temporary home, its
  bootstrap, session cookie, `/api/session`, and a mutation. The mutation is
  `POST /api/ui/preferences`, not a stance applied from a draft: a draft is a git worktree of the
  checkout, and every mutation passes the same guard in `Handler._dispatch` before its handler runs.
- **Outside the tunnel:** a TCP connect to the Studio port on the machine's routable address,
  found by a UDP route lookup. The test skips when the machine has none; every run below found
  one, `192.168.128.x` on Wi-Fi.

Command, run five times: `python3 -m unittest discover -s tests -p test_studio_phone_access_probe.py`.

## Result

Five of five runs passed all four cases, taking 3.85 to 4.28 s (mean 4.03 s). A sixth run under
the system Python 3.9.6 passed in 4.70 s.

| Candidate | Checks as measured | Works? |
| --- | --- | --- |
| SSH local forward, phone uses the Studio's port and `<hex>.localhost` name | Bootstrap 200, session 200, mutation with Origin and CSRF 200; without CSRF 403; foreign Origin 403 | Transport yes; bootstrap no, see below |
| SSH local forward on any other local port | Host check 403 before bootstrap, no cookie set | No |
| Tailscale Serve, Host `<machine>.<tailnet>.ts.net` | Valid cookie still 403; bootstrap without an Origin header 403 on Host alone, and the token stays unspent | No |
| Proxy rewriting Host to `127.0.0.1:<port>` | 403 | No |
| Remote Control | No HTTP path: the session "makes outbound HTTPS requests only and never opens inbound ports", and "all traffic travels through the Anthropic API" ([docs](https://code.claude.com/docs/en/remote-control)) | Hosted relay only |
| Outside the tunnel, the routable address on the Studio port | `ConnectionRefusedError` on the address found in every run | Boundary held |

The SSH forward fails on bootstrap, not on the network checks. The one-shot token reaches a browser
in only two ways. One is the launcher form written to the Mac's private state directory, which
Safari opens as a `file:` page and which posts with a null Origin. The other is the control
endpoint, which needs the control credential. iOS Safari can open neither, and it cannot send a
form POST from its address bar. Four more costs follow from the design, not from this run:

- The Host name is a fresh 32-hex-character name on every start, so the phone would have to be
  told it each time.
- The forward must use the Studio's port number, and `--port` can fall back to a random one.
- The Mac needs Remote Login turned on.
- Reaching the phone from another network needs either an inbound router port or a mesh VPN,
  whose coordination service is hosted.

Two claims were not measured on a phone: that iOS Safari resolves `*.localhost` names to
loopback, and that it keeps a `SameSite=Strict` cookie on them. WebKit
[bug 160504](https://bugs.webkit.org/show_bug.cgi?id=160504) was resolved when the OS frameworks
below WebKit began resolving `*.localhost`, which its reporter confirmed on macOS 26. This machine's
resolver returns `::1` and `127.0.0.1` for such a name. Whether iOS behaves the same is unverified,
and the decision does not depend on it.

**Machine:** Apple M5 Pro, 24 GB, macOS 26.5 (25F71), Python 3.14.5 and 3.9.6. No phone was used.

## Decision

Drop phone access from the v0.18.0 milestone; the Studio stays loopback-only and launcher-bootstrapped.
The cheapest next experiment, if phone access returns, is a pairing bootstrap for the SSH-forward
path, such as a one-shot token shown as a QR code on the Mac and redeemed at the loopback name.
That is a change to the security boundary (#965) and needs its own design review. Loosening the
Host or Origin check for a reverse proxy is not a candidate. `tests/test_studio_phone_access_probe.py`
stays as a regression test, so every future Studio build keeps refusing proxy-shaped requests.

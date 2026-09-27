# Studio architecture adversarial review

## Initial verdict

Not implementation-safe: nine blockers and four should-fix findings.

## Findings

- Bundle verification was optional while the committed bundle executes as trusted origin.
- Core reuse did not own DTOs, errors, idempotency, or the route-to-CLI registry.
- SSE had no replay, gaps, heartbeat, or snapshot recovery.
- Bootstrap, session, Host, Origin, CSRF, CSP, and filesystem rules allowed incompatible security stories.
- Draft saves lacked single-writer revisions and durable idempotency.
- Run identity, sidecars, snapshots, singleton recovery, and AI isolation/single-flight rules were incomplete.

## Resolution

Three fix rounds tightened AD-21 and AD-25 through AD-32. The final rules add mandatory release-time
bundle verification, one owned command/query/result contract, cursor epochs and snapshot barriers,
single-use bootstrap with explicit Origin exception, exact security headers, descriptor-relative file
operations, crash-recoverable draft journals, hash-chained run sidecars, versioned singleton control,
qualified AI adapters, full single-flight identity, and per-attempt cap reservations.

Final adversarial closure: **clean**.


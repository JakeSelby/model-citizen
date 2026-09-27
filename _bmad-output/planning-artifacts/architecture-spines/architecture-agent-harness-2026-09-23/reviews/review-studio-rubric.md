# Studio architecture rubric review

## Initial verdict

Fail: five high and three medium findings.

## Findings

- AD-29 rollback conflicted with AD-3 and FR-79.
- AD-31 persisted a bootstrap token that AD-27 described as one-shot.
- AD-25 covered CLI parity for mutations, not every Studio action.
- Threaded server state had no single-writer or publication authority.
- UX reconciliation was claimed before FR-85 and AD-32 aligned.
- Run metadata was not all file-backed.
- AI claim provenance omitted required immutable fields.
- The operational envelope omitted browser, build, bundle, and detached-lifecycle boundaries.

## Resolution

All findings were resolved in AD-3, AD-25 through AD-32, the operational envelope, PRD FR-79, the UX
spines, and AH-T036. The closure pass returned clean with zero findings.


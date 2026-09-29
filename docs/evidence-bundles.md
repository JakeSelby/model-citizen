# Evidence bundles

An evidence bundle is a read-only directory that makes a published benchmark claim reproducible
offline. `bundle.json` is the versioned index. Every referenced file is relative to the bundle,
has a SHA-256 digest, and is read as data. The verifier never runs the saved replay or verification
commands.

Schema version 1 has these top-level fields:

- `bundle_id`: a stable identifier for the run.
- `repository`: the bundled local Git checkout, its `owner/name`, and the full run commit.
- `artifacts`: hashed references for rows, task manifest, pre-registration, captured GitHub receipt,
  dated prices, audits, report, the two arm records, and every full redacted trajectory. The task
  and plan references also name their repository paths; the plan names its registration commit.
- `design`: pinned model, client version, effort, tasks, trials, arms, seeds, resample count, per-run
  cap, and saved replay and verification commands.
- `statistics`: the bootstrap seed and resample count.
- `published_figures`: `{name, pointer, value, estimand}` entries. `pointer` is a JSON pointer into
  the verifier's derived result; `value` must equal the re-derived value exactly.
- `evidence_cards`: `{id, claim, estimand, figure, interval, bundle, verify_status}` entries. Figure
  and interval each carry a derived pointer and exact value. Saved `verify_status` is ignored and
  replaced with the current verification result.
- `items`: exactly the twelve numbered evidence-standard items. Only deterministic judge agreement,
  item 8, may be `not-applicable`, with its reason recorded.

The loader rejects unknown or missing index keys, duplicate JSON keys, non-finite JSON numbers,
absolute paths, `.` or `..` segments, symlinks, containment escapes, missing artifacts, and digest
mismatches. The bundled repository is used only by fixed-argument Git reads. Its committed task and
plan bytes must match the bundle, and the registered plan commit and captured merge receipt must
precede every trial and be ancestors of the run commit. A receipt is retained provenance, not a
cryptographic attestation from GitHub.

The verifier re-prices every trial from the bundled dated price table and saved token counts. An
unknown model or incomplete token record leaves cost unknown and fails the pricing check; it is
never treated as zero. Timeouts retain the pre-registered per-run cap. Failed, fallback and timed-out
attempts remain in the intention-to-treat population. The verifier calls `replay_stats` for the
paired SM-2 result and derives per-task summaries, Pareto status, fallback rate, planned-attempt
completion, sample-ratio diagnostics, and descriptive ICC/design effects.

ICC uses the one-way random-effects single-measure estimator ICC(1,1) from Shrout and Fleiss,
computed separately for pass outcome and cost in each arm, with task as the cluster and the actual
trials per task as `m`. The design effect is `1 + (m - 1) * ICC`. Negative estimates remain negative;
they are not clamped. ICC and design effect are explicitly undefined, with a reason, for fewer than
two tasks, fewer than two trials per task, unequal cluster sizes, missing cost, or zero total
variance. These figures are descriptive and never change the paired SM-2 decision. Source:
[Shrout and Fleiss (1979), *Intraclass correlations: uses in assessing rater reliability*](https://doi.org/10.1037/0033-2909.86.2.420).

Synthetic bundles test the verifier. They are not proof sets and cannot support a product claim.
Publishing proof set 1 remains a separate campaign: fresh registered trials, complete trajectories,
the structural audits required by the evidence standard, and a report with a `What we do not claim`
section must all be present, whatever the result shows.

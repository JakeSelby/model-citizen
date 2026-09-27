# Operational truth and evidence review — agent-harness Studio

## Verdict

Five blockers and five should-fix findings remain before the AI overview and comparison surfaces can be
treated as evidence-safe.

## Blockers

- Every datum needs a provenance class, source, version, and evidence status (`EXPERIENCE.md:58`).
- AI claims must link to immutable report fields and native result or ledger IDs, digests, and observation
  times, not only another report (`EXPERIENCE.md:235`).
- Reusing a development tool does not prove read-only execution; AI assessment needs tool-disabled execution
  without mutation-capable credentials (`EXPERIENCE.md:229`).
- Matched-comparison keys and invalidation dependencies are underspecified (`EXPERIENCE.md:425-433`).
- Automatic AI refresh lacks eligibility, deduplication, retry/backoff, and terminal-failure rules
  (`EXPERIENCE.md:231-242`).

## Should fix

- Pin drill-downs to report snapshot IDs and propagate partial, failed, and unknown states with denominators
  (`EXPERIENCE.md:129-152`).
- Add `stale`, `inconclusive`, `refused`, and `unknown` to the closed state vocabulary
  (`DESIGN.md:218-244`).
- Disclose immutable model/tool version, pricing source and date, actual versus estimated spend, units,
  currency, coverage, and unpriced usage (`EXPERIENCE.md:233-236`).
- Lead comparison headlines with the evidence-standard verdict and the metric-specific no-effect value
  (`research.md:130-135`).
- Label investigation rows as generated suggestions and prohibit them from setting authoritative alert
  severity or state (`EXPERIENCE.md:131,248-249`).

## Counts

Blocker 5; should-fix 5; note 0.


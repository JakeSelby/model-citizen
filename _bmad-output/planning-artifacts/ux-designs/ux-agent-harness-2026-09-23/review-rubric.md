# Spine Pair Review — agent-harness Studio

## Overall verdict

Not story-ready. The pair is useful architecture input, but unresolved inheritance, missing state
coverage, source-name drift, and draft/finalization contradictions prevent clean downstream implementation.

## Category verdicts

1. Flow coverage — adequate
2. Token completeness — strong
3. Component coverage — adequate
4. State coverage — thin
5. Visual reference coverage — strong
6. Bloat and overspecification — adequate
7. Inheritance discipline — broken
8. Shape fit — adequate

## Findings

- **High — State coverage:** `AI settings` is an IA surface but has no disabled, enabled, provider-unavailable,
  cap-reached, validation, save-failure, or stale-value states (`EXPERIENCE.md:66,158`).
- **High — Inheritance and component coverage:** Mantine is conditional, so charts, skeletons, forms, tables,
  dialogs, and logs have no final visual or behavioral owner (`DESIGN.md:204-205`; `EXPERIENCE.md:31`).
- **High — Shape and readiness:** both spines are draft, the old memlog says finalized, and AI alignment is a
  phase blocker (`DESIGN.md:4`; `EXPERIENCE.md:3,450`; `.memlog.md:15`).
- **Medium — Flow coverage:** requirement closure traces FR-75–84 without explicitly excluding non-UX FR-1–74
  (`EXPERIENCE.md:74`).
- **Medium — Inheritance discipline:** UJ-1, UJ-3, and UJ-6–9 titles drift from the source despite a verbatim-name
  claim (`EXPERIENCE.md:273`; `prd.md:203`).

## Mechanical notes

- Critical 0; high 3; medium 2; low 0.
- Token definitions and visual-reference links are complete.


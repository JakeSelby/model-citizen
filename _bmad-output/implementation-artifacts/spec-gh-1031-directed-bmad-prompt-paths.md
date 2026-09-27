---
title: 'Directed BMad prompt paths survive explanatory em-dash clauses'
type: 'bugfix'
created: '2026-09-27'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context:
  - '{project-root}/docs/bmad-governance.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The v0.14.0 Linux qualification produced a BMad review brief that named the declared layer prompt, explained it after an em dash, and then directed the subagent to follow those instructions. Sentence splitting lost the file reference before the directive, so the spawn ran unconfined.

**Approach:** Extend directed-identifier continuity only when an intervening pronoun sentence still describes the named file as instructions or equivalent layer guidance. Lock the exact qualification wording and nearby false-positive cases in regression tests without changing the refusal text or other recognition signals.

</frozen-after-approval>

## Implementation Notes

- Delivery issue/story: #1031 / `AH-B120`.
- Reuse `frameworks._directed`, `ANAPHOR`, and the existing qualification-brief table in `tests/test_framework_directed_identifier.py`.
- The exact failure scores `(agents=0, directed=0, identifiers=1, phrases=0)` because `SENTENCE` splits at the em dash and `ANAPHOR` does not treat “it contains … review instructions” as continued reference to the prompt file.
- Keep plain “it” too weak: continuity should require an instruction-like noun so unrelated intervening clauses remain allowed.
- Update the BMad story, sprint status, and changelog fragment in the same PR; no landing-copy change because the existing confinement capability claim is unchanged.
- Added `PRONOUN_GUIDANCE` rather than widening `ANAPHOR`: bare “it” continues the prompt-file reference only when that segment also names instruction-like guidance.
- Added the exact qualification wording as a directed case and an unrelated fixture clause as the negative boundary; the focused eight-test file passes.
- Full gate passes: lint, sprint-status check, 600-item audit, 3,864 tests and generated-projection check.
- Blind review rejected the fixed-length lookahead: it admitted negation and another instruction owner while omitting longer clauses and common guidance nouns. Replaced it with a clause-scoped pronoun/verb/guidance check and added seven boundary cases.

## Review Triage Log

- `medium` — negated guidance could continue the file reference; fixed by rejecting negation between the governing verb and guidance noun.
- `medium` — an unrelated instruction owner could continue the reference; fixed by requiring `it` to directly govern guidance in the same unbroken clause.
- `medium` — the 120-character lookahead created an evasion boundary; fixed by using clause structure rather than a character cap.
- `medium` — common guidance synonyms were absent; added directions, rules, criteria, requirements and prompt.
- `medium` — the negative test did not exercise the matcher; added negation, another-owner and clause-break cases.

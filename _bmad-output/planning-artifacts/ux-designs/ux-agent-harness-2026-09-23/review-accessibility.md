# Accessibility and responsive review — agent-harness Studio

## Verdict

Four blockers and nine should-fix findings remain before WCAG 2.2 AA can be an acceptance claim.

## Blockers

- FR-77 lacks executable keyboard, screen-reader, contrast, reflow, and zoom consequences (`prd.md:1424`).
- Responsive rules omit 320 CSS-pixel reflow, 400% zoom, and 200% text-only zoom (`EXPERIENCE.md:213`).
- Editor errors lack label association, error summary, first-error focus, and diagnostic-to-line links
  (`DESIGN.md:309`).
- Notification read, dismissal, retention, deduplication, and restart persistence are unresolved
  (`EXPERIENCE.md:249`).

## Should fix

- Stack report cards in one column at the tested phone threshold (`.working/direction-briefing.html:10`).
- Define dialog naming, initial focus, inert background, trapped tab order, and focus restoration
  (`EXPERIENCE.md:183`).
- Debounce live run announcements and announce completion or failure once (`EXPERIENCE.md:198`).
- Add pause/resume for live updates without replacing content under the reader (`EXPERIENCE.md:148`).
- Define AI retry, cancel, backoff, timeout, terminal status, and last-good behavior (`EXPERIENCE.md:170`).
- Offer a prefilled matched rerun when comparison parameters differ (`EXPERIENCE.md:432`).
- Enforce 44px targets at phone width (`.working/direction-briefing.html:3`).
- Use native progress or progressbar semantics for run progress (`.working/direction-briefing.html:11`).
- Record AA ratios for every semantic token pairing and focus adjacency (`.working/color-themes-1.html:2`).

## Counts

Blocker 4; should-fix 9; note 0.


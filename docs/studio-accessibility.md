# Studio accessibility audit

The Studio targets WCAG 2.2 AA at every width from 320 CSS pixels up, in the light and dark themes.
This page records how that is checked, what the checks found, and what is still unverified.

## Automated checks

`tests/test_studio_accessibility_browser.py` drives headless Chrome over the served Studio and runs
the rule set in `tests/studio_a11y_checks.js` on every routed screen: the Hub, first run at
`/setup`, Configure, Library, Experiments, a run page, Activity, Reports, rule health, usage and
trends. Each screen is checked at 320, 390 and 1280 pixels wide in both themes. These populated
states are checked the same way, each served from a committed fixture under `studio/tests/fixtures/`
through one fetch stub:

- the three report screens;
- on Experiments: compare, the hook matrix, a paid tier's analysis, native acceptance progress, and
  a live replay with its progress table, result table and engine analysis;
- a run page with its exact command, cases and case evidence;
- Configure with a real draft loaded, its draft-test verdicts, the apply review and the apply
  result;
- Activity with an apply entry, its rollback preview, and a refused rollback's result.

Engine payloads are the files the frontend unit tests read; the other states are in
`a11y-states.json`, each entry naming the unit test or response type it is copied from. The stub
refuses any POST outside an allow-list of reads and the test fails on a refusal, so no audit starts
real work. The rules are hand-written because axe-core is MPL-2.0 and the repository takes no copyleft
dependency. The `studio-e2e` workflow runs the module with `STUDIO_E2E=1`, so a runner without
Chrome fails it instead of skipping it; the plain unit-suite run reaches it wherever Chrome exists.

| Rule | Success criterion |
|---|---|
| Page scrolls sideways at 320 px; the widest offending element is named | 1.4.10 Reflow |
| Text, SVG text by its `fill` and `fill-opacity`, a field's value and an empty field's placeholder against the composited background: 4.5:1, or 3:1 for large text, with no rounding | 1.4.3 Contrast |
| Field boundary or fill against its surroundings, 3:1 | 1.4.11 Non-text contrast |
| A colour the checker cannot decide (an image or gradient behind it) is reported, not passed | 1.4.3, 1.4.11 |
| Every control has an accessible name; a content-named control's name, and a labelled field's name, contain the visible text or label | 4.1.2, 2.5.3 |
| Targets are 24 by 24 px or spaced 24 px apart, inline links excepted | 2.5.8 Target size |
| One main landmark, all content inside a landmark, one `h1`, no skipped heading level | 1.3.1, 2.4.6 |
| ARIA references resolve; images, canvases and progress bars are named | 1.1.1, 4.1.2 |
| A scrolling region is a Tab stop, with a name on a region or landmark role | 2.1.1, 4.1.2 |
| Trend chart labels are at least 11 px as drawn, at every width; a phone scrolls the chart inside its region | 1.4.4, 1.4.10 |
| `Tab` from the skip link reaches every focusable control (compared as sets) and leaves the page; each stop's look changes from its unfocused state, by a ring of 3:1 against what it paints over that clipping ancestors leave at least half of, or a shadow of 3:1, and is not covered. Walked at 320 px light and 1280 px dark, and on each populated state at 320 px | 2.1.1, 2.1.2, 2.4.7, 2.4.11 |

Two planted-defect tests prove the rules fire before the real screens are trusted: one plants a
defect for every rule the checker can report, and asserts that list against the checker's own
source, and the other plants a clipped ring, a static shadow, a covered control, a skipped control
and a control that keeps `Tab`.

## Token contrast

Measured from the tokens in `studio/src/styles.css`. In `studio/src/theme.ts` a Mantine palette
name (`red`, `teal`, `yellow`, `gray` and the rest) given to a component's `color` or `c` resolves to
these tokens, as do the palette's text, light, outline and filled variables and the placeholder
colour. A numbered shade such as `red.7` used directly as a CSS variable still draws Mantine's own
palette; no Studio component does that today, and the rendered checks would report one that fell
under AA.

| Pair | Use | Floor | Light | Dark |
|---|---|---|---|---|
| ink-primary on surface-canvas | body text | 4.5:1 | 13.83:1 | 15.85:1 |
| ink-primary on surface-raised | body text | 4.5:1 | 14.77:1 | 13.93:1 |
| ink-secondary on surface-canvas | secondary text | 4.5:1 | 6.03:1 | 9.49:1 |
| ink-secondary on surface-raised | secondary text | 4.5:1 | 6.44:1 | 8.34:1 |
| primary on surface-raised | links, eyebrow | 4.5:1 | 7.06:1 | 9.62:1 |
| primary on surface-canvas | links | 4.5:1 | 6.61:1 | 10.95:1 |
| primary on primary-wash | current page, info | 4.5:1 | 6.26:1 | 7.11:1 |
| primary-foreground on primary | filled primary | 4.5:1 | 7.06:1 | 9.22:1 |
| success on success-wash | success | 4.5:1 | 6.43:1 | 7.76:1 |
| warning on warning-wash | warning, stale | 4.5:1 | 6.27:1 | 7.66:1 |
| danger on danger-wash | danger, refused | 4.5:1 | 5.45:1 | 7.29:1 |
| ink-secondary on primary-wash | neutral on wash | 4.5:1 | 5.72:1 | 6.17:1 |
| control-border on surface-raised | control boundary | 3:1 | 3.73:1 | 5.10:1 |
| control-border on surface-canvas | control boundary | 3:1 | 3.50:1 | 5.80:1 |
| primary on surface-raised | focus ring | 3:1 | 7.06:1 | 9.62:1 |
| primary on surface-canvas | focus ring | 3:1 | 6.61:1 | 10.95:1 |

## Six flows

| Flow | Keyboard | Screen reader |
|---|---|---|
| 1. Open the Studio | Not completed by a person. Automated: every screen's controls reached by `Tab` with visible focus | Not yet run |
| 2. Tune a draft | Not completed by a person. Automated: Configure reached by `Tab` empty and with a reviewed draft | Not yet run |
| 3. Apply and roll back | Not completed by a person. Automated: the apply review and the rollback preview reached by `Tab`; the apply and refused-rollback results audited | Not yet run |
| 4. Run a suite | Not completed by a person. Automated: populated Experiments panels and a populated run page reached by `Tab` | Not yet run |
| 5. Judge a change | Not completed by a person. Automated: the populated comparison audited and walked at 320 px | Not yet run |
| 6. First run | Not completed by a person. Automated: `/setup` reached by `Tab` | Not yet run |

The automated column records reach and visible focus, not completion: the Tab walk proves every
control on a screen can be reached and seen, not that a flow can be finished. A flow counts as
passed only when a person completes it with the keyboard alone and with a screen reader, and both
columns stay open until that pass is recorded here.

## Known limits

- The checks enforce the AA target floor of 24 px. The UX specification asks for 44 px at every
  width; links and buttons have a 44 px minimum height, but no check enforces 44 px yet.
- AC 1 remains open for two states. A completed rollback's result view is never audited, because
  the Activity timeline reloads at once and replaces it, so the refused result stands in. The
  draft-test plan, which needs a planned paired test, is not reached either.
- The populated states are fixtures, not engine runs; flow 3 runs against the real engine only in
  `tests/test_e2e_studio_flows.py`, without the accessibility rules.

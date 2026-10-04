# Studio accessibility audit

The Studio targets WCAG 2.2 AA at every width from 320 CSS pixels up, in the light and dark themes.
This page records how that is checked, what the checks found, and what is still unverified.

## Automated checks

`tests/test_studio_accessibility_browser.py` drives headless Chrome over the served Studio and runs
the rule set in `tests/studio_a11y_checks.js` on every routed screen: the Hub, first run at
`/setup`, Configure, Library, Experiments, a run page, Activity, Reports, rule health, usage and
trends. Each screen is checked at 320 and 1280 pixels wide in both themes, the three report screens
again in their populated state from the committed fixtures under `studio/tests/fixtures/`, and
Configure again with a draft loaded. The rules are hand-written because axe-core is MPL-2.0 and the
repository takes no copyleft dependency.

| Rule | Success criterion |
|---|---|
| Page scrolls sideways at 320 px; the widest offending element is named | 1.4.10 Reflow |
| Text against its composited background, 4.5:1 or 3:1 for large text | 1.4.3 Contrast |
| Field boundary or fill against its surroundings, 3:1 | 1.4.11 Non-text contrast |
| Every control has an accessible name; a content-named control's name contains its visible text | 4.1.2, 2.5.3 |
| Targets are 24 by 24 px or spaced 24 px apart, inline links excepted | 2.5.8 Target size |
| One main landmark, all content inside a landmark, one `h1`, no skipped heading level | 1.3.1, 2.4.6 |
| ARIA references resolve; images, canvases and progress bars are named | 1.1.1, 4.1.2 |
| A scrolling region is a Tab stop with a name | 2.1.1 |
| `Tab` from the skip link reaches every stop, each with a visible ring, none hidden, and leaves the page | 2.1.1, 2.1.2, 2.4.7, 2.4.11 |

A planted-defect test proves each rule fires before the real screens are trusted.

## Token contrast

Measured from the tokens in `studio/src/styles.css`. Mantine palette names (`red`, `teal`,
`yellow`, `gray` and the rest) resolve to these tokens in `studio/src/theme.ts`, so no component
draws from Mantine's own palette.

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
| 1. Open the Studio | Passed, automated: every screen | Not yet run |
| 2. Tune a draft | Passed, automated: Configure with a loaded draft | Not yet run |
| 3. Apply and roll back | Screens passed, automated; the confirm and rollback steps not walked by keyboard | Not yet run |
| 4. Run a suite | Passed, automated: Experiments and a run page | Not yet run |
| 5. Judge a change | Screens passed, automated; a populated comparison not walked | Not yet run |
| 6. First run | Passed, automated: `/setup` | Not yet run |

A flow counts as passed only when a person completes it with the keyboard alone and with a screen
reader. The screen-reader column stays open until that pass is recorded here.

## Known limits

- The checks enforce the AA target floor of 24 px. The UX specification asks for 44 px at every
  width; links and buttons have a 44 px minimum height, but no check enforces 44 px yet.
- Populated compare, replay, evaluation-tier, apply and rollback states need run evidence the test
  home does not have, so only their empty and loading states are checked.

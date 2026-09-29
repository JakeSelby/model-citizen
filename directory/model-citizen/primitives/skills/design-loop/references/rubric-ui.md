# UI rubric

For web apps, iOS/Mac apps, landing pages, dashboards and standalone HTML documents.

## Scored axes — 10 total

### Hierarchy and composition (0-3)

Does the eye land on the most important thing first, and travel in the intended order? Judge
spacing rhythm (is there a consistent scale, or arbitrary values), alignment (do edges actually
line up, including optical alignment), density (is it cramped or is it floating in dead space),
and grouping (does whitespace communicate relatedness).

Reaching for: a clear primary element, deliberate secondary and tertiary tiers, and nothing
competing for the same rank.

### Type and color (0-3)

Type scale coherence — a real scale, not seven arbitrary sizes. Weight and size doing hierarchy
work rather than color alone. Line length in a readable range and leading that matches the size.

Palette discipline — a small committed set, not accumulated one-off values. Color carrying meaning
consistently. Sufficient contrast between foreground, background and accent that the contrast gate
passes on its own merits rather than by tweaking one value at the end.

### Surface and depth (0-2)

Elevation, borders, corner radii and shadows applied as a coherent system. Every surface at a
given depth should look like it is at that depth. Shadows should match a single implied light
source and be soft enough not to read as a dark rectangle.

Losing points: muddy layering, borders and shadows both doing the same job, radii that vary
without reason, gradients that band.

### Detail and state (0-2)

Go over it with a fine-toothed comb. Icon optical alignment and consistent stroke weight. No
half-pixel edges, no ragged wrapping, no orphaned words, no clipped descenders.

Then the states that carry real product quality: **hover, focus, active, disabled, empty, loading,
error**. A design that only looks good in its happy, fully-populated state is not finished. Empty
and error states are where most UIs visibly fall apart.

## Hard gates — pass/fail, not scored

- **Contrast meets WCAG AA.** 4.5:1 for body text, 3:1 for large text and meaningful UI borders.
  Measure it; do not eyeball it. This is never traded for aesthetics.
- **Focus is visible** on every interactive element, and keyboard order is sane.
- **Design tokens and primitives are used**, not ad-hoc values. Whatever the project's token
  package and primitive library are, use them. A beautiful screen built from hardcoded hex values
  and magic numbers fails this gate.
- **Responsive at the target breakpoints**, with no horizontal scroll and no overlap at the
  narrowest supported width.
- **Both themes correct**, where the surface supports light and dark.
- **Content extremes do not break it** — longest realistic string, empty list, one item, many.

## Notes

The target does not outrank the design system. When a generated mockup wants something the tokens
do not offer, **the system wins**; record the conflict rather than forking the palette.

For HTML documents meant to read as siblings of previously shipped ones, matching the existing
family is itself a gate. Novel styling that looks good standalone but breaks the set has failed.

# Capturing the current state

**Verify the capture command works before round 1.** A loop that discovers in round 3 that it has
been judging a blank page or a stale image has burned every round before it.

Each capture must be of the **real running thing**, at the same framing and viewport as the
target. Changing framing between rounds makes scores meaningless.

## Find the project's own path first

Look for an existing way to run and capture before building one:

- A project skill covering launch or rendering
- Claude Code's built-in `run` skill, which launches the app and can screenshot it
- Existing render or gallery scripts in the repo

Use what exists. A one-off script that duplicates the repo's renderer will drift from it.

## By surface

| Surface | Path |
| --- | --- |
| **Web / HTML doc** | Headless browser screenshot at a fixed viewport. Capture each required breakpoint and both themes as separate images. |
| **iOS / Mac app** | Simulator screenshot, or the OS screenshot utility against a running build. Fixed device and scale factor. |
| **Blender** | Blender MCP `get_viewport_screenshot`, or a scripted render. Read the MCP server instructions before scripting. |
| **Project renderer or mock tool** | The repo's own capture scripts, if it has them. Label output **Mock** or **Renderer** so a concept image is never mistaken for the shipped surface. |
| **Game at play zoom** | Capture at the actual zoom levels the player uses, not only hero framing. The readability axis depends on this. |

## Known gaps

**A web project with no screenshot tooling cannot run this loop yet.** Standing up a headless
browser capture is a prerequisite and it is real work — raise it rather than faking a capture from
a component gallery or a static mockup.

## Capture rules

- **One capture per round, saved as `.design-loop/round-<n>.png`.** Keep them all; the judge needs
  the previous one and you need the series to detect a stall.
- **Multi-image surfaces** — breakpoints, themes, zoom levels — pass the full set to the judge each
  round. Do not rotate which one you show; that hides regressions.
- **Never substitute a mockup, a component gallery or a prior render** for a live capture. The
  entire value of the loop is that it measures the real artifact.
- **Fix breakage before capturing**, not after. Failed asset loads, wrong orientation and missing
  fonts produce a capture that wastes a judge round on gaps you already know about.

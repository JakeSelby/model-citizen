# The judge

## Why it is a separate agent

The agent that built the thing has already decided the thing is good — it chose every value in it.
It also carries the whole build conversation, so it scores its own reasoning rather than the
pixels. A judge with **no build context** sees only the two images.

Spawn the **`design-judge` agent** with the image paths, the surface type and the target path — a
fresh one every round; never fork it and never reuse the previous judge.

## What to pass it

Absolute paths, all of them, every round:

- The locked target
- The current capture
- **The previous round's capture and verdict**, if any

The prior-verdict handoff is what makes regressions visible. Without it the judge cannot tell
improvement from drift, and scores wander.

Name the mode — `ui` or `scene` — and the agent reads its own rubric. Do not tell it what you
changed, what you intended, or what you found hard. That is the context you are paying to keep out.

## Handling the verdict

Write it to `.design-loop/verdict-<n>.md` verbatim. You need the history to detect a stall.

**Address every gap, hardest first.** Cherry-picking the cheap ones is how a loop stalls at 6/10:
the expensive gap survives every round and the judge keeps naming it.

The judge can be wrong. If a gap contradicts a hard gate, the design system or real-world scale,
**the gate wins** — note the disagreement in `notes.md` and move on. Do not argue with the judge
by re-running it on the same state hoping for a better number.

## Relaying to the user

Judge output is a subagent's raw prose and does not follow the house output style. **Never paste
it through verbatim.** Reformat before it reaches the user: score and verdict first, gate failures
next, then what you changed and what is still open.

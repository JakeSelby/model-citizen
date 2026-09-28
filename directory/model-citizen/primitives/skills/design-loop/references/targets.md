# Establishing the target

The target is the whole point. A loop without a locked external reference degenerates into the
agent grading its own taste, which is exactly the failure this skill exists to prevent.

**Lock it before round 1 and never regenerate it mid-loop.** If the target turns out to be wrong,
stop the loop, say so, agree a new one, and restart the round count.

## Where a target comes from, best first

### 1. The user supplied one

Use it directly. A mockup, a screenshot they like, a Figma export, a competitor's screen, a prior
approved render. Do not "improve" it first.

### 2. An approved artifact already in the repo

A blessed prior render, an existing screen whose quality bar you are matching, a sibling document
whose styling the new one must match. This is the right default for **anything that must read as
part of a set** — matching a design system, or an HTML doc that has to look like a sibling of the
ones shipped before it.

Cheap and self-bootstrapping, but it can only ratchet toward what already exists. Say so when the
brief is asking for a genuine step change.

### 3. A human-authored direction plate

For a real step change, ask for one rather than inventing it. One plate per asset class or screen
type sets a far higher ceiling than any generated frame.

### 4. Generate one

Last, and mode-dependent. See the constraints below.

## Refining rather than diverging

If the thing already exists, **capture it first and feed that capture to the image model as the
base**, asking for a refined version along the user's direction. Generating from the prompt alone
produces a target that diverges instead of improving, and the loop then spends every round
fighting to become a different product.

## Prompting for a generated target

Prompt for an **exact, realistic target screenshot** of the finished thing. Not concept art, not
a cinematic shot, not an artist's interpretation, not a mood board. You are going to try to match
it closely, so it must depict something buildable.

Avoid the words "concept art", "artistic", "illustration" and "painting" in the prompt.

## Mode constraints

### ui

Generating a target is fine. A mockup is a reference that gets looked at and thrown away — no
generated pixels ship.

But the target does not outrank the design system. Where a generated mockup conflicts with the
project's tokens, primitives or accessibility gates, **the system wins and the target is wrong on
that point**. Note the conflict in `notes.md` rather than silently following either one.

### scene

**A generated frame may set direction. It may never be a source for shipped geometry or texture.**
Prefer an approved reference plate or a prior blessed render as the target.

Two reasons, both load-bearing:

- **Licensing.** Generated image → image-to-3D → shipped asset is a derivation chain on unresolved
  terms. The chosen `licensing` stance keeps unresolved material out of production.
- **Correctness.** An image model does not respect real-world scale, LOD budget or readability at
  playable zoom. Matching its frame produces a beautiful hero shot that is wrong in the game.

Where the target and real-world scale disagree, **scale wins**.

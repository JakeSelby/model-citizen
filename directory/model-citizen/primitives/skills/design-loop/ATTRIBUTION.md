# Attribution

Recorded per the chosen `licensing` stance (`~/.claude/rules/harness-stances/licensing.md`) and
the `licensing-review` procedure. This skill is a
development tool and is not shipped in any product; the record is kept because the policy applies
to copied material regardless of destination.

| Field | Value |
| --- | --- |
| **Source** | https://github.com/achimala/dream-loop |
| **Creator** | Anshu Chimala |
| **Version** | Repository state as of commit range ending 2026-09-09 (reviewed 2026-09-15) |
| **Licence** | MIT — permits commercial use, modification and redistribution, requires the copyright and permission notice be preserved |
| **Licence text** | Reproduced below |
| **What was taken** | The loop structure (locked target → build → capture → independent judge → fix → exit), the fresh-context judge with a scored rubric and prior-verdict handoff, the stall-detection exit criterion, and the "name the cause and the fix, not the symptom" constraint on judge feedback |
| **What was not taken** | No code. `scripts/fal-batch.mjs`, `scripts/preview-server.py` and `references/fal.md` were deliberately excluded |

## Modifications

- **Rubrics rewritten.** The original scores Composition / Lighting / Materials / Details for 3D
  only. Ours adds a UI rubric, and replaces the 3D "Details" axis with **readability at game
  zoom**.
- **Hard gates added.** Accessibility, design-token adherence, runtime budget, real-world scale and
  asset licensing are pass/fail and block exit at any score. The original has no equivalent.
- **Objective changed.** The original targets pixel-identity — *"not a single pixel should be
  different"*. Ours treats the target as a reference that loses to the design system, to real-world
  scale and to accessibility where they conflict.
- **Asset sourcing removed entirely.** The original ranks internet download first and image-to-3D
  second, with no licence check, and explicitly instructs the agent to override a user's
  no-downloads restriction. Both are incompatible with the chosen `licensing` stance and any
  path-scoped asset rule a project carries; sourcing defers to those rules instead.
- **Round budget and escalation added**, aligned to the standing autonomous-loop escalation rule.
- **Tier-based workflow selection dropped.** The original branches on ChatGPT Plus vs Pro
  subscription tier and includes a Codex-specific orchestration mode. Ours branches on surface.

## MIT Licence

```
MIT License

Copyright (c) 2026 Anshu Chimala

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

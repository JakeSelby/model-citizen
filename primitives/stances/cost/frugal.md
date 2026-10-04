# Cost stance: frugal

Session effort runs **low** unless the task is design or adversarial review. Subagents are gatherers only, fan-out no
wider than three, and fast mode is never used. Agent teams are off. End a task with `/clear`, never `/compact`. Past
160,000 tokens of context the feed says a call costs more; **past 200,000 the turn's end is blocked once: write the
handoff, then start a fresh session.** Before a model call, try a deterministic command — the test, the linter, the
exit code. The table prices each role and band: class, effort, budget. Tier comes from `delegation`. Cache: `cache-hygiene.md`.

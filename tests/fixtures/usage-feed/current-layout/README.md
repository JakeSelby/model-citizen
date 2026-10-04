# Current subagent layout (Claude Code 2.1)

Synthetic, hand-written to the shape Claude Code 2.1 writes; no content is copied from a real
session. `projects/<project>/<session>/subagents/agent-<id>.jsonl` is a spawned agent's transcript,
with its type in the `.meta.json` beside it. `events.json` is the order the hooks see in one turn:
the spawned agent's start and stop, the main session's `Stop`, then a `SubagentStop` with no start
before it, no agent type and no transcript on disk. That last one is Claude Code's own end-of-turn
agent, not a subagent anyone spawned, and the usage feed must not report it as `spend unknown`.
`test_usage_feed_internal.py` replays these events; the hook fills in `session_id` and the paths.

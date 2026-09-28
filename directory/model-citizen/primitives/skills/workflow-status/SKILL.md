---
name: workflow-status
description: Show who has returned, who is still working and how much output a background Workflow run produced. Use when the user asks about workflow progress, says "/workflows doesn't work", asks "is the workflow done", "how's the workflow going", "check the workflow", or wants to inspect a multi-agent orchestration run.
---

# Workflow status

For harness-owned isolated role workers, run `citizen role status` or
`citizen role status <worker-id>`. Report status, runtime, role and result path from those records.
They are separate CLI processes and do not appear as native subagent threads.

This reader inspects local Claude workflow journals from any client with filesystem access.
It does not inspect Codex native agent threads or hosted runs. For those, use the client's native
agent view; an empty local journal search does not mean no agents are running.

## Run it

```bash
python3 "{skill-root}/scripts/status.py"            # most recent run
python3 "{skill-root}/scripts/status.py" --all      # every run, newest first
python3 "{skill-root}/scripts/status.py" --limit 3  # the last three
python3 "{skill-root}/scripts/status.py" --run wf_eed4141a   # one specific run
```

Report the output to the user in prose — the counts, what is still in flight, and roughly how
far along the run is. Do not paste the raw table unless they ask for it.

## What it reads, and what it must not

The script reads:

- `~/.claude/projects/*/*/subagents/workflows/wf_*/journal.jsonl` — one line per agent start and
  one per agent result. This is the authoritative record of what has returned.
- the **first line only** of each `agent-*.jsonl`, to recover a human-readable identity (the
  workflow's `label` option is not persisted, so the agent's opening prompt is the best available
  name).
- the persisted script under `workflows/scripts/`, for the declared phase titles.

🛑 **Never read a full `agent-*.jsonl` transcript.** They routinely run to megabytes and will
overflow the context window. The journal plus first lines is always enough for status. If the user
wants an agent's actual findings, wait for the workflow to complete and read its returned result,
or read the file the workflow wrote — not the transcript.

## Interpreting it

- **`✓` vs `•`** — an agent whose transcript has been quiet for more than ~45 seconds has almost
  certainly returned; one still being written to is working. The journal's result count is the
  authoritative figure, and the two can briefly disagree while a result is being flushed.
- **`RUNNING` with no recent activity** across every agent usually means the run is between
  phases (a barrier), or that the parent is synthesising.
- **Growing transcript size** is a good sign for a research agent — it means real tool use
  (fetching, searching) rather than answering from memory.
- A run whose journal shows fewer `started` entries than there are `agent-*.jsonl` files is
  mid-fan-out; more agents are still being spawned.

## When a run has finished

The workflow's own completion notification carries the returned value, which is the thing to
report. This skill is for the interval before that arrives — or for checking on a run from a
different session, since the journal persists on disk.

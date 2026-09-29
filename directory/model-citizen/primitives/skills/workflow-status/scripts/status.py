#!/usr/bin/env python3
"""
Progress for background Workflow runs, reconstructed from the run journal.

`/workflows` is a terminal-only view; this reads the same underlying data so it works
anywhere (VS Code, web, a piped shell). It deliberately never reads a full agent
transcript — those run to megabytes — only the journal plus the first line of each
agent's transcript, which carries its prompt and is enough to name it.

Usage:
    status.py                 # most recent run
    status.py --all           # every run found, newest first
    status.py --run wf_abc123 # a specific run
    status.py --limit 5       # cap how many runs are shown
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"
SCRIPTS_GLOB = "workflows/scripts/*.js"


def find_runs() -> list[Path]:
    """Every workflow run directory on this machine, newest first."""
    runs = [p for p in PROJECTS.glob("*/*/subagents/workflows/wf_*") if p.is_dir()]
    return sorted(runs, key=lambda p: p.stat().st_mtime, reverse=True)


def read_journal(run: Path) -> tuple[list[str], dict[str, int], dict[str, str]]:
    """Started keys, result lengths and agent states, using journal IDs rather than file age."""
    journal = run / "journal.jsonl"
    if not journal.exists():
        return [], {}, {}

    started: list[str] = []
    results: dict[str, int] = {}
    identities: dict[str, str] = {}
    states: dict[str, str] = {}
    for line in journal.read_text(errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        if not isinstance(key, str) or not key:
            continue
        supplied_id = entry.get("agentId")
        if entry.get("type") == "started":
            if isinstance(supplied_id, str) and supplied_id:
                identities[key] = supplied_id
        agent_id = identities.get(key)
        if entry.get("type") != "started" and (
                not agent_id or ("agentId" in entry and supplied_id != agent_id)):
            continue
        if entry.get("type") == "started":
            if key not in started:
                started.append(key)
            results.pop(key, None)
            if agent_id:
                states[agent_id] = "pending"
        elif entry.get("type") == "result":
            payload = entry.get("result")
            if agent_id and isinstance(payload, str):
                results[key] = len(payload)
                states[agent_id] = "returned"
        elif entry.get("type") == "failed" and agent_id:
            results.pop(key, None)
            states[agent_id] = "failed"
    return started, results, states


def agent_identity(path: Path) -> str:
    """
    A short human label for an agent, taken from the first line of its transcript.

    The workflow's `label` option is not persisted, so the prompt is the best available
    identity. We look for an explicit marker first, then fall back to the opening words.
    """
    try:
        with path.open(errors="replace") as handle:
            first = handle.readline()
        message = json.loads(first).get("message", {})
        content = message.get("content")
        if isinstance(content, list):
            text = " ".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        else:
            text = str(content or "")
    except Exception:
        return "(unreadable)"

    text = text.strip()
    # Identity comes from the OPENING of the prompt only. Later phases receive earlier phases'
    # output appended wholesale, so searching the whole body would label every downstream agent
    # with whatever marker happened to lead the digest it was handed.
    head = re.sub(r"\s+", " ", text[:400]).strip()

    marker = re.match(r"=====\s*[A-Z]+:\s*([\w-]+)", head)
    if marker:
        return marker.group(1)
    lead = re.match(r"(?:Deep web research:\s*)?(.{0,88})", head)
    snippet = (lead.group(1) if lead else head[:88]).strip()
    return snippet or "(empty prompt)"


def phases_from_script(run_id: str) -> list[str]:
    """Phase titles declared in the run's persisted script, when it can be found."""
    for script in PROJECTS.glob(f"*/*/{SCRIPTS_GLOB}"):
        if run_id in script.name:
            text = script.read_text(errors="replace")
            return re.findall(r"title:\s*'([^']+)'", text)
    return []


def human_age(seconds: float) -> str:
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"


def report(run: Path, terminal_status=None) -> None:
    run_id = run.name
    started, results, states = read_journal(run)
    agents = sorted(run.glob("agent-*.jsonl"), key=lambda p: p.stat().st_mtime)

    now = time.time()
    began = min((p.stat().st_ctime for p in agents), default=run.stat().st_ctime)
    last = max((p.stat().st_mtime for p in agents), default=run.stat().st_mtime)
    idle = now - last

    done = len(results)
    total = max(len(started), len(agents))
    chars = sum(results.values())
    state = terminal_status.upper() if terminal_status else "UNKNOWN (no terminal signal)"

    print(f"\n\033[1m{run_id}\033[0m  —  {state}")
    print(f"  started {human_age(now - began)} ago · last activity {human_age(idle)} ago")
    print(f"  agents: {done}/{total} returned · {chars:,} chars of output")

    phases = phases_from_script(run_id)
    if phases:
        print(f"  phases: {' → '.join(phases)}")

    print()
    for path in agents:
        size = path.stat().st_size
        age = now - path.stat().st_mtime
        status = states.get(path.stem.removeprefix("agent-"), "unknown")
        mark = "\033[32m✓\033[0m" if status == "returned" else "\033[33m•\033[0m"
        status += f"; activity {human_age(age)} ago"
        print(f"  {mark} {agent_identity(path)[:74]:<74} {size / 1024:>7.0f}KB  {status}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="show every run")
    parser.add_argument("--run", help="a specific run id (wf_...)")
    parser.add_argument("--limit", type=int, default=1, help="how many runs to show")
    parser.add_argument("--terminal-status", choices=("completed", "failed", "cancelled"),
                        help="status explicitly reported by the runtime, never inferred from age")
    args = parser.parse_args()
    if args.terminal_status and not args.run:
        parser.error("--terminal-status requires an exact --run")

    runs = find_runs()
    if not runs:
        print("No workflow runs found under ~/.claude/projects/*/*/subagents/workflows/")
        return 1

    if args.run:
        runs = [r for r in runs if args.run == r.name]
        if not runs:
            print(f"No run matching {args.run!r}.")
            return 1
    elif not args.all:
        runs = runs[: max(1, args.limit)]

    for run in runs:
        report(run, args.terminal_status)
    return 0


if __name__ == "__main__":
    sys.exit(main())

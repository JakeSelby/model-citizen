#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""PreToolUse policy: stop an edit to a path a live sibling session has claimed.

Answers for Edit, Write, MultiEdit and NotebookEdit through the lifecycle coordinator. The ledger,
the ownership rule and the `coordination.repeat_overlap` variant are `lib/harness_core/intents.py`;
this file only turns an overlap into a PreToolUse answer and a decision-log row.

Under `deny`, the default, the first hit on a path in a session warns and the second denies;
under `warn` every hit warns. A warning is context for the agent and a notice for the user,
never a permission decision, so the user's own permission flow still answers for the edit.
Shell-mediated writes are `grade-bash`'s to judge, not this file's.

Silent, and never failing the edit, when nothing overlaps or anything here cannot be loaded.

Test: echo '{"tool_name":"Edit","session_id":"s","cwd":"'"$PWD"'","tool_input":{"file_path":"'"$PWD"'/README.md"}}' | python3 intent-overlap.py
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[2] / "lib" / "harness_core" / "intents.py"


def ledger():
    try:
        spec = importlib.util.spec_from_file_location("harness_intents", str(LIB))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def target(event):
    inputs = event.get("tool_input") or {}
    path = inputs.get("file_path") or inputs.get("notebook_path") or inputs.get("path")
    if not isinstance(path, str) or not path:
        return None
    full = Path(path)
    return full if full.is_absolute() else Path(event.get("cwd") or os.getcwd()) / full


def judge(event, intents, env=None):
    """The PreToolUse output for one edit, or None when nothing overlaps."""
    if event.get("tool_name") not in intents.EDIT_TOOLS:
        return None
    path = target(event)
    if path is None:
        return None
    session = str(event.get("session_id") or "")
    pid = intents.runtime_pid(env)
    # Only a Claude Code subagent's payload carries `agent_id`, and its `cwd` is its parent's. A
    # hook run by hand has no coordinator and is Claude Code's; a Codex payload keeps the `cwd` rule.
    runtime = (os.environ if env is None else env).get("HARNESS_RUNTIME", "claude-code")
    subagent = runtime == "claude-code" and bool(event.get("agent_id"))
    found = intents.overlaps(path, session or None, pid, event.get("cwd"), env, subagent)
    if not found:
        return None
    first = found[0]
    variant = intents.overlap_variant(env)
    target_root = (intents.repository(path) or {}).get("root", "")
    worktree = intents.editor_root(event.get("cwd"), target_root, subagent) or ""
    answer = intents.answer_for(intents.hit(session, worktree, first[2], env), variant)
    intents.log_overlap(answer, first, str(event.get("tool_name")), session, variant,
                        os.environ.get("HARNESS_RUNTIME", ""))
    what = intents.describe(first)
    if answer == "deny":
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": "intent-overlap: " + what + ", and this session was "
            "already warned about it. Do not write it: report the overlap to whoever assigned "
            "the work, and let them sequence the two sessions."}}
    return {"systemMessage": "intent-overlap: " + what + ".",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": "intent-overlap warning: " + what + ". A second edit to it "
                + ("in this session is denied" if variant == "deny" else "is warned again")
                + ". Prefer a new file or leave it to that session, and report the overlap to "
                "whoever assigned the work."}}


def main():
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    if not isinstance(event, dict):
        return
    intents = ledger()
    if intents is None:
        return
    try:
        result = judge(event, intents)
    except Exception:
        return
    if result:
        print(json.dumps(result))


if __name__ == "__main__":
    main()

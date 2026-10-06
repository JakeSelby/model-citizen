#!/usr/bin/env python3
"""PreToolUse hook: pipe verbose test, build and type-check runs through
`filter-lines.py`, so only failures, the summary and the tail of a run enter the
transcript.

Why a hook and not an instruction: by the time the model could decide to read the
output narrowly, the whole run is already in context and every later turn pays for
it. Trimming it is a computational job.

Behaviour:
  - Rewrites a command that runs one of MATCHES, after leading environment
    assignments and `cd ... &&` prefixes are stripped.
  - Leaves the command alone when it is already filtered, already pipes to a
    pager, redirects to a file, or watches.
  - Emits `updatedInput` only. It never returns a permission decision, so the
    normal permission flow and any other PreToolUse hook on Bash are untouched.
  - `set -o pipefail` keeps the original exit status; the filter always exits 0.

Test: echo '{"tool_name":"Bash","tool_input":{"command":"pytest -q"}}' | python3 filter-output.py
"""
import json
import re
import shlex
import sys
from pathlib import Path

MATCHES = [
    "pytest", "python -m pytest", "python3 -m pytest", "python -m unittest",
    "python3 -m unittest", "uv run pytest", "cargo test", "cargo check",
    "cargo clippy", "cargo build", "npm test", "npm run test", "pnpm test",
    "yarn test", "go test", "npx tsc", "tsc", "npx vitest run", "npx jest",
]
MATCH_PATTERNS = [
    re.compile(r"(?<![\w./-])" + r"\s+".join(re.escape(w) for w in m.split()) + r"(?![\w./-])")
    for m in MATCHES
] + [re.compile(r"(?:^|[\s/])(?:harness|citizen)\s+gate(?![\w-])")]  # the recorded gate run
ENV_PREFIX = re.compile(r"""^\s*[A-Za-z_][A-Za-z0-9_]*=(?:"[^"]*"|'[^']*'|[^\s;&|]*)\s+""")
CD_PREFIX = re.compile(r"""^\s*cd\s+(?:"[^"]*"|'[^']*'|[^\s;&|]+)\s*&&\s*""")
PAGED = re.compile(r"\|\s*(?:head|tail|grep|less|wc)\b")
REDIRECT_TO_FILE = re.compile(r">>?\s*(?!&)\S")
WATCH = re.compile(r"(?<![\w-])--watch(?![\w-])")
MAX_COMMAND = 10000


def strip_prefixes(cmd):
    prev = None
    while prev != cmd:
        prev = cmd
        cmd = CD_PREFIX.sub("", ENV_PREFIX.sub("", cmd))
    return cmd


def should_filter(cmd):
    if not cmd or len(cmd) > MAX_COMMAND:
        return False
    if "filter-lines.py" in cmd:
        return False
    if PAGED.search(cmd) or WATCH.search(cmd) or REDIRECT_TO_FILE.search(cmd):
        return False
    stripped = strip_prefixes(cmd)
    return any(p.search(stripped) for p in MATCH_PATTERNS)


def rewrite(cmd):
    filt = Path(__file__).resolve().parent / "filter-lines.py"
    return "set -o pipefail; ( %s ) 2>&1 | python3 %s" % (cmd, shlex.quote(str(filt)))


def main():
    try:
        payload = json.load(sys.stdin)
        if payload.get("tool_name") != "Bash":
            return
        tool_input = payload.get("tool_input") or {}
        cmd = tool_input.get("command")
        if not isinstance(cmd, str) or not should_filter(cmd):
            return
        updated = dict(tool_input)
        updated["command"] = rewrite(cmd)
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "updatedInput": updated,
            }
        }))
    except Exception:
        return


if __name__ == "__main__":
    main()

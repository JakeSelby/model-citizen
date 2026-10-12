#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""PostToolUse validator: enforce the Review Card contract on plan files.

Contract lives in the plan-authoring skill (~/.claude/skills/plan-authoring/SKILL.md).
Advisory only — this never fails a write; it feeds a correction back to the agent.
Each plan file checked is one `validate-plan-card` row in the decision log: `pass` or `fail`,
with the number of problems, the card's line count and the file's name as its input.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

CARD_CAP = 85
REQUIRED = ["## At a glance", "## Steps", "## Decisions for the reviewer"]
FORBIDDEN = ["## Context"]
SKILL_DIR = Path.home() / ".claude" / "skills" / "plan-authoring"


def log_decision(answer, text, payload, fields=None):
    """One `validate-plan-card` row in the decision log (`decisions.py`). Never raises."""
    try:
        location = Path(os.path.realpath(__file__)).parent / "decisions.py"
        spec = importlib.util.spec_from_file_location("harness_validate_plan_card_decisions", str(location))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.record("validate-plan-card", answer, text, payload if isinstance(payload, dict) else {},
                      fields=fields)
    except Exception:
        pass


def emit(problems, path):
    body = "\n".join("- " + p for p in problems)
    msg = (
        f"The plan file {os.path.basename(path)} does not meet the Review Card contract:\n"
        f"{body}\n\n"
        "Fix the file before showing it to the reviewer. The Review Card is everything above "
        "the first `---`: title, two-sentence verdict blockquote, `## At a glance` bullets, "
        "`## System design` diagram, `## Steps` with exit tests, "
        "`## Decisions for the reviewer`, `## Risks`. Background and detail belong below the "
        "rule under `# Addendum`. Full contract and template: "
        f"{SKILL_DIR / 'SKILL.md'} and {SKILL_DIR / 'TEMPLATE.md'}."
    )
    print(json.dumps({
        "systemMessage": f"Plan card check failed ({len(problems)}): {os.path.basename(path)}",
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": msg,
        },
    }))


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    ti = payload.get("tool_input") or {}
    tr = payload.get("tool_response") or {}
    path = tr.get("filePath") or ti.get("file_path") or ""
    if not path.endswith(".md"):
        return
    norm = path.replace(os.sep, "/")
    if "/.claude/plans/" not in norm and "/.agent-harness/plans/" not in norm:
        return
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return
    if not lines:
        return

    start = 0
    if lines[0].strip() == "---":  # tolerate YAML frontmatter
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                start = i + 1
                break

    end = None
    for i in range(start, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break

    problems = []
    if end is None:
        problems.append(
            f"No `---` separator, so the whole {len(lines)}-line file is the card. "
            "Put the detail below a `---` under `# Addendum`."
        )
        card = lines[start:]
    else:
        card = lines[start:end]
        if len(card) > CARD_CAP:
            problems.append(
                f"Card is {len(card)} lines, cap is {CARD_CAP} "
                "(70, plus up to five 3-line decisions). Move detail into the addendum."
            )

    fence = False
    table_lines = []
    for idx, raw in enumerate(lines[start:], start=start + 1):
        if raw.lstrip().startswith("```"):
            fence = not fence
        elif not fence and raw.lstrip().startswith("|"):
            table_lines.append(idx)
    if table_lines:
        where = ", ".join(str(n) for n in table_lines[:6])
        more = "" if len(table_lines) <= 6 else f" (+{len(table_lines) - 6} more)"
        problems.append(
            f"{len(table_lines)} markdown table lines at {where}{more}. A plan carries no "
            "tables, above or below the rule — one bullet per row instead."
        )

    text = "\n".join(card)
    for want in REQUIRED:
        if want not in text:
            problems.append(f"Card is missing `{want}`.")
    for bad in FORBIDDEN:
        if bad in text:
            problems.append(
                f"Card contains `{bad}`. Background goes in the addendum — the "
                "two-sentence verdict carries the why."
            )

    log_decision("fail" if problems else "pass", os.path.basename(path), payload,
                 {"problems": len(problems), "card_lines": len(card), "separator": end is not None})
    if problems:
        emit(problems, path)


if __name__ == "__main__":
    main()

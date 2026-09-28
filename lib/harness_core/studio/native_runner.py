"""Execute the native acceptance runner from one already-resolved immutable target."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--client", required=True)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--progress-id", required=True)
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--retry-source", required=True)
    parser.add_argument("--retry-case", required=True)
    parser.add_argument("--max-budget-usd", required=True)
    parser.add_argument("--spend-cap", required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    script = root / "scripts" / "native_acceptance.py"
    try:
        resolved = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
            timeout=5, check=False)
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True,
            timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return 2
    expected_progress = "native-%s.partial.jsonl" % args.progress_id
    expected_out = "native-%s.json" % args.progress_id
    expected_commit = "" if args.source_commit == "resolve-at-launch" else args.source_commit
    if (not script.is_file() or resolved.returncode or dirty.returncode
            or (expected_commit and resolved.stdout.strip() != expected_commit)
            or dirty.stdout.strip()):
        return 2
    if (args.progress.name != expected_progress or args.out.name != expected_out
            or args.progress.parent != args.out.parent):
        return 2
    retry = args.retry_source != "fresh" or args.retry_case != "fresh"
    if retry != (args.retry_source != "fresh" and args.retry_case != "fresh"):
        return 2
    command = [
        sys.executable, str(script), "--client", args.client, "--cases", args.cases,
        "--model", args.model, "--progress", str(args.progress), "--out", str(args.out),
        "--max-budget-usd", args.max_budget_usd, "--spend-cap", args.spend_cap,
    ]
    os.execv(sys.executable, command)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

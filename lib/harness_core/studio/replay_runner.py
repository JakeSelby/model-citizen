"""Allowlisted process adapter for a Studio live replay."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import replay

ROOT = Path(__file__).resolve().parents[3]


def write_spend_result(path: Path, run_id: str, summary: dict, stop_reason=None) -> None:
    cases = [{"id": item["id"], "status": item["status"], "spend_usd": item["spend_usd"]}
             for item in summary["cases"]]
    value = {"schema_version": 1, "run_id": run_id, "spend_usd": summary["spend_usd"],
             "cases": cases,
             "stop_reason": stop_reason or (
                 "spend_cap" if summary["stopped_at_cap"] else None)}
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-json", required=True)
    parser.add_argument("--repository")
    parser.add_argument("--out")
    parser.add_argument("--max-budget-usd", required=True)
    parser.add_argument("--spend-cap", required=True)
    args = parser.parse_args(argv)
    try:
        value = json.loads(args.request_json)
        if not isinstance(value, dict):
            raise replay.ReplayError("request must be a JSON object")
        value["max_budget_usd"] = args.max_budget_usd
        value["spend_cap_usd"] = args.spend_cap
        request = replay.ReplayRequest.parse(value)
        result = os.environ.get("CITIZEN_STUDIO_RESULT")
        run_id = os.environ.get("CITIZEN_STUDIO_RUN_ID")
        if not result or not run_id:
            raise replay.ReplayError("Studio replay needs its run identity and result path")
        repository = Path(args.repository).resolve() if args.repository else ROOT
        output = Path(args.out).resolve() if args.out else Path(result).resolve().parent / "replay"
        try:
            summary = replay.execute(request, repository, output)
        except replay.ReplayExecutionError as exc:
            write_spend_result(Path(result), run_id, exc.summary, "runner_failure")
            raise
        write_spend_result(Path(result), run_id, summary)
        return 1 if summary["stopped_at_cap"] else 0
    except (OSError, ValueError) as exc:
        print("studio-replay: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

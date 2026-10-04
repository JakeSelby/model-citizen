#!/usr/bin/env python3
"""Reads a test, build or log run on stdin and prints only the lines that carry
information: failures, tracebacks and what follows them, summary lines, and the
tail of the run.

Invoked by `filter-output.py`, which rewrites a matching Bash command to pipe
through it. Always exits 0, so the `pipefail` pipeline reports the status of the
command being filtered.

With `--session <id>`, which the rewrite passes, each run is one `filter-output`
row in the decision log: `filtered`, or `unchanged` when too little matched and
the whole run was kept, with `bytes_in`, `bytes_out`, `lines_in`, `lines_out` and
the `runner` the rewrite matched. The input is the runner's name, never the run's
output. Without it, as when run by hand, nothing is logged.
"""
import importlib.util
import os
import re
import sys
from pathlib import Path

KEEP = re.compile(
    r"FAILED|FAIL:|ERROR|error\[|error:|panicked|Traceback|AssertionError|assert |✗|✘|not ok|warning: unused"
)
CONTEXT_AFTER = re.compile(r"Traceback|panicked")
SUMMARY = [
    re.compile(r"^=+ .* =+$"),
    re.compile(r"^Ran \d+ tests"),
    re.compile(r"^test result:"),
    re.compile(r"^Tests:"),
    re.compile(r"^ok\b|^FAILED\b"),
]
COUNTED = re.compile(r"passed|failed")
DIGIT = re.compile(r"\d")

CONTEXT_LINES = 3
TAIL_LINES = 20
MIN_KEPT = 5
CAP = 200


def is_summary(line):
    if any(p.search(line) for p in SUMMARY):
        return True
    return bool(COUNTED.search(line) and DIGIT.search(line))


def select(lines):
    keep = set()
    for i, line in enumerate(lines):
        if KEEP.search(line) or is_summary(line):
            keep.add(i)
        if CONTEXT_AFTER.search(line):
            keep.update(range(i + 1, min(i + 1 + CONTEXT_LINES, len(lines))))
    keep.update(range(max(0, len(lines) - TAIL_LINES), len(lines)))
    return [lines[i] for i in sorted(keep)]


def filter_text(text):
    lines = text.splitlines()
    kept = select(lines)
    if len(kept) < MIN_KEPT:
        kept = lines
    if len(kept) > CAP:
        half = CAP // 2
        kept = kept[:half] + ["[filter-lines: %d lines omitted]" % (len(kept) - CAP)] + kept[-half:]
    return "\n".join(kept)


def option(argv, name):
    """The value after `name` in `argv`, or None."""
    if name in argv:
        index = argv.index(name)
        if index + 1 < len(argv):
            return argv[index + 1]
    return None


def log_run(argv, text, out):
    """One `filter-output` decision row for this run, when the rewrite named a session."""
    session = option(argv, "--session")
    if not session:
        return
    try:
        location = Path(os.path.realpath(__file__)).parent / "decisions.py"
        spec = importlib.util.spec_from_file_location("harness_filter_decisions", str(location))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        written = out + "\n" if out else ""
        lines_in = len(text.splitlines())
        lines_out = len(out.splitlines()) if out else 0
        runner = option(argv, "--runner") or "unknown"
        module.record("filter-output", "filtered" if out != "\n".join(text.splitlines()) else "unchanged",
                      runner, {"session_id": session},
                      fields={"runner": runner,
                              "bytes_in": len(text.encode("utf-8", "replace")),
                              "bytes_out": len(written.encode("utf-8", "replace")),
                              "lines_in": lines_in, "lines_out": lines_out})
    except Exception:
        pass


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        try:
            sys.stdin.reconfigure(errors="replace")
        except Exception:
            pass
        text = sys.stdin.read()
        out = filter_text(text)
        if out:
            sys.stdout.write(out + "\n")
            sys.stdout.flush()
        log_run(argv, text, out)
    except Exception:
        return


if __name__ == "__main__":
    main()

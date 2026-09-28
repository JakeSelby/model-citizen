#!/usr/bin/env python3
"""Run the deterministic pre-qualification checks as one tier: no model turn, no client, no cost.

Every check here already exists in this repository; the tier is the single command that runs
them before a qualification round is paid for — the acceptance runner's self-tests against
recorded transcripts, the documentation-link check, the credential and host precondition probe
for each target the round will run, the Studio lifecycle and browser flow, and the disposable-home
sync, projection-drift and lifecycle checks.

A green tier is never native client qualification. It observes no client behaviour, writes
nothing under `compatibility/evidence/` and appears in no catalog record; the run fails if
either of those is touched. What a client must be observed doing, and what evidence must
contain, is in docs/compatibility.md.

    python3 scripts/smoke_tier.py --list
    python3 scripts/smoke_tier.py
    python3 scripts/smoke_tier.py --only credentials,documentation-links
    python3 scripts/smoke_tier.py --targets claude-code-cli-macos,codex-cli-linux
"""
import argparse
import hashlib
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_acceptance import CLIENTS, redact

NOT_QUALIFICATION = ("smoke tier: deterministic pre-qualification checks, no model turn; "
                     "a green run is not native client qualification")
# What no check may touch: an evidence record is a claim about an observed client, and this tier
# observes none.
GUARDED = (Path("compatibility") / "evidence", Path("compatibility") / "catalog.json")
TAIL = 600
GIT_TIMEOUT = 30


def unittest_argv(pattern):
    return [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", pattern]


def steps(work, targets=None):
    """Each check, in the order a failing one is cheapest to read: fastest and narrowest first.

    `credentials` checks, for each target, what docs/qualification-runbook.md says must exist
    before it starts: its client and login here, or a Docker daemon for a Linux target this host
    runs in a container. With no targets named it checks every CLI target this host can run and
    reports the rest as skipped.
    """
    return [
        {"name": "credentials",
         "how": "check each target's client, login or Docker daemon on this host",
         "argv": [sys.executable, "-m", "harness_core.target_preconditions"]
                 + (["--targets", ",".join(targets)] if targets else []),
         "env": {"PYTHONPATH": str(ROOT / "lib")}, "timeout": 30},
        {"name": "projection-drift",
         "how": "regenerate every native projection and compare it with the committed one",
         "argv": [sys.executable, str(ROOT / "bin" / "harness"), "generate", "--check"],
         "timeout": 120},
        {"name": "documentation-links",
         "how": "resolve every relative documentation link and heading anchor",
         "argv": unittest_argv("test_doc_links.py"), "timeout": 120},
        {"name": "runner-self-tests",
         "how": "read recorded transcripts with the acceptance runner's own readers",
         "argv": unittest_argv("test_native_acceptance*.py"), "timeout": 600},
        {"name": "studio-lifecycle",
         "how": "start, authenticate, load and stop Studio in Chrome on this host",
         "argv": [sys.executable, str(ROOT / "scripts" / "studio_lifecycle_acceptance.py"),
                  "--output", str(work / "studio-lifecycle.json")],
         "timeout": 600, "clean_tree": True},
        {"name": "disposable-home-lifecycle",
         "how": "install, sync, upgrade, roll back and uninstall in disposable homes",
         "argv": [sys.executable, str(ROOT / "scripts" / "lifecycle_acceptance.py"),
                  "--output", str(work / "lifecycle.json")],
         "timeout": 1800, "clean_tree": True},
    ]


def dirty(root=ROOT):
    """What the working tree has changed, or `None` when its state could not be read at all.

    Outside a git checkout `git status` exits 128 and prints nothing, which is indistinguishable
    from a clean tree if only standard output is read; a check that needs a clean tree is
    `unverified` there rather than run against an unknown one.
    """
    try:
        result = subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                                capture_output=True, text=True, check=False, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    return result.stdout.strip()


def digest(root=ROOT):
    """One digest over everything this tier must not write, so a write is caught rather than trusted."""
    sha = hashlib.sha256()
    for relative in GUARDED:
        path = root / relative
        for item in ([path] if path.is_file() else sorted(path.rglob("*")) if path.exists() else []):
            sha.update(str(item.relative_to(root)).encode())
            if item.is_file():
                sha.update(item.read_bytes())
    return sha.hexdigest()


def outcome(step, result, seconds, detail):
    return {"name": step["name"], "result": result, "seconds": seconds, "detail": detail}


def kill_group(process):
    """Kill the check and everything it started; a surviving grandchild holds the run open."""
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except OSError:
        process.kill()


def tail(output, code):
    """The end of a failing check's output, redacted whole before it is cut.

    Cutting first can leave the second half of a home path or a token standing on its own, so
    the redaction runs over everything the check printed and the cut is taken from the result.
    """
    return redact(output)[-TAIL:] or "exit %s with no output" % code


def run_step(step, root=ROOT):
    """Run one check under a bounded timeout and classify it, never inferring a pass."""
    started = time.time()
    if step.get("clean_tree"):
        state = dirty(root)
        if state is None:
            return outcome(step, "unverified", 0.0,
                           "the tree state could not be read, so this check was not run")
        if state:
            return outcome(step, "unverified", 0.0,
                           "the checkout is dirty and this check requires a clean one")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env.update(step.get("env") or {})
    try:
        process = subprocess.Popen([str(item) for item in step["argv"]], cwd=str(root), env=env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, start_new_session=True)
    except OSError as error:
        return outcome(step, "unverified", 0.0, "the check could not be started: " + redact(error))
    try:
        out, err = process.communicate(timeout=step["timeout"])
    except subprocess.TimeoutExpired:
        # A check that never answered observed nothing, so it is unverified rather than failed —
        # and it is killed with its children, which is the hang this tier exists to replace.
        kill_group(process)
        process.communicate()
        return outcome(step, "unverified", float(step["timeout"]),
                       "no answer within %ss" % step["timeout"])
    except OSError as error:
        kill_group(process)
        return outcome(step, "unverified", round(time.time() - started, 1),
                       "the check could not be read: " + redact(error))
    seconds = round(time.time() - started, 1)
    if process.returncode == 0:
        return outcome(step, "passed", seconds, "")
    return outcome(step, "failed", seconds, tail((err or "") + (out or ""), process.returncode))


def names_in(names, plan):
    chosen = [name.strip() for name in (names or "").split(",") if name.strip()]
    unknown = [name for name in chosen if name not in {step["name"] for step in plan}]
    if unknown:
        raise SystemExit("unknown smoke check: " + ", ".join(unknown))
    return chosen


def selected(only, skip, plan):
    keep = names_in(only, plan) or [step["name"] for step in plan]
    dropped = names_in(skip, plan)
    return [step for step in plan if step["name"] in keep and step["name"] not in dropped]


def report(results, wrote):
    lines = [NOT_QUALIFICATION]
    for item in results:
        lines.append("  %-26s %-11s %ss" % (item["name"], item["result"], item["seconds"]))
        if item["detail"]:
            lines.append("      " + item["detail"])
    counted = [result for result in ("passed", "failed", "unverified")
               if any(item["result"] == result for item in results)]
    lines.append("smoke tier: " + ", ".join(
        "%s %s" % (sum(item["result"] == result for item in results), result)
        for result in counted or ["passed"]))
    if wrote:
        lines.append("smoke tier: a check wrote under %s; the tier writes no evidence"
                     % " or ".join(str(path) for path in GUARDED))
    return "\n".join(lines)


def tier(plan, root=ROOT, runner=run_step):
    before = digest(root)
    results = [runner(step, root) for step in plan]
    return results, digest(root) != before


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", help="comma-separated subset of the checks to run")
    parser.add_argument("--skip", help="comma-separated checks to leave out of this run")
    parser.add_argument("--targets",
                        help="comma-separated targets the round will run; default every CLI "
                             "target this host can run")
    parser.add_argument("--list", action="store_true", dest="listing",
                        help="print what would run, running nothing")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="harness-smoke-") as work:
        targets = [name.strip() for name in (args.targets or "").split(",") if name.strip()]
        unknown = [name for name in targets if name not in CLIENTS]
        if unknown:
            raise SystemExit("unknown target: " + ", ".join(unknown))
        plan = selected(args.only, args.skip, steps(Path(work), targets))
        if not plan:
            raise SystemExit("no smoke check selected; --list names them")
        if args.listing:
            print("\n".join([NOT_QUALIFICATION]
                            + ["  %-26s %s" % (step["name"], step["how"]) for step in plan]))
            return 0
        results, wrote = tier(plan)
    print(report(results, wrote))
    return 0 if not wrote and all(item["result"] == "passed" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

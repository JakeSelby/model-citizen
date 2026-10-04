#!/usr/bin/env python3
"""Drive one qualification round across its targets, from a provisioned round directory.

The driver that ran each round lived only in a per-round scratch copy, so every round rewrote it
and no two rounds were driven quite the same way. This is that driver, committed: it runs the
deterministic smoke tier once, then `scripts/native_acceptance.py` per target from the frozen
clone, and writes one record per target beside a summary an operator reads.

A smoke tier that does not pass, failed or timed out, stops the round before any target runs:
the tier spends no model turn, so a round it would have failed is refused before one is spent.
The round record names the tier's result and why nothing ran. `--skip-smoke`, for a tier already
run green at this commit, is recorded as `skipped` and runs every target.

Past the tier it decides nothing. A target's verdict is the runner's, a failed target does not
stop the others — a round collects every target's defects before any of them is fixed, which is
the rule in docs/releasing.md — and the exit status is 0 only when the tier passed or was skipped
and every case of every target passed.

    python3 scripts/qualification_round.py --round ../round-0.13.0 --targets claude-code-cli-macos
    python3 scripts/qualification_round.py --round ../round-0.13.0 --plan
    python3 scripts/qualification_round.py --round ../round-0.13.0 --execution-class light

Each target carries an execution class and an assessment class, resolved before anything is
launched and recorded with the round; `harness_core.qualification` holds the rules and the two
refusals.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness_core import qualification  # noqa: E402
from native_acceptance import (CLIENTS, catalog, confirmed_targets,  # noqa: E402
                               executed_by, host_mismatch, unobserved_note)
import smoke_tier  # noqa: E402
import studio_lifecycle_acceptance  # noqa: E402

SMOKE = "smoke_tier.py"
RUNNER = "native_acceptance.py"
PASSED = "passed"
SKIPPED = "skipped"
RUNNING = "running"
# A target's deadline, and at least the smoke tier's: every tier step may run to its own limit.
TARGET_TIMEOUT = 5400
ROUND_MARGIN = 300
ROUND_TIMEOUT = max(TARGET_TIMEOUT, smoke_tier.total_seconds() + ROUND_MARGIN)


def provision_record(round_dir):
    path = Path(round_dir) / "provision.json"
    if not path.is_file():
        raise SystemExit("no provision record at %s; run scripts/qualification_provision.py first"
                         % path)
    return json.loads(path.read_text())


def environment(report):
    """The operator's environment, unchanged: no required case reads anything the round adds.

    The credentials a client uses are the operator's and are passed through by name by the runner
    itself. A provisioned BMad checkout is for the optional integration suite, run by hand.
    """
    import os
    return dict(os.environ)


def smoke(clone, env, targets=()):
    """The deterministic tier, or `None` when it ran past the round's own deadline.

    The tier checks the preconditions of the targets this round runs and no others, so a round
    that leaves a target out is not failed for that target's missing login or daemon.
    """
    argv = [sys.executable, str(Path(clone) / "scripts" / SMOKE)]
    if targets:
        argv += ["--targets", ",".join(targets)]
    # The tier records each step's process group and every detached Studio server it starts, so
    # a tier that overruns the round is killed with everything it started, not just itself.
    with tempfile.TemporaryDirectory(prefix="studio-processes-") as tracked:
        env = dict(env, **{studio_lifecycle_acceptance.TRACK_ENV: tracked})
        try:
            return subprocess.run(argv, cwd=str(clone), env=env, check=False,
                                  timeout=ROUND_TIMEOUT, start_new_session=True)
        except subprocess.TimeoutExpired:
            return None
        finally:
            studio_lifecycle_acceptance.kill_tracked(tracked)


def nothing_observed(reason):
    """A target's record when the runner wrote none: every required case, unobserved.

    An absent `--out` is the one shape that must never be read as the previous run's: a runner
    that died before writing left no reading at all, and reporting the stale file would publish
    an old pass as this round's.
    """
    cases = dict((name, "unverified") for name in catalog()["required_cases"])
    return {"cases": cases, "observations": [reason]}


def target_argv(clone, client, model, out, confirmed, routing):
    """The runner's own argv for one target; confirmation is passed through per target only.

    Without an operator's `--model` the target's routed execution model is passed, so the runner
    executes on the model the round declares; only a target whose class the adapter leaves
    unmapped reaches the runner's own default.
    """
    argv = [sys.executable, str(Path(clone) / "scripts" / RUNNER), "--client", client,
            "--out", str(out), "--execution-class", routing["execution_class"],
            "--assessment-class", routing["assessment_class"]]
    model = model or (routing.get("execution_model")
                      if routing.get("model_source") != "runner default" else None)
    if model:
        argv += ["--model", model]
    if client in confirmed_targets(confirmed):
        argv += ["--home-confirmed", client]
    return argv


def routing(targets, execution, assessment):
    """One class pair per target, resolved before anything is launched.

    A refused pair stops the whole round rather than the target that asked for it: the round's
    record would otherwise say two different things about who read its evidence.
    """
    try:
        executes = qualification.parse_class_map(execution, targets,
                                                 qualification.EXECUTION_DEFAULT,
                                                 "--execution-class", CLIENTS)
        assesses = qualification.parse_class_map(assessment, targets,
                                                 qualification.ASSESSMENT_DEFAULT,
                                                 "--assessment-class", CLIENTS)
        return dict((client, qualification.resolve(ROOT, CLIENTS[client]["runtime"],
                                                   executes[client], assesses[client]))
                    for client in targets)
    except ValueError as error:
        raise SystemExit(str(error))


def with_models(tier_routing, model):
    """Each target's routing restated with the model its runner will be passed.

    The runner restates its own routing by the same rule, `native_acceptance.executed_by`, from
    the `--model` this round passes it, so the round record and the target's record agree.
    """
    return dict((client, executed_by(routes, model or routes.get("execution_model"))[1])
                for client, routes in tier_routing.items())


def where(client):
    """Where a target's cases run, read from the same comparison the runner refuses on."""
    if not host_mismatch(client):
        return "runs on this host"
    if CLIENTS[client]["platform"] == "linux":
        return "runs inside the Linux image"
    return "runs on a macOS host, not this one"


def summarise(records):
    """One line per target and case, in the order a reviewer reads them: worst first."""
    lines = []
    for client in sorted(records):
        data = records[client]
        cases = data.get("cases") or {}
        if not cases:
            lines.append("%-24s no record" % client)
            continue
        bad = sorted(name for name, value in cases.items() if value != PASSED)
        lines.append("%-24s %s of %s passed%s"
                     % (client, len(cases) - len(bad), len(cases),
                        (", not " + ", ".join(bad)) if bad else ""))
    return "\n".join(lines)


def write_round(round_dir, result):
    """The round record as it stands, rewritten as each target finishes.

    It is written before the smoke tier runs, so a round killed part way through still names the
    classes that were executing and whatever targets had finished.
    """
    (Path(round_dir) / "round.json").write_text(json.dumps(result, indent=2, sort_keys=True)
                                                + "\n")
    return result


def run_round(round_dir, targets, model, confirmed, skip_smoke=False, tier_routing=None):
    tier_routing = tier_routing or with_models(routing(targets, None, None), model)
    report = provision_record(round_dir)
    clone = Path(report["clone"])
    records_dir = Path(report["records"])
    records_dir.mkdir(parents=True, exist_ok=True)
    env = environment(report)
    # A round killed mid-tier must not read as a deliberate skip.
    result = {"source_commit": report.get("source_commit"),
              "smoke": SKIPPED if skip_smoke else RUNNING, "targets": {},
              "tier_routing": tier_routing}
    write_round(round_dir, result)
    if not skip_smoke:
        finished = smoke(clone, env, targets)
        result["smoke"] = ("timed out" if finished is None
                           else PASSED if finished.returncode == 0 else "failed")
        if result["smoke"] != PASSED:
            result["stopped"] = ("the smoke tier %s, so no target ran; fix what it names and "
                                 "run the round again" % result["smoke"])
            result["not_run"] = list(targets)
            return write_round(round_dir, result)
    for client in targets:
        out = records_dir / (client + ".json")
        note = unobserved_note(client, confirmed)
        # Move any earlier record aside before the runner is launched. A runner that exits
        # before writing `--out` would otherwise leave the previous round's file in place and
        # this round would report its passes as its own.
        if out.exists():
            out.replace(out.with_suffix(".json.previous"))
        failure = ""
        try:
            subprocess.run(target_argv(clone, client, model, out, confirmed,
                                       tier_routing[client]),
                           cwd=str(clone), env=env, check=False, timeout=ROUND_TIMEOUT)
        except subprocess.TimeoutExpired:
            # Reported and carried, not raised: a round collects every target's defects, and a
            # target that ran past the deadline must not cost the targets after it.
            failure = "the target ran past the round deadline of %ss" % ROUND_TIMEOUT
        try:
            data = json.loads(out.read_text())
        except (OSError, ValueError):
            data = nothing_observed(failure or "the runner wrote no record for this target")
        if failure and failure not in data.get("observations", []):
            data.setdefault("observations", []).append(failure)
        if note:
            data.setdefault("observations", []).append(note)
        result["targets"][client] = data
        write_round(round_dir, result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--round", type=Path, required=True, help="the provisioned round directory")
    parser.add_argument("--targets", default=",".join(sorted(CLIENTS)))
    parser.add_argument("--model", help="the model every target runs on, overriding each "
                                        "target's routed execution model")
    parser.add_argument("--home-confirmed", action="append", default=[], metavar="CLIENT",
                        help="a client whose configuration home was compared against a hand run; "
                             "repeat for each, and never for a surface nobody compared")
    parser.add_argument("--skip-smoke", action="store_true",
                        help="the smoke tier already passed at this commit; recorded as "
                             "skipped, and every target runs")
    parser.add_argument("--execution-class", action="append", metavar="[TARGET=]CLASS",
                        help="capability class running the cases (default %s, per target with "
                             "TARGET=CLASS)" % qualification.EXECUTION_DEFAULT)
    parser.add_argument("--assessment-class", action="append", metavar="[TARGET=]CLASS",
                        help="capability class assessing the observations (default %s)"
                             % qualification.ASSESSMENT_DEFAULT)
    parser.add_argument("--plan", action="store_true",
                        help="print what the round would run, launching nothing")
    args = parser.parse_args(argv)
    targets = [name.strip() for name in args.targets.split(",") if name.strip()]
    unknown = [name for name in targets if name not in CLIENTS]
    if unknown:
        raise SystemExit("unknown qualification target: " + ", ".join(unknown))
    tier_routing = with_models(routing(targets, args.execution_class, args.assessment_class),
                               args.model)
    # A target whose platform is not this host's is reported and left out, never run: the runner
    # would refuse it, and a round driven here would otherwise write one platform's outcome under
    # another's name.
    elsewhere = dict((client, reason) for client, reason in
                     ((client, host_mismatch(client)) for client in targets) if reason)
    here = [client for client in targets if client not in elsewhere]
    if args.plan:
        print("round: %s, targets %s, smoke tier %s"
              % (args.round, ", ".join(targets), "skipped" if args.skip_smoke else "first"))
        for client in targets:
            print("  %-24s %s" % (client, unobserved_note(client, args.home_confirmed)
                                  or "driven by this runner"))
            print("  %-24s %s" % ("", qualification.describe(tier_routing[client])))
            print("  %-24s %s" % ("", where(client)))
        return 0
    for client in sorted(elsewhere):
        print("%-24s not run here: %s" % (client, elsewhere[client]))
    if not here:
        print("no named target runs on this host, so the round ran nothing")
        return 1
    result = run_round(args.round, here, args.model, args.home_confirmed, args.skip_smoke,
                       dict((client, tier_routing[client]) for client in here))
    if elsewhere:
        result["not_run_here"] = elsewhere
    result = write_round(args.round, result)
    print("smoke tier: " + result["smoke"])
    if result.get("stopped"):
        print("round stopped: " + result["stopped"])
        return 1
    print(summarise(result["targets"]))
    for client in here:
        print("%-24s %s" % (client, qualification.describe(tier_routing[client])))
    clean = result["smoke"] in (PASSED, SKIPPED) and all(
        value == PASSED
        for data in result["targets"].values()
        for value in (data.get("cases") or {"none": "unverified"}).values())
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())

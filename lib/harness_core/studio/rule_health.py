# SPDX-License-Identifier: MIT
"""Rule health: every loaded rule with what the engines already say about it (AH-S310, #994).

Each column is read from the engine that owns it, and nothing here measures anything:

- **status and reason**: `classified_rules` in `bin/harness`, the function `citizen usage --rules`
  prints its coverage block from, so the two can never disagree;
- **detector hits**: `citizen usage --rules --json --days N` for 7, 30 and 90 days, per detector.
  These are host sessions, which `docs/evidence-standard.md` item 11 makes exploratory, so every
  hit figure says so. A detector fires on what its rule names, which may be the breach rather than
  the compliance, so a share is shown as fired, never inverted into "followed";
- **precision**: `scripts/detector_corpus.py --json`, each detector's labelled-corpus precision
  against the floor in force. A rule whose detector is under the floor, or has no score, has its
  hit figures marked unreliable;
- **advice followed**: `citizen usage --by adherence --json` (#798), read only for a rule some
  recommendation kind in `adherence.KINDS` names as its module; every other rule reads not measured;
- **context cost and effect**: `citizen scorecard --json` (#514), its soft token estimate and its
  ablation effect, which is `unmeasured` until an ablation run removed the module.

An engine that cannot be read leaves its column "not measured" with the reason, never zero.

"Try without it" creates a new draft through `citizen draft create` and switches the rule off in
it through the draft selection save, the same two steps an agent runs headless. Nothing live
changes; the developer decides whether to test or apply the draft.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from harness_core import catalog, rule_coverage
from harness_core.studio import drafts, selection, selection_editing


SCHEMA_VERSION = 1
WINDOWS = (7, 30, 90)
NOT_MEASURED = "not measured"
EXPLORATORY = "exploratory"
SOFT_ESTIMATE = "soft estimate"
SUBPROCESS_TIMEOUT = 120
UNIT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
EXPLORATORY_NOTE = ("Detector hits come from this machine's own sessions, which are exploratory "
                    "under docs/evidence-standard.md item 11 and cannot be cited as evidence.")
CLI_COMMANDS = {
    "status": ("citizen", "usage", "--rules", "--json"),
    "try_without": ("citizen", "draft", "create", "{draft}", "--json"),
}
SOURCES = {
    "status": "citizen usage --rules",
    "hits": "citizen usage --rules --json --days {days}",
    "precision": "python3 scripts/detector_corpus.py --json",
    "advice": "citizen usage --by adherence --json --days 30",
    "cost": "citizen scorecard --json",
}

Runner = Callable[[Sequence[str]], Dict[str, Any]]


class RuleHealthError(ValueError):
    """A rule-health request that cannot be carried out, with a stable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _environment() -> Dict[str, str]:
    # The suite's isolation sets HARNESS_QUIET, which silences every --json document.
    return {key: value for key, value in os.environ.items() if key != "HARNESS_QUIET"}


def command_runner(root: Path, cwd: Optional[Path] = None) -> Runner:
    """Run one engine command of checkout `root` in `cwd` and return its JSON document, or raise
    OSError. `cwd` matters: `usage --rules` loads the `.ruleprobe/detectors.yaml` found from it."""
    root = Path(root)

    def run(argv: Sequence[str]) -> Dict[str, Any]:
        if argv[0] == "scripts/detector_corpus.py":
            command = [sys.executable, str(root / "scripts" / "detector_corpus.py")] + list(argv[1:])
        else:
            command = [sys.executable, str(root / "bin" / "harness")] + list(argv)
        try:
            done = subprocess.run(command, cwd=None if cwd is None else str(cwd), env=_environment(),
                                  capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            raise OSError("%s timed out" % " ".join(argv)) from exc
        if done.returncode != 0:
            tail = (done.stderr or done.stdout).strip().splitlines()
            raise OSError(tail[-1] if tail else "%s exited %d" % (" ".join(argv), done.returncode))
        try:
            document = json.loads(done.stdout)
        except ValueError as exc:
            raise OSError("%s did not print JSON" % " ".join(argv)) from exc
        if not isinstance(document, dict):
            raise OSError("%s did not print a JSON object" % " ".join(argv))
        return document

    return run


def _message(exc: BaseException) -> str:
    return str(exc.args[0] if exc.args else exc) or exc.__class__.__name__


def _classified(root: Path, cwd: Optional[Path]):
    """`(rules, findings)` exactly as `citizen usage --rules` classifies them, or None."""
    module = selection._harness_module(Path(root))
    return module.classified_rules(cwd)


def _module_key(rule: Any) -> str:
    # As `coverage_states` keys them: a stance's file sits under a `stances` directory.
    kind = "stances" if "stances" in Path(str(rule.path)).parts[:-1] else "rules"
    return "%s/%s" % (kind, rule.rule)


def _unit(rule: Any) -> str:
    return Path(str(rule.path)).stem


def _advice_modules(root: Path) -> Dict[str, List[str]]:
    """`{module: [recommendation kinds]}` from `adherence.KINDS`, the ledger's own registry."""
    module = selection._harness_module(Path(root))
    adherence = module.load_hook_module("adherence", Path(root))
    kinds = getattr(adherence, "KINDS", None) or {}
    out: Dict[str, List[str]] = {}
    for kind, spec in sorted(kinds.items()):
        target = (spec or {}).get("module")
        if isinstance(target, str):
            out.setdefault(target, []).append(kind)
    return out


def _source(status: str, message: str = "", **extra: Any) -> Dict[str, Any]:
    return dict({"status": status, "message": message}, **extra)


def _gather(run: Runner, need_advice: bool) -> Dict[str, Any]:
    """Every engine document, read concurrently; a failed read is kept as its error."""
    jobs = {"hits-%d" % days: ["usage", "--rules", "--json", "--days", str(days)] for days in WINDOWS}
    jobs["cost"] = ["scorecard", "--json"]
    jobs["precision"] = ["scripts/detector_corpus.py", "--json"]
    if need_advice:
        jobs["advice"] = ["usage", "--by", "adherence", "--json", "--days", "30"]
    results: Dict[str, Any] = {}

    def one(argv):
        try:
            return run(argv)
        except (OSError, ValueError) as exc:
            return OSError(_message(exc))

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {name: pool.submit(one, argv) for name, argv in jobs.items()}
        for name, future in futures.items():
            results[name] = future.result()
    return results


def _detector_precision(precision: Any, detector_id: str) -> Dict[str, Any]:
    if isinstance(precision, BaseException) or not isinstance(precision, dict):
        return {"id": detector_id, "status": NOT_MEASURED, "precision": None, "recall": None,
                "below_floor": None, "reason": "the detector corpus could not be read"}
    floor = precision.get("floor")
    score = (precision.get("detectors") or {}).get(detector_id)
    if not isinstance(score, dict) or not score.get("scored") or score.get("precision") is None:
        return {"id": detector_id, "status": NOT_MEASURED, "precision": None, "recall": None,
                "below_floor": None, "reason": "no labelled example scores this detector"}
    listed = set(precision.get("below_floor") or [])
    below = detector_id in listed or (isinstance(floor, (int, float)) and score["precision"] < floor)
    return {"id": detector_id, "status": "measured", "precision": score["precision"],
            "recall": score.get("recall"), "below_floor": bool(below), "reason": ""}


def _reliability(detectors: List[Dict[str, Any]], floor: Any) -> Dict[str, Any]:
    under = [d for d in detectors if d["below_floor"]]
    unscored = [d for d in detectors if d["status"] != "measured"]
    if under:
        return {"reliable": False, "reason": "; ".join(
            "%s precision %.2f is under the %.2f floor" % (d["id"], d["precision"], floor)
            for d in under)}
    if unscored:
        return {"reliable": False, "reason": "; ".join(
            "%s has no measured precision" % d["id"] for d in unscored)}
    return {"reliable": True, "reason": ""}


def _hits(results: Dict[str, Any], detector_ids: List[str]) -> Dict[str, Any]:
    """Per detector and window, the `usage --rules` group as printed, or None with the reason."""
    windows: Dict[str, Any] = {}
    for days in WINDOWS:
        document = results.get("hits-%d" % days)
        if isinstance(document, BaseException) or not isinstance(document, dict):
            windows[str(days)] = {"status": NOT_MEASURED, "reason": "citizen usage --rules could not be read",
                                  "measured_sessions": None, "detectors": {}}
            continue
        groups = dict((g.get("id"), g) for g in document.get("groups") or [] if isinstance(g, dict))
        sessions = document.get("measured_sessions")
        if not sessions:
            windows[str(days)] = {"status": NOT_MEASURED, "measured_sessions": 0,
                                  "reason": "no measured sessions in the last %d day(s)" % days,
                                  "detectors": {}}
            continue
        found = {}
        for did in detector_ids:
            group = groups.get(did)
            found[did] = None if group is None else dict(
                (key, group.get(key)) for key in ("hits", "sessions", "of", "share", "note"))
        windows[str(days)] = {"status": "measured", "measured_sessions": sessions, "reason": "",
                              "detectors": found}
    last = None
    for days in WINDOWS:
        window = windows[str(days)]
        if window["status"] == "measured" and any(
                (group or {}).get("sessions") for group in window["detectors"].values()):
            last = days
            break
    return {"windows": windows, "last_fired_within_days": last}


def _advice(results: Dict[str, Any], kinds: List[str]) -> Dict[str, Any]:
    if not kinds:
        return {"status": NOT_MEASURED, "reason": "no recommendation in the adherence ledger names this rule",
                "groups": []}
    document = results.get("advice")
    if isinstance(document, BaseException) or not isinstance(document, dict):
        return {"status": NOT_MEASURED, "reason": "citizen usage --by adherence could not be read",
                "groups": []}
    groups = [g for g in document.get("groups") or []
              if isinstance(g, dict) and g.get("kind") in kinds]
    if not groups:
        return {"status": NOT_MEASURED, "reason": "no emission of %s in the last 30 days" % ", ".join(kinds),
                "groups": []}
    return {"status": "measured", "reason": "", "groups": groups, "footer": document.get("footer")}


def _cost(results: Dict[str, Any], key: str) -> Dict[str, Any]:
    document = results.get("cost")
    if isinstance(document, BaseException) or not isinstance(document, dict):
        reason = "citizen scorecard could not be read"
        return {"selection_state": None, "tokens": {"status": NOT_MEASURED, "reason": reason},
                "effect": {"status": NOT_MEASURED, "reason": reason}}
    row = next((r for r in document.get("rows") or [] if isinstance(r, dict) and r.get("module") == key), None)
    if row is None:
        reason = "the scorecard has no row for %s" % key
        return {"selection_state": None, "tokens": {"status": NOT_MEASURED, "reason": reason},
                "effect": {"status": NOT_MEASURED, "reason": reason}}
    tokens = row.get("tokens")
    if isinstance(tokens, dict):
        tokens = {"status": "measured", "tokens": tokens.get("tokens"),
                  "estimand": tokens.get("estimand") or SOFT_ESTIMATE, "reason": ""}
    else:
        tokens = {"status": NOT_MEASURED, "reason": "the scorecard reads %s" % tokens}
    effect = row.get("effect")
    if isinstance(effect, dict):
        effect = dict(effect, status="measured", reason="")
    else:
        effect = {"status": NOT_MEASURED, "reason": "no ablation run has removed this module"}
    return {"selection_state": row.get("state"), "tokens": tokens, "effect": effect}


def report(root: Path, cwd: Optional[Path] = None, run: Optional[Runner] = None,
           home: Optional[str] = None) -> Dict[str, Any]:
    """The rule-health document the Studio route serves. Reads only; writes nothing."""
    root = Path(root).resolve()
    run = run or command_runner(root, cwd)
    relative_to = str(cwd or Path.cwd())
    try:
        classified = _classified(root, cwd)
        coverage_error = "" if classified is not None else (
            "claude/hooks/rule-detectors.py is missing or does not import")
    except (OSError, ValueError, SystemExit) as exc:
        classified, coverage_error = None, _message(exc)
    try:
        advice_modules = _advice_modules(root)
    except (OSError, ValueError, SystemExit, AttributeError):
        advice_modules = {}
    rules, findings = classified if classified is not None else ([], [])
    keys = [_module_key(rule) for rule in rules]
    results = _gather(run, need_advice=any(key in advice_modules for key in keys))
    precision = results.get("precision")
    floor = precision.get("floor") if isinstance(precision, dict) else None
    rows = []
    for rule, key in zip(rules, keys):
        kind = key.split("/", 1)[0]
        cost = _cost(results, key)
        detectors = [_detector_precision(precision, did) for did in rule.detectors]
        if rule.state == "measured":
            hits = dict(_hits(results, list(rule.detectors)), status="measured", label=EXPLORATORY,
                        **_reliability(detectors, floor))
        else:
            hits = {"status": NOT_MEASURED, "reason": rule.reason, "label": EXPLORATORY, "windows": {},
                    "last_fired_within_days": None, "reliable": None}
        unit = _unit(rule)
        if kind != "rules":
            offer = {"available": False, "reason": "a stance is changed by choosing another variant, not switched off"}
        elif cost["selection_state"] != "on":
            offer = {"available": False, "reason": "it is not switched on in the selection in force"}
        else:
            offer = {"available": True, "reason": ""}
        rows.append({
            "rule": rule.rule, "module": key, "kind": kind, "unit": unit,
            "path": rule_coverage.short(str(rule.path), relative_to, home),
            "state": rule.state, "reason": rule.reason, "detectors": detectors, "hits": hits,
            "advice": _advice(results, advice_modules.get(key, [])),
            "selection_state": cost["selection_state"], "tokens": cost["tokens"], "effect": cost["effect"],
            "try_without": offer,
        })
    counts = rule_coverage.counts(rules)
    sources = {"status": _source("unavailable" if coverage_error else "ready", coverage_error)}
    for name, label in (("cost", "cost"), ("precision", "precision")):
        value = results.get(name)
        sources[label] = (_source("unavailable", _message(value)) if isinstance(value, BaseException)
                          else _source("ready"))
    failed = [days for days in WINDOWS if isinstance(results.get("hits-%d" % days), BaseException)]
    sources["hits"] = (_source("unavailable", _message(results["hits-%d" % failed[0]])) if failed
                       else _source("ready"))
    partial = any(item["status"] != "ready" for item in sources.values())
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "partial" if partial else "ready",
        "summary": {"rules": len(rules), "measured": counts["measured"], "dark": counts["dark"],
                    "unmeasured": counts["unmeasured"], "measured_text": rule_coverage.percent(rules).strip()},
        "windows": list(WINDOWS),
        "precision_floor": floor,
        "exploratory_note": EXPLORATORY_NOTE,
        "sources": sources,
        "commands": dict(SOURCES),
        "findings": [{"path": rule_coverage.short(str(f.path), relative_to, home), "line": f.line,
                      "reason": f.reason} for f in findings],
        "rows": rows,
    }


def _selected_rules(root: Path) -> Dict[str, Any]:
    posture = catalog.posture_module(root)
    if posture is None:
        raise RuleHealthError("selection-unavailable", "this checkout has no selection resolver")
    config = posture._user_config(os.environ, False)
    document = posture.selection(os.environ, strict=False, config=config, root=root)
    return dict(document.get("rules") or {})


def try_without(root: Path, unit: str, create: Callable[[Path, str], str],
                suffix: Optional[str] = None) -> Dict[str, Any]:
    """Create a draft with rule `unit` switched off. Nothing live changes.

    `create(root, name)` runs `citizen draft create` and returns "" or a failure code. The draft
    keeps its name when the switch cannot be saved, so the developer can see or discard it.
    """
    if not isinstance(unit, str) or not UNIT.fullmatch(unit):
        raise RuleHealthError("invalid-rule", "rule must name a rule module")
    root = Path(root).resolve()
    if _selected_rules(root).get(unit) != "on":
        raise RuleHealthError("rule-not-switchable", "%s is not a rule switched on in the selection" % unit)
    name = "without-%s-%s" % (unit, suffix or uuid.uuid4().hex[:8])
    failure = create(root, name)
    if failure:
        raise RuleHealthError(failure, "the draft could not be created")
    worktree, state = drafts.find(root, name)
    revision = drafts.describe(root, worktree, state)["revision"]
    change = {"rules.%s" % unit: "off"}
    saved = selection_editing.save(root, name, revision, uuid.uuid4().hex, change)
    if not saved.get("saved"):
        raise RuleHealthError("switch-failed", saved.get("error") or "the switch could not be saved")
    result = saved.get("result") or {}
    save_command = " ".join(selection_editing.CLI_COMMANDS["save"]).replace("{draft}", name)
    commands = [" ".join(CLI_COMMANDS["try_without"]).replace("{draft}", name),
                save_command.replace("{revision}", "REVISION")
                + "  # changes.json: " + json.dumps(change, sort_keys=True)]
    return {"schema_version": SCHEMA_VERSION, "rule": unit,
            "draft": {"name": name, "revision": result.get("revision") or saved.get("base_revision") or revision},
            "changes": change, "commands": commands,
            "message": "Draft %s switches %s off. Nothing live changed." % (name, unit)}

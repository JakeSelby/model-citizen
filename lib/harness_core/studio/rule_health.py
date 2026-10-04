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
import shlex
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
    "status": ("citizen", "usage", "--rules", "--health", "--json"),
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


# The keys `detector_corpus.py --json` always prints (`scores_as_dict` plus the script's own).
CORPUS_KEYS = frozenset(("floor", "detectors", "below_floor", "unscored", "failed"))


def _failure(argv: Sequence[str], done: Any, what: str) -> str:
    tail = (done.stderr or "").strip().splitlines()
    return "%s %s%s" % (" ".join(argv), what, ": " + tail[-1] if tail else "")


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
        # The corpus script exits 1 when a detector is under the floor, unscored or stale: that is
        # a result carrying failures, printed in full. Only another exit, or no JSON, is a failure.
        accepted = (0, 1) if argv[0] == "scripts/detector_corpus.py" else (0,)
        if done.returncode not in accepted:
            raise OSError(_failure(argv, done, "exited %d" % done.returncode))
        try:
            document = json.loads(done.stdout)
        except ValueError as exc:
            raise OSError(_failure(argv, done, "did not print JSON")) from exc
        if not isinstance(document, dict):
            raise OSError(_failure(argv, done, "did not print a JSON object"))
        # An uncaught exception also exits 1; only the script's own document makes exit 1 a result.
        if done.returncode == 1 and not CORPUS_KEYS <= set(document):
            raise OSError(_failure(argv, done, "exited 1 without its result document"))
        return document

    return run


def _message(exc: BaseException) -> str:
    return str(exc.args[0] if exc.args else exc) or exc.__class__.__name__


def _classified(root: Path, cwd: Optional[Path]):
    """`(rules, findings)` exactly as `citizen usage --rules` classifies them, or None."""
    module = selection._harness_module(Path(root))
    return module.classified_rules(cwd)


def _module_key(rule: Any) -> str:
    """The selection module a rule file is, as the scorecard and `config set` key it: `rules/<stem>`
    for a rule (front matter may give the rule another name), `stances/<dimension>` for a stance."""
    path = Path(str(rule.path))
    if "stances" in path.parts[:-1]:
        return "stances/%s" % path.parent.name
    return "rules/%s" % path.stem


def _unit(rule: Any) -> str:
    return _module_key(rule).split("/", 1)[1]


SHADOWED_PREFIX = rule_coverage.SHADOWED.split("%s", 1)[0]


def _shadowed(rule: Any) -> bool:
    """The engine's verdict: `rule_coverage.classify` names a later file of a taken name shadowed."""
    return rule.state == "unmeasured" and str(rule.reason).startswith(SHADOWED_PREFIX)


def _root_of(rule: Any) -> Path:
    """The primitive root a rule file belongs to: the directory holding `rules/` or `stances/`."""
    path = Path(str(rule.path))
    for parent in path.parents:
        if parent.name in ("rules", "stances"):
            return parent.parent
    return path.parent


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


UNAVAILABLE = "unavailable"
READ_FAILED = "precision unavailable (read failed)"


def _detector_precision(precision: Any, detector_id: str) -> Dict[str, Any]:
    """One detector's corpus score, and whether the engine's own `below_floor` verdict names it.

    The verdict is the engine's (`ruleprobe.validity.below_floor`: precision or recall under the
    floor), never a comparison made here."""
    if isinstance(precision, BaseException) or not isinstance(precision, dict):
        return {"id": detector_id, "status": UNAVAILABLE, "precision": None, "recall": None,
                "below_floor": None, "reason": READ_FAILED}
    score = (precision.get("detectors") or {}).get(detector_id)
    if not isinstance(score, dict) or not score.get("scored") or score.get("precision") is None:
        return {"id": detector_id, "status": NOT_MEASURED, "precision": None, "recall": None,
                "below_floor": None, "reason": "no labelled example scores this detector"}
    return {"id": detector_id, "status": "measured", "precision": score["precision"],
            "recall": score.get("recall"), "reason": "",
            "below_floor": detector_id in set(precision.get("below_floor") or [])}


def _score(value: Any) -> str:
    return "%.2f" % value if isinstance(value, (int, float)) and not isinstance(value, bool) else "unknown"


def _floor_text(floor: Any) -> str:
    return "%s floor" % _score(floor) if isinstance(floor, (int, float)) and not isinstance(floor, bool) else "floor"


def _reliability(detectors: List[Dict[str, Any]], floor: Any) -> Dict[str, Any]:
    """`reliable` False when a detector is below the floor or unscored; None when the corpus could
    not be read, since whether the figures can be trusted is then unknown, not settled."""
    under = [d for d in detectors if d["below_floor"]]
    unscored = [d for d in detectors if d["status"] == NOT_MEASURED]
    unread = [d for d in detectors if d["status"] == UNAVAILABLE]
    if under:
        return {"reliable": False, "reason": "; ".join(
            "%s is below the engine's %s (precision %s, recall %s)"
            % (d["id"], _floor_text(floor), _score(d["precision"]), _score(d["recall"])) for d in under)}
    if unscored:
        return {"reliable": False, "reason": "; ".join(
            "%s has no measured precision" % d["id"] for d in unscored)}
    if unread:
        return {"reliable": None, "reason": READ_FAILED}
    return {"reliable": True, "reason": ""}


def _hits(results: Dict[str, Any], detector_ids: List[str]) -> Dict[str, Any]:
    """Per detector and window, the `usage --rules` group as printed, or None with the reason."""
    windows: Dict[str, Any] = {}
    for days in WINDOWS:
        document = results.get("hits-%d" % days)
        if isinstance(document, BaseException) or not isinstance(document, dict):
            windows[str(days)] = {"status": UNAVAILABLE, "measured_sessions": None, "detectors": {},
                                  "reason": "citizen usage --rules --days %d could not be read: %s"
                                            % (days, _message(document) if isinstance(document, BaseException)
                                               else "not a JSON object")}
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
    return {"windows": windows, "last_fired": _last_fired(windows)}


def _last_fired(windows: Dict[str, Any]) -> Dict[str, Any]:
    """When a detector last fired, saying only what a read window supports.

    `fired` within the narrowest window where one did; `not-fired` in the widest window that was
    read, had sessions and reported every detector; `not-reported` when windows were read but some
    detector was missing from each; `no-sessions` when every read window had none; `unavailable`
    when no window could be read."""
    states = [(days, windows[str(days)]) for days in WINDOWS]
    for days, window in states:
        if window["status"] == "measured" and any(
                (group or {}).get("sessions") for group in window["detectors"].values()):
            return {"state": "fired", "days": days}
    complete = [days for days, window in states if window["status"] == "measured"
                and all(group is not None for group in window["detectors"].values())]
    if complete:
        return {"state": "not-fired", "days": max(complete)}
    read = [days for days, window in states if window["status"] == "measured"]
    if read:
        return {"state": "not-reported", "days": max(read)}
    empty = [days for days, window in states if window["status"] == NOT_MEASURED]
    if empty:
        return {"state": "no-sessions", "days": max(empty)}
    return {"state": UNAVAILABLE, "days": None}


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
        root_label = rule_coverage.short(str(_root_of(rule)), relative_to, home)
        # A file the engine shadows measures nothing of its own; the scorecard's cost, effect and
        # switch for its module would describe another file, so it borrows none of them.
        shadowed = _shadowed(rule)
        cost = _cost(results, key)
        if shadowed:
            reason = "shadowed: %s" % rule.reason
            cost = {"selection_state": None, "tokens": {"status": NOT_MEASURED, "reason": reason},
                    "effect": {"status": NOT_MEASURED, "reason": reason}}
        detectors = [_detector_precision(precision, did) for did in rule.detectors]
        if rule.state == "measured":
            hits = dict(_hits(results, list(rule.detectors)), status="measured", label=EXPLORATORY,
                        **_reliability(detectors, floor))
        else:
            hits = {"status": NOT_MEASURED, "reason": rule.reason, "label": EXPLORATORY, "windows": {},
                    "last_fired": {"state": NOT_MEASURED, "days": None}, "reliable": None}
        unit = _unit(rule)
        if shadowed:
            offer = {"available": False, "reason": cost["tokens"]["reason"]}
        elif kind != "rules":
            offer = {"available": False, "reason": "a stance is changed by choosing another variant, not switched off"}
        elif cost["selection_state"] != "on":
            offer = {"available": False, "reason": "it is not switched on in the selection in force"}
        else:
            offer = {"available": True, "reason": ""}
        rows.append({
            # One row per file: the path is unique where a rule name or module may not be.
            "id": rule_coverage.short(str(rule.path), relative_to, home), "root": root_label,
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
    if "advice" in results:
        value = results["advice"]
        sources["advice"] = (_source("unavailable", _message(value)) if isinstance(value, BaseException)
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
        # Statuses and hit windows read the `.ruleprobe/detectors.yaml` found from this directory,
        # as `citizen usage --rules` run there would.
        "working_directory": _home_short(relative_to, home),
        "sources": sources,
        "commands": dict(SOURCES),
        "findings": [{"path": rule_coverage.short(str(f.path), relative_to, home), "line": f.line,
                      "reason": f.reason} for f in findings],
        "rows": rows,
    }


def _home_short(path: str, home: Optional[str]) -> str:
    home = home or os.path.expanduser("~")
    if home and (path == home or path.startswith(home.rstrip(os.sep) + os.sep)):
        return "~" + path[len(home.rstrip(os.sep)):]
    return path


def _fill(template: Sequence[str], values: Dict[str, str]) -> str:
    """A CLI template with each whole placeholder word replaced, so a value never rewrites another."""
    return " ".join(values.get(word, word) for word in template)


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
    change = {"rules.%s" % unit: "off"}
    commands = [_fill(CLI_COMMANDS["try_without"], {"{draft}": name})]

    def kept(status: str, why: str, revision: str = "") -> Dict[str, Any]:
        # The draft exists from here on: every answer names it, so it can be found or discarded.
        return {"schema_version": SCHEMA_VERSION, "rule": unit, "status": status,
                "draft": {"name": name, "revision": revision}, "changes": change,
                "commands": commands, "warning": why,
                "message": "Draft %s was created but %s is not switched off in it: %s. Nothing live "
                           "changed; open it in Configure, or discard it with citizen draft discard %s."
                           % (name, unit, why, name)}

    try:
        worktree, state = drafts.find(root, name)
        revision = drafts.describe(root, worktree, state)["revision"]
    except drafts.DraftError as exc:
        return kept("draft-unreadable", "the new draft could not be read (%s)" % exc.code)
    key = uuid.uuid4().hex
    commands += [
        "printf '%%s\\n' %s > changes.json" % shlex.quote(json.dumps(change, sort_keys=True)),
        _fill(selection_editing.CLI_COMMANDS["save"], {"{draft}": name, "{revision}": revision, "KEY": key}),
    ]
    saved = selection_editing.save(root, name, revision, key, change)
    if not saved.get("saved"):
        return kept("switch-failed", saved.get("error") or "the switch could not be saved", revision)
    result = saved.get("result") or {}
    return {"schema_version": SCHEMA_VERSION, "rule": unit, "status": "switched",
            "draft": {"name": name, "revision": result.get("revision") or revision},
            "changes": change, "commands": commands, "warning": "",
            "message": "Draft %s switches %s off. Nothing live changed." % (name, unit)}

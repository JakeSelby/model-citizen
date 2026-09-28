"""Discovery, presentation and execution for Studio's free local suites."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


FREE_SUITE_IDS = frozenset((
    "unit-tests", "lint", "static-context", "detector-corpus", "lifecycle-acceptance",
))
SUITE_LABELS = {
    "unit-tests": "Unit tests",
    "lint": "Harness lint",
    "static-context": "Static context figure",
    "detector-corpus": "Detector corpus",
    "lifecycle-acceptance": "Lifecycle acceptance",
}
SUITE_DESCRIPTIONS = {
    "unit-tests": "All Python unit tests, or one discovered module, class or test.",
    "lint": "Personal-data shapes, secrets, generated content and context budgets.",
    "static-context": "The committed context-cost figure and its growth threshold.",
    "detector-corpus": "Precision and recall over the labelled rule-detector corpus.",
    "lifecycle-acceptance": "Deterministic lifecycle acceptance tests with no model use.",
}
_UNIT_RESULT = re.compile(
    r"^(?P<label>.+?) \((?P<id>[A-Za-z0-9_.]+)\) \.\.\. "
    r"(?P<status>ok|FAIL|ERROR|skipped .+|expected failure|unexpected success)$"
)
_UNIT_FAILURE = re.compile(r"^(?:FAIL|ERROR): .+ \((?P<id>[A-Za-z0-9_.]+)\)$")
_SEPARATOR = re.compile(r"^-{10,}$")
_LINT_FINDING = re.compile(
    r"^(?P<path>[^:\n]+):(?P<line>[1-9][0-9]*)(?::(?P<column>[1-9][0-9]*))?:\s*(?P<message>.+)$"
)


class FreeSuiteError(ValueError):
    """A free suite request is not one of the discovered allowlisted operations."""


def _tests(suite: unittest.TestSuite) -> Iterable[unittest.TestCase]:
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _tests(item)
        else:
            yield item


def _root(value: Any) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise FreeSuiteError("installed target root is invalid")
    root = Path(value)
    if not root.is_absolute() or not root.is_dir():
        raise FreeSuiteError("installed target root is unavailable")
    if not (root / "tests").is_dir() or not (root / "bin" / "harness").is_file():
        raise FreeSuiteError("installed target does not contain the harness test surface")
    return root.resolve()


def _discover_unit_tests_in_process(root: Path) -> List[Dict[str, str]]:
    target = _root(root)
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            suite = unittest.defaultTestLoader.discover(str(target / "tests"))
    except (ImportError, OSError, TypeError) as exc:
        raise FreeSuiteError("unit-test discovery failed") from exc
    found: Dict[str, Dict[str, str]] = {}
    for test in _tests(suite):
        identity = test.id()
        pieces = identity.split(".")
        if len(pieces) < 3 or any(not piece for piece in pieces):
            raise FreeSuiteError("unit-test discovery returned an invalid identity")
        found[identity] = {
            "id": identity,
            "module": pieces[0],
            "class_name": ".".join(pieces[1:-1]),
            "test_name": pieces[-1],
            "label": pieces[-1].replace("_", " "),
        }
    return [found[name] for name in sorted(found)]


def discover_unit_tests(root: Path) -> List[Dict[str, str]]:
    """Discover tests in a short-lived process with an isolated home profile."""
    target = _root(root)
    with tempfile.TemporaryDirectory() as temporary:
        profile = os.path.realpath(temporary)
        environment = dict(os.environ)
        library = str(Path(__file__).resolve().parents[2])
        environment.update({"HOME": profile, "HARNESS_HOME": profile,
                            "PYTHONDONTWRITEBYTECODE": "1"})
        environment["PYTHONPATH"] = library + (
            os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "harness_core.studio.free_suites",
                 "discover-json", "--root", str(target)],
                cwd=str(target), env=environment, capture_output=True, text=True,
                timeout=60, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise FreeSuiteError("unit-test discovery failed") from exc
    if completed.returncode != 0:
        raise FreeSuiteError("unit-test discovery failed")
    try:
        value = json.loads(completed.stdout)
    except (TypeError, ValueError) as exc:
        raise FreeSuiteError("unit-test discovery returned invalid output") from exc
    if (not isinstance(value, list)
            or any(not isinstance(item, dict)
                   or set(item) != {"id", "module", "class_name", "test_name", "label"}
                   or any(not isinstance(field, str) or not field for field in item.values())
                   for item in value)):
        raise FreeSuiteError("unit-test discovery returned invalid output")
    return value


def unit_scopes(cases: Sequence[Mapping[str, str]]) -> Dict[str, List[str]]:
    """Map every selectable module and class scope to its concrete test ids."""
    scopes: Dict[str, List[str]] = {"all": []}
    for item in cases:
        identity = item["id"]
        module = item["module"]
        class_id = module + "." + item["class_name"]
        scopes["all"].append(identity)
        scopes.setdefault(module, []).append(identity)
        scopes.setdefault(class_id, []).append(identity)
        scopes[identity] = [identity]
    return scopes


def resolve_case_identities(suite_id: str, parameters: Mapping[str, str],
                            target_kind: str, target_ref: str) -> List[str]:
    if suite_id not in FREE_SUITE_IDS:
        return []
    if target_kind != "installed":
        raise FreeSuiteError("free local suites support the installed target only")
    root = _root(parameters.get("root"))
    if root != Path(target_ref).resolve():
        raise FreeSuiteError("free suite root does not match its installed target")
    if suite_id != "unit-tests":
        return [suite_id]
    cases = discover_unit_tests(root)
    selected = parameters.get("case", "all")
    scopes = unit_scopes(cases)
    if selected not in scopes:
        raise FreeSuiteError("unit-test selection was not returned by discovery")
    return scopes[selected]


def command_text(argv: Sequence[str]) -> str:
    return shlex.join(list(argv))


def start_command(suite_id: str, parameters: Mapping[str, str],
                  target_kind: str, target_ref: str) -> str:
    return command_text(start_argv(suite_id, parameters, target_kind, target_ref))


def start_argv(suite_id: str, parameters: Mapping[str, str],
               target_kind: str, target_ref: str) -> List[str]:
    argv = ["citizen", "runs", "start", suite_id, "--target-kind", target_kind,
            "--target-ref", target_ref]
    for name in sorted(parameters):
        argv.extend(("--param", name + "=" + parameters[name]))
    argv.append("--json")
    return argv


def catalog_payload(catalog: Any, root: Path) -> Dict[str, Any]:
    target = _root(root)
    cases = discover_unit_tests(target)
    scopes = unit_scopes(cases)
    suites = []
    for suite_id in FREE_SUITE_IDS:
        suite = catalog.get(suite_id)
        parameters = {"root": str(target)}
        if suite_id == "unit-tests":
            parameters["case"] = "all"
        suite.render(parameters, "installed", str(target))
        command_argv = start_argv(suite_id, parameters, "installed", str(target))
        suites.append({
            "id": suite_id,
            "label": SUITE_LABELS[suite_id],
            "description": SUITE_DESCRIPTIONS[suite_id],
            "cost_class": suite.cost_class,
            "expected_duration_seconds": suite.expected_duration_seconds,
            "command": command_text(command_argv),
            "command_argv": command_argv,
            "parameters": parameters,
            "case_count": len(cases) if suite_id == "unit-tests" else 1,
        })
    suites.sort(key=lambda item: item["label"])
    return {
        "schema_version": 1,
        "target": {"kind": "installed", "label": "Installed checkout"},
        "suites": suites,
        "unit_tests": {"cases": cases, "scopes": scopes},
        "commands": {"catalog": "citizen runs catalog --json",
                     "start": "citizen runs start {suite} --target-kind installed "
                              "--target-ref {root} --param root={root} --json",
                     "show": "citizen runs show {run_id} --json",
                     "cancel": "citizen runs cancel {run_id} --json"},
    }


class UnitOutputState:
    """Incrementally parse unittest verbose output without losing chunk boundaries."""

    def __init__(self):
        self.buffer = ""
        self.results: Dict[str, Dict[str, Any]] = {}
        self.failure_id: Optional[str] = None
        self.failure_lines: List[str] = []
        self.failure_boundary = False

    def _finish_failure(self) -> None:
        if self.failure_id in self.results:
            self.results[self.failure_id]["detail"] = "\n".join(self.failure_lines).strip()
        self.failure_id = None
        self.failure_lines = []
        self.failure_boundary = False

    def feed(self, output: str, eof: bool = False) -> List[Dict[str, Any]]:
        text = self.buffer + output
        lines = text.splitlines(keepends=True)
        self.buffer = ""
        if lines and not lines[-1].endswith(("\n", "\r")) and not eof:
            self.buffer = lines.pop()
        for raw_line in lines:
            line = raw_line.rstrip("\r\n")
            result = _UNIT_RESULT.match(line.strip())
            if result is not None:
                status_text = result.group("status")
                status = ("passed" if status_text in ("ok", "expected failure") else
                          "skipped" if status_text.startswith("skipped ") else "failed")
                self.results[result.group("id")] = {
                    "id": result.group("id"), "status": status,
                    "detail": status_text[8:] if status_text.startswith("skipped ") else "",
                }
                continue
            failure = _UNIT_FAILURE.match(line.strip())
            if failure is not None:
                if self.failure_id is not None:
                    self._finish_failure()
                self.failure_id = failure.group("id")
                continue
            if self.failure_id is None:
                continue
            if _SEPARATOR.match(line):
                if self.failure_boundary:
                    self._finish_failure()
                else:
                    self.failure_boundary = True
                continue
            if self.failure_boundary:
                self.failure_lines.append(line)
        if eof:
            if self.buffer:
                trailing = self.buffer
                self.buffer = ""
                self.feed(trailing + "\n")
            if self.failure_id is not None:
                self._finish_failure()
        return [dict(self.results[name]) for name in sorted(self.results)]


def parse_unit_output(output: str) -> List[Dict[str, Any]]:
    return UnitOutputState().feed(output, eof=True)


def parse_lint_findings(output: str) -> List[Dict[str, Any]]:
    findings = []
    for line in output.splitlines():
        match = _LINT_FINDING.match(line.strip())
        if match is None or Path(match.group("path")).is_absolute():
            continue
        path = match.group("path")
        if ".." in Path(path).parts:
            continue
        finding = {"path": path, "line": int(match.group("line")),
                   "column": int(match.group("column")) if match.group("column") else None,
                   "message": match.group("message")}
        finding["library_href"] = None
        findings.append(finding)
    return findings


def progress_payload(record: Mapping[str, Any], stdout: str, stderr: str,
                     unit_state: Optional[UnitOutputState] = None,
                     stderr_eof: bool = False) -> Dict[str, Any]:
    case_results = ((unit_state or UnitOutputState()).feed(stderr, eof=stderr_eof)
                    if record.get("suite_id") == "unit-tests" else [])
    eligible = len(record.get("case_identities") or [])
    completed = len(case_results)
    if record.get("suite_id") != "unit-tests" and record.get("status") in (
            "succeeded", "failed", "cancelled", "timed_out"):
        completed = 1
    return {
        "completed": min(completed, eligible),
        "eligible": eligible,
        "cases": case_results,
        "lint_findings": parse_lint_findings(stdout + "\n" + stderr)
        if record.get("suite_id") == "lint" else [],
    }


def _execution(suite_id: str, root: Path, selected: str) -> Tuple[List[str], Dict[str, str]]:
    env = dict(os.environ)
    if suite_id == "unit-tests":
        cases = discover_unit_tests(root)
        if selected not in unit_scopes(cases):
            raise FreeSuiteError("unit-test selection was not returned by discovery")
        env["PYTHONPATH"] = str(root / "tests") + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        argv = [sys.executable, "-m", "unittest", "-v"]
        argv += [item["id"] for item in cases] if selected == "all" else [selected]
    elif suite_id == "lint":
        argv = [sys.executable, "bin/harness", "lint"]
    elif suite_id == "static-context":
        argv = [sys.executable, "scripts/cost_bench.py", "static", "--check"]
    elif suite_id == "detector-corpus":
        argv = [sys.executable, "scripts/detector_corpus.py", "--json"]
    elif suite_id == "lifecycle-acceptance":
        env["PYTHONPATH"] = str(root / "tests") + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        argv = [sys.executable, "-m", "unittest", "-v", "test_lifecycle_acceptance"]
    else:
        raise FreeSuiteError("unknown free suite")
    return argv, env


def main(argv: Optional[Sequence[str]] = None) -> int:
    values = list(argv) if argv is not None else sys.argv[1:]
    if values and values[0] == "discover-json":
        discovery = argparse.ArgumentParser()
        discovery.add_argument("mode")
        discovery.add_argument("--root", required=True)
        args = discovery.parse_args(values)
        try:
            cases = _discover_unit_tests_in_process(Path(args.root))
        except FreeSuiteError as exc:
            print("free suite: " + str(exc), file=sys.stderr)
            return 2
        print(json.dumps(cases, sort_keys=True, separators=(",", ":")))
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("suite", choices=sorted(FREE_SUITE_IDS))
    parser.add_argument("--root", required=True)
    parser.add_argument("--case", default="all")
    args = parser.parse_args(values)
    try:
        root = _root(args.root)
        command, environment = _execution(args.suite, root, args.case)
    except FreeSuiteError as exc:
        print("free suite: " + str(exc), file=sys.stderr)
        return 2
    os.chdir(root)
    try:
        os.execvpe(command[0], command, environment)
    except OSError:
        print("free suite: command could not start", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

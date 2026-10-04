"""The evaluation tiers and a rule's unit eval as Studio catalog suites.

Four engines become allowlisted suites in `policy/studio/suites.json`, each launched through the
run supervisor and, when it spends, through the targets and the spend guard:

- `rule-detection`: every rule detector over a saved replay's streams (`cost_bench.py detect`);
- `hook-matrix`: every hook under every stance variant against the recorded calls
  (`tests/fixtures/hook-calls/hook_matrix.py`);
- `micro-tier`: whether each claimed mechanism fires on the cheap model the micro manifest pins
  (`cost_bench.py replay --tier micro`);
- `unit-eval`: one rule's two-by-two against the economy concern
  (`cost_bench.py replay --design unit-economy`).

A suite whose engine files are not in the repository is left out of the catalog and refused at
launch, so an unmerged engine is absent rather than broken. The engines own every figure: a free
suite prints the engine's own output as one JSON line, and a paid suite records the engine's
`summarise --json` output verbatim beside its spend. Nothing here derives a verdict.
"""
from __future__ import annotations

import argparse
import signal
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import replay, runs, spend_guard

SCHEMA_VERSION = 1
RESULT_MARKER = "studio-eval-tier"
ANALYSIS_NAME = "eval-analysis.json"
OUTPUT_DIR = "eval"
MAX_STDOUT_BYTES = runs.MAX_REPORT_BYTES
MAX_ANALYSIS_BYTES = 8 * 1024 * 1024
PRICING_SOURCE = "api_credit"
# The model a unit eval runs: the engine requires one and has no default, so the Studio pins the
# replay's dated default rather than offering a choice the story leaves to the engine.
DEFAULT_MODEL = replay.DEFAULT_MODEL
# The arms `cost_bench.py detect` passes to `replay_detect.parse_name` (`DETECT_ARMS`).
DETECT_ARMS = ("bare", "harness", "reference", "treatment")
MAX_RAW_FILES = 5000
MAX_RAW_BYTES = 512 * 1024 * 1024
HEX40 = re.compile(r"^[0-9a-f]{40}$")
UNIT = re.compile(r"^rules\.[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PAID_TARGETS = ("branch", "draft", "installed", "release", "worktree")


class EvalTierError(ValueError):
    """An evaluation tier request Studio refuses, named by a stable code."""

    def __init__(self, message: str, code: str = "eval_refused"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Tier:
    suite_id: str
    label: str
    description: str
    issue: int
    cost_class: str
    engines: Tuple[str, ...]


TIERS: Dict[str, Tier] = {tier.suite_id: tier for tier in (
    Tier("rule-detection", "Offline rule detection",
         "Every rule detector over a saved replay's streams; calls no model.", 510, "free",
         ("scripts/cost_bench.py", "scripts/replay_detect.py")),
    Tier("hook-matrix", "Hook replay matrix",
         "Every hook, under every stance variant, against the recorded calls; calls no model.",
         511, "free",
         ("tests/fixtures/hook-calls/hook_matrix.py", "tests/fixtures/hook-calls/matrix.json")),
    Tier("micro-tier", "Micro tier",
         "Whether each claimed mechanism fires, on the cheap model the micro manifest pins.",
         512, "spends_usage",
         ("scripts/cost_bench.py", "scripts/replay_micro.py", "benchmarks/micro/tasks.json")),
    Tier("unit-eval", "Unit eval (two-by-two)",
         "One rule alone and with the economy concern, in a minimal profile, against bare.",
         797, "spends_usage",
         ("scripts/cost_bench.py", "scripts/unit_economy.py", "benchmarks/unit-economy.json")),
)}
FREE = tuple(suite for suite, tier in TIERS.items() if tier.cost_class == "free")
PAID = tuple(suite for suite, tier in TIERS.items() if tier.cost_class == "spends_usage")


def available(repository: Path) -> List[str]:
    """The tier suites whose every engine file is in `repository`, in catalog order."""
    root = Path(repository)
    return [suite for suite, tier in TIERS.items()
            if all((root / name).is_file() for name in tier.engines)]


def require_available(repository: Path, suite_id: Any) -> Tier:
    if not isinstance(suite_id, str) or suite_id not in TIERS:
        raise EvalTierError("unknown evaluation tier", "invalid_request")
    if suite_id not in available(repository):
        raise EvalTierError("the engine for %s is not in this checkout" % suite_id,
                            "eval_engine_absent")
    return TIERS[suite_id]


def _load_file_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise EvalTierError("engine %s cannot be loaded" % path.name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rule_units(repository: Path) -> List[str]:
    """Every rule the library lists, as the engine's `--unit` form, when the unit eval is present.

    The engine is the judge of a unit it cannot separate: it refuses before any spend."""
    if "unit-eval" not in available(repository):
        return []
    from . import module_library
    try:
        payload = module_library.inventory(Path(repository))
    except Exception:
        return []
    names = sorted({item["name"] for item in payload.get("modules", [])
                    if item.get("kind") == "rules"})
    return ["rules." + name for name in names if UNIT.fullmatch("rules." + name)]


def native_command(suite_id: str, parameters: Mapping[str, str], max_budget: str = "<run cap>",
                   cap: str = "<spend cap>") -> List[str]:
    """The engine command a suite runs, as a person would type it from the repository."""
    if suite_id == "rule-detection":
        return ["python3", "scripts/cost_bench.py", "detect", "--raw",
                parameters.get("raw", "<raw directory>")]
    if suite_id == "hook-matrix":
        return ["python3", "tests/fixtures/hook-calls/hook_matrix.py", "--check"]
    command = ["python3", "scripts/cost_bench.py", "replay",
               "--tag", parameters.get("revision", "<full commit>")]
    if suite_id == "micro-tier":
        command += ["--tier", "micro"]
    else:
        command += ["--design", "unit-economy", "--unit", parameters.get("unit", "<unit>"),
                    "--model", parameters.get("model", DEFAULT_MODEL)]
    return command + ["--run-cap", max_budget, "--spend-cap", cap, "--exploratory"]


def summarise_command(results: str = "<results directory>") -> List[str]:
    return ["python3", "scripts/cost_bench.py", "summarise", "--results", results, "--json"]


def catalog(repository: Path) -> Dict[str, Any]:
    present = available(repository)
    tiers = []
    for suite in present:
        tier = TIERS[suite]
        tiers.append({"id": suite, "label": tier.label, "description": tier.description,
                      "issue": tier.issue, "cost_class": tier.cost_class,
                      "target_kinds": list(PAID_TARGETS) if suite in PAID else ["installed"],
                      "command": " ".join(shlex.quote(part) for part in native_command(suite, {}))})
    return {"schema_version": SCHEMA_VERSION, "tiers": tiers,
            "units": rule_units(repository), "unit_model": DEFAULT_MODEL,
            "commands": {"summarise": " ".join(summarise_command())}}


# Requests ---------------------------------------------------------------------------------------

def _text(value: Any, pattern: "re.Pattern[str]", name: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise EvalTierError("invalid " + name, "invalid_request")
    return value


@dataclass(frozen=True)
class PaidRequest:
    suite_id: str
    target_kind: str
    target_ref: str
    unit: Optional[str]
    max_budget_usd: str
    spend_cap_usd: str
    revision: Optional[str] = None

    KEYS = frozenset(("suite", "target", "unit", "max_budget_usd", "spend_cap_usd", "revision"))

    @classmethod
    def parse(cls, value: Any, resolved: bool = False) -> "PaidRequest":
        if not isinstance(value, dict) or set(value) - cls.KEYS:
            raise EvalTierError("invalid evaluation request", "invalid_request")
        suite = value.get("suite")
        if suite not in PAID:
            raise EvalTierError("not a paid evaluation tier", "invalid_request")
        target = value.get("target")
        if (not isinstance(target, dict) or set(target) != {"kind", "ref"}
                or target.get("kind") not in PAID_TARGETS
                or not isinstance(target.get("ref"), str) or not target["ref"]
                or len(target["ref"]) > 4096 or "\0" in target["ref"]):
            raise EvalTierError("the target needs exactly a kind and a ref", "invalid_request")
        unit = None
        if suite == "unit-eval":
            unit = _text(value.get("unit"), UNIT, "unit")
        elif "unit" in value:
            raise EvalTierError("the micro tier names no unit", "invalid_request")
        try:
            maximum = replay._money(value.get("max_budget_usd"), "--max-budget-usd")
            cap = replay._money(value.get("spend_cap_usd"), "--spend-cap")
        except replay.ReplayError as exc:
            raise EvalTierError(str(exc), "invalid_request") from exc
        if Decimal(maximum) > Decimal(cap):
            raise EvalTierError("--max-budget-usd cannot exceed --spend-cap", "invalid_request")
        revision = value.get("revision")
        if resolved:
            revision = _text(revision, HEX40, "revision")
        elif revision is not None:
            raise EvalTierError("the server resolves the revision", "invalid_request")
        return cls(suite, target["kind"], target["ref"], unit, maximum, cap, revision)

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"suite": self.suite_id,
                               "target": {"kind": self.target_kind, "ref": self.target_ref},
                               "max_budget_usd": self.max_budget_usd,
                               "spend_cap_usd": self.spend_cap_usd}
        if self.suite_id == "unit-eval":
            out["unit"] = self.unit
        if self.revision is not None:
            out["revision"] = self.revision
        return out

    def parameters(self, repository: Path) -> Dict[str, str]:
        values = {"repository": str(Path(repository).resolve()), "revision": self.revision or ""}
        if self.suite_id == "unit-eval":
            values.update(unit=self.unit or "", model=DEFAULT_MODEL)
        return values


class EvalAdmission:
    """Launch the tier suites through the supervisor; paid ones through targets and the guard."""

    def __init__(self, repository: Path, state_directory: Path, supervisor: Any,
                 target_service: Any):
        self.repository = Path(repository).resolve()
        self.supervisor = supervisor
        self.replay = replay.ReplayAdmission(repository, state_directory, supervisor,
                                             target_service)
        self.confirmed_target: Optional[Dict[str, Any]] = None

    def _resolve_target(self, request: PaidRequest) -> Dict[str, Any]:
        # The replay's resolution: a full revision, a dirty worktree or edited draft refused.
        try:
            return dict(self.replay._resolve(request.target_kind, request.target_ref))
        except replay.ReplayError as exc:
            raise EvalTierError(str(exc), getattr(exc, "code", "eval_refused")) from exc

    def _resolve(self, request: PaidRequest) -> PaidRequest:
        values = request.as_dict()
        values["revision"] = self._resolve_target(request)["revision"]
        return PaidRequest.parse(values, resolved=True)

    def start_free(self, suite_id: Any, raw: Any = None) -> Dict[str, Any]:
        require_available(self.repository, suite_id)
        if suite_id not in FREE:
            raise EvalTierError("a paid tier starts through its spend preview", "invalid_request")
        root = str(self.repository)
        parameters = {"root": root}
        if suite_id == "rule-detection":
            if (not isinstance(raw, str) or not raw.startswith("/") or "\0" in raw
                    or len(raw) > 4096 or not Path(raw).is_dir()):
                raise EvalTierError("rule detection needs a saved raw directory", "invalid_request")
            parameters["raw"] = raw
        elif raw is not None:
            raise EvalTierError("the hook matrix takes no raw directory", "invalid_request")
        try:
            started = self.supervisor.start(suite_id, parameters, "installed", root)
        except runs.RunError as exc:
            raise EvalTierError(str(exc)) from exc
        return {"run_id": started["run_id"], "status": started["status"], "suite": suite_id,
                "command": " ".join(shlex.quote(part)
                                    for part in native_command(suite_id, parameters))}

    def resolve(self, value: Any) -> PaidRequest:
        request = PaidRequest.parse(value)
        require_available(self.repository, request.suite_id)
        return self._resolve(request)

    def preview_resolved(self, request: PaidRequest) -> Dict[str, Any]:
        parameters = request.parameters(self.repository)
        try:
            value = self.supervisor.spend_preview(
                request.suite_id, parameters, request.target_kind, request.target_ref,
                request.max_budget_usd, request.spend_cap_usd, PRICING_SOURCE)
        except runs.RunError as exc:
            raise EvalTierError(str(exc)) from exc
        command = native_command(request.suite_id, parameters, request.max_budget_usd,
                                 request.spend_cap_usd)
        return dict(value, request=request.as_dict(), evidence=replay.EXPLORATORY,
                    command=" ".join(shlex.quote(part) for part in command))

    def confirm(self, value: Any) -> PaidRequest:
        request = PaidRequest.parse(value, resolved=True)
        require_available(self.repository, request.suite_id)
        target = self._resolve_target(request)
        if target["revision"] != request.revision:
            raise EvalTierError("the target changed after the spend preview", "eval_target_changed")
        self.confirmed_target = target
        return request

    def start_confirmed(self, request: PaidRequest, token: Any) -> Dict[str, Any]:
        if not isinstance(token, str) or not token or len(token) > 512:
            raise EvalTierError("a paid tier needs its one-use confirmation token",
                                "invalid_request")
        try:
            started = self.supervisor.start(
                request.suite_id, request.parameters(self.repository), request.target_kind,
                request.target_ref, confirmed=token, max_budget_usd=request.max_budget_usd,
                spend_cap_usd=request.spend_cap_usd, pricing_source=PRICING_SOURCE,
                # The record must name the revision the engine runs: refuse a target that moved.
                expected_target=self.confirmed_target or {
                    "kind": request.target_kind, "ref": request.target_ref,
                    "revision": request.revision})
        except runs.RunError as exc:
            # The supervisor names a target that built differently from the confirmed one.
            moved = "target changed" in str(exc)
            raise EvalTierError(str(exc), "eval_target_changed" if moved else "eval_refused") from exc
        return {"run_id": started["run_id"], "status": started["status"],
                "suite": request.suite_id, "request": request.as_dict()}


# Results ----------------------------------------------------------------------------------------

def _bounded_read(path: Path, limit: int) -> Optional[str]:
    if path.is_symlink() or not path.is_file():
        return None
    with open(path, "rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise EvalTierError("the engine output is too large to show", "eval_result_invalid")
    return data.decode("utf-8", "replace")


def free_result(stdout: str) -> Optional[Dict[str, Any]]:
    """The last line the free runner printed with its marker, or None when it printed none."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("marker") == RESULT_MARKER:
            return value.get("result")
    return None


def result_payload(supervisor: Any, run_id: str) -> Dict[str, Any]:
    """`{schema_version, run, result, analysis_error}` for one tier run."""
    record = supervisor.show(run_id)
    suite = record.get("suite_id")
    if suite not in TIERS:
        raise runs.RunError("run is not an evaluation tier")
    run_root = supervisor._run_path(run_id).parent
    run = {"run_id": record["run_id"], "status": record["status"], "suite": suite}
    result = error = None
    if record.get("status") in runs.TERMINAL:
        if suite in FREE:
            result = free_result(_bounded_read(run_root / "stdout.log", MAX_STDOUT_BYTES) or "")
            if result is None:
                error = "the engine printed no result for this run"
        else:
            text = _bounded_read(run_root / OUTPUT_DIR / ANALYSIS_NAME, MAX_ANALYSIS_BYTES)
            recorded = json.loads(text) if text else None
            if not isinstance(recorded, dict):
                error = "no engine analysis was recorded for this run"
            else:
                result = recorded
                error = recorded.get("error")
    return {"schema_version": SCHEMA_VERSION, "run": run, "result": result,
            "analysis_error": error}


# Runner -----------------------------------------------------------------------------------------

def _emit(suite: str, result: Any) -> None:
    print(json.dumps({"marker": RESULT_MARKER, "suite": suite, "result": result},
                     sort_keys=True, separators=(",", ":")))


def run_hook_matrix(root: Path) -> Dict[str, Any]:
    """The engine's compact matrix computed now, and every cell moved from the committed one."""
    engine = _load_file_module(root / "tests" / "fixtures" / "hook-calls" / "hook_matrix.py",
                               "studio_hook_matrix")
    matrix = engine.compact(engine.compute_parallel())
    committed = json.loads(engine.MATRIX.read_text(encoding="utf-8"))
    expanded = engine.expand(matrix)
    moved = engine.differences(engine.expand(committed), expanded)
    return {"variants": matrix["variants"], "rows": matrix["rows"], "calls": matrix["calls"],
            "grid": hook_grid(matrix, expanded), "moved": moved, "runtime": matrix["runtime"]}


def hook_grid(matrix: Mapping[str, Any], expanded: Mapping[Tuple[str, str, str], str]
              ) -> Dict[str, List[Dict[str, Any]]]:
    """Per hook, each recorded call's verdict under every variant, read from the engine's `expand`."""
    return {row: [{"call": call, "cells": [expanded[(row, call, variant)]
                                           for variant in matrix["variants"]]}
                  for call in matrix["calls"]]
            for row in matrix["rows"]}


def run_rule_detection(root: Path, raw: Path) -> Dict[str, Any]:
    """`cost_bench.py detect --raw` over a copy, so the saved directory is never written."""
    if not raw.is_dir():
        raise EvalTierError("rule detection needs a saved raw directory")
    engine = _load_file_module(root / "scripts" / "replay_detect.py", "studio_replay_detect")
    with tempfile.TemporaryDirectory(prefix="studio-detect-") as scratch:
        copy = Path(scratch) / "raw"
        copied = copy_run_files(raw, copy, engine.parse_name)
        if not copied:
            raise EvalTierError("the directory holds no saved run file (<task>-<arm>-<rep>.json)")
        done = subprocess.run([sys.executable, str(root / "scripts" / "cost_bench.py"), "detect",
                               "--raw", str(copy)], cwd=str(root), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
        if done.returncode != 0:
            raise EvalTierError("detect exited %d: %s" % (done.returncode,
                                                          done.stderr.strip()[-2000:]))
        rows = [json.loads(line) for line in
                (copy / engine.DETECTIONS).read_text(encoding="utf-8").splitlines() if line.strip()]
    return {"raw": str(raw), "summary": done.stdout.strip(), "detections": rows}


def copy_run_files(raw: Path, copy: Path, parse_name: Any) -> int:
    """Copy only the top-level regular files the engine reads (`replay_detect.parse_name`).

    Both limits count what is copied, so a stream still growing cannot pass the byte cap."""
    copy.mkdir(mode=0o700)
    names = []
    with os.scandir(str(raw)) as entries:
        for entry in entries:
            if parse_name(entry.name, DETECT_ARMS) and entry.is_file(follow_symlinks=False):
                names.append(entry.name)
                if len(names) > MAX_RAW_FILES:
                    raise EvalTierError("the raw directory holds more than %d run files"
                                        % MAX_RAW_FILES)
    total = 0
    for name in sorted(names):
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(os.path.join(str(raw), name), flags), "rb") as source, \
                open(str(copy / name), "xb") as target:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_RAW_BYTES:
                    raise EvalTierError("the raw run files exceed %d bytes" % MAX_RAW_BYTES)
                target.write(chunk)
    return len(names)


def _terminate_cleanly(_signum, _frame):
    # The supervisor stops a timed-out run with SIGTERM; exiting through SystemExit lets each
    # `TemporaryDirectory` remove its copy instead of leaving it in TMPDIR.
    raise SystemExit(143)


def _read_rows(path: Path) -> List[Dict[str, Any]]:
    """The engine's native rows, read whole: every cell's arm counts, not only the two-arm names."""
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        cost = row.get("cost_usd") if isinstance(row, dict) else None
        if not isinstance(row, dict) or (cost is not None and (
                not isinstance(cost, (int, float)) or isinstance(cost, bool) or cost < 0)):
            raise EvalTierError("result line %d does not match the native schema" % number)
        rows.append(row)
    return rows


def settle_spend(results: Path, revision: str, run_cap: str, spend_cap: str,
                 returncode: int) -> Tuple[float, bool, Optional[str]]:
    """`(charged, stopped, failure)`, as the live replay settles a target.

    No output folder after a non-zero exit means cost_bench refused before any spend. Otherwise
    the sidecar is read and reconciled against the rows by `replay._read_spend`; one that is
    missing, malformed or disagrees with the rows charges the whole cap, since spend is unknown."""
    if returncode not in replay.SETTLED_EXITS:
        return (round(float(Decimal(spend_cap)), 6), True,
                "the replay exited %s, so its spend is unknown" % returncode)
    if not results.exists() and returncode != 0:
        return 0.0, False, None
    try:
        rows = (_read_rows(results / replay.RESULTS_NAME)
                if (results / replay.RESULTS_NAME).is_file() else [])
        record = replay._read_spend(results / replay.SPEND_NAME,
                                    SimpleNamespace(execution_ref=revision), run_cap, spend_cap,
                                    rows)
        if record["stopped_at_cap"] != (returncode == 1):
            raise EvalTierError("the replay's spend record does not match its exit status")
        return round(float(record["charged_spend_usd"]), 6), record["stopped_at_cap"], None
    except (OSError, ValueError, ArithmeticError, TypeError, KeyError) as exc:
        return round(float(Decimal(spend_cap)), 6), True, str(exc) or type(exc).__name__


def write_spend_result(result_path: Path, run_id: str, suite: str, spend: float,
                       stop_reason: Optional[str], ran: bool) -> None:
    value = {"schema_version": 1, "run_id": run_id, "spend_usd": spend,
             "cases": [{"id": suite, "status": "completed" if ran else "not_run",
                        "spend_usd": spend}],
             "stop_reason": stop_reason}
    descriptor = os.open(str(result_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")


def paid_command(suite: str, repository: Path, revision: str, out: Path, raw: Path,
                 run_cap: str, spend_cap: str, unit: Optional[str] = None,
                 model: Optional[str] = None) -> List[str]:
    parameters = {"revision": revision}
    if suite == "unit-eval":
        parameters.update(unit=unit or "", model=model or DEFAULT_MODEL)
    command = native_command(suite, parameters, run_cap, spend_cap)
    return ([sys.executable, str(Path(repository) / "scripts" / "cost_bench.py")] + command[2:]
            + ["--out", str(out), "--raw", str(raw)])


def run_paid(suite: str, repository: Path, revision: str, run_cap: str, spend_cap: str,
             result_path: Path, run_id: str, launch=subprocess.run, **unit_args: Any) -> int:
    """Run one paid tier, write the spend guard's result and the engine's analysis."""
    output = result_path.parent / OUTPUT_DIR
    output.mkdir(mode=0o700, exist_ok=True)
    native_out, raw = output / "out", output / "raw"
    command = paid_command(suite, repository, revision, native_out, raw, run_cap, spend_cap,
                           **unit_args)
    interrupted: Optional[BaseException] = None
    try:
        returncode = getattr(launch(command, cwd=str(repository)), "returncode", 2)
    except Exception:  # the spend is settled from what the engine left behind
        returncode = 2
    except BaseException as exc:  # cancelled or timed out: the run in flight is unaccounted
        interrupted, returncode = exc, -signal.SIGTERM
    results = native_out / revision
    spend, stopped, failure = settle_spend(results, revision, run_cap, spend_cap, returncode)
    stop_reason = ("runner_failure" if failure else "spend_cap" if stopped else
                   None if returncode == 0 else "runner_failure")
    ran = spend > 0 or returncode == 0
    write_spend_result(result_path, run_id, suite, spend, stop_reason, ran)
    done = SimpleNamespace(returncode=returncode)
    analysis: Dict[str, Any] = {"command": summarise_command(str(results)),
                                "replay_command": command[2:], "replay_exit": done.returncode,
                                "spend_usd": spend, "stopped_at_cap": stopped,
                                "evidence": replay.EXPLORATORY, "result": None, "error": None}
    if interrupted is not None:
        analysis["error"] = "the run was stopped before the engine could summarise it"
    elif (results / replay.RESULTS_NAME).is_file():
        summary = launch([sys.executable, str(Path(repository) / "scripts" / "cost_bench.py"),
                          "summarise", "--results", str(results), "--json"],
                         cwd=str(repository), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True)
        try:
            analysis["result"] = json.loads(summary.stdout)
            analysis["summarise_exit"] = summary.returncode
        except (TypeError, ValueError):
            analysis["error"] = ((summary.stderr or "").strip().splitlines() or
                                 ["the engine printed no analysis"])[-1]
    else:
        analysis["error"] = "the replay wrote no results (exit %d)" % done.returncode
    if failure:
        analysis["spend_error"] = "spend unknown, the whole cap was charged: " + failure
    (output / ANALYSIS_NAME).write_text(json.dumps(analysis, indent=2, sort_keys=True) + "\n",
                                        encoding="utf-8")
    if interrupted is not None:
        raise interrupted
    return 0 if done.returncode == 0 and not failure else (1 if stopped and not failure else 2)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m harness_core.studio.eval_tiers")
    sub = parser.add_subparsers(dest="suite", required=True)
    detect = sub.add_parser("rule-detection")
    detect.add_argument("--root", required=True)
    detect.add_argument("--raw", required=True)
    matrix = sub.add_parser("hook-matrix")
    matrix.add_argument("--root", required=True)
    for name in PAID:
        paid = sub.add_parser(name)
        paid.add_argument("--repository", required=True)
        paid.add_argument("--revision", required=True)
        if name == "unit-eval":
            paid.add_argument("--unit", required=True)
            paid.add_argument("--model", required=True)
        paid.add_argument("--max-budget-usd", required=True)
        paid.add_argument("--spend-cap", required=True)
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, _terminate_cleanly)
    try:
        if args.suite == "hook-matrix":
            _emit(args.suite, run_hook_matrix(Path(args.root).resolve()))
            return 0
        if args.suite == "rule-detection":
            _emit(args.suite, run_rule_detection(Path(args.root).resolve(), Path(args.raw)))
            return 0
        result, run_id = os.environ.get("CITIZEN_STUDIO_RESULT"), os.environ.get(
            "CITIZEN_STUDIO_RUN_ID")
        if not result or not run_id:
            raise EvalTierError("a paid tier needs its run identity and result path")
        if not HEX40.fullmatch(args.revision):
            raise EvalTierError("a paid tier needs a full resolved revision")
        extra = ({"unit": args.unit, "model": args.model} if args.suite == "unit-eval" else {})
        try:
            return run_paid(args.suite, Path(args.repository).resolve(), args.revision,
                            args.max_budget_usd, args.spend_cap, Path(result), run_id, **extra)
        except BaseException:
            # Always leave a spend result, on a cancel too: unknown spend is charged at the cap.
            if not Path(result).exists():
                write_spend_result(Path(result), run_id, args.suite,
                                   round(float(Decimal(args.spend_cap)), 6), "runner_failure",
                                   True)
            raise
    except (OSError, ValueError, ArithmeticError) as exc:
        print("studio-eval: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

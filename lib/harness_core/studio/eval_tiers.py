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
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import replay, runs, spend_guard

SCHEMA_VERSION = 1
RESULT_MARKER = "studio-eval-tier"
ANALYSIS_NAME = "eval-analysis.json"
OUTPUT_DIR = "eval"
MAX_STDOUT_BYTES = runs.MAX_REPORT_BYTES
MAX_ANALYSIS_BYTES = 8 * 1024 * 1024
PRICING_SOURCE = "api_credit"
DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_REPS = 5
HEX40 = re.compile(r"^[0-9a-f]{40}$")
UNIT = re.compile(r"^rules\.[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}$")
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
                    "--model", parameters.get("model", DEFAULT_MODEL),
                    "--reps", parameters.get("reps", str(DEFAULT_REPS))]
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
            "units": rule_units(repository), "default_model": DEFAULT_MODEL,
            "default_repetitions": DEFAULT_REPS,
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
    model: Optional[str]
    repetitions: Optional[int]
    unit: Optional[str]
    max_budget_usd: str
    spend_cap_usd: str
    revision: Optional[str] = None

    KEYS = frozenset(("suite", "target", "unit", "model", "repetitions", "max_budget_usd",
                      "spend_cap_usd", "revision"))

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
        model = repetitions = unit = None
        if suite == "unit-eval":
            unit = _text(value.get("unit"), UNIT, "unit")
            model = _text(value.get("model", DEFAULT_MODEL), MODEL, "model")
            repetitions = value.get("repetitions", DEFAULT_REPS)
            if (not isinstance(repetitions, int) or isinstance(repetitions, bool)
                    or not 1 <= repetitions <= 20):
                raise EvalTierError("repetitions must be between one and twenty", "invalid_request")
        elif any(name in value for name in ("unit", "model", "repetitions")):
            raise EvalTierError("the micro tier pins its tasks, model and reps", "invalid_request")
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
        return cls(suite, target["kind"], target["ref"], model, repetitions, unit, maximum, cap,
                   revision)

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"suite": self.suite_id,
                               "target": {"kind": self.target_kind, "ref": self.target_ref},
                               "max_budget_usd": self.max_budget_usd,
                               "spend_cap_usd": self.spend_cap_usd}
        if self.suite_id == "unit-eval":
            out.update(unit=self.unit, model=self.model, repetitions=self.repetitions)
        if self.revision is not None:
            out["revision"] = self.revision
        return out

    def parameters(self, repository: Path) -> Dict[str, str]:
        values = {"repository": str(Path(repository).resolve()), "revision": self.revision or ""}
        if self.suite_id == "unit-eval":
            values.update(unit=self.unit or "", model=self.model or "",
                          reps=str(self.repetitions))
        return values


class EvalAdmission:
    """Launch the tier suites through the supervisor; paid ones through targets and the guard."""

    def __init__(self, repository: Path, state_directory: Path, supervisor: Any,
                 target_service: Any):
        self.repository = Path(repository).resolve()
        self.supervisor = supervisor
        self.replay = replay.ReplayAdmission(repository, state_directory, supervisor,
                                             target_service)

    def _resolve(self, request: PaidRequest) -> PaidRequest:
        # The replay's resolution: a full revision, a dirty worktree or edited draft refused.
        try:
            target = self.replay._resolve(request.target_kind, request.target_ref)
        except replay.ReplayError as exc:
            raise EvalTierError(str(exc), getattr(exc, "code", "eval_refused")) from exc
        values = request.as_dict()
        values["revision"] = target["revision"]
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
        if self._resolve(request).revision != request.revision:
            raise EvalTierError("the target changed after the spend preview", "eval_target_changed")
        return request

    def start_confirmed(self, request: PaidRequest, token: Any) -> Dict[str, Any]:
        if not isinstance(token, str) or not token or len(token) > 512:
            raise EvalTierError("a paid tier needs its one-use confirmation token",
                                "invalid_request")
        try:
            started = self.supervisor.start(
                request.suite_id, request.parameters(self.repository), request.target_kind,
                request.target_ref, confirmed=token, max_budget_usd=request.max_budget_usd,
                spend_cap_usd=request.spend_cap_usd, pricing_source=PRICING_SOURCE)
        except runs.RunError as exc:
            raise EvalTierError(str(exc)) from exc
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
    moved = engine.differences(engine.expand(committed), engine.expand(matrix))
    return {"matrix": matrix, "moved": moved, "base": engine.BASE, "same": engine.SAME}


def run_rule_detection(root: Path, raw: Path) -> Dict[str, Any]:
    """`cost_bench.py detect --raw` over a copy, so the saved directory is never written."""
    if not raw.is_dir():
        raise EvalTierError("rule detection needs a saved raw directory")
    with tempfile.TemporaryDirectory(prefix="studio-detect-") as scratch:
        copy = Path(scratch) / "raw"
        shutil.copytree(str(raw), str(copy), symlinks=True)
        engine = _load_file_module(root / "scripts" / "replay_detect.py", "studio_replay_detect")
        stale = copy / engine.DETECTIONS
        if stale.exists():
            stale.unlink()
        done = subprocess.run([sys.executable, str(root / "scripts" / "cost_bench.py"), "detect",
                               "--raw", str(copy)], cwd=str(root), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
        if done.returncode != 0:
            raise EvalTierError("detect exited %d: %s" % (done.returncode,
                                                          done.stderr.strip()[-2000:]))
        rows = [json.loads(line) for line in
                (copy / engine.DETECTIONS).read_text(encoding="utf-8").splitlines() if line.strip()]
    return {"raw": str(raw), "summary": done.stdout.strip(), "detections": rows}


def _read_sidecar(path: Path, revision: str, run_cap: str, spend_cap: str) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or value.get("schema_version") != 1
            or value.get("tag") != revision
            or Decimal(str(value.get("run_cap_usd"))) != Decimal(run_cap)
            or Decimal(str(value.get("spend_cap_usd"))) != Decimal(spend_cap)
            or not isinstance(value.get("stopped_at_cap"), bool)):
        raise EvalTierError("the replay's spend record does not match its run")
    charged = value.get("charged_spend_usd")
    if (not isinstance(charged, (int, float)) or isinstance(charged, bool) or charged < 0):
        raise EvalTierError("the replay's spend record has no charged spend")
    return value


def paid_command(suite: str, repository: Path, revision: str, out: Path, raw: Path,
                 run_cap: str, spend_cap: str, unit: Optional[str] = None,
                 model: Optional[str] = None, reps: Optional[str] = None) -> List[str]:
    parameters = {"revision": revision}
    if suite == "unit-eval":
        parameters.update(unit=unit or "", model=model or "", reps=reps or "")
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
    done = launch(command, cwd=str(repository))
    results = native_out / revision
    sidecar = results / replay.SPEND_NAME
    spend, stopped = 0.0, False
    if sidecar.is_file():
        record = _read_sidecar(sidecar, revision, run_cap, spend_cap)
        spend, stopped = round(float(record["charged_spend_usd"]), 6), record["stopped_at_cap"]
    stop_reason = "spend_cap" if stopped else (None if done.returncode == 0 else "runner_failure")
    ran = spend > 0 or done.returncode == 0
    value = {"schema_version": 1, "run_id": run_id, "spend_usd": spend,
             "cases": [{"id": suite, "status": "completed" if ran else "not_run",
                        "spend_usd": spend}],
             "stop_reason": stop_reason}
    descriptor = os.open(str(result_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")
    analysis: Dict[str, Any] = {"command": summarise_command(str(results)),
                                "replay_command": command[2:], "replay_exit": done.returncode,
                                "spend_usd": spend, "stopped_at_cap": stopped,
                                "evidence": replay.EXPLORATORY, "result": None, "error": None}
    if (results / replay.RESULTS_NAME).is_file():
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
    (output / ANALYSIS_NAME).write_text(json.dumps(analysis, indent=2, sort_keys=True) + "\n",
                                        encoding="utf-8")
    return 0 if done.returncode == 0 else (1 if stopped else 2)


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
            paid.add_argument("--reps", required=True)
        paid.add_argument("--max-budget-usd", required=True)
        paid.add_argument("--spend-cap", required=True)
    args = parser.parse_args(argv)
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
        extra = ({"unit": args.unit, "model": args.model, "reps": args.reps}
                 if args.suite == "unit-eval" else {})
        return run_paid(args.suite, Path(args.repository).resolve(), args.revision,
                        args.max_budget_usd, args.spend_cap, Path(result), run_id, **extra)
    except (OSError, ValueError) as exc:
        print("studio-eval: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

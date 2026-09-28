"""Studio contract for selecting native acceptance cases and reading their durable evidence."""
from __future__ import annotations

import json
import math
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import targets


MAX_PROGRESS_BYTES = 4 * 1024 * 1024
VERDICTS = frozenset(("passed", "failed", "unverified"))
SETTLED = frozenset(("passed", "failed"))
IDENTITY_KEYS = ("kind", "client", "harness_version", "runtime_version", "client_version",
                 "platform", "source_commit", "tier_routing", "model_run")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
PROGRESS_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class NativeAcceptanceError(ValueError):
    """A native acceptance Studio request or progress record is unsafe or invalid."""


@dataclass(frozen=True)
class SpendRequest:
    max_budget_usd: str
    spend_cap_usd: str
    pricing_source: str

    @staticmethod
    def _money(value: Any, name: str) -> str:
        if not isinstance(value, str):
            raise NativeAcceptanceError(name + " must be a finite positive dollar amount")
        try:
            amount = Decimal(value)
        except InvalidOperation as exc:
            raise NativeAcceptanceError(name + " must be a finite positive dollar amount") from exc
        if not amount.is_finite() or amount <= 0 or format(amount, "f") != value:
            raise NativeAcceptanceError(name + " must be a finite positive dollar amount")
        return value

    @classmethod
    def parse(cls, value: Any) -> "SpendRequest":
        if not isinstance(value, dict) or set(value) != {
                "max_budget_usd", "spend_cap_usd", "pricing_source"}:
            raise NativeAcceptanceError("native acceptance spend request has invalid fields")
        maximum = cls._money(value["max_budget_usd"], "maximum turn budget")
        cap = cls._money(value["spend_cap_usd"], "set spend cap")
        if Decimal(maximum) > Decimal(cap):
            raise NativeAcceptanceError("maximum turn budget cannot exceed the set spend cap")
        source = value["pricing_source"]
        if source not in ("api_credit", "subscription"):
            raise NativeAcceptanceError("pricing source must be api_credit or subscription")
        return cls(maximum, cap, source)

    def public(self) -> Dict[str, Any]:
        return {"max_budget_usd": self.max_budget_usd,
                "spend_cap_usd": self.spend_cap_usd,
                "pricing_source": self.pricing_source}


def catalog(root: Path) -> Dict[str, Any]:
    try:
        source = json.loads((Path(root) / "compatibility" / "catalog.json").read_text(
            encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NativeAcceptanceError("native acceptance catalog is unavailable") from exc
    required = source.get("required_cases") if isinstance(source, dict) else None
    if not isinstance(required, list) or not required or len(required) != len(set(required)):
        raise NativeAcceptanceError("native acceptance case catalog is invalid")
    cases = []
    for case_id in required:
        if not isinstance(case_id, str) or not case_id:
            raise NativeAcceptanceError("native acceptance case catalog is invalid")
        cases.append({"id": case_id, "description": case_id.replace("-", " ").capitalize()})
    clients = []
    source_clients = source.get("clients") if isinstance(source, dict) else None
    if not isinstance(source_clients, list):
        raise NativeAcceptanceError("native acceptance client catalog is invalid")
    for value in source_clients:
        if (not isinstance(value, dict) or value.get("client") != "cli"
                or value.get("runtime") not in ("claude-code", "codex")):
            continue
        clients.append({"id": value.get("id"), "runtime": value["runtime"],
                        "platform": value.get("platform"),
                        "observed": value.get("status") == "qualified",
                        "spend_cap_supported": value["runtime"] == "claude-code",
                        "unavailable_reason": (None if value["runtime"] == "claude-code" else
                            "Codex has no in-flight dollar cap; paid Studio launch is refused.")})
    clients.sort(key=lambda item: str(item["id"]))
    return {"schema_version": 1, "clients": clients, "cases": cases,
            "default_model": "claude-haiku-4-5",
            "commands": {"run": "python3 scripts/native_acceptance.py --client CLIENT "
                                  "--cases CASES --model MODEL --progress PROGRESS --out EVIDENCE",
                         "resume": "Repeat the same command and progress log at the same commit.",
                         "retry_failed": "Select one failed case and use a fresh progress log."}}


@dataclass(frozen=True)
class Selection:
    client: str
    cases: Sequence[str]
    model: str
    source_commit: str
    progress_id: str
    target_kind: str = "installed"
    target_ref: str = "current"
    retry_source: str = ""
    retry_case: str = ""

    @classmethod
    def parse(cls, root: Path, value: Any) -> "Selection":
        base = {"client", "cases", "model", "source_commit", "progress_id"}
        target_fields = base | {"target_kind", "target_ref"}
        expanded = target_fields | {"retry_source", "retry_case"}
        if (not isinstance(value, dict)
                or set(value) not in (base, target_fields, expanded)):
            raise NativeAcceptanceError("native acceptance selection has invalid fields")
        known = catalog(root)
        clients = {item["id"]: item for item in known["clients"]}
        client = value.get("client")
        if client not in clients:
            raise NativeAcceptanceError("unknown native acceptance client")
        available = {item["id"] for item in known["cases"]}
        cases = value.get("cases")
        retry_source = value.get("retry_source", "")
        retry_case = value.get("retry_case", "")
        retry = bool(retry_source or retry_case)
        expected_count = 1 if retry else 3
        if (not isinstance(cases, list) or len(cases) != expected_count
                or any(not isinstance(case, str) for case in cases)
                or len(cases) != len(set(cases))
                or any(case not in available for case in cases)):
            raise NativeAcceptanceError(
                "select exactly one retry case" if retry else
                "select exactly three unique native acceptance cases")
        if retry and (not isinstance(retry_source, str)
                      or not PROGRESS_ID.fullmatch(retry_source)
                      or not isinstance(retry_case, str) or retry_case != cases[0]):
            raise NativeAcceptanceError("native acceptance retry identity is invalid")
        if not retry and (retry_source or retry_case):
            raise NativeAcceptanceError("native acceptance retry identity is invalid")
        model = value.get("model")
        if (not isinstance(model, str) or not model.strip() or len(model) > 200
                or "\0" in model or "\n" in model):
            raise NativeAcceptanceError("native acceptance model is invalid")
        source_commit = value.get("source_commit")
        if (not isinstance(source_commit, str)
                or source_commit and not COMMIT.fullmatch(source_commit)):
            raise NativeAcceptanceError("native acceptance source commit is invalid")
        progress_id = value.get("progress_id")
        if not isinstance(progress_id, str) or not PROGRESS_ID.fullmatch(progress_id):
            raise NativeAcceptanceError("native acceptance progress identity is invalid")
        if retry and progress_id == retry_source:
            raise NativeAcceptanceError("native acceptance retry needs a fresh progress identity")
        target_kind = value.get("target_kind", "installed")
        target_ref = value.get("target_ref", "current")
        if (target_kind not in ("installed", "release", "branch", "worktree", "draft")
                or not isinstance(target_ref, str) or not target_ref
                or len(target_ref) > 4096 or "\0" in target_ref):
            raise NativeAcceptanceError("native acceptance target request is invalid")
        return cls(client, tuple(cases), model.strip(), source_commit, progress_id,
                   target_kind, target_ref, retry_source, retry_case)

    def public(self) -> Dict[str, Any]:
        return {"client": self.client, "cases": list(self.cases), "model": self.model,
                "source_commit": self.source_commit, "progress_id": self.progress_id,
                "target_kind": self.target_kind, "target_ref": self.target_ref,
                "retry_source": self.retry_source, "retry_case": self.retry_case}


class NativeRunAdapter:
    """Bind native selection, pinned target and paid admission without owning HTTP routes."""

    def __init__(self, source_root: Path, state_directory: Path, admission: Any):
        self.source_root = Path(source_root)
        self.state_directory = Path(state_directory)
        self.admission = admission

    def _request(self, selection_value: Any, spend_value: Any) -> tuple:
        selection = Selection.parse(self.source_root, selection_value)
        if selection.retry_source:
            self.admission.validate_retry(selection)
        spend = SpendRequest.parse(spend_value)
        client = next(item for item in catalog(self.source_root)["clients"]
                      if item["id"] == selection.client)
        if not client["spend_cap_supported"]:
            raise NativeAcceptanceError(client["unavailable_reason"])
        evidence = self.state_directory / "native-evidence"
        evidence.mkdir(parents=True, exist_ok=True, mode=0o700)
        argv = [sys.executable, "-m", "harness_core.studio.native_runner", "--root", ".",
                "--source-commit", selection.source_commit, "--client", selection.client,
                "--cases", ",".join(selection.cases), "--model", selection.model,
                "--progress-id", selection.progress_id,
                "--progress", str(progress_path(evidence, selection.progress_id)),
                "--out", str(evidence_path(evidence, selection.progress_id)),
                "--retry-source", selection.retry_source or "fresh",
                "--retry-case", selection.retry_case or "fresh"]
        parameters = {
            "client": selection.client,
            "cases": ",".join(selection.cases),
            "model": selection.model,
            "source_commit": selection.source_commit or "resolve-at-launch",
            "progress_id": selection.progress_id,
            "progress": str(progress_path(evidence, selection.progress_id)),
            "out": str(evidence_path(evidence, selection.progress_id)),
            "retry_source": selection.retry_source or "fresh",
            "retry_case": selection.retry_case or "fresh",
        }
        target = {"kind": selection.target_kind, "ref": selection.target_ref,
                  "source_commit": selection.source_commit or None}
        return selection, spend, target, argv, parameters

    def preview(self, selection_value: Any, spend_value: Any) -> Dict[str, Any]:
        selection, spend, target, argv, parameters = self._request(
            selection_value, spend_value)
        value = self.admission.preview(argv=argv, case_identities=list(selection.cases),
                                       target=target, spend=spend.public(),
                                       parameters=parameters)
        required = {"estimate", "caps", "pricing", "confirmation_required",
                    "confirmation_token", "case_identities"}
        if (not isinstance(value, dict) or not required.issubset(value)
                or value.get("confirmation_required") is not True
                or value.get("case_identities") != list(selection.cases)
                or value.get("caps") != {"max_budget_usd": spend.max_budget_usd,
                                         "spend_cap_usd": spend.spend_cap_usd}
                or not isinstance(value.get("pricing"), dict)
                or value["pricing"].get("source") != spend.pricing_source
                or not isinstance(value.get("estimate"), dict)
                or not isinstance(value.get("confirmation_token"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", value["confirmation_token"])):
            raise NativeAcceptanceError("native acceptance spend preview is incomplete")
        return {name: value[name] for name in required} | {
            "selection": selection.public(), "target": target,
        }

    def start(self, selection_value: Any, spend_value: Any,
              confirmation_token: Any) -> Dict[str, Any]:
        selection, spend, target, argv, parameters = self._request(
            selection_value, spend_value)
        if not isinstance(confirmation_token, str) or not re.fullmatch(
                r"[0-9a-f]{64}", confirmation_token):
            raise NativeAcceptanceError("native acceptance confirmation token is invalid")
        value = self.admission.start(argv=argv, case_identities=list(selection.cases),
                                     target=target, spend=spend.public(),
                                     parameters=parameters,
                                     confirmation_token=confirmation_token)
        if (not isinstance(value, dict) or not isinstance(value.get("run_id"), str)
                or not isinstance(value.get("status"), str)):
            raise NativeAcceptanceError("native acceptance launch did not return a run identity")
        identity = value.get("native_target")
        if (not isinstance(identity, dict) or not COMMIT.fullmatch(
                str(identity.get("source_commit", "")))):
            raise NativeAcceptanceError("native acceptance launch target is incomplete")
        launched = Selection(selection.client, selection.cases, selection.model,
                             identity["source_commit"], selection.progress_id,
                             identity["kind"], identity["ref"],
                             selection.retry_source, selection.retry_case)
        public_target = {name: identity[name] for name in (
            "kind", "ref", "source_commit", "version", "draft", "config_digest")}
        return {"run_id": value["run_id"], "status": value["status"],
                "selection": launched.public(), "target": public_target}


class SupervisorAdmission:
    """Concrete paid-admission port backed by Studio's durable run supervisor."""

    SUITE_ID = "native-acceptance"

    def __init__(self, supervisor: Any, target_service: Any, evidence_directory: Path):
        self.supervisor = supervisor
        self.target_service = target_service
        self.evidence_directory = Path(evidence_directory)
        self.identities = self.evidence_directory.parent / "native-run-identities"
        self.identities.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.identities, 0o700)

    def _record_path(self, progress_id: str) -> Path:
        if not PROGRESS_ID.fullmatch(progress_id):
            raise NativeAcceptanceError("native acceptance progress identity is invalid")
        return self.identities / (progress_id + ".json")

    @staticmethod
    def _write_private(path: Path, value: Mapping[str, Any]) -> None:
        temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
        descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(value, stream, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _read_private(path: Path) -> Dict[str, Any]:
        descriptor = -1
        try:
            descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                    or hasattr(os, "getuid") and info.st_uid != os.getuid()
                    or info.st_size > 64 * 1024):
                raise NativeAcceptanceError("native acceptance identity is unsafe")
            with os.fdopen(descriptor, encoding="utf-8") as stream:
                descriptor = -1
                value = json.load(stream)
        except (OSError, ValueError, RecursionError) as exc:
            raise NativeAcceptanceError("native acceptance identity is unavailable") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if (not isinstance(value, dict)
                or set(value) != {"run_id", "root", "source_commit", "version"}
                or not isinstance(value.get("run_id"), str)
                or not isinstance(value.get("root"), str)
                or not Path(value["root"]).is_absolute()
                or not isinstance(value.get("source_commit"), str)
                or not COMMIT.fullmatch(value["source_commit"])
                or not isinstance(value.get("version"), str)):
            raise NativeAcceptanceError("native acceptance identity is incomplete")
        return value

    def _source_for_commit(self, commit: Any) -> Optional[Path]:
        if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
            return None
        try:
            paths = sorted(self.identities.glob("*.json"))
        except OSError:
            return None
        for path in reversed(paths):
            try:
                value = self._read_private(path)
            except NativeAcceptanceError:
                continue
            root = Path(value["root"])
            if value["source_commit"] == commit and root.is_dir() and not root.is_symlink():
                return root
        return None

    def validate_retry(self, selection: Selection) -> None:
        saved = self._read_private(self._record_path(selection.retry_source))
        if (saved["source_commit"] != selection.source_commit
                or selection.target_kind != "branch"
                or selection.target_ref != selection.source_commit):
            raise NativeAcceptanceError("native acceptance retry target changed")
        original = Selection(
            selection.client, (selection.retry_case,), selection.model,
            selection.source_commit, selection.retry_source,
            "branch", selection.source_commit)
        snapshot = progress_snapshot(
            Path(saved["root"]), progress_path(self.evidence_directory, selection.retry_source),
            original)
        if snapshot["cases"][0]["status"] != "failed":
            raise NativeAcceptanceError("only a failed case can use retry admission")

    def _execution_target(self, target: Mapping[str, Any]) -> tuple:
        reused = self._source_for_commit(target.get("source_commit"))
        if reused is not None:
            return targets.TargetService(reused), "installed", "current"
        return self.target_service, target["kind"], target["ref"]

    def _validate_target(self, service: Any, kind: str, ref: str,
                         expected: Any) -> None:
        temporary = Path(tempfile.mkdtemp(prefix="native-target-",
                                          dir=str(self.identities)))
        try:
            resolved = service.build(kind, ref, temporary / "target")
        finally:
            shutil.rmtree(str(temporary), ignore_errors=True)
        if expected and resolved.get("revision") != expected:
            raise targets.TargetError("native acceptance target commit changed")

    def preview(self, *, target: Mapping[str, Any], spend: Mapping[str, str],
                parameters: Mapping[str, str], case_identities: Sequence[str],
                **unused: Any) -> Dict[str, Any]:
        service, kind, ref = self._execution_target(target)
        try:
            self._validate_target(service, kind, ref, target.get("source_commit"))
        except targets.TargetError as exc:
            raise NativeAcceptanceError(str(exc)) from exc
        value = self.supervisor.spend_preview(
            self.SUITE_ID, parameters, kind, ref,
            spend["max_budget_usd"], spend["spend_cap_usd"], spend["pricing_source"])
        value["estimate"] = native_estimate(self.evidence_directory, len(case_identities))
        value["case_identities"] = list(case_identities)
        return value

    def start(self, *, target: Mapping[str, Any], spend: Mapping[str, str],
              parameters: Mapping[str, str], confirmation_token: str,
              **unused: Any) -> Dict[str, Any]:
        service, kind, ref = self._execution_target(target)
        original = self.supervisor.target_service
        self.supervisor.target_service = service
        try:
            value = self.supervisor.start(
                self.SUITE_ID, parameters, kind, ref,
                confirmed=confirmation_token, max_budget_usd=spend["max_budget_usd"],
                spend_cap_usd=spend["spend_cap_usd"], pricing_source=spend["pricing_source"])
        finally:
            self.supervisor.target_service = original
        root = self.evidence_directory.parent / "targets" / value["run_id"] / "source"
        try:
            commit = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                text=True, timeout=5, check=False).stdout.strip()
            version = (root / "VERSION").read_text(encoding="utf-8").strip()
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            raise NativeAcceptanceError("native acceptance launch target is incomplete") from exc
        if not COMMIT.fullmatch(commit):
            raise NativeAcceptanceError("native acceptance launch target is incomplete")
        self._write_private(self._record_path(parameters["progress_id"]), {
            "run_id": value["run_id"], "root": str(root),
            "source_commit": commit, "version": version,
        })
        identity = self.identity(value["run_id"], parameters["progress_id"])
        return dict(value, native_target=identity)

    def identity(self, run_id: str, progress_id: str) -> Dict[str, Any]:
        saved = self._read_private(self._record_path(progress_id))
        if saved["run_id"] != run_id:
            raise NativeAcceptanceError("native acceptance run identity is unavailable")
        record = self.supervisor.show(run_id)
        if record.get("suite_id") != self.SUITE_ID:
            raise NativeAcceptanceError("native acceptance run identity is unavailable")
        return {"kind": "branch", "ref": saved["source_commit"],
                "source_commit": saved["source_commit"], "version": saved["version"],
                "draft": None, "config_digest": None, "root": saved["root"],
                "status": record.get("status")}

    def progress(self, progress_id: str) -> Dict[str, Any]:
        saved = self._read_private(self._record_path(progress_id))
        return self.identity(saved["run_id"], progress_id)


def native_estimate(evidence_directory: Path, case_count: int) -> Dict[str, Any]:
    """Estimate selected native cases from complete progress sets, including resumed spend."""
    samples = []
    directory = Path(evidence_directory)
    try:
        paths = sorted(directory.glob("native-*.partial.jsonl"))[-100:]
    except OSError:
        paths = []
    for path in paths:
        final = path.with_name(path.name[:-len(".partial.jsonl")] + ".json")
        if not final.is_file() or final.is_symlink():
            continue
        try:
            rows = [json.loads(line) for line in _read(path).splitlines() if line]
        except (NativeAcceptanceError, ValueError, RecursionError):
            continue
        latest: Dict[str, Mapping[str, Any]] = {}
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("case"), str):
                latest[row["case"]] = row
        settled = [row for row in latest.values() if row.get("result") in SETTLED]
        amounts = [row.get("spend_usd") for row in settled]
        if (len(settled) == case_count
                and all(isinstance(amount, (int, float)) and not isinstance(amount, bool)
                        and math.isfinite(amount) and amount >= 0 for amount in amounts)):
            samples.append(sum(float(amount) for amount in amounts))
    return {
        "amount_usd": round(float(median(samples)), 6) if samples else None,
        "basis": "median_same_suite_scale" if samples else "no_history",
        "sample_count": len(samples), "suite_id": SupervisorAdmission.SUITE_ID,
        "case_count": case_count,
    }


def progress_path(run_directory: Path, progress_id: str) -> Path:
    if not PROGRESS_ID.fullmatch(progress_id):
        raise NativeAcceptanceError("native acceptance progress identity is invalid")
    return Path(run_directory) / ("native-" + progress_id + ".partial.jsonl")


def evidence_path(run_directory: Path, progress_id: str) -> Path:
    if not PROGRESS_ID.fullmatch(progress_id):
        raise NativeAcceptanceError("native acceptance progress identity is invalid")
    return Path(run_directory) / ("native-" + progress_id + ".json")


def command(root: Path, run_directory: Path, selection: Selection) -> List[str]:
    client = next((item for item in catalog(root)["clients"]
                   if item["id"] == selection.client), None)
    if not client or not client["spend_cap_supported"]:
        raise NativeAcceptanceError((client or {}).get("unavailable_reason")
                                    or "native acceptance client is unavailable")
    try:
        resolved = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
            timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise NativeAcceptanceError("native acceptance target commit is unavailable") from exc
    if resolved.returncode or resolved.stdout.strip() != selection.source_commit:
        raise NativeAcceptanceError("native acceptance target commit changed before launch")
    return [sys.executable, str(Path(root) / "scripts" / "native_acceptance.py"),
            "--client", selection.client, "--cases", ",".join(selection.cases),
            "--model", selection.model,
            "--progress", str(progress_path(run_directory, selection.progress_id)),
            "--out", str(evidence_path(run_directory, selection.progress_id))]


def cli_equivalent(root: Path, run_directory: Path, selection: Selection) -> str:
    return " ".join(shlex.quote(token) for token in command(root, run_directory, selection))


def _read(path: Path) -> str:
    descriptor = -1
    try:
        descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PROGRESS_BYTES
                or stat.S_IMODE(info.st_mode) & 0o022):
            raise NativeAcceptanceError(
                "native acceptance progress must be a bounded, non-writable regular file")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise NativeAcceptanceError("native acceptance progress is owned by another user")
        chunks = []
        remaining = info.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) != info.st_size:
            raise NativeAcceptanceError("native acceptance progress changed while it was read")
        return data.decode("utf-8")
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise NativeAcceptanceError("native acceptance progress is unreadable or unsafe") from exc
    except UnicodeError as exc:
        raise NativeAcceptanceError("native acceptance progress is not UTF-8") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def progress_snapshot(root: Path, path: Path, selection: Selection, *,
                      interrupted: bool = False) -> Dict[str, Any]:
    text = _read(path)
    latest: Dict[str, Mapping[str, Any]] = {}
    warnings = []
    lines = text.splitlines()
    parsed = []
    for index, raw in enumerate(lines):
        try:
            item = json.loads(raw)
        except ValueError:
            torn = index == len(lines) - 1 and not text.endswith("\n")
            warnings.append("Ignored a torn final progress line." if torn else
                            "Ignored a malformed progress line.")
            continue
        if isinstance(item, dict):
            parsed.append(item)
    matching = [item for item in parsed
                if item.get("client") == selection.client
                and item.get("source_commit") == selection.source_commit
                and item.get("model_run") == selection.model]
    declarations = [item for item in matching if item.get("declared") == "tier_routing"]
    identity_source = declarations[-1] if declarations else next(
        (item for item in reversed(matching) if item.get("case")), None)
    identity = ({key: identity_source.get(key) for key in IDENTITY_KEYS}
                if identity_source is not None else None)
    identities = {json.dumps({key: item.get(key) for key in IDENTITY_KEYS}, sort_keys=True)
                  for item in matching if item.get("case")}
    if len(identities) > 1:
        warnings.append("Showing the latest matching client and routing identity only.")
    for item in matching:
        if not item.get("case"):
            continue
        if identity is not None and any(item.get(key) != identity[key] for key in IDENTITY_KEYS):
            continue
        case = item.get("case")
        result = item.get("result")
        observation = item.get("observation")
        if case not in selection.cases or result not in VERDICTS or not isinstance(observation, str):
            warnings.append("Ignored an invalid native acceptance case record.")
            continue
        latest[case] = item
    cases = []
    for case in selection.cases:
        item = latest.get(case)
        if item is None:
            cases.append({"id": case, "status": "pending", "observation": None,
                          "seconds": None, "sessions": None, "spend_usd": None,
                          "price_as_of": None})
            continue
        spend = item.get("spend_usd")
        known_spend = (isinstance(spend, (int, float)) and not isinstance(spend, bool)
                       and math.isfinite(spend) and spend >= 0)
        seconds = item.get("seconds")
        valid_seconds = (isinstance(seconds, (int, float)) and not isinstance(seconds, bool)
                         and math.isfinite(seconds) and seconds >= 0)
        sessions = item.get("sessions")
        valid_sessions = (isinstance(sessions, int) and not isinstance(sessions, bool)
                          and sessions >= 0)
        cases.append({"id": case, "status": item["result"],
                      "observation": item["observation"],
                      "seconds": seconds if valid_seconds else None,
                      "sessions": sessions if valid_sessions else None,
                      "spend_usd": float(spend) if known_spend else None,
                      "price_as_of": (item.get("price_as_of")
                                      if isinstance(item.get("price_as_of"), str) else None)})
    settled_count = sum(item["status"] in SETTLED for item in cases)
    try:
        equivalent = cli_equivalent(root, path.parent, selection)
    except NativeAcceptanceError as exc:
        equivalent = "Launch unavailable: " + str(exc)
    return {"schema_version": 1, "selection": selection.public(), "cases": cases,
            "settled_count": settled_count, "eligible_count": len(cases),
            "interrupted": interrupted, "warnings": warnings,
            "resume_cases": [item["id"] for item in cases
                             if item["status"] in ("pending", "unverified")],
            "command": equivalent}


def failed_retry(selection: Selection, snapshot: Mapping[str, Any], case: str) -> Selection:
    states = {item.get("id"): item.get("status") for item in snapshot.get("cases", [])
              if isinstance(item, dict)}
    if case not in selection.cases or states.get(case) != "failed":
        raise NativeAcceptanceError("only a failed selected case can use a fresh retry log")
    return Selection(selection.client, (case,), selection.model, selection.source_commit,
                     uuid.uuid4().hex, "branch", selection.source_commit,
                     selection.progress_id, case)

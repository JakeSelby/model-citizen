#!/usr/bin/env python3
"""Verify one offline evidence bundle without executing anything it contains.

The versioned contract is documented in ``docs/evidence-bundles.md``. Verification returns a
machine-readable record; callers decide how to render it or which exit code to use.
"""
import datetime
import hashlib
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import experiment_protocol  # noqa: E402
import replay_arms  # noqa: E402
import replay_stats  # noqa: E402

SCHEMA_VERSION = 1
INDEX = "bundle.json"
ARMS = ("bare", "harness")
ESTIMANDS = ("intention-to-treat", "adherence", "complier-effect", "hypothetical")
SURFACE_FIELDS = tuple("init_" + name for name in
                       ("skills", "agents", "slash_commands", "tools", "mcp_servers", "memory_paths"))
SURFACE_HASH_FIELDS = tuple(name + "_sha256" for name in SURFACE_FIELDS)
# A card whose claim text speaks to reasoning effort or the loaded runtime surface verifies only
# when the rows observed that fact for every attempt of both arms; a request is not an observation.
OBSERVATION_CLAIMS = (
    ("effort", re.compile(r"\beffort\b", re.IGNORECASE)),
    ("surface", re.compile(r"\b(?:surface|parity)\b|\bloaded (?:skills|agents|tools|commands|"
                           r"mcp servers|memory)\b", re.IGNORECASE)),
)
ARTIFACT_KEYS = ("rows", "tasks", "plan", "github_receipt", "prices", "audits", "report",
                 "arms", "trajectories")
INDEX_KEYS = ("schema_version", "bundle_id", "repository", "artifacts", "design", "statistics",
              "published_figures", "evidence_cards", "items")


class StrictJSONError(ValueError):
    pass


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise StrictJSONError("duplicate JSON key %r" % key)
        out[key] = value
    return out


def _constant(value):
    raise StrictJSONError("non-finite JSON number %s" % value)


def strict_json(data, label):
    try:
        value = json.loads(data, object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, TypeError) as exc:
        raise StrictJSONError("%s is not strict JSON: %s" % (label, exc)) from exc
    if not _finite_json(value):
        raise StrictJSONError("%s contains a non-finite JSON number" % label)
    return value


def _finite_json(value):
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite_json(item) for item in value.values())
    if isinstance(value, list):
        return all(_finite_json(item) for item in value)
    return True


def _keys(value, required, label):
    if not isinstance(value, dict):
        raise StrictJSONError("%s is not an object" % label)
    missing = sorted(set(required) - set(value))
    extra = sorted(set(value) - set(required))
    if missing or extra:
        raise StrictJSONError("%s keys differ (missing %s; unexpected %s)" %
                              (label, missing, extra))


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _full_sha(value):
    return isinstance(value, str) and len(value) == 40 \
        and all(character in "0123456789abcdef" for character in value)


def _digest(value):
    return isinstance(value, str) and len(value) == 64 \
        and all(character in "0123456789abcdef" for character in value)


def _safe_path(root, relative, label, directory=False):
    if not isinstance(relative, str) or not relative or os.path.isabs(relative):
        raise StrictJSONError("%s path must be a nonempty relative path" % label)
    parts = Path(relative).parts
    if any(part in ("", ".", "..") for part in parts):
        raise StrictJSONError("%s path contains an unsafe segment" % label)
    current = Path(root).resolve()
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise StrictJSONError("%s path traverses a symlink" % label)
    try:
        current.resolve().relative_to(Path(root).resolve())
    except ValueError as exc:
        raise StrictJSONError("%s path escapes the bundle" % label) from exc
    if directory and not current.is_dir():
        raise StrictJSONError("%s directory does not exist" % label)
    if not directory and not current.is_file():
        raise StrictJSONError("%s file does not exist" % label)
    return current


def _read_ref(root, ref, label, metadata=()):
    _keys(ref, ("path", "sha256") + tuple(metadata), label)
    if not _digest(ref["sha256"]):
        raise StrictJSONError("%s sha256 is malformed" % label)
    path = _safe_path(root, ref["path"], label)
    data = path.read_bytes()
    if _sha(data) != ref["sha256"]:
        raise StrictJSONError("%s sha256 does not match" % label)
    return path, data


def _load_pricing():
    spec = importlib.util.spec_from_file_location("evidence_pricing", ROOT / "policy" / "hooks" / "pricing.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PRICING = _load_pricing()


def _git_env():
    env = dict((key, value) for key, value in os.environ.items() if not key.startswith("GIT_"))
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
               GIT_CONFIG_NOSYSTEM="1", GIT_EXTERNAL_DIFF="", GIT_PAGER="cat", PAGER="cat",
               GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    return env


def _git(repo, *args):
    command = ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
               "-c", "diff.external=", "-c", "pager.show=false", "-C", str(repo)] + list(args)
    try:
        done = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, env=_git_env(), timeout=30)
    except subprocess.TimeoutExpired:
        return 124, ""
    return done.returncode, done.stdout.strip()


def _git_bytes(repo, *args):
    command = ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
               "-c", "diff.external=", "-c", "pager.show=false", "-C", str(repo)] + list(args)
    try:
        done = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=_git_env(), timeout=30)
    except subprocess.TimeoutExpired:
        return 124, b""
    return done.returncode, done.stdout


def _time(value, label):
    if not isinstance(value, str):
        raise ValueError("%s is not an ISO timestamp" % label)
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("%s is not an ISO timestamp" % label) from exc
    if parsed.tzinfo is None:
        raise ValueError("%s has no timezone" % label)
    return parsed.timestamp()


def _jsonl(data, label):
    rows = []
    for number, line in enumerate(data.decode("utf-8").splitlines(), 1):
        if not line.strip():
            raise StrictJSONError("%s line %d is blank" % (label, number))
        row = strict_json(line, "%s line %d" % (label, number))
        if not isinstance(row, dict):
            raise StrictJSONError("%s line %d is not an object" % (label, number))
        rows.append(row)
    if not rows:
        raise StrictJSONError("%s has no rows" % label)
    return rows


def load_bundle(directory):
    """Load and hash every declared artifact, rejecting traversal, symlinks and loose JSON."""
    root = Path(directory).resolve()
    index_path = _safe_path(root, INDEX, "bundle index")
    index = strict_json(index_path.read_text(encoding="utf-8"), "bundle index")
    _keys(index, INDEX_KEYS, "bundle index")
    if index["schema_version"] != SCHEMA_VERSION:
        raise StrictJSONError("bundle schema is %r, expected %d" %
                              (index["schema_version"], SCHEMA_VERSION))
    if not isinstance(index["bundle_id"], str) or not index["bundle_id"]:
        raise StrictJSONError("bundle_id is empty")
    _keys(index["repository"], ("path", "name", "run_commit"), "repository")
    repo = _safe_path(root, index["repository"]["path"], "repository", directory=True)
    git_dir = repo / ".git"
    if not git_dir.is_dir():
        raise StrictJSONError("repository is not a self-contained Git checkout")
    if any((git_dir / "objects/info").glob("*alternates")) \
            or any(path.is_symlink() for path in git_dir.rglob("*")):
        raise StrictJSONError("repository Git data reaches outside the bundle")
    executable_keys = ("fsmonitor=", "hookspath=", "external=", "textconv=", "helper=",
                       "sshcommand=", "pager=")
    for config_path in git_dir.glob("config*"):
        if not config_path.is_file():
            continue
        compact_config = "".join(config_path.read_text(encoding="utf-8").lower().split())
        if "[include" in compact_config:
            raise StrictJSONError("repository Git config includes external configuration")
        if any(key in compact_config for key in executable_keys):
            raise StrictJSONError("repository Git config declares an executable helper")
    artifacts = index["artifacts"]
    _keys(artifacts, ARTIFACT_KEYS, "artifacts")
    loaded = {"root": root, "index": index, "repository": repo, "raw": {}}
    metadata = {"plan": ("git_path", "commit"), "tasks": ("git_path",)}
    for name in ("rows", "tasks", "plan", "github_receipt", "prices", "audits", "report"):
        path, data = _read_ref(root, artifacts[name], "artifact %s" % name,
                               metadata.get(name, ()))
        loaded["raw"][name] = (path, data)
    if not isinstance(artifacts["arms"], list) or len(artifacts["arms"]) != 2:
        raise StrictJSONError("artifacts.arms must contain two records")
    loaded["raw"]["arms"] = [_read_ref(root, ref, "arm record %d" % number)
                              for number, ref in enumerate(artifacts["arms"], 1)]
    if not isinstance(artifacts["trajectories"], list):
        raise StrictJSONError("artifacts.trajectories is not a list")
    loaded["raw"]["trajectories"] = [
        (_read_ref(root, ref, "trajectory %d" % number, ("task", "arm", "trial")), ref)
        for number, ref in enumerate(artifacts["trajectories"], 1)]
    return loaded


def _error(errors, item, message):
    errors.append("item %d: %s" % (item, message))


def _token_cost(row, table):
    if not isinstance(row.get("model"), str) or not row["model"]:
        return None, "model is unavailable"
    values = {}
    for target, source in (("input", "input_tokens"), ("output", "output_tokens"),
                           ("cache_read", "cache_read_input_tokens"),
                           ("cache_write", "cache_creation_input_tokens")):
        value = row.get(source)
        if type(value) is not int or value < 0:
            return None, "%s is unavailable" % source
        values[target] = value
    one_hour = row.get("cache_write_1h", 0)
    if type(one_hour) is not int or one_hour < 0 or one_hour > values["cache_write"]:
        return None, "cache_write_1h is invalid"
    values["cache_write_1h"] = one_hour
    values["cache_write_5m"] = values["cache_write"] - one_hour
    rate = PRICING.price_for(table, row.get("model"))
    if rate is None:
        return None, "model %r is unpriced" % row.get("model")
    try:
        cost = PRICING.tokens_cost(values, rate)
    except OverflowError:
        return None, "derived cost is non-finite"
    return (round(cost, 6), None) if math.isfinite(cost) else (None, "derived cost is non-finite")


def _icc(rows, arm, field):
    """Shrout-Fleiss ICC(1,1), tasks as clusters, separately within one arm."""
    groups = {}
    for row in rows:
        if row.get("arm") != arm:
            continue
        if not isinstance(row.get("task"), str):
            return {"value": None, "design_effect": None,
                    "reason": "a trial has no valid task cluster", "m": None,
                    "clusters": len(groups)}
        value = (1.0 if row.get("passed") and not row.get("error") else 0.0) if field == "pass" \
            else row.get("cost_usd")
        if value is None:
            return {"value": None, "design_effect": None,
                    "reason": "a trial has no priced cost", "m": None, "clusters": len(groups)}
        groups.setdefault(row["task"], []).append(float(value))
    sizes = {len(values) for values in groups.values()}
    if len(groups) < 2:
        return {"value": None, "design_effect": None,
                "reason": "fewer than two task clusters", "m": next(iter(sizes), None),
                "clusters": len(groups)}
    if len(sizes) != 1:
        return {"value": None, "design_effect": None,
                "reason": "unequal cluster sizes are unsupported", "m": None,
                "clusters": len(groups)}
    m = next(iter(sizes))
    if m < 2:
        return {"value": None, "design_effect": None,
                "reason": "fewer than two trials per task", "m": m, "clusters": len(groups)}
    means = [sum(values) / m for values in groups.values()]
    grand = sum(means) / len(means)
    between = m * sum((mean - grand) ** 2 for mean in means) / (len(means) - 1)
    within = sum(sum((value - mean) ** 2 for value in values)
                 for values, mean in zip(groups.values(), means)) / (len(groups) * (m - 1))
    denominator = between + (m - 1) * within
    if denominator == 0:
        return {"value": None, "design_effect": None,
                "reason": "zero total variance", "m": m, "clusters": len(groups)}
    value = (between - within) / denominator
    return {"value": value, "design_effect": 1 + (m - 1) * value,
            "reason": None, "m": m, "clusters": len(groups)}


def _sample_ratio(rows, planned):
    counts = {arm: sum(1 for row in rows if row.get("arm") == arm) for arm in ARMS}
    total = sum(counts.values())
    observed = counts["bare"]
    if planned["bare"] != planned["harness"]:
        return {"counts": counts, "p_value": None, "reason": "unequal assignment needs its declared test"}
    probabilities = [math.comb(total, k) * 0.5 ** total for k in range(total + 1)]
    threshold = probabilities[observed]
    return {"counts": counts, "p_value": min(1.0, sum(p for p in probabilities if p <= threshold + 1e-15)),
            "reason": None}


def _pointer(document, pointer):
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("JSON pointer %r is invalid" % pointer)
    value = document
    for part in pointer[1:].split("/") if pointer != "/" else [""]:
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise ValueError("JSON pointer %s does not resolve" % pointer)
    return value


def _counterbalanced(design):
    expected = []
    for task in design["tasks"]:
        for trial in range(1, design["trials_per_task"] + 1):
            arms = ARMS if trial % 2 else ARMS[::-1]
            expected += [{"task": task, "trial": trial, "arm": arm} for arm in arms]
    return expected


def _not_applicable(value, phrase):
    return isinstance(value, dict) and value.get("status") == "not-applicable" \
        and isinstance(value.get("reason"), str) and phrase in value["reason"].lower()


def _arm_pair_differences(bare, harness):
    differences = []
    bare_decl, harness_decl = copy_without_treatment(bare.get("declaration") or {}), \
        copy_without_treatment(harness.get("declaration") or {})
    if bare_decl != harness_decl:
        differences.append("declarations differ beyond the treatment")
    bare_manifest = _manifest_without_treatment(bare.get("manifest") or {}, False)
    harness_manifest = _manifest_without_treatment(harness.get("manifest") or {}, True)
    if bare_manifest != harness_manifest:
        differences.append("manifests differ beyond the treatment surface")
    return differences


def copy_without_treatment(declaration):
    copied = dict(declaration)
    copied.pop("arm", None)
    copied.pop("harness", None)
    copied["components"] = [component for component in copied.get("components", [])
                            if component.get("name") != "model-citizen"]
    return copied


def _manifest_without_treatment(manifest, treatment):
    copied = dict(manifest)
    entries = list(copied.get("entries") or [])
    if treatment:
        entries = [entry for entry in entries
                   if not entry.get("path", "").startswith("harness:")
                   and entry.get("path") not in replay_arms.HARNESS_WRITES
                   and not (entry.get("kind") == "link"
                            and replay_arms._inside(entry.get("target", ""), replay_arms.HARNESS_ROOT))]
    copied["entries"] = entries
    copied["summary"] = replay_arms.arm_manifest.summary(entries)
    copied["roots"] = dict((key, value) for key, value in (copied.get("roots") or {}).items()
                           if key != "harness")
    copied["harness_commit"] = None
    return copied


def verify(directory):
    """Re-derive a bundle. All failures are returned; no bundled command is executed."""
    errors, checks, derived, cards = [], {}, {}, []
    try:
        bundle = load_bundle(directory)
    except (OSError, UnicodeError, StrictJSONError) as exc:
        return {"ok": False, "bundle_id": None, "derived": {}, "cards": [], "checks": {},
                "errors": [str(exc)], "unknown": []}
    index, repo, raw = bundle["index"], bundle["repository"], bundle["raw"]
    bundle_id = index["bundle_id"]
    items = index["items"]
    if not isinstance(items, dict) or set(items) != {str(n) for n in range(1, 13)}:
        errors.append("items must contain exactly 1 through 12")
        items = {}
    for number in range(1, 13):
        value = items.get(str(number), {})
        status = value.get("status") if isinstance(value, dict) else None
        allowed_na = number == 8
        if status != "satisfied" and not (allowed_na and status == "not-applicable"):
            _error(errors, number, "status %r is not permitted" % status)

    try:
        rows = _jsonl(raw["rows"][1], "rows")
        tasks_doc = strict_json(raw["tasks"][1].decode("utf-8"), "tasks")
        plan_text = raw["plan"][1].decode("utf-8")
        receipt = strict_json(raw["github_receipt"][1].decode("utf-8"), "GitHub receipt")
        audits = strict_json(raw["audits"][1].decode("utf-8"), "audits")
        report_text = raw["report"][1].decode("utf-8")
        arm_records = [strict_json(data.decode("utf-8"), "arm record") for _, data in raw["arms"]]
        price_doc = strict_json(raw["prices"][1].decode("utf-8"), "prices")
        price_table = PRICING.shipped_prices(raw["prices"][0])
    except (UnicodeError, ValueError, StrictJSONError, OSError) as exc:
        return {"ok": False, "bundle_id": bundle_id, "derived": {}, "cards": [], "checks": {},
                "errors": [str(exc)], "unknown": []}
    if not isinstance(price_doc, dict) or price_doc.get("schema_version") != 1 \
            or not isinstance(price_doc.get("models"), dict) or not price_doc["models"]:
        _error(errors, 4, "price artifact has no supported dated model table")
    else:
        for model, entry in sorted(price_doc["models"].items()):
            rate = PRICING.usable_rate(entry)
            try:
                datetime.date.fromisoformat(entry.get("as_of", ""))
            except (TypeError, ValueError):
                dated = False
            else:
                dated = True
            if rate is None or any(not math.isfinite(value) or value < 0 for value in rate.values()) \
                    or not dated or not isinstance(entry.get("source"), str) or not entry["source"]:
                _error(errors, 4, "price entry %s is incomplete or non-finite" % model)
    if not isinstance(audits, dict):
        for item in (2, 8, 9, 11, 12):
            _error(errors, item, "audits artifact is not an object")
        audits = {}

    repository = index["repository"]
    run_commit = repository["run_commit"]
    if not _full_sha(run_commit):
        _error(errors, 1, "run_commit is not a full sha")
    else:
        code, resolved = _git(repo, "rev-parse", "--verify", run_commit + "^{commit}")
        if code or resolved != run_commit:
            _error(errors, 1, "run_commit does not resolve exactly")

    design = index["design"]
    if not isinstance(design, dict):
        _error(errors, 3, "design is not an object")
        design = {}
    design_keys = ("model", "cli_version", "effort", "tasks", "trials_per_task", "arms",
                   "task_order_seed", "bootstrap_seed", "resamples", "model_sampling_seedable",
                   "replay_command", "verify_command", "run_cap_usd")
    try:
        _keys(design, design_keys, "design")
    except StrictJSONError as exc:
        _error(errors, 3, str(exc))
    for field in ("model", "cli_version", "effort", "replay_command", "verify_command"):
        if not isinstance(design.get(field), str) or not design.get(field):
            _error(errors, 3 if field not in ("replay_command", "verify_command") else 7,
                   "design.%s is empty" % field)
    if design.get("arms") != list(ARMS):
        _error(errors, 5, "design arms are not bare and harness")
    valid_tasks = isinstance(design.get("tasks"), list) and bool(design["tasks"]) \
        and all(isinstance(task, str) and task for task in design["tasks"]) \
        and len(set(design["tasks"])) == len(design["tasks"])
    tasks = design["tasks"] if valid_tasks else []
    if not valid_tasks:
        _error(errors, 2, "design tasks must be unique nonempty strings")
    if design.get("model_sampling_seedable") is not False:
        _error(errors, 3, "model sampling must be recorded as not seedable")
    for field in ("task_order_seed", "bootstrap_seed", "resamples", "trials_per_task"):
        if type(design.get(field)) is not int or design[field] < 1:
            _error(errors, 3, "design.%s is not a positive integer" % field)
    trials_per_task = design.get("trials_per_task") \
        if type(design.get("trials_per_task")) is int and design["trials_per_task"] > 0 else 0
    run_cap = design.get("run_cap_usd")
    if isinstance(run_cap, bool) or not isinstance(run_cap, (int, float)) \
            or not math.isfinite(run_cap) or run_cap <= 0:
        _error(errors, 4, "design.run_cap_usd is not a positive finite number")
    if isinstance(design.get("trials_per_task"), int) and design["trials_per_task"] < 5:
        _error(errors, 7, "fewer than five trials per task and arm")

    plan_ref = index["artifacts"]["plan"]
    task_ref = index["artifacts"]["tasks"]
    starts = []
    identities, ordered = set(), []
    surfaces = {arm: [] for arm in ARMS}
    effort_observations = {arm: {"observed": 0, "unknown": 0} for arm in ARMS}
    for number, row in enumerate(rows, 1):
        try:
            start = _time(row.get("started_at"), "row %d started_at" % number)
            starts.append(start)
        except ValueError as exc:
            _error(errors, 3, str(exc))
        task, arm, trial = row.get("task"), row.get("arm"), row.get("rep")
        if not isinstance(task, str) or arm not in ARMS or type(trial) is not int or trial < 1:
            _error(errors, 6, "row %d has an invalid task, arm or trial identity" % number)
        else:
            identity = (task, arm, trial)
            identities.add(identity)
            if type(row.get("schedule_index")) is not int or row["schedule_index"] < 1:
                _error(errors, 6, "row %d has an invalid schedule index" % number)
            else:
                ordered.append((row["schedule_index"], identity))
        for field, wanted in (("model", design.get("model")), ("cli_version", design.get("cli_version")),
                              ("effort", design.get("effort")),
                              ("task_order_seed", design.get("task_order_seed")),
                              ("bootstrap_seed", design.get("bootstrap_seed"))):
            if field != "model" and row.get(field) != wanted:
                _error(errors, 3, "row %d %s differs from the design" % (number, field))
        if row.get("evidence") != experiment_protocol.PREREGISTERED:
            _error(errors, 1, "row %d is not pre-registered" % number)
        if row.get("pre_registration") != plan_ref.get("git_path") \
                or row.get("pre_registration_commit") != plan_ref.get("commit"):
            _error(errors, 1, "row %d does not identify the registered plan" % number)
        if not isinstance(row.get("command"), list) or not row["command"] \
                or any(not isinstance(part, str) for part in row["command"]):
            _error(errors, 7, "row %d has no recorded argv" % number)
        for field in ("arm_image_id", "arm_declaration_sha256", "arm_manifest_sha256",
                      "arm_base_image"):
            if not isinstance(row.get(field), str) or not row[field]:
                _error(errors, 3, "row %d has no %s" % (number, field))
        if row.get("error_kind") == "timeout":
            cost = row.get("cost_usd")
            if not isinstance(cost, (int, float)) or isinstance(cost, bool) or cost != design.get("run_cap_usd"):
                _error(errors, 4, "row %d timeout cost is not its declared cap" % number)
        surface = {field: row.get(field) for field in SURFACE_FIELDS + SURFACE_HASH_FIELDS}
        known_surface = row.get("init_surface_source") == "cli-init" \
            and all(type(surface[field]) is int and surface[field] >= 0 for field in SURFACE_FIELDS) \
            and all(_digest(surface[field]) for field in SURFACE_HASH_FIELDS)
        if arm in ARMS:
            if known_surface:
                surfaces[arm].append(surface)
            else:
                _error(errors, 3, "row %d has no verified CLI init surface" % number)
            if row.get("effort") is not None and row["effort"] != design.get("effort"):
                _error(errors, 3, "row %d requested effort differs from the pin" % number)
            if "observed_effort" not in row:
                _error(errors, 3, "row %d does not distinguish observed effort from unknown" % number)
            elif row["observed_effort"] is None:
                effort_observations[arm]["unknown"] += 1
            elif not isinstance(row["observed_effort"], str) or row["observed_effort"] != design.get("effort"):
                _error(errors, 3, "row %d observed effort differs from the pin" % number)
            else:
                effort_observations[arm]["observed"] += 1

    expected_schedule = _counterbalanced(design) if valid_tasks \
        and type(design.get("trials_per_task")) is int else []
    expected_identities = {(entry["task"], entry["arm"], entry["trial"]) for entry in expected_schedule}
    if identities != expected_identities:
        _error(errors, 10, "saved attempts do not exactly equal the planned schedule")
    ordered.sort(key=lambda item: item[0])
    if [identity for _, identity in ordered] != [(e["task"], e["arm"], e["trial"])
                                                 for e in expected_schedule]:
        _error(errors, 6, "recorded schedule is not the declared counterbalanced order")
    derived["runtime_surface"] = {}
    unknown = []
    planned = len(tasks) * trials_per_task
    for arm in ARMS:
        unique = {_sha(json.dumps(surface, sort_keys=True, separators=(",", ":")).encode())
                  for surface in surfaces[arm]}
        if len(unique) > 1:
            _error(errors, 3, "%s rows report different loaded runtime surfaces" % arm)
        surface_known = planned > 0 and len(surfaces[arm]) == planned and len(unique) == 1
        effort = effort_observations[arm]
        effort_known = planned > 0 and effort["observed"] == planned
        if not surface_known:
            unknown.append("%s loaded surface: CLI init observed on %d of %d planned attempts"
                           % (arm, len(surfaces[arm]), planned))
        if not effort_known:
            unknown.append("%s effort: requested %s, observed on %d of %d planned attempts"
                           % (arm, design.get("effort"), effort["observed"], planned))
        derived["runtime_surface"][arm] = {
            "status": "verified" if surface_known else "unknown",
            "surface": surfaces[arm][0] if len(unique) == 1 else None,
            "effort": dict({"pinned": design.get("effort"),
                            "status": "verified" if effort_known else "unknown"}, **effort),
        }

    for ref, label in ((plan_ref, "plan"), (task_ref, "tasks")):
        if not isinstance(ref.get("git_path"), str) or not ref["git_path"]:
            _error(errors, 1 if label == "plan" else 2, "%s has no git_path" % label)
    if experiment_protocol.missing_fields(plan_text):
        _error(errors, 1, "pre-registration leaves required fields unfilled")
    plan_commit = plan_ref.get("commit")
    if not _full_sha(plan_commit):
        _error(errors, 1, "plan commit is not a full sha")
    elif starts:
        code, ancestor = _git(repo, "merge-base", "--is-ancestor", plan_commit, run_commit)
        if code:
            _error(errors, 1, "plan commit is not an ancestor of the run commit")
        code, stamp = _git(repo, "show", "-s", "--format=%ct", plan_commit)
        if code or not stamp.isdigit() or int(stamp) >= min(starts):
            _error(errors, 1, "plan commit postdates the first trial")
        code, registered = _git_bytes(repo, "show", "%s:%s" % (plan_commit, plan_ref.get("git_path")))
        if code or not raw["plan"][1].startswith(registered):
            _error(errors, 1, "bundle plan does not preserve its registered commit")
    for ref, data, item in ((plan_ref, raw["plan"][1], 1), (task_ref, raw["tasks"][1], 2)):
        if isinstance(run_commit, str) and isinstance(ref.get("git_path"), str):
            code, held = _git_bytes(repo, "show", "%s:%s" % (run_commit, ref["git_path"]))
            if code or held != data:
                _error(errors, item, "%s bytes differ from the run commit" % ref.get("git_path"))
    if task_ref.get("git_path") not in plan_text or task_ref.get("sha256") not in plan_text:
        _error(errors, 2, "pre-registration does not pin the task manifest path and sha256")

    try:
        _keys(receipt, ("repository", "pull_request", "plan_commit", "merge_commit", "merged_at"),
              "GitHub receipt")
        merged = _time(receipt["merged_at"], "GitHub receipt merged_at")
        if receipt["repository"] != repository["name"] or receipt["plan_commit"] != plan_commit:
            _error(errors, 1, "GitHub receipt does not identify this repository and plan")
        if type(receipt["pull_request"]) is not int or receipt["pull_request"] < 1:
            _error(errors, 1, "GitHub receipt pull request is invalid")
        if starts and merged >= min(starts):
            _error(errors, 1, "plan merge postdates the first trial")
        if not _full_sha(receipt["merge_commit"]):
            _error(errors, 1, "receipt merge commit is not a full sha")
        elif _git(repo, "merge-base", "--is-ancestor", receipt["merge_commit"], run_commit)[0]:
            _error(errors, 1, "receipt merge commit is not an ancestor of the run commit")
        elif _full_sha(plan_commit) \
                and _git(repo, "merge-base", "--is-ancestor", plan_commit, receipt["merge_commit"])[0]:
            _error(errors, 1, "registered plan is not an ancestor of the receipt merge commit")
    except (StrictJSONError, ValueError, KeyError) as exc:
        _error(errors, 1, str(exc))

    task_list = tasks_doc.get("tasks") if isinstance(tasks_doc, dict) else None
    task_ids = [task.get("id") for task in task_list] if isinstance(task_list, list) \
        and all(isinstance(task, dict) for task in task_list) else None
    if task_ids is None or any(not isinstance(task, str) for task in task_ids) \
            or sorted(task_ids) != sorted(tasks):
        _error(errors, 2, "task manifest does not exactly name the designed tasks")
    task_audits = audits.get("task_audits") if isinstance(audits, dict) else None
    audit_ids = [audit.get("task") for audit in task_audits] if isinstance(task_audits, list) \
        and all(isinstance(audit, dict) for audit in task_audits) else None
    if audit_ids is None or any(not isinstance(task, str) for task in audit_ids) \
            or sorted(audit_ids) != sorted(tasks) or any(
                not all(audit.get(key) is True
                        for key in ("task_valid", "outcome_valid", "manifest_unchanged"))
                for audit in task_audits if isinstance(audit, dict)):
        _error(errors, 2, "task validity, outcome validity and frozen-manifest audits are incomplete")

    arm_by_name = {}
    for record in arm_records:
        if not isinstance(record, dict):
            _error(errors, 3, "arm record is not an object")
            continue
        try:
            replay_arms.admit(record)
        except (SystemExit, AttributeError, KeyError, TypeError, ValueError) as exc:
            _error(errors, 3, "arm record is not admissible: %s" % exc)
            continue
        if record.get("arm") not in ARMS:
            _error(errors, 3, "arm record names an unknown arm")
            continue
        arm_by_name[record["arm"]] = record
    if set(arm_by_name) != set(ARMS):
        _error(errors, 5, "arm records do not name bare and harness")
    elif _arm_pair_differences(arm_by_name["bare"], arm_by_name["harness"]):
        _error(errors, 4, "arm records differ beyond the declared treatment")
    for number, row in enumerate(rows, 1):
        record = arm_by_name.get(row.get("arm"))
        if record and any(row.get(field) != record.get(record_field)
                          for field, record_field in (("arm_image_id", "image_id"),
                                                      ("arm_declaration_sha256", "declaration_sha256"),
                                                      ("arm_manifest_sha256", "manifest_sha256"))):
            _error(errors, 3, "row %d arm identity differs from its admitted record" % number)
        if record and row.get("arm_base_image") != (record.get("declaration") or {}).get("base_image"):
            _error(errors, 3, "row %d base image differs from its admitted record" % number)
    rebuilds = audits.get("arm_rebuilds")
    if not isinstance(rebuilds, list) or len(rebuilds) != 2 or any(
            not isinstance(entry, dict) or entry.get("equal") is not True for entry in rebuilds):
        _error(errors, 9, "two-build manifest checks are incomplete")

    fallback = 0
    priced_rows = []
    for number, row in enumerate(rows, 1):
        fallback += row.get("model") != design.get("model")
        recalculated, reason = _token_cost(row, price_table)
        if reason and row.get("error_kind") != "timeout":
            if reason.startswith("model"):
                recalculated = None
                _error(errors, 4, "row %d: %s" % (number, reason))
            else:
                _error(errors, 4, "row %d: %s" % (number, reason))
        updated = dict(row, cost_usd=recalculated)
        if row.get("error_kind") == "timeout":
            updated["cost_usd"] = row.get("cost_usd")
        priced_rows.append(updated)
    derived["fallback"] = {"trials": fallback, "rate": fallback / len(rows)}
    derived["per_task"] = {}
    for task in sorted(tasks):
        derived["per_task"][task] = {}
        for arm in ARMS:
            selected = [row for row in priced_rows
                        if row.get("task") == task and row.get("arm") == arm]
            costs = [row.get("cost_usd") for row in selected]
            passed = sum(row.get("passed") is True and row.get("error") is not True
                         for row in selected)
            total = None if None in costs else sum(costs)
            if total is not None and not math.isfinite(total):
                _error(errors, 4, "%s %s aggregate cost is non-finite" % (task, arm))
                total = None
            derived["per_task"][task][arm] = {
                "attempts": len(selected), "passes": passed, "cost_usd": total,
                "cost_of_pass": replay_stats.cost_of_pass(total, passed),
            }
    try:
        stats = index["statistics"]
        _keys(stats, ("seed", "resamples"), "statistics")
        if stats["seed"] != design.get("bootstrap_seed") or stats["resamples"] != design.get("resamples"):
            _error(errors, 5, "statistics settings differ from the design")
        derived["sm2"] = replay_stats.analyse(priced_rows, stats["seed"], stats["resamples"])
        derived["pareto"] = [dict(arm=arm, mean_cost_per_attempt=cost, pass_rate=rate, status=status)
                             for arm, cost, rate, status in replay_stats.pareto(derived["sm2"])]
    except (KeyError, TypeError, ValueError, StrictJSONError) as exc:
        _error(errors, 5, "SM-2 derivation failed: %s" % exc)
    derived["icc"] = {arm: {field: _icc(priced_rows, arm, field) for field in ("pass", "cost")}
                      for arm in ARMS}
    planned = {arm: len(tasks) * trials_per_task for arm in ARMS}
    derived["sample_ratio"] = _sample_ratio(rows, planned)
    derived["schedule_complete"] = identities == expected_identities

    trajectories = {}
    for (path_data, ref) in raw["trajectories"]:
        (_, data) = path_data
        if not isinstance(ref["task"], str) or ref["arm"] not in ARMS \
                or type(ref["trial"]) is not int or ref["trial"] < 1:
            _error(errors, 7, "trajectory has an invalid task, arm or trial identity")
            continue
        identity = (ref["task"], ref["arm"], ref["trial"])
        if identity in trajectories:
            _error(errors, 7, "duplicate trajectory for %r" % (identity,))
        trajectories[identity] = data
        text = data.decode("utf-8", errors="replace")
        if not text.strip() or "[TRUNCATED]" in text or "<truncated>" in text.lower():
            _error(errors, 7, "trajectory %r is empty or marked truncated" % (identity,))
    if set(trajectories) != expected_identities:
        _error(errors, 7, "trajectories do not exactly cover planned attempts")

    judge = audits.get("judge", {})
    item8 = items.get("8", {})
    if item8.get("status") == "not-applicable":
        if judge.get("kind") != "deterministic" or not _not_applicable(item8, "deterministic"):
            _error(errors, 8, "judge agreement is N/A only for deterministic outcomes")
    else:
        labels = judge.get("labels") if isinstance(judge, dict) else None
        bias = judge.get("bias_audits") if isinstance(judge, dict) else None
        if not isinstance(labels, list) or len(labels) < 2 or judge.get("blind") is not True \
                or not isinstance(bias, dict) or not all(bias.get(k) is True
                           for k in ("position", "order", "length", "arm_identity", "self_preference")):
            _error(errors, 8, "judge labels, blinding or bias audits are incomplete")

    contamination = audits.get("contamination", {})
    required_contamination = ("answer_unreachable", "installed_checkout_checked", "no_observed_reads",
                              "identical_access", "training_cutoffs_recorded")
    if not isinstance(contamination, dict) \
            or not all(contamination.get(key) is True for key in required_contamination):
        _error(errors, 9, "contamination evidence is incomplete")

    field = audits.get("field_checks", {})
    field = field if isinstance(field, dict) else {}
    stated_ratio = field.get("sample_ratio", {})
    if stated_ratio.get("planned") != planned or stated_ratio.get("completed") != derived["sample_ratio"]["counts"] \
            or stated_ratio.get("p_value") != derived["sample_ratio"]["p_value"]:
        _error(errors, 11, "sample-ratio check does not match planned and completed attempts")
    for name in ("novelty", "cuped", "dilution"):
        if not _not_applicable(field.get(name), "replay-only"):
            _error(errors, 11, "%s is N/A only with a replay-only reason" % name)

    if "## What we do not claim" not in report_text:
        _error(errors, 12, "report has no What we do not claim section")
    limits = audits.get("limitations", {})
    limits = limits if isinstance(limits, dict) else {}
    for name in ("models", "runtimes", "task_kinds", "magnitudes", "mechanisms", "stop_condition"):
        if not limits.get(name):
            _error(errors, 12, "limitations do not name %s" % name)

    figures = index["published_figures"]
    if not isinstance(figures, list):
        _error(errors, 10, "published_figures is not a list")
        figures = []
    if not figures:
        _error(errors, 10, "published_figures has no claims")
    for number, figure in enumerate(figures, 1):
        try:
            _keys(figure, ("name", "pointer", "value", "estimand"), "published figure %d" % number)
            if figure["estimand"] not in ESTIMANDS:
                raise ValueError("estimand %r is unsupported" % figure["estimand"])
            actual = _pointer(derived, figure["pointer"])
            if actual != figure["value"]:
                raise ValueError("declared value %r differs from derived %r" % (figure["value"], actual))
        except (StrictJSONError, ValueError) as exc:
            _error(errors, 10, "published figure %d: %s" % (number, exc))

    declared_cards = index["evidence_cards"]
    if not isinstance(declared_cards, list):
        _error(errors, 10, "evidence_cards is not a list")
        declared_cards = []
    if not declared_cards:
        _error(errors, 10, "evidence_cards has no claims")
    for number, card in enumerate(declared_cards, 1):
        verified = True
        try:
            _keys(card, ("id", "claim", "estimand", "figure", "interval", "bundle", "verify_status"),
                  "evidence card %d" % number)
            if card["estimand"] not in ESTIMANDS or card["bundle"] != bundle_id:
                raise ValueError("card estimand or bundle is invalid")
            for name in ("figure", "interval"):
                part = card[name]
                _keys(part, ("pointer", "value"), "card %s" % name)
                if _pointer(derived, part["pointer"]) != part["value"]:
                    raise ValueError("card %s differs from the derived value" % name)
            claim = card["claim"] if isinstance(card["claim"], str) else ""
            for fact, pattern in OBSERVATION_CLAIMS:
                key = "effort" if fact == "effort" else None
                statuses = [(derived["runtime_surface"][arm][key]["status"] if key
                             else derived["runtime_surface"][arm]["status"]) for arm in ARMS]
                if pattern.search(claim) and statuses != ["verified"] * len(ARMS):
                    raise ValueError("claim describes %s the rows did not observe for every attempt"
                                     % ("reasoning effort" if key else "the loaded runtime surface"))
        except (StrictJSONError, ValueError) as exc:
            verified = False
            _error(errors, 10, "evidence card %d: %s" % (number, exc))
        cards.append(dict(card, verify_status=verified) if isinstance(card, dict)
                     else {"verify_status": False})

    for item in range(1, 13):
        checks[str(item)] = not any(message.startswith("item %d:" % item) for message in errors)
    return {"ok": not errors, "bundle_id": bundle_id, "derived": derived, "cards": cards,
            "checks": checks, "errors": errors, "unknown": unknown}


def main(argv=None):
    directory = Path(argv[0]) if argv else Path.cwd()
    result = verify(directory)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""The N-arm ablation manifest (`benchmarks/ablations.json`, schema 2) and what a replay of it needs.

A schema-2 manifest names one arm per entry it toggles. Bare and control (`harness`, the harness at
the tag with nothing removed) are implicit in every run, so a manifest of N arms runs N + 2. Each
arm changes exactly one entry: `removes` switches one module off (`rules/secrets`), `sets` gives one
variant kind's unit another variant (`{"stances/voice": "off"}`). The arm's selection is declared
into its image (`replay_arms.declaration(selection=...)`), never passed by value, so the session
loads exactly what the sync rendered. A schema-1 file is #754's one-policy pair, which keeps its
own reader and launch path (`replay_pair`): `load` hands it back unchanged.

Before anything is spent: `check_entries` refuses an id the tag's default selection does not hold
switched on, a variant that does not exist and a selection the resolver refuses; `admit_arms`
refuses an arm whose declaration differs from control's in anything but its selection, or whose
selection resolves to control's profile; and `planned_mde` states the smallest effect the run can
resolve under the manifest's declared variance assumption. After the run, `surface_parity`
refuses a trial whose arm loaded a surface that differs from control's beyond its declared entry,
and `attribution_problems` names a row whose removed entry is still in its attribution.

A `removes` may also be a list of one kind's entries, a whole listing (`skills/*`): `check_entries`
then refuses it unless it names every unit of that kind the tag holds switched on. A core hook's
removal declares `core_switches_acknowledged` in its selection, as a user must to switch one off.

The extended sweep (#1184) adds what each arm is judged on. An arm may name its `layer`, `what`
it removes in words, its own pack `tasks`, its `long_session` scenarios and its pre-registered
`scores`, each a metric and its equivalence margin; the manifest's `outcome` names the task subset
every arm also runs and the outcome's margin, `pricing` the per-turn token envelope a plan is
priced with, and `unbuilt` and `excluded` the layers the sweep cannot or does not remove, each
with its reason. `sweep_plan` and `price_plan` list and price every arm before any spend, and
`justify` turns a sweep's rows into one verdict per layer: `keep` when removing it worsens one of
its scores or the outcome beyond the margin, `trim` when every score and the outcome are
equivalent within it, `no evidence` otherwise; its marginal cost is reported beside the verdict.

Standard library only, and no model call. Reading and limits: docs/benchmarks.md, "Ablation runs".
"""
import argparse
import hashlib
import json
import random
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from harness_core import catalog  # noqa: E402  the core hooks a selection must acknowledge
sys.path.insert(0, str(Path(__file__).resolve().parent))
import equivalence  # noqa: E402  the interval-inside-bounds verdict and margin checks
import oracle_metrics  # noqa: E402  behaviour scores' paired intervals
import replay_pair  # noqa: E402  the schema-1 pair, its loader and its surface parity
import replay_stats  # noqa: E402  the per-arm comparison and the minimum detectable effect

SCHEMA = 2
PAIR_SCHEMA = replay_pair.SCHEMA
BARE, CONTROL = "bare", replay_stats.CONTROL
RESERVED = (BARE, CONTROL, "control", replay_pair.REFERENCE, replay_pair.TREATMENT)
MANIFEST_KEYS = ("schema", "name", "planning", "arms")
# The extended sweep's optional keys, manifest-wide and per arm (#1184).
SWEEP_KEYS = ("justification", "pack", "outcome", "pricing", "unbuilt", "excluded")
ARM_KEYS = ("layer", "what", "tasks", "long_session", "scores")
LAYERS = ("rule", "stance", "hook", "listing", "instructions")
PLANNING_KEYS = ("cv", "source")
ARM_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
ENTRY = re.compile(r"^([a-z][a-z-]*)/([A-Za-z0-9][A-Za-z0-9._-]*)$")
TASK_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
CORE_ACK = "core_switches_acknowledged"
# The loaded-surface fields (`cost_bench.SURFACE_FIELDS`) one entry's removal may move, by kind.
# Anything else that differs from control in a trial is a difference nobody declared. A hook is
# not in the loaded surface, so its removal may move none of it.
SURFACE_MOVES = {"skills": ("init_skills", "init_slash_commands"),
                 "workflows": ("init_skills", "init_slash_commands"),
                 "roles": ("init_agents",), "rules": ("init_memory_paths",),
                 "stances": ("init_memory_paths",), "hooks": ()}
KEEP, TRIM, NO_EVIDENCE = "keep", "trim", "no evidence"
OUTCOME_METRIC = equivalence.DIFFERENCE
COST_METRIC = equivalence.RATIO
# The run cap a plan is priced at when none is given: `cost_bench.RUN_CAP_USD`, the replay default.
RUN_CAP_USD = 2.0
TURN_TOKENS = ("input", "cache_read", "cache_write", "output")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    """The manifest at `path` with its `sha256`, or SystemExit naming every problem. A schema-1
    file is returned as `replay_pair.load` reads it, with `schema` 1, for the pair path."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit("ablations: cannot read the ablation manifest %s: %s" % (path, exc))
    if isinstance(data, dict) and data.get("schema") == PAIR_SCHEMA:
        return replay_pair.load(path)
    errors = structure_errors(data)
    if errors:
        raise SystemExit("ablations: refusing the ablation manifest %s:\n  %s" % (path, "\n  ".join(errors)))
    return dict(data, sha256=sha256(path))


def structure_errors(data):
    """Every way `data` is not a schema-2 manifest, one line each."""
    if not isinstance(data, dict):
        return ["the manifest is not a JSON object"]
    errors = []
    missing = [k for k in MANIFEST_KEYS if k not in data]
    if missing:
        errors.append("missing %s" % ", ".join(missing))
    extra = sorted(set(data) - set(MANIFEST_KEYS) - set(SWEEP_KEYS))
    if extra:
        errors.append("unknown key(s) %s" % ", ".join(extra))
    if data.get("schema") != SCHEMA:
        errors.append("schema must be %d (an N-arm manifest) or %d (a pair)" % (SCHEMA, PAIR_SCHEMA))
    if not isinstance(data.get("name"), str) or not ARM_ID.match(data.get("name") or ""):
        errors.append("name must be a lower-case slug")
    planning = data.get("planning")
    if not isinstance(planning, dict) or set(planning) != set(PLANNING_KEYS) \
            or type(planning.get("cv")) not in (int, float) or not planning.get("cv") > 0 \
            or not isinstance(planning.get("source"), str) or not planning["source"].strip():
        errors.append("planning must be {cv: the assumed per-attempt coefficient of variation, above 0, "
                      "source: where that assumption comes from}")
    errors += sweep_errors(data)
    arms = data.get("arms")
    if not isinstance(arms, list) or not arms:
        errors.append("arms must be a non-empty list")
        return errors
    seen_ids, seen_entries = set(), set()
    for number, arm in enumerate(arms):
        where = "arm %d" % number
        if not isinstance(arm, dict):
            errors.append("%s is not an object" % where)
            continue
        ident = arm.get("id")
        if not isinstance(ident, str) or not ARM_ID.match(ident) or ident in RESERVED:
            errors.append("%s: id %r is not a lower-case slug other than %s" % (where, ident, ", ".join(RESERVED)))
        elif ident in seen_ids:
            errors.append("%s: id %s is used twice" % (where, ident))
        seen_ids.add(ident)
        keys = set(arm) - {"id"} - set(ARM_KEYS)
        if keys not in ({"removes"}, {"sets"}):
            errors.append("%s: give exactly one of removes or sets, and nothing else" % where)
            continue
        errors += ["%s: %s" % (where, line) for line in arm_sweep_errors(arm)]
        entry = entry_of(arm)
        if entry is None:
            errors.append("%s: %s must name one kind/unit entry%s" % (
                where, "removes" if "removes" in arm else "sets",
                ", or a list of one kind's entries" if "removes" in arm else " with one non-empty variant"))
            continue
        # A listing's bound units of another kind may recur; what an arm is named for may not.
        for one in [e for e in entries_of(arm) if e.split("/")[0] == entry.split("/")[0]]:
            if one in seen_entries:
                errors.append("%s: %s is toggled by another arm too" % (where, one))
            seen_entries.add(one)
    return errors


def _margin_problem(metric, bounds):
    """Why `bounds` is not a pre-registered margin for `metric`, or None (`equivalence.check_margin`)."""
    if not isinstance(bounds, list) or len(bounds) != 2 or any(type(b) not in (int, float) for b in bounds):
        return "bounds must be [lower, upper], two numbers"
    try:
        equivalence.check_margin(metric, bounds)
    except ValueError as exc:
        return str(exc)
    return None


def _task_list_problem(value, what):
    if not isinstance(value, list) or any(not isinstance(t, str) or not TASK_ID.match(t) for t in value) \
            or len(set(value)) != len(value):
        return "%s must be a list of distinct task ids" % what
    return None


def sweep_errors(data):
    """Every problem with the manifest-wide keys of the extended sweep, one line each."""
    errors = []
    if "justification" in data and (not isinstance(data["justification"], str) or not data["justification"].strip()):
        errors.append("justification must state the pre-registered rule in words")
    pack = data.get("pack")
    if "pack" in data and (not isinstance(pack, dict) or set(pack) != {"name", "ref"}
                           or not all(isinstance(pack[k], str) and pack[k] for k in pack)):
        errors.append("pack must be {name, ref}: the evaluator pack and the tag its task ids are read at")
    outcome = data.get("outcome")
    if "outcome" in data:
        if not isinstance(outcome, dict) or set(outcome) != {"tasks", "metric", "bounds"}:
            errors.append("outcome must be {tasks, metric, bounds}")
        else:
            problem = _task_list_problem(outcome["tasks"], "outcome tasks") or (
                None if outcome["tasks"] else "outcome tasks must not be empty")
            if outcome["metric"] != OUTCOME_METRIC:
                problem = problem or "the outcome metric is %s" % OUTCOME_METRIC
            problem = problem or _margin_problem(OUTCOME_METRIC, outcome["bounds"])
            if problem:
                errors.append("outcome: %s" % problem)
    pricing = data.get("pricing")
    if "pricing" in data:
        turn = pricing.get("turn") if isinstance(pricing, dict) else None
        if not isinstance(pricing, dict) or set(pricing) != {"turn", "source"} or not isinstance(turn, dict) \
                or set(turn) != set(TURN_TOKENS) or any(type(v) is not int or v < 0 for v in turn.values()) \
                or not isinstance(pricing.get("source"), str) or not pricing["source"].strip():
            errors.append("pricing must be {turn: {%s: tokens per turn}, source: where the envelope comes from}"
                          % ", ".join(TURN_TOKENS))
    for key in ("unbuilt", "excluded"):
        if key not in data:
            continue
        items = data[key]
        if not isinstance(items, list) or any(
                not isinstance(i, dict) or set(i) - {"layer", "what", "reason"} != set() or
                not all(isinstance(i.get(k), str) and i.get(k).strip() for k in ("what", "reason"))
                for i in items):
            errors.append("%s must be a list of {what, reason}, with an optional layer" % key)
    return errors


def arm_sweep_errors(arm):
    """Every problem with one arm's extended-sweep keys, one line each."""
    errors = []
    if "layer" in arm and arm["layer"] not in LAYERS:
        errors.append("layer must be one of %s" % ", ".join(LAYERS))
    if "what" in arm and (not isinstance(arm["what"], str) or not arm["what"].strip()):
        errors.append("what must say in words what the arm removes")
    for key in ("tasks", "long_session"):
        if key in arm:
            problem = _task_list_problem(arm[key], key)
            if problem:
                errors.append(problem)
    if "scores" in arm:
        scores = arm["scores"]
        if not isinstance(scores, list):
            errors.append("scores must be a list of {metric, bounds}")
        else:
            for score in scores:
                if not isinstance(score, dict) or set(score) != {"metric", "bounds"} \
                        or not isinstance(score.get("metric"), str):
                    errors.append("each score is {metric, bounds}")
                    continue
                if score["metric"] not in equivalence.ANALYSED and not oracle_metrics.NAME.match(score["metric"]):
                    errors.append("score %r names no metric" % score["metric"])
                    continue
                problem = _margin_problem(score["metric"], score["bounds"])
                if problem:
                    errors.append("score %s: %s" % (score["metric"], problem))
    return errors


def entries_of(arm):
    """Every `kind/unit` entry an arm toggles: one, or a whole listing's. [] when malformed."""
    value = arm.get("removes")
    if isinstance(value, list):
        if not value or any(not isinstance(v, str) or not ENTRY.match(v) for v in value) \
                or len(set(value)) != len(value):
            return []
        return list(value)
    entry = entry_of(arm)
    return [entry] if entry else []


def entry_of(arm):
    """The `kind/unit` entry an arm toggles, `kind/*` for a whole listing, or None when it names
    none well-formed. A listing is a list of distinct entries: every unit of its first entry's
    kind, then any unit of another kind that cannot stay on without them (`check_entries`)."""
    if "removes" in arm:
        value = arm.get("removes")
        if isinstance(value, list):
            entries = entries_of(arm)
            return "%s/*" % ENTRY.match(entries[0]).group(1) if entries else None
        return value if isinstance(value, str) and ENTRY.match(value) else None
    sets = arm.get("sets")
    if not isinstance(sets, dict) or len(sets) != 1:
        return None
    (entry, variant), = sets.items()
    if not isinstance(entry, str) or not ENTRY.match(entry) or not isinstance(variant, str) or not variant.strip():
        return None
    return entry


def selection(arm):
    """The arm's selection in the user-config shape the sync reads: `{kind: {unit: value}}`, with
    `core_switches_acknowledged` true when it switches a core hook off, as a user must."""
    if "sets" in arm:
        kind, unit = ENTRY.match(entry_of(arm)).groups()
        return {kind: {unit: arm["sets"][entry_of(arm)].strip()}}
    out = {}
    for entry in entries_of(arm):
        kind, unit = ENTRY.match(entry).groups()
        out.setdefault(kind, {})[unit] = "off"
    if any(unit in catalog.CORE_HOOKS for unit in out.get("hooks", {})):
        out[CORE_ACK] = True
    return out


def arm_ids(manifest):
    return tuple(arm["id"] for arm in manifest["arms"])


def arm_names(manifest):
    """Every arm a run of the manifest launches, in schedule order: bare, control, then each arm."""
    return (BARE, CONTROL) + arm_ids(manifest)


def selections(manifest):
    return {arm["id"]: selection(arm) for arm in manifest["arms"]}


def check_entries(manifest, posture, root, env=None):
    """Every arm the tag cannot run, one line each, against `root`, a checkout of the tag, resolved
    by its own `posture` module with an empty home (the image has no configuration of the user's):
    `removes` must name a switch-kind unit switched on in the default selection; `sets` must name a
    variant kind's unit and a variant that exists other than its default; and the arm's whole
    selection must resolve strictly, so a dependency or core-hook refusal is caught here."""
    env = dict(env or {}, HOME=str(Path(root) / ".ablation-empty-home"))
    kinds = posture.selection_kinds(root)
    default = posture.selection(env, strict=False, config={}, root=root)
    errors = []
    for arm in manifest["arms"]:
        entry = entries_of(arm)[0]
        kind, unit = ENTRY.match(entry).groups()
        spec = kinds.get(kind)
        switch = (spec or {}).get("value") == "switch"
        current = (default.get(kind) or {}).get(unit)
        if spec is None:
            errors.append("%s: %s names an unknown kind %s" % (arm["id"], entry, kind))
            continue
        if "removes" in arm:
            if not switch:
                errors.append("%s: removes %s, but %s is not switched; name its variant with sets"
                              % (arm["id"], entry, kind))
            for one in entries_of(arm) if switch else ():
                held, unit = ENTRY.match(one).groups()
                if (kinds.get(held) or {}).get("value") != "switch" or unit not in (default.get(held) or {}):
                    errors.append("%s: removes an unknown id %s" % (arm["id"], one))
                elif (default.get(held) or {}).get(unit) != "on":
                    errors.append("%s: removes %s, which the tag's default selection does not hold switched on"
                                  % (arm["id"], one))
            if switch and isinstance(arm["removes"], list):
                errors += ["%s: %s" % (arm["id"], line) for line in _listing_errors(arm["removes"], kind, default, root)]
        else:
            variant = arm["sets"][entry].strip()
            if switch:
                errors.append("%s: sets %s, but %s is switched; use removes" % (arm["id"], entry, kind))
            elif unit not in (default.get(kind) or {}):
                errors.append("%s: sets an unknown id %s" % (arm["id"], entry))
            elif variant == current:
                errors.append("%s: sets %s to %s, which is already its default" % (arm["id"], entry, variant))
            elif not _variant_exists(posture, kind, spec, unit, variant, root):
                errors.append("%s: sets %s to %s, a variant the tag does not ship" % (arm["id"], entry, variant))
        if errors and errors[-1].startswith(arm["id"] + ":"):
            continue
        try:
            posture.selection(env, strict=True, config=selection(arm), root=root)
        except ValueError as exc:
            errors.append("%s: the tag's resolver refuses its selection: %s"
                          % (arm["id"], str(exc).splitlines()[0]))
    return errors


def _listing_errors(removes, kind, default, root):
    """Why `removes` is not the whole `kind` listing plus only the units bound to it: it must name
    every unit of `kind` the tag holds on, and each entry of another kind must depend on a removed
    entry or be depended on by one, per the tag's module manifest."""
    errors = []
    held = sorted("%s/%s" % (kind, u) for u, v in (default.get(kind) or {}).items() if v == "on")
    left = sorted(set(held) - set(removes))
    if left:
        errors.append("removes the %s listing but leaves %s switched on" % (kind, ", ".join(left)))
    try:
        modules = json.loads((Path(root) / "primitives" / "manifests.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        modules = {}

    def needs(entry):
        k, u = ENTRY.match(entry).groups()
        return set(((modules.get(k) or {}).get(u) or {}).get("dependencies") or [])
    removed = set(removes)
    for entry in removes:
        if ENTRY.match(entry).group(1) == kind:
            continue
        if not (needs(entry) & removed or any(entry in needs(other) for other in removed)):
            errors.append("removes %s, which the %s listing does not bind" % (entry, kind))
    return errors


def _variant_exists(posture, kind, spec, unit, variant, root):
    directory = spec.get("directory")
    if not directory:
        return False
    return any((source / unit / (variant + ".md")).is_file()
               for source in posture.primitive_roots({}, root, directory))


def stripped(declaration):
    """A declaration less its selection: what an ablation arm's must share with control's."""
    out = {k: v for k, v in (declaration or {}).items() if k != "selection"}
    out["components"] = [c for c in out.get("components") or [] if c.get("name") != "selection"]
    return out


def declaration_differences(control, arm):
    """Every declaration key on which `arm` differs from `control` beyond its selection."""
    left, right = stripped(control), stripped(arm)
    return ["declaration %s: control %r, arm %r" % (key, left.get(key), right.get(key))
            for key in sorted(set(left) | set(right)) if left.get(key) != right.get(key)]


def admit_arms(records, fingerprints):
    """SystemExit naming every arm that may not run; None when all may. `records` maps arm name to
    its built record and `fingerprints` arm name to its resolved profile fingerprint. An arm whose
    declaration differs from control's beyond its selection, or whose selection resolves to
    control's own profile (it toggles nothing the resolver sees), is refused before any spend."""
    reasons = []
    control = records[CONTROL]
    for name, record in records.items():
        if name in (BARE, CONTROL):
            continue
        reasons += ["%s: %s" % (name, line) for line in
                    declaration_differences(control.get("declaration"), record.get("declaration"))]
        if record.get("declaration", {}).get("selection") is None:
            reasons.append("%s: declares no selection" % name)
        mine, theirs = fingerprints.get(name), fingerprints.get(CONTROL)
        if mine is None or theirs is None:
            reasons.append("%s: its profile fingerprint or control's cannot be resolved" % name)
        elif mine == theirs:
            reasons.append("%s: its selection resolves to control's profile %s, so it toggles nothing" % (name, mine))
    if reasons:
        raise SystemExit("ablations: refusing the run before any spend:\n  %s" % "\n  ".join(reasons))


def default_seed(manifest):
    """The schedule seed when none is given: from the manifest's own digest, so a rerun of one
    manifest orders its runs the same way. Recorded on every row either way."""
    return int(manifest["sha256"][:8], 16)


def schedule(tasks, reps, names, seed, arm_tasks=None):
    """`[(task, rep, arm)]`: every arm once per task and rep. The leading arm rotates with the rep,
    as `cost_bench.schedule` rotates it, so none always runs on another's warm cache; the arms after
    it follow a permutation drawn from `seed`, so the order is derived and reproducible. With
    `arm_tasks` (`arm_task_filter`), a task it names runs only on the arms it names; the draw is
    the same, so the arms that remain keep their relative order."""
    rng = random.Random(seed)
    out = []
    for task in tasks:
        runs = (arm_tasks or {}).get(task["id"])
        for rep in range(1, reps + 1):
            lead = names[(rep - 1) % len(names)]
            rest = [arm for arm in names if arm != lead]
            rng.shuffle(rest)
            out += [(task, rep, arm) for arm in [lead] + rest if runs is None or arm in runs]
    return out


def arm_tasks_of(arm, manifest):
    """The pack tasks one arm runs: its own, then the outcome subset every arm runs."""
    own = list(arm.get("tasks") or [])
    return own + [t for t in (manifest.get("outcome") or {}).get("tasks") or [] if t not in own]


def arm_task_filter(manifest):
    """`{task id: set of arms}` for every task the sweep names, or None when it names none. Bare and
    control run every task; an arm runs its own tasks and the outcome subset (`arm_tasks_of`). A
    task the manifest does not name is not filtered, so a fixture or another set runs every arm."""
    named = {}
    for arm in manifest["arms"]:
        for task in arm_tasks_of(arm, manifest):
            named.setdefault(task, {BARE, CONTROL}).add(arm["id"])
    return named or None


def row_stamp(manifest, arm, seed):
    """What every row of an ablation run adds: the manifest it answers, the entry its arm removed or
    set (None for bare and control), its declared selection and the schedule seed."""
    ablation = {"name": manifest["name"], "sha256": manifest["sha256"], "schema": SCHEMA,
                "arms": list(arm_ids(manifest))}
    spec = next((a for a in manifest["arms"] if a["id"] == arm), None)
    return {"ablation": ablation,
            "ablation_removes": spec.get("removes") if spec else None,
            "ablation_sets": dict(spec["sets"]) if spec and "sets" in spec else None,
            "selection": selection(spec) if spec else {}, "schedule_seed": seed}


def is_ablation(rows):
    """True when the rows answer a schema-2 manifest."""
    ablation = replay_pair.ablation_of(rows) or {}
    return ablation.get("schema") == SCHEMA


def removed_of(rows):
    """`{arm: entry}` for every row that removed or set one."""
    out = {}
    for row in rows:
        removes = row.get("ablation_removes")
        entry = entry_of({"removes": removes}) if removes else next(iter(row.get("ablation_sets") or {}), None)
        if entry:
            out[row.get("arm")] = entry
    return out


def allowance(entry):
    """The surface fields the entry's removal may move: its counts and their content hashes."""
    kind = entry.split("/", 1)[0] if isinstance(entry, str) and "/" in entry else None
    fields = SURFACE_MOVES.get(kind, ())
    return tuple(fields) + tuple(field + "_sha256" for field in fields)


def surface_parity(rows, surface=replay_pair.default_surface):
    """One reason per trial whose arm loaded a surface that differs from control's in anything but
    the fields its declared entries may move (`allowance`); `replay_pair.surface_parity` decides."""
    allowed = {}
    for row in rows:
        removes = row.get("ablation_removes")
        entries = entries_of({"removes": removes}) if removes else list(row.get("ablation_sets") or {})
        if entries:
            allowed[row.get("arm")] = tuple(sorted({f for e in entries for f in allowance(e)}))
    return replay_pair.surface_parity(rows, surface, control=CONTROL, allowed=allowed)


def attribution_problems(rows):
    """One line per row whose removed entry is still in its `context_attribution`."""
    out = []
    for row in rows:
        removes = row.get("ablation_removes")
        modules = (row.get("context_attribution") or {}).get("modules")
        if not removes or not isinstance(modules, dict):
            continue
        for entry in entries_of({"removes": removes}):
            if entry in modules:
                out.append("%s rep %s: arm %s removed %s, yet its attribution holds it"
                           % (row.get("task"), row.get("rep"), row.get("arm"), entry))
    return out


def planned_mde(manifest, tasks, reps):
    """`{attempts_per_arm, cv, source, comparisons, effect, effect_bonferroni}`: the smallest
    relative change in a per-attempt mean the run can resolve at 80% power, from the manifest's
    declared variance assumption. A planning figure, stated before any spend, never a result."""
    n = tasks * reps
    cv, m = manifest["planning"]["cv"], len(manifest["arms"])
    one = replay_stats.minimum_detectable_effect(n, cv)
    every = replay_stats.minimum_detectable_effect(n, cv, comparisons=m)
    return {"attempts_per_arm": n, "cv": cv, "source": manifest["planning"]["source"], "comparisons": m,
            "effect": None if one is None else round(one, 4),
            "effect_bonferroni": None if every is None else round(every, 4)}


def render_mde(mde):
    return ("minimum detectable effect, before any spend: %s on a per-attempt mean at %d attempts per arm, "
            "80%% power, 95%% two-sided; %s with Bonferroni over %d arm(s). Assumes a per-attempt "
            "coefficient of variation of %g (%s): a planning assumption, not a measurement; an "
            "effect smaller than this reads inconclusive"
            % ("undefined" if mde["effect"] is None else "%.1f%%" % (100 * mde["effect"]),
               mde["attempts_per_arm"],
               "undefined" if mde["effect_bonferroni"] is None else "%.1f%%" % (100 * mde["effect_bonferroni"]),
               mde["comparisons"], mde["cv"], mde["source"]))


def summarise(rows, seed=replay_stats.SEED, resamples=replay_stats.RESAMPLES, correction=None,
              surface=replay_pair.default_surface):
    """The whole ablation result from saved rows: `replay_stats.compare` against control, plus the
    post-run surface parity and the attribution check. ValueError on malformed rows."""
    result = replay_stats.compare(rows, CONTROL, seed, resamples, correction, removed_of(rows))
    parity = surface_parity(rows, surface)
    attribution = attribution_problems(rows)
    seeds = sorted({r.get("schedule_seed") for r in rows if r.get("schedule_seed") is not None})
    return dict(result, ablation=replay_pair.ablation_of(rows), schedule_seeds=seeds,
                parity={"ok": not parity and not attribution, "reasons": parity + attribution})


def render(result):
    ablation = result.get("ablation") or {}
    lines = ["Ablation %s (manifest %s), schedule seed %s"
             % (ablation.get("name"), (ablation.get("sha256") or "")[:12],
                ", ".join(str(s) for s in result["schedule_seeds"]) or "unrecorded")]
    text = "\n".join(lines) + "\n" + replay_stats.render_compare(result)
    parity = result["parity"]
    text += "\npost-run parity: %s\n" % ("every arm differed from control only in its declared entry"
                                         if parity["ok"] else "REFUSED")
    text += "".join("  %s\n" % reason for reason in parity["reasons"])
    return text


# --- The extended sweep: plan, price and justify (#1184) -----------------------------------------

def verification(arm):
    """How the arm's removal is proven to be the only difference from control, in words."""
    entries = entries_of(arm) if "removes" in arm else [entry_of(arm)]
    moves = sorted({f for e in entries for f in allowance(e)})
    return ("declares %s into its image; refused before any spend unless its declaration differs from "
            "control's only in that selection and its profile fingerprint differs from control's; after "
            "the run its loaded surface may differ from control's only in %s, and its attribution may not "
            "hold %s" % (json.dumps(selection(arm), sort_keys=True),
                         ", ".join(f for f in moves if not f.endswith("_sha256")) or "nothing",
                         ", ".join(entries)))


def sweep_plan(manifest):
    """Every arm a sweep launches, with its tasks, before any spend. `{arms, control_tasks,
    long_session, unbuilt, excluded}`: each arm's id, layer, entry, what it removes, its selection,
    its pack tasks (`arm_tasks_of`), its long-session scenarios and how its removal is verified.
    Bare and control run every task and scenario any arm names."""
    arms, every, scenarios = [], [], []
    for arm in manifest["arms"]:
        tasks = arm_tasks_of(arm, manifest)
        every += [t for t in tasks if t not in every]
        scenarios += [s for s in arm.get("long_session") or [] if s not in scenarios]
        arms.append({"id": arm["id"], "layer": arm.get("layer"), "entry": entry_of(arm),
                     "what": arm.get("what"), "selection": selection(arm), "own_tasks": list(arm.get("tasks") or []),
                     "tasks": tasks, "long_session": list(arm.get("long_session") or []),
                     "scores": list(arm.get("scores") or []), "verify": verification(arm)})
    return {"arms": arms, "control_tasks": every, "long_session": scenarios,
            "unbuilt": list(manifest.get("unbuilt") or []), "excluded": list(manifest.get("excluded") or [])}


def turn_cost(rates, envelope):
    """One turn's ceiling in USD: each token class of the envelope at its per-million rate."""
    return sum(envelope[key] * rates[key] for key in TURN_TOKENS) / 1e6


def price_plan(plan, model, rates, envelope, task_turns, scenario_caps, run_cap=RUN_CAP_USD, reps=1):
    """The plan's ceiling on one model, before any spend. A task run's ceiling is its `max_turns`
    at the envelope's per-turn cost, never above `run_cap`, the cap the replay enforces; a
    long-session scenario's is its own cost cap. `{model, reps, per_turn_usd, runs, task_runs,
    scenario_runs, task_usd, scenario_usd, total_usd, arms: {arm: usd}}`; ValueError on a task or
    scenario the pack does not hold."""
    per_turn = turn_cost(rates, envelope)

    def task_usd(task):
        if task not in task_turns:
            raise ValueError("the pack holds no task %s" % task)
        return min(task_turns[task] * per_turn, run_cap)

    def scenario_usd(scenario):
        if scenario not in scenario_caps:
            raise ValueError("the pack holds no long-session scenario %s" % scenario)
        return float(scenario_caps[scenario])

    arms = {}
    for name in (BARE, CONTROL):
        arms[name] = (sum(task_usd(t) for t in plan["control_tasks"])
                      + sum(scenario_usd(s) for s in plan["long_session"])) * reps
    for arm in plan["arms"]:
        arms[arm["id"]] = (sum(task_usd(t) for t in arm["tasks"])
                           + sum(scenario_usd(s) for s in arm["long_session"])) * reps
    task_runs = (2 * len(plan["control_tasks"]) + sum(len(a["tasks"]) for a in plan["arms"])) * reps
    scenario_runs = (2 * len(plan["long_session"]) + sum(len(a["long_session"]) for a in plan["arms"])) * reps
    scenario_total = (2 * sum(scenario_usd(s) for s in plan["long_session"])
                      + sum(scenario_usd(s) for a in plan["arms"] for s in a["long_session"])) * reps
    total = sum(arms.values())
    return {"model": model, "reps": reps, "per_turn_usd": round(per_turn, 6), "run_cap": run_cap,
            "runs": task_runs + scenario_runs, "task_runs": task_runs, "scenario_runs": scenario_runs,
            "task_usd": round(total - scenario_total, 2), "scenario_usd": round(scenario_total, 2),
            "total_usd": round(total, 2), "arms": {k: round(v, 2) for k, v in arms.items()}}


def render_plan(plan, prices=()):
    lines = ["%d removal arm(s), plus bare and control on %d task(s) and %d long-session scenario(s)"
             % (len(plan["arms"]), len(plan["control_tasks"]), len(plan["long_session"]))]
    for arm in plan["arms"]:
        lines.append("  arm %s (%s) removes %s: %s" % (arm["id"], arm["layer"] or "layer unnamed", arm["entry"],
                                                       arm["what"] or "unstated"))
        lines.append("    tasks: %s" % (", ".join(arm["tasks"]) or "none"))
        if arm["long_session"]:
            lines.append("    long-session: %s" % ", ".join(arm["long_session"]))
        lines.append("    scores: %s" % ("; ".join("%s within [%g, %g]" % (s["metric"], s["bounds"][0], s["bounds"][1])
                                                   for s in arm["scores"]) or "the outcome only"))
        lines.append("    verified: %s" % arm["verify"])
        for price in prices:
            lines.append("    ceiling on %s: %.2f USD" % (price["model"], price["arms"][arm["id"]]))
    for key, label in (("unbuilt", "not buildable at this tag"), ("excluded", "excluded")):
        for item in plan[key]:
            lines.append("  %s: %s, %s" % (label, item["what"], item["reason"]))
    for price in prices:
        lines.append("ceiling for %d rep(s) on %s: %.2f USD over %d run(s) (%.2f USD on %d task run(s) at "
                     "%.4f USD a turn, capped at %g USD a run; %.2f USD on %d long-session run(s) at their "
                     "own caps)" % (price["reps"], price["model"], price["total_usd"], price["runs"],
                                    price["task_usd"], price["task_runs"], price["per_turn_usd"],
                                    price["run_cap"], price["scenario_usd"], price["scenario_runs"]))
    return "\n".join(lines) + "\n"


def _pair_rows(rows, arm, tasks):
    """The control and `arm` rows on `tasks` both arms ran, for a paired two-arm analysis."""
    tasks = set(tasks)
    picked = [r for r in rows if r.get("arm") in (CONTROL, arm) and r.get("task") in tasks]
    both = {t for t in tasks if any(r["task"] == t and r["arm"] == CONTROL for r in picked)
            and any(r["task"] == t and r["arm"] == arm for r in picked)}
    return [r for r in picked if r["task"] in both]


def _assess(interval, bounds, direction):
    """`{assessment, harmful, better}` for one interval of arm minus control (or arm over control)
    against its margin: harmful when it lies wholly beyond the bound on the worse side."""
    assessed, reason = equivalence.verdict(interval, tuple(bounds))
    lower, upper = bounds
    harmful = better = False
    if assessed == equivalence.NOT_EQUIVALENT:
        above = interval[0] >= upper
        harmful = above if direction == "lower" else not above
        better = not harmful
    return {"assessment": assessed, "reason": reason, "harmful": harmful, "better": better}


def _score(rows, arm, tasks, metric, bounds, seed, resamples):
    sub = _pair_rows(rows, arm, tasks)
    out = {"metric": metric, "bounds": list(bounds), "tasks": sorted({r["task"] for r in sub})}
    if not sub:
        return dict(out, interval=None, assessment=equivalence.INCONCLUSIVE, harmful=False, better=False,
                    reason="no task holds rows of both control and the arm", limitation=None)
    try:
        if metric in equivalence.ANALYSED:
            result = replay_stats.analyse(sub, seed, resamples, (CONTROL, arm))
            interval = result[equivalence.ANALYSED[metric][0]]
            direction = "lower" if metric == COST_METRIC else "higher"
            limitation = result["limitation"]
        else:
            summary = oracle_metrics.summarise(sub, seed, resamples, (CONTROL, arm)) or {}
            found = (summary.get("metrics") or {}).get(metric)
            if found is None:
                return dict(out, interval=None, assessment=equivalence.INCONCLUSIVE, harmful=False, better=False,
                            reason="no row reports %s" % metric, limitation=None)
            interval, direction = found.get("difference_interval"), found["direction"]
            limitation = None if found.get("paired_tasks") else "no paired task"
    except ValueError as exc:
        return dict(out, interval=None, assessment=equivalence.INCONCLUSIVE, harmful=False, better=False,
                    reason="unavailable: %s" % exc, limitation=None)
    return dict(out, interval=interval, direction=direction, limitation=limitation,
                **_assess(interval, bounds, direction))


def _marginal_cost(rows, arm, tasks, seed, resamples):
    """What the layer costs per attempt on `tasks`: control's mean minus the arm's, so a positive
    figure is what keeping the layer spends, with the arm-over-control Cost-of-Pass ratio."""
    sub = _pair_rows(rows, arm, tasks)
    try:
        result = replay_stats.analyse(sub, seed, resamples, (CONTROL, arm))
    except ValueError as exc:
        return {"usd_per_attempt": None, "ratio": None, "ratio_interval": None, "reason": str(exc)}
    control, removed = (result["arms"][name]["mean_cost_per_attempt"] for name in (CONTROL, arm))
    return {"usd_per_attempt": None if control is None or removed is None else round(control - removed, 6),
            "ratio": result["ratio"], "ratio_interval": result["ratio_interval"], "reason": None}


def justify(rows, manifest, seed=replay_stats.SEED, resamples=replay_stats.RESAMPLES):
    """One verdict per layer from a sweep's rows, by the pre-registered rule. A layer is `keep` when
    removing it worsens one of its `scores` (on its own tasks, or on the outcome subset when it has
    none) or the outcome's pass rate by more than the margin; `trim` when none is worse and each,
    the outcome included, is equivalent within its margin or better beyond it; `no evidence`
    otherwise, an absent or inconclusive interval included. Its marginal cost on its own tasks and
    the outcome subset is reported beside the verdict, never folded into it. A verdict resting on
    fewer than five paired trials per task carries that limitation and reads exploratory."""
    outcome = manifest.get("outcome")
    if not outcome:
        raise ValueError("the manifest names no outcome subset, so no layer can be judged")
    verdicts = []
    for arm in manifest["arms"]:
        own = list(arm.get("tasks") or [])
        scored = own or list(outcome["tasks"])
        scores = [_score(rows, arm["id"], scored, s["metric"], s["bounds"], seed, resamples)
                  for s in arm.get("scores") or []]
        result = _score(rows, arm["id"], outcome["tasks"], OUTCOME_METRIC, outcome["bounds"], seed, resamples)
        judged = scores + [result]
        if any(s["harmful"] for s in judged):
            verdict = KEEP
        elif all(s["assessment"] == equivalence.EQUIVALENT or s["better"] for s in judged):
            verdict = TRIM
        else:
            verdict = NO_EVIDENCE
        limitations = sorted({s["limitation"] for s in judged if s.get("limitation")})
        verdicts.append({"arm": arm["id"], "layer": arm.get("layer"), "entry": entry_of(arm),
                         "verdict": verdict, "scores": scores, "outcome": result,
                         "marginal_cost": _marginal_cost(rows, arm["id"], arm_tasks_of(arm, manifest), seed, resamples),
                         "exploratory": bool(limitations), "limitations": limitations})
    return verdicts


def verdicts_by_entry(verdicts):
    """`{entry: verdict record}`, the shape a scorecard row is keyed by."""
    return {v["entry"]: v for v in verdicts}


def render_verdicts(verdicts):
    lines = []
    for v in verdicts:
        cost = v["marginal_cost"]
        lines.append("%s %s: %s%s; marginal cost %s a attempt" % (
            v["arm"], v["entry"], v["verdict"], " (exploratory)" if v["exploratory"] else "",
            "unavailable" if cost["usd_per_attempt"] is None else "%+.4f USD" % cost["usd_per_attempt"]))
        for s in v["scores"] + [v["outcome"]]:
            lines.append("  %s on %d task(s): %s, %s" % (s["metric"], len(s["tasks"]), s["assessment"], s["reason"]))
    return "\n".join(lines) + "\n"


def pack_limits(repo, ref, tasks, scenarios):
    """`({task: max_turns}, {scenario: cost cap}, {task: declared metrics})` read from the evaluator
    pack at `ref`, by git."""
    def show(path):
        done = subprocess.run(["git", "-C", str(repo), "show", "%s:%s" % (ref, path)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        if done.returncode:
            raise SystemExit("ablations: cannot read %s at %s in %s: %s" % (path, ref, repo, done.stderr.strip()))
        return json.loads(done.stdout)
    documents = {t: show("tasks/%s/task.json" % t) for t in tasks}
    turns = {t: int(d["max_turns"]) for t, d in documents.items()}
    caps = {s: float(show("scenarios/%s/scenario.json" % s)["caps"]["max_cost_usd_hint"]) for s in scenarios}
    return turns, caps, {t: set(d.get("metrics") or {}) for t, d in documents.items()}


def score_errors(plan, outcome_tasks, declared):
    """One line per arm score no task it is scored on declares, so a misnamed metric is caught
    before any spend rather than read as `no evidence` after it."""
    errors = []
    for arm in plan["arms"]:
        scored = arm["own_tasks"] or outcome_tasks
        for score in arm["scores"]:
            if score["metric"] in equivalence.ANALYSED:
                continue
            if not any(score["metric"] in declared.get(t, ()) for t in scored):
                errors.append("%s: no task it is scored on (%s) declares %s"
                              % (arm["id"], ", ".join(scored), score["metric"]))
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ablations.py", description="List and price an ablation sweep, "
                                     "or judge its rows. Nothing is built and no model is called.")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="list every removal arm with its tasks and price it")
    plan.add_argument("--manifest", default=str(Path(__file__).resolve().parent.parent / "benchmarks" / "ablations.json"))
    plan.add_argument("--pack", required=True, help="the evaluator pack repository the manifest's tasks are read from")
    plan.add_argument("--pack-ref", help="the pack ref; default the manifest's pack ref")
    plan.add_argument("--model", action="append", required=True, help="a model to price at; repeat for several")
    plan.add_argument("--reps", type=int, default=1)
    plan.add_argument("--run-cap", type=float, default=RUN_CAP_USD)
    plan.add_argument("--json", action="store_true")
    judge = sub.add_parser("justify", help="one verdict per layer from a sweep's results")
    judge.add_argument("--manifest", required=True)
    judge.add_argument("--results", required=True, nargs="+", help="results.jsonl files of the sweep")
    judge.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    manifest = load(args.manifest)
    if manifest.get("schema") != SCHEMA:
        raise SystemExit("ablations: %s is a pair, not a sweep" % args.manifest)
    if args.command == "justify":
        rows = [json.loads(line) for path in args.results
                for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
        verdicts = justify(rows, manifest)
        sys.stdout.write(json.dumps(verdicts, indent=2, sort_keys=True) + "\n" if args.json else render_verdicts(verdicts))
        return 0
    if "pricing" not in manifest:
        raise SystemExit("ablations: the manifest declares no pricing envelope")
    ref = args.pack_ref or (manifest.get("pack") or {}).get("ref")
    if not ref:
        raise SystemExit("ablations: name --pack-ref, since the manifest names no pack ref")
    planned = sweep_plan(manifest)
    turns, caps, declared = pack_limits(args.pack, ref, planned["control_tasks"], planned["long_session"])
    errors = score_errors(planned, manifest["outcome"]["tasks"], declared)
    if errors:
        raise SystemExit("ablations: refusing the sweep's scores:\n  %s" % "\n  ".join(errors))
    rates = json.loads((Path(__file__).resolve().parent.parent / "policy" / "prices.json").read_text())["models"]
    missing = [m for m in args.model if m not in rates]
    if missing:
        raise SystemExit("ablations: policy/prices.json has no rates for %s" % ", ".join(missing))
    prices = [price_plan(planned, m, rates[m], manifest["pricing"]["turn"], turns, caps, args.run_cap, args.reps)
              for m in args.model]
    if args.json:
        sys.stdout.write(json.dumps({"plan": planned, "prices": prices}, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write("pack %s at %s; per-turn envelope %s (%s)\n" % (
            args.pack, ref, json.dumps(manifest["pricing"]["turn"], sort_keys=True), manifest["pricing"]["source"]))
        sys.stdout.write(render_plan(planned, prices))
    return 0


if __name__ == "__main__":
    sys.exit(main())

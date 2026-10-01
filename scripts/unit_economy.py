#!/usr/bin/env python3
"""The unit-by-economy two-by-two: one rule, skill or hook, the economy concern, and both.

From one base profile four cells are built: `base` (nothing added), `unit` (the unit alone),
`economy` (the economy concern alone) and `both`. The base is derived from the tag's catalog, never
hand-listed: every switch-kind module off but the core hooks, every stance at `off` or its
lightest variant, the economy members at their off values, and the unit's declared dependencies
on. The economy members and their off and on values are declared in `benchmarks/unit-economy.json`.
`bare`, with no harness at all, runs beside them as a fifth arm, outside the factor analysis.

Before any spend, `parity` refuses a grid whose cells differ in anything but the two factors:
each edge of the square must differ in exactly its factor once the tag's own resolver has read
the selections, the diagonal in exactly their union, and every held-constant input (commit, model,
effort, image, Claude Code version) must be one value across the cells. After the run,
`row_parity` repeats the held-constant check on the rows and the loaded-surface check per cell.

`analyse` reports, from saved rows alone, three simple effects and their interaction on three
metrics: the unit alone (`unit` against `base`), the unit with economy on (`both` against
`economy`), economy alone (`economy` against `base`), and the interaction (the ratio of the two
unit ratios for Cost-of-Pass; the difference of the two unit differences for pass rate and rule
adherence). One task-clustered paired bootstrap (#795) computes every figure over the same task
draws. SM-2's decision rule applies only to the primary contrast; every other figure is descriptive.

Standard library only, and no model call. Reading and limits: docs/benchmarks.md, "Unit evals".
"""
import copy
import hashlib
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ablations  # noqa: E402  the per-kind loaded-surface allowance
import replay_pair  # noqa: E402  the loaded-surface comparison
import replay_stats  # noqa: E402  attempts, cells, the bootstrap's rank and SM-2's rule
import rule_adherence  # noqa: E402  the per-run adherence values

SCHEMA = 1
RESULT_SCHEMA = 1
DESIGN = "unit-economy-2x2"
MANIFEST_DESIGN = "unit-economy"
BARE = "bare"
CELLS = ("base", "unit", "economy", "both")
ARM_NAMES = (BARE,) + CELLS
FACTORS = {"base": (False, False), "unit": (True, False), "economy": (False, True), "both": (True, True)}
MANIFEST_KEYS = ("schema", "design", "name", "planning", "economy")
ENTRY = re.compile(r"^([a-z][a-z-]*)[/.]([A-Za-z0-9][A-Za-z0-9._-]*)$")
SWITCH_UNIT_KINDS = ("rules", "skills", "roles", "workflows", "hooks")
# (treatment, reference) per simple effect; the interaction is derived from the first two.
CONTRASTS = (("unit_alone", "unit", "base"), ("unit_with_economy", "both", "economy"),
             ("economy_alone", "economy", "base"))
EFFECTS = tuple(name for name, _, _ in CONTRASTS) + ("interaction",)
METRICS = ("cost_of_pass", "pass_rate", "rule_adherence")
PRIMARY = {"metric": "cost_of_pass", "contrast": "unit_alone"}
# Row fields every row of one grid must share; a second value is a second experiment.
HELD = ("model", "cli_version", "harness_sha", "effort", "schedule_seed", "surface_drift_allowed")
ESTIMAND = "intention to treat"
DESCRIPTIVE = ("every figure but the primary contrast's is descriptive: it carries its interval and "
               "supports no claim, as no correction for the further comparisons is applied")


# --- The manifest and the grid -------------------------------------------------------------------

def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def load(path):
    """The design manifest at `path` with its `sha256`, or SystemExit naming every problem."""
    path = Path(path)
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit("unit-economy: cannot read the design manifest %s: %s" % (path, exc))
    errors = structure_errors(data)
    if errors:
        raise SystemExit("unit-economy: refusing the design manifest %s:\n  %s" % (path, "\n  ".join(errors)))
    return dict(data, sha256=sha256_bytes(raw))


def structure_errors(data):
    """Every way `data` is not a unit-economy design manifest, one line each."""
    if not isinstance(data, dict):
        return ["the manifest is not a JSON object"]
    errors = []
    missing = [k for k in MANIFEST_KEYS if k not in data]
    if missing:
        errors.append("missing %s" % ", ".join(missing))
    extra = sorted(set(data) - set(MANIFEST_KEYS))
    if extra:
        errors.append("unknown key(s) %s" % ", ".join(extra))
    if data.get("schema") != SCHEMA:
        errors.append("schema must be %d" % SCHEMA)
    if data.get("design") != MANIFEST_DESIGN:
        errors.append("design must be %s" % MANIFEST_DESIGN)
    if not isinstance(data.get("name"), str) or not re.match(r"^[a-z0-9][a-z0-9-]*$", data.get("name") or ""):
        errors.append("name must be a lower-case slug")
    planning = data.get("planning")
    if not isinstance(planning, dict) or set(planning) != {"cv", "source"} \
            or type(planning.get("cv")) not in (int, float) or not planning.get("cv") > 0 \
            or not isinstance(planning.get("source"), str) or not planning["source"].strip():
        errors.append("planning must be {cv: the assumed per-attempt coefficient of variation, above 0, "
                      "source: where that assumption comes from}")
    economy = data.get("economy")
    if not isinstance(economy, dict) or not economy:
        errors.append("economy must be a non-empty object of kind/unit to {off, on}")
        return errors
    for entry, values in sorted(economy.items()):
        if not ENTRY.match(entry) or "/" not in entry:
            errors.append("economy member %r is not a kind/unit entry" % entry)
        elif not isinstance(values, dict) or set(values) != {"off", "on"} \
                or not all(isinstance(v, str) and v.strip() for v in values.values()) \
                or values["off"] == values["on"]:
            errors.append("economy member %s must be {off: value, on: another value}" % entry)
    return errors


def unit_entry(text):
    """`kind/unit` from `kind.unit` (the `citizen config set` form) or `kind/unit`, or SystemExit."""
    match = ENTRY.match(text or "")
    if not match:
        raise SystemExit("unit-economy: --unit %r is not <kind>.<id>, such as rules.secrets" % text)
    return "%s/%s" % match.groups()


def members(manifest):
    return tuple(sorted(manifest["economy"]))


def _set(selection, entry, value):
    kind, unit = entry.split("/", 1)
    selection.setdefault(kind, {})[unit] = value


def _lightest(posture, spec, unit, root):
    """`off` when the stance ships one, else its smallest variant by bytes, else None."""
    files = []
    for source in posture.primitive_roots({}, root, spec.get("directory")):
        files += [p for p in (source / unit).glob("*.md") if p.is_file()]
    if any(p.stem == "off" for p in files):
        return "off"
    files.sort(key=lambda p: (p.stat().st_size, p.stem))
    return files[0].stem if files else None


def base_selection(manifest, unit, posture, root, env=None):
    """`(selection, dependencies, errors)`: the base cell in the user-config shape, derived from the
    tag's catalog as the module docstring says, with the unit's dependencies switched on."""
    env = dict(env or {}, HOME=str(Path(root) / ".unit-economy-empty-home"))
    kinds = posture.selection_kinds(root)
    default = posture.selection(env, strict=False, config={}, root=root)
    economy = members(manifest)
    kind = unit.split("/", 1)[0]
    errors = []
    if kind not in SWITCH_UNIT_KINDS:
        errors.append("the unit %s is not a rule, skill, role, workflow or hook" % unit)
    elif unit.split("/", 1)[1] not in (default.get(kind) or {}):
        errors.append("the unit %s is not one the tag ships" % unit)
    elif kind == "hooks" and unit.split("/", 1)[1] in posture.CORE_HOOKS:
        errors.append("the unit %s is a core hook, which is on in every cell" % unit)
    if unit in economy:
        errors.append("the unit %s is a member of the economy concern" % unit)
    for member in economy:
        member_kind, member_unit = member.split("/", 1)
        if member_unit not in (default.get(member_kind) or {}):
            errors.append("the economy member %s is not one the tag ships" % member)
    selection = {}
    for name, spec in kinds.items():
        if spec.get("value") == "switch":
            for item in (default.get(name) or {}):
                core = name == "hooks" and item in posture.CORE_HOOKS
                _set(selection, "%s/%s" % (name, item), "on" if core else "off")
        elif spec.get("value") == "variant" and spec.get("directory"):
            for item in (default.get(name) or {}):
                lightest = _lightest(posture, spec, item, root)
                if lightest:
                    _set(selection, "%s/%s" % (name, item), lightest)
    for member in economy:
        _set(selection, member, manifest["economy"][member]["off"])
    declared = posture.manifests({}, root)[0]
    dependencies = []

    def walk(entry, path):
        kind_of, name_of = entry.split("/", 1)
        for dependency in ((declared.get(kind_of) or {}).get(name_of) or {}).get("dependencies") or []:
            if dependency in economy:
                errors.append("the unit %s depends on %s, a member of the economy concern" % (unit, dependency))
            elif dependency in path:
                errors.append("the unit %s reaches the dependency cycle %s, so it cannot be switched on alone"
                              % (unit, " -> ".join(path[path.index(dependency):] + [dependency])))
            elif dependency not in dependencies:
                dependencies.append(dependency)
                walk(dependency, path + [dependency])

    walk(unit, [unit])
    for dependency in dependencies:
        _set(selection, dependency, "on")
    return selection, dependencies, errors


def cells(base, unit, manifest):
    """`{cell: selection}`: the base, then the unit on, the economy members on, and both."""
    out = {}
    for name, (with_unit, with_economy) in FACTORS.items():
        selection = copy.deepcopy(base)
        if with_unit:
            _set(selection, unit, "on")
        if with_economy:
            for member in members(manifest):
                _set(selection, member, manifest["economy"][member]["on"])
        out[name] = {kind: dict(sorted(units.items())) for kind, units in sorted(selection.items()) if units}
    return out


def selection_sha256(selection):
    return sha256_bytes(json.dumps(selection, sort_keys=True).encode("utf-8"))


def resolve(selections, posture, root, env=None):
    """`({cell: resolved {kind: {unit: value}}}, errors)`: each cell read strictly by the tag's own
    resolver with an empty home, so a dependency, conflict or core-hook refusal names its cell."""
    env = dict(env or {}, HOME=str(Path(root) / ".unit-economy-empty-home"))
    resolved, errors = {}, []
    for name in CELLS:
        try:
            found = posture.selection(env, strict=True, config=selections[name], root=root)
        except ValueError as exc:
            errors.append("cell %s: the tag's resolver refuses its selection: %s"
                          % (name, str(exc).splitlines()[0]))
            continue
        resolved[name] = {k: v for k, v in found.items() if isinstance(v, dict) and k not in ("sources", "shadowed")}
    return resolved, errors


def differences(left, right):
    """Every `kind/unit` whose resolved value differs between two selections."""
    out = set()
    for kind in set(left) | set(right):
        a, b = left.get(kind) or {}, right.get(kind) or {}
        out.update("%s/%s" % (kind, unit) for unit in set(a) | set(b) if a.get(unit) != b.get(unit))
    return out


def parity(resolved, unit, economy, inputs=None):
    """Every reason the grid may not run, one line each; empty when it may.

    `resolved` maps each cell to its resolved selection; `economy` is the members' entries;
    `inputs` maps each cell to the held-constant inputs it was built and launched with. The four
    edges must differ in exactly their factor, the diagonal in exactly both, and every input key
    must take one value across the cells."""
    reasons = []
    economy = set(economy)
    if unit in economy:
        reasons.append("the unit %s is a member of the economy concern, so the factors are not separate" % unit)
    missing = [name for name in CELLS if name not in resolved]
    if missing:
        return reasons + ["cell %s has no resolved selection" % name for name in missing]
    edges = (("base", "unit", {unit}), ("economy", "both", {unit}), ("base", "economy", economy),
             ("unit", "both", economy), ("base", "both", {unit} | economy))
    for low, high, expected in edges:
        found = differences(resolved[low], resolved[high])
        for entry in sorted(found - expected):
            reasons.append("%s and %s also differ in %s, which is neither factor" % (low, high, entry))
        for entry in sorted(expected - found):
            reasons.append("%s and %s do not differ in %s, so the factor does not move it" % (low, high, entry))
    for key, values in sorted(_spread(inputs or {}).items()):
        reasons.append("the cells differ in %s: %s" % (key, ", ".join("%s %r" % kv for kv in values)))
    return reasons


def _spread(by_cell):
    """`{key: [(cell, value), ...]}` for every key whose value is not one across the cells."""
    keys = set()
    for values in by_cell.values():
        keys.update(values)
    out = {}
    for key in keys:
        seen = [(cell, by_cell[cell].get(key)) for cell in CELLS if cell in by_cell]
        if len({json.dumps(v, sort_keys=True) for _, v in seen}) > 1:
            out[key] = seen
    return out


def grid(manifest, unit, posture, root, env=None):
    """Everything the runner needs about one grid, resolved against the tag at `root`: the cells'
    selections, the unit's detector ids, the base digest, and every reason it may not run."""
    unit = unit_entry(unit)
    base, dependencies, errors = base_selection(manifest, unit, posture, root, env)
    selections = cells(base, unit, manifest)
    resolved = {}
    if not errors:
        resolved, errors = resolve(selections, posture, root, env)
    if not errors:
        errors = parity(resolved, unit, members(manifest))
    instruments = rule_adherence.detector_ids(rule_adherence.unit_manifest(root, unit))
    return {"unit": unit, "economy": list(members(manifest)), "dependencies": dependencies,
            "selections": selections, "resolved": resolved, "instruments": list(instruments),
            "base_selection_sha256": selection_sha256(selections["base"]), "errors": errors}


def stripped(declaration):
    """A cell's image declaration less its selection: what every cell's must share."""
    out = {k: v for k, v in (declaration or {}).items() if k != "selection"}
    out["components"] = [c for c in out.get("components") or [] if c.get("name") != "selection"]
    return out


def admit_cells(records, fingerprints):
    """SystemExit naming every reason the built cells may not run; None when they may. Each cell's
    declaration less its selection must equal the others', each must declare a selection, and the
    four must resolve to four different profiles."""
    reasons = []
    flat = {name: stripped(records[name].get("declaration")) for name in CELLS if name in records}
    reasons += ["declaration %s" % line for line in parity_inputs(flat)]
    for name in CELLS:
        if name not in records:
            reasons.append("cell %s was not built" % name)
        elif (records[name].get("declaration") or {}).get("selection") is None:
            reasons.append("cell %s declares no selection" % name)
        if fingerprints.get(name) is None:
            reasons.append("cell %s: its profile fingerprint cannot be resolved" % name)
    known = [fingerprints.get(name) for name in CELLS if fingerprints.get(name) is not None]
    if len(known) == len(CELLS) and len(set(known)) != len(CELLS):
        reasons.append("two cells resolve to one profile, so a factor toggles nothing")
    if reasons:
        raise SystemExit("unit-economy: refusing the grid before any spend:\n  %s" % "\n  ".join(reasons))


def parity_inputs(by_cell):
    return ["%s: %s" % (key, ", ".join("%s %r" % kv for kv in values))
            for key, values in sorted(_spread(by_cell).items())]


def stamp_of(manifest, spec):
    """The `design` record every row of one grid carries."""
    return {"name": DESIGN, "schema": RESULT_SCHEMA, "manifest": manifest["name"],
            "manifest_sha256": manifest["sha256"], "unit": spec["unit"], "instruments": spec["instruments"],
            "economy": spec["economy"], "base_selection_sha256": spec["base_selection_sha256"]}


def row_stamp(design, arm, seed, selections):
    """What a grid's row adds: the design record, the arm's factor levels (None for bare), its
    selection's digest and the schedule seed."""
    with_unit, with_economy = FACTORS.get(arm, (None, None))
    selection = selections.get(arm)
    return {"design": design, "unit": design["unit"], "cell_unit": with_unit, "cell_economy": with_economy,
            "selection_sha256": None if selection is None else selection_sha256(selection),
            "schedule_seed": seed}


def is_design(rows):
    return any(isinstance(r.get("design"), dict) and r["design"].get("name") == DESIGN for r in rows)


def planned_mde(manifest, tasks, reps):
    """The smallest relative change in a per-attempt mean one cell-against-cell contrast can
    resolve at 80% power, from the manifest's declared variance assumption. Planning, not a result."""
    effect = replay_stats.minimum_detectable_effect(tasks * reps, manifest["planning"]["cv"])
    return ("minimum detectable effect, before any spend: %s on a per-attempt mean at %d attempts per cell, "
            "80%% power, 95%% two-sided, for the primary contrast; assumes a coefficient of variation of %g "
            "(%s), a planning assumption, not a measurement"
            % ("undefined" if effect is None else "%.1f%%" % (100 * effect), tasks * reps,
               manifest["planning"]["cv"], manifest["planning"]["source"]))


# --- After the run -------------------------------------------------------------------------------

def surface_allowance(unit, economy):
    """The loaded-surface fields each cell may move against `base`: its factors' entries' own."""
    unit_fields = set(ablations.allowance(unit))
    economy_fields = set()
    for member in economy:
        economy_fields.update(ablations.allowance(member))
    return {"unit": tuple(sorted(unit_fields)), "economy": tuple(sorted(economy_fields)),
            "both": tuple(sorted(unit_fields | economy_fields))}


def row_parity(rows, surface=replay_pair.default_surface):
    """One reason per way the saved rows are not one grid: a held-constant field or the design
    record taking two values, a row whose factor levels contradict its arm, or a cell's loaded
    surface differing from base's beyond its factors' entries."""
    reasons = []
    for key in HELD + ("design",):
        values = sorted({json.dumps(r.get(key), sort_keys=True) for r in rows})
        if len(values) > 1:
            reasons.append("the rows hold %d values of %s: %s" % (len(values), key, ", ".join(values)))
    for index, row in enumerate(rows, 1):
        expected = FACTORS.get(row.get("arm"), (None, None))
        if (row.get("cell_unit"), row.get("cell_economy")) != expected:
            reasons.append("row %d: arm %s carries factor levels %r, %r" % (index, row.get("arm"),
                                                                          row.get("cell_unit"), row.get("cell_economy")))
    design = next((r["design"] for r in rows if isinstance(r.get("design"), dict)), {})
    allowed = surface_allowance(design.get("unit") or "", design.get("economy") or [])
    reasons += replay_pair.surface_parity(rows, surface, control="base", allowed=allowed)
    return reasons


# --- The analysis --------------------------------------------------------------------------------

LO, HI = (0, 0.0), (2, 0.0)  # below and above every defined value; None is indeterminate


def _val(value):
    return None if value is None else (1, value)


def _div(a, b):
    """`a / b` over defined values and the two sentinels; None when indeterminate."""
    if a is None or b is None:
        return None
    (ka, va), (kb, vb) = a, b
    if ka == 1 and kb == 1:
        if vb == 0:
            return None if va == 0 else HI
        return (1, va / vb)
    if ka == kb:
        return None
    return HI if ka == 2 or kb == 0 else LO


def _sub(a, b):
    return None if a is None or b is None else (1, a[1] - b[1])


def _adherence(rows, tasks):
    """`({task: {arm: [compliant, scored, unknown]}}, measured)`; ValueError on a bad value."""
    out = {t: {arm: [0, 0, 0] for arm in ARM_NAMES} for t in tasks}
    seen = set()
    for index, row in enumerate(rows, 1):
        value = row.get("rule_adherence")
        if value not in rule_adherence.VALUES:
            raise ValueError("row %d has no rule_adherence of %s" % (index, ", ".join(rule_adherence.VALUES)))
        seen.add(value == rule_adherence.UNMEASURED)
        cell = out[row["task"]][row["arm"]]
        cell[0] += value == rule_adherence.COMPLIANT
        cell[1] += value in (rule_adherence.COMPLIANT, rule_adherence.HIT)
        cell[2] += value == rule_adherence.UNKNOWN
    if seen == {True, False}:
        raise ValueError("some rows are unmeasured and some are not; one unit is measured one way")
    return out, seen == {False}


def _totals(cells, adherence, picks):
    out = {}
    for arm in ARM_NAMES:
        costs = [cells[t][arm][0] for t in picks]
        out[arm] = {"cost": None if None in costs else sum(costs),
                    "passes": sum(cells[t][arm][1] for t in picks), "n": sum(cells[t][arm][2] for t in picks),
                    "compliant": sum(adherence[t][arm][0] for t in picks),
                    "scored": sum(adherence[t][arm][1] for t in picks),
                    "unknown": sum(adherence[t][arm][2] for t in picks)}
    return out


def _figures(totals, priced, measured):
    """`{metric: {contrast: value}}` and `{arm: ratio to bare}` from one set of totals."""
    cop = {arm: (HI if not t["passes"] else _val(t["cost"] / t["passes"])) for arm, t in totals.items()} \
        if priced else {}
    rate = {arm: _val(t["passes"] / t["n"]) for arm, t in totals.items()}
    adh = {arm: (None if not t["scored"] else _val(t["compliant"] / t["scored"])) for arm, t in totals.items()}
    out = {}
    for metric, values, combine in (("cost_of_pass", cop, _div), ("pass_rate", rate, _sub),
                                    ("rule_adherence", adh, _sub)):
        if (metric == "cost_of_pass" and not priced) or (metric == "rule_adherence" and not measured):
            continue
        found = {name: combine(values[treat], values[ref]) for name, treat, ref in CONTRASTS}
        found["interaction"] = combine(found["unit_with_economy"], found["unit_alone"])
        out[metric] = found
    bare = {cell: _div(cop[cell], cop[BARE]) for cell in CELLS} if priced else {}
    return out, bare


def _interval(samples):
    """The nearest-rank 95% interval of `samples`; an indeterminate sample counts against both
    bounds, so it can only widen the interval, and a bound at a sentinel is undefined (None)."""
    alpha = (1 - replay_stats.CONFIDENCE) / 2
    key = lambda s: (s[0], s[1])  # noqa: E731
    low = sorted((LO if s is None else s for s in samples), key=key)
    high = sorted((HI if s is None else s for s in samples), key=key)
    pick = lambda s: _round(s[1]) if s[0] == 1 else None  # noqa: E731
    return [pick(replay_stats._rank(low, alpha)), pick(replay_stats._rank(high, 1 - alpha))]


def _round(value, places=6):
    return None if value is None else round(value, places)


def _reason(metric, contrast, totals):
    involved = {"unit_alone": ("unit", "base"), "unit_with_economy": ("both", "economy"),
                "economy_alone": ("economy", "base"), "interaction": CELLS}.get(contrast, (contrast, BARE))
    if metric == "cost_of_pass":
        empty = [arm for arm in involved if not totals[arm]["passes"]]
        return "the %s cell(s) passed nothing" % " and ".join(empty) if empty else "indeterminate"
    empty = [arm for arm in involved if not totals[arm]["scored"]]
    return "the %s cell(s) have no scored run" % " and ".join(empty) if empty else "indeterminate"


def _design_of(rows):
    designs = {json.dumps(r.get("design"), sort_keys=True) for r in rows}
    if len(designs) != 1 or not is_design(rows):
        raise ValueError("the rows do not carry one %s design record" % DESIGN)
    return rows[0]["design"]


def analyse(rows, primary=None, seed=replay_stats.SEED, resamples=replay_stats.RESAMPLES):
    """Result schema 1 for one grid's saved rows; ValueError when it cannot be derived.

    `primary` is `{metric: "cost_of_pass", contrast}` as the pre-registration names it; SM-2's rule
    applies to it alone, with the pass-rate difference of the same contrast as its non-inferiority
    test. None means the default, the unit alone on Cost-of-Pass, and the result says so."""
    if type(resamples) is not int or resamples < 2:
        raise ValueError("resamples must be an integer of at least 2")
    design = _design_of(rows)
    primary = dict(primary or PRIMARY, source="pre-registration" if primary else "default")
    if primary.get("metric") != "cost_of_pass" or primary.get("contrast") not in [c for c, _, _ in CONTRASTS]:
        raise ValueError("the primary must be a simple effect on cost_of_pass")
    atts = replay_stats.attempts(rows, ARM_NAMES)
    cells, tasks = replay_stats._cells(atts, ARM_NAMES)
    if not tasks:
        raise ValueError("no rows")
    adherence, measured = _adherence(rows, tasks)
    priced = all(cells[t][arm][0] is not None for t in tasks for arm in ARM_NAMES)
    totals = _totals(cells, adherence, tasks)
    point, point_bare = _figures(totals, priced, measured)
    rng = random.Random(seed)
    samples = {metric: {name: [] for name in EFFECTS} for metric in point}
    bare_samples = {cell: [] for cell in point_bare}
    for _ in range(resamples):
        picks = [tasks[rng.randrange(len(tasks))] for _ in tasks]
        found, found_bare = _figures(_totals(cells, adherence, picks), priced, measured)
        for metric, contrasts in found.items():
            for name, value in contrasts.items():
                samples[metric][name].append(value)
        for cell, value in found_bare.items():
            bare_samples[cell].append(value)

    def effect(metric, name, value, drawn):
        return {"value": _round(value[1]) if value is not None and value[0] == 1 else None,
                "interval": _interval(drawn),
                "undefined_reason": None if value is not None and value[0] == 1 else _reason(metric, name, totals)}

    unpriced = next((arm for arm in ARM_NAMES for t in tasks if cells[t][arm][0] is None), None)
    effects, indeterminate = {}, {}
    for metric in METRICS:
        if metric not in point:
            effects[metric] = rule_adherence.UNMEASURED if metric == "rule_adherence" else {
                name: {"value": None, "interval": None,
                       "undefined_reason": "the %s arm has a run with no readable cost" % unpriced}
                for name in EFFECTS}
            continue
        effects[metric] = {name: effect(metric, name, point[metric][name], samples[metric][name])
                           for name in EFFECTS}
        indeterminate[metric] = {name: sum(1 for s in samples[metric][name] if s is None) for name in EFFECTS}
    summary = {}
    for arm in ARM_NAMES:
        t = totals[arm]
        low, high = replay_stats.wilson(t["passes"], t["n"])
        entry = {"attempts": t["n"], "passes": t["passes"],
                 "errors": sum(1 for a in atts if a["arm"] == arm and a["error"]),
                 "cost_usd": _round(t["cost"]),
                 "cost_of_pass": _round(replay_stats.cost_of_pass(t["cost"], t["passes"])),
                 "pass_rate": round(t["passes"] / t["n"], 4),
                 "pass_rate_interval_descriptive": [round(low, 4), round(high, 4)]}
        if measured:
            a_low, a_high = replay_stats.wilson(t["compliant"], t["scored"]) if t["scored"] else (None, None)
            entry["rule_adherence"] = {"scored": t["scored"], "compliant": t["compliant"], "unknown": t["unknown"],
                                       "rate": round(t["compliant"] / t["scored"], 4) if t["scored"] else None,
                                       "interval_descriptive": [_round(a_low, 4), _round(a_high, 4)]}
        else:
            entry["rule_adherence"] = rule_adherence.UNMEASURED
        if arm in point_bare:
            entry["cost_of_pass_ratio_to_bare"] = effect("cost_of_pass", arm, point_bare[arm], bare_samples[arm])
        summary[arm] = entry
    min_trials = min(cells[t][arm][2] for t in tasks for arm in ARM_NAMES)
    eligible = min_trials >= replay_stats.MIN_TRIALS
    chosen = effects["cost_of_pass"][primary["contrast"]]
    verdict, reason, claim = replay_stats.decide(chosen["value"], chosen["interval"],
                                                 effects["pass_rate"][primary["contrast"]]["interval"])
    limitation = None
    if not eligible:
        limitation = "fewer than five paired trials per task and arm; exploratory diagnostic only"
        verdict, reason, claim = replay_stats.INCONCLUSIVE, limitation, None
    kind, _, ident = design["unit"].partition("/")
    return {"schema": RESULT_SCHEMA, "design": DESIGN,
            "unit": {"kind": kind, "id": ident, "instruments": list(design.get("instruments") or [])},
            "economy": {"members": list(design.get("economy") or [])},
            "base_selection_sha256": design.get("base_selection_sha256"),
            "manifest_sha256": design.get("manifest_sha256"),
            "tasks": len(tasks), "cells": {cell: summary[cell] for cell in CELLS}, "bare": summary[BARE],
            "effects": effects, "primary": primary, "verdict": verdict, "reason": reason, "claim": claim,
            "sm2_eligible": eligible, "limitation": limitation, "descriptive": DESCRIPTIVE,
            "estimand": ESTIMAND, "method": replay_stats.METHOD, "seed": seed, "resamples": resamples,
            "confidence": replay_stats.CONFIDENCE, "delta": replay_stats.DELTA,
            "indeterminate_resamples": indeterminate,
            "schedule_seeds": sorted({r.get("schedule_seed") for r in rows if r.get("schedule_seed") is not None})}


def summarise(rows, primary=None, seed=replay_stats.SEED, resamples=replay_stats.RESAMPLES,
              surface=replay_pair.default_surface):
    """`analyse` plus the post-run parity over the rows."""
    result = analyse(rows, primary, seed, resamples)
    reasons = row_parity(rows, surface)
    return dict(result, parity={"ok": not reasons, "reasons": reasons})


def _num(value, places=3):
    return "undefined" if value is None else "%.*f" % (places, value)


def _span(interval):
    return "[undefined]" if not interval else "[%s]" % ", ".join(_num(v) for v in interval)


def render(result):
    unit = result["unit"]
    lines = ["Unit-by-economy two-by-two: unit %s/%s, economy %s; %d task(s), %s, seed %s, %d resamples"
             % (unit["kind"], unit["id"], ", ".join(result["economy"]["members"]), result["tasks"],
                result["method"], result["seed"], result["resamples"])]
    for arm in CELLS + (BARE,):
        cell = result["cells"].get(arm) or result["bare"]
        adh = cell["rule_adherence"]
        lines.append("  %-8s n %d, pass %.1f%% %s, Cost-of-Pass %s, adherence %s" % (
            arm, cell["attempts"], 100 * cell["pass_rate"], _span(cell["pass_rate_interval_descriptive"]),
            _num(cell["cost_of_pass"], 4),
            adh if isinstance(adh, str) else "%s (%d of %d scored, %d unknown)"
            % (_num(adh["rate"]), adh["compliant"], adh["scored"], adh["unknown"])))
    for metric in METRICS:
        figures = result["effects"][metric]
        if isinstance(figures, str):
            lines.append("%s: %s, no detector instrument for the unit" % (metric, figures))
            continue
        kind = "ratio" if metric == "cost_of_pass" else "difference"
        lines.append("%s (%s):" % (metric, kind))
        for name in EFFECTS:
            figure = figures[name]
            lines.append("  %-18s %s %s%s" % (name, _num(figure["value"]), _span(figure["interval"]),
                                              "" if figure["undefined_reason"] is None
                                              else "  (%s)" % figure["undefined_reason"]))
    primary = result["primary"]
    lines.append("primary (%s): %s on %s, pass-rate non-inferiority at -%g: %s; %s"
                 % (primary["source"], primary["contrast"], primary["metric"], result["delta"],
                    result["verdict"], result["reason"]))
    lines.append(result["descriptive"] + "; estimand: " + result["estimand"])
    parity = result.get("parity")
    if parity is not None:
        lines.append("post-run parity: %s" % ("the cells differed only in their factors" if parity["ok"] else "REFUSED"))
        lines += ["  %s" % reason for reason in parity["reasons"]]
    return "\n".join(lines) + "\n"

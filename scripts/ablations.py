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

Standard library only, and no model call. Reading and limits: docs/benchmarks.md, "Ablation runs".
"""
import hashlib
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_pair  # noqa: E402  the schema-1 pair, its loader and its surface parity
import replay_stats  # noqa: E402  the per-arm comparison and the minimum detectable effect

SCHEMA = 2
PAIR_SCHEMA = replay_pair.SCHEMA
BARE, CONTROL = "bare", replay_stats.CONTROL
RESERVED = (BARE, CONTROL, "control", replay_pair.REFERENCE, replay_pair.TREATMENT)
MANIFEST_KEYS = ("schema", "name", "planning", "arms")
PLANNING_KEYS = ("cv", "source")
ARM_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
ENTRY = re.compile(r"^([a-z][a-z-]*)/([A-Za-z0-9][A-Za-z0-9._-]*)$")
# The loaded-surface fields (`cost_bench.SURFACE_FIELDS`) one entry's removal may move, by kind.
# Anything else that differs from control in a trial is a difference nobody declared.
SURFACE_MOVES = {"skills": ("init_skills", "init_slash_commands"),
                 "workflows": ("init_skills", "init_slash_commands"),
                 "roles": ("init_agents",), "rules": ("init_memory_paths",),
                 "stances": ("init_memory_paths",)}


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
    extra = sorted(set(data) - set(MANIFEST_KEYS))
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
        keys = set(arm) - {"id"}
        if keys not in ({"removes"}, {"sets"}):
            errors.append("%s: give exactly one of removes or sets, and nothing else" % where)
            continue
        entry = entry_of(arm)
        if entry is None:
            errors.append("%s: %s must name one kind/unit entry%s" % (
                where, "removes" if "removes" in arm else "sets",
                "" if "removes" in arm else " with one non-empty variant"))
            continue
        if entry in seen_entries:
            errors.append("%s: %s is toggled by another arm too" % (where, entry))
        seen_entries.add(entry)
    return errors


def entry_of(arm):
    """The `kind/unit` entry an arm toggles, or None when it names none well-formed."""
    if "removes" in arm:
        value = arm.get("removes")
        return value if isinstance(value, str) and ENTRY.match(value) else None
    sets = arm.get("sets")
    if not isinstance(sets, dict) or len(sets) != 1:
        return None
    (entry, variant), = sets.items()
    if not isinstance(entry, str) or not ENTRY.match(entry) or not isinstance(variant, str) or not variant.strip():
        return None
    return entry


def selection(arm):
    """The arm's selection in the user-config shape the sync reads: `{kind: {unit: value}}`."""
    kind, unit = ENTRY.match(entry_of(arm)).groups()
    value = "off" if "removes" in arm else arm["sets"][entry_of(arm)].strip()
    return {kind: {unit: value}}


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
        entry = entry_of(arm)
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
            elif current != "on":
                errors.append("%s: removes %s, which the tag's default selection does not hold switched on"
                              % (arm["id"], entry))
            elif unit not in (default.get(kind) or {}):
                errors.append("%s: removes an unknown id %s" % (arm["id"], entry))
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


def schedule(tasks, reps, names, seed):
    """`[(task, rep, arm)]`: every arm once per task and rep. The leading arm rotates with the rep,
    as `cost_bench.schedule` rotates it, so none always runs on another's warm cache; the arms after
    it follow a permutation drawn from `seed`, so the order is derived and reproducible."""
    rng = random.Random(seed)
    out = []
    for task in tasks:
        for rep in range(1, reps + 1):
            lead = names[(rep - 1) % len(names)]
            rest = [arm for arm in names if arm != lead]
            rng.shuffle(rest)
            out += [(task, rep, arm) for arm in [lead] + rest]
    return out


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
        entry = row.get("ablation_removes") or next(iter(row.get("ablation_sets") or {}), None)
        if entry:
            out[row.get("arm")] = entry
    return out


def allowance(entry):
    """The surface fields the entry's removal may move: its counts and their content hashes."""
    match = ENTRY.match(entry or "")
    fields = SURFACE_MOVES.get(match.group(1) if match else None, ())
    return tuple(fields) + tuple(field + "_sha256" for field in fields)


def surface_parity(rows, surface=replay_pair.default_surface):
    """One reason per trial whose arm loaded a surface that differs from control's in anything but
    the fields its declared entry may move (`allowance`); `replay_pair.surface_parity` decides."""
    removed = removed_of(rows)
    allowed = {arm: allowance(entry) for arm, entry in removed.items()}
    return replay_pair.surface_parity(rows, surface, control=CONTROL, allowed=allowed)


def attribution_problems(rows):
    """One line per row whose removed entry is still in its `context_attribution`."""
    out = []
    for row in rows:
        entry = row.get("ablation_removes")
        modules = (row.get("context_attribution") or {}).get("modules")
        if entry and isinstance(modules, dict) and entry in modules:
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

"""The shared authoring catalog; runtime files are reproducible projections."""
import hashlib
import importlib.util
import json
import re
from pathlib import Path

IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]*$")
# Every kind of primitive: its directory under a primitive root, the files that are its units, the
# value a selection gives it and what sync projects it into. `variant` kinds pick one named file
# per dimension; `switch` kinds are `on` or `off`, default `on`; a kind with no value is not
# selectable. `policy/hooks/posture.py` reads this map to resolve a selection, so a new kind is
# one entry here. `hooks` has no directory: its units are the lifecycle modules `HOOK_IDS` names.
# Every policy module that answers a lifecycle event, by its basename under `policy/hooks/`. Both
# runtimes' adapters dispatch through `lifecycle.py`, so this one list is every runtime's hook ids;
# a module the other `policy/hooks/` files load as a library has no id and no switch.
HOOK_IDS = ("allow-plan-webfetch", "allow-readonly-bash", "approvals", "brief-guard", "filter-output",
            "grade-bash", "harness-session", "intent-overlap", "neutralize-tool-output",
            "stage-user-files", "stop-gate", "tier-agent-spawns", "usage-feed", "usage-log",
            "validate-plan-card", "workspace-session")
# The hooks that enforce rather than assist: `off` only with `core_switches_acknowledged` true.
CORE_HOOKS = ("brief-guard", "grade-bash", "neutralize-tool-output", "stop-gate")
HOOKS_DIRECTORY = "policy/hooks"
KINDS = {
    "rules": {"directory": "rules", "pattern": "*.md", "value": "switch", "projection": "rule link"},
    "stances": {"directory": "stances", "pattern": "*/*.md", "value": "variant",
                "projection": "stance link and generated instructions"},
    "skills": {"directory": "skills", "pattern": "*/SKILL.md", "value": "switch", "projection": "skill link"},
    "roles": {"directory": "roles", "pattern": "*.md", "value": "switch", "projection": "agent definition"},
    "workflows": {"directory": "workflows", "pattern": "*.md", "value": "switch",
                  "projection": "command and generated skill"},
    "hooks": {"directory": None, "pattern": None, "units": HOOK_IDS, "value": "switch",
              "projection": "settings hook"},
    "presentation": {"directory": "presentation", "pattern": "*.md", "value": None, "projection": "output style"},
}
# Capability classes, strongest first. A shared role names the class its work needs; each
# adapter's bindings.json maps the classes it has qualified onto its own native models.
TIER_CLASSES = ("frontier", "strong", "standard", "light")
EFFORTS = ("low", "medium", "high")
# What a `constraints.json` rule may hold. `excludes_roles` is the only condition that reads
# something other than the selection: a role contract's frontmatter, with `allow` naming the
# roles a skill exempts. Authoring contract: docs/primitive-authoring.md.
CONSTRAINT_KEYS = {"when", "requires", "excludes", "excludes_roles", "reason"}
# What an adapter's `roles.<name>` entry may hold, beside the `model` an override may add.
# `tools` is optional: a role that omits it inherits every tool the session has, which is the
# only way a rerouted spawn keeps the MCP tools a `general-purpose` spawn would have had, and
# `disallowed_tools` is then how it gives back the one tool it must not hold.
BINDING_KEYS = {"claude-code": ("tools", "disallowed_tools", "effort"),
                "codex": ("model_reasoning_effort",)}
# The spelling each key takes in the native file; anything absent here is already native.
NATIVE_KEYS = {"disallowed_tools": "disallowedTools"}


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("primitive identifiers use lowercase letters, digits and hyphens")
    return value


def frontmatter(path):
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("missing frontmatter: " + str(path))
    _, header, body = text.split("---", 2)
    fields = {}
    for line in header.strip().splitlines():
        key, sep, value = line.partition(":")
        if not sep or key in fields:
            raise ValueError("invalid or duplicate frontmatter field: " + str(path))
        fields[key] = value.strip()
    return fields, body.lstrip("\n")


def stance_roots(root, config):
    """The built-in stance directory and every configured primitive root's, in load order."""
    roots = [root / "primitives" / "stances"]
    for entry in config.get("primitive_roots", []):
        custom = Path(entry).expanduser()
        if not custom.is_absolute():
            raise ValueError("primitive_roots must be absolute directories")
        roots.append(custom / "stances")
    return roots


def stance_constraints(root, config):
    """Every constraint a configured root ships, as (file, rule) pairs, validated on the way.

    A rule states a nonempty `when` selection, a `reason`, and at least one of `requires`,
    `excludes` and `excludes_roles`; anything else in it is an authoring mistake rather than a
    key a future version might mean, so it is rejected here instead of being ignored.
    """
    rules = []
    for source in stance_roots(root, config):
        path = source.parent / "constraints.json"
        if not path.exists():
            continue
        for rule in json.loads(path.read_text()).get("stances", []):
            unknown = set(rule) - CONSTRAINT_KEYS
            if unknown:
                raise ValueError("unknown stance constraint field(s): " + ", ".join(sorted(unknown)))
            if not isinstance(rule.get("when"), dict) or not rule["when"]:
                raise ValueError("stance constraints require a nonempty when selection")
            if not str(rule.get("reason", "")).strip():
                raise ValueError("a stance constraint states its reason: " + json.dumps(rule["when"]))
            if not any(rule.get(key) for key in ("requires", "excludes", "excludes_roles")):
                raise ValueError("a stance constraint rules something out: " + json.dumps(rule["when"]))
            rules.append((path, rule))
    return rules


def excluded_roles(root, condition):
    """Shipped roles whose frontmatter matches `condition`, minus the ones it allows by name.

    The stance layer chooses variants, but a variant's text can also contradict a role contract:
    `delegation/tiered` refuses the frontier class while two design roles declare it. `allow` is
    how a constraint carries the skill-level exception instead of leaving it in prose only.
    """
    if not condition:
        return []
    allowed = set(condition.get("allow", []))
    fields = {k: v for k, v in condition.items() if k != "allow"}
    if not fields:
        raise ValueError("excludes_roles names at least one frontmatter field")
    hits = []
    for path in sorted((root / "primitives" / "roles").glob("*.md")):
        if path.stem in allowed:
            continue
        header, _ = frontmatter(path)
        if all(header.get(key) == value for key, value in fields.items()):
            hits.append(path.stem)
    return hits


def stance_conflicts(root, config):
    """One message per constraint the selection violates, each ending in the rule's reason.

    A conflict is a finding before it is anything else — `harness stances` and `harness lint`
    print these — and `resolve_stances` is the one caller that turns the first of them into the
    hard error a sync has always raised, so no projection is written from a contradiction.
    """
    selected = config.get("stances", {})
    if not isinstance(selected, dict):
        raise ValueError("stances must be an object")
    findings = []
    for _, rule in stance_constraints(root, config):
        if not all(selected.get(key) == value for key, value in rule["when"].items()):
            continue
        when = ", ".join(k + ": " + v for k, v in sorted(rule["when"].items()))
        for key, value in sorted(rule.get("requires", {}).items()):
            if selected.get(key) != value:
                findings.append(when + " requires " + key + ": " + value + " — " + rule["reason"])
        for key, value in sorted(rule.get("excludes", {}).items()):
            if selected.get(key) == value:
                findings.append(when + " excludes " + key + ": " + value + " — " + rule["reason"])
        for name in excluded_roles(root, rule.get("excludes_roles")):
            findings.append(when + " excludes the role " + name + " — " + rule["reason"])
    return findings


def resolve_stances(root, config, strict=True):
    """Resolve built-in and user-authored choices without accepting path traversal."""
    roots = stance_roots(root, config)
    available = {}
    for index, source in enumerate(roots):
        if not source.is_dir():
            # A custom root may carry rules and skills only — `harness import` writes one — so a
            # root with no `stances/` contributes nothing rather than breaking every sync.
            if index:
                continue
            raise ValueError("missing stance source: " + str(source))
        for dimension in sorted(source.iterdir()):
            if dimension.is_dir():
                identifier(dimension.name)
                variants = available.setdefault(dimension.name, {})
                for path in sorted(dimension.glob("*.md")):
                    identifier(path.stem)
                    if path.stem in variants:
                        raise ValueError("duplicate stance authority: " + dimension.name + "/" + path.stem)
                    variants[path.stem] = path
    selected = config.get("stances", {})
    if not isinstance(selected, dict):
        raise ValueError("stances must be an object")
    result = {}
    for name, variant in selected.items():
        identifier(name)
        identifier(variant)
        if name not in available or variant not in available[name]:
            raise ValueError("stance '" + name + "' has no variant '" + variant +
                             "'; options: " + ", ".join(sorted(available.get(name, {}))))
        result[name] = available[name][variant]
    # A custom dimension is optional until selected; built-in defaults are not.
    defaults = json.loads((root / "config.example.json").read_text())["stances"]
    for name in defaults:
        if name not in result:
            raise ValueError("config has no variant for stance '" + name + "'")
    conflicts = stance_conflicts(root, config)
    if strict and conflicts:
        raise ValueError("stance conflict: " + conflicts[0])
    return result


def catalog(root):
    entries = []
    for kind, entry in KINDS.items():
        if not entry["directory"]:
            continue
        source = root / "primitives" / entry["directory"]
        for path in sorted(source.glob(entry["pattern"])):
            ident = str(path.relative_to(source).with_suffix(""))
            if kind == "skills":
                ident = path.parent.name
            entries.append({"id": ident, "kind": kind, "source": str(path.relative_to(root)),
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    for ident in HOOK_IDS:
        path = root / HOOKS_DIRECTORY / (ident + ".py")
        if path.is_file():
            entries.append({"id": ident, "kind": "hooks", "source": str(path.relative_to(root)),
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                            "core": ident in CORE_HOOKS})
    return {"schema_version": 1, "version": (root / "VERSION").read_text().strip(),
            "primitives": entries}


def role_contract(root, name):
    identifier(name)
    path = root / "primitives" / "roles" / (name + ".md")
    if not path.is_file():
        raise ValueError("unknown harness role: " + name)
    fields, body = frontmatter(path)
    if fields.get("name") != name or fields.get("authority") not in ("read-only", "artifact-write", "workspace-write"):
        raise ValueError("invalid shared role name or authority: " + name)
    if fields.get("context") != "fresh" or fields.get("delegation") != "none":
        raise ValueError("unsupported role context or delegation contract: " + name)
    if fields.get("tier") not in TIER_CLASSES:
        raise ValueError("shared role tier must be one of " + ", ".join(TIER_CLASSES) + ": " + name)
    # `posture: fixed` is the role's refusal of a cost variant's class and effort; its budgets
    # still apply. Absent means the variant decides, which is the default for every other role.
    if fields.get("posture", "fixed") != "fixed":
        raise ValueError("shared role posture, when present, must be 'fixed': " + name)
    role_skills(root, fields)
    return fields, body


def role_skills(root, fields):
    """The skill directories a role declares beyond the ones the shared policy cites, as paths.

    Each name must resolve to a shipped skill, because a typo would quietly remove authority the
    role's body assumes. `all` is the planner's case: its body tells it to read the skills the
    plan will name, and which those are is not known until the brief is read.
    """
    shipped = root / "primitives" / "skills"
    declared = [part.strip() for part in str(fields.get("skills", "")).split(",") if part.strip()]
    if declared == ["all"]:
        return sorted(p for p in shipped.iterdir() if (p / "SKILL.md").is_file())
    paths = []
    for skill in declared:
        path = shipped / identifier(skill)
        if not (path / "SKILL.md").is_file():
            raise ValueError("role declares an unknown skill: " + skill)
        paths.append(path)
    return paths


def native_model(tiers, tier):
    """The adapter's model for a class, or the nearest stronger class it maps; None if neither.

    An unmapped class never resolves downward: a weaker model than the role asked for is a
    silent failure, while None makes the caller inherit the session model and say so.
    """
    for name in reversed(TIER_CLASSES[:TIER_CLASSES.index(tier) + 1]):
        if name in tiers:
            return tiers[name]
    return None


def adapter_tiers(root, runtime, tiers=None):
    """The adapter's class table with a user's `tiers.<runtime>` entries laid over it."""
    data = json.loads((root / "adapters" / runtime / "bindings.json").read_text())
    merged = dict(data.get("tiers", {}), **(tiers or {}))
    if set(merged) - set(TIER_CLASSES) or not all(
            isinstance(v, str) and v.strip() and not v.startswith("-") and not any(c.isspace() for c in v)
            for v in merged.values()):
        raise ValueError("adapter tiers map " + ", ".join(TIER_CLASSES) + " to native model identifiers")
    return data, merged


def tier_findings(tiers, models):
    """What a provider's model catalog says is wrong with a class table, as (class, model, problem).

    `models` is the provider's own list: `slug`, `priority` (lower is stronger) and `upgrade`, the
    successor it names once a model is superseded. A versioned id keeps resolving after its
    successor ships, so without this the table goes stale silently.
    """
    known = {m.get("slug"): m for m in models if isinstance(m, dict)}
    findings, last = [], None
    for name in TIER_CLASSES:
        model = tiers.get(name)
        if model is None:
            continue
        entry = known.get(model)
        if entry is None:
            findings.append((name, model, "not in the provider's catalog"))
            continue
        successor = entry.get("upgrade")
        successor = successor.get("model") or successor.get("slug") if isinstance(successor, dict) else successor
        if successor:
            findings.append((name, model, "superseded by " + str(successor)))
        priority = entry.get("priority")
        if isinstance(priority, int):
            if last is not None and priority < last:
                findings.append((name, model, "the catalog ranks it above the class before it"))
            last = priority
    return findings


def role_overrides(root, runtime, row=None, class_applies=True, tiers=None, binding=None):
    """One role's binding overrides: the active cost row, then the user's explicit binding.

    Precedence, lowest first: the role's own `tier` and the adapter's effort, which are not
    overrides at all and reach `role_binding` by themselves; then the resolved cost row; then
    `role_bindings.<runtime>.<role>`, which always wins because the user named it. A row carries
    a capability class and never a model, so the adapter's table stays the only place a native
    model is written; a `posture: fixed` role arrives with no class and no effort, because the
    resolver has already stripped them.
    """
    effort_key = "model_reasoning_effort" if runtime == "codex" else "effort"
    out = {}
    if row:
        if class_applies and row.get("class") in TIER_CLASSES:
            model = native_model(adapter_tiers(root, runtime, tiers)[1], row["class"])
            if model:
                out["model"] = model
        if row.get("effort") in EFFORTS:
            out[effort_key] = row["effort"]
    out.update(binding or {})
    return out


_POSTURE_MODULES = {}


def posture_module(root):
    """The stance and cost resolver the policy hooks run, loaded by file, or None when missing.

    Loading the same file the hooks load is what keeps one answer to "what does this variant
    say": a sync, a lint and an isolated role worker read the resolver, never a second copy of
    it. Loaded once per root, because a sync asks it a question per role per runtime.
    """
    key = str(root)
    if key in _POSTURE_MODULES:
        return _POSTURE_MODULES[key]
    # One file under two names: `claude/hooks` is a symlink to `policy/hooks`. The second name is
    # how a tree that carries only the projected side still resolves its own variants.
    path = next((p for p in (Path(root) / "policy" / "hooks" / "posture.py",
                             Path(root) / "claude" / "hooks" / "posture.py") if p.is_file()), None)
    if path is None:
        return None
    try:
        spec = importlib.util.spec_from_file_location("harness_posture", str(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception:
        return None
    _POSTURE_MODULES[key] = module
    return module


def cost_table(root, config):
    """The configured cost variant's resolved table; never a reason for the work in hand to fail.

    Built from the configuration the caller resolved, so the whole stance ladder it already
    walked — user file, project file, session variables — reaches the rows. A resolver this
    checkout does not carry, or a sidecar it cannot use, yields an empty table: everything then
    resolves exactly as it did before cost variants had rows.
    """
    module = posture_module(root)
    if module is None:
        return {"rows": {}, "class_applies": False,
                "warnings": ["no posture resolver in this checkout; rendering without cost rows"]}
    try:
        return module.table_for(dict(config.get("stances", {})), config, strict=False, root=root)
    except Exception as exc:
        return {"rows": {}, "class_applies": False,
                "warnings": ["cost table unusable, rendering without cost rows: " + str(exc)]}


def cost_row(root, table, role):
    """The cost row that governs one role, band rows included; the resolver decides which.

    One lookup for every caller, so a band worker is priced from the row the spawn hook reroutes
    to. A checkout with no resolver falls back to the role's own row.
    """
    module = posture_module(root)
    if module is None or not hasattr(module, "row_for"):
        return (table.get("rows") or {}).get(role)
    return module.row_for(table, role)


def cost_overrides(root, config, table, runtime, role):
    """The overrides one role is bound with under `table`, for either runtime.

    The sync path renders a native agent definition with these, and `workers.resolve` binds an
    isolated worker with them, so a role cannot run on one class as a definition and another as
    a worker.
    """
    return role_overrides(root, runtime, cost_row(root, table, role),
                          class_applies=bool(table.get("class_applies")),
                          tiers=config.get("tiers", {}).get(runtime),
                          binding=config.get("role_bindings", {}).get(runtime, {}).get(role, {}))


def role_binding(root, runtime, fields, overrides=None, tiers=None):
    """A role's native binding: the adapter's entry, its class resolved to a model, then overrides."""
    data, tiers = adapter_tiers(root, runtime, tiers)
    effort_key = "model_reasoning_effort" if runtime == "codex" else "effort"
    if set(overrides or {}) - {"model", effort_key}:
        raise ValueError("role bindings may change model and effort only")
    binding = dict(data["roles"][fields["name"]], **(overrides or {}))
    unknown = set(binding) - set(BINDING_KEYS.get(runtime, ())) - {"model"}
    if unknown or not all(isinstance(v, str) and v.strip() for v in binding.values()):
        raise ValueError("an adapter role entry holds " + ", ".join(BINDING_KEYS.get(runtime, ()))
                         + " as non-empty strings: " + fields["name"])
    if binding.get(effort_key, EFFORTS[0]) not in EFFORTS:
        raise ValueError("role effort must be one of " + ", ".join(EFFORTS) + ": " + fields["name"])
    model = binding.pop("model", None) or native_model(tiers, fields["tier"])
    # An `inherit` override is the way back to the session model, for a provider without these ids.
    binding = {NATIVE_KEYS.get(key, key): value for key, value in binding.items()}
    return dict({"model": model} if model and model != "inherit" else {}, **binding)


def role_projection(root, runtime, path, overrides=None, tiers=None):
    fields, body = role_contract(root, path.stem)
    binding = role_binding(root, runtime, fields, overrides, tiers)
    if runtime == "claude-code":
        values = {k: fields[k] for k in ("name", "description")}
        values.update(dict({"model": "inherit"}, **binding))
        return "---\n" + "".join(k + ": " + v + "\n" for k, v in values.items()) + "---\n\n" + body
    if runtime != "codex":
        raise ValueError("unsupported runtime: " + runtime)
    values = {k: fields[k] for k in ("name", "description")}
    values["developer_instructions"] = body
    values["sandbox_mode"] = "workspace-write" if fields["authority"] == "workspace-write" else "read-only"
    values.update(binding)
    # JSON strings/arrays are valid for this restricted TOML value set.
    return "# Generated from primitives/roles; edit the shared source.\n" + "".join(
        k + " = " + json.dumps(v, ensure_ascii=False) + "\n" for k, v in values.items())


def projections(root):
    files = {}
    for role in sorted((root / "primitives" / "roles").glob("*.md")):
        files["claude/agents/" + role.name] = role_projection(root, "claude-code", role)
    for workflow in sorted((root / "primitives" / "workflows").glob("*.md")):
        files["claude/commands/" + workflow.name] = workflow.read_text().replace("{{arguments}}", "$ARGUMENTS")
    files["claude/CLAUDE.md"] = (root / "primitives" / "instructions.md").read_text() + "\n@~/.claude/CLAUDE.personal.md\n"
    files["claude/CLAUDE.personal.template.md"] = (root / "primitives" / "personal.template.md").read_text()
    from . import compatibility
    for name in ("README.md", "docs/compatibility.md"):
        path = root / name
        if not path.exists() or "<!-- harness:compatibility:start -->" not in path.read_text():
            continue
        data = compatibility.catalog(root)
        lines = []
        for status in ("qualified", "unqualified", "planned", "unsupported"):
            clients = [row["id"] for row in data["clients"] if row["status"] == status]
            if clients:
                lines.append("**" + status.capitalize() + ":** " + ", ".join("`" + name + "`" for name in clients) + ".")
        lines.extend(compatibility_capability_table(root, data))
        block = "<!-- harness:compatibility:start -->\n" + "\n\n".join(lines) + "\n<!-- harness:compatibility:end -->"
        files[name] = re.sub(r"<!-- harness:compatibility:start -->.*?<!-- harness:compatibility:end -->", block, path.read_text(), flags=re.S)
    return files


def compatibility_capability_table(root, data):
    """The capability-by-client rows rendered beside the client statuses.

    A capability's state is derived at generation time from the runtime's adapter, never written
    here or in the catalog by hand. The layout follows the generated capability matrix in
    wshobson/agents' `docs/harnesses.md`.
    """
    from . import compatibility
    states = {row["id"]: compatibility.capability_states(root, data, row) for row in data["clients"]}
    columns = [row["id"] for row in data["clients"] if states[row["id"]]]
    names = sorted({name for row in columns for name in states[row]})
    if not columns or not names:
        return []
    rows = ["| Capability | " + " | ".join("`" + name + "`" for name in columns) + " |",
            "|---|" + "---|" * len(columns)]
    for name in names:
        rows.append("| `" + name + "` | "
                    + " | ".join(states[client][name]["state"] for client in columns) + " |")
    restrictions = {row["id"]: compatibility.tier_restriction(root, row) for row in data["clients"]}
    rows.append("| tier restriction | "
                + " | ".join(restrictions[client]["state"] for client in columns) + " |")
    carried = {}
    for client in columns:
        if restrictions[client]["mechanism"]:
            carried.setdefault(restrictions[client]["state"], set()).add(restrictions[client]["mechanism"])
    mechanisms = [state + " by " + ", ".join("`" + name + "`" for name in sorted(carried[state]))
                  for state in sorted(carried)]
    note = ("The last row is not a qualification state. It says whether the delegation stance's "
            "model-tier ceiling is **enforced** (a hook rewrites or refuses the spawn), "
            "**advisory** (prompt text only) or **none**"
            + (", carried " + "; ".join(mechanisms) if mechanisms else "")
            + ". `enforced` is narrower than it sounds. It never reaches the session's own model: "
            "the `model` settings key is one this harness never writes "
            "(`docs/settings-ownership.md`). Within a session it rewrites a spawn only while the "
            "selected `delegation` variant is `tiered`. `off` stops the spawn instead, any other "
            "variant leaves it alone, and it acts only while the adapter's class table maps at "
            "least two models, since one class is no ladder to move a spawn down. Under every "
            "other condition the ceiling is prose, exactly as `advisory` is everywhere.")
    return ["A client's status is not a capability's status. Each cell is derived from that "
            "runtime's `adapters/<runtime>/capabilities.json` at generation time:", "\n".join(rows),
            note]


def projection_drift(root):
    return [name for name, content in projections(root).items()
            if not (root / name).is_file() or (root / name).read_text() != content]

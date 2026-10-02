"""Compatibility claims must carry versioned native evidence."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

STATES = {"qualified", "unqualified", "planned", "unsupported"}
TIER_RESTRICTIONS = {"enforced", "advisory", "none"}
SOURCE_PATHS = ("VERSION", "bin", "lib", "adapters", "primitives", "policy", "templates",
                "config.example.json")
FREEZE_STATES = {"open", "frozen"}
SCOPE_VERSION = 1
REUSE_KINDS = {"carry-forward", "v0.14.1-bootstrap"}
REUSE_FIELDS = {"schema_version", "kind", "prior_version", "prior_tag", "evidence_version",
                "qualification_source_commit", "includes_bootstrap_exception", "limitation"}
BOOTSTRAP_BEHAVIOR_PATHS = {
    "lib/harness_core/compatibility.py",
    "scripts/release_notes.py",
    "scripts/release_preflight.py",
    "tests/test_compatibility.py",
    "tests/test_release.py",
    "tests/test_release_upkeep.py",
}
PRESENTATION_PATHS = {"VERSION", "README.md", "CHANGELOG.md", "product.json",
                      "compatibility/catalog.json", "compatibility/freeze.json",
                      "compatibility/migration.json"}
PRESENTATION_PREFIXES = ("_bmad-output/", "changelog.d/", "docs/")
MAX_REUSE_DEPTH = 32
SEMVER = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
CATALOG_FIELDS = {"schema_version", "harness_version", "release_state", "required_cases",
                  "evidence_invalidation", "clients", "limitations",
                  "qualification_source_commit", "qualification_reuse"}


def canonical_reuse_limitation(kind, current, prior_tag, evidence, includes_bootstrap):
    """The exact public limitation admitted for one reuse kind and provenance chain."""
    if kind == "v0.14.1-bootstrap":
        return ("Native evidence for v0.14.0 was carried into v0.14.1 under the one-release "
                "v0.14.1 bootstrap exception; no native client was rerun.")
    through = " through " + prior_tag
    if includes_bootstrap:
        through += " including the one-release v0.14.1 bootstrap exception"
    return ("Native evidence for v" + evidence + " was carried forward" + through
            + "; no native client was rerun for v" + current + ".")


def string_list(value):
    return isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)


def same_json(left, right):
    """Type-sensitive JSON equality; unlike Python equality, true never equals 1."""
    return (type(left) is type(right)
            and json.dumps(left, sort_keys=True, separators=(",", ":"))
            == json.dumps(right, sort_keys=True, separators=(",", ":")))


def reuse_metadata(data):
    """Return structurally valid evidence-reuse metadata, or None when none is declared."""
    declared = data.get("qualification_reuse")
    if declared is None:
        return None
    if not isinstance(declared, dict):
        raise ValueError("qualification reuse must be an object")
    unknown = sorted(set(declared) - REUSE_FIELDS)
    missing = sorted(REUSE_FIELDS - set(declared))
    if unknown:
        raise ValueError("qualification reuse has unknown field: " + unknown[0])
    if missing:
        raise ValueError("qualification reuse requires " + missing[0])
    if type(declared["schema_version"]) is not int or declared["schema_version"] != 1:
        raise ValueError("qualification reuse requires schema_version 1")
    for key in ("kind", "prior_version", "prior_tag", "evidence_version",
                "qualification_source_commit", "limitation"):
        if not isinstance(declared[key], str) or not declared[key].strip():
            raise ValueError("qualification reuse requires " + key)
    if not isinstance(declared["includes_bootstrap_exception"], bool):
        raise ValueError("qualification reuse includes_bootstrap_exception must be true or false")
    if declared["kind"] not in REUSE_KINDS:
        raise ValueError("qualification reuse kind must be one of: "
                         + ", ".join(sorted(REUSE_KINDS)))
    if not SEMVER.fullmatch(declared["prior_version"]):
        raise ValueError("qualification reuse prior_version must be a stable semantic version")
    if not SEMVER.fullmatch(declared["evidence_version"]):
        raise ValueError("qualification reuse evidence_version must be a stable semantic version")
    if declared["prior_tag"] != "v" + declared["prior_version"]:
        raise ValueError("qualification reuse prior_tag must match prior_version")
    if not re.fullmatch(r"[0-9a-f]{40,64}", declared["qualification_source_commit"]):
        raise ValueError("qualification reuse requires a full qualification_source_commit")
    current = data.get("harness_version", "")
    expected = canonical_reuse_limitation(
        declared["kind"], current, declared["prior_tag"], declared["evidence_version"],
        declared["includes_bootstrap_exception"])
    if declared["limitation"] != expected:
        raise ValueError("qualification reuse limitation must equal the canonical "
                         + declared["kind"] + " disclosure")
    return declared


def evidence_version(data):
    """The harness version an evidence record truthfully observed."""
    reuse = reuse_metadata(data)
    return reuse["evidence_version"] if reuse else data["harness_version"]


def qualification_disclosure(data):
    """Describe whether the release has new evidence, carried evidence, or the bootstrap exception."""
    reuse = reuse_metadata(data)
    if not reuse:
        return "native evidence recorded for this release"
    return reuse["limitation"]


def resolve_tag(root, tag, required=True):
    """Resolve one exact tag ref to its commit, never accepting a branch of the same name."""
    done = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify",
                           "refs/tags/" + tag + "^{commit}"], capture_output=True, text=True)
    if done.returncode:
        if required:
            raise ValueError("qualification reuse release tag is unavailable: " + tag)
        return None
    commit = done.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise ValueError("qualification reuse release tag did not resolve to a full commit: " + tag)
    return commit


def release_target(root, data):
    """Use the immutable current tag after release; candidates validate their clean HEAD."""
    version = data.get("harness_version")
    return resolve_tag(root, "v" + version, required=False) if isinstance(version, str) else None


def allowed_release_path(path, bootstrap):
    if path in PRESENTATION_PATHS or any(path.startswith(prefix) for prefix in PRESENTATION_PREFIXES):
        return True
    return bootstrap and path in BOOTSTRAP_BEHAVIOR_PATHS


def qualification_reuse(root, data, target=None, seen=None, depth=0):
    """Validate a patch release's explicit reuse of an immutable prior release's evidence."""
    reuse = reuse_metadata(data)
    if reuse is None:
        if (target or release_target(root, data) or "HEAD") == "HEAD":
            staged = subprocess.run(["git", "-C", str(root), "show", ":compatibility/catalog.json"],
                                    capture_output=True)
            # Open candidates may be edited before staging; released HEAD claims must match the index.
            required = data.get("release_state") == "released"
            if staged.returncode and required:
                raise ValueError("staged compatibility catalog is unavailable")
            if staged.returncode == 0:
                staged_catalog = json.loads(staged.stdout)
                if not isinstance(staged_catalog, dict):
                    raise ValueError("staged compatibility catalog must be an object")
                if (required or staged_catalog.get("qualification_reuse") is not None) and not same_json(data, staged_catalog):
                    raise ValueError("compatibility catalog differs from the staged catalog; stage the validated catalog")
        return None
    if depth >= MAX_REUSE_DEPTH:
        raise ValueError("qualification reuse chain exceeds " + str(MAX_REUSE_DEPTH) + " releases")
    if data.get("release_state") != "released":
        raise ValueError("qualification reuse requires a released catalog")
    current = data.get("harness_version", "")
    if not SEMVER.fullmatch(current):
        raise ValueError("qualification reuse requires a stable semantic version")
    prior_parts = tuple(map(int, reuse["prior_version"].split(".")))
    current_parts = tuple(map(int, current.split(".")))
    if current_parts[:2] != prior_parts[:2] or current_parts[2] <= prior_parts[2]:
        raise ValueError("qualification reuse requires a later patch in the same release line")
    if reuse["kind"] == "v0.14.1-bootstrap" and \
            (reuse["prior_version"], current) != ("0.14.0", "0.14.1"):
        raise ValueError("the bootstrap qualification exception applies only to v0.14.1")
    if reuse["kind"] == "v0.14.1-bootstrap" and not reuse["includes_bootstrap_exception"]:
        raise ValueError("the bootstrap qualification exception must be disclosed in its chain")
    if data.get("qualification_source_commit") != reuse["qualification_source_commit"]:
        raise ValueError("qualification reuse qualification source differs from the catalog")
    prior_commit = resolve_tag(root, reuse["prior_tag"])
    target_commit = target or release_target(root, data) or "HEAD"
    ancestor = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor",
                               prior_commit, target_commit], capture_output=True)
    if ancestor.returncode:
        raise ValueError("qualification reuse prior release tag is not an ancestor of "
                         + target_commit)
    try:
        prior = json.loads(git_output(root, "show", prior_commit + ":compatibility/catalog.json"))
    except (ValueError, json.JSONDecodeError) as error:
        raise ValueError("qualification reuse prior catalog is unavailable: " + str(error)) from error
    if not isinstance(prior, dict):
        raise ValueError("qualification reuse prior catalog must be an object")
    if prior.get("release_state") != "released" or \
            prior.get("harness_version") != reuse["prior_version"]:
        raise ValueError("qualification reuse prior catalog is not the named released version")
    if git_output(root, "show", prior_commit + ":VERSION").strip() != reuse["prior_version"]:
        raise ValueError("qualification reuse prior tag VERSION does not match prior_version")
    if prior.get("qualification_source_commit") != reuse["qualification_source_commit"]:
        raise ValueError("qualification reuse qualification source differs from the prior release")
    chain = set() if seen is None else set(seen)
    if prior_commit in chain:
        raise ValueError("qualification reuse chain contains a repeated prior tag")
    chain.add(prior_commit)
    qualification_reuse(root, prior, target=prior_commit, seen=chain, depth=depth + 1)
    if reuse["evidence_version"] != evidence_version(prior):
        raise ValueError("qualification reuse evidence version differs from the prior release")
    prior_reuse = reuse_metadata(prior)
    prior_bootstrap = (prior_reuse["includes_bootstrap_exception"] if prior_reuse else False)
    if reuse["includes_bootstrap_exception"] != (reuse["kind"] == "v0.14.1-bootstrap"
                                                   or prior_bootstrap):
        raise ValueError("qualification reuse bootstrap provenance differs from the prior release")
    if not same_json(data.get("required_cases"), prior.get("required_cases")):
        raise ValueError("qualification reuse required cases differ from the prior release")
    if not same_json(data.get("evidence_invalidation"), prior.get("evidence_invalidation")):
        raise ValueError("qualification reuse invalidation claims differ from the prior release")
    if not same_json(data.get("clients"), prior.get("clients")):
        raise ValueError("qualification reuse client and evidence claims differ from the prior release")
    if not string_list(prior.get("limitations")) or not string_list(data.get("limitations")):
        raise ValueError("qualification reuse limitations must be lists of nonempty strings")
    expected_limitations = prior["limitations"] + [reuse["limitation"]]
    if not same_json(data["limitations"], expected_limitations):
        raise ValueError("qualification reuse limitations differ from the prior release plus disclosure")
    unknown = sorted((set(data) | set(prior)) - CATALOG_FIELDS)
    if unknown:
        raise ValueError("qualification reuse catalog has unknown field: " + unknown[0])
    allowed_differences = {"harness_version", "qualification_reuse", "limitations"}
    for key in sorted(CATALOG_FIELDS - allowed_differences):
        if not same_json(data.get(key), prior.get(key)):
            raise ValueError("qualification reuse catalog field differs from prior release: " + key)
    changed = changed_files(root, prior_commit, target_commit, ["."], [])
    if changed is None:
        raise ValueError("qualification reuse source delta is unavailable")
    refused = [path for path in changed
               if not allowed_release_path(path, reuse["kind"] == "v0.14.1-bootstrap")]
    if refused:
        raise ValueError("qualification reuse release delta contains ineligible path: " + refused[0])
    if "VERSION" not in changed:
        raise ValueError("qualification reuse release delta must change VERSION")
    if target_commit == "HEAD":
        staged_catalog = json.loads(git_output(root, "show", ":compatibility/catalog.json"))
        if not same_json(data, staged_catalog):
            raise ValueError("qualification reuse catalog differs from the staged catalog; stage the validated catalog")
        if git_output(root, "show", ":VERSION").strip() != current:
            raise ValueError("qualification reuse staged VERSION does not match the catalog")
    return reuse


def qualification_source(data):
    """Return the source identity a catalog's evidence qualifies."""
    if data.get("release_state") != "released":
        return "HEAD"
    reuse = reuse_metadata(data)
    commit = reuse["qualification_source_commit"] if reuse else data.get("qualification_source_commit")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise ValueError("released compatibility catalog requires a full qualification source commit")
    return commit


def source_drift(root, data):
    """A released claim stays readable, but changed source needs new qualification."""
    if reuse_metadata(data):
        target = release_target(root, data) or "HEAD"
        qualification_reuse(root, data, target=target)
        if target == "HEAD":
            return False
        ancestry = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor",
                                   target, "HEAD"], capture_output=True)
        unchanged = subprocess.run(["git", "-C", str(root), "diff", "--quiet", target,
                                    "HEAD", "--", *SOURCE_PATHS], capture_output=True)
        return bool(ancestry.returncode or unchanged.returncode)
    target = qualification_source(data)
    if target == "HEAD":
        return False
    ancestry = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", target, "HEAD"],
                              capture_output=True)
    unchanged = subprocess.run(["git", "-C", str(root), "diff", "--quiet", target, "HEAD", "--",
                                *SOURCE_PATHS], capture_output=True)
    return bool(ancestry.returncode or unchanged.returncode)


def git_output(root, *args):
    """Run a read-only git command in `root`, refusing to guess when it fails."""
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if done.returncode:
        detail = (done.stderr.strip().splitlines() or [""])[0]
        raise ValueError("git " + args[0] + " failed: " + detail)
    return done.stdout


def freeze_record(root):
    """Read the release-branch freeze record; a missing file means no branch is frozen.

    A qualification round runs on the `release/vX.Y.Z` branch cut at this commit, so merges
    into `main` cannot invalidate the round's evidence. See docs/releasing.md.
    """
    path = root / "compatibility" / "freeze.json"
    if not path.is_file():
        return {"schema_version": 1, "state": "open"}
    data = json.loads(path.read_text())
    if data.get("schema_version") != 1:
        raise ValueError("unsupported freeze schema")
    if data.get("state") not in FREEZE_STATES:
        raise ValueError("freeze state must be one of: " + ", ".join(sorted(FREEZE_STATES)))
    if data["state"] == "frozen":
        if not isinstance(data.get("branch"), str) or not data["branch"].strip():
            raise ValueError("a frozen release branch requires its branch name")
        commit = data.get("commit")
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40,64}", commit):
            raise ValueError("a frozen release branch requires a full commit identity")
    return data


def stale_freeze_errors(freeze, data, tagged):
    """Refuse a freeze record still naming the branch of a release that is already tagged.

    docs/releasing.md returns the record to `open` after the tag, so the release commit itself
    may still be frozen; `tagged` says whether `v<harness_version>` exists. Reads no network.
    """
    if freeze.get("state") != "frozen" or data.get("release_state") != "released" or not tagged:
        return []
    branch = "release/v" + str(data.get("harness_version"))
    if freeze.get("branch") != branch:
        return []
    return ["compatibility/freeze.json is still frozen at " + branch + ", which is released;"
            " and tagged; return its state to open"]


def freeze_drift(root, data, ref="origin/main"):
    """Report runtime source drift between the frozen commit and `ref`.

    This is the check a release handoff otherwise records by hand; an empty `paths` means the
    round's evidence still describes `ref` as well as it describes the frozen commit.
    """
    result = {"state": data.get("state", "open"), "branch": data.get("branch"),
              "commit": data.get("commit"), "ref": ref, "paths": [], "stat": ""}
    if result["state"] != "frozen":
        return result
    numstat = git_output(root, "diff", "--numstat", "--no-renames", result["commit"], ref,
                         "--", *SOURCE_PATHS)
    result["paths"] = sorted({line.split("\t")[-1] for line in numstat.splitlines() if line.strip()})
    result["stat"] = git_output(root, "diff", "--stat", "--no-renames", result["commit"], ref,
                                "--", *SOURCE_PATHS).strip()
    return result


def merge_refusal(root, data, ref):
    """Refuse a merge into a frozen release branch that changes the qualified runtime source."""
    if data.get("state") != "frozen":
        return []
    result = freeze_drift(root, data, ref)
    if not result["paths"]:
        return []
    return [data["branch"] + " is frozen at " + data["commit"][:12] + "; " + ref
            + " changes qualified runtime source: " + ", ".join(result["paths"])]


def invalidation_declaration(data):
    """The catalog's validated per-target invalidation scope, or `{}` when none is declared.

    Three claims are declared rather than inferred, because a reviewer has to be able to read
    them: which directory each runtime owns, which files inside such a directory shared code
    reads for any runtime, and which files are loaded only for the runtime whose session is
    running. The first two are enforced by tests/test_adapter_directory_isolation.py; the third
    is a maintainer's claim about call sites, and docs/compatibility.md says so.
    """
    declared = data.get("evidence_invalidation")
    if not declared:
        return {}
    if not isinstance(declared, dict) or declared.get("version") != SCOPE_VERSION:
        raise ValueError("unsupported evidence invalidation scope version")
    scopes, known = declared.get("runtime_paths"), {row.get("runtime") for row in data.get("clients") or []}
    if not isinstance(scopes, dict) or len(scopes) < 2:
        raise ValueError("evidence invalidation scope requires two or more runtime paths")
    for runtime in sorted(scopes, key=str):
        if not isinstance(runtime, str) or runtime not in known:
            raise ValueError("evidence invalidation scope names a runtime no client runs: "
                             + str(runtime))
        if scopes[runtime] != "adapters/" + runtime:
            raise ValueError("evidence invalidation scope must name each runtime's own adapter "
                             "directory: " + runtime)
    shared, private = declared.get("shared_files"), declared.get("runtime_files")
    for names, label in ((shared, "shared_files"), (private, "runtime_files")):
        if not isinstance(names, list) or not all(isinstance(name, str) and name and "/" not in name
                                                  and name not in (".", "..") for name in names):
            raise ValueError("evidence invalidation " + label + " must be plain file names")
    if not shared:
        raise ValueError("evidence invalidation scope requires the shared file names, because a "
                         "narrowed scope that names none fails open")
    if set(shared) & set(private):
        raise ValueError("an adapter file is either shared or per-runtime, never both")
    return {"runtime_paths": dict(scopes), "shared_files": sorted(shared),
            "runtime_files": sorted(private)}


def runtime_scopes(data):
    """The adapter directory each runtime owns, as the catalog declares it."""
    return invalidation_declaration(data).get("runtime_paths", {})


def evidence_scope(data, client):
    """The path set whose change invalidates one client's evidence.

    Shared source always counts, and so do the files under another runtime's adapter directory
    that shared code reads whatever the runtime. The rest of another runtime's directory does
    not. A runtime the catalog does not map is excluded from nothing, so an unmapped or
    undeclared target keeps the whole-source rule and the scope fails closed.
    """
    declared = invalidation_declaration(data)
    scopes = declared.get("runtime_paths", {})
    excluded, shared = [], []
    if client.get("runtime") in scopes:
        excluded = sorted(path for name, path in scopes.items() if name != client["runtime"])
        shared = sorted(path + "/" + name for path in excluded for name in declared["shared_files"])
    return {"version": SCOPE_VERSION, "paths": list(SOURCE_PATHS), "excluded": excluded,
            "shared": shared}


def scope_pathspec(scope):
    """The git pathspec for the source a target's evidence is checked against.

    `literal` magic is what keeps an exclusion from widening: a declared directory is matched as
    the exact path it is, never as a glob.
    """
    return list(scope["paths"]) + [":(exclude,literal)" + path for path in scope["excluded"]]


def same_scope(declared, scope):
    """Whether an evidence record claims exactly the scope the catalog grants its client.

    A record is untrusted input, so a malformed claim is one more scope the catalog does not
    grant rather than a traceback.
    """
    if not isinstance(declared, dict) or declared.get("version") != scope["version"]:
        return False
    for key in ("paths", "excluded", "shared"):
        names = declared.get(key)
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            return False
        if sorted(names) != sorted(scope[key]):
            return False
    return True


def case_path_map(data):
    """The catalog's validated case-to-path map, or `{}` when none is declared.

    Every required case is a key, so adding a case forces a claim about it. A case's list names
    the mapped source paths whose change can alter what that case observes; an empty list claims
    the case depends on none of them. A changed file under no case's paths invalidates every case,
    so the map fails closed. The rule and the argument for each entry are in docs/compatibility.md.
    """
    block = data.get("evidence_invalidation") or {}
    if "case_paths" not in block:
        return {}
    # Present but empty or malformed is refused, not read as absent, so a broken declaration
    # cannot quietly turn per-case scoping off.
    declared = block["case_paths"]
    version = declared.get("version") if isinstance(declared, dict) else None
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError("the case-to-path map requires a positive integer version")
    cases, required = declared.get("cases"), data.get("required_cases") or []
    if not isinstance(cases, dict) or set(cases) != set(required):
        raise ValueError("the case-to-path map must name every required case and no other")
    for case in sorted(cases):
        paths = cases[case]
        if not isinstance(paths, list) or len(set(map(str, paths))) != len(paths) \
                or not all(mapped_path(path) for path in paths):
            raise ValueError("the case-to-path map gives " + case + " a path that is not a "
                             "literal file or directory under the runtime source")
    return {"version": version, "cases": {case: sorted(cases[case]) for case in cases}}


def mapped_path(path):
    """Whether `path` is a literal repository path under one of the runtime source paths."""
    if not isinstance(path, str) or not path or any(mark in path for mark in "*?[]\\"):
        return False
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    return parts[0] in SOURCE_PATHS


def case_map_identity(data):
    """What a new evidence record states about the map it assumed, or None with no map.

    The digest is what keeps the version honest: a map edited without a version bump no longer
    matches the digest an older record carries, so that record falls back to the whole target.
    """
    declared = case_path_map(data)
    if not declared:
        return None
    text = json.dumps(declared["cases"], sort_keys=True, separators=(",", ":"))
    return {"version": declared["version"], "sha256": hashlib.sha256(text.encode()).hexdigest()}


def under(changed, path):
    return changed == path or changed.startswith(path + "/")


def stale_cases(data, record, changed):
    """The cases a record's changed files invalidate, as `{case: [files]}`, or None for all.

    None means the whole record is stale: it states no map, a map other than the catalog's, or a
    changed file lies under no case's paths.
    """
    identity = case_map_identity(data)
    if identity is None or record.get("case_map") != identity:
        return None
    cases = case_path_map(data)["cases"]
    stale = {}
    for name in changed:
        owners = [case for case, paths in cases.items() if any(under(name, path) for path in paths)]
        if not owners:
            return None
        for case in owners:
            stale.setdefault(case, []).append(name)
    return {case: sorted(names) for case, names in stale.items()}


def changed_files(root, commit, target, paths, carved):
    """The files under `paths`, and under the carved-back `carved`, that differ between the two
    commits, including staged paths for a working HEAD, or None when git cannot say."""
    names = set()
    for spec in (paths, carved):
        if not spec:
            continue
        revisions = ["--cached", commit] if target == "HEAD" else [commit, target]
        done = subprocess.run(["git", "-C", str(root), "diff", "--name-only", "-z", "--no-renames",
                               *revisions, "--", *spec], capture_output=True)
        if done.returncode:
            return None
        names.update(os.fsdecode(name) for name in (done.stdout or b"").split(b"\0") if name)
    return sorted(names)


def catalog(root):
    data = json.loads((root / "compatibility" / "catalog.json").read_text())
    if data.get("schema_version") != 1:
        raise ValueError("unsupported compatibility schema")
    if data.get("harness_version") != (root / "VERSION").read_text().strip():
        raise ValueError("compatibility catalog does not match VERSION")
    identifiers = [row["id"] for row in data["clients"]]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("duplicate compatibility client")
    runtime_scopes(data)
    case_path_map(data)
    target = qualification_source(data)
    if target != "HEAD":
        available = subprocess.run(["git", "-C", str(root), "cat-file", "-e", target + "^{commit}"],
                                   capture_output=True)
        if available.returncode:
            raise ValueError("released qualification source commit is unavailable")
    qualification_reuse(root, data)
    for row in data["clients"]:
        if row.get("status") not in STATES:
            raise ValueError("invalid compatibility status")
        # A truthy string here would silently claim enforcement for a surface that has no hooks.
        if not isinstance(row.get("installs_hooks", True), bool):
            raise ValueError(row["id"] + ": installs_hooks must be true or false")
        if row["status"] == "qualified":
            errors = evidence_errors(root, data, row)
            if errors:
                raise ValueError(row["id"] + ": " + "; ".join(errors))
    return data


def evidence_errors(root, data, client):
    errors, passed, outdated = [], set(), {}
    target = qualification_source(data)
    scope = evidence_scope(data, client)
    if not client.get("runtime_version") or not client.get("client_version"):
        errors.append("native runtime and client versions are required")
    for item in client.get("evidence", []):
        path = (root / item.get("path", "")).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            errors.append("missing or external evidence artifact")
            continue
        try:
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != item.get("sha256"):
                errors.append("evidence digest mismatch")
                continue
            record = json.loads(content)
        except (ValueError, OSError):
            errors.append("unreadable evidence record")
            continue
        if not isinstance(record, dict):
            errors.append("evidence record must be an object")
            continue
        if record.get("client") != client["id"] or record.get("harness_version") != evidence_version(data):
            errors.append("evidence version or client mismatch")
            continue
        if any(not client.get(key) or record.get(key) != client[key]
               for key in ("runtime_version", "client_version", "platform")):
            errors.append("evidence runtime, client version or platform mismatch")
            continue
        if record.get("kind") != "native" or not record.get("observations") or not record.get("source_commit"):
            errors.append("native observations and source commit are required")
            continue
        commit = record["source_commit"]
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40,64}", commit):
            errors.append("native evidence requires a full source commit identity")
            continue
        declared = record.get("invalidation_scope")
        if declared is not None and not same_scope(declared, scope):
            errors.append("evidence claims an invalidation scope the catalog does not grant")
            continue
        paths = scope_pathspec(scope) if declared is not None else list(SOURCE_PATHS)
        # The carve-out cannot ride in the same pathspec: a git exclusion wins over every
        # positive pattern, so the shared files inside an excluded directory need their own diff.
        carved = scope["shared"] if declared is not None else []
        ancestry = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", commit, target], capture_output=True)
        changed = None if ancestry.returncode else changed_files(root, commit, target, paths, carved)
        # Per-case scoping narrows only within the target's path set, and only for a record that
        # states the catalog's current map; anything else keeps the whole-target rule.
        stale = {} if changed == [] else (stale_cases(data, record, changed) if changed else None)
        if stale is None:
            errors.append("runtime source changed or evidence commit is unavailable")
            continue
        cases = record.get("cases")
        if not isinstance(cases, dict) or not cases:
            errors.append("native evidence requires acceptance cases")
            continue
        for case, result in cases.items():
            if case not in data["required_cases"] or result not in ("passed", "failed", "unverified"):
                errors.append("unknown acceptance case or result")
            elif case in stale:
                # The result describes source that has since changed under this case's paths, so
                # it neither passes nor blocks; a rerun linked beside it answers for the case.
                outdated.setdefault(case, set()).update(stale[case])
            elif result != "passed":
                # Every linked record is part of the claim; another pass cannot hide a failure.
                errors.append(case + " is " + result + " in linked evidence")
            else:
                passed.add(case)
    missing = set(data["required_cases"]) - passed
    for case in sorted(missing & set(outdated)):
        errors.append(case + " is stale in linked evidence: source changed under "
                      + ", ".join(sorted(outdated[case])))
    missing -= set(outdated)
    if missing:
        errors.append("missing acceptance cases: " + ", ".join(sorted(missing)))
    return errors


def release_errors(root):
    data = catalog(root)
    errors = [row["id"] + " is " + row["status"] for row in data["clients"]
              if row.get("required_for_release") and row["status"] != "qualified"]
    errors += reconciliation_errors(root, data)
    if source_drift(root, data):
        errors.append("current runtime source differs from the released qualification source")
    return errors


def coverage(root, choices):
    result = {}
    for runtime in ("claude-code", "codex"):
        bindings = json.loads((root / "adapters" / runtime / "capabilities.json").read_text())
        result[runtime] = {name: bindings["stances"].get(name, bindings["custom_stance_default"]) for name in choices}
    return result


def capability_entries(root, runtime):
    """An adapter's capability declarations: its stance dimensions and its role execution.

    A missing adapter means the runtime declares no capability, which is how a `planned` client
    reaches this code without inventing one.
    """
    path = root / "adapters" / runtime / "capabilities.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text())
    entries = dict(data.get("stances", {}))
    if "role_execution" in data:
        entries["role_execution"] = data["role_execution"]
    return entries


def tier_restriction(root, client):
    """Whether the model-tier ceiling binds one client surface, and what makes it bind.

    Enforcement is a hook rewriting a spawn, so a surface that installs no hooks resolves to the
    adapter's `without_hooks` entry instead: the same prose, none of the refusal. A runtime with
    no adapter, or none declaring the key, restricts nothing.
    """
    runtime = str(client.get("runtime"))
    path = root / "adapters" / runtime / "capabilities.json"
    entry = json.loads(path.read_text()).get("tier_restriction") if path.is_file() else None
    if not isinstance(entry, dict):
        return {"state": "none", "mechanism": None}
    # Both branches are validated whichever one this client takes, so a typo in the fallback is
    # not discovered by the one surface that reads it.
    resolved = entry
    for candidate in (entry, entry.get("without_hooks")):
        if candidate is None:
            continue
        if not isinstance(candidate, dict) or candidate.get("state") not in TIER_RESTRICTIONS:
            raise ValueError(runtime + " declares an unknown tier restriction: "
                             + json.dumps(candidate))
        if candidate is not entry and client.get("installs_hooks", True) is False:
            resolved = candidate
    return {"state": resolved["state"], "mechanism": resolved.get("mechanism")}


def capability_states(root, data, client):
    """Per-capability qualification for one client, derived from that runtime's adapter.

    A capability is qualified only when the client is qualified and a required acceptance case
    the adapter names as exercising it passed for that client; it inherits nothing from the
    client's own status. The rule and the optional `acceptance_cases` key are in
    docs/compatibility.md.
    """
    # `catalog` refuses a qualified client whose evidence misses a required case, so a qualified
    # client has passing native evidence for all of them and no other client has any.
    passed = set(data["required_cases"]) if client.get("status") == "qualified" else set()
    result = {}
    for name, entry in sorted(capability_entries(root, client["runtime"]).items()):
        cases = sorted(set(entry.get("acceptance_cases") or ()) & passed)
        state = entry.get("qualification", "unqualified")
        derived = "unqualified" if state == "qualified" and not cases else state
        result[name] = {"state": derived, "declared": state,
                        "mode": entry.get("mode"), "cases": cases}
    return result


def reconciliation_errors(root, data=None):
    """Report where the catalog's acceptance cases and the adapters' capability states disagree.

    The catalog is the single authority: a capability may claim `qualified` only against a case
    the catalog requires, on a runtime some client has qualified natively.
    """
    data = catalog(root) if data is None else data
    required, errors = set(data["required_cases"]), []
    for runtime in sorted({row["runtime"] for row in data["clients"]}):
        qualified = [row["id"] for row in data["clients"]
                     if row["runtime"] == runtime and row["status"] == "qualified"]
        for name, entry in sorted(capability_entries(root, runtime).items()):
            label = runtime + " " + name
            state, cases = entry.get("qualification"), entry.get("acceptance_cases") or []
            if state not in STATES:
                errors.append(label + " declares no valid qualification state")
                continue
            if not isinstance(cases, list) or not all(isinstance(case, str) for case in cases):
                errors.append(label + " lists acceptance cases that are not strings")
                continue
            unknown = sorted(set(cases) - required)
            if unknown:
                errors.append(label + " names acceptance cases the catalog does not require: "
                              + ", ".join(unknown))
            if state == "qualified" and not cases:
                errors.append(label + " is qualified with no acceptance case covering it")
            if state == "qualified" and not qualified:
                errors.append(label + " is qualified while no " + runtime + " client is")
            if state != "qualified" and cases:
                errors.append(label + " is " + state + " yet acceptance cases cover it: "
                              + ", ".join(sorted(cases)))
    return errors

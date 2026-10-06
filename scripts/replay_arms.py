"""Replay arms as fresh containers: declare an arm, build its image, list what it holds, run it.

An arm is a Docker image built from pinned inputs and nothing else. `bare` is the Linux
qualification image's base, Claude Code and the shared observer; `harness` is that plus this repository at one
commit, synced for the image's agent user. Nothing from the machine running the replay reaches
either: no home directory, profile, environment, hook or setting. A run may also mount the fresh
benchmark-owned observation directory prepared and marked by `cost_bench.py`.

Each build writes two JSON files beside the image: the arm's **declaration**, the inputs it was
built from, and its **manifest**, every file and link the image holds under the agent user's
home, Claude Code's managed settings and the harness checkout, hashed (`scripts/arm_manifest.py`).
Building one declaration twice with no cache must give the same manifest; `two_build_check`
does exactly that. A run is `docker run --rm` of the image with the task snapshot as its only
mount, the credential passed by name, and an internal network whose one way out is an allowlist
proxy to the model API (`scripts/egress_proxy.py`). How to use it: docs/benchmarks.md.

Every Docker command is a list handed to a `launch` callable, `subprocess.run` outside a test,
so the unit tests read the command lines without a daemon.
"""
import contextlib
import hashlib
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import experiment_protocol  # noqa: E402  the evidence labels the admission check reads
import arm_manifest  # noqa: E402  re-derive the configuration summary before trusting it

ROOT = Path(__file__).resolve().parents[1]
QUALIFICATION_DOCKERFILE = ROOT / "scripts" / "linux-target.Dockerfile"
ARM_DOCKERFILE = ROOT / "scripts" / "replay-arm.Dockerfile"
MANIFEST_SCRIPT = ROOT / "scripts" / "arm_manifest.py"
OBSERVER_SOURCE = ROOT / "lib" / "harness_core" / "observer.py"
sys.path.insert(0, str(ROOT / "lib"))
from harness_core import observation  # noqa: E402

OBSERVER_COMMAND = "python3 /opt/model-citizen-observer/observe.py --runtime claude-code"

def observer_settings():
    return {"permissions": {"deny": ["WebFetch", "WebSearch"]},
            "hooks": observation.hooks_for(OBSERVER_COMMAND, "claude-code")}

PROXY_SCRIPT = ROOT / "scripts" / "egress_proxy.py"
IMAGE_PREFIX = "model-citizen-arm-"
ARMS = ("bare", "harness")
SCHEMA = 1
# Where every arm sees its task: a path outside the agent user's home, with no instruction file
# above it, so the CLI's parent-folder walk finds nothing but the snapshot itself.
WORKDIR = "/work"
# The one host secret an arm receives, and only by name: `docker run -e NAME` copies it from the
# client's environment, so its value never appears on a command line or in a row.
CREDENTIAL = "CLAUDE_CODE_OAUTH_TOKEN"
# The model API, and nothing else, as Claude Code's network requirements name it. Sign-in hosts
# are left out: an arm authenticates with a token and never signs in.
MODEL_API_HOSTS = ("api.anthropic.com",)
PROXY_PORT = 3128
NO_PROXY = "localhost,127.0.0.1,::1"
# The same for every arm: no optional traffic, no auto-update, no claude.ai connectors.
ARM_ENV = {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1",
           "ENABLE_CLAUDEAI_MCP_SERVERS": "false", "TERM": "dumb"}
# What the Docker client itself needs from the host: its own context and config, never passed on.
CLIENT_ENV = ("HOME", "USER", "PATH", "TERM", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG",
              "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY")
# Hardening every arm container gets: no privilege gain (the base image's user may use sudo) and
# no capabilities.
HARDENING = ["--security-opt", "no-new-privileges", "--cap-drop", "ALL"]
BUILD_TIMEOUT = 3600
MANIFEST_TIMEOUT = 300
# The reasoning effort a run is launched at, pinned with `--effort` on every arm's command line.
# Claude Code's levels; `ultracode` is left out, since it also turns on workflow orchestration.
# The variable overrides `--effort` (Claude Code's environment variable reference), so no arm is
# ever given it: `_pins_its_effort` refuses one that is.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_EFFORT = "high"
EFFORT_ENV = "CLAUDE_CODE_EFFORT_LEVEL"
# Declaration keys that change how an arm is launched and not what its image holds; the image
# name and build label are the digest of the rest, so changing one never rebuilds an image.
LAUNCH_INPUTS = ("effort",)
# A declared selection: its component name, the build-context file the Dockerfile installs as the
# image user's configuration, and the stage that does so.
SELECTION = "selection"
SELECTION_FILE = "selection.json"
SELECTED_TARGET = "harness-selected"


def digest(value):
    """sha256 over canonical JSON: sorted keys, no whitespace. One definition for every digest."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def client_env(extra=None, base=None):
    """The Docker client's environment: what it needs to reach the daemon, plus `extra`."""
    base = os.environ if base is None else base
    env = {name: base[name] for name in CLIENT_ENV if name in base}
    env.update(extra or {})
    return env


def qualification_inputs(path=QUALIFICATION_DOCKERFILE):
    """`{base_image, claude_code_version}` read from the Linux qualification Dockerfile, which is
    the one place both are pinned. A base without a digest is refused: an arm is pinned or it is
    not an arm."""
    text = Path(path).read_text(encoding="utf-8")
    base = re.search(r"^FROM\s+(\S+)", text, re.M)
    version = re.search(r"^ARG\s+CLAUDE_CODE_VERSION=(\S+)", text, re.M)
    if not base or "@sha256:" not in base.group(1) or not version:
        raise SystemExit("replay-arms: %s pins no base digest or Claude Code version" % path)
    return {"base_image": base.group(1), "claude_code_version": version.group(1)}


def selection_bytes(selection):
    """The user configuration a declared selection is installed as, byte for byte: canonical JSON
    with a trailing newline. The `selection` component's version is the sha256 of these bytes."""
    return (json.dumps(selection, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _selection_problem(selection):
    """Why `selection` is not a selection in the user-config shape, or None."""
    if not isinstance(selection, dict):
        return "a selection is an object of kind to {unit: value}"
    for kind, units in selection.items():
        # A core hook switched off needs the acknowledgement a user gives (`posture.CORE_ACK`).
        if kind == "core_switches_acknowledged" and units is True:
            continue
        if not isinstance(kind, str) or not re.fullmatch(r"[a-z][a-z-]*", kind) or not isinstance(units, dict) \
                or not units or any(not isinstance(unit, str) or not isinstance(value, str) or not value
                                    for unit, value in units.items()):
            return "selection kind %r is not an object of unit to a non-empty value" % (kind,)
    return None


def declaration(arm, inputs, harness=None, claude_code_version=None, effort=None, selection=None):
    """What one arm is built from, as a dict: every input that could change what it holds.

    `harness` is `{ref, commit}` for the harness arm and None for the bare one; the commit is the
    full sha the ref resolved to, so a moved tag is a different declaration. The Dockerfile and
    the manifest lister are inputs too, by content. `effort` is the pinned launch input: the
    reasoning effort every run of the arm is started at, None for an arm that is only built.

    `selection` is a declared selection in the user-config shape `bin/harness sync` reads, such as
    `{"rules": {"secrets": "off"}}`, for the harness arm only. It is installed as the image user's
    configuration before the sync, so the sync withholds what it switches off, and it is declared
    as a `selection` component whose version is the digest of the installed bytes; admission
    accepts that file only when it matches (`_user_config_problem`). An arm with no selection
    has no `selection` key at all, so its declaration and image name are what they always were."""
    if effort is not None and effort not in EFFORT_LEVELS:
        raise SystemExit("replay-arms: effort %r is not one of %s" % (effort, ", ".join(EFFORT_LEVELS)))
    if arm not in ARMS:
        raise SystemExit("replay-arms: unknown arm %r; the arms are %s" % (arm, ", ".join(ARMS)))
    if (arm == "harness") != bool(harness):
        raise SystemExit("replay-arms: the harness arm needs a ref and commit, and only it")
    if harness and not re.fullmatch(r"[0-9a-f]{40}", harness.get("commit") or ""):
        raise SystemExit("replay-arms: harness commit %r is not a full sha" % harness.get("commit"))
    if selection is not None:
        if not harness:
            raise SystemExit("replay-arms: only the harness arm takes a declared selection")
        problem = _selection_problem(selection)
        if problem:
            raise SystemExit("replay-arms: %s" % problem)
    version = claude_code_version or inputs["claude_code_version"]
    components = [{"name": "base-image", "version": inputs["base_image"]},
                  {"name": "@anthropic-ai/claude-code", "version": version},
                  {"name": "model-citizen-observer", "version": "sha256:" + file_sha(OBSERVER_SOURCE)}]
    if harness:
        components.append({"name": "model-citizen", "version": harness["ref"], "commit": harness["commit"]})
    if selection is not None:
        components.append({"name": SELECTION,
                           "version": "sha256:" + hashlib.sha256(selection_bytes(selection)).hexdigest()})
    out = {"schema": SCHEMA, "arm": arm, "base_image": inputs["base_image"],
           "claude_code_version": version,
           "harness": dict(ref=harness["ref"], commit=harness["commit"]) if harness else None,
           "components": components, "dockerfile_sha256": file_sha(ARM_DOCKERFILE),
           "manifest_script_sha256": file_sha(MANIFEST_SCRIPT), "effort": effort,
           "observer_settings_sha256": digest(observer_settings())}
    if selection is not None:
        out[SELECTION] = json.loads(json.dumps(selection))
    return out


def build_inputs(decl):
    """The declaration less its launch inputs: what the image itself is built from."""
    return {key: value for key, value in decl.items() if key not in LAUNCH_INPUTS}


def label(decl):
    """The arm as a row and a person name it: `bare`, or `harness@<ref>`."""
    return decl["arm"] if not decl["harness"] else "harness@%s" % decl["harness"]["ref"]


def image_name(decl, tag=None):
    """`model-citizen-arm-<arm>:<tag>`, the tag defaulting to the digest of the declaration's build
    inputs, so one set of inputs always names one image and a changed input a new one."""
    return "%s%s:%s" % (IMAGE_PREFIX, decl["arm"], tag or digest(build_inputs(decl))[:12])


def build_context(decl, parent, snapshot, repo=ROOT):
    """The build context with the declared observer, plus the harness commit for that arm."""
    context = Path(parent) / "context"
    context.mkdir(parents=True)
    observer = context / "observer"
    observer.mkdir()
    shutil.copyfile(str(OBSERVER_SOURCE), str(observer / "observe.py"))
    if decl["harness"]:
        snapshot(repo, decl["harness"]["commit"], context / "harness")
    if decl.get(SELECTION) is not None:
        (context / SELECTION_FILE).write_bytes(selection_bytes(decl[SELECTION]))
    return context


def target(decl):
    """The Dockerfile stage an arm builds: its own name, or `SELECTED_TARGET` for a harness arm
    with a declared selection, which installs the selection before the sync."""
    return SELECTED_TARGET if decl.get(SELECTION) is not None else decl["arm"]


def build_command(decl, context, image, no_cache=False):
    command = ["docker", "build", "--quiet", "-f", str(ARM_DOCKERFILE), "--target", target(decl),
               "--build-arg", "BASE_IMAGE=" + decl["base_image"],
               "--build-arg", "CLAUDE_CODE_VERSION=" + decl["claude_code_version"],
               "--label", "org.model-citizen.arm.declaration=" + digest(build_inputs(decl))]
    if decl["harness"]:
        command += ["--build-arg", "HARNESS_COMMIT=" + decl["harness"]["commit"]]
    if no_cache:
        command.append("--no-cache")
    return command + ["-t", image, str(context)]


def manifest_command(image):
    """The lister runs from stdin in a fresh container with no network and no mount."""
    return ["docker", "run", "--rm", "-i", "--network", "none"] + HARDENING + [image, "python3", "-"]


def inspect_command(image):
    return ["docker", "image", "inspect", "--format", "{{.Id}}", image]


def remove_image_command(*images):
    return ["docker", "image", "rm", "--force"] + list(images)


def _run(launch, command, **kwargs):
    kwargs.setdefault("env", client_env())
    kwargs.setdefault("stdout", subprocess.PIPE)
    kwargs.setdefault("stderr", subprocess.PIPE)
    kwargs.setdefault("universal_newlines", True)
    done = launch(command, **kwargs)
    if done.returncode:
        tail = "\n".join(((done.stderr or "") + (done.stdout or "")).strip().splitlines()[-8:])
        raise SystemExit("replay-arms: `%s` failed (exit %d)\n%s" % (" ".join(command[:4]), done.returncode, tail))
    return done


def read_manifest(image, launch=subprocess.run):
    """The image's manifest as a dict, listed inside a fresh container of it."""
    done = _run(launch, manifest_command(image), input=MANIFEST_SCRIPT.read_text(encoding="utf-8"),
                timeout=MANIFEST_TIMEOUT)
    try:
        return json.loads(done.stdout)
    except ValueError:
        raise SystemExit("replay-arms: the manifest of %s is not JSON" % image)


def write_record(out_dir, image, decl, manifest):
    """Write `<image>.declaration.json` and `<image>.manifest.json` into `out_dir`; their paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = image.replace(":", "-").replace("/", "-")
    paths = {"declaration": out_dir / (stem + ".declaration.json"), "manifest": out_dir / (stem + ".manifest.json")}
    for key, value in (("declaration", decl), ("manifest", manifest)):
        paths[key].write_text(json.dumps(value, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    return paths


def build_arm(decl, out_dir, snapshot, launch=subprocess.run, repo=ROOT, no_cache=False, tag=None, tmp=None):
    """Build one arm, list it, and write its declaration and manifest beside it.

    Returns the arm record every row is stamped from: `{arm, label, image, image_id, declaration,
    declaration_sha256, manifest, manifest_sha256, harness_ref, harness_commit, paths}`."""
    image = image_name(decl, tag)
    parent = Path(tempfile.mkdtemp(prefix="model-citizen-arm-build-", dir=tmp))
    try:
        context = build_context(decl, parent, snapshot, repo)
        _run(launch, build_command(decl, context, image, no_cache), timeout=BUILD_TIMEOUT)
    finally:
        shutil.rmtree(str(parent), ignore_errors=True)
    image_id = _run(launch, inspect_command(image)).stdout.strip()
    manifest = read_manifest(image, launch)
    paths = write_record(out_dir, image, decl, manifest)
    harness = decl["harness"] or {}
    return {"arm": decl["arm"], "label": label(decl), "image": image, "image_id": image_id,
            "declaration": decl, "declaration_sha256": digest(decl), "manifest": manifest,
            "manifest_sha256": digest(manifest), "harness_ref": harness.get("ref"),
            "harness_commit": harness.get("commit"), "paths": {k: str(v) for k, v in paths.items()}}


def compare_manifests(first, second):
    """Every difference between two manifests, one line each; empty when they are equal."""
    out = []
    for key in sorted(set(first) | set(second)):
        if key != "entries" and first.get(key) != second.get(key):
            out.append("%s: %r != %r" % (key, first.get(key), second.get(key)))
    left = {e["path"]: e for e in first.get("entries", [])}
    right = {e["path"]: e for e in second.get("entries", [])}
    for path in sorted(set(left) | set(right)):
        if path not in right:
            out.append("only in the first build: %s" % path)
        elif path not in left:
            out.append("only in the second build: %s" % path)
        elif left[path] != right[path]:
            fields = sorted(k for k in set(left[path]) | set(right[path]) if left[path].get(k) != right[path].get(k))
            out.append("differs: %s (%s)" % (path, ", ".join(fields)))
    return out


def two_build_check(decl, out_dir, snapshot, launch=subprocess.run, repo=ROOT, dry_run=False, tmp=None, say=print):
    """Build `decl` twice with no cache and compare the manifests. True when they are equal.

    Both images are removed afterwards; their declaration and manifest files stay in `out_dir`.
    A dry run prints every command it would run and builds nothing."""
    tags = ("check-1", "check-2")
    images = [image_name(decl, tag) for tag in tags]
    if dry_run:
        say("two-build check of %s (declaration %s), dry run:" % (label(decl), digest(decl)[:12]))
        for image in images:
            say("  " + " ".join(build_command(decl, "<context>", image, no_cache=True)))
            say("  " + " ".join(manifest_command(image)) + " < " + MANIFEST_SCRIPT.name)
        say("  compare the two manifests entry by entry; equal passes")
        say("  " + " ".join(remove_image_command(*images)))
        return True
    records = []
    try:
        for tag in tags:
            records.append(build_arm(decl, out_dir, snapshot, launch, repo, no_cache=True, tag=tag, tmp=tmp))
    finally:
        launch(remove_image_command(*images), env=client_env(), stdout=subprocess.PIPE,
               stderr=subprocess.PIPE, universal_newlines=True)
    diffs = compare_manifests(records[0]["manifest"], records[1]["manifest"])
    say("two-build check of %s: manifest %s and %s, %s"
        % (label(decl), records[0]["manifest_sha256"][:12], records[1]["manifest_sha256"][:12],
           "equal" if not diffs else "%d difference(s)" % len(diffs)))
    for line in diffs:
        say("  " + line)
    return not diffs


# --- Arm configs: a further harness arm built from a named stance selection --------------------

# An arm config is a small JSON file naming stance dimension to variant, for example
# `{"schema": 1, "description": "...", "stances": {"voice": "concise"}}`. Its stances become the
# arm's declared selection (`declaration(selection=...)`), so the image holds the harness at the
# tag synced with exactly that selection, and the run admits it with the pair check every harness
# arm passes. Shipped examples: benchmarks/arms/. Use: docs/benchmarks.md, "Arm configs".
ARM_CONFIG_SCHEMA = 1
ARM_CONFIG_KEYS = ("schema", "description", "stances")
ARM_CONFIG_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
# Names a run already gives an arm, in a replay, a pair or an ablation run.
RESERVED_ARM_NAMES = ARMS + ("control", "reference", "treatment")
STANCE_NAME = re.compile(r"[a-z][a-z0-9-]*")


def arm_config_problem(data):
    """Why `data` is not an arm config, or None: the schema, at most a description, and a non-empty
    `stances` object of dimension to variant, each an identifier."""
    if not isinstance(data, dict):
        return "an arm config is a JSON object"
    unknown = sorted(set(data) - set(ARM_CONFIG_KEYS))
    if unknown:
        return "unknown key(s) %s; an arm config holds %s" % (", ".join(unknown), ", ".join(ARM_CONFIG_KEYS))
    if data.get("schema") != ARM_CONFIG_SCHEMA:
        return "schema must be %d" % ARM_CONFIG_SCHEMA
    if "description" in data and not isinstance(data["description"], str):
        return "description must be a string"
    stances = data.get("stances")
    if not isinstance(stances, dict) or not stances:
        return "stances must be a non-empty object of dimension to variant"
    for dimension, variant in stances.items():
        if not STANCE_NAME.fullmatch(dimension) or not isinstance(variant, str) or not STANCE_NAME.fullmatch(variant):
            return "stance %r: %r is not a variant name" % (dimension, variant)
    return None


def load_arm_config(path):
    """The arm config at `path`, parsed and checked for shape; SystemExit naming the file otherwise."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit("replay-arms: cannot read the arm config %s: %s" % (path, exc))
    problem = arm_config_problem(data)
    if problem:
        raise SystemExit("replay-arms: the arm config %s is refused: %s" % (path, problem))
    return data


def arm_config_name_problem(name):
    """Why `name` cannot name a config arm, or None."""
    if not ARM_CONFIG_NAME.fullmatch(name or ""):
        return "arm name %r is not lower-case letters, digits and hyphens" % (name,)
    if name in RESERVED_ARM_NAMES:
        return "arm name %r is taken; the reserved names are %s" % (name, ", ".join(RESERVED_ARM_NAMES))
    return None


def arm_config_sha256(config):
    """The config's digest: canonical JSON, so formatting and key order never move it."""
    return digest(config)


def arm_config_selection(config):
    """The selection the arm declares, in the user-config shape the sync reads."""
    return {"stances": dict(config["stances"])}


def arm_config_stamp(name, config):
    """What every row of a config arm records as `arm_config`."""
    return {"name": name, "schema": config["schema"], "sha256": arm_config_sha256(config),
            "stances": dict(config["stances"])}


def stance_variants(posture, root):
    """`{dimension: [variant, ...]}` the checkout at `root` ships, read through its own resolver's
    primitive roots with no user configuration."""
    out = {}
    for source in posture.primitive_roots({}, Path(root), "stances"):
        if not source.is_dir():
            continue
        for path in sorted(source.glob("*/*.md")):
            out.setdefault(path.parent.name, set()).add(path.stem)
    return {dimension: sorted(variants) for dimension, variants in sorted(out.items())}


def arm_config_errors(name, config, posture, root, env=None):
    """Every reason the checkout at `root` cannot build this config arm, one line each: an unknown
    dimension, a variant it does not ship, a selection its resolver refuses, or a selection that
    is the tag's default throughout and so would be the harness arm again."""
    env = dict(env or {}, HOME=str(Path(root) / ".arm-config-empty-home"))
    available = stance_variants(posture, root)
    errors = []
    for dimension, variant in sorted(config["stances"].items()):
        if dimension not in available:
            errors.append("%s: unknown stance dimension %s; the dimensions are %s"
                          % (name, dimension, ", ".join(available)))
        elif variant not in available[dimension]:
            errors.append("%s: stance %s has no variant %s; its variants are %s"
                          % (name, dimension, variant, ", ".join(available[dimension])))
    if errors:
        return errors
    try:
        chosen = posture.selection(env, strict=True, config=arm_config_selection(config), root=Path(root))
        default = posture.selection(env, strict=False, config={}, root=Path(root))
    except ValueError as exc:
        return ["%s: the tag's resolver refuses its selection: %s" % (name, str(exc).splitlines()[0])]
    if chosen.get("stances") == default.get("stances"):
        errors.append("%s: every stance it names is already the tag's default, so it is the harness arm" % name)
    return errors


# --- The seam where an arm can be refused before it launches -----------------------------------

# Callables `check(record) -> reason or None`, run by `admit` in order. The replay calls `admit`
# once per arm, after its build and before the first launch of that arm; a check that refuses
# stops the replay before anything is spent. The protocol's refusals (docs/evidence-standard.md)
# are the checks below.
ADMISSION_CHECKS = []
MANIFEST_ENTRY_KINDS = ("dir", "file", "link", "other")


def _manifest_entries(manifest):
    """Manifest entries whose path and kind are safe for every downstream comparison."""
    entries = manifest.get("entries") if isinstance(manifest, dict) else None
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)
            and isinstance(entry.get("path"), str) and entry.get("path")
            and entry.get("kind") in MANIFEST_ENTRY_KINDS]


def _has_intact_records(record):
    """The stored declaration and manifest are schema-known and match their recorded digests."""
    problems = []
    for key in ("image", "image_id"):
        if not record.get(key):
            problems.append("no %s recorded" % key)
    for key, schema in (("declaration", SCHEMA), ("manifest", arm_manifest.SCHEMA)):
        value = record.get(key)
        if not isinstance(value, dict):
            problems.append("%s is not an object" % key)
            continue
        if value.get("schema") != schema:
            problems.append("%s schema is %r, expected %d" % (key, value.get("schema"), schema))
        recorded = record.get(key + "_sha256")
        if not isinstance(recorded, str) or not re.fullmatch(r"[0-9a-f]{64}", recorded):
            problems.append("%s_sha256 is not a sha256" % key)
        elif recorded != digest(value):
            problems.append("%s_sha256 does not match the recorded %s" % (key, key))
    manifest = record.get("manifest")
    if isinstance(manifest, dict):
        entries = manifest.get("entries")
        if not isinstance(entries, list):
            problems.append("manifest entries is not a list")
        else:
            paths = []
            for number, entry in enumerate(entries):
                if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) \
                        or not entry.get("path"):
                    problems.append("manifest entries contain a malformed path at entry %d" % number)
                    continue
                paths.append(entry["path"])
                if entry.get("kind") not in MANIFEST_ENTRY_KINDS:
                    problems.append("manifest entry %d has unsupported kind %r" %
                                    (number, entry.get("kind")))
            if len(paths) != len(set(paths)):
                problems.append("manifest entries contain duplicate paths")
        summary = manifest.get("summary")
        if not isinstance(summary, dict) or any(not isinstance(kind, str)
                or not isinstance(paths, list)
                or any(not isinstance(path, str) for path in paths)
                for kind, paths in summary.items()):
            problems.append("manifest summary is malformed")
        environment = manifest.get("environment")
        if not isinstance(environment, dict) or EFFORT_ENV not in environment:
            problems.append("manifest does not record the image's %s override" % EFFORT_ENV)
    return "; ".join(problems) or None


# Where the harness arm's declared component is installed, and the exact regular files its sync
# and trust commands write into a fresh profile. Other entries are links into the checkout.
HARNESS_ROOT = "/opt/model-citizen"
HARNESS_WRITES = ("home:.claude/settings.json", "home:.claude/CLAUDE.personal.md",
                  "home:.codex/AGENTS.md", "home:.codex/config.toml", "home:.codex/hooks.json",
                  "home:.config/agent-harness/config.json",
                  "home:.config/agent-harness/trusted.txt",
                  "home:.local/state/agent-harness/manifest.json",
                  "home:.local/state/agent-harness/applied.json",
                  # the sync's record of what it owns in each file it edits
                  "home:.local/state/agent-harness/ownership.json",
                  # `ensure_gitignore_entries`: the global ignore lines from claude/OWNERSHIP.json
                  "home:.config/git/ignore",
                  # the file the sync's lock is taken on; see `EMPTY_WRITES`
                  "home:.local/state/agent-harness/sync.lock")
# Writes the harness's only while they hold nothing: a lock file carrying bytes is not the sync's.
EMPTY_WRITES = ("home:.local/state/agent-harness/sync.lock",)
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
# Directories the sync makes even when it leaves them empty: Claude Code's plans directory, which
# the harness ignores globally. Any other directory is the harness's only as the parent of a write.
HARNESS_DIRS = ("home:.claude/plans",)
# Declared components that are not global npm packages; every other one is, as `name@version`.
NOT_PACKAGES = ("base-image", "model-citizen", "model-citizen-observer", SELECTION)
# The image user's configuration, and the file a sync copies there when it finds none.
USER_CONFIG = "home:.config/agent-harness/config.json"
EXAMPLE_CONFIG = "harness:config.example.json"


def _matches_its_declaration(record):
    """The manifest holds exactly the declared Claude Code, agent clients and harness commit."""
    decl = record.get("declaration") if isinstance(record.get("declaration"), dict) else {}
    manifest = record.get("manifest") if isinstance(record.get("manifest"), dict) else {}
    problems = []
    components = decl.get("components")
    if not isinstance(components, list):
        return "declaration components is not a list"
    valid = []
    for number, component in enumerate(components):
        if not isinstance(component, dict) or not isinstance(component.get("name"), str) \
                or not component.get("name") or not isinstance(component.get("version"), str) \
                or not component.get("version"):
            problems.append("declaration component %d is malformed" % number)
        else:
            valid.append(component)
    names = [component["name"] for component in valid]
    duplicates = sorted(name for name in set(names) if names.count(name) > 1)
    if duplicates:
        problems.append("declaration has duplicate components: %s" % ", ".join(duplicates))
    model_citizen = [component for component in valid if component["name"] == "model-citizen"]
    observers = [component for component in valid if component["name"] == "model-citizen-observer"]
    harness = decl.get("harness")
    if bool(harness) != (len(model_citizen) == 1):
        problems.append("declaration must have exactly one model-citizen component iff it names a harness")
    elif model_citizen and (model_citizen[0].get("version") != harness.get("ref")
                            or model_citizen[0].get("commit") != harness.get("commit")):
        problems.append("the model-citizen component does not match the declared harness")
    observer_entries = [entry for entry in manifest.get("entries") or []
                        if isinstance(entry, dict)
                        and str(entry.get("path") or "").startswith("observer:")]
    observer_entry = next((entry for entry in observer_entries
                           if entry.get("path") == "observer:observe.py"), None)
    observer_version = observers[0].get("version") if len(observers) == 1 else None
    if decl.get("observer_settings_sha256") != digest(observer_settings()):
        problems.append("observer hook settings differ from their declaration")
    if len(observers) != 1:
        problems.append("declaration must have exactly one model-citizen-observer component")
    elif not re.fullmatch(r"sha256:[0-9a-f]{64}", observer_version or ""):
        problems.append("the observer component version is not a sha256")
    elif not observer_entry or observer_entry.get("kind") != "file" \
            or "sha256:" + str(observer_entry.get("sha256")) != observer_version:
        problems.append("the installed observer does not match its declared sha256")
    if [entry.get("path") for entry in observer_entries] != ["observer:observe.py"]:
        problems.append("the observer root must hold only its declared entry point")
    if (manifest.get("roots") or {}).get("observer") != "/opt/model-citizen-observer":
        problems.append("the manifest lacks the declared observer root")
    if manifest.get("claude_code_version") != decl.get("claude_code_version"):
        problems.append("the manifest holds Claude Code %r, the declaration %r" % (
            manifest.get("claude_code_version"), decl.get("claude_code_version")))
    declared = sorted("%s@%s" % (c["name"], c["version"]) for c in valid
                      if c["name"] not in NOT_PACKAGES)
    held = sorted(manifest.get("cli_packages") or [])
    if held != declared:
        problems.append("the manifest's agent clients %s differ from the declared %s" % (held, declared))
    commit = (harness or {}).get("commit") if isinstance(harness, dict) else None
    if manifest.get("harness_commit") != commit:
        problems.append("the manifest's harness commit %r differs from the declared %r"
                        % (manifest.get("harness_commit"), commit))
    if ("harness" in (manifest.get("roots") or {})) != bool(commit):
        problems.append("the manifest %s a harness checkout the declaration %s" %
                        (("holds", "does not name") if not commit else ("lacks", "names")))
    return "; ".join(problems) or None


def _inside(path, root):
    if not isinstance(path, str) or not isinstance(root, str):
        return False
    path, root = posixpath.normpath(path), posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def _generated_writes(entries):
    """Exact generated home paths justified by source files in the declared checkout."""
    found = set(HARNESS_WRITES)
    for entry in entries:
        if entry.get("kind") != "file":
            continue
        path = entry["path"]
        role = re.fullmatch(r"harness:primitives/roles/([^/]+)\.md", path)
        workflow = re.fullmatch(r"harness:primitives/workflows/([^/]+)\.md", path)
        if role:
            found.add("home:.codex/agents/%s.toml" % role.group(1))
        if workflow:
            found.add("home:.agents/skills/harness-%s/SKILL.md" % workflow.group(1))
    return found


def _is_harness_write(entry, generated=None):
    """A regular file a fresh sync renders, excluding personal input files it only reads."""
    if not isinstance(entry, dict) or entry.get("kind") != "file":
        return False
    if entry.get("path") in EMPTY_WRITES and entry.get("sha256") != EMPTY_SHA256:
        return False
    return entry.get("path") in (set(HARNESS_WRITES) if generated is None else generated)


def _configuration_is_declared(record):
    """Every setting, hook, rule, skill, agent, plugin and instruction file in the manifest comes
    from a declared component: the bare arm has none, and the harness arm's are links into its
    declared checkout or the files its sync writes. Anything else was inherited."""
    decl = record.get("declaration") if isinstance(record.get("declaration"), dict) else {}
    manifest = record.get("manifest") if isinstance(record.get("manifest"), dict) else {}
    harness = bool(decl.get("harness"))
    listed = _manifest_entries(manifest)
    entries = {e.get("path"): e for e in listed}
    expected = arm_manifest.summary(listed)
    actual = manifest.get("summary") if isinstance(manifest.get("summary"), dict) else {}
    if any(not isinstance(kind, str) or not isinstance(paths, list)
           or any(not isinstance(path, str) for path in paths)
           for kind, paths in actual.items()):
        return "the manifest summary does not match its entries"
    normalised = dict((kind, sorted(actual.get(kind) or [])
                       if isinstance(actual.get(kind) or [], list) else [])
                      for kind in expected)
    if normalised != expected or set(actual) - set(expected):
        return "the manifest summary does not match its entries"
    problems = []
    config = _user_config_problem(decl, entries)
    if config:
        problems.append(config)
    generated = _generated_writes(listed)
    for kind, paths in sorted((manifest.get("summary") or {}).items()):
        for path in sorted(paths):
            entry = entries.get(path) or {}
            if harness and (entry.get("kind") == "dir" or _is_harness_write(entry, generated)
                            or (entry.get("kind") == "link" and _inside(entry.get("target", ""), HARNESS_ROOT))):
                continue
            problems.append("%s entry %s is not in the declaration" % (kind, path))
    return "; ".join(problems) or None


def _user_config_problem(decl, entries):
    """Why the image user's configuration is not the declared one, or None.

    With a declared selection the file must be exactly `selection_bytes` of it, matching the
    `selection` component. Without one, a harness arm's file may only be the copy of the commit's
    `config.example.json` the sync writes when it finds none, and a bare arm holds none at all: a
    selection nobody declared is refused rather than measured as the harness's default."""
    entry = entries.get(USER_CONFIG)
    selection = decl.get(SELECTION)
    declared = [c.get("version") for c in decl.get("components") or []
                if isinstance(c, dict) and c.get("name") == SELECTION]
    if selection is not None:
        expected = None if _selection_problem(selection) else \
            "sha256:" + hashlib.sha256(selection_bytes(selection)).hexdigest()
        if expected is None or declared != [expected]:
            return "the selection component does not match the declared selection"
        if not isinstance(entry, dict) or entry.get("kind") != "file":
            return "the declared selection is not installed as %s" % USER_CONFIG
        if "sha256:%s" % entry.get("sha256") != expected:
            return "%s differs from the declared selection" % USER_CONFIG
        return None
    if declared:
        return "a selection component is declared with no selection"
    if entry is None:
        return None
    example = entries.get(EXAMPLE_CONFIG)
    if not decl.get("harness") or not isinstance(example, dict) or entry.get("kind") != "file" \
            or entry.get("sha256") != example.get("sha256"):
        return "%s is not in the declaration: no selection is declared and it is not the sync's " \
               "copy of config.example.json" % USER_CONFIG
    return None


def host_paths(base=None, home=None):
    """The host paths no arm may see: the home directory, which holds the profile and the live
    checkout, this checkout, and an ambient CLAUDE_CONFIG_DIR. Resolved; the root itself is never
    one, since every path is under it."""
    base = os.environ if base is None else base
    found = [str(home or Path.home()), str(ROOT)] + ([base["CLAUDE_CONFIG_DIR"]] if base.get("CLAUDE_CONFIG_DIR") else [])
    out = []
    for path in found:
        for form in (path, os.path.realpath(path)):
            if form not in out and form.rstrip("/"):
                out.append(form.rstrip("/"))
    return out


def host_path_reason(sources=(), env=None, strings=(), paths=None):
    """The first host path reaching an arm: a mount source under one, an env value naming one, or
    one of `strings` (recorded inputs) naming one. None when there is none."""
    paths = host_paths() if paths is None else paths
    for source in sources:
        for form in (source, os.path.realpath(source)):
            if any(_inside(form, p) for p in paths):
                return "the mount %s is under the host path %s" % (source, next(p for p in paths if _inside(form, p)))
    named = [("the variable %s" % key, str(value)) for key, value in sorted((env or {}).items())]
    named += [("the recorded input %r" % value, value) for value in strings]
    for what, value in named:
        for path in paths:
            if value == path or path + "/" in value:
                return "%s names the host path %s" % (what, path)
    return None


def _strings(value):
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return [value] if isinstance(value, str) else []


def _no_host_path(record):
    """No recorded input names a host path: the declaration and manifest link targets.

    Manifest roots are paths inside the isolated image, not inputs from the host. Mounts and
    environment are refused at launch by `run_command`."""
    manifest = record.get("manifest") if isinstance(record.get("manifest"), dict) else {}
    strings = _strings(record.get("declaration") or {})
    strings += [e["target"] for e in _manifest_entries(manifest)
                if e.get("kind") == "link" and isinstance(e.get("target"), str) and e.get("target")]
    return host_path_reason(strings=strings)


def _is_preregistered_or_exploratory(record):
    """A run is exploratory, or names its committed plan; `experiment_protocol.admit` decides which
    before anything is built, and the replay passes its stamp here as `protocol`."""
    protocol = record.get("protocol") or {}
    if protocol.get("evidence") == experiment_protocol.EXPLORATORY:
        return None
    if protocol.get("evidence") == experiment_protocol.PREREGISTERED and protocol.get("pre_registration") \
            and protocol.get("pre_registration_commit"):
        return None
    return "no committed pre-registration and not labelled exploratory"


def _pins_its_effort(record):
    """The arm declares the reasoning effort it launches at, and nothing it is given by value can
    override that: the effort variable outranks `--effort`."""
    declaration = record.get("declaration") if isinstance(record.get("declaration"), dict) else {}
    effort = declaration.get("effort")
    if effort not in EFFORT_LEVELS:
        return "no reasoning effort pinned in its declaration (got %r)" % (effort,)
    if EFFORT_ENV in ARM_ENV:
        return "%s is set for every arm and would override --effort" % EFFORT_ENV
    manifest = record.get("manifest") if isinstance(record.get("manifest"), dict) else {}
    environment = manifest.get("environment") if isinstance(manifest.get("environment"), dict) else {}
    baked = environment.get(EFFORT_ENV)
    if baked:
        return "the image bakes %s=%r, which would override --effort" % (EFFORT_ENV, baked)
    return None


ADMISSION_CHECKS.extend([_has_intact_records, _matches_its_declaration, _configuration_is_declared, _no_host_path,
                         _is_preregistered_or_exploratory, _pins_its_effort])


def admit(record, checks=None):
    """SystemExit naming every reason the arm may not run; None when every check admits it."""
    reasons = [reason for reason in
               (check(record) for check in (ADMISSION_CHECKS if checks is None else checks)) if reason]
    if reasons:
        raise SystemExit("replay-arms: refusing the %s arm:\n  %s"
                         % (record.get("label") or "?", "\n  ".join(reasons)))


# --- Pair parity: the two arms differ by the declared treatment and nothing else ----------------

# Declaration keys that name the treatment itself; every other one must be equal across the pair.
TREATMENT_KEYS = ("arm", "harness", "components", SELECTION)
# Manifest keys that describe the treatment or are derived from the entries compared below.
MANIFEST_TREATMENT_KEYS = ("entries", "roots", "harness_commit", "summary")


def _treatment_paths(manifest):
    """Exact manifest paths attributable to the harness, the directories it declares, plus only
    their directory parents."""
    entries = _manifest_entries(manifest)
    generated = _generated_writes(entries)
    paths = {entry.get("path") for entry in entries
             if (entry.get("path") or "").startswith("harness:")
             or _is_harness_write(entry, generated)
             or (entry.get("kind") == "dir" and entry.get("path") in HARNESS_DIRS)
             or (entry.get("kind") == "link" and _inside(entry.get("target") or "", HARNESS_ROOT))}
    leaves = set(paths)
    for entry in entries:
        path = entry.get("path") or ""
        if entry.get("kind") == "dir" and any(_inside(leaf, path) for leaf in leaves):
            paths.add(path)
    return paths


def pair_differences(bare, harness):
    """Every way the two arms' declarations and manifests differ beyond the harness component, one
    line each; empty when the harness is the only difference. The per-arm checks above say each
    arm holds its own declaration; this says the pair holds the same everything else."""
    out = []
    left, right = bare.get("declaration") or {}, harness.get("declaration") or {}
    for key in sorted((set(left) | set(right)) - set(TREATMENT_KEYS)):
        if left.get(key) != right.get(key):
            out.append("declaration %s: bare %r, harness %r" % (key, left.get(key), right.get(key)))
    shared = [c for c in right.get("components") or [] if c.get("name") not in ("model-citizen", SELECTION)]
    if (left.get("components") or []) != shared:
        out.append("declaration components: bare %r, harness less its own %r" % (left.get("components"), shared))
    left, right = bare.get("manifest") or {}, harness.get("manifest") or {}
    for key in sorted((set(left) | set(right)) - set(MANIFEST_TREATMENT_KEYS)):
        if left.get(key) != right.get(key):
            out.append("manifest %s: bare %r, harness %r" % (key, left.get(key), right.get(key)))
    roots = {k: v for k, v in (right.get("roots") or {}).items() if k != "harness"}
    if (left.get("roots") or {}) != roots:
        out.append("manifest roots: bare %r, harness %r" % (left.get("roots"), right.get("roots")))
    ours = {e["path"]: e for e in _manifest_entries(left)}
    theirs = {e["path"]: e for e in _manifest_entries(right)}
    treatment = _treatment_paths(right)
    for path in sorted(set(ours) | set(theirs)):
        if path in treatment:
            if path in ours and ours[path].get("kind") != "dir":
                out.append("in the bare arm, inside the harness component: %s" % path)
            continue
        if path not in theirs:
            out.append("only in the bare arm: %s" % path)
        elif path not in ours:
            out.append("only in the harness arm, outside the harness component: %s" % path)
        elif ours[path] != theirs[path]:
            fields = sorted(k for k in set(ours[path]) | set(theirs[path]) if ours[path].get(k) != theirs[path].get(k))
            out.append("differs outside the harness component: %s (%s)" % (path, ", ".join(fields)))
    return out


def admit_pair(bare, harness):
    """SystemExit listing every difference `pair_differences` finds; None when there is none."""
    lines = pair_differences(bare, harness)
    if lines:
        raise SystemExit("replay-arms: refusing the pair bare and %s: they differ by more than the harness:\n  %s"
                         % (harness.get("label") or "harness", "\n  ".join(lines)))


# --- Egress: an internal network whose one way out is the allowlist proxy ----------------------

def egress_names(token):
    return {"network": "%segress-%s" % (IMAGE_PREFIX, token), "proxy": "%sproxy-%s" % (IMAGE_PREFIX, token)}


def proxy_url(names):
    return "http://%s:%d" % (names["proxy"], PROXY_PORT)


PROXY_READY = "egress proxy on "
PROXY_WAIT = (50, 0.2)  # attempts, seconds between them


def egress_commands(image, names, hosts=MODEL_API_HOSTS):
    """The commands that stand the egress up, in order: an internal network (no route out), the
    proxy on it, and the proxy's second leg on the default bridge, which does route out. The
    runner waits for the proxy to report its address between the second and the third: the proxy
    binds only its address on the internal network, which it can tell apart only while that is
    the one network it is on."""
    source = PROXY_SCRIPT.read_text(encoding="utf-8")
    return [["docker", "network", "create", "--internal", names["network"]],
            ["docker", "run", "-d", "--rm", "--name", names["proxy"], "--network", names["network"]]
            + HARDENING + [image, "python3", "-c", source, str(PROXY_PORT)] + list(hosts),
            ["docker", "network", "connect", "bridge", names["proxy"]]]


def wait_for_proxy(names, launch=subprocess.run, sleep=None):
    """The address the proxy bound, once its log reports it; SystemExit if it never does."""
    import time
    attempts, pause = PROXY_WAIT
    for _ in range(attempts):
        done = launch(["docker", "logs", names["proxy"]], env=client_env(), stdout=subprocess.PIPE,
                      stderr=subprocess.PIPE, universal_newlines=True)
        text = (done.stdout or "") + (done.stderr or "")
        for line in text.splitlines():
            if line.startswith(PROXY_READY):
                return line[len(PROXY_READY):].split(" ", 1)[0]
        (sleep or time.sleep)(pause)
    raise SystemExit("replay-arms: the egress proxy %s never reported its address" % names["proxy"])


def teardown_commands(names):
    return [["docker", "rm", "--force", names["proxy"]], ["docker", "network", "rm", names["network"]]]


@contextlib.contextmanager
def egress(image, launch=subprocess.run, token=None, hosts=MODEL_API_HOSTS):
    """The egress for one replay, torn down afterwards whatever happens; yields its names and URL."""
    names = egress_names(token or "%d" % os.getpid())
    try:
        create, proxy, connect = egress_commands(image, names, hosts)
        _run(launch, create)
        _run(launch, proxy)
        bound = wait_for_proxy(names, launch)
        _run(launch, connect)
        yield dict(names, url=proxy_url(names), bound=bound)
    finally:
        for command in teardown_commands(names):
            launch(command, env=client_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                   universal_newlines=True)


def arm_env(proxy=None, stance_cost=None, selection=None):
    """The variables an arm container is given by value: `ARM_ENV`, the proxy in both spellings,
    the harness arm's stance override, and a pair arm's session-scoped `selection` (variable to
    value; `replay_pair`). The credential is not among them; see `CREDENTIAL`."""
    env = dict(ARM_ENV)
    if proxy:
        for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            env[name] = proxy
        env["NO_PROXY"] = env["no_proxy"] = NO_PROXY
    if stance_cost:
        env["HARNESS_STANCE_COST"] = stance_cost
    for key, value in sorted((selection or {}).items()):
        if value is not None:
            env[key] = value
    return env


OBSERVATION_MOUNT = "/observations"
# Claude Code's managed memory file on Linux, the first memory it loads: a file mounted here opens
# the session segment, the first message after the system prompt (`cost_bench.trial_memory`).
MANAGED_MEMORY = "/etc/claude-code/CLAUDE.md"
# Where a check container sees the scored run's saved stream-json, read-only (`check_command`).
SESSION_STREAM = "/session-stream.jsonl"
# Where a long-session checkpoint's check sees the session's totals before its segment, read-only:
# `{"total_cost_usd": n, "modelUsage": {model: usage}}` (`replay_session.session_totals`).
SESSION_BASELINE = "/session-baseline.json"
# Where the image's CLI keeps its session transcripts: the `agent` user's home in both arms. A
# long-session run mounts one empty host directory here for each session, so a later turn's fresh
# container can `--resume` the session an earlier one wrote.
AGENT_HOME = "/home/agent"
SESSION_STORE = AGENT_HOME + "/.claude/projects"
OBSERVATION_MARKER = ".model-citizen-benchmark-output"


def observation_mount(path):
    """A prepared benchmark-owned observation directory, or a refusal reason."""
    if path is None:
        return None, None
    raw = Path(path)
    if raw.is_symlink() or not raw.is_absolute() or not raw.is_dir():
        return None, "observation output is not a real absolute directory"
    resolved = raw.resolve()
    reason = host_path_reason([str(raw), str(resolved)])
    if reason:
        return None, reason
    marker = resolved / OBSERVATION_MARKER
    if not marker.is_file() or marker.read_text(encoding="utf-8") != "cost-bench\n":
        return None, "observation output was not prepared by cost-bench"
    return resolved, None


def run_command(image, workdir, argv, network, env=None, name=None, credential=True, stdin=False,
                observation_dir=None, keep=False, managed_memory=None, session_stream=None,
                session_store=None, session_baseline=None):
    """`docker run --rm` of an arm with the snapshot and optional marked observation output.

    `env` goes by value,
    the credential by name alone, and the network is the one given (the egress network for a run,
    `none` for a check). With no `workdir` nothing at all is mounted; `stdin` keeps standard input
    attached, which Docker otherwise drops, for a program sent on it. A mount or a value that
    names a host path in `host_paths` is refused. The extra output is accepted only with the marker
    `cost_bench.py` creates, so no launch can reach the host's home, profile or live checkout. `keep`
    leaves out `--rm`, so a file can be copied out of the stopped container (`copy_command`) before
    it is removed by name; it needs a `name`. `managed_memory` is a file mounted read-only as
    `MANAGED_MEMORY`, the per-trial cache nonce; `session_stream` a run's saved stream mounted
    read-only as `SESSION_STREAM`, for the check that scores it, and `session_baseline` the
    session's totals before a checkpoint's segment, read-only as `SESSION_BASELINE`;
    `session_store` one session's transcript directory mounted writable as `SESSION_STORE`, so the
    next turn can resume it."""
    sources = [str(p) for p in (workdir, managed_memory, session_stream, session_baseline, session_store)
               if p is not None]
    reason = host_path_reason(sources, env)
    if reason:
        raise SystemExit("replay-arms: refusing to launch %s: %s" % (image, reason))
    if keep and not name:
        raise SystemExit("replay-arms: a kept container needs a name to be removed by")
    command = ["docker", "run"] + ([] if keep else ["--rm"]) + (["-i"] if stdin else []) + (["--name", name] if name else []) + [
        "--network", network] + HARDENING
    if workdir is not None:
        command += ["-v", "%s:%s" % (workdir, WORKDIR), "-w", WORKDIR]
    observation, error = observation_mount(observation_dir)
    if error:
        raise SystemExit("replay-arms: refusing to launch %s: %s" % (image, error))
    if observation:
        command += ["-v", "%s:%s" % (observation, OBSERVATION_MOUNT)]
    if managed_memory is not None:
        command += ["-v", "%s:%s:ro" % (managed_memory, MANAGED_MEMORY)]
    if session_stream is not None:
        command += ["-v", "%s:%s:ro" % (session_stream, SESSION_STREAM)]
    if session_baseline is not None:
        command += ["-v", "%s:%s:ro" % (session_baseline, SESSION_BASELINE)]
    if session_store is not None:
        command += ["-v", "%s:%s" % (session_store, SESSION_STORE)]
    if credential:
        command += ["-e", CREDENTIAL]
    for key in sorted(env or {}):
        command += ["-e", "%s=%s" % (key, env[key])]
    return command + [image] + list(argv)


def check_command(image, workdir, argv, env=None, name=None, stdin=False, session_stream=None,
                  session_baseline=None):
    """A held-back check in a fresh container: no network and no credential."""
    return run_command(image, workdir, argv, "none", env, name, credential=False, stdin=stdin,
                       session_stream=session_stream, session_baseline=session_baseline)


def kill_command(name):
    return ["docker", "rm", "--force", name]


def stop_command(name):
    """Stop a kept container that timed out without removing it, so its files can still be copied."""
    return ["docker", "kill", name]


def copy_command(name, path, dest):
    """Copy one file out of a stopped, kept container: no mount, and nothing is written into it."""
    return ["docker", "cp", "%s:%s" % (name, path), str(dest)]


# --- The snapshot as the image's user sees it ---------------------------------------------------

# Run in a fresh container of the arm before anything is measured: the image's user must be able
# to write the mounted snapshot and git must treat it as a repository it may use. The image
# trusts WORKDIR for git (`safe.directory`); this proves both on the mount that will be used.
WORKDIR_PROBE = ("touch {w}/.model-citizen-write-probe && rm {w}/.model-citizen-write-probe"
                 " && git -C {w} rev-parse --verify HEAD >/dev/null").format(w=WORKDIR)


def open_for_image(root):
    """Make a snapshot writable by any user, so the image's `agent` user can write it whatever
    user id created it on this machine. It is a throwaway tree made for one run, so nothing else
    can be affected, and no root is needed: the invoking user owns every file in it."""
    root = Path(root)
    for path in [root] + sorted(root.rglob("*")):
        if path.is_symlink():
            continue
        mode = path.stat().st_mode
        extra = 0o777 if path.is_dir() else (0o666 | (0o111 if mode & 0o100 else 0))
        os.chmod(str(path), mode | extra)
    return root


def workdir_probe_command(image, workdir, name=None):
    return check_command(image, workdir, ["sh", "-c", WORKDIR_PROBE], None, name)


def probe_workdir(record, workdir, launch=subprocess.run, name=None):
    """SystemExit naming the arm when its image's user cannot write `workdir` or git refuses it.
    The run fails closed here rather than measuring an arm that cannot do the task."""
    done = launch(workdir_probe_command(record["image"], workdir, name), env=client_env(),
                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                  timeout=MANIFEST_TIMEOUT)
    if done.returncode:
        reason = ((done.stderr or "") + (done.stdout or "")).strip().splitlines()
        raise SystemExit("replay-arms: refusing the %s arm: its user cannot write the mounted snapshot "
                         "or git will not use it (exit %d): %s"
                         % (record.get("label") or record.get("arm"), done.returncode,
                            reason[-1] if reason else "no message"))


# --- The egress rule, proved from inside an arm container without a model call -----------------

PROBE_ALLOWED = "https://%s/" % MODEL_API_HOSTS[0]
PROBE_REFUSED = "https://example.com/"


def probe_commands(image, names):
    """Three requests from a fresh arm container on the egress network, none carrying a
    credential: the model API through the proxy (any HTTP status passes), another host through
    the proxy (must be refused), and the model API with the proxy bypassed (must find no route)."""
    env = arm_env(proxy_url(names))
    curl = ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "20"]
    return [("model API through the proxy", True,
             run_command(image, None, curl + [PROBE_ALLOWED], names["network"], env, credential=False)),
            ("another host through the proxy", False,
             run_command(image, None, curl + [PROBE_REFUSED], names["network"], env, credential=False)),
            ("model API with the proxy bypassed", False,
             run_command(image, None, curl + ["--noproxy", "*", PROBE_ALLOWED], names["network"],
                         env, credential=False))]


def egress_probe(image, launch=subprocess.run, token=None):
    """(lines, passed). Stand the egress up, run the three probes, tear it down."""
    lines, passed = [], True
    with egress(image, launch, token or "probe-%d" % os.getpid()) as net:
        for what, should_reach, command in probe_commands(image, net):
            done = launch(command, env=client_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=120)
            status = (done.stdout or "").strip()
            reached = done.returncode == 0 and re.fullmatch(r"[1-5]\d\d", status) is not None
            ok = reached == should_reach
            passed = passed and ok
            detail = ("HTTP %s" % status) if reached else "refused: exit %d, %s" % (
                done.returncode, ((done.stderr or "").strip().splitlines() or ["no message"])[-1])
            lines.append("%s %s: %s" % ("PASS" if ok else "FAIL", what, detail))
    return lines, passed

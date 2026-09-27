"""Replay arms as fresh containers: declare an arm, build its image, list what it holds, run it.

An arm is a Docker image built from pinned inputs and nothing else. `bare` is the Linux
qualification image's base and Claude Code; `harness` is that plus this repository at one
commit, synced for the image's agent user. Nothing from the machine running the replay reaches
either: no home directory, profile, environment, hook or setting.

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
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import experiment_protocol  # noqa: E402  the evidence labels the admission check reads

ROOT = Path(__file__).resolve().parents[1]
QUALIFICATION_DOCKERFILE = ROOT / "scripts" / "linux-target.Dockerfile"
ARM_DOCKERFILE = ROOT / "scripts" / "replay-arm.Dockerfile"
MANIFEST_SCRIPT = ROOT / "scripts" / "arm_manifest.py"
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


def declaration(arm, inputs, harness=None, claude_code_version=None):
    """What one arm is built from, as a dict: every input that could change what it holds.

    `harness` is `{ref, commit}` for the harness arm and None for the bare one; the commit is the
    full sha the ref resolved to, so a moved tag is a different declaration. The Dockerfile and
    the manifest lister are inputs too, by content."""
    if arm not in ARMS:
        raise SystemExit("replay-arms: unknown arm %r; the arms are %s" % (arm, ", ".join(ARMS)))
    if (arm == "harness") != bool(harness):
        raise SystemExit("replay-arms: the harness arm needs a ref and commit, and only it")
    if harness and not re.fullmatch(r"[0-9a-f]{40}", harness.get("commit") or ""):
        raise SystemExit("replay-arms: harness commit %r is not a full sha" % harness.get("commit"))
    version = claude_code_version or inputs["claude_code_version"]
    components = [{"name": "base-image", "version": inputs["base_image"]},
                  {"name": "@anthropic-ai/claude-code", "version": version}]
    if harness:
        components.append({"name": "model-citizen", "version": harness["ref"], "commit": harness["commit"]})
    return {"schema": SCHEMA, "arm": arm, "base_image": inputs["base_image"],
            "claude_code_version": version,
            "harness": dict(ref=harness["ref"], commit=harness["commit"]) if harness else None,
            "components": components, "dockerfile_sha256": file_sha(ARM_DOCKERFILE),
            "manifest_script_sha256": file_sha(MANIFEST_SCRIPT)}


def label(decl):
    """The arm as a row and a person name it: `bare`, or `harness@<ref>`."""
    return decl["arm"] if not decl["harness"] else "harness@%s" % decl["harness"]["ref"]


def image_name(decl, tag=None):
    """`model-citizen-arm-<arm>:<tag>`, the tag defaulting to the declaration's digest, so one set
    of inputs always names one image and a changed input a new one."""
    return "%s%s:%s" % (IMAGE_PREFIX, decl["arm"], tag or digest(decl)[:12])


def build_context(decl, parent, snapshot, repo=ROOT):
    """The directory one build sends to the daemon: empty for the bare arm, and a `harness/` clone
    of the declared commit for the harness arm, made by `snapshot(repo, commit, dest)`."""
    context = Path(parent) / "context"
    context.mkdir(parents=True)
    if decl["harness"]:
        snapshot(repo, decl["harness"]["commit"], context / "harness")
    return context


def build_command(decl, context, image, no_cache=False):
    command = ["docker", "build", "--quiet", "-f", str(ARM_DOCKERFILE), "--target", decl["arm"],
               "--build-arg", "BASE_IMAGE=" + decl["base_image"],
               "--build-arg", "CLAUDE_CODE_VERSION=" + decl["claude_code_version"],
               "--label", "org.model-citizen.arm.declaration=" + digest(decl)]
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


# --- The seam where an arm can be refused before it launches -----------------------------------

# Callables `check(record) -> reason or None`, run by `admit` in order. The replay calls `admit`
# once per arm, after its build and before the first launch of that arm; a check that refuses
# stops the replay before anything is spent. The protocol's refusals (docs/evidence-standard.md)
# are the checks below.
ADMISSION_CHECKS = []


def _has_its_records(record):
    for key in ("image", "image_id", "declaration_sha256", "manifest_sha256"):
        if not record.get(key):
            return "no %s recorded" % key
    return None


# Where the harness arm's declared component is installed, and the two files its sync writes
# into the profile rather than linking; every other configuration entry is a link into it.
HARNESS_ROOT = "/opt/model-citizen"
HARNESS_WRITES = ("home:.claude/settings.json", "home:.claude/CLAUDE.personal.md")
# Declared components that are not global npm packages; every other one is, as `name@version`.
NOT_PACKAGES = ("base-image", "model-citizen")


def _matches_its_declaration(record):
    """The manifest holds exactly the declared Claude Code, agent clients and harness commit."""
    decl, manifest = record.get("declaration") or {}, record.get("manifest") or {}
    if manifest.get("claude_code_version") != decl.get("claude_code_version"):
        return "the manifest holds Claude Code %r, the declaration %r" % (
            manifest.get("claude_code_version"), decl.get("claude_code_version"))
    declared = sorted("%s@%s" % (c["name"], c["version"]) for c in decl.get("components") or []
                      if c["name"] not in NOT_PACKAGES)
    held = sorted(manifest.get("cli_packages") or [])
    if held != declared:
        return "the manifest's agent clients %s differ from the declared %s" % (held, declared)
    commit = (decl.get("harness") or {}).get("commit")
    if manifest.get("harness_commit") != commit:
        return "the manifest's harness commit %r differs from the declared %r" % (manifest.get("harness_commit"), commit)
    if ("harness" in (manifest.get("roots") or {})) != bool(commit):
        return "the manifest %s a harness checkout the declaration %s" % (
            ("holds", "does not name") if not commit else ("lacks", "names"))
    return None


def _inside(path, root):
    return path == root or path.startswith(root.rstrip("/") + "/")


def _configuration_is_declared(record):
    """Every setting, hook, rule, skill, agent, plugin and instruction file in the manifest comes
    from a declared component: the bare arm has none, and the harness arm's are links into its
    declared checkout or the files its sync writes. Anything else was inherited."""
    decl, manifest = record.get("declaration") or {}, record.get("manifest") or {}
    harness = bool(decl.get("harness"))
    entries = {e.get("path"): e for e in manifest.get("entries") or []}
    for kind, paths in sorted((manifest.get("summary") or {}).items()):
        for path in paths:
            entry = entries.get(path) or {}
            if harness and (entry.get("kind") == "dir" or path in HARNESS_WRITES
                            or (entry.get("kind") == "link" and _inside(entry.get("target", ""), HARNESS_ROOT))):
                continue
            return "%s entry %s is not in the declaration" % (kind, path)
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
    """No recorded input names a host path: the declaration, and the manifest's roots and link
    targets. Mounts and environment are refused at launch by `run_command`."""
    manifest = record.get("manifest") or {}
    strings = _strings(record.get("declaration") or {}) + _strings(manifest.get("roots") or {})
    strings += [e["target"] for e in manifest.get("entries") or [] if e.get("kind") == "link" and e.get("target")]
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


ADMISSION_CHECKS.extend([_has_its_records, _matches_its_declaration, _configuration_is_declared, _no_host_path,
                         _is_preregistered_or_exploratory])


def admit(record, checks=None):
    """SystemExit naming the first reason the arm may not run; None when every check admits it."""
    for check in ADMISSION_CHECKS if checks is None else checks:
        reason = check(record)
        if reason:
            raise SystemExit("replay-arms: refusing the %s arm: %s" % (record.get("label") or "?", reason))


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


def arm_env(proxy=None, stance_cost=None):
    """The variables an arm container is given by value: `ARM_ENV`, the proxy in both spellings,
    and the harness arm's stance override. The credential is not among them; see `CREDENTIAL`."""
    env = dict(ARM_ENV)
    if proxy:
        for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            env[name] = proxy
        env["NO_PROXY"] = env["no_proxy"] = NO_PROXY
    if stance_cost:
        env["HARNESS_STANCE_COST"] = stance_cost
    return env


def run_command(image, workdir, argv, network, env=None, name=None, credential=True, stdin=False):
    """`docker run --rm` of an arm: the snapshot at WORKDIR is the only mount, `env` goes by value,
    the credential by name alone, and the network is the one given (the egress network for a run,
    `none` for a check). With no `workdir` nothing at all is mounted; `stdin` keeps standard input
    attached, which Docker otherwise drops, for a program sent on it. A mount or a value that
    names a host path in `host_paths` is refused, so no launch can reach the host's home, profile
    or live checkout."""
    reason = host_path_reason([str(workdir)] if workdir is not None else [], env)
    if reason:
        raise SystemExit("replay-arms: refusing to launch %s: %s" % (image, reason))
    command = ["docker", "run", "--rm"] + (["-i"] if stdin else []) + (["--name", name] if name else []) + [
        "--network", network] + HARDENING
    if workdir is not None:
        command += ["-v", "%s:%s" % (workdir, WORKDIR), "-w", WORKDIR]
    if credential:
        command += ["-e", CREDENTIAL]
    for key in sorted(env or {}):
        command += ["-e", "%s=%s" % (key, env[key])]
    return command + [image] + list(argv)


def check_command(image, workdir, argv, env=None, name=None, stdin=False):
    """A held-back check in a fresh container: no network and no credential."""
    return run_command(image, workdir, argv, "none", env, name, credential=False, stdin=stdin)


def kill_command(name):
    return ["docker", "rm", "--force", name]


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

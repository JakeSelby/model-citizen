#!/usr/bin/env python3
"""The replay's evaluator pack: tasks, workspaces, held-back checks and reference solutions kept
in a versioned git repository outside this one.

The harness arm installs a checkout of this repository, so any task whose check or answer lives
here can be copied rather than solved (#1019). A pack keeps both out of it by construction:

- **Pinned.** `open_pack` reads one commit of the pack's repository through `git archive`, never
  its working tree, and its digest is the sha256 of every archived path and its bytes. A run that
  names `--pack-digest` refuses any other content, and every row carries the pack's name,
  version, commit and digest.
- **Outside.** A pack inside this repository, or one containing it, is refused.
- **Isolated scoring.** An arm sees only a workspace copied into a fresh one-commit git
  repository (`materialize`). The check reaches the scorer on stdin, in a fresh container with no
  network that mounts only the agent's tree (`cost_bench.score`); no arm container holds a byte
  of the pack beyond its workspace. The reference solution runs the same way under
  `--verify-tasks`; nothing from a pack is imported or executed on this machine.
- **Read, never followed.** An archive holding a link, a special file, an absolute path or a `..`
  component is refused before anything is extracted, and loading reads no path through a link.
- **Contamination control.** `contamination_errors` refuses a task when the harness commit under
  test, or any commit in its history, holds the exact bytes of the task's check or solution, or
  any file carrying the pack's canary.

Layout, long tasks and versioning: docs/benchmarks.md and the pack's own README. Standard library
only; nothing here calls a model.
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 1
PACK_FILE = "pack.json"
TASK_FILE = "task.json"
CHECK_FILE = "check.py"
SOLUTION_FILE = "solution.py"
TIERS = ("production", "micro")
GATE_GREEN = r"^OK\b"
# A workspace name or task id becomes one path component under the pack's root.
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# A fixed author and date, so one workspace always becomes the same commit.
WORKSPACE_GIT_ENV = {"GIT_AUTHOR_NAME": "workspace", "GIT_AUTHOR_EMAIL": "workspace@invalid",
                     "GIT_COMMITTER_NAME": "workspace", "GIT_COMMITTER_EMAIL": "workspace@invalid",
                     "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000",
                     "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000"}
KEPT_ENV = ("HOME", "PATH", "TMPDIR", "LANG")
# Upstream material a workspace may name to be fetched at a pinned commit (`vendor_errors`): only
# licences that permit commercial use, modification and redistribution with no further terms.
VENDOR_LICENCES = ("MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
_VENDOR_CACHE = {}


class PackError(SystemExit):
    """A pack that cannot be used; raised before anything is built or spent."""

    def __init__(self, message):
        super().__init__("replay-pack: " + message)


def _env(extra=None):
    env = dict((k, os.environ[k]) for k in KEPT_ENV if k in os.environ)
    env.update(extra or {})
    return env


def _git(repo, *args, **kwargs):
    return subprocess.run(["git", "-C", str(repo)] + list(args), env=_env(kwargs.pop("env", None)),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)


def _inside(child, parent):
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def _contained(path, base):
    """Whether `path` lies inside `base` with no symbolic link on the way to it or anywhere under
    it, so reading it never leaves `base`."""
    path, base = Path(path), Path(base)
    try:
        parts = path.relative_to(base).parts
    except ValueError:
        return False
    current = base
    for part in parts:
        current = current / part
        if part == ".." or current.is_symlink():
            return False
    if not _inside(path, base):
        return False
    return not (path.is_dir() and any(p.is_symlink() for p in path.rglob("*")))


def tree_digest(root):
    """sha256 over every file under `root`: its relative path, a NUL, the sha256 of its bytes."""
    root = Path(root)
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() or p.is_symlink()):
        data = os.readlink(str(path)).encode() if path.is_symlink() else path.read_bytes()
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0"
                      + hashlib.sha256(data).hexdigest().encode() + b"\n")
    return digest.hexdigest()


def open_pack(source, ref="HEAD", expect_digest=None, harness_root=ROOT, tmp=None):
    """The pack at `ref` of the git repository `source`, extracted to a private directory.

    Refused before anything else: a source inside `harness_root` or containing it, a source that
    is not a git repository, a ref that does not resolve, a malformed `pack.json` and, when
    `expect_digest` is given, any other digest. `close_pack` removes the extraction."""
    source = Path(source).expanduser()
    if _inside(source, harness_root) or _inside(harness_root, source):
        raise PackError("the pack at %s overlaps the harness checkout %s; a pack lives outside it"
                        % (source, harness_root))
    if not (source / ".git").exists():
        raise PackError("%s is not a git repository; a pack is read from a pinned commit" % source)
    done = _git(source, "rev-parse", "--verify", "--quiet", "%s^{commit}" % ref)
    commit = done.stdout.decode().strip()
    if done.returncode or len(commit) != 40:
        raise PackError("ref %s does not resolve to a commit in %s" % (ref, source))
    archive = _git(source, "archive", "--format=tar", commit)
    if archive.returncode:
        raise PackError("git archive failed in %s: %s" % (source, archive.stderr.decode().strip()))
    root = Path(tempfile.mkdtemp(prefix="model-citizen-pack-", dir=tmp))
    if _inside(root, harness_root):
        shutil.rmtree(str(root), ignore_errors=True)
        raise PackError("the extraction directory %s is inside the harness checkout" % root)
    try:
        extract(archive.stdout, root)
        digest = tree_digest(root)
        if expect_digest and expect_digest != digest:
            raise PackError("pack digest is %s, not the %s named; the pack changed" % (digest, expect_digest))
        document = json.loads((root / PACK_FILE).read_text(encoding="utf-8"))
        errors = document_errors(document)
        if errors:
            raise PackError("invalid %s:\n  %s" % (PACK_FILE, "\n  ".join(errors)))
    except BaseException:
        shutil.rmtree(str(root), ignore_errors=True)
        raise
    return {"source": str(source), "ref": ref, "commit": commit, "digest": digest, "root": root,
            "document": document, "name": document["name"], "version": document["version"]}


def unsafe_members(tar):
    """Why each member of `tar` may not be extracted: anything but a regular file or a directory
    (a symbolic or hard link, a device, a FIFO), an absolute name, or a `..` component."""
    errors = []
    for member in tar.getmembers():
        if member.name.startswith("/") or ".." in Path(member.name).parts:
            errors.append("an unsafe path: %s" % member.name)
        elif member.issym() or member.islnk():
            errors.append("a link: %s -> %s" % (member.name, member.linkname))
        elif not (member.isfile() or member.isdir()):
            errors.append("a special file: %s" % member.name)
    return errors


def extract(data, root):
    """Extract the tar bytes `data` into `root`, refusing the whole archive before writing anything
    when any member is unsafe (`unsafe_members`); on every Python, so the unfiltered extraction of
    Pythons before 3.12 never meets a link."""
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        errors = unsafe_members(tar)
        if errors:
            raise PackError("the archive holds %s" % "; ".join(errors[:5]))
        try:
            tar.extractall(str(root), filter="data")
        except TypeError:  # Python before 3.12 has no extraction filter; every member is checked above
            tar.extractall(str(root))


def close_pack(pack):
    if pack:
        shutil.rmtree(str(pack["root"]), ignore_errors=True)


def identity(pack):
    """What every row of a pack run records about the pack it ran."""
    return {"pack": pack["name"], "pack_version": pack["version"], "pack_commit": pack["commit"],
            "pack_digest": pack["digest"]}


def document_errors(document):
    errors = []
    if document.get("schema_version") != SCHEMA:
        errors.append("schema_version is not %d" % SCHEMA)
    for key in ("name", "version", "canary"):
        if not isinstance(document.get(key), str) or not document[key]:
            errors.append("%s is not a non-empty string" % key)
    if isinstance(document.get("canary"), str) and len(document["canary"]) < 24:
        errors.append("canary is too short to be unique")
    if not isinstance(document.get("break_even_calls"), (int, float)) or isinstance(document.get("break_even_calls"), bool):
        errors.append("break_even_calls is not a number")
    workspaces = document.get("workspaces")
    if not isinstance(workspaces, dict) or not workspaces:
        errors.append("workspaces is not a non-empty object")
    else:
        for name, spec in sorted(workspaces.items()):
            if not NAME.match(name):
                errors.append("workspace name %r is not a plain directory name" % name)
            gate = (spec or {}).get("gate")
            if not isinstance(gate, list) or not gate or not all(
                    isinstance(c, list) and c and all(isinstance(a, str) for a in c) for c in gate):
                errors.append("workspace %s has no gate of argv lists" % name)
            errors.extend(vendor_errors(name, (spec or {}).get("vendor", [])))
    sets = document.get("sets")
    if not isinstance(sets, dict) or not sets:
        errors.append("sets is not a non-empty object")
    else:
        for name, spec in sorted(sets.items()):
            ids = (spec or {}).get("tasks")
            if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids):
                errors.append("set %s has no list of unique task ids" % name)
            elif not all(isinstance(i, str) and NAME.match(i) for i in ids):
                errors.append("set %s names a task id that is not a plain directory name" % name)
            tier = set_tier(name, spec or {})
            if tier not in TIERS:
                errors.append("set %s names no tier of %s" % (name, ", ".join(TIERS)))
            elif tier == "micro" and (not isinstance((spec or {}).get("model"), str) or not spec["model"]):
                errors.append("set %s is a micro set and names no model to run on" % name)
    return errors


def _relative(path):
    return isinstance(path, str) and path and not path.startswith("/") and "\\" not in path \
        and all(part not in ("", ".", "..") for part in path.split("/"))


def vendor_errors(workspace, entries):
    """Why a workspace's `vendor` list cannot be used, or [].

    Each entry fetches upstream files at one pinned commit into the workspace as it is
    materialized, so a pack can carry a third-party skill without redistributing it: `name`,
    `source` (a git URL or path), `commit` (a full sha), `license` (one of `VENDOR_LICENCES`),
    `paths` (`[upstream path, workspace path]` pairs, both relative and plain) and `digest`,
    the `tree_digest` of the placed files, so the pack's own digest pins the bytes too."""
    errors, where = [], "workspace %s vendor" % workspace
    if not isinstance(entries, list):
        return ["%s is not a list" % where]
    targets = []
    for index, entry in enumerate(entries):
        at = "%s %d" % (where, index)
        if not isinstance(entry, dict):
            errors.append("%s is not an object" % at)
            continue
        for key in ("name", "source"):
            if not isinstance(entry.get(key), str) or not entry[key]:
                errors.append("%s: %s is not a non-empty string" % (at, key))
        if not isinstance(entry.get("commit"), str) or not COMMIT.match(entry["commit"]):
            errors.append("%s: commit is not a full 40-character sha" % at)
        if entry.get("license") not in VENDOR_LICENCES:
            errors.append("%s: license %r is not one of %s" % (at, entry.get("license"), ", ".join(VENDOR_LICENCES)))
        if not isinstance(entry.get("digest"), str) or not DIGEST.match(entry["digest"]):
            errors.append("%s: digest is not a sha256 hex digest" % at)
        paths = entry.get("paths")
        if not isinstance(paths, list) or not paths or not all(
                isinstance(p, list) and len(p) == 2 and _relative(p[0]) and _relative(p[1]) for p in paths):
            errors.append("%s: paths is not a list of [upstream, workspace] relative path pairs" % at)
            continue
        targets.extend(p[1] for p in paths)
    for first in targets:
        if any(other != first and other.startswith(first + "/") for other in targets) or targets.count(first) > 1:
            errors.append("%s: workspace path %s is named twice or nests another" % (where, first))
    return errors


def _vendor_archive(entry, tmp=None):
    """The tar bytes of the entry's upstream paths at its commit, fetched once per process."""
    key = (entry["source"], entry["commit"], tuple(p[0] for p in entry["paths"]))
    if key not in _VENDOR_CACHE:
        repo = Path(tempfile.mkdtemp(prefix="model-citizen-vendor-", dir=tmp))
        try:
            for args in (("init", "-q"), ("fetch", "-q", "--depth", "1", entry["source"], entry["commit"])):
                done = _git(repo, *args)
                if done.returncode:
                    raise PackError("fetching %s at %s failed: %s"
                                    % (entry["name"], entry["commit"], done.stderr.decode().strip()))
            done = _git(repo, "archive", "--format=tar", entry["commit"], "--", *key[2])
            if done.returncode:
                raise PackError("%s at %s has no %s: %s" % (entry["name"], entry["commit"],
                                                            ", ".join(key[2]), done.stderr.decode().strip()))
            _VENDOR_CACHE[key] = done.stdout
        finally:
            shutil.rmtree(str(repo), ignore_errors=True)
    return _VENDOR_CACHE[key]


def place_vendored(entries, dest, tmp=None):
    """Fetch each vendor entry and place its paths under `dest`, refusing before writing anything
    into `dest` when a placed file would replace a workspace file or the placed files' digest is
    not the one the pack pinned. Returns `{name: digest}`."""
    placed = {}
    for entry in entries or ():
        root = Path(tempfile.mkdtemp(prefix="model-citizen-vendor-", dir=tmp))
        try:
            upstream, staged = root / "upstream", root / "staged"
            upstream.mkdir()
            staged.mkdir()
            extract(_vendor_archive(entry, tmp), upstream)
            for source, target in entry["paths"]:
                origin, goal = upstream / source, staged / target
                if not origin.exists():
                    raise PackError("%s at %s has no %s" % (entry["name"], entry["commit"], source))
                goal.parent.mkdir(parents=True, exist_ok=True)
                (shutil.copytree if origin.is_dir() else shutil.copyfile)(str(origin), str(goal))
            digest = tree_digest(staged)
            if digest != entry["digest"]:
                raise PackError("%s at %s placed digest %s, not the %s the pack pins"
                                % (entry["name"], entry["commit"], digest, entry["digest"]))
            for path in sorted(p for p in staged.rglob("*") if p.is_file()):
                if (Path(dest) / path.relative_to(staged)).exists():
                    raise PackError("%s would replace the workspace's %s"
                                    % (entry["name"], path.relative_to(staged).as_posix()))
            shutil.copytree(str(staged), str(dest), dirs_exist_ok=True)
            placed[entry["name"]] = digest
        finally:
            shutil.rmtree(str(root), ignore_errors=True)
    return placed


def set_tier(name, spec):
    """A set's tier: its `tier`, or its name when the name is a tier."""
    return spec.get("tier") or (name if name in TIERS else None)


def task_errors(spec, task_dir, document):
    """Why one task directory cannot run, or []."""
    where = "task %r" % spec.get("id")
    errors = []
    if spec.get("id") != task_dir.name:
        errors.append("%s is in directory %s" % (where, task_dir.name))
    if spec.get("workspace") not in (document.get("workspaces") or {}):
        errors.append("%s names no declared workspace" % where)
    elif not (task_dir.parent.parent / "workspaces" / spec["workspace"]).is_dir():
        errors.append("%s: workspace %s is missing" % (where, spec["workspace"]))
    prompt = spec.get("prompt")
    if not (isinstance(prompt, str) and prompt) and not (isinstance(prompt, list) and prompt
                                                         and all(isinstance(p, str) for p in prompt)):
        errors.append("%s has no prompt" % where)
    turns = spec.get("max_turns")
    if not isinstance(turns, int) or isinstance(turns, bool) or turns < 1:
        errors.append("%s: max_turns is not a positive integer" % where)
    if not isinstance(spec.get("long"), bool):
        errors.append("%s: long must be true or false" % where)
    calls = spec.get("expected_absorbed_calls")
    if not isinstance(calls, (int, float)) or isinstance(calls, bool) or calls < 0:
        errors.append("%s: expected_absorbed_calls is not a non-negative number" % where)
    elif isinstance(spec.get("long"), bool) and spec["long"] != (calls > document["break_even_calls"]):
        errors.append("%s: long is %s but expected_absorbed_calls %s is %s break-even %s"
                      % (where, str(spec["long"]).lower(), calls,
                         "above" if calls > document["break_even_calls"] else "at or below",
                         document["break_even_calls"]))
    if not isinstance(spec.get("first_wave", False), bool):
        errors.append("%s: first_wave must be true or false" % where)
    skills = spec.get("requires_skills", [])
    if not isinstance(skills, list) or not all(isinstance(n, str) and NAME.match(n) for n in skills):
        errors.append("%s: requires_skills is not a list of skill names" % where)
    for name in (CHECK_FILE, SOLUTION_FILE):
        path = task_dir / name
        if not path.is_file():
            errors.append("%s has no %s" % (where, name))
        elif document["canary"] not in path.read_text(encoding="utf-8"):
            errors.append("%s: %s does not carry the pack's canary" % (where, name))
    return errors


def load_set(pack, set_name, tier):
    """`(tasks, document)` for one set of the pack, which must be of `tier`: the runner's task
    dicts, and a manifest document shaped as `replay_micro.check_manifest` reads it."""
    document = pack["document"]
    spec = (document.get("sets") or {}).get(set_name)
    if spec is None:
        raise PackError("pack %s %s has no set %s; it has %s" % (pack["name"], pack["version"], set_name,
                                                                ", ".join(sorted(document["sets"]))))
    if set_tier(set_name, spec) != tier:
        raise PackError("set %s is a %s set, not %s; pass --tier %s"
                        % (set_name, set_tier(set_name, spec), tier, set_tier(set_name, spec)))
    root = Path(pack["root"])
    tasks, errors = [], []
    for task_id in spec["tasks"]:
        task_dir = root / "tasks" / task_id
        if not _contained(task_dir, root):
            errors.append("task %r is a link or holds one; nothing is read through a link" % task_id)
            continue
        if not (task_dir / TASK_FILE).is_file():
            errors.append("task %r has no %s" % (task_id, TASK_FILE))
            continue
        task_spec = json.loads((task_dir / TASK_FILE).read_text(encoding="utf-8"))
        problems = task_errors(task_spec, task_dir, document)
        workspace = root / "workspaces" / str(task_spec.get("workspace"))
        if not problems and not _contained(workspace, root):
            problems.append("task %r: its workspace is a link or holds one; nothing is read through a link"
                            % task_id)
        if not problems and any(document["canary"] in p.read_text(encoding="utf-8", errors="replace")
                                for p in workspace.rglob("*") if p.is_file()):
            problems.append("task %r: its workspace carries the canary" % task_id)
        errors.extend(problems)
        if problems:
            continue
        task = {"id": task_id, "kind": "pack", "source": task_spec.get("source", ""),
                "parent_sha": None, "good_sha": None, "prompt": task_spec["prompt"],
                "max_turns": task_spec["max_turns"], "long": task_spec["long"],
                "expected_absorbed_calls": task_spec["expected_absorbed_calls"],
                "tests": {"oracle": task_id},
                "pack": {"task_dir": str(task_dir), "workspace": str(workspace),
                         "gate": document["workspaces"][task_spec["workspace"]]["gate"],
                         "canary": document["canary"], "name": pack["name"], "source": pack["source"]}}
        if "mechanism" in task_spec:
            task["mechanism"] = task_spec["mechanism"]
        for key in ("first_wave", "requires_skills"):
            if key in task_spec:
                task[key] = task_spec[key]
        vendor = document["workspaces"][task_spec["workspace"]].get("vendor")
        if vendor:
            task["pack"]["vendor"] = vendor
        tasks.append(task)
    if errors:
        raise PackError("pack %s %s, set %s:\n  %s" % (pack["name"], pack["version"], set_name, "\n  ".join(errors)))
    manifest = {"tier": tier, "set": set_name, "tasks": tasks}
    if "model" in spec:
        manifest["model"] = spec["model"]
    return tasks, manifest


def is_pack(task):
    return task.get("kind") == "pack"


def materialize(task, dest):
    """The task's workspace as a fresh git repository at `dest`, one commit on `main` with a
    fixed author and date; no file of the task directory, its check or its solution, is copied."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(task["pack"]["workspace"], str(dest), symlinks=True)
    place_vendored(task["pack"].get("vendor"), dest)
    env = dict(WORKSPACE_GIT_ENV)
    for args in (("init", "-q"), ("symbolic-ref", "HEAD", "refs/heads/main"), ("add", "-A"),
                 ("-c", "commit.gpgsign=false", "commit", "-q", "-m", "workspace")):
        done = _git(dest, *args, env=env)
        if done.returncode:
            raise RuntimeError("git %s failed in a workspace for %s: %s"
                               % (args[0], task["id"], done.stderr.decode().strip()))
    return dest


def check_source(task):
    """The held-back check, as the scorer sends it on stdin."""
    return (Path(task["pack"]["task_dir"]) / CHECK_FILE).read_text(encoding="utf-8")


def solution_source(task):
    """The reference solution, as `--verify-tasks` sends it on stdin to the scorer's isolated
    container (`cost_bench.solve_in_container`). Pack code is never imported or run on this
    machine: a pack is an outside repository nobody here reviewed."""
    return (Path(task["pack"]["task_dir"]) / SOLUTION_FILE).read_text(encoding="utf-8")


def git_blob_id(data):
    """The id git gives a blob of these bytes, so its presence anywhere in history is one lookup."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _canary_hits(checkout, canary, cache):
    key = (str(checkout), canary)
    if key not in cache:
        tree = _git(checkout, "grep", "-l", "-F", "-e", canary, "HEAD")
        history = _git(checkout, "log", "--all", "--format=%H", "-S", canary)
        if tree.returncode not in (0, 1) or history.returncode:
            raise RuntimeError("searching %s for the pack's canary failed" % checkout)
        cache[key] = ([line.split(":", 1)[-1] for line in tree.stdout.decode().splitlines()],
                      history.stdout.decode().split())
    return cache[key]


def contamination_errors(task, checkout, cache=None):
    """Why the harness commit checked out at `checkout`, a snapshot with its history, might hold
    this task's answer, or [] when it holds neither the check's nor the solution's bytes in any
    commit and no file carrying the pack's canary."""
    cache = {} if cache is None else cache
    errors = []
    task_dir = Path(task["pack"]["task_dir"])
    if _inside(task["pack"]["source"], checkout) or _inside(task_dir, checkout):
        errors.append("%s: the pack sits inside the installed checkout" % task["id"])
    for name in (CHECK_FILE, SOLUTION_FILE):
        blob = git_blob_id((task_dir / name).read_bytes())
        if _git(checkout, "cat-file", "-e", blob).returncode == 0:
            errors.append("%s: the installed checkout's history holds the exact bytes of its %s"
                          % (task["id"], name))
    files, commits = _canary_hits(checkout, task["pack"]["canary"], cache)
    if files:
        errors.append("%s: the installed checkout carries the pack's canary in %s"
                      % (task["id"], ", ".join(sorted(files)[:3])))
    if commits:
        errors.append("%s: the installed checkout's history carries the pack's canary (commit %s)"
                      % (task["id"], commits[0][:12]))
    return errors


def preflight_prompt(task):
    """The pack's own gate for the preflight to run, in the first task's workspace."""
    return "Run exactly this and reply with its output: `%s`" % " ".join(task["pack"]["gate"][0])

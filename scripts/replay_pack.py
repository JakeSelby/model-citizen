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
  of the pack beyond its workspace.
- **Contamination control.** `contamination_errors` refuses a task when the harness commit under
  test, or any commit in its history, holds the exact bytes of the task's check or solution, or
  any file carrying the pack's canary.

Layout, long tasks and versioning: docs/benchmarks.md and the pack's own README. Standard library
only; nothing here calls a model.
"""
import hashlib
import importlib.util
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
# A fixed author and date, so one workspace always becomes the same commit.
WORKSPACE_GIT_ENV = {"GIT_AUTHOR_NAME": "workspace", "GIT_AUTHOR_EMAIL": "workspace@invalid",
                     "GIT_COMMITTER_NAME": "workspace", "GIT_COMMITTER_EMAIL": "workspace@invalid",
                     "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000",
                     "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000"}
KEPT_ENV = ("HOME", "PATH", "TMPDIR", "LANG")


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
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
            for member in tar.getmembers():
                if member.name.startswith("/") or ".." in Path(member.name).parts:
                    raise PackError("the archive holds an unsafe path: %s" % member.name)
            try:
                tar.extractall(str(root), filter="data")
            except TypeError:  # Python before 3.12 has no extraction filter; paths are checked above
                tar.extractall(str(root))
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
            gate = (spec or {}).get("gate")
            if not isinstance(gate, list) or not gate or not all(
                    isinstance(c, list) and c and all(isinstance(a, str) for a in c) for c in gate):
                errors.append("workspace %s has no gate of argv lists" % name)
    sets = document.get("sets")
    if not isinstance(sets, dict) or not sets:
        errors.append("sets is not a non-empty object")
    else:
        for name, spec in sorted(sets.items()):
            ids = (spec or {}).get("tasks")
            if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids):
                errors.append("set %s has no list of unique task ids" % name)
            tier = set_tier(name, spec or {})
            if tier not in TIERS:
                errors.append("set %s names no tier of %s" % (name, ", ".join(TIERS)))
            elif tier == "micro" and (not isinstance((spec or {}).get("model"), str) or not spec["model"]):
                errors.append("set %s is a micro set and names no model to run on" % name)
    return errors


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
        if not (task_dir / TASK_FILE).is_file():
            errors.append("task %r has no %s" % (task_id, TASK_FILE))
            continue
        task_spec = json.loads((task_dir / TASK_FILE).read_text(encoding="utf-8"))
        problems = task_errors(task_spec, task_dir, document)
        workspace = root / "workspaces" / str(task_spec.get("workspace"))
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


def _module(task, name):
    path = Path(task["pack"]["task_dir"]) / name
    spec = importlib.util.spec_from_file_location("pack_%s_%s" % (re.sub(r"\W", "_", task["id"]), path.stem),
                                                  str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def apply_solution(task, root):
    """Run the reference solution's `solve` on a workspace copy, for `--verify-tasks`."""
    _module(task, SOLUTION_FILE).solve(Path(root))
    return root


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

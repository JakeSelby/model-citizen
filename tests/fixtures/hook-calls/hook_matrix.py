# SPDX-License-Identifier: MIT
"""The hook permutation matrix: every hook, every recorded call, every stance variant.

Each call under `calls/` is one tool-call payload, as a runtime hands it to the dispatcher, with a
line saying what it is for. The payloads are synthetic: every repository path is under
`/workspace/example-repo`, and anything a hook must read from disk is staged into a temporary
home through `{home}` and `{repo}` tokens, so no real transcript, home path or name is recorded.

The variant environments are the base, every stance at its default, then one per non-default
variant of each stance topic in `primitives/stances/`, set the way a session sets one, through
`HARNESS_STANCE_<TOPIC>`. Under each, every call is dispatched through `harness_core.lifecycle`
once per row: a row is one hook id from `catalog.HOOK_IDS` switched on alone, or `dispatcher`,
every id switched off, which leaves only the logic no switch turns off, such as role confinement.
Every dispatch gets a fresh temporary home, so no call sees another's state, and the decision log
is held off: it records decisions and cannot change one.

A cell is the decision's shape, not its wording: `allow`, `ask`, `deny`, `rewrite`, `block`,
`stop`, `context`, `message`, joined by `+`, `none`, or `error:<type>` when the dispatcher
raises. A hook row's cell is `as-dispatcher` where the hook answers exactly as the `dispatcher`
row does, so the logic every row shares is counted once and a hook's cells are what that hook
changes. Rewording a reason moves no cell; flipping an answer moves one. `matrix.json` stores
each row and call's base cell and only the variants that differ from it, and leaves out a hook
row and call that is `as-dispatcher` throughout, so a flipped cell is a small diff.
`tests/test_hook_matrix.py` recomputes the matrix and fails naming the hook, the call and the
variant of every cell that moved. After an intended change, rewrite the expected matrix and
review its diff before committing it:

    python3 tests/fixtures/hook-calls/hook_matrix.py --write

`--check`, the default, prints the moved cells and exits 1 when there are any. No model is
called and nothing leaves the machine. The variants are split across a few worker processes, so
the whole matrix takes seconds.
"""
import atexit
import contextlib
import importlib.machinery
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
CALLS = HERE / "calls"
MATRIX = HERE / "matrix.json"
STANCES = REPO / "primitives" / "stances"
RUNTIME = "claude-code"
# The runtime process a session's edits are attributed to. A runtime sets it; set here, the
# intent hook does not walk this process's own ancestry, which is neither fixed nor fast.
RUNTIME_PID = "4242"
BASE = "base"
DISPATCHER = "dispatcher"
# A hook row's cell where the hook, switched on, answers exactly as the dispatcher alone does.
SAME = "as-dispatcher"
# The one directory a staged call may treat as its repository, inside the temporary home.
REPO_DIR = "workspace/example-repo"
# The environment a dispatch keeps from the caller's; everything else, `HARNESS_*` and a config
# directory included, is dropped so a developer's own selection cannot reach a cell.
KEPT = ("PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")
WORKERS = 4

if str(REPO / "lib") not in sys.path:
    sys.path.insert(0, str(REPO / "lib"))
from harness_core import catalog, lifecycle  # noqa: E402


def defaults():
    """`{topic: default variant}`, as the hooks resolve it."""
    return dict(lifecycle.load("posture").DEFAULT_STANCES)


def variants():
    """`[(key, {topic: variant})]`: the base, then each non-default variant of every topic."""
    base = defaults()
    out = [(BASE, {})]
    for topic in sorted(p.name for p in STANCES.iterdir() if p.is_dir()):
        for variant in sorted(p.stem for p in (STANCES / topic).glob("*.md")):
            if base.get(topic) != variant:
                out.append((topic + "=" + variant, {topic: variant}))
    return out


def rows():
    return list(catalog.HOOK_IDS) + [DISPATCHER]


def calls():
    """`{name: document}` for every call in the corpus, by file stem."""
    return dict((p.stem, json.loads(p.read_text(encoding="utf-8"))) for p in sorted(CALLS.glob("*.json")))


def _fill(value, tokens):
    if isinstance(value, str):
        for token, real in tokens.items():
            value = value.replace(token, real)
        return value
    if isinstance(value, list):
        return [_fill(v, tokens) for v in value]
    if isinstance(value, dict):
        return dict((_fill(k, tokens), _fill(v, tokens)) for k, v in value.items())
    return value


_SCRATCH = []


def _scratch(prefix):
    """A temporary directory removed when the process exits."""
    where = tempfile.mkdtemp(prefix=prefix)
    if not _SCRATCH:
        atexit.register(lambda: [shutil.rmtree(p, ignore_errors=True) for p in _SCRATCH])
    _SCRATCH.append(where)
    return Path(where)


_TEMPLATE = []
# No background maintenance or garbage collection in the template: either would write inside it
# while a copy of it is being taken.
QUIET_GIT = ["-c", "maintenance.auto=false", "-c", "gc.auto=0"]


def _template():
    """A `.git` directory with one empty commit, made once per process and copied per call."""
    if not _TEMPLATE:
        where = _scratch("hook-matrix-git-")
        env = dict(os.environ, GIT_AUTHOR_NAME="Example", GIT_AUTHOR_EMAIL="",
                   GIT_COMMITTER_NAME="Example", GIT_COMMITTER_EMAIL="",
                   GIT_AUTHOR_DATE="2026-01-01T00:00:00Z", GIT_COMMITTER_DATE="2026-01-01T00:00:00Z",
                   GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "init"]):
            subprocess.run(["git", "-C", str(where)] + QUIET_GIT + args, check=True, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _TEMPLATE.append(where / ".git")
    return _TEMPLATE[0]


def _copy_repo(src, dst):
    """Copy a `.git` directory, leaving out lock files and tolerating a file that vanishes.

    A lock is another git process's, never part of the repository, and git's own housekeeping may
    create and remove one, or a temporary object, while the copy walks the directory.
    """
    def copy(source, target):
        try:
            return shutil.copy2(source, target)
        except FileNotFoundError:
            return target

    shutil.copytree(str(src), str(dst), ignore=shutil.ignore_patterns("*.lock"), copy_function=copy)


_BIN = []


def _bin():
    """A directory holding only `git`, the real binary, on macOS where `/usr/bin/git` is a shim.

    The shim looks the toolchain up again under every fresh home, which costs several times the
    command it runs; the command is the same either way. Empty everywhere else.
    """
    if not _BIN:
        real = ""
        if sys.platform == "darwin":
            try:
                real = subprocess.run(["xcrun", "--find", "git"], capture_output=True, text=True,
                                      timeout=10).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                real = ""
        if real and os.path.isfile(real):
            where = _scratch("hook-matrix-bin-")
            os.symlink(real, str(where / "git"))
            _BIN.append(str(where))
        else:
            _BIN.append("")
    return _BIN[0]


def _stage(home, document):
    """Write the call's files into `home`, copy in its repository if asked; the filled payload."""
    tokens = {"{home}": str(home), "{repo}": str(home / REPO_DIR)}
    (home / REPO_DIR).mkdir(parents=True)
    for rel, text in sorted(_fill(document.get("files") or {}, tokens).items()):
        target = Path(rel) if os.path.isabs(rel) else home / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    if document.get("git"):
        _copy_repo(_template(), home / REPO_DIR / ".git")
    return _fill(document["payload"], tokens)


def _config(row):
    """The user config that switches every hook but `row` off."""
    return {"core_switches_acknowledged": True,
            "hooks": dict((ident, "on" if ident == row else "off") for ident in catalog.HOOK_IDS)}


def decision(output):
    """The shape of a dispatcher answer: its decision words, `+`-joined, or `none`."""
    if not isinstance(output, dict) or not output:
        return "none"
    fields = output.get("hookSpecificOutput") or {}
    parts = []
    if fields.get("permissionDecision"):
        parts.append(str(fields["permissionDecision"]))
    if "updatedInput" in fields:
        parts.append("rewrite")
    if output.get("decision"):
        parts.append(str(output["decision"]))
    if output.get("continue") is False:
        parts.append("stop")
    if fields.get("additionalContext"):
        parts.append("context")
    if output.get("systemMessage"):
        parts.append("message")
    return "+".join(parts) or "none"


def run_one(row, stances, document, queried=None):
    """Dispatch one call under one row and one variant environment, in a fresh home.

    `queried`, a set, collects every hook id the dispatcher asked about; see `answers`.
    """
    home = Path(tempfile.mkdtemp(prefix="hook-matrix-"))
    saved_env, saved_cwd = dict(os.environ), os.getcwd()
    saved_log, saved_enabled = list(lifecycle._DECISIONS), lifecycle.enabled
    try:
        payload = _stage(home, document)
        path = home / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_config(row)), encoding="utf-8")
        env = dict((k, saved_env[k]) for k in KEPT if k in saved_env)
        if _bin():
            env["PATH"] = _bin() + os.pathsep + env.get("PATH", "")
        env.update({"HOME": str(home), "HARNESS_RUNTIME": RUNTIME, "HARNESS_QUIET": "1",
                    "CLAUDE_PID": RUNTIME_PID})
        for topic, variant in stances.items():
            env["HARNESS_STANCE_" + topic.upper().replace("-", "_")] = variant
        os.environ.clear()
        os.environ.update(env)
        os.chdir(str(home))
        lifecycle._DECISIONS[:] = [None]
        if queried is not None:
            def enabled(name):
                queried.add(name)
                return saved_enabled(name)
            lifecycle.enabled = enabled
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                return decision(lifecycle.dispatch(RUNTIME, payload))
            except Exception as exc:  # recorded, so a hook that starts raising moves a cell
                return "error:" + type(exc).__name__
    finally:
        lifecycle._DECISIONS[:] = saved_log
        lifecycle.enabled = saved_enabled
        os.chdir(saved_cwd)
        os.environ.clear()
        os.environ.update(saved_env)
        shutil.rmtree(str(home), ignore_errors=True)


def answers(stances, document, exhaustive=False):
    """`{row: decision}` for one call under one variant environment.

    The `dispatcher` run records every hook id the dispatcher asked `enabled` about. A row whose
    id was never asked about takes the `dispatcher` answer without a run of its own: that hook,
    switched on, changes nothing until it is asked about, and a deterministic dispatch of the same
    input never asks, so the two runs are one run. `enabled` is the only reader of the switches
    on the decision path. `exhaustive` runs every row anyway, which is how the test holds the
    shortcut to that claim.
    """
    queried = set()
    out = {DISPATCHER: run_one(DISPATCHER, stances, document, queried)}
    for row in catalog.HOOK_IDS:
        out[row] = run_one(row, stances, document) if exhaustive or row in queried else out[DISPATCHER]
    return out


@contextlib.contextmanager
def cached_code():
    """Unmarshal each source file's code object once per process instead of once per dispatch.

    The dispatcher and the hooks load their policy modules afresh on every event, so a module's
    top level, `Path.home()` included, runs under the home in force; that stays true here, since
    each load still executes the code into a new module. Only the reading and unmarshalling of an
    unchanged file is shared, keyed by its path, size and modification time.
    """
    loader = importlib.machinery.SourceFileLoader
    original = loader.get_code
    cache = {}

    def get_code(self, fullname):
        path = self.get_filename(fullname)
        try:
            stat = os.stat(path)
        except OSError:
            return original(self, fullname)
        key = (path, stat.st_size, stat.st_mtime_ns)
        if key not in cache:
            cache[key] = original(self, fullname)
        return cache[key]

    loader.get_code = get_code
    try:
        yield
    finally:
        loader.get_code = original


def compute(envs=None, corpus=None, exhaustive=False):
    """`{row: {call: {variant key: decision}}}` for `envs` (default every variant), in-process."""
    envs = variants() if envs is None else envs
    corpus = calls() if corpus is None else corpus
    out = dict((row, {}) for row in rows())
    with cached_code():
        for key, stances in envs:
            for name, document in corpus.items():
                for row, answer in answers(stances, document, exhaustive).items():
                    out[row].setdefault(name, {})[key] = answer
    return out


def compute_parallel(workers=WORKERS):
    """`compute()` over every variant, split across worker processes by variant."""
    envs = variants()
    count = max(1, min(workers, os.cpu_count() or 1, len(envs)))
    chunks = [envs[i::count] for i in range(count)]
    env = dict((k, v) for k, v in os.environ.items()
               if not k.startswith("HARNESS_") and k != "CLAUDE_CONFIG_DIR")
    procs = [subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker"]
                              + [key for key, _ in chunk], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=env, cwd=str(REPO))
             for chunk in chunks]
    out = dict((row, {}) for row in rows())
    failures = []
    for proc in procs:
        stdout, stderr = proc.communicate()
        if proc.returncode != 0:
            failures.append(stderr.decode("utf-8", "replace")[-2000:])
            continue
        for row, by_call in json.loads(stdout.decode("utf-8")).items():
            for name, by_key in by_call.items():
                out[row].setdefault(name, {}).update(by_key)
    if failures:
        raise RuntimeError("hook matrix worker failed:\n" + "\n".join(failures))
    return out


def flatten(full):
    """`{(row, call, variant): cell}` from `compute`'s form, each hook row's cell attributed.

    A hook row's cell is `SAME` where its answer is the `dispatcher` row's, so logic every row
    shares is one cell, in `dispatcher`, and a hook's cell is only what that hook changes.
    """
    flat = dict(((row, name, key), answer) for row, by_call in full.items()
                for name, by_key in by_call.items() for key, answer in by_key.items())
    return dict((cell, SAME if cell[0] != DISPATCHER and answer == flat.get((DISPATCHER,) + cell[1:])
                 else answer) for cell, answer in flat.items())


def compact(full):
    """The committed form: per row and call the base cell and the variants that differ from it.

    A hook row and call whose every cell is `SAME` is left out.
    """
    keys = [key for key, _ in variants()]
    flat = flatten(full)
    cells = {}
    for row in rows():
        for name in sorted(full[row]):
            base = flat[(row, name, BASE)]
            entry = {BASE: base}
            entry.update((k, flat[(row, name, k)]) for k in keys if flat[(row, name, k)] != base)
            if row == DISPATCHER or entry != {BASE: SAME}:
                cells.setdefault(row, {})[name] = entry
    return {"schema_version": 1, "runtime": RUNTIME, "variants": keys, "rows": rows(),
            "calls": sorted(full[DISPATCHER]), "cells": cells}


def expand(matrix):
    """`{(row, call, variant): cell}` from the committed form."""
    out = {}
    for row in matrix["rows"]:
        for name in matrix["calls"]:
            entry = matrix["cells"].get(row, {}).get(name, {BASE: SAME})
            for key in matrix["variants"]:
                out[(row, name, key)] = entry.get(key, entry[BASE])
    return out


def differences(expected, actual):
    """One line per cell that moved between two flat matrices, naming hook, call and variant."""
    lines = []
    for cell in sorted(set(expected) | set(actual)):
        if expected.get(cell) != actual.get(cell):
            row, name, key = cell
            lines.append("hook %s, call %s, variant %s: expected %s, got %s"
                         % (row, name, key, expected.get(cell, "(absent)"), actual.get(cell, "(absent)")))
    return lines


def render(matrix):
    return json.dumps(matrix, indent=1) + "\n"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--worker"]:
        wanted = set(argv[1:])
        print(json.dumps(compute([env for env in variants() if env[0] in wanted])))
        return 0
    if argv not in ([], ["--check"], ["--write"]):
        sys.stderr.write("usage: hook_matrix.py [--check | --write]\n")
        return 2
    matrix = compact(compute_parallel())
    if argv == ["--write"]:
        MATRIX.write_text(render(matrix), encoding="utf-8")
        print("wrote " + str(MATRIX.relative_to(REPO)))
        return 0
    lines = differences(expand(json.loads(MATRIX.read_text(encoding="utf-8"))), expand(matrix))
    for line in lines:
        print(line)
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main())

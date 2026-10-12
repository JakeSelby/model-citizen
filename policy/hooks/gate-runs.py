#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Library: the record of every gate run, and the two checks that read it.

A reply saying the tests pass, and a push, are each only as good as the last gate run before
them, and in practice both often follow an edit made after that run. So every run of a
repository's gate is recorded here, the stop-gate hook's own and a `citizen gate` run of the
`## Gate` block alike, with the commit, the state of the tree, the exit code and the time:

  - `runs.jsonl`: one row per run, in order, trimmed to its newer half past MAX_LOG_BYTES.
  - `latest/<checkout>.json`: the newest run in each checkout, which the Stop-time claim check
    reads (`fresh_green`). A run is fresh while the tree digest it ran on is the tree now, so any
    edit, commit or checkout since it makes it stale.
  - `green/<commit>.json`: a passing `## Gate` run on a commit with no tracked changes, which the
    push check reads (`push_verdict`). It is kept per commit, not per checkout, because a commit
    gated in one worktree is the same commit in another.

The stop gate's subset counts for a claim, since it is the repository's own definition of a
passing turn, but only the full `## Gate` block counts for a push. A repository that declares no
gate is not checked by either.

Never raises out of `record`: a run that cannot be written costs the record, never the gate.
"""
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

GATE_HEADING = "## Gate"
MAX_LOG_BYTES = 1024 * 1024
PASSED, FAILED = "passed", "failed"

# Paths a push may change without a gate run: documentation only. Rules, skills and stances are
# Markdown too, but they are behaviour, so only these count.
DOC_PREFIXES = ("docs/", "changelog.d/")
DOC_ROOT_SUFFIXES = (".md",)

# A reply claims the tests or the gate pass. Conservative by design: a subject (tests, the
# suite, the gate) followed closely by a verb of passing or by "green", in one
# sentence that carries no negation, condition or question. "Ran 12 tests ... OK" counts too.
CLAIM = re.compile(
    r"(?:\b(?:all\s+)?(?:the\s+)?(?:unit\s+|full\s+|whole\s+)?"
    r"(?:tests?|test\s+suite|suite|gate|gate\s+block|stop\s+gate)\b"
    r"(?:\s+(?:all|now|still|both|each|locally|again|cleanly))*"
    r"\s+(?:pass(?:es|ed|ing)?|(?:is|are|was|were|went|stays?|stayed)\s+green|green)\b"
    r"|\bgate[\s:,-]+green\b"
    r"|^\s*ran\s+\d+\s+tests?\b.*\bok\b)", re.IGNORECASE)
NEGATION = re.compile(
    r"n't\b|\b(?:not|no|never|neither|nor|unless|until|if|whether|once|before|fail(?:s|ed|ing)?"
    r"|cannot|red|unverified)\b|\?", re.IGNORECASE)
SENTENCE = re.compile(r"(?<=[.!;])\s+|\n+")


def state_dir():
    """Read at each call, so a HOME set after import is the one written to."""
    return Path.home() / ".local" / "state" / "agent-harness" / "gate-runs"


def _key(root):
    return hashlib.sha256(str(Path(root).resolve()).encode("utf-8")).hexdigest()


def git(root, *args):
    """git's stdout, or None when it fails."""
    try:
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=10)
    except Exception:
        return None
    return out.stdout if out.returncode == 0 else None


def head(root):
    out = git(root, "rev-parse", "--verify", "-q", "HEAD")
    return out.decode().strip() if out else None


def dirty(root):
    """True when tracked files differ from HEAD, in the index or the tree. Untracked files are
    left out: a scratch file beside the checkout changes nothing the commit holds."""
    out = git(root, "status", "--porcelain", "--untracked-files=no")
    return out is None or bool(out.strip())


def tree_digest(root):
    """A digest of everything the gate could have read: HEAD, the status, both diffs and the
    contents of every untracked file not ignored. Equal digests mean nothing was edited."""
    digest = hashlib.sha256()
    digest.update(str(Path(root).resolve()).encode())

    def checked(*args):
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              check=True, timeout=10).stdout
    for args in (("rev-parse", "HEAD"), ("status", "--porcelain", "-z"),
                 ("diff", "--binary"), ("diff", "--cached", "--binary")):
        digest.update(hashlib.sha256(checked(*args)).digest())
    for name in checked("ls-files", "--others", "--exclude-standard", "-z").split(b"\0"):
        if not name:
            continue
        path = Path(root) / os.fsdecode(name)
        digest.update(name + b"\0")
        if path.is_symlink():
            digest.update(os.fsencode(os.readlink(path)))
        elif path.is_file():
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def snapshot(root):
    """The facts a run is recorded against, taken before it starts."""
    return {"root": str(Path(root).resolve()), "head": head(root), "dirty": dirty(root),
            "tree": tree_digest(root)}


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".run-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, sort_keys=True)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _append(row):
    log = state_dir() / "runs.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    if log.stat().st_size > MAX_LOG_BYTES:
        lines = log.read_text(encoding="utf-8").splitlines(True)
        log.write_text("".join(lines[len(lines) // 2:]), encoding="utf-8")


def record(snap, heading, code, source, status=None, elapsed=None, now=None):
    """Record one run taken against `snap`; returns the row, or None when nothing was written.

    `code` is the shell's exit code, or None when it has none (a timeout). `status` defaults to
    passed or failed by the code; the stop gate passes `timeout` or `unverified` when a run
    ended without a result that stands for the tree it started on."""
    try:
        if status is None:
            status = PASSED if code == 0 else FAILED
        row = dict(snap, heading=heading, exit=code, status=status, source=source,
                   ts=time.time() if now is None else now)
        if elapsed is not None:
            row["elapsed_seconds"] = elapsed
        _append(row)
        _write_json(state_dir() / "latest" / (_key(snap["root"]) + ".json"), row)
        if (status == PASSED and heading.strip().lower() == GATE_HEADING.lower()
                and snap.get("head") and not snap.get("dirty")):
            _write_json(state_dir() / "green" / (snap["head"] + ".json"), row)
        return row
    except Exception:
        return None


def latest(root):
    return _read_json(state_dir() / "latest" / (_key(root) + ".json"))


def fresh_green(root, digest=None):
    """(fresh, last run): fresh when the newest run here passed on the tree as it is now."""
    last = latest(root)
    if last is None or last.get("status") != PASSED:
        return False, last
    try:
        now = digest or tree_digest(root)
    except Exception:
        return False, last
    return last.get("tree") == now, last


def green_for(commit):
    if not commit or not re.match(r"^[0-9a-f]{7,64}$", commit):
        return None
    return _read_json(state_dir() / "green" / (commit + ".json"))


def claims_pass(text):
    """The first sentence of `text` that claims the tests or the gate pass, or None."""
    for sentence in SENTENCE.split(text or ""):
        if CLAIM.search(sentence) and not NEGATION.search(sentence):
            return sentence.strip()[:200]
    return None


def is_doc(path):
    return path.startswith(DOC_PREFIXES) or ("/" not in path and path.endswith(DOC_ROOT_SUFFIXES))


DEFAULT_BRANCH_REFS = ("refs/remotes/origin/HEAD", "refs/remotes/origin/main",
                       "refs/remotes/origin/master")


def push_base(root):
    """The commit the push's changes are measured from: the upstream where one is set, else the
    merge base with origin's default branch, so a first push counts every commit the branch adds
    and not only its last. None when neither resolves."""
    out = git(root, "rev-parse", "--verify", "-q", "@{upstream}")
    if out:
        return out.decode().strip()
    for ref in DEFAULT_BRANCH_REFS:
        out = git(root, "rev-parse", "--verify", "-q", ref)
        if out:
            base = git(root, "merge-base", out.decode().strip(), "HEAD")
            return base.decode().strip() if base else None
    return None


def pushed_paths(root):
    """The paths the push adds, changed between `push_base` and HEAD. None when git cannot say,
    so the push is asked about rather than waved through as documentation."""
    base = push_base(root)
    if not base:
        return None
    out = git(root, "diff", "--name-only", "-z", base, "HEAD")
    if out is None:
        return None
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def _stop_gate():
    spec = importlib.util.spec_from_file_location(
        "gate_runs_stop_gate", str(Path(__file__).resolve().with_name("stop-gate.py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def declared(root):
    """The commands of the repository's `## Gate` block, or []."""
    try:
        return _stop_gate().gate_commands(str(root), GATE_HEADING)
    except Exception:
        return []


def toplevel(where):
    out = git(where, "rev-parse", "--show-toplevel")
    return out.decode().strip() if out else None


def _short(commit):
    return (commit or "?")[:12]


def push_verdict(where):
    """(verdict, sentence) for a push from directory `where`.

    `green`, `docs` (everything the push adds is documentation) and `undeclared` (no `## Gate`
    block, or not a repository) let it run; `missing` and `unknown` ask, with the sentence
    naming what is missing and the command that records it."""
    if where is None:
        return "unknown", ("Gate: the directory this push runs from cannot be known before it "
                           "runs, so no recorded gate run can be matched to the commit it sends.")
    root = toplevel(where)
    if root is None or not declared(root):
        return "undeclared", ""
    commit = head(root)
    found = green_for(commit)
    if found is not None:
        return "green", "Gate: green on %s at %s." % (_short(commit), _when(found.get("ts")))
    paths = pushed_paths(root)
    if paths and all(is_doc(p) for p in paths):
        return "docs", "Gate: not required; the push changes documentation only."
    last = latest(root)
    if last is None:
        seen = "no gate run is recorded in this checkout"
    else:
        seen = "the last run here was %s on %s%s at %s" % (
            last.get("status"), _short(last.get("head")),
            " with uncommitted changes" if last.get("dirty") else "", _when(last.get("ts")))
    return "missing", ("Gate: no green `## Gate` run is recorded for %s, the commit this pushes; "
                       "%s. Run `citizen gate` on the committed tree, then push."
                       % (_short(commit), seen))


def _when(ts):
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
    except Exception:
        return "an unknown time"

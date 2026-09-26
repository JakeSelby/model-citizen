# SPDX-License-Identifier: MIT
"""The write-intent ledger: parallel writers claim the paths they will edit, before editing them.

One file per session and worktree, `~/.local/state/agent-harness/intents/<session>--<hash>.json`,
where the hash is of the worktree root: subagents share their parent's session id, so two sibling
builders from one session hold two files, never one.

    {"schema": 1, "session": "…", "repo": "/abs/repo/.git", "branch": "feat/x",
     "worktree": "/abs/worktrees/repo/task", "paths": ["lib/x.py", "tests/test_x*.py"],
     "pid": 4242, "started_at": "2026-09-25T14:00:00Z", "updated_at": "…"}

`repo` is the git common directory, so every worktree of one repository shares it and a claim
from another repository never matches. `paths` are repository-relative: a plain path, which also
covers everything under it when it names a directory, or an `fnmatch` glob. A path need not exist
yet, because the paths a plan names include the files it will create.

`pid` is the long-lived runtime process that owns the session, not the shell that ran the claim:
`CLAUDE_PID` where the runtime exports it, otherwise the first ancestor that is not a shell or an
interpreter. A claim whose pid is dead, or whose worktree has been removed, is stale: every read
removes it, and so does `harness intent sweep`. The second rule is what ends a subagent's claim,
since its pid is the runtime's and outlives it; landing removes the worktree.

A claim never matches its owner's own edits. Own means an edit inside the worktree the claim was
made from, by the same session id or the same runtime process. Subagents share both their
parent's session id and its pid, so neither alone tells two sibling builders apart; the worktree
does, which is why it is required.

What an overlap does is the `coordination.repeat_overlap` variant in the user config. `deny`, the
default, warns on the first hit on a path and denies the second in the same session; `warn` never
denies; anything the harness cannot read or honour warns, because a setting nobody can read must
never be what blocks an edit. Every warn or deny is an `intent-overlap` row in the decision log,
and `harness intent merge` adds a `landing-merge` row per landing, so `harness usage --conflicts`
can put the two side by side per week. No model judgment anywhere.
"""
import contextlib
import datetime
import fnmatch
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - no POSIX locks on this platform
    fcntl = None

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = 1
POINT = "intent-overlap"
MERGE_POINT = "landing-merge"
VARIANTS = ("deny", "warn")
DEFAULT_VARIANT = "deny"
MERGE_RESULTS = ("clean", "conflicted")
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
# Hit counters are per session and carry no pid, so they age out instead: a fortnight, the same
# horizon the session registry keeps.
SWEEP_DAYS = 14
# How long `hit()` polls for the counter lock before counting unlocked, and how often.
HITS_LOCK_BUDGET = 0.5
HITS_LOCK_POLL = 0.01
SESSION = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
GLOB = re.compile(r"[*?\[]")
# Processes between a runtime and the command it ran. The runtime is the first ancestor not named
# here; an interpreter is skipped because `harness` itself is one.
WRAPPERS = ("sh", "bash", "zsh", "dash", "fish", "ksh", "tcsh", "csh", "env", "timeout", "uv",
            "harness", "xargs", "nohup")


def home(env=None):
    env = os.environ if env is None else env
    return Path(env.get("HARNESS_HOME") or env.get("HOME") or Path.home())


def state_dir(env=None):
    return home(env) / ".local" / "state" / "agent-harness"


def intents_dir(env=None):
    return state_dir(env) / "intents"


def hits_dir(env=None):
    return intents_dir(env) / "hits"


def config_path(env=None):
    return home(env) / ".config" / "agent-harness" / "config.json"


def now_ts(now=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now))


# ---- policy ---------------------------------------------------------------------------------

def overlap_variant(env=None):
    """`coordination.repeat_overlap`: `deny` when unset, `warn` when unreadable or unknown.

    Read the way `decisions.py` reads its `telemetry` block, from the same file: absent file,
    absent block and absent key are the default; a file that does not parse, a block that is
    not an object and a value outside VARIANTS are settings nobody can honour, so they warn.
    """
    path = config_path(env)
    try:
        with open(str(path), encoding="utf-8") as stream:
            cfg = json.load(stream)
    except FileNotFoundError:
        return DEFAULT_VARIANT
    except (OSError, ValueError):
        return "warn"
    if not isinstance(cfg, dict):
        return "warn"
    block = cfg.get("coordination")
    if block is None:
        return DEFAULT_VARIANT
    if not isinstance(block, dict):
        return "warn"
    value = block.get("repeat_overlap", DEFAULT_VARIANT)
    return value if value in VARIANTS else "warn"


# ---- identity -------------------------------------------------------------------------------

def _git(cwd, *args):
    try:
        out = subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True,
                             text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _existing(path):
    path = Path(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    return path if path.is_dir() else path.parent


def repository(path):
    """`{"root", "common", "branch"}` for the checkout holding `path`, or None outside one.

    `path` need not exist: the nearest existing directory above it decides.
    """
    where = _existing(Path(path).expanduser())
    out = _git(where, "rev-parse", "--show-toplevel", "--git-common-dir", "--abbrev-ref", "HEAD")
    if out is None:
        out = _git(where, "rev-parse", "--show-toplevel", "--git-common-dir")
        if out is None:
            return None
        out += "HEAD\n"
    lines = out.splitlines()
    if len(lines) < 3:
        return None
    root = Path(lines[0]).resolve()
    common = Path(lines[1])
    if not common.is_absolute():
        common = where / common
    return {"root": str(root), "common": str(common.resolve()), "branch": lines[2]}


def _ancestor(pid):
    try:
        out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True,
                             text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    parts = out.stdout.strip().split(None, 1) if out.returncode == 0 else []
    if len(parts) != 2 or not parts[0].isdigit():
        return None
    return int(parts[0]), os.path.basename(parts[1].strip())


def _wrapper(name):
    name = name.lstrip("-")
    return name in WRAPPERS or name.startswith("python")


def runtime_pid(env=None, start=None):
    """The long-lived runtime process: `CLAUDE_PID`, else the first non-wrapper ancestor."""
    env = os.environ if env is None else env
    value = str(env.get("CLAUDE_PID") or "")
    if value.isdigit() and int(value) > 1:
        return int(value)
    current = os.getpid() if start is None else start
    for _ in range(12):
        info = _ancestor(current)
        if info is None or info[0] <= 1:
            break
        parent = info[0]
        above = _ancestor(parent)
        if above is None or not _wrapper(above[1]):
            return parent
        current = parent
    return os.getppid()


def session_key(env=None, pid=None):
    """This session's claim-file name: the runtime's session id, else one derived from its pid."""
    env = os.environ if env is None else env
    for name in ("HARNESS_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        value = str(env.get(name) or "")
        if SESSION.match(value):
            return value
    return "pid-" + str(runtime_pid(env) if pid is None else pid)


def alive(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


# ---- paths ----------------------------------------------------------------------------------

def _resolved(path):
    """`path` made absolute with its existing part resolved, so /tmp and /private/tmp agree."""
    path = Path(path).expanduser()
    base = path
    tail = []
    while not base.exists() and base != base.parent:
        tail.append(base.name)
        base = base.parent
    out = base.resolve()
    for name in reversed(tail):
        out = out / name
    return out


def relative(path, root, cwd=None):
    """`path` relative to the worktree `root`, POSIX-style, or None when it lies outside it."""
    text = str(path)
    keep_dir = text.endswith("/")
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = Path(cwd or os.getcwd()) / candidate
    if GLOB.search(text):
        # A glob is anchored where it was written; only its literal head is resolved.
        head = Path(os.path.abspath(str(candidate)))
        parts = head.parts
        cut = next((i for i, p in enumerate(parts) if GLOB.search(p)), len(parts))
        base = _resolved(Path(*parts[:cut]))
        full = base.joinpath(*parts[cut:]) if parts[cut:] else base
    else:
        full = _resolved(os.path.abspath(str(candidate)))
    try:
        rel = full.relative_to(Path(root)).as_posix()
    except ValueError:
        return None
    if rel in ("", "."):
        return None
    return rel + "/" if keep_dir else rel


def matches(pattern, rel):
    """Whether a claimed `pattern` covers the repository-relative path `rel`."""
    if not pattern or not rel:
        return False
    if pattern.endswith("/"):
        return rel.startswith(pattern)
    if GLOB.search(pattern):
        if fnmatch.fnmatchcase(rel, pattern):
            return True
        # `**/` may stand for no directory at all, and `dir/**` covers the directory's contents.
        if "**/" in pattern and fnmatch.fnmatchcase(rel, pattern.replace("**/", "")):
            return True
        return pattern.endswith("/**") and rel.startswith(pattern[:-2])
    return rel == pattern or rel.startswith(pattern + "/")


# ---- claim files ----------------------------------------------------------------------------

def slot(session, worktree):
    """The file name shared by a claim and its hit counter: session plus a worktree hash."""
    if not SESSION.match(str(session or "")):
        raise ValueError("session id must be 1-128 letters, digits, dots, underscores or hyphens")
    digest = hashlib.sha256(str(worktree or "").encode("utf-8")).hexdigest()[:12]
    return session + "--" + digest + ".json"


def claim_path(session, worktree, env=None):
    return intents_dir(env) / slot(session, worktree)


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(str(path.parent), 0o700)
    except OSError:
        pass
    temp = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
    temp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(str(temp), str(path))


def _read(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def claim(paths, cwd=None, session=None, pid=None, env=None, now=None):
    """Add `paths` to this session's claim, creating it. Returns the claim as written.

    A re-claim adds to the paths already held, because a builder that touches a file outside its
    plan re-claims that file and must not drop the rest. Raises ValueError outside a repository
    and for a path outside the worktree.
    """
    cwd = str(cwd or os.getcwd())
    repo = repository(cwd)
    if repo is None:
        raise ValueError("not inside a git repository: " + cwd)
    pid = runtime_pid(env) if pid is None else int(pid)
    session = session or session_key(env, pid)
    wanted = []
    for item in paths:
        rel = relative(item, repo["root"], cwd)
        if rel is None:
            raise ValueError("outside the worktree " + repo["root"] + ": " + str(item))
        wanted.append(rel)
    sweep(env)
    target = claim_path(session, repo["root"], env)
    current = _read(target) or {}
    held = [p for p in current.get("paths", []) if isinstance(p, str)]
    data = {"schema": SCHEMA, "session": session, "repo": repo["common"],
            "branch": repo["branch"], "worktree": repo["root"],
            "paths": sorted(set(held) | set(wanted)), "pid": pid,
            "started_at": current.get("started_at") or now_ts(now), "updated_at": now_ts(now)}
    _write(target, data)
    return data


def release(session=None, cwd=None, env=None):
    """Remove this session's claim in this worktree, and its hit counter. Returns whether one was held.

    A sibling's claim under the same session id, in another worktree, is left alone.
    """
    session = session or session_key(env)
    repo = repository(str(cwd or os.getcwd()))
    if repo is None:
        raise ValueError("not inside a git repository: " + str(cwd or os.getcwd()))
    name = slot(session, repo["root"])
    target = intents_dir(env) / name
    held = target.exists()
    for path in (target, hits_dir(env) / name):
        try:
            path.unlink()
        except OSError:
            pass
    return held


def claims(env=None):
    """Every live claim, oldest first. Dead-pid claims are removed on the way, never returned."""
    found = []
    directory = intents_dir(env)
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        data = _read(path)
        if data is None or not isinstance(data.get("paths"), list):
            continue
        if stale(data):
            _remove(path)
            continue
        found.append(data)
    return sorted(found, key=lambda c: (str(c.get("started_at")), str(c.get("session"))))


def stale(data):
    """Whether a claim no longer holds: its process is dead or its worktree is gone."""
    worktree = data.get("worktree")
    return not alive(data.get("pid")) or not (isinstance(worktree, str) and Path(worktree).is_dir())


def _remove(path):
    try:
        path.unlink()
        return True
    except OSError:
        return False


def sweep(env=None, now=None):
    """Remove every stale claim, and unreadable files and hit counters past the horizon."""
    removed = []
    directory = intents_dir(env)
    horizon = (time.time() if now is None else now) - SWEEP_DAYS * 86400
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        data = _read(path)
        if data is None:
            stale_file = _mtime(path) < horizon
        else:
            stale_file = stale(data)
        if stale_file and _remove(path):
            removed.append(path.name)
    hits = hits_dir(env)
    for path in sorted(hits.glob("*.json")) if hits.is_dir() else []:
        if _mtime(path) < horizon and _remove(path):
            removed.append("hits/" + path.name)
    return removed


def _mtime(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return time.time()


def own(item, session, pid, root):
    """Whether a claim is the editor's own: its worktree, and its session id or its process."""
    if item.get("worktree") != root:
        return False
    return (session is not None and item.get("session") == session) or (
        pid is not None and item.get("pid") == pid)


def editor_root(cwd, target_root):
    """The worktree the editor runs in: the one holding `cwd`, or None outside a checkout.

    Only when no `cwd` is known does the target's worktree stand in. An absolute path can reach
    into a sibling's worktree, so the target never decides whose claim is the editor's own.
    """
    if not cwd:
        return target_root
    repo = repository(str(cwd))
    return repo["root"] if repo else None


def overlaps(path, session=None, pid=None, cwd=None, env=None):
    """Live siblings' claims covering `path`: a list of `(claim, pattern, rel)`.

    Empty outside a repository, for a path outside its worktree, and for the session's own claims.
    Repository and path come from the target; whose claims are the editor's own, from `cwd`.
    """
    target = Path(path)
    if not target.is_absolute():
        target = Path(cwd or os.getcwd()) / target
    repo = repository(target)
    if repo is None:
        return []
    rel = relative(target, repo["root"])
    if rel is None:
        return []
    editor = editor_root(cwd, repo["root"])
    found = []
    for item in claims(env):
        if item.get("repo") != repo["common"] or own(item, session, pid, editor):
            continue
        for pattern in item["paths"]:
            if isinstance(pattern, str) and matches(pattern, rel):
                found.append((item, pattern, rel))
                break
    return found


@contextlib.contextmanager
def _hits_lock(directory):
    """Hold an exclusive lock on the hit counters, or proceed unlocked if none can be taken.

    One lock file for the whole directory, so there is no per-counter lock file to sweep. The lock
    is polled without blocking for at most `HITS_LOCK_BUDGET` seconds: ordinary contention lasts
    one small read and write, while a holder that never lets go would otherwise stall the edit hook
    until the runtime's own timeout. Failing to lock must never be what blocks an edit, so running
    out of budget, or any error, degrades to the unlocked count.
    """
    stream = None
    if fcntl is not None:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            stream = open(str(directory / ".lock"), "a")
            deadline = time.monotonic() + HITS_LOCK_BUDGET
            while True:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(HITS_LOCK_POLL)
        except OSError:
            if stream is not None:
                stream.close()
            stream = None
    try:
        yield
    finally:
        if stream is not None:
            stream.close()


def hit(session, worktree, rel, env=None):
    """Count one overlap on `rel` for `session` editing in `worktree`; return the count so far.

    The read-modify-write runs under `_hits_lock`, so two concurrent edits get 1 and 2, never 1
    and 1, and the repeat is still denied.
    """
    safe = session if SESSION.match(str(session or "")) else "unknown"
    directory = hits_dir(env)
    target = directory / slot(safe, worktree)
    with _hits_lock(directory):
        counts = _read(target) or {}
        count = int(counts.get(rel, 0) or 0) + 1
        counts[rel] = count
        try:
            _write(target, counts)
        except OSError:
            pass
    return count


def answer_for(count, variant):
    """`deny` for a repeat under the `deny` variant, `warn` otherwise."""
    return "deny" if variant == "deny" and count >= 2 else "warn"


def describe(found):
    item, pattern, rel = found
    return ("`" + rel + "` is claimed by live session " + str(item.get("session")) + " (branch "
            + str(item.get("branch")) + ", worktree " + str(item.get("worktree")) + ", claim `"
            + pattern + "`)")


# ---- decision log ---------------------------------------------------------------------------

_DECISIONS = []


def decisions():
    """The hook-side decision log module, or None. Loaded once per process."""
    if not _DECISIONS:
        try:
            path = ROOT / "policy" / "hooks" / "decisions.py"
            spec = importlib.util.spec_from_file_location("harness_intent_decisions", str(path))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _DECISIONS.append(module)
        except Exception:
            _DECISIONS.append(None)
    return _DECISIONS[0]


def log(point, answer, text, session="", runtime="", extra=None, now=None):
    """Append one decision row carrying `extra` fields. Returns its id, or None. Never raises."""
    module = decisions()
    if module is None:
        return None
    try:
        if not module.enabled():
            return None
        row = module._decision_row(point, answer, text, {"session_id": session or ""},
                                   runtime, None, now)
        row.update(extra or {})
        module._append(row)
        return row["decision_id"]
    except Exception:
        return None


def log_overlap(answer, found, tool, session, variant, runtime=""):
    item, pattern, rel = found
    return log(POINT, answer, (tool + " " + rel).strip(), session, runtime,
               {"variant": variant, "path": rel, "claim": pattern,
                "claimed_by": item.get("session"), "claimed_branch": item.get("branch")})


# ---- landing merges -------------------------------------------------------------------------

def probe_merge(cwd, base):
    """`clean` or `conflicted` for merging `base` into HEAD, computed without touching the tree.

    `git merge-tree --write-tree` (git 2.38+) exits 1 on a conflict and 0 on a clean merge. None
    when git cannot answer, so the caller asks for the result instead of guessing one.
    """
    try:
        out = subprocess.run(["git", "-C", str(cwd), "merge-tree", "--write-tree", "HEAD", base],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return {0: "clean", 1: "conflicted"}.get(out.returncode)


def record_merge(result, cwd=None, base="origin/main", session="", now=None):
    cwd = str(cwd or os.getcwd())
    repo = repository(cwd) or {}
    name = Path(repo.get("common", "")).parent.name if repo else ""
    branch = repo.get("branch", "")
    return log(MERGE_POINT, result, branch + " into " + base, session, "",
               {"repo": name, "branch": branch, "base": base}, now)


def week_of(ts):
    """The Monday that starts the ISO week of a `YYYY-MM-DD…` timestamp, or None."""
    try:
        day = datetime.date(int(ts[0:4]), int(ts[5:7]), int(ts[8:10]))
    except (TypeError, ValueError):
        return None
    return (day - datetime.timedelta(days=day.weekday())).isoformat()


def conflict_weeks(rows, days, now=None):
    """Per week: landings, conflicted landings, overlap warns and denies. Newest week last."""
    cutoff = now_ts((time.time() if now is None else now) - max(days, 0) * 86400)
    weeks = {}
    for row in rows:
        if row.get("kind") != "decision" or (row.get("ts") or "") < cutoff:
            continue
        point, answer = row.get("point"), row.get("deterministic_answer")
        if point not in (MERGE_POINT, POINT):
            continue
        week = week_of(row.get("ts") or "")
        if week is None:
            continue
        slot = weeks.setdefault(week, {"merges": 0, "conflicted": 0, "warn": 0, "deny": 0})
        if point == MERGE_POINT:
            slot["merges"] += 1
            slot["conflicted"] += 1 if answer == "conflicted" else 0
        elif answer in ("warn", "deny"):
            slot[answer] += 1
    return [(week, weeks[week]) for week in sorted(weeks)]


def conflict_report(days, env=None, now=None):
    """The lines `harness usage --conflicts` prints."""
    module = decisions()
    path = state_dir(env) / "decisions.jsonl"
    rows = module.read_rows(path) if module is not None else []
    weeks = conflict_weeks(rows, days, now)
    if not weeks:
        return ["no landing merges or intent overlaps recorded in the last " + str(days)
                + " day(s); looked in " + str(path)]
    head = "{:<12}{:>8}{:>12}{:>9}{:>10}{:>8}".format(
        "week of", "merges", "conflicted", "share", "overlaps", "denied")
    lines = [head, "-" * len(head)]
    for week, slot in weeks:
        share = "{:.0%}".format(slot["conflicted"] / slot["merges"]) if slot["merges"] else "-"
        lines.append("{:<12}{:>8}{:>12}{:>9}{:>10}{:>8}".format(
            week, slot["merges"], slot["conflicted"], share, slot["warn"] + slot["deny"],
            slot["deny"]))
    return lines


# ---- pre-commit check -----------------------------------------------------------------------

def changed_paths(cwd):
    """Staged, unstaged and untracked paths in the worktree holding `cwd`, absolute."""
    repo = repository(cwd)
    if repo is None:
        return []
    names = set()
    for args in (("diff", "--name-only", "HEAD"), ("diff", "--name-only", "--cached"),
                 ("ls-files", "--others", "--exclude-standard")):
        out = _git(repo["root"], *args) or ""
        names.update(line for line in out.splitlines() if line.strip())
    return [str(Path(repo["root"]) / name) for name in sorted(names)]


def check(paths=None, cwd=None, session=None, pid=None, env=None, runtime=""):
    """Every overlap for `paths` (default: the worktree's changes), with its logged answer.

    The runtime without a pre-edit event runs this before commit. It has no earlier warning to
    count from, so under `deny` any overlap denies; under `warn` it only warns. Returns
    `(answer, found)` where answer is None when nothing overlaps.
    """
    cwd = str(cwd or os.getcwd())
    pid = runtime_pid(env) if pid is None else pid
    session = session or session_key(env, pid)
    targets = list(paths) if paths else changed_paths(cwd)
    found = []
    for target in targets:
        full = Path(target) if Path(target).is_absolute() else Path(cwd) / target
        found.extend(overlaps(full, session, pid, cwd, env))
    if not found:
        return None, []
    variant = overlap_variant(env)
    answer = "deny" if variant == "deny" else "warn"
    for item in found:
        log_overlap(answer, item, "check", session, variant, runtime)
    return answer, found


# ---- command line ---------------------------------------------------------------------------

def command(args, say):
    """`harness intent …`. Returns the exit status."""
    env = os.environ
    action = args.intent_action
    try:
        if action == "claim":
            data = claim(args.paths, session=args.session, pid=args.pid, env=env)
            say("claimed " + str(len(data["paths"])) + " path(s) for session " + data["session"]
                + " on " + data["branch"] + " (pid " + str(data["pid"]) + ")")
            for pattern in data["paths"]:
                say("  " + pattern)
            return 0
        if action == "release":
            held = release(args.session, os.getcwd(), env)
            say("released" if held else "no claim held")
            return 0
        if action == "sweep":
            removed = sweep(env)
            say("swept " + str(len(removed)) + " stale file(s)")
            return 0
        if action == "list":
            repo = repository(os.getcwd())
            shown = [c for c in claims(env) if args.all or (repo and c.get("repo") == repo["common"])]
            if not shown:
                say("no live claims" + ("" if args.all else " in this repository"))
            for item in shown:
                say(str(item.get("session")) + "  " + str(item.get("branch")) + "  pid "
                    + str(item.get("pid")) + "  " + ", ".join(item.get("paths", [])))
            return 0
        if action == "check":
            answer, found = check(args.paths, session=args.session, env=env,
                                  runtime=env.get("HARNESS_RUNTIME", ""))
            if answer is None:
                say("no overlap with a live sibling's claim")
                return 0
            for item in found:
                say(answer + ": " + describe(item))
            return 1 if answer == "deny" else 0
        if action == "merge":
            result = args.result or probe_merge(os.getcwd(), args.base)
            if result is None:
                say("could not tell whether merging " + args.base + " conflicts; pass clean or "
                    "conflicted")
                return 2
            record_merge(result, base=args.base, session=session_key(env))
            say("recorded landing merge of " + args.base + ": " + result)
            return 0
    except ValueError as error:
        say("intent " + action + ": " + str(error))
        return 2
    return 2

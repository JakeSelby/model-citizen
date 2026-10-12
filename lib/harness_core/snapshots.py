# SPDX-License-Identifier: MIT
"""Recoverable snapshots of a working tree and its index, taken before a risky command runs.

`grade-bash` grades every Bash command 0-3. Before one it rates 2 or more runs in a git working
tree, the hook records the tree's uncommitted state with `git stash create` and keeps the commit
under `refs/harness/snapshots/<time>` with `git update-ref`. Nothing in the tree or the index is
changed, nothing is discarded, and a later `git reset --hard`, `git checkout -- .` or `rm` of a
tracked file can be undone with `harness snapshot restore`.

What a snapshot holds is what `git stash create` holds: tracked files' working-tree changes and
the index. Untracked and ignored files are not in it, and a clean tree takes none. The refs are
shared by every worktree of the repository; each snapshot's subject names the worktree it came
from, and it is restored into whichever worktree `restore` runs in.

Every git process here runs hardened, as the grader's own do (`GIT_HARDENED` in
`policy/hooks/bash-grader.py`): no hooks, no fsmonitor, no pager, no global or system
configuration, and every filter driver the repository defines switched off, so a configuration
an earlier command planted cannot make taking a snapshot run a program. With filters off the
snapshot keeps the files' bytes as they sit in the tree, and `restore` writes them back the same way.
"""
import datetime
import os
import re
import subprocess
from pathlib import Path

PREFIX = "refs/harness/snapshots/"
KEEP_DAYS = 14
TIMEOUT = 10
STAMP = "%Y%m%dT%H%M%S"
NAME = re.compile(r"^(\d{8}T\d{6})\.(\d{6})Z$")
IDENTITY = ("harness snapshot", "harness-snapshot")

HARDENED = ["--no-pager", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
            "-c", "core.attributesFile=/dev/null", "-c", "core.pager=cat",
            "-c", "core.sshCommand=false", "-c", "credential.helper="]


def _env():
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith("GIT_")
           and k.upper() not in ("PAGER", "EDITOR", "VISUAL", "SSH_ASKPASS", "LESSOPEN", "LESSCLOSE")}
    env.update(GIT_OPTIONAL_LOCKS="0", LC_ALL="C", GIT_TERMINAL_PROMPT="0", GIT_ATTR_NOSYSTEM="1",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    # With the global configuration off there is no user.email, and `stash create` writes a
    # commit; a fixed identity keeps it from failing on a machine with no domain to guess.
    env.update(GIT_AUTHOR_NAME=IDENTITY[0], GIT_AUTHOR_EMAIL=IDENTITY[1],
               GIT_COMMITTER_NAME=IDENTITY[0], GIT_COMMITTER_EMAIL=IDENTITY[1])
    return env


def _git(where, args, extra=()):
    try:
        return subprocess.run(["git"] + HARDENED + list(extra) + ["-C", str(where)] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              stdin=subprocess.DEVNULL, timeout=TIMEOUT, env=_env())
    except (OSError, subprocess.TimeoutExpired):
        return None


def _filters_off(where):
    """`-c` pairs that switch off every filter driver the repository's configuration defines."""
    found = _git(where, ["config", "-z", "--name-only", "--get-regexp", r"^filter\."])
    off = []
    if found is None or found.returncode != 0:
        return off
    for key in set(found.stdout.decode("utf-8", "replace").split("\0")):
        name = key.strip()[len("filter."):].rpartition(".")[0]
        if name:
            for setting in ("clean", "smudge", "process"):
                off += ["-c", "filter.%s.%s=" % (name, setting)]
            off += ["-c", "filter.%s.required=false" % name]
    return off


def toplevel(where):
    """The root of the working tree `where` is in, or None outside one (a bare repository too)."""
    if not where or not Path(where).is_dir():
        return None
    done = _git(where, ["rev-parse", "--is-inside-work-tree", "--show-toplevel"])
    if done is None or done.returncode != 0:
        return None
    lines = done.stdout.decode("utf-8", "replace").splitlines()
    return lines[1] if len(lines) == 2 and lines[0] == "true" else None


def ref_name(now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return PREFIX + now.strftime(STAMP) + ".%06dZ" % now.microsecond


def taken_at(ref):
    """The UTC time a snapshot ref was taken, from its name, or None for a name not ours."""
    match = NAME.match(ref[len(PREFIX):]) if ref.startswith(PREFIX) else None
    if not match:
        return None
    stamp = datetime.datetime.strptime(match.group(1), STAMP)
    return stamp.replace(microsecond=int(match.group(2)), tzinfo=datetime.timezone.utc)


def take(where, label="", now=None, keep_days=KEEP_DAYS):
    """Snapshot the working tree at `where`; returns the ref written, or None when there is
    nothing to take: no working tree, a clean one, or a git that did not answer in time.
    Snapshots older than `keep_days` are pruned after a new one is written."""
    root = toplevel(where)
    if root is None:
        return None
    message = "harness snapshot in " + root + (": " + label if label else "")
    made = _git(root, ["stash", "create", message], extra=_filters_off(root))
    sha = made.stdout.decode().strip() if made is not None and made.returncode == 0 else ""
    if not sha:
        return None
    ref = ref_name(now)
    # An empty old value makes the write fail rather than replace a snapshot of the same instant.
    written = _git(root, ["update-ref", "-m", "harness snapshot", ref, sha, ""])
    if written is None or written.returncode != 0:
        return None
    if keep_days is not None:
        prune(root, keep_days, now=now)
    return ref


def entries(where):
    """Every snapshot in the repository at `where`, newest first: ref, sha, time and subject."""
    root = toplevel(where) or where
    done = _git(root, ["for-each-ref", "--format=%(refname)%00%(objectname)%00%(contents:subject)",
                       PREFIX])
    if done is None or done.returncode != 0:
        return []
    found = []
    for line in done.stdout.decode("utf-8", "replace").splitlines():
        ref, sha, subject = (line.split("\0") + ["", ""])[:3]
        when = taken_at(ref)
        if when is not None:
            found.append({"ref": ref, "sha": sha, "time": when, "subject": subject})
    return sorted(found, key=lambda item: item["time"], reverse=True)


def find(where, name):
    """The snapshot `name` names: `latest`, a full ref or its time suffix. None when none does."""
    found = entries(where)
    if name in (None, "", "latest"):
        return found[0] if found else None
    for item in found:
        if name in (item["ref"], item["ref"][len(PREFIX):], item["sha"]):
            return item
    return None


def restore(where, name="latest"):
    """Apply a snapshot to the working tree at `where`. Returns (ok, message). The index is
    restored too when it applies cleanly; otherwise the working-tree changes alone are applied,
    as `git stash apply` without `--index` would. The snapshot ref is kept either way."""
    root = toplevel(where)
    if root is None:
        return False, str(where) + " is not inside a git working tree"
    item = find(root, name)
    if item is None:
        return False, "no snapshot named " + str(name or "latest")
    off = _filters_off(root)
    done = _git(root, ["stash", "apply", "--index", item["sha"]], extra=off)
    if done is not None and done.returncode != 0:
        done = _git(root, ["stash", "apply", item["sha"]], extra=off)
    if done is None:
        return False, "git did not finish applying " + item["ref"]
    if done.returncode != 0:
        return False, (done.stderr or done.stdout).decode("utf-8", "replace").strip()
    return True, "restored " + item["ref"] + " into " + root


def prune(where, days, now=None):
    """Delete snapshots taken more than `days` days before `now`; returns the refs deleted."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=days)
    gone = []
    for item in entries(where):
        if item["time"] < cutoff:
            done = _git(toplevel(where) or where, ["update-ref", "-d", item["ref"], item["sha"]])
            if done is not None and done.returncode == 0:
                gone.append(item["ref"])
    return gone


def command(args, say):
    """`harness snapshot list|restore|prune`. Returns the exit status."""
    where = args.dir or os.getcwd()
    if toplevel(where) is None:
        say(str(where) + " is not inside a git working tree")
        return 1
    if args.snapshot_action == "list":
        found = entries(where)
        if not found:
            say("no snapshots")
        for item in found:
            say(item["ref"][len(PREFIX):] + "  " + item["sha"][:12] + "  " + item["subject"])
        return 0
    if args.snapshot_action == "restore":
        ok, message = restore(where, args.name)
        say(message)
        return 0 if ok else 1
    if args.days < 0:
        say("--days is zero or more")
        return 2
    gone = prune(where, args.days)
    say("pruned " + str(len(gone)) + " snapshot(s) older than " + str(args.days) + " day(s)")
    return 0

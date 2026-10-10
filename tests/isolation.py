# SPDX-License-Identifier: MIT
"""Environment isolation shared by the tests that run `bin/harness`.

`claude_dir()` prefers `CLAUDE_CONFIG_DIR` over the home a test controls, which is the right
runtime precedence: it is how a profile other than `~/.claude` is synced. It also means a value
inherited from the caller's shell silently overrides a temporary `HOME`, so a sync test writes
the harness into a real profile instead of its own fixture. The suite therefore drops the
variable everywhere it builds an environment, and importing this module drops it from the test
process once, before any test runs.

The same applies to the home. Every hook and harness module resolves its user state (the
decision log, the usage ledger, approvals, intent claims, the session registry) from
`HARNESS_HOME`, else `HOME`, so a test that runs a hook without pointing `HOME` somewhere
temporary appends its rows to the user's real `~/.local/state/agent-harness`. Importing this
module therefore moves the whole test process onto a disposable home, `SUITE_HOME`, with
`HARNESS_*` dropped. A test that sets its own home still does; one that forgets lands here.
`REAL_HOME` is the account's home from the password database, kept so the guard test can prove
nothing reaches it.
"""
import atexit
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

CONFIG_DIR = "CLAUDE_CONFIG_DIR"


def drop_inherited_config_dir():
    """Remove an inherited config dir from this process. Returns the value that was dropped."""
    return os.environ.pop(CONFIG_DIR, None)


def without_config_dir(env=None):
    """A mutable copy of `env` (default `os.environ`) with an inherited config dir removed."""
    clean = dict(os.environ if env is None else env)
    clean.pop(CONFIG_DIR, None)
    return clean


def isolate_home(home, quiet=True):
    """Point `os.environ` at a temporary home: `HOME` set, `HARNESS_*` and the config dir gone.

    Callers save and restore `os.environ` themselves; this only applies the isolation.
    """
    os.environ["HOME"] = str(home)
    for key in list(os.environ):
        if key.startswith("HARNESS_"):
            del os.environ[key]
    drop_inherited_config_dir()
    if quiet:
        os.environ["HARNESS_QUIET"] = "1"


def without_harness_vars(env=None):
    """`without_config_dir`, with the `HARNESS_*` variables dropped as well.

    The base for a subprocess a test drives, so neither the config dir nor a stance set in the
    developer's shell reaches it. The caller adds the home and whatever else it means to set.
    """
    clean = without_config_dir(env)
    for key in [name for name in clean if name.startswith("HARNESS_")]:
        del clean[key]
    return clean


# Git's automatic maintenance can detach a background process that is still writing into `.git`
# when a test removes its temporary repository, so the cleanup fails with "Directory not empty".
# Every git child that inherits this process's environment runs with both settings off.
QUIET_GIT_CONFIG = (("maintenance.auto", "false"), ("gc.auto", "0"))


def quiet_git_maintenance(env=None):
    """Add `QUIET_GIT_CONFIG` to `env` (default `os.environ`) as `GIT_CONFIG_KEY_n` entries.

    Entries already in the environment are kept and these are appended after them. Git uses the
    last value for a key, so a setting is skipped only when it is already that key's last value.
    Returns the environment it changed.
    """
    env = os.environ if env is None else env
    try:
        count = int(env.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        count = 0
    last = {}
    for i in range(count):
        last[env.get("GIT_CONFIG_KEY_%d" % i)] = env.get("GIT_CONFIG_VALUE_%d" % i)
    for key, value in QUIET_GIT_CONFIG:
        if last.get(key) != value:
            env["GIT_CONFIG_KEY_%d" % count] = key
            env["GIT_CONFIG_VALUE_%d" % count] = value
            count += 1
    env["GIT_CONFIG_COUNT"] = str(count)
    return env


def real_home():
    """The account's home from the password database: where an unisolated hook would write."""
    return pwd.getpwuid(os.getuid()).pw_dir


def isolate_suite():
    """Point this process at a disposable home for the rest of the run. Returns that home.

    Called once, when this module is first imported; the directory is removed at exit.
    """
    home = tempfile.mkdtemp(prefix="harness-suite-home-")
    atexit.register(shutil.rmtree, home, True)
    for key in [name for name in os.environ if name.startswith("HARNESS_")]:
        del os.environ[key]
    drop_inherited_config_dir()
    os.environ["HOME"] = home
    return home


REAL_HOME = real_home()
SUITE_HOME = isolate_suite()


def stub_presence():
    """No test raises the real Touch ID or password dialog (`harness_core.presence`).

    `MODEL_CITIZEN_PRESENCE_OFF` makes every child process refuse the check, and in this process
    `presence.confirm` answers as a person who said yes, so a test of a spend or apply runs as it
    did; a test of a refusal patches `confirm` to False. A child that must pass runs through
    `presence_support.present_cli`."""
    os.environ["MODEL_CITIZEN_PRESENCE_OFF"] = "1"
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
    from harness_core import presence
    from unittest import mock

    patch = mock.patch.object(presence, "confirm", return_value=True)
    # Entered, not `start()`ed: a started patch is one `mock.patch.stopall` stops, and a test
    # that cleans up with it would switch the stub off for every test after it.
    patch.__enter__()
    return patch


# `PRESENCE.temp_original` is the real `confirm`, for the tests of the check itself.
PRESENCE = stub_presence()
quiet_git_maintenance()

# Studio tests read only their own fixtures (#1211). Once this module is imported (every
# `unittest discover` run, and any test importing `test_harness` or this module), the guard
# below records every path a Studio test (a `test_studio*.py` file) opens, lists or stats under
# the account's real home, outside this checkout, its Git directory and the interpreter, and
# fails that test when it finishes, whatever order the tests run in. It sees Python-level opens,
# listings (audit events) and stat probes (`os.stat`, `os.lstat`, `os.path.exists`, `isdir`,
# `isfile`, wrapped here); a read made by a subprocess, such as `git -C <path> archive`, is NOT
# covered. A stat probe, which reads no content, is also allowed on the environment the suite
# runs in: an ancestor folder of an allowed root, a `PATH` folder, an executable in one and its
# resolved target, and a working tree this repository's Git directory registers or its ancestors. A thread a Studio test starts, and the threads those start, carry its attribution.
# A Studio test that found evaluator packs beside the checkout read `~/repos` this way.
CHECKOUT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STUDIO_TEST_PREFIX = "test_studio"
STUDIO_HOME_TOUCHES = []  # (test file, event, resolved path)
_HOME = os.path.realpath(REAL_HOME)


def _git_common_dir(root):
    try:
        done = subprocess.run(["git", "-C", root, "rev-parse", "--path-format=absolute",
                               "--git-common-dir"], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, universal_newlines=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else None


def _within(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _allowed_roots():
    """The checkout, its Git directory and the interpreter's own folders. A `sys.path` entry
    counts only when it is a package folder, and no root may hold the home itself, so running
    from `~` or a `PYTHONPATH` into `~/repos` cannot switch the guard off."""
    prefixes = [sys.prefix, sys.base_prefix, sys.exec_prefix,
                os.path.dirname(os.path.abspath(sys.executable))]
    roots = [CHECKOUT, _git_common_dir(CHECKOUT)] + prefixes
    roots += [entry for entry in sys.path if entry and os.path.isabs(entry)
              and os.path.basename(entry.rstrip(os.sep)) in ("site-packages", "dist-packages")]
    resolved = {os.path.realpath(root) for root in roots if root}
    return tuple(sorted(root for root in resolved if not _within(_HOME, root)))


ALLOWED_ROOTS = _allowed_roots()
_ALLOWED_ABS = tuple(sorted({os.path.abspath(root) for root in ALLOWED_ROOTS}
                            | {CHECKOUT}))
_HOME_PREFIXES = tuple(sorted({REAL_HOME.rstrip(os.sep) + os.sep, _HOME.rstrip(os.sep) + os.sep}))
_local = threading.local()


def outside_fixtures(path):
    """True when `path` resolves under the real home but outside the allowed roots: somewhere a
    Studio test has no business reading. Both sides are compared resolved."""
    full = os.path.abspath(path)
    if any(_within(full, root) for root in _ALLOWED_ABS):
        return False
    if not (full + os.sep).startswith(_HOME_PREFIXES):
        return False
    full = os.path.realpath(full)
    return _within(full, _HOME) and not any(_within(full, root) for root in ALLOWED_ROOTS)


_STAT_EVENTS = frozenset(("os.stat", "os.lstat", "os.path.exists", "os.path.isdir",
                          "os.path.isfile"))
_environment = {}
_WORKTREE_REFRESH = 30.0


def _path_targets():
    """`PATH` folders and the resolved targets of the executables in them, once."""
    if "path" not in _environment:
        folders, targets = set(), set()
        for folder in os.environ.get("PATH", "").split(os.pathsep):
            if not folder or not os.path.isabs(folder):
                continue
            folders.add(os.path.realpath(folder))
            try:
                names = os.listdir(folder)
            except OSError:
                continue
            for name in names:
                entry = os.path.join(folder, name)
                if os.path.islink(entry):
                    targets.add(os.path.realpath(entry))
        _environment["path"] = (tuple(folders), frozenset(targets))
    return _environment["path"]


def _registered_worktrees(refresh=False):
    """The working trees this repository's Git directory registers, main checkout included;
    re-read at most every `_WORKTREE_REFRESH` seconds, since listing them runs Git."""
    now = time.monotonic()
    if refresh and now - _environment.get("worktrees_at", -_WORKTREE_REFRESH) < _WORKTREE_REFRESH:
        refresh = False
    if refresh or "worktrees" not in _environment:
        _environment["worktrees_at"] = now
        try:
            done = subprocess.run(["git", "-C", CHECKOUT, "worktree", "list", "--porcelain"],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  universal_newlines=True, timeout=10)
            listed = [line[len("worktree "):] for line in done.stdout.splitlines()
                      if line.startswith("worktree ")]
        except (OSError, subprocess.SubprocessError):
            listed = []
        _environment["worktrees"] = frozenset(os.path.realpath(item) for item in listed)
    return _environment["worktrees"]


def _environment_probe(path):
    """True when a stat of `path` only probes the environment the suite runs in."""
    if any(_within(root, path) for root in ALLOWED_ROOTS):
        return True  # an ancestor of the checkout, its Git directory or the interpreter
    folders, targets = _path_targets()
    if path in targets or any(_within(path, folder) for folder in folders) \
            or any(_within(target, path) for target in targets):
        return True
    for refresh in (False, True):  # a working tree or one of its ancestor folders
        if any(_within(tree, path) for tree in _registered_worktrees(refresh)):
            return True
    return False


def _studio_test_on_stack(depth=2):
    frame = sys._getframe(depth)
    while frame is not None:
        name = os.path.basename(frame.f_code.co_filename)
        if name.startswith(STUDIO_TEST_PREFIX) and name.endswith(".py"):
            return name
        frame = frame.f_back
    return None


def _attributed():
    return _studio_test_on_stack(3) or getattr(threading.current_thread(), "_studio_test", None)


def _record(event, path):
    if getattr(_local, "busy", False):
        return  # the guard's own path resolution
    _local.busy = True
    try:
        if isinstance(path, bytes):
            path = os.fsdecode(path)
        elif hasattr(path, "__fspath__"):
            path = os.fspath(path)
        if not isinstance(path, str) or not outside_fixtures(path):
            return
        test = _attributed()
        if test is None:
            return
        resolved = os.path.realpath(os.path.abspath(path))
        if event in _STAT_EVENTS and _environment_probe(resolved):
            return
        STUDIO_HOME_TOUCHES.append((test, event, resolved))
    except Exception:  # the guard must never break the operation it watches
        return
    finally:
        _local.busy = False


def _audit(event, args):
    if event in ("open", "os.listdir", "os.scandir") and args:
        _record(event, args[0])


def _watch(module, name, event):
    original = getattr(module, name)

    def watched(path, *args, **kwargs):
        _record(event, path)
        return original(path, *args, **kwargs)
    watched.__wrapped__ = original
    setattr(module, name, watched)


for _module, _name in ((os, "stat"), (os, "lstat"), (os.path, "exists"), (os.path, "isdir"),
                       (os.path, "isfile")):
    _watch(_module, _name, "%s.%s" % ("os" if _module is os else "os.path", _name))

_thread_start = threading.Thread.start


def _start_attributed(self, *args, **kwargs):
    """A thread carries the Studio test that started it, or its starter's attribution."""
    self._studio_test = (_studio_test_on_stack(2)
                         or getattr(threading.current_thread(), "_studio_test", None))
    return _thread_start(self, *args, **kwargs)


threading.Thread.start = _start_attributed
_test_run = unittest.TestCase.run


def _run_guarded(self, result=None):
    """Run the test; a Studio test that read the real home fails, named with each path."""
    before = len(STUDIO_HOME_TOUCHES)
    result = _test_run(self, result)
    module = sys.modules.get(type(self).__module__)
    source = os.path.basename(getattr(module, "__file__", "") or "")
    found = sorted(set(STUDIO_HOME_TOUCHES[before:]))
    if source.startswith(STUDIO_TEST_PREFIX) and found and result is not None:
        try:
            raise AssertionError("read the real home outside its fixtures (build a fixture "
                                 "instead): %s" % "; ".join("%s %s" % (event, path)
                                                             for _test, event, path in found))
        except AssertionError:
            result.addFailure(self, sys.exc_info())
    return result


unittest.TestCase.run = _run_guarded
sys.addaudithook(_audit)

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
quiet_git_maintenance()

# Studio tests read only their own fixtures (#1211). An audit hook records every path a Studio
# test opens or lists under the real home outside this checkout, its Git directory and the
# interpreter; `test_studio_zz_home_guard` fails on any record. A Studio test that found an
# evaluator pack beside the checkout read the user's `~/repos/model-citizen-evals` this way.
CHECKOUT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STUDIO_TEST_PREFIX = "test_studio"
STUDIO_HOME_TOUCHES = []  # (test file, event, path)


def _git_common_dir(root):
    try:
        done = subprocess.run(["git", "-C", root, "rev-parse", "--path-format=absolute",
                               "--git-common-dir"], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, universal_newlines=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else None


def _allowed_roots():
    roots = [CHECKOUT, _git_common_dir(CHECKOUT), sys.prefix, sys.base_prefix,
             sys.exec_prefix, os.path.dirname(os.path.abspath(sys.executable))]
    roots += [entry for entry in sys.path if entry and os.path.isabs(entry)
              and not entry.startswith(CHECKOUT)]
    return tuple(sorted({os.path.realpath(root) for root in roots if root}))


ALLOWED_ROOTS = _allowed_roots()
_HOME_PREFIXES = tuple(sorted({REAL_HOME.rstrip(os.sep) + os.sep,
                               os.path.realpath(REAL_HOME).rstrip(os.sep) + os.sep}))


def outside_fixtures(path):
    """True when `path` is under the real home but outside the checkout, its Git directory and
    the interpreter: somewhere a Studio test has no business reading."""
    full = os.path.abspath(path)
    if not (full + os.sep).startswith(_HOME_PREFIXES):
        return False
    full = os.path.realpath(full)
    return not any(full == root or full.startswith(root.rstrip(os.sep) + os.sep)
                   for root in ALLOWED_ROOTS)


def _studio_test_on_stack():
    frame = sys._getframe(2)
    while frame is not None:
        name = os.path.basename(frame.f_code.co_filename)
        if name.startswith(STUDIO_TEST_PREFIX) and name.endswith(".py"):
            return name
        frame = frame.f_back
    return None


def _audit(event, args):
    if event not in ("open", "os.listdir", "os.scandir") or not args:
        return
    try:
        path = args[0]
        if isinstance(path, bytes):
            path = os.fsdecode(path)
        elif hasattr(path, "__fspath__"):
            path = os.fspath(path)
        if not isinstance(path, str) or not outside_fixtures(path):
            return
        test = _studio_test_on_stack()
        if test is not None:
            STUDIO_HOME_TOUCHES.append((test, event, path))
    except Exception:  # an audit hook must never break the operation it watches
        return


sys.addaudithook(_audit)

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

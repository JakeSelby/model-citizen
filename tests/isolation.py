# SPDX-License-Identifier: MIT
"""Environment isolation shared by the tests that run `bin/harness`.

`claude_dir()` prefers `CLAUDE_CONFIG_DIR` over the home a test controls, which is the right
runtime precedence: it is how a profile other than `~/.claude` is synced. It also means a value
inherited from the caller's shell silently overrides a temporary `HOME`, so a sync test writes
the harness into a real profile instead of its own fixture. The suite therefore drops the
variable everywhere it builds an environment, and importing this module drops it from the test
process once, before any test runs.
"""
import os

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

    Entries already in the environment are kept and these are appended after them; a setting
    already present is not added twice. Returns the environment it changed.
    """
    env = os.environ if env is None else env
    try:
        count = int(env.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        count = 0
    present = set((env.get("GIT_CONFIG_KEY_%d" % i), env.get("GIT_CONFIG_VALUE_%d" % i))
                  for i in range(count))
    for key, value in QUIET_GIT_CONFIG:
        if (key, value) not in present:
            env["GIT_CONFIG_KEY_%d" % count] = key
            env["GIT_CONFIG_VALUE_%d" % count] = value
            count += 1
    env["GIT_CONFIG_COUNT"] = str(count)
    return env


drop_inherited_config_dir()
quiet_git_maintenance()

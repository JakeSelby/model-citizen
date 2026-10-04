# SPDX-License-Identifier: MIT
"""No test writes the user's real harness state (#1202).

Hooks resolve every state file from `HARNESS_HOME`, else `HOME`. A test that ran a hook without a
temporary `HOME` used to append its rows, under session ids such as `s` and `x`, to the user's
real `~/.local/state/agent-harness/decisions.jsonl`. `isolation` now moves the whole test process
onto a disposable home when it is imported; these tests pin that, and run the entry point that
leaked with no home of their own to prove its row lands in the suite home and not the real one.

The real log is shared with every live session on the machine, so the check is not its size: it
is that a row carrying this run's unique session id never appears in what was appended to it.

Run: python3 -m unittest discover tests
"""
import importlib.util
import json
import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

import isolation
from test_harness import REPO  # noqa: F401  (puts lib/ on the path)
from test_native_acceptance import MODULE
from harness_core import intents, lifecycle, observer

HOOKS = REPO / "policy" / "hooks"
REAL_STATE = Path(isolation.REAL_HOME) / ".local" / "state" / "agent-harness"
REAL_LOG = REAL_STATE / "decisions.jsonl"


def hook(name):
    spec = importlib.util.spec_from_file_location("suite_isolation_" + name.replace("-", "_"),
                                                  str(HOOKS / (name + ".py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def appended_since(path, offset):
    """What was appended to `path` after `offset` bytes, or "" when it does not exist."""
    try:
        with open(str(path), "rb") as stream:
            stream.seek(offset)
            return stream.read().decode("utf-8", "replace")
    except OSError:
        return ""


def size(path):
    try:
        return path.stat().st_size
    except OSError:
        return 0


class SuiteHomeTests(unittest.TestCase):
    def test_the_suite_runs_under_a_temporary_home_that_is_not_the_users(self):
        self.assertEqual(os.environ["HOME"], isolation.SUITE_HOME)
        self.assertNotEqual(os.path.realpath(isolation.SUITE_HOME),
                            os.path.realpath(isolation.REAL_HOME))
        redirects = ("HARNESS_HOME", "HARNESS_OBSERVATION_LEDGER", "HARNESS_OBSERVATION_ERRORS")
        self.assertFalse([name for name in redirects if name in os.environ])

    def test_every_state_writer_resolves_under_the_suite_home(self):
        resolved = {
            "decisions": hook("decisions").path(),
            "usage-log": hook("usage-log").usage_path(),
            "approvals": hook("approvals").store_dir(),
            "adherence": hook("adherence").path(),
            "posture": hook("posture").state_dir(),
            "intents": intents.state_dir(),
            "observer": observer.ledger_path(),
        }
        suite = os.path.realpath(isolation.SUITE_HOME)
        for name, path in resolved.items():
            with self.subTest(writer=name):
                self.assertTrue(os.path.realpath(str(path)).startswith(suite + os.sep), path)


class LeakedEntryPointTests(unittest.TestCase):
    """The calls that wrote the polluted rows, made with no home of the test's own."""

    def setUp(self):
        self.session = "suite-isolation-" + uuid.uuid4().hex
        self.offset = size(REAL_LOG)
        self.suite_log = Path(isolation.SUITE_HOME) / ".local" / "state" / "agent-harness" \
            / "decisions.jsonl"

    def assertOnlyInTheSuiteLog(self):
        self.assertIn(self.session, appended_since(self.suite_log, 0))
        self.assertNotIn(self.session, appended_since(REAL_LOG, self.offset))

    def test_a_framework_refusal_is_logged_in_the_suite_home(self):
        _, spawn = MODULE.descriptor_spawn()
        answer = lifecycle.framework_deny("claude-code", self.session, " ".join(spawn["phrases"]),
                                          None)
        self.assertEqual(answer["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertOnlyInTheSuiteLog()

    def test_a_hook_run_as_a_subprocess_inherits_the_suite_home(self):
        script = ("import importlib.util, sys\n"
                  "spec = importlib.util.spec_from_file_location('d', sys.argv[1])\n"
                  "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
                  "m.record('grade-bash', 'ask', 'true', {'session_id': sys.argv[2]}, "
                  "'claude-code')\n")
        subprocess.run([sys.executable, "-c", script, str(HOOKS / "decisions.py"), self.session],
                       check=True, env=dict(os.environ))
        self.assertOnlyInTheSuiteLog()
        rows = [json.loads(line) for line in appended_since(self.suite_log, 0).splitlines()]
        self.assertTrue(any(row.get("session_id") == self.session for row in rows))


if __name__ == "__main__":
    unittest.main()

"""Git's automatic maintenance stays out of every temporary repository (#1164).

A background maintenance run still writing into `.git` makes a temporary directory's cleanup fail
with "Directory not empty". The suite turns it off for every git child through `isolation`, and
`replay_pack.materialize` turns it off in the repositories it creates, whose git runs without this
process's environment."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import isolation
from test_replay_pack import PACK, make_pack


def config(repo, key, env=None, scope=()):
    done = subprocess.run(["git", "-C", str(repo), "config"] + list(scope) + [key], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return done.stdout.decode().strip()


def without_config_entries():
    return dict((k, v) for k, v in os.environ.items() if not k.startswith("GIT_CONFIG_"))


class SuiteEnvironmentTests(unittest.TestCase):
    def test_a_repository_a_test_creates_runs_with_maintenance_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            self.assertEqual(config(tmp, "maintenance.auto"), "false")
            self.assertEqual(config(tmp, "gc.auto"), "0")

    def test_entries_already_in_the_environment_are_kept_and_none_is_added_twice(self):
        env = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor",
               "GIT_CONFIG_VALUE_0": "false"}
        isolation.quiet_git_maintenance(env)
        isolation.quiet_git_maintenance(env)
        self.assertEqual(env["GIT_CONFIG_COUNT"], "3")
        self.assertEqual(env["GIT_CONFIG_KEY_0"], "core.fsmonitor")
        self.assertEqual([(env["GIT_CONFIG_KEY_%d" % i], env["GIT_CONFIG_VALUE_%d" % i])
                          for i in (1, 2)], list(isolation.QUIET_GIT_CONFIG))

    def test_a_later_value_that_turns_maintenance_back_on_is_overridden(self):
        env = {"GIT_CONFIG_COUNT": "2",
               "GIT_CONFIG_KEY_0": "maintenance.auto", "GIT_CONFIG_VALUE_0": "false",
               "GIT_CONFIG_KEY_1": "maintenance.auto", "GIT_CONFIG_VALUE_1": "true"}
        isolation.quiet_git_maintenance(env)
        count = int(env["GIT_CONFIG_COUNT"])
        last = {}
        for i in range(count):
            last[env["GIT_CONFIG_KEY_%d" % i]] = env["GIT_CONFIG_VALUE_%d" % i]
        self.assertEqual(last["maintenance.auto"], "false")
        self.assertEqual(last["gc.auto"], "0")


class MaterializeTests(unittest.TestCase):
    def test_a_materialized_workspace_keeps_maintenance_off_in_its_own_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = PACK.open_pack(make_pack(Path(tmp) / "pack"), harness_root=Path(tmp) / "harness")
            self.addCleanup(PACK.close_pack, pack)
            task = PACK.load_set(pack, "production", "production")[0][0]
            repo = PACK.materialize(task, Path(tmp) / "work" / "repo")
            env = without_config_entries()
            self.assertEqual(config(repo, "maintenance.auto", env, ["--local"]), "false")
            self.assertEqual(config(repo, "gc.auto", env, ["--local"]), "0")


if __name__ == "__main__":
    unittest.main()

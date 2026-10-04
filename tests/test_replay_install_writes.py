"""The harness install's own writes in a replay arm (#1155): installing the harness leaves a global
git ignore file, the sync's ownership record, its empty lock file and an empty plans directory.
The pair check attributes exactly those to the harness component and still refuses anything else
the harness arm holds that the bare arm does not."""
import copy
import unittest

from test_cost_bench import BENCH, arm_record

ARMS = BENCH.arms

# What a sync at 59520dd3 left in the harness arm that the bare arm lacks, as the manifest lists it.
INSTALL_WRITES = [
    {"path": "home:.claude", "kind": "dir", "mode": "0755"},
    {"path": "home:.claude/plans", "kind": "dir", "mode": "0755"},
    {"path": "home:.config", "kind": "dir", "mode": "0755"},
    {"path": "home:.config/git", "kind": "dir", "mode": "0755"},
    {"path": "home:.config/git/ignore", "kind": "file", "mode": "0644", "sha256": "1" * 64, "size": 65},
    {"path": "home:.local", "kind": "dir", "mode": "0755"},
    {"path": "home:.local/state", "kind": "dir", "mode": "0755"},
    {"path": "home:.local/state/agent-harness", "kind": "dir", "mode": "0755"},
    {"path": "home:.local/state/agent-harness/ownership.json", "kind": "file", "mode": "0600",
     "sha256": "2" * 64, "size": 78886},
    {"path": "home:.local/state/agent-harness/sync.lock", "kind": "file", "mode": "0644",
     "sha256": ARMS.EMPTY_SHA256, "size": 0},
]


def pair(*extra):
    bare, harness = copy.deepcopy(arm_record("bare")), copy.deepcopy(arm_record("harness"))
    harness["manifest"]["entries"] += copy.deepcopy(INSTALL_WRITES) + list(extra)
    return bare, harness


class InstallWritesTests(unittest.TestCase):
    def test_the_installs_own_writes_are_the_harness_component(self):
        bare, harness = pair()
        self.assertEqual(ARMS.pair_differences(bare, harness), [])
        ARMS.admit_pair(bare, harness)

    def test_an_unrelated_extra_file_in_the_harness_arm_is_still_refused(self):
        bare, harness = pair({"path": "home:.config/git/config", "kind": "file", "sha256": "3" * 64})
        with self.assertRaises(SystemExit) as stop:
            ARMS.admit_pair(bare, harness)
        self.assertIn("only in the harness arm, outside the harness component: home:.config/git/config",
                      str(stop.exception))

    def test_an_unrelated_empty_directory_in_the_harness_arm_is_still_refused(self):
        bare, harness = pair({"path": "home:.claude/drafts", "kind": "dir", "mode": "0755"})
        self.assertEqual(ARMS.pair_differences(bare, harness),
                         ["only in the harness arm, outside the harness component: home:.claude/drafts"])

    def test_a_lock_file_holding_bytes_is_not_the_harnesss(self):
        bare, harness = pair()
        lock = next(e for e in harness["manifest"]["entries"] if e["path"].endswith("sync.lock"))
        lock.update(sha256="4" * 64, size=5)
        self.assertEqual(ARMS.pair_differences(bare, harness), [
            "only in the harness arm, outside the harness component: "
            "home:.local/state/agent-harness/sync.lock"])

    def test_a_declared_directory_must_be_a_directory(self):
        bare, harness = pair()
        plans = next(e for e in harness["manifest"]["entries"] if e["path"] == "home:.claude/plans")
        plans.update(kind="file", sha256="5" * 64)
        self.assertIn("only in the harness arm, outside the harness component: home:.claude/plans",
                      ARMS.pair_differences(bare, harness))

    def test_the_bare_arm_holding_an_install_write_is_refused(self):
        bare, harness = pair()
        bare["manifest"]["entries"].append(
            {"path": "home:.config/git/ignore", "kind": "file", "mode": "0644", "sha256": "1" * 64, "size": 65})
        self.assertEqual(ARMS.pair_differences(bare, harness),
                         ["in the bare arm, inside the harness component: home:.config/git/ignore"])

    def test_a_directory_both_arms_hold_is_not_a_difference(self):
        bare, harness = pair()
        bare["manifest"]["entries"].append({"path": "home:.config", "kind": "dir", "mode": "0755"})
        self.assertEqual(ARMS.pair_differences(bare, harness), [])


if __name__ == "__main__":
    unittest.main()

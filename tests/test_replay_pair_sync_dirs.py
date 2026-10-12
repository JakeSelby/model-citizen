"""The directories a harness sync makes before it links anything, in a replay arm: an arm whose
selection switches off every skill (`no-skill-listing`) or every role (`no-agent-listing`) still
holds an empty `~/.claude/skills` or `~/.claude/agents`. The pair check attributes exactly the
directories the sync creates to the harness component and still refuses anything else."""
import copy
import re
import unittest

from test_cost_bench import BENCH, arm_record
from test_replay_install_writes import INSTALL_WRITES

ARMS = BENCH.arms


def pair(*extra):
    bare, harness = copy.deepcopy(arm_record("bare")), copy.deepcopy(arm_record("harness"))
    harness["manifest"]["entries"] += copy.deepcopy(INSTALL_WRITES) + list(extra)
    return bare, harness


def empty(path):
    return {"path": path, "kind": "dir", "mode": "0755"}


def sync_made_dirs():
    """The `~/.claude` subdirectories `bin/harness sync` creates unconditionally, read from its code."""
    source = (ARMS.ROOT / "bin" / "harness").read_text(encoding="utf-8")
    loop = re.search(r'for sub in \(([^)]*)\):\n\s*\(cd / sub\)\.mkdir', source)
    nested = re.findall(r'\(cd / "([^"]+)" / "([^"]+)"\)\.mkdir', source)
    assert loop, "bin/harness no longer creates the Claude home's directories in one loop"
    subs = re.findall(r'"([^"]+)"', loop.group(1))
    return {"home:.claude/" + sub for sub in subs} | {"home:.claude/%s/%s" % parts for parts in nested}


class SyncMadeDirectoryTests(unittest.TestCase):
    def test_an_empty_skills_directory_is_the_harness_component(self):
        bare, harness = pair(empty("home:.claude/skills"))
        self.assertEqual(ARMS.pair_differences(bare, harness), [])
        ARMS.admit_pair(bare, harness)

    def test_an_empty_agents_directory_is_the_harness_component(self):
        bare, harness = pair(empty("home:.claude/agents"))
        self.assertEqual(ARMS.pair_differences(bare, harness), [])

    def test_every_directory_the_sync_makes_is_declared(self):
        made = sync_made_dirs()
        self.assertIn("home:.claude/skills", made)
        self.assertEqual(made, set(ARMS.HARNESS_DIRS))

    def test_a_skill_that_is_not_the_harnesss_is_still_refused(self):
        bare, harness = pair(empty("home:.claude/skills"),
                             {"path": "home:.claude/skills/other", "kind": "link", "target": "/opt/other/skill"})
        self.assertEqual(ARMS.pair_differences(bare, harness),
                         ["only in the harness arm, outside the harness component: home:.claude/skills/other"])

    def test_a_file_where_a_declared_directory_belongs_is_still_refused(self):
        bare, harness = pair({"path": "home:.claude/skills", "kind": "file", "sha256": "6" * 64})
        self.assertEqual(ARMS.pair_differences(bare, harness),
                         ["only in the harness arm, outside the harness component: home:.claude/skills"])

    def test_an_undeclared_empty_directory_is_still_refused(self):
        bare, harness = pair(empty("home:.claude/commands"))
        self.assertEqual(ARMS.pair_differences(bare, harness),
                         ["only in the harness arm, outside the harness component: home:.claude/commands"])


if __name__ == "__main__":
    unittest.main()

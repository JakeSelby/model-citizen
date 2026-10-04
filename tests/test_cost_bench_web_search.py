"""A task may opt in to WebSearch (#1216): `allow_web_search: true` leaves it enabled in both arms,
WebFetch stays denied, the arm declaration's settings digest is unchanged, and every row says
which settings its run had. No container is started and no model called."""
import json
import tempfile
import unittest
from pathlib import Path

from test_cost_bench import BENCH, Launch, options
from test_cost_bench_pack import pack_task
from test_replay_pack import PACK, make_pack, task_spec

RESULT = json.dumps([dict(type="result", subtype="success", is_error=False, num_turns=1,
                          total_cost_usd=0.1, usage={})])


def settings_of(argv):
    return json.loads(argv[argv.index("--settings") + 1])


class TaskSettingsTests(unittest.TestCase):
    def test_off_by_default(self):
        self.assertIs(BENCH.task_settings({"id": "t"}), BENCH.ARM_SETTINGS)
        self.assertIs(BENCH.task_settings({"id": "t", "allow_web_search": False}), BENCH.ARM_SETTINGS)

    def test_opting_in_drops_only_websearch_from_the_deny_list(self):
        settings = BENCH.task_settings({"allow_web_search": True})
        self.assertEqual(settings["permissions"]["deny"], ["WebFetch"])
        self.assertEqual(settings["hooks"], BENCH.ARM_SETTINGS["hooks"])
        self.assertIn("WebSearch", BENCH.ARM_SETTINGS["permissions"]["deny"])  # the base is untouched

    def test_the_command_line_carries_the_tasks_settings(self):
        argv = BENCH.arm_command("claude", "m", "p", 1.0, 4, settings=BENCH.task_settings({"allow_web_search": True}))
        self.assertEqual(settings_of(argv)["permissions"]["deny"], ["WebFetch"])
        self.assertEqual(settings_of(BENCH.arm_command("claude", "m", "p"))["permissions"]["deny"],
                         ["WebFetch", "WebSearch"])

    def test_a_manifest_task_with_a_non_boolean_flag_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            task = {"id": "t", "kind": "synthetic", "parent_sha": "a", "good_sha": "b", "prompt": "p",
                    "tests": {}, "max_turns": 2, "allow_web_search": "yes"}
            path.write_text(json.dumps({"tasks": [task]}), encoding="utf-8")
            with self.assertRaises(SystemExit) as caught:
                BENCH.load_tasks(path)
        self.assertIn("allow_web_search must be true or false", str(caught.exception))


class TrialTests(unittest.TestCase):
    def run_arm(self, arm, allow):
        with tempfile.TemporaryDirectory() as tmp:
            pack, task = pack_task(tmp)
            self.addCleanup(PACK.close_pack, pack)
            if allow is not None:
                task = dict(task, allow_web_search=allow)
            launch = Launch([RESULT])
            row = BENCH.run_one(task, 1, arm, options(tmp), launch)
        argv = launch.calls[0][0]
        return row, settings_of(argv)

    def test_both_arms_get_websearch_when_the_task_opts_in(self):
        for arm in ("bare", "harness"):
            row, settings = self.run_arm(arm, True)
            self.assertEqual(settings["permissions"]["deny"], ["WebFetch"], arm)
            self.assertTrue(row["web_search"], arm)
            self.assertFalse(row["error"], row["error_kind"])

    def test_a_task_that_does_not_opt_in_keeps_both_web_tools_denied(self):
        row, settings = self.run_arm("harness", None)
        self.assertEqual(settings["permissions"]["deny"], ["WebFetch", "WebSearch"])
        self.assertFalse(row["web_search"])


class PackFieldTests(unittest.TestCase):
    def test_a_pack_task_carries_the_flag_and_refuses_a_non_boolean(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = PACK.open_pack(make_pack(Path(tmp) / "pack", tasks=[task_spec("web", allow_web_search=True)]),
                                  harness_root=Path(tmp) / "harness")
            self.addCleanup(PACK.close_pack, pack)
            self.assertTrue(PACK.load_set(pack, "production", "production")[0][0]["allow_web_search"])
        with tempfile.TemporaryDirectory() as tmp:
            pack = PACK.open_pack(make_pack(Path(tmp) / "pack", tasks=[task_spec("web", allow_web_search=1)]),
                                  harness_root=Path(tmp) / "harness")
            self.addCleanup(PACK.close_pack, pack)
            with self.assertRaises(SystemExit) as caught:
                PACK.load_set(pack, "production", "production")
        self.assertIn("allow_web_search must be true or false", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

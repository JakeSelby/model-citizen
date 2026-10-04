"""The micro tier reads Workflow launches as a mechanism (#1216): `workflow_launches` is an
above-zero field, so a task built to launch a Workflow scores whether one was launched. Rows and
streams here are recorded fixtures; no model is called."""
import importlib.util
import sys
import unittest

from test_harness import REPO
from test_cost_bench import BENCH
from test_replay_spawns import FIXTURES

sys.path.insert(0, str(REPO / "scripts"))
SPEC = importlib.util.spec_from_file_location("replay_micro", REPO / "scripts" / "replay_micro.py")
MICRO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MICRO)

TASK = {"id": "micro-workflow-hooks",
        "mechanism": {"id": "workflow-hooks", "fired": "above-zero", "field": "workflow_launches"}}


class WorkflowMechanismTests(unittest.TestCase):
    def test_workflow_launches_is_a_mechanism_field(self):
        self.assertEqual(MICRO.mechanism_errors(TASK, {}), [])

    def test_a_run_that_launched_a_workflow_fired_and_one_that_did_not_did_not(self):
        rows = [{"task": TASK["id"], "arm": "harness", "rep": 1, "workflow_launches": 3},
                {"task": TASK["id"], "arm": "bare", "rep": 1, "workflow_launches": 0},
                {"task": TASK["id"], "arm": "bare", "rep": 2, "workflow_launches": None}]
        self.assertEqual([r["mechanism_fired"] for r in MICRO.score_rows(rows, [TASK], [])], [True, False, None])

    def test_the_row_field_comes_from_the_stream(self):
        text = (FIXTURES / "workflow-harness.jsonl").read_text(encoding="utf-8")
        self.assertEqual(BENCH.parse_diagnostics(text)["workflow_launches"], 3)


if __name__ == "__main__":
    unittest.main()

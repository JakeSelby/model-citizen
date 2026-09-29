# SPDX-License-Identifier: MIT
"""Workflow status uses explicit journal identity and completion evidence."""
import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("workflow_status", ROOT / "primitives/skills/workflow-status/scripts/status.py")
STATUS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STATUS)


class WorkflowStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name) / "wf_exact"
        self.run.mkdir()
        self.agent = self.run / "agent-one.jsonl"
        self.agent.write_text(json.dumps({"message": {"content": "Task one"}}) + "\n")

    def journal(self, rows):
        (self.run / "journal.jsonl").write_text("\n".join(json.dumps(r) for r in rows))

    def report(self, terminal=None):
        out = io.StringIO()
        with redirect_stdout(out), patch.object(STATUS, "phases_from_script", return_value=[]):
            STATUS.report(self.run, terminal)
        return out.getvalue()

    def test_quiet_agent_without_result_is_pending_not_done(self):
        self.journal([{"type": "started", "key": "k", "agentId": "one"}])
        os.utime(self.agent, (1, 1))
        text = self.report()
        self.assertIn("pending", text)
        self.assertIn("UNKNOWN (no terminal signal)", text)
        self.assertNotIn("COMPLETE", text)

    def test_recent_agent_result_is_returned_but_run_needs_terminal_signal(self):
        self.journal([{"type": "started", "key": "k", "agentId": "one"},
                      {"type": "result", "key": "k", "result": "abc"}])
        text = self.report()
        self.assertIn("returned;", text)
        self.assertIn("1/1 returned", text)
        self.assertIn("UNKNOWN", text)
        self.assertIn("COMPLETED", self.report("completed"))

    def test_failed_agent_and_unknown_identity_are_not_inferred_from_age(self):
        self.journal([{"type": "started", "key": "k", "agentId": "one"},
                      {"type": "failed", "key": "k"}, [], {"type": "result", "key": []}])
        self.assertIn("failed;", self.report())
        self.journal([{"type": "result", "key": "unmapped", "result": "abc"}])
        self.assertIn("unknown;", self.report())

    def test_restarted_key_does_not_keep_an_old_result(self):
        self.journal([{"type": "result", "key": "k", "agentId": "one", "result": "abc"},
                      {"type": "started", "key": "k", "agentId": "one"}])
        self.assertIn("0/1 returned", self.report())
        self.assertIn("pending;", self.report())

    def test_run_filter_is_exact_and_terminal_signal_requires_a_run(self):
        other = self.run.with_name("wf_exact_extra")
        with patch.object(STATUS, "find_runs", return_value=[self.run, other]), \
                patch.object(STATUS, "report") as report, \
                patch("sys.argv", ["status", "--run", "wf_exact"]):
            self.assertEqual(STATUS.main(), 0)
            report.assert_called_once_with(self.run, None)
        with patch("sys.argv", ["status", "--terminal-status", "completed"]), \
                patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as error:
            STATUS.main()
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()

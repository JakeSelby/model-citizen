# SPDX-License-Identifier: MIT
"""The gate-run record, and the Stop-time check of a reply that claims the tests pass.

Every run of a repository's gate is recorded with its commit, tree, exit code and time, by the
stop-gate hook and by `citizen gate` alike. A final reply claiming a pass is blocked once when
the newest run is not green on the tree as it is now. The hooks and the CLI run as subprocesses
with HOME pointed at a temporary directory, so every record lands there.

Run: python3 -m unittest discover tests
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import isolation  # noqa: F401 -- keeps git maintenance out of temporary repositories

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "policy" / "hooks"
STOP_GATE = HOOKS / "stop-gate.py"
HARNESS = REPO / "bin" / "harness"
IDENTITY = "gate" + "@" + "example" + ".invalid"

spec = importlib.util.spec_from_file_location("gate_runs_under_test", HOOKS / "gate-runs.py")
runs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runs)


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.home = base / "home"
        self.home.mkdir()
        self.repo = base / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        (self.repo / "file.txt").write_text("one\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "initial")
        patcher = mock.patch.dict(os.environ, self.env())
        patcher.start()
        self.addCleanup(patcher.stop)

    def env(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("CLAUDE_CONFIG_DIR", "HARNESS_QUIET")
               and not k.startswith("HARNESS_STANCE_")}
        env.update({"HOME": str(self.home), "HARNESS_HOME": str(self.home),
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "Gate Fixture", "GIT_COMMITTER_NAME": "Gate Fixture",
                    "GIT_AUTHOR_EMAIL": IDENTITY, "GIT_COMMITTER_EMAIL": IDENTITY})
        return env

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], env=self.env(),
                              capture_output=True, text=True, check=True).stdout

    def write_gate(self, *commands, heading="## Gate", commit=True):
        (self.repo / "AGENTS.md").write_text(
            "# a repo\n\n" + heading + "\n\n```sh\n" + "\n".join(commands) + "\n```\n")
        if commit:
            self.git("add", "-A")
            self.git("commit", "-q", "-m", "gate")

    def trust(self):
        (self.home / ".claude.json").write_text(json.dumps(
            {"projects": {str(self.repo): {"hasTrustDialogAccepted": True}}}))

    def stop(self, message, session="s1"):
        body = {"session_id": session, "cwd": str(self.repo), "hook_event_name": "Stop",
                "last_assistant_message": message}
        out = subprocess.run([sys.executable, str(STOP_GATE)], input=json.dumps(body),
                             env=self.env(), capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout) if out.stdout.strip() else {}

    def citizen_gate(self):
        return subprocess.run([sys.executable, str(HARNESS), "gate", str(self.repo)],
                              env=self.env(), capture_output=True, text=True, timeout=120)

    def rows(self):
        log = runs.state_dir() / "runs.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    def decisions(self):
        log = self.home / ".local" / "state" / "agent-harness" / "decisions.jsonl"
        if not log.exists():
            return []
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        return [r for r in rows if r.get("kind") == "decision" and r.get("check") == "pass_claim"]


class Record(Fixture):
    def test_a_green_full_gate_run_on_a_clean_commit_is_kept_by_commit(self):
        snap = runs.snapshot(self.repo)
        row = runs.record(snap, "## Gate", 0, "test", now=100.0)
        self.assertEqual((row["status"], row["exit"], row["ts"]), ("passed", 0, 100.0))
        self.assertEqual(row["head"], self.git("rev-parse", "HEAD").strip())
        self.assertFalse(row["dirty"])
        self.assertEqual(runs.green_for(row["head"])["tree"], snap["tree"])
        self.assertEqual(runs.latest(self.repo)["source"], "test")
        self.assertEqual(len(self.rows()), 1)

    def test_a_stop_gate_subset_or_a_dirty_tree_is_never_green_for_a_commit(self):
        runs.record(runs.snapshot(self.repo), "## Stop gate", 0, "test")
        (self.repo / "file.txt").write_text("two\n")
        dirty = runs.snapshot(self.repo)
        self.assertTrue(dirty["dirty"])
        runs.record(dirty, "## Gate", 0, "test")
        self.assertIsNone(runs.green_for(dirty["head"]))
        self.assertEqual(len(self.rows()), 2)

    def test_an_untracked_scratch_file_does_not_make_the_commit_dirty(self):
        (self.repo / "scratch.txt").write_text("notes\n")
        self.assertFalse(runs.snapshot(self.repo)["dirty"])

    def test_a_run_is_fresh_until_the_tree_changes(self):
        runs.record(runs.snapshot(self.repo), "## Gate", 0, "test")
        self.assertTrue(runs.fresh_green(self.repo)[0])
        (self.repo / "new.txt").write_text("edit\n")
        fresh, last = runs.fresh_green(self.repo)
        self.assertFalse(fresh)
        self.assertEqual(last["status"], "passed")

    def test_a_failed_run_is_never_fresh(self):
        runs.record(runs.snapshot(self.repo), "## Gate", 2, "test")
        fresh, last = runs.fresh_green(self.repo)
        self.assertFalse(fresh)
        self.assertEqual((last["status"], last["exit"]), ("failed", 2))

    def test_the_log_is_trimmed_past_its_cap(self):
        snap = runs.snapshot(self.repo)
        with mock.patch.object(runs, "MAX_LOG_BYTES", 2000):
            for _ in range(20):
                runs.record(snap, "## Gate", 1, "test")
        self.assertLess((runs.state_dir() / "runs.jsonl").stat().st_size, 2000)


class Claims(unittest.TestCase):
    def test_a_plain_claim_is_found(self):
        for text in ("Done. All tests pass.", "The gate passed on HEAD.", "Tests are green.",
                     "The full suite passes locally.", "Gate: green", "Ran 42 tests in 3.1s, OK",
                     "Fixed the bug; the test suite now passes."):
            self.assertIsNotNone(runs.claims_pass(text), text)

    def test_a_negated_conditional_or_questioning_sentence_is_not_a_claim(self):
        for text in ("The tests do not pass yet.", "Tests don't pass on main.",
                     "Do the tests pass?", "Once the tests pass, I will push.",
                     "The gate failed, so the tests pass only with the flag.",
                     "Gate cannot pass: the suite needs a network.",
                     "I edited the README.", "", "Passing the test of time."):
            self.assertIsNone(runs.claims_pass(text), text)


class StopGateRecordsItsRuns(Fixture):
    def test_a_green_and_a_red_stop_gate_run_are_both_recorded(self):
        self.trust()
        self.write_gate("test -f file.txt")
        self.stop("done")
        (self.repo / "file.txt").unlink()
        self.assertEqual(self.stop("done").get("decision"), "block")
        found = [(r["source"], r["status"], r["exit"], r["heading"]) for r in self.rows()]
        self.assertEqual(found, [("stop-gate", "passed", 0, "## Gate"),
                                 ("stop-gate", "failed", 1, "## Gate")])
        self.assertTrue(all(r["head"] and r["tree"] and r["ts"] for r in self.rows()))


class PassClaimAtStop(Fixture):
    def test_a_claim_after_a_green_stop_gate_run_on_this_tree_ends_the_turn(self):
        self.trust()
        self.write_gate("true")
        self.assertEqual(self.stop("All tests pass."), {})
        self.assertEqual(self.decisions(), [])

    def test_a_claim_with_no_recorded_run_blocks_once_then_releases(self):
        self.write_gate("true")  # untrusted: the stop gate itself does not run
        first = self.stop("All tests pass.")
        self.assertEqual(first.get("decision"), "block")
        self.assertIn("no gate run is recorded", first["reason"])
        self.assertIn("All tests pass", first["reason"])
        self.assertEqual(self.stop("All tests pass."), {})
        answers = [(r["deterministic_answer"], r.get("last_run")) for r in self.decisions()]
        self.assertEqual(answers, [("blocked", "none"), ("released", "none")])

    def test_a_claim_after_an_edit_since_the_green_run_names_it(self):
        self.write_gate("true")
        self.assertEqual(self.citizen_gate().returncode, 0)
        self.assertEqual(self.stop("The gate passed."), {})
        (self.repo / "file.txt").write_text("edited\n")
        block = self.stop("The gate passed.")
        self.assertEqual(block.get("decision"), "block")
        self.assertIn("edited since", block["reason"])

    def test_a_claim_after_a_failed_run_names_the_failure(self):
        self.write_gate("exit 5")
        self.assertEqual(self.citizen_gate().returncode, 5)
        block = self.stop("Tests are green.")
        self.assertIn("failed (exit 5)", block["reason"])

    def test_a_reply_that_claims_nothing_is_not_checked(self):
        self.write_gate("true")
        self.assertEqual(self.stop("I updated the README."), {})
        self.assertEqual(self.decisions(), [])

    def test_the_one_shot_is_per_session_and_tree(self):
        self.write_gate("true")
        self.assertEqual(self.stop("Tests pass.", session="a").get("decision"), "block")
        self.assertEqual(self.stop("Tests pass.", session="b").get("decision"), "block")
        (self.repo / "x.txt").write_text("edit\n")
        self.assertEqual(self.stop("Tests pass.", session="a").get("decision"), "block")

    def test_a_repo_without_a_gate_is_not_checked(self):
        self.assertEqual(self.stop("All tests pass."), {})


class CitizenGate(Fixture):
    def test_a_green_run_is_recorded_for_the_commit(self):
        self.write_gate("true")
        out = self.citizen_gate()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("gate: passed", out.stdout)
        head = self.git("rev-parse", "HEAD").strip()
        self.assertEqual(runs.green_for(head)["source"], "citizen-gate")

    def test_a_red_run_exits_with_its_code_and_is_recorded(self):
        self.write_gate("exit 3")
        out = self.citizen_gate()
        self.assertEqual(out.returncode, 3)
        self.assertEqual([(r["status"], r["exit"]) for r in self.rows()], [("failed", 3)])

    def test_a_tree_changed_by_the_gate_is_unverified(self):
        self.write_gate("echo x > touched.txt")
        out = self.citizen_gate()
        self.assertEqual(out.returncode, 1)
        self.assertEqual(self.rows()[-1]["status"], "unverified")
        self.assertIsNone(runs.green_for(self.git("rev-parse", "HEAD").strip()))

    def test_a_repo_without_a_gate_block_is_refused(self):
        out = self.citizen_gate()
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("no `## Gate` block", out.stderr)
        self.assertEqual(self.rows(), [])


class OutputFilter(unittest.TestCase):
    def test_a_recorded_gate_run_is_filtered_like_a_test_run(self):
        spec = importlib.util.spec_from_file_location("filter_output_gate", HOOKS / "filter-output.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for command in ("citizen gate", "python3 bin/harness gate", "cd x && harness gate ."):
            self.assertTrue(module.should_filter(command), command)
        for command in ("citizen gates", "citizen trust", "echo gate"):
            self.assertFalse(module.should_filter(command), command)


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: MIT
"""The stop gate honours an explicit reason it cannot pass, and nothing less.

Each case replays a Stop payload from `tests/fixtures/stop-gate-reason/` against a repository
whose gate is red, after one block has already been recorded for the session. Run:
python3 -m unittest discover tests
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import isolation  # noqa: F401 -- keeps git maintenance out of temporary repositories
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "claude" / "hooks" / "stop-gate.py"
FIXTURES = REPO / "tests" / "fixtures" / "stop-gate-reason"
IDENTITY = "gate" + "@" + "example" + ".invalid"


class StatedReasonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.home.mkdir()
        self.repo = base / "repo"
        self.repo.mkdir()
        (self.home / ".claude.json").write_text(json.dumps(
            {"projects": {str(self.repo): {"hasTrustDialogAccepted": True}}}))
        self.log = self.home / ".local" / "state" / "agent-harness" / "decisions.jsonl"
        (self.repo / "AGENTS.md").write_text("# a repo\n\n## Gate\n\n```sh\nexit 4\n```\n")
        self.git("init")
        self.git("add", "-A")
        self.git("commit", "-m", "initial")

    def env(self):
        env = dict(os.environ)
        env.pop("CLAUDE_CONFIG_DIR", None)
        for name in list(env):
            if name.startswith("HARNESS_STANCE_"):
                env.pop(name)
        env.update({"HOME": str(self.home), "HARNESS_HOME": str(self.home),
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "Gate Fixture",
                    "GIT_COMMITTER_NAME": "Gate Fixture", "GIT_AUTHOR_EMAIL": IDENTITY,
                    "GIT_COMMITTER_EMAIL": IDENTITY})
        return env

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.repo), *args], env=self.env(),
                       capture_output=True, text=True, check=True)

    def payload(self, name):
        """The fixture's Stop payload, pointed at this test's repository and transcript."""
        fixture = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        transcript = Path(self.tmp.name) / (name + ".jsonl")
        transcript.write_text("\n".join(json.dumps(r) for r in fixture["transcript"]) + "\n",
                              encoding="utf-8")
        return dict(fixture["payload"], cwd=str(self.repo), transcript_path=str(transcript))

    def stop(self, payload):
        out = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                             env=self.env(), capture_output=True, text=True, timeout=180)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out

    def blocked_once(self, payload):
        """Record the first block, as the session would have seen it before replying."""
        out = self.stop(dict(payload, transcript_path=""))
        self.assertEqual(json.loads(out.stdout)["decision"], "block")
        return out

    def state(self):
        files = sorted((self.home / ".local" / "state" / "agent-harness" / "stop-gate")
                       .glob("*.json"))
        self.assertEqual(len(files), 1)
        return json.loads(files[0].read_text())

    def answers(self):
        rows = [json.loads(l) for l in self.log.read_text(encoding="utf-8").splitlines()]
        return [r["deterministic_answer"] for r in rows
                if r.get("kind") == "decision" and r.get("point") == "stop-gate"]

    def test_the_block_message_names_the_line_that_declines(self):
        out = self.blocked_once(self.payload("plain-finish.json"))
        self.assertIn("`Gate cannot pass:`", json.loads(out.stdout)["reason"])

    def test_a_stated_reason_after_a_block_releases_the_turn_as_declined(self):
        payload = self.payload("stated-reason.json")
        self.blocked_once(payload)
        out = self.stop(payload)
        self.assertEqual(out.stdout.strip(), "")
        self.assertIn("declined: the failing test asserts the old behaviour", out.stderr)
        state = self.state()
        self.assertEqual(state["status"], "unverified")
        self.assertIsNone(state["green_hash"])
        self.assertNotIn(payload["session_id"], state["sessions"])
        self.assertEqual(self.answers(), ["blocked", "declined"])

    def test_the_runtimes_own_last_message_field_is_honoured_too(self):
        payload = dict(self.payload("plain-finish.json"), last_assistant_message=(
            "Gate cannot pass: the red test is in a module this task put out of scope."))
        self.blocked_once(payload)
        self.assertEqual(self.stop(payload).stdout.strip(), "")
        self.assertEqual(self.answers(), ["blocked", "declined"])

    def test_a_plain_finish_after_a_block_is_still_blocked(self):
        payload = self.payload("plain-finish.json")
        self.blocked_once(payload)
        out = self.stop(payload)
        self.assertEqual(json.loads(out.stdout)["decision"], "block")
        self.assertEqual(self.state()["sessions"][payload["session_id"]]["blocks"], 2)
        self.assertEqual(self.answers(), ["blocked", "blocked"])

    def test_a_reason_on_the_first_stop_does_not_skip_the_block(self):
        """The session declines only after it has been shown the red output."""
        out = self.stop(self.payload("stated-reason.json"))
        self.assertEqual(json.loads(out.stdout)["decision"], "block")

    def test_the_phrase_mid_sentence_or_without_a_reason_is_still_blocked(self):
        for name in ("mentioned-in-passing.json", "marker-without-reason.json"):
            with self.subTest(name):
                payload = dict(self.payload(name), session_id="s-" + name)
                self.blocked_once(payload)
                self.assertEqual(json.loads(self.stop(payload).stdout)["decision"], "block")


if __name__ == "__main__":
    unittest.main()

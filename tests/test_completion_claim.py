# SPDX-License-Identifier: MIT
"""Unit tests for the completion claim on a stop-gate decision row.

The properties under test are that the switch is off, that the row is unchanged while it is off,
and that a claim which cannot be read costs the row nothing: the decision is the record, the
claim is evidence attached to it.

Run: python3 -m unittest discover tests
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import isolation  # noqa: F401 -- keeps git maintenance out of temporary repositories
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


decisions = _load("claim_decisions", REPO / "claude" / "hooks" / "decisions.py")
telemetry = _load("claim_telemetry", REPO / "claude" / "hooks" / "telemetry.py")
STOP_GATE = REPO / "claude" / "hooks" / "stop-gate.py"
IDENTITY = "gate" + "@" + "example" + ".invalid"

CLAIM = "Fixed. The suite is green; the one failure was mine and is gone."


def claude_transcript(*messages):
    """A Claude Code transcript: one assistant record per message, with noise around them."""
    lines = [json.dumps({"type": "user", "message": {"role": "user", "content": "go"}})]
    for text in messages:
        lines.append(json.dumps({"type": "assistant", "message": {
            "role": "assistant", "content": [{"type": "text", "text": text}]}}))
    return "\n".join(lines) + "\n"


class ReaderTests(unittest.TestCase):
    """`read_claim` over each shape a transcript actually holds."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "transcript.jsonl"

    def write(self, *lines):
        self.path.write_text("\n".join(json.dumps(line) for line in lines) + "\n",
                             encoding="utf-8")
        return str(self.path)

    def test_the_newest_assistant_message_of_the_turn_wins(self):
        self.path.write_text(claude_transcript("first", CLAIM), encoding="utf-8")
        self.assertEqual(decisions.read_claim(str(self.path)), (CLAIM, None))

    def test_a_codex_rollout_carries_the_same_claim(self):
        path = self.write(
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                  "content": [{"text": "go"}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                                  "content": [{"type": "output_text",
                                                               "text": CLAIM}]}})
        self.assertEqual(decisions.read_claim(path), (CLAIM, None))

    def test_a_subagent_turn_is_not_the_sessions_last_word(self):
        path = self.write(
            {"type": "user", "message": {"role": "user", "content": "go"}},
            {"type": "assistant", "message": {"role": "assistant",
                                              "content": [{"type": "text", "text": CLAIM}]}},
            {"type": "assistant", "isSidechain": True,
             "message": {"role": "assistant",
                         "content": [{"type": "text", "text": "subagent report"}]}})
        self.assertEqual(decisions.read_claim(path), (CLAIM, None))

    def test_a_tool_call_and_its_result_do_not_end_the_turn(self):
        """Claude Code writes a tool result as a user record; the scan reads through both."""
        path = self.write(
            {"type": "user", "message": {"role": "user", "content": "go"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "true"}}]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "content": "ok"}]}},
            {"type": "assistant", "message": {"role": "assistant",
                                              "content": [{"type": "text", "text": CLAIM}]}})
        self.assertEqual(decisions.read_claim(path), (CLAIM, None))

    def test_a_turn_that_ended_in_a_tool_call_claims_nothing(self):
        """The previous turn's claim is not this turn's, so the scan stops at the prompt."""
        path = self.write(
            {"type": "user", "message": {"role": "user", "content": "go"}},
            {"type": "assistant", "message": {"role": "assistant",
                                              "content": [{"type": "text", "text": CLAIM}]}},
            {"type": "user", "message": {"role": "user", "content": "and again"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "true"}}]}})
        self.assertEqual(decisions.read_claim(path), (None, "no_claim"))

    def test_a_line_separator_inside_the_claim_does_not_tear_the_record(self):
        """`splitlines` breaks on U+2028, which a JSON string carries raw. `split` does not."""
        claim = "Fixed.\u2028The suite is green."
        # `ensure_ascii=False`, as the runtime's own writer does: `JSON.stringify` leaves the
        # separator raw in the line, which is the whole reason the reader cannot use `splitlines`.
        self.path.write_text("\n".join(json.dumps(line, ensure_ascii=False) for line in (
            {"type": "user", "message": {"role": "user", "content": "go"}},
            {"type": "assistant", "message": {"role": "assistant",
                                              "content": [{"type": "text", "text": claim}]}},
        )) + "\n", encoding="utf-8")
        self.assertIn("\u2028", self.path.read_text(encoding="utf-8"))
        self.assertEqual(decisions.read_claim(str(self.path)), (claim, None))

    def test_a_claim_older_than_the_tail_is_out_of_the_window(self):
        """The read is the last CLAIM_TAIL_BYTES and nothing before them."""
        filler = json.dumps({"type": "system", "text": "x" * 900})
        with open(str(self.path), "w", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "assistant", "message": {
                "role": "assistant", "content": [{"type": "text", "text": CLAIM}]}}) + "\n")
            while stream.tell() < decisions.CLAIM_TAIL_BYTES * 2:
                stream.write(filler + "\n")
        self.assertEqual(decisions.read_claim(str(self.path)), (None, "no_claim"))

    def test_each_way_of_having_no_claim_names_itself(self):
        self.assertEqual(decisions.read_claim(""), (None, "no_transcript_path"))
        self.assertEqual(decisions.read_claim(str(self.path / "nope")), (None, "unreadable"))
        self.assertEqual(decisions.read_claim(self.write({"type": "user"})), (None, "no_claim"))

    def test_a_transcript_past_the_size_bound_is_not_read_at_all(self):
        path = self.write({"type": "assistant", "message": {
            "role": "assistant", "content": [{"type": "text", "text": CLAIM}]}})
        saved = decisions.MAX_TRANSCRIPT
        decisions.MAX_TRANSCRIPT = 8
        try:
            self.assertEqual(decisions.read_claim(path), (None, "oversized"))
        finally:
            decisions.MAX_TRANSCRIPT = saved


class FieldTests(unittest.TestCase):
    """`claim_fields`, which is where the switch, the cap and the recorded miss are decided."""

    ON = {"telemetry": {"completion_claim": True}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "transcript.jsonl"
        self.path.write_text(claude_transcript(CLAIM), encoding="utf-8")
        self.addCleanup(decisions._CONFIG.clear)
        decisions._CONFIG.clear()

    def test_the_switch_is_off_unless_it_is_turned_on(self):
        self.assertFalse(decisions.claim_enabled({}))
        self.assertFalse(decisions.claim_enabled({"telemetry": {"decisions": True}}))
        self.assertFalse(decisions.claim_enabled({"telemetry": {"completion_claim": "yes"}}))
        self.assertTrue(decisions.claim_enabled(self.ON))

    def test_off_adds_no_field_at_all(self):
        self.assertEqual(decisions.claim_fields(str(self.path), {}), {})
        self.assertEqual(decisions.claim_fields("", {}), {})

    def test_on_carries_the_claim_and_its_hash(self):
        fields = decisions.claim_fields(str(self.path), self.ON)
        self.assertEqual(fields["completion_claim"], CLAIM)
        self.assertEqual(fields["completion_claim_sha256"], decisions.digest(CLAIM))
        self.assertNotIn("completion_claim_miss", fields)

    def test_a_message_past_the_cap_keeps_its_tail_and_hashes_the_whole(self):
        long = "prologue " * 400 + CLAIM
        self.assertGreater(len(long), decisions.MAX_CLAIM)
        self.path.write_text(claude_transcript(long), encoding="utf-8")
        fields = decisions.claim_fields(str(self.path), self.ON)
        claim = fields["completion_claim"]
        self.assertEqual(claim, long[-decisions.MAX_CLAIM:])
        self.assertTrue(claim.endswith(CLAIM))
        self.assertEqual(fields["completion_claim_sha256"], decisions.digest(long))
        self.assertNotEqual(fields["completion_claim_sha256"], decisions.digest(claim))

    def test_the_cap_is_bytes_and_cuts_on_a_character_boundary(self):
        long = "\u00e9" * 2000 + "done"  # two bytes each, so the cut lands mid-character
        self.path.write_text(claude_transcript(long), encoding="utf-8")
        claim = decisions.claim_fields(str(self.path), self.ON)["completion_claim"]
        self.assertLessEqual(len(claim.encode("utf-8")), decisions.MAX_CLAIM)
        self.assertGreater(len(claim.encode("utf-8")), decisions.MAX_CLAIM - 4)
        self.assertTrue(claim.endswith("done"))
        self.assertTrue(long.endswith(claim))

    def test_a_claim_that_cannot_be_read_is_a_null_claim_and_the_reason(self):
        no_path = decisions.claim_fields("", self.ON)
        self.assertEqual(no_path, {"completion_claim": None,
                                   "completion_claim_miss": "no_transcript_path"})
        gone = decisions.claim_fields(str(self.path) + ".gone", self.ON)
        self.assertEqual(gone, {"completion_claim": None,
                                "completion_claim_miss": "unreadable"})
        for fields in (no_path, gone):
            self.assertIn(fields["completion_claim_miss"], decisions.CLAIM_MISSES)


class SettingsTests(unittest.TestCase):
    def test_the_key_is_known_validated_and_off_by_default(self):
        self.assertFalse(telemetry.settings({"telemetry": {}})["completion_claim"])
        self.assertTrue(telemetry.settings(
            {"telemetry": {"completion_claim": True}})["completion_claim"])
        with self.assertRaises(ValueError):
            telemetry.settings({"telemetry": {"completion_claim": "yes"}})


class StopGateRowTests(unittest.TestCase):
    """The hook Claude Code runs at Stop, over a real repository with a red gate."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        self.log = self.home / ".local" / "state" / "agent-harness" / "decisions.jsonl"
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.transcript = Path(self.tmp.name) / "transcript.jsonl"
        self.transcript.write_text(claude_transcript(CLAIM), encoding="utf-8")
        (self.home / ".claude.json").write_text(json.dumps(
            {"projects": {str(self.repo): {"hasTrustDialogAccepted": True}}}))
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

    def config(self, block):
        path = self.home / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(block), encoding="utf-8")

    def row(self, transcript_path=None):
        payload = {"session_id": "s-1", "cwd": str(self.repo), "hook_event_name": "Stop",
                   "stop_hook_active": False}
        if transcript_path is not None:
            payload["transcript_path"] = transcript_path
        out = subprocess.run([sys.executable, str(STOP_GATE)], input=json.dumps(payload),
                             env=self.env(), capture_output=True, text=True, timeout=180)
        self.assertEqual(out.returncode, 0, out.stderr)
        rows = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
        return [r for r in rows if r.get("kind") == "decision"][0]

    def test_with_the_switch_off_the_row_is_the_row_it_always_was(self):
        row = self.row(str(self.transcript))
        self.assertEqual(row["point"], "stop-gate")
        self.assertEqual(row["deterministic_answer"], "blocked")
        self.assertEqual(sorted(row), ["decision_id", "deterministic_answer", "harness_version",
                                       "input", "input_sha256", "kind", "module", "outcome",
                                       "point", "profile_fingerprint", "runtime", "schema_version",
                                       "session_id", "ts"])

    def test_with_the_switch_on_the_row_carries_the_turns_claim(self):
        self.config({"telemetry": {"completion_claim": True}})
        row = self.row(str(self.transcript))
        self.assertEqual(row["completion_claim"], CLAIM)
        self.assertEqual(row["completion_claim_sha256"], decisions.digest(CLAIM))

    def test_a_stop_event_naming_no_transcript_records_the_miss_on_the_row(self):
        """A runtime that names no file is a gap a reader of the log has to be able to see."""
        self.config({"telemetry": {"completion_claim": True}})
        row = self.row()
        self.assertEqual(row["deterministic_answer"], "blocked")
        self.assertIsNone(row["completion_claim"])
        self.assertEqual(row["completion_claim_miss"], "no_transcript_path")
        self.assertNotIn("completion_claim_sha256", row)


if __name__ == "__main__":
    unittest.main()

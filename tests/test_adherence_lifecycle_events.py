# SPDX-License-Identifier: MIT
"""Fresh-session follow-through is answerable without the observation entry point.

The response reading needs each prompt and each session end. Those were read only from the
observation ledger, which nothing writes unless `observation.enabled` is on, so every emission
was answered `unknown`. The dispatcher now records the two events itself, and a session the
observation ledger holds is still read from it alone.

Run: python3 -m unittest discover tests
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from isolation import without_harness_vars

REPO = Path(__file__).resolve().parent.parent
DISPATCH = r"""
import sys
sys.path.insert(0, %(lib)r)
from harness_core import lifecycle
lifecycle.main("claude-code", [])
""" % {"lib": str(REPO / "lib")}


def _load():
    spec = importlib.util.spec_from_file_location(
        "harness_adherence_events", str(REPO / "policy" / "hooks" / "adherence.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adherence = _load()


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.env = {"HOME": str(self.home), "HARNESS_HOME": str(self.home)}

    def emit(self, session, turn, ts):
        target = adherence.path(self.env)
        target.parent.mkdir(parents=True, exist_ok=True)
        row = adherence.emission("fresh-session", session, turn, ts)
        with target.open("a") as stream:
            stream.write(json.dumps(row) + "\n")
        return row["adherence_id"]

    def answers(self):
        return {r["adherence_id"]: r for r in adherence.read_rows(adherence.path(self.env))
                if r.get("kind") == "response"}


class NoteEventTests(Base):
    def test_only_prompts_and_session_ends_are_written_and_identifiers_only(self):
        for event in ("UserPromptSubmit", "PreToolUse", "SessionEnd", "Stop"):
            adherence.note_event(event, "s-1", "claude-code", env=self.env, now=100)
        rows = adherence.read_rows(adherence.events_path(self.env))
        self.assertEqual([r["event"] for r in rows], ["UserPromptSubmit", "SessionEnd"])
        self.assertEqual(set(rows[0]), {"ts", "event", "session_id", "runtime", "source",
                                        "schema_version"})

    def test_a_full_events_file_is_moved_aside_and_still_read(self):
        adherence.note_event("UserPromptSubmit", "s-1", env=self.env, now=100)
        original = adherence.EVENTS_MAX_BYTES
        adherence.EVENTS_MAX_BYTES = 1
        self.addCleanup(setattr, adherence, "EVENTS_MAX_BYTES", original)
        adherence.note_event("SessionEnd", "s-1", env=self.env, now=101)
        self.assertTrue(Path(str(adherence.events_path(self.env)) + ".1").exists())
        self.assertEqual([r["event"] for r in adherence.observed_rows(self.env)],
                         ["UserPromptSubmit", "SessionEnd"])


class SettleTests(Base):
    def test_a_session_end_within_the_window_is_followed_without_observation(self):
        start = time.time() - 600
        for n in range(1, 3):
            adherence.note_event("UserPromptSubmit", "s-1", env=self.env, now=start + n)
        ident = self.emit("s-1", 2, start + 2)
        adherence.note_event("UserPromptSubmit", "s-1", env=self.env, now=start + 3)
        adherence.note_event("SessionEnd", "s-1", env=self.env, now=start + 4)
        adherence.settle(self.env)
        answer = self.answers()[ident]
        self.assertEqual((answer["outcome"], answer["reason"], answer["turns_after"]),
                         ("followed", "SessionEnd", 1))

    def test_prompts_past_the_window_are_not_followed(self):
        start = time.time() - 600
        adherence.note_event("UserPromptSubmit", "s-2", env=self.env, now=start)
        ident = self.emit("s-2", 1, start)
        for n in range(1, 5):
            adherence.note_event("UserPromptSubmit", "s-2", env=self.env, now=start + n)
        adherence.settle(self.env)
        self.assertEqual(self.answers()[ident]["outcome"], "not_followed")

    def test_a_session_the_observation_ledger_holds_is_read_from_it_alone(self):
        start = time.time() - 600
        observed = adherence.observation_path(self.env)
        observed.parent.mkdir(parents=True, exist_ok=True)
        observed.write_text(json.dumps({"event": "UserPromptSubmit", "session_id": "s-3",
                                        "ts": adherence.now_ts(start)}) + "\n")
        # The same prompt, written by the dispatcher too, must not count a second time.
        adherence.note_event("UserPromptSubmit", "s-3", env=self.env, now=start)
        adherence.note_event("SessionEnd", "s-3", env=self.env, now=start + 5)
        rows = [r for r in adherence.observed_rows(self.env) if r.get("session_id") == "s-3"]
        self.assertEqual(len(rows), 1)
        self.assertNotIn("source", rows[0])


class DispatchTests(Base):
    def dispatch(self, payload):
        env = without_harness_vars()
        env.update(self.env)
        out = subprocess.run([sys.executable, "-c", DISPATCH], input=json.dumps(payload),
                             capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_the_dispatcher_records_each_prompt_and_the_session_end(self):
        for kind in ("UserPromptSubmit", "UserPromptSubmit", "SessionEnd"):
            self.dispatch({"hook_event_name": kind, "session_id": "s-9", "prompt": "hello",
                           "cwd": str(self.home)})
        rows = adherence.read_rows(adherence.events_path(self.env))
        self.assertEqual([(r["event"], r["session_id"], r["runtime"]) for r in rows],
                         [("UserPromptSubmit", "s-9", "claude-code")] * 2
                         + [("SessionEnd", "s-9", "claude-code")])
        self.assertNotIn("hello", adherence.events_path(self.env).read_text())


if __name__ == "__main__":
    unittest.main()

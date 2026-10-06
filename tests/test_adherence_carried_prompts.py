# SPDX-License-Identifier: MIT
"""Prompts rotated out of the session events file still count toward an open emission.

A rotation replaces the `.1` before it. A session still open across two rotations lost its early
prompts, so a later `SessionEnd` was read against too few prompts, the emission never counted as
followed, and `settle` recorded `unknown` once it aged out. The replaced file is now folded into a
per-session summary that the reading expands back.

Run: python3 -m unittest discover tests
"""
import importlib.util
import json
import unittest
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "harness_adherence_carried", str(REPO / "policy" / "hooks" / "adherence.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adherence = _load()


class CarriedPromptTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.env = {"HOME": str(self.home), "HARNESS_HOME": str(self.home)}
        # Every append past the first rotates, so two more rows push a row out of both files.
        self.addCleanup(setattr, adherence, "EVENTS_MAX_BYTES", adherence.EVENTS_MAX_BYTES)
        adherence.EVENTS_MAX_BYTES = 1

    def note(self, event, session, now):
        adherence.note_event(event, session, env=self.env, now=now)

    def emit(self, session, turn, now):
        row = adherence.emission("fresh-session", session, turn, now)
        with open(str(adherence.path(self.env)), "a") as handle:
            handle.write(json.dumps(row) + "\n")
        return row

    def test_an_emission_whose_prompts_rotated_away_is_still_followed(self):
        self.note("UserPromptSubmit", "s-open", 100)
        self.note("UserPromptSubmit", "s-open", 101)
        self.emit("s-open", 2, 101)
        for offset, session in enumerate(("x-1", "x-2", "x-3", "x-4")):
            self.note("UserPromptSubmit", session, 110 + offset)
        current = adherence.read_rows(adherence.events_path(self.env))
        rotated = adherence.read_rows(str(adherence.events_path(self.env)) + ".1")
        self.assertNotIn("s-open", [row["session_id"] for row in current + rotated])
        self.note("SessionEnd", "s-open", 120)
        written = adherence.settle(self.env, now=121)
        self.assertEqual([(row["outcome"], row["reason"]) for row in written],
                         [("followed", "SessionEnd")])

    def test_carried_events_keep_their_place_among_the_prompts(self):
        self.note("UserPromptSubmit", "s-done", 100)
        self.note("SessionEnd", "s-done", 101)
        self.note("UserPromptSubmit", "s-done", 102)
        for offset, session in enumerate(("x-1", "x-2", "x-3")):
            self.note("UserPromptSubmit", session, 110 + offset)
        rows = [row["event"] for row in adherence.observed_rows(self.env)
                if row["session_id"] == "s-done"]
        self.assertEqual(rows, ["UserPromptSubmit", "SessionEnd", "UserPromptSubmit"])

    def test_a_summary_entry_is_dropped_once_it_is_older_than_the_carry_window(self):
        self.note("UserPromptSubmit", "s-old", 100)
        for offset, session in enumerate(("x-1", "x-2")):
            self.note("UserPromptSubmit", session, 110 + offset)
        self.assertIn("s-old", adherence.read_carried(self.env))
        later = 110 + adherence.CARRY_FOR
        for offset, session in enumerate(("y-1", "y-2", "y-3")):
            self.note("UserPromptSubmit", session, later + offset)
        carried = adherence.read_carried(self.env)
        self.assertNotIn("s-old", carried)
        self.assertIn("x-1", carried)


if __name__ == "__main__":
    unittest.main()

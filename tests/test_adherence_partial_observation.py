# SPDX-License-Identifier: MIT
"""A session the observation ledger holds only part of is read from the events file.

Turning `observation.enabled` on mid-session starts the ledger partway through a session. Read
from the ledger alone, its prompts fall short of the emitting turn, and the emission was settled
`unknown` once old enough although the events file showed it followed.

Run: python3 -m unittest discover tests
"""
import json
import time
import unittest

from test_adherence_lifecycle_events import Base, adherence


class PartialObservationTests(Base):
    def observe(self, session, events):
        target = adherence.observation_path(self.env)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a") as stream:
            for event, ts in events:
                stream.write(json.dumps({"event": event, "session_id": session,
                                         "ts": adherence.now_ts(ts)}) + "\n")

    def test_a_session_observed_from_part_way_is_answered_from_the_events_file(self):
        start = time.time() - 2 * adherence.UNKNOWN_AFTER
        for n in (1, 2):
            adherence.note_event("UserPromptSubmit", "s-5", env=self.env, now=start + n)
        ident = self.emit("s-5", 2, start + 2)
        # Observation is switched on here: it sees the third prompt and the end, not the start.
        adherence.note_event("UserPromptSubmit", "s-5", env=self.env, now=start + 3)
        adherence.note_event("SessionEnd", "s-5", env=self.env, now=start + 4)
        self.observe("s-5", [("UserPromptSubmit", start + 3), ("SessionEnd", start + 4)])
        adherence.settle(self.env)
        answer = self.answers()[ident]
        self.assertEqual((answer["outcome"], answer["reason"]), ("followed", "SessionEnd"))

    def test_partial_observation_rows_are_not_mixed_with_the_events_file(self):
        adherence.note_event("UserPromptSubmit", "s-6", env=self.env, now=100)
        self.observe("s-6", [("UserPromptSubmit", 100)])
        rows = [r for r in adherence.observed_rows(self.env) if r.get("session_id") == "s-6"]
        self.assertEqual([r.get("source") for r in rows], ["lifecycle"])

    def test_a_session_only_the_ledger_holds_is_still_read_from_it(self):
        self.observe("s-7", [("UserPromptSubmit", 100), ("SessionEnd", 101)])
        rows = [r for r in adherence.observed_rows(self.env) if r.get("session_id") == "s-7"]
        self.assertEqual([r["event"] for r in rows], ["UserPromptSubmit", "SessionEnd"])


if __name__ == "__main__":
    unittest.main()

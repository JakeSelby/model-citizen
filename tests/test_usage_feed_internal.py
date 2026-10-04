# SPDX-License-Identifier: MIT
"""Claude Code's own end-of-turn agent is not a finished subagent in the usage feed.

Since Claude Code 2.1 a `SubagentStop` fires within seconds of most main-session `Stop`s with no
`SubagentStart` before it, no agent type, and no transcript written anywhere. The feed reported
each one as `unknown finished, spend unknown, no transcript found for agent …`, which became most
of its per-agent lines. The stop now journals such an agent as `internal` and the reader skips it,
unless a start was seen for it: a spawned agent whose transcript went missing is still reported.
The fixture under `fixtures/usage-feed/current-layout` is the current on-disk layout.

Run: python3 -m unittest discover tests
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from test_usage_feed import Fixture, append, assistant, load_feed, prompt, write

LAYOUT = Path(__file__).resolve().parent / "fixtures" / "usage-feed" / "current-layout"
FEED = load_feed()
SPAWNED = "a1b2c3d4e5f600001"
INTERNAL = "a9f8e7d6c5b400002"


class CurrentLayout(Fixture):
    SESSION = "s-1"

    def setUp(self):
        super().setUp()
        shutil.copytree(str(LAYOUT / "projects"), str(self.home / ".claude" / "projects"),
                        dirs_exist_ok=True)
        self.project = self.home / ".claude" / "projects" / "-tmp-repo"
        self.transcript = write(self.project / (self.SESSION + ".jsonl"), [prompt()])

    def replay(self):
        for event in json.loads((LAYOUT / "events.json").read_text()):
            if event["hook_event_name"] == "Stop":
                continue
            fields = {k: v for k, v in event.items() if k not in ("hook_event_name", "agent_id")}
            self.event(event["hook_event_name"], event["agent_id"], **fields)
        append(self.transcript, [assistant("m1", 40)])
        return [line for line in self.submit() if "finished" in line]

    def test_the_spawned_agent_is_found_in_the_current_layout(self):
        self.assertTrue(self.agent_path(SPAWNED).is_file())
        lines = self.replay()
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith(
            "usage-feed: gatherer finished at 1,500 output tokens and 1 tool call"), lines[0])

    def test_the_end_of_turn_agent_is_not_reported_or_counted(self):
        lines = self.replay()
        self.assertFalse([line for line in lines if "spend unknown" in line], lines)
        self.assertNotIn(INTERNAL, " ".join(lines))
        totals = self.state()["subagents"]
        self.assertEqual((totals["count"], totals["unknown"]), (1, 0))
        self.assertIs(self.journal()[-1].get("internal"), True)
        self.assertNotIn(INTERNAL, self.state()["counted"])

    def test_a_spawned_agent_is_never_journalled_internal(self):
        self.replay()
        spawned = [r for r in self.journal() if r["id"] == SPAWNED and r["t"] == "stop"]
        self.assertNotIn("internal", spawned[0])


class SpawnedAgentsStillUnknown(Fixture):
    """The skip is narrow: a started or typed agent with no transcript still says so."""

    SESSION = "s-2"

    def finished(self):
        append(self.transcript, [assistant("m1", 40)])
        return [line for line in self.submit() if "finished" in line]

    def test_an_untyped_agent_that_started_is_still_spend_unknown(self):
        self.start("gone")
        self.stop("gone")
        self.assertIs(self.journal()[-1].get("internal"), True)
        self.assertEqual(self.finished(), ["usage-feed: unknown finished, spend unknown, "
                                           "no transcript found for agent gone"])

    def test_a_typed_agent_with_no_transcript_is_still_spend_unknown(self):
        self.stop("lost", agent_type="gatherer")
        self.assertNotIn("internal", self.journal()[-1])
        self.assertEqual(self.finished(), ["usage-feed: gatherer finished, spend unknown, "
                                           "no transcript found for agent lost"])

    def test_an_untyped_agent_with_a_transcript_is_reported(self):
        self.agent("kept", "gatherer", [("k1", 500, ("t1",))])
        self.stop("kept")
        self.assertNotIn("internal", self.journal()[-1])
        self.assertEqual(len(self.finished()), 1)


class SettleSkipsInternal(unittest.TestCase):
    """No part of the hook's sum budget is spent on a stop that has no transcript to sum."""

    def test_to_settle_names_no_internal_stop(self):
        state = FEED.new_state()
        with self.subTest("unstarted"):
            journal = self.journal([{"t": "stop", "id": "x1", "internal": True}])
            self.assertEqual(FEED.to_settle(state, journal, {}, {}), [])
        with self.subTest("started in the same bytes"):
            journal = self.journal([{"t": "start", "id": "x2"},
                                    {"t": "stop", "id": "x2", "internal": True}])
            self.assertEqual([a for a, _ in FEED.to_settle(state, journal, {}, {})], ["x2"])

    def journal(self, records):
        handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        self.addCleanup(lambda: Path(handle.name).unlink())
        with handle:
            handle.write("".join(json.dumps(r) + "\n" for r in records))
        return Path(handle.name)


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: MIT
"""Three edges of the usage feed, from a downstream soak: a line that never cleared, a resumed
agent that fed nothing after its first round, and a figure that could be read as another measure.

`spend unknown` carries no figure and cannot be reconciled, so a session that says it every turn
says nothing new every turn; it now names the agent it is about and is said once. A resumed agent
stops once per round against one agent id, and every round after the first was folded into the
totals silently. And the feed's figure — output tokens summed from the agent's own transcript —
was read live at 31,121 beside a task notification's `subagent_tokens 102398`, so the feed says
once per session what its numbers are.

Run: python3 -m unittest discover tests
"""
import json
import unittest

from test_usage_feed import Fixture, MEASURE, append, assistant, prompt, write
from test_usage_feed_settling import SettlingFixture, ended


class ResumedAgentTests(Fixture):
    """An agent resumed with a follow-up message feeds a line on every completion, not its first."""

    def resume(self, agent_id, mid, output, tools=()):
        """One more round: the agent's transcript grows, and the agent stops again."""
        append(self.agent_path(agent_id), [assistant(mid, output, tools)])
        self.stop(agent_id)

    def turn(self, mid, output):
        append(self.transcript, [prompt(), assistant(mid, output)])
        return self.submit()

    def finished(self, lines):
        """The subagent lines of one emission. The turn line is only there when it moved on."""
        return [line for line in lines if "finished" in line]

    def test_every_round_of_a_resumed_agent_feeds_a_line(self):
        self.agent("aaa", "gatherer", [("a1", 300, ("t1",))])
        self.stop("aaa")
        first = self.finished(self.turn("m1", 10))
        self.assertIn("usage-feed: gatherer finished at 300 output tokens and 1 tool call",
                      first[0])
        # The transcript is the agent's whole life, so a later round's figure is cumulative and
        # the line says so rather than reading as what that round alone cost.
        self.resume("aaa", "a2", 500, ("t2",))
        second = self.finished(self.turn("m2", 10))
        self.assertIn("usage-feed: gatherer finished round 2 at 800 output tokens and 2 tool "
                      "calls (cumulative)", second[0])
        self.resume("aaa", "a3", 200)
        third = self.finished(self.turn("m3", 10))
        self.assertIn("usage-feed: gatherer finished round 3 at 1,000 output tokens and 2 tool "
                      "calls (cumulative)", third[0])

    def test_a_later_round_raises_the_totals_without_counting_a_second_agent(self):
        self.agent("bbb", "reviewer", [("b1", 400, ())])
        self.stop("bbb")
        self.turn("m1", 10)
        self.resume("bbb", "b2", 250)
        self.turn("m2", 10)
        totals = self.state()["subagents"]
        self.assertEqual((totals["output"], totals["count"], totals["unknown"]), (650, 1, 0))
        self.assertEqual(self.state()["rounds"]["bbb"], 2)

    def test_the_settled_stop_of_a_round_already_reported_is_not_a_second_round(self):
        # A synchronous return names the agent before its stop is journalled. That stop is the
        # same round, and announcing it would report one completion twice.
        self.agent("ccc", "gatherer", [("c1", 1000, ())])
        self.assertIn("finished at 1,000 output tokens", self.returned("ccc", "gatherer")[0])
        self.stop("ccc")
        self.assertEqual(self.finished(self.turn("m1", 10)), [])
        self.assertEqual(self.state()["rounds"]["ccc"], 1)

    def test_a_later_round_whose_transcript_has_gone_is_dropped_unsaid(self):
        # There is no spend left to recover and no figure to name, so the round is given up on
        # rather than announced with nothing in it — and the record does not sit there forever.
        self.agent("ddd", "gatherer", [("d1", 120, ())])
        self.stop("ddd")
        self.turn("m1", 10)
        self.agent_path("ddd").unlink()
        self.stop("ddd")
        self.assertEqual(self.finished(self.turn("m2", 10)), [])
        self.assertEqual(self.finished(self.turn("m3", 20)), [])
        self.assertEqual(self.state()["pending"], [])   # asked once, then given up on
        self.assertEqual(self.state()["subagents"]["output"], 120)


class UnknownLineTests(Fixture):
    """`spend unknown` names the agent it is about, and is fed once rather than every turn."""

    def test_the_line_names_the_agent_whose_spend_is_unknown(self):
        # Started, so it is a spawned agent and not Claude Code's own end-of-turn one.
        self.start("missing")
        self.stop("missing")
        append(self.transcript, [assistant("m1", 60)])
        lines = self.submit()
        self.assertEqual(lines[1], "usage-feed: unknown finished, spend unknown, "
                                   "no transcript found for agent missing")
        # No figure was fed, so the measure line is not owed yet.
        self.assertNotIn(MEASURE, lines)

    def test_a_reader_that_lost_its_record_still_says_it_only_once(self):
        self.start("missing")
        self.stop("missing")
        append(self.transcript, [assistant("m1", 60)])
        self.assertIn("spend unknown", "\n".join(self.submit()))
        # What the soak looked like from here: the reader's state no longer remembers counting
        # the agent, so the journal's stop is ingested and announced a second time.
        path = self.feed_dir() / (self.SESSION + ".json")
        state = json.loads(path.read_text(encoding="utf-8"))
        state["counted"], state["journal_offset"] = [], 0
        path.write_text(json.dumps(state), encoding="utf-8")
        append(self.transcript, [prompt(), assistant("m2", 20)])
        lines = self.submit()
        self.assertEqual([line for line in lines if "spend unknown" in line], [])
        # And it is not counted a second time either: one agent, one unknown.
        self.assertEqual(self.state()["subagents"]["count"], 1)
        self.assertEqual(self.state()["subagents"]["unknown"], 1)
        self.assertTrue(lines[0].endswith("1 subagent (partial)"), lines[0])


class MeasureLineTests(Fixture):
    """What the figures are, said once a session, so they are not read as `subagent_tokens`."""

    def test_the_measure_is_stated_once_and_after_the_first_figure(self):
        self.agent("aaa", "gatherer", [("a1", 300, ())])
        self.stop("aaa")
        append(self.transcript, [assistant("m1", 10)])
        first = self.submit()
        self.assertEqual(first[-1], MEASURE)
        self.assertIn("output tokens", MEASURE)
        self.assertIn("subagent_tokens", MEASURE)
        self.agent("bbb", "gatherer", [("b1", 400, ())])
        self.stop("bbb")
        append(self.transcript, [prompt(), assistant("m2", 10)])
        second = self.submit()
        self.assertIn("finished at 400 output tokens", "\n".join(second))
        self.assertNotIn(MEASURE, second)
        self.assertTrue(self.state()["said_measure"])

    def test_a_compaction_owes_the_measure_again(self):
        # The reset is what a compaction looks like from here, and it takes the legend out of
        # the orchestrator's context with everything else that was said before it.
        self.agent("aaa", "gatherer", [("a1", 300, ())])
        self.stop("aaa")
        append(self.transcript, [assistant("m1", 10)])
        self.assertEqual(self.submit()[-1], MEASURE)
        write(self.transcript, [prompt("a different context entirely"), assistant("n1", 20)])
        self.submit()
        self.assertFalse(self.state()["said_measure"])
        self.agent("bbb", "gatherer", [("b1", 400, ())])
        self.stop("bbb")
        self.assertEqual(self.submit()[-1], MEASURE)

    def test_a_turn_line_on_its_own_does_not_state_it(self):
        # The confusion is about a subagent's figure. A session that spawns nothing never needs
        # the sentence, and a feed spends no context on what it does not need to say.
        append(self.transcript, [assistant("m1", 500)])
        self.assertEqual(len(self.submit()), 1)


class ResumedRaceTests(SettlingFixture):
    """A resumed round's stop fires before that round's responses are flushed.

    The transcript ends on the previous round's finished response, so every cheap test of
    settledness passes while the new round is not on disk at all. What is left is the figure.
    """

    def turn(self, mid, output):
        append(self.transcript, [prompt(), assistant(mid, output)])
        return self.submit()

    def finished(self, lines):
        return [line for line in lines if "finished" in line]

    def test_a_round_that_has_not_landed_waits_rather_than_repeating_the_last_figure(self):
        self.records("aaa", "gatherer", [ended("a1", 300)])
        self.stop("aaa")
        self.assertIn("finished at 300 output tokens", self.finished(self.turn("m1", 10))[0])
        # Round two ends; its stop is journalled against a transcript that still holds only
        # round one. Nothing is said, and the stale figure is not counted a second time.
        self.stop("aaa")
        self.assertEqual(self.finished(self.turn("m2", 10)), [])
        self.assertEqual(self.state()["subagents"]["output"], 300)
        self.assertTrue(self.state()["pending"][0]["awaiting"])
        # The response lands, the re-sum happens, and the round is named with its own figure.
        append(self.agent_path("aaa"), [ended("a2", 500)])
        line = self.finished(self.turn("m3", 10))[0]
        self.assertIn("usage-feed: gatherer finished round 2 at 800 output tokens and 0 tool "
                      "calls (cumulative)", line)
        self.assertEqual(self.state()["subagents"]["output"], 800)
        self.assertEqual(self.state()["pending"], [])

    def test_a_round_that_never_lands_stops_being_asked_about(self):
        self.records("bbb", "gatherer", [ended("b1", 300)])
        self.stop("bbb")
        self.turn("m1", 10)
        self.stop("bbb")
        for step in range(5):
            self.assertEqual(self.finished(self.turn("n%d" % step, 10 + step)), [])
        self.assertEqual(self.state()["pending"], [])
        self.assertEqual(self.state()["subagents"]["output"], 300)


if __name__ == "__main__":
    unittest.main()

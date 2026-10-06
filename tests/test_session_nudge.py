# SPDX-License-Identifier: MIT
"""The feed's fresh-session nudge: one line when the session's context passes a posture threshold.

The turn line reports what a turn produced. What a long session costs is mostly the context every
further turn re-reads, and nothing in the feed showed that. These tests hold the three things the
line is worth nothing without: it appears at the crossing, it never appears twice for the same
threshold however long the session stays above it, and a posture that names no threshold is silent.

Run: python3 -m unittest discover tests
"""
import importlib.util
import json
import unittest
from pathlib import Path

from test_usage_feed import Fixture, append, assistant, load_feed, prompt, write

REPO = Path(__file__).resolve().parent.parent
NUDGE = "fresh-session threshold"


def load_posture():
    spec = importlib.util.spec_from_file_location(
        "harness_posture_nudge", str(REPO / "policy" / "hooks" / "posture.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def response(mid, output, context, cached=None):
    """One assistant record whose usage reports a context of `context` tokens.

    Split the way a real one is: a few fresh input tokens over a cache read of everything else,
    because the sum of those two is what the next turn will pay to continue here.
    """
    entry = assistant(mid, output)
    fresh = 12 if cached is None else context - cached
    entry["message"]["usage"].update({"input_tokens": fresh,
                                      "cache_read_input_tokens": context - fresh})
    return entry


class SessionNudgeTests(Fixture):
    def nudged(self, thresholds=(100000, 200000)):
        self.variant("nudged", {"schema_version": 1, "extends": "balanced",
                                "switches": {"session_nudge_at": list(thresholds)}})

    def nudges(self, lines):
        return [line for line in lines if NUDGE in line]

    def test_a_context_below_every_threshold_says_nothing_about_the_session(self):
        self.nudged()
        append(self.transcript, [response("m1", 400, 90000)])
        lines = self.submit()
        self.assertIn("last turn 400 output tokens", lines[0])
        self.assertEqual(self.nudges(lines), [])

    def test_crossing_a_threshold_says_so_once_naming_both_figures(self):
        self.nudged()
        append(self.transcript, [response("m1", 400, 120000)])
        self.assertEqual(self.nudges(self.submit()),
                         ["usage-feed: session context 120,000 tokens, past the fresh-session "
                          "threshold of 100,000 — finish the task, write the handoff, start a "
                          "fresh session"])

    def test_a_session_that_stays_above_the_threshold_is_not_told_again(self):
        # The expensive mistake this line prevents is made once per session, so saying it every
        # turn afterwards only spends the context it is warning about.
        self.nudged()
        append(self.transcript, [response("m1", 400, 120000)])
        self.assertEqual(len(self.nudges(self.submit())), 1)
        append(self.transcript, [response("m2", 500, 150000)])
        lines = self.submit()
        self.assertIn("last turn", lines[0])     # the feed is still speaking
        self.assertEqual(self.nudges(lines), [])

    def test_the_next_threshold_up_is_its_own_crossing(self):
        self.nudged()
        append(self.transcript, [response("m1", 400, 120000)])
        self.assertEqual(len(self.nudges(self.submit())), 1)
        append(self.transcript, [response("m2", 500, 240000)])
        said = self.nudges(self.submit())
        self.assertEqual(len(said), 1)
        self.assertIn("threshold of 200,000", said[0])
        self.assertEqual(self.state()["said_nudge"], [100000, 200000])

    def test_a_context_past_two_thresholds_at_once_is_one_line_naming_the_larger(self):
        self.nudged()
        append(self.transcript, [response("m1", 400, 260000)])
        said = self.nudges(self.submit())
        self.assertEqual(len(said), 1)
        self.assertIn("threshold of 200,000", said[0])
        self.assertEqual(self.state()["said_nudge"], [100000, 200000])
        append(self.transcript, [response("m2", 500, 280000)])
        self.assertEqual(self.nudges(self.submit()), [])

    def test_a_posture_with_no_threshold_never_mentions_the_session(self):
        # `max` is the posture that optimises for the answer and not the bill, so it names no
        # size and the line it would carry is never said, however large the session grows.
        self.variant("max")
        append(self.transcript, [response("m1", 400, 900000)])
        lines = self.submit()
        self.assertIn("last turn 400 output tokens", lines[0])
        self.assertEqual(self.nudges(lines), [])
        self.assertEqual(self.state()["said_nudge"], [])

    def test_the_shipped_variants_carry_their_soft_threshold(self):
        # The soft threshold is where the per-call cost curve leaves its floor, which no posture
        # moves; the variants differ in the hard one (#1197). Read from the sidecars.
        module = load_feed()
        for name, sizes in (("frugal", [160000]), ("balanced", [160000]), ("max", [])):
            self.variant(name)
            table = module.settings(self.env())[0]
            self.assertEqual(module.session_nudges(table), sizes, msg=name)

    def test_a_context_no_response_reported_is_no_crossing(self):
        # Never zero and never a guess: a usage block with neither field is a size nothing
        # measured, and a threshold it was not compared against is not one it passed.
        self.nudged()
        append(self.transcript, [assistant("m1", 400)])
        self.assertEqual(self.nudges(self.submit()), [])
        self.assertIsNone(self.state()["context"])

    def test_the_prefix_a_response_wrote_into_the_cache_counts_as_context(self):
        # The three fields do not overlap, so the turn that first caches a 90,000-token prefix
        # is reading it, not saving it for later: counting only input and cache reads would
        # report that session at 10,000 tokens.
        self.nudged()
        entry = assistant("m1", 400)
        entry["message"]["usage"].update({"input_tokens": 10000,
                                          "cache_read_input_tokens": 0,
                                          "cache_creation_input_tokens": 90000})
        append(self.transcript, [entry])
        said = self.nudges(self.submit())
        self.assertEqual(len(said), 1)
        self.assertIn("session context 100,000 tokens", said[0])

    def test_thresholds_mode_still_carries_the_nudge(self):
        # It is not a subagent's line and has no ratio to compare, so the mode that drops the
        # turn line and the quiet agents has no reason to drop it.
        self.variant("nudged-thresholds", {"schema_version": 1, "extends": "balanced",
                                           "switches": {"turn_feed": "thresholds",
                                                        "session_nudge_at": [100000]}})
        append(self.transcript, [response("m1", 400, 120000)])
        lines = self.submit()
        self.assertEqual(len(lines), 1)
        self.assertIn(NUDGE, lines[0])

    def test_the_feed_switched_off_says_nothing_and_writes_nothing(self):
        self.variant("nudged-off", {"schema_version": 1, "extends": "balanced",
                                    "switches": {"turn_feed": "off",
                                                 "session_nudge_at": [100000]}})
        append(self.transcript, [response("m1", 400, 120000)])
        self.assertEqual(self.submit(), [])
        self.assertFalse(self.feed_dir().exists())

    def test_a_threshold_already_said_survives_a_transcript_reset(self):
        # The reader starts over when the transcript's identity changes — a new inode, a new
        # first record, a file that shrank — and everything it had read is gone with it. What
        # was said to the orchestrator is not a fact about the transcript, so it stays: a
        # resumed session that rebuilt its reader used to feed the same line a second time.
        self.nudged()
        append(self.transcript, [response("m1", 400, 120000)])
        self.assertEqual(len(self.nudges(self.submit())), 1)
        path = self.feed_dir() / (self.SESSION + ".json")
        before = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(before["said_nudge"], [100000])

        write(self.transcript, [prompt("resumed"), response("m2", 500, 130000)])
        module = load_feed()
        state = module.advance(module.load_state(path), self.transcript)
        self.assertNotEqual(state["inode"] and state["head"], before["head"])
        self.assertEqual(state["said_nudge"], [100000])
        self.assertIsNone(module.session_line(state, [100000]))
        self.assertEqual(self.nudges(self.submit()), [])

    def test_a_threshold_the_context_fell_back_under_is_said_again(self):
        # An in-place compact halves the session, and an hour of work fills it again. The
        # orchestrator was told about the first crossing, not about this one.
        self.nudged()
        append(self.transcript, [response("m1", 400, 120000)])
        self.assertEqual(len(self.nudges(self.submit())), 1)
        append(self.transcript, [response("m2", 500, 40000)])
        self.assertEqual(self.nudges(self.submit()), [])
        self.assertEqual(self.state()["said_nudge"], [])
        append(self.transcript, [response("m3", 600, 130000)])
        said = self.nudges(self.submit())
        self.assertEqual(len(said), 1)
        self.assertIn("session context 130,000 tokens", said[0])

    def test_the_line_names_the_highest_threshold_newly_crossed(self):
        # Not the highest passed: a variant that gains a smaller size mid-session has one new
        # crossing, and naming the larger one already fed would read as the line repeating.
        module = load_feed()
        state = dict(module.new_state(), context=250000, said_nudge=[200000])
        line = module.session_line(state, [100000, 200000])
        self.assertIn("threshold of 100,000", line)
        self.assertEqual(state["said_nudge"], [200000, 100000])

    def test_a_token_figure_no_integer_can_hold_does_not_silence_the_feed(self):
        # JSON admits 1e400, Python reads it as an infinity and int() raises OverflowError on
        # it. The offset past that record is saved either way, so an uncaught raise here would
        # cost the session every line it had left.
        self.nudged()
        entry = assistant("m1", 400)
        entry["message"]["usage"].update({"input_tokens": 120000})
        raw = json.dumps(entry).replace('"output_tokens": 400', '"output_tokens": 1e400')
        with self.transcript.open("a", encoding="utf-8") as handle:
            handle.write(raw + "\n")
        said = self.nudges(self.submit())
        self.assertEqual(len(said), 1)
        self.assertIn("session context 120,000 tokens", said[0])


class ThresholdResolutionTests(unittest.TestCase):
    """The switch itself: read over `extends`, and validated before anything reads it."""

    def setUp(self):
        self.module = load_feed()

    def nudges(self, switches):
        return self.module.session_nudges({"switches": switches})

    def test_sizes_are_read_smallest_first_and_junk_is_dropped(self):
        self.assertEqual(self.nudges({"session_nudge_at": [200000, 100000]}), [100000, 200000])
        self.assertEqual(self.nudges({"session_nudge_at": [0, -1, True, "x", 1.5]}), [])
        self.assertEqual(self.nudges({}), [])
        self.assertEqual(self.module.session_nudges(None), [])

    def test_a_variant_inherits_the_sizes_it_does_not_set(self):
        posture = load_posture()
        base = {"schema_version": 1, "switches": {"session_nudge_at": [100000]}}
        layer = {"schema_version": 1, "extends": "base", "switches": {"max_parallel": 2}}
        merged = posture._merge(posture.validate_sidecar(base)[0],
                                posture.validate_sidecar(layer)[0])
        self.assertEqual(merged["switches"]["session_nudge_at"], [100000])

    def test_lint_rejects_a_size_that_is_not_a_count_of_tokens(self):
        posture = load_posture()
        for bad in ([1.5], ["100000"], [-1], [0], [True], "100000",
                    [1] * (posture.MAX_NUDGES + 1)):
            clean, findings = posture.validate_sidecar(
                {"schema_version": 1, "switches": {"session_nudge_at": bad}})
            self.assertTrue(any("session_nudge_at" in f for f in findings), bad)
            self.assertNotIn("session_nudge_at", clean.get("switches", {}))
        clean, findings = posture.validate_sidecar(
            {"schema_version": 1, "switches": {"session_nudge_at": [100000, 250000]}})
        self.assertEqual(findings, [])
        self.assertEqual(clean["switches"]["session_nudge_at"], [100000, 250000])

    def test_lint_rejects_an_unsorted_or_repeating_list(self):
        # The hook sorts what it reads, so either would run; both are an author who meant
        # something else, and the smallest size is what the first crossing is measured against.
        posture = load_posture()
        for bad in ([200000, 100000], [100000, 100000]):
            clean, findings = posture.validate_sidecar(
                {"schema_version": 1, "switches": {"session_nudge_at": bad}})
            self.assertEqual(len(findings), 1, bad)
            self.assertIn("the sizes ascend and none repeats", findings[0])
            self.assertNotIn("session_nudge_at", clean.get("switches", {}))

    def test_a_finding_names_the_entry_and_the_rule_it_breaks(self):
        # A list reported as "an unusable value" leaves the author comparing eight numbers
        # against a rule written down nowhere.
        posture = load_posture()
        for bad, expected in (([1.5], "entry 1.5 is not a whole positive token count"),
                              ([1] * (posture.MAX_NUDGES + 1),
                               "entries and at most " + str(posture.MAX_NUDGES) + " are read"),
                              ("100000", "is a list of whole positive token counts")):
            findings = posture.validate_sidecar(
                {"schema_version": 1, "switches": {"session_nudge_at": bad}})[1]
            self.assertIn(expected, findings[0])


if __name__ == "__main__":
    unittest.main()

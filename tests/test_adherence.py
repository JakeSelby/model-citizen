# SPDX-License-Identifier: MIT
"""Adherence events: each recommendation a hook emits, and whether the user then followed it.

The emitting side is the usage feed's fresh-session nudge, which records a row naming the
recommendation, its module, the session and the turn. The answering side reads the observation
ledger's identifier-only rows, so every reading here runs on recorded rows: fixtures, and the rows
a scripted bare-arm session writes through `observe.py`. Recording must change nothing the feed
says, which is AD-23's zero-footprint rule applied to the one hook that emits.

Run: python3 -m unittest discover tests
"""
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_session_nudge import NUDGE, response
from test_usage_feed import HOOK, Fixture, append, load_feed
from test_zero_footprint_observation import SESSION, Arms, ledger_rows
from harness_core import observation

REPO = Path(__file__).resolve().parent.parent
ROW_KEYS = ["adherence_id", "kind", "module", "profile_fingerprint", "recommendation",
            "schema_version", "session_id", "ts", "turn"]
DAY = 86400.0
T0 = 1790000000.0
# The feed hook run as its own process with adherence recording switched off, for comparison.
OFF = ("import importlib.util, sys\n"
       "spec = importlib.util.spec_from_file_location('feed', sys.argv[1])\n"
       "feed = importlib.util.module_from_spec(spec)\n"
       "spec.loader.exec_module(feed)\n"
       "feed.record_adherence = lambda *args: None\n"
       "feed.main()\n")


def nudged(fixture, thresholds=(100000, 200000)):
    fixture.variant("nudged", {"schema_version": 1, "extends": "balanced",
                               "switches": {"session_nudge_at": list(thresholds)}})


def load_adherence():
    spec = importlib.util.spec_from_file_location(
        "harness_adherence", str(REPO / "policy" / "hooks" / "adherence.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adherence = load_adherence()


def obs(event, session="s-1", at=None):
    ts = "t" if at is None else adherence.now_ts(at)
    return {"event": event, "session_id": session, "runtime": "claude-code", "ts": ts}


def emitted(turn=2, session="s-1", recommendation="fresh-session", ident="e1", now=T0):
    return {"kind": "emitted", "adherence_id": ident, "recommendation": recommendation,
            "module": "hooks/usage-feed", "session_id": session, "turn": turn,
            "ts": adherence.now_ts(now), "profile_fingerprint": "p", "schema_version": 1}


class Home(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="adherence-"))
        self.addCleanup(shutil.rmtree, str(self.home), ignore_errors=True)
        self.env = {"HOME": str(self.home)}

    def write(self, target, rows):
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")

    def rows(self):
        return adherence.read_rows(adherence.path(self.env))


class EmissionTests(Fixture):
    """AC 1: the nudge, when said, records its kind, its module, the session and the turn."""

    def nudged(self):
        nudged(self)

    def ledger(self):
        return adherence.read_rows(adherence.path({"HOME": str(self.home)}))

    def test_the_nudge_records_one_emission_naming_kind_module_session_and_turn(self):
        self.nudged()
        append(self.transcript, [response("m1", 400, 90000)])
        self.submit()
        self.assertEqual(self.ledger(), [])
        append(self.transcript, [response("m2", 400, 120000)])
        self.assertEqual(len([line for line in self.submit() if NUDGE in line]), 1)
        rows = self.ledger()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(sorted(row), ROW_KEYS)
        self.assertEqual((row["kind"], row["recommendation"], row["module"], row["session_id"],
                          row["turn"], row["schema_version"]),
                         ("emitted", "context-reset-nudge", "hooks/usage-feed", self.SESSION, 2, 1))

    def test_a_session_told_once_records_once(self):
        self.nudged()
        append(self.transcript, [response("m1", 400, 120000)])
        self.submit()
        append(self.transcript, [response("m2", 400, 150000)])
        self.submit()
        self.assertEqual([row["turn"] for row in self.ledger()], [1])
        self.assertEqual(self.state()["turns"], 2)

    def test_the_turn_survives_a_transcript_reset(self):
        self.nudged()
        self.submit()
        self.submit()
        self.transcript.write_text("")
        append(self.transcript, [response("m9", 400, 130000)])
        self.submit()
        self.assertEqual([row["turn"] for row in self.ledger()], [3])

    def test_a_kind_no_table_names_and_a_turn_that_is_not_one_record_nothing(self):
        env = {"HOME": str(self.home)}
        adherence.emit("never-heard-of", "s-1", 1, env)
        adherence.emit("fresh-session", "s-1", 0, env)
        adherence.emit("fresh-session", "s-1", True, env)
        adherence.emit("fresh-session", None, 1, env)
        self.assertEqual(self.ledger(), [])

    def test_every_kind_names_a_hook_that_exists_and_a_positive_window(self):
        for kind, spec in list(adherence.KINDS.items()) + list(adherence.RETIRED.items()):
            owner = spec["module"].split("/", 1)[1]
            self.assertTrue((REPO / "policy" / "hooks" / (owner + ".py")).is_file(), kind)
            # A kind with no prompt window is closed by its `close` events instead.
            if spec["window"] is None:
                self.assertTrue(spec.get("close"), kind)
            else:
                self.assertTrue(isinstance(spec["window"], int) and spec["window"] > 0, kind)
            self.assertTrue(spec["follow"], kind)
        self.assertFalse(set(adherence.RETIRED) & set(adherence.KINDS))
        self.assertTrue(set(adherence.RENAMED.values()) <= set(adherence.KINDS))


class RespondTests(unittest.TestCase):
    """AC 2: the next N prompts decide followed, not followed or unknown."""

    def test_a_session_that_ends_inside_the_window_followed_it(self):
        observed = [obs("SessionStart"), obs("UserPromptSubmit"), obs("UserPromptSubmit"),
                    obs("Stop"), obs("UserPromptSubmit"), obs("SessionEnd")]
        self.assertEqual(adherence.respond(emitted(turn=2), observed), ("followed", "SessionEnd", 1))

    def test_a_session_that_ends_on_the_emitting_turn_followed_it(self):
        observed = [obs("UserPromptSubmit"), obs("UserPromptSubmit"), obs("SessionEnd")]
        self.assertEqual(adherence.respond(emitted(turn=2), observed), ("followed", "SessionEnd", 0))

    def test_a_session_that_ends_on_the_last_turn_of_the_window_followed_it(self):
        observed = [obs("UserPromptSubmit")] * 5 + [obs("SessionEnd")]
        self.assertEqual(adherence.respond(emitted(turn=2), observed), ("followed", "SessionEnd", 3))

    def test_a_prompt_past_the_window_means_it_was_not_followed(self):
        observed = [obs("UserPromptSubmit")] * 6 + [obs("SessionEnd")]
        self.assertEqual(adherence.respond(emitted(turn=2), observed),
                         ("not_followed", "UserPromptSubmit", 3))

    def test_an_end_before_the_emitting_turn_is_not_an_answer(self):
        observed = [obs("UserPromptSubmit"), obs("SessionEnd"), obs("UserPromptSubmit")]
        self.assertEqual(adherence.respond(emitted(turn=2), observed), ("unknown", "window_open", 0))

    def test_another_sessions_rows_are_not_read(self):
        observed = [obs("UserPromptSubmit"), obs("UserPromptSubmit", "other"),
                    obs("SessionEnd", "other")]
        self.assertEqual(adherence.respond(emitted(turn=1), observed), ("unknown", "window_open", 0))

    def test_no_observation_of_the_emitting_turn_is_unknown_unobserved(self):
        self.assertEqual(adherence.respond(emitted(turn=2), []), ("unknown", "unobserved", 0))
        self.assertEqual(adherence.respond(emitted(turn=2), [obs("UserPromptSubmit")]),
                         ("unknown", "unobserved", 0))

    def test_a_turn_behind_the_ledger_is_read_from_the_prompts_stamped_by_the_emission(self):
        # The feed counted one prompt; the ledger saw three by the time it spoke, then an end
        # three prompts later. Read from the feed's count alone, that would be not followed.
        before = [obs("UserPromptSubmit", at=T0 - 60), obs("UserPromptSubmit", at=T0 - 30),
                  obs("UserPromptSubmit", at=T0)]
        after = [obs("UserPromptSubmit", at=T0 + 30 * n) for n in (1, 2, 3)]
        observed = before + after + [obs("SessionEnd", at=T0 + 120)]
        self.assertEqual(adherence.respond(emitted(turn=1), observed), ("followed", "SessionEnd", 3))

    def test_a_turn_ahead_of_the_ledger_is_kept(self):
        observed = [obs("UserPromptSubmit", at=T0), obs("UserPromptSubmit", at=T0 + 30),
                    obs("SessionEnd", at=T0 + 60)]
        self.assertEqual(adherence.respond(emitted(turn=2), observed), ("followed", "SessionEnd", 0))

    def test_prompts_after_the_emission_do_not_move_its_turn(self):
        observed = [obs("UserPromptSubmit", at=T0)] + [
            obs("UserPromptSubmit", at=T0 + 30 * n) for n in (1, 2, 3, 4)]
        self.assertEqual(adherence.respond(emitted(turn=1), observed),
                         ("not_followed", "UserPromptSubmit", 3))

    def test_an_emission_this_cannot_read_is_unknown(self):
        self.assertEqual(adherence.respond(emitted(recommendation="gone"), []),
                         ("unknown", "unrecognised", 0))


class SettleTests(Home):
    """AC 2: a reading appends a response row, final when made, and never a second one."""

    def seed(self, emissions, observed):
        self.write(adherence.path(self.env), emissions)
        self.write(adherence.observation_path(self.env), observed)

    def test_a_final_reading_is_written_once(self):
        self.seed([emitted(turn=1)], [obs("UserPromptSubmit"), obs("SessionEnd")])
        written = adherence.settle(self.env, now=T0 + 60)
        self.assertEqual([(r["outcome"], r["reason"], r["window"]) for r in written],
                         [("followed", "SessionEnd", 3)])
        self.assertEqual(adherence.settle(self.env, now=T0 + 120), [])
        self.assertEqual([r["kind"] for r in self.rows()], ["emitted", "response"])
        answer = self.rows()[1]
        self.assertEqual((answer["adherence_id"], answer["session_id"], answer["turn"],
                          answer["profile_fingerprint"]), ("e1", "s-1", 1, "p"))

    def test_an_open_window_waits_and_an_old_one_is_answered_unknown(self):
        self.seed([emitted(turn=1)], [obs("UserPromptSubmit")])
        self.assertEqual(adherence.settle(self.env, now=T0 + 60), [])
        later = adherence.settle(self.env, now=T0 + DAY + 1)
        self.assertEqual([(r["outcome"], r["reason"]) for r in later], [("unknown", "window_open")])

    def test_an_unobserved_session_is_answered_unknown_once_old(self):
        # Every emission reads this way until the observation entry point is registered live.
        self.seed([emitted(turn=4)], [])
        self.assertEqual(adherence.settle(self.env, now=T0), [])
        later = adherence.settle(self.env, now=T0 + DAY)
        self.assertEqual([(r["outcome"], r["reason"]) for r in later], [("unknown", "unobserved")])

    def test_no_ledger_settles_nothing_and_writes_nothing(self):
        self.assertEqual(adherence.settle(self.env), [])
        self.assertFalse(adherence.path(self.env).exists())


class RateTests(Home):
    """AC 3: a rate per recommendation, from identifiers alone."""

    def test_the_rate_is_followed_over_judged_with_unknown_and_pending_apart(self):
        rows = [emitted(ident=str(n)) for n in range(5)]
        for ident, outcome in (("0", "followed"), ("1", "followed"), ("2", "not_followed"),
                               ("3", "unknown"), ("0", "not_followed")):
            rows.append({"kind": "response", "adherence_id": ident, "outcome": outcome})
        self.assertEqual(adherence.rates(rows)["fresh-session"],
                         {"emitted": 5, "followed": 2, "not_followed": 1, "unknown": 1,
                          "pending": 1, "rate": 2 / 3.0})

    def test_no_judged_emission_is_no_rate(self):
        rows = [emitted(), {"kind": "response", "adherence_id": "e1", "outcome": "unknown"}]
        self.assertIsNone(adherence.rates(rows)["fresh-session"]["rate"])
        self.assertEqual(adherence.rates([]), {})

    def test_a_ledger_read_back_holds_no_message_body(self):
        # The feed's own nudge text, the transcript and the prompt never reach a row.
        self.write(adherence.observation_path(self.env), [obs("UserPromptSubmit"), obs("SessionEnd")])
        adherence.emit("fresh-session", "s-1", 1, self.env)
        adherence.settle(self.env)
        rows = self.rows()
        self.assertEqual(sorted(rows[0]), ROW_KEYS)
        self.assertEqual(sorted(rows[1]), sorted(ROW_KEYS + ["outcome", "reason", "turns_after",
                                                             "window"]))
        self.assertNotIn("handoff", json.dumps(rows))
        self.assertEqual(adherence.rates(rows)["fresh-session"]["rate"], 1.0)


class FootprintTests(Fixture):
    """AC 4: recording adherence changes nothing any arm's model receives."""

    def said(self, mode):
        """The feed's two prompts' lines in a fresh state, with recording `on`, `off` or `broken`."""
        shutil.rmtree(str(self.feed_dir()), ignore_errors=True)
        target = adherence.path({"HOME": str(self.home)})
        if target.is_file():
            target.unlink()
        feed = load_feed()
        payload = {"hook_event_name": "UserPromptSubmit", "session_id": self.SESSION,
                   "transcript_path": str(self.transcript), "prompt": "next"}
        out = []
        if mode == "off":
            with patch.object(feed, "record_adherence", lambda *a: None):
                for _ in range(2):
                    out.append(feed.run(dict(payload), self.env()))
            return out
        if mode == "broken":
            target.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            out.append(feed.run(dict(payload), self.env()))
        if mode == "broken":
            target.rmdir()
        return out

    def test_the_feed_says_the_same_with_recording_on_off_and_failing(self):
        nudged(self)
        append(self.transcript, [response("m1", 400, 120000)])
        off = self.said("off")
        self.assertTrue(any(NUDGE in line for line in off[0]))     # not vacuous: the nudge is said
        self.assertEqual(self.said("on"), off)
        self.assertEqual(self.said("broken"), off)

    def hook_output(self, off=False):
        """The feed hook process's stdout and status, from a fresh state, recording on or off."""
        shutil.rmtree(str(self.feed_dir()), ignore_errors=True)
        payload = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": self.SESSION,
                              "transcript_path": str(self.transcript), "prompt": "next"})
        command = [sys.executable, str(HOOK)]
        if off:
            command = [sys.executable, "-c", OFF, str(HOOK)]
        done = subprocess.run(command, input=payload, capture_output=True, text=True,
                              env=self.env())
        return done.returncode, done.stdout, done.stderr

    def test_the_hook_process_prints_the_same_with_recording_on_off_and_failing(self):
        nudged(self)
        append(self.transcript, [response("m1", 400, 120000)])
        target = adherence.path({"HOME": str(self.home)})
        off = self.hook_output(off=True)
        self.assertIn(NUDGE, off[1])
        self.assertFalse(target.exists())
        self.assertEqual(self.hook_output(), off)
        self.assertEqual(len(adherence.read_rows(target)), 1)
        target.unlink()
        target.mkdir()
        self.assertEqual(self.hook_output(), off)


class BareArmTests(Arms):
    """AC 4, bare arm: nothing is installed for adherence, and its rows answer from observation."""

    def test_the_bare_install_carries_nothing_for_adherence(self):
        dest = self.tmp / "install"
        observation.bare_install(dest)
        self.assertEqual(observation.install_files(dest), ["observe.py", "settings.json"])
        self.assertNotIn("adherence", (dest / "settings.json").read_text())

    def test_a_recorded_bare_session_answers_an_emission_from_its_observation_rows(self):
        session, _, run_root = self.session("recorded", self.bare_hooks("recorded"))
        env = {"HOME": str(run_root / "home")}
        self.assertTrue(ledger_rows(run_root))
        row = emitted(turn=1, session=SESSION)
        target = adherence.path(env)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(row) + "\n")
        written = adherence.settle(env, now=T0 + 60)
        self.assertEqual([(r["outcome"], r["reason"]) for r in written], [("followed", "SessionEnd")])
        self.assertEqual(adherence.rates(adherence.read_rows(target))["fresh-session"]["rate"], 1.0)


if __name__ == "__main__":
    unittest.main()

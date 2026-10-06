# SPDX-License-Identifier: MIT
"""A subagent whose stop another hook blocked runs on, so the fan-out count must not drop it until
a stop that was not followed by more work. Run: python3 -m unittest discover tests
"""
import datetime
import json
import time
import unittest

from test_session_caps import CAPS, SESSION, Fixture, decision
from harness_core import lifecycle


def iso(moment):
    return datetime.datetime.fromtimestamp(moment, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def record(kind, moment, **fields):
    entry = {"type": kind, "timestamp": iso(moment)}
    entry.update(fields)
    return entry


class TranscriptFixture(Fixture):
    def transcript(self, agent_id):
        return self.home / ".claude" / "projects" / "p" / SESSION / "subagents" / ("agent-%s.jsonl" % agent_id)

    def write(self, agent_id, *entries):
        path = self.transcript(agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry) + "\n")

    def stop(self, agent_id, blocked_before=False):
        payload = {"hook_event_name": "SubagentStop", "session_id": SESSION, "agent_id": agent_id,
                   "agent_type": "worker-a", "agent_transcript_path": str(self.transcript(agent_id)),
                   "stop_hook_active": blocked_before}
        return lifecycle.dispatch("claude-code", payload)

    def started_with_transcripts(self, count):
        for index in range(count):
            agent_id = "a%d" % index
            self.spawn()
            self.event("SubagentStart", agent_id)
            self.write(agent_id, record("assistant", time.time() - 5,
                                        message={"content": [{"type": "text", "text": "Working."}]}))

    def last_stop(self, agent_id):
        stops = [r for r in CAPS.records(self.journal) if r.get("t") == "stop" and r.get("id") == agent_id]
        return stops[-1]


class BlockedStopFanOutTests(TranscriptFixture):
    def test_a_blocked_stop_keeps_the_slot_until_the_stop_after_it(self):
        self.started_with_transcripts(6)
        self.stop("a0")
        # The blocking hook returns and Claude Code hands the subagent its reason as a new turn.
        self.write("a0", record("user", time.time() + 1, isMeta=True,
                                message={"content": "Your report has not been delivered."}))
        self.assertEqual(decision(self.spawn()), "deny")
        self.stop("a0", blocked_before=True)
        self.assertNotEqual(decision(self.spawn()), "deny")

    def test_a_stop_raised_after_a_block_is_journalled(self):
        self.started_with_transcripts(1)
        self.stop("a0")
        self.stop("a0", blocked_before=True)
        stops = [r for r in CAPS.records(self.journal) if r.get("t") == "stop"]
        self.assertEqual([r.get("after_block", False) for r in stops], [False, True])
        self.assertEqual(stops[0]["path"], str(self.transcript("a0")))
        self.assertEqual(stops[0]["size"], self.transcript("a0").stat().st_size)

    def test_a_final_response_flushed_after_the_stop_does_not_hold_the_slot(self):
        self.started_with_transcripts(6)
        before = time.time() - 0.5
        self.stop("a0")
        self.write("a0", record("assistant", before,
                                message={"content": [{"type": "text", "text": "Done."}]}))
        self.assertNotEqual(decision(self.spawn()), "deny")

    def test_a_stop_with_no_transcript_still_releases_the_slot(self):
        self.started_with_transcripts(6)
        lifecycle.dispatch("claude-code", {"hook_event_name": "SubagentStop", "session_id": SESSION,
                                           "agent_id": "a0", "agent_type": "worker-a"})
        self.assertNotIn("path", self.last_stop("a0"))
        self.assertNotEqual(decision(self.spawn()), "deny")


class ContinuedTests(TranscriptFixture):
    def stopped(self):
        self.write("a0", record("assistant", time.time() - 5))
        self.event("SubagentStart", "a0")
        self.stop("a0")
        return self.last_stop("a0")

    def test_a_turn_after_the_stop_reads_as_continued(self):
        stop = self.stopped()
        self.write("a0", record("user", stop["at"] + 0.01))
        self.assertTrue(CAPS.continued(stop))

    def test_nothing_written_after_the_stop_is_not_continued(self):
        self.assertFalse(CAPS.continued(self.stopped()))

    def test_an_attachment_after_the_stop_is_not_a_turn(self):
        stop = self.stopped()
        self.write("a0", record("attachment", stop["at"] + 1, attachment={"type": "hook_success"}))
        self.assertFalse(CAPS.continued(stop))

    def test_a_missing_transcript_or_a_torn_line_is_not_continued(self):
        stop = self.stopped()
        with self.transcript("a0").open("a", encoding="utf-8") as handle:
            handle.write('{"type": "user", "timestamp": "not a time"}\n{"type": "assist')
        self.assertFalse(CAPS.continued(stop))
        self.transcript("a0").unlink()
        self.assertFalse(CAPS.continued(stop))

    def test_a_stop_journalled_without_a_size_reads_the_tail(self):
        stop = self.stopped()
        del stop["size"]
        self.write("a0", record("assistant", stop["at"] + 2))
        self.assertTrue(CAPS.continued(stop))


class TallyProbeTests(unittest.TestCase):
    def test_a_probed_stop_counts_the_subagent_as_running(self):
        now = time.time()
        entries = [{"t": "start", "id": "a", "at": now - 10}, {"t": "stop", "id": "a", "at": now - 5}]
        self.assertEqual(CAPS.tally(entries, now)["live"], 0)
        self.assertEqual(CAPS.tally(entries, now, probe=lambda stop: True)["live"], 1)
        self.assertEqual(CAPS.tally(entries, now, probe=lambda stop: False)["live"], 0)

    def test_only_the_last_stop_is_probed_and_a_later_start_supersedes_it(self):
        now = time.time()
        seen = []
        entries = [{"t": "start", "id": "a", "at": now - 10}, {"t": "stop", "id": "a", "at": now - 8},
                   {"t": "stop", "id": "a", "at": now - 4, "after_block": True}]
        CAPS.tally(entries, now, probe=lambda stop: seen.append(stop["at"]) or False)
        self.assertEqual(seen, [now - 4])
        entries += [{"t": "start", "id": "a", "at": now - 2}]
        seen[:] = []
        self.assertEqual(CAPS.tally(entries, now, probe=lambda stop: seen.append(1) or True)["live"], 1)
        self.assertEqual(seen, [])

    def test_a_stop_with_no_start_and_a_decayed_stop_are_never_probed(self):
        now = time.time()
        entries = [{"t": "stop", "id": "x", "at": now},
                   {"t": "start", "id": "old", "at": now - CAPS.RUNNING_TTL - 20},
                   {"t": "stop", "id": "old", "at": now - CAPS.RUNNING_TTL - 10}]
        self.assertEqual(CAPS.tally(entries, now, probe=lambda stop: True)["live"], 0)


if __name__ == "__main__":
    unittest.main()

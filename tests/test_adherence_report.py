# SPDX-License-Identifier: MIT
"""`citizen usage --by adherence` over recorded transcripts: prompt ordinals, per-call rebuild
fields, the session lookup, and the report end to end in a temporary home."""
import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_soft_estimates import POSITIVE, TABLE, context, emitted, response
from test_rebuilds import HARNESS, assistant

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from harness_core import rebuilds, soft_estimates as soft  # noqa: E402

PRICING = HARNESS.pricing
NOW = rebuilds.timestamp("2026-09-30T00:00:00Z")


def stamp(second):
    return "2026-09-29T10:%02d:%02dZ" % (second // 60, second % 60)


def prompt(second, text="go"):
    return {"type": "user", "timestamp": stamp(second), "message": {"role": "user", "content": text}}


def tool_result(second):
    return {"type": "user", "timestamp": stamp(second),
            "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}}


def meta(second):
    return {"type": "user", "isMeta": True, "timestamp": stamp(second),
            "message": {"role": "user", "content": "<local-command-caveat>"}}


def compact_summary(second):
    return {"type": "user", "isCompactSummary": True, "timestamp": stamp(second),
            "message": {"role": "user", "content": [{"type": "text", "text": "summary"}]}}


def write(root, name, rows, project="project"):
    path = Path(root) / project / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def positive_transcript():
    """test_soft_estimates.POSITIVE as a transcript: one call after each of four prompts."""
    rows = []
    for index, record in enumerate(POSITIVE):
        rows.append(prompt(index * 10))
        entry = assistant("r%d" % index, stamp(index * 10 + 1), read=record["cache_read"],
                          write=record["cache_write"], model=record["model"], split=False)
        entry["message"]["usage"].update(input_tokens=record["input"],
                                         output_tokens=record["output"])
        rows.append(entry)
    return rows


class PromptOrdinalTests(unittest.TestCase):
    """Each call carries the ordinal of the user prompt it answers, counted as the feed counts
    a turn: tool results, meta entries and compaction summaries are not prompts."""

    def test_only_real_prompts_advance_the_ordinal(self):
        rows = [prompt(0), assistant("a", stamp(1)), tool_result(2), assistant("b", stamp(3)),
                meta(4), prompt(5), assistant("c", stamp(6)), compact_summary(7),
                assistant("d", stamp(8))]
        with tempfile.TemporaryDirectory() as root:
            calls, _ = rebuilds.read_calls(write(root, "s.jsonl", rows), 0)
        self.assertEqual([call["prompt"] for call in calls], [1, 1, 2, 2])

    def test_a_prompt_before_the_window_still_counts(self):
        rows = [prompt(0), assistant("a", stamp(1)), prompt(20), assistant("b", stamp(21))]
        with tempfile.TemporaryDirectory() as root:
            calls, _ = rebuilds.read_calls(write(root, "s.jsonl", rows), rebuilds.timestamp(stamp(10)))
        self.assertEqual([(call["id"], call["prompt"]) for call in calls], [("b", 2)])

    def test_a_sidechain_prompt_is_not_a_main_prompt(self):
        side = dict(prompt(2), isSidechain=True)
        rows = [prompt(0), assistant("a", stamp(1)), side, assistant("b", stamp(3))]
        with tempfile.TemporaryDirectory() as root:
            calls, _ = rebuilds.read_calls(write(root, "s.jsonl", rows), 0)
        self.assertEqual([call["prompt"] for call in calls], [1, 1])


class BreakFieldTests(unittest.TestCase):
    """`annotate` puts each call's rebuild on the call: `rewritten` and `cause`, the same break
    and cause `analyse` counts."""

    def calls(self):
        rows = [prompt(0), assistant("a", stamp(1), read=0, write=100000),
                prompt(10), assistant("b", stamp(11), read=110000, write=5000, one_hour=0),
                compact_summary(20), assistant("c", stamp(21), read=0, write=30000)]
        with tempfile.TemporaryDirectory() as root:
            calls, _ = rebuilds.read_calls(write(root, "s.jsonl", rows), 0)
        return rebuilds.annotate(calls)

    def test_a_break_carries_its_rewritten_tokens_and_cause(self):
        first, second, third = self.calls()
        self.assertEqual((first["rewritten"], first["cause"]), (0, None))
        self.assertEqual((second["rewritten"], second["cause"]), (0, None))
        # Sent 10k + 110k + 5k = 125k, read 0: a 125k shortfall, of which 30k was rewritten.
        self.assertEqual((third["rewritten"], third["cause"]), (30000, soft.COMPACTION))

    def test_analyse_counts_the_breaks_annotate_marks(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "s.jsonl", [prompt(0), assistant("a", stamp(1), read=0, write=100000),
                                    compact_summary(20),
                                    assistant("c", stamp(21), read=0, write=30000)])
            result = rebuilds.analyse(root, 0, 30, TABLE, PRICING)
        row, = result["scopes"]["all"]["causes"]
        self.assertEqual((row["cause"], row["breaks"], row["rewritten_tokens"]),
                         ("compaction", 1, 30000))


class SessionCallsTests(unittest.TestCase):
    def test_finds_the_session_transcript_in_any_project(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "sess-1.jsonl", positive_transcript(), project="other")
            calls = rebuilds.session_calls(root, "sess-1")
        self.assertEqual([call["prompt"] for call in calls], [1, 2, 3, 4])
        self.assertTrue(all("cause" in call and "rewritten" in call for call in calls))

    def test_a_missing_transcript_is_none_not_empty(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(rebuilds.session_calls(root, "sess-1"))

    def test_a_session_id_that_is_not_a_plain_name_is_never_globbed(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "sess-1.jsonl", positive_transcript())
            for name in ("*", "../project/sess-1", "", None, "sess-?"):
                with self.subTest(name=name):
                    self.assertIsNone(rebuilds.session_calls(root, name))


def usage_args(**overrides):
    values = dict(action=None, days=30, by="adherence", rules=False, stance=None, rescan=False,
                  conflicts=False, json=False)
    values.update(overrides)
    return argparse.Namespace(**values)


class ReportCommandTests(unittest.TestCase):
    """The report end to end: a ledger in a temporary home and a transcript under projects."""

    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        home = Path(self.home.name)
        self.projects = home / "projects"
        write(self.projects, "sess-1.jsonl", positive_transcript())
        ledger = home / ".local" / "state" / "agent-harness" / HARNESS.load_hook_module(
            "adherence", required=True).LEDGER
        ledger.parent.mkdir(parents=True)
        rows = [emitted("n1", session="sess-1", turn=2, ts="2026-09-29T10:00:05Z"),
                response("n1", "not_followed"),
                emitted("f1", session="sess-1", turn=3, ts="2026-09-29T10:00:25Z"),
                response("f1", "followed"),
                emitted("n2", session="gone", turn=2, ts="2026-09-29T10:00:05Z"),
                response("n2", "not_followed")]
        ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def run_usage(self, **overrides):
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"HARNESS_HOME": self.home.name}), \
                mock.patch.object(HARNESS, "claude_projects_root", lambda: self.projects), \
                mock.patch.object(HARNESS, "load_prices", lambda config: TABLE), \
                mock.patch.object(HARNESS.time, "time", return_value=NOW), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            os.environ.pop("HARNESS_QUIET", None)
            code = HARNESS.cmd_usage(usage_args(**overrides))
        return code, output.getvalue(), errors.getvalue()

    def test_json_reproduces_the_hand_computed_estimate(self):
        code, out, _ = self.run_usage(json=True)
        self.assertEqual(code, 0)
        document = json.loads(out)
        self.assertEqual((document["report"], document["by"]), ("adherence", "adherence"))
        rate, estimate = document["groups"]
        self.assertAlmostEqual(rate["figures"]["rate"]["value"], 1 / 3.0)
        self.assertEqual(rate["figures"]["rate"]["label"], soft.MEASURED)
        figures = estimate["figures"]
        expected = soft.reprice(POSITIVE, 2, TABLE, PRICING, context)["saving"]
        # POSITIVE's docstring figure in test_soft_estimates: $0.0465.
        self.assertAlmostEqual(expected, 0.0465)
        self.assertAlmostEqual(figures["saving"]["value"], expected)
        self.assertEqual(figures["saving"]["label"], soft.SOFT_ESTIMATE)
        self.assertEqual(figures["saving"]["estimator"], soft.ESTIMATOR)
        self.assertEqual((figures["priced"]["value"], figures["transcript_missing"]["value"]),
                         (1, 1))
        self.assertEqual(document["footer"], soft.FOOTER)

    def test_text_prints_both_labelled_sections(self):
        code, out, _ = self.run_usage()
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertIn("Adherence (Measured)", lines)
        self.assertIn("If followed (Soft estimate: carried-context reprice)", lines)
        self.assertIn("    saving $0.05 n=1 [soft estimate]", lines)
        self.assertEqual(lines[-1], soft.FOOTER)

    def test_rules_stance_and_rescan_are_refused(self):
        for option in ({"rules": True}, {"stance": "cost"}, {"rescan": True}):
            with self.subTest(option=option):
                code, out, err = self.run_usage(**option)
                self.assertEqual(code, 2)
                self.assertIn("--by adherence", err)
                self.assertEqual(out, "")

    def test_the_command_line_accepts_the_view(self):
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"HARNESS_HOME": self.home.name}), \
                mock.patch.object(HARNESS, "claude_projects_root", lambda: self.projects), \
                mock.patch.object(HARNESS, "load_prices", lambda config: TABLE), \
                mock.patch.object(HARNESS.time, "time", return_value=NOW), \
                contextlib.redirect_stdout(output):
            code = HARNESS.main(["usage", "--by", "adherence", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["report"], "adherence")

if __name__ == "__main__":
    unittest.main()

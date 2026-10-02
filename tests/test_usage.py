# SPDX-License-Identifier: MIT
"""Unit tests for the usage-log hook and `harness usage`. Run: python3 -m unittest discover tests"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from isolation import without_config_dir

REPO = Path(__file__).resolve().parent.parent


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


harness = _load("harness", REPO / "bin" / "harness")
usage_log = _load("usage_log", REPO / "claude" / "hooks" / "usage-log.py")

OWNERSHIP = json.loads((REPO / "claude" / "OWNERSHIP.json").read_text())
CFG = json.loads((REPO / "config.example.json").read_text())
STAMPS = [time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 300 + i)) for i in range(5)]


def assistant(mid, stamp, usage=None, tool=None, model="model-a"):
    message = {"id": mid, "model": model, "content": []}
    if tool:
        message["content"].append({"type": "tool_use", "id": tool, "name": "Agent", "input": {}})
    if usage is not None:
        message["usage"] = usage
    return {"type": "assistant", "sessionId": "s-1", "cwd": "", "gitBranch": "topic",
            "timestamp": stamp, "message": message}


def fixture(path):
    """Three messages, one of them split across two entries that repeat the same usage object."""
    entries = [
        {"type": "user", "sessionId": "s-1", "timestamp": STAMPS[0]},
        assistant("m1", STAMPS[1],
                  {"input_tokens": 10, "output_tokens": 100, "cache_read_input_tokens": 800,
                   "cache_creation_input_tokens": 90}),
        assistant("m2", STAMPS[2],
                  {"input_tokens": 20, "output_tokens": 200, "cache_read_input_tokens": 1600,
                   "cache_creation_input_tokens": 180},
                  tool="tu-1"),
        assistant("m2", STAMPS[3],
                  {"input_tokens": 20, "output_tokens": 200, "cache_read_input_tokens": 1600,
                   "cache_creation_input_tokens": 180}),
        assistant("m3", STAMPS[4],
                  {"input_tokens": 30, "output_tokens": 300, "cache_read_input_tokens": 1600,
                   "cache_creation_input_tokens": 130},
                  model="model-b"),
        "not json at all",
    ]
    path.write_text("".join(
        (e if isinstance(e, str) else json.dumps(e)) + "\n" for e in entries), encoding="utf-8")
    return path


FIELD_KEYS = dict(usage_log.FIELDS)
USAGE = {"input_tokens": 10, "output_tokens": 100, "cache_read_input_tokens": 800,
         "cache_creation_input_tokens": 90}


def line(kind, message=None, stamp=STAMPS[0], **extra):
    entry = {"type": kind, "sessionId": "s-1", "cwd": "", "timestamp": stamp}
    if message is not None:
        entry["message"] = message
    entry.update(extra)
    return entry


def block_line(*blocks, **kwargs):
    """One assistant transcript line carrying the given content blocks."""
    message = {"id": kwargs.get("mid", "m1"), "model": kwargs.get("model", "model-a"),
               "content": list(blocks), "usage": kwargs.get("usage", USAGE)}
    return line("assistant", message, kwargs.get("stamp", STAMPS[0]))


def rules_fixture(path, extra=()):
    """A transcript with one hit for each of six detectors, and nothing else.

    The first two lines are one API response written twice, as Claude Code writes it: same
    message id, same `usage`, one content block each.
    """
    entries = [
        block_line({"type": "text", "text": "Reading the file now."}, mid="m1", stamp=STAMPS[0]),
        block_line({"type": "tool_use", "id": "tu1", "name": "Bash",
                    "input": {"command": "cat big.py"}}, mid="m1", stamp=STAMPS[0]),
        line("user", {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu1",
             "content": [{"type": "text", "text": "print('hello')"}], "is_error": False}]}),
        line("user", {"role": "user", "content": "now delegate it"}, stamp=STAMPS[1]),
        block_line({"type": "tool_use", "id": "a1", "name": "Agent",
                    "input": {"subagent_type": "general-purpose",
                              "prompt": "Summarise the module."}}, mid="m2", stamp=STAMPS[1]),
        block_line({"type": "tool_use", "id": "tu2", "name": "Bash",
                    "input": {"command": 'git commit -m "fixed it"'}}, mid="m3", stamp=STAMPS[2]),
        line("system", None, STAMPS[3], subtype="compact_boundary", content="Conversation compacted"),
        block_line({"type": "text", "text": "Great question — the module parses the manifest."},
                   mid="m4", stamp=STAMPS[4]),
    ] + list(extra)
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    return path


class TempHome(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        os.environ.pop("HARNESS_QUIET", None)
        self.transcript = fixture(self.home / "transcript.jsonl")

    def tearDown(self):
        if self._old_home is not None:
            os.environ["HOME"] = self._old_home
        self.tmp.cleanup()

    def report(self, **kwargs):
        args = harness.argparse.Namespace(days=kwargs.pop("days", 30), by=kwargs.pop("by", "day"),
                                          rules=kwargs.pop("rules", False),
                                          rescan=kwargs.pop("rescan", False))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = harness.cmd_usage(args)
        self.assertEqual(rc, 0)
        return buf.getvalue()


class ScanTests(TempHome):
    def test_worker_writes_one_record_with_correct_sums(self):
        usage_log.main(["--worker", str(self.transcript), "s-1", ""])
        lines = (self.home / ".local/state/agent-harness/usage.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual(rec["session_id"], "s-1")
        self.assertEqual((rec["input"], rec["output"]), (60, 600))
        self.assertEqual((rec["cache_read"], rec["cache_write"]), (4000, 400))
        self.assertEqual(rec["turns"], 3)
        self.assertEqual(rec["subagents"], 1)
        self.assertEqual(rec["models"], ["model-a", "model-b"])
        self.assertEqual(rec["branch"], "topic")
        self.assertEqual(rec["started"], STAMPS[0])
        self.assertEqual(rec["ended"], STAMPS[4])

    def test_repeated_entries_of_one_message_are_counted_once(self):
        rec = usage_log.scan(self.transcript, "s-1", "")
        doubled = fixture(self.home / "again.jsonl")
        self.assertEqual(usage_log.scan(doubled, "s-1", "")["input"], rec["input"])

    def test_upsert_replaces_rather_than_appends(self):
        usage_log.main(["--worker", str(self.transcript), "s-1", ""])
        usage_log.main(["--worker", str(self.transcript), "s-1", ""])
        path = self.home / ".local/state/agent-harness/usage.jsonl"
        self.assertEqual(len(path.read_text().splitlines()), 1)
        usage_log.upsert(dict(json.loads(path.read_text().splitlines()[0]), session_id="s-2"))
        self.assertEqual(len(path.read_text().splitlines()), 2)

    def test_a_transcript_with_no_assistant_message_records_nothing(self):
        empty = self.home / "empty.jsonl"
        empty.write_text(json.dumps({"type": "user", "sessionId": "s-9"}) + "\n", encoding="utf-8")
        self.assertIsNone(usage_log.scan(empty))

    def test_rescan_picks_up_a_transcript_no_hook_ever_saw(self):
        project = self.home / ".claude" / "projects" / "a-repo"
        project.mkdir(parents=True)
        fixture(project / "s-1.jsonl")
        self.assertEqual(usage_log.rescan(30), 1)
        self.assertEqual(usage_log.rescan(30), 1)
        rows = (self.home / ".local/state/agent-harness/usage.jsonl").read_text().splitlines()
        self.assertEqual(len(rows), 1)

    def test_rescan_skips_transcripts_older_than_the_window(self):
        project = self.home / ".claude" / "projects" / "a-repo"
        project.mkdir(parents=True)
        old = fixture(project / "s-1.jsonl")
        stale = time.time() - 40 * 86400
        os.utime(old, (stale, stale))
        self.assertEqual(usage_log.rescan(7), 0)


class ReportTests(TempHome):
    def setUp(self):
        super().setUp()
        usage_log.main(["--worker", str(self.transcript), "s-1", ""])

    def test_totals_and_hit_rate(self):
        out = self.report()
        self.assertIn(STAMPS[4][:10], out)
        self.assertIn("TOTAL", out)
        self.assertIn("4,000", out)
        # 4000 / (60 + 4000 + 400)
        self.assertIn("90%", out)

    def test_grouping_by_repo_and_model(self):
        record = json.loads((self.home / ".local/state/agent-harness/usage.jsonl").read_text())
        usage_log.upsert(dict(record, session_id="s-2", repo="other-repo"))
        by_repo = self.report(by="repo")
        self.assertIn("other-repo", by_repo)
        self.assertIn("(no repo)", by_repo)
        self.assertIn("model-a+model-b", self.report(by="model"))

    def test_window_excludes_older_sessions(self):
        path = self.home / ".local/state/agent-harness/usage.jsonl"
        record = json.loads(path.read_text())
        # Its slices move with its end date: a session grouped by day is read through them, so
        # a row left with this week's slices would still be in this week's window.
        old = {"2020-01-01": dict(list(record["days"].values())[0])}
        path.write_text(json.dumps(dict(record, ended="2020-01-01T00:00:00.000Z", days=old)) + "\n")
        self.assertIn("no sessions recorded", self.report(days=7))

    def test_empty_state_is_not_an_error(self):
        os.remove(self.home / ".local/state/agent-harness/usage.jsonl")
        self.assertIn("no sessions recorded", self.report())


class RuleRecordTests(TempHome):
    """The worker's rule fields, from the one pass it already makes over the transcript."""

    EXPECTED = {
        "transcript-hygiene/whole-file-cat": 1,
        "transcript-hygiene/model-wrote-no-cap": 1,
        "commits/non-conventional": 1,
        "commits/missing-trailer": 1,
        "cache-hygiene/compact": 1,
        "voice/banned-opener": 1,
    }

    def setUp(self):
        super().setUp()
        self._old_stance = os.environ.get("HARNESS_STANCE_COMMITS")
        os.environ["HARNESS_STANCE_COMMITS"] = "conventional-attributed"

    def tearDown(self):
        if self._old_stance is None:
            os.environ.pop("HARNESS_STANCE_COMMITS", None)
        else:
            os.environ["HARNESS_STANCE_COMMITS"] = self._old_stance
        super().tearDown()

    def record(self, path):
        usage_log.main(["--worker", str(path), "s-1", ""])
        rows = (self.home / ".local/state/agent-harness/usage.jsonl").read_text().splitlines()
        self.assertEqual(len(rows), 1)
        return json.loads(rows[0])

    def test_the_record_carries_the_hits_the_counts_and_the_stances(self):
        rec = self.record(rules_fixture(self.home / "rules.jsonl"))
        self.assertEqual(rec["rules"], self.EXPECTED)
        self.assertEqual(rec["counts"], {"web_search": 0, "agent": 1, "ask_user": 0})
        self.assertEqual(rec["stances"]["commits"], "conventional-attributed")
        self.assertNotIn("rules_error", rec)
        self.assertEqual((rec["input"], rec["output"]), (40, 400))
        self.assertEqual((rec["cache_read"], rec["cache_write"]), (3200, 360))
        self.assertEqual((rec["turns"], rec["subagents"]), (4, 1))

    def test_a_sidechain_line_makes_no_event_but_keeps_its_tokens(self):
        """Older Claude Code wrote a subagent's turns into this file; the session paid for
        them, so the sums keep them, and the detectors — which measure this session's own
        conduct — do not see them."""
        noise = [block_line({"type": "tool_use", "id": "tu9", "name": "Bash",
                             "input": {"command": "cat elsewhere.py"}},
                            mid="m9", stamp=STAMPS[4])]
        noise[0]["isSidechain"] = True
        plain = self.record(rules_fixture(self.home / "rules.jsonl"))
        noisy = self.record(rules_fixture(self.home / "noisy.jsonl", noise))
        self.assertEqual(noisy["rules"], plain["rules"])
        self.assertEqual(noisy["rules"]["transcript-hygiene/whole-file-cat"], 1)
        self.assertEqual(noisy["turns"], plain["turns"] + 1)
        for field in ("input", "output", "cache_read", "cache_write"):
            self.assertEqual(noisy[field], plain[field] + USAGE[FIELD_KEYS[field]])

    def test_a_meta_user_line_does_not_end_a_turn_and_so_makes_no_message_final(self):
        """The `isMeta` line is a slash command's echo, not a prompt. Were it counted as one,
        the message before it would be read as final and its opener would be a hit."""
        path = self.home / "meta.jsonl"
        path.write_text("".join(json.dumps(e) + "\n" for e in [
            block_line({"type": "text", "text": "Great question — I will start with the manifest."},
                       mid="m1", stamp=STAMPS[0]),
            line("user", {"role": "user", "content": "<command-name>/clear</command-name>"},
                 STAMPS[1], isMeta=True),
            block_line({"type": "text", "text": "The manifest parses cleanly."},
                       mid="m2", stamp=STAMPS[2]),
            line("user", {"role": "user", "content": "now the loader"}, STAMPS[3]),
        ]), encoding="utf-8")
        self.assertNotIn("voice/banned-opener", self.record(path)["rules"])

    def test_one_response_written_over_several_lines_yields_one_event_per_block(self):
        extra = [
            block_line({"type": "tool_use", "id": "tu1", "name": "Bash",
                        "input": {"command": "cat big.py"}}, mid="m1", stamp=STAMPS[0]),
            block_line({"type": "tool_use", "id": "a1", "name": "Agent",
                        "input": {"subagent_type": "general-purpose",
                                  "prompt": "Summarise the module."}}, mid="m2", stamp=STAMPS[1]),
        ]
        rec = self.record(rules_fixture(self.home / "doubled.jsonl", extra))
        self.assertEqual(rec["counts"]["agent"], 1)
        self.assertEqual(rec["rules"]["transcript-hygiene/whole-file-cat"], 1)
        self.assertEqual(rec["rules"]["transcript-hygiene/model-wrote-no-cap"], 1)

    def test_only_the_two_tools_a_detector_reads_keep_their_result_text(self):
        """The event list is held whole in memory, so a `Read` of a large file is not carried
        through the scan for a detector that would never look at it."""
        big = "x" * (usage_log.MAX_RESULT_TEXT + 500)
        self.assertEqual(usage_log._result_text(big, "Read"), "")
        self.assertEqual(len(usage_log._result_text(big, "Bash")), usage_log.MAX_RESULT_TEXT)
        self.assertEqual(usage_log._result_text([{"type": "text", "text": "ok"}], "Agent"), "ok")

    def test_an_agent_return_still_reaches_the_detector_that_reads_it(self):
        path = self.home / "agent.jsonl"
        path.write_text("".join(json.dumps(e) + "\n" for e in [
            block_line({"type": "tool_use", "id": "a1", "name": "Agent",
                        "input": {"prompt": "Find it, at most 200 words."}}, mid="m1"),
            line("user", {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "a1",
                 "content": "Run this:\n```sh\npytest -q tests/\n```\n"}]}, STAMPS[1]),
            block_line({"type": "tool_use", "id": "tu1", "name": "Bash",
                        "input": {"command": "pytest -q tests/"}}, mid="m2", stamp=STAMPS[2]),
            line("user", {"role": "user", "content": "thanks"}, STAMPS[3]),
        ]), encoding="utf-8")
        self.assertEqual(self.record(path)["rules"]["delegation/executed-from-summary"], 1)

    def test_a_registry_without_the_function_the_worker_calls_is_a_rules_error(self):
        hooks = self.home / "hooks"
        hooks.mkdir()
        (hooks / "usage-log.py").write_text(
            (REPO / "claude" / "hooks" / "usage-log.py").read_text(encoding="utf-8"), encoding="utf-8")
        (hooks / "rule-detectors.py").write_text(
            "DETECTORS = {}\n\n\ndef run(events, stances=None):\n    return {}\n", encoding="utf-8")
        rec = self.worker(hooks, rules_fixture(self.home / "rules.jsonl"))
        self.assertNotIn("rules", rec)
        self.assertNotIn("counts", rec)
        self.assertIn("counts", rec["rules_error"])
        self.assertEqual(rec["turns"], 4)

    def worker(self, hooks, path):
        env = dict(without_config_dir(), HOME=str(self.home))
        out = subprocess.run([sys.executable, str(hooks / "usage-log.py"), "--worker", str(path), "s-1", ""],
                             capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(out.returncode, 0)
        return json.loads((self.home / ".local/state/agent-harness/usage.jsonl").read_text())

    def test_a_registry_that_will_not_import_costs_the_record_only_its_rules(self):
        hooks = self.home / "hooks"
        hooks.mkdir()
        (hooks / "usage-log.py").write_text(
            (REPO / "claude" / "hooks" / "usage-log.py").read_text(encoding="utf-8"), encoding="utf-8")
        path = rules_fixture(self.home / "rules.jsonl")
        env = dict(without_config_dir(), HOME=str(self.home))
        out = subprocess.run([sys.executable, str(hooks / "usage-log.py"), "--worker", str(path), "s-1", ""],
                             capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(out.returncode, 0)
        rec = json.loads((self.home / ".local/state/agent-harness/usage.jsonl").read_text())
        self.assertNotIn("rules", rec)
        self.assertNotIn("counts", rec)
        self.assertTrue(rec["rules_error"])
        self.assertEqual(rec["turns"], 4)


class RuleReportTests(TempHome):
    HIT = "transcript-hygiene/whole-file-cat"
    QUIET = "voice/second-table"

    def write(self, rows):
        path = self.home / ".local/state/agent-harness/usage.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def row(self, i, **extra):
        base = {"session_id": "s-%d" % i, "repo": "alpha", "ended": STAMPS[4],
                "models": ["model-a"], "rules": {}, "stances": {"commits": "conventional"}}
        base.update(extra)
        return base

    def test_a_third_of_the_window_promotes_and_an_unobserved_rule_says_so(self):
        rows = [self.row(i, rules={self.HIT: 2} if i < 10 else {}) for i in range(25)]
        self.write(rows)
        lines = self.report(rules=True).splitlines()
        self.assertTrue(lines[0].startswith("detector"))
        self.assertIn(
            "transcript-hygiene/whole-file-cat          20        10    25     40%  promote?", lines)
        self.assertIn(
            "voice/second-table                          0         0    25      0%  unobserved", lines)

    def test_a_window_too_narrow_for_a_share_is_annotated_nothing(self):
        self.write([self.row(i, rules={self.HIT: 1}) for i in range(19)])
        out = self.report(rules=True)
        self.assertNotIn("promote?", out)
        self.assertNotIn("unobserved", out)

    def test_a_session_with_no_rule_data_is_neither_a_zero_nor_a_denominator(self):
        """A record written before the detectors existed, or one whose registry would not
        import, is a gap. Counted as a session with no hit it would read as a clean bill."""
        rows = [self.row(i, rules={self.HIT: 1}) for i in range(5)]
        for i in range(5, 25):
            legacy = self.row(i)
            legacy.pop("rules")
            rows.append(legacy)
        rows.append(dict(self.row(99), rules=None, rules_error="ImportError: no module"))
        rows[-1].pop("rules")
        self.write(rows)
        # The detector table ends at the blank line; the rule coverage block follows it.
        lines = self.report(rules=True).split("\n\n", 1)[0].splitlines()
        self.assertEqual(lines[0], "21 session(s) in the window carry no rule data "
                                   "(20 recorded before detectors, 1 rules_error); "
                                   "run --rescan to backfill")
        self.assertNotIn("unobserved", "\n".join(lines))
        for ln in lines[3:]:
            self.assertEqual(ln.split()[3], "5")

    def test_a_window_of_gaps_alone_says_so_rather_than_printing_zeroes(self):
        row = self.row(0)
        row.pop("rules")
        self.write([row])
        self.assertIn("no measured sessions", self.report(rules=True))

    def test_every_registry_id_gets_a_line_even_with_no_records_to_show_it(self):
        self.write([self.row(0)])
        table = self.report(rules=True).split("\n\n", 1)[0]
        printed = {ln.split()[0] for ln in table.splitlines()[2:]}
        detectors = usage_log.detectors()
        self.assertTrue(set(detectors.DETECTORS) <= printed)

    def test_grouping_by_stance_puts_a_session_under_each_dimension_it_names(self):
        self.write([self.row(0), self.row(1), self.row(2, stances={"commits": "off"})])
        out = self.report(rules=True, by="stance").splitlines()
        rows = dict((ln.split()[0], ln.split()) for ln in out[2:])
        self.assertEqual(rows["commits=conventional"][1], "2")
        self.assertEqual(rows["commits=off"][1], "1")

    def test_a_session_fans_out_under_every_dimension_it_carries(self):
        self.write([self.row(0, stances={"commits": "conventional", "autonomy": "execute",
                                         "testing": "required"}, rules={self.HIT: 3})])
        keys = [ln.split()[0] for ln in self.report(rules=True, by="stance").splitlines()[2:]]
        self.assertEqual(keys, ["autonomy=execute", "commits=conventional", "testing=required"])

    def test_a_rescanned_session_is_excluded_from_the_stance_grouping(self):
        self.write([self.row(0), self.row(1, stances={"commits": "off"}, stances_source="rescan")])
        out = self.report(rules=True, by="stance")
        self.assertIn("1 rescanned session(s) excluded: stance not known at the time", out)
        self.assertNotIn("commits=off", out)
        self.assertIn("commits=conventional", out)
        # The same session still counts everywhere its stance is not the grouping key.
        self.assertIn("alpha", self.report(rules=True, by="repo"))

    def test_rules_by_model_is_refused_rather_than_quietly_regrouped(self):
        self.write([self.row(0)])
        args = harness.argparse.Namespace(days=30, by="model", rules=True, rescan=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(harness.cmd_usage(args), 2)
        self.assertEqual(buf.getvalue(), "")

    def test_grouping_by_repo_names_the_top_three_detectors_with_their_counts(self):
        self.write([self.row(0, rules={"a/one": 5, "b/two": 4, "c/three": 3, "d/four": 2})])
        line_ = [ln for ln in self.report(rules=True, by="repo").splitlines()
                 if ln.startswith("alpha")][0]
        label, rest = line_[:30].strip(), line_[30:]
        self.assertEqual(label, "alpha")
        self.assertEqual(rest.split()[:2], ["1", "14"])
        self.assertEqual(rest.split("14", 1)[1].strip(), "a/one 5, b/two 4, c/three 3")

    def test_an_empty_window_is_not_an_error(self):
        self.write([])
        self.assertIn("no measured sessions", self.report(rules=True))


class StanceResolutionTests(TempHome):
    """The worker resolves a stance the way the CLI does: defaults, config, environment."""

    def config(self, stances):
        path = self.home / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"stances": stances}), encoding="utf-8")

    def test_the_defaults_are_the_example_configs_and_cannot_drift_from_it(self):
        self.assertEqual(usage_log.DEFAULT_STANCES, CFG["stances"])

    def test_a_missing_config_yields_the_defaults_not_an_empty_map(self):
        self.assertEqual(usage_log.stances({}), usage_log.DEFAULT_STANCES)

    def test_the_config_wins_over_the_defaults(self):
        self.config({"commits": "conventional", "testing": "off"})
        resolved = usage_log.stances({})
        self.assertEqual(resolved["commits"], "conventional")
        self.assertEqual(resolved["testing"], "off")
        self.assertEqual(resolved["autonomy"], usage_log.DEFAULT_STANCES["autonomy"])

    def test_the_environment_wins_over_the_config_for_a_hyphenated_dimension(self):
        self.config({"plan-ceremony": "review-card", "commits": "conventional"})
        resolved = usage_log.stances({"HARNESS_STANCE_PLAN_CEREMONY": "lightweight"})
        self.assertEqual(resolved["plan-ceremony"], "lightweight")
        self.assertEqual(resolved["commits"], "conventional")


class RescanStanceTests(TempHome):
    """A backfill knows the transcript; it does not know the stance the session ran under."""

    def project(self):
        project = self.home / ".claude" / "projects" / "a-repo"
        project.mkdir(parents=True)
        return rules_fixture(project / "s-1.jsonl")

    def test_a_rescan_records_the_rules_it_could_not_have_had_before(self):
        self.project()
        self.assertEqual(usage_log.rescan(30), 1)
        rec = json.loads((self.home / ".local/state/agent-harness/usage.jsonl").read_text())
        self.assertEqual(rec["rules"]["transcript-hygiene/whole-file-cat"], 1)
        self.assertEqual(rec["counts"]["agent"], 1)

    def test_a_rescan_stamps_its_guess_at_the_stances_and_says_it_guessed(self):
        self.project()
        usage_log.rescan(30)
        rec = json.loads((self.home / ".local/state/agent-harness/usage.jsonl").read_text())
        self.assertEqual(rec["stances_source"], "rescan")
        self.assertEqual(rec["stances"]["commits"], usage_log.stances()["commits"])
        # The stamped stances still drive the detectors, so the commit hits do backfill.
        self.assertIn("commits/non-conventional", rec["rules"])

    def test_a_rescan_leaves_the_stances_a_live_session_recorded(self):
        self.project()
        usage_log.upsert({"session_id": "s-1", "ended": STAMPS[4],
                          "stances": {"commits": "conventional"}})
        usage_log.rescan(30)
        rec = json.loads((self.home / ".local/state/agent-harness/usage.jsonl").read_text())
        self.assertEqual(rec["stances"], {"commits": "conventional"})
        self.assertNotIn("stances_source", rec)
        self.assertIn("rules", rec)


class HookEntryTests(TempHome):
    def test_malformed_stdin_exits_zero_quickly(self):
        start = time.time()
        out = subprocess.run([sys.executable, str(REPO / "claude" / "hooks" / "usage-log.py")],
                             input="not json", capture_output=True, text=True, timeout=10)
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stdout, "")
        self.assertLess(time.time() - start, 1.0)

    def test_registration_matches_the_ownership_contract(self):
        # The coordinator owns the event; the ownership entry still names the policy behind it.
        template = harness.runtime_template()
        commands = [h["command"] for e in template["hooks"]["SessionEnd"] for h in e["hooks"]]
        self.assertTrue(any("# harness:runtime-sessionend" in c for c in commands))
        self.assertEqual(OWNERSHIP["claude"]["hook_ids"]["usage-log"],
                         {"event": "SessionEnd", "always": True})
        merged = harness.merge_claude_settings({}, template, CFG)
        self.assertIn("runtime-sessionend", harness.claude_projection(merged, template)["hooks"])
        self.assertNotIn("SessionEnd", harness.strip_claude_settings(merged, template).get("hooks", {}))


if __name__ == "__main__":
    unittest.main()

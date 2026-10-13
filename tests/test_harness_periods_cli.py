# SPDX-License-Identifier: MIT
"""`citizen harness periods` prints what `GET /api/harness/periods` answers (#1321).
Run: python3 -m unittest discover -s tests -p test_harness_periods_cli.py"""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_harness import REPO  # noqa: E402
from harness_core.studio import headless, periods  # noqa: E402

CLI = REPO / "bin" / "harness"
ROUTE = "/api/harness/periods"


def usage_row(when, voice):
    stamp = when.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {"kind": "session", "repo": "proj", "harness_version": "0.15.0", "ended": stamp,
            "started": stamp, "profile_fingerprint": "a",
            "stances": {"voice": voice, "cost": "balanced"}, "input": 10, "output": 100,
            "cache_read": 900, "cache_write": 90}


class HarnessPeriodsCommandTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(os.path.realpath(tmp.name)) / "home"
        state = self.home / ".local" / "state" / "agent-harness"
        state.mkdir(parents=True)
        now = dt.datetime.now(dt.timezone.utc)
        rows = [usage_row(now - dt.timedelta(days=n), "answer-card") for n in (20, 12)]
        rows += [usage_row(now - dt.timedelta(days=n), "concise") for n in (2, 1)]
        (state / "usage.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("HARNESS_") and key != "CLAUDE_CONFIG_DIR"}
        self.env.update({"HOME": str(self.home), "HARNESS_HOME": str(self.home)})
        periods.clear_cache()
        self.addCleanup(periods.clear_cache)

    def cli(self, *argv):
        done = subprocess.run([sys.executable, str(CLI), "harness", "periods", *argv],
                              cwd=REPO, env=self.env, capture_output=True, text=True, timeout=120)
        return done.returncode, done.stdout, done.stderr

    def route(self, request):
        with mock.patch.dict(os.environ, self.env, clear=True):
            return headless.call_route(REPO, self.home / ".local" / "state" / "agent-harness"
                                       / "studio", "GET", ROUTE, request)

    def test_json_output_equals_the_route_answer(self):
        for surface in ("stances", "sources"):
            with self.subTest(surface=surface):
                status, sent = self.route({"surface": surface, "days": "30"})
                code, out, err = self.cli("--surface", surface, "--days", "30", "--json")
                self.assertEqual((status, code), (200, 0), err)
                self.assertEqual(json.loads(out), sent)
        status, sent = self.route({"surface": "stances"})
        code, out, _err = self.cli("--surface", "stances", "--json")
        self.assertEqual(json.loads(out), sent)
        voice = [(r["setting_label"], r["status"]) for r in sent["rows"] if r["item_id"] == "voice"]
        self.assertEqual(voice, [("answer-card", "measured"), ("concise", "measuring")])

    def test_a_refusal_exits_non_zero_with_the_route_error_code(self):
        for argv, request, error in (
                (["--surface", "nonesuch"], {"surface": "nonesuch"}, "unknown_surface"),
                (["--surface", "stances", "--days", "0"], {"surface": "stances", "days": "0"},
                 "invalid_request"),
                (["--surface", "stances", "--days", "x"], {"surface": "stances", "days": "x"},
                 "invalid_request")):
            with self.subTest(argv=argv):
                status, sent = self.route(request)
                code, out, _err = self.cli(*argv, "--json")
                self.assertEqual((status, sent), (400, {"error": error}))
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(out), sent)
                code, out, err = self.cli(*argv)
                self.assertEqual((code, out), (2, ""))
                self.assertIn(error, err)

    def test_text_output_lists_each_period(self):
        code, out, err = self.cli("--surface", "stances")
        self.assertEqual(code, 0, err)
        voice = [line.split("\t") for line in out.splitlines() if line.startswith("voice\t")]
        self.assertEqual([(cells[1], cells[4]) for cells in voice],
                         [("answer-card", "measured"), ("concise", "measuring")])

    def test_the_headless_route_refuses_a_field_outside_its_query(self):
        self.assertEqual(self.route({"surface": "stances", "other": "1"}),
                         (400, {"error": "invalid_request"}))


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: MIT
"""Baseline: start from what you have (AH-S382, #1334). The reader and recorder, the baseline
boundary source in periods, and the two routes.
Run: python3 -m unittest discover -s tests -p test_studio_baseline.py"""
import datetime as dt
import json
import os
import stat
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_studio_security as studio_security  # noqa: E402
from test_studio_periods import NOW, Home, days_ago, stamp, usage_row  # noqa: E402
from harness_core.studio import baseline, periods, server  # noqa: E402

GET = "/api/baseline"
RECORD = "/api/baseline/record"


def write_baselines(home, *rows):
    path = periods.baselines_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def baseline_row(when, stances, ident="b"):
    return {"schema_version": 1, "id": ident, "ts": stamp(when), "label": "",
            "config": {"stances": stances}, "plugins": []}


def write_plugins(home):
    claude = Path(home) / ".claude"
    (claude / "plugins").mkdir(parents=True, exist_ok=True)
    (claude / "settings.json").write_text(json.dumps({"enabledPlugins": {
        "superpowers@market": True, "model-citizen@mc": True, "off@market": False}}))
    install = claude / "plugins" / "cache" / "sp"
    (install / "skills" / "one").mkdir(parents=True)
    (install / "skills" / "one" / "SKILL.md").write_text("x")
    (claude / "plugins" / "installed_plugins.json").write_text(json.dumps({"plugins": {
        "superpowers@market": [{"scope": "user", "installPath": str(install)}]}}))


class BaselinePeriodsTests(unittest.TestCase):
    """A recorded baseline starts a period on every surface; none leaves periods as they were."""

    def setUp(self):
        self.home = Home(self)

    def found(self, unit="voice"):
        return periods.periods("stances", unit, home=self.home.root, now=NOW)

    def test_no_baseline_leaves_every_answer_unchanged(self):
        self.home.usage([usage_row(days_ago(n)) for n in (12, 9, 2)])
        self.home.config({"voice": "concise"})
        before = periods.efficacy_rows("stances", home=self.home.root, now=NOW)
        self.assertEqual(before["note"], periods.NOTE)
        self.assertFalse(any("baseline" in row for row in before["rows"]))
        write_baselines(self.home.root)  # an empty file is still no baseline
        periods.clear_cache()
        self.assertEqual(periods.efficacy_rows("stances", home=self.home.root, now=NOW), before)

    def test_a_baseline_starts_a_period_at_the_value_it_saw(self):
        self.home.usage([usage_row(days_ago(n)) for n in (12, 9, 2)])
        self.home.config({"voice": "concise"})
        write_baselines(self.home.root, baseline_row(days_ago(5), {"voice": "concise"}))
        found = self.found()
        self.assertEqual([(p["value"], p["source"], p["rows"]) for p in found],
                         [("concise", "baseline", 2), ("concise", "baseline", 1)])
        self.assertEqual(found[1]["start"], periods.iso(days_ago(5)))
        answer = periods.efficacy_rows("stances", "voice", home=self.home.root, now=NOW)
        self.assertNotIn("note", answer)
        before, after = answer["rows"]
        self.assertNotIn("baseline", before)
        self.assertIs(after["baseline"], True)
        self.assertEqual(after["period"]["start"], periods.iso(days_ago(5)))

    def test_a_baseline_after_a_change_to_the_same_value_is_never_collapsed(self):
        rows = [usage_row(days_ago(n), voice="answer-card") for n in (20, 15)]
        rows += [usage_row(days_ago(n), voice="concise") for n in (10, 6, 2)]
        self.home.usage(rows)
        write_baselines(self.home.root, baseline_row(days_ago(4), {"voice": "concise"}))
        self.assertEqual([(p["value"], p["source"]) for p in self.found()],
                         [("answer-card", "usage"), ("concise", "usage"),
                          ("concise", "baseline")])

    def test_a_baseline_never_explains_away_a_usage_change(self):
        rows = [usage_row(days_ago(n), voice="answer-card") for n in (20, 15)]
        rows += [usage_row(days_ago(n, 12), voice="concise") for n in (5, 3)]
        self.home.usage(rows)
        write_baselines(self.home.root, baseline_row(days_ago(5), {"voice": "concise"}))
        self.assertEqual([(p["value"], p["source"]) for p in self.found()],
                         [("answer-card", "usage"), ("concise", "usage"),
                          ("concise", "baseline")])

    def test_a_baseline_outside_the_window_starts_nothing(self):
        self.home.usage([usage_row(days_ago(n)) for n in (12, 2)])
        write_baselines(self.home.root, baseline_row(days_ago(40), {"voice": "concise"}))
        self.assertEqual([p["source"] for p in self.found()], ["none"])

    def test_a_surface_without_a_config_reader_takes_the_value_observed_before(self):
        surface = periods.Surface("probe", lambda ctx: {}, periods.usage_metrics)
        write_baselines(self.home.root, baseline_row(days_ago(5), {}))
        observations = [(days_ago(9), "a", "session/local"), (days_ago(4), "b", "session/local")]
        found = periods.baseline_boundaries(self.home.root, surface, "u", days_ago(30),
                                            observations, "b")
        self.assertEqual([(b["from"], b["to"], b["source"]) for b in found],
                         [("a", "a", "baseline")])
        self.assertEqual(periods.baseline_boundaries(self.home.root, surface, "u", days_ago(30),
                                                     [], "now")[0]["to"], "now")


class BaselineModuleTests(unittest.TestCase):
    def setUp(self):
        self.home = Home(self)
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("CLAUDE_CONFIG_DIR", None)

    def test_a_fresh_home_reads_as_no_config_and_no_baseline(self):
        answer = baseline.read(self.home.root)
        self.assertEqual(set(answer), {"schema_version", "current", "baselines", "commands"})
        self.assertEqual(answer["baselines"], [])
        current = answer["current"]
        self.assertEqual((current["config_present"], current["stances"], current["plugins"],
                          current["settings"]), (False, {}, [], []))
        self.assertEqual(answer["commands"]["show"], "citizen baseline show --json")

    def test_current_reads_config_stances_rules_settings_and_third_party_plugins(self):
        config = {"stances": {"voice": "concise", "cost": "balanced", "bad": 3},
                  "rules": {"cache-hygiene": "off"}, "identity": {"name": "Casey"},
                  "telemetry": {"headers_env": "OTEL_HEADERS"}}
        (self.home.config_dir / "config.json").write_text(json.dumps(config))
        write_plugins(self.home.root)
        current = baseline.current(self.home.root)
        self.assertTrue(current["config_present"])
        self.assertEqual(current["stances"], {"cost": "balanced", "voice": "concise"})
        self.assertEqual(current["rules"], {"cache-hygiene": "off"})
        settings = {s["path"]: s["value"] for s in current["settings"]}
        self.assertEqual(settings["identity.name"], "Casey")
        self.assertEqual(settings["telemetry.headers_env"],
                         {"source": "environment", "name": "OTEL_HEADERS"})
        self.assertEqual([(p["label"], p["modules"]) for p in current["plugins"]],
                         [("superpowers", 1)])

    def test_record_appends_a_redacted_snapshot_and_touches_no_config(self):
        config_file = self.home.config_dir / "config.json"
        config_file.write_text(json.dumps({"stances": {"voice": "concise"},
                                           "telemetry": {"api_key": "s3cret"}}))
        config_bytes = config_file.read_bytes()
        write_plugins(self.home.root)
        first = baseline.record(self.home.root, "before", now=days_ago(2))
        second = baseline.record(self.home.root, "", now=days_ago(1))
        self.assertEqual(config_file.read_bytes(), config_bytes)
        self.assertEqual(set(first), {"schema_version", "baseline"})
        self.assertEqual(set(first["baseline"]), {"id", "ts", "label", "stances", "rules",
                                                  "plugins"})
        self.assertEqual((first["baseline"]["label"], first["baseline"]["plugins"],
                          first["baseline"]["ts"]), ("before", ["superpowers"],
                                                     periods.iso(days_ago(2))))
        path = periods.baselines_path(self.home.root)
        self.assertEqual(path.parent, periods.state_dir(self.home.root) / "studio")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(len(lines), 2)
        self.assertNotIn("s3cret", path.read_text())
        self.assertEqual(lines[0]["config"]["telemetry"]["api_key"], {"configured": True})
        listed = baseline.read(self.home.root)["baselines"]
        self.assertEqual([b["id"] for b in listed],
                         [second["baseline"]["id"], first["baseline"]["id"]])

    def test_record_refuses_a_label_that_is_not_short_printable_text(self):
        for label in ("x" * (baseline.LABEL_MAX + 1), "a\nb", "\x00", 3, None):
            with self.subTest(label=label), self.assertRaises(baseline.BaselineError):
                baseline.record(self.home.root, label)
        self.assertFalse(periods.baselines_path(self.home.root).exists())


class BaselineRouteTests(studio_security.StudioSecurityFixture):
    def setUp(self):
        super().setUp()
        _issued, status, headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        self.cookie_value = self.cookie(headers)
        _status, _headers, body = self.request("GET", "/api/session",
                                               {"Cookie": self.cookie_value})
        self.csrf = json.loads(body)["csrf_token"]
        config = self.home / ".config" / "agent-harness" / "config.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({"stances": {"voice": "concise"}}))

    def get(self, path, cookie=True):
        status, _headers, body = self.request("GET", path,
                                              {"Cookie": self.cookie_value} if cookie else {})
        return status, json.loads(body)

    def post(self, path, payload, csrf=True):
        body = json.dumps(payload).encode("utf-8")
        headers = {"Cookie": self.cookie_value, "Content-Type": "application/json",
                   "Content-Length": str(len(body)), "Origin": self.record["url"].rstrip("/")}
        if csrf:
            headers["X-Studio-CSRF"] = self.csrf
        status, _headers, answer = self.request("POST", path, headers, body)
        return status, json.loads(answer)

    def test_read_record_and_read_again(self):
        status, document = self.get(GET)
        self.assertEqual(status, 200, document)
        server.BASELINE.validate(document)
        self.assertEqual(document["baselines"], [])
        self.assertEqual(document["current"]["stances"], {"voice": "concise"})

        status, recorded = self.post(RECORD, {"label": "start"})
        self.assertEqual(status, 200, recorded)
        server.BASELINE_RECORDED.validate(recorded)
        self.assertEqual(recorded["baseline"]["label"], "start")

        status, document = self.get(GET)
        self.assertEqual([b["id"] for b in document["baselines"]], [recorded["baseline"]["id"]])
        status, answer = self.get("/api/harness/periods?surface=stances")
        self.assertEqual(status, 200, answer)
        self.assertTrue(any(row.get("baseline") for row in answer["rows"]),
                        "the recorded baseline did not start a period")

    def test_anonymous_is_refused_on_both_routes(self):
        status, document = self.get(GET, cookie=False)
        self.assertEqual((status, document), (401, {"error": "unauthorized"}))
        status, _headers, body = self.request("POST", RECORD,
                                              {"Content-Type": "application/json"}, b"{}")
        self.assertEqual((status, json.loads(body)), (401, {"error": "unauthorized"}))

    def test_record_without_the_csrf_token_is_refused_and_records_nothing(self):
        status, document = self.post(RECORD, {"label": ""}, csrf=False)
        self.assertEqual((status, document), (403, {"error": "request_refused"}))
        self.assertFalse(periods.baselines_path(self.home).exists())

    def test_record_refuses_a_bad_body(self):
        for payload in ({}, {"label": 3}, {"label": "x" * 81}, {"label": "a\x07"},
                        {"label": "", "extra": 1}):
            status, document = self.post(RECORD, payload)
            self.assertEqual((status, document), (400, {"error": "invalid_request"}), payload)
        self.assertFalse(periods.baselines_path(self.home).exists())

    def test_the_read_refuses_a_query_string(self):
        status, document = self.get(GET + "?x=1")
        self.assertEqual((status, document), (404, {"error": "not_found"}))


if __name__ == "__main__":
    unittest.main()

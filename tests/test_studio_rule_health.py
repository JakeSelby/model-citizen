# SPDX-License-Identifier: MIT
"""Rule health shows each rule as the engines report it (AH-S310, #994).

The statuses are `citizen usage --rules`'s own; hit figures, precision, cost and effect come from
the engines' JSON commands and read "not measured" where those have nothing. "Try without it"
makes a draft with the rule off and changes nothing live.

The committed fixture `studio/tests/fixtures/rule-health.json`, which
`studio/tests/rule-health.test.ts` feeds through the page, is `report()` over the fixed engine
documents below. Regenerate it with:

    python3 tests/test_studio_rule_health.py --write
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import draft_support  # noqa: E402
from test_harness import REPO  # noqa: E402
from harness_core import rule_coverage  # noqa: E402
from harness_core.studio import rule_health, selection_editing, server  # noqa: E402

FIXTURE = REPO / "studio" / "tests" / "fixtures" / "rule-health.json"
LINE = re.compile(r"^  (measured|dark|unmeasured) +(\S+) +(\S+?)(?:: (.*))?$")
Rule = rule_coverage.Rule


def fake_rules():
    """One rule in each state, one of whose detectors is under the floor, and a stance."""
    rules = [
        Rule("secrets", "/repo/primitives/rules/secrets.md", "measured", "",
             ["secrets/git-add-secret-file", "secrets/secret-in-write"]),
        Rule("voice-and-format", "/repo/primitives/rules/voice-and-format.md", "measured", "",
             ["voice/banned-opener"]),
        Rule("conciseness", "/repo/primitives/rules/conciseness.md", "dark",
             "a comment's redundancy is a judgment over the codebase, not a transcript pattern", []),
        Rule("working-style", "/repo/primitives/rules/working-style.md", "unmeasured",
             rule_coverage.NO_DETECTOR, []),
        Rule("autonomy", "/repo/primitives/stances/autonomy/execute.md", "measured", "",
             ["autonomy/denied-by-grade"]),
    ]
    findings = [rule_coverage.Finding("/repo/.ruleprobe/detectors.yaml", 4, "unknown command key 'x'")]
    return rules, findings


def hits_document(days, sessions, groups):
    return {"schema_version": 1, "report": "rules", "by": "rule", "days": days,
            "measured_sessions": sessions, "groups": groups, "coverage": []}


def group(did, hits, seen, total):
    return {"id": did, "hits": hits, "sessions": seen, "of": total,
            "share": seen / float(total) if total else 0.0, "note": ""}


def engine_documents():
    hits = {
        "7": hits_document(7, 10, [group("secrets/git-add-secret-file", 0, 0, 10),
                                   group("secrets/secret-in-write", 0, 0, 10),
                                   group("voice/banned-opener", 2, 1, 10),
                                   group("autonomy/denied-by-grade", 0, 0, 10)]),
        "30": hits_document(30, 40, [group("secrets/git-add-secret-file", 1, 1, 40),
                                     group("secrets/secret-in-write", 0, 0, 40),
                                     group("voice/banned-opener", 9, 6, 40),
                                     group("autonomy/denied-by-grade", 0, 0, 40)]),
        "90": hits_document(90, 60, [group("secrets/git-add-secret-file", 1, 1, 60),
                                     group("secrets/secret-in-write", 0, 0, 60),
                                     group("voice/banned-opener", 12, 8, 60),
                                     group("autonomy/denied-by-grade", 0, 0, 60)]),
    }

    def scored(precision):
        return {"scored": True, "precision": precision, "recall": 1.0, "source": "corpus"}

    precision = {"floor": 0.9, "below_floor": ["voice/banned-opener"], "unscored": [],
                 "detectors": {"secrets/git-add-secret-file": scored(1.0),
                               "secrets/secret-in-write": scored(1.0),
                               "voice/banned-opener": scored(0.75),
                               "autonomy/denied-by-grade": {"scored": False, "precision": None}}}
    cost = {"rows": [
        {"module": "rules/secrets", "state": "on", "tokens": {"tokens": 210, "estimand": "soft estimate"},
         "effect": {"estimand": "measured", "arm": "no-secrets", "measure": "cost", "effect": -0.04,
                    "interval": [-0.09, 0.01], "n": 6, "reading": "inconclusive", "exploratory": False}},
        {"module": "rules/voice-and-format", "state": "on",
         "tokens": {"tokens": 95, "estimand": "soft estimate"}, "effect": "unmeasured"},
        {"module": "rules/conciseness", "state": "on",
         "tokens": {"tokens": 218, "estimand": "soft estimate"}, "effect": "unmeasured"},
        {"module": "rules/working-style", "state": "off", "tokens": "not loaded", "effect": "unmeasured"},
        {"module": "stances/autonomy", "state": "execute",
         "tokens": {"tokens": 120, "estimand": "soft estimate"}, "effect": "unmeasured"},
    ]}
    return hits, precision, cost


def fake_runner(fail=(), hits=None, precision=None, cost=None, advice=None, calls=None):
    base_hits, base_precision, base_cost = engine_documents()
    hits = base_hits if hits is None else hits
    precision = base_precision if precision is None else precision
    cost = base_cost if cost is None else cost

    def run(argv):
        argv = list(argv)
        if calls is not None:
            calls.append(argv)
        name = ("hits-" + argv[-1] if argv[:2] == ["usage", "--rules"] else
                "cost" if argv[0] == "scorecard" else
                "precision" if argv[0] == "scripts/detector_corpus.py" else
                "advice" if argv[:3] == ["usage", "--by", "adherence"] else None)
        if name in fail:
            raise OSError(name + " is unavailable")
        if name and name.startswith("hits-"):
            return hits[argv[-1]]
        return {"cost": cost, "precision": precision, "advice": advice}[name]

    return run


def fixture_report(**runner):
    with mock.patch.object(rule_health, "_classified", return_value=fake_rules()), \
            mock.patch.object(rule_health, "_advice_modules", return_value={}):
        document = rule_health.report(REPO, cwd=Path("/repo"), run=fake_runner(**runner), home="/home/someone")
    document["generated_at"] = "2026-10-03T00:00:00Z"
    return document


def by_rule(document):
    return dict((row["rule"], row) for row in document["rows"])


def isolated_env(home):
    env = dict((k, v) for k, v in os.environ.items() if not k.startswith(("HARNESS_", "CLAUDE")))
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
               XDG_STATE_HOME=str(home / ".local" / "state"),
               CLAUDE_CONFIG_DIR=str(home / ".claude"), CODEX_HOME=str(home / ".codex"))
    return env


class StatusParity(unittest.TestCase):
    """AC1: the table shows the same status and reason for every rule as `citizen usage --rules`."""

    def test_every_rule_reads_the_status_and_reason_usage_rules_prints(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, cwd = Path(tmp) / "home", Path(tmp) / "repo"
            home.mkdir()
            cwd.mkdir()
            env = isolated_env(home)
            printed = subprocess.run([sys.executable, str(REPO / "bin" / "harness"), "usage", "--rules"],
                                     cwd=str(cwd), env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(printed.returncode, 0, printed.stderr)
            # A shadowed rule's reason names its owner relative to the process directory, so the
            # in-process classification runs where the command ran, as the Studio's does.
            previous = os.getcwd()
            os.chdir(str(cwd))
            try:
                with mock.patch.dict(os.environ, env, clear=True):
                    document = rule_health.report(REPO, cwd=cwd)
            finally:
                os.chdir(previous)
        lines = printed.stdout.split("\nrules: ", 1)[1].splitlines()
        cli = []
        for line in lines[1:]:
            match = LINE.match(line)
            if not match:
                break
            cli.append((match.group(1), match.group(2), match.group(3), match.group(4) or ""))
        studio = [(row["state"], row["rule"], row["path"], row["reason"]) for row in document["rows"]]
        self.assertTrue(cli)
        self.assertEqual(studio, cli)
        summary = document["summary"]
        self.assertTrue(lines[0].startswith("%d measured, %d dark, %d unmeasured" % (
            summary["measured"], summary["dark"], summary["unmeasured"])), lines[0])
        self.assertIn(summary["measured_text"], lines[0])

    def test_the_route_names_usage_rules_as_its_command(self):
        routes = dict((route.path, route) for route in server.ROUTES.entries)
        self.assertEqual(routes["/api/rules/health"].cli_command, ("citizen", "usage", "--rules", "--json"))
        self.assertIsNone(routes["/api/rules/health"].parity_exemption)
        self.assertEqual(routes["/api/rules/try-without"].cli_command,
                         ("citizen", "draft", "create", "{draft}", "--json"))


class Figures(unittest.TestCase):
    def test_hit_figures_are_the_engine_groups_per_window_labelled_exploratory(self):
        row = by_rule(fixture_report())["voice-and-format"]
        hits = row["hits"]
        self.assertEqual(hits["label"], "exploratory")
        self.assertEqual(hits["windows"]["30"]["measured_sessions"], 40)
        self.assertEqual(hits["windows"]["30"]["detectors"]["voice/banned-opener"],
                         {"hits": 9, "sessions": 6, "of": 40, "share": 0.15, "note": ""})
        self.assertEqual(hits["last_fired_within_days"], 7)
        self.assertEqual(by_rule(fixture_report())["secrets"]["hits"]["last_fired_within_days"], 30)

    def test_a_detector_under_the_precision_floor_marks_its_rule_unreliable(self):
        """AC3."""
        document = fixture_report()
        voice = by_rule(document)["voice-and-format"]
        self.assertEqual(document["precision_floor"], 0.9)
        self.assertFalse(voice["hits"]["reliable"])
        self.assertEqual(voice["hits"]["reason"], "voice/banned-opener precision 0.75 is under the 0.90 floor")
        self.assertTrue(voice["detectors"][0]["below_floor"])
        self.assertTrue(by_rule(document)["secrets"]["hits"]["reliable"])

    def test_a_detector_with_no_measured_precision_is_unreliable_too(self):
        autonomy = by_rule(fixture_report())["autonomy"]
        self.assertFalse(autonomy["hits"]["reliable"])
        self.assertEqual(autonomy["detectors"][0]["status"], "not measured")
        self.assertIn("has no measured precision", autonomy["hits"]["reason"])

    def test_dark_and_unmeasured_rules_read_not_measured_with_their_reason(self):
        rows = by_rule(fixture_report())
        for name in ("conciseness", "working-style"):
            self.assertEqual(rows[name]["hits"]["status"], "not measured")
            self.assertEqual(rows[name]["hits"]["reason"], rows[name]["reason"])
            self.assertTrue(rows[name]["reason"])
            self.assertEqual(rows[name]["hits"]["windows"], {})

    def test_cost_and_effect_are_the_scorecards_and_absent_ones_say_not_measured(self):
        rows = by_rule(fixture_report())
        self.assertEqual(rows["secrets"]["tokens"],
                         {"status": "measured", "tokens": 210, "estimand": "soft estimate", "reason": ""})
        self.assertEqual(rows["secrets"]["effect"]["reading"], "inconclusive")
        self.assertEqual(rows["secrets"]["effect"]["interval"], [-0.09, 0.01])
        self.assertEqual(rows["conciseness"]["effect"]["status"], "not measured")
        self.assertEqual(rows["working-style"]["tokens"],
                         {"status": "not measured", "reason": "the scorecard reads not loaded"})

    def test_advice_is_not_measured_unless_a_recommendation_names_the_rule(self):
        rows = by_rule(fixture_report())
        self.assertEqual(rows["secrets"]["advice"]["status"], "not measured")
        advice = {"groups": [{"section": "adherence", "kind": "secret-scan", "figures": {"rate": {
            "label": "measured", "value": 0.5}}}], "footer": "exploratory footer"}
        calls = []
        with mock.patch.object(rule_health, "_classified", return_value=fake_rules()), \
                mock.patch.object(rule_health, "_advice_modules", return_value={"rules/secrets": ["secret-scan"]}):
            document = rule_health.report(REPO, cwd=Path("/repo"), run=fake_runner(advice=advice, calls=calls))
        secrets = by_rule(document)["secrets"]["advice"]
        self.assertEqual(secrets["status"], "measured")
        self.assertEqual(secrets["groups"], advice["groups"])
        self.assertIn(["usage", "--by", "adherence", "--json", "--days", "30"], calls)
        self.assertEqual(by_rule(document)["conciseness"]["advice"]["status"], "not measured")

    def test_the_real_adherence_registry_names_no_rule_so_advice_is_never_requested(self):
        calls = []
        with mock.patch.object(rule_health, "_classified", return_value=fake_rules()):
            rule_health.report(REPO, cwd=Path("/repo"), run=fake_runner(calls=calls))
        self.assertNotIn("adherence", [argv[2] for argv in calls if len(argv) > 2])

    def test_an_engine_that_fails_leaves_its_column_not_measured_and_the_report_partial(self):
        document = fixture_report(fail=("hits-7", "precision", "cost"))
        self.assertEqual(document["status"], "partial")
        self.assertEqual(document["sources"]["precision"]["status"], "unavailable")
        self.assertEqual(document["sources"]["hits"]["message"], "hits-7 is unavailable")
        secrets = by_rule(document)["secrets"]
        self.assertEqual(secrets["hits"]["windows"]["7"]["status"], "not measured")
        self.assertEqual(secrets["hits"]["windows"]["30"]["status"], "measured")
        self.assertFalse(secrets["hits"]["reliable"])
        self.assertEqual(secrets["tokens"]["status"], "not measured")
        self.assertIsNone(document["precision_floor"])

    def test_a_window_with_no_measured_sessions_is_not_measured_never_zero(self):
        hits, _precision, _cost = engine_documents()
        hits["7"] = hits_document(7, 0, [])
        row = by_rule(fixture_report(hits=hits))["voice-and-format"]
        self.assertEqual(row["hits"]["windows"]["7"],
                         {"status": "not measured", "measured_sessions": 0, "detectors": {},
                          "reason": "no measured sessions in the last 7 day(s)"})
        self.assertEqual(row["hits"]["last_fired_within_days"], 30)

    def test_try_without_is_offered_only_for_a_rule_switched_on(self):
        rows = by_rule(fixture_report())
        self.assertTrue(rows["conciseness"]["try_without"]["available"])
        self.assertFalse(rows["working-style"]["try_without"]["available"])
        self.assertFalse(rows["autonomy"]["try_without"]["available"])
        self.assertIn("variant", rows["autonomy"]["try_without"]["reason"])

    def test_findings_and_paths_are_shortened_as_usage_rules_prints_them(self):
        document = fixture_report()
        self.assertEqual(by_rule(document)["secrets"]["path"], "primitives/rules/secrets.md")
        self.assertEqual(document["findings"],
                         [{"path": ".ruleprobe/detectors.yaml", "line": 4, "reason": "unknown command key 'x'"}])

    def test_the_committed_fixture_is_this_report(self):
        self.assertEqual(json.loads(FIXTURE.read_text(encoding="utf-8")), fixture_report())


class TryWithout(unittest.TestCase):
    """AC2: a dark rule's "Try without it" opens a draft with it off and its test ready to run."""

    def test_a_name_that_is_not_a_rule_unit_is_refused_before_anything_runs(self):
        create = mock.Mock()
        for bad in ("", "../secrets", "a b", "x" * 80, 7):
            with self.assertRaises(rule_health.RuleHealthError) as caught:
                rule_health.try_without(REPO, bad, create)
            self.assertEqual(caught.exception.code, "invalid-rule")
        create.assert_not_called()

    def test_a_rule_not_switched_on_is_refused_before_a_draft_is_made(self):
        create = mock.Mock()
        with mock.patch.object(rule_health, "_selected_rules", return_value={"secrets": "off"}):
            for unit in ("secrets", "no-such-rule"):
                with self.assertRaises(rule_health.RuleHealthError) as caught:
                    rule_health.try_without(REPO, unit, create)
                self.assertEqual(caught.exception.code, "rule-not-switchable")
        create.assert_not_called()

    def test_a_failed_create_is_reported_with_its_code(self):
        with mock.patch.object(rule_health, "_selected_rules", return_value={"secrets": "on"}):
            with self.assertRaises(rule_health.RuleHealthError) as caught:
                rule_health.try_without(REPO, "secrets", lambda root, name: "create-timeout", suffix="x")
        self.assertEqual(caught.exception.code, "create-timeout")

    def test_the_draft_has_the_rule_off_and_nothing_live_changes(self):
        # A cleanup, not a `with`: the draft's worktree lives in this home until it is discarded.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(os.path.realpath(tmp.name)) / "home"
        home.mkdir()
        env = isolated_env(home)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        suffix = draft_support.draft_name("")
        name = "without-conciseness-" + suffix
        draft_support.register_draft_cleanup(self, name, env)
        with mock.patch.dict(os.environ, env, clear=True):
            result = rule_health.try_without(REPO, "conciseness", server._run_draft_create, suffix=suffix)
            read = selection_editing.read(REPO, name)
        self.assertFalse((home / ".config" / "agent-harness" / "config.json").exists())
        self.assertEqual(result["draft"]["name"], name)
        self.assertEqual(result["changes"], {"rules.conciseness": "off"})
        self.assertEqual(result["commands"][0], "citizen draft create %s --json" % name)
        self.assertIn('{"rules.conciseness": "off"}', result["commands"][1])
        self.assertEqual(read["status"], "ready", read["message"])
        self.assertEqual(read["current"]["selection"]["rules"]["conciseness"], "off")
        # Its test is ready to run: the draft test plans against exactly this revision.
        self.assertEqual(read["draft"]["revision"], result["draft"]["revision"])


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        FIXTURE.write_text(json.dumps(fixture_report(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("wrote " + str(FIXTURE))
    else:
        unittest.main()

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

    def scored(precision, recall=1.0):
        return {"scored": True, "precision": precision, "recall": recall, "source": "corpus"}

    # The engine's verdict is precision or recall under the floor: voice's precision clears it and
    # its recall does not, so only the engine's list can say it is below.
    precision = {"floor": 0.9, "below_floor": ["voice/banned-opener"], "unscored": ["autonomy/denied-by-grade"],
                 "detectors": {"secrets/git-add-secret-file": scored(1.0),
                               "secrets/secret-in-write": scored(1.0),
                               "voice/banned-opener": scored(0.95, 0.6),
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
        self.assertEqual(routes["/api/rules/health"].cli_command,
                         ("citizen", "usage", "--rules", "--health", "--json"))
        self.assertIsNone(routes["/api/rules/health"].parity_exemption)
        self.assertEqual(routes["/api/rules/try-without"].cli_command,
                         ("citizen", "draft", "try-without", "{rule}", "--json"))


class Figures(unittest.TestCase):
    def test_hit_figures_are_the_engine_groups_per_window_labelled_exploratory(self):
        row = by_rule(fixture_report())["voice-and-format"]
        hits = row["hits"]
        self.assertEqual(hits["label"], "exploratory")
        self.assertEqual(hits["windows"]["30"]["measured_sessions"], 40)
        self.assertEqual(hits["windows"]["30"]["detectors"]["voice/banned-opener"],
                         {"hits": 9, "sessions": 6, "of": 40, "share": 0.15, "note": ""})
        self.assertEqual(hits["last_fired"], {"state": "fired", "days": 7})
        self.assertEqual(by_rule(fixture_report())["secrets"]["hits"]["last_fired"], {"state": "fired", "days": 30})

    def test_a_detector_under_the_precision_floor_marks_its_rule_unreliable(self):
        """AC3."""
        document = fixture_report()
        voice = by_rule(document)["voice-and-format"]
        self.assertEqual(document["precision_floor"], 0.9)
        self.assertFalse(voice["hits"]["reliable"])
        self.assertEqual(voice["hits"]["reason"],
                         "voice/banned-opener is below the engine's 0.90 floor (precision 0.95, recall 0.60)")
        self.assertTrue(voice["detectors"][0]["below_floor"])
        self.assertTrue(by_rule(document)["secrets"]["hits"]["reliable"])

    def test_the_floor_verdict_is_the_engines_list_not_a_studio_comparison(self):
        _hits, precision, _cost = engine_documents()
        precision["detectors"]["secrets/secret-in-write"]["precision"] = 0.5
        secrets = by_rule(fixture_report(precision=precision))["secrets"]
        self.assertTrue(secrets["hits"]["reliable"])
        self.assertFalse(secrets["detectors"][1]["below_floor"])

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
        self.assertEqual(secrets["hits"]["windows"]["7"]["status"], "unavailable")
        self.assertIn("could not be read: hits-7 is unavailable", secrets["hits"]["windows"]["7"]["reason"])
        self.assertEqual(secrets["hits"]["windows"]["30"]["status"], "measured")
        self.assertIsNone(secrets["hits"]["reliable"])
        self.assertEqual(secrets["hits"]["reason"], "precision unavailable (read failed)")
        self.assertEqual(secrets["detectors"][0]["status"], "unavailable")
        self.assertEqual(secrets["detectors"][0]["reason"], "precision unavailable (read failed)")
        self.assertEqual(secrets["tokens"]["status"], "not measured")
        self.assertIsNone(document["precision_floor"])

    def test_a_widest_window_that_failed_is_never_claimed_as_read(self):
        document = fixture_report(fail=("hits-90",))
        secrets = by_rule(document)["secrets"]["hits"]
        self.assertEqual(secrets["windows"]["90"]["status"], "unavailable")
        self.assertEqual(secrets["last_fired"], {"state": "fired", "days": 30})
        voice_hits, _precision, _cost = engine_documents()
        for days in ("7", "30", "90"):
            for item in voice_hits[days]["groups"]:
                item["sessions"] = item["hits"] = 0
        voice = by_rule(fixture_report(fail=("hits-90",), hits=voice_hits))["voice-and-format"]["hits"]
        self.assertEqual(voice["last_fired"], {"state": "not-fired", "days": 30})

    def test_every_window_failing_reads_unavailable(self):
        voice = by_rule(fixture_report(fail=("hits-7", "hits-30", "hits-90")))["voice-and-format"]["hits"]
        self.assertEqual(voice["last_fired"], {"state": "unavailable", "days": None})

    def test_every_window_read_with_no_sessions_reads_no_sessions_not_unavailable(self):
        hits = dict((days, hits_document(int(days), 0, [])) for days in ("7", "30", "90"))
        voice = by_rule(fixture_report(hits=hits))["voice-and-format"]["hits"]
        self.assertEqual([voice["windows"][d]["status"] for d in ("7", "30", "90")], ["not measured"] * 3)
        self.assertEqual(voice["last_fired"], {"state": "no-sessions", "days": 90})

    def test_a_detector_missing_from_every_report_is_never_called_not_fired(self):
        hits, _precision, _cost = engine_documents()
        for days in ("7", "30", "90"):
            hits[days]["groups"] = [g for g in hits[days]["groups"] if g["id"] != "voice/banned-opener"]
        voice = by_rule(fixture_report(hits=hits))["voice-and-format"]["hits"]
        self.assertIsNone(voice["windows"]["90"]["detectors"]["voice/banned-opener"])
        self.assertEqual(voice["last_fired"], {"state": "not-reported", "days": 90})

    def test_a_failed_adherence_read_is_named_in_the_sources(self):
        with mock.patch.object(rule_health, "_classified", return_value=fake_rules()), \
                mock.patch.object(rule_health, "_advice_modules", return_value={"rules/secrets": ["secret-scan"]}):
            document = rule_health.report(REPO, cwd=Path("/repo"), run=fake_runner(fail=("advice",)))
        self.assertEqual(document["status"], "partial")
        self.assertEqual(document["sources"]["advice"], {"status": "unavailable", "message": "advice is unavailable"})

    def test_a_rule_the_engine_shadows_is_its_own_row_and_borrows_nothing(self):
        """Through the real classify: `a.md` names itself `b` in front matter, after `b.md`."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Path(os.path.realpath(tmp.name))
        rules_dir = repo / "primitives" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "b.md").write_text("# B\n\nDo b.\n", encoding="utf-8")
        (rules_dir / "a.md").write_text("---\nrule: b\n---\n# A\n\nDo a.\n", encoding="utf-8")
        engine = rule_health.selection._harness_module(REPO).load_detectors()
        classified = rule_coverage.classify([("b", rules_dir / "b.md"), ("a", rules_dir / "a.md")],
                                            [], {}, {}, engine.read_rule_file)
        self.assertTrue(rule_health._shadowed(classified[0][1]))
        _hits, _precision, cost = engine_documents()
        cost["rows"] += [{"module": "rules/b", "state": "on", "tokens": {"tokens": 50, "estimand": "soft estimate"},
                          "effect": "unmeasured"},
                         {"module": "rules/a", "state": "on", "tokens": {"tokens": 60, "estimand": "soft estimate"},
                          "effect": "unmeasured"}]
        with mock.patch.object(rule_health, "_classified", return_value=classified), \
                mock.patch.object(rule_health, "_advice_modules", return_value={}):
            document = rule_health.report(REPO, cwd=repo, run=fake_runner(cost=cost))
        rows = document["rows"]
        self.assertEqual([row["id"] for row in rows], ["primitives/rules/b.md", "primitives/rules/a.md"])
        self.assertEqual([row["module"] for row in rows], ["rules/b", "rules/a"])
        self.assertEqual(rows[0]["tokens"]["tokens"], 50)
        self.assertTrue(rows[0]["try_without"]["available"])
        self.assertEqual(rows[1]["rule"], "b")
        self.assertEqual(rows[1]["tokens"]["status"], "not measured")
        self.assertIn("shadowed: its name's detectors measure", rows[1]["tokens"]["reason"])
        self.assertFalse(rows[1]["try_without"]["available"])

    def test_the_report_names_the_directory_it_read_from(self):
        self.assertEqual(fixture_report()["working_directory"], "/repo")
        with mock.patch.object(rule_health, "_classified", return_value=fake_rules()):
            document = rule_health.report(REPO, cwd=Path("/srv/someone/project"), run=fake_runner(),
                                          home="/srv/someone")
        self.assertEqual(document["working_directory"], "~/project")

    def test_a_window_with_no_measured_sessions_is_not_measured_never_zero(self):
        hits, _precision, _cost = engine_documents()
        hits["7"] = hits_document(7, 0, [])
        row = by_rule(fixture_report(hits=hits))["voice-and-format"]
        self.assertEqual(row["hits"]["windows"]["7"],
                         {"status": "not measured", "measured_sessions": 0, "detectors": {},
                          "reason": "no measured sessions in the last 7 day(s)"})
        self.assertEqual(row["hits"]["last_fired"], {"state": "fired", "days": 30})

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


class CorpusExitCodes(unittest.TestCase):
    """The corpus script exits 1 with a full result when a detector fails; that is not a failed read."""

    def runner_with(self, body):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "scripts").mkdir()
        (root / "scripts" / "detector_corpus.py").write_text("import sys\n" + body, encoding="utf-8")
        return rule_health.command_runner(root)

    def test_exit_one_with_json_is_a_result_carrying_its_failures(self):
        document = {"floor": 0.9, "below_floor": ["a/b"], "unscored": ["c/d"], "failed": ["a/b"], "stale": [],
                    "detectors": {}}
        run = self.runner_with("print(%r)\nsys.exit(1)\n" % json.dumps(document))
        self.assertEqual(run(["scripts/detector_corpus.py", "--json"]), document)

    def test_an_uncaught_exception_exits_one_and_is_a_failed_read_with_its_error(self):
        run = self.runner_with("raise KeyError('x')\n")
        with self.assertRaises(OSError) as caught:
            run(["scripts/detector_corpus.py", "--json"])
        self.assertEqual(str(caught.exception), "scripts/detector_corpus.py --json did not print JSON: KeyError: 'x'")

    def test_exit_one_with_json_that_is_not_the_scripts_document_is_a_failed_read(self):
        run = self.runner_with("print('{}')\nraise RuntimeError('half way')\n")
        with self.assertRaises(OSError) as caught:
            run(["scripts/detector_corpus.py", "--json"])
        self.assertEqual(str(caught.exception), "scripts/detector_corpus.py --json exited 1 without its "
                                                "result document: RuntimeError: half way")

    def test_another_exit_code_is_a_failed_read_with_its_last_error_line(self):
        run = self.runner_with("sys.stderr.write('Traceback\\nKeyError: x\\n')\nsys.exit(2)\n")
        with self.assertRaises(OSError) as caught:
            run(["scripts/detector_corpus.py", "--json"])
        self.assertEqual(str(caught.exception), "scripts/detector_corpus.py --json exited 2: KeyError: x")

    def test_the_real_script_reports_below_floor_and_the_studio_reads_that_field(self):
        """Real `detector_corpus.py` output: at an unreachable floor every scored detector is below
        it, the script exits 1, and the Studio reads the result and the engine's verdict."""
        run = rule_health.command_runner(REPO)
        document = run(["scripts/detector_corpus.py", "--json", "--floor", "1.01"])
        self.assertLessEqual(rule_health.CORPUS_KEYS, set(document))
        scored = sorted(did for did, score in document["detectors"].items() if score.get("scored"))
        self.assertTrue(scored)
        self.assertEqual(sorted(document["below_floor"]), scored)
        verdict = rule_health._detector_precision(document, scored[0])
        self.assertTrue(verdict["below_floor"])
        self.assertIn("below the engine's 1.01 floor", rule_health._reliability([verdict], document["floor"])["reason"])
        clean = run(["scripts/detector_corpus.py", "--json"])
        self.assertIn("below_floor", clean)
        self.assertEqual(rule_health._detector_precision(clean, scored[0])["below_floor"],
                         scored[0] in clean["below_floor"])

    def test_floor_and_scores_are_rounded_and_a_missing_floor_is_handled(self):
        detector = {"id": "x/y", "status": "measured", "precision": 0.9444444444, "recall": None,
                    "below_floor": True, "reason": ""}
        self.assertEqual(rule_health._reliability([detector], None)["reason"],
                         "x/y is below the engine's floor (precision 0.94, recall unknown)")
        self.assertEqual(rule_health._reliability([detector], 0.9)["reason"],
                         "x/y is below the engine's 0.90 floor (precision 0.94, recall unknown)")

    def test_output_that_is_not_json_is_a_failed_read(self):
        run = self.runner_with("print('table, not json')\nsys.exit(1)\n")
        with self.assertRaises(OSError) as caught:
            run(["scripts/detector_corpus.py", "--json"])
        self.assertIn("did not print JSON", str(caught.exception))


class TryWithoutKeptDraft(unittest.TestCase):
    """Once the draft exists, every answer names it, whatever fails after."""

    def setUp(self):
        patcher = mock.patch.object(rule_health, "_selected_rules", return_value={"secrets": "on"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_switch_that_cannot_be_saved_returns_the_kept_drafts_name(self):
        with mock.patch.object(rule_health.drafts, "find", return_value=(Path("/w"), {})), \
                mock.patch.object(rule_health.drafts, "describe", return_value={"revision": "r" * 40}), \
                mock.patch.object(rule_health.selection_editing, "save",
                                  return_value={"saved": False, "error": "lint refused the change"}):
            result = rule_health.try_without(REPO, "secrets", lambda root, name: "", suffix="abc")
        self.assertEqual(result["status"], "switch-failed")
        self.assertEqual(result["draft"], {"name": "without-secrets-abc", "revision": "r" * 40})
        self.assertEqual(result["warning"], "lint refused the change")
        self.assertIn("without-secrets-abc was created", result["message"])
        self.assertIn("citizen draft discard without-secrets-abc", result["message"])
        server.RULE_TRY_WITHOUT.validate(result)

    def test_a_draft_that_cannot_be_read_after_create_is_reported_created_with_its_name(self):
        with mock.patch.object(rule_health.drafts, "find",
                               side_effect=rule_health.drafts.DraftError("busy", "locked")):
            result = rule_health.try_without(REPO, "secrets", lambda root, name: "", suffix="abc")
        self.assertEqual(result["status"], "draft-unreadable")
        self.assertEqual(result["draft"]["name"], "without-secrets-abc")
        self.assertIn("was created", result["message"])
        server.RULE_TRY_WITHOUT.validate(result)

    def test_a_unit_holding_a_placeholder_word_never_rewrites_the_draft_name(self):
        with mock.patch.object(rule_health, "_selected_rules", return_value={"API-KEY-hygiene": "on"}), \
                mock.patch.object(rule_health.drafts, "find", return_value=(Path("/w"), {})), \
                mock.patch.object(rule_health.drafts, "describe", return_value={"revision": "r" * 40}), \
                mock.patch.object(rule_health.selection_editing, "save",
                                  return_value={"saved": True, "result": {"revision": "s" * 40}}):
            result = rule_health.try_without(REPO, "API-KEY-hygiene", lambda root, name: "", suffix="k")
        name = "without-API-KEY-hygiene-k"
        self.assertEqual(result["commands"][0], "citizen draft create %s --json" % name)
        words = result["commands"][2].split()
        self.assertEqual(words[4], name)
        self.assertEqual(words[6], "r" * 40)
        self.assertRegex(words[8], r"^[0-9a-f]{32}$")

    def test_the_server_create_clears_only_a_failed_partial_create(self):
        with mock.patch.object(server, "_run_draft_create", return_value="create-timeout"), \
                mock.patch.object(server.first_run, "clear_partial") as clear:
            self.assertEqual(server._create_rule_draft(REPO, "without-x-1"), "create-timeout")
        clear.assert_called_once_with(REPO, "without-x-1")
        with mock.patch.object(server, "_run_draft_create", return_value=""), \
                mock.patch.object(server.first_run, "clear_partial") as clear:
            self.assertEqual(server._create_rule_draft(REPO, "without-x-2"), "")
        clear.assert_not_called()


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
        self.assertEqual(result["status"], "switched")
        self.assertEqual(result["commands"][0], "citizen draft create %s --json" % name)
        self.assertEqual(result["commands"][1], "printf '%s\\n' '{\"rules.conciseness\": \"off\"}' > changes.json")
        self.assertRegex(result["commands"][2], r"^citizen draft selection save %s --base-revision [0-9a-f]{40} "
                                                r"--idempotency-key [0-9a-f]{32} --changes changes.json --json$" % name)
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

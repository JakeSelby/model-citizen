# SPDX-License-Identifier: MIT
"""Trends show each measure by version and date as the run store holds it (AH-S309, #992).

Every figure, interval and label is the row's own; the proof set is what `citizen evidence verify
--json` reports. The committed fixture `studio/tests/fixtures/trends.json`, which
`studio/tests/trends.test.ts` feeds through the page, is `report()` over the rows below.
Regenerate it with:

    python3 tests/test_studio_trends.py --write
"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_harness import REPO  # noqa: E402
from harness_core.studio import run_store, trends  # noqa: E402

FIXTURE = REPO / "studio" / "tests" / "fixtures" / "trends.json"
GENERATED_AT = "2026-10-04T00:00:00Z"


def summary(passed, cost):
    return {"runs": 10, "errors": 0, "passed": passed, "cost_per_passed": cost}


def history_row(date, version, ratio, normalised, **extra):
    """A row shaped like `cost_bench.history_row`'s, with SM-2's stored intervals."""
    row = {
        "date": date, "series": "replay-v2", "bucket": "", "predicted_ratio": None,
        "harness_version": version, "harness_sha": (version.replace(".", "") * 40)[:40],
        "tag": "v" + version, "model": "claude-haiku-4-5", "cli_version": "2.1.0", "reps": 5,
        "runs": 40, "change_note": "release " + version, "per_task": {},
        "bare": summary(4.0, 0.5), "harness": summary(4.2, 0.45), "ratio": ratio,
        "ratio_cache_normalised": normalised, "cache_miss": {"bare": 0.1, "harness": 0.2},
        "threshold": 0.85, "status": "failed", "arms": {},
        "sm2": {"method": "SM-2", "confidence": 0.95, "tasks": 4, "ratio": ratio - 0.01,
                "ratio_interval": [round(ratio - 0.11, 4), round(ratio + 0.09, 4)],
                "difference": 0.05, "difference_interval": [-0.1, 0.2],
                "arms": {"bare": {"pass_rate": 0.8, "pass_rate_interval_descriptive": [0.6, 0.9]},
                         "harness": {"pass_rate": 0.85, "pass_rate_interval_descriptive": [0.65, 0.95]}},
                "sm2_eligible": True, "limitation": None, "verdict": "inconclusive",
                "reason": "the ratio interval spans 1"},
        "delegation": {"label": "adherence, descriptive, not causal", "registered": False,
                       "verdicts": {"fired": 2, "declined-below-break-even": 1,
                                    "missed-above-break-even": 0, "not-offered": 0, "unknown": 1}},
    }
    row.update(extra)
    return row


def fixture_rows():
    registered = history_row("2026-09-20", "0.13.0", 1.052, 0.98)
    registered["delegation"]["registered"] = True
    exploratory = history_row("2026-09-27", "0.14.0", 0.97, 0.93,
                              change_note="cold cache per trial",
                              # Fields a later engine adds are read past, never refused.
                              metrics={"task": {"lines": 3}}, cache_basis="cold")
    labelled = history_row("2026-10-01", "0.15.0", 0.91, 0.9, evidence="pre-registered",
                           pre_registration="docs/plans/registered.md")
    no_sm2 = history_row("2026-09-21", "0.13.0", 0.8, 0.79, series="micro-v1")
    no_sm2["sm2"] = {"unavailable": "the rows saved no pass or fail"}
    return [registered, exploratory, labelled, no_sm2]


STATIC = {"schema_version": 2, "harness_version": "0.15.0",
          "total": {"files": 12, "lines": 300, "chars": 12000, "est_tokens": 3000},
          "usd": {"m": {"session_start": 0.01, "later_turn": 0.001}}, "files": {}}


def fake_verify(result):
    def verify(path):
        verify.calls.append(path)
        return copy.deepcopy(result)
    verify.calls = []
    return verify


VERIFIED = {"ok": True, "bundle_id": "proof-set-1", "errors": [],
            "unknown": ["reasoning effort for the bare arm"],
            "checks": {str(n): True for n in range(1, 13)},
            "cards": [{"id": "ratio", "claim": "Observed cost ratio", "estimand": "intention-to-treat",
                       "figure": {"pointer": "/sm2/ratio", "value": 0.82},
                       "interval": {"pointer": "/sm2/ratio_interval", "value": [0.7, 0.95]},
                       "bundle": "proof-set-1", "verify_status": True},
                      {"id": "passes", "claim": "Pass rate held", "estimand": "intention-to-treat",
                       "figure": {"pointer": "/x", "value": 0.0},
                       "interval": {"pointer": "/y", "value": [-0.1, 0.1]},
                       "bundle": "proof-set-1", "verify_status": False}],
            "derived": {"large": "never forwarded"}}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name).resolve()
        self.repository = root / "repo"
        self.repository.mkdir()
        (self.repository / "product.json").write_text(json.dumps({"headline": "x"}))
        state = root / "state"
        state.mkdir(mode=0o700)
        self.store = run_store.RunStore(state)
        for number, row in enumerate(fixture_rows(), 1):
            self.store.upsert(run_store._benchmark_history("benchmarks/history.jsonl", number, row))
        self.store.upsert(run_store._benchmark_static("benchmarks/static.json", STATIC))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def bind(self, cards):
        (self.repository / "product.json").write_text(json.dumps({"evidence_cards": cards}))

    def report(self, verify=None):
        return trends.report(self.repository, trends.collect(self.store), verify or fake_verify(VERIFIED))


def by_line(document):
    return {line["id"]: line for line in document["lines"]}


class TrendLineTests(Fixture):
    def test_each_series_plots_by_version_and_date_with_its_change_notes(self):
        document = self.report()
        lines = by_line(document)
        self.assertEqual(sorted(lines), ["micro-v1", "replay-v2"])
        points = lines["replay-v2"]["points"]
        self.assertEqual([(p["date"], p["harness_version"]) for p in points],
                         [("2026-09-20", "0.13.0"), ("2026-09-27", "0.14.0"), ("2026-10-01", "0.15.0")])
        self.assertEqual([p["change_note"] for p in points],
                         ["release 0.13.0", "cold cache per trial", "release 0.15.0"])
        self.assertEqual([p["measures"]["ratio"]["value"] for p in points], [1.052, 0.97, 0.91])
        self.assertEqual([p["measures"]["ratio_cache_normalised"]["value"] for p in points], [0.98, 0.93, 0.9])
        self.assertEqual(points[0]["measures"]["pass_rate_harness"]["value"], 0.85)

    def test_ratios_only_no_dollar_figure_leaves_the_route(self):
        document = self.report()
        self.assertIn("never dollars", document["ratio_note"])
        text = json.dumps(document["lines"])
        for key in ("cost_per_passed", "cost_usd", "usd"):
            self.assertNotIn('"%s"' % key, text)
        self.assertEqual({m["unit"] for m in document["measures"]}, {"ratio", "share", "points"})

    def test_a_stored_interval_is_carried_verbatim_and_none_is_ever_made(self):
        points = by_line(self.report())["replay-v2"]["points"]
        sm2 = points[0]["measures"]["ratio_sm2"]
        self.assertEqual((sm2["value"], sm2["interval"], sm2["interval_kind"]),
                         (1.042, [0.942, 1.142], "SM-2"))
        self.assertEqual(points[0]["measures"]["pass_rate_harness"]["interval"], [0.65, 0.95])
        self.assertEqual(points[0]["measures"]["pass_rate_harness"]["interval_kind"], "descriptive")
        self.assertEqual(points[0]["measures"]["pass_rate_difference"]["interval"], [-0.1, 0.2])
        # The mean-of-reps ratio has no interval in the row, so none is drawn.
        self.assertIsNone(points[0]["measures"]["ratio"]["interval"])
        micro = by_line(self.report())["micro-v1"]["points"][0]
        self.assertIsNone(micro["measures"]["ratio_sm2"]["value"])
        self.assertIsNone(micro["measures"]["ratio_sm2"]["interval"])
        self.assertEqual(micro["sm2"]["unavailable"], "the rows saved no pass or fail")

    def test_sm2_verdict_and_reason_are_the_rows_own(self):
        point = by_line(self.report())["replay-v2"]["points"][0]
        self.assertEqual((point["sm2"]["verdict"], point["sm2"]["reason"]),
                         ("inconclusive", "the ratio interval spans 1"))
        self.assertEqual(point["delegation"]["verdicts"]["fired"], 2)

    def test_each_point_is_labelled_and_an_exploratory_line_says_so(self):
        lines = by_line(self.report())
        labels = [p["evidence"]["label"] for p in lines["replay-v2"]["points"]]
        self.assertEqual(labels, ["pre-registered", "exploratory", "pre-registered"])
        self.assertEqual(lines["replay-v2"]["points"][2]["evidence"]["pre_registration"],
                         "docs/plans/registered.md")
        self.assertEqual(lines["replay-v2"]["evidence"], "mixed")
        self.assertIn("exploratory points cannot be cited", lines["replay-v2"]["evidence_note"])
        self.assertEqual(lines["micro-v1"]["evidence"], "exploratory")
        self.assertIn("exploratory", lines["micro-v1"]["evidence_note"])

    def test_an_unlabelled_row_reads_exploratory(self):
        self.assertEqual(trends.evidence_label({})["label"], "exploratory")
        self.assertEqual(trends.evidence_label({"delegation": {"registered": "yes"}})["label"], "exploratory")
        self.assertEqual(trends.evidence_label({"evidence": "maybe"})["label"], "exploratory")

    def test_new_row_fields_are_read_past(self):
        point = by_line(self.report())["replay-v2"]["points"][1]
        self.assertEqual(point["cache_basis"], "cold")
        self.assertNotIn("metrics", point)
        odd = trends.history_point({"raw": {"date": 3, "sm2": [], "delegation": "x", "metrics": {"a": 1},
                                            "ratio": float("nan"), "reps": True}})
        self.assertIsNone(odd["date"])
        self.assertIsNone(odd["measures"]["ratio"]["value"])
        self.assertIsNone(odd["delegation"])
        self.assertIsNone(trends.history_point({"raw": None}))

    def test_static_context_is_by_version(self):
        static = self.report()["static"]
        self.assertEqual(static["points"][0]["harness_version"], "0.15.0")
        self.assertEqual(static["points"][0]["value"], 3000)

    def test_measures_with_no_history_say_so_rather_than_show_a_line(self):
        names = [item["measure"] for item in self.report()["not_tracked"]]
        self.assertEqual(names, ["Detector precision", "Rule adherence"])

    def test_reading_is_paged_and_cut_at_its_bound(self):
        original = trends.PAGE, trends.MAX_RECORDS
        try:
            trends.PAGE, trends.MAX_RECORDS = 1, 2
            collected = trends.collect(self.store)
        finally:
            trends.PAGE, trends.MAX_RECORDS = original
        self.assertEqual(len(collected["history"]), 2)
        self.assertTrue(collected["truncated"])
        self.assertFalse(trends.collect(self.store)["truncated"])


class ProofSetTests(Fixture):
    def test_with_no_bound_bundle_the_project_claims_nothing(self):
        verify = fake_verify(VERIFIED)
        proof = self.report(verify)["proof"]
        self.assertEqual(proof["statement"], trends.NO_CLAIM)
        self.assertEqual((proof["bundles"], proof["claims"]), ([], []))
        self.assertEqual(verify.calls, [])

    def test_a_bound_bundle_shows_its_verify_status_and_cards_as_the_verifier_reports(self):
        self.bind([{"field": "/hero/line", "text": "Observed cost ratio 0.82", "bundle": "proof/one",
                    "card": "ratio"}])
        verify = fake_verify(VERIFIED)
        proof = self.report(verify)["proof"]
        self.assertEqual(verify.calls, [self.repository / "proof/one"])
        self.assertIsNone(proof["statement"])
        bundle = proof["bundles"][0]
        self.assertEqual((bundle["status"], bundle["bundle_id"]), ("verified", "proof-set-1"))
        self.assertEqual(bundle["unknown"], ["reasoning effort for the bare arm"])
        self.assertEqual(bundle["command"], "citizen evidence verify --json proof/one")
        self.assertNotIn("derived", bundle)
        cards = {card["id"]: card for card in bundle["cards"]}
        self.assertEqual((cards["ratio"]["figure"], cards["ratio"]["interval"]), (0.82, [0.7, 0.95]))
        self.assertTrue(cards["ratio"]["verify_status"])
        self.assertEqual(cards["ratio"]["published"], {"field": "/hero/line", "text": "Observed cost ratio 0.82"})
        self.assertFalse(cards["passes"]["verify_status"])
        self.assertIsNone(cards["passes"]["published"])
        self.assertEqual(proof["claims"], [{"field": "/hero/line", "text": "Observed cost ratio 0.82",
                                            "bundle": "proof/one", "card": "ratio", "verify_status": True}])

    def test_a_failed_or_crashing_verifier_never_reads_verified(self):
        self.bind([{"field": "/a", "text": "t", "bundle": "proof/one", "card": "ratio"}])
        failed = dict(VERIFIED, ok=False, errors=["item 3: plan is missing"])
        proof = self.report(fake_verify(failed))["proof"]
        self.assertEqual(proof["bundles"][0]["status"], "failed")
        self.assertEqual(proof["bundles"][0]["errors"], ["item 3: plan is missing"])
        self.assertFalse(proof["claims"][0]["verify_status"])

        def boom(_path):
            raise OSError("unreadable")
        proof = self.report(boom)["proof"]
        self.assertEqual(proof["bundles"][0]["status"], "failed")
        self.assertIn("unreadable", proof["bundles"][0]["errors"][0])
        self.assertFalse(proof["claims"][0]["verify_status"])

    def test_the_status_equals_what_citizen_evidence_verify_json_prints(self):
        """The real verifier and the real CLI, on a bundle that cannot verify."""
        (self.repository / "proof" / "one").mkdir(parents=True)
        self.bind([{"field": "/a", "text": "t", "bundle": "proof/one", "card": "ratio"}])
        bundle = trends.report(self.repository, trends.collect(self.store))["proof"]["bundles"][0]
        environment = {k: v for k, v in os.environ.items() if k != "HARNESS_QUIET"}
        completed = subprocess.run(
            [sys.executable, str(REPO / "bin" / "harness"), "evidence", "verify", "--json",
             str(self.repository / "proof" / "one")],
            capture_output=True, text=True, env=environment, timeout=120)
        self.assertEqual(completed.returncode, 1, completed.stderr)
        printed = json.loads(completed.stdout)
        self.assertFalse(printed["ok"])
        self.assertEqual(bundle["status"], "failed")
        self.assertEqual(bundle["errors"], printed["errors"])
        self.assertEqual(bundle["bundle_id"], printed["bundle_id"])


def fixture_report():
    """`report()` over the fixed rows and a verified bundle, with a fixed generation time."""
    case = Fixture("run")
    case.setUp()
    try:
        case.bind([{"field": "/hero/line", "text": "Observed cost ratio 0.82", "bundle": "proof/one",
                    "card": "ratio"}])
        document = case.report(fake_verify(VERIFIED))
    finally:
        case.tearDown()
    document["generated_at"] = GENERATED_AT
    return document


class CommittedFixtureTests(unittest.TestCase):
    def test_the_page_fixture_is_the_current_report(self):
        current = json.loads(json.dumps(fixture_report()))
        for line in current["lines"]:
            for point in line["points"]:
                point["run_id"] = None
        committed = json.loads(FIXTURE.read_text(encoding="utf-8"))
        for line in committed["lines"]:
            for point in line["points"]:
                point["run_id"] = None
        current["static"]["points"][0]["run_id"] = committed["static"]["points"][0]["run_id"] = None
        self.assertEqual(committed, current, "regenerate with python3 tests/test_studio_trends.py --write")


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        FIXTURE.write_text(json.dumps(fixture_report(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("wrote " + str(FIXTURE))
    else:
        unittest.main()

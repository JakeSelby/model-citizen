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
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_harness import REPO  # noqa: E402
import test_evidence_bundle as bundle_tests  # noqa: E402  a module import keeps its tests out
from harness_core.studio import run_store, runs, trends  # noqa: E402

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
                       "break_even": 7.6, "break_even_source": "FR-34, hypothetical", "min_runs": 4,
                       "fired_share": 0.75, "absorbable": ["Read", "Grep"], "tasks": {},
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
    exploratory["sm2"].update(ratio=None, ratio_interval=None, ratio_undefined="bare passed nothing",
                              verdict="supported", reason="both conditions of the decision rule hold",
                              claim="the harness costs less per pass")
    labelled = history_row("2026-10-01", "0.15.0", 0.91, 0.9, evidence="pre-registered",
                           pre_registration="docs/plans/registered.md")
    labelled["delegation"]["registered"] = True
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
        points = by_line(self.report())["replay-v2"]["points"]
        self.assertEqual((points[0]["sm2"]["verdict"], points[0]["sm2"]["reason"]),
                         ("inconclusive", "the ratio interval spans 1"))
        self.assertEqual(points[0]["sm2"]["text"], "SM-2 eligibility: eligible. verdict: inconclusive, "
                         "because the ratio interval spans 1")
        self.assertEqual(points[0]["delegation"]["verdicts"]["fired"], 2)

    def test_an_exploratory_verdict_carries_the_engines_own_qualifier(self):
        point = by_line(self.report())["replay-v2"]["points"][1]
        self.assertEqual(point["evidence"]["label"], "exploratory")
        self.assertEqual(point["sm2"]["text"], "SM-2 eligibility: eligible. verdict: supported "
                         "(exploratory, not from a registered run), because both conditions of the "
                         "decision rule hold; claim: the harness costs less per pass")
        self.assertIn("exploratory, not from a registered run", point["delegation"]["heading"])
        registered = by_line(self.report())["replay-v2"]["points"][0]
        self.assertIn("(adherence, descriptive, not causal; registered)", registered["delegation"]["heading"])

    def test_the_delegation_heading_and_the_label_read_the_same_verdict(self):
        row = history_row("2026-10-02", "0.16.0", 0.9, 0.9, evidence="pre-registered")
        row["delegation"]["registered"] = True  # the block says registered, the label fails the rule
        point = trends.history_point({"raw": row})
        self.assertEqual(point["evidence"]["label"], "exploratory")
        self.assertIn("exploratory, not from a registered run", point["delegation"]["heading"])
        self.assertIn("(exploratory, not from a registered run)", point["sm2"]["text"])

    def test_an_eligibility_the_row_did_not_store_is_said_so(self):
        row = history_row("2026-10-02", "0.16.0", 0.9, 0.9)
        del row["sm2"]["sm2_eligible"]
        self.assertTrue(trends.history_point({"raw": row})["sm2"]["text"].startswith(
            "SM-2 eligibility: not stored in the row."))

    def test_an_undefined_ratio_shows_sm2s_stored_reason(self):
        figure = by_line(self.report())["replay-v2"]["points"][1]["measures"]["ratio_sm2"]
        self.assertEqual((figure["value"], figure["interval"], figure["undefined"]),
                         (None, None, "bare passed nothing"))

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

    def test_pre_registered_needs_the_engines_rule_on_every_record(self):
        label = trends.evidence_label
        named = {"evidence": "pre-registered", "pre_registration": "docs/plan.md"}
        self.assertEqual(label(named)["label"], "pre-registered")
        # The engine's rule needs the pre-registration named as well as the label.
        self.assertEqual(label({"evidence": "pre-registered"})["label"], "exploratory")
        # The delegation block's stored reading is never overridden by the label.
        conflict = dict(named, delegation={"registered": False})
        self.assertEqual(label(conflict)["label"], "exploratory")
        self.assertIn("the delegation block", label(conflict)["reason"])
        self.assertEqual(label(dict(named, delegation={"registered": True}))["label"], "pre-registered")
        self.assertEqual(label({"delegation": {"registered": True}})["label"], "pre-registered")

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

    def test_versions_sort_as_releases_not_as_text(self):
        self.assertLess(trends.version_key("v0.9.0"), trends.version_key("0.10.0"))
        self.assertEqual(sorted(["0.10.0", "0.9.1", "0.9.0"], key=trends.version_key), ["0.9.0", "0.9.1", "0.10.0"])
        points = [{"date": "2026-01-01", "harness_version": v, "harness_sha": "", "series": "s",
                   "bucket": "", "evidence": {"label": "exploratory"}} for v in ("0.10.0", "0.9.0")]
        self.assertEqual([p["harness_version"] for p in trends.lines(points)[0]["points"]],
                         ["0.9.0", "0.10.0"])

    def git(self, *args):
        environment = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t",
                           GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t")
        subprocess.run(["git", "-C", str(self.repository)] + list(args), check=True,
                       capture_output=True, env=environment)

    def test_every_committed_static_version_survives_a_reindex_and_a_rebuild(self):
        """The index holds each version's figure from the repository's history, in release order."""
        root = Path(self.tmp.name).resolve()
        (self.repository / "benchmarks").mkdir()
        self.git("init", "-q")
        static = self.repository / "benchmarks" / "static.json"
        for version, tokens in (("0.9.0", 2000), ("0.10.0", 2500)):
            value = dict(STATIC, harness_version=version, total=dict(STATIC["total"], est_tokens=tokens))
            static.write_text(json.dumps(value))
            self.git("add", "-A")
            self.git("commit", "-qm", version)
        # An older schema in history is skipped with its reason, never a crash.
        static.write_text(json.dumps({"schema_version": 1, "harness_version": "0.8.0"}))
        self.git("commit", "-qam", "old schema")
        static.write_text(json.dumps(dict(STATIC, harness_version="0.11.0")))
        catalog = root / "suites.json"
        catalog.write_text(json.dumps({"schema_version": 1, "suites": []}))
        supervisor = runs.RunSupervisor(root / "supervisor", catalog)
        self.addCleanup(supervisor.close)
        report = supervisor.reindex(self.repository)
        self.assertEqual([item["path"][:len("benchmarks/static.json@")] for item in report["skipped"]],
                         ["benchmarks/static.json@"])
        expected = [("0.9.0", 2000), ("0.10.0", 2500), ("0.11.0", 3000)]
        document = trends.report(self.repository, trends.collect(supervisor.history), fake_verify(VERIFIED))
        self.assertEqual([(p["harness_version"], p["value"]) for p in document["static"]["points"]], expected)
        supervisor.history.close()
        (root / "supervisor" / run_store.DATABASE_NAME).unlink()
        supervisor.history = run_store.RunStore(root / "supervisor")
        supervisor.reindex(self.repository)
        supervisor.reindex(self.repository)
        document = trends.report(self.repository, trends.collect(supervisor.history), fake_verify(VERIFIED))
        self.assertEqual([(p["harness_version"], p["value"]) for p in document["static"]["points"]], expected)

    def test_a_directory_that_is_not_its_own_repository_reads_no_history(self):
        self.assertEqual(run_store.static_revisions(self.repository), [])

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
        self.assertEqual(len(collected["history"]["records"]), 2)
        self.assertTrue(collected["history"]["truncated"])
        self.assertFalse(trends.collect(self.store)["history"]["truncated"])
        self.assertEqual(self.report()["sections"]["history"],
                         {"status": "ready", "reason": None, "unreadable": 0, "truncated": False})

    def test_an_unreadable_record_is_counted_and_the_rest_still_show(self):
        real = self.store.get
        bad = trends.collect(self.store)["history"]["records"][0]["run_id"]

        def get(run_id):
            if run_id == bad:
                raise run_store.RunStoreError("indexed run record is corrupt")
            return real(run_id)
        self.store.get = get
        document = self.report()
        self.assertEqual(document["sections"]["history"]["unreadable"], 1)
        self.assertEqual(sum(len(line["points"]) for line in document["lines"]), 3)

    def test_a_paging_failure_draws_no_line_from_the_records_read_before_it(self):
        real = self.store.history
        calls = []

        def history(**filters):
            calls.append(filters)
            if len(calls) > 1:
                raise run_store.RunStoreError("run history read failed")
            return real(**filters)
        self.store.history = history
        original = trends.PAGE
        try:
            trends.PAGE = 1
            document = self.report()
        finally:
            trends.PAGE = original
        self.assertEqual(document["sections"]["history"]["status"], "unavailable")
        self.assertEqual(document["lines"], [])

    def test_an_engine_that_will_not_load_leaves_static_and_proof_standing(self):
        self.bind([{"field": "/a", "text": "t", "bundle": "proof/one", "card": "ratio"}])

        def broken():
            raise trends.EngineUnavailable("scripts/delegation_verdict.py could not be loaded: boom")
        original = trends._delegation_engine
        trends._delegation_engine = broken
        try:
            document = self.report()
        finally:
            trends._delegation_engine = original
        self.assertEqual(document["sections"]["history"]["status"], "unavailable")
        self.assertIn("could not be loaded: boom", document["sections"]["history"]["reason"])
        self.assertEqual(document["lines"], [])
        self.assertEqual(document["sections"]["static"]["status"], "ready")
        self.assertEqual(document["static"]["points"][0]["harness_version"], "0.15.0")
        self.assertEqual(document["proof"]["claims"][0]["status"], "verified")

    def test_a_run_store_that_fails_leaves_the_proof_set_standing(self):
        def broken(**_filters):
            raise run_store.RunStoreError("run history read failed")
        self.store.history = broken
        self.bind([{"field": "/a", "text": "t", "bundle": "proof/one", "card": "ratio"}])
        document = self.report()
        self.assertEqual(document["sections"]["history"]["status"], "unavailable")
        self.assertIn("run history read failed", document["sections"]["static"]["reason"])
        self.assertEqual(document["lines"], [])
        self.assertEqual(document["proof"]["bundles"][0]["status"], "verified")
        whole = trends.report(self.repository, trends.unavailable("no index"), fake_verify(VERIFIED))
        self.assertEqual(whole["sections"]["history"]["reason"], "no index")
        self.assertEqual(whole["proof"]["claims"][0]["status"], "verified")


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
        self.assertTrue(cards["ratio"]["verified"])
        self.assertEqual(cards["ratio"]["published"], {"field": "/hero/line", "text": "Observed cost ratio 0.82"})
        self.assertFalse(cards["passes"]["verify_status"])
        self.assertIsNone(cards["passes"]["published"])
        self.assertEqual(proof["claims"], [{"field": "/hero/line", "text": "Observed cost ratio 0.82",
                                            "bundle": "proof/one", "card": "ratio", "status": "verified",
                                            "reason": None}])

    def test_a_failed_or_crashing_verifier_never_reads_verified(self):
        self.bind([{"field": "/a", "text": "t", "bundle": "proof/one", "card": "ratio"}])
        failed = dict(VERIFIED, ok=False, errors=["item 3: plan is missing"])
        proof = self.report(fake_verify(failed))["proof"]
        self.assertEqual(proof["bundles"][0]["status"], "failed")
        self.assertEqual(proof["bundles"][0]["errors"], ["item 3: plan is missing"])
        # The card's own status stays the verifier's, but it never reads verified in a failed bundle.
        card = proof["bundles"][0]["cards"][0]
        self.assertEqual((card["verify_status"], card["verified"]), (True, False))
        self.assertEqual((proof["claims"][0]["status"], proof["claims"][0]["reason"]),
                         ("failed", "its bundle failed verification"))

        def boom(_path):
            raise OSError("unreadable")
        proof = self.report(boom)["proof"]
        self.assertEqual(proof["bundles"][0]["status"], "failed")
        self.assertIn("unreadable", proof["bundles"][0]["errors"][0])
        self.assertEqual(proof["claims"][0]["status"], "failed")

    def test_a_claim_no_check_ran_on_reads_not_checked_never_failed(self):
        cards = [{"field": "/f%d" % n, "text": "t", "bundle": "proof/b%d" % n, "card": "ratio"}
                 for n in range(trends.MAX_BUNDLES + 1)]
        cards.append({"field": "/outside", "text": "t", "bundle": "../elsewhere", "card": "ratio"})
        cards.append({"field": "/slash", "text": "t", "bundle": "proof\\b", "card": "ratio"})
        cards.append({"field": "/root", "text": "t", "bundle": "/tmp/proof", "card": "ratio"})
        self.bind(cards)
        verify = fake_verify(VERIFIED)
        proof = self.report(verify)["proof"]
        self.assertEqual(len(verify.calls), trends.MAX_BUNDLES)
        last = proof["bundles"][-1]
        self.assertEqual((last["status"], last["reason"]), ("not checked", "past the page's limit of 16 bundles"))
        by_field = {claim["field"]: claim for claim in proof["claims"]}
        self.assertEqual(by_field["/f16"]["status"], "not checked")
        self.assertEqual(by_field["/outside"]["status"], "not checked")
        self.assertEqual(by_field["/outside"]["reason"], "the bundle path leaves the repository")
        self.assertEqual(by_field["/slash"]["reason"], "the bundle path contains a backslash")
        self.assertEqual(by_field["/root"]["reason"], "the bundle path is absolute")

    def test_no_bundle_starts_once_the_verification_budget_is_spent(self):
        self.bind([{"field": "/f%d" % n, "text": "t", "bundle": "proof/b%d" % n, "card": "ratio"} for n in range(3)])
        ticks = iter([0.0, 0.0, trends.VERIFY_BUDGET_SECONDS - 1, trends.VERIFY_BUDGET_SECONDS])
        verify = fake_verify(VERIFIED)
        proof = trends.proof_set(self.repository, verify, clock=lambda: next(ticks))
        self.assertEqual([b["status"] for b in proof["bundles"]], ["verified", "verified", "not checked"])
        self.assertIn("budget ran out", proof["bundles"][2]["reason"])
        self.assertEqual(len(verify.calls), 2)

    def cli(self, bundle):
        environment = {k: v for k, v in os.environ.items() if k != "HARNESS_QUIET"}
        completed = subprocess.run(
            [sys.executable, str(REPO / "bin" / "harness"), "evidence", "verify", "--json", str(bundle)],
            capture_output=True, text=True, env=environment, timeout=300)
        return completed.returncode, json.loads(completed.stdout)

    def assert_matches_cli(self, bundle, printed):
        """Every field the page shows, against what the CLI printed for the same bundle."""
        self.assertEqual(bundle["status"], "verified" if printed["ok"] is True else "failed")
        for name in ("bundle_id", "errors", "unknown", "checks"):
            self.assertEqual(bundle[name], printed[name], name)
        self.assertEqual(len(bundle["cards"]), len(printed["cards"]))
        for shown, card in zip(bundle["cards"], printed["cards"]):
            self.assertEqual((shown["id"], shown["claim"], shown["estimand"]),
                             (card["id"], card["claim"], card["estimand"]))
            self.assertEqual(shown["figure"], card["figure"]["value"])
            self.assertEqual(shown["interval"], card["interval"]["value"])
            self.assertIs(shown["verify_status"], card["verify_status"])
            self.assertIs(shown["verified"], card["verify_status"] and printed["ok"] is True)

    def real_bundle(self):
        fixture = bundle_tests.EvidenceBundleTest(
            methodName="test_valid_bundle_rederives_figures_cards_and_descriptive_statistics")
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        shutil.copytree(str(fixture.root), str(self.repository / "proof"), symlinks=True)
        fixture.root = self.repository / "proof"
        return fixture

    def test_a_real_verified_bundle_shows_every_field_as_the_cli_prints_it(self):
        self.real_bundle()
        printed_index = json.loads((self.repository / "proof" / "bundle.json").read_text())
        card_id = printed_index["evidence_cards"][0]["id"]
        self.bind([{"field": "/headline", "text": "x", "bundle": "proof", "card": card_id}])
        bundle = trends.report(self.repository, trends.collect(self.store))["proof"]["bundles"][0]
        code, printed = self.cli(self.repository / "proof")
        self.assertEqual(code, 0)
        self.assertIs(printed["ok"], True)
        self.assertTrue(bundle["cards"])
        self.assert_matches_cli(bundle, printed)
        claim = trends.proof_set(self.repository)["claims"][0]
        self.assertEqual(claim["status"], "verified")

    def test_a_real_contradicted_bundle_shows_every_field_as_the_cli_prints_it(self):
        fixture = self.real_bundle()
        fixture._mutate_rows(lambda value: value.update(model="another-model"))
        self.bind([{"field": "/headline", "text": "x", "bundle": "proof", "card": "card"}])
        bundle = trends.report(self.repository, trends.collect(self.store))["proof"]["bundles"][0]
        code, printed = self.cli(self.repository / "proof")
        self.assertEqual(code, 1)
        self.assertIs(printed["ok"], False)
        self.assert_matches_cli(bundle, printed)

    def test_an_unloadable_bundle_shows_the_cli_errors(self):
        (self.repository / "empty").mkdir()
        self.bind([{"field": "/a", "text": "t", "bundle": "empty", "card": "ratio"}])
        bundle = trends.report(self.repository, trends.collect(self.store))["proof"]["bundles"][0]
        code, printed = self.cli(self.repository / "empty")
        self.assertEqual(code, 1)
        self.assert_matches_cli(bundle, printed)


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

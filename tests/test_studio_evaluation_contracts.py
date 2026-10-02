"""Studio reads the landed Measured contracts as the engines write them, and derives none of them."""
import argparse
import contextlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from test_harness import REPO, harness
from harness_core.studio import evaluation, run_store, runs, server
from studio_target_support import FixtureTargetService

import test_evidence_bundle as bundle_tests  # a module import keeps its tests out of this module

sys.path.insert(0, str(REPO / "scripts"))
import ablations  # noqa: E402
import replay_pack  # noqa: E402
import replay_pair  # noqa: E402
import unit_economy  # noqa: E402

SHA = "a" * 40


def row(**fields):
    base = {"task": "alpha", "arm": "harness", "rep": 1, "harness_sha": SHA, "tag": "v1.0.0",
            "model": "model", "passed": True, "error": False, "cost_usd": 0.5,
            "evidence": "pre-registered", "pre_registration": "plan.md",
            "pre_registration_commit": "b" * 40}
    base.update(fields)
    return base


PAIR = {"name": "cost-pair", "sha256": "c" * 64, "factor": "HARNESS_STANCE_COST",
        "reference": "balanced", "treatment": "economy"}
ABLATION = {"name": "drop-rule", "sha256": "d" * 64,
            "arms": [{"id": "no-rule", "removes": "rules/conciseness"}]}
PACK = {"name": "evals", "version": "1.0.0", "commit": "e" * 40, "digest": "f" * 64}


def design_stamp():
    manifest = {"name": "unit-by-economy", "sha256": "9" * 64}
    spec = {"unit": "rule/conciseness", "instruments": ["detector"], "economy": ["cost"],
            "base_selection_sha256": "8" * 64}
    return unit_economy.stamp_of(manifest, spec)


class ContractParityTests(unittest.TestCase):
    """The reader's constants are the engines' constants, so a landed bump cannot pass silently."""

    def test_constants_equal_the_engines(self):
        self.assertEqual(evaluation.PAIR_SCHEMA, replay_pair.SCHEMA)
        self.assertEqual(evaluation.PAIR_ARMS, replay_pair.ARMS)
        self.assertEqual(evaluation.ABLATION_SCHEMA, ablations.SCHEMA)
        self.assertEqual(evaluation.ABLATION_CONTROL, ablations.CONTROL)
        self.assertEqual(evaluation.DESIGN_NAME, unit_economy.DESIGN)
        self.assertEqual(evaluation.DESIGN_SCHEMA, unit_economy.RESULT_SCHEMA)
        self.assertEqual(evaluation.DESIGN_CELLS, unit_economy.CELLS)
        self.assertEqual(tuple(sorted(replay_pack.identity(PACK))), tuple(sorted(evaluation.PACK_FIELDS)))

    def test_engine_stamps_are_read_as_their_shapes(self):
        pair = row(arm="treatment", **replay_pair.row_stamp(PAIR, "treatment"))
        self.assertEqual(evaluation.row_contract(pair)["shape"], "pair")
        for arm in ("bare", "harness", "no-rule"):
            stamped = row(arm=arm, **ablations.row_stamp(ABLATION, arm, 7))
            contract = evaluation.row_contract(stamped)
            self.assertEqual(contract["shape"], "variable-arm")
            self.assertEqual(contract["arms"], ["bare", "harness", "no-rule"])
        cell = row(arm="both", design=design_stamp())
        self.assertEqual(evaluation.row_contract(cell)["shape"], "four-cell")
        packed = row(**replay_pack.identity(PACK))
        contract = evaluation.row_contract(packed)
        self.assertEqual(contract["shape"], "two-arm")
        self.assertEqual(contract["pack"]["pack_digest"], "f" * 64)

    def test_unsupported_versions_are_named(self):
        cases = [
            (row(ablation={"name": "x", "sha256": "1" * 64, "schema": 3, "arms": ["a"]}),
             "unsupported ablation stamp schema: 3"),
            (row(arm="both", design=dict(design_stamp(), schema=2)),
             "unsupported design result schema: 2"),
            (row(arm="both", design=dict(design_stamp(), name="three-by-three")),
             'unsupported design: "three-by-three"'),
        ]
        for value, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(evaluation.ContractError, message):
                    evaluation.row_contract(value)

    def test_an_arm_outside_its_contract_and_a_partial_pack_are_refused(self):
        with self.assertRaisesRegex(evaluation.ContractError, "not an arm of its pair contract"):
            evaluation.row_contract(row(arm="harness", **replay_pair.row_stamp(PAIR, "treatment")))
        with self.assertRaisesRegex(evaluation.ContractError, "incomplete evaluator pack"):
            evaluation.row_contract(row(pack="evals", pack_digest="f" * 64))

    def test_an_unlabelled_row_is_never_promoted(self):
        bare = {key: value for key, value in row().items()
                if key not in ("evidence", "pre_registration", "pre_registration_commit")}
        self.assertEqual(evaluation.row_contract(bare)["registration"],
                         {"evidence": None, "pre_registration": None, "pre_registration_commit": None})


class RunStoreContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repository = self.root / "repository"
        (self.repository / "benchmarks" / "v1").mkdir(parents=True)
        self.catalog = self.root / "suites.json"
        self.catalog.write_text(json.dumps({"schema_version": 1, "suites": [{
            "id": "fixture", "version": 1, "argv": [sys.executable, "-c", "print('done')"],
            "parameters": {}, "cost_class": "free", "expected_duration_seconds": 1,
            "timeout_seconds": 10, "targets": ["installed"]}]}))
        self.supervisor = runs.RunSupervisor(self.root / "state", self.catalog,
                                             target_service=FixtureTargetService())

    def write(self, name, rows):
        path = self.repository / "benchmarks" / name / "results.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(value) + "\n" for value in rows), encoding="utf-8")
        return path.relative_to(self.repository).as_posix()

    def records(self):
        return self.supervisor.history.list(limit=200)

    def test_two_arm_rows_keep_the_identity_they_had_before_the_contracts(self):
        relative = self.write("v1", [row(), row(arm="bare", passed=False, cost_usd=None,
                                                 error="timeout")])
        report = self.supervisor.reindex(self.repository)
        self.assertEqual(report["skipped"], [])
        legacy = run_store._stable_id("benchmark-result", relative, run_store._digest(
            {"task": "alpha", "arm": "harness", "rep": 1, "harness_sha": SHA, "tag": "v1.0.0"}))
        ids = {record["run_id"] for record in self.records()}
        self.assertIn(legacy, ids)
        again = self.supervisor.reindex(self.repository)
        self.assertEqual(ids, {record["run_id"] for record in self.records()})
        self.assertEqual(again["total_runs"], 2)
        errored = next(r for r in self.records() if r["raw"]["arm"] == "bare")
        self.assertIsNone(errored["cost"])
        self.assertEqual(errored["raw"]["error"], "timeout")
        self.assertEqual(errored["status"], "failed")

    def test_pair_ablation_and_four_cell_rows_import_with_their_contract(self):
        self.write("pair", [row(arm=arm, **replay_pair.row_stamp(PAIR, arm))
                            for arm in replay_pair.ARMS])
        self.write("ablation", [row(arm=arm, **ablations.row_stamp(ABLATION, arm, 7))
                                for arm in ("bare", "harness", "no-rule")])
        self.write("grid", [row(arm=arm, design=design_stamp(), schedule_seed=7)
                            for arm in ("bare",) + unit_economy.CELLS])
        report = self.supervisor.reindex(self.repository)
        self.assertEqual(report["skipped"], [])
        shapes = sorted(r["evaluation"]["shape"] for r in self.records())
        self.assertEqual(shapes, ["four-cell"] * 5 + ["pair"] * 3 + ["variable-arm"] * 3)
        grid = next(r for r in self.records() if r["evaluation"]["shape"] == "four-cell")
        contract = self.supervisor.run_evaluation(grid["run_id"])
        self.assertEqual(contract["design"]["manifest_sha256"], "9" * 64)
        detail = self.supervisor.run_detail(grid["run_id"])
        server.RUN_DETAIL.validate(detail)
        self.assertEqual(detail["evaluation"], contract)

    def test_pack_rows_keep_their_ids_across_a_rebuild_and_carry_the_digest(self):
        first = self.write("pack-a", [row(**replay_pack.identity(PACK))])
        self.write("pack-b", [row(**replay_pack.identity(dict(PACK, digest="0" * 64)))])
        report = self.supervisor.reindex(self.repository)
        self.assertEqual(report["skipped"], [])
        legacy = run_store._stable_id("benchmark-result", first, run_store._digest(
            {"task": "alpha", "arm": "harness", "rep": 1, "harness_sha": SHA, "tag": "v1.0.0"}))
        before = {r["run_id"]: r["evaluation"]["pack"]["pack_digest"] for r in self.records()}
        self.assertEqual(before[legacy], "f" * 64)
        self.assertEqual(sorted(before.values()), ["0" * 64, "f" * 64])
        self.supervisor.reindex(self.repository)
        self.assertEqual(before, {r["run_id"]: r["evaluation"]["pack"]["pack_digest"]
                                  for r in self.records()})

    def test_the_manifest_digest_separates_ablation_cohorts(self):
        self.write("ablations", [row(arm="harness", **ablations.row_stamp(ABLATION, "harness", 7)),
                                 row(arm="harness", **ablations.row_stamp(dict(ABLATION, sha256="7" * 64),
                                                                          "harness", 7))])
        report = self.supervisor.reindex(self.repository)
        self.assertEqual((report["skipped"], report["imported_runs"]), ([], 2))

    def test_a_pair_row_without_a_schema_reads_as_the_engine_reads_it(self):
        stamp = replay_pair.row_stamp(PAIR, "treatment")
        stamp["ablation"].pop("schema")
        contract = evaluation.row_contract(row(arm="treatment", **stamp))
        self.assertEqual((contract["shape"], contract["ablation"]["schema"]), ("pair", replay_pair.SCHEMA))

    def test_unsupported_contract_versions_are_reported_and_skipped(self):
        self.write("next", [row(ablation={"name": "x", "sha256": "1" * 64, "schema": 3,
                                          "arms": ["a"]})])
        report = self.supervisor.reindex(self.repository)
        self.assertEqual(report["imported_runs"], 0)
        self.assertEqual(report["skipped"], [{"path": "benchmarks/next/results.jsonl",
                                              "reason": "unsupported ablation stamp schema: 3"}])


class ProofBundleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = bundle_tests.EvidenceBundleTest(
            methodName="test_valid_bundle_rederives_figures_cards_and_descriptive_statistics")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repository = self.root / "repository"
        self.repository.mkdir()
        shutil.copytree(str(self.fixture.root), str(self.repository / "proof"), symlinks=True)
        (self.repository / "product.json").write_text(json.dumps({"evidence_cards": [
            {"field": "/headline", "text": "x", "bundle": "proof", "card": "card"}]}), encoding="utf-8")
        catalog = self.root / "suites.json"
        catalog.write_text(json.dumps({"schema_version": 1, "suites": []}))
        self.supervisor = runs.RunSupervisor(self.root / "state", catalog,
                                             target_service=FixtureTargetService())

    def cli(self):
        done = subprocess.run([sys.executable, str(REPO / "bin" / "harness"), "evidence", "verify",
                               str(self.repository / "proof"), "--json"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return json.loads(done.stdout)

    def test_proof_status_equals_the_cli_verifier(self):
        report = self.supervisor.reindex(self.repository)
        self.assertEqual(report["skipped"], [])
        record = next(r for r in self.supervisor.history.list() if r["source"]["kind"] == "evidence-bundle")
        expected = self.cli()
        self.assertEqual(record["raw"], expected)
        self.assertEqual(record["evaluation"]["proof"], evaluation.proof_status(expected))
        self.assertIs(expected["ok"], True)
        self.assertEqual(record["evaluation"]["proof"]["status"], "verified")
        self.assertEqual(record["status"], "succeeded")
        self.assertEqual(self.supervisor.run_evaluation(record["run_id"])["shape"], "proof-bundle")

    def test_a_contradicted_bundle_is_failed_with_the_verifier_errors(self):
        self.fixture.root = self.repository / "proof"
        self.fixture._mutate_rows(lambda value: value.update(model="another-model"))
        self.supervisor.reindex(self.repository)
        record = next(r for r in self.supervisor.history.list() if r["source"]["kind"] == "evidence-bundle")
        expected = self.cli()
        self.assertFalse(expected["ok"])
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["evaluation"]["proof"]["status"], "failed")
        self.assertEqual(record["evaluation"]["proof"]["errors"], expected["errors"])

    def test_a_different_verdict_on_re_verification_is_a_new_record(self):
        self.supervisor.reindex(self.repository)
        before = [r for r in self.supervisor.history.list() if r["source"]["kind"] == "evidence-bundle"]
        self.fixture.root = self.repository / "proof"
        self.fixture._mutate_rows(lambda value: value.update(model="another-model"))
        report = self.supervisor.reindex(self.repository)
        self.assertEqual(report["skipped"], [])
        after = [r for r in self.supervisor.history.list() if r["source"]["kind"] == "evidence-bundle"]
        self.assertEqual(len(after), 1)
        self.assertNotEqual(before[0]["run_id"], after[0]["run_id"])
        self.assertEqual((before[0]["status"], after[0]["status"]), ("succeeded", "failed"))

    def test_a_bundle_path_outside_the_repository_is_never_read(self):
        (self.repository / "product.json").write_text(json.dumps({"evidence_cards": [
            {"bundle": "../proof"}, {"bundle": "/tmp/proof"}, {"bundle": 3}]}), encoding="utf-8")
        self.assertEqual(evaluation.bound_bundles(self.repository), [])


class UsageLedgerTests(unittest.TestCase):
    """A Studio spend row in the versioned ledger reads through `citizen usage --json`."""

    def test_studio_spend_counts_without_unknown_token_metrics(self):
        loader = importlib.machinery.SourceFileLoader(
            "usage_log_contracts", str(REPO / "claude" / "hooks" / "usage-log.py"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        usage_log = importlib.util.module_from_spec(spec)
        loader.exec_module(usage_log)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            usage_log.upsert({"kind": "studio_run", "runtime": "studio", "run_id": "run-a",
                              "suite_id": "paid-suite", "pricing_source": "api_credit",
                              "spend_usd": 2.5, "spend_cap_usd": "5.0",
                              "ended": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                             path=root / "usage.jsonl")
            parser_args = ["usage", "--json", "--days", "30"]
            output = io.StringIO()
            with mock.patch.object(harness, "state_dir", return_value=root), \
                    contextlib.redirect_stdout(output):
                status = harness.main(parser_args)
        self.assertEqual(status, 0)
        document = json.loads(output.getvalue())
        self.assertEqual(document["schema_version"], harness.USAGE_JSON_VERSION)
        text = json.dumps(document)
        self.assertIn("2.5", text)
        for metric in harness.RATE_FIELDS:
            self.assertNotIn('"%s": 1' % metric, json.dumps(document.get("unknown") or {}))


if __name__ == "__main__":
    unittest.main()

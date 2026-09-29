import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("evidence_bundle", ROOT / "scripts" / "evidence_bundle.py")
EVIDENCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVIDENCE)


def run(*args, cwd, env=None):
    return subprocess.run(args, cwd=str(cwd), env=env, check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True).stdout.strip()


class EvidenceBundleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        run("git", "init", "-q", cwd=self.repo)
        run("git", "config", "user.email", "test", cwd=self.repo)
        run("git", "config", "user.name", "Test", cwd=self.repo)
        (self.repo / "benchmarks/preregistrations").mkdir(parents=True)
        (self.repo / "benchmarks/tasks").mkdir(parents=True)
        tasks = {"tasks": [{"id": "alpha"}, {"id": "beta"}]}
        self._write_json(self.repo / "benchmarks/tasks/proof.json", tasks)
        task_sha = self._sha(self.repo / "benchmarks/tasks/proof.json")
        plan = """## Run
- **Question:** Does the treatment change Cost-of-Pass?
- **Date registered:** 2026-01-01

## Hypotheses
- **Primary:** SM-2

## Primary metric
- **Metric:** Cost-of-Pass

## Sample size
- **Tasks:** alpha, beta
- **Trials per task and arm:** 5

## Decision rule
Use the registered SM-2 rule.

Task manifest: benchmarks/tasks/proof.json
Task manifest sha256: %s
""" % task_sha
        (self.repo / "benchmarks/preregistrations/2026-01-01-proof.md").write_text(plan)
        stamp = dict(os.environ, GIT_AUTHOR_DATE="2026-01-01T12:00:00Z",
                     GIT_COMMITTER_DATE="2026-01-01T12:00:00Z")
        run("git", "add", ".", cwd=self.repo, env=stamp)
        run("git", "commit", "-qm", "register", cwd=self.repo, env=stamp)
        self.plan_commit = run("git", "rev-parse", "HEAD", cwd=self.repo)
        (self.repo / "run.txt").write_text("frozen\n")
        stamp.update(GIT_AUTHOR_DATE="2026-01-02T12:00:00Z",
                     GIT_COMMITTER_DATE="2026-01-02T12:00:00Z")
        run("git", "add", ".", cwd=self.repo, env=stamp)
        run("git", "commit", "-qm", "freeze", cwd=self.repo, env=stamp)
        self.run_commit = run("git", "rev-parse", "HEAD", cwd=self.repo)
        self._build()

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _write_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True, indent=1) + "\n")

    def _ref(self, relative, **metadata):
        path = self.root / relative
        return dict({"path": relative, "sha256": self._sha(path)}, **metadata)

    def _arm(self, arm):
        inputs = {"base_image": "base@sha256:" + "1" * 64, "claude_code_version": "2.0.0"}
        harness = {"ref": "v0.15.0", "commit": self.run_commit} if arm == "harness" else None
        declaration = EVIDENCE.replay_arms.declaration(arm, inputs, harness=harness)
        manifest = {"schema": 1, "roots": ({"harness": "/opt/model-citizen"} if harness else {}),
                    "excluded": {}, "normalised": [], "claude_code_version": "2.0.0",
                    "cli_packages": ["@anthropic-ai/claude-code" + "@2.0.0"],
                    "harness_commit": self.run_commit if harness else None,
                    "summary": {key: [] for key in
                                ("settings", "hooks", "rules", "skills", "agents", "plugins",
                                 "instructions")}, "entries": []}
        return {"arm": arm, "label": arm, "image": "proof-%s:1" % arm,
                "image_id": "sha256:" + ("2" if arm == "bare" else "3") * 64,
                "declaration": declaration,
                "declaration_sha256": EVIDENCE.replay_arms.digest(declaration),
                "manifest": manifest, "manifest_sha256": EVIDENCE.replay_arms.digest(manifest),
                "harness_ref": harness and harness["ref"],
                "harness_commit": harness and harness["commit"], "paths": {},
                "protocol": {"evidence": "pre-registered",
                             "pre_registration": "benchmarks/preregistrations/2026-01-01-proof.md",
                             "pre_registration_commit": self.plan_commit}}

    def _build(self):
        artifacts = self.root / "artifacts"
        artifacts.mkdir()
        for name, source in (("tasks", self.repo / "benchmarks/tasks/proof.json"),
                             ("plan", self.repo / "benchmarks/preregistrations/2026-01-01-proof.md"),
                             ("prices", ROOT / "policy/prices.json")):
            (artifacts / (name + source.suffix)).write_bytes(source.read_bytes())
        arm_records = {arm: self._arm(arm) for arm in EVIDENCE.ARMS}
        for arm, record in arm_records.items():
            self._write_json(artifacts / (arm + ".json"), record)
        rows, trajectories = [], []
        index = 0
        for task in ("alpha", "beta"):
            for trial in range(1, 6):
                for arm in (EVIDENCE.ARMS if trial % 2 else EVIDENCE.ARMS[::-1]):
                    index += 1
                    record = arm_records[arm]
                    passed = (trial + (task == "beta") + (arm == "harness")) % 3 != 0
                    row = {"task": task, "arm": arm, "rep": trial, "schedule_index": index,
                           "started_at": "2026-01-03T%02d:00:00Z" % index,
                           "model": "claude-sonnet-5", "cli_version": "2.0.0", "effort": "high",
                           "task_order_seed": 17, "bootstrap_seed": 23,
                           "evidence": "pre-registered",
                           "pre_registration": "benchmarks/preregistrations/2026-01-01-proof.md",
                           "pre_registration_commit": self.plan_commit,
                           "command": ["claude", "--print", "task"],
                           "arm_image_id": record["image_id"],
                           "arm_declaration_sha256": record["declaration_sha256"],
                           "arm_manifest_sha256": record["manifest_sha256"],
                           "arm_base_image": record["declaration"]["base_image"],
                           "input_tokens": 1000 + trial, "output_tokens": 100 + trial,
                           "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                           "cache_write_1h": 0, "passed": passed, "error": False,
                           "outcome": "pass" if passed else "fail", "task_long": task == "beta",
                           "init_surface_source": "cli-init", "observed_effort": None}
                    for field in EVIDENCE.SURFACE_FIELDS:
                        row[field] = 1 if arm == "harness" else 0
                    for field in EVIDENCE.SURFACE_HASH_FIELDS:
                        row[field] = hashlib.sha256((field + arm).encode()).hexdigest()
                    rows.append(row)
                    rel = "artifacts/trajectories/%s-%s-%d.log" % (task, arm, trial)
                    path = self.root / rel
                    path.parent.mkdir(exist_ok=True)
                    path.write_text("redacted complete transcript\n")
                    trajectories.append(self._ref(rel, task=task, arm=arm, trial=trial))
        rows_path = artifacts / "rows.jsonl"
        rows_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
        receipt = {"repository": "JakeSelby/model-citizen", "pull_request": 1,
                   "plan_commit": self.plan_commit, "merge_commit": self.plan_commit,
                   "merged_at": "2026-01-02T00:00:00Z"}
        self._write_json(artifacts / "receipt.json", receipt)
        audits = {"task_audits": [{"task": task, "task_valid": True, "outcome_valid": True,
                                    "manifest_unchanged": True} for task in ("alpha", "beta")],
                  "arm_rebuilds": [{"arm": arm, "equal": True} for arm in EVIDENCE.ARMS],
                  "judge": {"kind": "deterministic"},
                  "contamination": {key: True for key in
                                    ("answer_unreachable", "installed_checkout_checked",
                                     "no_observed_reads", "identical_access",
                                     "training_cutoffs_recorded")},
                  "field_checks": {"sample_ratio": {"planned": {"bare": 10, "harness": 10},
                                                       "completed": {"bare": 10, "harness": 10},
                                                       "p_value": 1.0},
                                   "novelty": {"status": "not-applicable", "reason": "replay-only"},
                                   "cuped": {"status": "not-applicable", "reason": "replay-only"},
                                   "dilution": {"status": "not-applicable", "reason": "replay-only"}},
                  "limitations": {name: "limited" for name in
                                  ("models", "runtimes", "task_kinds", "magnitudes", "mechanisms",
                                   "stop_condition")}}
        self._write_json(artifacts / "audits.json", audits)
        (artifacts / "report.md").write_text("# Proof\n\n## What we do not claim\n\nNo generality.\n")
        artifact_map = {
            "rows": self._ref("artifacts/rows.jsonl"),
            "tasks": self._ref("artifacts/tasks.json", git_path="benchmarks/tasks/proof.json"),
            "plan": self._ref("artifacts/plan.md",
                              git_path="benchmarks/preregistrations/2026-01-01-proof.md",
                              commit=self.plan_commit),
            "github_receipt": self._ref("artifacts/receipt.json"),
            "prices": self._ref("artifacts/prices.json"),
            "audits": self._ref("artifacts/audits.json"),
            "report": self._ref("artifacts/report.md"),
            "arms": [self._ref("artifacts/%s.json" % arm) for arm in EVIDENCE.ARMS],
            "trajectories": trajectories,
        }
        index_doc = {"schema_version": 1, "bundle_id": "proof-1",
                     "repository": {"path": "repo", "name": "JakeSelby/model-citizen",
                                    "run_commit": self.run_commit},
                     "artifacts": artifact_map,
                     "design": {"model": "claude-sonnet-5", "cli_version": "2.0.0",
                                "effort": "high", "tasks": ["alpha", "beta"],
                                "trials_per_task": 5, "arms": ["bare", "harness"],
                                "task_order_seed": 17, "bootstrap_seed": 23, "resamples": 100,
                                "model_sampling_seedable": False,
                                "replay_command": "python3 scripts/cost_bench.py replay",
                                "verify_command": "harness evidence verify proof", "run_cap_usd": 5.0},
                     "statistics": {"seed": 23, "resamples": 100},
                     "published_figures": [], "evidence_cards": [],
                     "items": {str(n): ({"status": "not-applicable",
                                         "reason": "deterministic outcome"} if n == 8
                                        else {"status": "satisfied"}) for n in range(1, 13)}}
        self._write_json(self.root / "bundle.json", index_doc)
        first = EVIDENCE.verify(self.root)
        self.assertIn("sm2", first["derived"], first["errors"])
        index_doc["published_figures"] = [{"name": "ratio", "pointer": "/sm2/ratio",
                                            "value": first["derived"]["sm2"]["ratio"],
                                            "estimand": "intention-to-treat"}]
        index_doc["evidence_cards"] = [{"id": "ratio", "claim": "Observed ratio",
                                         "estimand": "intention-to-treat",
                                         "figure": {"pointer": "/sm2/ratio",
                                                    "value": first["derived"]["sm2"]["ratio"]},
                                         "interval": {"pointer": "/sm2/ratio_interval",
                                                      "value": first["derived"]["sm2"]["ratio_interval"]},
                                         "bundle": "proof-1", "verify_status": False}]
        self._write_json(self.root / "bundle.json", index_doc)

    def _index(self):
        return json.loads((self.root / "bundle.json").read_text())

    def _save_index(self, index):
        self._write_json(self.root / "bundle.json", index)

    def _mutate_artifact(self, name, change):
        index = self._index()
        ref = index["artifacts"][name]
        path = self.root / ref["path"]
        value = json.loads(path.read_text())
        change(value)
        self._write_json(path, value)
        ref["sha256"] = self._sha(path)
        self._save_index(index)

    def test_valid_bundle_rederives_figures_cards_and_descriptive_statistics(self):
        result = EVIDENCE.verify(self.root)
        self.assertTrue(result["ok"], result["errors"])
        self.assertTrue(all(result["checks"].values()))
        self.assertTrue(result["cards"][0]["verify_status"])
        self.assertEqual(10, result["derived"]["per_task"]["alpha"]["bare"]["attempts"] * 2)
        self.assertEqual(5, result["derived"]["icc"]["bare"]["pass"]["m"])
        self.assertEqual("verified", result["derived"]["runtime_surface"]["bare"]["status"])
        self.assertEqual({"pinned": "high", "status": "unknown", "observed": 0, "unknown": 10},
                         result["derived"]["runtime_surface"]["bare"]["effort"])

    def test_each_standard_item_fails_closed(self):
        cases = {
            1: lambda i: i["repository"].update(run_commit="0" * 40),
            2: lambda i: i["design"].update(tasks=["alpha", "missing"]),
            3: lambda i: i["design"].update(cli_version="different"),
            4: lambda i: i["design"].update(run_cap_usd=float("inf")),
            5: lambda i: i["statistics"].update(seed=99),
            6: lambda i: i["design"].update(tasks=["beta", "alpha"]),
            7: lambda i: i["artifacts"].update(trajectories=i["artifacts"]["trajectories"][:-1]),
            8: lambda i: i["items"]["8"].update(reason="unknown"),
            9: lambda i: i["items"]["9"].update(status="missing"),
            10: lambda i: i["published_figures"][0].update(value=999),
            11: lambda i: i["items"]["11"].update(status="not-applicable"),
            12: lambda i: i["items"]["12"].update(status="missing"),
        }
        original = (self.root / "bundle.json").read_text()
        for item, mutate in cases.items():
            with self.subTest(item=item):
                index = json.loads(original)
                mutate(index)
                self._save_index(index)
                result = EVIDENCE.verify(self.root)
                self.assertFalse(result["checks"].get(str(item), False), result["errors"])
        (self.root / "bundle.json").write_text(original)

    def test_structural_audits_drive_their_numbered_checks(self):
        original_index = self._index()
        audits_ref = original_index["artifacts"]["audits"]
        audits_path = self.root / audits_ref["path"]
        original_audits = json.loads(audits_path.read_text())
        cases = {
            2: lambda audit: audit["task_audits"][0].update(task_valid=False),
            8: lambda audit: audit.update(judge={"kind": "model"}),
            9: lambda audit: audit["contamination"].update(answer_unreachable=False),
            11: lambda audit: audit["field_checks"]["sample_ratio"].update(p_value=0.5),
            12: lambda audit: audit["limitations"].update(models=""),
        }
        for item, mutate in cases.items():
            with self.subTest(item=item):
                index, audits = copy.deepcopy(original_index), copy.deepcopy(original_audits)
                if item == 8:
                    index["items"]["8"] = {"status": "satisfied"}
                mutate(audits)
                self._write_json(audits_path, audits)
                index["artifacts"]["audits"]["sha256"] = self._sha(audits_path)
                self._save_index(index)
                result = EVIDENCE.verify(self.root)
                self.assertFalse(result["checks"][str(item)], result["errors"])
        self._write_json(audits_path, original_audits)
        original_index["artifacts"]["audits"]["sha256"] = self._sha(audits_path)
        self._save_index(original_index)

    def test_unknown_price_and_nonfinite_json_are_refused(self):
        index = self._index()
        rows = self.root / index["artifacts"]["rows"]["path"]
        values = [json.loads(line) for line in rows.read_text().splitlines()]
        values[0]["model"] = "unknown-model"
        rows.write_text("".join(json.dumps(row) + "\n" for row in values))
        index["artifacts"]["rows"]["sha256"] = self._sha(rows)
        self._save_index(index)
        result = EVIDENCE.verify(self.root)
        self.assertFalse(result["checks"]["4"])
        self.assertTrue(any("unpriced" in error for error in result["errors"]))
        (self.root / "bundle.json").write_text('{"schema_version": NaN}')
        self.assertIn("non-finite", EVIDENCE.verify(self.root)["errors"][0])
        (self.root / "bundle.json").write_text('{"schema_version": 1e999}')
        self.assertIn("non-finite", EVIDENCE.verify(self.root)["errors"][0])

    def _mutate_rows(self, change):
        index = self._index()
        rows = self.root / index["artifacts"]["rows"]["path"]
        values = [json.loads(line) for line in rows.read_text().splitlines()]
        for row in values:
            change(row)
        rows.write_text("".join(json.dumps(row) + "\n" for row in values))
        index["artifacts"]["rows"]["sha256"] = self._sha(rows)
        self._save_index(index)

    def _set_card_claim(self, claim):
        index = self._index()
        index["evidence_cards"][0]["claim"] = claim
        self._save_index(index)

    def test_missing_init_surface_is_unknown_and_fails_item_three(self):
        first = {}

        def drop_init(row):
            if not first:
                first.update(arm=row["arm"])
                row["init_surface_source"] = None
                for field in EVIDENCE.SURFACE_FIELDS + EVIDENCE.SURFACE_HASH_FIELDS:
                    row[field] = None
        self._mutate_rows(drop_init)
        result = EVIDENCE.verify(self.root)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["3"])
        self.assertTrue(any("no verified CLI init surface" in error for error in result["errors"]))
        self.assertEqual("unknown", result["derived"]["runtime_surface"][first["arm"]]["status"])
        self.assertTrue(any(line.startswith(first["arm"] + " loaded surface")
                            for line in result["unknown"]), result["unknown"])

    def test_absent_observed_effort_field_fails_item_three(self):
        self._mutate_rows(lambda row: row.pop("observed_effort"))
        result = EVIDENCE.verify(self.root)
        self.assertFalse(result["checks"]["3"])
        self.assertTrue(any("observed effort from unknown" in error for error in result["errors"]))
        for arm in EVIDENCE.ARMS:
            self.assertEqual("unknown", result["derived"]["runtime_surface"][arm]["effort"]["status"])

    def test_requested_but_unobserved_effort_is_reported_unknown_never_verified(self):
        self._mutate_rows(lambda row: row.update(effort="high", observed_effort=None))
        result = EVIDENCE.verify(self.root)
        self.assertTrue(result["ok"], result["errors"])
        effort = result["derived"]["runtime_surface"]["harness"]["effort"]
        self.assertEqual({"pinned": "high", "status": "unknown", "observed": 0, "unknown": 10}, effort)
        self.assertIn("harness effort: requested high, observed on 0 of 10 planned attempts",
                      result["unknown"])
        self._set_card_claim("Observed ratio at matched high effort")
        refused = EVIDENCE.verify(self.root)
        self.assertFalse(refused["ok"])
        self.assertFalse(refused["cards"][0]["verify_status"])
        self.assertTrue(any("reasoning effort the rows did not observe" in error
                            for error in refused["errors"]), refused["errors"])

    def test_observed_effort_on_every_attempt_verifies_and_admits_an_effort_claim(self):
        self._mutate_rows(lambda row: row.update(effort="high", observed_effort="high"))
        self._set_card_claim("Observed ratio at matched high effort and loaded surface")
        result = EVIDENCE.verify(self.root)
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual([], result["unknown"])
        self.assertTrue(result["cards"][0]["verify_status"])

    def test_observed_or_requested_effort_off_the_pin_fails_item_three(self):
        for field, phrase in (("observed_effort", "observed effort differs"),
                              ("effort", "requested effort differs")):
            with self.subTest(field=field):
                self._mutate_rows(lambda row: row.update({"observed_effort": None, field: "low"}))
                result = EVIDENCE.verify(self.root)
                self.assertFalse(result["checks"]["3"])
                self.assertTrue(any(phrase in error for error in result["errors"]), result["errors"])
                self._mutate_rows(lambda row: row.update({"observed_effort": None, "effort": "high"}))

    def test_surface_claim_needs_the_init_surface_on_every_attempt(self):
        def drop_init(row):
            if row["arm"] == "harness":
                row["init_surface_source"] = None
        self._mutate_rows(drop_init)
        self._set_card_claim("Observed ratio with the loaded surface held constant")
        result = EVIDENCE.verify(self.root)
        self.assertFalse(result["cards"][0]["verify_status"])
        self.assertTrue(any("loaded runtime surface the rows did not observe" in error
                            for error in result["errors"]), result["errors"])

    def test_safe_loader_rejects_traversal_symlink_and_duplicate_keys(self):
        index = self._index()
        original = copy.deepcopy(index)
        index["artifacts"]["report"]["path"] = "../outside"
        self._save_index(index)
        self.assertIn("unsafe segment", EVIDENCE.verify(self.root)["errors"][0])
        target = self.root / "artifacts/report-link.md"
        target.symlink_to(self.root / "repo/run.txt")
        original["artifacts"]["report"] = {"path": "artifacts/report-link.md",
                                             "sha256": self._sha(self.root / "repo/run.txt")}
        self._save_index(original)
        self.assertIn("symlink", EVIDENCE.verify(self.root)["errors"][0])
        (self.root / "bundle.json").write_text('{"schema_version":1,"schema_version":1}')
        self.assertIn("duplicate JSON key", EVIDENCE.verify(self.root)["errors"][0])

    def test_bundle_commands_are_data_not_executed(self):
        marker = self.root / "executed"
        index = self._index()
        index["design"]["verify_command"] = "touch %s" % marker
        self._save_index(index)
        EVIDENCE.verify(self.root)
        self.assertFalse(marker.exists())

    def test_inherited_git_selectors_do_not_redirect_provenance_checks(self):
        marker = self.root / "global-helper-ran"
        global_config = self.root / "host-gitconfig"
        global_config.write_text("[core]\n\tfsmonitor = touch %s\n" % marker)
        with mock.patch.dict(os.environ, {"GIT_DIR": str(self.root / "missing"),
                                          "GIT_WORK_TREE": str(self.root / "missing"),
                                          "GIT_CONFIG_GLOBAL": str(global_config),
                                          "GIT_CONFIG_COUNT": "1",
                                          "GIT_CONFIG_KEY_0": "core.fsmonitor",
                                          "GIT_CONFIG_VALUE_0": "touch %s" % marker}, clear=False):
            result = EVIDENCE.verify(self.root)
        self.assertTrue(result["ok"], result["errors"])
        self.assertFalse(marker.exists())

    def test_repository_git_helper_is_refused_without_execution(self):
        marker = self.root / "repository-helper-ran"
        run("git", "config", "core.fsmonitor", "touch %s" % marker, cwd=self.repo)
        result = EVIDENCE.verify(self.root)
        self.assertFalse(result["ok"])
        self.assertIn("executable helper", result["errors"][0])
        self.assertFalse(marker.exists())

    def test_malformed_nested_records_fail_without_a_traceback(self):
        index = self._index()
        arm_ref = index["artifacts"]["arms"][0]
        arm = self.root / arm_ref["path"]
        original_arm = arm.read_bytes()
        self._write_json(arm, ["not", "an", "object"])
        arm_ref["sha256"] = self._sha(arm)
        self._save_index(index)
        result = EVIDENCE.verify(self.root)
        self.assertFalse(result["checks"]["3"])
        self.assertTrue(any("arm record is not an object" in error for error in result["errors"]))
        arm.write_bytes(original_arm)
        arm_ref["sha256"] = self._sha(arm)
        index["design"] = []
        self._save_index(index)
        result = EVIDENCE.verify(self.root)
        self.assertFalse(result["checks"]["3"])
        self.assertTrue(any("design is not an object" in error for error in result["errors"]))

    def test_icc_is_per_arm_and_preserves_negative_and_undefined_results(self):
        rows = []
        for task, values in (("a", [0, 1]), ("b", [1, 0])):
            for arm in EVIDENCE.ARMS:
                rows += [{"task": task, "arm": arm, "passed": bool(value), "error": False,
                          "cost_usd": float(value + 1)} for value in values]
        estimate = EVIDENCE._icc(rows, "bare", "pass")
        self.assertLess(estimate["value"], 0)
        self.assertEqual(1 + (estimate["m"] - 1) * estimate["value"],
                         estimate["design_effect"])
        rows.pop()
        undefined = EVIDENCE._icc(rows, "harness", "pass")
        self.assertIsNone(undefined["value"])
        self.assertIn("unequal", undefined["reason"])

    def test_plan_postdating_and_git_byte_drift_fail(self):
        index = self._index()
        rows = self.root / index["artifacts"]["rows"]["path"]
        original_rows = rows.read_bytes()
        values = [json.loads(line) for line in rows.read_text().splitlines()]
        values[0]["started_at"] = "2026-01-01T11:00:00Z"
        rows.write_text("".join(json.dumps(row) + "\n" for row in values))
        index["artifacts"]["rows"]["sha256"] = self._sha(rows)
        self._save_index(index)
        result = EVIDENCE.verify(self.root)
        self.assertTrue(any("postdates" in error for error in result["errors"]))
        rows.write_bytes(original_rows)
        index["artifacts"]["rows"]["sha256"] = self._sha(rows)
        self._save_index(index)
        plan = self.root / "artifacts/plan.md"
        plan.write_text(plan.read_text() + "drift\n")
        index = self._index()
        index["artifacts"]["plan"]["sha256"] = self._sha(plan)
        self._save_index(index)
        self.assertTrue(any("bytes differ" in error for error in EVIDENCE.verify(self.root)["errors"]))


if __name__ == "__main__":
    unittest.main()

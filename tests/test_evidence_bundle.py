import copy
import hashlib
import shutil
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

## Deviation log

- 2026-01-01: none yet
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
        # The arm pins the design's effort, and its image bakes no effort override (schema 2).
        declaration = EVIDENCE.replay_arms.declaration(arm, inputs, harness=harness, effort="high")
        lister = EVIDENCE.replay_arms.arm_manifest
        manifest = {"schema": lister.SCHEMA, "roots": ({"harness": "/opt/model-citizen"} if harness else {}),
                    "excluded": {}, "normalised": [], "claude_code_version": "2.0.0",
                    "cli_packages": ["@anthropic-ai/claude-code" + "@2.0.0"],
                    "environment": {lister.EFFORT_ENV: None},
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
                           "init_surface_source": "cli-init", "observed_effort": None,
                           "observed_model": "claude-sonnet-5"}
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
        values[0]["observed_model"] = "unknown-model"
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

    def _set_arm_effort(self, effort):
        """Re-declare both arms at `effort`, keeping every row's arm identity consistent."""
        index = self._index()
        digests = {}
        for ref in index["artifacts"]["arms"]:
            path = self.root / ref["path"]
            record = json.loads(path.read_text())
            record["declaration"]["effort"] = effort
            record["declaration_sha256"] = EVIDENCE.replay_arms.digest(record["declaration"])
            digests[record["arm"]] = record["declaration_sha256"]
            self._write_json(path, record)
            ref["sha256"] = self._sha(path)
        self._save_index(index)
        self._mutate_rows(lambda row: row.update(arm_declaration_sha256=digests[row["arm"]]))

    def test_arm_declarations_off_the_design_effort_fail_item_four(self):
        self._set_arm_effort("low")
        result = EVIDENCE.verify(self.root)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["4"])
        self.assertIn("item 4: bare arm declaration pins effort 'low', not the design's 'high'",
                      result["errors"])
        self.assertIn("item 4: harness arm declaration pins effort 'low', not the design's 'high'",
                      result["errors"])

    def test_arm_declaration_pinning_no_effort_fails_item_four(self):
        self._set_arm_effort(None)
        result = EVIDENCE.verify(self.root)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["4"])
        self.assertIn("item 4: bare arm declaration pins no effort", result["errors"])
        self.assertIn("item 4: harness arm declaration pins no effort", result["errors"])

    def test_arm_declarations_at_the_design_effort_verify(self):
        self._set_arm_effort("low")
        index = self._index()
        index["design"]["effort"] = "low"
        self._save_index(index)
        self._mutate_rows(lambda row: row.update(effort="low", observed_effort="low"))
        result = EVIDENCE.verify(self.root)
        self.assertTrue(result["ok"], result["errors"])
        self.assertTrue(result["checks"]["4"])

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


    # Rebuilding the history lets a test change the committed plan or sign the plan commit while
    # every other part of the fixture stays the valid bundle.
    def _rebuild_bundle(self):
        shutil.rmtree(str(self.root / "artifacts"))
        (self.root / "bundle.json").unlink()
        self._build()

    def _commit_plan_amendment(self, suffix):
        plan = self.repo / "benchmarks/preregistrations/2026-01-01-proof.md"
        plan.write_text(plan.read_text() + suffix)
        stamp = dict(os.environ, GIT_AUTHOR_DATE="2026-01-02T13:00:00Z",
                     GIT_COMMITTER_DATE="2026-01-02T13:00:00Z")
        run("git", "commit", "-qam", "amend plan", cwd=self.repo, env=stamp)
        self.run_commit = run("git", "rev-parse", "HEAD", cwd=self.repo)
        self._rebuild_bundle()

    def _write_object(self, kind, body):
        return subprocess.run(["git", "hash-object", "-t", kind, "-w", "--stdin"], cwd=str(self.repo),
                              input=body, stdout=subprocess.PIPE, check=True).stdout.decode().strip()

    def _sign_plan_commit(self):
        plan = subprocess.run(["git", "cat-file", "commit", self.plan_commit], cwd=str(self.repo),
                              stdout=subprocess.PIPE, check=True).stdout
        headers, message = plan.split(b"\n\n", 1)
        signature = (b"gpgsig -----BEGIN PGP SIGNATURE-----\n \n iQEzBAABCAAdFiEE\n"
                     b" -----END PGP SIGNATURE-----")
        signed = self._write_object("commit", headers + b"\n" + signature + b"\n\n" + message)
        child = subprocess.run(["git", "cat-file", "commit", self.run_commit], cwd=str(self.repo),
                               stdout=subprocess.PIPE, check=True).stdout
        child = child.replace(b"parent " + self.plan_commit.encode(), b"parent " + signed.encode())
        self.plan_commit, self.run_commit = signed, self._write_object("commit", child)
        run("git", "update-ref", "HEAD", self.run_commit, cwd=self.repo)
        self._rebuild_bundle()

    def test_signed_plan_commit_never_runs_a_repository_signature_helper(self):
        marker = self.root / "gpg-helper-ran"
        helper = self.root / "gpg-helper.sh"
        helper.write_text("#!/bin/sh\ntouch %s\n" % marker)
        helper.chmod(0o755)
        self._sign_plan_commit()
        for key in ("gpg.program", "gpg.ssh.program", "gpg.x509.program"):
            run("git", "config", key, str(helper), cwd=self.repo)
        run("git", "config", "log.showSignature", "true", cwd=self.repo)
        result = EVIDENCE.verify(self.root)
        self.assertFalse(marker.exists())
        self.assertTrue(result["ok"], result["errors"])

    def test_repository_configuration_is_never_read(self):
        marker = self.root / "repository-helper-ran"
        included = self.root / "included-config"
        included.write_text("[core]\n\tfsmonitor = touch %s\n" % marker)
        for key, value in (("core.fsmonitor", "touch %s" % marker), ("core.hooksPath", str(self.root)),
                           ("include.path", str(included)), ("core.commitGraph", "true"),
                           ("core.pager", "touch %s" % marker)):
            run("git", "config", key, value, cwd=self.repo)
        result = EVIDENCE.verify(self.root)
        self.assertTrue(result["ok"], result["errors"])
        self.assertFalse(marker.exists())

    def test_commondir_redirection_to_another_repository_is_refused(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(outside))
        shutil.copytree(str(self.repo / ".git"), str(outside / ".git"))
        (self.repo / ".git/commondir").write_text(str(outside / ".git") + "\n")
        result = EVIDENCE.verify(self.root)
        self.assertFalse(result["ok"])
        self.assertIn("reaches outside the bundle", result["errors"][0])

    def test_gitdir_file_alternates_and_symlinked_objects_are_refused(self):
        git_dir = self.repo / ".git"
        moved = self.root / "moved-git"

        def gitdir_file():
            git_dir.rename(moved)
            git_dir.write_text("gitdir: %s\n" % moved)

        def restore_gitdir():
            git_dir.unlink()
            moved.rename(git_dir)

        def alternates():
            (git_dir / "objects/info").mkdir(exist_ok=True)
            (git_dir / "objects/info/alternates").write_text(str(moved) + "\n")

        def symlinked_objects():
            (git_dir / "objects").rename(moved)
            (git_dir / "objects").symlink_to(moved)

        def restore_objects():
            (git_dir / "objects").unlink()
            moved.rename(git_dir / "objects")

        cases = ((gitdir_file, restore_gitdir, "self-contained"),
                 (alternates, lambda: (git_dir / "objects/info/alternates").unlink(), "outside the bundle"),
                 (symlinked_objects, restore_objects, "no local object store"))
        for change, restore, phrase in cases:
            with self.subTest(phrase=phrase):
                change()
                result = EVIDENCE.verify(self.root)
                restore()
                self.assertFalse(result["ok"])
                self.assertIn(phrase, result["errors"][0])
        self.assertTrue(EVIDENCE.verify(self.root)["ok"])

    def test_run_commit_is_never_passed_to_git_as_an_option(self):
        # Git would append ":<path>" to the value, so the directories that would receive the
        # written file exist; the verifier must never hand Git the value at all.
        written = str(self.root / "git-wrote-this")
        targets = [Path(written + ":" + relative) for relative in
                   ("benchmarks/tasks/proof.json", "benchmarks/preregistrations/2026-01-01-proof.md")]
        for target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
        index = self._index()
        index["repository"]["run_commit"] = "--output=" + written
        self._save_index(index)
        result = EVIDENCE.verify(self.root)
        self.assertFalse(result["checks"]["1"])
        self.assertEqual([], [str(target) for target in targets if target.exists()])

    def test_fields_appended_to_the_registered_plan_are_refused(self):
        self._commit_plan_amendment("## Hypotheses\n- **Primary:** a different hypothesis\n")
        result = EVIDENCE.verify(self.root)
        self.assertFalse(result["checks"]["1"])
        self.assertTrue(any("appended outside a deviation log" in error or
                            "not a dated deviation-log entry" in error
                            for error in result["errors"]), result["errors"])

    def test_dated_deviation_entries_appended_after_registration_verify(self):
        self._commit_plan_amendment("- 2026-01-02: trial cap raised; touches no figure\n"
                                    "  because the first build timed out\n")
        result = EVIDENCE.verify(self.root)
        self.assertTrue(result["ok"], result["errors"])

    def test_plan_amendment_rules(self):
        logged = b"## Run\n- **Question:** q\n\n## Deviation log\n\n- 2026-01-01: none yet\n"
        unlogged = b"## Run\n- **Question:** q\n"
        cases = (
            (logged, logged, None),
            (logged, logged + b"- 2026-01-05: changed cap\n  continued\n", None),
            (logged, logged + b"## Decision rule\nnew\n", "not a dated"),
            (logged, logged + b"- **Primary:** new\n", "not a dated"),
            (logged, logged + b"\n", "not a dated"),
            (logged, logged.replace(b"q\n", b"x\n"), "edited"),
            (unlogged, unlogged + b"- 2026-01-05: late\n", "outside a deviation log"),
            (logged.rstrip(b"\n"), logged + b"- 2026-01-05: e\n", "own line"),
        )
        for registered, bundled, phrase in cases:
            with self.subTest(bundled=bundled):
                problem = EVIDENCE._plan_amendment_problem(registered, bundled)
                if phrase is None:
                    self.assertIsNone(problem)
                else:
                    self.assertIn(phrase, problem or "")

    def test_synonymous_effort_and_surface_claims_need_observations(self):
        def drop_init(row):
            if row["arm"] == "harness":
                row["init_surface_source"] = None
        for claim, phrase in (("Observed ratio at matched reasoning level", "reasoning effort"),
                              ("Observed ratio with extended thinking", "reasoning effort")):
            with self.subTest(claim=claim):
                self._set_card_claim(claim)
                result = EVIDENCE.verify(self.root)
                self.assertFalse(result["cards"][0]["verify_status"])
                self.assertTrue(any(phrase in error for error in result["errors"]), result["errors"])
        self._mutate_rows(drop_init)
        for claim in ("Observed ratio with the same skills loaded", "A like-for-like ratio"):
            with self.subTest(claim=claim):
                self._set_card_claim(claim)
                result = EVIDENCE.verify(self.root)
                self.assertFalse(result["cards"][0]["verify_status"])
                self.assertTrue(any("loaded runtime surface" in error for error in result["errors"]),
                                result["errors"])

    def test_parity_claim_compares_bare_against_harness(self):
        self._set_card_claim("Observed ratio at surface parity")
        result = EVIDENCE.verify(self.root)
        self.assertEqual({"status": "differs"}, result["derived"]["surface_parity"])
        self.assertFalse(result["cards"][0]["verify_status"])
        self.assertTrue(any("parity the arms' observed surfaces do not show" in error
                            for error in result["errors"]), result["errors"])

        def same_surface(row):
            for field in EVIDENCE.SURFACE_FIELDS:
                row[field] = 0
            for field in EVIDENCE.SURFACE_HASH_FIELDS:
                row[field] = hashlib.sha256(field.encode()).hexdigest()
        self._mutate_rows(same_surface)
        result = EVIDENCE.verify(self.root)
        self.assertEqual({"status": "equal"}, result["derived"]["surface_parity"])
        self.assertTrue(result["ok"], result["errors"])

    def test_requested_model_must_match_design_and_observed_model_counts_fallback(self):
        self._mutate_rows(lambda row: row.update(model="claude-opus-5"))
        result = EVIDENCE.verify(self.root)
        self.assertFalse(result["checks"]["3"])
        self.assertTrue(any("model differs from the design" in error for error in result["errors"]))
        self._mutate_rows(lambda row: row.update(model="claude-sonnet-5", observed_model=None))
        result = EVIDENCE.verify(self.root)
        self.assertFalse(result["checks"]["3"])
        self.assertTrue(any("no observed model" in error for error in result["errors"]))
        first = {}

        def fall_back(row):
            row["observed_model"] = "claude-sonnet-5"
            if not first:
                first["row"] = True
                row["observed_model"] = "claude-haiku-4-5"
        self._mutate_rows(fall_back)
        result = EVIDENCE.verify(self.root)
        self.assertEqual(1, result["derived"]["fallback"]["trials"])
        self.assertEqual(0.05, result["derived"]["fallback"]["rate"])

    def test_nested_non_object_values_fail_their_items_without_a_traceback(self):
        original_index = self._index()
        audits_path = self.root / original_index["artifacts"]["audits"]["path"]
        original_audits = json.loads(audits_path.read_text())
        prices_path = self.root / original_index["artifacts"]["prices"]["path"]
        original_prices = prices_path.read_bytes()
        cases = (
            (8, lambda index, audits: audits.update(judge=[])),
            (11, lambda index, audits: audits["field_checks"].update(sample_ratio=[])),
            (8, lambda index, audits: index["items"].update({"8": "not-applicable"})),
        )
        for item, mutate in cases:
            with self.subTest(item=item):
                index, audits = copy.deepcopy(original_index), copy.deepcopy(original_audits)
                mutate(index, audits)
                self._write_json(audits_path, audits)
                index["artifacts"]["audits"]["sha256"] = self._sha(audits_path)
                self._save_index(index)
                result = EVIDENCE.verify(self.root)
                self.assertFalse(result["checks"][str(item)], result["errors"])
        self._write_json(audits_path, original_audits)
        prices = json.loads(original_prices)
        prices["models"][sorted(prices["models"])[0]] = []
        prices_path.write_text(json.dumps(prices))
        index = copy.deepcopy(original_index)
        index["artifacts"]["audits"]["sha256"] = self._sha(audits_path)
        index["artifacts"]["prices"]["sha256"] = self._sha(prices_path)
        self._save_index(index)
        result = EVIDENCE.verify(self.root)
        self.assertFalse(result["ok"])
        self.assertTrue(any("is not an object" in error or "malformed" in error
                            for error in result["errors"]), result["errors"])

if __name__ == "__main__":
    unittest.main()

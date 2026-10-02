"""Studio replay sampling, matched draft comparisons and the engine's own analysis (AH-S324)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_harness import REPO
from harness_core.studio import drafts, replay, server
from test_replay_stats import rows_for

PLAN = """# Plan

## Sample size

- **Tasks:** 2, of which 1 are long multi-turn tasks.
- **Trials per task and arm:** {trials}
- **Power calculation:** `python3 scripts/replay_power.py --have 2 1 {trials}` printed claim power 0.81.
"""


def target(kind, ref, revision, digest=None):
    return {"kind": kind, "ref": ref, "revision": revision,
            "version": "1.0.0" if kind == "release" else None,
            "draft": ref if kind == "draft" else None, "config_digest": digest}


def request(tasks=("one", "two"), repetitions=5, registration="plan.md", **changes):
    value = {"targets": [target("release", "v1.0.0", "a" * 40),
                         target("draft", "cost-pass", "b" * 40, replay.DEFAULT_CONFIG_DIGEST)],
             "model": "m", "repetitions": repetitions, "tasks": list(tasks),
             "max_budget_usd": "1", "spend_cap_usd": "200", "pre_registration": registration}
    value.update(changes)
    return replay.ReplayRequest.parse(value)


class EvidenceLabelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / "benchmarks").mkdir()
        (self.root / "benchmarks" / "tasks.json").write_text(json.dumps(
            {"schema_version": 1, "tasks": [{"id": "one"}, {"id": "two"}]}), encoding="utf-8")
        self.plan(5)

    def plan(self, trials):
        (self.root / "plan.md").write_text(PLAN.format(trials=trials), encoding="utf-8")

    def test_the_registered_sample_is_read_through_the_protocol_readers(self):
        sample = replay.registered_sample(self.root, "plan.md")
        self.assertEqual((sample["tasks"], sample["long"], sample["trials"]), (2, 1, 5))
        self.assertIn("replay_power.py --have 2 1 5", sample["power_calculation"])
        self.assertEqual(sample["min_trials"], 5)

    def test_a_whole_set_matching_its_registration_is_pre_registered(self):
        labelled = replay.label_evidence(self.root, request())
        self.assertEqual(labelled.evidence, replay.PREREGISTERED)
        command = replay.command_for_target(labelled, labelled.targets[0], self.root, self.root / "o")
        self.assertIn("--pre-registration", command)
        self.assertNotIn("--exploratory", command)
        sampling = replay.sampling_payload(self.root, labelled)
        self.assertEqual((sampling["registered"]["trials"], sampling["requested"]),
                         (5, {"tasks": 2, "trials": 5}))

    def test_a_task_subset_is_exploratory_and_says_so(self):
        labelled = replay.label_evidence(self.root, request(tasks=("one",)))
        self.assertEqual(labelled.evidence, replay.EXPLORATORY)
        for chosen in labelled.targets:
            command = replay.command_for_target(labelled, chosen, self.root, self.root / "o")
            self.assertIn("--exploratory", command)
            self.assertNotIn("--pre-registration", command)
        sampling = replay.sampling_payload(self.root, labelled)
        self.assertIsNone(sampling["registered"])
        self.assertIn("task subset", sampling["note"])
        self.assertIn("--exploratory", replay.native_commands(labelled))

    def test_a_whole_set_off_its_registered_sample_is_refused(self):
        with self.assertRaises(replay.ReplayRefusal) as refused:
            replay.label_evidence(self.root, request(repetitions=6))
        self.assertEqual(refused.exception.code, "replay_sample_unregistered")
        self.plan(3)
        with self.assertRaises(replay.ReplayRefusal) as floor:
            replay.label_evidence(self.root, request(repetitions=3))
        self.assertIn("at least 5", str(floor.exception))

    def test_the_form_cannot_choose_its_own_label(self):
        with self.assertRaisesRegex(replay.ReplayError, "unsupported fields: evidence"):
            replay.resolve_request(dict(request().as_dict(), evidence="pre-registered",
                                        targets=[{"kind": "release", "ref": "v1.0.0"},
                                                 {"kind": "draft", "ref": "d"}]),
                                   lambda kind, ref: target(kind, ref, "a" * 40))
        with self.assertRaisesRegex(replay.ReplayError, "names its pre-registration"):
            replay.ReplayRequest.parse(dict(request(registration=None,
                                                    targets=[target("draft", "x", "c" * 40),
                                                             target("draft", "y", "d" * 40)]).as_dict(),
                                            evidence="pre-registered"))


class DraftComparisonTests(unittest.TestCase):
    def current(self, revision="b" * 40, config=None):
        return {"draft": {"revision": revision}, "config": config or {}}

    def test_a_comparison_records_its_identities_and_is_fresh_until_the_draft_changes(self):
        chosen = request()
        with mock.patch.object(drafts, "read_config", return_value=self.current()):
            fresh = replay.draft_comparisons(REPO, chosen)
        self.assertEqual(len(fresh), 1)
        record = fresh[0]
        self.assertEqual((record["draft"], record["revision"], record["tasks"], record["model"],
                          record["trials"], record["base"]["ref"], record["stale"]),
                         ("cost-pass", "b" * 40, ["one", "two"], "m", 5, "v1.0.0", False))
        with mock.patch.object(drafts, "read_config", return_value=self.current("e" * 40)):
            self.assertEqual(replay.draft_comparisons(REPO, chosen)[0]["stale_reason"],
                             "the draft has a newer checkpoint")
        with mock.patch.object(drafts, "read_config", return_value=self.current(config={"a": 1})):
            self.assertEqual(replay.draft_comparisons(REPO, chosen)[0]["stale_reason"],
                             "the draft's configuration changed")
        with mock.patch.object(drafts, "read_config",
                               side_effect=drafts.DraftError("missing", "gone")):
            self.assertTrue(replay.draft_comparisons(REPO, chosen)[0]["stale"])

    def test_only_matching_task_model_trial_and_pack_identities_share_a_key(self):
        self.assertEqual(replay.comparison_key(request()), replay.comparison_key(
            request(tasks=("two", "one"))))
        for other in (request(repetitions=6), request(tasks=("one",)), request(model="n")):
            self.assertNotEqual(replay.comparison_key(request()), replay.comparison_key(other))


class EngineAnalysisParityTests(unittest.TestCase):
    """The Studio's analysis is the CLI's `summarise --json` output, value for value."""

    FIXTURES = {
        "paired": rows_for({"a": {"bare": [(True, 1.0), (False, 1.2)], "harness": [(True, 0.7), (True, 0.9)]},
                            "b": {"bare": [(True, 2.0), (True, 1.5)], "harness": [(False, 1.0), (True, 1.1)]},
                            "c": {"bare": [(False, 0.8), (True, 0.9)], "harness": [(True, 0.6), (True, 0.5)]}}),
        "zero-pass": rows_for({"a": {"bare": [(False, 1.0), (False, 1.0)], "harness": [(False, 0.5), (False, 0.5)]},
                               "b": {"bare": [(False, 1.0), (False, 1.0)], "harness": [(False, 0.5), (False, 0.5)]}}),
        "metric-null": rows_for({"a": {"bare": [(True, None), (True, 1.0)], "harness": [(None, None), (True, 0.5)]},
                                 "b": {"bare": [(True, 1.0), (True, 1.0)], "harness": [(True, 0.5), (True, 0.5)]}}),
    }

    def cli(self, path):
        done = subprocess.run([sys.executable, str(REPO / "scripts" / "cost_bench.py"), "summarise",
                               "--results", str(path), "--json"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
        return json.loads(done.stdout) if done.stdout.strip() else {"stderr": done.stderr}

    def test_studio_and_cli_report_identical_values_for_each_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = []
            for name, rows in self.FIXTURES.items():
                path = root / name / "results.jsonl"
                path.parent.mkdir()
                path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
                files.append(path)
            output = root / "replay"
            output.mkdir()
            replay.write_analysis(REPO, output, {"result_files": [str(p) for p in files]})
            studio = replay.read_analysis(output)
            for index, path in enumerate(files, 1):
                with self.subTest(fixture=path.parent.name):
                    expected = self.cli(path)
                    self.assertEqual(studio[index - 1], {"target": index, "result": expected})
        zero = studio[1]["result"]
        self.assertIsNone(zero["ratio"])
        self.assertTrue(zero["ratio_undefined"])
        self.assertIsNone(zero["arms"]["harness"]["cost_of_pass"])
        self.assertIsNotNone(studio[0]["result"]["ratio_interval"])

    def test_an_engine_refusal_is_carried_as_its_own_words(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.jsonl"
            path.write_text(json.dumps({"task": "a", "arm": "bare", "rep": 1}) + "\n", encoding="utf-8")
            result = replay.engine_analysis(REPO, path)
        self.assertIn("cost-bench", result["error"])

    def test_the_result_route_returns_the_recorded_analysis_and_comparisons(self):
        routes = {item.path for item in server.ROUTES.entries}
        self.assertIn("/api/runs/replay/result", routes)
        server.REPLAY_RESULT.validate({"schema_version": 1, "run": {"run_id": "r", "status": "succeeded"},
                                       "progress": [], "result": {"analysis": [], "comparisons": []}})


if __name__ == "__main__":
    unittest.main()

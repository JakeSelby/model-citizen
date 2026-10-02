"""The engine's analysis reaches the Studio's display unchanged (AH-S324 AC 3).

Real native rows go through `replay.execute`, which runs `cost_bench.py summarise --json` on each
target, and then through `replay.result_payload`, the result route's `result`. The analysis it
returns must equal the CLI's output for the same rows and the committed fixture
`studio/tests/fixtures/replay-analysis.json`, which `studio/tests/replay-analysis.test.ts` feeds
through the display. Regenerate the fixture with:

    python3 tests/test_studio_replay_analysis_parity.py --write
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_harness import REPO  # noqa: E402
from harness_core.studio import replay  # noqa: E402
from test_replay_stats import rows_for  # noqa: E402
from test_studio_replay import write_native_result  # noqa: E402

FIXTURE = REPO / "studio" / "tests" / "fixtures" / "replay-analysis.json"
TASKS = ("a", "b", "c")
# Target 1: a paired set with intervals. Target 2: no pass in either arm (undefined ratio), with an
# errored run and unknown costs (metric nulls).
ROWS = {
    1: rows_for({"a": {"bare": [(True, 1.0), (False, 1.2)], "harness": [(True, 0.7), (True, 0.9)]},
                 "b": {"bare": [(True, 2.0), (True, 1.5)], "harness": [(False, 1.0), (True, 1.1)]},
                 "c": {"bare": [(False, 0.8), (True, 0.9)], "harness": [(True, 0.6), (True, 0.5)]}}),
    2: rows_for({"a": {"bare": [(False, 1.0), (False, None)], "harness": [(None, None), (False, 0.5)]},
                 "b": {"bare": [(False, 1.0), (False, 1.0)], "harness": [(False, 0.5), (False, 0.5)]},
                 "c": {"bare": [(False, 1.0), (False, 1.0)], "harness": [(False, 0.5), (False, 0.5)]}}),
}


def target(ref, revision):
    return {"kind": "draft", "ref": ref, "revision": revision, "version": None, "draft": ref,
            "config_digest": replay.DEFAULT_CONFIG_DIGEST}


def run_through_studio(root):
    """`(result_payload, {target: rows path})` for the two fixture targets."""
    request = replay.ReplayRequest.parse({
        "targets": [target("first", "a" * 40), target("second", "b" * 40)], "model": "claude-test",
        "repetitions": 2, "tasks": list(TASKS), "max_budget_usd": "2", "spend_cap_usd": "200",
        "pre_registration": None})
    output = Path(root) / "run" / "replay"
    output.parent.mkdir()
    paths = {}

    def launch(command, **_kwargs):
        ref = command[command.index("--tag") + 1]
        index = 1 if ref == "a" * 40 else 2
        rows = [dict(row, tag=ref, harness_sha=ref, model="claude-test", schema_version=1)
                for row in ROWS[index]]
        write_native_result(command, rows)
        paths[index] = Path(command[command.index("--out") + 1]) / ref / replay.RESULTS_NAME
        return SimpleNamespace(returncode=0)

    summary = replay.execute(request, REPO, output, launch)
    return replay.result_payload(REPO, output.parent, request, summary), paths


def cli(path):
    done = subprocess.run([sys.executable, str(REPO / "scripts" / "cost_bench.py"), "summarise",
                           "--results", str(path), "--json"], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, check=True)
    return json.loads(done.stdout)


class AnalysisParityTests(unittest.TestCase):
    def test_the_route_returns_the_clis_analysis_and_the_committed_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, paths = run_through_studio(tmp)
            expected = [{"target": index, "result": cli(paths[index])} for index in (1, 2)]
        self.assertIsNone(result["analysis_error"])
        self.assertEqual(result["analysis"], expected)
        self.assertEqual(json.loads(FIXTURE.read_text(encoding="utf-8")), result["analysis"])
        undefined = result["analysis"][1]["result"]
        self.assertIsNone(undefined["ratio"])
        self.assertTrue(undefined["ratio_undefined"])
        self.assertIsNotNone(result["analysis"][0]["result"]["ratio_interval"])

    def test_a_run_without_a_recorded_analysis_reports_it_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _paths = run_through_studio(tmp)
            (Path(tmp) / "run" / "replay" / replay.ANALYSIS_NAME).unlink()
            summary = replay.read_summary(Path(tmp) / "run" / "replay" / replay.SUMMARY_NAME)
            request = replay.ReplayRequest.parse({
                "targets": [target("first", "a" * 40), target("second", "b" * 40)],
                "model": "claude-test", "repetitions": 2, "tasks": list(TASKS),
                "max_budget_usd": "2", "spend_cap_usd": "200", "pre_registration": None})
            missing = replay.result_payload(REPO, Path(tmp) / "run", request, summary)
        self.assertIsNone(missing["analysis"])
        self.assertEqual(missing["analysis_error"], "no engine analysis was recorded for this run")
        self.assertEqual(result["evidence"], "exploratory")


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        with tempfile.TemporaryDirectory() as tmp:
            produced, _ = run_through_studio(tmp)
        FIXTURE.write_text(json.dumps(produced["analysis"], indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
        print("wrote " + str(FIXTURE))
    else:
        unittest.main()

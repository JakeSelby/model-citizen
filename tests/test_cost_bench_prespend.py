"""A replay refused before its first paid call records a zero spend sidecar, which the Studio
reads as nothing spent; see `replay()` in scripts/cost_bench.py."""
import contextlib
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from test_cost_bench import BENCH, TASK
from harness_core.studio import replay as studio_replay

REVISION = "a" * 40


def run_tag(tmp, **patches):
    """Drive `replay_tag` as `cmd_replay` does, up to the first refusal."""
    tmp = Path(tmp)
    tasks_file = tmp / "tasks.json"
    tasks_file.write_text("{}")
    args = types.SimpleNamespace(
        tasks=str(tasks_file), model="claude-test", task=None, tmp=None, stance_cost=None,
        raw=None, change_note=None, skip_preflight=False, bucket=None, predicted_ratio=None,
        reps=1, run_cap=2.0, spend_cap=20.0, history_dir=None, allow_surface_drift=False)
    common = {"tasks": [TASK], "plan": [], "out": tmp / "target-1", "prices": {},
              "bare": {"image": "bare"}, "network": "net", "proxy": "http://proxy",
              "client_env": {}, "cli_version": "1", "protocol": {}}
    with mock.patch.object(BENCH, "tag_version", return_value="9.9.9"), \
            mock.patch.object(BENCH, "snapshot", return_value=tmp / "checkout"), \
            (mock.patch.multiple(BENCH, **patches) if patches else contextlib.nullcontext()):
        return BENCH.replay_tag(REVISION, args, common, {"harness_commit": REVISION})


class PreSpendRefusalTests(unittest.TestCase):
    def assert_refused_at_zero(self, **patches):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as refused:
                run_tag(tmp, **patches)
            native = Path(tmp) / "target-1" / REVISION
            self.assertFalse((native / BENCH.RESULTS).exists())
            spend = json.loads((native / BENCH.SPEND).read_text())
            self.assertEqual((spend["charged_spend_usd"], spend["stopped_at_cap"]), (0.0, False))
            self.assertEqual((native / BENCH.SPEND).stat().st_mode & 0o777, 0o600)
            # The Studio adapter reads that sidecar as a refusal that spent nothing.
            request = studio_replay.ReplayRequest.parse({
                "targets": [{"kind": "branch", "ref": "main", "revision": REVISION,
                             "version": None, "draft": None, "config_digest": None},
                            {"kind": "branch", "ref": "next", "revision": "b" * 40,
                             "version": None, "draft": None, "config_digest": None}],
                "model": "claude-test", "repetitions": 1, "tasks": ["demo"],
                "max_budget_usd": "2", "spend_cap_usd": "20", "pre_registration": None})
            code = 1 if isinstance(refused.exception.code, str) else refused.exception.code
            with self.assertRaisesRegex(studio_replay.ReplayError, "before any spend"):
                studio_replay._verify_target_output(request, request.targets[0], native,
                                                    code, "20")

    def test_an_arm_admission_refusal_records_zero_spend(self):
        admit = mock.Mock(side_effect=SystemExit("replay-arms: refused"))
        with mock.patch.object(BENCH.arms, "admit", admit):
            self.assert_refused_at_zero()
        admit.assert_called_once()

    def test_a_workdir_probe_refusal_records_zero_spend(self):
        probe = mock.Mock(side_effect=SystemExit("replay-arms: workdir not writable"))
        # Admission, the pair check and the contamination check all pass, so the probe refuses.
        with mock.patch.object(BENCH.arms, "admit", mock.Mock()), \
                mock.patch.object(BENCH.arms, "admit_pair", mock.Mock()):
            self.assert_refused_at_zero(probe_workdirs=probe,
                                        contamination_errors=mock.Mock(return_value=[]))
        probe.assert_called_once()


if __name__ == "__main__":
    unittest.main()

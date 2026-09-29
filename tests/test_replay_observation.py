"""Benchmark observation is real native hook output, isolated from everyday ledgers."""
import json
import os
import copy
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from test_cost_bench import BENCH, TASK, arm_record, options, result
from harness_core import observer


def env_values(command):
    return dict(value.split("=", 1) for index, value in enumerate(command)
                if index and command[index - 1] == "-e" and "=" in value)


class NativeLaunch:
    """A CLI launch whose native observer wrote one identifier-only event."""
    def __init__(self, output=None, collector_error=False):
        self.output = output or json.dumps(result())
        self.collector_error = collector_error
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        values = env_values(command)
        if observer.LEDGER_ENV in values:
            mount = next(value for index, value in enumerate(command)
                         if index and command[index - 1] == "-v" and value.endswith(":" + BENCH.arms.OBSERVATION_MOUNT))
            host = Path(mount.rsplit(":", 1)[0])
            target = values[observer.ERRORS_ENV] if self.collector_error else values[observer.LEDGER_ENV]
            payload = {"error": "OSError", "detail": "disk unavailable"} if self.collector_error else {
                "schema_version": 1, "event": "SessionStart", "session_id": "session",
                "profile_fingerprint": values.get(observer.PROFILE_ENV)}
            payload.update(ts="2026-09-29T00:00:00Z", runtime="claude-code")
            (host / Path(target).name).write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return types.SimpleNamespace(stdout=self.output, stderr="", returncode=0)


class DestinationTests(unittest.TestCase):
    def test_the_observer_honours_explicit_ledger_error_and_profile_destinations(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {observer.LEDGER_ENV: str(Path(tmp) / "run.jsonl"),
                   observer.ERRORS_ENV: str(Path(tmp) / "run.errors.jsonl"),
                   observer.PROFILE_ENV: "benchmark-profile"}
            with mock.patch.dict(os.environ, env, clear=False):
                code = observer.main("claude-code", [], stdin=__import__("io").StringIO(
                    json.dumps({"hook_event_name": "Stop", "session_id": "s"})))
            self.assertEqual(code, 0)
            row = json.loads(Path(env[observer.LEDGER_ENV]).read_text(encoding="utf-8"))
            self.assertEqual((row["event"], row["profile_fingerprint"]), ("Stop", "benchmark-profile"))
            self.assertFalse(Path(env[observer.ERRORS_ENV]).exists())
            with mock.patch.dict(os.environ, env, clear=False):
                observer.main("claude-code", [], stdin=__import__("io").StringIO("not json"))
            self.assertEqual(json.loads(Path(env[observer.ERRORS_ENV]).read_text(encoding="utf-8"))["runtime"],
                             "claude-code")

    def test_only_a_fresh_cost_bench_owned_directory_can_be_mounted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unsafe = root / "ordinary"
            unsafe.mkdir()
            with self.assertRaisesRegex(SystemExit, "not prepared by cost-bench"):
                BENCH.arms.run_command("image", root, ["true"], "none", observation_dir=unsafe)
            owned = BENCH.prepare_observation_dir(root / "run")
            command = BENCH.arms.run_command("image", root, ["true"], "none", observation_dir=owned)
            self.assertIn(str(owned) + ":" + BENCH.arms.OBSERVATION_MOUNT, command)
            with self.assertRaisesRegex(SystemExit, "existing observation output"):
                BENCH.prepare_observation_dir(root / "run")

    def test_marked_host_profile_output_is_never_mounted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            owned = BENCH.prepare_observation_dir(root / "profile" / "run")
            with mock.patch.object(BENCH.arms, "host_paths", return_value=[str((root / "profile").resolve())]):
                with self.assertRaisesRegex(SystemExit, "host path"):
                    BENCH.arms.run_command("image", root / "task", ["true"], "none", observation_dir=owned)

    def test_each_native_session_mounts_only_its_own_fresh_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = BENCH.prepare_observation_dir(Path(tmp) / "run")
            opts = {"observation_dir": root, "tmp": tmp}
            first = BENCH.observation_run(opts, "task-bare-1", "bare")
            second = BENCH.observation_run(opts, "task-harness-1", "fingerprint")
            self.assertNotEqual(first["mount"], second["mount"])
            self.assertNotEqual(first["mount"], root)
            self.assertFalse((second["mount"] / first["ledger"].name).exists())
            self.assertEqual(first["ledger"].parent, first["mount"])
            with self.assertRaisesRegex(SystemExit, "repeated observation session"):
                BENCH.observation_run(opts, "task-bare-1", "bare")

    def test_native_settings_are_part_of_each_arm_declaration(self):
        inputs = {"base_image": "base@sha256:" + "0" * 64, "claude_code_version": "1.2.3"}
        before = BENCH.arms.declaration("bare", inputs)
        with mock.patch.object(BENCH.arms, "observer_settings", return_value={"hooks": {}}):
            after = BENCH.arms.declaration("bare", inputs)
        self.assertNotEqual(BENCH.arms.digest(before), BENCH.arms.digest(after))
        record = copy.deepcopy(arm_record("bare"))
        record["declaration"]["observer_settings_sha256"] = "0" * 64
        record["declaration_sha256"] = BENCH.arms.digest(record["declaration"])
        with self.assertRaisesRegex(SystemExit, "observer hook settings"):
            BENCH.arms.admit(dict(record, protocol={"evidence": "exploratory"}))

    def test_launch_refuses_settings_that_changed_after_declaration(self):
        record = arm_record("bare")
        with mock.patch.object(BENCH, "ARM_SETTINGS", {"hooks": {}}):
            with self.assertRaisesRegex(SystemExit, "settings differ"):
                BENCH.launch_arm(record, None, ["true"], {}, "test")

    def test_both_images_declare_and_install_the_same_exact_observer(self):
        inputs = {"base_image": "base@sha256:" + "0" * 64, "claude_code_version": "1.2.3"}
        bare = BENCH.arms.declaration("bare", inputs)
        harness = BENCH.arms.declaration("harness", inputs, {"ref": "v1", "commit": "c" * 40})
        expected = "sha256:" + BENCH.arms.file_sha(BENCH.arms.OBSERVER_SOURCE)
        self.assertEqual([c for c in bare["components"] if c["name"] == "model-citizen-observer"],
                         [{"name": "model-citizen-observer", "version": expected}])
        self.assertEqual(bare["components"][-1], harness["components"][-2])
        with tempfile.TemporaryDirectory() as tmp:
            context = BENCH.arms.build_context(bare, tmp, lambda *args: None)
            self.assertEqual((context / "observer" / "observe.py").read_bytes(),
                             BENCH.arms.OBSERVER_SOURCE.read_bytes())

    def test_admission_recomputes_the_installed_observer_hash(self):
        record = copy.deepcopy(arm_record("bare"))
        observer_entry = next(e for e in record["manifest"]["entries"]
                              if e["path"] == "observer:observe.py")
        observer_entry["sha256"] = "0" * 64
        record["manifest_sha256"] = BENCH.arms.digest(record["manifest"])
        with self.assertRaisesRegex(SystemExit, "installed observer"):
            BENCH.arms.admit(dict(record, protocol={"evidence": "exploratory"}))

    def test_admission_refuses_an_undeclared_file_beside_the_observer(self):
        record = copy.deepcopy(arm_record("bare"))
        record["manifest"]["entries"].append({"path": "observer:answer.py", "kind": "file",
                                                "sha256": "0" * 64})
        record["manifest_sha256"] = BENCH.arms.digest(record["manifest"])
        with self.assertRaisesRegex(SystemExit, "only its declared entry point"):
            BENCH.arms.admit(dict(record, protocol={"evidence": "exploratory"}))


class ReplayCollectionTests(unittest.TestCase):
    def run_attempt(self, root, launch):
        opts = options(root, reps=1)
        run_root = Path(root) / "output"
        run_root.mkdir()
        opts["observation_dir"] = BENCH.prepare_observation_dir(run_root)
        return BENCH._attempt(TASK, 1, "bare", opts, launch), opts

    def test_a_real_native_row_is_mounted_and_counted_without_host_profile_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = NativeLaunch()
            row, opts = self.run_attempt(tmp, launch)
            self.assertEqual((row["observation_rows"], row["observation_errors"], row["error"]),
                             (1, 0, False))
            self.assertEqual(row["observation_ledger"], "observations/demo-bare-1.jsonl")
            ledger = Path(opts["observation_dir"]) / "demo-bare-1.jsonl"
            self.assertEqual(json.loads(ledger.read_text(encoding="utf-8"))["event"], "SessionStart")
            self.assertNotEqual(ledger, observer.ledger_path())
            command = launch.calls[0][0]
            values = env_values(command)
            self.assertTrue(values[observer.LEDGER_ENV].startswith(BENCH.arms.OBSERVATION_MOUNT + "/"))
            self.assertNotIn(str(Path.home()), " ".join(command))

    def test_malformed_main_stream_does_not_erase_valid_collector_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = BENCH.prepare_observation_dir(Path(tmp) / "run")
            run = BENCH.observation_run({"observation_dir": root, "tmp": tmp}, "one", "bare")
            run["ledger"].write_text("{}\n")
            run["errors"].write_text(json.dumps({"ts": "2026-09-29T00:00:00Z",
                "runtime": "claude-code", "error": "OSError", "detail": "failed"}) + "\n")
            fields, error = BENCH.observation_result(run)
            self.assertIsNone(fields["observation_rows"])
            self.assertEqual(fields["observation_errors"], 1)
            self.assertIn("collector error", error)
            self.assertEqual((root / "one.jsonl").read_text(), "{}\n")

    def test_collector_symlinks_are_not_followed_by_the_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = BENCH.prepare_observation_dir(Path(tmp) / "run")
            run = BENCH.observation_run({"observation_dir": root, "tmp": tmp}, "one", "bare")
            private = Path(tmp) / "private"
            private.write_text("private contents")
            run["ledger"].unlink()
            run["ledger"].symlink_to(private)
            fields, error = BENCH.observation_result(run)
            self.assertIsNone(fields["observation_rows"])
            self.assertIn("unreadable ledger", error)
            self.assertFalse((root / "one.jsonl").exists())

    def test_missing_or_errored_collection_makes_the_attempt_an_error_without_losing_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            row, _ = self.run_attempt(tmp, NativeLaunch(collector_error=True))
            self.assertTrue(row["error"])
            self.assertEqual((row["observation_rows"], row["observation_errors"], row["cost_usd"]),
                             (0, 1, 0.5))
            self.assertIn("collector error", row["error_kind"])


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: MIT
"""The Studio release lifecycle is executable and records only completed checks."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_harness import REPO


def load():
    path = REPO / "scripts" / "studio_lifecycle_acceptance.py"
    spec = importlib.util.spec_from_file_location("studio_lifecycle_acceptance", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load()


class StudioLifecycleAcceptanceTests(unittest.TestCase):
    def test_real_lifecycle_loads_ui_refuses_host_and_token_and_stops(self):
        original = MODULE.command

        def allow_dirty_git(*args, **kwargs):
            if tuple(args[-4:]) == ("-C", str(REPO), "status", "--porcelain"):
                return subprocess.CompletedProcess(args, 0, "", "")
            return original(*args, **kwargs)

        with patch.object(MODULE, "command", side_effect=allow_dirty_git):
            cases = MODULE.lifecycle(REPO)
        self.assertEqual([item["name"] for item in cases], [
            "loopback-only-bind", "host-refusal", "token-refusal",
            "authenticated-ui-load", "clean-stop"])
        self.assertEqual({item["status"] for item in cases}, {"passed"})

    def test_record_names_exact_commit_platform_browser_and_cases(self):
        cases = [{"name": "clean-stop", "status": "passed"}]
        with patch.object(MODULE, "lifecycle", return_value=cases), \
                patch.object(MODULE, "supported_tuple", return_value={
                    "browser": "chrome", "version_policy": "stable-channel",
                    "stable_major": 140}), \
                patch.object(MODULE, "chrome", return_value="/fixture/chrome"), \
                patch.object(MODULE, "command",
                             return_value=subprocess.CompletedProcess([], 0, "a" * 40 + "\n", "")):
            record = MODULE.run(REPO, browser_runner=lambda *_args: "Google Chrome 140.0.0.0")
        self.assertEqual(record["schema_version"], 1)
        self.assertEqual(record["candidate_commit"], "a" * 40)
        self.assertEqual(record["browser"], {
            "family": "chrome", "version": "Google Chrome 140.0.0.0",
            "version_policy": "stable-channel"})
        self.assertEqual(record["cases"][-1], {"name": "chrome-browser-flow", "status": "passed"})
        self.assertIn(record["environment"]["platform"], ("darwin", "linux"))

    def test_main_writes_the_same_record_it_prints(self):
        record = {"schema_version": 1, "cases": [{"name": "clean-stop", "status": "passed"}]}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "record.json"
            with patch.object(MODULE, "run", return_value=record):
                self.assertEqual(MODULE.main(["--output", str(output)]), 0)
            self.assertEqual(json.loads(output.read_text()), record)

    def test_release_evidence_requires_every_case_on_both_platforms(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "compatibility/evidence").mkdir(parents=True)
            (root / "VERSION").write_text("1.2.3\n")
            supported = []
            for platform_name, observed in (("macos", "darwin"), ("linux", "linux")):
                template = "compatibility/evidence/studio-{platform}-{version}.json"
                supported.append({"platform": platform_name, "browser": "chrome",
                                  "version_policy": "stable-channel",
                                  "stable_major": 140,
                                  "release_evidence": template})
                record = {
                    "schema_version": 1, "candidate_commit": "a" * 40,
                    "environment": {"platform": observed},
                    "browser": {"family": "chrome", "version": "Google Chrome 140.0.0.0",
                                "version_policy": "stable-channel"},
                    "cases": [{"name": name, "status": "passed"}
                              for name in sorted(MODULE.REQUIRED_CASES)],
                }
                path = root / template.format(platform=platform_name, version="1.2.3")
                path.write_text(json.dumps(record))
            (root / "compatibility/studio.json").write_text(json.dumps(
                {"schema_version": 1, "supported": supported}))
            self.assertEqual(MODULE.evidence_errors(root, "a" * 40), [])
            first = root / supported[0]["release_evidence"].format(
                platform="macos", version="1.2.3")
            broken = json.loads(first.read_text())
            broken["cases"].pop()
            first.write_text(json.dumps(broken))
            self.assertTrue(any("cases did not pass" in item
                                for item in MODULE.evidence_errors(root, "a" * 40)))

    def test_release_evidence_rejects_failed_skipped_duplicate_and_unknown_cases(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "compatibility/evidence").mkdir(parents=True)
            (root / "VERSION").write_text("1.2.3\n")
            support = {"platform": "linux", "browser": "chrome",
                       "version_policy": "stable-channel",
                       "stable_major": 140,
                       "release_evidence": "compatibility/evidence/studio-{platform}-{version}.json"}
            (root / "compatibility/studio.json").write_text(json.dumps(
                {"schema_version": 1, "supported": [support]}))
            path = root / support["release_evidence"].format(platform="linux", version="1.2.3")
            base = {
                "schema_version": 1, "candidate_commit": "a" * 40,
                "environment": {"platform": "linux"},
                "browser": {"family": "chrome", "version": "Google Chrome 140.0.0.0",
                            "version_policy": "stable-channel"},
                "cases": [{"name": name, "status": "passed"}
                          for name in sorted(MODULE.REQUIRED_CASES)],
            }
            corruptions = {
                "failed": lambda data: data["cases"].__setitem__(0, {
                    "name": data["cases"][0]["name"], "status": "failed"}),
                "skipped": lambda data: data["cases"].__setitem__(0, {
                    "name": data["cases"][0]["name"], "status": "skipped"}),
                "duplicate": lambda data: data["cases"].append(dict(data["cases"][0])),
                "unknown": lambda data: data["cases"].append({"name": "invented", "status": "passed"}),
            }
            for name, corrupt in corruptions.items():
                with self.subTest(name=name):
                    data = json.loads(json.dumps(base))
                    corrupt(data)
                    path.write_text(json.dumps(data))
                    self.assertNotEqual(MODULE.evidence_errors(root, "a" * 40), [])

    def test_cleanup_starts_for_malformed_launch_json_and_state(self):
        for malformed in ("launch-json", "state-json"):
            with self.subTest(malformed=malformed):
                calls = []

                def fake_command(*args, **kwargs):
                    calls.append(args)
                    if args[0] == "git":
                        return subprocess.CompletedProcess(args, 0, "", "")
                    if "--detach" in args:
                        output = "not-json" if malformed == "launch-json" else '{"pid":43210}\n'
                        return subprocess.CompletedProcess(args, 0, output, "")
                    if "stop" in args:
                        return subprocess.CompletedProcess(args, 0, '{"stopped":true}\n', "")
                    if "status" in args:
                        return subprocess.CompletedProcess(args, 1, '{"running":false}\n', "")
                    raise AssertionError(args)

                def parse(started, state_path):
                    if malformed == "state-json":
                        state_path.parent.mkdir(parents=True)
                        state_path.write_text("not-json")
                    return MODULE.json.loads(started.stdout), MODULE.json.loads(state_path.read_text())

                with patch.object(MODULE, "command", side_effect=fake_command), \
                        patch.object(MODULE, "launch_record", side_effect=parse):
                    with self.assertRaises((json.JSONDecodeError, AssertionError)):
                        MODULE.lifecycle(REPO)
                self.assertTrue(any("stop" in args for args in calls), calls)

    def test_browser_flow_rejects_zero_tests_missing_cases_and_chromium(self):
        expected = list(MODULE.BROWSER_SUITES.values())
        complete = []
        for names in expected:
            complete.append("\n".join(
                [name + " (fixture) ... ok" for name in names]
                + ["Ran {} tests in 0.1s".format(len(names)), "OK"]))

        def run_with(outputs, version="Google Chrome 153.0.0.0"):
            # The version is read first, so a wrong browser fails before ten minutes of suites.
            read = subprocess.CompletedProcess([], 0, version + "\n", "")
            with patch.object(MODULE, "command", return_value=read), \
                    patch.object(MODULE, "run_suite", side_effect=list(outputs)), \
                    patch.object(MODULE, "supported_tuple", return_value={"stable_major": 153}):
                return MODULE.browser_flow(REPO, os.sys.executable, "/fixture/chrome")

        with self.assertRaisesRegex(AssertionError, "discovered no complete suite"):
            run_with(["Ran 0 tests in 0.0s\nOK", complete[1]])
        missing = complete[0].replace(expected[0][0], "another_test")
        with self.assertRaisesRegex(AssertionError, "did not run"):
            run_with([missing, complete[1]])
        with self.assertRaisesRegex(AssertionError, "stable channel"):
            run_with(complete, version="Chromium 140.0.0.0")
        with self.assertRaisesRegex(AssertionError, "pinned stable Chrome major 153"):
            run_with(complete, version="Google Chrome 1.2.3.4")


if __name__ == "__main__":
    unittest.main()

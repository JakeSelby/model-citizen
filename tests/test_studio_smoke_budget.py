# SPDX-License-Identifier: MIT
"""The Studio qualification's time bounds, browser choice and hosted-image record hold together."""
import inspect
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_harness import REPO
from test_studio_lifecycle_acceptance import MODULE

sys.path.insert(0, str(REPO / "scripts"))
import smoke_tier


def studio_step():
    with tempfile.TemporaryDirectory() as work:
        return next(step for step in smoke_tier.steps(Path(work)) if step["name"] == "studio-lifecycle")


def suite_output(pattern):
    names = MODULE.BROWSER_SUITES[pattern]
    return "\n".join([name + " (fixture) ... ok" for name in names]
                     + ["Ran {} tests in 0.1s".format(len(names)), "OK"])


class StudioQualificationBudgetTests(unittest.TestCase):
    def test_smoke_step_outlasts_startup_every_suite_budget_and_a_margin(self):
        floor = (MODULE.STARTUP_TIMEOUT + MODULE.LIFECYCLE_OVERHEAD
                 + MODULE.BROWSER_SUITE_TIMEOUT * len(MODULE.BROWSER_SUITES))
        self.assertEqual(MODULE.worst_case_seconds(), floor)
        self.assertGreaterEqual(studio_step()["timeout"], floor + smoke_tier.STUDIO_MARGIN)
        self.assertGreater(smoke_tier.STUDIO_MARGIN, 0)

    def test_each_suite_gets_the_whole_suite_budget_sized_for_the_slowest_suite(self):
        # The module editor's 19 rendered flows ran 383 s on a loaded Mac; 300 s timed it out.
        self.assertGreaterEqual(MODULE.BROWSER_SUITE_TIMEOUT, 900)
        default = inspect.signature(MODULE.run_suite).parameters["timeout"].default
        self.assertEqual(default, MODULE.BROWSER_SUITE_TIMEOUT)
        calls = []

        def run_suite(python, pattern, env, **kwargs):
            calls.append((pattern, kwargs, env.get(MODULE.CHROME_ENV)))
            return suite_output(pattern)

        read = subprocess.CompletedProcess([], 0, "Google Chrome 154.0.1.2\n", "")
        with patch.object(MODULE, "command", return_value=read), \
                patch.object(MODULE, "run_suite", side_effect=run_suite), \
                patch.object(MODULE, "supported_tuple", return_value={"stable_major": 154}):
            version = MODULE.browser_flow(REPO, sys.executable, "/fixture/chrome")
        self.assertEqual(version, "Google Chrome 154.0.1.2")
        self.assertEqual([pattern for pattern, _, _ in calls], list(MODULE.BROWSER_SUITES))
        self.assertTrue(all(kwargs == {} for _, kwargs, _ in calls))
        # The suites drive the very binary whose version the record carries.
        self.assertEqual({chosen for _, _, chosen in calls}, {"/fixture/chrome"})

    def test_a_wrong_major_is_refused_before_any_suite_runs(self):
        read = subprocess.CompletedProcess([], 0, "Google Chrome 152.0.1.2\n", "")
        with patch.object(MODULE, "command", return_value=read), \
                patch.object(MODULE, "run_suite") as run_suite, \
                patch.object(MODULE, "supported_tuple", return_value={"stable_major": 154}):
            with self.assertRaisesRegex(AssertionError, "pinned stable Chrome major 154"):
                MODULE.browser_flow(REPO, sys.executable, "/fixture/chrome")
        run_suite.assert_not_called()

    def test_an_overrunning_suite_is_killed_with_the_browser_it_started(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "child.pid"
            fake = Path(temporary) / "python"
            # Stands in for the interpreter: starts a long-lived child, as a suite starts Chrome.
            fake.write_text("#!/bin/sh\nsleep 300 &\necho $! > '{}'\nwait\n".format(marker))
            fake.chmod(0o755)
            started = time.monotonic()
            with self.assertRaisesRegex(AssertionError, "exceeded 1 seconds"):
                MODULE.run_suite(str(fake), "test_never.py", dict(os.environ), timeout=1)
            self.assertLess(time.monotonic() - started, 30)
            child = int(marker.read_text())
            for _ in range(50):
                try:
                    os.kill(child, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)
            else:
                self.fail("the suite's child survived the timeout")

    def test_hosted_image_chrome_is_recorded_and_validated(self):
        with patch.dict(os.environ, {MODULE.IMAGE_CHROME_ENV: "Google Chrome 152.0.7977.83"}):
            self.assertEqual(MODULE.image_chrome(), "Google Chrome 152.0.7977.83")
        with patch.dict(os.environ, {MODULE.IMAGE_CHROME_ENV: "Chromium 152.0.0.0"}):
            with self.assertRaisesRegex(AssertionError, "not a Google Chrome version"):
                MODULE.image_chrome()
        with patch.dict(os.environ, {MODULE.IMAGE_CHROME_ENV: ""}):
            self.assertIsNone(MODULE.image_chrome())

    def test_preflight_refuses_a_malformed_hosted_image_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "compatibility" / "evidence").mkdir(parents=True)
            (root / "VERSION").write_text("9.9.9\n")
            (root / "compatibility" / "studio.json").write_text(json.dumps({
                "schema_version": 1,
                "supported": [{"platform": "macos", "browser": "chrome",
                               "version_policy": "stable-channel", "stable_major": 154,
                               "release_evidence":
                                   "compatibility/evidence/studio-{platform}-{version}.json"}]}))
            record = {"schema_version": 1, "candidate_commit": "a" * 40,
                      "environment": {"platform": "darwin"},
                      "browser": {"family": "chrome", "version": "Google Chrome 154.0.1.2",
                                  "version_policy": "stable-channel",
                                  "image_version": "Google Chrome 152.0.7977.83"},
                      "cases": [{"name": name, "status": "passed"}
                                for name in sorted(MODULE.REQUIRED_CASES)]}
            path = root / "compatibility" / "evidence" / "studio-macos-9.9.9.json"
            path.write_text(json.dumps(record))
            self.assertEqual(MODULE.evidence_errors(root, "a" * 40), [])
            record["browser"]["image_version"] = "152"
            path.write_text(json.dumps(record))
            self.assertEqual(MODULE.evidence_errors(root, "a" * 40),
                             ["compatibility/evidence/studio-macos-9.9.9.json: "
                              "hosted image Chrome version is malformed"])
            # A record from any other commit than the frozen source is refused.
            record["browser"].pop("image_version")
            path.write_text(json.dumps(record))
            self.assertIn("candidate commit does not match qualification source",
                          " ".join(MODULE.evidence_errors(root, "b" * 40)))

    def test_rendered_suites_honour_the_qualification_chrome(self):
        import test_studio_browser
        with tempfile.TemporaryDirectory() as temporary:
            chosen = Path(temporary) / "chrome"
            chosen.write_text("")
            with patch.dict(os.environ, {MODULE.CHROME_ENV: str(chosen)}):
                self.assertEqual(test_studio_browser._chrome(), str(chosen))
            with patch.dict(os.environ, {MODULE.CHROME_ENV: str(chosen) + "-missing"}):
                self.assertIsNone(test_studio_browser._chrome())

    def test_hosted_macos_installs_signed_current_stable_before_qualifying(self):
        workflow = (REPO / ".github/workflows/studio-qualification.yml").read_text()
        install = workflow.index("Install current stable Chrome on macOS")
        qualify = workflow.index("scripts/studio_lifecycle_acceptance.py")
        self.assertLess(install, qualify)
        step = workflow[install:qualify]
        self.assertIn("if: runner.os == 'macOS'", step)
        self.assertIn(MODULE.IMAGE_CHROME_ENV + "=", step)
        self.assertIn("https://dl.google.com/chrome/mac/universal/stable/", step)
        self.assertIn("codesign --verify --deep --strict", step)
        self.assertIn("TeamIdentifier=EQHXZ8M8AV", step)
        self.assertLess(step.index(MODULE.IMAGE_CHROME_ENV), step.index("curl"))

    def test_release_docs_regenerate_studio_records_at_the_frozen_source(self):
        releasing = " ".join((REPO / "docs/releasing.md").read_text().split())
        self.assertIn("Generate these records only once `VERSION` names the release", releasing)
        self.assertIn("`browser.image_version`", releasing)


if __name__ == "__main__":
    unittest.main()

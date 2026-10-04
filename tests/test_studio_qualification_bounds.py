# SPDX-License-Identifier: MIT
"""The Studio qualification's time bounds, process cleanup, browser choice and records hold."""
import importlib.util
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_harness import REPO
from test_studio_lifecycle_acceptance import MODULE

sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "lib"))
import smoke_tier
from harness_core.studio import lifecycle as studio_lifecycle


def load_round():
    path = REPO / "scripts" / "qualification_round.py"
    spec = importlib.util.spec_from_file_location("qualification_round_bounds", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROUND = load_round()


def studio_step():
    with tempfile.TemporaryDirectory() as work:
        return next(step for step in smoke_tier.steps(Path(work)) if step["name"] == "studio-lifecycle")


def suite_output(pattern):
    names = MODULE.BROWSER_SUITES[pattern]
    return "\n".join([name + " (fixture) ... ok" for name in names]
                     + ["Ran {} tests in 0.1s".format(len(names)), "OK"])


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A killed child of this process lingers as a zombie until reaped.
    try:
        reaped, _status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return True
    return reaped == 0


def wait_dead(test, pid, what):
    for _ in range(100):
        if not alive(pid):
            return
        time.sleep(0.1)
    test.fail(what + " survived the kill")


def detached_sleeper():
    """A process in its own session, as a detached Studio server is."""
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"],
                               start_new_session=True)
    return process


def fake_python(directory, marker):
    """Stands in for the suite's interpreter: starts a long-lived child, as a suite starts Chrome,
    and copies the tracking directory's listing so the test can see the suite was tracked."""
    fake = Path(directory) / "python"
    fake.write_text("#!/bin/sh\nsleep 300 &\necho $! > '{0}'\nls \"$HARNESS_STUDIO_TRACK_DIR\" "
                    "> '{0}.tracked'\nwait\n".format(marker))
    fake.chmod(0o755)
    return fake


class SuiteBudgetTests(unittest.TestCase):
    def test_each_suite_budget_is_proportional_to_the_cases_it_runs(self):
        loader = unittest.defaultTestLoader
        for pattern in MODULE.BROWSER_SUITES:
            discovered = loader.discover(str(REPO / "tests"), pattern=pattern).countTestCases()
            self.assertEqual(MODULE.suite_cases(pattern), discovered, pattern)
            self.assertEqual(MODULE.suite_timeout(pattern),
                             MODULE.SUITE_BASE_SECONDS + MODULE.SUITE_CASE_SECONDS * discovered)
        # The module editor's 19 cases ran 383 s on a loaded Mac; its budget keeps 2x headroom.
        self.assertGreaterEqual(MODULE.suite_timeout("test_studio_module_editor_browser.py"),
                                2 * 383)
        self.assertLess(MODULE.suite_timeout("test_studio_browser.py"), 300)

    def test_browser_flow_gives_each_suite_its_own_budget_and_the_observed_chrome(self):
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
        # No override: run_suite sizes each suite itself.
        self.assertTrue(all(kwargs == {} for _, kwargs, _ in calls))
        self.assertEqual({chosen for _, _, chosen in calls}, {"/fixture/chrome"})


class TierAndRoundBudgetTests(unittest.TestCase):
    def test_smoke_step_outlasts_startup_every_suite_budget_and_a_margin(self):
        floor = (MODULE.STARTUP_TIMEOUT + MODULE.LIFECYCLE_OVERHEAD
                 + sum(MODULE.suite_timeout(pattern) for pattern in MODULE.BROWSER_SUITES))
        self.assertEqual(MODULE.worst_case_seconds(), floor)
        self.assertGreater(smoke_tier.STUDIO_MARGIN, 0)
        self.assertGreaterEqual(studio_step()["timeout"], floor + smoke_tier.STUDIO_MARGIN)

    def test_round_deadline_covers_every_smoke_step_running_to_its_limit(self):
        with tempfile.TemporaryDirectory() as work:
            budgets = [step["timeout"] for step in smoke_tier.steps(Path(work))]
        self.assertEqual(smoke_tier.total_seconds(), sum(budgets))
        self.assertGreaterEqual(ROUND.ROUND_TIMEOUT, sum(budgets) + ROUND.ROUND_MARGIN)
        self.assertGreaterEqual(ROUND.ROUND_TIMEOUT, ROUND.TARGET_TIMEOUT)

    def test_lifecycle_overhead_covers_its_bounds_and_a_measured_lifecycle(self):
        self.assertGreaterEqual(MODULE.LIFECYCLE_OVERHEAD, MODULE.LIFECYCLE_BOUNDS)
        original = MODULE.command

        def allow_dirty_git(*args, **kwargs):
            if tuple(args[-4:]) == ("-C", str(REPO), "status", "--porcelain"):
                return subprocess.CompletedProcess(args, 0, "", "")
            return original(*args, **kwargs)

        started = time.monotonic()
        with patch.object(MODULE, "command", side_effect=allow_dirty_git):
            cases = MODULE.lifecycle(REPO)
        measured = time.monotonic() - started
        self.assertEqual({item["status"] for item in cases}, {"passed"})
        # A real start, probe and stop on this host fits inside what the step budgets for it.
        self.assertLess(measured, MODULE.STARTUP_TIMEOUT + MODULE.LIFECYCLE_OVERHEAD)


class ProcessCleanupTests(unittest.TestCase):
    def test_an_overrunning_suite_is_killed_with_its_browser_and_tracked_servers(self):
        with tempfile.TemporaryDirectory() as temporary:
            tracked = Path(temporary) / "tracked"
            tracked.mkdir()
            server = detached_sleeper()
            self.addCleanup(lambda: server.poll() is None and server.kill())
            MODULE.track(server.pid, str(tracked))
            marker = Path(temporary) / "child.pid"
            fake = fake_python(temporary, marker)
            env = dict(os.environ, **{MODULE.TRACK_ENV: str(tracked)})
            started = time.monotonic()
            with self.assertRaisesRegex(AssertionError, "exceeded 1 seconds"):
                MODULE.run_suite(str(fake), "test_never.py", env, timeout=1)
            self.assertLess(time.monotonic() - started, 30)
            wait_dead(self, int(marker.read_text()), "the suite's browser")
            wait_dead(self, server.pid, "the detached server")
            self.assertEqual(list(tracked.iterdir()), [])

    def test_a_running_suite_is_tracked_so_a_caller_can_kill_its_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            tracked = Path(temporary) / "tracked"
            tracked.mkdir()
            marker = Path(temporary) / "child.pid"
            fake = fake_python(temporary, marker)
            env = dict(os.environ, **{MODULE.TRACK_ENV: str(tracked)})
            with self.assertRaises(AssertionError):
                MODULE.run_suite(str(fake), "test_never.py", env, timeout=1)
            listed = Path(str(marker) + ".tracked").read_text().split()
            self.assertEqual(len(listed), 1)
            self.assertTrue(listed[0].isdigit())

    def test_kill_tracked_takes_a_group_leader_with_its_group(self):
        leader = subprocess.Popen(["/bin/sh", "-c", "sleep 300 & echo $!; wait"],
                                  stdout=subprocess.PIPE, text=True, start_new_session=True)
        self.addCleanup(lambda: leader.poll() is None and leader.kill())
        grandchild = int(leader.stdout.readline())
        with tempfile.TemporaryDirectory() as tracked:
            MODULE.track(leader.pid, tracked)
            MODULE.kill_tracked(tracked)
            leader.wait(timeout=10)
            wait_dead(self, grandchild, "the leader's group member")
            self.assertEqual(os.listdir(tracked), [])

    def test_a_tracked_smoke_step_kills_its_detached_servers_after_a_timeout(self):
        server = detached_sleeper()
        self.addCleanup(lambda: server.poll() is None and server.kill())
        script = ("import os, pathlib, time\n"
                  "pathlib.Path(os.environ['HARNESS_STUDIO_TRACK_DIR'], '%d').write_text('')\n"
                  "time.sleep(300)\n" % server.pid)
        step = {"name": "studio-lifecycle", "how": "fixture", "timeout": 2, "track": True,
                "argv": [sys.executable, "-c", script]}
        env = {key: value for key, value in os.environ.items() if key != MODULE.TRACK_ENV}
        with patch.dict(os.environ, env, clear=True):
            result = smoke_tier.run_step(step, REPO)
        self.assertEqual(result["result"], "unverified")
        wait_dead(self, server.pid, "the detached server")

    def test_a_round_that_outlives_its_deadline_kills_the_tier_and_its_servers(self):
        server = detached_sleeper()
        self.addCleanup(lambda: server.poll() is None and server.kill())
        with tempfile.TemporaryDirectory() as clone:
            marker = Path(clone) / "child.pid"
            (Path(clone) / "scripts").mkdir()
            (Path(clone) / "scripts" / "smoke_tier.py").write_text(
                "import os, pathlib, subprocess, time\n"
                "tracked = os.environ['HARNESS_STUDIO_TRACK_DIR']\n"
                "pathlib.Path(tracked, '%d').write_text('')\n"
                # The real tier names its own group, as smoke_tier.main does.
                "pathlib.Path(tracked, str(os.getpid())).write_text('')\n"
                "child = subprocess.Popen(['sleep', '300'])\n"
                "pathlib.Path(%r).write_text(str(child.pid))\n"
                "time.sleep(300)\n" % (server.pid, str(marker)))
            with patch.object(ROUND, "ROUND_TIMEOUT", 3):
                finished = ROUND.smoke(clone, dict(os.environ))
            self.assertIsNone(finished)
            wait_dead(self, int(marker.read_text()), "the tier's child")
            wait_dead(self, server.pid, "the detached server")

    def test_the_tier_names_its_own_group_and_each_running_step_to_its_supervisor(self):
        with tempfile.TemporaryDirectory() as tracked, \
                patch.dict(os.environ, {MODULE.TRACK_ENV: tracked}), \
                redirect_stdout(io.StringIO()):
            smoke_tier.main(["--list"])
            self.assertEqual(os.listdir(tracked), [str(os.getpid())])
            MODULE.untrack(os.getpid(), tracked)
            listing = os.path.join(tracked, "seen")
            step = {"name": "fixture", "how": "fixture", "timeout": 30,
                    "argv": [sys.executable, "-c",
                             "import os; open(%r, 'w').write(' '.join(os.listdir(%r)))"
                             % (listing, tracked)]}
            self.assertEqual(smoke_tier.run_step(step, REPO)["result"], "passed")
            seen = [name for name in Path(listing).read_text().split() if name.isdigit()]
            self.assertEqual(len(seen), 1)
            # A finished untracked-kind step is forgotten, never killed later by number.
            self.assertEqual(os.listdir(tracked), ["seen"])

    def test_a_round_smoke_reports_the_tier_exit_code(self):
        with tempfile.TemporaryDirectory() as clone:
            (Path(clone) / "scripts").mkdir()
            (Path(clone) / "scripts" / "smoke_tier.py").write_text("raise SystemExit(3)\n")
            self.assertEqual(ROUND.smoke(clone, dict(os.environ)).returncode, 3)

    def test_a_detached_studio_server_names_itself_and_forgets_itself_on_stop(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(os.path.realpath(temporary)) / "home"
            home.mkdir()
            tracked = Path(temporary) / "tracked"
            tracked.mkdir()
            env = {key: value for key, value in os.environ.items() if key != "HARNESS_QUIET"}
            env.update(HOME=str(home), HARNESS_HOME=str(home), PYTHONDONTWRITEBYTECODE="1",
                       **{MODULE.TRACK_ENV: str(tracked)})
            cli = [sys.executable, str(REPO / "bin" / "harness"), "studio"]
            started = subprocess.run(cli + ["--detach", "--no-open", "--json"], env=env,
                                     capture_output=True, text=True, timeout=60)
            self.addCleanup(subprocess.run, cli + ["stop", "--json"], env=env,
                            capture_output=True, text=True, timeout=10)
            self.addCleanup(MODULE.kill_tracked, str(tracked))
            self.assertEqual(started.returncode, 0, started.stderr)
            state = home / ".local/state/agent-harness/studio/instance.json"
            pid = json.loads(state.read_text())["pid"]
            self.assertEqual([entry.name for entry in tracked.iterdir()], [str(pid)])
            stopped = subprocess.run(cli + ["stop", "--json"], env=env, capture_output=True,
                                     text=True, timeout=10)
            self.assertEqual(stopped.returncode, 0, stopped.stderr)
            for _ in range(50):
                if not list(tracked.iterdir()):
                    break
                time.sleep(0.1)
            self.assertEqual(list(tracked.iterdir()), [])

    def test_studio_tracking_reads_the_same_variable_as_the_qualification(self):
        self.assertEqual(studio_lifecycle.TRACK_ENV, MODULE.TRACK_ENV)


class ChromePinTests(unittest.TestCase):
    def read(self, version):
        return subprocess.CompletedProcess([], 0, version + "\n", "")

    def test_a_newer_stable_says_the_pin_is_behind_before_any_suite_runs(self):
        with patch.object(MODULE, "command", return_value=self.read("Google Chrome 155.0.1.2")), \
                patch.object(MODULE, "run_suite") as run_suite, \
                patch.object(MODULE, "supported_tuple", return_value={"stable_major": 154}):
            with self.assertRaisesRegex(AssertionError,
                                        "pin is behind: stable is major 155 but "
                                        "compatibility/studio.json pins 154; update "
                                        "compatibility/studio.json"):
                MODULE.browser_flow(REPO, sys.executable, "/fixture/chrome")
        run_suite.assert_not_called()

    def test_an_older_chrome_is_refused_as_off_the_pin(self):
        with patch.object(MODULE, "command", return_value=self.read("Google Chrome 152.0.1.2")), \
                patch.object(MODULE, "supported_tuple", return_value={"stable_major": 154}):
            with self.assertRaisesRegex(AssertionError, "not pinned stable Chrome major 154"):
                MODULE.observed_chrome(REPO, "/fixture/chrome")

    def test_hosted_image_chrome_and_installer_digest_are_validated(self):
        with patch.dict(os.environ, {MODULE.IMAGE_CHROME_ENV: "Google Chrome 152.0.7977.83",
                                     MODULE.INSTALLER_SHA_ENV: "a" * 64}):
            self.assertEqual(MODULE.image_chrome(), "Google Chrome 152.0.7977.83")
            self.assertEqual(MODULE.installer_sha256(), "a" * 64)
        with patch.dict(os.environ, {MODULE.IMAGE_CHROME_ENV: "Chromium 152.0.0.0",
                                     MODULE.INSTALLER_SHA_ENV: "xyz"}):
            with self.assertRaisesRegex(AssertionError, "not a Google Chrome version"):
                MODULE.image_chrome()
            with self.assertRaisesRegex(AssertionError, "not a SHA-256 digest"):
                MODULE.installer_sha256()
        with patch.dict(os.environ, {MODULE.IMAGE_CHROME_ENV: "", MODULE.INSTALLER_SHA_ENV: ""}):
            self.assertIsNone(MODULE.image_chrome())
            self.assertIsNone(MODULE.installer_sha256())

    def test_preflight_validates_hosted_fields_and_the_frozen_source(self):
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
                                  "image_version": "Google Chrome 152.0.7977.83",
                                  "installer_sha256": "b" * 64},
                      "cases": [{"name": name, "status": "passed"}
                                for name in sorted(MODULE.REQUIRED_CASES)]}
            path = root / "compatibility" / "evidence" / "studio-macos-9.9.9.json"
            relative = "compatibility/evidence/studio-macos-9.9.9.json: "
            path.write_text(json.dumps(record))
            self.assertEqual(MODULE.evidence_errors(root, "a" * 40), [])
            record["browser"].update(image_version="152", installer_sha256="B" * 64)
            path.write_text(json.dumps(record))
            self.assertEqual(MODULE.evidence_errors(root, "a" * 40), [
                relative + "hosted image Chrome version is malformed",
                relative + "Chrome installer digest is malformed"])
            record["browser"].pop("image_version")
            record["browser"].pop("installer_sha256")
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


class WorkflowAndDocsTests(unittest.TestCase):
    def setUp(self):
        self.workflow = (REPO / ".github/workflows/studio-qualification.yml").read_text()

    def test_hosted_macos_installs_chain_verified_current_stable_and_records_it(self):
        install = self.workflow.index("Install current stable Chrome on macOS")
        pin = self.workflow.index("Check the Chrome pin")
        qualify = self.workflow.index("scripts/studio_lifecycle_acceptance.py \\")
        self.assertLess(install, pin)
        self.assertLess(pin, qualify)
        step = self.workflow[install:pin]
        self.assertIn("if: runner.os == 'macOS'", step)
        self.assertLess(step.index(MODULE.IMAGE_CHROME_ENV), step.index("curl"))
        self.assertIn("https://dl.google.com/chrome/mac/universal/stable/", step)
        self.assertIn(MODULE.INSTALLER_SHA_ENV + "=$(shasum -a 256", step)
        self.assertIn("-R='anchor apple generic and certificate leaf[subject.OU] = "
                      "\"EQHXZ8M8AV\"'", step)
        self.assertNotIn("TeamIdentifier", step)

    def test_a_pin_behind_stable_is_reported_by_name_on_every_platform(self):
        pin = self.workflow[self.workflow.index("Check the Chrome pin"):
                            self.workflow.index("Qualify lifecycle, security and Chrome flows")]
        self.assertNotIn("if:", pin)
        self.assertIn("qualification.observed_chrome(", pin)
        self.assertIn("::error title={}::{}", pin)
        self.assertIn("Studio Chrome pin is behind", pin)
        self.assertIn("workflow_dispatch:", self.workflow)

    def test_release_docs_name_where_each_platform_record_comes_from(self):
        releasing = " ".join((REPO / "docs/releasing.md").read_text().split())
        self.assertIn("after the freeze and never before it", releasing)
        self.assertIn("commit the record its macOS job prints as "
                      "`compatibility/evidence/studio-macos-<version>.json`", releasing)
        self.assertIn("the one its Linux job prints as "
                      "`compatibility/evidence/studio-linux-<version>.json`", releasing)
        self.assertIn("so only its record carries `browser.image_version`", releasing)
        self.assertIn("freeze the new commit as the candidate, and qualify it again", releasing)


if __name__ == "__main__":
    unittest.main()

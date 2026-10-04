# SPDX-License-Identifier: MIT
"""The Studio's six main flows, end to end in headless Chrome, against a fixture home (AH-S316).

Each test drives the shipped bundle through the served Studio of a fixture checkout, as a user
would: open the Studio, follow the first-run guide, tune a draft and plan its test, run the free
suites, compare two finished replays, and apply a draft and roll it back. Every test ends by
proving the tab requested nothing outside the Studio's loopback origin and no model client ran;
:class:`SealTests` proves each seal fails when it is breached. The fixture and its seals are
described in ``studio_e2e_support``; the module runs only with ``STUDIO_E2E=1``, which
``.github/workflows/studio-e2e.yml`` sets.
"""
from __future__ import annotations

import datetime
import json
import os
import secrets
import subprocess
import time
import unittest
from unittest import mock

import studio_e2e_support as support
from harness_core.studio import drafts, replay, runs, state_root

FIRST_RUN_DRAFT = "first-run"
FOREIGN = "http://127.0.0.1:1"
SWITCHED_RULE = "cache-hygiene"


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def seed_finished_replays(home, checkout):
    """Two finished live replays in ``home``'s run store, with native rows and no model process.

    The rows are ``test_studio_compare``'s: the first run's second target is cheaper on every
    task. Admission is held off while each run is recorded, then the record is closed as the
    run worker would close it. Returns the two run ids.
    """
    import test_studio_compare as compare_fixture
    from studio_target_support import FixtureTargetService
    from test_studio_replay import write_native_result
    from test_replay_stats import rows_for

    plans = (
        ([compare_fixture.target("branch", "main", compare_fixture.BASE_REV),
          compare_fixture.target("branch", "faster", compare_fixture.CANDIDATE_REV)],
         {compare_fixture.BASE_REV: compare_fixture.BASE,
          compare_fixture.CANDIDATE_REV: compare_fixture.CHEAPER}),
        ([compare_fixture.target("branch", "noisy", compare_fixture.OTHER_REV),
          compare_fixture.target("branch", "main", compare_fixture.BASE_REV)],
         {compare_fixture.OTHER_REV: compare_fixture.NOISY,
          compare_fixture.BASE_REV: compare_fixture.BASE}),
    )
    supervisor = runs.RunSupervisor(state_root(home), runs.default_catalog_path(checkout),
                                    target_service=FixtureTargetService())
    run_ids = []
    try:
        for targets, rows_by_revision in plans:
            selected = compare_fixture.request(targets)
            launch = replay.launch_payload(selected, "unused", checkout)
            cases = replay.case_identities(selected)
            preview = supervisor.spend_preview(
                "live-replay", launch["parameters"], launch["target_kind"], launch["target_ref"],
                selected.max_budget_usd, selected.spend_cap_usd, "api_credit",
                case_identities=cases)
            with mock.patch.object(supervisor, "_admit_locked"):
                started = supervisor.start(
                    "live-replay", launch["parameters"], launch["target_kind"],
                    launch["target_ref"], confirmed=preview["confirmation_token"],
                    max_budget_usd=selected.max_budget_usd,
                    spend_cap_usd=selected.spend_cap_usd, pricing_source="api_credit",
                    case_identities=cases)
            run_id = started["run_id"]

            def native(command, **_kwargs):
                ref = command[command.index("--tag") + 1]
                write_native_result(command, [
                    dict(row, tag=ref, harness_sha=ref, model=selected.model, schema_version=1)
                    for row in rows_for(rows_by_revision[ref])])
                return mock.Mock(returncode=0)

            replay.execute(selected, checkout, supervisor._run_path(run_id).parent / "replay",
                           native)
            # The lifecycle the run worker writes, each step a legal transition in the sidecar.
            identity = runs.process_identity(os.getpid()) or secrets.token_hex(24)
            stamp = _now()
            for fields in ({"status": "admitted", "admission_token": secrets.token_hex(16)},
                           {"status": "starting", "runner_pid": os.getpid(),
                            "runner_identity": identity},
                           {"status": "running", "command_pid": os.getpid(),
                            "command_identity": identity, "started_at": stamp},
                           {"status": "succeeded", "completed_at": _now(), "returncode": 0}):
                with supervisor.lock():
                    record = supervisor._read(run_id)
                    record.update(fields)
                    supervisor._write(record)
            run_ids.append(run_id)
    finally:
        supervisor.close()
    return run_ids


class StudioEndToEndFlows(support.StudioE2E):
    def tearDown(self):
        if hasattr(self, "devtools"):
            self.assert_no_network()
        self.assert_no_model_calls()
        super().tearDown()

    def prepare_home(self):
        if self._testMethodName == "test_compare_two_finished_replays_from_the_compare_panel":
            self.run_ids = seed_finished_replays(self.home, self.checkout.root)

    def test_open_the_studio_and_reach_every_area(self):
        """Flow 1: the launcher's session opens the Hub, and every stable area renders."""
        self.open()
        self.wait("document.querySelector('.system-summary') !== null", "Hub did not load")
        self.assertIn("Installed system", self.js("document.querySelector('main').textContent"))
        # The detached Studio resolves the model clients to the stubs, so the model seal
        # watches the processes the Studio itself starts.
        status, overview = self.api("GET", "/api/overview")
        self.assertEqual(status, 200)
        self.assertIn("0.0.0-fixture", json.dumps(overview))
        areas = (("#/configure", "Configure", "document.querySelector('input') !== null"),
                 ("#/library", "Library", "document.querySelectorAll('.library-module').length > 0"),
                 ("#/experiments", "Experiments",
                  "document.querySelector('.experiment-launch') !== null"),
                 ("#/reports", "Reports", "document.querySelector('h1') !== null"),
                 ("#/activity", "Activity", "document.querySelector('h1') !== null"))
        for route, label, ready in areas:
            with self.subTest(area=label):
                self.js("location.hash = %s" % json.dumps(route))
                self.wait("document.querySelector('.nav-link[aria-current=page]')?.textContent === %s"
                          % json.dumps(label), label + " was not the current area")
                self.wait(ready, label + " did not render", seconds=60)

    def test_first_run_guide_goes_from_health_to_an_applied_setup(self):
        """Flow 6 (#997): health, draft, identity, preferences, the free check, then apply."""
        self.discard_after(FIRST_RUN_DRAFT)
        live_before = self.config_path.read_bytes()
        self.open("#/setup")
        self.wait("__has('From this install to an applied setup.')", "the guide did not open")
        self.click("Continue")
        self.click("Create the draft")
        self.wait("__has('Draft first-run is ready.')", "the guide did not create its draft")
        self.wait("[...document.querySelectorAll('label')]"
                  ".some(label => label.textContent.trim().startsWith('Name'))",
                  "the identity fields did not load")
        self.js("__setLabelValue('Name', 'Casey Example')")
        self.click("Save and continue")
        self.wait("__has('Each pick previews its operative text')", "preferences did not render")
        self.assertEqual(self.config_path.read_bytes(), live_before, "a draft step changed live")
        self.click("Continue")
        self.click("Run the free check")
        self.wait("__has('The free check passed.')", "the free check did not pass", seconds=180)
        self.click("Continue to review")
        self.review_passes()
        self.js("__setLabelValue('Confirm the draft to apply', %s)" % json.dumps(FIRST_RUN_DRAFT))
        self.click("Apply " + FIRST_RUN_DRAFT)
        status = None
        for _ in range(2):
            self.wait("!__has('Applying %s under the sync lock')" % FIRST_RUN_DRAFT,
                      "apply did not finish", seconds=180)
            status = self.cli_json("draft", "first-run", FIRST_RUN_DRAFT, timeout=60)
            if status["state"] == "complete":
                break
        self.assertEqual(status["state"], "complete", status)
        live = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(live["identity"]["name"], "Casey Example")

    def test_tune_a_draft_then_plan_its_test_without_starting_it(self):
        """Flow 2 and #991/#1211: a checkpointed draft, then a test plan that starts nothing.

        The change is a configuration switch, which the plan refuses by name; a source change a
        replay can measure cannot be made from the Studio for a core module.
        """
        draft = self.create_draft("e2e-tune-")
        live_before = self.config_path.read_bytes()
        self.tune(draft)
        self.assertIn("Nothing applied", self.js("document.body.textContent"))
        self.assertEqual(self.config_path.read_bytes(), live_before)

        self.wait("[...document.querySelectorAll('h2')].some(h => h.textContent === 'Test this draft')",
                  "the draft test panel did not mount")
        self.wait("__has('No tests of this draft yet.')", "the verdicts route did not answer")
        self.choose_option("Tasks", self.options("Tasks")[0])
        self.wait("!__has('Choose at least one task.')", "the task was not chosen")
        self.click("Check power and spend")
        # A replay builds the harness arm from the commit's defaults, so a configuration change
        # is refused by name before any target is built or any spend is guarded.
        self.wait("__has('This draft changed its configuration, which a replay cannot measure.')",
                  "the plan route's refusal was not shown", seconds=60, section="Test this draft")
        self.assertFalse(self.js("__buttonReady('Confirm and test')"))
        listed = self.cli_json("runs", "history")
        self.assertEqual([item for item in listed.get("items", [])
                          if item.get("suite_id") == "live-replay"], [],
                         "planning a draft test started a replay")

    def tune(self, draft: str) -> None:
        """Load ``draft`` in Configure, switch one rule off and wait for its checkpoint."""
        created = drafts.find(self.checkout.root, draft)[1]["revision"]
        self.open("#/configure")
        self.wait("document.querySelector('input') !== null", "Configure did not render")
        self.js("__setLabelValue('Draft name', %s)" % json.dumps(draft))
        self.click("Load draft")
        self.wait("__has('Selection controls are ready.')", "the selection editor did not load")
        self.js("[...document.querySelectorAll('.selection-switch-group')]"
                ".forEach(group => { group.open = true; })")
        self.assertTrue(self.js("__labelled(%s).checked" % json.dumps(SWITCHED_RULE)))
        self.js("__labelled(%s).click()" % json.dumps(SWITCHED_RULE))
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if drafts.find(self.checkout.root, draft)[1]["revision"] != created:
                break
            time.sleep(0.1)
        else:
            self.fail("switching %s off was not checkpointed" % SWITCHED_RULE)
        self.wait("__has('Nothing applied')", "the draft did not say nothing was applied")

    def review_passes(self) -> None:
        """Press Review draft and require the review to allow the apply."""
        self.click("Review draft", seconds=60)
        self.wait("__has('Nothing has been applied.')", "the review did not finish", seconds=180)
        panel = self.js("document.querySelector('#draft-apply').innerText")
        self.assertIn("Review complete. Nothing has been applied.", panel, panel[-3000:])


    def test_free_suites_run_to_completion_with_no_model_usage(self):
        """Flow 4 and #988: a free suite from the launcher and an evaluation tier's free run."""
        self.open("#/experiments")
        self.wait("document.querySelector('.experiment-launch') !== null",
                  "the free-suite launcher did not load", seconds=60)
        self.assertIn("No model usage", self.js(
            "document.querySelector('.experiment-launch').textContent"))
        lint = next((item for item in self.options("Suite") if "lint" in item.lower()), None)
        self.assertTrue(lint, "the launcher offers no lint suite")
        self.choose_option("Suite", lint)
        self.click("Run " + lint)
        self.wait("__has('Run detail')", "the run detail did not open")
        lint_run = self._await_run("lint")
        self.assertEqual(lint_run["status"], "succeeded", lint_run)
        self.wait("/succeeded/.test([...document.querySelectorAll('h2')].find(heading =>"
                  " heading.textContent === 'lint')?.closest('.mantine-Group-root')"
                  "?.textContent || '')",
                  "the run detail did not show the run succeeded")

        self.click("Run the hook matrix")
        self.wait("[...document.querySelectorAll('h2')].some(h => h.textContent ==="
                  " 'Run an engine, read its own result.') && __has('hook-matrix')",
                  "the hook matrix did not start")
        finished = self._await_run("hook-matrix")
        self.assertEqual(finished["status"], "succeeded", finished)

    def _await_run(self, suite_id: str, seconds: float = 300.0) -> dict:
        deadline = time.monotonic() + seconds
        latest: dict = {}
        while time.monotonic() < deadline:
            listed = self.cli_json("runs", "history")
            matches = [item for item in listed.get("items", []) if item.get("suite_id") == suite_id]
            if matches:
                latest = matches[0]
                if latest.get("status") in runs.TERMINAL:
                    return latest
            time.sleep(1)
        self.fail("%s did not finish: %s" % (suite_id, latest))

    def test_compare_two_finished_replays_from_the_compare_panel(self):
        """Flow 5 (#990): two finished replays, compared from the Compare panel.

        The served route has a known defect: it reads the run supervisor off the mutation
        thread (``server._runs_compare``) and the run index refuses a second thread, so it
        answers 404 ``compare_not_found`` for any real run. Its fix is in review separately. A
        capability probe asks the route to compare the seeded runs: a 200 means the fix is
        present and the success path is asserted; exactly the known 404 means it is not, and
        the panel must show that answer. Any other answer fails. The fallback goes once both
        changes are merged.
        """
        first, _second = self.run_ids
        self.assertEqual(self.cli_json("runs", "compare", first + ":1", first + ":2")["base"]
                         ["run_id"], first, "the seeded runs are not finished replays")
        probe = {"base": {"run_id": first, "target": 1},
                 "candidate": {"run_id": first, "target": 2}}
        status, answer = self.api("POST", "/api/runs/compare", probe)
        fixed = status == 200
        if not fixed:
            self.assertEqual((status, answer), (404, {"error": "compare_not_found"}))
        else:
            self.assertTrue(answer["comparable"], answer)
            self.assertEqual(answer["result"]["arms"][0]["measures"]["cost_per_passed"]
                             ["reading"], "lower", "the cheaper target did not read lower")
        self.open("#/experiments")
        self.wait("__has('Compare two runs, paired by task.')", "the compare panel did not render",
                  seconds=60)
        # One field at a time: each edit re-renders the form from the last one's state.
        for label in ("Base run id", "Candidate run id"):
            self.js("__setLabelValue(%s, %s)" % (json.dumps(label), json.dumps(first)))
            self.wait("__labelled(%s).value === %s" % (json.dumps(label), json.dumps(first)),
                      label + " did not take the run id")
        self.choose_option("Candidate target", "2")
        self.click("Compare")
        if fixed:
            self.wait("__has('Compared by the engine.')", "the engine did not compare the runs",
                      seconds=60, section="Compare two runs")
            self.assertIn("paired by task",
                          self.js("document.querySelector('caption')?.textContent || ''"))
        else:
            self.wait("__has('One of the runs is not known to this Studio.')",
                      "the panel did not show the route's answer", seconds=60,
                      section="Compare two runs")

    def test_apply_a_draft_then_roll_it_back_from_activity(self):
        """Flow 3 (#978/#979): review, confirm and apply; then preview and roll back."""
        draft = self.create_draft("e2e-apply-")
        live_before = self.config_path.read_bytes()
        self.tune(draft)
        self.review_passes()
        self.assertEqual(self.config_path.read_bytes(), live_before, "review changed live")
        self.js("__setLabelValue('Confirm the draft to apply', %s)" % json.dumps(draft))
        self.click("Apply " + draft)
        self.wait("!__has('under the sync lock')", "apply did not finish", seconds=180)
        applied = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(applied.get("rules", {}).get(SWITCHED_RULE), "off", applied.get("rules"))

        self.js("location.hash = '#/activity'")
        self.click("Preview rollback", seconds=60)
        self.wait("__has('Preview ready. Nothing has changed.')", "rollback preview failed",
                  seconds=60)
        self.js("__setLabelValue('Confirm the applied draft to roll back', %s)" % json.dumps(draft))
        self.click("Roll back " + draft)
        self.wait("!__has('Rolling back under the sync lock')", "rollback did not finish",
                  seconds=180)
        self.assertEqual(self.config_path.read_bytes(), live_before,
                         "rollback did not restore the live configuration byte for byte")


class SealTests(support.StudioE2E):
    """Each seal fails when it is breached; a seal that always passes would hide a breach."""

    def test_the_network_seal_fails_on_a_request_outside_the_loopback_origin(self):
        self.open()
        self.wait("document.querySelector('.system-summary') !== null", "Hub did not load")
        self.assert_no_network()
        # The browser blocks the foreign navigation itself, so nothing leaves this machine, yet
        # the request is still announced, as a real one would be.
        # Port 1 on loopback is outside the Studio's origin yet never leaves this machine.
        self.devtools.call("Page.navigate", {"url": FOREIGN + "/seal-probe"})
        with self.assertRaises(AssertionError) as caught:
            self.assert_no_network()
        self.assertIn(FOREIGN + "/seal-probe", str(caught.exception))

    def test_the_network_seal_counts_websockets(self):
        self.open()
        self.devtools.call("Page.navigate", {"url": "about:blank"})
        self.wait("location.href === 'about:blank'", "the blank page did not load")
        self.js("try { new WebSocket('ws://127.0.0.1:1/seal-probe') } catch (error) {}")
        with self.assertRaises(AssertionError) as caught:
            for _ in range(10):
                self.assert_no_network(settle=0.2)
        self.assertIn("ws://127.0.0.1:1/seal-probe", str(caught.exception))

    def test_the_model_seal_fails_on_a_model_call_and_ignores_the_offline_probes(self):
        for argv in (["claude", "--version"], ["codex", "--version"],
                     ["codex", "app-server", "generate-json-schema", "--out", "/nonexistent"]):
            subprocess.run(argv, env=self.env, capture_output=True, timeout=10)
        self.assert_no_model_calls()
        called = subprocess.run(["claude", "-p", "hello"], env=self.env, capture_output=True,
                                timeout=10)
        self.assertEqual(called.returncode, 97)
        with self.assertRaises(AssertionError) as caught:
            self.assert_no_model_calls()
        self.assertIn("claude -p hello", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

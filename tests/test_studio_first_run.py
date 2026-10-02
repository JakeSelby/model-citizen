# SPDX-License-Identifier: MIT
"""The guided first run: its state comes from its draft and the apply journal, abandoning it changes
nothing live, the commands it shows reproduce its choices, and an apply finishes it."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from harness_core.studio import apply as draft_apply  # noqa: E402
from harness_core.studio import drafts, first_run, selection_editing, server, settings  # noqa: E402

import draft_support  # noqa: E402
from test_studio_draft_apply import Home  # noqa: E402

STANCE = "stances.voice"


def _row(phase, draft, apply_id="a1", **extra):
    return dict({"apply_id": apply_id, "phase": phase, "draft": draft, "ts": "t"}, **extra)


class DeriveTests(unittest.TestCase):
    """The state machine alone, from synthetic drafts and journal rows."""

    def test_no_draft_and_no_apply_is_a_fresh_install_that_has_not_started(self):
        derived = first_run.derive("first-run", None, [], [], [])
        self.assertEqual(derived["state"], "not-started")
        self.assertTrue(derived["fresh"])
        self.assertTrue(derived["nothing_live_changed"])

    def test_a_kept_draft_is_in_progress_and_nothing_live_changed(self):
        derived = first_run.derive("first-run", {"name": "first-run"}, [], [], ["first-run"])
        self.assertEqual(derived["state"], "in-progress")
        self.assertTrue(derived["fresh"])
        self.assertTrue(derived["nothing_live_changed"])

    def test_another_draft_or_an_earlier_apply_means_the_install_is_not_fresh(self):
        self.assertFalse(first_run.derive("first-run", None, [], [], ["tuning"])["fresh"])
        rows = [_row("intent", "tuning"), _row("completed", "tuning", doctor="passed")]
        derived = first_run.derive("first-run", None, rows, [], [])
        self.assertFalse(derived["fresh"])
        self.assertEqual(derived["state"], "not-started")

    def test_an_open_apply_of_the_draft_is_interrupted_and_names_its_recovery(self):
        intent = _row("intent", "first-run")
        derived = first_run.derive("first-run", {"name": "first-run"}, [intent], [intent], ["first-run"])
        self.assertEqual(derived["state"], "interrupted")
        self.assertFalse(derived["nothing_live_changed"])
        self.assertEqual(derived["interrupted"]["recover_command"], "citizen draft recover --json")
        other = _row("intent", "tuning", apply_id="a2")
        self.assertEqual(first_run.derive("first-run", {"name": "first-run"}, [other], [other],
                                          ["first-run"])["state"], "in-progress")

    def test_a_completed_apply_finishes_it_with_the_commands_that_apply_ran(self):
        rows = [
            _row("intent", "first-run", revision="r2", config=[
                {"action": "set", "key": STANCE, "value": "concise"},
                {"action": "unset", "key": "identity.github", "value": None},
                {"action": "none", "key": "mode", "value": None}]),
            _row("completed", "first-run", revision="r2", doctor="passed"),
        ]
        derived = first_run.derive("first-run", {"name": "first-run"}, rows, [], ["first-run"])
        self.assertEqual(derived["state"], "complete")
        self.assertFalse(derived["fresh"])
        self.assertEqual((derived["applied"]["doctor"], derived["applied"]["revision"]), ("passed", "r2"))
        self.assertEqual([item["command"] for item in derived["applied"]["choices"]],
                         ["citizen config set stances.voice concise",
                          "citizen config unset identity.github"])

    def test_an_invalid_draft_name_is_refused(self):
        for name in ("", "../x", "-x", 7):
            with self.subTest(name=name):
                with self.assertRaises(first_run.FirstRunError):
                    first_run.status(ROOT, name, Path(tempfile.gettempdir()))


class FirstRunDraftTests(unittest.TestCase):
    """A real first-run draft in an isolated home."""

    def setUp(self):
        config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        config["primitive_roots"] = []
        self.initial = config

    @contextmanager
    def first_run_draft(self):
        # A unique name: draft branches live in the shared repository every worktree sees.
        name = "first-run-" + uuid.uuid4().hex[:10]
        with tempfile.TemporaryDirectory() as temporary:
            home = Home(temporary, "home", self.initial)
            before = first_run.status(ROOT, name, home.path)
            self.assertEqual(before["state"], "not-started")
            created = home.cli("draft", "create", name, "--json", timeout=60)
            self.assertEqual(created.returncode, 0, created.stderr or created.stdout)
            try:
                with mock.patch.dict(os.environ, home.env, clear=True):
                    yield name, json.loads(created.stdout)["revision"], home, Path(temporary)
            finally:
                draft_support.discard_draft(self, name, home.env)

    def _choose(self, name, revision):
        """The guide's identity and preference steps, through the same draft cores."""
        current = selection_editing.read(ROOT, name)["current"]["selection"]["stances"]["voice"]
        options = next(item["options"] for item in selection_editing.read(ROOT, name)["controls"]["stances"]
                       if item["name"] == "voice")
        stance = next(option for option in options if option != current)
        picked = selection_editing.save(ROOT, name, revision, "first-run-voice", {STANCE: stance})
        self.assertTrue(picked["saved"], picked)
        named = settings.save(ROOT, name, picked["result"]["revision"], "first-run-identity",
                              {"identity.name": "Casey Example", "identity.role": "Developer"})
        self.assertTrue(named["saved"], named)
        return named["result"]["revision"], stance

    def _status(self, home, name):
        done = home.cli("draft", "first-run", name, "--json", timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr or done.stdout)
        return json.loads(done.stdout)

    def test_an_abandoned_run_changes_nothing_live_and_keeps_its_draft_to_resume(self):
        """AC2."""
        with self.first_run_draft() as (name, revision, home, _base):
            live = home.config.read_bytes()
            revision, _stance = self._choose(name, revision)
            status = self._status(home, name)
            self.assertEqual(status["state"], "in-progress")
            self.assertTrue(status["nothing_live_changed"])
            self.assertEqual(status["draft"]["revision"], revision)
            self.assertEqual(home.config.read_bytes(), live)
            self.assertFalse(home.dest.exists())
            self.assertFalse(draft_apply.journal_path(home.path).exists())
            # Resuming finds the same draft and its checkpoints.
            self.assertEqual(drafts.find(ROOT, name)[1]["revision"], revision)

    def test_the_commands_it_shows_reproduce_the_same_choices_headless(self):
        """AC3: run the shown `citizen config set` lines in a second home; it matches the draft."""
        with self.first_run_draft() as (name, revision, home, base):
            revision, stance = self._choose(name, revision)
            status = self._status(home, name)
            keys = [item["key"] for item in status["choices"]]
            self.assertEqual(sorted(keys), sorted([STANCE, "identity.name", "identity.role"]))
            self.assertEqual(status["commands"]["headless"][-2:], ["citizen sync", "citizen doctor"])
            twin = Home(base, "twin", self.initial)
            for item in status["choices"]:
                done = twin.cli("config", item["action"], item["key"],
                                *(() if item["value"] is None else (item["value"],)))
                self.assertEqual(done.returncode, 0, done.stderr or done.stdout)
            reproduced = json.loads(twin.config.read_text(encoding="utf-8"))
            drafted = drafts.read_config(ROOT, name)
            self.assertEqual(reproduced["stances"]["voice"], stance)
            for key in ("name", "role"):
                self.assertEqual(reproduced["identity"][key], drafted["config"]["identity"][key])

    def test_applying_the_draft_finishes_the_run_with_the_doctor_result(self):
        """AC1: the run ends on an applied draft whose doctor result it reports."""
        with self.first_run_draft() as (name, revision, home, _base):
            revision, stance = self._choose(name, revision)
            shown = self._status(home, name)["commands"]["headless"]
            done = home.cli("draft", "apply", name, "--revision", revision, "--json", timeout=600)
            result = json.loads(done.stdout)
            self.assertEqual(result["status"], "applied", result)
            status = self._status(home, name)
            self.assertEqual(status["state"], "complete")
            self.assertFalse(status["nothing_live_changed"])
            self.assertEqual(status["applied"]["doctor"], result["doctor"]["status"])
            self.assertEqual(status["applied"]["revision"], revision)
            self.assertEqual(json.loads(home.config.read_text(encoding="utf-8"))["stances"]["voice"], stance)
            self.assertEqual(sorted(status["commands"]["headless"]), sorted(shown))

    def test_the_cli_reports_an_invalid_name_as_an_error_object(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Home(temporary, "home", self.initial)
            done = home.cli("draft", "first-run", "../escape", "--json", timeout=60)
            self.assertEqual(done.returncode, 1)
            self.assertEqual(json.loads(done.stdout)["error"]["code"], "invalid-name")


class FirstRunRouteRegistryTests(unittest.TestCase):
    def test_both_routes_map_to_their_citizen_commands(self):
        routes = {(route.method, route.path): route for route in server.ROUTES.entries}
        self.assertEqual(routes[("POST", "/api/first-run")].cli_command, first_run.CLI_COMMANDS["status"])
        self.assertEqual(routes[("POST", "/api/first-run/start")].cli_command, first_run.CLI_COMMANDS["start"])
        for path in ("/api/first-run", "/api/first-run/start"):
            self.assertIsNone(routes[("POST", path)].parity_exemption)

    def test_init_and_the_installer_point_at_the_studio(self):
        lines = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8").rstrip().splitlines()
        self.assertEqual(lines[-1], "EOF")
        self.assertTrue(lines[-2].endswith("$CHECKOUT/bin/citizen studio"), lines[-2])
        with tempfile.TemporaryDirectory() as temporary:
            env = {key: value for key, value in os.environ.items() if not key.startswith("HARNESS_")}
            env.update(HOME=temporary, HARNESS_HOME=temporary)
            done = subprocess.run([sys.executable, str(ROOT / "bin" / "harness"), "init", "--yes",
                                   "--name", "Casey", "--role", "Developer"],
                                  env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(done.stdout.rstrip().splitlines()[-1].endswith("citizen studio"), done.stdout)

if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: MIT
"""The guided first run: its state comes from its draft and the apply journal, abandoning it changes
nothing live, the commands it shows reproduce its choices, and an apply finishes it."""
from __future__ import annotations

import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
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
from isolation import without_harness_vars  # noqa: E402
from test_installer_script import source_repo  # noqa: E402
from test_studio_draft_apply import Home  # noqa: E402

STANCE = "stances.voice"
DRAFT = {"name": "first-run", "draft_id": "d1"}


def _load_cli():
    loader = importlib.machinery.SourceFileLoader("harness_first_run_cli", str(ROOT / "bin" / "harness"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


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
        derived = first_run.derive("first-run", DRAFT, [], [], ["first-run"])
        self.assertEqual(derived["state"], "in-progress")
        self.assertTrue(derived["fresh"])
        self.assertTrue(derived["nothing_live_changed"])

    def test_another_draft_an_earlier_apply_or_a_configured_home_is_not_fresh(self):
        self.assertFalse(first_run.derive("first-run", None, [], [], ["tuning"])["fresh"])
        configured = first_run.derive("first-run", None, [], [], [], configured=True)
        self.assertFalse(configured["fresh"])
        self.assertEqual(configured["state"], "not-started")
        rows = [_row("intent", "tuning"), _row("completed", "tuning", doctor="passed")]
        derived = first_run.derive("first-run", None, rows, [], [])
        self.assertFalse(derived["fresh"])
        self.assertEqual(derived["state"], "not-started")

    def test_an_open_apply_of_the_draft_is_interrupted_and_names_its_recovery(self):
        intent = _row("intent", "first-run", draft_id="d1")
        derived = first_run.derive("first-run", DRAFT, [intent], [intent], ["first-run"])
        self.assertEqual(derived["state"], "interrupted")
        self.assertFalse(derived["nothing_live_changed"])
        self.assertEqual(derived["interrupted"]["recover_command"], "citizen draft recover --json")
        self.assertEqual(derived["blocked_by"], {})

    def test_an_open_apply_of_another_draft_blocks_this_one_without_claiming_it(self):
        other = _row("intent", "tuning", apply_id="a2", draft_id="d9")
        derived = first_run.derive("first-run", DRAFT, [other], [other], ["first-run", "tuning"])
        self.assertEqual(derived["state"], "in-progress")
        self.assertEqual(derived["interrupted"], {})
        self.assertEqual(derived["blocked_by"]["draft"], "tuning")

    def test_a_failed_and_restored_apply_leaves_the_run_in_progress_with_nothing_live_changed(self):
        rows = [_row("intent", "first-run", draft_id="d1"),
                _row("failed", "first-run", draft_id="d1", restored=True)]
        derived = first_run.derive("first-run", DRAFT, rows, [], ["first-run"])
        self.assertEqual(derived["state"], "in-progress")
        self.assertTrue(derived["nothing_live_changed"])
        self.assertTrue(derived["fresh"])

    def test_a_recreated_draft_of_the_same_name_is_a_new_run(self):
        """An apply of a discarded draft does not finish the draft that now has its name."""
        rows = [_row("intent", "first-run", draft_id="old"),
                _row("completed", "first-run", draft_id="old", doctor="passed")]
        derived = first_run.derive("first-run", DRAFT, rows, [], ["first-run"])
        self.assertEqual(derived["state"], "in-progress")
        self.assertEqual(derived["applied"], {})
        # With no draft of the name left, the earlier apply is the run's history.
        self.assertEqual(first_run.derive("first-run", None, rows, [], [])["state"], "complete")

    def test_a_completed_apply_finishes_it_with_the_commands_that_apply_ran(self):
        rows = [
            _row("intent", "first-run", draft_id="d1", revision="r2", config=[
                {"action": "set", "key": STANCE, "value": "concise"},
                {"action": "unset", "key": "identity.github", "value": None},
                {"action": "none", "key": "mode", "value": None}]),
            _row("completed", "first-run", draft_id="d1", revision="r2", doctor="passed"),
        ]
        derived = first_run.derive("first-run", DRAFT, rows, [], ["first-run"])
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
    def first_run_draft(self, config=None, tools=()):
        # A unique name: draft branches live in the shared repository every worktree sees.
        name = draft_support.draft_name("first-run-")
        with tempfile.TemporaryDirectory() as temporary:
            home = Home(temporary, "home", self.initial if config is None else config)
            if tools:
                # Stand-ins for clients this machine may lack, so doctor reads the same everywhere.
                bin_dir = Path(temporary) / "bin"
                bin_dir.mkdir()
                for tool, version in tools:
                    script = bin_dir / tool
                    script.write_text("#!/bin/sh\necho '%s'\n" % version, encoding="utf-8")
                    script.chmod(0o755)
                home.env["PATH"] = str(bin_dir) + os.pathsep + home.env.get("PATH", "")
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

    def test_a_fully_configured_twin_home_ends_with_doctor_passed(self):
        """AC1: every identity field set and every client on PATH, so the applied run reads green."""
        config = dict(self.initial, identity=dict(self.initial["identity"], name="Casey Example",
                                                  role="Developer", github="casey-example"))
        # Every client doctor probes, faked, so the result is the same on a bare CI runner.
        tools = tuple((tool, "codex-cli 0.130.0" if tool == "codex" else tool + " 1.0.0 (stub)")
                      for tool in _load_cli().DOCTOR_TOOLS)
        with self.first_run_draft(config, tools=tools) as (name, revision, home, _b):
            revision, _stance = self._choose(name, revision)
            done = home.cli("draft", "apply", name, "--revision", revision, "--json", timeout=600)
            result = json.loads(done.stdout)
            self.assertEqual(result["status"], "applied", result)
            self.assertEqual(result["doctor"]["status"], "passed", result["doctor"]["checks"])
            self.assertIn("drift: none", [check["message"] for check in result["doctor"]["checks"]])
            status = self._status(home, name)
            self.assertEqual((status["state"], status["applied"]["doctor"]), ("complete", "passed"))

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
        self.assertEqual(routes[("POST", "/api/first-run/start")].cli_command,
                         ("citizen", "draft", "first-run", "{draft}", "--start", "--json"))
        for path in ("/api/first-run", "/api/first-run/start"):
            self.assertIsNone(routes[("POST", path)].parity_exemption)



class ConfiguredHomeTests(unittest.TestCase):
    """Fresh means nothing set beyond what init wrote; a CLI-configured home is never fresh."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(os.path.realpath(self.tmp.name))
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("HARNESS_")}
        self.env.update(HOME=str(self.home), HARNESS_HOME=str(self.home))

    def cli(self, *args, timeout=120):
        done = subprocess.run([sys.executable, str(ROOT / "bin" / "harness"), *args], env=self.env,
                              capture_output=True, text=True, timeout=timeout)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done

    def test_a_home_init_wrote_is_fresh_and_stays_fresh_through_a_sync(self):
        self.assertFalse(first_run.configured(self.home))
        self.cli("init", "--yes", "--name", "Casey", "--role", "Developer")
        self.assertFalse(first_run.configured(self.home))
        self.cli("sync", timeout=600)
        self.assertFalse(first_run.configured(self.home))

    def test_setting_only_an_identity_field_from_the_cli_configures_the_home(self):
        self.cli("init", "--yes", "--name", "Casey", "--role", "Developer")
        self.cli("config", "set", "identity.pronouns", "she/her")
        self.assertTrue(first_run.configured(self.home))

    def test_setting_a_stance_configures_the_home(self):
        self.cli("init", "--yes", "--name", "Casey", "--role", "Developer")
        self.cli("config", "set", "stances.voice", "scannable")
        self.assertTrue(first_run.configured(self.home))

    def test_a_configuration_with_no_init_record_or_an_unreadable_one_is_configured(self):
        config = draft_apply.config_file(self.home)
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"stances": {}}), encoding="utf-8")
        self.assertTrue(first_run.configured(self.home))
        record = draft_apply.journal_path(self.home).parent / first_run.INIT_RECORD
        record.parent.mkdir(parents=True)
        record.write_text("{not json", encoding="utf-8")
        self.assertTrue(first_run.configured(self.home))


class StatusErrorTests(unittest.TestCase):
    """Every failure leaves status as a FirstRunError, which the route and CLI report as data."""

    def _status(self):
        return first_run.status(ROOT, "first-run-" + uuid.uuid4().hex[:8], Path(tempfile.gettempdir()))

    def test_an_unreadable_draft_elsewhere_in_the_repository_is_a_structured_error(self):
        with mock.patch.object(drafts, "list_drafts", side_effect=drafts.DraftError("invalid-state", "bad")):
            with self.assertRaises(first_run.FirstRunError) as caught:
                self._status()
        self.assertEqual(caught.exception.code, "invalid-state")

    def test_an_unreadable_journal_is_a_structured_error(self):
        with mock.patch.object(first_run, "_journal_rows", side_effect=PermissionError("denied")):
            with self.assertRaises(first_run.FirstRunError) as caught:
                self._status()
        self.assertEqual(caught.exception.code, "unreadable")

    def test_a_draft_held_by_a_long_save_is_busy_within_the_bounded_wait(self):
        draft = {"name": "x", "draft_id": "d", "revision": "r", "base_revision": "b",
                 "behind_installed": False, "created_at": "t"}
        with mock.patch.object(drafts, "find", return_value=(ROOT, {})), \
                mock.patch.object(drafts, "describe", return_value=draft), \
                mock.patch.object(drafts, "read_snapshot",
                                  side_effect=drafts.DraftError("busy", "held")) as read:
            with self.assertRaises(first_run.FirstRunError) as caught:
                self._status()
        self.assertEqual(caught.exception.code, "busy")
        self.assertEqual(read.call_args.kwargs["lock_timeout"], first_run.STATUS_LOCK_TIMEOUT)
        self.assertLessEqual(first_run.STATUS_LOCK_TIMEOUT, 10)

    def test_the_cli_prints_a_structured_error_instead_of_a_traceback(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Home(temporary, "home", {"stances": {}})
            journal = draft_apply.journal_path(home.path)
            journal.parent.mkdir(parents=True)
            journal.write_text("", encoding="utf-8")
            journal.chmod(0)  # present but unreadable
            try:
                done = home.cli("draft", "first-run", "first-run-" + uuid.uuid4().hex[:8], "--json",
                                timeout=60)
            finally:
                journal.chmod(0o600)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertNotIn("Traceback", done.stderr)
        self.assertEqual(json.loads(done.stdout)["error"]["code"], "unreadable")


class FirstRunHandlerTests(unittest.TestCase):
    """The route handlers' error paths, without a running server."""

    def _handler(self, draft):
        handler = mock.Mock()
        handler.request_json = {"draft": draft}
        handler.server.repo_root = ROOT
        handler.server.mutations.call.side_effect = lambda operation: operation()
        return handler

    def _route(self, path):
        return next(route for route in server.ROUTES.entries if route.path == path)

    def test_busy_status_answers_429_so_the_guide_retries(self):
        handler = self._handler("first-run")
        with mock.patch.object(first_run, "status", side_effect=first_run.FirstRunError("busy", "held")):
            server._first_run_status(handler, self._route("/api/first-run"))
        handler._error.assert_called_once_with(429, "first_run_busy")

    def test_a_structured_status_failure_answers_409_with_its_code(self):
        handler = self._handler("first-run")
        with mock.patch.object(first_run, "status", side_effect=first_run.FirstRunError("unreadable", "x")):
            server._first_run_status(handler, self._route("/api/first-run"))
        handler._error.assert_called_once_with(409, "unreadable")

    def _start(self, cleared, created="create-timeout"):
        handler = self._handler("first-run")
        not_started = {"draft": {}, "state": "not-started"}
        with mock.patch.object(first_run, "status", return_value=not_started), \
                mock.patch.object(first_run, "clear_partial", side_effect=cleared) as clear, \
                mock.patch.object(server, "_run_draft_create", return_value=created):
            server._first_run_start(handler, self._route("/api/first-run/start"))
        return handler, clear

    def test_a_timed_out_create_is_cleaned_up_before_and_after_and_reported(self):
        handler, clear = self._start([first_run.NOTHING, first_run.CLEARED])
        self.assertEqual(clear.call_count, 2)
        handler._error.assert_called_once_with(409, "create-timeout")

    def test_a_cleanup_that_fails_is_reported_honestly(self):
        handler, _clear = self._start([first_run.NOTHING, first_run.FAILED])
        handler._error.assert_called_once_with(409, "create-cleanup-failed")
        handler, _clear = self._start([first_run.FAILED])
        handler._error.assert_called_once_with(409, "create-cleanup-failed")

    def test_a_leftover_that_may_hold_work_stops_start_without_creating(self):
        with mock.patch.object(server, "_run_draft_create") as create:
            handler, _clear = self._start([first_run.KEPT])
        create.assert_not_called()
        handler._error.assert_called_once_with(409, "partial-draft-kept")

    def test_a_timeout_stops_the_whole_process_group_before_returning(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bin").mkdir()
            marker = root / "grandchild.pid"
            # Stands in for `citizen draft create`: it starts a child, as git does, then hangs.
            (root / "bin" / "harness").write_text(
                "import subprocess, sys, time\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                "open(%r, 'w').write(str(child.pid))\n"
                "time.sleep(60)\n" % str(marker), encoding="utf-8")
            with mock.patch.object(server, "CREATE_TIMEOUT", 3):
                self.assertEqual(server._run_draft_create(root, "first-run"), "create-timeout")
            grandchild = int(marker.read_text())
        for _ in range(50):
            try:
                os.kill(grandchild, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            self.fail("the create's child outlived the timeout")


class ClearPartialTests(unittest.TestCase):
    """What a killed `draft create` leaves is removed; a leftover that may hold work is kept."""

    def _git(self, *args, **kwargs):
        return subprocess.run(["git", *args], capture_output=True, text=True, **kwargs)

    def _leftover(self, temporary, commit=False, lock=False):
        name = draft_support.draft_name("first-run-")
        path = Path(temporary) / ("draft-" + name)
        self._git("-C", str(ROOT), "worktree", "add", "-q", "-b", "draft/" + name, str(path), "HEAD",
                  check=True)
        self.addCleanup(self._git, "-C", str(ROOT), "branch", "-D", "draft/" + name)
        self.addCleanup(self._git, "-C", str(ROOT), "worktree", "prune")
        self.addCleanup(self._git, "-C", str(ROOT), "worktree", "unlock", str(path))
        if commit:
            self._git("-C", str(path), "-c", "user.name=t", "-c", "user.email=t" + "@" + "example.invalid",
                      "commit", "-q", "--allow-empty", "-m", "work", check=True)
        if lock:
            # What a `git worktree add` killed mid-checkout leaves.
            self._git("-C", str(ROOT), "worktree", "lock", "--reason", "initializing", str(path), check=True)
        return name, path

    def test_a_branch_and_worktree_with_no_draft_state_are_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            name, path = self._leftover(temporary)
            self.assertEqual(first_run.clear_partial(ROOT, name), first_run.CLEARED)
            self.assertFalse(draft_support.draft_branch_exists(name))
            self.assertFalse(draft_support.draft_worktree_registered(name))
            self.assertFalse(path.exists())

    def test_a_worktree_locked_as_initializing_is_removed_too(self):
        with tempfile.TemporaryDirectory() as temporary:
            name, path = self._leftover(temporary, lock=True)
            self.assertEqual(first_run.clear_partial(ROOT, name), first_run.CLEARED)
            self.assertFalse(draft_support.draft_branch_exists(name))
            self.assertFalse(path.exists())

    def test_a_leftover_carrying_its_own_commit_is_kept(self):
        with tempfile.TemporaryDirectory() as temporary:
            name, _path = self._leftover(temporary, commit=True)
            self.assertEqual(first_run.clear_partial(ROOT, name), first_run.KEPT)
            self.assertTrue(draft_support.draft_branch_exists(name))

    def test_a_leftover_whose_draft_state_cannot_be_read_is_never_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            name, path = self._leftover(temporary)
            with mock.patch.object(drafts, "_paths", side_effect=drafts.DraftError("git-failed", "x")):
                self.assertEqual(first_run.clear_partial(ROOT, name), first_run.KEPT)
            self.assertTrue(path.exists())
            self.assertTrue(draft_support.draft_branch_exists(name))
            self._git("-C", str(ROOT), "worktree", "remove", "--force", str(path))

    def test_a_failed_branch_delete_is_reported_as_failed(self):
        with tempfile.TemporaryDirectory() as temporary:
            name, _path = self._leftover(temporary)
            real = drafts._git

            def refuse_branch_delete(repo, *args, check=True):
                if args[:2] == ("branch", "-D"):
                    return subprocess.CompletedProcess(args, 1, "", "refused")
                return real(repo, *args, check=check)

            with mock.patch.object(drafts, "_git", side_effect=refuse_branch_delete):
                self.assertEqual(first_run.clear_partial(ROOT, name), first_run.FAILED)

    def test_nothing_to_clear_is_a_no_op(self):
        self.assertEqual(first_run.clear_partial(ROOT, "first-run-" + uuid.uuid4().hex[:10]),
                         first_run.NOTHING)


class PointersTests(unittest.TestCase):
    def test_interactive_init_points_at_the_studio(self):
        harness = _load_cli()
        answers = iter(["Casey", "", "Developer", "", "Europe/Lisbon", "", ""] + [""] * len(harness.STANCE_NAMES))
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.dict(os.environ, {"HOME": temporary, "HARNESS_HOME": temporary}), \
                mock.patch("builtins.input", lambda _prompt: next(answers)), \
                mock.patch.object(harness.sys.stdin, "isatty", return_value=True), \
                mock.patch.object(harness, "_detect_github", return_value=""), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(harness.cmd_init(argparse.Namespace(force=False)), 0)
        self.assertTrue(out.getvalue().rstrip().splitlines()[-1].endswith("bin/citizen studio"),
                        out.getvalue())

    def test_the_installer_ends_on_the_studio_once_as_the_step_after_install(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = source_repo(Path(temporary) / "source")
            home = Path(temporary) / "home"
            home.mkdir()
            checkout = home / "repos" / "agent-harness"
            env = without_harness_vars()
            env.pop("HARNESS_QUIET", None)
            env.update({"HOME": str(home), "HARNESS_HOME": str(home), "HARNESS_CHECKOUT": str(checkout),
                        "HARNESS_REPO_URL": str(source), "HARNESS_INSTALL_NO_HOMEBREW": "1",
                        "HARNESS_INSTALL_NO_APPS": "1"})
            done = subprocess.run(["/bin/sh", str(ROOT / "scripts" / "install.sh")], capture_output=True,
                                  text=True, cwd=temporary, env=env, timeout=600)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.count("citizen studio"), 1, done.stdout)
        last = done.stdout.rstrip().splitlines()[-1]
        self.assertTrue(last.startswith("After install,"), last)
        self.assertTrue(last.endswith("%s/bin/citizen studio" % checkout), last)


if __name__ == "__main__":
    unittest.main()

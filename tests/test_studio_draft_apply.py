# SPDX-License-Identifier: MIT
"""Review a draft and apply it through the governed path: checks, the sync lock, core refusals,
and an applied state equal to running the commands the review shows."""
from __future__ import annotations

import json
import os
import shutil
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

from harness_core import reconcile  # noqa: E402
from harness_core.studio import apply as draft_apply  # noqa: E402
from harness_core.studio import drafts, module_authoring, selection_editing, server  # noqa: E402

import draft_support  # noqa: E402

CLI = ROOT / "bin" / "harness"
PASS = [sys.executable, "-c", "raise SystemExit(0)"]
SWITCHED = "cache-hygiene"
# Built at run time so this file never carries the secret shape the lint looks for.
SECRET = "AKIA" + "Q" * 16


def _rule(name, description="Greets the reader first."):
    return {"action": "add", "kind": "rules", "name": name, "description": description,
            "create_root": True}


def _tree(root):
    if not root.is_dir():
        return {}
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*")) if path.is_file()}


class Home:
    """One isolated user: its configuration, state and the environment that points at it."""

    def __init__(self, base, label, config):
        self.path = Path(os.path.realpath(str(base))) / label
        self.config = draft_apply.config_file(self.path)
        self.config.parent.mkdir(parents=True)
        self.config.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        self.state = self.path / ".local" / "state" / "agent-harness"
        self.dest = draft_apply.destination(self.path)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("HARNESS_") and key != "CLAUDE_CONFIG_DIR"}
        env.update({"HOME": str(self.path), "HARNESS_HOME": str(self.path),
                    "HARNESS_WORKTREE_ROOT": str(Path(base) / "worktrees")})
        self.env = env

    def cli(self, *args, timeout=300):
        return subprocess.run([sys.executable, str(CLI), *args], cwd=ROOT, env=self.env,
                              capture_output=True, text=True, timeout=timeout)

    def normalized(self, relative):
        path = self.path / relative
        return path.read_text(encoding="utf-8").replace(str(self.path), "<HOME>") if path.is_file() else None


class DraftApplyTests(unittest.TestCase):
    def setUp(self):
        config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        config["primitive_roots"] = []
        self.initial = config

    @contextmanager
    def real_draft(self, prefix):
        name = draft_support.draft_name(prefix + "-")
        with tempfile.TemporaryDirectory() as temporary:
            home = Home(temporary, "home", self.initial)
            created = home.cli("draft", "create", name, "--json", timeout=60)
            self.assertEqual(created.returncode, 0, created.stderr or created.stdout)
            try:
                with mock.patch.dict(os.environ, home.env, clear=True):
                    yield name, json.loads(created.stdout), home, Path(temporary)
            finally:
                draft_support.discard_draft(self, name, home.env)

    def _add_rule(self, name, revision, rule="greeting"):
        added = module_authoring.save(ROOT, name, revision, "add-" + rule, _rule(rule))
        self.assertTrue(added["saved"], added)
        return added["result"]["revision"]

    def _review(self, home, name):
        done = home.cli("draft", "review", name, "--json")
        self.assertIn(done.returncode, (0, 1), done.stderr)
        return json.loads(done.stdout)

    def _apply(self, home, name, revision):
        done = home.cli("draft", "apply", name, "--revision", revision, "--json")
        return done.returncode, json.loads(done.stdout)

    def assertNothingApplied(self, home):
        self.assertEqual(json.loads(home.config.read_text(encoding="utf-8")), self.initial)
        self.assertFalse(home.dest.exists())
        self.assertFalse((home.state / "manifest.json").exists())

    def test_applied_state_equals_running_the_commands_the_review_shows(self):
        """AC4: apply, then run the shown commands in a second identical home; they agree."""
        with self.real_draft("apply-equal") as (name, initial, home, base):
            revision = self._add_rule(name, initial["revision"])
            switched = selection_editing.save(ROOT, name, revision, "switch-off",
                                              {"rules." + SWITCHED: "off"})
            self.assertTrue(switched["saved"], switched)
            revision = switched["result"]["revision"]
            twin = Home(base, "twin", self.initial)

            review = self._review(home, name)
            self.assertTrue(review["can_apply"], review["refusals"])
            self.assertEqual(review["draft"]["revision"], revision)
            self.assertEqual([(item["path"], item["scope"]) for item in review["files"]], [
                ("personal-primitives/manifests.json", "personal"),
                ("personal-primitives/rules/greeting.md", "personal")])
            self.assertEqual({row["key"]: row["action"] for row in review["config"]},
                             {"primitive_roots": "set", "rules." + SWITCHED: "set"})
            roots = next(row for row in review["config"] if row["key"] == "primitive_roots")
            self.assertEqual(roots["after"], [str(home.dest)])
            self.assertEqual(review["checks"]["status"], "passed")
            self.assertIsInstance(review["budget"]["delta_lines"], int)
            steps = [item["step"] for item in review["commands"]]
            self.assertEqual(steps, ["root", "config", "config", "sync", "check"])
            self.assertTrue(review["nothing_applied"])
            self.assertNothingApplied(home)

            shown = self._review(twin, name)
            self.assertTrue(shown["can_apply"], shown["refusals"])

            code, result = self._apply(home, name, revision)
            self.assertEqual(code, 0, result)
            self.assertEqual(result["status"], "applied")
            self.assertIn(result["doctor"]["status"], ("passed", "attention"))
            self.assertIn("drift: none", [check["message"] for check in result["doctor"]["checks"]])

            for item in shown["commands"]:
                if item["step"] == "check":
                    continue
                command = item["command"]
                if command.startswith("citizen "):
                    command = "%s %s %s" % (sys.executable, CLI, command[len("citizen "):])
                ran = subprocess.run(["/bin/sh", "-c", command], cwd=ROOT, env=twin.env,
                                     capture_output=True, text=True, timeout=300)
                self.assertEqual(ran.returncode, 0, command + "\n" + ran.stdout + ran.stderr)

            self.assertEqual(home.normalized(".config/agent-harness/config.json"),
                             twin.normalized(".config/agent-harness/config.json"))
            applied = json.loads(home.config.read_text(encoding="utf-8"))
            self.assertEqual(applied["primitive_roots"], [str(home.dest)])
            self.assertEqual(applied["rules"][SWITCHED], "off")
            self.assertEqual(_tree(home.dest), _tree(twin.dest))
            self.assertIn("rules/greeting.md", _tree(home.dest))
            ledger = ".local/state/agent-harness/ownership.json"
            self.assertEqual(home.normalized(ledger), twin.normalized(ledger))
            manifests = [json.loads(side.normalized(".local/state/agent-harness/manifest.json"))
                         for side in (home, twin)]
            for manifest in manifests:
                manifest.pop("synced_at")
            self.assertEqual(manifests[0], manifests[1])
            projected = [sorted(path.relative_to(side.path / ".claude").as_posix()
                                for path in (side.path / ".claude").rglob("*"))
                         for side in (home, twin)]
            self.assertEqual(projected[0], projected[1])
            self.assertTrue(any(item.endswith("/greeting.md") for item in projected[0]), projected[0])

            # The decision log and the apply journal name the draft and the actor.
            events = [json.loads(line) for line in
                      (home.state / "decisions.jsonl").read_text(encoding="utf-8").splitlines()]
            event = next(row for row in events if row.get("event") == "studio.apply")
            self.assertEqual(event["detail"]["draft"], name)
            self.assertEqual(event["detail"]["actor"], "citizen")
            self.assertEqual(event["detail"]["outcome"], "completed")
            journal = [json.loads(line) for line in
                       draft_apply.journal_path(home.path).read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["phase"] for row in journal], ["intent", "completed"])
            self.assertEqual({row["draft"] for row in journal}, {name})
            recorded = {item["key"]: item["applied"] for item in journal[0]["config"]}
            self.assertEqual(recorded["primitive_roots"], [str(home.dest)])
            self.assertEqual(json.loads(draft_apply._decoded(journal[0]["prior_config"])), self.initial)

            # Applied: a second review has nothing left to do, and apply refuses rather than repeat.
            again = self._review(home, name)
            self.assertEqual([item["code"] for item in again["refusals"]], ["nothing-to-apply"])
            self.assertEqual({row["action"] for row in again["config"]}, {"none"})

    def test_lint_findings_refuse_apply_with_the_findings(self):
        """AC1."""
        with self.real_draft("apply-lint") as (name, initial, home, _base):
            revision = self._add_rule(name, initial["revision"])
            leaky = drafts.checkpoint(
                ROOT, name, revision, "leak",
                files={"personal-primitives/rules/leaky.md": ("# Leaky\n\nkey " + SECRET + "\n").encode()},
                check_command=PASS)
            review = self._review(home, name)
            self.assertFalse(review["can_apply"])
            self.assertEqual(review["checks"]["status"], "failed")
            self.assertTrue(any("leaky.md" in line for line in review["checks"]["findings"]),
                            review["checks"]["findings"])
            self.assertIn("checks-failed", [item["code"] for item in review["refusals"]])
            code, result = self._apply(home, name, leaky["revision"])
            self.assertEqual(code, 1)
            self.assertEqual(result["status"], "refused")
            self.assertEqual(result["error_code"], "checks-failed")
            self.assertTrue(any("leaky.md" in line for line in result["review"]["checks"]["findings"]))
            self.assertNothingApplied(home)
            self.assertFalse(draft_apply.journal_path(home.path).exists())

    def test_a_held_sync_lock_refuses_apply_naming_its_holder(self):
        """AC2: refused at once, naming the holder, with nothing written."""
        with self.real_draft("apply-lock") as (name, initial, home, _base):
            revision = self._add_rule(name, initial["revision"])
            for directory, holder in ((home.state, "citizen sync"),
                                      (home.config.parent, "citizen config set mode")):
                with self.subTest(holder=holder):
                    with reconcile.lock(directory, holder=holder):
                        code, result = self._apply(home, name, revision)
                    self.assertEqual(code, 1)
                    self.assertEqual(result["error_code"], "busy")
                    self.assertTrue(result["holder"].startswith(holder + " (pid %d" % os.getpid()),
                                    result["holder"])
                    self.assertIn("nothing was applied", result["message"])
                    self.assertNothingApplied(home)
                    self.assertFalse(draft_apply.journal_path(home.path).exists())

    def _operations(self, home, sync, config_set=None, record=None, set_keys=None):
        set_keys = [] if set_keys is None else set_keys

        def default_set(key, value):
            set_keys.append(key)
            current = json.loads(home.config.read_text(encoding="utf-8"))
            current[key] = json.loads(value) if key == "primitive_roots" else value
            home.config.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")

        return draft_apply.Operations(
            lock=lambda holder: reconcile.lock(home.state, holder=holder),
            config_lock=lambda holder: reconcile.lock(home.config.parent, holder=holder),
            config_set=config_set or default_set, config_unset=lambda key: None,
            sync=sync, doctor=lambda: [], record=record or (lambda row: None))

    @staticmethod
    def _journal(home):
        return [json.loads(line) for line in
                draft_apply.journal_path(home.path).read_text(encoding="utf-8").splitlines()]

    def test_apply_holds_the_sync_lock_so_a_sync_cannot_interleave(self):
        """AC2: while apply runs, `citizen sync` is refused naming the apply. Both syncs here are
        refused, the re-projection included, so the restore is reported incomplete."""
        with self.real_draft("apply-interleave") as (name, initial, home, _base):
            revision = self._add_rule(name, initial["revision"])
            outputs = []

            def blocked_sync(dry):
                if dry:
                    return {"code": 0, "attention": [], "refused": False}
                ran = home.cli("sync")
                outputs.append(ran.stdout + ran.stderr)
                return {"code": ran.returncode, "attention": [], "refused": True}

            set_keys = []
            result = draft_apply.apply(ROOT, name, revision,
                                       self._operations(home, blocked_sync, set_keys=set_keys),
                                       actor="studio", home=home.path)
            self.assertEqual(result["status"], "failed", result)
            self.assertEqual(result["error_code"], "sync-refused")
            self.assertFalse(result["restored"])
            self.assertIn("did not complete", result["message"])
            self.assertEqual(set_keys, ["primitive_roots"])
            self.assertEqual(len(outputs), 2)
            for output in outputs:
                self.assertIn("citizen draft apply %s from Studio (pid %d" % (name, os.getpid()), output)
            self.assertNothingApplied(home)
            journal = self._journal(home)
            self.assertEqual([row["phase"] for row in journal], ["intent", "failed"])
            self.assertFalse(journal[1]["restored"])

    def test_a_failed_sync_restores_and_reports_restored_when_the_reprojection_settles(self):
        with self.real_draft("apply-restore") as (name, initial, home, _base):
            revision = self._add_rule(name, initial["revision"])
            outcomes = iter([{"code": 0, "attention": [], "refused": False},
                             {"code": 2, "attention": ["new item"], "refused": False},
                             {"code": 0, "attention": [], "refused": False}])
            result = draft_apply.apply(ROOT, name, revision,
                                       self._operations(home, lambda dry: next(outcomes)), home=home.path)
            self.assertEqual((result["status"], result["error_code"]), ("failed", "sync-refused"))
            self.assertIn("new item", result["message"])
            self.assertTrue(result["restored"])
            self.assertNothingApplied(home)

    def test_attention_items_that_predate_the_draft_do_not_fail_the_apply(self):
        """Sync exits 2 for an unmanaged file the user already had; apply still completes."""
        with self.real_draft("apply-attention") as (name, initial, home, _base):
            revision = self._add_rule(name, initial["revision"])
            commands = home.path / ".claude" / "commands"
            commands.mkdir(parents=True)
            (commands / "review.md").write_text("mine\n", encoding="utf-8")
            code, result = self._apply(home, name, revision)
            self.assertEqual((code, result["status"]), (0, "applied"), result["message"])
            self.assertEqual((commands / "review.md").read_text(encoding="utf-8"), "mine\n")
            self.assertTrue((home.dest / "rules" / "greeting.md").is_file())

    def test_an_interrupt_restores_then_propagates(self):
        with self.real_draft("apply-interrupt") as (name, initial, home, _base):
            revision = self._add_rule(name, initial["revision"])

            def interrupted(key, value):
                raise KeyboardInterrupt()

            settled = {"code": 0, "attention": [], "refused": False}
            with self.assertRaises(KeyboardInterrupt):
                draft_apply.apply(ROOT, name, revision,
                                  self._operations(home, lambda dry: settled, config_set=interrupted),
                                  home=home.path)
            self.assertNothingApplied(home)
            self.assertEqual([row["phase"] for row in self._journal(home)], ["intent", "failed"])

    def _interrupted(self, home, name, revision):
        """Leave an apply as a kill would: files and keys written, only the intent journalled."""
        settled = {"code": 0, "attention": [], "refused": False}

        def killed(dry):
            if dry:
                return settled
            raise draft_apply.ApplyError("killed", "killed")

        real = draft_apply._journal
        with mock.patch.object(draft_apply, "_journal",
                               side_effect=lambda home_path, row: real(home_path, row)
                               if row["phase"] == "intent" else None), \
                mock.patch.object(draft_apply, "_restore", return_value=False):
            draft_apply.apply(ROOT, name, revision, self._operations(home, killed), home=home.path)
        self.assertTrue(home.dest.joinpath("rules", "greeting.md").is_file())
        self.assertEqual(len(draft_apply.unfinished_applies(home.path)), 1)

    def test_an_interrupted_apply_is_restored_before_anything_else(self):
        with self.real_draft("apply-recover") as (name, initial, home, _base):
            revision = self._add_rule(name, initial["revision"])
            self._interrupted(home, name, revision)
            review = self._review(home, name)
            self.assertIn("interrupted-apply", [item["code"] for item in review["refusals"]])
            self.assertEqual(review["interrupted"]["draft"], name)
            code, result = self._apply(home, name, revision)
            self.assertEqual((code, result["status"], result["error_code"]), (1, "recovered", ""), result)
            self.assertTrue(result["restored"])
            self.assertNothingApplied_but_synced(home)
            self.assertEqual(self._journal(home)[-1]["phase"], "recovered")
            self.assertEqual(draft_apply.unfinished_applies(home.path), [])
            code, result = self._apply(home, name, revision)
            self.assertEqual((code, result["status"]), (0, "applied"), result["message"])

    def _recover_cli(self, home, *args):
        done = home.cli("draft", "recover", "--json", *args)
        return done.returncode, json.loads(done.stdout)

    def test_a_second_interrupt_during_the_restore_keeps_the_intent_open(self):
        with self.real_draft("apply-double-interrupt") as (name, initial, home, _base):
            revision = self._add_rule(name, initial["revision"])
            settled = {"code": 0, "attention": [], "refused": False}

            def first(key, value):
                raise KeyboardInterrupt()

            with mock.patch.object(draft_apply, "_restore_root", side_effect=KeyboardInterrupt()), \
                    self.assertRaises(KeyboardInterrupt):
                draft_apply.apply(ROOT, name, revision,
                                  self._operations(home, lambda dry: settled, config_set=first),
                                  home=home.path)
            phases = [(row["phase"], row.get("restored")) for row in self._journal(home)]
            self.assertEqual(phases, [("intent", None), ("failed", False)])
            self.assertEqual(len(draft_apply.unfinished_applies(home.path)), 1)
            self.assertTrue(home.dest.joinpath("rules", "greeting.md").is_file())
            code, result = self._recover_cli(home)
            self.assertEqual((code, result["status"]), (0, "recovered"), result["message"])
            self.assertNothingApplied_but_synced(home)
            self.assertEqual(draft_apply.unfinished_applies(home.path), [])

    def test_recovery_restores_only_what_the_interrupted_apply_wrote(self):
        with self.real_draft("apply-recover-scope") as (name, initial, home, _base):
            revision = self._add_rule(name, initial["revision"])
            self._interrupted(home, name, revision)
            live = json.loads(home.config.read_text(encoding="utf-8"))
            live["identity"]["name"] = "Changed Later"
            home.config.write_text(json.dumps(live, indent=2) + "\n", encoding="utf-8")
            code, result = self._apply(home, name, revision)
            self.assertEqual(result["status"], "recovered", result["message"])
            restored = json.loads(home.config.read_text(encoding="utf-8"))
            self.assertEqual(restored["identity"]["name"], "Changed Later")
            self.assertEqual(restored["primitive_roots"], [])
            self.assertEqual(_tree(home.dest), {})

    def test_recover_can_abandon_explicitly_and_checks_the_draft_named(self):
        with self.real_draft("apply-abandon") as (name, initial, home, _base):
            revision = self._add_rule(name, initial["revision"])
            self._interrupted(home, name, revision)
            code, result = self._recover_cli(home, "--abandon", "--draft", "someone-else")
            self.assertEqual((code, result["error_code"]), (1, "confirmation-mismatch"))
            self.assertEqual(len(draft_apply.unfinished_applies(home.path)), 1)
            code, result = self._recover_cli(home, "--abandon", "--draft", name)
            self.assertEqual((code, result["status"]), (0, "abandoned"), result["message"])
            self.assertEqual(draft_apply.unfinished_applies(home.path), [])
            self.assertTrue(home.dest.joinpath("rules", "greeting.md").is_file())
            self.assertEqual(self._journal(home)[-1]["phase"], "abandoned")
            code, result = self._recover_cli(home)
            self.assertEqual((code, result["error_code"]), (1, "nothing-to-recover"))

    def test_an_interrupt_during_recovery_propagates_and_leaves_the_intent_open(self):
        with self.real_draft("apply-recover-interrupt") as (name, initial, home, _base):
            revision = self._add_rule(name, initial["revision"])
            self._interrupted(home, name, revision)

            def interrupted_sync(dry):
                raise KeyboardInterrupt()

            with self.assertRaises(KeyboardInterrupt):
                draft_apply.recover(self._operations(home, interrupted_sync), home=home.path)
            self.assertEqual(len(draft_apply.unfinished_applies(home.path)), 1)

    def test_a_review_beside_a_running_apply_does_not_call_it_interrupted(self):
        with self.real_draft("apply-running") as (name, initial, home, _base):
            revision = self._add_rule(name, initial["revision"])
            self._interrupted(home, name, revision)
            with reconcile.lock(home.state, holder="citizen draft apply " + name):
                review = draft_apply.review(ROOT, name, home.path)
            codes = [item["code"] for item in review["refusals"]]
            self.assertIn("apply-running", codes)
            self.assertNotIn("interrupted-apply", codes)
            self.assertIsNone(review["interrupted"])
            review = draft_apply.review(ROOT, name, home.path)
            self.assertIn("interrupted-apply", [item["code"] for item in review["refusals"]])
            self.assertEqual(review["interrupted"]["draft"], name)

    def test_checks_read_the_revision_not_the_working_copy(self):
        with self.real_draft("apply-check-revision") as (name, initial, home, _base):
            revision = self._add_rule(name, initial["revision"])
            leaked = drafts.checkpoint(
                ROOT, name, revision, "leak",
                files={"personal-primitives/rules/leaky.md": ("# Leaky\n\nkey " + SECRET + "\n").encode()},
                check_command=PASS)
            worktree, state = drafts.find(ROOT, name)
            leaky = worktree / "personal-primitives" / "rules" / "leaky.md"
            leaky.write_text("# Leaky\n\nclean now\n", encoding="utf-8")
            try:
                raw = drafts.read_config(ROOT, name)["config"]
                before = draft_support.draft_worktree_registered(name)
                checks = draft_apply.checks_for(ROOT, worktree, raw, leaked["revision"])
                self.assertEqual(checks["status"], "failed")
                self.assertTrue(any("leaky.md" in line for line in checks["findings"]), checks)
                listed = subprocess.run(["git", "-C", str(ROOT), "worktree", "list", "--porcelain"],
                                        capture_output=True, text=True, check=True).stdout
                self.assertNotIn("studio-apply-check-", listed)
                self.assertEqual(before, draft_support.draft_worktree_registered(name))
            finally:
                subprocess.run(["git", "-C", str(worktree), "checkout", "--", "."], check=True)

    def assertNothingApplied_but_synced(self, home):
        self.assertEqual(json.loads(home.config.read_text(encoding="utf-8")), self.initial)
        self.assertEqual(_tree(home.dest), {})

    def test_an_interrupted_apply_with_a_later_edit_is_refused_and_left_alone(self):
        with self.real_draft("apply-recover-conflict") as (name, initial, home, _base):
            revision = self._add_rule(name, initial["revision"])
            self._interrupted(home, name, revision)
            home.dest.joinpath("rules", "greeting.md").write_text("edited later\n", encoding="utf-8")
            before = home.config.read_bytes()
            code, result = self._apply(home, name, revision)
            self.assertEqual((code, result["error_code"]), (1, "interrupted-apply-conflict"))
            self.assertIn("greeting.md", result["message"])
            self.assertEqual(home.dest.joinpath("rules", "greeting.md").read_text(encoding="utf-8"),
                             "edited later\n")
            self.assertEqual(home.config.read_bytes(), before)
            self.assertEqual(len(draft_apply.unfinished_applies(home.path)), 1)

    def test_an_unrecordable_outcome_is_reported_not_raised(self):
        with self.real_draft("apply-journal") as (name, initial, home, _base):
            revision = self._add_rule(name, initial["revision"])
            settled = {"code": 0, "attention": [], "refused": False}
            real = draft_apply._journal

            def failing(home_path, row):
                if row["phase"] != "intent":
                    raise OSError("disk full")
                real(home_path, row)

            with mock.patch.object(draft_apply, "_journal", side_effect=failing):
                result = draft_apply.apply(ROOT, name, revision,
                                           self._operations(home, lambda dry: settled), home=home.path)
            self.assertEqual(result["status"], "applied")
            self.assertIn("could not record this outcome (disk full)", result["message"])

    def test_checks_run_before_the_sync_lock_is_taken(self):
        with self.real_draft("apply-lock-order") as (name, initial, home, _base):
            revision = self._add_rule(name, initial["revision"])
            free = []
            real = draft_apply._checks

            def probing(worktree, mapped):
                with reconcile.lock(home.state):
                    free.append(True)
                return real(worktree, mapped)

            settled = {"code": 0, "attention": [], "refused": False}
            with mock.patch.object(draft_apply, "_checks", side_effect=probing):
                result = draft_apply.apply(ROOT, name, revision,
                                           self._operations(home, lambda dry: settled), home=home.path)
            self.assertEqual(result["status"], "applied", result)
            self.assertEqual(free, [True])

    def test_a_dirty_working_copy_is_refused_so_checked_content_is_written_content(self):
        with self.real_draft("apply-dirty") as (name, initial, home, _base):
            revision = self._add_rule(name, initial["revision"])
            leaked = drafts.checkpoint(
                ROOT, name, revision, "leak",
                files={"personal-primitives/rules/leaky.md": ("# Leaky\n\nkey " + SECRET + "\n").encode()},
                check_command=PASS)
            worktree = drafts.find(ROOT, name)[0]
            leaky = worktree / "personal-primitives" / "rules" / "leaky.md"
            leaky.write_text("# Leaky\n\nclean now\n", encoding="utf-8")
            try:
                review = self._review(home, name)
                self.assertIn("draft-dirty", [item["code"] for item in review["refusals"]])
                code, result = self._apply(home, name, leaked["revision"])
                self.assertEqual((code, result["error_code"]), (1, "draft-dirty"))
                self.assertNothingApplied(home)
            finally:
                subprocess.run(["git", "-C", str(worktree), "checkout", "--", "."], check=True)

    def test_a_link_inside_the_personal_root_is_never_followed(self):
        with self.real_draft("apply-link") as (name, initial, home, base):
            revision = self._add_rule(name, initial["revision"])
            outside = base / "outside"
            outside.mkdir()
            home.dest.mkdir(parents=True)
            (home.dest / "rules").symlink_to(outside, target_is_directory=True)
            review = self._review(home, name)
            self.assertIn("root-symlink", [item["code"] for item in review["refusals"]])
            code, result = self._apply(home, name, revision)
            self.assertEqual(code, 1)
            self.assertEqual(list(outside.iterdir()), [])

    def test_decision_logging_switched_off_writes_no_ledger(self):
        self.initial = dict(self.initial, telemetry=dict(self.initial.get("telemetry") or {}, decisions=False))
        with self.real_draft("apply-quiet-ledger") as (name, initial, home, _base):
            revision = self._add_rule(name, initial["revision"])
            code, result = self._apply(home, name, revision)
            self.assertEqual((code, result["status"]), (0, "applied"), result["message"])
            self.assertFalse((home.state / "decisions.jsonl").exists())
            self.assertEqual([row["phase"] for row in self._journal(home)], ["intent", "completed"])

    def test_a_core_edit_is_refused_with_a_fork_and_a_contribution_branch(self):
        """AC3."""
        with self.real_draft("apply-core") as (name, initial, home, _base):
            core = "primitives/rules/" + SWITCHED + ".md"
            worktree = drafts.find(ROOT, name)[0]
            edited = (worktree / core).read_text(encoding="utf-8") + "\nAn in-place edit.\n"
            saved = drafts.checkpoint(ROOT, name, initial["revision"], "core-edit",
                                      files={core: edited.encode("utf-8")}, check_command=PASS)
            review = self._review(home, name)
            self.assertFalse(review["can_apply"])
            self.assertIn("core-change", [item["code"] for item in review["refusals"]])
            self.assertEqual(review["files"], [{"path": core, "status": "modified", "scope": "core"}])
            self.assertEqual(review["core"]["files"], [core])
            self.assertEqual(review["core"]["fork"]["modules"],
                             [{"source": "core:rules:" + SWITCHED, "kind": "rules", "name": SWITCHED}])
            self.assertIn("citizen draft module plan " + name, review["core"]["fork"]["command"])
            branch = review["core"]["branch"]
            self.assertEqual((branch["name"], branch["revision"]), ("draft/" + name, saved["revision"]))
            self.assertIn("push origin draft/%s:refs/heads/contrib/%s" % (name, name), branch["commands"][0])
            self.assertIn("gh pr create --head contrib/" + name, branch["commands"][1])
            code, result = self._apply(home, name, saved["revision"])
            self.assertEqual((code, result["error_code"]), (1, "core-change"))
            self.assertNothingApplied(home)
            self.assertEqual((ROOT / core).read_text(encoding="utf-8") + "\nAn in-place edit.\n", edited)

    def test_a_later_live_edit_is_never_overwritten_and_a_stale_revision_is_refused(self):
        with self.real_draft("apply-diverged") as (name, initial, home, _base):
            revision = self._add_rule(name, initial["revision"])
            code, stale = self._apply(home, name, initial["revision"])
            self.assertEqual((code, stale["error_code"]), (1, "stale-revision"))
            home.dest.joinpath("rules").mkdir(parents=True)
            home.dest.joinpath("rules", "greeting.md").write_text("mine\n", encoding="utf-8")
            live = json.loads(home.config.read_text(encoding="utf-8"))
            live["primitive_roots"] = ["/somewhere/else"]
            home.config.write_text(json.dumps(live, indent=2) + "\n", encoding="utf-8")
            review = self._review(home, name)
            codes = [item["code"] for item in review["refusals"]]
            self.assertIn("root-diverged", codes)
            self.assertIn("config-diverged", codes)
            row = next(item for item in review["config"] if item["key"] == "primitive_roots")
            self.assertEqual((row["action"], row["live"]), ("conflict", ["/somewhere/else"]))
            code, result = self._apply(home, name, revision)
            self.assertEqual(code, 1)
            self.assertEqual(home.dest.joinpath("rules", "greeting.md").read_text(encoding="utf-8"), "mine\n")
            self.assertEqual(json.loads(home.config.read_text(encoding="utf-8")), live)

    def test_review_of_a_missing_draft_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Home(temporary, "home", self.initial)
            review = draft_apply.review(ROOT, "no-such-draft-" + uuid.uuid4().hex[:8], home.path)
        self.assertFalse(review["can_apply"])
        self.assertEqual(review["refusals"][0]["code"], "not-found")


class PlanningTests(unittest.TestCase):
    def test_keys_flatten_to_what_config_set_accepts_and_values_render_as_its_arguments(self):
        flat = draft_apply._flatten({"mode": "minimal", "stances": {"voice": "concise"},
                                     "rules": {}, "claude": {"manage": False},
                                     "governance": {"jev": {"timeout": 3}},
                                     "init_defaults": {"stances": {"voice": "concise"}}})
        self.assertEqual(flat, {"mode": "minimal", "stances.voice": "concise", "claude.manage": False,
                                "governance.jev.timeout": 3})
        self.assertEqual(draft_apply._value_text(False), "false")
        self.assertEqual(draft_apply._value_text(["/a"]), '["/a"]')
        self.assertEqual(draft_apply._value_text("on"), "on")
        self.assertEqual(draft_apply._citizen({"action": "set", "key": "identity.name", "value": "A B"}),
                         "citizen config set identity.name 'A B'")

    def test_only_the_drafts_own_root_is_rewritten_out_of_the_checkout(self):
        repo, worktree, dest = Path("/repo"), Path("/wt/draft-x"), Path("/h/.config/agent-harness/personal-primitives")
        self.assertEqual(draft_apply._root_target(
            repo, worktree, ["/repo/personal-primitives", "/elsewhere", str(dest)], dest),
            [str(dest), "/elsewhere"])
        own, others = draft_apply._registered_root(
            repo, worktree, {"primitive_roots": ["/repo/personal-primitives", "/repo/other", "/elsewhere"]})
        self.assertEqual((own, others), (True, ["/repo/other"]))

    def test_a_restore_keeps_permission_bits_and_drops_directories_apply_made(self):
        with tempfile.TemporaryDirectory() as temporary:
            dest = Path(temporary) / "personal-primitives"
            (dest / "bin").mkdir(parents=True)
            script = dest / "bin" / "run.sh"
            script.write_bytes(b"old\n")
            script.chmod(0o755)
            operations = [
                {"path": "bin/run.sh", "action": "write", "content": b"new\n", "prior": b"old\n",
                 "prior_mode": 0o755, "executable": False},
                {"path": "rules/deep/new.md", "action": "write", "content": b"x\n", "prior": None,
                 "prior_mode": 0, "executable": False},
            ]
            created = []
            draft_apply._write_root(dest, operations, created)
            self.assertEqual(created, [str(dest / "rules"), str(dest / "rules" / "deep")])
            draft_apply._restore_root(dest, operations, created)
            self.assertEqual(script.read_bytes(), b"old\n")
            self.assertEqual(script.stat().st_mode & 0o777, 0o755)
            self.assertFalse((dest / "rules").exists())
            self.assertTrue(dest.is_dir())

    def test_a_path_through_a_link_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            dest = Path(temporary) / "root"
            dest.mkdir()
            (dest / "rules").symlink_to(Path(temporary), target_is_directory=True)
            with self.assertRaises(draft_apply.ApplyError) as caught:
                draft_apply._contained(dest, "rules/x.md")
            self.assertEqual(caught.exception.code, "root-symlink")
            for bad in ("../x", "/etc/x", ""):
                with self.assertRaises(draft_apply.ApplyError):
                    draft_apply._contained(dest, bad)
            self.assertEqual(draft_apply._contained(dest, "skills/a/SKILL.md"), dest / "skills/a/SKILL.md")

    def test_journal_rows_are_written_whole_and_never_joined_to_a_cut_row(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            path = draft_apply.journal_path(home)
            path.parent.mkdir(parents=True)
            path.write_bytes(b'{"apply_id": "cut", "pha')
            real = os.write
            with mock.patch.object(draft_apply.os, "write",
                                   side_effect=lambda fd, data: real(fd, bytes(data[:7]))):
                draft_apply._journal(home, {"apply_id": "a1", "phase": "intent", "draft": "x" * 200})
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], '{"apply_id": "cut", "pha')
            self.assertEqual(json.loads(lines[1])["draft"], "x" * 200)
            self.assertEqual([row["apply_id"] for row in draft_apply.unfinished_applies(home)], ["a1"])

    def test_a_failed_row_closes_its_intent_only_when_the_restore_finished(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            for apply_id, restored in (("a", True), ("b", False)):
                draft_apply._journal(home, {"apply_id": apply_id, "phase": "intent"})
                draft_apply._journal(home, {"apply_id": apply_id, "phase": "failed", "restored": restored})
            self.assertEqual([row["apply_id"] for row in draft_apply.unfinished_applies(home)], ["b"])

    def test_only_new_attention_items_or_a_refusal_unsettle_a_sync(self):
        settled = draft_apply._sync_settled
        self.assertEqual(settled({"code": 0}, []), (True, []))
        self.assertEqual(settled({"code": 2, "attention": ["a"], "refused": False}, ["a"]), (True, []))
        self.assertEqual(settled({"code": 2, "attention": ["a", "b"], "refused": False}, ["a"]), (False, ["b"]))
        self.assertEqual(settled({"code": 2, "attention": [], "refused": True}, []), (False, []))
        self.assertEqual(settled({"code": 1, "attention": [], "refused": False}, []), (False, []))

    def test_the_lock_names_its_holder_and_clears_the_record_on_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with reconcile.lock(directory, holder="citizen sync"):
                with self.assertRaisesRegex(ValueError, r"running: citizen sync \(pid %d" % os.getpid()):
                    with reconcile.lock(directory):
                        self.fail("second writer entered")
            self.assertIsNone(reconcile.lock_holder(directory))
            (directory / reconcile.HOLDER_FILE).write_text('{"holder": "crashed"}', encoding="utf-8")
            with reconcile.lock(directory):
                self.assertIsNone(reconcile.lock_holder(directory))
            with reconcile.lock(directory):
                with self.assertRaises(ValueError) as caught:
                    with reconcile.lock(directory):
                        pass
            self.assertEqual(str(caught.exception), "another harness configuration operation is running")


class RouteTests(unittest.TestCase):
    def test_apply_routes_name_their_cli_equivalents(self):
        routes = {route.path: route for route in server.ROUTES.entries}
        self.assertEqual(routes["/api/configure/apply/review"].cli_command, draft_apply.CLI_COMMANDS["review"])
        self.assertEqual(routes["/api/configure/apply"].cli_command, draft_apply.CLI_COMMANDS["apply"])
        self.assertEqual(routes["/api/configure/apply/recover"].cli_command, draft_apply.CLI_COMMANDS["recover"])
        for path in ("/api/configure/apply/review", "/api/configure/apply", "/api/configure/apply/recover"):
            self.assertEqual(routes[path].method, "POST")
            self.assertIsNone(routes[path].parity_exemption)

    def test_a_second_concurrent_review_is_refused_not_queued(self):
        handler = mock.Mock()
        handler.request_json = {"draft": "tuning"}
        route = {route.path: route for route in server.ROUTES.entries}["/api/configure/apply/review"]
        self.assertTrue(server._REVIEW_SLOTS.acquire(blocking=False))
        try:
            with mock.patch.object(server.draft_apply, "review") as review:
                server._draft_apply_review(handler, route)
            review.assert_not_called()
            handler._error.assert_called_once_with(429, "review_busy")
        finally:
            server._REVIEW_SLOTS.release()
        with mock.patch.object(server.draft_apply, "review",
                               return_value=draft_apply.unavailable("not-found", "gone")):
            handler = mock.Mock()
            handler.request_json = {"draft": "tuning"}
            server._draft_apply_review(handler, route)
        handler._json.assert_called_once()
        self.assertTrue(server._REVIEW_SLOTS.acquire(blocking=False))
        server._REVIEW_SLOTS.release()

    def test_the_studio_apply_runs_the_cli_without_the_quiet_flag(self):
        completed = subprocess.CompletedProcess([], 0, stdout='noise\n{"status": "applied"}\n', stderr="")
        with mock.patch.dict(os.environ, {"HARNESS_QUIET": "1"}), \
                mock.patch.object(server.subprocess, "run", return_value=completed) as run:
            payload = server._run_draft_apply(ROOT, "tuning", "a" * 40)
        self.assertEqual(payload, {"status": "applied"})
        argv = run.call_args[0][0]
        self.assertEqual(argv[1:], [str(ROOT / "bin" / "harness"), "draft", "apply", "tuning",
                                    "--revision", "a" * 40, "--via-studio", "--json"])
        self.assertNotIn("HARNESS_QUIET", run.call_args[1]["env"])
        with mock.patch.object(server.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 2, stdout="", stderr="boom")):
            failed = server._run_draft_apply(ROOT, "tuning", "a" * 40)
        self.assertEqual((failed["status"], failed["error_code"]), ("failed", "apply-unavailable"))
        server.APPLY_RESULT.validate(failed)


if __name__ == "__main__":
    unittest.main()

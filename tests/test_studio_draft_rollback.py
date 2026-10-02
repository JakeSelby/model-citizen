# SPDX-License-Identifier: MIT
"""Roll back an applied draft from its apply journal: byte-for-byte restores, refusals that name
the later apply or edit in the way, a rollback that is itself journalled and reversible, and an
Activity entry linked to the apply it reversed."""
from __future__ import annotations

import json
import os
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
from harness_core.studio import activity, module_authoring, selection_editing  # noqa: E402
from harness_core.studio import apply as draft_apply  # noqa: E402
from harness_core.studio import rollback as draft_rollback  # noqa: E402

import draft_support  # noqa: E402
from test_studio_draft_apply import Home, _rule  # noqa: E402

SWITCHED = "cache-hygiene"
SETTLED = {"code": 0, "attention": [], "refused": False}
STATE = Path(".local") / "state" / "agent-harness"


def _snapshot(home):
    """Every file in the home outside `.local/state`, by relative path.

    State is journals, logs and tools' own records (the manifest is compared on its own). The empty
    `sync.lock` file the configuration lock leaves beside the configuration is the lock's, not
    configuration, so it is left out too.
    """
    state = home.path / ".local" / "state"
    return {path.relative_to(home.path).as_posix(): path.read_bytes()
            for path in sorted(home.path.rglob("*"))
            if path.is_file() and state not in path.parents
            and path.name not in ("sync.lock", "sync.lock.holder")}


def _manifest(home):
    path = home.path / STATE / "manifest.json"
    if not path.is_file():
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest.pop("synced_at", None)
    return manifest


class RollbackTests(unittest.TestCase):
    def setUp(self):
        config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        config["primitive_roots"] = []
        self.initial = config

    @contextmanager
    def home(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Home(temporary, "home", self.initial)
            self.drafts = []
            try:
                with mock.patch.dict(os.environ, home.env, clear=True):
                    yield home
            finally:
                # Discarded while the home and its worktree root still exist.
                for name in self.drafts:
                    draft_support.discard_draft(self, name, home.env)

    def draft(self, home, prefix):
        name = prefix + "-" + uuid.uuid4().hex[:10]
        created = home.cli("draft", "create", name, "--json", timeout=60)
        self.assertEqual(created.returncode, 0, created.stderr or created.stdout)
        self.drafts.append(name)
        return name, json.loads(created.stdout)["revision"]

    def add_rule(self, name, revision, rule="greeting"):
        added = module_authoring.save(ROOT, name, revision, "add-" + rule, _rule(rule))
        self.assertTrue(added["saved"], added)
        return added["result"]["revision"]

    def switch(self, name, revision, value):
        switched = selection_editing.save(ROOT, name, revision, "switch-" + value,
                                          {"rules." + SWITCHED: value})
        self.assertTrue(switched["saved"], switched)
        return switched["result"]["revision"]

    def apply(self, home, name, revision):
        done = home.cli("draft", "apply", name, "--revision", revision, "--json")
        result = json.loads(done.stdout)
        self.assertEqual((done.returncode, result["status"]), (0, "applied"), result["message"])
        return result["apply_id"]

    def cli(self, home, *args):
        done = home.cli("draft", "rollback", *args, "--json")
        return done.returncode, json.loads(done.stdout)

    def operations(self, home, sync, record=None):
        return draft_apply.Operations(
            lock=lambda holder: reconcile.lock(home.state, holder=holder),
            config_lock=lambda holder: reconcile.lock(home.config.parent, holder=holder),
            config_set=lambda key, value: None, config_unset=lambda key: None,
            sync=sync, doctor=lambda: [], record=record or (lambda row: None))

    @staticmethod
    def journal(home):
        return [json.loads(line) for line in
                draft_apply.journal_path(home.path).read_text(encoding="utf-8").splitlines()]

    def test_rollback_restores_config_root_and_projections_byte_for_byte(self):
        """AC1 and AC3, and a rollback is itself rolled back."""
        with self.home() as home:
            synced = home.cli("sync")
            self.assertIn(synced.returncode, (0, 2), synced.stdout + synced.stderr)
            before, manifest = _snapshot(home), _manifest(home)
            name, revision = self.draft(home, "rollback-bytes")
            revision = self.switch(name, self.add_rule(name, revision), "off")
            apply_id = self.apply(home, name, revision)
            applied = _snapshot(home)
            self.assertNotEqual(applied, before)
            self.assertTrue((home.dest / "rules" / "greeting.md").is_file())

            code, preview = self.cli(home, apply_id, "--preview")
            self.assertEqual(code, 0, preview["refusals"])
            self.assertTrue(preview["can_rollback"])
            self.assertEqual(preview["apply"]["draft"], name)
            self.assertEqual({row["key"]: row["action"] for row in preview["config"]},
                             {"primitive_roots": "set", "rules." + SWITCHED: "unset"})
            self.assertEqual({row["action"] for row in preview["files"]}, {"delete"})
            self.assertEqual(_snapshot(home), applied, "a preview writes nothing")

            code, mismatch = self.cli(home, apply_id, "--draft", "someone-else")
            self.assertEqual((code, mismatch["error_code"]), (1, "confirmation-mismatch"))
            self.assertEqual(_snapshot(home), applied)

            code, result = self.cli(home, apply_id, "--draft", name)
            self.assertEqual((code, result["status"]), (0, "rolled-back"), result["message"])
            self.assertEqual(_snapshot(home), before)
            self.assertEqual(_manifest(home), manifest)
            self.assertFalse(home.dest.exists())

            journal = self.journal(home)
            self.assertEqual([(row["phase"], row.get("kind", "apply")) for row in journal],
                             [("intent", "apply"), ("completed", "apply"),
                              ("intent", "rollback"), ("completed", "rollback")])
            self.assertEqual(journal[2]["reverses"], apply_id)
            rollback_id = result["apply_id"]
            self.assertEqual(journal[2]["apply_id"], rollback_id)

            # AC3: Activity shows the rollback, linked to the apply it reversed.
            entries = activity.query(home.state, {"limit": 10, "cursor": "", "session": "",
                                                  "repository": "", "hook": "", "outcome": ""})["entries"]
            by_kind = {entry["kind"]: entry for entry in entries}
            self.assertTrue(by_kind["apply"]["id"].startswith("studio:%s@" % apply_id),
                            by_kind["apply"]["id"])
            rolled = by_kind["rollback"]
            self.assertEqual((rolled["title"], rolled["draft"], rolled["outcome"], rolled["actor"]),
                             ("Apply rolled back", name, "completed", "citizen"))
            self.assertEqual(rolled["evidence_href"], "/activity?entry=studio%3A" + apply_id)
            self.assertIn(apply_id[:12], rolled["reason"])

            # Rolled back once: the apply is refused a second time, and the rollback reverses.
            code, again = self.cli(home, apply_id, "--preview")
            self.assertEqual((code, again["refusals"][0]["code"]), (1, "already-rolled-back"))
            self.assertIn(rollback_id[:12], again["refusals"][0]["message"])
            code, redo = self.cli(home, rollback_id, "--draft", name)
            self.assertEqual((code, redo["status"]), (0, "rolled-back"), redo["message"])
            self.assertEqual(_snapshot(home), applied)

    def test_a_later_apply_of_the_same_key_refuses_naming_it(self):
        """AC2, then rolling the later apply back first clears the way."""
        with self.home() as home:
            first, revision = self.draft(home, "rollback-first")
            first_id = self.apply(home, first, self.switch(first, revision, "off"))
            after_first = _snapshot(home)
            second, revision = self.draft(home, "rollback-second")
            second_id = self.apply(home, second, self.switch(second, revision, "on"))
            after_second = _snapshot(home)

            code, refused = self.cli(home, first_id, "--draft", first)
            self.assertEqual((code, refused["status"], refused["error_code"]),
                             (1, "refused", "later-apply"))
            self.assertIn(second_id[:12], refused["message"])
            self.assertIn("draft " + second, refused["message"])
            self.assertIn("configuration key rules." + SWITCHED, refused["message"])
            self.assertEqual(_snapshot(home), after_second)

            code, result = self.cli(home, second_id, "--draft", second)
            self.assertEqual((code, result["status"]), (0, "rolled-back"), result["message"])
            self.assertEqual(_snapshot(home), after_first)
            code, result = self.cli(home, first_id, "--draft", first)
            self.assertEqual((code, result["status"]), (0, "rolled-back"), result["message"])
            restored = json.loads(home.config.read_text(encoding="utf-8"))
            self.assertEqual(restored, self.initial)

    def test_a_later_hand_edit_is_refused_by_name_and_kept(self):
        with self.home() as home:
            name, revision = self.draft(home, "rollback-edit")
            apply_id = self.apply(home, name, self.add_rule(name, revision))
            greeting = home.dest / "rules" / "greeting.md"
            greeting.write_text("my own words\n", encoding="utf-8")
            config = json.loads(home.config.read_text(encoding="utf-8"))
            config["primitive_roots"].append(str(home.path / "elsewhere"))
            home.config.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
            edited = _snapshot(home)

            code, refused = self.cli(home, apply_id, "--preview")
            self.assertEqual(code, 1)
            self.assertEqual(refused["refusals"][0]["code"], "rollback-conflict")
            message = refused["refusals"][0]["message"]
            self.assertIn("configuration key primitive_roots", message)
            self.assertIn(str(greeting), message)
            code, result = self.cli(home, apply_id)
            self.assertEqual((code, result["error_code"]), (1, "rollback-conflict"))
            self.assertEqual(_snapshot(home), edited)
            self.assertEqual(len(self.journal(home)), 2)

    def test_other_keys_changed_since_are_kept_and_only_the_applied_keys_restored(self):
        with self.home() as home:
            name, revision = self.draft(home, "rollback-semantic")
            apply_id = self.apply(home, name, self.switch(name, revision, "off"))
            changed = home.cli("config", "set", "rules.research-and-verification", "off")
            self.assertEqual(changed.returncode, 0, changed.stderr)
            code, result = self.cli(home, apply_id)
            self.assertEqual((code, result["status"]), (0, "rolled-back"), result["message"])
            config = json.loads(home.config.read_text(encoding="utf-8"))
            self.assertNotIn(SWITCHED, config["rules"])
            self.assertNotIn("rules", self.initial)
            self.assertEqual(config["rules"]["research-and-verification"], "off")

    def test_a_failed_sync_undoes_the_rollback(self):
        with self.home() as home:
            name, revision = self.draft(home, "rollback-fail")
            apply_id = self.apply(home, name, self.add_rule(name, revision))
            applied = _snapshot(home)
            outcomes = iter([SETTLED, {"code": 1, "attention": [], "refused": True}, SETTLED])
            records = []
            result = draft_rollback.rollback(apply_id, self.operations(
                home, lambda dry: next(outcomes), record=records.append), home=home.path)
            self.assertEqual((result["status"], result["error_code"]), ("failed", "sync-refused"))
            self.assertTrue(result["restored"])
            self.assertEqual(_snapshot(home), applied)
            self.assertEqual(self.journal(home)[-1]["phase"], "failed")
            self.assertEqual(draft_apply.unfinished_applies(home.path), [])
            self.assertEqual([row["detail"]["outcome"] for row in records], ["failed"])
            self.assertEqual(records[0]["detail"]["reverses"], apply_id)

    def test_an_interrupted_rollback_is_restored_by_recover(self):
        with self.home() as home:
            name, revision = self.draft(home, "rollback-kill")
            apply_id = self.apply(home, name, self.add_rule(name, revision))
            applied = _snapshot(home)

            def killed(dry):
                if dry:
                    return SETTLED
                raise draft_apply.ApplyError("killed", "killed")

            real = draft_apply._journal
            with mock.patch.object(draft_apply, "_journal",
                                   side_effect=lambda path, row: real(path, row)
                                   if row["phase"] == "intent" else None), \
                    mock.patch.object(draft_apply, "_restore", return_value=False):
                draft_rollback.rollback(apply_id, self.operations(home, killed), home=home.path)
            self.assertFalse(home.dest.exists())
            open_intents = draft_apply.unfinished_applies(home.path)
            self.assertEqual([row.get("kind") for row in open_intents], ["rollback"])
            code, preview = self.cli(home, apply_id, "--preview")
            self.assertEqual((code, preview["refusals"][0]["code"]), (1, "interrupted-apply"))

            done = home.cli("draft", "recover", "--json")
            recovered = json.loads(done.stdout)
            self.assertEqual(recovered["status"], "recovered", recovered["message"])
            self.assertEqual(_snapshot(home), applied)
            code, result = self.cli(home, apply_id)
            self.assertEqual((code, result["status"]), (0, "rolled-back"), result["message"])

    def test_a_held_lock_refuses_naming_its_holder(self):
        with self.home() as home:
            with reconcile.lock(home.state, holder="citizen sync"):
                code, result = self.cli(home, "a" * 32)
            self.assertEqual((code, result["error_code"]), (1, "busy"))
            self.assertTrue(result["holder"].startswith("citizen sync (pid %d" % os.getpid()))


class JournalTests(unittest.TestCase):
    """The journal rules, on rows written directly."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.config = draft_apply.config_file(self.home)
        self.config.parent.mkdir(parents=True)
        self.config.write_bytes(b'{"mode": "b"}\n')

    def row(self, apply_id, phase, **extra):
        draft_apply._journal(self.home, dict(apply_id=apply_id, phase=phase, draft="d", ts="t", **extra))

    def intent(self, apply_id, key="mode", **extra):
        self.row(apply_id, "intent", prior_config=draft_apply._encoded(b'{"mode": "a"}\n'),
                 config=[{"key": key, "action": "set", "applied": "b"}], files=[],
                 destination=str(self.home / "root"), **extra)

    def code(self, apply_id):
        refusals = draft_rollback.preview(apply_id, self.home)["refusals"]
        return refusals[0]["code"] if refusals else ""

    def test_ids_must_be_journalled_completed_applies(self):
        self.assertEqual(self.code("not-an-id"), "invalid-request")
        self.assertEqual(self.code("f" * 32), "unknown-apply")
        for phase, extra in (("failed", {"restored": True}), ("recovered", {}), ("abandoned", {})):
            identity = uuid.uuid4().hex
            self.intent(identity)
            self.row(identity, phase, **extra)
            self.assertEqual(self.code(identity), "not-applied", phase)

    def test_an_open_intent_refuses_every_rollback(self):
        done, open_ = uuid.uuid4().hex, uuid.uuid4().hex
        self.intent(done)
        self.row(done, "completed")
        self.assertEqual(self.code(done), "")
        self.intent(open_)
        self.assertEqual(self.code(done), "interrupted-apply")

    def test_a_later_change_still_in_effect_refuses_but_a_reversed_one_does_not(self):
        first, later, undo = (uuid.uuid4().hex for _ in range(3))
        for identity in (first, later):
            self.intent(identity)
            self.row(identity, "completed")
        self.assertEqual(self.code(first), "later-apply")
        self.intent(undo, kind="rollback", reverses=later)
        self.row(undo, "completed")
        self.assertEqual(self.code(first), "")
        self.assertEqual(self.code(later), "already-rolled-back")

    def test_a_later_file_in_a_root_the_apply_registered_refuses(self):
        first, later = uuid.uuid4().hex, uuid.uuid4().hex
        self.intent(first, key="primitive_roots")
        self.row(first, "completed")
        self.row(later, "intent", prior_config=draft_apply._encoded(b"{}"), config=[],
                 files=[{"path": "rules/other.md", "action": "write", "prior": None,
                         "applied_sha256": "0" * 64}], destination=str(self.home / "root"))
        self.row(later, "completed")
        refusals = draft_rollback.preview(first, self.home)["refusals"]
        self.assertEqual(refusals[0]["code"], "later-apply")
        self.assertIn("personal root " + str(self.home / "root"), refusals[0]["message"])

    def test_a_key_already_back_at_its_prior_value_leaves_nothing_to_roll_back(self):
        identity = uuid.uuid4().hex
        self.intent(identity)
        self.row(identity, "completed")
        self.config.write_bytes(b'{"mode": "a"}\n')
        self.assertEqual(self.code(identity), "nothing-to-roll-back")


class ActivityLinkTests(unittest.TestCase):
    def entry(self, detail):
        return activity._event_entry({"kind": "event", "event": "studio.rollback", "id": "r" * 32,
                                      "ts": "2026-10-02T12:00:00Z", "detail": detail})

    def test_a_rollback_links_only_a_well_formed_apply_id(self):
        linked = self.entry({"draft": "d", "reverses": "a" * 32, "outcome": "completed"})
        self.assertEqual(linked["title"], "Apply rolled back")
        self.assertEqual(linked["evidence_href"], "/activity?entry=studio%3A" + "a" * 32)
        self.assertEqual(linked["evidence_label"], "Open the change this rolled back")
        for reverses in ("../../library", "A" * 32, None, 7):
            unlinked = self.entry({"draft": "d", "reverses": reverses})
            self.assertEqual((unlinked["evidence_href"], unlinked["evidence_label"]), ("", ""))


if __name__ == "__main__":
    unittest.main()

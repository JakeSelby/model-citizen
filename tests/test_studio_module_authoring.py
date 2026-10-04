# SPDX-License-Identifier: MIT
"""New modules from templates and forks of core modules, in a draft's personal root."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from harness_core.studio import (drafts, module_authoring, module_editing,  # noqa: E402
                                 module_library, server)

import draft_support  # noqa: E402

PASS = [sys.executable, "-c", "raise SystemExit(0)"]
FORKED = "cache-hygiene"


def _rule(name, description="Say what the rule is for."):
    return {"action": "add", "kind": "rules", "name": name, "description": description}


class RequestValidationTests(unittest.TestCase):
    def test_requests_are_refused_before_any_draft_is_read(self):
        cases = [
            ({"action": "edit"}, "invalid-request"),
            (dict(_rule("Bad_Name")), "invalid-name"),
            (dict(_rule("x"), kind="roles"), "invalid-kind"),
            (_rule("fine", ""), "invalid-description"),
            (_rule("fine", "two\nlines"), "invalid-description"),
            ({"action": "add", "kind": "stances", "name": "novariant", "description": "d"},
             "invalid-name"),
            ({"action": "fork", "source": "root-1:rules:mine"}, "fork-unavailable"),
            ({"action": "fork", "source": "core:roles:reviewer"}, "fork-unavailable"),
            ({"action": "fork", "source": "core:rules:../../../../etc/passwd"}, "fork-unavailable"),
            ({"action": "fork", "source": "core:skills:Beta"}, "fork-unavailable"),
            (dict(_rule("a---b")), "invalid-name"),
            ({"action": "fork", "source": "core:skills:beta", "name": "x---y"}, "invalid-name"),
            ({"action": "add", "kind": "stances", "name": "tone/a---b", "description": "d"},
             "invalid-name"),
            (_rule("fine", "a --- b"), "invalid-description"),
            (_rule("fine", "tab\there"), "invalid-description"),
        ]
        for request, code in cases:
            with self.subTest(request=request):
                with self.assertRaises(module_authoring.AuthoringError) as caught:
                    module_authoring._normalized(request)
                self.assertEqual(caught.exception.code, code)

    def test_a_fork_is_named_apart_from_its_source_so_its_switch_stays_its_own(self):
        normalized = module_authoring._normalized({"action": "fork", "source": "core:rules:secrets"})
        self.assertEqual(normalized["name"], "secrets-fork")
        self.assertEqual(normalized["kind"], "rules")

    def test_a_forked_skill_is_renamed_in_its_frontmatter_only(self):
        renamed = module_authoring._renamed_skill(
            b"---\nname: beta\ndescription: d\n---\n\nname: beta stays in the body\n", "beta-fork")
        self.assertEqual(renamed, b"---\nname: beta-fork\ndescription: d\n---\n\n"
                                  b"name: beta stays in the body\n")
        self.assertEqual(module_authoring._renamed_skill(b"\xff\x00", "x"), b"\xff\x00")

    def test_templates_carry_manifest_fields_the_resolver_accepts(self):
        posture = module_authoring.load_posture(ROOT)
        kinds = [kind for kind, entry in posture.selection_kinds(ROOT).items()
                 if entry.get("value") == "switch"]
        for kind in module_authoring.MANIFEST_KINDS:
            manifest = module_authoring._manifest_template(kind, "What it is for.")
            self.assertIsNone(posture.validate_manifest(kind, "probe", manifest, kinds))
        for kind, name in (("rules", "probe"), ("skills", "probe"), ("stances", "tone/plain"),
                           ("modes", "probe")):
            files = module_authoring._template_files(kind, name, "What it is for.")
            self.assertEqual(len(files), 1)
        mode = json.loads(module_authoring._template_files("modes", "probe", "d")["modes/probe.json"])
        self.assertEqual(mode, {"schema_version": 1, "description": "d"})


class UpstreamDiffTests(unittest.TestCase):
    """AC4: the original is read from git at the recorded revision and diffed against core now."""

    def _git(self, checkout, *args):
        return subprocess.run(["git", "-C", str(checkout), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def _checkout(self, files):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        checkout = Path(temporary.name) / "checkout"
        checkout.mkdir()
        self._git(checkout, "init", "-q")
        self._git(checkout, "config", "user.email", "t")
        self._git(checkout, "config", "user.name", "Test")
        return checkout, self._commit(checkout, files)

    def _commit(self, checkout, files):
        for relative, content in files.items():
            path = checkout / relative
            if content is None:
                path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self._git(checkout, "add", "-A")
        self._git(checkout, "commit", "-q", "-m", "update")
        return self._git(checkout, "rev-parse", "HEAD")

    @staticmethod
    def _forks(source, revision, files):
        kind = source.split("/", 1)[0]
        return {"schema_version": 1, kind: {"mine": {
            "source": source, "version": "0.1.0", "revision": revision,
            "files": {key: module_library.digest(value) for key, value in files.items()}}}}

    def test_an_unchanged_original_reports_no_upstream_diff(self):
        original = {"primitives/rules/alpha.md": b"# Alpha\n\nKeep it.\n"}
        checkout, revision = self._checkout(original)
        fork = module_library._fork(checkout, self._forks(
            "rules/alpha", revision, {"alpha.md": original["primitives/rules/alpha.md"]}), "rules", "mine")
        self.assertEqual(fork["upstream"], {"changed": False, "missing": False,
                                            "original_available": True, "diff": ""})
        self.assertEqual((fork["source"], fork["version"], fork["revision"]),
                         ("rules/alpha", "0.1.0", revision))

    def test_an_update_shows_a_multi_file_diff_with_binary_files_compared_by_digest(self):
        skill = "primitives/skills/beta/"
        original = {skill + "SKILL.md": b"---\nname: beta\n---\n\nOld step.\n",
                    skill + "scripts/run.py": b"print('old')\n",
                    skill + "icon.png": b"\x89PNG\x00\xff old"}
        checkout, revision = self._checkout(original)
        self._commit(checkout, {skill + "SKILL.md": b"---\nname: beta\n---\n\nNew step.\n",
                                skill + "scripts/run.py": b"print('new')\n",
                                skill + "icon.png": b"\x89PNG\x00\xff new"})
        recorded = {key[len(skill):]: value for key, value in original.items()}
        fork = module_library._fork(checkout, self._forks("skills/beta", revision, recorded),
                                    "skills", "mine")
        diff = fork["upstream"]["diff"]
        self.assertTrue(fork["upstream"]["changed"])
        self.assertIn("-Old step.", diff)
        self.assertIn("+New step.", diff)
        self.assertIn("+print('new')", diff)
        self.assertIn("Binary file skills/beta/icon.png differs", diff)
        self.assertEqual(module_library.module_bytes(checkout / "primitives", "skills", "beta")["icon.png"],
                         b"\x89PNG\x00\xff new")

    def test_a_removed_original_is_reported_missing(self):
        original = {"primitives/rules/alpha.md": b"# Alpha\n"}
        checkout, revision = self._checkout(original)
        self._commit(checkout, {"primitives/rules/alpha.md": None, "README": b"x\n"})
        fork = module_library._fork(checkout, self._forks(
            "rules/alpha", revision, {"alpha.md": b"# Alpha\n"}), "rules", "mine")
        self.assertTrue(fork["upstream"]["missing"])
        self.assertIn("-# Alpha", fork["upstream"]["diff"])

    def test_originals_are_cached_by_revision_and_sha256_names_are_accepted(self):
        original = {"primitives/rules/alpha.md": b"# Alpha\n"}
        checkout, revision = self._checkout(original)
        module_library._ORIGINALS.clear()
        real_run = subprocess.run
        spawned = []

        def counting(*args, **kwargs):
            spawned.append(args[0])
            return real_run(*args, **kwargs)

        with mock.patch.object(module_library.subprocess, "run", side_effect=counting):
            for _ in range(3):
                self.assertEqual(module_library._at_revision(
                    checkout, revision, "primitives/rules/alpha.md"), b"# Alpha\n")
        self.assertEqual(len(spawned), 1)
        self.assertTrue(module_library.REVISION.fullmatch("a" * 64))
        self.assertTrue(module_library.REVISION.fullmatch("a" * 40))
        self.assertFalse(module_library.REVISION.fullmatch("a" * 41))

    def test_a_traversing_source_is_never_read(self):
        checkout, revision = self._checkout({"primitives/rules/alpha.md": b"# Alpha\n",
                                             "secret.md": b"do not show\n"})
        for source, files in (("rules/../../secret", {"secret.md": b"do not show\n"}),
                              ("skills/beta", {"../../secret.md": b"do not show\n"}),
                              ("roles/alpha", {"alpha.md": b"# Alpha\n"})):
            with self.subTest(source=source):
                self.assertIsNone(module_library._fork(
                    checkout, self._forks(source, revision, files), source.split("/")[0], "mine"))
        self.assertIsNone(module_library.module_files(checkout / "primitives", "rules", "../../secret"))

    def test_the_library_attaches_a_fork_from_the_personal_root(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        own = Path(temporary.name) / "own"
        (own / "rules").mkdir(parents=True)
        (own / "rules" / (FORKED + "-fork.md")).write_text("# Mine\n", encoding="utf-8")
        current = (ROOT / "primitives" / "rules" / (FORKED + ".md")).read_bytes()
        forks = self._forks("rules/" + FORKED, "0" * 40, {FORKED + ".md": current})
        forks["rules"] = {FORKED + "-fork": forks["rules"].pop("mine")}
        (own / module_library.FORKS_FILE).write_text(json.dumps(forks), encoding="utf-8")
        payload = module_library.inventory(ROOT, {"primitive_roots": [str(own)]})
        modules = {item["key"]: item for item in payload["modules"]}
        fork = modules["root-1:rules:" + FORKED + "-fork"]["fork"]
        self.assertEqual(fork["source"], "rules/" + FORKED)
        self.assertFalse(fork["upstream"]["changed"])
        self.assertIsNone(modules["core:rules:" + FORKED]["fork"])


class DraftAuthoringTests(unittest.TestCase):
    @contextmanager
    def real_draft(self, prefix):
        name = draft_support.draft_name(prefix + "-")
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            home = base / "home"
            config_path = home.joinpath(".config", "agent-harness", "config.json")
            config_path.parent.mkdir(parents=True)
            config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            config["primitive_roots"] = []
            config_path.write_text(json.dumps(config), encoding="utf-8")
            # HARNESS_QUIET comes from tests/isolation.py; a --json CLI call must print.
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith("HARNESS_")}
            environment.update({"HARNESS_HOME": str(home),
                                "HARNESS_WORKTREE_ROOT": str(base / "worktrees")})
            created = subprocess.run(
                [sys.executable, str(ROOT / "bin" / "harness"), "draft", "create", name, "--json"],
                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(created.returncode, 0, created.stderr or created.stdout)
            try:
                with mock.patch.dict(os.environ, environment, clear=True):
                    yield name, json.loads(created.stdout), environment, base
            finally:
                draft_support.discard_draft(self, name, environment)

    def _config(self, name):
        return drafts.read_config(ROOT, name)["config"]

    def test_add_offers_a_root_then_fork_switches_core_off_and_tracks_upstream(self):
        with self.real_draft("authoring-flow") as (name, initial, environment, base):
            # AC1: no personal root, so the Studio offers one and refuses to guess.
            read = module_authoring.read(ROOT, name)
            self.assertEqual(read["status"], "ready")
            self.assertIsNone(read["root"])
            self.assertEqual(read["offer"]["label"], module_authoring.OWN_ROOT)
            self.assertIn("core:rules:" + FORKED, [item["key"] for item in read["forkable"]])
            offered = module_authoring.preview(ROOT, name, _rule("greeting"))
            self.assertEqual(offered["error_code"], "root-required")
            refused = module_authoring.save(ROOT, name, initial["revision"], "no-root",
                                            _rule("greeting"))
            self.assertFalse(refused["saved"])
            self.assertEqual(drafts.find(ROOT, name)[1]["revision"], initial["revision"])

            request = dict(_rule("greeting", "Greets the reader first."), create_root=True)
            planned = module_authoring.preview(ROOT, name, request)
            self.assertTrue(planned["valid"], planned)
            self.assertTrue(planned["root"]["created"])
            self.assertEqual(planned["findings"], [])
            self.assertEqual([item["path"] for item in planned["files"]],
                             ["manifests.json", "rules/greeting.md"])
            added = module_authoring.save(ROOT, name, initial["revision"], "add-greeting", request)
            self.assertTrue(added["saved"], added)
            self.assertEqual(added["module"]["key"], "root-1:rules:greeting")
            self.assertEqual(self._config(name)["primitive_roots"],
                             [str(ROOT.resolve() / module_authoring.OWN_ROOT)])
            self.assertEqual(module_authoring.read(ROOT, name)["root"]["id"], "root-1")
            editable = [item["key"] for item in module_editing.list_modules(ROOT, name)]
            self.assertIn("root-1:rules:greeting", editable)

            # AC2: the saved manifest is one the resolver accepts, from the draft's own root.
            worktree = drafts.find(ROOT, name)[0]
            mapped = module_editing._mapped_config(ROOT.resolve(), worktree, self._config(name))
            posture = module_authoring.load_posture(worktree)
            declared, errors = posture.manifests(mapped, worktree)
            self.assertEqual(errors, [])
            self.assertEqual(declared["rules"]["greeting"]["claims"], ["Greets the reader first."])
            self.assertEqual(module_authoring.resolution_findings(posture, mapped, worktree), [])

            # Exact retry returns the durable response; the same key for another request is refused.
            replayed = module_authoring.save(ROOT, name, initial["revision"], "add-greeting", request)
            self.assertTrue(replayed["result"]["replayed"])
            reused = module_authoring.save(ROOT, name, initial["revision"], "add-greeting",
                                           _rule("other"))
            self.assertEqual(reused["error_code"], "idempotency-conflict")
            duplicate = module_authoring.preview(ROOT, name, dict(_rule("greeting")))
            self.assertEqual(duplicate["error_code"], "module-exists")

            # AC3: a fork switches the core rule off in the same draft and records provenance.
            fork_request = {"action": "fork", "source": "core:rules:" + FORKED}
            forked = module_authoring.save(ROOT, name, added["result"]["revision"], "fork", fork_request)
            self.assertTrue(forked["saved"], forked)
            self.assertEqual(forked["config_changes"], [{"path": "rules." + FORKED, "value": "off"}])
            self.assertEqual(self._config(name)["rules"][FORKED], "off")
            # forks.json records digests, never the original's text, so it stays small.
            forks_text = next(item["text"] for item in forked["files"] if item["path"] == "forks.json")
            recorded = json.loads(forks_text)["rules"][FORKED + "-fork"]
            self.assertEqual(recorded["revision"], initial["base_revision"])
            self.assertRegex(recorded["files"][FORKED + ".md"], r"^[0-9a-f]{64}$")
            self.assertLess(len(forks_text), 1024)
            version = (worktree / "VERSION").read_text(encoding="utf-8").strip()
            library = {item["key"]: item for item in module_authoring.library(ROOT, name)["modules"]}
            fork_key = "root-1:rules:" + FORKED + "-fork"
            self.assertEqual(library["core:rules:" + FORKED]["state"]["value"], "off")
            self.assertEqual(library[fork_key]["state"]["value"], "on")
            self.assertEqual(library[fork_key]["fork"]["source"], "rules/" + FORKED)
            self.assertEqual(library[fork_key]["fork"]["version"], version)
            self.assertFalse(library[fork_key]["fork"]["upstream"]["changed"])
            self.assertTrue(library[fork_key]["source"]["path"].startswith("<draft>/"))
            self.assertEqual(
                (worktree / module_authoring.OWN_ROOT / "rules" / (FORKED + "-fork.md")).read_bytes(),
                (worktree / "primitives" / "rules" / (FORKED + ".md")).read_bytes())

            # AC4: an update to the original shows on the fork as an upstream diff.
            core_rule = "primitives/rules/" + FORKED + ".md"
            updated = (worktree / core_rule).read_text(encoding="utf-8") + "\nAn upstream addition.\n"
            drafts.checkpoint(ROOT, name, forked["result"]["revision"], "upstream-update",
                              files={core_rule: updated.encode("utf-8")}, check_command=PASS)
            fork = {item["key"]: item for item in
                    module_authoring.library(ROOT, name)["modules"]}[fork_key]["fork"]
            self.assertTrue(fork["upstream"]["changed"])
            self.assertIn("+An upstream addition.", fork["upstream"]["diff"])

            # The CLI answers exactly what the Studio route does.
            request_path = base / "request.json"
            request_path.write_text(json.dumps({"action": "add", "kind": "modes",
                                                "name": "focus", "description": "Focus."}),
                                    encoding="utf-8")
            command = [sys.executable, str(ROOT / "bin" / "harness"), "draft", "module", "plan",
                       name, "--request", str(request_path), "--json"]
            executed = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True,
                                      text=True, timeout=120)
            self.assertEqual(executed.returncode, 0, executed.stderr or executed.stdout)
            self.assertEqual(json.loads(executed.stdout), module_authoring.preview(
                ROOT, name, json.loads(request_path.read_text(encoding="utf-8"))))

    def test_a_forked_skill_is_renamed_copied_whole_and_diffed_across_files(self):
        skill = "workflow-status"
        with self.real_draft("authoring-skill") as (name, initial, _environment, _base):
            forked = module_authoring.save(ROOT, name, initial["revision"], "fork-skill", {
                "action": "fork", "source": "core:skills:" + skill, "create_root": True})
            self.assertTrue(forked["saved"], forked)
            worktree = drafts.find(ROOT, name)[0]
            core = worktree / "primitives" / "skills" / skill
            fork = worktree / module_authoring.OWN_ROOT / "skills" / (skill + "-fork")
            core_files = sorted(path.relative_to(core).as_posix() for path in core.rglob("*")
                                if path.is_file())
            self.assertGreater(len(core_files), 1)
            self.assertEqual(sorted(path.relative_to(fork).as_posix() for path in fork.rglob("*")
                                    if path.is_file()), core_files)
            header = (fork / "SKILL.md").read_text(encoding="utf-8").split("\n---", 1)[0]
            self.assertIn("name: " + skill + "-fork\n", header + "\n")
            self.assertNotIn("name: " + skill + "\n", header + "\n")
            for relative in core_files:
                if relative != "SKILL.md":
                    self.assertEqual((fork / relative).read_bytes(), (core / relative).read_bytes())
            self.assertEqual(self._config(name)["skills"][skill], "off")

            other = next(relative for relative in core_files if relative != "SKILL.md")
            updates = {}
            for relative in ("SKILL.md", other):
                text = (core / relative).read_text(encoding="utf-8")
                updates["primitives/skills/" + skill + "/" + relative] = (
                    text + "\n# upstream change in " + relative + "\n").encode("utf-8")
            drafts.checkpoint(ROOT, name, forked["result"]["revision"], "skill-update",
                              files=updates, check_command=PASS)
            library = {item["key"]: item for item in module_authoring.library(ROOT, name)["modules"]}
            upstream = library["root-1:skills:" + skill + "-fork"]["fork"]["upstream"]
            self.assertTrue(upstream["changed"])
            self.assertTrue(upstream["original_available"])
            self.assertIn("+# upstream change in SKILL.md", upstream["diff"])
            self.assertIn("+# upstream change in " + other, upstream["diff"])

    def test_a_concurrent_authoring_read_never_makes_a_draft_write_fail_busy(self):
        with self.real_draft("authoring-concurrent") as (name, initial, _environment, _base):
            entered, release = threading.Event(), threading.Event()
            real_inventory = module_library.inventory
            calls = []

            def slow_inventory(*args, **kwargs):
                calls.append(1)
                if len(calls) == 1:
                    entered.set()
                    release.wait(30)
                return real_inventory(*args, **kwargs)

            results = {}
            with mock.patch.object(module_library, "inventory", side_effect=slow_inventory):
                reader = threading.Thread(target=lambda: results.update(
                    library=module_authoring.library(ROOT, name)))
                reader.start()
                self.assertTrue(entered.wait(30))
                try:
                    written = drafts.checkpoint(
                        ROOT, name, initial["revision"], "write-during-read",
                        files={"concurrent.md": b"# Written while a read ran\n"},
                        check_command=PASS,
                    )
                    also = module_authoring.read(ROOT, name)
                finally:
                    release.set()
                    reader.join(60)
            self.assertNotEqual(written["revision"], initial["revision"])
            self.assertEqual(also["status"], "ready")
            # The read that overlapped the write is retried against the new revision.
            self.assertGreaterEqual(len(calls), 2)
            self.assertIn("modules", results["library"])

    def test_lock_free_reads_see_every_write_attempt_recover_and_wait_for_the_lock(self):
        with self.real_draft("authoring-seqlock") as (name, initial, _environment, _base):
            worktree = drafts.find(ROOT, name)[0]
            paths = drafts._paths(worktree)

            # A save that fails its check and rolls back still invalidates an overlapping read.
            calls = []

            def overlapped(worktree_path, state, config):
                calls.append(state["revision"])
                if len(calls) == 1:
                    with self.assertRaises(drafts.DraftError):
                        drafts.checkpoint(ROOT, name, initial["revision"], "rolled-back",
                                          files={"rolled.md": b"# Never committed\n"},
                                          check_command=[sys.executable, "-c", "raise SystemExit(1)"])
                return (worktree_path / "rolled.md").exists()

            self.assertFalse(drafts.read_snapshot(ROOT, name, overlapped))
            self.assertEqual(len(calls), 2)

            # An exception raised inside an overlapping write is retried, a genuine one is not.
            raised = []

            def transient(worktree_path, state, config):
                raised.append(1)
                if len(raised) == 1:
                    with drafts._locked(worktree_path):
                        pass
                    raise FileNotFoundError("moved aside mid-publish")
                return "read"

            self.assertEqual(drafts.read_snapshot(ROOT, name, transient), "read")
            with self.assertRaises(ValueError):
                drafts.read_snapshot(ROOT, name, lambda *_args: (_ for _ in ()).throw(ValueError("real")))

            # A generation a crashed writer left odd is recovered once, not reported busy.
            paths["generation"].write_text(str(drafts._generation(paths) + 1), encoding="ascii")
            self.assertEqual(drafts.read_snapshot(ROOT, name, lambda *_args: "recovered"), "recovered")
            self.assertEqual(drafts._generation(paths) % 2, 0)

            # A write longer than the fast retries is waited for, not refused busy.
            held, release = threading.Event(), threading.Event()

            def writer():
                with drafts._locked(worktree):
                    held.set()
                    release.wait(30)

            thread = threading.Thread(target=writer)
            thread.start()
            self.assertTrue(held.wait(30))
            threading.Timer(3.0, release.set).start()
            try:
                self.assertEqual(drafts.read_snapshot(ROOT, name, lambda *_args: "waited"), "waited")
            finally:
                release.set()
                thread.join(30)

    def test_a_fork_after_a_draft_edit_of_the_core_module_still_diffs_upstream(self):
        with self.real_draft("authoring-edited-core") as (name, initial, _environment, _base):
            worktree = drafts.find(ROOT, name)[0]
            core_rule = "primitives/rules/" + FORKED + ".md"
            edited = (worktree / core_rule).read_text(encoding="utf-8") + "\nA draft edit before the fork.\n"
            first = drafts.checkpoint(ROOT, name, initial["revision"], "edit-core",
                                      files={core_rule: edited.encode("utf-8")}, check_command=PASS)
            forked = module_authoring.save(ROOT, name, first["revision"], "fork-edited", {
                "action": "fork", "source": "core:rules:" + FORKED, "create_root": True})
            self.assertTrue(forked["saved"], forked)
            recorded = json.loads(next(item["text"] for item in forked["files"]
                                       if item["path"] == "forks.json"))["rules"][FORKED + "-fork"]
            self.assertEqual(recorded["revision"], first["revision"])
            drafts.checkpoint(ROOT, name, forked["result"]["revision"], "upstream-after",
                              files={core_rule: (edited + "Upstream after the fork.\n").encode("utf-8")},
                              check_command=PASS)
            upstream = {item["key"]: item for item in module_authoring.library(ROOT, name)["modules"]}[
                "root-1:rules:" + FORKED + "-fork"]["fork"]["upstream"]
            self.assertTrue(upstream["original_available"])
            self.assertIn("+Upstream after the fork.", upstream["diff"])
            self.assertNotIn("+A draft edit before the fork.", upstream["diff"])

    def test_manifest_checks_refuse_before_any_checkpoint(self):
        with self.real_draft("authoring-manifest") as (name, initial, _environment, _base):
            first = module_authoring.save(ROOT, name, initial["revision"], "first",
                                          dict(_rule("first"), create_root=True))
            self.assertTrue(first["saved"], first)
            worktree = drafts.find(ROOT, name)[0]
            manifests_path = module_authoring.OWN_ROOT + "/manifests.json"
            manifests = json.loads((worktree / manifests_path).read_text(encoding="utf-8"))
            manifests["rules"]["first"]["conflicts"] = ["rules/secrets"]
            seeded = drafts.checkpoint(
                ROOT, name, first["result"]["revision"], "seed-conflict",
                files={manifests_path: (json.dumps(manifests) + "\n").encode("utf-8")},
                check_command=PASS,
            )
            mode = {"action": "add", "kind": "modes", "name": "calm", "description": "Calm."}
            planned = module_authoring.preview(ROOT, name, mode)
            self.assertFalse(planned["valid"])
            self.assertEqual(planned["error_code"], "manifest-refused")
            self.assertTrue(any("conflicts with" in line for line in planned["findings"]),
                            planned["findings"])
            refused = module_authoring.save(ROOT, name, seeded["revision"], "refused", mode)
            self.assertFalse(refused["saved"])
            self.assertEqual(drafts.find(ROOT, name)[1]["revision"], seeded["revision"])

            # The save's own check command enforces the same resolution after writing.
            with mock.patch.object(module_authoring, "_candidate_findings", return_value=[]):
                gated = module_authoring.save(ROOT, name, seeded["revision"], "gated", mode)
            self.assertFalse(gated["saved"])
            self.assertEqual(gated["error_code"], "check-failed")
            self.assertIn("conflicts with rules/secrets", gated["error"])
            self.assertEqual(drafts.find(ROOT, name)[1]["revision"], seeded["revision"])
            self.assertFalse((worktree / module_authoring.OWN_ROOT / "modes" / "calm.json").exists())


class RouteTests(unittest.TestCase):
    def test_authoring_routes_name_their_cli_equivalents(self):
        routes = {route.path: route for route in server.ROUTES.entries}
        for path, action in (("/api/configure/authoring/read", "read"),
                             ("/api/configure/authoring/preview", "preview"),
                             ("/api/configure/authoring/save", "save"),
                             ("/api/configure/authoring/library", "library")):
            with self.subTest(path=path):
                self.assertEqual(routes[path].method, "POST")
                self.assertEqual(routes[path].cli_command, module_authoring.CLI_COMMANDS[action])


if __name__ == "__main__":
    unittest.main()

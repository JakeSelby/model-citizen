# SPDX-License-Identifier: MIT
"""New modules from templates and forks of core modules, in a draft's personal root."""
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
    """AC4: the library diffs the original as forked against the core module as it is now."""

    def _library(self, recorded):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        own = Path(temporary.name) / "own"
        (own / "rules").mkdir(parents=True)
        (own / "rules" / (FORKED + "-fork.md")).write_text("# Mine\n", encoding="utf-8")
        (own / module_library.FORKS_FILE).write_text(json.dumps({
            "schema_version": 1,
            "rules": {FORKED + "-fork": {"source": "rules/" + FORKED, "version": "0.1.0",
                                         "revision": "a" * 40, "files": recorded}},
        }), encoding="utf-8")
        payload = module_library.inventory(ROOT, {"primitive_roots": [str(own)]})
        return {item["key"]: item for item in payload["modules"]}

    def test_an_unchanged_original_reports_no_upstream_diff(self):
        current = (ROOT / "primitives" / "rules" / (FORKED + ".md")).read_text(encoding="utf-8")
        modules = self._library({FORKED + ".md": current})
        fork = modules["root-1:rules:" + FORKED + "-fork"]["fork"]
        self.assertEqual(fork["source"], "rules/" + FORKED)
        self.assertEqual(fork["version"], "0.1.0")
        self.assertEqual(fork["upstream"], {"changed": False, "missing": False, "diff": ""})
        self.assertIsNone(modules["core:rules:" + FORKED]["fork"])

    def test_an_updated_original_shows_the_upstream_diff_on_the_fork(self):
        current = (ROOT / "primitives" / "rules" / (FORKED + ".md")).read_text(encoding="utf-8")
        older = current.replace("\n", "\nA line the update removed.\n", 1)
        fork = self._library({FORKED + ".md": older})["root-1:rules:" + FORKED + "-fork"]["fork"]
        self.assertTrue(fork["upstream"]["changed"])
        self.assertIn("-A line the update removed.", fork["upstream"]["diff"])
        self.assertIn("core/rules/" + FORKED + "/" + FORKED + ".md", fork["upstream"]["diff"])

    def test_a_removed_original_is_reported_missing(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        own = Path(temporary.name) / "own"
        (own / "rules").mkdir(parents=True)
        (own / "rules" / "gone-fork.md").write_text("# Mine\n", encoding="utf-8")
        (own / module_library.FORKS_FILE).write_text(json.dumps({
            "schema_version": 1, "rules": {"gone-fork": {
                "source": "rules/no-such-core-rule", "files": {"no-such-core-rule.md": "x\n"}}},
        }), encoding="utf-8")
        payload = module_library.inventory(ROOT, {"primitive_roots": [str(own)]})
        fork = next(item for item in payload["modules"] if item["name"] == "gone-fork")["fork"]
        self.assertTrue(fork["upstream"]["missing"])
        self.assertTrue(fork["upstream"]["changed"])


class DraftAuthoringTests(unittest.TestCase):
    @contextmanager
    def real_draft(self, prefix):
        name = prefix + "-" + uuid.uuid4().hex[:10]
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
    def test_authoring_routes_are_authenticated_posts_with_cli_equivalents(self):
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

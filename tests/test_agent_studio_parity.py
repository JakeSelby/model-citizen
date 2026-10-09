# SPDX-License-Identifier: MIT
"""Each Studio domain route and the `citizen` command it shows answer alike on one fixture: a
disposable home with one real draft. Where the route answers, the command prints an object with
the same keys that the route's response schema accepts; where the route refuses, the command
refuses too. A route without an entry here fails, so a new route must show a working command.
Run: python3 -m unittest discover -s tests -p test_agent_studio_parity.py"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_harness import REPO
from harness_core.studio import headless, server

import draft_support

CLI = REPO / "bin" / "harness"
APPLY_ID = "0" * 32
STALE = "0" * 40
UNKNOWN_RUN = "00000000-0000-4000-8000-000000000000"
HISTORY = {"limit": 50, "cursor": None, "suite_id": None, "target": None, "status": None,
           "created_from": None, "created_to": None, "min_cost_usd": None,
           "max_cost_usd": None, "min_duration_ms": None, "max_duration_ms": None}
FORM = {"model": "claude-test", "repetitions": 1, "tasks": ["one"], "max_budget_usd": "1",
        "spend_cap_usd": "2", "pack": {"name": "absent", "digest": "0" * 64}}
# The HTML report shows the same artifact `citizen runs evidence` prints; no JSON to compare.
NOT_JSON = {("GET", server.PLUGIN_EVAL_REPORT_PATH)}


def cases(draft, revision, files):
    """(method, path) -> (argv after `citizen`, route request, "same" or "refused")."""
    d = draft
    return {
        ("GET", "/api/overview"): (["doctor"], None, "same"),
        ("GET", "/api/configure/schema"): (["draft", "settings", "schema"], None, "same"),
        ("POST", "/api/selection"): (["selection", "--report"],
                                     {"repository": "", "project_file": ""}, "same"),
        ("POST", "/api/configure/read"): (["draft", "settings", "read", d], {"draft": d}, "same"),
        ("POST", "/api/configure/preview"): (
            ["draft", "settings", "preview", d, "--changes", files["empty"]],
            {"draft": d, "changes": {}}, "same"),
        ("POST", "/api/configure/save"): (
            ["draft", "settings", "save", d, "--base-revision", STALE, "--idempotency-key", "k1",
             "--changes", files["empty"]],
            {"draft": d, "base_revision": STALE, "idempotency_key": "k1", "changes": {}}, "same"),
        ("POST", "/api/configure/selection/read"): (
            ["draft", "selection", "read", d], {"draft": d}, "same"),
        ("POST", "/api/configure/selection/preview"): (
            ["draft", "selection", "preview", d, "--changes", files["empty"]],
            {"draft": d, "changes": {}}, "same"),
        ("POST", "/api/configure/selection/save"): (
            ["draft", "selection", "save", d, "--base-revision", STALE, "--idempotency-key", "k2",
             "--changes", files["empty"]],
            {"draft": d, "base_revision": STALE, "idempotency_key": "k2", "changes": {}}, "same"),
        ("POST", "/api/configure/module/read"): (
            ["draft", "module", "read", d], {"draft": d, "module": ""}, "same"),
        ("POST", "/api/configure/module/preview"): (
            ["draft", "module", "preview", d, "core:rules:absent", "--content", files["text"]],
            {"draft": d, "module": "core:rules:absent", "content": "text\n"}, "same"),
        ("POST", "/api/configure/module/save"): (
            ["draft", "module", "save", d, "core:rules:absent", "--base-revision", STALE,
             "--source-digest", STALE, "--idempotency-key", "k3", "--content", files["text"]],
            {"draft": d, "module": "core:rules:absent", "base_revision": STALE,
             "source_digest": STALE, "idempotency_key": "k3", "content": "text\n"}, "same"),
        ("POST", "/api/configure/authoring/read"): (
            ["draft", "module", "templates", d], {"draft": d}, "same"),
        ("POST", "/api/configure/authoring/preview"): (
            ["draft", "module", "plan", d, "--request", files["empty"]],
            {"draft": d, "request": {}}, "same"),
        ("POST", "/api/configure/authoring/save"): (
            ["draft", "module", "add", d, "--request", files["empty"], "--base-revision", STALE,
             "--idempotency-key", "k4"],
            {"draft": d, "base_revision": STALE, "idempotency_key": "k4", "request": {}}, "same"),
        ("POST", "/api/configure/authoring/library"): (
            ["draft", "module", "library", d], {"draft": d}, "same"),
        ("POST", "/api/configure/apply/review"): (["draft", "review", d], {"draft": d}, "same"),
        ("POST", "/api/configure/apply"): (
            ["draft", "apply", d, "--revision", STALE],
            {"draft": d, "revision": STALE, "confirm": d}, "same"),
        ("POST", "/api/configure/apply/recover"): (
            ["draft", "recover", "--draft", d], {"action": "restore", "confirm": d}, "same"),
        ("POST", "/api/first-run"): (["draft", "first-run", d], {"draft": d}, "same"),
        ("POST", "/api/first-run/start"): (["draft", "first-run", "Not A Name", "--start"],
                                           {"draft": "Not A Name"}, "refused"),
        ("POST", "/api/configure/apply/rollback/preview"): (
            ["draft", "rollback", APPLY_ID, "--preview"], {"apply_id": APPLY_ID}, "same"),
        ("POST", "/api/configure/apply/rollback"): (
            ["draft", "rollback", APPLY_ID, "--draft", d], {"apply_id": APPLY_ID, "confirm": d},
            "same"),
        ("GET", "/api/library"): (["catalog", "--library"], None, "same"),
        ("POST", "/api/activity"): (["activity"], {}, "same"),
        # The Studio wraps the command's own document in an envelope naming it and its basis.
        ("POST", "/api/reports/spend"): (["usage", "--by", "day", "--days", "30"],
                                         {"by": "day", "days": 30}, "ledger"),
        ("GET", "/api/runs/catalog"): (["runs", "catalog"], None, "same"),
        ("POST", "/api/runs/start"): (
            ["runs", "start", "absent-suite", "--target-kind", "installed", "--target-ref",
             str(REPO), "--param", "root=" + str(REPO)],
            {"suite_id": "absent-suite", "parameters": {"root": str(REPO)},
             "target_kind": "installed", "target_ref": str(REPO)}, "refused"),
        ("POST", "/api/runs/show"): (["runs", "show", UNKNOWN_RUN], {"run_id": UNKNOWN_RUN},
                                     "refused"),
        ("POST", "/api/runs/history"): (["runs", "history"], dict(HISTORY), "same"),
        ("POST", "/api/runs/detail"): (
            ["runs", "detail", UNKNOWN_RUN],
            {"run_id": UNKNOWN_RUN, "lineage_limit": 50, "lineage_cursor": None}, "refused"),
        ("POST", "/api/runs/case-history"): (
            ["runs", "case-history", "absent-case"],
            {"case_id": "absent-case", "limit": 50, "cursor": None}, "same"),
        ("POST", "/api/runs/evidence"): (
            ["runs", "evidence", UNKNOWN_RUN, "stdout"],
            {"run_id": UNKNOWN_RUN, "artifact": "stdout"}, "refused"),
        ("POST", "/api/runs/rerun"): (["runs", "rerun", UNKNOWN_RUN], {"run_id": UNKNOWN_RUN},
                                      "refused"),
        ("POST", "/api/runs/cancel"): (["runs", "cancel", UNKNOWN_RUN], {"run_id": UNKNOWN_RUN},
                                       "refused"),
        ("POST", "/api/runs/compare"): (
            ["runs", "compare", UNKNOWN_RUN + ":1", UNKNOWN_RUN + ":2"],
            {"base": {"run_id": UNKNOWN_RUN, "target": 1},
             "candidate": {"run_id": UNKNOWN_RUN, "target": 2}}, "refused"),
        ("POST", "/api/configure/test/register"): (
            ["draft", "test", d, "--register", "--model", "claude-test", "--repetitions", "1",
             "--task", "one", "--pack", "absent", "--pack-digest", "0" * 64, "--effect", "0.1"],
            {"draft": d, "request": dict(FORM), "effect": 0.1, "cv": None}, "refused"),
        ("POST", "/api/configure/test/verdicts"): (["draft", "test", d], {"draft": d}, "same"),
        ("POST", "/api/reports/trends"): (["reports", "trends"], {}, "same"),
        ("POST", "/api/rules/health"): (["usage", "--rules", "--health"], {}, "same"),
        ("POST", "/api/rules/try-without"): (
            ["draft", "try-without", "not a rule"], {"rule": "not a rule"}, "refused"),
    }


def headless_cases(files):
    """Every `citizen runs GROUP ACTION`: an empty body is refused alike; a catalog answers."""
    table = {}
    for group, actions in headless.ROUTES.items():
        for action, (method, path) in actions.items():
            argv = ["runs", group, action]
            if method == "GET" or (group, action) == ("eval", "catalog"):
                table[(method, path)] = (argv, None if method == "GET" else {}, "same")
            else:
                table[(method, path)] = (argv + ["--request", files["empty"]], {}, "refused")
    return table


class ShapeParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        base = Path(os.path.realpath(cls.temporary.name))
        cls.home = base / "home"
        config = json.loads((REPO / "config.example.json").read_text(encoding="utf-8"))
        config["primitive_roots"] = []
        target = cls.home / ".config" / "agent-harness" / "config.json"
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("HARNESS_") and key != "CLAUDE_CONFIG_DIR"}
        env.update({"HOME": str(cls.home), "HARNESS_HOME": str(cls.home),
                    "HARNESS_WORKTREE_ROOT": str(base / "worktrees")})
        cls.env = env
        cls.files = {"empty": str(base / "empty.json"), "text": str(base / "text.md")}
        Path(cls.files["empty"]).write_text("{}", encoding="utf-8")
        Path(cls.files["text"]).write_text("text\n", encoding="utf-8")
        cls.draft = draft_support.draft_name("parity-")
        created = cls.cli(["draft", "create", cls.draft])
        if created[0] != 0:
            cls.temporary.cleanup()
            raise AssertionError("draft create failed: %r" % (created,))
        cls.revision = created[1]["revision"]

    @classmethod
    def tearDownClass(cls):
        try:
            draft_support.discard_draft(unittest.TestCase(), cls.draft, cls.env)
        finally:
            cls.temporary.cleanup()

    @classmethod
    def cli(cls, argv):
        # As a person who confirmed at the Mac (`presence_stub_cli.py`).
        done = subprocess.run([sys.executable, str(REPO / "tests" / "presence_stub_cli.py"), *argv, "--json"], cwd=REPO, env=cls.env,
                              capture_output=True, text=True, timeout=600)
        printed = None
        for text in (done.stdout, (done.stdout.strip().splitlines() or [""])[-1]):
            try:
                printed = json.loads(text)
                break
            except ValueError:
                continue
        return done.returncode, printed, done.stderr[-400:]

    def route(self, method, path, request):
        with mock.patch.dict(os.environ, self.env, clear=True):
            return headless.call_route(REPO, self.home / ".local" / "state" / "agent-harness"
                                       / "studio", method, path, request)

    def table(self):
        table = cases(self.draft, self.revision, self.files)
        table.update(headless_cases(self.files))
        return table

    def test_every_domain_route_has_a_fixture_entry(self):
        table = self.table()
        domain = {(route.method, route.path) for route in server.ROUTES.entries
                  if route.parity_exemption is None}
        self.assertEqual(sorted(domain - set(table) - NOT_JSON), [])
        self.assertEqual(sorted(set(table) - domain), [])

    def test_each_command_answers_as_its_route(self):
        table = self.table()
        for (method, path), (argv, request, expected) in sorted(table.items()):
            with self.subTest(route="%s %s" % (method, path), command=" ".join(argv)):
                status, sent = self.route(method, path, request)
                code, printed, stderr = self.cli(argv)
                self.assertIsInstance(printed, dict, "no JSON object: " + stderr)
                if expected == "refused":
                    self.assertGreaterEqual(status, 400, sent)
                    self.assertNotEqual(code, 0, printed)
                    self.assertEqual(printed, sent)
                    continue
                self.assertEqual(status, 200, sent)
                if expected == "ledger":
                    shown = [word.replace("{by}", request["by"]).replace(
                        "{days}", str(request["days"]))
                        for word in server.ROUTES.resolve(method, path).cli_command]
                    self.assertEqual(shown, ["citizen"] + argv + ["--json"])
                    self.assertEqual(sorted(sent["command"].split()), sorted(shown))
                    self.assertEqual(sorted(printed), sorted(sent["ledger"]))
                    continue
                self.assertNotIn("error", set(printed) - set(sent), printed)
                self.assertEqual(sorted(printed), sorted(sent))
                server.ROUTES.resolve(method, path).response_schema.validate(printed)


if __name__ == "__main__":
    unittest.main()

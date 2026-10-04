# SPDX-License-Identifier: MIT
"""Agents run the Studio loop from `citizen`: every domain route names a command that parses and
prints JSON, the commands answer as the routes do, and a headless loop applies what the Studio
applies. Run: python3 -m unittest tests.test_studio_agent_loop"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from test_harness import REPO, harness
from harness_core.studio import apply as draft_apply
from harness_core.studio import headless, server

import draft_support

SKILL = REPO / "primitives" / "skills" / "studio-loop" / "SKILL.md"
SWITCHED = "cache-hygiene"


def _filled(command):
    """A route's shown command with each placeholder given a value, as argv after `citizen`."""
    argv = []
    for word in command[1:]:
        word = re.sub(r"\{[a-z_]+\}", "value", word)
        argv.append(word[1:-1] if word.startswith("[") and word.endswith("]") else word)
    return argv


# Fields minted fresh on every answer: a new progress log id, and how long the answer took.
VOLATILE = ("progress_id", "generated_ms")


def _stable(value):
    if isinstance(value, dict):
        return {key: ("<volatile>" if key in VOLATILE else _stable(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [_stable(item) for item in value]
    return value


def _parse(argv):
    """The parsed namespace, or the reason argparse refused the command."""
    errors = io.StringIO()
    try:
        with contextlib.redirect_stderr(errors), contextlib.redirect_stdout(io.StringIO()):
            return harness.build_parser().parse_args(argv)
    except SystemExit:
        return errors.getvalue().strip().splitlines()[-1:] or ["refused"]


def parity_failures(routes):
    """Every domain route whose shown command is not a working `citizen ... --json` command."""
    failures = []
    for route in routes:
        if route.parity_exemption is not None:
            continue
        name = "%s %s" % (route.method, route.path)
        command = route.cli_command or ()
        if command[:1] != ("citizen",):
            failures.append((name, "not a citizen command: %r" % (command,)))
            continue
        parsed = _parse(_filled(command))
        if not isinstance(parsed, argparse.Namespace):
            failures.append((name, "does not parse: %s" % parsed))
        elif not getattr(parsed, "json", False):
            failures.append((name, "does not print JSON"))
    return failures


class ParityTests(unittest.TestCase):
    def test_every_domain_route_names_a_citizen_command_that_parses_with_json(self):
        """AC1: a route's command must run, not only be named."""
        self.assertEqual(parity_failures(server.ROUTES.entries), [])

    def test_the_check_fails_an_unmapped_or_broken_route(self):
        """AC1: an unmapped route is refused, and a command that does not parse fails the check."""
        handler = server.ROUTES.entries[0].handler
        with self.assertRaisesRegex(ValueError, "must name its citizen command"):
            server.RouteRegistry((server.Route("POST", "/api/new", "application/json",
                                               server.SESSION, handler, None),))
        broken = [
            server.Route("POST", "/api/a", "application/json", server.SESSION, handler, None,
                         "application/json", ("citizen", "runs", "spend-preview")),
            server.Route("POST", "/api/b", "application/json", server.SESSION, handler, None,
                         "application/json", ("python3", "scripts/native_acceptance.py")),
            server.Route("GET", "/api/c", "application/json", server.SESSION, handler, None,
                         cli_command=("citizen", "catalog", "--nonsense")),
            server.Route("GET", "/api/d", "application/json", server.SESSION, handler, None,
                         cli_command=("citizen", "runs", "show", "{run_id}")),
        ]
        self.assertEqual([path for path, _reason in parity_failures(broken)],
                         ["POST /api/a", "POST /api/b", "GET /api/c", "GET /api/d"])

    def test_every_headless_action_is_the_command_its_route_shows(self):
        for group, actions in headless.ROUTES.items():
            for action, (method, path) in actions.items():
                with self.subTest(group=group, action=action):
                    route = server.ROUTES.resolve(method, path)
                    self.assertIsNotNone(route)
                    self.assertEqual(route.media_type, "application/json")
                    self.assertEqual(route.cli_command, headless.cli_command(group, action))

    def test_every_paid_catalog_suite_has_its_own_admission(self):
        catalog = json.loads((REPO / "policy" / "studio" / "suites.json").read_text(encoding="utf-8"))
        paid = {suite["id"] for suite in catalog["suites"] if suite["cost_class"] == "spends_usage"}
        self.assertEqual(paid, set(headless.ADMITTED_SUITES))
        for group in headless.ADMITTED_SUITES.values():
            self.assertIn("start", headless.ROUTES[group])


class CommandShapeTests(unittest.TestCase):
    """The CLI answers with the object the Studio route sends."""

    def setUp(self):
        self.home = Path(os.path.realpath(tempfile.mkdtemp()))
        self.addCleanup(lambda: __import__("shutil").rmtree(str(self.home), ignore_errors=True))
        self.state = self.home / "state"

    def cli(self, *argv, stdin=""):
        output = io.StringIO()
        with mock.patch.object(harness, "state_dir", return_value=self.state), \
                mock.patch.object(sys, "stdin", io.StringIO(stdin)), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = harness.main(list(argv))
        return code, json.loads(output.getvalue())

    def route(self, method, path, request=None):
        return headless.call_route(REPO, self.state / "studio", method, path, request)

    def test_library_and_schema_commands_answer_as_their_routes(self):
        for argv, path in ((("catalog", "--library", "--json"), "/api/library"),
                           (("draft", "settings", "schema", "--json"), "/api/configure/schema")):
            with self.subTest(path=path):
                code, printed = self.cli(*argv)
                status, sent = self.route("GET", path)
                self.assertEqual((code, status), (0, 200))
                self.assertEqual(_stable(printed), _stable(sent))

    def test_doctor_json_is_the_overview_the_studio_shows(self):
        code, printed = self.cli("doctor", "--json")
        self.assertEqual(code, 0)
        server.ROUTES.resolve("GET", "/api/overview").response_schema.validate(printed)

    def test_headless_commands_print_the_route_answer_and_its_refusals(self):
        code, printed = self.cli("runs", "eval", "catalog", "--json")
        self.assertEqual((code, printed), (0, self.route("POST", "/api/evals/catalog", {})[1]))
        code, printed = self.cli("runs", "native", "catalog", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(_stable(printed), _stable(
            self.route("GET", "/api/experiments/native-acceptance/catalog")[1]))
        # The Studio's own 400 for a malformed body, through stdin and through a missing file.
        self.assertEqual(self.cli("runs", "replay", "preview", "--request", "-", "--json",
                                  stdin='{"unexpected": 1}'), (2, {"error": "invalid_request"}))
        self.assertEqual(self.route("POST", "/api/runs/replay/preview", {"unexpected": 1}),
                         (400, {"error": "invalid_request"}))
        self.assertEqual(self.cli("runs", "draft-test", "plan", "--request",
                                  str(self.home / "absent.json"), "--json"),
                         (2, {"error": "invalid_request"}))
        self.assertEqual(self.cli("runs", "replay", "start", "--json"),
                         (2, {"error": "invalid_request"}))

    def test_a_paid_start_without_the_previewed_token_is_refused(self):
        """The spend guard: a start must carry the token its own preview issued."""
        request = {"request": {"targets": []}, "confirmation_token": "not-issued"}
        code, printed = self.cli("runs", "replay", "start", "--request", "-", "--json",
                                 stdin=json.dumps(request))
        self.assertEqual(code, 2)
        self.assertEqual(printed, self.route("POST", "/api/runs/replay/start", request)[1])
        self.assertIn("error", printed)

    def test_runs_start_refuses_a_suite_that_has_its_own_admission(self):
        """A generic start would skip the target checks the Studio's admission makes."""
        for suite, group in sorted(headless.ADMITTED_SUITES.items()):
            with self.subTest(suite=suite):
                code, printed = self.cli("runs", "start", suite, "--target-kind", "installed",
                                         "--target-ref", str(REPO), "--json")
                self.assertEqual(code, 2)
                self.assertIn("citizen runs %s preview" % group, printed["error"])
        self.assertFalse((self.state / "studio" / "runs").exists()
                         and any((self.state / "studio" / "runs").iterdir()))


class Home:
    """One isolated user, its configuration and the environment that points at it."""

    def __init__(self, base, label, config):
        self.path = Path(os.path.realpath(str(base))) / label
        self.config = draft_apply.config_file(self.path)
        self.config.parent.mkdir(parents=True)
        self.config.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        self.state = self.path / ".local" / "state" / "agent-harness"
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("HARNESS_") and key != "CLAUDE_CONFIG_DIR"}
        env.update({"HOME": str(self.path), "HARNESS_HOME": str(self.path),
                    "HARNESS_WORKTREE_ROOT": str(Path(base) / "worktrees")})
        self.env = env

    def cli(self, *args):
        import subprocess
        done = subprocess.run([sys.executable, str(REPO / "bin" / "harness"), *args], cwd=REPO,
                              env=self.env, capture_output=True, text=True, timeout=600)
        return done.returncode, json.loads(done.stdout.strip().splitlines()[-1])

    def route(self, path, request):
        with mock.patch.dict(os.environ, self.env, clear=True):
            return headless.call_route(REPO, self.state / "studio", "POST", path, request)

    def normalized(self, relative):
        path = self.path / relative
        return (path.read_text(encoding="utf-8").replace(str(self.path), "<HOME>")
                if path.is_file() else None)


class HeadlessLoopTests(unittest.TestCase):
    def test_the_headless_loop_applies_and_rolls_back_what_the_studio_does(self):
        """AC3: the same choices through `citizen` and through the Studio routes."""
        initial = json.loads((REPO / "config.example.json").read_text(encoding="utf-8"))
        initial["primitive_roots"] = []
        with tempfile.TemporaryDirectory() as base:
            agent, studio = Home(base, "agent", initial), Home(base, "studio", initial)
            names = {}
            try:
                for side, home in (("agent", agent), ("studio", studio)):
                    draft = draft_support.draft_name("loop-%s-" % side)
                    names[side] = draft
                    code, created = home.cli("draft", "create", draft, "--json")
                    self.assertEqual(code, 0, created)
                    names[side + "-revision"] = created["revision"]
                changes = {"rules." + SWITCHED: "off"}
                changes_file = Path(base) / "changes.json"
                changes_file.write_text(json.dumps(changes), encoding="utf-8")

                # The agent: save, review, apply, all through citizen.
                name = names["agent"]
                code, saved = agent.cli("draft", "selection", "save", name, "--base-revision",
                                        names["agent-revision"], "--idempotency-key",
                                        uuid.uuid4().hex, "--changes", str(changes_file), "--json")
                self.assertEqual(code, 0, saved)
                self.assertTrue(saved["saved"], saved)
                code, reviewed = agent.cli("draft", "review", name, "--json")
                self.assertTrue(reviewed["can_apply"], reviewed)
                code, applied = agent.cli("draft", "apply", name, "--revision",
                                          reviewed["draft"]["revision"], "--json")
                self.assertEqual((code, applied["status"]), (0, "applied"), applied)

                # The Studio: the same choices through its routes.
                name = names["studio"]
                status, saved_s = studio.route("/api/configure/selection/save", {
                    "draft": name, "base_revision": names["studio-revision"],
                    "idempotency_key": uuid.uuid4().hex, "changes": changes})
                self.assertEqual(status, 200, saved_s)
                self.assertTrue(saved_s["saved"], saved_s)
                status, reviewed_s = studio.route("/api/configure/apply/review", {"draft": name})
                self.assertTrue(reviewed_s["can_apply"], reviewed_s)
                status, applied_s = studio.route("/api/configure/apply", {
                    "draft": name, "revision": reviewed_s["draft"]["revision"], "confirm": name})
                self.assertEqual((status, applied_s["status"]), (200, "applied"), applied_s)

                # One result shape, and one applied state.
                self.assertEqual(sorted(saved), sorted(saved_s))
                self.assertEqual(sorted(reviewed), sorted(reviewed_s))
                self.assertEqual(sorted(applied), sorted(applied_s))
                self.assertEqual([row["key"] for row in reviewed["config"]],
                                 [row["key"] for row in reviewed_s["config"]])
                config = ".config/agent-harness/config.json"
                self.assertEqual(agent.normalized(config), studio.normalized(config))
                self.assertEqual(json.loads(agent.config.read_text(encoding="utf-8"))["rules"][SWITCHED],
                                 "off")
                ledger = ".local/state/agent-harness/ownership.json"
                self.assertEqual(agent.normalized(ledger), studio.normalized(ledger))

                # Rolling back restores the starting configuration on both sides.
                code, rolled = agent.cli("draft", "rollback", applied["apply_id"], "--draft",
                                         names["agent"], "--json")
                self.assertEqual(code, 0, rolled)
                status, rolled_s = studio.route("/api/configure/apply/rollback", {
                    "apply_id": applied_s["apply_id"], "confirm": names["studio"]})
                self.assertEqual(status, 200, rolled_s)
                self.assertEqual((rolled["status"], sorted(rolled)),
                                 (rolled_s["status"], sorted(rolled_s)))
                for home in (agent, studio):
                    self.assertEqual(json.loads(home.config.read_text(encoding="utf-8")), initial)
            finally:
                for side, home in (("agent", agent), ("studio", studio)):
                    if side in names:
                        draft_support.discard_draft(self, names[side], home.env)


class SkillTests(unittest.TestCase):
    def setUp(self):
        self.text = SKILL.read_text(encoding="utf-8")

    def test_every_command_the_skill_teaches_parses(self):
        commands = [line.split("#", 1)[0].strip()
                    for block in re.findall(r"```bash\n(.*?)```", self.text, re.S)
                    for line in block.splitlines()]
        commands += re.findall(r"`(citizen [^`]+)`", self.text)
        commands = [command for command in commands if command.startswith("citizen ")]
        self.assertGreater(len(commands), 15)
        for command in commands:
            argv = command.split()[1:]
            if "|" in command or "ACTION" in argv:
                continue  # a family of commands, spelled out elsewhere in the skill
            with self.subTest(command=command):
                parsed = _parse(argv)
                self.assertIsInstance(parsed, argparse.Namespace, parsed)
                self.assertTrue(getattr(parsed, "json", False), command + " omits --json")

    def test_the_skill_hands_over_the_url_from_json(self):
        """AC2: the link comes from `--json`, never a screenshot or a description."""
        self.assertIn("citizen studio --detach --json", self.text)
        self.assertIn("Hand over that `url`", self.text)
        self.assertIn("Do not send a screenshot or a description", self.text)

    def test_the_studio_json_names_the_url_the_skill_hands_over(self):
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            harness._studio_report({"url": "http://nonce.localhost:1/", "port": 1, "pid": 2,
                                    "reused": True, "port_fallback": False}, True)
        self.assertEqual(json.loads(printed.getvalue())["url"], "http://nonce.localhost:1/")

    def test_the_skill_keeps_the_studio_confirmations(self):
        for phrase in ("Spend is the user's call", "Apply only a reviewed revision",
                       "A refusal is an answer", "`--via-studio`, which only the Studio passes"):
            self.assertIn(phrase, self.text)


if __name__ == "__main__":
    unittest.main()

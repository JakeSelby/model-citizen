# SPDX-License-Identifier: MIT
"""Agents run the Studio loop from `citizen`: every domain route names a command that parses and
prints JSON, the commands answer as the routes do, and a headless loop applies what the Studio
applies. Run: python3 -m unittest discover -s tests -p test_agent_studio_loop.py"""
from __future__ import annotations

import argparse
import contextlib
import http.client
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
import uuid
from pathlib import Path
from unittest import mock

from test_harness import REPO, harness
from harness_core.studio import apply as draft_apply
from harness_core.studio import auth, draft_tests, eval_tiers, headless, native_acceptance, server
from harness_core.studio.state import Store

import draft_support
import presence_support
from studio_target_support import FixtureTargetService
from test_studio_replay import fixture_tasks
from test_studio_security import StudioSecurityFixture

SKILL = REPO / "primitives" / "skills" / "studio-loop" / "SKILL.md"
SWITCHED = "cache-hygiene"


# Placeholders that must parse as a number or a choice; every other placeholder takes a word.
NUMERIC = {"repetitions": "3", "effect": "0.1", "days": "30", "by": "day"}


def _filled(command):
    """A route's shown command with each placeholder given a value, as argv after `citizen`."""
    argv = []
    for word in command[1:]:
        word = re.sub(r"\{([a-z_]+)\}", lambda match: NUMERIC.get(match.group(1), "value"), word)
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
        for groups in headless.ADMITTED_SUITES.values():
            for group in groups:
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
                contextlib.redirect_stdout(output):
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
        # A real reading, not the fallback the route sends when its sources fail.
        self.assertNotEqual(printed["doctor"]["status"], "failed", printed["doctor"])
        self.assertNotEqual(printed["doctor"].get("message"), "Overview sources are unavailable.")
        self.assertTrue(printed["doctor"]["checks"])
        self.assertTrue(any(str(REPO) in check["message"] for check in printed["doctor"]["checks"]))

    def test_a_run_state_refusal_is_printed_as_json(self):
        with mock.patch.object(headless.runs, "RunSupervisor",
                               side_effect=headless.runs.RunError("run state is unsafe")):
            code, printed = self.cli("runs", "eval", "catalog", "--json")
        self.assertEqual((code, printed), (1, {"error": "run_state_unavailable"}))

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

    def test_runs_start_refuses_a_suite_that_has_its_own_admission(self):
        """A generic start would skip the target checks the Studio's admission makes."""
        for suite, groups in sorted(headless.ADMITTED_SUITES.items()):
            with self.subTest(suite=suite):
                reason = io.StringIO()
                with contextlib.redirect_stderr(reason):
                    code, printed = self.cli("runs", "start", suite, "--target-kind", "installed",
                                             "--target-ref", str(REPO), "--json")
                self.assertEqual((code, printed), (2, {"error": "invalid_run"}))
                for group in groups:
                    self.assertIn("`citizen runs %s %s`" % (group, headless.START_ACTIONS[group]),
                                  reason.getvalue())
        self.assertFalse((self.state / "studio" / "runs").exists()
                         and any((self.state / "studio" / "runs").iterdir()))


REQUESTS = REPO / "primitives" / "skills" / "studio-loop" / "requests.md"
EXAMPLE = re.compile(r"`citizen runs ([a-z-]+) ([a-z]+) --request FILE --json`.*?\n```json\n(.*?)```", re.S)


def request_examples():
    """(group, action) -> the request body requests.md shows for that command."""
    return {(group, action): json.loads(body)
            for group, action, body in EXAMPLE.findall(REQUESTS.read_text(encoding="utf-8"))}


class RequestExampleTests(unittest.TestCase):
    """Every `--request` example is the body its route takes."""

    def test_every_request_command_has_an_example(self):
        wanted = {(group, action) for group, actions in headless.ROUTES.items()
                  for action, (method, _path) in actions.items()
                  if method == "POST" and (group, action) != ("eval", "catalog")}
        self.assertEqual(set(request_examples()), wanted)

    def test_each_example_has_exactly_the_keys_its_route_requires(self):
        for (group, action), body in sorted(request_examples().items()):
            with self.subTest(command="%s %s" % (group, action)):
                route = server.ROUTES.resolve(*headless.ROUTES[group][action])
                if (group, action) == ("eval", "run"):  # checked inline by its handler
                    self.assertIn(set(body), ({"suite"}, {"suite", "raw"}))
                    continue
                required = []
                handler = mock.Mock(request_json=body)

                def record(_handler, names):
                    required.append(set(names))
                    return None

                with mock.patch.object(server, "_required_request", side_effect=record):
                    route.handler(handler, route)
                self.assertEqual(required[:1], [set(body)])

    def test_the_inner_requests_parse_as_their_admissions_parse_them(self):
        examples = request_examples()
        eval_tiers.PaidRequest.parse(examples[("eval", "preview")]["request"])
        native_acceptance.SpendRequest.parse(examples[("native", "preview")]["spend"])
        selection = dict(examples[("native", "preview")]["selection"], progress_id="a" * 32)
        with tempfile.TemporaryDirectory() as state:
            _status, catalog = headless.call_route(
                REPO, Path(os.path.realpath(state)), "GET",
                "/api/experiments/native-acceptance/catalog")
        self.assertEqual(set(selection), set(catalog["initial"]))
        self.assertEqual(selection["source_commit"], catalog["initial"]["source_commit"])
        native_acceptance.Selection.parse(REPO, selection)
        plan = examples[("draft-test", "plan")]
        self.assertEqual(set(plan["request"]), set(draft_tests.FORM_KEYS))
        draft_tests.parse_plan(plan["effect"], plan["cv"])
        start = examples[("draft-test", "start")]
        self.assertEqual(set(start) - {"confirmation_token", "request"},
                         set(plan) - {"request"})


class SpendGuardHttpTests(unittest.TestCase):
    """The spend guard over HTTP and from the CLI, on one run store: the skill's preview example
    previews, a start with that preview's unchanged request and token is admitted from either
    face, and a forged or mismatched token is refused alike. The launcher is mocked, so nothing
    runs and nothing spends."""

    def setUp(self):
        self.base = Path(os.path.realpath(tempfile.mkdtemp()))
        self.addCleanup(lambda: shutil.rmtree(str(self.base), ignore_errors=True))
        self.state = self.base / "state"
        self.state.mkdir()
        patches = [
            fixture_tasks("link-alias"),
            mock.patch.object(headless.targets, "TargetService",
                              lambda _repository: FixtureTargetService()),
            mock.patch.object(headless.runs.RunSupervisor, "_admit_locked"),
            mock.patch.dict(os.environ, {"HOME": str(self.base), "HARNESS_HOME": str(self.base)}),
        ]
        for patch in patches:
            patch.__enter__()
            self.addCleanup(patch.__exit__, None, None, None)
        self.store = Store(self.state / "studio")
        self.store.__enter__()
        self.addCleanup(self.store.__exit__, None, None, None)
        self.server, _fallback = server.bind(REPO / "studio" / "dist", "credential", self.store, 0)
        thread = threading.Thread(target=self.server.serve_forever,
                                  kwargs={"poll_interval": 0.01})
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        token, _form = self.server.sessions.issue()
        session, _form = self.server.sessions.consume(token)
        self.headers = {"Host": self.server.host, "Origin": "http://" + self.server.host,
                        "Cookie": "%s=%s" % (auth.SESSION_COOKIE, session.cookie),
                        "X-Studio-CSRF": session.csrf, "Content-Type": "application/json"}

    def http(self, path, body):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1],
                                                timeout=60)
        try:
            connection.request("POST", path, json.dumps(body), self.headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def cli(self, *argv, stdin=""):
        output = io.StringIO()
        with mock.patch.object(harness, "state_dir", return_value=self.state), \
                mock.patch.object(sys, "stdin", io.StringIO(stdin)), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = harness.main(list(argv))
        return code, json.loads(output.getvalue())

    def test_each_face_admits_the_others_preview_and_refuses_a_forged_token(self):
        body = request_examples()[("replay", "preview")]
        status, by_http = self.http("/api/runs/replay/preview", body)
        self.assertEqual(status, 200, by_http)
        code, by_cli = self.cli("runs", "replay", "preview", "--request", "-", "--json",
                                stdin=json.dumps(body))
        self.assertEqual(code, 0, by_cli)
        self.assertEqual(sorted(by_cli), sorted(by_http))
        self.assertTrue(by_http["confirmation_required"])

        for request, refused in (
                ({"request": by_http["request"], "confirmation_token": "f" * 64}, True),
                ({"request": dict(by_http["request"], spend_cap_usd="21"),
                  "confirmation_token": by_http["confirmation_token"]}, True)):
            status, sent = self.http("/api/runs/replay/start", request)
            code, printed = self.cli("runs", "replay", "start", "--request", "-", "--json",
                                     stdin=json.dumps(request))
            self.assertEqual((status, sent), (400, {"error": "replay_refused"}))
            self.assertEqual((code, printed), (2, sent))

        # The HTTP preview's token admits the CLI start, and the CLI preview's the HTTP start.
        code, started = self.cli("runs", "replay", "start", "--request", "-", "--json",
                                 stdin=json.dumps({"request": by_http["request"],
                                                   "confirmation_token": by_http["confirmation_token"]}))
        self.assertEqual(code, 0, started)
        status, started_http = self.http("/api/runs/replay/start", {
            "request": by_cli["request"], "confirmation_token": by_cli["confirmation_token"]})
        self.assertEqual(status, 200, started_http)
        self.assertEqual(sorted(started), sorted(started_http))
        self.assertNotEqual(started["run_id"], started_http["run_id"])
        # A token is spent once.
        status, again = self.http("/api/runs/replay/start", {
            "request": by_cli["request"], "confirmation_token": by_cli["confirmation_token"]})
        self.assertEqual((status, again), (400, {"error": "replay_refused"}))


class StudioJsonTests(unittest.TestCase):
    """AC2: `citizen studio --detach --json` prints a `url` that reaches the running Studio."""

    def test_the_detached_studio_prints_a_url_that_answers(self):
        base = Path(os.path.realpath(tempfile.mkdtemp()))
        self.addCleanup(lambda: shutil.rmtree(str(base), ignore_errors=True))
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("HARNESS_") and key != "CLAUDE_CONFIG_DIR"}
        env.update(HOME=str(base), HARNESS_HOME=str(base))

        def citizen(*argv):
            done = subprocess.run([sys.executable, str(REPO / "bin" / "harness"), *argv],
                                  cwd=REPO, env=env, capture_output=True, text=True, timeout=60)
            return done.returncode, json.loads(done.stdout.strip().splitlines()[-1])

        code, launched = citizen("studio", "--detach", "--no-open", "--json")
        self.addCleanup(citizen, "studio", "stop", "--json")
        self.assertEqual(code, 0, launched)
        url = urllib.parse.urlsplit(launched["url"])
        self.assertEqual(url.scheme, "http")
        self.assertTrue(url.hostname.endswith(".localhost"), url.hostname)

        def get(host):
            connection = http.client.HTTPConnection("127.0.0.1", url.port, timeout=10)
            try:
                connection.request("GET", url.path or "/", headers={"Host": host})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        # The url's exact origin is the one this Studio answers; any other host is refused.
        # Anonymous, it asks for the session the launcher's browser bootstrap provides.
        self.assertEqual(get(url.netloc), (401, {"error": "unauthorized"}))
        self.assertEqual(get("other.localhost:%d" % url.port), (403, {"error": "request_refused"}))
        code, status = citizen("studio", "status", "--json")
        self.assertEqual((code, status["running"], status["url"]), (0, True, launched["url"]))


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
        # As a person who confirmed at the Mac (`presence_support.present_cli`).
        done = subprocess.run([*presence_support.present_cli(), *args], cwd=REPO,
                              env=self.env, capture_output=True, text=True, timeout=600)
        return done.returncode, json.loads(done.stdout.strip().splitlines()[-1])

    def normalized(self, relative):
        path = self.path / relative
        return (path.read_text(encoding="utf-8").replace(str(self.path), "<HOME>")
                if path.is_file() else None)


class HeadlessLoopTests(StudioSecurityFixture):
    """AC3: the same choices through `citizen` and through a running Studio over HTTP."""

    def setUp(self):
        super().setUp()
        presence_support.serve_as_present(self)

    def _post(self, path, payload):
        body = json.dumps(payload).encode("utf-8")
        status, _headers, response = self.request("POST", path, dict(self.trusted, **{
            "Content-Type": "application/json", "Content-Length": str(len(body))}), body)
        return status, json.loads(response)

    def test_the_headless_loop_applies_and_rolls_back_what_the_studio_does(self):
        _issued, status, headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        cookie = self.cookie(headers)
        status, _headers, body = self.request("GET", "/api/session", {"Cookie": cookie})
        self.assertEqual(status, 200)
        self.trusted = {"Cookie": cookie, "Origin": self.record["url"].rstrip("/"),
                        "X-Studio-CSRF": json.loads(body)["csrf_token"]}

        initial = json.loads((REPO / "config.example.json").read_text(encoding="utf-8"))
        initial["primitive_roots"] = []
        agent = Home(self.home.parent, "agent", initial)
        # The Studio side is the running server's own home.
        studio = Home(self.home.parent, "unused", initial)
        studio.path, studio.config = self.home, draft_apply.config_file(self.home)
        studio.config.parent.mkdir(parents=True, exist_ok=True)
        studio.config.write_text(json.dumps(initial, indent=2) + "\n", encoding="utf-8")
        studio.env.update(HOME=str(self.home), HARNESS_HOME=str(self.home))
        names = {}
        for side, home in (("agent", agent), ("studio", studio)):
            draft = draft_support.draft_name("loop-%s-" % side)
            names[side] = draft
            code, created = home.cli("draft", "create", draft, "--json")
            self.assertEqual(code, 0, created)
            draft_support.register_draft_cleanup(self, draft, home.env)
            names[side + "-revision"] = created["revision"]
        changes = {"rules." + SWITCHED: "off"}
        changes_file = self.home.parent / "changes.json"
        changes_file.write_text(json.dumps(changes), encoding="utf-8")

        # The agent: save, review, apply, all through citizen.
        name = names["agent"]
        code, saved = agent.cli("draft", "selection", "save", name, "--base-revision",
                                names["agent-revision"], "--idempotency-key", uuid.uuid4().hex,
                                "--changes", str(changes_file), "--json")
        self.assertEqual(code, 0, saved)
        self.assertTrue(saved["saved"], saved)
        code, reviewed = agent.cli("draft", "review", name, "--json")
        self.assertEqual(code, 0, reviewed)
        self.assertTrue(reviewed["can_apply"], reviewed)
        code, applied = agent.cli("draft", "apply", name, "--revision",
                                  reviewed["draft"]["revision"], "--json")
        self.assertEqual((code, applied["status"]), (0, "applied"), applied)

        # The Studio: the same choices through its HTTP routes.
        name = names["studio"]
        status, saved_s = self._post("/api/configure/selection/save", {
            "draft": name, "base_revision": names["studio-revision"],
            "idempotency_key": uuid.uuid4().hex, "changes": changes})
        self.assertEqual(status, 200, saved_s)
        self.assertTrue(saved_s["saved"], saved_s)
        status, reviewed_s = self._post("/api/configure/apply/review", {"draft": name})
        self.assertEqual(status, 200, reviewed_s)
        self.assertTrue(reviewed_s["can_apply"], reviewed_s)
        status, applied_s = self._post("/api/configure/apply", {
            "draft": name, "revision": reviewed_s["draft"]["revision"], "confirm": name})
        self.assertEqual((status, applied_s["status"]), (200, "applied"), applied_s)

        # One result, apart from the names and revisions that differ by draft.
        self.assertEqual(sorted(saved), sorted(saved_s))
        self.assertEqual(sorted(reviewed), sorted(reviewed_s))
        self.assertEqual(sorted(applied), sorted(applied_s))
        self.assertEqual([(row["key"], row["action"], row["after"]) for row in reviewed["config"]],
                         [(row["key"], row["action"], row["after"]) for row in reviewed_s["config"]])
        self.assertEqual(reviewed["checks"]["status"], reviewed_s["checks"]["status"])
        self.assertEqual([item["step"] for item in reviewed["commands"]],
                         [item["step"] for item in reviewed_s["commands"]])
        config = agent.config.relative_to(agent.path)
        self.assertEqual(agent.normalized(config), studio.normalized(config))
        self.assertEqual(json.loads(agent.config.read_text(encoding="utf-8"))["rules"][SWITCHED],
                         "off")
        ledger = Path(".local") / "state" / "agent-harness" / "ownership.json"
        self.assertEqual(agent.normalized(ledger), studio.normalized(ledger))

        # Rolling back restores the starting configuration on both sides.
        code, rolled = agent.cli("draft", "rollback", applied["apply_id"], "--draft",
                                 names["agent"], "--json")
        self.assertEqual(code, 0, rolled)
        status, rolled_s = self._post("/api/configure/apply/rollback", {
            "apply_id": applied_s["apply_id"], "confirm": names["studio"]})
        self.assertEqual(status, 200, rolled_s)
        self.assertEqual((rolled["status"], sorted(rolled)), (rolled_s["status"], sorted(rolled_s)))
        for home in (agent, studio):
            self.assertEqual(json.loads(home.config.read_text(encoding="utf-8")), initial)


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

    def test_the_skill_keeps_the_studio_confirmations(self):
        for phrase in ("Spend is the user's call", "Apply only a reviewed revision",
                       "A refusal is an answer", "`--via-studio`, which only the Studio passes"):
            self.assertIn(phrase, self.text)


if __name__ == "__main__":
    unittest.main()

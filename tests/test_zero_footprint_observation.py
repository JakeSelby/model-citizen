"""Observing an arm changes no model request: the zero-footprint test AD-23 requires.

A scripted session runs against a stub model endpoint that returns fixed responses, raising every
hook event the arm installs: session start, prompt submit, pre and post tool use, subagent start
and stop, stop and session end. Each installed hook runs as the runtime runs it, a subprocess fed
the event on stdin, and its output is folded back the way Claude Code folds it: context into the
next model request, a system message to the user, a deny or a block into the conversation. Every
request the stub receives is compared, byte for byte after timestamps, ids and run paths are
normalised, between observation on and observation off. It runs per arm: the bare arm, which
installs only the observation entry point, and the harness arm, which installs `hook.py` beside it.
"""
import ast
import contextlib
import http.server
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
import urllib.request
from pathlib import Path
from unittest.mock import patch

from test_harness import REPO
from harness_core import lifecycle, observation, observer
from harness_core.reconcile import is_harness_hook_entry

SESSION = "fixture-session"
PROMPT = "Summarise the repository in one line."
# Call by call, what the recorded model answers: a Bash call, an Agent call, the subagent's own
# reply, the final answer, and the answer to any stop block.
RESPONSES = (
    {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "tool-1", "name": "Bash",
                                             "input": {"command": "echo fixture"}}]},
    {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "tool-2", "name": "Agent",
                                             "input": {"description": "look", "prompt": "List the files."}}]},
    {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Two files."}]},
    {"stop_reason": "end_turn", "content": [{"type": "text", "text": "A fixture repository."}]},
    {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Done."}]},
)
STDLIB = {"datetime", "importlib", "importlib.util", "json", "os", "sys", "pathlib"}
STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class StubModel:
    """A local model endpoint: fixed responses in order, and every request body kept as bytes."""

    def __init__(self):
        self.requests = []
        stub = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                stub.requests.append(body)
                answer = RESPONSES[min(len(stub.requests), len(RESPONSES)) - 1]
                data = json.dumps(answer).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def call(self, messages, system):
        body = json.dumps({"model": "stub", "system": system, "messages": messages},
                          sort_keys=True).encode()
        request = urllib.request.Request("http://127.0.0.1:%d/v1/messages" % self.server.server_port,
                                         data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())


class ScriptedSession:
    """One scripted Claude Code session: the hooks it installs, and what reaches model and user."""

    def __init__(self, hooks, run_root, model):
        self.hooks, self.model = hooks, model
        self.home, self.cwd = run_root / "home", run_root / "work"
        self.home.mkdir(parents=True, exist_ok=True)
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.shown, self.pending, self.runs = [], [], []
        self.env = {"HOME": str(self.home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "LANG": "C.UTF-8", "TMPDIR": str(run_root / "tmp")}
        (run_root / "tmp").mkdir(exist_ok=True)

    def fire(self, event, **fields):
        """Run every hook registered for `event`, and fold its output as the runtime does."""
        payload = dict({"hook_event_name": event, "session_id": SESSION, "cwd": str(self.cwd),
                        "transcript_path": str(self.home / "transcript.jsonl"),
                        "permission_mode": "default"}, **fields)
        folded = {"deny": None, "block": None, "input": None}
        for entry in self.hooks.get(event, []):
            for hook in entry["hooks"]:
                done = subprocess.run(hook["command"], shell=True, input=json.dumps(payload), text=True,
                                      capture_output=True, cwd=str(self.cwd), env=self.env,
                                      timeout=hook.get("timeout", 60) + 30)
                self.runs.append((event, hook["command"], done.returncode, done.stdout, done.stderr))
                self.fold(event, done, folded)
        return folded

    def fold(self, event, done, folded):
        if done.returncode == 2:
            if event in ("PreToolUse", "Stop"):
                folded["deny" if event == "PreToolUse" else "block"] = done.stderr
            self.pending.append(done.stderr)
            return
        if done.returncode != 0:
            self.shown.append(done.stderr)
            return
        text = done.stdout.strip()
        if not text:
            return
        try:
            result = json.loads(text)
        except ValueError:
            result = None
        if not isinstance(result, dict):
            # Plain stdout is context on these two events and transcript text on the rest.
            (self.pending if event in ("SessionStart", "UserPromptSubmit") else self.shown).append(text)
            return
        specific = result.get("hookSpecificOutput") or {}
        if specific.get("additionalContext"):
            self.pending.append(specific["additionalContext"])
        if result.get("systemMessage"):
            self.shown.append(result["systemMessage"])
        if specific.get("updatedInput"):
            folded["input"] = specific["updatedInput"]
        if specific.get("permissionDecision") == "deny":
            folded["deny"] = specific.get("permissionDecisionReason", "")
        if result.get("decision") == "block":
            folded["block"] = result.get("reason", "")

    def user_turn(self, text):
        content = [{"type": "text", "text": text}] + [
            {"type": "text", "text": "<system-reminder>" + note + "</system-reminder>"} for note in self.pending]
        self.pending = []
        return {"role": "user", "content": content}

    def tool_turn(self, tool_id, result):
        content = [{"type": "tool_result", "tool_use_id": tool_id, "content": result}] + [
            {"type": "text", "text": "<system-reminder>" + note + "</system-reminder>"} for note in self.pending]
        self.pending = []
        return {"role": "user", "content": content}

    def run_tool(self, call):
        folded = self.fire("PreToolUse", tool_name=call["name"], tool_input=call["input"],
                           tool_use_id=call["id"])
        if folded["deny"] is not None:
            return "Denied: " + folded["deny"]
        call = dict(call, input=folded["input"] or call["input"])
        if call["name"] == "Agent":
            self.fire("SubagentStart", agent_id="agent-1", agent_type="general-purpose")
            reply = self.model.call([{"role": "user", "content": [{"type": "text", "text": call["input"]["prompt"]}]}],
                                    "subagent")
            self.fire("SubagentStop", agent_id="agent-1", agent_type="general-purpose",
                      stop_hook_active=False)
            result = reply["content"][0]["text"]
        else:
            result = "fixture\n"
        self.fire("PostToolUse", tool_name=call["name"], tool_input=call["input"], tool_use_id=call["id"],
                  tool_response={"stdout": result, "stderr": "", "interrupted": False})
        return result

    def play(self):
        self.fire("SessionStart", source="startup")
        self.fire("UserPromptSubmit", prompt=PROMPT)
        messages = [self.user_turn(PROMPT)]
        for _ in range(8):
            answer = self.model.call(messages, "main")
            messages.append({"role": "assistant", "content": answer["content"]})
            calls = [part for part in answer["content"] if part["type"] == "tool_use"]
            if calls:
                messages.append(self.tool_turn(calls[0]["id"], self.run_tool(calls[0])))
                continue
            folded = self.fire("Stop", stop_hook_active=False)
            if folded["block"] is None:
                break
            messages.append(self.user_turn("Stop hook feedback: " + folded["block"]))
        # A manual compaction: the event is raised, and only an automatic one writes a snapshot.
        self.fire("PreCompact", trigger="manual", custom_instructions="")
        self.fire("SessionEnd", reason="other")


def merged(*blocks):
    hooks = {}
    for block in blocks:
        for event, entries in block["hooks"].items():
            hooks.setdefault(event, []).extend(entries)
    return hooks


def normalised(requests, run_root):
    root = str(run_root)
    out = []
    for body in requests:
        text = body.decode().replace(root, "<RUN>").replace(os.path.realpath(root), "<RUN>")
        out.append(UUID.sub("<ID>", STAMP.sub("<TS>", text)).encode())
    return out


def ledger_rows(run_root):
    path = observer.ledger_path({"HOME": str(run_root / "home")})
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class Arms(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zero-footprint-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), ignore_errors=True)

    def session(self, name, hooks, before=None):
        run_root = self.tmp / name
        with StubModel() as model:
            session = ScriptedSession(hooks, run_root, model)
            if before:
                before(session)
            session.play()
        return session, normalised(model.requests, run_root), run_root

    def bare_hooks(self, name):
        settings = observation.bare_install(self.tmp / (name + "-install"))
        return json.loads(settings.read_text())["hooks"]

    def assert_observer_silent(self, session):
        mine = [run for run in session.runs if observation.MARKER in run[1]]
        self.assertTrue(mine)
        for event, _, code, stdout, stderr in mine:
            self.assertEqual((code, stdout, stderr), (0, "", ""), msg=event)


class BareArmTests(Arms):
    def test_every_model_request_is_byte_identical_with_observation_on_and_off(self):
        _, off, _ = self.session("off", {})
        session, on, run_root = self.session("on", self.bare_hooks("on"))
        self.assertGreaterEqual(len(off), 4)
        self.assertEqual(on, off)
        self.assertEqual(session.shown, [])
        self.assert_observer_silent(session)
        rows = ledger_rows(run_root)
        self.assertEqual([row["event"] for row in rows], [run[0] for run in session.runs])
        self.assertEqual({row["profile_fingerprint"] for row in rows}, {"bare"})

    def test_the_session_raises_every_event_the_arm_installs(self):
        session, _, _ = self.session("events", self.bare_hooks("events"))
        self.assertEqual(sorted({run[0] for run in session.runs}), sorted(lifecycle.EVENTS["claude-code"]))

    def test_the_comparison_catches_a_recorder_that_speaks(self):
        # Proof the comparison bites: one line of context from an "observer" changes a request.
        leaky = self.tmp / "leaky.py"
        leaky.write_text("import json, sys\nsys.stdin.read()\nprint(json.dumps({'hookSpecificOutput': "
                         "{'hookEventName': 'UserPromptSubmit', 'additionalContext': 'observed'}}))\n")
        hooks = {"UserPromptSubmit": [{"hooks": [{"type": "command",
                                                   "command": "python3 " + str(leaky), "timeout": 5}]}]}
        _, off, _ = self.session("off", {})
        _, on, _ = self.session("leaky", hooks)
        self.assertNotEqual(on, off)


class HarnessArmTests(Arms):
    def test_observation_adds_nothing_beyond_what_the_harness_profile_adds(self):
        profile = lifecycle.registration(REPO, "claude-code")
        plain, off, _ = self.session("off", merged(profile))
        session, on, run_root = self.session("on", merged(profile, observation.registration(REPO, "claude-code")))
        self.assertGreaterEqual(len(off), 4)
        self.assertEqual(on, off)
        self.assertEqual(session.shown, plain.shown)
        self.assert_observer_silent(session)
        # Not vacuous: the profile itself does reach the model, through the subagent's brief.
        _, bare, _ = self.session("bare", {})
        self.assertNotEqual(off, bare)
        self.assertEqual(len(ledger_rows(run_root)), len([r for r in session.runs if observation.MARKER in r[1]]))


class FailureTests(Arms):
    def test_an_unwritable_ledger_changes_nothing_and_says_nothing(self):
        def block_ledger(session):
            state = observer.state_dir({"HOME": str(session.home)})
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text("a file where the ledger directory belongs\n")

        _, off, _ = self.session("off", {}, block_ledger)
        session, on, run_root = self.session("on", self.bare_hooks("on"), block_ledger)
        self.assertEqual(on, off)
        self.assertEqual(session.shown, [])
        self.assert_observer_silent(session)
        self.assertEqual(ledger_rows(run_root), [])

    def test_a_crashed_recorder_changes_nothing_and_says_nothing(self):
        install = self.tmp / "crash-install"
        observation.bare_install(install)
        crashing = install / "crash.py"
        crashing.write_text(
            "import importlib.util, sys\n"
            "spec = importlib.util.spec_from_file_location('observe', %r)\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "def record(*args, **kwargs):\n"
            "    raise RuntimeError('recorder crashed')\n"
            "module.record = record\n"
            "module.main(argv=['--runtime', 'claude-code', '--profile', 'bare'])\n" % str(install / "observe.py"))
        hooks = observation.hooks_for("python3 " + str(crashing), "claude-code")
        _, off, _ = self.session("off", {})
        session, on, run_root = self.session("on", hooks)
        self.assertEqual(on, off)
        self.assertEqual(session.shown, [])
        self.assert_observer_silent(session)
        errors = observer.errors_path({"HOME": str(run_root / "home")}).read_text().splitlines()
        self.assertEqual(len(errors), len(session.runs))
        self.assertEqual({json.loads(line)["error"] for line in errors}, {"RuntimeError"})


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="observer-"))
        self.addCleanup(shutil.rmtree, str(self.home), ignore_errors=True)
        patcher = patch.dict(os.environ, {"HOME": str(self.home), "HARNESS_HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, raw, argv=("--profile", "bare")):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = observer.main("claude-code", list(argv), io.StringIO(raw))
        return code, out.getvalue(), err.getvalue()

    def test_a_row_carries_identifiers_and_never_a_body(self):
        payload = {"hook_event_name": "PostToolUse", "session_id": "s", "tool_name": "Bash",
                   "prompt": "secret prompt", "tool_input": {"command": "cat secret"},
                   "tool_response": {"stdout": "secret output"}, "transcript_path": "/x"}
        self.assertEqual(self.run_main(json.dumps(payload)), (0, "", ""))
        row = json.loads(observer.ledger_path().read_text())
        self.assertEqual(sorted(row), ["event", "profile_fingerprint", "runtime", "schema_version",
                                       "session_id", "tool_name", "ts"])
        self.assertNotIn("secret", json.dumps(row))
        self.assertEqual((row["event"], row["profile_fingerprint"], row["schema_version"]),
                         ("PostToolUse", "bare", 1))

    def test_unreadable_input_is_logged_locally_and_the_hook_still_exits_zero_in_silence(self):
        self.assertEqual(self.run_main("not json"), (0, "", ""))
        self.assertFalse(observer.ledger_path().exists())
        error = json.loads(observer.errors_path().read_text())
        self.assertEqual(error["runtime"], "claude-code")

    def test_a_failure_to_log_the_failure_is_dropped(self):
        with patch.object(observer, "record", side_effect=RuntimeError("boom")), \
                patch.object(observer, "append", side_effect=OSError("disk full")):
            self.assertEqual(self.run_main("{}"), (0, "", ""))

    def test_without_a_named_profile_the_checkout_resolves_one_or_stamps_null(self):
        self.assertEqual(self.run_main(json.dumps({"hook_event_name": "Stop"}), argv=()), (0, "", ""))
        value = json.loads(observer.ledger_path().read_text())["profile_fingerprint"]
        self.assertTrue(value is None or re.fullmatch(r"[0-9a-f]{64}", value), value)
        with patch.object(observer.importlib.util, "spec_from_file_location", side_effect=OSError):
            self.assertIsNone(observer.profile_fingerprint())


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.dest = Path(tempfile.mkdtemp(prefix="bare-install-"))
        self.addCleanup(shutil.rmtree, str(self.dest), ignore_errors=True)

    def test_a_bare_install_holds_the_observation_entry_point_and_its_registration_only(self):
        for runtime in ("claude-code", "codex"):
            with self.subTest(runtime=runtime):
                dest = self.dest / runtime
                settings = observation.bare_install(dest, runtime)
                self.assertEqual(observation.install_files(dest),
                                 sorted(["observe.py", observation.SETTINGS_FILE[runtime]]))
                block = json.loads(settings.read_text())
                self.assertEqual(sorted(block), ["hooks"])
                self.assertEqual(sorted(block["hooks"]), sorted(lifecycle.EVENTS[runtime]))
                for entries in block["hooks"].values():
                    command = entries[0]["hooks"][0]["command"]
                    self.assertIn(str(dest / "observe.py"), command)
                    self.assertIn("--profile bare", command)
                    self.assertNotIn(str(REPO), command)

    def test_the_installed_entry_point_imports_nothing_from_the_harness(self):
        observation.bare_install(self.dest)
        tree = ast.parse((self.dest / "observe.py").read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
        self.assertEqual(imported - STDLIB, set())

    def test_the_installed_entry_point_runs_away_from_the_checkout(self):
        observation.bare_install(self.dest)
        home = self.dest / "home"
        done = subprocess.run([sys.executable, str(self.dest / "observe.py"), "--runtime", "claude-code",
                               "--profile", "bare"], input=json.dumps({"hook_event_name": "Stop"}),
                              text=True, capture_output=True, cwd=str(self.dest),
                              env={"HOME": str(home), "PATH": os.environ.get("PATH", "")})
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))
        row = json.loads(observer.ledger_path({"HOME": str(home)}).read_text())
        self.assertEqual((row["event"], row["profile_fingerprint"]), ("Stop", "bare"))


class RegistrationTests(unittest.TestCase):
    def test_observation_registers_from_the_one_event_table_beside_the_dispatcher(self):
        for runtime in ("claude-code", "codex"):
            with self.subTest(runtime=runtime):
                observe = observation.registration(REPO, runtime)["hooks"]
                self.assertEqual(sorted(observe), sorted(lifecycle.registration(REPO, runtime)["hooks"]))
                for event, entries in observe.items():
                    command = entries[0]["hooks"][0]["command"]
                    self.assertIn(observation.MARKER + event.lower(), command)
                    self.assertNotIn("hook.py", command)
                    self.assertTrue((REPO / "adapters" / runtime / "observe.py").is_file())
                    self.assertTrue(is_harness_hook_entry(entries[0]))

    def test_an_event_added_to_the_table_is_observed_without_another_edit(self):
        with patch.dict(lifecycle.EVENTS, {"codex": lifecycle.BASE_EVENTS + ("UserPromptSubmit",)}):
            self.assertIn("UserPromptSubmit", observation.registration(REPO, "codex")["hooks"])

    def test_an_unknown_runtime_is_refused(self):
        with self.assertRaises(ValueError):
            observation.registration(REPO, "other")
        with self.assertRaises(ValueError):
            observation.bare_install(Path(tempfile.gettempdir()) / "never-written", "other")


if __name__ == "__main__":
    unittest.main()

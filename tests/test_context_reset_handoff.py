# SPDX-License-Identifier: MIT
"""Before the context resets: the PreCompact snapshot, the saving session on the task store, and
the hook's registration on Claude Code alone.

The snapshot is the fallback for whatever the hand-off block's own save missed, so these tests
hold what it records from a transcript, that only an automatic compaction gets one, that it never
answers or raises whatever it is given, and that a session keeps only its newest few.

Run: python3 -m unittest discover tests
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import isolation  # noqa: F401 -- keeps git maintenance out of temporary repositories

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))
from harness_core import lifecycle, tasks  # noqa: E402

HOOK = REPO / "policy" / "hooks" / "pre-compact.py"
SESSION = "5f0c1d2e-aaaa-bbbb-cccc-0123456789ab"
# 2026-10-12T14:03:22Z
NOW = 1791813802.0
# Assembled at run time so the fixture identity is not an address-shaped literal the lint reads.
IDENTITY = "reset" + "@" + "example" + ".invalid"


def load_hook():
    spec = importlib.util.spec_from_file_location("harness_pre_compact", str(HOOK))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prompt(text, **extra):
    return dict({"type": "user", "isSidechain": False,
                 "message": {"role": "user", "content": text}}, **extra)


def todos(*items, **extra):
    listed = [{"content": text, "status": status, "activeForm": text} for text, status in items]
    return dict({"type": "assistant", "isSidechain": False, "message": {
        "role": "assistant", "content": [
            {"type": "text", "text": "updating"},
            {"type": "tool_use", "id": "t", "name": "TodoWrite", "input": {"todos": listed}}]}},
        **extra)


class Repository(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.home = base / "home"
        self.home.mkdir()
        self.repo = base / "repo"
        self.repo.mkdir()
        (self.repo / "a.txt").write_text("one\n")
        (self.repo / "b.txt").write_text("two\n")
        self.git("init", "-q", "-b", "work")
        self.git("add", "-A")
        self.git("commit", "-qm", "initial")
        self.transcript = base / "session.jsonl"
        self.transcript.write_text("")
        self.hook = load_hook()
        patcher = mock.patch.dict(os.environ, {"HARNESS_HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def git(self, *args):
        env = dict(os.environ, HOME=str(self.home), GIT_CONFIG_NOSYSTEM="1",
                   GIT_AUTHOR_NAME="Reset Fixture", GIT_COMMITTER_NAME="Reset Fixture",
                   GIT_AUTHOR_EMAIL=IDENTITY, GIT_COMMITTER_EMAIL=IDENTITY)
        subprocess.run(["git", "-C", str(self.repo)] + list(args), check=True,
                       capture_output=True, env=env)

    def write(self, *entries):
        with self.transcript.open("a") as handle:
            for entry in entries:
                handle.write((entry if isinstance(entry, str) else json.dumps(entry)) + "\n")

    def payload(self, **extra):
        return dict({"hook_event_name": "PreCompact", "session_id": SESSION, "trigger": "auto",
                     "cwd": str(self.repo),
                     "transcript_path": str(self.transcript), "custom_instructions": "",
                     "prompt_id": "p-1"}, **extra)

    def snapshots(self, session=SESSION):
        directory = self.repo / ".agent-harness" / "handoffs"
        if not directory.is_dir():
            return []
        return sorted(p.name for p in directory.iterdir() if p.name.startswith("auto-" + session))


class Snapshot(Repository):
    def test_an_auto_compaction_writes_the_state_it_can_read(self):
        long_prompt = "x" * 800
        self.write(prompt("first, dropped as older than five"),
                   prompt("second"),
                   prompt("from a subagent", isSidechain=True),
                   todos(("old item", "pending")),
                   prompt([{"type": "tool_result", "tool_use_id": "t", "content": "done"}]),
                   prompt("meta", isMeta=True),
                   prompt("summary", isCompactSummary=True),
                   prompt([{"type": "text", "text": "third\nacross   lines"}]),
                   "{torn line",
                   prompt("fourth"),
                   todos(("ship it", "completed"), ("write the tests", "in_progress"),
                         ("run the gate", "pending")),
                   todos(("sidechain list", "pending"), isSidechain=True),
                   prompt(long_prompt),
                   prompt("sixth"),
                   # Not typed by the user, so none displaces a prompt from the five.
                   prompt("/compact keep the plan"),
                   prompt("<command-name>/clear</command-name>"),
                   prompt("<local-command-stdout>done</local-command-stdout>"),
                   prompt([{"type": "text", "text": "[Request interrupted by user]"}]),
                   prompt("Stop hook feedback: usage-feed: session context"),
                   prompt([{"type": "image", "source": {"type": "base64", "data": "AAAA"}}]))
        (self.repo / "a.txt").write_text("changed\n")
        (self.repo / "b.txt").write_text("staged\n")
        self.git("add", "b.txt")
        (self.repo / "new.txt").write_text("new\n")
        task = self.repo / ".agent-harness" / "task.json"
        task.parent.mkdir()
        task.write_text(json.dumps({"revision": 3, "session_id": SESSION}))

        target = self.hook.snapshot(self.payload(), now=NOW)

        self.assertEqual(target.name, "auto-" + SESSION + "-20261012T140322.md")
        self.assertEqual(target.parent, self.repo / ".agent-harness" / "handoffs")
        text = target.read_text()
        self.assertIn("- Session: " + SESSION, text)
        self.assertIn("- Taken: 2026-10-12T14:03:22Z", text)
        self.assertIn("- Branch: work", text)
        # a.txt modified, b.txt staged, and new.txt and the `.agent-harness/` folder untracked.
        self.assertIn("- Working tree: 4 changed (1 staged, 1 modified, 2 untracked)", text)
        self.assertIn("- Task store: revision 3, saved by this session", text)
        prompts = text.split("## Last user prompts, oldest first\n\n")[1].split("\n\n")[0]
        self.assertEqual(prompts.splitlines(), [
            "1. second", "2. third across lines", "3. fourth",
            "4. " + "x" * 499 + "…", "5. sixth"])
        items = text.split("## Open todo items\n\n")[1]
        self.assertEqual(items.splitlines(),
                         ["- [in_progress] write the tests", "- [pending] run the gate"])
        self.assertNotIn("subagent", text)
        self.assertNotIn("sidechain list", text)
        # Deterministic: the same inputs at the same moment write the same file.
        self.assertEqual(self.hook.snapshot(self.payload(), now=NOW).read_text(), text)
        # Never committed: the folder's own ignore file covers the snapshot and its temporary.
        self.assertEqual((target.parent / ".gitignore").read_text(), "auto-*\n.auto-*\n")
        for name in (target.name, "." + target.name + ".tmp"):
            done = subprocess.run(["git", "-C", str(self.repo), "check-ignore", "-q",
                                   ".agent-harness/handoffs/" + name])
            self.assertEqual(done.returncode, 0, name)
        listed = subprocess.run(["git", "-C", str(self.repo), "status", "--porcelain",
                                 "--untracked-files=all"], capture_output=True, text=True).stdout
        self.assertNotIn("auto-", listed)
        self.assertIn(".agent-harness/handoffs/.gitignore", listed)

    def test_a_secret_shaped_prompt_is_redacted(self):
        # Assembled at run time so no secret shape is written into a committed file.
        token = "sk-" + "a" * 24
        key = "AKIA" + "B" * 16
        self.write(prompt("use " + token + " and " + key + " then API_TOKEN=hunter2 go"))
        text = self.hook.snapshot(self.payload(), now=NOW).read_text()
        self.assertNotIn(token, text)
        self.assertNotIn(key, text)
        self.assertNotIn("hunter2", text)
        self.assertIn("1. use <redacted> and <redacted> then API_TOKEN=<redacted> go", text)

    def test_without_the_secret_shapes_no_prompt_is_written(self):
        self.write(prompt("anything at all"))
        with mock.patch.object(self.hook, "_REDACTION", [None]):
            text = self.hook.snapshot(self.payload(), now=NOW).read_text()
        self.assertNotIn("anything at all", text)
        self.assertIn("Left out: the secret patterns to redact them with could not be read.", text)

    def test_a_git_that_times_out_still_gets_a_snapshot(self):
        self.write(prompt("keep me"))
        calls = []

        def slow(command, **kwargs):
            calls.append((command, kwargs.get("timeout")))
            raise subprocess.TimeoutExpired(command, kwargs.get("timeout"))

        with mock.patch.object(self.hook.subprocess, "run", side_effect=slow):
            text = self.hook.snapshot(self.payload(), now=NOW).read_text()
        self.assertEqual(calls, [(["git", "-C", str(self.repo), "status", "--porcelain=v1", "-b"],
                                  3)])
        self.assertIn("- Branch: unknown", text)
        self.assertIn("- Working tree: unknown (git did not answer)", text)
        self.assertIn("1. keep me", text)

    def test_a_large_transcript_is_read_from_its_tail_only(self):
        result = json.dumps(prompt([{"type": "tool_result", "tool_use_id": "t",
                                     "content": "r" * 100000}]))
        with self.transcript.open("w") as handle:
            handle.write(json.dumps(prompt("too old to be read")) + "\n")
            for _ in range(400):  # about 40 MB of tool results
                handle.write(result + "\n")
            for number in range(1, 7):
                handle.write(json.dumps(prompt("recent " + str(number))) + "\n")
        parsed = []
        real = json.loads

        def counting(text, *args, **kwargs):
            parsed.append(len(text))
            return real(text, *args, **kwargs)

        started = time.monotonic()
        with mock.patch.object(self.hook.json, "loads", side_effect=counting):
            prompts, _ = self.hook.read_transcript(str(self.transcript))
        elapsed = time.monotonic() - started
        self.assertEqual(prompts, ["recent " + str(n) for n in range(2, 7)])
        self.assertLessEqual(len(parsed), 6)
        self.assertLess(elapsed, 3.0)

    def test_a_manual_compaction_writes_nothing(self):
        self.write(prompt("hello"))
        for trigger in ("manual", None, "AUTO"):
            with self.subTest(trigger=trigger):
                self.assertIsNone(self.hook.snapshot(self.payload(trigger=trigger), now=NOW))
        self.assertEqual(self.snapshots(), [])

    def test_a_missing_transcript_still_records_the_tree(self):
        target = self.hook.snapshot(self.payload(transcript_path=str(self.transcript) + ".gone"),
                                    now=NOW)
        text = target.read_text()
        self.assertIn("- Working tree: clean", text)
        self.assertIn("- Task store: none saved", text)
        self.assertIn("## Last user prompts, oldest first\n\nNone.", text)
        self.assertIn("## Open todo items\n\nNone.", text)

    def test_another_sessions_task_store_is_named_without_the_claim(self):
        task = self.repo / ".agent-harness" / "task.json"
        task.parent.mkdir()
        task.write_text(json.dumps({"revision": 2, "session_id": "someone-else"}))
        text = self.hook.snapshot(self.payload(), now=NOW).read_text()
        self.assertIn("- Task store: revision 2\n", text)

    def test_the_snapshot_is_written_in_the_repository_root_from_a_subdirectory(self):
        nested = self.repo / "deep" / "er"
        nested.mkdir(parents=True)
        target = self.hook.snapshot(self.payload(cwd=str(nested)), now=NOW)
        self.assertEqual(target.parent, self.repo / ".agent-harness" / "handoffs")

    def test_each_session_keeps_its_newest_five(self):
        other = "other-session"
        for offset in range(7):
            self.hook.snapshot(self.payload(), now=NOW + offset)
        self.hook.snapshot(self.payload(session_id=other), now=NOW - 100)
        kept = self.snapshots()
        self.assertEqual(kept, ["auto-" + SESSION + "-20261012T1403" + str(24 + n) + ".md"
                                for n in range(5)])
        self.assertEqual(len(self.snapshots(other)), 1)

    def test_a_snapshot_does_not_make_the_task_store_stale(self):
        saved = tasks.save(self.repo, {"objective": "Continue"}, "claude-code")
        self.assertEqual(saved["status"], "current")
        self.hook.snapshot(self.payload(), now=NOW)
        # A temporary left by a write that was killed part-way is not an input either.
        (self.repo / ".agent-harness" / "handoffs" / (".auto-" + SESSION + "-x.md.tmp")).write_text("")
        self.assertEqual(tasks.read(self.repo)["status"], "current")
        # A handoff written by hand is still a task input.
        (self.repo / ".agent-harness" / "handoffs" / "by-hand.md").write_text("notes\n")
        self.assertEqual(tasks.read(self.repo)["status"], "stale")


class NeverBlocks(Repository):
    def run_main(self, raw):
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(raw)), contextlib.redirect_stdout(out):
            self.hook.main()
        return out.getvalue()

    def errors(self):
        target = self.home / ".local" / "state" / "agent-harness" / "pre-compact.errors.jsonl"
        return [json.loads(line) for line in target.read_text().splitlines()] \
            if target.exists() else []

    def test_bad_input_prints_nothing_and_raises_nothing(self):
        for raw in ("", "{torn", "[]", "null", json.dumps({"trigger": "auto"}),
                    json.dumps(self.payload(session_id="../escape")),
                    json.dumps(self.payload(session_id=7)),
                    json.dumps(self.payload(cwd="/no/such/place")),
                    json.dumps(self.payload(cwd=tempfile.gettempdir()))):
            with self.subTest(raw=raw[:40]):
                self.assertEqual(self.run_main(raw), "")
        self.assertEqual(self.snapshots(), [])
        self.assertEqual([row["where"] for row in self.errors()], ["input", "input"])

    def test_a_failure_while_writing_is_logged_and_swallowed(self):
        (self.repo / ".agent-harness").mkdir()
        (self.repo / ".agent-harness" / "handoffs").symlink_to(self.home)
        self.assertEqual(self.run_main(json.dumps(self.payload())), "")
        self.assertEqual([(row["where"], row["error"]) for row in self.errors()],
                         [("snapshot", "ValueError")])
        self.assertEqual(list(self.home.glob("auto-*")), [])

    def test_the_script_exits_zero_and_silent(self):
        for raw in ("{torn", json.dumps(self.payload())):
            with self.subTest(raw=raw[:10]):
                done = subprocess.run([sys.executable, str(HOOK)], input=raw, text=True,
                                      capture_output=True,
                                      env=dict(os.environ, HARNESS_HOME=str(self.home)))
                self.assertEqual((done.returncode, done.stdout), (0, ""))
        self.assertEqual(len(self.snapshots()), 1)


class SavingSession(Repository):
    def cli(self, *args, **env):
        environment = dict(os.environ, HOME=str(self.home), HARNESS_HOME=str(self.home))
        for name in ("HARNESS_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CONFIG_DIR"):
            environment.pop(name, None)
        environment.update(env)
        return subprocess.run([sys.executable, str(REPO / "bin" / "harness")] + list(args),
                              capture_output=True, text=True, env=environment)

    def contract(self):
        path = Path(self.tmp.name) / "contract.json"
        path.write_text(json.dumps({"objective": "Continue the fixture"}))
        return str(path)

    def stored(self):
        return json.loads((self.repo / ".agent-harness" / "task.json").read_text())

    def events(self):
        target = self.home / ".local" / "state" / "agent-harness" / "session-events.jsonl"
        return [json.loads(line) for line in target.read_text().splitlines()] \
            if target.exists() else []

    def test_the_library_records_the_session_only_when_given_one(self):
        tasks.save(self.repo, {"objective": "One"}, "claude-code", 0, session_id=SESSION)
        self.assertEqual(self.stored()["session_id"], SESSION)
        tasks.save(self.repo, {"objective": "Two"}, "claude-code", 1)
        self.assertNotIn("session_id", self.stored())

    def test_the_cli_records_the_runtime_session_and_notes_the_save(self):
        done = self.cli("task", "save", str(self.repo), "--input", self.contract(),
                        "--runtime", "claude-code", CLAUDE_CODE_SESSION_ID=SESSION)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.stored()["session_id"], SESSION)
        self.assertEqual([(row["event"], row["session_id"], row["runtime"])
                          for row in self.events()], [("TaskSave", SESSION, "claude-code")])

    def test_the_harness_session_variable_wins(self):
        done = self.cli("task", "save", str(self.repo), "--input", self.contract(),
                        "--runtime", "codex", HARNESS_SESSION_ID="harness-named",
                        CLAUDE_CODE_SESSION_ID=SESSION)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.stored()["session_id"], "harness-named")

    def test_without_a_session_the_field_is_absent_and_nothing_is_noted(self):
        for env in ({}, {"CLAUDE_CODE_SESSION_ID": "not a session id"}):
            with self.subTest(env=env):
                revision = tasks.read(self.repo)["revision"]
                done = self.cli("task", "save", str(self.repo), "--input", self.contract(),
                                "--runtime", "codex", "--revision", str(revision), **env)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertNotIn("session_id", self.stored())
        self.assertEqual(self.events(), [])


class Registration(unittest.TestCase):
    def test_pre_compact_is_registered_on_claude_code_only(self):
        claude = lifecycle.registration(REPO, "claude-code")["hooks"]
        codex = lifecycle.registration(REPO, "codex")["hooks"]
        self.assertIn("PreCompact", claude)
        self.assertNotIn("PreCompact", codex)
        entry = claude["PreCompact"][0]["hooks"][0]
        self.assertIn("harness:runtime-precompact", entry["command"])
        self.assertEqual(entry["timeout"], 10)

    def answer(self, runtime, invoke=None):
        calls = []

        def recording(name, event):
            calls.append(name)
            if invoke:
                return invoke(name, event)
            return {"decision": "block", "reason": "never passed on"}

        with mock.patch.object(lifecycle, "invoke", side_effect=recording), \
                mock.patch.object(lifecycle, "note_adherence") as noted:
            out = lifecycle.dispatch(runtime, {"hook_event_name": "PreCompact",
                                               "session_id": SESSION, "trigger": "auto",
                                               "cwd": str(REPO)})
        return out, calls, noted.call_count

    def test_claude_code_runs_the_snapshot_and_never_answers(self):
        self.assertEqual(self.answer("claude-code"), ({}, ["pre-compact"], 1))

    def test_a_failing_snapshot_still_answers_nothing(self):
        def boom(name, event):
            raise RuntimeError("broken")
        self.assertEqual(self.answer("claude-code", boom), ({}, ["pre-compact"], 1))

    def test_the_real_dispatch_writes_the_snapshot(self):
        # No mock between the lifecycle and the hook: the event as Claude Code sends it.
        fixture = Repository("setUp")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.write(prompt("dispatched"))
        with mock.patch.dict(os.environ, {"HOME": str(fixture.home)}):
            out = lifecycle.dispatch("claude-code", fixture.payload())
        self.assertEqual(out, {})
        kept = fixture.snapshots()
        self.assertEqual(len(kept), 1)
        text = (fixture.repo / ".agent-harness" / "handoffs" / kept[0]).read_text()
        self.assertIn("1. dispatched", text)

    def test_codex_never_runs_it(self):
        self.assertEqual(self.answer("codex"), ({}, [], 0))


if __name__ == "__main__":
    unittest.main()

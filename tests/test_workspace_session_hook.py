# SPDX-License-Identifier: MIT
"""The workspace SessionStart hook: its own entry, which members it supplies, and how.

The hook runs through the real adapter as a subprocess, under a temporary HOME whose config
points `workspaces_dir` at a synthetic workspace. A member counts as loaded natively only when a
real parent process carries it as `--add-dir`, so the tests spawn one and pass its pid as
`CLAUDE_PID`. Run: python3 -m unittest tests.test_workspace_session_hook
"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from isolation import without_harness_vars

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))
from harness_core import lifecycle  # noqa: E402

ADAPTERS = {runtime: REPO / "adapters" / runtime / "hook.py" for runtime in ("claude-code", "codex")}
NATIVE_VAR = "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"
MARKER = "# harness:runtime-sessionstart-workspace"
# Variables the developer's own session sets, which would otherwise decide the tests' outcome.
INHERITED = ("CLAUDE_PID", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_PROJECT_DIR", NATIVE_VAR)


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(os.path.realpath(self.tmp.name))
        self.home = base / "home"
        self.ws = base / "ws"
        for name in ("root", "member-a", "member-b"):
            (self.ws / name).mkdir(parents=True)
        (self.ws / "member-a" / "CLAUDE.md").write_text("Codeword ALPHA.\n")
        (self.ws / "member-b" / "AGENTS.md").write_text("Codeword BRAVO.\n")
        self.workspace("demo", ["root", "member-a", "member-b"])
        self.configure(str(self.ws))

    def workspace(self, name, folders):
        entries = ",\n".join('    {"path": "%s"}' % folder for folder in folders)
        (self.ws / (name + ".code-workspace")).write_text(
            '{\n  // members, in order\n  "folders": [\n' + entries + ',\n  ],\n}\n')

    def configure(self, directory):
        path = self.home / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"workspaces_dir": directory} if directory else {}))

    def run_hook(self, cwd=None, runtime="claude-code", argument="workspace", **extra):
        env = without_harness_vars()
        for key in INHERITED:
            env.pop(key, None)
        env["HOME"] = str(self.home)
        env.update(extra)
        cwd = str(cwd or self.ws / "root")
        payload = json.dumps({"hook_event_name": "SessionStart", "source": "startup", "cwd": cwd,
                              "session_id": "s"})
        argv = [sys.executable, str(ADAPTERS[runtime])] + ([argument] if argument else [])
        return subprocess.run(argv, input=payload, cwd=cwd, env=env, capture_output=True,
                              text=True, timeout=30)

    def context(self, out):
        self.assertEqual(out.returncode, 0, msg=out.stderr)
        if not out.stdout.strip():
            return ""
        data = json.loads(out.stdout)
        return data.get("hookSpecificOutput", {}).get("additionalContext", "")

    def parent(self, *add_dirs, tail=()):
        """A live process whose arguments carry `--add-dir`, standing in for the runtime."""
        argv = [sys.executable, "-c", "import time; time.sleep(30)"]
        for path in add_dirs:
            argv += ["--add-dir", str(path)]
        argv += list(tail)
        proc = subprocess.Popen(argv)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        return str(proc.pid)

    def line(self, text, folder):
        found = [row for row in text.splitlines() if row.startswith("- " + str(self.ws / folder) + ":")]
        self.assertEqual(len(found), 1, msg=text)
        return found[0]


class Delivery(Fixture):
    def test_unset_is_silent(self):
        self.configure(None)
        self.assertEqual(self.context(self.run_hook()), "")

    def test_a_folder_in_no_workspace_is_silent(self):
        elsewhere = self.home / "elsewhere"
        elsewhere.mkdir(parents=True)
        self.assertEqual(self.context(self.run_hook(cwd=elsewhere)), "")

    def test_a_single_workspace_supplies_every_other_member_inline(self):
        text = self.context(self.run_hook())
        self.assertIn("Workspace demo (decided by: single)", text)
        self.assertIn("supplied by this hook", self.line(text, "member-a"))
        self.assertIn("supplied by this hook", self.line(text, "member-b"))
        self.assertIn("Codeword ALPHA.", text)
        self.assertIn("Codeword BRAVO.", text)
        self.assertNotIn("- " + str(self.ws / "root") + ":", text)
        self.assertNotIn("request_directory", text)

    def test_an_ambiguous_folder_names_its_candidates_and_the_overrides_file(self):
        # The session's folder is first in none of the three, so no rule decides.
        self.workspace("demo", ["member-a", "root", "member-b"])
        self.workspace("other", ["member-b", "root"])
        self.workspace("third", ["member-a", "root"])
        text = self.context(self.run_hook())
        self.assertIn("demo, other, third", text)
        self.assertIn(str(self.ws / "overrides.json"), text)
        self.assertNotIn("Codeword", text)

    def test_a_missing_member_is_listed_and_skipped(self):
        self.workspace("demo", ["root", "member-a", "gone"])
        text = self.context(self.run_hook())
        self.assertIn(str(self.ws / "gone") + ": missing", text)

    def test_the_block_names_where_the_path_scoped_rules_are(self):
        rules = self.ws / "member-a" / ".claude" / "rules"
        rules.mkdir(parents=True)
        (rules / "scoped.md").write_text("---\npaths:\n  - src/**\n---\nScoped SECRETWORD.\n")
        text = self.context(self.run_hook())
        self.assertIn(str(rules / "scoped.md"), text)
        self.assertNotIn("SECRETWORD", text)

    def test_past_the_inline_limit_the_instructions_go_to_a_bundle_file(self):
        (self.ws / "member-a" / "CLAUDE.md").write_text("filler line\n" * 900 + "Codeword ALPHA.\n")
        text = self.context(self.run_hook())
        bundles = list(self.bundles().glob("*.md"))
        self.assertEqual(len(bundles), 1)
        bundle = bundles[0]
        digest = hashlib.sha256(bundle.read_bytes()).hexdigest()[:12]
        self.assertEqual(bundle.name, "demo-" + digest + ".md")
        self.assertLess(len(text), 9000)
        self.assertIn(str(bundle), text)
        self.assertIn("before you answer or take any other action", text)
        self.assertNotIn("Codeword", text)
        self.assertIn("Codeword ALPHA.", bundle.read_text())
        self.assertIn("Codeword BRAVO.", bundle.read_text())
        self.assertEqual([p.name for p in bundle.parent.iterdir()], [bundle.name])

    def bundles(self, home=None):
        return (home or self.home) / ".local" / "state" / "agent-harness" / "workspaces"

    def big(self):
        (self.ws / "member-a" / "CLAUDE.md").write_text("filler line\n" * 900 + "Codeword ALPHA.\n")

    def test_a_session_supplied_different_members_gets_its_own_bundle(self):
        self.big()
        self.context(self.run_hook())
        self.context(self.run_hook(CLAUDE_CODE_ENTRYPOINT="claude-desktop"))
        self.context(self.run_hook(cwd=self.ws / "member-b"))
        self.assertEqual(len(list(self.bundles().glob("demo-*.md"))), 2)

    def test_the_bundle_follows_harness_home_like_the_rest_of_the_state(self):
        self.big()
        other = self.home.parent / "other-home"
        other.mkdir()
        text = self.context(self.run_hook(HOME=str(other), HARNESS_HOME=str(self.home)))
        self.assertIn(str(self.bundles()), text)
        self.assertFalse(self.bundles(other).exists())

    def test_bundles_older_than_seven_days_are_pruned_on_write(self):
        self.big()
        folder = self.bundles()
        folder.mkdir(parents=True)
        old, fresh = folder / "gone-000000000000.md", folder / "kept-000000000000.md"
        old.write_text("old")
        fresh.write_text("fresh")
        stale = time.time() - 8 * 86400
        os.utime(str(old), (stale, stale))
        self.context(self.run_hook())
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_a_bundle_that_cannot_be_written_lists_each_instruction_file(self):
        self.big()
        self.bundles().parent.mkdir(parents=True)
        self.bundles().write_text("a file where the folder should be")
        text = self.context(self.run_hook())
        self.assertIn("Workspace demo", text)
        self.assertIn("- " + str(self.ws / "member-a" / "CLAUDE.md"), text)
        self.assertIn("- " + str(self.ws / "member-b" / "AGENTS.md"), text)
        self.assertNotIn("Codeword", text)

    def test_a_dot_claude_claude_md_is_read_like_claude_md(self):
        (self.ws / "member-a" / "CLAUDE.md").unlink()
        (self.ws / "member-a" / ".claude").mkdir()
        (self.ws / "member-a" / ".claude" / "CLAUDE.md").write_text("Codeword DELTA.\n")
        self.assertIn("Codeword DELTA.", self.context(self.run_hook()))
        pid = self.parent(self.ws / "member-a")
        text = self.context(self.run_hook(CLAUDE_PID=pid, **{NATIVE_VAR: "1"}))
        self.assertIn("loaded natively", self.line(text, "member-a"))

    def test_the_desktop_app_is_told_to_request_a_folder_on_first_use(self):
        text = self.context(self.run_hook(CLAUDE_CODE_ENTRYPOINT="claude-desktop"))
        self.assertIn("request_directory", text)
        self.assertIn("do not request them all now", text)

    def test_a_broken_workspace_file_never_fails_the_session(self):
        (self.ws / "demo.code-workspace").write_text("{ not json")
        out = self.run_hook()
        self.assertEqual(out.returncode, 0, msg=out.stderr)
        self.assertEqual(self.context(out), "")

    def test_a_malformed_config_never_fails_the_session(self):
        (self.home / ".config" / "agent-harness" / "config.json").write_text("[1, 2")
        self.assertEqual(self.context(self.run_hook()), "")


class NativeSkip(Fixture):
    def test_a_member_the_parent_loads_natively_is_not_supplied_again(self):
        pid = self.parent(self.ws / "member-a", self.ws / "member-b")
        text = self.context(self.run_hook(CLAUDE_PID=pid, **{NATIVE_VAR: "1"}))
        self.assertIn("loaded natively", self.line(text, "member-a"))
        self.assertNotIn("Codeword ALPHA.", text)

    def test_an_agents_only_member_is_supplied_even_when_added(self):
        pid = self.parent(self.ws / "member-a", self.ws / "member-b")
        text = self.context(self.run_hook(CLAUDE_PID=pid, **{NATIVE_VAR: "1"}))
        self.assertIn("supplied by this hook", self.line(text, "member-b"))
        self.assertIn("Codeword BRAVO.", text)

    def test_without_the_variable_an_added_member_is_still_supplied(self):
        pid = self.parent(self.ws / "member-a", self.ws / "member-b")
        text = self.context(self.run_hook(CLAUDE_PID=pid))
        self.assertIn("supplied by this hook", self.line(text, "member-a"))
        self.assertIn("Codeword ALPHA.", text)

    def test_a_member_not_among_the_parent_arguments_is_supplied(self):
        pid = self.parent(self.ws / "member-b")
        text = self.context(self.run_hook(CLAUDE_PID=pid, **{NATIVE_VAR: "1"}))
        self.assertIn("supplied by this hook", self.line(text, "member-a"))

    def test_a_member_named_only_in_the_prompt_is_not_native(self):
        pid = self.parent("../member-b", tail=["-p", "fix " + str(self.ws / "member-a") + " now"])
        text = self.context(self.run_hook(CLAUDE_PID=pid, **{NATIVE_VAR: "1"}))
        self.assertIn("supplied by this hook", self.line(text, "member-a"))
        self.assertIn("Codeword ALPHA.", text)

    def test_a_relative_add_dir_resolves_against_the_session_folder(self):
        pid = self.parent("../member-a")
        text = self.context(self.run_hook(CLAUDE_PID=pid, **{NATIVE_VAR: "1"}))
        self.assertIn("loaded natively", self.line(text, "member-a"))

    def test_codex_is_supplied_every_member_whatever_its_parent_carries(self):
        pid = self.parent(self.ws / "member-a", self.ws / "member-b")
        text = self.context(self.run_hook(runtime="codex", CLAUDE_PID=pid, **{NATIVE_VAR: "1"}))
        self.assertIn("supplied by this hook", self.line(text, "member-a"))
        self.assertIn("Codeword ALPHA.", text)
        self.assertIn("Codeword BRAVO.", text)


class Parsing(unittest.TestCase):
    def setUp(self):
        self.hook = module("harness_workspace_session_test", REPO / "policy" / "hooks" / "workspace-session.py")

    def test_add_dir_takes_several_values_and_the_equals_form(self):
        base = os.path.realpath(tempfile.gettempdir())
        argv = ["-p", "prompt", "--add-dir", "/a", "/b", "--model", "haiku", "--add-dir=/c", "x"]
        self.assertEqual(self.hook.add_dirs(argv, base),
                         [os.path.realpath(p) for p in ("/a", "/b", "/c")])

    def test_a_value_after_another_flag_is_not_an_added_folder(self):
        argv = ["--add-dir", "/a", "-p", "/b", "--add-dir=", "/c"]
        self.assertEqual(self.hook.add_dirs(argv, "/"), [os.path.realpath("/a")])

    def test_the_exact_argument_vector_keeps_a_path_with_a_space_whole(self):
        if not (sys.platform == "darwin" or os.path.isdir("/proc")):
            self.skipTest("no exact argument source on this platform")
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)",
                                 "--add-dir", "/x/my folder"])
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        deadline = time.time() + 5
        argv = []
        while time.time() < deadline and "--add-dir" not in argv:
            argv = self.hook.parent_command(proc.pid)
            time.sleep(0.05)
        self.assertEqual(argv[-2:], ["--add-dir", "/x/my folder"])

    def test_the_supplied_text_stops_at_the_total_limit_and_names_the_rest(self):
        with tempfile.TemporaryDirectory() as temp:
            for name in ("a", "b"):
                os.mkdir(os.path.join(temp, name))
                with open(os.path.join(temp, name, "CLAUDE.md"), "w") as handle:
                    handle.write(name * 100)
            ws = module("harness_workspaces_limit_test", REPO / "lib" / "harness_core" / "workspaces.py")
            saved = self.hook.TOTAL_LIMIT
            self.hook.TOTAL_LIMIT = 150
            try:
                text, paths = self.hook.instructions(ws, [os.path.join(temp, "a"), os.path.join(temp, "b")])
            finally:
                self.hook.TOTAL_LIMIT = saved
        self.assertIn("a" * 100, text)
        self.assertNotIn("b" * 100, text)
        self.assertIn("- " + os.path.join(temp, "b", "CLAUDE.md"), text)
        self.assertEqual(len(paths), 2)

    def test_an_unreadable_parent_has_no_arguments(self):
        self.assertEqual(self.hook.parent_command("not-a-pid"), [])
        self.assertEqual(self.hook.parent_command(None), [])


class Registration(unittest.TestCase):
    def test_both_runtimes_register_a_second_session_start_entry(self):
        for runtime in ADAPTERS:
            with self.subTest(runtime=runtime):
                entries = lifecycle.registration(REPO, runtime)["hooks"]["SessionStart"]
                self.assertEqual(len(entries), 2)
                command = entries[1]["hooks"][0]["command"]
                self.assertTrue(command.endswith(" workspace " + MARKER), msg=command)
                self.assertIn(str(ADAPTERS[runtime]), command)
                self.assertEqual(entries[1]["hooks"][0]["timeout"], 10)

    def test_the_workspace_entry_on_another_event_prints_an_empty_answer(self):
        with tempfile.TemporaryDirectory() as temp:
            env = without_harness_vars()
            env["HOME"] = temp
            out = subprocess.run([sys.executable, str(ADAPTERS["claude-code"]), "workspace"],
                                 input=json.dumps({"hook_event_name": "Stop", "cwd": temp}),
                                 env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual((out.returncode, out.stdout.strip()), (0, "{}"))


class PlainEntry(Fixture):
    def test_the_plain_session_start_entry_never_carries_the_workspace_block(self):
        control = self.run_hook()
        self.assertIn("Workspace demo", self.context(control))
        out = self.run_hook(argument=None)
        self.assertEqual(out.returncode, 0, msg=out.stderr)
        self.assertNotIn("Workspace demo", out.stdout)
        self.assertNotIn("Codeword", out.stdout)


class Deadline(Fixture):
    def test_past_the_deadline_no_bundle_is_written_and_the_files_are_listed(self):
        (self.ws / "member-a" / "CLAUDE.md").write_text("filler line\n" * 900 + "Codeword ALPHA.\n")
        hook = module("harness_workspace_session_deadline", REPO / "policy" / "hooks" / "workspace-session.py")
        hook.over_budget = lambda: True
        env = {"HOME": str(self.home), "HARNESS_RUNTIME": "claude-code"}
        text = hook.context({"cwd": str(self.ws / "root")}, env)
        self.assertIn("- " + str(self.ws / "member-a" / "CLAUDE.md"), text)
        self.assertFalse((self.home / ".local" / "state" / "agent-harness" / "workspaces").exists())


class Workers(unittest.TestCase):
    """A role worker must not run the user's hooks, so the workspace entry never reaches one."""

    def test_the_claude_worker_reads_no_user_settings(self):
        worker = module("harness_claude_worker_test", REPO / "adapters" / "claude-code" / "worker.py")
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            command = worker.prepare("claude", work, work, work, [], "brief", {"model": "m"},
                                     {"HOME": temp}, {})
        at = command.index("--setting-sources")
        self.assertEqual(command[at + 1], "")

    def test_the_codex_worker_gets_a_fresh_home_with_no_hooks_file(self):
        worker = module("harness_codex_worker_test", REPO / "adapters" / "codex" / "worker.py")
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp) / "work"
            work.mkdir()
            env = {}
            worker.prepare("codex", work, work, work, [], "brief", {"model": "m"},
                           {"HOME": temp}, env)
            home = Path(env["CODEX_HOME"])
            self.assertEqual(home, work / "codex")
            self.assertFalse((home / "hooks.json").exists())


if __name__ == "__main__":
    unittest.main()

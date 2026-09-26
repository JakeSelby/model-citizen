# SPDX-License-Identifier: MIT
"""Unit tests for the write-intent ledger and the overlap check in front of Edit and Write.

Every test runs under a temporary HOME with its own git repository and sibling worktrees, so no
test reads or writes the real config or state directories.

Run: python3 -m unittest discover tests
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from isolation import drop_inherited_config_dir, isolate_home

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from harness_core import intents, lifecycle  # noqa: E402


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


harness = _load("harness_for_intents", REPO / "bin" / "harness")
drop_inherited_config_dir()


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.name=test", "-c", "user.email=test"]
                   + list(args), check=True, capture_output=True, text=True)


def dead_pid():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


class Base(unittest.TestCase):
    def setUp(self):
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        isolate_home(self.home)
        for name in ("CLAUDE_PID", "CLAUDE_CODE_SESSION_ID", "HARNESS_SESSION_ID"):
            os.environ.pop(name, None)
        # The editing session's own runtime: live, and never the pid a sibling claims with.
        os.environ["CLAUDE_PID"] = str(os.getppid())
        self.env = os.environ
        self.main = self.root / "repo"
        self.main.mkdir()
        git(self.main, "init", "-q", "-b", "main")
        (self.main / "shared.py").write_text("x = 1\n")
        git(self.main, "add", "shared.py")
        git(self.main, "commit", "-q", "-m", "init")
        self.a = self.root / "a"
        self.b = self.root / "b"
        git(self.main, "worktree", "add", "-q", "-b", "a", str(self.a))
        git(self.main, "worktree", "add", "-q", "-b", "b", str(self.b))
        self.log = self.home / ".local" / "state" / "agent-harness" / "decisions.jsonl"

    def claim_a(self, *paths, pid=None):
        return intents.claim(list(paths), cwd=str(self.a), session="A",
                             pid=os.getpid() if pid is None else pid, env=self.env)

    def rows(self, point=intents.POINT):
        if not self.log.exists():
            return []
        rows = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [r for r in rows if r.get("point") == point]

    def config(self, text):
        path = self.home / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


class ClaimTests(Base):
    def test_claim_writes_the_session_file_with_every_field(self):
        data = self.claim_a("shared.py", "docs/planned.md", "lib/*.py")
        path = self.home / ".local" / "state" / "agent-harness" / "intents" / intents.slot("A", str(self.a))
        self.assertEqual(json.loads(path.read_text()), data)
        self.assertEqual(data["paths"], ["docs/planned.md", "lib/*.py", "shared.py"])
        self.assertEqual(data["branch"], "a")
        self.assertEqual(data["worktree"], str(self.a))
        self.assertEqual(data["repo"], str((self.main / ".git").resolve()))
        self.assertEqual(data["pid"], os.getpid())
        self.assertTrue(data["started_at"].endswith("Z"))

    def test_a_reclaim_adds_to_the_paths_already_held(self):
        self.claim_a("shared.py")
        data = self.claim_a(str(self.a / "new" / "file.py"))
        self.assertEqual(data["paths"], ["new/file.py", "shared.py"])

    def test_a_path_outside_the_worktree_or_outside_git_is_refused(self):
        with self.assertRaises(ValueError):
            self.claim_a(str(self.b / "shared.py"))
        with self.assertRaises(ValueError):
            intents.claim(["x"], cwd=str(self.home), session="A", pid=os.getpid(), env=self.env)

    def test_release_removes_the_claim(self):
        self.claim_a("shared.py")
        self.assertTrue(intents.release("A", str(self.a), self.env))
        self.assertEqual(intents.claims(self.env), [])
        self.assertFalse(intents.release("A", str(self.a), self.env))

    def test_a_dead_pid_is_ignored_and_swept(self):
        self.claim_a("shared.py", pid=dead_pid())
        path = intents.claim_path("A", str(self.a), self.env)
        self.assertEqual(intents.sweep(self.env), [path.name])
        self.assertFalse(path.exists())
        self.claim_a("shared.py", pid=dead_pid())
        self.assertEqual(intents.overlaps(self.b / "shared.py", "B", None, env=self.env), [])
        self.assertFalse(path.exists())

    def test_a_removed_worktree_ends_the_claim(self):
        self.claim_a("shared.py")
        git(self.main, "worktree", "remove", str(self.a))
        self.assertEqual(intents.overlaps(self.b / "shared.py", "B", None, env=self.env), [])
        self.assertFalse(intents.claim_path("A", str(self.a), self.env).exists())

    def test_the_runtime_pid_is_the_runtime_not_the_shell(self):
        self.assertEqual(intents.runtime_pid({"CLAUDE_PID": "4242"}), 4242)
        walked = intents.runtime_pid({})
        self.assertGreater(walked, 1)
        self.assertNotEqual(walked, os.getpid())


class MatchTests(unittest.TestCase):
    def test_exact_directory_and_glob(self):
        self.assertTrue(intents.matches("lib/x.py", "lib/x.py"))
        self.assertFalse(intents.matches("lib/x.py", "lib/x.pyc"))
        self.assertTrue(intents.matches("lib", "lib/x.py"))
        self.assertTrue(intents.matches("lib/", "lib/deep/x.py"))
        self.assertFalse(intents.matches("lib/", "library.py"))
        self.assertTrue(intents.matches("tests/test_*.py", "tests/test_x.py"))
        self.assertTrue(intents.matches("src/**/x.py", "src/x.py"))
        self.assertTrue(intents.matches("src/**", "src/a/b.py"))
        self.assertFalse(intents.matches("tests/test_*.py", "lib/test_x.txt"))


class OverlapTests(Base):
    def test_a_sibling_worktree_overlaps_on_exact_and_glob(self):
        self.claim_a("shared.py", "lib/*.py")
        exact = intents.overlaps(self.b / "shared.py", "B", None, env=self.env)
        self.assertEqual([(c["session"], p, r) for c, p, r in exact], [("A", "shared.py", "shared.py")])
        glob = intents.overlaps(self.b / "lib" / "new.py", "B", None, env=self.env)
        self.assertEqual([p for _, p, _ in glob], ["lib/*.py"])
        self.assertEqual(intents.overlaps(self.b / "other.py", "B", None, env=self.env), [])

    def test_own_claims_never_match(self):
        self.claim_a("shared.py")
        self.assertEqual(intents.overlaps(self.a / "shared.py", "A", None, env=self.env), [])
        # One process, its own worktree: a subagent's edit under a different session id.
        self.assertEqual(intents.overlaps(self.a / "shared.py", "X", os.getpid(), env=self.env), [])
        # The same process in a sibling's worktree is a sibling.
        self.assertEqual(len(intents.overlaps(self.b / "shared.py", "X", os.getpid(), env=self.env)), 1)

    def test_a_claim_from_another_repository_never_matches(self):
        other = self.root / "other"
        other.mkdir()
        git(other, "init", "-q")
        intents.claim(["shared.py"], cwd=str(other), session="O", pid=os.getpid(), env=self.env)
        self.assertEqual(intents.overlaps(self.b / "shared.py", "B", None, env=self.env), [])


class SharedSessionTests(Base):
    """Sibling builders spawned by one session carry its session id and its pid."""

    def claim(self, worktree, *paths):
        return intents.claim(list(paths), cwd=str(worktree), session="S", pid=os.getpid(),
                             env=self.env)

    def edit(self, worktree, name="shared.py", cwd=None):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "session_id": "S",
                   "cwd": str(cwd or worktree),
                   "tool_input": {"file_path": str(worktree / name),
                                  "old_string": "x", "new_string": "y"}}
        return lifecycle.dispatch("claude-code", payload)

    def test_an_absolute_path_into_a_sibling_worktree_is_the_siblings_claim(self):
        os.environ["CLAUDE_PID"] = str(os.getpid())
        self.claim(self.b, "shared.py")
        found = intents.overlaps(self.b / "shared.py", "S", os.getpid(), str(self.a), self.env)
        self.assertEqual([(c["worktree"], r) for c, _, r in found], [(str(self.b), "shared.py")])
        self.assertEqual(intents.overlaps(self.b / "shared.py", "S", os.getpid(), str(self.b),
                                          self.env), [])
        first = self.edit(self.b, cwd=self.a)
        self.assertIn("intent-overlap warning", first["hookSpecificOutput"]["additionalContext"])
        # The overlap counts under the editor's worktree, never under the sibling's.
        hits = intents.hits_dir(self.env)
        self.assertTrue((hits / intents.slot("S", str(self.a))).exists())
        self.assertFalse((hits / intents.slot("S", str(self.b))).exists())
        self.assertEqual(self.edit(self.b, cwd=self.a)["hookSpecificOutput"]["permissionDecision"],
                         "deny")

    def test_two_worktrees_under_one_session_keep_both_claims(self):
        self.claim(self.a, "shared.py")
        self.claim(self.b, "other.py")
        held = sorted((c["worktree"], tuple(c["paths"])) for c in intents.claims(self.env))
        self.assertEqual(held, [(str(self.a), ("shared.py",)), (str(self.b), ("other.py",))])
        self.assertTrue(intents.release("S", str(self.b), self.env))
        self.assertEqual([c["worktree"] for c in intents.claims(self.env)], [str(self.a)])

    def test_a_sibling_under_the_same_session_is_warned_then_denied(self):
        os.environ["CLAUDE_PID"] = str(os.getpid())
        self.claim(self.a, "shared.py")
        self.claim(self.b, "other.py")
        self.assertEqual(self.edit(self.a), {})
        first = self.edit(self.b)
        self.assertIn("intent-overlap warning", first["hookSpecificOutput"]["additionalContext"])
        second = self.edit(self.b)
        self.assertEqual(second["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual([r["deterministic_answer"] for r in self.rows()], ["warn", "deny"])

    def test_hit_counters_are_separate_per_worktree(self):
        self.assertEqual(intents.hit("S", str(self.a), "shared.py", self.env), 1)
        self.assertEqual(intents.hit("S", str(self.b), "shared.py", self.env), 1)
        self.assertEqual(intents.hit("S", str(self.b), "shared.py", self.env), 2)
        self.assertEqual(intents.hit("S", str(self.a), "shared.py", self.env), 2)


class ConcurrentHitTests(Base):
    """Two edits at once from one session and worktree, such as parallel tool calls."""

    def test_two_concurrent_hits_on_one_claim_warn_once_and_deny_once(self):
        read = intents._read

        def slow_read(path):
            # Widen the read-modify-write window so an unlocked pair would both read zero.
            data = read(path)
            time.sleep(0.3)
            return data

        start = threading.Barrier(2)
        counts = []

        def edit():
            start.wait()
            counts.append(intents.hit("S", str(self.a), "shared.py", self.env))

        intents._read = slow_read
        self.addCleanup(setattr, intents, "_read", read)
        workers = [threading.Thread(target=edit) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
        self.assertEqual(sorted(counts), [1, 2])
        self.assertEqual(sorted(intents.answer_for(c, "deny") for c in counts), ["deny", "warn"])
        stored = json.loads((intents.hits_dir(self.env) / intents.slot("S", str(self.a)))
                            .read_text())
        self.assertEqual(stored, {"shared.py": 2})

    # Hooks run one process per event, and dispatch captures a hook's output by redirecting the
    # process-wide stdout, so the hook path is raced from two processes, never two threads.
    EDIT_PROCESS = """
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from harness_core import intents, lifecycle
hits, read_text = intents.hits_dir(), Path.read_text
def slow_read_text(path, *args, **kwargs):
    # The hook loads its own copy of the ledger, so widen the window at the file read.
    text = read_text(path, *args, **kwargs)
    if Path(path).parent == hits:
        time.sleep(0.3)
    return text
Path.read_text = slow_read_text
payload = json.loads(sys.argv[2])
time.sleep(max(0.0, float(sys.argv[3]) - time.time()))
print(json.dumps(lifecycle.dispatch("claude-code", payload)))
"""

    def test_two_concurrent_edits_through_the_hook_warn_once_and_deny_once(self):
        os.environ["CLAUDE_PID"] = str(os.getpid())
        intents.claim(["shared.py"], cwd=str(self.b), session="B", pid=os.getpid(), env=self.env)
        payload = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Edit",
                              "session_id": "A", "cwd": str(self.a),
                              "tool_input": {"file_path": str(self.a / "shared.py"),
                                             "old_string": "x", "new_string": "y"}})
        start = str(time.time() + 1.5)
        children = [subprocess.Popen([sys.executable, "-c", self.EDIT_PROCESS, str(REPO / "lib"),
                                      payload, start], stdout=subprocess.PIPE, text=True)
                    for _ in range(2)]
        results = [json.loads(child.communicate(timeout=60)[0].strip().splitlines()[-1])
                   for child in children]
        denied = [r for r in results
                  if r.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"]
        warned = [r for r in results if "intent-overlap warning"
                  in r.get("hookSpecificOutput", {}).get("additionalContext", "")]
        self.assertEqual((len(denied), len(warned)), (1, 1))
        self.assertEqual(sorted(r["deterministic_answer"] for r in self.rows()),
                         ["deny", "warn"])

    def test_a_held_lock_falls_back_to_an_unlocked_count_within_the_budget(self):
        import fcntl
        hits = intents.hits_dir(self.env)
        hits.mkdir(parents=True, exist_ok=True)
        with open(str(hits / ".lock"), "a") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX)
            began = time.monotonic()
            count = intents.hit("S", str(self.a), "shared.py", self.env)
            elapsed = time.monotonic() - began
        self.assertEqual(count, 1)
        self.assertGreaterEqual(elapsed, intents.HITS_LOCK_BUDGET)
        self.assertLess(elapsed, intents.HITS_LOCK_BUDGET + 2)
        self.assertEqual(intents.hit("S", str(self.a), "shared.py", self.env), 2)


class HookTests(Base):
    def edit(self, name="shared.py", session="B", tool="Edit"):
        payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "session_id": session,
                   "cwd": str(self.b), "tool_input": {"file_path": str(self.b / name),
                                                      "old_string": "x", "new_string": "y"}}
        return lifecycle.dispatch("claude-code", payload)

    def test_warned_once_then_denied_and_both_rows_logged(self):
        self.claim_a("shared.py")
        first = self.edit()
        self.assertNotIn("permissionDecision", first["hookSpecificOutput"])
        self.assertIn("intent-overlap warning", first["hookSpecificOutput"]["additionalContext"])
        self.assertIn("intent-overlap", first["systemMessage"])
        second = self.edit()
        self.assertEqual(second["hookSpecificOutput"]["permissionDecision"], "deny")
        rows = self.rows()
        self.assertEqual([r["deterministic_answer"] for r in rows], ["warn", "deny"])
        self.assertEqual({r["variant"] for r in rows}, {"deny"})
        self.assertEqual({r["session_id"] for r in rows}, {"B"})
        self.assertEqual(rows[0]["claimed_by"], "A")

    def test_non_overlapping_paths_write_no_row(self):
        self.claim_a("lib/*.py")
        self.assertEqual(self.edit("other.py", tool="Write"), {})
        self.assertEqual(self.rows(), [])

    def test_the_warn_variant_never_denies(self):
        self.config(json.dumps({"coordination": {"repeat_overlap": "warn"}}))
        self.claim_a("shared.py")
        for _ in range(3):
            self.assertNotIn("permissionDecision", self.edit()["hookSpecificOutput"])
        self.assertEqual([r["variant"] for r in self.rows()], ["warn"] * 3)

    def test_the_hook_id_switched_off_answers_nothing(self):
        self.config(json.dumps({"hooks": {"intent-overlap": "off"}}))
        self.claim_a("shared.py")
        self.assertEqual(self.edit(), {})
        self.assertEqual(self.rows(), [])

    def test_a_setting_that_cannot_be_honoured_only_warns(self):
        for text in ("{not json", json.dumps({"coordination": {"repeat_overlap": "block"}}),
                     json.dumps({"coordination": "deny"})):
            self.config(text)
            self.assertEqual(intents.overlap_variant(self.env), "warn", msg=text)
        (self.home / ".config" / "agent-harness" / "config.json").unlink()
        self.assertEqual(intents.overlap_variant(self.env), "deny")

    def test_a_multiedit_and_a_notebook_edit_are_checked_too(self):
        self.claim_a("shared.py", "nb.ipynb")
        self.assertIn("additionalContext", self.edit(tool="MultiEdit")["hookSpecificOutput"])
        payload = {"hook_event_name": "PreToolUse", "tool_name": "NotebookEdit", "session_id": "B",
                   "cwd": str(self.b), "tool_input": {"notebook_path": str(self.b / "nb.ipynb")}}
        self.assertIn("additionalContext",
                      lifecycle.dispatch("claude-code", payload)["hookSpecificOutput"])


class CommandTests(Base):
    def run_cli(self, *argv, cwd=None):
        out = io.StringIO()
        previous = os.getcwd()
        os.environ.pop("HARNESS_QUIET", None)
        os.chdir(str(cwd or self.b))
        try:
            with contextlib.redirect_stdout(out):
                code = harness.main(list(argv))
        finally:
            os.chdir(previous)
        return code, out.getvalue()

    def test_check_before_commit_exits_on_an_overlap(self):
        self.claim_a("shared.py")
        (self.b / "shared.py").write_text("x = 2\n")
        code, text = self.run_cli("intent", "check", "--session", "B")
        self.assertEqual(code, 1, text)
        self.assertIn("deny: `shared.py`", text)
        self.assertEqual(self.run_cli("intent", "check", "other.py", "--session", "B")[0], 0)
        self.config(json.dumps({"coordination": {"repeat_overlap": "warn"}}))
        self.assertEqual(self.run_cli("intent", "check", "--session", "B")[0], 0)

    def test_claim_and_release_from_the_command_line(self):
        code, text = self.run_cli("intent", "claim", "docs/*.md", "--session", "C",
                                  "--pid", str(os.getpid()), cwd=self.a)
        self.assertEqual(code, 0, text)
        self.assertIn("docs/*.md", self.run_cli("intent", "list")[1])
        self.assertEqual(self.run_cli("intent", "release", "--session", "C", cwd=self.a)[0], 0)
        self.assertIn("no live claims", self.run_cli("intent", "list")[1])

    def test_merge_probe_and_conflict_report(self):
        (self.a / "shared.py").write_text("x = 'a'\n")
        git(self.a, "commit", "-q", "-am", "a")
        (self.b / "shared.py").write_text("x = 'b'\n")
        git(self.b, "commit", "-q", "-am", "b")
        self.assertEqual(intents.probe_merge(self.b, "a"), "conflicted")
        self.assertEqual(intents.probe_merge(self.b, "main"), "clean")
        self.assertEqual(self.run_cli("intent", "merge", "--base", "a")[0], 0)
        self.assertEqual(self.run_cli("intent", "merge", "clean")[0], 0)
        rows = self.rows(intents.MERGE_POINT)
        self.assertEqual([r["deterministic_answer"] for r in rows], ["conflicted", "clean"])
        self.assertEqual(rows[0]["branch"], "b")
        code, text = self.run_cli("usage", "--conflicts")
        self.assertEqual(code, 0)
        line = text.splitlines()[-1].split()
        self.assertEqual(line[1:4], ["2", "1", "50%"])
        code, text = self.run_cli("usage", "--by", "decision")
        self.assertIn("landing-merge", text)

    def test_conflicts_refuses_another_grouping(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.run_cli("usage", "--conflicts", "--by", "repo")[0], 2)

    def test_weeks_start_on_monday(self):
        self.assertEqual(intents.week_of("2026-09-27T10:00:00Z"), "2026-09-21")
        self.assertEqual(intents.week_of("2026-09-21T00:00:00Z"), "2026-09-21")
        self.assertIsNone(intents.week_of("garbage"))


class CodexGapTests(unittest.TestCase):
    def test_codex_declares_the_edit_check_gap(self):
        data = json.loads((REPO / "adapters" / "codex" / "capabilities.json").read_text())
        text = " ".join(data["limitations"])
        self.assertIn("intent-overlap", text)
        self.assertIn("harness intent check", text)

    def test_the_builder_role_claims_before_its_first_edit(self):
        text = (REPO / "primitives" / "roles" / "builder.md").read_text()
        self.assertIn("harness intent claim", text)
        self.assertIn("harness intent check", text)


if __name__ == "__main__":
    unittest.main()

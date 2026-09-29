"""PostToolUse nudges for deterministic multi-file delegation opportunities."""
import importlib.util
import json
import fcntl
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from harness_core import lifecycle
from isolation import without_harness_vars


def load_posture():
    spec = importlib.util.spec_from_file_location("delegation_nudge_posture",
                                                  ROOT / "policy/hooks/posture.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Recorder:
    def __init__(self):
        self.rows = []

    def record(self, *args, **kwargs):
        self.rows.append((args, kwargs))


class DelegationNudgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"HARNESS_HOME": str(self.home), "HOME": str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.recorder = Recorder()
        self.decisions = patch.object(lifecycle, "decisions", return_value=self.recorder)
        self.decisions.start()
        self.addCleanup(self.decisions.stop)
        self.selection = patch.object(lifecycle, "selected", side_effect=self.select)
        self.selection.start()
        self.addCleanup(self.selection.stop)

    @staticmethod
    def select(name, fallback):
        return {"delegation": "tiered", "plan-ceremony": "light"}.get(name, fallback)

    def event(self, path, session="s1", response=None):
        return {"hook_event_name": "PostToolUse", "tool_name": "Read",
                "session_id": session, "cwd": str(self.home),
                "tool_input": {"file_path": path},
                "tool_response": {} if response is None else response}

    def test_third_distinct_read_nudges_once_and_records_the_firing(self):
        first = lifecycle.dispatch("claude-code", self.event("one.py"))
        second = lifecycle.dispatch("claude-code", self.event("two.py"))
        third = lifecycle.dispatch("claude-code", self.event("three.py"))
        fourth = lifecycle.dispatch("claude-code", self.event("four.py"))

        self.assertEqual(first, {})
        self.assertEqual(second, {})
        context = third["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Spawn worker-a", context)
        self.assertIn("400 words", context)
        self.assertEqual(fourth, {})
        self.assertEqual(len(self.recorder.rows), 1)
        self.assertEqual(self.recorder.rows[0][0][:3],
                         ("delegation-nudge", "nudge", "3 distinct files"))

    def test_two_files_and_repeated_reads_add_no_context(self):
        for path in ("one.py", "one.py", "two.py"):
            self.assertEqual(lifecycle.dispatch("codex", self.event(path, "two-files")), {})
        self.assertEqual(self.recorder.rows, [])

    def test_failed_read_does_not_advance_the_signal(self):
        self.assertEqual(lifecycle.dispatch("claude-code", self.event(
            "missing.py", "fail", {"is_error": True})), {})
        for path in ("one.py", "two.py"):
            self.assertEqual(lifecycle.dispatch("claude-code", self.event(path, "fail")), {})
        self.assertEqual(self.recorder.rows, [])

    def test_delegation_off_never_creates_session_state(self):
        with patch.object(lifecycle, "selected", side_effect=lambda name, fallback:
                          "off" if name == "delegation" else "light"):
            for path in ("one.py", "two.py", "three.py"):
                self.assertEqual(lifecycle.dispatch("claude-code", self.event(path, "off")), {})
        self.assertFalse((self.home / ".local/state/agent-harness/sessions/off.json").exists())

    def test_disabled_spawn_hook_never_creates_nudge_state(self):
        with patch.object(lifecycle, "switches", return_value={"tier-agent-spawns": "off"}):
            for path in ("one.py", "two.py", "three.py"):
                self.assertEqual(lifecycle.dispatch("claude-code", self.event(path, "hook-off")), {})
        self.assertFalse((self.home / ".local/state/agent-harness/sessions/hook-off.json").exists())

    def test_simple_read_only_bash_operands_are_counted(self):
        cases = {
            "cat alpha.py": [str(self.home / "alpha.py")],
            "head -n 5 beta.py": [str(self.home / "beta.py")],
            "sed -n '1,5p' gamma.py": [str(self.home / "gamma.py")],
            "wc -l -- delta.py": [str(self.home / "delta.py")],
        }
        for command, expected in cases.items():
            event = lifecycle.normalize({"tool_name": "Bash", "cwd": str(self.home),
                                         "tool_input": {"command": command}})
            self.assertEqual(lifecycle.delegation_read_paths(event), expected, command)

    def test_native_read_treats_shell_metacharacters_as_literal_path_text(self):
        event = lifecycle.normalize(self.event("draft[1].py", "literal"))
        self.assertEqual(lifecycle.delegation_read_paths(event),
                         [str(self.home / "draft[1].py")])

    def test_dynamic_compound_and_mutating_bash_are_not_counted(self):
        for command in ("cat $TARGET", "cat one.py | head", "cat one.py && cat two.py",
                        "cat one.py two.py three.py", "sed -n -e '1p' one.py",
                        "cat one.py # plus two.py",
                        "python3 tool.py", "cat one.py > copy.py", "cat one.py > /dev/null",
                        "shasum -a 256 one.py"):
            event = lifecycle.normalize({"tool_name": "Bash", "cwd": str(self.home),
                                         "tool_input": {"command": command}})
            self.assertEqual(lifecycle.delegation_read_paths(event), [], command)

    def test_variant_sidecar_uses_custom_roots_and_rejects_invalid_threshold(self):
        posture = load_posture()
        custom = self.home / "custom"
        directory = custom / "stances/delegation"
        directory.mkdir(parents=True)
        sidecar = directory / "custom.json"
        sidecar.write_text(json.dumps({"schema_version": 1, "threshold": 4,
                                       "message": "Use the custom gatherer."}))
        config = {"primitive_roots": [str(custom)]}
        self.assertEqual(posture.delegation_nudge("custom", config, root=self.home / "builtin"),
                         {"threshold": 4, "message": "Use the custom gatherer.",
                          "source": str(sidecar)})
        sidecar.write_text(json.dumps({"schema_version": 1, "threshold": 2,
                                       "message": "Too soon."}))
        self.assertIsNone(posture.delegation_nudge("custom", config, root=self.home / "builtin"))

    def test_session_update_preserves_existing_registry_fields(self):
        posture = load_posture()
        self.assertTrue(posture.write_session_record("registry", {"agents": ["worker-a"]}))
        self.assertEqual(posture.delegation_read("registry", ["/one"], 3), (False, 1))
        record = posture.read_session_record("registry")
        self.assertEqual(record["agents"], ["worker-a"])
        self.assertEqual(record[posture.DELEGATION_READS_KEY], ["/one"])

    def test_reads_after_firing_or_already_counted_do_not_rewrite_the_record(self):
        posture = load_posture()
        self.assertEqual(posture.delegation_read("settled", ["/one", "/two"], 3), (False, 2))
        writes = []
        original = posture._write_session_record

        def counting(path, record):
            writes.append(path)
            return original(path, record)

        with patch.object(posture, "_write_session_record", counting):
            self.assertEqual(posture.delegation_read("settled", ["/two"], 3), (False, 2))
            self.assertEqual(writes, [])
            self.assertEqual(posture.delegation_read("settled", ["/three"], 3), (True, 3))
            self.assertEqual(len(writes), 1)
            self.assertEqual(posture.delegation_read("settled", ["/four"], 3), (False, 3))
            self.assertEqual(len(writes), 1)
        self.assertTrue(posture.read_session_record("settled")[posture.DELEGATION_FIRED_KEY])

    def test_concurrent_threshold_crossing_fires_at_most_once(self):
        posture = load_posture()
        self.assertEqual(posture.delegation_read("concurrent", ["/one", "/two"], 3),
                         (False, 2))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda path: posture.delegation_read(
                "concurrent", [path], 3), ("/three", "/four")))
        self.assertEqual(sum(1 for fired, _count in results if fired), 1)
        self.assertTrue(posture.read_session_record("concurrent")[posture.DELEGATION_FIRED_KEY])

    def test_registry_and_nudge_updates_share_one_lock(self):
        posture = load_posture()
        self.assertEqual(posture.delegation_read("shared", ["/one", "/two"], 3), (False, 2))
        active = {"now": 0, "max": 0}
        guard = threading.Lock()
        real_write = posture._write_session_record

        def slow_write(*args):
            with guard:
                active["now"] += 1
                active["max"] = max(active["max"], active["now"])
            time.sleep(0.02)
            try:
                return real_write(*args)
            finally:
                with guard:
                    active["now"] -= 1

        with patch.object(posture, "_write_session_record", side_effect=slow_write):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda call: call(), (
                    lambda: posture.delegation_read("shared", ["/three"], 3),
                    lambda: posture.remember_agents("shared", ["worker-a"]),
                )))
        record = posture.read_session_record("shared")
        self.assertEqual(active["max"], 1)
        self.assertEqual(results[0], (True, 3))
        self.assertTrue(results[1])
        self.assertTrue(record[posture.DELEGATION_FIRED_KEY])
        self.assertEqual(record["announced"], ["worker-a"])

    def test_killed_lock_owner_does_not_block_the_session_forever(self):
        posture = load_posture()
        path = posture.session_record_path("killed")
        path.parent.mkdir(parents=True)
        lock = path.with_name(path.name + ".lock")
        code = ("import fcntl, os, sys, time; "
                "fd=os.open(sys.argv[1], os.O_RDWR|os.O_CREAT, 0o600); "
                "fcntl.flock(fd, fcntl.LOCK_EX); print('locked', flush=True); time.sleep(60)")
        owner = subprocess.Popen([sys.executable, "-c", code, str(lock)],
                                 stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: owner.poll() is None and owner.kill())
        self.assertEqual(owner.stdout.readline().strip(), "locked")
        owner.kill()
        owner.wait(timeout=5)
        owner.stdout.close()
        self.assertEqual(posture.delegation_read("killed", ["/one"], 3), (False, 1))

    def test_failed_later_composition_does_not_consume_the_threshold_read(self):
        for path in ("one.py", "two.py"):
            self.assertEqual(lifecycle.dispatch("claude-code", self.event(path, "compose")), {})
        real_invoke = lifecycle.invoke

        def fail_neutralizer(name, event):
            if name == "neutralize-tool-output":
                raise RuntimeError("neutralizer failed")
            return real_invoke(name, event)

        with patch.object(lifecycle, "invoke", side_effect=fail_neutralizer):
            with self.assertRaisesRegex(RuntimeError, "neutralizer failed"):
                lifecycle.dispatch("claude-code", self.event("three.py", "compose"))
        record = load_posture().read_session_record("compose")
        self.assertEqual(record["delegation_read_files"],
                         [str(self.home / "one.py"), str(self.home / "two.py")])
        self.assertFalse(record["delegation_nudge_fired"])
        retry = lifecycle.dispatch("claude-code", self.event("three.py", "compose"))
        self.assertIn("Spawn worker-a", retry["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(len(self.recorder.rows), 1)

    def test_custom_sidecar_symlink_cannot_escape_its_primitive_root(self):
        posture = load_posture()
        custom = self.home / "custom"
        directory = custom / "stances/delegation"
        directory.mkdir(parents=True)
        outside = self.home / "outside.json"
        outside.write_text(json.dumps({"schema_version": 1, "threshold": 3,
                                       "message": "Outside."}))
        (directory / "escaped.json").symlink_to(outside)
        config = {"primitive_roots": [str(custom)]}
        self.assertIsNone(posture.delegation_nudge("escaped", config,
                                                  root=self.home / "builtin"))

    def test_sidecar_swap_after_resolution_loads_the_resolved_file(self):
        posture = load_posture()
        custom = self.home / "custom"
        directory = custom / "stances/delegation"
        directory.mkdir(parents=True)
        good = directory / "good.json"
        bad = directory / "bad.json"
        good.write_text(json.dumps({"schema_version": 1, "threshold": 3,
                                    "message": "Trusted."}))
        bad.write_text(json.dumps({"schema_version": 1, "threshold": 3,
                                   "message": "Swapped."}))
        selected = directory / "racing.json"
        selected.symlink_to(good.name)
        real_resolve = Path.resolve

        def racing_resolve(path, *args, **kwargs):
            resolved = real_resolve(path, *args, **kwargs)
            if path == selected and path.is_symlink():
                path.unlink()
                path.symlink_to(bad.name)
            return resolved

        config = {"primitive_roots": [str(custom)]}
        with patch.object(Path, "resolve", racing_resolve):
            result = posture.delegation_nudge("racing", config, root=self.home / "builtin")
        self.assertEqual(result["message"], "Trusted.")


    def test_custom_sidecar_fifo_swapped_in_is_refused_without_hanging(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("no FIFOs on this platform")
        posture = load_posture()
        custom = self.home / "custom"
        directory = custom / "stances/delegation"
        directory.mkdir(parents=True)
        selected = directory / "fifo.json"
        selected.write_text(json.dumps({"schema_version": 1, "threshold": 3,
                                        "message": "Regular."}))
        real_resolve = Path.resolve

        def racing_resolve(path, *args, **kwargs):
            resolved = real_resolve(path, *args, **kwargs)
            if path == selected and not selected.is_fifo():
                selected.unlink()
                os.mkfifo(str(selected))
            return resolved

        config = {"primitive_roots": [str(custom)]}
        result = {}

        def load():
            with patch.object(Path, "resolve", racing_resolve):
                result["value"] = posture.delegation_nudge("fifo", config,
                                                           root=self.home / "builtin")

        worker = threading.Thread(target=load, daemon=True)
        worker.start()
        worker.join(10)
        if worker.is_alive():
            # Release the blocked open so the thread can end, then fail.
            os.close(os.open(str(selected), os.O_WRONLY | os.O_NONBLOCK))
            self.fail("a FIFO sidecar hung the hook")
        self.assertTrue(selected.is_fifo())
        self.assertIn("value", result)
        self.assertIsNone(result["value"])


class SessionStateTests(unittest.TestCase):
    """Hook-time writers share one lock, and a sweep never removes one a hook holds."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"HARNESS_HOME": str(self.home), "HOME": str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.posture = load_posture()

    def stale(self, *paths):
        old = time.time() - (self.posture.SESSION_TTL_DAYS + 1) * 86400
        for path in paths:
            os.utime(str(path), (old, old))

    def files(self, session):
        record = self.posture.session_record_path(session)
        return record, record.with_name(record.name + ".lock")

    def test_resume_narrows_under_the_lock_and_keeps_a_concurrent_write(self):
        posture = self.posture
        self.assertTrue(posture.write_session_record("resumed", {"agents": ["worker-a"], "at": 0}))
        record, _lock = self.files("resumed")
        env = without_harness_vars()
        env["HOME"] = str(self.home)
        payload = {"hook_event_name": "SessionStart", "source": "resume",
                   "session_id": "resumed", "cwd": str(self.home)}
        with posture._session_lock("resumed") as held:
            self.assertEqual(held, record)
            hook = subprocess.Popen([sys.executable, str(ROOT / "policy/hooks/harness-session.py")],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, env=env)
            self.addCleanup(lambda: hook.poll() is None and hook.kill())
            hook.stdin.write(json.dumps(payload))
            hook.stdin.close()
            # Long enough for an unlocked resume to have read the record it would write back.
            time.sleep(1.5)
            self.assertIsNone(hook.poll())
            self.assertTrue(posture._write_session_record(record, {
                "agents": ["worker-a"], "at": 0, posture.DELEGATION_READS_KEY: ["/one"]}))
        stderr = hook.stderr.read()
        hook.stdout.close()
        hook.stderr.close()
        self.assertEqual(hook.wait(timeout=30), 0, stderr)
        narrowed = posture.read_session_record("resumed")
        self.assertEqual(narrowed["agents"], [])
        self.assertEqual(narrowed[posture.DELEGATION_READS_KEY], ["/one"])

    def test_prune_removes_a_stale_record_with_its_idle_lock(self):
        self.assertTrue(self.posture.write_session_record("old", {"at": 0}))
        record, lock = self.files("old")
        self.stale(record, lock)
        self.assertEqual(self.posture.prune_session_records(), 1)
        self.assertFalse(record.exists())
        self.assertFalse(lock.exists())

    def test_prune_keeps_a_stale_record_and_lock_a_live_hook_holds(self):
        self.assertTrue(self.posture.write_session_record("held", {"at": 0}))
        record, lock = self.files("held")
        self.stale(record, lock)
        with self.posture._session_lock("held") as path:
            self.assertEqual(path, record)
            self.assertEqual(self.posture.prune_session_records(), 0)
            self.assertTrue(record.exists())
            self.assertTrue(lock.exists())

    def test_prune_removes_only_stale_idle_orphaned_locks_and_never_keeps(self):
        for session in ("orphan", "fresh", "mine"):
            self.assertTrue(self.posture.write_session_record(session, {"at": 0}))
            self.files(session)[0].unlink()
        self.stale(self.files("orphan")[1], self.files("mine")[1])
        self.posture.prune_session_records(keep="mine")
        self.assertFalse(self.files("orphan")[1].exists())
        self.assertTrue(self.files("fresh")[1].exists())
        self.assertTrue(self.files("mine")[1].exists())

    def test_a_lock_file_removed_before_it_is_locked_is_retaken_at_the_path(self):
        posture = self.posture
        record, lock = self.files("moved")
        real_flock = fcntl.flock
        removed = []

        def racing_flock(descriptor, operation):
            if not removed:
                removed.append(descriptor)
                os.unlink(str(lock))
            return real_flock(descriptor, operation)

        with patch.object(posture.fcntl, "flock", side_effect=racing_flock):
            with posture._session_lock("moved") as path:
                self.assertEqual(path, record)
                self.assertTrue(removed)
                self.assertTrue(lock.exists())
                probe = os.open(str(lock), os.O_RDWR)
                try:
                    with self.assertRaises(OSError):
                        real_flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(probe)

if __name__ == "__main__":
    unittest.main()

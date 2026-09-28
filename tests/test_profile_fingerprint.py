# SPDX-License-Identifier: MIT
"""Every new ledger row names the profile that wrote it, and a row from before reads unattributed.

The fingerprint is `posture.fingerprint()`: a digest of each switched-on module's content, the
stance variants, the configuration that reaches the model or a hook, and the harness version
(AD-22). These tests hold its two properties, that identical inputs match and any one difference
does not, and that every writer stamps it: the usage ledger, the decision log, a role worker's
row and the replay. A backfill never guesses one. Every row here is synthetic.

Run: python3 -m unittest discover tests
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from harness_core import workers  # noqa: E402


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


harness = _load("harness_fingerprint", REPO / "bin" / "harness")
posture = _load("posture_fingerprint", REPO / "policy" / "hooks" / "posture.py")
usage_log = _load("usage_log_fingerprint", REPO / "claude" / "hooks" / "usage-log.py")
decisions = _load("decisions_fingerprint", REPO / "claude" / "hooks" / "decisions.py")
bench = _load("cost_bench_fingerprint", REPO / "scripts" / "cost_bench.py")
KEY = posture.FINGERPRINT_KEY


def stamp(hours_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - hours_ago * 3600))


def session_row(session_id="s-1", **extra):
    row = {"kind": "session", "session_id": session_id, "runtime": "claude-code", "repo": "repo",
           "branch": "main", "models": ["model-a"], "started": stamp(2), "ended": stamp(1),
           "input": 10, "output": 200, "cache_read": 0, "cache_write": 0, "subagents": 0, "turns": 1}
    row.update(extra)
    return row


def checkout(root, version="1.0.0"):
    """A checkout small enough to reason about: the real catalog, two rules, one skill, one stance."""
    files = {"VERSION": version + "\n", "primitives/rules/alpha.md": "alpha\n",
             "primitives/rules/beta.md": "beta\n", "primitives/skills/gamma/SKILL.md": "gamma\n",
             "primitives/skills/gamma/reference.md": "detail\n",
             "primitives/stances/cost/balanced.md": "balanced\n",
             "primitives/stances/cost/lean.md": "lean\n"}
    for name, text in files.items():
        path = Path(root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    catalog = Path(root) / "lib" / "harness_core" / "catalog.py"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(str(REPO / "lib" / "harness_core" / "catalog.py"), str(catalog))
    return Path(root)


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        self.env = {"HOME": str(self.home), "HARNESS_HOME": str(self.home)}
        # One patch restores the whole environment, so a session's own HARNESS_* can be dropped.
        patched = patch.dict(os.environ)
        patched.start()
        self.addCleanup(patched.stop)
        for name in [n for n in os.environ if n.startswith("HARNESS_")]:
            del os.environ[name]
        os.environ.update(self.env)
        self.state = self.home / ".local" / "state" / "agent-harness"
        self.state.mkdir(parents=True)
        self.usage = self.state / "usage.jsonl"
        self.decisions = self.state / "decisions.jsonl"

    def config(self, data):
        path = self.home / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def lines(self, target):
        return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]

    def usage_report(self, by):
        args = harness.argparse.Namespace(days=30, by=by, rules=False, rescan=False, stance=None)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = harness.cmd_usage(args)
        self.assertEqual(rc, 0)
        return buf.getvalue()


class FingerprintTests(Home):
    def fingerprint(self, root, **env):
        return posture.fingerprint(dict(self.env, **env), config=None, root=root)

    def setUp(self):
        super().setUp()
        self.root = checkout(Path(self.tmp.name) / "checkout")
        self.session = Path(self.tmp.name) / "session.json"

    def session_file(self, data):
        self.session.write_text(json.dumps(data), encoding="utf-8")
        posture._FINGERPRINTS.clear()
        return {"HARNESS_SESSION_CONFIG": str(self.session)}

    def test_identical_inputs_match_within_a_process_and_across_processes(self):
        first = self.fingerprint(self.root)
        posture._FINGERPRINTS.clear()
        self.assertEqual(self.fingerprint(self.root), first)
        self.assertRegex(first, "^[0-9a-f]{64}$")
        script = ("import importlib.util, json, sys\n"
                  "spec = importlib.util.spec_from_file_location('p', sys.argv[1])\n"
                  "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
                  "print(m.fingerprint(json.loads(sys.argv[2]), root=__import__('pathlib').Path(sys.argv[3])))\n")
        out = subprocess.run([sys.executable, "-c", script, str(REPO / "policy" / "hooks" / "posture.py"),
                              json.dumps(self.env), str(self.root)],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), first)

    def test_one_module_switched_off_changes_it(self):
        base = self.fingerprint(self.root)
        self.assertNotEqual(self.fingerprint(self.root, **self.session_file({"rules": {"beta": "off"}})), base)
        self.assertNotEqual(self.fingerprint(self.root, **self.session_file({"skills": {"gamma": "off"}})), base)

    def test_one_modules_content_changes_it_including_a_skills_supporting_file(self):
        base = self.fingerprint(self.root)
        (self.root / "primitives" / "skills" / "gamma" / "reference.md").write_text("changed\n")
        posture._FINGERPRINTS.clear()
        edited = self.fingerprint(self.root)
        self.assertNotEqual(edited, base)
        (self.root / "primitives" / "rules" / "alpha.md").write_text("alpha, edited\n")
        posture._FINGERPRINTS.clear()
        self.assertNotEqual(self.fingerprint(self.root), edited)

    def test_one_stance_variant_changes_it(self):
        base = self.fingerprint(self.root)
        self.assertNotEqual(self.fingerprint(self.root, HARNESS_STANCE_COST="lean"), base)

    def test_one_setting_that_reaches_a_hook_changes_it_and_an_installer_switch_does_not(self):
        base = self.fingerprint(self.root)
        self.config({"telemetry": {"export": "on"}})
        posture._FINGERPRINTS.clear()
        self.assertNotEqual(self.fingerprint(self.root), base)
        self.config({"vscode": {"manage": False}, "remote_control": {"keep_awake": True}})
        posture._FINGERPRINTS.clear()
        self.assertEqual(self.fingerprint(self.root), base)

    def test_a_rewritten_configuration_or_selection_file_is_not_served_from_the_cache(self):
        base = self.fingerprint(self.root)
        self.config({"telemetry": {"export": "on"}})
        configured = self.fingerprint(self.root)
        self.assertNotEqual(configured, base)
        env = self.session_file({})
        empty = self.fingerprint(self.root, **env)
        self.session.write_text(json.dumps({"rules": {"beta": "off"}}), encoding="utf-8")
        self.assertNotEqual(self.fingerprint(self.root, **env), empty)

    def test_the_harness_version_changes_it(self):
        other = checkout(Path(self.tmp.name) / "other", version="1.0.1")
        self.assertNotEqual(self.fingerprint(other), self.fingerprint(self.root))

    def test_one_profile_matches_wherever_its_checkout_and_user_root_live(self):
        user_a, user_b = Path(self.tmp.name) / "a", Path(self.tmp.name) / "b"
        for user in (user_a, user_b):
            (user / "rules").mkdir(parents=True)
            (user / "rules" / "mine.md").write_text("mine\n")
        moved = checkout(Path(self.tmp.name) / "moved")
        self.config({"primitive_roots": [str(user_a)]})
        posture._FINGERPRINTS.clear()
        first = self.fingerprint(self.root)
        self.config({"primitive_roots": [str(user_b)]})
        posture._FINGERPRINTS.clear()
        self.assertEqual(self.fingerprint(moved), first)
        self.assertIn("mine", posture.profile(self.env, root=moved)["modules"]["rules"])

    def test_the_profile_names_no_path_and_leaves_out_switched_off_modules(self):
        self.config({"primitive_roots": [str(Path(self.tmp.name) / "a")]})
        document = posture.profile(dict(self.env, **self.session_file({"rules": {"beta": "off"}})),
                                   root=self.root)
        self.assertEqual(sorted(document["modules"]["rules"]), ["alpha"])
        self.assertEqual(document["stances"]["cost"]["variant"], "balanced")
        self.assertEqual(document["harness_version"], "1.0.0")
        self.assertEqual(document["config"], {})
        self.assertNotIn(self.tmp.name, json.dumps(document))


class UsageLedgerTests(Home):
    def setUp(self):
        super().setUp()
        posture._FINGERPRINTS.clear()
        usage_log._POSTURE[:] = []
        self.expected = posture.fingerprint(dict(os.environ))

    def test_appended_and_upserted_rows_carry_the_active_profile_under_current_schema(self):
        usage_log.append_row({"kind": "decision", "session_id": "d-1"}, path=self.usage)
        usage_log.upsert(session_row(), path=self.usage)
        rows = self.lines(self.usage)
        self.assertEqual([r[KEY] for r in rows], [self.expected] * 2)
        self.assertEqual([r["schema_version"] for r in rows],
                         [usage_log.SCHEMA_VERSION] * 2)
        self.assertEqual(usage_log.SCHEMA_VERSION, 2)
        self.assertEqual(decisions.SCHEMA_VERSION, 1)

    def test_a_row_that_names_its_profile_keeps_it_even_when_null(self):
        self.assertIsNone(usage_log.stamped({"kind": "worker", KEY: None})[KEY])
        self.assertEqual(usage_log.stamped({KEY: "abc"})[KEY], "abc")

    def test_a_copy_without_its_resolver_stamps_null(self):
        usage_log._POSTURE[:] = [None]
        self.assertIsNone(usage_log.stamped(session_row())[KEY])

    def test_a_worker_row_carries_the_profile_its_run_started_under_or_none(self):
        for name, extra in (("new", {KEY: "f" * 64}), ("old", {})):
            run = self.state / "workers" / name
            run.mkdir(parents=True)
            (run / "status.json").write_text(json.dumps(dict(
                {"schema_version": 1, "id": name, "role": "reviewer", "status": "completed",
                 "started_at": time.time() - 60, "finished_at": time.time()}, **extra)))
        rows = {r["agent_id"]: r for r in usage_log.worker_rows()}
        self.assertEqual(rows["new"][KEY], "f" * 64)
        self.assertIsNone(rows["old"][KEY])
        usage_log.upsert(list(rows.values()), path=self.usage)
        written = {r["agent_id"]: r[KEY] for r in self.lines(self.usage)}
        self.assertEqual(written, {"new": "f" * 64, "old": None})

    def test_a_worker_run_record_is_stamped_with_the_launching_profile(self):
        self.assertEqual(workers.session_fingerprint(REPO, dict(os.environ)), self.expected)
        self.assertIsNone(workers.session_fingerprint(Path(self.tmp.name) / "no-checkout"))

    def test_a_rescan_keeps_a_known_sessions_profile_and_never_guesses_one(self):
        projects = self.home / ".claude" / "projects" / "p"
        projects.mkdir(parents=True)
        for ident in ("known", "unknown"):
            (projects / (ident + ".jsonl")).write_text("{}\n")

        def scanned(path, session_id="", cwd="", prior=None, rescan=False):
            self.assertTrue(rescan)
            return [session_row(path.stem, harness_version=None),
                    {"kind": "subagent", "runtime": "claude-code", "session_id": path.stem,
                     "agent_id": "a-" + path.stem, "ended": stamp(1)}]

        usage_log.upsert(session_row("known", **{KEY: "a" * 64}), path=self.usage)
        with patch.object(usage_log, "scan_all", scanned), \
                patch.object(usage_log, "codex_home", return_value=self.home / "no-codex"):
            self.assertEqual(usage_log.rescan(1), 2)
        rows = {(r["session_id"], r["kind"]): r[KEY] for r in self.lines(self.usage)}
        self.assertEqual(rows, {("known", "session"): "a" * 64, ("known", "subagent"): "a" * 64,
                                ("unknown", "session"): None, ("unknown", "subagent"): None})


class ReaderTests(Home):
    def test_a_row_from_before_the_field_reads_unattributed_and_is_never_given_one(self):
        old = session_row("old", schema_version=1)
        self.usage.write_text(json.dumps(old) + "\n" + json.dumps(session_row("new", **{KEY: "c" * 64}))
                              + "\n", encoding="utf-8")
        self.assertNotIn(KEY, usage_log.ledger_rows(self.usage.read_text())[0])
        report = self.usage_report("profile")
        self.assertIn(harness.UNATTRIBUTED, report)
        self.assertIn("c" * 34, report)
        self.assertEqual(self.lines(self.usage)[0], old)

    def test_a_fingerprint_that_is_not_a_string_reads_unattributed(self):
        self.usage.write_text("".join(json.dumps(session_row(name, **{KEY: value})) + "\n"
                                      for name, value in (("list", ["x"]), ("dict", {"a": 1}),
                                                          ("number", 7))), encoding="utf-8")
        report = self.usage_report("profile")
        self.assertIn(harness.UNATTRIBUTED, report)
        self.assertNotIn("['x']", report)

    def test_a_reader_from_before_the_field_still_reads_a_new_row(self):
        usage_log.upsert(session_row("s-1"), path=self.usage)
        self.assertIn("TOTAL", self.usage_report("day"))
        rows = usage_log.ledger_rows(self.usage.read_text())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][KEY], usage_log.profile_fingerprint())

    def test_rules_by_profile_is_refused_rather_than_reported_as_something_else(self):
        args = harness.argparse.Namespace(days=30, by="profile", rules=True, rescan=False, stance=None)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(harness.cmd_usage(args), 2)


class DecisionLogTests(Home):
    def setUp(self):
        super().setUp()
        posture._FINGERPRINTS.clear()
        decisions._POSTURE[:] = []

    def test_decision_and_outcome_rows_carry_the_active_profile_under_schema_1(self):
        identity = decisions.record("grade-bash", "ask", "git push", key="k-1",
                                    event={"session_id": "s-1"}, target=self.decisions)
        decisions.observe(identity, "ran", "grade-bash", "s-1", target=self.decisions)
        rows = self.lines(self.decisions)
        self.assertEqual([r[KEY] for r in rows], [posture.fingerprint(dict(os.environ))] * 2)
        self.assertEqual([r["schema_version"] for r in rows], [1, 1])

    def test_an_old_decision_row_reads_without_a_fingerprint(self):
        self.decisions.write_text(json.dumps({"kind": "decision", "decision_id": "d-1",
                                              "point": "grade-bash", "schema_version": 1}) + "\n")
        self.assertNotIn(KEY, decisions.read_rows(self.decisions)[0])

    def test_a_resolver_that_fails_stamps_null_and_the_row_is_still_written(self):
        decisions._POSTURE[:] = [None]
        decisions.record("grade-bash", "ask", "git push", target=self.decisions)
        self.assertIsNone(self.lines(self.decisions)[0][KEY])


class ReplayTests(Home):
    def test_the_bare_arm_is_named_bare_and_the_harness_arm_carries_its_profile(self):
        opts = {"home": self.home, "harness_source": None, "profile_root": REPO}
        env = bench.arm_env("harness", None)
        self.assertEqual(bench.arm_profile("bare", bench.arm_env("bare", self.home), opts), "bare")
        self.assertEqual(bench.arm_profile("harness", env, opts),
                         posture.fingerprint(dict(env, HOME=str(self.home)), root=REPO))

    def test_the_harness_arms_stance_override_reaches_its_fingerprint(self):
        opts = {"home": self.home, "profile_root": REPO}
        plain = bench.arm_profile("harness", bench.arm_env("harness", None), opts)
        lean = bench.arm_profile("harness", bench.arm_env("harness", None, stance_cost="lean"), opts)
        self.assertNotEqual(plain, lean)


if __name__ == "__main__":
    unittest.main()

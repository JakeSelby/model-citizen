"""The replay runs an evaluator pack (#796): its tasks and checks come from outside this repository,
the scorer gets the check on stdin in an isolated container, the dry run checks contamination at
each tag's commit, and a registered run pins the pack's digest. No image is built, no container
started and no model called: Docker and the arms are fakes."""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from test_cost_bench import BENCH, Launch, env_flags, gate_reply, mounts, options
from test_cost_bench_tags import FakeArms, fake_replay, git, harness_repo, replay_args
from test_replay_pack import CANARY, SOLUTION, make_pack, task_spec

PACK = BENCH.replay_pack


def pack_task(tmp, long=False):
    pack = PACK.open_pack(make_pack(Path(tmp) / "pack"), harness_root=Path(tmp) / "harness")
    return pack, PACK.load_set(pack, "production", "production")[0][1 if long else 0]


class PackScoringTests(unittest.TestCase):
    def test_the_check_reaches_an_isolated_container_on_stdin_and_nothing_else_is_mounted(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack, task = pack_task(tmp)
            self.addCleanup(PACK.close_pack, pack)
            workdir = BENCH.task_workdir(task, None, Path(tmp) / "work" / "repo")
            calls = []

            def launch(command, **kwargs):
                calls.append((command, kwargs))
                return types.SimpleNamespace(stdout=BENCH.ORACLE_MARK + '["no answer"]\n', returncode=0)

            self.assertEqual(BENCH.score(task, workdir, None, "model-citizen-arm-bare:test", launch),
                             (False, "no answer"))
        command, kwargs = calls[0]
        self.assertEqual(command[command.index("--network") + 1], "none")
        self.assertEqual(len(mounts(command)), 1)
        self.assertTrue(mounts(command)[0].endswith(":/work"))
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", " ".join(env_flags(command)))
        self.assertTrue(kwargs["input"].startswith("# " + CANARY))
        self.assertIn("check(_Path('/work'))", kwargs["input"])
        self.assertNotIn(str(Path(tmp) / "pack"), " ".join(command))

    def test_a_run_starts_in_the_workspace_and_never_sees_the_check_or_the_solution(self):
        seen = {}

        def scorer(task, workdir, repo):
            seen["files"] = sorted(p.relative_to(workdir).as_posix() for p in Path(workdir).rglob("*")
                                   if p.is_file() and ".git" not in p.parts)
            seen["canary"] = any(CANARY.encode() in p.read_bytes() for p in Path(workdir).rglob("*") if p.is_file())
            return True, ""

        with tempfile.TemporaryDirectory() as tmp:
            pack, task = pack_task(tmp)
            self.addCleanup(PACK.close_pack, pack)
            launch = Launch([json.dumps([dict(type="result", subtype="success", is_error=False, num_turns=1,
                                              total_cost_usd=0.1, usage={})])])
            row = BENCH.run_one(task, 1, "bare", options(tmp, scorer=scorer), launch)
        self.assertEqual((row["outcome"], row["task_long"]), ("pass", False))
        self.assertEqual(seen, {"files": ["README.md", "tests/test_app.py"], "canary": False})
        argv = launch.calls[0][0]
        self.assertIn("Write answer.txt.", argv)


class PackVerifyTests(unittest.TestCase):
    def verify(self, tmp, verdicts, gate_exit=0):
        pack, task = pack_task(tmp)
        self.addCleanup(PACK.close_pack, pack)
        answers, calls = list(verdicts), []

        def launch(command, **kwargs):
            calls.append((command, kwargs))
            if command[-2:] == ["python3", "-"]:
                return types.SimpleNamespace(stdout=BENCH.ORACLE_MARK + answers.pop(0) + "\n", returncode=0)
            return types.SimpleNamespace(stdout="", returncode=gate_exit)

        errors = BENCH.verify_tasks([task], None, Path(tmp) / "verify", "model-citizen-arm-bare:test",
                                    [["python3", "bin/harness", "lint"]], launch)
        return errors, calls

    def test_the_workspace_gate_runs_then_the_check_fails_before_and_passes_after_the_solution(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors, calls = self.verify(tmp, ('["no answer"]', "[]"))
            solved = Path(tmp) / "verify" / "short-one-parent" / "answer.txt"
            self.assertEqual(solved.read_text(encoding="utf-8"), "42\n")
        self.assertEqual(errors, [])
        self.assertEqual(calls[0][0][-6:], ["python3", "-m", "unittest", "discover", "-s", "tests"])
        self.assertEqual(len(calls), 3)

    def test_a_check_that_already_passes_or_never_passes_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors, _ = self.verify(tmp, ("[]", '["still no"]'), gate_exit=1)
        self.assertIn("already fails in a clean snapshot", errors[0])
        self.assertIn("short-one: the check already passes at the parent sha", errors)
        self.assertIn("short-one: the check fails on the known-good tree (still no)", errors)


class PackContaminationTests(unittest.TestCase):
    def test_each_pack_task_is_checked_at_the_exact_harness_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = PACK.open_pack(make_pack(Path(tmp) / "pack"), harness_root=Path(tmp) / "repo")
            self.addCleanup(PACK.close_pack, pack)
            tasks = PACK.load_set(pack, "production", "production")[0]
            repo = harness_repo(Path(tmp) / "repo")
            clean = git_head(repo)
            (repo / "leak.py").write_text(SOLUTION, encoding="utf-8")
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "chore: leak")
            leaked = git_head(repo)
            self.assertEqual(BENCH.contamination_by_task(tasks, repo, clean), [("short-one", []), ("long-one", [])])
            report = dict(BENCH.contamination_by_task(tasks, repo, leaked))
            self.assertIn("short-one: the installed checkout's history holds the exact bytes of its solution.py",
                          report["short-one"])
            self.assertEqual(len(BENCH.contamination_errors(tasks, repo, leaked)), 6)


def git_head(repo):
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"]).decode().strip()


class PackCliTests(unittest.TestCase):
    def args(self, tmp, **over):
        source = Path(tmp) / "pack"
        if not source.exists():
            make_pack(source, tasks=[task_spec("short-one"), task_spec("long-one", True, 12),
                                     task_spec("nudge", True, 13, mechanism={"id": "delegation", "fired": "above-zero",
                                                                             "field": "spawns"})],
                      sets={"production": {"tasks": ["short-one", "long-one"]},
                            "micro": {"model": "claude-small", "tasks": ["nudge"]}})
        values = dict(pack=str(source), pack_ref=None, pack_digest=None, pack_set=None, tier="production",
                      pair=None, break_even=7.6)
        values.update(over)
        args = replay_args(tmp, **{k: v for k, v in values.items() if k in ("exploratory", "dry_run", "tag")})
        for key, value in values.items():
            setattr(args, key, value)
        args.tasks = None
        return args

    def replay_cli(self, tmp, args, replay=fake_replay):
        fake, out, err = FakeArms(), io.StringIO(), io.StringIO()
        with mock.patch.object(BENCH, "ROOT", Path(tmp) / "repo"), \
                mock.patch.object(BENCH.arms, "build_arm", fake.build_arm), \
                mock.patch.object(BENCH.arms, "egress", fake.egress), \
                mock.patch.object(BENCH, "replay", replay), \
                mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "t"}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = BENCH.cmd_replay(args)
        return code, out.getvalue(), err.getvalue(), fake

    def test_a_dry_run_names_the_pack_each_task_and_its_contamination_result_and_builds_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            code, out, _, fake = self.replay_cli(tmp, self.args(tmp, dry_run=True, exploratory=True, tag=["v1", "v2"]))
        self.assertEqual((code, fake.built), (0, []))
        self.assertRegex(out, r"pack test-pack 1\.0\.0 at [0-9a-f]{40}, digest [0-9a-f]{64}, set production")
        self.assertIn("task long-one: long, expected absorbed calls 12", out)
        self.assertEqual(out.count("contamination short-one: clean"), 2)
        self.assertEqual(out.count("short-one rep 1"), 4)

    def test_a_dry_run_at_a_commit_that_carries_the_canary_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = harness_repo(Path(tmp) / "repo")
            (repo / "notes.md").write_text(CANARY + "\n", encoding="utf-8")
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "docs: notes")
            git(repo, "tag", "v3")
            code, out, err, _ = self.replay_cli(tmp, self.args(tmp, dry_run=True, exploratory=True, tag=["v3"]))
        self.assertEqual(code, 2)
        self.assertIn("contamination short-one: short-one: the installed checkout carries the pack's canary in notes.md", out)
        self.assertIn("refused by the contamination control", err)

    def test_the_tasks_file_a_pair_and_pack_flags_without_a_pack_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            args = self.args(tmp)
            args.tasks = "benchmarks/tasks.json"
            with self.assertRaisesRegex(SystemExit, "--tasks and --pack name two task sources"):
                self.replay_cli(tmp, args)
            with self.assertRaisesRegex(SystemExit, "--pair is refused with --pack"):
                self.replay_cli(tmp, self.args(tmp, pair="benchmarks/ablations/x.json"))
            with self.assertRaisesRegex(SystemExit, "need --pack"):
                self.replay_cli(tmp, self.args(tmp, pack=None, pack_digest="0" * 64))

    def test_a_registered_run_must_pin_the_digest_and_every_row_names_the_pack(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            with self.assertRaisesRegex(SystemExit, "pass --pack-digest [0-9a-f]{64}"):
                self.replay_cli(tmp, self.args(tmp))
            seen = {}

            def replay(tasks, opts, launch=None, out=None):
                seen.update(opts=opts, tasks=tasks)
                return fake_replay(tasks, opts, launch, out)

            pinned = PACK.open_pack(Path(tmp) / "pack", harness_root=Path(tmp) / "repo")
            PACK.close_pack(pinned)
            digest = pinned["digest"]
            code, out, _, _ = self.replay_cli(tmp, self.args(tmp, pack_digest=digest), replay)
        stamp = seen["opts"]["stamp"]
        self.assertEqual((stamp["pack"], stamp["pack_version"], stamp["pack_digest"]), ("test-pack", "1.0.0", digest))
        self.assertEqual(len(stamp["pack_commit"]), 40)
        self.assertEqual(seen["opts"]["preflight_green"], PACK.GATE_GREEN)
        self.assertIn("python3 -m unittest discover -s tests", seen["opts"]["preflight_prompt"])
        self.assertEqual([t["id"] for t in seen["tasks"]], ["short-one", "long-one"])

    def test_the_micro_tier_reads_its_set_and_model_from_the_pack(self):
        seen = {}

        def verify(args, tasks):
            seen.update(model=args.model, tasks=[t["id"] for t in tasks], spend_cap=args.spend_cap)
            return 0

        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            args = self.args(tmp, tier="micro", model=None, verify_tasks=True)
            args.model, args.verify_tasks, args.reps, args.run_cap, args.spend_cap = None, True, None, None, None
            with mock.patch.object(BENCH, "verify_command", side_effect=verify):
                code, out, _, _ = self.replay_cli(tmp, args)
            self.assertEqual(code, 0)
            self.assertEqual(seen, {"model": "claude-small", "tasks": ["nudge"], "spend_cap": BENCH.micro.SPEND_CAP_USD})
            self.assertIn("set micro (micro tier)", out)
            with self.assertRaisesRegex(SystemExit, "is a production set, not micro"):
                self.replay_cli(tmp, self.args(tmp, tier="micro", pack_set="production"))

    def test_a_pack_series_differs_from_another_set_of_the_same_pack(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            series = []
            for tier in ("production", "micro"):
                args = self.args(tmp, tier=tier, dry_run=True, exploratory=True)
                args.model = None if tier == "micro" else args.model
                args.reps = args.run_cap = args.spend_cap = None
                self.replay_cli(tmp, args)
                series.append(args.series_source)
        self.assertNotEqual(series[0], series[1])
        self.assertEqual(json.loads(series[1])["set"], "micro")


class PackPreflightTests(unittest.TestCase):
    def test_a_pack_preflight_is_green_on_its_own_gate_and_red_on_a_refusal(self):
        ok = "test_ok (test_app.T) ... ok\n\nRan 1 test in 0.001s\n\nOK\n"
        self.assertTrue(BENCH.gate_passed(gate_reply(ok), PACK.GATE_GREEN))
        self.assertFalse(BENCH.gate_passed(gate_reply("Ran 1 test\n\nFAILED (failures=1)\n"), PACK.GATE_GREEN))
        self.assertFalse(BENCH.gate_passed(gate_reply(ok + "PermissionError: [Errno 1] Operation not permitted\n"),
                                           PACK.GATE_GREEN))
        self.assertFalse(BENCH.gate_passed(gate_reply(ok)))  # the harness's own bar is lint, not a suite

    def test_the_preflight_runs_the_pack_gate_in_the_first_task_s_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack, task = pack_task(tmp)
            self.addCleanup(PACK.close_pack, pack)
            ok = "Ran 1 test in 0.001s\n\nOK\n"
            launch = Launch([gate_reply(ok), gate_reply(ok)])
            opts = options(tmp, preflight_prompt=PACK.preflight_prompt(task), preflight_green=PACK.GATE_GREEN)
            checks, _ = BENCH.preflight([task], opts, launch)
        self.assertEqual([c["passed"] for c in checks], [True, True])
        self.assertTrue(all(PACK.preflight_prompt(task) in command for command, _ in launch.calls))


if __name__ == "__main__":
    unittest.main()

"""An ablation run on an evaluator pack (#1152): `replay --ablations` with `--pack` runs each pack
task's contamination check at the exact commit `--tag` names before any arm is built or launched,
refuses a registered run without the pack's digest pinned, stamps every row with the pack, and
states the worst-case cost at the run's own `--run-cap` beside the minimum detectable effect. No
image is built, no container started and no model called: Docker and the arms are fakes."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_cost_bench import BENCH, TASK, Launch
from test_cost_bench_ablations import ABL, ablation_options, manifest, run_output, write
from test_cost_bench_tags import FakeArms, fake_replay, git, harness_repo, replay_args
from test_replay_pair import MANIFEST as PAIR
from test_replay_pack import CANARY, SOLUTION, make_pack, task_spec

PACK = BENCH.replay_pack
SWEEP = manifest({"id": "no-secrets", "removes": "rules/secrets"})


def contaminate(repo, how):
    """Tag `v3` on a commit whose installed checkout may hold a pack task's answer: a file carrying
    the pack's canary, or the exact bytes of its solution somewhere in history."""
    if how == "canary":
        (repo / "notes.md").write_text(CANARY + "\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "docs: notes")
    else:
        (repo / "leak.py").write_text(SOLUTION, encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "chore: leak")
        git(repo, "rm", "-q", "leak.py")
        git(repo, "commit", "-qm", "chore: unleak")
    git(repo, "tag", "v3")


class AblationPackTests(unittest.TestCase):
    def args(self, tmp, **over):
        source = Path(tmp) / "pack"
        if not source.exists():
            make_pack(source, tasks=[task_spec("short-one"), task_spec("long-one", True, 12)])
        values = dict(pack=str(source), pack_ref=None, pack_digest=None, pack_set=None, tier="production",
                      pair=None, break_even=7.6, ablations=str(write(tmp, SWEEP)), schedule_seed=None,
                      run_cap=0.1)
        values.update(over)
        args = replay_args(tmp, **{k: v for k, v in values.items() if k in ("exploratory", "dry_run", "tag")})
        for key, value in values.items():
            setattr(args, key, value)
        args.tasks = None
        return args

    def cli(self, tmp, args, replay=fake_replay):
        fake, out, err = FakeArms(), io.StringIO(), io.StringIO()
        calls = []

        def counted(*a, **kw):
            calls.append(a)
            return replay(*a, **kw)
        # The entry check reads a real harness's resolver; the stand-in repository has none.
        with mock.patch.object(BENCH, "ROOT", Path(tmp) / "repo"), \
                mock.patch.object(BENCH, "ablation_entry_errors", lambda *a, **kw: []), \
                mock.patch.object(BENCH.arms, "build_arm", fake.build_arm), \
                mock.patch.object(BENCH.arms, "egress", fake.egress), \
                mock.patch.object(BENCH, "replay", counted), \
                mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "t"}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = BENCH.cmd_replay(args)
            except SystemExit as exc:
                code = exc
        return code, out.getvalue(), err.getvalue(), fake, calls

    def digest(self, tmp):
        pack = PACK.open_pack(Path(tmp) / "pack", harness_root=Path(tmp) / "repo")
        PACK.close_pack(pack)
        return pack["digest"]

    def test_a_dry_run_checks_every_task_at_the_tag_s_commit_and_builds_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = harness_repo(Path(tmp) / "repo")
            v1 = BENCH.resolve_tag(repo, "v1")
            code, out, _, fake, calls = self.cli(tmp, self.args(tmp, dry_run=True, exploratory=True, tag=["v1"]))
        self.assertEqual((code, fake.built, calls), (0, [], []))
        self.assertIn("arm harness (control) harness@v1 at %s" % v1, out)
        self.assertIn("contamination short-one at %s: clean" % v1, out)
        self.assertIn("contamination long-one at %s: clean" % v1, out)
        self.assertLess(out.index("contamination long-one"), out.index("    short-one rep 1"))

    def test_a_contaminated_pack_task_is_refused_on_the_ablation_path(self):
        for how, expected in (("canary", "the installed checkout carries the pack's canary in notes.md"),
                              ("solution", "the installed checkout's history holds the exact bytes of its solution.py")):
            with self.subTest(how=how), tempfile.TemporaryDirectory() as tmp:
                contaminate(harness_repo(Path(tmp) / "repo"), how)
                code, out, err, fake, _ = self.cli(tmp, self.args(tmp, dry_run=True, exploratory=True, tag=["v3"]))
                self.assertEqual(code, 2)
                self.assertIn("short-one: " + expected, out)
                self.assertIn("refused by the contamination control", err)
                # A real run, its digest pinned, stops before any image is built or arm launched.
                code, _, err, fake, calls = self.cli(tmp, self.args(tmp, exploratory=True, tag=["v3"],
                                                                     pack_digest=self.digest(tmp)))
                self.assertEqual(code.code, 2)
                self.assertEqual((fake.built, calls), ([], []))
                self.assertIn("cost-bench: contamination: short-one: " + expected, err)
                self.assertIn("refusing the ablation run before any arm launches", err)

    def test_a_clean_tag_passes_where_a_contaminated_one_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            contaminate(harness_repo(Path(tmp) / "repo"), "canary")
            code, _, _, _, _ = self.cli(tmp, self.args(tmp, dry_run=True, exploratory=True, tag=["v2"]))
        self.assertEqual(code, 0)

    def test_a_registered_run_must_pin_the_digest_before_anything_is_built(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            code, _, _, fake, calls = self.cli(tmp, self.args(tmp))
        self.assertIsInstance(code, SystemExit)
        self.assertRegex(str(code), "pass --pack-digest [0-9a-f]{64}")
        self.assertEqual((fake.built, calls), ([], []))

    def test_a_pinned_registered_run_builds_control_from_the_tag_and_stamps_the_pack(self):
        seen = {}

        def replay(tasks, opts, launch=None, out=None):
            seen.update(opts=opts, tasks=tasks)
            return fake_replay(tasks, opts, launch, out)

        with tempfile.TemporaryDirectory() as tmp:
            repo = harness_repo(Path(tmp) / "repo")
            args = self.args(tmp, tag=["v1"])
            args.pack_digest = digest = self.digest(tmp)
            code, _, _, fake, _ = self.cli(tmp, args, replay)
            v1, head = BENCH.resolve_tag(repo, "v1"), BENCH.resolve_tag(repo, "HEAD")
        self.assertEqual(code, 0)
        self.assertNotEqual(v1, head)
        harness = [d for d in fake.built if d["harness"]]
        self.assertEqual({d["harness"]["commit"] for d in harness}, {v1})
        self.assertEqual(len(harness), 2)  # control and the one ablation arm
        stamp = seen["opts"]["stamp"]
        self.assertEqual((stamp["pack"], stamp["pack_version"], stamp["pack_digest"]), ("test-pack", "1.0.0", digest))
        self.assertEqual(seen["opts"]["ablation"]["name"], "sweep")
        self.assertEqual(seen["opts"]["preflight_green"], PACK.GATE_GREEN)
        self.assertEqual([t["id"] for t in seen["tasks"]], ["short-one", "long-one"])

    def test_the_dry_run_states_the_worst_case_at_the_run_s_own_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            figures = {}
            for cap, preflight in ((0.1, False), (0.5, True), (None, False)):
                args = self.args(tmp, dry_run=True, exploratory=True, run_cap=cap)
                args.skip_preflight, args.reps = preflight, 3
                _, out, _, _, _ = self.cli(tmp, args)
                figures[cap] = out
        arms = len(ABL.arm_names(SWEEP))
        self.assertIn("worst case, before any spend: %.2f USD if all %d run(s) reach 0.1 USD (--run-cap) and "
                      "all %d preflight(s) reach %g USD"
                      % (2 * 3 * arms * 0.1 + arms * BENCH.PREFLIGHT_CAP_USD, 2 * 3 * arms, arms,
                         BENCH.PREFLIGHT_CAP_USD), figures[0.1])
        self.assertIn("0.1 USD per run (--run-cap)", figures[0.1])
        self.assertIn("worst case, before any spend: %.2f USD if all %d run(s) reach 0.5 USD (--run-cap) and "
                      "all 0 preflight(s)" % (2 * 3 * arms * 0.5, 2 * 3 * arms), figures[0.5])
        self.assertIn("reach %g USD (default run cap)" % BENCH.RUN_CAP_USD, figures[None])
        for out in figures.values():
            self.assertLess(out.index("worst case"), out.index("    short-one rep 1"))
            self.assertLess(out.index("minimum detectable effect"), out.index("    short-one rep 1"))

    def test_a_pair_stays_refused_with_a_pack(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            with self.assertRaisesRegex(SystemExit, "--pair is refused with --pack"):
                BENCH.open_pack_for(self.args(tmp, pair="benchmarks/ablations/x.json", ablations=None))


    def test_a_pair_file_given_to_ablations_is_refused_with_a_pack_before_anything_opens(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            pair = Path(tmp) / "pair.json"
            pair.write_text(json.dumps(PAIR), encoding="utf-8")
            args = self.args(tmp, ablations=str(pair), exploratory=True, dry_run=True)
            with mock.patch.object(BENCH.replay_pack, "open_pack") as opened:
                code, _, _, fake, calls = self.cli(tmp, args)
            self.assertIsInstance(code, SystemExit)
            self.assertIn("schema-1 pair file given to --ablations is refused with --pack", str(code))
            self.assertIsNone(args.pair)
            opened.assert_not_called()
            self.assertEqual((fake.built, calls), ([], []))


class AblationPackRowTests(unittest.TestCase):
    def test_every_ablation_row_records_the_pack(self):
        identity = {"pack": "test-pack", "pack_version": "1.0.0", "pack_commit": "c" * 40, "pack_digest": "d" * 64}
        with tempfile.TemporaryDirectory() as tmp:
            opts = ablation_options(tmp, SWEEP)
            opts["stamp"].update(identity)
            launch = Launch([run_output() for _ in range(2 * len(ABL.arm_names(SWEEP)))])
            rows, stopped = BENCH.replay([TASK], opts, launch)
        self.assertFalse(stopped)
        self.assertEqual({row["arm"] for row in rows}, set(ABL.arm_names(SWEEP)))
        for row in rows:
            self.assertEqual({k: row[k] for k in identity}, identity)
            self.assertEqual(row["ablation"]["schema"], 2)


if __name__ == "__main__":
    unittest.main()

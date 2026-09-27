"""Each `--tag` is a harness ref built into an arm image of its own, and every ref resolves before
anything is built. No test here builds an image or launches an agent: the builder and the egress
are replaced by fakes that record what they were asked for."""
import argparse
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from test_cost_bench import BENCH, arm_record

TASK = {"id": "demo", "kind": "synthetic", "parent_sha": "HEAD", "good_sha": None,
        "prompt": ["do", "it"], "tests": {"oracle": "none"}, "max_turns": 5}


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t"] + list(args),
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def harness_repo(root):
    """A checkout with a VERSION, tagged `v1` one version behind its branch, which is `v2`."""
    root = Path(root)
    root.mkdir(parents=True)
    (root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "chore: one")
    git(root, "tag", "v1")
    (root / "VERSION").write_text("2.0.0\n", encoding="utf-8")
    git(root, "commit", "-qam", "chore: two")
    git(root, "tag", "v2")
    (root / "policy").mkdir()
    (root / "policy" / "prices.json").write_text(json.dumps({"models": {}}), encoding="utf-8")
    return root


def replay_args(work, **over):
    values = {"tasks": str(Path(work) / "tasks.json"), "task": None, "verify_tasks": False,
              "check_image": None, "tag": ["v1"], "model": "claude-test", "reps": 1, "run_cap": 2.0,
              "spend_cap": 25.0, "stance_cost": None, "bucket": "", "predicted_ratio": None,
              "history_dir": str(Path(work) / "history"), "change_note": "",
              "out": str(Path(work) / "out"), "arms_dir": str(Path(work) / "arms"), "raw": None,
              "tmp": None, "skip_preflight": True, "dry_run": False}
    values.update(over)
    Path(values["tasks"]).write_text(json.dumps({"tasks": [TASK]}), encoding="utf-8")
    return argparse.Namespace(**values)


class FakeArms:
    """Stands in for `replay_arms.build_arm` and `replay_arms.egress`: records the declarations it
    was asked to build and the egress it stood up, and builds nothing."""
    def __init__(self):
        self.built, self.egress_up = [], []

    def build_arm(self, decl, out_dir, snapshot, launch=None, repo=None, no_cache=False, tag=None, tmp=None):
        self.built.append(decl)
        ref = (decl["harness"] or {}).get("ref")
        record = arm_record(decl["arm"], ref or "v9.9.9")
        if decl["harness"]:
            record = dict(record, harness_commit=decl["harness"]["commit"])
        return dict(record, declaration=decl, manifest={"claude_code_version": "1.0"})

    @contextlib.contextmanager
    def egress(self, image, launch=None, token=None, hosts=None):
        self.egress_up.append(image)
        yield {"network": "net", "proxy": "proxy", "url": "http://proxy:3128"}


def fake_replay(tasks, opts, launch=None, out=None):
    rows = [dict(opts["stamp"], task="demo", arm=arm, tag=opts["tag"], rep=1, error=False, passed=True,
                 cost_usd=1.0, cost_normalised_usd=1.0, cache_miss_ratio=0.5, change_note="",
                 **BENCH.arm_stamp(opts["arms"][arm]))
            for arm in BENCH.ARMS]
    Path(out).write_text("", encoding="utf-8")
    return rows, False


class TagResolutionTests(unittest.TestCase):
    def test_a_ref_that_does_not_resolve_is_named_not_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = harness_repo(Path(tmp) / "repo")
            self.assertEqual(len(BENCH.resolve_tag(repo, "v1")), 40)
            with self.assertRaises(SystemExit) as caught:
                BENCH.resolve_tag(repo, "v9")
            self.assertIn("v9", str(caught.exception))

    def test_a_tag_is_labelled_from_its_own_commit_not_the_branch(self):
        """The branch is a version ahead: a pinned row labelled 2.0.0 would name a harness that
        never ran."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = harness_repo(Path(tmp) / "repo")
            self.assertEqual(BENCH.tag_version(repo, BENCH.resolve_tag(repo, "v1"), "v1"), "1.0.0")

    def test_each_tag_declares_its_full_commit_and_the_bare_arm_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = harness_repo(Path(tmp) / "repo")
            bare, harness = BENCH.declarations(["v1", "v2"], repo)
            self.assertIsNone(bare["harness"])
            self.assertEqual([d["harness"]["ref"] for _, d in harness], ["v1", "v2"])
            self.assertEqual([d["harness"]["commit"] for _, d in harness],
                             [BENCH.resolve_tag(repo, "v1"), BENCH.resolve_tag(repo, "v2")])


class TagScheduleTests(unittest.TestCase):
    def run_replay(self, tmp, args, fake=None):
        fake = fake or FakeArms()
        out = io.StringIO()
        with mock.patch.object(BENCH, "ROOT", Path(tmp) / "repo"), \
                mock.patch.object(BENCH.arms, "build_arm", fake.build_arm), \
                mock.patch.object(BENCH.arms, "egress", fake.egress), \
                mock.patch.object(BENCH, "replay", fake_replay), \
                mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "t"}), redirect_stdout(out):
            code = BENCH.cmd_replay(args)
        return code, out.getvalue(), fake

    def test_a_dry_run_prints_the_arms_and_the_schedule_for_every_tag_and_builds_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            args = replay_args(tmp, tag=["v1", "v2"], dry_run=True)
            code, out, fake = self.run_replay(tmp, args)
            self.assertEqual((code, fake.built, fake.egress_up), (0, [], []))
            self.assertIn("arm bare: model-citizen-arm-bare:", out)
            self.assertIn("tag v1: arm harness@v1 at ", out)
            self.assertIn("model-citizen-arm-harness:", out)
            self.assertEqual(out.count("demo rep 1"), 4)  # two arms, two tags

    def test_the_installed_harness_is_no_longer_a_tag(self):
        """#428: `candidate` ran the harness the host profile held, so no arm may be it now."""
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            for tags in ([], ["candidate"], ["v1", "candidate"]):
                with self.assertRaises(SystemExit) as caught:
                    self.run_replay(tmp, replay_args(tmp, tag=tags))
                self.assertIn("--tag", str(caught.exception))

    def test_a_ref_that_does_not_resolve_stops_the_run_before_anything_is_built(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            fake = FakeArms()
            with self.assertRaises(SystemExit):
                self.run_replay(tmp, replay_args(tmp, tag=["v1", "v9"]), fake)
            self.assertEqual(fake.built, [])

    def test_no_credential_stops_the_run_before_anything_is_built(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            fake = FakeArms()
            with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": ""}), \
                    mock.patch.object(BENCH, "ROOT", Path(tmp) / "repo"), \
                    mock.patch.object(BENCH.arms, "build_arm", fake.build_arm):
                with self.assertRaises(SystemExit) as caught:
                    BENCH.cmd_replay(replay_args(tmp))
            self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", str(caught.exception))
            self.assertEqual(fake.built, [])

    def test_each_tag_is_its_own_image_and_writes_its_own_history_row(self):
        """The whole point of the flag: two tags, one invocation, one bare arm, two rows."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = harness_repo(Path(tmp) / "repo")
            code, _, fake = self.run_replay(tmp, replay_args(tmp, tag=["v1", "v2"]))
            self.assertEqual(code, 0)
            self.assertEqual([d["arm"] for d in fake.built], ["bare", "harness", "harness"])
            self.assertEqual(len(fake.egress_up), 1)
            rows = [json.loads(line) for line in
                    (Path(tmp) / "history" / "history.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([r["tag"] for r in rows], ["v1", "v2"])
            self.assertEqual([r["harness_version"] for r in rows], ["1.0.0", "2.0.0"])
            self.assertEqual([r["harness_sha"] for r in rows],
                             [BENCH.resolve_tag(repo, "v1"), BENCH.resolve_tag(repo, "v2")])
            self.assertEqual(rows[0]["arms"]["harness"]["harness_commit"], BENCH.resolve_tag(repo, "v1"))
            self.assertEqual(rows[0]["arms"]["bare"]["harness_commit"], None)
            self.assertEqual(rows[0]["series"], rows[1]["series"])
            for tag in ("v1", "v2"):
                self.assertTrue((Path(tmp) / "out" / tag / BENCH.RESULTS).exists(), tag)


if __name__ == "__main__":
    unittest.main()

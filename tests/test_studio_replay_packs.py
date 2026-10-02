"""The Studio replay form runs evaluator packs found beside the checkout, pinned by digest."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_harness import REPO
from harness_core.studio import packs, replay, run_store, server
from test_replay_pack import make_pack, task_spec

sys.path.insert(0, str(REPO / "scripts"))
import replay_pack  # noqa: E402

REVISION = "a" * 40


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def harness(root):
    """A committed stand-in for a harness checkout whose own task list is empty, as main's is."""
    root = Path(root)
    (root / "benchmarks").mkdir(parents=True)
    (root / "benchmarks" / "tasks.json").write_text(
        json.dumps({"schema_version": 1, "tasks": [], "retired": []}), encoding="utf-8")
    git(root.parent, "init", "-q", str(root))
    git(root, "-c", "user.name=t", "-c", "user.email=t@invalid", "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@invalid", "commit", "-qm", "chore: one")
    return root


def target(kind, ref):
    return {"kind": kind, "ref": ref, "revision": REVISION if kind == "release" else "b" * 40,
            "version": "1.0.0" if kind == "release" else None,
            "draft": ref if kind == "draft" else None}


class PackCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = harness(self.root / "harness")
        self.pack = make_pack(self.root / "test-pack")

    def fresh_clone(self):
        clone = self.root / "fresh"
        git(self.root, "clone", "-q", str(self.source), str(clone))
        return clone

    def test_a_fresh_checkout_lists_the_pack_beside_it_with_its_tasks(self):
        catalog = replay.task_catalog(self.fresh_clone())
        server.REPLAY_CATALOG.validate(catalog)
        self.assertEqual(catalog["tasks"], [])
        self.assertEqual([p["name"] for p in catalog["packs"]], ["test-pack"])
        listed = catalog["packs"][0]
        opened = replay_pack.open_pack(self.pack, "HEAD", None, self.source)
        replay_pack.close_pack(opened)
        self.assertEqual((listed["version"], listed["commit"], listed["digest"]),
                         (opened["version"], opened["commit"], opened["digest"]))
        self.assertEqual(listed["short_digest"], opened["digest"][:12])
        self.assertEqual([t["id"] for t in listed["tasks"]], ["short-one", "long-one"])
        self.assertEqual(catalog["default_pack"], opened["digest"])
        self.assertNotIn("source", listed)

    def test_the_documented_pack_is_the_default_over_an_earlier_name(self):
        make_pack(self.root / "model-citizen-evals", name="model-citizen-evals")
        found = packs.discover(self.source)
        self.assertEqual([p["name"] for p in found["packs"]], ["model-citizen-evals", "test-pack"])
        self.assertEqual(found["default_digest"], found["packs"][0]["digest"])
        only = make_pack(self.root / "nested" / "a-pack", name="a-pack")
        self.assertTrue(only.exists())
        self.assertNotIn("a-pack", [p["name"] for p in packs.discover(self.source)["packs"]])

    def test_a_linked_worktree_finds_the_packs_beside_its_main_checkout(self):
        elsewhere = self.root / "worktrees"
        elsewhere.mkdir()
        git(self.source, "worktree", "add", "-q", "--detach", str(elsewhere / "task"))
        names = [p["name"] for p in packs.discover(elsewhere / "task")["packs"]]
        self.assertEqual(names, ["test-pack"])

    def test_a_broken_pack_is_reported_not_listed(self):
        broken = self.root / "broken"
        broken.mkdir()
        (broken / "pack.json").write_text("{}", encoding="utf-8")
        found = packs.discover(self.source)
        self.assertEqual([p["name"] for p in found["packs"]], ["test-pack"])
        self.assertEqual([item["source"] for item in found["skipped"]], [str(broken.resolve())])

    def test_the_chosen_pack_is_pinned_into_the_native_command(self):
        chosen = packs.discover(self.source)["packs"][0]

        def resolver(kind, ref):
            return target(kind, ref)

        request = replay.resolve_request({
            "targets": [{"kind": "release", "ref": "v1.0.0"}, {"kind": "draft", "ref": "d"}],
            "model": "m", "repetitions": 1, "tasks": ["short-one"], "max_budget_usd": "1",
            "spend_cap_usd": "2", "pre_registration": "plan.md",
            "pack": {"name": chosen["name"], "digest": chosen["digest"]}},
            resolver, lambda name, digest: packs.select(self.source, name, digest))
        self.assertEqual(request.pack["source"], str(self.pack.resolve()))
        command = replay.command_for_target(request, request.targets[1], self.source,
                                            self.root / "out")
        at = command.index("--pack")
        self.assertEqual(command[at:at + 6], ["--pack", str(self.pack.resolve()), "--pack-ref",
                                              chosen["commit"], "--pack-digest", chosen["digest"]])
        self.assertEqual(replay.ReplayRequest.parse(request.as_dict()), request)
        replay.validate_task_selection(self.source, request)
        with self.assertRaisesRegex(replay.ReplayError, "unknown benchmark tasks: nope"):
            replay.validate_task_selection(self.source, replay.ReplayRequest.parse(
                dict(request.as_dict(), tasks=["nope"])))

    def admission(self):
        admission = replay.ReplayAdmission(self.source, self.root / "state", None, None)
        admission._resolve = target
        return admission

    def resolved(self, admission):
        chosen = packs.discover(self.source)["packs"][0]
        return admission.resolve({
            "targets": [{"kind": "draft", "ref": "a"}, {"kind": "draft", "ref": "b"}],
            "model": "m", "repetitions": 1, "tasks": ["short-one"], "max_budget_usd": "1",
            "spend_cap_usd": "2", "pre_registration": None,
            "pack": {"name": chosen["name"], "digest": chosen["digest"]}})

    def test_a_pack_whose_content_moved_after_preview_is_refused_at_launch(self):
        admission = self.admission()
        request = self.resolved(admission)
        (self.pack / "README.md").write_text("changed\n", encoding="utf-8")
        git(self.pack, "-c", "user.name=t", "-c", "user.email=t@invalid", "add", "-A")
        git(self.pack, "-c", "user.name=t", "-c", "user.email=t@invalid", "commit", "-qm", "docs")
        with self.assertRaisesRegex(replay.ReplayError, "not available at that digest"):
            admission.confirm(request.as_dict())

    def test_a_pack_whose_commit_moved_with_the_same_content_is_refused_at_launch(self):
        admission = self.admission()
        request = self.resolved(admission)
        git(self.pack, "-c", "user.name=t", "-c", "user.email=t@invalid", "commit", "-q",
            "--allow-empty", "-m", "chore: empty")
        with self.assertRaisesRegex(replay.ReplayError, "pack identity changed after spend preview"):
            admission.confirm(request.as_dict())

    def test_a_pack_is_chosen_by_name_and_digest_never_by_path(self):
        base = {"targets": [{"kind": "draft", "ref": "a"}, {"kind": "draft", "ref": "b"}],
                "model": "m", "repetitions": 1, "tasks": ["short-one"], "max_budget_usd": "1",
                "spend_cap_usd": "2", "pre_registration": None}
        select = lambda name, digest: packs.select(self.source, name, digest)  # noqa: E731
        with self.assertRaisesRegex(replay.ReplayError, "only name and digest"):
            replay.resolve_request(dict(base, pack={"name": "test-pack", "digest": "0" * 64,
                                                    "source": "/tmp"}), target, select)
        with self.assertRaisesRegex(ValueError, "not available at that digest"):
            replay.resolve_request(dict(base, pack={"name": "test-pack", "digest": "0" * 64}),
                                   target, select)


class PackRowIdentityTests(unittest.TestCase):
    PACK = {"name": "evals", "version": "1.0.0", "commit": "e" * 40, "digest": "f" * 64,
            "source": "/packs/evals"}

    def row(self, digest):
        return {"task": "short-one", "arm": "harness", "rep": 1, "harness_sha": REVISION,
                "tag": "v1.0.0", "model": "m", "passed": True, "error": False, "cost_usd": 0.5,
                **replay_pack.identity(dict(self.PACK, digest=digest))}

    def test_the_pack_digest_reaches_the_indexed_row_under_its_stable_id(self):
        relative = "benchmarks/x/results.jsonl"
        first = run_store._benchmark_result(relative, 1, self.row("f" * 64))
        legacy = run_store._stable_id("benchmark-result", relative, run_store._digest(
            {"task": "short-one", "arm": "harness", "rep": 1, "harness_sha": REVISION, "tag": "v1.0.0"}))
        self.assertEqual(first["run_id"], legacy)
        self.assertEqual(first["evaluation"]["pack"]["pack_digest"], "f" * 64)
        self.assertEqual(first["raw"]["pack_digest"], "f" * 64)

    def test_a_row_without_the_pinned_pack_is_refused(self):
        released = replay.ReplayTarget.parse(target("release", "v1.0.0"))
        row = dict(self.row("f" * 64), harness_sha=released.revision, tag=released.execution_ref)
        replay._verify_target_rows(released, [row], self.PACK)
        with self.assertRaisesRegex(replay.ReplayError, "pinned evaluator pack"):
            replay._verify_target_rows(released, [dict(row, pack_digest="0" * 64)], self.PACK)


if __name__ == "__main__":
    unittest.main()

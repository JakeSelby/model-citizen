"""The evaluator pack (#796): read from a pinned commit outside this repository, validated, turned
into fresh workspaces, and checked for contamination against a harness commit. No model is called
and no container is started; every pack here is a small one built in a temporary directory."""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_harness import REPO

sys.path.insert(0, str(REPO / "scripts"))
SPEC = importlib.util.spec_from_file_location("replay_pack", REPO / "scripts" / "replay_pack.py")
PACK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACK)

# A canary for test packs only. It is not the published pack's, which never enters this repository.
CANARY = "TEST-PACK-CANARY 00000000-0000-4000-8000-000000000796"
CHECK = "# %s\ndef check(root):\n    return [] if (root / 'answer.txt').is_file() else ['no answer']\n" % CANARY
SOLUTION = ("# %s\ndef solve(root):\n    (root / 'answer.txt').write_text('42\\n', encoding='utf-8')\n"
            % CANARY)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@invalid",
                           "-c", "commit.gpgsign=false"] + list(args), check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode().strip()


def task_spec(task_id, long=False, calls=2, **over):
    spec = {"id": task_id, "workspace": "app", "source": "written for a test", "max_turns": 5,
            "long": long, "expected_absorbed_calls": calls, "prompt": ["Write answer.txt."]}
    spec.update(over)
    return spec


def make_pack(root, tasks=None, sets=None, check=CHECK, solution=SOLUTION, workspace_extra=None, **doc):
    """A committed pack with one workspace, `app`, and the given task specs; returns its root."""
    root = Path(root)
    (root / "workspaces" / "app" / "tests").mkdir(parents=True)
    (root / "workspaces" / "app" / "tests" / "test_app.py").write_text(
        "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n", encoding="utf-8")
    (root / "workspaces" / "app" / "README.md").write_text("an app\n", encoding="utf-8")
    for name, text in (workspace_extra or {}).items():
        (root / "workspaces" / "app" / name).write_text(text, encoding="utf-8")
    tasks = tasks if tasks is not None else [task_spec("short-one"), task_spec("long-one", True, 12)]
    for spec in tasks:
        folder = root / "tasks" / spec["id"]
        folder.mkdir(parents=True)
        (folder / "task.json").write_text(json.dumps(spec), encoding="utf-8")
        (folder / "check.py").write_text(check, encoding="utf-8")
        (folder / "solution.py").write_text(solution, encoding="utf-8")
    document = {"schema_version": 1, "name": "test-pack", "version": "1.0.0", "canary": CANARY,
                "break_even_calls": 7.6,
                "workspaces": {"app": {"gate": [["python3", "-m", "unittest", "discover", "-s", "tests"]]}},
                "sets": sets or {"production": {"tasks": [t["id"] for t in tasks]}}}
    document.update(doc)
    (root / "pack.json").write_text(json.dumps(document), encoding="utf-8")
    git(root.parent, "init", "-q", str(root))
    git(root, "add", "-A")
    git(root, "commit", "-qm", "feat: pack")
    return root


def harness_checkout(root, files=None):
    """A stand-in for a harness commit's checkout with history: one commit per `files` entry."""
    root = Path(root)
    root.mkdir(parents=True)
    (root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    git(root.parent, "init", "-q", str(root))
    git(root, "add", "-A")
    git(root, "commit", "-qm", "chore: one")
    for name, text in (files or {}).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "chore: add %s" % name)
    return root


class OpenPackTests(unittest.TestCase):
    def test_a_pack_is_read_from_its_commit_and_names_its_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = make_pack(Path(tmp) / "pack")
            (source / "pack.json").write_text("not json, and not committed", encoding="utf-8")
            pack = PACK.open_pack(source, harness_root=Path(tmp) / "harness")
            try:
                self.assertEqual(pack["version"], "1.0.0")
                self.assertEqual(pack["commit"], git(source, "rev-parse", "HEAD"))
                self.assertEqual(len(pack["digest"]), 64)
                self.assertEqual(PACK.identity(pack), {"pack": "test-pack", "pack_version": "1.0.0",
                                                       "pack_commit": pack["commit"], "pack_digest": pack["digest"]})
                again = PACK.open_pack(source, pack["commit"], pack["digest"], Path(tmp) / "harness")
                self.assertEqual(again["digest"], pack["digest"])
                PACK.close_pack(again)
            finally:
                PACK.close_pack(pack)
            self.assertFalse(Path(pack["root"]).exists())

    def test_another_digest_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = make_pack(Path(tmp) / "pack")
            with self.assertRaisesRegex(SystemExit, "the pack changed"):
                PACK.open_pack(source, expect_digest="0" * 64, harness_root=Path(tmp) / "harness")

    def test_a_changed_file_changes_the_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = make_pack(Path(tmp) / "pack")
            first = PACK.open_pack(source, harness_root=Path(tmp) / "harness")
            PACK.close_pack(first)
            (source / "tasks" / "short-one" / "check.py").write_text(CHECK + "# changed\n", encoding="utf-8")
            git(source, "commit", "-qam", "fix: check")
            second = PACK.open_pack(source, harness_root=Path(tmp) / "harness")
            PACK.close_pack(second)
            self.assertNotEqual(first["digest"], second["digest"])

    def test_a_pack_inside_the_harness_checkout_or_containing_it_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = make_pack(Path(tmp) / "pack")
            with self.assertRaisesRegex(SystemExit, "overlaps the harness checkout"):
                PACK.open_pack(source, harness_root=source / "inner")
            with self.assertRaisesRegex(SystemExit, "overlaps the harness checkout"):
                PACK.open_pack(source, harness_root=Path(tmp))

    def test_a_directory_that_is_not_a_repository_and_a_bad_ref_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "plain"
            plain.mkdir()
            with self.assertRaisesRegex(SystemExit, "not a git repository"):
                PACK.open_pack(plain, harness_root=Path(tmp) / "harness")
            source = make_pack(Path(tmp) / "pack")
            with self.assertRaisesRegex(SystemExit, "does not resolve"):
                PACK.open_pack(source, "v9", harness_root=Path(tmp) / "harness")

    def test_a_malformed_document_names_each_problem(self):
        errors = PACK.document_errors({"schema_version": 2, "name": "", "canary": "short",
                                       "break_even_calls": True, "workspaces": {"app": {"gate": "make"}},
                                       "sets": {"odd": {"tasks": ["a", "a"]},
                                                "micro": {"tasks": ["b"]}}})
        joined = "\n".join(errors)
        for fragment in ("schema_version", "name is not", "version is not", "canary is too short",
                         "break_even_calls", "gate of argv lists", "set odd has no list of unique",
                         "set odd names no tier", "set micro is a micro set and names no model"):
            self.assertIn(fragment, joined)


class LoadSetTests(unittest.TestCase):
    def open(self, tmp, **kwargs):
        pack = PACK.open_pack(make_pack(Path(tmp) / "pack", **kwargs), harness_root=Path(tmp) / "harness")
        self.addCleanup(PACK.close_pack, pack)
        return pack

    def test_tasks_become_runner_tasks_that_never_name_this_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks, manifest = PACK.load_set(self.open(tmp), "production", "production")
            self.assertEqual([t["id"] for t in tasks], ["short-one", "long-one"])
            self.assertEqual([t["long"] for t in tasks], [False, True])
            self.assertEqual({t["kind"] for t in tasks}, {"pack"})
            self.assertEqual({(t["parent_sha"], t["good_sha"]) for t in tasks}, {(None, None)})
            self.assertEqual(tasks[0]["pack"]["gate"], [["python3", "-m", "unittest", "discover", "-s", "tests"]])
            self.assertEqual((manifest["tier"], manifest["set"]), ("production", "production"))

    def test_a_named_set_carries_its_tier_and_model(self):
        sets = {"production": {"tasks": ["short-one"]},
                "nudge": {"tier": "micro", "model": "claude-small", "tasks": ["long-one"]}}
        tasks = [task_spec("short-one"), task_spec("long-one", True, 12, mechanism={"id": "delegation"})]
        with tempfile.TemporaryDirectory() as tmp:
            pack = self.open(tmp, tasks=tasks, sets=sets)
            loaded, manifest = PACK.load_set(pack, "nudge", "micro")
            self.assertEqual(([t["id"] for t in loaded], manifest["model"]), (["long-one"], "claude-small"))
            self.assertEqual(loaded[0]["mechanism"], {"id": "delegation"})
            with self.assertRaisesRegex(SystemExit, "is a micro set, not production; pass --tier micro"):
                PACK.load_set(pack, "nudge", "production")
            with self.assertRaisesRegex(SystemExit, "has no set absent; it has nudge, production"):
                PACK.load_set(pack, "absent", "production")

    def test_long_must_agree_with_the_expected_absorbed_calls(self):
        """FR-34's top figure is the line: a long task above it, a short one at or below it."""
        tasks = [task_spec("claims-long", True, 7.6), task_spec("claims-short", False, 8)]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as caught:
                PACK.load_set(self.open(tmp, tasks=tasks), "production", "production")
        self.assertIn("'claims-long': long is true but expected_absorbed_calls 7.6 is at or below", str(caught.exception))
        self.assertIn("'claims-short': long is false but expected_absorbed_calls 8 is above", str(caught.exception))

    def test_a_check_or_solution_without_the_canary_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = self.open(tmp, solution="def solve(root):\n    pass\n")
            with self.assertRaisesRegex(SystemExit, "solution.py does not carry the pack's canary"):
                PACK.load_set(pack, "production", "production")

    def test_a_workspace_that_carries_the_canary_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = self.open(tmp, workspace_extra={"leak.py": "# " + CANARY + "\n"})
            with self.assertRaisesRegex(SystemExit, "its workspace carries the canary"):
                PACK.load_set(pack, "production", "production")

    def test_a_malformed_task_names_each_problem(self):
        bad = task_spec("bad", max_turns=0, prompt=[], workspace="nowhere", long="yes")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as caught:
                PACK.load_set(self.open(tmp, tasks=[bad]), "production", "production")
        for fragment in ("names no declared workspace", "has no prompt", "max_turns", "long must be"):
            self.assertIn(fragment, str(caught.exception))


class WorkspaceTests(unittest.TestCase):
    def test_a_workspace_is_a_fresh_one_commit_repository_without_the_check_or_solution(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = PACK.open_pack(make_pack(Path(tmp) / "pack"), harness_root=Path(tmp) / "harness")
            self.addCleanup(PACK.close_pack, pack)
            task = PACK.load_set(pack, "production", "production")[0][0]
            first = PACK.materialize(task, Path(tmp) / "one" / "repo")
            second = PACK.materialize(task, Path(tmp) / "two" / "repo")
            self.assertEqual(git(first, "rev-list", "--count", "HEAD"), "1")
            self.assertEqual(git(first, "rev-parse", "HEAD"), git(second, "rev-parse", "HEAD"))
            self.assertEqual(git(first, "symbolic-ref", "--short", "HEAD"), "main")
            names = sorted(p.relative_to(first).as_posix() for p in first.rglob("*")
                           if p.is_file() and ".git" not in p.parts)
            self.assertEqual(names, ["README.md", "tests/test_app.py"])
            for path in first.rglob("*"):
                if path.is_file():
                    self.assertNotIn(CANARY.encode(), path.read_bytes())
            self.assertIn("answer.txt", PACK.check_source(task))
            PACK.apply_solution(task, first)
            self.assertEqual((first / "answer.txt").read_text(encoding="utf-8"), "42\n")

    def test_the_preflight_runs_the_workspace_gate(self):
        task = {"pack": {"gate": [["python3", "-m", "unittest", "discover", "-s", "tests"]]}}
        self.assertEqual(PACK.preflight_prompt(task),
                         "Run exactly this and reply with its output: `python3 -m unittest discover -s tests`")


class ContaminationTests(unittest.TestCase):
    def task(self, tmp):
        pack = PACK.open_pack(make_pack(Path(tmp) / "pack"), harness_root=Path(tmp) / "harness")
        self.addCleanup(PACK.close_pack, pack)
        return PACK.load_set(pack, "production", "production")[0][0]

    def test_a_checkout_without_the_answer_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = self.task(tmp)
            checkout = harness_checkout(Path(tmp) / "harness", {"docs/pack.md": "TEST-PACK-CANARY is named here\n"})
            self.assertEqual(PACK.contamination_errors(task, checkout), [])

    def test_the_exact_bytes_of_a_check_anywhere_in_history_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = self.task(tmp)
            checkout = harness_checkout(Path(tmp) / "harness", {"copied/check.py": CHECK.replace(CANARY, "x")})
            self.assertEqual(PACK.contamination_errors(task, checkout), [])  # different bytes, no canary
            checkout = harness_checkout(Path(tmp) / "harness-two", {"copied/solution.py": SOLUTION})
            git(checkout, "rm", "-q", "copied/solution.py")
            git(checkout, "commit", "-qm", "chore: remove it again")
            errors = PACK.contamination_errors(task, checkout)
            self.assertIn("short-one: the installed checkout's history holds the exact bytes of its solution.py",
                          errors)
            self.assertIn("history carries the pack's canary", errors[-1])

    def test_the_canary_in_the_tree_is_refused_even_in_reworded_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = self.task(tmp)
            checkout = harness_checkout(Path(tmp) / "harness", {"notes/answer.md": "reworded\n" + CANARY + "\n"})
            errors = PACK.contamination_errors(task, checkout)
            self.assertEqual(errors[0], "short-one: the installed checkout carries the pack's canary in notes/answer.md")

    def test_a_pack_inside_the_checkout_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = self.task(tmp)
            checkout = harness_checkout(Path(tmp) / "harness")
            inside = dict(task, pack=dict(task["pack"], source=str(checkout / "evals")))
            self.assertIn("short-one: the pack sits inside the installed checkout",
                          PACK.contamination_errors(inside, checkout))

    def test_git_blob_ids_match_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "f"
            path.write_bytes(b"bytes\n")
            expected = subprocess.check_output(["git", "hash-object", str(path)]).decode().strip()
        self.assertEqual(PACK.git_blob_id(b"bytes\n"), expected)


if __name__ == "__main__":
    unittest.main()

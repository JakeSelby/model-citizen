# SPDX-License-Identifier: MIT
"""Every hook, run on every recorded call under every stance variant, answers as committed.

The matrix, its corpus and how to rewrite it are described in
`tests/fixtures/hook-calls/hook_matrix.py`. These tests hold four things: the committed matrix
is what the hooks answer now, it covers every hook id and every selectable variant, a variant
that resolves differently fails exactly the cell it moves and names it, and the shortcut that
skips a hook the dispatcher never asked about gives the answer a full run gives.

Run: python3 -m unittest discover tests
"""
import importlib.util
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "hook-calls"


def _load():
    spec = importlib.util.spec_from_file_location("hook_matrix_under_test", str(FIXTURE / "hook_matrix.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HM = _load()
EVENTS = ("PreToolUse", "PostToolUse", "Stop")


def committed():
    return json.loads(HM.MATRIX.read_text(encoding="utf-8"))


class HookMatrixTests(unittest.TestCase):
    def test_hooks_answer_as_the_committed_matrix(self):
        moved = HM.differences(HM.expand(committed()), HM.expand(HM.compact(HM.compute_parallel())))
        self.assertEqual(moved, [], "\n".join(moved) + "\nIf the change is intended, run "
                         "`python3 tests/fixtures/hook-calls/hook_matrix.py --write` and review the diff.")

    def test_matrix_covers_every_hook_variant_and_call(self):
        matrix = committed()
        from harness_core import catalog
        self.assertEqual(matrix["rows"], list(catalog.HOOK_IDS) + [HM.DISPATCHER])
        self.assertEqual(matrix["calls"], sorted(HM.calls()))
        keys = matrix["variants"]
        defaults = HM.defaults()
        for topic in sorted(p for p in HM.STANCES.iterdir() if p.is_dir()):
            self.assertIn(topic.name, defaults, topic.name + " has no default the hooks resolve")
            for path in topic.glob("*.md"):
                if path.stem != defaults[topic.name]:
                    self.assertIn(topic.name + "=" + path.stem, keys)
        self.assertEqual(keys[0], HM.BASE)
        self.assertEqual(len(HM.expand(matrix)), len(matrix["rows"]) * len(matrix["calls"]) * len(keys))

    def test_a_variant_that_resolves_differently_fails_exactly_its_cell(self):
        # `autonomy=ask` resolving as `execute` stands for an edit to that variant that changes
        # what it lets through: a commit is asked about under `ask` and passes under `execute`.
        calls = {"pre-bash-commit": HM.calls()["pre-bash-commit"]}
        flipped = HM.flatten(HM.compute([("autonomy=ask", {"autonomy": "execute"})], calls))
        expected = dict((cell, answer) for cell, answer in HM.expand(committed()).items() if cell in flipped)
        moved = HM.differences(expected, flipped)
        self.assertEqual(len(moved), 1, moved)
        self.assertEqual(moved[0], "hook grade-bash, call pre-bash-commit, variant autonomy=ask: "
                                   "expected ask, got as-dispatcher")

    def test_one_changed_cell_is_reported_once_with_its_names(self):
        matrix = committed()
        changed = json.loads(json.dumps(matrix))
        changed["cells"]["validate-plan-card"]["post-write-plan-bad"]["plan-ceremony=light"] = "context"
        moved = HM.differences(HM.expand(matrix), HM.expand(changed))
        self.assertEqual(moved, ["hook validate-plan-card, call post-write-plan-bad, variant "
                                 "plan-ceremony=light: expected as-dispatcher, got context"])

    def test_skipping_hooks_never_asked_about_matches_running_them(self):
        corpus = HM.calls()
        sample = dict((name, corpus[name]) for name in (
            "pre-bash-plan-grep", "pre-agent-unbounded-brief", "post-write-plan-bad", "pre-write-source"))
        envs = [env for env in HM.variants() if env[0] in (HM.BASE, "autonomy=ask", "delegation=off")]
        self.assertEqual(HM.compute(envs, sample, exhaustive=True), HM.compute(envs, sample))

    def test_corpus_is_synthetic_and_redacted(self):
        home = re.compile(r"/Users/|/home/|[A-Za-z]:\\\\|/root/|/private/var/|/var/folders/")
        for name, document in HM.calls().items():
            self.assertEqual(sorted(set(document) - {"files", "git"}), ["about", "payload"], name)
            payload = document["payload"]
            self.assertIn(payload["hook_event_name"], EVENTS, name)
            self.assertIn(payload["cwd"], ("/workspace/example-repo", "{repo}"), name)
            self.assertIsNone(home.search(json.dumps(document)), name)

    def test_repository_copy_survives_a_vanishing_file_and_skips_locks(self):
        # Git's background maintenance creates and removes `maintenance.lock` inside the template
        # while it is copied; a file removed between listing and copying must not fail the run.
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "src", Path(tmp) / "dst"
            (src / "objects").mkdir(parents=True)
            for name in ("kept", "gone", "maintenance.lock"):
                (src / "objects" / name).write_text(name)
            real = shutil.copyfile

            def vanishing(source, target, **kwargs):
                if os.path.basename(source) == "gone":
                    os.remove(source)
                return real(source, target, **kwargs)

            with mock.patch.object(shutil, "copyfile", vanishing):
                HM._copy_repo(src, dst)
            self.assertEqual(sorted(p.name for p in (dst / "objects").iterdir()), ["kept"])

    def test_template_git_calls_disable_background_maintenance(self):
        self.assertIn("maintenance.auto=false", HM.QUIET_GIT)
        self.assertIn("gc.auto=0", HM.QUIET_GIT)

    def test_decision_shape(self):
        self.assertEqual(HM.decision({}), "none")
        self.assertEqual(HM.decision({"hookSpecificOutput": {"permissionDecision": "allow",
                                                             "updatedInput": {"command": "x"}}}),
                         "allow+rewrite")
        self.assertEqual(HM.decision({"decision": "block", "reason": "gate failed"}), "block")
        self.assertEqual(HM.decision({"systemMessage": "note", "hookSpecificOutput": {
            "additionalContext": "flagged"}}), "context+message")


if __name__ == "__main__":
    unittest.main()

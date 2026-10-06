# SPDX-License-Identifier: MIT
"""Every hook, run on every recorded call under every stance variant, answers as committed.

The matrix, its corpus and how to rewrite it are described in
`tests/fixtures/hook-calls/hook_matrix.py`. These tests hold four things: the committed matrix
is what the hooks answer now, it covers every hook id and every selectable variant, a variant
that resolves differently fails exactly the cell it moves and names it, and every row is actually executed even when the event does not query that hook.

Run: python3 -m unittest discover tests
"""
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import isolation  # noqa: F401 -- keeps git maintenance out of temporary repositories
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

    def test_a_variant_behavior_change_fails_exactly_its_cell_in_the_full_matrix(self):
        # Prose is not executable policy. Mutate the hook's behavior for one variant and call,
        # then run the complete corpus; no rows or variant environments are filtered out.
        original = HM.lifecycle.load
        command = HM.calls()["pre-bash-commit"]["payload"]["tool_input"]["command"]

        def changed_load(name):
            module = original(name)
            if name == "grade-bash" and os.environ.get("HARNESS_STANCE_AUTONOMY") == "ask":
                grade = module.grade_text

                def changed_grade(text, *args, **kwargs):
                    result = grade(text, *args, **kwargs)
                    if text == command:
                        module.THRESHOLDS = dict(module.THRESHOLDS, ask=3)
                    return result

                module.grade_text = changed_grade
            return module

        with mock.patch.object(HM.lifecycle, "load", side_effect=changed_load):
            flipped = HM.flatten(HM.compute())
        moved = HM.differences(HM.expand(committed()), flipped)
        self.assertEqual(moved, ["hook grade-bash, call pre-bash-commit, variant autonomy=ask: "
                                 "expected ask, got as-dispatcher"])

    def test_one_changed_cell_is_reported_once_with_its_names(self):
        matrix = committed()
        changed = json.loads(json.dumps(matrix))
        changed["cells"]["validate-plan-card"]["post-write-plan-bad"]["plan-ceremony=light"] = "context"
        moved = HM.differences(HM.expand(matrix), HM.expand(changed))
        self.assertEqual(moved, ["hook validate-plan-card, call post-write-plan-bad, variant "
                                 "plan-ceremony=light: expected as-dispatcher, got context"])

    def test_every_row_is_executed_even_when_dispatcher_never_queries_it(self):
        with mock.patch.object(HM, "run_one", return_value="none") as run:
            HM.compute()
        self.assertEqual(run.call_count, len(HM.rows()) * len(HM.calls()) * len(HM.variants()))
        for row in HM.rows():
            self.assertEqual(sum(call.args[0] == row for call in run.call_args_list),
                             len(HM.calls()) * len(HM.variants()))

    def test_corpus_is_redacted_and_recorded_calls_have_provenance(self):
        home = re.compile(r"/Users/|/home/|[A-Za-z]:\\\\|/root/|/private/var/|/var/folders/")
        recorded = [d for d in HM.calls().values() if "provenance" in d]
        self.assertEqual({d["payload"]["hook_event_name"] for d in recorded
                          if d["provenance"]["kind"] == "recorded-tool-data"},
                         {"PreToolUse", "PostToolUse"})
        for document in recorded:
            self.assertRegex(document["provenance"]["sha256"], r"^[0-9a-f]{64}$")
            self.assertIsInstance(document["provenance"]["record_index"], int)
            self.assertTrue(document["provenance"]["normalization"])
            provenance = document["provenance"]
            event = document["payload"]["hook_event_name"]
            if event == "Stop":
                self.assertEqual(provenance["kind"], "recorded-native-stop")
                continue
            source = (FIXTURE / provenance["source_excerpt"]).resolve()
            self.assertTrue(source.is_relative_to(FIXTURE.resolve()))
            content = source.read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(), provenance["source_excerpt_sha256"])
            record = json.loads(content)["records"][provenance["excerpt_index"]]
            if event == "PreToolUse":
                block = record["message"]["content"][0]
                self.assertEqual(document["payload"]["tool_name"], block["name"])
                self.assertEqual(document["payload"]["tool_input"], block["input"])
            else:
                self.assertEqual(document["payload"]["tool_response"]["stdout"],
                                 record["message"]["content"][0]["content"])
        for name, document in HM.calls().items():
            self.assertEqual(sorted(set(document) - {"files", "git", "provenance"}), ["about", "payload"], name)
            payload = document["payload"]
            self.assertIn(payload["hook_event_name"], EVENTS, name)
            self.assertIn(payload["cwd"], ("/workspace/example-repo", "{repo}"), name)
            self.assertIsNone(home.search(json.dumps(document)), name)

    def test_stop_call_is_a_recorded_native_payload_with_its_source(self):
        stops = {name: d for name, d in HM.calls().items()
                 if "provenance" in d and d["payload"]["hook_event_name"] == "Stop"}
        self.assertEqual(list(stops), ["recorded-stop-hook-inventory"])
        document = stops["recorded-stop-hook-inventory"]
        provenance, payload = document["provenance"], document["payload"]
        self.assertEqual(provenance["kind"], "recorded-native-stop")
        self.assertEqual(provenance["run"], "stop-capture-2026-10-05")
        self.assertEqual(provenance["image"], "model-citizen-arm-bare:a904eb2f983d")
        self.assertEqual(provenance["claude_code_version"], "2.1.280")
        self.assertRegex(provenance["sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn("Synthetic", document["about"])
        self.assertEqual(provenance["native_keys"], sorted(payload))
        self.assertEqual((payload["session_id"], payload["prompt_id"], payload["last_assistant_message"]),
                         ("s-recorded", "p-recorded", "<placeholder>"))
        self.assertIs(payload["stop_hook_active"], False)

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

    def test_template_ignores_an_inherited_repository_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp) / "other"
            subprocess.run(["git", "init", "-q", str(other)], check=True, env=HM.git_env())
            before = sorted(str(p.relative_to(other)) for p in other.rglob("*"))
            saved = list(HM._TEMPLATE)
            HM._TEMPLATE[:] = []
            try:
                with mock.patch.dict(os.environ, {"GIT_DIR": str(other / ".git"),
                                                  "GIT_WORK_TREE": str(other),
                                                  "GIT_INDEX_FILE": str(other / ".git" / "index")}):
                    template = HM._template()
            finally:
                HM._TEMPLATE[:] = saved
            self.assertEqual(sorted(str(p.relative_to(other)) for p in other.rglob("*")), before)
            head = subprocess.run(["git", "--git-dir", str(template), "rev-parse", "--verify", "-q", "HEAD"],
                                  capture_output=True, text=True, env=HM.git_env())
            self.assertEqual(head.returncode, 0, head.stderr)

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

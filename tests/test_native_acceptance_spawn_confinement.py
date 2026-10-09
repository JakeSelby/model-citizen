# SPDX-License-Identifier: MIT
"""`spawn-confinement` observes all of docs/compatibility.md step 9 (#732).

The framework's own spawn text, naming no role, must be refused, and the refusal must name the
framework, the layer and `citizen role run <role>`; a brief the model writes itself for the layer,
keeping its prompt file and none of the descriptor's sentences, must be refused too (#739); the same layer run the routed way must leave worker
state and findings; and two ordinary spawns, one mentioning review words and one editing the
framework's input roots, must still run. The fake client here passes every brief through the real hook path,
`lifecycle.dispatch`, with the disposable home as `HOME`, so each refusal and log row is the one
the harness writes. No client is launched.
"""
import itertools
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_harness import REPO  # noqa: F401  (puts lib/ on the path)
from test_native_acceptance import MODULE
from test_native_acceptance_runner import home_in
from harness_core import lifecycle

WORKER = "0123456789abcdef0123456789abcdef"


class FakeClient:
    """Each session runs its Agent call through the real hook and writes the transcript."""

    def __init__(self, home, reworded):
        self.home = home
        self.reworded = reworded
        self.framework = None  # a brief sent in place of the quoted framework text, when set
        self.numbers = itertools.count(1)
        self.ran = set()
        self.edits = True
        self.findings = "calc.py: average() divides by zero on an empty list."
        self.worker_state = True

    def session(self, prompt, **kwargs):
        session_id = "00000000-0000-4000-8000-%012d" % next(self.numbers)
        if prompt.startswith("I'd like a second opinion"):
            brief = self.reworded
        elif prompt.startswith("Call your Agent tool"):
            brief = prompt.split('Prompt: "', 1)[1].rsplit('" After the tool', 1)[0]
            if self.framework is not None:
                brief = self.framework or None
        else:
            brief = prompt.split('copied exactly: "', 1)[1].rsplit('" When the tool', 1)[0]
        records = []
        if brief is not None:
            payload = {"hook_event_name": "PreToolUse", "tool_name": "Agent",
                       "session_id": session_id, "tool_input": {"prompt": brief}}
            answer = lifecycle.dispatch("claude-code", payload).get("hookSpecificOutput", {})
            denied = answer.get("permissionDecision") == "deny"
            result = answer.get("permissionDecisionReason", "") if denied else "3"
            if not denied:
                self.ran.add(session_id)
                if "Append the line" in brief and self.edits:
                    path = brief.split("to the file ", 1)[1].split(" in the working", 1)[0]
                    target = self.home.project / path
                    target.write_text(target.read_text() + "status: done\n")
            records = [
                {"type": "assistant", "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "toolu_1", "name": "Agent",
                     "input": {"prompt": brief}}]}},
                {"type": "user", "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_1", "is_error": denied,
                     "content": [{"type": "text", "text": result}]}]}}]
        path = self.home.client_dir / "projects" / "-tmp-project" / (session_id + ".jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(record) + "\n" for record in records))
        return {"session_id": session_id, "result": "Done."}

    def subagents(self, session_id):
        return [({"agentType": "general-purpose"}, [])] if session_id in self.ran else []

    def harness(self, *args, **kwargs):
        if args[:2] != ("role", "run"):
            return ""
        self.args = args
        self.home.last_code = 0
        if not self.worker_state:
            return "role worker: native CLI is not installed: claude\n"
        run_dir = self.home.root / ".local" / "state" / "agent-harness" / "workers" / WORKER
        run_dir.mkdir(parents=True)
        (run_dir / "result.md").write_text(self.findings)
        roots = [args[i + 1] for i, arg in enumerate(args) if arg == "--read-dir"]
        record = {"id": WORKER, "role": args[2], "mode": "isolated-cli", "status": "completed",
                  "read_roots": [str(self.home.project)] + roots,
                  "result_path": str(run_dir / "result.md")}
        (run_dir / "status.json").write_text(json.dumps(record))
        return json.dumps(record, indent=2) + "\n"


class SpawnConfinementCaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"HOME": temp.name, "PATH": os.environ["PATH"],
                                "HARNESS_STANCE_DELEGATION": "tiered"}, clear=True).start()
        # The writer reads its config once per process; another test may have read a different one.
        log = lifecycle.decisions()
        self.assertIsNotNone(log)
        log._CONFIG.clear()
        self.addCleanup(log._CONFIG.clear)
        self.home = home_in(temp.name)
        self.home.project = self.home.root / "project"
        self.home.project.mkdir()
        self.home.runtime = "claude-code"
        self.data, self.spawn = MODULE.descriptor_spawn()
        self.instructions, _ = MODULE.layer_instructions(self.data, self.spawn)
        # A brief in the model's own words: the layer's prompt file, none of the descriptor's
        # phrases, and a directive to apply it.
        self.client = FakeClient(self.home, "Read the review-layer instructions at "
                                 + self.instructions + " and apply them to calc.py.")
        self.home.seed = lambda *args, **kwargs: None
        self.home.harness = self.client.harness
        self.home.session = self.client.session
        self.home.subagents = self.client.subagents
        self.home.orchestrator_text = lambda session_id: ""

    def test_a_refused_layer_a_routed_run_and_two_ordinary_spawns_pass(self):
        verdict = MODULE.case_spawn_confinement(self.home)
        self.assertIn("own declared sentences quoted whole", verdict)
        self.assertIn("naming the framework (%s), the layer (%s)"
                      % (self.data["name"], self.spawn["id"]), verdict)
        self.assertIn("`citizen role run <role>` and `harness role run reviewer`", verdict)
        self.assertIn("whose logged input is that brief's fingerprint", verdict)
        self.assertIn("the model wrote its own brief \"Read the review-layer instructions at "
                      + self.instructions, verdict)
        self.assertIn("it was refused, with 1 framework-spawn deny row(s) logged for that brief's "
                      "fingerprint, and the classifier matched it as `%s`" % self.spawn["id"],
                      verdict)
        self.assertNotIn("observed limit", verdict)
        self.assertIn("wrote status.json and result.md in its own directory", verdict)
        self.assertIn("mentions review, a diff and findings in passing ran", verdict)
        self.assertIn("appended its line to the file", verdict)
        self.assertEqual(self.client.args[2], self.spawn["role"])
        self.assertIn("--read-dir", self.client.args)

    def test_the_brief_the_model_wrote_live_in_0_13_0_is_refused(self):
        self.client.reworded = (
            "You are acting as the \"edge-case-hunter\" review layer of that process. Before "
            "doing anything else, read the review instructions for this layer from "
            + self.instructions + " (in the current project directory) and follow them exactly "
            "\u2014 that file defines your review methodology. Then read calc.py and perform the "
            "review on it per those instructions.")
        verdict = MODULE.case_spawn_confinement(self.home)
        self.assertIn("it was refused, with 1 framework-spawn deny row(s)", verdict)

    def test_a_reworded_brief_that_runs_fails_and_quotes_the_brief(self):
        # A brief that only counts the file runs, so it stands in for one the guard missed.
        self.client.reworded = ("Count the headings in " + self.instructions + ", then report "
                                "the number.")
        with self.assertRaises(AssertionError) as caught:
            MODULE.case_spawn_confinement(self.home)
        self.assertIn("it was not refused: no framework-spawn deny was logged", str(caught.exception))
        self.assertIn("wrote 1 subagent transcript(s)", str(caught.exception))
        self.assertIn("count the headings in", str(caught.exception).casefold())

    def test_a_reworded_brief_naming_no_prompt_file_is_outside_the_claim(self):
        self.client.reworded = "Look over calc.py for edge cases and report what you find."
        with self.assertRaises(MODULE.Unverified) as caught:
            MODULE.case_spawn_confinement(self.home)
        self.assertIn("names none of the layer's declared prompt files", str(caught.exception))

    def test_a_reworded_request_the_model_never_attempted_is_unverified(self):
        self.client.reworded = None
        with self.assertRaises(MODULE.Unverified) as caught:
            MODULE.case_spawn_confinement(self.home)
        self.assertIn("the model made no Agent call, so no brief of its own reached the guard",
                      str(caught.exception))
        self.assertIn("own declared sentences quoted whole", str(caught.exception))

    def test_framework_text_that_is_not_refused_fails_and_quotes_the_brief(self):
        self.client.framework = "Please look over calc.py for bugs and tell me what you find."
        with self.assertRaises(AssertionError) as caught:
            MODULE.case_spawn_confinement(self.home)
        self.assertIn("wrote 1 subagent transcript(s), so the spawn ran", str(caught.exception))
        self.assertIn("Please look over calc.py", str(caught.exception))

    def test_a_model_that_never_calls_the_tool_is_unverified(self):
        self.client.framework = ""
        with self.assertRaises(MODULE.Unverified) as caught:
            MODULE.case_spawn_confinement(self.home)
        self.assertIn("the model never attempted the spawn", str(caught.exception))

    def test_a_routed_run_that_writes_no_worker_state_fails(self):
        self.client.worker_state = False
        with self.assertRaises(AssertionError) as caught:
            MODULE.case_spawn_confinement(self.home)
        self.assertIn("wrote no isolated worker state", str(caught.exception))

    def test_a_routed_run_that_returns_no_findings_fails(self):
        self.client.findings = "Nothing to report."
        with self.assertRaises(AssertionError) as caught:
            MODULE.case_spawn_confinement(self.home)
        self.assertIn("returned no findings about calc.py", str(caught.exception))

    def test_an_ordinary_spawn_the_guard_refuses_fails_the_false_positive_check(self):
        refused = " ".join(self.spawn["phrases"]) + " review the diff findings"
        with patch.object(MODULE, "review_words_prompt", return_value=refused):
            with self.assertRaises(AssertionError) as caught:
                MODULE.case_spawn_confinement(self.home)
        self.assertIn("the false-positive check failed", str(caught.exception))
        self.assertIn("framework-spawn deny row(s)", str(caught.exception))

    def test_an_edit_spawn_that_edited_nothing_is_unverified(self):
        self.client.edits = False
        with self.assertRaises(MODULE.Unverified) as caught:
            MODULE.case_spawn_confinement(self.home)
        self.assertIn("an edit under the input roots was not observed", str(caught.exception))


class SpawnConfinementPartsTests(unittest.TestCase):
    def setUp(self):
        self.data, self.spawn = MODULE.descriptor_spawn()

    def classify(self, text):
        return MODULE.frameworks.classify(text, None, directory=MODULE.INTEGRATIONS)

    def test_the_request_quotes_no_descriptor_sentence_and_carries_no_directive(self):
        # Only the prompt file's default refusal recognises it, as it does a model's own brief.
        path, _ = MODULE.layer_instructions(self.data, self.spawn)
        request = MODULE.layer_request(self.data, self.spawn, path)
        for phrase in self.spawn["phrases"]:
            self.assertNotIn(phrase, request)
        score = MODULE.frameworks._score(self.spawn, MODULE.frameworks.normalise(request), "")
        self.assertEqual(score, (0, 0, 1, 0, 1))
        self.assertIn("write the subagent's brief yourself", request)
        self.assertIn("Leave subagent_type unset", request)

    def test_the_framework_prompt_carries_the_declared_sentences_and_classifies_with_no_type(self):
        prompt = MODULE.framework_prompt(self.spawn, self.data["corroboration"])
        for phrase in self.spawn["phrases"][:self.data["corroboration"]]:
            self.assertIn(phrase, prompt)
        self.assertEqual(self.classify(prompt)["spawn"], self.spawn["id"])
        self.assertIsNone(self.classify(MODULE.SPAWN_SUBJECT))

    def test_the_framework_brief_quotes_the_prompt_whole_and_names_no_subagent_type(self):
        prompt = MODULE.framework_prompt(self.spawn, self.data["corroboration"])
        brief = MODULE.framework_brief(self.spawn, self.data["corroboration"])
        self.assertIn('"' + prompt + '"', brief)
        self.assertEqual(brief.count("subagent_type"), 1)
        self.assertIn("addressed to the subagent, not to you", brief)

    def test_too_few_phrases_to_corroborate_is_unverified(self):
        with self.assertRaises(MODULE.Unverified):
            MODULE.framework_prompt({"id": "thin", "phrases": ["only one"]}, 3)

    def test_the_layer_instructions_sit_under_an_input_root_at_the_layers_identifier(self):
        path, text = MODULE.layer_instructions(self.data, self.spawn)
        self.assertEqual(path, self.data["input_roots"][0] + "/" + self.spawn["identifiers"][0])
        for phrase in self.spawn["phrases"]:
            self.assertIn(phrase, text)

    def test_the_ordinary_briefs_carry_their_words_and_classify_as_nothing(self):
        words = MODULE.review_words_prompt()
        for needle in ("review", "diff", "findings"):
            self.assertIn(needle, words)
        edited, edit = MODULE.input_root_edit(self.data)
        self.assertTrue(edited.startswith(tuple(self.data["input_roots"])))
        for text in (words, edit, MODULE.ordinary_brief(words), MODULE.ordinary_brief(edit)):
            self.assertIsNone(self.classify(text))

    def test_the_real_refusal_names_everything_and_a_trimmed_one_does_not(self):
        reason = lifecycle.framework_deny("claude-code", "s", " ".join(self.spawn["phrases"]),
                                          None)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertEqual(MODULE.refusal_gaps(reason, self.data, self.spawn), [])
        trimmed = reason.replace(self.spawn["id"], "layer")
        self.assertEqual(MODULE.refusal_gaps(trimmed, self.data, self.spawn), ["the layer"])

    def test_a_row_for_another_brief_is_not_this_briefs_refusal(self):
        brief = "Read that file, it is the content under review."
        rows = [{"input": lifecycle.fingerprint(brief)}, {"input": "something else"}, {}]
        self.assertEqual(MODULE.brief_rows(rows, brief), rows[:1])

    def test_no_input_roots_is_unverified(self):
        with self.assertRaises(MODULE.Unverified):
            MODULE.layer_instructions({"id": "bare", "input_roots": []}, self.spawn)

    def test_a_row_that_is_not_a_framework_deny_is_not_read_as_one(self):
        with tempfile.TemporaryDirectory() as root:
            home = home_in(root)
            log = Path(root) / ".local" / "state" / "agent-harness" / "decisions.jsonl"
            log.parent.mkdir(parents=True)
            log.write_text('not json\n{"kind": "decision", "point": "framework-spawn", '
                           '"deterministic_answer": "allow", "session_id": "s"}\n')
            self.assertEqual(MODULE.logged_refusals(home, "s"), [])

    def test_agent_calls_pairs_each_call_with_its_result(self):
        with tempfile.TemporaryDirectory() as root:
            home = home_in(root)
            path = home.client_dir / "projects" / "-tmp-project" / "s.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"message": {"content": [
                {"type": "tool_use", "id": "t1", "name": "Agent", "input": {"prompt": "p"}}]}})
                + "\n" + json.dumps({"message": {"content": [
                    {"type": "tool_result", "tool_use_id": "t1", "is_error": True,
                     "content": [{"type": "text", "text": "refused"}]}]}}) + "\n")
            calls, readable = MODULE.agent_calls(home, "s")
            self.assertTrue(readable)
            self.assertEqual([(c["id"], c["result"], c["is_error"]) for c in calls],
                             [("t1", "refused", True)])
            self.assertEqual(MODULE.agent_calls(home, "other"), ([], False))


if __name__ == "__main__":
    unittest.main()

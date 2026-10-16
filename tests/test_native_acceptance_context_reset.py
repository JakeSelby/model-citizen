"""The context-reset acceptance case reads its verdict from the stream and the workspace.

No client runs: each stream under `fixtures/context-reset/` is synthetic (see its README), and the
workspace is given as the done files a run would have left.
"""
import json
import re
import tempfile
import unittest
from pathlib import Path

from test_native_acceptance import MODULE

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "context-reset"
CODES = dict((n, MODULE.ledger_code(n)) for n in range(1, MODULE.RESET_ITEMS + 1))


def events(name):
    return MODULE.stream_events((FIXTURES / (name + ".stream.jsonl")).read_text())


def assess(name, items=None, snapshots=None):
    stream = events(name)
    written = dict((n, CODES[n] + "\n") for n in (items or CODES))
    if snapshots is None:
        auto = [s for t, s in MODULE.compactions(stream) if t == "auto"]
        snapshots = min(len(auto), MODULE.SNAPSHOT_KEEP)
    return dict((check, (held, detail)) for check, held, detail
                in MODULE.assess_reset(stream, written, CODES, snapshots))


def failed(result):
    return sorted(name for name, (held, _) in result.items() if not held)


class AssessmentTests(unittest.TestCase):
    def test_a_clean_run_holds_every_assertion_separately(self):
        result = assess("passing")
        self.assertEqual(sorted(result), ["compaction", "continued", "ending", "items", "one-turn",
                                          "reseed", "snapshot"])
        self.assertEqual(failed(result), [])
        self.assertIn("6 automatic compaction(s) at 56538", result["compaction"][1])
        self.assertIn("6 of 6", result["continued"][1])

    def test_a_thrash_stop_fails_the_ending_the_unfinished_items_and_the_last_reset(self):
        result = assess("thrash", items=range(1, 7))
        self.assertEqual(failed(result), ["continued", "ending", "items"])
        self.assertIn(MODULE.THRASH, result["ending"][1])

    def test_a_refusal_fails_the_ending_alone(self):
        result = assess("refusal")
        self.assertEqual(failed(result), ["ending"])
        self.assertIn("refusal", result["ending"][1])

    def test_an_item_written_twice_fails_the_items_alone(self):
        result = assess("duplicate")
        self.assertEqual(failed(result), ["items"])
        self.assertIn("written more than once [9]", result["items"][1])

    def test_no_compaction_fails_compaction_continuation_snapshot_and_reseed(self):
        result = assess("no-compaction")
        self.assertEqual(failed(result), ["compaction", "continued", "reseed", "snapshot"])
        self.assertIn("0 automatic compaction(s)", result["compaction"][1])

    def test_a_reset_far_from_the_expected_point_fails_compaction(self):
        result = MODULE.assess_reset(events("passing"), dict((n, CODES[n]) for n in CODES),
                                     CODES, 5, expected=MODULE.expected_reset(pct=40))
        self.assertFalse(dict((c, h) for c, h, _ in result)["compaction"])

    def test_a_missing_snapshot_fails_the_snapshot_assertion(self):
        self.assertEqual(failed(assess("passing", snapshots=1)), ["snapshot"])

    def test_a_wrong_audit_code_counts_as_not_done(self):
        written = dict((n, CODES[n]) for n in CODES)
        written[4] = "AUDIT-04-000"
        result = dict((c, h) for c, h, _ in MODULE.assess_reset(events("passing"), written, CODES, 5))
        self.assertFalse(result["items"])

    def test_a_turn_ending_after_a_reset_breaks_the_turn_and_that_reset_continued(self):
        stream = events("passing")
        first = next(i for i, e in enumerate(stream) if MODULE.is_boundary(e))
        stream.insert(first + 1, {"type": "result", "subtype": "success", "result": "paused"})
        self.assertEqual(MODULE.one_turn(stream), "2 result events, not one")
        self.assertEqual(MODULE.continued(stream)[0], False)
        self.assertTrue(all(MODULE.continued(stream)[1:]))


class ReseedTests(unittest.TestCase):
    def test_the_reseed_is_read_from_the_compact_hook_response_before_each_boundary(self):
        self.assertEqual(MODULE.reseeded(events("passing")), [True] * 6)

    def test_a_summary_quoting_the_line_is_not_a_reseed(self):
        stream = events("passing")
        for event in stream:
            if MODULE.compact_hook_reseeds(event):
                event["output"] = event["stdout"] = json.dumps(
                    {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "other"}})
            if event.get("isSynthetic"):
                event["message"]["content"][0]["text"] += "\n" + MODULE.RESEED_LINE
        self.assertEqual(MODULE.reseeded(stream), [False] * 6)

    def test_a_startup_hook_carrying_the_line_does_not_count(self):
        hook = {"type": "system", "subtype": "hook_response", "hook_event": "SessionStart",
                "hook_name": "SessionStart:startup", "output": MODULE.RESEED_LINE}
        self.assertIsNone(MODULE.compact_hook_reseeds(hook))

    def test_a_response_just_after_its_boundary_also_counts(self):
        hook = {"type": "system", "subtype": "hook_response", "hook_event": "SessionStart",
                "hook_name": "SessionStart:compact", "output": MODULE.RESEED_LINE}
        boundary = {"type": "system", "subtype": "compact_boundary", "compact_metadata": {"trigger": "auto"}}
        self.assertEqual(MODULE.reseeded([boundary, hook, {"type": "assistant", "message": {"content": []}}]),
                         [True])
        late = {"type": "assistant", "message": {"content": []}}
        self.assertEqual(MODULE.reseeded([boundary, late, hook]), [False])


def written(command=None, name="Bash", **data):
    if command is not None:
        data["command"] = command
    stream = [{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": data}]}}]
    return MODULE.item_writes(stream)


class WriteTests(unittest.TestCase):
    def test_file_tools_count_on_their_own_path(self):
        for name, key in (("Write", "file_path"), ("Edit", "file_path"), ("MultiEdit", "file_path"),
                          ("NotebookEdit", "notebook_path")):
            with self.subTest(name):
                self.assertEqual(written(name=name, **{key: "/w/done/item-03.txt"}), {3: 1})

    def test_shell_writes_count(self):
        for command in ("echo AUDIT > done/item-03.txt", "echo AUDIT >> done/item-03.txt",
                        "echo AUDIT>done/item-03.txt", "printf x | tee done/item-03.txt",
                        "printf x | tee -a done/item-03.txt >/dev/null", "cp /tmp/x done/item-03.txt",
                        "mv /tmp/x done/item-03.txt", "sed -i 's/a/b/' done/item-03.txt",
                        "sed -i.bak -e 's/a/b/' done/item-03.txt",
                        "python3 -c \"open('done/item-03.txt', 'w').write('x')\"",
                        "cd /w && echo x 1> 'done/item-03.txt'"):
            with self.subTest(command):
                self.assertEqual(written(command), {3: 1})

    def test_reads_and_stderr_redirections_do_not_count(self):
        for command in ("cat done/item-03.txt 2>/dev/null", "cat done/item-03.txt", "ls done/item-03.txt",
                        "grep AUDIT done/item-03.txt >&2", "echo 'x > done/item-03.txt'",
                        "cp done/item-03.txt /tmp/backup", "test -f done/item-03.txt || echo missing 2>> err.log",
                        "python3 -c \"print(open('done/item-03.txt').read())\""):
            with self.subTest(command):
                self.assertEqual(written(command), {})

    def test_one_call_writing_two_items_counts_each_once(self):
        self.assertEqual(written("echo a > done/item-01.txt; echo b > done/item-02.txt; "
                                 "echo c >> done/item-02.txt"), {1: 1, 2: 1})


class FixtureTests(unittest.TestCase):
    def test_ledgers_are_plain_prose_of_about_four_thousand_tokens(self):
        text, code = MODULE.inventory_ledger(7)
        self.assertEqual((text, code), MODULE.inventory_ledger(7))
        self.assertIn("The audit code for this ledger is %s." % code, text)
        self.assertTrue(14000 <= len(text) <= 20000, len(text))  # four characters a token
        for line in text.splitlines():
            if line and not line.startswith("#"):
                self.assertTrue(line.endswith("."), line)
                self.assertGreaterEqual(len(line.split()), 8, line)

    def test_every_ledger_has_its_own_code(self):
        self.assertEqual(len(set(CODES.values())), MODULE.RESET_ITEMS)

    def test_the_workspace_holds_the_ledgers_and_checklist(self):
        with tempfile.TemporaryDirectory() as tmp:
            codes = MODULE.reset_workspace(Path(tmp))
            self.assertEqual(codes, CODES)
            ledgers = sorted(p.name for p in (Path(tmp) / "inventory").glob("*.md"))
            self.assertEqual(len(ledgers), MODULE.RESET_ITEMS)
            checklist = (Path(tmp) / "CHECKLIST.md").read_text()
            self.assertEqual(len(re.findall(r"^- \[ \] \d{2}:", checklist, re.M)), MODULE.RESET_ITEMS)

    def test_the_probe_variant_names_the_lowest_reset_point(self):
        self.assertEqual(MODULE.RESET_VARIANT_JSON["switches"]["context_reset_at"], 80000)
        self.assertEqual(json.loads(json.dumps(MODULE.RESET_VARIANT_JSON))["extends"], "balanced")


class RegistrationTests(unittest.TestCase):
    def test_the_case_runs_by_name_but_is_not_required(self):
        self.assertEqual(MODULE.selected("context-reset"), ["context-reset"])
        self.assertNotIn("context-reset", MODULE.selected("all"))
        self.assertNotIn("context-reset", MODULE.CASES)
        self.assertIs(MODULE.case_entry("context-reset")[0], MODULE.case_context_reset)

    def test_the_plan_describes_it(self):
        printed = MODULE.plan("claude-code-cli-macos", ["context-reset"], "cheapest")
        self.assertIn("lowered reset point", printed)
        self.assertNotIn(MODULE.NOT_AUTOMATED, printed)

    def test_a_codex_home_is_unverified(self):
        home = MODULE.Home.__new__(MODULE.Home)
        home.runtime = "codex"
        with self.assertRaises(MODULE.Unverified):
            MODULE.case_context_reset(home)


if __name__ == "__main__":
    unittest.main()

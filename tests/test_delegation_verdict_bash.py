"""A read-only Bash command counts as a gather call in the delegation verdict (#1192).

The read-only judgement is the `allow-readonly-bash` hook's own; these tests pin that the verdict
uses it for the gathering commands agents run, and that a write anywhere in a command, a pipeline
or a sequence keeps it from counting. No test launches an agent or calls a model."""
import unittest

from test_cost_bench import BENCH

VERDICT = BENCH.delegation_verdict

GATHERS = (
    "git log --oneline -20",
    "git show HEAD~1:scripts/cost_bench.py",
    "git diff origin/main -- scripts",
    "git status --short",
    "git -C /work/repo log -5",
    "gh issue view 1192",
    "gh pr view 12 --json title",
    "gh pr list --state open",
    "gh issue list --label bug",
    "rg -n gather_calls scripts",
    "sed -n 1,40p scripts/delegation_verdict.py",
    "head -50 README.md",
    "tail -n 20 log.txt",
    "cat a.py b.py",
    "wc -l scripts/*.py",
    "ls -la tests",
    "find . -name '*.py' -type f",
    "grep -rn GATHER_TOOLS scripts 2>/dev/null",
)
COMPOUND_GATHERS = (
    "cd /work/repo && git log --oneline -5",
    "cd /work/repo && sed -n 10,30p a.py && wc -l a.py",
    "git show HEAD:a.py | head -40",
    "find . -name '*.md' | grep -v node_modules | wc -l",
    "cat a.py | sed -n 1,5p",
    "git log --oneline; git status",
)
NOT_GATHERS = (
    "git commit -m 'fix: x'",
    "git push origin fix",
    "sed -i 's/a/b/' a.py",
    "cat a.py > b.py",
    "echo done >> notes.txt",
    "git log > history.txt",
    "python3 -m unittest discover -s tests",
    "rm -rf build",
    "gh issue create --title x",
    "find . -name '*.pyc' -delete",
)
COMPOUND_NOT_GATHERS = (
    "cd /work/repo && git commit -m x",
    "cd /work/repo && python3 scripts/run.py",
    "cat a.py | tee b.py",
    "git status && git add -A",
    "grep -n x a.py | sed -n 1p > out.txt",
    "find . -name '*.py' | xargs rm",
    "",
    "   ",
)


def bash(command):
    return {"command": command}


class BashGatherTest(unittest.TestCase):
    def test_read_only_commands_are_gathers(self):
        for command in GATHERS:
            with self.subTest(command=command):
                self.assertTrue(VERDICT.gather_call("Bash", bash(command)))

    def test_pipelines_and_cd_sequences_count_when_every_stage_reads(self):
        for command in COMPOUND_GATHERS:
            with self.subTest(command=command):
                self.assertTrue(VERDICT.gather_call("Bash", bash(command)))

    def test_writes_and_runs_are_not_gathers(self):
        for command in NOT_GATHERS:
            with self.subTest(command=command):
                self.assertFalse(VERDICT.gather_call("Bash", bash(command)))

    def test_one_writing_stage_or_step_disqualifies_the_whole_command(self):
        for command in COMPOUND_NOT_GATHERS:
            with self.subTest(command=command):
                self.assertFalse(VERDICT.gather_call("Bash", bash(command)))

    def test_dedicated_gather_tools_still_count_and_others_do_not(self):
        for name in VERDICT.GATHER_TOOLS:
            self.assertTrue(VERDICT.gather_call(name, {}))
        self.assertFalse(VERDICT.gather_call("Edit", {"file_path": "a.py"}))
        self.assertFalse(VERDICT.gather_call("Write", bash("cat a.py")))

    def test_a_bash_call_without_a_command_is_not_a_gather(self):
        self.assertFalse(VERDICT.gather_call("Bash", None))
        self.assertFalse(VERDICT.gather_call("Bash", {}))
        self.assertFalse(VERDICT.gather_call("Bash", {"command": ["ls"]}))

    def test_classifier_is_the_hook_itself(self):
        self.assertEqual(VERDICT.READONLY_HOOK.name, "allow-readonly-bash.py")
        self.assertTrue(VERDICT.READONLY_HOOK.is_file())


def say(thread, calls):
    return {"type": "assistant", "parent_tool_use_id": thread,
            "message": {"content": [{"type": "tool_use", "id": "toolu_%d" % i, "name": name, "input": tool_input}
                                    for i, (name, tool_input) in enumerate(calls)]}}


class GatherThreadsTest(unittest.TestCase):
    def test_counts_bash_and_tool_gathers_per_thread_and_skips_writes(self):
        messages = [
            {"type": "system", "subtype": "init", "tools": ["Bash", "Read", "Agent"]},
            say(None, [("Bash", bash("cd /r && git log -3")), ("Read", {"file_path": "a"}),
                       ("Bash", bash("git commit -m x")), ("Agent", {"prompt": "look"})]),
            say("toolu_3", [("Bash", bash("rg -n x | head")), ("Grep", {"pattern": "x"}),
                            ("Bash", bash("sed -i s/a/b/ f"))]),
            {"type": "user", "parent_tool_use_id": None,
             "message": {"content": [{"type": "tool_use", "name": "Read", "input": {}}]}},
        ]
        self.assertEqual(VERDICT.gather_threads(messages), [None, None, "toolu_3", "toolu_3"])

    def test_an_empty_or_malformed_stream_has_no_gathers(self):
        self.assertEqual(VERDICT.gather_threads([]), [])
        self.assertEqual(VERDICT.gather_threads(["x", None, {"type": "assistant"}]), [])


if __name__ == "__main__":
    unittest.main()

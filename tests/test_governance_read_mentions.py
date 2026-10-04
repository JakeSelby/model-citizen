# SPDX-License-Identifier: MIT
"""A read of a protected path, or a mention of one in quoted text, is not a change to it.

An inline program that opens the harness configuration or a governance policy file read-only
and makes no write call reads it, and a path named only inside quoted gh issue or pull request
text is data. Every write, through the program, a redirect or a copying command, stays a
level-1 change that needs the user's yes.

Run: python3 -m unittest discover tests
"""
import unittest

from test_governance_binding import Home, grader

CONFIG = "~/.config/agent-harness/config.json"
POLICY = ".agent-harness/governance.json"

READS = [
    # Regression: the program that was refused, `open` with no mode.
    "python3 -c \"import json; print(json.load(open('%s'))['governance'])\"" % CONFIG,
    "python3 -c 'import json, os; print(json.load(open(os.path.expanduser(\"%s\"))))'" % CONFIG,
    "python3 -c \"import json; print(json.load(open('%s', 'r')))\"" % CONFIG,
    "python3 -c \"import json; print(json.load(open('%s', 'rb')))\"" % CONFIG,
    "python3 -c \"print(open('%s', mode='r').read())\"" % POLICY,
    # Regression: an issue body naming the path, in the shapes agents write.
    "gh issue create --title x --body \"The grader reads %s.\nIt never writes it.\"" % CONFIG,
    "gh issue create --title x --body \"$(cat <<'EOF'\nThe grader reads %s.\nEOF\n)\"" % CONFIG,
    "gh pr create --title x --body \"reads %s through \\`json.load\\`\"" % POLICY,
    "gh issue comment 3 --body \"see %s\" --label type::bug" % CONFIG,
]

WRITES = [
    "python3 -c \"open('%s', 'w').write('{}')\"" % CONFIG,
    "python3 -c \"open('%s', 'a').write('{}')\"" % CONFIG,
    "python3 -c \"open('%s', 'x').write('{}')\"" % CONFIG,
    "python3 -c \"open('%s', 'r+').write('{}')\"" % POLICY,
    "python3 -c \"import pathlib; pathlib.Path('%s').write_text('{}')\"" % CONFIG,
    "python3 -c \"import json; json.dump({}, open('%s', 'w'))\"" % CONFIG,
    "python3 -c \"import json; f = open('%s'); json.dump({}, f)\"" % CONFIG,
    "echo '{}' > " + CONFIG,
    "python3 -c \"print(open('/tmp/x').read())\" > " + CONFIG,
    "cp /tmp/config.json " + CONFIG,
    "mv /tmp/config.json " + CONFIG,
    "cp /tmp/governance.json " + POLICY,
    "mv /tmp/governance.json " + POLICY,
]

# A read, or quoted text, that something else on the line may turn into a write.
NEAR_MISSES = [
    "python3 -c \"print('echo {} > %s')\" | sh" % CONFIG,
    "$(python3 -c \"print(open('%s').read())\")" % CONFIG,
    "P=%s python3 -c \"import os; print(open(os.environ['P']).read())\"" % CONFIG,
    "python3 -W ignore -c \"open('%s', 'w')\"" % CONFIG,
    "python3 - <<'EOF'\nprint(open('%s').read())\nEOF" % CONFIG,
    "gh issue create --title x --body " + CONFIG,
    "gh issue create --title x --label '%s'" % CONFIG,
    "gh issue create --body-file - <<EOF\n$(echo {} > %s)\nEOF" % CONFIG,
    "gh issue create --body \"$(python3 -c \"open('%s', 'w')\")\"" % CONFIG,
    "gh issue create --body \"see %s\"; echo {} > %s" % (CONFIG, CONFIG),
    "eval \"python3 -c 'print(open(\\\"%s\\\").read())'\"" % CONFIG,
    "for f in %s; do python3 -c 'print(1)'; done" % CONFIG,
    "git commit -m \"document %s\"" % POLICY,
]


class ProtectedReads(Home):
    def setUp(self):
        super().setUp()
        self.configure("local")

    def test_regression_a_read_or_quoted_mention_is_not_a_change(self):
        for command in READS:
            with self.subTest(command=command):
                _answer, reason = self.bash(command)
                self.assertNotIn("level 1", reason)
                self.assertNotIn("harness configuration", reason)
                self.assertNotIn("policy file", reason)

    def test_every_write_stays_a_level_one_change(self):
        for command in WRITES:
            with self.subTest(command=command):
                answer, reason = self.bash(command)
                self.assertEqual(answer, "ask")
                self.assertIn("level 1", reason)

    def test_a_read_another_command_may_turn_into_a_write_stays_gated(self):
        for command in NEAR_MISSES:
            with self.subTest(command=command):
                answer, reason = self.bash(command)
                self.assertEqual(answer, "ask")
                self.assertIn("level 1", reason)


class DataMentionsOnly(unittest.TestCase):
    def test_reads_and_quoted_text_are_data(self):
        for command in READS:
            with self.subTest(command=command):
                self.assertTrue(grader.data_mentions_only(command))

    def test_writes_and_near_misses_are_not(self):
        # A body only gh or `cat` reads is data here; `literal_text_command` judges `tee`'s.
        bodies = ["tee /tmp/x <<'EOF'\n%s\nEOF" % CONFIG,
                  "cat 1<> /tmp/x <<'EOF'\n%s\nEOF" % CONFIG,
                  "cat <<'EOF' > /tmp/x\n%s\nEOF" % CONFIG]
        for command in WRITES + NEAR_MISSES + bodies:
            with self.subTest(command=command):
                self.assertFalse(grader.data_mentions_only(command))

    def test_a_read_only_mode_reads_only_for_the_protected_path_check(self):
        program = "import json; print(json.load(open('/x/config.json', 'rb')))"
        self.assertFalse(grader._program_writes(program, modes=True))
        self.assertTrue(grader._program_writes(program))  # the approvals store stays as it was
        for mode in ("w", "a", "x", "r+", "wb"):
            with self.subTest(mode=mode):
                self.assertTrue(grader._program_writes(
                    "open('/x/config.json', '%s')" % mode, modes=True))


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: MIT
"""No tracked file trips the plugin-catalog scanner on a placeholder, and real shapes still would.

The catalog runs `plugin-scanner` (3.12.2) and rejects a plugin with any high finding. Its secret
check reads every file, so a test's stand-in credential written as one literal reads as a leak;
its skill check reads every `SKILL.md` for the SSH path and cannot tell a deny entry from an
instruction to read. The shapes below mirror the scanner's, and each fixed form is paired with
the near miss it must still catch, so the guard is proven to bite. The near misses are built by
concatenation so this file holds none of them whole.
"""
import ast
import json
import re
import subprocess
import unittest

from test_harness import REPO


PROVIDER_TOKEN = re.compile(r"(?<![A-Za-z0-9])sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}")
ASSIGNED_SECRET = re.compile(
    r"(?i)(?:password|secret|token|api_?key|private_key)\s*[=:]\s*[\"'][^\s\"']{8,}")
SKILL_SSH = re.compile(r"(?i)~/\.ssh|id_rsa|authorized_keys")


def secret_hits(text):
    return [m.group(0) for p in (PROVIDER_TOKEN, ASSIGNED_SECRET) for m in p.finditer(text)]


# (what a fixture now says, the near miss the scanner must still flag)
PAIRS = {
    "provider token split in two": (
        'SECRET = "sk-ant-" + "oat01-never-on-a-command-line"',
        'SECRET = "sk-ant-' + 'oat01-never-on-a-command-line"'),
    "rule id under an unambiguous name": (
        'SECRET_RULE = "secrets/git-add-secret-file"',
        'SECRET = "' + 'secrets/git-add-secret-file"'),
    "key placeholder bound to a name": (
        "TYPESAFE_API_KEY=PLACEHOLDER_KEY",
        'TYPESAFE_API_KEY="' + 'not-a-real-key"'),
}


def tracked_files():
    listed = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"], check=True,
                            stdout=subprocess.PIPE).stdout.decode("utf-8").split("\0")
    return [REPO / name for name in listed if name]


class ScannerShapeTests(unittest.TestCase):
    def test_each_fixed_fixture_is_clean_and_its_near_miss_is_still_flagged(self):
        for name, (fixed, near_miss) in PAIRS.items():
            with self.subTest(name):
                self.assertEqual(secret_hits(fixed), [])
                self.assertNotEqual(secret_hits(near_miss), [])

    def test_a_skill_that_reads_ssh_material_is_still_flagged(self):
        self.assertIsNotNone(SKILL_SSH.search("Run `cat " + "~/" + ".ssh/config` first."))
        self.assertIsNotNone(SKILL_SSH.search("Copy the " + "id_" + "rsa file over."))


class TrackedTreeTests(unittest.TestCase):
    def test_no_tracked_file_holds_a_secret_shape(self):
        found = []
        for path in tracked_files():
            if path.is_symlink() or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            found += ["{}: {}".format(path.relative_to(REPO), hit) for hit in secret_hits(text)]
        self.assertEqual(found, [])

    def test_no_skill_text_names_ssh_material(self):
        skills = [path for path in tracked_files()
                  if path.name == "SKILL.md" and not path.is_symlink()]
        self.assertTrue(skills)
        self.assertEqual([str(path.relative_to(REPO)) for path in skills
                          if SKILL_SSH.search(path.read_text(encoding="utf-8"))], [])


class SandboxDenyTests(unittest.TestCase):
    def test_the_sandbox_skill_still_denies_reads_of_credential_directories(self):
        skill = REPO / "primitives" / "skills" / "sandbox"
        settings = json.loads((skill / "claude-settings.json").read_text(encoding="utf-8"))
        deny = settings["sandbox"]["filesystem"]["denyRead"]
        for path in ("~/.ssh", "~/.aws", "~/.config/gh"):
            self.assertIn(path, deny)
        self.assertTrue(settings["sandbox"]["failIfUnavailable"])
        self.assertFalse(settings["sandbox"]["allowUnsandboxedCommands"])
        self.assertIn("(claude-settings.json)", (skill / "SKILL.md").read_text(encoding="utf-8"))


class FixtureValueTests(unittest.TestCase):
    def test_the_split_stand_in_keeps_its_token_shape_at_run_time(self):
        source = (REPO / "tests" / "test_cost_bench.py").read_text(encoding="utf-8")
        line = next(line for line in source.splitlines() if line.startswith("SECRET = "))
        expression = ast.parse(line).body[0].value
        self.assertIsInstance(expression, ast.BinOp)
        value = ast.literal_eval(expression.left) + ast.literal_eval(expression.right)
        self.assertEqual(value, "sk-ant-" + "oat01-never-on-a-command-line")
        self.assertIsNotNone(PROVIDER_TOKEN.fullmatch(value))


if __name__ == "__main__":
    unittest.main()

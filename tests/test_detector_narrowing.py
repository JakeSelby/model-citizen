# SPDX-License-Identifier: MIT
"""Regression tests for two detectors narrowed to clear the corpus precision floor.

`secrets/git-add-secret-file` read any basename holding `id_rsa` as a key, and
`autonomy/denied-by-grade` read the grade hook's signature anywhere in a Bash result as a
denial. Each case below is a false positive the old match produced, beside the hit it keeps.

Run: python3 -m unittest discover -s tests -p test_detector_narrowing.py
"""
import importlib.util
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rd = _load("rule_detectors_narrowing", REPO / "claude" / "hooks" / "rule-detectors.py")
grade = _load("grade_bash_narrowing", REPO / "claude" / "hooks" / "grade-bash.py")

STANCES = {"commits": "conventional-attributed", "voice": "concise"}
SECRET = "secrets/git-add-secret-file"
DENIED = "autonomy/denied-by-grade"
CLIENT_PREFIX = "PreToolUse:Bash hook error: "


def bash(command):
    return {"kind": "tool_use", "turn": 1, "id": "tu1", "name": "Bash",
            "input": {"command": command}}


def result(text):
    return {"kind": "tool_result", "turn": 1, "tool_use_id": "tu1", "tool_name": "Bash",
            "text": text}


def hits(detector_id, events):
    return len(rd.run(events, STANCES).get(detector_id, []))


def deny_reason(target="--force origin main"):
    return grade.reason(3, "git push", target, "git-push", "execute")


class SshKeyNameTests(unittest.TestCase):
    def test_a_private_key_by_its_default_name_is_a_hit(self):
        for path in ("config/id_rsa", "id_rsa", "keys/id_ed25519", "~/.ssh/id_ed25519"):
            with self.subTest(path=path):
                self.assertEqual(hits(SECRET, [bash("git add " + path)]), 1)

    def test_a_name_that_only_holds_the_key_name_is_not(self):
        for path in ("docs/id_rsa-rotation.md", "keys/id_ed25519.pub", "id_rsa.pub",
                     "scripts/rotate_id_rsa.sh", "notes/id_ed25519_howto.txt"):
            with self.subTest(path=path):
                self.assertEqual(hits(SECRET, [bash("git add " + path)]), 0)


class GradeDenyTests(unittest.TestCase):
    def test_the_reason_the_hook_writes_is_a_denial(self):
        self.assertEqual(hits(DENIED, [result(deny_reason())]), 1)

    def test_the_reason_behind_the_client_prefix_is_a_denial(self):
        self.assertEqual(hits(DENIED, [result(CLIENT_PREFIX + deny_reason())]), 1)

    def test_a_reason_naming_a_multi_line_command_is_a_denial(self):
        text = CLIENT_PREFIX + deny_reason("--force origin main\necho done")
        self.assertEqual(hits(DENIED, [result(text)]), 1)

    def test_a_result_that_only_quotes_the_signature_is_not(self):
        quoted = (
            "486:            \"— this cannot be undone (grade-bash hook, autonomy=execute)\")",
            "tests/test_grade_bash.py:12: " + deny_reason(),
            "Running the gate.\n" + deny_reason(),
            "the grade-bash hook is described on line 40",
        )
        for text in quoted:
            with self.subTest(text=text[:40]):
                self.assertEqual(hits(DENIED, [result(text)]), 0)


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: MIT
"""The pre-compact snapshot redacts before it clips, and redacts todos as it does prompts."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_context_reset_handoff import NOW, Repository, prompt, todos  # noqa: E402


class PreCompactRedactionTests(Repository):
    def test_a_secret_across_the_clip_point_is_redacted_before_the_cut(self):
        # Assembled at run time so no secret shape is written into a committed file.
        token = "sk-" + "a" * 40
        text = "x" * (self.hook.PROMPT_CHARS - 20) + " " + token + " tail"
        self.write(prompt(text))
        snapshot = self.hook.snapshot(self.payload(), now=NOW).read_text()
        self.assertNotIn("sk-" + "a" * 10, snapshot)
        self.assertIn("<redacted>", snapshot)

    def test_a_secret_in_a_todo_is_redacted(self):
        token = "sk-" + "b" * 24
        self.write(todos(("rotate " + token, "pending")))
        snapshot = self.hook.snapshot(self.payload(), now=NOW).read_text()
        self.assertNotIn(token, snapshot)
        self.assertIn("- [pending] rotate <redacted>", snapshot)

    def test_without_the_secret_shapes_no_todo_is_written(self):
        self.write(todos(("keep this private", "in_progress")))
        with mock.patch.object(self.hook, "_REDACTION", [None]):
            snapshot = self.hook.snapshot(self.payload(), now=NOW).read_text()
        self.assertNotIn("keep this private", snapshot)
        self.assertEqual(snapshot.count("Left out: the secret patterns"), 2)

    def test_git_status_takes_no_optional_lock(self):
        calls = []
        real = self.hook.subprocess.run

        def spy(command, **kwargs):
            calls.append(kwargs.get("env") or {})
            return real(command, **kwargs)

        with mock.patch.object(self.hook.subprocess, "run", side_effect=spy):
            self.hook.git_status(self.repo)
        self.assertEqual(calls[0].get("GIT_OPTIONAL_LOCKS"), "0")


if __name__ == "__main__":
    unittest.main()

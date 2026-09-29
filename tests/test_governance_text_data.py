# SPDX-License-Identifier: MIT
"""Literal documentation text is not a write to the configuration it mentions."""
from test_governance_binding import Home, grader


class LiteralDocumentationWrites(Home):
    def setUp(self):
        super().setUp()
        self.configure("local")

    def test_literal_heredoc_and_quoted_text_write_only_their_destination(self):
        commands = [
            "cat > /tmp/example.md <<'EOF'\nNever edit ~/.config/agent-harness/config.json.\nEOF",
            'cat <<"EOF" > /tmp/example.md\nMention .config/agent-harness/config.json\nEOF',
            "printf '%s' '~/.config/agent-harness/config.json' > /tmp/example.md",
            "echo '~/.config/agent-harness/config.json' > /tmp/example.md",
            "tee /tmp/example.md <<'EOF'\n.config/agent-harness/config.json\nEOF",
        ]
        for command in commands:
            for mode in ("default", "auto"):
                with self.subTest(command=command, mode=mode):
                    answer, reason = self.bash(command, mode=mode)
                    self.assertIsNone(answer, reason)
                    self.assertNotIn("level 1", reason)

    def test_actual_configuration_writes_remain_protected(self):
        target = "~/.config/agent-harness/config.json"
        for command in ("echo '{}' > " + target, "tee " + target,
                        "cp /tmp/source " + target, "mv /tmp/source " + target,
                        "sed -i '' s/a/b/ " + target,
                        "cat > " + target + " <<'EOF'\n{}\nEOF"):
            with self.subTest(command=command):
                answer, reason = self.bash(command)
                self.assertEqual(answer, "ask")
                self.assertIn("level 1", reason)

    def test_literal_text_does_not_hide_a_symlinked_configuration_destination(self):
        target = self.home / ".config" / "agent-harness" / "config.json"
        link = self.home / "document.md"
        link.symlink_to(target)
        command = "echo '.config/agent-harness/config.json' > " + str(link)
        answer, reason = self.bash(command)
        self.assertEqual(answer, "ask")
        self.assertIn("level 1", reason)

    def test_executable_or_ambiguous_text_never_gets_the_literal_exemption(self):
        target = ".config/agent-harness/config.json"
        commands = [
            "python3 -c 'open(\"" + target + "\",\"w\").write(\"x\")'",
            "bash <<'EOF'\necho x > " + target + "\nEOF",
            "cat > /tmp/x <<EOF\n$(echo x > " + target + ")\nEOF",
            "cat > /tmp/x <<'EOF'\ntext\nEOF\necho x > " + target,
            "printf '%s' '" + target + "' | bash",
            "echo \"$(touch " + target + ")\" > /tmp/x",
            "cat > /tmp/x # <<'EOF'\necho x > " + target + "\nEOF",
            "printf -v 'x[$(cp /tmp/p ~/.config/agent-harness/config.json)]' %s x",
            "tee /tmp/{one,two} <<'EOF'\n" + target + "\nEOF",
            "echo '" + target + "' >! /tmp/x",
            "echo '" + target + "' >| /tmp/x",
            "cat 1<> /tmp/x <<'EOF'\n" + target + "\nEOF",
            "echo '" + target + "' &>> /tmp/x",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(grader.literal_text_command(command))
                answer, reason = self.bash(command)
                self.assertIn(answer, ("ask", "deny"))

    def test_alternate_writes_and_utility_expansion_reach_the_configuration_guard(self):
        target = "~/.config/agent-harness/config.json"
        commands = ["printf -v 'x[$(cp /tmp/p " + target + ")]' %s x",
                    "tee " + target + "{,.bak} </dev/null",
                    "echo x >! " + target, "echo '{}' >| " + target,
                    "cat /tmp/payload 1<> " + target, "echo x &>> " + target]
        for command in commands:
            with self.subTest(command=command):
                answer, reason = self.bash(command)
                self.assertEqual(answer, "ask", reason)
                self.assertIn("level 1", reason)
                self.assertIn("configuration", reason)

    def test_printf_variable_formats_and_redirected_options_are_not_data(self):
        target = "~/.config/agent-harness/config.json"
        payload = "'x[$(cp /tmp/p " + target + "; echo 1)]'"
        for command in ("printf >/dev/null -v " + payload + " %s x",
                        "printf '%n' " + payload,
                        "printf 2>/dev/null '%n' " + payload,
                        "tee ~/.config/agent-harness/{config.json,other} </dev/null",
                        "tee ~/.config/{agent-harness,other}/config.json </dev/null"):
            with self.subTest(command=command):
                self.assertFalse(grader.literal_text_command(command))
                answer, reason = self.bash(command)
                self.assertEqual(answer, "ask", reason)
                self.assertIn("level 1", reason)
                self.assertIn("configuration", reason)

    def test_force_clobber_redirect_resolves_its_actual_symlink_destination(self):
        target = self.home / ".config" / "agent-harness" / "config.json"
        link = self.home / "document.md"
        link.symlink_to(target)
        for operator in (">!", ">>!", ">&", "&>!", "&>>!", ">|", ">>|", "&>|", "&>>|"):
            with self.subTest(operator=operator):
                answer, reason = self.bash("echo x " + operator + " " + str(link))
                self.assertEqual(answer, "ask", reason)
                self.assertIn("configuration", reason)
                self.assertIn("level 1", reason)

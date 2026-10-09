# SPDX-License-Identifier: MIT
"""A Studio CLI spend or apply needs a person's yes, carried by the harness approval channel.

The command grader asks about every paid start and every apply in each permission mode, and
refuses one in `auto` and `bypassPermissions` with an approval code for that exact line. The
CLI refuses a spend or apply unless it takes a one-use grant written by that channel or by the
Studio's own dialog, so nothing an agent supplies by itself confirms one. Every test runs under a
temporary HOME.

Run: python3 -m unittest tests.test_cli_confirmation
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from isolation import isolate_home
from test_harness import REPO, harness

from harness_core import lifecycle
from harness_core.studio import activity, server

import cli_confirmation_support as support

approvals = support.approvals
grader = support.load("grade-bash", "cli_confirmation_grade_bash")

SESSION = "s-cli-confirm"
REPLAY_START = "citizen runs replay start --request request.json --json"
SPENDS = (
    REPLAY_START,
    "citizen runs eval start --request request.json --json",
    "citizen runs native start --request request.json --json",
    "citizen runs draft-test start --request request.json --json",
    "citizen runs start paid-suite --target-kind installed --target-ref current --confirm-spend "
    + "a" * 64,
    "citizen draft apply tuning --revision " + "b" * 40 + " --json",
    "citizen draft rollback " + "c" * 32 + " --draft tuning --json",
    "citizen draft recover --draft tuning --json",
    "python3 bin/harness draft apply tuning --revision " + "b" * 40,
)
FREE = (
    "citizen runs replay preview --request request.json --json",
    "citizen runs replay catalog --json",
    "citizen runs eval preview --request request.json --json",
    "citizen runs eval catalog --request request.json --json",
    "citizen runs native preview --request request.json --json",
    "citizen runs native retry --request request.json --json",
    "citizen runs draft-test plan --request request.json --json",
    "citizen runs start free-suite --json",
    "citizen runs list --json",
    "citizen runs history --json",
    "citizen draft review tuning --json",
    "citizen draft rollback " + "c" * 32 + " --preview --json",
    "citizen reports trends --json",
    "citizen doctor --json",
    "grep -n 'citizen draft apply' README.md",
)
GRANTS = "~/.local/state/agent-harness/approvals/cli-grants/grants.json"


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))
        isolate_home(self.home)
        os.environ.pop("HARNESS_QUIET", None)
        os.environ["HARNESS_STANCE_AUTONOMY"] = "execute"
        self.cwd = self.home / "work"
        self.cwd.mkdir()

    def bash(self, command, mode="default", session=SESSION, runtime="claude-code"):
        """`(permissionDecision, reason)` the dispatcher gives one Bash call."""
        out = lifecycle.dispatch(runtime, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                           "tool_input": {"command": command}, "session_id": session,
                                           "permission_mode": mode, "cwd": str(self.cwd)})
        block = out.get("hookSpecificOutput", {})
        return block.get("permissionDecision"), block.get("permissionDecisionReason", "")

    def prompt(self, text, session=SESSION):
        lifecycle.dispatch("claude-code", {"hook_event_name": "UserPromptSubmit", "session_id": session,
                                           "prompt": text, "cwd": str(self.cwd)})

    @staticmethod
    def words(command):
        return grader.cli_spends(command)[0][0]


class GraderAsks(Home):
    """AC1 and AC4: the grader asks about spends and applies, and about nothing free."""

    def test_every_spend_and_apply_asks_in_each_prompting_mode_and_files_a_prompt_grant(self):
        for command in SPENDS:
            for mode in ("default", "acceptEdits", "plan"):
                with self.subTest(command=command, mode=mode):
                    decision, reason = self.bash(command, mode=mode)
                    self.assertEqual(decision, "ask", reason)
                    self.assertIn("a person confirms it each time", reason)
                    self.assertEqual(approvals.take_grant(self.words(command)), "prompt")
                    self.assertIsNone(approvals.take_grant(self.words(command)))

    def test_auto_mode_refuses_with_a_code_that_covers_the_exact_line_once(self):
        for command in SPENDS:
            with self.subTest(command=command):
                decision, reason = self.bash(command, mode="auto")
                code = approvals.code_for(SESSION, command)
                self.assertEqual(decision, "deny")
                self.assertIn("reply with exactly `approve %s`" % code, reason)
                self.assertIsNone(approvals.take_grant(self.words(command)))
                self.prompt("approve " + code)
                self.assertNotIn(self.bash(command, mode="auto")[0], ("deny", "ask"))
                self.assertEqual(approvals.take_grant(self.words(command)), "approval")
                self.assertEqual(self.bash(command, mode="auto")[0], "deny")

    def test_an_approval_covers_neither_another_command_nor_another_session(self):
        self.prompt("approve " + approvals.code_for(SESSION, REPLAY_START))
        other = REPLAY_START.replace("request.json", "other.json")
        self.assertEqual(self.bash(other, mode="auto")[0], "deny")
        self.assertEqual(self.bash(REPLAY_START, mode="auto", session="s-other")[0], "deny")
        self.assertIsNone(approvals.take_grant(self.words(other)))

    def test_free_commands_are_not_asked_about_and_file_no_grant(self):
        for command in FREE:
            for mode in ("default", "auto"):
                with self.subTest(command=command, mode=mode):
                    self.assertIsNone(grader.cli_confirmation(command, mode, SESSION))
                    self.assertNotIn(self.bash(command, mode=mode)[0], ("deny", "ask"))
        self.assertFalse(approvals.grants_path().exists())

    def test_the_standalone_hook_asks_and_denies_as_the_dispatcher_does(self):
        script = REPO / "policy" / "hooks" / "grade-bash.py"
        for mode, expected in (("default", "ask"), ("auto", "deny"), ("bypassPermissions", "deny")):
            with self.subTest(mode=mode):
                done = subprocess.run(
                    ["python3", str(script)], input=json.dumps({
                        "tool_name": "Bash", "tool_input": {"command": REPLAY_START},
                        "permission_mode": mode, "session_id": SESSION, "cwd": str(self.cwd)}),
                    capture_output=True, text=True, env=dict(os.environ), timeout=60)
                out = json.loads(done.stdout)["hookSpecificOutput"]
                self.assertEqual(out["permissionDecision"], expected)


class AgentCannotConfirm(Home):
    """AC2: nothing an agent writes or sets by itself counts as a person's yes."""

    def test_the_marker_confirms_nothing_in_any_mode(self):
        marked = "HARNESS_CONFIRMED=1 " + REPLAY_START
        self.assertEqual(self.bash(marked, mode="default")[0], "ask")
        approvals.take_grant(self.words(REPLAY_START))  # the prompt's grant, for the person's yes
        for mode in ("auto", "bypassPermissions"):
            with self.subTest(mode=mode):
                decision, reason = self.bash(marked, mode=mode)
                self.assertEqual(decision, "deny")
                self.assertNotIn("HARNESS_CONFIRMED=1 in front", reason)
                self.assertIsNone(approvals.take_grant(self.words(REPLAY_START)))

    def test_bypass_mode_takes_the_approval_code_instead_of_the_marker(self):
        decision, reason = self.bash(REPLAY_START, mode="bypassPermissions")
        code = approvals.code_for(SESSION, REPLAY_START)
        self.assertEqual(decision, "deny")
        self.assertIn("approve %s" % code, reason)
        self.prompt("approve " + code)
        self.assertNotIn(self.bash(REPLAY_START, mode="bypassPermissions")[0], ("deny", "ask"))
        self.assertEqual(approvals.take_grant(self.words(REPLAY_START)), "approval")

    def test_codex_refuses_and_files_no_grant(self):
        for mode in ("default", "auto"):
            with self.subTest(mode=mode):
                self.assertEqual(self.bash(REPLAY_START, mode=mode, runtime="codex")[0], "deny")
        self.assertIsNone(approvals.take_grant(self.words(REPLAY_START)))

    def test_a_spend_hidden_where_no_grant_can_name_it_is_refused_in_every_mode(self):
        for command in ('bash -c "citizen draft apply tuning --revision abc"',
                        'echo "$(citizen runs replay start --request r.json)"',
                        "eval citizen draft recover",
                        "xargs citizen draft apply tuning --revision"):
            for mode in ("default", "auto"):
                with self.subTest(command=command, mode=mode):
                    decision, reason = self.bash(command, mode=mode)
                    self.assertEqual(decision, "deny", reason)
                    self.assertIn("as a plain command", reason)
        self.assertFalse(approvals.grants_path().exists())

    def test_an_agent_written_grant_is_refused_by_the_grader_and_the_file_guard(self):
        for command in ("echo '{}' > " + GRANTS,
                        "mkdir -p ~/.local/state/agent-harness/approvals/cli-grants",
                        "cp forged.json " + GRANTS):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command, mode="auto")[0], "deny")
                self.assertEqual(self.bash(command, mode="default")[0], "ask")
        path = os.path.expanduser(GRANTS)
        self.assertIsNotNone(approvals.file_write_deny([path]))

    def test_a_grant_is_one_use_bound_to_its_words_and_short_lived(self):
        words = self.words(REPLAY_START)
        self.assertTrue(approvals.grant(words, "prompt", now=1000.0))
        self.assertIsNone(approvals.take_grant(words + ["--extra"], now=1001.0))
        self.assertIsNone(approvals.take_grant(words, now=1000.0 + approvals.GRANT_TTL + 1))
        self.assertTrue(approvals.grant(words, "approval", now=2000.0))
        self.assertEqual(approvals.take_grant(words, now=2001.0), "approval")
        self.assertIsNone(approvals.take_grant(words, now=2002.0))
        self.assertFalse(approvals.grant(words, "agent"))


class CliRefuses(Home):
    """AC2 and AC3 at the CLI: no grant, no spend or apply; a grant is used once and logged."""

    def cli(self, *argv, stdin=""):
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(harness, "state_dir", return_value=self.home / "state"), \
                mock.patch.object(harness.sys, "stdin", io.StringIO(stdin)), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = harness.main(list(argv))
        return code, output.getvalue(), errors.getvalue()

    def refused(self, *argv, stdin=""):
        code, printed, said = self.cli(*argv, stdin=stdin)
        self.assertEqual(code, 2, said)
        self.assertEqual(json.loads(printed), {"error": "person_confirmation_required"})
        self.assertIn("needs a person's confirmation", said)

    def test_a_paid_start_without_a_grant_is_refused_before_its_route_runs(self):
        body = json.dumps({"request": {}, "confirmation_token": "f" * 64})
        with mock.patch.object(harness.studio_headless, "call") as route:
            for group in ("replay", "eval", "native", "draft-test"):
                with self.subTest(group=group):
                    self.refused("runs", group, "start", "--request", "-", "--json", stdin=body)
            route.assert_not_called()

    def test_a_token_environment_variable_marker_or_studio_flag_does_not_confirm(self):
        os.environ.update({"HARNESS_CONFIRMED": "1", "CITIZEN_CONFIRMED": "1"})
        with mock.patch.object(harness.studio_headless, "call") as route, \
                mock.patch.object(harness.studio_apply, "apply") as apply:
            self.refused("runs", "replay", "start", "--request", "-", "--json",
                         stdin=json.dumps({"confirmation_token": "f" * 64}))
            self.refused("draft", "apply", "tuning", "--revision", "b" * 40, "--via-studio",
                         "--json")
            self.refused("draft", "rollback", "c" * 32, "--draft", "tuning", "--json")
            self.refused("draft", "recover", "--draft", "tuning", "--json")
            route.assert_not_called()
            apply.assert_not_called()

    def test_a_paid_catalog_start_with_a_token_but_no_grant_is_refused(self):
        supervisor = mock.Mock()
        supervisor.spend_preview.return_value = {"confirmation_required": True}
        with mock.patch.object(harness.studio.runs, "RunSupervisor", return_value=supervisor), \
                mock.patch.object(harness.studio_free_suites, "start_refusal", return_value=None):
            argv = ["runs", "start", "paid-suite", "--target-kind", "installed", "--target-ref",
                    "current", "--confirm-spend", "a" * 64, "--json"]
            self.refused(*argv)
            supervisor.start.assert_not_called()
            supervisor.start.return_value = {"run_id": "r", "status": "queued", "suite_id": "s"}
            approvals.grant(argv, "prompt")
            self.assertEqual(self.cli(*argv)[0], 0)
            supervisor.start.assert_called_once()

    def test_a_grant_for_the_exact_words_lets_one_run_through_and_logs_it(self):
        argv = ["runs", "replay", "start", "--request", "-", "--json"]
        approvals.grant(argv, "approval")
        with mock.patch.object(harness.studio_headless, "call",
                               return_value=(200, {"run_id": "r"})) as route:
            code, printed, _said = self.cli(*argv, stdin="{}")
            self.assertEqual((code, json.loads(printed)), (0, {"run_id": "r"}))
            self.refused(*argv, stdin="{}")
            route.assert_called_once()
        page = activity.query(self.home / ".local" / "state" / "agent-harness", {})
        rows = [e for e in page["entries"] if e["kind"] == "cli-confirmed"]
        self.assertEqual(len(rows), 1, page)
        self.assertEqual(rows[0]["title"], "CLI spend or apply confirmed")
        self.assertEqual(rows[0]["actor"], "citizen")
        self.assertIn("the user's approve reply", rows[0]["reason"])
        self.assertEqual(rows[0]["command"], "citizen " + " ".join(argv))
        self.assertEqual(rows[0]["apply_id"], "")

    def test_an_apply_with_a_grant_reaches_the_governed_apply(self):
        argv = ["draft", "apply", "tuning", "--revision", "b" * 40, "--json"]
        approvals.grant(argv, "prompt")
        result = {"applied": False, "status": "refused", "message": "stale", "log": [],
                  "doctor": {"checks": []}}
        with mock.patch.object(harness.studio_apply, "apply", return_value=result) as apply:
            code, printed, _said = self.cli(*argv)
        self.assertEqual((code, json.loads(printed)["status"]), (1, "refused"))
        apply.assert_called_once()

    def test_a_person_at_a_terminal_confirms_but_not_inside_an_agent_runtime(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True

        argv = ["draft", "recover", "--draft", "tuning", "--json"]
        result = {"status": "recovered", "message": "done"}
        for variables, typed, confirmed in (({}, "yes\n", True), ({}, "no\n", False),
                                            ({"CLAUDECODE": "1"}, "yes\n", False),
                                            ({"CODEX_SANDBOX": "seatbelt"}, "yes\n", False)):
            with self.subTest(variables=variables, typed=typed):
                for name in harness.AGENT_RUNTIME_VARIABLES:
                    os.environ.pop(name, None)
                os.environ.update(variables)
                output = io.StringIO()
                with mock.patch.object(harness.sys, "stdin", Terminal(typed)), \
                        mock.patch.object(harness.sys, "stderr", Terminal()), \
                        mock.patch.object(harness.studio_apply, "recover",
                                          return_value=result) as recover, \
                        contextlib.redirect_stdout(output):
                    code = harness.main(argv)
                self.assertEqual(code, 0 if confirmed else 2)
                self.assertEqual(recover.called, confirmed)

    def test_free_commands_run_without_a_grant(self):
        code, printed, _said = self.cli("runs", "replay", "preview", "--json")
        self.assertEqual(json.loads(printed), {"error": "invalid_request"})
        self.assertEqual(code, 2)
        with mock.patch.object(harness.studio_rollback, "preview", return_value={
                "config": [], "files": [], "refusals": [], "can_rollback": False}):
            code, printed, _said = self.cli("draft", "rollback", "c" * 32, "--preview", "--json")
        self.assertEqual((code, json.loads(printed)["can_rollback"]), (1, False))


class StudioDialog(Home):
    """AC3: the Studio's own confirmed apply still reaches the CLI, through a Studio grant."""

    def test_each_studio_action_grants_exactly_the_command_it_runs(self):
        seen = []

        def run(command, **_kwargs):
            seen.append(approvals.take_grant(command[2:]))
            return subprocess.CompletedProcess(command, 0, stdout='{"status": "x"}\n', stderr="")

        with mock.patch.object(server.subprocess, "run", side_effect=run):
            server._run_draft_apply(REPO, "tuning", "b" * 40)
            server._run_draft_recover(REPO, "restore", "tuning")
            server._run_draft_rollback(REPO, "c" * 32, "tuning")
        self.assertEqual(seen, ["studio", "studio", "studio"])


if __name__ == "__main__":
    unittest.main()

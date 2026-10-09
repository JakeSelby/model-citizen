# SPDX-License-Identifier: MIT
"""A Studio spend or apply needs a person at the Mac, proved out of band.

Two layers. The command grader asks about every paid start and every apply in each permission
mode, refuses one in `auto` and `bypassPermissions` with an approval code for that exact line, and
files nothing. Then the CLI, and the Studio's own spend routes, ask macOS LocalAuthentication for
Touch ID or the login password at the moment they would spend or apply (`presence.confirm`), and
refuse when that does not pass. The suite never raises the real dialog: `isolation.stub_presence`
makes `confirm` a yes in-process and switches the real check off in children, and each refusal
test patches `confirm` to a no.

Run: python3 -m unittest tests.test_cli_confirmation
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pty
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from isolation import isolate_home
from test_harness import REPO, harness

from harness_core import lifecycle, presence
from harness_core.studio import activity, eval_tiers, headless, mutations, replay, runs, server

HOOKS = REPO / "policy" / "hooks"


def _load(name, alias):
    spec = importlib.util.spec_from_file_location(alias, str(HOOKS / name))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


approvals = _load("approvals.py", "cli_confirmation_approvals")
grader = _load("grade-bash.py", "cli_confirmation_grade_bash")

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
    "citizen runs native retry --request request.json --json",
    "citizen runs draft-test plan --request request.json --json",
    "citizen runs start free-suite --json",
    "citizen runs list --json",
    "citizen draft review tuning --json",
    "citizen draft rollback " + "c" * 32 + " --preview --json",
    "citizen reports trends --json",
    "citizen doctor --json",
    "grep -n 'citizen draft apply' README.md",
    "python3 -c 'print(1)'",
)
APPLY = ["draft", "apply", "tuning", "--revision", "b" * 40, "--json"]
PREFLIGHT = "apply draft tuning at revision %s to the live configuration" % ("b" * 40)
# The CLI in a child with the presence check on, where the person at the Mac declines: `confirm`
# records what it was asked and answers no, and the host counts as able to ask, so the refusal
# can only be the person's. Written to a temporary directory by the test that runs it.
DECLINING_CLI = """import importlib.machinery, importlib.util, sys
sys.path.insert(0, %(lib)r)
from harness_core import presence
def declined(reason):
    with open(%(asked)r, "a") as handle:
        handle.write(reason + "\\n")
    return False
presence.confirm = declined
presence.unavailable = lambda: None
loader = importlib.machinery.SourceFileLoader("declining_harness", %(cli)r)
module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(module)
module._draft_preflight = lambda args, repo: (None, %(reason)r)
sys.exit(module.main())
"""
APPLIED = {"applied": True, "status": "applied", "message": "applied", "log": [],
           "doctor": {"checks": []}, "apply_id": "d" * 32}


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

    def store_files(self):
        root = approvals.store_dir()
        return sorted(p.name for p in root.rglob("*")) if root.exists() else []

    def cli(self, *argv, stdin=None):
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(harness, "state_dir", return_value=self.home / "state"), \
                mock.patch.object(harness.sys, "stdin", stdin or io.StringIO("")), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = harness.main(list(argv))
        return code, output.getvalue(), errors.getvalue()

    def refused(self, *argv, stdin=None):
        """The refusal's message: a draft action answers in its result shape, exit 1; a run
        answers the route's error, exit 2."""
        code, printed, said = self.cli(*argv, stdin=stdin)
        answer = json.loads(printed.strip().splitlines()[-1])
        if "error_code" in answer:
            self.assertEqual((code, answer["status"], answer["error_code"], answer["applied"]),
                             (1, "refused", "person-confirmation-required", False))
            return answer["message"]
        self.assertEqual((code, answer), (2, {"error": "person_confirmation_required"}), said)
        return said

    def confirmations(self):
        page = activity.query(self.home / ".local" / "state" / "agent-harness", {})
        return [e for e in page["entries"] if e["kind"] == "person-confirmed"]


class GraderAsks(Home):
    """AC1 and AC4: the first layer asks about spends and applies, files nothing, skips the free."""

    def test_every_spend_and_apply_asks_in_each_prompting_mode_and_files_nothing(self):
        for command in SPENDS:
            for mode in ("default", "acceptEdits", "plan"):
                with self.subTest(command=command, mode=mode):
                    decision, reason = self.bash(command, mode=mode)
                    self.assertEqual(decision, "ask", reason)
                    self.assertIn("Touch ID or the login password", reason)
        self.assertEqual(self.store_files(), [])

    def test_auto_mode_refuses_with_a_code_that_covers_the_exact_line_once(self):
        for command in SPENDS:
            with self.subTest(command=command):
                decision, reason = self.bash(command, mode="auto")
                code = approvals.code_for(SESSION, command)
                self.assertEqual(decision, "deny")
                self.assertIn("reply with exactly `approve %s`" % code, reason)
                self.prompt("approve " + code)
                self.assertNotIn(self.bash(command, mode="auto")[0], ("deny", "ask"))
                self.assertEqual(self.bash(command, mode="auto")[0], "deny")

    def test_an_approval_covers_neither_another_command_nor_another_session(self):
        self.prompt("approve " + approvals.code_for(SESSION, REPLAY_START))
        other = REPLAY_START.replace("request.json", "other.json")
        self.assertEqual(self.bash(other, mode="auto")[0], "deny")
        self.assertEqual(self.bash(REPLAY_START, mode="auto", session="s-other")[0], "deny")

    def test_the_marker_confirms_nothing_in_any_mode(self):
        marked = "HARNESS_CONFIRMED=1 " + REPLAY_START
        self.assertEqual(self.bash(marked, mode="default")[0], "ask")
        for mode in ("auto", "bypassPermissions"):
            with self.subTest(mode=mode):
                decision, reason = self.bash(marked, mode=mode)
                self.assertEqual(decision, "deny")
                self.assertNotIn("HARNESS_CONFIRMED=1 in front", reason)

    def test_codex_refuses(self):
        for mode in ("default", "auto"):
            with self.subTest(mode=mode):
                self.assertEqual(self.bash(REPLAY_START, mode=mode, runtime="codex")[0], "deny")

    def test_a_spend_hidden_where_no_approval_can_name_it_is_refused_in_every_mode(self):
        """Finding 5: inline interpreter text is refused, not let through unread."""
        for command in ('bash -c "citizen draft apply tuning --revision abc"',
                        'echo "$(citizen runs replay start --request r.json)"',
                        "eval citizen draft recover",
                        "xargs citizen draft apply tuning --revision",
                        "python3 -c \"import subprocess; subprocess.run(['citizen','runs','eval',"
                        "'start','--request','r.json'])\"",
                        "node -e \"require('child_process').execSync('citizen draft apply x')\""):
            for mode in ("default", "auto"):
                with self.subTest(command=command, mode=mode):
                    decision, reason = self.bash(command, mode=mode)
                    self.assertEqual(decision, "deny", reason)
                    self.assertIn("as a plain command", reason)

    def test_free_commands_are_not_asked_about(self):
        for command in FREE:
            for mode in ("default", "auto"):
                with self.subTest(command=command, mode=mode):
                    self.assertIsNone(grader.cli_confirmation(command, mode, SESSION))
                    self.assertNotIn(self.bash(command, mode=mode)[0], ("deny", "ask"))

    def test_the_standalone_hook_asks_and_denies_as_the_dispatcher_does(self):
        script = HOOKS / "grade-bash.py"
        for mode, expected in (("default", "ask"), ("auto", "deny"), ("bypassPermissions", "deny")):
            with self.subTest(mode=mode):
                done = subprocess.run(
                    [sys.executable, str(script)], input=json.dumps({
                        "tool_name": "Bash", "tool_input": {"command": REPLAY_START},
                        "permission_mode": mode, "session_id": SESSION, "cwd": str(self.cwd)}),
                    capture_output=True, text=True, env=dict(os.environ), timeout=60)
                out = json.loads(done.stdout)["hookSpecificOutput"]
                self.assertEqual(out["permissionDecision"], expected)


class AgentCannotConfirm(Home):
    """AC2 and AC5: with the person declining, nothing an agent writes, sets or types spends."""

    def setUp(self):
        super().setUp()
        self.asked = []
        declined = mock.patch.object(presence, "confirm",
                                     side_effect=lambda reason: self.asked.append(reason) or False)
        declined.start()
        self.addCleanup(declined.stop)
        # The draft, apply and interrupted apply each names exist, so only the person refuses.
        found = mock.patch.object(harness, "_draft_preflight", return_value=(None, PREFLIGHT))
        found.start()
        self.addCleanup(found.stop)

    def test_a_forged_grant_under_a_relocated_home_does_not_spend(self):
        """Finding 1: a grant file the agent builds under its own HARNESS_HOME."""
        forged = self.home / "forged"
        store = forged / ".local" / "state" / "agent-harness" / "approvals" / "cli-grants"
        store.mkdir(parents=True)
        (store / "grants.json").write_text(json.dumps({"grants": [
            {"key": "x", "via": "approval", "created": 9e12, "used": False}]}))
        os.environ["HARNESS_HOME"] = str(forged)
        with mock.patch.object(harness.studio_apply, "apply") as apply:
            self.refused(*APPLY)
            apply.assert_not_called()
        self.assertEqual(len(self.asked), 1)

    def test_a_pty_fed_yes_does_not_spend(self):
        """Finding 2: a terminal with `yes` typed into it, agent variables unset."""
        class Terminal(io.StringIO):
            def isatty(self):
                return True

        for name in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_SANDBOX", "CODEX_THREAD_ID"):
            os.environ.pop(name, None)
        with mock.patch.object(harness.studio_apply, "apply") as apply, \
                mock.patch.object(harness.sys, "stderr", Terminal()):
            self.refused(*APPLY, stdin=Terminal("yes\n"))
            apply.assert_not_called()

    def test_a_real_pty_fed_yes_to_a_child_cli_does_not_spend(self):
        """Finding 2 end to end: the CLI under a pseudo-terminal with `yes` written to it and the
        presence check on, not switched off. The person declines, and that is why it refuses."""
        directory = Path(tempfile.mkdtemp(prefix="pty-presence-"))
        self.addCleanup(shutil.rmtree, str(directory), True)
        asked = directory / "asked.txt"
        launcher = directory / "declining_cli.py"
        launcher.write_text(DECLINING_CLI % {
            "lib": str(REPO / "lib"), "asked": str(asked), "cli": str(REPO / "bin" / "harness"),
            "reason": PREFLIGHT}, encoding="utf-8")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("CLAUDE", "CODEX")) and k != presence.OFF}
        leader, follower = pty.openpty()
        child = subprocess.Popen([sys.executable, str(launcher), *APPLY],
                                 stdin=follower, stdout=subprocess.PIPE, stderr=follower,
                                 env=env, cwd=str(REPO))
        os.close(follower)
        os.write(leader, b"yes\n")
        out, _ = child.communicate(timeout=120)
        os.close(leader)
        answer = json.loads(out.decode().strip().splitlines()[-1])
        self.assertEqual((child.returncode, answer["error_code"], answer["applied"]),
                         (1, "person-confirmation-required", False))
        self.assertIn("declined, cancelled or failed", answer["message"])
        self.assertNotIn("switched off", answer["message"])
        self.assertEqual(asked.read_text(encoding="utf-8").splitlines(), [PREFLIGHT])

    def test_a_declined_prompt_leaves_nothing_a_rerun_can_use(self):
        """Finding 3: the native prompt declined, then the same words run another way."""
        self.assertEqual(self.bash(REPLAY_START, mode="default")[0], "ask")
        self.assertEqual(self.store_files(), [])
        with mock.patch.object(harness.studio_apply, "apply") as apply:
            self.refused(*APPLY)
            apply.assert_not_called()

    def test_an_approval_from_another_session_still_needs_the_person(self):
        """Finding 3: an approval consumed in one session spends nothing anywhere by itself."""
        command = "citizen " + " ".join(APPLY)
        self.prompt("approve " + approvals.code_for(SESSION, command))
        self.assertEqual(self.bash(command, mode="auto", session="s-other")[0], "deny")
        self.assertNotIn(self.bash(command, mode="auto")[0], ("deny", "ask"))
        with mock.patch.object(harness.studio_apply, "apply") as apply:
            self.refused(*APPLY)
            apply.assert_not_called()

    def test_a_token_environment_variable_marker_or_studio_flag_does_not_confirm(self):
        os.environ.update({"HARNESS_CONFIRMED": "1", "CITIZEN_CONFIRMED": "1"})
        with mock.patch.object(harness.studio_headless, "call") as route, \
                mock.patch.object(harness.studio_apply, "apply") as apply, \
                mock.patch.object(harness.studio_apply, "recover") as recover, \
                mock.patch.object(harness.studio_rollback, "rollback") as rollback:
            self.refused(*APPLY[:-1], "--via-studio", "--json")
            self.refused("draft", "rollback", "c" * 32, "--draft", "tuning", "--json")
            self.refused("draft", "recover", "--draft", "tuning", "--json")
            for mocked in (route, apply, recover, rollback):
                mocked.assert_not_called()

    def test_a_paid_catalog_start_with_a_token_is_refused(self):
        supervisor = mock.Mock()
        supervisor.spend_preview.return_value = {"confirmation_required": True}
        argv = ["runs", "start", "paid-suite", "--target-kind", "installed", "--target-ref",
                "current", "--confirm-spend", "a" * 64, "--json"]
        with mock.patch.object(harness.studio.runs, "RunSupervisor", return_value=supervisor), \
                mock.patch.object(harness.studio_free_suites, "start_refusal", return_value=None):
            self.refused(*argv)
        supervisor.start.assert_not_called()

    def test_the_refusal_points_to_the_studio_dialog(self):
        with mock.patch.object(harness.studio_apply, "apply"):
            said = self.refused(*APPLY)
        self.assertIn("Studio's own dialog", said)
        self.assertEqual(self.confirmations(), [])


class SpendRoutes(Home):
    """Both faces of each paid start check the request, then ask the person, then start."""

    REASON = "start the paid live-replay run of 2 case(s) on installed current vs draft d"

    def call(self, group, body):
        return headless.call(REPO, self.home / "state" / "studio", group, "start", body)

    def admission(self):
        admission = mock.Mock()
        admission.start_confirmed.side_effect = replay.ReplayError("reached the start")
        return admission

    def test_a_declined_person_stops_each_start_before_it_runs(self):
        admission = self.admission()
        native = mock.Mock()
        native.check.return_value = {"suite_id": "native-acceptance", "case_count": 1}
        with mock.patch.object(presence, "confirm", return_value=False), \
                mock.patch.object(server, "_replay_launch", return_value=admission), \
                mock.patch.object(server, "_replay_spend", return_value=self.REASON), \
                mock.patch.object(server, "_eval_admission", return_value=admission), \
                mock.patch.object(server, "_eval_spend", return_value=self.REASON), \
                mock.patch.object(server, "_native_adapter", return_value=native), \
                mock.patch.object(server, "_native_request",
                                  return_value={"selection": {}, "spend": {},
                                                "confirmation_token": "t"}):
            for group, body in (("replay", {"request": {}, "confirmation_token": "t"}),
                                ("eval", {"request": {}, "confirmation_token": "t"}),
                                ("native", {})):
                with self.subTest(group=group):
                    self.assertEqual(self.call(group, body),
                                     (403, {"error": "person_confirmation_required"}))
        admission.start_confirmed.assert_not_called()
        native.start.assert_not_called()

    def test_a_request_that_fails_its_check_never_asks_the_person(self):
        """A wrong token, spend or id is refused before any dialog is raised."""
        admission = self.admission()
        native = mock.Mock()
        native.check.side_effect = runs.RunError("paid run confirmation does not match")
        asked = mock.Mock(return_value=True)
        with mock.patch.object(presence, "confirm", asked), \
                mock.patch.object(server, "_replay_launch", return_value=admission), \
                mock.patch.object(server, "_replay_spend",
                                  side_effect=replay.ReplayError("token mismatch")), \
                mock.patch.object(server, "_eval_admission", return_value=admission), \
                mock.patch.object(server, "_eval_spend", side_effect=eval_tiers.EvalTierError(
                    "token mismatch", "invalid_request")), \
                mock.patch.object(server, "_native_adapter", return_value=native), \
                mock.patch.object(server, "_native_request",
                                  return_value={"selection": {}, "spend": {},
                                                "confirmation_token": "t"}):
            for group, body in (("replay", {"request": {}, "confirmation_token": "t"}),
                                ("eval", {"request": {}, "confirmation_token": "t"}),
                                ("native", {})):
                with self.subTest(group=group):
                    self.assertEqual(self.call(group, body)[0], 400)
        asked.assert_not_called()
        admission.start_confirmed.assert_not_called()
        native.start.assert_not_called()

    def test_the_dialog_names_the_suite_targets_estimate_and_caps(self):
        admission = self.admission()
        asked = []
        launch = {"suite_id": "live-replay", "parameters": {}, "target_kind": "installed",
                  "target_ref": "current", "confirmed": "t", "max_budget_usd": "0.50",
                  "spend_cap_usd": "2.00", "pricing_source": "api_credit",
                  "case_identities": ["a", "b"],
                  "targets": [{"kind": "installed", "ref": "current"},
                              {"kind": "draft", "ref": "tuning"}]}
        admission.supervisor.check_start.return_value = {
            "suite_id": "live-replay", "target_kind": "installed", "target_ref": "current",
            "case_count": 2, "estimate_usd": 0.31, "max_budget_usd": "0.50",
            "spend_cap_usd": "2.00", "pricing_source": "api_credit"}
        with mock.patch.object(presence, "confirm",
                               side_effect=lambda reason: asked.append(reason) or False), \
                mock.patch.object(server, "_replay_launch", return_value=admission), \
                mock.patch.object(server.replay, "launch_payload", return_value=launch):
            self.assertEqual(self.call("replay", {"request": {}, "confirmation_token": "t"})[0],
                             403)
        self.assertEqual(asked, [
            "start the paid live-replay run of 2 case(s) on installed current vs draft tuning: "
            "estimated $0.31, at most $0.50 for the run and $2.00 against the spend cap "
            "(api_credit)"])
        _args, kwargs = admission.supervisor.check_start.call_args
        self.assertEqual((kwargs["confirmed"], kwargs["case_identities"]), ("t", ["a", "b"]))

    def test_a_present_person_reaches_the_start_and_is_recorded_in_activity(self):
        admission = self.admission()
        with mock.patch.object(server, "_replay_launch", return_value=admission), \
                mock.patch.object(server, "_replay_spend", return_value=self.REASON):
            status, _body = self.call("replay", {"request": {}, "confirmation_token": "t"})
        self.assertEqual(status, 400)
        admission.start_confirmed.assert_called_once()
        rows = self.confirmations()
        self.assertEqual([(r["actor"], r["title"]) for r in rows],
                         [("citizen", "Spend or apply confirmed in person")])

    def test_the_draft_test_start_asks_before_it_claims_or_starts(self):
        admission = self.admission()
        draft_tests = server.draft_tests
        asked = []
        with mock.patch.object(presence, "confirm",
                               side_effect=lambda reason: asked.append(reason) or False), \
                mock.patch.object(draft_tests, "parse_plan"), \
                mock.patch.object(draft_tests, "identity"), \
                mock.patch.object(draft_tests, "check_request"), \
                mock.patch.object(draft_tests, "power"), \
                mock.patch.object(draft_tests, "start_registration", return_value=(None, [])), \
                mock.patch.object(server.replay.ReplayRequest, "parse"), \
                mock.patch.object(server, "_replay_spend", return_value=self.REASON), \
                mock.patch.object(server, "_replay_admission", return_value=admission):
            status, body = self.call("draft-test", {"draft": "d", "request": {},
                                                    "confirmation_token": "t", "effect": 0.1,
                                                    "cv": 0.2})
        self.assertEqual((status, body), (403, {"error": "person_confirmation_required"}))
        self.assertEqual(asked, ["test draft d: " + self.REASON])
        admission.start_confirmed.assert_not_called()

    def test_a_studio_apply_rollback_or_recover_holds_no_shared_executor(self):
        """A CLI child waiting on the dialog must not hold other Studio requests."""
        refused = harness.studio_apply._result("refused", "person-confirmation-required", "no")
        refused["applied"] = False
        routes = (("/api/configure/apply", "_run_draft_apply",
                   {"draft": "tuning", "revision": "b" * 40, "confirm": "tuning"}),
                  ("/api/configure/apply/rollback", "_run_draft_rollback",
                   {"apply_id": "c" * 32, "confirm": "tuning"}),
                  ("/api/configure/apply/recover", "_run_draft_recover",
                   {"action": "restore", "confirm": "tuning"}))
        real = mutations.MutationExecutor.call
        inside = []  # one entry per operation the executor is running now

        def tracking(executor, operation):
            def counted():
                inside.append(operation)
                try:
                    return operation()
                finally:
                    inside.pop()
            return real(executor, counted)

        for path, runner, body in routes:
            held = []
            with self.subTest(path=path), \
                    mock.patch.object(mutations.MutationExecutor, "call", tracking), \
                    mock.patch.object(server, runner, side_effect=lambda *a, **k: (
                        held.append(len(inside)), refused)[1]):
                status, _answer = headless.call_route(
                    REPO, self.home / "state" / "studio", "POST", path, body)
                self.assertEqual((status, held), (200, [0]))


class Recorded(Home):
    """AC3: a confirmed CLI apply is in Activity, even with the decision log switched off."""

    def test_a_confirmed_apply_is_recorded_with_decisions_off(self):
        config = self.home / ".config" / "agent-harness" / "config.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"telemetry": {"decisions": False}}))
        with mock.patch.object(harness.studio_apply, "apply", return_value=APPLIED) as apply, \
                mock.patch.object(harness, "_draft_preflight", return_value=(None, PREFLIGHT)):
            code, _printed, _said = self.cli(*APPLY)
        self.assertEqual(code, 0)
        apply.assert_called_once()
        rows = self.confirmations()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["command"], "citizen " + " ".join(APPLY))
        self.assertEqual(rows[0]["actor"], "citizen")
        self.assertEqual(rows[0]["apply_id"], "")

    def test_the_studio_apply_runs_the_cli_which_asks_the_person_itself(self):
        seen = []

        def run(command, **kwargs):
            seen.append(command)
            return subprocess.CompletedProcess(command, 0, stdout='{"status": "x"}\n', stderr="")

        with mock.patch.object(server.subprocess, "run", side_effect=run):
            server._run_draft_apply(REPO, "tuning", "b" * 40)
        self.assertEqual(seen[0][1:], [str(REPO / "bin" / "harness"), *APPLY[:-1],
                                       "--via-studio", "--json"])
        self.assertEqual(self.store_files(), [])


class CliPreflight(Home):
    """The CLI checks what it can without a lock before it asks, and names it in the dialog."""

    def setUp(self):
        super().setUp()
        self.asked = []
        declined = mock.patch.object(presence, "confirm",
                                     side_effect=lambda reason: self.asked.append(reason) or False)
        declined.start()
        self.addCleanup(declined.stop)

    def test_an_unknown_draft_or_stale_revision_is_refused_without_asking(self):
        code, printed, _said = self.cli(*APPLY)
        self.assertEqual((code, json.loads(printed)["error_code"]), (1, "not-found"))
        with mock.patch.object(harness.studio.drafts, "read_snapshot", return_value="f" * 40):
            code, printed, _said = self.cli(*APPLY)
        self.assertEqual((code, json.loads(printed)["error_code"]), (1, "stale-revision"))
        self.assertEqual(self.asked, [])

    def test_the_apply_dialog_names_the_draft_and_revision(self):
        with mock.patch.object(harness.studio.drafts, "read_snapshot", return_value="b" * 40), \
                mock.patch.object(harness.studio_apply, "apply") as apply:
            self.refused(*APPLY)
            apply.assert_not_called()
        self.assertEqual(self.asked, [PREFLIGHT])

    def test_an_unknown_apply_or_nothing_to_recover_is_refused_without_asking(self):
        code, printed, _said = self.cli("draft", "rollback", "c" * 32, "--draft", "tuning",
                                        "--json")
        self.assertEqual((code, json.loads(printed)["error_code"]), (1, "unknown-apply"))
        code, printed, _said = self.cli("draft", "recover", "--draft", "tuning", "--json")
        self.assertEqual((code, json.loads(printed)["error_code"]), (1, "nothing-to-recover"))
        self.assertEqual(self.asked, [])

    def test_the_recover_dialog_names_the_interrupted_apply(self):
        intent = {"apply_id": "e" * 32, "draft": "tuning", "revision": "b" * 40}
        with mock.patch.object(harness.studio_apply, "unfinished_applies", return_value=[intent]), \
                mock.patch.object(harness.studio_apply, "recover") as recover:
            self.refused("draft", "recover", "--draft", "tuning", "--json")
            recover.assert_not_called()
        self.assertEqual(self.asked, ["recover the interrupted apply %s of draft tuning at "
                                      "revision %s" % ("e" * 32, "b" * 40)])

    def test_a_paid_catalog_start_with_a_wrong_token_is_refused_without_asking(self):
        supervisor = mock.Mock()
        supervisor.spend_preview.return_value = {"confirmation_required": True}
        supervisor.check_start.side_effect = harness.studio.runs.RunError(
            "paid run confirmation does not match the exact displayed request")
        argv = ["runs", "start", "paid-suite", "--target-kind", "installed", "--target-ref",
                "current", "--confirm-spend", "a" * 64, "--json"]
        with mock.patch.object(harness.studio.runs, "RunSupervisor", return_value=supervisor), \
                mock.patch.object(harness.studio_free_suites, "start_refusal", return_value=None):
            code, _printed, _said = self.cli(*argv)
        self.assertNotEqual(code, 0)
        self.assertEqual(self.asked, [])
        supervisor.start.assert_not_called()


class PresenceCheck(unittest.TestCase):
    """Only a system answer of `present` from the absolute helper passes; inputs only refuse."""

    def setUp(self):
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))
        os.environ.pop(presence.OFF, None)
        for name in presence.CODEX_VARIABLES:
            os.environ.pop(name, None)
        self.calls = []

    def run_with(self, answer, manager="Aqua\n", system="Darwin"):
        def run(argv, **kwargs):
            self.calls.append((argv, kwargs))
            if argv[0] == presence.LAUNCHCTL:
                return subprocess.CompletedProcess(argv, 0, stdout=manager, stderr="")
            if isinstance(answer, BaseException):
                raise answer
            return subprocess.CompletedProcess(argv, answer[0], stdout=answer[1], stderr="")

        with mock.patch.object(presence.subprocess, "run", side_effect=run), \
                mock.patch.object(presence.platform, "system", return_value=system), \
                mock.patch.object(presence.os, "access", return_value=True):
            return presence.real_confirm("a paid live replay")

    def test_only_a_present_answer_from_the_absolute_helper_passes(self):
        self.assertTrue(self.run_with((0, "present\n")))
        argv, kwargs = self.calls[-1]
        self.assertEqual(argv[:3], ["/usr/bin/osascript", "-l", "JavaScript"])
        self.assertEqual(kwargs["env"], {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"})
        self.assertIn("evaluatePolicyLocalizedReasonReply(2", argv[4])

    def test_a_declined_cancelled_failed_or_timed_out_check_refuses(self):
        for answer in ((0, "declined\n"), (0, "unavailable\n"), (1, "present\n"), (0, ""),
                       subprocess.TimeoutExpired("osascript", 1), OSError("gone")):
            with self.subTest(answer=answer):
                self.assertFalse(self.run_with(answer))

    def test_no_environment_or_planted_helper_changes_what_runs(self):
        planted = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(planted, True))
        fake = Path(planted) / "osascript"
        fake.write_text("#!/bin/sh\necho present\n")
        fake.chmod(0o755)
        os.environ.update({"PATH": planted + os.pathsep + os.environ.get("PATH", ""),
                           "HARNESS_CONFIRMED": "1", "HARNESS_HOME": planted,
                           "MODEL_CITIZEN_PRESENT": "1", "PYTHONPATH": planted})
        self.assertFalse(self.run_with((0, "declined\n")))
        argv, kwargs = self.calls[-1]
        self.assertEqual(argv[0], "/usr/bin/osascript")
        self.assertNotIn(planted, json.dumps(kwargs["env"]))

    def test_nothing_in_the_repository_replaces_the_check_outside_the_suite_harness(self):
        """No tracked file, tests included, swaps `presence.confirm` except the suite's own
        in-process stub, its temp-dir launcher source and this module's declining child; and no
        runnable stub CLI is tracked at all."""
        allowed = {"tests/isolation.py", "tests/presence_support.py",
                   "tests/test_cli_confirmation.py"}
        # Built from parts, so this test's own text is not what it finds.
        patterns = ("presence" + ".confirm =", "presence" + ".confirm=",
                    "setattr(" + "presence", "real_" + "confirm", "presence" + "_stub")
        tracked = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"], capture_output=True,
                                 check=True).stdout.decode("utf-8").split("\0")
        offenders = []
        for name in filter(None, tracked):
            if "stub_cli" in name or "presence_stub" in name:
                offenders.append(name)
                continue
            path = REPO / name
            if name in allowed or not path.is_file() or path.suffix not in ("", ".py", ".sh"):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeError, OSError):
                continue
            if any(pattern in text for pattern in patterns):
                offenders.append(name)
        self.assertEqual(offenders, [])
        self.assertGreater(len(tracked), 100)

    def test_where_presence_cannot_be_checked_it_refuses_without_asking(self):
        for setup, manager, system in ((lambda: None, "Aqua\n", "Linux"),
                                       (lambda: None, "Background\n", "Darwin"),
                                       (lambda: os.environ.update({"CODEX_SANDBOX": "seatbelt"}),
                                        "Aqua\n", "Darwin"),
                                       (lambda: os.environ.update({presence.OFF: "1"}),
                                        "Aqua\n", "Darwin")):
            with self.subTest(system=system, manager=manager):
                os.environ.pop("CODEX_SANDBOX", None)
                os.environ.pop(presence.OFF, None)
                setup()
                self.calls = []
                self.assertFalse(self.run_with((0, "present\n"), manager, system))
                self.assertFalse(any(argv[0] == presence.OSASCRIPT for argv, _ in self.calls))


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: MIT
"""Every scripted acceptance case's readers, driven against recorded shapes instead of a client.

No test here launches a client, installs anything, or reaches the network. What is tested is the
part of each case that decides: how a merged settings table is read, what a stance link resolves
to, which review layers carry a role declaration, what a Codex event stream and rollout say, and
that a surface this runner has never been observed against cannot report a pass.

The Codex fixtures under `fixtures/qualification/codex/` are hand-authored to the shapes
`adapters/codex/worker.py` and `policy/hooks/usage-log.py` parse, with placeholder identifiers.
They are derived from those readers and from docs/usage.md, **not** recorded from a Codex run:
no Codex round has been driven through this runner, which is why every Codex verdict is
`unverified` until an operator confirms the home against a hand run.
"""
import io
import json
import os
import shlex
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from test_harness import REPO
from test_native_acceptance import MODULE
from test_native_acceptance_runner import home_in, stub_init

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "qualification"
CODEX = FIXTURES / "codex"
PARENT_THREAD = "00000000-0000-7000-8000-0000000000c1"
CHILD_THREAD = "00000000-0000-7000-8000-0000000000c2"
CLAUDE = "claude-code-cli-macos"
CODEX_CLIENT = "codex-cli-macos"


def codex_home():
    """A CodexHome whose configuration home is the fixture tree, with no client behind it."""
    home = MODULE.CodexHome.__new__(MODULE.CodexHome)
    home.root = CODEX
    home.client_dir = CODEX
    home.model = "cheapest"
    home.keep = True
    home.launched = 0
    return home


class RegistrationTests(unittest.TestCase):
    def test_every_required_case_is_now_scripted(self):
        required = MODULE.catalog()["required_cases"]
        self.assertEqual(sorted(MODULE.CASES), sorted(required))

    def test_no_plan_still_says_a_case_is_not_automated(self):
        for client in sorted(MODULE.CLIENTS):
            printed = MODULE.plan(client, MODULE.catalog()["required_cases"], "cheapest")
            self.assertNotIn(MODULE.NOT_AUTOMATED, printed)

    def test_each_case_describes_what_it_reads_rather_than_what_it_configures(self):
        for name, (function, how) in sorted(MODULE.CASES.items()):
            self.assertTrue(callable(function), name)
            self.assertTrue(how.strip() and not how.endswith("."), name)


class ClientSurfaceTests(unittest.TestCase):
    def test_a_codex_surface_names_its_own_configuration_home(self):
        self.assertEqual(MODULE.CLIENTS[CODEX_CLIENT]["home_var"], "CODEX_HOME")
        self.assertEqual(MODULE.HOMES["codex"], MODULE.CodexHome)

    def test_the_disposable_home_exports_whichever_variable_that_surface_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            home = home_in(directory)
            home.home_var = "CODEX_HOME"
            with patch.dict(MODULE.os.environ, {"PATH": "/usr/bin"}, clear=True):
                env = home.env()
        self.assertEqual(env["CODEX_HOME"], str(home.client_dir))
        self.assertNotIn("CLAUDE_CONFIG_DIR", env)

    def test_an_unobserved_surface_says_so_and_an_observed_one_does_not(self):
        self.assertIn("CODEX_HOME", MODULE.unobserved_note(CODEX_CLIENT, []))
        self.assertEqual(MODULE.unobserved_note(CODEX_CLIENT, [CODEX_CLIENT]), "")
        self.assertEqual(MODULE.unobserved_note(CLAUDE, []), "")

    def test_confirming_one_target_confirms_no_other(self):
        self.assertIn("CODEX_HOME", MODULE.unobserved_note(CODEX_CLIENT, ["codex-cli-linux"]))
        self.assertEqual(MODULE.confirmed_targets([CODEX_CLIENT]), frozenset([CODEX_CLIENT]))
        with self.assertRaises(SystemExit) as caught:
            MODULE.confirmed_targets(["no-such-client"])
        self.assertIn("no-such-client", str(caught.exception))

    def test_an_unobserved_surface_cannot_report_a_pass(self):
        def case(home):
            return "the assertion held"

        with patch.dict(MODULE.CASES, {"installation": (case, "canned")}), \
                patch.object(MODULE.CodexHome, "__init__", stub_init), \
                patch.object(MODULE.CodexHome, "discard", lambda self: None, create=True):
            outcome = MODULE.probe(CODEX_CLIENT, "installation", "cheapest", False)
            confirmed = MODULE.probe(CODEX_CLIENT, "installation", "cheapest", False,
                                     [CODEX_CLIENT])
        self.assertEqual(outcome["result"], "unverified")
        self.assertIn("the assertion held", outcome["observation"])
        self.assertIn("not been confirmed", outcome["observation"])
        self.assertEqual(confirmed["result"], "passed")


class CodexStreamTests(unittest.TestCase):
    def setUp(self):
        self.events = MODULE.codex_events((CODEX / "exec-stream.jsonl").read_text())

    def test_the_stream_is_read_past_lines_that_are_not_events(self):
        self.assertEqual(len(self.events), 4)
        self.assertEqual(MODULE.codex_events("not json\n\n"), [])

    def test_the_last_thing_the_model_said_is_the_answer(self):
        self.assertEqual(MODULE.codex_answer(self.events), "PLAIN")

    def test_a_stream_that_said_nothing_is_read_as_nothing(self):
        self.assertEqual(MODULE.codex_answer([{"type": "thread.started"}]), "")

    def test_the_thread_id_comes_from_the_stream_itself(self):
        self.assertEqual(codex_home().thread_id(self.events), PARENT_THREAD)


class CodexRolloutTests(unittest.TestCase):
    def setUp(self):
        self.home = codex_home()

    def test_both_rollouts_are_found_under_the_configuration_home(self):
        self.assertEqual(len(self.home.rollouts()), 2)

    def test_the_thread_is_identified_by_its_own_first_session_meta(self):
        records = self.home.rollout_records(PARENT_THREAD)
        self.assertTrue(records)
        self.assertEqual(MODULE.rollout_thread(records), PARENT_THREAD)

    def test_a_top_level_thread_is_not_a_spawn_and_a_spawned_one_is(self):
        self.assertIsNone(MODULE.rollout_spawn(self.home.rollout_records(PARENT_THREAD)))
        spawn = MODULE.rollout_spawn(self.home.rollout_records(CHILD_THREAD))
        self.assertEqual(spawn["parent_thread_id"], PARENT_THREAD)

    def test_a_spawned_thread_is_read_as_its_parents_subagent(self):
        found = self.home.subagents(PARENT_THREAD)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0]["agentType"], "amber-fox")

    def test_a_thread_that_spawned_nothing_has_no_subagents(self):
        self.assertEqual(self.home.subagents(CHILD_THREAD), [])

    def test_an_unknown_thread_reads_as_nothing_observed(self):
        self.assertEqual(self.home.rollout_records("00000000-0000-7000-8000-00000000ffff"), [])
        self.assertEqual(self.home.orchestrator_text("00000000-0000-7000-8000-00000000ffff"), "")

    def test_the_orchestrator_text_carries_what_the_thread_said(self):
        self.assertIn("SPAWNED", self.home.orchestrator_text(PARENT_THREAD))


class InstallationRolesTests(unittest.TestCase):
    def home(self, runtime, roles):
        home = Mock(runtime=runtime)
        home.harness.side_effect = [MODULE.SYNC_DONE, MODULE.NO_DRIFT]
        home.session.side_effect = [MODULE.FIXTURE_NAME, "YES", "\n".join(roles)]
        home.answer.side_effect = lambda value: value
        return home

    def test_both_clients_are_asked_for_and_judged_on_their_projected_roles(self):
        roles = sorted(path.stem for path in (MODULE.ROOT / "primitives" / "roles").glob("*.md"))
        self.assertTrue(roles)
        for runtime, prompt in (("claude-code", MODULE.ROLES_PROMPT),
                                ("codex", MODULE.CODEX_ROLES_PROMPT)):
            with self.subTest(runtime=runtime):
                home = self.home(runtime, roles + ["default", "explorer"])
                result = MODULE.case_installation(home)
                home.session.assert_called_with(prompt, tools=())
                self.assertIn("all %s harness roles" % len(roles), result)

    def test_codex_cannot_pass_with_a_missing_projected_role(self):
        roles = sorted(path.stem for path in (MODULE.ROOT / "primitives" / "roles").glob("*.md"))
        home = self.home("codex", roles[1:])
        with self.assertRaisesRegex(AssertionError, "starting with " + roles[0]):
            MODULE.case_installation(home)
        self.assertEqual(home.session.call_count, 3)
    def test_a_similarly_named_role_does_not_satisfy_a_missing_role(self):
        roles = sorted(path.stem for path in (MODULE.ROOT / "primitives" / "roles").glob("*.md"))
        self.assertIn("spec-reviewer", roles)
        home = self.home("codex", ["`" + name + "`" for name in roles if name != "reviewer"])
        with self.assertRaisesRegex(AssertionError, "starting with reviewer"):
            MODULE.case_installation(home)



class RuntimeGapTests(unittest.TestCase):
    """A record one runtime never writes is a named gap, never an assertion that holds vacuously."""

    def test_claude_code_has_no_gap(self):
        self.assertEqual(MODULE.native_only(home_in("/tmp"), "a spawn"), "")

    def test_another_runtime_names_itself_and_what_was_not_read(self):
        home = home_in("/tmp")
        home.runtime = "codex"
        gap = MODULE.native_only(home, "a spawn's subagent transcript")
        self.assertIn("codex", gap)
        self.assertIn("subagent transcript", gap)


class HookCompositionReaderTests(unittest.TestCase):
    def setUp(self):
        self.settings = json.loads(
            (FIXTURES / "claude-code" / "merged-settings.json").read_text())

    def test_the_merged_table_holds_both_the_coordinator_and_the_user_entry(self):
        coordinator, user = MODULE.user_hook_entries(self.settings, "/tmp/probe/user-hook.py")
        self.assertTrue(coordinator)
        self.assertTrue(user)

    def test_a_dropped_user_entry_is_visible(self):
        self.settings["hooks"]["PostToolUse"] = self.settings["hooks"]["PostToolUse"][:1]
        self.assertEqual(MODULE.user_hook_entries(self.settings, "/tmp/probe/user-hook.py"),
                         (True, False))

    def test_a_settings_file_with_no_hooks_at_all_reads_as_neither(self):
        self.assertEqual(MODULE.user_hook_entries({}, "/tmp/probe/user-hook.py"), (False, False))


class StanceLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = home_in(self.tmp.name)
        self.directory = self.home.client_dir / "rules" / "harness-stances"
        self.directory.mkdir(parents=True)

    def test_a_resolved_link_reports_the_variant_it_points_at(self):
        variant = self.home.root / "tiered.md"
        variant.write_text("# tiered\n")
        os.symlink(str(variant), str(self.directory / "delegation.md"))
        self.assertEqual(MODULE.link_target(MODULE.stance_link(self.home, "delegation")),
                         "tiered.md")

    def test_a_file_that_is_not_a_link_reports_nothing_rather_than_guessing(self):
        (self.directory / "voice.md").write_text("# not a link\n")
        self.assertEqual(MODULE.link_target(MODULE.stance_link(self.home, "voice")), "")

    def test_an_absent_link_reports_nothing(self):
        self.assertEqual(MODULE.link_target(MODULE.stance_link(self.home, "cost")), "")


class SpawnConfinementTests(unittest.TestCase):
    """The case reads the integration descriptor rather than restating what it declares."""

    def setUp(self):
        self.data, self.spawn = MODULE.descriptor_spawn()

    def test_the_declared_spawn_comes_from_a_committed_descriptor_that_validates(self):
        self.assertEqual(MODULE.frameworks.problems(self.data), [])
        self.assertTrue(self.spawn["phrases"])

    def test_no_valid_descriptor_is_unverified_rather_than_a_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "broken.json").write_text("{not json")
            Path(directory, "empty.json").write_text(json.dumps({"spawns": []}))
            with self.assertRaises(MODULE.Unverified) as caught:
                MODULE.descriptor_spawn(directory)
        self.assertIn("no valid integration descriptor", str(caught.exception))

    def test_the_refusal_clauses_are_the_ones_the_harness_actually_writes(self):
        source = (REPO / "lib" / "harness_core" / "frameworks.py").read_text()
        self.assertIn(MODULE.FRAMEWORK_ORIGIN, source)
        self.assertIn(MODULE.FRAMEWORK_ROOTS, source)


class HandoffRevisionTests(unittest.TestCase):
    """The revision sequence the handoff case sends, against the rule that decides it.

    `tasks.save` requires `--revision` to be the record's current revision and writes the next,
    so a save repeated against a revision that has been spent is the refusal, and the same save
    against the current one is the next revision. The case sent the same revision twice and
    expected a refusal second, which no record can ever produce.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        (self.repo / "progress.txt").write_text("start" + chr(10))
        for args in (("init", "-q"), ("add", "-A"),
                     ("-c", "user.email=" + MODULE.PROBE_EMAIL, "-c", "user.name=probe",
                      "commit", "-qm", "probe")):
            MODULE.run(["git", "-C", str(self.repo)] + list(args))

    def save(self, runtime, revision):
        from harness_core import tasks
        return tasks.save(self.repo, json.loads(MODULE.task_contract()), runtime, revision)

    def test_the_first_save_writes_revision_one_and_the_next_writes_two(self):
        self.assertEqual(self.save("claude-code", 0)["revision"], 1)
        self.assertEqual(MODULE.task_revision(self.repo), 1)
        self.assertEqual(self.save("codex", 1)["revision"], 2)
        self.assertEqual(MODULE.task_revision(self.repo), 2)

    def test_a_save_against_a_spent_revision_is_the_one_that_is_refused(self):
        self.save("claude-code", 0)
        self.save("codex", 1)
        with self.assertRaises(ValueError) as caught:
            self.save("codex", 1)
        self.assertIn(MODULE.STALE_SAVE, str(caught.exception))

    def test_repeating_the_current_revision_is_not_a_refusal(self):
        # What the case used to assert: this is the save that succeeds, so it could never pass.
        self.save("claude-code", 0)
        self.assertEqual(self.save("claude-code", 1)["revision"], 2)

    def test_an_absent_record_reads_as_no_revision_rather_than_zero(self):
        self.assertIsNone(MODULE.task_revision(self.repo))


class GradeDenyTests(unittest.TestCase):
    """The hook-composition case must read a hook's decision, not the stance name in prose."""

    def test_the_marker_is_text_only_the_grade_bash_hook_writes(self):
        hook = (REPO / "policy" / "hooks" / "grade-bash.py").read_text()
        self.assertIn('HOOK = "grade-bash hook"', hook)
        self.assertIn("autonomy=%s", hook)
        self.assertIn("grade-bash hook, autonomy=", MODULE.GRADE_DENY)

    def test_the_stance_name_alone_is_not_the_marker(self):
        self.assertNotIn("autonomy=ask", "a reply that merely mentions the selected variant")
        self.assertNotIn(MODULE.GRADE_DENY, "I cannot do that because autonomy=ask is selected.")

    def test_a_real_deny_reason_carries_the_marker(self):
        reason = ("grade 1, local write: touch ./compose.txt writes to the working tree — this "
                  "changes files on this machine (grade-bash hook, autonomy=ask)")
        self.assertIn(MODULE.GRADE_DENY, reason)


class CodexDenialTests(unittest.TestCase):
    """A Codex turn's refused calls, read from its event stream rather than assumed absent."""

    def test_a_rejected_approval_is_read_as_a_denial(self):
        denials = MODULE.codex_denials([
            {"msg": {"type": "exec_approval_request", "decision": "rejected",
                     "command": "touch ./probe.txt"}}])
        self.assertEqual(len(denials), 1)
        self.assertEqual(denials[0]["tool_name"], "touch ./probe.txt")

    def test_a_clean_stream_records_no_denial(self):
        self.assertEqual(MODULE.codex_denials(
            [{"msg": {"type": "agent_message", "message": "DONE"}}]), [])

    def test_a_turn_the_client_failed_is_unobserved_rather_than_answered(self):
        home = codex_home()
        home.project = CODEX
        home.env = lambda extra=None: {}
        stream = (CODEX / "exec-stream.jsonl").read_text()
        finished = type("R", (), {"stdout": stream, "stderr": "boom", "returncode": 1})()
        with patch.object(MODULE, "run", return_value=finished):
            with self.assertRaises(MODULE.Unverified) as caught:
                home.session("anything", tools=())
        self.assertIn("exited 1", str(caught.exception))


class ProvisionedRoundTests(unittest.TestCase):
    """The committed provisioning and driver scripts, read without running either end to end."""

    def setUp(self):
        self.provision = MODULE_FOR("qualification_provision")
        self.driver = MODULE_FOR("qualification_round")

    def test_the_round_exports_only_the_checkout_it_provisioned(self):
        self.assertEqual(self.provision.environment({"bmad": "/round/bmad"}),
                         {self.provision.BMAD_ENV: "/round/bmad"})
        self.assertEqual(self.provision.environment({"bmad": None}), {})

    def test_a_round_directory_inside_any_checkout_of_this_repository_is_refused(self):
        with self.assertRaises(SystemExit) as caught:
            self.provision.main(["--out", str(REPO / "inside")])
        self.assertIn("outside every checkout of this repository", str(caught.exception))
        self.assertTrue(self.provision.inside_this_repository(REPO / "deep" / "inside"))

    def test_a_directory_in_no_checkout_of_this_repository_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(self.provision.inside_this_repository(Path(directory) / "round"))

    def test_the_clone_flag_git_accepts_is_the_one_used(self):
        # `--shared=false` is rejected by git: the option takes no value, so every clone exited.
        source = (REPO / "scripts" / "qualification_provision.py").read_text()
        self.assertIn("--no-shared", source)
        self.assertNotIn("--shared=false", source)

    def test_a_clone_is_taken_at_the_commit_asked_for(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "round"
            out.mkdir()
            commit = self.provision.head()
            target = self.provision.clone(out, commit)
            self.assertEqual(self.provision.git("rev-parse", "HEAD",
                                                repo=target).stdout.strip(), commit)
            self.assertTrue((target / self.provision.CLONE_MARKER).is_file())

    def test_a_clone_directory_this_script_did_not_create_is_never_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "round"
            (out / self.provision.CLONE).mkdir(parents=True)
            (out / self.provision.CLONE / "somebody-elses-work.txt").write_text("keep me\n")
            with self.assertRaises(SystemExit) as caught:
                self.provision.clone(out, self.provision.head())
            self.assertIn("move it aside", str(caught.exception))
            self.assertTrue((out / self.provision.CLONE / "somebody-elses-work.txt").exists())

    def test_every_exported_value_is_quoted_for_a_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "round"
            out.mkdir()
            (out / "provision.json").write_text(json.dumps(
                {"bmad": "/tmp/a round/bmad; echo pwned"}))
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                self.provision.main(["--out", str(out), "--print-env"])
        self.assertIn(shlex.quote("/tmp/a round/bmad; echo pwned"), buffer.getvalue())
        self.assertNotIn("; echo pwned\n", buffer.getvalue())

    def test_the_bmad_installer_is_pinned_rather_than_floating(self):
        self.assertRegex(self.provision.BMAD_INSTALLER, r"@\d+\.\d+\.\d+$")

    def test_a_target_argv_names_the_frozen_clones_own_runner(self):
        argv = self.driver.target_argv("/round/clone", CLAUDE, "cheapest", "/round/out.json",
                                       [CLAUDE], {"execution_class": "standard",
                                                  "assessment_class": "strong"})
        self.assertIn("/round/clone/scripts/native_acceptance.py", argv)
        self.assertEqual(argv[argv.index("--home-confirmed") + 1], CLAUDE)

    def test_confirmation_is_not_passed_through_for_a_target_nobody_confirmed(self):
        argv = self.driver.target_argv("/round/clone", CODEX_CLIENT, None, "/round/out.json",
                                       [CLAUDE], {"execution_class": "standard",
                                                  "assessment_class": "strong"})
        self.assertNotIn("--home-confirmed", argv)

    def test_confirming_every_surface_at_once_is_refused(self):
        with self.assertRaises(SystemExit) as caught:
            MODULE.confirmed_targets(True)
        self.assertIn("every surface at once", str(caught.exception))

    def test_a_target_with_no_record_is_reported_rather_than_assumed_green(self):
        summary = self.driver.summarise({CLAUDE: {"cases": {}},
                                         CODEX_CLIENT: {"cases": {"installation": "unverified"}}})
        self.assertIn("no record", summary)
        self.assertIn("not installation", summary)

    def test_an_unknown_target_is_refused_by_name(self):
        with self.assertRaises(SystemExit) as caught:
            self.driver.main(["--round", "/round", "--targets", "no-such-client"])
        self.assertIn("no-such-client", str(caught.exception))

    def test_a_runner_that_wrote_no_record_reports_every_case_unobserved(self):
        nothing = self.driver.nothing_observed("the runner wrote no record for this target")
        self.assertEqual(sorted(nothing["cases"]), sorted(MODULE.catalog()["required_cases"]))
        self.assertTrue(all(value == "unverified" for value in nothing["cases"].values()))

    def test_a_stale_record_is_moved_aside_rather_than_reported_as_this_run(self):
        with tempfile.TemporaryDirectory() as directory:
            round_dir, clone, records = self.provisioned(directory)
            stale = records / (CLAUDE + ".json")
            stale.write_text(json.dumps({"cases": {"installation": "passed"},
                                         "observations": ["from an older round"]}))
            with patch.object(self.driver.subprocess, "run", return_value=None):
                result = self.driver.run_round(round_dir, [CLAUDE], None, [], skip_smoke=True)
        cases = result["targets"][CLAUDE]["cases"]
        self.assertTrue(all(value == "unverified" for value in cases.values()))
        self.assertNotIn("from an older round",
                         json.dumps(result["targets"][CLAUDE]["observations"]))

    def test_a_target_that_ran_past_the_deadline_is_carried_rather_than_raised(self):
        timeout = self.driver.subprocess.TimeoutExpired(cmd="runner", timeout=1)
        with tempfile.TemporaryDirectory() as directory:
            round_dir, clone, records = self.provisioned(directory)
            with patch.object(self.driver.subprocess, "run", side_effect=timeout):
                result = self.driver.run_round(round_dir, [CLAUDE, CODEX_CLIENT], None, [],
                                               skip_smoke=True)
        self.assertEqual(sorted(result["targets"]), sorted([CLAUDE, CODEX_CLIENT]))
        self.assertIn("past the round deadline",
                      json.dumps(result["targets"][CLAUDE]["observations"]))

    def test_a_smoke_tier_that_ran_past_the_deadline_is_not_a_pass(self):
        timeout = self.driver.subprocess.TimeoutExpired(cmd="smoke", timeout=1)
        with patch.object(self.driver.subprocess, "run", side_effect=timeout):
            self.assertIsNone(self.driver.smoke("/round/clone", {}))

    def provisioned(self, directory):
        """A round directory whose provision record points at empty places, launching nothing."""
        round_dir = Path(directory)
        clone = round_dir / "clone"
        records = round_dir / "records"
        for path in (clone, records):
            path.mkdir(parents=True)
        (round_dir / "provision.json").write_text(json.dumps(
            {"clone": str(clone), "records": str(records), "bmad": None,
             "source_commit": "a" * 40}))
        return round_dir, clone, records


def MODULE_FOR(name):
    """One of the committed round scripts, loaded the way the runner's own tests load theirs."""
    import importlib.util
    path = REPO / "scripts" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()

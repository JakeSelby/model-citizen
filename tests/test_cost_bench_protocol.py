"""The experiment protocol's refusals in the replay (docs/evidence-standard.md): an arm whose
manifest differs from its declaration, carries configuration no component declared, or names a
host path is refused before it launches; so is a run that is neither pre-registered nor
exploratory. No test here builds an image, launches an agent or calls a model."""
import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from test_cost_bench import BENCH, TASK, Launch, arm_record, options
from test_cost_bench_tags import FakeArms, fake_replay, harness_repo, replay_args
from test_experiment_protocol import TEMPLATE, commit_plan

ARMS = BENCH.arms
PLAN = {"evidence": "pre-registered", "pre_registration": "benchmarks/preregistrations/2026-10-01-x.md",
        "pre_registration_commit": "f" * 40}


def admitted(arm="harness", **protocol):
    return dict(arm_record(arm), protocol=dict(PLAN, **protocol))


def refusal(record):
    with redirect_stderr(io.StringIO()):
        try:
            ARMS.admit(record)
        except SystemExit as stop:
            return str(stop)
    return None


def rehash(record):
    record["declaration_sha256"] = ARMS.digest(record["declaration"])
    record["manifest_sha256"] = ARMS.digest(record["manifest"])
    return record


class DeclarationTests(unittest.TestCase):
    def test_an_arm_holding_exactly_its_declaration_is_admitted(self):
        self.assertIsNone(refusal(admitted("bare")))
        self.assertIsNone(refusal(admitted("harness")))

    def test_a_different_claude_code_is_refused(self):
        record = admitted()
        record["manifest"] = dict(record["manifest"], claude_code_version="2.0")
        self.assertIn("holds Claude Code '2.0'", refusal(record))

    def test_an_undeclared_agent_client_is_refused(self):
        record = admitted("bare")
        record["manifest"] = dict(record["manifest"], cli_packages=record["manifest"]["cli_packages"]
                                  + ["@openai/codex" + "@1.0"])
        self.assertIn("agent clients", refusal(record))

    def test_a_different_harness_commit_or_a_stray_checkout_is_refused(self):
        record = admitted()
        record["manifest"] = dict(record["manifest"], harness_commit="e" * 40)
        self.assertIn("harness commit", refusal(record))
        bare = admitted("bare")
        bare["manifest"] = dict(bare["manifest"], roots={"home": "/home/agent", "harness": "/opt/model-citizen"})
        self.assertIn("harness checkout", refusal(bare))


class InheritedConfigurationTests(unittest.TestCase):
    def with_entry(self, arm, kind, entry):
        record = copy.deepcopy(admitted(arm))
        record["manifest"]["entries"].append(entry)
        record["manifest"]["summary"].setdefault(kind, []).append(entry["path"])
        return rehash(record)

    def test_a_hook_or_setting_in_the_bare_arm_is_refused(self):
        hook = {"path": "home:.claude/hooks/stop.py", "kind": "file", "sha256": "0"}
        self.assertIn("hooks entry home:.claude/hooks/stop.py", refusal(self.with_entry("bare", "hooks", hook)))
        settings = {"path": "home:.claude/settings.json", "kind": "file", "sha256": "0"}
        self.assertIn("settings entry", refusal(self.with_entry("bare", "settings", settings)))
        managed = {"path": "managed:managed-settings.json", "kind": "file", "sha256": "0"}
        self.assertIn("settings entry", refusal(self.with_entry("bare", "settings", managed)))

    def test_a_harness_arm_hook_that_is_not_the_harness_is_refused(self):
        stray = {"path": "home:.claude/hooks/extra.py", "kind": "file", "sha256": "0"}
        self.assertIn("not in the declaration", refusal(self.with_entry("harness", "hooks", stray)))
        elsewhere = {"path": "home:.claude/rules/mine", "kind": "link", "target": "/srv/rules"}
        self.assertIn("not in the declaration", refusal(self.with_entry("harness", "rules", elsewhere)))

    def test_the_files_the_harness_sync_writes_are_part_of_its_component(self):
        settings = {"path": "home:.claude/settings.json", "kind": "file", "sha256": "0"}
        self.assertIsNone(refusal(self.with_entry("harness", "settings", settings)))

    def test_an_entry_omitted_from_the_manifest_summary_is_refused(self):
        record = copy.deepcopy(admitted("harness"))
        record["manifest"]["entries"].append(
            {"path": "home:.claude/hooks/hidden.py", "kind": "file", "sha256": "0"})
        self.assertIn("summary does not match", refusal(record))


class HostPathTests(unittest.TestCase):
    def test_a_recorded_input_naming_the_host_home_is_refused(self):
        record = copy.deepcopy(admitted())
        record["declaration"]["base_image"] = str(Path.home() / "images" / "base.tar")
        self.assertIn("names the host path", refusal(record))

    def test_a_link_into_the_host_checkout_is_refused(self):
        record = copy.deepcopy(admitted())
        record["manifest"]["entries"].append({"path": "home:.bashrc", "kind": "link",
                                              "target": str(ARMS.ROOT / "AGENTS.md")})
        self.assertIn("names the host path", refusal(record))

    def test_an_image_home_that_matches_the_host_home_is_not_a_host_input(self):
        record = copy.deepcopy(admitted())
        record["manifest"]["roots"]["home"] = str(Path.home())
        self.assertIsNone(refusal(rehash(record)))

    def test_a_mount_or_variable_reaching_the_host_is_refused_at_launch(self):
        for workdir in (Path.home() / "snap", ARMS.ROOT / "benchmarks"):
            with self.assertRaises(SystemExit) as caught:
                ARMS.run_command("img", str(workdir), ["true"], "none")
            self.assertIn("under the host path", str(caught.exception))
        home_snapshot = str(Path.home() / "snap")
        for build in (lambda: ARMS.workdir_probe_command("img", home_snapshot, name="probe-1"),
                      lambda: ARMS.check_command("img", home_snapshot, ["true"], name="check-1")):
            with self.assertRaises(SystemExit):  # the mount probe and the named check containers too
                build()
        with self.assertRaises(SystemExit) as caught:
            ARMS.run_command("img", None, ["true"], "none", env={"HOME": str(Path.home())})
        self.assertIn("the variable HOME", str(caught.exception))
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/srv/profile"}):
            with self.assertRaises(SystemExit):
                ARMS.run_command("img", "/srv/profile/inner", ["true"], "none")

    def test_a_temporary_snapshot_mounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            command = ARMS.run_command("img", tmp, ["true"], "none", env={"TERM": "dumb"})
        self.assertIn("%s:%s" % (tmp, ARMS.WORKDIR), command)


class PreRegistrationAdmissionTests(unittest.TestCase):
    def test_a_run_with_no_protocol_stamp_is_refused(self):
        self.assertIn("pre-registration", refusal(dict(arm_record("bare"))))

    def test_a_pre_registered_stamp_without_its_commit_is_refused(self):
        self.assertIn("pre-registration", refusal(admitted(pre_registration_commit=None)))

    def test_an_exploratory_run_is_admitted(self):
        self.assertIsNone(refusal(dict(arm_record("bare"), protocol={"evidence": "exploratory"})))

    def test_replay_refuses_an_unstamped_run_before_anything_launches(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = Launch([])
            opts = options(tmp)
            opts["stamp"] = {"date": "2026-01-01"}
            with self.assertRaises(SystemExit) as caught:
                BENCH.replay([TASK], opts, launch)
            self.assertIn("pre-registration", str(caught.exception))
            self.assertEqual(launch.calls, [])

    def test_replay_refuses_an_arm_whose_manifest_differs_from_its_declaration(self):
        """The seam runs every check on the record the replay was given, protocol included."""
        with tempfile.TemporaryDirectory() as tmp:
            opts = options(tmp)
            opts["arms"]["harness"] = dict(opts["arms"]["harness"],
                                           manifest=dict(opts["arms"]["harness"]["manifest"], claude_code_version="9"))
            launch = Launch([])
            with self.assertRaises(SystemExit) as caught:
                BENCH.replay([TASK], opts, launch)
            self.assertIn("holds Claude Code '9'", str(caught.exception))
            self.assertEqual(launch.calls, [])


class ReplayCliTests(unittest.TestCase):
    """The replay's command line refuses a run that is neither pre-registered nor exploratory."""

    def main(self, tmp, *extra):
        tasks = Path(tmp) / "tasks.json"
        tasks.write_text(json.dumps({"tasks": [TASK]}), encoding="utf-8")
        err, out = io.StringIO(), io.StringIO()
        argv = ["replay", "--tasks", str(tasks), "--model", "claude-test", "--tag", "v1", "--dry-run"] + list(extra)
        with mock.patch.object(BENCH, "ROOT", Path(tmp) / "repo"), redirect_stderr(err), redirect_stdout(out):
            try:
                code = BENCH.main(argv)
            except SystemExit as stop:
                code = stop.code if isinstance(stop.code, int) else str(stop.code)
        return code, out.getvalue(), err.getvalue()

    def test_a_replay_with_neither_flag_is_refused_before_anything_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            with mock.patch.object(BENCH, "schedule", side_effect=AssertionError("nothing is scheduled")):
                code, out, err = self.main(tmp)
            self.assertEqual(code, 2)
            self.assertIn("needs --pre-registration", err)
            self.assertEqual(out, "")

    def test_both_flags_are_an_argument_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, _ = self.main(tmp, "--exploratory", "--pre-registration", "x.md")
            self.assertEqual(code, 2)

    def test_an_unfilled_pre_registration_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = harness_repo(Path(tmp) / "repo")
            plan = commit_plan(repo, text=TEMPLATE)
            code, _, err = self.main(tmp, "--pre-registration", str(plan))
            self.assertEqual(code, 2)
            self.assertIn("unfilled", err)

    def test_an_exploratory_dry_run_proceeds_and_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            code, out, err = self.main(tmp, "--exploratory")
            self.assertEqual(code, 0)
            self.assertIn("exploratory run", err)
            self.assertIn("demo rep 1", out)

    def test_verify_tasks_needs_no_pre_registration(self):
        """Proving the checks calls no model and runs no arm, so the protocol does not govern it."""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(BENCH, "verify_command", lambda args, tasks: 0):
                code, _, _ = self.main(tmp, "--verify-tasks")
            self.assertEqual(code, 0)


class ReplayHistoryTests(unittest.TestCase):
    """An exploratory run labels every row it writes and writes no history row."""

    def run_replay(self, tmp, args):
        fake, rows = FakeArms(), []

        def recording(tasks, opts, launch=None, out=None):
            done = fake_replay(tasks, opts, launch, out)
            rows.extend(done[0])
            return done
        err = io.StringIO()
        with mock.patch.object(BENCH, "ROOT", Path(tmp) / "repo"), \
                mock.patch.object(BENCH.arms, "build_arm", fake.build_arm), \
                mock.patch.object(BENCH.arms, "egress", fake.egress), \
                mock.patch.object(BENCH, "replay", recording), \
                mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "t"}), \
                redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = BENCH.cmd_replay(args)
        return code, rows, Path(tmp) / "history" / "history.jsonl", err.getvalue()

    def test_an_exploratory_run_labels_its_rows_and_writes_no_history_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            code, rows, history, err = self.run_replay(tmp, replay_args(tmp, exploratory=True))
            self.assertEqual(code, 0)
            self.assertEqual({r["evidence"] for r in rows}, {"exploratory"})
            self.assertFalse(history.exists())
            self.assertIn("exploratory run is not a history row", err)

    def test_a_pre_registered_run_names_its_plan_on_every_row_and_writes_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness_repo(Path(tmp) / "repo")
            args = replay_args(tmp)
            code, rows, history, _ = self.run_replay(tmp, args)
            self.assertEqual(code, 0)
            self.assertEqual({r["evidence"] for r in rows}, {"pre-registered"})
            self.assertEqual({r["pre_registration"] for r in rows},
                             {"benchmarks/preregistrations/" + Path(args.pre_registration).name})
            self.assertTrue(all(r["pre_registration_commit"] for r in rows))
            self.assertTrue(history.exists())


if __name__ == "__main__":
    unittest.main()

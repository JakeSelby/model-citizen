"""The N-arm ablation run (#514): `benchmarks/ablations.json` (schema 2) declares one arm per removed
or set entry beside bare and control; an unknown id is refused before any spend; the minimum
detectable effect is stated before the schedule; the schedule is drawn from a recorded seed with the
leading arm rotating; every row names its removed entry, which is absent from its attribution; and
a schema-1 pair file keeps its own path. Every launch is a fake; no test builds an image or calls a
model."""
import copy
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from test_cost_bench import BENCH, TASK, Launch, arm_record, options, result
from test_harness import REPO
from test_replay_parity import stream

ABL = BENCH.ablations
ARMS = BENCH.arms
SHIPPED = REPO / "benchmarks" / "ablations.json"
FIXTURE_TASKS = REPO / "tests" / "fixtures" / "ablation-tasks.json"
POSTURE = importlib.util.spec_from_file_location("posture_ablations", REPO / "policy" / "hooks" / "posture.py")
POSTURE_MODULE = importlib.util.module_from_spec(POSTURE)
POSTURE.loader.exec_module(POSTURE_MODULE)


def manifest(*arms, planning=None):
    data = {"schema": 2, "name": "sweep", "planning": planning or {"cv": 0.25, "source": "assumed"},
            "arms": list(arms) or [{"id": "no-secrets", "removes": "rules/secrets"}]}
    return dict(data, sha256="ab" * 32)


def write(tmp, data):
    path = Path(tmp) / "ablations.json"
    path.write_text(json.dumps({k: v for k, v in data.items() if k != "sha256"}), encoding="utf-8")
    return path


def selected_record(selection, ident):
    """A built declared-selection arm, as `replay_arms.build_arm` would record it."""
    record = copy.deepcopy(arm_record("harness"))
    decl = record["declaration"]
    decl["selection"] = selection
    data = ARMS.selection_bytes(selection)
    decl["components"].append({"name": "selection", "version": "sha256:" + hashlib.sha256(data).hexdigest()})
    entries = record["manifest"]["entries"] + [{"path": ARMS.USER_CONFIG, "kind": "file", "mode": "0644",
                                                 "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}]
    record["manifest"] = dict(record["manifest"], entries=entries,
                              summary=ARMS.arm_manifest.summary(entries))
    record.update(image="model-citizen-arm-harness:%s" % ident, image_id="sha256:" + "3" * 64,
                  declaration_sha256=ARMS.digest(decl), manifest_sha256=ARMS.digest(record["manifest"]))
    return record


def ablation_options(tmp, data, reps=2, seed=7):
    opts = options(tmp, reps=reps)
    opts.update(arm_names=ABL.arm_names(data), ablation=data, schedule_seed=seed,
                ablation_selections=ABL.selections(data))
    opts["arms"].update({ident: selected_record(sel, ident) for ident, sel in ABL.selections(data).items()})
    return opts


def run_output(prefix=20000):
    first = {"type": "assistant", "parent_tool_use_id": None,
             "message": {"model": "claude-test", "usage": {"input_tokens": 5, "cache_creation_input_tokens": prefix,
                                                           "cache_read_input_tokens": 0}}}
    return stream(first, result())


class ManifestTests(unittest.TestCase):
    def test_the_shipped_manifest_loads_with_its_digest(self):
        loaded = ABL.load(SHIPPED)
        self.assertEqual(loaded["schema"], 2)
        self.assertEqual(loaded["sha256"], hashlib.sha256(SHIPPED.read_bytes()).hexdigest())
        self.assertEqual(ABL.arm_names(loaded)[:2], ("bare", "harness"))
        self.assertEqual(len(ABL.arm_names(loaded)), len(loaded["arms"]) + 2)

    def test_a_schema_one_pair_file_is_read_as_the_pair_reads_it(self):
        pair = {"schema": 1, "name": "voice", "tag": "v1", "factor": "HARNESS_STANCE_VOICE",
                "reference": None, "treatment": "off"}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "voice.json"
            path.write_text(json.dumps(pair), encoding="utf-8")
            self.assertEqual(ABL.load(path), BENCH.replay_pair.load(path))

    def test_malformed_manifests_are_refused_naming_each_problem(self):
        cases = (({"id": "harness", "removes": "rules/secrets"}, "id 'harness'"),
                 ({"id": "x", "removes": "rules/secrets", "sets": {"stances/voice": "off"}}, "exactly one"),
                 ({"id": "x", "sets": {"stances/voice": "off", "stances/cost": "lean"}}, "one non-empty variant"),
                 ({"id": "x", "removes": "secrets"}, "kind/unit"))
        for arm, expected in cases:
            with self.subTest(arm=arm), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(SystemExit) as caught:
                    ABL.load(write(tmp, manifest(arm)))
                self.assertIn(expected, str(caught.exception))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as caught:
                ABL.load(write(tmp, manifest({"id": "a", "removes": "rules/secrets"},
                                             {"id": "b", "removes": "rules/secrets"}, planning={"cv": 0})))
        self.assertIn("toggled by another arm", str(caught.exception))
        self.assertIn("planning must be", str(caught.exception))

    def test_each_arm_selects_its_one_entry_in_the_user_config_shape(self):
        data = manifest({"id": "no-secrets", "removes": "rules/secrets"},
                        {"id": "voice-off", "sets": {"stances/voice": "off"}})
        self.assertEqual(ABL.selections(data), {"no-secrets": {"rules": {"secrets": "off"}},
                                                "voice-off": {"stances": {"voice": "off"}}})


class EntryCheckTests(unittest.TestCase):
    def check(self, *arms):
        return ABL.check_entries(manifest(*arms), POSTURE_MODULE, REPO)

    def test_every_shipped_entry_exists_at_this_commit(self):
        self.assertEqual(ABL.check_entries(ABL.load(SHIPPED), POSTURE_MODULE, REPO), [])

    def test_an_unknown_id_is_refused(self):
        errors = self.check({"id": "no-nope", "removes": "rules/no-such-rule"})
        self.assertEqual(len(errors), 1)
        self.assertIn("rules/no-such-rule", errors[0])
        self.assertIn("unknown kind", self.check({"id": "x", "removes": "widgets/a"})[0])

    def test_a_removal_of_a_variant_kind_a_missing_variant_and_a_default_are_refused(self):
        self.assertIn("not switched", self.check({"id": "x", "removes": "stances/voice"})[0])
        self.assertIn("does not ship", self.check({"id": "x", "sets": {"stances/voice": "nonesuch"}})[0])
        default = POSTURE_MODULE.DEFAULT_STANCES["voice"]
        self.assertIn("already its default", self.check({"id": "x", "sets": {"stances/voice": default}})[0])
        self.assertIn("is switched", self.check({"id": "x", "sets": {"rules/secrets": "off"}})[0])


class ScheduleTests(unittest.TestCase):
    NAMES = ("bare", "harness", "a", "b", "c")

    def test_a_recorded_seed_reproduces_the_order_and_another_seed_changes_it(self):
        tasks = [{"id": "t1"}, {"id": "t2"}]
        first = ABL.schedule(tasks, 5, self.NAMES, 11)
        self.assertEqual(first, ABL.schedule(tasks, 5, self.NAMES, 11))
        self.assertNotEqual(first, ABL.schedule(tasks, 5, self.NAMES, 12))
        self.assertEqual(len(first), 2 * 5 * len(self.NAMES))

    def test_the_leading_arm_rotates_with_the_rep(self):
        plan = ABL.schedule([{"id": "t"}], 5, self.NAMES, 3)
        leads = [plan[i * len(self.NAMES)][2] for i in range(5)]
        self.assertEqual(leads, list(self.NAMES))
        for i in range(5):
            block = plan[i * len(self.NAMES):(i + 1) * len(self.NAMES)]
            self.assertEqual(sorted(arm for _, _, arm in block), sorted(self.NAMES))


class DryRunTests(unittest.TestCase):
    def run_main(self, path, *extra):
        out, err = io.StringIO(), io.StringIO()
        head = BENCH._git_required(REPO, "rev-parse", "HEAD").stdout.strip()
        argv = ["replay", "--tasks", str(FIXTURE_TASKS), "--ablations", str(path), "--tag", head,
                "--model", "claude-test", "--exploratory", "--dry-run", "--reps", "2"] + list(extra)
        with mock.patch.object(BENCH, "snapshot", lambda repo, sha, dest: REPO), \
                redirect_stdout(out), redirect_stderr(err):
            status = BENCH.main(argv)
        return status, out.getvalue()

    def test_a_dry_run_prints_every_arm_and_states_the_effect_before_the_schedule(self):
        status, text = self.run_main(SHIPPED)
        data = ABL.load(SHIPPED)
        self.assertEqual(status, 0)
        self.assertIn("%d run(s): 2 task(s) x %d arm(s)" % (2 * 2 * (len(data["arms"]) + 2), len(data["arms"]) + 2), text)
        for arm in data["arms"]:
            self.assertIn("  arm %s " % arm["id"], text)
        self.assertIn("arm harness (control)", text)
        self.assertLess(text.index("minimum detectable effect, before any spend"), text.index("    fixture-one rep 1"))
        self.assertIn("schedule seed %d" % ABL.default_seed(data), text)

    def test_the_schedule_seed_flag_is_used_and_printed(self):
        _status, text = self.run_main(SHIPPED, "--schedule-seed", "42")
        self.assertIn("schedule seed 42", text)
        lines = [line.split()[-1] for line in text.splitlines() if line.startswith("    fixture-one rep 1 ")]
        names = ABL.arm_names(ABL.load(SHIPPED))
        self.assertEqual(lines, [arm for _, _, arm in ABL.schedule([{"id": "x"}], 1, names, 42)])

    def test_an_unknown_id_is_refused_before_anything_is_planned(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, manifest({"id": "no-nope", "removes": "rules/no-such-rule"}))
            with self.assertRaises(SystemExit) as caught:
                self.run_main(path)
        self.assertIn("refusing the ablation manifest before any spend", str(caught.exception))
        self.assertIn("rules/no-such-rule", str(caught.exception))

    def test_without_the_flag_the_dry_run_is_unchanged(self):
        out = io.StringIO()
        head = BENCH._git_required(REPO, "rev-parse", "HEAD").stdout.strip()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            BENCH.main(["replay", "--tasks", str(FIXTURE_TASKS), "--tag", head, "--model", "claude-test",
                        "--exploratory", "--dry-run", "--reps", "2"])
        text = out.getvalue()
        self.assertIn("2 task(s) x bare + harness x 2 rep(s)", text)
        self.assertNotIn("minimum detectable effect", text)
        self.assertEqual([l.split()[-1] for l in text.splitlines() if l.startswith("    fixture-one")],
                         ["bare", "harness", "harness", "bare"])

    def test_two_tags_or_a_stance_override_are_refused(self):
        for extra, expected in ((["--tag", "v0.1.0"], "exactly one --tag"),
                                (["--stance-cost", "lean"], "--stance-cost is refused")):
            with self.subTest(extra=extra), self.assertRaises(SystemExit) as caught:
                self.run_main(SHIPPED, *extra)
            self.assertIn(expected, str(caught.exception))


class FakeRunTests(unittest.TestCase):
    def replay(self, tmp, data, reps=2):
        opts = ablation_options(tmp, data, reps=reps)
        launch = Launch([run_output() for _ in range(reps * len(ABL.arm_names(data)))])
        return BENCH.replay([TASK], opts, launch), opts, launch

    def test_every_row_names_its_removed_entry_which_is_absent_from_its_attribution(self):
        data = manifest({"id": "no-secrets", "removes": "rules/secrets"},
                        {"id": "voice-off", "sets": {"stances/voice": "off"}})
        with tempfile.TemporaryDirectory() as tmp:
            (rows, stopped), _opts, _launch = self.replay(tmp, data)
        self.assertFalse(stopped)
        self.assertEqual(len(rows), 2 * 4)
        by_arm = {}
        for row in rows:
            by_arm.setdefault(row["arm"], []).append(row)
            self.assertEqual(row["schedule_seed"], 7)
            self.assertEqual(row["ablation"]["sha256"], data["sha256"])
            self.assertEqual(row["ablation"]["schema"], 2)
        for row in by_arm["no-secrets"]:
            self.assertEqual(row["ablation_removes"], "rules/secrets")
            self.assertNotIn("rules/secrets", row["context_attribution"]["modules"])
            self.assertEqual(row["selection"], {"rules": {"secrets": "off"}})
        for row in by_arm["harness"]:
            self.assertIsNone(row["ablation_removes"])
            self.assertIn("rules/secrets", row["context_attribution"]["modules"])
        self.assertEqual(by_arm["voice-off"][0]["ablation_sets"], {"stances/voice": "off"})
        self.assertNotEqual(by_arm["voice-off"][0]["context_attribution"]["modules"].get("stances/voice"),
                            by_arm["harness"][0]["context_attribution"]["modules"].get("stances/voice"))
        self.assertNotEqual(by_arm["no-secrets"][0]["profile_fingerprint"], by_arm["harness"][0]["profile_fingerprint"])
        self.assertEqual(ABL.attribution_problems(rows), [])

    def test_the_runs_follow_the_recorded_seed(self):
        data = manifest({"id": "no-secrets", "removes": "rules/secrets"})
        with tempfile.TemporaryDirectory() as tmp:
            (rows, _), _opts, _launch = self.replay(tmp, data)
        self.assertEqual([(r["rep"], r["arm"]) for r in rows],
                         [(rep, arm) for _, rep, arm in ABL.schedule([TASK], 2, ABL.arm_names(data), 7)])

    def test_an_arm_differing_from_control_beyond_its_selection_is_refused_before_any_launch(self):
        data = manifest({"id": "no-secrets", "removes": "rules/secrets"})
        with tempfile.TemporaryDirectory() as tmp:
            opts = ablation_options(tmp, data)
            arm = opts["arms"]["no-secrets"]
            arm["declaration"]["claude_code_version"] = "2.0"
            arm["declaration_sha256"] = ARMS.digest(arm["declaration"])
            launch = Launch([])
            with self.assertRaises(SystemExit) as caught:
                BENCH.admit_ablation_arms(opts)
        self.assertIn("declaration claude_code_version: control '1.0', arm '2.0'", str(caught.exception))
        self.assertEqual(launch.calls, [])

    def test_an_arm_whose_selection_toggles_nothing_is_refused(self):
        data = manifest({"id": "no-secrets", "removes": "rules/secrets"})
        with tempfile.TemporaryDirectory() as tmp:
            opts = ablation_options(tmp, data)
            opts["ablation_selections"] = {"no-secrets": {}}
            with self.assertRaises(SystemExit) as caught:
                BENCH.admit_ablation_arms(opts)
        self.assertIn("toggles nothing", str(caught.exception))

    def test_a_removed_entry_still_in_the_attribution_is_named(self):
        rows = [{"task": "t", "rep": 1, "arm": "no-secrets", "ablation_removes": "rules/secrets",
                 "context_attribution": {"modules": {"rules/secrets": 200}}}]
        self.assertIn("removed rules/secrets, yet its attribution holds it", ABL.attribution_problems(rows)[0])


class SurfaceParityTests(unittest.TestCase):
    def rows(self, arm_surface, entry="skills/demo"):
        control = {"init_surface_source": "cli-init", "init_skills": 3, "init_skills_sha256": "a",
                   "init_tools": 5, "init_tools_sha256": "t"}
        base = {"task": "t", "rep": 1}
        return [dict(base, arm="harness", **control),
                dict(base, arm="no-demo", ablation_removes=entry, **dict(control, **arm_surface))]

    def test_a_difference_in_the_removed_entrys_own_fields_is_allowed(self):
        self.assertEqual(ABL.surface_parity(self.rows({"init_skills": 2, "init_skills_sha256": "b"})), [])

    def test_any_other_difference_is_refused(self):
        reasons = ABL.surface_parity(self.rows({"init_skills": 2, "init_tools": 6}))
        self.assertEqual(len(reasons), 1)
        self.assertIn("no-demo loaded a surface that differs from harness beyond its declared entry, in init_tools",
                      reasons[0])
        self.assertNotIn("init_skills", reasons[0])

    def test_a_removed_rule_may_not_move_the_skill_listing(self):
        reasons = ABL.surface_parity(self.rows({"init_skills": 2}, entry="rules/secrets"))
        self.assertIn("init_skills", reasons[0])

    def test_the_pair_still_refuses_any_difference_with_its_own_message(self):
        pair_rows = [{"task": "t", "rep": 1, "arm": "reference", "init_surface_source": "cli-init", "init_skills": 3},
                     {"task": "t", "rep": 1, "arm": "treatment", "init_surface_source": "cli-init", "init_skills": 2}]
        self.assertEqual(BENCH.replay_pair.surface_parity(pair_rows),
                         ["t rep 1: loaded surface differs in init_skills"])


class SummariseTests(unittest.TestCase):
    def test_saved_rows_summarise_as_an_ablation_and_not_as_a_pair(self):
        data = manifest({"id": "no-secrets", "removes": "rules/secrets"})
        with tempfile.TemporaryDirectory() as tmp:
            opts = ablation_options(tmp, data, reps=5)
            launch = Launch([run_output() for _ in range(5 * 3)])
            rows, _ = BENCH.replay([TASK], opts, launch)
            path = Path(tmp) / "results.jsonl"
            BENCH.write_jsonl(path, rows)
            out = io.StringIO()
            with redirect_stdout(out):
                status = BENCH.main(["summarise", "--results", str(path), "--resamples", "200"])
        self.assertFalse(BENCH.replay_pair.is_pair(rows))
        self.assertTrue(ABL.is_ablation(rows))
        self.assertEqual(status, 0)
        text = out.getvalue()
        self.assertIn("Ablation sweep", text)
        # An arm may remove an entry or set it to another variant; the label names neither.
        self.assertIn("1. no-secrets (changes rules/secrets)", text)
        self.assertNotIn("(removes ", text)
        self.assertIn("post-run parity: every arm differed from control only in its declared entry", text)

    def test_a_correction_on_a_plain_replay_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.jsonl"
            BENCH.write_jsonl(path, [{"task": "t", "arm": "bare", "rep": 1, "cost_usd": 1.0, "passed": True}])
            with self.assertRaises(SystemExit) as caught:
                BENCH.main(["summarise", "--results", str(path), "--correction", "bonferroni"])
        self.assertIn("ablation run's arms only", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

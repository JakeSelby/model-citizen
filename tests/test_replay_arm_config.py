"""Replay arms built from a named stance selection (#1183): an arm config names stance dimension to
variant; an unknown dimension or variant is refused against the checkout's own variants; the
config's digest is stable; a config arm passes the pair parity check every harness arm passes;
every row records its `arm_config`; and the dry run lists each config arm. Every launch is a fake;
no test builds an image or calls a model."""
import copy
import importlib.util
import io
import json
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from test_cost_bench import BENCH, TASK, Launch, options
from test_cost_bench_ablations import FIXTURE_TASKS, run_output, selected_record
from test_harness import REPO

ARMS = BENCH.arms
SHIPPED = REPO / "benchmarks" / "arms"
POSTURE = importlib.util.spec_from_file_location("posture_arm_config", REPO / "policy" / "hooks" / "posture.py")
POSTURE_MODULE = importlib.util.module_from_spec(POSTURE)
POSTURE.loader.exec_module(POSTURE_MODULE)


def config(**stances):
    return {"schema": 1, "description": "test", "stances": stances}


def write(tmp, data, name="arm.json"):
    path = Path(tmp) / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class ShapeTests(unittest.TestCase):
    def test_the_shipped_configs_load_and_name_the_selection_they_claim(self):
        self.assertEqual(ARMS.load_arm_config(SHIPPED / "maintainer.json")["stances"], {"voice": "concise"})
        self.assertEqual(ARMS.load_arm_config(SHIPPED / "frugal.json")["stances"], {"cost": "frugal"})

    def test_a_malformed_config_is_refused_with_its_reason(self):
        for data, expected in (([], "JSON object"), (dict(config(voice="concise"), rules={}), "unknown key"),
                               (dict(config(voice="concise"), schema=2), "schema must be 1"),
                               (config(), "non-empty"), (config(voice=""), "not a variant name"),
                               ({"schema": 1, "stances": {"voice": ["concise"]}}, "not a variant name")):
            with self.subTest(data=data):
                self.assertIn(expected, ARMS.arm_config_problem(data))
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(SystemExit) as caught:
            ARMS.load_arm_config(write(tmp, config()))
        self.assertIn("is refused", str(caught.exception))

    def test_arm_names_that_a_run_already_uses_are_refused(self):
        for name in ("bare", "harness", "control", "reference", "treatment", "Maint", "a_b", ""):
            with self.subTest(name=name):
                self.assertIsNotNone(ARMS.arm_config_name_problem(name))
        self.assertIsNone(ARMS.arm_config_name_problem("maintainer"))


class ValidationTests(unittest.TestCase):
    def errors(self, data):
        return ARMS.arm_config_errors("x", data, POSTURE_MODULE, REPO)

    def test_the_shipped_configs_are_valid_against_this_checkout(self):
        for path in sorted(SHIPPED.glob("*.json")):
            with self.subTest(path=path.name):
                self.assertEqual(self.errors(ARMS.load_arm_config(path)), [])

    def test_an_unknown_dimension_is_refused(self):
        errors = self.errors(config(tone="concise"))
        self.assertEqual(len(errors), 1)
        self.assertIn("unknown stance dimension tone", errors[0])
        self.assertIn("voice", errors[0])  # names the dimensions there are

    def test_an_unknown_variant_is_refused(self):
        errors = self.errors(config(voice="terse", cost="frugal"))
        self.assertEqual(len(errors), 1)
        self.assertIn("stance voice has no variant terse", errors[0])
        self.assertIn("concise", errors[0])

    def test_a_selection_that_is_the_default_throughout_is_refused(self):
        default = POSTURE_MODULE.selection({"HOME": str(REPO / ".no-home")}, strict=False, config={}, root=REPO)
        errors = self.errors(config(voice=default["stances"]["voice"]))
        self.assertEqual(len(errors), 1)
        self.assertIn("already the tag's default", errors[0])

    def test_the_variants_read_are_the_checkouts_own(self):
        variants = ARMS.stance_variants(POSTURE_MODULE, REPO)
        self.assertIn("frugal", variants["cost"])
        self.assertEqual(variants["voice"], sorted(p.stem for p in (REPO / "primitives" / "stances" / "voice").glob("*.md")))


class DigestTests(unittest.TestCase):
    def test_the_digest_ignores_formatting_and_key_order(self):
        data = config(voice="concise", cost="frugal")
        reordered = {"stances": {"cost": "frugal", "voice": "concise"}, "description": "test", "schema": 1}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spaced.json"
            path.write_text(json.dumps(reordered, indent=4) + "\n\n", encoding="utf-8")
            loaded = ARMS.load_arm_config(path)
        self.assertEqual(ARMS.arm_config_sha256(data), ARMS.arm_config_sha256(loaded))
        self.assertEqual(ARMS.arm_config_sha256(data), ARMS.digest(data))

    def test_a_changed_variant_changes_the_digest_and_the_image(self):
        one, other = config(voice="concise"), config(voice="off")
        self.assertNotEqual(ARMS.arm_config_sha256(one), ARMS.arm_config_sha256(other))
        inputs = ARMS.qualification_inputs()
        harness = {"ref": "v1", "commit": "c" * 40}
        decls = [ARMS.declaration("harness", inputs, harness, selection=ARMS.arm_config_selection(c)) for c in (one, other)]
        self.assertNotEqual(ARMS.image_name(decls[0]), ARMS.image_name(decls[1]))
        plain = ARMS.declaration("harness", inputs, harness)
        self.assertEqual(BENCH.ablations.declaration_differences(plain, decls[0]), [])


def config_options(tmp, configs, reps=1):
    """A run with config arms, as `replay_tag` assembles it from `--arm-config`."""
    opts = options(tmp, reps=reps)
    stamps = {name: ARMS.arm_config_stamp(name, data) for name, data in configs.items()}
    selections = {name: ARMS.arm_config_selection(data) for name, data in configs.items()}
    opts.update(arm_names=BENCH.ARMS + tuple(configs), arm_configs=stamps, ablation_selections=selections)
    opts["arms"].update({name: selected_record(selections[name], name) for name in configs})
    return opts


class RunTests(unittest.TestCase):
    CONFIGS = {"maintainer": config(voice="concise"), "frugal": config(cost="frugal")}

    def test_every_row_records_its_arm_config_and_the_other_arms_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            opts = config_options(tmp, self.CONFIGS)
            launch = Launch([run_output() for _ in range(4)])
            rows, stopped = BENCH.replay([TASK], opts, launch)
        self.assertFalse(stopped)
        by_arm = {row["arm"]: row for row in rows}
        self.assertEqual(sorted(by_arm), ["bare", "frugal", "harness", "maintainer"])
        self.assertIsNone(by_arm["bare"]["arm_config"])
        self.assertIsNone(by_arm["harness"]["arm_config"])
        stamp = by_arm["maintainer"]["arm_config"]
        self.assertEqual(stamp, {"name": "maintainer", "schema": 1, "stances": {"voice": "concise"},
                                 "sha256": ARMS.arm_config_sha256(self.CONFIGS["maintainer"])})
        self.assertEqual(by_arm["frugal"]["arm_config"]["stances"], {"cost": "frugal"})
        self.assertNotEqual(by_arm["maintainer"]["profile_fingerprint"], by_arm["harness"]["profile_fingerprint"])

    def test_a_config_arm_passes_the_pair_parity_check_with_bare(self):
        with tempfile.TemporaryDirectory() as tmp:
            opts = config_options(tmp, self.CONFIGS)
        for name in self.CONFIGS:
            self.assertEqual(ARMS.pair_differences(opts["arms"]["bare"], opts["arms"][name]), [])

    def test_a_config_arm_holding_more_than_its_selection_is_refused_before_any_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            opts = config_options(tmp, {"maintainer": self.CONFIGS["maintainer"]})
            record = copy.deepcopy(opts["arms"]["maintainer"])
            entries = record["manifest"]["entries"] + [{"path": "home:.bashrc", "kind": "file", "mode": "0644",
                                                         "size": 1, "sha256": "f" * 64}]
            record["manifest"] = dict(record["manifest"], entries=entries,
                                      summary=ARMS.arm_manifest.summary(entries))
            record["manifest_sha256"] = ARMS.digest(record["manifest"])
            opts["arms"]["maintainer"] = record
            launch = Launch([])
            with self.assertRaises(SystemExit) as caught:
                BENCH.replay([TASK], opts, launch)
        self.assertIn("home:.bashrc", str(caught.exception))
        self.assertEqual(launch.calls, [])

    def test_a_config_arm_declared_from_another_commit_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            opts = config_options(tmp, {"maintainer": self.CONFIGS["maintainer"]})
            record = copy.deepcopy(opts["arms"]["maintainer"])
            record["declaration"]["claude_code_version"] = "2.0"
            record["declaration_sha256"] = ARMS.digest(record["declaration"])
            opts["arms"]["maintainer"] = record
            with self.assertRaises(SystemExit) as caught:
                BENCH.admit_config_arms(opts)
        self.assertIn("claude_code_version", str(caught.exception))


class ReplayTagTests(unittest.TestCase):
    """`replay_tag` over a full, pre-registered, unstopped set: only the config arms keep the history row out."""

    def run_tag(self, tmp, config_records):
        tmp = Path(tmp)
        args = types.SimpleNamespace(
            tasks=str(FIXTURE_TASKS), model="claude-test", tier=None, series_source=b"set", tmp=str(tmp),
            reps=1, run_cap=1.0, spend_cap=10.0, stance_cost=None, raw=False, change_note=None,
            skip_preflight=True, bucket=None, predicted_ratio=None, allow_surface_drift=False,
            history_dir=str(tmp / "history"), set_size=1)
        common = {"tasks": [TASK], "plan": [(TASK, 1, "bare"), (TASK, 1, "harness")], "bare": {}, "out": tmp / "out",
                  "prices": {}, "network": "n", "proxy": "p", "cli_version": "2.0", "client_env": {},
                  "protocol": {"evidence": "pre-registered"}}
        if config_records:
            common.update(config_records=config_records, arm_configs={}, config_selections={})
        rows = [{"arm": arm, "evidence": BENCH.experiment_protocol.PREREGISTERED} for arm in ("bare", "harness")]
        err = io.StringIO()
        with mock.patch.object(BENCH, "snapshot", lambda repo, sha, dest: dest), \
                mock.patch.object(BENCH, "tag_version", lambda repo, commit, ref: "9.9.9"), \
                mock.patch.object(BENCH, "replay", lambda tasks, opts, out=None: (rows, False)), \
                mock.patch.object(BENCH, "history_row", lambda *a, **k: {"version": "9.9.9"}), \
                mock.patch.object(BENCH, "render_history", lambda kept: "table\n"), \
                redirect_stdout(io.StringIO()), redirect_stderr(err):
            status = BENCH.replay_tag("v9.9.9", args, common, {"harness_commit": "0" * 40})
        return status, err.getvalue(), sorted(p.name for p in (tmp / "history").glob("*"))

    def test_a_run_with_config_arms_writes_no_history_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            status, err, written = self.run_tag(tmp, {"maintainer": {}})
        self.assertEqual(status, 0)
        self.assertEqual(written, [])
        self.assertIn("a run with config arms writes no history row", err)

    def test_the_same_set_without_config_arms_writes_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            status, _err, written = self.run_tag(tmp, None)
        self.assertEqual(status, 0)
        self.assertEqual(written, [BENCH.HISTORY.name, BENCH.HISTORY_MD.name])

    def test_the_config_arm_message_names_the_rows_and_offers_no_summarise(self):
        with tempfile.TemporaryDirectory() as tmp:
            _status, err, _written = self.run_tag(tmp, {"maintainer": {}})
        self.assertIn(str(Path(tmp) / "out" / "v9.9.9" / BENCH.RESULTS), err)
        self.assertNotIn("summarise", err)


class DryRunTests(unittest.TestCase):
    def run_main(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        head = BENCH._git_required(REPO, "rev-parse", "HEAD").stdout.strip()
        argv = ["replay", "--tasks", str(FIXTURE_TASKS), "--tag", head, "--model", "claude-test",
                "--exploratory", "--dry-run", "--reps", "1"] + list(extra)
        # The fixture tasks are same-repository synthetics the contamination control always refuses.
        with mock.patch.object(BENCH, "snapshot", lambda repo, sha, dest: REPO), \
                mock.patch.object(BENCH, "contamination_by_task", lambda tasks, repo, commit, tmp=None: []), \
                redirect_stdout(out), redirect_stderr(err):
            status = BENCH.main(argv)
        return status, out.getvalue()

    def test_the_dry_run_lists_every_config_arm_and_schedules_it(self):
        status, text = self.run_main("--arm-config", "maintainer=%s" % (SHIPPED / "maintainer.json"),
                                     "--arm-config", "frugal=%s" % (SHIPPED / "frugal.json"))
        self.assertEqual(status, 0)
        self.assertIn("x bare + harness + maintainer + frugal x 1 rep(s)", text)
        digest = ARMS.arm_config_sha256(ARMS.load_arm_config(SHIPPED / "maintainer.json"))
        self.assertIn("    arm maintainer (config sha256 %s) sets voice to concise: model-citizen-arm-harness:"
                      % digest[:12], text)
        self.assertIn("    arm frugal (config sha256 ", text)
        scheduled = [line.split()[-1] for line in text.splitlines() if line.startswith("    fixture-one rep 1 ")]
        self.assertEqual(sorted(scheduled), ["bare", "frugal", "harness", "maintainer"])

    def test_an_unknown_variant_is_refused_before_anything_is_planned(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, config(voice="terse"))
            with self.assertRaises(SystemExit) as caught:
                self.run_main("--arm-config", "terse=%s" % path)
        self.assertIn("refusing the arm config(s) before any spend", str(caught.exception))
        self.assertIn("no variant terse", str(caught.exception))

    def test_a_malformed_flag_a_reserved_name_or_a_conflicting_mode_is_refused(self):
        maintainer = str(SHIPPED / "maintainer.json")
        for extra, expected in ((["--arm-config", maintainer], "NAME=PATH"),
                                (["--arm-config", "harness=" + maintainer], "is taken"),
                                (["--arm-config", "a=" + maintainer, "--arm-config", "a=" + maintainer], "twice"),
                                (["--arm-config", "a=" + maintainer, "--stance-cost", "frugal"],
                                 "--stance-cost is refused"),
                                (["--arm-config", "a=" + maintainer, "--pair", "x.json"], "declare their own arms")):
            with self.subTest(extra=extra), self.assertRaises(SystemExit) as caught:
                self.run_main(*extra)
            self.assertIn(expected, str(caught.exception))

    def test_a_micro_run_with_config_arms_needs_an_explicit_spend_cap(self):
        head = BENCH._git_required(REPO, "rev-parse", "HEAD").stdout.strip()
        argv = ["replay", "--tier", "micro", "--raw", "unused-raw-dir", "--tag", head, "--exploratory", "--reps", "1",
                "--arm-config", "maintainer=%s" % (SHIPPED / "maintainer.json")]
        with mock.patch.object(BENCH, "snapshot", lambda repo, sha, dest: REPO), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                BENCH.main(argv)
            self.assertIn("a run with config arms needs --spend-cap", str(caught.exception))
            # Named, the cap passes the guard and the run stops at the next one, the credential.
            with mock.patch.dict("os.environ", {ARMS.CREDENTIAL: ""}), self.assertRaises(SystemExit) as caught:
                BENCH.main(argv + ["--spend-cap", "12"])
            self.assertIn("%s is not set" % ARMS.CREDENTIAL, str(caught.exception))

    def test_each_flag_is_read_in_the_order_given(self):
        args = mock.Mock(arm_config=["m=%s" % (SHIPPED / "maintainer.json"), "f=%s" % (SHIPPED / "frugal.json")],
                         pair=None, ablations=None, design=None, stance_cost=None)
        self.assertEqual([(name, data["stances"]) for name, data in BENCH.arm_configs(args)],
                         [("m", {"voice": "concise"}), ("f", {"cost": "frugal"})])


if __name__ == "__main__":
    unittest.main()

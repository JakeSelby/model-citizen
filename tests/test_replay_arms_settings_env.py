"""An ablation arm may declare a Claude Code settings `env`, such as the auto-compaction window: it
is in the arm's declaration and digest, merged into its image after the sync, read back by the
manifest lister and admitted only when the image holds exactly what was declared. Every Docker
command goes to a fake launcher, so no test needs a daemon."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_harness import REPO

sys.path.insert(0, str(REPO / "scripts"))


def load(name):
    spec = importlib.util.spec_from_file_location(name + "_settings_env", REPO / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ARMS = load("replay_arms")
LISTER = load("arm_manifest")
ABL = load("ablations")
INPUTS = {"base_image": "base@sha256:" + "0" * 64, "claude_code_version": "1.2.3"}
HARNESS = {"ref": "v1", "commit": "c" * 40}
RESET_ON = {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "160000", "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "70",
            "HARNESS_HANDOFF_BLOCK": "on"}
RESET_OFF = {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": None, "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": None,
             "HARNESS_HANDOFF_BLOCK": "on"}
MANIFEST = REPO / "benchmarks" / "ablations" / "context-reset.json"


def declared(settings_env=None, **kwargs):
    return ARMS.declaration("harness", INPUTS, HARNESS, effort="high", settings_env=settings_env, **kwargs)


def record(decl, held):
    return {"label": "arm", "declaration": decl, "manifest": {"settings_env": held}}


class DeclarationTests(unittest.TestCase):
    def test_the_env_is_declared_as_a_component_and_in_full(self):
        decl = declared(RESET_ON)
        self.assertEqual(decl[ARMS.SETTINGS_ENV], RESET_ON)
        component = [c for c in decl["components"] if c["name"] == ARMS.SETTINGS_ENV_COMPONENT]
        self.assertEqual(component, [{"name": "settings-env", "version": "sha256:"
                                      + hashlib.sha256(ARMS.settings_env_bytes(RESET_ON)).hexdigest()}])

    def test_each_env_names_its_own_image_and_none_leaves_the_harness_arm_as_it_was(self):
        plain, on, early = declared(), declared(RESET_ON), declared(dict(RESET_ON, CLAUDE_AUTOCOMPACT_PCT_OVERRIDE="35"))
        self.assertNotIn(ARMS.SETTINGS_ENV, plain)
        self.assertEqual(plain, ARMS.declaration("harness", INPUTS, HARNESS, effort="high"))
        self.assertEqual(len({ARMS.image_name(d) for d in (plain, on, early)}), 3)

    def test_a_malformed_env_a_bare_arm_or_a_selection_beside_it_is_refused(self):
        for bad in ({}, {"lower": "x"}, {"WINDOW": 100000}, ["WINDOW"]):
            with self.subTest(bad), self.assertRaises(SystemExit):
                declared(bad)
        with self.assertRaises(SystemExit):
            ARMS.declaration("bare", INPUTS, settings_env=RESET_ON)
        with self.assertRaises(SystemExit):
            declared(RESET_ON, selection={"rules": {"secrets": "off"}})

    def test_the_build_context_carries_the_env_and_targets_the_env_stage(self):
        decl = declared(RESET_OFF)
        with tempfile.TemporaryDirectory() as tmp:
            context = ARMS.build_context(decl, tmp, lambda repo, sha, dest: Path(dest).mkdir(parents=True))
            self.assertEqual(json.loads((context / ARMS.SETTINGS_ENV_FILE).read_text()), RESET_OFF)
        command = ARMS.build_command(decl, "/ctx", "image")
        self.assertEqual(command[command.index("--target") + 1], ARMS.SETTINGS_ENV_TARGET)
        self.assertIn(ARMS.SETTINGS_ENV_COMPONENT, ARMS.NOT_PACKAGES)


class DockerfileStageTests(unittest.TestCase):
    def merge(self, settings, env):
        """Run the env stage's merge step against a temporary home, as the image's agent user."""
        text = ARMS.ARM_DOCKERFILE.read_text(encoding="utf-8")
        stage = text[text.index("FROM harness AS " + ARMS.SETTINGS_ENV_TARGET):]
        script = re.search(r"python3 -c '([^']*)'", stage).group(1)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude").mkdir()
            (home / ".claude" / "settings.json").write_text(json.dumps(settings))
            source = home / "settings-env.json"
            source.write_text(json.dumps(env))
            script = script.replace("/tmp/model-citizen-settings-env.json", str(source))
            subprocess.run([sys.executable, "-c", script], check=True, env=dict(os.environ, HOME=str(home)))
            return json.loads((home / ".claude" / "settings.json").read_text())

    def test_the_stage_sets_values_and_takes_nulls_out(self):
        synced = {"hooks": {"Stop": []}, "env": {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "420000",
                                                  "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "100", "KEEP": "1"}}
        self.assertEqual(self.merge(synced, RESET_ON)["env"], dict(RESET_ON, KEEP="1"))
        off = self.merge(synced, RESET_OFF)
        self.assertEqual(off["env"], {"HARNESS_HANDOFF_BLOCK": "on", "KEEP": "1"})
        self.assertEqual(off["hooks"], {"Stop": []})

    def test_the_stage_builds_on_the_synced_harness(self):
        text = ARMS.ARM_DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn("FROM harness AS %s\nCOPY --chown=agent:agent %s " % (ARMS.SETTINGS_ENV_TARGET,
                                                                            ARMS.SETTINGS_ENV_FILE), text)


class ListerTests(unittest.TestCase):
    def test_the_manifest_records_the_settings_env_or_null(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            roots = (("home", str(home)),)
            home.mkdir()
            with mock.patch.object(LISTER, "ROOTS", roots):
                self.assertIsNone(LISTER.settings_env())
                (home / ".claude").mkdir()
                (home / ".claude" / "settings.json").write_text(json.dumps({"env": {"A": "1"}}))
                self.assertEqual(LISTER.settings_env(), {"A": "1"})
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    LISTER.main()
        self.assertEqual(json.loads(out.getvalue())["settings_env"], {"A": "1"})


class AdmissionTests(unittest.TestCase):
    def test_an_image_holding_the_declared_env_is_admitted(self):
        self.assertIsNone(ARMS._settings_env_installed(record(declared(RESET_ON), dict(RESET_ON, OTHER="x"))))
        self.assertIsNone(ARMS._settings_env_installed(record(declared(RESET_OFF), {"HARNESS_HANDOFF_BLOCK": "on"})))
        self.assertIsNone(ARMS._settings_env_installed(record(declared(), None)))

    def test_a_value_that_differs_or_a_null_still_present_is_refused_by_name(self):
        held = dict(RESET_ON, CLAUDE_AUTOCOMPACT_PCT_OVERRIDE="100")
        self.assertIn("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
                      ARMS._settings_env_installed(record(declared(RESET_ON), held)))
        present = {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "420000", "HARNESS_HANDOFF_BLOCK": "on"}
        self.assertIn("CLAUDE_CODE_AUTO_COMPACT_WINDOW",
                      ARMS._settings_env_installed(record(declared(RESET_OFF), present)))

    def test_an_unread_env_or_a_component_out_of_step_is_refused(self):
        self.assertIn("does not record", ARMS._settings_env_installed(record(declared(RESET_ON), None)))
        decl = declared(RESET_ON)
        decl[ARMS.SETTINGS_ENV] = dict(RESET_ON, CLAUDE_AUTOCOMPACT_PCT_OVERRIDE="35")
        self.assertIn("does not match", ARMS._settings_env_installed(record(decl, RESET_ON)))
        stray = declared(RESET_ON)
        del stray[ARMS.SETTINGS_ENV]
        self.assertIn("no settings env", ARMS._settings_env_installed(record(stray, RESET_ON)))

    def test_the_check_runs_with_every_admission(self):
        self.assertIn(ARMS._settings_env_installed, ARMS.ADMISSION_CHECKS)


class ManifestTests(unittest.TestCase):
    def manifest(self, *arms):
        return {"schema": 2, "name": "context-reset", "planning": {"cv": 0.25, "source": "assumed"},
                "arms": list(arms)}

    def test_the_committed_manifest_declares_the_three_reset_arms(self):
        data = ABL.load(MANIFEST)
        self.assertEqual(ABL.arm_names(data), ("bare", "harness", "reset-on", "reset-off", "reset-early"))
        envs = dict((a["id"], ABL.settings_env_of(a)) for a in data["arms"])
        self.assertEqual(envs["reset-on"], RESET_ON)
        self.assertEqual(envs["reset-off"], RESET_OFF)
        self.assertEqual(envs["reset-early"], dict(RESET_ON, CLAUDE_AUTOCOMPACT_PCT_OVERRIDE="35"))

    def test_the_reset_points_follow_the_measured_relation(self):
        # trigger = pct / 100 x (window - 19,300), measured in the spike (#1291)
        envs = dict((a["id"], ABL.settings_env_of(a)) for a in ABL.load(MANIFEST)["arms"])
        trigger = dict((name, int(env["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"]) / 100.0
                        * (int(env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"]) - 19300))
                       for name, env in envs.items() if env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"])
        self.assertGreaterEqual(trigger["reset-early"], 45000)
        self.assertAlmostEqual(trigger["reset-on"] / trigger["reset-early"], 2.0)

    def test_an_env_arm_takes_no_other_entry_and_a_bad_env_is_named(self):
        both = {"id": "x", "settings_env": RESET_ON, "sets": {"stances/voice": "off"}}
        self.assertIn("exactly one of removes, sets or settings_env",
                      "; ".join(ABL.structure_errors(self.manifest(both))))
        bad = {"id": "x", "settings_env": {"window": 1}}
        self.assertIn("upper-case variable name", "; ".join(ABL.structure_errors(self.manifest(bad))))
        self.assertEqual(ABL.structure_errors(self.manifest({"id": "x", "settings_env": RESET_OFF})), [])

    def test_an_env_arm_declares_its_env_and_no_selection(self):
        arm = {"id": "reset-on", "settings_env": RESET_ON}
        self.assertEqual(ABL.declared_inputs(arm), {"settings_env": RESET_ON})
        self.assertIsNone(ABL.selection(arm))
        self.assertEqual(ABL.declared_inputs({"id": "v", "sets": {"stances/voice": "off"}}),
                         {"selection": {"stances": {"voice": "off"}}})
        self.assertEqual(ABL.check_entries(self.manifest(arm), ABL.catalog.posture_module(REPO), REPO), [])

    def test_admission_holds_an_env_arm_to_its_image_not_to_a_profile_difference(self):
        control = {"declaration": declared(), "manifest": {"settings_env": {}}}
        good = {"declaration": declared(RESET_ON), "manifest": {"settings_env": RESET_ON}}
        same = {"harness": "fp", "reset-on": "fp"}
        self.assertIsNone(ABL.admit_arms({"harness": control, "reset-on": good}, same))
        wrong = {"declaration": declared(RESET_ON), "manifest": {"settings_env": {}}}
        with self.assertRaises(SystemExit) as caught:
            ABL.admit_arms({"harness": control, "reset-on": wrong}, same)
        self.assertIn("reset-on: the image's settings env differs", str(caught.exception))
        moved = {"declaration": dict(declared(RESET_ON), claude_code_version="9"),
                 "manifest": {"settings_env": RESET_ON}}
        with self.assertRaises(SystemExit) as caught:
            ABL.admit_arms({"harness": control, "reset-on": moved}, same)
        self.assertIn("declaration claude_code_version", str(caught.exception))

    def test_every_row_names_the_env_its_arm_ran_with(self):
        data = dict(ABL.load(MANIFEST))
        self.assertEqual(ABL.row_stamp(data, "reset-off", 7)["ablation_settings_env"], RESET_OFF)
        self.assertIsNone(ABL.row_stamp(data, "harness", 7)["ablation_settings_env"])


class DryRunTests(unittest.TestCase):
    def test_the_dry_plan_names_each_env_arm_and_its_image(self):
        bench = REPO / "scripts" / "cost_bench.py"
        text = bench.read_text(encoding="utf-8")
        self.assertIn("ablations.declared_inputs(spec)", text)
        self.assertIn('"sets settings env %s"', text)


if __name__ == "__main__":
    unittest.main()

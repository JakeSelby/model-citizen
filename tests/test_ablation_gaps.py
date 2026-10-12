"""Removal arms for the layers the sweep (#1184) could not remove (#1250): the user-level CLAUDE.md,
withheld from the arm's image and admitted by a manifest parity check against control, and the
autonomy and plan-ceremony stances, through `off` variants that change only the stance text. No
image is built and no model is called; the withholding build step runs in a temporary home."""
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from test_cost_bench import BENCH
from test_harness import REPO

sys.path.insert(0, str(REPO / "lib"))
from harness_core import decision  # noqa: E402

ABL = BENCH.ablations
ARMS = BENCH.arms
SHIPPED = REPO / "benchmarks" / "ablations.json"
FIXTURE_TASKS = REPO / "tests" / "fixtures" / "ablation-tasks.json"
DOCKERFILE = REPO / "scripts" / "replay-arm.Dockerfile"
POSTURE = importlib.util.spec_from_file_location("posture_gaps", REPO / "policy" / "hooks" / "posture.py")
POSTURE_MODULE = importlib.util.module_from_spec(POSTURE)
POSTURE.loader.exec_module(POSTURE_MODULE)
GRADER = importlib.util.spec_from_file_location("bash_grader_gaps", REPO / "policy" / "hooks" / "bash-grader.py")
GRADER_MODULE = importlib.util.module_from_spec(GRADER)
GRADER.loader.exec_module(GRADER_MODULE)
CLAUDE_MD = "home:.claude/CLAUDE.md"
NEW_ARMS = ("no-claude-md", "autonomy-off", "plan-ceremony-off")


def shipped_arm(ident):
    return next(a for a in ABL.load(SHIPPED)["arms"] if a["id"] == ident)


def withhold_step():
    """The shell command of the `harness-selected` stage's withholding RUN instruction."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    stage = text.split("AS harness-selected", 1)[1]
    match = re.search(r"^RUN (if python3 .*?)\n(?!\s)", stage, re.M | re.S)
    return match.group(1).replace("\\\n", "\n")


def entry(path, sha="0" * 64, kind="file"):
    return {"path": path, "kind": kind, "sha256": sha} if kind == "file" else {"path": path, "kind": kind,
                                                                              "target": sha}


def manifest(*entries, **extra):
    return dict({"schema": 2, "entries": list(entries), "summary": {}, "harness_commit": "a" * 40}, **extra)


CONTROL_MANIFEST = manifest(entry(CLAUDE_MD, "/opt/model-citizen/claude/CLAUDE.md", "link"),
                            entry("home:.claude/settings.json", "1" * 64),
                            entry("home:.config/agent-harness/config.json", "2" * 64),
                            entry("home:.local/state/agent-harness/applied.json", "3" * 64))


def record(selection, the_manifest):
    decl = ARMS.declaration("harness", ARMS.qualification_inputs(), {"ref": "v1", "commit": "a" * 40},
                            "1.0.0", "high", selection=selection)
    return {"declaration": decl, "manifest": the_manifest}


class WithholdStepTests(unittest.TestCase):
    """The build step runs as the image runs it, against a temporary home."""

    def run_step(self, config, holds=True):
        home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(home), True)
        (home / ".config" / "agent-harness").mkdir(parents=True)
        (home / ".config" / "agent-harness" / "config.json").write_text(json.dumps(config), encoding="utf-8")
        (home / ".claude").mkdir()
        if holds:
            os.symlink("/opt/model-citizen/claude/CLAUDE.md", str(home / ".claude" / "CLAUDE.md"))
        done = subprocess.run(["sh", "-c", withhold_step()], env={"HOME": str(home), "PATH": os.environ["PATH"]},
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return done.returncode, os.path.lexists(str(home / ".claude" / "CLAUDE.md"))

    def test_the_withholding_selection_deletes_the_synced_claude_md(self):
        self.assertEqual(self.run_step(ABL.selection(shipped_arm("no-claude-md"))), (0, False))

    def test_any_other_selection_keeps_it(self):
        self.assertEqual(self.run_step({"rules": {"secrets": "off"}}), (0, True))
        self.assertEqual(self.run_step({"instructions": {"CLAUDE.md": "on"}}), (0, True))

    def test_the_build_fails_when_there_is_no_claude_md_to_withhold(self):
        code, _ = self.run_step({"instructions": {"CLAUDE.md": "off"}}, holds=False)
        self.assertNotEqual(code, 0)

    def test_the_step_deletes_the_path_the_parity_check_expects(self):
        self.assertIn('"$HOME/.claude/CLAUDE.md"', withhold_step())
        self.assertEqual(ABL.WITHHOLDABLE, {"instructions/CLAUDE.md": CLAUDE_MD})


class WithheldSelectionTests(unittest.TestCase):
    def test_the_arm_declares_its_withholding_selection_into_its_own_image(self):
        selection = ABL.selection(shipped_arm("no-claude-md"))
        self.assertEqual(selection, {"instructions": {"CLAUDE.md": "off"}})
        self.assertEqual(ABL.withheld(selection), [CLAUDE_MD])
        self.assertEqual(ABL.withheld({"rules": {"secrets": "off"}}), [])
        arm, control = record(selection, {})["declaration"], record(None, {})["declaration"]
        self.assertEqual(ARMS.target(arm), ARMS.SELECTED_TARGET)
        self.assertNotEqual(ARMS.image_name(arm), ARMS.image_name(control))
        self.assertEqual(ABL.declaration_differences(control, arm), [])

    def test_the_resolver_accepts_every_new_arm_and_resolves_the_stances_off(self):
        env = {"HOME": str(REPO / ".ablation-empty-home")}
        for ident in NEW_ARMS:
            with self.subTest(arm=ident):
                chosen = POSTURE_MODULE.selection(env, strict=True, config=ABL.selection(shipped_arm(ident)), root=REPO)
                unit = ABL.entry_of(shipped_arm(ident)).split("/")[1]
                if ident != "no-claude-md":
                    self.assertEqual(chosen["stances"][unit], "off")

    def test_every_new_arm_resolves_at_this_commit(self):
        data = ABL.load(SHIPPED)
        data = dict(data, arms=[a for a in data["arms"] if a["id"] in NEW_ARMS])
        self.assertEqual(ABL.check_entries(data, POSTURE_MODULE, REPO), [])

    def test_a_withheld_entry_must_be_known_removed_alone_and_shipped_by_the_tag(self):
        def errors(spec, root=REPO):
            return ABL._withhold_errors(dict(spec, id="x"), ABL.entry_of(spec), root)
        self.assertEqual(errors({"removes": "instructions/CLAUDE.md"}), [])
        self.assertIn("use removes", errors({"sets": {"instructions/CLAUDE.md": "gone"}})[0])
        self.assertIn("unknown id", errors({"removes": "instructions/AGENTS.md"})[0])
        self.assertIn("one file", errors({"removes": ["instructions/CLAUDE.md", "instructions/x"]})[0])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIn("ships no primitives/instructions.md", errors({"removes": "instructions/CLAUDE.md"}, tmp)[0])

    def test_its_run_may_move_only_the_loaded_memory_paths(self):
        self.assertEqual(ABL.allowance("instructions/CLAUDE.md"), ("init_memory_paths", "init_memory_paths_sha256"))
        self.assertIn("deletes home:.claude/CLAUDE.md after the sync", ABL.verification(shipped_arm("no-claude-md")))
        self.assertIn("into its image", ABL.verification(shipped_arm("no-claude-md")))


class WithheldParityTests(unittest.TestCase):
    """Fingerprints cannot see a withheld file, so the image manifests decide admission."""

    def admit(self, arm_manifest, selection=None, fingerprints=None):
        records = {ABL.CONTROL: record(None, CONTROL_MANIFEST),
                   "no-claude-md": record(selection or {"instructions": {"CLAUDE.md": "off"}}, arm_manifest)}
        ABL.admit_arms(records, fingerprints or {ABL.CONTROL: "f", "no-claude-md": "f"})

    def arm_manifest(self, *extra, drop=(CLAUDE_MD,)):
        entries = [e for e in CONTROL_MANIFEST["entries"] if e["path"] not in drop]
        entries = [dict(e, sha256="9" * 64) if e["path"].endswith("config.json") else e for e in entries]
        return manifest(*(entries + list(extra)))

    def test_an_arm_lacking_only_claude_md_is_admitted_with_control_s_fingerprint(self):
        self.assertIsNone(self.admit(self.arm_manifest()))
        self.assertEqual(ABL.withheld_differences(CONTROL_MANIFEST, self.arm_manifest(), [CLAUDE_MD]), [])

    def test_an_arm_still_holding_claude_md_or_differing_elsewhere_is_refused(self):
        with self.assertRaises(SystemExit) as caught:
            self.admit(self.arm_manifest(drop=()))
        self.assertIn("still holds home:.claude/CLAUDE.md", str(caught.exception))
        with self.assertRaises(SystemExit) as caught:
            self.admit(self.arm_manifest(entry("home:.claude/rules/extra.md")))
        self.assertIn("only in the arm's image: home:.claude/rules/extra.md", str(caught.exception))
        changed = self.arm_manifest()
        changed["entries"] = [dict(e, sha256="8" * 64) if e["path"].endswith("settings.json") else e
                              for e in changed["entries"]]
        with self.assertRaises(SystemExit) as caught:
            self.admit(changed)
        self.assertIn("differs from control's image: home:.claude/settings.json", str(caught.exception))

    def test_control_without_claude_md_or_a_moved_manifest_key_is_refused(self):
        lines = ABL.withheld_differences(self.arm_manifest(), dict(self.arm_manifest(), harness_commit="b" * 40),
                                         [CLAUDE_MD])
        self.assertIn("control's image holds no home:.claude/CLAUDE.md to withhold", lines)
        self.assertTrue(any(line.startswith("manifest harness_commit") for line in lines))

    def test_a_switch_arm_with_control_s_fingerprint_is_still_refused(self):
        records = {ABL.CONTROL: record(None, CONTROL_MANIFEST),
                   "no-secrets": record({"rules": {"secrets": "off"}}, CONTROL_MANIFEST)}
        with self.assertRaises(SystemExit) as caught:
            ABL.admit_arms(records, {ABL.CONTROL: "f", "no-secrets": "f"})
        self.assertIn("toggles nothing", str(caught.exception))


class StanceOffTests(unittest.TestCase):
    def test_both_stances_ship_an_off_variant_in_the_off_format(self):
        for dimension, title in (("autonomy", "Autonomy stance"), ("plan-ceremony", "Plan ceremony stance")):
            with self.subTest(dimension=dimension):
                text = (REPO / "primitives" / "stances" / dimension / "off.md").read_text(encoding="utf-8")
                self.assertTrue(text.startswith("# %s: off\n\nNo " % title))

    def test_autonomy_off_keeps_the_default_gate_and_never_tightens_it(self):
        default = GRADER_MODULE.DEFAULT_STANCE
        self.assertEqual(GRADER_MODULE.THRESHOLDS["off"], GRADER_MODULE.THRESHOLDS[default])
        self.assertEqual(GRADER_MODULE.STRICTEST, "ask")
        self.assertEqual(decision.stance_level("off"), decision.stance_level(default))
        self.assertEqual(decision.STANCE_LEVELS, GRADER_MODULE.THRESHOLDS)

    def test_the_autonomy_arm_runs_its_rule_task_and_is_scored_on_handing_back(self):
        spec = shipped_arm("autonomy-off")
        self.assertEqual(spec["tasks"], ["rt-autonomy-dep"])
        self.assertEqual([s["metric"] for s in spec["scores"]], ["handed_back"])
        for ident in ("no-claude-md", "plan-ceremony-off"):
            self.assertEqual((shipped_arm(ident)["tasks"], shipped_arm(ident)["scores"]), ([], []))


class DryRunTests(unittest.TestCase):
    def test_the_dry_run_lists_the_three_new_arms(self):
        data = ABL.load(SHIPPED)
        data = {k: v for k, v in data.items() if k not in ("sha256", "pack")}
        data["arms"] = [dict(a, tasks=[]) for a in data["arms"] if a["id"] in NEW_ARMS]
        data["outcome"] = dict(data["outcome"], tasks=["fixture-one"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sweep.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            out = io.StringIO()
            head = BENCH._git_required(REPO, "rev-parse", "HEAD").stdout.strip()
            argv = ["replay", "--tasks", str(FIXTURE_TASKS), "--ablations", str(path), "--tag", head,
                    "--model", "claude-test", "--exploratory", "--dry-run", "--reps", "1"]
            with mock.patch.object(BENCH, "snapshot", lambda repo, sha, dest: REPO), \
                    redirect_stdout(out), redirect_stderr(io.StringIO()):
                self.assertEqual(BENCH.main(argv), 0)
        text = out.getvalue()
        # fixture-two is named by no arm, so it runs on every arm as well.
        for line in ("no-claude-md removes instructions/CLAUDE.md", "autonomy-off sets stances/autonomy to off",
                     "plan-ceremony-off sets stances/plan-ceremony to off"):
            self.assertRegex(text, r"arm %s: \S+; runs fixture-one, fixture-two\n" % re.escape(line))


if __name__ == "__main__":
    unittest.main()

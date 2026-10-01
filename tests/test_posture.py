# SPDX-License-Identifier: MIT
"""Unit tests for the shared stance and model-ladder resolver.

The resolver is loaded by path, the way every hook loads it, and the hooks that use it run as
subprocesses with the environment each case needs.

Run: python3 -m unittest discover tests
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "claude" / "hooks"
CFG = json.loads((REPO / "config.example.json").read_text())

sys.path.insert(0, str(REPO / "lib"))
from harness_core import catalog  # noqa: E402


def module(path, name="harness_posture"):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


posture = module(HOOKS / "posture.py")


class Layers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def config(self, stances, root=None):
        path = (root or self.home) / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"stances": stances}), encoding="utf-8")
        return path

    def project(self, data):
        path = self.home / "project.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return str(path)

    def env(self, **extra):
        return dict({"HARNESS_HOME": str(self.home)}, **extra)

    def test_the_defaults_are_the_example_configs_and_cannot_drift_from_it(self):
        self.assertEqual(posture.DEFAULT_STANCES, CFG["stances"])

    def test_every_dimension_is_present_without_any_configuration(self):
        self.assertEqual(posture.resolve(self.env())["stances"], posture.DEFAULT_STANCES)

    def test_environment_beats_project_beats_user_beats_default(self):
        self.config({"commits": "from-user", "testing": "from-user", "voice": "from-user"})
        project = self.project({"stances": {"commits": "from-project", "testing": "from-project"}})
        stances = posture.resolve(self.env(HARNESS_PROJECT_CONFIG=project,
                                          HARNESS_STANCE_COMMITS="from-env"))["stances"]
        self.assertEqual(stances["commits"], "from-env")
        self.assertEqual(stances["testing"], "from-project")
        self.assertEqual(stances["voice"], "from-user")
        self.assertEqual(stances["autonomy"], posture.DEFAULT_STANCES["autonomy"])

    def test_a_hyphenated_dimension_is_reachable_from_the_environment(self):
        stances = posture.resolve(self.env(HARNESS_STANCE_PLAN_CEREMONY="lightweight"))["stances"]
        self.assertEqual(stances["plan-ceremony"], "lightweight")

    def test_harness_home_selects_the_user_config(self):
        other = Path(self.tmp.name) / "elsewhere"
        self.config({"commits": "from-harness-home"})
        self.config({"commits": "from-plain-home"}, root=other)
        self.assertEqual(posture.selected("commits", env={"HARNESS_HOME": str(self.home),
                                                          "HOME": str(other)}), "from-harness-home")
        self.assertEqual(posture.selected("commits", env={"HOME": str(other)}), "from-plain-home")

    def test_a_project_file_may_select_stances_and_nothing_else(self):
        project = self.project({"stances": {"commits": "ok"}, "identity": {"name": "someone"}})
        with self.assertRaises(ValueError):
            posture.resolve(self.env(HARNESS_PROJECT_CONFIG=project))
        # A hook asks for the layers it can read rather than failing the tool call it is guarding.
        stances = posture.resolve(self.env(HARNESS_PROJECT_CONFIG=project), strict=False)["stances"]
        self.assertEqual(stances["commits"], posture.DEFAULT_STANCES["commits"])

    def test_a_project_file_whose_stances_are_not_an_object_selects_nothing(self):
        # Not an error: it never was one, and a raise here denies every tool call of the session.
        env = self.env(HARNESS_PROJECT_CONFIG=self.project({"stances": "tiered"}))
        for strict in (True, False):
            with self.subTest(strict=strict):
                self.assertEqual(posture.resolve(env, strict=strict)["stances"],
                                 posture.DEFAULT_STANCES)

    def test_a_user_config_that_exists_but_cannot_be_read_is_an_error_where_it_can_be_reported(self):
        path = self.config({})
        path.unlink()
        path.mkdir()  # a directory where the file should be: present, unreadable, not absent
        with self.assertRaises(OSError):
            posture.resolve(self.env())
        self.assertEqual(posture.resolve(self.env(), strict=False)["stances"], posture.DEFAULT_STANCES)

    def test_a_config_that_is_not_there_is_the_defaults_in_both_modes(self):
        for strict in (True, False):
            with self.subTest(strict=strict):
                self.assertEqual(posture.resolve(self.env(), strict=strict)["stances"],
                                 posture.DEFAULT_STANCES)

    def test_a_missing_project_file_is_an_error_only_where_one_can_be_reported(self):
        env = self.env(HARNESS_PROJECT_CONFIG=str(self.home / "absent.json"))
        with self.assertRaises(OSError):
            posture.resolve(env)
        self.assertEqual(posture.resolve(env, strict=False)["stances"], posture.DEFAULT_STANCES)

    def test_a_malformed_user_config_is_the_defaults_for_a_hook(self):
        self.config({"commits": "ok"}).write_text("{not json", encoding="utf-8")
        self.assertEqual(posture.resolve(self.env(), strict=False)["stances"], posture.DEFAULT_STANCES)
        with self.assertRaises(ValueError):
            posture.resolve(self.env())

    def test_a_layer_that_is_not_an_object_selects_nothing(self):
        for body in ('"a string"', "[]", '{"stances": "tiered"}', '{"stances": {"autonomy": {}}}'):
            with self.subTest(config=body):
                self.config({}).write_text(body, encoding="utf-8")
                self.assertEqual(posture.resolve(self.env(), strict=False)["stances"],
                                 posture.DEFAULT_STANCES)

    def test_a_fallback_answers_a_dimension_no_layer_names(self):
        self.assertEqual(posture.selected("invented", "fallback", env=self.env()), "fallback")


class Ladder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_the_classes_are_the_catalogs_and_the_ladder_is_the_adapters_table(self):
        self.assertEqual(posture.TIER_CLASSES, catalog.TIER_CLASSES)
        self.assertEqual(posture.EFFORTS, catalog.EFFORTS)
        tiers = json.loads((REPO / "adapters" / "claude-code" / "bindings.json").read_text())["tiers"]
        self.assertEqual(posture.ladder(), [tiers[name] for name in catalog.TIER_CLASSES])

    def test_bindings_that_cannot_be_read_are_an_empty_ladder_not_a_crash(self):
        self.assertEqual(posture.ladder(root=Path(self.tmp.name)), [])
        self.assertEqual(posture.ladder(runtime="no-such-runtime"), [])
        broken = Path(self.tmp.name) / "adapters" / "claude-code"
        broken.mkdir(parents=True)
        (broken / "bindings.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(posture.ladder(root=Path(self.tmp.name)), [])


class HooksUseIt(unittest.TestCase):
    """The hooks resolve through the same file: one ladder, one answer, no model names."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()

    def config(self, root, stances):
        path = root / ".config" / "agent-harness" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"stances": stances}), encoding="utf-8")

    def run_hook(self, path, payload, env):
        merged = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_STANCE_")}
        merged.pop("HARNESS_PROJECT_CONFIG", None)
        merged["HOME"] = str(self.home)
        merged.update(env)
        out = subprocess.run([sys.executable, str(path)], input=json.dumps(payload),
                             capture_output=True, text=True, env=merged)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout) if out.stdout.strip() else None

    def spawn(self):
        return {"tool_name": "Agent", "tool_input": {"prompt": "do a thing"}}

    def test_a_hook_honours_harness_home(self):
        elsewhere = Path(self.tmp.name) / "elsewhere"
        self.config(elsewhere, {"delegation": "off"})
        out = self.run_hook(HOOKS / "tier-agent-spawns.py", self.spawn(),
                            {"HARNESS_HOME": str(elsewhere)})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        # Prove the filter bites: without it the hook reads $HOME, which selects no variant.
        self.assertIsNone(self.run_hook(HOOKS / "tier-agent-spawns.py", self.spawn(), {}))

    def test_a_hook_honours_the_project_configuration(self):
        project = Path(self.tmp.name) / "project.json"
        project.write_text(json.dumps({"stances": {"delegation": "off"}}), encoding="utf-8")
        out = self.run_hook(HOOKS / "brief-guard.py", self.spawn(),
                            {"HARNESS_PROJECT_CONFIG": str(project)})
        self.assertIsNone(out)  # `off` leaves the brief to the spawn's own hook
        appended = self.run_hook(HOOKS / "brief-guard.py", self.spawn(), {})
        self.assertIn("400 words", appended["hookSpecificOutput"]["updatedInput"]["prompt"])

    def test_a_ladder_the_hook_cannot_read_leaves_the_spawn_alone_and_says_so(self):
        hooks = Path(self.tmp.name) / "install" / "policy" / "hooks"
        hooks.mkdir(parents=True)
        for name in ("posture.py", "tier-agent-spawns.py"):
            shutil.copy2(HOOKS / name, hooks / name)
        out = self.run_hook(hooks / "tier-agent-spawns.py", self.spawn(), {})
        self.assertNotIn("hookSpecificOutput", out)
        self.assertIn("runs as written", out["systemMessage"])

    def bash(self, path, command, env):
        return self.run_hook(path, {"tool_name": "Bash", "cwd": str(self.home),
                                    "permission_mode": "default",
                                    "tool_input": {"command": command}}, env)

    def test_the_grading_hook_fails_closed_when_the_resolver_cannot_be_loaded(self):
        lonely = Path(self.tmp.name) / "lonely"
        lonely.mkdir()
        for name in ("grade-bash.py", "bash-grader.py", "allow-readonly-bash.py"):
            shutil.copy2(HOOKS / name, lonely / name)
        out = self.bash(lonely / "grade-bash.py", "gh pr create --fill", {})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertIn("unresolved", out["hookSpecificOutput"]["permissionDecisionReason"])
        # With the resolver beside it the same command is the user's default to run unasked.
        self.assertIsNone(self.bash(HOOKS / "grade-bash.py", "gh pr create --fill", {}))

    def test_the_grading_hook_fails_closed_when_the_resolver_raises(self):
        out = self.bash(HOOKS / "grade-bash.py", "gh pr create --fill",
                        {"HARNESS_PROJECT_CONFIG": str(self.home / "absent.json")})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertIn("unresolved", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_a_named_role_gets_no_notice_when_the_ladder_is_short(self):
        hooks = Path(self.tmp.name) / "install" / "policy" / "hooks"
        hooks.mkdir(parents=True)
        for name in ("posture.py", "tier-agent-spawns.py"):
            shutil.copy2(HOOKS / name, hooks / name)
        payload = {"tool_name": "Agent", "tool_input": {"prompt": "x", "subagent_type": "reviewer"}}
        self.assertIsNone(self.run_hook(hooks / "tier-agent-spawns.py", payload, {}))

    def test_no_model_name_is_written_in_hook_code(self):
        names = [m for m in json.loads(
            (REPO / "adapters" / "claude-code" / "bindings.json").read_text())["tiers"].values()]
        for path in sorted(Path(HOOKS.resolve()).glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for name in names:
                self.assertNotIn(name, text, f"{path.name} writes the model name {name}")


if __name__ == "__main__":
    unittest.main()

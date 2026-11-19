# SPDX-License-Identifier: MIT
"""Modes: named selection bundles under `modes/<name>.json`, resolved by `posture.selection()`.

A mode sits above what `harness init` wrote as a default and below every key a user typed; an
unknown mode, a bad mode file, a duplicate name or an unacknowledged core-hook switch is refused
before anything acts on the selection. Contract: `docs/modes.md`.

Run: python3 -m unittest discover tests
"""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from isolation import isolate_home, without_harness_vars

REPO = Path(__file__).resolve().parent.parent
loader = importlib.machinery.SourceFileLoader("harness", str(REPO / "bin" / "harness"))
spec = importlib.util.spec_from_loader("harness", loader)
harness = importlib.util.module_from_spec(spec)
loader.exec_module(harness)

spec = importlib.util.spec_from_file_location("harness_posture_modes", str(REPO / "policy/hooks/posture.py"))
posture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(posture)

SHIPPED = REPO / "primitives" / "modes"
WORKFLOWS = sorted(p.stem for p in (REPO / "primitives" / "workflows").glob("*.md"))


@contextlib.contextmanager
def loud():
    prior = os.environ.pop("HARNESS_QUIET", None)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            yield buf
    finally:
        if prior is not None:
            os.environ["HARNESS_QUIET"] = prior


class Resolution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.roots = self.dir / "roots"
        (self.roots / "modes").mkdir(parents=True)

    def mode(self, name, **keys):
        data = dict({"schema_version": 1, "description": "a test mode"}, **keys)
        (self.roots / "modes" / (name + ".json")).write_text(json.dumps(data), encoding="utf-8")

    def file(self, name, data):
        path = self.dir / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return str(path)

    def user(self, **data):
        return dict({"primitive_roots": [str(self.roots)]}, **data)

    def resolve(self, user=None, strict=True, **env):
        return posture.selection(dict({"HARNESS_HOME": str(self.dir)}, **env), strict=strict,
                                 config=self.user() if user is None else user)

    def test_resolution_runs_default_init_mode_user_project_session(self):
        self.mode("order", stances={"licensing": "off", "build-vs-buy": "off", "commits": "off",
                                    "testing": "off", "voice": "off"})
        user = self.user(mode="order", init_defaults={"stances": {"licensing": "permissive-commercial",
                                                                 "build-vs-buy": "off"}},
                         stances={"licensing": "permissive-commercial", "build-vs-buy": "off",
                                  "commits": "conventional", "testing": "pragmatic", "voice": "concise",
                                  "cost": "frugal"})
        project = self.file("project.json", {"stances": {"testing": "required", "voice": "answer-card"}})
        session = self.file("session.json", {"stances": {"voice": "scannable"}})
        result = self.resolve(user=user, HARNESS_PROJECT_CONFIG=project, HARNESS_SESSION_CONFIG=session)
        expected = {"autonomy": ("execute", "default"),
                    "licensing": ("off", "mode:order"),       # init's default yields to the mode
                    "build-vs-buy": ("off", "mode:order"),
                    "cost": ("frugal", "user"),               # typed, and the mode does not set it
                    "commits": ("conventional", "user"),      # typed beats the mode
                    "testing": ("required", "project"),
                    "voice": ("scannable", "session")}
        for unit, (value, source) in expected.items():
            with self.subTest(unit=unit):
                self.assertEqual((result["stances"][unit], result["sources"]["stances"][unit]), (value, source))
        self.assertEqual(result["shadowed"], {"stances": {"commits": "user", "testing": "project",
                                                          "voice": "session"}})

    def test_an_init_default_with_no_mode_still_resolves_as_the_users(self):
        user = self.user(init_defaults={"stances": {"testing": "pragmatic"}}, stances={"testing": "pragmatic"})
        result = self.resolve(user=user)
        self.assertEqual((result["stances"]["testing"], result["sources"]["stances"]["testing"]),
                         ("pragmatic", "init"))
        self.assertEqual(result["shadowed"], {})

    def test_a_default_edited_in_the_file_after_init_is_typed_and_beats_the_mode(self):
        self.mode("order", stances={"testing": "off"})
        user = self.user(mode="order", init_defaults={"stances": {"testing": "pragmatic"}},
                         stances={"testing": "required"})
        result = self.resolve(user=user)
        self.assertEqual((result["stances"]["testing"], result["sources"]["stances"]["testing"]),
                         ("required", "user"))
        self.assertEqual(result["shadowed"], {"stances": {"testing": "user"}})

    def test_harness_mode_selects_for_one_session_without_touching_the_user_layer(self):
        self.mode("quiet", stances={"voice": "off"})
        user = self.user(stances={"testing": "off"})
        result = self.resolve(user=user, HARNESS_MODE="quiet")
        self.assertEqual((result["mode"], result["sources"]["mode"]), ("quiet", "session"))
        self.assertEqual(result["sources"]["stances"]["voice"], "mode:quiet")
        self.assertNotIn("mode", user)

    def test_an_unknown_mode_is_an_error_naming_the_installed_ones(self):
        with self.assertRaisesRegex(ValueError, "unknown mode 'nope'; installed modes: .*minimal"):
            self.resolve(user=self.user(mode="nope"))
        with self.assertRaisesRegex(ValueError, "unknown mode 'Not A Name'"):
            self.resolve(HARNESS_MODE="Not A Name")

    def test_a_mode_naming_an_unknown_unit_is_an_error(self):
        for kind, unit, value in (("rules", "no-such-rule", "off"), ("stances", "no-such-stance", "off"),
                                  ("workflows", "no-such-workflow", "off"), ("hooks", "no-such-hook", "off")):
            with self.subTest(kind=kind):
                self.mode("typo", **{kind: {unit: value}})
                with self.assertRaisesRegex(ValueError, kind + "." + unit + ", which is not an installed"):
                    self.resolve(user=self.user(mode="typo"))
                # A hook reads the selection non-strict and runs without the broken mode.
                result = self.resolve(user=self.user(mode="typo"), strict=False)
                self.assertEqual(result["sources"]["stances"]["testing"], "default")

    def test_a_mode_carrying_a_non_selection_key_or_no_header_is_an_error(self):
        cases = ((dict(schema_version=1, description="d", permissions="bypass"), "'permissions'"),
                 (dict(schema_version=1, description="d", mode="full"), "'mode'"),
                 (dict(schema_version=1, description="d", sources={}), "'sources'"),
                 (dict(description="d"), "schema_version"),
                 (dict(schema_version=2, description="d"), "schema_version"),
                 (dict(schema_version=1, description=" "), "description"),
                 (dict(schema_version=1, description="d", rules={"secrets": "maybe"}), "on or off"),
                 (dict(schema_version=1, description="d", stances={"testing": None}), "names a variant"),
                 (dict(schema_version=1, description="d", stances={"testing": ""}), "names a variant"),
                 (dict(schema_version=1, description="d", rules=["secrets"]), "a kind is an object"))
        for data, message in cases:
            with self.subTest(data=data):
                (self.roots / "modes" / "bad.json").write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    self.resolve(user=self.user(mode="bad"))

    def test_a_core_hook_off_needs_the_acknowledgement(self):
        for hook in posture.CORE_HOOKS:
            with self.subTest(hook=hook):
                self.mode("bare", hooks={hook: "off"})
                with self.assertRaisesRegex(ValueError, "core hook.*" + hook + ".*core_switches_acknowledged"):
                    self.resolve(user=self.user(mode="bare"))
                result = self.resolve(user=self.user(mode="bare", core_switches_acknowledged=True))
                self.assertEqual((result["hooks"][hook], result["sources"]["hooks"][hook]), ("off", "mode:bare"))
        self.mode("bare", hooks={"grade-bash": "on"})
        self.assertEqual(self.resolve(user=self.user(mode="bare"))["hooks"]["grade-bash"], "on")

    def test_an_unacknowledged_core_hook_off_is_refused_once_and_resolves_on_for_a_hook(self):
        # One refusal path for every layer: the mode is not refused a second time on its own,
        # and a hook keeps the rest of the mode while the core hook keeps enforcing.
        self.mode("bare", hooks={"grade-bash": "off"}, rules={"secrets": "off"})
        with self.assertRaises(ValueError) as caught:
            self.resolve(user=self.user(mode="bare"))
        self.assertEqual(str(caught.exception).count("grade-bash"), 1)
        self.assertIn("mode:bare switches the core hook grade-bash off", str(caught.exception))
        result = self.resolve(user=self.user(mode="bare"), strict=False)
        self.assertEqual((result["hooks"]["grade-bash"], result["sources"]["hooks"]["grade-bash"]), ("on", "default"))
        self.assertEqual((result["rules"]["secrets"], result["sources"]["rules"]["secrets"]), ("off", "mode:bare"))

    def test_a_user_root_may_add_a_mode(self):
        self.mode("mine", rules={"secrets": "off"})
        result = self.resolve(user=self.user(mode="mine"))
        self.assertEqual((result["rules"]["secrets"], result["sources"]["rules"]["secrets"]), ("off", "mode:mine"))
        self.assertIn("mine", posture.modes(self.user())[0])

    def test_a_duplicate_mode_name_is_an_error_even_when_not_selected(self):
        self.mode("minimal", stances={"testing": "required"})
        with self.assertRaisesRegex(ValueError, "mode 'minimal' is defined in both .*primitives/modes"):
            self.resolve()
        # A hook takes the shipped definition, the first root's.
        result = self.resolve(user=self.user(mode="minimal"), strict=False)
        self.assertEqual(result["stances"]["testing"], "off")

    def test_the_output_reads_back_as_a_session_file(self):
        self.mode("quiet", stances={"voice": "off"})
        first = self.resolve(user=self.user(mode="quiet", stances={"voice": "concise"}))
        self.assertEqual(first["shadowed"], {"stances": {"voice": "user"}})
        again = self.resolve(HARNESS_SESSION_CONFIG=self.file("session.json", first))
        self.assertEqual(again["stances"], first["stances"])


class ShippedModes(unittest.TestCase):
    def load(self, name):
        return json.loads((SHIPPED / (name + ".json")).read_text(encoding="utf-8"))

    def test_every_shipped_mode_validates(self):
        for path in sorted(SHIPPED.glob("*.json")):
            with self.subTest(mode=path.stem):
                self.assertEqual(posture.validate_mode(path.stem, self.load(path.stem), {}), [])
        self.assertEqual(sorted(p.stem for p in SHIPPED.glob("*.json")), ["full", "minimal"])

    def test_full_is_empty_apart_from_its_header(self):
        self.assertEqual(sorted(self.load("full")), ["description", "schema_version"])
        env = {"HARNESS_HOME": tempfile.gettempdir(), "HARNESS_MODE": "full"}
        without = posture.selection({"HARNESS_HOME": tempfile.gettempdir()}, config={})
        with_full = posture.selection(env, config={})
        for kind in posture.selection_kinds():
            self.assertEqual(with_full[kind], without[kind])

    def test_minimal_keeps_hooks_cost_delegation_autonomy_decisions_and_drops_the_rest(self):
        data = self.load("minimal")
        self.assertNotIn("hooks", data)
        kept = {"cost", "delegation", "autonomy", "decisions"}
        self.assertEqual(set(data["stances"]), set(posture.DEFAULT_STANCES) - kept)
        self.assertTrue(set(data["stances"].values()) <= {"off", "light"})
        self.assertEqual(data["workflows"], {name: "off" for name in WORKFLOWS})

    def test_docs_list_what_each_shipped_mode_changes(self):
        text = (REPO / "docs" / "modes.md").read_text(encoding="utf-8")
        for path in sorted(SHIPPED.glob("*.json")):
            data = self.load(path.stem)
            self.assertIn("### `" + path.stem + "`", text)
            for kind, units in data.items():
                for unit, value in (units.items() if isinstance(units, dict) else []):
                    with self.subTest(mode=path.stem, unit=unit):
                        self.assertIn("`" + unit + "` " + value, text)
        self.assertEqual(text.count("## Precedence"), 1)


class CommandLine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old_environ = dict(os.environ)
        isolate_home(self.home)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_environ)
        self.tmp.cleanup()

    def cli(self, *args):
        env = dict(without_harness_vars(), HOME=str(self.home))
        return subprocess.run([sys.executable, str(REPO / "bin" / "harness")] + list(args),
                              capture_output=True, text=True, env=env, cwd=str(self.home))

    def init(self):
        with unittest.mock.patch.object(harness, "_detect_github", return_value=""), \
             unittest.mock.patch.object(harness, "_detect_git_name", return_value=""), \
             unittest.mock.patch.object(harness, "_detect_timezone", return_value="UTC"):
            args = argparse.Namespace(force=False, yes=True, preset=None, name=None, role=None,
                                      github=None, timezone=None)
            self.assertEqual(harness.cmd_init(args), 0)
        return json.loads(harness.config_path().read_text())

    def test_init_marks_every_stance_it_wrote_as_a_default(self):
        cfg = self.init()
        self.assertEqual(cfg["init_defaults"], {"stances": cfg["stances"]})

    def test_interactive_init_marks_only_the_answers_enter_took(self):
        first = harness.STANCE_NAMES[0]
        answers = iter(["A Name", "", "Does a thing", "", "UTC", "", "", "off"]
                       + [""] * (len(harness.STANCE_NAMES) - 1))
        with unittest.mock.patch("builtins.input", lambda _prompt: next(answers)), \
             unittest.mock.patch.object(harness.sys.stdin, "isatty", return_value=True), \
             unittest.mock.patch.object(harness, "_detect_github", return_value=""):
            with loud():
                harness.cmd_init(argparse.Namespace(force=False))
        cfg = json.loads(harness.config_path().read_text())
        self.assertEqual(sorted(cfg["init_defaults"]["stances"]), sorted(set(harness.STANCE_NAMES) - {first}))

    def test_interactive_init_counts_a_typed_offered_value_as_a_choice(self):
        first = harness.STANCE_NAMES[0]
        offered = dict(harness.example_config().get("stances", {}),
                       **harness.STANCE_PRESETS["software"]).get(first, harness.stance_variants(first)[0])
        answers = iter(["A Name", "", "Does a thing", "", "UTC", "", "", offered]
                       + [""] * (len(harness.STANCE_NAMES) - 1))
        with unittest.mock.patch("builtins.input", lambda _prompt: next(answers)), \
             unittest.mock.patch.object(harness.sys.stdin, "isatty", return_value=True), \
             unittest.mock.patch.object(harness, "_detect_github", return_value=""):
            with loud():
                harness.cmd_init(argparse.Namespace(force=False))
        cfg = json.loads(harness.config_path().read_text())
        self.assertEqual(cfg["stances"][first], offered)
        self.assertNotIn(first, cfg["init_defaults"]["stances"])
        self.assertEqual(sorted(cfg["init_defaults"]["stances"]), sorted(set(harness.STANCE_NAMES) - {first}))

    def test_setting_a_stance_by_hand_takes_it_out_of_the_defaults(self):
        self.init()
        harness.config_set("stances.testing", "required")
        cfg = json.loads(harness.config_path().read_text())
        self.assertNotIn("testing", cfg["init_defaults"]["stances"])
        self.assertIn("voice", cfg["init_defaults"]["stances"])

    def test_set_on_a_missing_file_marks_the_example_stances_as_defaults(self):
        harness.config_set("mode", "minimal")
        cfg = json.loads(harness.config_path().read_text())
        self.assertEqual(cfg["mode"], "minimal")
        self.assertEqual(cfg["init_defaults"]["stances"], cfg["stances"])

    def test_config_set_refuses_an_unknown_mode_and_writes_nothing(self):
        self.init()
        before = harness.config_path().read_text()
        with self.assertRaisesRegex(SystemExit, "unknown mode 'nope'"):
            harness.config_set("mode", "nope")
        self.assertEqual(harness.config_path().read_text(), before)

    def test_sync_refuses_an_unknown_mode_before_it_writes_a_file(self):
        self.init()
        cfg = json.loads(harness.config_path().read_text())
        cfg["mode"] = "nope"
        harness.config_path().write_text(json.dumps(cfg), encoding="utf-8")
        out = self.cli("sync")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("unknown mode 'nope'", out.stderr)
        self.assertFalse((self.home / ".claude").exists())

    def test_minimal_after_init_syncs_to_the_documented_state_with_no_drift(self):
        self.init()
        harness.config_set("mode", "minimal")
        synced = self.cli("sync")
        self.assertEqual(synced.returncode, 0, synced.stdout + synced.stderr)
        links = self.home / ".claude" / "rules" / "harness-stances"
        minimal = json.loads((SHIPPED / "minimal.json").read_text(encoding="utf-8"))["stances"]
        for name, default in posture.DEFAULT_STANCES.items():
            with self.subTest(stance=name):
                self.assertEqual(os.path.realpath(links / (name + ".md")),
                                 str((REPO / "primitives" / "stances" / name /
                                      (minimal.get(name, default) + ".md")).resolve()))
        commands = self.home / ".claude" / "commands"
        for workflow in json.loads((SHIPPED / "minimal.json").read_text(encoding="utf-8"))["workflows"]:
            with self.subTest(workflow=workflow):
                self.assertFalse(os.path.lexists(commands / (workflow + ".md")))
        diff = self.cli("diff")
        self.assertEqual(diff.returncode, 0, diff.stdout + diff.stderr)
        self.assertIn("no drift", diff.stdout)

    def test_selection_names_a_mode_key_a_typed_key_shadowed(self):
        self.init()
        harness.config_set("mode", "minimal")
        harness.config_set("stances.testing", "required")
        out = self.cli("selection")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("shadowed stances.testing: the mode's value is overridden by user", out.stdout)
        self.assertIn("stances.voice=off (mode:minimal)", out.stdout)
        data = json.loads(self.cli("selection", "--json").stdout)
        self.assertEqual(data["shadowed"], {"stances": {"testing": "user"}})


if __name__ == "__main__":
    unittest.main()

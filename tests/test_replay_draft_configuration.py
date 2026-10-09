"""The harness arm built from a draft's configuration (#1269): a configuration that differs from the
one the draft inherited is installed in the arm's image and every row records its digest; the image
holds it and nothing of the host's; it is admitted only when it resolves strictly against the arm's
own commit, and refused with a named reason otherwise; and an inherited or empty configuration
builds the plain harness arm, digests and all. Every launch is a fake; no image is built and no model
is called."""
import copy
import hashlib
import importlib.util
import io
import json
import os
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from test_cost_bench import BENCH, TASK, arm_record
from test_cost_bench_ablations import FIXTURE_TASKS
from test_harness import REPO

ARMS = BENCH.arms
POSTURE = importlib.util.spec_from_file_location("posture_draft_config", REPO / "policy" / "hooks" / "posture.py")
POSTURE_MODULE = importlib.util.module_from_spec(POSTURE)
POSTURE.loader.exec_module(POSTURE_MODULE)
HARNESS = {"ref": "v9.9.9", "commit": "c" * 40}
EDITED = {"identity": {"name": "A Name"}, "stances": {"voice": "concise"}}


def personal_root(path):
    """A personal root as an apply leaves it beside the user's configuration: one stance variant."""
    (path / "stances" / "voice").mkdir(parents=True)
    (path / "stances" / "voice" / "mine.md").write_text("# Voice: mine\n\nShort answers.\n")
    return path


def configured_record(configuration, sha=None, installed=None, roots=None, held=None):
    """A built configured harness arm, as `replay_arms.build_arm` would record it; `installed` is
    the configuration file the image actually holds, the declared one by default, and `held` the
    files of its copied root 0."""
    record = copy.deepcopy(arm_record("harness"))
    decl = record["declaration"]
    decl[ARMS.CONFIGURATION] = configuration
    decl[ARMS.CONFIGURATION_SHA256] = sha or ARMS.configuration_sha256(configuration)
    if roots:
        decl[ARMS.CONFIGURATION_ROOTS] = roots
    declared = ARMS.selection_bytes(configuration)
    decl["components"].append({"name": ARMS.CONFIGURATION, "version": "sha256:" + hashlib.sha256(declared).hexdigest()})
    data = ARMS.selection_bytes(configuration if installed is None else installed)
    entries = record["manifest"]["entries"] + [{"path": ARMS.USER_CONFIG, "kind": "file", "mode": "0644",
                                                 "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}]
    entries += [{"path": "harness:.primitive-roots/0/" + relative, "kind": "file", "mode": "0644", "size": 1,
                 "sha256": sha256} for relative, sha256 in sorted((held or {}).items())]
    record["manifest"] = dict(record["manifest"], entries=entries, summary=ARMS.arm_manifest.summary(entries))
    record.update(declaration_sha256=ARMS.digest(decl), manifest_sha256=ARMS.digest(record["manifest"]))
    return record


def write(tmp, data, name):
    path = Path(tmp) / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


class AppliedTests(unittest.TestCase):
    """AC1 and AC4: what is applied, and what is not."""

    def test_an_edited_configuration_is_applied_and_its_digest_is_the_drafts(self):
        self.assertIs(ARMS.applied_configuration(EDITED, {"identity": {"name": "A Name"}}), EDITED)
        canonical = json.dumps(EDITED, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.assertEqual(ARMS.configuration_sha256(EDITED), hashlib.sha256(canonical).hexdigest())

    def test_an_inherited_or_empty_configuration_is_not_applied(self):
        self.assertIsNone(ARMS.applied_configuration({}))
        self.assertIsNone(ARMS.applied_configuration(None))
        reordered = {"stances": {"voice": "concise"}, "identity": {"name": "A Name"}}
        self.assertIsNone(ARMS.applied_configuration(EDITED, reordered))

    def test_a_configured_declaration_installs_it_through_the_selected_stage(self):
        inputs = ARMS.qualification_inputs()
        decl = ARMS.declaration("harness", inputs, HARNESS, effort="high", configuration=EDITED)
        self.assertEqual(ARMS.target(decl), ARMS.SELECTED_TARGET)
        self.assertEqual(decl[ARMS.CONFIGURATION_SHA256], ARMS.configuration_sha256(EDITED))
        component = [c for c in decl["components"] if c["name"] == ARMS.CONFIGURATION]
        self.assertEqual(component, [{"name": ARMS.CONFIGURATION, "version": "sha256:" + hashlib.sha256(
            ARMS.selection_bytes(EDITED)).hexdigest()}])
        plain = ARMS.declaration("harness", inputs, HARNESS, effort="high")
        self.assertNotEqual(ARMS.image_name(decl), ARMS.image_name(plain))
        self.assertEqual(ARMS.pair_differences(arm_record("bare"), configured_record(EDITED)), [])

    def test_a_configuration_is_refused_on_the_bare_arm_or_beside_a_selection(self):
        inputs = ARMS.qualification_inputs()
        for kwargs in ({"arm": "bare", "harness": None, "configuration": EDITED},
                       {"arm": "harness", "harness": HARNESS, "configuration": EDITED,
                        "selection": {"stances": {"voice": "concise"}}}):
            with self.subTest(kwargs=sorted(kwargs)), self.assertRaises(SystemExit):
                ARMS.declaration(kwargs.pop("arm"), inputs, kwargs.pop("harness"), **kwargs)

    def test_the_inherited_and_empty_configurations_declare_the_plain_arm(self):
        head = BENCH._git_required(REPO, "rev-parse", "HEAD").stdout.strip()
        plain = BENCH.declarations([head])
        with tempfile.TemporaryDirectory() as tmp:
            for config, inherited in (({}, None), (EDITED, EDITED)):
                args = mock.Mock(harness_config=write(tmp, config, "config.json"),
                                 inherited_config=inherited and write(tmp, inherited, "base.json"),
                                 pair=None, ablations=None, design=None, arm_config=None, stance_cost=None)
                with self.subTest(config=config):
                    configuration = BENCH.harness_configuration(args)
                    self.assertIsNone(configuration)
                    same = BENCH.declarations([head], configuration=configuration)
                    self.assertEqual(same, plain)
                    self.assertEqual(ARMS.image_name(same[1][0][1]), ARMS.image_name(plain[1][0][1]))
                    self.assertEqual(ARMS.digest(same[1][0][1]), ARMS.digest(plain[1][0][1]))

    def test_every_harness_row_records_the_digest_it_measured_and_no_other_row_changes(self):
        sha = "d" * 64
        self.assertEqual(BENCH.arm_stamp(configured_record(EDITED, sha))["arm_configuration_sha256"], sha)
        for arm in ("bare", "harness"):
            with self.subTest(arm=arm):
                self.assertNotIn("arm_configuration_sha256", BENCH.arm_stamp(arm_record(arm)))


class ImageTests(unittest.TestCase):
    """AC2: the image holds the draft's configuration and nothing of the host's."""

    def test_a_primitive_root_in_the_checkout_is_rerooted_into_the_image(self):
        config = dict(EDITED, primitive_roots=[str(REPO / "personal-primitives")])
        mapped = ARMS.arm_configuration(config, REPO, ARMS.HARNESS_ROOT)
        self.assertEqual(mapped["primitive_roots"], [ARMS.HARNESS_ROOT + "/personal-primitives"])
        self.assertEqual(config["primitive_roots"], [str(REPO / "personal-primitives")])  # not mutated

    def test_the_build_context_holds_the_observer_the_commit_and_the_configuration_only(self):
        decl = ARMS.declaration("harness", ARMS.qualification_inputs(), HARNESS, configuration=EDITED)
        with tempfile.TemporaryDirectory() as tmp:
            context = ARMS.build_context(decl, tmp, lambda repo, sha, dest: Path(dest).mkdir())
            self.assertEqual(sorted(p.name for p in context.iterdir()), ["harness", "observer", ARMS.SELECTION_FILE])
            self.assertEqual((context / ARMS.SELECTION_FILE).read_bytes(), ARMS.selection_bytes(EDITED))

    def test_a_configuration_naming_a_host_path_in_a_value_or_a_key_is_refused(self):
        home = str(Path.home() / "work")
        for config in (dict(EDITED, remote_control={"folders": [home]}), dict(EDITED, x={home: "on"})):
            with self.subTest(config=sorted(config)):
                self.assertEqual(ARMS.configuration_problem(config, POSTURE_MODULE, REPO)[0], ARMS.CONFIG_HOST_PATH)
        hosted = configured_record(dict(EDITED, x={str(Path.home()): "on"}))
        self.assertIn("host path", ARMS._no_host_path(hosted))

    def test_a_root_outside_the_checkout_is_copied_into_the_image_with_no_host_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            personal = personal_root(Path(tmp) / ".config" / "agent-harness" / "personal-primitives")
            config = dict(EDITED, stances={"voice": "mine"}, primitive_roots=[str(personal)])
            self.assertIsNone(ARMS.configuration_problem(config, POSTURE_MODULE, REPO, paths=[tmp]))
            installed = ARMS.arm_configuration(config, REPO, ARMS.HARNESS_ROOT)
            self.assertEqual(installed["primitive_roots"], [ARMS.HARNESS_ROOT + "/.primitive-roots/0"])
            self.assertIsNone(ARMS.host_path_reason(strings=ARMS._strings(installed), paths=[tmp]))
            sources, roots = ARMS.configuration_roots(config, REPO)
            self.assertEqual(sources, {"0": os.path.realpath(str(personal))})
            self.assertEqual(sorted(roots["0"]), ["stances/voice/mine.md"])
            decl = ARMS.declaration("harness", ARMS.qualification_inputs(), HARNESS, configuration=installed,
                                    configuration_roots=roots)
            self.assertNotIn(tmp, json.dumps(decl))
            context = ARMS.build_context(decl, Path(tmp) / "ctx", lambda repo, sha, dest: Path(dest).mkdir(),
                                         root_sources=sources)
            self.assertEqual((context / "harness" / ".primitive-roots" / "0" / "stances" / "voice" / "mine.md")
                             .read_text(), (personal / "stances" / "voice" / "mine.md").read_text())
            (personal / "stances" / "voice" / "mine.md").write_text("changed\n")
            with self.assertRaises(SystemExit) as caught:
                ARMS.build_context(decl, Path(tmp) / "ctx2", lambda repo, sha, dest: Path(dest).mkdir(),
                                   root_sources=sources)
            self.assertIn("changed since it was declared", str(caught.exception))

    def test_an_unreadable_root_outside_the_checkout_is_refused_by_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            for root in (str(Path(tmp) / "missing"), "~/no-such-primitives-root"):
                with self.subTest(root=root):
                    self.assertEqual(ARMS.configuration_problem(dict(EDITED, primitive_roots=[root]),
                                                                POSTURE_MODULE, REPO)[0], ARMS.CONFIG_ROOT_UNREADABLE)

    def test_a_root_adding_a_role_or_workflow_is_refused_before_any_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            for kind in ("roles", "workflows"):
                root = Path(tmp) / kind
                (root / kind).mkdir(parents=True)
                (root / kind / "extra.md").write_text("# Extra\n")
                with self.subTest(kind=kind):
                    problem = ARMS.configuration_problem(dict(EDITED, primitive_roots=[str(root)]), POSTURE_MODULE, REPO)
                    self.assertEqual(problem[0], ARMS.CONFIG_ROOT_UNSUPPORTED)
                    self.assertIn("%s/extra.md" % kind, problem[1])

    def test_admission_holds_the_copied_roots_to_their_declaration(self):
        roots = {"0": {"stances/voice/mine.md": "a" * 64}}
        record = configured_record(EDITED, roots=roots, held={"stances/voice/mine.md": "a" * 64})
        self.assertIsNone(ARMS._configuration_is_declared(record))
        changed = configured_record(EDITED, roots=roots, held={"stances/voice/mine.md": "b" * 64})
        self.assertIn("configuration roots differ", ARMS._configuration_is_declared(changed))
        undeclared = configured_record(EDITED, held={"stances/voice/mine.md": "a" * 64})
        self.assertIn("configuration roots differ", ARMS._configuration_is_declared(undeclared))

    def test_admission_accepts_the_installed_configuration_and_refuses_any_other(self):
        record = configured_record(EDITED)
        self.assertIsNone(ARMS._configuration_is_declared(record))
        self.assertIsNone(ARMS._matches_its_declaration(record))
        swapped = configured_record(EDITED, installed={"identity": {"name": "Host"}})
        self.assertIn("differs from the declared configuration", ARMS._configuration_is_declared(swapped))
        undeclared = configured_record(EDITED)
        del undeclared["declaration"][ARMS.CONFIGURATION]
        self.assertIn("declared with no configuration", ARMS._configuration_is_declared(undeclared))
        hosted = configured_record(dict(EDITED, note=str(Path.home())))
        self.assertIn("host path", ARMS._no_host_path(hosted))


class ResolutionTests(unittest.TestCase):
    """AC3: strict resolution against the commit, and a named reason for a refusal."""

    def problem(self, config):
        return ARMS.configuration_problem(config, POSTURE_MODULE, REPO)

    def test_a_configuration_that_resolves_strictly_is_admitted(self):
        self.assertIsNone(self.problem(EDITED))
        self.assertIsNone(self.problem(dict(EDITED, rules={"secrets": "off"})))

    def test_a_configuration_the_resolver_refuses_is_refused_by_name(self):
        for config in ({"stances": "concise"}, {"rules": {"secrets": "maybe"}}, {"mode": "no-such-mode"}):
            with self.subTest(config=config):
                problem = self.problem(config)
                self.assertIsNotNone(problem)
                self.assertEqual(problem[0], ARMS.CONFIG_UNRESOLVED)
                self.assertTrue(problem[1])

    def test_a_realistic_inherited_configuration_with_an_applied_personal_root_is_admitted(self):
        """The example configuration every install starts from, with a personal root an apply wrote
        under the configuration directory and one stance edited to use it."""
        with tempfile.TemporaryDirectory() as tmp:
            personal = personal_root(Path(tmp) / ".config" / "agent-harness" / "personal-primitives")
            config = json.loads((REPO / "config.example.json").read_text(encoding="utf-8"))
            config.update(primitive_roots=[str(personal)], stances=dict(config["stances"], voice="mine"))
            self.assertIsNone(ARMS.configuration_problem(config, POSTURE_MODULE, REPO))

    def test_a_configuration_that_is_not_an_object_of_finite_json_is_invalid(self):
        for config in ([], {"x": float("nan")}, {"primitive_roots": "here"}):
            with self.subTest(config=config):
                self.assertEqual(self.problem(config)[0], ARMS.CONFIG_INVALID)


class CommandTests(unittest.TestCase):
    """The replay and `check-config` refuse before anything is built, and the dry run names it."""

    def main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(BENCH, "snapshot", lambda repo, sha, dest: REPO), \
                mock.patch.object(BENCH, "contamination_by_task", lambda tasks, repo, commit, tmp=None: []), \
                redirect_stdout(out), redirect_stderr(err):
            status = BENCH.main(argv)
        return status, out.getvalue()

    def replay(self, *extra):
        head = BENCH._git_required(REPO, "rev-parse", "HEAD").stdout.strip()
        return self.main(["replay", "--tasks", str(FIXTURE_TASKS), "--tag", head, "--model", "claude-test",
                          "--exploratory", "--dry-run", "--reps", "1"] + list(extra))

    def test_the_dry_run_names_the_configuration_it_applies(self):
        with tempfile.TemporaryDirectory() as tmp:
            status, text = self.replay("--harness-config", write(tmp, EDITED, "c.json"))
        self.assertEqual(status, 0)
        self.assertIn("harness configuration sha256 %s applied" % ARMS.configuration_sha256(EDITED), text)

    def test_a_refused_configuration_stops_the_replay_before_anything_is_built(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(ARMS, "build_arm", side_effect=AssertionError("built")):
            with self.assertRaises(SystemExit) as caught:
                self.replay("--harness-config", write(tmp, {"rules": {"secrets": "maybe"}}, "c.json"))
        self.assertIn("refusing the harness configuration before any spend (config_unresolved)",
                      str(caught.exception))

    def test_a_configuration_is_refused_beside_flags_that_set_the_arm_another_way(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, EDITED, "c.json")
            for extra in (["--stance-cost", "frugal"], ["--pair", "x.json"]):
                with self.subTest(extra=extra), self.assertRaises(SystemExit) as caught:
                    self.replay("--harness-config", path, *extra)
                self.assertIn("refused with", str(caught.exception))

    def test_check_config_answers_an_invalid_configuration_in_json(self):
        head = BENCH._git_required(REPO, "rev-parse", "HEAD").stdout.strip()
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in (("array", "[]"), ("nan", '{"x": NaN}'), ("garbage", "{")):
                path = Path(tmp) / (name + ".json")
                path.write_text(text, encoding="utf-8")
                with self.subTest(name=name):
                    got, out = self.main(["check-config", "--tag", head, "--harness-config", str(path)])
                    answer = json.loads(out)
                    self.assertEqual((got, answer["code"], answer["applied"]), (2, ARMS.CONFIG_INVALID, False))

    def test_check_config_refuses_a_role_from_an_extra_root(self):
        head = BENCH._git_required(REPO, "rev-parse", "HEAD").stdout.strip()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "root" / "roles").mkdir(parents=True)
            (Path(tmp) / "root" / "roles" / "extra.md").write_text("# Extra\n")
            got, out = self.main(["check-config", "--tag", head, "--harness-config",
                                  write(tmp, dict(EDITED, primitive_roots=[str(Path(tmp) / "root")]), "c.json")])
        self.assertEqual((got, json.loads(out)["code"]), (2, ARMS.CONFIG_ROOT_UNSUPPORTED))

    def test_check_config_answers_in_one_json_line(self):
        head = BENCH._git_required(REPO, "rev-parse", "HEAD").stdout.strip()
        with tempfile.TemporaryDirectory() as tmp:
            for config, status, code in ((EDITED, 0, None), ({"rules": {"secrets": "maybe"}}, 2, "config_unresolved"),
                                         ({}, 0, None)):
                with self.subTest(config=config):
                    got, text = self.main(["check-config", "--tag", head, "--harness-config",
                                           write(tmp, config, "c.json")])
                    answer = json.loads(text)
                    self.assertEqual((got, answer["code"]), (status, code))
                    self.assertEqual(answer["applied"], code is None and bool(config))
                    self.assertEqual(answer["config_sha256"],
                                     ARMS.configuration_sha256(config) if config else None)
                    self.assertEqual(bool(answer["reason"]), code is not None)



class ReplayTagTests(unittest.TestCase):
    """A configured harness arm resolves its profile with the configuration and keeps no history row."""

    def run_tag(self, tmp, harness):
        tmp = Path(tmp)
        args = types.SimpleNamespace(
            tasks=str(FIXTURE_TASKS), model="claude-test", tier=None, series_source=b"set", tmp=str(tmp),
            reps=1, run_cap=1.0, spend_cap=10.0, stance_cost=None, raw=False, change_note=None,
            skip_preflight=True, bucket=None, predicted_ratio=None, allow_surface_drift=False,
            history_dir=str(tmp / "history"), set_size=1)
        common = {"tasks": [TASK], "plan": [(TASK, 1, "bare"), (TASK, 1, "harness")], "bare": {}, "out": tmp / "out",
                  "prices": {}, "network": "n", "proxy": "p", "cli_version": "2.0", "client_env": {},
                  "protocol": {"evidence": "pre-registered"}}
        rows = [{"arm": arm, "task": TASK["id"], "rep": 1, "evidence": BENCH.experiment_protocol.PREREGISTERED}
                for arm in ("bare", "harness")]
        seen = {}

        def replay(tasks, opts, out=None):
            seen["selection"] = BENCH.declared_selection(opts, "harness")
            return rows, False

        err = io.StringIO()
        with mock.patch.object(BENCH, "snapshot", lambda repo, sha, dest: Path(dest)), \
                mock.patch.object(BENCH, "tag_version", lambda repo, commit, ref: "9.9.9"), \
                mock.patch.object(BENCH, "replay", replay), \
                mock.patch.object(BENCH, "history_row", lambda *a, **k: {"version": "9.9.9"}), \
                mock.patch.object(BENCH, "render_history", lambda kept: "table\n"), \
                redirect_stdout(io.StringIO()), redirect_stderr(err):
            status = BENCH.replay_tag("v9.9.9", args, common, harness)
        return status, err.getvalue(), sorted(p.name for p in (tmp / "history").glob("*")), seen, tmp

    def test_a_configured_arm_resolves_with_its_configuration_and_writes_no_history_row(self):
        installed = dict(EDITED, primitive_roots=[ARMS.HARNESS_ROOT + "/personal-primitives"])
        record = dict(configured_record(installed), harness_commit="0" * 40)
        with tempfile.TemporaryDirectory() as tmp:
            status, err, written, seen, root = self.run_tag(tmp, record)
            profile = [p for p in root.iterdir() if p.name.startswith("cost-profile-")]
        self.assertEqual(status, 0)
        self.assertEqual(written, [])
        self.assertIn("a run with a harness configuration writes no history row", err)
        self.assertEqual(seen["selection"]["stances"], {"voice": "concise"})
        self.assertTrue(seen["selection"]["primitive_roots"][0].endswith("/checkout/personal-primitives"))
        self.assertEqual(profile, [])  # the profile clone is gone

    def test_the_plain_arm_still_writes_its_history_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            status, _err, written, seen, _root = self.run_tag(tmp, {"harness_commit": "0" * 40})
        self.assertEqual(status, 0)
        self.assertEqual(written, [BENCH.HISTORY.name, BENCH.HISTORY_MD.name])
        self.assertIsNone(seen["selection"])


if __name__ == "__main__":
    unittest.main()

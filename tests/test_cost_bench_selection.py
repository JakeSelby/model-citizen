"""Declared-selection arms (#514): a harness arm built with a selection in the user-config shape,
installed as the image user's configuration before the sync, and admitted only when the installed
file is the declared one. Every Docker command goes to a fake launcher."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from test_cost_bench_arms import ARMS, COMMIT, INPUTS, LISTER, Docker, fake_snapshot

SELECTION = {"rules": {"secrets": "off"}}
EXAMPLE_SHA = "e" * 64


def sha(data):
    return hashlib.sha256(data).hexdigest()


def harness_record(selection=None, config_sha=None, config=True):
    """An admissible harness-arm record, with the user configuration `config_sha` (default: the
    declared selection's bytes, or the example copy when no selection is declared)."""
    decl = ARMS.declaration("harness", INPUTS, {"ref": "v1", "commit": COMMIT}, effort="high",
                            selection=selection)
    if config_sha is None:
        config_sha = sha(ARMS.selection_bytes(selection)) if selection is not None else EXAMPLE_SHA
    observer_sha = ARMS.file_sha(ARMS.OBSERVER_SOURCE)
    entries = [{"path": "observer:observe.py", "kind": "file", "sha256": observer_sha},
               {"path": ARMS.EXAMPLE_CONFIG, "kind": "file", "sha256": EXAMPLE_SHA}]
    if config:
        entries.append({"path": ARMS.USER_CONFIG, "kind": "file", "sha256": config_sha})
    manifest = {"schema": LISTER.SCHEMA, "claude_code_version": "1.2.3",
                "cli_packages": ["@anthropic-ai/claude-code" + "@1.2.3"], "harness_commit": COMMIT,
                "roots": {"home": "/home/agent", "observer": "/opt/model-citizen-observer",
                          "harness": "/opt/model-citizen"},
                "summary": LISTER.summary(entries), "entries": entries,
                "environment": {ARMS.EFFORT_ENV: None}}
    return {"label": ARMS.label(decl), "image": "i", "image_id": "sha256:1", "declaration": decl,
            "declaration_sha256": ARMS.digest(decl), "manifest": manifest,
            "manifest_sha256": ARMS.digest(manifest), "protocol": {"evidence": "exploratory"}}


class SelectionDeclarationTests(unittest.TestCase):
    def test_a_selection_is_declared_as_a_component_digesting_the_installed_bytes(self):
        decl = ARMS.declaration("harness", INPUTS, {"ref": "v1", "commit": COMMIT}, selection=SELECTION)
        self.assertEqual(decl["selection"], SELECTION)
        self.assertEqual(decl["components"][-1],
                         {"name": "selection", "version": "sha256:" + sha(ARMS.selection_bytes(SELECTION))})

    def test_an_arm_without_a_selection_declares_what_it_always_did(self):
        plain = ARMS.declaration("harness", INPUTS, {"ref": "v1", "commit": COMMIT})
        self.assertNotIn("selection", plain)
        self.assertNotIn("selection", [c["name"] for c in plain["components"]])
        selected = ARMS.declaration("harness", INPUTS, {"ref": "v1", "commit": COMMIT}, selection=SELECTION)
        self.assertNotEqual(ARMS.image_name(plain), ARMS.image_name(selected))

    def test_a_selection_on_the_bare_arm_or_in_another_shape_is_refused(self):
        with self.assertRaises(SystemExit):
            ARMS.declaration("bare", INPUTS, selection=SELECTION)
        for bad in ([], {"rules": "off"}, {"rules": {}}, {"rules": {"secrets": ""}}, {"Rules": {"a": "off"}}):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                ARMS.declaration("harness", INPUTS, {"ref": "v1", "commit": COMMIT}, selection=bad)


class SelectionBuildTests(unittest.TestCase):
    def test_the_context_carries_the_selection_file_and_the_build_targets_its_stage(self):
        decl = ARMS.declaration("harness", INPUTS, {"ref": "v1", "commit": COMMIT}, selection=SELECTION)
        with tempfile.TemporaryDirectory() as tmp:
            context = ARMS.build_context(decl, tmp, fake_snapshot([]))
            self.assertEqual((context / ARMS.SELECTION_FILE).read_bytes(), ARMS.selection_bytes(SELECTION))
        command = ARMS.build_command(decl, "<context>", ARMS.image_name(decl))
        self.assertEqual(command[command.index("--target") + 1], ARMS.SELECTED_TARGET)
        self.assertIn("HARNESS_COMMIT=" + COMMIT, command)

    def test_an_arm_without_a_selection_builds_its_own_stage_with_no_selection_file(self):
        decl = ARMS.declaration("harness", INPUTS, {"ref": "v1", "commit": COMMIT})
        with tempfile.TemporaryDirectory() as tmp:
            context = ARMS.build_context(decl, tmp, fake_snapshot([]))
            self.assertFalse((context / ARMS.SELECTION_FILE).exists())
        command = ARMS.build_command(decl, "<context>", ARMS.image_name(decl))
        self.assertEqual(command[command.index("--target") + 1], "harness")

    def test_the_dockerfile_installs_the_selection_before_the_sync(self):
        text = ARMS.ARM_DOCKERFILE.read_text(encoding="utf-8")
        stage = text[text.index("AS %s" % ARMS.SELECTED_TARGET):]
        self.assertLess(stage.index(".config/agent-harness/config.json"), stage.index("bin/harness sync"))
        self.assertIn("COPY --chown=agent:agent %s" % ARMS.SELECTION_FILE, stage)

    def test_a_built_selected_arm_is_recorded_with_its_selection(self):
        decl = ARMS.declaration("harness", INPUTS, {"ref": "v1", "commit": COMMIT}, selection=SELECTION)
        docker = Docker()
        with tempfile.TemporaryDirectory() as tmp:
            record = ARMS.build_arm(decl, tmp, fake_snapshot([]), launch=docker)
            written = json.loads(Path(record["paths"]["declaration"]).read_text(encoding="utf-8"))
        self.assertEqual(written["selection"], SELECTION)
        self.assertIn(ARMS.SELECTED_TARGET, docker.commands("docker", "build")[0])


class SelectionAdmissionTests(unittest.TestCase):
    def test_the_declared_selection_file_is_admitted(self):
        self.assertIsNone(ARMS.admit(harness_record(SELECTION)))

    def test_a_harness_arm_holding_the_syncs_example_copy_is_admitted(self):
        self.assertIsNone(ARMS.admit(harness_record()))

    def test_a_selection_file_nobody_declared_is_refused(self):
        with self.assertRaises(SystemExit) as caught:
            ARMS.admit(harness_record(config_sha=sha(ARMS.selection_bytes(SELECTION))))
        self.assertIn("is not in the declaration", str(caught.exception))

    def test_a_selection_file_whose_hash_differs_from_the_declared_one_is_refused(self):
        with self.assertRaises(SystemExit) as caught:
            ARMS.admit(harness_record(SELECTION, config_sha=sha(b"{}\n")))
        self.assertIn("differs from the declared selection", str(caught.exception))

    def test_a_declared_selection_that_was_never_installed_is_refused(self):
        with self.assertRaises(SystemExit) as caught:
            ARMS.admit(harness_record(SELECTION, config=False))
        self.assertIn("is not installed", str(caught.exception))

    def test_a_selection_component_that_does_not_match_the_selection_is_refused(self):
        record = harness_record(SELECTION)
        record["declaration"]["selection"] = {"rules": {"testing": "off"}}
        record["declaration_sha256"] = ARMS.digest(record["declaration"])
        with self.assertRaises(SystemExit) as caught:
            ARMS.admit(record)
        self.assertIn("selection component does not match", str(caught.exception))

    def test_the_bare_arm_holding_any_user_configuration_is_refused(self):
        decl = ARMS.declaration("bare", INPUTS, effort="high")
        record = harness_record()
        record["declaration"] = decl
        record["declaration_sha256"] = ARMS.digest(decl)
        self.assertIn("is not in the declaration", ARMS._configuration_is_declared(record) or "")

    def test_pair_parity_with_bare_counts_the_selection_as_part_of_the_treatment(self):
        bare_decl = ARMS.declaration("bare", INPUTS, effort="high")
        selected = harness_record(SELECTION)
        bare = {"declaration": bare_decl, "manifest": {"entries": [], "roots": {}}}
        lines = ARMS.pair_differences(bare, dict(selected, manifest={"entries": [], "roots": {}}))
        self.assertFalse([line for line in lines if "selection" in line], lines)


if __name__ == "__main__":
    unittest.main()

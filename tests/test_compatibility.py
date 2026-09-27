"""Published support cannot be inferred from generated files or empty evidence."""
import json
import hashlib
import re
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_harness import REPO
from harness_core import compatibility


class CompatibilityTests(unittest.TestCase):
    def test_current_catalog_is_honest_and_blocks_release_only_for_its_state(self):
        data = compatibility.catalog(REPO)
        required = [row["id"] for row in data["clients"] if row.get("required_for_release")]
        # 0.14.0, like 0.13.x, requires the two Claude Code CLI targets. Codex CLI stays out: FR-12
        # admits it only once a scripted round agrees with a hand-driven one (#700), and no such
        # round has run. codex-cli-linux ships as a stated limitation for the same reason.
        self.assertEqual(required, ["claude-code-cli-macos", "claude-code-cli-linux"])
        # Released: exactly the required clients are qualified. Candidate: none is yet.
        qualified = [row["id"] for row in data["clients"] if row["status"] == "qualified"]
        self.assertEqual(qualified, required if data.get("release_state") == "released" else [])
        self.assertTrue(all(row["status"] in compatibility.STATES for row in data["clients"]))
        # The expectation follows the catalog's state so it survives a release without an edit.
        # Released: changed runtime source must block publication and nothing else, leaving the
        # released evidence intact. Candidate: exactly the required clients still unqualified.
        if data.get("release_state") == "released":
            drift = ["current runtime source differs from the released qualification source"]
            expected = drift if compatibility.source_drift(REPO, data) else []
        else:
            expected = [row["id"] + " is unqualified" for row in data["clients"]
                        if row.get("required_for_release") and row["status"] != "qualified"]
        self.assertEqual(compatibility.release_errors(REPO), expected)

    def test_qualified_claim_without_native_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(REPO / "compatibility", root / "compatibility")
            shutil.copy(REPO / "VERSION", root / "VERSION")
            path = root / "compatibility" / "catalog.json"
            data = json.loads(path.read_text())
            data["release_state"] = "candidate"
            data.pop("qualification_source_commit", None)
            data["clients"][0]["status"] = "qualified"
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "missing acceptance"):
                compatibility.catalog(root)

    def test_custom_stance_reports_advisory_coverage_for_both_adapters(self):
        result = compatibility.coverage(REPO, {"feedback": "direct"})
        for runtime in ("codex", "claude-code"):
            self.assertEqual(result[runtime]["feedback"]["mode"], "instruction")

    def test_released_catalog_stays_readable_while_changed_source_blocks_release(self):
        data = compatibility.catalog(REPO)
        data = dict(data, release_state="released", qualification_source_commit="a" * 40)
        with patch.object(compatibility, "catalog", return_value=data), \
                patch.object(compatibility, "source_drift", return_value=True):
            self.assertIn("current runtime source differs", compatibility.release_errors(REPO)[-1])

    def test_released_catalog_fails_when_qualification_source_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(REPO / "compatibility", root / "compatibility")
            shutil.copy(REPO / "VERSION", root / "VERSION")
            path = root / "compatibility" / "catalog.json"
            data = json.loads(path.read_text())
            data.update(release_state="released", qualification_source_commit="a" * 40)
            path.write_text(json.dumps(data))
            with patch.object(compatibility.subprocess, "run",
                              return_value=subprocess.CompletedProcess([], 1)):
                with self.assertRaisesRegex(ValueError, "source commit is unavailable"):
                    compatibility.catalog(root)


class QualificationReuseTests(unittest.TestCase):
    """A patch may reuse evidence only when its public qualification claims stay identical."""

    def fixture(self, prior="1.2.3", current="1.2.4", kind="carry-forward"):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        subprocess.run(["git", "init", "--quiet", "-b", "main", str(root)], check=True)

        def git(*args):
            return subprocess.check_output(
                ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t", *args],
                text=True).strip()

        (root / "lib/harness_core").mkdir(parents=True)
        (root / "compatibility").mkdir()
        (root / "VERSION").write_text(prior + "\n")
        (root / "lib/harness_core/compatibility.py").write_text("# prior validator\n")
        prior_data = {
            "schema_version": 1,
            "harness_version": prior,
            "release_state": "released",
            "required_cases": ["installation"],
            "clients": [{"id": "fixture", "runtime": "fixture", "status": "qualified",
                         "runtime_version": "1", "client_version": "1", "platform": "fixture",
                         "evidence": []}],
            "limitations": ["Existing limitation."],
            "qualification_source_commit": "0" * 40,
        }
        (root / "compatibility/catalog.json").write_text(json.dumps(prior_data))
        git("add", ".")
        git("commit", "--quiet", "-m", "prior")
        source = git("rev-parse", "HEAD")
        prior_data["qualification_source_commit"] = source
        record = {"kind": "native", "client": "fixture", "harness_version": prior,
                  "runtime_version": "1", "client_version": "1", "platform": "fixture",
                  "source_commit": source, "observations": ["fixture"],
                  "cases": {"installation": "passed"}}
        evidence = root / "compatibility/evidence.json"
        evidence.write_text(json.dumps(record))
        prior_data["clients"][0]["evidence"] = [
            {"path": "compatibility/evidence.json",
             "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}
        ]
        (root / "compatibility/catalog.json").write_text(json.dumps(prior_data))
        git("add", "compatibility")
        git("commit", "--quiet", "-m", "pin source")
        git("tag", "v" + prior)

        includes_bootstrap = kind == "v0.14.1-bootstrap"
        limitation = compatibility.canonical_reuse_limitation(
            kind, current, "v" + prior, prior, includes_bootstrap)
        current_data = json.loads(json.dumps(prior_data))
        current_data.update(
            harness_version=current,
            qualification_reuse={
                "schema_version": 1,
                "kind": kind,
                "prior_version": prior,
                "prior_tag": "v" + prior,
                "evidence_version": prior,
                "qualification_source_commit": source,
                "includes_bootstrap_exception": includes_bootstrap,
                "limitation": limitation,
            },
            limitations=prior_data["limitations"] + [limitation],
        )
        (root / "VERSION").write_text(current + "\n")
        if kind == "v0.14.1-bootstrap":
            for name in compatibility.BOOTSTRAP_BEHAVIOR_PATHS:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("# reviewed bootstrap behavior\n")
        (root / "compatibility/catalog.json").write_text(json.dumps(current_data))
        git("add", ".")
        git("commit", "--quiet", "-m", "candidate")
        return root, current_data, git

    def test_a_patch_with_only_a_version_delta_can_carry_evidence_forward(self):
        root, data, _ = self.fixture()
        reuse = compatibility.qualification_reuse(root, data)
        self.assertEqual(reuse["prior_version"], "1.2.3")
        self.assertEqual(compatibility.evidence_version(data), "1.2.3")

    def test_valid_and_invalid_reuse_flow_through_real_catalog_consumers(self):
        root, data, _ = self.fixture()
        self.assertEqual(compatibility.catalog(root)["qualification_reuse"],
                         data["qualification_reuse"])
        path = root / "compatibility/catalog.json"
        data["qualification_reuse"]["misspelled"] = True
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "unknown field: misspelled"):
            compatibility.catalog(root)

    def test_reused_evidence_accepts_only_the_version_that_was_observed(self):
        root, data, _ = self.fixture()
        client = data["clients"][0]
        self.assertEqual(compatibility.evidence_errors(root, data, client), [])
        path = root / client["evidence"][0]["path"]
        record = json.loads(path.read_text())
        record["harness_version"] = data["harness_version"]
        path.write_text(json.dumps(record))
        client["evidence"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertIn("evidence version or client mismatch",
                      compatibility.evidence_errors(root, data, client))

    def test_valid_reuse_suppresses_release_drift_until_runtime_changes_after_the_tag(self):
        root, data, git = self.fixture()
        self.assertFalse(compatibility.source_drift(root, data))
        self.assertEqual(compatibility.release_errors(root), [])
        git("tag", "v1.2.4")
        (root / "lib/later.py").write_text("changed after release\n")
        git("add", "lib/later.py")
        git("commit", "--quiet", "-m", "later runtime change")
        self.assertTrue(compatibility.source_drift(root, data))
        self.assertIn("current runtime source differs from the released qualification source",
                      compatibility.release_errors(root))

    def test_chained_reuse_retains_and_validates_the_version_the_evidence_observed(self):
        root, prior, git = self.fixture()
        git("tag", "v1.2.4")
        limitation = compatibility.canonical_reuse_limitation(
            "carry-forward", "1.2.5", "v1.2.4", "1.2.3", False)
        current = json.loads(json.dumps(prior))
        current.update(
            harness_version="1.2.5",
            qualification_reuse={
                "schema_version": 1,
                "kind": "carry-forward",
                "prior_version": "1.2.4",
                "prior_tag": "v1.2.4",
                "evidence_version": "1.2.3",
                "qualification_source_commit": prior["qualification_source_commit"],
                "includes_bootstrap_exception": False,
                "limitation": limitation,
            },
            limitations=prior["limitations"] + [limitation],
        )
        (root / "VERSION").write_text("1.2.5\n")
        (root / "compatibility/catalog.json").write_text(json.dumps(current))
        git("add", ".")
        git("commit", "--quiet", "-m", "second carry")
        self.assertEqual(compatibility.qualification_reuse(root, current)["evidence_version"],
                         "1.2.3")
        self.assertEqual(compatibility.evidence_version(current), "1.2.3")
        self.assertIn("Native evidence for v1.2.3 was carried forward through v1.2.4",
                      compatibility.qualification_disclosure(current))

        current["qualification_reuse"]["evidence_version"] = "1.2.4"
        current["qualification_reuse"]["limitation"] = compatibility.canonical_reuse_limitation(
            "carry-forward", "1.2.5", "v1.2.4", "1.2.4", False)
        with self.assertRaisesRegex(ValueError, "evidence version differs"):
            compatibility.qualification_reuse(root, current)

    def test_chained_disclosure_retains_the_bootstrap_exception(self):
        root, prior, git = self.fixture("0.14.0", "0.14.1", "v0.14.1-bootstrap")
        git("tag", "v0.14.1")
        limitation = compatibility.canonical_reuse_limitation(
            "carry-forward", "0.14.2", "v0.14.1", "0.14.0", True)
        current = json.loads(json.dumps(prior))
        current.update(
            harness_version="0.14.2",
            qualification_reuse={
                "schema_version": 1,
                "kind": "carry-forward",
                "prior_version": "0.14.1",
                "prior_tag": "v0.14.1",
                "evidence_version": "0.14.0",
                "qualification_source_commit": prior["qualification_source_commit"],
                "includes_bootstrap_exception": True,
                "limitation": limitation,
            },
            limitations=prior["limitations"] + [limitation],
        )
        (root / "VERSION").write_text("0.14.2\n")
        (root / "compatibility/catalog.json").write_text(json.dumps(current))
        git("add", ".")
        git("commit", "--quiet", "-m", "carry bootstrap evidence")
        compatibility.qualification_reuse(root, current)
        self.assertIn("including the one-release v0.14.1 bootstrap exception",
                      compatibility.qualification_disclosure(current))

    def test_the_one_release_bootstrap_accepts_only_its_named_implementation_delta(self):
        root, data, _ = self.fixture("0.14.0", "0.14.1", "v0.14.1-bootstrap")
        self.assertEqual(compatibility.qualification_reuse(root, data)["kind"],
                         "v0.14.1-bootstrap")

        root, data, git = self.fixture("0.14.0", "0.14.1", "v0.14.1-bootstrap")
        (root / "lib/harness_core/other.py").write_text("changed\n")
        git("add", "lib/harness_core/other.py")
        git("commit", "--quiet", "-m", "extra runtime change")
        with self.assertRaisesRegex(ValueError, "ineligible path"):
            compatibility.qualification_reuse(root, data)

    def test_non_patch_missing_tag_and_unavailable_ancestry_fail_closed(self):
        root, data, git = self.fixture(current="1.3.0")
        with self.assertRaisesRegex(ValueError, "later patch"):
            compatibility.qualification_reuse(root, data)

        root, data, git = self.fixture()
        git("tag", "-d", data["qualification_reuse"]["prior_tag"])
        git("branch", data["qualification_reuse"]["prior_tag"])
        with self.assertRaisesRegex(ValueError, "release tag is unavailable"):
            compatibility.qualification_reuse(root, data)

        root, data, _ = self.fixture()
        original_run = compatibility.subprocess.run

        def unavailable_ancestry(command, **kwargs):
            if "merge-base" in command:
                return subprocess.CompletedProcess(command, 1)
            return original_run(command, **kwargs)

        with patch.object(compatibility.subprocess, "run", side_effect=unavailable_ancestry):
            with self.assertRaisesRegex(ValueError, "prior release tag is not an ancestor"):
                compatibility.qualification_reuse(root, data)

    def test_major_downgrade_and_malformed_versions_are_refused(self):
        for current, message in (("2.0.0", "later patch"), ("1.2.2", "later patch"),
                                 ("01.2.4", "stable semantic version"),
                                 ("not-a-version", "stable semantic version")):
            with self.subTest(current=current):
                root, data, _ = self.fixture(current=current)
                with self.assertRaisesRegex(ValueError, message):
                    compatibility.qualification_reuse(root, data)
        root, data, _ = self.fixture()
        data["qualification_reuse"]["prior_version"] = "bad"
        with self.assertRaisesRegex(ValueError, "prior_version must be a stable semantic version"):
            compatibility.qualification_reuse(root, data)

    def test_malformed_schema_kind_and_tag_are_refused(self):
        for key, value, message in (
            ("schema_version", 2, "schema_version 1"),
            ("schema_version", True, "schema_version 1"),
            ("kind", "waiver", "kind must be one of"),
            ("prior_tag", "release-1.2.3", "prior_tag must match prior_version"),
            ("evidence_version", "bad", "evidence_version must be a stable semantic version"),
        ):
            with self.subTest(key=key):
                root, data, _ = self.fixture()
                data["qualification_reuse"][key] = value
                with self.assertRaisesRegex(ValueError, message):
                    compatibility.qualification_reuse(root, data)

        root, data, _ = self.fixture()
        data["qualification_reuse"]["extra"] = "claim"
        with self.assertRaisesRegex(ValueError, "unknown field: extra"):
            compatibility.qualification_reuse(root, data)

    def test_semver_numeric_identifiers_may_not_have_leading_zeroes(self):
        for key in ("prior_version", "evidence_version"):
            with self.subTest(key=key):
                root, data, _ = self.fixture()
                data["qualification_reuse"][key] = "01.2.3"
                with self.assertRaisesRegex(ValueError, key + " must be a stable semantic version"):
                    compatibility.qualification_reuse(root, data)

    def test_limitation_is_canonical_for_its_kind_and_lists_are_well_formed(self):
        root, data, _ = self.fixture()
        data["qualification_reuse"]["limitation"] = "No rerun."
        with self.assertRaisesRegex(ValueError, "canonical carry-forward disclosure"):
            compatibility.qualification_reuse(root, data)
        for limitations in ("not-a-list", [""], [1]):
            with self.subTest(limitations=limitations):
                root, data, _ = self.fixture()
                data["limitations"] = limitations
                with self.assertRaisesRegex(ValueError, "lists of nonempty strings"):
                    compatibility.qualification_reuse(root, data)

    def test_prior_catalog_must_be_released_and_match_version_and_source(self):
        for key, value, message in (
            ("release_state", "candidate", "not the named released version"),
            ("harness_version", "1.2.2", "not the named released version"),
            ("qualification_source_commit", "f" * 40,
             "qualification source differs from the prior release"),
        ):
            with self.subTest(key=key):
                root, data, _ = self.fixture()
                original = compatibility.git_output
                prior = json.loads(original(root, "show", "v1.2.3:compatibility/catalog.json"))
                prior[key] = value

                def changed_output(repo, *args):
                    if args[0] == "show" and args[1].endswith(":compatibility/catalog.json"):
                        return json.dumps(prior)
                    return original(repo, *args)

                with patch.object(compatibility, "git_output", side_effect=changed_output), \
                        self.assertRaisesRegex(ValueError, message):
                    compatibility.qualification_reuse(root, data)

    def test_prior_tag_version_is_read_from_the_once_resolved_commit(self):
        root, data, _ = self.fixture()
        original = compatibility.git_output
        seen = []

        def changed_output(repo, *args):
            seen.append(args)
            if args[0] == "show" and args[1].endswith(":VERSION"):
                return "1.2.2\n"
            return original(repo, *args)

        with patch.object(compatibility, "git_output", side_effect=changed_output), \
                self.assertRaisesRegex(ValueError, "tag VERSION does not match"):
            compatibility.qualification_reuse(root, data)
        shown = [args[1].split(":", 1)[0] for args in seen if args[0] == "show"]
        self.assertTrue(shown)
        self.assertEqual(len(set(shown)), 1)
        self.assertRegex(shown[0], r"^[0-9a-f]{40,64}$")

    def test_carried_claim_equality_is_type_sensitive(self):
        root, data, _ = self.fixture()
        original = compatibility.git_output
        prior = json.loads(original(root, "show", "v1.2.3:compatibility/catalog.json"))
        prior["required_cases"] = [1]
        data["required_cases"] = [True]

        def changed_output(repo, *args):
            if args[0] == "show" and args[1].endswith(":compatibility/catalog.json"):
                return json.dumps(prior)
            return original(repo, *args)

        with patch.object(compatibility, "git_output", side_effect=changed_output), \
                self.assertRaisesRegex(ValueError, "required cases differ"):
            compatibility.qualification_reuse(root, data)

    def test_prior_catalog_must_be_a_json_object(self):
        root, data, _ = self.fixture()
        original = compatibility.git_output

        def list_catalog(repo, *args):
            if args[0] == "show" and args[1].endswith(":compatibility/catalog.json"):
                return "[]"
            return original(repo, *args)

        with patch.object(compatibility, "git_output", side_effect=list_catalog), \
                self.assertRaisesRegex(ValueError, "prior catalog must be an object"):
            compatibility.qualification_reuse(root, data)

    def test_reuse_chain_depth_is_bounded_before_recursion(self):
        root, data, _ = self.fixture()
        with self.assertRaisesRegex(ValueError, "chain exceeds"):
            compatibility.qualification_reuse(root, data, depth=compatibility.MAX_REUSE_DEPTH)

    def test_bootstrap_exception_is_refused_for_every_other_version(self):
        root, data, _ = self.fixture("0.14.1", "0.14.2", "v0.14.1-bootstrap")
        with self.assertRaisesRegex(ValueError, "only to v0.14.1"):
            compatibility.qualification_reuse(root, data)

    def test_every_runtime_source_category_but_version_blocks_general_reuse(self):
        for path in ("bin/tool", "lib/other.py", "adapters/x/file", "primitives/x/file",
                     "policy/x/file", "templates/x/file", "config.example.json"):
            with self.subTest(path=path):
                root, data, git = self.fixture()
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("changed\n")
                git("add", path)
                git("commit", "--quiet", "-m", "runtime change")
                with self.assertRaisesRegex(ValueError, "ineligible path"):
                    compatibility.qualification_reuse(root, data)

    def test_installer_workflow_and_other_executable_changes_are_ineligible(self):
        for path in ("install.sh", ".github/workflows/release.yml", "scripts/unrelated.py"):
            with self.subTest(path=path):
                root, data, git = self.fixture()
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("changed\n")
                git("add", path)
                git("commit", "--quiet", "-m", "ineligible executable")
                with self.assertRaisesRegex(ValueError, "ineligible path: " + re.escape(path)):
                    compatibility.qualification_reuse(root, data)

    def test_changed_carried_claims_evidence_and_limitations_are_refused(self):
        for label, mutate, message in (
            ("client", lambda data: data["clients"][0].update(status="unqualified"),
             "client and evidence claims differ"),
            ("digest", lambda data: data["clients"][0]["evidence"][0].update(sha256="2" * 64),
             "client and evidence claims differ"),
            ("path", lambda data: data["clients"][0]["evidence"][0].update(path="different"),
             "client and evidence claims differ"),
            ("case", lambda data: data.update(required_cases=["different"]),
             "required cases differ"),
            ("limitation", lambda data: data.update(limitations=["rewritten"]),
             "limitations differ"),
        ):
            with self.subTest(label=label):
                root, data, _ = self.fixture()
                mutate(data)
                with self.assertRaisesRegex(ValueError, message):
                    compatibility.qualification_reuse(root, data)

    def test_unknown_and_changed_top_level_catalog_fields_are_refused(self):
        root, data, _ = self.fixture()
        data["future_claim"] = True
        with self.assertRaisesRegex(ValueError, "unknown field: future_claim"):
            compatibility.qualification_reuse(root, data)

        root, data, _ = self.fixture()
        data["schema_version"] = 2
        with self.assertRaisesRegex(ValueError, "catalog field differs.*schema_version"):
            compatibility.qualification_reuse(root, data)

    def test_missing_or_mismatched_reuse_metadata_is_refused(self):
        for key in ("prior_version", "prior_tag", "evidence_version",
                    "qualification_source_commit", "includes_bootstrap_exception", "limitation"):
            with self.subTest(key=key):
                root, data, _ = self.fixture()
                data["qualification_reuse"].pop(key)
                with self.assertRaisesRegex(ValueError, "requires " + key):
                    compatibility.qualification_reuse(root, data)
        root, data, _ = self.fixture()
        data["qualification_reuse"]["qualification_source_commit"] = "f" * 40
        with self.assertRaisesRegex(ValueError, "qualification source differs"):
            compatibility.qualification_reuse(root, data)

    def test_an_ordinary_release_keeps_the_existing_evidence_behavior(self):
        data = {"harness_version": "1.2.3"}
        self.assertIsNone(compatibility.qualification_reuse(Path("."), data))
        self.assertEqual(compatibility.evidence_version(data), "1.2.3")
        self.assertEqual(compatibility.qualification_disclosure(data),
                         "native evidence recorded for this release")


class QualificationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = {"harness_version": "0.9.0", "required_cases": ["read", "write-denial"]}
        self.client = {"id": "fixture-cli", "runtime_version": "1.2", "client_version": "3.4",
                       "platform": "fixture-os", "evidence": []}
        self.record = {"kind": "native", "client": self.client["id"], "harness_version": "0.9.0",
                       "runtime_version": "1.2", "client_version": "3.4", "platform": "fixture-os",
                       "source_commit": "a" * 40, "observations": ["Synthetic validation fixture"],
                       "cases": {"read": "passed", "write-denial": "passed"}}
        self.git = patch.object(compatibility.subprocess, "run", return_value=subprocess.CompletedProcess([], 0))
        self.git.start()
        self.addCleanup(self.git.stop)

    def add_record(self, record):
        path = self.root / ("evidence-" + str(len(self.client["evidence"])) + ".json")
        path.write_text(json.dumps(record))
        self.client["evidence"].append({"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})

    def errors(self):
        return compatibility.evidence_errors(self.root, self.data, self.client)

    def test_matching_complete_records_can_qualify(self):
        self.add_record(self.record)
        self.assertEqual(self.errors(), [])

    def test_released_evidence_is_checked_against_its_pinned_source(self):
        target = "b" * 40
        self.data.update(release_state="released", qualification_source_commit=target)
        self.add_record(self.record)
        with patch.object(compatibility.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(self.errors(), [])
        calls = [call.args[0] for call in run.mock_calls]
        self.assertTrue(any(command[:5] == ["git", "-C", str(self.root), "merge-base", "--is-ancestor"]
                            and command[-1] == target for command in calls))

    def test_released_catalog_requires_a_full_source_commit(self):
        for value in (None, "short", "z" * 40):
            with self.subTest(value=value):
                data = dict(self.data, release_state="released", qualification_source_commit=value)
                with self.assertRaisesRegex(ValueError, "full qualification source commit"):
                    compatibility.qualification_source(data)

    def test_partial_passing_records_can_cover_distinct_cases(self):
        for case in self.data["required_cases"]:
            self.add_record(dict(self.record, cases={case: "passed"}))
        self.assertEqual(self.errors(), [])

    def test_failed_and_unverified_records_cannot_be_hidden_by_a_pass(self):
        self.add_record(self.record)
        for result in ("failed", "unverified"):
            with self.subTest(result=result):
                self.client["evidence"] = self.client["evidence"][:1]
                self.add_record(dict(self.record, cases={"read": result}))
                self.assertIn("read is " + result + " in linked evidence", self.errors())

    def test_native_versions_and_platform_must_match(self):
        for key in ("runtime_version", "client_version", "platform"):
            for value in (None, "different"):
                with self.subTest(key=key, value=value):
                    self.client["evidence"] = []
                    self.add_record(dict(self.record, **{key: value}))
                    self.assertTrue(any("mismatch" in error for error in self.errors()))

    def test_unknown_cases_results_and_invalid_records_fail_closed(self):
        for record in ([], dict(self.record, cases=[]), dict(self.record, cases={}),
                       dict(self.record, cases={"unknown": "passed"}),
                       dict(self.record, cases={"read": "skip"})):
            with self.subTest(record=record):
                self.client["evidence"] = []
                self.add_record(record)
                self.assertTrue(self.errors())

    def test_changed_evidence_cannot_reuse_a_digest(self):
        self.add_record(self.record)
        (self.root / self.client["evidence"][0]["path"]).write_text("{}")
        self.assertIn("evidence digest mismatch", self.errors())

    def test_evidence_replaced_after_read_cannot_change_verified_record(self):
        failed = dict(self.record, cases={"read": "failed", "write-denial": "passed"})
        self.add_record(failed)
        original_read = Path.read_bytes

        def replace_after_read(path):
            content = original_read(path)
            path.write_text(json.dumps(self.record))
            return content

        with patch.object(Path, "read_bytes", replace_after_read):
            self.assertIn("read is failed in linked evidence", self.errors())

    def test_unreadable_evidence_fails_closed(self):
        self.add_record(self.record)
        with patch.object(Path, "read_bytes", side_effect=OSError("fixture read failure")):
            self.assertIn("unreadable evidence record", self.errors())

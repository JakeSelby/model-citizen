"""Release publication depends on qualification and immutable source identity."""
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_harness import REPO


def load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class ReleaseTests(unittest.TestCase):
    def test_native_gaps_block_preflight_even_with_a_clean_tree(self):
        module = load("release_preflight")
        with patch.object(module, "git", return_value=""), patch.object(module.compatibility, "release_errors", return_value=["fixture is unqualified"]):
            errors = module.check(REPO)
        self.assertTrue(any("unqualified" in error for error in errors))

    def test_preflight_takes_no_downstream_site_checkout(self):
        result = subprocess.run([sys.executable, str(REPO / "scripts" / "release_preflight.py"),
                                 "--reference-repo", str(REPO)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, msg=result.stderr)
        self.assertIn("unrecognized arguments: --reference-repo", result.stderr)

    def test_release_notes_use_current_product_and_support_data(self):
        text = load("release_notes").notes()
        self.assertIn(json.loads((REPO / "product.json").read_text())["headline"], text)
        data = json.loads((REPO / "compatibility" / "catalog.json").read_text())
        for client in data["clients"]:
            self.assertIn(client["id"] + ": " + client["status"], text)
        version = (REPO / "VERSION").read_text().strip()
        self.assertIn("blob/v" + version + "/docs/compatibility-policy.md", text)
        self.assertIn("## Migration", text)
        # From 0.14.0 the migration names the command `citizen`; `harness` stays its alias.
        self.assertRegex(text, r"`(citizen|harness) sync --dry-run`")
        self.assertIn("architecture-viewer preview is inert", text)
        self.assertIn("### Recovery", text)

    def test_release_notes_reject_stale_or_incomplete_migration_metadata(self):
        module = load("release_notes")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("VERSION", "product.json"):
                (root / name).write_bytes((REPO / name).read_bytes())
            (root / "compatibility").mkdir()
            path = root / "compatibility/migration.json"
            for value in ({"schema_version": 1, "harness_version": "different", "summary": "x",
                           "actions": ["x"], "recovery": ["x"]},
                          {"schema_version": 1, "harness_version": (REPO / "VERSION").read_text().strip(),
                           "summary": "x", "actions": [], "recovery": ["x"]}):
                path.write_text(json.dumps(value))
                with self.subTest(value=value), patch.object(module.compatibility, "catalog", return_value={"clients": []}), self.assertRaises(ValueError):
                    module.notes(root)


PRODUCT = json.loads((REPO / "product.json").read_text())
README = (REPO / "README.md").read_text()


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            for found in strings(item):
                yield found
    elif isinstance(value, list):
        for item in value:
            for found in strings(item):
                yield found


class ProductCopyTests(unittest.TestCase):
    """The landing copy is data, so the page, the README and GitHub About cannot drift apart."""

    def test_the_hero_leads_the_headline_and_the_github_description(self):
        hero = PRODUCT["hero"]
        for key in ("title", "subtitle", "proof"):
            self.assertTrue(hero[key].strip(), msg=key)
        self.assertEqual(PRODUCT["headline"], hero["title"])
        self.assertTrue(PRODUCT["github_description"].startswith(hero["title"]),
                        msg=PRODUCT["github_description"])

    def test_every_documented_feature_points_at_a_path_that_exists(self):
        for group in PRODUCT["capabilities"]:
            for feature in group["features"]:
                with self.subTest(feature=feature["name"]):
                    self.assertTrue((REPO / feature["doc"]).exists(), msg=feature["doc"])

    def test_the_grid_stays_a_page_rather_than_a_catalog(self):
        self.assertTrue(4 <= len(PRODUCT["capabilities"]) <= 7, msg=len(PRODUCT["capabilities"]))
        ids = [group["id"] for group in PRODUCT["capabilities"]]
        self.assertEqual(len(ids), len(set(ids)))
        for group in PRODUCT["capabilities"]:
            with self.subTest(group=group["id"]):
                self.assertTrue(group["title"].strip() and group["pitch"].strip())
                self.assertTrue(3 <= len(group["features"]) <= 6, msg=len(group["features"]))

    def test_every_feature_line_stays_short_enough_to_read_in_a_card(self):
        for group in PRODUCT["capabilities"]:
            for feature in group["features"]:
                with self.subTest(feature=feature["name"]):
                    self.assertLessEqual(len(feature["line"]), 170, msg=feature["line"])

    def test_no_published_string_carries_an_em_dash(self):
        for value in strings(PRODUCT):
            self.assertNotIn("—", value, msg=value)

    def test_planned_work_names_an_issue_a_planned_client_or_a_document(self):
        planned = {client["id"] for client in
                   json.loads((REPO / "compatibility" / "catalog.json").read_text())["clients"]
                   if client["status"] == "planned"}
        entries = PRODUCT["on_the_way"]
        self.assertLessEqual(len(entries), 5)
        for entry in entries:
            with self.subTest(entry=entry["title"]):
                self.assertTrue(entry["line"].strip())
                if "issue" in entry:
                    self.assertIsInstance(entry["issue"], int)
                elif "catalog" in entry:
                    self.assertIn(entry["catalog"], planned)
                else:
                    self.assertTrue((REPO / entry["doc"]).exists(), msg=entry["doc"])

    def test_the_readme_carries_the_same_groups_and_features(self):
        for group in PRODUCT["capabilities"]:
            self.assertIn(group["title"], README, msg=group["title"])
            for feature in group["features"]:
                self.assertIn(feature["name"], README, msg=feature["name"])

    def test_the_first_screen_sells_before_it_reports_qualification_status(self):
        install = README.index("git clone --branch stable")
        self.assertLess(README.index("## What it does for you"), install)
        self.assertLess(install, README.index("**Release status:**"))
        self.assertLess(install, README.index("<!-- harness:compatibility:start -->"))

    def test_the_demo_visual_is_committed_and_described(self):
        match = re.search(r"!\[([^\]]*)\]\((docs/assets/[^)]+\.svg)\)", README)
        self.assertIsNotNone(match)
        self.assertGreater(len(match.group(1).strip()), 40, msg="alt text")
        self.assertLess(README.index(match.group(0)), README.index("## What it does for you"))
        visual = (REPO / match.group(2)).read_text()
        self.assertIn("<title", visual)
        self.assertNotIn("/Users/", visual)


class StableBranchTests(unittest.TestCase):
    def setUp(self):
        self.module = load("advance_stable")
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.remote, self.work = Path(temp.name) / "remote.git", Path(temp.name) / "work"
        subprocess.run(["git", "init", "--quiet", "--bare", str(self.remote)], check=True)
        subprocess.run(["git", "init", "--quiet", "-b", "main", str(self.work)], check=True)
        self.git("remote", "add", "origin", str(self.remote))
        (self.work / "VERSION").write_text("1.0.0\n")
        self.first, self.second = self.release("v1.0.0"), self.release("v1.0.1")
        patcher = patch.object(self.module, "ROOT", self.work); patcher.start(); self.addCleanup(patcher.stop)

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.work), "-c", "user.name=t", "-c", "user.email=t",
                                        *args], text=True, stderr=subprocess.DEVNULL).strip()

    def release(self, tag):
        self.git("commit", "--quiet", "--allow-empty", "-m", tag)
        self.git("tag", "-a", tag, "-m", tag)
        self.git("push", "--quiet", "origin", "main", tag)
        return self.git("rev-parse", "HEAD")

    def stable(self):
        return subprocess.check_output(["git", "--git-dir", str(self.remote), "rev-parse", "refs/heads/stable"],
                                       text=True).strip()

    def test_first_release_creates_stable_and_the_next_fast_forwards_it(self):
        self.assertEqual(self.module.main(["v1.0.0"]), 0)
        self.assertEqual(self.stable(), self.first)
        self.assertEqual(self.module.main(["v1.0.1"]), 0)
        self.assertEqual(self.stable(), self.second)
        self.assertEqual(self.module.plan(self.work, "v1.0.1"), ("current", self.second))

    def test_stable_never_moves_backward_to_an_older_tag(self):
        self.module.main(["v1.0.1"])
        self.assertEqual(self.module.main(["v1.0.0"]), 1)
        self.assertEqual(self.stable(), self.second)

    def test_a_checkout_that_lacks_the_stable_tip_still_refuses_cleanly(self):
        self.module.main(["v1.0.1"])
        old = self.work.parent / "old"
        subprocess.run(["git", "clone", "--quiet", "--single-branch", "--branch", "v1.0.0", self.remote.as_uri(),
                        str(old)], check=True, stderr=subprocess.DEVNULL)
        with patch.object(self.module, "ROOT", old):
            self.assertEqual(self.module.plan(old, "v1.0.0"), ("refuse", self.first))

    def test_a_rejected_push_is_reported_not_raised(self):
        (self.remote / "hooks" / "pre-receive").write_text("#!/bin/sh\nexit 1\n")
        (self.remote / "hooks" / "pre-receive").chmod(0o755)
        self.assertEqual(self.module.main(["v1.0.0"]), 1)
        self.assertEqual(self.module.remote_head(self.work, "origin"), None)

    def test_check_reports_a_stale_branch_without_pushing(self):
        self.module.main(["v1.0.0"])
        self.assertEqual(self.module.main(["v1.0.1", "--check"]), 1)
        self.assertEqual(self.stable(), self.first)
        self.assertEqual(self.module.main(["v1.0.0", "--check"]), 0)

    def test_the_tag_defaults_to_the_checkout_version(self):
        self.assertEqual(self.module.main([]), 0)
        self.assertEqual(self.stable(), self.first)

    def test_release_workflow_advances_stable_only_after_publishing(self):
        text = (REPO / ".github/workflows/release.yml").read_text()
        self.assertIn("\npermissions:\n  contents: write\n", text)
        job = text[text.index("\n  advance-stable:\n"):].splitlines()
        self.assertIn("    needs: qualified-release", job)
        self.assertIn("    continue-on-error: true", job)
        self.assertIn('        run: python3 scripts/advance_stable.py "$GITHUB_REF_NAME"', job)

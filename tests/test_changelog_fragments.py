"""Changelog fragments: the release-time assembler and the lint rule that asks a branch for one."""

import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from harness_core import changelog  # noqa: E402

spec = importlib.util.spec_from_file_location("release_notes_fragments", ROOT / "scripts" / "release_notes.py")
release_notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_notes)

HEAD = """# Changelog

Preamble.

## [Unreleased]

## [0.13.0] — 2026-10-01

### Fixed

- An older fix. (#1)
"""


def write(root, name, text):
    path = Path(root) / changelog.DIRECTORY / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class AssemblerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_sections_follow_keep_a_changelog_order_and_numbers_ascend_within_each(self):
        write(self.root, "40.fixed.md", "Fix forty.")
        write(self.root, "9.fixed.md", "Fix nine.")
        write(self.root, "12.added.md", "Add twelve.")
        write(self.root, "3.removed.md", "Remove three.")
        write(self.root, "7.changed.md", "Change seven.")
        write(self.root, "5.none.md", "A test-only change with nothing to announce.")
        body = changelog.render(changelog.fragments(self.root))
        headings = [line for line in body.splitlines() if line.startswith("### ")]
        self.assertEqual(headings, ["### Added", "### Changed", "### Removed", "### Fixed"])
        self.assertLess(body.index("Fix nine."), body.index("Fix forty."))
        self.assertNotIn("test-only", body, "a waiver is never rendered")

    def test_the_same_fragments_render_identically_whatever_order_they_were_written_in(self):
        for name in ("2.added.md", "1.added.md"):
            write(self.root, name, "Entry %s." % name[0])
        first = changelog.render(changelog.fragments(self.root))
        other = Path(self.tmp.name) / "other"
        for name in ("1.added.md", "2.added.md"):
            write(other, name, "Entry %s." % name[0])
        self.assertEqual(first, changelog.render(changelog.fragments(other)))

    def test_an_entry_gains_its_reference_once_and_wrapped_lines_stay_in_the_bullet(self):
        write(self.root, "8.added.md", "First line\nsecond line.\n")
        write(self.root, "9.added.md", "Already referenced (#9), mid-sentence.")
        body = changelog.render(changelog.fragments(self.root))
        self.assertIn("- First line\n  second line. (#8)", body)
        self.assertIn("- Already referenced (#9), mid-sentence.\n", body + "\n")
        self.assertEqual(body.count("(#9)"), 1)

    def test_an_empty_set_renders_nothing_and_assembling_it_is_refused(self):
        self.assertEqual(changelog.fragments(self.root), [])
        self.assertEqual(changelog.render([]), "")
        with self.assertRaisesRegex(ValueError, "no changelog fragments"):
            changelog.assemble(HEAD, "0.14.0", "2026-11-01", [])

    def test_a_malformed_name_is_refused_with_the_expected_shape(self):
        for name in ("fix-thing.md", "12.feature.md", "12.added.txt", "0.added.md", "12.Added.md"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, r"<issue-or-pr>\.<added\|changed"):
                    changelog.parse_name(name)
        write(self.root, "oops.md", "Text.")
        with self.assertRaises(ValueError):
            changelog.fragments(self.root)

    def test_an_empty_fragment_is_refused(self):
        write(self.root, "4.fixed.md", "  \n")
        with self.assertRaisesRegex(ValueError, "empty"):
            changelog.fragments(self.root)

    def test_the_readme_dotfiles_and_editor_leftovers_are_not_fragments(self):
        for name in ("README.md", ".DS_Store", ".12.added.md.swp", "12.added.md~", "notes.txt"):
            write(self.root, name, "Not an entry.")
        self.assertEqual(changelog.fragments(self.root), [])

    def test_assembly_inserts_the_version_under_an_empty_unreleased_heading(self):
        text = changelog.assemble(HEAD, "0.14.0", "2026-11-01", [(5, "fixed", "A fix.")])
        self.assertIn("## [Unreleased]\n\n## [0.14.0] — 2026-11-01\n\n### Fixed\n\n- A fix. (#5)\n\n"
                      "## [0.13.0] — 2026-10-01", text)
        self.assertTrue(text.startswith("# Changelog\n\nPreamble.\n"))

    def test_hand_written_unreleased_entries_are_refused_rather_than_folded_silently(self):
        dirty = HEAD.replace("## [Unreleased]\n", "## [Unreleased]\n\n### Added\n\n- By hand.\n")
        with self.assertRaisesRegex(ValueError, "hand-written"):
            changelog.assemble(dirty, "0.14.0", "2026-11-01", [(5, "fixed", "A fix.")])

    def test_a_version_that_already_has_a_section_is_refused(self):
        with self.assertRaisesRegex(ValueError, "already has a section"):
            changelog.assemble(HEAD, "0.13.0", "2026-11-01", [(5, "fixed", "A fix.")])

    def test_the_release_script_writes_the_changelog_and_consumes_every_fragment(self):
        (self.root / "CHANGELOG.md").write_text(HEAD, encoding="utf-8")
        write(self.root, "README.md", "Kept.")
        write(self.root, "5.fixed.md", "A fix.")
        write(self.root, "6.none.md", "A waiver long enough to count.")
        write(self.root, ".keep", "")
        release_notes.write_changelog("0.14.0", "2026-11-01", root=self.root)
        self.assertIn("- A fix. (#5)", (self.root / "CHANGELOG.md").read_text(encoding="utf-8"))
        self.assertEqual(sorted(p.name for p in (self.root / changelog.DIRECTORY).iterdir()),
                         [".keep", "README.md"])

    def test_a_dry_run_changes_nothing(self):
        (self.root / "CHANGELOG.md").write_text(HEAD, encoding="utf-8")
        write(self.root, "5.fixed.md", "A fix.")
        text = release_notes.write_changelog("0.14.0", "2026-11-01", root=self.root, dry_run=True)
        self.assertIn("## [0.14.0]", text)
        self.assertEqual((self.root / "CHANGELOG.md").read_text(encoding="utf-8"), HEAD)
        self.assertTrue((self.root / changelog.DIRECTORY / "5.fixed.md").is_file())


def git(root, *argv):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t",
                    "-c", "commit.gpgsign=false"] + list(argv), check=True, capture_output=True)


class LintRuleTests(unittest.TestCase):
    """A throwaway repository whose `origin/main` is a local ref, so no network is involved."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        git(self.root, "init", "-q")
        (self.root / "CHANGELOG.md").write_text(HEAD)
        (self.root / "docs").mkdir()
        (self.root / "docs" / "a.md").write_text("a\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "base")
        git(self.root, "update-ref", "refs/remotes/origin/main", "HEAD")

    def commit(self, relative, text="changed\n"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "change")

    def test_a_branch_touching_a_governed_root_without_a_fragment_is_a_finding(self):
        self.commit("docs/a.md")
        hits = changelog.findings(self.root)
        self.assertEqual(len(hits), 1)
        self.assertIn("docs/a.md", hits[0])
        self.assertIn("changelog.d/<issue-or-pr>.none.md", hits[0])

    def test_a_committed_or_uncommitted_fragment_satisfies_it(self):
        self.commit("docs/a.md")
        write(self.root, "12.fixed.md", "A fix.")
        self.assertEqual(changelog.findings(self.root), [])
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "fragment")
        self.assertEqual(changelog.findings(self.root), [])

    def test_a_waiver_needs_a_reason_as_long_as_the_landing_copy_one(self):
        self.commit("docs/a.md")
        for thin in ("tests only", "12345678901234567890 -- ####", "a-b-c-d-e-f-g-h-i-j-k"):
            with self.subTest(reason=thin):
                write(self.root, "12.none.md", thin)
                self.assertEqual(len(changelog.findings(self.root)), 1)
        write(self.root, "12.none.md", "Tests only, nothing visible.")
        self.assertEqual(changelog.findings(self.root), [])

    def test_editing_a_fragment_already_on_the_base_does_not_count_as_adding_one(self):
        self.commit("changelog.d/5.fixed.md", "An earlier fix.\n")
        git(self.root, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.commit("changelog.d/5.fixed.md", "An earlier fix, reworded.\n")
        self.commit("docs/a.md")
        self.assertEqual(len(changelog.findings(self.root)), 1)

    def test_a_fragment_in_a_subdirectory_is_refused_rather_than_dropped(self):
        write(self.root, "sub/12.added.md", "Nested.")
        hits = changelog.findings(self.root)
        self.assertEqual(hits, ["changelog: changelog.d/sub/ is a subdirectory; fragments live directly in changelog.d/"])

    def test_paths_outside_the_governed_roots_need_no_fragment(self):
        self.commit("tests/test_x.py")
        self.commit(".github/workflows/ci.yml")
        self.assertEqual(changelog.findings(self.root), [])

    def test_a_checkout_on_the_trunk_or_without_a_base_is_never_judged(self):
        (self.root / "docs" / "a.md").write_text("dirty\n")
        self.assertEqual(changelog.findings(self.root), [], "no commit of its own yet")
        git(self.root, "update-ref", "-d", "refs/remotes/origin/main")
        self.commit("docs/a.md", "again\n")
        self.assertEqual(changelog.findings(self.root), [], "no origin/main to compare with")

    def test_an_unreleased_edit_never_substitutes_for_a_fragment(self):
        for base in (HEAD, HEAD.replace("0.13.0", "0.12.0")):
            with self.subTest(base=base):
                if (self.root / "CHANGELOG.md").read_text() != base:
                    self.commit("CHANGELOG.md", base)
                git(self.root, "update-ref", "refs/remotes/origin/main", "HEAD")
                self.commit("CHANGELOG.md", base.replace("## [Unreleased]", "## [Unreleased]\n\n- New entry."))
                self.commit("docs/a.md", base)
                hits = changelog.findings(self.root)
                self.assertEqual(len(hits), 1)
                self.assertIn("adds no fragment", hits[0])

    def test_git_that_cannot_run_skips_the_rule_with_a_note(self):
        self.commit("docs/a.md")
        for error in (FileNotFoundError("git"), subprocess.TimeoutExpired("git", 10)):
            with self.subTest(error=type(error).__name__):
                notes = []
                with mock.patch.object(changelog.subprocess, "run", side_effect=error):
                    self.assertEqual(changelog.findings(self.root, notes), [])
                self.assertEqual(len(notes), 1)
                self.assertIn("fragment rule skipped", notes[0])

    def lint(self):
        """`harness lint` in-process, printing, with the projection-drift check a bare tree would crash."""
        import importlib.machinery
        from isolation import isolate_home
        loader = importlib.machinery.SourceFileLoader("harness_for_fragments", str(ROOT / "bin" / "harness"))
        harness = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(harness)
        home = Path(self.tmp.name + "-home")
        home.mkdir(exist_ok=True)
        self.addCleanup(lambda: __import__("shutil").rmtree(str(home), ignore_errors=True))
        out = io.StringIO()
        with mock.patch.dict(os.environ, {}), \
                mock.patch.object(harness.primitive_catalog, "projection_drift", return_value=[]), \
                redirect_stdout(out):
            isolate_home(home, quiet=False)
            code = harness.cmd_lint(harness.argparse.Namespace(path=str(self.root), staged=False))
        return code, out.getvalue().splitlines()

    def test_the_lint_command_applies_it_only_to_a_harness_checkout(self):
        self.commit("docs/a.md")
        _, lines = self.lint()
        self.assertFalse([line for line in lines if line.startswith("changelog:")], lines)

    def test_the_lint_command_reports_it_and_keeps_its_summary_line(self):
        self.commit("primitives/roles/.keep", "")
        git(self.root, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.commit("docs/a.md")
        code, lines = self.lint()
        self.assertEqual(code, 1, lines)
        self.assertTrue(any(line.startswith("changelog: this branch changes docs/a.md") for line in lines), lines)
        self.assertTrue(lines[-1].startswith("lint: 1 finding(s) in %s" % self.root.resolve()), lines[-1])

    def test_a_malformed_fragment_is_a_finding_even_on_the_trunk(self):
        write(self.root, "fix.md", "Text.")
        hits = changelog.findings(self.root)
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0].startswith("changelog: changelog fragment fix.md"))


if __name__ == "__main__":
    unittest.main()

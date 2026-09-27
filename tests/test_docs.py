# SPDX-License-Identifier: MIT
"""Unit tests for the onboarding surface: prerequisites, platform, first session, cost, support."""
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
README = (REPO / "README.md").read_text()
GUIDE = (REPO / "docs" / "getting-started.md").read_text()
SUPPORT = (REPO / "SUPPORT.md").read_text()
PREFERENCES = (REPO / "docs" / "preferences.md").read_text()


class ReadmeTests(unittest.TestCase):
    def test_the_safe_existing_runtime_path_comes_before_full_installation(self):
        quick_start = "## Try it with runtimes you already have"
        full_install = "## Full installation and ownership"
        self.assertIn(quick_start, README)
        self.assertLess(README.index(quick_start), README.index(full_install))
        self.assertLess(README.index("bin/harness sync --dry-run"),
                        README.index("\nbin/harness sync\n"))

    def test_the_account_is_stated_rather_than_assumed(self):
        prose = " ".join(README.split())
        self.assertIn("your own account for every runtime", prose)
        self.assertIn("does not provide model access", prose)

    def test_the_supported_platforms_are_named(self):
        self.assertIn("macOS and Linux", README)
        self.assertIn("Windows is unsupported", README)

    def test_the_tools_the_harness_itself_needs_are_named(self):
        for needle in ("`git`", "Python 3.9"):
            self.assertIn(needle, README, msg=needle)

    def test_the_guide_is_linked_before_full_installation_and_detailed_docs_are_listed(self):
        self.assertIn("](docs/getting-started.md)", README)
        self.assertLess(README.index("](docs/getting-started.md)"),
                        README.index("## Full installation and ownership"))
        self.assertIn("](docs/compatibility.md)", README)

    def test_status_and_native_evidence_limits_are_visible(self):
        prose = " ".join(README.split())
        self.assertIn("**Release status:**", prose)
        self.assertIn("`0.14.0` is a release candidate", prose)
        self.assertIn("`0.13.1` is the current stable release", prose)
        self.assertIn("two required Claude Code CLI targets", prose)
        # The page must name the last qualified release rather than leave a reader to infer one.
        self.assertIn("`0.11.1` remains the last release qualified", prose)
        # A candidate carries no evidence for its own source, so no client reads qualified.
        self.assertNotIn("**Qualified:**", prose)
        self.assertIn("**Unqualified:**", prose)
        self.assertIn("Projection generation and unit tests are not proof", prose)


class GuideTests(unittest.TestCase):
    def test_it_says_what_the_harness_is_not(self):
        self.assertIn("not an AI model", GUIDE)

    def test_it_carries_a_first_session_with_something_to_type(self):
        self.assertIn("## Your first session", GUIDE)
        self.assertIn("cd ~/some-folder\nclaude", GUIDE)
        self.assertIn("\ncodex\n", GUIDE)

    def test_it_is_honest_about_which_commands_need_a_code_project(self):
        self.assertIn("Build and review workflows need repository context", GUIDE)
        self.assertIn("signed-in GitHub account", GUIDE)

    def test_it_says_what_a_session_costs(self):
        self.assertIn("## What it costs", GUIDE)
        self.assertIn("rate-limit", GUIDE)

    def test_it_names_the_three_commands_that_diagnose_a_broken_install(self):
        for command in ("citizen doctor", "citizen diff", "citizen uninstall"):
            self.assertIn(command, GUIDE, msg=command)

    def test_the_links_it_offers_resolve(self):
        for name in ("usage.md", "preferences.md", "how-it-works.md", "sandboxing.md"):
            self.assertIn(f"]({name})", GUIDE, msg=name)
            self.assertTrue((REPO / "docs" / name).exists(), msg=name)


class SupportTests(unittest.TestCase):
    def test_support_sends_a_first_time_reader_to_the_guide_first(self):
        self.assertIn("docs/getting-started.md", SUPPORT)
        self.assertLess(SUPPORT.index("getting-started"), SUPPORT.index("Discussions"))

    def test_support_says_a_github_account_is_needed_for_the_rest(self):
        self.assertIn("needs a GitHub account and is public", SUPPORT)


class CostDocTests(unittest.TestCase):
    def test_preferences_explains_what_a_session_costs_and_names_the_dial(self):
        self.assertIn("## What a session costs", PREFERENCES)
        self.assertIn("`cost` stance is the dial", PREFERENCES)
        self.assertIn("citizen usage", PREFERENCES)


if __name__ == "__main__":
    unittest.main()

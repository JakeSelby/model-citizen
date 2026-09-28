# SPDX-License-Identifier: MIT
"""Unit tests for the onboarding surface: prerequisites, platform, first session, cost, support."""
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
README = (REPO / "README.md").read_text()
GUIDE = (REPO / "docs" / "getting-started.md").read_text()
SUPPORT = (REPO / "SUPPORT.md").read_text()
PREFERENCES = (REPO / "docs" / "preferences.md").read_text()
CURRENT_COMMAND_DOCS = (
    "README.md",
    "docs/getting-started.md",
    "docs/bmad.md",
    "docs/bmad-governance.md",
    "docs/runtime-installation.md",
    "docs/standalone-measurement.md",
    "docs/runtime-controls.md",
    "docs/primitive-authoring.md",
    "docs/telemetry.md",
    "docs/caught-in-the-act.md",
    "docs/compatibility.md",
    "docs/releasing.md",
    "docs/usage.md",
)
SOURCE_REFERENCES = {
    "docs/usage.md": {"`RULE_MIN_SESSIONS` in `bin/harness`"},
}


class ReadmeTests(unittest.TestCase):
    def test_the_safe_existing_runtime_path_comes_before_full_installation(self):
        quick_start = "## Try it with runtimes you already have"
        full_install = "## Full installation and ownership"
        self.assertIn(quick_start, README)
        self.assertLess(README.index(quick_start), README.index(full_install))
        self.assertLess(README.index("bin/citizen sync --dry-run"),
                        README.index("\nbin/citizen sync\n"))

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
        self.assertIn("`0.14.0` is the current stable release", prose)
        self.assertIn("two required Claude Code CLI targets", prose)
        self.assertIn("#741 were explicitly deferred to v0.15.0", prose)
        # The page must name the last qualified release rather than leave a reader to infer one.
        self.assertIn("`0.11.1` remains the last release qualified", prose)
        self.assertIn("**Qualified:** `claude-code-cli-macos`, `claude-code-cli-linux`", prose)
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

    def test_commands_after_entering_a_project_use_the_checkout_launcher(self):
        self.assertIn("~/repos/agent-harness/bin/citizen usage --rules", GUIDE)
        self.assertIn("~/repos/agent-harness/bin/citizen uninstall", GUIDE)

    def test_the_links_it_offers_resolve(self):
        for name in ("usage.md", "preferences.md", "how-it-works.md", "sandboxing.md"):
            self.assertIn(f"]({name})", GUIDE, msg=name)
            self.assertTrue((REPO / "docs" / name).exists(), msg=name)


class LivingBrandTests(unittest.TestCase):
    def test_current_command_docs_use_citizen_except_for_source_file_references(self):
        for rel in CURRENT_COMMAND_DOCS:
            for number, line in enumerate((REPO / rel).read_text().splitlines(), 1):
                if "bin/harness" not in line:
                    continue
                with self.subTest(path=rel, line=number):
                    remaining = line
                    for reference in SOURCE_REFERENCES.get(rel, set()):
                        remaining = remaining.replace(reference, "")
                    self.assertNotIn("bin/harness", remaining, msg=f"{rel}:{number}: {line}")

    def test_current_entry_pages_do_not_carry_the_retired_brand_disclaimer(self):
        old_brand = "Agent" + " Harness"
        for rel in ("README.md", "docs/getting-started.md"):
            with self.subTest(path=rel):
                self.assertNotIn("Formerly " + old_brand + ".", (REPO / rel).read_text())


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

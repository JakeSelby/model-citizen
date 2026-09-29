"""The two release checks that read GitHub: About drift and a closed on-the-way issue.

Every `gh` call is served from recorded JSON under `tests/fixtures/gh/`; nothing here
touches the network.
"""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_harness import REPO

FIXTURES = REPO / "tests" / "fixtures" / "gh"
REPO_VIEW = json.loads((FIXTURES / "repo-view.json").read_text())
OPEN_ISSUE = json.loads((FIXTURES / "issue-open.json").read_text())
CLOSED_ISSUE = json.loads((FIXTURES / "issue-closed.json").read_text())
PRODUCT = json.loads((REPO / "product.json").read_text())


def load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


sync_about = sys.modules.setdefault("sync_about", load("sync_about"))



class FakeGh:
    """A `gh` that answers from recorded output and records what it was asked."""

    def __init__(self, view=None, states=None, authenticated=True, fails=False):
        self.view = REPO_VIEW if view is None else view
        self.states = {} if states is None else states
        self.authenticated, self.fails = authenticated, fails
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        if args[:2] == ["auth", "status"]:
            if not self.authenticated:
                raise sync_about.GhError("gh is not available: no such file")
            return ""
        if self.fails:
            raise sync_about.GhError("HTTP 503: unavailable")
        if args[:2] == ["repo", "view"]:
            return json.dumps(self.view)
        if args[:2] == ["issue", "view"]:
            return json.dumps({"state": self.states.get(int(args[2]), OPEN_ISSUE["state"])})
        if args[:2] == ["repo", "edit"]:
            return ""
        raise AssertionError("unexpected gh call: " + " ".join(args))


def view_with(**changes):
    view = json.loads(json.dumps(REPO_VIEW))
    view.update(changes)
    return view


def product_root(**changes):
    """A checkout whose `product.json` differs from this repository's."""
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    data = json.loads(json.dumps(PRODUCT))
    data.update(changes)
    (root / "product.json").write_text(json.dumps(data))
    return temp, root


class AboutComparisonTests(unittest.TestCase):
    def test_the_recorded_about_panel_matches_the_committed_landing_copy(self):
        runner = FakeGh()
        wanted = sync_about.product(REPO)
        self.assertEqual(sync_about.differences(wanted, sync_about.published(runner)), [])

    def test_topics_compare_as_a_set_rather_than_a_sequence(self):
        shuffled = list(reversed(REPO_VIEW["repositoryTopics"]))
        runner = FakeGh(view=view_with(repositoryTopics=shuffled))
        wanted = sync_about.product(REPO)
        self.assertEqual(sync_about.differences(wanted, sync_about.published(runner)), [])

    def test_every_differing_field_is_named_with_both_values(self):
        topics = [topic for topic in REPO_VIEW["repositoryTopics"] if topic["name"] != "codex"]
        runner = FakeGh(view=view_with(description="something else", repositoryTopics=topics))
        wanted, live = sync_about.product(REPO), sync_about.published(runner)
        fields = sync_about.differences(wanted, live)
        self.assertEqual(fields, ["description", "topics"])
        lines = []
        sync_about.report(fields, wanted, live, out=lines.append)
        text = "\n".join(lines)
        self.assertIn("something else", text)
        self.assertIn(PRODUCT["github_description"], text)
        self.assertIn("codex", text)

    def test_homepage_is_compared_only_when_the_landing_copy_names_one(self):
        runner = FakeGh(view=view_with(homepageUrl="https://example.invalid"))
        live = sync_about.published(runner)
        temp, root = product_root(homepage="")
        self.addCleanup(temp.cleanup)
        self.assertEqual(sync_about.differences(sync_about.product(root), live), [])
        self.assertEqual(sync_about.differences(sync_about.product(REPO), live), ["homepage"])

    def test_the_landing_copy_names_the_project_site_as_the_homepage(self):
        self.assertEqual(PRODUCT["homepage"], "https://model-citizen.dev")
        self.assertIn("model-citizen", PRODUCT["topics"])
        self.assertLessEqual(len(PRODUCT["topics"]), 20)

    def test_an_apply_edits_only_the_fields_that_differ(self):
        topics = [topic for topic in REPO_VIEW["repositoryTopics"] if topic["name"] != "codex"]
        topics.append({"name": "retired-topic"})
        runner = FakeGh(view=view_with(repositoryTopics=topics))
        args = sync_about.edit_args(sync_about.product(REPO), sync_about.published(runner))
        self.assertEqual(args[:2], ["repo", "edit"])
        self.assertNotIn("--description", args)
        self.assertNotIn("--homepage", args)
        self.assertIn("--add-topic", args)
        self.assertEqual(args[args.index("--add-topic") + 1], "codex")
        self.assertEqual(args[args.index("--remove-topic") + 1], "retired-topic")


class SyncAboutCommandTests(unittest.TestCase):
    def run_main(self, argv, runner):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = sync_about.main(argv, REPO, runner)
        return code, out.getvalue()

    def test_check_passes_when_about_matches_and_fails_on_drift(self):
        self.assertEqual(self.run_main(["--check"], FakeGh())[0], 0)
        runner = FakeGh(view=view_with(description="stale"))
        code, text = self.run_main(["--check"], runner)
        self.assertEqual(code, 1)
        self.assertIn("About drift: description", text)
        self.assertNotIn(["repo", "edit"], runner.calls)

    def test_apply_writes_through_gh_repo_edit(self):
        runner = FakeGh(view=view_with(description="stale"))
        self.assertEqual(self.run_main(["--apply"], runner)[0], 0)
        self.assertEqual(runner.calls[-1][:2], ["repo", "edit"])

    def test_a_gh_failure_is_reported_rather_than_raised(self):
        code, text = self.run_main(["--check"], FakeGh(fails=True))
        self.assertEqual(code, 1)
        self.assertIn("About check failed", text)


class PreflightGithubChecksTests(unittest.TestCase):
    """The two checks sit behind one probe, and the probe decides skip from failure."""

    def setUp(self):
        self.module = load("release_preflight")
        for target, value in (("git", ""), ):
            patcher = patch.object(self.module, target, return_value=value)
            patcher.start(); self.addCleanup(patcher.stop)
        for owner, name in ((self.module.compatibility, "release_errors"),
                            (self.module.catalog, "projection_drift")):
            patcher = patch.object(owner, name, return_value=[] if name == "release_errors" else False)
            patcher.start(); self.addCleanup(patcher.stop)

    def check(self, runner):
        warnings = []
        errors = self.module.check(REPO, runner=runner, warn=warnings.append)
        return errors, warnings

    def test_a_matching_about_panel_and_open_issues_add_no_errors(self):
        errors, warnings = self.check(FakeGh())
        self.assertEqual((errors, warnings), ([], []))

    def test_an_unauthenticated_gh_skips_both_checks_with_the_named_warning(self):
        errors, warnings = self.check(FakeGh(authenticated=False))
        self.assertEqual(errors, [])
        self.assertEqual(warnings, ["release warning: About and On-the-way checks skipped, "
                                    "gh is not authenticated"])

    def test_a_gh_failure_after_a_good_probe_blocks_the_release(self):
        errors, warnings = self.check(FakeGh(fails=True))
        self.assertEqual(warnings, [])
        self.assertTrue(any("could not be read" in error for error in errors), msg=errors)

    def test_about_drift_blocks_the_release_and_names_the_field(self):
        errors, _ = self.check(FakeGh(view=view_with(description="stale")))
        self.assertEqual(errors, ["GitHub About does not match product.json: description"])

    def test_a_closed_on_the_way_issue_says_promote_or_remove(self):
        planned = [entry for entry in PRODUCT["on_the_way"] if "issue" in entry]
        self.assertTrue(planned, msg="the landing copy names no planned issue")
        number = planned[0]["issue"]
        errors, _ = self.check(FakeGh(states={number: CLOSED_ISSUE["state"]}))
        self.assertEqual(len(errors), 1, msg=errors)
        self.assertIn("promote or remove", errors[0])
        self.assertIn("#" + str(number), errors[0])


class ReleaseDocumentationTests(unittest.TestCase):
    RELEASING = " ".join((REPO / "docs" / "releasing.md").read_text().split())
    AGENTS = " ".join((REPO / "AGENTS.md").read_text().split())

    def test_the_documented_skip_warning_is_the_one_the_preflight_prints(self):
        self.assertIn(load("release_preflight").GH_SKIPPED, self.RELEASING)

    def test_the_cadence_and_bump_rules_are_written_where_agents_read_them(self):
        self.assertIn("## Issues, milestones and releases", self.AGENTS)
        for needle in ("seven days old", "at once as a patch", "scripts/sync_about.py --check"):
            self.assertIn(needle, self.AGENTS, msg=needle)
            self.assertIn(needle, self.RELEASING, msg=needle)

    def test_the_release_surfaces_end_at_this_repository(self):
        self.assertIn("A release has five surfaces", self.AGENTS)
        for text in (self.AGENTS, self.RELEASING):
            self.assertNotIn("--reference-repo", text)

    def test_evidence_reuse_is_documented_as_fail_closed_and_not_new_qualification(self):
        for needle in ("qualification_reuse", "refs/tags/v<prior>^{commit}", "fails closed",
                       "never rewritten", "not call v0.14.1 byte-identical or newly qualified"):
            self.assertIn(needle, self.RELEASING, msg=needle)

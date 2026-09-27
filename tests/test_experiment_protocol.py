"""The pre-registration gate: a run is pre-registered against a committed, dated, filled plan, or
it says it is exploratory. No test here launches an agent or calls a model."""
import datetime
import importlib.util
import io
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_harness import REPO

spec = importlib.util.spec_from_file_location("experiment_protocol", REPO / "scripts" / "experiment_protocol.py")
PROTOCOL = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PROTOCOL)

TEMPLATE = (REPO / "docs" / "pre-registration-template.md").read_text(encoding="utf-8")
DAY = datetime.date(2026, 10, 1)


def filled_plan(date=DAY.isoformat()):
    """The shipped template with every placeholder filled, so the field names stay the template's."""
    return PROTOCOL.PLACEHOLDER.sub("filled", TEMPLATE.replace("<YYYY-MM-DD>", date))


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t"] + list(args),
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def commit_plan(repo, name="%s-harness-vs-bare.md" % DAY.isoformat(), text=None):
    """A plan committed to `repo`, a git repository, under benchmarks/preregistrations/."""
    path = Path(repo) / PROTOCOL.DIRECTORY / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(filled_plan() if text is None else text, encoding="utf-8")
    git(repo, "add", "--", str(path))
    git(repo, "commit", "-qm", "docs(benchmarks): pre-register %s" % name)
    return path


def new_repo(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    (root / "README").write_text("r\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "chore: start")
    return root


class PlanFieldTests(unittest.TestCase):
    def test_the_unfilled_template_misses_every_required_field(self):
        self.assertEqual(PROTOCOL.missing_fields(TEMPLATE),
                         ["Run: Question", "Run: Date registered", "Sample size: Tasks",
                          "Sample size: Trials per task and arm"])

    def test_a_filled_template_misses_nothing(self):
        self.assertEqual(PROTOCOL.missing_fields(filled_plan()), [])

    def test_an_emptied_required_section_is_named(self):
        text = filled_plan()
        head, rest = text.split("## Decision rule", 1)
        text = head + "## Decision rule\n\n" + rest[rest.index("## Exclusions"):]
        self.assertEqual(PROTOCOL.missing_fields(text), ["Decision rule"])

    def test_a_placeholder_in_a_continuation_line_counts_as_unfilled(self):
        text = filled_plan().replace("- **Metric:** Cost-of-Pass ratio",
                                     "- **Metric:** Cost-of-Pass ratio\n  measured over <which set>", 1)
        self.assertEqual(PROTOCOL.missing_fields(text), ["Primary metric: Metric"])


class CheckTests(unittest.TestCase):
    def test_a_committed_dated_filled_plan_passes_and_names_its_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = new_repo(Path(tmp) / "r")
            path = commit_plan(repo)
            errors, record = PROTOCOL.check(str(path), repo, today=DAY)
            self.assertEqual(errors, [])
            head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], stdout=subprocess.PIPE,
                                  universal_newlines=True).stdout.strip()
            self.assertEqual(record, {"evidence": "pre-registered",
                                      "pre_registration": "benchmarks/preregistrations/2026-10-01-harness-vs-bare.md",
                                      "pre_registration_commit": head})

    def test_an_uncommitted_plan_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = new_repo(Path(tmp) / "r")
            path = repo / PROTOCOL.DIRECTORY / "2026-10-01-x.md"
            path.parent.mkdir(parents=True)
            path.write_text(filled_plan(), encoding="utf-8")
            errors, record = PROTOCOL.check(str(path), repo, today=DAY)
            self.assertIsNone(record)
            self.assertTrue(any("not committed" in e for e in errors), errors)

    def test_an_edit_after_the_commit_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = new_repo(Path(tmp) / "r")
            path = commit_plan(repo)
            path.write_text(filled_plan() + "\nlater\n", encoding="utf-8")
            errors, _ = PROTOCOL.check(str(path), repo, today=DAY)
            self.assertTrue(any("uncommitted changes" in e for e in errors), errors)

    def test_an_undated_name_and_a_mismatched_or_future_date_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = new_repo(Path(tmp) / "r")
            undated = commit_plan(repo, name="harness-vs-bare.md")
            errors, _ = PROTOCOL.check(str(undated), repo, today=DAY)
            self.assertTrue(any("must start with its date" in e for e in errors), errors)
            other = commit_plan(repo, name="2026-09-30-x.md")
            errors, _ = PROTOCOL.check(str(other), repo, today=DAY)
            self.assertTrue(any("differs from the date in the file name" in e for e in errors), errors)
            errors, _ = PROTOCOL.check(str(commit_plan(repo)), repo, today=DAY - datetime.timedelta(days=1))
            self.assertTrue(any("after today" in e for e in errors), errors)

    def test_an_unfilled_plan_is_refused_with_its_missing_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = new_repo(Path(tmp) / "r")
            path = commit_plan(repo, text=TEMPLATE)
            errors, _ = PROTOCOL.check(str(path), repo, today=DAY)
            self.assertTrue(any("Run: Question" in e for e in errors), errors)

    def test_a_plan_outside_the_repository_or_missing_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = new_repo(Path(tmp) / "r")
            outside = Path(tmp) / "2026-10-01-x.md"
            outside.write_text(filled_plan(), encoding="utf-8")
            errors, record = PROTOCOL.check(str(outside), repo, today=DAY)
            self.assertIsNone(record)
            self.assertIn("not inside the repository", errors[0])
            errors, _ = PROTOCOL.check("benchmarks/preregistrations/2026-10-01-none.md", repo, today=DAY, cwd=tmp)
            self.assertIn("does not exist", errors[0])


class AdmitTests(unittest.TestCase):
    def admit(self, *args, **kwargs):
        err = io.StringIO()
        return PROTOCOL.admit(*args, err=err, **kwargs), err.getvalue()

    def test_neither_flag_refuses_and_names_both_ways_forward(self):
        err = io.StringIO()
        with self.assertRaises(SystemExit) as stop:
            PROTOCOL.admit(None, False, REPO, prog="bench", err=err)
        self.assertEqual(stop.exception.code, 2)
        self.assertIn("--pre-registration", err.getvalue())
        self.assertIn("--exploratory", err.getvalue())

    def test_both_flags_refuse(self):
        with self.assertRaises(SystemExit):
            PROTOCOL.admit("plan.md", True, REPO, err=io.StringIO())

    def test_exploratory_is_labelled_and_writes_no_history(self):
        stamp, err = self.admit(None, True, REPO)
        self.assertEqual(stamp["evidence"], "exploratory")
        self.assertIn("never cited as evidence", err)
        self.assertFalse(PROTOCOL.writes_history([dict(stamp, arm="bare")]))

    def test_a_bad_plan_refuses_with_exit_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = new_repo(Path(tmp) / "r")
            path = commit_plan(repo, text=TEMPLATE)
            err = io.StringIO()
            with self.assertRaises(SystemExit) as stop:
                PROTOCOL.admit(str(path), False, repo, today=DAY, err=err)
            self.assertEqual(stop.exception.code, 2)
            self.assertIn("refusing to run", err.getvalue())

    def test_a_good_plan_is_admitted_and_writes_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = new_repo(Path(tmp) / "r")
            stamp, _ = self.admit(str(commit_plan(repo)), False, repo, today=DAY)
            self.assertEqual(stamp["evidence"], "pre-registered")
            self.assertTrue(PROTOCOL.writes_history([dict(stamp, arm="bare"), dict(stamp, arm="harness")]))

    def test_rows_without_a_label_or_mixed_with_exploratory_write_no_history(self):
        good = {"evidence": "pre-registered"}
        self.assertFalse(PROTOCOL.writes_history([]))
        self.assertFalse(PROTOCOL.writes_history([good, {}]))
        self.assertFalse(PROTOCOL.writes_history([good, {"evidence": "exploratory"}]))


if __name__ == "__main__":
    unittest.main()

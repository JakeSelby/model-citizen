# SPDX-License-Identifier: MIT
"""The figures this repository publishes about itself, derived from what they describe.

Every claim here was written by hand once and went stale silently: the landing copy said
nineteen detectors while the registry held seventeen, and the benchmark oracle's module count
reads as a fact about HEAD when it is a fact about a pinned snapshot. Each test derives the
number from the thing it is a number of, so the next drift fails rather than ships (#516).
"""
import importlib.util
import json
import re
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from context_budget import LINE_BUDGET, LINE_CAP, load_harness  # noqa: E402

WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
         "nineteen", "twenty")
WHEEL = REPO / "lib" / "vendor" / "ruleprobe-0.1.0-py3-none-any.whl"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RD = load(REPO / "claude" / "hooks" / "rule-detectors.py", "doc_figures_rule_pack")
BENCH = load(REPO / "scripts" / "cost_bench.py", "doc_figures_cost_bench")


def spelled(n):
    """The copy's spelling of a count, capitalised as a sentence opener too."""
    return WORDS[n]


def labelled(text):
    """Every detector id a corpus labels file names, in a fire or a near list."""
    found = set()
    for match in re.finditer(r"(?:fire|near):\s*\[([^\]]*)\]", text):
        found.update(part.strip() for part in match.group(1).split(",") if part.strip())
    return found


def lines_naming(path, needle):
    body = (REPO / path).read_text(encoding="utf-8")
    return [line for line in body.splitlines() if needle in line]


class DetectorCountTests(unittest.TestCase):
    """The landing copy states the number of detectors the registry actually holds."""

    def setUp(self):
        self.count = len(RD.DETECTORS)
        self.product = json.loads((REPO / "product.json").read_text(encoding="utf-8"))

    def copy_lines(self):
        """Every published sentence that states a detector count. The hero proof no longer
        states one (#764), so it is checked only if it names detectors again."""
        proof = self.product["hero"]["proof"]
        found = [proof] if "detector" in proof else []
        for group in self.product["capabilities"]:
            for feature in group["features"]:
                if "deterministic detectors" in feature["line"]:
                    found.append(feature["line"])
        found += lines_naming("README.md", "deterministic detectors read the transcript")
        return found

    def test_the_registry_and_the_landing_copy_state_the_same_count(self):
        self.assertGreaterEqual(len(self.copy_lines()), 2)  # feature line, README
        for line in self.copy_lines():
            self.assertIn(spelled(self.count), line.lower(), msg=line)

    def test_no_published_sentence_states_a_different_count(self):
        wrong = set(WORDS) - {spelled(len(RD.DETECTORS))}
        for line in self.copy_lines():
            for word in re.findall(r"[a-z]+", line.lower()):
                self.assertNotIn(word, wrong, msg=line)

    def test_the_generic_half_is_the_engine_s_and_the_rest_is_this_repository_s(self):
        generic = [d.id for d in RD._GENERIC]
        self.assertEqual(len(generic), 6)
        self.assertEqual(len(RD.DETECTORS) - len(generic), 13)

    def test_the_docs_say_how_many_detectors_the_two_corpora_score(self):
        """The wheel's labels and this repository's together name every detector, and the
        docs say how many that is. The wheel's half still covers the generic detectors and
        no other, which is why a repository corpus had to exist at all (#522)."""
        with zipfile.ZipFile(str(WHEEL)) as wheel:
            shipped = wheel.read("ruleprobe/corpus/labels.yaml").decode("utf-8")
        ours = (REPO / "tests" / "fixtures" / "detector-corpus" / "labels.yaml").read_text(
            encoding="utf-8")
        self.assertEqual(labelled(shipped), set(d.id for d in RD._GENERIC))
        scored = labelled(shipped) | labelled(ours)
        self.assertEqual(scored, set(RD.DETECTORS))
        claim = "%s detectors of %s" % (spelled(len(scored)), spelled(len(RD.DETECTORS)))
        for path in ("docs/field-scan.md", "docs/caught-in-the-act.md"):
            self.assertTrue(lines_naming(path, claim), msg=path)
            self.assertTrue(lines_naming(path, "--floor 0.9"), msg=path)


class PinnedSnapshotTests(unittest.TestCase):
    """The hook-ids oracle counts modules at the task's parent, not at HEAD."""

    def test_expected_modules_is_the_glob_at_the_task_s_parent_sha(self):
        oracle = BENCH._oracle(REPO, "hook_ids")
        manifest = json.loads((REPO / BENCH.TASKS).read_text(encoding="utf-8"))
        tasks = manifest["tasks"] + [entry["task"] for entry in manifest["retired"]]
        sha = [t for t in tasks if t["id"] == "hook-ids"][0]["parent_sha"]
        listed = subprocess.check_output(
            ["git", "ls-tree", "--name-only", sha, "policy/hooks/"], cwd=str(REPO))
        at_sha = [n for n in listed.decode("utf-8").split() if n.endswith(".py")]
        self.assertEqual(oracle.EXPECTED_MODULES, len(at_sha))

    def test_the_comment_says_which_sha_the_count_belongs_to(self):
        source = (REPO / "benchmarks" / "oracles" / "hook_ids.py").read_text(encoding="utf-8")
        self.assertIn("parent_sha", source.split("EXPECTED_MODULES")[0])


class ContextScopeTests(unittest.TestCase):
    """Both always-loaded figures name the set they count, and the ratchet is the cap."""

    def test_the_line_ratchet_is_the_line_cap_itself(self):
        self.assertEqual(LINE_BUDGET, LINE_CAP)

    def test_the_lint_context_line_names_its_scope(self):
        summary = load_harness().context_summary(REPO)
        self.assertIn("longest variant of every stance", summary)
        self.assertIn("benchmarks/static.json", summary)

    def test_the_committed_static_figure_names_the_scope_of_every_count(self):
        committed = json.loads((REPO / BENCH.STATIC).read_text(encoding="utf-8"))
        self.assertEqual(committed["scopes"], BENCH.SCOPES)
        for key in ("always_loaded", "listings", "worst_case_est_tokens"):
            self.assertIn(key, committed)
            self.assertTrue(committed["scopes"][key].strip())
        self.assertIn("harness lint", committed["scopes"]["note"])


if __name__ == "__main__":
    unittest.main()

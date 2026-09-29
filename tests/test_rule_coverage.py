# SPDX-License-Identifier: MIT
"""`usage --rules` lists every loaded rule as measured, dark or unmeasured (#647).

The states, the share line and the detector file are standalone `ruleprobe`'s, read through the
vendored engine, so one `.ruleprobe/detectors.yaml` works in both. Each state is pinned here,
with the share, a declarative detector loading and firing through the session hook, and a
malformed file refused entry by entry with its line.

Run: python3 -m unittest discover -s tests -p 'test_rule_coverage.py'
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))
from harness_core import rule_coverage  # noqa: E402

STAMP = "2026-09-20T12:00:00.000Z"
DETECTOR_FILE = """detectors:
  - id: house-style/sudo-install
    rule: house-style
    event: tool_use
    when:
      command: {starts_with: [sudo, pip]}
"""
# The second entry's misspelt matcher key is refused by the engine's compiler, which names the
# line of its `when:` (10), and the first entry still loads.
MALFORMED = DETECTOR_FILE + """  - id: house-style/broken
    rule: house-style
    event: tool_use
    when:
      command: {starts_wiht: sudo}
"""
# `yes` is refused by the engine's parser, which loads nothing from a file it cannot read.
UNPARSED = DETECTOR_FILE + """  - id: house-style/ambiguous
    rule: house-style
    event: tool_use
    when:
      command: {starts_with: yes}
"""


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(module)
    return module


harness = _load("harness_cli_coverage", REPO / "bin" / "harness")
rd = _load("rule_detectors_coverage", REPO / "claude" / "hooks" / "rule-detectors.py")
usage_log = _load("usage_log_coverage", REPO / "claude" / "hooks" / "usage-log.py")


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def classify(surface, stances=None, extra=()):
    detectors = list(rd.DETECTORS.values()) + list(extra)
    return rule_coverage.classify(surface, detectors, dict(rd.OPT_OUT), stances or {},
                                  rd.read_rule_file)


def by_name(rules):
    return dict((r.rule, r) for r in rules)


class EachState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def rule(self, name, text="# A rule\n\nDo the thing.\n"):
        return (name, write(self.root / "rules" / (name + ".md"), text))

    def test_a_registry_detector_measures_its_rule(self):
        rules, _ = classify([self.rule("secrets")])
        entry = rules[0]
        self.assertEqual(entry.state, "measured")
        self.assertIn("secrets/git-add-secret-file", entry.detectors)
        self.assertEqual(entry.reason, "")

    def test_an_opt_out_entry_is_dark_with_its_reason(self):
        entry = classify([self.rule("conciseness")])[0][0]
        self.assertEqual((entry.state, entry.reason), ("dark", rd.OPT_OUT["conciseness"]))

    def test_front_matter_opt_out_is_dark_with_its_reason(self):
        text = "---\nopt_out: judged by a reviewer, not a transcript\n---\n# Taste\n"
        entry = classify([self.rule("taste", text)])[0][0]
        self.assertEqual((entry.state, entry.reason),
                         ("dark", "judged by a reviewer, not a transcript"))

    def test_front_matter_names_the_rule(self):
        text = "---\nrule: secrets\n---\n# Keys stay out of git\n"
        entry = classify([self.rule("keys", text)])[0][0]
        self.assertEqual((entry.rule, entry.state), ("secrets", "measured"))

    def test_a_rule_nothing_names_is_unmeasured_with_a_reason(self):
        entry = classify([self.rule("house-style")])[0][0]
        self.assertEqual((entry.state, entry.reason), ("unmeasured", rule_coverage.NO_DETECTOR))

    def test_a_rule_whose_detectors_are_gated_off_is_unmeasured(self):
        stance = ("commits", write(self.root / "stances" / "commits" / "off.md", "# Off\n"))
        entry = classify([stance], stances={"commits": "off"})[0][0]
        self.assertEqual((entry.state, entry.reason), ("unmeasured", rule_coverage.GATED_OFF))
        on = classify([stance], stances={"commits": "conventional-attributed"})[0][0]
        self.assertEqual(on.state, "measured")

    def test_a_second_file_of_one_name_does_not_borrow_the_first_ones_detectors(self):
        stance = ("delegation", write(self.root / "stances" / "delegation" / "tiered.md", "# T\n"))
        rules, _ = classify([self.rule("delegation"), stance])
        self.assertEqual([r.state for r in rules], ["measured", "unmeasured"])
        self.assertIn("the first rule of that name", rules[1].reason)

    def test_a_front_matter_detector_is_not_claimed_as_measured(self):
        """The session hook never runs it, so reporting it measured would be a quiet zero."""
        text = ("---\ndetector:\n  event: tool_use\n  when:\n"
                "    command: {starts_with: [sudo, pip]}\n---\n# House style\n")
        entry = classify([self.rule("house-style", text)])[0][0]
        self.assertEqual((entry.state, entry.reason),
                         ("unmeasured", rule_coverage.FRONT_MATTER_DETECTOR))


class TheShare(unittest.TestCase):
    @staticmethod
    def rules(*states):
        return [rule_coverage.Rule("r%d" % i, "/r%d.md" % i, s, "", [])
                for i, s in enumerate(states)]

    def test_dark_rules_count_in_the_denominator(self):
        rules = self.rules("measured", "dark", "unmeasured")
        self.assertAlmostEqual(rule_coverage.share(rules), 1 / 3.0)
        self.assertEqual(rule_coverage.summary(rules, [], relative_to="/")[0],
                         "rules: 1 measured, 1 dark, 1 unmeasured (33% measured)")

    def test_the_share_is_floored_so_a_gap_never_reads_whole(self):
        rules = self.rules(*(["measured"] * 199 + ["unmeasured"]))
        self.assertIn("(99% measured)", rule_coverage.summary(rules, [])[0])

    def test_one_measured_rule_in_many_is_not_zero(self):
        rules = self.rules(*(["measured"] + ["unmeasured"] * 200))
        self.assertIn("(<1% measured)", rule_coverage.summary(rules, [])[0])

    def test_no_rules_has_no_share(self):
        self.assertIsNone(rule_coverage.share([]))
        self.assertEqual(rule_coverage.summary([], []), [])

    def test_every_rule_is_a_line_with_its_reason(self):
        rules = self.rules("measured", "dark")
        rules[1] = rules[1]._replace(reason="judged by a reviewer")
        lines = rule_coverage.summary(rules, [], relative_to="/")
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[2].startswith("  dark       r1"))
        self.assertTrue(lines[2].endswith("r1.md: judged by a reviewer"))


class DeclarativeDetectors(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.nested = self.repo / "src" / "pkg"
        self.nested.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_repository_file_is_found_from_a_subdirectory_and_loaded(self):
        write(self.repo / ".ruleprobe" / "detectors.yaml", DETECTOR_FILE)
        detectors, findings = rd.declarative(str(self.nested))
        self.assertEqual([d.id for d in detectors], ["house-style/sudo-install"])
        self.assertEqual(findings, [])

    def test_no_directory_loads_nothing(self):
        self.assertEqual(rd.declarative(""), ([], []))

    def test_its_rule_counts_as_measured(self):
        write(self.repo / ".ruleprobe" / "detectors.yaml", DETECTOR_FILE)
        rule = write(self.repo / "rules" / "house-style.md", "# House style\n")
        lines = harness.rule_coverage_lines(self.repo, surface=[("house-style", rule)],
                                            stances={})
        self.assertEqual(lines[0], "rules: 1 measured, 0 dark, 0 unmeasured (100% measured)")
        self.assertTrue(lines[1].startswith("  measured   house-style"))

    def test_an_override_that_changes_the_rule_moves_the_measurement(self):
        """A reused shipped id replaces the shipped detector, as the hook's registry does, so
        the rule it named loses it rather than staying measured by a detector that never runs."""
        write(self.repo / ".ruleprobe" / "detectors.yaml", DETECTOR_FILE.replace(
            "house-style/sudo-install", "secrets/git-add-secret-file"))
        secrets = [d.id for d in rd.DETECTORS.values() if d.rule == "secrets"]
        self.assertEqual(secrets, ["secrets/secret-in-write", "secrets/git-add-secret-file"])
        rules_dir = self.repo / "rules"
        surface = [("secrets", write(rules_dir / "secrets.md", "# Secrets\n")),
                   ("house-style", write(rules_dir / "house-style.md", "# House style\n"))]
        lines = harness.rule_coverage_lines(self.repo, surface=surface, stances={})
        self.assertEqual(lines[0], "rules: 2 measured, 0 dark, 0 unmeasured (100% measured)")
        # With the shipped `secret-in-write` gone too, `secrets` would be unmeasured.
        only = write(self.repo / ".ruleprobe" / "detectors.yaml", DETECTOR_FILE.replace(
            "house-style/sudo-install", "secrets/secret-in-write") + DETECTOR_FILE.replace(
            "detectors:\n", "").replace("house-style/sudo-install", "secrets/git-add-secret-file"))
        self.assertTrue(only.is_file())
        lines = harness.rule_coverage_lines(self.repo, surface=surface, stances={})
        self.assertEqual(lines[0], "rules: 1 measured, 0 dark, 1 unmeasured (50% measured)")
        self.assertTrue(lines[1].startswith("  unmeasured secrets"), lines)

    def test_it_fires_in_the_session_hook(self):
        write(self.repo / ".ruleprobe" / "detectors.yaml", DETECTOR_FILE)
        path = write(Path(self.tmp.name) / "session.jsonl", "".join(json.dumps(e) + "\n" for e in [
            {"type": "user", "sessionId": "s-1", "cwd": str(self.repo), "timestamp": STAMP,
             "message": {"role": "user", "content": "install ruff"}},
            {"type": "assistant", "sessionId": "s-1", "cwd": str(self.repo), "timestamp": STAMP,
             "message": {"id": "m1", "model": "model-a", "content": [
                 {"type": "tool_use", "id": "toolu_1", "name": "Bash",
                  "input": {"command": "sudo pip install ruff"}}]}},
        ]))
        record = usage_log.scan(path, "s-1", str(self.repo))
        self.assertNotIn("rules_error", record)
        self.assertEqual(record["rules"].get("house-style/sudo-install"), 1)
        # Outside the repository the file is not in force, and nothing fires.
        elsewhere = usage_log.scan(path, "s-1", str(Path(self.tmp.name)))
        self.assertNotIn("house-style/sudo-install", elsewhere.get("rules", {}))

    def session(self, command):
        return write(Path(self.tmp.name) / "session.jsonl", "".join(json.dumps(e) + "\n" for e in [
            {"type": "user", "sessionId": "s-1", "cwd": str(self.repo), "timestamp": STAMP,
             "message": {"role": "user", "content": "go"}},
            {"type": "assistant", "sessionId": "s-1", "cwd": str(self.repo), "timestamp": STAMP,
             "message": {"id": "m1", "model": "model-a", "content": [
                 {"type": "tool_use", "id": "toolu_1", "name": "Bash",
                  "input": {"command": command}}]}},
        ]))

    def test_a_loaded_detector_with_no_hit_is_recorded_as_zero(self):
        """The ledger carries the zero, so a report run from another repository shows it."""
        write(self.repo / ".ruleprobe" / "detectors.yaml", DETECTOR_FILE)
        record = usage_log.scan(self.session("uv pip install ruff"), "s-1", str(self.repo))
        self.assertEqual(record["rules"].get("house-style/sudo-install"), 0)
        lines = []
        real_say = harness.say
        harness.say = lines.append
        try:
            harness.rule_report([dict(record, repo="elsewhere")], "repo", 30)
        finally:
            harness.say = real_say
        self.assertNotIn("house-style/sudo-install 0", "\n".join(lines))

    def test_a_malformed_entry_does_not_unmeasure_the_session(self):
        """A finding is not a `rules_errors` entry, which would drop the whole session."""
        write(self.repo / ".ruleprobe" / "detectors.yaml", MALFORMED)
        record = usage_log.scan(self.session("sudo pip install ruff"), "s-1", str(self.repo))
        self.assertNotIn("rules_errors", record)
        self.assertNotIn("rules_error", record)
        self.assertEqual(record["rules"].get("house-style/sudo-install"), 1)
        self.assertNotIn("house-style/broken", record["rules"])

    def test_a_zero_hit_detector_is_still_a_line(self):
        lines = []
        real_say = harness.say
        harness.say = lines.append
        try:
            harness.rule_report([{"rules": {}}], "rule", 30, ["house-style/sudo-install"])
        finally:
            harness.say = real_say
        line = [ln for ln in lines if ln.startswith("house-style/sudo-install")]
        self.assertEqual(len(line), 1, lines)
        self.assertEqual(line[0].split()[1:4], ["0", "0", "1"])


class AMalformedFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_bad_entry_is_refused_with_its_line_and_the_rest_loads(self):
        write(self.repo / ".ruleprobe" / "detectors.yaml", MALFORMED)
        detectors, findings = rd.declarative(str(self.repo))
        self.assertEqual([d.id for d in detectors], ["house-style/sudo-install"])
        self.assertEqual([f.line for f in findings], [10])
        self.assertIn("starts_wiht", findings[0].reason)

    def test_a_file_that_does_not_parse_loads_nothing_and_names_its_line(self):
        write(self.repo / ".ruleprobe" / "detectors.yaml", UNPARSED)
        detectors, findings = rd.declarative(str(self.repo))
        self.assertEqual(detectors, [])
        self.assertEqual([f.line for f in findings], [11])
        self.assertIn("'yes'", findings[0].reason)

    def test_the_report_prints_the_file_and_line(self):
        write(self.repo / ".ruleprobe" / "detectors.yaml", MALFORMED)
        rule = write(self.repo / "rules" / "house-style.md", "# House style\n")
        lines = harness.rule_coverage_lines(self.repo, surface=[("house-style", rule)],
                                            stances={})
        self.assertIn("findings: 1 (everything else still loaded)", lines)
        found = [ln for ln in lines if ln.startswith("  .ruleprobe/detectors.yaml:10  ")]
        self.assertEqual(len(found), 1, lines)
        # The good entry still measures the rule.
        self.assertTrue(lines[1].startswith("  measured   house-style"))


class TheSurface(unittest.TestCase):
    def test_rules_imported_into_an_external_root_are_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            imported = write(Path(tmp) / "rules" / "team-review.md", "# Review\n")
            write(Path(tmp) / "rules" / "withheld.md", "# Withheld\n")
            cfg = {"primitive_roots": [tmp], "stances": {}}
            surface = harness.rule_surface(cfg, {"rules": {"withheld": "off"}})
            names = [name for name, _ in surface]
            self.assertIn(("team-review", imported), surface)
            self.assertNotIn("withheld", names)
            self.assertIn("secrets", names)
            rules, _ = classify(surface)
            self.assertEqual(by_name(rules)["team-review"].state, "unmeasured")

    def test_a_rule_switched_off_is_not_in_the_surface(self):
        surface = harness.rule_surface({"stances": {}}, {"rules": {"secrets": "off"}})
        self.assertNotIn("secrets", [name for name, _ in surface])

    def test_each_selected_stance_is_listed_under_its_dimension(self):
        cfg = harness.load_config({})
        cfg["primitive_roots"] = []
        surface = dict(harness.rule_surface(cfg, {}))
        for dimension, variant in cfg["stances"].items():
            self.assertEqual(Path(surface[dimension]).stem, variant)


class TheCommand(unittest.TestCase):
    def test_usage_rules_prints_the_block_in_a_fresh_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            repo = Path(tmp) / "repo"
            write(repo / ".ruleprobe" / "detectors.yaml", MALFORMED)
            env = dict((k, v) for k, v in os.environ.items()
                       if not k.startswith(("HARNESS_", "CLAUDE")))
            env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
                       XDG_STATE_HOME=str(home / ".local" / "state"),
                       CLAUDE_CONFIG_DIR=str(home / ".claude"), CODEX_HOME=str(home / ".codex"))
            out = subprocess.run([sys.executable, str(REPO / "bin" / "harness"), "usage",
                                  "--rules"], cwd=str(repo), env=env, capture_output=True,
                                 text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        text = out.stdout
        self.assertRegex(text, r"\nrules: \d+ measured, \d+ dark, \d+ unmeasured \(\d+% measured\)")
        self.assertIn("  dark       conciseness", text)
        self.assertIn("  .ruleprobe/detectors.yaml:10  unknown command key 'starts_wiht'", text)


if __name__ == "__main__":
    unittest.main()

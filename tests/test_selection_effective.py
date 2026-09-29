# SPDX-License-Identifier: MIT
"""A stance selection takes effect in every layer that speaks to it: the always-loaded rule,
the stance text, the violation detector, both runtime projections and repeated syncs, and a
session's override stays in the session.

Run: python3 -m unittest discover tests
"""
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "policy" / "hooks"
sys.path.insert(0, str(HOOKS))
import posture  # noqa: E402

loader = importlib.machinery.SourceFileLoader("harness", str(REPO / "bin" / "harness"))
spec = importlib.util.spec_from_loader("harness", loader)
harness = importlib.util.module_from_spec(spec)
loader.exec_module(harness)

_rd_spec = importlib.util.spec_from_file_location("rule_detectors_sel", HOOKS / "rule-detectors.py")
rd = importlib.util.module_from_spec(_rd_spec)
_rd_spec.loader.exec_module(rd)

COST = REPO / "primitives" / "stances" / "cost"
RULE = REPO / "primitives" / "rules" / "cache-hygiene.md"
COMPACT = "cache-hygiene/compact"
SESSION = [{"kind": "compact", "turn": 1}, {"kind": "user_prompt", "turn": 2}]


def compaction_switch(variant):
    return posture.table_for({"cost": variant}, {}, strict=True)["switches"]["compaction"]


class CompactionPrecedenceTests(unittest.TestCase):
    def test_shipped_switches_determine_whether_compaction_is_a_miss(self):
        for path in COST.glob("*.md"):
            with self.subTest(variant=path.stem):
                found = rd.run(SESSION, {"cost": path.stem}, strict=True)
                self.assertEqual(COMPACT not in found,
                                 compaction_switch(path.stem) == "compact-allowed")

    def test_custom_inheritance_and_explicit_switches_determine_compaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            primitives = home / "custom"
            cost = primitives / "stances" / "cost"
            cost.mkdir(parents=True)
            cfg = home / ".config" / "agent-harness" / "config.json"
            cfg.parent.mkdir(parents=True)
            cfg.write_text(json.dumps({"primitive_roots": [str(primitives)]}))
            variants = {
                "inherited": {"extends": "max"},
                "explicit": {"extends": "balanced", "switches": {"compaction": "compact-allowed"}},
                "restricted": {"extends": "max", "switches": {"compaction": "clear-only"}},
            }
            for name, data in variants.items():
                (cost / (name + ".json")).write_text(json.dumps(dict(schema_version=1, **data)))
                (cost / (name + ".md")).write_text("# Custom cost\n")
            with patch.dict(os.environ, {"HOME": tmp, "HARNESS_HOME": tmp}):
                for name in variants:
                    with self.subTest(variant=name):
                        found = rd.run(SESSION, {"cost": name}, strict=True)
                        self.assertEqual(COMPACT in found, name == "restricted")

    def test_a_compaction_under_max_is_not_counted_as_a_miss(self):
        self.assertNotIn(COMPACT, rd.run(SESSION, {"cost": "max"}, strict=True))

    def test_a_compaction_still_counts_wherever_the_rule_stands(self):
        for stances in ({"cost": "frugal"}, {"cost": "balanced"}, {}, None):
            with self.subTest(stances=stances):
                self.assertEqual(len(rd.run(SESSION, stances, strict=True)[COMPACT]), 1)

    def test_the_gate_reaches_only_the_compaction_detector(self):
        switch = [{"kind": "assistant_text", "turn": 1, "text": "a", "model": "m-a"},
                  {"kind": "assistant_text", "turn": 2, "text": "b", "model": "m-b"}]
        self.assertIn("cache-hygiene/model-switch", rd.run(switch, {"cost": "max"}, strict=True))
        self.assertIs(rd.DETECTORS[COMPACT].fn,
                      {d.id: d.fn for d in rd.generic.DETECTORS}[COMPACT])

    def test_the_rule_names_the_exception_the_stance_takes(self):
        text = RULE.read_text()
        self.assertIn("not compaction", text)
        self.assertIn("unless `cost` allows it", text)
        self.assertIn("compaction\nis allowed", (COST / "max.md").read_text())

    def test_the_precedence_is_documented(self):
        doc = (REPO / "docs" / "preferences.md").read_text()
        self.assertIn("the variant's `compaction` switch is what decides", doc)

    def test_a_session_override_is_the_one_selection_the_hook_and_the_detector_read(self):
        with tempfile.TemporaryDirectory() as home:
            env = {"HOME": home, "HARNESS_STANCE_COST": "max"}
            resolved = posture.resolve(env, table=True)
            self.assertEqual(resolved["switches"]["compaction"], "compact-allowed")
            self.assertNotIn(COMPACT, rd.run(SESSION, resolved["stances"], strict=True))
            plain = posture.resolve({"HOME": home})["stances"]
            self.assertIn(COMPACT, rd.run(SESSION, plain, strict=True))


class SyncTests(unittest.TestCase):
    """End to end through `harness sync`, in a throwaway home, for both runtime projections."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = dict(os.environ)
        os.environ["HOME"] = str(self.home)
        for key in list(os.environ):
            if key.startswith("HARNESS_"):
                del os.environ[key]
        os.environ["HARNESS_QUIET"] = "1"
        self.config = self.home / ".config" / "agent-harness" / "config.json"
        self.settings = self.home / ".claude" / "settings.json"
        self.agents = self.home / ".codex" / "AGENTS.md"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old)
        self.tmp.cleanup()

    def select(self, **stances):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(json.dumps({"stances": {k.replace("_", "-"): v
                                                       for k, v in stances.items()}}))

    def sync(self):
        rc = harness.cmd_sync(harness.argparse.Namespace(
            dry_run=False, adopt=True, adopt_codex=False, print_only=False))
        self.assertEqual(rc, 0)
        return self.snapshot()

    def snapshot(self):
        link = self.home / ".claude" / "rules" / "harness-stances" / "cost.md"
        return {"cost": link.resolve().stem,
                "codex": self.agents.read_text(),
                "settings": json.loads(self.settings.read_text())}

    def assert_projects(self, snap, cost, style):
        self.assertEqual(snap["cost"], cost)
        self.assertIn("<!-- stance cost: " + cost + " -->", snap["codex"])
        self.assertIn("unless `cost` allows it", snap["codex"])
        self.assertEqual(snap["settings"].get("outputStyle"), style)

    def test_frugal_max_frugal_projects_each_selection_and_repeats_idempotently(self):
        for cost, voice, style in (("frugal", "off", None), ("max", "scannable", "Scannable"),
                                   ("frugal", "off", None)):
            with self.subTest(cost=cost, voice=voice):
                self.select(cost=cost, voice=voice)
                first = self.sync()
                self.assert_projects(first, cost, style)
                self.assertEqual(self.sync(), first)
                self.assertEqual(harness._diff_lines(), [])

    def test_a_session_override_is_not_synced_or_persisted(self):
        self.select(cost="balanced", voice="off")
        before = self.config.read_bytes()
        with patch.dict(os.environ, {"HARNESS_STANCE_COST": "max",
                                     "HARNESS_STANCE_VOICE": "scannable"}):
            snap = self.sync()
            self.assertEqual(self.sync(), snap)
        self.assert_projects(snap, "balanced", None)
        self.assertNotIn("<!-- stance cost: max -->", snap["codex"])
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(harness._diff_lines(), [])


if __name__ == "__main__":
    unittest.main()

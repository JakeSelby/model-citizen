# SPDX-License-Identifier: MIT
"""Every session row says which module put which tokens in context, and every decision row which
module's hook made it.

Context attribution is `posture.context_attribution()`: resident text per switched-on module and
per stance, estimated at four characters a token and labelled a soft estimate with that method
(AD-12, AD-23). A decision row names the `hooks/<id>` that owns its point. One module switched
off changes that module's entry alone, and the profile fingerprint with it. Every row is synthetic.

Run: python3 -m unittest discover tests
"""
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


posture = _load("posture_attribution", REPO / "policy" / "hooks" / "posture.py")
usage_log = _load("usage_log_attribution", REPO / "policy" / "hooks" / "usage-log.py")
decisions = _load("decisions_attribution", REPO / "policy" / "hooks" / "decisions.py")
bench = _load("cost_bench_attribution", REPO / "scripts" / "cost_bench.py")
KEY = posture.ATTRIBUTION_KEY
STAMP = "2026-09-24T10:00:00.000Z"
# The refusals that run with every hook off: no hook id owns them.
CONFINEMENT_POINTS = {"evasion-deny", "framework-spawn", "role-confinement", "workflow-launch"}

# One unit to switch off for every switch kind, and the entry it leaves in the attribution: a
# hook keeps no resident text, so it has none. A kind added to the catalog without a line here
# fails `test_every_switch_kind_is_covered`.
# The hook is not core: a core hook switched off without the acknowledgement resolves on.
SWITCHES = {"rules": ("beta", "rules/beta"), "skills": ("gamma", "skills/gamma"),
            "roles": ("delta", "roles/delta"), "workflows": ("epsilon", "workflows/epsilon"),
            "hooks": ("validate-plan-card", None)}

FILES = {
    "VERSION": "1.0.0\n",
    "primitives/rules/alpha.md": "a" * 40,
    "primitives/rules/beta.md": "b" * 400,
    "primitives/skills/gamma/SKILL.md": "---\nname: gamma\ndescription: " + "g" * 93 + "\n---\n" + "body " * 500,
    "primitives/skills/gamma/reference.md": "detail loaded on demand\n",
    "primitives/roles/delta.md": "---\nname: delta\ndescription: " + "d" * 33 + "\n---\n" + "body " * 300,
    "primitives/workflows/epsilon.md": "---\ndescription: " + "e" * 31 + "\n---\n" + "body " * 200,
    "primitives/stances/cost/balanced.md": "c" * 80,
    "primitives/stances/cost/lean.md": "l" * 20,
}


def checkout(root):
    for name, text in FILES.items():
        path = Path(root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    catalog = Path(root) / "lib" / "harness_core" / "catalog.py"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(str(REPO / "lib" / "harness_core" / "catalog.py"), str(catalog))
    return Path(root)


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        self.env = {"HOME": str(self.home), "HARNESS_HOME": str(self.home)}
        patched = patch.dict(os.environ)
        patched.start()
        self.addCleanup(patched.stop)
        for name in [n for n in os.environ if n.startswith("HARNESS_")]:
            del os.environ[name]
        os.environ.update(self.env)
        self.root = checkout(Path(self.tmp.name) / "checkout")
        self.session = Path(self.tmp.name) / "session.json"
        posture._FINGERPRINTS.clear()

    def selected(self, data):
        """The environment of a session whose selection file is `data`."""
        self.session.write_text(json.dumps(data), encoding="utf-8")
        posture._FINGERPRINTS.clear()
        return dict(self.env, HARNESS_SESSION_CONFIG=str(self.session))

    def attribution(self, env=None):
        return posture.context_attribution(env or self.env, root=self.root)

    def fingerprint(self, env=None):
        return posture.fingerprint(env or self.env, root=self.root)


class ContextAttributionTests(Home):
    def test_it_is_labelled_a_soft_estimate_and_names_its_method(self):
        found = self.attribution()
        self.assertEqual(found["estimand"], "soft estimate")
        self.assertIn("chars/4", found["method"])

    def test_each_selected_module_is_attributed_its_resident_text(self):
        # A rule or stance whole; a listed kind its `name: description` entry, never its body.
        self.assertEqual(self.attribution()["modules"], {
            "rules/alpha": 10, "rules/beta": 100, "skills/gamma": 25, "roles/delta": 10,
            "workflows/epsilon": 10, "stances/cost": 20})

    def test_a_stance_is_attributed_its_selected_variant(self):
        lean = self.attribution(dict(self.env, HARNESS_STANCE_COST="lean"))["modules"]
        self.assertEqual(lean["stances/cost"], 5)

    def test_every_switch_kind_is_covered(self):
        kinds = sorted(k for k, e in posture.selection_kinds(self.root).items() if e.get("value") == "switch")
        self.assertEqual(kinds, sorted(SWITCHES))

    def test_one_module_switched_off_changes_only_its_entry_and_the_fingerprint(self):
        # Every unit starts explicitly on, so a hook, which keeps no resident text, still shows
        # its switch in the fingerprint.
        seeded = {kind: {unit: "on"} for kind, (unit, _) in SWITCHES.items()}
        env = self.selected(seeded)
        base, print_base = self.attribution(env)["modules"], self.fingerprint(env)
        for kind, (unit, entry) in sorted(SWITCHES.items()):
            with self.subTest(kind=kind):
                env = self.selected(dict(seeded, **{kind: {unit: "off"}}))
                after = self.attribution(env)["modules"]
                expected = {k: v for k, v in base.items() if k != entry}
                self.assertEqual(after, expected)
                self.assertNotEqual(self.fingerprint(env), print_base)

    def test_a_folded_description_is_attributed_every_line(self):
        skill = self.root / "primitives" / "skills" / "zeta" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: zeta\ndescription: >\n  " + "z" * 20 + "\n  " + "y" * 21
                         + "\nother: value\n---\n" + "body " * 100, encoding="utf-8")
        # "zeta: " plus both lines joined by a space: 6 + 20 + 1 + 21 = 48 characters.
        self.assertEqual(self.attribution()["modules"]["skills/zeta"], 12)

    def test_a_stance_variant_changes_only_its_entry_and_the_fingerprint(self):
        base, env = self.attribution()["modules"], dict(self.env, HARNESS_STANCE_COST="lean")
        after = self.attribution(env)["modules"]
        self.assertEqual({k for k in base if base[k] != after.get(k)}, {"stances/cost"})
        self.assertEqual(set(after), set(base))
        self.assertNotEqual(self.fingerprint(env), self.fingerprint())

    def test_a_unit_a_layer_names_that_nothing_installs_is_not_attributed(self):
        env = self.selected({"rules": {"missing": "on"}})
        self.assertNotIn("rules/missing", self.attribution(env)["modules"])


class UsageRowTests(Home):
    def setUp(self):
        super().setUp()
        usage_log._POSTURE[:] = []

    def transcript(self):
        path = self.home / "s-1.jsonl"
        lines = [{"type": "user", "sessionId": "s-1", "timestamp": STAMP},
                 {"type": "assistant", "sessionId": "s-1", "cwd": "", "timestamp": STAMP,
                  "message": {"id": "m1", "model": "test-model", "content": [],
                              "usage": {"input_tokens": 10, "output_tokens": 20,
                                        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}}]
        path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
        return path

    def test_a_live_session_row_carries_the_attribution_of_the_selection_in_force(self):
        row = usage_log.scan(self.transcript(), "s-1", "")
        self.assertEqual(row[KEY], posture.context_attribution(dict(os.environ)))
        self.assertEqual(row[KEY]["estimand"], "soft estimate")

    def test_a_rescan_keeps_what_the_ledger_holds_and_never_guesses(self):
        known = {"estimand": "soft estimate", "method": "m", "modules": {"rules/alpha": 1}}
        self.assertEqual(usage_log.scan(self.transcript(), "s-1", "", prior={KEY: known}, rescan=True)[KEY], known)
        self.assertNotIn(KEY, usage_log.scan(self.transcript(), "s-1", "", prior={}, rescan=True))

    def test_a_codex_session_row_carries_it_too(self):
        fixture = REPO / "tests" / "fixtures" / "codex" / "session-vscode.jsonl"
        with patch.object(usage_log, "context_attribution", return_value={"modules": {}}):
            self.assertEqual(usage_log.scan(fixture)[KEY], {"modules": {}})

    def test_a_copy_without_its_resolver_leaves_the_field_out(self):
        usage_log._POSTURE[:] = [None]
        self.assertNotIn(KEY, usage_log.scan(self.transcript(), "s-1", ""))


class DecisionRowTests(Home):
    def setUp(self):
        super().setUp()
        decisions._POSTURE[:] = []
        self.target = self.home / "decisions.jsonl"
        self.config = self.home / ".config" / "agent-harness" / "config.json"

    def rows(self):
        return [json.loads(line) for line in self.target.read_text(encoding="utf-8").splitlines()]

    def test_every_answer_kind_names_the_owning_hook(self):
        # A route, allow, deny, ask and injected context (brief-guard's rewrite of a brief). Role
        # confinement runs with every hook off, so its points name no hook.
        cases = [("tier-agent-spawns", "worker-a"), ("workflow-launch", "allow"), ("evasion-deny", "deny"),
                 ("grade-bash", "ask"), ("brief-guard", "cap")]
        for point, answer in cases:
            decisions.record(point, answer, "text", {"session_id": "s-1"}, target=self.target)
        self.assertEqual([r["module"] for r in self.rows()],
                         ["hooks/tier-agent-spawns", None, None, "hooks/grade-bash", "hooks/brief-guard"])

    def test_an_outcome_and_a_sampled_allow_name_it_too(self):
        identity = decisions.record("stop-gate", "blocked", "t", {"session_id": "s-1"}, target=self.target)
        decisions.observe(identity, "ran", "stop-gate", "s-1", target=self.target)
        decisions.record_allowed("ls", {"session_id": "s-1"}, target=self.target,
                                 cfg={"telemetry": {"allow_sample_rate": 1}})
        self.assertEqual({r["module"] for r in self.rows()}, {"hooks/stop-gate", "hooks/grade-bash"})

    def test_a_point_no_hook_owns_names_null_rather_than_a_guess(self):
        decisions.record("decision-provider", "allow", "t", target=self.target)
        decisions.record("never-heard-of", "allow", "t", target=self.target)
        self.assertEqual([r["module"] for r in self.rows()], [None, None])

    def test_every_hook_point_a_call_site_writes_has_an_owner(self):
        # A literal point at a call site, or a module constant naming one, as `WORKFLOW_POINT`.
        pattern = re.compile(r"""\.record\(\s*["']([a-z-]+)["']|^[A-Z_]*POINT = ["']([a-z-]+)["']""", re.M)
        sources = [REPO / "lib" / "harness_core" / "lifecycle.py"] + sorted((REPO / "policy" / "hooks").glob("*.py"))
        points = set(decisions.POINTS)
        for source in sources:
            points.update(a or b for a, b in pattern.findall(source.read_text(encoding="utf-8")))
        self.assertIn("workflow-launch", points)
        # Role confinement has no hook id, because no switch turns it off, so it names no owner.
        missing = sorted(p for p in points if decisions.module_of(p) is None)
        self.assertEqual(missing, sorted(CONFINEMENT_POINTS))
        owners = {decisions.module_of(p).split("/", 1)[1] for p in points - CONFINEMENT_POINTS}
        for owner in owners:
            self.assertTrue((REPO / "policy" / "hooks" / (owner + ".py")).is_file(), owner)


class ReplayTests(Home):
    def test_the_bare_arm_attributes_nothing_and_the_harness_arm_its_profile(self):
        opts = {"home": self.home, "profile_root": self.root}
        bare = bench.arm_attribution("bare", bench.arm_env("bare"), opts)
        self.assertEqual(bare, {"estimand": "soft estimate", "method": posture.ATTRIBUTION_METHOD, "modules": {}})
        env = bench.arm_env("harness")
        self.assertEqual(bench.arm_attribution("harness", env, opts),
                         posture.context_attribution(dict(env, HOME=str(self.home)), root=self.root))

    def test_the_same_task_replayed_with_one_module_off_changes_only_that_entry(self):
        opts = {"home": self.home, "profile_root": self.root}
        env = bench.arm_env("harness")
        before = bench.arm_attribution("harness", env, opts)["modules"]
        profile = bench.arm_profile("harness", env, opts)
        off = dict(env, HARNESS_SESSION_CONFIG=str(self.session))
        self.selected({"rules": {"alpha": "off"}})
        after = bench.arm_attribution("harness", off, opts)["modules"]
        self.assertEqual(after, {k: v for k, v in before.items() if k != "rules/alpha"})
        self.assertNotEqual(bench.arm_profile("harness", off, opts), profile)


if __name__ == "__main__":
    unittest.main()

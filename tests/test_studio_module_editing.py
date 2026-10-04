# SPDX-License-Identifier: MIT
"""Draft module editing identity, preview, conflict, and parity behavior."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from harness_core.studio import drafts, module_editing, module_evaluator, selection, server

import draft_support  # noqa: E402


class FakeHarness:
    ALWAYS_LOADED_CAP = 225
    ALWAYS_LOADED_TOKEN_CAP = 5000

    @staticmethod
    def lint_tree(root):
        text = (root / "custom" / "rules" / "sample.md").read_text(encoding="utf-8")
        return (["custom/rules/sample.md:2: secret pattern test-secret"]
                if "test-secret" in text else [])

    @staticmethod
    def check_context_cap(_root):
        return []

    @staticmethod
    def check_cost_sidecars(_root):
        return []

    @staticmethod
    def check_stance_constraints(_root):
        return []

    @staticmethod
    def check_collisions(_root, _config=None):
        return []

    @staticmethod
    def always_loaded_lines(_root):
        return 10, []

    @staticmethod
    def always_loaded_tokens(_root):
        return 40, []

    @staticmethod
    def always_loaded_groups(_root):
        return [("base", 10, 160)]

    @staticmethod
    def est_tokens(chars):
        return round(chars / 4)

    @staticmethod
    def load_posture():
        return SimpleNamespace(selection=lambda *_args, **kwargs: {
            "rules": {}, "skills": {}, "roles": {}, "workflows": {},
            "stances": dict(kwargs.get("config", {}).get("stances", {})),
        })

    @staticmethod
    def switched_off(_selected):
        return {kind: set() for kind in ("rules", "skills", "roles", "workflows")}

    @staticmethod
    def resolve_stances(config):
        root = Path(config["primitive_roots"][0])
        return {
            name: root / "stances" / name / (variant + ".md")
            for name, variant in config.get("stances", {}).items()
            if (root / "stances" / name / (variant + ".md")).is_file()
        }

    @staticmethod
    def external_primitive_roots(config):
        root = Path(config["primitive_roots"][0])
        return ([{"path": root, "slug": "custom",
                  "rules": sorted((root / "rules").glob("*.md")), "skills": []}], [])

    @staticmethod
    def render_personal(_config, _existing):
        return "Personal projection.\n"

    @staticmethod
    def render_codex_agents(_stances, personal, externals, _off):
        rules = [path.read_text(encoding="utf-8")
                 for entry in externals for path in entry["rules"]]
        stances = [path.read_text(encoding="utf-8") for path in _stances.values()]
        return "Codex aggregate.\n" + personal + "".join(rules + stances)


class ModuleEditingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.worktree = Path(self.temporary.name) / "draft"
        self.source = self.worktree / "custom" / "rules" / "sample.md"
        self.source.parent.mkdir(parents=True)
        self.source.write_text("# Sample\n\nClean text.\n", encoding="utf-8")
        (self.worktree / "bin").mkdir()
        (self.worktree / "bin" / "harness").write_text("# placeholder\n", encoding="utf-8")
        self.state = {"name": "draft-one", "revision": "a" * 40}
        self.item = {
            "key": "root-1:rules:sample", "name": "sample", "kind": "rules",
            "root": {"id": "root-1", "label": "custom", "path": str(self.source.parent.parent),
                     "core": False},
            "source": {"path": str(self.source), "text": self.source.read_text(encoding="utf-8")},
            "projections": [{"runtime": "claude-code", "path": "~/.claude/rules/sample.md"},
                            {"runtime": "codex", "path": "~/.codex/AGENTS.md (aggregated)"}],
            "context_cost": {"tokens": 5, "estimate": "soft estimate", "method": "chars/4"},
        }

    def _copy_for_test(self, worktree, _revision, relative, content):
        temporary = tempfile.TemporaryDirectory()
        target = Path(temporary.name) / "candidate"
        shutil.copytree(worktree, target)
        (target / relative).write_bytes(content)
        return temporary, target

    def _evaluate_for_test(self, candidate, config, item, _aliases, diagnostics, _environment=None):
        harness = module_editing.selection._harness_module(candidate)
        rendered = module_editing._runtime_renderings(harness, candidate, config)
        runtime = {name: {"lines": len(text.splitlines()),
                          "tokens": harness.est_tokens(len(text))}
                   for name, text in rendered.items()}
        lines = harness.always_loaded_lines(candidate)[0]
        tokens = harness.always_loaded_tokens(candidate)[0]
        source = (candidate / item["_relative"]).read_text(encoding="utf-8")
        posture = harness.load_posture()
        selected = posture.selection({}, strict=True, config=config, root=candidate)
        off = harness.switched_off(selected)
        if item["kind"] == "stances":
            dimension, variant = item["name"].split("/", 1)
            active = selected.get("stances", {}).get(dimension) == variant
        else:
            active = item["name"] not in off.get(item["kind"], set())
        return {
            "ok": True, "authored": {"lines": lines, "tokens": tokens},
            "rendered": runtime, "named": {name: source if active else "" for name in runtime},
            "diagnostics": (module_editing._diagnostics(
                harness, candidate, item["_relative"], config,
            ) if diagnostics else []),
            "caps": {"lines": harness.ALWAYS_LOADED_CAP,
                     "tokens": harness.ALWAYS_LOADED_TOKEN_CAP},
        }

    def patches(self):
        @contextmanager
        def locked(*_args, **_kwargs):
            yield self.worktree, self.state, {
                "primitive_roots": [str(self.worktree / "custom")],
            }

        @contextmanager
        def unlocked(*_args, **_kwargs):
            yield

        @contextmanager
        def draft_state():
            with mock.patch.object(module_editing.drafts, "locked_context", side_effect=locked), \
                    mock.patch.object(module_editing.drafts, "request_lock", side_effect=unlocked):
                yield

        return (
            draft_state(),
            mock.patch.object(module_editing.selection, "_harness_module",
                              return_value=FakeHarness),
            mock.patch.object(module_editing, "_copy_candidate", side_effect=self._copy_for_test),
            mock.patch.object(module_editing, "_evaluate_candidate",
                              side_effect=self._evaluate_for_test),
        )

    def test_secret_preview_marks_the_exact_line_without_touching_the_draft(self):
        original = self.source.read_bytes()
        first, second, third, fourth = self.patches()
        with first, second, third, fourth:
            result = module_editing.preview(
                self.worktree, "draft-one", self.item["key"], "# Sample\ntest-secret\n",
            )
        self.assertFalse(result["valid"])
        self.assertEqual(result["diagnostics"][0]["line"], 2)
        self.assertIn("secret pattern", result["diagnostics"][0]["message"])
        self.assertEqual(self.source.read_bytes(), original)
        self.assertTrue(result["nothing_applied"])

    def test_preview_names_both_runtime_caps_and_current_deltas(self):
        candidate = "# Sample\nMore text.\n"
        first, second, third, fourth = self.patches()
        with first, second, third, fourth:
            result = module_editing.preview(
                self.worktree, "draft-one", self.item["key"], candidate,
            )
        self.assertTrue(result["valid"])
        self.assertEqual([row["label"] for row in result["budgets"]], ["Claude Code", "Codex"])
        self.assertTrue(all(row["token_cap"] == 5000 for row in result["budgets"]))
        self.assertTrue(all("token_delta" in row for row in result["budgets"]))
        self.assertTrue(all("rendered_tokens" in row for row in result["budgets"]))
        projections = {row["runtime"]: row for row in result["projections"]}
        self.assertEqual(projections["claude-code"]["text"], candidate)
        self.assertEqual(projections["codex"]["text"], candidate)

    def test_runtime_aggregates_are_measured_separately_from_sync_inputs(self):
        (self.worktree / "claude" / "rules").mkdir(parents=True)
        (self.worktree / "primitives" / "rules").mkdir(parents=True)
        (self.worktree / "claude" / "CLAUDE.md").write_text("Claude instructions.\n", encoding="utf-8")
        (self.worktree / "claude" / "rules" / "core.md").write_text("Claude rule.\n", encoding="utf-8")
        (self.worktree / "primitives" / "instructions.md").write_text(
            "Codex instructions are deliberately longer.\n", encoding="utf-8",
        )
        (self.worktree / "primitives" / "rules" / "core.md").write_text(
            "Codex rule differs.\n", encoding="utf-8",
        )
        config = {"primitive_roots": [str(self.worktree / "custom")]}
        sizes = module_editing._runtime_sizes(FakeHarness, self.worktree, config)
        self.assertNotEqual(sizes["claude-code"], sizes["codex"])
        claude_text = ("Claude instructions.\nClaude rule.\n# Sample\n\nClean text.\n"
                       "Personal projection.\n")
        codex_text = ("Codex aggregate.\nPersonal projection.\n"
                      "# Sample\n\nClean text.\n")
        self.assertEqual(sizes["claude-code"],
                         (len(claude_text.splitlines()), FakeHarness.est_tokens(len(claude_text))))
        self.assertEqual(sizes["codex"],
                         (len(codex_text.splitlines()), FakeHarness.est_tokens(len(codex_text))))

    def test_skill_projection_is_the_exact_projected_file_for_both_runtimes(self):
        source = self.worktree / "custom" / "skills" / "sample" / "SKILL.md"
        source.parent.mkdir(parents=True)
        source.write_text("---\nname: sample\n---\n\nOld.\n", encoding="utf-8")
        item = dict(self.item, key="root-1:skills:sample", kind="skills", name="sample")
        item["source"] = {"path": str(source), "text": source.read_text(encoding="utf-8")}
        item["projections"] = [
            {"runtime": "claude-code", "path": "~/.claude/skills/sample/SKILL.md"},
            {"runtime": "codex", "path": "~/.agents/skills/sample/SKILL.md"},
        ]
        candidate = "---\nname: sample\n---\n\nNew exact skill.\n"
        @contextmanager
        def locked(*_args, **_kwargs):
            yield self.worktree, self.state, {
                "primitive_roots": [str(self.worktree / "custom")],
            }
        first = mock.patch.object(module_editing.drafts, "locked_context", side_effect=locked)
        second = mock.patch.object(module_editing.selection, "_harness_module", return_value=FakeHarness)
        third = mock.patch.object(module_editing, "_evaluate_candidate",
                                  side_effect=self._evaluate_for_test)
        with first, second, third, mock.patch.object(
                module_editing, "_copy_candidate", side_effect=self._copy_for_test):
            result = module_editing.preview(self.worktree, "draft-one", item["key"], candidate)
        self.assertTrue(result["valid"])
        self.assertEqual(
            {row["runtime"]: row["text"] for row in result["projections"]},
            {"claude-code": candidate, "codex": candidate},
        )

    def test_selected_stance_uses_its_exact_named_projection(self):
        source = self.worktree / "custom" / "stances" / "voice" / "concise.md"
        source.parent.mkdir(parents=True)
        source.write_text("Old stance.\n", encoding="utf-8")
        item = dict(self.item, key="root-1:stances:voice/concise",
                    kind="stances", name="voice/concise")
        item["source"] = {"path": str(source), "text": source.read_text(encoding="utf-8")}
        item["projections"] = [
            {"runtime": "claude-code", "path": "~/.claude/rules/harness-stances/voice.md"},
            {"runtime": "codex", "path": "~/.codex/AGENTS.md (aggregated)"},
        ]
        config = {
            "primitive_roots": [str(self.worktree / "custom")],
            "stances": {"voice": "concise"},
        }
        @contextmanager
        def locked(*_args, **_kwargs):
            yield self.worktree, self.state, config
        first = mock.patch.object(module_editing.drafts, "locked_context", side_effect=locked)
        second = mock.patch.object(module_editing.selection, "_harness_module", return_value=FakeHarness)
        third = mock.patch.object(module_editing, "_evaluate_candidate",
                                  side_effect=self._evaluate_for_test)
        candidate = "Exact candidate stance.\n"
        with first, second, third, mock.patch.object(
                module_editing, "_copy_candidate", side_effect=self._copy_for_test):
            result = module_editing.preview(self.worktree, "draft-one", item["key"], candidate)
        projections = {row["runtime"]: row["text"] for row in result["projections"]}
        self.assertEqual(projections, {"claude-code": candidate, "codex": candidate})

    def test_over_cap_candidate_is_refused_with_each_named_runtime_and_exact_cap(self):
        first, second, third, fourth = self.patches()
        with first, second, third, fourth, mock.patch.object(
                FakeHarness, "always_loaded_lines", return_value=(226, [])):
            result = module_editing.preview(
                self.worktree, "draft-one", self.item["key"], "\n".join(["line"] * 230),
            )
        self.assertFalse(result["valid"])
        self.assertTrue(all(row["over_cap"] for row in result["budgets"]), result)
        self.assertEqual([(row["label"], row["line_cap"]) for row in result["budgets"]],
                         [("Claude Code", 225), ("Codex", 225)])

    def test_token_only_overflow_is_refused_below_the_line_cap(self):
        candidate = "x" * 21000 + "\n"
        first, second, third, fourth = self.patches()
        with first, second, third, fourth, \
                mock.patch.object(FakeHarness, "always_loaded_lines", return_value=(10, [])), \
                mock.patch.object(FakeHarness, "always_loaded_tokens", return_value=(5001, [])):
            result = module_editing.preview(
                self.worktree, "draft-one", self.item["key"], candidate,
            )
        self.assertTrue(all(row["lines"] < row["line_cap"] for row in result["budgets"]))
        self.assertTrue(all(row["tokens"] > row["token_cap"] for row in result["budgets"]))
        self.assertTrue(all(row["over_cap"] for row in result["budgets"]), result)

    def test_save_refuses_an_over_cap_candidate_before_checkpoint(self):
        source_digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        first, second, third, fourth = self.patches()
        with first, second, third, fourth, \
                mock.patch.object(FakeHarness, "always_loaded_lines", return_value=(226, [])), \
                mock.patch.object(module_editing.drafts, "replay_request_response", return_value=None), \
                mock.patch.object(module_editing.drafts, "checkpoint") as checkpointed:
            result = module_editing.save(
                self.worktree, "draft-one", self.item["key"], self.state["revision"],
                source_digest, "over-cap-save", "\n".join(["line"] * 230),
            )
        self.assertFalse(result["saved"])
        self.assertEqual(result["error_code"], "lint-refused")
        checkpointed.assert_not_called()

    def test_concurrent_exact_retry_returns_the_durable_canonical_response(self):
        source_digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        canonical = {"valid": True, "saved": True, "content_digest": "durable",
                     "budgets": [{"label": "durable"}],
                     "result": {"revision": "b" * 40, "replayed": True}}
        first, second, third, fourth = self.patches()
        with first, second, third, fourth, \
                mock.patch.object(module_editing.drafts, "replay_request_response",
                               side_effect=[None, canonical]) as replayed, \
                mock.patch.object(module_editing.drafts, "checkpoint",
                                  return_value={"revision": "b" * 40, "replayed": True}):
            result = module_editing.save(
                self.worktree, "draft-one", self.item["key"], self.state["revision"],
                source_digest, "concurrent-key", "candidate\n",
            )
        self.assertEqual(result, canonical)
        self.assertEqual(replayed.call_count, 2)

    def test_two_concurrent_public_exact_saves_share_one_durable_response(self):
        source_digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        barrier = threading.Barrier(3)
        durable = {}
        results = []
        previews = 0
        original_snapshot = module_editing._preview_snapshot

        def replay(_repo, _name, _key, _identity):
            return durable.get("response")

        def checkpoint(_repo, _name, _revision, _key, **kwargs):
            time.sleep(0.1)
            result = {"revision": "b" * 40, "replayed": False}
            response = dict(kwargs["canonical_response"])
            response["result"] = result
            durable["response"] = response
            return result

        def snapshot(*args, **kwargs):
            nonlocal previews
            previews += 1
            return original_snapshot(*args, **kwargs)

        def invoke():
            barrier.wait()
            results.append(module_editing.save(
                self.worktree, "draft-one", self.item["key"], self.state["revision"],
                source_digest, "same-key", "# Sample\nConcurrent candidate.\n",
            ))

        first, second, third, fourth = self.patches()
        with first, second, third, fourth, \
                mock.patch.object(module_editing.drafts, "replay_request_response",
                                  side_effect=replay), \
                mock.patch.object(module_editing.drafts, "checkpoint", side_effect=checkpoint), \
                mock.patch.object(module_editing, "_preview_snapshot", side_effect=snapshot):
            threads = [threading.Thread(target=invoke) for _ in range(2)]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(5)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])
        self.assertEqual(previews, 1)

    def test_unselected_stance_variant_has_no_projection_or_budget_delta(self):
        concise = self.worktree / "custom" / "stances" / "voice" / "concise.md"
        detailed = concise.with_name("detailed.md")
        concise.parent.mkdir(parents=True)
        concise.write_text("Concise old.\n", encoding="utf-8")
        detailed.write_text("Selected detailed.\n", encoding="utf-8")
        item = dict(self.item, key="root-1:stances:voice/concise",
                    kind="stances", name="voice/concise")
        item["source"] = {"path": str(concise), "text": concise.read_text()}
        item["projections"] = [
            {"runtime": "claude-code", "path": "claude"},
            {"runtime": "codex", "path": "codex"},
        ]

        @contextmanager
        def locked(*_args, **_kwargs):
            yield self.worktree, self.state, {
                "primitive_roots": [str(self.worktree / "custom")],
                "stances": {"voice": "detailed"},
            }

        with mock.patch.object(module_editing.drafts, "locked_context", side_effect=locked), \
                mock.patch.object(module_editing.selection, "_harness_module",
                                  return_value=FakeHarness), \
                mock.patch.object(module_editing, "_copy_candidate",
                                  side_effect=self._copy_for_test), \
                mock.patch.object(module_editing, "_evaluate_candidate",
                                  side_effect=self._evaluate_for_test):
            result = module_editing.preview(
                self.worktree, "draft-one", item["key"], "Concise candidate.\n",
            )
        self.assertTrue(result["valid"])
        self.assertEqual({row["runtime"]: row["text"] for row in result["projections"]},
                         {"claude-code": "", "codex": ""})
        self.assertTrue(all(row["line_delta"] == 0 and row["token_delta"] == 0
                            for row in result["budgets"]))

    def test_preview_uses_locked_config_for_collision_lint(self):
        seen = []

        class CollisionHarness(FakeHarness):
            @staticmethod
            def check_collisions(_root, config=None):
                seen.append(config)
                return ["collision from draft config"]

        first, second, third, fourth = self.patches()
        with first, second, mock.patch.object(
                module_editing.selection, "_harness_module", return_value=CollisionHarness,
        ), third, fourth:
            result = module_editing.preview(
                self.worktree, "draft-one", self.item["key"], "# Candidate\n",
            )
        self.assertFalse(result["valid"])
        self.assertEqual(result["diagnostics"][0]["message"], "collision from draft config")
        self.assertEqual(len(seen[0]["primitive_roots"]), 1)
        self.assertEqual(Path(seen[0]["primitive_roots"][0]).name, "custom")

    def test_non_list_primitive_roots_is_a_stable_invalid_config_refusal(self):
        @contextmanager
        def locked(*_args, **_kwargs):
            yield self.worktree, self.state, {"primitive_roots": "not-a-list"}

        with mock.patch.object(module_editing.drafts, "locked_context", side_effect=locked):
            result = module_editing.read(self.worktree, "draft-one", self.item["key"])
        self.assertEqual(result["error_code"], "invalid-config")

    def test_disabled_rule_and_skill_have_empty_runtime_projections(self):
        for kind, source, item in (
            ("rules", self.source, self.item),
            ("skills", self.worktree / "custom" / "skills" / "sample" / "SKILL.md",
             dict(self.item, key="root-1:skills:sample", kind="skills")),
        ):
            with self.subTest(kind=kind):
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text("---\nname: sample\n---\n" if kind == "skills" else "# Sample\n",
                                  encoding="utf-8")
                current = dict(item)
                current["source"] = {"path": str(source), "text": source.read_text()}
                if kind == "skills":
                    current["projections"] = [
                        {"runtime": "claude-code", "path": "claude"},
                        {"runtime": "codex", "path": "codex"},
                    ]
                @contextmanager
                def locked(*_args, **_kwargs):
                    yield self.worktree, self.state, {
                        "primitive_roots": [str(self.worktree / "custom")],
                    }
                with mock.patch.object(module_editing.drafts, "locked_context", side_effect=locked), \
                        mock.patch.object(module_editing.selection, "_harness_module",
                                          return_value=FakeHarness), \
                        mock.patch.object(FakeHarness, "switched_off", return_value={
                            "rules": {"sample"}, "skills": {"sample"},
                            "roles": set(), "workflows": set(),
                        }), \
                        mock.patch.object(module_editing, "_copy_candidate",
                                          side_effect=self._copy_for_test), \
                        mock.patch.object(module_editing, "_evaluate_candidate",
                                          side_effect=self._evaluate_for_test):
                    result = module_editing.preview(
                        self.worktree, "draft-one", current["key"], source.read_text() + "Changed\n",
                    )
                projections = {row["runtime"]: row["text"] for row in result["projections"]}
                self.assertEqual(projections, {"claude-code": "", "codex": ""})

    def test_core_external_and_symlink_modules_are_not_editable(self):
        linked = dict(self.item)
        linked["key"] = "root-1:rules:linked"
        link = self.source.with_name("linked.md")
        link.symlink_to(self.source)
        linked["source"] = {"path": str(link), "text": ""}
        core = dict(self.item)
        core["key"] = "core:rules:sample"
        core["root"] = dict(self.item["root"], core=True)
        outside = dict(self.item)
        outside["key"] = "root-2:rules:outside"
        outside["root"] = dict(self.item["root"], path=str(Path(self.temporary.name) / "outside"))
        @contextmanager
        def locked(*_args, **_kwargs):
            yield self.worktree, self.state, {
                "primitive_roots": [str(self.worktree / "custom"),
                                    str(Path(self.temporary.name) / "outside")],
            }
        with mock.patch.object(module_editing.drafts, "locked_context", side_effect=locked):
            self.assertEqual([row["key"] for row in module_editing.list_modules(
                self.worktree, "draft-one")], [self.item["key"]])

    def test_core_root_alias_is_canonicalized_once_and_never_becomes_editable(self):
        core = self.worktree / "primitives"
        core.mkdir()
        alias = self.worktree / "core-alias"
        alias.symlink_to(core, target_is_directory=True)
        roots = module_editing.module_library._roots(self.worktree, {
            "primitive_roots": [str(core), str(alias)],
        })
        self.assertEqual([(item["id"], item["core"]) for item in roots], [("core", True)])

    def test_busy_read_and_timed_out_preview_are_structured_refusals(self):
        @contextmanager
        def busy(*_args, **_kwargs):
            raise drafts.DraftError("busy", "another writer is changing this draft")
            yield
        with mock.patch.object(module_editing.drafts, "locked_context", side_effect=busy):
            loaded = module_editing.read(self.worktree, "draft-one", self.item["key"])
        self.assertEqual((loaded["status"], loaded["error_code"]), ("unavailable", "busy"))

        first, second, third, _fourth = self.patches()
        with first, second, third, mock.patch.object(
                module_editing.subprocess, "run",
                side_effect=subprocess.TimeoutExpired("git clone", 120)):
            preview = module_editing.preview(
                self.worktree, "draft-one", self.item["key"], "candidate",
            )
        self.assertEqual(preview["error_code"], "preview-timeout")

    def test_deleted_source_after_load_is_a_stale_source_refusal(self):
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.source.unlink()
        first, _second, _third, _fourth = self.patches()
        with first, mock.patch.object(
                module_editing.drafts, "replay_request_response", return_value=None):
            result = module_editing.save(
                self.worktree, "draft-one", self.item["key"], self.state["revision"],
                digest, "deleted-source", "retained editor buffer",
            )
        self.assertEqual(result["error_code"], "stale-source")

    def test_clean_save_uses_revision_and_source_digest_guards_and_reports_cli_parity(self):
        source_digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        first, second, third, fourth = self.patches()
        with first, second, third, fourth, \
                mock.patch.object(module_editing.drafts, "replay_request_response", return_value=None), \
                mock.patch.object(module_editing.drafts, "checkpoint",
                                  return_value={"revision": "b" * 40, "replayed": False}) as saved:
            result = module_editing.save(
                self.worktree, "draft-one", self.item["key"], "a" * 40,
                source_digest, "save-one", "# Sample\nChanged.\n",
            )
        self.assertTrue(result["saved"], result["error"])
        self.assertEqual(result["saved_lint"], [])
        self.assertEqual(saved.call_args.kwargs["expected_digests"], {
            "custom/rules/sample.md": source_digest,
        })
        self.assertEqual(saved.call_args.args[2:4], ("a" * 40, "save-one"))
        self.assertTrue(saved.call_args.kwargs["canonical_response"]["saved"])

    def test_routes_are_authenticated_post_contracts_with_cli_equivalents(self):
        for path, operation in (("/api/configure/module/read", "read"),
                                ("/api/configure/module/preview", "preview"),
                                ("/api/configure/module/save", "save")):
            route = server.ROUTES.resolve("POST", path)
            self.assertIsNotNone(route)
            self.assertEqual(route.request_media_type, "application/json")
            self.assertEqual(route.cli_command, module_editing.CLI_COMMANDS[operation])

    def test_shared_cli_lint_reports_broken_local_markdown_links_on_the_source_line(self):
        harness = selection._harness_module(ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "rules" / "sample.md"
            source.parent.mkdir()
            source.write_text("# Sample\n\nSee [missing](../skills/missing/SKILL.md).\n", encoding="utf-8")
            findings = harness.lint_files(root, [source], terms=([], []))
        self.assertIn("rules/sample.md:3: broken local link ../skills/missing/SKILL.md", findings)

    def test_markdown_link_titles_are_not_part_of_the_local_destination(self):
        harness = selection._harness_module(ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "shared.md"
            target.write_text("Target.\n", encoding="utf-8")
            paths = (
                root / "rules" / "sample.md",
                root / "skills" / "sample" / "SKILL.md",
                root / "stances" / "voice" / "sample.md",
            )
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                relative = os.path.relpath(target, path.parent)
                path.write_text("See [target](%s \"Helpful title\").\n" % relative, encoding="utf-8")
            findings = harness.lint_files(root, list(paths), terms=([], []))
        self.assertFalse([finding for finding in findings if "broken local link" in finding])

    def test_markdown_destinations_cover_balanced_parentheses_uri_schemes_and_all_kinds(self):
        harness = selection._harness_module(ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "shared (v2).md"
            target.write_text("Target.\n", encoding="utf-8")
            paths = (
                root / "rules" / "sample.md",
                root / "skills" / "sample" / "SKILL.md",
                root / "stances" / "voice" / "sample.md",
            )
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                relative = os.path.relpath(target, path.parent)
                path.write_text(
                    ("See [balanced](%s), [query](%s?mode=1#section), "
                     "[phone](tel:+15551212), and [missing](missing.md).\n"
                     "[reference][shared] and [missing reference][gone].\n"
                     "[nested [label]](nested-missing.md).\n"
                     "[multiline](\n  multiline-missing.md\n  \"title\").\n"
                     "[escaped space](%s).\n"
                     "[nul](bad%%00path.md).\n"
                     "[shared]: %s\n[gone]: reference-missing.md\n"
                     "\\[escaped](ignored.md), ![image][gone], and `[inline](ignored.md)`.\n"
                     "    [indented](ignored.md)\n"
                     "\t[tab-indented](ignored.md)\n"
                     "```md\n[fenced](ignored.md)\n```\n")
                    % (relative.replace(" ", "%20"), relative.replace(" ", "%20"),
                       relative.replace(" ", "\\ "), relative.replace(" ", "%20")),
                    encoding="utf-8",
                )
            findings = harness.lint_files(root, list(paths), terms=([], []))
        broken = [finding for finding in findings if "broken local link" in finding]
        self.assertEqual(len(broken), 15)
        self.assertTrue(any("rules/sample.md" in finding for finding in broken))
        self.assertTrue(any("skills/sample/SKILL.md" in finding for finding in broken))
        self.assertTrue(any("stances/voice/sample.md" in finding for finding in broken))

    def test_markdown_duplicate_references_nested_fences_and_malformed_urls_are_stable(self):
        harness = selection._harness_module(ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "rules" / "sample.md"
            source.parent.mkdir()
            (source.parent / "first.md").write_text("Target.\n", encoding="utf-8")
            source.write_text(
                ("[first][same]\n"
                 "[same]: first.md\n"
                 "[same]: missing.md\n"
                 "> ```md\n> [quoted fence](ignored.md)\n> ```\n"
                 "- ~~~md\n  [listed fence](ignored.md)\n  ~~~\n"
                 "[malformed](http://[broken)\n"),
                encoding="utf-8",
            )
            findings = harness.lint_files(root, [source], terms=([], []))
        broken = [finding for finding in findings if "broken local link" in finding]
        self.assertEqual(len(broken), 1)
        self.assertIn("http://[broken", broken[0])

    def test_configured_draft_root_uses_the_public_authored_cap_measurement(self):
        harness = selection._harness_module(ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            external = root / "draft-owned"
            (external / "rules").mkdir(parents=True)
            (external / "rules" / "large.md").write_text(
                "\n".join("line" for _ in range(harness.ALWAYS_LOADED_CAP + 1)) + "\n",
                encoding="utf-8",
            )
            findings = harness.check_context_cap(root, {"primitive_roots": [str(external)]})
            lines = harness.always_loaded_lines(root, {"primitive_roots": [str(external)]})[0]
        self.assertEqual(lines, harness.ALWAYS_LOADED_CAP + 1)
        self.assertTrue(any("over the %d-line cap" % harness.ALWAYS_LOADED_CAP in row
                            for row in findings))

    def test_source_read_boundary_is_exact_and_over_limit_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.md"
            source.write_bytes(b"x" * module_editing.MAX_SOURCE_BYTES)
            text, _digest = module_editing._regular_text(source)
            self.assertEqual(len(text), module_editing.MAX_SOURCE_BYTES)
            source.write_bytes(b"x" * (module_editing.MAX_SOURCE_BYTES + 1))
            with self.assertRaises(module_editing.ModuleEditError) as refused:
                module_editing._regular_text(source)
            self.assertEqual(refused.exception.code, "module-too-large")

    def test_public_preview_refuses_an_ancestor_symlink_swap_without_reading_outside(self):
        outside = Path(self.temporary.name) / "outside-rules"
        outside.mkdir()
        (outside / "sample.md").write_text("OUTSIDE SENTINEL\n", encoding="utf-8")
        rules = self.source.parent
        held = rules.with_name("held-rules")
        original_safe_target = drafts._safe_target
        swapped = False

        def swap_after_validation(worktree, relative, allow_leaf_symlink=False):
            nonlocal swapped
            target = original_safe_target(worktree, relative, allow_leaf_symlink)
            if not swapped:
                swapped = True
                rules.rename(held)
                rules.symlink_to(outside, target_is_directory=True)
            return target

        first, second, third, fourth = self.patches()
        with first, second, third, fourth, mock.patch.object(
                module_editing.drafts, "_safe_target", side_effect=swap_after_validation):
            result = module_editing.preview(
                self.worktree, "draft-one", self.item["key"], "# Candidate\n",
            )
        self.assertEqual(result["error_code"], "stale-source")
        self.assertNotIn("OUTSIDE SENTINEL", json.dumps(result))

    def test_projection_text_is_bounded_and_discloses_truncation(self):
        candidate = "x" * (module_editing.MAX_PROJECTION_CHARS + 50)
        first, second, third, fourth = self.patches()
        with first, second, third, fourth:
            result = module_editing.preview(
                self.worktree, "draft-one", self.item["key"], candidate,
            )
        claude = next(row for row in result["projections"] if row["runtime"] == "claude-code")
        self.assertEqual(len(claude["text"]), module_editing.MAX_PROJECTION_CHARS)
        self.assertTrue(claude["truncated"])

    def test_latest_preview_supersedes_the_queued_request_and_cleans_registry(self):
        started = threading.Event()
        release = threading.Event()
        ran = []
        results = {}

        def operation(label):
            ran.append(label)
            if label == "first":
                started.set()
                release.wait(5)
            return {"label": label}

        def invoke(label):
            try:
                results[label] = module_editing._latest_preview(
                    "bounded", lambda: operation(label),
                )
            except module_editing.ModuleEditError as exc:
                results[label] = exc.code

        threads = [threading.Thread(target=invoke, args=(label,))
                   for label in ("first", "second", "third")]
        threads[0].start()
        self.assertTrue(started.wait(2))
        threads[1].start()
        time.sleep(0.05)
        threads[2].start()
        time.sleep(0.05)
        release.set()
        for thread in threads:
            thread.join(5)
        self.assertEqual(ran, ["first", "third"])
        self.assertEqual(results["second"], "preview-superseded")
        self.assertNotIn("bounded", module_editing._PREVIEW_STATES)

    def test_three_public_previews_run_only_the_first_and_newest_request(self):
        started = threading.Event()
        release = threading.Event()
        ran = []
        results = {}

        def evaluate(_repo, _name, _key, content):
            ran.append(content)
            if content == "first":
                started.set()
                release.wait(5)
            return {
                "valid": True, "error": "", "error_code": "", "base_revision": "a" * 40,
                "source_digest": "source", "content_digest": content, "unchanged": False,
                "module": {}, "diagnostics": [], "budgets": [], "projections": [],
                "nothing_applied": True, "_relative": "rules/sample.md",
                "_content": content.encode(), "_config": {},
            }

        def invoke(content):
            results[content] = module_editing.preview(
                self.worktree, "public-bounded", self.item["key"], content,
            )

        with mock.patch.object(module_editing, "_preview", side_effect=evaluate):
            threads = [threading.Thread(target=invoke, args=(content,))
                       for content in ("first", "second", "third")]
            threads[0].start()
            self.assertTrue(started.wait(2))
            threads[1].start()
            time.sleep(0.05)
            threads[2].start()
            time.sleep(0.05)
            release.set()
            for thread in threads:
                thread.join(5)
        self.assertEqual(ran, ["first", "third"])
        self.assertEqual(results["second"]["error_code"], "preview-superseded")
        self.assertEqual(results["third"]["content_digest"], "third")
        self.assertNotIn("public-bounded", module_editing._PREVIEW_STATES)

    def test_public_previews_are_globally_bounded_across_drafts(self):
        guard = threading.Lock()
        active = 0
        maximum = 0
        release = threading.Event()

        def evaluate(_repo, _name, _key, content):
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
            release.wait(5)
            with guard:
                active -= 1
            return {
                "valid": True, "error": "", "error_code": "", "base_revision": "a" * 40,
                "source_digest": "source", "content_digest": content, "unchanged": False,
                "module": {}, "diagnostics": [], "budgets": [], "projections": [],
                "nothing_applied": True, "_relative": "rules/sample.md",
                "_content": content.encode(), "_config": {},
            }

        with mock.patch.object(module_editing, "_preview", side_effect=evaluate):
            threads = [threading.Thread(
                target=module_editing.preview,
                args=(self.worktree, "global-" + str(index), self.item["key"], str(index)),
            ) for index in range(8)]
            for thread in threads:
                thread.start()
            deadline = time.time() + 2
            while maximum < 4 and time.time() < deadline:
                time.sleep(0.01)
            release.set()
            for thread in threads:
                thread.join(5)
        self.assertEqual(maximum, 4)

    def test_ephemeral_harness_import_cleanup_restores_process_state_on_success_and_failure(self):
        baseline_path = list(sys.path)
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "bin").mkdir()
                (root / "bin" / "harness").write_text("VALUE = 1\n", encoding="utf-8")
                try:
                    with selection.ephemeral_harness_module(root) as loaded:
                        self.assertEqual(loaded.VALUE, 1)
                        sys.path.insert(0, str(root / "lib"))
                        if fail:
                            raise RuntimeError("candidate failure")
                except RuntimeError:
                    pass
                self.assertNotIn(str(root), selection._HARNESS_MODULES)
                self.assertNotIn(str(root), selection.catalog._POSTURE_MODULES)
                self.assertEqual(sys.path, baseline_path)

    def test_ephemeral_harness_imports_candidate_core_instead_of_resident_modules(self):
        resident = sys.modules.get("harness_core.catalog")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bin").mkdir()
            (root / "lib" / "harness_core").mkdir(parents=True)
            (root / "lib" / "harness_core" / "__init__.py").write_text("", encoding="utf-8")
            (root / "lib" / "harness_core" / "catalog.py").write_text(
                "ORIGIN = __file__\n", encoding="utf-8",
            )
            (root / "bin" / "harness").write_text(
                "import harness_core.catalog as catalog\nORIGIN = catalog.ORIGIN\n",
                encoding="utf-8",
            )
            with selection.ephemeral_harness_module(root) as loaded:
                self.assertTrue(Path(loaded.ORIGIN).resolve().is_relative_to((root / "lib").resolve()))
            self.assertIs(sys.modules.get("harness_core.catalog"), resident)

    def test_size_boundary_and_request_identity_include_the_base_revision(self):
        self.assertEqual(len(module_editing._content_bytes("x" * module_editing.MAX_SOURCE_BYTES)),
                         module_editing.MAX_SOURCE_BYTES)
        with self.assertRaises(module_editing.ModuleEditError):
            module_editing._content_bytes("x" * (module_editing.MAX_SOURCE_BYTES + 1))
        content = b"candidate"
        first = module_editing._request_identity("module", "a" * 40, "digest", content)
        second = module_editing._request_identity("module", "b" * 40, "digest", content)
        self.assertNotEqual(first, second)

    def test_real_checkpoint_matrix_has_cli_parity_conflicts_and_canonical_retry(self):
        name = draft_support.draft_name("module-matrix-")
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "home"
            config_path = home / ".config" / "agent-harness" / "config.json"
            config_path.parent.mkdir(parents=True)
            config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            config["primitive_roots"] = [str(ROOT / "developer-primitives")]
            config_path.write_text(json.dumps(config), encoding="utf-8")
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith("HARNESS_")}
            environment.update({
                "HARNESS_HOME": str(home),
                "HARNESS_WORKTREE_ROOT": str(Path(temporary) / "worktrees"),
            })
            created = subprocess.run(
                [sys.executable, str(ROOT / "bin" / "harness"), "draft", "create", name, "--json"],
                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(created.returncode, 0, created.stderr or created.stdout)
            try:
                initial = json.loads(created.stdout)
                relative = "developer-primitives/rules/matrix.md"
                with mock.patch.dict(os.environ, environment, clear=True):
                    added = drafts.checkpoint(
                        ROOT, name, initial["revision"], "matrix-module",
                        files={relative: b"# Matrix\n\nOriginal.\n"},
                        check_command=[sys.executable, "-c", "raise SystemExit(0)"],
                    )
                    loaded = module_editing.read(ROOT, name, "root-1:rules:matrix")
                    candidate = "# Matrix\n\nSaved cleanly.\n"
                    content_path = Path(temporary) / "module.md"
                    content_path.write_text(candidate, encoding="utf-8")
                    for action, extra in (
                        ("read", []),
                        ("preview", ["--content", str(content_path)]),
                    ):
                        command = [sys.executable, str(ROOT / "bin" / "harness"), "draft", "module",
                                   action, name, "root-1:rules:matrix", *extra, "--json"]
                        executed = subprocess.run(command, cwd=ROOT, env=environment,
                                                  capture_output=True, text=True, timeout=60)
                        self.assertEqual(executed.returncode, 0, executed.stderr or executed.stdout)
                        self.assertNotIn("invalid choice", executed.stderr)
                        payload = json.loads(executed.stdout)
                        if action == "read":
                            self.assertEqual(payload["content"], "# Matrix\n\nOriginal.\n")
                            self.assertEqual(payload["source_digest"], loaded["source_digest"])
                        else:
                            self.assertEqual(payload["content_digest"], hashlib.sha256(
                                candidate.encode(),
                            ).hexdigest())
                            self.assertIn("diagnostics", payload)
                            self.assertEqual({row["runtime"] for row in payload["budgets"]},
                                             {"claude-code", "codex"})
                            self.assertEqual({row["runtime"] for row in payload["projections"]},
                                             {"claude-code", "codex"})
                    save_command = [
                        sys.executable, str(ROOT / "bin" / "harness"), "draft", "module", "save",
                        name, "root-1:rules:matrix", "--base-revision", added["revision"],
                        "--source-digest", loaded["source_digest"], "--idempotency-key", "matrix-save",
                        "--content", str(content_path), "--json",
                    ]
                    executed = subprocess.run(
                        save_command, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=60,
                    )
                    self.assertEqual(executed.returncode, 0, executed.stderr or executed.stdout)
                    saved = json.loads(executed.stdout)
                    replayed = module_editing.save(
                        ROOT, name, "root-1:rules:matrix", added["revision"],
                        loaded["source_digest"], "matrix-save", candidate,
                    )
                    changed = module_editing.save(
                        ROOT, name, "root-1:rules:matrix", added["revision"],
                        loaded["source_digest"], "matrix-save", candidate + "Changed request.\n",
                    )

                    self.assertTrue(saved["saved"], saved["error"])
                    self.assertEqual(saved["saved_lint"], [])
                    self.assertTrue(replayed["saved"], replayed["error"])
                    self.assertTrue(replayed["result"]["replayed"])
                    self.assertEqual(replayed["result"]["revision"], saved["result"]["revision"])
                    self.assertEqual(changed["error_code"], "idempotency-conflict")

                    unchanged_key = "matrix-unchanged"
                    unchanged = module_editing.save(
                        ROOT, name, "root-1:rules:matrix", saved["result"]["revision"],
                        hashlib.sha256(candidate.encode()).hexdigest(), unchanged_key, candidate,
                    )
                    self.assertTrue(unchanged["unchanged"])
                    self.assertFalse(unchanged["saved"])
                    self.assertIsNone(unchanged["result"])
                    self.assertEqual(drafts._revision(drafts.find(ROOT, name)[0]),
                                     saved["result"]["revision"])
                    self.assertIsNone(drafts.replay_request_response(
                        ROOT, name, unchanged_key,
                        module_editing._request_identity(
                            "root-1:rules:matrix", saved["result"]["revision"],
                            hashlib.sha256(candidate.encode()).hexdigest(), candidate.encode(),
                        ),
                    ))

                    worktree, _state = drafts.find(ROOT, name)
                    commits = subprocess.run(
                        ["git", "-C", str(worktree), "rev-list", "--count",
                         added["revision"] + ".." + saved["result"]["revision"]],
                        capture_output=True, text=True, check=True,
                    )
                    self.assertEqual(commits.stdout.strip(), "1")
                    linted = subprocess.run(
                        [sys.executable, "bin/harness", "lint"], cwd=worktree,
                        env=environment, capture_output=True, text=True, timeout=60,
                    )
                    self.assertEqual(linted.returncode, 0, linted.stderr or linted.stdout)
                    self.assertIn("lint: 0 finding(s)", linted.stdout)

                    digest_after_save = hashlib.sha256(candidate.encode()).hexdigest()
                    advanced = drafts.checkpoint(
                        ROOT, name, saved["result"]["revision"], "advance-revision",
                        files={"matrix-unrelated.txt": b"advance\n"},
                        check_command=[sys.executable, "-c", "raise SystemExit(0)"],
                    )
                    old_replay = module_editing.save(
                        ROOT, name, "root-1:rules:matrix", added["revision"],
                        loaded["source_digest"], "matrix-save", candidate,
                    )
                    self.assertTrue(old_replay["result"]["replayed"])
                    self.assertEqual(old_replay["result"]["revision"], saved["result"]["revision"])
                    self.assertNotEqual(old_replay["result"]["revision"], advanced["revision"])
                    revision_buffer = "# Matrix\n\nRevision conflict buffer.\n"
                    stale_revision = module_editing.save(
                        ROOT, name, "root-1:rules:matrix", saved["result"]["revision"],
                        digest_after_save, "revision-conflict", revision_buffer,
                    )
                    self.assertEqual(stale_revision["error_code"], "stale-revision")
                    self.assertEqual((worktree / relative).read_text(encoding="utf-8"), candidate)
                    self.assertEqual(drafts._revision(worktree), advanced["revision"])

                    (worktree / relative).write_text("# Matrix\n\nExternal source.\n", encoding="utf-8")
                    subprocess.run(["git", "-C", str(worktree), "add", "--", relative], check=True)
                    subprocess.run(
                        ["git", "-C", str(worktree), "commit", "-qm", "external source"], check=True,
                    )
                    state = drafts._read_state(worktree)
                    state["revision"] = drafts._revision(worktree)
                    drafts._atomic_json(drafts._paths(worktree)["state"], state)
                    source_buffer = "# Matrix\n\nSource conflict buffer.\n"
                    stale_source = module_editing.save(
                        ROOT, name, "root-1:rules:matrix", state["revision"], digest_after_save,
                        "source-conflict", source_buffer,
                    )
                    self.assertEqual(stale_source["error_code"], "stale-source")
                    self.assertEqual(
                        (worktree / relative).read_text(encoding="utf-8"),
                        "# Matrix\n\nExternal source.\n",
                    )
                    self.assertEqual(drafts._revision(worktree), state["revision"])
            finally:
                draft_support.discard_draft(self, name, environment)

    def test_linked_worktree_cli_uses_its_checkout_for_configured_root_resolution(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            linked = base / "linked-checkout"
            subprocess.run(
                ["git", "-C", str(ROOT), "worktree", "add", "--detach", str(linked), "HEAD"],
                capture_output=True, text=True, timeout=30, check=True,
            )
            self.addCleanup(lambda: subprocess.run(
                ["git", "-C", str(ROOT), "worktree", "remove", "--force", str(linked)],
                capture_output=True, text=True, timeout=30,
            ))
            shutil.copy2(ROOT / "bin" / "harness", linked / "bin" / "harness")
            shutil.copytree(
                ROOT / "lib" / "harness_core" / "studio",
                linked / "lib" / "harness_core" / "studio",
                dirs_exist_ok=True,
            )
            home = base / "home"
            config_path = home / ".config" / "agent-harness" / "config.json"
            config_path.parent.mkdir(parents=True)
            config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            config["primitive_roots"] = [str(linked / "custom-primitives")]
            config_path.write_text(json.dumps(config), encoding="utf-8")
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith("HARNESS_")}
            environment.update({"HARNESS_HOME": str(home),
                                "HARNESS_WORKTREE_ROOT": str(base / "drafts")})
            name = draft_support.draft_name("linked-resolution-")
            created = subprocess.run(
                [sys.executable, str(linked / "bin" / "harness"),
                 "draft", "create", name, "--json"],
                cwd=linked, env=environment, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(created.returncode, 0, created.stderr or created.stdout)
            try:
                initial = json.loads(created.stdout)
                seeded = drafts.checkpoint(
                    linked, name, initial["revision"], "linked-module",
                    files={"custom-primitives/rules/linked-only.md": b"# Linked checkout module\n"},
                    check_command=[sys.executable, "-c", "raise SystemExit(0)"],
                )
                draft_worktree, draft_state = drafts.find(linked, name)
                drafts._atomic_bytes(
                    drafts._paths(draft_worktree)["config"],
                    (json.dumps(config, indent=2, sort_keys=True) + "\n").encode(),
                )
                draft_state["config_present"] = True
                drafts._atomic_json(drafts._paths(draft_worktree)["state"], draft_state)
                self.assertIn("primitive_roots", json.loads(
                    drafts._paths(draft_worktree)["config"].read_text(encoding="utf-8"),
                ))
                self.assertEqual(drafts.find(linked, name)[0], draft_worktree)
                self.assertTrue((draft_worktree / "custom-primitives" / "rules"
                                 / "linked-only.md").is_file())
                with drafts.locked_context(linked, name) as (locked_worktree, _state, raw_config):
                    mapped = module_editing._mapped_config(linked, locked_worktree, raw_config)
                self.assertEqual(mapped["primitive_roots"],
                                 [str(draft_worktree / "custom-primitives")])
                self.assertEqual(drafts.read_config(linked, name)["config"]["primitive_roots"],
                                 [str(linked / "custom-primitives")])
                self.assertIn("linked-only", [item["name"]
                                               for item in module_editing.list_modules(linked, name)])
                inventory = subprocess.run(
                    [sys.executable, str(linked / "bin" / "harness"), "draft", "module", "read",
                     name, "--json"],
                    cwd=linked, env=environment, capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(inventory.returncode, 0, inventory.stderr or inventory.stdout)
                inventory_payload = json.loads(inventory.stdout)
                module_key = next(item["key"] for item in inventory_payload["modules"]
                                  if item["name"] == "linked-only")
                read = subprocess.run(
                    [sys.executable, str(linked / "bin" / "harness"), "draft", "module", "read",
                     name, module_key, "--json"],
                    cwd=linked, env=environment, capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(read.returncode, 0, read.stderr or read.stdout)
                payload = json.loads(read.stdout)
                self.assertEqual(payload["status"], "ready", payload)
                self.assertEqual(payload["content"], "# Linked checkout module\n")
                self.assertEqual(payload["draft"]["revision"], seeded["revision"])
            finally:
                draft_support.discard_draft(self, name, environment, repo=linked,
                                             cli=linked / "bin" / "harness")
            removed = subprocess.run(
                ["git", "-C", str(ROOT), "worktree", "remove", "--force", str(linked)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(removed.returncode, 0, removed.stderr or removed.stdout)

    def test_real_save_lints_with_changed_draft_config_instead_of_installed_profile(self):
        name = draft_support.draft_name("module-config-lint-")
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            home = base / "home"
            external = base / "external-primitives"
            (external / "skills" / "sandbox").mkdir(parents=True)
            (external / "skills" / "sandbox" / "SKILL.md").write_text(
                "---\nname: sandbox\ndescription: Duplicate for collision proof\n---\n",
                encoding="utf-8",
            )
            config_path = home / ".config" / "agent-harness" / "config.json"
            config_path.parent.mkdir(parents=True)
            installed_config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            installed_config["primitive_roots"] = [str(ROOT / "developer-primitives")]
            config_path.write_text(json.dumps(installed_config), encoding="utf-8")
            draft_config = json.loads(json.dumps(installed_config))
            draft_config["primitive_roots"].append(str(external))
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith("HARNESS_")}
            environment.update({"HARNESS_HOME": str(home),
                                "HARNESS_WORKTREE_ROOT": str(base / "worktrees")})
            created = subprocess.run(
                [sys.executable, str(ROOT / "bin" / "harness"), "draft", "create", name, "--json"],
                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(created.returncode, 0, created.stderr or created.stdout)
            initial = json.loads(created.stdout)
            try:
                with mock.patch.dict(os.environ, environment, clear=True):
                    seeded = drafts.checkpoint(
                        ROOT, name, initial["revision"], "seed-config-lint",
                        files={"developer-primitives/rules/config-lint.md":
                               b"# Draft config lint\n\nOriginal.\n"},
                        check_command=[sys.executable, "-c", "raise SystemExit(0)"],
                    )
                    configured = drafts.checkpoint_config(
                        ROOT, name, seeded["revision"], "changed-draft-config", draft_config,
                        check_command=[sys.executable, "-c", "raise SystemExit(0)"],
                    )
                    key = "root-1:rules:config-lint"
                    loaded = module_editing.read(ROOT, name, key)
                    candidate = "# Draft config lint\n\nCandidate.\n"
                    planned = module_editing._preview(ROOT, name, key, candidate)
                    self.assertTrue(any("collision: skill 'sandbox'" in item["message"]
                                        for item in planned["diagnostics"]), planned)
                    planned.update(valid=True, error="", error_code="", diagnostics=[])
                    for budget in planned["budgets"]:
                        budget["over_cap"] = False
                    with mock.patch.object(module_editing, "_preview_snapshot", return_value=planned):
                        refused = module_editing.save(
                            ROOT, name, key, configured["revision"], loaded["source_digest"],
                            "real-draft-config-lint", candidate,
                        )
                    self.assertEqual(refused["error_code"], "check-failed", refused)
                    self.assertIn("collision: skill 'sandbox'", refused["error"])
                    worktree, state = drafts.find(ROOT, name)
                    self.assertEqual(state["revision"], configured["revision"])
                    self.assertIn("Original", (worktree / "developer-primitives" / "rules"
                                                / "config-lint.md").read_text(encoding="utf-8"))
            finally:
                draft_support.discard_draft(self, name, environment)

    def test_real_skill_and_layer_selected_stance_save_with_external_runtime_input(self):
        name = draft_support.draft_name("module-kinds-")
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            home = base / "home"
            external = base / "external-primitives"
            (external / "rules").mkdir(parents=True)
            (external / "rules" / "external.md").write_text(
                "# External runtime sentinel\n", encoding="utf-8",
            )
            config_path = home / ".config" / "agent-harness" / "config.json"
            config_path.parent.mkdir(parents=True)
            config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            config["primitive_roots"] = [str(ROOT / "developer-primitives"), str(external)]
            config["stances"]["voice"] = "concise"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            draft_config = json.loads(json.dumps(config))
            draft_config["mode"] = "matrix-mode"
            draft_config.setdefault("init_defaults", {}).setdefault("stances", {})["voice"] = "concise"
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith("HARNESS_")}
            environment.update({"HARNESS_HOME": str(home),
                                "HARNESS_WORKTREE_ROOT": str(base / "worktrees")})
            created = subprocess.run(
                [sys.executable, str(ROOT / "bin" / "harness"), "draft", "create", name, "--json"],
                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(created.returncode, 0, created.stderr or created.stdout)
            try:
                initial = json.loads(created.stdout)
                with mock.patch.dict(os.environ, environment, clear=True):
                    seeded = drafts.checkpoint(
                        ROOT, name, initial["revision"], "seed-kinds",
                        files={
                            "developer-primitives/rules/matrix-rule.md":
                                b"# Matrix rule\n\nOld rule.\n",
                            "developer-primitives/skills/matrix-skill/SKILL.md":
                                b"---\nname: matrix-skill\ndescription: Matrix skill\n---\n\nOld skill.\n",
                            "developer-primitives/stances/voice/matrix.md": b"Old matrix voice.\n",
                            "developer-primitives/stances/voice/inactive.md": b"Old inactive voice.\n",
                            "developer-primitives/modes/matrix-mode.json": json.dumps({
                                "schema_version": 1, "description": "Select the matrix test voice.",
                                "stances": {"voice": "matrix"},
                                "rules": {"matrix-rule": "off"},
                                "skills": {"matrix-skill": "off"},
                            }).encode() + b"\n",
                        },
                        check_command=[sys.executable, "-c", "raise SystemExit(0)"],
                    )
                    configured = drafts.checkpoint_config(
                        ROOT, name, seeded["revision"], "select-matrix-mode", draft_config,
                        check_command=[sys.executable, "-c", "raise SystemExit(0)"],
                    )
                    baseline_harness = set(selection._HARNESS_MODULES)
                    baseline_posture = set(selection.catalog._POSTURE_MODULES)
                    revision = configured["revision"]
                    for key, candidate in (
                        ("root-1:rules:matrix-rule", "# Matrix rule\n\nNew rule.\n"),
                        ("root-1:skills:matrix-skill",
                         "---\nname: matrix-skill\ndescription: Matrix skill\n---\n\nNew skill.\n"),
                        ("root-1:stances:voice/inactive", "New inactive voice.\n"),
                        ("root-1:stances:voice/matrix", "New matrix voice.\n"),
                    ):
                        loaded = module_editing.read(ROOT, name, key)
                        self.assertEqual(loaded["status"], "ready", loaded["message"])
                        previewed = module_editing.preview(ROOT, name, key, candidate)
                        self.assertTrue(previewed["valid"], previewed["error"])
                        if key.endswith("voice/matrix"):
                            projected = {row["runtime"]: row["text"]
                                         for row in previewed["projections"]}
                            self.assertIn("New matrix voice", projected["claude-code"])
                            self.assertIn("New matrix voice", projected["codex"])
                            self.assertNotIn("External runtime sentinel", projected["codex"])
                            self.assertNotIn(str(external), json.dumps(projected))
                            self.assertNotIn("studio-module-preview-", json.dumps(projected))
                        else:
                            self.assertTrue(all(not row["text"]
                                                for row in previewed["projections"]), previewed)
                            self.assertTrue(all(row["line_delta"] == 0
                                                and row["token_delta"] == 0
                                                for row in previewed["budgets"]), previewed)
                        saved = module_editing.save(
                            ROOT, name, key, revision, loaded["source_digest"],
                            "save-" + hashlib.sha256(key.encode()).hexdigest()[:12], candidate,
                        )
                        self.assertTrue(saved["saved"], saved["error"])
                        revision = saved["result"]["revision"]

                    stance = module_editing.read(ROOT, name, "root-1:stances:voice/matrix")
                    failed = module_editing.preview(
                        ROOT, name, "root-1:stances:voice/matrix",
                        stance["content"] + "\n" + "AKIA" + "I" * 16 + "\n",
                    )
                    self.assertFalse(failed["valid"])
                    self.assertEqual(set(selection._HARNESS_MODULES), baseline_harness)
                    self.assertEqual(set(selection.catalog._POSTURE_MODULES), baseline_posture)
            finally:
                draft_support.discard_draft(self, name, environment)

    # -- R6 review proofs ------------------------------------------------------------------

    def _internal_paths(self):
        paths = {self.temporary.name, os.path.realpath(self.temporary.name),
                 tempfile.gettempdir(), os.path.realpath(tempfile.gettempdir())}
        return sorted(path for path in paths if path)

    def assert_no_internal_path(self, payload):
        text = json.dumps(payload, sort_keys=True)
        for internal in self._internal_paths():
            self.assertNotIn(internal, text)

    @contextmanager
    def config_state(self, config):
        @contextmanager
        def locked(*_args, **_kwargs):
            yield self.worktree, self.state, config

        @contextmanager
        def unlocked(*_args, **_kwargs):
            yield

        with mock.patch.object(module_editing.drafts, "locked_context", side_effect=locked), \
                mock.patch.object(module_editing.drafts, "request_lock", side_effect=unlocked), \
                mock.patch.object(module_editing.drafts, "replay_request_response",
                                  return_value=None):
            yield

    def test_refusal_detail_is_path_aliased_and_length_capped(self):
        noisy = (str(self.worktree) + "/custom/rules/sample.md:2: failed in "
                 + tempfile.gettempdir() + "/studio-module-check-x " + "y" * 20000)
        first, second, third, fourth = self.patches()
        with first, second, third, fourth, \
                mock.patch.object(module_editing.drafts, "replay_request_response",
                                  return_value=None), \
                mock.patch.object(module_editing.drafts, "checkpoint",
                                  side_effect=drafts.DraftError("check-failed", noisy)):
            saved = module_editing.save(
                self.worktree, "draft-one", self.item["key"], self.state["revision"],
                hashlib.sha256(self.source.read_bytes()).hexdigest(), "noisy-lint",
                "# Sample\n\nChanged text.\n",
            )
        self.assertEqual(saved["error_code"], "check-failed")
        self.assertLessEqual(len(saved["error"]), module_evaluator.MAX_MESSAGE_CHARS)
        self.assertTrue(saved["error"].endswith(module_evaluator.TRUNCATED))
        self.assertIn("<temporary>/studio-module-check-x", saved["error"])
        self.assert_no_internal_path(saved)

    def test_evaluator_diagnostics_are_capped_and_the_cut_is_reported(self):
        many = ["finding %d %s" % (index, "m" * 5000) for index in range(300)]
        harness = SimpleNamespace(
            lint_tree=lambda _root: list(many), check_context_cap=lambda _root, _config=None: [],
            check_cost_sidecars=lambda _root: [], check_stance_constraints=lambda _root: [],
            check_collisions=lambda _root, _config=None: [],
        )
        evaluated = module_evaluator._diagnostics(harness, self.worktree, "x.md", {}, {})
        self.assertEqual(len(evaluated), module_evaluator.MAX_DIAGNOSTICS + 1)
        self.assertTrue(all(len(item["message"]) <= module_evaluator.MAX_MESSAGE_CHARS
                            for item in evaluated))
        self.assertTrue(evaluated[0]["message"].endswith(module_evaluator.TRUNCATED))
        self.assertIn("200 more findings not shown", evaluated[-1]["message"])

        worker = {"ok": True, "line_cap": 225, "token_cap": 3500,
                  "authored": {"lines": 1, "tokens": 1}, "rendered": {}, "named": {},
                  "diagnostics": [{"message": "w" * 9000, "line": None, "severity": "error"}] * 250}
        item = dict(self.item, _relative="custom/rules/sample.md")
        with mock.patch.object(module_editing.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, stdout=json.dumps(worker), stderr="")):
            bounded = module_editing._evaluate_candidate(self.worktree, {}, item, {}, True)
        self.assertEqual(len(bounded["diagnostics"]), module_evaluator.MAX_DIAGNOSTICS + 1)
        self.assertIn("150 more findings not shown", bounded["diagnostics"][-1]["message"])
        self.assertTrue(all(len(row["message"]) <= module_evaluator.MAX_MESSAGE_CHARS
                            for row in bounded["diagnostics"]))

    def test_symlinked_and_oversized_external_roots_are_refused_without_path_leaks(self):
        base = Path(self.temporary.name)
        real_root = base / "outside-real"
        (real_root / "rules").mkdir(parents=True)
        for index in range(3):
            (real_root / "rules" / ("r%d.md" % index)).write_text("# R\n", encoding="utf-8")
        linked_root = base / "outside-link"
        linked_root.symlink_to(real_root, target_is_directory=True)
        inner_link = base / "outside-inner"
        (inner_link / "rules").mkdir(parents=True)
        (inner_link / "rules" / "escape.md").symlink_to(real_root / "rules" / "r0.md")
        cases = (
            # A configured root that is itself a link is canonicalized once, then bounded.
            ("symlinked root", [str(linked_root)], {"MAX_EXTERNAL_FILES": 3},
             "external-root-too-large"),
            ("symlink inside root", [str(inner_link)], {}, "external-root-refused"),
            ("file count", [str(real_root)], {"MAX_EXTERNAL_FILES": 3}, "external-root-too-large"),
            ("byte count", [str(real_root)], {"MAX_EXTERNAL_BYTES": 11}, "external-root-too-large"),
        )
        for label, roots, limits, code in cases:
            with self.subTest(label):
                config = {"primitive_roots": [str(self.worktree / "custom"), *roots]}
                _first, second, third, fourth = self.patches()
                with self.config_state(config), second, third, fourth, \
                        mock.patch.multiple(module_editing, **limits) if limits else \
                        nullcontext():
                    result = module_editing.preview(
                        self.worktree, "draft-one", self.item["key"], "# Sample\n\nNew.\n",
                    )
                self.assertEqual((result["valid"], result["error_code"]), (False, code), result)
                self.assert_no_internal_path(result)
        within = {"primitive_roots": [str(self.worktree / "custom"), str(real_root)]}
        _first, second, third, fourth = self.patches()
        with self.config_state(within), second, third, fourth, \
                mock.patch.multiple(module_editing, MAX_EXTERNAL_FILES=4, MAX_EXTERNAL_BYTES=12):
            result = module_editing.preview(
                self.worktree, "draft-one", self.item["key"], "# Sample\n\nNew.\n",
            )
        self.assertEqual(result["error_code"], "", result)

    def test_unexpected_exceptions_become_structured_refusals(self):
        leak = RuntimeError("unexpected at " + str(self.worktree))
        first, second, third, _fourth = self.patches()
        with first, second, third, \
                mock.patch.object(module_editing, "_evaluate_candidate", side_effect=leak), \
                mock.patch.object(module_editing.drafts, "replay_request_response",
                                  return_value=None):
            previewed = module_editing.preview(
                self.worktree, "draft-one", self.item["key"], "# Sample\n\nNew.\n",
            )
            saved = module_editing.save(
                self.worktree, "draft-one", self.item["key"], self.state["revision"],
                hashlib.sha256(self.source.read_bytes()).hexdigest(), "unexpected",
                "# Sample\n\nNew.\n",
            )
        with self.config_state({"primitive_roots": [str(self.worktree / "custom")]}), \
                mock.patch.object(module_editing, "_editable_from_context", side_effect=leak):
            loaded = module_editing.read(self.worktree, "draft-one", self.item["key"])
        self.assertEqual((previewed["valid"], previewed["error_code"]), (False, "preview-unavailable"))
        self.assertEqual(previewed["error"], "draft module evaluation is unavailable")
        self.assertEqual((saved["saved"], saved["error_code"]), (False, "preview-unavailable"))
        self.assertEqual((loaded["status"], loaded["error_code"]), ("unavailable", "module-invalid"))
        for payload in (previewed, saved, loaded):
            self.assert_no_internal_path(payload)
            self.assertEqual(
                set(payload) - {"saved", "result", "saved_lint"},
                {field for field, _kind in (
                    server.MODULE_READ if payload is loaded else server.MODULE_PREVIEW).fields},
            )

    def test_inventory_item_and_byte_caps_hold_exactly_at_the_limit(self):
        rules = self.worktree / "custom" / "rules"
        for index in range(2):
            (rules / ("extra-%d.md" % index)).write_text("# Extra %d\n" % index, encoding="utf-8")
        total = sum(len(path.read_bytes()) for path in rules.glob("*.md"))
        config = {"primitive_roots": [str(self.worktree / "custom")]}
        cases = (
            ({"MAX_INVENTORY_MODULES": 3}, "ready"),
            ({"MAX_INVENTORY_MODULES": 2}, "module-inventory-too-large"),
            ({"MAX_INVENTORY_BYTES": total}, "ready"),
            ({"MAX_INVENTORY_BYTES": total - 1}, "module-inventory-too-large"),
        )
        for limits, expected in cases:
            with self.subTest(limits=limits), self.config_state(config), \
                    mock.patch.multiple(module_editing, **limits):
                loaded = module_editing.read(self.worktree, "draft-one")
            if expected == "ready":
                self.assertEqual((loaded["status"], len(loaded["modules"])), ("ready", 3), loaded)
            else:
                self.assertEqual((loaded["status"], loaded["error_code"]), ("unavailable", expected))
                self.assertEqual(loaded["modules"], [])

    @contextmanager
    def real_draft(self, prefix, extra_environment=None):
        name = draft_support.draft_name(prefix + "-")
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            home = base / "home"
            config_path = home / ".config" / "agent-harness" / "config.json"
            config_path.parent.mkdir(parents=True)
            config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            config["primitive_roots"] = [str(ROOT / "developer-primitives")]
            config_path.write_text(json.dumps(config), encoding="utf-8")
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith("HARNESS_")}
            environment.update({"HARNESS_HOME": str(home),
                                "HARNESS_WORKTREE_ROOT": str(base / "worktrees")})
            created = subprocess.run(
                [sys.executable, str(ROOT / "bin" / "harness"), "draft", "create", name, "--json"],
                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(created.returncode, 0, created.stderr or created.stdout)
            initial = json.loads(created.stdout)
            try:
                with mock.patch.dict(os.environ, dict(environment, **(extra_environment or {})),
                                     clear=True):
                    yield name, initial, environment, base, config
            finally:
                draft_support.discard_draft(self, name, environment)

    def test_real_preview_and_save_evaluate_out_of_process_with_the_save_lint_environment(self):
        leaked = {"HARNESS_STANCE_VOICE": "verbose", "HARNESS_MODE": "leaky"}
        with self.real_draft("module-isolation", leaked) as (name, initial, _env, _base, _config):
            relative = "developer-primitives/rules/isolation.md"
            seeded = drafts.checkpoint(
                ROOT, name, initial["revision"], "seed-isolation",
                files={relative: b"# Isolation\n\nOriginal.\n"},
                check_command=[sys.executable, "-c", "raise SystemExit(0)"],
            )
            key = "root-1:rules:isolation"
            loaded = module_editing.read(ROOT, name, key)
            calls = []
            original_run = subprocess.run

            def spy(command, *args, **kwargs):
                argv = [str(part) for part in command] if isinstance(command, (list, tuple)) else []
                role = ("evaluator" if any(part.endswith("module_evaluator.py") for part in argv)
                        else "lint" if argv[-2:] == ["bin/harness", "lint"] else None)
                if role:
                    environment = kwargs.get("env")
                    home = Path(environment["HARNESS_HOME"]) if environment else None
                    config_file = home / ".config" / "agent-harness" / "config.json" if home else None
                    calls.append((role, environment, config_file.read_text(encoding="utf-8")
                                  if config_file and config_file.is_file() else None))
                return original_run(command, *args, **kwargs)

            path_before, modules_before = list(sys.path), set(sys.modules)
            candidate = "# Isolation\n\nSaved out of process.\n"
            with mock.patch.object(module_editing.subprocess, "run", side_effect=spy):
                previewed = module_editing.preview(ROOT, name, key, candidate)
                saved = module_editing.save(
                    ROOT, name, key, seeded["revision"], loaded["source_digest"],
                    "isolation-save", candidate,
                )
            self.assertTrue(previewed["valid"], previewed)
            self.assertTrue(saved["saved"], saved)
            self.assertEqual(sys.path, path_before)
            added = set(sys.modules) - modules_before
            self.assertNotIn("studio_candidate_harness", added)
            for module_name in added:
                origin = getattr(sys.modules.get(module_name), "__file__", None) or ""
                self.assertFalse(origin.startswith(tuple(self._internal_paths())), module_name)
            roles = [role for role, _environment, _config in calls]
            self.assertEqual(roles.count("evaluator"), 4, roles)
            self.assertEqual(roles.count("lint"), 1, roles)
            lint_environment, lint_config = next(
                (environment, config) for role, environment, config in calls if role == "lint")
            for role, environment, config in calls:
                self.assertIsNotNone(environment, role)
                self.assertFalse([key for key in environment if key.startswith("HARNESS_STANCE_")])
                self.assertNotIn("HARNESS_MODE", environment)
                self.assertNotEqual(environment["HARNESS_HOME"], os.environ["HARNESS_HOME"])
                self.assertEqual(set(environment) - {"HARNESS_HOME"},
                                 set(lint_environment) - {"HARNESS_HOME"})
                self.assertEqual(config, lint_config)

    def test_check_environment_drops_every_variable_load_config_folds_over_the_draft(self):
        leaked = {"HARNESS_IDENTITY_NAME": "Leaked", "HARNESS_PERMISSIONS": "leaky",
                  "HARNESS_MANAGE_VSCODE": "0", "HARNESS_MANAGE_CODEX": "0",
                  "HARNESS_STANCE_VOICE": "verbose", "HARNESS_MODE": "leaky"}
        draft = {"identity": {"name": "Draft"}, "permissions": "default",
                 "vscode": {"manage": True}, "codex": {"manage": True}}
        harness = selection._harness_module(ROOT)
        with mock.patch.dict(os.environ, leaked), \
                module_editing._draft_check_environment(draft) as environment:
            self.assertFalse(set(leaked) & set(environment))
            with mock.patch.dict(os.environ, environment, clear=True):
                loaded = harness.load_config(environment)
        self.assertEqual(loaded["identity"]["name"], "Draft")
        self.assertEqual(loaded["permissions"], "default")
        self.assertTrue(loaded["vscode"]["manage"])
        self.assertTrue(loaded["codex"]["manage"])

    def test_personal_lint_terms_flag_preview_and_refuse_save_like_citizen_lint(self):
        # Built at run time so no file in the repository ever contains the term.
        term = "studio-forbidden-" + uuid.uuid4().hex[:12]
        with self.real_draft("module-lint-terms") as (name, initial, environment, _base, _config):
            terms_file = Path(environment["HARNESS_HOME"]) / ".config" / "agent-harness" / "lint-terms.txt"
            terms_file.write_text("# test terms\n" + term + "\n", encoding="utf-8")
            relative = "developer-primitives/rules/terms.md"
            seeded = drafts.checkpoint(
                ROOT, name, initial["revision"], "seed-terms",
                files={relative: b"# Terms\n\nOriginal.\n"},
                check_command=[sys.executable, "-c", "raise SystemExit(0)"],
            )
            key = "root-1:rules:terms"
            loaded = module_editing.read(ROOT, name, key)
            clean = module_editing.preview(ROOT, name, key, "# Terms\n\nStill clean.\n")
            self.assertTrue(clean["valid"], clean)
            candidate = "# Terms\n\nMentions " + term.upper() + " here.\n"
            previewed = module_editing.preview(ROOT, name, key, candidate)
            self.assertFalse(previewed["valid"], previewed)
            self.assertEqual(previewed["error_code"], "lint-refused")
            self.assertIn("personal term from lint-terms file",
                          json.dumps(previewed["diagnostics"]))
            saved = module_editing.save(
                ROOT, name, key, seeded["revision"], loaded["source_digest"],
                "terms-save", candidate,
            )
            self.assertFalse(saved["saved"], saved)
            self.assertEqual(saved["error_code"], "lint-refused")
            worktree, state = drafts.find(ROOT, name)
            self.assertEqual(state["revision"], seeded["revision"])
            self.assertNotIn(term.upper(), (worktree / relative).read_text(encoding="utf-8"))
            # The public command, run over the same content with the same home, agrees.
            original = (worktree / relative).read_bytes()
            (worktree / relative).write_text(candidate, encoding="utf-8")
            try:
                linted = subprocess.run(
                    [sys.executable, "bin/harness", "lint"], cwd=worktree, env=environment,
                    capture_output=True, text=True, timeout=300,
                )
            finally:
                (worktree / relative).write_bytes(original)
            self.assertNotEqual(linted.returncode, 0, linted.stdout)
            self.assertIn(relative + ":3: personal term from lint-terms file", linted.stdout)

    def test_check_environment_copies_only_a_bounded_regular_lint_terms_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            folder = home / ".config" / "agent-harness"
            folder.mkdir(parents=True)
            terms = folder / "lint-terms.txt"
            with mock.patch.dict(os.environ, {"HARNESS_HOME": str(home),
                                              "HARNESS_LINT_TERMS": "extra-term"}):
                with module_editing._draft_check_environment({}) as environment:
                    copied = Path(environment["HARNESS_HOME"]) / ".config" / "agent-harness"
                    self.assertFalse((copied / "lint-terms.txt").exists())
                    self.assertEqual(environment["HARNESS_LINT_TERMS"], "extra-term")
                terms.write_text("term-one\n", encoding="utf-8")
                with module_editing._draft_check_environment({}) as environment:
                    copied = Path(environment["HARNESS_HOME"]) / ".config" / "agent-harness"
                    self.assertEqual((copied / "lint-terms.txt").read_text(encoding="utf-8"),
                                     "term-one\n")
                terms.write_bytes(b"x" * (module_editing.MAX_LINT_TERMS_BYTES + 1))
                with self.assertRaises(module_editing.ModuleEditError) as raised:
                    with module_editing._draft_check_environment({}):
                        pass
                self.assertEqual(raised.exception.code, "lint-terms-refused")
                terms.unlink()
                target = home / "elsewhere.txt"
                target.write_text("term-one\n", encoding="utf-8")
                terms.symlink_to(target)
                with self.assertRaises(module_editing.ModuleEditError):
                    with module_editing._draft_check_environment({}):
                        pass
                terms.unlink()
                shutil.rmtree(folder)
                (home / "real").mkdir()
                (home / "real" / "lint-terms.txt").write_text("term-one\n", encoding="utf-8")
                folder.symlink_to(home / "real")
                with self.assertRaises(module_editing.ModuleEditError):
                    with module_editing._draft_check_environment({}):
                        pass

    def test_two_processes_with_the_same_exact_save_share_one_durable_response(self):
        with self.real_draft("module-processes") as (name, initial, environment, base, _config):
            relative = "developer-primitives/rules/processes.md"
            seeded = drafts.checkpoint(
                ROOT, name, initial["revision"], "seed-processes",
                files={relative: b"# Processes\n\nOriginal.\n"},
                check_command=[sys.executable, "-c", "raise SystemExit(0)"],
            )
            key = "root-1:rules:processes"
            loaded = module_editing.read(ROOT, name, key)
            content = base / "processes.md"
            content.write_text("# Processes\n\nOne durable save.\n", encoding="utf-8")
            command = [sys.executable, str(ROOT / "bin" / "harness"), "draft", "module", "save",
                       name, key, "--base-revision", seeded["revision"],
                       "--source-digest", loaded["source_digest"],
                       "--idempotency-key", "shared-process-key", "--content", str(content), "--json"]
            processes = [subprocess.Popen(command, cwd=ROOT, env=environment, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, text=True) for _ in range(2)]
            outputs = [process.communicate(timeout=600) for process in processes]
            for process, (stdout, stderr) in zip(processes, outputs):
                self.assertEqual(process.returncode, 0, stderr or stdout)
            payloads = [json.loads(stdout) for stdout, _stderr in outputs]
            self.assertTrue(all(payload["saved"] for payload in payloads), payloads)
            self.assertEqual(sorted(payload["result"]["replayed"] for payload in payloads),
                             [False, True])
            for payload in payloads:
                payload["result"].pop("replayed")
            self.assertEqual(payloads[0], payloads[1])
            worktree, state = drafts.find(ROOT, name)
            self.assertEqual(state["revision"], payloads[0]["result"]["revision"])
            count = subprocess.run(
                ["git", "-C", str(worktree), "rev-list", "--count",
                 seeded["revision"] + ".." + state["revision"]],
                capture_output=True, text=True, check=True,
            )
            self.assertEqual(count.stdout.strip(), "1")

    def test_inventory_refreshes_and_cli_json_honours_the_field_contract(self):
        with self.real_draft("module-inventory") as (name, initial, environment, base, config):
            first = drafts.checkpoint(
                ROOT, name, initial["revision"], "seed-inventory",
                files={"developer-primitives/rules/first.md": b"# First\n\nOriginal.\n"},
                check_command=[sys.executable, "-c", "raise SystemExit(0)"],
            )
            keys = lambda: {row["key"] for row in module_editing.read(ROOT, name)["modules"]}
            self.assertIn("root-1:rules:first", keys())
            self.assertNotIn("root-1:rules:second", keys())
            second = drafts.checkpoint(
                ROOT, name, first["revision"], "add-inventory",
                files={"developer-primitives/rules/second.md": b"# Second\n\nAdded.\n"},
                check_command=[sys.executable, "-c", "raise SystemExit(0)"],
            )
            self.assertIn("root-1:rules:second", keys())

            candidate = "# First\n\nCLI candidate.\n"
            content = base / "first.md"
            content.write_text(candidate, encoding="utf-8")
            payloads = {}
            for action, extra in (("read", []), ("preview", ["--content", str(content)])):
                executed = subprocess.run(
                    [sys.executable, str(ROOT / "bin" / "harness"), "draft", "module", action,
                     name, "root-1:rules:first", *extra, "--json"],
                    cwd=ROOT, env=environment, capture_output=True, text=True, timeout=120,
                )
                self.assertEqual(executed.returncode, 0, executed.stderr or executed.stdout)
                payloads[action] = json.loads(executed.stdout)
            read_payload, preview_payload = payloads["read"], payloads["preview"]
            self.assertEqual(set(read_payload), {field for field, _ in server.MODULE_READ.fields})
            self.assertEqual(set(preview_payload),
                             {field for field, _ in server.MODULE_PREVIEW.fields})
            self.assertEqual(read_payload["content"], "# First\n\nOriginal.\n")
            self.assertEqual(read_payload["source_digest"],
                             hashlib.sha256(b"# First\n\nOriginal.\n").hexdigest())
            self.assertEqual(read_payload["draft"]["revision"], second["revision"])
            self.assertEqual(read_payload["module"]["key"], "root-1:rules:first")
            self.assertTrue(preview_payload["valid"], preview_payload)
            self.assertEqual(preview_payload["diagnostics"], [])
            self.assertEqual(preview_payload["source_digest"], read_payload["source_digest"])
            self.assertEqual(preview_payload["content_digest"],
                             hashlib.sha256(candidate.encode()).hexdigest())
            self.assertEqual(preview_payload["base_revision"], second["revision"])
            self.assertEqual({row["runtime"] for row in preview_payload["budgets"]},
                             {"claude-code", "codex"})
            for row in preview_payload["budgets"]:
                self.assertEqual(row["line_delta"], 0)
                self.assertFalse(row["over_cap"])
            self.assertEqual({row["runtime"]: row["text"] for row in preview_payload["projections"]},
                             {"claude-code": candidate, "codex": candidate})
            for payload in (read_payload, preview_payload):
                self.assert_no_internal_path(payload)

            dropped = dict(config, primitive_roots=[])
            drafts.checkpoint_config(
                ROOT, name, second["revision"], "drop-root", dropped,
                check_command=[sys.executable, "-c", "raise SystemExit(0)"],
            )
            self.assertEqual(keys(), set())


if __name__ == "__main__":
    unittest.main()

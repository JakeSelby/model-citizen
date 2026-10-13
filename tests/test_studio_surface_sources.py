# SPDX-License-Identifier: MIT
"""The Sources page's surface: one row per source per harness-version period (AH-S375, #1327)."""
import datetime as dt
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_studio_security as studio_security  # noqa: E402
from harness_core.studio import periods  # noqa: E402
from harness_core.studio.surfaces import sources  # noqa: E402
from test_studio_periods import Home, NOW, days_ago, usage_row  # noqa: E402

ROUTE = "/api/harness/periods?surface=sources"
PLUGIN = "bmad-method@bmad"


def attributed(when, modules, voice="concise", **extra):
    return usage_row(when, voice=voice, context_attribution={
        "estimand": "soft estimate", "method": "chars/4", "modules": modules}, **extra)


class SourcesHome(Home):
    """A temp home with one personal primitive root and one enabled third-party plugin."""

    def __init__(self, case):
        super().__init__(case)
        self.personal = self.root / "my-primitives"
        for relative in ("rules/mine.md", "stances/voice/pirate.md", "skills/note/SKILL.md"):
            path = self.personal / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("---\nname: x\ndescription: y\n---\nbody\n")
        plugin = self.root / ".claude" / "plugins" / "cache" / "bmad"
        for relative in ("skills/a/SKILL.md", "skills/b/SKILL.md", "commands/c.md"):
            path = plugin / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
        self.plugin = plugin
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        case.addCleanup(patcher.stop)
        os.environ.pop("CLAUDE_CONFIG_DIR", None)

    def roots(self, roots=None):
        (self.config_dir / "config.json").write_text(json.dumps(
            {"primitive_roots": [str(self.personal)] if roots is None else roots}))

    def plugins(self, enabled):
        claude = self.root / ".claude"
        (claude / "settings.json").write_text(json.dumps({"enabledPlugins": enabled}))
        (claude / "plugins" / "installed_plugins.json").write_text(json.dumps(
            {"version": 2, "plugins": {PLUGIN: [{"scope": "user", "installPath": str(self.plugin)}]}}))


class SourcesProviderTests(unittest.TestCase):
    def setUp(self):
        self.home = SourcesHome(self)

    def answer(self, unit=None):
        return periods.efficacy_rows("sources", unit, home=self.home.root, now=NOW)

    def by_item(self, answer):
        out = {}
        for row in answer["rows"]:
            out.setdefault(row["item_id"], []).append(row)
        return out

    def test_each_source_is_labelled_core_personal_or_third_party(self):
        self.home.roots()
        self.home.plugins({PLUGIN: True, "model-citizen@model-citizen": True, "off@x": False})
        self.home.usage([usage_row(days_ago(3))])
        found = sources.sources(periods._context(self.home.root, 30, NOW))
        personal = "personal:" + str(self.home.personal)
        self.assertEqual(found["order"], ["core", personal, "third-party:" + PLUGIN])
        labels = {k: (v["source"], v["label"]) for k, v in found["by_id"].items()}
        self.assertEqual(labels, {"core": ("Core", "Core"),
                                  personal: ("Personal", "Personal: my-primitives"),
                                  "third-party:" + PLUGIN: ("Third-party", "Third-party: bmad-method")})
        self.assertEqual(found["by_id"][personal]["modules"], 3)
        self.assertEqual(found["by_id"]["third-party:" + PLUGIN]["modules"], 3)
        self.assertEqual(found["by_id"]["third-party:" + PLUGIN]["path"], str(self.home.plugin))
        # No source is ever labelled Team: nothing in the configuration marks a team root.
        self.assertNotIn("Team", {v["source"] for v in found["by_id"].values()})

    def test_attributed_tokens_are_credited_to_the_first_root_holding_each_module(self):
        self.home.roots()
        first = sources.sources(periods._context(self.home.root, 30, NOW))["first"]
        personal = "personal:" + str(self.home.personal)
        modules = {"rules/secrets": 100, "rules/mine": 40, "stances/voice": 30,
                   "skills/gone": 7}
        self.assertEqual(sources.credited(attributed(days_ago(1), modules, voice="pirate"), first),
                         {"core": 100, personal: 70})
        self.assertEqual(sources.credited(attributed(days_ago(1), modules, voice="concise"), first),
                         {"core": 130, personal: 40})
        self.assertEqual(sources.credited(usage_row(days_ago(1)), first), {})

    def test_a_version_change_splits_every_source_with_its_load_and_sessions(self):
        self.home.roots()
        self.home.plugins({PLUGIN: True})
        modules = {"rules/secrets": 100, "rules/mine": 40}
        rows = [attributed(days_ago(n), modules, version="0.14.2") for n in (20, 15)]
        rows += [attributed(days_ago(n), dict(modules, **{"rules/mine": 80}), version="0.15.0")
                 for n in (3, 2)]
        rows.append(usage_row(days_ago(1), version="0.15.0"))  # carries no attribution
        rows.append(attributed(days_ago(2, 1), modules, version="0.15.0", kind="subagent"))
        self.home.usage(rows)
        answer = self.answer()
        self.assertNotIn("note", answer)
        grouped = self.by_item(answer)
        personal = grouped["personal:" + str(self.home.personal)]
        self.assertEqual([(r["setting_label"], r["status"], r["metrics"]["sessions"],
                           r["metrics"]["tokens_per_session"]) for r in personal],
                         [("0.14.2", "measured", 2, 40), ("0.15.0", "measuring", 3, 80)])
        self.assertEqual([r["metrics"]["tokens_per_session"] for r in grouped["core"]], [100, 100])
        self.assertEqual(personal[1]["metrics"]["modules"], 3)
        self.assertEqual((personal[1]["metrics"]["source"], personal[1]["metrics"]["root"],
                          personal[1]["metrics"]["enabled"]),
                         ("Personal", str(self.home.personal), "on"))
        plugin = grouped["third-party:" + PLUGIN][-1]["metrics"]
        self.assertEqual((plugin["sessions"], plugin["tokens_per_session"], plugin["modules"]),
                         (sources.NOT_RECORDED, sources.NOT_RECORDED, 3))
        self.assertEqual(personal[0]["period"]["end"], personal[1]["period"]["start"])

    def test_one_version_answers_one_period_per_source_with_the_note(self):
        self.home.roots()
        self.home.usage([attributed(days_ago(n), {"rules/secrets": 10}) for n in (10, 5)])
        answer = self.answer()
        self.assertEqual(answer["note"], periods.NOTE)
        self.assertEqual([(r["item_id"], r["setting_label"], r["period"]["end"])
                          for r in answer["rows"]],
                         [("core", "0.15.0", None),
                          ("personal:" + str(self.home.personal), "0.15.0", None)])
        self.assertEqual(answer["rows"][1]["metrics"]["tokens_per_session"], 0)

    def test_no_sessions_still_lists_the_sources_unmeasured(self):
        answer = self.answer()
        self.assertEqual([(r["item_id"], r["setting_label"], r["metrics"]["sessions"],
                           r["metrics"]["tokens_per_session"]) for r in answer["rows"]],
                         [("core", "no sessions in this window", 0, sources.NOT_RECORDED)])

    def test_a_malformed_plugin_record_is_not_recorded_rather_than_guessed(self):
        claude = self.home.root / ".claude"
        (claude / "settings.json").write_text(json.dumps({"enabledPlugins": {PLUGIN: True}}))
        (claude / "plugins" / "installed_plugins.json").write_text("not json")
        found = sources.plugins(self.home.root)
        self.assertEqual([(p["path"], p["modules"]) for p in found],
                         [(sources.NOT_RECORDED, sources.NOT_RECORDED)])


class SourcesRouteTests(studio_security.StudioSecurityFixture):
    def setUp(self):
        super().setUp()
        state = self.home / ".local" / "state" / "agent-harness"
        state.mkdir(parents=True, exist_ok=True)
        now = dt.datetime.now(dt.timezone.utc)
        rows = [attributed(now - dt.timedelta(days=n), {"rules/secrets": 50}, version="0.14.2")
                for n in (18, 11)]
        rows += [attributed(now - dt.timedelta(days=n), {"rules/secrets": 60}, version="0.15.0")
                 for n in (3, 1)]
        (state / "usage.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        _issued, status, headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        self.cookie_value = self.cookie(headers)

    def get(self, path, cookie=True):
        return self.request("GET", path, {"Cookie": self.cookie_value} if cookie else {})

    def test_the_route_answers_period_stamped_source_rows(self):
        status, headers, body = self.get(ROUTE)
        self.assertEqual(status, 200, body)
        self.assertEqual(headers.get_content_type(), "application/json")
        document = json.loads(body)
        self.assertEqual((set(document), document["surface"]), ({"surface", "rows"}, "sources"))
        core = [r for r in document["rows"] if r["item_id"] == "core"]
        self.assertEqual([(r["setting_label"], r["status"], r["period"]["end"] is None,
                           r["metrics"]["tokens_per_session"]) for r in core],
                         [("0.14.2", "measured", False, 50), ("0.15.0", "measuring", True, 60)])
        self.assertEqual(set(core[1]["metrics"]), {"sessions", "tokens_per_session", "modules",
                                                   "source", "root", "enabled"})

    def test_a_bad_query_is_400(self):
        status, _headers, body = self.get(ROUTE + "&days=0")
        self.assertEqual((status, json.loads(body)), (400, {"error": "invalid_request"}))

    def test_anonymous_is_refused_like_its_neighbours(self):
        status, _headers, body = self.get(ROUTE, cookie=False)
        self.assertEqual((status, json.loads(body)), (401, {"error": "unauthorized"}))
        status, _headers, _body = self.request("GET", ROUTE, {"Cookie": self.cookie_value},
                                               host="evil.localhost")
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: MIT
"""The Stances page's surface: one row per stance per variant period (AH-S373, #1325)."""
import datetime as dt
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_studio_security as studio_security  # noqa: E402
from harness_core.studio import periods  # noqa: E402
from harness_core.studio.surfaces import stances  # noqa: E402
from test_studio_periods import Home, NOW, days_ago, usage_row  # noqa: E402

ROUTE = "/api/harness/periods?surface=stances"


class StancesProviderTests(unittest.TestCase):
    def setUp(self):
        self.home = Home(self)

    def answer(self, unit=None):
        return periods.efficacy_rows("stances", unit, home=self.home.root, now=NOW)

    def test_items_carry_configured_stances_and_session_observations(self):
        self.home.config({"voice": "concise", "testing": "required"})
        rows = [usage_row(days_ago(4)), usage_row(days_ago(3), stances={"voice": "concise"}),
                usage_row(days_ago(2), stances_source="rescan")]
        self.home.usage(rows)
        found = stances.items(periods._context(self.home.root, 30, NOW))
        self.assertEqual(sorted(found), ["cost", "testing", "voice"])
        self.assertEqual((found["voice"]["label"], found["voice"]["current"]), ("Voice", "concise"))
        self.assertIsNone(found["cost"]["current"])
        # The rescanned row is skipped; a stance missing from a row is observed as unset.
        self.assertEqual([o[1] for o in found["cost"]["observations"]], ["balanced", "(unset)"])
        self.assertEqual([o[1] for o in found["testing"]["observations"]], ["(unset)", "(unset)"])

    def test_config_value_ignores_a_non_string_or_missing_stance(self):
        self.assertEqual(stances.config_value({"stances": {"voice": "concise"}}, "voice"), "concise")
        self.assertIsNone(stances.config_value({"stances": {"voice": ""}}, "voice"))
        self.assertIsNone(stances.config_value({"stances": ["voice"]}, "voice"))
        self.assertIsNone(stances.config_value({}, "voice"))

    def test_a_variant_switch_is_one_row_per_variant_period_with_its_usage(self):
        rows = [usage_row(days_ago(n), voice="answer-card", output=200, cache_read=300)
                for n in (20, 15)]
        rows += [usage_row(days_ago(n), voice="concise") for n in (3, 2, 1)]
        rows.append(usage_row(days_ago(2, 1), voice="concise", kind="subagent", output=50))
        self.home.usage(rows)
        answer = self.answer("voice")
        self.assertNotIn("note", answer)
        self.assertEqual([(r["setting_label"], r["status"], r["metrics"]) for r in answer["rows"]], [
            ("answer-card", "measured",
             {"sessions": 2, "agents": 0, "output_tokens": 400, "cache_read_share": 0.75}),
            ("concise", "measuring",
             {"sessions": 3, "agents": 1, "output_tokens": 350, "cache_read_share": 0.9}),
        ])
        before, after = answer["rows"]
        self.assertEqual(before["period"]["end"], after["period"]["start"])
        self.assertEqual(after["evals_href"], "/evals?surface=stances&item=voice")

    def test_unchanged_stances_answer_one_period_each_with_the_note(self):
        self.home.config({"voice": "concise", "cost": "balanced"})
        self.home.usage([usage_row(days_ago(n)) for n in (10, 5)])
        answer = self.answer()
        self.assertEqual(answer["note"], periods.NOTE)
        self.assertEqual([(r["item_id"], r["setting_label"], r["period"]["end"])
                          for r in answer["rows"]],
                         [("cost", "balanced", None), ("voice", "concise", None)])
        self.assertEqual({r["metrics"]["sessions"] for r in answer["rows"]}, {2})


class StancesRouteTests(studio_security.StudioSecurityFixture):
    def setUp(self):
        super().setUp()
        state = self.home / ".local" / "state" / "agent-harness"
        state.mkdir(parents=True, exist_ok=True)
        now = dt.datetime.now(dt.timezone.utc)
        rows = [usage_row(now - dt.timedelta(days=n), voice="answer-card") for n in (18, 11)]
        rows += [usage_row(now - dt.timedelta(days=n), voice="concise") for n in (3, 1)]
        (state / "usage.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        _issued, status, headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        self.cookie_value = self.cookie(headers)

    def get(self, path, cookie=True):
        return self.request("GET", path, {"Cookie": self.cookie_value} if cookie else {})

    def test_the_route_answers_period_stamped_stance_rows(self):
        status, headers, body = self.get(ROUTE)
        self.assertEqual(status, 200, body)
        self.assertEqual(headers.get_content_type(), "application/json")
        document = json.loads(body)
        self.assertEqual((set(document), document["surface"]), ({"surface", "rows"}, "stances"))
        voice = [r for r in document["rows"] if r["item_id"] == "voice"]
        self.assertEqual([(r["setting_label"], r["status"], r["period"]["end"] is None)
                          for r in voice],
                         [("answer-card", "measured", False), ("concise", "measuring", True)])
        self.assertEqual(set(voice[1]["metrics"]),
                         {"sessions", "agents", "output_tokens", "cache_read_share"})

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

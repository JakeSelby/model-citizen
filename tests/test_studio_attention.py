# SPDX-License-Identifier: MIT
"""Home's attention registry: item contract, ordering, the five-item cap, failing sources (AH-S380, #1332)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from harness_core.studio import attention  # noqa: E402


def item(source, key, severity="medium", at="2026-10-12T10:00:00Z", **extra):
    row = {"id": "%s:%s" % (source, key), "source": source, "kind": "thing", "severity": severity,
           "title": "Title " + key, "detail": "", "at": at,
           "action": {"label": "Open", "href": "/harness/rules"}}
    row.update(extra)
    return row


class AttentionTests(unittest.TestCase):
    def setUp(self):
        attention.sources()  # Load the package's sources now, so none registers mid-test.
        saved = dict(attention._REGISTRY)
        attention._REGISTRY.clear()
        self.addCleanup(lambda: (attention._REGISTRY.clear(), attention._REGISTRY.update(saved)))
        self.home = Path("/nonexistent-home")

    def test_the_queue_caps_at_five_and_reports_the_total(self):
        attention.register_attention("a", lambda home: [item("a", str(n)) for n in range(8)])
        result = attention.queue(self.home)
        self.assertEqual(len(result["items"]), 5)
        self.assertEqual(result["total"], 8)
        self.assertEqual(result["sources"], {"a": "ok"})
        self.assertEqual(attention.LIMIT, 5)

    def test_severity_orders_first_then_newest(self):
        attention.register_attention("a", lambda home: [
            item("a", "old-high", "high", "2026-10-01T00:00:00Z"), item("a", "low", "low", "2026-10-12T00:00:00Z"),
            item("a", "new-high", "high", "2026-10-11T00:00:00Z"), item("a", "medium", "medium", None)])
        ids = [i["id"] for i in attention.queue(self.home)["items"]]
        self.assertEqual(ids, ["a:new-high", "a:old-high", "a:medium", "a:low"])

    def test_a_raising_source_is_an_error_and_the_rest_still_show(self):
        def broken(home):
            raise RuntimeError("boom")
        attention.register_attention("bad", broken)
        attention.register_attention("good", lambda home: [item("good", "1")])
        result = attention.queue(self.home)
        self.assertEqual([i["id"] for i in result["items"]], ["good:1"])
        self.assertEqual(result["sources"], {"bad": "error", "good": "ok"})

    def test_a_malformed_item_drops_its_whole_source(self):
        cases = [
            item("m", "1", severity="urgent"),
            item("m", "1", action={"label": "Go", "href": "https://elsewhere.example"}),
            item("m", "1", action={"label": "Go", "href": "//elsewhere.example"}),
            item("m", "1", action={"label": "One", "href": "/a", "second": "/b"}),
            item("other", "1"),                       # id not under its source
            dict(item("m", "1"), source="other"),
            dict(item("m", "1"), title=""),
            "not a dict",
        ]
        for bad in cases:
            attention.register_attention("m", lambda home, bad=bad: [item("m", "ok"), bad])
            result = attention.queue(self.home)
            self.assertEqual((result["items"], result["sources"]), ([], {"m": "error"}), bad)

    def test_a_source_returning_a_non_list_is_an_error(self):
        attention.register_attention("m", lambda home: {"id": "m:1"})
        self.assertEqual(attention.queue(self.home)["sources"], {"m": "error"})

    def test_items_keep_only_contract_fields_and_duplicate_ids_count_once(self):
        attention.register_attention("a", lambda home: [item("a", "1", extra="x"), item("a", "1")])
        result = attention.queue(self.home)
        self.assertEqual(result["total"], 1)
        self.assertEqual(sorted(result["items"][0]), sorted(attention.FIELDS))

    def test_the_source_receives_the_home_it_was_asked_for(self):
        seen = []
        attention.register_attention("a", lambda home: seen.append(home) or [])
        attention.queue("/tmp/some-home")
        self.assertEqual(seen, [Path("/tmp/some-home")])

    def test_a_name_is_required(self):
        with self.assertRaises(ValueError):
            attention.register_attention("", lambda home: [])

    def test_sources_load_from_the_attention_sources_package(self):
        attention._LOADED = False
        self.addCleanup(setattr, attention, "_LOADED", True)
        attention.sources()
        self.assertTrue(attention._LOADED)


if __name__ == "__main__":
    unittest.main()

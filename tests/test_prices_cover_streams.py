# SPDX-License-Identifier: MIT
"""Every model a recorded stream names has a price, and one that has none is unknown, never 0.

Run: python3 -m unittest tests.test_prices_cover_streams
"""
import importlib.machinery
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures"


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


pricing = _load("pricing_cover", REPO / "policy" / "hooks" / "pricing.py")

# Ids the fixtures invent, which no provider bills: a test's own rate table, a stand-in for a
# runtime's model, the judge version a decision record names, and the id the Studio's spend
# fixture uses to show an unpriced model reading unknown. Any other id must be priced.
PLACEHOLDERS = {"claude-test", "model-a", "placeholder-model", "jev-1.13.0", "gpt-unpriced-model"}


def _records(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        yield json.loads(text)
        return
    except ValueError:
        pass
    for line in text.splitlines():
        try:
            yield json.loads(line)
        except ValueError:
            continue


def _models(value):
    """Every model id under a `model` key or a `modelUsage` map, at any depth."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "model" and isinstance(item, str):
                yield item
            elif key == "modelUsage" and isinstance(item, dict):
                for name in item:
                    yield name
            yield from _models(item)
    elif isinstance(value, list):
        for item in value:
            yield from _models(item)


def stream_models(root):
    """`{model id: first file naming it}` over every JSON and JSONL file under `root`."""
    seen = {}
    for path in sorted(root.rglob("*")):
        if path.suffix not in (".json", ".jsonl") or not path.is_file():
            continue
        for record in _records(path):
            for name in _models(record):
                if name and not name.startswith("<") and name not in PLACEHOLDERS:
                    seen.setdefault(name, path.relative_to(root).as_posix())
    return seen


def unpriced(models, table):
    return sorted(name for name in models if pricing.price_for(table, name) is None)


class RecordedStreamsArePricedTests(unittest.TestCase):
    def test_every_model_in_a_recorded_fixture_stream_has_a_price(self):
        table = pricing.load_prices({})
        models = stream_models(FIXTURES)
        # The scan has to find the provider ids the fixtures do hold, or an empty result proves
        # nothing about the table.
        self.assertIn("claude-haiku-4-5-20251001", models)
        self.assertIn("claude-sonnet-5", models)
        missing = unpriced(models, table)
        self.assertEqual(missing, [], ["%s (first seen in %s)" % (m, models[m]) for m in missing])

    def test_the_scan_names_a_model_the_table_lacks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "run.jsonl").write_text(
                json.dumps({"type": "system", "model": "claude-haiku-4-5-20251001"}) + "\n"
                + json.dumps({"type": "assistant", "message": {"model": "claude-opus-9-9"}}) + "\n"
                + json.dumps({"type": "result", "modelUsage": {"claude-sonnet-9-9": {}}}) + "\n",
                encoding="utf-8")
            models = stream_models(root)
        self.assertEqual(unpriced(models, pricing.load_prices({})),
                         ["claude-opus-9-9", "claude-sonnet-9-9"])


class CurrentModelsTests(unittest.TestCase):
    """The ids the provider lists today, at the figures its pricing page states."""

    def test_each_current_id_prices_at_its_own_entry(self):
        table = pricing.load_prices({})
        for name, expected in (
                ("claude-fable-5-1", (10.0, 50.0, 0.25, 12.5, 20.0)),
                ("claude-opus-5-5", (4.0, 20.0, 0.2, 5.0, 8.0)),
                ("claude-sonnet-5-5", (2.0, 10.0, 0.2, 2.5, 4.0)),
                ("claude-haiku-4-5-20251001", (1.0, 5.0, 0.1, 1.25, 2.0))):
            with self.subTest(model=name):
                rate = pricing.price_for(table, name)
                self.assertIsNotNone(rate)
                self.assertEqual((rate["input"], rate["output"], rate["cache_read"],
                                  rate["cache_write_5m"], rate["cache_write_1h"]), expected)

    def test_opus_5_5_is_not_billed_at_the_opus_5_rate(self):
        # The regression: with no entry of its own, Opus 5.5 was either unpriced or, under a
        # prefix lookup, charged at Opus 5's higher rate.
        table = pricing.load_prices({})
        self.assertNotEqual(pricing.price_for(table, "claude-opus-5-5"),
                            pricing.price_for(table, "claude-opus-5"))


class UnpricedIsUnknownTests(unittest.TestCase):
    def row(self, model):
        return {"kind": "session", "runtime": "claude-code", "session_id": model,
                "models": [model], "input": 1000, "output": 1000, "cache_read": 0,
                "cache_write": 0}

    def test_an_unpriced_model_is_none_beside_a_priced_one(self):
        table = pricing.load_prices({})
        result = pricing.priced([self.row("claude-opus-5-5"), self.row("claude-opus-9-9")], table)
        self.assertAlmostEqual(result[0][0], (1000 * 4.0 + 1000 * 20.0) / 1e6)
        self.assertEqual(result[0][1], "2026-10-03")
        self.assertEqual(result[1], (None, ""))

    def test_a_breakdown_with_one_unpriced_part_is_unknown_not_partial(self):
        table = pricing.load_prices({})
        part = {"input": 1000, "output": 0, "cache_read": 0, "cache_write": 0}
        row = dict(self.row("claude-opus-5-5"), by_model={"claude-opus-5-5": part,
                                                          "claude-opus-9-9": part})
        self.assertIsNone(pricing.row_cost(row, table))


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: MIT
"""The adherence report: measured rates with Wilson intervals, and the soft if-followed estimate."""
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

from test_usage import harness

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))
sys.path.insert(0, str(REPO / "scripts"))

from harness_core import intervals, soft_estimates as soft  # noqa: E402
import replay_stats  # noqa: E402

ADHERENCE = harness.load_hook_module("adherence", required=True)
PRICING = harness.load_hook_module("pricing", required=True)
FEED = harness.load_hook_module("usage-feed", required=True)
POSTURE = harness.load_hook_module("posture", required=True)

# Two models and one unpriced: dollars per million tokens, chosen so the arithmetic is readable.
TABLE = {"model-a": {"input": 1.0, "output": 2.0, "cache_read": 0.1, "cache_write": 1.25},
         "model-b": {"input": 2.0, "output": 4.0, "cache_read": 0.2, "cache_write": 2.5,
                     "cache_write_5m": 2.5, "cache_write_1h": 4.0}}


def context(usage):
    return FEED._context(usage)


def call(prompt, read, write, model="model-a", inp=10000, out=1000, rewritten=None, cause=None,
         one_hour=None):
    return {"prompt": prompt, "model": model, "input": inp, "output": out, "cache_read": read,
            "cache_write": write, "cache_write_1h": one_hour,
            "cache_write_5m": None if one_hour is None else write - one_hour,
            "rewritten": rewritten, "cause": cause}


# B = 10k (first call), C = 300k at prompt 2, so K = 290k.
POSITIVE = [call(1, 0, 10000, inp=0, out=0), call(2, 10000, 290000, inp=0, out=0),
            call(3, 300000, 10000), call(4, 310000, 10000)]
# B = 100k, C = 300k, so K = 200k.
NEGATIVE = [call(1, 0, 100000, inp=0, out=0), call(2, 100000, 200000, inp=0, out=0),
            call(3, 300000, 50000), call(4, 350000, 50000)]


def emitted(aid, session="s1", turn=2, fingerprint="fp1", kind="fresh-session",
            ts="2026-09-20T00:00:00Z"):
    return {"kind": "emitted", "adherence_id": aid, "recommendation": kind,
            "module": "hooks/usage-feed", "session_id": session, "turn": turn, "ts": ts,
            "profile_fingerprint": fingerprint, "schema_version": 1}


def response(aid, outcome, kind="fresh-session"):
    return {"kind": "response", "adherence_id": aid, "recommendation": kind,
            "outcome": outcome, "schema_version": 1}


def ledger(followed=7, not_followed=3, unknown=2, pending=1, fingerprint="fp1", prefix="a"):
    rows, index = [], 0
    for outcome, count in (("followed", followed), ("not_followed", not_followed),
                           ("unknown", unknown), (None, pending)):
        for _ in range(count):
            index += 1
            aid = "%s%d" % (prefix, index)
            rows.append(emitted(aid, session="s%d" % index, fingerprint=fingerprint))
            if outcome:
                rows.append(response(aid, outcome))
    return rows


class Stub(object):
    """An adherence module with one more kind, which has no estimator."""

    def __init__(self):
        self.KINDS = dict(ADHERENCE.KINDS, **{"other-advice": {}})
        self.OUTCOMES = ADHERENCE.OUTCOMES
        self.FINGERPRINT_KEY = ADHERENCE.FINGERPRINT_KEY
        self.responses = ADHERENCE.responses


def run(rows, sessions, adherence=ADHERENCE):
    return soft.report(rows, adherence, intervals, sessions.get, TABLE, PRICING, context)


def fresh(groups):
    """The `fresh-session` groups: every kind the adherence log knows gets a group of its own."""
    return [group for group in groups if group.get("kind") == "fresh-session"]


def rendered(result):
    lines = []
    soft.render(result, lines.append)
    return lines


class LabelTests(unittest.TestCase):
    def test_soft_label_matches_posture(self):
        self.assertEqual(soft.SOFT_ESTIMATE, POSTURE.SOFT_ESTIMATE)

    def test_measured_plus_soft_raises(self):
        with self.assertRaises(soft.MixedLabels):
            soft.total([soft.Figure(1.0, soft.MEASURED),
                        soft.Figure(2.0, soft.SOFT_ESTIMATE, estimator=soft.ESTIMATOR)])

    def test_soft_from_two_estimators_raises(self):
        with self.assertRaises(soft.MixedLabels):
            soft.total([soft.Figure(1.0, soft.SOFT_ESTIMATE, estimator="one"),
                        soft.Figure(2.0, soft.SOFT_ESTIMATE, estimator="two")])

    def test_same_label_sums_and_unknown_stays_unknown(self):
        both = soft.total([soft.Figure(1.0, soft.MEASURED, n=1),
                           soft.Figure(2.5, soft.MEASURED, n=2)])
        self.assertEqual((both.value, both.label, both.n), (3.5, soft.MEASURED, 3))
        unknown = soft.total([soft.Figure(1.0, soft.MEASURED), soft.Figure(None, soft.MEASURED)])
        self.assertIsNone(unknown.value)

    def test_soft_estimate_must_name_its_estimator(self):
        with self.assertRaises(ValueError):
            soft.Figure(1.0, soft.SOFT_ESTIMATE)
        with self.assertRaises(ValueError):
            soft.Figure(1.0, "roughly")

    def test_renderer_refuses_a_bare_number(self):
        with self.assertRaises(TypeError):
            soft.show(3.0)


class IntervalTests(unittest.TestCase):
    def test_replay_stats_keeps_the_moved_wilson(self):
        self.assertIs(replay_stats.wilson, intervals.wilson)
        self.assertEqual(replay_stats.Z95, intervals.Z95)


class RateTests(unittest.TestCase):
    def test_seven_of_ten_matches_wilson(self):
        """7 followed, 3 not followed, 2 unknown, 1 pending: the rate is 7/10 with Wilson(7, 10)."""
        group, = soft.adherence_groups(ledger(), ADHERENCE, intervals)
        rate = group["figures"]["rate"]
        self.assertEqual(rate.label, soft.MEASURED)
        self.assertAlmostEqual(rate.value, 0.7)
        self.assertEqual(rate.n, 10)
        self.assertEqual(rate.interval, replay_stats.wilson(7, 10))
        self.assertEqual(rate.fingerprint, "fp1")
        self.assertEqual(rate.qualifiers, ())

    def test_counts_equal_adherence_rates(self):
        rows = ledger() + ledger(followed=1, not_followed=0, unknown=0, pending=0,
                                 fingerprint="fp2", prefix="b")
        groups = soft.adherence_groups(rows, ADHERENCE, intervals)
        expected = ADHERENCE.rates(rows)["fresh-session"]
        for name in soft.COUNTS:
            summed = soft.total(group["figures"][name] for group in groups)
            self.assertEqual(summed.value, expected[name], name)

    def test_no_judged_emissions_prints_no_rate(self):
        group, = soft.adherence_groups(ledger(followed=0, not_followed=0, unknown=3, pending=0),
                                       ADHERENCE, intervals)
        rate = group["figures"]["rate"]
        self.assertIsNone(rate.value)
        self.assertIsNone(rate.interval)
        self.assertEqual(soft.show(rate, "rate"), "no judged emissions [measured]")

    def test_no_fingerprint_is_unattributed(self):
        group, = soft.adherence_groups(ledger(fingerprint=""), ADHERENCE, intervals)
        self.assertIsNone(group["fingerprint"])
        self.assertEqual(group["figures"]["rate"].qualifiers, (soft.UNATTRIBUTED,))

    def test_cutoff_drops_older_emissions(self):
        rows = ledger() + [emitted("old", ts="2026-08-01T00:00:00Z")]
        group, = soft.adherence_groups(rows, ADHERENCE, intervals, cutoff="2026-09-01T00:00:00Z")
        self.assertEqual(group["figures"]["emitted"].value, 13)


class AnchorTests(unittest.TestCase):
    def setUp(self):
        # Twelve prompts; prompt 7 has two calls and prompt 9 none.
        self.calls = []
        for prompt in range(1, 13):
            if prompt == 9:
                continue
            self.calls.append(call(prompt, prompt * 1000, 100))
            if prompt == 7:
                self.calls.append(call(7, 7500, 100))

    def test_turn_seven_maps_to_its_last_call(self):
        self.assertEqual(soft.anchor(self.calls, 7)["cache_read"], 7500)

    def test_turn_with_no_call_maps_to_the_one_before(self):
        self.assertEqual(soft.anchor(self.calls, 9)["prompt"], 8)

    def test_turn_before_any_call_has_no_anchor(self):
        self.assertIsNone(soft.anchor(self.calls, 0))

    def test_context_uses_the_feeds_formula(self):
        one = call(1, 300, 20, inp=5)
        self.assertEqual(context(soft.context_of(one)), 325)


class EstimatorTests(unittest.TestCase):
    def test_positive_saving(self):
        """K = 300k - 10k = 290k.

        Prompt 3, actual: 10k*1 + 1k*2 + 300k*0.1 + 10k*1.25 = 54,500 -> $0.0545.
        Cold start: reads 0, writes 10k + (300k - 290k) = 20k: 10,000 + 2,000 + 25,000 -> $0.037.
        Prompt 4, actual: 10,000 + 2,000 + 31,000 + 12,500 -> $0.0555.
        Reads 310k - 290k = 20k: 10,000 + 2,000 + 2,000 + 12,500 -> $0.0265.
        Saving: (0.0545 + 0.0555) - (0.037 + 0.0265) = $0.0465.
        """
        result = soft.reprice(POSITIVE, 2, TABLE, PRICING, context)
        self.assertEqual(result["status"], "priced")
        self.assertAlmostEqual(result["actual"], 0.110)
        self.assertAlmostEqual(result["counterfactual"], 0.0635)
        self.assertAlmostEqual(result["saving"], 0.0465)

    def test_negative_saving_is_kept_signed(self):
        """K = 300k - 100k = 200k.

        Prompt 3, actual 10,000 + 2,000 + 30,000 + 62,500 -> $0.1045; cold start writes
        50k + 100k = 150k: 10,000 + 2,000 + 187,500 -> $0.1995.
        Prompt 4, actual 10,000 + 2,000 + 35,000 + 62,500 -> $0.1095; reads 150k:
        10,000 + 2,000 + 15,000 + 62,500 -> $0.0895.
        Saving: 0.214 - 0.289 = -$0.075.
        """
        result = soft.reprice(NEGATIVE, 2, TABLE, PRICING, context)
        self.assertAlmostEqual(result["saving"], -0.075)

    def test_compaction_ends_the_horizon(self):
        tail = [call(5, 0, 400000, cause="compaction"), call(6, 400000, 10000)]
        result = soft.reprice(POSITIVE + tail, 2, TABLE, PRICING, context)
        self.assertAlmostEqual(result["saving"], 0.0465)

    def test_rebuild_writes_less_by_the_carried_context(self):
        """Prompt 4 is a rebuild that rewrote 5k: it writes 10k - min(290k, 5k) = 5k and keeps its
        310k read, so its counterfactual is 10,000 + 2,000 + 31,000 + 6,250 -> $0.04925, a saving
        of $0.00625 there; with prompt 3's $0.0175 the total is $0.02375."""
        calls = POSITIVE[:3] + [call(4, 310000, 10000, rewritten=5000, cause="date changed")]
        result = soft.reprice(calls, 2, TABLE, PRICING, context)
        self.assertAlmostEqual(result["saving"], 0.02375)

    def test_tier_split_is_kept_in_proportion(self):
        """On model-b, a cold start writing 20k with a 50% one-hour share bills 10k at 2.5 and 10k
        at 4.0: 20,000 + 4,000 + 65,000 -> $0.089 against an actual of
        20,000 + 4,000 + 300k*0.2 + 5k*2.5 + 5k*4.0 = 116,500 -> $0.1165."""
        calls = [call(1, 0, 10000, inp=0, out=0, model="model-b"),
                 call(2, 10000, 290000, inp=0, out=0, model="model-b"),
                 call(3, 300000, 10000, model="model-b", one_hour=5000)]
        result = soft.reprice(calls, 2, TABLE, PRICING, context)
        self.assertAlmostEqual(result["actual"], 0.1165)
        self.assertAlmostEqual(result["counterfactual"], 0.089)

    def test_unpriced_model_is_counted_apart(self):
        calls = POSITIVE[:3] + [call(4, 310000, 10000, model="model-x")]
        self.assertEqual(soft.reprice(calls, 2, TABLE, PRICING, context), {"status": "unpriced"})

    def test_no_call_at_or_before_the_turn_is_missing(self):
        self.assertEqual(soft.reprice(POSITIVE[2:], 2, TABLE, PRICING, context),
                         {"status": "missing"})
        self.assertEqual(soft.reprice([], 2, TABLE, PRICING, context), {"status": "missing"})


class ReportTests(unittest.TestCase):
    def rows(self):
        return [emitted("n1", session="s1"), response("n1", "not_followed"),
                emitted("n2", session="s2"), response("n2", "not_followed"),
                emitted("n3", session="gone"), response("n3", "not_followed"),
                emitted("n4", session="s4"), response("n4", "not_followed"),
                emitted("f1", session="s1"), response("f1", "followed"),
                emitted("u1", session="s1"), response("u1", "unknown"),
                emitted("p1", session="s1")]

    def sessions(self):
        unpriced = POSITIVE[:3] + [call(4, 310000, 10000, model="model-x")]
        return {"s1": POSITIVE, "s2": NEGATIVE, "s4": unpriced}

    def test_only_not_followed_emissions_are_priced(self):
        group, = fresh(run(self.rows(), self.sessions())["if_followed"])
        figures = group["figures"]
        self.assertEqual(group["estimator"], soft.ESTIMATOR)
        self.assertEqual(group["assumption"], soft.ASSUMPTION)
        self.assertEqual(figures["priced"].value, 2)
        self.assertEqual(figures["unpriced"].value, 1)
        self.assertEqual(figures["transcript_missing"].value, 1)
        self.assertEqual(figures["saving"].label, soft.SOFT_ESTIMATE)
        self.assertEqual(figures["saving"].estimator, soft.ESTIMATOR)
        self.assertAlmostEqual(figures["saving"].value, 0.0465 - 0.075)
        self.assertAlmostEqual(figures["mean_saving"].value, (0.0465 - 0.075) / 2)

    def test_a_kind_without_an_estimator_is_unmeasured(self):
        groups = run(self.rows(), self.sessions(), adherence=Stub())["if_followed"]
        other = [group for group in groups if group["kind"] == "other-advice"][0]
        self.assertEqual(other["figures"]["saving"].label, soft.UNMEASURED)
        self.assertEqual(other["figures"]["saving"].note, "no estimator")

    def test_nothing_priced_is_unknown_not_zero(self):
        rows = [emitted("n1", session="gone"), response("n1", "not_followed")]
        group, = fresh(run(rows, {})["if_followed"])
        self.assertIsNone(group["figures"]["saving"].value)
        self.assertEqual(group["figures"]["transcript_missing"].value, 1)

    def test_every_rendered_number_carries_a_label(self):
        lines = rendered(run(self.rows(), self.sessions()))
        self.assertIn("Adherence (Measured)", lines)
        self.assertIn("If followed (Soft estimate: carried-context reprice)", lines)
        self.assertIn("    saving -$0.03 n=2 [soft estimate]", lines)
        self.assertEqual(lines[-1], soft.FOOTER)
        for line in lines:
            # The footer's item number and a fingerprint's hex digits are identifiers, not figures.
            text = line.replace(soft.FOOTER, "").replace("fingerprint fp1", "")
            if any(ch.isdigit() for ch in text):
                self.assertRegex(text, r"\[(measured|soft estimate|unmeasured)")
        self.assertFalse([line for line in lines if "total" in line.lower()])

    def test_sections_cannot_be_summed(self):
        result = run(self.rows(), self.sessions())
        rate = result["adherence"][0]["figures"]["rate"]
        with self.assertRaises(soft.MixedLabels):
            soft.total([rate, result["if_followed"][0]["figures"]["saving"]])


class JsonTests(unittest.TestCase):
    def document(self, rows, sessions):
        data = soft.summary(run(rows, sessions))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = harness.usage_json("adherence", "adherence", 30, data["groups"],
                                        labels=data["labels"], footer=data["footer"])
        self.assertEqual(status, 0)
        return json.loads(out.getvalue())

    def test_follows_the_usage_json_contract_with_labelled_figures(self):
        rows = ledger(followed=0, not_followed=1, unknown=0, pending=0)
        document = self.document(rows, {})
        self.assertEqual(document["schema_version"], harness.USAGE_JSON_VERSION)
        self.assertEqual((document["report"], document["by"], document["days"]),
                         ("adherence", "adherence", 30))
        sections = [group["section"] for group in fresh(document["groups"])]
        self.assertEqual(sections, ["adherence", "if_followed"])
        for group in document["groups"]:
            for figure in group["figures"].values():
                self.assertIn(figure["label"], soft.LABELS)
        adherence_group, estimate = fresh(document["groups"])
        self.assertEqual(adherence_group["figures"]["rate"]["value"], 0.0)
        self.assertIsNotNone(adherence_group["figures"]["rate"]["interval"])
        # The one not-followed emission has no transcript: its saving is null, never 0.
        self.assertIsNone(estimate["figures"]["saving"]["value"])
        self.assertIsNone(estimate["figures"]["mean_saving"]["value"])
        self.assertEqual(estimate["figures"]["saving"]["estimator"], soft.ESTIMATOR)

    def test_no_judged_rate_is_null(self):
        document = self.document(ledger(followed=0, not_followed=0, unknown=2, pending=0), {})
        rate = document["groups"][0]["figures"]["rate"]
        self.assertIsNone(rate["value"])
        self.assertIsNone(rate["interval"])


if __name__ == "__main__":
    unittest.main()

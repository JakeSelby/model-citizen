"""The replay pair's saved rows and the guards around them (#754): pre-launch parity over arm records
read back from disk, a pair recognised by its `ablation` whichever arms ran, a row schema that
names its factors as a list, plain replay rows that summarise as before, and a decision call the
closed egress blocked labelled as such. Every launch is a fake; no test builds an image or calls a
model."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from test_cost_bench import BENCH, PRICES, TASK, arm_record, options
from test_replay_pair import (FACTOR, PAIR, PRICING, PairLaunch, attempt, decision, ledger_text, pair_options,
                              pair_rows, run_output)


def from_disk(directory, record, **declaration):
    """`record` as a caller would rebuild it from the declaration and manifest files
    `replay_arms.write_record` leaves in the arms directory, with `declaration` overrides."""
    decl = dict(record["declaration"], **declaration)
    paths = BENCH.arms.write_record(directory, record["image"], decl, record["manifest"])
    decl = json.loads(paths["declaration"].read_text(encoding="utf-8"))
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    return dict(record, declaration=decl, declaration_sha256=BENCH.arms.digest(decl), manifest=manifest,
                manifest_sha256=BENCH.arms.digest(manifest), paths={k: str(v) for k, v in paths.items()})


def with_manifest(record, **manifest):
    manifest = dict(record["manifest"], **manifest)
    return dict(record, manifest=manifest, manifest_sha256=BENCH.arms.digest(manifest))


class DiskRecordParityTests(unittest.TestCase):
    """The pre-launch parity check, driven through `replay` with harness arms read back from disk."""

    def replay(self, tmp, reference, treatment):
        opts = pair_options(tmp)
        opts["arms"].update(reference=reference, treatment=treatment)
        launch = PairLaunch([run_output("s-bare"), run_output("s-ref"), run_output("s-treat")],
                            [ledger_text(), ledger_text()])
        return launch, opts

    def test_two_identical_records_from_disk_run_the_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = arm_record("harness")
            launch, opts = self.replay(tmp, from_disk(Path(tmp) / "a", harness), from_disk(Path(tmp) / "b", harness))
            rows, stopped = BENCH.replay([TASK], opts, launch)
        self.assertEqual((len(rows), stopped), (3, False))

    def test_a_manifest_digest_difference_is_refused_before_any_launch(self):
        """A file inside the harness root passes the arm checks against bare, so parity decides."""
        with tempfile.TemporaryDirectory() as tmp:
            harness = arm_record("harness")
            extra = harness["manifest"]["entries"] + [{"path": "harness:NOTES.md", "kind": "file"}]
            treatment = from_disk(Path(tmp) / "b", with_manifest(harness, entries=extra))
            launch, opts = self.replay(tmp, from_disk(Path(tmp) / "a", harness), treatment)
            with self.assertRaises(SystemExit) as caught:
                BENCH.replay([TASK], opts, launch)
        message = str(caught.exception)
        self.assertIn("replay-pair: refusing the pair: the harness arms differ by more than %s" % FACTOR, message)
        self.assertIn("manifest_sha256: reference ", message)
        self.assertEqual((launch.calls, launch.probes), ([], []))

    def test_an_effort_difference_is_refused_before_any_launch_and_parity_names_it(self):
        """The arm checks against bare refuse it first; parity, asked of the same records, names
        the command line and the declaration digest."""
        with tempfile.TemporaryDirectory() as tmp:
            harness = arm_record("harness")
            reference = from_disk(Path(tmp) / "a", harness)
            treatment = from_disk(Path(tmp) / "b", harness, effort="low")
            launch, opts = self.replay(tmp, reference, treatment)
            with self.assertRaises(SystemExit) as caught:
                BENCH.replay([TASK], opts, launch)
            reasons = PAIR.parity(BENCH.arm_spec("reference", opts), BENCH.arm_spec("treatment", opts), FACTOR)
        self.assertIn("effort", str(caught.exception))
        self.assertEqual((launch.calls, launch.probes), ([], []))
        self.assertEqual([r.split(":")[0] for r in reasons], ["argv", "declaration_sha256"])
        self.assertIn("'low'", reasons[0])


class StoppedPairTests(unittest.TestCase):
    """A pair stopped at its spend cap is still a pair: its report names what did not run."""

    def stopped(self, spend_cap):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "out"
            out_dir.mkdir()
            launch = PairLaunch([run_output("s-bare"), run_output("s-ref")],
                                [ledger_text(decision("s-ref", model="claude-haiku-4-5"))])
            rows, stopped = BENCH.replay([TASK], pair_options(tmp, decisions=out_dir / PAIR.DECISIONS,
                                                              spend_cap=spend_cap), launch,
                                         out=out_dir / BENCH.RESULTS)
            self.assertTrue(stopped)
            reports = []
            for extra in (["--json"], []):
                printed, errors = io.StringIO(), io.StringIO()
                with redirect_stdout(printed), redirect_stderr(errors):
                    code = BENCH.main(["summarise", "--results", str(out_dir), "--resamples", "50"] + extra)
                self.assertEqual(code, 0, errors.getvalue())
                reports.append(printed.getvalue())
        return rows, json.loads(reports[0]), reports[1]

    def test_a_pair_stopped_after_one_arm(self):
        rows, result, text = self.stopped(2.0)
        self.assertEqual([r["arm"] for r in rows], ["bare"])
        self.assertEqual(result["incomplete"], ["demo rep 1: reference, treatment did not run"])
        self.assertEqual(result["arms"]["reference"]["with_decisions_undefined"], "the arm did not run")
        self.assertEqual(result["arms"]["bare"]["attempts"], 1)
        self.assertIn("incomplete trial, the run stopped before it: demo rep 1: reference, treatment did not run", text)
        self.assertNotIn("verdict", text)

    def test_a_pair_stopped_after_two_arms_still_reports_its_decision_costs(self):
        rows, result, text = self.stopped(2.5)
        self.assertEqual([r["arm"] for r in rows], ["bare", "reference"])
        self.assertEqual(result["incomplete"], ["demo rep 1: treatment did not run"])
        reference = result["arms"]["reference"]
        self.assertEqual(reference["decisions"]["calls"], 1)
        self.assertGreater(reference["decisions"]["priced_usd"], 0)
        self.assertTrue(result["parity"]["ok"])  # an unfinished trial compares nothing; it is not refused
        self.assertIn("  reference: 1/1 passed", text)


class RowSchemaTests(unittest.TestCase):
    """The design travels in `ablation.factors` and `selection`; rows carry no scalar `factor`."""

    def test_replayed_rows_name_their_factors_as_a_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = PairLaunch([run_output("s-bare"), run_output("s-ref"), run_output("s-treat")],
                                [ledger_text(), ledger_text()])
            rows, _ = BENCH.replay([TASK], pair_options(tmp), launch)
        for row in rows:
            self.assertNotIn("factor", row)
            self.assertEqual(row["ablation"]["factors"], [FACTOR])
        self.assertTrue(PAIR.is_pair(rows))

    def summarise(self, rows):
        decisions = {(r["task"], r["arm"], r["rep"]): [] for r in rows if r["arm"] != "bare"}
        return PAIR.summarise(rows, decisions, PRICES, resamples=50, pricing=PRICING)

    def test_rows_with_the_earlier_scalar_factor_still_render(self):
        rows = []
        for row in pair_rows(1, 2):
            ablation = {k: v for k, v in row["ablation"].items() if k != "factors"}
            rows.append(dict(row, ablation=ablation, factor=FACTOR))
        result = self.summarise(rows)
        self.assertEqual(result["factors"], [FACTOR])
        self.assertTrue(PAIR.render(result).startswith(
            "Pair delegation-off: factor %s, reference None, treatment 'off';" % FACTOR))

    def test_a_second_factor_needs_no_new_row_keys(self):
        second = "HARNESS_STANCE_COST"
        rows = []
        for row in pair_rows(1, 2):
            selection = dict(row["selection"], **({second: "frugal"} if row["arm"] == "treatment" else
                                                  {second: None} if row["arm"] == "reference" else {}))
            rows.append(dict(row, ablation=dict(row["ablation"], factors=[FACTOR, second]), selection=selection))
        self.assertEqual(set(rows[0]), set(pair_rows(1, 1)[0]))
        self.assertTrue(PAIR.is_pair(rows))
        result = self.summarise(rows)
        self.assertEqual(result["factors"], [FACTOR, second])
        self.assertIn("treatment {%s: 'off', %s: 'frugal'}" % (FACTOR, second), PAIR.render(result))

    def test_rows_without_an_ablation_are_no_pair(self):
        self.assertFalse(PAIR.is_pair([{"arm": "bare"}, {"arm": "harness"}]))
        self.assertFalse(PAIR.is_pair([]))


class PlainSummaryTests(unittest.TestCase):
    """Plain replay rows gained `session_ids`, `respawns_up` and `spawns_unranked`; the plain
    report reads none of them."""

    def report(self, tmp, name, rows, extra):
        path = Path(tmp) / name
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        printed = io.StringIO()
        with redirect_stdout(printed):
            code = BENCH.main(["summarise", "--results", str(path), "--resamples", "50"] + extra)
        self.assertEqual(code, 0)
        return printed.getvalue()

    def test_the_new_fields_leave_the_plain_report_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = PairLaunch([run_output("s%d" % n) for n in range(10)])
            rows, _ = BENCH.replay([TASK], options(tmp, reps=5), launch)
            self.assertEqual({r["arm"] for r in rows}, {"bare", "harness"})
            for row in rows:
                self.assertIn("session_ids", row)
                self.assertIn("respawns_up", row)
                self.assertIn("spawns_unranked", row)
            fields = ("session_ids", "respawns_up", "spawns_unranked")
            stripped = [{k: v for k, v in r.items() if k not in fields} for r in rows]
            for extra in ([], ["--json"]):
                with_fields = self.report(tmp, "with.jsonl", rows, extra)
                self.assertEqual(with_fields, self.report(tmp, "without.jsonl", stripped, extra), extra)
                self.assertNotIn("respawn", with_fields)
        self.assertEqual(BENCH.summarise(rows), BENCH.summarise(stripped))


class EgressLabelTests(unittest.TestCase):
    """A decision call the closed egress blocked is named as blocked, not only as partial."""

    def test_a_network_failure_row_is_labelled_blocked_by_egress(self):
        blocked = dict(decision("s1", partial=True), status="unavailable", error="network")
        timeout = dict(decision("s1", partial=True, point="review"), status="unavailable", error="timeout")
        rows = [attempt("bare"), attempt("reference"), attempt("treatment")]
        decisions = {("demo", "treatment", 1): [blocked, timeout], ("demo", "reference", 1): []}
        result = PAIR.summarise(rows, decisions, PRICES, resamples=50, pricing=PRICING)
        reasons = [u["reason"] for u in result["arms"]["treatment"]["decisions"]["unpriced"]]
        self.assertEqual(reasons, ["partial: blocked by egress (unavailable: network)",
                                   "partial (unavailable: timeout)"])
        self.assertIn("point routing, model claude-test: partial: blocked by egress", PAIR.render(result))
        self.assertIsNone(result["arms"]["treatment"]["cost_of_pass_with_decisions"])


if __name__ == "__main__":
    unittest.main()

"""Two finished replay targets compared with the engine's paired intervals (AH-S307, #990).

Each side is a target of a finished replay that went through `replay.execute`; the comparison
pairs the two harness arms by task and hands them to `replay_stats.compare`. The route, the
`citizen runs compare --json` command and the engine called directly on the same rows must agree,
and the committed fixture `studio/tests/fixtures/compare.json`, which
`studio/tests/compare.test.ts` feeds through the display, is the route's payload. Regenerate it with:

    python3 tests/test_studio_compare.py --write
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_harness import REPO, harness  # noqa: E402
from harness_core.studio import compare, drafts, replay, runs, server  # noqa: E402
from test_replay_stats import rows_for  # noqa: E402
from test_studio_replay import write_native_result  # noqa: E402
import test_studio_security as studio_security  # noqa: E402

FIXTURE = REPO / "studio" / "tests" / "fixtures" / "compare.json"
FIRST_RUN = "00000000-0000-4000-8000-000000000001"
SECOND_RUN = "00000000-0000-4000-8000-000000000002"
BASE_REV, CANDIDATE_REV, OTHER_REV = "a" * 40, "b" * 40, "c" * 40


def spec(costs, tasks=("a", "b", "c"), passed=True):
    """Harness trials at `costs[task]`, beside a bare arm at 1.0 that always passes."""
    return {task: {"bare": [(True, 1.0)] * len(costs[task]),
                   "harness": [(passed, cost) for cost in costs[task]]} for task in tasks}


# Target 1 of the first run is the base; target 2 is cheaper on every task, so the engine reads
# its cost lower. Target 1 of the second run is cheaper on two tasks and dearer on the third: its
# point estimate is below 1.0 but the interval spans it.
BASE = spec({"a": [1.0, 1.0], "b": [1.0, 1.0], "c": [1.0, 1.0]})
CHEAPER = spec({"a": [0.5, 0.52], "b": [0.55, 0.5], "c": [0.6, 0.58]})
NOISY = spec({"a": [0.4, 0.5], "b": [1.5, 1.4], "c": [0.6, 0.7]})


def target(kind, ref, revision):
    return {"kind": kind, "ref": ref, "revision": revision, "version": None,
            "draft": ref if kind == "draft" else None,
            "config_digest": replay.DEFAULT_CONFIG_DIGEST if kind == "draft" else None}


def request(targets, **changes):
    value = {"targets": targets, "model": "claude-test", "repetitions": 2,
             "tasks": ["a", "b", "c"], "max_budget_usd": "2", "spend_cap_usd": "200",
             "pre_registration": None}
    value.update(changes)
    return replay.ReplayRequest.parse(value)


class Runs:
    """Finished replays on disk, behind the supervisor surface `compare` reads."""

    def __init__(self, root):
        self.root = Path(root)
        self.records = {}

    def add(self, run_id, selected, rows_by_revision, status="succeeded", stop_after=None, stamp=None):
        """`stop_after` keeps only that many tasks of the first target and stops at the cap, as
        the native runner does, so the second target never launches."""
        run_root = self.root / run_id
        run_root.mkdir()

        def launch(command, **_kwargs):
            ref = command[command.index("--tag") + 1]
            rows = [dict(row, tag=ref, harness_sha=ref, model=selected.model, schema_version=1,
                         **(stamp or {}))
                    for row in rows_for(rows_by_revision[ref])]
            if stop_after is not None:
                kept = sorted({row["task"] for row in rows})[:stop_after]
                write_native_result(command, [row for row in rows if row["task"] in kept],
                                    stopped=True)
                return SimpleNamespace(returncode=1)
            write_native_result(command, rows)
            return SimpleNamespace(returncode=0)

        replay.execute(selected, REPO, run_root / "replay", launch)
        self.records[run_id] = (selected, status)

    def show(self, run_id):
        if run_id not in self.records:
            raise runs.RunError("unknown run")
        return {"run_id": run_id, "suite_id": "live-replay", "status": self.records[run_id][1]}

    @staticmethod
    def lock():
        return contextlib.nullcontext()

    def _read(self, run_id):
        return {"parameters": {"request_json": json.dumps(self.records[run_id][0].as_dict())}}

    def _run_path(self, run_id):
        return self.root / run_id / "run.json"


def two_runs(root):
    """The first run measures base and cheaper; the second measures noisy and a dearer target."""
    supervisor = Runs(root)
    supervisor.add(FIRST_RUN, request([target("branch", "main", BASE_REV),
                                       target("branch", "faster", CANDIDATE_REV)]),
                   {BASE_REV: BASE, CANDIDATE_REV: CHEAPER})
    supervisor.add(SECOND_RUN, request([target("branch", "noisy", OTHER_REV),
                                        target("branch", "main", BASE_REV)]),
                   {OTHER_REV: NOISY, BASE_REV: BASE})
    return supervisor


def sides(base, candidate):
    return {"base": {"run_id": base[0], "target": base[1]},
            "candidate": {"run_id": candidate[0], "target": candidate[1]}}


class Handler:
    def __init__(self, supervisor, body):
        self.server = SimpleNamespace(run_supervisor=supervisor, repo_root=REPO)
        self.request_json = body
        self.response = None

    def _json(self, code, payload):
        self.response = (code, payload)

    def _error(self, code, name):
        self.response = (code, {"error": name})


def route_call(supervisor, body):
    route = next(item for item in server.ROUTES.entries if item.path == "/api/runs/compare")
    handler = Handler(supervisor, body)
    route.handler(handler, route)
    return handler.response


def engine_rows(supervisor, base, candidate):
    """The two sides' harness rows as the engine is given them: base as control."""
    out = []
    for (run_id, index), arm in ((base, "base"), (candidate, "candidate")):
        selected = supervisor.records[run_id][0]
        path = (supervisor.root / run_id / "replay" / ("target-%d" % index)
                / selected.targets[index - 1].execution_ref / replay.RESULTS_NAME)
        out += [dict(row, arm=arm) for row in replay._read_rows(path) if row["arm"] == "harness"]
    return out


def cli(supervisor, base, candidate, json_mode=True):
    output = io.StringIO()
    arguments = ["runs", "compare", "%s:%d" % base, "%s:%d" % candidate]
    with mock.patch.object(runs, "RunSupervisor", return_value=mock.Mock(wraps=supervisor)), \
            mock.patch.dict(os.environ, {"HARNESS_QUIET": ""}), \
            contextlib.redirect_stdout(output):
        code = harness.main(arguments + (["--json"] if json_mode else []))
    return code, output.getvalue()


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runs = two_runs(self.tmp.name)

    def test_the_route_the_cli_and_the_engine_agree_on_the_same_rows(self):
        base, candidate = (FIRST_RUN, 1), (FIRST_RUN, 2)
        status, payload = route_call(self.runs, sides(base, candidate))
        self.assertEqual(status, 200, payload)
        server.RUNS_COMPARE.validate(payload)
        self.assertTrue(payload["comparable"])
        self.assertEqual(payload["refusals"], [])
        expected = compare._engine().compare(engine_rows(self.runs, base, candidate), control="base")
        self.assertEqual(payload["result"], expected)
        # The same figures from the fixture specification alone, independent of how compare.py
        # reads and relabels the stored rows.
        from_spec = rows_for({task: {"base": BASE[task]["harness"], "candidate": CHEAPER[task]["harness"]}
                              for task in BASE})
        self.assertEqual(payload["result"], compare._engine().compare(from_spec, control="base"))
        code, printed = cli(self.runs, base, candidate)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(printed), json.loads(json.dumps(payload)))
        measure = payload["result"]["arms"][0]["measures"]["cost_per_passed"]
        self.assertEqual(measure["reading"], "lower")
        self.assertEqual(payload["preferred"], {"cost_per_passed": "lower", "pass_rate": "higher"})
        self.assertEqual(payload["direction_withheld"], [
            "the base is exploratory", "the candidate is exploratory",
            "the engine marks candidate exploratory"])
        self.assertEqual(set(payload["preferred"]) - {key for key, _, _ in compare._engine().MEASURES},
                         set())
        self.assertEqual(json.loads(FIXTURE.read_text(encoding="utf-8")), json.loads(json.dumps(payload)))

    def test_each_side_carries_its_identity_and_its_own_recorded_analysis(self):
        _status, payload = route_call(self.runs, sides((FIRST_RUN, 1), (SECOND_RUN, 1)))
        self.assertTrue(payload["key_match"])
        self.assertEqual(payload["base"]["ref"]["ref"], "main")
        self.assertEqual(payload["candidate"]["ref"]["ref"], "noisy")
        self.assertEqual(payload["candidate"]["finished"], {"a": 2, "b": 2, "c": 2})
        self.assertEqual(payload["base"]["evidence"], "exploratory")
        recorded = replay.read_analysis(self.runs.root / SECOND_RUN / "replay")
        self.assertEqual(payload["candidate"]["analysis"], recorded[0])
        self.assertNotIn("_rows", payload["base"])

    def test_a_ratio_whose_interval_spans_one_reads_inconclusive_whatever_its_estimate(self):
        _status, payload = route_call(self.runs, sides((FIRST_RUN, 1), (SECOND_RUN, 1)))
        measure = payload["result"]["arms"][0]["measures"]["cost_per_passed"]
        self.assertLess(measure["estimate"], 1.0)
        self.assertLess(measure["interval"][0], 0.0)
        self.assertGreater(measure["interval"][1], 0.0)
        self.assertEqual(measure["reading"], "inconclusive")
        self.assertEqual(measure["reason"], "the interval spans no effect")

    def test_runs_on_different_task_sets_are_refused_and_the_difference_named(self):
        self.runs.add("00000000-0000-4000-8000-000000000003",
                      request([target("branch", "main", BASE_REV),
                               target("branch", "other", OTHER_REV)], tasks=["a", "b", "d"]),
                      {BASE_REV: spec({"a": [1.0, 1.0], "b": [1.0, 1.0], "d": [1.0, 1.0]},
                                      tasks=("a", "b", "d")),
                       OTHER_REV: spec({"a": [1.0, 1.0], "b": [1.0, 1.0], "d": [1.0, 1.0]},
                                       tasks=("a", "b", "d"))})
        status, payload = route_call(self.runs, sides(
            (FIRST_RUN, 1), ("00000000-0000-4000-8000-000000000003", 2)))
        self.assertEqual(status, 200)
        self.assertFalse(payload["comparable"])
        self.assertIsNone(payload["result"])
        self.assertEqual(payload["refusals"], [
            "different task sets: only the base runs c; only the candidate runs d"])
        code, printed = cli(self.runs, (FIRST_RUN, 1), ("00000000-0000-4000-8000-000000000003", 2),
                            json_mode=False)
        self.assertEqual(code, 1)
        self.assertIn("not compared, different task sets", printed)

    def test_model_trial_pack_and_finished_trial_differences_are_each_named(self):
        base = compare.load_side(self.runs, REPO, FIRST_RUN, 1)
        other = dict(base, run_id=SECOND_RUN, model="claude-other", trials=3,
                     pack_digest="f" * 64, max_budget_usd="5",
                     finished={"a": 3, "b": 1, "c": 3})
        self.assertEqual(compare.refusals(base, other), [
            "the candidate did not finish the trials it requested: b (1 of 3)",
            "different models: the base ran claude-test, the candidate claude-other",
            "different trials per task: the base ran 2, the candidate 3",
            "different evaluator packs: the base ran no pack, the candidate " + "f" * 64,
            "different per-trial budget caps: the base ran $2, the candidate $5"])
        self.assertEqual(compare.refusals(base, base), ["both sides are target 1 of the same run"])

    def test_pinned_stamps_must_match_and_a_different_run_date_is_named(self):
        stamp = {"cli_version": "2.1.0", "os": "linux", "prices_sha256": "p" * 64,
                 "date": "2026-09-01"}
        later = "00000000-0000-4000-8000-000000000007"
        upgraded = "00000000-0000-4000-8000-000000000008"
        self.runs.add(later, request([target("branch", "main", BASE_REV),
                                      target("branch", "faster", CANDIDATE_REV)]),
                      {BASE_REV: BASE, CANDIDATE_REV: CHEAPER}, stamp=stamp)
        self.runs.add(upgraded, request([target("branch", "main", BASE_REV),
                                         target("branch", "faster", CANDIDATE_REV)]),
                      {BASE_REV: BASE, CANDIDATE_REV: CHEAPER},
                      stamp=dict(stamp, cli_version="2.2.0", date="2026-10-01"))
        _status, payload = route_call(self.runs, sides((later, 1), (upgraded, 2)))
        self.assertFalse(payload["comparable"])
        self.assertEqual(payload["refusals"], [
            "different CLI versions: the base ran 2.1.0, the candidate 2.2.0"])
        self.assertEqual(payload["notes"], [
            "different run dates: the base ran 2026-09-01, the candidate 2026-10-01"])
        _status, payload = route_call(self.runs, sides((FIRST_RUN, 1), (later, 2)))
        self.assertEqual(payload["refusals"], [
            "different CLI versions: the base ran unrecorded, the candidate 2.1.0",
            "different container platforms: the base ran unrecorded, the candidate linux",
            "different price tables: the base ran unrecorded, the candidate " + "p" * 64])
        same_day = dict(stamp, date="2026-09-01")
        self.runs.add("00000000-0000-4000-8000-000000000009",
                      request([target("branch", "main", BASE_REV),
                               target("branch", "faster", CANDIDATE_REV)]),
                      {BASE_REV: BASE, CANDIDATE_REV: CHEAPER},
                      stamp=dict(same_day, date="2026-10-01"))
        code, printed = cli(self.runs, (later, 1), ("00000000-0000-4000-8000-000000000009", 2),
                            json_mode=False)
        self.assertEqual(code, 0)
        self.assertIn("runs: note, different run dates: the base ran 2026-09-01, the candidate "
                      "2026-10-01", printed)

    def test_a_direction_is_withheld_unless_the_result_could_be_cited(self):
        base = compare.load_side(self.runs, REPO, FIRST_RUN, 1)
        candidate = compare.load_side(self.runs, REPO, FIRST_RUN, 2)
        registered = [dict(side, evidence="pre-registered") for side in (base, candidate)]
        solid = {"arms": [{"arm": "candidate", "exploratory": False}]}
        self.assertEqual(compare.direction_withheld(*registered, solid), [])
        self.assertEqual(compare.direction_withheld(
            *registered, {"arms": [{"arm": "candidate", "exploratory": True}]}),
            ["the engine marks candidate exploratory"])
        self.assertEqual(compare.direction_withheld(registered[0], candidate, solid),
                         ["the candidate is exploratory"])
        dated = dict(registered[1], stamps=dict(registered[1]["stamps"], date=["2026-10-01"]))
        self.assertEqual(compare.direction_withheld(registered[0], dated, solid),
                         ["the two runs are from different dates"])

    def test_runs_with_different_per_trial_budget_caps_are_refused(self):
        run_id = "00000000-0000-4000-8000-000000000005"
        self.runs.add(run_id, request([target("branch", "main", BASE_REV),
                                       target("branch", "faster", CANDIDATE_REV)],
                                      max_budget_usd="0.5"),
                      {BASE_REV: BASE, CANDIDATE_REV: CHEAPER})
        _status, payload = route_call(self.runs, sides((FIRST_RUN, 1), (run_id, 2)))
        self.assertFalse(payload["comparable"])
        self.assertEqual(payload["refusals"], [
            "different per-trial budget caps: the base ran $2, the candidate $0.5"])

    def test_a_run_stopped_at_its_cap_is_refused_and_every_shortfall_named(self):
        run_id = "00000000-0000-4000-8000-000000000006"
        self.runs.add(run_id, request([target("branch", "faster", CANDIDATE_REV),
                                       target("branch", "main", BASE_REV)]),
                      {BASE_REV: BASE, CANDIDATE_REV: CHEAPER}, status="capped", stop_after=1)
        self.assertTrue(json.loads((self.runs.root / run_id / "replay" / replay.SUMMARY_NAME)
                                   .read_text())["stopped_at_cap"])
        status, payload = route_call(self.runs, sides((FIRST_RUN, 1), (run_id, 1)))
        self.assertEqual(status, 200)
        self.assertFalse(payload["comparable"])
        self.assertIsNone(payload["result"])
        self.assertEqual(payload["refusals"], [
            "the candidate run ended capped, not succeeded",
            "the candidate run stopped at its spend cap",
            "the candidate did not finish the trials it requested: b (0 of 2); c (0 of 2)"])
        self.assertEqual(route_call(self.runs, sides((FIRST_RUN, 1), (run_id, 2))),
                         (409, {"error": "compare_unavailable"}))

    def test_a_draft_side_goes_stale_when_the_draft_changes(self):
        run_id = "00000000-0000-4000-8000-000000000004"
        self.runs.add(run_id, request([target("branch", "main", BASE_REV),
                                       target("draft", "tuned", CANDIDATE_REV)]),
                      {BASE_REV: BASE, CANDIDATE_REV: CHEAPER})
        fresh = {"draft": {"revision": CANDIDATE_REV}, "config": {}}
        with mock.patch.object(drafts, "read_config", return_value=fresh):
            _status, payload = route_call(self.runs, sides((run_id, 1), (run_id, 2)))
        self.assertEqual((payload["candidate"]["stale"], payload["candidate"]["stale_reason"],
                          payload["candidate"]["freshness"]), (False, None, "current"))
        newer = {"draft": {"revision": "e" * 40}, "config": {}}
        with mock.patch.object(drafts, "read_config", return_value=newer):
            _status, payload = route_call(self.runs, sides((run_id, 1), (run_id, 2)))
        self.assertEqual((payload["candidate"]["stale"], payload["candidate"]["stale_reason"]),
                         (True, "the draft has a newer checkpoint"))
        self.assertEqual(payload["candidate"]["freshness"], "stale")
        self.assertEqual(payload["stale"], ["candidate: the draft has a newer checkpoint"])
        self.assertEqual((payload["base"]["stale"], payload["base"]["freshness"]),
                         (False, "not checked"))
        with mock.patch.object(drafts, "read_config", return_value=newer):
            code, printed = cli(self.runs, (run_id, 1), (run_id, 2), json_mode=False)
        self.assertEqual(code, 0)
        self.assertIn("candidate is run %s target 2, draft tuned; freshness stale: the draft has "
                      "a newer checkpoint" % run_id, printed)
        self.assertIn("base is run %s target 1, branch main; freshness not checked" % run_id, printed)
        self.assertIn("runs: stale, the comparison describes an older revision of the candidate",
                      printed)
        self.assertTrue(payload["comparable"])
        comparison = replay.draft_comparisons(REPO, self.runs.records[run_id][0])
        self.assertEqual((comparison[0]["target"], comparison[0]["base_target"]), (2, 1))

    def test_an_engine_refusal_is_returned_in_its_own_words(self):
        with mock.patch.object(compare._engine(), "compare", side_effect=ValueError("no rows")):
            _status, payload = route_call(self.runs, sides((FIRST_RUN, 1), (FIRST_RUN, 2)))
        self.assertTrue(payload["comparable"])
        self.assertIsNone(payload["result"])
        self.assertEqual(payload["error"], "no rows")

    def test_unknown_unfinished_and_malformed_sides_are_errors_with_status_codes(self):
        cases = [
            (sides(("00000000-0000-4000-8000-00000000000f", 1), (FIRST_RUN, 2)),
             (404, "compare_not_found")),
            ({"base": {"run_id": FIRST_RUN, "target": 3},
              "candidate": {"run_id": FIRST_RUN, "target": 1}}, (400, "invalid_request")),
            ({"base": {"run_id": "not-a-run", "target": 1},
              "candidate": {"run_id": FIRST_RUN, "target": 1}}, (400, "invalid_request")),
            ({"base": {"run_id": FIRST_RUN, "target": True},
              "candidate": {"run_id": FIRST_RUN, "target": 1}}, (400, "invalid_request")),
            ({"base": {"run_id": FIRST_RUN, "target": 1}}, (400, "invalid_request")),
        ]
        for body, expected in cases:
            with self.subTest(body=body):
                status, payload = route_call(self.runs, body)
                self.assertEqual((status, payload["error"]), expected)
        self.runs.records[SECOND_RUN] = (self.runs.records[SECOND_RUN][0], "running")
        self.assertEqual(route_call(self.runs, sides((FIRST_RUN, 1), (SECOND_RUN, 1))),
                         (409, {"error": "compare_not_finished"}))

    def test_a_target_with_no_native_rows_is_unavailable(self):
        selected = self.runs.records[FIRST_RUN][0]
        path = (self.runs.root / FIRST_RUN / "replay" / "target-2"
                / selected.targets[1].execution_ref / replay.RESULTS_NAME)
        summary_path = self.runs.root / FIRST_RUN / "replay" / replay.SUMMARY_NAME
        summary = json.loads(summary_path.read_text())
        summary["result_files"] = [item for item in summary["result_files"]
                                   if Path(item).resolve() != path.resolve()]
        self.assertEqual(len(summary["result_files"]), 1)
        summary_path.write_text(json.dumps(summary))
        self.assertEqual(route_call(self.runs, sides((FIRST_RUN, 1), (FIRST_RUN, 2))),
                         (409, {"error": "compare_unavailable"}))

    def test_the_cli_names_a_malformed_side(self):
        output = io.StringIO()
        with mock.patch.object(runs, "RunSupervisor", return_value=mock.Mock(wraps=self.runs)), \
                mock.patch.dict(os.environ, {"HARNESS_QUIET": ""}), \
                contextlib.redirect_stdout(output):
            code = harness.main(["runs", "compare", FIRST_RUN, FIRST_RUN + ":2", "--json"])
        self.assertEqual(code, 2)
        self.assertIn("base must be RUN_ID:TARGET", json.loads(output.getvalue())["error"])

    def test_the_route_names_its_citizen_command(self):
        route = next(item for item in server.ROUTES.entries if item.path == "/api/runs/compare")
        self.assertEqual((route.method, route.cli_command),
                         ("POST", ("citizen", "runs", "compare", "{base}", "{candidate}", "--json")))


class CompareRouteSecurityTests(studio_security.StudioSecurityFixture):
    def setUp(self):
        super().setUp()
        _issued, status, headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        self.cookie_value = self.cookie(headers)
        status, _headers, body = self.request("GET", "/api/session", {"Cookie": self.cookie_value})
        self.csrf = json.loads(body)["csrf_token"]

    def post(self, payload, headers=None):
        body = json.dumps(payload).encode()
        supplied = {"Cookie": self.cookie_value, "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                    "Origin": self.record["url"].rstrip("/"), "X-Studio-CSRF": self.csrf}
        supplied.update(headers or {})
        return self.request("POST", "/api/runs/compare", supplied, body)

    def test_the_route_requires_a_session_csrf_and_origin_then_validates_its_input(self):
        valid = sides((FIRST_RUN, 1), (SECOND_RUN, 1))
        body = json.dumps(valid).encode()
        status, _headers, _body = self.request("POST", "/api/runs/compare", {
            "Content-Type": "application/json", "Content-Length": str(len(body))}, body)
        self.assertEqual(status, 401)
        status, _headers, _body = self.post(valid, {"X-Studio-CSRF": "wrong"})
        self.assertEqual(status, 403)
        status, _headers, _body = self.post(valid, {"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        status, _headers, body = self.post({"base": {"run_id": FIRST_RUN, "target": 9},
                                            "candidate": {"run_id": SECOND_RUN, "target": 1}})
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"error": "invalid_request"})
        status, _headers, body = self.post(valid)
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body), {"error": "compare_not_found"})


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        with tempfile.TemporaryDirectory() as tmp:
            _status, produced = route_call(two_runs(tmp), sides((FIRST_RUN, 1), (FIRST_RUN, 2)))
        FIXTURE.write_text(json.dumps(produced, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("wrote " + str(FIXTURE))
    else:
        unittest.main()

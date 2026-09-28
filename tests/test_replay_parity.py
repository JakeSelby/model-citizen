"""The replay's arms are comparable and its tasks fair: the task count the docs state, the turn cap,
the stop gate in a snapshot, web access, the retired task and the stream capture. Every launch here
is a fake that replays recorded CLI output; no test calls a model."""
import json
import re
import tempfile
import unittest
from pathlib import Path

from test_cost_bench import BENCH, TASK, Launch, call, options, result
from test_harness import REPO

DOCS = REPO / "docs"
RUN_LINE = re.compile(r"#\s*(\d+) tasks x (\d+) arms x (\d+) reps")
SYNTHETIC_LINE = re.compile(r"(\d+) of the (\d+) tasks\s+are\s+synthetic")


def stream(*messages):
    """What `--output-format stream-json` prints: one compact message per line."""
    return "".join(json.dumps(m, separators=(",", ":")) + "\n" for m in messages)


def hook(event="Stop", stdout="", exit_code=0):
    return {"type": "system", "subtype": "hook_response", "hook_id": "h", "hook_name": event + ":x",
            "hook_event": event, "output": stdout, "stdout": stdout, "stderr": "",
            "exit_code": exit_code, "outcome": "success", "uuid": "u", "session_id": "s"}


BLOCK = json.dumps({"decision": "block", "reason": "Gate is red"})


def count_errors(texts, tasks):
    """Every place a doc's stated task count disagrees with the manifest, and how many were read."""
    errors, seen = [], 0
    synthetic = sum(1 for t in tasks if t["kind"] == "synthetic")
    for name, text in texts.items():
        for match in RUN_LINE.finditer(text):
            seen += 1
            if int(match.group(1)) != len(tasks) or int(match.group(2)) != len(BENCH.ARMS):
                errors.append("%s: %s" % (name, match.group(0)))
        for match in SYNTHETIC_LINE.finditer(text):
            seen += 1
            if (int(match.group(1)), int(match.group(2))) != (synthetic, len(tasks)):
                errors.append("%s: %s" % (name, match.group(0)))
    return errors, seen


class TaskCountTests(unittest.TestCase):
    def setUp(self):
        self.tasks = BENCH.load_tasks(REPO / BENCH.TASKS)
        self.texts = {p.name: p.read_text(encoding="utf-8") for p in sorted(DOCS.glob("*.md"))}

    def test_every_task_count_the_docs_state_is_the_manifests(self):
        errors, seen = count_errors(self.texts, self.tasks)
        self.assertEqual(errors, [])
        self.assertGreaterEqual(seen, 3)  # benchmarks.md twice and caught-in-the-act.md once

    def test_the_check_fails_when_the_docs_and_the_manifest_diverge(self):
        """Proves the check bites: a doc still saying four tasks is reported, not skipped."""
        stale = {"x.md": "replay --model <id>   # 4 tasks x 2 arms x 2 reps\n"
                         "two of them, 2 of the 4 tasks are synthetic\n"}
        errors, seen = count_errors(stale, self.tasks)
        self.assertEqual((len(errors), seen), (2, 2))


class TurnCapTests(unittest.TestCase):
    def test_each_tasks_max_turns_reaches_the_launched_command_in_both_arms(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = Launch([json.dumps(result())] * 2)
            BENCH.replay([dict(TASK, max_turns=17)], options(tmp, reps=1), launch)
            self.assertEqual(len(launch.calls), 2)
            for command, _ in launch.calls:
                self.assertEqual(command[command.index("--max-turns") + 1], "17")

    def test_a_command_with_no_cap_names_none(self):
        self.assertNotIn("--max-turns", BENCH.arm_command("claude", "m", "p"))
        command = BENCH.arm_command("claude", "m", "p", max_turns=60)
        self.assertEqual(command[command.index("--max-turns") + 1], "60")
        self.assertEqual(command[-2], "--settings")  # the fence stays the last argument


class StopGateTrustTests(unittest.TestCase):
    def test_the_harness_image_trusts_the_one_path_every_snapshot_is_mounted_at(self):
        """The stop-gate hook runs a gate only in a trusted root. Every run mounts its snapshot at
        the same path, so the harness image trusts that path when it is built, and no run writes
        a trust file anywhere, on this machine or in the container."""
        dockerfile = (REPO / "scripts" / "replay-arm.Dockerfile").read_text(encoding="utf-8")
        self.assertIn("bin/harness trust " + BENCH.arms.WORKDIR, dockerfile)
        self.assertFalse(hasattr(BENCH, "trusted_run"))

    def test_a_replay_writes_nothing_under_the_home_it_is_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            opts = options(tmp, reps=1)
            BENCH.replay([TASK], opts, Launch([json.dumps(result())] * 2))
            self.assertEqual(list(Path(opts["home"]).iterdir()), [])

    def test_a_run_ending_on_a_red_gate_records_the_stop_hooks_feedback(self):
        red = stream(call(None, ["Bash"]), hook(stdout=BLOCK), call(None, ["Bash"]),
                     hook(stdout=""), result())
        with tempfile.TemporaryDirectory() as tmp:
            rows, _ = BENCH.replay([TASK], options(tmp, reps=1), Launch([red, red]))
        for row in rows:
            self.assertEqual((row["stop_hooks"], row["hook_blocks"]), (2, 1))

    def test_only_a_stop_hooks_block_counts(self):
        messages, _ = BENCH.cli_messages(stream(
            hook(stdout=BLOCK), hook(exit_code=2), hook(stdout="not json"),
            hook(event="PreToolUse", stdout=BLOCK), hook(event="SubagentStop", stdout=BLOCK), result()))
        self.assertEqual(BENCH.stop_hook_counts(messages, True), (3, 2))
        self.assertEqual(BENCH.stop_hook_counts(messages, False), (None, None))


class WebAccessTests(unittest.TestCase):
    def test_both_arms_launch_with_the_web_tools_denied_on_the_same_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = Launch([json.dumps(result())] * 2)
            opts = options(tmp, reps=1)
            BENCH.replay([TASK], opts, launch)
            settings = [json.loads(command[-1]) for command, _ in launch.calls]
            networks = [command[command.index("--network") + 1] for command, _ in launch.calls]
        self.assertEqual(settings[0], settings[1])
        self.assertEqual(sorted(settings[0]["permissions"]["deny"]), ["WebFetch", "WebSearch"])
        self.assertEqual(networks, [opts["network"]] * 2)

    def test_the_deny_is_the_same_whatever_the_arm(self):
        self.assertEqual(BENCH.ARM_SETTINGS, {"permissions": {"deny": list(BENCH.NO_WEB)}})
        self.assertEqual(BENCH.arm_command("claude", "m", "p")[-1], json.dumps(BENCH.ARM_SETTINGS))


class RetiredTaskTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((REPO / BENCH.TASKS).read_text(encoding="utf-8"))

    def test_usage_prices_left_the_set_with_its_reason_recorded(self):
        live = [t["id"] for t in BENCH.load_tasks(REPO / BENCH.TASKS)]
        retired = {r["id"]: r for r in self.manifest["retired"]}
        self.assertNotIn("usage-prices", live)
        self.assertIn("usage-prices", retired)
        for entry in retired.values():
            self.assertNotIn(entry["id"], live)
            self.assertRegex(entry["retired"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertGreater(len(entry["reason"]), 80)
            self.assertEqual(entry["task"]["id"], entry["id"])  # kept whole, not rewritten


class StreamCaptureTests(unittest.TestCase):
    def test_runs_are_launched_as_stream_json_with_hook_events(self):
        command = BENCH.arm_command("claude", "m", "p")
        self.assertEqual(command[command.index("--output-format") + 1], "stream-json")
        for flag in ("--include-hook-events", "--verbose"):
            self.assertIn(flag, command)

    def test_a_stream_reads_the_same_as_the_single_document_it_replaces(self):
        messages = [call(None, ["Read", "Task"], write=900, read=100), call("toolu_1", ["Bash"], read=50),
                    result()]
        old = BENCH.parse_result(json.dumps(messages))
        new = BENCH.parse_result(stream(*messages))
        for field in ("cost_usd", "tokens", "turns", "first_turns", "first_call_cache_write",
                      "tool_counts", "spawns", "cache_miss_ratio"):
            self.assertEqual(new[field], old[field], field)
        self.assertEqual((old["stop_hooks"], old["hook_blocks"]), (None, None))
        self.assertEqual((new["stop_hooks"], new["hook_blocks"]), (0, 0))

    def test_a_line_cut_off_mid_write_is_skipped_and_no_json_at_all_is_an_error(self):
        cut = stream(call(None, ["Bash"]), result()) + '{"type":"assis'
        self.assertEqual(BENCH.parse_result(cut)["tool_counts"], {"Bash": 1})
        for bad in ("", "not json\nstill not", "5\n6\n"):
            with self.assertRaises(ValueError, msg=bad):
                BENCH.parse_result(bad)

    def test_the_preflight_reads_its_gate_and_reply_from_a_stream(self):
        out = stream({"type": "user", "message": {"content": [
                         {"type": "tool_result", "content": "lint: 0 finding(s) in 3 files"}]}},
                     dict(result(), result="lint: 0 finding(s) in 3 files"))
        self.assertTrue(BENCH.gate_passed(out))
        self.assertEqual(BENCH.reply_text(out), "lint: 0 finding(s) in 3 files")

    def test_a_raw_stream_backfills_its_stop_hook_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp)
            (raw / "demo-harness-1.json").write_text(stream(hook(stdout=BLOCK), result()), encoding="utf-8")
            rows, missing = BENCH.backfill_rows([{"task": "demo", "arm": "harness", "rep": 1}], raw,
                                                home=tmp)
        self.assertEqual(missing, [])
        self.assertEqual((rows[0]["stop_hooks"], rows[0]["hook_blocks"]), (1, 1))


if __name__ == "__main__":
    unittest.main()

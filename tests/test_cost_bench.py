"""The static context figure is counted, priced and gated without calling a model, and the replay
runner is exercised on recorded CLI output only: no test here launches an agent."""
import importlib.util
import json
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from test_harness import REPO


def load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


BENCH = load("cost_bench")


def tree(root, rule="r" * 400):
    """A checkout small enough to count by hand: 40 + 400 + 80 + 40 always-loaded, 20 listed."""
    files = {
        "VERSION": "9.9.9\n",
        "config.example.json": json.dumps({"stances": {"cost": "balanced"}}),
        "policy/prices.json": json.dumps({"models": {
            "claude-test": {"input": 1.0, "output": 5.0, "cache_read": 0.5, "cache_write": 2.0},
            "gpt-test": {"input": 1.0, "output": 5.0, "cache_read": 0.5, "cache_write": 0}}}),
        "claude/CLAUDE.md": "c" * 40,
        "claude/rules/one.md": rule,
        "claude/stances/cost/balanced.md": "b" * 80,
        "claude/stances/cost/max.md": "m" * 800,
        "claude/output-styles/style.md": "s" * 40,
        "claude/agents/worker.md": "---\nname: worker\ndescription: " + "a" * 8 + "\n  " + "a" * 3
                                   + "\nmodel: opus\n---\n" + "body " * 500,
        "claude/skills/demo/SKILL.md": "---\nname: demo\ndescription: " + "k" * 8 + "\n---\nlong body\n",
    }
    for name, text in files.items():
        path = Path(root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return Path(root)


class StaticCountTests(unittest.TestCase):
    def test_counts_the_selected_variant_and_only_the_listed_descriptions(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = BENCH.measure(tree(tmp))
        self.assertEqual(result["harness_version"], "9.9.9")
        self.assertEqual(result["always_loaded"], {"files": 4, "lines": 4, "chars": 560, "est_tokens": 140})
        self.assertEqual(result["listings"]["chars"], 12 + 8)  # the folded line joins with a space
        self.assertEqual(result["total"]["est_tokens"], 145)
        self.assertEqual(result["worst_case_est_tokens"], 325)  # max.md stands in for balanced.md
        self.assertEqual(result["largest"][0]["path"], "claude/rules/one.md")

    def test_prices_only_models_with_cache_rates_from_the_anthropic_family(self):
        with tempfile.TemporaryDirectory() as tmp:
            usd = BENCH.measure(tree(tmp))["usd"]
        self.assertEqual(usd, {"claude-test": {"session_start": 0.00029, "later_turn": 0.000073}})

    def test_a_file_without_frontmatter_lists_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plain.md"
            path.write_text("no frontmatter\ndescription: not this\n", encoding="utf-8")
            self.assertEqual(BENCH._description(path), "")


class StaticGateTests(unittest.TestCase):
    def committed(self, root):
        (root / "benchmarks").mkdir()
        (root / BENCH.STATIC).write_text(json.dumps(BENCH.measure(root)), encoding="utf-8")

    def test_missing_baseline_is_an_error_not_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIn("is missing", BENCH.check(tree(tmp))[0])

    def test_growth_inside_the_limit_passes_and_past_it_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = tree(tmp)
            self.committed(root)
            self.assertEqual(BENCH.check(root), [])
            tree(tmp, rule="r" * 420)  # +3.4%
            self.assertEqual(BENCH.check(root), [])
            tree(tmp, rule="r" * 480)  # +13.8%
            errors = BENCH.check(root)
            self.assertEqual(len(errors), 1)
            self.assertIn("grew from 145 to 165", errors[0])

    def test_an_allow_entry_must_name_this_version_this_figure_and_a_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = tree(tmp)
            self.committed(root)
            tree(tmp, rule="r" * 480)
            allow = root / BENCH.ALLOW
            for entry in ({"harness_version": "9.9.9", "est_tokens": 165, "reason": " "},
                          {"harness_version": "9.9.8", "est_tokens": 165, "reason": "new rule"},
                          {"harness_version": "9.9.9", "est_tokens": 160, "reason": "new rule"}):
                allow.write_text(json.dumps([entry]), encoding="utf-8")
                self.assertEqual(len(BENCH.check(root)), 1, msg=entry)
            allow.write_text(json.dumps([{"harness_version": "9.9.9", "est_tokens": 165,
                                          "reason": "new rule"}]), encoding="utf-8")
            self.assertEqual(BENCH.check(root), [])

    def test_shrinking_always_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = tree(tmp)
            self.committed(root)
            tree(tmp, rule="r" * 40)
            self.assertEqual(BENCH.check(root), [])


class CommittedFigureTests(unittest.TestCase):
    def test_this_checkout_is_inside_its_committed_figure(self):
        self.assertEqual(BENCH.check(REPO), [])

    def test_the_committed_figure_names_its_estimate_and_prices_a_current_model(self):
        data = json.loads((REPO / BENCH.STATIC).read_text(encoding="utf-8"))
        self.assertEqual(data["chars_per_token"], BENCH.CHARS_PER_TOKEN)
        self.assertGreater(data["total"]["est_tokens"], 0)
        self.assertTrue(any(name.startswith("claude-") for name in data["usd"]))


PRICES = {"claude-test": {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5}}
TASK = {"id": "demo", "kind": "synthetic", "parent_sha": "HEAD", "good_sha": None, "prompt": ["do", "it"],
        "tests": {"oracle": "none"}, "max_turns": 5}


def result(cost=0.5, error=False, subtype="success"):
    return {"type": "result", "subtype": subtype, "is_error": error, "num_turns": 3, "total_cost_usd": cost,
            "usage": {"input_tokens": 10, "output_tokens": 20, "cache_creation_input_tokens": 30,
                      "cache_read_input_tokens": 40}}


def turn(thread, read, model="claude-test-20260101"):
    return {"type": "assistant", "parent_tool_use_id": thread,
            "message": {"model": model, "usage": {"cache_read_input_tokens": read}}}


def call(thread, tools=(), write=0, read=0, model="claude-test-20260101"):
    """An assistant message that both spends cache and calls tools, as a real turn does."""
    return {"type": "assistant", "parent_tool_use_id": thread,
            "message": {"model": model,
                        "usage": {"cache_read_input_tokens": read, "cache_creation_input_tokens": write},
                        "content": [{"type": "tool_use", "name": name, "input": {}} for name in tools]}}


def config_tree(root, personal="p" * 64, rules=2):
    """A profile directory shaped like an installed one: the layers a fingerprint counts."""
    root = Path(root)
    files = {"CLAUDE.md": "c" * 100, "CLAUDE.personal.md": personal,
             "skills/one/SKILL.md": "s" * 10, "agents/worker.md": "a" * 10,
             "output-styles/style.md": "o" * 10, "notes.md": "ignored"}
    for index in range(rules):
        files["rules/rule-%d.md" % index] = "r" * 20
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


class Launch:
    """Stands in for Docker: replays recorded output and records how it was called."""
    def __init__(self, outputs, probe_exit=0):
        self.outputs, self.calls, self.probes, self.probe_exit = list(outputs), [], [], probe_exit

    def __call__(self, command, **kwargs):
        if BENCH.arms.WORKDIR_PROBE in command:  # the writable-snapshot probe, answered apart
            self.probes.append(command)
            return types.SimpleNamespace(stdout="", stderr="touch: cannot touch '/work/x': Permission denied",
                                         returncode=self.probe_exit)
        self.calls.append((command, kwargs))
        if command[:2] == ["docker", "rm"]:  # removing a timed-out container prints nothing to replay
            return types.SimpleNamespace(stdout="", stderr="", returncode=0)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return types.SimpleNamespace(stdout=out, stderr="", returncode=0)


def git_repo(root):
    root = Path(root)
    root.mkdir(parents=True)
    (root / "file.txt").write_text("one\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t",
                                                 "commit", "-q", "-m", "chore: first"]):
        subprocess.run(["git", "-C", str(root)] + args, check=True, stdout=subprocess.DEVNULL)
    return root


# Stands in for the credential's value: it may sit in the Docker client's environment and nowhere
# else, never on a command line and never in a row.
SECRET = "sk-ant-oat01-never-on-a-command-line"


def arm_record(arm, ref="v9.9.9"):
    """An arm as `replay_arms.build_arm` returns it, without a daemon."""
    harness = {"ref": ref, "commit": "c" * 40} if arm == "harness" else None
    decl = {"schema": 1, "arm": arm, "base_image": "base@sha256:" + "0" * 64, "claude_code_version": "1.0",
            "harness": harness, "components": []}
    return {"arm": arm, "label": "harness@" + ref if harness else "bare",
            "image": "model-citizen-arm-%s:test" % arm, "image_id": "sha256:" + ("1" if harness else "2") * 64,
            "declaration": decl, "declaration_sha256": ("d" if harness else "e") * 64, "manifest": {},
            "manifest_sha256": ("a" if harness else "b") * 64,
            "harness_ref": ref if harness else None, "harness_commit": "c" * 40 if harness else None}


def options(tmp, **over):
    opts = {"repo": git_repo(Path(tmp) / "source"), "home": Path(tmp) / "home",
            "model": "claude-test", "tag": "v9.9.9", "reps": 2, "run_cap": 2.0, "spend_cap": 25.0,
            "prices": PRICES, "tmp": str(Path(tmp) / "runs"),
            "scorer": lambda task, workdir, repo: (True, ""), "stamp": {"date": "2026-01-01"},
            "arms": {"bare": arm_record("bare"), "harness": arm_record("harness")},
            "network": "model-citizen-arm-egress-test", "proxy": "http://model-citizen-arm-proxy-test:3128",
            "client_env": {"PATH": "/bin", "CLAUDE_CODE_OAUTH_TOKEN": SECRET},
            "skip_preflight": True}  # the pre-flight has its own tests; these count scored launches
    (Path(tmp) / "runs").mkdir()
    opts["repo"].parent.joinpath("home").mkdir()
    opts.update(over)
    return opts


def mounts(command):
    return [command[i + 1] for i, part in enumerate(command) if part == "-v"]


def env_flags(command):
    return [command[i + 1] for i, part in enumerate(command) if part == "-e"]


class ReplayArmTests(unittest.TestCase):
    def test_both_arms_get_the_same_variables_bar_the_harness_arms_stance_override(self):
        bare = BENCH.arm_env("bare", "frugal", "http://proxy:3128")
        harness = BENCH.arm_env("harness", "frugal", "http://proxy:3128")
        self.assertNotIn("HARNESS_STANCE_COST", bare)
        self.assertEqual(harness.pop("HARNESS_STANCE_COST"), "frugal")
        self.assertEqual(bare, harness)
        for name in ("HOME", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN", "PATH"):
            self.assertNotIn(name, bare)  # the image's own HOME; the credential goes by name
        self.assertEqual(bare["HTTPS_PROXY"], "http://proxy:3128")
        self.assertEqual(bare["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"], "1")

    def test_every_arm_runs_one_command_with_the_required_flags(self):
        command = BENCH.arm_command("claude", "claude-test", "prompt")
        for flag in ("--strict-mcp-config", "--no-session-persistence", "--verbose"):
            self.assertIn(flag, command)
        for flag, value in (("--model", "claude-test"), ("--output-format", "stream-json"),
                            ("--max-budget-usd", "2"), ("--permission-mode", "bypassPermissions")):
            self.assertEqual(command[command.index(flag) + 1], value)
        self.assertEqual(json.loads(command[command.index("--settings") + 1]),
                         {"permissions": {"deny": ["WebFetch", "WebSearch"]}})

    def test_a_run_is_a_fresh_container_with_the_snapshot_its_only_mount(self):
        """#428: no arm reads the host's home. The run is `docker run --rm` of the arm's image on
        the egress network, with the snapshot mounted and nothing else, and the credential named
        but never valued on the command line."""
        with tempfile.TemporaryDirectory() as tmp:
            launch = Launch([json.dumps(result())] * 2)
            opts = options(tmp, reps=1)
            BENCH.replay([TASK], opts, launch)
            for (command, kwargs), arm in zip(launch.calls, ("bare", "harness")):
                self.assertEqual(command[:3], ["docker", "run", "--rm"])
                self.assertEqual(command[command.index("--network") + 1], opts["network"])
                self.assertIn(opts["arms"][arm]["image"], command)
                self.assertEqual(len(mounts(command)), 1)
                self.assertTrue(mounts(command)[0].endswith(":/work"))
                self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", env_flags(command))
                self.assertNotIn(SECRET, " ".join(command))
                self.assertNotIn(str(Path.home()), " ".join(command))
                self.assertNotIn("cwd", kwargs)
                self.assertEqual(kwargs["env"]["CLAUDE_CODE_OAUTH_TOKEN"], SECRET)  # the client's only
                self.assertIn("HTTPS_PROXY=" + opts["proxy"], env_flags(command))
                self.assertIn("no-new-privileges", command)

    def test_a_snapshot_keeps_the_history_its_gate_needs(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = git_repo(Path(tmp) / "source")
            for n in range(3):
                (repo / "file.txt").write_text("v%d\n" % n, encoding="utf-8")
                subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t",
                                "commit", "-qam", "chore: step %d" % n], check=True)
            subprocess.run(["git", "-C", str(repo), "tag", "v1"], check=True)
            at = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"]).decode().strip()
            (repo / "file.txt").write_text("the fix\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t",
                            "commit", "-qam", "fix: it"], check=True)
            subprocess.run(["git", "-C", str(repo), "tag", "v2"], check=True)
            clone = BENCH.snapshot(repo, at, Path(tmp) / "clone")
            count = subprocess.check_output(["git", "-C", str(clone), "rev-list", "--all", "--count"])
            self.assertEqual(count.decode().strip(), "4")  # the ancestors the gate reads survive
            self.assertEqual(subprocess.check_output(["git", "-C", str(clone), "tag"]).decode().split(), ["v1"])

    def test_a_snapshot_cannot_reach_the_commit_that_solved_the_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = git_repo(Path(tmp) / "source")
            first = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"]).decode().strip()
            (repo / "file.txt").write_text("the fix\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t",
                            "commit", "-qam", "fix: it"], check=True)
            fix = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"]).decode().strip()
            clone = BENCH.snapshot(repo, first, Path(tmp) / "clone")
            self.assertEqual((clone / "file.txt").read_text(encoding="utf-8"), "one\n")
            self.assertFalse(BENCH.reaches(clone, fix))
            self.assertTrue(BENCH.reaches(clone, first))
            count = subprocess.check_output(["git", "-C", str(clone), "rev-list", "--all", "--count"])
            self.assertEqual(count.decode().strip(), "1")


class ReplayCaptureTests(unittest.TestCase):
    def test_cost_tokens_and_first_turns_come_from_the_cli_json(self):
        parsed = BENCH.parse_result(json.dumps([turn(None, 1000), turn(None, 9000), turn("toolu_1", 500), result()]))
        self.assertEqual(parsed["cost_usd"], 0.5)
        self.assertEqual(parsed["tokens"], {"input_tokens": 10, "output_tokens": 20,
                                            "cache_creation_input_tokens": 30, "cache_read_input_tokens": 40})
        self.assertEqual([t["cache_read"] for t in parsed["first_turns"]], [1000, 500])
        self.assertEqual(BENCH.parse_result(json.dumps(result()))["first_turns"], [])

    def test_model_usage_is_preferred_because_it_includes_subagents(self):
        data = dict(result(), modelUsage={"a": {"inputTokens": 1, "outputTokens": 2, "cacheCreationInputTokens": 3,
                                                "cacheReadInputTokens": 4}, "b": {"inputTokens": 5}})
        self.assertEqual(BENCH.parse_result(json.dumps(data))["tokens"]["input_tokens"], 6)

    def test_output_without_a_priced_result_is_an_error_not_a_zero(self):
        for bad in ("", "not json", "[]", json.dumps({"type": "result"})):
            with self.assertRaises(ValueError, msg=bad):
                BENCH.parse_result(bad)

    def test_the_first_call_cache_write_is_the_standing_prefix_not_the_run_total(self):
        """The first usage block pays for the instruction layer; the run's total also pays for
        everything the agent read afterwards, so the two answer different questions."""
        stream = [call(None, ["Read"], write=9000), call(None, ["Bash"], write=400), result()]
        parsed = BENCH.parse_result(json.dumps(stream))
        self.assertEqual(parsed["first_call_cache_write"], 9000)
        self.assertEqual(parsed["tokens"]["cache_creation_input_tokens"], 30)  # the result's total
        self.assertIsNone(BENCH.parse_result(json.dumps(result()))["first_call_cache_write"])

    def test_every_tool_use_block_is_counted_and_spawns_are_the_subagent_share(self):
        stream = [call(None, ["Bash", "Read", "Task"]), call(None, ["Task", "Agent", "Bash"]),
                  call("toolu_1", ["Bash"]), result()]
        parsed = BENCH.parse_result(json.dumps(stream))
        self.assertEqual(parsed["tool_counts"], {"Bash": 3, "Read": 1, "Task": 2, "Agent": 1})
        self.assertEqual(parsed["spawns"], 3)
        self.assertEqual(BENCH.parse_result(json.dumps(result()))["tool_counts"], {})

    def test_hook_blocks_is_none_for_output_kept_as_one_json_document(self):
        """Hook lifecycle events are the only structured place a Stop hook's `block` appears, and
        the CLI emits them only under `--include-hook-events`, which its help limits to
        `--output-format=stream-json`. Output kept in the older single-document form cannot carry
        one, so the field is unknown rather than zero, and no text pattern stands in for it."""
        stream = [call(None, ["Bash"]), result()]
        self.assertIsNone(BENCH.parse_result(json.dumps(stream))["hook_blocks"])
        self.assertIn("--include-hook-events", BENCH.parse_result.__doc__)

    def test_normalised_cost_reprices_first_turn_reads_as_writes_or_declines(self):
        turns = [{"model": "claude-test-20260101", "cache_read": 1000000}]
        self.assertEqual(BENCH.normalised_cost(1.0, turns, PRICES), 3.3)  # + 1M x (2.5 - 0.2)
        self.assertIsNone(BENCH.normalised_cost(1.0, [], PRICES))
        self.assertIsNone(BENCH.normalised_cost(1.0, [{"model": "other", "cache_read": 5}], PRICES))


class ReplayRunTests(unittest.TestCase):
    def test_arms_interleave_and_the_leading_arm_alternates(self):
        order = [(t["id"], rep, arm) for t, rep, arm in BENCH.schedule([TASK], 2)]
        self.assertEqual(order, [("demo", 1, "bare"), ("demo", 1, "harness"),
                                 ("demo", 2, "harness"), ("demo", 2, "bare")])

    def test_a_seeded_over_cap_run_stops_before_launching(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = Launch([])
            rows, stopped = BENCH.replay([TASK], options(tmp, spend_cap=1.5), launch)
            self.assertEqual((rows, stopped, launch.calls), ([], True, []))

    def test_the_cumulative_stop_counts_reported_cost_and_a_costless_error_at_the_run_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = Launch([json.dumps(result(cost=1.2)), "garbage", json.dumps(result())])
            rows, stopped = BENCH.replay([TASK], options(tmp, spend_cap=5.0), launch)
            self.assertTrue(stopped)  # 1.2 + 2.0 spent, and 3.2 + 2.0 would pass 5.0
            self.assertEqual(len(launch.calls), 2)
            self.assertEqual([r["error"] for r in rows], [False, True])

    def test_an_errored_run_is_an_error_and_never_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = Launch([json.dumps(result(error=True, subtype="error_max_budget_usd")), "garbage",
                             subprocess.TimeoutExpired("claude", 1), json.dumps(result())])
            out = Path(tmp) / "results.jsonl"
            rows, _ = BENCH.replay([TASK], options(tmp, scorer=lambda *a: (False, "")), launch, out=out)
            self.assertEqual([(r["error"], r["passed"]) for r in rows],
                             [(True, None), (True, None), (True, None), (False, False)])
            self.assertEqual(rows[0]["error_kind"], "error_max_budget_usd")
            self.assertEqual(rows[0]["cost_usd"], 0.5)  # an error's cost is still recorded
            self.assertEqual(len(out.read_text(encoding="utf-8").splitlines()), 4)

    def test_a_check_that_cannot_run_is_an_error(self):
        def broken(task, workdir, repo):
            raise KeyError("oracle")
        with tempfile.TemporaryDirectory() as tmp:
            rows, _ = BENCH.replay([TASK], options(tmp, reps=1, scorer=broken), Launch([json.dumps(result())] * 2))
            self.assertEqual([(r["error"], r["passed"], r["error_kind"]) for r in rows],
                             [(True, None, "check: KeyError")] * 2)

    def test_a_run_starts_in_a_removed_clone_mounted_into_the_arms_own_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = Launch([json.dumps(result())] * 4)
            opts = options(tmp)
            BENCH.replay([TASK], opts, launch)
            command, kwargs = launch.calls[0]
            self.assertEqual(command[command.index("-p") + 1], "do\nit")
            self.assertIn("model-citizen-arm-bare:test", command)
            self.assertIn("model-citizen-arm-harness:test", launch.calls[1][0])
            workdir = mounts(command)[0].rsplit(":", 1)[0]
            self.assertFalse(Path(workdir).exists())
            self.assertEqual(list(Path(opts["tmp"]).iterdir()), [])

    def test_a_row_records_the_container_its_arm_ran_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            opts = options(tmp, reps=1, change_note="raised the effort dial")
            stream = json.dumps([call(None, ["Bash", "Task"], write=1234), result()])
            rows, _ = BENCH.replay([TASK], opts, Launch([stream] * 2))
            bare = [r for r in rows if r["arm"] == "bare"][0]
            harness = [r for r in rows if r["arm"] == "harness"][0]
            self.assertNotIn("arm_label", harness)  # `harness@<dotted ref>` reads as an email to the lint
            self.assertEqual(bare["arm_image_id"], opts["arms"]["bare"]["image_id"])
            self.assertEqual(harness["arm_manifest_sha256"], "a" * 64)
            self.assertEqual(harness["arm_declaration_sha256"], "d" * 64)
            self.assertEqual((harness["harness_ref"], harness["harness_commit"]), ("v9.9.9", "c" * 40))
            self.assertEqual((bare["harness_ref"], bare["harness_commit"]), (None, None))
            self.assertEqual(bare["arm_base_image"], "base@sha256:" + "0" * 64)
            for gone in ("arm_config_dir", "arm_fingerprint", "fingerprint_source"):
                self.assertNotIn(gone, bare)  # host-profile fields: no arm has a host profile now
            for row in rows:
                self.assertEqual(row["change_note"], "raised the effort dial")
                self.assertEqual((row["first_call_cache_write"], row["spawns"]), (1234, 1))
                self.assertEqual(row["tool_counts"], {"Bash": 1, "Task": 1})
                self.assertIsNone(row["hook_blocks"])
                self.assertNotIn(SECRET, json.dumps(row))

    def test_an_errored_row_carries_the_arm_fields_and_no_stream_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows, _ = BENCH.replay([TASK], options(tmp, reps=1), Launch(["garbage"] * 2))
            self.assertEqual([r["error"] for r in rows], [True, True])
            self.assertEqual(rows[0]["tool_counts"], {})
            self.assertIsNone(rows[0]["spawns"])
            self.assertEqual(rows[0]["arm_manifest_sha256"], "b" * 64)

    def test_a_run_that_times_out_has_its_container_removed_by_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = Launch([subprocess.TimeoutExpired("docker", 1), json.dumps(result())])
            rows, _ = BENCH.replay([TASK], options(tmp, reps=1), launch)
            run, kill = launch.calls[0][0], launch.calls[1][0]
            name = run[run.index("--name") + 1]
            self.assertTrue(name.startswith("model-citizen-arm-run-"))
            self.assertEqual(kill, ["docker", "rm", "--force", name])
            self.assertEqual(rows[0]["error_kind"], "timeout")

    def test_each_arm_must_write_its_mounted_snapshot_before_anything_is_measured(self):
        """A snapshot made by another user id can be read-only to the image's user, or refused by
        git as dubious ownership. The replay fails closed rather than measure a broken arm."""
        with tempfile.TemporaryDirectory() as tmp:
            launch = Launch([json.dumps(result())] * 2)
            BENCH.replay([TASK], options(tmp, reps=1), launch)
            self.assertEqual(len(launch.probes), 2)
            for probe, arm in zip(launch.probes, ("bare", "harness")):
                self.assertIn("model-citizen-arm-%s:test" % arm, probe)
                self.assertEqual(probe[probe.index("--network") + 1], "none")
                self.assertEqual(len(mounts(probe)), 1)
                self.assertIn("--name", probe)
            refused = Launch([], probe_exit=1)
            with self.assertRaises(SystemExit) as caught:
                BENCH.replay([TASK], options(Path(tmp) / "again", reps=1), refused)
            self.assertIn("cannot write the mounted snapshot", str(caught.exception))
            self.assertIn("Permission denied", str(caught.exception))
            self.assertEqual(refused.calls, [])

    def test_a_mounted_snapshot_is_writable_by_any_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = git_repo(Path(tmp) / "source")
            clone = BENCH.mounted_snapshot(repo, "HEAD", Path(tmp) / "clone")
            for path in [clone] + list(clone.rglob("*")):
                if not path.is_symlink():
                    self.assertEqual(path.stat().st_mode & 0o006, 0o006, path)
            self.assertTrue((clone / ".git").stat().st_mode & 0o001)

    def test_a_check_that_times_out_has_its_container_removed_by_name(self):
        calls = []

        def launch(command, **kwargs):
            calls.append(command)
            if command[:2] == ["docker", "run"]:
                raise subprocess.TimeoutExpired("docker", 1)
            return types.SimpleNamespace(stdout="", stderr="", returncode=0)
        issue = dict(TASK, kind="issue", good_sha="HEAD", tests={"copy": [], "pattern": "test_*.py"})
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(subprocess.TimeoutExpired):
                BENCH.score(issue, tmp, tmp, "model-citizen-arm-bare:test", launch)
            with self.assertRaises(subprocess.TimeoutExpired):
                BENCH.repo_gate(tmp, [["python3", "bin/harness", "lint"]], "model-citizen-arm-bare:test", launch)
        runs = [c for c in calls if c[:2] == ["docker", "run"]]
        kills = [c for c in calls if c[:2] == ["docker", "rm"]]
        self.assertEqual(len(runs), 2)
        for run, kill in zip(runs, kills):
            name = run[run.index("--name") + 1]
            self.assertTrue(name.startswith("model-citizen-arm-run-"))
            self.assertEqual(kill, ["docker", "rm", "--force", name])
        self.assertNotEqual(runs[0][runs[0].index("--name") + 1], runs[1][runs[1].index("--name") + 1])

    def test_an_arm_the_admission_seam_refuses_launches_nothing(self):
        """`replay_arms.admit` is the one place an arm is refused before anything of it runs."""
        with tempfile.TemporaryDirectory() as tmp:
            launch = Launch([])
            opts = options(tmp)
            opts["arms"]["harness"] = dict(opts["arms"]["harness"], manifest_sha256="")
            with self.assertRaises(SystemExit) as caught:
                BENCH.replay([TASK], dict(opts, skip_preflight=False), launch)
            self.assertIn("manifest_sha256", str(caught.exception))
            self.assertEqual(launch.calls, [])

    def test_by_default_the_check_runs_in_a_bare_container_with_no_network_or_credential(self):
        """The agent's tree is unreviewed code and a check executes it, so it never runs here."""
        with tempfile.TemporaryDirectory() as tmp:
            opts = options(tmp, reps=1)
            del opts["scorer"]
            oracle = opts["repo"] / BENCH.ORACLES / "none.py"
            oracle.parent.mkdir(parents=True)
            oracle.write_text("def check(root):\n    return []\n", encoding="utf-8")
            verdict = BENCH.ORACLE_MARK + "[]\n"
            launch = Launch([json.dumps(result()), verdict, json.dumps(result()), verdict])
            rows, _ = BENCH.replay([TASK], opts, launch)
            self.assertEqual([r["passed"] for r in rows], [True, True])
            for command, kwargs in (launch.calls[1], launch.calls[3]):
                self.assertIn("model-citizen-arm-bare:test", command)
                self.assertEqual(command[command.index("--network") + 1], "none")
                self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env_flags(command))
                self.assertEqual(len(mounts(command)), 1)
                self.assertEqual(command[-2:], ["python3", "-"])
                self.assertIn("-i", command)  # Docker drops stdin without it, and the oracle is on stdin
                self.assertIn("def check(root)", kwargs["input"])


def gate_reply(text, cost=0.1):
    """The CLI's JSON stream for a `-p` run in which one Bash call returned `text`: the gate's
    output as the tool saw it, which is what the pre-flight judges, plus a relay in `result`."""
    call = {"type": "assistant", "message": {"role": "assistant", "id": "m1", "content": [
        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "gate"}}]}}
    back = {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": text}]}}
    return json.dumps([call, back, dict(result(cost=cost), result=text.strip().splitlines()[-1] if text.strip() else "")])


GREEN = "lint: 0 finding(s) in /repo\n"
RED = "tests/test_x.py:1: home-directory path\nlint: 1 finding(s) in /repo\n"


class ReplayPreflightTests(unittest.TestCase):
    """The gate runs once per arm, in that arm's own container, before anything is scored."""
    def preflight_calls(self, launch):
        return [c for c in launch.calls if BENCH.PREFLIGHT_PROMPT in c[0]]

    def test_a_red_gate_refuses_the_replay_before_any_scored_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = Launch([gate_reply(GREEN), gate_reply(RED)])
            with self.assertRaises(SystemExit) as caught:
                BENCH.replay([TASK], options(tmp, reps=1, skip_preflight=False), launch)
            self.assertEqual(caught.exception.code, 2)
            self.assertEqual(len(self.preflight_calls(launch)), 2)
            self.assertEqual(len(launch.calls), 2)  # the scored schedule never launched

    def test_the_bar_is_lint_and_a_red_suite_does_not_refuse(self):
        """The full suite is profile-dependent at every snapshot commit, so a suite verdict in the
        output, or worktree-path noise after it, is neither the bar nor a refusal."""
        noisy = GREEN + "Ran 704 tests in 35s\n\nFAILED (failures=22)\n/private/tmp/x/worktrees/project/task-one\n"
        self.assertTrue(BENCH.gate_passed(gate_reply(noisy)))
        self.assertNotIn("unittest", BENCH.PREFLIGHT_PROMPT)
        self.assertFalse(BENCH.gate_passed(gate_reply("/private/tmp/x/task-one\n")))

    def test_a_red_gate_keeps_its_failure_lines_in_the_saved_stream(self):
        """A refusal has to be readable from the saved stream: the lint findings and any refused read."""
        self.assertIn("harness lint", BENCH.PREFLIGHT_PROMPT)
        with tempfile.TemporaryDirectory() as tmp:
            red = RED + "PermissionError: [Errno 1] Operation not permitted: '/x/rules'\n"
            launch = Launch([gate_reply(red), gate_reply(red)])
            opts = options(tmp, reps=1, skip_preflight=False); opts["raw"] = tmp
            with self.assertRaises(SystemExit):
                BENCH.replay([TASK], opts, launch)
            saved = (Path(tmp) / "preflight-bare.json").read_text(encoding="utf-8")
            self.assertIn("home-directory path", BENCH.gate_output(saved))
            self.assertIn("Operation not permitted", BENCH.gate_output(saved))

    def test_a_refused_read_is_red_even_when_lint_is_clean(self):
        blocked = GREEN + "PermissionError: [Errno 1] Operation not permitted: '/x/plans'\n"
        self.assertFalse(BENCH.gate_passed(gate_reply(blocked)))
        self.assertFalse(BENCH.gate_passed(gate_reply(GREEN.replace("lint: 0 finding(s)", "lint: 2 finding(s)"))))

    def test_unreadable_output_is_a_red_gate_and_never_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = Launch(["garbage", subprocess.TimeoutExpired("claude", 1)])
            checks, spent = BENCH.preflight([TASK], options(tmp), launch)
            self.assertEqual([c["passed"] for c in checks], [False, False])
            self.assertEqual([c["reply"] for c in checks], ["", "timeout"])
            self.assertEqual(spent, 0.5)  # a run with no readable cost is counted at its own cap

    def test_a_green_gate_stamps_every_scored_row_and_runs_in_each_arms_own_container(self):
        with tempfile.TemporaryDirectory() as tmp:
            opts = options(tmp, reps=1, skip_preflight=False)
            launch = Launch([gate_reply(GREEN)] * 2 + [json.dumps(result())] * 2)
            rows, stopped = BENCH.replay([TASK], opts, launch)
            self.assertEqual((stopped, len(rows)), (False, 2))
            self.assertEqual([r["preflight"] for r in rows], ["passed"] * 2)
            checks = self.preflight_calls(launch)
            self.assertEqual(len(checks), 2)
            for (command, kwargs), arm in zip(checks, ("bare", "harness")):
                self.assertEqual(command[command.index("--max-turns") + 1], "3")
                self.assertEqual(command[command.index("--max-budget-usd") + 1], "0.25")
                self.assertEqual(command[command.index("--model") + 1], "claude-test")
                self.assertIn(opts["arms"][arm]["image"], command)
                self.assertEqual(len(mounts(command)), 1)
                self.assertNotIn(SECRET, " ".join(command))

    def test_the_pre_flight_spends_against_the_same_cumulative_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = Launch([gate_reply(GREEN, cost=0.5)] * 2)
            rows, stopped = BENCH.replay([TASK], options(tmp, reps=1, skip_preflight=False,
                                                         spend_cap=2.5), launch)
            self.assertEqual((rows, stopped), ([], True))  # 1.0 spent, and 1.0 + 2.0 passes 2.5
            self.assertEqual(len(launch.calls), 2)

    def test_skipping_the_pre_flight_stamps_the_rows_and_launches_nothing_extra(self):
        with tempfile.TemporaryDirectory() as tmp:
            opts = options(tmp, reps=1)
            launch = Launch([json.dumps(result())] * 2)
            rows, _ = BENCH.replay([TASK], opts, launch)
            self.assertEqual([r["preflight"] for r in rows], ["skipped"] * 2)
            self.assertEqual(self.preflight_calls(launch), [])
            scored = json.loads(launch.calls[0][0][launch.calls[0][0].index("--settings") + 1])
            self.assertEqual(scored, BENCH.ARM_SETTINGS)


def row(arm, rep, passed, cost, error=False, bucket="", predicted=None, task="demo", note=""):
    return {"arm": arm, "rep": rep, "passed": None if error else passed, "error": error, "cost_usd": cost,
            "cost_normalised_usd": cost, "date": "2026-01-01", "harness_version": "9.9.9", "harness_sha": "a" * 40,
            "tag": "candidate", "model": "claude-test", "cli_version": "1.0", "bucket": bucket,
            "predicted_ratio": predicted, "task": task, "change_note": note}


class FixtureGateTests(unittest.TestCase):
    """`--verify-tasks` runs the task's gate and every check in the bare arm's container. On this
    machine an older snapshot read the owner's live configuration through HOME and went red for
    it, charging every task's gate with a failure that was the host's."""
    def verify(self, tmp, gate_exit=1, verdicts=('["no"]', '["no"]')):
        repo = git_repo(Path(tmp) / "source")
        oracle = repo / BENCH.ORACLES / "none.py"
        oracle.parent.mkdir(parents=True)
        oracle.write_text("def check(root):\n    return ['no']\n\n\ndef solve(root):\n    pass\n",
                          encoding="utf-8")
        answers, calls = list(verdicts), []

        def launch(command, **kwargs):
            calls.append((command, kwargs))
            if command[-2:] == ["python3", "-"]:
                return types.SimpleNamespace(stdout=BENCH.ORACLE_MARK + answers.pop(0) + "\n", returncode=0)
            return types.SimpleNamespace(stdout="", returncode=gate_exit)
        task = dict(TASK, parent_sha="HEAD", tests={"oracle": "none"})
        errors = BENCH.verify_tasks([task], repo, Path(tmp) / "work", "model-citizen-arm-bare:test",
                                    [["python3", "bin/harness", "lint"]], launch)
        return errors, calls

    def test_a_red_gate_in_a_clean_snapshot_is_reported_against_the_task_not_the_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors, _ = self.verify(tmp)
        self.assertIn("already fails in a clean snapshot", errors[0])
        self.assertIn("fails on the known-good tree", errors[-1])

    def test_the_gate_and_every_check_run_in_the_bare_container_with_no_host_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors, calls = self.verify(tmp, gate_exit=0, verdicts=("[\"no\"]", "[]"))
        self.assertEqual(errors, [])
        self.assertEqual(len(calls), 3)  # the gate, the check at the parent, the check once solved
        home = str(Path.home())
        for command, kwargs in calls:
            self.assertEqual(command[:3], ["docker", "run", "--rm"])
            self.assertIn("model-citizen-arm-bare:test", command)
            self.assertEqual(command[command.index("--network") + 1], "none")
            self.assertEqual(len(mounts(command)), 1)
            self.assertTrue(mounts(command)[0].endswith(":/work"))
            self.assertFalse([f for f in env_flags(command) if f.split("=", 1)[0] in
                              ("HOME", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN")])
            self.assertNotIn(home, " ".join(c for c in command if not c.endswith(":/work")))
            self.assertNotIn("cwd", kwargs)
        gate = calls[0][0]
        self.assertEqual(gate[-3:], ["python3", "bin/harness", "lint"])
        self.assertIn("PYTHONPATH=/work/lib", env_flags(gate))


class ReplaySummaryTests(unittest.TestCase):
    def test_cost_per_passed_task_is_the_mean_of_reps_and_failures_still_cost(self):
        rows = [row("bare", 1, True, 1.0), row("bare", 1, False, 1.0), row("bare", 2, True, 1.0),
                row("bare", 2, True, 1.0), row("harness", 1, True, 0.5), row("harness", 2, True, 0.7)]
        summary = BENCH.summarise(rows)
        self.assertEqual(summary["bare"], {"runs": 4, "errors": 0, "passed": 1.5, "cost_per_passed": 1.5})
        self.assertEqual(summary["harness"]["cost_per_passed"], 0.6)

    def test_errors_sit_outside_both_figures_and_are_counted(self):
        summary = BENCH.summarise([row("bare", 1, True, 1.0), row("bare", 1, None, 9.0, error=True)])
        self.assertEqual(summary["bare"], {"runs": 2, "errors": 1, "passed": 1.0, "cost_per_passed": 1.0})

    def test_the_threshold_needs_both_the_ratio_and_the_pass_count(self):
        def pair(harness_cost, harness_passed, bare_passed=4.0):
            return {"bare": {"cost_per_passed": 1.0, "passed": bare_passed},
                    "harness": {"cost_per_passed": harness_cost, "passed": harness_passed}}
        self.assertEqual(BENCH.verdict(pair(0.85, 3.0)), (0.85, "passed"))
        self.assertEqual(BENCH.verdict(pair(0.86, 4.0)), (0.86, "failed"))
        self.assertEqual(BENCH.verdict(pair(0.5, 2.5)), (0.5, "failed"))  # cheaper, passing too few
        self.assertEqual(BENCH.verdict(pair(None, 0.0)), (None, "failed"))  # passed nothing: no ratio needed
        self.assertEqual(BENCH.verdict(pair(None, 3.5)), (None, "inconclusive"))

    def test_history_holds_one_row_per_version_and_day_and_renders_without_em_dashes(self):
        rows = [row("bare", 1, True, 1.0), row("harness", 1, True, 0.5)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.jsonl"
            BENCH.upsert_history(path, BENCH.history_row(rows, "s1"))
            kept = BENCH.upsert_history(path, BENCH.history_row(rows, "s1"))
            self.assertEqual(len(kept), 1)
            self.assertEqual(len(BENCH.upsert_history(path, BENCH.history_row(rows, "s2"))), 2)
        self.assertEqual((kept[0]["ratio"], kept[0]["status"]), (0.5, "passed"))
        text = BENCH.render_history(kept)
        self.assertIn("| 0.500 |", text)
        self.assertNotIn("\u2014", text)

    def test_two_buckets_on_one_day_and_commit_are_two_rows_not_one(self):
        """The programme changes one thing at a time, so several buckets share a day and a sha.
        Before the bucket joined the key each row silently replaced the one before it."""
        first = [row("bare", 1, True, 1.0, bucket="A"), row("harness", 1, True, 0.5, bucket="A")]
        second = [row("bare", 1, True, 1.0, bucket="C"), row("harness", 1, True, 0.4, bucket="C")]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.jsonl"
            BENCH.upsert_history(path, BENCH.history_row(first, "s1"))
            kept = BENCH.upsert_history(path, BENCH.history_row(second, "s1"))
            self.assertEqual([r["bucket"] for r in kept], ["A", "C"])
            again = BENCH.upsert_history(path, BENCH.history_row(second, "s1"))
            self.assertEqual(len(again), 2)

    def test_a_row_carries_the_ratio_its_bucket_was_predicted_to_produce(self):
        rows = [row("bare", 1, True, 1.0, bucket="A", predicted=1.03),
                row("harness", 1, True, 0.5, bucket="A", predicted=1.03)]
        built = BENCH.history_row(rows, "s1")
        self.assertEqual((built["bucket"], built["predicted_ratio"], built["ratio"]), ("A", 1.03, 0.5))
        text = BENCH.render_history([built])
        self.assertIn("| A |", text)
        self.assertIn("| 1.030 |", text)

    def test_a_row_written_before_buckets_existed_still_renders(self):
        legacy = BENCH.history_row([row("bare", 1, True, 1.0), row("harness", 1, True, 0.5)], "s1")
        del legacy["bucket"], legacy["predicted_ratio"], legacy["per_task"], legacy["change_note"]
        self.assertIn("| n/a |", BENCH.render_history([legacy]))

    def test_the_history_row_breaks_the_aggregate_down_by_task_with_each_cell_s_spread(self):
        """One task moving is the usual shape of a regression, and a cell whose reps differ by half
        says the aggregate above it is noise. Both are unreadable from the aggregate alone."""
        rows = [row("bare", 1, True, 1.0, task="alpha"), row("bare", 2, True, 2.0, task="alpha"),
                row("harness", 1, True, 0.5, task="alpha"), row("harness", 2, True, 0.5, task="alpha"),
                row("bare", 1, True, 4.0, task="beta"), row("harness", 1, True, 8.0, task="beta")]
        cells = BENCH.history_row(rows, "s1")["per_task"]
        self.assertEqual(cells["alpha"], {"bare": 1.5, "harness": 0.5, "ratio": 0.3333,
                                          "bare_spread": 2.0, "harness_spread": 1.0, "n": 2})
        self.assertEqual(cells["beta"]["ratio"], 2.0)
        self.assertIsNone(cells["beta"]["bare_spread"])  # one priced run has no spread

    def test_the_rendered_ledger_carries_the_task_lines_and_the_change_note(self):
        rows = [row("bare", 1, True, 1.0, task="alpha", note="moved the skills out of context"),
                row("bare", 2, True, 2.0, task="alpha", note="moved the skills out of context"),
                row("harness", 1, True, 0.5, task="alpha", note="moved the skills out of context"),
                row("harness", 2, True, 0.5, task="alpha", note="moved the skills out of context")]
        text = BENCH.render_history([BENCH.history_row(rows, "s1")])
        self.assertIn("    note: moved the skills out of context", text)
        self.assertIn("    alpha: bare 1.500, harness 0.500, ratio 0.333, spread bare 2.000 /"
                      " harness 1.000, n 2", text)
        self.assertIn("| 9.9.9 @ aaaaaaa |", text)  # the aggregate columns are untouched
        self.assertNotIn("—", text)

    def test_an_errored_or_unpriced_run_is_left_out_of_its_task_cell(self):
        rows = [row("bare", 1, True, 1.0, task="alpha"), row("bare", 2, None, 9.0, True, task="alpha"),
                row("harness", 1, True, 0.5, task="alpha")]
        cells = BENCH.per_task(rows)
        self.assertEqual((cells["alpha"]["bare"], cells["alpha"]["bare_spread"]), (1.0, None))
        self.assertEqual(cells["alpha"]["n"], 2)  # the rep happened, even though it priced nothing


class ArmFingerprintTests(unittest.TestCase):
    def test_the_fingerprint_counts_the_layers_and_moves_when_a_file_changes_length(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = config_tree(Path(tmp) / "profile")
            first = BENCH.config_fingerprint(root)
            self.assertEqual({k: v for k, v in first.items() if k != "sha"},
                             {"rules": 2, "skills": 1, "agents": 1, "personal_bytes": 64})
            (root / "rules" / "rule-0.md").write_text("r" * 21, encoding="utf-8")
            self.assertNotEqual(BENCH.config_fingerprint(root)["sha"], first["sha"])
            (root / "rules" / "rule-0.md").write_text("q" * 21, encoding="utf-8")
            same_size = BENCH.config_fingerprint(root)
            self.assertNotEqual(same_size["sha"], first["sha"])
            self.assertEqual(len(same_size["sha"]), 8)
            self.assertEqual(BENCH.config_fingerprint(root / "gone"),
                             {"sha": BENCH.config_fingerprint(Path(tmp) / "empty")["sha"], "rules": 0,
                              "skills": 0, "agents": 0, "personal_bytes": 0})

    def test_an_inherited_arm_is_fingerprinted_from_the_home_profile_and_named_not_pathed(self):
        """A row must survive being read by anyone, so it carries `inherited` and sizes, never the
        owner's home directory. The repository's own lint rejects a home path in a file."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            config_tree(home / ".claude", rules=3)
            self.assertEqual(BENCH.config_fingerprint(BENCH.INHERITED, home)["rules"], 3)
            self.assertEqual(BENCH.config_fingerprint(None, home),
                             BENCH.config_fingerprint(home / ".claude", home))
            self.assertEqual(BENCH.config_label(None, home), "inherited")
            self.assertEqual(BENCH.config_label(home / ".claude-bare", home), "~/.claude-bare")
            self.assertEqual(BENCH.config_label("/opt/profile", home), "/opt/profile")


class BackfillTests(unittest.TestCase):
    def written(self, tmp, rows, raw):
        results = Path(tmp) / "results"
        results.mkdir()
        BENCH.write_jsonl(results / BENCH.RESULTS, rows)
        folder = Path(tmp) / "raw"
        folder.mkdir()
        for name, text in raw.items():
            (folder / name).write_text(text, encoding="utf-8")
        return results, folder

    def test_backfill_enriches_a_copy_and_leaves_the_original_alone(self):
        stream = json.dumps([call(None, ["Bash", "Task"], write=777), result()])
        with tempfile.TemporaryDirectory() as tmp:
            profile = config_tree(Path(tmp) / "profile")
            old = [row("bare", 1, True, 1.0), row("harness", 1, True, 0.5)]
            for stale in old:
                stale.pop("change_note")
            results, raw = self.written(tmp, old, {"demo-bare-1.json": stream,
                                                   "demo-harness-1.json": stream})
            before = (results / BENCH.RESULTS).read_text(encoding="utf-8")
            code = BENCH.main(["backfill", "--results", str(results), "--raw", str(raw),
                               "--config-dir", str(profile)])
            self.assertEqual(code, 0)
            self.assertEqual((results / BENCH.RESULTS).read_text(encoding="utf-8"), before)
            enriched = BENCH.read_jsonl(results / BENCH.ENRICHED)
            self.assertEqual([r["spawns"] for r in enriched], [1, 1])
            self.assertEqual(enriched[0]["first_call_cache_write"], 777)
            self.assertEqual(enriched[0]["tool_counts"], {"Bash": 1, "Task": 1})
            self.assertEqual(enriched[0]["change_note"], "")
            self.assertEqual(enriched[0]["cost_usd"], 1.0)  # the original figures are untouched
            self.assertEqual(enriched[0]["arm_config_dir"], str(profile))
            self.assertEqual(enriched[0]["arm_fingerprint"], BENCH.config_fingerprint(profile))
            for row_out in enriched:
                self.assertEqual(row_out["fingerprint_source"], "backfill")

    def test_a_row_with_no_raw_output_is_reported_and_keeps_its_fields_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            results, raw = self.written(tmp, [row("bare", 1, True, 1.0)], {})
            code = BENCH.main(["backfill", "--results", str(results), "--raw", str(raw),
                               "--inherited", "--in-place"])
            self.assertEqual(code, 1)
            enriched = BENCH.read_jsonl(results / BENCH.ENRICHED)
            self.assertEqual(enriched[0]["tool_counts"], {})
            self.assertIsNone(enriched[0]["spawns"])
            self.assertEqual(enriched[0]["arm_config_dir"], "inherited")
            self.assertEqual(BENCH.read_jsonl(results / BENCH.RESULTS), enriched)  # --in-place

    def test_a_missing_results_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                BENCH.main(["backfill", "--results", tmp, "--raw", tmp])


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tasks = BENCH.load_tasks(REPO / BENCH.TASKS)

    def test_every_solved_issue_and_synthetic_task_is_pinned_by_full_sha(self):
        kinds = [t["kind"] for t in self.tasks]
        self.assertEqual((kinds.count("issue"), kinds.count("synthetic")), (5, 2))
        self.assertEqual(len(kinds), len(set(t["id"] for t in self.tasks)))
        for task in self.tasks:
            self.assertRegex(task["parent_sha"], r"^[0-9a-f]{40}$")
            if task["kind"] == "issue":
                self.assertRegex(task["good_sha"], r"^[0-9a-f]{40}$")
            else:
                self.assertTrue((REPO / BENCH.ORACLES / (task["tests"]["oracle"] + ".py")).is_file())

    def test_a_prompt_never_names_its_held_back_check(self):
        for task in self.tasks:
            prompt = BENCH.prompt_of(task)
            for held in task["tests"].get("copy", []) + task["tests"].get("select", []) + ["oracle"]:
                self.assertNotIn(held, prompt, msg=task["id"])

    def test_every_task_declares_a_known_leak_class_the_loader_ignores(self):
        for task in self.tasks:
            self.assertIn(task["leak_class"], ("clean", "leaks", "control"), msg=task["id"])
        self.assertEqual([t["id"] for t in self.tasks if t["leak_class"] != "clean"],
                         ["link-alias", "cost-variants"])

    def test_a_malformed_task_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            path.write_text(json.dumps({"tasks": [{"id": "x", "kind": "issue"}]}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                BENCH.load_tasks(path)


class OracleTests(unittest.TestCase):
    # The fixture builds exactly as many modules as the oracle expects, which is the count at the
    # task's pinned `parent_sha`; `tests/test_doc_figures_derive_from_code.py` holds that constant to the glob
    # at that sha. A repin moves both without a hand-typed number here.
    MODULES = BENCH._oracle(REPO, "hook_ids").EXPECTED_MODULES

    def hooks(self, tmp):
        folder = Path(tmp) / "policy" / "hooks"
        folder.mkdir(parents=True)
        for index in range(self.MODULES):
            future = "from __future__ import annotations\n" if index == 0 else ""
            (folder / ("hook-%02d.py" % index)).write_text(
                '"""Hook %d does  one thing.\n\nMore.\n"""\n%simport os\n\n\ndef run():\n    def inner():\n'
                '        pass\n\n\nclass K:\n    def method(self):\n        pass\n' % (index, future), encoding="utf-8")
        return Path(tmp)

    def test_the_inventory_check_fails_empty_passes_solved_and_catches_one_wrong_count(self):
        oracle = BENCH._oracle(REPO, "hook_inventory")
        with tempfile.TemporaryDirectory() as tmp:
            root = self.hooks(tmp)
            self.assertEqual(oracle.check(root), ["hook-inventory.json is missing"])
            oracle.solve(root)
            self.assertEqual(oracle.check(root), [])
            data = json.loads((root / oracle.OUTPUT).read_text(encoding="utf-8"))
            self.assertEqual(data["hook-03.py"], {"summary": "Hook 3 does one thing.", "top_level_functions": 1})
            data["hook-03.py"]["top_level_functions"] = 2
            (root / oracle.OUTPUT).write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(len(oracle.check(root)), 1)

    def test_the_constant_check_fails_untouched_passes_solved_and_catches_a_broken_module(self):
        oracle = BENCH._oracle(REPO, "hook_ids")
        with tempfile.TemporaryDirectory() as tmp:
            root = self.hooks(tmp)
            self.assertEqual(len(oracle.check(root)), self.MODULES)
            oracle.solve(root)
            self.assertEqual(oracle.check(root), [])
            first = root / "policy" / "hooks" / "hook-00.py"
            first.write_text('HOOK_ID = "hook-00"\n' + first.read_text(encoding="utf-8"), encoding="utf-8")
            self.assertEqual(len(oracle.check(root)), 1)  # before the __future__ import: no longer compiles


if __name__ == "__main__":
    unittest.main()

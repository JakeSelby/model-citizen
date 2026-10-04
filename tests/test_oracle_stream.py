"""A check written `check(root, stream=None)` gets the scored run's saved stream-json (#1177): the
stream is mounted read-only beside the tree, a one-argument check is called as before, and the row
says whether the stream went in. No container is started and no model called: a local Python plays
the check container, reading the tree and the stream where the container's mounts would put them."""
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

from test_cost_bench import BENCH, mounts, options, result
from test_replay_pack import CANARY, make_pack, task_spec

PACK = BENCH.replay_pack
DECLARED = {"tool_results": "lower", "subagent_results": "lower"}

# The pack's contract in miniature: stream metrics from the stream, null with a reason without one.
CHECK_STREAM = '''# %s
import json
from pathlib import Path

NO_STREAM = "stream metrics unknown: no stream was given"


def check(root, stream=None):
    passed = (Path(root) / "README.md").is_file()
    if stream is None:
        return {"pass": passed, "metrics": {"tool_results": None, "subagent_results": None},
                "errors": [NO_STREAM]}
    messages = [json.loads(line) for line in Path(stream).read_text(encoding="utf-8").splitlines()
                if line.strip()]
    results = [m.get("parent_tool_use_id") for m in messages if m.get("type") == "user"
               for block in m["message"]["content"] if block.get("type") == "tool_result"]
    return {"pass": passed, "errors": [],
            "metrics": {"tool_results": len(results), "subagent_results": len([t for t in results if t])}}
''' % CANARY
CHECK_ONE_ARGUMENT = ('# %s\ndef check(root):\n    return {"pass": True, "metrics": '
                      '{"tool_results": None, "subagent_results": None}}\n' % CANARY)
CHECK_UNREADABLE = ('# %s\ndef check(root, stream=None):\n    return {"pass": True, "errors": '
                    '["stream metrics unknown: bad line", "unrelated note"], '
                    '"metrics": {"tool_results": None, "subagent_results": None}}\n' % CANARY)
CHECK_VARARGS = ('# %s\ndef check(*args):\n    return {"pass": len(args) == 2 and args[1] is not None, '
                 '"metrics": {"tool_results": 1, "subagent_results": 0}}\n' % CANARY)
# A one-argument check printing the stream marker the driver once used, as code loaded from the
# agent's tree could: whether the stream went in is the runner's call, so the row says it did not.
CHECK_FORGES_MARKER = ('# %s\ndef check(root):\n    print("cost-bench-oracle-stream: true")\n'
                       '    return {"pass": True, "metrics": {"tool_results": 1, "subagent_results": 0}}\n' % CANARY)
CHECK_SILENT_NULL = ('# %s\ndef check(root, stream=None):\n    return {"pass": True, "errors": [], '
                     '"metrics": {"tool_results": None, "subagent_results": 0}}\n' % CANARY)


def synthetic_stream():
    """A made-up `-p` stream: one main-thread Read, one spawn, and one Read inside the subagent."""
    def use(thread, tool_id, name):
        return {"type": "assistant", "parent_tool_use_id": thread,
                "message": {"id": "m-" + tool_id, "model": "claude-test", "usage": {},
                            "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": {}}]}}

    def answer(thread, tool_id):
        return {"type": "user", "parent_tool_use_id": thread,
                "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}]}}

    events = [use(None, "t1", "Read"), answer(None, "t1"), use(None, "t2", "Agent"),
              use("t2", "t3", "Read"), answer("t2", "t3"), answer(None, "t2"), result()]
    return "".join(json.dumps(e) + "\n" for e in events)


def local_container(command, **kwargs):
    """The check container, played by a local Python: each container path in the driver is
    rewritten to the host path the command mounts there, as Docker would resolve it."""
    source = kwargs["input"]
    for mount in mounts(command):
        host, inside = mount.split(":")[:2]
        source = source.replace(repr(inside), repr(host))
    done = subprocess.run([sys.executable, "-"], input=source, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, universal_newlines=True)
    return types.SimpleNamespace(stdout=done.stdout, stderr=done.stderr, returncode=done.returncode)


def fixture_task(tmp, check):
    (Path(tmp) / "check.py").write_text(check, encoding="utf-8")
    return {"id": "stream-task", "kind": "pack", "tests": {"oracle": "stream-task"},
            "pack": {"task_dir": str(tmp)}, "metrics": DECLARED}


class ScoreTests(unittest.TestCase):
    def score(self, check, stream_text):
        calls = []

        def launch(command, **kwargs):
            calls.append(command)
            return local_container(command, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            tree.mkdir()
            (tree / "README.md").write_text("an app\n", encoding="utf-8")
            stream = None
            if stream_text is not None:
                stream = Path(tmp) / "session-stream.jsonl"
                stream.write_text(stream_text, encoding="utf-8")
            got = BENCH.score(fixture_task(tmp, check), tree, None, "model-citizen-arm-bare:test", launch,
                              stream=stream)
        return got, calls[0]

    def test_a_stream_check_scores_the_stream_mounted_read_only(self):
        got, command = self.score(CHECK_STREAM, synthetic_stream())
        self.assertEqual(got, (True, "", {"metrics": {"subagent_results": 1.0, "tool_results": 3.0},
                                          "metric_errors": [], "metric_stream": True}))
        self.assertEqual([m.split(":", 1)[1] for m in mounts(command)],
                         ["/work", BENCH.arms.SESSION_STREAM + ":ro"])

    def test_with_no_stream_the_metrics_are_null_with_a_reason_and_nothing_extra_is_mounted(self):
        got, command = self.score(CHECK_STREAM, None)
        self.assertEqual(got, (True, "stream metrics unknown: no stream was given",
                               {"metrics": {"subagent_results": None, "tool_results": None},
                                "metric_errors": [BENCH.NO_STREAM], "metric_stream": False}))
        self.assertEqual(len(mounts(command)), 1)

    def test_an_unreadable_stream_is_unknown_with_the_checks_own_reason(self):
        got, _ = self.score(CHECK_UNREADABLE, "not json\n")
        self.assertEqual(got[2], {"metrics": {"subagent_results": None, "tool_results": None},
                                  "metric_errors": ["stream metrics unknown: bad line"], "metric_stream": True})

    def test_a_one_argument_check_is_called_as_before_and_records_no_stream(self):
        got, _ = self.score(CHECK_ONE_ARGUMENT, synthetic_stream())
        self.assertEqual(got[0], True)
        self.assertEqual(got[2]["metric_stream"], False)
        self.assertEqual(got[2]["metric_errors"], [BENCH.NO_STREAM])

    def test_a_check_taking_any_positional_arguments_gets_the_stream(self):
        got, _ = self.score(CHECK_VARARGS, synthetic_stream())
        self.assertEqual((got[0], got[2]["metric_stream"]), (True, True))

    def test_a_stream_marker_printed_by_the_check_is_ignored(self):
        got, _ = self.score(CHECK_FORGES_MARKER, synthetic_stream())
        self.assertEqual(got[2]["metric_stream"], False)
        self.assertEqual(got[2]["metric_errors"], [BENCH.NO_STREAM])

    def test_a_null_stream_metric_without_a_reason_gets_one(self):
        got, _ = self.score(CHECK_SILENT_NULL, synthetic_stream())
        self.assertEqual(got[2], {"metrics": {"subagent_results": 0.0, "tool_results": None},
                                  "metric_errors": [BENCH.STREAM_NULL % "tool_results"], "metric_stream": True})


class TakesStreamTests(unittest.TestCase):
    def test_the_signature_is_read_from_the_source_without_running_it(self):
        cases = [("def check(root, stream=None): pass", True),
                 ("def check(*args): pass", True),
                 ("def check(root, /, stream): pass", True),
                 ("def check(root): pass", False),
                 ("def check(root, *, stream=None): pass", False),
                 ("def check(root, stream): pass\ncheck = print", False),
                 ("def check(root, stream): pass\nfrom os import path as check", False),
                 ("check = lambda root, stream: []", False),
                 ("def check(root, stream): pass\nif True:\n    check = None", False),
                 ("def check(root, stream:", False),
                 ("raise SystemExit('ran')\ndef check(root, stream): pass", True)]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertIs(BENCH.takes_stream(source), expected)


class EndToEndTests(unittest.TestCase):
    def test_a_pack_run_hands_its_whole_stream_to_the_check_and_the_row_carries_the_metrics(self):
        stream = synthetic_stream()

        def launch(command, **kwargs):
            if "input" in kwargs:  # the check container
                return local_container(command, **kwargs)
            return types.SimpleNamespace(stdout=stream, stderr="", returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            pack = PACK.open_pack(make_pack(Path(tmp) / "pack", tasks=[task_spec("rt-stream", metrics=DECLARED)],
                                            check=CHECK_STREAM), harness_root=Path(tmp) / "harness")
            self.addCleanup(PACK.close_pack, pack)
            task = PACK.load_set(pack, "production", "production")[0][0]
            opts = options(tmp, reps=1)
            del opts["scorer"]
            row = BENCH.run_one(task, 1, "bare", opts, launch)
        self.assertEqual(row["outcome"], "pass")
        self.assertIs(row["metric_stream"], True)
        # Three tool results, one of them the subagent's: the stream is the whole session's.
        self.assertEqual(row["metrics"], {"subagent_results": 1.0, "tool_results": 3.0})
        self.assertEqual(row["metric_errors"], [])


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: MIT
"""Corpus tests for the rule-detector registry.

`CASES` is keyed by detector id and holds `(events, expected_hits)` pairs. A test asserts
that every id in `DETECTORS` has at least one case that hits and one that does not, so a
detector cannot enter the registry without evidence on both sides.

Secret-shaped and identifier-shaped fixtures are assembled at run time; the lint scans
this file like every other.

Run: python3 -m unittest discover tests
"""
import importlib.util
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODULE = REPO / "claude" / "hooks" / "rule-detectors.py"
spec = importlib.util.spec_from_file_location("rule_detectors", MODULE)
rd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rd)

# `concise` turns every voice detector on, the concise-only ones included.
STANCES = {"commits": "conventional-attributed", "voice": "concise"}
FAKE_KEY = "AKIA" + "Q" * 16


# --- fixture helpers ---------------------------------------------------------------


def bash(command, turn=1, id="tu1"):
    return {"kind": "tool_use", "turn": turn, "id": id, "name": "Bash",
            "input": {"command": command}}


def tool_use(name, data, turn=1, id="tu1"):
    return {"kind": "tool_use", "turn": turn, "id": id, "name": name, "input": data}


def tool_result(text, tool_name="Agent", tool_use_id="tu1", turn=1):
    return {"kind": "tool_result", "turn": turn, "tool_use_id": tool_use_id,
            "tool_name": tool_name, "text": text}


def say(text, turn=1, final=True, model="claude-opus-5"):
    return {"kind": "assistant_text", "turn": turn, "text": text, "final": final, "model": model}


def prompt(turn=1):
    return {"kind": "user_prompt", "turn": turn}


def compact(turn=1, trigger=None):
    event = {"kind": "compact", "turn": turn}
    if trigger is not None:
        event["trigger"] = trigger
    return event


def fence(*lines):
    return "\n".join(["Here is the command:", "```sh"] + list(lines) + ["```", "done."])


def searches(n):
    return [tool_use("WebSearch", {"query": "q"}, id="s%d" % i) for i in range(n)]


TRAILER = "Co-Authored-By: Claude Fable 5.1"
# The form Claude Code writes for nearly every commit: the message is a heredoc inside a
# command substitution bound to -m.
CC_FORM = "git commit -m \"$(cat <<'EOF'\n%s\n\nCloses #1\n%s\nEOF\n)\""
CC_OK = CC_FORM % ("feat(cli): add a flag", TRAILER)
CC_NO_TRAILER = "git commit -m \"$(cat <<'EOF'\nfeat(cli): add a flag\n\nCloses #1\nEOF\n)\""
CC_BAD_SUBJECT = CC_FORM % ("fixed the thing", TRAILER)
# A heredoc that belongs to another command in the same call is not the commit message.
AMEND_AFTER_HEREDOC = "cat > notes.md <<EOF\nsome notes\nEOF\ngit commit --amend --no-edit"
# One command over three physical lines, and a message with real newlines in it.
CONTINUED = "git commit \\\n  -m \"feat(cli): add a flag\" \\\n  -m \"%s\"" % TRAILER
MULTILINE_MESSAGE = 'git commit -m "feat(cli): add a flag\n\n%s"' % TRAILER
MULTILINE_LONG_FLAG = 'git commit --message="feat(cli): add a flag\n\n%s"' % TRAILER
# A message the parse never opened: judged as no message at all.
SUB_MESSAGE = 'git commit -m "$(cat msg.txt)"'
SUB_HEREDOC_MESSAGE = "git commit -m \"$(cat <<'EOF' | head -1\nfixed the thing\nEOF\n)\""
HUGE = "echo " + "x" * (100 * 1024)
# The deny reason the grade hook writes. The parenthesised signature is what the detector
# matches, because the hook is what signs it; `test_the_deny_signature_is_the_one_the_grade_hook_writes`
# holds the two together.
DENIED = ("grade 3, irreversible: git push --force origin main rewrites remote history "
          "(grade-bash hook, autonomy=execute)")


CASES = {
    "transcript-hygiene/whole-file-cat": [
        ([bash("cat docs/how-it-works.md")], 1),
        ([bash("cat a | head -20")], 0),
        ([bash("cat <<EOF > out.txt\nbody\nEOF")], 0),
        ([bash("sed -n '1,40p' docs/how-it-works.md")], 0),
        ([bash("cat a b")], 0),
        ([bash("cat \\\n  foo.txt | grep x")], 0),
        ([bash("cat \\\n  foo.txt")], 1),
    ],
    "transcript-hygiene/unfiltered-find": [
        ([bash("find .")], 1),
        ([bash("find . -name x")], 0),
        ([bash("find . | head -20")], 0),
        ([bash("find src -type f -maxdepth 2")], 0),
        ([bash("find . -regex '.*py'")], 0),
        ([bash("find . -exec cat {} \\;")], 0),
        ([bash("find . > list.txt")], 0),
    ],
    "transcript-hygiene/model-wrote-no-cap": [
        ([tool_use("Agent", {"subagent_type": "general-purpose", "prompt": "Summarise the module."})], 1),
        ([tool_use("Agent", {"subagent_type": "general-purpose",
                             "prompt": "Summarise the module, keeping it short."})], 1),
        ([tool_use("Agent", {"subagent_type": "gatherer", "prompt": "Summarise the module."})], 0),
    ] + [([tool_use("Agent", {"subagent_type": "general-purpose", "prompt": "Read the module. " + cap})], 0)
         for cap in ("Summarise it in at most 400 words.", "Cap the return at 400 words.",
                     "400 words max.", "400 words or fewer.", "Answer within 400 words.",
                     "≤ 400 words.", "This brief carries a 400-word cap.",
                     "Word cap 400.")],
    "delegation/executed-from-summary": [
        ([tool_result(fence("pytest  -q   tests/")), bash("pytest -q tests/", id="tu2")], 1),
        ([tool_result(fence("pytest -q tests/")), bash("cd /repo && pytest -q tests/", id="tu2")], 1),
        # A command the session ran first, quoted back by a subagent, is repetition, not
        # execution from a summary.
        ([bash("pytest -q"), tool_result(fence("pytest -q"), tool_use_id="tu2"),
          bash("pytest -q", id="tu3")], 0),
        ([tool_result(fence("pytest -q")), bash("pytest -q", id="tu2")], 1),
        # A four-backtick block holding a three-backtick block closes only on four.
        ([tool_result("````\n```\npytest -q tests/\n```\n````"), bash("pytest -q tests/", id="tu2")], 1),
        ([tool_result(fence("pytest -q tests/")), bash("pytest -q tests/unit", id="tu2")], 0),
        ([tool_result("pytest -q tests/", tool_name="Read"), bash("pytest -q tests/", id="tu2")], 0),
        ([bash("pytest -q tests/")], 0),
    ],
    "verification/no-verify": [
        ([bash("git commit --no-verify -m 'feat(x): y'")], 1),
        ([bash("cd x && git commit --no-verify")], 1),
        ([bash("git commit -n -m 'feat(x): y'")], 1),
        ([bash("SKIP=ruff git commit -m 'feat(x): y'")], 1),
        ([bash("SKIP=ruff pre-commit run --all-files")], 1),
        ([bash("git -c core.hooksPath=/dev/null commit -m 'feat(x): y'")], 1),
        ([bash("git commit -m 'feat(x): y'")], 0),
        ([bash("git push -n origin main")], 0),
        # The flag and the assignment named, not used: an argument to another command.
        ([bash("grep -rn 'SKIP=' docs/")], 0),
        ([bash("grep -rn -- --no-verify docs/")], 0),
        ([bash("rg 'core.hooksPath=' docs/")], 0),
    ],
    "secrets/secret-in-write": [
        ([tool_use("Write", {"file_path": "a.py", "content": "KEY = '%s'\n" % FAKE_KEY})], 1),
        ([tool_use("Edit", {"file_path": "a.py", "new_string": "key = '%s'" % FAKE_KEY})], 1),
        ([bash("cat > .env <<EOF\nAWS_ACCESS_KEY_ID=%s\nEOF" % FAKE_KEY)], 1),
        ([tool_use("Write", {"file_path": "a.py", "content": "KEY = os.environ['AWS_KEY']\n"})], 0),
        ([bash("cat > a.txt <<EOF\nnothing secret\nEOF")], 0),
    ],
    "secrets/git-add-secret-file": [
        ([bash("git add .env")], 1),
        ([bash("git add .env.local")], 1),
        ([bash("git add -A config/credentials")], 1),
        ([bash("git add -A config/credentials.json")], 1),
        ([bash("git add ~/.ssh/id_ed25519")], 1),
        ([bash("git add certs/server.pem")], 1),
        ([bash("git add .env.example")], 0),
        ([bash("git add .env.sample .env.template .env.dist")], 0),
        ([bash("git add docs/credentials-policy.md")], 0),
        ([bash("git add README.md")], 0),
        ([bash("git add -A")], 0),
    ],
    "research/search-over-cap": [
        (searches(rd.SEARCH_CAP + 1), 1),
        (searches(rd.SEARCH_CAP), 0),
        ([], 0),
    ],
    "cache-hygiene/model-switch": [
        ([say("a", model="claude-opus-5"), say("b", turn=2, model="claude-sonnet-5")], 1),
        ([say("a", model="claude-opus-5"), say("b", turn=2, model="claude-opus-5")], 0),
        ([say("a", model="claude-opus-5"), say("b", turn=2, model="<synthetic>"),
          say("c", turn=3, model="claude-opus-5")], 0),
        ([say("a", model="")], 0),
    ],
    "cache-hygiene/compact": [
        ([compact(), prompt(turn=2)], 1),
        ([compact(), compact(turn=2)], 2),
        ([prompt(), say("a")], 0),
        # An automatic compaction with no recorded size is never the projected reset, whatever
        # sync wrote; the exemption itself is `tests/test_context_reset_docs.py`'s.
        ([compact(trigger="manual"), compact(turn=2, trigger="auto")], 2),
    ],
    "voice/banned-opener": [
        ([say("I started by reading the hook.")], 1),
        ([say("Fixed. Let me know if you want the other half too.")], 1),
        ([say("Great question — the hook fires first.")], 1),
        ([say("**Great question** — the hook fires first.")], 1),
        ([say("## After investigating\n\nthe hook fires first.")], 1),
        ([say("Fixed. The hook fires before the permission check.")], 0),
        # The phrase under discussion, not the phrase in use.
        ([say("Fixed: removed the phrase 'Let me know if' from the draft.")], 0),
        ([say("Fixed: the style bans `Let me know if` as a closer.")], 0),
        ([say("I started by reading the hook.", final=False)], 0),
    ],
    "voice/second-table": [
        ([say("| a | b |\n| - | - |\n\ntext\n\n| c | d |\n| - | - |")], 1),
        ([say("| a | b |\n| - | - |\n\ntext")], 0),
        ([say("no tables here")], 0),
    ],
    "voice/scaffold-leak": [
        ([say("Done.\n\n**What changed**\n\n- the hook")], 1),
        ([say("Done.\n\n## What you need to do\n\nRun the sync.")], 1),
        ([say("- **Still open:** the Linux client.")], 1),
        # A colon after the closing bold is the commonest template form.
        ([say("**What changed**: the hook")], 1),
        ([say("- **Still open**: the Linux client.")], 1),
        ([say("Done.\n\n## **What changed:**\n\n- the hook")], 1),
        ([say("Why: the cache was stale.")], 1),
        ([say("What Changed:\n- the hook")], 1),
        ([say("**What changed** \u2014 the fix")], 1),
        ([say("The Catch: it costs a line.")], 1),
        # One hit per message, however many labels it wears.
        ([say("**What changed**\n\n**Verification**\n\n**Still open**")], 1),
        ([say("Merged #214, Fix the login redirect loop.")], 0),
        ([say("Why this matters: nothing, the style is prose.")], 0),
        ([say("I fixed the redirect loop. The tests pass.")], 0),
        # A fix report may carry its status label; the stance allows it.
        ([say("Fixed: the redirect loop. The tests pass.")], 0),
        ([say("The path is **Unverified** until CI runs.")], 0),
        ([say("Why *not* cache it? The key changes per run.")], 0),
        ([say("Why __init__.py loads twice: it is imported under two names.")], 0),
        ([say("The pipeline has three stages:\n- Build\n- Verification\n- Deploy")], 0),
        # Mentioned, not worn.
        ([say("The old template used `**What changed**` as a label.")], 0),
        ([say("It printed \"Why:\" before the line.")], 0),
        ([say("Here is the template:\n```md\n**What changed**\nWhy:\n```\nIt is gone.")], 0),
        ([say("Here is the template:\n~~~md\n**What changed**\nWhy:\n~~~\nIt is gone.")], 0),
        # A tilde fence is not closed by backticks.
        ([say("~~~\nexample\n```\n**What changed**\n~~~")], 0),
        ([say("**What changed**", final=False)], 0),
    ],
    "voice/heading-first": [
        ([say("# Summary\n\nThe hook fires first.")], 1),
        ([say("\n\n  ## Result\n\ntext")], 1),
        ([say("The hook fires first.\n\n## Detail\n\ntext")], 0),
        ([say("#214 merged.")], 0),
        ([say("# Summary", final=False)], 0),
    ],
    "decisions/no-alternatives": [
        ([say("**Recommend narrow.** The substrate is the defensible thing.")], 1),
        ([say("Recommendation: split the record from the view.")], 1),
        ([say("## Recommended\n\nLand the detector first.")], 1),
        ([say("**Decisions**\n\n1. Narrow the service to its core.")], 1),
        ([say("**Decisions**\n\n1. Narrow it. Against: it reverses the epic order.")], 0),
        # A decision heading mid-sentence is prose, not a block.
        ([say("Fixed. The decisions you asked about are in the doc.")], 0),
        # The alternative is named, however it is spelled.
        ([say("**Recommend narrow.**\n\n**Alt — wait:** keep it manual for now.")], 0),
        ([say("Recommend narrow. The alternative is to keep the current frame.")], 0),
        ([say("**Recommend narrow.**\n\nAgainst: it inverts the epic order.")], 0),
        ([say("Recommendation: split it.\n\nOption B keeps one inbox.")], 0),
        # A recommendation in prose is not a decision block.
        ([say("Fixed. I recommend running the suite before you push.")], 0),
        # The word under discussion, not the word in use.
        ([say("Fixed: dropped the word `recommend` from the draft.")], 0),
        ([say("Fixed the crash. Nothing else changed.")], 0),
        ([say("**Recommend narrow.**", final=False)], 0),
    ],
    "autonomy/confirmed-irreversible": [
        ([bash("HARNESS_CONFIRMED=1 git push --force origin main")], 1),
        ([bash("env HARNESS_CONFIRMED=1 terraform apply")], 1),
        ([bash("# confirmed in chat\nHARNESS_CONFIRMED=1 git reset --hard")], 1),
        ([bash("git push --force origin main")], 0),
        # The marker is a prefix, not a word anywhere in the line: the gate graded the text
        # that began with it, so anything before it went ungraded.
        ([bash("echo HARNESS_CONFIRMED=1")], 0),
        ([bash("git fetch && HARNESS_CONFIRMED=1 git push --force")], 0),
        # Not the marker the hook strips either: the shell assigns the string, quotes and all.
        ([bash('HARNESS_CONFIRMED="1" git push --force')], 0),
    ],
    "autonomy/denied-by-grade": [
        ([tool_result(DENIED, tool_name="Bash", tool_use_id="tu4")], 1),
        ([tool_result("Files changed: 3", tool_name="Bash")], 0),
        # Another hook's output, and another tool's, are not this gate firing.
        ([tool_result(DENIED, tool_name="Agent")], 0),
        ([tool_result("permission denied", tool_name="Bash")], 0),
    ],
    "commits/non-conventional": [
        ([bash("git commit -m 'fixed the thing'")], 1),
        ([bash('git commit -m "fixed the thing"')], 1),
        ([bash("git commit -am 'fixed the thing'")], 1),
        ([bash("git commit -sm 'fixed the thing'")], 1),
        ([bash(CC_BAD_SUBJECT)], 1),
        ([bash("git commit -m 'feat(cli): add a flag'")], 0),
        ([bash('git commit -m "fix: treat a dangling && as unparseable"')], 0),
        ([bash("git commit --message='feat(cli): add a flag'")], 0),
        ([bash(CC_OK)], 0),
        ([bash(CC_NO_TRAILER)], 0),
        ([bash(AMEND_AFTER_HEREDOC)], 0),
        ([bash("git commit -F message.txt")], 0),
        ([bash(CONTINUED)], 0),
        ([bash(MULTILINE_MESSAGE)], 0),
        ([bash(MULTILINE_LONG_FLAG)], 0),
        ([bash(SUB_MESSAGE)], 0),
        ([bash(SUB_HEREDOC_MESSAGE)], 0),
        ([bash("git commit -m ''")], 0),
        ([bash("git status")], 0),
    ],
    "commits/missing-trailer": [
        ([bash("git commit -m 'feat(cli): add a flag'")], 1),
        ([bash(CC_NO_TRAILER)], 1),
        ([bash("git commit -am 'fixed the thing'")], 1),
        ([bash(CC_OK)], 0),
        ([bash(CC_BAD_SUBJECT)], 0),
        ([bash("git commit -m 'feat(cli): add a flag' -m '%s'" % TRAILER)], 0),
        ([bash(AMEND_AFTER_HEREDOC)], 0),
        ([bash("git commit --amend --no-edit")], 0),
        ([bash(CONTINUED)], 0),
        ([bash(MULTILINE_MESSAGE)], 0),
        ([bash(MULTILINE_LONG_FLAG)], 0),
        ([bash(SUB_MESSAGE)], 0),
        ([bash(SUB_HEREDOC_MESSAGE)], 0),
        ([bash("git commit -m ''")], 0),
        ([bash("git status")], 0),
    ],
}


class RegistryTests(unittest.TestCase):
    def test_every_detector_has_a_hitting_case_and_a_clean_one(self):
        for did in rd.DETECTORS:
            with self.subTest(detector=did):
                cases = CASES.get(did)
                self.assertTrue(cases, msg="no corpus case for %s" % did)
                self.assertTrue(any(n > 0 for _, n in cases), msg="no positive case")
                self.assertTrue(any(n == 0 for _, n in cases), msg="no negative case")

    def test_the_corpus_names_no_detector_the_registry_lacks(self):
        self.assertEqual(sorted(set(CASES) - set(rd.DETECTORS)), [])

    def test_every_case_yields_its_expected_hit_count(self):
        # strict=True re-raises: run() swallows a detector's exception at runtime, which
        # would otherwise let a detector that always raises pass every negative case.
        for did, cases in CASES.items():
            for i, (events, expected) in enumerate(cases):
                with self.subTest(detector=did, case=i):
                    hits = rd.run(events, STANCES, strict=True).get(did, [])
                    self.assertEqual(len(hits), expected)

    def test_a_raising_detector_is_swallowed_at_runtime_and_raised_under_strict(self):
        def boom(events, ctx):
            raise RuntimeError("detector is broken")

        broken = rd.Detector("test/boom", "transcript-hygiene", "session", boom)
        rd._REGISTRY.append(broken)
        try:
            self.assertEqual(rd.run([bash("ls")], STANCES), {})
            with self.assertRaises(RuntimeError):
                rd.run([bash("ls")], STANCES, strict=True)
        finally:
            rd._REGISTRY.remove(broken)

    def test_a_hit_is_an_id_a_turn_and_a_tool_use_id_and_never_a_snippet(self):
        events = [bash("cat a.md", turn=3, id="tu9")]
        hits = rd.run(events, STANCES)["transcript-hygiene/whole-file-cat"]
        self.assertEqual(hits, [("transcript-hygiene/whole-file-cat", 3, "tu9")])
        for did, cases in CASES.items():
            for events, _ in cases:
                for hit in rd.run(events, STANCES).get(did, []):
                    self.assertEqual(len(hit), 3)
                    self.assertEqual(hit[0], did)
                    self.assertIsInstance(hit[1], int)
                    self.assertTrue(hit[2] is None or isinstance(hit[2], str))

    def test_detectors_with_no_hits_are_omitted(self):
        self.assertEqual(rd.run([bash("git status")], STANCES), {})

    def test_every_registry_entry_carries_an_id_a_rule_and_a_kind(self):
        kinds = {"bash", "agent-brief", "write", "assistant-final", "session"}
        for did, detector in rd.DETECTORS.items():
            self.assertEqual(detector.id, did)
            self.assertIn(detector.kind, kinds)
            self.assertTrue(detector.rule and "/" not in detector.rule)
            self.assertTrue(callable(detector.fn))

    def test_opt_outs_carry_a_reason(self):
        self.assertTrue(rd.OPT_OUT)
        for stem, reason in rd.OPT_OUT.items():
            self.assertTrue(reason.strip())
            self.assertNotIn("\n", reason)

    def test_every_rule_file_is_measured_or_opted_out(self):
        measured = {d.rule for d in rd.DETECTORS.values()}
        for path in sorted((REPO / "claude" / "rules").glob("*.md")):
            with self.subTest(rule=path.stem):
                self.assertTrue(path.stem in measured or path.stem in rd.OPT_OUT)

    def test_the_deny_signature_is_the_one_the_grade_hook_writes(self):
        """The detector matches a string, not a module, so only a test holds it to the hook."""
        path = REPO / "claude" / "hooks" / "grade-bash.py"
        hook_spec = importlib.util.spec_from_file_location("grade_bash", path)
        grade = importlib.util.module_from_spec(hook_spec)
        hook_spec.loader.exec_module(grade)
        self.assertIn(grade.HOOK, rd.GRADE_SIGNATURE)
        emitted = grade.reason(3, "git push", "--force origin main", "git-push", "execute")
        self.assertIn(rd.GRADE_SIGNATURE, emitted)
        self.assertEqual(len(rd.run([tool_result(emitted, tool_name="Bash")], STANCES)
                                    .get("autonomy/denied-by-grade", [])), 1)

    def test_the_confirm_marker_is_the_one_the_grade_hook_strips(self):
        path = REPO / "claude" / "hooks" / "grade-bash.py"
        hook_spec = importlib.util.spec_from_file_location("grade_bash", path)
        grade = importlib.util.module_from_spec(hook_spec)
        hook_spec.loader.exec_module(grade)
        for command in ("HARNESS_CONFIRMED=1 git push --force", "env HARNESS_CONFIRMED=1 rm -rf /"):
            with self.subTest(command=command):
                self.assertTrue(grade.strip_marker(command)[1])
                self.assertEqual(len(rd.run([bash(command)], STANCES)
                                        .get("autonomy/confirmed-irreversible", [])), 1)
        for command in ('HARNESS_CONFIRMED="1" git push --force', "echo HARNESS_CONFIRMED=1"):
            with self.subTest(command=command):
                self.assertFalse(grade.strip_marker(command)[1])
                self.assertNotIn("autonomy/confirmed-irreversible", rd.run([bash(command)], STANCES))

    def test_the_banned_openers_are_the_ones_the_output_style_names(self):
        style = (REPO / "claude" / "output-styles" / "scannable.md").read_text(encoding="utf-8")
        for opener in rd.BANNED_OPENERS:
            self.assertIn(opener, style)
        self.assertIn(rd.BANNED_CLOSER, style)


class StanceTests(unittest.TestCase):
    COMMIT = [bash("git commit -m 'fixed the thing'")]

    def test_commit_detectors_are_skipped_without_the_stance(self):
        self.assertEqual(rd.run(self.COMMIT), {})
        self.assertEqual(rd.run(self.COMMIT, {"commits": "off"}), {})

    def test_voice_detectors_are_skipped_when_the_dimension_is_off(self):
        banned = [say("I started by reading the hook.")]
        self.assertIn("voice/banned-opener", rd.run(banned, STANCES))
        self.assertEqual(rd.run(banned, {"voice": "off"}), {})
        self.assertIn("voice/banned-opener", rd.run(banned, {"voice": "answer-card"}))

    def test_the_concise_detectors_are_silent_under_every_other_voice(self):
        events = [say("# Report\n\n**What changed**\n\n- the hook")]
        hits = rd.run(events, {"voice": "concise"})
        self.assertIn("voice/scaffold-leak", hits)
        self.assertIn("voice/heading-first", hits)
        for variant in ("scannable", "answer-card", "off"):
            with self.subTest(voice=variant):
                hits = rd.run(events, {"voice": variant})
                self.assertNotIn("voice/scaffold-leak", hits)
                self.assertNotIn("voice/heading-first", hits)

    def test_the_trailer_detector_needs_the_attributed_variant(self):
        plain = rd.run(self.COMMIT, {"commits": "conventional"})
        self.assertIn("commits/non-conventional", plain)
        self.assertNotIn("commits/missing-trailer", plain)
        attributed = rd.run(self.COMMIT, STANCES)
        self.assertIn("commits/missing-trailer", attributed)


class CountTests(unittest.TestCase):
    def test_counts_every_capped_tool_whether_or_not_a_detector_fires(self):
        events = searches(3) + [
            tool_use("Agent", {"prompt": "go, at most 200 words"}, id="a1"),
            tool_use("AskUserQuestion", {"questions": []}, id="q1"),
            bash("ls"),
        ]
        self.assertEqual(rd.counts(events), {"web_search": 3, "agent": 1, "ask_user": 1})

    def test_an_empty_session_counts_zero(self):
        self.assertEqual(rd.counts([]), {"web_search": 0, "agent": 0, "ask_user": 0})

    def test_a_malformed_event_is_skipped_rather_than_fatal(self):
        events = ["not a dict", None, 7] + searches(2)
        self.assertEqual(rd.counts(events)["web_search"], 2)
        self.assertEqual(rd.run(events, STANCES), {})


class MalformedEventTests(unittest.TestCase):
    """A transcript is machine-written but not schema-checked. No shape may raise out of
    run(): a session that loses its record loses the whole report, not one detector."""

    SHAPES = [
        {"kind": "tool_use", "turn": 1, "id": "tu1", "name": "Bash", "input": {"command": 5}},
        {"kind": "tool_use", "turn": 1, "id": "tu1", "name": "Bash", "input": {"command": ["cat", "x"]}},
        {"kind": "tool_use", "turn": 1, "id": "tu1", "name": "Bash", "input": {"command": b"cat x"}},
        {"kind": "tool_use", "turn": 1, "id": "tu1", "name": "Bash", "input": "cat x"},
        {"kind": "tool_use", "turn": 1, "id": "tu1", "name": "Agent", "input": {"prompt": 7}},
        {"kind": "tool_use", "turn": 1, "id": "tu1", "name": "Write", "input": {"content": None}},
        {"kind": "assistant_text", "turn": 1, "final": True, "text": 3, "model": 9},
        {"kind": "tool_result", "turn": 1, "tool_name": "Agent", "text": None},
    ]

    def test_no_shape_raises_and_no_shell_detector_fires(self):
        for shape in self.SHAPES:
            with self.subTest(shape=str(shape.get("input", shape))[:40]):
                for strict in (False, True):
                    hits = rd.run([shape], STANCES, strict=strict)
                    # A brief whose prompt is not text still carries no word cap, which is
                    # a real hit; nothing else may fire on an unreadable event.
                    self.assertLessEqual(set(hits), {"transcript-hygiene/model-wrote-no-cap"})

    def test_the_whole_event_list_may_be_the_wrong_shape(self):
        for events in (None, 7, "not events", {"kind": "tool_use"}):
            with self.subTest(events=str(events)):
                self.assertEqual(rd.run(events, STANCES), {})
                self.assertEqual(rd.run(events, STANCES, strict=True), {})

    def test_a_malformed_event_does_not_hide_a_real_one(self):
        events = self.SHAPES + [bash("cat a.md", id="tu9")]
        self.assertIn("transcript-hygiene/whole-file-cat", rd.run(events, STANCES, strict=True))


class ParseBudgetTests(unittest.TestCase):
    def test_an_oversized_command_is_never_tokenized(self):
        event = bash(HUGE)
        parsed = rd.analyse([event]).bash[0]
        self.assertTrue(parsed.skipped)
        self.assertEqual(parsed.pipelines, [])
        self.assertGreater(len(HUGE), rd.MAX_COMMAND)

    def test_an_oversized_command_yields_no_shell_hits_but_is_still_scanned_for_secrets(self):
        self.assertEqual(rd.run([bash("cat " + "x" * rd.MAX_COMMAND)], STANCES, strict=True), {})
        leaky = bash("echo " + "x" * rd.MAX_COMMAND + " " + FAKE_KEY)
        self.assertIn("secrets/secret-in-write", rd.run([leaky], STANCES, strict=True))

    def test_each_bash_command_is_parsed_once_for_every_detector(self):
        events = [bash("git commit -m 'feat(x): y'"), bash("cat a.md", id="tu2")]
        ctx = rd.analyse(events)
        self.assertEqual(len(ctx.bash), 2)
        self.assertEqual([p.event for p in ctx.bash], events)


class ShellTests(unittest.TestCase):
    def test_a_pipeline_keeps_its_segments_together(self):
        self.assertEqual(rd.pipelines("cat a | head -20"), [[["cat", "a"], ["head", "-20"]]])

    def test_a_break_starts_a_new_pipeline(self):
        self.assertEqual(rd.pipelines("cd x && git status"), [[["cd", "x"]], [["git", "status"]]])

    def test_a_heredoc_body_is_separated_from_the_command(self):
        text, bodies = rd.strip_heredocs("cat > a <<EOF\nline one\nline two\nEOF\nls")
        self.assertEqual(bodies, ["line one\nline two"])
        self.assertNotIn("line one", text)
        self.assertIn("ls", text)

    def test_every_heredoc_operator_spelling_is_recognised(self):
        for command in ("cat <<-EOF\nbody\nEOF", 'cat <<"EOF"\nbody\nEOF',
                        "cat <<'MSG-END'\nbody\nMSG-END", "cat << EOF\nbody\nEOF"):
            with self.subTest(command=command.split("\n")[0]):
                self.assertEqual(rd.strip_heredocs(command)[1], ["body"])

    def test_a_quoted_operator_is_data_and_opens_no_heredoc(self):
        text, bodies = rd.strip_heredocs("grep -n '<<EOF' hooks.py\ncat big.md")
        self.assertEqual(bodies, [])
        self.assertIn("cat big.md", text)

    def test_a_heredoc_inside_a_substitution_is_bound_to_the_flag_that_carries_it(self):
        text, bodies = rd.strip_heredocs(CC_OK)
        self.assertEqual(len(bodies), 1)
        self.assertIn("feat(cli): add a flag", bodies[0])
        self.assertEqual(rd.pipelines(CC_OK)[0][0][:3], ["git", "commit", "-m"])

    def test_a_nested_fence_closes_only_on_a_fence_at_least_as_long(self):
        self.assertEqual(rd._fenced_lines("````\n```\npytest -q\n```\n````"),
                         ["```", "pytest -q", "```"])
        self.assertEqual(rd._fenced_lines("```sh\npytest -q\n```\noutside"), ["pytest -q"])

    def test_a_herestring_is_not_a_heredoc(self):
        text, bodies = rd.strip_heredocs("grep x <<<\"$var\"")
        self.assertEqual(bodies, [])
        self.assertIn("grep", text)

    def test_a_continuation_is_one_command_not_two(self):
        self.assertEqual(rd.pipelines("git commit \\\n  -m 'feat(x): y'"),
                         [["git commit -m".split() + ["feat(x): y"]]])

    def test_a_newline_inside_quotes_stays_in_the_argument(self):
        segment = rd.pipelines(MULTILINE_MESSAGE)[0][0]
        self.assertEqual(segment[:3], ["git", "commit", "-m"])
        self.assertIn("\n\n" + TRAILER, segment[3])

    def test_a_newline_outside_quotes_still_separates_commands(self):
        self.assertEqual(rd.pipelines("git status\ngit diff"), [["git status".split()], ["git diff".split()]])

    def test_normalise_collapses_whitespace_and_drops_a_leading_cd(self):
        self.assertEqual(rd.normalise("cd /repo &&  pytest   -q  tests/"), "pytest -q tests/")
        self.assertEqual(rd.normalise("  ls  -la "), "ls -la")

    def test_operands_skip_flags_and_redirect_targets(self):
        self.assertEqual(rd.operands(["cat", "-n", "a.txt", ">", "b.txt"]), ["a.txt"])

    def test_an_unparseable_command_yields_no_pipelines_and_no_crash(self):
        self.assertEqual(rd.pipelines("echo 'unterminated"), [])
        self.assertEqual(rd.run([bash("echo 'unterminated")], STANCES), {})


if __name__ == "__main__":
    unittest.main()

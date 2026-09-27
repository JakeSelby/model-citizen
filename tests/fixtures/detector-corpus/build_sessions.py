# SPDX-License-Identifier: MIT
"""Writes the synthetic transcripts under `sessions/`, which are committed beside this file.

The cases are hand-authored here rather than typed as JSON so that a reader can see what each
one is for; the JSONL is machine-written because a transcript is. Nothing in here is real
session content: every path is under `/workspace/example-repo`, every name is invented and
every credential-shaped string is a shape, not a value.

Run `python3 tests/fixtures/detector-corpus/build_sessions.py` after changing a case and commit
what it writes; `tests/test_detector_corpus.py` fails when the committed files and this file
disagree.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SESSIONS = os.path.join(HERE, "sessions")
CWD = "/workspace/example-repo"
DAY = "2026-02-0"

# The trailer shape `commits/missing-trailer` looks for. It carries no address: the detector
# reads the line's opening and the lint refuses an address shape anywhere in the tree.
TRAILER = "Co-Authored-By: An Agent"
# The deny reason `grade-bash.py` writes, in full: the grade, the target, the consequence, the
# same fact in plain words, and the signature `autonomy/denied-by-grade` matches.
DENY = ("grade 3, irreversible: %s rewrites remote history \u2014 this cannot be undone "
        "(grade-bash hook, autonomy=execute)")


class Builder(object):
    """One transcript being written: user turns, assistant text, tool uses and results."""

    def __init__(self, name):
        self.name = name
        self.lines = []
        self.clock = 0

    def _stamp(self):
        self.clock += 1
        return "%s1T09:%02d:%02d.000Z" % (DAY, self.clock // 60, self.clock % 60)

    def _write(self, kind, message):
        self.lines.append({"type": kind, "timestamp": self._stamp(),
                           "sessionId": "corpus-" + self.name, "cwd": CWD,
                           "message": message})

    def user(self, text):
        self._write("user", {"role": "user", "content": text})
        return self

    def say(self, text):
        self._write("assistant", {"id": "m%d" % len(self.lines),
                                  "model": "claude-opus-5", "content": [
                                      {"type": "text", "text": text}]})
        return self

    def use(self, use_id, name, data):
        self._write("assistant", {"id": "m%d" % len(self.lines),
                                  "model": "claude-opus-5", "content": [
                                      {"type": "tool_use", "id": use_id, "name": name,
                                       "input": data}]})
        return self

    def uses(self, blocks):
        """Several tool uses in one response, which is one line with many content blocks."""
        self._write("assistant", {"id": "m%d" % len(self.lines), "model": "claude-opus-5",
                                  "content": [{"type": "tool_use", "id": i, "name": n,
                                               "input": d} for i, n, d in blocks]})
        return self

    def bash(self, use_id, command):
        return self.use(use_id, "Bash", {"command": command})

    def result(self, use_id, text):
        self._write("user", {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": use_id, "content": text}]})
        return self

    def save(self):
        path = os.path.join(SESSIONS, self.name + ".jsonl")
        with open(path, "w") as handle:
            for line in self.lines:
                handle.write(json.dumps(line) + "\n")
        return path


def fence(*lines):
    """A subagent return quoting a command, which is where `delegation` reads one from."""
    return "\n".join(["Run this:", "```sh"] + list(lines) + ["```", "That is the gate."])


def briefs():
    """`transcript-hygiene/model-wrote-no-cap`: an Agent brief the model wrote with no cap."""
    b = Builder("briefs-word-cap")
    b.user("find out how the parser handles a trailing comma")
    b.use("tu-b1", "Agent", {"subagent_type": "general-purpose",
                             "prompt": "Read the parser module and describe how it handles "
                                       "a trailing comma in a list literal."})
    b.use("tu-n1", "Agent", {"subagent_type": "gatherer",
                             "prompt": "Read the parser module and describe how it handles "
                                       "a trailing comma."})
    b.user("now the release notes")
    b.use("tu-b2", "Agent", {"subagent_type": "general-purpose",
                             "prompt": "Summarise the open questions in the release note. "
                                       "Keep it short."})
    b.use("tu-n2", "Agent", {"subagent_type": "general-purpose",
                             "prompt": "Summarise the open questions in the release note, "
                                       "in at most 400 words."})
    b.user("and the migration")
    b.use("tu-b3", "Agent", {"subagent_type": "worker-b",
                             "prompt": "Read the migration and report in at most three "
                                       "paragraphs what it changes."})
    b.use("tu-n3", "Agent", {"subagent_type": "worker-b",
                             "prompt": "Read the migration and report what it changes. "
                                       "Cap the return at 300 words."})
    b.user("check the two adapters")
    b.use("tu-b4", "Agent", {"subagent_type": "general-purpose",
                             "prompt": "List every difference between the two adapter "
                                       "directories. Findings only."})
    b.use("tu-n4", "Agent", {"subagent_type": "general-purpose",
                             "prompt": "List every difference between the two adapter "
                                       "directories. 200 words or fewer."})
    b.user("last one: the workflow files")
    b.use("tu-b5", "Agent", {"subagent_type": "researcher",
                             "prompt": "Say which jobs the workflow runs and which of them "
                                       "the ruleset requires."})
    b.use("tu-n5", "Agent", {"subagent_type": "researcher",
                             "prompt": "Say which jobs the workflow runs. This brief "
                                       "carries a 150-word cap."})
    return b


def delegation():
    """`delegation/executed-from-summary`: a command whose first appearance was a return."""
    b = Builder("delegation-from-summary")
    capped = {"subagent_type": "gatherer", "prompt": "Find the gate command."}
    b.user("what does the gate run?")
    b.use("tu-a1", "Agent", capped)
    b.result("tu-a1", fence("python3 -m unittest discover -s tests"))
    b.bash("tu-d1", "python3 -m unittest   discover -s tests")
    b.user("and the linter")
    b.use("tu-a2", "Agent", capped)
    b.result("tu-a2", fence("ruff check ."))
    b.bash("tu-d2", "cd /workspace/example-repo && ruff check .")
    b.user("build it too")
    b.use("tu-a3", "Agent", capped)
    b.result("tu-a3", fence("npm run build"))
    b.bash("tu-d3", "npm run build")
    b.bash("tu-e1", "npm run build --silent")
    b.user("and the installer")
    b.use("tu-a4", "Agent", capped)
    b.result("tu-a4", "````\n```\nbash scripts/install.sh --dry-run\n```\n````")
    b.bash("tu-d4", "bash scripts/install.sh --dry-run")
    b.user("and the check script")
    b.use("tu-a8", "Agent", capped)
    b.result("tu-a8", fence("python3 scripts/check.py --all"))
    b.bash("tu-e6", "python3 scripts/check.py")
    b.user("and the type check")
    b.use("tu-a5", "Agent", capped)
    b.result("tu-a5", fence("mypy src"))
    b.bash("tu-d5", "mypy src")
    b.user("re-run the formatter")
    b.bash("tu-e2", "black --check src")
    b.use("tu-a6", "Agent", capped)
    b.result("tu-a6", fence("black --check src"))
    b.bash("tu-e3", "black --check src")
    b.user("what about the smoke script?")
    b.use("tu-a7", "Agent", capped)
    b.result("tu-a7", "The smoke script is run with python3 scripts/smoke.py, in prose "
                      "and not in a block, so nothing here is a quoted command.")
    b.bash("tu-e4", "python3 scripts/smoke.py")
    b.user("read the coverage note")
    b.use("tu-r1", "Read", {"file_path": "/workspace/example-repo/docs/coverage.md"})
    b.result("tu-r1", "```sh\npytest --cov src\n```")
    b.bash("tu-e5", "pytest --cov src")
    return b


def secrets():
    """`secrets/git-add-secret-file`: staging a path whose name says it holds a credential."""
    b = Builder("secrets-git-add")
    b.user("stage the config")
    b.bash("tu-s1", "git add .env")
    b.bash("tu-p1", "git add .env.example")
    b.user("and the deploy key")
    b.bash("tu-s2", "git add config/id_rsa")
    b.bash("tu-p2", "git add docs/id_rsa-rotation.md")
    b.user("the certificate as well")
    b.bash("tu-s3", "git add certs/server.pem")
    b.bash("tu-p3", "git add certs/README.md")
    b.user("and the service account")
    b.bash("tu-s4", "git add infra/credentials.json")
    b.bash("tu-p4", "git add infra/credentials-format.md")
    b.user("finally the production settings")
    b.bash("tu-s5", "git add .env.production")
    b.bash("tu-p5", "git add .env.sample")
    b.user("and the CI key pair")
    b.bash("tu-s6", "git add keys/id_ed25519")
    b.bash("tu-p6", "git add keys/id_ed25519.pub")
    return b


def search(name, total, fetches=0, per_line=None):
    """`research/search-over-cap`: the search that takes a session past the per-session cap.

    A positive needs more than two hundred searches in one session and the detector fires at
    most once per session, so these four are the whole shape of the case: one dense session
    over the cap, one spread over several turns, one that stops exactly at the cap, and one
    that reaches the count only by counting other web tools. Five identical copies of a
    two-hundred-line fixture would weigh sixty kilobytes and discriminate nothing further.
    """
    b = Builder(name)
    b.user("research the options")
    per_line = per_line or total
    made = 0
    while made < total:
        chunk = min(per_line, total - made)
        b.uses([("q%d" % (made + i + 1), "WebSearch", {"query": "option %d" % (made + i + 1)})
                for i in range(chunk)])
        made += chunk
        if made < total:
            b.user("keep going")
    for i in range(fetches):
        b.use("f%d" % (i + 1), "WebFetch", {"url": "https://example.invalid/%d" % (i + 1)})
    return b


def openers():
    """`voice/banned-opener`: an opener the output style bans, or its closing phrase."""
    b = Builder("voice-openers")
    b.user("what did the parser change?")
    b.say("I started by reading the parser, then the tests around it.")
    b.user("and the adapter?")
    b.say("After investigating the adapter, the difference is one default.")
    b.user("why is the flag off?")
    b.say("Great question. It is off because the renderer has no fallback.")
    b.user("summarise the migration")
    b.say("The migration adds one column and backfills it. Let me know if you want the "
          "column list as well.")
    b.user("and the workflow?")
    b.say("**I started by** listing the jobs, which is where the answer is.")
    b.user("did the server come up?")
    b.say("I started the server and it answered on the first request.")
    b.user("what is the banned closing phrase?")
    b.say("The style bans `Let me know if` as a closing phrase, so a post never ends on it.")
    b.user("what did the reviewer ask?")
    b.say("The reviewer asked one thing, and called it a great question, which it was.")
    b.user("what happened after the investigation?")
    b.say("After investigation the cause was a stale cache, not the parser.")
    b.user("anything else in the note?")
    b.say("I started by reading the note, which is not what you asked for.")
    b.say("The note lists three follow-ups and none of them is blocking.")
    return b


def tables():
    """`voice/second-table`: two table blocks in one final message."""
    one = "| job | state |\n|---|---|\n| test | green |"
    two = "| check | state |\n|---|---|\n| lint | green |"
    b = Builder("voice-tables")
    b.user("how did the run go?")
    b.say("Green.\n\n%s\n\nAnd the checks:\n\n%s" % (one, two))
    b.user("and the second run?")
    b.say("%s\n\n%s\n\n%s" % (one, two, one))
    b.user("the adapters?")
    b.say("Both adapters:\n\n%s\ntext between them\n%s" % (one, two))
    b.user("the two workflows?")
    b.say("%s\n\nthen\n\n| workflow | state |\n| ci | green |" % one)
    b.user("the nightly?")
    b.say("Nightly and weekly:\n\n%s\n\n%s\n\nBoth green." % (two, one))
    b.user("just the one table, please")
    b.say("Here it is:\n\n%s\n\nNothing else to add." % one)
    b.user("a single row?")
    b.say("One row: | test | green |\n\nThat is the whole answer.")
    b.user("two separated rows?")
    b.say("| test | green |\n\nand later\n\n| lint | green |")
    b.user("a long table?")
    b.say("%s\n| lint | green |\n| smoke | green |\n| docs | green |" % one)
    b.user("no table at all?")
    b.say("Every job is green; there is nothing worth a table in three names.")
    return b


def scaffold():
    """`voice/scaffold-leak`: a reply template's section labels, worn."""
    b = Builder("voice-scaffold")
    b.user("what did the parser change?")
    b.say("Done.\n\n**What changed**\n\n- the parser reads the new header")
    b.user("anything for me to do?")
    b.say("One thing.\n\n## What you need to do\n\nRe-run the sync.")
    b.user("is the flag work finished?")
    b.say("Mostly.\n\n**What you need to know** \u2014 the renderer is done, the adapter is not.")
    b.user("and the cache?")
    b.say("- **Still open:** the Linux client has not been checked.")
    b.user("why did the build break?")
    b.say("Why: the lockfile pinned a yanked release.")
    b.user("what did the old template look like?")
    b.say("It used `**What changed**` and `**Still open**` as its labels.")
    b.user("show me the template")
    b.say("Here it is:\n\n```md\n**What changed**\nWhy:\n```\n\nNone of it is used now.")
    b.user("why is the flag off?")
    b.say("Why this matters is simple: the renderer has no fallback.")
    b.user("did the fix land?")
    b.say("Fixed: the redirect loop, and the tests pass.")
    b.user("summarise the review")
    b.say("**What changed**\n\nthe draft, not the final")
    b.say("The review asked for one rename, and it is done.")
    return b


def headings():
    """`voice/heading-first`: a final message that opens on a markdown heading."""
    b = Builder("voice-heading-first")
    b.user("how did the run go?")
    b.say("# Run report\n\nEvery job is green.")
    b.user("and the migration?")
    b.say("## Migration\n\nIt adds one column.")
    b.user("the adapters?")
    b.say("\n\n### Adapters\n\nBoth pinned.")
    b.user("the nightly?")
    b.say("   #### Nightly\n\nGreen twice.")
    b.user("the review?")
    b.say("###### Review\n\nOne rename asked for.")
    b.user("the pull request?")
    b.say("#214 merged, Fix the login redirect loop.")
    b.user("the details?")
    b.say("Every job is green.\n\n## Detail\n\nThe slowest took four minutes.")
    b.user("the tag?")
    b.say("#hashtag-style text is not a heading.")
    b.user("the code?")
    b.say("    # an indented code line\n\nThat is the comment in the script.")
    b.user("the summary?")
    b.say("# Draft heading")
    b.say("The summary is one line: all green.")
    return b


def decisions():
    """`decisions/no-alternatives`: a decision named with no other course beside it."""
    b = Builder("decisions-alternatives")
    b.user("which storage should the ledger use?")
    b.say("Recommendation: keep the ledger in one append-only file.")
    b.user("and the retry policy?")
    b.say("Decisions\n\nRetry three times, then give up and write the failure.")
    b.user("should the job block the merge?")
    b.say("- Recommended: leave it advisory until the corpus has run for a week.")
    b.user("which adapter first?")
    b.say("I recommend the first adapter, because its runtime is already pinned.")
    b.user("and the two adapters?")
    b.say("## Decisions\n\n1. Pin the first adapter. 2. Leave the second on the floor.")
    b.user("what about the floor?")
    b.say("My recommendation: hold the floor at 0.9 and mark anything under it.")
    b.user("and the scheduler, with the other side?")
    b.say("Recommendation: run it hourly.\n\nAlternative: run it on push, which is cheaper "
          "but leaves a gap overnight.")
    b.user("the cache, please")
    b.say("Recommendation: cache the parse.\n\nOption 2 is to cache nothing, whose honest "
          "case is that the parse is already under a millisecond.")
    b.user("is there a word you avoid?")
    b.say("A block opening with `Recommendation:` and naming one course is the shape the "
          "rule forbids; here the word is only being discussed.")
    b.user("what did the note say about retries?")
    b.say("The note says nothing about retries, so there is no decision to take yet.")
    b.user("and the two runtimes, both cases?")
    b.say("Decisions\n\n1. Pin the runtime.\n\nAlternatively, track the latest release and "
          "accept a break, whose honest case is that breaks are found sooner.")
    return b


def confirmed():
    """`autonomy/confirmed-irreversible`: a command re-run behind the confirmation marker."""
    b = Builder("autonomy-confirmed")
    b.user("push it, yes")
    b.bash("tu-c1", "HARNESS_CONFIRMED=1 git push --force-with-lease origin topic")
    b.bash("tu-m1", "git push --force-with-lease origin topic")
    b.user("clear the build directory")
    b.bash("tu-c2", "HARNESS_CONFIRMED=1 rm -rf build/")
    b.bash("tu-m2", "HARNESS_CONFIRMED=\"1\" rm -rf build/")
    b.user("drop the draft release")
    b.bash("tu-c3", "env HARNESS_CONFIRMED=1 gh release delete v0.0.1 --yes")
    b.bash("tu-m3", "echo HARNESS_CONFIRMED=1 is the marker the gate reads")
    b.user("delete the old tag")
    b.bash("tu-c4", "# re-run after the yes\nHARNESS_CONFIRMED=1 git tag -d v0.0.1")
    b.bash("tu-m4", "git tag -d v0.0.2 && HARNESS_CONFIRMED=1 git push origin :v0.0.2")
    b.user("and the merged branch")
    b.bash("tu-c5", "HARNESS_CONFIRMED=1; git branch -D old-topic")
    b.bash("tu-m5", "export HARNESS_CONFIRMED=1")
    return b


def denied():
    """`autonomy/denied-by-grade`: a Bash result carrying the grade hook's own signature."""
    b = Builder("autonomy-denied")
    b.user("force-push the branch")
    b.bash("tu-g1", "git push --force origin main")
    b.result("tu-g1", DENY % "git push --force origin main")
    b.bash("tu-k1", "git push origin topic")
    b.result("tu-k1", "Everything up-to-date")
    b.user("try the other remote")
    b.bash("tu-g2", "git push --force origin release")
    b.result("tu-g2", DENY % "git push --force origin release")
    b.bash("tu-k2", "git push --force origin spike")
    b.result("tu-k2", "denied by a policy hook: this remote is protected")
    b.user("and the tag")
    b.bash("tu-g3", "git push --force origin v0.0.1")
    b.result("tu-g3", DENY % "git push --force origin v0.0.1")
    b.bash("tu-k3", "ls claude/hooks")
    b.result("tu-k3", "grade-bash.py\nusage-log.py")
    b.user("the mirror as well")
    b.bash("tu-g4", "git push --force mirror main")
    b.result("tu-g4", DENY % "git push --force mirror main")
    b.bash("tu-k4", "grep -c hook docs/hooks.md")
    b.result("tu-k4", "the grade-bash hook is described on line 40")
    b.user("last one")
    b.bash("tu-g5", "git push --force origin gh-pages")
    b.result("tu-g5", DENY % "git push --force origin gh-pages")
    b.bash("tu-k5", "git push --dry-run origin main")
    b.result("tu-k5", "To example.invalid:repo.git\n  refs/heads/main -> main")
    b.user("where is the signature asserted?")
    b.bash("tu-k6", "grep -n 'grade-bash hook,' tests/test_grade_bash.py")
    b.result("tu-k6", "486:            \"\u2014 this cannot be undone "
                      "(grade-bash hook, autonomy=execute)\")")
    b.user("and the release branch")
    b.bash("tu-g6", "git push --force origin release/1.0")
    b.result("tu-g6", "PreToolUse:Bash hook error: " + DENY % "git push --force origin release/1.0")
    b.bash("tu-k7", "grep -rn 'grade 3, irreversible' notes/")
    b.result("tu-k7", "notes/incident.md:12: " + DENY % "git push --force origin main")
    return b


def commits():
    """`commits/non-conventional` and `commits/missing-trailer`, on the same commits."""
    heredoc = "git commit -m \"$(cat <<'EOF'\n%s\nEOF\n)\""
    b = Builder("commits-messages")
    b.user("commit it")
    b.bash("tu-x1", "git commit -m \"fixed the thing\"")
    b.bash("tu-y1", "git commit -m \"feat(cli): add a flag\" -m \"%s\"" % TRAILER)
    b.user("and the second one")
    b.bash("tu-x2", "git commit -am \"update\"")
    b.bash("tu-y2", "git commit --amend --no-edit")
    b.user("the parser fix")
    b.bash("tu-x3", heredoc % ("tidied the parser up\n\nCloses #1\n" + TRAILER))
    b.bash("tu-y3", "git commit -m \"$(cat message.txt)\"")
    b.user("the docs change")
    b.bash("tu-x4", heredoc % "docs(readme): say what the job checks\n\nCloses #2")
    b.bash("tu-y4", heredoc % ("fix(parser): accept a trailing comma\n\nCloses #3\n"
                               + TRAILER))
    b.user("and the two spikes")
    b.bash("tu-x5", "git commit -m \"WIP\"")
    b.bash("tu-x6", "git commit --message=\"tidy up\"")
    b.bash("tu-y5", "git commit \\\n  -m \"chore(ci): run the corpus\" \\\n  -m \"%s\""
           % TRAILER)
    return b


def build():
    if not os.path.isdir(SESSIONS):
        os.makedirs(SESSIONS)
    made = [briefs(), delegation(), secrets(),
            search("research-search-over-cap", 201, per_line=201),
            search("research-search-spread", 201, per_line=70),
            search("research-search-at-cap", 200, per_line=200),
            search("research-search-mixed-tools", 150, fetches=60, per_line=150),
            openers(), tables(), scaffold(), headings(), decisions(), confirmed(), denied(), commits()]
    return sorted(session.save() for session in made)


if __name__ == "__main__":
    for written in build():
        print(os.path.basename(written))

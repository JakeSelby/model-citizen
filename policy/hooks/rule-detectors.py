#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""This repository's rule pack, over the vendored `ruleprobe` measurement engine.

The engine — the event schema, the shell decomposition, the registry and `run()` — is
`ruleprobe`, vendored as a wheel in `lib/vendor` beside `tomlkit` and imported below. Six
generic detectors ship with it (`transcript-hygiene/whole-file-cat`,
`transcript-hygiene/unfiltered-find`, `verification/no-verify`, `secrets/secret-in-write`,
`cache-hygiene/compact`, `cache-hygiene/model-switch`); this file holds the eleven that are
about *these* rules, the opt-outs, and the registry `bin/harness` and `usage-log.py` read.

This is not a hook: it has no `main()` and no lifecycle event. `usage-log.py` builds the
event list in the pass it already makes over a transcript and calls `run()`; `bin/harness
lint` imports `DETECTORS` and `OPT_OUT`, to check that every rule file is either measured or
has opted out with a reason, and takes `SECRET_PATTERNS` from here. Both load this file by
path from the checkout, which is why the vendored wheel is always reachable.

Event schema, hits, cost and the known misses of the shell parse: `ruleprobe.events`,
`ruleprobe.registry` and `ruleprobe.shell`. The two facts a reader of this file needs are
that `run()` returns `{detector_id: [Hit, ...]}` with the empty detectors omitted, and that a
hit never carries a snippet — the transcript is the evidence, and `usage.jsonl` holds no
command text (plan decision 3).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                       / "lib" / "vendor" / "ruleprobe-0.1.0-py3-none-any.whl"))
from ruleprobe import Registry, analyse, counts, run as _run  # noqa: E402,F401
from ruleprobe.detectors import common as generic  # noqa: E402
from ruleprobe.events import hit, input_of, text_of  # noqa: E402
from ruleprobe.registry import Detector as _Detector  # noqa: E402
from ruleprobe.shell import (MAX_COMMAND, MARKER_RE, SUB_PLACEHOLDER, git_calls,  # noqa: E402
                             has_redirect, normalise, operands, pipelines, strip_heredocs)

# The lint greps the tree for these shapes and the engine's own `secret-in-write` detector
# reads the same list, so there is one source of truth for both and it is the wheel's.
SECRET_PATTERNS = generic.SECRET_PATTERNS

# The three openers and the closing phrase are read from `claude/output-styles/scannable.md`
# (sections 1 and 9) at build time and frozen here; this module never reads a file at runtime.
BANNED_OPENERS = ("I started by", "After investigating", "Great question")
BANNED_CLOSER = "Let me know if"

# The two shapes `decisions-and-plans` prescribes — a batched "Decisions" block, or a
# recommendation line — and the markers that show another course was named beside them.
# The trigger is a line that *opens* with the word; an inline "I recommend" in running
# prose is not a decision block and does not fire.
DECISION_RE = re.compile(
    r"^[\s*_>#|-]*recommend(?:ation|ed|ing|s)?\b"
    r"|\brecommendation:"
    r"|^[\s*_>#|-]*decisions?\b[\s*_:-]*$",
    re.I | re.M,
)
ALTERNATIVE_RE = re.compile(
    r"\balternativ|^[\s*_>#|-]*alt\b|\bagainst:|\bhonest case\b|^[\s*_>#|-]*option\s",
    re.I | re.M,
)

# `WebSearch` is capped per session by claude/rules/research-and-verification.md.
SEARCH_CAP = 200

# Agents whose own definition carries the word cap, so a brief need not repeat it.
CAPPED_AGENTS = frozenset(("log-compressor", "gatherer", "reviewer", "spec-reviewer", "design-judge"))

CONVENTIONAL_RE = re.compile(r"^(feat|fix|chore|docs|refactor|test|perf|build|ci|style|revert)(\([^)]+\))?!?: \S")
# Every shape a word cap is written in: "at most 400 words", "400 words max", "400 words
# or fewer", "within 400 words", "a 400-word cap", "cap the return at 400 words". A cap
# always carries a number, so "keep it short" is still a miss.
WORD_CAP_RE = re.compile(
    r"(?i)(?:(?:at most|no more than|under|within|max(?:imum)?|≤|<=)\s*\d+\s*[- ]?words?"
    r"|\d+\s*[- ]?words?\s*(?:or (?:fewer|less)|max(?:imum)?|cap)"
    r"|\d+\s*[- ]?word\s+cap"
    r"|cap[^.\n]{0,40}?\d+\s*[- ]?words?"
    r"|word\s+cap\s*(?:of\s+)?\d+)"
)
# A brief that already prices itself: the sentence `brief-guard` writes, or a spend the author
# wrote in their own words ("under 20k output tokens", "at most 30 tool calls"). A spend is three
# things together — a limiting word, a quantity and one of the two units — because any one of
# them alone is ordinary prose: "fix the 3 tool calls in parser.py" counts nothing, and neither
# does "the budget of the project".
_BUDGET_LIMIT = (r"(?:at most|no more than|not more than|fewer than|less than|up to|under|within"
                 r"|about|around|approx(?:\.|imately)?|expected|expect|spend|budget(?:ed)?|cap(?:ped)?"
                 r"|limit(?:ed)?|max(?:imum)?|≤|<=|<|~)")
_BUDGET_UNIT = r"(?:(?:output|completion)[- ]tokens?|tool[- ]?calls?)"
BUDGET_RE = re.compile(
    r"(?i)(?:expected spend:"
    r"|" + _BUDGET_LIMIT + r"(?:\s+[\w,'’-]+){0,3}\s*"
    r"\d[\d,._]*\s*[kKmM]?\s*(?:of\s+)?" + _BUDGET_UNIT + r")"
)
# The autonomy gate's two marks: the prefix the model re-runs a denied command behind, and the
# signature the grade hook writes into the reason it denies with. The marker pattern mirrors
# `grade-bash.py`'s own `MARKER_RE`, so what the gate lets through is what this counts; a
# quoted value (`HARNESS_CONFIRMED="1"`) is not the marker there and is not one here.
_COMMENT_RE = re.compile(r"^(?:\s*(?:#[^\n]*)?\n)+")
_CONFIRMED_RE = re.compile(r"^\s*(?:env\s+)?HARNESS_CONFIRMED=1\s*;?\s*")
GRADE_SIGNATURE = "(grade-bash hook,"
# The whole deny as Claude Code records it, and nothing else: the reason opens the result,
# behind at most the client's own `PreToolUse:Bash hook error:` prefix; the signature closes
# the reason; and what may follow is only what `grade-bash.py` appends to it, a governance
# sentence and then one of its two refusal tails. A result that quotes the signature, such as a
# grep over a test that asserts it, or a denial-shaped line with other output after it, is not a
# denial. The tails are copied, not imported; a test holds them to the hook's own.
GRADE_CLIENT_PREFIX = "PreToolUse:Bash hook error:"
_GRADE_REFUSED = (" Nothing can prompt in this permission mode, so the command was refused rather"
                  " than asked about.")
GRADE_DENY_TAIL = (_GRADE_REFUSED + " Say in chat what it would change and why that is hard to"
                   " undo; if the user says yes, run the same command again with"
                   " HARNESS_CONFIRMED=1 in front of it.")
GRADE_APPROVAL_TAIL = (_GRADE_REFUSED + " Stop, say in chat what it would change and why that is"
                       " hard to undo, and ask the user, if they agree, to reply with exactly"
                       " `approve %s` as the whole message, since any other text in it records"
                       " nothing. After that reply, run exactly the same command again with no"
                       " marker: the approval covers this command once, in this session, for"
                       " thirty minutes.")
GRADE_DENY_RE = re.compile(
    r"\s*(?:" + re.escape(GRADE_CLIENT_PREFIX) + r"\s*)?grade [0-3], [a-z -]+: .*?"
    + re.escape(GRADE_SIGNATURE) + r" autonomy=[\w-]+(?:, unresolved: [^)\n]*)?\)"
    r"(?: Governance: [^\n]*?\.)?"
    r"(?:" + re.escape(GRADE_DENY_TAIL)
    + r"|" + r"\S+".join(re.escape(part) for part in GRADE_APPROVAL_TAIL.split("%s")) + r")?"
    r"\s*\Z", re.S)
# The private halves of an SSH key pair by their default names, whole: a runbook named after
# one, or its public half, is not the key.
SSH_KEY_NAMES = frozenset(("id_rsa", "id_ed25519"))
# A path that says it holds a credential, by basename; see `_is_secret_path`.
ENV_EXAMPLES = frozenset(("example", "sample", "template", "dist"))
KEY_SUFFIXES = (".pem", ".p12", ".pfx")
_FENCE_RE = re.compile(r"^\s{0,3}(`{3,})(.*)$")


class Detector(_Detector):
    """The engine's `Detector` under the two field names this repository's registry uses.

    `kind` is the engine's `event` and `stance` is its `gate`; both are read by the tests and
    by `check_detectors`, and neither is worth a rename across a stored ledger.
    """

    __slots__ = ()

    @property
    def kind(self):
        return self.event

    @property
    def stance(self):
        return self.gate


# --- helpers ------------------------------------------------------------------------


def _messages_of(parsed, args):
    """Every `-m` value of one `git commit`, in order, markers resolved to their body.

    `git commit` with no `-m` — `--amend --no-edit`, `-F file`, `-C <commit>` — carries no
    message here: a heredoc elsewhere in the command belongs to that other command, not
    to the commit. Neither does a commit whose message is a substitution the parse could
    not open (`-m "$(cat msg.txt)"`): an unread message is not a bad one, so the whole
    commit yields no messages rather than an empty subject.
    """
    out = []
    i = 0
    while i < len(args):
        token = args[i]
        value = None
        if token == "--message":
            value = args[i + 1] if i + 1 < len(args) else ""
            i += 2
        elif token.startswith("--message="):
            value = token.split("=", 1)[1]
            i += 1
        elif token.startswith("-") and not token.startswith("--") and "m" in token[1:]:
            # A short cluster: everything after the first `m` is the value, as git reads
            # it, so `-am`, `-sm` and `-mfeat: x` all land here.
            rest = token[1:].split("m", 1)[1]
            if rest:
                value = rest
                i += 1
            else:
                value = args[i + 1] if i + 1 < len(args) else ""
                i += 2
        else:
            i += 1
            continue
        value = text_of(value)
        match = MARKER_RE.match(value)
        if match:
            index = int(match.group(1))
            value = parsed.heredocs[index] if index < len(parsed.heredocs) else ""
        if not value.strip() or SUB_PLACEHOLDER in value or "$(" in value:
            return []
        out.append(value)
    return out


def _commit_messages(parsed):
    """The `-m` values of every `git commit` in one parsed command, commit by commit."""
    return [_messages_of(parsed, args) for _, sub, args in git_calls(parsed, ("commit",))]


def _is_secret_path(path):
    """A path whose own name says it holds a credential."""
    base = path.rstrip("/").rsplit("/", 1)[-1]
    if base == ".env":
        return True
    if base.startswith(".env."):
        return base.split(".", 2)[2].lower() not in ENV_EXAMPLES
    if base.lower().endswith(KEY_SUFFIXES):
        return True
    if base in SSH_KEY_NAMES:
        return True
    return base.split(".", 1)[0] == "credentials"


def _unmarked(text):
    """`text` with quoted and backticked spans blanked, so a phrase under discussion is
    not read as a phrase in use."""
    return re.sub(r"`[^`]*`|'[^'\n]*'|\"[^\"\n]*\"", " ", text)


def _fenced_lines(text):
    """Every line inside a fenced code block, by the CommonMark closing rule: a closing
    fence carries at least as many backticks as the opener and no info string."""
    out, opener = [], None
    for line in (text or "").split("\n"):
        match = _FENCE_RE.match(line)
        if opener is None:
            if match:
                opener = len(match.group(1))
            continue
        if match and len(match.group(1)) >= opener and not match.group(2).strip():
            opener = None
            continue
        out.append(line)
    return out


# --- detectors ---------------------------------------------------------------------


def model_wrote_no_cap(events, ctx):
    """An `Agent` brief the model wrote with no word cap, for an agent whose definition
    carries none.

    This measures the brief as authored, never as delivered. Claude Code writes the `tool_use`
    block with the input the model produced; a `PreToolUse` hook's `updatedInput` is recorded
    separately, on an `attachment` line of type `hook_success`, which the event builder does not
    read. So every brief `brief-guard` capped is still a hit here, and the count is a measure of
    orchestrator compliance — the same way `usage-log.mark_reroutes` measures the spawn hook's
    work from what happened rather than from what the hook announced.

    The id says so since #324: the old `transcript-hygiene/brief-without-cap` read as a count
    of uncapped briefs reaching a subagent, which, with the hook installed, is a number this
    module cannot see and is very nearly zero. `promote?` on this detector means the
    orchestrator does not write bounds and the hook is carrying the rule.
    """
    hits = []
    for event in events:
        if event.get("kind") != "tool_use" or event.get("name") != "Agent":
            continue
        data = input_of(event)
        if text_of(data.get("subagent_type")) in CAPPED_AGENTS:
            continue
        if not WORD_CAP_RE.search(text_of(data.get("prompt"))):
            hits.append(hit(event))
    return hits


def executed_from_summary(events, ctx):
    """A Bash command whose first appearance in the session was inside an `Agent` return.

    A command the session already ran, and a subagent then quoted back, is not a hit: the
    rule is about acting on text that arrived from a subagent, not about repetition.
    """
    origin, hits = {}, []
    for event in events:
        kind = event.get("kind")
        if kind == "tool_result" and event.get("tool_name") == "Agent":
            for line in _fenced_lines(text_of(event.get("text"))):
                text = " ".join(line.split())
                if text:
                    origin.setdefault(text, "agent")
        elif kind == "tool_use" and event.get("name") == "Bash":
            command = text_of(input_of(event).get("command"))
            if not command:
                continue
            text = normalise(command)
            if origin.setdefault(text, "bash") == "agent":
                hits.append(hit(event))
    return hits


def git_add_secret_file(events, ctx):
    """`git add` of a path whose name says it holds a credential."""
    hits = []
    for parsed in ctx.bash:
        for _, _, args in git_calls(parsed, ("add",)):
            if any(_is_secret_path(a) for a in args if not a.startswith("-")):
                hits.append(hit(parsed.event))
                break
    return hits


def search_over_cap(events, ctx):
    """One hit on the search that takes the session past the per-session cap."""
    seen = 0
    for event in events:
        if event.get("kind") == "tool_use" and event.get("name") == "WebSearch":
            seen += 1
            if seen == SEARCH_CAP + 1:
                return [hit(event)]
    return []


def banned_opener(events, ctx):
    hits = []
    for event in ctx.finals:
        text = text_of(event.get("text"))
        opener = re.sub(r"^[\s*#>_\-]+", "", text)
        if opener.startswith(BANNED_OPENERS) or BANNED_CLOSER in _unmarked(text):
            hits.append(hit(event, tool_use_id=False))
    return hits


def second_table(events, ctx):
    """Two or more table blocks in one final message; a block is two or more
    consecutive lines starting with `|`."""
    hits = []
    for event in ctx.finals:
        blocks, run_len = 0, 0
        for line in text_of(event.get("text")).split("\n"):
            if line.lstrip().startswith("|"):
                run_len += 1
                if run_len == 2:
                    blocks += 1
            else:
                run_len = 0
        if blocks >= 2:
            hits.append(hit(event, tool_use_id=False))
    return hits


# The `concise` voice forbids the scaffold other voices used: a reply template's section labels.
# Status words are not in the list, because the stance allows them when reporting a fix. A label
# counts only in label position: closed by a colon (after closing bold or not), wrapped whole in
# bold, or standing as a heading. A bare word at the end of a line is a list item or prose.
_SCAFFOLD_LABELS = (r"(?:What changed|What you need to know|What you need to do|Still open|"
                    r"Verification|Why|The catch|Catch|Alternatives)")
_LIST_MARKER = r"^\s{0,3}(?:(?:[-*+]|\d+[.)])\s+)?"
SCAFFOLD_LABEL_RE = re.compile(
    _LIST_MARKER + _SCAFFOLD_LABELS + r"\s*:"
    r"|" + _LIST_MARKER + r"(\*\*|__)\s*" + _SCAFFOLD_LABELS + r"\s*:?\s*\1"
    r"|^\s{0,3}#{1,6}\s+(?:\*\*|__)?" + _SCAFFOLD_LABELS + r"\s*:?\s*(?:\*\*|__)?\s*:?\s*#*\s*$",
    re.IGNORECASE)
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")


# Backtick and tilde fences, for `_unfenced_lines` only; `_fenced_lines` keeps `_FENCE_RE` so the
# detectors that read fenced commands keep their measured behaviour.
_ANY_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")


def _unfenced_lines(text):
    """Every line outside a fenced code block, backtick or tilde, by the CommonMark closing
    rule: the same fence character, at least as many of it, and no info string."""
    out, opener = [], None
    for line in (text or "").split("\n"):
        match = _ANY_FENCE_RE.match(line)
        if opener is None:
            if match:
                opener = match.group(1)
            else:
                out.append(line)
        elif (match and match.group(1)[0] == opener[0] and len(match.group(1)) >= len(opener)
              and not match.group(2).strip()):
            opener = None
    return out


def scaffold_leak(events, ctx):
    """A final message that wears a reply template's section labels, outside code fences; a
    backticked or quoted mention is not a label. One hit per message."""
    hits = []
    for event in ctx.finals:
        for line in _unfenced_lines(text_of(event.get("text"))):
            line = _unmarked(line)
            if SCAFFOLD_LABEL_RE.search(line):
                hits.append(hit(event, tool_use_id=False))
                break
    return hits


def heading_first(events, ctx):
    """A final message whose first non-blank line is a markdown heading."""
    hits = []
    for event in ctx.finals:
        lines = [l for l in text_of(event.get("text")).split("\n") if l.strip()]
        if lines and HEADING_RE.match(lines[0]):
            hits.append(hit(event, tool_use_id=False))
    return hits


def recommendation_without_alternative(events, ctx):
    """A final message that decides between courses and names only one. The rule asks for
    "the alternatives with their honest case", so a batched `Decisions` block or a
    recommendation line standing alone is the shape it forbids. Markers are read out of
    the raw text rather than the unmarked text, so an alternative named inside a quote
    still counts as named."""
    hits = []
    for event in ctx.finals:
        text = text_of(event.get("text"))
        if DECISION_RE.search(_unmarked(text)) and not ALTERNATIVE_RE.search(text):
            hits.append(hit(event, tool_use_id=False))
    return hits


def non_conventional(events, ctx):
    """A commit subject that is not a Conventional Commit line."""
    hits = []
    for parsed in ctx.bash:
        for messages in _commit_messages(parsed):
            if not messages:
                continue
            subject = messages[0].strip().split("\n")[0].strip()
            if subject and not CONVENTIONAL_RE.match(subject):
                hits.append(hit(parsed.event))
                break
    return hits


def missing_trailer(events, ctx):
    """A commit whose message carries no `Co-Authored-By:` line, in any `-m`."""
    hits = []
    for parsed in ctx.bash:
        for messages in _commit_messages(parsed):
            if not messages:
                continue
            if not re.search(r"(?im)^\s*Co-Authored-By:", "\n".join(messages)):
                hits.append(hit(parsed.event))
                break
    return hits


def confirmed_irreversible(events, ctx):
    """A command re-run behind the marker, which is a grade-3 action the user said yes to.

    `env` may carry the assignment, as the shell allows, and leading blank or comment lines
    are nothing the shell runs; anything else before the marker means the gate saw a different
    command from this one, so a marker buried mid-command confirms nothing and counts nothing.
    """
    return [hit(p.event) for p in ctx.bash
            if _CONFIRMED_RE.match(_COMMENT_RE.sub("", p.command))]


def denied_by_grade(events, ctx):
    """A Bash result that is the grade hook's deny reason: the gate fired and the command
    never ran. The hook signs its own reason, so the string is the evidence — the detector
    never imports it, and reads no other hook's output, nor a quotation of this one's, as a
    denial; see `GRADE_DENY_RE`."""
    hits = []
    for event in events:
        if event.get("kind") != "tool_result" or event.get("tool_name") != "Bash":
            continue
        if GRADE_DENY_RE.match(text_of(event.get("text"))):
            hits.append((event.get("turn", 0), event.get("tool_use_id") or None))
    return hits


# --- the registry -------------------------------------------------------------------

_COMMITS_ON = ("commits", None)  # any variant but `off`
_VOICE_ON = ("voice", None)  # the shape is the stance's; `off` imposes none
_VOICE_CONCISE = ("voice", ("concise",))  # shapes only the `concise` voice forbids
_COMMITS_ATTRIBUTED = ("commits", ("conventional-attributed",))

# A cost variant whose `compaction` switch is `compact-allowed` lifts `cache-hygiene.md`'s "not
# compaction", so a compaction there is the stance working, not a miss. Frozen here because
# this module reads no file at runtime; a test holds it equal to the shipped sidecars.
COMPACTION_ALLOWED = frozenset(("max",))
_GATES = {"cache-hygiene/compact": lambda stances: stances.get("cost") not in COMPACTION_ALLOWED}

# The six the engine ships, re-registered under this file's `Detector` so every entry in the
# registry answers to the same field names. The functions are the wheel's, not a second copy.
_GENERIC = [Detector(d.id, d.rule, d.event, d.fn, _GATES.get(d.id, d.gate))
            for d in generic.DETECTORS]

_REGISTRY = _GENERIC + [
    Detector("transcript-hygiene/model-wrote-no-cap", "transcript-hygiene", "agent-brief",
             model_wrote_no_cap),
    Detector("delegation/executed-from-summary", "delegation", "bash", executed_from_summary),
    Detector("secrets/git-add-secret-file", "secrets", "bash", git_add_secret_file),
    Detector("research/search-over-cap", "research-and-verification", "session", search_over_cap),
    Detector("voice/banned-opener", "voice-and-format", "assistant-final", banned_opener, _VOICE_ON),
    Detector("voice/second-table", "voice-and-format", "assistant-final", second_table, _VOICE_ON),
    Detector("voice/scaffold-leak", "voice-and-format", "assistant-final", scaffold_leak,
             _VOICE_CONCISE),
    Detector("voice/heading-first", "voice-and-format", "assistant-final", heading_first,
             _VOICE_CONCISE),
    Detector("decisions/no-alternatives", "decisions-and-plans", "assistant-final",
             recommendation_without_alternative),
    Detector("autonomy/confirmed-irreversible", "autonomy", "bash", confirmed_irreversible),
    Detector("autonomy/denied-by-grade", "autonomy", "bash", denied_by_grade),
    Detector("commits/non-conventional", "commits", "bash", non_conventional, _COMMITS_ON),
    Detector("commits/missing-trailer", "commits", "bash", missing_trailer, _COMMITS_ATTRIBUTED),
]

DETECTORS = dict((d.id, d) for d in _REGISTRY)

# A detector that was renamed, old id → new. A ledger row written under the old id is never
# rewritten; `harness usage --rules` folds this map on every read instead, so one measurement
# stays one line and one series across the rename. Renaming is still a last resort.
RENAMED = {"transcript-hygiene/brief-without-cap": "transcript-hygiene/model-wrote-no-cap"}

# Rules with nothing a transcript can decide. The reason is what the lint prints.
OPT_OUT = {
    "conciseness": "a comment's redundancy is a judgment over the codebase, not a transcript pattern",
    "working-style": "\"verify before you claim\" needs a semantic link between a claim and a command",
}


def run(events, stances=None, strict=False, errors=None):
    """Every detector over one session's events; detectors with no hits are omitted.

    The registry is built per call from `_REGISTRY`, which is a list so a test can add a
    detector to it and take it away again; the cost is once per session, not once per event.
    """
    return _run(events, stances, registry=Registry(_REGISTRY), strict=strict, errors=errors)

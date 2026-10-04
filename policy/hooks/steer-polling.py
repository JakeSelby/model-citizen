#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""PreToolUse on Bash: steer a foreground wait to a background command or Monitor.

A session that sleeps or polls in the foreground holds its turn open while the prompt cache
ages, and a wait past five minutes lets it expire, so the next call pays to write the whole
context again. A command run with `run_in_background` notifies the session when it exits, and
Monitor watches output; neither costs anything while it waits.

Three shapes are a foreground wait:

- a `sleep` of more than WAIT_NOTE seconds;
- an `until`, `while` or `for` loop whose body sleeps, at any length;
- `gh pr checks --watch`, `gh run watch` or `docker wait` with no `timeout` in front of it.

Each one gets a note in the agent's context naming the alternative, and no permission decision,
so the rest of the Bash path still answers. Only a foreground sleep past WAIT_DENY seconds, alone
or summed across one command, is denied. A command run with `run_in_background` is never judged,
and a job the shell itself backgrounds with `&` is judged only through a foreground `wait` that
holds the call open for it, as in `sleep 600 & wait $!`. Every note, denial and exempted
background wait is a `steer-polling` row in the decision log; a command with no wait in it
writes nothing.

The words are split the way the shell would, quotes and comments included, so `sleep` inside a
quoted string or a commit message is not a command. Text the splitter cannot read is left alone.

Silent, and never failing the command, when anything here goes wrong.

Test: echo '{"tool_name":"Bash","tool_input":{"command":"sleep 600"}}' | python3 steer-polling.py
"""
import importlib.util
import json
import re
import shlex
import sys
from pathlib import Path

HOOK = "steer-polling"
HOOKS = Path(__file__).resolve().parent
# A pause this short is a rate limit or a settle, not a wait worth a background command.
WAIT_NOTE = 30
# The prompt cache lives five minutes; a foreground sleep past it is refused.
WAIT_DENY = 300
LOOPS = ("until", "while", "for")
# Words that may stand before a command's name without being it.
PREFIXES = {"do", "then", "else", "elif", "!", "{", "time", "exec", "nohup", "command"}
SEPARATORS = {";", "&", "&&", "|", "||", "|&", ";;", "\n", "(", ")"}
# Separators that end a shell job; `&&`, `||` and the pipes join commands into one.
JOB_ENDS = {";", "&", ";;", "\n"}
# Words that open or close a compound command when they stand where a command's name would.
OPENERS = {"until", "while", "for", "select", "if", "case"}
CLOSERS = {"done", "fi", "esac", "}"}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
DURATION = re.compile(r"^(\d+(?:\.\d*)?|\.\d+)([smhd]?)$")
UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
BOUNDED = {"timeout", "gtimeout"}
FD_REDIRECT = re.compile(r"(^|\s)\d+(?=[<>])")

ALTERNATIVE = ("Run the wait with run_in_background and a command that exits when the condition "
               "holds, such as `until <check>; do sleep 10; done`; the session is notified when it "
               "exits and pays nothing while it waits. To follow output as it arrives, use the "
               "Monitor tool.")


def nesting(words):
    """How many groups `words` opens, and whether it closes one, from its command position."""
    k, opened = 0, 0
    while k < len(words) and (words[k] in PREFIXES or ASSIGNMENT.match(words[k])):
        opened += words[k] == "{"
        k += 1
    if k < len(words) and words[k] in OPENERS:
        opened += 1
    return opened, k < len(words) and words[k] in CLOSERS


def segments(command):
    """The command's simple commands as `(words, job)`, or None when it cannot be split.

    `job` numbers the shell job a trailing `&` sends to the background, or is None in the
    foreground. The `&` covers the whole job before it, back to the last `;` or newline at its
    own depth, so `sleep 600 | cat &` and a loop ending `done &` are background throughout.
    """
    # `2>&1` names a descriptor, not an argument: drop the number before the lexer splits it off.
    command = FD_REDIRECT.sub(r"\1", command)
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    out, words, target = [], [], False
    # pending[d] holds the segments of the job open at depth d; a nested segment is in each.
    pending, jobs = [[]], 0

    def flush():
        if not words:
            return
        opened, closes = nesting(words)
        for job in pending:
            job.append(len(out))
        out.append([words, None])
        if closes and len(pending) > 1:
            pending.pop()
        for _ in range(opened):
            pending.append([])

    for token in tokens + ["\n"]:
        if target:
            target = False
        elif ("<" in token or ">" in token) and not token.strip("<>&|"):
            # A redirection operator; its target is the next word, and neither is an argument.
            target = True
        elif token in SEPARATORS:
            flush()
            words = []
            if token == "(":
                pending.append([])
            elif token == ")":
                if len(pending) > 1:
                    pending.pop()
            elif token in JOB_ENDS:
                if token == "&":
                    jobs += 1
                    for index in pending[-1]:
                        if out[index][1] is None:
                            out[index][1] = jobs
                pending[-1] = []
        else:
            words.append(token)
    return [tuple(segment) for segment in out]


def head(words):
    """`(name, arguments)` of one simple command, past assignments and keywords that prefix it."""
    k = 0
    while k < len(words) and (words[k] in PREFIXES or ASSIGNMENT.match(words[k])):
        k += 1
    if k >= len(words):
        return None, []
    return Path(words[k]).name, words[k + 1:]


def seconds(arguments):
    """What `sleep arguments` waits, in seconds, or None when an argument is not a duration."""
    if not arguments:
        return None
    total = 0.0
    for word in arguments:
        if word in ("inf", "infinity"):
            return float("inf")
        match = DURATION.match(word)
        if match is None:
            return None
        total += float(match.group(1)) * UNITS[match.group(2)]
    return total


def watcher(name, arguments):
    """The polling command `name arguments` names, or None when it is not one."""
    if name == "gh" and arguments[:2] == ["run", "watch"]:
        return "gh run watch"
    if name == "gh" and "checks" in arguments[:2] and any(a == "--watch" or a.startswith("--watch=")
                                                           for a in arguments):
        return "gh pr checks --watch"
    if name == "docker" and arguments[:1] == ["wait"]:
        return "docker wait"
    return None


def judge(command):
    """`(answer, findings)` for one foreground command; answer is None when nothing waits."""
    parts = segments(command)
    if not parts:
        return None, []
    findings, slept, loops, reported = [], [0.0], [], [False]
    # Each background job's sleep in seconds and its watchers, until a foreground `wait` takes it.
    background, last = {}, None

    def sleeps(wait, label):
        # A loop that sleeps is a poll at any length, `sleep $DELAY` included.
        if loops and not reported[0]:
            findings.append("a `%s` loop that sleeps" % loops[0])
            reported[0] = True
        if wait is None:
            return
        slept[0] += wait
        if not loops and wait > WAIT_NOTE:
            findings.append(label)

    for words, job in parts:
        name, arguments = head(words)
        if name is None:
            continue
        if job is not None:
            last = job
            held = background.setdefault(job, [0.0, [], []])
            if name == "sleep":
                wait = seconds(arguments)
                held[0] = None if wait is None or held[0] is None else held[0] + wait
                held[1].append("sleep %s" % " ".join(arguments))
            elif name not in BOUNDED and watcher(name, arguments) is not None:
                held[2].append(watcher(name, arguments))
            continue
        if name in LOOPS:
            loops.append(name)
        elif name == "done":
            if loops:
                loops.pop()
            if not loops:
                reported[0] = False
        elif name == "sleep":
            sleeps(seconds(arguments), "`sleep %s`" % " ".join(arguments))
        elif name == "wait":
            # A foreground `wait` holds the call open for the background jobs it waits on.
            taken = [last] if arguments == ["$!"] else list(background)
            held = [background.pop(j) for j in taken if j in background]
            waits = [h for h in held if h[1]]
            if waits:
                known = [h[0] for h in waits if h[0] is not None]
                sleeps(max(known) if len(known) == len(waits) else None,
                       "`wait` on `%s &`" % "; ".join(s for h in waits for s in h[1]))
            for polled in (p for h in held for p in h[2]):
                findings.append("`wait` on `%s &` with no timeout" % polled)
        elif name not in BOUNDED:
            polled = watcher(name, arguments)
            if polled is not None:
                findings.append("`%s` with no timeout" % polled)
    if not findings:
        return None, []
    return ("deny" if slept[0] > WAIT_DENY else "note"), findings


def answer_for(event):
    """The PreToolUse output and the logged answer for one Bash event, or `(None, None)`."""
    inputs = event.get("tool_input") or {}
    command = inputs.get("command")
    if not isinstance(command, str):
        return None, None
    answer, findings = judge(command)
    if answer is None:
        return None, None
    if inputs.get("run_in_background") is True:
        return None, "background"
    what = ", ".join(findings)
    if answer == "deny":
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": "%s: this waits more than %d minutes in the foreground (%s), "
            "past the prompt cache's lifetime. %s" % (HOOK, WAIT_DENY // 60, what, ALTERNATIVE)}}, "deny"
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": "%s: this waits in the foreground (%s), holding the turn while the "
        "prompt cache ages. %s" % (HOOK, what, ALTERNATIVE)}}, "note"


def sibling(name):
    """A module beside this hook, or None; a failed import never touches the command."""
    try:
        spec = importlib.util.spec_from_file_location("harness_" + name.replace("-", "_"),
                                                      str(HOOKS / (name + ".py")))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def main():
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    if not isinstance(event, dict) or event.get("tool_name") != "Bash":
        return
    try:
        result, logged = answer_for(event)
    except Exception:
        return
    if logged is not None:
        log = sibling("decisions")
        if log is not None:
            log.record(HOOK, logged, event["tool_input"]["command"], event)
    if result:
        print(json.dumps(result))


if __name__ == "__main__":
    main()

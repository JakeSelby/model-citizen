#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""PreToolUse hook: grade every Bash command 0-3 and gate the grades the autonomy stance forbids.

Why a hook and not a rule: "never force-push without asking" is a sentence the model can read
and still skip, and the native permission prompt cannot tell `git push` from `git push --force`
or `terraform plan` from `terraform apply`. A hook sees the command before it runs, in every
permission mode, and can put the consequence in front of the user in one line.

Behaviour:
  - Grades the maximum over the simple commands the read-only grammar decomposes the command
    into: 0 read-only (`allow-readonly-bash.command_ok` proves it), 1 local write, 2
    remote-mutating, 3 irreversible. An unknown command grades 1, never 3: a false low grade is
    the missed prompt native gives today, and the corpus grows from each miss.
  - The text is normalised before anything else — backslash continuations joined where bash
    joins them, quoted heredoc bodies and comments dropped — so a `#` comment or a here-document
    cannot hide the verb or break the parse with an unbalanced quote or backtick. A body is then
    graded as the commands it holds where the shell runs them: the substitutions of an unquoted
    body, and the whole of one a shell reads (`_grade_bodies`). Text reaching a shell's standard
    input from a here-string or a pipe is graded the same way when it is known, and a program
    another interpreter reads, or a file written and then run, is graded unknown or as the
    commands it names (`_grade_streams`, `_grade_program`). When the text still does not
    parse, the raw text is scanned for grade-3 verb families rather than graded 1: an
    unparseable command that says `--force` or `rm -rf` is irreversible whatever the rest is.
    A program another interpreter reads that calls nothing able to run a command is text, so a
    quoted verb in an edit script grades nothing (`_inert_program`).
  - Every route that discards uncommitted work grades 3 alike: `git checkout` of paths, `git
    restore` of the working tree, `git reset --hard` and `--merge`, `git read-tree`, `git
    checkout-index -f`, `git rm -f`, `git worktree remove --force`, a blob from `git show` or
    `git cat-file` written over its own path, and `rm`, `truncate`, `cp /dev/null` or any `>`,
    a git command's included, on a tracked file whose working-tree changes `git status` reports
    (`_discards`). An index-only `git reset` or `git restore --staged` keeps that work.
  - Grading runs under a deadline inside the hook's timeout (`grade_within`); past it the raw
    text is scanned, a destructive verb is refused and anything else stays open.
  - `bash -c`, `sh -c`, `eval`, `xargs`, `find -exec` and command-substitution bodies grade 3 when
    their inner text carries a grade-3 verb, else 1; the read-only hook refuses them all anyway.
  - The autonomy stance sets the threshold: `execute` gates grade 3, `confirm-writes` grade 2 and
    up, `ask` grade 1 and up. Below the threshold the hook prints nothing. The stance comes from
    `posture.py`; when that cannot answer the hook grades under the strictest variant it knows
    and says the selection is unresolved, because guessing the permissive one would drop a
    prompt the user asked for.
  - At or above it, prompting modes get `ask` and the non-prompting modes get `deny` with the
    confirmation channel in the reason, because per the Claude Code hooks reference, in
    `bypassPermissions` and in `auto` mode 'The "ask" decision is ignored', while 'A hook that
    returns `permissionDecision: "deny"` blocks the tool even in `bypassPermissions` mode or
    with `--dangerously-skip-permissions`'.
  - In `bypassPermissions`, a command prefixed `HARNESS_CONFIRMED=1` is the confirmation
    channel: the marker is stripped and the command passes silently at any grade. The marker is
    leading and confirms the whole command line, compounds included, because that is the text
    the user was shown and said yes to; a marker in the middle confirms nothing.
  - In `auto` mode the classifier refuses that marker as a bypass of this hook, so the deny
    names an approval code instead (`approvals.py`): the user replies `approve <code>` as the whole
    message, and the same command, with no marker, then passes once in that session within
    thirty minutes. The approval is consumed here, at the point the hook would deny. A Bash command that writes to
    the approvals store grades 3, so the agent cannot record an approval of its own; one that
    only reads it does not (`_store_write`).
  - When `governance.provider` names a decision provider other than `none`, a command the stance
    lets through is put to it as well (`govern`): each simple command is classified as
    `coding.git_push`, `coding.git_commit`, `coding.pr_merge`, `coding.deploy` or
    `coding.shell_exec`, its counterparty is the repository and branch of the directory it runs in
    (a `git -C <dir>` and an earlier `cd <dir>` move it), and the strictest answer across the
    segments stands. The provider only tightens: its `ask` is emitted through the same mode split
    and approval channel as the grader's, and it is never asked about a command the grader
    already gates. A configured provider that raises asks, naming the error, rather than allows,
    and a write to a governance policy file or to the user `config.json`, or a `harness config
    set governance...`, is always asked about, as a level-1 action. Each
    decision is one `governance` row in the decision log. Under `none` nothing is imported and
    the output is exactly the stance's.
  - A Studio CLI spend or apply is asked about whatever the stance and grade, and the marker does
    not confirm it (`cli_confirmation`); the CLI refuses one without the grant filed here.
  - Never raises: a missing sibling grammar and any unexpected error are a silent exit 0, so a
    fault here can only cost a prompt that native would not have shown either. The one thing it
    will not guess at is the stance, above.

Test: echo '{"tool_name":"Bash","tool_input":{"command":"git push --force origin main"}}' | python3 grade-bash.py
"""
import importlib.util
import json
import re
import sys
from pathlib import Path


def _sibling(name, alias):
    """A module beside this hook, or None: a broken sibling leaves the hook silent, never crashing."""
    try:
        spec = importlib.util.spec_from_file_location(alias, Path(__file__).resolve().with_name(name))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


# The grader is a library beside this hook (`bash-grader.py`), which the lifecycle's read-only
# allow loads without this hook. Its names are re-exported here, since callers read them here.
library = _sibling("bash-grader.py", "grade_bash_grader")
if library is not None:
    for _name, _value in vars(library).items():
        if not _name.startswith("__"):
            globals().setdefault(_name, _value)


def stance():
    """The autonomy variant to grade under, and the label the notice carries.

    Resolved by `posture.py`, so one file answers for every hook. This one gates commands, so
    it fails closed: a resolver that cannot be loaded or cannot answer means the strictest
    variant the hook knows, not the permissive default, and the notice says the selection is
    unresolved so the user can see why a familiar command suddenly asks."""
    module = _sibling("posture.py", "harness_posture")
    try:
        if module is None:
            raise ImportError("posture.py is not beside this hook")
        variant = module.selected("autonomy", DEFAULT_STANCE)
    except Exception:
        return STRICTEST, STRICTEST + ", unresolved: the stance resolver did not answer"
    return variant, variant


def emit(decision, reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}))


def approval_code(mode, session_id, command):
    """The code the user replies with to approve `command`, or None where that channel is closed.

    Only `auto` mode has it: a prompting mode asks natively, and `bypassPermissions` keeps the
    marker. `command` is the raw text the agent sent, so the code names exactly that command."""
    if mode != "auto" or approvals is None or approvals.store_path(session_id) is None:
        return None
    return approvals.code_for(session_id, command)


def approved(mode, session_id, command):
    """Consume a live approval of `command` in this session; True when one was used."""
    code = approval_code(mode, session_id, command)
    return code is not None and approvals.consume(session_id, code)


def deny_tail(mode, session_id, command):
    code = approval_code(mode, session_id, command)
    return APPROVAL_TAIL % code if code else DENY_TAIL


# The Studio's paid and applying CLI commands: a person confirms each one (`cli_confirmation`).
CLI_PROGRAMS = ("citizen", "harness")
SPEND_GROUPS = ("replay", "eval", "native", "draft-test")
APPLY_ACTIONS = ("apply", "rollback", "recover")
PYTHON_RE = re.compile(r"python(\d+(\.\d+)*)?\Z")
# Wrappers looked through to the program they run, with the options that take a value.
CLI_WRAPPERS = {"env": ("-u", "--unset"), "command": (), "exec": (), "nohup": (), "time": (),
                "nice": ("-n",)}
CLI_SHELLS = ("bash", "sh", "zsh", "dash", "ksh")
SUBSTITUTION_RE = re.compile(r"\$\(|[()`]")
CLI_MENTION_RE = re.compile(r"(?:citizen|harness)\b[\s\S]*\b(?:runs|draft)\b")
CONFIRM_REASON = ("This Studio CLI command spends money or changes the live configuration, so a person"
                  " confirms it each time: no token, revision, flag, environment variable or"
                  " marker stands in for that yes, and the CLI refuses it without one.")
CONFIRM_APPROVAL_TAIL = (" Nothing can prompt in this permission mode, so it was refused. Stop, say in"
                         " chat what it would spend or change, and ask the user, if they agree, to"
                         " reply with exactly `approve %s` as the whole message. Then run exactly the"
                         " same command again, with no marker: the approval covers it once.")
CONFIRM_DENY_TAIL = (" Nothing here can carry a person's yes to it, so it was refused. Ask the user to"
                     " run it from their own terminal, or to start it from the Studio.")
CONFIRM_UNCLEAR = ("A Studio CLI spend or apply runs inside text this hook cannot name exactly (a"
                   " substitution, a shell's -c text, or a program word it cannot read), so no"
                   " confirmation can cover it. Run the citizen command as a plain command of its"
                   " own.")


def _cli_words(tokens, raw):
    """The words after the program when `tokens` runs the Model Citizen CLI, else None.

    Redirections are dropped, and so is a descriptor number the lexer split from its operator
    when the raw text writes them together (`2>&1`), so the words are the argv the CLI sees."""
    clean = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if ro.PUNCTUATION_RUN.match(token):
            if clean and clean[-1].isdigit() and (clean[-1] + token) in raw:
                clean.pop()
            i += 2
            continue
        clean.append(token)
        i += 1
    while clean and ASSIGN_RE.match(clean[0]):
        clean = clean[1:]
    while clean and clean[0].rpartition("/")[2] in CLI_WRAPPERS:
        valued, clean = CLI_WRAPPERS[clean[0].rpartition("/")[2]], clean[1:]
        while clean and (clean[0].startswith("-") or ASSIGN_RE.match(clean[0])):
            clean = clean[2:] if clean[0] in valued else clean[1:]
    if not clean:
        return None
    program = clean[0].rpartition("/")[2]
    if PYTHON_RE.match(program):
        rest = clean[1:]
        while rest and rest[0].startswith("-") and rest[0] not in ("-c", "-m", "-"):
            rest = rest[1:]
        if not rest or rest[0].startswith("-"):
            return None
        program, clean = rest[0].rpartition("/")[2], rest
    return clean[1:] if program in CLI_PROGRAMS else None


def _option(words, name, shortest):
    """Whether any word is `name` or an argparse abbreviation of it at least `shortest` long."""
    return any(len(w.split("=", 1)[0]) >= shortest and name.startswith(w.split("=", 1)[0])
               for w in words if w.startswith("--"))


def needs_person(words):
    """Whether the CLI words spend money or apply to the live configuration.

    Read loosely, so a doubtful reading asks: the CLI makes the exact call from its parsed
    arguments and refuses a spend or apply that arrives without a grant."""
    plain = [w for w in words if not w.startswith("-")]
    if plain[:1] == ["runs"] and len(plain) > 1:
        if plain[1] in SPEND_GROUPS:
            return "start" in plain[2:]
        if plain[1] == "start":
            return _option(words, "--confirm-spend", 3)
        return False
    if plain[:1] == ["draft"] and len(plain) > 1 and plain[1] in APPLY_ACTIONS:
        return not (plain[1] == "rollback" and _option(words, "--preview", 4))
    return False


def cli_spends(command, depth=0):
    """(the argv of each spend or apply the line runs, whether one hides where none can name it).

    A shell's `-c` text is read as the commands it holds; a spend inside a substitution, or in a
    body this cannot decompose, is unclear: it cannot be named exactly, so it cannot be granted."""
    found, unclear = [], False
    parts = segments(command) if depth <= MAX_DEPTH else None
    if parts is None:
        return found, bool(CLI_MENTION_RE.search(command))
    for tokens in parts:
        words = _cli_words(tokens, command)
        if words is not None and needs_person(words):
            found.append(words)
            continue
        head = tokens[0].rpartition("/")[2] if tokens else ""
        # The program as an argument of something this does not look through (a privilege or
        # timeout wrapper, `xargs`, `script`): the spend runs, but not as words a grant can name.
        if any(t.rpartition("/")[2] in CLI_PROGRAMS and needs_person(tokens[i + 1:])
               for i, t in enumerate(tokens)):
            unclear = True
            continue
        inners = [tokens[tokens.index("-c") + 1]] if head in CLI_SHELLS and "-c" in tokens[1:-1] else []
        if head == "eval":
            inners.append(" ".join(tokens[1:]))
        inners.extend(SUBSTITUTION_RE.sub(" ; ", t) for t in tokens
                      if ("$(" in t or "`" in t) and CLI_MENTION_RE.search(t))
        for text in inners:
            inner, hidden = cli_spends(text, depth + 1)
            unclear = unclear or hidden or bool(inner)
    return found, unclear


def cli_confirmation(raw, mode, session_id, runtime="claude-code"):
    """None when `raw` runs no Studio CLI spend or apply, else (answer, reason, note).

    The answer is `pass` when the user's `approve <code>` for this exact line was consumed here,
    `ask` in a prompting mode, and `deny` elsewhere. A grant for each spend's exact argv is written
    for `pass`, and beside an `ask` on Claude Code, whose native prompt is the person's yes; the
    CLI takes the grant. The confirm marker confirms none of these: in `bypassPermissions` and
    `auto` alike the approval code is the channel, and Codex, which carries no typed approval,
    refuses."""
    if approvals is None:
        return None
    command, _marker = strip_marker(raw)
    found, unclear = cli_spends(command)
    if not found and not unclear:
        return None
    if unclear:
        return "deny", CONFIRM_REASON + " " + CONFIRM_UNCLEAR, "unclear"
    prompting = runtime != "codex" and mode not in DENY_MODES
    if prompting:
        if runtime == "claude-code":
            for words in found:
                approvals.grant(words, "prompt")
        return "ask", CONFIRM_REASON, "asked"
    store = approvals.store_path(session_id) if runtime == "claude-code" else None
    code = approvals.code_for(session_id, raw) if store is not None else None
    if code and approvals.consume(session_id, code):
        if all(approvals.grant(words, "approval") for words in found):
            return "pass", CONFIRM_REASON, "approved"
        return "deny", CONFIRM_REASON + " The approval was used, but the grant could not be written.", "unwritten"
    return "deny", CONFIRM_REASON + (CONFIRM_APPROVAL_TAIL % code if code else CONFIRM_DENY_TAIL), \
        "offered" if code else "refused"


def main():
    if library is None or ro is None:
        return  # no grammar, no grading: fall through to the normal permission flow
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return
    raw = command
    mode, session_id = payload.get("permission_mode"), payload.get("session_id")
    confirming = cli_confirmation(raw, mode, session_id)
    if confirming is not None:
        answer, why, _note = confirming
        if answer != "pass":
            emit(answer, why)
        return
    command, confirmed = strip_marker(command)
    if confirmed:
        return
    variant, label = stance()
    threshold = THRESHOLDS.get(variant, THRESHOLDS[STRICTEST])
    (grade, verb, target, family), _timed = grade_within(command, payload.get("cwd") or "",
                                                         grade=grade_text)
    if grade == 0:
        return
    text = reason(grade, verb, target, family, label)
    decision = "ask"
    if grade < threshold:
        governed = govern(command, payload.get("cwd") or "", grade, variant, payload)
        if governed is None:
            return
        decision, sentence = governed
        text = text + " " + sentence
    if decision == "deny":
        emit("deny", text)
    elif mode in DENY_MODES:
        if approved(mode, session_id, raw):
            return
        emit("deny", text + deny_tail(mode, session_id, raw))
    else:
        emit("ask", text)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # fail open: a bug here costs a prompt, never a block

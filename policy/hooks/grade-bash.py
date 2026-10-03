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
    restore` (`--staged` too), `git reset` but `--soft` and `--keep`, `git read-tree`, `git
    checkout-index -f`, `git rm -f`, `git worktree remove --force`, a blob from `git show` or
    `git cat-file` written over its own path, and `rm`, `truncate`, `cp /dev/null` or an empty
    `>` on a tracked file whose working-tree changes `git status` reports (`_discards`).
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
  - Never raises: a missing sibling grammar and any unexpected error are a silent exit 0, so a
    fault here can only cost a prompt that native would not have shown either. The one thing it
    will not guess at is the stance, above.

Test: echo '{"tool_name":"Bash","tool_input":{"command":"git push --force origin main"}}' | python3 grade-bash.py
"""
import importlib.util
import json
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
    mode, session_id = payload.get("permission_mode"), payload.get("session_id")
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

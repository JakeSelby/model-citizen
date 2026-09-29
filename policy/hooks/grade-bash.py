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
    cannot hide the verb or break the parse with an unbalanced quote or backtick. When the text still does not parse, the
    raw text is scanned for grade-3 verb families rather than graded 1: an unparseable command
    that says `--force` or `rm -rf` is irreversible whatever the rest of it is.
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
    the approvals store grades 3, so the agent cannot record an approval of its own.
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
import os
import re
import sys
from pathlib import Path

HOOK = "grade-bash hook"
MARKER = "HARNESS_CONFIRMED=1"
DEFAULT_STANCE = "execute"
THRESHOLDS = {"execute": 3, "confirm-writes": 2, "ask": 1}
# What to grade under when the stance cannot be resolved: the variant that gates the most.
STRICTEST = min(THRESHOLDS, key=THRESHOLDS.get)
DENY_MODES = {"auto", "bypassPermissions"}
DENY_TAIL = (" Nothing can prompt in this permission mode, so the command was refused rather than"
             " asked about. Say in chat what it would change and why that is hard to undo; if the"
             " user says yes, run the same command again with " + MARKER + " in front of it.")
LABELS = {1: "local write", 2: "remote-mutating", 3: "irreversible"}
# The label above is for a log; this is the same fact for whoever is reading the prompt.
PLAIN = {1: "this changes files on this machine",
         2: "this changes something other people can see",
         3: "this cannot be undone"}
PLACEHOLDER = "__GRADESUB__"
MAX_DEPTH = 4


def _sibling(name, alias):
    """A module beside this hook, or None: a broken sibling leaves the hook silent, never crashing."""
    try:
        spec = importlib.util.spec_from_file_location(alias, Path(__file__).resolve().with_name(name))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


ro = _sibling("allow-readonly-bash.py", "grade_bash_readonly")
approvals = _sibling("approvals.py", "grade_bash_approvals")
APPROVAL_TAIL = (" Nothing can prompt in this permission mode, so the command was refused rather than"
                 " asked about. Stop, say in chat what it would change and why that is hard to undo,"
                 " and ask the user, if they agree, to reply with exactly `approve %s` as the whole"
                 " message, since any other text in it records nothing. After that reply, run exactly"
                 " the same command again with no marker: the approval covers this command once, in"
                 " this session, for thirty minutes.")

# One consequence clause per verb family, plus a generic fallback per grade. The clause is the
# whole preview: the reason line is verb, target, clause.
CLAUSES = {
    "git-history": "rewrites remote history",
    "git-discard": "discards local work with no undo",
    "merge": "merges into the shared branch",
    "delete": "deletes data that cannot be restored",
    "archive": "locks the repository read-only for everyone",
    "rename": ("moves the repository to a new name, and the old URLs redirect only while no"
               " repository takes the old name"),
    "database": "drops data that cannot be restored",
    "migration": "changes a database schema in place",
    "infra": "changes live infrastructure",
    "deploy": "ships to a live environment",
    "cluster": "changes a live cluster",
    "publish": "publishes a release that cannot be withdrawn",
    "system": "changes this machine outside the project",
    "opaque": "runs text this hook cannot inspect",
    "remote": "changes shared state",
    "remote-delete": "deletes a remote resource",
    "approvals": "records an approval only the user may give",
}
GENERIC = {1: "writes to the working tree", 2: "changes shared state", 3: "cannot be undone"}

MARKER_RE = re.compile(r"^\s*(env\s+)?" + MARKER + r"\s*;?\s*")
ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
DASH_C_RE = re.compile(r"^-[A-Za-z]*c$")
# Wrappers that run the command in their remaining arguments, with the option letters that take
# a value of their own, so `nice -n 10 git push --force` is graded as the push.
WRAPPERS = {
    "timeout": ("-k", "--kill-after", "-s", "--signal"),
    "time": (),
    "nice": ("-n", "--adjustment"),
    "nohup": (),
    "stdbuf": ("-i", "-o", "-e", "--input", "--output", "--error"),
    "command": (),
    "exec": (),
    "noglob": (),
    "env": ("-u", "--unset", "--chdir", "-C"),
    "npx": ("--package", "-p"),
    "uvx": ("--from", "-p"),
}
# Runners that execute the rest of the line in a managed environment, like `npx`.
RUNNERS = {("bundle", "exec"), ("poetry", "run"), ("uv", "run"), ("pipx", "run"),
           ("pnpm", "dlx"), ("pnpm", "exec"), ("yarn", "dlx"), ("yarn", "exec"),
           ("npm", "exec"), ("rye", "run"), ("hatch", "run")}
SUDO = {"sudo": ("-u", "-g", "-U", "--user", "--group", "-p", "--prompt"),
        "doas": ("-u", "-C"),
        "su": ("-c", "-s", "--shell", "--command")}
SHELLS = {"bash", "sh", "zsh", "ksh", "dash"}
GIT_VALUE_GLOBALS = ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
                     "--config-env")
XARGS_VALUE_FLAGS = ("-I", "-i", "-n", "-P", "-L", "-s", "-d", "-a", "-E", "-e", "--replace",
                     "--max-args", "--max-procs", "--max-lines", "--delimiter", "--arg-file")
TEMP_ROOTS = ["/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/"]
DOCKER_EXEC_VALUE_FLAGS = ("-e", "--env", "-u", "--user", "-w", "--workdir",
                           "--index", "--env-file")
SSH_VALUE_FLAGS = ("-p", "-i", "-l", "-o", "-F", "-L", "-R", "-D", "-b", "-c", "-E", "-J", "-W")
SCAN_CAP = 16384
TOO_LONG = (3, "command too long to grade", "", "opaque")
SQL_RE = re.compile(
    r"\b(DROP\s+(?:TABLE|DATABASE|SCHEMA|INDEX|VIEW|ROLE|USER)|TRUNCATE(?:\s+TABLE)?|"
    r"DELETE\s+FROM)\s+(?:IF\s+EXISTS\s+)?([`\"\w.]+)", re.I)
ALTER_DROP_RE = re.compile(r"\bALTER\s+TABLE\s+([`\"\w.]+)[\s\S]{0,200}?\bDROP\b", re.I)
MONGO_RE = re.compile(r"\bdb(?:\.\w+)*\.(dropDatabase|drop|deleteMany|remove)\s*\(")
SQL_CLIENTS = {"psql", "mysql", "mariadb", "sqlite3", "mongosh", "mongo", "clickhouse-client"}
CLOUD = {"aws", "gcloud", "az", "doctl", "flyctl"}
CLOUD_G3 = {"delete", "terminate", "destroy", "purge", "remove"}
CLOUD_G2 = {"create", "put", "update", "start", "stop", "attach", "detach", "tag", "modify",
            "associate", "deploy", "set", "cp", "mv", "sync", "upload"}
HTTP_LONG_BODY = ("--data", "--form", "--upload-file", "--post-data", "--post-file",
                  "--body-data", "--body-file", "--json")
HTTP_BODY_LETTERS = "dFT"
HTTP_G2_METHODS = {"POST", "PUT", "PATCH"}
HTTP_G3_METHOD = "DELETE"
GH_G2_NOUNS = {"pr", "issue", "release", "repo"}
GH_G2_VERBS = {"create", "edit", "comment", "close", "reopen", "ready", "review", "merge"}
PUBLISH = {"npm": "publish", "pnpm": "publish", "yarn": "publish", "cargo": "publish",
           "twine": "upload", "gem": "push", "poetry": "publish"}

# Last resort when the text does not parse: a grade-3 verb family anywhere in it is a grade 3.
# Substring needles, not regexes: the text can be large, and every check here must stay linear.
# A chunk is one separator-free run, so the needles of an entry must co-occur in one command.
SCAN = [
    (("force-with-lease",), "git push --force-with-lease", "git-history"),
    (("push", "--force"), "git push --force", "git-history"),
    (("push", "--mirror"), "git push --mirror", "git-history"),
    (("push", "--delete"), "git push --delete", "git-history"),
    (("push", " -f"), "git push -f", "git-history"),
    (("reset", "--hard"), "git reset --hard", "git-discard"),
    (("clean", " -f"), "git clean -f", "git-discard"),
    (("clean", "--force"), "git clean -f", "git-discard"),
    (("filter-branch",), "git filter-branch", "git-history"),
    (("filter-repo",), "git filter-repo", "git-history"),
    (("rm ", "-rf"), "rm -rf", "delete"),
    (("rm ", "-fr"), "rm -rf", "delete"),
    (("rm ", "-r "), "rm -r", "delete"),
    (("rm ", "-f "), "rm -f", "delete"),
    (("drop table",), "DROP TABLE", "database"),
    (("drop database",), "DROP DATABASE", "database"),
    (("drop schema",), "DROP SCHEMA", "database"),
    (("truncate ",), "TRUNCATE", "database"),
    (("delete from",), "DELETE FROM", "database"),
    (("dropdatabase",), "db.dropDatabase()", "database"),
    (("flushall",), "redis-cli FLUSHALL", "database"),
    (("terraform", "destroy"), "terraform destroy", "infra"),
    (("terraform", "apply"), "terraform apply", "infra"),
    (("pulumi", "destroy"), "pulumi destroy", "infra"),
    (("kubectl", "delete"), "kubectl delete", "cluster"),
    (("sudo ",), "sudo", "system"),
    (("doas ",), "doas", "system"),
    (("mkfs",), "mkfs", "system"),
    (("shutdown",), "shutdown", "system"),
    (("reboot",), "reboot", "system"),
    (("dd if=",), "dd", "system"),
]
SCAN_SPLIT = re.compile(r"[\n;&|]+")


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


def strip_marker(cmd):
    """(command without a leading confirm marker, marker seen)."""
    stripped = MARKER_RE.sub("", cmd, count=1)
    return stripped, stripped != cmd


def _split_heredocs(text):
    """Text without the body of every here-document. A body is data: the shell expands a
    variable in an unquoted one but never runs its lines, and a quoted body is not even
    expanded. Bodies go before continuations are joined, so a body line ending in a backslash
    cannot swallow the delimiter."""
    lines = text.split("\n")
    out, bodies = [], []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        match = HEREDOC_RE.search(line)
        i += 1
        if not match:
            continue
        delimiter, body = match.group(2), []
        while i < len(lines) and lines[i].strip() != delimiter:
            body.append(lines[i])
            i += 1
        if i < len(lines):
            out.append(lines[i])
            i += 1
        bodies.append("\n".join(body))
    return "\n".join(out), bodies


def _strip_comments(text):
    """Text without its `#` comments, with quote state carried across newlines, so a `#` inside
    a multi-line quoted string stays and a comment outside one takes the rest of its line."""
    out = []
    sq = dq = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if sq:
            sq = c != "'"
        elif dq:
            if c == "\\":
                out.append(text[i:i + 2])
                i += 2
                continue
            dq = c != '"'
        elif c == "\\":
            out.append(text[i:i + 2])
            i += 2
            continue
        elif c == "'":
            sq = True
        elif c == '"':
            dq = True
        elif c == "#" and (i == 0 or text[i - 1] in " \t\n;|&()"):
            while i < n and text[i] != "\n":
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def normalize(cmd):
    """(shell text, here-document bodies). Bodies come out first, then continuations are
    joined, then comments are dropped, so nothing can hide a verb behind a `#`, inside a body,
    or behind a line continuation. A body is data to the shell; only a client that interprets
    it — a SQL client — is graded on its contents."""
    texts, bodies = _readings(cmd)
    if texts is None:
        return _strip_comments(_outer(cmd)[0]), bodies
    return texts[0], bodies


def _outer(cmd):
    """(the shell text with here-document bodies split out, the bodies)."""
    return _split_heredocs(cmd.replace("\r\n", "\n").replace("\r", "\n"))


def _readings(cmd):
    """([shell text, …], here-document bodies): one reading, or two when `_join_continuations`
    cannot place a backslash-newline with certainty — its own, then every pair removed, as the
    hook read continuations before it modelled quoting. A caller grades the worse reading and
    takes no directory from either.

    The texts are None when the shell text, bodies excluded, is longer than `SCAN_CAP`: the
    caller grades it too long without decomposing it, because the lexer and the substitution
    walk are not linear in its length and a hook that runs past its timeout fails open."""
    text, bodies = _outer(cmd)
    if len(text) > SCAN_CAP:
        return None, bodies
    joined, certain = _join_continuations(text)
    texts = [joined] if certain else [joined, text.replace("\\\n", "")]
    return [_strip_comments(each) for each in texts], bodies


WORD_START = set(" \t\n;&|()<>") | {"$(", "<(", ">("}
CASE_WORD_RE = re.compile(r"(?:^|[\s;&|(])case(?:\s|$)")


def _join_continuations(text):
    """(text without the backslash-newlines bash removes, whether each one was placed for sure).

    Bash removes the pair unquoted, in double quotes, in `${…}`, in `$(…)` and throughout a
    backtick body, whose quotes it does not read. It keeps the pair in single quotes, in ANSI-C
    `$'…'` quotes, where `\\'` is an escaped quote, and in a comment, which a `#` opens at the
    start of an unquoted word and the newline ends. Double quotes do not reach into a `$(…)`:
    its quotes are its own. What bash versions read differently — a comment or `case` inside a
    substitution, quotes inside `${…}` — or a construct left open is not certain, nor is a `$`,
    `<` or `>` whose next character sits behind a continuation."""
    if "\\\n" not in text:
        return text, True
    out, stack, certain, i, n = [], [["top"]], True, 0, len(text)
    while i < n:
        frame = stack[-1]
        state = frame[0]
        char = text[i]
        if state in ("single", "comment"):
            if (state == "single" and char == "'") or (state == "comment" and char == "\n"):
                stack.pop()
            out.append(char)
            i += 1
            continue
        if state == "ansi":
            if char == "\\" and i + 1 < n:
                out.append(text[i:i + 2])
                i += 2
                continue
            if char == "'":
                stack.pop()
            out.append(char)
            i += 1
            continue
        if char == "\\" and i + 1 < n:
            if text[i + 1] != "\n":
                out.append(text[i:i + 2])
            i += 2
            continue
        if state == "backtick":
            if char == "`":
                stack.pop()
            out.append(char)
            i += 1
            continue
        opener = text[i:i + 2]
        if char in "$<>" and text[i + 1:i + 3] == "\\\n":
            certain = False
        if opener == "$(" or (state not in ("double", "brace") and opener in ("<(", ">(")):
            stack.append(["sub", 0, len(out) + 2])
            out.append(opener)
            i += 2
            continue
        if opener == "${":
            stack.append(["brace"])
            out.append(opener)
            i += 2
            continue
        if char == "`":
            stack.append(["backtick"])
            out.append(char)
            i += 1
            continue
        if state == "double":
            if char == '"':
                stack.pop()
            out.append(char)
            i += 1
            continue
        if state == "brace":
            if char == "}":
                stack.pop()
            elif char in "'\"":
                certain = False
            out.append(char)
            i += 1
            continue
        # Unquoted: the top level or the body of a substitution.
        if opener in ("$'", '$"'):
            stack.append(["ansi" if opener == "$'" else "double"])
            out.append(opener)
            i += 2
            continue
        if char in "'\"":
            stack.append(["single" if char == "'" else "double"])
        elif char == "#" and (not out or out[-1] in WORD_START):
            if state == "sub":
                certain = False
            stack.append(["comment"])
        elif state == "sub" and char == "(":
            frame[1] += 1
        elif state == "sub" and char == ")":
            if frame[1]:
                frame[1] -= 1
            else:
                if CASE_WORD_RE.search("".join(out)[frame[2]:]):
                    certain = False
                stack.pop()
        out.append(char)
        i += 1
    if any(frame[0] != "comment" for frame in stack[1:]):
        certain = False
    return "".join(out), certain


def _scan(text):
    """The fallback for text this hook cannot decompose: a grade-3 verb family in any one of
    its separator-free chunks, or grade 1. Linear in the length of the text, and the text it
    reads is capped, because a hook that runs past its timeout fails open."""
    if len(text) > SCAN_CAP:
        return TOO_LONG
    for chunk in SCAN_SPLIT.split(text.lower()):
        for needles, verb, family in SCAN:
            if all(needle in chunk for needle in needles):
                return 3, verb, "", family
    return 1, "", "", "opaque"


def _extract_subs(cmd):
    """(text with every substitution replaced by a placeholder, the inner texts). The text is
    None when a substitution never closes."""
    out, inners = [], []
    i, n = 0, len(cmd)
    sq = dq = False
    while i < n:
        c = cmd[i]
        if sq:
            out.append(c)
            if c == "'":
                sq = False
            i += 1
            continue
        if c == "'" and not dq:
            sq = True
            out.append(c)
            i += 1
            continue
        if c == '"':
            dq = not dq
            out.append(c)
            i += 1
            continue
        if c == "\\":
            out.append(cmd[i:i + 2])
            i += 2
            continue
        if c == "`":
            j = i + 1
            while j < n and cmd[j] != "`":
                j += 2 if cmd[j] == "\\" else 1
            if j >= n:
                return None, inners
            inners.append(cmd[i + 1:j])
            out.append(PLACEHOLDER)
            i = j + 1
            continue
        if cmd.startswith("$(", i):
            end = ro._match_paren(cmd, i + 1)
            if end is None:
                return None, inners
            inner = cmd[i + 2:end]
            if not inner.startswith("("):  # `$(( ))` is arithmetic, not a command
                inners.append(inner)
            out.append(PLACEHOLDER)
            i = end + 1
            continue
        if c in "<>" and not dq and cmd.startswith("(", i + 1):
            end = ro._match_paren(cmd, i + 1)
            if end is None:
                return None, inners
            inners.append(cmd[i + 2:end])
            out.append(PLACEHOLDER)
            i = end + 1
            continue
        out.append(c)
        i += 1
    return "".join(out), inners


def segments(text):
    """The simple commands in `text`, by the read-only grammar's own decomposition: newlines as
    separators, reserved words structural only in command position. None when it does not
    tokenize."""
    text = " ; ".join(text.split("\n"))
    try:
        tokens = ro.tokenize(text)
    except ValueError:
        return None
    out, cur, skipping = [], [], False
    for token in tokens:
        if token in ro.ALWAYS_DELIM:
            if cur:
                out.append(cur)
            cur, skipping = [], False
            continue
        if skipping:
            continue
        if not cur:
            if token in ro.WORD_DROP or token in ro.WORD_COND or token == "!":
                continue
            if token in ro.WORD_HEADER:  # `for x in *` names data, not commands
                skipping = True
                continue
        cur.append(token)
    if cur:
        out.append(cur)
    return out


def _redirects(tokens):
    """(tokens without redirections, the files they write; a descriptor duplication writes none)."""
    clean, targets = [], []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if ro.PUNCTUATION_RUN.match(token):
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            # `>&1` and `2>&-` duplicate or close a descriptor; `>2` and `&>2` write a file named 2
            dup = re.match(r"^\d*>&$", token) and (target.isdigit() or target == "-")
            if ro.WRITE_REDIRECTS.match(token) and not dup:
                targets.append(target)
            i += 2
            continue
        clean.append(token)
        i += 1
    return clean, targets


def operands(args):
    out, done = [], False
    for a in args:
        if a == "--" and not done:
            done = True
        elif done or not a.startswith("-"):
            out.append(a)
    return out


def has(args, *names):
    return any(a == n or a.startswith(n + "=") for a in args for n in names)


def short(args, letters):
    """True when a short-option cluster carries any of `letters`, so `-fu` reads like `-f`."""
    for a in args:
        if a.startswith("-") and not a.startswith("--") and any(c in letters for c in a[1:]):
            return True
    return False


def strip_options(args, value_flags):
    """Arguments past a wrapper's own options, with the value of each option that takes one."""
    i = 0
    while i < len(args):
        a = args[i]
        if not a.startswith("-") or a == "-":
            break
        if a in value_flags:
            i += 2
            continue
        i += 1
    return args[i:]


def _joined(args, limit=2):
    return " ".join(operands(args)[:limit])


def _rm_flagged(tokens):
    """True when `tokens` is an `rm` carrying a recursive or force flag."""
    return bool(tokens) and tokens[0].rpartition("/")[2] == "rm" and (
        short(tokens[1:], "rRf") or has(tokens[1:], "--recursive", "--force"))


def _inner(text, cwd, depth):
    """A body this hook cannot model as a command: grade 3 when it carries a grade-3 verb."""
    grade, verb, target, family = grade_text(text, cwd, depth + 1)
    if grade == 3:
        return 3, verb, target, family
    return 1, None, None, None


def _inner_tokens(tokens, cwd, depth):
    if not tokens:
        return 1, None, None, None
    if depth >= MAX_DEPTH:
        return _scan(" ".join(tokens))
    grade, verb, target, family = grade_tokens(tokens, cwd, depth + 1)
    if grade == 3:
        return 3, verb, target, family
    return 1, None, None, None


def _git(args, cwd):
    i = 0
    while i < len(args):
        a = args[i]
        if a in GIT_VALUE_GLOBALS and i + 1 < len(args):
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        break
    rest = args[i:]
    if not rest:
        return 1, "git", "", None
    sub, sargs = rest[0], rest[1:]
    ops = operands(sargs)
    dry_run = has(sargs, "--dry-run") or short(sargs, "n")
    if sub == "push":
        if dry_run:
            return 1, "git push --dry-run", " ".join(ops), None
        if has(sargs, "--force-with-lease"):
            return 3, "git push --force-with-lease", " ".join(ops), "git-history"
        if has(sargs, "--force") or has(sargs, "--mirror") or short(sargs, "f"):
            verb = "git push --force" if has(sargs, "--force") else (
                "git push --mirror" if has(sargs, "--mirror") else "git push -f")
            return 3, verb, " ".join(ops), "git-history"
        if has(sargs, "--delete") or short(sargs, "d") or any(o.startswith(":") for o in ops):
            return 3, "git push --delete", " ".join(ops), "git-history"
        if any(o.startswith("+") for o in ops):
            return 3, "git push", " ".join(ops), "git-history"
        return 2, "git push", " ".join(ops), "remote"
    if sub == "reset" and has(sargs, "--hard"):
        return 3, "git reset --hard", _joined(sargs, 1), "git-discard"
    if sub == "clean" and (has(sargs, "--force") or short(sargs, "f")) and not dry_run:
        return 3, "git clean -f", _joined(sargs, 1), "git-discard"
    if sub == "checkout":
        if "--" in sargs:
            return 3, "git checkout --", _joined(sargs, 1), "git-discard"
        if has(sargs, "--force") or short(sargs, "f"):
            return 3, "git checkout -f", _joined(sargs, 1), "git-discard"
        if ops[:1] in (["."], ["./"]):
            return 3, "git checkout", ops[0], "git-discard"
    if sub == "switch" and (has(sargs, "--discard-changes", "--force") or short(sargs, "f")):
        return 3, "git switch --discard-changes", _joined(sargs, 1), "git-discard"
    if sub == "restore" and not (has(sargs, "--staged") or short(sargs, "S")):
        return 3, "git restore", _joined(sargs, 1), "git-discard"
    if sub == "branch" and (has(sargs, "-D", "--delete") or short(sargs, "D")):
        return 3, "git branch -D", _joined(sargs, 1), "git-discard"
    if sub == "stash" and ops and ops[0] in ("drop", "clear"):
        return 3, "git stash " + ops[0], " ".join(ops[1:2]), "git-discard"
    if sub == "reflog" and ops and ops[0] in ("expire", "delete"):
        return 3, "git reflog " + ops[0], " ".join(ops[1:2]), "git-history"
    if sub in ("filter-branch", "filter-repo"):
        return 3, "git " + sub, _joined(sargs, 1), "git-history"
    return 1, "git " + sub, _joined(sargs, 1), None


def _gh(args):
    ops = operands(args)
    noun = ops[0] if ops else ""
    verb = ops[1] if len(ops) > 1 else ""
    if (noun, verb) in (("repo", "delete"), ("release", "delete"), ("gist", "delete")):
        return 3, "gh %s %s" % (noun, verb), " ".join(ops[2:3]), "delete"
    if (noun, verb) == ("repo", "archive"):
        return 3, "gh repo archive", " ".join(ops[2:3]), "archive"
    if (noun, verb) == ("repo", "rename"):
        return 3, "gh repo rename", " ".join(ops[2:3]), "rename"
    if (noun, verb) == ("pr", "merge"):
        return 2, "gh pr merge", " ".join(ops[2:3]), "merge"
    if noun == "api":
        method = ""
        for i, a in enumerate(args):
            if a in ("-X", "--method") and i + 1 < len(args):
                method = args[i + 1].upper()
            elif a.startswith("--method="):
                method = a.split("=", 1)[1].upper()
        path = " ".join([o for o in ops[1:] if o != method][:1])
        if method == HTTP_G3_METHOD:
            return 3, "gh api DELETE", path, "remote-delete"
        if method not in ("", "GET", "HEAD") or has(args, "-f", "-F", "--input", "--field",
                                                    "--raw-field"):
            return 2, "gh api " + (method or "POST"), path, "remote"
        return 1, "gh api", path, None
    if noun in GH_G2_NOUNS and verb in GH_G2_VERBS:
        return 2, "gh %s %s" % (noun, verb), " ".join(ops[2:3]), "remote"
    return 1, "gh " + noun, verb, None


def _http(prog, args):
    method, body = "", False
    for i, a in enumerate(args):
        if a in ("-X", "--request", "--method") and i + 1 < len(args):
            method = args[i + 1].upper()
        elif a.startswith(("--request=", "--method=")):
            method = a.split("=", 1)[1].upper()
        elif a.startswith("--"):
            if any(a.startswith(f) for f in HTTP_LONG_BODY):
                body = True
        elif a.startswith("-") and len(a) > 1:
            cluster = a[1:]
            if cluster.endswith("X") and i + 1 < len(args):
                method = args[i + 1].upper()
            elif "X" in cluster:
                method = cluster.split("X", 1)[1].upper()
            if any(c in HTTP_BODY_LETTERS for c in cluster):
                body = True
    ops = operands(args)
    if prog in ("http", "https", "httpie") and ops:
        verb = ops[0].upper()
        if verb == HTTP_G3_METHOD:
            return 3, "%s DELETE" % prog, " ".join(ops[1:2]), "remote-delete"
        if verb in HTTP_G2_METHODS:
            return 2, "%s %s" % (prog, verb), " ".join(ops[1:2]), "remote"
    url = " ".join([o for o in ops if o != method][:1])
    if method == HTTP_G3_METHOD:
        return 3, "%s DELETE" % prog, url, "remote-delete"
    if method in HTTP_G2_METHODS or body:
        return 2, "%s %s" % (prog, method or "with a request body"), url, "remote"
    return 1, prog, url, None


def _cloud(prog, args):
    ops = operands(args)
    for i, op in enumerate(ops):
        head = op.split("-")[0].lower()
        if op.lower() in CLOUD_G3 or head in CLOUD_G3:
            return 3, "%s %s" % (prog, " ".join(ops[:i + 1])), " ".join(ops[i + 1:i + 2]), "infra"
    if prog == "aws" and ops[:1] == ["s3"]:
        if len(ops) > 1 and ops[1] in ("rm", "rb"):
            return 3, "aws s3 " + ops[1], " ".join(ops[2:3]), "delete"
        if len(ops) > 1 and ops[1] == "sync" and has(args, "--delete"):
            return 3, "aws s3 sync --delete", " ".join(ops[2:4]), "delete"
    for i, op in enumerate(ops):
        head = op.split("-")[0].lower()
        if op.lower() in CLOUD_G2 or head in CLOUD_G2:
            return 2, "%s %s" % (prog, " ".join(ops[:i + 1])), " ".join(ops[i + 1:i + 2]), "remote"
    return 1, prog, " ".join(ops[:2]), None


def _sql(text):
    match = SQL_RE.search(text)
    if match:
        verb = re.sub(r"\s+", " ", match.group(1)).upper()
        return 3, verb, match.group(2).strip("`\""), "database"
    match = ALTER_DROP_RE.search(text)
    if match:
        return 3, "ALTER TABLE DROP", match.group(1).strip("`\""), "database"
    match = MONGO_RE.search(text)
    if match:
        return 3, "db.%s()" % match.group(1), "", "database"
    return None


def _expand(op):
    """The operand with `~`, `$HOME` and `$TMPDIR` expanded and `.`/`..` segments resolved, so
    `/tmp/../etc` is judged as `/etc` and `$HOME` as the home directory."""
    text = op.replace("${HOME}", "$HOME").replace("${TMPDIR}", "$TMPDIR")
    text = text.replace("$HOME", os.path.expanduser("~"))
    text = text.replace("$TMPDIR", (os.environ.get("TMPDIR") or "/tmp").rstrip("/"))
    if text.startswith("~"):
        text = os.path.expanduser(text)
    return os.path.normpath(text) if text else text


def _rm_risky(op, cwd):
    """True when deleting `op` recursively reaches outside the working tree, or takes the whole
    working tree, the repository metadata or a wildcard with it."""
    if "*" in op:
        return True
    path = _expand(op)
    if not path or path == "/":
        return True
    if path == ".git" or path.endswith("/.git"):
        return True
    if path.startswith("/"):
        temp_roots = list(TEMP_ROOTS)
        tmpdir = os.environ.get("TMPDIR")
        if tmpdir:
            temp_roots.append(tmpdir.rstrip("/") + "/")
        if any(path.startswith(root) for root in temp_roots):
            return False  # the system temp directories are outside cwd by design
        root = os.path.normpath(cwd) if cwd else ""
        return not (root and (path == root or path.startswith(root + "/")))
    return path == "." or path == ".." or path.startswith("../")


def _rm(args, cwd):
    if not (short(args, "rRf") or has(args, "--recursive", "--force")):
        return 1, "rm", _joined(args, 1), None
    verb = "rm -rf" if short(args, "rR") or has(args, "--recursive") else "rm -f"
    for op in operands(args):
        if _rm_risky(op, cwd):
            return 3, verb, op, "delete"
    return 1, verb, _joined(args, 1), None


G3_SUBCOMMANDS = {
    "terraform": ({"apply", "destroy"}, "infra"),
    "tofu": ({"apply", "destroy"}, "infra"),
    "pulumi": ({"up", "destroy"}, "infra"),
    "cdk": ({"deploy", "destroy"}, "infra"),
    "sam": ({"deploy"}, "deploy"),
    "serverless": ({"deploy", "remove"}, "deploy"),
    "sls": ({"deploy", "remove"}, "deploy"),
    "fly": ({"deploy"}, "deploy"),
    "railway": ({"up"}, "deploy"),
    "alembic": ({"upgrade", "downgrade"}, "migration"),
    "flyway": ({"migrate", "clean"}, "migration"),
    "goose": ({"up", "down"}, "migration"),
    "helm": ({"uninstall", "delete"}, "cluster"),
}
G2_SUBCOMMANDS = {
    "kubectl": ({"apply", "create", "patch", "scale", "rollout", "label", "annotate"}, "cluster"),
    "helm": ({"install", "upgrade"}, "cluster"),
    "docker": ({"push"}, "publish"),
}
RAILS_G3 = re.compile(r"^db:(migrate|drop|reset|schema:load|rollback)$")


def grade_tokens(tokens, cwd, depth):
    """(grade, verb, target, family) for one simple command."""
    tokens, written = _redirects(tokens)
    wrote = ""
    for target in written:
        if re.match(r"^/dev/(sd|disk|nvme|rdisk)", target):
            return 3, "redirect to", target, "system"
        if target and target != "/dev/null":
            wrote = target
    while tokens and ASSIGN_RE.match(tokens[0]):
        tokens = tokens[1:]
    if not tokens or ro.segment_ok(list(tokens)):
        # A redirect-only segment, as after a subshell in `(ls) > out.txt`, still writes its file.
        return (1, "redirect to", wrote, None) if wrote else (0, None, None, None)
    head = tokens[0]
    prog = head.rpartition("/")[2]
    args = tokens[1:]
    ops = operands(args)
    text = " ".join(tokens)

    if PLACEHOLDER in head or head.startswith("$"):
        return _scan(text)  # the program comes from a substitution or a variable
    if (prog, ops[0] if ops else "") in RUNNERS:
        rest = args[args.index(ops[0]) + 1:]
        while rest and (rest[0].startswith("-") or ASSIGN_RE.match(rest[0])):
            rest = rest[1:]
        if rest:
            return grade_tokens(rest, cwd, depth)
    if prog == "cargo" and ops[:1] == ["run"] and "--" in args:
        rest = args[args.index("--") + 1:]
        if rest:
            return grade_tokens(rest, cwd, depth)
    if prog == "ssh":
        rest = strip_options(args, SSH_VALUE_FLAGS)
        if len(rest) > 1:  # the first operand is the host; the rest runs on it
            return _inner(" ".join(rest[1:]), cwd, depth)
    if prog == "kubectl" and ops[:1] == ["exec"] and "--" in args:
        return _inner_tokens(args[args.index("--") + 1:], cwd, depth)
    if prog in ("docker", "docker-compose") and "exec" in args:
        rest = strip_options(args[args.index("exec") + 1:], DOCKER_EXEC_VALUE_FLAGS)
        if len(rest) > 1:  # the first operand is the container or the service
            return _inner_tokens(rest[1:], cwd, depth)
    if prog in ("fly", "flyctl") and ops[:2] == ["ssh", "console"]:
        for i, a in enumerate(args):
            if a in ("-C", "--command") and i + 1 < len(args):
                return _inner(args[i + 1], cwd, depth)
    if prog in SUDO:
        rest = strip_options(args, SUDO[prog])
        return 3, prog, " ".join(rest[:2]) or _joined(args, 1), "system"
    if prog in SHELLS:
        for i, a in enumerate(args):
            if DASH_C_RE.match(a) and i + 1 < len(args):
                return _inner(args[i + 1], cwd, depth)
    if prog in ("eval",):
        return _inner(" ".join(args), cwd, depth)
    if prog in ("xargs", "parallel"):
        rest = strip_options(args, XARGS_VALUE_FLAGS)
        if _rm_flagged(rest):  # the operands arrive on stdin, so any rm -rf here is grade 3
            return 3, "xargs rm -rf", "", "delete"
        return _inner_tokens(rest, cwd, depth)
    if prog in WRAPPERS:
        rest = strip_options(args, WRAPPERS[prog])
        while rest and ASSIGN_RE.match(rest[0]):
            rest = rest[1:]
        if prog == "timeout" and rest:
            rest = rest[1:]  # the duration
        if rest:
            return grade_tokens(rest, cwd, depth)
        return 1, prog, "", None
    if prog == "git":
        return _git(args, cwd)
    if prog == "gh":
        return _gh(args)
    if prog in ("curl", "wget", "http", "https", "httpie"):
        return _http(prog, args)
    if prog in CLOUD:
        return _cloud(prog, args)
    if prog == "rm":
        return _rm(args, cwd)
    if prog == "find":
        if "-delete" in args:
            return 3, "find -delete", _joined(args, 1), "delete"
        for flag in ("-exec", "-execdir", "-ok", "-okdir"):
            if flag in args:
                inner = args[args.index(flag) + 1:]
                inner = [t for t in inner if t not in (";", "+", "\\;", "{}")]
                if _rm_flagged(inner):
                    return 3, "find " + flag + " rm -rf", _joined(args, 1), "delete"
                return _inner_tokens(inner, cwd, depth)
    if prog in SQL_CLIENTS:
        hit = _sql(text)
        if hit:
            return hit
        return 1, prog, _joined(args, 1), None
    if prog == "redis-cli":
        for op in ops:
            if op.upper() in ("FLUSHALL", "FLUSHDB"):
                return 3, "redis-cli " + op.upper(), "", "database"
    if SQL_RE.match(text) or ALTER_DROP_RE.match(text):  # a heredoc body, on its own segment
        return _sql(text)
    if prog == "prisma" or (prog in ("npm", "pnpm", "yarn") and ops[:1] == ["prisma"]):
        rest = ops[1:] if prog != "prisma" else ops
        joined = " ".join(rest[:2])
        if joined in ("migrate deploy", "migrate reset", "db push"):
            return 3, "prisma " + joined, "", "migration"
    if prog in PUBLISH and ops[:1] == [PUBLISH[prog]]:
        return 2, "%s %s" % (prog, PUBLISH[prog]), " ".join(ops[1:2]), "publish"
    if prog in ("rails", "rake", "bin/rails") or ops[:1] == ["rails"]:
        for op in ops:
            if RAILS_G3.match(op):
                return 3, "rails " + op, "", "migration"
    if prog in ("manage.py", "./manage.py") or "manage.py" in ops:
        for op in ops:
            if op in ("migrate", "flush", "sqlflush", "reset_db"):
                return 3, "manage.py " + op, "", "migration"
    if prog == "dbmate" and ops:
        return 3, "dbmate " + ops[0], "", "migration"
    if prog in ("docker", "docker-compose"):
        compose = ops[:2] == ["compose", "down"] or (prog == "docker-compose" and ops[:1] == ["down"])
        if compose and (has(args, "--volumes") or short(args, "v")):
            return 3, "docker compose down -v", "", "delete"
        if ops[:2] == ["system", "prune"] or ops[:2] == ["volume", "prune"]:
            return 3, "docker " + " ".join(ops[:2]), "", "delete"
        if ops[:1] in (["rm"], ["rmi"]) and short(args, "f"):
            return 3, "docker %s -f" % ops[0], " ".join(ops[1:2]), "delete"
    if prog == "kubectl":
        if ops[:1] == ["delete"]:
            if any(a.startswith("--dry-run") for a in args):
                return 1, "kubectl delete --dry-run", " ".join(ops[1:2]), None
            return 3, "kubectl delete", " ".join(ops[1:2]), "cluster"
    if prog == "vercel":
        if has(args, "--prod"):
            return 3, "vercel --prod", "", "deploy"
        if ops[:1] == ["deploy"] or not ops:
            return 2, "vercel deploy", "", "remote"
    if prog == "netlify" and ops[:1] == ["deploy"]:
        if has(args, "--prod"):
            return 3, "netlify deploy --prod", "", "deploy"
        return 2, "netlify deploy", "", "remote"
    if prog in G3_SUBCOMMANDS:
        verbs, family = G3_SUBCOMMANDS[prog]
        if ops[:1] and ops[0] in verbs:
            target = " ".join(ops[1:2]) or ("." if family == "infra" else "")
            return 3, "%s %s" % (prog, ops[0]), target, family
    if prog in G2_SUBCOMMANDS:
        verbs, family = G2_SUBCOMMANDS[prog]
        if ops[:1] and ops[0] in verbs:
            return 2, "%s %s" % (prog, ops[0]), " ".join(ops[1:2]), family
    if prog in ("chmod", "chown", "chgrp") and (short(args, "R") or has(args, "--recursive")):
        for op in ops:
            if op.rstrip("/") in ("", "~"):
                return 3, "%s -R" % prog, op, "system"
    if prog.startswith("mkfs") or prog in ("dd", "shutdown", "reboot", "halt", "diskutil"):
        return 3, prog, _joined(args, 1), "system"
    if prog == "crontab" and has(args, "-r"):
        return 3, "crontab -r", "", "system"
    if prog == "launchctl" and ops[:1] and ops[0] in ("unload", "bootout"):
        return 3, "launchctl " + ops[0], " ".join(ops[1:2]), "system"
    if prog == "kill" and has(args, "-9") and has(args, "-1"):
        return 3, "kill -9 -1", "", "system"
    if prog == "history" and has(args, "-c"):
        return 3, "history -c", "", "system"
    if prog == "shred":
        return 3, "shred", _joined(args, 1), "delete"
    return 1, prog, _joined(args, 1), None


def grade_text(cmd, cwd="", depth=0):
    """(grade, verb, target, family) for a whole command line: the maximum over its parts.

    A command that is not read-only and names the approvals store grades 3, whatever else it
    does: an approval must come from the user's prompt, never from a write the agent makes."""
    best = _grade_text(cmd, cwd, depth)
    if depth == 0 and 0 < best[0] < 3 and approvals is not None and approvals.mentions_store(cmd):
        return 3, "write to", "the approvals store", "approvals"
    return best


def _grade_text(cmd, cwd, depth):
    if depth == 0 and ro.command_ok(cmd):
        return 0, None, None, None
    if depth >= MAX_DEPTH:
        return _scan(cmd)
    texts, bodies = _readings(cmd)
    if texts is None:
        return TOO_LONG
    if len(texts) > 1:
        # A continuation the lexer cannot place: the worse reading, and never read-only.
        return max([_grade_reading(text, bodies, cwd, depth) for text in texts]
                   + [_scan(cmd), (1, "", "", "opaque")], key=lambda h: h[0])
    return _grade_reading(texts[0], bodies, cwd, depth)


def _grade_reading(text, bodies, cwd, depth):
    stripped, inners = _extract_subs(text)
    best = (0, None, None, None)
    for inner in inners:
        hit = _inner(inner, cwd, depth)
        if hit[0] > best[0]:
            best = hit
    parts = segments(stripped) if stripped is not None else None
    if parts is None:
        return max(best, _scan(text), key=lambda h: h[0])
    if bodies and any(seg and seg[0].rpartition("/")[2] in SQL_CLIENTS for seg in parts):
        for body in bodies:  # the shell does not run a body, but a SQL client interprets it
            hit = _sql(body)
            if hit and hit[0] > best[0]:
                best = hit
    for tokens in parts:
        hit = grade_tokens(tokens, cwd, depth)
        if hit[0] > best[0]:
            best = hit
        if best[0] == 3:
            break
    return best


def reason(grade, verb, target, family, variant):
    clause = CLAUSES.get(family) or GENERIC[grade]
    phrase = " ".join(p for p in (verb, target) if p).strip()
    return "grade %d, %s: %s %s — %s (%s, autonomy=%s)" % (
        grade, LABELS[grade], phrase or "this command", clause, PLAIN[grade], HOOK, variant)


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


# ------------------------------------------------------------------ governance

GOVERNANCE_POINT = "governance"
NO_PROVIDER = "none"
PUSH, COMMIT, MERGE = "coding.git_push", "coding.git_commit", "coding.pr_merge"
DEPLOY, SHELL, FILE_WRITE = "coding.deploy", "coding.shell_exec", "coding.file_write"
# A deploy is the grader's `deploy` family plus the preview deploys and stack deploys it grades
# under another family, so a policy on `coding.deploy` covers every verb the grader knows ships.
DEPLOY_VERBS = ("vercel deploy", "netlify deploy", "cdk deploy")
RANK = {"allow": 0, "ask": 1, "deny": 2}
POLICY_NAME = "governance.json"
POLICY_DIR = ".agent-harness"
# The repository's `.agent-harness/governance.json` and the user's
# `.config/agent-harness/governance.json`, found by name in any line that is not `gh_text_only`.
POLICY_RE = re.compile(r"agent-harness[/\\]+governance\.json")
# The user configuration selects the provider, so a write to it can switch governance off; it is
# guarded like a policy file, and so is the command that sets a `governance` key in it.
CONFIG_NAME = "config.json"
CONFIG_RE = re.compile(r"\.config[/\\]+agent-harness[/\\]+config\.json")
# `citizen` is the CLI's other name, so both spellings are the same command.
CONFIG_SET_RE = re.compile(r"(?:harness|citizen)\b[^;&|\n]*\bconfig\s+set\s+[\"']?governance\b")
# Programs every operand of which may be a path they write, move or remove.
PATH_WRITERS = {"tee", "cp", "mv", "install", "ln", "rm", "unlink", "truncate", "touch", "rsync",
                "shred", "dd"}
IN_PLACE = {"sed", "gsed", "perl", "ruby"}
# The one line that may mention a policy path as data (`gh_text_only`): issue and pull request
# text passed to gh's built-in subcommands, which aliases and extensions cannot shadow.
GH_TEXT_SUBCOMMANDS = {("issue", "create"), ("issue", "comment"), ("issue", "edit"),
                       ("pr", "create"), ("pr", "comment"), ("pr", "edit"), ("pr", "review")}
GH_TEXT_FLAGS = {"--body", "--title", "-b", "-t"}
HEREDOC_DELIMITER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
POLICY_LEVEL = 1
FILE_APPROVAL_TAIL = (" Nothing can prompt in this permission mode, so the edit was refused rather"
                      " than asked about. Stop, say in chat what the edit changes, and ask the user,"
                      " if they agree, to reply with exactly `approve %s` as the whole message."
                      " After that reply, make exactly the same edit again: the approval covers it"
                      " once, in this session, for thirty minutes.")
FILE_DENY_TAIL = (" Nothing can prompt in this permission mode, so the edit was refused. Ask the"
                  " user to make this change to the policy file themselves.")


def _config():
    """The user configuration, found as `posture.py` finds it; `{}` when it cannot be read."""
    home = os.environ.get("HARNESS_HOME") or os.environ.get("HOME") or str(Path.home())
    try:
        data = json.loads((Path(home) / ".config" / "agent-harness" / "config.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def provider_name(config):
    """The provider `governance.provider` names, read as `decision.select_provider` reads it."""
    block = config.get("governance")
    name = block.get("provider") if isinstance(block, dict) else None
    return name if isinstance(name, str) and name.strip() else NO_PROVIDER


def _decision_module():
    """`harness_core.decision`, imported only once a provider is configured."""
    lib = str(Path(__file__).resolve().parents[2] / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    return importlib.import_module("harness_core.decision")


def _resolve(target, cwd):
    """`target` as an absolute path, relative to `cwd`, with `~` and `$HOME` expanded; None when
    `cwd` is unknown (None) and `target` is relative."""
    path = _expand(target)
    if not path:
        return cwd
    if os.path.isabs(path):
        return path
    return None if cwd is None else os.path.normpath(os.path.join(cwd, path))


# A directory change is statically known only when its target is a literal path: nothing the
# shell expands at run time. `~` and `~/…` are the one expansion allowed, being the user's home.
DYNAMIC_CHARS = set("$`*?[{") | {"\\"}
VARIABLE_OPERAND_RE = re.compile(r"^\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))$")
SAFE_ASSIGNMENT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=([A-Za-z0-9_./-]+)$")
SAFE_VARIABLE_WORD_RE = re.compile(
    r'^(?:\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))|'
    r'"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")$')
_UNSAFE_OPERANDS = object()
# A context key: True once the line may have changed HOME, so no `~` is expanded after it.
_HOME_UNKNOWN = object()
# The value of a variable assigned something the resolver cannot read, such as `$(pwd)/x` or
# `-x`: the variable is known to be set, to a value that is not, so it resolves to nothing.
_UNKNOWN_VALUE = object()
# Assigning these names invokes shell semantics or changes later word expansion. Their values
# cannot be treated as inert path strings, and an IFS change makes every later unquoted value
# dependent on runtime splitting. `getopts` owns `OPTIND` and `OPTARG`; the variable it names
# is set by running a command, which already ends static assignment in `_assignment_contexts`.
SHELL_SPECIAL_ASSIGNMENTS = {
    "BASHPID", "BASHOPTS", "DIRSTACK", "EPOCHREALTIME", "EPOCHSECONDS", "EUID",
    "FUNCNAME", "GROUPS", "IFS", "LINENO", "OPTARG", "OPTIND", "PIPESTATUS", "PPID", "RANDOM",
    "SECONDS", "SHELLOPTS", "UID",
}


def _static_dir(target, cwd):
    """The directory a `cd`, `pushd` or `-C` to `target` reaches, or None when it cannot be known
    without running the line: `-`, a variable, a substitution, `~user`, a glob."""
    if (not target or target.startswith("-") or PLACEHOLDER in target
            or any(c in DYNAMIC_CHARS for c in target)
            or (target.startswith("~") and target != "~" and not target.startswith("~/"))):
        return None
    return _resolve(target, cwd)


def _source_segments(text):
    """Semicolon-separated source segments, retaining quote and escape provenance.

    Anything with shell control flow, a pipeline, a background job or a subshell is outside
    the static assignment model. Returning None disables assignment resolution for the line.
    """
    out, current, quote, i = [], [], "", 0
    while i < len(text):
        char = text[i]
        if char == "\\" and quote != "'" and i + 1 < len(text):
            current.append(text[i:i + 2])
            i += 2
            continue
        if char in "'\"":
            quote = "" if quote == char else char if not quote else quote
            current.append(char)
        elif not quote and char in "|&()":
            return None
        elif not quote and char in ";\n":
            if "".join(current).strip():
                out.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        i += 1
    if quote:
        return None
    if "".join(current).strip():
        out.append("".join(current).strip())
    return out


def _source_words(text):
    """Shell words as written, quotes included; None when an operator is present."""
    words, word, quote, i = [], [], "", 0
    while i < len(text):
        char = text[i]
        if char == "\\" and quote != "'" and i + 1 < len(text):
            word.append(text[i:i + 2])
            i += 2
            continue
        if char in "'\"":
            quote = "" if quote == char else char if not quote else quote
            word.append(char)
        elif not quote and char in " \t":
            if word:
                words.append("".join(word))
                word = []
        elif not quote and char in ";|&()<>":
            return None
        else:
            word.append(char)
        i += 1
    if quote:
        return None
    if word:
        words.append("".join(word))
    return words


def _operand_context(values, source):
    """Assignment values plus source operands whose quote semantics make them unsafe."""
    context = dict(values)
    words = _source_words(source)
    unsafe = set()
    if words is None:
        context[_UNSAFE_OPERANDS] = None
        return context
    for index, word in enumerate(words[:-1]):
        try:
            option = ro.tokenize(word)
        except ValueError:
            option = []
        if option != ["-C"]:
            continue
        raw = words[index + 1]
        try:
            cooked = ro.tokenize(raw)
        except ValueError:
            cooked = []
        if len(cooked) != 1:
            continue
        target = cooked[0]
        variable = SAFE_VARIABLE_WORD_RE.match(raw)
        if variable:
            continue
        # bash expands a tilde-prefix only when no character of it, up to the first slash, is
        # quoted: `~"/beta"` stays `~/beta`, relative, where zsh expands it.
        quoted = any(c in raw.split("/", 1)[0] for c in "'\"")
        if "$" in raw or "`" in raw or "\\" in raw or (target.startswith("~") and quoted):
            unsafe.add(target)
    context[_UNSAFE_OPERANDS] = unsafe
    return context


def _mentions_home(tokens):
    """Whether a simple command may assign HOME: any word `HOME`, or an assignment to it, as in
    `HOME=x`, `export HOME=x`, `read HOME` or `unset HOME`."""
    return any(token == "HOME" or token.startswith(("HOME=", "HOME+=")) for token in tokens)


def _assignment_contexts(text, parts, home_unknown=False):
    """Static assignment values visible before each parsed command in a straight sequence.

    Each context also says whether HOME may have changed before its command, from
    `home_unknown` or an earlier command naming HOME, so no later `~` is expanded."""
    homes, home = [], home_unknown
    for tokens in parts:
        homes.append(home)
        home = home or _mentions_home(tokens)
    values, contexts, enabled = {}, [], True
    sources = _source_segments(text)
    if sources is None or len(sources) != len(parts):
        return [{_UNSAFE_OPERANDS: None, _HOME_UNKNOWN: moved} for moved in homes]
    reserved = ro.WORD_DROP | ro.WORD_COND | ro.WORD_HEADER
    for tokens, source, moved in zip(parts, sources, homes):
        context = (_operand_context(values, source)
                   if enabled else {_UNSAFE_OPERANDS: None})
        context[_HOME_UNKNOWN] = moved
        contexts.append(context)
        raw_words = _source_words(source)
        matches = [SAFE_ASSIGNMENT_RE.match(word) for word in (raw_words or [])]
        assignments = bool(matches) and all(matches) and not any(token in reserved
                                                                 for token in tokens)
        if not enabled or not assignments:
            # A command may change variable attributes, shell options, or whether a later
            # assignment succeeds. Once one runs, no later assignment is statically trusted.
            enabled = False
            values.clear()
            continue
        if any(match.group(1) in SHELL_SPECIAL_ASSIGNMENTS for match in matches):
            enabled = False
            values.clear()
            continue
        for match in matches:
            name, value = match.group(1), match.group(2)
            # Never forget an assignment it cannot read: the variable is now set to an unknown
            # value, which neither resolves nor falls back to the hook's own environment.
            values[name] = _UNKNOWN_VALUE if _static_dir(value, "/") is None else value
    return contexts


def _static_operand(target, variables):
    """A literal operand, including an exact reference to an earlier static assignment."""
    variables = variables or {}
    unsafe = variables.get(_UNSAFE_OPERANDS, set())
    if unsafe is None and (VARIABLE_OPERAND_RE.match(target) or target.startswith("~")):
        return None
    if unsafe is not None and target in unsafe:
        return None
    if target.startswith("~") and _home_unknown(variables):
        return None
    match = VARIABLE_OPERAND_RE.match(target)
    if not match:
        return target
    value = variables.get(match.group(1) or match.group(2))
    return None if value is _UNKNOWN_VALUE else value


def _home_unknown(variables):
    """Whether HOME may differ from the hook's own by the time this context's command runs:
    the line named HOME earlier, or a command ran that the static model does not follow."""
    variables = variables or {}
    return bool(variables.get(_HOME_UNKNOWN)) or "HOME" in variables


def _isolating(text):
    """Whether the line has a subshell, a pipeline or a background job, where a `cd` does not
    carry to the commands after it."""
    try:
        for token in ro.tokenize(" ; ".join(text.split("\n"))):
            if token and set(token) <= set("();|&<>"):
                # An operator such as `)` or `|&`, or a quoted run of operator characters: drop
                # the two list operators and the descriptor redirections, and look for what is left.
                rest = token.replace("&&", "").replace("||", "")
                for redirect in (">&", "<&", "&>"):
                    rest = rest.replace(redirect, "")
                if any(c in rest for c in "()|&"):
                    return True
        return False
    except ValueError:
        return True


COMPOUND_OPEN = {"{", "if", "while", "until", "for", "select", "case"}
COMPOUND_CLOSE = {"}", "fi", "done", "esac"}
LIST_ENDS = {"&&", "||", ";", ";;", "&"}


def _unquoted_structure(text):
    """`text` with every quoted or escaped operator character replaced by `_`, so the tokenizer,
    which turns a quoted `|` into a word spelled like the operator, returns only real operators.
    Word boundaries do not move: those characters were inside a word already."""
    out, quote, i = [], "", 0
    while i < len(text):
        c = text[i]
        if c == "\\" and quote != "'" and i + 1 < len(text):
            nxt = text[i + 1]
            out.append(c + ("_" if nxt in ro.OPERATOR_CHARS else nxt))
            i += 2
            continue
        if quote and c == quote:
            quote = ""
        elif not quote and c in "'\"":
            quote = c
        elif quote and c in ro.OPERATOR_CHARS:
            c = "_"
        out.append(c)
        i += 1
    return "".join(out)


def _confined(text):
    """Per simple command of `segments(text)`, whether a `cd` there is confined to it, or None
    when the walk cannot place the line's structure and each `cd` falls back to `_isolating`.

    A pipeline binds tighter than `&&`, `||` and `;`, so every element of a pipeline starts in the
    directory in effect when it begins: only a `cd` inside a pipeline element, a subshell or a
    background job is confined. A `cd` inside a brace group, conditional or loop keeps the
    line-wide rule, because such a construct may itself be a pipeline element."""
    try:
        tokens = ro.tokenize(" ; ".join(_unquoted_structure(text).split("\n")))
    except ValueError:
        return None
    # [(pipeline index, paren depth, compound depth)] per segment; per pipeline, whether it is
    # piped and its AND-OR list; per list, whether `&` backgrounds it. `&` ends and backgrounds
    # the whole list, `cd d && true & git push` included; `;` ends one without confining it.
    places, piped, list_of, backgrounded = [], [False], [0], [False]
    cur, skipping, parens, compounds = [], False, 0, 0
    for token in tokens:
        if token in ro.ALWAYS_DELIM:
            if cur:
                places.append(open_at)
            cur, skipping = [], False
            if token == "(":
                parens += 1
            elif token == ")":
                parens -= 1
                if parens < 0:  # a `case` pattern, which this walk does not place
                    return None
            elif parens == 0 and compounds == 0:
                if token in ("|", "|&"):
                    piped[-1] = True
                elif token in LIST_ENDS:
                    piped.append(False)
                    if token in ("&&", "||"):
                        list_of.append(list_of[-1])
                    else:
                        backgrounded[-1] = token == "&"
                        backgrounded.append(False)
                        list_of.append(len(backgrounded) - 1)
            continue
        if skipping:
            continue
        if not cur:
            if token in COMPOUND_OPEN:
                compounds += 1
            elif token in COMPOUND_CLOSE:
                compounds -= 1
                if compounds < 0:
                    return None
            if token in ro.WORD_DROP or token in ro.WORD_COND or token == "!":
                continue
            if token in ro.WORD_HEADER:
                skipping = True
                continue
            open_at = (len(piped) - 1, parens, compounds)
        cur.append(token)
    if cur:
        places.append(open_at)
    if parens or compounds:
        return None
    return [True if in_parens else None if in_compound
            else piped[index] or backgrounded[list_of[index]]
            for index, in_parens, in_compound in places]


def _user_policy(name=POLICY_NAME):
    home = os.environ.get("HARNESS_HOME") or os.environ.get("HOME") or str(Path.home())
    return os.path.join(home, ".config", "agent-harness", name)


def is_user_config(path):
    """Whether `path` is the harness user configuration, `config.json`, which selects the provider."""
    try:
        return (os.path.realpath(os.path.expanduser(str(path)))
                == os.path.realpath(_user_policy(CONFIG_NAME)))
    except (OSError, ValueError):
        return False


def guarded(path):
    """What `path` is, when a write to it is a level-1 action, or None."""
    if is_policy_file(path):
        return "the governance policy file " + str(path)
    if is_user_config(path):
        return "the harness configuration " + str(path) + ", which selects the decision provider"
    return None


def is_policy_file(path):
    """Whether `path` is a governance policy file: any repository's or the user's."""
    try:
        real = os.path.realpath(os.path.expanduser(str(path)))
        user = os.path.realpath(_user_policy())
    except (OSError, ValueError):
        return False
    if os.path.basename(real) != POLICY_NAME:
        return False
    return real == user or os.path.basename(os.path.dirname(real)) == POLICY_DIR


def _git_dir(args, cwd, variables=None):
    """(the directory a git command runs in, after each `-C <dir>`, its subcommand, and the `-C`
    operand as written when that is what left the directory unknown). The directory is None when
    a `-C` is not a literal path, or `--git-dir` or `--work-tree` points the command at a
    repository its directory does not name."""
    i, cause = 0, None
    while i < len(args):
        a = args[i]
        if a.startswith(("--git-dir=", "--work-tree=")):
            cwd, cause = None, None
        if a in GIT_VALUE_GLOBALS and i + 1 < len(args):
            if a == "-C":
                operand = _static_operand(args[i + 1], variables)
                cwd = _static_dir(operand, cwd) if operand is not None else None
                # A literal relative operand under an unknown directory is not the cause.
                static = operand is not None and _static_dir(operand, "/") is not None
                cause = None if cwd is not None or static else args[i + 1]
            elif a in ("--git-dir", "--work-tree"):
                cwd, cause = None, None
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        break
    return cwd, (args[i] if i < len(args) else ""), cause


def _written(prog, args, targets, cwd):
    """The paths one simple command may write, move or remove: absolute where the directory is
    known, and otherwise the operand as written, so `_policy_hits` can still judge it by name."""
    paths = [t for t in targets if t and t != "/dev/null"]
    if prog in PATH_WRITERS:
        paths.extend(operands(args))
        paths.extend(a.split("=", 1)[1] for a in args if a.startswith("of="))
    if prog in IN_PLACE and (short(args, "i") or has(args, "--in-place")):
        paths.extend(operands(args))
    out = []
    for path in paths:
        head = os.path.dirname(path)
        resolved = None if (PLACEHOLDER in head or "$" in head) else _resolve(path, cwd)
        out.append(resolved or path)
    return out


def _governed(tokens, cwd, depth, variables=None, causes=None):
    """[(action class, grade, directory, paths written)] for one simple command.

    Wrappers, runners, `sudo` and a shell's `-c` text are looked through, as the grader looks
    through them, and the inner command is governed at the higher of the two grades. Each push
    found appends to `causes` the `git -C` operand that left its directory unknown, or None, so
    the list pairs one to one, in order, with the push entries returned."""
    grade, verb, _target, family = grade_tokens(list(tokens), cwd or "", depth)
    body, targets = _redirects(list(tokens))
    while body and ASSIGN_RE.match(body[0]):
        body = body[1:]
    if not body:
        return [(SHELL, grade, cwd, _written("", [], targets, cwd))]
    prog, args = body[0].rpartition("/")[2], body[1:]
    ops = operands(args)
    written = _written(prog, args, targets, cwd)
    inner = None
    if depth < MAX_DEPTH:
        if prog in WRAPPERS:
            if prog == "env" and any(a in ("-C", "--chdir") or a.startswith("--chdir=")
                                     for a in args):
                cwd = None  # `env -C` moves the inner command; no literal is trusted here
            rest = strip_options(args, WRAPPERS[prog])
            while rest and ASSIGN_RE.match(rest[0]):
                rest = rest[1:]
            inner = ("tokens", rest[1:] if prog == "timeout" else rest)
        elif prog in SUDO:
            inner = ("tokens", strip_options(args, SUDO[prog]))
        elif (prog, ops[0] if ops else "") in RUNNERS:
            rest = args[args.index(ops[0]) + 1:]
            while rest and (rest[0].startswith("-") or ASSIGN_RE.match(rest[0])):
                rest = rest[1:]
            inner = ("tokens", rest)
        elif prog in SHELLS:
            for i, a in enumerate(args):
                if DASH_C_RE.match(a) and i + 1 < len(args):
                    inner = ("text", args[i + 1])
                    break
        elif prog == "eval":
            inner = ("text", " ".join(args))
    if inner is not None and inner[1]:
        if inner[0] == "tokens":
            if _mentions_home(tokens):  # `env HOME=x bash -c '…'` hands the shell that HOME
                variables = dict(variables or {})
                variables[_HOME_UNKNOWN] = True
            found = _governed(inner[1], cwd, depth + 1, variables, causes)
        else:
            # An inner shell inherits HOME: from this line, or from an assignment prefixed to
            # the command that runs it, as `HOME=x bash -c '…'` and `env HOME=x sh -c '…'` do.
            variables = variables or {}
            moved = (_home_unknown(variables) or variables.get(_UNSAFE_OPERANDS, set()) is None
                     or _mentions_home(tokens))
            found = governed_text(inner[1], cwd, depth + 1, causes=causes, home_unknown=moved)
        if found:
            return [(c, max(g, grade), d, w + written) for c, g, d, w in found]
    if prog == "git":
        where, sub, cause = _git_dir(args, cwd, variables)
        if sub == "push" and causes is not None:
            causes.append(cause)
        return [({"push": PUSH, "commit": COMMIT}.get(sub, SHELL), grade, where, written)]
    if prog == "gh" and ops[:2] == ["pr", "merge"]:
        return [(MERGE, grade, cwd, written)]
    if family == "deploy" or verb in DEPLOY_VERBS:
        return [(DEPLOY, grade, cwd, written)]
    return [(SHELL, grade, cwd, written)]


def governed_text(cmd, cwd, depth=0, isolated=False, causes=None, home_unknown=False):
    """[(action class, grade, directory, paths written)] for every simple command in `cmd`, in
    execution order, or None when the text does not decompose.

    The directory is the one in effect when the command runs, walking the line as the shell
    would: a substitution is governed with the directory of the segment it sits in, so
    `cd ../other && echo "$(git push)"` pushes from `../other`. A directory is None, which
    `govern` names `repo:unknown/local`, from the first change that cannot be known without
    running the line: a `cd` or `pushd` to anything but a literal path, `popd`, and any `cd` in a
    subshell, a substitution, a pipeline or a background job, where it does not carry over. A
    pipeline after a `cd` starts in that `cd`'s directory, as `_confined` places it.

    A line with a continuation the lexer cannot place is walked in each reading `_readings`
    gives, and every directory is None: which repository it reaches is not known. When neither
    reading decomposes, the line is one entry at an unknown directory, a push at grade 2 when a
    chunk of it names `git` and `push`, as `_scan` reads text it cannot decompose. A line too
    long for `_readings` to read is not walked and is that one entry, at grade 3. `causes` is
    filled as `_governed` describes. With `home_unknown`, HOME may have changed before `cmd`
    runs, so no `~` in it is expanded."""
    if depth >= MAX_DEPTH:
        return None
    texts, _bodies = _readings(cmd)
    found = []
    if texts is None:  # too long to decompose: unplaced, and graded as `_scan` grades it
        texts = [_outer(cmd)[0]]
    elif len(texts) == 1:
        return _walk(texts[0], cwd, depth, isolated, causes, home_unknown)
    else:
        for text in texts:
            found.extend(_walk(text, cwd, depth, isolated, causes, home_unknown) or [])
    if not found:
        pushes = any("git" in chunk and "push" in chunk
                     for text in texts for chunk in SCAN_SPLIT.split(text.lower()))
        if pushes and causes is not None:
            causes.append(None)
        # A push is remote-mutating, grade 2, whether or not the line decomposes.
        found = [(PUSH if pushes else SHELL, max(2 if pushes else 1, _scan(cmd)[0]), None, [])]
    return [(action, grade, None, written) for action, grade, _where, written in found]


def _walk(text, cwd, depth, isolated, causes, home_unknown=False):
    """`governed_text` for one normalized reading."""
    stripped, inners = _extract_subs(text)
    parts = segments(stripped) if stripped is not None else None
    if parts is None:
        return None
    line_wide = isolated or _isolating(stripped)
    confined = None if isolated else _confined(stripped)
    if confined is None or len(confined) != len(parts):
        confined = [None] * len(parts)
    queue = list(inners)
    found = []
    assignment_contexts = _assignment_contexts(stripped, parts, home_unknown)

    def substitutions(count, where, variables):
        # A substitution runs in a subshell of this one, with its HOME.
        moved = (_home_unknown(variables)
                 or (variables or {}).get(_UNSAFE_OPERANDS, set()) is None)
        for _ in range(min(count, len(queue))):
            inner = queue.pop(0)
            found.extend(governed_text(inner, where, depth + 1, isolated=True, causes=causes,
                                       home_unknown=moved)
                         or [(SHELL, _scan(inner)[0], where, [])])

    here = cwd
    for tokens, alone, variables in zip(parts, confined, assignment_contexts):
        substitutions(sum(t.count(PLACEHOLDER) for t in tokens), here, variables)
        body, _targets = _redirects(list(tokens))
        while body and ASSIGN_RE.match(body[0]):
            body = body[1:]
        head = body[0].rpartition("/")[2] if body else ""
        if head in ("cd", "pushd", "popd"):
            # A directory change that also writes, through a redirect, is governed where it runs.
            moved_grade = grade_tokens(list(tokens), here or "", depth)[0]
            if moved_grade > 0:
                found.append((SHELL, moved_grade, here, _written(head, [], _targets, here)))
            args = body[1:]
            if alone is None:
                alone = line_wide
            # A confined `cd` leaves the directory unknown, not unchanged: zsh runs a
            # pipeline's last element in the current shell, so `x | cd d` moves it there.
            if alone or head == "popd" or any(a.startswith("-") for a in args) or len(args) > 1:
                here = None
            elif head == "pushd" and not args:
                here = None  # swaps with the directory stack, which this walk does not hold
            elif (args[0] if args else "~").startswith("~") and _home_unknown(variables):
                here = None  # HOME may have been reassigned earlier in the line
            else:
                here = _static_dir(args[0] if args else "~", here)
            continue
        found.extend(_governed(tokens, here, depth, variables, causes))
    # Any the segments did not account for: fail closed, HOME included.
    substitutions(len(queue), None, {_HOME_UNKNOWN: True})
    return found


def _unresolved_git_c_operands(command, cwd):
    """One unresolved `git -C` operand or None per governed `git push`, in execution order: the
    `causes` of the same walk `govern` reads, so wrappers are looked through exactly as there."""
    causes = []
    governed_text(command, cwd, causes=causes)
    return causes


_LEDGER = []


def _log(action_class, slug, level, grade, outcome, provider, event, runtime,
         error=None):
    """One `governance` row, with level None when no provider answer supplied one."""
    if not _LEDGER:
        _LEDGER.append(_sibling("decisions.py", "grade_bash_decisions"))
    module = _LEDGER[0]
    if module is None:
        return
    detail = {"action": action_class, "counterparty": slug, "level": level, "grade": grade,
              "outcome": outcome, "provider": provider}
    if error:
        detail["error"] = error
    module.record(GOVERNANCE_POINT, outcome, json.dumps(detail, sort_keys=True), event or {},
                  runtime)


def unresolved(operand):
    """What a write operand whose directory is unknown may be, judged by its name alone.

    An operand under a `cd` the walk cannot follow, or with a variable or substitution in its
    directory, has no path to check, so a name ending in `governance.json` or `config.json` is
    taken to be the file it names. An operand that is itself a variable is not judged here."""
    name = operand.strip("\"'")
    for suffix, what in ((POLICY_NAME, "a governance policy file"),
                         (CONFIG_NAME, "the harness configuration")):
        if name.endswith(suffix):
            return what + ", " + name + ", in a directory that cannot be known before it runs"
    return None


def _plain_words(line, redirects=False):
    """Return words with quote marks, the quoted heredoc delimiter, and whether it strips tabs.

    `marks` holds, per character of the word, whether it sat inside
    quotes, and `quoted` is True only when every character did. None for anything but plain
    words: a backslash, a `$` or backtick outside single quotes, an unquoted operator other than
    one `<<` or `<<-` opening a word before a quoted delimiter, or an unclosed quote."""
    words, delimiter, strip_tabs = [], None, False
    word, marks, started, quote, i, n = [], [], False, None, 0, len(line)
    while i < n:
        c = line[i]
        if quote == "'" and c != "'" or quote == '"' and c not in '"$`\\':
            word.append(c)
            marks.append(True)
        elif quote and c == quote:
            quote = None
        elif c in "$`\\":
            return None
        elif c in "'\"":
            quote, started = c, True
        elif c in " \t":
            if started:
                words.append(("".join(word), all(marks), marks))
            word, marks, started = [], [], False
        elif line.startswith("<<", i) and not started and delimiter is None:
            strip_tabs = line.startswith("<<-", i)
            rest = line[i + (3 if strip_tabs else 2):].lstrip(" \t")
            close = rest.find(rest[0], 1) if rest and rest[0] in "'\"" else -1
            if close < 0 or not HEREDOC_DELIMITER_RE.match(rest[1:close]):
                return None
            delimiter = rest[1:close]
            i = n - len(rest) + close + 1
            if i < n and line[i] not in " \t":
                return None
            continue
        elif redirects and c in "<>":
            if i + 1 < n and line[i + 1] in "<>|&!" and not (c == ">" and line[i + 1] == ">"):
                return None
            if started and not ("".join(word).isdigit() and not any(marks)):
                words.append(("".join(word), all(marks), marks))
            word, marks, started = [], [], False
            operator = c
            if i + 1 < n and line[i + 1] == c:
                operator += c
                i += 1
            words.append((operator, False, [False] * len(operator)))
        elif redirects and c in "#{}*?[]!~":
            return None
        elif c in ";&|<>()":
            return None
        else:
            word.append(c)
            marks.append(False)
            started = True
        i += 1
    if quote:
        return None
    if started:
        words.append(("".join(word), all(marks), marks))
    return words, delimiter, strip_tabs


def _names_policy(text):
    return bool(POLICY_RE.search(text) or CONFIG_RE.search(text))


def _names_config_set(text):
    return bool(CONFIG_SET_RE.search(text))


def gh_text_only(command, names=_names_policy):
    """Whether `command` is the one shape that may mention a policy path as data: exactly one
    `gh issue|pr create|comment|edit|review` command on one line, every policy path in it inside
    a quoted `--body`, `--title`, `-b` or `-t` value or inside the body of one quoted
    here-document fed to `--body-file -` or `-F -`, and nothing else on the line: no separator,
    pipe, background job, substitution, expansion or other redirect. Every other command that
    names a policy path may run code that writes it, so any doubt is False.

    `names` says what counts as a mention: a policy path by default, or `_names_config_set` for a
    `harness config set governance` command, which the same shape carries only as text."""
    lines = command.split("\n")
    parsed = _plain_words(lines[0])
    if parsed is None:
        return False
    words, delimiter, strip_tabs = parsed
    if len(words) < 3 or words[0][:2] != ("gh", False) or any(w[1] for w in words[:3]):
        return False
    if (words[1][0], words[2][0]) not in GH_TEXT_SUBCOMMANDS:
        return False
    tail = lines[1:]
    if strip_tabs:
        tail = [line.lstrip("\t") for line in tail]
    if delimiter is not None:
        if delimiter not in tail:
            return False
        end = tail.index(delimiter)
        tail = tail[end + 1:]
    if any(line.strip() for line in tail):
        return False
    plain = [w[0] for w in words]
    if delimiter is not None and not any(
            plain[k:k + 2] in (["--body-file", "-"], ["-F", "-"]) for k in range(len(plain))) \
            and "--body-file=-" not in plain:
        return False
    rest = []
    for k, (w, quoted, marks) in enumerate(words):
        name, eq, _value = w.partition("=")
        if eq and name in GH_TEXT_FLAGS and all(marks[len(name) + 1:]) and not any(
                marks[:len(name) + 1]) and not names(name):
            continue  # `--body='...'`: the value after `=` is quoted
        if quoted and k and words[k - 1][0] in GH_TEXT_FLAGS and not words[k - 1][1]:
            continue
        if names(w):
            return False
        rest.append(w)
    # A mention spread over several words outside the text values, such as an unquoted
    # `harness config set governance`, is not data either.
    return not names(" ".join(rest))


def literal_text_command(command):
    """One data-only utility with literal arguments and an optional quoted here-document.

    The caller still checks all written paths. Substitutions, unquoted here-documents, command
    chaining and interpreters are excluded so text cannot conceal another writer.
    """
    lines = command.split("\n")
    parsed = _plain_words(lines[0], redirects=True)
    if parsed is None:
        return False
    words, delimiter, strip_tabs = parsed
    if not words or words[0][1] or words[0][0] not in ("cat", "printf", "echo", "tee"):
        return False
    if words[0][0] == "printf":
        args, index = [], 1
        while index < len(words):
            if not words[index][1] and words[index][0] in ("<", ">", ">>"):
                index += 2
                continue
            args.append(words[index][0])
            index += 1
        # Shell printf has variable-writing options and formats (notably zsh %n).
        # Only string output and escaped percent conversions are known to be data-only.
        if not args or args[0].startswith("-") or "%" in re.sub(r"%%|%s", "", args[0]):
            return False
    tail = lines[1:]
    if strip_tabs:
        tail = [line.lstrip("\t") for line in tail]
    if delimiter is not None:
        if delimiter not in tail:
            return False
        tail = tail[tail.index(delimiter) + 1:]
    return not any(line.strip() for line in tail)


def _policy_hits(command, found, walked=True):
    """What a command changes that is a level-1 action: a policy file, the user configuration or
    a `governance` key set through `harness config set`.

    A policy path is judged first from the paths the walk found written: redirect targets and
    the operands of `tee`, `cp`, `mv`, `sed -i` and the like. The whole text, here-document
    bodies included, is then searched for a policy path by name unless the walk decomposed the
    line and it is `gh_text_only`: other commands may run code that writes a path they only
    names, so the search fails closed. The search for `harness config set governance` takes the
    same exemption. A literal data-only utility may additionally mention the user configuration
    path, but never exempts an actual write target."""
    paths = [p for entry in found for p in entry[3]]
    hits = sorted(set(filter(None, (guarded(p) if os.path.isabs(p) else unresolved(p)
                                    for p in paths))))
    try:
        exempt = walked and gh_text_only(command)
    except Exception:
        exempt = False
    if not hits and not exempt:
        match = POLICY_RE.search(command)
        if match:
            hits = ["the governance policy file " + match.group(0)]
        else:
            match = CONFIG_RE.search(command)
            if match is None:
                match = re.search(r"\.config[^\s]*[{}*?\[][^\s]*config\.json|"
                                  r"\.config[/\\]+agent-harness[/\\]+[^\s]*[{}*?\[]", command)
            if match and not (walked and literal_text_command(command)):
                hits = ["the harness configuration " + match.group(0)
                        + ", which selects the decision provider"]
    try:
        set_exempt = walked and gh_text_only(command, _names_config_set)
    except Exception:
        set_exempt = False
    if CONFIG_SET_RE.search(command) and not set_exempt:
        hits.append("the governance configuration, through `harness config set`")
    return hits


def govern(command, cwd, grade, variant, event=None, runtime=""):
    """What the decision provider adds to a command the grader lets through.

    None when nothing is added: provider `none`, a grade-0 command, or a provider that allows.
    Otherwise `(outcome, sentence)`, `ask` or `deny`, the sentence naming the class, counterparty,
    level and its source. Tighten-only by construction: the caller asks this only when its own
    answer is to let the command through. A configured provider that cannot answer is an ask
    naming the error, never an allow."""
    config = _config()
    name = provider_name(config)
    if name == NO_PROVIDER or not grade:
        return None
    cwd = cwd or os.getcwd()
    unresolved_git_c = []
    try:
        found = governed_text(command, cwd, causes=unresolved_git_c)
    except Exception:
        found, unresolved_git_c = None, []
    walked = found is not None
    if not found or max(entry[1] for entry in found) <= 0:
        # The grader graded the line above 0 yet no segment carries that grade: govern the whole
        # line at its grade rather than let the walk find nothing to ask about.
        found = (found or []) + [(SHELL, grade, cwd, [])]
    hits = _policy_hits(command, found, walked)
    if hits:
        _log(FILE_WRITE, None, POLICY_LEVEL, grade, "ask", name, event, runtime)
        return "ask", ("Governance: this changes %s, which is level %d: every change to it"
                       " needs the user's explicit yes." % ("; ".join(hits), POLICY_LEVEL))
    positive = []
    for action_class, level_grade, where, _written_paths in found:
        # Popped for every push, graded or not, so each operand stays with its own push.
        operand = (unresolved_git_c.pop(0)
                   if action_class == PUSH and unresolved_git_c else None)
        if level_grade > 0:
            positive.append((action_class, level_grade, where, operand))
    resolved, providers = [], {}
    try:
        decision = _decision_module()
        places = {cwd: decision.locate(cwd)}
        home_root = places[cwd][1] or cwd
        for action_class, level_grade, where, operand in positive:
            if where is None:
                slug, root = decision.UNKNOWN_COUNTERPARTY, home_root
            else:
                if where not in places:
                    places[where] = decision.locate(where)
                slug, top = places[where]
                root = top or where
            resolved.append((action_class, level_grade, slug, root, operand))
        # Validate every involved policy before any judgments, including the command's home
        # policy when its only positive-grade action is hidden from the segment walk.
        roots = dict.fromkeys([home_root] + [row[3] for row in resolved])
        for root in roots:
            providers[root] = decision.select_provider(config, root=root, variant=variant)
            load = getattr(providers[root], "policy", None)
            if callable(load):
                load()
    except Exception as exc:
        error = "%s: %s" % (type(exc).__name__, exc)
        rows = resolved + [(action_class, level_grade, None, None, None)
                           for action_class, level_grade, _where, _operand
                           in positive[len(resolved):]]
        for action_class, level_grade, slug, _root, _operand in rows:
            _log(action_class, slug, None, level_grade, "ask", name, event, runtime,
                 error=type(exc).__name__)
        return "ask", ("Governance: provider %s could not be set up, so this asks rather than runs"
                       " (%s)." % (name, error))

    worst = None
    for action_class, level_grade, slug, root, operand in resolved:
        try:
            answer = providers[root].decide(decision.Action(action_class, level_grade), slug)
            if answer.outcome not in RANK:
                raise decision.PolicyError("provider %s answered %r, not allow, ask or deny"
                                           % (name, answer.outcome))
            _log(action_class, slug, answer.autonomy_level, level_grade, answer.outcome,
                 answer.provider, event, runtime)
            if answer.outcome != "allow" and (worst is None
                                              or RANK[answer.outcome] > RANK[worst[0]]):
                sentence = ("Governance: %s on %s is level %d (%s)."
                            % (action_class, slug, answer.autonomy_level, answer.reason))
                if operand is not None:
                    shown = operand.replace("`", "'")[:160]
                    sentence += (" Git -C operand `%s` could not be resolved; pass the"
                                 " repository path literally." % shown)
                worst = answer.outcome, sentence
        except Exception as exc:
            error = "%s: %s" % (type(exc).__name__, exc)
            _log(action_class, slug, None, level_grade, "ask", name, event, runtime,
                 error=type(exc).__name__)
            if worst is None or RANK["ask"] > RANK[worst[0]]:
                worst = ("ask", "Governance: provider %s could not answer %s on %s at grade %d, "
                         "so this asks rather than runs (%s)."
                         % (name, action_class, slug, level_grade, error))
    return worst


def govern_file(tool, tool_input, paths, event=None, runtime=""):
    """`(subject, sentence)` for a file-tool write to a policy file or the user config, or None.

    Only when a provider other than `none` is configured. `subject` is what an approval code
    names: the tool and its exact input, so an approval covers that one edit."""
    name = provider_name(_config())
    if name == NO_PROVIDER:
        return None
    hits = sorted(set(filter(None, (guarded(p) for p in paths))))
    if not hits:
        return None
    _log(FILE_WRITE, None, POLICY_LEVEL, 1, "ask", name, event, runtime)
    subject = tool + "\n" + json.dumps(tool_input, sort_keys=True)
    return subject, ("Governance: this edits %s, which is level %d: every change to it needs"
                     " the user's explicit yes." % ("; ".join(hits), POLICY_LEVEL))


def main():
    if ro is None:
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
    grade, verb, target, family = grade_text(command, payload.get("cwd") or "")
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

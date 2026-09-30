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
import bisect
import fnmatch
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
SHELLS = {"bash", "sh", "zsh", "ksh", "mksh", "dash", "csh", "tcsh", "fish"}
# A word naming one of these makes any here-document body on the line a script (`_runs_input`).
SHELL_RUNNERS = SHELLS | {"eval", "ssh"}
# What a pipe from a group, subshell or loop carries: text this hook does not read.
COMPOUND = "compound"
COMPOUND_OPEN = {"{", "if", "while", "until", "for", "select", "case"}
COMPOUND_CLOSE = {"}", "fi", "done", "esac"}
# The opening words each closing word ends.
COMPOUND_MATCH = {"}": ("{",), "fi": ("if",), "done": ("while", "until", "for", "select"),
                  "esac": ("case",)}
# Text on a standard input this hook does not read (`_grade_streams`), and text the line spells
# out that a filter or a `printf` directive then reshapes past what the hook models.
UNKNOWN = object()
RESHAPED = object()
# A file copied from any file a line wrote, as a `cp` from a glob or variable writes (`_wild`).
ANY_FILE = object()
# A line that may change its directory, and the commands that may move, copy or link a file it
# wrote or change its directory by being sourced: a file it runs then matches one it wrote by
# base name alone (`_Written`).
DIR_CHANGE_RE = re.compile(r"\b(?:cd|pushd|popd|chdir)\b|--directory\b|(?<!\S)-[A-Za-z]*[CD]")
RELOCATORS = {"cp", "mv", "ln", "install", "rsync", "ditto", "tar", "unzip", "cpio", "pax", "git",
              ".", "source"}
HERE_STRING_RE = re.compile(r"^\d*<<<$")
# The names under which a program reads its standard input as a file.
STDIN_PATHS = {"-", "/dev/stdin", "/dev/fd/0", "/proc/self/fd/0"}
# Interpreters that read their program from standard input when nothing else names one, by
# family: (the options whose value is the program instead, the options that take another value).
INTERPRETER_RE = re.compile(
    r"^(python|pypy|perl|ruby|node|nodejs|php|lua|luajit|tclsh|wish|julia|Rscript|R|osascript|"
    r"expect|pwsh|powershell|irb|jshell|swift|guile|racket|sbcl|ghci|iex|elixir|erl|groovy|"
    r"scala|deno|bun)[0-9.]*$")
INTERPRETER_FAMILY = {"pypy": "python", "nodejs": "node", "luajit": "lua", "powershell": "pwsh"}
PROGRAM_FLAGS = {
    "python": ({"-c", "-m"}, {"-W", "-X", "-Q"}),
    "perl": ({"-e", "-E"}, {"-I", "-M", "-m"}),
    "ruby": ({"-e"}, {"-I", "-r", "-C", "-E", "-F"}),
    "node": ({"-e", "-p", "--eval", "--print"},
             {"-r", "--require", "--import", "--loader", "--experimental-loader", "-C",
              "--conditions", "--input-type", "--env-file"}),
    "php": ({"-r", "-R", "-B", "-E", "-F"}, {"-c", "-d", "-z"}),
    "lua": ({"-e"}, {"-l"}),
    "osascript": ({"-e"}, {"-l", "-s"}),
    "Rscript": ({"-e", "--expr"}, set()),
    "pwsh": ({"-c", "-Command", "-command", "-f", "-File", "-file", "-EncodedCommand", "-e"},
             {"-ExecutionPolicy", "-ep", "-WorkingDirectory", "-wd"}),
}
DEFAULT_PROGRAM_FLAGS = ({"-c", "-e", "-E", "--eval"}, set())
# Options whose value is a file the interpreter runs as its program.
PROGRAM_FILE_VALUES = {"pwsh": {"-f", "-File", "-file"}, "php": {"-f"}}
# Families whose short options cluster as getopt reads them, so `perl -ne` reads like `-n -e`.
CLUSTERED = {"python", "perl", "ruby"}
# Interpreters that read a program file only through an option, by name: (the option letters
# whose value is a program file, the long options that are, the other letters that take a value,
# the letters whose optional value is only the rest of their cluster).
PROGRAM_FILE_OPTIONS = {name: ("fEi", ("--file", "--exec", "--include"), "vFl", "")
                        for name in ("awk", "gawk", "mawk", "nawk")}
PROGRAM_FILE_OPTIONS.update({name: ("f", ("--file",), "e", "") for name in ("sed", "gsed")})
MAKES = {"make", "gmake", "bmake"}
PROGRAM_FILE_OPTIONS.update({name: ("f", ("--file", "--makefile"), "CIoW", "jl") for name in MAKES})
# The files `make` runs when no option names one.
DEFAULT_MAKEFILES = ("GNUmakefile", "makefile", "Makefile")
# A word naming an interpreter, for text that does not decompose.
INTERPRETER_WORD_RE = re.compile(r"(?:^|[\s;&|(`])(?:\S*/)?(?:python|pypy|perl|ruby|node|php|"
                                 r"lua|tclsh|osascript|Rscript|pwsh|deno|bun|[gmn]?awk|g?sed|"
                                 r"[gb]?make)[\w.]*(?=[\s;&|)`]|$)")
# The characters that open a string in program text (`_literals`).
QUOTE_RE = re.compile(r"['\"`]")
# How many lines and strings of an interpreter's program `_grade_program` grades as shell text;
# past these it scans the rest.
PROGRAM_CHECKS = 64
# How far `_statement` looks either way for the lines an open bracket joins to one.
STATEMENT_LINES = 16
STATEMENT_CHARS = 4096
PRINTF_SPEC_RE = re.compile(r"%(?:%|([-+ #0']*)(\*|\d+)?(?:\.(\*|\d*))?([a-zA-Z]))")
# The widest field `_printf` pads, and the longest text it writes; past these it reads as unknown.
PRINTF_WIDTH = 4096
PRINTF_ESCAPE_RE = re.compile(r"\\(0[0-7]{0,3}|[1-7][0-7]{0,2}|x[0-9a-fA-F]{1,2}|.)", re.S)
PRINTF_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v",
                  "\\": "\\", "e": "\x1b", "E": "\x1b", "'": "'", '"': '"'}
SHELL_WORD_RE = re.compile(r"(?:^|[\s;&|(`])=?(?:\S*/)?"
                           r"(?:bash|sh|zsh|m?ksh|dash|t?csh|fish|eval|ssh|source|\.)"
                           r"(?=[\s;&|)`]|$)")
# A command word bash may expand into a name: a brace expansion, a variable or a glob.
BRACE_RE = re.compile(r"\{[^{}]*(?:,|\.\.)[^{}]*\}")
# How many substitutions `_body_substitutions` reads in one unquoted body before it calls the
# body unreadable: each can read to the end of the body.
BODY_SUB_CHECKS = 64
# What in a body substitution bash 3.2 and zsh may close at a different `)` than the matcher
# does: a comment, a `case` pattern or a nested here-document.
UNSURE_SUB_RE = re.compile(r"(?<![^\s;&|()])(?:#|case(?![^\s;&|)]))|<<")
# The backslashes bash removes from the text of a backtick substitution before running it.
BACKTICK_ESCAPE_RE = re.compile(r"\\([\\`$])")
# The backslashes bash removes from an unquoted here-document body as it expands it: before `$`,
# a backtick, a backslash or a newline, which goes too.
BODY_ESCAPE_RE = re.compile(r"\\([\\`$\n])")
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
# The call only: the `db.…` chain before it is checked by `_mongo`, walking back, because a
# regex that matches the chain backtracks quadratically on a body of repeated `db.`.
MONGO_CALL_RE = re.compile(r"\.(dropDatabase|drop|deleteMany|remove)\s*\(")
SQL_CLIENT_RE = re.compile(r"(?<![\w.-])(?:psql|mysql|mariadb|sqlite3|mongosh|mongo|"
                           r"clickhouse-client)(?![\w.-])")
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


HEREDOC_WORD_END = set(" \t\n;&|()<>")
# A substitution holding a here-document is checked against bash 3.2's reading, which finds
# its closing parenthesis without skipping the body. Each check can read to the end of the text,
# so past this many, or in a text longer than `HEREDOC_CHECKED_LENGTH`, the line is uncertain.
HEREDOC_SUB_CHECKS = 8
HEREDOC_CHECKED_LENGTH = 65536
# A word that opens an array subscript `a[…]` or a compound assignment `a=(…)` / `a+=(…)`.
ARRAY_OPEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\[|\+?=\()")
IDENT_START = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_")
COMMAND_OPENERS = {"if", "then", "else", "elif", "do", "while", "until", "time", "!", "{"}


def _command_position(text, i):
    """Whether bash surely reads the word at text[i] where a command or assignment begins: first
    in the text, or after an unescaped newline, `;`, `&`, `|` or `(` that is no redirection, or
    after a word such as `then` that opens a command. After an assignment or `declare` bash may
    still take one, so those, like anything else, are not sure."""
    j = i - 1
    while j >= 0 and text[j] in " \t":
        j -= 1
    if j < 0:
        return True
    if j and text[j - 1] == "\\":
        return False
    if text[j] in "\n;(":
        return True
    if text[j] in "&|":
        return not j or text[j - 1] not in "<>"
    k = j
    while k >= 0 and j - k < 6 and text[k] not in HEREDOC_WORD_END:
        k -= 1
    return text[k + 1:j + 1] in COMMAND_OPENERS and (k < 0 or text[k] in HEREDOC_WORD_END)


def _heredoc_word(text, i):
    """(delimiter, quoted, end, certain) for the here-document word starting at text[i], or None
    when there is none. bash takes the word as written and quote-removed, expanding nothing: the
    body of `<<E"OF"` ends at `EOF`, and any quoted part leaves the body unexpanded. A `$` or
    backtick, a `$'…'` or `$"…"` string, a newline or a continuation in the word, or a quote
    left open is read differently by shells and versions, so the word is not certain."""
    out, quoted, certain, n = [], False, True, len(text)
    start = i
    while i < n and text[i] not in HEREDOC_WORD_END:
        c = text[i]
        if c == "\\":
            if i + 1 >= n or text[i + 1] == "\n":
                certain = False
                i += 2
                continue
            out.append(text[i + 1])
            quoted = True
            i += 2
            continue
        if c in "'\"":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if c == '"' and text[j] == "\\" else 1
            content = text[i + 1:j]
            if c == '"':
                if "$" in content or "`" in content:
                    certain = False
                content = re.sub(r'\\([$`"\\])', r"\1", content)
            if j >= n or "\n" in content:
                certain = False
            out.append(content)
            quoted = True
            i = j + 1
            continue
        if c in "$`":
            certain = False
            if text.startswith("$'", i):
                j = ro._ansi_end(text, i) or n
                out.append(text[i + 2:j - 1])
                quoted = True
                i = j
                continue
            if text.startswith('$"', i):
                i += 1  # read here as the double-quoted string alone
                continue
        out.append(c)
        i += 1
    if i == start:
        return None
    return "".join(out), quoted, min(i, n), certain


class Body(str):
    """A here-document body. `quoted` when its delimiter was, so the shell expands nothing in it:
    an unquoted body runs its `$(…)` and backtick substitutions even when it is only data."""
    quoted = True


def _body(text, quoted):
    body = Body(text)
    body.quoted = quoted
    return body


def _heredoc_bodies(text, i, pending):
    """(index after the bodies, [(body, delimiter line)]) for the here-documents `pending`, whose
    bodies start at text[i]. A body ends at the first line that is exactly its delimiter, after
    `<<-` strips its leading tabs. In a body whose delimiter is unquoted, a line ending in an odd
    number of backslashes continues onto the next, as bash reads it: `a\\` then `EOF` is `aEOF`,
    and a lone `\\` then `EOF` is the delimiter. A body never ended runs to the end of the text."""
    n, found = len(text), []
    for delimiter, strip_tabs, quoted in pending:
        lines = []
        while i < n:
            start = i
            parts = []
            while True:
                end = text.find("\n", i)
                end = n if end < 0 else end
                line = text[i:end]
                i = min(end + 1, n)
                backslashes = len(line) - len(line.rstrip("\\"))
                if not quoted and backslashes % 2 and i < n and end < n:
                    parts.append(line[:-1])
                    continue
                parts.append(line)
                break
            logical = "".join(parts)
            if (logical.lstrip("\t") if strip_tabs else logical) == delimiter:
                found.append((_body("\n".join(lines), quoted), text[start:i]))
                break
            lines.append(text[start:i].rstrip("\n"))
        else:
            found.append((_body("\n".join(lines), quoted), ""))
    return i, found


def _split_heredocs(text):
    """(text without the body of every here-document, the bodies, whether every here-document
    operator and delimiter was placed for sure). Each body is a `Body` that knows whether its
    delimiter was quoted; which parts of it run is `_grade_bodies`'s call.

    An operator is a `<<` or `<<-` bash would read as one: unquoted, outside a comment, not the
    `<<<` of a here-string, not a shift inside `$((…))`, `((…))` or `$[…]`, and not inside an
    array subscript or compound assignment that starts a command. Its body starts after the next
    newline bash reads as a token in the construct that holds the operator: one a continuation
    does not end, and not one inside a substitution, an expansion or a subscript, which bash
    reads to its close first. The body ends at the delimiter line `_heredoc_bodies` finds, which
    leaves the text as a bare newline, so no quote or backslash in it reaches the lines after.

    Not certain: a delimiter `_heredoc_word` cannot read for sure, a `<<` inside `${…}`, an
    arithmetic expression that does not close as one, a construct left open, a here-document
    whose substitution closes before its body starts, one whose line ends inside a nested
    construct, which bash 3.2 reads as above and later versions are not checked on, one after
    a subscript or compound assignment that may be an assignment, and one inside a `$(…)` whose
    closing parenthesis bash 3.2, matching it without skipping the body, would place elsewhere.
    The caller then also reads the text with no body removed and keeps the worse grade.

    Linear: text past `SCAN_CAP` outside the bodies is returned unread, for the caller to refuse
    by its length, and the bash 3.2 check runs a bounded number of times."""
    if "<<" not in text:
        return text, [], True
    out, bodies, pending, certain = [], [], [], True
    stack, checks, skipped, i, n = [["top"]], 0, 0, 0, len(text)
    has_case, unsure = CASE_WORD_RE.search(text) is not None, None
    # The `top` or `sub` frame that holds each run of `pending`, as [frame, index of its first
    # entry], innermost last: an operator's body starts only at a newline in its own frame.
    holders = []
    while i < n:
        if i - skipped > SCAN_CAP:  # too long to grade: the caller refuses the text by its length
            out.append(text[i:])
            return "".join(out), bodies, False
        frame = stack[-1]
        state, c = frame[0], text[i]
        if c == "\n" and pending and state in ("top", "sub", "arith", "brace", "index",
                                                "compound"):
            owner = stack[-2] if state == "compound" else frame
            first = holders[-1][1] if holders and holders[-1][0] is owner else len(pending)
            ready = pending[first:]
            if first:
                certain = False
            if ready:
                del pending[first:]
                holders.pop()
                out.append(c)
                start = i + 1
                i, found = _heredoc_bodies(text, start, ready)
                skipped += i - start
                for body, delimiter_line in found:
                    bodies.append(body)
                    if delimiter_line.endswith("\n"):
                        out.append("\n")
                        skipped -= 1
                continue
        if state == "single":
            if c == "'":
                stack.pop()
            out.append(c)
            i += 1
            continue
        if state == "comment":
            if c == "\n":
                stack.pop()
                continue
            out.append(c)
            i += 1
            continue
        if c == "\\":
            out.append(text[i:i + 2])
            i += 2
            continue
        if state == "ansi":
            if c == "'":
                stack.pop()
            out.append(c)
            i += 1
            continue
        if state == "backtick":  # opaque here: bash finds its end without reading the body
            if c == "`":
                stack.pop()
            out.append(c)
            i += 1
            continue
        step, opens = 1, None
        if unsure and c == unsure and state in ("top", "sub"):
            unsure = None
        if state in ("top", "sub") and c in IDENT_START and (i == 0 or text[i - 1]
                                                            in HEREDOC_WORD_END):
            opens = ARRAY_OPEN_RE.match(text, i)
        if text.startswith("$$", i):
            step = 2
        elif text.startswith("$[", i):
            step = 2
            stack.append(["index", 0])
        elif text.startswith("$((", i) or (state != "double" and text.startswith("((", i)):
            step = 3 if c == "$" else 2
            stack.append(["arith", 0, c == "$"])
        elif text.startswith("$(", i) or (state in ("top", "sub")
                                           and text.startswith(("<(", ">("), i)):
            step = 2
            stack.append(["sub", 0, i + 1, False])
        elif text.startswith("${", i):
            step = 2
            stack.append(["brace"])
        elif c == "`":
            stack.append(["backtick"])
        elif state == "double":
            if c == '"':
                stack.pop()
        elif text.startswith(("$'", '$"'), i):
            step = 2
            stack.append(["ansi" if text[i + 1] == "'" else "double"])
        elif c in "'\"":
            stack.append(["single" if c == "'" else "double"])
            if state == "brace":
                certain = False
        elif opens:
            # bash reads `a[…]` as a subscript and `a=(…)` as a compound assignment, neither
            # holding a here-document, where an assignment can start; elsewhere `a[1<<2]` holds
            # one. Where that is not sure, a `<<` before the close is read both ways.
            if _command_position(text, i) and not has_case:
                step = opens.end() - i
                stack.append(["index", 0] if opens.group(1) == "[" else ["compound"])
            else:
                step = opens.start(1) - i
                unsure = "]" if opens.group(1) == "[" else ")"
        elif state == "index":
            if c == "[":
                frame[1] += 1
            elif c == "]":
                if frame[1]:
                    frame[1] -= 1
                else:
                    stack.pop()
        elif state == "compound" and c == ")":
            stack.pop()
        elif state == "brace":
            if c == "}":
                stack.pop()
            elif text.startswith("<<", i):
                certain = False
        elif text.startswith("<<<", i):
            step = 3
        elif text.startswith("<<", i) and state not in ("arith", "compound"):
            if unsure:
                certain = False
            j = i + 2
            strip_tabs = text.startswith("-", j)
            j += 1 if strip_tabs else 0
            while j < n and text[j] in " \t":
                j += 1
            word = _heredoc_word(text, j)
            if word is None:
                certain = False
                step = 2
            else:
                delimiter, quoted, step, sure = word
                step -= i
                certain = certain and sure
                if not holders or holders[-1][0] is not frame:
                    holders.append([frame, len(pending)])
                pending.append((delimiter, strip_tabs, quoted))
                if state == "sub":
                    frame[3] = True
        elif c == "#" and state != "arith" and (i == 0 or text[i - 1] in WORD_START):
            stack.append(["comment"])
        elif c == "(" and state in ("sub", "arith"):
            frame[1] += 1
        elif c == ")" and state in ("sub", "arith") and frame[1]:
            frame[1] -= 1
        elif c == ")" and state == "arith":
            if text.startswith("))", i):
                step = 2
                stack.pop()
            else:  # not arithmetic after all: bash reads a subshell or a substitution
                certain = False
                stack.pop()
                if frame[2]:
                    stack.append(["sub", 0, i, False])
        elif c == ")" and state == "sub":
            stack.pop()
            if pending:
                certain = False
                if holders and holders[-1][0] is frame:  # its bodies start in the parent
                    if len(holders) > 1 and holders[-2][0] is stack[-1]:
                        holders.pop()
                    else:
                        holders[-1][0] = stack[-1]
            if frame[3]:
                # bash 3.2 closes the substitution where a quote-aware match does, which a `)` in
                # the body can move and run the lines after it; where the match finds no close,
                # 3.2 stops on a syntax error and runs nothing more.
                checks += 1
                if checks > HEREDOC_SUB_CHECKS or n > HEREDOC_CHECKED_LENGTH:
                    certain = False
                elif ro._match_paren(text, frame[2]) not in (i, None):
                    certain = False
        out.append(text[i:i + step])
        i += step
    if len(stack) > 1 and any(f[0] != "comment" for f in stack[1:]):
        certain = False
    return "".join(out), bodies, certain


def _strip_comments(text):
    """Text without its `#` comments, with quote state carried across newlines, so a `#` inside
    a multi-line quoted string stays and a comment outside one takes the rest of its line. An
    ANSI-C `$'…'` string is copied whole: its `\\'` does not end it."""
    out = []
    sq = dq = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if not sq and text.startswith("$$", i):
            out.append("$$")
            i += 2
            continue
        if not (sq or dq) and text.startswith("$'", i):
            end = ro._ansi_end(text, i) or n
            out.append(text[i:end])
            i = end
            continue
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
    or behind a line continuation. What in a body runs is graded by `_grade_bodies`, and a SQL
    client's body by `_sql`."""
    texts, bodies = _readings(cmd)
    if texts is None:
        return _strip_comments(_outer(cmd)[0]), bodies
    return texts[0], bodies


def _unix(cmd):
    return cmd.replace("\r\n", "\n").replace("\r", "\n")


def _outer(cmd):
    """(the shell text with here-document bodies split out, the bodies, whether every
    here-document was placed for sure)."""
    return _split_heredocs(_unix(cmd))


def _readings(cmd):
    """([shell text, …], here-document bodies): one reading, or more when a construct cannot be
    placed with certainty. A here-document `_split_heredocs` cannot place adds the text with no
    body removed; a backslash-newline `_join_continuations` cannot place adds, per text, the
    text with every pair removed, as the hook read continuations before it modelled quoting. A
    caller grades the worse reading and takes no directory from any: with more than one, the
    texts may even be equal.

    The texts are None when a text to read, bodies excluded where they were placed, is longer
    than `SCAN_CAP`: the caller grades it too long without decomposing it, because the lexer and
    the substitution walk are not linear in its length and a hook that runs past its timeout
    fails open."""
    text, bodies, placed = _outer(cmd)
    bases = [text] if placed else [text, _unix(cmd)]
    if any(len(base) > SCAN_CAP for base in bases):
        return None, bodies
    texts = []
    for base in bases:
        joined, certain = _join_continuations(base)
        texts.extend([joined] if certain else [joined, base.replace("\\\n", "")])
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
        if opener == "$$":  # one parameter: a quote after it is a plain one, not `$'…'`
            out.append(opener)
            i += 2
            continue
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
    its separator-free chunks, else a push at grade 2 when one chunk names `git` and `push`, as
    `governed_text` counts it, or grade 1. Linear in the length of the text, and the text it
    reads is capped, because a hook that runs past its timeout fails open."""
    if len(text) > SCAN_CAP:
        return TOO_LONG
    return _scan_text(text)


def _scan_text(text):
    """`_scan` without its cap, for text that is not a command line, such as a program an
    interpreter reads: every check is a substring search, so it stays linear at any length."""
    lowered = text.lower()
    # Only an entry whose needles all occur in the whole text can match one chunk of it.
    entries = [entry for entry in SCAN if all(needle in lowered for needle in entry[0])]
    push = "git" in lowered and "push" in lowered
    if not entries and not push:
        return 1, "", "", "opaque"
    chunks = SCAN_SPLIT.split(lowered)
    for chunk in chunks:
        for needles, verb, family in entries:
            if all(needle in chunk for needle in needles):
                return 3, verb, "", family
    if push and any("git" in chunk and "push" in chunk for chunk in chunks):
        return 2, "git push", "", "remote"
    return 1, "", "", "opaque"


def _extract_subs(cmd):
    """(text with every substitution replaced by a placeholder, the inner texts). The text is
    None when a substitution or an ANSI-C `$'…'` string never closes."""
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
        if cmd.startswith("$$", i):
            out.append("$$")
            i += 2
            continue
        if not dq and cmd.startswith("$'", i):
            end = ro._ansi_end(cmd, i)
            if end is None:
                return None, inners
            out.append(cmd[i:end])
            i = end
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
    linked = _linked_segments(text)
    return None if linked is None else [tokens for tokens, _fed in linked]


def _linked_segments(text):
    """[(simple command, what feeds its standard input)] as `segments` splits `text`, or None.
    What feeds it is the index of the command piped into it, `COMPOUND` for a pipe from a
    group, subshell or loop, or None. A `(` or `{` after the pipe leaves its first command fed,
    and every later command inside that compound reads what the first left of the pipe, graded
    as `COMPOUND`, as bash gives each of them the compound's standard input."""
    text = " ; ".join(text.split("\n"))
    try:
        tokens = ro.tokenize(text)
    except ValueError:
        return None
    # Per open compound, its opening word and `COMPOUND` when a pipe feeds it, else None.
    out, cur, skipping, fed, groups = [], [], False, None, []

    def opened(word):
        groups.append((word, COMPOUND if fed is not None or (groups and groups[-1][1])
                       else None))

    def closed(words):
        if groups and groups[-1][0] in words:
            groups.pop()
        return groups[-1][1] if groups else None

    for token in tokens:
        if token in ro.ALWAYS_DELIM:
            if cur:
                out.append((cur, fed))
            if token in ("|", "|&"):
                fed = len(out) - 1 if cur else COMPOUND
            elif token == "(":
                opened(token)
            else:
                fed = closed(("(",)) if token == ")" else groups[-1][1] if groups else None
            cur, skipping = [], False
            continue
        if skipping:
            continue
        if not cur:
            if token in COMPOUND_OPEN:
                opened(token)
            elif token in COMPOUND_CLOSE:
                fed = closed(COMPOUND_MATCH[token])
            if token in ro.WORD_DROP or token in ro.WORD_COND or token == "!":
                continue
            if token in ro.WORD_HEADER:  # `for x in *` names data, not commands
                skipping = True
                continue
        cur.append(token)
    if cur:
        out.append((cur, fed))
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
    method = _mongo(text)
    if method:
        return 3, "db.%s()" % method, "", "database"
    return None


def _mongo(text):
    """The destructive method of the first `db.<name>….<method>(` call in `text`, or None: what
    `\\bdb(?:\\.\\w+)*\\.(method)\\s*\\(` finds, in linear time. Each call is preceded by a run of
    word characters and dots that ends at a `(` or earlier, so the walks back never overlap."""
    for match in MONGO_CALL_RE.finditer(text):
        start = match.start()
        while start and (text[start - 1] == "." or text[start - 1] == "_"
                         or text[start - 1].isalnum()):
            start -= 1
        names = text[start:match.start()].split(".")
        for index, name in enumerate(names):
            if name == "db" and all(names[index + 1:]):
                return match.group(1)
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
# The programs `grade_tokens` may grade above 1, by name: a line or string of an interpreter's
# program that names none of them is not graded as shell text (`_grade_program`).
NAMED_PROGRAMS = (SHELL_RUNNERS | CLOUD | SQL_CLIENTS | set(PUBLISH) | set(G3_SUBCOMMANDS)
                  | set(G2_SUBCOMMANDS) | set(SUDO) | set(WRAPPERS) | {p for p, _s in RUNNERS}
                  | {"git", "gh", "curl", "wget", "http", "https", "httpie", "rm", "find",
                     "redis-cli", "prisma", "rails", "rake", "manage.py", "dbmate", "docker",
                     "docker-compose", "kubectl", "vercel", "netlify", "chmod", "chown",
                     "chgrp", "mkfs", "dd", "shutdown", "reboot", "halt", "diskutil", "crontab",
                     "launchctl", "kill", "history", "shred", "xargs", "parallel", "fly",
                     "cargo"})
# A lookbehind would defeat the regex engine's first-character search, so `_named` checks the
# character before each match instead.
NAMED_RE = re.compile(r"(?:%s)(?![\w-])"
                      % "|".join(re.escape(n) for n in sorted(NAMED_PROGRAMS, key=len,
                                                             reverse=True)))
NAME_BEFORE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")


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
    if depth == 0 and len(cmd) <= ro.MAX_LENGTH and ro.command_ok(cmd):
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
    linked = _linked_segments(stripped) if stripped is not None else None
    parts = None if linked is None else [tokens for tokens, _fed in linked]
    executed = False
    if linked is not None:
        hit, executed = _grade_streams(linked, stripped, cwd, depth)
        best = max(best, hit, key=lambda h: h[0])
    if bodies:
        runs = executed or _feeds_shell(text, parts, inners, depth)
        programs = executed or _feeds_program(text, parts, inners, depth)
        best = max(best, _grade_bodies(bodies, runs, cwd, depth, programs), key=lambda h: h[0])
    if parts is None:
        return max(best, _scan(text), key=lambda h: h[0])
    # A SQL client named anywhere, a substitution included, since a body inside `$(…)` is
    # split out of the outer text before its command is graded.
    if bodies and SQL_CLIENT_RE.search(text):
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


def _runs_input(tokens):
    """Whether the simple command `tokens` may run its standard input or an argument as shell
    text: a shell, `eval`, `ssh`, or a `.` or `source` at its head, past assignments and
    wrappers, or a head that may expand to one of them (`_may_name_shell`)."""
    tokens, _written = _redirects(list(tokens))
    if any(t.rpartition("/")[2].lstrip("=") in SHELL_RUNNERS for t in tokens):
        return True
    for _ in range(MAX_DEPTH):
        while tokens and ASSIGN_RE.match(tokens[0]):
            tokens = tokens[1:]
        if not tokens:
            return False
        head = tokens[0]
        if _may_name_shell(head):
            return True
        prog = head.rpartition("/")[2]
        if prog == "env":
            rest = _env_split(tokens[1:])
            if rest is None:
                return True
            tokens = strip_options(rest, WRAPPERS[prog])
        elif prog in WRAPPERS:
            tokens = strip_options(tokens[1:], WRAPPERS[prog])
        elif prog in ("xargs", "parallel"):
            tokens = strip_options(tokens[1:], XARGS_VALUE_FLAGS)
        else:
            return False
    return True


def _may_name_shell(word):
    """Whether bash, or zsh, may run the command word `word` as a shell, `eval`, `ssh`, `.` or
    `source`: by its name, a zsh `=name`, or a variable, substitution, brace expansion or glob
    that may expand to one. What cannot be expanded here counts as a shell."""
    if PLACEHOLDER in word or "$" in word or "`" in word or BRACE_RE.search(word):
        return True
    name = word.rpartition("/")[2].lstrip("=")
    if name in SHELL_RUNNERS or name in (".", "source") or re.search(r"\[.*\]", name):
        return True
    return ("*" in name or "?" in name) and any(
        fnmatch.fnmatchcase(each, name) for each in SHELL_RUNNERS | {"source"})


def _env_split(args):
    """`env`'s arguments with the string of its `-S` split into the words env reads, or None
    when the string holds a quote, a backslash or a `$`, which env interprets itself."""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--" or not a.startswith("-") or a == "-":
            return args
        value = None
        if a in ("-S", "--split-string"):
            value, i = (args[i + 1] if i + 1 < len(args) else ""), i + 2
        elif a.startswith("--split-string="):
            value, i = a.partition("=")[2], i + 1
        elif not a.startswith("--"):
            letters = a[1:]
            cut = min((letters.find(c) for c in "uCPS" if c in letters), default=-1)
            if cut >= 0 and letters[cut] == "S":
                value = letters[cut + 1:]
                if not value:
                    value, i = (args[i + 1] if i + 1 < len(args) else ""), i + 1
                i += 1
        if value is None:
            i += 2 if a in WRAPPERS["env"] or a == "-P" else 1
            continue
        if any(c in value for c in "\\'\"$"):
            return None
        return value.split() + args[i:]
    return args


def _feeds_shell(text, parts, inners, depth, reads=None, words=SHELL_WORD_RE):
    """Whether a shell may run a here-document body of `text`: some simple command in it, a
    substitution's included, is one `_runs_input` names. Which body reaches which command is not
    modelled, so one such command makes every body a script. Text that does not decompose is
    searched for the names instead. `reads` and `words` put another reader in the shell's place,
    as `_feeds_program` does."""
    reads = reads or _runs_input
    if parts is None:
        return words.search(text) is not None
    if any(reads(tokens) for tokens in parts):
        return True
    if depth >= MAX_DEPTH:
        return bool(inners)
    for inner in inners:
        stripped, nested = _extract_subs(inner)
        if _feeds_shell(inner, segments(stripped) if stripped is not None else None, nested,
                        depth + 1, reads, words):
            return True
    return False


def _feeds_program(text, parts, inners, depth):
    """Whether an interpreter other than a shell may read a here-document body of `text` as its
    program (`_interprets_input`), in the way `_feeds_shell` answers it for a shell."""
    return _feeds_shell(text, parts, inners, depth, _interprets_input, INTERPRETER_WORD_RE)


def _body_substitutions(body):
    """The texts the shell runs while it expands an unquoted here-document body, or None when
    they cannot be read for sure. Quotes are literal in a body, so `'$(x)'` runs `x`; a
    backslash escapes `$`, a backtick and itself, and removes a newline, so `$\\` then `(x)`
    runs `x` too. A `$((…))` is arithmetic whose own substitutions the walk still finds; a
    `$((` that does not close as one is a substitution, as bash 3.2 reads `$((x) )`.

    Where a comment, a `case` pattern or a nested here-document may move the `)` that closes a
    substitution, as bash 3.2 and zsh disagree on it, the rest of the body from that `$(` on is
    one more text, read as a script. A backtick substitution loses the backslashes bash removes
    before running it, so an escaped backtick inside it opens a nested one."""
    out, i, n = [], 0, len(body)
    while i < n:
        if body[i] == "\\":
            if body.startswith("\n", i + 1):
                i += 2
                continue
            out.append(body[i:i + 2])
            i += 2
            continue
        out.append(body[i])
        i += 1
    text = "".join(out)
    if "$(" not in text and "`" not in text:
        return []
    if len(text) > HEREDOC_CHECKED_LENGTH:
        return None
    inners, rest, checks, i, n = [], None, 0, 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if text.startswith("$$", i):  # the shell's process ID, then plain text
            i += 2
            continue
        if text.startswith("$(", i):
            checks += 1
            if checks > BODY_SUB_CHECKS:
                return None
            if text.startswith("$((", i):
                k = ro._match_paren(text, i + 2)
                if k is not None and text.startswith(")", k + 1):
                    if rest is None and UNSURE_SUB_RE.search(text, i + 3, k):
                        rest = text[i + 2:]
                    i += 3
                    continue
            j = ro._match_paren(text, i + 1)
            if j is None:
                return None
            if rest is None and UNSURE_SUB_RE.search(text, i + 2, j):
                rest = text[i + 2:]
            inners.append(text[i + 2:j])
            i = j + 1
            continue
        if c == "`":
            j = i + 1
            while j < n and text[j] != "`":
                j += 2 if text[j] == "\\" else 1
            if j >= n:
                return None
            inners.append(BACKTICK_ESCAPE_RE.sub(r"\1", text[i + 1:j]))
            i = j + 1
            continue
        i += 1
    if len(inners) > BODY_SUB_CHECKS or sum(len(inner) for inner in inners) > SCAN_CAP:
        return None
    return inners + ([rest] if rest is not None else [])


def _nested_texts(text, levels):
    """[(text, level)] for `text` and every substitution nested in it down `levels` levels, each
    of which runs when `text` does, or None past `BODY_SUB_CHECKS` of them. A text with a
    backslash before a backtick, `$` or backslash is read unescaped as well, as bash reads the
    text of a backtick substitution."""
    out, todo = [], [(text, 0)]
    while todo:
        each, level = todo.pop()
        variants = [each]
        if "\\" in each:
            unescaped = BACKTICK_ESCAPE_RE.sub(r"\1", each)
            if unescaped != each:
                variants.append(unescaped)
        for variant in variants:
            out.append((variant, level))
            if len(out) > BODY_SUB_CHECKS:
                return None
            if level >= levels or len(variant) > SCAN_CAP or (
                    "$(" not in variant and "`" not in variant and "<(" not in variant
                    and ">(" not in variant):
                continue
            _stripped, nested = _extract_subs(variant)
            todo.extend((inner, level + 1) for inner in nested)
    return out


def _grade_bodies(bodies, runs, cwd, depth, programs=False):
    """The worst grade the here-document bodies can carry. An unquoted body's substitutions run
    as the shell expands it, a body a shell may read runs as a script, and each is graded as the
    commands it holds; a body neither applies to is data, graded 0. A shell reads an unquoted
    body with the escapes bash removed while expanding it, so `\\$(x)` in it runs `x`: that
    script is graded as well as the body as written. When `programs`, another interpreter may
    read a body as its program, which `_grade_program` grades. What cannot be read for sure is
    graded unknown, or as `_scan` finds it, never lower."""
    best = (0, None, None, None)
    for body in bodies:
        hits = [_grade_program(body, cwd, depth)] if programs else []
        if runs:
            hits.append(grade_text(body, cwd, depth + 1))
            if not getattr(body, "quoted", True):
                script = BODY_ESCAPE_RE.sub(
                    lambda m: "" if m.group(1) == "\n" else m.group(1), body)
                if script != body:
                    hits.append(grade_text(script, cwd, depth + 1))
        if not getattr(body, "quoted", True):
            inners = _body_substitutions(body)
            texts = []
            for inner in inners or ():
                nested = _nested_texts(inner, MAX_DEPTH - depth - 1)
                if nested is None:
                    inners = None
                    break
                texts.extend(nested)
            if inners is None:
                hits.append(max(_scan(body), (1, "", "", "opaque"), key=lambda h: h[0]))
            else:
                # A nested text is graded as deep as it sits, so the deepest are scanned.
                hits.extend(grade_text(text, cwd, depth + 1 + level) for text, level in texts)
        for hit in hits:
            if hit[0] > best[0]:
                best = hit
        if best[0] == 3:
            break
    return best


def _program_family(word):
    """The interpreter family the command word `word` names, as `PROGRAM_FLAGS` and
    `PROGRAM_FILE_OPTIONS` key it, or None."""
    name = word.rpartition("/")[2]
    if name in PROGRAM_FILE_OPTIONS:
        return name
    match = INTERPRETER_RE.match(name)
    return INTERPRETER_FAMILY.get(match.group(1), match.group(1)) if match else None


def _option_files(args, letters, longs, values, optional):
    """The values of the program-file options in `args` as getopt reads them, so `-f x`, `-fx`,
    `-sf x` and `--file=x` all name `x`; one with no value left may take it from `xargs`, which
    reads as standard input. Options after operands count, as GNU tools permute them."""
    out, i = [], 0
    while i < len(args):
        a = args[i]
        i += 1
        if a == "--":
            break
        if a.startswith("--"):
            name, eq, value = a.partition("=")
            if name in longs:
                if not eq:
                    value = args[i] if i < len(args) else "-"
                    i += 1
                out.append(value)
            continue
        if not a.startswith("-") or a == "-":
            continue
        for k in range(1, len(a)):
            letter = a[k]
            if letter in optional:
                break
            if letter in letters or letter in values:
                value = a[k + 1:]
                if not value:
                    value = args[i] if i < len(args) else "-"
                    i += 1
                if letter in letters:
                    out.append(value)
                break
    return out


def _program_source(family, args):
    """Where an interpreter of `family` given `args` reads its program: ("stdin", []),
    ("inline", []) from an option's value or an operand, ("file", [paths]), or ("module",
    [name]) for `python -m`. Awk, sed and make read a file only through their program-file
    option, and make its default makefiles without one; deno and bun an operand; any other the
    first operand unless a program option comes first. A program option with no value may take
    one from `xargs`, so it reads as standard input."""
    if family in PROGRAM_FILE_OPTIONS:
        files = _option_files(args, *PROGRAM_FILE_OPTIONS[family])
        if any(f in STDIN_PATHS for f in files):
            return "stdin", []
        if files:
            return "file", files
        return ("file", list(DEFAULT_MAKEFILES)) if family in MAKES else ("inline", [])
    if family in ("deno", "bun"):
        if any(a in STDIN_PATHS for a in args):
            return "stdin", []
        words = [a for a in args if not a.startswith("-")]
        words = words[1:] if words[:1] == ["run"] else words
        return ("file", words[:1]) if words else ("inline", [])
    evals, values = PROGRAM_FLAGS.get(family, DEFAULT_PROGRAM_FLAGS)
    files = PROGRAM_FILE_VALUES.get(family, set())

    def given(flag, value):
        if value is None or value in STDIN_PATHS:
            return "stdin", []
        if flag in files or flag == "--":
            return "file", [value]
        return ("module", [value]) if (family, flag) == ("python", "-m") else ("inline", [])

    i = 0
    while i < len(args):
        a = args[i]
        after = args[i + 1] if i + 1 < len(args) else None
        if a in STDIN_PATHS:
            return "stdin", []
        if a == "--" or a in evals or a in files:
            return given(a, after)
        if not a.startswith("-"):
            return "file", [a]
        name, eq, value = a.partition("=")
        if eq and (name in evals or name in files):
            return given(name, value)
        if not a.startswith("--"):
            if family in CLUSTERED:
                step = 1
                for k in range(1, len(a)):
                    flag = "-" + a[k]
                    if flag in values:
                        step = 1 if a[k + 1:] else 2
                        break
                    if flag in evals or flag in files:
                        return given(flag, a[k + 1:] or after)
                i += step
                continue
            elif a[:2] in evals or a[:2] in files:
                return given(a[:2], a[2:])
        i += 2 if a in values else 1
    return "stdin", []


def _reads_program(family, args):
    """Whether an interpreter of `family` given `args` reads its program from standard input
    (`_program_source`)."""
    return _program_source(family, args)[0] == "stdin"


def _shell_source(args):
    """Where a shell given `args` reads its commands: ("inline", [text]) from `-c`, ("stdin",
    []) with `-s` or no operand, or ("file", [script]) from its first operand."""
    inline, i = False, 0
    while i < len(args):
        a = args[i]
        if a in ("--", "-"):
            i += 1
            break
        if a.startswith("--"):
            i += 2 if a in ("--rcfile", "--init-file") else 1
            continue
        if len(a) < 2 or a[0] not in "-+":
            break
        inline = inline or (a[0] == "-" and "c" in a)
        if a[0] == "-" and "s" in a and not inline:
            return "stdin", []
        i += 2 if ("o" in a or "O" in a) else 1
    if i >= len(args):
        return ("inline", []) if inline else ("stdin", [])
    return ("inline", [args[i]]) if inline else ("file", [args[i]])


def _run_files(tokens, depth=0):
    """The words of the simple command `tokens` that may name a file it runs: its command word
    past assignments and wrappers, and the script a shell or interpreter reads its program from
    (`_shell_source`, `_program_source`), never the data arguments after it. `python -m x` runs
    `x.py`, and the commands of a shell's `-c` text are read the same way."""
    tokens, _written = _redirects(list(tokens))
    for _ in range(MAX_DEPTH):
        while tokens and ASSIGN_RE.match(tokens[0]):
            tokens = tokens[1:]
        if not tokens:
            return []
        prog = tokens[0].rpartition("/")[2]
        if len(tokens) > 1 and (prog, tokens[1]) in RUNNERS:
            tokens = strip_options(tokens[2:], ())
        elif prog == "env":
            tokens = strip_options(_env_split(tokens[1:]) or [], WRAPPERS[prog])
        elif prog in WRAPPERS:
            tokens = strip_options(tokens[1:], WRAPPERS[prog])
            if prog == "timeout" and tokens:
                tokens = tokens[1:]
        elif prog in ("sudo", "doas", "xargs", "parallel"):
            tokens = strip_options(tokens[1:], SUDO.get(prog, XARGS_VALUE_FLAGS))
        else:
            break
    if not tokens:
        return []
    out, name = [_searched(tokens[0])], tokens[0].rpartition("/")[2].lstrip("=")
    if name in (".", "source"):
        return out + [_searched(w) for w in tokens[1:2]]
    if name in SHELLS:
        kind, words = _shell_source(tokens[1:])
        if kind == "inline" and words and depth < MAX_DEPTH:
            for part in segments(words[0]) or []:
                out += _run_files(part, depth + 1)
        return out + ([_searched(w) for w in words] if kind == "file" else [])
    family = _program_family(tokens[0])
    if family:
        kind, words = _program_source(family, tokens[1:])
        if kind == "file":
            out += words
        elif kind == "module":
            out.append(_Searched(words[0].rpartition(".")[2] + ".py"))
    return out


class _Searched(str):
    """A file name a shell looks up on `PATH`, or a module Python looks up on its path: any file
    of that base name the line wrote may be the one run (`_written_names`)."""


def _searched(word):
    """`word` as `_Searched` when it names no directory, as a command word, a sourced file or a
    shell's script does, since the shell then looks it up on `PATH`."""
    return word if "/" in word else _Searched(word)


def _interprets_input(tokens):
    """Whether the simple command `tokens` may read its standard input as the program of an
    interpreter other than a shell (`_reads_program`), past assignments, wrappers, runners such
    as `uv run` and `xargs`. A command word from a variable or substitution may name one."""
    tokens, _written = _redirects(list(tokens))
    for _ in range(MAX_DEPTH):
        while tokens and ASSIGN_RE.match(tokens[0]):
            tokens = tokens[1:]
        if not tokens:
            return False
        head = tokens[0]
        if PLACEHOLDER in head or "$" in head or "`" in head:
            return True
        family = _program_family(head)
        if family:
            return _reads_program(family, tokens[1:])
        prog = head.rpartition("/")[2]
        if len(tokens) > 1 and (prog, tokens[1]) in RUNNERS:
            tokens = strip_options(tokens[2:], ())
        elif prog == "env":
            rest = _env_split(tokens[1:])
            if rest is None:
                return True
            tokens = strip_options(rest, WRAPPERS[prog])
        elif prog in WRAPPERS:
            tokens = strip_options(tokens[1:], WRAPPERS[prog])
            if prog == "timeout" and tokens:
                tokens = tokens[1:]
        elif prog in ("xargs", "parallel"):
            tokens = strip_options(tokens[1:], XARGS_VALUE_FLAGS)
        else:
            return False
    return True


def _grade_program(text, cwd, depth, skip=None):
    """The grade of `text` read as its program by an interpreter this hook cannot parse, such as
    Python, awk or make: unknown, graded 1, and higher when the text names a command that grades
    higher, since the program may run it. Each line, each quoted string on it and a line's
    strings joined by spaces (`["git", "push"]`), or a statement's when an open bracket carries
    it over several lines (`_statement`), is graded as shell text when it names a program the
    grader knows, `PROGRAM_CHECKS` of them at most, `skip` never, as its caller grades it; the
    whole text is scanned for grade-3 families and a push, which covers what any one line or
    string of it would show a scan. Linear in the length of the text."""
    best = max(_scan_text(text), (1, "", "", "opaque"), key=lambda h: h[0])
    checks, line_end = 0, -1
    for match in _named(text):
        if best[0] == 3:
            break
        if match.start() < line_end:
            continue  # a line already graded
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        line_end = len(text) if line_end < 0 else line_end
        line = text[line_start:line_end]
        strings = _literals(line)
        candidates = [line] + strings + ([" ".join(strings)] if len(strings) > 1 else [])
        before, after = _statement(text, line_start, line_end)
        if before or after:
            candidates.append(" ".join([s for each in before for s in _literals(each)] + strings
                                       + [s for each in after for s in _literals(each)]))
        for candidate in candidates:
            if candidate == skip or next(_named(candidate), None) is None:
                continue
            checks += 1
            if checks > PROGRAM_CHECKS:
                return best
            hit = (_scan_text(candidate) if len(candidate) > SCAN_CAP
                   else grade_text(candidate, cwd, depth + 1))
            if hit[0] > best[0]:
                best = hit
    return best


def _statement(text, start, end):
    """(the lines before, the lines after) the line `text[start:end]` that an open bracket joins
    to it as one statement, as in `run(['git',\\n 'push'])`, looking `STATEMENT_LINES` lines and
    `STATEMENT_CHARS` characters either way. Brackets are counted from the first line looked at,
    strings and comments included: an approximation that costs a bounded read per line."""
    head = text[max(0, start - STATEMENT_CHARS):start].split("\n")[:-1]
    if start > STATEMENT_CHARS:
        head = head[1:]  # a line cut short
    tail = text[end + 1:end + 1 + STATEMENT_CHARS].split("\n") if end < len(text) else []
    if end + 1 + STATEMENT_CHARS < len(text):
        tail = tail[:-1]
    lines = head[-STATEMENT_LINES:] + [text[start:end]] + tail[:STATEMENT_LINES]
    here, depth, opens = len(head[-STATEMENT_LINES:]), 0, []
    for line in lines:
        opens.append(depth)
        depth = max(0, depth + sum(line.count(c) for c in "([{")
                    - sum(line.count(c) for c in ")]}"))
    first = max(i for i in range(here + 1) if not opens[i])
    last = next((i for i in range(here + 1, len(lines)) if not opens[i]), len(lines))
    return lines[first:here], lines[here + 1:last]


def _literals(line):
    """The quoted strings of the one line `line`, as a scan from its start reads them: a string
    opens at a quote and closes at the next quote of its kind that no backslash escapes, and one
    that does not close is passed over. Whether a quote is escaped depends only on the run of
    backslashes right before it, so each close is found by a search rather than by a scan from
    every quote, and the reading is linear in the length of the line."""
    starts, closes = [], {q: [] for q in "'\"`"}
    for match in QUOTE_RE.finditer(line):
        at = before = match.start()
        while before and line[before - 1] == "\\":
            before -= 1
        starts.append(at)
        if not (at - before) % 2:
            closes[line[at]].append(at)
    out, pos = [], 0
    for at in starts:
        if at < pos:
            continue
        ends = closes[line[at]]
        k = bisect.bisect_right(ends, at)
        if k < len(ends):
            out.append(line[at + 1:ends[k]])
            pos = ends[k] + 1
    return out


def _named(text):
    """Each match of `NAMED_RE` in `text` that starts a word, lazily."""
    for match in NAMED_RE.finditer(text):
        if not match.start() or text[match.start() - 1] not in NAME_BEFORE:
            yield match


def _unescape(text):
    """`text` with the backslash escapes `printf` and `echo -e` interpret replaced; a NUL, which
    `xargs -0` splits on, reads as a newline."""
    def one(match):
        code = match.group(1)
        if code[0] in "01234567":
            value = int(code, 8) & 0xFF
        elif code[0] == "x":
            value = int(code[1:], 16)
        else:
            return PRINTF_ESCAPES.get(code, "\\" + code)
        return "\n" if value == 0 else chr(value)
    return PRINTF_ESCAPE_RE.sub(one, text)


def _printf(fmt, args):
    """What `printf fmt args…` writes: each conversion takes the next argument, `%b` with its
    escapes interpreted, with its flags, width and precision, `*` taking them from an argument,
    and the format repeats while arguments remain. `RESHAPED` for a directive this hook does not
    model, a field or text too wide to build, or arguments left after `PROGRAM_CHECKS` repeats."""
    out, size = [], 0
    for _ in range(PROGRAM_CHECKS):
        used, pos = 0, 0
        for match in PRINTF_SPEC_RE.finditer(fmt):
            literal = fmt[pos:match.start()]
            if "%" in literal:
                return RESHAPED
            out.append(_unescape(literal))
            size += len(out[-1])
            pos = match.end()
            if match.group(0) == "%%":
                out.append("%")
                continue
            flags, width, precision, conv = match.groups()
            taken = []
            for part in (width, precision):
                if part == "*":
                    taken.append(args[used] if used < len(args) else "0")
                    used += 1
            value = args[used] if used < len(args) else ""
            used += 1
            field = _printf_field(flags, width, precision, conv, taken, value)
            if field is None:
                return RESHAPED
            out.append(field)
            size += len(field)
            if size > SCAN_CAP:
                return RESHAPED
        if "%" in fmt[pos:]:
            return RESHAPED
        out.append(_unescape(fmt[pos:]))
        size += len(out[-1])
        if size > SCAN_CAP:
            return RESHAPED
        args = args[used:]
        if not used or not args:
            break
    else:
        return RESHAPED
    return "".join(out)


def _printf_field(flags, width, precision, conv, taken, value):
    """One conversion of `printf`, or None when this hook does not model it: `%s`, `%b` and `%c`,
    and a number whose argument reads as one. `taken` holds the `*` width and precision."""
    try:
        if width == "*":
            width = int(taken.pop(0) or 0)
        if precision == "*":
            precision = int(taken.pop(0) or 0)
        width, precision = int(width or 0), None if precision is None else int(precision or 0)
    except ValueError:
        return None
    if width < 0:
        flags, width = flags + "-", -width
    if width > PRINTF_WIDTH or "'" in flags:
        return None
    if conv in "sbc":
        text = _unescape(value) if conv == "b" else value[:1] if conv == "c" else value
        if precision is not None and conv != "c":
            text = text[:precision]
        return text.ljust(width) if "-" in flags else text.rjust(width)
    if conv not in "diouxXeEfFgG" or (precision or 0) > PRINTF_WIDTH:
        return None
    try:
        if conv in "diouxX":
            number = int(value, 8) if re.match(r"^[+-]?0[0-7]+$", value) else int(value or "0", 0)
            if number < 0 and conv in "ouxX":
                return None
        else:
            number = float(value or 0)
    except ValueError:
        return None
    spec = "%" + flags + str(width or "") + ("" if precision is None else "." + str(precision))
    return (spec + ("d" if conv == "u" else conv)) % number


def _output_text(tokens, incoming, written):
    """The text the simple command `tokens` writes to its standard output when this hook can read
    it: an `echo` or `printf`'s words, `builtin` or `command` in front of either, or through a
    `cat` or `tee`, `incoming`, the text on its standard input, and the text this line wrote to
    each file a `cat` names. An `echo` is read with and without its escapes, as shells differ on
    them. None when it cannot be read, `RESHAPED` when `printf` builds it past what is modelled."""
    tokens, _written = _redirects(list(tokens))
    while tokens and ASSIGN_RE.match(tokens[0]):
        tokens = tokens[1:]
    while tokens and tokens[0] in ("builtin", "command"):
        tokens = tokens[1:]
        while tokens and tokens[0] in ("-p", "--"):
            tokens = tokens[1:]
    if not tokens:
        return None
    prog, args = tokens[0].rpartition("/")[2], tokens[1:]
    if prog == "echo":
        while args and re.match(r"^-[neE]+$", args[0]):
            args = args[1:]
        text = " ".join(args)
        plain = _unescape(text)
        return text + "\n" + (plain + "\n" if plain != text else "")
    if prog == "printf":
        if args[:1] == ["--"]:
            args = args[1:]
        if not args or args[0].startswith("-v"):
            return None
        return _printf(args[0], args[1:])
    if prog == "tee":
        return incoming
    if prog == "cat" and not any(a.startswith("-") and a not in ("-", "-u") for a in args):
        files = [a for a in args if a != "-u"] or ["-"]
        return _concat([incoming if a == "-" else _file_text(written, a) for a in files])
    return None


def _concat(texts):
    """`texts` one after another: the text when each is known, `RESHAPED` when each is known or
    reshaped, else None."""
    if all(isinstance(t, str) for t in texts):
        return "".join(texts)
    if all(isinstance(t, str) or t is RESHAPED for t in texts):
        return RESHAPED
    return None


def _wild(word):
    """Whether a variable, substitution or glob in `word` may expand to any file name."""
    return any(c in word for c in "$`*?[") or PLACEHOLDER in word


class _Written(dict):
    """The files a line writes, each by its path as written and normalized, with the texts
    written to it; `names` holds the paths under each base name, and `loose()` whether the line
    may change its directory or move a file, so a path it runs can name a file it wrote under
    another path."""

    def __init__(self, loose):
        super().__init__()
        self.names, self.loose = {}, loose

    def texts(self, path):
        """The texts written to `path`, which is then among the files written."""
        key = os.path.normpath(path)
        if key not in self:
            self[key] = {}
            self.names.setdefault(key.rpartition("/")[2], []).append(key)
        return self[key]


def _written_names(written, word):
    """The paths of the files in `written` the word `word` may name: every one when it is
    `_wild`; each of its base name when it is `_Searched`, the line is `loose()`, or one of the
    two paths starts at `~` or `/` and the other does not; else the same path, `./x` and `x`
    being one."""
    if _wild(word):
        return list(written)
    key = os.path.normpath(word)
    same = written.names.get(key.rpartition("/")[2], [])
    if not same or isinstance(word, _Searched) or written.loose():
        return list(same)
    return [k for k in same
            if k == key or "~" in (k[:1], key[:1]) or (k[:1] == "/") != (key[:1] == "/")]


def _file_text(written, word):
    """What this line wrote to the files `word` may name (`_concat`), or None, as for a `_wild`
    word or a copy of one."""
    names = [] if _wild(word) else _written_names(written, word)
    texts = {t: None for name in names for t in written[name]}
    if not names or ANY_FILE in texts:
        return None
    return _concat(list(texts))


def _grade_input(tokens, incoming, unread, cwd, depth):
    """The grade of `incoming` fed to the standard input of the simple command `tokens`: as the
    commands it holds for a shell, by `_grade_program` for another interpreter, and as the
    arguments of the command an `xargs` runs. Text this hook cannot read (`UNKNOWN`) is graded by
    `unread()`, the line's own text read as a program; text the line spells out and a filter or
    a `printf` directive then reshapes (`RESHAPED`) is graded that way and at least 2, as the
    hook cannot vouch for what it becomes."""
    shell = _runs_input(tokens)
    if shell or _interprets_input(tokens):
        if isinstance(incoming, str):
            return (grade_text(incoming, cwd, depth + 1) if shell
                    else _grade_program(incoming, cwd, depth))
        hit = unread()
        if incoming is RESHAPED and hit[0] < 2:
            clean = _redirects(list(tokens))[0]
            hit = (2, clean[0].rpartition("/")[2] if clean else "", "", "opaque")
        return hit
    clean, _written = _redirects(list(tokens))
    if isinstance(incoming, str) and clean and clean[0].rpartition("/")[2] in ("xargs",
                                                                               "parallel"):
        rest = strip_options(clean[1:], XARGS_VALUE_FLAGS)
        if rest:
            return grade_tokens(rest + incoming.split(), cwd, depth + 1)
    return 0, None, None, None


def _grade_streams(linked, whole, cwd, depth):
    """(the worst grade of the text the simple commands `linked` hand one another, whether a file
    this line writes is then run). Text reaches a command's standard input from a here-string, a
    `<` or a pipe, and `_grade_input` grades it; what flows through a pipe is known from an
    `echo`, a `printf`, or a `cat` or `tee` passing either on or reading a file this line wrote.
    A filter the hook does not model reshapes known text, and a group, loop or other command
    hands on text it cannot read. A file is run when a command word names it, or a shell or
    interpreter is handed it as its script (`_run_files`), as `./x.sh`, `sh x.sh` or `python3
    x.py` are, or reads it on its standard input; the text written to it, a copy's included, is
    then graded as a script and as a program. Files match by path (`_written_names`), by base name
    where the line may change directory or move a file, and the order of the writes is not
    modelled."""
    best, outputs, sourced_out, runs = (0, None, None, None), [], [], []
    unread_hit, executed, memo = [], False, {}

    def run_lists():
        if "runs" not in memo:
            memo["runs"] = [_run_files(tokens) for tokens, _fed in linked]
        return memo["runs"]

    def loose():
        if "loose" not in memo:
            memo["loose"] = bool(DIR_CHANGE_RE.search(whole)) or any(
                w.rpartition("/")[2] in RELOCATORS or _wild(w)
                for words in run_lists() for w in words)
        return memo["loose"]

    written = _Written(loose)

    def unread():
        # Below 2 it adds nothing to the grade each command of the line has of its own.
        if not unread_hit:
            hit = _grade_program(whole, cwd, depth, skip=whole)
            unread_hit.append(hit if hit[0] > 1 else (0, None, None, None))
        return unread_hit[0]

    for tokens, fed in linked:
        incoming, sourced, redirected = None, False, []
        if fed is not None:
            known = fed != COMPOUND and outputs[fed] is not None
            incoming = outputs[fed] if known else UNKNOWN
            sourced = fed != COMPOUND and sourced_out[fed]
        for i, token in enumerate(tokens[:-1]):
            if HERE_STRING_RE.match(token):
                incoming, sourced = tokens[i + 1] + "\n", False
            elif token == "<" and not (i and tokens[i - 1].isdigit() and tokens[i - 1] != "0"):
                text = _file_text(written, tokens[i + 1]) if written else None
                incoming = UNKNOWN if text is None else text
                sourced = bool(_written_names(written, tokens[i + 1]))
                redirected = tokens[i + 1:i + 2]
        clean, targets = _redirects(list(tokens))
        prog = clean[0].rpartition("/")[2] if clean else ""
        if incoming is not None:
            hit = _grade_input(tokens, incoming, unread, cwd, depth)
            if hit[0] > best[0]:
                best = hit
            if sourced and (_runs_input(tokens) or _interprets_input(tokens)):
                executed = True
                runs += redirected
            if isinstance(incoming, str) and prog in ("xargs", "parallel"):
                runs += _run_files(clean + incoming.split())
            elif isinstance(incoming, str) and len(incoming) <= SCAN_CAP and _runs_input(tokens):
                runs += [w for part in segments(incoming) or [] for w in _run_files(part)]
        out = _output_text(tokens, incoming, written)
        if out is None and incoming is not None and incoming is not UNKNOWN:
            out = RESHAPED
        outputs.append(out)
        sourced_out.append(sourced or (prog == "cat" and any(
            _written_names(written, a) for a in clean[1:])))
        text = out
        if prog == "tee":
            targets = targets + operands(clean[1:])
            text = incoming
        for target in targets:
            if target and not target.startswith("/dev/"):
                written.texts(target)[text] = None
        words = operands(clean[1:]) if prog in ("cp", "mv", "ln", "install") else []
        if len(words) > 1 and written and words[-1].rpartition("/")[2]:
            # Texts are kept once per file, and a copy from a `_wild` word stands for any file
            # this line wrote, so a chain of copies cannot compound.
            copy = written.texts(words[-1])
            for word in words[:-1]:
                if _wild(word):
                    copy[ANY_FILE] = None
                else:
                    for name in _written_names(written, word):
                        copy.update(written[name])
    if not written:
        return best, executed
    if any(w.rpartition("/")[2] in RELOCATORS for w in runs):
        memo["loose"] = True
    names = set()
    for word in runs + [w for words in run_lists() for w in words]:
        names.update(_written_names(written, word))
    texts = {t: None for name in names for t in written[name]}
    if ANY_FILE in texts:
        texts = {t: None for each in written.values() for t in each}
    for text in texts:
        executed = True
        if text is ANY_FILE:
            continue
        if isinstance(text, str):
            hits = (grade_text(text, cwd, depth + 1), _grade_program(text, cwd, depth))
        elif text is RESHAPED:
            hits = ((2, "a reshaped script", "", "opaque"), unread())
        else:
            hits = (unread(),)
        for hit in hits:
            if hit[0] > best[0]:
                best = hit
    return best, executed


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


# A tilde next to a quote, a backslash or a `$`, or any ANSI-C string, anywhere in the line: a `cd`
# operand the tokenizer returns as `~/x` may have been written `"~/x"`, `~"/x"`, `~$''/x` or
# `$'\x7e/x'`, which bash does not expand to the home directory, so no `~` operand of the line
# resolves.
QUOTED_TILDE_RE = re.compile(r"[\\'\"]~|~[\\'\"$]|\$'")


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
    the static assignment model, and so is an ANSI-C `$'…'` string, which this reader does not
    follow. Returning None disables assignment resolution for the line.
    """
    if "$'" in text:
        return None
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
    """Shell words as written, quotes included; None when an operator or an ANSI-C `$'…'`
    string is present."""
    if "$'" in text:
        return None
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
        if quote != "'" and text.startswith("$$", i):
            out.append("$$")
            i += 2
            continue
        if not quote and text.startswith("$'", i):
            end = ro._ansi_end(text, i) or len(text)
            out.append("$'" + "".join("_" if ch in ro.OPERATOR_CHARS else ch
                                      for ch in text[i + 2:end]))
            i = end
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
                # A literal relative operand under an unknown directory is not the cause, and it
                # cannot restore the directory an earlier operand left unknown, so that earlier
                # operand stays the cause.
                static = operand is not None and _static_dir(operand, "/") is not None
                if cwd is not None:
                    cause = None
                elif not static:
                    cause = args[i + 1]
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
        texts = [_outer(cmd)[0]]  # bodies removed where placed; `_scan(cmd)` reads them all
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
            elif args and args[0].startswith("~") and QUOTED_TILDE_RE.search(stripped):
                here = None  # bash keeps a quoted tilde-prefix literal; zsh expands `~"/x"`
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

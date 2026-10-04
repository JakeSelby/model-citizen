# SPDX-License-Identifier: MIT
"""The Bash command grader: the 0-3 grade, its reason line and the governance walk.

A library, not a hook: it has no id and no switch. `grade-bash.py` gates on what it answers and
re-exports every name here, so its docstring documents the behaviour. The lifecycle's read-only
allow loads this module alone, so switching `grade-bash` off takes that hook out of the Bash path.
"""
import bisect
import fnmatch
import importlib.util
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
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
# Wrappers read only by `_unwrap`, kept out of `NAMED_PROGRAMS` so an interpreter's program that
# says `script` or `arch` is not read as shell text. `arch` runs its command operand, if any.
MORE_WRAPPERS = {
    "builtin": (),
    "caffeinate": ("-t", "-w"),
    "setsid": (),
    "flock": ("-w", "--timeout", "-E", "--conflict-exit-code"),
    "script": ("-B", "-I", "-O", "-T", "-E", "-m", "--log-io", "--log-in", "--log-out",
               "--log-timing", "--echo", "--logging-format"),
    "sandbox-exec": ("-f", "-n", "-p", "-D"),
    "chrt": ("-T", "-P", "-D", "--sched-runtime", "--sched-period", "--sched-deadline"),
    "ionice": ("-c", "-n", "-p", "-P", "-u", "--class", "--classdata", "--pid", "--pgid", "--uid"),
    "taskset": (),
    "unbuffer": (),
    "firejail": (),
    "arch": ("-arch", "-d", "-e"),
    "bunx": ("--package", "-p"),
}
WRAPPERS.update(MORE_WRAPPERS)
# Every option each of these wrappers takes, GNU's and BSD's: (short letters that take a value,
# short letters that take none, long options that take a value, long options that take none).
# An option outside its entry makes the command it wraps unknown (`_unwrap`).
WRAPPER_OPTIONS = {
    "timeout": ("ks", "fpv", ("--kill-after", "--signal"),
                ("--foreground", "--preserve-status", "--verbose")),
    "time": ("fo", "ahlpqv", ("--format", "--output"),
             ("--append", "--portability", "--quiet", "--verbose")),
    "nice": ("n", "", ("--adjustment",), ()),
    "nohup": ("", "", (), ()),
    "stdbuf": ("eio", "", ("--error", "--input", "--output"), ()),
    "command": ("", "pvV", (), ()),
    "exec": ("a", "cl", (), ()),
    "noglob": ("", "", (), ()),
    "env": ("CLPSUu", "0iv", ("--chdir", "--split-string", "--unset"),
            ("--ignore-environment", "--null", "--debug", "--list-signal-handling",
             "--block-signal", "--default-signal", "--ignore-signal")),
    "builtin": ("", "", (), ()),
    "caffeinate": ("tw", "dimsu", (), ()),
    "setsid": ("", "cfw", (), ("--ctty", "--fork", "--wait")),
    "flock": ("wEc", "sxenuoFv", ("--timeout", "--conflict-exit-code", "--command"),
              ("--shared", "--exclusive", "--nonblock", "--nb", "--unlock", "--close",
               "--no-fork", "--verbose")),
    "script": ("BIOTEmct", "aeFkqrfdp", ("--log-io", "--log-in", "--log-out", "--log-timing",
                                         "--echo", "--logging-format", "--command", "--timing"),
               ("--append", "--return", "--flush", "--force", "--quiet")),
    "sandbox-exec": ("fnpD", "", (), ()),
    "chrt": ("TPD", "abdfiormRpv", ("--sched-runtime", "--sched-period", "--sched-deadline"),
             ("--all-tasks", "--batch", "--deadline", "--fifo", "--idle", "--other", "--rr",
              "--reset-on-fork", "--pid", "--verbose", "--max")),
    "ionice": ("cnpPu", "t", ("--class", "--classdata", "--pid", "--pgid", "--uid"),
               ("--ignore",)),
    "taskset": ("", "apc", (), ("--all-tasks", "--pid", "--cpu-list")),
    "unbuffer": ("", "p", (), ()),
}
# Options whose value is a file the wrapper itself writes: `time -o f ls` writes f, so a
# governed file named there is decided as a redirect to it would be. `time` the reserved word
# takes no `-o`, so a bare leading `time` writes nothing (`_peel`).
WRAPPER_WRITES = {
    "time": {"-o", "--output"},
    "script": {"-B", "-I", "-O", "-T", "-t", "--log-io", "--log-in", "--log-out",
               "--log-timing", "--timing"},
    "firejail": {"--output", "--output-stderr", "--trace"},
}
# Wrappers that take operands before the command: (how many, whether the first is a file the
# wrapper creates or writes). `flock` creates its lock file; `script` writes its typescript.
WRAPPER_OPERANDS = {"flock": (1, True), "script": (1, True), "chrt": (1, False),
                    "taskset": (1, False)}
# Options after which a wrapper runs no command of its own: `chrt -p 5 123`, `ionice -p 123`.
WRAPPER_NO_COMMAND = {"chrt": {"p", "--pid", "m", "--max"}, "ionice": {"p", "P", "u", "--pid",
                      "--pgid", "--uid"}, "taskset": {"p", "--pid"}, "script": {"p"}}
NICE_LEGACY_RE = re.compile(r"^-[-+]?\d+$")
# Runners that execute the rest of the line in a managed environment, like `npx`.
RUNNERS = {("bundle", "exec"), ("poetry", "run"), ("uv", "run"), ("pipx", "run"),
           ("pnpm", "dlx"), ("pnpm", "exec"), ("yarn", "dlx"), ("yarn", "exec"),
           ("npm", "exec"), ("rye", "run"), ("hatch", "run"), ("pipenv", "run"),
           ("mise", "exec"), ("mise", "x"), ("asdf", "exec"), ("direnv", "exec")}
SUDO = {"sudo": ("-u", "-g", "-U", "--user", "--group", "-p", "--prompt"),
        "doas": ("-u", "-C"),
        "pkexec": ("--user",),
        "runuser": ("-u", "-g", "-G", "--user", "--group", "--supp-group", "-s", "--shell",
                    "-w", "--whitelist-environment"),
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
UNREADABLE = (3, "command the grader could not read", "", "opaque")
PREFIX_CHAIN = (3, "command behind too many prefixes to grade", "", "opaque")
_AGAIN = object()  # `_grade_step`: look through to the tokens that follow
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


def _peel(tokens, keyword_time=False):
    """(the command a run of wrappers ends in, the first thing `_unwrap` could not read or None,
    whether it may run in another directory, the files the wrappers themselves write, whether
    the run is longer than `ro.MAX_PREFIXES`, the cap `grade_tokens` keeps). Iterative, so no
    run of wrappers can exhaust the stack; past the cap the command is unknown. With
    `keyword_time`, a leading bare `time` is bash's reserved word, which takes no `-o`."""
    tokens, unread, moved, written = list(tokens), None, False, []
    for hop in range(ro.MAX_PREFIXES + 1):
        while tokens and ASSIGN_RE.match(tokens[0]):
            tokens = tokens[1:]
        prog = tokens[0].rpartition("/")[2] if tokens else ""
        if prog not in WRAPPERS:
            return tokens, unread, moved, written, False
        if hop == ro.MAX_PREFIXES:
            return tokens, unread or "%d wrappers" % hop, moved, written, True
        keyword = keyword_time and hop == 0 and tokens[0] == "time"
        tokens, why, chdir, wrote = _unwrap(prog, tokens[1:], keyword)
        unread, moved = unread or why, moved or chdir
        written.extend(wrote)
    return tokens, unread, moved, written, True


def _split_text(text, what):
    """The words of a command string a wrapper hands to a shell or splits itself, and what of
    it this hook could not read or None: a quote, a backslash, a `$`, a `#` (a comment to BSD
    env and to a shell) or a shell operator makes the string unread."""
    if any(c in text for c in "\\'\"$#`;&|<>(){}*?[~\n"):
        try:
            return shlex.split(text), what
        except ValueError:
            return text.split(), what
    return text.split(), None


def _unwrap(prog, args, keyword=False):
    """(the command a wrapper in `WRAPPERS` runs, what of it this hook could not read or None,
    whether it may run in another directory, the files the wrapper itself writes). Options are
    read from `WRAPPER_OPTIONS`; an option not listed there, or an `env -S`, `flock -c` or
    `script -c` string `_split_text` cannot read, is named as unread, and the command returned
    is then only the likeliest reading. `env` takes every word holding a `=` as an assignment,
    whatever its name, so `env 'X%=1' rm -rf /` runs the `rm`. A file named by an option in
    `WRAPPER_WRITES` or an operand in `WRAPPER_OPERANDS` is written; the bare reserved word
    `time` (`keyword`) writes none."""
    if prog in ("firejail", "arch"):
        return _unwrap_words(prog, args)
    if prog not in WRAPPER_OPTIONS:  # `npx`, `uvx`, `bunx`: past options and assignments
        rest = strip_options(args, WRAPPERS[prog])
        while rest and ASSIGN_RE.match(rest[0]):
            rest = rest[1:]
        return rest, None, False, []
    shorts, flags, longs, long_flags = WRAPPER_OPTIONS[prog]
    writes = set() if keyword else WRAPPER_WRITES.get(prog, set())
    idle = WRAPPER_NO_COMMAND.get(prog, set())
    args, unread, moved, i, written, text, quiet = list(args), None, False, 0, [], None, False
    while i < len(args):
        a = args[i]
        if a == "--":
            i += 1
            break
        if prog == "env" and a == "-":  # the same as `-i`
            i += 1
            continue
        if not a.startswith("-") or a == "-":
            break
        if prog == "nice" and NICE_LEGACY_RE.match(a):
            i += 1
            continue
        split = None
        if a.startswith("--"):
            name, eq, value = a.partition("=")
            if name in longs:
                if not eq:
                    value, i = (args[i + 1] if i + 1 < len(args) else ""), i + 1
                if name == "--split-string":
                    split = value
                elif name == "--command":
                    text = value
                elif name in writes:
                    written.append(value)
                moved = moved or name == "--chdir"
            elif name not in long_flags:
                unread = unread or "%s %s" % (prog, name)
            quiet = quiet or name in idle
            i += 1
        else:
            for j, letter in enumerate(a[1:]):
                quiet = quiet or letter in idle
                if letter in shorts:
                    value = a[j + 2:]
                    if prog == "script" and letter == "t" and not value:
                        # GNU's `-t[file]` takes no separate value; BSD's `-t time` does.
                        nxt = args[i + 1] if i + 1 < len(args) else ""
                        value, i = (nxt, i + 1) if nxt.isdigit() else ("", i)
                    elif not value:
                        value, i = (args[i + 1] if i + 1 < len(args) else ""), i + 1
                    if letter == "S" and prog == "env":
                        split = value
                    elif letter == "c" and prog in ("flock", "script"):
                        text = value
                    elif "-" + letter in writes and value and not value.isdigit():
                        written.append(value)
                    moved = moved or (letter == "C" and prog == "env")
                    break
                if letter not in flags:
                    unread = unread or "%s -%s" % (prog, letter)
                    break
            i += 1
        if split is not None and prog == "env":
            # GNU and BSD env read quotes, backslash escapes (`\c` ends the string), `${…}` and,
            # to BSD, a `#` starting a comment, so env, not this hook, decides what then runs.
            if any(c in split for c in "\\'\"$#"):
                unread = unread or "env -S"
                try:
                    words = shlex.split(split)
                except ValueError:
                    words = split.split()
            else:
                words = split.split()
            args, i = words + args[i:], 0
    rest = args[i:]
    if prog == "env":
        while rest and "=" in rest[0]:
            rest = rest[1:]
    elif prog == "timeout":
        rest = rest[1:]  # the duration
    elif prog in WRAPPER_OPERANDS:
        count, writes_first = WRAPPER_OPERANDS[prog]
        if writes_first and rest[:1] and not (prog == "flock" and rest[0].isdigit()):
            written.append(rest[0])
        rest = rest[count:]
        if prog == "flock" and rest[:1] in (["-c"], ["--command"]):
            text, rest = (rest[1] if len(rest) > 1 else ""), []
    if text is not None:
        rest, why = _split_text(text, "%s -c" % prog)
        unread = unread or why
    if quiet:
        rest = []
    return rest, unread, moved, [w for w in written if w and w != "/dev/null"]


def _unwrap_words(prog, args):
    """`_unwrap` for `firejail`, whose every option is one word (`--net=none`), and `arch`,
    whose options name architectures (`-arm64`) and of which only `-arch`, `-d` and `-e` take
    the next word."""
    i, written = 0, []
    while i < len(args):
        a = args[i]
        if a == "--":
            i += 1
            break
        if not a.startswith("-") or a == "-":
            break
        if prog == "arch" and a in WRAPPERS["arch"]:
            i += 1
        elif prog == "firejail" and a.partition("=")[0] in WRAPPER_WRITES["firejail"]:
            written.append(a.partition("=")[2])
        i += 1
    return args[i:], None, False, [w for w in written if w and w != "/dev/null"]


def _runner_readings(rest):
    """Each way the words after a runner such as `uv run` may begin the command it runs. Its
    options are not modelled, so each one without a `=` may or may not take the next word."""
    starts, seen, readings = [0], set(), []
    while starts:
        p = starts.pop()
        if p in seen or p >= len(rest):
            continue
        seen.add(p)
        word = rest[p]
        if ASSIGN_RE.match(word) or (word.startswith("-") and "=" in word):
            starts.append(p + 1)
        elif word.startswith("-") and word != "-":
            starts.extend((p + 1, p + 2))
        else:
            readings.append(rest[p:])
    return sorted(readings, key=len, reverse=True)


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


def _git_sub(args, cwd=""):
    """(the subcommand, its arguments, the directory `-C` moves to) of `git` given `args`."""
    i, where = 0, cwd
    while i < len(args):
        a = args[i]
        if a in GIT_VALUE_GLOBALS and i + 1 < len(args):
            if a == "-C":
                where = os.path.join(where or "", _expand(args[i + 1]))
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        break
    rest = args[i:]
    return (rest[0], rest[1:], where) if rest else ("", [], where)


# A `git checkout` operand that can only be a pathspec, never a branch: a ref name holds none of
# these characters, so `git checkout '*.py'` restores files, as `git checkout -- '*.py'` does.
PATHSPEC_RE = re.compile(r"[*?\[]|^:")


def _checkout_paths(flags, ops, where):
    """Whether `git checkout` given `flags` overwrites files rather than only switching branch:
    a tree-ish followed by paths, a pathspec only a path can be, or an operand that exists in
    the working tree. One that creates a branch (`-b`, `--orphan`) takes a start point, not
    paths; `-B` is graded before this, since it resets a branch that exists."""
    if "--orphan" in flags or "-b" in flags or "-B" in flags:
        return False
    if len(ops) > 1 or any(PATHSPEC_RE.search(op) for op in ops):
        return True
    if not ops or not where or not os.path.isabs(where):
        return False
    return os.path.lexists(os.path.join(where, _expand(ops[0])))


# Git's own commands. Any other word is an alias or a `git-<name>` program on PATH.
GIT_COMMANDS = frozenset((
    "add am annotate apply archive bisect blame branch bundle cat-file check-attr check-ignore "
    "check-mailmap check-ref-format checkout checkout-index cherry cherry-pick citool clean clone "
    "column commit commit-graph commit-tree config count-objects credential daemon describe diff "
    "diff-files diff-index diff-tree difftool fast-export fast-import fetch fetch-pack "
    "filter-branch fmt-merge-msg for-each-ref for-each-repo format-patch fsck gc "
    "get-tar-commit-id grep gui hash-object help hook http-backend index-pack init instaweb "
    "interpret-trailers log ls-files ls-remote ls-tree mailinfo mailsplit maintenance merge "
    "merge-base merge-file merge-index merge-tree mktag mktree multi-pack-index mv name-rev notes "
    "pack-objects pack-redundant pack-refs patch-id prune prune-packed pull push range-diff "
    "read-tree rebase reflog refs remote repack replace rerere reset restore rev-list rev-parse "
    "revert rm send-email send-pack shortlog show show-branch show-index show-ref sparse-checkout "
    "stash status stripspace submodule switch symbolic-ref tag unpack-file unpack-objects "
    "update-index update-ref update-server-info var verify-commit verify-pack verify-tag version "
    "whatchanged worktree write-tree lfs filter-repo").split())
# Per subcommand, the short letters and the long options whose value is the next word unless it
# is joined: `git push -o -n` hands `-n` to `-o`, and `git clean -e -n` to `-e`, so neither is a
# dry run. A long option abbreviated to a prefix of one of these takes a value as well.
GIT_VALUE_OPTIONS = {
    "push": ("o", ("--push-option", "--repo", "--receive-pack", "--exec")),
    "clean": ("e", ("--exclude",)),
    "checkout": ("bB", ("--conflict", "--pathspec-from-file")),
    "switch": ("cC", ("--create", "--force-create", "--conflict")),
    "branch": ("u", ("--set-upstream-to", "--contains", "--no-contains", "--merged",
                     "--no-merged", "--points-at", "--sort", "--format")),
    "restore": ("s", ("--source", "--pathspec-from-file", "--conflict")),
    "reset": ("", ("--pathspec-from-file",)),
    "rm": ("", ("--pathspec-from-file",)),
    "stash": ("m", ("--message", "--pathspec-from-file")),
    "update-ref": ("m", ()),
    "read-tree": ("", ("--prefix", "--index-output")),
    "worktree": ("bB", ("--reason",)),
    "apply": ("pC", ("--exclude", "--include", "--directory", "--whitespace",
                     "--build-fake-ancestor")),
    "config": ("f", ("--file", "--blob", "--type", "--default", "--comment", "--value")),
}
# Configuration that makes git run a program, reach another working tree, or change what a
# command it governs destroys: set with `-c` on a command, or written with `git config`, the
# command it governs can no longer be graded by its words. Matched on the lower-cased key.
GIT_RISKY_CONFIG_RE = re.compile(
    r"^(?:core\.(?:fsmonitor|hookspath|sshcommand|pager|editor|askpass|gitproxy|worktree|bare|"
    r"alternaterefscommand)|sequence\.editor|diff\.external|diff\..+\.(?:textconv|command)|"
    r"filter\..+|merge\..+\.driver|credential\..*|gpg\..*|pager\..+|include\..+|includeif\..+|"
    r"uploadpack\..+|remote\..+\.(?:mirror|push|uploadpack|receivepack|vcs)|"
    r"clean\.requireforce|interactive\.difffilter|init\.templatedir|ssh\.variant)$")
# Configuration put in from the environment, which `_git` cannot read from the words.
GIT_CONFIG_ENV_RE = re.compile(
    r"(?<![A-Za-z0-9_])GIT_CONFIG(?:_PARAMETERS|_COUNT|_KEY_\d+|_VALUE_\d+|_GLOBAL|_SYSTEM)?=")
GIT_ALIASES = 4
# Whole option names that are also the start of longer ones: git reads each as itself.
GIT_WHOLE_OPTIONS = frozenset(("--force", "--get", "--unset", "--delete", "--merge", "--patch"))
# The command line `grade_text` is grading, for what one command of it must see of the others.
_LINE = [""]


def _git_options(sub, sargs):
    """(the options of `git <sub>` given `sargs`, its operands), read as git's option parser
    reads them: a short cluster splits into its letters, an option that takes a value takes the
    rest of its cluster or the next word, and a long option is kept by its name without the
    value. Everything after `--` is an operand, and `--` itself is kept among the options."""
    shorts, longs = GIT_VALUE_OPTIONS.get(sub, ("", ()))
    flags, ops, i, done = set(), [], 0, False
    while i < len(sargs):
        a = sargs[i]
        i += 1
        if done or a == "-" or not a.startswith("-"):
            ops.append(a)
            continue
        if a == "--":
            done = True
            flags.add("--")
            continue
        if a.startswith("--"):
            name, eq, _value = a.partition("=")
            flags.add(name)
            if not eq and any(n == name or (len(name) > 3 and n.startswith(name)) for n in longs):
                i += 1
            continue
        for k in range(1, len(a)):
            flags.add("-" + a[k])
            if a[k] in shorts:
                if k == len(a) - 1:
                    i += 1
                break
    return flags, ops


def _on(flags, *names):
    """Whether `flags` hold any of `names`, a long one under any prefix git may expand to it:
    `--for` is `--force` and `--mirr` is `--mirror`. Only for an option that destroys, since
    reading a prefix as it errs toward grading higher; a safe option must be written whole."""
    for flag in flags:
        for name in names:
            if flag == name or (name.startswith("--") and flag.startswith("--") and len(flag) > 3
                                and name.startswith(flag) and flag not in GIT_WHOLE_OPTIONS):
                return True
    return False


def _git_configs(args):
    """The `(key, value)` pairs `-c` and `--config-env` set before git's subcommand; the value of
    one taken from the environment is None."""
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if a == "-c" and i + 1 < len(args):
            key, eq, value = args[i + 1].partition("=")
            out.append((key, value if eq else "true"))
            i += 2
            continue
        if a == "--config-env" and i + 1 < len(args):
            out.append((args[i + 1].partition("=")[0], None))
            i += 2
            continue
        if a.startswith("--config-env="):
            out.append((a[len("--config-env="):].partition("=")[0], None))
            i += 1
            continue
        if a in GIT_VALUE_GLOBALS:
            i += 2
            continue
        if not a.startswith("-"):
            break
        i += 1
    return out


def _git_alias(args, sub, sargs, configs, where, cwd, aliases):
    """The grade of `git <sub>` when `sub` is no command of git's own: the command its alias
    expands to, set by `-c alias.<sub>` or found in git's configuration, graded in its place; a
    `!` alias graded as the shell line it runs. 3 when the alias cannot be known: one this line
    sets or may set, a lookup that did not answer, or a chain of aliases deeper than
    `GIT_ALIASES`. None when `sub` is no alias, and so a `git-<sub>` program."""
    unknowable = (3, "git " + sub, "an alias the grader cannot read", "opaque")
    name = "alias." + sub.lower()
    value = next((v for k, v in reversed(configs) if k.lower() == name), False)
    if value is False:
        if re.search(r"alias\.", _LINE[0], re.I) or GIT_CONFIG_ENV_RE.search(_LINE[0]):
            return unknowable  # this line may define the alias before it runs
        done = _git_call(["config", "--get", name], where if where and os.path.isdir(where)
                         else "")
        if done is None or done.returncode not in (0, 1):
            return unknowable
        if done.returncode == 1:
            return None
        value = done.stdout.decode("utf-8", "replace").strip()
    if value is None or aliases >= GIT_ALIASES:
        return unknowable
    if value.startswith("!"):
        line = value[1:] + "".join(" " + shlex.quote(a) for a in sargs)
        hit = grade_text(line, where or cwd, 1)
        return hit if hit[0] > 1 else (1, "git " + sub, value[1:], None)
    try:
        words = shlex.split(value)
    except ValueError:
        return unknowable
    if not words:
        return unknowable
    at = len(args) - len(sargs) - 1
    return _git(args[:at] + words + list(sargs), cwd, aliases + 1)


def _config_write(flags, ops):
    """The key `git config` given `flags` and `ops` sets, or None when it only reads, unsets or
    edits a file the user opens."""
    if _on(flags, "--get", "--get-all", "--get-regexp", "--get-urlmatch", "--get-color",
           "--get-colorbool", "--list", "--unset", "--unset-all", "--remove-section",
           "--rename-section", "--edit") or {"-l", "-e"} & flags:
        return None
    if ops[:1] == ["set"]:
        return ops[1] if len(ops) > 1 else None
    if ops[:1] and ops[0] in ("get", "list", "unset", "remove-section", "rename-section", "edit"):
        return None
    return ops[0] if len(ops) > 1 else None


def _git(args, cwd, aliases=0):
    sub, sargs, where = _git_sub(args, cwd)
    if not sub:
        return 1, "git", "", None
    configs = _git_configs(args)
    for key, _value in configs:
        if GIT_RISKY_CONFIG_RE.match(key.lower()):
            return 3, "git -c " + key, sub, "opaque"
    if GIT_CONFIG_ENV_RE.search(_LINE[0]):
        return 3, "git with configuration from the environment", sub, "opaque"
    if sub.lower() not in GIT_COMMANDS:
        hit = _git_alias(args, sub, sargs, configs, where, cwd, aliases)
        if hit is not None:
            return hit
        return 1, "git " + sub, _joined(sargs, 1), None
    flags, ops = _git_options(sub, sargs)
    # Only a dry-run option in position counts: a value an option took is no option.
    dry_run = "--dry-run" in flags or "-n" in flags
    target = " ".join(ops[:1])
    if sub == "push":
        if dry_run:
            return 1, "git push --dry-run", " ".join(ops), None
        if _on(flags, "--force-with-lease"):
            return 3, "git push --force-with-lease", " ".join(ops), "git-history"
        if _on(flags, "--force", "--mirror") or "-f" in flags:
            verb = "git push --force" if _on(flags, "--force") else (
                "git push --mirror" if _on(flags, "--mirror") else "git push -f")
            return 3, verb, " ".join(ops), "git-history"
        if _on(flags, "--delete", "--prune") or "-d" in flags or any(
                o.startswith(":") for o in ops):
            verb = "git push --prune" if _on(flags, "--prune") else "git push --delete"
            return 3, verb, " ".join(ops), "git-history"
        if any(o.startswith("+") for o in ops):
            return 3, "git push", " ".join(ops), "git-history"
        return 2, "git push", " ".join(ops), "remote"
    # Every route that overwrites the working tree or the index grades alike: what was changed
    # and not committed is gone, whichever command replaced it. A reset that keeps the index
    # (`--soft`) or refuses over local changes (`--keep`) is not one.
    if sub == "reset" and _on(flags, "--hard"):
        return 3, "git reset --hard", target, "git-discard"
    if sub == "reset" and not ({"--soft", "--keep"} & flags):
        verb = "git reset --merge" if _on(flags, "--merge") else "git reset"
        return 3, verb, target, "git-discard"
    # `clean.requireForce` set false, in a file or with `-c`, lets a bare `git clean` delete, so
    # any `git clean` but a dry run grades as the forced one does.
    if sub == "clean" and not dry_run:
        return 3, "git clean -f", target, "git-discard"
    if sub == "checkout":
        if "--" in flags:
            return 3, "git checkout --", target, "git-discard"
        if _on(flags, "--force") or "-f" in flags:
            return 3, "git checkout -f", target, "git-discard"
        if "-B" in flags:
            return 3, "git checkout -B", target, "git-discard"
        if _on(flags, "--pathspec-from-file"):
            return 3, "git checkout --pathspec-from-file", target, "git-discard"
        if ops[:1] in (["."], ["./"]):
            return 3, "git checkout", ops[0], "git-discard"
        if _on(flags, "--ours", "--theirs", "--patch") or "-p" in flags:
            return 3, "git checkout", target, "git-discard"
        if _checkout_paths(flags, ops, where):
            return 3, "git checkout", target, "git-discard"
    if sub == "switch":
        if _on(flags, "--discard-changes", "--force") or "-f" in flags:
            return 3, "git switch --discard-changes", target, "git-discard"
        if "-C" in flags or _on(flags, "--force-create"):
            return 3, "git switch -C", target, "git-discard"
    if sub == "restore":
        staged = _on(flags, "--staged") or "-S" in flags
        return 3, "git restore --staged" if staged else "git restore", target, "git-discard"
    if sub == "read-tree" and not dry_run:
        return 3, "git read-tree", target, "git-discard"
    if sub == "checkout-index" and (_on(flags, "--force") or "-f" in flags):
        return 3, "git checkout-index -f", target, "git-discard"
    if sub == "rm" and (_on(flags, "--force") or "-f" in flags) and not dry_run:
        return 3, "git rm -f", target, "git-discard"
    if sub == "worktree" and ops[:1] == ["remove"] and (_on(flags, "--force") or "-f" in flags):
        return 3, "git worktree remove --force", " ".join(ops[1:2]), "git-discard"
    if sub == "branch":
        if _on(flags, "--delete") or "-D" in flags:
            return 3, "git branch -D", target, "git-discard"
        if "-f" in flags or _on(flags, "--force") or "-M" in flags or "-C" in flags:
            return 3, "git branch -f", target, "git-discard"
    if sub == "update-ref":
        verb = "git update-ref -d" if "-d" in flags else "git update-ref"
        return 3, verb, target, "git-discard"
    if sub == "apply" and ("-R" in flags or _on(flags, "--reverse")):
        return 3, "git apply -R", target, "git-discard"
    if sub == "stash" and ops and ops[0] in ("drop", "clear"):
        return 3, "git stash " + ops[0], " ".join(ops[1:2]), "git-discard"
    if sub == "reflog" and ops and ops[0] in ("expire", "delete"):
        return 3, "git reflog " + ops[0], " ".join(ops[1:2]), "git-history"
    if sub in ("filter-branch", "filter-repo"):
        return 3, "git " + sub, target, "git-history"
    if sub == "config":
        key = _config_write(flags, ops)
        if key and GIT_RISKY_CONFIG_RE.match(key.lower()):
            return 3, "git config " + key, "", "opaque"
    return 1, "git " + sub, target, None


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
    flagged = short(args, "rRf") or has(args, "--recursive", "--force")
    verb = ("rm -rf" if short(args, "rR") or has(args, "--recursive") else "rm -f") if flagged else "rm"
    if flagged:
        for op in operands(args):
            if _rm_risky(op, cwd):
                return 3, verb, op, "delete"
    return (_discards(verb, operands(args), cwd, deletes=True)
            or (1, verb, _joined(args, 1), None))


# How long the grader waits for one `git status` to say whether a file holds uncommitted work,
# how long all of a command line's calls to git may take together, and how many it may make;
# `grade_text` clears the answers for each line. Past any of them the answer is "it may": a
# destructive verb the grader cannot check fails closed. The total stays well inside
# `GRADE_SECONDS`, so the deadline is never what stops a check.
GIT_STATUS_SECONDS = 1.5
GIT_STATUS_TOTAL = 2.0
GIT_STATUS_CALLS = 16
_DIRTY = {}
# [seconds spent, calls made] asking git on this command line.
_GIT_SPENT = [0.0, 0]
# The directories a `cd` earlier on the line may have moved to, and whether one went where
# the grader cannot say; `grade_text` clears both for each line.
_CDS = []
_CD_LOST = [False]
UNCHECKED = "which git status did not answer for in time"
UNKNOWN_PATH = "a path the grader cannot know"
HIDDEN = "which git is told not to check (assume-unchanged or skip-worktree)"


class _Unknown(str):
    """The answer of `_dirty_tracked` when it cannot say: the text says why."""


def _git_call(argv, where):
    """The finished `git` run of `argv` in `where` (the process's own directory when ""), or
    None when this line's budget of calls or seconds is spent or the run did not finish in time.
    No `git` to run reads as a repository-less answer, exit 128."""
    remaining = GIT_STATUS_TOTAL - _GIT_SPENT[0]
    if _GIT_SPENT[1] >= GIT_STATUS_CALLS or remaining <= 0.05:
        return None
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", LC_ALL="C")
    started = time.monotonic()
    try:
        return subprocess.run(["git"] + (["-C", where] if where else []) + argv,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              stdin=subprocess.DEVNULL, timeout=min(GIT_STATUS_SECONDS, remaining),
                              env=env)
    except subprocess.TimeoutExpired:
        return None
    except OSError:
        return subprocess.CompletedProcess(argv, 128, b"", b"not a git repository")
    finally:
        _GIT_SPENT[0] += time.monotonic() - started
        _GIT_SPENT[1] += 1


def _unknowable(path):
    """Whether the word `path` names a file the grader cannot know: a variable, a substitution
    or a backtick, but not `$HOME` or `$TMPDIR`, which `_expand` resolves."""
    path = re.sub(r"\$(?:\{(?:HOME|TMPDIR)\}|(?:HOME|TMPDIR)(?![A-Za-z0-9_]))", "", path)
    return "$" in path or "`" in path or PLACEHOLDER in path


def _dirty_tracked(paths, cwd, deletes=False):
    """The first of `paths` (pathspecs, relative to `cwd`) whose loss git cannot undo: a tracked
    file with working-tree changes not in the index, and when `deletes`, an untracked file that
    is not ignored, as `git clean -f` would take. "" when there is none or no repository to ask.

    Each path is asked about in its own directory, so a path outside the repository costs
    nothing for the paths inside it, and relative to every directory a `cd` earlier on the line
    may have moved to. Unknown, an `_Unknown` naming why, when a `cd` went where the grader
    cannot say; when git is told not to check a file (assume-unchanged, skip-worktree); and,
    for a delete, when the path itself is a variable or a substitution. None when `git` did not
    answer in time or this line's budget (`_git_call`) is spent."""
    words = [p for p in paths if p]
    # A variable may hold `../`, so even `/tmp/$x` can name any file.
    if deletes and any(_unknowable(p) for p in words):
        return _Unknown(UNKNOWN_PATH)
    specs = [p for p in words if not _unknowable(p)]
    if not specs:
        return ""
    bases = [d for d in [cwd] + _CDS if d and os.path.isabs(d)]
    if deletes and _CD_LOST[0] and any(not _expand(p).startswith("/") for p in specs):
        return _Unknown(UNKNOWN_PATH)
    groups = {}
    for spec in specs:
        path = _expand(spec)
        for base in ([""] if path.startswith("/") else bases):
            full = os.path.normpath(os.path.join(base, path)) if base else path
            parent, name = os.path.split(full)
            if PATHSPEC_RE.search(parent) or not name:
                parent, name = base or "/", os.path.relpath(full, base or "/")
            groups.setdefault(parent, []).append(name)
    for parent, names in groups.items():
        found = _dirty_in(parent, tuple(names), deletes)
        if found is None or found:
            return found
    return ""


def _dirty_in(parent, names, deletes):
    """`_dirty_tracked` for `names`, each relative to the directory `parent`."""
    key = (parent, names, deletes)
    if key in _DIRTY:
        return _DIRTY[key]
    if not os.path.isdir(parent):
        _DIRTY[key] = ""  # no such directory holds a file to lose
        return ""
    done = _git_call(["status", "--porcelain=v1", "-z",
                      "--untracked-files=" + ("all" if deletes else "no"),
                      "--ignore-submodules", "--"] + list(names), parent)
    if done is None:
        _DIRTY[key] = None
        return None
    found = ""
    if done.returncode == 128 and b"not a git repository" in done.stderr:
        _DIRTY[key] = ""
        return ""
    if done.returncode != 0:
        _DIRTY[key] = _Unknown(UNCHECKED)
        return _DIRTY[key]
    entries = done.stdout.decode("utf-8", "replace").split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        if entry[0] in "RC":
            i += 1  # a rename's or a copy's source path follows as its own field
        if entry[1] in "MT" or (deletes and entry[:2] == "??"):
            found = entry[3:]
            break
    if not found:
        # git status does not look at a file it is told is unchanged, so such a file may hold
        # any change: its loss is unknown, never clean.
        listed = _git_call(["ls-files", "-v", "-z", "--"] + list(names), parent)
        if listed is None:
            found = None
        elif listed.returncode != 0:
            found = _Unknown(UNCHECKED)
        else:
            for entry in listed.stdout.decode("utf-8", "replace").split("\0"):
                if len(entry) > 2 and (entry[0].islower() or entry[0] == "S"):
                    found = _Unknown(HIDDEN + ": " + entry[2:])
                    break
    _DIRTY[key] = found
    return found


def _discards(verb, paths, cwd, deletes=False):
    """Grade 3 when `verb` deletes, empties or overwrites a file of `paths` that holds
    uncommitted work, as `git checkout -- <path>` grades for the same loss, or when that cannot
    be known (`_dirty_tracked`); None when none does."""
    found = _dirty_tracked(paths, cwd, deletes)
    if found is None:
        return 3, verb, " ".join(paths[:2]) + ", " + UNCHECKED, "git-discard"
    if isinstance(found, _Unknown):
        named = " ".join(paths[:2]).replace(PLACEHOLDER, "$(…)")
        return 3, verb, named + ", " + found, "git-discard"
    if found:
        return 3, verb, found, "git-discard"
    return None


def _overwrites(tokens):
    """The files the redirects of `tokens` truncate (`>`, `>|`, `&>`), not those they append to."""
    out = []
    for i, token in enumerate(tokens[:-1]):
        if re.match(r"^\d*&?>[!|]?$", token):
            out.append(tokens[i + 1])
    return out


# Commands whose whole output is nothing, so a `>` from one of them empties its target.
EMPTY_OUTPUT = ([], [":"], ["true"], ["false"], ["cat", "/dev/null"], ["printf", ""], ["echo", "-n"])


def _emptying(tokens, clean, cwd):
    """The grade of a simple command that empties or restores a file in place: a `>` with no
    output, `cp /dev/null`, `truncate`, or a blob written over a path from `git show <rev>:path`
    or `git cat-file`. The last is `git checkout <rev> -- path` by another route and grades 3
    whatever the file holds; the others grade 3 when the file holds uncommitted work. None when
    the command is none of these."""
    targets = [t for t in _overwrites(tokens) if t and not t.startswith("/dev/")]
    prog = clean[0].rpartition("/")[2] if clean else ""
    if targets and clean in EMPTY_OUTPUT:
        return _discards("empty write to", targets, cwd)
    if prog == "cp" and operands(clean[1:])[:1] == ["/dev/null"]:
        return _discards("cp /dev/null", operands(clean[1:])[1:], cwd)
    if prog != "git":
        # Any other `>` replaces what the file held, whatever the command writes into it.
        hit = _discards("overwrite of", targets, cwd) if targets else None
        if hit is None and prog in ("cp", "mv", "install"):
            hit = _discards(prog + " over", _copy_targets(clean[1:], cwd), cwd)
        if hit is None and prog == "tee" and not (short(clean[1:], "a") or has(clean[1:],
                                                                             "--append")):
            hit = _discards("tee over", operands(clean[1:]), cwd)
        if hit is None and prog in ("sed", "gsed"):
            hit = _sed_empties(clean[1:], cwd)
        if hit is not None:
            return hit
    if prog == "truncate":
        files, i, args = [], 0, clean[1:]
        while i < len(args):
            if args[i] in ("-s", "-r", "-o", "--size", "--reference"):
                i += 2
                continue
            if not args[i].startswith("-"):
                files.append(args[i])
            i += 1
        return _discards("truncate", files, cwd)
    if prog == "git" and targets:
        sub, sargs, _where = _git_sub(clean[1:])
        blobs = [os.path.normpath(op.partition(":")[2]) for op in operands(sargs)
                 if ":" in op and op.partition(":")[2]]
        if sub in ("show", "cat-file") and blobs:
            for target in targets:
                path = os.path.normpath(_expand(target))
                root = os.path.normpath(cwd) if cwd else ""
                if path.startswith("/") and not (root and path.startswith(root + "/")):
                    continue  # outside the working tree, as a copy to a scratch file is
                path = os.path.relpath(path, root) if path.startswith("/") else path
                if any(path == b or path.endswith("/" + b) or b.endswith("/" + path) for b in blobs):
                    return 3, "git " + sub + " >", target, "git-discard"
            return _discards("git " + sub + " >", targets, cwd)
    return None


# Programs that delete or empty the files they are named, for `xargs` and `find -exec`, which
# name them from what the grader cannot read.
DELETERS = {"rm", "unlink", "shred", "truncate"}
COPY_VALUE_FLAGS = ("-S", "--suffix", "-m", "--mode", "-o", "--owner", "-g", "--group")
# A `sed` script whose every command deletes lines: run in place, it empties the file.
SED_DELETE_RE = re.compile(r"^(?:\d+|\$|/(?:[^/\\]|\\.)*/)?(?:\s*,\s*(?:\d+|\$|/(?:[^/\\]|\\.)*/))?"
                           r"\s*!?\s*d$")


FIND_UNBOUNDED = {"!", "-not", "-o", "-or", "-regex", "-iregex", "-path", "-ipath", "-wholename",
                  "-iwholename", "-iname", "-lname", "-ilname", "-samefile", "-inum"}


def _find_discards(verb, args, cwd):
    """The grade of a `find` given `args` (its words before the action) that deletes what it
    finds: when only `-name` patterns bound what it matches, each is asked about under each
    start path, as a delete (`_discards`); otherwise which files it reaches is unknown, and
    it grades 3."""
    starts, names, i = [], [], 0
    while i < len(args) and not args[i].startswith("-") and args[i] not in ("!", "(", ")"):
        starts.append(args[i])
        i += 1
    rest = args[i:]
    for k, word in enumerate(rest):
        if word in FIND_UNBOUNDED:
            names = []
            break
        if word == "-name" and k + 1 < len(rest):
            names.append(rest[k + 1])
    if not names:
        return 3, verb, ", ".join(filter(None, [" ".join(starts[:1]), UNKNOWN_PATH])), "delete"
    # `find` matches a name at any depth; a pathspec's `*` crosses `/`, so `*/name` reaches the
    # ones below the start path.
    paths = [os.path.join(start, *parts) for start in starts or ["."] for name in names
             for parts in ((name,), ("*", name))]
    return _discards(verb, paths, cwd, deletes=True)


def _copy_targets(args, cwd):
    """The files `cp`, `mv` or `install` given `args` writes over: the last operand, each
    source's name inside it when it is a directory, and inside a `-t` directory."""
    ops, directory, i = [], None, 0
    while i < len(args):
        a = args[i]
        if a in ("-t", "--target-directory") and i + 1 < len(args):
            directory = args[i + 1]
            i += 2
            continue
        if a.startswith("--target-directory="):
            directory = a.partition("=")[2]
        elif a in COPY_VALUE_FLAGS:
            i += 1
        elif a == "--":
            ops.extend(args[i + 1:])
            break
        elif not a.startswith("-") or a == "-":
            ops.append(a)
        i += 1
    if directory is None:
        if len(ops) < 2:
            return []
        directory, sources = ops[-1], ops[:-1]
        out = [directory]
        where = os.path.join(cwd or "", _expand(directory))
        if not os.path.isdir(where):
            return out
    else:
        sources, out = ops, []
    return out + [os.path.join(directory, os.path.basename(s.rstrip("/"))) for s in sources]


def _sed_empties(args, cwd):
    """The grade of an in-place `sed` (`-i`, `-i.bak`, `-i ''`, `--in-place`) whose script only
    deletes lines, as `sed -i d f` empties `f`: graded as an empty write of each file. None for
    any other `sed`."""
    if not (short(args, "i") or has(args, "--in-place")):
        return None
    scripts, files, i = [], [], 0
    while i < len(args):
        a = args[i]
        if a in ("-e", "--expression") and i + 1 < len(args):
            scripts.append(args[i + 1])
            i += 2
            continue
        if a == "-i" and i + 1 < len(args) and args[i + 1] == "":
            i += 2  # BSD's suffix, here none
            continue
        if not a.startswith("-") or a == "-":
            files.append(a)
        i += 1
    if not scripts and files:
        scripts = [files.pop(0)]
    pieces = [p.strip() for s in scripts for p in re.split(r"[;\n]", s) if p.strip()]
    if not pieces or not all(SED_DELETE_RE.match(p) for p in pieces):
        return None
    return _discards("sed -i over", files, cwd)


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
                  | set(G2_SUBCOMMANDS) | set(SUDO) | (set(WRAPPERS) - set(MORE_WRAPPERS))
                  | {p for p, _s in RUNNERS}
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


def grade_tokens(tokens, cwd, depth, keyword_time=False, budget=None):
    """(grade, verb, target, family) for one simple command. One that redirects into a file
    grades at least 1 whatever it runs, since bash opens the file before it starts a wrapper:
    `nice -n 5 echo x > f` writes `f`. `keyword_time` says a leading `time` is written bare, so
    it is bash's reserved word (`_unwrap`).

    A wrapper is looked through in a loop, never by recursion, and a runner one level deeper
    for each way its options may be read (`_runner`). A command behind more prefixes than
    `budget` (`ro.MAX_PREFIXES`, less those a caller has looked through), counted from any word,
    is graded 3, as text too long to grade is, and names the file a redirect on it writes. What
    a wrapper leaves unread, a `git push` its options took and the files it writes itself raise
    the grade of the command it runs (`_wrapped_grade`)."""
    budget = ro.MAX_PREFIXES if budget is None else budget
    keyword = keyword_time and tokens[:1] == ["time"]
    wrote, wrapped = [], []
    for step in range(max(budget, 0) + 1):
        found = _grade_step(tokens, cwd, depth, wrote, budget - step, keyword and step == 0,
                            wrapped)
        if found[0] is not _AGAIN:
            hit = _wrapped_grade(found, wrapped)
            if hit[0] == 0 and wrote:
                return 1, "redirect to", wrote[-1], None
            return hit
        tokens = found[1]
    return _prefix_chain(wrote, wrapped)


def _prefix_chain(wrote, wrapped=None):
    """`PREFIX_CHAIN`, naming the first file a redirect or a wrapper looked through writes."""
    wrote = wrote + [f for _why, _push, files in wrapped or [] for f in files]
    return PREFIX_CHAIN[:2] + (wrote[0] if wrote else "",) + PREFIX_CHAIN[3:]


def _wrapped_grade(hit, wrapped):
    """`hit`, raised by the wrappers looked through to reach it, each a (what of it `_unwrap`
    could not read or None, whether its options took a `git … push`, the files it writes)."""
    unread = next((why for why, _push, _files in wrapped if why), None)
    if unread and hit[0] < 3:
        # What the wrapper runs is not known, so it may be anything: never let it through.
        return 3, unread, "", "opaque"
    if hit[0] < 2 and any(push for _why, push, _files in wrapped):
        # `timeout -k 5 git push`: the wrapper took `git` as a value; it may still push.
        return 2, "git push", "", "remote"
    files = [f for _why, _push, fs in wrapped for f in fs]
    if files and hit[0] < 1:
        return 1, "write to", files[-1], None
    return hit


def _runner(readings, cwd, depth, budget):
    """The worst grade over the readings `_runner_readings` gives of a runner's command."""
    if depth >= MAX_DEPTH:
        return max(_scan(" ".join(readings[0])), (3, "runner", "", "opaque"),
                   key=lambda h: h[0])
    return max((grade_tokens(r, cwd, depth + 1, budget=budget) for r in readings),
               key=lambda h: h[0])


def _grade_step(tokens, cwd, depth, recorded, budget, keyword_time=False, wrapped=None):
    """`grade_tokens` for one command word, or (`_AGAIN`, the tokens a wrapper runs). The files
    its redirects write are appended to `recorded`, the steps before it included, and what a
    wrapper it looks through leaves unread, takes or writes to `wrapped`; `budget` is how many
    more prefixes may be looked through."""
    raw = tokens
    tokens, written = _redirects(tokens)
    recorded.extend(t for t in written if t and t != "/dev/null")
    wrote = ""
    for target in written:
        if re.match(r"^/dev/(sd|disk|nvme|rdisk)", target):
            return 3, "redirect to", target, "system"
        if target and target != "/dev/null":
            wrote = target
    while tokens and ASSIGN_RE.match(tokens[0]):
        tokens = tokens[1:]
    emptied = _emptying(raw, tokens, cwd)
    if emptied is not None:
        return emptied
    read_only = ro.segment_verdict(list(tokens), budget) if tokens else True
    if read_only is None:  # the shorter chain a later step sees must not pass as read-only
        return _prefix_chain(recorded, wrapped)
    if read_only:
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
        readings = _runner_readings(_runner_words(prog, args, ops))
        if readings:
            return _runner(readings, cwd, depth, budget - 1)
    if prog == "nix-shell":
        for i, a in enumerate(args):
            if a in ("--run", "--command") and i + 1 < len(args):
                return _inner(args[i + 1], cwd, depth)
    if prog == "cargo" and ops[:1] == ["run"] and "--" in args:
        rest = args[args.index("--") + 1:]
        if rest:
            return _AGAIN, rest
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
    family = _program_family(head)
    if family:
        programs = _inline_programs(family, args)
        if programs:
            hit = max((_grade_program(p, cwd, depth, families={family}) for p in programs),
                      key=lambda h: h[0])
            if hit[0] < 3 and not all(_inert_program(p, {family}) for p in programs):
                # A program that can run a command may run one its arguments spell out.
                for word in args:
                    if word not in programs and next(_named(word), None) is not None:
                        hit = max(hit, grade_text(word, cwd, depth + 1), key=lambda h: h[0])
            if hit[0] > 1:
                return hit
    if prog in ("xargs", "parallel"):
        rest = strip_options(args, XARGS_VALUE_FLAGS)
        if _rm_flagged(rest):  # the operands arrive on stdin, so any rm -rf here is grade 3
            return 3, "xargs rm -rf", "", "delete"
        if rest and rest[0].rpartition("/")[2] in DELETERS:
            # Which files stdin names is not known, so whether one holds work is not either.
            return 3, prog + " " + rest[0].rpartition("/")[2], UNKNOWN_PATH, "delete"
        return _inner_tokens(rest, cwd, depth)
    if prog in WRAPPERS:
        rest, unread, _moved, files = _unwrap(prog, args, keyword_time and head == "time")
        if wrapped is not None:
            wrapped.append((unread, _names_push(args) and not _names_push(rest), files))
        if rest:
            return _AGAIN, rest
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
    if prog == "unlink":
        return (_discards("unlink", operands(args), cwd, deletes=True)
                or (1, "unlink", _joined(args, 1), None))
    if prog == "find":
        if "-delete" in args:
            return 3, "find -delete", _joined(args, 1), "delete"
        for flag in ("-exec", "-execdir", "-ok", "-okdir"):
            if flag in args:
                inner = args[args.index(flag) + 1:]
                inner = [t for t in inner if t not in (";", "+", "\\;", "{}")]
                if _rm_flagged(inner):
                    return 3, "find " + flag + " rm -rf", _joined(args, 1), "delete"
                if inner and inner[0].rpartition("/")[2] in DELETERS:
                    verb = "find " + flag + " " + inner[0].rpartition("/")[2]
                    return (_find_discards(verb, args[:args.index(flag)], cwd)
                            or (1, verb, _joined(args, 1), None))
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
    if _names_push(args):
        # A program this hook does not model, handed `git … push` as words: it may run it.
        return 2, "git push", "", "remote"
    return 1, prog, _joined(args, 1), None


def _inline_programs(family, args):
    """The programs an interpreter of `family` is handed inline by `args`, as `python3 -c`,
    `node -e`, `perl -e` or `ruby -e` take one, each graded as `_grade_program` reads a program:
    `perl -ne 'x'`, `perl -e'x'` and `node --eval=x` included. Reading stops at the first operand,
    the script whose own arguments follow."""
    evals, values = PROGRAM_FLAGS.get(family, DEFAULT_PROGRAM_FLAGS)
    evals = evals - {"-m"}
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if a == "--" or not a.startswith("-") or a == "-":
            break
        long_name, eq, value = a.partition("=")
        if a in evals or (family in CLUSTERED and re.match(r"^-[A-Za-z]+$", a)
                          and "-" + a[-1] in evals):
            if i + 1 < len(args):
                out.append(args[i + 1])
            i += 2
            continue
        if eq and long_name in evals:
            out.append(value)
        elif len(a) > 2 and a[:2] in evals and not a.startswith("--"):
            out.append(a[2:])
        elif a in values:
            i += 1
        i += 1
    return out


def _names_push(words):
    """Whether `words` hold a `git` word with a `push` word after it."""
    for i, word in enumerate(words):
        if word.rpartition("/")[2] == "git" and "push" in words[i + 1:]:
            return True
    return False


def _runner_words(prog, args, ops):
    """The words after a runner's subcommand that its command starts in: past the directory
    `direnv exec` takes, and after the `--` that `mise exec` needs before a command."""
    rest = args[args.index(ops[0]) + 1:]
    if prog == "direnv":
        return rest[1:]
    if prog == "mise" and "--" in rest:
        return rest[rest.index("--") + 1:]
    return rest


# The grader's own deadline, inside the 10-second PreToolUse timeout `lifecycle.registration`
# gives the dispatcher: a hook the runtime kills lets the command run ungraded and unlogged.
GRADE_SECONDS = 5.0
DEADLINE_NOTE = "(the grader ran past its deadline, so this is refused rather than run ungraded)"
DEADLINE_VERB = "command the grader ran past its deadline on"


class _Expired(BaseException):
    """The deadline's alarm. A BaseException, so no `except Exception` in the grader takes it."""


def timed_out(cmd):
    """The grade of `cmd` once grading ran past its deadline: the raw text scanned for the verb
    families (`_scan_text`, linear at any length). A destructive verb fails closed at 3; any
    other text keeps what the scan finds, at least 1, so a read-only command stays open."""
    hit = _scan_text(cmd)
    if hit[0] == 3:
        return 3, hit[1], DEADLINE_NOTE, hit[3]
    lowered = cmd.lower()
    entries = [entry for entry in DEADLINE_SCAN if all(n in lowered for n in entry[0])]
    if entries:
        for chunk in SCAN_SPLIT.split(lowered):
            for needles, verb, family in entries:
                if all(needle in chunk for needle in needles):
                    return 3, verb, DEADLINE_NOTE, family
    if hit[0] == 2:
        return hit
    return 1, DEADLINE_VERB, "", None


# What `timed_out` refuses beyond `SCAN`: each route that discards work, and each command whose
# grade waits on `git status` (`_discards`) or an alias lookup, which a deadline may have cut
# short. Substring needles within one chunk, as `SCAN`'s are, so the check stays linear.
DEADLINE_SCAN = [
    (("checkout",), "git checkout", "git-discard"),
    (("restore",), "git restore", "git-discard"),
    (("switch",), "git switch", "git-discard"),
    (("reset",), "git reset", "git-discard"),
    (("clean",), "git clean", "git-discard"),
    (("read-tree",), "git read-tree", "git-discard"),
    (("stash", "drop"), "git stash drop", "git-discard"),
    (("stash", "clear"), "git stash clear", "git-discard"),
    (("branch", "-d"), "git branch -D", "git-discard"),
    (("branch", "-f"), "git branch -f", "git-discard"),
    (("branch", "-m"), "git branch -M", "git-discard"),
    (("branch", "--"), "git branch", "git-discard"),
    (("update-ref",), "git update-ref", "git-discard"),
    (("apply", "-r"), "git apply -R", "git-discard"),
    (("git", "-c"), "git -c", "opaque"),
    (("alias",), "git alias", "opaque"),
    (("-delete",), "find -delete", "delete"),
    (("-exec",), "find -exec", "delete"),
    (("-ok",), "find -ok", "delete"),
    (("xargs",), "xargs", "delete"),
    (("rm ",), "rm", "git-discard"),
    (("unlink",), "unlink", "git-discard"),
    (("truncate",), "truncate", "git-discard"),
    (("shred",), "shred", "delete"),
    ((">",), "overwrite of", "git-discard"),
    (("cp ",), "cp over", "git-discard"),
    (("mv ",), "mv over", "git-discard"),
    (("install ",), "install over", "git-discard"),
    (("tee ",), "tee over", "git-discard"),
    (("sed", "-i"), "sed -i over", "git-discard"),
    (("sed", "--in-place"), "sed -i over", "git-discard"),
    (("of=",), "dd", "system"),
]


def grade_within(cmd, cwd="", seconds=None, grade=None):
    """(the grade `grade`, `grade_text` by default, gives `cmd`, whether the deadline cut it
    short), bounded by `seconds`, `GRADE_SECONDS` by default; past it the grade is
    `timed_out`'s. The deadline is a SIGALRM timer, so it holds on the main thread of a platform
    that has one; elsewhere grading is unbounded, as it was."""
    seconds = GRADE_SECONDS if seconds is None else seconds
    grade = grade or grade_text
    if not (seconds and seconds > 0 and hasattr(signal, "setitimer")
            and threading.current_thread() is threading.main_thread()):
        return grade(cmd, cwd), False

    def expire(_signum, _frame):
        raise _Expired()

    previous = signal.signal(signal.SIGALRM, expire)
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        try:
            return grade(cmd, cwd), False
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    except _Expired:
        return timed_out(cmd), True
    finally:
        signal.signal(signal.SIGALRM, previous)


def grade_text(cmd, cwd="", depth=0):
    """(grade, verb, target, family) for a whole command line: the maximum over its parts.

    A command that is not read-only and names the approvals store grades 3, whatever else it
    does: an approval must come from the user's prompt, never from a write the agent makes.
    Text whose reading raises, as a parser bug would, grades 3: it is never passed unread."""
    if depth == 0:
        # What `git status` said, the time spent asking it and where a `cd` went hold for one
        # command line, never the next.
        _DIRTY.clear()
        _GIT_SPENT[:] = [0.0, 0]
        del _CDS[:]
        _CD_LOST[0] = False
        _LINE[0] = cmd
    try:
        best = _grade_text(cmd, cwd, depth)
    except Exception:
        if depth:
            raise
        return UNREADABLE
    if depth == 0 and best[0] > 0 and best[3] != "approvals" and approvals is not None \
            and _store_write(cmd):
        return 3, "write to", "the approvals store", "approvals"
    return best


# The harness's state directory, where the approvals store lives: a `cd` into it, or a variable
# holding it, carries the store to the commands after it.
STATE_RE = re.compile(r"\.local[/\\]+state[/\\]+agent-harness|agent-harness[/\\]+approvals")
DECLARING = {"export", "declare", "typeset", "local", "readonly"}


def _store_write(cmd):
    """Whether `cmd`, which is not read-only as a whole, may write to the approvals store.

    A simple command reaches the store when it names the state directory, uses a variable
    assigned from it, follows a `cd` into it, or reads a pipe from one that does; the line may
    write the store when one of those is not read-only. Reading the store beside commands that
    write elsewhere is not a write to it. Text this cannot decompose surely, a here-document or
    a substitution naming the store, or a variable whose name is not literal, is read as before:
    any mention of the store is a write."""
    if not approvals.mentions_store(cmd):
        return False
    texts, bodies = _readings(cmd)
    if texts is None or len(texts) != 1:
        return True
    stripped, inners = _extract_subs(texts[0])
    linked = _linked_segments(stripped) if stripped is not None else None
    if linked is None:
        return True
    tainted, moved, reached = set(), False, []

    def names_store(text):
        return bool(STATE_RE.search(text) or approvals.mentions_store(text)
                    or any(re.search(r"\$\{?" + re.escape(v) + r"(?![A-Za-z0-9_])", text)
                           for v in tainted))

    for tokens, fed in linked:
        text = " ".join(tokens)
        here = moved or names_store(text) or (isinstance(fed, int) and reached[fed])
        reached.append(here)
        words = list(tokens)
        while words and words[0] in COMMAND_OPENERS:
            words = words[1:]
        while words and ASSIGN_RE.match(words[0]):
            name, _eq, value = words.pop(0).partition("=")
            if names_store(value):
                tainted.add(name)
        if words and words[0] in DECLARING:
            for word in words[1:]:
                name, eq, value = word.partition("=")
                if eq and names_store(value):
                    tainted.add(name)
        if not here:
            continue
        head = words[0].rpartition("/")[2] if words else ""
        if head in ("for", "select", "read") and len(words) > 1:
            tainted.update(w for w in words[1:] if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", w))
        if head in ("cd", "pushd"):
            moved = True
            continue
        if not words or head in DECLARING and all("=" in w for w in words[1:]):
            continue
        # A redirect writes where it names, so `ls <store> > /tmp/list` writes no store file;
        # after a `cd` into the state directory every target may be in it.
        clean, targets = _redirects(words)
        if targets and (moved or any(names_store(t) for t in targets)):
            return True
        if clean and not ro.segment_verdict(clean) and not _reads_only(clean):
            return True
    if any(names_store(inner) for inner in inners):
        return True

    def mentions(text):
        return len(STATE_RE.findall(text)) + sum(
            len(re.findall(r"\$\{?" + re.escape(v) + r"(?![A-Za-z0-9_])", text)) for v in tainted)

    # A loop's or a `case`'s header is no simple command, so a store it names, as `for f in
    # <store>/*` does, reaches commands this walk never saw reach it.
    if mentions(stripped) > sum(mentions(" ".join(tokens)) for tokens, _fed in linked):
        return True
    reaching = [b for b in bodies if names_store(b)]
    if not reaching:
        return False
    # A body naming the store is data unless something runs it: a shell, a reader this hook
    # cannot name, or a program that can run a command or write a file.
    parts = [tokens for tokens, _fed in linked]
    families = _input_families(parts, inners)
    if families is None or any(_runs_input(tokens) for tokens in parts):
        return True
    return any(not _inert_program(b, families) or _program_writes(b)
               for b in reaching) if families else False


# What lets a program write a file, for a program run where the approvals store is reached.
STORE_WRITES_RE = re.compile(
    r"\bopen\s*\([^)]*,|\.write|\bwrite\w*\s*\(|\bdump\w*\s*\(|\bshutil\b|\bfs\b|\bFile\b|>|"
    r"\b(?:remove|unlink|rename|replace|rmtree|copy\w*|move|touch|mkdir|makedirs|chmod|chown|"
    r"symlink|link|truncate|utime)\s*\(")


def _reads_only(words):
    """Whether the simple command `words` is an interpreter of a `TEXT_FAMILIES` family running an
    inline program that can neither run a command nor write a file, as `python3 -c
    "json.load(open(p))"` can only read."""
    family = _program_family(words[0]) if words else None
    if family not in TEXT_FAMILIES:
        return False
    evals = PROGRAM_FLAGS.get(family, DEFAULT_PROGRAM_FLAGS)[0] - {"-m"}
    for i in range(1, len(words) - 1):
        if words[i] in evals:
            program = words[i + 1]
            return _inert_program(program, {family}) and not _program_writes(program)
        if not words[i].startswith("-"):
            return False
    return False


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
        hit, executed = _grade_streams(linked, stripped, cwd, depth, bodies)
        best = max(best, hit, key=lambda h: h[0])
    if bodies:
        # A file this line wrote and an interpreter then runs is that interpreter's program,
        # not a script: only a shell, or a reader this hook cannot name, makes every body one.
        runs = executed is True or _feeds_shell(text, parts, inners, depth)
        programs = bool(executed) or _feeds_program(text, parts, inners, depth)
        families = _input_families(parts, inners) if programs else None
        if families is not None and isinstance(executed, frozenset):
            families = families | executed
        elif executed is True:
            families = None
        best = max(best, _grade_bodies(bodies, runs, cwd, depth, programs, families or None),
                   key=lambda h: h[0])
    if parts is None:
        return max(best, _scan(text), key=lambda h: h[0])
    # A SQL client named anywhere, a substitution included, since a body inside `$(…)` is
    # split out of the outer text before its command is graded.
    if bodies and SQL_CLIENT_RE.search(text):
        for body in bodies:  # the shell does not run a body, but a SQL client interprets it
            hit = _sql(body)
            if hit and hit[0] > best[0]:
                best = hit
    bare = _bare_times(stripped, parts)
    for tokens in parts:
        _track_cd(tokens, cwd)
        hit = grade_tokens(tokens, cwd, depth, bare)
        if hit[0] > best[0]:
            best = hit
        if best[0] == 3:
            break
    return best


def _track_cd(tokens, cwd):
    """Record where a `cd`, `pushd` or `chdir` in `tokens` may move the commands after it, so a
    file they name is asked about there as well as in `cwd` (`_dirty_tracked`). A subshell's
    `cd` is kept too, which can only ask about more places. One to a directory the grader
    cannot know marks the line lost; `popd` and `cd -` return to a directory already kept."""
    words = [t for t in tokens if t not in COMMAND_OPENERS]
    while words and ASSIGN_RE.match(words[0]):
        words = words[1:]
    if not words or words[0].rpartition("/")[2] not in ("cd", "pushd", "chdir"):
        return
    ops = [w for w in words[1:] if not (w.startswith("-") and w != "-")]
    target = ops[0] if ops else "~"
    if target == "-":
        return
    if _unknowable(target) or "*" in target or "?" in target:
        _CD_LOST[0] = True
        return
    path = _expand(target)
    bases = [path] if path.startswith("/") else [
        os.path.normpath(os.path.join(base, path)) for base in [cwd] + _CDS
        if base and os.path.isabs(base)]
    if not bases:
        _CD_LOST[0] = True
    for base in bases:
        if base not in _CDS and len(_CDS) < GIT_STATUS_CALLS:
            _CDS.append(base)
        elif base not in _CDS:
            _CD_LOST[0] = True


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


def _input_families(parts, inners):
    """The families of the interpreters among `parts` that read their program on standard
    input, possibly none; None when one may whose family this hook cannot name, the line does
    not decompose, or a substitution names an interpreter of its own."""
    if parts is None or any(INTERPRETER_WORD_RE.search(inner) for inner in inners or ()):
        return None
    found = set()
    for tokens in parts:
        family = _interprets_input(tokens)
        if family is True:
            return None
        if family:
            found.add(family)
    return frozenset(found)


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


def _grade_bodies(bodies, runs, cwd, depth, programs=False, families=None):
    """The worst grade the here-document bodies can carry. An unquoted body's substitutions run
    as the shell expands it, a body a shell may read runs as a script, and each is graded as the
    commands it holds; a body neither applies to is data, graded 0. A shell reads an unquoted
    body with the escapes bash removed while expanding it, so `\\$(x)` in it runs `x`: that
    script is graded as well as the body as written. When `programs`, another interpreter may
    read a body as its program, which `_grade_program` grades. What cannot be read for sure is
    graded unknown, or as `_scan` finds it, never lower."""
    best = (0, None, None, None)
    for body in bodies:
        hits = [_grade_program(body, cwd, depth, families=families)] if programs else []
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
            out += [_Program(w, family) for w in words]
        elif kind == "module":
            out.append(_Searched(words[0].rpartition(".")[2] + ".py"))
    return out


class _Searched(str):
    """A file name a shell looks up on `PATH`, or a module Python looks up on its path: any file
    of that base name the line wrote may be the one run (`_written_names`)."""


class _Program(str):
    """A file an interpreter of `family` reads as its program, not a shell (`_grade_streams`)."""

    def __new__(cls, word, family):
        made = super().__new__(cls, word)
        made.family = family
        return made


def _searched(word):
    """`word` as `_Searched` when it names no directory, as a command word, a sourced file or a
    shell's script does, since the shell then looks it up on `PATH`."""
    return word if "/" in word else _Searched(word)


def _interprets_input(tokens):
    """Whether the simple command `tokens` may read its standard input as the program of an
    interpreter other than a shell (`_reads_program`), past assignments, wrappers, runners such
    as `uv run` and `xargs`. A command word from a variable or substitution may name one.
    Truthy as the interpreter's family when it is known, True when it is not."""
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
            # The family itself, truthy, so `_input_families` can say which interpreter reads.
            return family if _reads_program(family, tokens[1:]) else False
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


# What lets a program in another language run a command line or reach a database: a process,
# shell or dynamic-code call, or a destructive database method. A program of a family in
# `TEXT_FAMILIES` that names none of these cannot run what its strings spell out, so they are
# text: an edit script that replaces "git push --force" in a document is not a push.
CALLS_RE = re.compile(
    r"\b(?:system|popen\w*|spawn\w*|posix_spawn\w*|exec|exec[lv]p?e?|execute\w*|eval|fork|"
    r"run|call|check_output|check_call|getoutput|getstatusoutput|startfile|shell_exec|"
    r"passthru|proc_open|pcntl_exec|execSync|execFile\w*|import_module|__import__|getattr|"
    r"setattr|vars|globals|Command|ProcessBuilder|drop\w*|delete_many|deleteMany|remove|"
    r"truncate)\s*\("
    r"|\b(?:subprocess|Popen|child_process|Open3|pexpect|ctypes|cffi|__builtins__|__dict__)\b"
    r"|\b(?:import|require|use)\b[^\n]*\b(?:sh|commands|plumbum|sarge|pty|invoke|fabric|system|"
    r"popen|exec\w*|spawn\w*|run|call|check_output|getoutput|startfile)\b"
    r"|Deno\.run|Bun\.spawn|io\.popen|os\.execute")
# The same in the families whose syntax runs a command with no call: Perl's and Ruby's
# backticks, `qx` and `%x`, a paren-less `system "…"`, a piped `open`, awk's `getline` and
# `print | "cmd"`.
SHELL_SYNTAX_RE = re.compile(
    r"`|\bqx\b|%x|\b(?:system|exec|spawn)\b\s*[\"'\[\w$@]|\bgetline\b|\|\s*[\"']|[\"']\s*\|")
# Families whose strings are text unless the program calls something above; `NO_BACKTICKS`
# among them read a backtick as a string, not a command. Every other family, make's recipes
# and sed's `e` included, and a program whose interpreter is not known, is read as before.
NO_BACKTICKS = {"python", "node", "deno", "bun", "lua", "julia", "Rscript", "R"}
TEXT_FAMILIES = NO_BACKTICKS | {"perl", "ruby", "php", "awk", "gawk", "mawk", "nawk"}
INERT = (1, "", "", "opaque")
# A reference, called or not, to what runs a command or deletes a file: a program can hand a
# function on without calling it where it is named (`map(os.system, …)`, `f = os.system`,
# `getattr(os, …)`), so any mention of one makes the program more than text.
REFERENCES_RE = re.compile(
    r"\b(?:os|posix|nt)\s*\.\s*(?:system|popen\w*|exec\w*|spawn\w*|posix_spawn\w*|fork\w*|"
    r"remove|unlink|rmdir|removedirs|truncate|replace|rename\w*|kill\w*)\b"
    r"|\b(?:subprocess|pty|pexpect|child_process|rmtree|commands|plumbum|sarge|importlib|"
    r"execSync|execFileSync|spawnSync|shell_exec|passthru|proc_open|getattr|attrgetter|"
    r"methodcaller|__getattribute__|__import__|import_module|__builtins__|builtins|__dict__|"
    r"eval|exec|dlopen)\b"
    r"|\bsys\s*\.\s*modules\b|\bprocess\s*\.\s*binding\b"
    r"|\.\s*(?:unlink\w*|rmdir\w*|rmtree|rm|rmSync|system|popen|exec\w*|spawn\w*|kill)\b"
    r"|\bimport\s+(?:os|posix|shutil)\s+as\b|\bfrom\s+(?:os|posix|nt|shutil|subprocess|pty)\s+import\b"
    r"|=\s*(?:os|posix|shutil)\s*(?:$|[;,)\]\n])"
    r"|\brequire\s*\(\s*(?![\"'][\w./@:-]+[\"']\s*\))")
# A reference to what writes a file, for a program run where the approvals store is reached:
# what `STORE_WRITES_RE` and `_open_writes` read too.
WRITE_REFERENCES_RE = re.compile(
    r"\b(?:FileIO|fdopen|O_WRONLY|O_RDWR|O_CREAT|O_TRUNC|O_APPEND|write_text|write_bytes|"
    r"writelines|writeFile\w*|appendFile\w*|createWriteStream|copyFile\w*|symlink\w*|"
    r"rename\w*|touch|mkdir\w*|makedirs|chmod|chown|truncate|unlink\w*|rmdir\w*|rmtree|"
    r"savetxt|to_csv|to_json|dump\w*)\b|\b(?:os|io|codecs|gzip|bz2|lzma)\s*\.\s*open\b")
OPEN_RE = re.compile(r"\bopen\b")
# A mode string `open` reads without writing: only these letters, and none of `w`, `a`, `x`, `+`.
READ_MODE_RE = re.compile(r"^[rbtU]*$")
MODE_LIKE_RE = re.compile(r"^[rwabxtU+]+$")


def _inert_program(text, families):
    """Whether `text`, read as its program by an interpreter of each of `families`, can run none
    of the commands its strings name. False when a family is unknown (None) or outside
    `TEXT_FAMILIES`, or when the program calls or names anything that runs a command or deletes
    a file (`CALLS_RE`, `REFERENCES_RE`)."""
    if not families or not all(f in TEXT_FAMILIES for f in families):
        return False
    if CALLS_RE.search(text) or REFERENCES_RE.search(text):
        return False
    return all(f in NO_BACKTICKS for f in families) or not SHELL_SYNTAX_RE.search(text)


def _call_args(text, start):
    """The top-level arguments of the call whose `(` is at `text[start]`, or None when it does
    not close within `STATEMENT_CHARS` characters. Strings are passed over, so a comma or a
    bracket in one splits nothing."""
    args, depth, quote, current, i = [], 0, None, [], start + 1
    end = min(len(text), start + 1 + STATEMENT_CHARS)
    while i < end:
        c = text[i]
        if quote:
            current.append(c)
            if c == "\\" and i + 1 < end:
                current.append(text[i + 1])
                i += 1
            elif c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
            current.append(c)
        elif c in "([{":
            depth += 1
            current.append(c)
        elif c in ")]}":
            if not depth:
                args.append("".join(current).strip())
                return [a for a in args if a]
            depth -= 1
            current.append(c)
        elif c == "," and not depth:
            args.append("".join(current).strip())
            current = []
        else:
            current.append(c)
        i += 1
    return None


def _string_value(arg):
    """The text of `arg` when it is one plain string literal, else None."""
    match = re.match(r"^[rRbBuU]?([\"'])([^\"'\\]*)\1$", arg)
    return match.group(2) if match else None


def _open_writes(text):
    """Whether any `open` in `text` may write: one handed on rather than called, one whose
    arguments are spread, or one given a mode that is not a plain read-only string. The path is
    the first argument of a bare `open(…)`; a method such as `Path(p).open('w')` takes the mode
    first. A string that is no mode, as a path is, is passed over wherever it sits."""
    for match in OPEN_RE.finditer(text):
        rest = text[match.end():]
        paren = re.match(r"\s*\(", rest)
        if not paren:
            return True  # `f = open`, `map(open, …)`: called where this cannot see
        args = _call_args(text, match.end() + paren.end() - 1)
        if args is None:
            return True
        method = text[:match.start()].rstrip().endswith(".")
        positional = 0
        for arg in args:
            if arg.startswith("*"):
                return True
            keyword = re.match(r"^([A-Za-z_]\w*)\s*=(?!=)\s*(.*)$", arg, re.S)
            if keyword:
                name, value = keyword.groups()
                if name == "opener" or name in ("mode", "flags") and not READ_MODE_RE.match(
                        _string_value(value) or "w"):
                    return True
                continue
            positional += 1
            if positional == 1 and not method:
                continue  # the file
            value = _string_value(arg)
            if value is None:
                if re.match(r"^-?\d+$", arg):
                    continue  # buffering
                return True
            if MODE_LIKE_RE.match(value) and not READ_MODE_RE.match(value):
                return True
    return False


def _program_writes(text):
    """Whether the program `text` may write a file, as a program reaching the approvals store
    must not."""
    return bool(STORE_WRITES_RE.search(text) or WRITE_REFERENCES_RE.search(text)
                or _open_writes(text))


def _grade_program(text, cwd, depth, skip=None, families=None):
    """The grade of `text` read as its program by an interpreter this hook cannot parse, such as
    Python, awk or make: unknown, graded 1, and higher when the text names a command that grades
    higher, since the program may run it. A program `families` reads that calls nothing that
    runs a command is text, graded 1 (`_inert_program`). Each line, each quoted string on it and a line's
    strings joined by spaces (`["git", "push"]`), or a statement's when an open bracket carries
    it over several lines (`_statement`), is graded as shell text when it names a program the
    grader knows, `PROGRAM_CHECKS` of them at most, `skip` never, as its caller grades it; the
    whole text is scanned for grade-3 families and a push, which covers what any one line or
    string of it would show a scan. Linear in the length of the text."""
    if _inert_program(text, families):
        return INERT
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
    being one. A `_wild` word whose base name is literal, as `$dir/run.py` is, names only a file
    of that base name or of a `_wild` one: whatever `$dir` holds, it is not `$dir/notes.txt`."""
    if _wild(word):
        base = word.rpartition("/")[2]
        if not base or _wild(base) or "{" in base:
            return list(written)
        return [k for k in written
                if k.rpartition("/")[2] == base or _wild(k.rpartition("/")[2])
                or "{" in k.rpartition("/")[2]]
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
    family = False if shell else _interprets_input(tokens)
    if shell or family:
        if isinstance(incoming, str):
            return (grade_text(incoming, cwd, depth + 1) if shell
                    else _grade_program(incoming, cwd, depth,
                                        families=None if family is True else {family}))
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


def _heredoc_inputs(linked, bodies):
    """{(command index, token index): text} for each here-document operator in `linked` whose
    body is known text: quoted, or holding nothing the shell expands. Bodies are matched to the
    operators in order, and only when they pair one to one, as they do unless a substitution
    holds a here-document of its own."""
    ops = [(n, i) for n, (tokens, _fed) in enumerate(linked)
           for i, token in enumerate(tokens[:-1]) if token in ("<<", "<<-")]
    if not bodies or len(ops) != len(bodies):
        return {}
    return {op: str(body) + "\n" for op, body in zip(ops, bodies)
            if getattr(body, "quoted", False) or not re.search(r"[$`\\]", body)}


def _grade_streams(linked, whole, cwd, depth, bodies=None):
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
    readers, unknown_reader = set(), False
    fed_bodies = _heredoc_inputs(linked, bodies)

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

    for n, (tokens, fed) in enumerate(linked):
        incoming, sourced, redirected = None, False, []
        if fed is not None:
            known = fed != COMPOUND and outputs[fed] is not None
            incoming = outputs[fed] if known else UNKNOWN
            sourced = fed != COMPOUND and sourced_out[fed]
        for i, token in enumerate(tokens[:-1]):
            if (n, i) in fed_bodies:
                incoming, sourced = fed_bodies[(n, i)], False
            elif HERE_STRING_RE.match(token):
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
            reader = _runs_input(tokens) or _interprets_input(tokens) if sourced else False
            if reader:
                executed = True
                if reader is True:
                    unknown_reader = True
                else:
                    readers.add(reader)
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
    def ran():
        """`executed` as `_grade_reading` reads it: False, True when a shell or an interpreter
        this hook cannot name may run what the line wrote, else the interpreters' families."""
        if not executed:
            return False
        return True if unknown_reader or not readers else frozenset(readers)

    if not written:
        return best, ran()
    if any(w.rpartition("/")[2] in RELOCATORS for w in runs):
        memo["loose"] = True
    # Each file run, with who runs it: an interpreter's family when it reads the file as its
    # script (`_Program`), None for a shell, a command word or a file fed to standard input.
    names = {}
    for word in runs + [w for words in run_lists() for w in words]:
        for name in _written_names(written, word):
            names.setdefault(name, set()).add(getattr(word, "family", None))
    texts = {}
    for name, families in names.items():
        for t in written[name]:
            texts.setdefault(t, set()).update(families)
    if ANY_FILE in texts:
        texts = {t: {None} for each in written.values() for t in each}
    for text, families in texts.items():
        executed = True
        if None in families:
            unknown_reader = True
        else:
            readers.update(families)
        if text is ANY_FILE:
            continue
        if isinstance(text, str) and None not in families:
            hits = (_grade_program(text, cwd, depth, families=families),)
        elif isinstance(text, str):
            hits = (grade_text(text, cwd, depth + 1), _grade_program(text, cwd, depth))
        elif text is RESHAPED:
            hits = ((2, "a reshaped script", "", "opaque"), unread())
        else:
            hits = (unread(),)
        for hit in hits:
            if hit[0] > best[0]:
                best = hit
    return best, ran()


def reason(grade, verb, target, family, variant):
    clause = CLAUSES.get(family) or GENERIC[grade]
    phrase = " ".join(p for p in (verb, target) if p).strip()
    return "grade %d, %s: %s %s — %s (%s, autonomy=%s)" % (
        grade, LABELS[grade], phrase or "this command", clause, PLAIN[grade], HOOK, variant)


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
# A context key: True once a `cd` may resolve other than logically against the working
# directory, through CDPATH or a physical-path option; `_cd_certain` names what still resolves.
_CD_UNKNOWN = object()
# A context key: True once a `cd` itself may not move where it says, because the line defines a
# function, which may be named `cd`, sources a script or sets a trap, an alias or `enable`, or a
# shell started here may read a startup file first; `_walk` says which.
_CD_UNTRUSTED = object()
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


HOME_WORD_RE = re.compile(r"HOME(?:\[[^]]*\])?(?:\+?=|$)")


def _mentions_home(tokens):
    """Whether a simple command may assign HOME: any word `HOME`, or an assignment to it, as in
    `HOME=x`, `HOME[0]=x`, `export HOME=x`, `read HOME` or `unset HOME`."""
    return any(HOME_WORD_RE.match(token) for token in tokens)


# Options that make `cd` resolve `..` physically: bash's `set -P` or `set -o physical`, and
# zsh's `set -w`, `chase_links` and `chase_dots`, which zsh spells with any case and underscores.
PHYSICAL_OPTION_RE = re.compile(r"^[-+][A-Za-z]*[Pw]|physical|chase_?(?:links|dots)", re.I)
# `shopt -s cdable_vars` or zsh's `setopt cdablevars`: a `cd name` that finds no directory goes
# to the value of the variable `name`.
CDABLE_OPTION_RE = re.compile(r"cd_?able_?vars", re.I)
# A function defined at a command position, `f() {`, `f ()` or `function f`, outside quotes as
# `_unquoted_structure` leaves them. zsh's anonymous `() { … }` runs at once, as walked.
FUNCTION_DEF_RE = re.compile(
    r"(?:^|[;&|(){}\n]|(?<![^\s;&|(){}])(?:then|do|else|elif|time|!))[ \t]*"
    r"(?:function\s+[^\s;&|()<>]|[^\s;&|(){}<>'\"$`=]+\s*\(\s*\))")
# The same definitions, each with its name and the body's opening bracket.
FUNCTION_HEAD_RE = re.compile(
    r"(?:^|[;&|(){}\n]|(?<![^\s;&|(){}])(?:then|do|else|elif|time|!))[ \t]*"
    r"(?:function\s+(?P<keyword>[^\s;&|()<>{}]+)(?:\s*\(\s*\))?"
    r"|(?P<name>[^\s;&|(){}<>'\"$`=]+)\s*\(\s*\))\s*(?P<open>[{(]?)")
# Words that, run from a trap or a function body, may move this shell, make `cd` other than the
# builtin, or run text the walk does not read.
MOVING_WORDS = frozenset(("cd", "pushd", "popd", "source", ".", "eval", "exec", "trap",
                          "enable", "disable", "alias", "autoload", "functions", "builtin",
                          "command"))
# Variables that make a shell started later read a startup file first, which may `cd` or
# redefine it: bash's BASH_ENV, zsh's ZDOTDIR and an exported bash function.
STARTUP_WORD_RE = re.compile(r"(?<![A-Za-z0-9_])(?:BASH_ENV|ZDOTDIR|BASH_FUNC_)")
# A word naming CDPATH anywhere: `CDPATH=w`, `${CDPATH:=w}`, `declare -n r=CDPATH`.
CDPATH_WORD_RE = re.compile(r"(?<![A-Za-z0-9_])CDPATH(?![A-Za-z0-9_])")
# Builtins that assign the variable an argument names, which may be CDPATH behind a `$v`.
ASSIGNING_BUILTINS = {"declare", "typeset", "export", "local", "readonly", "read", "printf",
                      "getopts", "mapfile", "readarray", "let", "unset", "vared"}
# A command word bash may expand into another, `cd` or `eval` included: `$c` or `c?`, when it
# has no `/`, since a word with a `/` names a file, never a builtin; a brace expansion such as
# `{cd,../d}` always, since it splits into words before the `/`. A `[` is a glob only with a `]`
# after it, so `[` and `[[` are themselves.
DYNAMIC_HEAD_RE = re.compile(r"[$`*?]|\[.*\]")


def _command_word(tokens):
    """(the command word a simple command runs, as written, its arguments, whether bash and zsh agree it is that
    command), past assignments and the prefixes that run a builtin in this shell.

    `time` is the reserved word only as the first word, so `x=1 time cd d`, `builtin time cd d`
    and `time time cd d` run the program `time`, which cannot move this shell; `time -p` and a
    `time` after a redirect are the reserved word to bash and a command to zsh. `builtin` runs
    the builtin in both shells; `builtin --`, `command`, `noglob` and `nocorrect` are read
    differently by the two, so what follows them is not certain. `command -v cd` prints and runs
    nothing, so it is the command `command`, as is `command -pv cd`; bash reads bundled and
    repeated option letters, so `command -pp cd` runs the cd. `builtin -p cd` fails in both. The lexer drops
    quoting, so `_bare_times` must also hold for a leading `time` to be the reserved word."""
    tokens = list(tokens)
    body, sure = _redirects(tokens)[0], True
    if body[:1] == ["time"] and tokens[:1] != ["time"]:
        body, sure = body[1:], False  # `>f time cd d`: bash times the cd, zsh runs `time`
    elif body[:1] == ["time"]:
        body = body[1:]
        if body[:1] == ["-p"]:
            body, sure = body[1:], False
        if body[:1] == ["!"]:
            body = body[1:]
    while body and ASSIGN_RE.match(body[0]):
        body = body[1:]
    while body:
        if body[0] == "builtin":
            if body[1:2] == ["--"]:
                body, sure = body[2:], False
            elif body[1:2] and body[1].startswith("-"):
                return body[0], body[1:], sure
            else:
                body = body[1:]
        elif body[0] in ("command", "noglob", "nocorrect"):
            rest = body[1:]
            while rest and rest[0].startswith("-") and body[0] == "command":
                if rest[0] == "--":
                    rest = rest[1:]
                    break
                if not re.fullmatch(r"-p+", rest[0]):
                    return body[0], body[1:], True  # `-v`, `-pV` print; `-x` and `-` run no cd
                rest = rest[1:]
            body, sure = rest, False
        else:
            break
    if not body:
        return "", [], sure
    return body[0], body[1:], sure


def _bare_times(text, parts):
    """Whether each `time` word in `parts`, the simple commands of `text`, is written bare and
    none follows a `!`: the lexer returns `\\time`, `'time'` and `t''ime` as `time`, which bash
    runs as the program, and `! time cd d` stays put in bash where zsh moves."""
    unquoted = re.sub(r"'[^']*'|\"(?:\\.|[^\"\\])*\"|\\.", "_", text)
    bare = re.findall(r"(?<![^\s;&|(){}])(!\s+)?time(?![^\s;&|()<>])", unquoted)
    return (not any(bare) and len(bare) == sum(tokens.count("time") for tokens in parts))


def _dynamic_head(word):
    """Whether the command word `word` may expand into a builtin, as `$c` or `{cd,d}` can."""
    return bool(BRACE_RE.search(word)) or ("/" not in word and (
        PLACEHOLDER in word or bool(DYNAMIC_HEAD_RE.search(word))))


def _runs_unseen(tokens):
    """Whether a simple command runs text the static walk does not follow in this shell, so it
    may change the directory, HOME, CDPATH or a shell option: `eval`, or a command word bash
    expands, as `v=HOM; eval ${v}E=x` and `$c d` do."""
    word = _command_word(tokens)[0]
    return word.rpartition("/")[2] == "eval" or _dynamic_head(word)


def _moves_cd_resolution(tokens):
    """Whether a simple command may change how a later `cd` resolves its operand: it names
    CDPATH, as `CDPATH=w`, `export CDPATH`, `: ${CDPATH:=w}` or `CDPATH=w cd b` do, an assigning
    builtin names a variable through an expansion, as `read $v` may, or it sets a physical-path
    or `cdable_vars` option with `set`, `setopt` or `shopt`, or one it names by an expansion."""
    if any(CDPATH_WORD_RE.search(token) for token in tokens):
        return True
    word, args, _sure = _command_word(tokens)
    # Only the name part of `name=value` can be CDPATH; `PATH=$PATH:x` cannot assign it.
    names = [a.partition("=")[0] for a in args]
    if word in ASSIGNING_BUILTINS and any(
            PLACEHOLDER in n or any(c in n for c in "$`") for n in names):
        return True
    return word in ("set", "setopt", "shopt") and any(
        PHYSICAL_OPTION_RE.search(a) or CDABLE_OPTION_RE.search(a) or PLACEHOLDER in a
        or any(c in a for c in "$`") for a in args)


def _sources(tokens):
    """Whether a simple command reads a script into this shell, as `. ./s.sh` and `source s.sh`
    do: the script may change the directory or redefine `cd`, so what follows runs where it
    left off, whatever a later `cd` says. The script is not read."""
    return _command_word(tokens)[0] in (".", "source")


def _inert(text, literal=False):
    """Whether shell text run later, as a trap's action or a function's body, leaves this
    shell's directory and its `cd` alone: it names none of `MOVING_WORDS`, defines no function,
    runs no command word the walk cannot read, and neither assigns HOME nor changes how `cd`
    resolves, as `export HOME=x` and `shopt -s cdable_vars` do. A `literal` text, a trap's,
    holds no `$`, backquote or substitution either, since those expand as the trap is set."""
    if literal and (PLACEHOLDER in text or any(c in text for c in "$`")):
        return False
    if _defines_function(text):
        return False
    parts = segments(text)
    return parts is not None and not any(
        _runs_unseen(tokens) or _moves_cd_resolution(tokens) or _mentions_home(tokens)
        or any(t in MOVING_WORDS for t in tokens) for tokens in parts)


def _resets_cd(tokens):
    """Whether a simple command may make every later `cd` go elsewhere, or a later command run
    in another directory: a trap other than on EXIT, which runs its text before a command
    (`DEBUG`), after a failure (`ERR`) or a return (`RETURN`), where `cd` moves this shell; an
    alias, which may name `cd`; or `enable` or zsh's `disable` with a name, since `enable -n cd`
    leaves `cd` the program, which moves nothing; or zsh's `autoload` of a name or `functions
    -c`, which define a function. A trap that prints, as `trap -p`, or resets, as `trap - INT`
    or `trap INT`, or ignores, as `trap '' INT`, runs no text, and one whose action is `_inert`
    moves nothing, as `trap 'echo failed' ERR` does not."""
    word, args, _sure = _command_word(tokens)
    if word in ("enable", "disable", "autoload"):
        return any(not a.startswith(("-", "+")) for a in args)
    if word == "functions":
        return any(a.startswith("-") and "c" in a for a in args)
    if word == "alias":
        return any("=" in a for a in args)
    if word != "trap":
        return False
    if args[:1] == ["--"]:
        args = args[1:]
    elif args[:1] and args[0].startswith("-") and args[0] != "-":
        return False  # `-l` and `-p` print
    if len(args) < 2 or args[0] in ("-", ""):
        return False
    return (any(sig.upper() not in ("EXIT", "0") for sig in args[1:])
            and not _inert(args[0], literal=True))


def _evals_inert(tokens):
    """Whether a simple command is an `eval` of literal text that is `_inert`."""
    word, args, _sure = _command_word(tokens)
    return (word.rpartition("/")[2] == "eval" and not _dynamic_head(word)
            and _inert(" ".join(args), literal=True))


def _defines_function(text):
    """Whether `text` defines a shell function: one named `cd`, `pushd` or `builtin` changes
    what every later `cd` does, and any may be called where the walk does not look."""
    return bool(FUNCTION_DEF_RE.search(_unquoted_structure(text)))


def _defines_moving_function(text):
    """Whether `text` defines a function that may move this shell or change what `cd` does: one
    named in `MOVING_WORDS`, or whose body is not a bracketed group this reader takes apart and
    finds `_inert`, as it finds `log() { echo "$@"; }`. A body holding any bracket other than a
    `${…}` expansion is not taken apart, so `f() { echo }; cd ..; }` is not read as `echo `."""
    structure = _unquoted_structure(text)
    heads = list(FUNCTION_HEAD_RE.finditer(structure))
    if len(heads) != len(FUNCTION_DEF_RE.findall(structure)):
        return True  # a definition this reader does not take apart
    # Quoted brackets are blanked, so only the ones the shell reads are counted.
    masked = re.sub(r"'[^']*'|\"(?:\\.|[^\"\\])*\"|\\.",
                    lambda m: "_" * len(m.group()), structure)
    for head in heads:
        name = re.sub(r"[\\'\"]", "", head.group("keyword") or head.group("name"))
        opening = head.group("open")
        if name in MOVING_WORDS or not opening:
            return True
        start = end = head.end("open")
        while end < len(masked):
            if masked.startswith("${", end):  # `${x}`, unnested
                close = masked.find("}", end)
                if close < 0 or "{" in masked[end + 2:close]:
                    return True
                end = close + 1
            elif masked[end] in "{}()":
                break
            else:
                end += 1
        if masked[end:end + 1] != ("}" if opening == "{" else ")"):
            return True
        # `}` closes the group only where a command may start: `{ echo; }`, not `{ echo }`.
        if opening == "{" and masked[:end].rstrip(" \t")[-1:] not in (";", "&", "\n"):
            return True
        if not _inert(text[start:end]):
            return True
    return False


def _cd_unknown(variables):
    """Whether a relative `cd` operand may not resolve against the working directory with `..`
    taken logically: CDPATH is set here, in the hook's environment or earlier in the line, or a
    physical-path option may be on. `_cd_certain` names the operands that still resolve."""
    variables = variables or {}
    return (bool(os.environ.get("CDPATH")) or bool(variables.get(_CD_UNKNOWN))
            or "CDPATH" in variables)


def _cd_certain(target):
    """Whether `cd target` reaches the same directory under CDPATH and a physical-path option as
    without them: bash searches CDPATH for an operand that starts with neither `/` nor a `.` or
    `..` segment, and resolves `..` physically, so `/x`, `~/x` and `./x` are certain and `b` and
    `l/..` are not."""
    parts = target.split("/")
    return ".." not in parts and (target.startswith(("/", "~")) or parts[0] == ".")


def _assignment_contexts(text, parts, home_unknown=False, cd_unknown=False):
    """Static assignment values visible before each parsed command in a straight sequence.

    Each context also says whether HOME may have changed before its command, from
    `home_unknown` or an earlier command naming HOME, so no later `~` is expanded, and whether a
    `cd` may resolve other than logically against the working directory, from `cd_unknown` or
    an earlier command `_moves_cd_resolution` names. A command that runs text the walk does not
    read may do both: one `_runs_unseen` names, a sourced script, or a trap `_resets_cd` names."""
    homes, home, paths, path = [], home_unknown, [], cd_unknown
    for tokens in parts:
        homes.append(home)
        paths.append(path)
        unseen = _runs_unseen(tokens) or _sources(tokens) or _resets_cd(tokens)
        home = home or _mentions_home(tokens) or unseen
        path = path or _moves_cd_resolution(tokens) or unseen
    values, contexts, enabled = {}, [], True
    sources = _source_segments(text)
    if sources is None or len(sources) != len(parts):
        return [{_UNSAFE_OPERANDS: None, _HOME_UNKNOWN: moved, _CD_UNKNOWN: resolving}
                for moved, resolving in zip(homes, paths)]
    reserved = ro.WORD_DROP | ro.WORD_COND | ro.WORD_HEADER
    for tokens, source, moved, resolving in zip(parts, sources, homes, paths):
        context = (_operand_context(values, source)
                   if enabled else {_UNSAFE_OPERANDS: None})
        context[_HOME_UNKNOWN] = moved
        context[_CD_UNKNOWN] = resolving
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
LOOP_OPEN = {"while", "until", "for", "select"}


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


CONTINUED_RE = re.compile(r"(?:&&|\|\||\|&?)\s*$")


def _structure_line(text):
    """`_unquoted_structure(text)` on one line: a newline reads as `;`, except after `&&`, `||`,
    `|` or `|&`, where bash continues the list or pipeline on the next line, blank lines
    included, so a `cd d` on the line after `false &&` does not run."""
    lines = _unquoted_structure(text).split("\n")
    out = lines[0]
    for line in lines[1:]:
        out += (" " if CONTINUED_RE.search(out) else " ; ") + line
    return out


def _confined(text):
    """Per simple command of `segments(text)`, whether a `cd` there is confined to it, or None
    when the walk cannot place the line's structure and each `cd` falls back to `_isolating`.

    A pipeline binds tighter than `&&`, `||` and `;`, so every element of a pipeline starts in the
    directory in effect when it begins: only a `cd` inside a pipeline element, a subshell or a
    background job is confined. A `cd` inside a brace group, conditional or loop keeps the
    line-wide rule, because such a construct may itself be a pipeline element."""
    try:
        tokens = ro.tokenize(_structure_line(text))
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


def _list_places(text):
    """Per simple command of `segments(text)`, (whether it may not run when its AND-OR list
    does, the list it is in, whether it runs only past a `||` of that list, the chains it runs
    only through, its own chain), or None when the walk cannot place the line's structure.

    A command after `&&` or `||`, negated with `!`, or inside a conditional, loop or `case` may
    not run, or may run where a `cd` before it failed: `true || cd d` stays put, and `cd d || x`
    runs `x` where `cd d` did not go. The first command of a list always runs, and so does one
    in a brace group or subshell that does, whose `;` does not end the list around it.

    A chain holds pipelines each of which runs only when every one before it in the chain ran
    and succeeded. A list's first pipeline, and one after `||`, starts a chain; one after `&&`
    joins the chain of the pipeline before it, unless that one ran only past a `||`, as the
    `c` of `a || b && c` runs where `b` never did. A command runs only through its pipeline's
    chain, the chains of the pipelines holding it, and, in the `then` part of an `if`, the chain
    of its condition's last pipeline: `git push` in `[ -d d ] && cd d && git push` and in
    `if cd d; then git push; fi` runs only where the `cd` went. A negated command has no chain
    of its own, since it runs its successors when it fails, and inside a loop, where a later
    pass may start where an earlier one moved, a command has none and runs through none."""
    try:
        tokens = ro.tokenize(_structure_line(text))
    except ValueError:
        return None
    places, ops, lists, cur, skipping, negated = [], [], 0, [], False, False
    groups = []  # (opening word, whether what it holds may not run), innermost last
    # Per open compound, innermost last: the chains all it holds runs through (None in a loop),
    # whether it opened past a `||`, as the `{ x; }` of `cd d || { x; }` runs only where `cd d`
    # failed, and its list state — the operator before the pipeline to come, whether that
    # pipeline has begun, the chain it is in, the chain a pipeline after `&&` joins, whether it
    # is negated.
    frames = [{"base": frozenset(), "through": frozenset(), "past_or": False, "after": None,
               "begun": False, "chain": None, "joinable": None, "negated": False}]
    chains = [0]

    def enclosed():
        return bool(ops) or negated or bool(groups and groups[-1][1])

    def begin():
        frame = frames[-1]
        if frame["begun"]:
            return
        if frame["after"] == "&&" and frame["joinable"] is not None:
            chain = frame["joinable"]
        else:
            chains[0] += 1
            chain = chains[0]
        frame.update(chain=chain, joinable=None if frame["after"] == "||" else chain,
                     begun=True)

    def new_list(frame, after=None):
        frame.update(after=after, begun=False, negated=False)

    def enter(word):
        begin()
        outer = frames[-1]
        base = (None if word in LOOP_OPEN or outer["through"] is None
                else outer["through"] | {outer["chain"]})
        frames.append({"word": word, "base": base, "through": base,
                       "past_or": "||" in ops or outer["past_or"], "after": None,
                       "begun": False, "chain": None, "joinable": None, "negated": False})

    for token in tokens:
        if token in ro.ALWAYS_DELIM:
            if cur:
                places.append(opened)
            cur, skipping = [], False
            if token in ("&&", "||"):
                ops.append(token)
                new_list(frames[-1], token)
            elif token == "(":
                enter(token)
                groups.append((token, enclosed()))
                ops, negated = [], False
            elif token == ")":
                if groups and groups[-1][0] == "(":
                    groups.pop()
                    frames.pop()
                elif not groups or groups[-1][0] != "case":  # a `case` pattern ends at `)`
                    return None
                else:
                    new_list(frames[-1])
            elif token in (";", "&", ";;"):
                ops, lists = [], lists + (not groups)
                new_list(frames[-1])
            continue
        if skipping:
            continue
        if not cur:
            frame = frames[-1]
            if token in COMPOUND_OPEN:
                enter(token)
                groups.append((token, token != "{" or enclosed()))
                ops, negated = [], False
            elif token in COMPOUND_CLOSE:
                if not groups or groups[-1][0] == "(":
                    return None
                groups.pop()
                frames.pop()
            elif token == "then" and frame.get("word") == "if":
                frame["through"] = (frame["base"] | {frame["joinable"]}
                                    if frame["base"] is not None
                                    and frame["joinable"] is not None else frame["base"])
                new_list(frame)
            elif token in ("elif", "else", "do"):
                frame["through"] = frame["base"]
                new_list(frame)
            if token == "!":
                begin()
                frames[-1]["negated"] = negated = True
                continue
            if token in ro.WORD_DROP or token in ro.WORD_COND:
                continue
            if token in ro.WORD_HEADER:
                skipping = True
                continue
            begin()
            frame = frames[-1]
            through = frame["through"]
            opened = (enclosed(), lists, "||" in ops or frame["past_or"],
                      frozenset() if through is None else through | {frame["chain"]},
                      None if through is None or frame["negated"] else frame["chain"])
            negated = False
        cur.append(token)
    if cur:
        places.append(opened)
    return None if groups else places


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


def _governed(tokens, cwd, depth, variables=None, causes=None, keyword_time=False):
    """[(action class, grade, directory, paths written)] for one simple command.

    Wrappers, runners, `sudo` and a shell's `-c` text are looked through, as the grader looks
    through them, and the inner command is governed at the higher of the two grades; a run of
    wrappers is one step (`_peel`), and a command still to look through at `MAX_DEPTH` is grade
    3 at an unknown directory, a push when it names one. A command this hook does not model
    that is handed `git … push` as words is a push at an unknown directory. Each push
    found appends to `causes` the `git -C` operand that left its directory unknown, or None, so
    the list pairs one to one, in order, with the push entries returned."""
    grade, verb, _target, family = grade_tokens(list(tokens), cwd or "", depth, keyword_time)
    body, targets = _redirects(list(tokens))
    while body and ASSIGN_RE.match(body[0]):
        body = body[1:]
    if not body:
        return [(SHELL, grade, cwd, _written("", [], targets, cwd))]
    prog, args = body[0].rpartition("/")[2], body[1:]
    ops = operands(args)
    written = _written(prog, args, targets, cwd)
    inner, readings = None, []
    if prog in WRAPPERS:
        keyword = keyword_time and tokens[:1] == ["time"]
        rest, _unread, chdir, wrote, capped = _peel(body, keyword)
        written += _written("", [], wrote, None if chdir else cwd)
        if chdir:
            cwd = None  # `env -C` moves the inner command; no literal is trusted here
        inner = ("tokens", rest)
        if capped:
            depth = MAX_DEPTH
        elif _names_push(args) and not _names_push(rest):
            inner = None  # a `git` its options took: governed below as a possible push
    elif prog in SUDO:
        inner = ("tokens", strip_options(args, SUDO[prog]))
    elif (prog, ops[0] if ops else "") in RUNNERS:
        # Every reading is governed, as every one is graded (`_runner`).
        readings = _runner_readings(_runner_words(prog, args, ops))
        inner = ("tokens", readings[0]) if readings else None
    elif prog in SHELLS:
        for i, a in enumerate(args):
            if DASH_C_RE.match(a) and i + 1 < len(args):
                inner = ("text", args[i + 1])
                break
    elif prog == "eval":
        inner = ("text", " ".join(args))
    elif prog == "nix-shell":
        for i, a in enumerate(args):
            if a in ("--run", "--command") and i + 1 < len(args):
                inner = ("text", args[i + 1])
                break
    if inner is not None and inner[1] and depth >= MAX_DEPTH:
        # Too deep to look through: never dropped, but governed as unknown.
        words = inner[1] if inner[0] == "tokens" else inner[1].split()
        if _names_push(words) or _names_push(args):
            if causes is not None:
                causes.append(None)
            return [(PUSH, 3, None, written)]
        return [(SHELL, 3, None, written)]
    if inner is not None and inner[1]:
        if inner[0] == "tokens":
            if _mentions_home(tokens):  # `env HOME=x bash -c '…'` hands the shell that HOME
                variables = dict(variables or {})
                variables[_HOME_UNKNOWN] = True
            if _moves_cd_resolution(tokens):  # and `env CDPATH=w bash -c '…'` its CDPATH
                variables = dict(variables or {})
                variables[_CD_UNKNOWN] = True
            found = []
            for reading in readings or [inner[1]]:
                found.extend(_governed(reading, cwd, depth + 1, variables, causes))
        else:
            # An inner shell inherits HOME: from this line, or from an assignment prefixed to
            # the command that runs it, as `HOME=x bash -c '…'` and `env HOME=x sh -c '…'` do.
            variables = variables or {}
            moved = (_home_unknown(variables) or variables.get(_UNSAFE_OPERANDS, set()) is None
                     or _mentions_home(tokens))
            found = governed_text(inner[1], cwd, depth + 1, causes=causes, home_unknown=moved,
                                  cd_unknown=(_cd_unknown(variables)
                                              or _moves_cd_resolution(tokens)),
                                  cd_untrusted=bool(variables.get(_CD_UNTRUSTED)))
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
    if inner is None and grade > 0 and _names_push(body):
        # `xargs git push`, `$X git push`, a wrapper this hook does not know: may push.
        if causes is not None:
            causes.append(None)
        return [(PUSH, max(grade, 2), None, written)]
    return [(SHELL, grade, cwd, written)]


def governed_text(cmd, cwd, depth=0, isolated=False, causes=None, home_unknown=False,
                  cd_unknown=False, cd_untrusted=False):
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
    runs, so no `~` in it is expanded; with `cd_unknown`, CDPATH or a physical-path option may
    be set, so only a `cd` `_cd_certain` accepts is followed; with `cd_untrusted`, `cd` may not
    be the builtin, or a trap may move the shell, so no `cd` is followed, as `_resets_cd` says."""
    if depth >= MAX_DEPTH:
        return None
    texts, _bodies = _readings(cmd)
    found = []
    if texts is None:  # too long to decompose: unplaced, and graded as `_scan` grades it
        texts = [_outer(cmd)[0]]  # bodies removed where placed; `_scan(cmd)` reads them all
    elif len(texts) == 1:
        return _walk(texts[0], cwd, depth, isolated, causes, home_unknown, cd_unknown,
                     cd_untrusted)
    else:
        for text in texts:
            found.extend(_walk(text, cwd, depth, isolated, causes, home_unknown, cd_unknown,
                               cd_untrusted) or [])
    if not found:
        pushes = any("git" in chunk and "push" in chunk
                     for text in texts for chunk in SCAN_SPLIT.split(text.lower()))
        if pushes and causes is not None:
            causes.append(None)
        # A push is remote-mutating, grade 2, whether or not the line decomposes.
        found = [(PUSH if pushes else SHELL, max(2 if pushes else 1, _scan(cmd)[0]), None, [])]
    return [(action, grade, None, written) for action, grade, _where, written in found]


def _walk(text, cwd, depth, isolated, causes, home_unknown=False, cd_unknown=False,
          cd_untrusted=False):
    """`governed_text` for one normalized reading.

    A line that defines a function `_defines_moving_function` names follows none of its `cd`s:
    the function may be `cd` itself, and its body, walked here as if it ran at once, runs where
    it is called. From a command `_resets_cd` or `_sources` names, or one that runs text the
    walk does not read, as `eval "$x"` and `$t` do, no `cd` is followed and the directory is
    unknown; from one naming a startup file, as `BASH_ENV=f` does, so is every shell it starts."""
    stripped, inners = _extract_subs(text)
    parts = segments(stripped) if stripped is not None else None
    if parts is None:
        return None
    line_wide = isolated or _isolating(stripped)
    confined = None if isolated else _confined(stripped)
    if confined is None or len(confined) != len(parts):
        confined = [None] * len(parts)
    places = _list_places(stripped)
    if places is None or len(places) != len(parts):
        places = [(True, None, True, frozenset(), None)] * len(parts)  # every `cd` may not run
    plain_times = _bare_times(stripped, parts)
    queue = list(inners)
    found = []
    assignment_contexts = _assignment_contexts(stripped, parts, home_unknown, cd_unknown)
    untrusted = cd_untrusted or _defines_moving_function(stripped)
    shells = untrusted  # a shell started from here may read a startup file that moves it

    def substitutions(count, where, variables):
        # A substitution runs in a subshell of this one, with its HOME.
        moved = (_home_unknown(variables)
                 or (variables or {}).get(_UNSAFE_OPERANDS, set()) is None)
        for _ in range(min(count, len(queue))):
            inner = queue.pop(0)
            found.extend(governed_text(inner, where, depth + 1, isolated=True, causes=causes,
                                       home_unknown=moved, cd_unknown=_cd_unknown(variables),
                                       cd_untrusted=untrusted or shells)
                         or [(SHELL, _scan(inner)[0], where, [])])

    # `pending` is (where the last `cd` goes when it runs, its chain) for a `cd` that may not
    # run: a command that runs only through that chain runs there, as `_list_places` says.
    here, moved_in, pending = (None if untrusted else cwd), None, None
    for tokens, alone, variables, (may_skip, in_list, past_or, through, own) in zip(
            parts, confined, assignment_contexts, places):
        # Past a `||` in the list of a `cd`, the command runs where that `cd` failed, or where
        # a command after it failed: `cd d || x` runs `x` where `cd d` did not go.
        if pending is not None and pending[1] in through:
            at = pending[0]
        else:
            at = None if past_or and in_list == moved_in else here
        substitutions(sum(t.count(PLACEHOLDER) for t in tokens), at, variables)
        _targets = _redirects(list(tokens))[1]
        word, args, sure = _command_word(tokens)
        sure = sure and (plain_times or "time" not in tokens)
        head = word.rpartition("/")[2]
        if head in ("cd", "pushd", "popd"):
            # A directory change that also writes, through a redirect, is governed where it runs.
            moved_grade = grade_tokens(list(tokens), at or "", depth)[0]
            if moved_grade > 0:
                found.append((SHELL, moved_grade, at, _written(head, [], _targets, at)))
            if alone is None:
                alone = line_wide
            moved_in = in_list
            # A confined `cd` leaves the directory unknown, not unchanged: zsh runs a
            # pipeline's last element in the current shell, so `x | cd d` moves it there. So
            # does one that may not run, as `true || cd d` does not.
            if (alone or not sure or untrusted or head == "popd"
                    or any(a.startswith("-") for a in args) or len(args) > 1):
                dest = None
            elif head == "pushd" and not args:
                dest = None  # swaps with the directory stack, which this walk does not hold
            elif (args[0] if args else "~").startswith("~") and (
                    _home_unknown(variables) or (not args and _mentions_home(tokens))):
                # HOME may have been reassigned earlier in the line, or for this `cd` alone,
                # as `HOME=x cd` goes to x; bash expands a `~` operand before that assignment.
                dest = None
            elif args and not _cd_certain(args[0]) and (
                    _cd_unknown(variables) or _moves_cd_resolution(tokens)):
                dest = None  # CDPATH, as `CDPATH=w cd b`, or a physical `..`
            elif args and args[0].startswith("~") and QUOTED_TILDE_RE.search(stripped):
                dest = None  # bash keeps a quoted tilde-prefix literal; zsh expands `~"/x"`
            else:
                dest = _static_dir(args[0] if args else "~", at)
            here = None if may_skip else dest
            # `time ! cd d` is negated inside the command, so its successors run where it failed.
            pending = ((dest, own) if may_skip and dest is not None and own is not None
                       and "!" not in tokens else None)
            continue
        shells = shells or any(STARTUP_WORD_RE.search(t) for t in tokens)
        if untrusted or shells:
            variables = dict(variables or {})
            variables[_CD_UNTRUSTED] = True
        found.extend(_governed(tokens, at, depth, variables, causes, plain_times))
        # `eval cd d`, `{cd,d}`, `$c d` or `. s.sh` may move this shell, and may also set a
        # trap or redefine `cd`, as `eval "trap 'cd ..' DEBUG"` does, unless it is an `eval` of
        # literal `_inert` text. A DEBUG trap runs before the very next command.
        if _runs_unseen(tokens) or _sources(tokens):
            here = pending = None
            if not _evals_inert(tokens):
                untrusted = shells = True
        if _resets_cd(tokens):
            untrusted = shells = True
            here = pending = None
    # Any the segments did not account for: fail closed, HOME included.
    substitutions(len(queue), None, {_HOME_UNKNOWN: True, _CD_UNKNOWN: True})
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
        # A walk that raises places nothing: the line runs in a directory not known, a push
        # when it names one, as `governed_text` places a line it cannot decompose.
        pushes = bool(re.search(r"\bgit\b[^;&|]*\bpush\b", command))
        found, unresolved_git_c = None, [None] if pushes else []
    walked = found is not None
    if not found or max(entry[1] for entry in found) <= 0:
        # The grader graded the line above 0 yet no segment carries that grade: govern the whole
        # line at its grade rather than let the walk find nothing to ask about.
        action = PUSH if not walked and unresolved_git_c else SHELL
        found = (found or []) + [(action, grade, cwd if walked else None, [])]
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

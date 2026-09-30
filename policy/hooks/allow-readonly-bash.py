#!/usr/bin/env python3
"""PreToolUse hook: approve read-only Bash commands that Claude Code's built-in
read-only set misses, so plan mode and Manual mode stop prompting for them.

Why a hook and not allow rules: `git -C <dir> status`, `gh repo view`, `npm view`
and friends cannot be expressed as a prefix rule without a wildcard before the
subcommand, which Claude Code warns about and which would also match writes.

Behaviour:
  - Approves only when EVERY command that would run is read-only under the grammar
    below. Compound commands are decomposed first: pipelines and `;`/`&&`/`||`
    sequences, newlines, `for`/`while`/`until`/`if` blocks, subshell `( ... )` and
    group `{ ...; }`, and command substitutions `$(...)` / backticks / `<(...)` are
    each verified, recursively, and the whole thing is approved only if every part
    is. Anything the grammar cannot prove read-only returns no decision and falls
    through to the normal permission flow. This hook never denies.
  - Output redirections to a file (`>`, `>>`, `>|`, `&>`, `>&`, `<>`) are never
    approved here; `/dev/null` and fd duplication (`2>&1`) are. Heredocs and
    backslash continuations are not modelled and fall through. A `#` comment ends
    at its line, as it does for bash, so nothing after a comment is hidden from
    the check.
  - Subshells that run a write, `bash -c`, `eval`, `xargs`, `sudo`, `find -exec`
    and `find -delete`, and a command built from a substitution's output are never
    approved here; nor are the write or exec flags of otherwise read-only tools
    (`sort -o`, `fd -x`, `rg --pre`, `sed w`, awk's `system()`), a program run by
    path outside the system bin directories, or an environment assignment that
    steers a later command (`PATH`, `GIT_*`, `NODE_OPTIONS` and the like).

Test: echo '{"tool_name":"Bash","tool_input":{"command":"git -C /x status"}}' | python3 allow-readonly-bash.py
"""
import json
import re
import sys

# Commands that are read-only regardless of arguments. Claude Code still checks
# redirect targets on its own; we refuse file redirects below anyway.
PLAIN = {
    "ls", "cat", "head", "tail", "wc", "grep", "egrep", "fgrep", "find",
    "stat", "du", "df", "pwd", "echo", "printf", "true", "false",
    "test", "[", "which", "type", "whoami", "id", "uname", "sw_vers",
    "printenv", "basename", "dirname", "realpath", "readlink", "uniq", "cut",
    "tr", "jq", "column", "nl", "od", "strings", "md5", "md5sum",
    "shasum", "sha256sum", "diff", "cmp", "comm", "tac", "rev", "seq", "expr",
    "cd", "nproc", "lsof", "ps", "top", "uptime",
    "read", "fold", "paste", "join", "look", "hexdump", "base64", "cksum",
}

# Read-only unless one of these flags appears: a long option matched by prefix, or a
# letter anywhere in a short-option cluster, so `-Hx` is caught the same as `-x`.
FLAGGED = {
    "sort": (("--output", "--compress-program"), "o"),
    "tree": ((), "o"),
    "yq": (("--inplace", "--split-exp"), "is"),
    "fd": (("--exec", "--exec-batch"), "xX"),
    "rg": (("--pre",), ""),
    "file": (("--compile",), "C"),
    "date": (("--set",), "s"),  # plus: no operand other than a +FORMAT
    "sysctl": (("--write",), "w"),
    "hostname": (("--file",), "F"),
    "xxd": ((), ""),
}
# Operands beyond this count name an output file (`xxd in out`) or set state (`hostname x`).
POSITIONAL_MAX = {"xxd": 1, "hostname": 0}

# Commands read-only only when the arguments match the given regex
# (matched against the argument string after the program name).
PREFIXED = [
    ("gh", r"^(auth status|repo view|repo list|pr view|pr list|pr diff|pr checks|pr status|"
           r"issue view|issue list|run list|run view|release list|release view|label list|"
           r"search \S+|api (-X GET |--method GET )?\S+$|--version)"),
    ("npm", r"^(view|info|show|ls|list|outdated|why|explain|--version|-v)(\s|$)"),
    ("pnpm", r"^(ls|list|why|outdated|--version|-v)(\s|$)"),
    ("yarn", r"^(info|why|--version|-v)(\s|$)"),
    ("cargo", r"^(metadata|tree|--version|-V)(\s|$)"),
    ("uv", r"^(pip list|pip show|tree|--version|-V)(\s|$)"),
    ("python3", r"^--version$"),
    ("python", r"^--version$"),
    ("node", r"^--version$"),
    ("claude", r"^--version$"),
    ("rustc", r"^--version$"),
    ("brew", r"^(list|info|--version|--prefix)(\s|$)"),
    ("aws", r"^sts get-caller-identity(\s|$)"),
]

# Variables that change which program runs or what it executes: assigning one, even
# without `export`, is a way to steer an approved command. `PAGER=cat` is the one
# idiom worth keeping.
DANGEROUS_ENV = re.compile(
    r"^(PATH|LD_|DYLD_|GIT_|PAGER|LESS|EDITOR|VISUAL|NODE_OPTIONS|PYTHON|PERL|RUBY|BASH_ENV|"
    r"ENV$|IFS|CDPATH|GLOBIGNORE|HOME|SHELL|TMPDIR|PS4|PROMPT_COMMAND|AWKPATH|AWKLIBPATH|"
    r"GREP_OPTIONS|BROWSER|MANPATH|GH_|NPM_|CARGO|RUSTC|GOFLAGS|GOENV|UV_|HOMEBREW|SSH_|"
    r"PYENV|NVM|JAVA|_JAVA|CLASSPATH|JQ_)"
)
PAGER_OK = re.compile(r"^(PAGER|GIT_PAGER)=(cat)?$")

# A program named by absolute path is approved only from these directories; a
# repository can ship a `bin/cat` of its own.
SAFE_BIN_DIRS = {"/bin", "/usr/bin", "/usr/local/bin", "/opt/homebrew/bin", "/sbin", "/usr/sbin"}

# git subcommands that are read-only with any arguments.
GIT_ANY = {
    "status", "log", "diff", "show", "rev-parse", "ls-files", "ls-tree", "check-ignore",
    "blame", "describe", "shortlog", "cat-file", "rev-list", "name-rev", "merge-base",
    "count-objects", "for-each-ref", "show-ref", "var", "diff-tree", "diff-index",
    "diff-files", "grep", "whatchanged", "version", "--version", "help",
}

GIT_ARGS_WRITE = re.compile(r"^(--output|--open-files-in-pager|-O)")

# awk: shell escapes, file output, script files and extension loading.
AWK_FORBIDDEN = re.compile(r"system\s*\(|getline|[|>]|@load|@include|^-[filE]|^--(file|include|load|exec)")

FIND_FORBIDDEN = {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"}
NEVER = {"sudo", "eval", "exec", "bash", "sh", "zsh", "xargs", "source", "."}

# Metacharacters that separate commands wherever they appear.
ALWAYS_DELIM = {";", "&&", "||", "|", "|&", "&", "(", ")", ";;"}
# Reserved words are structural only in command position (see command_ok). As an
# argument, e.g. `grep -q done`, the same word is ordinary data.
#   WORD_DROP    separate commands but carry none to check.
#   WORD_COND    introduce a condition command whose remainder must be checked.
#   WORD_HEADER  introduce a loop/case header whose words are data, not commands.
WORD_DROP = {"do", "done", "then", "fi", "else", "esac", "{", "}"}
WORD_COND = {"while", "until", "if", "elif"}
WORD_HEADER = {"for", "select", "case"}

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PLACEHOLDER = "__ROSUB__"  # stands in for a verified substitution; never a real command

# Redirection tokens that are safe on their own: input redirects (their operand is
# read, never written) and fd duplication.
READ_REDIRECTS = {"<", "<<", "<<<", "<&"}
# A here-document operator anywhere in the text, `<<<` aside. Matched on the raw text, inside
# quotes too: refusing a quoted `<<` costs a prompt, missing a real one approves its body's lines.
HEREDOC_OPERATOR = re.compile(r"(?<!<)<<(?!<)")
# The longest command this hook reads; a longer one falls through unread.
MAX_LENGTH = 10000
WRITE_REDIRECTS = re.compile(r"^\d*(?:&?>>?[!|]?|>&|<>)$")
PUNCTUATION_RUN = re.compile(r"^\d*[<>&|]+!?$")


# Characters that begin an operator outside quotes, and the operators they spell, longest
# first. `;&` and `;;&` are not listed, so they split into delimiters and fail closed.
OPERATOR_CHARS = "();<>|&"
OPERATORS = ("&>>!", "&>>|", "&>!", "&>|", ">>!", ">>|", ">!", "&>>", "<<<", "&&", "||", ";;", "|&", "&>", ">>", ">&", ">|", "<<", "<&", "<>",
             ";", "&", "|", "(", ")", "<", ">")
_WHITESPACE = " \t\r\n"


def _operators(run):
    """An unquoted run of operator characters as the operators bash reads in it: the longest
    operator at each position, left to right, so `);` is `)` then `;`."""
    out, i = [], 0
    while i < len(run):
        op = next(o for o in OPERATORS if run.startswith(o, i))
        out.append(op)
        i += len(op)
    return out


def _ansi_end(s, i):
    """The index just past the ANSI-C string `$'…'` opening at s[i], or None when it never
    closes. Inside it a backslash escapes the next character, so `\\'` is a quote and not its
    end, unlike in a single-quoted string."""
    j, n = i + 2, len(s)
    while j < n:
        if s[j] == "\\":
            j += 2
        elif s[j] == "'":
            return j + 1
        else:
            j += 1
    return None


ANSI_ESCAPES = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r",
                "t": "\t", "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}
ANSI_HEX = {"x": 2, "u": 4, "U": 8}


def _ansi_decode(body):
    """The text of an ANSI-C string's body, its escapes decoded as bash 5 does, cut at the first
    NUL: bash drops the rest of the string, and a command's arguments end there anyway, so
    `$'push\\0x'` is `push`. Raises ValueError on an escape that names no character."""
    out, i, n = [], 0, len(body)
    while i < n:
        c = body[i]
        if c != "\\" or i + 1 >= n:
            out.append(c)
            i += 1
            continue
        e = body[i + 1]
        if e in ANSI_ESCAPES:
            out.append(ANSI_ESCAPES[e])
            i += 2
        elif e in "01234567":
            j = i + 1
            while j < n and j < i + 4 and body[j] in "01234567":
                j += 1
            out.append(chr(int(body[i + 1:j], 8) & 0xFF))
            i = j
        elif e in ANSI_HEX:
            j = i + 2
            while j < n and j < i + 2 + ANSI_HEX[e] and body[j] in "0123456789abcdefABCDEF":
                j += 1
            if j == i + 2:
                out.append(body[i:j])
            else:
                out.append(chr(int(body[i + 2:j], 16)))  # ValueError past the Unicode range
            i = j
        elif e == "c" and i + 2 < n:
            out.append(chr(ord(body[i + 2]) & 0x1F))
            i += 3
        else:
            out.append(body[i:i + 2])
            i += 2
    return "".join(out).split("\x00", 1)[0]


def tokenize(cmd, strict=False):
    """The words and operators of `cmd`, as POSIX `shlex` with `punctuation_chars` splits them,
    except that an unquoted run of operator characters is split into its operators. `shlex`
    returns such a run as one token, so `(true);` ended in `);`, which is not a delimiter.
    A quoted or escaped operator character is part of a word. Raises ValueError on an unclosed
    quote or a trailing backslash, as `shlex` does.

    Unlike `shlex` it reads bash's `$'…'`, decoding its escapes, and `$"…"`, as a double-quoted
    string: `$'-delete'` is the word `-delete`. `$$` is one parameter, so the quote after it is
    an ordinary one. With `strict` it raises ValueError instead on an ANSI-C string holding a
    backslash, whose decoding can differ between shells and versions, and on `$"…"`, whose
    text a message catalog may replace."""
    tokens = []
    word = None  # None: no word in progress; "" is an empty quoted word
    i, n = 0, len(cmd)
    while i < n:
        c = cmd[i]
        if c in _WHITESPACE or c in OPERATOR_CHARS:
            if word is not None:
                tokens.append(word)
                word = None
            if c in _WHITESPACE:
                i += 1
                continue
            j = i
            while j < n and cmd[j] in OPERATOR_CHARS:
                j += 1
            if j < n and cmd[j] == "!" and cmd[j - 1] == ">":
                j += 1  # zsh force-clobber redirect; ! elsewhere remains an ordinary word.
            tokens.extend(_operators(cmd[i:j]))
            i = j
        elif c == "\\":
            if i + 1 >= n:
                raise ValueError("No escaped character")
            word = (word or "") + cmd[i + 1]
            i += 2
        elif c == "'":
            end = cmd.find("'", i + 1)
            if end < 0:
                raise ValueError("No closing quotation")
            word = (word or "") + cmd[i + 1:end]
            i = end + 1
        elif c == '"':
            buf, j = [], i + 1
            while j < n and cmd[j] != '"':
                if cmd[j] == "\\" and j + 1 < n:
                    if cmd[j + 1] not in '"\\':
                        buf.append("\\")  # only a quote or a backslash is escaped here
                    buf.append(cmd[j + 1])
                    j += 2
                    continue
                buf.append(cmd[j])
                j += 1
            if j >= n:
                raise ValueError("No closing quotation")
            word = (word or "") + "".join(buf)
            i = j + 1
        elif c == "$" and cmd.startswith("$$", i):
            word = (word or "") + "$$"
            i += 2
        elif c == "$" and cmd.startswith("$'", i):
            end = _ansi_end(cmd, i)
            if end is None:
                raise ValueError("No closing quotation")
            body = cmd[i + 2:end - 1]
            if strict and "\\" in body:
                raise ValueError("ANSI-C escape")
            word = (word or "") + _ansi_decode(body)
            i = end
        elif c == "$" and cmd.startswith('$"', i):
            if strict:
                raise ValueError("locale-translated string")
            word = word or ""
            i += 1  # the double-quoted string that follows is read as one
        else:
            word = (word or "") + c
            i += 1
    if word is not None:
        tokens.append(word)
    return tokens


def assignment_ok(token):
    """`NAME=value` may precede a command, or stand alone, only when NAME cannot
    steer what a later approved command runs."""
    return PAGER_OK.match(token) is not None or DANGEROUS_ENV.match(token) is None


def flags_hit(args, longs, shorts):
    for a in args:
        if a == "--":
            break
        if a.startswith("--"):
            if any(a.startswith(l) for l in longs):
                return True
        elif a.startswith("-") and len(a) > 1:
            if any(ch in shorts for ch in a[1:]):
                return True
    return False


def positionals(args):
    out, opts_done = [], False
    for a in args:
        if a == "--":
            opts_done = True
        elif opts_done or not a.startswith("-"):
            out.append(a)
    return out


def _skip_delimited(s, i, delim):
    """Index just past the next unescaped `delim` from s[i], honouring bracket
    expressions; None when the section never closes."""
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == "[":
            j = i + 1
            if j < n and s[j] == "^":
                j += 1
            if j < n and s[j] == "]":
                j += 1
            while j < n and s[j] != "]":
                j += 1
            if j >= n:
                return None
            i = j + 1
            continue
        if c == delim:
            return i + 1
        i += 1
    return None


def sed_script_ok(s):
    """True when a sed script only prints, edits the pattern space, branches or
    quits — never `w`, `W`, `e` or the `w`/`e` flags of `s`."""
    n = len(s)
    i = 0
    while i < n:
        c = s[i]
        if c in " \t\n;":
            i += 1
            continue
        if c == "#":
            while i < n and s[i] != "\n":
                i += 1
            continue
        # Addresses: N, $, /re/, \cREc, optional ~step, I/M flags, a comma and a second one.
        while i < n:
            c = s[i]
            if c.isdigit() or c in "$+~":
                while i < n and (s[i].isdigit() or s[i] in "$~+"):
                    i += 1
            elif c == "/":
                i = _skip_delimited(s, i + 1, "/")
            elif c == "\\" and i + 1 < n:
                i = _skip_delimited(s, i + 2, s[i + 1])
            else:
                break
            if i is None:
                return False
            while i < n and s[i] in "IM":
                i += 1
            while i < n and s[i] in " \t":
                i += 1
            if i < n and s[i] == ",":
                i += 1
                while i < n and s[i] in " \t":
                    i += 1
                continue
            break
        while i < n and s[i] in " \t!":
            i += 1
        if i >= n:
            return True
        cmd = s[i]
        i += 1
        if cmd in "wWe":
            return False
        if cmd in "{}pPnNdDhHgGxz=F":
            continue
        if cmd in ":btT" or cmd in "rR" or cmd in "aic":
            while i < n and s[i] != "\n":
                i += 1
            continue
        if cmd in "qQlLv":
            while i < n and s[i].isdigit():
                i += 1
            continue
        if cmd in "sy":
            if i >= n:
                return False
            delim = s[i]
            i = _skip_delimited(s, i + 1, delim)
            if i is None:
                return False
            i = _skip_delimited(s, i, delim)
            if i is None:
                return False
            if cmd == "s":
                while i < n and s[i] in "gpImM0123456789":
                    i += 1
                if i < n and s[i] not in " \t\n;}":
                    return False  # `e`, `w file`, or a flag this parser does not know
            continue
        return False
    return True


def sed_ok(args):
    """`sed -n` with inline scripts that never write or execute: no -i, no -f, no w/e."""
    scripts, files, i, saw_n = [], [], 0, False
    while i < len(args):
        a = args[i]
        if a == "--":
            files.extend(args[i + 1:])
            break
        if a.startswith("--"):
            name, eq, value = a.partition("=")
            if name in ("--quiet", "--silent"):
                saw_n = True
            elif name == "--expression":
                if eq:
                    scripts.append(value)
                elif i + 1 < len(args):
                    scripts.append(args[i + 1])
                    i += 1
                else:
                    return False
            elif name in ("--regexp-extended", "--separate", "--unbuffered", "--null-data",
                          "--posix", "--debug", "--sandbox", "--line-length"):
                if name == "--line-length" and not eq:
                    i += 1
            else:
                return False
        elif a.startswith("-") and len(a) > 1:
            letters = a[1:]
            for k, ch in enumerate(letters):
                if ch == "n":
                    saw_n = True
                elif ch in "Ersuz":
                    pass
                elif ch == "e":
                    rest = letters[k + 1:]
                    if rest:
                        scripts.append(rest)
                    elif i + 1 < len(args):
                        scripts.append(args[i + 1])
                        i += 1
                    else:
                        return False
                    break
                elif ch == "l":
                    if not letters[k + 1:]:
                        i += 1
                    break
                else:
                    return False  # -i, -f, and anything unknown
        else:
            (files if scripts else scripts).append(a)
        i += 1
    return saw_n and bool(scripts) and all(sed_script_ok(s) for s in scripts)


def git_ok(args):
    """args: list of tokens after `git`. Strips -C <dir>, --no-pager, -P."""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-C" and i + 1 < len(args):
            i += 2
            continue
        if a in ("--no-pager", "-P"):
            i += 1
            continue
        if a.startswith("-"):
            return False  # -c key=val and unknown globals are not approved
        break
    rest = args[i:]
    if not rest:
        return False
    sub, sargs = rest[0], rest[1:]
    if any(GIT_ARGS_WRITE.match(s) for s in sargs):
        return False
    if sub in GIT_ANY:
        return True
    if sub == "branch":
        ro = {"-a", "-r", "-v", "-vv", "--all", "--remotes", "--list", "-l", "--show-current",
              "--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--verbose"}
        listing = any(s in ("--list", "-l") for s in sargs)
        for s in sargs:
            if s.startswith("--format=") or s.startswith("--sort="):
                continue
            if s in ro:
                continue
            if not s.startswith("-") and listing:
                continue
            return False
        return True
    if sub == "remote":
        return not sargs or sargs[0] in ("-v", "--verbose", "show", "get-url")
    if sub == "tag":
        return not sargs or sargs[0] in ("-l", "--list", "-n", "--contains", "--points-at")
    if sub == "stash":
        return bool(sargs) and sargs[0] in ("list", "show")
    if sub == "worktree":
        return bool(sargs) and sargs[0] == "list"
    if sub == "submodule":
        return bool(sargs) and sargs[0] == "status"
    if sub == "reflog":
        return not sargs or sargs[0] not in ("expire", "delete")
    if sub == "config":
        getters = {"--get", "--get-all", "--get-regexp", "--list", "-l"}
        writers = {"--unset", "--unset-all", "--add", "--replace-all", "--edit", "-e",
                   "--remove-section", "--rename-section"}
        if any(s in writers for s in sargs) or not any(s in getters for s in sargs):
            return False
        positional = [s for s in sargs if not s.startswith("-")]
        return len(positional) <= 1
    if sub == "symbolic-ref":
        return len([s for s in sargs if not s.startswith("-")]) <= 1
    return False


def strip_redirects(tokens):
    """Tokens with safe redirections removed; None when any redirection writes a file."""
    cleaned = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if not PUNCTUATION_RUN.match(t):
            cleaned.append(t)
            i += 1
            continue
        target = tokens[i + 1] if i + 1 < len(tokens) else ""
        if t in READ_REDIRECTS:
            i += 2
        elif re.match(r"^\d*>&$", t) and re.match(r"^\d+$", target):
            i += 2  # 2>&1
        elif WRITE_REDIRECTS.match(t) and target == "/dev/null":
            i += 2
        else:
            return None  # a file is written, or an operator this hook does not model
    return cleaned


# Wrappers that run the command after their own words; `arch` runs one only when given any.
WRAPPERS = ("timeout", "time", "nice", "nohup", "stdbuf", "command", "noglob")
# The most wrappers read in a row: a longer run is not approved.
WRAP_CAP = 32


def segment_ok(tokens):
    """True when a single simple command (already free of substitutions and of the
    structural keywords) is read-only. A run of wrappers is read in a loop, never by
    recursion, so no run of them can exhaust the stack."""
    for _ in range(WRAP_CAP):
        verdict = _segment_step(tokens)
        if not isinstance(verdict, list):
            return verdict
        tokens = verdict
    return False


def _segment_step(tokens):
    """`segment_ok` for one command: True or False, or the words a wrapper runs."""
    tokens = strip_redirects(tokens)
    if not tokens:
        return False
    # Strip leading assignments (LANG=C, S=/path, NAME=value cmd ...). A segment
    # that is nothing but assignments runs no command, so it is read-only — unless
    # the variable steers a command that runs later in the same call.
    while tokens and ASSIGN_RE.match(tokens[0]):
        if not assignment_ok(tokens[0]):
            return False
        tokens = tokens[1:]
    if not tokens:
        return True
    for t in tokens:
        if "$(" in t or "`" in t or "<(" in t or ">(" in t:
            return False  # an unextracted substitution: fail closed
    head = tokens[0]
    if "/" in head:
        base, _, prog = head.rpartition("/")
        if base not in SAFE_BIN_DIRS:
            return False  # a relative path, or a binary outside the system directories
    else:
        prog = head
    args = tokens[1:]
    if prog in NEVER:
        return False
    if prog in WRAPPERS:
        return list(args[1:] if prog == "timeout" and args else args)
    if prog == "arch":
        return not args  # `arch -arm64 cmd` runs cmd
    if prog == "env":
        while args and ASSIGN_RE.match(args[0]):
            if not assignment_ok(args[0]):
                return False
            args = args[1:]
        if not args:
            return True
        return False if args[0].startswith("-") else list(args)
    if prog == "export":
        return bool(args) and all(
            NAME_RE.match(a.split("=", 1)[0]) is not None and assignment_ok(a) for a in args)
    if prog == "find":
        return not any(a in FIND_FORBIDDEN for a in args)
    if prog in PLAIN:
        return True
    if prog in FLAGGED:
        longs, shorts = FLAGGED[prog]
        if flags_hit(args, longs, shorts):
            return False
        if prog in POSITIONAL_MAX and len(positionals(args)) > POSITIONAL_MAX[prog]:
            return False
        if prog == "sysctl" and any("=" in a for a in args):
            return False
        if prog == "date" and any(not a.startswith("+") for a in positionals(args)):
            return False  # a bare MMDDhhmm operand sets the clock
        return True
    if prog == "awk":
        return not any(AWK_FORBIDDEN.search(a) for a in args)
    if prog == "sed":
        return sed_ok(args)
    if prog == "git":
        return git_ok(args)
    if prog == "go":
        return bool(args) and (args[0] == "version" or (args[0] == "env" and not flags_hit(args[1:], (), "wu")))
    for name, pattern in PREFIXED:
        if prog == name:
            return re.match(pattern, " ".join(args)) is not None
    if args == ["--version"] and "/" not in head:
        return True
    return False


def _match_paren(s, start):
    """s[start] == '('. Return the index of the matching ')', or None. Quote-aware, ANSI-C
    `$'…'` strings included."""
    depth = 0
    i = start
    n = len(s)
    sq = dq = False
    while i < n:
        c = s[i]
        if sq:
            if c == "'":
                sq = False
        elif dq:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                dq = False
        else:
            if c == "$" and s.startswith("$$", i):
                i += 2
                continue
            if c == "$" and s.startswith("$'", i):
                i = _ansi_end(s, i)
                if i is None:
                    return None
                continue
            if c == "'":
                sq = True
            elif c == '"':
                dq = True
            elif c == "\\":
                i += 2
                continue
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return None


def _strip_subs(cmd, depth):
    """Replace every command/process/arithmetic substitution in `cmd` with a
    placeholder, verifying each command substitution is itself read-only. Returns
    the rewritten string, or None if any substitution is not read-only or the text
    does not parse."""
    out = []
    i = 0
    n = len(cmd)
    sq = dq = False
    while i < n:
        c = cmd[i]
        if sq:
            out.append(c)
            if c == "'":
                sq = False
            i += 1
            continue
        if c == "$" and cmd.startswith("$$", i):
            out.append("$$")
            i += 2
            continue
        if c == "$" and not dq and cmd.startswith("$'", i):
            end = _ansi_end(cmd, i)
            if end is None:
                return None
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
                return None
            inner = cmd[i + 1:j].replace("\\`", "`").replace("\\$", "$")
            if not command_ok(inner, depth + 1):
                return None
            out.append(_PLACEHOLDER)
            i = j + 1
            continue
        if cmd.startswith("$(", i):
            end = _match_paren(cmd, i + 1)
            if end is None:
                return None
            inner = cmd[i + 2:end]
            if not inner.startswith("("):  # a plain '(' opener is arithmetic $(( )), no command
                if not command_ok(inner, depth + 1):
                    return None
            out.append(_PLACEHOLDER)
            i = end + 1
            continue
        if c in "<>" and not dq and cmd.startswith("(", i + 1):
            end = _match_paren(cmd, i + 1)
            if end is None:
                return None
            inner = cmd[i + 2:end]
            if not command_ok(inner, depth + 1):
                return None
            out.append(_PLACEHOLDER)
            i = end + 1
            continue
        out.append(c)
        i += 1
    if sq or dq:
        return None
    return "".join(out)


def _strip_comments(text):
    """`text` without its bash comments: an unquoted `#` at the start of a word, to the end of
    its line. Quote state carries across newlines, as bash reads it, so a `#` on a later line
    of a quoted string is text and a quote inside a comment opens nothing. Substitutions are
    already placeholders, so only quotes, ANSI-C `$'…'` strings included, need tracking."""
    out, quote, i, n = [], None, 0, len(text)
    while i < n:
        c = text[i]
        if quote == "'":
            quote = None if c == "'" else quote
        elif quote == '"':
            if c == "\\":
                out.append(text[i:i + 2])
                i += 2
                continue
            if c == '"':
                quote = None
        elif c == "\\":
            out.append(text[i:i + 2])
            i += 2
            continue
        elif c == "$" and text.startswith("$$", i):
            out.append("$$")
            i += 2
            continue
        elif c == "$" and text.startswith("$'", i):
            end = _ansi_end(text, i) or n
            out.append(text[i:end])
            i = end
            continue
        elif c in "'\"":
            quote = c
        elif c == "#" and (i == 0 or text[i - 1] in " \t\n;|&()"):
            end = text.find("\n", i)
            i = n if end < 0 else end
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _header_ok(tokens):
    """A `for NAME [in WORDS]` / `select NAME ...` header runs no command; its words
    are data. Accept the well-formed shapes; reject C-style `for (( ))` and `case`."""
    kw = tokens[0]
    if kw in ("for", "select"):
        if len(tokens) < 2 or not NAME_RE.match(tokens[1]):
            return False
        return len(tokens) == 2 or tokens[2] == "in"
    return False  # `case` headers are not decomposed here; fail closed


def command_ok(cmd, depth=0):
    if depth > 6 or len(cmd) > MAX_LENGTH:
        return False
    cmd = _strip_subs(cmd, depth)
    if cmd is None:
        return False
    # A newline separates commands for bash but is whitespace to `tokenize`, so the text
    # loses its comments and the lines are joined with `;`. `tokenize` knows no
    # comments: a `#` inside a word is part of the word, as in bash. A backslash
    # continuation and a here-document, whose body bash does not read as commands, are
    # not modelled and fall through.
    if re.search(r"\\\r?\n", cmd) or HEREDOC_OPERATOR.search(cmd):
        return False
    cmd = cmd.replace("\r\n", "\n").replace("\r", "\n")
    cmd = " ; ".join(_strip_comments(cmd).split("\n"))
    try:
        tokens = tokenize(cmd, strict=True)
    except ValueError:
        return False
    segments = []
    cur = []
    for t in tokens:
        if t in ALWAYS_DELIM:
            if cur:
                segments.append(cur)
                cur = []
            continue
        if not cur:  # command position: reserved words are structural here only
            if t in WORD_DROP:
                continue
            if t in WORD_COND or t in WORD_HEADER:
                cur = [t]
                continue
        cur.append(t)
    if cur:
        segments.append(cur)
    if not segments:
        return False
    for seg in segments:
        head = seg[0]
        if head in WORD_HEADER:
            if not _header_ok(seg):
                return False
        elif head in WORD_COND:
            rest = seg[1:]
            if rest and rest[0] == "!":
                rest = rest[1:]
            if not rest or not segment_ok(rest):
                return False
        else:
            rest = seg[1:] if head == "!" else seg
            if not rest or not segment_ok(rest):
                return False
    return True


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    if payload.get("tool_name") != "Bash":
        return
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if command_ok(cmd):
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": "read-only command (allow-readonly-bash hook)",
            }
        }))


if __name__ == "__main__":
    main()

# SPDX-License-Identifier: MIT
"""The workspace map: which folders belong together, worked out from `.code-workspace` files.

The `.code-workspace` files directly inside one folder, the `workspaces_dir` config key, are the
only definition. Nothing is stored or cached: every call parses the files again, so the map can
never disagree with them. A workspace's name is its file stem, and its members are its
`folders[].path` entries in order, each resolved against the file's own directory after `~`
expansion and then through `realpath`. A `uri`-only folder is skipped; a member that does not
exist is reported and left out of `members`.

An optional `<workspaces_dir>/overrides.json`, read with the same reader, pins a folder to one
workspace or, with `null`, to none:

    {"~/repos/shared-lib": "billing", "../scratch": null}

Its folder paths resolve against `workspaces_dir`. An override naming an unknown workspace, or
one that does not contain the folder, is reported and ignored.

A cwd resolves to the member folder that is the longest match on a path boundary, retried
against a linked git worktree's main checkout when nothing matches. Among the workspaces holding
that folder, the first rule that decides wins:

1. `env`: `HARNESS_WORKSPACE` names one of them (`citizen workspace open` sets it).
2. `add-dirs`: exactly one of them has, as its other members, exactly the `--add-dir` folders
   the session was launched with, which is how a VS Code window reveals its workspace.
3. `override` or `override-none`: the overrides file names the folder.
4. `single`: the folder is in one workspace only.
5. `first`: the folder is the first existing member of exactly one of them.

Otherwise the result is `ambiguous`, with the candidates, and nothing is attached.

Standard library only and no import of another `harness_core` module, so a policy hook can load
this file by path. The API:

    read_jsonc(text) -> value                  JSON with comments and trailing commas
    parse_workspace(path) -> dict              name, file, folders, members, missing
    workspace_map(directory) -> dict           workspaces (sorted by name), errors
    read_overrides(directory, names) -> dict   overrides, unknown, errors
    resolve(cwd, directory, env, add_dirs, ...) -> dict
        cwd, folder, workspace, members, rule, candidates
    member_instructions(folder) -> dict        files [(path, text)], scoped [path]
    claude_files(folder) -> [path]             its CLAUDE.md and .claude/CLAUDE.md
"""
import json
import os
import re

SUFFIX = ".code-workspace"
OVERRIDES = "overrides.json"
ENV = "HARNESS_WORKSPACE"
IMPORT_DEPTH = 5
# One instruction file is read up to this many characters, so a huge file cannot stall a hook.
READ_LIMIT = 64 * 1024
CLAUDE_FILES = ("CLAUDE.md", os.path.join(".claude", "CLAUDE.md"))

_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
_IMPORT = re.compile(r"(?<![\w`@])@((?:~|\.{1,2})?/?[^\s`@()\[\]<>\"']+)")
_FENCE = re.compile(r"^\s*(```|~~~)")


def read_jsonc(text):
    """Parse JSON that may hold `//` and `/* */` comments and trailing commas.

    One pass copies string literals intact, honouring backslash escapes, and drops comments;
    then a comma followed only by whitespace before `}` or `]` goes, outside strings only.
    """
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(ch)
            i += 1
    return json.loads(_strip_trailing_commas("".join(out)))


def _strip_trailing_commas(text):
    """Drop a comma before `}` or `]` in the text between string literals, never inside one."""
    result, i, n, start = [], 0, len(text), 0
    while i < n:
        if text[i] == '"':
            result.append(_TRAILING_COMMA.sub(r"\1", text[start:i]))
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            result.append(text[i:j + 1])
            i = start = j + 1
        else:
            i += 1
    result.append(_TRAILING_COMMA.sub(r"\1", text[start:]))
    return "".join(result)


def _resolve_path(raw, base):
    path = os.path.expanduser(raw)
    if not os.path.isabs(path):
        path = os.path.join(base, path)
    return os.path.realpath(path)


def parse_workspace(path):
    """One workspace file as {name, file, folders, members, missing}; raises ValueError if unreadable."""
    path = os.path.realpath(path)
    try:
        with open(path, encoding="utf-8") as handle:
            data = read_jsonc(handle.read())
    except (OSError, ValueError) as exc:
        raise ValueError("%s: %s" % (os.path.basename(path), exc))
    if not isinstance(data, dict) or not isinstance(data.get("folders", []), list):
        raise ValueError("%s: expected an object with a folders list" % os.path.basename(path))
    base = os.path.dirname(path)
    folders = []
    for entry in data.get("folders", []):
        if isinstance(entry, dict) and isinstance(entry.get("path"), str) and entry["path"].strip():
            resolved = _resolve_path(entry["path"], base)
            if resolved not in folders:
                folders.append(resolved)
    name = os.path.basename(path)[:-len(SUFFIX)] if path.endswith(SUFFIX) else os.path.basename(path)
    return {"name": name, "file": path, "folders": folders,
            "members": [f for f in folders if os.path.isdir(f)],
            "missing": [f for f in folders if not os.path.isdir(f)]}


def workspace_map(directory):
    """Every `.code-workspace` file directly in `directory`, parsed; unreadable ones in `errors`."""
    directory = os.path.realpath(os.path.expanduser(directory))
    workspaces, errors = [], []
    try:
        names = sorted(os.listdir(directory))
    except OSError as exc:
        return {"directory": directory, "workspaces": [], "errors": ["%s: %s" % (directory, exc.strerror)]}
    for entry in names:
        if not entry.endswith(SUFFIX) or entry.startswith("."):
            continue
        try:
            workspaces.append(parse_workspace(os.path.join(directory, entry)))
        except ValueError as exc:
            errors.append(str(exc))
    return {"directory": directory, "workspaces": workspaces, "errors": errors}


def read_overrides(directory, names=None):
    """`overrides.json` as {overrides: {folder: name|None}, unknown: {folder: name}, errors: [...]}.

    `names` is the set of known workspace names; when given, an override naming another is moved
    to `unknown`. A missing file is no overrides, not an error.
    """
    directory = os.path.realpath(os.path.expanduser(directory))
    path = os.path.join(directory, OVERRIDES)
    result = {"overrides": {}, "unknown": {}, "errors": []}
    if not os.path.exists(path):
        return result
    try:
        with open(path, encoding="utf-8") as handle:
            data = read_jsonc(handle.read())
    except (OSError, ValueError) as exc:
        result["errors"].append("%s: %s" % (OVERRIDES, exc))
        return result
    if not isinstance(data, dict):
        result["errors"].append("%s: expected an object of folder to workspace name or null" % OVERRIDES)
        return result
    for raw, value in data.items():
        if value is not None and not isinstance(value, str):
            result["errors"].append("%s: %s must map to a workspace name or null" % (OVERRIDES, raw))
            continue
        folder = _resolve_path(raw, directory)
        if value is not None and names is not None and value not in names:
            result["unknown"][folder] = value
        else:
            result["overrides"][folder] = value
    return result


def _within(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _read_small(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read(4096)
    except (OSError, UnicodeDecodeError):
        return None


def _main_checkout(cwd):
    """The same place in a linked worktree's main checkout, or None when cwd is not in one.

    Plain file reads, no `git`, so a session hook stays inside its budget: walk up to the `.git`
    file, follow its `gitdir:` line, then that directory's `commondir`. A `.git` directory is a
    main checkout, and a `.git` file with no `commondir` (a submodule) is not a linked worktree.
    """
    top = cwd
    while not os.path.isfile(os.path.join(top, ".git")):
        if os.path.isdir(os.path.join(top, ".git")):
            return None
        parent = os.path.dirname(top)
        if parent == top:
            return None
        top = parent
    text = _read_small(os.path.join(top, ".git")) or ""
    line = text.strip().splitlines()[0] if text.strip() else ""
    if not line.startswith("gitdir:"):
        return None
    git_dir = os.path.realpath(os.path.join(top, line[len("gitdir:"):].strip()))
    common = _read_small(os.path.join(git_dir, "commondir"))
    if not common or not common.strip():
        return None
    main = os.path.dirname(os.path.realpath(os.path.join(git_dir, common.strip())))
    rel = os.path.relpath(cwd, top)
    return os.path.realpath(main if rel == "." else os.path.join(main, rel))


def _match(path, workspaces):
    best = None
    for ws in workspaces:
        for member in ws["members"]:
            if _within(path, member) and (best is None or len(member) > len(best)):
                best = member
    return best


def resolve(cwd, directory, env=None, add_dirs=None, ws_map=None, overrides=None):
    """Which workspace a session in `cwd` belongs to, by the precedence in the module docstring.

    `env` defaults to `os.environ`; pass `{}` to ignore launch facts. `add_dirs` is the session's
    `--add-dir` folders, when the caller can see them. `ws_map` and `overrides` take the results
    of `workspace_map` and `read_overrides` to save a second parse.

    Returns {cwd, folder, workspace, members, rule, candidates}: `workspace` is the attached
    workspace's dict or None, `members` its existing members, `rule` the rule that decided (or
    `none` when the cwd is in no workspace, `ambiguous` when nothing decided), and `candidates`
    the names of every workspace holding the folder.
    """
    env = os.environ if env is None else env
    ws_map = workspace_map(directory) if ws_map is None else ws_map
    workspaces = ws_map["workspaces"]
    if overrides is None:
        overrides = read_overrides(directory, {ws["name"] for ws in workspaces})
    cwd = os.path.realpath(os.path.expanduser(cwd))
    result = {"cwd": cwd, "folder": None, "workspace": None, "members": [], "rule": "none",
              "candidates": []}
    folder = _match(cwd, workspaces)
    if folder is None:
        main = _main_checkout(cwd)
        folder = _match(main, workspaces) if main else None
    if folder is None:
        return result
    candidates = [ws for ws in workspaces if folder in ws["members"]]
    result.update(folder=folder, candidates=[ws["name"] for ws in candidates])

    def attach(ws, rule):
        result.update(workspace=ws, members=list(ws["members"]), rule=rule)
        return result

    named = env.get(ENV)
    for ws in candidates:
        if named and ws["name"] == named:
            return attach(ws, "env")
    if add_dirs:
        given = {os.path.realpath(os.path.expanduser(p)) for p in add_dirs}
        exact = [ws for ws in candidates if set(ws["members"]) - {folder} == given]
        if len(exact) == 1:
            return attach(exact[0], "add-dirs")
    pinned = overrides.get("overrides", {})
    if folder in pinned:
        name = pinned[folder]
        if name is None:
            result["rule"] = "override-none"
            return result
        for ws in candidates:
            if ws["name"] == name:
                return attach(ws, "override")
    if len(candidates) == 1:
        return attach(candidates[0], "single")
    first = [ws for ws in candidates if ws["members"][0] == folder]
    if len(first) == 1:
        return attach(first[0], "first")
    result["rule"] = "ambiguous"
    return result


def _frontmatter_has_paths(text):
    if not text.startswith("---"):
        return False
    lines = text.splitlines()
    for line in lines[1:]:
        if line.strip() == "---":
            return False
        if line.startswith("paths:"):
            return True
    return False


def _imports(path, text):
    found, fenced = [], False
    base = os.path.dirname(path)
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        for raw in _IMPORT.findall(re.sub(r"`[^`]*`", "", line)):
            target = _resolve_path(raw.rstrip(".,;:"), base)
            if os.path.isfile(target):
                found.append(target)
    return found


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read(READ_LIMIT)
    except (OSError, UnicodeDecodeError):
        return None


def claude_files(folder):
    """The folder's project instruction files Claude Code loads: `CLAUDE.md`, `.claude/CLAUDE.md`."""
    return [os.path.join(folder, name) for name in CLAUDE_FILES
            if os.path.isfile(os.path.join(folder, name))]


def member_instructions(folder):
    """The instruction files a session outside `folder` needs from it, in load order.

    `CLAUDE.md` and `.claude/CLAUDE.md`, else `AGENTS.md`; then `CLAUDE.local.md` and each `.claude/rules/*.md` without
    `paths:` frontmatter; each followed by the files it `@`-imports, relative to itself, to depth
    five and outside code. A rule scoped by `paths:` is listed in `scoped` by path only.
    Returns {files: [(path, text)], scoped: [path]}.
    """
    files, scoped, seen = [], [], set()

    def add(path, depth):
        real = os.path.realpath(path)
        if real in seen:
            return
        text = _read(path)
        if text is None:
            return
        seen.add(real)
        files.append((path, text))
        if depth < IMPORT_DEPTH:
            for target in _imports(real, text):
                add(target, depth + 1)

    primary = claude_files(folder) or [os.path.join(folder, "AGENTS.md")]
    for path in primary + [os.path.join(folder, "CLAUDE.local.md")]:
        if os.path.isfile(path):
            add(path, 0)
    rules = os.path.join(folder, ".claude", "rules")
    if os.path.isdir(rules):
        for name in sorted(os.listdir(rules)):
            path = os.path.join(rules, name)
            if not name.endswith(".md") or not os.path.isfile(path):
                continue
            text = _read(path)
            if text is not None and _frontmatter_has_paths(text):
                scoped.append(path)
            elif text is not None:
                add(path, 0)
    return {"files": files, "scoped": scoped}

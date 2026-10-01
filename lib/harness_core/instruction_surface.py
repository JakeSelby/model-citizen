# SPDX-License-Identifier: MIT
"""Every instruction source a Claude Code session loads, harness-owned or not, with its tokens.

`citizen usage --surface` prints this. A source is one thing that puts text in the session's
context, with an owner:

- **harness**: the harness's modules, taken whole from `posture.context_attribution`, the one
  definition, so the two figures cannot drift; plus harness-linked instruction files the
  attribution does not cover, such as the user-level `CLAUDE.md` link;
- **own**: the user's own files under the Claude directory: instructions, imports, rules, the
  listing entries of skills, agents and commands, the selected output style, auto memory;
- **project**: `CLAUDE.md`, `.claude/CLAUDE.md` and `CLAUDE.local.md` from the working directory
  up to the filesystem root (or the `AGENTS.md` files instead, when none exists), the project's
  rules and listings, and every `@path` import, expanded to Claude Code's depth of four hops;
- **plugin**, **mcp**, **hooks**: each enabled plugin, configured MCP server and configured hook
  event, always `unmeasured`: their text is served by the CLI at run time or arrives per event,
  so no file here says how much of it reaches the context. Never 0;
- **runtime**: the CLI's own system prompt and built-in tool definitions, `unmeasured`.

A token figure is a soft estimate, characters over four of the text as read, labelled with its
method; a file that cannot be read is `unmeasured` with the reason. A user-level file that links
into the harness checkout is a harness module the attribution already counts, so it is skipped,
never counted twice. The loading rules follow Claude Code's memory, settings and plugin
documentation as of 2026-10; a source whose loading the CLI decides at run time is unmeasured.

Nothing here writes or resolves a selection: the caller hands in the home, the working directory,
the harness checkout and the attribution, so the enumeration is testable on its own and this
module never sends a user's file anywhere. The report is a local diagnostic.
"""
import json
import os
import re
from collections import namedtuple

#: One source: its id, owner, kind, path (or None), tokens (or None), estimand, method, reason.
Source = namedtuple("Source", "id owner kind path tokens estimand method reason")

OWNERS = ("harness", "own", "project", "plugin", "mcp", "hooks", "runtime")
SOFT_ESTIMATE = "soft estimate"
UNMEASURED = "unmeasured"
CHARS_PER_TOKEN = 4.0
FILE_METHOD = "chars/4 of the file as read"
LISTING_METHOD = "chars/4 of the listing entry: name and description"
IMPORT_DEPTH = 4  # Claude Code: "a maximum depth of four hops"
MEMORY_LINES, MEMORY_BYTES = 200, 25 * 1024  # what auto memory loads of MEMORY.md
INSTRUCTION_NAMES = ("CLAUDE.md", os.path.join(".claude", "CLAUDE.md"), "CLAUDE.local.md")
AGENTS_NAMES = ("AGENTS.md", os.path.join(".claude", "AGENTS.md"))
IMPORT = re.compile(r"(?<![\w`])@((?:\\ |[^\s`])+)")
RUNTIME_REASON = "the CLI's own system prompt and built-in tool definitions are served at run time"
MCP_REASON = "tool definitions are served by the server at run time"
HOOK_REASON = "hook output arrives per event; the decision log attributes it"
PLUGIN_REASON = "a plugin's skills, agents and hooks are resolved by the CLI at run time"


def tokens_of(text):
    return int(round(len(text) / CHARS_PER_TOKEN))


def _read(path):
    """(text, None) or (None, reason)."""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read(), None
    except (OSError, ValueError) as exc:
        return None, "unreadable: %s" % (exc.strerror or type(exc).__name__ if isinstance(exc, OSError)
                                          else type(exc).__name__)


#: Where the harness lives: its checkouts, and the exact paths its sync linked or wrote.
Places = namedtuple("Places", "roots links")


def _norm(path):
    """`path` with its directory resolved and its own name kept, so a link is named, not followed."""
    path = os.path.abspath(path)
    return os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))


def _inside(path, places):
    """True when `path` is the harness's: a path its sync recorded, or one resolving into one of
    its checkouts. `places` is a `Places`, a single checkout path, or None."""
    if not places:
        return False
    if not isinstance(places, Places):
        places = Places((places,), frozenset())
    if _norm(path) in places.links:
        return True
    real = os.path.realpath(path)
    for root in places.roots:
        root = os.path.realpath(root)
        if real == root or real.startswith(root.rstrip(os.sep) + os.sep):
            return True
    return False


def _file(ident, owner, kind, path, text=None):
    if text is None:
        text, reason = _read(path)
        if text is None:
            return Source(ident, owner, kind, path, None, UNMEASURED, None, reason)
    return Source(ident, owner, kind, path, tokens_of(text), SOFT_ESTIMATE, FILE_METHOD, None)


def front_matter(text):
    """`{key: value}` of a file's YAML front matter, folded continuation lines joined; enough for
    `name`, `description` and `paths`, which is all a listing or a rule's scope needs."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out, key = {}, None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        match = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if match:
            key = match.group(1)
            out[key] = match.group(2).strip()
        elif key and line[:1] in (" ", "\t"):
            out[key] = (out[key] + " " + line.strip()).strip()
    return {k: v for k, v in out.items() if v not in (">", "|", ">-", "|-", ">+", "|+")} \
        if out else {}


def _listing(ident, owner, kind, path, fallback):
    text, reason = _read(path)
    if text is None:
        return Source(ident, owner, kind, path, None, UNMEASURED, None, reason)
    meta = front_matter(text)
    entry = (meta.get("name") or fallback) + ": " + meta.get("description", "")
    return Source(ident, owner, kind, path, tokens_of(entry), SOFT_ESTIMATE, LISTING_METHOD, None)


def imports(text):
    """The `@path` imports of an instruction file, outside code spans and fenced blocks."""
    found, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced:
            continue
        bare = re.sub(r"`[^`]*`", "", line)
        found += [m.group(1).replace("\\ ", " ").rstrip(".,;:)") for m in IMPORT.finditer(bare)]
    return found


def _resolve_import(target, base, home):
    if target.startswith("~/"):
        return os.path.join(home, target[2:])
    return target if os.path.isabs(target) else os.path.join(base, target)


def _instruction(path, owner, kind, home, harness_root, seen, depth=0, prefix=""):
    """One instruction file and the files it imports, recursively to `IMPORT_DEPTH` hops."""
    real = os.path.realpath(path)
    if real in seen or not os.path.isfile(path):
        return []
    seen.add(real)
    text, reason = _read(path)
    ident = prefix + _short(path, home)
    if text is None:
        return [Source(ident, owner, kind, path, None, UNMEASURED, None, reason)]
    out = [_file(ident, owner, kind, path, text)]
    if depth >= IMPORT_DEPTH:
        return out
    for target in imports(text):
        resolved = _resolve_import(target, os.path.dirname(path), home)
        # A user-level import is the harness's only when it is one of the harness's own files.
        child_owner = owner if owner == "project" else ("harness" if _inside(resolved, harness_root) else "own")
        out += _instruction(resolved, child_owner, "import", home, harness_root, seen, depth + 1, "import:")
    return out


def _short(path, home):
    if home and (path == home or path.startswith(home.rstrip(os.sep) + os.sep)):
        return "~" + path[len(home.rstrip(os.sep)):]
    return path


def _md_files(directory):
    out = []
    for dirpath, dirnames, filenames in os.walk(directory, followlinks=True):
        dirnames.sort()
        out += [os.path.join(dirpath, name) for name in sorted(filenames) if name.endswith(".md")]
    return out


def _rules(directory, owner, home, harness_root):
    out = []
    for path in _md_files(directory) if os.path.isdir(directory) else []:
        if owner == "own" and _inside(path, harness_root):
            continue  # a harness module, counted once by the attribution
        text, reason = _read(path)
        scoped = text is not None and front_matter(text).get("paths")
        kind = "rule (loads when a matching file is read)" if scoped else "rule"
        out.append(_file(_short(path, home), owner, kind, path, text))
    return out


def _listings(base, owner, home, harness_root):
    out = []
    pairs = (("skills", "skill listing"), ("agents", "agent listing"), ("commands", "command listing"))
    for folder, kind in pairs:
        root = os.path.join(base, folder)
        if not os.path.isdir(root):
            continue
        if folder == "skills":
            found = [(os.path.join(root, name, "SKILL.md"), name) for name in sorted(os.listdir(root))
                     if os.path.isfile(os.path.join(root, name, "SKILL.md"))]
        else:
            found = [(path, os.path.splitext(os.path.basename(path))[0]) for path in _md_files(root)]
        for path, name in found:
            if owner == "own" and _inside(path, harness_root):
                continue
            out.append(_listing(_short(path, home), owner, kind, path, name))
    return out


def _json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _project_chain(cwd):
    """The working directory and every directory above it, outermost first."""
    chain, here = [], os.path.realpath(cwd)
    while True:
        chain.append(here)
        parent = os.path.dirname(here)
        if parent == here:
            return list(reversed(chain))
        here = parent


def _project_root(cwd):
    for directory in reversed(_project_chain(cwd)):
        if os.path.exists(os.path.join(directory, ".git")):
            return directory
    return os.path.realpath(cwd)


def _memory_slug(path):
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def sources(home, cwd, harness_root=None, attribution=None, config_dir=None, sync_manifest=None):
    """Every source the session would load, as `Source` tuples, harness modules first.

    `home` is the user's home, `cwd` the session's working directory, `harness_root` the harness
    checkout (for telling its links apart), `attribution` the `posture.context_attribution` of the
    selection in force (None when it could not be resolved), `config_dir` the Claude directory
    when it is not `home/.claude`, and `sync_manifest` the sync's own record, whose `repo` and
    `links` name the checkout and files it installed, which may be another checkout than this."""
    home = os.path.realpath(home)
    record = sync_manifest if isinstance(sync_manifest, dict) else {}
    roots = tuple(r for r in (harness_root, record.get("repo")) if isinstance(r, str) and r)
    links = frozenset(_norm(entry["path"]) for entry in record.get("links") or []
                      if isinstance(entry, dict) and isinstance(entry.get("path"), str))
    harness_root = Places(roots, links) if roots or links else None
    claude = os.path.realpath(config_dir) if config_dir else os.path.join(home, ".claude")
    out = []
    if attribution is None:
        out.append(Source("harness modules", "harness", "modules", None, None, UNMEASURED, None,
                          "the selection in force could not be resolved"))
    else:
        for key, tokens in sorted((attribution.get("modules") or {}).items()):
            out.append(Source(key, "harness", key.split("/")[0], None, tokens,
                              attribution.get("estimand") or SOFT_ESTIMATE, attribution.get("method"), None))
    seen = set()
    user_md = os.path.join(claude, "CLAUDE.md")
    user_owner = "harness" if _inside(user_md, harness_root) else "own"
    out += _instruction(user_md, user_owner, "user instructions", home, harness_root, seen)
    out += _rules(os.path.join(claude, "rules"), "own", home, harness_root)
    out += _listings(claude, "own", home, harness_root)
    chain = [d for d in _project_chain(cwd) if os.path.realpath(d) != os.path.realpath(claude)]
    found = [os.path.join(d, name) for d in chain for name in INSTRUCTION_NAMES
             if os.path.isfile(os.path.join(d, name)) and os.path.realpath(os.path.join(d, name))
             != os.path.realpath(user_md)]
    if not found:  # Claude Code reads AGENTS.md only when no CLAUDE.md is on the path
        found = [os.path.join(d, name) for d in chain for name in AGENTS_NAMES
                 if os.path.isfile(os.path.join(d, name))]
    for path in found:
        kind = "local instructions" if path.endswith("CLAUDE.local.md") else "project instructions"
        out += _instruction(path, "project", kind, home, harness_root, seen)
    project = _project_root(cwd)
    out += _rules(os.path.join(project, ".claude", "rules"), "project", home, harness_root)
    out += _listings(os.path.join(project, ".claude"), "project", home, harness_root)
    settings = [(os.path.join(claude, "settings.json"), "own"),
                (os.path.join(project, ".claude", "settings.json"), "project"),
                (os.path.join(project, ".claude", "settings.local.json"), "project")]
    style = None
    for path, _owner in settings:
        data = _json(path)
        style = data.get("outputStyle") if isinstance(data.get("outputStyle"), str) else style
    if style:
        out += _output_style(style, claude, project, home, harness_root)
    memory = os.path.join(claude, "projects", _memory_slug(project), "memory", "MEMORY.md")
    if os.path.isfile(memory):
        text, reason = _read(memory)
        if text is not None:
            text = "\n".join(text.splitlines()[:MEMORY_LINES])[:MEMORY_BYTES]
        out.append(_file(_short(memory, home), "own", "auto memory index", memory, text) if text is not None
                   else Source(_short(memory, home), "own", "auto memory index", memory, None, UNMEASURED, None, reason))
    plugins = {}
    for path, _owner in settings:
        enabled = _json(path).get("enabledPlugins")
        for name, on in (enabled.items() if isinstance(enabled, dict) else []):
            plugins[name] = bool(on)
    out += [Source(name, "plugin", "plugin", None, None, UNMEASURED, None, PLUGIN_REASON)
            for name, on in sorted(plugins.items()) if on]
    servers = {}
    user_json = _json(os.path.join(home, ".claude.json"))
    for name in sorted(user_json.get("mcpServers") or {}):
        servers.setdefault(name, "user")
    for key, value in sorted((user_json.get("projects") or {}).items()):
        if isinstance(value, dict) and os.path.realpath(key) in (project, os.path.realpath(cwd)):
            for name in sorted(value.get("mcpServers") or {}):
                servers.setdefault(name, "local")
    for name in sorted(_json(os.path.join(project, ".mcp.json")).get("mcpServers") or {}):
        servers.setdefault(name, "project")
    out += [Source("%s (%s scope)" % (name, scope), "mcp", "mcp server", None, None, UNMEASURED, None, MCP_REASON)
            for name, scope in sorted(servers.items())]
    for path, owner in settings:
        hooks = _json(path).get("hooks")
        for event in sorted(hooks if isinstance(hooks, dict) else {}):
            out.append(Source("%s (%s settings)" % (event, owner), "hooks", "hook event", path, None,
                              UNMEASURED, None, HOOK_REASON))
    out.append(Source("system prompt and built-in tools", "runtime", "runtime", None, None, UNMEASURED,
                      None, RUNTIME_REASON))
    return out


def _output_style(name, claude, project, home, harness_root):
    for base, owner in ((os.path.join(project, ".claude"), "project"), (claude, "own")):
        path = os.path.join(base, "output-styles", name + ".md")
        if os.path.isfile(path):
            owner = "harness" if owner == "own" and _inside(path, harness_root) else owner
            return [_file("output style %s" % name, owner, "output style", path)]
    return [Source("output style %s" % name, "runtime", "output style", None, None, UNMEASURED, None,
                   "a built-in style, served by the CLI")]


def summary(found):
    """`{total_tokens, unmeasured, owners: {owner: {tokens, unmeasured, sources}}}`, every owner
    present even when it holds nothing, so an empty owner reads as none rather than missing."""
    owners = {owner: {"tokens": 0, "unmeasured": 0, "sources": 0} for owner in OWNERS}
    for source in found:
        entry = owners[source.owner]
        entry["sources"] += 1
        if source.tokens is None:
            entry["unmeasured"] += 1
        else:
            entry["tokens"] += source.tokens
    return {"total_tokens": sum(o["tokens"] for o in owners.values()),
            "unmeasured": sum(o["unmeasured"] for o in owners.values()), "owners": owners}


def as_json(found, coverage=None):
    """The report as one JSON-ready document."""
    coverage = coverage or {}
    total = summary(found)
    return {"estimand": SOFT_ESTIMATE, "method": FILE_METHOD, "total_tokens": total["total_tokens"],
            "unmeasured": total["unmeasured"], "owners": total["owners"],
            "sources": [dict(s._asdict(), coverage=coverage.get(s.id)) for s in found]}


def render(found, coverage=None, home=None):
    """The report as lines: a total, then each owner's total and its sources. `coverage` maps a
    harness module id (`rules/secrets`) to its rule-coverage state."""
    coverage = coverage or {}
    home = os.path.realpath(home) if home else home
    total = summary(found)
    lines = ["surface: {:,} est. tokens ({}, chars/4); {} source(s) unmeasured".format(
        total["total_tokens"], SOFT_ESTIMATE, total["unmeasured"])]
    width = min(48, max([24] + [len(s.id) + 1 for s in found]))
    for owner in OWNERS:
        mine = [s for s in found if s.owner == owner]
        entry = total["owners"][owner]
        lines.append("{}: {:,} est. tokens, {} source(s), {} unmeasured".format(
            owner, entry["tokens"], entry["sources"], entry["unmeasured"]))
        for s in mine:
            if s.tokens is None:
                lines.append("  %-*s unmeasured: %s" % (width, s.id, s.reason))
                continue
            state = coverage.get(s.id, "-")
            where = _short(s.path, home) if s.path and home else (s.path or "")
            where = "" if where in (s.id, s.id.split(":", 1)[-1]) else where
            lines.append("  %-*s %8s  %-10s %s" % (width, s.id, "{:,}".format(s.tokens), state, where))
    return lines

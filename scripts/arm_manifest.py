#!/usr/bin/env python3
"""Print, as JSON, every file and link that makes up a replay arm's configuration surface.

The runner pipes this file into a fresh container of the arm's image (`python3 -`), with no
network and no mount, so what it lists is what the image holds and nothing else. It imports
nothing from this repository and runs on any Python 3.9 or later.

What is listed, and what is left out and why, is fixed here so two builds of one arm can be
compared entry by entry:

- `home`: the agent user's whole home directory, less `EXCLUDED`: caches whose contents are
  download and log records, not configuration.
- `managed`: Claude Code's system-wide managed settings directory, when the image has one.
- `harness`: the harness checkout the harness arm was synced from, less its `.git` directory,
  whose pack layout differs between two clones of one commit. The commit itself is recorded.
- `cli_packages`: every global npm package by name and version, which is where agent clients live.

A file is recorded by mode, size and sha256; a link by its target; a directory by its mode.
Timestamps are never recorded. A file whose bytes carry a build time is normalised by
`VOLATILE` before it is hashed, and the manifest names each one so the normalisation is visible.
"""
import hashlib
import json
import os
import re
import stat
import subprocess
import sys

HOME = os.environ.get("HOME") or "/home/agent"
ROOTS = (("home", HOME), ("managed", "/etc/claude-code"), ("harness", "/opt/model-citizen"))
# Relative to the home directory: npm's download cache and its per-command logs, and the tool
# caches the base image leaves (corepack). Neither holds anything a session loads.
EXCLUDED = {"home": (".npm", ".cache"), "harness": (".git",)}
# `section:relative path` -> (pattern, replacement), applied to the file's bytes before hashing.
# The harness sync stamps the time it ran into its own record; nothing else in either arm moves.
VOLATILE = {"home:.local/state/agent-harness/manifest.json":
            (r'"synced_at": *"[^"]*"', '"synced_at": "<normalised>"')}
CLI_PACKAGE = "lib/node_modules/@anthropic-ai/claude-code/package.json"
SCHEMA = 1


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _excluded(section, rel):
    return any(rel == name or rel.startswith(name + "/") for name in EXCLUDED.get(section, ()))


def entry(section, root, rel):
    path = os.path.join(root, rel)
    info = os.lstat(path)
    out = {"path": "%s:%s" % (section, rel), "mode": "%04o" % stat.S_IMODE(info.st_mode),
           "uid": info.st_uid, "gid": info.st_gid}
    if stat.S_ISLNK(info.st_mode):
        out.update(kind="link", target=os.readlink(path))
    elif stat.S_ISDIR(info.st_mode):
        out.update(kind="dir")
    elif stat.S_ISREG(info.st_mode):
        with open(path, "rb") as handle:
            data = handle.read()
        key = "%s:%s" % (section, rel)
        if key in VOLATILE:
            pattern, replacement = VOLATILE[key]
            data = re.sub(pattern.encode(), replacement.encode(), data)
            out["normalised"] = True
        out.update(kind="file", size=len(data), sha256=_sha(data))
    else:
        out.update(kind="other")
    return out


def walk(section, root):
    found = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir
        kept = []
        for name in sorted(dirnames):
            rel = os.path.join(rel_dir, name) if rel_dir else name
            if _excluded(section, rel):
                continue
            found.append(entry(section, root, rel))
            if not os.path.islink(os.path.join(root, rel)):
                kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            rel = os.path.join(rel_dir, name) if rel_dir else name
            if not _excluded(section, rel):
                found.append(entry(section, root, rel))
    return found


def _npm_prefix():
    return os.environ.get("NPM_CONFIG_PREFIX") or "/usr/local"


def cli_version():
    try:
        with open(os.path.join(_npm_prefix(), CLI_PACKAGE), encoding="utf-8") as handle:
            return json.load(handle).get("version")
    except (OSError, ValueError):
        return None


def cli_packages():
    """Every globally installed npm package as `name@version`, sorted: the agent clients an image
    carries, which live outside the home directory and would otherwise go unlisted."""
    root, out = os.path.join(_npm_prefix(), "lib", "node_modules"), []
    names = []
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        if name.startswith("@"):
            names += [name + "/" + sub for sub in sorted(os.listdir(os.path.join(root, name)))]
        elif not name.startswith("."):
            names.append(name)
    for name in names:
        try:
            with open(os.path.join(root, name, "package.json"), encoding="utf-8") as handle:
                out.append("%s@%s" % (name, json.load(handle).get("version")))
        except (OSError, ValueError):
            out.append(name + "@unknown")
    return out


def harness_commit(root):
    if not os.path.isdir(os.path.join(root, ".git")):
        return None
    done = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, universal_newlines=True)
    return done.stdout.strip() or None


def summary(entries):
    """The configuration surface by kind, as sorted path lists: what #1010's declaration check and
    a reader both look for first. Paths under `home:.claude/` only, where the CLI loads them."""
    def under(prefix):
        return sorted(e["path"] for e in entries if e["path"].startswith("home:.claude/" + prefix))
    top = lambda prefix: sorted({"/".join(p.split("/")[:3]) for p in under(prefix)})
    return {"settings": sorted(e["path"] for e in entries
                               if re.match(r"home:\.claude/settings[^/]*\.json$", e["path"])
                               or e["path"].startswith("managed:")),
            "hooks": under("hooks/"), "rules": under("rules/"), "skills": top("skills/"),
            "agents": under("agents/"), "plugins": top("plugins/"),
            "instructions": sorted(e["path"] for e in entries if e["path"].startswith("home:.claude/")
                                   and e["path"].endswith(".md") and e["path"].count("/") == 1)}


def main():
    entries, roots = [], {}
    for section, root in ROOTS:
        if os.path.isdir(root):
            roots[section] = root
            entries += walk(section, root)
    entries.sort(key=lambda e: e["path"])
    manifest = {"schema": SCHEMA, "roots": roots,
                "excluded": {k: list(v) for k, v in sorted(EXCLUDED.items())},
                "normalised": sorted(VOLATILE), "claude_code_version": cli_version(),
                "cli_packages": cli_packages(),
                "harness_commit": harness_commit(roots["harness"]) if "harness" in roots else None,
                "summary": summary(entries), "entries": entries}
    json.dump(manifest, sys.stdout, sort_keys=True, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

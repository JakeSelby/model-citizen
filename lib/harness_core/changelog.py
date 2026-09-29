"""Changelog fragments: one file per change, assembled into CHANGELOG.md at release time.

Every branch used to add its entry to the same `## [Unreleased]` block, so any two branches in
flight conflicted there; new files merge. The naming, the waiver and the assembly are described
for contributors in `changelog.d/README.md`.
"""
import re
import subprocess
from pathlib import Path

DIRECTORY = "changelog.d"
# Keep a Changelog order; `none` is the waiver and is never rendered.
KINDS = ("added", "changed", "removed", "fixed")
WAIVER = "none"
NAME = re.compile(r"^([1-9][0-9]*)\.([a-z]+)\.md$")
IGNORED = ("README.md",)
ROOTS = ("bin/", "lib/", "adapters/", "primitives/", "policy/", "docs/", "scripts/")
MINIMUM_REASON_WORDS = 3
BASE = "origin/main"


def parse_name(name):
    """(number, kind) for a fragment file name, or ValueError naming the expected shape."""
    match = NAME.match(name)
    kinds = KINDS + (WAIVER,)
    if not match or match.group(2) not in kinds:
        raise ValueError("changelog fragment %s is not named <issue-or-pr>.<%s>.md"
                         % (name, "|".join(kinds)))
    return int(match.group(1)), match.group(2)


def is_candidate(name):
    """Whether a file in the directory is meant as a fragment: a visible `.md` other than the README.

    Dotfiles, editor swap and backup files and anything not ending in `.md` are skipped; a `.md`
    that is meant as a fragment but misnamed is refused, because dropping it loses an entry.
    """
    return name.endswith(".md") and not name.startswith(".") and name not in IGNORED


def fragments(root):
    """Every fragment as (number, kind, text), in render order. Malformed names are refused."""
    directory = Path(root) / DIRECTORY
    found = []
    if not directory.is_dir():
        return found
    for path in sorted(directory.iterdir()):
        if not path.is_file() or not is_candidate(path.name):
            continue
        number, kind = parse_name(path.name)
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError("changelog fragment %s is empty" % path.name)
        found.append((number, kind, text))
    order = {kind: index for index, kind in enumerate(KINDS + (WAIVER,))}
    return sorted(found, key=lambda item: (order[item[1]], item[0]))


def _entry(number, text):
    reference = "(#%d)" % number
    if reference not in text:
        text += " " + reference
    lines = text.splitlines()
    return "\n".join(["- " + lines[0]] + [("  " + line) if line.strip() else "" for line in lines[1:]])


def render(entries):
    """The body of a version section: one `###` block per kind that has entries."""
    blocks = []
    for kind in KINDS:
        items = [_entry(number, text) for number, found, text in entries if found == kind]
        if items:
            blocks.append("### %s\n\n%s" % (kind.capitalize(), "\n\n".join(items)))
    return "\n\n".join(blocks)


def assemble(changelog, version, date, entries):
    """CHANGELOG.md text with a `## [version] — date` section inserted under an empty Unreleased.

    Refuses rather than guesses when Unreleased still holds hand-written entries, when the
    version already has a section, or when there is nothing to assemble.
    """
    body = render(entries)
    if not body:
        raise ValueError("no changelog fragments to assemble")
    if re.search(r"^## \[%s\]" % re.escape(version), changelog, re.MULTILINE):
        raise ValueError("CHANGELOG.md already has a section for %s" % version)
    heading = re.search(r"^## \[Unreleased\][^\n]*\n", changelog, re.MULTILINE)
    if heading is None:
        raise ValueError("CHANGELOG.md has no ## [Unreleased] heading")
    following = re.search(r"^## \[", changelog[heading.end():], re.MULTILINE)
    end = heading.end() + following.start() if following else len(changelog)
    if changelog[heading.end():end].strip():
        raise ValueError("## [Unreleased] still holds hand-written entries; fold them into a "
                         "version section or move them into fragments first")
    section = "## [%s] — %s\n\n%s\n" % (version, date, body)
    rest = changelog[end:]
    return changelog[:heading.end()] + "\n" + section + ("\n" + rest if rest else "")


class GitUnavailable(Exception):
    """git is missing or did not answer in time; the rule is skipped, not failed."""


def _git(root, *argv):
    try:
        out = subprocess.run(["git", "-C", str(root)] + list(argv), capture_output=True, text=True,
                             timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise GitUnavailable(str(error))
    return out.stdout if out.returncode == 0 else None


def _lines(text):
    return [line for line in (text or "").splitlines() if line]


def branch_changes(root, base=BASE):
    """(fork, changed, added) for this branch against where it left `base`, uncommitted work
    included; `added` holds only files the branch creates.

    None when there is no `base` to compare with or the branch has no commit of its own, so a
    checkout sitting on the trunk, a tag build and a push to main are never judged.
    """
    head = _git(root, "rev-parse", "HEAD")
    fork = _git(root, "merge-base", "HEAD", base)
    if not head or not fork or head.strip() == fork.strip():
        return None
    fork = fork.strip()
    diffed = _git(root, "diff", "--name-only", fork)
    created = _git(root, "diff", "--name-only", "--diff-filter=A", fork)
    untracked = _git(root, "ls-files", "--others", "--exclude-standard")
    if diffed is None or created is None or untracked is None:
        return None
    changed = sorted(set(_lines(diffed) + _lines(untracked)))
    added = sorted(set(_lines(created) + _lines(untracked)))
    return fork, changed, added


VERSION_HEADING = re.compile(r"^## \[([0-9][^\]]*)\]", re.MULTILINE)


def assembles_release(root, fork):
    """Whether the branch is a release: its CHANGELOG.md gains a `## [<version>]` section the base
    lacks and it deletes fragments the base carried, which is what `release_notes.py --changelog`
    leaves behind. Such a branch consumes fragments, so asking it for one would strand a waiver."""
    path = Path(root) / "CHANGELOG.md"
    after = set(VERSION_HEADING.findall(path.read_text(encoding="utf-8") if path.is_file() else ""))
    before = set(VERSION_HEADING.findall(_git(root, "show", "%s:CHANGELOG.md" % fork) or ""))
    if not after - before:
        return False
    deleted = _git(root, "diff", "--name-only", "--diff-filter=D", fork, "--", DIRECTORY + "/")
    return any(is_candidate(Path(name).name) and name.count("/") == 1 for name in _lines(deleted))


def reason_words(text):
    return [word for word in text.split() if re.search(r"[A-Za-z]", word)]


def findings(root, notes=None):
    """Lint findings: a malformed or misplaced fragment, a thin waiver, or a branch that touches
    ROOTS without adding a fragment, unless it assembles a release. Why the rule was skipped, if
    it was, goes to `notes`."""
    root = Path(root)
    hits = []
    try:
        fragments(root)
    except ValueError as error:
        hits.append("changelog: %s" % error)
    directory = root / DIRECTORY
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            if path.is_dir():
                hits.append("changelog: %s/%s/ is a subdirectory; fragments live directly in %s/"
                            % (DIRECTORY, path.name, DIRECTORY))
    try:
        found = branch_changes(root)
        if found is None:
            return hits
        fork, changed, added = found
        touched = [path for path in changed if path.startswith(ROOTS)]
        if not touched:
            return hits
        new = [path for path in added if path.startswith(DIRECTORY + "/")
               and path.count("/") == 1 and is_candidate(Path(path).name) and (root / path).is_file()]
        for path in new:
            try:
                kind = parse_name(Path(path).name)[1]
            except ValueError:
                continue
            words = reason_words((root / path).read_text(encoding="utf-8"))
            if kind == WAIVER and len(words) < MINIMUM_REASON_WORDS:
                hits.append("changelog: waiver %s must give a reason of at least %d words"
                            % (path, MINIMUM_REASON_WORDS))
        if new:
            return hits
        if assembles_release(root, fork):
            return hits
    except GitUnavailable as error:
        if notes is not None:
            notes.append("changelog: fragment rule skipped, git unavailable (%s)" % error)
        return hits
    hits.append("changelog: this branch changes %s but adds no fragment; add %s/<issue-or-pr>.<%s>.md, "
                "or %s/<issue-or-pr>.%s.md saying in at least %d words why no entry is needed"
                % (touched[0] + (" and %d more" % (len(touched) - 1) if len(touched) > 1 else ""),
                   DIRECTORY, "|".join(KINDS), DIRECTORY, WAIVER, MINIMUM_REASON_WORDS))
    return hits

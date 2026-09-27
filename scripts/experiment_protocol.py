"""The pre-registration gate every eval entry point calls before it starts an arm.

A run is either pre-registered or exploratory. A pre-registered run names a plan filled from
`docs/pre-registration-template.md`, committed to this repository with a date, whose required
fields are all filled. A run without one must say it is exploratory: every row it writes carries
that label and it writes no history row, since an exploratory result is never cited as evidence.
The protocol itself is `docs/evidence-standard.md`. Standard library only.
"""
import datetime
import re
import subprocess
import sys
from pathlib import Path

EXPLORATORY = "exploratory"
PREREGISTERED = "pre-registered"
DIRECTORY = Path("benchmarks") / "preregistrations"

# (section, field): the fields the gate checks. A field of None is the whole section's body.
REQUIRED = (
    ("Run", "Question"),
    ("Run", "Date registered"),
    ("Hypotheses", "Primary"),
    ("Primary metric", "Metric"),
    ("Sample size", "Tasks"),
    ("Sample size", "Trials per task and arm"),
    ("Decision rule", None),
)
PLACEHOLDER = re.compile(r"<[^<>\n]+>")
DATED_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})-[A-Za-z0-9][A-Za-z0-9._-]*\.md$")
FIELD = re.compile(r"^- \*\*(.+?):\*\*(.*)$")


def sections(text):
    """`{heading: body}` for every `## ` heading in a plan."""
    found, current = {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            found[current] = []
        elif current is not None:
            found[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in found.items()}


def fields(body):
    """`{name: value}` for each `- **Name:** value` bullet, with its indented continuation lines."""
    found, current = {}, None
    for line in body.splitlines():
        match = FIELD.match(line)
        if match:
            current = match.group(1).strip()
            found[current] = match.group(2).strip()
        elif current is not None and line.startswith("  ") and line.strip():
            found[current] = (found[current] + " " + line.strip()).strip()
        else:
            current = None
    return found


def missing_fields(text):
    """The required fields that are absent, empty, or still hold a `<...>` placeholder."""
    parts, missing = sections(text), []
    for section, field in REQUIRED:
        body = parts.get(section)
        value = body if field is None else fields(body or "").get(field)
        label = section if field is None else "%s: %s" % (section, field)
        if not value or PLACEHOLDER.search(value):
            missing.append(label)
    return missing


def _git(root, *args):
    done = subprocess.run(["git", "-C", str(root)] + list(args), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, universal_newlines=True)
    return done.returncode, done.stdout.strip()


def _first_filled_commit(root, rel):
    """The first commit whose version of `rel` has every required field filled."""
    _, history = _git(root, "log", "--reverse", "--format=%H", "--", rel.as_posix())
    for commit in history.splitlines():
        code, text = _git(root, "show", "%s:%s" % (commit, rel.as_posix()))
        if not code and not missing_fields(text):
            return commit
    return None


def locate(path, root, cwd=None):
    """The plan's path relative to `root`, or None when it is outside the repository."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        here = Path(cwd) if cwd else Path.cwd()
        candidate = here / candidate if (here / candidate).exists() else Path(root) / candidate
    try:
        return candidate.resolve().relative_to(Path(root).resolve())
    except ValueError:
        return None


def check(path, root, today=None, cwd=None):
    """(errors, record). The record names the plan and the commit that added the filled plan."""
    today = today or datetime.date.today()
    rel = locate(path, root, cwd)
    if rel is None:
        return ["the pre-registration %s is not inside the repository at %s" % (path, root)], None
    if DIRECTORY not in rel.parents:
        return ["the pre-registration %s must be under %s"
                % (rel.as_posix(), DIRECTORY.as_posix())], None
    full = Path(root) / rel
    if not full.is_file():
        return ["the pre-registration %s does not exist" % rel.as_posix()], None
    errors = []
    name = DATED_NAME.match(rel.name)
    named_date = name.group(1) if name else None
    if not named_date:
        errors.append("the pre-registration's file name must start with its date, as in %s"
                      % (DIRECTORY / "2026-10-01-harness-vs-bare.md").as_posix())
    text = full.read_text(encoding="utf-8")
    missing = missing_fields(text)
    if missing:
        errors.append("the pre-registration leaves required fields unfilled: " + "; ".join(missing))
    registered = fields(sections(text).get("Run", "")).get("Date registered", "")
    if "Run: Date registered" not in missing:
        try:
            date = datetime.date.fromisoformat(registered)
        except ValueError:
            errors.append("Date registered %r is not a YYYY-MM-DD date" % registered)
        else:
            if date > today:
                errors.append("Date registered %s is after today" % registered)
            if named_date and registered != named_date:
                errors.append("Date registered %s differs from the date in the file name, %s"
                              % (registered, named_date))
    code, _ = _git(root, "ls-files", "--error-unmatch", "--", rel.as_posix())
    if code:
        errors.append("the pre-registration %s is not committed" % rel.as_posix())
        return errors, None
    _, dirty = _git(root, "status", "--porcelain", "--", rel.as_posix())
    if dirty:
        errors.append("the pre-registration %s has uncommitted changes; a plan is fixed by its "
                      "commit, and a later change is a dated entry in its deviation log" % rel.as_posix())
    _, shallow = _git(root, "rev-parse", "--is-shallow-repository")
    if shallow == "true":
        errors.append("the repository is shallow; full history is required to identify the plan commit")
    commit = None if shallow == "true" else _first_filled_commit(root, rel)
    if not commit:
        errors.append("the pre-registration %s has no commit containing a filled plan"
                      % rel.as_posix())
    return errors, {"evidence": PREREGISTERED, "pre_registration": rel.as_posix(),
                    "pre_registration_commit": commit or None}


def admit(pre_registration, exploratory, root, prog="experiment", today=None, cwd=None, err=None):
    """The stamp every row of the run carries, or SystemExit(2) naming each reason it is refused.

    Exactly one of `pre_registration` and `exploratory` is given. A pre-registered stamp names the
    plan and its commit; an exploratory one says so, and `writes_history` refuses its rows."""
    err = err or sys.stderr
    if pre_registration and exploratory:
        print("%s: --exploratory and --pre-registration exclude each other" % prog, file=err)
        raise SystemExit(2)
    if exploratory:
        print("%s: exploratory run; its rows are labelled exploratory, it writes no history row "
              "and it is never cited as evidence" % prog, file=err)
        return {"evidence": EXPLORATORY, "pre_registration": None, "pre_registration_commit": None}
    if not pre_registration:
        print("%s: refusing to run: a run that is not exploratory needs --pre-registration <path>, "
              "a plan filled from docs/pre-registration-template.md and committed with a date; "
              "pass --exploratory to run without one, never to be cited as evidence" % prog, file=err)
        raise SystemExit(2)
    errors, record = check(pre_registration, root, today, cwd)
    if errors:
        for error in errors:
            print("%s: %s" % (prog, error), file=err)
        print("%s: refusing to run; fix the plan or pass --exploratory" % prog, file=err)
        raise SystemExit(2)
    return record


def writes_history(rows):
    """False when any row is exploratory, or carries no evidence label at all."""
    return bool(rows) and all(row.get("evidence") == PREREGISTERED for row in rows)

#!/usr/bin/env python3
"""Provision one qualification round's scratch directory, once, from this checkout.

Every round before this one built the same three things by hand in a per-round scratch copy: a
frozen clone of the commit under qualification, a BMad framework checkout for the optional
integration suite,
and a place to keep each target's record. Hand-built means unreviewed, and a clone taken from the
wrong commit invalidates the round it is used for, so it is a committed script instead.

The clone is taken from this repository's own object store — no network — and it is refused
unless the tree is clean and the commit is the one the caller named. The BMad step is the only
one that reaches the network, it is opt-in, and it runs the pinned installer from docs/bmad.md
rather than a floating version.

    python3 scripts/qualification_provision.py --out ../round-0.13.0
    python3 scripts/qualification_provision.py --out ../round-0.13.0 --commit <sha> --bmad
    python3 scripts/qualification_provision.py --out ../round-0.13.0 --print-env
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text().strip()
# Split around the `@`, as the runner splits its throwaway committer identity, so the lint's
# address pattern does not match a pinned npm specifier.
BMAD_INSTALLER = "bmad-method" "@6.12.0"
BMAD_MODULES = "bmm"
CLONE = "clone"
# Written into a clone this script made, and checked before one is ever removed: a directory
# somebody else put at that path is refused rather than deleted.
CLONE_MARKER = ".harness-round-clone"
RECORDS = "records"
BMAD = "bmad"
BMAD_ENV = "HARNESS_ACCEPTANCE_BMAD"
TIMEOUT = 600


def repository_local_variables():
    """The variables Git treats as local to a repository, including stable fallbacks."""
    listed = subprocess.run(["git", "rev-parse", "--local-env-vars"],
                            capture_output=True, text=True, check=False)
    names = set(listed.stdout.split()) if listed.returncode == 0 else set()
    return names | {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR"}


LOCAL_GIT_VARIABLES = repository_local_variables()


def isolated_environment():
    """Keep the caller's intended environment without Git's repository selectors."""
    return {key: value for key, value in os.environ.items() if key not in LOCAL_GIT_VARIABLES}


def run(args, cwd=None, timeout=TIMEOUT):
    return subprocess.run([str(item) for item in args], cwd=str(cwd) if cwd else None,
                          capture_output=True, text=True, check=False, timeout=timeout,
                          env=isolated_environment())


def git(*args, **kwargs):
    return run(["git", "-C", str(kwargs.pop("repo", ROOT))] + list(args), **kwargs)


def head():
    return git("rev-parse", "HEAD").stdout.strip()


def clean():
    return not git("status", "--porcelain").stdout.strip()


def clone(out, commit):
    """A read-only clone of one commit, taken from the local object store.

    The round's evidence names a source commit; a clone that is not at that commit records a
    claim about source nobody ran. `--no-hardlinks` is deliberate: a hard-linked object store
    shares a fate with the checkout the operator keeps working in.
    """
    target = out / CLONE
    if target.exists():
        if not (target / CLONE_MARKER).is_file():
            raise SystemExit("%s exists and was not created by this script; move it aside first"
                             % target)
        shutil.rmtree(str(target))
    # `--no-shared` is the spelling git accepts: the option takes no value, so passing one makes
    # every clone exit 129. A shared object store would also give the clone the same fate as the
    # checkout being worked in, which is the reason the flag is here at all.
    result = run(["git", "clone", "--quiet", "--no-hardlinks", "--no-shared", str(ROOT),
                  str(target)])
    if result.returncode:
        raise SystemExit("could not clone this checkout: " + result.stderr.strip()[-300:])
    checked = git("checkout", "--quiet", "--detach", commit, repo=target)
    if checked.returncode:
        raise SystemExit("the clone could not be moved to %s: %s"
                         % (commit, checked.stderr.strip()[-300:]))
    at = git("rev-parse", "HEAD", repo=target).stdout.strip()
    if at != commit:
        raise SystemExit("the clone is at %s, not the commit asked for" % at)
    (target / CLONE_MARKER).write_text(commit + "\n")
    exclude_marker(target)
    status = git("status", "--porcelain", repo=target)
    if status.returncode:
        raise SystemExit("could not check whether the clone at %s is clean: %s"
                         % (target, status.stderr.strip()[-300:]))
    if status.stdout.strip():
        raise SystemExit("the clone at %s is not clean, and the runner inside it refuses a dirty "
                         "checkout" % target)
    return target


def exclude_marker(target):
    """Keep the marker out of the clone's status, so the runner inside the clone accepts it.

    The runner refuses a checkout with any `git status --porcelain` output, and a round drives
    the runner from this clone. The exclusion is the clone's own `.git/info/exclude`, never a
    tracked `.gitignore`: the clone must stay byte-identical to the commit under qualification.
    Fixing it here rather than in the runner reaches every round at once, since the runner that
    executes is the frozen commit's, while this script runs from the operator's checkout.
    """
    exclude = Path(git("rev-parse", "--git-path", "info/exclude", repo=target).stdout.strip())
    if not exclude.is_absolute():
        exclude = target / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text() if exclude.is_file() else ""
    line = "/" + CLONE_MARKER
    if line not in existing.splitlines():
        separator = "" if not existing or existing.endswith("\n") else "\n"
        exclude.write_text(existing + separator + line + "\n")


def bmad_install_args(target):
    """The optional suite's install command from docs/bmad.md, with `target` as the framework root.

    It must stay that command flag for flag. Without `--shims` the legacy review skill names some
    workflows still invoke are absent, and `harness integration apply bmad` reports drift on the
    provisioned root.
    """
    return ["npx", "--yes", BMAD_INSTALLER, "install", "--directory", str(target),
            "--modules", BMAD_MODULES, "--tools", "claude-code,codex",
            "--output-folder", "_bmad-output", "--shims", "--yes"]


def bmad(out):
    """A BMad framework checkout for the optional integration suite, from the pinned installer.

    No required case reads it: `framework-spawn-routing` builds its recipe from the integration
    descriptor and runs no framework workflow. The checkout is for the native review suite
    docs/releasing.md asks for once per minor release, which an operator runs by hand. It is the
    only step here that reaches the network, so it is opt-in, and an installer this script cannot
    find is a reported gap rather than a failed provision.
    """
    target = out / BMAD
    target.mkdir(parents=True, exist_ok=True)
    if not (target / ".git").exists():
        git("init", "--quiet", repo=target)
    if not shutil.which("npx"):
        return None, "npx is not on PATH, so no BMad framework checkout was installed"
    result = run(bmad_install_args(target), timeout=TIMEOUT)
    if result.returncode or not (target / "_bmad").is_dir():
        return None, ("the pinned BMad installer did not produce a framework checkout: "
                      + (result.stderr or result.stdout).strip()[-300:])
    return target, ""


def provision(out, commit, want_bmad):
    out.mkdir(parents=True, exist_ok=True)
    (out / RECORDS).mkdir(exist_ok=True)
    report = {"harness_version": VERSION, "source_commit": commit,
              "clone": str(clone(out, commit)), "records": str(out / RECORDS),
              "bmad": None, "notes": []}
    if want_bmad:
        path, note = bmad(out)
        report["bmad"] = str(path) if path else None
        if note:
            report["notes"].append(note)
    else:
        report["notes"].append("no BMad framework checkout was asked for; only the optional "
                               "integration suite needs one")
    return report


def environment(report):
    """The one variable a round exports, so no case reaches the network to find a checkout."""
    return {BMAD_ENV: report["bmad"]} if report.get("bmad") else {}


def inside_this_repository(path):
    """Whether `path` resolves inside any checkout or worktree of this repository.

    A round directory there would be removed by a clean tree check, or worse be committed. The
    test is the git object store both paths share, so a sibling worktree of this repository is
    refused exactly as the checkout itself is, and an unrelated repository elsewhere is not.
    """
    mine = git("rev-parse", "--git-common-dir").stdout.strip()
    if not mine:
        return False
    mine = (ROOT / mine).resolve()
    for candidate in [path] + list(path.parents):
        if not (candidate / ".git").exists():
            continue
        common = run(["git", "-C", str(candidate), "rev-parse", "--git-common-dir"]).stdout.strip()
        if common and (candidate / common).resolve() == mine:
            return True
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True,
                        help="round directory, outside this checkout")
    parser.add_argument("--commit", help="commit to qualify (default: HEAD of this checkout)")
    parser.add_argument("--bmad", action="store_true",
                        help="install the pinned BMad framework checkout for the optional integration "
                             "suite; the only network step")
    parser.add_argument("--print-env", action="store_true",
                        help="print the exports a round needs, one per line, and provision nothing")
    args = parser.parse_args(argv)
    out = args.out.expanduser().resolve()
    if inside_this_repository(out):
        raise SystemExit("the round directory must be outside every checkout of this repository")
    if args.print_env:
        existing = out / "provision.json"
        if not existing.is_file():
            raise SystemExit("no provision record at %s; provision the round first" % existing)
        for key, value in sorted(environment(json.loads(existing.read_text())).items()):
            print("export %s=%s" % (key, shlex.quote(str(value))))
        return 0
    if not clean():
        raise SystemExit("the checkout must be clean: a round's evidence names a source commit")
    commit = args.commit or head()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        resolved = git("rev-parse", commit).stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{40}", resolved):
            raise SystemExit("not a commit in this repository: " + commit)
        commit = resolved
    report = provision(out, commit, args.bmad)
    (out / "provision.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

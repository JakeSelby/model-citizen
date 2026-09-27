#!/usr/bin/env python3
"""Fail closed before publishing a tag."""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness_core import catalog, compatibility
import sync_about

GH_SKIPPED = "release warning: About and On-the-way checks skipped, gh is not authenticated"


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def published_surfaces(root, runner):
    """About drift and `on_the_way` entries whose issue has closed.

    Both read GitHub, so both run only behind a good `gh` probe; once the probe
    passes, a failing call is a blocked release rather than a skip.
    """
    errors = []
    wanted = sync_about.product(root)
    fields = sync_about.differences(wanted, sync_about.published(runner))
    if fields:
        errors.append("GitHub About does not match product.json: " + ", ".join(fields))
    entries = json.loads((root / "product.json").read_text()).get("on_the_way", [])
    for entry in entries:
        number = entry.get("issue")
        if number is None:
            continue
        state = json.loads(runner(["issue", "view", str(number), "--json", "state"]))["state"]
        if state.upper() != "OPEN":
            errors.append('on-the-way entry "{}" names issue #{}, which is {}; '
                          "promote or remove it".format(entry["title"], number, state.lower()))
    return errors


def check(root, runner=sync_about.gh, warn=print):
    errors = compatibility.release_errors(root)
    version = (root / "VERSION").read_text().strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        errors.append("VERSION is not a stable semantic version")
    if git(root, "status", "--porcelain"):
        errors.append("release checkout is dirty")
    if catalog.projection_drift(root):
        errors.append("native source projections have drifted")
    if sync_about.authenticated(runner):
        try:
            errors.extend(published_surfaces(root, runner))
        except (sync_about.GhError, ValueError, KeyError) as error:
            errors.append("About and On-the-way checks could not be read: " + str(error))
    else:
        warn(GH_SKIPPED)
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    errors = check(ROOT)
    for error in errors:
        print("release blocked: " + error)
    if not errors:
        data = compatibility.catalog(ROOT)
        print("release source verified; qualification basis: "
              + compatibility.qualification_disclosure(data)
              + "; run the repository gates before publishing")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())

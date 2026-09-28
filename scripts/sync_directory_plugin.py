#!/usr/bin/env python3
"""Build and verify the minimal Claude Directory plugin bundle."""

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUNDLE_RELATIVE = Path("directory/model-citizen")
MAX_DIRECTORY_FILES = 512

COPY_DIRECTORIES = (
    Path("primitives/skills"),
    Path("claude/agents"),
    Path("claude/commands"),
    Path("primitives/presentation"),
)
COPY_FILES = (
    Path(".claude-plugin/icon.svg"),
    Path(".claude-plugin/plugin.json"),
    Path("docs/privacy.md"),
    Path("LICENSE"),
)

DIRECTORY_README = """# Model Citizen for Claude Code

Model Citizen is a set of reusable skills, subagent roles, slash commands and an output style for
Claude Code. This directory bundle is the small, standalone plugin distribution from the
[Model Citizen repository](https://github.com/JakeSelby/model-citizen).

## What is included

- 15 skills for planning, verification, delegation, licensing, migration safety and delivery
- 11 scoped subagent roles
- 7 slash commands
- the scannable output style

The bundle does not install Model Citizen's hooks, global rules, stances, local ledgers or Codex
projection. Those belong to the separately installed full harness.

## Data and external services in the Claude Directory plugin

The plugin runs inside Claude Code and can read local files, repository metadata, command output
and task context when you ask it to work with them. It has no Model Citizen account, analytics,
remote MCP server or hosted service, and the maintainer does not receive or retain that data.

Some included workflows can use tools you configure or approve:

- delivery workflows can run `git` and the GitHub CLI, sending repository content and metadata to
  GitHub or another configured Git remote;
- research and review workflows can invoke Claude Code's WebFetch and WebSearch tools, sending a
  URL, query and relevant request context to the services behind those tools; and
- commands, agents and skills can read or write local project files and run local programs under
  Claude Code's native permissions and approval controls.

Model Citizen does not add credentials or bypass those controls. Each external product remains
governed by its own terms, privacy policy and retention settings. See the bundled
[privacy policy](docs/privacy.md) for details.

## Support

Read the [documentation](https://model-citizen.dev/) or report a problem in
[GitHub Issues](https://github.com/JakeSelby/model-citizen/issues).
"""


def _source_files(root):
    """Return the canonical source-to-bundle path mapping."""
    files = {}
    for relative in COPY_FILES:
        files[relative] = root / relative
    for relative in COPY_DIRECTORIES:
        source = root / relative
        for path in sorted(source.rglob("*")):
            if path.is_symlink():
                raise ValueError("canonical plugin source is a symlink: %s" % path)
            if path.is_file():
                files[path.relative_to(root)] = path
    return files


def desired_files(root=ROOT):
    """Return every expected bundle path and its bytes."""
    expected = {relative: source.read_bytes()
                for relative, source in _source_files(root).items()}
    expected[Path("README.md")] = DIRECTORY_README.encode("utf-8")
    privacy = expected[Path("docs/privacy.md")].decode("utf-8")
    privacy = privacy.replace(
        "(telemetry.md)",
        "(https://github.com/JakeSelby/model-citizen/blob/main/docs/telemetry.md)")
    privacy = privacy.replace(
        "(../SECURITY.md)",
        "(https://github.com/JakeSelby/model-citizen/blob/main/SECURITY.md)")
    expected[Path("docs/privacy.md")] = privacy.encode("utf-8")
    return expected


def _manifest_targets(manifest):
    targets = [manifest["skills"], manifest["commands"], manifest["outputStyles"]]
    targets.extend(manifest["agents"])
    return targets


def bundle_errors(root=ROOT):
    """Return deterministic bundle drift and safety errors."""
    bundle = root / BUNDLE_RELATIVE
    expected = desired_files(root)
    errors = []
    actual = {}
    if bundle.exists():
        for path in sorted(bundle.rglob("*")):
            relative = path.relative_to(bundle)
            if path.is_symlink():
                errors.append("bundle entry is a symbolic link: %s" % relative)
            elif path.is_file():
                actual[relative] = path.read_bytes()
    for relative in sorted(expected):
        if relative not in actual:
            errors.append("bundle file is missing: %s" % relative)
        elif actual[relative] != expected[relative]:
            errors.append("bundle file is stale: %s" % relative)
    for relative in sorted(set(actual) - set(expected)):
        errors.append("bundle has an unexpected file: %s" % relative)
    if len(actual) > MAX_DIRECTORY_FILES:
        errors.append("bundle has %d files; Claude Directory allows at most %d" %
                      (len(actual), MAX_DIRECTORY_FILES))

    manifest_path = bundle / ".claude-plugin" / "plugin.json"
    if manifest_path.is_file() and not manifest_path.is_symlink():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            version = (root / "VERSION").read_text(encoding="utf-8").strip()
            if manifest.get("version") != version:
                errors.append("bundle manifest version does not match VERSION")
            bundle_root = bundle.resolve()
            for target in _manifest_targets(manifest):
                resolved = (bundle / target).resolve()
                try:
                    resolved.relative_to(bundle_root)
                except ValueError:
                    errors.append("manifest path escapes the bundle: %s" % target)
                    continue
                if not resolved.exists():
                    errors.append("manifest path does not exist in the bundle: %s" % target)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            errors.append("bundle manifest is invalid: %s" % exc)
    return errors


def sync(root=ROOT):
    """Replace the committed bundle with a regular-file snapshot."""
    bundle = root / BUNDLE_RELATIVE
    bundle.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".model-citizen-", dir=str(bundle.parent)))
    try:
        for relative, data in desired_files(root).items():
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        if bundle.exists():
            shutil.rmtree(str(bundle))
        os.replace(str(staging), str(bundle))
    finally:
        if staging.exists():
            shutil.rmtree(str(staging))
    return len(desired_files(root))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="report drift without changing the bundle")
    args = parser.parse_args(argv)
    if args.check:
        errors = bundle_errors()
        for error in errors:
            print("directory plugin: " + error)
        if errors:
            return 1
        print("directory plugin: bundle is current (%d files)" % len(desired_files()))
        return 0
    count = sync()
    print("directory plugin: synced %d files" % count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Maintain the public BMad-to-GitHub issue mapping.

GitHub owns delivery state. This tool owns only the idempotent planning block,
exact BMad type label, primary parent relationship, and native issue type when
the repository supports that organization-managed field. In each story file it
owns the frontmatter, the H1 and the managed block; the rest is the design, and
the tool never rewrites it. The format is described in docs/bmad.md.
"""

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MAP_RELATIVE_PATH = "_bmad-output/issue-map.json"
MAP_PATH = ROOT / MAP_RELATIVE_PATH
# Every GitHub API call goes to this slug, not the map's "repository". The map keeps the slug it was
# bootstrapped with because planning_block renders it into every mapped issue's body; since the
# repository was renamed, GitHub answers a write to that slug with a 307 that `gh api` does not follow.
API_REPOSITORY = "JakeSelby/model-citizen"
ARTIFACT_DIR = ROOT / "_bmad-output" / "implementation-artifacts"
BEGIN = "<!-- bmad-traceability:start -->"
END = "<!-- bmad-traceability:end -->"
# A notice carrying this prefix prints as a warning: reported, never failing the audit.
WARNING = "warning: "
TEMPLATE_DIR = Path(__file__).resolve().parent / "bmad_story_templates"
SYNC_BEGIN = "<!-- bmad-sync:begin -->"
SYNC_END = "<!-- bmad-sync:end -->"
AUTHORITY = "The issue carries the summary, discussion and acceptance evidence; this file carries the design."
TEMPLATE = {
    "story": "story",
    "bug": "bug",
    "spike": "spike",
    "decision": "decision",
    "epic": "epic",
    "task": "task",
    "chore": "task",
}
# The sections `audit --delivery` requires to hold content once placeholder comments are stripped.
REQUIRED = {
    "story": ("Story", "Acceptance criteria", "Design", "Tasks", "Dev notes"),
    "bug": ("Reproduction", "Root cause", "Acceptance criteria", "Design", "Dev notes"),
    "spike": ("Question", "Experiment", "Exit criterion", "Result"),
    "decision": ("Context", "Options", "Decision", "Consequences"),
    "epic": ("Goal", "Scope and requirement coverage", "Exit criteria"),
    "task": ("Goal", "Acceptance criteria", "Tasks"),
    "chore": ("Goal", "Acceptance criteria", "Tasks"),
}
GH_TIMEOUT_SECONDS = 30
GIT_TIMEOUT_SECONDS = 30
MISSING = object()
ADVANCE_HELP = "skip IDs a sibling branch or worktree already took, leaving them unused"
KINDS = ("epic", "story", "task", "bug", "chore", "spike", "decision")
PREFIX = {
    "epic": "E",
    "story": "S",
    "task": "T",
    "bug": "B",
    "chore": "C",
    "spike": "SP",
    "decision": "D",
}
NATIVE_TYPE = {
    "epic": "Feature",
    "story": "Feature",
    "task": "Task",
    "bug": "Bug",
    "chore": "Task",
    "spike": "Task",
    "decision": "Task",
}
EPICS = {93, 116, 135, 189}
DECISIONS = {52}
SPIKES = {141, 146, 159}
BUGS = {37, 67, 80, 84, 85, 181, 200, 203}
AUTHORED = {189, 190, 191, 192, 193, 199, 200, 203}
PARENTS = {
    **{number: 93 for number in range(94, 101)},
    **{number: 116 for number in range(117, 131)},
    134: 98,
    **{number: 135 for number in list(range(136, 148)) + list(range(156, 163))},
    163: 94,
    164: 95,
    165: 96,
    166: 97,
    167: 98,
    168: 94,
    169: 99,
    170: 95,
    171: 94,
    172: 100,
    173: 96,
    174: 97,
    175: 97,
    176: 97,
    177: 96,
    178: 98,
    179: 97,
    180: 97,
    181: 96,
    183: 99,
    185: 98,
    187: 98,
    188: 98,
    **{number: 189 for number in range(190, 194)},
    199: 116,
    200: 98,
    203: 189,
}


def gh_command(args, input_data=None):
    command = ["gh"] + list(args)
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            input=None if input_data is None else json.dumps(input_data),
            text=True,
            capture_output=True,
            timeout=GH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            "GitHub command timed out after {} seconds".format(GH_TIMEOUT_SECONDS)
        ) from error
    except OSError as error:
        raise RuntimeError("GitHub command failed: {}".format(error)) from error
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result


def gh_json(args, input_data=None):
    result = gh_command(args, input_data)
    return json.loads(result.stdout) if result.stdout.strip() else None


def fetch_issues(repo):
    pages = gh_json(
        [
            "api",
            "--method",
            "GET",
            "--paginate",
            "--slurp",
            "-H",
            "X-GitHub-Api-Version: 2026-03-10",
            "repos/{}/issues?state=all&per_page=100".format(repo),
        ]
    )
    return sorted(
        [issue for page in pages for issue in page if "pull_request" not in issue],
        key=lambda issue: issue["number"],
    )


def infer_kind(issue):
    number = issue["number"]
    title = issue["title"].lower()
    labels = {label["name"].lower() for label in issue.get("labels", [])}
    if number in EPICS:
        return "epic"
    if number in DECISIONS:
        return "decision"
    if number in SPIKES:
        return "spike"
    if number in BUGS or "bug" in labels or title.startswith("fix"):
        return "bug"
    if "docs" in labels or title.startswith("docs") or title.startswith("refactor"):
        return "chore"
    return "story"


def build_manifest(issues, repo, native_type_projection="labels-only"):
    counters = defaultdict(int)
    items = []
    by_number = {}
    for issue in sorted(issues, key=lambda value: value["number"]):
        kind = infer_kind(issue)
        counters[kind] += 1
        bmad_id = "AH-{}{:03d}".format(PREFIX[kind], counters[kind])
        item = {
            "bmad_id": bmad_id,
            "github_number": issue["number"],
            "github_url": issue["html_url"],
            "title": issue["title"],
            "type": kind,
            "native_type": NATIVE_TYPE[kind],
            "artifact_path": "_bmad-output/implementation-artifacts/{}.md".format(bmad_id),
            "parent_bmad_id": None,
            "parent_github_number": PARENTS.get(issue["number"]),
            "lifecycle": "active" if issue["state"] == "open" else "completed",
            "provenance": "authored" if issue["number"] in AUTHORED else "reconstructed",
        }
        items.append(item)
        by_number[issue["number"]] = item
    for item in items:
        parent = by_number.get(item["parent_github_number"])
        if parent:
            item["parent_bmad_id"] = parent["bmad_id"]
    return {
        "schema_version": 2,
        "repository": repo,
        "native_type_projection": native_type_projection,
        "generated_at": dt.date.today().isoformat(),
        "next_ids": {kind: counters[kind] + 1 for kind in KINDS},
        "items": items,
    }


def yaml_value(value):
    return "null" if value is None else json.dumps(value, ensure_ascii=False)


def history_line(item):
    return (
        "This file reconstructs planning metadata from the existing GitHub record. It does not imply "
        "that a BMad artifact existed when the original work was performed."
        if item["provenance"] == "reconstructed"
        else "This work item was authored as part of the repository's committed BMad planning system."
    )


def parent_link(item):
    if not item["parent_github_number"]:
        return "None"
    repository_url = item["github_url"].rsplit("/issues/", 1)[0]
    return "[{}]({}/issues/{})".format(
        item["parent_bmad_id"], repository_url, item["parent_github_number"]
    )


def render_frontmatter(item):
    fields = [
        ("bmad_id", item["bmad_id"]),
        ("type", item["type"]),
        ("title", item["title"]),
        ("lifecycle", item["lifecycle"]),
        ("provenance", item["provenance"]),
        ("github_issue", item["github_number"]),
        ("github_issue_url", item["github_url"]),
        ("parent_bmad_id", item["parent_bmad_id"]),
        ("parent_github_issue", item["parent_github_number"]),
        ("updated", dt.date.today().isoformat()),
    ]
    return "\n".join("{}: {}".format(key, yaml_value(value)) for key, value in fields)


def render_legacy_stub(item):
    """The pre-typed stub: kept so refresh can maintain files not yet upgraded, and upgrade can read them."""
    return """---
{}
---

# {} — {}

{}

## Delivery authority

- **GitHub issue:** [#{}]({})
- **Primary parent:** {}
- **State:** {}

The GitHub issue owns scope, discussion, delivery state and acceptance evidence. This immutable-ID
file owns the planning identity and reverse link; amendments belong here only when they add durable
planning context rather than duplicate the issue.
""".format(
        render_frontmatter(item),
        item["bmad_id"],
        item["title"],
        history_line(item),
        item["github_number"],
        item["github_url"],
        parent_link(item),
        item["lifecycle"],
    )


def render_head(item):
    """The tool-owned part of a typed story file: frontmatter, H1 and the managed block."""
    return render_frontmatter_block(item) + "\n" + render_title_block(item)


def render_frontmatter_block(item):
    return "---\n{}\n---\n".format(render_frontmatter(item))


def render_title_block(item):
    return """# {} — {}

{}
- **GitHub issue:** [#{}]({})
- **Primary parent:** {}
- **State:** {}

{}

{}
{}""".format(
        item["bmad_id"],
        item["title"],
        SYNC_BEGIN,
        item["github_number"],
        item["github_url"],
        parent_link(item),
        item["lifecycle"],
        AUTHORITY,
        history_line(item),
        SYNC_END,
    )


def story_template(kind):
    return (TEMPLATE_DIR / "{}.md".format(TEMPLATE[kind])).read_text(encoding="utf-8")


def render_artifact(item):
    """A new typed story file: the tool-owned head, then the kind's skeleton for people and agents to fill."""
    return render_head(item) + "\n\n" + story_template(item["type"])


MALFORMED = "managed block missing or malformed"
BOM = "﻿"
FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
FENCE_CLOSE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")


def fence_opening(line):
    """The fence a line opens, or None; a backtick fence's info string may not hold a backtick."""
    match = FENCE_OPEN.match(line)
    if not match or (match.group(1)[0] == "`" and "`" in match.group(2)):
        return None
    return match.group(1)


def fence_closes(line, fence):
    match = FENCE_CLOSE.match(line)
    return bool(match) and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence)


def parse_layout(text):
    """Locate the tool-owned parts of a typed story file.

    Returns None when no managed block opens on the first non-blank line after the H1, else a dict
    of offsets into `text`: `bom` (0 or 1), `frontmatter_end` (just past the closing `---` line),
    `title_start` (the H1) and `head_end` (just past the end marker). Between the frontmatter and
    the H1 only blank lines and whole-line HTML comments may stand; they belong to the author and
    are kept. Raises when that area holds anything else, when the begin marker has no end before
    the first `## `, or when a second begin marker stands outside a code fence before that line.
    Markers anywhere else in the body are ordinary text.
    """
    bom = 1 if text.startswith(BOM) else 0
    lines = text[bom:].splitlines(True)
    offsets = []
    offset = bom
    for line in lines:
        offsets.append(offset)
        offset += len(line)
    offsets.append(offset)
    bare = [line.rstrip("\r\n") for line in lines]
    index = 0
    if bare and bare[0] == "---":
        index = next((i for i in range(1, len(bare)) if bare[i] == "---"), len(bare)) + 1
    frontmatter_end = offsets[min(index, len(lines))]
    while index < len(bare) and not bare[index].startswith("# "):
        if bare[index].strip() and not is_whole_line_comment(bare[index]):
            return None  # neither typed nor a legacy render, so artifact_layout refuses it
        index += 1
    if index >= len(bare):
        return None
    title = index
    index += 1
    while index < len(bare) and not bare[index].strip():
        index += 1
    if index >= len(bare) or bare[index] != SYNC_BEGIN:
        return None
    end = None
    fence = None
    for position in range(index + 1, len(bare)):
        line = bare[position]
        if fence is not None:
            if fence_closes(line, fence):
                fence = None
            continue
        if line.startswith("## "):
            break
        if end is None:
            if line == SYNC_END:
                end = position
            elif line == SYNC_BEGIN:
                _malformed()
            continue
        if line == SYNC_BEGIN:
            _malformed()
        fence = fence_opening(line)
    if end is None:
        _malformed()
    return {
        "bom": bom,
        "frontmatter_end": frontmatter_end,
        "title_start": offsets[title],
        "head_end": offsets[end] + len(SYNC_END),
    }


def _malformed():
    raise RuntimeError(MALFORMED)


def sync_layout(text):
    """The offset just past the end marker of a well-formed managed block, or None when there is none."""
    layout = parse_layout(text)
    return None if layout is None else layout["head_end"]


def legacy_text(text):
    """LF-normalised and without a BOM, for comparing with the LF legacy render."""
    return (text[1:] if text.startswith(BOM) else text).replace("\r\n", "\n")


def is_legacy_stub(item, text):
    """A legacy stub is recognised positively: it opens with the tool's legacy render for its item."""
    return strip_updated(legacy_text(text)).startswith(strip_updated(render_legacy_stub(item)))


def is_whole_line_comment(line):
    """True when a line holds one Markdown comment and nothing else, such as a lint directive above the H1.

    Plain string tests rather than a regular expression: this classifies Markdown structure, it does
    not sanitize HTML.
    """
    stripped = line.strip()
    return stripped.startswith("<!--") and stripped.endswith("-->") and stripped.count("-->") == 1


def artifact_layout(item, text):
    """The parse_layout dict for a typed file, None for a legacy stub; raises for anything else."""
    layout = parse_layout(text)
    if layout is None and not is_legacy_stub(item, text):
        _malformed()
    return layout


def newline_of(text):
    """The file's own line ending, so a rewrite never mixes styles."""
    end = text.find("\n")
    return "\r\n" if end > 0 and text[end - 1] == "\r" else "\n"


def with_newlines(text, newline):
    """Convert text rendered with LF endings to `newline`."""
    return text if newline == "\n" else text.replace("\n", newline)


def read_exact(path):
    """Read without newline translation, so a body is preserved byte for byte."""
    return Path(path).read_bytes().decode("utf-8")


def write_text_atomic(path, text):
    """Replace a file in one step, keeping its mode, so an interrupted run never leaves half of one behind."""
    path = Path(path)
    try:
        mode = path.stat().st_mode & 0o7777
    except FileNotFoundError:
        mode = 0o644
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".{}.".format(path.name))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, str(path))
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    fsync_directory(path.parent)


def fsync_directory(directory):
    """Make the rename durable where the platform allows it; best effort."""
    if os.name != "posix":
        return
    try:
        handle = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(handle)
    except OSError:
        pass
    finally:
        os.close(handle)


def write_manifest(manifest):
    map_path = ROOT / "_bmad-output" / "issue-map.json"
    artifact_dir = ROOT / "_bmad-output" / "implementation-artifacts"
    map_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    expected = set()
    for item in manifest["items"]:
        path = ROOT / item["artifact_path"]
        expected.add(path)
    for path in artifact_dir.glob("AH-*.md"):
        if path not in expected:
            raise RuntimeError("refusing to remove unreferenced artifact: {}".format(path))
    for item in manifest["items"]:
        path = ROOT / item["artifact_path"]
        if not path.exists():
            write_text_atomic(path, render_artifact(item))
    write_text_atomic(map_path, json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    # Derived from the map and the story files just written, so the two never drift apart.
    write_sprint_status(manifest)


MANIFEST_ITEM_KEYS = (
    "bmad_id", "github_number", "github_url", "title", "type", "native_type", "artifact_path",
    "parent_bmad_id", "parent_github_number", "lifecycle", "provenance",
)


def load_manifest():
    """Read the issue map, refusing one whose shape would fail later as a bare KeyError."""
    manifest = json.loads((ROOT / "_bmad-output" / "issue-map.json").read_text(encoding="utf-8"))
    try:
        manifest["repository"]
        for item in manifest["items"]:
            for key in MANIFEST_ITEM_KEYS:
                item[key]
    except (KeyError, TypeError, IndexError) as error:
        raise ValueError("issue map is malformed ({!r}); fix it before running this tool".format(error))
    return manifest


def frontmatter_value(text, key):
    lines = (text[1:] if text.startswith(BOM) else text).splitlines()
    if not lines or lines[0] != "---":
        return MISSING
    try:
        end = lines.index("---", 1)
    except ValueError:
        return MISSING
    frontmatter = "\n".join(lines[1:end])
    matches = re.findall(r"^{}:\s*(.+)$".format(re.escape(key)), frontmatter, re.MULTILINE)
    if len(matches) != 1:
        return MISSING
    raw = matches[0]
    if raw == "null":
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return MISSING


def audit_manifest(manifest=None, sprint_status=False):
    """Local findings; `sprint_status` adds the derived file's drift, which only the audit command
    checks, because reserve and new run this as a precondition and regenerate the file themselves."""
    manifest = manifest or load_manifest()
    errors = []
    if manifest.get("schema_version") not in {1, 2}:
        errors.append("unsupported manifest schema_version")
    if (
        not (manifest.get("schema_version") == 1 and "native_type_projection" not in manifest)
        and manifest.get("native_type_projection") not in {"labels-only", "native-and-labels"}
    ):
        errors.append("native_type_projection must be labels-only or native-and-labels")
    seen_ids = set()
    seen_issues = set()
    seen_artifacts = set()
    by_id = {item["bmad_id"]: item for item in manifest.get("items", [])}
    for item in manifest.get("items", []):
        bmad_id = item["bmad_id"]
        issue_number = item["github_number"]
        expected_id = r"^AH-{}\d{{3}}$".format(PREFIX.get(item["type"], "INVALID"))
        if not re.match(expected_id, bmad_id):
            errors.append("{}: ID does not match type {}".format(bmad_id, item["type"]))
        if bmad_id in seen_ids:
            errors.append("duplicate BMad ID {}".format(bmad_id))
        if issue_number in seen_issues:
            errors.append("duplicate GitHub issue #{}".format(issue_number))
        if item["artifact_path"] in seen_artifacts:
            errors.append("duplicate artifact path {}".format(item["artifact_path"]))
        seen_ids.add(bmad_id)
        seen_issues.add(issue_number)
        seen_artifacts.add(item["artifact_path"])
        expected_path = "_bmad-output/implementation-artifacts/{}.md".format(bmad_id)
        if item["artifact_path"] != expected_path:
            errors.append("{}: artifact path does not match ID".format(bmad_id))
        if item["native_type"] != NATIVE_TYPE.get(item["type"]):
            errors.append("{}: invalid native type".format(bmad_id))
        if bool(item["parent_bmad_id"]) != bool(item["parent_github_number"]):
            errors.append("{}: parent mapping is incomplete".format(bmad_id))
        elif item["parent_bmad_id"]:
            parent = by_id.get(item["parent_bmad_id"])
            if not parent or parent["github_number"] != item["parent_github_number"]:
                errors.append("{}: parent mapping is inconsistent".format(bmad_id))
        path = ROOT / item["artifact_path"]
        if not path.is_file():
            errors.append("{}: missing artifact {}".format(bmad_id, item["artifact_path"]))
            continue
        text = path.read_text(encoding="utf-8")
        try:
            artifact_layout(item, text)
        except RuntimeError as error:
            errors.append("{}: {}".format(bmad_id, error))
        expected = {
            "bmad_id": bmad_id,
            "type": item["type"],
            "title": item["title"],
            "lifecycle": item["lifecycle"],
            "provenance": item["provenance"],
            "github_issue": issue_number,
            "github_issue_url": item["github_url"],
            "parent_bmad_id": item["parent_bmad_id"],
            "parent_github_issue": item["parent_github_number"],
        }
        for key, value in expected.items():
            if frontmatter_value(text, key) != value:
                errors.append("{}: artifact {} does not match manifest".format(bmad_id, key))
    for kind in KINDS:
        next_id = manifest.get("next_ids", {}).get(kind)
        used = []
        pattern = re.compile(r"^AH-{}(\d{{3}})$".format(PREFIX[kind]))
        for item in manifest.get("items", []):
            match = pattern.match(item["bmad_id"])
            if item.get("type") == kind and match:
                used.append(int(match.group(1)))
        if type(next_id) is not int or next_id < 1 or (used and next_id <= max(used)):
            errors.append("{}: next ID is not monotonic".format(kind))
    expected_paths = {ROOT / path for path in seen_artifacts}
    artifact_dir = ROOT / "_bmad-output" / "implementation-artifacts"
    for path in artifact_dir.glob("AH-*.md"):
        if path not in expected_paths:
            errors.append("unreferenced artifact {}".format(path.relative_to(ROOT)))
    if sprint_status:
        errors.extend(sprint_status_findings(manifest))
    return errors


def planning_block(item, repo):
    artifact_url = "https://github.com/{}/blob/main/{}".format(repo, item["artifact_path"])
    lines = [
        BEGIN,
        "## Planning",
        "",
        "- **BMad ID:** `{}`".format(item["bmad_id"]),
        "- **Artifact:** [{}]({})".format(item["bmad_id"], artifact_url),
    ]
    if item["parent_github_number"]:
        lines.append(
            "- **Primary parent:** [{}](https://github.com/{}/issues/{})".format(
                item["parent_bmad_id"], repo, item["parent_github_number"]
            )
        )
    lines += ["", END]
    return "\n".join(lines)


def upsert_planning_block(body, block):
    body = body or ""
    pattern = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.DOTALL)
    matches = list(pattern.finditer(body))
    if body.count(BEGIN) != body.count(END) or len(matches) != body.count(BEGIN):
        raise RuntimeError("issue body has malformed BMad Planning fences")
    if matches:
        parts = []
        cursor = 0
        for index, match in enumerate(matches):
            parts.append(body[cursor:match.start()])
            if index == 0:
                parts.append(block)
            cursor = match.end()
        parts.append(body[cursor:])
        return "".join(parts)
    return body.rstrip() + ("\n\n" if body.strip() else "") + block


def type_name(value):
    if isinstance(value, dict):
        return value.get("name")
    return value


def desired_labels(issue, kind):
    unrelated = {
        label["name"] for label in issue.get("labels", [])
        if not label["name"].startswith("type::")
    }
    return unrelated | {"type::{}".format(kind)}


def projection_mode(manifest):
    if manifest.get("schema_version") == 1 and "native_type_projection" not in manifest:
        return "native-and-labels"
    mode = manifest.get("native_type_projection")
    if mode not in {"labels-only", "native-and-labels"}:
        raise RuntimeError("native_type_projection must be labels-only or native-and-labels")
    return mode


def planned_actions(manifest, live_issues):
    project_native_types = projection_mode(manifest) == "native-and-labels"
    live = {issue["number"]: issue for issue in live_issues}
    actions = []
    for item in manifest["items"]:
        issue = live.get(item["github_number"])
        if not issue:
            actions.append({"issue": item["github_number"], "action": "missing"})
            continue
        desired_body = upsert_planning_block(issue.get("body"), planning_block(item, manifest["repository"]))
        labels = {label["name"] for label in issue.get("labels", [])}
        changes = []
        if desired_body != (issue.get("body") or ""):
            changes.append("planning-block")
        if (
            project_native_types and type_name(issue.get("type")) != item["native_type"]
        ):
            changes.append("native-type")
        if labels != desired_labels(issue, item["type"]):
            changes.append("type-label")
        parent_url = issue.get("parent_issue_url")
        current_parent = int(parent_url.rstrip("/").split("/")[-1]) if parent_url else None
        if current_parent != item["parent_github_number"]:
            changes.append("parent")
        if changes:
            actions.append({"issue": item["github_number"], "changes": changes})
    return actions


MAINTAINER_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


def live_parent(issue):
    url = issue.get("parent_issue_url")
    return int(url.rstrip("/").split("/")[-1]) if url else None


def adoptable_parent(item, issue, by_number):
    """GitHub records a mapped parent the map does not; only that case is safe to adopt."""
    return (
        item["parent_github_number"] is None
        and live_parent(issue) is not None
        and live_parent(issue) in by_number
    )


def live_lifecycle(issue):
    return "active" if issue["state"] == "open" else "completed"


def accepted_for_delivery(issue):
    """A community issue may wait for triage; maintainer-filed or triaged work needs an ID."""
    if issue.get("state_reason") in {"not_planned", "duplicate"}:
        return False
    labels = {label["name"] for label in issue.get("labels", [])}
    return (
        issue.get("author_association") in MAINTAINER_ASSOCIATIONS
        or issue.get("milestone") is not None
        or any(label.startswith("type::") for label in labels)
    )


def within_grace(issue, grace_days, now):
    created = issue.get("created_at")
    if not grace_days or not created:
        return False
    opened = dt.datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ")
    return (now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)) - opened < dt.timedelta(days=grace_days)


def live_findings(manifest, live_issues, check_lifecycle=True, grace_days=0, now=None, check_unreserved=True):
    """Compare the manifest with GitHub without changing either; returns (findings, notices).

    With check_unreserved false, an accepted issue past its grace period without a BMad ID is a
    notice starting with WARNING instead of a finding: the backlog stays visible without holding
    every other finding class hostage to it.
    """
    live = {issue["number"]: issue for issue in live_issues}
    mapped = {item["github_number"] for item in manifest["items"]}
    findings = []
    notices = []
    for number in sorted(set(live) - mapped):
        if accepted_for_delivery(live[number]) and within_grace(live[number], grace_days, now):
            notices.append("#{}: accepted, no BMad ID yet, inside the grace period".format(number))
        elif accepted_for_delivery(live[number]):
            message = "#{}: accepted issue has no BMad ID; run reserve".format(number)
            if check_unreserved:
                findings.append(message)
            else:
                notices.append(WARNING + message)
        else:
            notices.append("#{}: awaiting triage, no BMad ID yet".format(number))
    by_number = {item["github_number"]: item for item in manifest["items"]}
    for item in manifest["items"]:
        issue = live.get(item["github_number"])
        if not issue:
            continue
        label = "{} #{}".format(item["bmad_id"], item["github_number"])
        if issue["title"] != item["title"]:
            findings.append("{}: title drift; run refresh".format(label))
        if live_lifecycle(issue) != item["lifecycle"]:
            message = "{}: GitHub is {} but the manifest says {}; run refresh".format(
                label, issue["state"], item["lifecycle"]
            )
            (findings if check_lifecycle else notices).append(message)
        if adoptable_parent(item, issue, by_number):
            findings.append(
                "{}: GitHub records parent #{} and the manifest records none; run refresh".format(
                    label, live_parent(issue)
                )
            )
        elif live_parent(issue) is not None and item["parent_github_number"] is None:
            findings.append(
                "{}: GitHub records parent #{}, which has no BMad ID; run reserve for it first".format(
                    label, live_parent(issue)
                )
            )
        elif live_parent(issue) is not None and live_parent(issue) != item["parent_github_number"]:
            findings.append(
                "{}: parent conflict, GitHub #{} against manifest #{}; decide which is right".format(
                    label, live_parent(issue), item["parent_github_number"]
                )
            )
    for item in manifest["items"]:
        # One item at a time, so a malformed body is that issue's finding rather than the audit's end.
        try:
            actions = planned_actions(dict(manifest, items=[item]), live_issues)
        except RuntimeError as error:
            findings.append("#{}: {}".format(item["github_number"], error))
            continue
        issue = live.get(item["github_number"])
        for action in actions:
            if action.get("action") == "missing":
                findings.append("#{}: mapped issue was not found on GitHub".format(action["issue"]))
                continue
            changes = [
                change for change in action["changes"]
                # A parent only GitHub records is reported above; apply still owns the other direction.
                if not (change == "parent" and issue is not None and live_parent(issue) is not None)
            ]
            if changes:
                findings.append(
                    "#{}: projection drift ({}); run apply".format(action["issue"], ", ".join(changes))
                )
    return findings, notices


def strip_updated(value):
    return re.sub(r"(?m)^updated: .*$", "updated:", value)


def is_unamended_legacy_stub(item, text):
    """Compare in LF, so a checkout with core.autocrlf behaves like any other."""
    return strip_updated(legacy_text(text)) == strip_updated(render_legacy_stub(item))


def refresh(manifest, live_issues):
    """Copy GitHub's title and open/closed state into the manifest and its artifacts.

    A typed story file keeps its body byte for byte: only the frontmatter, the H1 and the managed
    block are rewritten. A legacy stub is re-rendered whole, so one that carries amendments is refused.
    """
    live = {issue["number"]: issue for issue in live_issues}
    by_number = {item["github_number"]: item for item in manifest["items"]}
    drifted = []
    amended = []
    malformed = []
    for item in manifest["items"]:
        issue = live.get(item["github_number"])
        if not issue:
            continue
        if (
            issue["title"] == item["title"]
            and live_lifecycle(issue) == item["lifecycle"]
            and not adoptable_parent(item, issue, by_number)
        ):
            continue
        text = read_exact(ROOT / item["artifact_path"])
        try:
            layout = artifact_layout(item, text)
        except RuntimeError:
            malformed.append(item["bmad_id"])
            continue
        if layout is None and not is_unamended_legacy_stub(item, text):
            amended.append(item["bmad_id"])
        drifted.append((item, issue, text, layout))
    # Refuse before the first write: a half-applied refresh fails the audit that refresh requires.
    if malformed:
        raise RuntimeError("{}; repair it by hand: {}".format(MALFORMED, ", ".join(malformed)))
    if amended:
        raise RuntimeError(
            "artifact carries amendments; update its title and lifecycle by hand, or run upgrade: {}".format(
                ", ".join(amended)
            )
        )
    for item, issue, text, layout in drifted:
        item["title"] = issue["title"]
        item["lifecycle"] = live_lifecycle(issue)
        parent = by_number[live_parent(issue)] if adoptable_parent(item, issue, by_number) else None
        if parent:
            item["parent_github_number"] = parent["github_number"]
            item["parent_bmad_id"] = parent["bmad_id"]
        newline = newline_of(text)
        bom = BOM if text.startswith(BOM) else ""
        if layout is None:
            rendered = bom + with_newlines(render_legacy_stub(item), newline)
        else:
            # Lines the author keeps between the frontmatter and the H1 survive byte for byte.
            rendered = (
                bom
                + with_newlines(render_frontmatter_block(item), newline)
                + text[layout["frontmatter_end"]:layout["title_start"]]
                + with_newlines(render_title_block(item), newline)
                + text[layout["head_end"]:]
            )
        write_text_atomic(ROOT / item["artifact_path"], rendered)
    if drifted:
        write_manifest(manifest)
    return [item["bmad_id"] for item, _, _, _ in drifted]


def upgrade_text(item, text):
    """Convert one artifact to its typed skeleton; returns (status, new text or None, reason).

    The status is "current" for a file already in the typed format, "convert" for a legacy stub
    whose every byte outside the tool-rendered stub is carried into the new file verbatim, and
    "refuse" for anything whose conversion could lose text.
    """
    try:
        if artifact_layout(item, text) is not None:
            return "current", None, "already in the typed format"
    except RuntimeError as error:
        return "refuse", None, str(error)
    bom = BOM if text.startswith(BOM) else ""
    text = text[len(bom):]
    newline = newline_of(text)
    stub = render_legacy_stub(item)
    tail = with_newlines(stub[stub.rindex("\n", 0, len(stub) - 1) + 1:], newline)
    cut = text.find(tail)
    if cut < 0 or not is_unamended_legacy_stub(item, text[:cut + len(tail)]):
        return "refuse", None, "the stub differs from the rendered legacy stub; convert it by hand"
    # Everything after the rendered stub is someone's writing, typically `## Amendment` sections.
    carried = text[cut + len(tail):]
    skeleton = with_newlines(render_artifact(item), newline)
    if carried and not carried.startswith(("\n", "\r\n")):
        skeleton += newline
    converted = bom + skeleton + carried
    if sync_layout(converted) is None:
        return "refuse", None, "conversion did not produce the managed block"
    for key in ("bmad_id", "type", "title", "lifecycle", "provenance", "github_issue",
                "github_issue_url", "parent_bmad_id", "parent_github_issue"):
        if frontmatter_value(converted[len(bom):], key) != frontmatter_value(text, key):
            return "refuse", None, "conversion would change the frontmatter field {}".format(key)
    return "convert", converted, "converts without loss" + (
        ", carrying {} line(s) verbatim".format(len(carried.strip("\r\n").splitlines())) if carried.strip() else ""
    )


def upgrade(manifest, ids=None, check=False):
    """Convert legacy stubs to typed skeletons, all or none; returns [(bmad_id, status, reason)].

    Every conversion is computed before the first write, and a failed write restores the files
    already written from their originals.
    """
    items = manifest["items"]
    if ids:
        known = {item["bmad_id"] for item in items}
        unknown = sorted(set(ids) - known)
        if unknown:
            raise RuntimeError("not in the issue map: {}".format(", ".join(unknown)))
        items = [item for item in items if item["bmad_id"] in set(ids)]
    report = []
    writes = []
    for item in items:
        path = ROOT / item["artifact_path"]
        original = read_exact(path)
        status, converted, reason = upgrade_text(item, original)
        report.append((item["bmad_id"], status, reason))
        if status == "convert":
            writes.append((path, converted, original))
    if check or any(status == "refuse" for _, status, _ in report):
        return report
    written = []
    try:
        for path, converted, original in writes:
            # Recorded first: an interrupt inside the write still restores it, harmlessly if unreplaced.
            written.append((path, original))
            write_text_atomic(path, converted)
    except BaseException as error:
        unrestored = []
        for path, original in reversed(written):
            try:
                write_text_atomic(path, original)
            except Exception as restore_error:
                unrestored.append("{} ({})".format(path, restore_error))
        if unrestored:
            raise RuntimeError(
                "upgrade failed ({!r}) and these files could not be restored: {}".format(
                    error, "; ".join(unrestored)
                )
            ) from error
        raise
    if writes:
        write_sprint_status(manifest)
    return report


def comment_state(line, in_comment):
    """Whether an HTML comment is still open at the end of `line`."""
    position = 0
    while True:
        if in_comment:
            end = line.find("-->", position)
            if end < 0:
                return True
            in_comment, position = False, end + 3
        else:
            start = line.find("<!--", position)
            if start < 0:
                return False
            in_comment, position = True, start + 4


def markdown_sections(body):
    """Map each H2 heading in `body` to the list of its sections' text.

    ATX and setext headings of level 1 or 2 end a section; headings inside fenced code or an HTML
    comment are text.
    """
    sections = defaultdict(list)
    current = None
    fence = None
    in_comment = False
    paragraph = []
    for line in body.splitlines():
        if fence is not None:
            if fence_closes(line, fence):
                fence = None
            if current is not None:
                current.append(line)
            continue
        if not in_comment:
            setext = re.match(r"^ {0,3}(=+|-+)[ \t]*$", line)
            if setext and paragraph:
                # The paragraph above was the heading's text, not the previous section's content.
                if current is not None:
                    del current[len(current) - len(paragraph):]
                name = " ".join(text.strip() for text in paragraph)
                paragraph = []
                current = None
                if setext.group(1)[0] == "-":
                    current = []
                    sections[name.casefold()].append(current)
                continue
            fence = fence_opening(line)
            heading = None if fence else re.match(r"^ {0,3}(#{1,2})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$", line)
            if heading:
                paragraph = []
                current = None
                if len(heading.group(1)) == 2:
                    current = []
                    sections[(heading.group(2) or "").strip().casefold()].append(current)
                continue
            starts_comment = line.lstrip().startswith("<!--")
            if fence or not line.strip() or starts_comment or re.match(r"^ {0,3}(#{3,6}([ \t]|$)|[-*+][ \t]|\d+[.)][ \t]|>)", line):
                paragraph = []
            else:
                paragraph.append(line)
        else:
            paragraph = []
        in_comment = comment_state(line, in_comment)
        if current is not None:
            current.append(line)
    return {name: ["\n".join(lines) for lines in found] for name, found in sections.items()}


def section_filled(text):
    """Content remains once HTML comments, an unclosed one included, and sub-headings are taken out."""
    text = re.sub(r"<!--.*?(-->|\Z)", "", text, flags=re.DOTALL)
    text = re.sub(r"(?m)^ {0,3}#{3,6}([ \t].*)?$", "", text)
    return bool(text.strip())


def depth_findings(manifest, issue_number):
    """Check the story of one delivery issue; returns (findings, notices)."""
    item = next((entry for entry in manifest["items"] if entry["github_number"] == issue_number), None)
    if item is None:
        return ["#{}: no BMad ID; run reserve".format(issue_number)], []
    return item_depth_findings(item)


def item_depth_findings(item):
    """The depth check for one mapped item; returns (findings, notices)."""
    label = "{} #{}".format(item["bmad_id"], item["github_number"])
    path = ROOT / item["artifact_path"]
    if not path.is_file():
        return ["{}: missing artifact {}".format(label, item["artifact_path"])], []
    text = path.read_text(encoding="utf-8")
    try:
        layout = artifact_layout(item, text)
    except RuntimeError as error:
        return ["{}: {}".format(label, error)], []
    if layout is None:
        return [], [
            "{}: legacy stub, not depth-checked until upgraded "
            "(python3 scripts/bmad_issue_sync.py upgrade --id {})".format(label, item["bmad_id"])
        ]
    sections = markdown_sections(text[layout["head_end"]:])
    findings = []
    for name in REQUIRED[item["type"]]:
        found = sections.get(name.casefold())
        problem = None
        if not found:
            problem = "is missing"
        elif len(found) > 1:
            problem = "is a duplicate section"
        elif not section_filled(found[0]):
            problem = "is unfilled"
        if problem:
            findings.append("{}: required {} section '{}' {}".format(label, item["type"], name, problem))
    return findings, []


def verify_remote_artifacts(manifest, repo):
    tree = gh_json(["api", "repos/{}/git/trees/main?recursive=1".format(repo)])
    if tree.get("truncated"):
        raise RuntimeError("GitHub returned a truncated main tree; artifact presence is unknown")
    paths = {entry["path"] for entry in tree.get("tree", [])}
    return [item["artifact_path"] for item in manifest["items"] if item["artifact_path"] not in paths]


def apply_manifest(manifest, repo):
    errors = audit_manifest(manifest)
    if errors:
        raise RuntimeError("\n".join(errors))
    missing = verify_remote_artifacts(manifest, repo)
    if missing:
        raise RuntimeError("artifacts are not on main: {}".format(", ".join(missing[:5])))
    project_native_types = projection_mode(manifest) == "native-and-labels"
    live_issues = fetch_issues(repo)
    live = {issue["number"]: issue for issue in live_issues}
    missing_issues = [item["github_number"] for item in manifest["items"] if item["github_number"] not in live]
    if missing_issues:
        raise RuntimeError(
            "mapped issues were not found: {}".format(
                ", ".join("#{}".format(number) for number in missing_issues)
            )
        )
    planned_actions(manifest, live_issues)
    for kind in sorted({item["type"] for item in manifest["items"]}):
        gh_command(
            [
                "label", "create", "type::{}".format(kind), "-R", repo,
                "--description", "BMad work item type: {}".format(kind), "--color", "6f42c1", "--force",
            ]
        )
    by_number = {item["github_number"]: item for item in manifest["items"]}
    for item in manifest["items"]:
        issue = live[item["github_number"]]
        current_labels = {label["name"] for label in issue.get("labels", [])}
        projected_labels = desired_labels(issue, item["type"])
        desired_body = upsert_planning_block(issue.get("body"), planning_block(item, manifest["repository"]))
        payload = {}
        if desired_body != (issue.get("body") or ""):
            payload["body"] = desired_body
        if (
            project_native_types and type_name(issue.get("type")) != item["native_type"]
        ):
            payload["type"] = item["native_type"]
        if projected_labels != current_labels:
            payload["labels"] = sorted(projected_labels)
        if payload:
            gh_json(
                ["api", "--method", "PATCH", "-H", "X-GitHub-Api-Version: 2026-03-10", "repos/{}/issues/{}".format(repo, item["github_number"]), "--input", "-"],
                payload,
            )
    live_issues = fetch_issues(repo)
    live = {issue["number"]: issue for issue in live_issues}
    for item in manifest["items"]:
        issue = live[item["github_number"]]
        parent_url = issue.get("parent_issue_url")
        current_parent = int(parent_url.rstrip("/").split("/")[-1]) if parent_url else None
        desired_parent = item["parent_github_number"]
        if current_parent == desired_parent:
            continue
        child_id = issue["id"]
        if current_parent:
            gh_json(
                ["api", "--method", "DELETE", "-H", "X-GitHub-Api-Version: 2026-03-10", "repos/{}/issues/{}/sub_issue".format(repo, current_parent), "--input", "-"],
                {"sub_issue_id": child_id},
            )
        if desired_parent and desired_parent in by_number:
            try:
                gh_json(
                    ["api", "--method", "POST", "-H", "X-GitHub-Api-Version: 2026-03-10", "repos/{}/issues/{}/sub_issues".format(repo, desired_parent), "--input", "-"],
                    {"sub_issue_id": child_id},
                )
            except RuntimeError as error:
                if current_parent:
                    try:
                        gh_json(
                            ["api", "--method", "POST", "-H", "X-GitHub-Api-Version: 2026-03-10", "repos/{}/issues/{}/sub_issues".format(repo, current_parent), "--input", "-"],
                            {"sub_issue_id": child_id},
                        )
                    except RuntimeError as rollback_error:
                        raise RuntimeError(
                            "failed to set parent #{}: {}; failed to restore parent #{}: {}".format(
                                desired_parent, error, current_parent, rollback_error
                            )
                        ) from error
                raise


def git_output(args, input_data=None, text=True):
    """Run a read-only git command in ROOT, returning None when git cannot answer."""
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT)] + list(args),
            input=input_data,
            capture_output=True,
            text=text,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return None if result.returncode else result.stdout


def blobs_at_refs(refs):
    """Read one path out of many refs in a single git process, skipping refs without it."""
    if not refs:
        return {}
    query = "".join("{}:{}\n".format(ref, MAP_RELATIVE_PATH) for ref in refs)
    raw = git_output(["cat-file", "--batch"], input_data=query.encode("utf-8"), text=False)
    if raw is None:
        return {}
    blobs = {}
    offset = 0
    for ref in refs:
        end = raw.find(b"\n", offset)
        if end < 0:
            break
        header = raw[offset:end].split()
        if len(header) != 3 or header[1] != b"blob":
            offset = end + 1
            continue
        try:
            size = int(header[2])
        except ValueError:
            break
        blobs[ref] = raw[end + 1:end + 1 + size]
        offset = end + 1 + size + 1
    return blobs


def sibling_maps():
    """Every issue map this clone can see besides the one in this working tree.

    An uncommitted reservation in another worktree is exactly what a counter-only check
    misses, so each linked worktree is read from its working copy rather than its HEAD.
    """
    listing = git_output(["worktree", "list", "--porcelain"])
    for line in (listing or "").splitlines():
        if not line.startswith("worktree "):
            continue
        path = Path(line[len("worktree "):])
        try:
            if path.resolve() == ROOT.resolve():
                continue
            yield "worktree {}".format(path), (path / MAP_RELATIVE_PATH).read_bytes()
        except OSError:
            continue
    refs = (git_output(["for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes"]) or "").split()
    for ref, blob in blobs_at_refs(refs).items():
        yield "branch {}".format(re.sub(r"^refs/(heads|remotes)/", "", ref)), blob


def unreadable_remote_heads():
    """Heads git ls-remote advertises whose commits this clone holds no object for.

    Their maps cannot be scanned, so the survey below is incomplete by exactly this much.
    """
    remotes = git_output(["remote"])
    if remotes is None or "origin" not in remotes.split():
        return []
    git_output(["fetch", "--quiet", "origin"])
    listing = git_output(["ls-remote", "--heads", "origin"])
    if listing is None:
        return ["origin (could not be reached)"]
    heads = {}
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) == 2:
            heads[parts[1]] = parts[0]
    if not heads:
        return []
    query = "".join("{}\n".format(sha) for sha in sorted(set(heads.values())))
    raw = git_output(["cat-file", "--batch-check"], input_data=query)
    if raw is None:
        return sorted(heads)
    absent = {line.split()[0] for line in raw.splitlines() if line.strip().endswith(" missing")}
    return sorted(ref for ref, sha in heads.items() if sha in absent)


def survey_ids_elsewhere(kind):
    """Which IDs of `kind` sibling maps already spend, and how far their counters have run.

    Returns the in-use numbers mapped to where each was seen, the highest sibling counter,
    and the remote branches that could not be read.
    """
    pattern = re.compile(r"^AH-{}(\d{{3}})$".format(PREFIX[kind]))
    used = defaultdict(set)
    claimed = 1
    for source, text in sibling_maps():
        try:
            data = json.loads(text)
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        for item in data.get("items") or []:
            if not isinstance(item, dict):
                continue
            match = pattern.match(str(item.get("bmad_id")))
            if match:
                used[int(match.group(1))].add(source)
        counter = (data.get("next_ids") or {}).get(kind)
        if type(counter) is int and counter > claimed:
            claimed = counter
    return used, claimed, unreadable_remote_heads()


def collision_report(kind, sequence, used, claimed, unreadable):
    """The refusal text for a counter that a sibling map has already overtaken."""
    floor = max([claimed] + [number + 1 for number in used])
    if sequence >= floor:
        return None, floor
    lines = [
        "next {} ID AH-{}{:03d} is not free: the counter in {} trusts this checkout alone, and "
        "another branch or worktree has already gone past it".format(
            kind, PREFIX[kind], sequence, MAP_RELATIVE_PATH
        )
    ]
    for number in sorted(number for number in used if number >= sequence):
        lines.append(
            "  AH-{}{:03d} is already taken in {}".format(
                PREFIX[kind], number, ", ".join(sorted(used[number]))
            )
        )
    if claimed > sequence:
        lines.append("  a sibling map has already counted {} up to {}".format(kind, claimed))
    for ref in unreadable:
        lines.append("  warning: {} could not be scanned".format(ref))
    lines.append(
        "rebase onto the branch that took them; the {} range is exhausted, so there is "
        "nothing left to skip to".format(kind)
        if floor > 999
        else "rebase onto the branch that took them, or re-run with --advance to reserve "
        "AH-{}{:03d} and leave the gap".format(PREFIX[kind], floor)
    )
    return "\n".join(lines), floor


SPRINT_STATUS_RELATIVE_PATH = "_bmad-output/implementation-artifacts/sprint-status.yaml"
SPRINT_STATUS_COMMAND = "python3 scripts/bmad_issue_sync.py sprint-status"
SLUG_LENGTH = 48
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def sprint_key(item):
    """`<id>-<title slug>`, the slug cut at a word boundary so keys stay readable."""
    slug = re.sub(r"[^a-z0-9]+", "-", item["title"].lower()).strip("-")
    if len(slug) > SLUG_LENGTH:
        cut = slug.rfind("-", 0, SLUG_LENGTH + 1)
        slug = slug[:cut if cut > 0 else SLUG_LENGTH].strip("-")
    return "{}-{}".format(item["bmad_id"].lower(), slug) if slug else item["bmad_id"].lower()


def story_state(item):
    """(ready, updated) for one item's story file; a missing or malformed file is simply not ready."""
    path = ROOT / item["artifact_path"]
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False, None
    updated = frontmatter_value(text, "updated")
    updated = updated if isinstance(updated, str) and ISO_DATE.match(updated) else None
    try:
        typed = artifact_layout(item, text) is not None
    except RuntimeError:
        return False, updated
    return typed and not item_depth_findings(item)[0], updated


def render_sprint_status(manifest):
    """BMad's sprint-status.yaml, derived from the map and the story files; see docs/bmad.md.

    `generated` is the newest date the map or a story file records, never the clock, so an
    unchanged corpus renders byte for byte the same.
    """
    items = sorted(manifest["items"], key=lambda value: value["bmad_id"])
    by_id = {item["bmad_id"]: item for item in items}
    status = {}
    generated_at = manifest.get("generated_at")
    dates = [generated_at] if isinstance(generated_at, str) and ISO_DATE.match(generated_at) else []
    for item in items:
        ready, updated = story_state(item)
        if updated:
            dates.append(updated)
        if item["lifecycle"] == "completed":
            status[item["bmad_id"]] = "done"
        elif ready:
            status[item["bmad_id"]] = "ready-for-dev"
        else:
            status[item["bmad_id"]] = "backlog"

    def epic_of(item):
        # The nearest epic above the item; a story's own tasks sit in the story's epic block.
        seen = {item["bmad_id"]}
        parent = by_id.get(item["parent_bmad_id"])
        while parent is not None and parent["type"] != "epic" and parent["bmad_id"] not in seen:
            seen.add(parent["bmad_id"])
            parent = by_id.get(parent["parent_bmad_id"])
        return parent["bmad_id"] if parent is not None and parent["type"] == "epic" else None

    members = defaultdict(list)
    sub_epics = defaultdict(list)
    for item in items:
        if item["type"] == "epic":
            parent = epic_of(item)
            if parent is not None:
                sub_epics[parent].append(item["bmad_id"])
        else:
            members[epic_of(item)].append(item)

    def epic_status(bmad_id, visiting):
        # Done follows the epic's own lifecycle, like every other item; children only decide
        # between in-progress and backlog while it is open.
        if by_id[bmad_id]["lifecycle"] == "completed":
            return "done"
        children = [status[item["bmad_id"]] for item in members[bmad_id]]
        for child in sub_epics[bmad_id]:
            if child not in visiting:
                children.append(epic_status(child, visiting | {child}))
        if any(value in {"done", "ready-for-dev", "in-progress"} for value in children):
            return "in-progress"
        return "backlog"

    lines = [
        "# Derived from _bmad-output/issue-map.json and the story files. Do not edit by hand;",
        "# regenerate with: {}".format(SPRINT_STATUS_COMMAND),
        "# Status follows the map's lifecycle, which `refresh` copies from GitHub. done: the map",
        "# records the issue as closed. ready-for-dev: open, with a typed story that passes the",
        "# depth check. backlog: any other open item. An open epic is in-progress when any child",
        "# is done, ready or in progress, and backlog otherwise.",
        "",
        'generated: "{}"'.format(max(dates) if dates else "unknown"),
        "project: {}".format(manifest["repository"].rsplit("/", 1)[-1]),
        "project_key: AH",
        "tracking_system: github-issues",
        'story_location: "_bmad-output/implementation-artifacts"',
        "",
        "development_status:",
    ]
    blocks = []
    for item in items:
        if item["type"] == "epic":
            block = ["  {}: {}".format(sprint_key(item), epic_status(item["bmad_id"], {item["bmad_id"]}))]
            block += ["  {}: {}".format(sprint_key(child), status[child["bmad_id"]]) for child in members[item["bmad_id"]]]
            blocks.append(block)
    if members[None]:
        block = ["  # Items with no epic"]
        block += ["  {}: {}".format(sprint_key(child), status[child["bmad_id"]]) for child in members[None]]
        blocks.append(block)
    for index, block in enumerate(blocks):
        if index:
            lines.append("")
        lines.extend(block)
    if not blocks:
        lines[-1] = "development_status: {}"
    return "\n".join(lines) + "\n"


def current_sprint_status():
    """The committed file in LF, so a checkout with core.autocrlf compares like any other.

    Raises FileNotFoundError when it is absent, and OSError or UnicodeDecodeError when it cannot
    be read as text.
    """
    return read_exact(ROOT / SPRINT_STATUS_RELATIVE_PATH).replace("\r\n", "\n")


def write_sprint_status(manifest):
    """Write the derived file when its render changed; returns whether it wrote."""
    path = ROOT / SPRINT_STATUS_RELATIVE_PATH
    rendered = render_sprint_status(manifest)
    try:
        if current_sprint_status() == rendered:
            return False
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
    except UnicodeDecodeError:
        pass
    write_text_atomic(path, rendered)
    return True


def sprint_status_findings(manifest):
    """One finding when the committed file is missing, unreadable or differs from a fresh render."""
    try:
        current = current_sprint_status()
    except FileNotFoundError:
        return ["{} is missing; run {}".format(SPRINT_STATUS_RELATIVE_PATH, SPRINT_STATUS_COMMAND)]
    except (OSError, UnicodeDecodeError) as error:
        return ["{} cannot be read ({}); remove it and run {}".format(
            SPRINT_STATUS_RELATIVE_PATH, type(error).__name__, SPRINT_STATUS_COMMAND
        )]
    if current != render_sprint_status(manifest):
        return ["{} differs from the issue map and story files; run {}".format(
            SPRINT_STATUS_RELATIVE_PATH, SPRINT_STATUS_COMMAND
        )]
    return []


def reserve(manifest, repo, issue_number, kind, parent_number, advance=False):
    if any(item["github_number"] == issue_number for item in manifest["items"]):
        raise RuntimeError("issue #{} already has a BMad ID".format(issue_number))
    errors = audit_manifest(manifest)
    if errors:
        raise RuntimeError("\n".join(errors))
    issues = {issue["number"]: issue for issue in fetch_issues(repo)}
    issue = issues.get(issue_number)
    if not issue:
        raise RuntimeError("issue #{} was not found".format(issue_number))
    parent = next((item for item in manifest["items"] if item["github_number"] == parent_number), None)
    if parent_number is not None and parent is None:
        raise RuntimeError("parent issue #{} does not have a BMad ID".format(parent_number))
    sequence = manifest["next_ids"][kind]
    if type(sequence) is not int or sequence < 1 or sequence > 999:
        raise RuntimeError("next {} ID sequence is invalid: {}".format(kind, sequence))
    used, claimed, unreadable = survey_ids_elsewhere(kind)
    for ref in unreadable:
        print("notice: {} could not be scanned for IDs already in use".format(ref))
    report, floor = collision_report(kind, sequence, used, claimed, unreadable)
    if report and not advance:
        raise RuntimeError(report)
    if report:
        if floor > 999:
            raise RuntimeError("next {} ID sequence is invalid: {}".format(kind, floor))
        print("notice: --advance left AH-{}{:03d}..AH-{}{:03d} unused; they are taken elsewhere".format(
            PREFIX[kind], sequence, PREFIX[kind], floor - 1
        ))
        sequence = floor
    bmad_id = "AH-{}{:03d}".format(PREFIX[kind], sequence)
    if any(item["bmad_id"] == bmad_id for item in manifest["items"]):
        raise RuntimeError("next {} ID {} is already in use".format(kind, bmad_id))
    artifact_path = "_bmad-output/implementation-artifacts/{}.md".format(bmad_id)
    if (ROOT / artifact_path).exists():
        raise RuntimeError("target artifact already exists: {}".format(artifact_path))
    manifest["next_ids"][kind] = sequence + 1
    item = {
        "bmad_id": bmad_id,
        "github_number": issue_number,
        "github_url": issue["html_url"],
        "title": issue["title"],
        "type": kind,
        "native_type": NATIVE_TYPE[kind],
        "artifact_path": artifact_path,
        "parent_bmad_id": parent["bmad_id"] if parent else None,
        "parent_github_number": parent_number,
        "lifecycle": "active" if issue["state"] == "open" else "completed",
        "provenance": "authored",
    }
    manifest["items"].append(item)
    manifest["items"].sort(key=lambda value: value["github_number"])
    write_manifest(manifest)
    return item


def create_issue(manifest, repo, title, body, kind, parent_number, milestone, advance=False):
    """File an issue already typed, then reserve its ID, so new work never starts unmapped."""
    errors = audit_manifest(manifest)
    if errors:
        raise RuntimeError("\n".join(errors))
    if parent_number is not None and not any(
        item["github_number"] == parent_number for item in manifest["items"]
    ):
        raise RuntimeError("parent issue #{} does not have a BMad ID".format(parent_number))
    fields = {"title": title, "body": body, "labels": ["type::{}".format(kind)]}
    if milestone is not None:
        fields["milestone"] = milestone
    created = gh_json(
        ["api", "--method", "POST", "repos/{}/issues".format(repo), "--input", "-"],
        input_data=fields,
    )
    number = created.get("number") if isinstance(created, dict) else None
    if type(number) is not int:
        raise RuntimeError("GitHub did not return the new issue's number; check whether it was filed")
    try:
        # GitHub drops labels silently when the token cannot write them.
        if fields["labels"][0] not in {label["name"] for label in created.get("labels", [])}:
            raise RuntimeError("GitHub did not apply {}".format(fields["labels"][0]))
        return reserve(manifest, repo, number, kind, parent_number, advance)
    except (RuntimeError, OSError, KeyError, TypeError, ValueError) as error:
        raise RuntimeError(
            "issue #{} was filed but not reserved; fix the cause and run reserve --issue {}: {!r}".format(
                number, number, error
            )
        ) from error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    bootstrap_parser = subparsers.add_parser("bootstrap")
    bootstrap_parser.add_argument("--repo", default="JakeSelby/model-citizen")
    bootstrap_parser.add_argument(
        "--native-type-projection",
        choices=("labels-only", "native-and-labels"),
        required=True,
    )
    audit_parser = subparsers.add_parser("audit")
    audit_parser.add_argument("--live", action="store_true", help="also compare with GitHub, read-only")
    audit_parser.add_argument(
        "--ignore-lifecycle", action="store_true", help="with --live, report open/closed drift without failing"
    )
    audit_parser.add_argument(
        "--warn-unreserved",
        action="store_true",
        help="with --live, report accepted issues without a BMad ID as warnings, not findings",
    )
    audit_parser.add_argument(
        "--grace-days", type=int, default=0, help="with --live, days an accepted issue may wait for its ID"
    )
    audit_parser.add_argument(
        "--delivery", type=int, metavar="N", help="only check that issue N's story fills its kind's required sections"
    )
    upgrade_parser = subparsers.add_parser("upgrade", help="convert legacy stubs to their typed skeletons")
    upgrade_parser.add_argument("--id", action="append", dest="ids", metavar="BMAD_ID", help="repeatable; default all")
    upgrade_parser.add_argument("--check", action="store_true", help="report what would convert, writing nothing")
    subparsers.add_parser("refresh")
    sprint_parser = subparsers.add_parser("sprint-status", help="render the derived sprint-status.yaml")
    sprint_parser.add_argument("--check", action="store_true", help="exit 1 when the file is stale, writing nothing")
    subparsers.add_parser("plan")
    subparsers.add_parser("apply")
    reserve_parser = subparsers.add_parser("reserve")
    reserve_parser.add_argument("--issue", type=int, required=True)
    reserve_parser.add_argument("--kind", choices=KINDS, required=True)
    reserve_parser.add_argument("--parent", type=int)
    reserve_parser.add_argument("--advance", action="store_true", help=ADVANCE_HELP)
    new_parser = subparsers.add_parser("new")
    new_parser.add_argument("--title", required=True)
    new_parser.add_argument("--kind", choices=KINDS, required=True)
    new_parser.add_argument("--body-file", required=True, help="Markdown file holding the issue body")
    new_parser.add_argument("--parent", type=int)
    new_parser.add_argument("--milestone", type=int, help="milestone number, not its title")
    new_parser.add_argument("--advance", action="store_true", help=ADVANCE_HELP)
    args = parser.parse_args(argv)
    if args.command == "bootstrap":
        if MAP_PATH.exists():
            raise RuntimeError("issue map already exists")
        manifest = build_manifest(
            fetch_issues(args.repo), args.repo, args.native_type_projection
        )
        write_manifest(manifest)
        print("bootstrapped {} issues".format(len(manifest["items"])))
        return 0
    manifest = load_manifest()
    if args.command == "audit" and args.delivery is not None:
        if args.live:
            raise RuntimeError("--delivery is a local check; run --live separately")
        errors, notices = depth_findings(manifest, args.delivery)
        for notice in notices:
            print("notice: {}".format(notice))
        for error in errors:
            print(error)
        print("depth: issue #{}, {} finding(s)".format(args.delivery, len(errors)))
        return 1 if errors else 0
    if args.command == "upgrade":
        errors = audit_manifest(manifest)
        if errors:
            raise RuntimeError("\n".join(errors))
        report = upgrade(manifest, args.ids, args.check)
        counts = defaultdict(int)
        for bmad_id, status, reason in report:
            counts[status] += 1
            if status != "current":
                print("{}: {}".format(bmad_id, reason))
        refused = counts["refuse"]
        verb = "convertible" if args.check or refused else "converted"
        print("upgrade{}: {} {}, {} already current, {} refused{}".format(
            " --check" if args.check else "", counts["convert"], verb, counts["current"], refused,
            "; nothing written" if refused and not args.check else "",
        ))
        return 1 if refused else 0
    if args.command == "sprint-status":
        if args.check:
            findings = sprint_status_findings(manifest)
            for finding in findings:
                print(finding)
            return 1 if findings else 0
        wrote = write_sprint_status(manifest)
        print("sprint-status: {} {}".format(SPRINT_STATUS_RELATIVE_PATH, "written" if wrote else "unchanged"))
        return 0
    if args.command == "audit":
        errors = audit_manifest(manifest, sprint_status=True)
        notices = []
        if args.live and errors:
            notices.append("live comparison skipped until the local findings below are fixed")
        if args.live and not errors:
            errors, notices = live_findings(
                manifest,
                fetch_issues(API_REPOSITORY),
                not args.ignore_lifecycle,
                args.grace_days,
                check_unreserved=not args.warn_unreserved,
            )
        for notice in notices:
            print(notice if notice.startswith(WARNING) else "notice: {}".format(notice))
        warnings = sum(1 for notice in notices if notice.startswith(WARNING))
        for error in errors:
            print(error)
        summary = "audit: {} issue(s), {} finding(s)".format(len(manifest["items"]), len(errors))
        print(summary + (", {} warning(s)".format(warnings) if warnings else ""))
        return 1 if errors else 0
    if args.command == "refresh":
        errors = audit_manifest(manifest)
        if errors:
            raise RuntimeError("\n".join(errors))
        changed = refresh(manifest, fetch_issues(API_REPOSITORY))
        print("refresh: {} item(s) updated".format(len(changed)))
        return 0
    if args.command == "plan":
        errors = audit_manifest(manifest)
        if errors:
            raise RuntimeError("\n".join(errors))
        print(json.dumps(planned_actions(manifest, fetch_issues(API_REPOSITORY)), indent=2))
        return 0
    if args.command == "apply":
        apply_manifest(manifest, API_REPOSITORY)
        remaining = planned_actions(manifest, fetch_issues(API_REPOSITORY))
        print("apply: {} remaining action(s)".format(len(remaining)))
        return 1 if remaining else 0
    if args.command == "new":
        body = Path(args.body_file).read_text(encoding="utf-8")
        item = create_issue(
            manifest,
            API_REPOSITORY,
            args.title,
            body,
            args.kind,
            args.parent,
            args.milestone,
            args.advance,
        )
        print("filed #{} and reserved {}".format(item["github_number"], item["bmad_id"]))
        return 0
    item = reserve(manifest, API_REPOSITORY, args.issue, args.kind, args.parent, args.advance)
    print("reserved {} for issue #{}".format(item["bmad_id"], item["github_number"]))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print("bmad issue sync: {}".format(error), file=sys.stderr)
        sys.exit(1)

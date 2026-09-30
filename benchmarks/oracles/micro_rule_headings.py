"""Held-back check for the micro `micro-delegation` task: every rule file's title, read from the tree."""
import json
from pathlib import Path

OUTPUT = "rule-headings.json"


def expected(root):
    out = {}
    for path in sorted((Path(root) / "claude" / "rules").glob("*.md")):
        titles = [line[2:].strip() for line in path.read_text(encoding="utf-8").splitlines()
                  if line.startswith("# ")]
        out[path.name] = titles[0] if titles else ""
    return out


def check(root):
    """Errors, empty when the file maps exactly every rule file to its first level-one heading."""
    path = Path(root) / OUTPUT
    if not path.is_file():
        return ["%s is missing" % OUTPUT]
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return ["%s is not JSON" % OUTPUT]
    want = expected(root)
    if not isinstance(got, dict) or sorted(got) != sorted(want):
        return ["the keys are not exactly the %d rule file names" % len(want)]
    return ["%s: the heading should be %r" % (name, title) for name, title in sorted(want.items())
            if " ".join(str(got[name]).split()) != title]


def solve(root):
    (Path(root) / OUTPUT).write_text(json.dumps(expected(root), indent=2) + "\n", encoding="utf-8")

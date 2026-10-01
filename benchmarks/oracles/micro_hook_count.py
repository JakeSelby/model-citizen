"""Held-back check for the micro `micro-output-style` task: the hook script count, in a file."""
from pathlib import Path

OUTPUT = "hook-count.txt"
# The count at the task's `parent_sha` in `benchmarks/micro/tasks.json`, pinned so an arm that adds
# a hook script cannot move the answer it is scored against.
EXPECTED_HOOKS = 8


def check(root):
    """Errors, empty when the file holds the number of Python hook scripts and nothing else."""
    found = len(list((Path(root) / "claude" / "hooks").glob("*.py")))
    if found != EXPECTED_HOOKS:
        return ["expected %d hook scripts in the tree, found %d" % (EXPECTED_HOOKS, found)]
    path = Path(root) / OUTPUT
    if not path.is_file():
        return ["%s is missing" % OUTPUT]
    text = path.read_text(encoding="utf-8").strip()
    return [] if text == str(EXPECTED_HOOKS) else ["%s holds %r, not %d" % (OUTPUT, text[:40], EXPECTED_HOOKS)]


def solve(root):
    (Path(root) / OUTPUT).write_text("%d\n" % EXPECTED_HOOKS, encoding="utf-8")

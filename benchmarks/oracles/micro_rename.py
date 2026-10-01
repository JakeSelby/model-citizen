"""Held-back check for the micro `micro-stop-gate` task: one function renamed inside `bin/harness`.

The tests at the task's parent still call the old name, so a tree that makes only this edit is red
under the repository's gate. That is the point of the task: it gives the harness arm's stop gate a
red tree to refuse. This check reads `bin/harness` alone, so an arm that also mends the tests passes
exactly as one that does not."""
import ast
from pathlib import Path

OLD, NEW = "strip_claude_settings", "unmerge_claude_settings"
SCRIPT = Path("bin") / "harness"


def check(root):
    """Errors, empty when `bin/harness` defines and calls the new name and never the old one."""
    path = Path(root) / SCRIPT
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        return ["%s does not parse: %s" % (SCRIPT.as_posix(), exc)]
    defined = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    named = [n.id for n in ast.walk(tree) if isinstance(n, ast.Name)]
    errors = []
    if NEW not in defined:
        errors.append("%s is not defined" % NEW)
    if OLD in defined or OLD in named:
        errors.append("%s is still defined or called" % OLD)
    if NEW not in named:
        errors.append("%s is never called" % NEW)
    return errors


def solve(root):
    path = Path(root) / SCRIPT
    path.write_text(path.read_text(encoding="utf-8").replace(OLD, NEW), encoding="utf-8")

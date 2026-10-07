"""Held-back check for the micro `micro-stop-gate` task: one function renamed inside `bin/harness`.

The tests at the task's parent still call the old name, so a tree that renames it in `bin/harness`
alone is red under the repository's gate. That is the point of the task: it gives the harness arm's
stop gate a red tree to refuse. The only edit that gate accepts is the rename with the tests that
call it updated to match, so the prompt allows that edit and this check requires it (#1170); a
check that froze the tests would fail every run the gate did its job in."""
import ast
from pathlib import Path

OLD, NEW = "strip_claude_settings", "unmerge_claude_settings"
SCRIPT = Path("bin") / "harness"
# The test modules that call the function at the task's parent sha.
CALLERS = ("tests/test_harness.py", "tests/test_neutralize.py", "tests/test_usage.py")


def _names(tree):
    """Every bare name and attribute name the module refers to."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.append(node.id)
        elif isinstance(node, ast.Attribute):
            out.append(node.attr)
    return out


def _parse(path, shown):
    try:
        return ast.parse(path.read_text(encoding="utf-8")), None
    except (OSError, SyntaxError, ValueError) as exc:
        return None, "%s does not parse: %s" % (shown, exc)


def check(root):
    """Errors, empty when `bin/harness` defines and calls the new name and never the old one, and
    no test still calls the old name, the three that called it now calling the new one."""
    root = Path(root)
    tree, error = _parse(root / SCRIPT, SCRIPT.as_posix())
    if error:
        return [error]
    defined = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    named = [n.id for n in ast.walk(tree) if isinstance(n, ast.Name)]
    errors = []
    if NEW not in defined:
        errors.append("%s is not defined" % NEW)
    if OLD in defined or OLD in named:
        errors.append("%s is still defined or called" % OLD)
    if NEW not in named:
        errors.append("%s is never called" % NEW)
    for path in sorted((root / "tests").rglob("*.py")):
        shown = path.relative_to(root).as_posix()
        module, error = _parse(path, shown)
        if error:
            errors.append(error)
            continue
        names = _names(module)
        if OLD in names:
            errors.append("%s still calls %s" % (shown, OLD))
        elif shown in CALLERS and NEW not in names:
            errors.append("%s no longer calls %s" % (shown, NEW))
    errors += ["%s is missing" % name for name in CALLERS if not (root / name).is_file()]
    return errors


def solve(root):
    for name in (SCRIPT.as_posix(),) + CALLERS:
        path = Path(root) / name
        path.write_text(path.read_text(encoding="utf-8").replace(OLD, NEW), encoding="utf-8")

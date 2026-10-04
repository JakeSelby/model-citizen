"""No test may leave a ``draft/*`` branch in the shared repository."""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from typing import List

import draft_support

ROOT = draft_support.ROOT
HOLD_LOCK = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from harness_core.studio import drafts
with drafts._locked(Path(sys.argv[2])):
    print("locked", flush=True)
    sys.stdin.read()
"""
GUARDS = {"register_draft_cleanup", "discard_draft"}
GUARDED_RUN = (
    "test_studio_selection_editing.SelectionEditingTests"
    ".test_saved_checkpoint_equals_sequential_real_config_set_output",
    "test_studio_selection_editing.SelectionEditingTests"
    ".test_json_type_change_is_checkpointed_and_reloads_as_boolean",
)
GUARDED_PREFIXES = ("selection-parity-", "selection-json-type-")


def _is_shared_create(node: ast.AST) -> bool:
    """A ``[sys.executable, <cli>, "draft", "create", ...]`` argument list: the real CLI,
    which creates the draft in the shared repository."""
    if not isinstance(node, ast.List) or not node.elts:
        return False
    first = node.elts[0]
    if not (isinstance(first, ast.Attribute) and first.attr == "executable"
            and isinstance(first.value, ast.Name) and first.value.id == "sys"):
        return False
    words = [element.value if isinstance(element, ast.Constant) else None
             for element in node.elts]
    return any(words[index:index + 2] == ["draft", "create"] for index in range(len(words)))


def _is_guard(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in GUARDS and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "draft_support")


def unguarded_creates(source: str, filename: str = "<source>") -> List[str]:
    """Each shared-repository ``draft create`` whose function registers no later guard.

    Every create needs its own ``draft_support`` guard after it in the same function, so a
    second create beside a guarded one is still reported.
    """
    found = []
    with warnings.catch_warnings():
        # Another test file's invalid escape sequence is not this scan's concern.
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(source, filename)
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        nested = {id(node) for inner in ast.walk(function)
                  if inner is not function
                  and isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                  for node in ast.walk(inner)}
        own = [node for node in ast.walk(function) if id(node) not in nested]
        creates = sorted(node.lineno for node in own if _is_shared_create(node))
        guards = sorted(node.lineno for node in own if _is_guard(node))
        for line in creates:
            later = [guard for guard in guards if guard > line]
            if later:
                guards.remove(later[0])
            else:
                found.append(f"{filename}:{line}")
    return found


CREATING_ROUTES = {"/api/first-run/start"}


def _words(elements) -> List[object]:
    return [element.value if isinstance(element, ast.Constant) else None for element in elements]


def _new_branch(elements):
    """The ``<name>`` of a ``"-b", "draft/" + <name>`` pair among git arguments, or ``None``."""
    words = _words(elements)
    for index, element in enumerate(elements[1:], 1):
        if (words[index - 1] == "-b" and isinstance(element, ast.BinOp)
                and isinstance(element.op, ast.Add) and isinstance(element.left, ast.Constant)
                and element.left.value == "draft/"):
            return element.right
    return None


def _created_name(node: ast.AST):
    """The draft-name expression of a create in the shared repository, or ``None``.

    Four shapes create one: the real CLI's ``draft create`` argument list, a test home's
    ``cli("draft", "create", name)``, a ``-b draft/<name>`` git argument list and a POST to a
    route that creates its draft, whose JSON body names it under ``draft``.
    """
    if isinstance(node, ast.List):
        words = _words(node.elts)
        if _is_shared_create(node):
            index = next(index for index in range(len(words))
                         if words[index:index + 2] == ["draft", "create"])
            return node.elts[index + 2] if index + 2 < len(node.elts) else None
        return _new_branch(node.elts)
    if not isinstance(node, ast.Call):
        return None
    branch = _new_branch(node.args)
    if branch is not None:
        return branch
    if (isinstance(node.func, ast.Attribute) and node.func.attr == "cli"
            and _words(node.args[:2]) == ["draft", "create"] and len(node.args) > 2):
        return node.args[2]
    if any(isinstance(arg, ast.Constant) and arg.value in CREATING_ROUTES for arg in node.args):
        for arg in node.args:
            if isinstance(arg, ast.Dict):
                for key, value in zip(arg.keys, arg.values):
                    if isinstance(key, ast.Constant) and key.value == "draft":
                        return value
    return None


def _is_draft_name(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "draft_name" and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "draft_support")


def _assigned(scope: ast.AST, matches) -> List[ast.AST]:
    """The value of every single-target assignment in ``scope`` whose target ``matches``."""
    values = []
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign):
            values += [node.value for target in node.targets if matches(target)]
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and matches(node.target):
            values.append(node.value)
    return values


def unstamped_creates(source: str, filename: str = "<source>") -> List[str]:
    """Each shared-repository draft create whose name does not come from ``draft_name``.

    The name is the call itself, a local whose every assignment in the function is one, or an
    attribute whose every assignment in the module is one. A parameter, a literal or a
    hand-built name is reported, since a leak check scoped to the run token cannot see it.
    """
    found = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(source, filename)
    functions = [node for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for node in ast.walk(tree):
        name = _created_name(node)
        if name is None:
            continue
        if isinstance(name, ast.Name):
            scope = min((function for function in functions
                         if function.lineno <= node.lineno <= function.end_lineno),
                        key=lambda function: function.end_lineno - function.lineno, default=tree)
            values = _assigned(scope, lambda target: isinstance(target, ast.Name)
                               and target.id == name.id)
        elif isinstance(name, ast.Attribute):
            values = _assigned(tree, lambda target: isinstance(target, ast.Attribute)
                               and target.attr == name.attr)
        else:
            values = [name]
        if not values or not all(_is_draft_name(value) for value in values):
            found.append((node.lineno, f"{filename}:{node.lineno}"))
    return [location for _line, location in sorted(found)]


def tearDownModule():
    leaked = draft_support.leaked_drafts(("cleanup-guard-",))
    if leaked:
        raise AssertionError("draft branches survived the cleanup tests: " + ", ".join(leaked))


class DraftCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        home = Path(os.path.realpath(temporary.name)) / "home"
        config = home / ".config" / "agent-harness" / "config.json"
        config.parent.mkdir(parents=True)
        config.write_bytes((ROOT / "config.example.json").read_bytes())
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("HARNESS_")}
        self.env.update({
            "HOME": str(home),
            "HARNESS_HOME": str(home),
            "HARNESS_WORKTREE_ROOT": str(Path(temporary.name) / "worktrees"),
        })
        self.name = draft_support.draft_name("cleanup-guard-")
        created = subprocess.run(
            [sys.executable, str(draft_support.CLI), "draft", "create", self.name, "--json"],
            cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(created.returncode, 0, created.stderr or created.stdout)
        # Registered before the holder so it runs after the holder is released.
        draft_support.register_draft_cleanup(self, self.name, self.env, missing_ok=True)
        self.worktree = Path(json.loads(created.stdout)["path"])

    def _hold_lock(self) -> subprocess.Popen:
        holder = subprocess.Popen(
            [sys.executable, "-c", HOLD_LOCK, str(ROOT / "lib"), str(self.worktree)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        self.addCleanup(self._release, holder)
        self.assertEqual(holder.stdout.readline().strip(), "locked")
        return holder

    @staticmethod
    def _release(holder: subprocess.Popen) -> None:
        if holder.poll() is None:
            holder.stdin.close()
            holder.wait(timeout=10)
        holder.stdout.close()

    def test_cleanup_stops_the_writer_before_discarding(self):
        holder = self._hold_lock()
        draft_support.discard_draft(self, self.name, self.env, stop=lambda: self._release(holder))
        self.assertFalse(draft_support.draft_branch_exists(self.name))
        self.assertFalse(draft_support.draft_worktree_registered(self.name))

    def test_cleanup_fails_the_test_when_the_branch_survives(self):
        self._hold_lock()
        with self.assertRaisesRegex(AssertionError, "busy"):
            draft_support.discard_draft(self, self.name, self.env, attempts=2)
        self.assertTrue(draft_support.draft_branch_exists(self.name))

    def test_cleanup_sees_a_busy_refusal_from_a_quiet_environment(self):
        self._hold_lock()
        quiet = dict(self.env, HARNESS_QUIET="1")
        with self.assertRaisesRegex(AssertionError, "busy"):
            draft_support.discard_draft(self, self.name, quiet, attempts=2)
        self.assertTrue(draft_support.draft_branch_exists(self.name))

    def test_cleanup_discards_even_when_stopping_the_studio_raises(self):
        def stop():
            raise subprocess.TimeoutExpired("studio", 10)

        with self.assertRaises(subprocess.TimeoutExpired):
            draft_support.discard_draft(self, self.name, self.env, stop=stop)
        self.assertFalse(draft_support.draft_branch_exists(self.name))
        self.assertFalse(draft_support.draft_worktree_registered(self.name))

    def test_guarded_studio_tests_leave_no_draft_branch(self):
        # A token of its own, so only the drafts this child run creates can count as leaks.
        token = draft_support.new_run_token()
        log = Path(self.env["HOME"]).parent / "draft-names"
        env = {key: value for key, value in os.environ.items() if not key.startswith("HARNESS_")}
        env.update({draft_support.RUN_TOKEN_ENV: token, draft_support.NAME_LOG_ENV: str(log)})
        run = subprocess.run(
            [sys.executable, "-m", "unittest", *GUARDED_RUN],
            cwd=ROOT / "tests", env=env, capture_output=True, text=True, timeout=300,
        )
        self.assertEqual(run.returncode, 0, run.stderr[-2000:])
        self.assertIn("Ran 2 tests", run.stderr)
        # Positive control: the child named its drafts with this token, so the filter sees them.
        created = log.read_text(encoding="utf-8").split()
        self.assertEqual(len(created), 2, created)
        for name in created:
            self.assertTrue(draft_support.owned_by(name, GUARDED_PREFIXES, token), name)
            self.assertFalse(draft_support.draft_branch_exists(name), f"draft/{name} leaked")
        self.assertEqual(draft_support.leaked_drafts(GUARDED_PREFIXES, token), [])


class GuardScanTests(unittest.TestCase):
    def test_every_shared_repository_draft_is_discarded_through_the_guard(self):
        unguarded = []
        for path in sorted((ROOT / "tests").glob("test_*.py")):
            unguarded += unguarded_creates(path.read_text(encoding="utf-8"), path.name)
        self.assertEqual(unguarded, [])

    def test_every_shared_repository_draft_is_named_with_the_run_token(self):
        unstamped = []
        for path in sorted((ROOT / "tests").glob("test_*.py")):
            unstamped += unstamped_creates(path.read_text(encoding="utf-8"), path.name)
        self.assertEqual(unstamped, [])

    def test_name_scan_reports_a_hand_built_name_in_every_create_shape(self):
        source = (
            "def test_a(self):\n"
            "    name = 'selection-parity-' + uuid.uuid4().hex\n"
            "    run([sys.executable, CLI, 'draft', 'create', name])\n"
            "    home.cli('draft', 'create', name, '--json')\n"
            "    git('worktree', 'add', '-b', 'draft/' + name, path)\n"
            "    self._post('/api/first-run/start', {'draft': name})\n"
            "    run([sys.executable, CLI, 'draft', 'create', 'literal'])\n"
        )
        self.assertEqual(unstamped_creates(source),
                         ["<source>:%d" % line for line in (3, 4, 5, 6, 7)])

    def test_name_scan_reports_a_parameter_and_a_mixed_attribute(self):
        source = (
            "def helper(self, name):\n"
            "    home.cli('draft', 'create', name)\n"
            "def setUp(self):\n"
            "    self.draft = draft_support.draft_name('x-')\n"
            "def other(self):\n"
            "    self.draft = 'x-' + secrets.token_hex(4)\n"
            "    run([sys.executable, CLI, 'draft', 'create', self.draft])\n"
        )
        self.assertEqual(unstamped_creates(source), ["<source>:2", "<source>:7"])

    def test_name_scan_accepts_names_from_draft_name(self):
        source = (
            "def setUp(self):\n"
            "    self.draft = draft_support.draft_name('x-')\n"
            "    run([sys.executable, CLI, 'draft', 'create', self.draft])\n"
            "def test_a(self, prefix):\n"
            "    name = draft_support.draft_name(prefix + '-')\n"
            "    home.cli('draft', 'create', name, '--json')\n"
            "    self._post('/api/first-run/start', {'draft': name})\n"
            "    run([sys.executable, CLI, 'draft', 'create', draft_support.draft_name('y-')])\n"
        )
        self.assertEqual(unstamped_creates(source), [])

    def test_scan_catches_a_multi_line_create_without_a_guard(self):
        source = (
            "def test_a(self):\n"
            "    subprocess.run(\n"
            "        [sys.executable, str(CLI),\n"
            "         'draft',\n"
            "         'create', name, '--json'],\n"
            "    )\n"
        )
        self.assertEqual(unguarded_creates(source), ["<source>:3"])

    def test_scan_needs_one_guard_per_create(self):
        source = (
            "def test_a(self):\n"
            "    run([sys.executable, CLI, 'draft', 'create', one])\n"
            "    draft_support.register_draft_cleanup(self, one, env)\n"
            "    run([sys.executable, CLI, 'draft', 'create', two])\n"
        )
        self.assertEqual(unguarded_creates(source), ["<source>:4"])

    def test_scan_rejects_a_guard_before_the_create_and_one_in_another_function(self):
        source = (
            "def helper(self):\n"
            "    draft_support.discard_draft(self, name, env)\n"
            "def test_a(self):\n"
            "    draft_support.register_draft_cleanup(self, name, env)\n"
            "    run([sys.executable, CLI, 'draft', 'create', name])\n"
        )
        self.assertEqual(unguarded_creates(source), ["<source>:5"])

    def test_scan_accepts_every_create_with_its_own_later_guard(self):
        source = (
            "def test_a(self):\n"
            "    run([sys.executable, CLI, 'draft', 'create', one])\n"
            "    draft_support.register_draft_cleanup(self, one, env)\n"
            "    run([sys.executable, CLI, 'draft', 'create', two])\n"
            "    draft_support.discard_draft(self, two, env)\n"
        )
        self.assertEqual(unguarded_creates(source), [])


if __name__ == "__main__":
    unittest.main()

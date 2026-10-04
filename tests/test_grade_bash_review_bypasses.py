# SPDX-License-Identifier: MIT
"""Each route around the grade-3 gate that review found grades 3, and the work it guards stays.

A program that hands `os.system` on without calling it, an inline `python -c`, a `git -c` that
redefines what a command does, an option git reads as a prefix, a ref moved by force, a delete
whose path the grader cannot know, a path outside the repository beside one inside it, a `cd`,
a file git is told not to check, an overwrite of uncommitted work, a status check cut short by
its budget and a confirmed grade-3 command each once graded below 3 or wrote no log row.

Run: python3 -m unittest discover tests
"""
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from test_grade_bash import CWD, grade, grader
from test_grade_bash_decision_log import Base

library = grader.library
GIT = shutil.which("git")
FORCE = "git push --force origin main"
STORE = "~/.local/state/agent-harness/approvals"


class ProgramsThatRunCommands(unittest.TestCase):
    # Findings 1 and 3: a reference in any form, and an inline program, run what they name.
    RUNS = [
        "python3 - <<'EOF'\nimport os\nlist(map(os.system, [\"{v}\"]))\nEOF",
        "python3 - <<'EOF'\nimport os\nf = os.system\nf(\"{v}\")\nEOF",
        "python3 - <<'EOF'\nimport os\ngetattr(os, 'sys' + 'tem')('{v}')\nEOF",
        "python3 - <<'EOF'\nfrom os import system as s\ns('{v}')\nEOF",
        "python3 - <<'EOF'\nimport os as o\no.system('{v}')\nEOF",
        "python3 - <<'EOF'\nimport os\nr = os\nr.system('{v}')\nEOF",
        "node - <<'EOF'\nconst c = require('child' + '_process');\nc.execSync('{v}');\nEOF",
        "python -c \"import os; os.system('{v}')\"",
        "python3 -c \"import os; os.system('{v}')\"",
        "python3 -W ignore -c \"import subprocess; subprocess.run('{v}', shell=True)\"",
        "python3 -c \"import os, sys; os.system(sys.argv[1])\" \"{v}\"",
        "node -e \"require('child_process').execSync('{v}')\"",
        "node --eval=\"require('child_process').execSync('{v}')\"",
        "perl -e 'system(\"{v}\")'",
        "perl -ne 'system(\"{v}\")' f",
        "ruby -e 'system(\"{v}\")'",
    ]
    # Text an inline program only prints is still text.
    TEXT = [
        "python3 -c \"print('{v}')\"",
        "node -e \"console.log('{v}')\"",
        "perl -e 'print \"{v}\\n\"'",
    ]

    def test_a_program_that_names_a_runner_in_any_form_grades_three(self):
        for verb in (FORCE, "rm -rf /"):
            for template in self.RUNS:
                command = template.format(v=verb)
                with self.subTest(command=command):
                    self.assertEqual(grade(command), 3)

    def test_an_inline_program_that_only_prints_a_verb_is_text(self):
        for template in self.TEXT:
            command = template.format(v=FORCE)
            with self.subTest(command=command):
                self.assertLess(grade(command), 3)

    def test_a_reference_to_a_delete_or_a_runner_is_never_inert(self):
        for program in ("import shutil\nshutil.rmtree(p)", "import os\nos.remove(p)",
                        "import os\nos.unlink(p)", "from pathlib import Path\nPath(p).unlink()",
                        "Path(p).rmdir()", "import pty\npty.spawn(['sh'])",
                        "import os\nos.execvp('sh', ['sh'])", "import os\nos.spawnlp(0, 'sh')",
                        "f = os.system", "x = list(map(os.popen, cmds))"):
            with self.subTest(program=program):
                self.assertFalse(library._inert_program(program, {"python"}))


class StoreWrites(unittest.TestCase):
    # Finding 2: every form of a write to the approvals store grades 3.
    def test_every_form_of_a_store_write_grades_three(self):
        for command in (
                "python3 -c \"import pathlib; print('x', file=pathlib.Path('%s/s.json').open('w'))\""
                % STORE,
                "python3 -c \"import io; io.FileIO('%s/s.json', 'w')\"" % STORE,
                "python3 -c \"import pathlib; pathlib.Path('%s/s.json').write_text('{}')\"" % STORE,
                "python3 -c \"f = open; f('%s/s.json', 'a')\"" % STORE,
                "python3 -c \"import os; os.open('%s/s.json', os.O_WRONLY)\"" % STORE):
            with self.subTest(command=command):
                self.assertEqual(grader.grade_text(command, CWD)[:2], (3, "write to"))

    def test_a_read_of_the_store_is_still_a_read(self):
        command = "python3 -c \"import pathlib; print(pathlib.Path('%s/s.json').read_text())\"" % STORE
        self.assertNotEqual(grader.grade_text(command, CWD)[1], "write to")

    def test_open_writes_reads_the_mode_wherever_it_sits(self):
        for program, writes in (("open(p)", False), ("open(p, 'rb')", False),
                                ("open(p, mode='r')", False), ("Path(p).open()", False),
                                ("open(p, 'w')", True), ("open(p, 'r+')", True),
                                ("open(p, mode='x')", True), ("Path(p).open('a')", True),
                                ("open(p, m)", True), ("open(*a)", True), ("g = open", True)):
            with self.subTest(program=program):
                self.assertEqual(library._open_writes(program), writes)


class GitOptions(unittest.TestCase):
    # Findings 4, 5 and 6.
    THREE = [
        "git -c alias.p='push --force' p origin main",
        "git -c clean.requireForce=false clean -dx",
        "git -c remote.origin.mirror=true push",
        "git -c core.fsmonitor='rm -rf ~' status",
        "git config alias.p 'push --force'; git p origin main",
        "git config core.fsmonitor 'sleep 1.8'",
        "git config clean.requireForce false",
        "git push -o -n --force origin main",
        "git clean -fdx -e -n",
        "git clean --for -d",
        "git clean -dx",
        "git push --mirr origin",
        "git push --del origin x",
        "git push --prune origin",
        "git switch --discard x",
        "git switch -C main HEAD~9",
        "git branch -f main HEAD~9",
        "git branch -M other main",
        "git checkout -B main HEAD~9",
        "git update-ref -d refs/heads/x",
        "git update-ref refs/heads/main HEAD~9",
        "git checkout --pathspec-from-file=f",
        "git checkout --pathspec-from-file f",
        "git diff | git apply -R",
        "git apply --reverse p.diff",
    ]
    BELOW = [
        "git push -n --force origin main",
        "git push --dry-run --force origin main",
        "git clean -n",
        "git clean -fdn",
        "git checkout -b topic main",
        "git switch -c topic",
        "git branch topic",
        "git config user.name x",
        "git -c user.name=x commit -m hi",
        "git apply p.diff",
    ]

    def test_each_route_grades_three(self):
        for command in self.THREE:
            with self.subTest(command=command):
                self.assertEqual(grade(command), 3)

    def test_what_keeps_the_work_stays_below_three(self):
        for command in self.BELOW:
            with self.subTest(command=command):
                self.assertLess(grade(command), 3)

    def test_a_whole_option_is_not_read_as_the_prefix_of_a_longer_one(self):
        self.assertEqual(grader.grade_text(FORCE, CWD)[1], "git push --force")


class UnknowablePaths(unittest.TestCase):
    # Finding 7.
    def test_a_delete_of_a_path_the_grader_cannot_know_grades_three(self):
        for command in ("git diff --name-only | xargs rm", "git ls-files | xargs unlink",
                        "rm $(git diff --name-only)", "f=app.py; rm \"$f\"", "rm `cat list`",
                        "find . -exec rm {} +", "find . -type f -exec unlink {} \\;",
                        "find . -name x -delete"):
            with self.subTest(command=command):
                self.assertEqual(grade(command), 3)


@unittest.skipUnless(GIT, "git is not installed")
class Repository(unittest.TestCase):
    """Findings 4 (an alias in git's own configuration), 8, 9 and 10 against a real repository
    holding a clean file, a file with working-tree changes and an untracked one."""

    def setUp(self):
        library._DIRTY.clear()
        self.addCleanup(library._DIRTY.clear)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = os.path.realpath(tmp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test" + "@" + "example.invalid")
        self.git("config", "user.name", "test")
        os.mkdir(os.path.join(self.repo, "src"))
        for name in ("clean.py", "dirty.py", "src/app.py"):
            self.write(name, "one\n")
        self.git("add", ".")
        self.git("commit", "-qm", "init")
        self.write("dirty.py", "two\n")
        self.write("src/app.py", "two\n")
        self.write("new.txt", "untracked\n")

    def git(self, *args):
        subprocess.run([GIT, "-C", self.repo] + list(args), check=True, capture_output=True,
                       stdin=subprocess.DEVNULL)

    def write(self, name, text):
        with open(os.path.join(self.repo, name), "w") as stream:
            stream.write(text)

    def grade(self, command):
        return grader.grade_text(command, self.repo)

    def test_an_alias_in_gits_configuration_grades_as_what_it_runs(self):
        self.git("config", "alias.wipe", "reset --hard")
        self.git("config", "alias.st", "status")
        self.assertEqual(self.grade("git wipe")[:2], (3, "git reset --hard"))
        self.assertLess(self.grade("git st")[0], 3)

    def test_each_path_is_asked_about_on_its_own(self):
        with tempfile.TemporaryDirectory() as outside:
            elsewhere = os.path.join(os.path.realpath(outside), "x")
            self.assertEqual(self.grade("rm %s dirty.py" % elsewhere)[0], 3)
            self.assertEqual(self.grade("rm %s clean.py" % elsewhere)[0], 1)

    def test_a_cd_on_the_line_moves_the_check(self):
        self.assertEqual(self.grade("cd src && rm app.py")[0], 3)
        self.assertEqual(self.grade("cd src; : > app.py")[0], 3)
        self.assertEqual(self.grade("cd $SOMEWHERE && rm app.py")[0], 3)

    def test_a_file_git_is_told_not_to_check_is_unknown(self):
        self.git("update-index", "--assume-unchanged", "clean.py")
        self.write("clean.py", "changed\n")
        grade, _verb, target, _family = self.grade(": > clean.py")
        self.assertEqual(grade, 3)
        self.assertIn(library.HIDDEN, target)
        self.git("update-index", "--no-assume-unchanged", "clean.py")
        self.git("update-index", "--skip-worktree", "clean.py")
        self.assertEqual(self.grade("rm clean.py")[0], 3)

    def test_a_find_bounded_by_name_is_asked_about_where_it_reaches(self):
        self.assertEqual(self.grade("find . -name app.py -exec rm {} +")[0], 3)
        self.assertEqual(self.grade("find src -name '*.py' -exec unlink {} \\;")[0], 3)
        self.assertLess(self.grade("find . -name '*.pyc' -exec rm {} +")[0], 3)
        self.assertEqual(self.grade("find . -not -name '*.pyc' -exec rm {} +")[0], 3)

    def test_an_untracked_file_grades_as_git_clean_does(self):
        self.assertEqual(self.grade("rm new.txt")[0], 3)
        self.assertEqual(self.grade("git clean -f new.txt")[0], 3)

    def test_an_overwrite_of_uncommitted_work_grades_three(self):
        # Finding 9.
        for command in ("echo x > dirty.py", "cp clean.py dirty.py", "mv clean.py dirty.py",
                        "cp clean.py src", "echo -n \"\" > dirty.py", "cat </dev/null > dirty.py",
                        "sed -i '' d dirty.py", "sed -i d dirty.py", "sed -i -e '1,$d' dirty.py",
                        "echo x | tee dirty.py", "dd if=/dev/zero of=dirty.py"):
            with self.subTest(command=command):
                self.assertEqual(self.grade(command)[0], 3)

    def test_an_overwrite_that_keeps_the_work_stays_below_three(self):
        for command in ("echo x > clean.py", "echo x >> dirty.py", "cp clean.py out.txt",
                        "echo x | tee -a dirty.py", "sed -i 's/one/two/' dirty.py",
                        "echo x > new.txt"):
            with self.subTest(command=command):
                self.assertLess(self.grade(command)[0], 3)

    def test_a_check_past_its_budget_fails_closed_well_inside_the_deadline(self):
        # Finding 10: a slow status (an fsmonitor, a large tree) spends the line's budget, and
        # the command whose grade waited on it is refused rather than let through.
        self.assertLess(library.GIT_STATUS_TOTAL, library.GRADE_SECONDS / 2)

        def slow(argv, **kwargs):
            time.sleep(min(0.2, kwargs.get("timeout") or 0.2))
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        # A line asks git three times per repository (`_repo_state`); this budget runs out
        # before the third.
        with mock.patch.object(library, "GIT_STATUS_TOTAL", 0.3), \
                mock.patch.object(library.subprocess, "run", side_effect=slow):
            started = time.monotonic()
            grade, verb, target, _family = self.grade(": > a.py; : > b.py; : > clean.py")
            elapsed = time.monotonic() - started
        self.assertEqual(grade, 3)
        self.assertIn(library.UNCHECKED, target)
        self.assertLess(elapsed, 1.5)


class DeadlineFallback(unittest.TestCase):
    # Finding 10: past the deadline the raw text is refused for every discard route, and for
    # every command whose grade waits on git status.
    def test_the_fallback_refuses_every_discard_route(self):
        for command in ("git checkout -- .", "git restore app.py", "git stash drop",
                        "git stash clear", "git branch -D x", "git branch -f main HEAD~1",
                        "git update-ref -d refs/heads/x", "find . -delete", "rm app.py",
                        ": > app.py", "cp a app.py", "sed -i d app.py"):
            with self.subTest(command=command):
                self.assertEqual(library.timed_out(command)[0], 3)

    def test_the_fallback_leaves_a_read_open(self):
        self.assertLess(library.timed_out("ls -la")[0], 3)


class ConfirmedIsLogged(Base):
    # Finding 11: the marker's grade-3 pass writes a row in every permission mode.
    def test_a_confirmed_grade_three_command_writes_a_row_in_every_mode(self):
        for mode in ("default", "acceptEdits", "auto", "bypassPermissions", "plan"):
            with self.subTest(mode=mode):
                if self.log.exists():
                    self.log.unlink()
                self.run_hook("HARNESS_CONFIRMED=1 " + FORCE, mode=mode)
                rows = self.rows()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0].get("grade"), 3)

    def test_a_confirmed_grade_one_command_writes_no_graded_row(self):
        self.run_hook("HARNESS_CONFIRMED=1 make build")
        self.assertEqual([r for r in self.rows() if r.get("grade") == 3], [])


class RoundTwoEnvironment(unittest.TestCase):
    # Round two, item 1: a variable git or its programs run as a command, set for the command.
    THREE = [
        "GIT_SSH_COMMAND='%s' git fetch" % FORCE,
        "GIT_EXTERNAL_DIFF='rm -rf ~' git diff",
        "GIT_PAGER='rm -rf ~' git log",
        "GIT_EDITOR='rm -rf ~' git commit",
        "GIT_ASKPASS=./x.sh git fetch",
        "GIT_SSH=./x.sh git fetch",
        "PAGER='rm -rf ~' git log",
        "EDITOR='rm -rf ~' git commit",
        "GIT_CONFIG_COUNT=1 git status",
        "env GIT_SSH_COMMAND='rm -rf ~' git fetch",
        "export GIT_EXTERNAL_DIFF='rm -rf ~'",
    ]
    BELOW = [
        "GIT_PAGER=cat git log",
        "PAGER= git log",
        "GIT_EDITOR=true git rebase --continue",
        "LANG=C git status",
    ]

    def test_a_variable_that_runs_a_command_grades_three(self):
        for command in self.THREE:
            with self.subTest(command=command):
                self.assertEqual(grade(command), 3)

    def test_an_inert_pager_or_editor_stays_below_three(self):
        for command in self.BELOW:
            with self.subTest(command=command):
                self.assertLess(grade(command), 3)


class RoundTwoPrograms(unittest.TestCase):
    # Round two, items 2 to 5.
    THREE = [
        "node -pe \"require('child_process').execSync('%s')\"" % FORCE,
        "deno eval \"new Deno.Command('sh',{args:['-c','rm -rf ~']}).outputSync()\"",
        "python3 -m timeit -n1 -r1 \"__import__('os').system('rm -rf ~')\"",
        "python3 -mtimeit \"__import__('os').system('rm -rf ~')\"",
        "python3 -c \"import os; os.remove(p)\"",
        "python3 -c \"import shutil, sys; shutil.rmtree(sys.argv[1])\" src",
        "python3 -c \"from pathlib import Path; p.unlink()\"",
        "node -e \"require('fs').rmSync(dir, {recursive: true})\"",
        "perl -e 'unlink $ARGV[0]' f",
    ]
    BELOW = [
        "python3 -c \"print('open the unlink')\"",
        "python3 -c \"print(open('README.md').read())\"",
        "node -pe \"1+1\"",
        "python3 -m pytest -q tests",
        "python3 -m json.tool data.json",
        "python3 -c \"import shutil; shutil.rmtree('/tmp/scratch-dir')\"",
        # A write to a path the program computes grades as `> "$f"` does.
        "python3 -c \"f = open(name, 'w')\"",
    ]

    def test_each_route_grades_three(self):
        for command in self.THREE:
            with self.subTest(command=command):
                self.assertEqual(grade(command), 3)

    def test_a_program_that_deletes_nothing_it_cannot_name_stays_below_three(self):
        for command in self.BELOW:
            with self.subTest(command=command):
                self.assertLess(grade(command), 3)

    def test_a_short_option_cluster_hands_its_program_on(self):
        self.assertEqual(library._inline_programs("node", ["-pe", "x"]), ["x"])
        self.assertEqual(library._inline_programs("deno", ["eval", "--quiet", "x"]), ["x"])
        self.assertEqual(library._inline_programs("python", ["-m", "timeit", "-n1", "x"]), ["x"])

    def test_a_call_named_only_inside_a_string_is_text(self):
        self.assertEqual(library._program_targets("print('os.remove(p)')"), ([], [], False))


class RoundTwoGitRoutes(unittest.TestCase):
    # Round two, items 7 and 8 and the index-only false refusals.
    THREE = [
        "git send-pack --force origin main",
        "git send-pack origin +main",
        "git fetch -f . HEAD~9:main",
        "git fetch origin +main:main",
        "git fetch --force origin main:refs/heads/main",
        "git config alias.x '!rm -rf ~'",
        "git config diff.py.textconv cat",
        "git config core.hooksPath /tmp/hooks",
        "git config core.editor vi",
        "git config filter.x.clean cat",
        "git diff | patch -R -p1",
        "patch --reverse -p1 < fix.diff",
        "git restore --staged --worktree app.py",
        "git restore app.py",
        "git reset --hard",
        "git reset --merge",
    ]
    BELOW = [
        "git fetch origin",
        "git fetch origin main:topic",
        "git fetch origin +refs/heads/*:refs/remotes/origin/*",
        "git send-pack origin main",
        "git config alias.st status",
        "git reset",
        "git reset HEAD app.py",
        "git reset -- app.py",
        "git restore --staged app.py",
        "git restore -S app.py",
        "patch -p1 < fix.diff",
    ]

    def test_each_route_grades_three(self):
        for command in self.THREE:
            with self.subTest(command=command):
                self.assertEqual(grade(command), 3)

    def test_what_keeps_the_work_stays_below_three(self):
        for command in self.BELOW:
            with self.subTest(command=command):
                self.assertLess(grade(command), 3)


class RoundTwoGitInternals(unittest.TestCase):
    # Round two, item 9: what git runs from its own directory is never written below grade 3.
    THREE = [
        "printf '[core]\\n\\tfsmonitor = \"rm -rf ~ #\"\\n' >> .git/config",
        "printf 'x' > .git/hooks/pre-commit",
        "chmod +x .git/hooks/pre-commit",
        "cp evil .git/hooks/post-checkout",
        "echo x >> .git/info/attributes",
        "echo x >> /work/repo/.git/config",
        "cd .git && echo x >> config",
        "echo x >> \"$(git rev-parse --git-dir)/hooks/pre-push\"",
        "python3 -c \"open('.git/config', 'a').write('x')\"",
    ]
    BELOW = [
        "cat .git/config",
        "echo node_modules >> .gitignore",
        "git clone https://example.invalid/x/y.git dest",
    ]

    def test_a_write_into_gits_directory_grades_three(self):
        for command in self.THREE:
            with self.subTest(command=command):
                self.assertEqual(grade(command), 3)

    def test_a_read_of_it_or_a_name_that_only_starts_alike_stays_below_three(self):
        for command in self.BELOW:
            with self.subTest(command=command):
                self.assertLess(grade(command), 3)

    def test_the_graders_git_runs_hardened(self):
        for setting in ("core.fsmonitor=false", "core.hooksPath=/dev/null"):
            self.assertIn(setting, library.GIT_HARDENED)
        planted = {"GIT_DIR": "/elsewhere", "GIT_SSH_COMMAND": "x", "GIT_EXTERNAL_DIFF": "x",
                   "GIT_CONFIG_PARAMETERS": "'core.fsmonitor=x'", "PAGER": "x", "EDITOR": "x"}
        with mock.patch.dict(os.environ, planted):
            env = library._git_env()
            reads = library._git_env(user_config=True)
        for name in planted:
            self.assertNotIn(name, env)
        self.assertEqual((env["GIT_CONFIG_NOSYSTEM"], env["GIT_CONFIG_GLOBAL"]), ("1", os.devnull))
        self.assertNotIn("GIT_CONFIG_GLOBAL", reads)


@unittest.skipUnless(GIT, "git is not installed")
class RoundTwoRepository(unittest.TestCase):
    """Round two against a real repository: the clean, dirty and untracked files of
    `Repository`, an ignored build artefact and a configuration planted to run a program."""
    setUp = Repository.setUp
    git = Repository.git
    write = Repository.write
    grade = Repository.grade

    def ignore(self):
        self.write(".gitignore", "*.o\n")
        self.write("x.o", "built\n")

    def test_a_program_that_deletes_or_writes_over_work_grades_three(self):
        for command in ("python3 -c \"import shutil; shutil.rmtree('src')\"",
                        "python3 -c \"import os; os.remove('dirty.py')\"",
                        "python3 -c \"import os; os.remove('clean.py')\"",
                        "python3 -c \"import os; os.remove('new.txt')\"",
                        "python3 -c \"from pathlib import Path; Path('dirty.py').write_text('')\"",
                        "python3 -c \"open('dirty.py', 'w')\"",
                        "node -e \"require('fs').rmSync('src', {recursive: true})\"",
                        "node -e \"require('fs').writeFileSync('dirty.py', '')\""):
            with self.subTest(command=command):
                self.assertEqual(self.grade(command)[0], 3)

    def test_a_program_that_edits_in_place_or_touches_only_ignored_files_stays_below_three(self):
        # A delete of a tracked file grades 3 from a program; a write over one grades as `>`
        # does, and one that writes back what it read is an edit, as `sed -i` is.
        self.ignore()
        for command in ("python3 -c \"import os; os.remove('x.o')\"",
                        "python3 -c \"open('out.json', 'w').write('{}')\"",
                        "python3 -c \"open('clean.py', 'w').write('x')\"",
                        "python3 -c \"p = 'dirty.py'; s = open(p).read(); "
                        "open(p, 'w').write(s.upper())\"",
                        "python3 -c \"from pathlib import Path; p = Path('dirty.py'); "
                        "p.write_text(p.read_text().upper())\"",
                        "python3 -c \"import sys\nfor p in sys.argv[1:]:\n"
                        "    s = open(p).read()\n    open(p, 'w').write(s)\" a b"):
            with self.subTest(command=command):
                self.assertLess(self.grade(command)[0], 3)

    def test_a_cd_through_a_variable_a_builtin_or_env_is_followed(self):
        for command in ("D=src; cd \"$D\" && : > app.py", "builtin cd src && rm app.py",
                        "command cd src && rm app.py", "env -C src rm app.py",
                        "env --chdir=src rm app.py", "cd \"$(mktemp -d)\" && : > app.py"):
            with self.subTest(command=command):
                self.assertEqual(self.grade(command)[0], 3)

    def test_an_overwrite_by_rsync_ln_or_git_mv_grades_three(self):
        for command in ("rsync clean.py dirty.py", "ln -sf clean.py dirty.py",
                        "ln -f clean.py dirty.py", "git mv -f clean.py dirty.py"):
            with self.subTest(command=command):
                self.assertEqual(self.grade(command)[0], 3)
        for command in ("ln -s clean.py link.py", "rsync clean.py copy.py"):
            with self.subTest(command=command):
                self.assertLess(self.grade(command)[0], 3)

    def test_a_delete_of_ignored_build_output_stays_below_three(self):
        self.ignore()
        for command in ("rm -f *.o", "rm x.o", "rm -f x.o"):
            with self.subTest(command=command):
                self.assertEqual(self.grade(command)[0], 1)
        for command in ("rm -f *.py", "rm -rf *.o", "rm new.txt"):
            with self.subTest(command=command):
                self.assertEqual(self.grade(command)[0], 3)

    def test_an_index_only_reset_or_restore_stays_below_three(self):
        self.git("add", "dirty.py")
        for command in ("git reset", "git reset HEAD dirty.py", "git restore --staged dirty.py"):
            with self.subTest(command=command):
                self.assertLess(self.grade(command)[0], 3)

    def test_one_line_asks_git_once_whatever_number_of_files_it_writes(self):
        line = "; ".join("echo %d > new%d.txt" % (n, n) for n in range(20))
        calls = []
        real = subprocess.run

        def counted(argv, **kwargs):
            calls.append(argv)
            return real(argv, **kwargs)

        with mock.patch.object(library.subprocess, "run", side_effect=counted):
            self.assertEqual(self.grade(line)[0], 1)
        self.assertEqual(len([c for c in calls if "status" in c]), 1)
        self.assertLessEqual(len(calls), 3)

    def test_a_planted_configuration_runs_nothing_while_the_grader_asks_git(self):
        marker = os.path.join(self.repo, "ran")
        script = os.path.join(self.repo, "planted.sh")
        self.write("planted.sh", "#!/bin/sh\ntouch '%s'\ncat\n" % marker)
        os.chmod(script, 0o755)
        self.write(".gitattributes", "*.py filter=planted\n")
        self.git("config", "core.fsmonitor", script)
        self.git("config", "filter.planted.clean", script)
        self.git("config", "filter.planted.required", "true")
        # The same file, re-stamped: git status re-reads it through the clean filter.
        stamp = time.time() + 5
        os.utime(os.path.join(self.repo, "clean.py"), (stamp, stamp))
        subprocess.run([GIT, "-C", self.repo, "status", "--porcelain"], capture_output=True,
                       stdin=subprocess.DEVNULL)
        self.assertTrue(os.path.exists(marker), "the planted configuration must be live")
        os.unlink(marker)
        os.utime(os.path.join(self.repo, "clean.py"), (stamp + 5, stamp + 5))
        for command in ("rm clean.py", ": > clean.py", "rm -f *.o"):
            with self.subTest(command=command):
                self.grade(command)
                self.assertFalse(os.path.exists(marker))


if __name__ == "__main__":
    unittest.main()

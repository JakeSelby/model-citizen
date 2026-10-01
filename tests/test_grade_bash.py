# SPDX-License-Identifier: MIT
"""Unit tests for the grade-bash PreToolUse hook.

The safety property under test: a command grades 3 only when something it does cannot be
undone, and every command the read-only hook approves grades 0 and produces no output. A
misgrade downwards is a missed prompt, which is what the native permission flow gives anyway;
a misgrade upwards costs one prompt, or one re-run with the confirm marker.

Run: python3 -m unittest discover tests
"""
import importlib.util
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

from isolation import without_config_dir

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "claude" / "hooks" / "grade-bash.py"
spec = importlib.util.spec_from_file_location("grade_bash", HOOK)
grader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(grader)
OWNERSHIP = json.loads((REPO / "claude" / "OWNERSHIP.json").read_text())

sys.path.insert(0, str(REPO / "tests"))
from test_allow_readonly_bash import ALLOW, APPROVED  # noqa: E402

CWD = "/work/repo"

sys.path.insert(0, str(REPO / "lib"))
from harness_core import lifecycle  # noqa: E402


def pre_tool_use_timeout():
    """The tightest PreToolUse timeout `harness sync` registers for any runtime, in seconds."""
    return min(lifecycle.registration(REPO, runtime)["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"]
               for runtime in ("claude-code", "codex"))


def cpu_growth(large, small, pairs=11, batch_seconds=0.01, floor=1e-9):
    """How the CPU cost of a call grows with its input: `(seconds, ratio)`, where `seconds` is
    the best per-call CPU time of `large()` and `ratio` is the median over `pairs` of `large()`'s
    per-call cost over `small()`'s, each timed in a batch of about `batch_seconds`.

    CPU time rather than wall time, so waiting for a core is not charged. The two batches of a
    pair run back to back, in alternating order, and the ratio is taken per pair: a machine
    whose cores differ in speed can move this process between a fast and a slow core under
    load, which changes both calls' cost alike within a pair but not across a best-of-each.
    The median discards the pairs a migration split. `floor` bounds `small()`'s cost from below
    for a call too cheap to time."""
    calls = (large, small)
    repeats = []
    for call in calls:
        call()
        start = time.process_time()
        call()
        once = time.process_time() - start
        repeats.append(max(1, math.ceil(batch_seconds / max(once, 1e-6))))
    best, ratios = math.inf, []
    for pair in range(pairs):
        cost = [0.0, 0.0]
        for index in ((0, 1) if pair % 2 == 0 else (1, 0)):
            start = time.process_time()
            for _ in range(repeats[index]):
                calls[index]()
            cost[index] = (time.process_time() - start) / repeats[index]
        best = min(best, cost[0])
        ratios.append(cost[0] / max(cost[1], floor))
    return best, statistics.median(ratios)


def grade(command):
    return grader.grade_text(command, CWD)[0]


def run(command, stance="execute", mode="default"):
    """Run the hook as a subprocess with an empty HOME, so no real config is read. Returns
    (permissionDecision, permissionDecisionReason), or (None, None) when it stays silent."""
    with tempfile.TemporaryDirectory() as home:
        env = dict(without_config_dir(), HOME=home, HARNESS_STANCE_AUTONOMY=stance)
        out = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps({
                "tool_name": "Bash",
                "cwd": CWD,
                "permission_mode": mode,
                "tool_input": {"command": command},
            }),
            capture_output=True, text=True, env=env,
        )
    if not out.stdout.strip():
        return None, None
    block = json.loads(out.stdout)["hookSpecificOutput"]
    return block["permissionDecision"], block["permissionDecisionReason"]


# Grade 0: read-only, the hook says nothing.
GRADE0 = [
    "ls -la",
    "git status",
    "git -C /repo log --oneline -5",
    "git diff --stat | head -40",
    "cat foo.txt | head -20",
    "grep -rn pattern src",
    "find . -name '*.py'",
    "gh pr view 12",
    "gh api repos/o/r",
    "gh pr list --json number",
    "npm view react version",
    "cargo tree",
    "sed -n '1,20p' file",
    "git status; git diff --stat",
    "(cd /repo && git status)",
    "echo $(git rev-parse HEAD)",
    "for f in a b c; do echo $f; done",
]

# Grade 1: writes, but nothing leaves this machine and nothing is unrecoverable.
GRADE1 = [
    "git commit -m 'feat: x'",
    "git add -A",
    "git stash",
    "git stash pop",
    "git checkout -b topic",
    "git merge origin/main",
    "git fetch origin",
    "git pull --rebase",
    "mkdir -p build",
    "touch notes.md",
    "cp a.txt b.txt",
    "mv a.txt b.txt",
    "rm -rf build",
    "rm -f target/debug/app",
    "rm notes.md",
    "echo hi > out.txt",
    "cargo build --release",
    "cargo test",
    "npm install",
    "npm run build",
    "pip install -r requirements.txt",
    "uv sync",
    "make clean",
    "python3 -m unittest discover tests",
    "docker build -t app .",
    "sed -i 's/a/b/' file",
    "chmod +x script.sh",
    "ln -s a b",
    "tar -czf out.tgz src",
    "kubectl get pods",
    "terraform plan",
    "terraform init",
    "alembic revision -m 'add table'",
    "psql -c 'SELECT 1'",
    "curl https://example.com/data.json",
    "wget https://example.com/x.tgz",
    "somethingnobodyknows --weird-flag x",
    "bash -c 'ls -la'",
    "eval 'echo hi'",
    "ls | xargs wc -l",
    "git push --dry-run",
    "rm -rf /tmp/build/../cache",
    "rm -rf sub/../other",
    "rm -rf /work/repo/../repo/build",
    "git checkout -b topic",
    "git checkout main",
    "git switch main",
    "cat > notes.md <<EOF\nDon't ever run rm -rf / on prod\nEOF",
    "ssh -p 2222 prod 'ls -la'",
    "docker exec app ls",
    "cargo run -- --help",
    "uv run pytest -q",
    "rm -rf /tmp/build-cache",
    "rm -rf /private/tmp/scratch",
    "rm -rf /var/folders/zz/T/pytest-1",
    "git restore --staged src/main.rs",
    "git restore -S src/main.rs",
    "git clean -fn",
    "git clean --force --dry-run",
    "kubectl delete pod web --dry-run=client",
    "bash -lc 'ls -la'",
    "xargs -n 1 echo",
    "find . -name '*.pyc' -exec rm {} +",
]

# Grade 2: changes shared state that someone else can see, and can be undone.
GRADE2 = [
    "git push",
    "git push origin main",
    "git push -u origin topic",
    "gh pr create --fill",
    "gh pr edit 3 --title x",
    "gh pr comment 3 --body hi",
    "gh pr close 3",
    "gh pr reopen 3",
    "gh pr ready 3",
    "gh pr review 3 --approve",
    "gh issue create --title x --body y",
    "gh issue close 9",
    "gh release create v1.0.0",
    "gh repo create o/r --private",
    "gh api -X POST repos/o/r/issues",
    "gh api --method PATCH repos/o/r",
    "gh api -f title=x repos/o/r/issues",
    "curl -X POST https://api.example.com/v1/things",
    "curl -X PUT https://api.example.com/v1/things/1",
    "curl -d name=x https://api.example.com/v1/things",
    "curl --data-binary @body.json https://api.example.com/v1",
    "curl -F file=@x.txt https://api.example.com/upload",
    "curl -T x.txt https://api.example.com/upload",
    "wget --post-data=a=b https://api.example.com",
    "npm publish",
    "pnpm publish --access public",
    "cargo publish",
    "twine upload dist/*",
    "docker push registry.example.com/app:1",
    "kubectl apply -f deploy.yaml",
    "kubectl create namespace app",
    "kubectl patch deploy app -p '{}'",
    "kubectl scale deploy app --replicas=3",
    "kubectl rollout restart deploy/app",
    "kubectl label pod x env=prod",
    "kubectl annotate pod x note=hi",
    "helm install app ./chart",
    "helm upgrade app ./chart",
    "aws ec2 create-tags --resources i-1 --tags Key=a",
    "aws s3 cp x.txt s3://bucket/x.txt",
    "gcloud compute instances create web-1",
    "az group create --name rg --location eastus",
    "gh pr merge 3 --squash",
    "vercel deploy",
    "netlify deploy",
    "curl -sSfX POST https://api.example.com/v1/things",
    "curl -sd '{}' https://api.example.com/v1/things",
    "curl --json '{}' https://api.example.com/v1/things",
    "http POST https://api.example.com/v1/things",
]

# Grade 3: irreversible. Every entry carries the verb and target the reason must name.
GRADE3 = [
    ("git push --force origin main", "git push --force", "origin main"),
    ("git push -f origin main", "git push -f", "origin main"),
    ("git push --force-with-lease origin main", "git push --force-with-lease", "origin main"),
    ("git push --delete origin topic", "git push --delete", "origin topic"),
    ("git push origin +main", "git push", "origin +main"),
    ("git reset --hard HEAD~1", "git reset --hard", "HEAD~1"),
    ("git clean -fd", "git clean -f", ""),
    ("git clean --force -x", "git clean -f", ""),
    ("git checkout -- src/main.rs", "git checkout --", "src/main.rs"),
    ("git restore src/main.rs", "git restore", "src/main.rs"),
    ("git branch -D topic", "git branch -D", "topic"),
    ("git stash drop", "git stash drop", ""),
    ("git stash clear", "git stash clear", ""),
    ("git filter-branch --tree-filter x HEAD", "git filter-branch", "x"),
    ("git filter-repo --path x", "git filter-repo", "x"),
    ("rm -rf /", "rm -rf", "/"),
    ("rm -rf ..", "rm -rf", ".."),
    ("rm -rf .git", "rm -rf", ".git"),
    ("rm -rf /etc/nginx", "rm -rf", "/etc/nginx"),
    ("rm -fr /srv/data", "rm -rf", "/srv/data"),
    ("find /srv -name '*.log' -delete", "find -delete", "/srv"),
    ("shred -u secrets.txt", "shred", "secrets.txt"),
    ("psql -c 'DROP TABLE users;'", "DROP TABLE", "users"),
    ("psql -c 'TRUNCATE TABLE events'", "TRUNCATE TABLE", "events"),
    ("mysql -e 'DELETE FROM sessions WHERE 1'", "DELETE FROM", "sessions"),
    ("sqlite3 app.db 'DROP TABLE cache'", "DROP TABLE", "cache"),
    ("psql app <<EOF\nDROP TABLE users;\nEOF", "DROP TABLE", "users"),
    ("alembic upgrade head", "alembic upgrade", "head"),
    ("alembic downgrade -1", "alembic downgrade", ""),
    ("npx prisma migrate deploy", "prisma migrate deploy", ""),
    ("npx prisma migrate reset", "prisma migrate reset", ""),
    ("npx prisma db push", "prisma db push", ""),
    ("rails db:migrate", "rails db:migrate", ""),
    ("rails db:drop", "rails db:drop", ""),
    ("python3 manage.py migrate", "manage.py migrate", ""),
    ("python3 manage.py flush", "manage.py flush", ""),
    ("flyway migrate", "flyway migrate", ""),
    ("flyway clean", "flyway clean", ""),
    ("goose up", "goose up", ""),
    ("dbmate up", "dbmate up", ""),
    ("terraform apply -auto-approve", "terraform apply", "."),
    ("terraform destroy", "terraform destroy", "."),
    ("pulumi up --yes", "pulumi up", "."),
    ("cdk deploy Stack", "cdk deploy", "Stack"),
    ("sam deploy --guided", "sam deploy", ""),
    ("serverless deploy", "serverless deploy", ""),
    ("serverless remove", "serverless remove", ""),
    ("fly deploy", "fly deploy", ""),
    ("vercel --prod", "vercel --prod", ""),
    ("netlify deploy --prod", "netlify deploy --prod", ""),
    ("railway up", "railway up", ""),
    ("kubectl delete pod web", "kubectl delete", "pod"),
    ("helm uninstall app", "helm uninstall", "app"),
    ("aws s3 rm s3://bucket/key --recursive", "aws s3 rm", "s3://bucket/key"),
    ("aws s3 rb s3://bucket", "aws s3 rb", "s3://bucket"),
    ("aws s3 sync . s3://bucket --delete", "aws s3 sync --delete", ". s3://bucket"),
    ("aws ec2 terminate-instances --instance-ids i-1", "aws ec2 terminate-instances", "i-1"),
    ("gcloud sql instances delete db-1", "gcloud sql instances delete", "db-1"),
    ("az group delete --name rg", "az group delete", "rg"),
    ("gh repo delete o/r --yes", "gh repo delete", "o/r"),
    ("gh repo archive o/r", "gh repo archive", "o/r"),
    ("gh repo rename new-name", "gh repo rename", "new-name"),
    ("gh release delete v1.0.0", "gh release delete", "v1.0.0"),
    ("docker system prune -af", "docker system prune", ""),
    ("docker rm -f app", "docker rm -f", "app"),
    ("docker rmi -f app:1", "docker rmi -f", "app:1"),
    ("sudo systemctl restart nginx", "sudo", "systemctl restart"),
    ("chmod -R 777 /", "chmod -R", "/"),
    ("chown -R nobody /", "chown -R", "/"),
    ("mkfs.ext4 /dev/sdb1", "mkfs.ext4", "/dev/sdb1"),
    ("dd if=/dev/zero of=/dev/sdb", "dd", "if=/dev/zero"),
    ("crontab -r", "crontab -r", ""),
    ("launchctl bootout gui/501", "launchctl bootout", "gui/501"),
    ("kill -9 -1", "kill -9 -1", ""),
    ("shutdown -h now", "shutdown", "now"),
    ("reboot", "reboot", ""),
    ("history -c", "history -c", ""),
    ("cat image.iso > /dev/disk2", "redirect to", "/dev/disk2"),
    ("bash -c 'rm -rf /'", "rm -rf", "/"),
    ("sh -c 'git push --force'", "git push --force", ""),
    ("eval 'terraform destroy'", "terraform destroy", "."),
    ("echo $(git push --force origin main)", "git push --force", "origin main"),
    ("ls | xargs rm -rf /", "xargs rm -rf", ""),
    # comments, here-documents and continuations may not hide the verb (review items 1 and 2)
    ("git push --force origin main # see `docs", "git push --force", "origin main"),
    ("rm -rf / # `", "rm -rf", "/"),
    ("cat <<'EOF'\n$(\nEOF\ngit push --force origin main", "git push --force", "origin main"),
    ('git push --force origin main 2>&1 | tee "log', "git push --force", ""),
    ("git push \\\n  --force origin main", "git push --force", "origin main"),
    ("rm -rf \\\n /", "rm -rf", "/"),
    # short-option clusters and alternate spellings (item 3)
    ("git push -fu origin main", "git push -f", "origin main"),
    ("git push -uf origin main", "git push -f", "origin main"),
    ("git push -fv origin main", "git push -f", "origin main"),
    ("git push origin :topic", "git push --delete", "origin :topic"),
    ("git branch -Df topic", "git branch -D", "topic"),
    ("git branch -fD topic", "git branch -D", "topic"),
    # wrappers and the sudo family (item 4)
    ("nice -n 10 git push --force origin main", "git push --force", "origin main"),
    ("timeout -k 5 10 terraform destroy", "terraform destroy", "."),
    ("stdbuf -oL rm -rf /", "rm -rf", "/"),
    ("exec rm -rf /", "rm -rf", "/"),
    ("nohup terraform apply -auto-approve", "terraform apply", "."),
    ("sudo -u postgres psql -c 'DROP TABLE users'", "sudo", "psql -c"),
    ("doas rm -rf /", "doas", "rm -rf"),
    ("su -c 'rm -rf /'", "su", "rm -rf /"),
    # shells, xargs and find -exec (item 5)
    ("bash -lc 'git push --force'", "git push --force", ""),
    ("sh -xc 'rm -rf /'", "rm -rf", "/"),
    ("bash -ec 'terraform destroy'", "terraform destroy", "."),
    ("git ls-files | xargs rm -rf", "xargs rm -rf", ""),
    ("find . -print0 | xargs -0 rm -rf", "xargs rm -rf", ""),
    ("ls | xargs -I{} rm -rf {}", "xargs rm -rf", ""),
    ("find . -name '*.tmp' -exec rm -rf {} +", "find -exec rm -rf", "."),
    # rm operands (item 6)
    ("rm -rf .", "rm -rf", "."),
    ("rm -rf ./", "rm -rf", "./"),
    ("rm -rf ./.git", "rm -rf", "./.git"),
    ("rm -rf sub/.git", "rm -rf", "sub/.git"),
    ("rm -rf $HOME", "rm -rf", "$HOME"),
    ("rm -rf ${HOME}", "rm -rf", "${HOME}"),
    ("rm -rf ~/Downloads", "rm -rf", "~/Downloads"),
    # more data loss (item 10)
    ("psql -c 'ALTER TABLE users DROP COLUMN email'", "ALTER TABLE DROP", "users"),
    ("psql -c 'DROP ROLE app'", "DROP ROLE", "app"),
    ("psql -c 'DROP SCHEMA public CASCADE'", "DROP SCHEMA", "public"),
    ("psql -c 'DROP INDEX idx_users'", "DROP INDEX", "idx_users"),
    ("mongosh --eval 'db.dropDatabase()'", "db.dropDatabase()", ""),
    ("redis-cli FLUSHALL", "redis-cli FLUSHALL", ""),
    ("redis-cli -n 0 FLUSHDB", "redis-cli FLUSHDB", ""),
    ("docker compose down -v", "docker compose down -v", ""),
    ("docker-compose down --volumes", "docker compose down -v", ""),
    ("git reflog expire --expire=now --all", "git reflog expire", ""),
    ("vercel deploy --prod", "vercel --prod", ""),
    # a remote DELETE is not undone by another request (item 9)
    ("curl -sX DELETE https://api.example.com/v1/things/1", "curl DELETE",
     "https://api.example.com/v1/things/1"),
    ("wget --method=DELETE https://api.example.com/v1/things/1", "wget DELETE",
     "https://api.example.com/v1/things/1"),
    ("http DELETE https://api.example.com/v1/things/1", "http DELETE",
     "https://api.example.com/v1/things/1"),
    ("gh api -X DELETE /repos/o/r/issues/1", "gh api DELETE", "/repos/o/r/issues/1"),
    # normalisation order and cross-line quote state (items 1 and 2)
    ("cat <<'EOF' > out.txt\nline one \\\nEOF\ngit push --force", "git push --force", ""),
    ('echo "a\n# b" && git push --force', "git push --force", ""),
    # operand paths are resolved before the temp and cwd checks (item 3)
    ("rm -rf /tmp/../etc", "rm -rf", "/tmp/../etc"),
    ("rm -rf /work/repo/../other", "rm -rf", "/work/repo/../other"),
    ("rm -rf ../..", "rm -rf", "../.."),
    ("rm -rf ./../sibling", "rm -rf", "./../sibling"),
    # a program from a substitution, and the recursion cap, scan instead of guessing (item 4)
    ("$(which git) push --force origin main", "git push --force", ""),
    ("echo $(echo $(echo $(echo $(git push --force))))", "git push --force", ""),
    # git global options that take a value (item 5)
    ("git --git-dir /x/.git push --force origin main", "git push --force", "origin main"),
    ("git --work-tree /x push --force origin main", "git push --force", "origin main"),
    ("git -c user.name=x push --force origin main", "git push --force", "origin main"),
    # discarding the working tree (item 6)
    ("git checkout .", "git checkout", "."),
    ("git checkout -f main", "git checkout -f", "main"),
    ("git switch --discard-changes main", "git switch --discard-changes", "main"),
    ("git switch -f main", "git switch --discard-changes", "main"),
    # runner wrappers (item 7)
    ("bundle exec rails db:drop", "rails db:drop", ""),
    ("poetry run alembic upgrade head", "alembic upgrade", "head"),
    ("uv run alembic downgrade -1", "alembic downgrade", ""),
    ("pnpm dlx vercel --prod", "vercel --prod", ""),
    ("pnpm exec prisma migrate reset", "prisma migrate reset", ""),
    ("yarn dlx netlify deploy --prod", "netlify deploy --prod", ""),
    ("npm exec -- prisma migrate deploy", "prisma migrate deploy", ""),
    ("pipx run flyway clean", "flyway clean", ""),
    ("cargo run -- terraform destroy", "terraform destroy", "."),
    # remote execution is graded like `bash -c` (item 8)
    ("ssh prod 'rm -rf /var/lib/app'", "rm -rf", "/var/lib/app"),
    ("ssh -p 2222 prod 'terraform destroy'", "terraform destroy", "."),
    ("kubectl exec deploy/web -- rm -rf /data", "rm -rf", "/data"),
    ("docker exec app rm -rf /srv", "rm -rf", "/srv"),
    ("docker exec -u root app rm -rf /srv", "rm -rf", "/srv"),
    ("docker compose exec db psql -c 'DROP TABLE users'", "DROP TABLE", "users"),
    ("fly ssh console -C 'rm -rf /data'", "rm -rf", "/data"),
]


class GradeTests(unittest.TestCase):
    def test_read_only_commands_grade_zero(self):
        self.assertEqual([c for c in GRADE0 if grade(c) != 0], [])

    def test_local_writes_grade_one(self):
        self.assertEqual([(c, grade(c)) for c in GRADE1 if grade(c) != 1], [])

    def test_remote_mutations_grade_two(self):
        self.assertEqual([(c, grade(c)) for c in GRADE2 if grade(c) != 2], [])

    def test_irreversible_commands_grade_three(self):
        self.assertEqual([(c, grade(c)) for c, _, _ in GRADE3 if grade(c) != 3], [])

    def test_every_grade_three_names_its_verb_and_target(self):
        for command, verb, target in GRADE3:
            with self.subTest(command=command):
                _, got_verb, got_target, _ = grader.grade_text(command, CWD)
                self.assertEqual((got_verb, got_target), (verb, target))

    def test_every_read_only_corpus_case_grades_zero(self):
        # The safety property that keeps the two Bash hooks from contradicting each other.
        self.assertEqual([c for c in ALLOW + APPROVED if grade(c) != 0], [])

    def test_grading_a_hundred_kilobyte_command_stays_well_inside_the_hook_timeout(self):
        # A PreToolUse hook past its registered timeout fails open, and every PreToolUse policy
        # shares that one timeout, so the grader gets a tenth of it. The quarter-size run bounds
        # the growth: linear grading quadruples, a quadratic scan grows sixteenfold and fails here
        # long before it would outgrow the budget. `cpu_growth` measures the grader rather than
        # the machine's load.
        budget = pre_tool_use_timeout() / 10
        for suffix in ("", ' "unbalanced'):
            long, short = ("echo " + "push " * n + suffix for n in (20000, 5000))
            full, growth = cpu_growth(lambda: grader.grade_text(long, CWD),
                                      lambda: grader.grade_text(short, CWD))
            with self.subTest(unbalanced=bool(suffix)):
                self.assertLess(full, budget)
                self.assertLess(growth, 8)

    def _record(self, name, commands):
        """The length of every text `grader.library.<name>` received while grading and governing
        `commands`; the real function still runs."""
        lengths = []
        original = getattr(grader.library, name)

        def spy(text, *args, **kwargs):
            lengths.append(len(text))
            return original(text, *args, **kwargs)

        with mock.patch.object(grader.library, name, side_effect=spy):
            for command in commands:
                grader.grade_text(command, CWD)
                grader.governed_text(command, CWD)
        return lengths

    # Over the cap by its outer text alone: substitutions, a continuation and a push in a repo.
    OVER_CAP = ("cd " + CWD + " && git push " + "$(a \\\n b)" * 2000 + " \\\n x",
                "echo " + "$(a)" * 8000)

    def test_the_continuation_lexer_never_reads_text_past_the_scan_cap(self):
        # The lexer still runs on the short inner texts of a line under the cap, so the spy
        # is live; it never receives a text longer than the cap.
        under = "echo " + "$(a \\\n b)" * 100 + " \\\n x"
        self.assertTrue(self._record("_join_continuations", [under]))
        lengths = self._record("_join_continuations", self.OVER_CAP)
        self.assertLessEqual(max(lengths, default=0), grader.SCAN_CAP)

    def test_substitutions_are_never_extracted_from_text_past_the_scan_cap(self):
        lengths = self._record("_extract_subs", self.OVER_CAP)
        self.assertLessEqual(max(lengths, default=0), grader.SCAN_CAP)

    def test_a_line_past_the_scan_cap_grades_too_long_at_an_unknown_directory(self):
        for command in self.OVER_CAP:
            with self.subTest(command=command[:30]):
                self.assertEqual(grader.grade_text(command, CWD),
                                 (3, "command too long to grade", "", "opaque"))
                found = grader.governed_text(command, CWD)
                self.assertEqual([(grade, where) for _, grade, where, _ in found], [(3, None)])
        self.assertEqual(grader.governed_text(self.OVER_CAP[0], CWD)[0][0], grader.PUSH)

    def test_a_long_here_document_body_is_not_counted_toward_the_scan_cap(self):
        short = "cat > notes.md <<'EOF'\nline\nEOF\ngit push"
        long = "cat > notes.md <<'EOF'\n" + "line $(a) \\\n" * 2000 + "EOF\ngit push"
        self.assertGreater(len(long), grader.SCAN_CAP)
        self.assertEqual(grader.grade_text(long, CWD), grader.grade_text(short, CWD))
        self.assertEqual(grader.governed_text(long, CWD), grader.governed_text(short, CWD))
        self.assertEqual({where for _, _, where, _ in grader.governed_text(long, CWD)}, {CWD})

    def test_an_unparseable_command_past_the_scan_cap_grades_three(self):
        command = "echo " + "x" * 20000 + ' "unbalanced'
        grade, verb, _, _ = grader.grade_text(command, CWD)
        self.assertEqual((grade, verb), (3, "command too long to grade"))

    def test_a_here_document_body_is_data_unless_a_client_interprets_it(self):
        self.assertEqual(grade("cat > notes.md <<EOF\nrun rm -rf / on prod\nEOF"), 1)
        self.assertEqual(grade("psql app <<EOF\nDROP TABLE users;\nEOF"), 3)

    def test_one_segment_lifts_the_grade_of_the_whole(self):
        for command, expected in (
            ("git fetch && git push --force", 3),
            ("git status; git commit -m x", 1),
            ("ls && git push", 2),
            ("cargo test && git push --force-with-lease origin main", 3),
            ("git add -A; git commit -m x; git push", 2),
            ("(cd /repo && rm -rf /srv/data)", 3),
            ("for f in *.sql; do psql -c 'DROP TABLE t'; done", 3),
        ):
            with self.subTest(command=command):
                self.assertEqual(grade(command), expected)

    def test_the_confirm_marker_is_stripped_and_passes_silently(self):
        for command in (
            "HARNESS_CONFIRMED=1 git push --force origin main",
            "HARNESS_CONFIRMED=1; git push --force origin main",
            "env HARNESS_CONFIRMED=1 terraform destroy",
            "HARNESS_CONFIRMED=1 ls",
            # The marker is leading and confirms the whole line, compounds included: it is the
            # text the user was shown and said yes to.
            "HARNESS_CONFIRMED=1 ls; git push --force origin main",
        ):
            with self.subTest(command=command):
                self.assertEqual(run(command), (None, None))

    def test_an_unknown_command_grades_one_never_three(self):
        for command in ("frobnicate --all", "./scripts/deploy-ish.sh", "weird | thing"):
            with self.subTest(command=command):
                self.assertEqual(grade(command), 1)

    def test_a_redirect_only_segment_after_a_subshell_still_writes_its_file(self):
        for command in ("(ls) > out.txt", "(ls)>out.txt", "{ ls; } > out.txt", "x=1 > out.txt"):
            with self.subTest(command=command):
                self.assertEqual(grader.grade_text(command, CWD)[:3], (1, "redirect to", "out.txt"))
        # a temp-directory target is graded as it is after a plain command
        self.assertEqual(grade("(ls) > /tmp/out.txt"), grade("ls > /tmp/out.txt"))
        self.assertEqual(grade("(ls) > /dev/null"), 0)

    def test_a_numeric_file_target_is_a_write_but_a_descriptor_duplication_is_not(self):
        for command in ("ls >2", "ls > 2", "ls &>2", "ls 2>1"):
            with self.subTest(command=command):
                self.assertGreaterEqual(grade(command), 1)
        for command in ("ls 2>&1", "ls >&2", "ls 2>&-"):
            with self.subTest(command=command):
                self.assertEqual(grade(command), 0)
        self.assertEqual(grader._written("ls", [], ["2"], ""), ["2"])
        self.assertEqual(grader._redirects(grader.ro.tokenize("ls 2>&1 >2"))[1], ["2"])


class HookTests(unittest.TestCase):
    def test_stance_and_mode_matrix(self):
        cases = [
            ("execute", "default", "git push --force origin main", "ask"),
            ("execute", "bypassPermissions", "git push --force origin main", "deny"),
            ("execute", "default", "gh pr create --fill", None),
            ("confirm-writes", "default", "gh pr create --fill", "ask"),
            ("ask", "auto", "git commit -m x", "deny"),
            ("execute", "auto", "ls -la", None),
            ("execute", "plan", "terraform destroy", "ask"),
            ("execute", "acceptEdits", "rm -rf /", "ask"),
            ("execute", "dontAsk", "rm -rf /", "ask"),
            ("ask", "default", "cargo build", "ask"),
            ("confirm-writes", "default", "cargo build", None),
        ]
        for variant, mode, command, expected in cases:
            with self.subTest(stance=variant, mode=mode, command=command):
                decision, _ = run(command, variant, mode)
                self.assertEqual(decision, expected)

    def test_the_reason_names_the_grade_verb_target_consequence_and_stance(self):
        decision, why = run("git push --force origin main", "execute", "default")
        self.assertEqual(decision, "ask")
        self.assertEqual(
            why,
            "grade 3, irreversible: git push --force origin main rewrites remote history "
            "— this cannot be undone (grade-bash hook, autonomy=execute)")

    def test_every_grade_says_in_plain_words_what_it_is(self):
        """The label is for a log; whoever reads the prompt gets the same fact in words."""
        for command, variant, plain in (
                ("git push --force origin main", "execute", "this cannot be undone"),
                ("gh pr create --fill", "confirm-writes", "this changes something other people can see"),
                ("cargo build", "ask", "this changes files on this machine")):
            with self.subTest(command=command):
                _, why = run(command, variant, "default")
                self.assertIn("— " + plain + " (", why)

    def test_a_deny_mode_reason_names_the_confirm_marker(self):
        decision, why = run("git push --force origin main", "execute", "bypassPermissions")
        self.assertEqual(decision, "deny")
        self.assertTrue(why.endswith(
            " Nothing can prompt in this permission mode, so the command was refused rather than"
            " asked about. Say in chat what it would change and why that is hard to undo; if the"
            " user says yes, run the same command again with HARNESS_CONFIRMED=1 in front of it."), why)

    def test_grade_two_and_one_reasons_carry_their_labels(self):
        _, two = run("gh pr create --fill", "confirm-writes", "default")
        self.assertTrue(two.startswith("grade 2, remote-mutating: gh pr create"), two)
        self.assertIn("changes shared state", two)
        _, one = run("cargo build", "ask", "default")
        self.assertTrue(one.startswith("grade 1, local write: "), one)
        self.assertIn("autonomy=ask", one)

    def test_a_missing_permission_mode_prompts_rather_than_denies(self):
        with tempfile.TemporaryDirectory() as home:
            out = subprocess.run(
                [sys.executable, str(HOOK)],
                input=json.dumps({"tool_name": "Bash",
                                  "tool_input": {"command": "git push --force"}}),
                capture_output=True, text=True,
                env=dict(without_config_dir(), HOME=home, HARNESS_STANCE_AUTONOMY="execute"),
            )
        block = json.loads(out.stdout)["hookSpecificOutput"]
        self.assertEqual(block["permissionDecision"], "ask")

    def test_non_bash_and_malformed_payloads_are_ignored(self):
        for payload in (
            '{"tool_name":"Read","tool_input":{"command":"rm -rf /"}}',
            '{"tool_name":"Agent","tool_input":{"prompt":"x"}}',
            '{"tool_name":"Bash","tool_input":{"command":""}}',
            '{"tool_name":"Bash","tool_input":"rm -rf /"}',
            "not json",
            "{}",
            "",
        ):
            with self.subTest(payload=payload):
                with tempfile.TemporaryDirectory() as home:
                    out = subprocess.run(
                        [sys.executable, str(HOOK)], input=payload, capture_output=True,
                        text=True, env=dict(without_config_dir(), HOME=home),
                    )
                self.assertEqual(out.stdout.strip(), "")
                self.assertEqual(out.returncode, 0)

    def test_an_unknown_stance_uses_the_strictest_threshold(self):
        for command in ("touch notes.md", "gh pr create --fill", "rm -rf /"):
            with self.subTest(command=command):
                self.assertEqual(run(command, "made-up", "default")[0], "ask")


    def test_a_marker_that_is_not_leading_confirms_nothing(self):
        decision, _ = run("ls; HARNESS_CONFIRMED=1 git push --force origin main")
        self.assertEqual(decision, "ask")

    def test_gh_pr_merge_carries_the_merge_clause_not_the_history_one(self):
        _, why = run("gh pr merge 3 --squash", "confirm-writes", "default")
        self.assertIn("merges into the shared branch", why)
        self.assertNotIn("rewrites remote history", why)

    def test_gh_repo_rename_carries_its_own_clause_not_the_archive_one(self):
        _, why = run("gh repo rename new-name")
        self.assertEqual(
            why,
            "grade 3, irreversible: gh repo rename new-name moves the repository to a new name, "
            "and the old URLs redirect only while no repository takes the old name "
            "— this cannot be undone (grade-bash hook, autonomy=execute)")
        _, why = run("gh repo archive o/r")
        self.assertEqual(
            why,
            "grade 3, irreversible: gh repo archive o/r locks the repository read-only for everyone "
            "— this cannot be undone (grade-bash hook, autonomy=execute)")

    def test_every_family_in_use_has_a_clause(self):
        families = {grader.grade_text(c, CWD)[3] for c, _, _ in GRADE3}
        families |= {grader.grade_text(c, CWD)[3] for c in GRADE2}
        self.assertEqual(sorted(f for f in families if f and f not in grader.CLAUSES), [])

    def test_a_malformed_config_still_grades_under_the_default_stance(self):
        for body in ('"str"', '{"stances": "execute"}', '{"stances": {"autonomy": {}}}',
                     '{"stances": {}}', 'not json at all'):
            with self.subTest(config=body):
                self.assertEqual(self.with_config(body, "git push --force origin main"), "ask")

    def test_the_config_file_sets_the_stance_with_no_environment_override(self):
        self.assertEqual(
            self.with_config('{"stances": {"autonomy": "confirm-writes"}}', "gh pr create --fill"),
            "ask")
        self.assertIsNone(
            self.with_config('{"stances": {"autonomy": "execute"}}', "gh pr create --fill"))

    def with_config(self, body, command):
        """Run the hook with a temporary HOME holding `body` as the harness config and no
        HARNESS_STANCE_AUTONOMY override."""
        with tempfile.TemporaryDirectory() as home:
            config = Path(home) / ".config" / "agent-harness"
            config.mkdir(parents=True)
            (config / "config.json").write_text(body)
            env = dict(without_config_dir(), HOME=home)
            env.pop("HARNESS_STANCE_AUTONOMY", None)
            out = subprocess.run(
                [sys.executable, str(HOOK)],
                input=json.dumps({"tool_name": "Bash", "cwd": CWD, "permission_mode": "default",
                                  "tool_input": {"command": command}}),
                capture_output=True, text=True, env=env,
            )
        if not out.stdout.strip():
            return None
        return json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"]

    def test_the_hook_exits_silently_without_its_sibling_grammar(self):
        with tempfile.TemporaryDirectory() as lonely:
            copy = Path(lonely) / "grade-bash.py"
            copy.write_text(HOOK.read_text())
            out = subprocess.run(
                [sys.executable, str(copy)],
                input=json.dumps({"tool_name": "Bash", "permission_mode": "default",
                                  "tool_input": {"command": "rm -rf /"}}),
                capture_output=True, text=True,
                env=dict(without_config_dir(), HOME=lonely, HARNESS_STANCE_AUTONOMY="execute"),
            )
        self.assertEqual(out.stdout.strip(), "")
        self.assertEqual(out.returncode, 0)

    def test_a_hidden_verb_still_reaches_the_prompt(self):
        for command in (
            "git push --force origin main # see `docs",
            "git push \\\n  --force origin main",
            "cat <<'EOF'\n$(\nEOF\ngit push --force origin main",
        ):
            with self.subTest(command=command):
                self.assertEqual(run(command)[0], "ask")

    def test_ownership_claims_the_hook_id(self):
        self.assertEqual(
            OWNERSHIP["claude"]["hook_ids"]["grade-bash"],
            {"event": "PreToolUse", "always": True},
        )


if __name__ == "__main__":
    unittest.main()

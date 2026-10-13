# SPDX-License-Identifier: MIT
"""The Changes inbox routes: the draft list, draft create and the apply journal's history."""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import draft_support  # noqa: E402
import test_studio_security as studio_security  # noqa: E402
from harness_core.studio import apply as draft_apply  # noqa: E402
from harness_core.studio import changes, drafts, headless, server  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
_loader = importlib.machinery.SourceFileLoader("harness_changes_test", str(ROOT / "bin" / "harness"))
_spec = importlib.util.spec_from_loader("harness_changes_test", _loader)
harness = importlib.util.module_from_spec(_spec)
_loader.exec_module(harness)

DRAFTS = "/api/changes/drafts"
CREATE = "/api/changes/drafts/create"
HISTORY = "/api/changes/history"
APPLY_A, APPLY_B, ROLLBACK_A = "a" * 32, "b" * 32, "c" * 32


def journal_rows():
    """An apply of `one`, an apply of `two`, then a completed rollback of the first, in order."""
    return [
        {"apply_id": APPLY_A, "phase": "intent", "draft": "one", "draft_id": "id-one",
         "revision": "r1", "ts": "2026-10-01T10:00:00+00:00", "actor": "studio"},
        {"apply_id": APPLY_A, "phase": "completed"},
        {"apply_id": APPLY_B, "phase": "intent", "draft": "two", "draft_id": "id-two",
         "revision": "r2", "ts": "2026-10-02T10:00:00+00:00", "actor": "citizen"},
        {"apply_id": APPLY_B, "phase": "completed"},
        {"apply_id": ROLLBACK_A, "phase": "intent", "kind": "rollback", "reverses": APPLY_A,
         "draft": "one", "draft_id": "id-one", "revision": "r1",
         "ts": "2026-10-03T10:00:00+00:00", "actor": "studio"},
        {"apply_id": ROLLBACK_A, "phase": "completed"},
    ]


def write_journal(home, rows):
    path = draft_apply.journal_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class ChangesRouteTests(studio_security.StudioSecurityFixture):
    def setUp(self):
        super().setUp()
        _issued, status, headers, _body = self.bootstrap(origin="null")
        self.assertEqual(status, 200)
        self.cookie_value = self.cookie(headers)
        _status, _headers, body = self.request("GET", "/api/session", {"Cookie": self.cookie_value})
        self.csrf = json.loads(body)["csrf_token"]

    def get(self, path, cookie=True):
        status, headers, body = self.request("GET", path, {"Cookie": self.cookie_value} if cookie else {})
        return status, headers, json.loads(body)

    def post(self, path, payload, csrf=True):
        body = json.dumps(payload).encode("utf-8")
        headers = {"Cookie": self.cookie_value, "Content-Type": "application/json",
                   "Content-Length": str(len(body)), "Origin": self.record["url"].rstrip("/")}
        if csrf:
            headers["X-Studio-CSRF"] = self.csrf
        status, _headers, answer = self.request("POST", path, headers, body)
        return status, json.loads(answer)

    def test_history_is_empty_in_a_fresh_home(self):
        status, headers, document = self.get(HISTORY)
        self.assertEqual(status, 200, document)
        self.assertEqual(headers.get_content_type(), "application/json")
        self.assertEqual(document, {"schema_version": 1, "entries": []})

    def test_history_lists_applies_and_rollbacks_newest_first(self):
        write_journal(self.home, journal_rows())
        status, _headers, document = self.get(HISTORY)
        self.assertEqual(status, 200, document)
        self.assertEqual([(e["apply_id"], e["kind"], e["status"], e["draft"]) for e in document["entries"]],
                         [(ROLLBACK_A, "rollback", "completed", "one"),
                          (APPLY_B, "apply", "completed", "two"),
                          (APPLY_A, "apply", "completed", "one")])
        self.assertEqual(document["entries"][0]["reverses"], APPLY_A)
        self.assertEqual(set(document["entries"][0]),
                         {"apply_id", "kind", "status", "draft", "draft_id", "revision", "ts",
                          "actor", "reverses"})

    def test_anonymous_is_refused_on_every_changes_route(self):
        for path in (DRAFTS, HISTORY):
            status, _headers, document = self.get(path, cookie=False)
            self.assertEqual((status, document), (401, {"error": "unauthorized"}), path)
        status, _headers, body = self.request("POST", CREATE, {"Content-Type": "application/json"},
                                              b"{}")
        self.assertEqual((status, json.loads(body)), (401, {"error": "unauthorized"}))

    def test_create_without_the_csrf_token_is_refused(self):
        status, document = self.post(CREATE, {"draft": "never-made", "source": "stances"}, csrf=False)
        self.assertEqual((status, document), (403, {"error": "request_refused"}))

    def test_create_refuses_a_bad_name_source_or_body(self):
        for payload in ({"draft": "Not A Name", "source": ""}, {"draft": "ok", "source": "Stances"},
                        {"draft": "ok"}, {"draft": "ok", "source": "", "extra": 1},
                        {"draft": 3, "source": ""}):
            status, document = self.post(CREATE, payload)
            self.assertEqual((status, document), (400, {"error": "invalid_request"}), payload)

    def test_an_unknown_draft_path_is_404(self):
        status, _headers, document = self.get(DRAFTS + "/nonesuch")
        self.assertEqual((status, document), (404, {"error": "not_found"}))
        status, _headers, document = self.get(DRAFTS + "?draft=nonesuch")
        self.assertEqual(status, 404)

    def test_a_created_draft_is_listed_open_with_its_source(self):
        name = draft_support.draft_name("changes-")
        self.addCleanup(draft_support.discard_draft, self, name, self.env, missing_ok=True)
        status, created = self.post(CREATE, {"draft": name, "source": "stances"})
        self.assertEqual(status, 200, created)
        server.DRAFT_DESCRIBED.validate(created)
        self.assertEqual((created["name"], created["source"]), (name, "stances"))

        status, again = self.post(CREATE, {"draft": name, "source": ""})
        self.assertEqual((status, again), (409, {"error": "draft-exists"}))

        status, _headers, document = self.get(DRAFTS)
        self.assertEqual(status, 200, document)
        server.CHANGES_DRAFTS.validate(document)
        listed = {item["name"]: item for item in document["drafts"]}
        self.assertIn(name, listed)
        item = listed[name]
        self.assertEqual((item["status"], item["source"], item["tests"], item["applied"],
                          item["rolled_back"]), ("open", "stances", 0, None, None))
        self.assertEqual(item["draft_id"], created["draft_id"])

    def test_routes_name_their_citizen_commands(self):
        routes = {(r.method, r.path): r for r in server.ROUTES.entries}
        self.assertEqual(routes[("GET", DRAFTS)].cli_command, ("citizen", "draft", "list", "--json"))
        self.assertEqual(routes[("GET", HISTORY)].cli_command, ("citizen", "draft", "history", "--json"))
        self.assertEqual(routes[("POST", CREATE)].request_media_type, "application/json")
        for key in (("GET", DRAFTS), ("GET", HISTORY), ("POST", CREATE)):
            self.assertIsNone(routes[key].parity_exemption)


class InboxRouteTests(unittest.TestCase):
    """The draft-list route over a private repository holding exactly the drafts this test made."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(os.path.realpath(temporary.name))
        self.repo = self.root / "repos" / "project"
        self.repo.mkdir(parents=True)
        for args in (("init", "-q", "-b", "main"), ("config", "user.name", "Test"),
                     ("config", "user.email", "test" + "@" + "example.invalid")):
            self.git(*args)
        (self.repo / "README.md").write_text("base\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "initial")
        self.home = self.root / "home"
        self.config = self.home / ".config" / "agent-harness" / "config.json"
        self.config.parent.mkdir(parents=True)
        self.config.write_text('{"identity":{"name":"A Name"}}\n', encoding="utf-8")
        self.state = self.home / ".local" / "state" / "agent-harness" / "studio"
        self.state.mkdir(parents=True)
        environment = {key: value for key, value in os.environ.items() if key != "HARNESS_QUIET"}
        environment.update(HOME=str(self.home), HARNESS_HOME=str(self.home),
                           HARNESS_WORKTREE_ROOT=str(self.root / "worktrees"))
        patcher = mock.patch.dict(os.environ, environment, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                              capture_output=True, text=True)

    def create(self, name, source=""):
        def worktree(repo, worktree_name, branch, base):
            dest, run = harness.create_managed_worktree(repo, worktree_name, branch, base, fetch=False)
            if run.returncode:
                raise SystemExit(run.stderr)
            return dest

        def remove(repo, dest, branch, revision):
            raise AssertionError("no create in this test is expected to clean up")

        return drafts.create(self.repo, name, "installed", self.config, worktree, remove, source=source)

    def record(self, created):
        """A draft-test record for the draft's current revision, in `draft_tests.record`'s shape."""
        directory = self.state / "draft-tests"
        directory.mkdir(mode=0o700, exist_ok=True)
        run_id = "00000000-0000-4000-8000-000000000001"
        (directory / (run_id + ".json")).write_text(json.dumps({
            "schema_version": 1, "run_id": run_id, "draft": created["name"],
            "draft_id": created["draft_id"], "revision": created["revision"], "config_digest": "0" * 64,
            "base_revision": created["base_revision"], "power": {}, "created_at": "2026-10-12T00:00:00+00:00",
            "registration": None, "deviations": []}), encoding="utf-8")

    def test_the_route_lists_every_draft_with_its_status(self):
        applied = self.create("applied-one")
        tested = self.create("tested-one", source="rules")
        self.create("open-one", source="stances")
        self.record(tested)
        write_journal(self.home, [
            {"apply_id": APPLY_A, "phase": "intent", "draft": "applied-one", "draft_id": applied["draft_id"],
             "revision": applied["revision"], "ts": "2026-10-01T10:00:00+00:00", "actor": "studio"},
            {"apply_id": APPLY_A, "phase": "completed"}])
        status, document = headless.call_route(self.repo, self.state, "GET", DRAFTS)
        self.assertEqual(status, 200, document)
        server.CHANGES_DRAFTS.validate(document)
        self.assertEqual(sorted((item["name"], item["status"], item["source"]) for item in document["drafts"]),
                         [("applied-one", "applied", ""), ("open-one", "open", "stances"),
                          ("tested-one", "tested", "rules")])
        listed = {item["name"]: item for item in document["drafts"]}
        self.assertEqual(listed["applied-one"]["applied"]["apply_id"], APPLY_A)
        self.assertEqual(listed["tested-one"]["tests"], 1)

    def test_an_unsafe_records_directory_reads_as_untested(self):
        created = self.create("only-one")
        self.record(created)
        with mock.patch.object(changes.draft_tests, "read_records",
                               side_effect=changes.draft_tests.DraftTestError("unsafe", "draft_test_records_unsafe")):
            document = changes.inbox(self.repo, self.home, self.state)
        self.assertEqual([(item["name"], item["status"]) for item in document["drafts"]], [("only-one", "open")])

    def test_an_empty_repository_lists_nothing(self):
        status, document = headless.call_route(self.repo, self.state, "GET", DRAFTS)
        self.assertEqual((status, document), (200, {"schema_version": 1, "drafts": []}))


class DraftStatusTests(unittest.TestCase):
    """`changes._status`: open, tested, applied and rolled back, from the journal and test records."""

    def entries(self, rows):
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            write_journal(Path(home), rows)
            return changes._summaries(Path(home))

    def status(self, draft, rows=(), records=()):
        return changes._status(draft, self.entries(list(rows)), list(records))

    def test_open_without_tests_or_applies(self):
        self.assertEqual(self.status({"draft_id": "id-new", "revision": "r9"}, journal_rows())["status"], "open")

    def test_tested_only_for_its_current_revision(self):
        draft = {"draft_id": "id-new", "revision": "r9"}
        current = [{"draft_id": "id-new", "revision": "r9"}]
        stale = [{"draft_id": "id-new", "revision": "r8"}, {"draft_id": "other", "revision": "r9"}]
        self.assertEqual(self.status(draft, records=current)["status"], "tested")
        answer = self.status(draft, records=stale)
        self.assertEqual((answer["status"], answer["tests"]), ("open", 1))

    def test_applied_and_rolled_back(self):
        applied = self.status({"draft_id": "id-two", "revision": "r2"}, journal_rows())
        self.assertEqual((applied["status"], applied["applied"]["apply_id"]), ("applied", APPLY_B))
        rolled = self.status({"draft_id": "id-one", "revision": "r1"}, journal_rows())
        self.assertEqual((rolled["status"], rolled["rolled_back"]["apply_id"]), ("rolled back", ROLLBACK_A))

    def test_a_rolled_back_rollback_leaves_the_apply_in_effect(self):
        rows = journal_rows() + [
            {"apply_id": "d" * 32, "phase": "intent", "kind": "rollback", "reverses": ROLLBACK_A,
             "draft": "one", "draft_id": "id-one", "revision": "r1", "ts": "2026-10-04T10:00:00+00:00"},
            {"apply_id": "d" * 32, "phase": "completed"}]
        self.assertEqual(self.status({"draft_id": "id-one", "revision": "r1"}, rows)["status"], "applied")

    def test_a_draft_moved_past_its_apply_is_open_again(self):
        answer = self.status({"draft_id": "id-two", "revision": "r3"}, journal_rows())
        self.assertEqual((answer["status"], answer["applied"]["revision"]), ("open", "r2"))

    def test_an_interrupted_or_failed_apply_is_not_applied(self):
        rows = [{"apply_id": APPLY_A, "phase": "intent", "draft": "one", "draft_id": "id-one",
                 "revision": "r1", "ts": "t"}]
        self.assertEqual(self.status({"draft_id": "id-one", "revision": "r1"}, rows)["status"], "open")


if __name__ == "__main__":
    unittest.main()

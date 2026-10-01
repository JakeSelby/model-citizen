"""The replay arms as containers: each declared from pinned inputs, built, listed in a manifest
that two builds must agree on, and run with the snapshot as its only mount and the model API as
its only way out. Every Docker command goes to a fake launcher, so no test needs a daemon."""
import contextlib
import importlib.util
import io
import json
import os
import socket
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

from isolation import isolate_home
from test_harness import REPO, harness


def load(name):
    spec = importlib.util.spec_from_file_location(name + "_under_test", REPO / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ARMS = load("replay_arms")
LISTER = load("arm_manifest")
PROXY = load("egress_proxy")
PROXY_SOURCE = (REPO / "scripts" / "egress_proxy.py").read_text(encoding="utf-8")
INPUTS = {"base_image": "base@sha256:" + "0" * 64, "claude_code_version": "1.2.3"}
COMMIT = "c" * 40


def done(stdout="", returncode=0, stderr=""):
    return types.SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


class Docker:
    """A fake Docker client: answers each kind of command, records every call. `manifests` is the
    manifest each successive `run ... python3 -` prints, so two builds can be made to disagree."""
    def __init__(self, manifests=None, probe=None):
        self.calls, self.manifests, self.probe = [], list(manifests or []), probe or {}

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[:2] == ["docker", "logs"]:
            return done("", 0, "egress proxy on 192.0.2.10:3128 for api.anthropic.com\n")
        if command[:3] == ["docker", "image", "inspect"]:
            return done("sha256:" + "9" * 64 + "\n")
        if command[:2] == ["docker", "run"] and command[-2:] == ["python3", "-"]:
            return done(json.dumps(self.manifests.pop(0) if self.manifests else {"entries": []}))
        if command[:2] == ["docker", "run"] and "curl" in command:
            url = command[-1]
            bypass = "--noproxy" in command
            return self.probe.get((url, bypass), done("", 56, "curl: (56) CONNECT tunnel failed, response 403"))
        return done()

    def commands(self, *prefix):
        return [c for c, _ in self.calls if c[:len(prefix)] == list(prefix)]


def fake_snapshot(calls):
    def snapshot(repo, sha, dest):
        calls.append((repo, sha, Path(dest)))
        Path(dest).mkdir(parents=True)
        return Path(dest)
    return snapshot


def manifest(*entries, **top):
    base = {"schema": 1, "claude_code_version": "1.2.3", "harness_commit": None,
            "entries": [dict({"kind": "file", "mode": "0644", "size": 1, "sha256": "x"}, path=p) for p in entries]}
    base.update(top)
    return base


class DeclarationTests(unittest.TestCase):
    def test_the_inputs_are_the_linux_qualification_images_own_pins(self):
        inputs = ARMS.qualification_inputs()
        text = (REPO / "scripts" / "linux-target.Dockerfile").read_text(encoding="utf-8")
        self.assertIn("@sha256:", inputs["base_image"])
        self.assertIn("FROM " + inputs["base_image"], text)
        self.assertIn("ARG CLAUDE_CODE_VERSION=" + inputs["claude_code_version"], text)

    def test_an_unpinned_qualification_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Dockerfile"
            path.write_text("FROM ubuntu:24.04\nARG CLAUDE_CODE_VERSION=1\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                ARMS.qualification_inputs(path)

    def test_the_arm_dockerfile_takes_its_base_from_the_runner_and_names_no_other(self):
        text = ARMS.ARM_DOCKERFILE.read_text(encoding="utf-8")
        froms = [line.split()[1] for line in text.splitlines() if line.startswith("FROM ")]
        # The harness stage and the declared-selection harness stage both build on the bare one.
        self.assertEqual(froms, ["${BASE_IMAGE}", "bare", "bare"])

    def test_each_arm_declares_exactly_its_components(self):
        bare = ARMS.declaration("bare", INPUTS)
        harness = ARMS.declaration("harness", INPUTS, {"ref": "v1", "commit": COMMIT})
        self.assertEqual([c["name"] for c in bare["components"]],
                         ["base-image", "@anthropic-ai/claude-code", "model-citizen-observer"])
        self.assertEqual(bare["components"][-1]["version"], "sha256:" + ARMS.file_sha(ARMS.OBSERVER_SOURCE))
        self.assertIsNone(bare["harness"])
        self.assertEqual(harness["components"][-1], {"name": "model-citizen", "version": "v1", "commit": COMMIT})
        self.assertEqual(harness["harness"], {"ref": "v1", "commit": COMMIT})
        self.assertEqual((ARMS.label(bare), ARMS.label(harness)), ("bare", "harness@v1"))
        self.assertEqual(harness["dockerfile_sha256"], ARMS.file_sha(ARMS.ARM_DOCKERFILE))

    def test_a_harness_arm_without_a_full_commit_or_a_bare_arm_with_one_is_refused(self):
        for arm, harness in (("harness", None), ("harness", {"ref": "v1", "commit": "abc123"}),
                             ("bare", {"ref": "v1", "commit": COMMIT}), ("superpowers", None)):
            with self.assertRaises(SystemExit, msg=(arm, harness)):
                ARMS.declaration(arm, INPUTS, harness)

    def test_one_set_of_inputs_names_one_image_and_a_moved_tag_another(self):
        first = ARMS.declaration("harness", INPUTS, {"ref": "v1", "commit": COMMIT})
        again = ARMS.declaration("harness", INPUTS, {"ref": "v1", "commit": COMMIT})
        moved = ARMS.declaration("harness", INPUTS, {"ref": "v1", "commit": "d" * 40})
        self.assertEqual(ARMS.image_name(first), ARMS.image_name(again))
        self.assertNotEqual(ARMS.image_name(first), ARMS.image_name(moved))
        self.assertTrue(ARMS.image_name(first).startswith("model-citizen-arm-harness:"))


class BuildTests(unittest.TestCase):
    def test_the_build_command_pins_every_input_and_targets_the_arm(self):
        harness = ARMS.declaration("harness", INPUTS, {"ref": "v1", "commit": COMMIT})
        command = ARMS.build_command(harness, "/ctx", "model-citizen-arm-harness:t", no_cache=True)
        self.assertEqual(command[:2], ["docker", "build"])
        self.assertEqual(command[command.index("--target") + 1], "harness")
        args = [command[i + 1] for i, part in enumerate(command) if part == "--build-arg"]
        self.assertEqual(args, ["BASE_IMAGE=" + INPUTS["base_image"], "CLAUDE_CODE_VERSION=1.2.3",
                                "HARNESS_COMMIT=" + COMMIT])
        self.assertIn("--no-cache", command)
        self.assertEqual(command[-3:], ["-t", "model-citizen-arm-harness:t", "/ctx"])
        bare = ARMS.build_command(ARMS.declaration("bare", INPUTS), "/ctx", "i")
        self.assertFalse(any(a.startswith("HARNESS_COMMIT") for a in bare))
        self.assertNotIn("--no-cache", bare)

    def test_a_build_lists_the_image_in_a_sealed_container_and_writes_both_records(self):
        manifest_body = manifest("home:.bashrc")
        docker, cloned = Docker([manifest_body]), []
        decl = ARMS.declaration("harness", INPUTS, {"ref": "v1", "commit": COMMIT})
        with tempfile.TemporaryDirectory() as tmp:
            record = ARMS.build_arm(decl, Path(tmp) / "out", fake_snapshot(cloned), docker, repo="/repo", tmp=tmp)
            self.assertEqual([c[0][1] for c in docker.calls[:3]], ["build", "image", "run"])
            self.assertEqual(cloned[0][:2], ("/repo", COMMIT))
            self.assertEqual(cloned[0][2].name, "harness")
            run, kwargs = docker.calls[2]
            self.assertEqual(run[run.index("--network") + 1], "none")
            self.assertNotIn("-v", run)
            self.assertEqual(kwargs["input"], ARMS.MANIFEST_SCRIPT.read_text(encoding="utf-8"))
            self.assertEqual(record["manifest_sha256"], ARMS.digest(manifest_body))
            self.assertEqual(record["declaration_sha256"], ARMS.digest(decl))
            self.assertEqual((record["harness_ref"], record["harness_commit"]), ("v1", COMMIT))
            self.assertEqual(record["image_id"], "sha256:" + "9" * 64)
            written = json.loads(Path(record["paths"]["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(written, manifest_body)
            self.assertEqual(json.loads(Path(record["paths"]["declaration"]).read_text(encoding="utf-8")), decl)
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["out"])  # the context is gone

    def test_the_bare_arm_context_holds_no_harness_checkout(self):
        docker, cloned = Docker(), []
        with tempfile.TemporaryDirectory() as tmp:
            ARMS.build_arm(ARMS.declaration("bare", INPUTS), tmp, fake_snapshot(cloned), docker, tmp=tmp)
        self.assertEqual(cloned, [])

    def test_a_failed_build_is_named_and_lists_nothing(self):
        docker = Docker()
        docker_fail = lambda command, **kw: docker(command, **kw) if command[1] != "build" else done("", 1, "no space")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as caught:
                ARMS.build_arm(ARMS.declaration("bare", INPUTS), tmp, fake_snapshot([]), docker_fail, tmp=tmp)
        self.assertIn("no space", str(caught.exception))


class TwoBuildTests(unittest.TestCase):
    def check(self, manifests, dry_run=False):
        docker, out = Docker(manifests), io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            equal = ARMS.two_build_check(ARMS.declaration("bare", INPUTS), tmp, fake_snapshot([]), docker,
                                         dry_run=dry_run, tmp=tmp, say=lambda line: out.write(line + "\n"))
        return equal, out.getvalue(), docker

    def test_two_equal_builds_pass_and_both_images_are_removed(self):
        equal, out, docker = self.check([manifest("home:a"), manifest("home:a")])
        self.assertTrue(equal)
        self.assertIn(", equal", out)
        builds = docker.commands("docker", "build")
        self.assertEqual(len(builds), 2)
        self.assertTrue(all("--no-cache" in b for b in builds))
        self.assertEqual(docker.commands("docker", "image", "rm"),
                         [["docker", "image", "rm", "--force", "model-citizen-arm-bare:check-1",
                           "model-citizen-arm-bare:check-2"]])

    def test_two_builds_that_differ_fail_and_name_every_difference(self):
        second = manifest("home:a", "home:b")
        second["entries"][0]["sha256"] = "y"
        equal, out, _ = self.check([manifest("home:a"), second])
        self.assertFalse(equal)
        self.assertIn("2 difference(s)", out)
        self.assertIn("differs: home:a (sha256)", out)
        self.assertIn("only in the second build: home:b", out)

    def test_a_dry_run_shows_the_check_and_runs_nothing(self):
        equal, out, docker = self.check([], dry_run=True)
        self.assertTrue(equal)
        self.assertEqual(docker.calls, [])
        self.assertEqual(out.count("docker build"), 2)
        self.assertEqual(out.count("--no-cache"), 2)
        self.assertIn("compare the two manifests", out)

    def test_a_top_level_difference_counts_too(self):
        lines = ARMS.compare_manifests(manifest(claude_code_version="1"), manifest(claude_code_version="2"))
        self.assertEqual(lines, ["claude_code_version: '1' != '2'"])


class RunTests(unittest.TestCase):
    def test_a_run_mounts_the_snapshot_alone_and_names_the_credential_without_its_value(self):
        env = ARMS.arm_env("http://p:3128", "lean")
        command = ARMS.run_command("img", "/tmp/snap", ["claude", "-p", "x"], "net", env, "run-1")
        self.assertEqual(command[:3], ["docker", "run", "--rm"])
        self.assertEqual([command[i + 1] for i, p in enumerate(command) if p == "-v"], ["/tmp/snap:/work"])
        self.assertEqual(command[command.index("-w") + 1], "/work")
        flags = [command[i + 1] for i, p in enumerate(command) if p == "-e"]
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", flags)  # by name: Docker copies it from the client
        self.assertFalse(any(f.startswith("CLAUDE_CODE_OAUTH_TOKEN=") for f in flags))
        self.assertFalse(any(f.startswith(("HOME=", "CLAUDE_CONFIG_DIR=")) for f in flags))
        self.assertEqual(flags[1:], sorted(flags[1:]))
        self.assertEqual(command[command.index("--network") + 1], "net")
        self.assertIn("no-new-privileges", command)
        self.assertEqual(command[command.index("--cap-drop") + 1], "ALL")
        self.assertEqual(command[-4:], ["img", "claude", "-p", "x"])

    def test_a_check_has_no_network_and_no_credential(self):
        command = ARMS.check_command("img", "/tmp/snap", ["python3", "-"])
        self.assertEqual(command[command.index("--network") + 1], "none")
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", command)

    def test_the_docker_client_keeps_its_own_context_and_passes_nothing_else_on(self):
        base = {"HOME": "/h", "PATH": "/bin", "DOCKER_CONTEXT": "desktop", "ANTHROPIC_API_KEY": "k",
                "CLAUDE_CONFIG_DIR": "/h/.claude", "HARNESS_STANCE_COST": "max"}
        self.assertEqual(ARMS.client_env(base=base), {"HOME": "/h", "PATH": "/bin", "DOCKER_CONTEXT": "desktop"})

    def test_the_egress_is_an_internal_network_whose_one_exit_is_the_allowlist_proxy(self):
        names = ARMS.egress_names("t")
        create, proxy, connect = ARMS.egress_commands("bare-img", names)
        self.assertEqual(create, ["docker", "network", "create", "--internal", names["network"]])
        self.assertEqual(proxy[proxy.index("--network") + 1], names["network"])
        self.assertIn("bare-img", proxy)
        self.assertEqual(proxy[proxy.index("-c") + 1], ARMS.PROXY_SCRIPT.read_text(encoding="utf-8"))
        self.assertEqual(proxy[-2:], [str(ARMS.PROXY_PORT), "api.anthropic.com"])
        self.assertNotIn("-v", proxy)
        self.assertEqual(connect, ["docker", "network", "connect", "bridge", names["proxy"]])
        self.assertEqual(ARMS.arm_env(ARMS.proxy_url(names))["HTTPS_PROXY"], "http://%s:3128" % names["proxy"])

    def test_the_proxy_joins_the_outbound_network_only_after_it_has_bound_its_internal_address(self):
        docker = Docker()
        with ARMS.egress("img", docker, "t") as net:
            self.assertEqual(net["bound"], "192.0.2.10:3128")
        order = [c[1] if c[1] != "network" else "network " + c[2] for c, _ in docker.calls]
        self.assertEqual(order[:4], ["network create", "run", "logs", "network connect"])

    def test_a_proxy_that_never_reports_its_address_stops_the_run(self):
        silent = lambda command, **kw: done()
        with mock.patch.object(ARMS, "PROXY_WAIT", (3, 0)):
            with self.assertRaises(SystemExit):
                ARMS.wait_for_proxy(ARMS.egress_names("t"), silent, sleep=lambda s: None)

    def test_the_egress_is_torn_down_even_when_the_replay_raises(self):
        docker = Docker()
        with self.assertRaises(ValueError):
            with ARMS.egress("img", docker, "t"):
                raise ValueError("a run in the middle of the schedule")
        self.assertEqual(docker.calls[-2][0][:3], ["docker", "rm", "--force"])
        self.assertEqual(docker.calls[-1][0][:3], ["docker", "network", "rm"])

    def test_the_probe_passes_only_when_the_model_api_answers_and_the_rest_is_refused(self):
        allowed = ARMS.PROBE_ALLOWED
        docker = Docker(probe={(allowed, False): done("404")})
        lines, passed = ARMS.egress_probe("img", docker, "t")
        self.assertTrue(passed, lines)
        self.assertEqual([line.split(":")[0] for line in lines],
                         ["PASS model API through the proxy", "PASS another host through the proxy",
                          "PASS model API with the proxy bypassed"])
        for command in docker.commands("docker", "run"):
            if "curl" in command:
                self.assertNotIn("-v", command)
                self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", command)
        leaky = Docker(probe={(allowed, False): done("404"), (ARMS.PROBE_REFUSED, False): done("200")})
        self.assertFalse(ARMS.egress_probe("img", leaky, "t")[1])


class AdmissionSeamTests(unittest.TestCase):
    def setUp(self):
        declaration = {"schema": ARMS.SCHEMA, "arm": "bare", "base_image": "base@sha256:0",
                       "claude_code_version": "1.0", "harness": None,
                       "components": [{"name": "base-image", "version": "base@sha256:0"},
                                      {"name": "@anthropic-ai/claude-code", "version": "1.0"},
                                      {"name": "model-citizen-observer",
                                       "version": "sha256:" + ARMS.file_sha(ARMS.OBSERVER_SOURCE)}],
                       "effort": "high",
                       "observer_settings_sha256": ARMS.digest(ARMS.observer_settings())}
        observer_sha = ARMS.file_sha(ARMS.OBSERVER_SOURCE)
        manifest = {"schema": LISTER.SCHEMA, "claude_code_version": "1.0",
                    "cli_packages": ["@anthropic-ai/claude-code" + "@1.0"], "harness_commit": None,
                    "roots": {"home": "/home/agent", "observer": "/opt/model-citizen-observer"},
                    "summary": LISTER.summary([{"path": "observer:observe.py", "kind": "file",
                                                 "sha256": observer_sha}]),
                    "entries": [{"path": "observer:observe.py", "kind": "file",
                                  "sha256": observer_sha}],
                    "environment": {ARMS.EFFORT_ENV: None}}
        self.record = {"label": "bare", "image": "i", "image_id": "sha256:1",
                       "declaration_sha256": ARMS.digest(declaration),
                       "manifest_sha256": ARMS.digest(manifest), "declaration": declaration,
                       "manifest": manifest, "protocol": {"evidence": "exploratory"}}

    def test_a_complete_record_is_admitted(self):
        self.assertIsNone(ARMS.admit(self.record))

    def test_a_record_missing_its_manifest_digest_is_refused(self):
        with self.assertRaises(SystemExit) as caught:
            ARMS.admit(dict(self.record, manifest_sha256=None))
        self.assertIn("manifest_sha256", str(caught.exception))

    def test_record_schemas_and_digests_are_recomputed(self):
        record = dict(self.record, declaration=dict(self.record["declaration"], schema=0),
                      manifest=dict(self.record["manifest"], schema=0))
        with self.assertRaises(SystemExit) as caught:
            ARMS.admit(record)
        refusal = str(caught.exception)
        for text in ("declaration schema", "manifest schema", "declaration_sha256 does not match",
                     "manifest_sha256 does not match"):
            self.assertIn(text, refusal)

    def test_missing_duplicate_and_malformed_harness_components_are_refused(self):
        harness = {"ref": "v1", "commit": "c" * 40}
        for components, expected in (
                ([{"name": "base-image", "version": "base@sha256:0"},
                  {"name": "@anthropic-ai/claude-code", "version": "1.0"}],
                 "exactly one model-citizen"),
                ([{"name": "model-citizen", "version": "v1", "commit": "c" * 40},
                  {"name": "model-citizen", "version": "v1", "commit": "c" * 40}],
                 "duplicate components"),
                ([{"name": "model-citizen"}], "component 0 is malformed")):
            record = dict(self.record, declaration=dict(self.record["declaration"], arm="harness",
                                                         harness=harness, components=components))
            record["declaration_sha256"] = ARMS.digest(record["declaration"])
            with self.assertRaises(SystemExit) as caught:
                ARMS.admit(record)
            self.assertIn(expected, str(caught.exception))

    def test_admission_reports_independent_defects_together(self):
        record = dict(self.record, declaration_sha256="0" * 64,
                      protocol={}, manifest=dict(self.record["manifest"]))
        record["manifest"]["environment"] = {ARMS.EFFORT_ENV: "max"}
        record["manifest_sha256"] = ARMS.digest(record["manifest"])
        with self.assertRaises(SystemExit) as caught:
            ARMS.admit(record)
        refusal = str(caught.exception)
        self.assertIn("declaration_sha256 does not match", refusal)
        self.assertIn("no committed pre-registration", refusal)
        self.assertIn("image bakes", refusal)

    def test_a_non_object_manifest_entry_is_refused_without_crashing_other_checks(self):
        record = dict(self.record, manifest=dict(self.record["manifest"]))
        record["manifest"]["entries"] = [42]
        record["manifest_sha256"] = ARMS.digest(record["manifest"])
        with self.assertRaises(SystemExit) as caught:
            ARMS.admit(record)
        self.assertIn("manifest entries contain a malformed path", str(caught.exception))

    def test_malformed_paths_and_unsupported_entry_kinds_are_refused_without_a_crash(self):
        for entry, expected in (({"path": 7, "kind": "file"}, "malformed path"),
                                ({"path": "home:socket", "kind": "socket"},
                                 "unsupported kind 'socket'")):
            record = dict(self.record, manifest=dict(self.record["manifest"]))
            record["manifest"]["entries"] = [entry]
            record["manifest_sha256"] = ARMS.digest(record["manifest"])
            with self.subTest(entry=entry), self.assertRaises(SystemExit) as caught:
                ARMS.admit(record)
            self.assertIn(expected, str(caught.exception))

    def test_parent_segments_cannot_make_a_link_look_inside_the_harness(self):
        self.assertFalse(ARMS._inside("/opt/model-citizen/../outside", ARMS.HARNESS_ROOT))
        self.assertTrue(ARMS._inside("/opt/model-citizen/primitives", ARMS.HARNESS_ROOT))

    def test_admission_names_every_undeclared_configuration_entry(self):
        record = dict(self.record, manifest=dict(self.record["manifest"]))
        entries = [{"path": "home:.claude/settings.z.json", "kind": "file"},
                   {"path": "home:.claude/settings.a.json", "kind": "file"}]
        record["manifest"]["entries"] = entries
        record["manifest"]["summary"] = LISTER.summary(entries)
        record["manifest_sha256"] = ARMS.digest(record["manifest"])
        with self.assertRaises(SystemExit) as caught:
            ARMS.admit(record)
        refusal = str(caught.exception)
        for entry in entries:
            self.assertIn(entry["path"], refusal)
        self.assertLess(refusal.index(entries[1]["path"]), refusal.index(entries[0]["path"]))

    def test_a_further_check_can_refuse_through_the_same_seam(self):
        """The protocol's own refusals plug in here, each a function of the arm's record."""
        differs = lambda record: "manifest differs from its declaration"
        with mock.patch.object(ARMS, "ADMISSION_CHECKS", ARMS.ADMISSION_CHECKS + [differs]):
            with self.assertRaises(SystemExit) as caught:
                ARMS.admit(self.record)
        self.assertIn("differs from its declaration", str(caught.exception))


class ManifestListerTests(unittest.TestCase):
    def listed(self, home, harness=None):
        roots = (("home", str(home)), ("managed", str(Path(home).parent / "no-managed")),
                 ("harness", str(harness or Path(home).parent / "no-harness")))
        out = io.StringIO()
        with mock.patch.object(LISTER, "ROOTS", roots), contextlib.redirect_stdout(out):
            LISTER.main()
        return json.loads(out.getvalue())

    def tree(self, tmp):
        home = Path(tmp) / "home"
        for rel, text in ((".claude/settings.json", "{}"), (".claude/rules/a.md", "rule"),
                          (".npm/_logs/debug.log", "volatile"), (".bashrc", "x"),
                          (".local/state/agent-harness/manifest.json", '{"synced_at": "2026-01-01T00:00:00+0000"}')):
            (home / rel).parent.mkdir(parents=True, exist_ok=True)
            (home / rel).write_text(text, encoding="utf-8")
        (home / ".claude" / "hooks").mkdir()
        (home / ".claude" / "hooks" / "harness").symlink_to("/opt/model-citizen/policy/hooks")
        return home

    def test_two_listings_of_one_tree_are_identical_and_ignore_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self.tree(tmp)
            first = self.listed(home)
            os.utime(str(home / ".bashrc"), (1, 1))
            self.assertEqual(self.listed(home), first)
            (home / ".bashrc").write_text("y", encoding="utf-8")
            self.assertNotEqual(self.listed(home), first)

    def test_every_file_and_link_is_listed_with_its_hash_or_target_and_caches_are_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            listed = self.listed(self.tree(tmp))
            paths = {e["path"]: e for e in listed["entries"]}
            self.assertEqual(paths["home:.claude/hooks/harness"]["target"], "/opt/model-citizen/policy/hooks")
            self.assertEqual(paths["home:.claude/rules/a.md"]["size"], 4)
            self.assertEqual(len(paths["home:.bashrc"]["sha256"]), 64)
            self.assertFalse([p for p in paths if p.startswith("home:.npm")])
            self.assertEqual([e["path"] for e in listed["entries"]], sorted(paths))
            self.assertEqual(listed["summary"]["settings"], ["home:.claude/settings.json"])
            self.assertEqual(listed["summary"]["hooks"], ["home:.claude/hooks/harness"])
            self.assertEqual(listed["summary"]["rules"], ["home:.claude/rules/a.md"])
            self.assertEqual(listed["excluded"]["home"], [".npm", ".cache"])

    def test_the_sync_time_is_normalised_and_the_manifest_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self.tree(tmp)
            first = self.listed(home)
            state = home / ".local/state/agent-harness/manifest.json"
            state.write_text('{"synced_at": "2027-05-05T09:09:09+0000"}', encoding="utf-8")
            second = self.listed(home)
            self.assertEqual(first, second)
            entry = [e for e in second["entries"] if e["path"].endswith("agent-harness/manifest.json")][0]
            self.assertTrue(entry["normalised"])
            self.assertIn("home:.local/state/agent-harness/manifest.json", second["normalised"])

    def test_every_global_cli_package_is_listed_by_name_and_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            modules = Path(tmp) / "npm" / "lib" / "node_modules"
            # Versions carry no dot, since a dotted `name@version` trips the lint's email pattern.
            for name, version in (("@anthropic-ai/claude-code", "123"), ("@openai/codex", "10"), ("left", "2")):
                (modules / name).mkdir(parents=True)
                (modules / name / "package.json").write_text(json.dumps({"version": version}), encoding="utf-8")
            with mock.patch.dict(os.environ, {"NPM_CONFIG_PREFIX": str(Path(tmp) / "npm")}):
                self.assertEqual(LISTER.cli_packages(), ["@anthropic-ai/claude-code@123", "@openai/codex@10",
                                                         "left@2"])
                self.assertEqual(LISTER.cli_version(), "123")

    def test_treatment_paths_match_files_rendered_by_a_real_sync(self):
        old_environ = dict(os.environ)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp) / "home"
                home.mkdir()
                isolate_home(home)
                personal = home / ".codex" / "AGENTS.personal.md"
                personal.parent.mkdir(parents=True)
                personal.write_text("user-owned\n", encoding="utf-8")
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(harness.cmd_sync(harness.argparse.Namespace(
                        dry_run=False, adopt=False, adopt_codex=False, print_only=False)), 0)
                listed = self.listed(home, harness=REPO)
        finally:
            os.environ.clear()
            os.environ.update(old_environ)
        treatment = ARMS._treatment_paths(listed)
        for path in ("home:.codex/AGENTS.md", "home:.codex/agents/builder.toml",
                     "home:.agents/skills/harness-build/SKILL.md",
                     "home:.config/agent-harness/config.json"):
            self.assertIn(path, treatment)
        self.assertNotIn("home:.codex/AGENTS.personal.md", treatment)

    def test_generated_agent_and_workflow_paths_require_their_checkout_sources(self):
        entries = [
            {"path": "harness:primitives/roles/builder.md", "kind": "file"},
            {"path": "harness:primitives/workflows/build.md", "kind": "file"},
            {"path": "home:.codex/agents/builder.toml", "kind": "file"},
            {"path": "home:.agents/skills/harness-build/SKILL.md", "kind": "file"},
            {"path": "home:.codex/agents/personal.toml", "kind": "file"},
            {"path": "home:.agents/skills/harness-personal/SKILL.md", "kind": "file"},
        ]
        treatment = ARMS._treatment_paths({"entries": entries})
        self.assertIn("home:.codex/agents/builder.toml", treatment)
        self.assertIn("home:.agents/skills/harness-build/SKILL.md", treatment)
        self.assertNotIn("home:.codex/agents/personal.toml", treatment)
        self.assertNotIn("home:.agents/skills/harness-personal/SKILL.md", treatment)

    def test_the_arm_image_removes_the_base_templates_codex_client(self):
        """The base is the Codex sandbox template; an arm carries Claude Code and no other client."""
        text = ARMS.ARM_DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn("npm uninstall -g @openai/codex", text)
        self.assertIn("! command -v codex", text)

    def test_the_harness_checkout_is_listed_without_its_git_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self.tree(tmp)
            harness = Path(tmp) / "harness"
            (harness / ".git").mkdir(parents=True)
            (harness / ".git" / "packed").write_text("differs per clone", encoding="utf-8")
            (harness / "VERSION").write_text("1\n", encoding="utf-8")
            listed = self.listed(home, harness)
            paths = [e["path"] for e in listed["entries"]]
            self.assertIn("harness:VERSION", paths)
            self.assertFalse([p for p in paths if p.startswith("harness:.git")])


class WorkdirTests(unittest.TestCase):
    def test_the_image_trusts_the_mount_for_git(self):
        self.assertIn("safe.directory /work", ARMS.ARM_DOCKERFILE.read_text(encoding="utf-8"))

    def test_the_probe_writes_and_asks_git_inside_the_mount_with_no_network(self):
        command = ARMS.workdir_probe_command("img", "/tmp/snap", "probe-1")
        self.assertEqual(command[command.index("--network") + 1], "none")
        self.assertEqual(command[command.index("--name") + 1], "probe-1")
        self.assertIn("touch /work/", command[-1])
        self.assertIn("git -C /work rev-parse", command[-1])
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", command)

    def test_a_probe_that_fails_refuses_the_arm_with_its_reason(self):
        failing = lambda command, **kw: done("", 128, "fatal: detected dubious ownership in repository at '/work'")
        with self.assertRaises(SystemExit) as caught:
            ARMS.probe_workdir({"label": "bare", "image": "img"}, "/tmp/snap", failing)
        self.assertIn("dubious ownership", str(caught.exception))
        self.assertIsNone(ARMS.probe_workdir({"label": "bare", "image": "img"}, "/tmp/snap",
                                             lambda command, **kw: done()))


class EgressProxyTests(unittest.TestCase):
    def test_the_proxy_listens_only_on_its_own_resolved_address_never_everywhere(self):
        with mock.patch.object(PROXY.socket, "gethostname", lambda: "proxy"), \
                mock.patch.object(PROXY.socket, "gethostbyname", lambda name: "192.0.2.10"):
            self.assertEqual(PROXY.bind_address(), "192.0.2.10")
        for answer in ("127.0.0.1", "0.0.0.0"):
            with mock.patch.object(PROXY.socket, "gethostbyname", lambda name, a=answer: a):
                with self.assertRaises(SystemExit):
                    PROXY.bind_address()
        self.assertNotIn('"0.0.0.0", port', PROXY_SOURCE)


    def test_only_a_connect_to_an_allowed_host_on_443_is_let_through(self):
        hosts = {"api.anthropic.com"}
        self.assertTrue(PROXY.allowed(PROXY.target_of(b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n\r\n"), hosts))
        for head in (b"CONNECT example.com:443 HTTP/1.1\r\n\r\n", b"CONNECT api.anthropic.com:80 HTTP/1.1\r\n\r\n",
                     b"GET http://api.anthropic.com/ HTTP/1.1\r\n\r\n", b"CONNECT api.anthropic.com.evil:443 H\r\n\r\n",
                     b"garbage"):
            self.assertFalse(PROXY.allowed(PROXY.target_of(head), hosts), head)

    def test_a_refused_request_gets_a_403_from_the_running_proxy(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            threading.Thread(target=PROXY.main, args=([str(port), "api.anthropic.com"], "127.0.0.1"),
                             daemon=True).start()
            for _ in range(50):
                try:
                    client = socket.create_connection(("127.0.0.1", port), timeout=5)
                    break
                except OSError:
                    threading.Event().wait(0.05)
            client.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
            reply = client.recv(1024)
            client.close()
        self.assertTrue(reply.startswith(b"HTTP/1.1 403"), reply)


if __name__ == "__main__":
    unittest.main()

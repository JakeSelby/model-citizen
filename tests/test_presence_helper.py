# SPDX-License-Identifier: MIT
"""The Model Citizen helper app that asks for presence (`harness_core.presence_helper`).

`presence.confirm` asks through the helper when it is built and verified, relays its answer
(a decline is a refusal, never a reason to ask another way), and falls back to `osascript` when
the helper is missing or cannot be built. A helper whose signature no longer matches the hash
recorded at its build is rebuilt or not used. No test here raises the real dialog: the helper
is only ever run with no reason, which prints its usage.

Run: python3 -m unittest tests.test_presence_helper
"""
import contextlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

import isolation  # noqa: E402,F401  (switches the real check off in children)
from harness_core import presence, presence_helper  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "presence_check.py"


ACTION = ("Model Citizen", "apply draft d", "your edits take effect")


class Asking(unittest.TestCase):
    """Which program asks, with which words, and what its answer means."""

    def setUp(self):
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))
        os.environ.pop(presence.OFF, None)
        for name in presence.CODEX_VARIABLES:
            os.environ.pop(name, None)
        self.calls = []

    def ask(self, helper, answer):
        def run(argv, **kwargs):
            self.calls.append((argv, kwargs))
            if argv[0] == presence.LAUNCHCTL:
                return subprocess.CompletedProcess(argv, 0, stdout="Aqua\n", stderr="")
            return subprocess.CompletedProcess(argv, answer[0], stdout=answer[1], stderr="")

        with mock.patch.object(presence.subprocess, "run", side_effect=run), \
                mock.patch.object(presence.platform, "system", return_value="Darwin"), \
                mock.patch.object(presence.os, "access", return_value=True), \
                mock.patch.object(presence_helper, "ready",
                                  return_value=contextlib.nullcontext(helper)):
            return presence.check(*ACTION)

    def asked_with(self):
        return [argv for argv, _ in self.calls if argv[0] != presence.LAUNCHCTL]

    def test_the_helper_asks_with_a_verb_phrase(self):
        helper = presence_helper.Helper(Path("/state/presence-helper/x/Model Citizen.app/x"), "c" * 40)
        self.assertEqual(self.ask(helper, (0, "present\n")), (True, "helper", "c" * 40))
        self.assertEqual(self.asked_with(), [[str(helper.path),
                                              "apply draft d, so your edits take effect"]])
        _argv, kwargs = self.calls[-1]
        self.assertEqual(kwargs["env"], {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"})

    def test_osascript_asks_with_the_whole_sentence(self):
        self.assertEqual(self.ask(None, (0, "present\n")), (True, "osascript", ""))
        argv = self.asked_with()[0]
        self.assertEqual(argv[:3], [presence.OSASCRIPT, "-l", "JavaScript"])
        self.assertEqual(argv[-1], "Model Citizen wants to apply draft d, so your edits take effect.")
        self.calls = []
        self.assertEqual(self.ask(None, (0, "declined\n")).present, False)

    def test_a_declined_helper_is_a_refusal_and_nothing_else_is_asked(self):
        helper = presence_helper.Helper(Path("/state/helper"), "c" * 40)
        for answer in ((1, "declined\n"), (2, "timeout\n"), (3, "unavailable\n"), (0, "declined\n"),
                       (1, "present\n"), (0, ""), (0, "present\ntimeout\n")):
            with self.subTest(answer=answer):
                self.calls = []
                self.assertEqual(self.ask(helper, answer), (False, "helper", "c" * 40))
                self.assertEqual(len(self.asked_with()), 1)

    def test_a_helper_that_cannot_be_prepared_falls_back(self):
        with mock.patch.object(presence_helper, "root", side_effect=KeyError("no such user")):
            with presence_helper.ready() as found:
                self.assertIsNone(found)

    def test_where_presence_cannot_be_checked_no_helper_is_built(self):
        os.environ[presence.OFF] = "1"
        with mock.patch.object(presence_helper, "ready", side_effect=AssertionError("built")):
            self.assertEqual(presence.check(*ACTION), (False, "none", ""))


class ActivityRecord(unittest.TestCase):
    """Each confirmation row names the program that answered and the helper's signature."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="presence-record-"))
        self.addCleanup(shutil.rmtree, str(self.home), True)
        patched = mock.patch.dict(os.environ, {"HARNESS_HOME": str(self.home)})
        patched.start()
        self.addCleanup(patched.stop)

    def rows(self):
        path = presence.state_root() / "decisions.jsonl"
        return [json.loads(line)["detail"] for line in path.read_text().splitlines()]

    def test_the_row_names_the_helper_and_its_hash_or_osascript(self):
        reason = presence.Reason(*ACTION)
        for answer in (presence.Answer(True, "helper", "c" * 40), presence.Answer(True, "osascript", "")):
            presence._LAST.answer = answer
            presence.record("citizen", ["citizen", "draft", "apply"], reason)
        helper, osascript = self.rows()
        self.assertEqual((helper["program"], helper["helper_cdhash"]), ("helper", "c" * 40))
        self.assertIn("the Model Citizen helper's dialog (signature %s)" % ("c" * 40), helper["reason"])
        self.assertTrue(helper["reason"].endswith(
            "Model Citizen wants to apply draft d, so your edits take effect."))
        self.assertEqual((osascript["program"], osascript["helper_cdhash"]), ("osascript", ""))
        self.assertIn("osascript's dialog", osascript["reason"])

    def test_check_leaves_its_answer_for_the_record(self):
        with mock.patch.object(presence, "unavailable", return_value=None), \
                mock.patch.object(presence_helper, "ready", return_value=contextlib.nullcontext(
                    presence_helper.Helper(Path("/h"), "d" * 40))), \
                mock.patch.object(presence.subprocess, "run", return_value=subprocess.CompletedProcess(
                    [], 0, stdout="present\n", stderr="")):
            self.assertTrue(presence.check(*ACTION).present)
        presence.record("Studio", [], presence.Reason(*ACTION))
        self.assertEqual(self.rows()[-1]["helper_cdhash"], "d" * 40)


class Verification(unittest.TestCase):
    """The recorded code-signing hash decides whether a built helper is used."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="presence-helper-"))
        self.addCleanup(shutil.rmtree, str(self.base), True)
        os.chmod(str(self.base), 0o700)
        self.directory = self.base / presence_helper.source_digest()[:16]
        self.directory.mkdir(mode=0o700)
        (self.directory / presence_helper.APP).mkdir()

    def write_record(self, cdhash="a" * 40, source=None):
        (self.directory / presence_helper.RECORD).write_text(json.dumps(
            {"cdhash": cdhash, "source": source or presence_helper.source_digest()}))

    def test_a_matching_hash_is_used_from_the_source_digests_directory(self):
        self.write_record()
        with mock.patch.object(presence_helper, "cdhash", return_value="a" * 40), \
                mock.patch.object(presence_helper, "build", side_effect=AssertionError("built")):
            with presence_helper.ready(self.base) as found:
                self.assertEqual(found, presence_helper.Helper(
                    self.directory / presence_helper.APP / "Contents" / "MacOS"
                    / presence_helper.EXECUTABLE, "a" * 40))

    def test_a_tampered_helper_is_refused_and_rebuilt(self):
        self.write_record()
        rebuilt = presence_helper.Helper(self.directory / "rebuilt", "b" * 40)
        with mock.patch.object(presence_helper, "cdhash", return_value="b" * 40), \
                mock.patch.object(presence_helper, "build", return_value=rebuilt) as build:
            self.assertIsNone(presence_helper.verified(self.directory))
            self.assertEqual(presence_helper.executable(self.directory), rebuilt)
        build.assert_called_once_with(self.directory)

    def test_a_tampered_helper_that_cannot_be_rebuilt_is_not_used(self):
        self.write_record()
        with mock.patch.object(presence_helper, "cdhash", return_value=None), \
                mock.patch.object(presence_helper, "toolchain", return_value=None):
            with presence_helper.ready(self.base) as found:
                self.assertIsNone(found)

    def test_a_record_of_older_source_or_a_missing_record_rebuilds(self):
        for write in (lambda: self.write_record(source="0" * 64), lambda: None):
            with self.subTest(write=write):
                (self.directory / presence_helper.RECORD).unlink(missing_ok=True)
                write()
                with mock.patch.object(presence_helper, "cdhash", return_value="a" * 40):
                    self.assertIsNone(presence_helper.verified(self.directory))

    def test_a_directory_others_can_write_is_not_trusted(self):
        self.write_record()
        os.chmod(str(self.directory), 0o777)
        with mock.patch.object(presence_helper, "cdhash", return_value="a" * 40):
            self.assertIsNone(presence_helper.verified(self.directory))

    def test_without_developer_tools_nothing_is_compiled(self):
        with mock.patch.object(presence_helper, "toolchain", return_value=None), \
                mock.patch.object(presence_helper.subprocess, "run",
                                  side_effect=AssertionError("ran a tool")):
            self.assertIsNone(presence_helper.build(self.directory))

    def test_a_failed_build_is_not_retried_for_a_day_with_the_same_toolchain(self):
        compiles = []

        def run(argv, **kwargs):
            compiles.append(argv[0])
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="licence not accepted")

        with mock.patch.object(presence_helper, "toolchain", return_value="t1"), \
                mock.patch.object(presence_helper.subprocess, "run", side_effect=run):
            self.assertIsNone(presence_helper.build(self.directory))
            self.assertIsNone(presence_helper.build(self.directory))
        self.assertEqual(compiles, [presence_helper.SWIFTC])
        failed = json.loads((self.directory / presence_helper.FAILED).read_text())
        self.assertEqual(failed["toolchain"], "t1")
        with mock.patch.object(presence_helper, "toolchain", return_value="t2"), \
                mock.patch.object(presence_helper.subprocess, "run", side_effect=run):
            presence_helper.build(self.directory)
        self.assertEqual(len(compiles), 2)
        with mock.patch.object(presence_helper, "toolchain", return_value="t2"), \
                mock.patch.object(presence_helper.time, "time",
                                  return_value=failed["at"] + presence_helper.RETRY_AFTER * 2), \
                mock.patch.object(presence_helper.subprocess, "run", side_effect=run):
            presence_helper.build(self.directory)
        self.assertEqual(len(compiles), 3)

    def test_the_compile_is_capped_at_two_minutes(self):
        seen = {}

        def run(argv, **kwargs):
            seen.setdefault(argv[0], kwargs["timeout"])
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

        with mock.patch.object(presence_helper, "toolchain", return_value="t"), \
                mock.patch.object(presence_helper.subprocess, "run", side_effect=run):
            self.assertIsNone(presence_helper.build(self.directory))
        self.assertEqual(seen[presence_helper.SWIFTC], 120)

    def test_two_builders_build_once_and_both_use_it(self):
        """Two first uses at once: one builds under the exclusive lock, the other waits and
        uses what it built; neither sees a half-replaced helper."""
        builds, results = [], []
        lock = threading.Lock()

        def build(directory):
            with lock:
                builds.append(directory)
            time.sleep(0.3)
            self.write_record()
            return presence_helper.verified(directory)

        def cdhash(app):
            return "a" * 40 if (self.directory / presence_helper.RECORD).exists() else None

        def use():
            with presence_helper.ready(self.base) as found:
                results.append(found)
                time.sleep(0.1)

        with mock.patch.object(presence_helper, "build", side_effect=build), \
                mock.patch.object(presence_helper, "cdhash", side_effect=cdhash):
            threads = [threading.Thread(target=use) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(10)
        self.assertEqual(len(builds), 1)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(found is not None and found.cdhash == "a" * 40 for found in results))

    def test_a_build_waits_while_a_helper_is_in_use(self):
        self.write_record()
        order = []
        with mock.patch.object(presence_helper, "cdhash", return_value="a" * 40):
            with presence_helper.ready(self.base) as found:
                self.assertIsNotNone(found)

                def rebuild():
                    with presence_helper._locked(self.directory, presence_helper.fcntl.LOCK_EX):
                        order.append("rebuilt")

                thread = threading.Thread(target=rebuild)
                thread.start()
                time.sleep(0.2)
                order.append("used")
            thread.join(10)
        self.assertEqual(order, ["used", "rebuilt"])

    def test_the_record_is_written_through_a_temporary_file_of_its_own(self):
        names = []
        real = presence_helper.tempfile.mkstemp

        def mkstemp(**kwargs):
            fd, name = real(**kwargs)
            names.append(name)
            return fd, name

        with mock.patch.object(presence_helper.tempfile, "mkstemp", side_effect=mkstemp):
            presence_helper._write_json(self.directory, presence_helper.RECORD, {"a": 1})
            presence_helper._write_json(self.directory, presence_helper.RECORD, {"a": 2})
        self.assertEqual(len(set(names)), 2)
        self.assertEqual(json.loads((self.directory / presence_helper.RECORD).read_text()), {"a": 2})
        self.assertEqual(sorted(p.name for p in self.directory.iterdir() if p.name.startswith(".helper")), [])

    def test_no_environment_variable_moves_the_helper(self):
        before = presence_helper.root()
        with mock.patch.dict(os.environ, {"HOME": str(self.directory),
                                          "HARNESS_HOME": str(self.directory)}):
            self.assertEqual(presence_helper.root(), before)
        self.assertNotIn(str(self.directory), str(before))

    def test_the_tools_run_with_a_fixed_environment(self):
        with mock.patch.dict(os.environ, {"DEVELOPER_DIR": "/planted", "SDKROOT": "/planted",
                                          "PATH": "/planted"}):
            environment = presence_helper._environment()
        self.assertEqual(set(environment), {"PATH", "HOME", "LANG"})
        self.assertEqual(environment["PATH"], "/usr/bin:/bin")


class Source(unittest.TestCase):
    """The helper's source relays LocalAuthentication's answer and reads nothing else."""

    def setUp(self):
        self.text = (presence_helper.SOURCE / "main.swift").read_text(encoding="utf-8")

    def test_present_is_printed_only_from_the_policys_success_reply(self):
        self.assertEqual(self.text.count('print("present")'), 1)
        reply = self.text.index("localizedReason: arguments[1]) { success, _ in")
        success = self.text.index("if success {", reply)
        self.assertLess(success, self.text.index('print("present")'))
        self.assertEqual(self.text.count("exit(0)"), 1)
        self.assertLess(self.text.index('print("present")'), self.text.index("exit(0)"))
        self.assertIn(".deviceOwnerAuthentication", self.text)

    def test_it_reads_no_environment_file_or_defaults(self):
        for reader in ("ProcessInfo", "getenv", "FileManager", "UserDefaults", "contentsOf",
                       "Process(", "NSTask", "dlopen"):
            self.assertNotIn(reader, self.text)

    def test_the_bundle_is_named_model_citizen(self):
        import plistlib
        info = plistlib.loads((presence_helper.SOURCE / "Info.plist").read_bytes())
        self.assertEqual((info["CFBundleName"], info["CFBundleDisplayName"]),
                         ("Model Citizen", "Model Citizen"))
        self.assertEqual(info["CFBundleExecutable"], presence_helper.EXECUTABLE)
        self.assertEqual(info["CFBundleIconFile"] + ".icns", presence_helper.ICON)

    def test_no_built_helper_is_tracked(self):
        tracked = subprocess.run(["git", "-C", str(REPO), "ls-files"], capture_output=True,
                                 text=True, check=True).stdout.splitlines()
        self.assertEqual([name for name in tracked if name.endswith((".app", ".icns"))
                          or "/Model Citizen.app/" in name or name.endswith(
                              "/" + presence_helper.EXECUTABLE)], [])

    def test_the_icon_is_drawn_as_an_icns_of_png_entries(self):
        data = presence_helper.icon()
        self.assertEqual((data[:4], struct.unpack(">I", data[4:8])[0]), (b"icns", len(data)))
        offset, kinds = 8, []
        while offset < len(data):
            kind, length = data[offset:offset + 4], struct.unpack(">I", data[offset + 4:offset + 8])[0]
            self.assertEqual(data[offset + 8:offset + 16], b"\x89PNG\r\n\x1a\n")
            kinds.append(kind)
            offset += length
        self.assertEqual(kinds, [b"ic07", b"ic08", b"ic09"])


@unittest.skipUnless(sys.platform == "darwin" and presence_helper.developer_tools(),
                     "needs macOS with the developer tools")
class RealBuild(unittest.TestCase):
    """Compile and sign the helper for real, without ever asking: run with no reason, it prints
    its usage."""

    def test_built_verified_tampered_and_rebuilt(self):
        directory = Path(tempfile.mkdtemp(prefix="presence-build-"))
        self.addCleanup(shutil.rmtree, str(directory), True)
        built = presence_helper.build(directory)
        self.assertIsNotNone(built)
        self.assertEqual(presence_helper.verified(directory), built)
        built = built.path
        done = subprocess.run([str(built)], capture_output=True, text=True, timeout=30)
        self.assertEqual((done.returncode, done.stdout), (64, "usage\n"))
        info = subprocess.run(["/usr/bin/codesign", "--display", "--verbose=2", str(
            directory / presence_helper.APP)], capture_output=True, text=True, timeout=30)
        self.assertIn("Signature=adhoc", info.stderr)
        data = bytearray(built.read_bytes())
        data[len(data) // 4] ^= 1
        built.write_bytes(bytes(data))
        self.assertIsNone(presence_helper.verified(directory))
        self.assertEqual(presence_helper.executable(directory).path, built)
        self.assertEqual(presence_helper.verified(directory).path, built)


class ManualCheck(unittest.TestCase):
    """`scripts/presence_check.py` says which program asked; here it is only ever unavailable."""

    def test_it_says_unavailable_without_asking(self):
        env = {k: v for k, v in os.environ.items() if k != "HARNESS_QUIET"}
        env[presence.OFF] = "1"
        done = subprocess.run([sys.executable, "-I", str(SCRIPT)], capture_output=True, text=True,
                              env=env, timeout=60)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout, "unavailable: presence checks are switched off in this "
                                      "process\n")

    def test_it_names_the_path_it_used(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("presence_check_script", str(SCRIPT))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for answer, printed in (((True, "helper", "c" * 40),
                                 "present (asked by the Model Citizen helper)"),
                                ((False, "osascript", ""), "refused (asked by osascript; the "
                                                           "helper could not be built or verified)")):
            with self.subTest(answer=answer), \
                    mock.patch.object(module.presence, "unavailable", return_value=None), \
                    mock.patch.object(module.presence, "check", return_value=answer), \
                    mock.patch("sys.stdout") as stdout, mock.patch("sys.stderr"):
                code = module.main()
            self.assertEqual(code, 0 if answer[0] else 1)
            self.assertEqual(stdout.write.call_args_list[0][0][0], printed)


if __name__ == "__main__":
    unittest.main()

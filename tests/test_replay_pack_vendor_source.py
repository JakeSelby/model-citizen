"""A vendor entry's `source` is never parsed as a git option or run through a transport helper
(#1218 review): only an https URL or an absolute local path is accepted, and the fetch passes the
source after `--` with only those transports allowed. Nothing is fetched from the network."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_replay_pack import PACK

COMMIT = "a" * 40


def entry(source):
    return {"name": "upstream", "source": source, "commit": COMMIT, "license": "MIT",
            "paths": [["LICENSE", "_vendor/LICENSE"]], "digest": "0" * 64}


class SourceValidationTests(unittest.TestCase):
    def test_an_option_shaped_source_is_refused(self):
        errors = PACK.vendor_errors("app", [entry("--upload-pack=touch /tmp/pwned")])
        self.assertTrue(any("is neither an https URL nor an absolute local path" in e for e in errors), errors)

    def test_transport_helpers_and_other_schemes_are_refused(self):
        for source in ("ext::sh -c touch% /tmp/pwned", "fd::3", "file:///tmp/x", "ssh://host/x",
                       "git" + "@" + "host.invalid:o/r.git", "http://example.invalid/r.git", "relative/path",
                       "https://example.invalid/r.git --upload-pack=x", "/tmp/x::y", "-/abs"):
            self.assertFalse(PACK.source_allowed(source), source)
            self.assertTrue(PACK.vendor_errors("app", [entry(source)]), source)

    def test_an_https_url_and_an_absolute_path_are_accepted(self):
        for source in ("https://github.com/bmad-code-org/BMAD-METHOD.git", "/srv/repos/upstream"):
            self.assertTrue(PACK.source_allowed(source), source)
            self.assertEqual(PACK.vendor_errors("app", [entry(source)]), [])


class FetchArgvTests(unittest.TestCase):
    def test_the_source_follows_a_double_dash_and_only_two_transports_are_allowed(self):
        args = list(PACK.fetch_args("https://example.invalid/r.git", COMMIT))
        fetch = args.index("fetch")
        self.assertEqual(args[fetch:], ["fetch", "-q", "--depth", "1", "--", "https://example.invalid/r.git", COMMIT])
        self.assertEqual(args[:fetch], ["-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
                                        "-c", "protocol.file.allow=always"])

    def test_the_fetch_runs_with_that_argv(self):
        calls = []

        def fake(repo, *args, **kwargs):
            calls.append(args)
            return mock.Mock(returncode=1, stdout=b"", stderr=b"stopped here")

        PACK._VENDOR_CACHE.clear()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(PACK, "_git", side_effect=fake):
            with self.assertRaises(SystemExit):
                PACK._vendor_archive(entry("/srv/upstream"), tmp)
        self.assertEqual(calls[0], ("init", "-q"))

        calls.clear()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
                PACK, "_git", side_effect=lambda repo, *a, **k: calls.append(a) or mock.Mock(
                    returncode=0 if a[0] == "init" else 1, stdout=b"", stderr=b"")):
            with self.assertRaises(SystemExit):
                PACK._vendor_archive(entry("/srv/upstream"), tmp)
        self.assertEqual(calls[1][-3:], ("--", "/srv/upstream", COMMIT))

    def test_an_unvalidated_option_source_never_reaches_git(self):
        with mock.patch.object(PACK, "_git") as git, tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as caught:
                PACK._vendor_archive(entry("--upload-pack=touch /tmp/pwned"), tmp)
        git.assert_not_called()
        self.assertIn("neither an https URL", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

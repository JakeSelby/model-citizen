#!/usr/bin/env python3
"""Qualify the shipped Studio lifecycle and browser flow on this host."""
import argparse
import http.client
import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "harness"
REQUIRED_CASES = {
    "loopback-only-bind", "host-refusal", "token-refusal",
    "authenticated-ui-load", "clean-stop", "chrome-browser-flow",
}
BROWSER_SUITES = {
    "test_studio_browser.py": (
        "test_shell_routes_focuses_and_styles_under_the_production_csp",
    ),
    "test_studio_configure_browser.py": (
        "test_configure_debounces_and_retains_an_edit_during_inflight_save",
        "test_failed_save_keeps_loaded_identity_and_allows_explicit_retry",
        "test_redacted_malformed_reference_has_a_working_clear_action",
    ),
    "test_studio_module_editor_browser.py": (
        "test_secret_line_refuses_keyboard_save_and_reflows_at_320px",
        "test_lost_save_retries_canonically_and_creates_one_checkpoint",
        "test_source_conflict_preserves_the_buffer_and_writes_nothing",
        "test_late_preview_and_save_responses_cannot_update_a_switched_module",
        "test_late_preview_cannot_update_a_switched_draft",
    ),
}
# Each suite runs whole, under a budget proportional to its size: a fixed allowance for Studio
# and Chrome startup plus a per-case allowance about twice the 20 s a rendered case took on a
# loaded Mac (the module editor's 19 cases ran 383 s).
SUITE_BASE_SECONDS = 60
SUITE_CASE_SECONDS = 45
STARTUP_TIMEOUT = 45
# The lifecycle's own bounded steps outside startup: the clean-tree and HEAD reads (30 s each),
# five HTTP probes (3 s each), the non-loopback connect (0.5 s), the Chrome version read (10 s)
# and the stop with its confirming status (10 s each). The overhead adds slack for the host
# lookup, which has no bound of its own.
LIFECYCLE_BOUNDS = 30 + 30 + 5 * 3 + 0.5 + 10 + 10 + 10
LIFECYCLE_OVERHEAD = 150
# The browser the suites must use, so the recorded version is the one that ran.
CHROME_ENV = "HARNESS_STUDIO_CHROME"
# Set by the hosted workflow to the runner image's own Chrome before it installs current stable.
IMAGE_CHROME_ENV = "HARNESS_STUDIO_IMAGE_CHROME"
# Set by the same workflow to the SHA-256 of the Chrome installer it verified and installed.
INSTALLER_SHA_ENV = "HARNESS_STUDIO_CHROME_INSTALLER_SHA256"
SHA256 = re.compile(r"[0-9a-f]{64}")
# A directory of PID files naming every Chrome and detached Studio server a qualification starts.
# Detached servers leave the process group, so whoever kills a qualification also kills these.
TRACK_ENV = "HARNESS_STUDIO_TRACK_DIR"  # harness_core.studio.lifecycle.TRACK_ENV
CHROME_VERSION = re.compile(r"Google Chrome ([0-9]+)(?:\.[0-9]+){3}")
TEST_CASE = re.compile(r"^    def (test_[A-Za-z0-9_]+)\(", re.MULTILINE)


def suite_cases(pattern, root=ROOT):
    return len(TEST_CASE.findall((root / "tests" / pattern).read_text(encoding="utf-8")))


def suite_timeout(pattern, root=ROOT):
    return SUITE_BASE_SECONDS + SUITE_CASE_SECONDS * suite_cases(pattern, root)


def worst_case_seconds(root=ROOT):
    """The longest a qualification can take before one of its own bounds stops it."""
    return (STARTUP_TIMEOUT + LIFECYCLE_OVERHEAD
            + sum(suite_timeout(pattern, root) for pattern in BROWSER_SUITES))


def track(pid, directory=None):
    directory = directory or os.environ.get(TRACK_ENV)
    if directory:
        Path(directory, str(int(pid))).write_text("")


def untrack(pid, directory=None):
    directory = directory or os.environ.get(TRACK_ENV)
    if directory:
        try:
            Path(directory, str(int(pid))).unlink()
        except OSError:
            pass


def kill_tracked(directory):
    """Kill every tracked process with its group, and forget them all.

    A tracked process that leads a group is killed with the group, which outlives its leader; one
    that leads none has no group of its own number and is killed alone.
    """
    if not directory or not Path(directory).is_dir():
        return
    for entry in Path(directory).iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        untrack(pid, directory)


def command(*args, env=None, expected=0, timeout=30):
    result = subprocess.run(args, cwd=str(ROOT), env=env, capture_output=True,
                            text=True, check=False, timeout=timeout)
    if result.returncode != expected:
        raise AssertionError("command returned {}, expected {}: {}".format(
            result.returncode, expected, (result.stdout + result.stderr).strip()))
    return result


def supported_tuple(root=ROOT):
    platform_name = "macos" if platform.system().lower() == "darwin" else platform.system().lower()
    matrix = json.loads((root / "compatibility" / "studio.json").read_text())
    matches = [item for item in matrix.get("supported", [])
               if item.get("platform") == platform_name]
    if len(matches) != 1:
        raise AssertionError("Studio compatibility matrix has no unique tuple for this platform")
    support = matches[0]
    if support.get("browser") != "chrome" or support.get("version_policy") != "stable-channel":
        raise AssertionError("Studio qualification supports only the declared Chrome stable channel")
    if not isinstance(support.get("stable_major"), int) or support["stable_major"] < 1:
        raise AssertionError("Studio qualification requires a pinned stable Chrome major")
    return support


def chrome(root=ROOT):
    supported_tuple(root)
    for candidate in (
        shutil.which("google-chrome-stable"),
        shutil.which("google-chrome"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise AssertionError("Studio qualification requires the Google Chrome stable channel")


def non_loopback_address():
    candidates = []
    try:
        candidates.extend(item[4][0] for item in socket.getaddrinfo(
            socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM))
    except OSError:
        pass
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        candidates.append(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()
    for candidate in candidates:
        if candidate and not candidate.startswith("127.") and candidate != "0.0.0.0":
            return candidate
    raise AssertionError("no non-loopback IPv4 interface was available to test the bind")


def request(port, host, method, path, headers=None, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    supplied = {"Host": host}
    supplied.update(headers or {})
    try:
        connection.request(method, path, body=body, headers=supplied)
        response = connection.getresponse()
        return response.status, response.headers, response.read()
    finally:
        connection.close()


def run_suite(python, pattern, env, timeout=None):
    """Run one rendered suite, killing it and every browser and server it started on overrun.

    The suite leads its own process group, so its Chrome can be killed with it without killing
    this process. The group is tracked, as is each detached Studio server the suite starts (the
    server records itself), so a caller that kills this qualification reaches them all too.
    """
    timeout = suite_timeout(pattern) if timeout is None else timeout
    directory = env.get(TRACK_ENV)
    process = subprocess.Popen(
        [python, "-m", "unittest", "discover", "-s", "tests", "-p", pattern, "-v"],
        cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True)
    track(process.pid, directory)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tracked(directory)
        process.communicate()
        raise AssertionError("Studio browser suite {} exceeded {} seconds".format(pattern, timeout))
    finally:
        untrack(process.pid, directory)
    if process.returncode != 0:
        raise AssertionError("command returned {}, expected 0: {}".format(
            process.returncode, (stdout + stderr).strip()))
    return stdout + stderr


def observed_chrome(root, executable):
    """The version of the Chrome the suites will drive, refused unless it is the pinned major."""
    version = command(executable, "--version", timeout=10).stdout.strip()
    match = CHROME_VERSION.fullmatch(version)
    if match is None:
        raise AssertionError("Studio browser is not the declared Google Chrome stable channel")
    expected_major = supported_tuple(root)["stable_major"]
    observed_major = int(match.group(1))
    if observed_major > expected_major:
        raise AssertionError(
            "Studio Chrome pin is behind: stable is major {} but compatibility/studio.json "
            "pins {}; update compatibility/studio.json".format(observed_major, expected_major))
    if observed_major != expected_major:
        raise AssertionError("Studio browser is not pinned stable Chrome major {}".format(
            expected_major))
    return version


def browser_flow(root, python, executable):
    version = observed_chrome(root, executable)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env[CHROME_ENV] = executable
    outputs = []
    for pattern, expected_cases in BROWSER_SUITES.items():
        output = run_suite(python, pattern, env)
        count = re.search(r"Ran ([0-9]+) tests? in", output)
        if count is None or int(count.group(1)) < len(expected_cases):
            raise AssertionError("Studio browser qualification discovered no complete suite for "
                                 + pattern)
        missing = [name for name in expected_cases if name not in output]
        if missing:
            raise AssertionError("Studio browser qualification did not run: " + ", ".join(missing))
        outputs.append(output)
    combined = "\n".join(outputs)
    if "skipped=" in combined or "OK (skipped=" in combined:
        raise AssertionError("Studio browser qualification skipped a case")
    return version


def image_chrome():
    """The hosted image's own Chrome version, when the workflow replaced it; else None."""
    value = os.environ.get(IMAGE_CHROME_ENV, "").strip()
    if not value:
        return None
    if not CHROME_VERSION.fullmatch(value):
        raise AssertionError(IMAGE_CHROME_ENV + " is not a Google Chrome version")
    return value


def installer_sha256():
    """The digest of the Chrome installer the hosted workflow installed; else None."""
    value = os.environ.get(INSTALLER_SHA_ENV, "").strip()
    if not value:
        return None
    if not SHA256.fullmatch(value):
        raise AssertionError(INSTALLER_SHA_ENV + " is not a SHA-256 digest")
    return value


def launch_record(started, state_path):
    public = json.loads(started.stdout)
    record = json.loads(state_path.read_text())
    return public, record


def _candidate_pid(started, state_path):
    for raw in (started.stdout, state_path.read_text() if state_path.is_file() else ""):
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            continue
        pid = value.get("pid") if isinstance(value, dict) else None
        if isinstance(pid, int) and pid > 1:
            return pid
    return None


def cleanup(root, python, env, started, state_path):
    """Stop immediately after launch; a malformed record falls back to the launch PID."""
    try:
        command(python, str(root / "bin" / "harness"), "studio", "stop", "--json",
                env=env, timeout=10)
    except (AssertionError, OSError, subprocess.TimeoutExpired):
        pid = _candidate_pid(started, state_path)
        if pid is None:
            raise AssertionError("Studio launch could not be identified for cleanup")
        try:
            os.kill(pid, 15)
        except ProcessLookupError:
            pass
        for _attempt in range(100):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            import time
            time.sleep(0.02)
        else:
            os.kill(pid, 9)
    status = command(python, str(root / "bin" / "harness"), "studio", "status", "--json",
                     env=env, expected=1, timeout=10)
    if json.loads(status.stdout) != {"running": False} or state_path.exists():
        raise AssertionError("Studio did not stop cleanly")


def lifecycle(root=ROOT, python=sys.executable):
    if command("git", "-C", str(root), "status", "--porcelain").stdout.strip():
        raise ValueError("candidate checkout must be clean")
    with tempfile.TemporaryDirectory(prefix="studio-lifecycle-") as temporary:
        home = Path(os.path.realpath(temporary)) / "home"
        home.mkdir()
        env = dict(os.environ, HOME=str(home), HARNESS_HOME=str(home),
                   PYTHONDONTWRITEBYTECODE="1", HARNESS_STUDIO_STARTUP_TRACE="1")
        started = command(python, str(root / "bin" / "harness"), "studio", "--detach",
                          "--no-open", "--json", env=env, timeout=STARTUP_TIMEOUT)
        state_path = home / ".local/state/agent-harness/studio/instance.json"
        cases = []
        try:
            _public, record = launch_record(started, state_path)
            port, host = int(record["port"]), str(record["host"])
            outside = non_loopback_address()
            try:
                exposed = socket.create_connection((outside, port), timeout=0.5)
            except OSError:
                exposed = None
            if exposed is not None:
                exposed.close()
                raise AssertionError("Studio accepted a connection outside loopback")
            cases.append({"name": "loopback-only-bind", "status": "passed"})

            status, _headers, body = request(port, "example.invalid", "GET", "/")
            if status != 403 or json.loads(body) != {"error": "request_refused"}:
                raise AssertionError("foreign Host was not refused")
            cases.append({"name": "host-refusal", "status": "passed"})

            status, _headers, issued_body = request(
                port, host, "POST", "/__studio/control/bootstrap",
                {"Authorization": "Bearer " + record["control_credential"]})
            if status != 200:
                raise AssertionError("launcher could not issue a bootstrap token")
            issued = json.loads(issued_body)
            invalid = urllib.parse.urlencode({"token": "invalid"}).encode("ascii")
            status, _headers, body = request(
                port, host, "POST", "/__studio/bootstrap",
                {"Content-Type": "application/x-www-form-urlencoded",
                 "Content-Length": str(len(invalid))}, invalid)
            if status != 401 or json.loads(body) != {"error": "unauthorized"}:
                raise AssertionError("invalid bootstrap token was not refused")
            cases.append({"name": "token-refusal", "status": "passed"})

            form = urllib.parse.urlencode({"token": issued["token"]}).encode("ascii")
            status, headers, _body = request(
                port, host, "POST", "/__studio/bootstrap",
                {"Content-Type": "application/x-www-form-urlencoded",
                 "Content-Length": str(len(form))}, form)
            if status != 200 or not headers.get("Set-Cookie"):
                raise AssertionError("valid launcher bootstrap did not create a session")
            cookie = headers["Set-Cookie"].split(";", 1)[0]
            status, _headers, body = request(port, host, "GET", "/", {"Cookie": cookie})
            if status != 200 or not body.lstrip().lower().startswith(b"<!doctype html"):
                raise AssertionError("authenticated Studio UI did not load")
            cases.append({"name": "authenticated-ui-load", "status": "passed"})
        finally:
            cleanup(root, python, env, started, state_path)
        cases.append({"name": "clean-stop", "status": "passed"})
        return cases


def run(root=ROOT, python=sys.executable, browser_runner=browser_flow):
    image = image_chrome()
    installer = installer_sha256()
    inherited = os.environ.get(TRACK_ENV)
    with tempfile.TemporaryDirectory(prefix="studio-processes-") as own:
        directory = inherited or own
        os.environ[TRACK_ENV] = directory
        try:
            return _run(root, python, browser_runner, image, installer)
        finally:
            kill_tracked(directory)
            if inherited is None:
                os.environ.pop(TRACK_ENV, None)


def _run(root, python, browser_runner, image, installer):
    cases = lifecycle(root, python)
    support = supported_tuple(root)
    executable = chrome(root)
    browser_version = browser_runner(root, python, executable)
    cases.append({"name": "chrome-browser-flow", "status": "passed"})
    commit = command("git", "-C", str(root), "rev-parse", "HEAD").stdout.strip()
    browser = {"family": "chrome", "version": browser_version,
               "version_policy": support["version_policy"]}
    if image is not None:
        browser["image_version"] = image
    if installer is not None:
        browser["installer_sha256"] = installer
    return {
        "schema_version": 1,
        "candidate_commit": commit,
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python": platform.python_version(),
        },
        "browser": browser,
        "cases": cases,
    }


def evidence_errors(root, source_commit):
    """Validate one passing record per supported platform for this release."""
    errors = []
    version = (root / "VERSION").read_text().strip()
    try:
        matrix = json.loads((root / "compatibility" / "studio.json").read_text())
    except (OSError, ValueError) as error:
        return ["Studio compatibility matrix could not be read: " + str(error)]
    if matrix.get("schema_version") != 1 or not isinstance(matrix.get("supported"), list):
        return ["Studio compatibility matrix requires schema_version 1 and supported tuples"]
    for support in matrix["supported"]:
        platform_name = support.get("platform")
        template = support.get("release_evidence")
        if platform_name not in ("macos", "linux") or not isinstance(template, str):
            errors.append("Studio compatibility tuple is malformed")
            continue
        relative = template.format(platform=platform_name, version=version)
        path = root / relative
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            errors.append("Studio qualification evidence is missing or invalid: " + relative)
            continue
        observed = record.get("environment", {}).get("platform")
        expected = "darwin" if platform_name == "macos" else "linux"
        cases = record.get("cases") if isinstance(record.get("cases"), list) else []
        names = [item.get("name") for item in cases if isinstance(item, dict)]
        passing = {item.get("name") for item in cases if isinstance(item, dict)
                   and item.get("status") == "passed"}
        if record.get("schema_version") != 1:
            errors.append(relative + ": unsupported schema")
        if record.get("candidate_commit") != source_commit:
            errors.append(relative + ": candidate commit does not match qualification source")
        if observed != expected:
            errors.append(relative + ": platform does not match the supported tuple")
        if record.get("browser", {}).get("family") != support.get("browser"):
            errors.append(relative + ": browser does not match the supported tuple")
        if record.get("browser", {}).get("version_policy") != support.get("version_policy"):
            errors.append(relative + ": browser version policy does not match the supported tuple")
        browser_version = record.get("browser", {}).get("version", "")
        match = CHROME_VERSION.fullmatch(browser_version)
        if match is None or int(match.group(1)) != support.get("stable_major"):
            errors.append(relative + ": browser does not match the pinned stable Chrome major")
        image_version = record.get("browser", {}).get("image_version")
        if image_version is not None and (not isinstance(image_version, str)
                                          or CHROME_VERSION.fullmatch(image_version) is None):
            errors.append(relative + ": hosted image Chrome version is malformed")
        installer = record.get("browser", {}).get("installer_sha256")
        if installer is not None and (not isinstance(installer, str)
                                      or SHA256.fullmatch(installer) is None):
            errors.append(relative + ": Chrome installer digest is malformed")
        named = [name for name in names if isinstance(name, str)]
        unknown = sorted(set(named) - REQUIRED_CASES)
        duplicates = sorted(name for name in set(named) if named.count(name) > 1)
        refused = sorted((str(item.get("name", "<malformed>")) if isinstance(item, dict)
                          else "<malformed>") for item in cases
                         if not isinstance(item, dict) or item.get("status") != "passed")
        if len(names) != len(cases) or len(named) != len(names):
            errors.append(relative + ": malformed case record")
        if unknown:
            errors.append(relative + ": unknown cases: " + ", ".join(unknown))
        if duplicates:
            errors.append(relative + ": duplicate cases: " + ", ".join(duplicates))
        if refused:
            errors.append(relative + ": cases were not passed: " + ", ".join(refused))
        missing = sorted(REQUIRED_CASES - passing)
        if missing:
            errors.append(relative + ": cases did not pass: " + ", ".join(missing))
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    # A terminated qualification still runs its cleanup and kills what it tracked.
    previous = signal.signal(signal.SIGTERM, lambda *_args: sys.exit(143))
    try:
        rendered = json.dumps(run(), indent=2, sort_keys=True) + "\n"
    finally:
        signal.signal(signal.SIGTERM, previous)
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

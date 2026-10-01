#!/usr/bin/env python3
"""The observation-only hook path: one ledger row per hook event, and nothing else (AD-23).

Observation runs in every arm of a comparison, the bare one included, so it must never change what
the model or the user sees. This path therefore prints nothing, adds no context, returns no
decision and exits 0 on every input. Any failure, an unwritable ledger included, goes to a local
error log beside the ledger, and a failure to write that log is dropped.

A row carries the event's name, the runtime, the session id, the tool name where the event has
one, and the profile fingerprint. It never carries a prompt, a tool input, a tool result or any
other message body.

The file is standard library only and imports nothing from the harness, because a bare-arm install
copies it alone (`harness_core.observation.bare_install`). Run as a script it takes
`--runtime <name>` and `--profile <fingerprint>`; a bare install passes `--profile bare`, which is
the name the bare arm's rows carry, since it loads no profile to digest.

The benchmark runner sets explicit ledger, error and profile environment variables inside its
container. Ordinary sessions set none and continue to use the local state directory.
"""
import datetime
import importlib.util
import json
import os
import sys
from pathlib import Path

SCHEMA_KEY = "schema_version"
SCHEMA_VERSION = 1
FINGERPRINT_KEY = "profile_fingerprint"
LEDGER = "observation.jsonl"
ERRORS = "observation.errors.jsonl"
LEDGER_ENV = "HARNESS_OBSERVATION_LEDGER"
ERRORS_ENV = "HARNESS_OBSERVATION_ERRORS"
PROFILE_ENV = "HARNESS_OBSERVATION_PROFILE"


def state_dir(env=None):
    env = os.environ if env is None else env
    home = env.get("HARNESS_HOME") or env.get("HOME") or str(Path.home())
    return Path(home) / ".local" / "state" / "agent-harness"


def ledger_path(env=None):
    env = os.environ if env is None else env
    return Path(env[LEDGER_ENV]) if env.get(LEDGER_ENV) else state_dir(env) / LEDGER


def errors_path(env=None):
    env = os.environ if env is None else env
    return Path(env[ERRORS_ENV]) if env.get(ERRORS_ENV) else state_dir(env) / ERRORS


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def profile_fingerprint(profile=None):
    """The named profile, else the digest of the `posture.py` in this checkout, else None.

    None is the answer whenever the resolver is absent or fails: a guessed fingerprint would
    attribute a row to a profile that did not produce it.
    """
    if profile:
        return profile
    location = Path(os.path.realpath(__file__)).parents[2] / "policy" / "hooks" / "posture.py"
    try:
        spec = importlib.util.spec_from_file_location("harness_observer_posture", str(location))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.fingerprint()
    except Exception:
        return None


def row_for(payload, runtime, profile=None):
    """The ledger row for one event: identifiers only, never a body."""
    row = {"ts": now(), "runtime": runtime,
           "event": payload.get("hook_event_name") if isinstance(payload, dict) else None,
           "session_id": payload.get("session_id") if isinstance(payload, dict) else None}
    tool = payload.get("tool_name") if isinstance(payload, dict) else None
    if tool:
        row["tool_name"] = tool
    row[SCHEMA_KEY] = SCHEMA_VERSION
    row[FINGERPRINT_KEY] = profile_fingerprint(profile)
    return row


def append(row, target):
    """One line, one `write`, to a ledger only its owner can read."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(str(target.parent), 0o700)
    except OSError:
        pass
    fd = os.open(str(target), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(row, sort_keys=True) + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def record(payload, runtime, profile=None):
    append(row_for(payload, runtime, profile), ledger_path())


def note_error(runtime, error):
    try:
        append({"ts": now(), "runtime": runtime, "error": type(error).__name__,
                "detail": str(error)[:500]}, errors_path())
    except Exception:
        pass


def parse_args(argv):
    runtime, profile, index = None, None, 0
    while index < len(argv):
        if argv[index] == "--runtime" and index + 1 < len(argv):
            runtime, index = argv[index + 1], index + 2
        elif argv[index] == "--profile" and index + 1 < len(argv):
            profile, index = argv[index + 1], index + 2
        else:
            index += 1
    return runtime, profile


def main(runtime=None, argv=None, stdin=None):
    """Record the event on stdin. Returns 0 on every path, and never writes stdout or stderr."""
    try:
        named, profile = parse_args(sys.argv[1:] if argv is None else argv)
        runtime = runtime or named or "unknown"
        raw = (sys.stdin if stdin is None else stdin).read()
        record(json.loads(raw), runtime, profile or os.environ.get(PROFILE_ENV))
    except BaseException as error:  # noqa: BLE001 - observation fails open and silent, always.
        note_error(runtime or "unknown", error)
    return 0


if __name__ == "__main__":
    main()

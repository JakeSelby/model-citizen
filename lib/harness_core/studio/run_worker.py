"""Detached owner for one allowlisted Studio run."""
from __future__ import annotations

import argparse
import contextlib
import os
import subprocess
import sys
from pathlib import Path

from .runs import (RunError, RunSupervisor, SuiteCatalog, TERMINAL, process_identity,
                   terminate_owned_group, utc_now)


def execute(supervisor: RunSupervisor, run_id: str, admission_token: str) -> int:
    with supervisor.lock():
        record = supervisor._read(run_id)
        authenticated = record.get("admission_token") == admission_token
        if record["status"] == "admitted" and authenticated:
            record.pop("admission_token", None)
            record["status"] = "failed"
            record["completed_at"] = utc_now()
            record["reason"] = "run worker handoff was not committed"
            supervisor._write(record)
            supervisor._admit_locked()
            return 1
        if record["status"] == "cancel_requested" and authenticated:
            record.pop("admission_token", None)
            record["status"] = "cancelled"
            record["completed_at"] = utc_now()
            supervisor._write(record)
            supervisor._admit_locked()
            return 0
        if record["status"] != "starting" or not authenticated:
            return 0
        suite = SuiteCatalog.load(supervisor.catalog_path).get(record["suite_id"])
        rendered = suite.render(record["parameters"], record["target"]["kind"],
                                record["target"]["ref"])
        if rendered != record.get("argv") or suite.version != record.get("suite_version"):
            record["status"] = "failed"
            record.pop("admission_token", None)
            record["completed_at"] = utc_now()
            record["reason"] = "catalog changed before launch"
            supervisor._write(record)
            supervisor._admit_locked()
            return 1
        run_dir = supervisor._run_path(run_id).parent
        profile = supervisor.prepare_profile(run_id)
        environment = dict(os.environ)
        environment.update({
            "HOME": str(profile),
            "HARNESS_HOME": str(profile),
            "CLAUDE_CONFIG_DIR": str(profile / ".claude"),
            "CODEX_HOME": str(profile / ".codex"),
            "XDG_CONFIG_HOME": str(profile / ".config"),
            "XDG_DATA_HOME": str(profile / ".local" / "share"),
            "XDG_STATE_HOME": str(profile / ".local" / "state"),
            "XDG_CACHE_HOME": str(profile / ".cache"),
        })
        stdout = supervisor.open_run_output(run_id, "stdout.log")
        stderr = supervisor.open_run_output(run_id, "stderr.log")
        try:
            process = subprocess.Popen(rendered, cwd=str(run_dir), stdin=subprocess.DEVNULL,
                                       stdout=stdout, stderr=stderr, close_fds=True,
                                       start_new_session=True, env=environment)
        except OSError as exc:
            stdout.close()
            stderr.close()
            record["status"] = "failed"
            record.pop("admission_token", None)
            record["completed_at"] = utc_now()
            record["reason"] = "suite process could not start"
            supervisor._write(record)
            supervisor._admit_locked()
            return 1
        record["status"] = "running"
        record.pop("admission_token", None)
        record["command_pid"] = process.pid
        record["command_identity"] = process_identity(process.pid)
        record["started_at"] = utc_now()
        if record["command_identity"] is None:
            stopped = terminate_owned_group(process.pid, process=process)
            stdout.close()
            stderr.close()
            record.pop("command_pid", None)
            record.pop("command_identity", None)
            record["status"] = "failed" if stopped else "orphaned"
            record["completed_at"] = utc_now()
            record["reason"] = "suite process identity could not be established"
            if not stopped:
                record["capacity_reserved"] = True
            supervisor._write(record)
            if stopped:
                supervisor._admit_locked()
            return 1
        try:
            supervisor._write(record)
        except (OSError, RunError):
            terminate_owned_group(process.pid, process=process)
            stdout.close()
            stderr.close()
            raise
        timeout = record["timeout_seconds"]
    timed_out = False
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        returncode = -9
    finally:
        group_stopped = terminate_owned_group(process.pid, process=process)
        if process.returncode is None:
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=0.2)
        if process.returncode is not None:
            returncode = process.returncode
        stdout.close()
        stderr.close()
    with supervisor.lock():
        record = supervisor._read(run_id)
        if record["status"] not in TERMINAL:
            if not group_stopped:
                record["status"] = "orphaned"
                record["capacity_reserved"] = True
                record["reason"] = "owned process group could not be stopped"
            elif record["status"] == "cancel_requested":
                record["status"] = "cancelled"
            elif timed_out:
                record["status"] = "timed_out"
            else:
                record["status"] = "succeeded" if returncode == 0 else "failed"
            record["returncode"] = returncode
            record["completed_at"] = utc_now()
            supervisor._write(record)
        supervisor._recover_locked()
        if group_stopped:
            supervisor._admit_locked()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--max-running", type=int, required=True)
    parser.add_argument("--admission-token", required=True)
    args = parser.parse_args(argv)
    supervisor = None
    try:
        supervisor = RunSupervisor(Path(args.state_root), Path(args.catalog), args.max_running)
        return execute(supervisor, args.run_id, args.admission_token)
    except (OSError, RunError) as exc:
        if supervisor is not None:
            with contextlib.suppress(OSError, RunError):
                detail = str(exc) if isinstance(exc, RunError) else "I/O failure"
                supervisor.fail(args.run_id, "run worker failed: " + detail)
        return 2


if __name__ == "__main__":
    sys.exit(main())

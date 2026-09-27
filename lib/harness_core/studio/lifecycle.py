# SPDX-License-Identifier: MIT
"""Start, discover and stop the one Studio instance."""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

from harness_core import workers

from . import server
from .state import PROTOCOL_VERSION, SCHEMA_VERSION, StateError, Store

START_TIMEOUT = 5.0
STOP_TIMEOUT = 2.0


class InstanceError(RuntimeError):
    """A recorded Studio instance cannot be controlled safely."""


def state_root(home: Path) -> Path:
    return Path(home) / ".local" / "state" / "agent-harness" / "studio"


def _request(record: Dict[str, object], path: str, method: str = "GET") -> Dict[str, object]:
    request = urllib.request.Request(str(record["url"]).rstrip("/") + path, method=method,
                                     headers={"Authorization": "Bearer " + str(record["control_credential"])})
    try:
        with urllib.request.urlopen(request, timeout=0.5) as response:
            value = json.loads(response.read().decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("control response is not an object")
            return value
    except (OSError, UnicodeError, ValueError, urllib.error.HTTPError) as exc:
        raise InstanceError("Studio instance did not complete its control handshake") from exc


def _matches(record: Dict[str, object], health: Dict[str, object]) -> bool:
    fields = ("schema_version", "protocol_version", "pid", "pid_start", "port")
    return all(health.get(name) == record.get(name) for name in fields)


def current(root: Path) -> Optional[Dict[str, object]]:
    try:
        with Store(root) as store:
            record = store.read()
            if record is None:
                return None
            running = workers.running(record["pid"], record["pid_start"])
            if running is not True:
                lock = store.acquire()
                if lock is not None:
                    try:
                        store.remove()
                    finally:
                        os.close(lock)
                    return None
                raise InstanceError("Studio state is stale but its instance lock is still held")
            try:
                health = _request(record, server.CONTROL_HEALTH)
            except InstanceError:
                lock = store.acquire()
                if lock is not None:
                    try:
                        store.remove()
                    finally:
                        os.close(lock)
                    return None
                raise
            if not _matches(record, health):
                raise InstanceError("Studio control handshake does not match its recorded process")
            return record
    except (OSError, StateError) as exc:
        raise InstanceError(str(exc)) from exc


def serve(static_root: Path, root: Path, requested_port: int, ready=None) -> None:
    try:
        with Store(root) as store:
            server.run(static_root, store, requested_port, ready)
    except (OSError, StateError, RuntimeError) as exc:
        raise InstanceError(str(exc)) from exc


def launch_detached(command: List[str], root: Path, requested_port: int) -> Dict[str, object]:
    argv = list(command) + ["studio", "--_serve", "--no-open", "--port", str(requested_port)]
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
    deadline = time.monotonic() + START_TIMEOUT
    last_error = None
    while time.monotonic() < deadline:
        try:
            record = current(root)
            if record is not None:
                won = record["pid"] == process.pid
                if not won and process.poll() is None:
                    try:
                        process.wait(timeout=0.25)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        try:
                            process.wait(timeout=0.25)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                fallback = won and requested_port != 0 and record["port"] != requested_port
                return server.public_result(record, requested_port, fallback, reused=not won)
        except InstanceError as exc:
            last_error = exc
        if process.poll() is not None:
            # A racing child can lose the instance lock just before the winner publishes state.
            # Keep polling the validated control handshake until the normal startup deadline.
            time.sleep(0.05)
            continue
        time.sleep(0.05)
    try:
        process.terminate()
    except OSError:
        pass
    raise InstanceError("detached Studio did not become ready" +
                        ((": " + str(last_error)) if last_error else ""))


def stop(root: Path) -> Optional[Dict[str, object]]:
    record = current(root)
    if record is None:
        return None
    _request(record, server.CONTROL_STOP, method="POST")
    deadline = time.monotonic() + STOP_TIMEOUT
    while time.monotonic() < deadline:
        if workers.running(record["pid"], record["pid_start"]) is False:
            break
        time.sleep(0.05)
    try:
        with Store(root) as store:
            state = store.read()
            if state is not None and state.get("instance_epoch") == record.get("instance_epoch"):
                lock = store.acquire()
                if lock is None:
                    raise InstanceError("Studio did not stop within two seconds")
                try:
                    store.remove()
                finally:
                    os.close(lock)
    except (OSError, StateError) as exc:
        raise InstanceError(str(exc)) from exc
    return {"stopped": True, "pid": record["pid"]}

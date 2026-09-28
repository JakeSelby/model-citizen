# SPDX-License-Identifier: MIT
"""Start, discover and stop the one Studio instance."""
from __future__ import annotations

import json
import http.client
import errno
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

from harness_core import workers

from . import auth
from . import server
from .state import PROTOCOL_VERSION, SCHEMA_VERSION, StateError, Store

# Hosted macOS runners can take several seconds to schedule the detached interpreter.
START_TIMEOUT = 30.0
STOP_TIMEOUT = 2.0
CHILD_SESSION_ENV = "HARNESS_STUDIO_CHILD_SESSION"


class InstanceError(RuntimeError):
    """A recorded Studio instance cannot be controlled safely."""


def state_root(home: Path) -> Path:
    return Path(home) / ".local" / "state" / "agent-harness" / "studio"


def _request(record: Dict[str, object], path: str, method: str = "GET") -> Dict[str, object]:
    connection = http.client.HTTPConnection("127.0.0.1", int(record["port"]), timeout=0.5)
    try:
        connection.request(method, path, headers={
            "Authorization": "Bearer " + str(record["control_credential"]),
            "Host": str(record["host"]),
        })
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("control response was refused")
        value = json.loads(response.read().decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("control response is not an object")
        return value
    except (OSError, UnicodeError, ValueError) as exc:
        raise InstanceError("Studio instance did not complete its control handshake") from exc
    finally:
        connection.close()


def _matches(record: Dict[str, object], health: Dict[str, object]) -> bool:
    fields = ("schema_version", "protocol_version", "pid", "pid_start", "port")
    return all(health.get(name) == record.get(name) for name in fields)


def _process_running(record: Dict[str, object]):
    if sys.platform != "darwin":
        return workers.running(record["pid"], record["pid_start"])
    try:
        os.kill(int(record["pid"]), 0)
        return True
    except ProcessLookupError:
        return False
    except (OSError, TypeError, ValueError):
        return None


def _close_inherited_fds() -> None:
    """Close the descriptors that survived exec without scanning the process limit."""
    for name in os.listdir("/dev/fd"):
        try:
            descriptor = int(name)
        except ValueError:
            continue
        if descriptor <= 2:
            continue
        try:
            os.close(descriptor)
        except OSError as exc:
            # The directory enumeration can briefly expose its own already-closed handle.
            if exc.errno != errno.EBADF:
                raise


def _launch_ready(root: Path) -> Optional[Dict[str, object]]:
    """Read a newly published instance through its authenticated handshake."""
    try:
        with Store(root) as store:
            record = store.read()
        if record is None:
            return None
        health = _request(record, server.CONTROL_HEALTH)
        if not _matches(record, health):
            raise InstanceError("Studio control handshake does not match its recorded process")
        return record
    except (OSError, StateError) as exc:
        raise InstanceError(str(exc)) from exc


def _cleanup_failed_launch(process: subprocess.Popen, root: Path) -> None:
    if process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        except OSError:
            pass
    try:
        with Store(root) as store:
            record = store.read()
            if record is None or record.get("pid") != process.pid:
                return
            lock = store.acquire()
            if lock is not None:
                try:
                    store.remove()
                finally:
                    os.close(lock)
    except (OSError, StateError):
        pass


def current(root: Path) -> Optional[Dict[str, object]]:
    try:
        with Store(root) as store:
            record = store.read()
            if record is None:
                return None
            running = _process_running(record)
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


def serve(static_root: Path, root: Path, requested_port: int, ready=None,
          browser: bool = False) -> None:
    try:
        if os.environ.pop(CHILD_SESSION_ENV, None) == "1":
            os.setsid()
            _close_inherited_fds()
        with Store(root) as store:
            server.run(static_root, store, requested_port, ready, browser=browser)
    except (OSError, StateError, RuntimeError) as exc:
        raise InstanceError(str(exc)) from exc


def launch_detached(command: List[str], root: Path, requested_port: int) -> Dict[str, object]:
    argv = list(command) + ["studio", "--_serve", "--no-open", "--port", str(requested_port)]
    start_new_session = True
    close_fds = True
    child_env = None
    if sys.platform == "darwin":
        # Avoid Python's fork-before-exec path on macOS. The exec'd child creates its own
        # session before opening Studio state or sockets.
        argv = ["/usr/bin/nohup"] + argv
        start_new_session = False
        close_fds = False
        child_env = dict(os.environ, **{CHILD_SESSION_ENV: "1"})
    startup_errors = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=startup_errors, close_fds=close_fds, env=child_env,
                                   start_new_session=start_new_session)
    except BaseException:
        startup_errors.close()
        raise
    deadline = time.monotonic() + START_TIMEOUT
    last_error = None
    while time.monotonic() < deadline:
        try:
            # The server already records and returns its process-start token. Avoid a second
            # platform liveness probe while the just-spawned process is publishing readiness.
            record = _launch_ready(root)
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
                result = server.public_result(record, requested_port, fallback, reused=not won)
                startup_errors.close()
                return result
        except InstanceError as exc:
            last_error = exc
        if process.poll() is not None:
            # A racing child can lose the instance lock just before the winner publishes state.
            # Keep polling the validated control handshake until the normal startup deadline.
            time.sleep(0.05)
            continue
        time.sleep(0.05)
    _cleanup_failed_launch(process, root)
    startup_errors.seek(0)
    child_error = startup_errors.read(4096).strip()
    startup_errors.close()
    raise InstanceError("detached Studio did not become ready" +
                        ((": " + str(last_error)) if last_error else "") +
                        (("; child: " + child_error) if child_error else ""))


def prepare_browser(root: Path) -> Path:
    record = current(root)
    if record is None:
        raise InstanceError("Studio is not running")
    bootstrap = _request(record, server.CONTROL_BOOTSTRAP, method="POST")
    token = bootstrap.get("token")
    form_name = bootstrap.get("form_name")
    if not isinstance(token, str) or not isinstance(form_name, str):
        raise InstanceError("Studio instance did not complete its control handshake")
    return auth.write_launcher_form(root, form_name,
                                    str(record["url"]).rstrip("/") + server.BOOTSTRAP, token)


def stop(root: Path) -> Optional[Dict[str, object]]:
    record = current(root)
    if record is None:
        return None
    _request(record, server.CONTROL_STOP, method="POST")
    deadline = time.monotonic() + STOP_TIMEOUT
    while time.monotonic() < deadline:
        if _process_running(record) is False:
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

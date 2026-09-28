# SPDX-License-Identifier: MIT
"""Descriptor-anchored state for the one local Studio instance."""
from __future__ import annotations

import errno
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover - Studio currently targets POSIX hosts
    fcntl = None

SCHEMA_VERSION = 2
PROTOCOL_VERSION = 2
STATE_NAME = "instance.json"
LOCK_NAME = "instance.lock"
UI_PREFERENCES_NAME = "ui-preferences.json"
UI_COLOR_SCHEMES = frozenset(("auto", "light", "dark"))


class StateError(ValueError):
    """The instance store cannot be trusted or is incompatible."""


class Store:
    """Open owned state without following links or trusting permissions.

    The checks isolate Studio from other local users and accidental permission drift. Processes
    already running as the same user remain inside the documented trust boundary.
    """

    def __init__(self, path: Path):
        self.path = Path(os.path.abspath(str(path)))
        self.fd: Optional[int] = None

    def __enter__(self):
        if not self.path.is_absolute():
            raise StateError("Studio state path must be absolute")
        fd = os.open(os.sep, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in self.path.parts[1:]:
                if component in ("", ".", ".."):
                    raise StateError("Studio state path has an unsafe component")
                try:
                    os.mkdir(component, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                  dir_fd=fd)
                os.close(fd)
                fd = next_fd
            self.fd = fd
            try:
                self._secure_directory()
            except BaseException:
                self.fd = None
                raise
            fd = -1
            return self
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise StateError("Studio state path contains a symlink or non-directory") from exc
            raise
        finally:
            if fd >= 0:
                os.close(fd)

    def _secure_directory(self) -> None:
        assert self.fd is not None
        info = os.fstat(self.fd)
        if not stat.S_ISDIR(info.st_mode):
            raise StateError("Studio state path is not a directory")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise StateError("Studio state directory is owned by another user")
        if stat.S_IMODE(info.st_mode) != 0o700:
            os.fchmod(self.fd, 0o700)

    def __exit__(self, *_):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def acquire(self, blocking: bool = False) -> Optional[int]:
        if fcntl is None:
            raise StateError("Studio process control requires POSIX file locks")
        assert self.fd is not None
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
        try:
            handle = os.open(LOCK_NAME, flags, 0o600, dir_fd=self.fd)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.EISDIR, errno.ENXIO):
                raise StateError("Studio instance lock is not a safe regular file") from exc
            raise
        try:
            info = os.fstat(handle)
            if not stat.S_ISREG(info.st_mode):
                raise StateError("Studio instance lock is not a regular file")
            if hasattr(os, "getuid") and info.st_uid != os.getuid():
                raise StateError("Studio instance lock is owned by another user")
            if stat.S_IMODE(info.st_mode) != 0o600:
                os.fchmod(handle, 0o600)
        except BaseException:
            os.close(handle)
            raise
        operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(handle, operation)
        except BlockingIOError:
            os.close(handle)
            return None
        return handle

    def read(self) -> Optional[Dict[str, Any]]:
        assert self.fd is not None
        try:
            handle = os.open(STATE_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(handle)
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                raise StateError("Studio state file must be a mode-0600 regular file")
            if hasattr(os, "getuid") and info.st_uid != os.getuid():
                raise StateError("Studio state file is owned by another user")
            with os.fdopen(handle, encoding="utf-8") as stream:
                handle = -1
                try:
                    record = json.load(stream)
                except (UnicodeError, ValueError) as exc:
                    raise StateError("Studio state file is not valid JSON") from exc
        finally:
            if handle >= 0:
                os.close(handle)
        return validate(record)

    def write(self, record: Dict[str, Any]) -> None:
        assert self.fd is not None
        record = validate(record)
        temporary = ".instance-" + secrets.token_hex(12)
        handle = -1
        try:
            handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self.fd)
            os.fchmod(handle, 0o600)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                handle = -1
                json.dump(record, stream, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, STATE_NAME, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            if handle >= 0:
                os.close(handle)
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass

    def remove(self) -> None:
        assert self.fd is not None
        try:
            os.unlink(STATE_NAME, dir_fd=self.fd)
            os.fsync(self.fd)
        except FileNotFoundError:
            pass

    def write_private(self, name: str, data: bytes) -> None:
        """Create one private launcher artifact relative to the retained state descriptor."""
        assert self.fd is not None
        if not name.startswith(".bootstrap-") or not name.endswith(".html") or "/" in name:
            raise StateError("Studio private state name is invalid")
        handle = -1
        try:
            handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self.fd)
            os.fchmod(handle, 0o600)
            with os.fdopen(handle, "wb") as stream:
                handle = -1
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(self.fd)
        finally:
            if handle >= 0:
                os.close(handle)

    def remove_private(self, name: str) -> None:
        assert self.fd is not None
        if not name.startswith(".bootstrap-") or not name.endswith(".html") or "/" in name:
            raise StateError("Studio private state name is invalid")
        try:
            os.unlink(name, dir_fd=self.fd)
            os.fsync(self.fd)
        except FileNotFoundError:
            pass

    def read_ui_preferences(self) -> Dict[str, str]:
        """Read the small origin-independent Studio chrome preference record."""
        assert self.fd is not None
        try:
            handle = os.open(UI_PREFERENCES_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        except FileNotFoundError:
            return {"color_scheme": "auto"}
        try:
            info = os.fstat(handle)
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_size > 1024):
                raise StateError("Studio UI preferences must be a bounded mode-0600 regular file")
            if hasattr(os, "getuid") and info.st_uid != os.getuid():
                raise StateError("Studio UI preferences are owned by another user")
            with os.fdopen(handle, encoding="utf-8") as stream:
                handle = -1
                try:
                    value = json.load(stream)
                except (UnicodeError, ValueError) as exc:
                    raise StateError("Studio UI preferences are not valid JSON") from exc
        finally:
            if handle >= 0:
                os.close(handle)
        if (not isinstance(value, dict) or set(value) != {"color_scheme"}
                or value["color_scheme"] not in UI_COLOR_SCHEMES):
            raise StateError("Studio UI preferences are invalid")
        return value

    def write_ui_preferences(self, value: Dict[str, str]) -> None:
        """Atomically persist Studio chrome preferences across random-host restarts."""
        assert self.fd is not None
        if (not isinstance(value, dict) or set(value) != {"color_scheme"}
                or value["color_scheme"] not in UI_COLOR_SCHEMES):
            raise StateError("Studio UI preferences are invalid")
        temporary = ".ui-preferences-" + secrets.token_hex(12)
        handle = -1
        try:
            handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self.fd)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                handle = -1
                json.dump(value, stream, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, UI_PREFERENCES_NAME, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            if handle >= 0:
                os.close(handle)
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass


def validate(record: Any) -> Dict[str, Any]:
    if not isinstance(record, dict):
        raise StateError("Studio state must be a JSON object")
    if record.get("schema_version") != SCHEMA_VERSION:
        raise StateError("unsupported Studio state schema")
    if record.get("protocol_version") != PROTOCOL_VERSION:
        raise StateError("unsupported Studio control protocol")
    for name in ("pid", "port"):
        value = record.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise StateError("Studio state has an invalid " + name)
    if record["port"] > 65535:
        raise StateError("Studio state has an invalid port")
    for name in ("pid_start", "control_credential", "host", "instance_epoch", "url"):
        value = record.get(name)
        if not isinstance(value, str) or not value:
            raise StateError("Studio state has an invalid " + name)
    host = record["host"]
    suffix = ".localhost:%d" % record["port"]
    nonce = host[:-len(suffix)] if host.endswith(suffix) else ""
    if len(nonce) != 32 or any(character not in "0123456789abcdef" for character in nonce):
        raise StateError("Studio state has an invalid host")
    expected = "http://%s/" % host
    if record["url"] != expected:
        raise StateError("Studio state URL does not match its loopback port")
    return record

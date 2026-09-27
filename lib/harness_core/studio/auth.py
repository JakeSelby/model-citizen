# SPDX-License-Identifier: MIT
"""Launcher authentication and descriptor-anchored file access for Studio."""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import os
import re
import secrets
import stat
import threading
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Mapping, Optional, Tuple

from .state import Store

SESSION_COOKIE = "studio_session"
MAX_FORM_BYTES = 4096
MAX_JSON_BYTES = 1024 * 1024
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'; object-src 'none'")
SECURITY_HEADERS = (
    ("Content-Security-Policy", CSP),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
)
SECRET_KEYS = frozenset(("api_key", "authorization", "credential", "passphrase", "password",
                         "private_key", "secret", "token"))
SECRET_KEY_COMPACT = frozenset(("apikey", "accesstoken", "authorization", "clientsecret",
                                "credential", "passphrase", "password", "privatekey",
                                "refreshtoken", "secret", "token"))
REFERENCE_SOURCES = frozenset(("environment", "keychain", "profile", "secret-store"))
KNOWN_ROOT_KINDS = ("checkout", "config", "draft", "primitive", "state", "static")
REFERENCE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9._:/-]{0,127}$")
ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


class SecurityError(ValueError):
    """A Studio request crossed a security boundary."""


@dataclass(frozen=True)
class Session:
    cookie: str
    csrf: str


class Sessions:
    """Per-process browser bootstrap and session state."""

    def __init__(self, host: str):
        self.host = host
        self.origin = "http://" + host
        self._lock = threading.Lock()
        self._bootstraps: Dict[str, str] = {}
        self._sessions: Dict[str, str] = {}

    def issue(self) -> Tuple[str, str]:
        token = secrets.token_urlsafe(32)
        form_name = ".bootstrap-%s.html" % secrets.token_hex(12)
        with self._lock:
            self._bootstraps[token] = form_name
        return token, form_name

    def consume(self, token: str) -> Optional[Tuple[Session, str]]:
        with self._lock:
            form_name = self._bootstraps.pop(token, None)
            if form_name is None:
                return None
            session = Session(secrets.token_urlsafe(32), secrets.token_urlsafe(32))
            self._sessions[session.cookie] = session.csrf
            return session, form_name

    def pending_forms(self) -> Tuple[str, ...]:
        with self._lock:
            return tuple(self._bootstraps.values())

    def authenticate(self, cookie_header: Optional[str]) -> Optional[Session]:
        if not cookie_header or len(cookie_header) > 4096:
            return None
        try:
            parsed = SimpleCookie()
            parsed.load(cookie_header)
            morsel = parsed.get(SESSION_COOKIE)
        except Exception:
            return None
        if morsel is None:
            return None
        supplied = morsel.value
        with self._lock:
            for cookie, csrf in self._sessions.items():
                if hmac.compare_digest(cookie, supplied):
                    return Session(cookie, csrf)
        return None

    @staticmethod
    def csrf_matches(session: Session, supplied: Optional[str]) -> bool:
        return bool(supplied) and hmac.compare_digest(session.csrf, str(supplied))


def launcher_document(action: str, token: str) -> bytes:
    """Return a local one-shot POST form; the token never enters an HTTP URL."""
    script = "document.getElementById('studio-bootstrap').submit();"
    digest = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode("ascii")
    policy = "default-src 'none'; script-src 'sha256-%s'; form-action %s; base-uri 'none'" % (
        digest, action)
    document = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="%s">
<title>Opening Model Citizen Studio</title></head><body>
<form id="studio-bootstrap" method="post" action="%s">
<input type="hidden" name="token" value="%s"><button type="submit">Open Studio</button></form>
<script>%s</script></body></html>
""" % (html.escape(policy, quote=True), html.escape(action, quote=True),
       html.escape(token, quote=True), script)
    return document.encode("utf-8")


def write_launcher_form(root: Path, name: str, action: str, token: str) -> Path:
    if not name.startswith(".bootstrap-") or not name.endswith(".html") or "/" in name:
        raise SecurityError("launcher form name refused")
    with Store(root) as store:
        store.write_private(name, launcher_document(action, token))
    return Path(root) / name


@dataclass(frozen=True)
class SecretReference:
    source: str
    name: str

    def public(self) -> Dict[str, str]:
        if not isinstance(self.source, str) or not isinstance(self.name, str):
            raise SecurityError("secret reference refused")
        pattern = ENVIRONMENT_NAME if self.source == "environment" else REFERENCE_NAME
        if self.source not in REFERENCE_SOURCES or pattern.fullmatch(self.name) is None:
            raise SecurityError("secret reference refused")
        return {"source": self.source, "name": self.name}


def _normalized_key(key: str) -> str:
    value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", str(key))
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()


def _public_reference(value: Any) -> Optional[Dict[str, str]]:
    if not isinstance(value, Mapping) or set(value) != {"source", "name"}:
        return None
    if not isinstance(value["source"], str) or not isinstance(value["name"], str):
        return None
    try:
        return SecretReference(value["source"], value["name"]).public()
    except SecurityError:
        return None


def public_data(value: Any, key: str = "") -> Any:
    """Serialize configuration without exposing secret values.

    Reference provider and name are displayable; the value resolved through that reference is not.
    """
    if isinstance(value, SecretReference):
        try:
            return value.public()
        except SecurityError:
            return {"configured": True}
    normalized = _normalized_key(key)
    compact = normalized.replace("_", "")
    if (normalized in SECRET_KEYS or compact in SECRET_KEY_COMPACT
            or normalized.endswith(("_secret", "_token", "_password", "_key"))):
        reference = _public_reference(value)
        if reference is not None:
            return reference
        return {"configured": value not in (None, "", False)}
    if isinstance(value, Mapping):
        return {str(name): public_data(item, str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [public_data(item) for item in value]
    return value


class KnownRoots:
    """Retain no-follow descriptors for the roots file APIs may name."""

    def __init__(self, roots: Mapping[str, Path]):
        self._fds: Dict[str, int] = {}
        try:
            for name, path in roots.items():
                label = name.split(":")
                kind = label[0]
                if (kind not in KNOWN_ROOT_KINDS or len(label) > 2
                        or any(not part or not part.replace("-", "").replace("_", "").isalnum()
                               for part in label)):
                    raise SecurityError("file root refused")
                self._fds[name] = self._open_root(Path(path))
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _open_root(path: Path) -> int:
        absolute = Path(os.path.abspath(str(path)))
        if not absolute.is_absolute():
            raise SecurityError("file root refused")
        handle = os.open(os.sep, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in absolute.parts[1:]:
                if component in ("", ".", ".."):
                    raise SecurityError("file root refused")
                next_handle = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                      dir_fd=handle)
                os.close(handle)
                handle = next_handle
            return handle
        except BaseException:
            os.close(handle)
            raise SecurityError("file root refused") from None

    @property
    def names(self) -> Tuple[str, ...]:
        return tuple(sorted(self._fds))

    @staticmethod
    def _parts(relative: str) -> Tuple[str, ...]:
        path = PurePosixPath(relative)
        if (path.is_absolute() or not path.parts or "\\" in relative or str(path) != relative
                or any(part in ("", ".", "..") for part in path.parts)):
            raise SecurityError("file request refused")
        return tuple(path.parts)

    def _parent(self, root: str, relative: str) -> Tuple[int, str]:
        if root not in self._fds:
            raise SecurityError("file request refused")
        parts = self._parts(relative)
        directory = os.dup(self._fds[root])
        try:
            for component in parts[:-1]:
                next_directory = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                         dir_fd=directory)
                os.close(directory)
                directory = next_directory
            return directory, parts[-1]
        except OSError:
            os.close(directory)
            raise SecurityError("file request refused") from None

    def read_bytes(self, root: str, relative: str, limit: int = MAX_JSON_BYTES) -> bytes:
        if limit < 0:
            raise SecurityError("file request refused")
        directory, name = self._parent(root, relative)
        handle = -1
        try:
            handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
            info = os.fstat(handle)
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
                raise SecurityError("file request refused")
            chunks = []
            remaining = limit + 1
            while remaining:
                chunk = os.read(handle, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > limit:
                raise SecurityError("file request refused")
            return data
        except (OSError, ValueError):
            raise SecurityError("file request refused") from None
        finally:
            if handle >= 0:
                os.close(handle)
            os.close(directory)

    def replace_bytes(self, root: str, relative: str, data: bytes, mode: int = 0o600) -> None:
        if not isinstance(data, bytes) or mode & ~0o777:
            raise SecurityError("file request refused")
        directory, name = self._parent(root, relative)
        temporary = ".studio-write-" + secrets.token_hex(12)
        handle = -1
        try:
            try:
                current = os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                current = None
            if current is not None and not stat.S_ISREG(current.st_mode):
                raise SecurityError("file request refused")
            handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             mode, dir_fd=directory)
            os.fchmod(handle, mode)
            with os.fdopen(handle, "wb") as stream:
                handle = -1
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
                written = os.fstat(stream.fileno())
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            installed = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if ((installed.st_dev, installed.st_ino) != (written.st_dev, written.st_ino)
                    or not stat.S_ISREG(installed.st_mode)):
                raise SecurityError("file request refused")
            os.fsync(directory)
        except (OSError, ValueError):
            raise SecurityError("file request refused") from None
        finally:
            if handle >= 0:
                os.close(handle)
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
            os.close(directory)

    def delete(self, root: str, relative: str) -> None:
        directory, name = self._parent(root, relative)
        try:
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(current.st_mode):
                raise SecurityError("file request refused")
            os.unlink(name, dir_fd=directory)
            os.fsync(directory)
        except (OSError, ValueError):
            raise SecurityError("file request refused") from None
        finally:
            os.close(directory)

    def close(self) -> None:
        for handle in self._fds.values():
            os.close(handle)
        self._fds.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

# SPDX-License-Identifier: MIT
"""The Model Citizen helper app that asks for presence, so the dialog names Model Citizen.

macOS names the calling program, and shows its icon, in the Touch ID or login-password dialog;
asked through `/usr/bin/osascript` it reads "osascript". `presence_app/` holds the source of a
tiny app bundle named Model Citizen whose only work is to call LocalAuthentication
(`evaluatePolicy(.deviceOwnerAuthentication, localizedReason:)`) and relay its answer. No binary
is checked in: `build` compiles it with `swiftc`, draws its icon, ad-hoc signs it
(`codesign -s -`) into the owner's state directory, under a directory named by the source's
digest so checkouts with different source keep their own, and records its code-signing hash.

`ready` verifies the bundle (`codesign --verify --strict`, and its hash against the record) under
a shared file lock that it holds while the caller runs the helper; a build or rebuild, after a
mismatch, a changed source or a missing record, takes the lock exclusively, so no check runs a
helper another process is replacing. A failed build is remembered with the toolchain's
fingerprint and not retried for a day, or until the toolchain or the source changes. Where the
developer tools are absent or a build fails, `ready` yields None and `presence.check` asks through
`osascript`, so the check is never lost.

The helper directory comes from the account's home in the password database, not `HOME`, and the
tools run with a fixed environment. Neither closes the same-user limit: code running as this user
can still steer the compile (the `swiftc` shim's own caches are the user's files) or replace the
helper and its record together, and the result is then trusted. Such a helper lives outside git,
so each confirmation row names the program that answered and the helper's signature hash
(`presence.record`); `docs/runtime-controls.md` states the limit.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import pwd
import shutil
import stat
import struct
import subprocess
import tempfile
import time
import zlib
from pathlib import Path
from typing import Iterator, NamedTuple, Optional

SOURCE = Path(__file__).resolve().parent / "presence_app"
SOURCES = ("main.swift", "Info.plist")
APP = "Model Citizen.app"
EXECUTABLE = "model-citizen-presence"
ICON = "ModelCitizen.icns"
RECORD = "helper.json"
FAILED = "build-failed.json"
LOCK = ".lock"
CODESIGN = "/usr/bin/codesign"
SWIFTC = "/usr/bin/swiftc"
XCODE_SELECT = "/usr/bin/xcode-select"
# The icon's drawing is part of what the record covers: change it and the helper rebuilds.
ICON_VERSION = "1"
COMPILE_TIMEOUT = 120
RETRY_AFTER = 24 * 60 * 60


class Helper(NamedTuple):
    path: Path
    cdhash: str


def root() -> Path:
    """Where helpers live: the account's own state directory, read from the password database
    so no environment variable can move it. Each source digest has its own directory below."""
    home = pwd.getpwuid(os.getuid()).pw_dir
    return Path(home) / ".local" / "state" / "agent-harness" / "presence-helper"


def _environment() -> dict:
    """A fixed environment for the tools: no `DEVELOPER_DIR`, `SDKROOT` or `PATH` from the caller."""
    home = pwd.getpwuid(os.getuid()).pw_dir
    return {"PATH": "/usr/bin:/bin", "HOME": home, "LANG": "en_US.UTF-8"}


def source_digest() -> Optional[str]:
    """A digest of the helper's source and icon version, or None when the source is missing."""
    digest = hashlib.sha256(("icon:" + ICON_VERSION + "\0").encode("utf-8"))
    for name in SOURCES:
        try:
            data = (SOURCE / name).read_bytes()
        except OSError:
            return None
        digest.update(name.encode("utf-8") + b"\0" + data + b"\0")
    return digest.hexdigest()


def _private(path: Path) -> bool:
    """Whether `path` is a real directory owned by this user and writable by no one else."""
    try:
        info = os.lstat(str(path))
    except OSError:
        return False
    return (stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and not info.st_mode & (stat.S_IWGRP | stat.S_IWOTH))


def _make_private(directory: Path) -> bool:
    """Create `directory` and its parent for this user only; whether both are private."""
    try:
        for path in (directory.parent, directory):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            if _private(path) is False and os.lstat(str(path)).st_uid == os.getuid():
                os.chmod(str(path), 0o700)
    except OSError:
        return False
    return _private(directory.parent) and _private(directory)


def toolchain() -> Optional[str]:
    """A fingerprint of the developer tools `build` would use, or None when they are absent;
    asking never offers to install them."""
    if not (os.access(SWIFTC, os.X_OK) and os.access(CODESIGN, os.X_OK)
            and os.access(XCODE_SELECT, os.X_OK)):
        return None
    try:
        found = subprocess.run([XCODE_SELECT, "-p"], capture_output=True, text=True, timeout=10,
                               env=_environment(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    developer = found.stdout.strip()
    if found.returncode != 0 or not developer or not Path(developer).is_dir():
        return None
    parts = [developer]
    for path in (SWIFTC, developer):
        try:
            info = os.stat(path)
        except OSError:
            return None
        parts.append("%s:%d:%d" % (path, info.st_mtime_ns, info.st_size))
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def developer_tools() -> bool:
    """Whether `swiftc` and `codesign` can run without prompting an install of the tools."""
    return toolchain() is not None


def cdhash(app: Path) -> Optional[str]:
    """The bundle's code-signing hash once its signature verifies strictly, else None."""
    try:
        checked = subprocess.run([CODESIGN, "--verify", "--strict", str(app)], capture_output=True,
                                 text=True, timeout=30, env=_environment(),
                                 stdin=subprocess.DEVNULL)
        if checked.returncode != 0:
            return None
        shown = subprocess.run([CODESIGN, "--display", "--verbose=4", str(app)],
                               capture_output=True, text=True, timeout=30, env=_environment(),
                               stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    if shown.returncode != 0:
        return None
    for line in (shown.stderr + "\n" + shown.stdout).splitlines():
        if line.startswith("CDHash="):
            return line.partition("=")[2].strip() or None
    return None


def _read_json(path: Path) -> Optional[dict]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write_json(directory: Path, name: str, value: dict) -> None:
    """Write `name` atomically through a temporary file of its own, never a shared name."""
    fd, temporary = tempfile.mkstemp(prefix="." + name + ".", dir=str(directory))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(value, sort_keys=True) + "\n")
        os.replace(temporary, str(directory / name))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def verified(directory: Path) -> Optional[Helper]:
    """The helper in `directory` when the bundle matches its record and today's source."""
    digest = source_digest()
    if digest is None or not _private(directory):
        return None
    record = _read_json(directory / RECORD)
    if record is None or record.get("source") != digest or not record.get("cdhash"):
        return None
    app = directory / APP
    if cdhash(app) != record["cdhash"]:
        return None
    return Helper(app / "Contents" / "MacOS" / EXECUTABLE, str(record["cdhash"]))


@contextlib.contextmanager
def _locked(directory: Path, mode: int) -> Iterator[None]:
    fd = os.open(str(directory / LOCK), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, mode)
        yield
    finally:
        os.close(fd)


def _prepare(stack: contextlib.ExitStack, base: Optional[Path]) -> Optional[Helper]:
    digest = source_digest()
    if digest is None:
        return None
    directory = (root() if base is None else base) / digest[:16]
    if not _make_private(directory):
        return None
    for _attempt in range(2):
        shared = contextlib.ExitStack()
        shared.enter_context(_locked(directory, fcntl.LOCK_SH))
        found = verified(directory)
        if found is not None:
            stack.push(shared.__exit__)
            return found
        shared.close()
        with _locked(directory, fcntl.LOCK_EX):
            if verified(directory) is None and build(directory) is None:
                return None
    return None


@contextlib.contextmanager
def ready(base: Optional[Path] = None) -> Iterator[Optional[Helper]]:
    """Yield the verified helper, built first if need be, held under a shared lock until the
    block ends so no build replaces it while it runs; or None, to ask through `osascript`.
    Preparing it never raises. `base` replaces `root()` for tests."""
    stack = contextlib.ExitStack()
    try:
        helper = _prepare(stack, base)
    except Exception:  # noqa: BLE001 - the osascript path still asks the person
        stack.close()
        helper = None
    with stack:
        yield helper


def executable(directory: Path) -> Optional[Helper]:
    """The verified helper in `directory`, built or rebuilt first; None when it cannot be."""
    with _locked(directory, fcntl.LOCK_EX):
        found = verified(directory)
        return found if found is not None else build(directory)


def _recent_failure(directory: Path, fingerprint: str) -> bool:
    failed = _read_json(directory / FAILED)
    if failed is None or failed.get("toolchain") != fingerprint:
        return False
    at = failed.get("at")
    return isinstance(at, (int, float)) and 0 <= time.time() - at < RETRY_AFTER


def build(directory: Path) -> Optional[Helper]:
    """Compile, assemble and ad-hoc sign the helper into `directory`, record its hash, and
    answer it; None when the tools are missing, a recent build with the same toolchain failed,
    or any step fails, which is then remembered. The caller holds the exclusive lock (`ready`).
    Never raises."""
    digest = source_digest()
    fingerprint = toolchain() if digest is not None else None
    if fingerprint is None or not _make_private(directory) or _recent_failure(directory,
                                                                              fingerprint):
        return None
    try:
        staging = Path(tempfile.mkdtemp(prefix=".build-", dir=str(directory)))
    except OSError:
        return None
    built = None
    try:
        app = staging / APP
        contents = app / "Contents"
        (contents / "MacOS").mkdir(parents=True)
        (contents / "Resources").mkdir()
        shutil.copyfile(str(SOURCE / "Info.plist"), str(contents / "Info.plist"))
        (contents / "Resources" / ICON).write_bytes(icon())
        compiled = subprocess.run(
            [SWIFTC, "-O", "-module-cache-path", str(staging / "cache"),
             "-Xlinker", "-sectcreate", "-Xlinker", "__TEXT", "-Xlinker", "__info_plist",
             "-Xlinker", str(SOURCE / "Info.plist"),
             "-o", str(contents / "MacOS" / EXECUTABLE), str(SOURCE / "main.swift")],
            capture_output=True, text=True, timeout=COMPILE_TIMEOUT, env=_environment(),
            stdin=subprocess.DEVNULL, cwd=str(staging))
        signed = compiled.returncode == 0 and subprocess.run(
            [CODESIGN, "--force", "--sign", "-", str(app)], capture_output=True, text=True,
            timeout=60, env=_environment(), stdin=subprocess.DEVNULL).returncode == 0
        built = cdhash(app) if signed else None
        if built is not None:
            final = directory / APP
            if final.exists() or final.is_symlink():
                retired = directory / (".retired-" + os.urandom(6).hex())
                os.rename(str(final), str(retired))
                shutil.rmtree(str(retired), ignore_errors=True)
            os.rename(str(app), str(final))
            _write_json(directory, RECORD, {"cdhash": built, "source": digest})
    except (OSError, subprocess.SubprocessError):
        built = None
    finally:
        shutil.rmtree(str(staging), ignore_errors=True)
    try:
        if built is None:
            _write_json(directory, FAILED, {"toolchain": fingerprint, "at": time.time()})
            return None
        with contextlib.suppress(FileNotFoundError):
            os.unlink(str(directory / FAILED))
    except OSError:
        return None
    return verified(directory)


def _png(size: int) -> bytes:
    """A plain icon at `size` pixels: a deep green rounded square holding a white ring."""
    radius, corner = size / 2.0, size * 0.22
    ring_outer, ring_inner = size * 0.30, size * 0.20
    rows = []
    for y in range(size):
        row = bytearray(b"\0")
        for x in range(size):
            cx, cy = x + 0.5, y + 0.5
            dx = max(abs(cx - radius) - (radius - corner), 0.0)
            dy = max(abs(cy - radius) - (radius - corner), 0.0)
            if dx * dx + dy * dy > corner * corner:
                row += b"\0\0\0\0"
                continue
            distance = ((cx - radius) ** 2 + (cy - radius) ** 2) ** 0.5
            row += (b"\xff\xff\xff\xff" if ring_inner <= distance <= ring_outer
                    else b"\x1f\x5c\x4a\xff")
        rows.append(bytes(row))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def icon() -> bytes:
    """The helper's `.icns`, drawn here so no image asset is checked in: PNG entries at 128, 256
    and 512 pixels."""
    entries = b""
    for kind, size in ((b"ic07", 128), (b"ic08", 256), (b"ic09", 512)):
        data = _png(size)
        entries += kind + struct.pack(">I", len(data) + 8) + data
    return b"icns" + struct.pack(">I", len(entries) + 8) + entries

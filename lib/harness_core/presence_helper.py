# SPDX-License-Identifier: MIT
"""The Model Citizen helper app that asks for presence, so the dialog names Model Citizen.

macOS names the calling program, and shows its icon, in the Touch ID or login-password dialog;
asked through `/usr/bin/osascript` it reads "osascript". `presence_app/` holds the source of a
tiny app bundle named Model Citizen whose only work is to call LocalAuthentication
(`evaluatePolicy(.deviceOwnerAuthentication, localizedReason:)`) and relay its answer. No binary
is checked in: `executable()` compiles it with `swiftc`, draws its icon, ad-hoc signs it
(`codesign -s -`) into the owner's state directory, and records its code-signing hash.

Before each use the bundle is verified (`codesign --verify --strict`) and its hash compared with
the record; a mismatch, or source that changed since the build, rebuilds it. Where the developer
tools are absent or a build fails, `executable()` answers None and `presence.confirm` asks through
`osascript` as before, so the check is never lost. The helper directory comes from the account's
home in the password database, never from `HOME` or `HARNESS_HOME`, so an environment variable
cannot point the check at a planted helper. Code running as the same user can still replace the
helper and its record together, the same limit `docs/runtime-controls.md` states for the rest.
"""
from __future__ import annotations

import hashlib
import json
import os
import pwd
import shutil
import stat
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path
from typing import Optional

SOURCE = Path(__file__).resolve().parent / "presence_app"
SOURCES = ("main.swift", "Info.plist")
APP = "Model Citizen.app"
EXECUTABLE = "model-citizen-presence"
ICON = "ModelCitizen.icns"
RECORD = "helper.json"
CODESIGN = "/usr/bin/codesign"
SWIFTC = "/usr/bin/swiftc"
XCODE_SELECT = "/usr/bin/xcode-select"
# The icon's drawing is part of what the record covers: change it and the helper rebuilds.
ICON_VERSION = "1"
BUILD_TIMEOUT = 300


def root() -> Path:
    """Where the helper lives: the account's own state directory, read from the password
    database so no environment variable can move it."""
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


def developer_tools() -> bool:
    """Whether `swiftc` and `codesign` can run without prompting an install of the tools."""
    if not (os.access(SWIFTC, os.X_OK) and os.access(CODESIGN, os.X_OK)
            and os.access(XCODE_SELECT, os.X_OK)):
        return False
    try:
        # Prints the active developer directory without offering to install one.
        found = subprocess.run([XCODE_SELECT, "-p"], capture_output=True, text=True, timeout=10,
                               env=_environment(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return found.returncode == 0 and Path(found.stdout.strip() or "/nonexistent").is_dir()


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


def verified(directory: Optional[Path] = None) -> Optional[Path]:
    """The helper's executable when the built bundle matches its record and today's source."""
    directory = root() if directory is None else directory
    digest = source_digest()
    if digest is None or not _private(directory):
        return None
    try:
        record = json.loads((directory / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("source") != digest or not record.get("cdhash"):
        return None
    app = directory / APP
    if cdhash(app) != record["cdhash"]:
        return None
    return app / "Contents" / "MacOS" / EXECUTABLE


def executable(directory: Optional[Path] = None) -> Optional[Path]:
    """The verified helper, built or rebuilt first when it is missing, stale or tampered with;
    None when it cannot be, and `presence.confirm` falls back to `osascript`."""
    directory = root() if directory is None else directory
    found = verified(directory)
    if found is not None:
        return found
    return build(directory)


def build(directory: Optional[Path] = None) -> Optional[Path]:
    """Compile, assemble and ad-hoc sign the helper into `directory`, record its hash, and answer
    its executable; None when the tools are missing or any step fails. Never raises."""
    directory = root() if directory is None else directory
    digest = source_digest()
    if digest is None or not developer_tools():
        return None
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(str(directory), 0o700)
        if not _private(directory):
            return None
        staging = Path(tempfile.mkdtemp(prefix=".build-", dir=str(directory)))
    except OSError:
        return None
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
            capture_output=True, text=True, timeout=BUILD_TIMEOUT, env=_environment(),
            stdin=subprocess.DEVNULL, cwd=str(staging))
        if compiled.returncode != 0:
            return None
        signed = subprocess.run([CODESIGN, "--force", "--sign", "-", str(app)],
                                capture_output=True, text=True, timeout=60, env=_environment(),
                                stdin=subprocess.DEVNULL)
        if signed.returncode != 0:
            return None
        built = cdhash(app)
        if built is None:
            return None
        final = directory / APP
        if final.exists() or final.is_symlink():
            retired = directory / (".retired-" + os.urandom(6).hex())
            os.rename(str(final), str(retired))
            shutil.rmtree(str(retired), ignore_errors=True)
        os.rename(str(app), str(final))
        record = directory / (RECORD + ".tmp")
        record.write_text(json.dumps({"cdhash": built, "source": digest}, sort_keys=True) + "\n",
                          encoding="utf-8")
        os.replace(str(record), str(directory / RECORD))
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        shutil.rmtree(str(staging), ignore_errors=True)
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

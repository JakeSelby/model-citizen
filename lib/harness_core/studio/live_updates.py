# SPDX-License-Identifier: MIT
"""Bounded local change observation and ordered Studio live events."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import stat
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Iterable, List, Mapping, Optional, Tuple

from harness_core import catalog, observer


POLL_SECONDS = 1.0
HEARTBEAT_SECONDS = 10.0
REPLAY_LIMIT = 256
MAX_WATCHED_PATHS = 4096
OVERFLOW_PATH = "<studio-watch-overflow>"
ALL_TOPICS = ("activity", "configure", "library", "library-index", "overview", "reports",
              "runs", "selection")
Fingerprint = Optional[Tuple[object, ...]]
Snapshot = Dict[str, Tuple[Fingerprint, Tuple[str, ...]]]


class OverflowAggregate:
    """Constant-space fingerprint for paths beyond the retained watch set."""

    def __init__(self) -> None:
        self.count = 0
        self.topics: set[str] = set()
        self._hasher = hashlib.sha256()

    def add(self, path: str, fingerprint: Fingerprint, topics: Iterable[str]) -> None:
        topic_list = tuple(sorted(set(topics)))
        self.count += 1
        self.topics.update(topic_list)
        self._hasher.update(repr((path, fingerprint, topic_list)).encode("utf-8"))

    def fingerprint(self) -> Tuple[object, ...]:
        return ("overflow", self.count, self._hasher.hexdigest())


def _within(path: Path, root: Path) -> bool:
    try:
        target = path.resolve(strict=True)
        approved = root.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return False
    return target == approved or approved in target.parents


def _fingerprint(path: Path, allowed_root: Optional[Path] = None,
                 info: Optional[os.stat_result] = None) -> Fingerprint:
    if info is None:
        try:
            info = path.lstat()
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
            return None
    if stat.S_ISLNK(info.st_mode):
        if allowed_root is None or not _within(path, allowed_root):
            return None
        try:
            target = path.resolve(strict=True)
            target_info = target.stat()
            if not (stat.S_ISREG(target_info.st_mode) or stat.S_ISDIR(target_info.st_mode)):
                return None
            digest = ""
            if stat.S_ISREG(target_info.st_mode):
                hasher = hashlib.sha256()
                with target.open("rb") as source:
                    for block in iter(lambda: source.read(65536), b""):
                        hasher.update(block)
                digest = hasher.hexdigest()
            return ("symlink", info.st_ino, info.st_size, info.st_mtime_ns, str(target),
                    stat.S_IFMT(target_info.st_mode), target_info.st_ino, target_info.st_size,
                    target_info.st_mtime_ns, digest)
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
            return None
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        return None
    return (stat.S_IFMT(info.st_mode), info.st_ino, info.st_size, info.st_mtime_ns)


def _add(snapshot: Snapshot, path: Path, topics: Iterable[str], overflow: OverflowAggregate,
         allowed_root: Optional[Path] = None, info: Optional[os.stat_result] = None) -> None:
    expanded = path.expanduser()
    resolved = str(expanded if expanded.is_absolute() else Path(os.path.abspath(str(expanded))))
    previous = snapshot.get(resolved)
    if previous is None and len(snapshot) >= MAX_WATCHED_PATHS:
        overflow.add(resolved, _fingerprint(Path(resolved), allowed_root, info), topics)
        return
    merged = set(previous[1] if previous else ())
    merged.update(topics)
    snapshot[resolved] = (_fingerprint(Path(resolved), allowed_root, info), tuple(sorted(merged)))


class WatchScanner:
    """Snapshot only catalog-shaped inputs and known Studio state files."""

    def __init__(self, repo_root: Path, state_root: Path,
                 environ: Optional[Mapping[str, str]] = None):
        self.repo_root = Path(repo_root).resolve()
        self.state_root = Path(state_root).resolve()
        self.environ = dict(os.environ if environ is None else environ)
        self._overflow = OverflowAggregate()
        self._scan_lock = threading.Lock()

    def _record(self, snapshot: Snapshot, path: Path, topics: Iterable[str],
                allowed_root: Optional[Path] = None,
                info: Optional[os.stat_result] = None) -> None:
        _add(snapshot, path, topics, self._overflow, allowed_root, info)

    @staticmethod
    def _matches(base: Path, pattern: str, root: Path) -> List[Tuple[Path, os.stat_result]]:
        first, separator, second = pattern.partition("/")
        matches: List[Tuple[Path, os.stat_result]] = []
        with os.scandir(str(base)) as entries:
            parents = sorted((entry for entry in entries
                              if fnmatch.fnmatchcase(entry.name, first)), key=lambda item: item.name)
        if not separator:
            for entry in parents:
                try:
                    matches.append((Path(entry.path), entry.stat(follow_symlinks=False)))
                except OSError:
                    continue
            return matches
        for parent in parents:
            parent_path = Path(parent.path)
            try:
                if not parent.is_dir() or not _within(parent_path, root):
                    continue
                with os.scandir(parent.path) as children:
                    selected = sorted((entry for entry in children
                                       if fnmatch.fnmatchcase(entry.name, second)),
                                      key=lambda item: item.name)
                for entry in selected:
                    matches.append((Path(entry.path), entry.stat(follow_symlinks=False)))
            except OSError:
                continue
        return matches

    def _config(self) -> Tuple[Dict[str, Any], List[Tuple[Path, Path]]]:
        posture = catalog.posture_module(self.repo_root)
        paths: List[Tuple[Path, Path]] = []
        config: Dict[str, Any] = {}
        if posture is not None:
            user_path = Path(posture.config_path(self.environ)).expanduser()
            paths.append((user_path, user_path.parent))
            if not user_path.is_symlink() or _within(user_path, user_path.parent):
                try:
                    loaded = posture._user_config(self.environ, False)
                    if isinstance(loaded, dict):
                        config = loaded
                except (OSError, ValueError, TypeError):
                    pass
        for name in ("HARNESS_PROJECT_CONFIG", "HARNESS_SESSION_CONFIG"):
            value = self.environ.get(name)
            if value:
                path = Path(value).expanduser()
                paths.append((path, path.parent))
        return config, paths

    def _module_roots(self, config: Mapping[str, Any]) -> List[Path]:
        roots = [self.repo_root / "primitives"]
        values = config.get("primitive_roots", [])
        if isinstance(values, list):
            for value in values:
                if isinstance(value, str) and Path(value).expanduser().is_absolute():
                    roots.append(Path(value).expanduser())
        return roots

    def _modules(self, snapshot: Snapshot, config: Mapping[str, Any]) -> None:
        for root in self._module_roots(config):
            self._record(snapshot, root, ("library-index", "overview", "selection"), root)
            self._record(snapshot, root / "manifests.json", ("library-index",), root)
            definitions = dict(catalog.KINDS)
            definitions["modes"] = {"directory": "modes", "pattern": "*.json"}
            for kind, definition in definitions.items():
                directory, pattern = definition.get("directory"), definition.get("pattern")
                if not directory or not pattern:
                    continue
                base = root / directory
                self._record(snapshot, base, ("library-index", "overview", "selection"), root)
                try:
                    matches = self._matches(base, pattern, root) if _within(base, root) else ()
                except OSError:
                    matches = ()
                for path, info in matches:
                    path_topics = ("library", "overview", "selection") if kind == "modes" else (
                        "library", "overview")
                    self._record(snapshot, path, path_topics, root, info)
                    self._record(snapshot, path.parent,
                                 ("library-index", "overview", "selection"), root)
        hook_root = self.repo_root / catalog.HOOKS_DIRECTORY
        self._record(snapshot, hook_root, ("library-index", "overview"), self.repo_root)
        self._record(snapshot, self.repo_root / "policy" / "hooks" / "manifests.json",
                     ("library-index",), self.repo_root)
        try:
            hook_paths = (sorted(hook_root.glob("*.py"))
                          if _within(hook_root, self.repo_root) and hook_root.is_dir() else ())
        except OSError:
            hook_paths = ()
        for path in hook_paths:
            self._record(snapshot, path, ("library", "overview"), self.repo_root)

    def _runs(self, snapshot: Snapshot) -> None:
        topics = ("overview", "reports", "runs")
        for name in ("queue.json", "run-index.sqlite3", "run-index.sqlite3-wal",
                     "run-index.sqlite3-shm"):
            self._record(snapshot, self.state_root / name, topics)
        runs_root = self.state_root / "runs"
        self._record(snapshot, runs_root, topics)
        try:
            run_dirs = sorted(path for path in runs_root.iterdir() if path.is_dir())
        except OSError:
            run_dirs = ()
        for path in run_dirs:
            self._record(snapshot, path, topics)
            self._record(snapshot, path / "run.json", topics)

    def _scan(self) -> Snapshot:
        snapshot: Snapshot = {}
        self._overflow = OverflowAggregate()
        config, config_paths = self._config()
        for path, allowed_root in config_paths:
            self._record(snapshot, path,
                         ("configure", "library-index", "overview", "selection"), allowed_root)
        self._modules(snapshot, config)
        ledger_topics = ("activity", "overview", "reports")
        self._record(snapshot, observer.ledger_path(self.environ), ledger_topics)
        self._record(snapshot, observer.errors_path(self.environ), ledger_topics)
        self._runs(snapshot)
        if self._overflow.count:
            snapshot[OVERFLOW_PATH] = (self._overflow.fingerprint(),
                                       tuple(sorted(self._overflow.topics)))
        return snapshot

    def scan(self) -> Snapshot:
        with self._scan_lock:
            return self._scan()


class EventBroker:
    """Publish one ordered event log shared by every connected browser."""

    def __init__(self, instance_epoch: str, replay_limit: int = REPLAY_LIMIT):
        if not instance_epoch or replay_limit < 1:
            raise ValueError("live event broker needs an epoch and replay capacity")
        self.instance_epoch = instance_epoch
        self._sequence = 0
        self._events: Deque[Dict[str, object]] = deque(maxlen=replay_limit)
        self._condition = threading.Condition()
        self._closed = False

    @property
    def sequence(self) -> int:
        with self._condition:
            return self._sequence

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    def publish(self, topics: Iterable[str], paths: Iterable[str] = ()) -> Dict[str, object]:
        topic_list = sorted(set(topics))
        path_list = sorted(set(paths))
        if not topic_list:
            raise ValueError("live event must name at least one topic")
        with self._condition:
            if self._closed:
                raise RuntimeError("live event broker is closed")
            self._sequence += 1
            event = {"kind": "change", "instance_epoch": self.instance_epoch,
                     "sequence": self._sequence, "topics": topic_list, "paths": path_list}
            self._events.append(event)
            self._condition.notify_all()
            return dict(event)

    def _cursor(self, event_id: Optional[str]) -> Optional[Tuple[str, int]]:
        if not event_id:
            return None
        epoch, separator, sequence = event_id.rpartition(":")
        if not separator:
            return ("", -1)
        try:
            parsed = int(sequence)
        except ValueError:
            parsed = -1
        return epoch, parsed

    def _control(self, kind: str) -> Dict[str, object]:
        return {"kind": kind, "instance_epoch": self.instance_epoch,
                "sequence": self._sequence, "topics": list(ALL_TOPICS), "paths": []}

    def events_after(self, event_id: Optional[str]) -> List[Dict[str, object]]:
        with self._condition:
            cursor = self._cursor(event_id)
            if cursor is None:
                return [self._control("snapshot")]
            epoch, sequence = cursor
            oldest = int(self._events[0]["sequence"]) if self._events else self._sequence + 1
            if (epoch != self.instance_epoch or sequence < 0 or sequence > self._sequence
                    or sequence < oldest - 1):
                return [self._control("gap")]
            return [dict(event) for event in self._events
                    if int(event["sequence"]) > sequence]

    def wait(self, event_id: Optional[str], timeout: float) -> List[Dict[str, object]]:
        with self._condition:
            ready = self.events_after(event_id)
            if ready:
                return ready
            self._condition.wait_for(
                lambda: self._closed or bool(self.events_after(event_id)), timeout=timeout)
            return [] if self._closed else self.events_after(event_id)

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()


class LiveWatcher:
    """Poll once for all clients and coalesce each scan into one broker event."""

    def __init__(self, scanner: WatchScanner, broker: EventBroker,
                 interval: float = POLL_SECONDS,
                 wait: Optional[Callable[[float], bool]] = None):
        self.scanner = scanner
        self.broker = broker
        self.interval = interval
        self._stopped = threading.Event()
        self._wait = wait or self._stopped.wait
        self._thread: Optional[threading.Thread] = None
        self.scan_count = 0

    @staticmethod
    def changes(previous: Snapshot, current: Snapshot) -> Tuple[List[str], List[str]]:
        paths = sorted(path for path in set(previous) | set(current)
                       if previous.get(path, (None, ()))[0] != current.get(path, (None, ()))[0])
        topics = sorted({topic for path in paths
                         for topic in (current.get(path) or previous.get(path) or (None, ()))[1]})
        if OVERFLOW_PATH in paths:
            return topics, []
        return topics, paths

    def poll(self, previous: Snapshot) -> Snapshot:
        current = self.scanner.scan()
        self.scan_count += 1
        topics, paths = self.changes(previous, current)
        if topics and not self.broker.closed:
            self.broker.publish(topics, paths)
        return current

    def _run(self) -> None:
        previous = self.scanner.scan()
        self.scan_count += 1
        failed = False
        while not self._wait(self.interval):
            try:
                previous = self.poll(previous)
                failed = False
            except Exception:
                if not failed and not self.broker.closed:
                    self.broker.publish(ALL_TOPICS)
                failed = True

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("live watcher already started")
        self._thread = threading.Thread(target=self._run, name="studio-live-watcher", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stopped.set()
        self.broker.close()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval * 4))
            self._thread = None


def event_id(event: Mapping[str, object]) -> str:
    return "%s:%d" % (event["instance_epoch"], event["sequence"])


def encode(event: Mapping[str, object]) -> bytes:
    name = str(event["kind"])
    body = json.dumps(dict(event), sort_keys=True, separators=(",", ":"))
    return ("retry: 1000\nid: %s\nevent: %s\ndata: %s\n\n" % (
        event_id(event), name, body)).encode("utf-8")

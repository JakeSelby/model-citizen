# SPDX-License-Identifier: MIT
"""The single executor through which Studio domain mutations run."""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class _Work:
    operation: Callable[[], Any]
    done: threading.Event
    result: Any = None
    error: Optional[BaseException] = None


class MutationExecutor:
    """Serialize request-thread mutations and return their exact result or error."""

    def __init__(self) -> None:
        self._queue = queue.Queue()  # type: queue.Queue
        self._guard = threading.Lock()
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="studio-mutations", daemon=True)
        self._thread.start()

    def call(self, operation: Callable[[], Any]) -> Any:
        if not callable(operation):
            raise TypeError("Studio mutation must be callable")
        if threading.current_thread() is self._thread:
            raise RuntimeError("Studio mutation cannot recursively enter its executor")
        work = _Work(operation, threading.Event())
        with self._guard:
            if self._closed:
                raise RuntimeError("Studio mutation executor is closed")
            self._queue.put(work)
        work.done.wait()
        if work.error is not None:
            raise work.error
        return work.result

    def _run(self) -> None:
        while True:
            work = self._queue.get()
            if work is None:
                return
            try:
                work.result = work.operation()
            except BaseException as exc:
                work.error = exc
            finally:
                work.done.set()

    def close(self) -> None:
        with self._guard:
            if self._closed:
                return
            self._closed = True
            self._queue.put(None)
        self._thread.join()

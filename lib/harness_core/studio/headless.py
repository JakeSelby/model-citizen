# SPDX-License-Identifier: MIT
"""Answer a Studio route from the CLI by running the route's own handler without HTTP.

Some Studio actions have no older `citizen` command: the paid replay, eval, native-acceptance and
draft-test admissions, whose previews resolve targets and issue a spend confirmation, and whose
starts re-resolve and consume it. Rather than a second CLI path that could validate, refuse or
answer differently, `citizen runs replay|eval|native|draft-test ACTION --request FILE` hands the
request body to the same handler the Studio calls, on the same mutation executor shape, and prints
what the Studio would have sent. Only transport differs: no session, cookie or Origin, because the
CLI is the user's own process.
"""
from __future__ import annotations

import json
import socket
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Mapping, Optional, Tuple

from . import runs, targets
from .mutations import MutationExecutor

# `citizen runs GROUP ACTION` -> the Studio route it answers as. Every entry is a JSON route.
ROUTES = OrderedDict((
    ("replay", OrderedDict((
        ("catalog", ("GET", "/api/runs/replay/catalog")),
        ("preview", ("POST", "/api/runs/replay/preview")),
        ("start", ("POST", "/api/runs/replay/start")),
        ("result", ("POST", "/api/runs/replay/result")),
    ))),
    ("eval", OrderedDict((
        ("catalog", ("POST", "/api/evals/catalog")),
        ("run", ("POST", "/api/evals/run")),
        ("preview", ("POST", "/api/evals/preview")),
        ("start", ("POST", "/api/evals/start")),
        ("result", ("POST", "/api/evals/result")),
    ))),
    ("native", OrderedDict((
        ("catalog", ("GET", "/api/experiments/native-acceptance/catalog")),
        ("progress", ("POST", "/api/experiments/native-acceptance/progress")),
        ("preview", ("POST", "/api/experiments/native-acceptance/preview")),
        ("start", ("POST", "/api/experiments/native-acceptance/start")),
        ("retry", ("POST", "/api/experiments/native-acceptance/retry")),
    ))),
    ("draft-test", OrderedDict((
        ("plan", ("POST", "/api/configure/test/plan")),
        ("start", ("POST", "/api/configure/test/start")),
    ))),
))

# Paid catalog suites that only these admissions may start. `citizen runs start` refuses them,
# because starting one there would skip the target resolution and refusals the Studio applies.
# A draft test is a live replay of the draft against its base, so both admissions start one.
ADMITTED_SUITES = {"live-replay": ("replay", "draft-test"), "native-acceptance": ("native",),
                   "micro-tier": ("eval",), "unit-eval": ("eval",)}
START_ACTIONS = {"replay": "preview|start", "draft-test": "plan|start", "native": "preview|start",
                 "eval": "preview|start"}
# The admissions that spend. Each asks the person at the Mac in its route's handler, on both faces
# (`presence.confirm`); a preview, plan, catalog or retry plan is free.
SPEND_ACTIONS = frozenset((group, "start") for group in START_ACTIONS)


def cli_command(group: str, action: str) -> Tuple[str, ...]:
    """The exact `citizen` command a Studio route shows for one group and action."""
    method, _path = ROUTES[group][action]
    body = () if method == "GET" else ("--request", "request.json")
    return ("citizen", "runs", group, action) + body + ("--json",)


class _Server:
    """The attributes a JSON route handler reads from the Studio server."""

    def __init__(self, repo_root: Path, state_directory: Path):
        self.repo_root = Path(repo_root).resolve()
        self.store = SimpleNamespace(path=Path(state_directory).resolve())
        self.mutations = MutationExecutor()
        self.target_service = targets.TargetService(self.repo_root)
        self.run_progress = OrderedDict()  # type: OrderedDict
        try:
            self.run_supervisor = self.mutations.call(lambda: runs.RunSupervisor(
                self.store.path, runs.default_catalog_path(self.repo_root),
                target_service=self.target_service))
        except BaseException:
            self.mutations.close()
            raise

    def close(self) -> None:
        """Release the supervisor's descriptors and index connection, then the executor."""
        try:
            self.mutations.call(self.run_supervisor.close)
        finally:
            self.mutations.close()


class _Handler:
    def __init__(self, server: _Server, request: Mapping[str, Any]):
        self.server = server
        self.request_json = dict(request)
        self.answer = None  # type: Optional[Tuple[int, Dict[str, Any]]]
        self.face = "citizen"  # who the Activity record names; never a reason to pass
        # Long reads poll whether their client left; the CLI's caller stays for the answer.
        self.connection, self._peer = socket.socketpair()
        self.close_connection = False

    def close(self) -> None:
        self.connection.close()
        self._peer.close()

    def _json(self, code: int, payload: Dict[str, Any]) -> None:
        self.answer = (code, json.loads(json.dumps(payload, sort_keys=True)))

    def _error(self, code: int, name: str) -> None:
        self.answer = (code, {"error": name})


def call(repo_root: Path, state_directory: Path, group: str, action: str,
         request: Optional[Mapping[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
    """Run the route one `citizen runs GROUP ACTION` maps to; its status and JSON body."""
    method, path = ROUTES[group][action]
    return call_route(repo_root, state_directory, method, path, request)


def call_route(repo_root: Path, state_directory: Path, method: str, path: str,
               request: Optional[Mapping[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
    """Run one registered JSON route's handler and return its status and JSON body."""
    from . import server as studio_server  # the route table imports this module's commands

    route = studio_server.ROUTES.resolve(method, path)
    if route is None or route.media_type != "application/json" or route.parity_exemption:
        raise ValueError("not a headless Studio route: %s %s" % (method, path))
    if request is not None and not isinstance(request, Mapping):
        return 400, {"error": "invalid_request"}
    server = _Server(repo_root, state_directory)
    try:
        handler = _Handler(server, request or {})
        try:
            route.handler(handler, route)
        finally:
            handler.close()
    finally:
        server.close()
    if handler.answer is None:
        raise RuntimeError("Studio route %s %s sent no JSON answer" % (method, path))
    return handler.answer

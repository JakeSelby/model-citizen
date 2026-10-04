# SPDX-License-Identifier: MIT
"""The Python-standard-library loopback server for Studio."""
from __future__ import annotations

import errno
import hmac
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from typing import Callable, Dict, Iterable, Optional, Tuple

from harness_core import overview, workers

from . import (activity, auth, compare, draft_registration, draft_tests, drafts, free_suites,
               live_updates, module_authoring, module_editing, module_library,
               native_acceptance, replay, runs, selection, selection_editing, settings, targets)
from . import apply as draft_apply
from . import eval_tiers, first_run
from . import rollback as draft_rollback
from .mutations import MutationExecutor
from .state import PROTOCOL_VERSION, SCHEMA_VERSION, Store

STARTUP_TRACE_ENV = "HARNESS_STUDIO_STARTUP_TRACE"
CONTROL_HEALTH = "/__studio/control/health"
CONTROL_STOP = "/__studio/control/stop"
CONTROL_BOOTSTRAP = "/__studio/control/bootstrap"
BOOTSTRAP = "/__studio/bootstrap"
PARITY_EXEMPTIONS = frozenset(("transport", "bootstrap", "static", "authenticated-health",
                               "sse", "ui-preferences"))
STATIC_ASSET = re.compile(
    r"^/assets/[A-Za-z0-9_-]+-[A-Za-z0-9_-]{8}\.(?P<extension>css|js)$"
)
STATIC_ASSET_ROUTES = {
    "css": "/assets/{content-hash}.css",
    "js": "/assets/{content-hash}.js",
}
PLUGIN_EVAL_REPORT = re.compile(
    r"^" + re.escape(runs.PLUGIN_EVAL_REPORT_ROUTE)
    + r"(?P<run_id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)
PLUGIN_EVAL_REPORT_PATH = runs.PLUGIN_EVAL_REPORT_ROUTE + "{run_id}"
# The imported report is third-party HTML with inline script. The sandbox directive gives it an
# opaque origin, so it can reach neither the Studio session nor the API, and it may load nothing.
PLUGIN_EVAL_REPORT_CSP = ("sandbox allow-scripts; default-src 'none'; "
                          "script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                          "img-src data:; font-src data:; base-uri 'none'; "
                          "form-action 'none'; frame-ancestors 'none'")
RUN_PROGRESS_MAX_ENTRIES = 128
RUN_PROGRESS_TERMINAL_TTL_SECONDS = 300


def _process_identity() -> Optional[str]:
    # Darwin lifecycle validates this private token through the authenticated handshake as well
    # as checking PID liveness, without spawning ps from the detached child.
    if sys.platform == "darwin":
        return secrets.token_hex(24)
    return workers.process_start(os.getpid())


def _startup_trace(stage: str) -> None:
    if os.environ.get(STARTUP_TRACE_ENV) == "1":
        print("studio-startup: " + stage, file=sys.stderr, flush=True)


MAX_STATIC_ASSET_BYTES = 8 * 1024 * 1024
MAX_MODULE_JSON_BYTES = module_editing.MAX_SOURCE_BYTES * 6 + 16 * 1024
STYLE_NONCE_MARKER = b"__STUDIO_STYLE_NONCE__"


class StopRequested(BaseException):
    pass


@dataclass(frozen=True)
class ResponseSchema:
    """The successful payload shape a registered route is allowed to emit."""

    kind: str
    fields: Tuple[Tuple[str, str], ...] = ()

    def validate(self, payload) -> None:
        if self.kind == "html-document":
            if not isinstance(payload, bytes) or not payload.lstrip().lower().startswith(b"<!doctype html"):
                raise ValueError("route response is not an HTML document")
            return
        if self.kind in ("binary", "sse-stream", "live-sse-stream"):
            if not isinstance(payload, bytes):
                raise ValueError("route response is not binary")
            if self.kind == "sse-stream" and payload and not payload.startswith(b"event: run\n"):
                raise ValueError("route response is not an SSE run event")
            if self.kind == "live-sse-stream" and payload and b"\nevent: " not in payload:
                raise ValueError("route response is not an SSE live event")
            return
        if self.kind == "redirect":
            if payload is not None:
                raise ValueError("redirect route emitted a body")
            return
        if self.kind == "run-record":
            required = {
                "schema_version", "run_id", "suite_id", "suite_version", "target",
                "cost_class", "expected_duration_seconds", "timeout_seconds", "status",
                "queue_sequence", "created_at", "case_identities", "spend_estimate",
                "spend_cap", "pricing_identity", "canonical_run_digest", "command",
                "exact_command", "parameters",
            }
            optional = {
                "runner_pid", "runner_identity", "command_pid", "command_identity",
                "started_at", "completed_at", "reason", "returncode", "capacity_reserved",
                "spend_actual", "spend_stop_reason", "case_results", "usage_ledger_state",
                "rerun_of",
            }
            if (not isinstance(payload, dict) or required - set(payload)
                    or set(payload) - required - optional):
                raise ValueError("route response is not a public run record")
            return
        if self.kind != "json-object" or not isinstance(payload, dict):
            raise ValueError("route response does not match its schema kind")
        if set(payload) != {name for name, _ in self.fields}:
            raise ValueError("route response fields do not match its schema")
        types = {"boolean": bool, "integer": int, "string": str}
        types.update({"array": list, "object": dict})
        for name, type_name in self.fields:
            if type_name == "object-or-null":
                if payload[name] is not None and not isinstance(payload[name], dict):
                    raise ValueError("route response field %s is not object-or-null" % name)
                continue
            if type_name == "string-or-null":
                if payload[name] is not None and not isinstance(payload[name], str):
                    raise ValueError("route response field %s is not string-or-null" % name)
                continue
            if type_name == "number-or-null":
                if (payload[name] is not None
                        and (not isinstance(payload[name], (int, float))
                             or isinstance(payload[name], bool))):
                    raise ValueError("route response field %s is not number-or-null" % name)
                continue
            if type_name == "integer-or-null":
                if (payload[name] is not None
                        and (not isinstance(payload[name], int) or isinstance(payload[name], bool))):
                    raise ValueError("route response field %s is not integer-or-null" % name)
                continue
            expected = types[type_name]
            value = payload[name]
            if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
                raise ValueError("route response field %s is not %s" % (name, type_name))


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    media_type: str
    response_schema: ResponseSchema
    handler: Callable[["Handler", "Route"], None]
    parity_exemption: Optional[str]
    request_media_type: Optional[str] = None
    cli_command: Optional[Tuple[str, ...]] = None


class RouteRegistry:
    """The one owned contract for every private Studio HTTP route."""

    def __init__(self, routes: Iterable[Route]):
        self.entries = tuple(routes)
        self.error_media_type = "application/json"
        self.error_schema = ResponseSchema("json-object", (("error", "string"),))
        self._routes = {}
        for route in self.entries:
            key = (route.method, route.path)
            if route.method != route.method.upper() or not route.path.startswith("/"):
                raise ValueError("route method and path must be canonical")
            if route.parity_exemption is not None and route.parity_exemption not in PARITY_EXEMPTIONS:
                raise ValueError("unsupported Studio parity exemption: " + route.parity_exemption)
            if route.parity_exemption is None and not route.cli_command:
                raise ValueError("Studio domain route must name its citizen command")
            if key in self._routes:
                raise ValueError("duplicate Studio route: %s %s" % key)
            self._routes[key] = route

    def resolve(self, method: str, path: str) -> Optional[Route]:
        exact = self._routes.get((method, path))
        if exact is not None:
            return exact
        if PLUGIN_EVAL_REPORT.fullmatch(path) is not None:
            return self._routes.get((method, PLUGIN_EVAL_REPORT_PATH))
        asset = STATIC_ASSET.fullmatch(path)
        if asset is None:
            return None
        return self._routes.get((method, STATIC_ASSET_ROUTES[asset.group("extension")]))


class Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True

    def server_bind(self) -> None:
        # HTTPServer resolves the bound address through getfqdn(), which can block on a
        # misconfigured local resolver. Studio binds a literal loopback address and never uses
        # that display name, so keep startup independent of DNS.
        TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = int(self.server_address[1])

    def __init__(self, address: Tuple[str, int], static_root: Path, credential: str, store: Store):
        self.static_root = Path(static_root)
        self.repo_root = self.static_root.parent.parent
        self.control_credential = credential
        self.store = store
        self.instance_epoch = str(uuid.uuid4())
        super().__init__(address, Handler)
        try:
            self.static_files = auth.KnownRoots({"static": self.static_root})
            self.host = "%s.localhost:%d" % (secrets.token_hex(16), self.server_address[1])
            self.sessions = auth.Sessions(self.host)
            self.mutations = MutationExecutor()
            self.target_service = targets.TargetService(self.repo_root)
            self.run_supervisor = self.mutations.call(lambda: runs.RunSupervisor(
                self.store.path, runs.default_catalog_path(self.repo_root),
                target_service=self.target_service))
            self.run_progress = OrderedDict()
            self.live_broker = live_updates.EventBroker(self.instance_epoch)
            self.live_watcher = live_updates.LiveWatcher(
                live_updates.WatchScanner(self.repo_root, self.store.path), self.live_broker)
            self.live_watcher.start()
        except BaseException:
            watcher = getattr(self, "live_watcher", None)
            if watcher is not None:
                watcher.close()
            mutations = getattr(self, "mutations", None)
            if mutations is not None:
                mutations.close()
            super().server_close()
            raise

    def server_close(self):
        live_watcher = getattr(self, "live_watcher", None)
        if live_watcher is not None:
            live_watcher.close()
            self.live_watcher = None
        run_supervisor = getattr(self, "run_supervisor", None)
        if run_supervisor is not None:
            self.mutations.call(run_supervisor.close)
            self.run_supervisor = None
        mutations = getattr(self, "mutations", None)
        if mutations is not None:
            mutations.close()
            self.mutations = None
        static_files = getattr(self, "static_files", None)
        if static_files is not None:
            static_files.close()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server: Server

    def log_message(self, _format, *_args):
        return

    def _control_authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = "Bearer " + self.server.control_credential
        return hmac.compare_digest(supplied, expected)

    def _send(self, code: int, body: bytes, content_type: str,
              extra_headers: Tuple[Tuple[str, str], ...] = (),
              cache_control: str = "no-store",
              content_security_policy: Optional[str] = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        for name, value in auth.SECURITY_HEADERS:
            if name == "Content-Security-Policy" and content_security_policy is not None:
                value = content_security_policy
            self.send_header(name, value)
        for name, value in extra_headers:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, value: Dict[str, object]) -> None:
        body = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        self._send(code, body, "application/json")

    def _error(self, code: int, name: str) -> None:
        payload = {"error": name}
        ROUTES.error_schema.validate(payload)
        body = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        self._send(code, body, ROUTES.error_media_type)

    def send_error(self, code, _message=None, _explain=None):
        if code == 501:
            if not self._host_allowed():
                self._error(403, "request_refused")
                return
            path = urllib.parse.urlsplit(self.path).path
            if not path.startswith("/__studio/control/") and path != BOOTSTRAP and self._session() is None:
                self._error(401, "unauthorized")
                return
            self._error(405, "method_not_allowed")
            return
        self._error(code, "request_refused")

    def _session(self) -> Optional[auth.Session]:
        return self.server.sessions.authenticate(self.headers.get("Cookie"))

    def _host_allowed(self) -> bool:
        supplied = self.headers.get("Host")
        return bool(supplied) and hmac.compare_digest(str(supplied), self.server.host)

    def _dispatch(self) -> None:
        if not self._host_allowed():
            self._error(403, "request_refused")
            return
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.query or parsed.fragment:
            self._error(404, "not_found")
            return
        route = ROUTES.resolve(self.command, parsed.path)
        is_control = parsed.path.startswith("/__studio/control/")
        is_bootstrap = parsed.path == BOOTSTRAP
        if not is_control and not is_bootstrap:
            session = self._session()
            if session is None:
                self._error(401, "unauthorized")
                return
            if self.command not in ("GET", "HEAD"):
                origin = self.headers.get("Origin")
                if (not origin or not hmac.compare_digest(origin, self.server.sessions.origin)
                        or not self.server.sessions.csrf_matches(
                            session, self.headers.get("X-Studio-CSRF"))):
                    self._error(403, "request_refused")
                    return
        if route is None:
            self._error(404, "not_found")
            return
        if route.request_media_type is not None:
            if self.headers.get_content_type() != route.request_media_type:
                self._error(415, "unsupported_media_type")
                return
            maximum = (MAX_MODULE_JSON_BYTES
                       if route.path in ("/api/configure/module/preview",
                                         "/api/configure/module/save")
                       else auth.MAX_JSON_BYTES)
            length = _content_length(self, maximum)
            if length is None:
                self._error(413, "request_refused")
                return
            try:
                value = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeError, ValueError):
                self._error(400, "request_refused")
                return
            if not isinstance(value, dict):
                self._error(400, "request_refused")
                return
            self.request_json = value
        route.handler(self, route)

    def do_HEAD(self):
        self._dispatch()

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()


def _static(handler: Handler, route: Route) -> None:
    try:
        body = handler.server.static_files.read_bytes("static", "index.html")
    except auth.SecurityError:
        handler._send(503, b"Studio bundle is unavailable\n", "text/plain; charset=utf-8")
        return
    if body.count(STYLE_NONCE_MARKER) != 1:
        handler._send(503, b"Studio bundle is unavailable\n", "text/plain; charset=utf-8")
        return
    nonce = secrets.token_urlsafe(24)
    body = body.replace(STYLE_NONCE_MARKER, nonce.encode("ascii"))
    route.response_schema.validate(body)
    handler._send(200, body, route.media_type,
                  content_security_policy=auth.csp_with_style_nonce(nonce))


def _static_asset(handler: Handler, route: Route) -> None:
    path = urllib.parse.urlsplit(handler.path).path.lstrip("/")
    try:
        body = handler.server.static_files.read_bytes(
            "static", path, limit=MAX_STATIC_ASSET_BYTES)
    except auth.SecurityError:
        handler._error(404, "not_found")
        return
    route.response_schema.validate(body)
    handler._send(200, body, route.media_type,
                  cache_control="public, max-age=31536000, immutable")


def _health(handler: Handler, route: Route) -> None:
    if not handler._control_authorized():
        handler._error(401, "unauthorized")
        return
    payload = {"schema_version": SCHEMA_VERSION, "protocol_version": PROTOCOL_VERSION,
               "pid": os.getpid(), "pid_start": handler.server.pid_start,
               "port": handler.server.server_address[1]}
    route.response_schema.validate(payload)
    handler._send(200, (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(),
                  route.media_type)


def _control_bootstrap(handler: Handler, route: Route) -> None:
    if not handler._control_authorized():
        handler._error(401, "unauthorized")
        return
    token, form_name = handler.server.sessions.issue()
    payload = {"token": token, "form_name": form_name}
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _content_length(handler: Handler, maximum: int) -> Optional[int]:
    raw = handler.headers.get("Content-Length")
    try:
        length = int(raw) if raw is not None else -1
    except ValueError:
        return None
    return length if 0 <= length <= maximum else None


def _bootstrap(handler: Handler, route: Route) -> None:
    if handler.headers.get("Cookie") is not None:
        handler._error(403, "request_refused")
        return
    if handler.headers.get("Origin") not in (None, "null"):
        handler._error(403, "request_refused")
        return
    if handler.headers.get_content_type() != "application/x-www-form-urlencoded":
        handler._error(415, "unsupported_media_type")
        return
    length = _content_length(handler, auth.MAX_FORM_BYTES)
    if length is None:
        handler._error(413, "request_refused")
        return
    try:
        values = urllib.parse.parse_qs(handler.rfile.read(length).decode("ascii"),
                                       keep_blank_values=True, strict_parsing=True,
                                       max_num_fields=2)
    except (UnicodeError, ValueError):
        handler._error(400, "request_refused")
        return
    if set(values) != {"token"} or len(values["token"]) != 1:
        handler._error(400, "request_refused")
        return
    consumed = handler.server.sessions.consume(values["token"][0])
    if consumed is None:
        handler._error(401, "unauthorized")
        return
    session, form_name = consumed
    handler.server.store.remove_private(form_name)
    route.response_schema.validate(BOOTSTRAP_SUCCESS)
    cookie = "%s=%s; Path=/; HttpOnly; SameSite=Strict" % (auth.SESSION_COOKIE, session.cookie)
    handler._send(200, BOOTSTRAP_SUCCESS, route.media_type, (("Set-Cookie", cookie),))


def _session_info(handler: Handler, route: Route) -> None:
    session = handler._session()
    if session is None:
        handler._error(401, "unauthorized")
        return
    payload = {"authenticated": True, "csrf_token": session.csrf}
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _required_request(handler: Handler, names: Iterable[str]) -> Optional[Dict[str, object]]:
    payload = getattr(handler, "request_json", {})
    if set(payload) != set(names):
        handler._error(400, "invalid_request")
        return None
    return payload


def _configure_schema(handler: Handler, route: Route) -> None:
    payload = settings.descriptor(handler.server.repo_root)
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _overview(handler: Handler, route: Route) -> None:
    try:
        payload = overview.current()
    except Exception:
        payload = overview.unavailable("Overview sources are unavailable.")
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _selection_read(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("repository", "project_file"))
    if request is None:
        return
    if any(not isinstance(request[name], str) for name in ("repository", "project_file")):
        handler._error(400, "invalid_request")
        return
    try:
        payload = selection.report(handler.server.repo_root, request["repository"],
                                   request["project_file"])
    except selection.SelectionError:
        handler._error(400, "invalid_selection")
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _library(handler: Handler, route: Route) -> None:
    payload = module_library.inventory(handler.server.repo_root)
    payload["repository"] = str(handler.server.repo_root.resolve())
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _activity(handler: Handler, route: Route) -> None:
    request = getattr(handler, "request_json", {})
    try:
        payload = activity.query(handler.server.store.path.parent, request)
    except activity.ActivityError:
        handler._error(400, "invalid_activity_query")
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _runs_catalog(handler: Handler, route: Route) -> None:
    # A read that discovers tests in a subprocess for seconds; on the serial mutation executor
    # it would hold every queued history refetch past the two-second live-update contract.
    try:
        payload = handler.server.run_supervisor.catalog(handler.server.repo_root)
    except runs.RunError:
        handler._error(503, "run_catalog_unavailable")
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _native_adapter(handler: Handler) -> native_acceptance.NativeRunAdapter:
    return native_acceptance.NativeRunAdapter(
        handler.server.repo_root, handler.server.store.path,
        native_acceptance.SupervisorAdmission(
            handler.server.run_supervisor, handler.server.target_service,
            handler.server.store.path / "native-evidence"))


def _native_catalog(handler: Handler, route: Route) -> None:
    try:
        payload = native_acceptance.catalog(handler.server.repo_root)
        platform = "macos" if sys.platform == "darwin" else sys.platform
        available = [item for item in payload["clients"]
                     if item["spend_cap_supported"] and item["platform"] == platform]
        if not available:
            available = [item for item in payload["clients"] if item["spend_cap_supported"]]
        if not available:
            raise native_acceptance.NativeAcceptanceError(
                "native acceptance has no client with an enforceable spend cap")
        initial = native_acceptance.Selection(
            available[0]["id"], tuple(item["id"] for item in payload["cases"][:3]),
            payload["default_model"], "", uuid.uuid4().hex, "installed", "current")
        payload = dict(payload,
                       target={"kind": "installed", "ref": "current",
                               "source_commit": None},
                       initial=initial.public())
    except native_acceptance.NativeAcceptanceError:
        handler._error(503, "native_acceptance_unavailable")
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _native_request(handler: Handler, names: Iterable[str]):
    request = _required_request(handler, names)
    if request is None:
        return None
    if not isinstance(request.get("selection"), dict):
        handler._error(400, "invalid_request")
        return None
    return request


def _native_progress(handler: Handler, route: Route) -> None:
    request = _native_request(handler, ("selection",))
    if request is None:
        return
    try:
        selected = native_acceptance.Selection.parse(
            handler.server.repo_root, request["selection"])
        if not selected.source_commit:
            raise native_acceptance.NativeAcceptanceError(
                "native progress requires a resolved target commit")
        identity = handler.server.mutations.call(
            lambda: _native_adapter(handler).admission.progress(selected.progress_id))
        if (identity["source_commit"] != selected.source_commit
                or identity["kind"] != selected.target_kind
                or identity["ref"] != selected.target_ref):
            raise native_acceptance.NativeAcceptanceError("native target identity changed")
        selected = native_acceptance.Selection.parse(
            Path(identity["root"]), request["selection"])
        run_status = identity["status"]
        path = native_acceptance.progress_path(
            handler.server.store.path / "native-evidence", selected.progress_id)
        payload = native_acceptance.progress_snapshot(
            Path(identity["root"]), path, selected)
        interrupted = (run_status in ("cancelled", "timed_out", "orphaned")
                       and any(item["status"] == "pending" for item in payload["cases"]))
        if interrupted:
            payload["interrupted"] = True
        payload["run_status"] = run_status
    except (native_acceptance.NativeAcceptanceError, runs.RunError):
        handler._error(400, "invalid_native_acceptance")
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _native_preview(handler: Handler, route: Route) -> None:
    request = _native_request(handler, ("selection", "spend"))
    if request is None:
        return
    if not isinstance(request.get("spend"), dict):
        handler._error(400, "invalid_request")
        return
    try:
        payload = handler.server.mutations.call(lambda: _native_adapter(handler).preview(
            request["selection"], request["spend"]))
    except (native_acceptance.NativeAcceptanceError, runs.RunError):
        handler._error(400, "native_acceptance_refused")
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _native_start(handler: Handler, route: Route) -> None:
    request = _native_request(handler, ("selection", "spend", "confirmation_token"))
    if request is None:
        return
    if (not isinstance(request.get("spend"), dict)
            or not isinstance(request.get("confirmation_token"), str)):
        handler._error(400, "invalid_request")
        return
    try:
        payload = handler.server.mutations.call(lambda: _native_adapter(handler).start(
            request["selection"], request["spend"], request["confirmation_token"]))
    except (native_acceptance.NativeAcceptanceError, runs.RunError):
        handler._error(400, "native_acceptance_refused")
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _native_retry(handler: Handler, route: Route) -> None:
    request = _native_request(handler, ("selection", "case"))
    if request is None:
        return
    if not isinstance(request.get("case"), str):
        handler._error(400, "invalid_request")
        return
    try:
        selected = native_acceptance.Selection.parse(
            handler.server.repo_root, request["selection"])
        identity = handler.server.mutations.call(
            lambda: _native_adapter(handler).admission.progress(selected.progress_id))
        if (identity["source_commit"] != selected.source_commit
                or identity["kind"] != selected.target_kind
                or identity["ref"] != selected.target_ref):
            raise native_acceptance.NativeAcceptanceError("native target identity changed")
        selected = native_acceptance.Selection.parse(
            Path(identity["root"]), request["selection"])
        path = native_acceptance.progress_path(
            handler.server.store.path / "native-evidence", selected.progress_id)
        snapshot = native_acceptance.progress_snapshot(Path(identity["root"]), path, selected)
        payload = native_acceptance.failed_retry(
            selected, snapshot, request["case"]).public()
    except (native_acceptance.NativeAcceptanceError, runs.RunError):
        handler._error(400, "invalid_native_acceptance_retry")
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _replay_admission(handler: Handler) -> replay.ReplayAdmission:
    return replay.ReplayAdmission(
        handler.server.repo_root, handler.server.store.path,
        handler.server.run_supervisor, handler.server.target_service)


def _replay_catalog(handler: Handler, route: Route) -> None:
    try:
        payload = replay.task_catalog(handler.server.repo_root)
    except replay.ReplayError:
        handler._error(503, "replay_catalog_unavailable")
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _replay_preview(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("request",))
    if request is None:
        return
    try:
        # Resolving builds both targets (clone and sandboxed sync); it stays off the one
        # mutation thread so cancel and polling are never queued behind it.
        admission = _replay_admission(handler)
        resolved = admission.resolve(request["request"])
        payload = handler.server.mutations.call(lambda: admission.preview_resolved(resolved))
    except replay.ReplayError as exc:
        handler._error(400, getattr(exc, "code", "replay_refused"))
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _replay_start(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("request", "confirmation_token"))
    if request is None:
        return
    if not isinstance(request["confirmation_token"], str):
        handler._error(400, "invalid_request")
        return
    try:
        admission = _replay_admission(handler)
        confirmed = admission.confirm(request["request"])
        payload = handler.server.mutations.call(lambda: admission.start_confirmed(
            confirmed, request["confirmation_token"]))
    except replay.ReplayError as exc:
        handler._error(400, getattr(exc, "code", "replay_refused"))
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _replay_result(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("run_id",))
    if request is None:
        return
    if not isinstance(request["run_id"], str):
        handler._error(400, "invalid_request")
        return
    try:
        def snapshot():
            supervisor = handler.server.run_supervisor
            run = supervisor.show(request["run_id"])
            if run.get("suite_id") != "live-replay":
                raise runs.RunError("run is not a live replay")
            with supervisor.lock():
                private = supervisor._read(request["run_id"])
            selected = replay.ReplayRequest.parse(
                json.loads(private["parameters"]["request_json"]))
            run_root = supervisor._run_path(request["run_id"]).parent
            live_rows = {}
            for index, target in enumerate(selected.targets, 1):
                path = (run_root / "replay" / ("target-%d" % index)
                        / target.execution_ref / replay.RESULTS_NAME)
                live_rows[index] = replay.read_progress_rows(path, target, selected)
            progress = replay.progress_payload(selected, live_rows)
            result = None
            if run.get("status") in runs.TERMINAL:
                summary_path = run_root / "replay" / replay.SUMMARY_NAME
                if summary_path.is_file():
                    summary = replay.read_summary(summary_path)
                    replay.index_native_rows(supervisor.history, run_root, summary)
                    result = replay.result_payload(handler.server.repo_root, run_root,
                                                   selected, summary)
            return {"schema_version": 1,
                    "run": {"run_id": run["run_id"], "status": run["status"]},
                    "progress": progress, "result": result}
        payload = handler.server.mutations.call(snapshot)
    except runs.RunError:
        handler._error(404, "replay_not_found")
        return
    except (ValueError, replay.ReplayError):
        handler._error(409, "replay_result_invalid")
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


# A comparison that cannot be read is the client's to fix (400) or not there yet (404, 409).
_COMPARE_STATUS = {"invalid_request": 400, "compare_not_found": 404}


def _runs_compare(handler: Handler, route: Route) -> None:
    request = _required_request(handler, compare.SIDES)
    if request is None:
        return
    try:
        sides = compare.parse_request(request)
        # Read-only and engine-bound, so it stays off the one mutation thread.
        payload = compare.compare_runs(handler.server.run_supervisor,
                                       handler.server.repo_root, sides)
    except compare.CompareError as exc:
        handler._error(_COMPARE_STATUS.get(exc.code, 409), exc.code)
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


# A draft that is not there is 404; a request that names the wrong draft or checkpoint is the
# client's to plan again (409); every other refusal is the client's to fix (400).
_DRAFT_TEST_STATUS = {"draft_not_found": 404, "draft_test_mismatch": 409, "draft_unavailable": 409,
                      "draft_test_unchanged": 409, "draft_test_records_unsafe": 409,
                      "draft_test_registration_not_found": 404,
                      "draft_test_registration_stale": 409,
                      "draft_test_registration_mismatch": 409,
                      "draft_test_registration_used": 409,
                      "draft_test_underpowered": 409, "draft_test_effect_too_large": 400,
                      "draft_test_cv_not_declared": 400, "draft_test_registration_subset": 400,
                      "draft_test_registration_failed": 500}


def _draft_test_error(handler: Handler, exc: Exception) -> None:
    code = getattr(exc, "code", None) or "draft_test_refused"
    if isinstance(exc, draft_tests.DraftTestError):
        handler._error(_DRAFT_TEST_STATUS.get(code, 400), code)
    else:
        handler._error(400, code if isinstance(exc, replay.ReplayRefusal) else "replay_refused")


def _draft_test_plan(handler: Handler, route: Route) -> None:
    """Power and spend before a draft test, and how it departs from the registration it names:
    nothing starts here, and nothing is claimed."""
    body = getattr(handler, "request_json", {})
    request = _required_request(handler, ("draft", "request", "effect", "cv") + (
        ("registration",) if isinstance(body, dict) and "registration" in body else ()))
    if request is None:
        return
    try:
        planned = draft_tests.parse_plan(request["effect"], request["cv"])
        draft = draft_tests.identity(handler.server.repo_root, request["draft"])
        form = draft_tests.replay_form(draft, request["request"])
        # Power needs only the task count and trials, so it answers before any target build.
        power = draft_tests.power(handler.server.repo_root,
                                  *draft_tests.form_size(form), planned)
        admission = _replay_admission(handler)
        resolved = admission.resolve(form)
        draft_tests.check_request(draft, resolved)
        registration, deviations = draft_tests.start_registration(
            handler.server.run_supervisor.state_root, handler.server.repo_root, draft, resolved,
            request.get("registration"))
        preview = handler.server.mutations.call(lambda: admission.preview_resolved(resolved))
    except (draft_tests.DraftTestError, replay.ReplayError) as exc:
        _draft_test_error(handler, exc)
        return
    payload = {"schema_version": draft_tests.SCHEMA_VERSION, "draft": draft, "power": power,
               "power_line": draft_tests.power_line(power),
               "evidence_note": draft_tests.EXPLORATORY_NOTE, "preview": preview,
               "registration": None if registration is None else {
                   "registration_id": registration, "deviations": deviations}}
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _draft_test_register(handler: Handler, route: Route) -> None:
    """Pre-register a test of the draft's current checkpoint (`citizen draft test --register`);
    nothing runs here, and a registration is never edited afterwards."""
    request = _required_request(handler, ("draft", "request", "effect", "cv"))
    if request is None:
        return
    try:
        root = handler.server.run_supervisor.state_root
        registration = handler.server.mutations.call(lambda: draft_registration.register(
            root, handler.server.repo_root, request["draft"], request["request"],
            request["effect"], request["cv"]))
    except draft_tests.DraftTestError as exc:
        _draft_test_error(handler, exc)
        return
    except OSError:
        handler._error(500, "draft_test_registration_failed")
        return
    payload = {"registration": registration,
               "power_line": draft_tests.power_line(registration["power"])}
    route.response_schema.validate(payload)
    handler._json(200, payload)


_START_KEYS = ("draft", "request", "confirmation_token", "effect", "cv")


def _draft_test_start(handler: Handler, route: Route) -> None:
    """Start the planned pair after its spend confirmation, and record it against the draft,
    with the registration it was started under, if any."""
    body = getattr(handler, "request_json", {})
    request = _required_request(handler, _START_KEYS + (
        ("registration",) if isinstance(body, dict) and "registration" in body else ()))
    if request is None:
        return
    if not isinstance(request["confirmation_token"], str):
        handler._error(400, "invalid_request")
        return
    try:
        planned = draft_tests.parse_plan(request["effect"], request["cv"])
        draft = draft_tests.identity(handler.server.repo_root, request["draft"])
        selected = replay.ReplayRequest.parse(request["request"])
        draft_tests.check_request(draft, selected)
        power = draft_tests.power(handler.server.repo_root, len(selected.tasks),
                                  selected.repetitions, planned)
        registration, deviations = draft_tests.start_registration(
            handler.server.run_supervisor.state_root, handler.server.repo_root, draft, selected,
            request.get("registration"))
        admission = _replay_admission(handler)
        confirmed = admission.confirm(request["request"])
        started = handler.server.mutations.call(lambda: admission.start_confirmed(
            confirmed, request["confirmation_token"]))
    except (draft_tests.DraftTestError, replay.ReplayError) as exc:
        _draft_test_error(handler, exc)
        return
    if registration is not None:
        # Single use: the first run started claims it; a run that loses the race runs exploratory.
        try:
            if not draft_registration.claim(handler.server.run_supervisor.state_root,
                                            registration, started["run_id"]):
                deviations = deviations + ["the registration already backs another run"]
        except (OSError, draft_tests.DraftTestError):
            deviations = deviations + ["the registration's use could not be recorded"]
    try:
        recorded = draft_tests.record(handler.server.run_supervisor.state_root, started["run_id"],
                                      draft, confirmed, power, registration, deviations)
    except (OSError, draft_tests.DraftTestError):
        # The run is admitted and keeps running; only its link to the draft failed.
        handler._error(500, "draft_test_unrecorded")
        return
    payload = {"run_id": started["run_id"], "status": started["status"], "record": recorded}
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _draft_test_verdicts(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("draft",))
    if request is None:
        return
    try:
        supervisor = handler.server.run_supervisor
        # Read-only: run records, replay results and the engine, off the one mutation thread.
        payload = draft_tests.verdicts(supervisor, handler.server.repo_root,
                                       supervisor.state_root, request["draft"])
    except draft_tests.DraftTestError as exc:
        _draft_test_error(handler, exc)
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _eval_admission(handler: Handler) -> eval_tiers.EvalAdmission:
    return eval_tiers.EvalAdmission(
        handler.server.repo_root, handler.server.store.path,
        handler.server.run_supervisor, handler.server.target_service)


_EVAL_STATUS = {"invalid_request": 400, "eval_engine_absent": 404,
                "replay_target_busy": 409, "replay_worktree_dirty": 409,
                "replay_target_config_unsupported": 409, "eval_target_changed": 409}


def _eval_error(handler: Handler, exc: eval_tiers.EvalTierError) -> None:
    handler._error(_EVAL_STATUS.get(exc.code, 400), exc.code)


def _evals_catalog(handler: Handler, route: Route) -> None:
    if _required_request(handler, ()) is None:
        return
    payload = eval_tiers.catalog(handler.server.repo_root)
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _evals_start_free(handler: Handler, route: Route) -> None:
    payload = getattr(handler, "request_json", {})
    if not isinstance(payload, dict) or set(payload) not in ({"suite"}, {"suite", "raw"}):
        handler._error(400, "invalid_request")
        return
    try:
        admission = _eval_admission(handler)
        result = handler.server.mutations.call(
            lambda: admission.start_free(payload["suite"], payload.get("raw")))
    except eval_tiers.EvalTierError as exc:
        _eval_error(handler, exc)
        return
    route.response_schema.validate(result)
    handler._json(200, result)


def _evals_preview(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("request",))
    if request is None:
        return
    try:
        # Resolving builds the target; it stays off the one mutation thread, as a replay's does.
        admission = _eval_admission(handler)
        resolved = admission.resolve(request["request"])
        payload = handler.server.mutations.call(lambda: admission.preview_resolved(resolved))
    except eval_tiers.EvalTierError as exc:
        _eval_error(handler, exc)
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _evals_start(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("request", "confirmation_token"))
    if request is None:
        return
    if not isinstance(request["confirmation_token"], str):
        handler._error(400, "invalid_request")
        return
    try:
        admission = _eval_admission(handler)
        confirmed = admission.confirm(request["request"])
        payload = handler.server.mutations.call(lambda: admission.start_confirmed(
            confirmed, request["confirmation_token"]))
    except eval_tiers.EvalTierError as exc:
        _eval_error(handler, exc)
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _evals_result(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("run_id",))
    if request is None:
        return
    if not isinstance(request["run_id"], str):
        handler._error(400, "invalid_request")
        return
    try:
        payload = handler.server.mutations.call(lambda: eval_tiers.result_payload(
            handler.server.run_supervisor, request["run_id"]))
    except runs.RunError:
        handler._error(404, "eval_not_found")
        return
    except (ValueError, eval_tiers.EvalTierError):
        handler._error(409, "eval_result_invalid")
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _free_run(supervisor: runs.RunSupervisor, run_id: str) -> Dict[str, object]:
    record = supervisor.show(run_id)
    if record.get("suite_id") not in free_suites.FREE_SUITE_IDS:
        raise runs.RunError("run is not a free local suite")
    return record


def _library_source_paths(server: Server):
    payload = module_library.inventory(server.repo_root)
    return frozenset(Path(item["source"]["path"]).resolve() for item in payload["modules"])


def _prune_run_progress(server: Server, now: Optional[float] = None) -> None:
    current = time.monotonic() if now is None else now
    expired = [run_id for run_id, saved in server.run_progress.items()
               if saved["terminal"]
               and current - saved["touched"] >= RUN_PROGRESS_TERMINAL_TTL_SECONDS]
    for run_id in expired:
        server.run_progress.pop(run_id, None)
    while len(server.run_progress) > RUN_PROGRESS_MAX_ENTRIES:
        server.run_progress.popitem(last=False)


def _save_run_progress(server: Server, run_id: str, saved: Dict[str, object],
                       terminal: bool, now: Optional[float] = None) -> None:
    current = time.monotonic() if now is None else now
    saved["touched"] = current
    saved["terminal"] = terminal
    server.run_progress[run_id] = saved
    server.run_progress.move_to_end(run_id)
    _prune_run_progress(server, current)


def _link_lint_findings(server: Server, progress: Dict[str, object]) -> None:
    sources = _library_source_paths(server)
    for finding in progress["lint_findings"]:
        candidate = (server.repo_root / finding["path"]).resolve()
        if candidate not in sources:
            finding["library_href"] = None
            continue
        finding["library_href"] = "/library?path=%s&line=%d" % (
            urllib.parse.quote(finding["path"], safe="/"), finding["line"])


def _run_start(handler: Handler, route: Route) -> None:
    request = _required_request(
        handler, ("suite_id", "parameters", "target_kind", "target_ref"))
    if request is None:
        return
    if (not isinstance(request["suite_id"], str)
            or not isinstance(request["parameters"], dict)
            or any(not isinstance(name, str) or not isinstance(value, str)
                   for name, value in request["parameters"].items())
            or not isinstance(request["target_kind"], str)
            or not isinstance(request["target_ref"], str)):
        handler._error(400, "invalid_request")
        return
    if (request["suite_id"] not in free_suites.FREE_SUITE_IDS
            or request["target_kind"] != "installed"
            or request["target_ref"] != str(handler.server.repo_root.resolve())
            or request["parameters"].get("root") != str(handler.server.repo_root.resolve())):
        handler._error(400, "invalid_run")
        return
    try:
        payload = handler.server.mutations.call(lambda: handler.server.run_supervisor.start(
            request["suite_id"], request["parameters"], request["target_kind"],
            request["target_ref"]))
    except runs.RunError:
        handler._error(400, "invalid_run")
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _run_show(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("run_id",))
    if request is None:
        return
    if not isinstance(request["run_id"], str):
        handler._error(400, "invalid_request")
        return
    try:
        payload = handler.server.mutations.call(
            lambda: _free_run(handler.server.run_supervisor, request["run_id"]))
    except runs.RunError:
        handler._error(404, "run_not_found")
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _run_history(handler: Handler, route: Route) -> None:
    names = ("limit", "cursor", "suite_id", "target", "status", "created_from",
             "created_to", "min_cost_usd", "max_cost_usd", "min_duration_ms",
             "max_duration_ms")
    request = _required_request(handler, names)
    if request is None:
        return
    try:
        payload = handler.server.mutations.call(
            lambda: handler.server.run_supervisor.history_page(**request))
    except (runs.RunError, TypeError):
        handler._error(400, "invalid_history_query")
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _run_detail(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("run_id", "lineage_limit", "lineage_cursor"))
    if request is None:
        return
    try:
        payload = handler.server.mutations.call(
            lambda: handler.server.run_supervisor.run_detail(
                request["run_id"], lineage_limit=request["lineage_limit"],
                lineage_cursor=request["lineage_cursor"]))
    except (runs.RunError, TypeError):
        handler._error(404, "run_not_found")
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _run_case_history(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("case_id", "limit", "cursor"))
    if request is None:
        return
    try:
        payload = handler.server.mutations.call(
            lambda: handler.server.run_supervisor.case_history(
                request["case_id"], limit=request["limit"], cursor=request["cursor"]))
    except (runs.RunError, TypeError):
        handler._error(400, "invalid_case_history_query")
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _run_evidence(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("run_id", "artifact"))
    if request is None:
        return
    try:
        payload = handler.server.mutations.call(
            lambda: handler.server.run_supervisor.evidence(
                request["run_id"], request["artifact"]))
    except (runs.RunError, TypeError):
        handler._error(404, "evidence_unavailable")
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _plugin_eval_report(handler: Handler, route: Route) -> None:
    match = PLUGIN_EVAL_REPORT.fullmatch(urllib.parse.urlsplit(handler.path).path)
    if match is None:
        handler._error(404, "not_found")
        return
    try:
        body = handler.server.mutations.call(
            lambda: handler.server.run_supervisor.plugin_eval_report(match.group("run_id")))
        route.response_schema.validate(body)
    except (runs.RunError, ValueError):
        handler._error(404, "evidence_unavailable")
        return
    handler._send(200, body, route.media_type, content_security_policy=PLUGIN_EVAL_REPORT_CSP)


def _run_rerun(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("run_id",))
    if request is None:
        return
    try:
        payload = handler.server.mutations.call(
            lambda: handler.server.run_supervisor.rerun(request["run_id"]))
    except (runs.RunError, TypeError):
        handler._error(409, "rerun_unavailable")
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _run_cancel(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("run_id",))
    if request is None:
        return
    if not isinstance(request["run_id"], str):
        handler._error(400, "invalid_request")
        return
    try:
        def cancel_free():
            _free_run(handler.server.run_supervisor, request["run_id"])
            return handler.server.run_supervisor.cancel(request["run_id"])
        payload = handler.server.mutations.call(cancel_free)
    except runs.RunError:
        handler._error(404, "run_not_found")
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _run_stream(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("run_id", "stdout_cursor", "stderr_cursor"))
    if request is None:
        return
    if (not isinstance(request["run_id"], str)
            or any(not isinstance(request[name], int) or isinstance(request[name], bool)
                   or request[name] < 0 for name in ("stdout_cursor", "stderr_cursor"))):
        handler._error(400, "invalid_request")
        return
    try:
        def snapshot():
            record = _free_run(handler.server.run_supervisor, request["run_id"])
            stdout = handler.server.run_supervisor.read_output(
                request["run_id"], "stdout", request["stdout_cursor"])
            stderr = handler.server.run_supervisor.read_output(
                request["run_id"], "stderr", request["stderr_cursor"])
            unit_state = None
            if record.get("suite_id") == "unit-tests":
                saved = handler.server.run_progress.get(request["run_id"])
                if saved is None or saved["cursor"] != request["stderr_cursor"]:
                    parser = free_suites.UnitOutputState()
                    replay_cursor = 0
                    while replay_cursor < request["stderr_cursor"]:
                        prior = handler.server.run_supervisor.read_output(
                            request["run_id"], "stderr", replay_cursor,
                            min(runs.MAX_OUTPUT_CHUNK,
                                request["stderr_cursor"] - replay_cursor))
                        if prior["next_cursor"] <= replay_cursor:
                            raise runs.RunError("output cursor could not be replayed")
                        parser.feed(prior["chunk"])
                        replay_cursor = prior["next_cursor"]
                    if replay_cursor != request["stderr_cursor"]:
                        raise runs.RunError("output cursor could not be replayed")
                    saved = {"cursor": replay_cursor, "parser": parser}
                unit_state = saved["parser"]
                saved["cursor"] = stderr["next_cursor"]
                _save_run_progress(handler.server, request["run_id"], saved,
                                   record.get("status") in runs.TERMINAL)
            progress = free_suites.progress_payload(
                record, stdout["chunk"], stderr["chunk"], unit_state, stderr["eof"])
            if (record.get("suite_id") == "unit-tests" and stdout["eof"] and stderr["eof"]):
                handler.server.run_progress.pop(request["run_id"], None)
            if record.get("suite_id") == "lint" and progress["lint_findings"]:
                _link_lint_findings(handler.server, progress)
            return record, stdout, stderr, progress
        record, stdout, stderr, progress = handler.server.mutations.call(snapshot)
    except runs.RunError:
        handler._error(404, "run_not_found")
        return
    stdout["cursor"] = stdout.pop("next_cursor")
    stderr["cursor"] = stderr.pop("next_cursor")
    payload = {"run": record, "stdout": stdout, "stderr": stderr, "progress": progress}
    body = ("event: run\ndata: "
            + json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n\n").encode()
    route.response_schema.validate(body)
    handler._send(200, body, route.media_type)


def _live_stream(handler: Handler, route: Route) -> None:
    cursor = handler.headers.get("Last-Event-ID")
    if cursor is not None and len(cursor) > 256:
        handler._error(400, "invalid_cursor")
        return
    handler.send_response(200)
    handler.send_header("Content-Type", route.media_type)
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Connection", "keep-alive")
    for name, value in auth.SECURITY_HEADERS:
        handler.send_header(name, value)
    handler.end_headers()
    try:
        while not handler.server.live_broker.closed:
            events = handler.server.live_broker.wait(cursor, live_updates.HEARTBEAT_SECONDS)
            if not events:
                handler.wfile.write(b": heartbeat\n\n")
                handler.wfile.flush()
                continue
            for event in events:
                body = live_updates.encode(event)
                route.response_schema.validate(body)
                handler.wfile.write(body)
                handler.wfile.flush()
                cursor = live_updates.event_id(event)
    except (BrokenPipeError, ConnectionError, OSError):
        return
    finally:
        handler.close_connection = True


def _ui_preferences_read(handler: Handler, route: Route) -> None:
    payload = handler.server.store.read_ui_preferences()
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _ui_preferences_write(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("color_scheme",))
    if request is None:
        return
    if request["color_scheme"] not in ("auto", "light", "dark"):
        handler._error(400, "invalid_request")
        return
    payload = {"color_scheme": request["color_scheme"]}
    handler.server.mutations.call(lambda: handler.server.store.write_ui_preferences(payload))
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _configure_read(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("draft",))
    if request is None:
        return
    if not isinstance(request["draft"], str) or not request["draft"]:
        handler._error(400, "invalid_request")
        return
    payload = handler.server.mutations.call(lambda: settings.read(
        handler.server.repo_root, request["draft"],
    ))
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _configure_preview(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("draft", "changes"))
    if request is None:
        return
    if not isinstance(request["draft"], str) or not request["draft"]:
        handler._error(400, "invalid_request")
        return
    payload = handler.server.mutations.call(lambda: settings.preview(
        handler.server.repo_root, request["draft"], request["changes"],
    ))
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _configure_save(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("draft", "base_revision", "idempotency_key", "changes"))
    if request is None:
        return
    strings = (request["draft"], request["base_revision"], request["idempotency_key"])
    if any(not isinstance(value, str) or not value for value in strings):
        handler._error(400, "invalid_request")
        return
    payload = handler.server.mutations.call(lambda: settings.save(
        handler.server.repo_root, request["draft"], request["base_revision"],
        request["idempotency_key"], request["changes"],
    ))
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _draft_selection_read(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("draft",))
    if request is None:
        return
    if not isinstance(request["draft"], str) or not request["draft"]:
        handler._error(400, "invalid_request")
        return
    payload = handler.server.mutations.call(lambda: selection_editing.read(
        handler.server.repo_root, request["draft"],
    ))
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _draft_selection_preview(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("draft", "changes"))
    if request is None:
        return
    if not isinstance(request["draft"], str) or not request["draft"]:
        handler._error(400, "invalid_request")
        return
    payload = handler.server.mutations.call(lambda: selection_editing.preview(
        handler.server.repo_root, request["draft"], request["changes"],
    ))
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _draft_selection_save(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("draft", "base_revision", "idempotency_key", "changes"))
    if request is None:
        return
    strings = (request["draft"], request["base_revision"], request["idempotency_key"])
    if any(not isinstance(value, str) or not value for value in strings):
        handler._error(400, "invalid_request")
        return
    payload = handler.server.mutations.call(lambda: selection_editing.save(
        handler.server.repo_root, request["draft"], request["base_revision"],
        request["idempotency_key"], request["changes"],
    ))
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _draft_module_read(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("draft", "module"))
    if request is None:
        return
    if any(not isinstance(value, str) for value in (request["draft"], request["module"])) \
            or not request["draft"]:
        handler._error(400, "invalid_request")
        return
    payload = handler.server.mutations.call(lambda: module_editing.read(
        handler.server.repo_root, request["draft"], request["module"],
    ))
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _draft_module_preview(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("draft", "module", "content"))
    if request is None:
        return
    if any(not isinstance(request[name], str) or not request[name]
           for name in ("draft", "module")) or not isinstance(request["content"], str):
        handler._error(400, "invalid_request")
        return
    payload = module_editing.preview(
        handler.server.repo_root, request["draft"], request["module"], request["content"],
    )
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _draft_module_save(handler: Handler, route: Route) -> None:
    request = _required_request(handler, (
        "draft", "module", "base_revision", "source_digest", "idempotency_key", "content",
    ))
    if request is None:
        return
    if any(not isinstance(request[name], str) or not request[name]
           for name in ("draft", "module", "base_revision", "source_digest", "idempotency_key")) \
            or not isinstance(request["content"], str):
        handler._error(400, "invalid_request")
        return
    payload = handler.server.mutations.call(lambda: module_editing.save(
        handler.server.repo_root, request["draft"], request["module"],
        request["base_revision"], request["source_digest"], request["idempotency_key"],
        request["content"],
    ))
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _draft_authoring_read(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("draft",))
    if request is None:
        return
    if not isinstance(request["draft"], str) or not request["draft"]:
        handler._error(400, "invalid_request")
        return
    payload = handler.server.mutations.call(lambda: module_authoring.read(
        handler.server.repo_root, request["draft"],
    ))
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _draft_authoring_preview(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("draft", "request"))
    if request is None:
        return
    if not isinstance(request["draft"], str) or not request["draft"] \
            or not isinstance(request["request"], dict):
        handler._error(400, "invalid_request")
        return
    payload = module_authoring.preview(handler.server.repo_root, request["draft"], request["request"])
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _draft_authoring_save(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("draft", "base_revision", "idempotency_key", "request"))
    if request is None:
        return
    if any(not isinstance(request[name], str) or not request[name]
           for name in ("draft", "base_revision", "idempotency_key")) \
            or not isinstance(request["request"], dict):
        handler._error(400, "invalid_request")
        return
    payload = handler.server.mutations.call(lambda: module_authoring.save(
        handler.server.repo_root, request["draft"], request["base_revision"],
        request["idempotency_key"], request["request"],
    ))
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _draft_library(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("draft",))
    if request is None:
        return
    if not isinstance(request["draft"], str) or not request["draft"]:
        handler._error(400, "invalid_request")
        return
    try:
        payload = handler.server.mutations.call(lambda: module_authoring.library(
            handler.server.repo_root, request["draft"],
        ))
    except (drafts.DraftError, module_editing.ModuleEditError) as exc:
        handler._error(409, exc.code)
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


_REVIEW_SLOTS = threading.BoundedSemaphore(1)


def _draft_apply_review(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("draft",))
    if request is None:
        return
    if not isinstance(request["draft"], str) or not request["draft"]:
        handler._error(400, "invalid_request")
        return
    # A review runs lint and `config set` processes for up to minutes, so one runs at a time and
    # a second is refused rather than queued. It stays off the mutation lane: a review is
    # read-only and lock-free, and never makes a save beside it fail busy.
    if not _REVIEW_SLOTS.acquire(blocking=False):
        handler._error(429, "review_busy")
        return
    try:
        payload = draft_apply.review(handler.server.repo_root, request["draft"])
    finally:
        _REVIEW_SLOTS.release()
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _run_draft_apply(repo_root: Path, draft: str, revision: str) -> Dict[str, object]:
    """Run `citizen draft apply` itself, so a Studio apply takes the CLI's locks in the CLI."""
    environment = {key: value for key, value in os.environ.items() if key != "HARNESS_QUIET"}
    command = [sys.executable, str(repo_root / "bin" / "harness"), "draft", "apply", draft,
               "--revision", revision, "--via-studio", "--json"]
    try:
        done = subprocess.run(command, cwd=str(repo_root), env=environment, capture_output=True,
                              text=True, timeout=1800)
        payload = json.loads(done.stdout.strip().splitlines()[-1])
        if not isinstance(payload, dict):
            raise ValueError("apply did not answer with an object")
        return payload
    except (OSError, IndexError, ValueError, subprocess.SubprocessError):
        return draft_apply._result("failed", "apply-unavailable",
                                   "citizen draft apply did not report a result; check Activity "
                                   "and `citizen doctor` before retrying")


def _run_draft_recover(repo_root: Path, action: str, draft: str) -> Dict[str, object]:
    """Run `citizen draft recover` itself, under the CLI's own locks."""
    environment = {key: value for key, value in os.environ.items() if key != "HARNESS_QUIET"}
    command = [sys.executable, str(repo_root / "bin" / "harness"), "draft", "recover",
               "--draft", draft, "--via-studio", "--json"] + (["--abandon"] if action == "abandon" else [])
    try:
        done = subprocess.run(command, cwd=str(repo_root), env=environment, capture_output=True,
                              text=True, timeout=1800)
        payload = json.loads(done.stdout.strip().splitlines()[-1])
        if not isinstance(payload, dict):
            raise ValueError("recover did not answer with an object")
        return payload
    except (OSError, IndexError, ValueError, subprocess.SubprocessError):
        return draft_apply._result("failed", "recover-unavailable",
                                   "citizen draft recover did not report a result; run it from a "
                                   "terminal to see why")


def _draft_recover(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("action", "confirm"))
    if request is None:
        return
    if request["action"] not in ("restore", "abandon") or not isinstance(request["confirm"], str) \
            or not request["confirm"]:
        handler._error(400, "invalid_request")
        return
    # `confirm` is the interrupted apply's draft, typed back; the CLI refuses any other draft.
    payload = handler.server.mutations.call(lambda: _run_draft_recover(
        handler.server.repo_root, request["action"], request["confirm"],
    ))
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _run_draft_rollback(repo_root: Path, apply_id: str, draft: str) -> Dict[str, object]:
    """Run `citizen draft rollback` itself, under the CLI's own locks."""
    environment = {key: value for key, value in os.environ.items() if key != "HARNESS_QUIET"}
    command = [sys.executable, str(repo_root / "bin" / "harness"), "draft", "rollback", apply_id,
               "--draft", draft, "--via-studio", "--json"]
    try:
        done = subprocess.run(command, cwd=str(repo_root), env=environment, capture_output=True,
                              text=True, timeout=1800)
        payload = json.loads(done.stdout.strip().splitlines()[-1])
        if not isinstance(payload, dict):
            raise ValueError("rollback did not answer with an object")
        return payload
    except (OSError, IndexError, ValueError, subprocess.SubprocessError):
        return draft_apply._result("failed", "rollback-unavailable",
                                   "citizen draft rollback did not report a result; check Activity "
                                   "and `citizen doctor` before retrying")


def _draft_rollback_preview(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("apply_id",))
    if request is None:
        return
    if not isinstance(request["apply_id"], str) or not draft_rollback.APPLY_ID.match(request["apply_id"]):
        handler._error(400, "invalid_request")
        return
    payload = draft_rollback.preview(request["apply_id"])
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _draft_rollback(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("apply_id", "confirm"))
    if request is None:
        return
    if not isinstance(request["apply_id"], str) or not draft_rollback.APPLY_ID.match(request["apply_id"]) \
            or not isinstance(request["confirm"], str) or not request["confirm"]:
        handler._error(400, "invalid_request")
        return
    # `confirm` is the applied draft's name, typed back; the CLI refuses any other draft.
    payload = handler.server.mutations.call(lambda: _run_draft_rollback(
        handler.server.repo_root, request["apply_id"], request["confirm"],
    ))
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _draft_apply(handler: Handler, route: Route) -> None:
    request = _required_request(handler, ("draft", "revision", "confirm"))
    if request is None:
        return
    if any(not isinstance(request[name], str) or not request[name]
           for name in ("draft", "revision", "confirm")):
        handler._error(400, "invalid_request")
        return
    if not hmac.compare_digest(request["confirm"].encode("utf-8"), request["draft"].encode("utf-8")):
        # Applying changes the live harness, so the request must name the draft it applies.
        handler._error(400, "confirmation_required")
        return
    payload = handler.server.mutations.call(lambda: _run_draft_apply(
        handler.server.repo_root, request["draft"], request["revision"],
    ))
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _first_run_request(handler: Handler) -> Optional[str]:
    request = _required_request(handler, ("draft",))
    if request is None:
        return None
    if not first_run.valid_name(request["draft"]):
        handler._error(400, "invalid_request")
        return None
    return str(request["draft"])


def _first_run_status(handler: Handler, route: Route) -> None:
    name = _first_run_request(handler)
    if name is None:
        return
    try:
        payload = first_run.status(handler.server.repo_root, name)
    except first_run.FirstRunError as exc:
        _first_run_error(handler, exc)
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _first_run_error(handler: Handler, exc: "first_run.FirstRunError") -> None:
    # Busy is the draft's writer lock held by a save's checks: the caller retries shortly.
    if exc.code == "busy":
        handler._error(429, "first_run_busy")
    else:
        handler._error(409, exc.code)


CREATE_TIMEOUT = 120


def _run_draft_create(repo_root: Path, draft: str) -> str:
    """Run `citizen draft create` itself, so the draft is the CLI's managed worktree.

    The CLI runs in its own session, so a timeout stops its git children with it and the
    cleanup that follows never races a checkout still in progress.
    """
    environment = {key: value for key, value in os.environ.items() if key != "HARNESS_QUIET"}
    command = [sys.executable, str(repo_root / "bin" / "harness"), "draft", "create", draft, "--json"]
    try:
        child = subprocess.Popen(command, cwd=str(repo_root), env=environment, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, start_new_session=True)
    except OSError:
        return "create-unavailable"
    try:
        stdout, _stderr = child.communicate(timeout=CREATE_TIMEOUT)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except OSError:
            pass
        child.communicate()
        return "create-timeout"
    try:
        payload = json.loads(stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        return "create-unavailable"
    if not isinstance(payload, dict):
        return "create-unavailable"
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("code") or "create-failed")
    return ""


def _clear_partial_draft(repo_root: Path, name: str) -> None:
    outcome = first_run.clear_partial(repo_root, name)
    if outcome == first_run.FAILED:
        raise first_run.FirstRunError("create-cleanup-failed", "a half-created draft could not be removed")
    if outcome == first_run.KEPT:
        raise first_run.FirstRunError("partial-draft-kept", "a leftover draft branch may hold work")


def _first_run_start(handler: Handler, route: Route) -> None:
    name = _first_run_request(handler)
    if name is None:
        return

    def start() -> Dict[str, object]:
        # Resuming is starting again: an existing draft of this name is the run to continue.
        current = first_run.status(handler.server.repo_root, name)
        if current["draft"]:
            return current
        # A create an earlier timeout killed may have left a branch with no draft state.
        _clear_partial_draft(handler.server.repo_root, name)
        failure = _run_draft_create(handler.server.repo_root, name)
        if failure:
            _clear_partial_draft(handler.server.repo_root, name)
            raise first_run.FirstRunError(failure, "the first-run draft could not be created")
        return first_run.status(handler.server.repo_root, name)

    try:
        payload = handler.server.mutations.call(start)
    except first_run.FirstRunError as exc:
        _first_run_error(handler, exc)
        return
    route.response_schema.validate(payload)
    handler._json(200, payload)


def _stop(handler: Handler, route: Route) -> None:
    if not handler._control_authorized():
        handler._error(401, "unauthorized")
        return
    payload = {"stopping": True}
    route.response_schema.validate(payload)
    handler._send(200, b'{"stopping":true}\n', route.media_type)
    handler.server.shutdown()


HTML = ResponseSchema("html-document")
BINARY = ResponseSchema("binary")
BOOTSTRAP_SUCCESS = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta http-equiv="refresh" content="0; url=/">
<title>Opening Model Citizen Studio</title></head><body>
<p>Session established. <a href="/">Continue to Studio</a>.</p></body></html>
"""
HEALTH = ResponseSchema("json-object", (("schema_version", "integer"),
                                         ("protocol_version", "integer"),
                                         ("pid", "integer"),
                                         ("pid_start", "string"),
                                         ("port", "integer")))
STOP = ResponseSchema("json-object", (("stopping", "boolean"),))
BOOTSTRAP_CONTROL = ResponseSchema("json-object", (("token", "string"), ("form_name", "string")))
SESSION = ResponseSchema("json-object", (("authenticated", "boolean"), ("csrf_token", "string")))
UI_PREFERENCES = ResponseSchema("json-object", (("color_scheme", "string"),))
OVERVIEW = ResponseSchema("json-object", (("schema_version", "integer"),
                                             ("generated_at", "string"),
                                             ("installed", "object"),
                                             ("release", "object"),
                                             ("mode", "object"),
                                             ("doctor", "object"),
                                             ("drift", "object"),
                                             ("runs", "object"),
                                             ("commands", "object")))
CONFIGURE_SCHEMA = ResponseSchema("json-object", (("schema_version", "integer"),
                                                    ("commands", "object"), ("sections", "array")))
SELECTION_REPORT = ResponseSchema("json-object", (("schema_version", "integer"),
                                                    ("repository", "string"),
                                                    ("project_file", "string"),
                                                    ("selection", "object"), ("mode", "object"),
                                                    ("groups", "array"), ("budgets", "array"),
                                                    ("commands", "object")))
CONFIGURE_READ = ResponseSchema("json-object", (("status", "string"), ("message", "string"),
                                                  ("draft", "object"), ("values", "object"),
                                                  ("warnings", "array")))
CONFIGURE_PREVIEW = ResponseSchema("json-object", (("valid", "boolean"), ("errors", "array"),
                                                     ("warnings", "array"), ("changed", "array"),
                                                     ("preview", "object"),
                                                     ("base_revision", "string")))
CONFIGURE_SAVE = ResponseSchema("json-object", CONFIGURE_PREVIEW.fields +
                                (("saved", "boolean"), ("result", "object-or-null")))
DRAFT_SELECTION_READ = ResponseSchema("json-object", (("status", "string"),
                                                         ("message", "string"),
                                                         ("draft", "object"),
                                                         ("controls", "object"),
                                                         ("current", "object")))
DRAFT_SELECTION_PREVIEW = ResponseSchema("json-object", (("valid", "boolean"),
                                                            ("error", "string"),
                                                            ("error_code", "string"),
                                                            ("changed", "array"),
                                                            ("unchanged", "boolean"),
                                                            ("base_revision", "string"),
                                                            ("before", "object"),
                                                            ("after", "object"),
                                                            ("controls", "object"),
                                                            ("applied", "array")))
DRAFT_SELECTION_SAVE = ResponseSchema("json-object", DRAFT_SELECTION_PREVIEW.fields +
                                       (("saved", "boolean"), ("result", "object-or-null")))
MODULE_READ = ResponseSchema("json-object", (("status", "string"), ("message", "string"),
                                              ("draft", "object"), ("modules", "array"),
                                              ("module", "object-or-null"), ("content", "string"),
                                              ("source_digest", "string"),
                                              ("nothing_applied", "boolean"),
                                              ("error_code", "string")))
MODULE_PREVIEW = ResponseSchema("json-object", (("valid", "boolean"), ("error", "string"),
                                                 ("error_code", "string"),
                                                 ("base_revision", "string"),
                                                 ("source_digest", "string"),
                                                 ("content_digest", "string"),
                                                 ("unchanged", "boolean"),
                                                 ("module", "object-or-null"),
                                                 ("diagnostics", "array"),
                                                 ("budgets", "array"),
                                                 ("projections", "array"),
                                                 ("nothing_applied", "boolean")))
MODULE_SAVE = ResponseSchema("json-object", MODULE_PREVIEW.fields +
                              (("saved", "boolean"), ("result", "object-or-null"),
                               ("saved_lint", "array")))
AUTHORING_READ = ResponseSchema("json-object", (("status", "string"), ("message", "string"),
                                                 ("draft", "object"), ("root", "object-or-null"),
                                                 ("offer", "object"), ("templates", "array"),
                                                 ("forkable", "array"),
                                                 ("nothing_applied", "boolean"),
                                                 ("error_code", "string")))
AUTHORING_PREVIEW = ResponseSchema("json-object", (("valid", "boolean"), ("error", "string"),
                                                    ("error_code", "string"),
                                                    ("base_revision", "string"),
                                                    ("action", "string"),
                                                    ("module", "object-or-null"),
                                                    ("root", "object-or-null"),
                                                    ("files", "array"),
                                                    ("manifest", "object-or-null"),
                                                    ("fork", "object-or-null"),
                                                    ("config_changes", "array"),
                                                    ("findings", "array"),
                                                    ("nothing_applied", "boolean")))
AUTHORING_SAVE = ResponseSchema("json-object", AUTHORING_PREVIEW.fields +
                                 (("saved", "boolean"), ("result", "object-or-null")))
APPLY_REVIEW = ResponseSchema("json-object", (("schema_version", "integer"),
                                               ("draft", "object"), ("destination", "string"),
                                               ("files", "array"), ("root", "array"),
                                               ("config", "array"), ("checks", "object"),
                                               ("budget", "object-or-null"),
                                               ("commands", "array"),
                                               ("core", "object-or-null"),
                                               ("interrupted", "object-or-null"),
                                               ("refusals", "array"), ("can_apply", "boolean"),
                                               ("apply_command", "string"),
                                               ("nothing_applied", "boolean")))
APPLY_RESULT = ResponseSchema("json-object", (("schema_version", "integer"),
                                               ("status", "string"), ("applied", "boolean"),
                                               ("error_code", "string"), ("message", "string"),
                                               ("holder", "string"), ("apply_id", "string"),
                                               ("review", "object"), ("doctor", "object"),
                                               ("restored", "boolean"), ("log", "array")))
ROLLBACK_PREVIEW = ResponseSchema("json-object", (("schema_version", "integer"),
                                                   ("apply", "object"), ("destination", "string"),
                                                   ("config", "array"), ("files", "array"),
                                                   ("commands", "array"), ("refusals", "array"),
                                                   ("can_rollback", "boolean"),
                                                   ("rollback_command", "string"),
                                                   ("nothing_changed", "boolean")))
LIBRARY = ResponseSchema("json-object", (("schema_version", "integer"),
                                          ("repository", "string"),
                                          ("modules", "array"), ("summary", "object")))
ACTIVITY = ResponseSchema("json-object", (("schema_version", "integer"),
                                           ("entries", "array"),
                                           ("next_cursor", "string"),
                                           ("next_command", "string"),
                                           ("sources", "array"),
                                           ("filters", "object"),
                                           ("command", "string")))
RUN_CATALOG = ResponseSchema("json-object", (("schema_version", "integer"),
                                              ("target", "object"), ("suites", "array"),
                                              ("unit_tests", "object"),
                                              ("commands", "object")))
RUN_RECORD = ResponseSchema("run-record")
RUN_HISTORY = ResponseSchema("json-object", (("items", "array"),
                                                ("next_cursor", "string-or-null")))
RUN_DETAIL = ResponseSchema("json-object", (
    ("run_id", "string"), ("suite_id", "string"), ("status", "string"),
    ("created_at", "string-or-null"), ("completed_at", "string-or-null"),
    ("target", "object"), ("cost_usd", "number-or-null"),
    ("duration_ms", "integer-or-null"), ("rerun_of", "string-or-null"),
    ("case_count", "integer"), ("flaky_count", "integer"), ("cases", "array"),
    ("reruns", "object"), ("exact_command", "string-or-null"), ("rerun", "object"),
    ("artifacts", "array"), ("evaluation", "object-or-null")))
CASE_HISTORY = ResponseSchema("json-object", (("case_id", "string"),
                                                 ("items", "array"),
                                                 ("next_cursor", "string-or-null")))
RUN_EVIDENCE = ResponseSchema("json-object", (("run_id", "string"),
                                                 ("artifact", "string"),
                                                 ("content", "string")))
RUN_STREAM = ResponseSchema("sse-stream")
LIVE_STREAM = ResponseSchema("live-sse-stream")
NATIVE_CATALOG = ResponseSchema("json-object", (("schema_version", "integer"),
                                                  ("clients", "array"), ("cases", "array"),
                                                  ("default_model", "string"),
                                                  ("commands", "object"), ("target", "object"),
                                                  ("initial", "object")))
NATIVE_SNAPSHOT = ResponseSchema("json-object", (("schema_version", "integer"),
                                                   ("selection", "object"), ("cases", "array"),
                                                   ("settled_count", "integer"),
                                                   ("eligible_count", "integer"),
                                                   ("interrupted", "boolean"),
                                                   ("warnings", "array"),
                                                   ("resume_cases", "array"),
                                                   ("command", "string"),
                                                   ("run_status", "string")))
NATIVE_PREVIEW = ResponseSchema("json-object", (("estimate", "object"),
                                                  ("caps", "object"), ("pricing", "object"),
                                                  ("confirmation_required", "boolean"),
                                                  ("confirmation_token", "string"),
                                                  ("case_identities", "array"),
                                                  ("selection", "object"), ("target", "object")))
NATIVE_RUN = ResponseSchema("json-object", (("run_id", "string"), ("status", "string"),
                                              ("selection", "object"), ("target", "object")))
NATIVE_SELECTION = ResponseSchema("json-object", (("client", "string"), ("cases", "array"),
                                                    ("model", "string"),
                                                    ("source_commit", "string"),
                                                    ("progress_id", "string"),
                                                    ("target_kind", "string"),
                                                    ("target_ref", "string"),
                                                    ("retry_source", "string"),
                                                    ("retry_case", "string")))
REPLAY_CATALOG = ResponseSchema("json-object", (("schema_version", "integer"),
                                                  ("tasks", "array"),
                                                  ("packs", "array"),
                                                  ("default_pack", "string-or-null"),
                                                  ("target_kinds", "array"),
                                                  ("default_model", "string"),
                                                  ("commands", "object")))
REPLAY_PREVIEW = ResponseSchema("json-object", (("estimate", "object"),
                                                  ("caps", "object"),
                                                  ("pricing", "object"),
                                                  ("confirmation_required", "boolean"),
                                                  ("confirmation_token", "string"),
                                                  ("cost_class", "string"),
                                                  ("case_identities", "array"),
                                                  ("valid", "boolean"),
                                                  ("errors", "array"),
                                                  ("request", "object"),
                                                  ("command", "string"),
                                                  ("sampling", "object")))
EVAL_CATALOG = ResponseSchema("json-object", (("schema_version", "integer"),
                                                ("tiers", "array"), ("units", "array"),
                                                ("unit_model", "string"),
                                                ("commands", "object")))
EVAL_PREVIEW = ResponseSchema("json-object", (("estimate", "object"), ("caps", "object"),
                                                ("pricing", "object"),
                                                ("confirmation_required", "boolean"),
                                                ("confirmation_token", "string"),
                                                ("cost_class", "string"),
                                                ("case_identities", "array"),
                                                ("request", "object"),
                                                ("evidence", "string"),
                                                ("command", "string")))
EVAL_FREE_RUN = ResponseSchema("json-object", (("run_id", "string"), ("status", "string"),
                                                 ("suite", "string"), ("command", "string")))
EVAL_RUN = ResponseSchema("json-object", (("run_id", "string"), ("status", "string"),
                                            ("suite", "string"), ("request", "object")))
EVAL_RESULT = ResponseSchema("json-object", (("schema_version", "integer"),
                                               ("run", "object"),
                                               ("result", "object-or-null"),
                                               ("analysis_error", "string-or-null")))
REPLAY_RUN = ResponseSchema("json-object", (("run_id", "string"),
                                              ("status", "string"),
                                              ("targets", "array")))
FIRST_RUN = ResponseSchema("json-object", (("schema_version", "integer"),
                                            ("state", "string"),
                                            ("fresh", "boolean"),
                                            ("nothing_live_changed", "boolean"),
                                            ("draft_name", "string"),
                                            ("draft", "object"),
                                            ("applied", "object"),
                                            ("interrupted", "object"),
                                            ("blocked_by", "object"),
                                            ("steps", "array"),
                                            ("choices", "array"),
                                            ("commands", "object")))
REPLAY_RESULT = ResponseSchema("json-object", (("schema_version", "integer"),
                                                 ("run", "object"),
                                                 ("progress", "array"),
                                                 ("result", "object-or-null")))
RUNS_COMPARE = ResponseSchema("json-object", (("schema_version", "integer"),
                                                ("engine", "string"), ("control", "string"),
                                                ("base", "object"), ("candidate", "object"),
                                                ("key_match", "boolean"),
                                                ("comparable", "boolean"),
                                                ("refusals", "array"),
                                                ("stale", "array"),
                                                ("notes", "array"),
                                                ("direction_withheld", "array"),
                                                ("preferred", "object"),
                                                ("result", "object-or-null"),
                                                ("error", "string-or-null")))
DRAFT_TEST_PLAN = ResponseSchema("json-object", (("schema_version", "integer"),
                                                   ("draft", "object"), ("power", "object"),
                                                   ("power_line", "string"),
                                                   ("evidence_note", "string"),
                                                   ("preview", "object"),
                                                   ("registration", "object-or-null")))
DRAFT_TEST_REGISTER = ResponseSchema("json-object", (("registration", "object"),
                                                       ("power_line", "string")))
DRAFT_TEST_RUN = ResponseSchema("json-object", (("run_id", "string"), ("status", "string"),
                                                  ("record", "object")))
DRAFT_TEST_VERDICTS = ResponseSchema("json-object", (("schema_version", "integer"),
                                                       ("draft", "string"), ("revision", "string"),
                                                       ("base_revision", "string"),
                                                       ("evidence_note", "string"),
                                                       ("tests", "array"),
                                                       ("checkpoints", "array"),
                                                       ("registrations", "array"),
                                                       ("unreadable_records", "integer")))
ROUTES = RouteRegistry((
    Route("GET", "/", "text/html; charset=utf-8", HTML, _static, "static"),
    Route("HEAD", "/", "text/html; charset=utf-8", HTML, _static, "static"),
    Route("GET", "/index.html", "text/html; charset=utf-8", HTML, _static, "static"),
    Route("HEAD", "/index.html", "text/html; charset=utf-8", HTML, _static, "static"),
    Route("GET", STATIC_ASSET_ROUTES["css"], "text/css; charset=utf-8",
          BINARY, _static_asset, "static"),
    Route("HEAD", STATIC_ASSET_ROUTES["css"], "text/css; charset=utf-8",
          BINARY, _static_asset, "static"),
    Route("GET", STATIC_ASSET_ROUTES["js"], "text/javascript; charset=utf-8",
          BINARY, _static_asset, "static"),
    Route("HEAD", STATIC_ASSET_ROUTES["js"], "text/javascript; charset=utf-8",
          BINARY, _static_asset, "static"),
    Route("POST", BOOTSTRAP, "text/html; charset=utf-8", HTML, _bootstrap, "bootstrap"),
    Route("GET", "/api/session", "application/json", SESSION, _session_info, "transport"),
    Route("POST", "/api/session", "application/json", SESSION, _session_info, "transport",
          "application/json"),
    Route("GET", "/api/ui/preferences", "application/json", UI_PREFERENCES,
          _ui_preferences_read, "ui-preferences"),
    Route("POST", "/api/ui/preferences", "application/json", UI_PREFERENCES,
          _ui_preferences_write, "ui-preferences", "application/json"),
    Route("GET", "/api/overview", "application/json", OVERVIEW,
          _overview, None, cli_command=("citizen", "doctor")),
    Route("GET", "/api/configure/schema", "application/json", CONFIGURE_SCHEMA,
          _configure_schema, None, cli_command=settings.CLI_COMMANDS["schema"]),
    Route("POST", "/api/selection", "application/json", SELECTION_REPORT,
          _selection_read, None, "application/json", selection.CLI_COMMANDS["selection"]),
    Route("POST", "/api/configure/read", "application/json", CONFIGURE_READ,
          _configure_read, None, "application/json", settings.CLI_COMMANDS["read"]),
    Route("POST", "/api/configure/preview", "application/json", CONFIGURE_PREVIEW,
          _configure_preview, None, "application/json", settings.CLI_COMMANDS["preview"]),
    Route("POST", "/api/configure/save", "application/json", CONFIGURE_SAVE,
          _configure_save, None, "application/json", settings.CLI_COMMANDS["save"]),
    Route("POST", "/api/configure/selection/read", "application/json", DRAFT_SELECTION_READ,
          _draft_selection_read, None, "application/json", selection_editing.CLI_COMMANDS["read"]),
    Route("POST", "/api/configure/selection/preview", "application/json", DRAFT_SELECTION_PREVIEW,
          _draft_selection_preview, None, "application/json", selection_editing.CLI_COMMANDS["preview"]),
    Route("POST", "/api/configure/selection/save", "application/json", DRAFT_SELECTION_SAVE,
          _draft_selection_save, None, "application/json", selection_editing.CLI_COMMANDS["save"]),
    Route("POST", "/api/configure/module/read", "application/json", MODULE_READ,
          _draft_module_read, None, "application/json", module_editing.CLI_COMMANDS["read"]),
    Route("POST", "/api/configure/module/preview", "application/json", MODULE_PREVIEW,
          _draft_module_preview, None, "application/json", module_editing.CLI_COMMANDS["preview"]),
    Route("POST", "/api/configure/module/save", "application/json", MODULE_SAVE,
          _draft_module_save, None, "application/json", module_editing.CLI_COMMANDS["save"]),
    Route("POST", "/api/configure/authoring/read", "application/json", AUTHORING_READ,
          _draft_authoring_read, None, "application/json", module_authoring.CLI_COMMANDS["read"]),
    Route("POST", "/api/configure/authoring/preview", "application/json", AUTHORING_PREVIEW,
          _draft_authoring_preview, None, "application/json", module_authoring.CLI_COMMANDS["preview"]),
    Route("POST", "/api/configure/authoring/save", "application/json", AUTHORING_SAVE,
          _draft_authoring_save, None, "application/json", module_authoring.CLI_COMMANDS["save"]),
    Route("POST", "/api/configure/authoring/library", "application/json", LIBRARY,
          _draft_library, None, "application/json", module_authoring.CLI_COMMANDS["library"]),
    Route("POST", "/api/configure/apply/review", "application/json", APPLY_REVIEW,
          _draft_apply_review, None, "application/json", draft_apply.CLI_COMMANDS["review"]),
    Route("POST", "/api/configure/apply", "application/json", APPLY_RESULT,
          _draft_apply, None, "application/json", draft_apply.CLI_COMMANDS["apply"]),
    Route("POST", "/api/configure/apply/recover", "application/json", APPLY_RESULT,
          _draft_recover, None, "application/json", draft_apply.CLI_COMMANDS["recover"]),
    Route("POST", "/api/first-run", "application/json", FIRST_RUN,
          _first_run_status, None, "application/json", first_run.CLI_COMMANDS["status"]),
    Route("POST", "/api/first-run/start", "application/json", FIRST_RUN,
          _first_run_start, None, "application/json", first_run.CLI_COMMANDS["start"]),
    Route("POST", "/api/configure/apply/rollback/preview", "application/json", ROLLBACK_PREVIEW,
          _draft_rollback_preview, None, "application/json", draft_rollback.CLI_COMMANDS["preview"]),
    Route("POST", "/api/configure/apply/rollback", "application/json", APPLY_RESULT,
          _draft_rollback, None, "application/json", draft_rollback.CLI_COMMANDS["rollback"]),
    Route("GET", "/api/library", "application/json", LIBRARY,
          _library, None, cli_command=("citizen", "catalog", "--json")),
    Route("POST", "/api/activity", "application/json", ACTIVITY,
          _activity, None, "application/json", ("citizen", "activity", "--json")),
    Route("GET", "/api/runs/catalog", "application/json", RUN_CATALOG,
          _runs_catalog, None, cli_command=("citizen", "runs", "catalog", "--json")),
    Route("POST", "/api/runs/start", "application/json", RUN_RECORD,
          _run_start, None, "application/json", ("citizen", "runs", "start")),
    Route("POST", "/api/runs/show", "application/json", RUN_RECORD,
          _run_show, None, "application/json", ("citizen", "runs", "show")),
    Route("POST", "/api/runs/history", "application/json", RUN_HISTORY,
          _run_history, None, "application/json", ("citizen", "runs", "history", "--json")),
    Route("POST", "/api/runs/detail", "application/json", RUN_DETAIL,
          _run_detail, None, "application/json", ("citizen", "runs", "detail")),
    Route("POST", "/api/runs/case-history", "application/json", CASE_HISTORY,
          _run_case_history, None, "application/json", ("citizen", "runs", "case-history")),
    Route("POST", "/api/runs/evidence", "application/json", RUN_EVIDENCE,
          _run_evidence, None, "application/json", ("citizen", "runs", "evidence")),
    Route("GET", PLUGIN_EVAL_REPORT_PATH, "text/html; charset=utf-8", HTML,
          _plugin_eval_report, None, cli_command=("citizen", "runs", "evidence")),
    Route("POST", "/api/runs/rerun", "application/json", RUN_RECORD,
          _run_rerun, None, "application/json", ("citizen", "runs", "rerun")),
    Route("POST", "/api/runs/cancel", "application/json", RUN_RECORD,
          _run_cancel, None, "application/json", ("citizen", "runs", "cancel")),
    Route("POST", "/api/runs/stream", "text/event-stream; charset=utf-8", RUN_STREAM,
          _run_stream, "sse", "application/json"),
    Route("GET", "/api/live", "text/event-stream; charset=utf-8", LIVE_STREAM,
          _live_stream, "sse"),
    Route("GET", "/api/experiments/native-acceptance/catalog", "application/json",
          NATIVE_CATALOG, _native_catalog, None,
          cli_command=("python3", "scripts/native_acceptance.py", "--help")),
    Route("POST", "/api/experiments/native-acceptance/progress", "application/json",
          NATIVE_SNAPSHOT, _native_progress, None, "application/json",
          ("python3", "scripts/native_acceptance.py")),
    Route("POST", "/api/experiments/native-acceptance/preview", "application/json",
          NATIVE_PREVIEW, _native_preview, None, "application/json",
          ("citizen", "runs", "spend-preview")),
    Route("POST", "/api/experiments/native-acceptance/start", "application/json",
          NATIVE_RUN, _native_start, None, "application/json",
          ("citizen", "runs", "start")),
    Route("POST", "/api/experiments/native-acceptance/retry", "application/json",
          NATIVE_SELECTION, _native_retry, None, "application/json",
          ("python3", "scripts/native_acceptance.py")),
    Route("GET", "/api/runs/replay/catalog", "application/json",
          REPLAY_CATALOG, _replay_catalog, None,
          cli_command=replay.NATIVE_COMMAND + ("--help",)),
    Route("POST", "/api/runs/replay/preview", "application/json",
          REPLAY_PREVIEW, _replay_preview, None, "application/json",
          ("citizen", "runs", "spend-preview")),
    Route("POST", "/api/runs/replay/start", "application/json",
          REPLAY_RUN, _replay_start, None, "application/json",
          ("citizen", "runs", "start")),
    Route("POST", "/api/runs/replay/result", "application/json",
          REPLAY_RESULT, _replay_result, None, "application/json",
          ("citizen", "runs", "show")),
    Route("POST", "/api/evals/catalog", "application/json", EVAL_CATALOG,
          _evals_catalog, None, "application/json", ("citizen", "runs", "catalog", "--json")),
    Route("POST", "/api/evals/run", "application/json", EVAL_FREE_RUN,
          _evals_start_free, None, "application/json", ("citizen", "runs", "start")),
    Route("POST", "/api/evals/preview", "application/json", EVAL_PREVIEW,
          _evals_preview, None, "application/json", ("citizen", "runs", "spend-preview")),
    Route("POST", "/api/evals/start", "application/json", EVAL_RUN,
          _evals_start, None, "application/json", ("citizen", "runs", "start")),
    Route("POST", "/api/evals/result", "application/json", EVAL_RESULT,
          _evals_result, None, "application/json",
          ("python3", "scripts/cost_bench.py", "summarise", "--json")),
    Route("POST", "/api/runs/compare", "application/json",
          RUNS_COMPARE, _runs_compare, None, "application/json",
          ("citizen", "runs", "compare")),
    Route("POST", "/api/configure/test/plan", "application/json",
          DRAFT_TEST_PLAN, _draft_test_plan, None, "application/json",
          ("citizen", "runs", "spend-preview")),
    Route("POST", "/api/configure/test/register", "application/json",
          DRAFT_TEST_REGISTER, _draft_test_register, None, "application/json",
          ("citizen", "draft", "test", "--register")),
    Route("POST", "/api/configure/test/start", "application/json",
          DRAFT_TEST_RUN, _draft_test_start, None, "application/json",
          ("citizen", "runs", "start")),
    Route("POST", "/api/configure/test/verdicts", "application/json",
          DRAFT_TEST_VERDICTS, _draft_test_verdicts, None, "application/json",
          ("citizen", "draft", "test")),
    Route("GET", CONTROL_HEALTH, "application/json", HEALTH, _health, "authenticated-health"),
    Route("POST", CONTROL_BOOTSTRAP, "application/json", BOOTSTRAP_CONTROL,
          _control_bootstrap, "bootstrap"),
    Route("POST", CONTROL_STOP, "application/json", STOP, _stop, "transport"),
))


def bind(static_root: Path, credential: str, store: Store,
         requested_port: int) -> Tuple[Server, bool]:
    try:
        return Server(("127.0.0.1", requested_port), static_root, credential, store), False
    except OSError as exc:
        if not requested_port or exc.errno not in (errno.EADDRINUSE, errno.EACCES):
            raise
    return Server(("127.0.0.1", 0), static_root, credential, store), True


def run(static_root: Path, store: Store, requested_port: int,
        ready: Optional[Callable[[Dict[str, object]], None]] = None,
        browser: bool = False) -> None:
    lock = store.acquire()
    if lock is None:
        raise RuntimeError("another Studio instance owns the instance lock")
    _startup_trace("instance-locked")
    server = None
    old_handlers = {}
    try:
        def request_stop(_signum, _frame):
            raise StopRequested()

        try:
            if threading.current_thread() is threading.main_thread():
                for signum in (signal.SIGINT, signal.SIGTERM):
                    old_handlers[signum] = signal.getsignal(signum)
                    signal.signal(signum, request_stop)
            credential = secrets.token_urlsafe(48)
            _startup_trace("binding")
            server, fallback = bind(static_root, credential, store, requested_port)
            _startup_trace("bound")
            pid_start = _process_identity()
            if not pid_start:
                raise RuntimeError("this platform cannot identify the Studio process start time")
            server.pid_start = pid_start
            port = int(server.server_address[1])
            record = {"schema_version": SCHEMA_VERSION, "protocol_version": PROTOCOL_VERSION,
                      "pid": os.getpid(), "pid_start": pid_start, "port": port,
                      "host": server.host, "url": server.sessions.origin + "/",
                      "control_credential": credential, "instance_epoch": server.instance_epoch}
            store.write(record)
            _startup_trace("state-published")
            result = public_result(record, requested_port, fallback, reused=False)
            launch_path = None
            if browser:
                token, form_name = server.sessions.issue()
                launch_path = auth.write_launcher_form(
                    store.path, form_name, server.sessions.origin + BOOTSTRAP, token)
            if ready:
                if launch_path is not None:
                    result["_launch_path"] = str(launch_path)
                ready(result)
            server.serve_forever(poll_interval=0.05)
        except StopRequested:
            pass
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
        if server is not None:
            for form_name in server.sessions.pending_forms():
                store.remove_private(form_name)
            server.server_close()
        store.remove()
        os.close(lock)


def public_result(record: Dict[str, object], requested_port: int = 0,
                  fallback: bool = False, reused: bool = False) -> Dict[str, object]:
    result = {"url": record["url"], "port": record["port"], "pid": record["pid"],
              "reused": reused, "port_fallback": fallback}
    if fallback:
        result["requested_port"] = requested_port
    return result

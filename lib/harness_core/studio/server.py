# SPDX-License-Identifier: MIT
"""The Python-standard-library loopback server for Studio."""
from __future__ import annotations

import errno
import hmac
import json
import os
import signal
import threading
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional, Tuple

from harness_core import workers

from .state import PROTOCOL_VERSION, SCHEMA_VERSION, Store

CONTROL_HEALTH = "/__studio/control/health"
CONTROL_STOP = "/__studio/control/stop"
PARITY_EXEMPTIONS = frozenset(("transport", "bootstrap", "static", "authenticated-health", "sse"))


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
        if self.kind != "json-object" or not isinstance(payload, dict):
            raise ValueError("route response does not match its schema kind")
        if set(payload) != {name for name, _ in self.fields}:
            raise ValueError("route response fields do not match its schema")
        types = {"boolean": bool, "integer": int, "string": str}
        for name, type_name in self.fields:
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
    parity_exemption: str


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
            if route.parity_exemption not in PARITY_EXEMPTIONS:
                raise ValueError("unsupported Studio parity exemption: " + route.parity_exemption)
            if key in self._routes:
                raise ValueError("duplicate Studio route: %s %s" % key)
            self._routes[key] = route

    def resolve(self, method: str, path: str) -> Optional[Route]:
        return self._routes.get((method, path))


class Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True

    def __init__(self, address: Tuple[str, int], static_root: Path, credential: str):
        self.static_root = Path(static_root)
        self.control_credential = credential
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server: Server

    def log_message(self, _format, *_args):
        return

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = "Bearer " + self.server.control_credential
        return hmac.compare_digest(supplied, expected)

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
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
        self._error(code, "method_not_allowed" if code == 501 else "request_refused")

    def _dispatch(self) -> None:
        route = ROUTES.resolve(self.command, self.path)
        if route is None:
            self._error(404, "not_found")
            return
        route.handler(self, route)

    def do_HEAD(self):
        self._dispatch()

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()


def _static(handler: Handler, route: Route) -> None:
    try:
        body = (handler.server.static_root / "index.html").read_bytes()
    except OSError:
        handler._send(503, b"Studio bundle is unavailable\n", "text/plain; charset=utf-8")
        return
    route.response_schema.validate(body)
    handler._send(200, body, route.media_type)


def _health(handler: Handler, route: Route) -> None:
    if not handler._authorized():
        handler._json(401, {"error": "unauthorized"})
        return
    payload = {"schema_version": SCHEMA_VERSION, "protocol_version": PROTOCOL_VERSION,
               "pid": os.getpid(), "pid_start": workers.process_start(os.getpid()),
               "port": handler.server.server_address[1]}
    route.response_schema.validate(payload)
    handler._send(200, (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(),
                  route.media_type)


def _stop(handler: Handler, route: Route) -> None:
    if not handler._authorized():
        handler._json(401, {"error": "unauthorized"})
        return
    payload = {"stopping": True}
    route.response_schema.validate(payload)
    handler._send(200, b'{"stopping":true}\n', route.media_type)
    handler.server.shutdown()


HTML = ResponseSchema("html-document")
HEALTH = ResponseSchema("json-object", (("schema_version", "integer"),
                                         ("protocol_version", "integer"),
                                         ("pid", "integer"),
                                         ("pid_start", "string"),
                                         ("port", "integer")))
STOP = ResponseSchema("json-object", (("stopping", "boolean"),))
ROUTES = RouteRegistry((
    Route("GET", "/", "text/html; charset=utf-8", HTML, _static, "static"),
    Route("HEAD", "/", "text/html; charset=utf-8", HTML, _static, "static"),
    Route("GET", "/index.html", "text/html; charset=utf-8", HTML, _static, "static"),
    Route("HEAD", "/index.html", "text/html; charset=utf-8", HTML, _static, "static"),
    Route("GET", CONTROL_HEALTH, "application/json", HEALTH, _health, "authenticated-health"),
    Route("POST", CONTROL_STOP, "application/json", STOP, _stop, "transport"),
))


def bind(static_root: Path, credential: str, requested_port: int) -> Tuple[Server, bool]:
    try:
        return Server(("127.0.0.1", requested_port), static_root, credential), False
    except OSError as exc:
        if not requested_port or exc.errno not in (errno.EADDRINUSE, errno.EACCES):
            raise
    return Server(("127.0.0.1", 0), static_root, credential), True


def run(static_root: Path, store: Store, requested_port: int,
        ready: Optional[Callable[[Dict[str, object]], None]] = None) -> None:
    lock = store.acquire()
    if lock is None:
        raise RuntimeError("another Studio instance owns the instance lock")
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
            credential = uuid.uuid4().hex + uuid.uuid4().hex
            server, fallback = bind(static_root, credential, requested_port)
            pid_start = workers.process_start(os.getpid())
            if not pid_start:
                raise RuntimeError("this platform cannot identify the Studio process start time")
            port = int(server.server_address[1])
            record = {"schema_version": SCHEMA_VERSION, "protocol_version": PROTOCOL_VERSION,
                      "pid": os.getpid(), "pid_start": pid_start, "port": port,
                      "url": "http://127.0.0.1:%d/" % port,
                      "control_credential": credential, "instance_epoch": str(uuid.uuid4())}
            store.write(record)
            result = public_result(record, requested_port, fallback, reused=False)
            if ready:
                ready(result)
            server.serve_forever(poll_interval=0.05)
        except StopRequested:
            pass
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
        if server is not None:
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

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
import sys
import threading
import urllib.parse
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional, Tuple

from harness_core import workers

from . import auth, settings
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
        if self.kind == "binary":
            if not isinstance(payload, bytes):
                raise ValueError("route response is not binary")
            return
        if self.kind == "redirect":
            if payload is not None:
                raise ValueError("redirect route emitted a body")
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
        asset = STATIC_ASSET.fullmatch(path)
        if asset is None:
            return None
        return self._routes.get((method, STATIC_ASSET_ROUTES[asset.group("extension")]))


class Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True

    def __init__(self, address: Tuple[str, int], static_root: Path, credential: str, store: Store):
        self.static_root = Path(static_root)
        self.repo_root = self.static_root.parent.parent
        self.control_credential = credential
        self.store = store
        super().__init__(address, Handler)
        try:
            self.static_files = auth.KnownRoots({"static": self.static_root})
            self.host = "%s.localhost:%d" % (secrets.token_hex(16), self.server_address[1])
            self.sessions = auth.Sessions(self.host)
            self.mutations = MutationExecutor()
        except BaseException:
            super().server_close()
            raise

    def server_close(self):
        mutations = getattr(self, "mutations", None)
        if mutations is not None:
            mutations.close()
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
            length = _content_length(self, auth.MAX_JSON_BYTES)
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
CONFIGURE_SCHEMA = ResponseSchema("json-object", (("schema_version", "integer"),
                                                    ("commands", "object"), ("sections", "array")))
CONFIGURE_READ = ResponseSchema("json-object", (("status", "string"), ("message", "string"),
                                                  ("draft", "object"), ("values", "object"),
                                                  ("warnings", "array")))
CONFIGURE_PREVIEW = ResponseSchema("json-object", (("valid", "boolean"), ("errors", "array"),
                                                     ("warnings", "array"), ("changed", "array"),
                                                     ("preview", "object"),
                                                     ("base_revision", "string")))
CONFIGURE_SAVE = ResponseSchema("json-object", CONFIGURE_PREVIEW.fields +
                                (("saved", "boolean"), ("result", "object-or-null")))
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
    Route("GET", "/api/configure/schema", "application/json", CONFIGURE_SCHEMA,
          _configure_schema, None, cli_command=settings.CLI_COMMANDS["schema"]),
    Route("POST", "/api/configure/read", "application/json", CONFIGURE_READ,
          _configure_read, None, "application/json", settings.CLI_COMMANDS["read"]),
    Route("POST", "/api/configure/preview", "application/json", CONFIGURE_PREVIEW,
          _configure_preview, None, "application/json", settings.CLI_COMMANDS["preview"]),
    Route("POST", "/api/configure/save", "application/json", CONFIGURE_SAVE,
          _configure_save, None, "application/json", settings.CLI_COMMANDS["save"]),
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
                      "control_credential": credential, "instance_epoch": str(uuid.uuid4())}
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

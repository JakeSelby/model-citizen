#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Optional OTLP/HTTP export of usage ledger rows. Off unless `config.json` turns it on.

The ledger is the record and a backend is a rebuildable copy of it: every row is written to
`usage.jsonl` first, and only then offered to an endpoint from the detached worker, where a
slow or dead collector cannot reach the session. Delivery is at-least-once by design, so a
reader de-duplicates on `harness.row_key` and keeps the greatest `harness.exported_at`;
`harness usage export --since` replays a window.

This module sits beside `usage-log.py` rather than in `lib/harness_core` because the hook is
also a standalone script: it is reached through `~/.claude/hooks/harness`, which resolves to
the checkout's hook directory and to nothing above it. `usage-log.py` loads it with the same
`sibling()` resolver it loads the detectors with, and a copy running away from it exports
nothing rather than failing the session.

Nothing here ever logs, prints or records a header value. See docs/telemetry.md.
"""
import calendar
import importlib.util
import json
import os
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# SessionEnd already ran by the time the worker exports, so the budget here is the user's
# patience on the next command, not the hook's. One attempt, no retry: the ledger still holds
# the row, and `harness usage export --since` is the recovery path.
TIMEOUT = 2.0

# A batch is bounded twice: by rows, so a replay of a month is many modest requests rather than
# one enormous one, and by bytes, because a single row carrying a large `rules` map can be tens
# of kilobytes and collectors refuse oversized bodies.
MAX_ROWS = 100
MAX_BYTES = 1 << 20

SERVICE_NAME = "agent-harness"
# A decision row measures a provider call, not a session, and carries `input`, `output` and a
# price under the same names a session row does. Exported bare they would land in the same
# columns, and a backend summing `input` would count a hook's question as session spend. So a
# decision row's own fields travel under one namespace of their own; see `attributes`.
DECISION_KIND = "decision"
DECISION_PREFIX = "harness.decision."
SEVERITY_NUMBER = 9  # INFO, per the OTLP logs data model.

# `native` is validated here and used by `harness sync`, never by this exporter: runtime
# pass-through writes a runtime's own telemetry settings and sends nothing itself.
KNOWN_KEYS = ("export", "endpoint", "headers_env", "headers_file", "labels", "native",
              "decisions", "completion_claim", "allow_sample_rate", "decision_log_max_bytes",
              "decision_log_keep")

# The runtimes native pass-through can configure. `native` is `true` for all of them, `false`
# for none, or the list of the ones it names: Codex takes header values only as literals in
# `config.toml`, which the harness will not write, so a collector that authenticates can be
# fed natively from Claude Code alone. See docs/telemetry.md.
NATIVE_RUNTIMES = ("claude-code", "codex")
DEFAULT_ENDPOINT = "http://localhost:4318"
# The allowed-command sample, kept in step with `decisions.DEFAULT_SAMPLE_RATE`, which this
# module does not import: validation here must not depend on a sibling hook being loadable.
DEFAULT_SAMPLE_RATE = 20
# The decision log's retention, kept in step with `decisions.DEFAULT_MAX_BYTES` and `DEFAULT_KEEP`.
DEFAULT_DECISION_LOG_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_DECISION_LOG_KEEP = 3

# Backing off a collector that is not there. A refused or unreachable endpoint is written to the
# backoff file beside the errors file, and no export is tried again until its `until`: a minute
# after the first failure, doubling with each one after it, never more than six hours. A
# collector that answers, even with an error status, clears it, because a listener is there.
# The rows are in the ledger either way; `harness usage export --since` sends what was skipped.
BACKOFF_BASE = 60
BACKOFF_MAX = 6 * 3600


def home():
    return Path(os.environ.get("HARNESS_HOME") or os.environ.get("HOME") or Path.home())


def config_path():
    return home() / ".config" / "agent-harness" / "config.json"


def read_config(path=None):
    try:
        with open(str(path or config_path()), encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def settings(cfg=None, path=None):
    """The validated `telemetry` block, or `{"export": "off"}` when there is none.

    Raises `ValueError` on a block that cannot be honoured, so the CLI can refuse loudly while
    the hook stays silent: a mistyped endpoint should stop a sync, never a session.
    """
    cfg = read_config(path) if cfg is None else cfg
    block = cfg.get("telemetry") if isinstance(cfg, dict) else None
    if block is None:
        return {"export": "off"}
    if not isinstance(block, dict):
        raise ValueError("telemetry must be an object")
    unknown = sorted(set(block) - set(KNOWN_KEYS))
    if unknown:
        # A header value in the config is the one typo worth naming: it would put a credential
        # in a file that is backed up, synced and read by every tool that reads the config.
        if any(k in ("headers", "header", "authorization") for k in unknown):
            raise ValueError(
                "telemetry headers must be read from headers_env or headers_file; a header "
                "value in config.json is a credential in a configuration file")
        raise ValueError("telemetry does not take " + ", ".join(unknown))
    mode = block.get("export", "off")
    if mode not in ("off", "otlp"):
        raise ValueError("telemetry.export must be \"off\" or \"otlp\"; got " + repr(mode))
    endpoint = block.get("endpoint") or DEFAULT_ENDPOINT
    if not isinstance(endpoint, str) or not endpoint.startswith(("http://", "https://")):
        raise ValueError("telemetry.endpoint must be an http:// or https:// URL")
    headers_env = block.get("headers_env") or ""
    if not isinstance(headers_env, str):
        raise ValueError("telemetry.headers_env must be the name of an environment variable")
    if "=" in headers_env:
        raise ValueError(
            "telemetry.headers_env names an environment variable to read headers from; it is "
            "not the headers themselves")
    headers_file = block.get("headers_file") or ""
    if not isinstance(headers_file, str):
        raise ValueError("telemetry.headers_file must be a path")
    labels = block.get("labels") or {}
    if not isinstance(labels, dict) or any(not isinstance(v, (str, int, float, bool)) for v in labels.values()):
        raise ValueError("telemetry.labels must be an object of scalar values")
    native = native_runtimes(block.get("native", False))
    # The local decision log, which never leaves the machine and is no part of `export`: see
    # `decisions.py`. Validated here because it is a `telemetry` key and an unknown key in that
    # block stops a sync; the exporter itself never reads it.
    decisions = block.get("decisions", True)
    if not isinstance(decisions, bool):
        raise ValueError("telemetry.decisions must be true or false; got " + repr(decisions))
    # The completion claim on a stop-gate row, off by default: `decisions.claim_enabled`.
    claim = block.get("completion_claim", False)
    if not isinstance(claim, bool):
        raise ValueError("telemetry.completion_claim must be true or false; got " + repr(claim))
    # One in how many allowed Bash commands is kept as an ungraded negative:
    # `decisions.sample_rate`, 20 by default, 0 for none.
    rate = block.get("allow_sample_rate", DEFAULT_SAMPLE_RATE)
    if isinstance(rate, bool) or not isinstance(rate, int) or rate < 0:
        raise ValueError("telemetry.allow_sample_rate must be a whole number of commands, one "
                         "of which is logged, or 0 for none; got " + repr(rate))
    # The decision log's retention: `decisions.max_bytes` and `decisions.keep`.
    retention = {}
    for name, default in (("decision_log_max_bytes", DEFAULT_DECISION_LOG_MAX_BYTES),
                          ("decision_log_keep", DEFAULT_DECISION_LOG_KEEP)):
        value = block.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("telemetry." + name + " must be a whole number, 0 or more; got "
                             + repr(value))
        retention[name] = value
    return dict({"export": mode, "endpoint": endpoint.rstrip("/"), "headers_env": headers_env,
                 "headers_file": headers_file, "labels": dict(labels), "native": native,
                 "decisions": decisions, "completion_claim": claim, "allow_sample_rate": rate},
                **retention)


def native_runtimes(value):
    """`telemetry.native` as the list of runtimes it names, in a stable order.

    `true` is every runtime and `false` is none, so a config written before the key took a list
    keeps its meaning. An unknown name is refused rather than ignored: a typo would otherwise
    leave a runtime silently unconfigured with nothing said about it.
    """
    if isinstance(value, bool):
        return list(NATIVE_RUNTIMES) if value else []
    if not isinstance(value, (list, tuple)) or any(not isinstance(n, str) for n in value):
        raise ValueError(
            "telemetry.native must be true, false, or a list of runtime names ("
            + ", ".join(NATIVE_RUNTIMES) + "); got " + repr(value))
    unknown = sorted(set(value) - set(NATIVE_RUNTIMES))
    if unknown:
        raise ValueError(
            "telemetry.native does not know the runtime " + ", ".join(repr(n) for n in unknown)
            + "; the runtimes are " + ", ".join(NATIVE_RUNTIMES))
    return [name for name in NATIVE_RUNTIMES if name in set(value)]


def parse_headers(text):
    """`name=value` per line, or the comma-separated form `OTEL_EXPORTER_OTLP_HEADERS` uses."""
    out = {}
    for line in str(text or "").replace(",", "\n").splitlines():
        item = line.strip()
        if not item:
            continue
        name, sep, value = item.partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not name or any(c.isspace() for c in name):
            raise ValueError("a telemetry header must read name=value")
        out[name] = value
    return out


def _in_git_work_tree(path):
    for parent in Path(path).resolve().parents:
        if (parent / ".git").exists():
            return True
    return False


def headers_from_file(path):
    """Headers from a file that is outside every work tree and unreadable by other users.

    A credential inside a repository is one `git add -A` from being published, and one that
    any account on the machine can read is not a secret. Neither the value nor the file's text
    appears in the refusal.
    """
    target = Path(path).expanduser()
    try:
        mode = target.stat().st_mode
    except OSError:
        raise ValueError("telemetry.headers_file {} cannot be read".format(target))
    if not stat.S_ISREG(mode):
        raise ValueError("telemetry.headers_file {} is not a regular file".format(target))
    if _in_git_work_tree(target):
        raise ValueError(
            "telemetry.headers_file {} is inside a git work tree; keep the credential outside "
            "every repository".format(target))
    if stat.S_IMODE(mode) & 0o007:
        raise ValueError(
            "telemetry.headers_file {} is readable by other users; `chmod 600` it".format(target))
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        raise ValueError("telemetry.headers_file {} cannot be read".format(target))
    return parse_headers(text)


def headers(config, env=None):
    """The request headers, from the named environment variable and file and nowhere else."""
    env = os.environ if env is None else env
    out = {}
    name = config.get("headers_env") or ""
    if name:
        raw = env.get(name)
        if raw is None:
            raise ValueError("telemetry.headers_env names ${}, which is not set".format(name))
        out.update(parse_headers(raw))
    if config.get("headers_file"):
        out.update(headers_from_file(config["headers_file"]))
    return out


# ------------------------------------------------------------------ prices

# Loaded once per process: the exporter runs in a detached worker that sends one batch and
# exits, and re-reading the price file per row would be the most expensive thing it did.
_PRICING = []


def pricing():
    """The sibling price module, or None when this copy is running away from it.

    Same resolver `usage-log.py` uses for the detectors, and the same consequence: a copy of
    these files somewhere else exports rows without dollars rather than failing.
    """
    if not _PRICING:
        module = None
        try:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pricing.py")
            spec = importlib.util.spec_from_file_location("harness_pricing", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:
            module = None
        _PRICING.append(module)
    return _PRICING[0]


def price_table(cfg=None):
    """The merged price table, or `{}` when pricing cannot be had at all.

    Never raises. A missing price file, a malformed `prices` override or a missing sibling
    costs an exported row its two dollar attributes and nothing else: the row still travels,
    with its tokens, exactly as it did before prices were exported.
    """
    module = pricing()
    if module is None:
        return {}
    try:
        return module.load_prices(read_config() if cfg is None else cfg)
    except Exception:
        return {}


def row_prices(rows, table):
    """`(usd, as_of)` per row, in order, or `(None, "")` for each when pricing is unavailable.

    Priced over the whole set rather than row by row, because a Claude Code session is priced
    with its subagent rows in hand — the figure on the session row already includes them.
    """
    module = pricing()
    if module is not None and table:
        try:
            return module.priced(rows, table)
        except Exception:
            pass
    return [(None, "")] * len(rows)


# ------------------------------------------------------------------ the OTLP/JSON payload


def row_key(row):
    """The stable identity of a row: the tuple `usage-log.py`'s `row_key` upserts on, as text.

    A replay re-sends the same string for the same row, which is what lets a backend
    de-duplicate an at-least-once stream.
    """
    return "|".join(str(row.get(k) or d) for k, d in (
        ("session_id", ""), ("runtime", "claude-code"), ("kind", "session"), ("agent_id", "")))


def _nanos(stamp):
    """Epoch nanoseconds for a ledger timestamp, or None when it will not parse."""
    if not isinstance(stamp, str) or not stamp:
        return None
    text = stamp.replace("Z", "").split(".")[0]
    try:
        parsed = time.strptime(text, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    try:
        return int(calendar.timegm(parsed)) * 1000000000
    except (OverflowError, ValueError):
        return None


def exported_at(now=None):
    """The export time as a fixed-width RFC 3339 UTC string: `2026-09-21T18:04:05.123456Z`.

    Fixed width, always six fractional digits and always `Z`, because a backend that lands
    attributes in a string map — ClickHouse's `otel_logs` keeps `LogAttributes` as
    `Map(String, String)` — compares this lexically, and only a fixed-width form makes lexical
    order equal time order. It is what tells two records for one `harness.row_key` apart: the
    OTLP observed time does not survive that ingest.
    """
    stamp = time.time() if now is None else float(now)
    whole = int(stamp // 1)
    micros = int((stamp - whole) * 1000000)
    if micros >= 1000000:  # only reachable through float rounding at a second boundary
        whole, micros = whole + 1, 0
    return "{}.{:06d}Z".format(time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(whole)), micros)


def any_value(value):
    """One OTLP `AnyValue`. Ints travel as decimal strings, which the JSON mapping requires."""
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": str(value)}


def attributes(row, config=None, version="", price=None, exported=None):
    """The row's flat scalars, its stances, its key, its price, its export time and the labels.

    A nested map — `days`, `by_model`, `rules`, `counts` — travels in the body only: attribute
    sets are flat, and flattening a hundred per-day slices into attribute names would make
    every row a new column in a backend. `stances` is the exception, because a stance is a
    dimension a report groups by: it flattens to one `harness.<dimension>` each, and only here,
    which is why `body_row` takes the map back out of the body.

    `price` is the `(usd, as_of)` this row was priced at. An unpriced row carries neither
    attribute: a zero would say the run was free rather than that nobody knows what it cost.

    `exported` is the `harness.exported_at` stamp; see that function for why every record
    carries one. Rows in one batch may share a stamp, which is harmless: they are distinct keys.

    A `kind: "decision"` row is a provider call rather than a session, and its fields travel
    under `harness.decision.*` — its price included — so a backend that sums `input` or `usd`
    over its logs counts what the sessions spent and not what the harness spent asking.
    """
    out = {}
    prefix = DECISION_PREFIX if row.get("kind") == DECISION_KIND else ""
    for key, value in sorted(row.items()):
        if value is None or key == "stances":
            continue
        if isinstance(value, (str, int, float, bool)):
            out[prefix + key] = value
    for dimension, variant in sorted((row.get("stances") or {}).items()):
        if isinstance(variant, (str, int, float, bool)):
            out["harness." + str(dimension)] = variant
    out["harness.row_key"] = row_key(row)
    out["harness.exported_at"] = exported or exported_at()
    usd, as_of = price or (None, "")
    if usd is not None:
        out[(prefix or "harness.") + "usd"] = float(usd)
        if as_of:
            out[(prefix or "harness.") + "price_as_of"] = str(as_of)
    stamped = row.get("harness_version") or version
    if stamped:
        out["harness.version"] = stamped
    for key, value in sorted(((config or {}).get("labels") or {}).items()):
        out[str(key)] = value
    return [{"key": k, "value": any_value(v)} for k, v in sorted(out.items())]


def body_row(row):
    """The row as it travels in the body: everything the ledger holds but the `stances` map.

    A backend that parses a JSON body flattens a nested map into dotted keys of its own, so a
    body carrying `stances` lands a second copy of every stance beside the `harness.<dimension>`
    attributes above. One stance, one attribute: the map comes out here and nothing else does,
    because no other nested field is also exported as attributes.
    """
    return dict((k, v) for k, v in row.items() if k != "stances")


def log_record(row, config=None, version="", now=None, price=None):
    seconds = time.time() if now is None else now
    now_nanos = int(seconds * 1000000000)
    return {
        "timeUnixNano": str(_nanos(row.get("ended")) or now_nanos),
        "observedTimeUnixNano": str(now_nanos),
        "severityNumber": SEVERITY_NUMBER,
        "severityText": "INFO",
        "body": {"stringValue": json.dumps(body_row(row), sort_keys=True)},
        # The same instant as the observed time above, in the form that survives ingest.
        "attributes": attributes(row, config, version, price, exported_at(seconds)),
    }


def payload(rows, config=None, version="", now=None, prices=None):
    """One OTLP request body. `prices` is the `(usd, as_of)` per row, in the order given."""
    prices = list(prices or []) + [None] * max(len(rows) - len(prices or []), 0)
    resource = [{"key": "service.name", "value": {"stringValue": SERVICE_NAME}}]
    if version:
        resource.append({"key": "service.version", "value": {"stringValue": version}})
    return {"resourceLogs": [{
        "resource": {"attributes": resource},
        "scopeLogs": [{"scope": {"name": SERVICE_NAME},
                       "logRecords": [log_record(r, config, version, now, p)
                                      for r, p in zip(rows, prices)]}],
    }]}


def batches(rows, max_rows=MAX_ROWS, max_bytes=MAX_BYTES):
    """Rows grouped into requests, bounded by count and by encoded size."""
    batch, size = [], 0
    for row in rows:
        cost = len(json.dumps(row)) + 2048  # the row's body plus room for its attributes
        if batch and (len(batch) >= max_rows or size + cost > max_bytes):
            yield batch
            batch, size = [], 0
        batch.append(row)
        size += cost
    if batch:
        yield batch


# ------------------------------------------------------------------ delivery


def endpoint_label(endpoint):
    """Scheme and host only. A path or a query can carry a token; neither is ever recorded."""
    try:
        parts = urllib.parse.urlsplit(endpoint)
        host = parts.hostname or ""
        if parts.port:
            host = "{}:{}".format(host, parts.port)
        return "{}://{}".format(parts.scheme, host)
    except Exception:
        return ""


def post(rows, config, request_headers, version="", timeout=TIMEOUT, opener=None, prices=None):
    """POST one batch. Raises on transport or status failure; the caller records the class."""
    url = config["endpoint"]
    if not url.endswith("/v1/logs"):
        url = url + "/v1/logs"
    body = json.dumps(payload(rows, config, version, None, prices)).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    for name, value in request_headers.items():
        request.add_header(name, value)
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            status = getattr(response, "status", None) or response.getcode()
            response.read()
    except urllib.error.HTTPError as exc:
        # An HTTPError is also the response, and holds a socket until it is closed.
        exc.close()
        raise
    if not 200 <= int(status) < 300:
        raise urllib.error.HTTPError(url, int(status), "OTLP export rejected", None, None)
    return len(rows)


def record_failure(path, endpoint, error, rows):
    """One line in the existing errors file: when, what class, how many rows, which host."""
    if not path:
        return
    entry = {"time": time.time(), "error": type(error).__name__, "action": "otlp-export",
             "endpoint": endpoint_label(endpoint), "rows": rows}
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as stream:
            stream.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def unreachable(error):
    """Whether `error` says nothing was listening, as opposed to a collector that answered."""
    if isinstance(error, urllib.error.HTTPError):
        return False
    return isinstance(error, (urllib.error.URLError, OSError))


def backoff_until(path, now=None):
    """The time before which no export is tried, or 0 when there is no backoff in force."""
    if not path:
        return 0
    try:
        with open(str(path), encoding="utf-8") as stream:
            state = json.load(stream)
        until = state.get("until", 0) if isinstance(state, dict) else 0
    except (OSError, ValueError):
        return 0
    moment = time.time() if now is None else now
    if isinstance(until, bool) or not isinstance(until, (int, float)) or until <= moment:
        return 0
    # A clock that went backwards, or a file nobody wrote, must not silence export for longer
    # than the cap.
    return min(until, moment + BACKOFF_MAX)


def note_backoff(path, failed, now=None):
    """Record one more unreachable attempt, or clear the record after one that got through."""
    if not path:
        return
    path = Path(path)
    try:
        if not failed:
            if path.exists():
                path.unlink()
            return
        try:
            with open(str(path), encoding="utf-8") as stream:
                state = json.load(stream)
        except (OSError, ValueError):
            state = {}
        count = state.get("failures", 0) if isinstance(state, dict) else 0
        count = (count if isinstance(count, int) and not isinstance(count, bool) else 0) + 1
        moment = time.time() if now is None else now
        delay = min(BACKOFF_MAX, BACKOFF_BASE * 2 ** min(count - 1, 20))
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(json.dumps({"failures": count, "until": moment + delay}) + "\n")
        os.replace(str(temp), str(path))
    except OSError:
        pass


def export_rows(rows, config=None, env=None, version="", errors_path=None,
                timeout=TIMEOUT, opener=None, dry_run=False, prices=None, backoff_path=None,
                now=None):
    """Send rows to the configured endpoint. Returns `(sent, failed)` and never raises.

    With export off this opens no socket and reads no credential: the first check is the mode,
    so a machine that has not turned export on behaves exactly as it did before it existed.

    `prices` is the price table to stamp rows with; with none given it is read from the price
    file and the caller's own `prices` overrides, which is what the hook does. Pricing runs
    after the mode check and cannot fail the export: an unpriced row still travels.

    `backoff_path`, which the session-end hook passes and a replay from the CLI does not, makes
    an unreachable collector cost one attempt per backoff window rather than one per session:
    while the window is open the rows are counted failed and nothing is tried or written. See
    BACKOFF_BASE.
    """
    rows = [r for r in rows if isinstance(r, dict)]
    cfg = None
    try:
        if config is None:
            # One read of the config file for both the endpoint and the `prices` overrides.
            cfg = read_config()
            config = settings(cfg)
    except ValueError as exc:
        record_failure(errors_path, "", exc, len(rows))
        return 0, len(rows)
    if config.get("export") != "otlp" or not rows:
        return 0, 0
    try:
        request_headers = headers(config, env)
    except ValueError as exc:
        record_failure(errors_path, config.get("endpoint", ""), exc, len(rows))
        return 0, len(rows)
    # By identity, because a batch is a slice of these same row objects and a row has no key
    # of its own until `row_key` builds one.
    priced = {}
    if not dry_run:
        table = price_table(cfg) if prices is None else prices
        priced = dict((id(row), price) for row, price in zip(rows, row_prices(rows, table)))
    if not dry_run and backoff_until(backoff_path, now):
        return 0, len(rows)
    sent = failed = 0
    reached = False
    for batch in batches(rows):
        if dry_run:
            sent += len(batch)
            continue
        try:
            sent += post(batch, config, request_headers, version, timeout, opener,
                         [priced.get(id(row)) for row in batch])
            reached = True
        except Exception as exc:
            if backoff_path and unreachable(exc):
                # Nothing is listening, so the batches after this one would fail the same way.
                left = len(rows) - sent - failed
                failed += left
                record_failure(errors_path, config.get("endpoint", ""), exc, left)
                note_backoff(backoff_path, True, now)
                return sent, failed
            reached = reached or not unreachable(exc)
            failed += len(batch)
            record_failure(errors_path, config.get("endpoint", ""), exc, len(batch))
    if reached and not dry_run:
        note_backoff(backoff_path, False, now)
    return sent, failed

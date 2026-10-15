# SPDX-License-Identifier: MIT
"""Baseline: start from what you have (AH-S382, #1334).

Baseline reads the configuration and plugins in effect now and records them as a baseline: one
timestamped row appended to `periods.baselines_path`, under the Studio state directory. Recording
writes nothing else, never `config.json`. A recorded baseline is a boundary source in `periods`, so
every Harness surface, and Evals through `GET /api/harness/periods`, sees a period that starts at
it and can compare later periods against it.

The readers are the ones the Studio already uses: `config.json` through `periods`, its fields and
redaction through `settings`, and enabled third-party plugins through the sources surface.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from . import auth, periods, settings
from .surfaces import sources

SCHEMA_VERSION = 1
LABEL_MAX = 80
LIST_LIMIT = 50
CLI_COMMANDS = {
    "show": ("citizen", "baseline", "show", "--json"),
    "record": ("citizen", "baseline", "record", "--label", "{label}", "--json"),
}


class BaselineError(ValueError):
    """A record request the route refuses with `invalid_request`."""


def valid_label(label: Any) -> bool:
    return (isinstance(label, str) and len(label) <= LABEL_MAX
            and all(ch.isprintable() for ch in label))


def _mapping(config: Mapping[str, Any], key: str) -> Dict[str, str]:
    value = config.get(key)
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in sorted(value.items()) if isinstance(v, str) and v}


def _plugins(home: Path) -> List[Dict[str, Any]]:
    return [{"id": plugin["id"], "label": plugin["label"].split(": ", 1)[-1],
             "path": plugin["path"], "modules": plugin["modules"]}
            for plugin in sources.plugins(home)]


def _settings(public: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [{"path": field["path"], "section": field["section"], "label": field["label"],
             "value": public[field["path"]]}
            for field in settings.FIELDS if public.get(field["path"]) not in (None, [], {})]


def _config(home: Path) -> Optional[Dict[str, Any]]:
    path = periods.config_path(home)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def current(home: Path) -> Dict[str, Any]:
    """What you have now: `config_present`, `stances`, `rules`, `settings` and `plugins`."""
    config = _config(home)
    present = config is not None
    config = config or {}
    return {"config_present": present, "config_path": str(periods.config_path(home)),
            "stances": _mapping(config, "stances"), "rules": _mapping(config, "rules"),
            "settings": _settings(settings._public_values(config)),
            "plugins": _plugins(home)}


def _entry(row: Mapping[str, Any]) -> Dict[str, Any]:
    config = row.get("config") if isinstance(row.get("config"), dict) else {}
    plugins = row.get("plugins") if isinstance(row.get("plugins"), list) else []
    return {"id": str(row.get("id") or ""), "ts": periods.iso(row["_ts"]),
            "label": str(row.get("label") or ""), "stances": _mapping(config, "stances"),
            "rules": _mapping(config, "rules"),
            "plugins": [str(p.get("label") or p.get("id")) for p in plugins
                        if isinstance(p, dict)]}


def recorded(home: Path) -> List[Dict[str, Any]]:
    """Recorded baselines, newest first, at most `LIST_LIMIT`."""
    rows = periods._jsonl(periods.baselines_path(home), "ts")
    return [_entry(row) for row in reversed(rows[-LIST_LIMIT:])]


def read(home: Path) -> Dict[str, Any]:
    """The `GET /api/baseline` answer."""
    return {"schema_version": SCHEMA_VERSION, "current": current(home),
            "baselines": recorded(home), "commands": {k: " ".join(v)
                                                      for k, v in CLI_COMMANDS.items()}}


def record(home: Path, label: str = "", now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """Append one baseline holding the redacted config and the plugins seen now; return it."""
    if not valid_label(label):
        raise BaselineError("label must be printable text of at most %d characters" % LABEL_MAX)
    when = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    row = {"schema_version": SCHEMA_VERSION, "id": uuid.uuid4().hex, "ts": periods.iso(when),
           "label": label, "config": auth.public_data(_config(home) or {}),
           "plugins": _plugins(home)}
    path = periods.baselines_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(row, sort_keys=True) + "\n").encode("utf-8")
    descriptor = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, line)
    finally:
        os.close(descriptor)
    row["_ts"] = periods.parse_ts(row["ts"])
    return {"schema_version": SCHEMA_VERSION, "baseline": _entry(row)}

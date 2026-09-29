"""Opt-in controls for a decision provider that leaves the machine: modes, a kill switch, an
allowlist.

Three separate questions, deliberately not one switch:

* **May this point be judged at all, and how far?** `governance.jev.mode` sets a default and
  `governance.jev.modes` names a decision point: `off` calls nothing, `shadow` calls and logs
  the answer where only the ledger sees it, `advise` adds a line to the decision, `act` lets a
  judgment tighten one. Every mode defaults to `off`, so an existing configuration that has
  never heard of this provider makes no request.
* **Is anything allowed out right now?** A sentinel file disables every call while it exists,
  with no configuration change and no restart: `touch ~/.local/state/agent-harness/jev-disabled`
  is the kill switch, and `mode_for` reads it per decision rather than at construction.
* **What may leave?** `governance.jev.state_fields` is an allowlist over `STATE_FIELDS`,
  empty by default. A field not listed is never built into the request, and no key outside
  `BASE_FIELDS` plus the listed ones can reach the wire — `check_outbound` refuses the request
  rather than trimming it.

A live request needs all three to agree and a credential in the environment besides; the
harness never reads a key file. `SessionSpend` sits alongside them for the budget the provider
charges: a hook is a new process per event, so counters that live in one bound nothing, and a
session's spend is kept in the state directory under the lock instead. Nothing here has an
effect until a configuration asks for one.
"""
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import decision
from .. import reconcile

# The hook points a mode may name. A copy rather than an import: `policy/hooks/decisions.py`
# is reached by file from a hook directory, not by module path, and a configuration must
# validate in a process that never loads it. `test_jev_outbound_controls.py` asserts the two
# lists agree, so a point added there and forgotten here is a test failure.
POINTS = ("grade-bash", "stop-gate", "tier-agent-spawns", "delegation-nudge",
          "brief-guard", "evasion-deny")

MODES = ("off", "shadow", "advise", "act")
DEFAULT_MODE = "off"

# The state fields a caller's context may contribute, and the ones every request carries
# whatever the configuration says. Closed on purpose: a decision point that touches
# permissions may describe the command it is judging and nothing else, so tool output,
# assistant prose, file contents and environment values have no field to travel in.
STATE_FIELDS = ("command", "summary")
BASE_FIELDS = ("action_class", "counterparty", "grade", "grade_scale")

SENTINEL_NAME = "jev-disabled"
# Where a session's spend is kept, so a ceiling bounds a session rather than a process: every
# hook is a new process, and a counter that lives in one bounds nothing at all.
SPEND_NAME = "jev-spend.json"
# A session's row is dropped a day after its last request. Long enough that a session cannot
# outlive its own ceiling, short enough that the file stays a file a person can read.
SPEND_RETENTION_SECONDS = 24 * 60 * 60
SPEND_LOCK_ATTEMPTS = 5
SPEND_LOCK_PAUSE = 0.01
SESSION_VARIABLES = ("HARNESS_SESSION_ID", "CLAUDE_SESSION_ID")
# The bucket a process with no session id spends from. Shared rather than per-process, which
# is the conservative direction: an unidentified caller may not have a fresh ceiling.
UNKNOWN_SESSION = "unknown-session"
# Read from the environment only. The harness never reads a key file, and a value is never
# printed: what a report may say is which of these names is set.
KEY_VARIABLES = ("TYPESAFE_API_KEY", "JEV_API_KEY")
# Two seconds, inside the ten a hook has: a judgment that has not arrived by then is worth
# less than the turn it is holding up, and the deterministic answer is already in hand.
DEFAULT_TIMEOUT = 2
DEFAULT_MAX_REQUESTS = 50
DEFAULT_MAX_TOKENS = 200000
KEYS = ("mode", "modes", "state_fields", "sentinel", "timeout", "max_requests", "max_tokens")


def state_dir() -> Path:
    """`~/.local/state/agent-harness`, the directory the ledgers already live in."""
    home = os.environ.get("HARNESS_HOME") or os.environ.get("HOME")
    return (Path(home) if home else Path.home()) / ".local" / "state" / "agent-harness"


def _where(key: str) -> str:
    return "governance.jev." + key


def _mode(value: Any, key: str) -> str:
    if value not in MODES:
        # The offending value is never quoted back. A configuration value can hold anything a
        # user pasted, and an error message is printed, logged and scrolled past.
        raise decision.PolicyError(_where(key) + " must be one of " + ", ".join(MODES))
    return value


def _count(value: Any, key: str, fallback: int) -> int:
    if value is None:
        return fallback
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise decision.PolicyError(_where(key) + " must be a non-negative integer")
    return value


class Controls:
    """One resolved answer to each of the three questions above.

    Built from a configuration with `from_config`, which defaults every mode to `off` and the
    allowlist to empty. `acting()` is the other constructor: every point at `act` and both
    optional fields allowed, for a caller that has assembled a provider by hand and supplied
    its own client. Only configured controls may put a client on the network — a provider
    built in a test or a script is inert however its modes read.
    """

    def __init__(self, default_mode=DEFAULT_MODE, modes=None, state_fields=(), sentinel=None,
                 timeout=DEFAULT_TIMEOUT, max_requests=DEFAULT_MAX_REQUESTS,
                 max_tokens=DEFAULT_MAX_TOKENS, configured=False):
        self.default_mode = _mode(default_mode, "mode")
        self.modes = {}
        for name in sorted(modes or {}):
            if name not in POINTS:
                raise decision.PolicyError(
                    _where("modes") + " names an unknown decision point " + repr(name)
                    + "; known points are " + ", ".join(POINTS))
            self.modes[name] = _mode((modes or {})[name], "modes." + name)
        self.state_fields = []
        for name in state_fields or ():
            if name not in STATE_FIELDS:
                raise decision.PolicyError(
                    _where("state_fields") + " names a field a decision point may not send; "
                    "it may send " + ", ".join(STATE_FIELDS) + " and nothing else")
            if name not in self.state_fields:
                self.state_fields.append(name)
        if sentinel is not None and (not isinstance(sentinel, str) or not sentinel.strip()
                                     or "\x00" in sentinel or "\n" in sentinel):
            raise decision.PolicyError(_where("sentinel") + " must be a path")
        self.sentinel = sentinel.strip() if sentinel else None
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) \
                or not 0 < timeout <= 10:
            raise decision.PolicyError(_where("timeout")
                                       + " must be a number of seconds in (0, 10]")
        self.timeout = timeout
        self.max_requests = _count(max_requests, "max_requests", DEFAULT_MAX_REQUESTS)
        self.max_tokens = _count(max_tokens, "max_tokens", DEFAULT_MAX_TOKENS)
        self.configured = bool(configured)

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]]) -> "Controls":
        """The `governance.jev` block, validated, or every point `off` when there is none."""
        block = (config or {}).get("governance")
        block = block.get("jev") if isinstance(block, dict) else None
        if block is None:
            return cls(configured=True)
        if not isinstance(block, dict):
            raise decision.PolicyError("governance.jev must be an object of "
                                       + ", ".join(KEYS))
        unknown = sorted(set(block) - set(KEYS))
        if unknown:
            raise decision.PolicyError("governance.jev has unknown key(s) "
                                       + ", ".join(unknown) + "; known keys are "
                                       + ", ".join(KEYS))
        modes = block.get("modes", {})
        if not isinstance(modes, dict):
            raise decision.PolicyError(_where("modes")
                                       + " must be an object of decision point to mode")
        fields = block.get("state_fields", [])
        if not isinstance(fields, list):
            raise decision.PolicyError(_where("state_fields") + " must be an array of field "
                                       "names; known fields are " + ", ".join(STATE_FIELDS))
        return cls(default_mode=block.get("mode", DEFAULT_MODE), modes=modes,
                   state_fields=fields, sentinel=block.get("sentinel"),
                   timeout=block.get("timeout", DEFAULT_TIMEOUT),
                   max_requests=block.get("max_requests"),
                   max_tokens=block.get("max_tokens"), configured=True)

    @classmethod
    def acting(cls) -> "Controls":
        return cls(default_mode="act", modes=dict((point, "act") for point in POINTS),
                   state_fields=STATE_FIELDS)

    def sentinel_path(self) -> Path:
        """Where the kill switch lives. Relative is resolved against the state directory.

        Never against the working directory: a switch whose meaning depends on where a hook
        happened to be invoked from is one that is on for some decisions and off for others.
        """
        if not self.sentinel:
            return state_dir() / SENTINEL_NAME
        path = Path(self.sentinel).expanduser()
        return path if path.is_absolute() else state_dir() / path

    def disabled(self) -> bool:
        """Whether the kill switch is in place. Read per decision, never cached."""
        try:
            return self.sentinel_path().exists()
        except OSError:
            # A path that cannot even be stat'd is not a reason to start calling out.
            return True

    def mode_for(self, point: Optional[str] = None) -> str:
        """The mode for one decision point, the sentinel and an unknown name included.

        A point this harness does not know reads `off`, never the default: a caller naming a
        point nobody configured is a caller nobody decided about.
        """
        if self.disabled():
            return "off"
        if point is not None and point not in POINTS:
            return "off"
        if point is not None and point in self.modes:
            return self.modes[point]
        return self.default_mode

    def selected(self) -> Dict[str, str]:
        """Every known point and the mode it resolves to, the sentinel included."""
        return dict((point, self.mode_for(point)) for point in POINTS)

    def enabled(self) -> bool:
        """Whether any configured mode asks for a call, the sentinel left out of it.

        What a client is built with, because the kill switch is answered per decision by
        `mode_for`: a client built while the sentinel existed must still work the moment it is
        removed, without a restart.
        """
        if not self.configured:
            return False
        modes = [self.modes.get(point, self.default_mode) for point in POINTS]
        return any(mode != "off" for mode in modes)

    def live(self) -> bool:
        """Whether a call could be made right now: enabled, and not switched off."""
        return self.enabled() and not self.disabled()

    def allowed_fields(self) -> List[str]:
        return list(BASE_FIELDS) + list(self.state_fields)

    def outbound(self, context: Optional[Dict[str, Any]]) -> Dict[str, str]:
        """The listed fields of `context` that are safe to send, and nothing else.

        A field whose text matches a known secret shape is dropped whole rather than masked:
        the match says where a credential is, not how long it is, and a masked remainder still
        carries whatever sat beside it.
        """
        patterns = secret_patterns()
        out = {}
        for name in self.state_fields:
            value = (context or {}).get(name)
            if not isinstance(value, str) or not value.strip():
                continue
            if patterns is None or any(rx.search(value) for rx in patterns):
                continue
            out[name] = value
        return out

    def check_outbound(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """`state`, or a `PolicyError` naming the first key no configuration allowed out."""
        allowed = set(self.allowed_fields())
        extra = sorted(set(state) - allowed)
        if extra:
            raise decision.PolicyError(
                "jev: " + ", ".join(extra) + " is not a field this configuration allows out; "
                "allowed fields are " + ", ".join(sorted(allowed)))
        return state

    def status(self, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """What `harness doctor` prints: modes, the switch, and whether a key exists.

        The credential is reported by variable name only. A value is a credential and is never
        printed, here or anywhere.
        """
        try:
            path = str(self.sentinel_path())
        except (OSError, RuntimeError):
            path = "unresolved"
        return {"default_mode": self.default_mode, "modes": self.selected(),
                "sentinel": path, "sentinel_present": self.disabled(),
                "state_fields": list(self.state_fields),
                "credential": credential_variable(env), "live": self.live(),
                "timeout": self.timeout, "max_requests": self.max_requests,
                "max_tokens": self.max_tokens}


class SessionSpend:
    """One session's requests and tokens, shared by every process that answers for it.

    A hook is a new process per event, so a ceiling counted in memory bounds a single decision
    and nothing else. The counters live in the state directory keyed by session id, read before
    each check and added to after each charge, under the same lock the rest of the harness uses
    for a file two processes may write.

    Nothing here may fail a decision. A lock that stays held, an unreadable file or a full disk
    leaves the process-local count standing, which is the same conservative direction as the
    rest of this provider: a judgment is never worth a turn.
    """

    def __init__(self, session: Optional[str] = None, path=None,
                 env: Optional[Dict[str, str]] = None):
        env = os.environ if env is None else env
        named = session or next((env[name] for name in SESSION_VARIABLES if env.get(name)), None)
        self.session = named if _session_key(named) else UNKNOWN_SESSION
        self.path = Path(path) if path else state_dir() / SPEND_NAME

    def read(self) -> Tuple[int, int]:
        """`(requests, tokens)` already spent in this session. Unreadable is zero."""
        row = self._rows().get(self.session) or {}
        return (_nonnegative(row.get("requests")), _nonnegative(row.get("tokens")))

    def add(self, requests: int, tokens: int) -> bool:
        """Add to this session's spend; says whether the file took it."""
        for attempt in range(SPEND_LOCK_ATTEMPTS):
            try:
                with reconcile.lock(self.path.parent):
                    rows = self._prune(self._rows())
                    row = rows.get(self.session) or {}
                    rows[self.session] = {
                        "requests": _nonnegative(row.get("requests")) + int(requests),
                        "tokens": max(0, _nonnegative(row.get("tokens")) + int(tokens)),
                        "updated": int(time.time())}
                    reconcile.atomic_text(self.path, json.dumps(rows, sort_keys=True) + "\n")
                return True
            except ValueError:
                # Another process holds the lock. It holds it for one small write.
                time.sleep(SPEND_LOCK_PAUSE * (attempt + 1))
            except OSError:
                return False
        return False

    def _rows(self) -> Dict[str, Any]:
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return dict((k, v) for k, v in rows.items() if isinstance(v, dict)) \
            if isinstance(rows, dict) else {}

    def _prune(self, rows: Dict[str, Any]) -> Dict[str, Any]:
        cutoff = time.time() - SPEND_RETENTION_SECONDS
        return dict((name, row) for name, row in rows.items()
                    if name == self.session or _nonnegative(row.get("updated")) >= cutoff)


def _session_key(value: Any) -> bool:
    """A session id safe to key a row by: ASCII, bounded, no separator and no traversal."""
    return (isinstance(value, str) and value.isascii() and 0 < len(value) <= 128
            and value[0].isalnum() and all(c.isalnum() or c in "._-" for c in value))


def _nonnegative(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def credential_variables() -> List[str]:
    return list(KEY_VARIABLES)


def credential_variable(env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """The name of the environment variable holding a key, or None. Never the value."""
    env = os.environ if env is None else env
    return next((name for name in KEY_VARIABLES if env.get(name)), None)


def secret_patterns():
    """The shared secret shapes, compiled, or None when the list cannot be loaded.

    One list for the lint, the `secret-in-write` detector and this filter. None is not "no
    secrets": a caller reads it as "scan unavailable" and sends no free text at all, because a
    redactor that failed to load must not be mistaken for one that found nothing.
    """
    module = decision._hook_module("rule-detectors")
    patterns = getattr(module, "SECRET_PATTERNS", None) if module else None
    if not patterns:
        return None
    try:
        return [re.compile(pattern) for pattern in patterns]
    except re.error:
        return None

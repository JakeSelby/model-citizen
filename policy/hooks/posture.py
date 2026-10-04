#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The one place a stance and the model ladder are resolved.

Not a hook: nothing registers it, and it reads no event. It sits beside the hooks because
they are standalone scripts run as subprocesses and share code by loading a sibling file
(`rule-detectors.py` is the precedent); `lifecycle.py` loads this same file by path, so the
dispatcher and the hooks cannot drift into two answers for one question.

`selection(env)` resolves every unit of every kind over the selection ladder `docs/preferences.md`
documents: built-in defaults, the selected mode, the user config under `HARNESS_HOME` or `$HOME`,
the file `HARNESS_PROJECT_CONFIG` names, the file `HARNESS_SESSION_CONFIG` names, then the
session's `HARNESS_MODE` and `HARNESS_STANCE_*`. `resolve(env)` and `selected()` read the same
ladder for stances only. `strict` says what an unusable file means: the dispatcher wants the
error, a hook wants the spawn to run anyway, so it passes `strict=False` and takes the layers it
could read.

`cost_table(env)`, and `resolve(env, table=True)`, additionally resolve the active `cost`
variant's JSON sidecar — switches, per-role and per-band rows, the default band — over its
`extends` chain. Schema and authoring: `docs/primitive-authoring.md`. It is opt-in because it
reads more files than a stance question needs. No number lives here: an unusable sidecar yields
the base variant's table and a warning, never a guessed default.

`fingerprint(env)` digests the resolved selection into the profile fingerprint every new ledger
row carries; see `FINGERPRINT_KEY`.

Import-cheap on purpose: no work at import, JSON reads only, because the dispatcher loads
this on every tool call.
"""
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
import stat
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "HARNESS_STANCE_"
# How long a session record is worth keeping. A session that has not started in a fortnight
# will never spawn again, and its record is three fields nobody reads.
SESSION_TTL_DAYS = 14
SESSION_ID_MAX = 128
# Retakes of a session lock whose file a prune removed between the open and the lock.
SESSION_LOCK_ATTEMPTS = 8
# How stale a record may get before a spawn that read it moves its mtime out of the sweep's way.
SESSION_REFRESH_SECONDS = 86400
DELEGATION_READS_KEY = "delegation_read_files"
DELEGATION_FIRED_KEY = "delegation_nudge_fired"
DELEGATION_READS_MAX = 64
# The transcript attachment a session writes when the set of types it resolves changes, and how
# much of the transcript's tail is read to find one. A reload is announced in the turn it is
# noticed, so it is at the end of the file, and a bounded read keeps a spawn hook's cost flat.
AGENT_LISTING = "agent_listing_delta"
TRANSCRIPT_TAIL_BYTES = 256 * 1024
# The user-config key naming tool-name globs plan mode may use, and the postures under which a
# widened plan-mode authority is what the user already asked for everywhere else.
PLAN_TOOLS_KEY = "plan_allow_tools"
OPEN_POSTURES = ("bypass", "auto")

# The dimensions `bin/harness` resolves and the variant each falls back to, which is
# `config.example.json`'s — the file the CLI layers the user config over, and a test holds the
# two together.
DEFAULT_STANCES = {
    "licensing": "permissive-commercial",
    "build-vs-buy": "capability-ceiling",
    "commits": "conventional-attributed",
    "plan-ceremony": "review-card",
    "delegation": "tiered",
    "testing": "required",
    "autonomy": "execute",
    "cost": "balanced", "voice": "scannable",
}
# Capability classes, strongest first; a test holds this equal to `catalog.TIER_CLASSES`, which
# is the definition. Named here so a hook can order an adapter's table without importing the
# library: a policy hook is a subprocess with no package on its path.
TIER_CLASSES = ("frontier", "strong", "standard", "light")
EFFORTS = ("low", "medium", "high")
BANDS = ("A", "B", "C")
# The role a band's row renders and reroutes to. A band classes the *work*, so it needs an agent
# definition to carry its class and effort into a native spawn; these three are those definitions,
# and this map is the only place the naming is written.
BAND_ROLES = {band: "worker-" + band.lower() for band in BANDS}
# The variant every other one falls back to, and the one a sidecar-less variant resolves to.
BASE_COST_VARIANT = DEFAULT_STANCES["cost"]
SIDECAR_SCHEMA_VERSION = 1
MAX_EXTENDS_DEPTH = 5
SWITCH_VALUES = {
    "session_effort": ("low", "medium", "high", "default"),
    "fast_mode": ("never", "off-unless-asked", "allowed"),
    "compaction": ("clear-only", "clear-at-task-end", "compact-allowed"),
    "turn_feed": ("off", "thresholds", "every-turn"),
}
BUDGET_KEYS = ("budget_output_tokens", "budget_tool_calls")
# The unit each budgeted key is stated in, in the order the sentence states them.
BUDGET_UNITS = dict(zip(BUDGET_KEYS, ("output tokens", "tool calls")))
# Ceilings that only rule out a number no machine could mean. A budget is soft, so the cap is
# about arithmetic that stays finite, not about an opinion on how much is too much.
MAX_MULTIPLIER = 100
MAX_BUDGET = 10 ** 9
MAX_NUDGES = 8
SIDECAR_KEYS = ("schema_version", "extends", "switches", "default_band", "rows")
# The selection document: `mode`, then one object per kind in `catalog.KINDS`. `sources` and
# `shadowed` are what `harness selection --json` prints beside them; they select nothing, and are
# accepted so that the output reads back unchanged as a session file. Shape and precedence:
# `docs/preferences.md`.
SELECTION_EXTRA_KEYS = ("mode", "sources", "shadowed")
SWITCH_STATES = ("on", "off")
MODE_VARIABLE = "HARNESS_MODE"
# A mode file carries these beside its selection keys. Contract and shipped modes: `docs/modes.md`.
MODE_KEYS = ("schema_version", "description")
# Hooks a layer, a mode included, may switch off only when the user configuration sets `CORE_ACK`
# true (`core_refusals`). The same four ids as `catalog.CORE_HOOKS`, held here too because a hook
# copied out of its checkout has no catalog; a test keeps the two equal.
CORE_HOOKS = ("brief-guard", "grade-bash", "neutralize-tool-output", "stop-gate")
CORE_ACK = "core_switches_acknowledged"
# The user-configuration key naming, per kind, the units `harness init` wrote as defaults rather
# than ones the user chose. Those resolve below the mode (AD-2); every other user key above it.
INIT_DEFAULTS = "init_defaults"
_KINDS = {}
# A module's manifest (AD-22): what it claims to change, where it reaches the model, what measures
# it, the one exclusive slot it takes, and the modules it needs or collides with. Authoring
# contract and vocabulary: `docs/primitive-authoring.md`.
MANIFEST_FIELDS = ("claims", "surface", "instruments", "slot", "dependencies", "conflicts")
MANIFEST_FILE = "manifests.json"
SURFACES = ("resident-context", "on-demand-context", "hook-events")


def home(env=None):
    env = os.environ if env is None else env
    return Path(env.get("HARNESS_HOME") or env.get("HOME") or Path.home())


def config_path(env=None):
    return home(env) / ".config" / "agent-harness" / "config.json"


def user_agents_dir(env=None):
    """Where the tool resolves a user-level agent definition; `CLAUDE_CONFIG_DIR` moves it.

    The one place that rule is written, so the hook that reroutes a spawn and the hook that
    records what a session can resolve are never looking at two different directories.
    """
    env = os.environ if env is None else env
    config = env.get("CLAUDE_CONFIG_DIR")
    return (Path(config) if config else Path(env.get("HOME") or Path.home()) / ".claude") / "agents"


def installed_agents(env=None):
    """The user-level agent definitions on disk now, sorted; `[]` when the directory is unreadable."""
    try:
        return sorted(path.stem for path in user_agents_dir(env).glob("*.md") if path.is_file())
    except OSError:
        return []


def transcript_agents(transcript_path, limit=TRANSCRIPT_TAIL_BYTES):
    """The types a reload announced to this session after it started, or None when none did.

    Claude Code attaches an `agent_listing_delta` record to the transcript whenever the set of
    types it can resolve changes: one with `isInitial` true at session start, and one more each
    time the watcher picks a definition up. A later record is the runtime's own statement that
    this session resolves the names it adds, which no directory listing can give — a headless
    session reloads nothing and writes no later record, so this answers for the session that
    asked rather than for the machine. Measured in `docs/spikes/2026-09-22-registry-reload.md`.

    Only the tail is read, so a long session costs what a short one does, and a listing that
    has fallen out of it reads as None: unknown, which routes nothing.
    """
    if not transcript_path:
        return None
    try:
        with open(str(transcript_path), "rb") as stream:
            try:
                stream.seek(-limit, os.SEEK_END)
            except OSError:
                stream.seek(0)
            tail = stream.read().decode("utf-8", "replace")
    except OSError:
        return None
    names = None
    # One record per line, so only `\n` ends one: a body holding a line separator of its own
    # must not be read as two half-records.
    for line in tail.split("\n"):
        if AGENT_LISTING not in line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("type") != "attachment" or record.get("isSidechain"):
            continue
        listing = record.get("attachment")
        if not isinstance(listing, dict) or listing.get("type") != AGENT_LISTING:
            continue
        if listing.get("isInitial"):
            # A listing a session starts from replaces everything before it, exactly as a
            # `startup` replaces the record: what an earlier session resolved is not this one's.
            names = None
            continue
        names = set() if names is None else names
        for key, apply in (("addedTypes", names.add), ("removedTypes", names.discard)):
            value = listing.get(key)
            if isinstance(value, list):
                for name in value:
                    if isinstance(name, str):
                        apply(name)
    return sorted(names) if names is not None else None


def state_dir(env=None):
    return home(env) / ".local" / "state" / "agent-harness"


def sessions_dir(env=None):
    """The session registry: one record per session, written when its process started.

    A definition on disk is not evidence that a running session can resolve the type it names:
    an interactive session picks one up seconds after it appears, a headless one never does,
    and rerouting to a type the session cannot resolve turns a spawn that would have worked
    into one that fails. The SessionStart policy writes what the registry held; the spawn hook
    reroutes to a name it finds there, or to one `transcript_agents` shows the session was
    later told about.
    """
    return state_dir(env) / "sessions"


def _session_id(value):
    """A session identifier safe to make a file name of: no separator, no traversal, bounded."""
    return (isinstance(value, str) and value.isascii() and 0 < len(value) <= SESSION_ID_MAX
            and value[0].isalnum() and all(c.isalnum() or c in "._-" for c in value))


def session_record_path(session_id, env=None):
    return sessions_dir(env) / (session_id + ".json") if _session_id(session_id) else None


def read_session_record(session_id, env=None):
    """One session's record, or None for no record, an unreadable one, or anything but an object."""
    path = session_record_path(session_id, env)
    if path is None:
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _same_file(descriptor, path):
    """Whether an open lock descriptor is still the file at `path`, not one a prune unlinked."""
    try:
        held, current = os.fstat(descriptor), os.stat(str(path))
    except OSError:
        return False
    return (held.st_dev, held.st_ino) == (current.st_dev, current.st_ino)


@contextlib.contextmanager
def _session_lock(session_id, env=None):
    """Hold this session's kernel-released state lock, or yield no path on failure.

    A prune may unlink an idle lock file, so a lock taken on a descriptor whose file has since
    left the path is released and taken again on the file now there; otherwise two hooks could
    each hold a lock on a different inode of the same name.
    """
    path = session_record_path(session_id, env)
    descriptor = None
    if path is None:
        yield None
        return
    lock = path.with_name(path.name + ".lock")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(str(path.parent), 0o700)
        for _ in range(SESSION_LOCK_ATTEMPTS):
            descriptor = os.open(str(lock), os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            if _same_file(descriptor, lock):
                break
            os.close(descriptor)
            descriptor = None
        if descriptor is None:
            raise OSError("session lock kept moving")
    except OSError:
        yield None
        if descriptor is not None:
            os.close(descriptor)
        return
    try:
        yield path
    finally:
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(descriptor)


def _write_session_record(path, record):
    """Replace a locked session record atomically."""
    temp = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
    try:
        with os.fdopen(os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600),
                       "w", encoding="utf-8") as handle:
            json.dump(record, handle)
        os.replace(str(temp), str(path))
        return True
    except OSError:
        try:
            os.unlink(str(temp))
        except OSError:
            pass
        return False


def update_session_record(session_id, update, env=None):
    """Apply one read-modify-write while holding the session's shared state lock."""
    with _session_lock(session_id, env) as path:
        if path is None:
            return False
        record = read_session_record(session_id, env) or {}
        changed = update(dict(record))
        return isinstance(changed, dict) and _write_session_record(path, changed)


def write_session_record(session_id, record, env=None):
    """Replace one session's record under its shared lock; `True` when it was written.

    The directory is the session's own business and nobody else's, so it is 0700 and the file
    is 0600 from the moment it exists rather than after a chmod a reader could race.
    """
    if not isinstance(record, dict):
        return False
    with _session_lock(session_id, env) as path:
        return path is not None and _write_session_record(path, record)


def session_agents(session_id, env=None):
    """The agent names this session's registry held, or None when nothing recorded them.

    An absent or unusable `agents` key is None, which every caller reads as unknown, and
    unknown is never routable. That is what lets a record exist purely to remember a notice
    without ever authorising a reroute.
    """
    record = read_session_record(session_id, env)
    names = record.get("agents") if record else None
    return [name for name in names if isinstance(name, str)] if isinstance(names, list) else None


def session_announced(session_id, env=None):
    """The types a reload told this session about on an earlier spawn; `[]` when none did."""
    record = read_session_record(session_id, env)
    names = record.get("announced") if record else None
    return [name for name in names if isinstance(name, str)] if isinstance(names, list) else []


def remember_agents(session_id, names, env=None):
    """Keep what a reload announced, so routing outlives the transcript tail. `True` when written.

    The transcript is where an announcement is discovered and the read of it is bounded, so a
    long session pushes the delta out of the tail; routing that switched off there would be the
    same defect again on a slower clock. The remembered set is replaced rather than merged,
    because the reader's answer already accounts for every `removedTypes` in the tail, and a
    merge would reinstate a worker the session has been told it no longer resolves.
    """
    wanted = sorted({name for name in names if isinstance(name, str)}) if names else []
    changed = []

    def update(record):
        if record.get("announced") == wanted:
            return None
        changed.append(True)
        return dict(record, announced=wanted, at=int(time.time()))

    return update_session_record(session_id, update, env) if not changed else False


def refresh_session_record(session_id, env=None, older_than=SESSION_REFRESH_SECONDS):
    """Keep a session in use out of another session's sweep. `True` when the mtime was moved.

    A session open longer than the TTL would otherwise have its record pruned under it and stop
    routing halfway through, so reading the record is evidence the session is alive. A day's
    granularity, because this runs on a spawn and the sweep measures a fortnight.
    """
    path = session_record_path(session_id, env)
    try:
        if path is not None and time.time() - path.stat().st_mtime > older_than:
            os.utime(str(path), None)
            return True
    except OSError:
        pass
    return False


def note_once(session_id, key, env=None):
    """`True` the first time this session is told `key`; `False` once anything remembers it.

    A session with no record is exactly the session these notices are for, so one is created
    to hold the memory — with no `agents` key, which reads as unknown and can never authorise
    a reroute. A hook is a process per event, so nothing but the record remembers: when it
    cannot be written this says nothing at all, because a notice repeated on every spawn is a
    worse failure than one never given.
    """
    added = []

    def update(record):
        seen = record.get("notified")
        seen = sorted({name for name in seen if isinstance(name, str)}) if isinstance(seen, list) else []
        if key in seen:
            return None
        added.append(True)
        return dict(record, notified=sorted(seen + [key]), at=int(time.time()))

    written = update_session_record(session_id, update, env)
    return bool(added and written)


def delegation_read(session_id, paths, threshold, env=None):
    """`(fired now, distinct count)` after atomically adding read paths to session state.

    The session registry's shared kernel lock keeps every read-modify-write from losing another
    field, and the operating system releases it when a hook exits or is killed. Paths are bounded
    because this record is session memory, not a log.
    """
    path = session_record_path(session_id, env)
    if path is None or not (_number(threshold, 3, DELEGATION_READS_MAX, integer=True)):
        return False, 0
    wanted = {value for value in paths if isinstance(value, str) and value}
    if not wanted:
        return False, 0
    result = []

    def update(record):
        seen = record.get(DELEGATION_READS_KEY)
        seen = {value for value in seen if isinstance(value, str)} if isinstance(seen, list) else set()
        # A session that has fired, or a read it has already counted, cannot change the answer,
        # so it returns without a write rather than rewriting the record on every later read.
        if record.get(DELEGATION_FIRED_KEY) is True or wanted <= seen:
            result.append((False, len(seen)))
            return None
        seen.update(wanted)
        seen = set(sorted(seen)[-DELEGATION_READS_MAX:])
        fire_now = len(seen) >= threshold
        updated = dict(record, **{DELEGATION_READS_KEY: sorted(seen),
                                 DELEGATION_FIRED_KEY: fire_now,
                                 "at": int(time.time())})
        result.append((fire_now, len(seen)))
        return updated

    written = update_session_record(session_id, update, env)
    if not result:
        return False, 0
    fire_now, count = result[0]
    # Firing is transactional: a nudge that could not be recorded is not reported as fired.
    return (fire_now, count) if written or not fire_now else (False, 0)


def _prune_locked(record, lock, cutoff):
    """Remove a stale record and its lock file, only while holding that lock without waiting.

    A lock another hook holds is skipped, and the record's age is judged again under the lock,
    so a session written to since the sweep listed it keeps both files. The lock file goes last,
    while still held; `_session_lock` retakes a lock whose file left the path.
    """
    try:
        descriptor = os.open(str(lock), os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return 0
    removed = 0
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not _same_file(descriptor, lock):
            return 0
        try:
            if record.stat().st_mtime >= cutoff:
                return 0
            record.unlink()
            removed = 1
        except FileNotFoundError:
            # An orphaned lock: only one idle past the same age goes.
            if os.fstat(descriptor).st_mtime >= cutoff:
                return 0
        lock.unlink()
    except OSError:
        pass
    finally:
        os.close(descriptor)
    return removed


def prune_session_records(keep=None, days=SESSION_TTL_DAYS, env=None):
    """Drop records older than `days`, never `keep`'s. Best effort: a sweep never fails a session.

    A record's `<id>.json.lock` goes with it, and an orphaned lock of the same age goes alone,
    but never one a live hook holds.
    """
    cutoff, removed = time.time() - days * 86400, 0
    try:
        paths = sorted(sessions_dir(env).glob("*.json"))
        locks = set(sessions_dir(env).glob("*.json.lock"))
    except OSError:
        return 0
    swept = set()
    for path in paths:
        if keep is not None and path.stem == keep:
            continue
        lock = path.with_name(path.name + ".lock")
        if lock in locks:
            swept.add(lock)
            removed += _prune_locked(path, lock, cutoff)
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    for lock in sorted(locks):
        record = lock.with_name(lock.name[:-len(".lock")])
        if lock in swept or (keep is not None and record.stem == keep):
            continue
        _prune_locked(record, lock, cutoff)
    return removed


def _stances_of(data):
    """The usable `{dimension: variant}` pairs of one layer; anything else is not a selection."""
    stances = data.get("stances") if isinstance(data, dict) else None
    if not isinstance(stances, dict):
        return {}
    return {name: value.strip() for name, value in stances.items()
            if isinstance(value, str) and value.strip()}


def _user_config(env, strict):
    """The user's configuration. No file is the defaults; a file that cannot be read is not.

    A config that exists but will not open or will not parse is a selection nobody can see, so
    strict callers hear about it rather than running under defaults the user did not choose.
    """
    path = config_path(env)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, NotADirectoryError):
        return {}
    except (OSError, ValueError):
        if strict:
            raise
        return {}


def selection_kinds(root=None):
    """`{kind: catalog entry}` for every selectable kind, read from `catalog.KINDS` by file.

    The catalog is the one definition of a kind, so a new kind is a new entry there and nothing
    here. A hook copied out of its checkout has no catalog beside it and knows `stances` only,
    which is the one kind whose defaults this file carries.
    """
    key = str(root or ROOT)
    if key not in _KINDS:
        kinds = {"stances": {"directory": "stances", "pattern": "*/*.md", "value": "variant"}}
        path = Path(key) / "lib" / "harness_core" / "catalog.py"
        if path.is_file():
            try:
                spec = importlib.util.spec_from_file_location("harness_catalog_kinds", str(path))
                loaded = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(loaded)
                kinds = {name: dict(entry) for name, entry in loaded.KINDS.items()
                         if entry.get("value") in ("variant", "switch")}
            except Exception:
                pass
        _KINDS[key] = kinds
    return _KINDS[key]


def _selection_file(env, variable, strict, root=None):
    """The selection document an environment variable names; `{}` when it names none.

    Carries selection keys only. Identity, permissions, runtime flags, `primitive_roots` and
    telemetry keep their own validation in the user configuration, so a key outside the
    selection is refused by name rather than ignored.
    """
    named = env.get(variable)
    if not named:
        return {}
    try:
        data = json.loads(Path(named).expanduser().read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(variable + " names " + named + ", which is not a JSON object")
        refused(data, variable + " file " + named, root)
        # A kind whose value is not an object is refused by `selection()` when strict and selects
        # nothing otherwise; only a key the file may not set drops the whole file.
        return data
    except (OSError, ValueError):
        if strict:
            raise
        return {}


def refused(data, where, root=None):
    """Raise `ValueError` naming every key of `data` a selection document may not carry."""
    extra = sorted(set(data) - set(SELECTION_EXTRA_KEYS) - set(selection_kinds(root)))
    if extra:
        raise ValueError(where + " may carry selection keys only, not " +
                         ", ".join("'" + key + "'" for key in extra) +
                         "; identity, permissions, runtime flags, primitive_roots and telemetry "
                         "stay in the user configuration")


def _project_config(env, strict, root=None):
    return _selection_file(env, "HARNESS_PROJECT_CONFIG", strict, root)


def _session_config(env, strict, root=None):
    return _selection_file(env, "HARNESS_SESSION_CONFIG", strict, root)


def overrides(env=None):
    """The `{dimension: variant}` a session set through `HARNESS_STANCE_*`."""
    env = os.environ if env is None else env
    return {key[len(PREFIX):].lower().replace("_", "-"): value.strip()
            for key, value in env.items()
            if key.startswith(PREFIX) and isinstance(value, str) and value.strip()}


def _identifier(value):
    return (isinstance(value, str) and value and value[0].isalpha() and value.islower()
            and all(c.isalnum() or c == "-" for c in value) and value.isascii())


def _number(value, low, high, integer=False):
    """A finite in-range number. `True` is not 1 here, and neither NaN nor an infinity is a value.

    JSON admits `Infinity` and `NaN`, and Python's `json` reads them, so a multiplier arriving
    from a file can be either; both would raise out of the arithmetic below rather than warn.
    """
    if isinstance(value, bool) or not isinstance(value, int if integer else (int, float)):
        return False
    if value != value or value in (float("inf"), float("-inf")):
        return False
    return low <= value <= high


def _sizes(value):
    """None when `value` is a usable list of context sizes, else the rule it breaks.

    Specific, where every other switch reports "an unusable value", because this one is a list:
    an author told eight numbers are unusable has to find which, against a rule written nowhere.
    """
    if not isinstance(value, list):
        return "is a list of whole positive token counts, smallest first"
    if len(value) > MAX_NUDGES:
        return "has " + str(len(value)) + " entries and at most " + str(MAX_NUDGES) + " are read"
    previous = None
    for item in value:
        shown = json.dumps(item, default=str)
        if not (_number(item, 0, MAX_BUDGET, integer=True) and item > 0):
            return "entry " + shown + " is not a whole positive token count"
        if previous is not None and item <= previous:
            return ("entry " + shown + " does not follow " + json.dumps(previous) +
                    "; the sizes ascend and none repeats")
        previous = item
    return None


def _curve(value):
    """None when `value` is a usable context cost curve, else the rule it breaks.

    `[[tokens, multiple], …]`: from `tokens` of context up, one call costs about `multiple` times
    a call under the first entry's size. The sizes follow `_sizes`; a multiple is positive.
    """
    if not isinstance(value, list) or not all(isinstance(p, list) and len(p) == 2 for p in value):
        return "is a list of [whole token count, multiple] pairs, smallest first"
    problem = _sizes([pair[0] for pair in value])
    if problem:
        return problem
    for pair in value:
        if not (_number(pair[1], 0, MAX_MULTIPLIER) and pair[1] > 0):
            return "multiple " + json.dumps(pair[1], default=str) + " is not a positive number"
    return None


def validate_sidecar(data, roles=None):
    """`(usable copy, findings)` for one sidecar object; every finding drops the value it names.

    Findings are warnings to the resolver and failures to lint, which is the whole point: a
    switch added in a later release must never break a variant somebody else authored, while a
    shipped variant with an unknown key is a mistake nobody should have to discover at runtime.

    `roles` is the role catalog when the caller has one, so a row naming no role and no band is
    reported rather than silently applying to nothing.
    """
    findings, clean = [], {}
    for key in sorted(data):
        if key not in SIDECAR_KEYS:
            findings.append("unknown key '" + key + "'")
    version = data.get("schema_version")
    if version != SIDECAR_SCHEMA_VERSION:
        findings.append("schema_version must be " + str(SIDECAR_SCHEMA_VERSION))
    extends = data.get("extends")
    if extends is not None:
        if _identifier(extends):
            clean["extends"] = extends
        else:
            findings.append("extends names a cost variant or is null")
    band = data.get("default_band")
    if band is not None:
        if band in BANDS:
            clean["default_band"] = band
        else:
            findings.append("default_band is one of " + ", ".join(BANDS))
    switches = data.get("switches", {})
    if "switches" in data and not isinstance(switches, dict):
        findings.append("switches is an object")
        switches = {}
    kept = {}
    for key in sorted(switches):
        value = switches[key]
        if key in SWITCH_VALUES:
            ok = value in SWITCH_VALUES[key]
        elif key == "max_parallel":
            ok = value is None or _number(value, 1, MAX_BUDGET, integer=True)
        elif key == "budget_multiplier":
            ok = _number(value, 0, MAX_MULTIPLIER) and value > 0
        elif key == "nudge_at":
            ok = (isinstance(value, list) and len(value) <= MAX_NUDGES
                  and all(_number(v, 0, MAX_MULTIPLIER) and v > 0 for v in value))
        elif key == "session_nudge_at":
            # Context sizes, not multiples: whole tokens, because that is what a transcript
            # counts in and a fractional token is a number nobody measured.
            problem = _sizes(value)
            if problem:
                findings.append("switch 'session_nudge_at' " + problem)
                continue
            ok = True
        elif key == "session_handoff_at":
            # The hard threshold: one size, or null for a variant that never blocks.
            ok = value is None or (_number(value, 0, MAX_BUDGET, integer=True) and value > 0)
        elif key == "context_cost_curve":
            problem = _curve(value)
            if problem:
                findings.append("switch 'context_cost_curve' " + problem)
                continue
            ok = True
        else:
            findings.append("unknown switch '" + key + "'")
            continue
        if ok:
            kept[key] = value
        else:
            findings.append("switch '" + key + "' has an unusable value")
    if kept:
        clean["switches"] = kept
    rows = data.get("rows", {})
    if "rows" in data and not isinstance(rows, dict):
        findings.append("rows is an object")
        rows = {}
    resolved_rows = {}
    for name in sorted(rows):
        row = rows[name]
        if not (name in BANDS or _identifier(name)):
            findings.append("row '" + str(name) + "' is a role name or a band")
            continue
        if roles is not None and name not in BANDS and name not in roles:
            # A row naming nothing applies to nothing, which is a typo nobody would see.
            findings.append("row '" + name + "' names no role and no band")
            continue
        if not isinstance(row, dict):
            findings.append("row '" + name + "' is an object")
            continue
        cells = {}
        for key in sorted(row):
            value = row[key]
            if key == "class":
                # `frontier` is never reachable by request; the delegation stance decides that,
                # and a variant is a request.
                ok = value in TIER_CLASSES[1:]
            elif key == "effort":
                ok = value in EFFORTS
            elif key in BUDGET_KEYS:
                ok = value is None or _number(value, 0, MAX_BUDGET, integer=True)
            else:
                findings.append("row '" + name + "' has an unknown key '" + str(key) + "'")
                continue
            if ok:
                cells[key] = value
            else:
                findings.append("row '" + name + "' cell '" + key + "' has an unusable value")
        resolved_rows[name] = cells
    if resolved_rows:
        clean["rows"] = resolved_rows
    return clean, findings


def stance_roots(config=None, root=None):
    """The stance directories to search, the built-in one first, then a user's `primitive_roots`."""
    roots = [(root or ROOT) / "primitives" / "stances"]
    entries = config.get("primitive_roots") if isinstance(config, dict) else None
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, str) and entry.strip():
            path = Path(entry).expanduser()
            if path.is_absolute():
                roots.append(path / "stances")
    return roots


def primitive_roots(config=None, root=None, kind="stances"):
    """The primitive directories of one kind, the built-in one first, then a user's roots."""
    roots = [(root or ROOT) / "primitives" / kind]
    entries = config.get("primitive_roots") if isinstance(config, dict) else None
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, str) and entry.strip():
            path = Path(entry).expanduser()
            if path.is_absolute():
                roots.append(path / kind)
    return roots


def stance_roots(config=None, root=None):
    return primitive_roots(config, root, "stances")


def sidecar_path(variant, roots):
    """The first root holding `cost/<variant>.json`, or None. Never reads outside a root.

    The variant name arrives from a config file or `HARNESS_STANCE_COST`, so it is held to the
    same identifier rule as the `.md` it accompanies, and the file it names must still resolve
    inside the root it was found in: a symlink out of the tree is a read nobody asked for.
    """
    if not _identifier(variant):
        return None
    for source in roots:
        path = source / "cost" / (variant + ".json")
        if not path.is_file():
            continue
        try:
            real, base = path.resolve(), source.resolve()
        except OSError:
            continue
        if real == base or base in real.parents:
            return path
    return None


def delegation_nudge(variant, config=None, strict=False, root=None, env=None):
    """The selected delegation variant's bounded read nudge, or None when it defines none."""
    if not _identifier(variant):
        return None
    if config is None:
        config = _user_config(os.environ if env is None else env, strict)
    path = base = source_path = None
    for source in stance_roots(config, root):
        candidate = source / "delegation" / (variant + ".json")
        if not candidate.is_file():
            continue
        try:
            real, base = candidate.resolve(), source.resolve()
        except OSError:
            continue
        if real == base or base in real.parents:
            path = real
            source_path = candidate
            break
    if path is None:
        return None
    warnings = []
    data = _load_sidecar_inside(path, base, strict, warnings)
    if data is None:
        return None
    allowed = {"schema_version", "threshold", "message"}
    valid = (set(data) <= allowed and data.get("schema_version") == SIDECAR_SCHEMA_VERSION
             and _number(data.get("threshold"), 3, DELEGATION_READS_MAX, integer=True)
             and isinstance(data.get("message"), str)
             and 0 < len(data["message"].strip()) <= 500)
    if not valid:
        if strict:
            raise ValueError(str(path) + " is not a usable delegation nudge sidecar")
        return None
    return {"threshold": data["threshold"], "message": data["message"].strip(),
            "source": str(source_path)}


def _load_sidecar_inside(path, base, strict, warnings):
    """Read a resolved sidecar without following a component changed after containment."""
    descriptors = []
    try:
        relative = path.relative_to(base)
        descriptor = os.open(str(base), os.O_RDONLY | os.O_DIRECTORY)
        descriptors.append(descriptor)
        parts = relative.parts
        for index, part in enumerate(parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW
            last = index == len(parts) - 1
            # The leaf opens without blocking, so a FIFO swapped in cannot hang the hook, and
            # anything but a regular file is refused before a byte is read.
            flags |= os.O_NONBLOCK if last else os.O_DIRECTORY
            descriptor = os.open(part, flags, dir_fd=descriptor)
            descriptors.append(descriptor)
            if last and not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError(str(path) + " is not a regular file")
        with os.fdopen(os.dup(descriptors[-1]), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        if strict:
            raise
        warnings.append(str(path) + " is not readable JSON: " + str(exc))
        return None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    if not isinstance(data, dict):
        if strict:
            raise ValueError(str(path) + " is not a JSON object")
        warnings.append(str(path) + " is not a JSON object")
        return None
    return data


def _load_sidecar(path, strict, warnings):
    """The sidecar's object, or None with a warning; unreadable is an error only in strict mode."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        if strict:
            raise
        warnings.append(str(path) + " is not readable JSON: " + str(exc))
        return None
    if not isinstance(data, dict):
        if strict:
            raise ValueError(str(path) + " is not a JSON object")
        warnings.append(str(path) + " is not a JSON object")
        return None
    return data


def _frontmatter(path):
    """A role file's frontmatter fields, or `{}`; the same `key: value` shape `catalog` parses."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---\n") or text.count("---") < 2:
        return {}
    fields = {}
    for line in text.split("---", 2)[1].strip().splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def role_catalog(config=None, root=None):
    """`(every role name, the names whose frontmatter says `posture: fixed`)`.

    A verifier's class and effort are its contract, so a variant row may budget it but never
    down-class it, and the role file is the one place that says so. Roles a user added through
    `primitive_roots` count the same as shipped ones; a missing catalog is two empty sets, which
    is how an installed hook with no checkout beside it behaves.
    """
    names, fixed = set(), set()
    for directory in primitive_roots(config, root, "roles"):
        for path in sorted(directory.glob("*.md")) if directory.is_dir() else []:
            fields = _frontmatter(path)
            if not fields:
                continue
            names.add(path.stem)
            if fields.get("posture") == "fixed":
                fixed.add(path.stem)
    return names, fixed


def fixed_roles(config=None, root=None):
    return role_catalog(config, root)[1]


def _merge(base, layer):
    """`layer` over `base`, one level into `switches` and two into `rows`."""
    merged = dict(base)
    for key in ("extends", "default_band"):
        if key in layer:
            merged[key] = layer[key]
    merged["switches"] = dict(base.get("switches", {}), **layer.get("switches", {}))
    rows = {name: dict(cells) for name, cells in base.get("rows", {}).items()}
    for name, cells in layer.get("rows", {}).items():
        rows[name] = dict(rows.get(name, {}), **cells)
    merged["rows"] = rows
    return merged


def _round_to(value, step):
    return int((value + step / 2) // step) * step


def table_for(stances=None, config=None, strict=True, root=None):
    """The active cost variant resolved: switches, rows, default band, chain and warnings.

    Budgets carry both figures: `base_*` is what the variant wrote and `budget_*` is that times
    the resolved `budget_multiplier`, so a reader never multiplies twice. A link that cannot be
    followed — no sidecar, unreadable, a schema this release does not know, a name that is not
    an identifier — resolves to the base variant rather than to an empty table.
    """
    stances = dict(DEFAULT_STANCES) if stances is None else stances
    variant = stances.get("cost") or BASE_COST_VARIANT
    roots = stance_roots(config, root)
    roles, fixed = role_catalog(config, root)
    warnings, chain, layers, seen = [], [], [], set()
    name = variant
    while name:
        if name in seen:
            warnings.append("extends cycle at cost variant '" + str(name) + "'")
            break
        if len(chain) >= MAX_EXTENDS_DEPTH:
            warnings.append("extends chain deeper than " + str(MAX_EXTENDS_DEPTH) +
                            " variants, stopped at '" + str(name) + "'")
            break
        seen.add(name)
        data = None
        if not _identifier(name):
            warnings.append("cost variant '" + str(name) + "' is not a primitive identifier")
        else:
            path = sidecar_path(name, roots)
            if path is None:
                warnings.append("cost variant '" + name + "' has no sidecar")
            else:
                data = _load_sidecar(path, strict, warnings)
                version = data.get("schema_version") if data is not None else None
                if data is not None and version != SIDECAR_SCHEMA_VERSION:
                    warnings.append(name + ".json: schema_version " + json.dumps(version) +
                                    " is not one this release reads")
                    data = None
        if data is None:
            if name != BASE_COST_VARIANT and BASE_COST_VARIANT not in seen:
                # An unusable link is the base variant's table, not an empty one.
                name = BASE_COST_VARIANT
                continue
            break
        clean, findings = validate_sidecar(data, roles or None)
        warnings.extend(name + ".json: " + finding for finding in findings)
        chain.append({"variant": name, "source": str(path)})
        layers.append(clean)
        name = clean.get("extends")
    resolved = {}
    for layer in reversed(layers):
        resolved = _merge(resolved, layer)
    switches = resolved.get("switches", {})
    multiplier = switches.get("budget_multiplier", 1)
    rows = {}
    for row_name, cells in sorted(resolved.get("rows", {}).items()):
        row = {"class": cells.get("class"), "effort": cells.get("effort")}
        if row_name in fixed:
            # The role keeps its frontmatter tier and its bindings effort; only budgets apply.
            row["class"], row["effort"], row["posture"] = None, None, "fixed"
        for key, step in zip(BUDGET_KEYS, (100, 1)):
            base = cells.get(key)
            row["base_" + key] = base
            row[key] = None if base is None else _round_to(base * multiplier, step)
        rows[row_name] = row
    return {"cost_variant": variant, "switches": switches, "rows": rows,
            "default_band": resolved.get("default_band"), "extends_chain": chain,
            "class_applies": stances.get("delegation") == "tiered", "warnings": warnings}


def _mode_of(data):
    value = data.get("mode") if isinstance(data, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def modes(config=None, root=None):
    """`({name: path}, [refusal])`: every `modes/<name>.json` in the primitive roots.

    A name two roots both define is a refusal rather than first-wins, so a user root cannot
    silently replace a shipped mode; the first definition is still returned for a hook.
    """
    found, errors = {}, []
    for directory in primitive_roots(config, root, "modes"):
        for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
            name = path.stem
            if not _identifier(name):
                errors.append("mode file " + str(path) + " is not named with lowercase letters, digits and hyphens")
            elif name in found:
                errors.append("mode '" + name + "' is defined in both " + str(found[name]) + " and " + str(path))
            else:
                found[name] = path
    return found, errors


def validate_mode(name, data, config, root=None):
    """Every refusal the mode file `data` earns, as messages; empty when it is sound.

    A mode carries `schema_version` 1, a `description`, and selection keys naming installed
    units only; a stance variant is checked where every layer's is, by `sync`. A core hook it
    switches off is refused where every layer's is, by `core_refusals`.
    """
    where = "mode file " + name
    if not isinstance(data, dict):
        return [where + " is not a JSON object"]
    kinds = selection_kinds(root)
    errors = []
    extra = sorted(set(data) - set(MODE_KEYS) - set(kinds))
    if extra:
        errors.append(where + " may carry schema_version, description and selection keys only, not " +
                      ", ".join("'" + key + "'" for key in extra))
    if data.get("schema_version") != 1:
        errors.append(where + " needs \"schema_version\": 1")
    if not (isinstance(data.get("description"), str) and data["description"].strip()):
        errors.append(where + " needs a nonempty \"description\"")
    declared = None
    for kind in sorted(set(data) & set(kinds)):
        chosen, entry = data[kind], kinds[kind]
        if not isinstance(chosen, dict):
            errors.append(where + " sets " + kind + " to " + json.dumps(chosen) + "; a kind is an object of unit to value")
            continue
        if entry.get("directory"):
            known = set(_units(kind, entry, config, root))
        else:
            if declared is None:
                declared = manifests(config, root)[0]
            known = set(declared.get(kind, {})) | (set(CORE_HOOKS) if kind == "hooks" else set())
        for unit, value in sorted(chosen.items()):
            label = where + " sets " + kind + "." + unit
            if unit not in known:
                errors.append(label + ", which is not an installed " + kind + " unit")
            elif entry.get("value") == "switch" and value not in SWITCH_STATES:
                errors.append(label + " to " + json.dumps(value) + "; a " + kind + " unit is on or off")
            elif entry.get("value") == "variant" and not (isinstance(value, str) and value.strip()):
                errors.append(label + " to " + json.dumps(value) + "; a " + kind + " unit names a variant")
    return errors


def _mode_file(name, config, strict, root=None):
    """The selection keys of mode `name`, validated; `{}` when a hook cannot use it.

    Strict callers hear about an unknown mode, a duplicate name, or a mode file that fails
    `validate_mode`, before anything acts on the selection. A hook runs without a mode it cannot
    use, and takes the first root's definition of a duplicated name.
    """
    found, errors = modes(config, root)
    path = found.get(name)
    if errors and strict:
        raise ValueError("\n".join(errors))
    try:
        if path is None:
            raise ValueError("unknown mode '" + name + "'; installed modes: " + (", ".join(sorted(found)) or "none"))
        data = json.loads(path.read_text(encoding="utf-8"))
        problems = validate_mode(name, data, config, root)
        if problems:
            raise ValueError("\n".join(problems))
    except (OSError, ValueError):
        if strict:
            raise
        return {}
    return {key: value for key, value in data.items() if key not in MODE_KEYS}


def _init_split(config):
    """`(typed, defaults)`: the user configuration without, and with only, what init defaulted.

    A unit counts as init's default only while it still holds the value init recorded for it, so
    a value edited in `config.json` afterwards is typed and stays above the mode.
    """
    listed = config.get(INIT_DEFAULTS)
    if not isinstance(listed, dict):
        return config, {}
    typed, defaults = dict(config), {}
    for kind, names in listed.items():
        chosen = config.get(kind)
        if not isinstance(chosen, dict) or not isinstance(names, dict):
            continue
        moved = {unit: chosen[unit] for unit, recorded in names.items()
                 if unit in chosen and chosen[unit] == recorded}
        if moved:
            defaults[kind] = moved
            typed[kind] = {unit: value for unit, value in chosen.items() if unit not in moved}
    return typed, defaults


def layers(config, env, strict, root=None):
    """`(mode, [(source, document)])`, lowest precedence first: the one selection ladder.

    What `harness init` wrote as a default (`init`), the mode, the user configuration, project
    file, session file, then the session's environment sugar: `HARNESS_MODE` and
    `HARNESS_STANCE_*` resolve as the session layer's last word. The mode is whichever layer named
    one last, and its file sits under every explicit layer but over init's defaults.
    """
    project = _project_config(env, strict, root)
    session = _session_config(env, strict, root)
    sugar = {"stances": overrides(env)}
    if (env.get(MODE_VARIABLE) or "").strip():
        sugar["mode"] = env[MODE_VARIABLE]
    user, defaults = _init_split(config if isinstance(config, dict) else {})
    explicit = [("user", user), ("project", project),
                ("session", session), ("session", sugar)]
    mode = None
    for source, data in explicit:
        if _mode_of(data):
            mode = (_mode_of(data), source)
    ladder = [("init", defaults)] if defaults else []
    if mode:
        ladder.append(("mode:" + mode[0], _mode_file(mode[0], config, strict, root)))
    return mode, ladder + explicit


def _selection(config, env, strict, root=None):
    """Every stance's variant in force: the ladder read for `stances` only, without walking units."""
    stances = dict(DEFAULT_STANCES)
    for _, data in layers(config, env, strict, root)[1]:
        stances.update(_stances_of(data))
    return stances


def _units(kind, entry, config, root=None):
    """The installed units of one kind, sorted: stance dimensions, rule, skill or role names.

    A kind the catalog enumerates, `hooks`, counts each listed id whose module is in this checkout.
    """
    if entry.get("units"):
        base = (root or ROOT) / "policy" / "hooks"
        return sorted(unit for unit in entry["units"] if (base / (unit + ".py")).is_file())
    directory, pattern = entry.get("directory"), entry.get("pattern")
    if not directory or not pattern:
        return []
    names = set()
    for source in primitive_roots(config, root, directory):
        names.update(_units_in(source, pattern))
    return sorted(names)


def _units_in(source, pattern):
    return {path.parent.name if "/" in pattern else path.stem
            for path in (source.glob(pattern) if source.is_dir() else [])}


def _manifest_files(config, root=None):
    """`[(path, shipped)]`: the catalog's and the hook kernel's files, then each user root's."""
    base = root or ROOT
    files = [(base / "primitives" / MANIFEST_FILE, True), (base / "policy" / "hooks" / MANIFEST_FILE, True)]
    for source in primitive_roots(config, root, "rules")[1:]:
        files.append((source.parent / MANIFEST_FILE, False))
    return files


def _reference(value, kinds):
    kind, sep, unit = value.partition("/") if isinstance(value, str) else ("", "", "")
    return bool(sep) and kind in kinds and _identifier(unit)


def validate_manifest(kind, unit, entry, kinds):
    """The one message `entry` earns as `kind/unit`'s manifest, or None when it is sound.

    `kinds` is the switch kinds a dependency or conflict may name, as `kind/unit`.
    """
    name = kind + "/" + unit
    if not isinstance(entry, dict):
        return name + " manifest is not an object"
    unknown = sorted(set(entry) - set(MANIFEST_FIELDS))
    if unknown:
        return name + " manifest has unknown field(s): " + ", ".join(unknown)
    for field in MANIFEST_FIELDS:
        if field not in entry:
            return name + " manifest is missing '" + field + "'"
    strings = lambda value: isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)
    if not strings(entry["claims"]) or not entry["claims"]:
        return name + " manifest 'claims' is a nonempty list of what the module is for"
    if not strings(entry["surface"]) or not entry["surface"] or set(entry["surface"]) - set(SURFACES):
        return name + " manifest 'surface' is a nonempty list drawn from " + ", ".join(SURFACES)
    if not strings(entry["instruments"]):
        return name + " manifest 'instruments' is a list of instrument ids, empty when nothing measures it"
    slot = entry["slot"]
    if slot is not None and not (isinstance(slot, dict) and set(slot) == {"id", "cedes"}
                                 and _identifier(slot["id"]) and isinstance(slot["cedes"], bool)):
        return name + " manifest 'slot' is null or {\"id\": <identifier>, \"cedes\": true|false}"
    for field in ("dependencies", "conflicts"):
        refs = entry[field]
        if not isinstance(refs, list) or not all(_reference(v, kinds) for v in refs):
            return name + " manifest '" + field + "' is a list of kind/unit, kind one of " + ", ".join(kinds)
        if name in refs:
            return name + " manifest '" + field + "' names the module itself"
    return None


def manifests(config=None, root=None, kinds=None):
    """`({kind: {unit: manifest}}, [refusal])` from every manifest file, each entry validated.

    A shipped file must parse; a user root may carry none. One module declared in two files is a
    refusal, so a user root cannot rewrite what a shipped module needs or collides with.
    """
    kinds = [k for k, e in selection_kinds(root).items() if e.get("value") == "switch"] if kinds is None else kinds
    declared, origin, errors = {kind: {} for kind in kinds}, {}, []
    for path, shipped in _manifest_files(config, root):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, NotADirectoryError):
            continue
        except (OSError, ValueError) as exc:
            errors.append(str(path) + " is not readable JSON: " + str(exc))
            continue
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            errors.append(str(path) + " is not a schema_version 1 manifest file")
            continue
        for kind, units in data.items():
            if kind == "schema_version":
                continue
            if kind not in kinds or not isinstance(units, dict):
                errors.append(str(path) + " declares '" + kind + "', which is not a switch kind")
                continue
            for unit, entry in sorted(units.items()):
                problem = validate_manifest(kind, unit, entry, kinds)
                if problem:
                    errors.append(problem)
                elif unit in declared[kind]:
                    errors.append(kind + "/" + unit + " has a manifest in both " + origin[kind, unit] +
                                  " and " + str(path))
                else:
                    declared[kind][unit], origin[kind, unit] = entry, str(path)
    return declared, errors


def manifest_refusals(document, declared, required, installed=None):
    """Every refusal a resolved selection earns from its modules' manifests, as messages.

    `required` is `{kind: units}` that must declare a manifest: the shipped ones. `installed` is
    `{kind: units}` that exist, for the kinds that have a module directory; a kind without one,
    such as hooks, counts a unit as present when it declares a manifest. Only switched-on modules
    take a slot, need a dependency or collide; a module switched off asks nothing.
    """
    installed = {} if installed is None else installed
    errors = []
    for kind in sorted(required):
        for unit in sorted(required[kind]):
            if unit not in declared.get(kind, {}):
                errors.append(kind + "/" + unit + " has no manifest; declare " +
                              ", ".join(MANIFEST_FIELDS) + " in " + MANIFEST_FILE)
    on = {kind + "/" + unit: declared[kind][unit] for kind in sorted(declared)
          for unit in sorted(declared[kind]) if (document.get(kind) or {}).get(unit) == "on"}
    slots = {}
    for name, entry in on.items():
        for needed in entry["dependencies"]:
            kind, _, unit = needed.partition("/")
            if unit not in installed.get(kind, declared.get(kind, {})):
                errors.append(name + " depends on " + needed + ", which is not installed")
            elif (document.get(kind) or {}).get(unit) != "on":
                errors.append(name + " depends on " + needed + ", which is not switched on")
        for other in entry["conflicts"]:
            if other in on and (other < name or name not in on[other]["conflicts"]):
                errors.append(name + " conflicts with " + other + "; switch one of them off")
        if entry["slot"]:
            slots.setdefault(entry["slot"]["id"], []).append(name)
    for slot, names in sorted(slots.items()):
        # A ceding claimant yields; the slot is refused while two or more still hold it.
        holders = [name for name in names if not on[name]["slot"]["cedes"]]
        if len(holders) > 1:
            errors.append(", ".join(holders) + " each claim the slot '" + slot +
                          "'; switch one off, or have one declare that it cedes the slot")
    return errors


def core_refusals(document, sources, config):
    """One message per core hook `document` switches off while the user has not acknowledged it.

    The acknowledgement is read from the user configuration only: a project, session or mode file
    cannot carry it, so no file a repository ships can turn enforcement off on its own authority.
    """
    if isinstance(config, dict) and config.get(CORE_ACK) is True:
        return []
    hooks = document.get("hooks") or {}
    return [(sources.get("hooks") or {}).get(unit, "a layer") + " switches the core hook " + unit +
            " off; set " + CORE_ACK + " true in the user configuration to allow it"
            for unit in CORE_HOOKS if hooks.get(unit) == "off"]


def measurement(manifest):
    """How a report shows a module: its instruments, or `unmeasured`, never `no effect`."""
    instruments = (manifest or {}).get("instruments") or []
    return "measured by " + ", ".join(instruments) if instruments else "unmeasured"


def selection(env=None, strict=True, config=None, root=None):
    """Every unit of every kind with its value, and the source that set it.

    Returns the selection document — `mode`, then `{kind: {unit: value}}` for each kind in
    `catalog.KINDS` — plus `sources` in the same shape, each one of `default`, `init`,
    `mode:<name>`, `user`, `project` or `session`, in that precedence, and `shadowed`,
    `{kind: {unit: source}}` for every mode key a higher layer overrode. A variant kind's default is the built-in
    stance or null; a switch kind's is `on`. `config` is the user configuration when the caller
    has already read it. A kind that is not an object, or a switch value other than `on` or `off`,
    is an error when strict and selects nothing otherwise; a unit a layer names that nothing installs is still reported.
    So is a core hook switched off without `core_switches_acknowledged` true in the user
    configuration, which resolves `on` when not strict (`core_refusals`).
    Strict resolution also enforces the switch kinds' manifests (AD-22): a shipped module without
    one, a field missing or malformed, a switched-on module whose dependency is not on, two that
    conflict, or two that claim one slot with neither ceding it, is a `ValueError` naming them.
    """
    env = os.environ if env is None else env
    config = _user_config(env, strict) if config is None else config
    kinds = selection_kinds(root)
    if strict:
        duplicates = modes(config, root)[1]
        if duplicates:
            raise ValueError("\n".join(duplicates))
    mode, ladder = layers(config, env, strict, root)
    result, sources = {"mode": mode[0] if mode else None}, {"mode": mode[1] if mode else "default"}
    shadowed = {}  # {kind: units the mode layer set}
    for kind, entry in kinds.items():
        switch = entry.get("value") == "switch"
        result[kind] = {unit: ("on" if switch else DEFAULT_STANCES.get(unit))
                        for unit in _units(kind, entry, config, root)}
        if not switch:
            result[kind].update(DEFAULT_STANCES)
        sources[kind] = {unit: "default" for unit in result[kind]}
        for source, data in ladder:
            if not isinstance(data, dict) or kind not in data:
                continue
            chosen = data[kind]
            if not isinstance(chosen, dict):
                if strict:
                    raise ValueError(source + " sets " + kind + " to " + json.dumps(chosen) +
                                     "; a kind is an object of unit to value")
                continue
            for unit, value in chosen.items():
                value = value.strip() if isinstance(value, str) else value
                if switch and value not in SWITCH_STATES:
                    if strict:
                        shown = "'" + value + "'" if isinstance(value, str) else json.dumps(value)
                        raise ValueError(source + " sets " + kind + "." + unit + " to " + shown +
                                         "; a " + kind + " unit is on or off")
                    continue
                if not isinstance(value, str) or not value:
                    continue
                if source.startswith("mode:"):
                    shadowed.setdefault(kind, set()).add(unit)
                result[kind][unit], sources[kind][unit] = value, source
    refusals = core_refusals(result, sources, config)
    if refusals and strict:
        raise ValueError("\n".join(refusals))
    if refusals:
        # A hook resolving non-strictly keeps enforcing: an unacknowledged `off` is not one.
        for unit in CORE_HOOKS:
            if result.get("hooks", {}).get(unit) == "off":
                result["hooks"][unit], sources["hooks"][unit] = "on", "default"
    for kind in kinds:
        result[kind] = dict(sorted(result[kind].items()))
        sources[kind] = dict(sorted(sources[kind].items()))
    if strict:
        switches = [kind for kind, entry in kinds.items() if entry.get("value") == "switch"]
        declared, errors = manifests(config, root, switches)
        base = (root or ROOT) / "primitives"
        required = {kind: _units_in(base / kinds[kind]["directory"], kinds[kind]["pattern"])
                    for kind in switches if kinds[kind].get("directory") and kinds[kind].get("pattern")}
        installed = {kind: set(_units(kind, kinds[kind], config, root)) for kind in required}
        # Hooks have no module directory, so `installed` keeps counting one as present when it
        # declares a manifest; each hook this checkout ships must declare one.
        required.update({kind: set(_units(kind, kinds[kind], config, root))
                         for kind in switches if kinds[kind].get("units")})
        errors += manifest_refusals(result, declared, required, installed)
        if errors:
            raise ValueError("module manifest: " + "\nmodule manifest: ".join(errors))
    result["sources"] = sources
    # A mode key is shadowed when a layer above the mode set the same unit, whatever its value.
    shadowed = {kind: {unit: sources[kind][unit] for unit in sorted(units)
                       if not sources[kind][unit].startswith("mode:")} for kind, units in sorted(shadowed.items())}
    result["shadowed"] = {kind: units for kind, units in shadowed.items() if units}
    return result


# The profile fingerprint (AD-22, AD-23): which profile wrote a ledger row. The configuration it
# covers is the user keys that reach the model or a hook; installer switches, remote control and
# integrations change neither. `primitive_roots` is left out because the modules it adds are
# hashed by content, so one profile on two machines matches. A row that predates the field is
# unattributed, and the bare arm of a replay, which loads no harness, is `BARE_FINGERPRINT`.
FINGERPRINT_KEY = "profile_fingerprint"
FINGERPRINT_CONFIG_KEYS = ("identity", "permissions", "permissions_bypass_acknowledged",
                           "plan_allow_tools", "telemetry", "governance")
BARE_FINGERPRINT = "bare"
_FINGERPRINTS = {}


def _unit_files(entry, unit, value, config, root=None):
    """`[(label, path)]` for the files one unit's content is, across every primitive root.

    The label is the root's position and the path inside it, so a checkout's own location never
    reaches the digest. A skill is its whole directory; a stance is its selected variant and the
    sidecar beside it; any other kind is its one file.
    """
    directory, pattern = entry.get("directory"), entry.get("pattern")
    if not directory or not pattern or not _identifier(unit):
        return []
    files = []
    for index, source in enumerate(primitive_roots(config, root, directory)):
        if pattern == "*/*.md":
            if not _identifier(value):
                continue
            found = [source / unit / (value + suffix) for suffix in (".md", ".json")]
        elif "/" in pattern:
            base = source / unit
            found = sorted(path for path in base.rglob("*") if path.is_file() and not any(
                part.startswith(".") or part == "__pycache__" for part in path.relative_to(base).parts)) \
                if base.is_dir() else []
        else:
            found = [source / pattern.replace("*", unit)]
        files += [(str(index) + "/" + path.relative_to(source).as_posix(), path)
                  for path in found if path.is_file()]
    return files


def _content_digest(files):
    """The sha256 of the labelled files' bytes, or None when the unit has no file anywhere."""
    if not files:
        return None
    digest = hashlib.sha256()
    for label, path in files:
        try:
            data = path.read_bytes()
        except OSError:
            data = b"\0unreadable"
        digest.update(label.encode("utf-8") + b"\0" + str(len(data)).encode("ascii") + b"\0" + data)
    return digest.hexdigest()


def _version(root=None):
    try:
        return ((root or ROOT) / "VERSION").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def profile(env=None, config=None, root=None):
    """The document the profile fingerprint digests, resolved non-strict from the one ladder.

    Every switched-on module with its content digest, every stance with its variant and that
    variant's digest, the configuration values `FINGERPRINT_CONFIG_KEYS` names, and the harness
    version. A switched-off module is absent, as it is from the session. A mode is not named:
    what it selects is.
    """
    env = os.environ if env is None else env
    config = _user_config(env, False) if config is None else config
    document = selection(env, strict=False, config=config, root=root)
    modules, stances = {}, {}
    for kind, entry in selection_kinds(root).items():
        units = document.get(kind) or {}
        if entry.get("value") == "switch":
            on = {unit: _content_digest(_unit_files(entry, unit, None, config, root))
                  for unit, value in units.items() if value == "on"}
            if on:
                modules[kind] = on
        else:
            stances.update({unit: {"variant": value,
                                   "digest": _content_digest(_unit_files(entry, unit, value, config, root))}
                            for unit, value in units.items() if value})
    settings = config if isinstance(config, dict) else {}
    return {"harness_version": _version(root), "modules": modules, "stances": stances,
            "config": {key: settings[key] for key in FINGERPRINT_CONFIG_KEYS if key in settings}}


def _file_state(path):
    """`(mtime_ns, size)` of a file, or None when it cannot be read: the fingerprint cache's key."""
    try:
        state = path.stat()
    except OSError:
        return None
    return (state.st_mtime_ns, state.st_size)


def fingerprint(env=None, config=None, root=None):
    """The profile fingerprint: the sha256 of `profile()` as canonical JSON. See `FINGERPRINT_KEY`.

    Identical inputs give one fingerprint on every run and machine, and any module, stance or
    setting that differs gives another. Remembered per process for one environment and one state
    of the configuration files, since a hook stamps it on each row it writes: rewriting the user
    configuration or a named selection file gives a fresh digest. A module edited under a running
    process is not seen until the next one, which is where the checkout's edits land.
    """
    env = os.environ if env is None else env
    files = [config_path(env)] + [Path(env[name]).expanduser() for name in
                                  ("HARNESS_PROJECT_CONFIG", "HARNESS_SESSION_CONFIG") if env.get(name)]
    key = (str(root or ROOT), tuple(sorted((name, value) for name, value in env.items()
                                           if name in ("HOME", "HARNESS_HOME", MODE_VARIABLE,
                                                       "HARNESS_PROJECT_CONFIG", "HARNESS_SESSION_CONFIG")
                                           or name.startswith(PREFIX))),
           tuple(_file_state(path) for path in files))
    if config is None and key in _FINGERPRINTS:
        return _FINGERPRINTS[key]
    text = json.dumps(profile(env, config, root), sort_keys=True, separators=(",", ":"))
    value = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if config is None:
        _FINGERPRINTS[key] = value
    return value


# Per-module attribution of context tokens (AD-23). Context is shared, so what each module put
# there is estimated, never measured, and carries AD-12's soft-estimate label and its method.
# The estimate is of resident text only: what is loaded before the first prompt. A listed kind
# is resident as its listing entry, its name and description, and its body loads on demand; a
# hook's context arrives per event at run time and is not estimated here.
ATTRIBUTION_KEY = "context_attribution"
SOFT_ESTIMATE = "soft estimate"
CHARS_PER_TOKEN = 4.0
ATTRIBUTION_METHOD = ("chars/4 of resident text: a rule or stance variant whole; a skill, role or "
                      "workflow its name and description")
LISTED_KINDS = ("skills", "roles", "workflows")


def _listed_description(path):
    """The frontmatter `description` a session lists, folded and literal continuation lines
    included, read the way `scripts/cost_bench.py` counts it for the static tier."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return ""
    if not lines or lines[0].strip() != "---":
        return ""
    out, taking = [], False
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("description:"):
            out.append(line.split(":", 1)[1].strip())
            taking = True
        elif taking and line[:1] in (" ", "\t"):
            out.append(line.strip())
        else:
            taking = False
    return " ".join(part for part in out if part and part not in (">", "|", ">-", "|-", ">+", "|+"))


def _resident_text(kind, entry, unit, value, config, root=None):
    """The text one unit keeps resident, from the first primitive root holding it, or None."""
    directory, pattern = entry.get("directory"), entry.get("pattern")
    if not directory or not pattern or not _identifier(unit):
        return None
    if pattern == "*/*.md" and not _identifier(value):
        return None
    for source in primitive_roots(config, root, directory):
        if pattern == "*/*.md":
            path = source / unit / (value + ".md")
        else:
            path = source / pattern.replace("*", unit)
        if not path.is_file():
            continue
        if kind in LISTED_KINDS:
            return (_frontmatter(path).get("name") or unit) + ": " + _listed_description(path)
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            return None
    return None


def context_attribution(env=None, config=None, root=None):
    """`{"estimand", "method", "modules": {"kind/unit": tokens}}` for the selection in force.

    Resolved non-strict from the ladder `profile()` reads, so one module switched off removes
    that module's entry and changes no other. Every switched-on module and every stance with
    resident text has an entry; a unit installed nowhere, and a hook, has none.
    """
    env = os.environ if env is None else env
    config = _user_config(env, False) if config is None else config
    document = selection(env, strict=False, config=config, root=root)
    modules = {}
    for kind, entry in selection_kinds(root).items():
        switch = entry.get("value") == "switch"
        for unit, value in sorted((document.get(kind) or {}).items()):
            if (switch and value != "on") or not value:
                continue
            text = _resident_text(kind, entry, unit, None if switch else value, config, root)
            if text is not None:
                modules[kind + "/" + unit] = int(round(len(text) / CHARS_PER_TOKEN))
    return {"estimand": SOFT_ESTIMATE, "method": ATTRIBUTION_METHOD, "modules": modules}


def resolve(env=None, strict=True, table=False):
    """The posture in force: `{"stances": {dimension: variant}}`, every dimension present.

    A missing config file is the default set and never an empty map, which would read as
    "no stance in force". The cost table is opt-in with `table=True`, because most callers are
    hot-path hooks answering one question and walking sidecars for them would be pure cost.
    """
    env = os.environ if env is None else env
    config = _user_config(env, strict)
    stances = _selection(config, env, strict)
    if not table:
        return {"stances": stances}
    return dict(table_for(stances, config, strict=strict), stances=stances)


def cost_table(env=None, strict=False, root=None):
    """The active cost variant's table for a caller that has only an environment.

    Non-strict by default: an unusable sidecar somewhere on the chain is a warning in the table,
    never a reason for the work in hand to stop.
    """
    env = os.environ if env is None else env
    config = _user_config(env, strict)
    return table_for(_selection(config, env, strict, root), config, strict=strict, root=root)


def selected(name, fallback=None, env=None, strict=True):
    """One dimension's variant, or `fallback` when nothing on the ladder names it.

    Reads the stance ladder only: a hook asking one question should not pay for the cost table.
    """
    env = os.environ if env is None else env
    return _selection(_user_config(env, strict), env, strict).get(name) or fallback


def permissions(env=None, strict=False):
    """The permission posture the user selected, or `inherit` when the config names none.

    Non-strict by default: a posture nobody can read is not a posture the user chose, and a
    caller that widens authority on it would be doing so on a file it could not open.
    """
    env = os.environ if env is None else env
    value = _user_config(env, strict).get("permissions")
    return value.strip() if isinstance(value, str) and value.strip() else "inherit"


def plan_allow_tools(env=None, strict=False):
    """The tool-name globs the user allows during plan mode, `fnmatch` style. Empty by default.

    Nothing is inferred: a PreToolUse payload carries no read-only hint for an MCP tool, so the
    only thing that can say a tool is safe to investigate with is the user naming it. A value
    that is not a list of non-empty strings names nothing.
    """
    env = os.environ if env is None else env
    value = _user_config(env, strict).get(PLAN_TOOLS_KEY)
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def row_for(table, role):
    """The row that governs one role: its own, or its band's when it is a band worker.

    The bands exist so a variant can price work it cannot name a role for, and the band workers
    are the roles that carry a band into a spawn. A row keyed by the role beats the band's,
    because naming the role is the more specific thing a variant can say.
    """
    rows = table.get("rows") if isinstance(table, dict) else None
    rows = rows if isinstance(rows, dict) else {}
    if role in rows:
        return rows[role]
    for band, name in BAND_ROLES.items():
        if name == role:
            return rows.get(band)
    return None


def _sibling(name):
    """A module beside this file, or None. Resolving a posture must never raise on an import."""
    try:
        spec = importlib.util.spec_from_file_location(
            "harness_" + name.replace("-", "_"), str(Path(__file__).resolve().parent / (name + ".py")))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def budget_figures(row):
    """The halves of one row's soft budget worth stating, keyed as the row keys them.

    A null half is left out rather than written as "no budget", which would read as permission to
    spend without limit, and a half under one unit goes with it: "about 0 output tokens" would
    read as an instruction to do nothing, which is a budget nobody wrote.
    """
    if not isinstance(row, dict):
        return {}
    return {key: row[key] for key in BUDGET_KEYS
            if isinstance(row.get(key), int) and not isinstance(row[key], bool) and row[key] >= 1}


def budget_sentence(row):
    """The sentence one row's soft budget is stated in, or None when the row prices nothing.

    The one wording for every brief the harness writes: a native spawn's, appended by
    `brief-guard`, and an isolated role worker's, appended by `harness role run`. An agent cannot
    see the cost variant that priced it, so the row's figures are stated in the brief — every
    number from the table and none of the words. It is soft, because a hard cap would truncate
    the work rather than the spend.
    """
    parts = ["about {:,} {}".format(value, BUDGET_UNITS[key])
             for key, value in budget_figures(row).items()]
    if not parts:
        return None
    return ("\n\nExpected spend: " + " and ".join(parts) + ". Past that, finish if you are "
            "close; otherwise return what you have and say why.")


def budget_stated(text, detectors=None):
    """True when this brief already prices itself, or when nothing here can tell.

    What counts as a stated budget belongs to `rule-detectors.py` and not to a second copy per
    caller: a sentence the detector still reads as missing would be appended forever and the
    number would never move. A registry that will not load, or one without the pattern, is
    "cannot tell", which appends nothing.
    """
    module = _sibling("rule-detectors") if detectors is None else detectors
    pattern = getattr(module, "BUDGET_RE", None)
    return pattern is None or bool(pattern.search(text or ""))


def tier_models(runtime="claude-code", root=None):
    """The adapter's `{class: native model}`, strongest class first, or `{}` when unreadable.

    Model names belong to `adapters/<runtime>/bindings.json`, never to hook code: a lineup
    change is a data edit, and a caller that gets nothing says so rather than guessing.
    """
    path = (root or ROOT) / "adapters" / runtime / "bindings.json"
    try:
        tiers = json.loads(path.read_text(encoding="utf-8"))["tiers"]
        return {name: tiers[name] for name in TIER_CLASSES
                if isinstance(tiers.get(name), str) and tiers[name].strip()}
    except Exception:
        return {}


def ladder(runtime="claude-code", root=None):
    """The adapter's native models, strongest class first, or `[]` when it cannot be read."""
    return list(tier_models(runtime, root).values())

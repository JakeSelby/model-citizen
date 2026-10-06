#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The decision log: one local, append-only record per judgment a harness hook makes.

`~/.local/state/agent-harness/decisions.jsonl`, beside `usage.jsonl`. The ledger says what a
session spent; this says what the harness decided and, where the session later showed it, how
the decision turned out. Nothing here reaches the network, nothing here is model-visible, and
no row is ever rewritten: an outcome is a second record joined to the first by `decision_id`,
so a reader of the file sees the decision exactly as the hook made it.

    {"kind": "decision", "decision_id": "…", "point": "grade-bash", "session_id": "…",
     "ts": "2026-09-21T18:04:05Z", "input_sha256": "…", "input": "git push --force",
     "deterministic_answer": "ask", "outcome": null, "runtime": "claude-code",
     "harness_version": "0.12.0", "profile_fingerprint": "…", "module": "hooks/grade-bash",
     "schema_version": 1}
    {"kind": "outcome", "decision_id": "…", "point": "grade-bash", "session_id": "…",
     "ts": "…", "outcome": "ran", "harness_version": "0.12.0", "profile_fingerprint": "…",
     "module": "hooks/grade-bash", "schema_version": 1}

`input` is the text the hook judged, capped at MAX_INPUT (2 KiB) — a command or a brief, never
tool output and never assistant prose. `input_sha256` is over the **uncapped** text, so two
rows whose capped text is identical are still told apart, and a long command can be matched
against its own later events.

`telemetry.completion_claim`, off by default, adds `completion_claim` and its hash to a
stop-gate row: the tail of the turn's final assistant message, read from the transcript the
Stop event names, or a null claim beside the reason there is none. It is the only assistant
prose this file ever holds, which is why it is a switch of its own and why it is off. See
`claim_fields`.

This module sits beside the hooks rather than in `lib/harness_core`, for the reason
`telemetry.py` gives: a hook is reached through `~/.claude/hooks/harness` and nothing above
that directory resolves from it. `lifecycle.py` loads it with its own `load()`.

Every write is wrapped: a logging failure counts in `errors()` and changes no hook's decision,
output or exit status. See docs/usage.md for the report and docs/telemetry.md for the switch.
"""
import hashlib
import importlib.util
import json
import os
import re
import time
import uuid
from pathlib import Path

# The points that write. Named here so the report can list a point that has not fired yet, and
# so a typo in a call site is a test failure rather than a silent new group.
POINTS = ("grade-bash", "stop-gate", "tier-agent-spawns", "delegation-nudge",
          "brief-guard", "evasion-deny")
# The points that only record what an advisory hook did. Kept apart from POINTS, which is also
# the set a decision provider's modes may name (`decisions/controls.py`): none of these asks one.
HOOK_POINTS = ("filter-output", "allow-readonly-bash", "validate-plan-card", "harness-session",
               "workspace-session", "allow-plan-webfetch", "stage-user-files",
               "neutralize-tool-output")

# The module that owns each point's decision (AD-23): the hook id whose logic made it, named as a
# selection reference, `hooks/<id>`. The integration notice runs in the spawn path
# `tier-agent-spawns` owns. Role confinement, framework and evasion refusals and the Workflow
# launch guard have no id, since no switch turns them off, so they name `null`, as does any
# other point no hook owns, such as `decision-provider`, rather than a guess.
MODULE_KEY = "module"
POINT_MODULES = {
    "grade-bash": "hooks/grade-bash",
    "stop-gate": "hooks/stop-gate",
    "tier-agent-spawns": "hooks/tier-agent-spawns",
    "delegation-nudge": "hooks/tier-agent-spawns",
    "brief-guard": "hooks/brief-guard",
    "evasion-deny": None,
    "role-confinement": None,
    "framework-spawn": None,
    "workflow-launch": None,
    "integration-descriptor": "hooks/tier-agent-spawns",
    "intent-overlap": "hooks/intent-overlap",
    "steer-polling": "hooks/steer-polling",
    "session-caps": "hooks/session-caps",
    # `citizen intent merge` records a landing from the command line; no hook makes it.
    "landing-merge": None,
    # One row per decision `grade-bash` asks the configured decision provider for.
    "governance": "hooks/grade-bash",
    # The hooks that answer for themselves; what each row carries is in docs/runtime-controls.md.
    "filter-output": "hooks/filter-output",
    "allow-readonly-bash": "hooks/allow-readonly-bash",
    "validate-plan-card": "hooks/validate-plan-card",
    "harness-session": "hooks/harness-session",
    "workspace-session": "hooks/workspace-session",
    "allow-plan-webfetch": "hooks/allow-plan-webfetch",
    "stage-user-files": "hooks/stage-user-files",
    "neutralize-tool-output": "hooks/neutralize-tool-output",
}

# 2 KiB. Far past any command or the head of a brief, and small enough that a session's worth of
# rows stays a file a person can read. The hash is over the uncapped text, so the cap loses
# evidence, never identity.
MAX_INPUT = 2048

# A Bash ask with no matching PostToolUse by the end of the session. Not "denied": a user who
# refused, a user who interrupted the turn and a session that crashed all look the same here,
# and naming one of them would put a label in the file that nobody measured.
NOT_RUN = "not_run"
RAN = "ran"

# The sampled allows: a fraction of the Bash commands the harness let through without a prompt,
# written as `grade-bash` rows with `deterministic_answer: allow` and `sampled: true`. They are
# negatives for shadow evaluation — a check that may only tighten an allow into an ask has
# nothing to measure its false alarms against otherwise — and they carry no outcome: "it ran"
# says nothing about whether declining to interrupt was right. One in DEFAULT_SAMPLE_RATE by
# default, `telemetry.allow_sample_rate` to change it and 0 to stop it. The choice is the
# command's own hash, so a rerun of the same corpus samples the same commands, and the row
# names the rate it was drawn at so a reader knows the denominator.
DEFAULT_SAMPLE_RATE = 20

# A sampled row is the one place this log writes text nobody prompted about, so the text is
# redacted first and the row holds nothing but the redacted text: the value of every assignment
# and of every credential flag, every shape the rule detectors match, and the home directory as
# `~` so no username reaches the row. Those shapes are read out of the vendored measurement
# engine — see `secret_shapes` — so there is one list and this file holds no copy of it.
# `input_sha256` on a sampled row is over the **redacted** text, unlike every other row: the
# hash of an original beside the redacted text is a dictionary attack on a short secret, which
# is the one way a value could be recovered from the row it was taken out of. Identity within
# the sample survives, because two rows that redact alike are alike in all this row kept.
REDACTED = "<redacted>"
# The engine the shapes come from, at the version `rule-detectors.py` pins: one list, one file,
# one pin. `tests/test_allow_sampling.py` fails when this name and that import drift apart.
ENGINE_WHEEL = "ruleprobe-0.1.0-py3-none-any.whl"
SHAPES_MODULE = "ruleprobe/detectors/common.py"
SHAPES_NAME = "SECRET_PATTERNS"
# A value is a quoted string or a bare word, so `API_TOKEN="abc def"` loses the whole of it.
VALUE = r'''"[^"]*"|'[^']*'|\S+'''
# An assignment starts a word — after nothing, after whitespace, or after the `;`, `&&`, `|` or
# `(` that starts the next command — so `FOO=secret cmd` loses its value where the `value` of
# `cmd --flag=value` is kept. A credential flag is the other way a secret reaches a command
# line, long or short, with or without a space after it.
ASSIGNMENT_RE = re.compile(r"(^|[\s;&|(])([A-Za-z_][A-Za-z0-9_]*)=(" + VALUE + ")")
CREDENTIAL_FLAGS = ("password", "passwd", "pass", "username", "user", "token", "api-key",
                    "api_key", "apikey", "secret", "key")
FLAG_RE = re.compile(r"(--(?:" + "|".join(CREDENTIAL_FLAGS) + r")(?:=|\s+))(" + VALUE + ")",
                     re.IGNORECASE)
# The short form takes its value attached, as `-phunter2` does; `-p` with a space after it is
# `mkdir -p dir` far more often than it is a password, and that value is kept.
SHORT_FLAG_RE = re.compile(r"(^|[\s;&|(])(-[pu])(" + VALUE + ")")

_SHAPES = []

# The completion claim: the tail of the turn's final assistant message, on a stop-gate row and
# nowhere else. It is the one place this log holds model prose, so it has its own switch and
# that switch is off. 2 KiB of it, measured in bytes as `input` is, hashed over the whole.
MAX_CLAIM = 2048
# The read is the last CLAIM_TAIL_BYTES of the file, so it costs the same on a transcript of any
# size, and a file past MAX_TRANSCRIPT is not opened at all — nothing that large is a transcript
# whose last line this hook should be seeking to inside a Stop hook's budget.
CLAIM_TAIL_BYTES = 256 * 1024
MAX_TRANSCRIPT = 256 * 1024 * 1024
# Why a row carries no claim, recorded on the row itself. `no_transcript_path` is the runtime's
# gap and the rest are the file's; `error` is the reader raising, which nothing has been seen to
# do. A reader of the log tells a claim nobody made from a claim nobody could read.
CLAIM_MISSES = ("no_transcript_path", "unreadable", "oversized", "no_claim", "error")

_CONFIG = []
_ERRORS = [0]


def home():
    return Path(os.environ.get("HARNESS_HOME") or os.environ.get("HOME") or Path.home())


def state_dir():
    return home() / ".local" / "state" / "agent-harness"


def path():
    return state_dir() / "decisions.jsonl"


def config_path():
    return home() / ".config" / "agent-harness" / "config.json"


def read_config():
    """The user's config, read at most once per process. `{}` when there is none to read."""
    if not _CONFIG:
        try:
            with open(str(config_path()), encoding="utf-8") as stream:
                data = json.load(stream)
        except (OSError, ValueError):
            data = {}
        _CONFIG.append(data if isinstance(data, dict) else {})
    return _CONFIG[0]


def enabled(cfg=None):
    """Whether decisions are logged: `telemetry.decisions`, which defaults to on.

    Collection is local and on by default, like the usage ledger it sits beside, because the
    labels are only worth having from the day the hook starts writing them. `false` turns it
    off and the harness writes nothing at all — no file, no directory. A `telemetry` block that
    is not an object is a configuration nobody can honour, and writes nothing either.
    """
    cfg = read_config() if cfg is None else cfg
    block = cfg.get("telemetry") if isinstance(cfg, dict) else None
    if block is None:
        return True
    if not isinstance(block, dict):
        return False
    return block.get("decisions", True) is True


def claim_enabled(cfg=None):
    """Whether a stop-gate row carries the completion claim: `telemetry.completion_claim`.

    Off by default, unlike `decisions` beside it, because this is the only field in the log that
    holds assistant prose: a row that quotes the turn's last words is a different thing to keep
    on a shared machine from a row holding a command. It is no part of `export` either — a
    decision row reaches no endpoint whatever `export` says.
    """
    cfg = read_config() if cfg is None else cfg
    block = cfg.get("telemetry") if isinstance(cfg, dict) else None
    if not isinstance(block, dict):
        return False
    return block.get("completion_claim", False) is True


def sample_rate(cfg=None):
    """One in how many allowed commands is logged: `telemetry.allow_sample_rate`, 20 by default.

    0 stops the sampling and writes no allow row at all. A value this module cannot honour is
    read as 0 rather than as the default: `telemetry.settings` refuses it by name when the CLI
    reads the same block, and a hook that cannot read its own setting must not log more than
    the user asked for. `telemetry.decisions: false` turns this off with everything else.
    """
    cfg = read_config() if cfg is None else cfg
    block = cfg.get("telemetry") if isinstance(cfg, dict) else None
    if block is None:
        return DEFAULT_SAMPLE_RATE
    if not isinstance(block, dict):
        return 0
    rate = block.get("allow_sample_rate", DEFAULT_SAMPLE_RATE)
    if isinstance(rate, bool) or not isinstance(rate, int) or rate < 0:
        return 0
    return rate


def in_sample(text, rate):
    """Whether this command is one of the one-in-`rate` that are logged.

    The command's own hash, never a random draw and never a clock: the same corpus replayed
    through this function samples the same commands, which is what makes a measurement taken
    against these rows reproducible.
    """
    if not rate or not text:
        return False
    return int(digest(text)[:8], 16) % rate == 0


# Retention. The log is append-only, so without a cap it grows for as long as the harness runs:
# 20 MB in its first thirteen days, most of it routine provider approvals. When an append finds
# the file at `telemetry.decision_log_max_bytes` or past it, the file is renamed to
# `decisions.jsonl.1`, each older `.N` moves up one, and the one past
# `telemetry.decision_log_keep` is removed; the next row starts a new file. A rotated file is never
# rewritten either, so a row still reads exactly as the hook wrote it. `max_bytes` of 0 turns
# rotation off; `keep` of 0 keeps no rotated file at all.
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_KEEP = 3


def _whole_setting(name, default, cfg=None):
    """A non-negative whole number from the `telemetry` block, or `default` for anything else.

    `telemetry.settings` refuses a bad value by name when the CLI reads the block; a hook that
    meets one keeps the default, because retention that stops working is a disk that fills.
    """
    cfg = read_config() if cfg is None else cfg
    block = cfg.get("telemetry") if isinstance(cfg, dict) else None
    value = block.get(name, default) if isinstance(block, dict) else default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return default
    return value


def max_bytes(cfg=None):
    """The size at which the log rotates: `telemetry.decision_log_max_bytes`, 0 for never."""
    return _whole_setting("decision_log_max_bytes", DEFAULT_MAX_BYTES, cfg)


def keep(cfg=None):
    """How many rotated files are kept: `telemetry.decision_log_keep`."""
    return _whole_setting("decision_log_keep", DEFAULT_KEEP, cfg)


def rotated(target, count):
    """`target`'s rotated siblings, `.1` (newest) to `.count`."""
    target = Path(target)
    return [target.with_name(target.name + "." + str(n)) for n in range(1, count + 1)]


def rotate(target, cap=None, count=None):
    """Rotate `target` when it has reached `cap` bytes. Returns whether it rotated. Never raises.

    Only the process that takes the rotation lock rotates, and it checks the size again under the
    lock, so two appends that both see a full file shift the rotated files once, not twice. A
    process that cannot take the lock appends to the full file and leaves rotating to the next.
    """
    try:
        cap = max_bytes() if cap is None else cap
        count = keep() if count is None else count
        target = Path(target)
        if not cap or os.path.getsize(str(target)) < cap:
            return False
    except OSError:
        return False
    try:
        import fcntl
    except ImportError:  # pragma: no cover - a platform with no advisory locking
        return False
    lock = None
    try:
        lock = os.open(str(target) + ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if os.path.getsize(str(target)) < cap:
            return False
        older = rotated(target, count)
        # A lowered `decision_log_keep` leaves files numbered past it, which no reader reaches.
        prefix = target.name + "."
        for sibling in target.parent.iterdir():
            suffix = sibling.name[len(prefix):]
            if (sibling.name.startswith(prefix) and suffix.isascii() and suffix.isdigit()
                    and int(suffix) > count):
                sibling.unlink()
        if not older:
            target.unlink()
            return True
        if older[-1].exists():
            older[-1].unlink()
        for newer, next_older in reversed(list(zip(older, older[1:]))):
            if newer.exists():
                os.replace(str(newer), str(next_older))
        os.replace(str(target), str(older[0]))
        return True
    except (OSError, ValueError):
        _ERRORS[0] += 1
        return False
    finally:
        if lock is not None:
            os.close(lock)


def secret_shapes():
    """The shapes the rule detectors match, compiled, or None when they cannot be read.

    Read out of the vendored `ruleprobe` wheel rather than imported from it: importing the
    engine inside a PreToolUse hook costs a fifth of a second, and a copy of the list in this
    file would be both a second source of truth and, to `harness lint`, a secret pattern
    written into a committed file. `rule-detectors.py` takes the same list from the same wheel,
    at the same pinned version. None stops the sampling, which is the safe direction: no row
    rather than an unredacted one.
    """
    if not _SHAPES:
        _SHAPES.append(_read_shapes())
    return _SHAPES[0]


def _read_shapes(root=None):
    """`SHAPES_NAME` out of `SHAPES_MODULE` in the pinned wheel, compiled, or None.

    None for every way this can fail — no checkout, no wheel, a wheel that is not a zip, a
    module that has been renamed, a list that is not literal — because each of them means the
    same thing to the caller: the text cannot be redacted, so it is not written.
    """
    import ast
    import zipfile

    root = checkout_root() if root is None else Path(root)
    if root is None:
        return None
    wheel = root / "lib" / "vendor" / ENGINE_WHEEL
    if not wheel.is_file():
        return None
    try:
        with zipfile.ZipFile(str(wheel)) as archive:
            source = archive.read(SHAPES_MODULE).decode("utf-8")
        for node in ast.parse(source).body:
            if not isinstance(node, ast.Assign):
                continue
            if any(getattr(target, "id", "") == SHAPES_NAME for target in node.targets):
                return [re.compile(pattern) for pattern in ast.literal_eval(node.value)]
    except Exception:
        return None
    return None


def _value(match, keep):
    """One match with its value replaced by REDACTED, keeping the first `keep` groups."""
    return "".join(match.group(index + 1) for index in range(keep)) + REDACTED


def redact(text, shapes, home_dir=None):
    """`text` with every value a secret can hide in replaced by REDACTED. See SHAPES_MODULE.

    Assignments and credential flags lose their values, quoted or not; every shape in `shapes`
    is replaced wherever it appears; and the home directory is written `~`, so a row carries no
    username even when a path names one.
    """
    text = ASSIGNMENT_RE.sub(lambda m: m.group(1) + m.group(2) + "=" + REDACTED, text)
    text = FLAG_RE.sub(lambda m: _value(m, 1), text)
    text = SHORT_FLAG_RE.sub(lambda m: _value(m, 2), text)
    for shape in shapes:
        text = shape.sub(REDACTED, text)
    root = str(home() if home_dir is None else home_dir).rstrip("/")
    return text.replace(root, "~") if root and root != "/" else text


def errors():
    """How many writes this process swallowed. A hook's decision never depends on it."""
    return _ERRORS[0]


def now_ts(now=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now))


def digest(text):
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()


def checkout_root():
    """The root of the checkout this file belongs to, or None when it is running outside one.

    The same walk `usage-log.py` does, and for the same reason: a hook is a script, not an
    import of the CLI.
    """
    here = Path(os.path.realpath(__file__)).parent
    for parent in [here] + list(here.parents):
        if (parent / "VERSION").is_file() and (parent / "bin" / "harness").exists():
            return parent
    return None


def harness_version():
    """The version in the `VERSION` file at the root of this checkout, or None outside one.

    A copy running outside a checkout stamps no version rather than a guess.
    """
    root = checkout_root()
    if root is None:
        return None
    try:
        return (root / "VERSION").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def match_key(event, text):
    """The identity a later event re-derives to find this decision again.

    The tool-use id when the runtime's payload carries one — neither adapter's does today, and
    both pass the payload through `lifecycle.normalize()` untouched, so if one starts carrying
    it both sides of the join gain it at once — and otherwise the session and the hash of the
    text the hook judged. Two identical commands in one session share a key, which joins the
    same outcome to both rather than to neither.
    """
    event = event if isinstance(event, dict) else {}
    for name in ("tool_use_id", "call_id"):
        value = event.get(name)
        if isinstance(value, str) and value:
            return value
    return str(event.get("session_id") or "") + ":" + digest(text)


def decision_id(point, key):
    """The reproducible id of a decision at `point` over `key`. See `match_key`."""
    return digest(point + "|" + key)[:32]


# How much of the tail an arriving event reads to find out whether its decision was logged. A
# decision made seconds ago is at the end of the file, and a bounded read is what keeps a
# PostToolUse hook's cost flat as the log grows.
TAIL_BYTES = 256 * 1024


def tail_text(target=None, limit=TAIL_BYTES):
    """The last `limit` bytes of the log as text, or "" when there is nothing to read."""
    target = Path(target) if target else path()
    try:
        with open(str(target), "rb") as stream:
            try:
                stream.seek(-limit, os.SEEK_END)
            except OSError:
                stream.seek(0)
            return stream.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _message_of(record):
    """The message object in one transcript record, or None for a record that holds none.

    Both runtimes in one shape: Claude Code holds the message under `message`, a Codex rollout
    wraps the same object in a `response_item`, and each carries its own `role`. A sidechain
    record is a subagent's turn and never part of the session's own.
    """
    if not isinstance(record, dict) or record.get("isSidechain"):
        return None
    if record.get("type") in ("assistant", "user"):
        message = record.get("message")
    elif record.get("type") == "response_item":
        message = record.get("payload")
    else:
        message = record
    return message if isinstance(message, dict) else None


def _message_text(message):
    """The prose of a message: every text block, joined, and nothing else.

    A block with no `text` is a tool call, a thought or an image, none of which is a claim.
    """
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = [b["text"] for b in content
             if isinstance(b, dict) and isinstance(b.get("text"), str) and b["text"]]
    return "\n".join(parts).strip()


def _starts_the_turn(message):
    """Whether this message is the user's own words, and so the far edge of the current turn.

    Claude Code writes a tool result as a `user` record, so a scan that stopped at every user
    record would stop in the middle of the turn it is reading; one that stopped at none would
    take a claim from the turn before when this turn ended in a tool call.
    """
    if message.get("role") != "user":
        return False
    content = message.get("content")
    if isinstance(content, list):
        return not any(isinstance(b, dict)
                       and b.get("type") in ("tool_result", "function_call_output")
                       for b in content)
    return True


def _claim_in(tail):
    """The claim in a tail of transcript, or None when this turn ended without one.

    Newest first, back to the user message that opened the turn. Lines are split on `\n` alone:
    a JSON string can carry U+2028, U+2029, U+0085 and the other characters `splitlines` breaks
    on, and splitting there would tear a record in half and silently read an older turn. The
    first line of the window is usually a fragment, which fails to parse and is discarded.
    """
    for line in reversed(tail.split("\n")):
        if '"assistant"' not in line and '"user"' not in line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        message = _message_of(record)
        if message is None:
            continue
        if _starts_the_turn(message):
            return None
        if message.get("role") != "assistant":
            continue
        text = _message_text(message)
        if text:
            return text
    return None


def read_claim(transcript):
    """`(claim, miss)`: the turn's claim and why there is none, exactly one of them set.

    The miss is a reason a reader of the log can act on. `no_transcript_path` is a runtime that
    named no file — a gap in what the event carries, not in the session — where `unreadable`,
    `oversized` and `no_claim` are all about a file that was named. The read is bounded on both
    sides, see MAX_TRANSCRIPT and CLAIM_TAIL_BYTES, so it costs the same at any session length.
    """
    if not transcript:
        return None, "no_transcript_path"
    target = os.path.expanduser(str(transcript))
    try:
        if os.path.getsize(target) > MAX_TRANSCRIPT:
            return None, "oversized"
        with open(target, "rb") as stream:
            try:
                stream.seek(-CLAIM_TAIL_BYTES, os.SEEK_END)
            except OSError:
                stream.seek(0)
            tail = stream.read().decode("utf-8", "replace")
    except OSError:
        return None, "unreadable"
    text = _claim_in(tail)
    return (text, None) if text else (None, "no_claim")


def _capped(text):
    """The last MAX_CLAIM **bytes** of the claim, cut back to a character boundary.

    Bytes rather than characters because the cap is there to bound the file on disk, and one
    emoji is four of them.
    """
    raw = text.encode("utf-8")
    return text if len(raw) <= MAX_CLAIM else raw[-MAX_CLAIM:].decode("utf-8", "ignore")


def claim_fields(transcript, cfg=None):
    """The completion-claim fields for a decision row, or `{}` when there are none to add.

    `{}` whenever the switch is off, so the row is byte for byte the row written before this
    existed. With it on the row always says something: the claim and its hash, or a null claim
    beside the reason there is none. Missing evidence is recorded, never a reason to lose the
    decision it was evidence for, so nothing here raises.
    """
    if not claim_enabled(cfg):
        return {}
    try:
        text, miss = read_claim(transcript)
    except Exception:
        text, miss = None, "error"
    if text is None:
        return {"completion_claim": None, "completion_claim_miss": miss}
    return {"completion_claim": _capped(text), "completion_claim_sha256": digest(text)}


# The log grows compatibly, under the rule `usage-log.py` states for the usage ledger: a change
# adds a field, a rename ships a fold (`old name: new name`), nothing is removed in place and no
# old row is rewritten. A row without SCHEMA_KEY predates the version and reads as version 0.
SCHEMA_KEY = "schema_version"
# Version 1 is first released in v0.14.0 and carries `profile_fingerprint`; see `usage-log.py`.
SCHEMA_VERSION = 1
FIELD_FOLDS = {}
FINGERPRINT_KEY = "profile_fingerprint"
_POSTURE = []


def profile_fingerprint():
    """The fingerprint of the profile in force, from the `posture.py` beside this file, or None.

    Loaded by path, as `lifecycle.py` loads this file, and remembered for the process. A copy
    running away from its resolver, or a resolver that fails, stamps null rather than a guess.
    """
    if not _POSTURE:
        location = Path(os.path.realpath(__file__)).parent / "posture.py"
        try:
            spec = importlib.util.spec_from_file_location("harness_decisions_posture", str(location))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:
            module = None
        _POSTURE.append(module)
    try:
        return _POSTURE[0].fingerprint() if _POSTURE[0] else None
    except Exception:
        return None


def module_of(point):
    """The `hooks/<id>` that owns decisions at `point`, or None for a point no hook owns."""
    return POINT_MODULES.get(point) if isinstance(point, str) else None


def fold(row, folds=None):
    """A copy of `row` with every renamed field under its current name; see usage-log's `fold`."""
    folds = FIELD_FOLDS if folds is None else folds
    out = dict(row)
    for old, new in folds.items():
        if old in out:
            value = out.pop(old)
            out.setdefault(new, value)
    return out


def _append(row, target=None):
    """One line, one `write`. Appending is the only way this file is ever changed."""
    row = dict(row, **{SCHEMA_KEY: SCHEMA_VERSION})
    if FINGERPRINT_KEY not in row:
        row[FINGERPRINT_KEY] = profile_fingerprint()
    if MODULE_KEY not in row:
        row[MODULE_KEY] = module_of(row.get("point"))
    target = Path(target) if target else path()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(str(target.parent), 0o700)
    except OSError:
        pass
    rotate(target)
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (json.dumps(row, sort_keys=True) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    return target


def _decision_row(point, answer, text, event, runtime, key, now, written=None):
    """The decision row itself, with `written` standing in for `text` when it is redacted."""
    return {"kind": "decision",
            "decision_id": uuid.uuid4().hex if key is None else decision_id(point, key),
            "point": point,
            "session_id": str((event or {}).get("session_id") or "") if event else "",
            "ts": now_ts(now), "input_sha256": digest(text),
            "input": (text if written is None else written)[:MAX_INPUT],
            "deterministic_answer": answer, "outcome": None,
            "runtime": runtime or os.environ.get("HARNESS_RUNTIME", ""),
            "harness_version": harness_version()}


def record(point, answer, text="", event=None, runtime="", key=None, target=None, now=None,
           transcript=None, fields=None):
    """Log one judgment. Returns its `decision_id`, or None when nothing was written.

    Never raises. A failed write is counted and the caller carries on with the decision it had
    already made: a log that can change a permission answer is worse than no log.

    `key` makes the id reproducible, so an event that arrives later can name this decision
    without having read the file; with none, the id is a fresh one nobody will join to.

    `transcript` is the file a completion claim is read from, and adds nothing to the row unless
    the caller passes one and the switch is on: see `claim_fields`. `fields` adds facts about
    how the answer was reached, such as an approval offered or consumed or a grader that ran
    past its deadline; a claim that cannot be read costs the claim, never the row.
    """
    try:
        if not enabled():
            return None
        text = text if isinstance(text, str) else ""
        row = _decision_row(point, answer, text, event, runtime, key, now)
        try:
            row.update(claim_fields(transcript))
        except Exception:
            _ERRORS[0] += 1
        for name, value in (fields or {}).items():
            row.setdefault(name, value)
        identity = row["decision_id"]
        _append(row, target)
        return identity
    except Exception:
        _ERRORS[0] += 1
        return None


def record_allowed(command, event=None, runtime="", target=None, now=None, cfg=None):
    """Log one allowed command, if it is in the sample. Returns its id, or None. Never raises.

    The negatives for shadow evaluation: `deterministic_answer: allow`, `sampled: true` and the
    rate it was drawn at. Only a command the harness itself allowed reaches here — a command it
    said nothing about is the runtime's to answer and may yet be prompted on or refused, so it
    is no evidence of an allow. The row carries no outcome and no match key, because there is
    no judgment here to label and an allow that later "ran" grades nothing; nothing joins to it,
    `close_session` passes it by and `usage --by decision` counts it apart from the graded rows.
    The text is redacted before it is capped, unlike the text of a prompt the user was shown.
    See DEFAULT_SAMPLE_RATE.
    """
    try:
        if not isinstance(command, str) or not command.strip() or not enabled(cfg):
            return None
        rate = sample_rate(cfg)
        if not in_sample(command, rate):
            return None
        shapes = secret_shapes()
        if shapes is None:
            return None
        written = redact(command, shapes)
        row = _decision_row("grade-bash", "allow", command, event, runtime, None, now,
                            written=written)
        # The hash of a sampled row is over the redacted text, not the original: see REDACTED.
        row.update({"input_sha256": digest(written), "sampled": True, "sample_rate": rate})
        _append(row, target)
        return row["decision_id"]
    except Exception:
        _ERRORS[0] += 1
        return None


def observe(decision_id, outcome, point="", session_id="", target=None, now=None):
    """Log the outcome of an earlier decision. Never raises; returns whether a line was written.

    The decision row keeps its `null`. An outcome is its own record, and a reader joins them.
    """
    try:
        if not decision_id or not enabled():
            return False
        _append({"kind": "outcome", "decision_id": decision_id, "point": point,
                 "session_id": str(session_id or ""), "ts": now_ts(now), "outcome": outcome,
                 "harness_version": harness_version()}, target)
        return True
    except Exception:
        _ERRORS[0] += 1
        return False


def observe_if_logged(identity, outcome, point="", session_id="", target=None, now=None):
    """Log an outcome only for a decision this log actually holds. Never raises.

    Most events that could carry an outcome follow no decision at all — the harness answers the
    permission question on a small minority of Bash calls — and an outcome with nothing to join
    to would be both a wrong count and a file that grows with every tool call. The tail read is
    bounded; a decision older than the tail goes unlabelled, which the report shows as such.
    """
    try:
        if not identity or not enabled():
            return False
        if identity not in tail_text(target):
            return False
    except Exception:
        _ERRORS[0] += 1
        return False
    return observe(identity, outcome, point, session_id, target, now)


# A count summary instead of a row per answer, for a point that answers on most tool calls: the
# read-only Bash allow answers tens of thousands of times a month, and a row each would be most of
# the file. Each answer appends one short line, its kind, to a per-session tally file, which is
# one `write` and needs no lock; SessionEnd turns the file into one decision row whose `counts`
# holds how many of each kind the session gave, and removes it. A tally whose session never
# ended is flushed by the next flush that finds it a day old, marked `stale`.
TALLY_DIR = "tallies"
TALLY_STALE = 86400
SESSION_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _tally_file(point, session_id, base=None):
    """The tally file for one session at one point, or None for an id unfit for a file name."""
    if not isinstance(session_id, str) or not SESSION_NAME.match(session_id) or session_id[0] == ".":
        return None
    return (Path(base) if base else state_dir()) / TALLY_DIR / point / (session_id + ".tally")


def tally(point, session_id, kind, base=None):
    """Count one answer of `kind` for the session. Never raises; returns whether it counted."""
    try:
        if not enabled() or not isinstance(kind, str) or not re.match(r"^[a-z0-9-]{1,40}$", kind):
            return False
        target = _tally_file(point, session_id, base)
        if target is None:
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (kind + "\n").encode("ascii"))
        finally:
            os.close(fd)
        return True
    except Exception:
        _ERRORS[0] += 1
        return False


def _flush_one(point, source, session_id, runtime, target, now, stale):
    # Taken out of the way first, so an answer counted while this reads starts a new tally.
    taken = source.with_name(source.name + "." + uuid.uuid4().hex[:8])
    os.replace(str(source), str(taken))
    counts = {}
    try:
        for line in taken.read_text(encoding="ascii", errors="replace").splitlines():
            if line:
                counts[line] = counts.get(line, 0) + 1
    finally:
        taken.unlink()
    if not counts:
        return None
    fields = {"counts": counts, "total": sum(counts.values())}
    if stale:
        fields["stale"] = True
    return record(point, "summary", "", {"session_id": session_id}, runtime, target=target,
                  now=now, fields=fields)


def flush_tally(point, session_id, runtime="", base=None, target=None, now=None):
    """Write the session's count summary for `point` and any day-old orphan. Never raises.

    Returns the summary's `decision_id`, or None when the session counted nothing.
    """
    identity = None
    try:
        if not enabled():
            return None
        source = _tally_file(point, session_id, base)
        if source is not None and source.is_file():
            identity = _flush_one(point, source, session_id, runtime, target, now, False)
        folder = (Path(base) if base else state_dir()) / TALLY_DIR / point
        moment = time.time() if now is None else now
        for orphan in (sorted(folder.glob("*.tally")) if folder.is_dir() else []):
            try:
                if moment - orphan.stat().st_mtime >= TALLY_STALE:
                    _flush_one(point, orphan, orphan.name[:-len(".tally")], runtime, target, now, True)
            except OSError:
                continue
    except Exception:
        _ERRORS[0] += 1
    return identity


def read_rows(target=None, folds=None, history=False):
    """Every well-formed record in the log, folded, oldest first. An unreadable file is no rows.

    A field or a schema version this reader does not know is carried, never refused. With
    `history`, the rotated files are read first, oldest to newest, so a report sees every row
    retention kept; without it only the current file is read, which is what a hook on a time
    budget wants.
    """
    target = Path(target) if target else path()
    rows = []
    files = (list(reversed(rotated(target, keep()))) if history else []) + [target]
    text = ""
    for source in files:
        try:
            text += source.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not text.endswith("\n"):
            text += "\n"
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            row = fold(row, folds)
            if row.get("decision_id"):
                rows.append(row)
    return rows


def joined(rows):
    """Decision rows with their outcome filled in, oldest first.

    The first outcome recorded for a `decision_id` is the one that holds: a second record for
    the same decision is a duplicate event, not a change of mind, and an append-only file has
    no way to say the earlier one was wrong.
    """
    outcomes = {}
    for row in rows:
        if row.get("kind") == "outcome" and row["decision_id"] not in outcomes:
            outcomes[row["decision_id"]] = row.get("outcome")
    out = []
    for row in rows:
        if row.get("kind") == "outcome":
            continue
        result = row.get("outcome")
        if result is None:
            result = outcomes.get(row["decision_id"])
        out.append(dict(row, outcome=result))
    return out


def close_session(session_id, points=("grade-bash",), outcome=NOT_RUN, target=None, now=None):
    """Label this session's unanswered decisions at SessionEnd. Returns how many were labelled.

    A Bash ask whose PostToolUse never arrived is the session's answer to it, and the session
    is over: nothing else will ever arrive. Only the points whose outcome is observed this way
    are closed, so a decision that is simply not labelled yet stays unlabelled and shows up in
    the report's unlabelled share rather than as a fabricated result. A sampled allow is passed
    by: nobody was asked about it, so "not run" would be a label about a prompt that never was.
    """
    try:
        if not session_id or not enabled():
            return 0
        rows = read_rows(target)
        answered = set(r["decision_id"] for r in rows if r.get("kind") == "outcome")
        closed = 0
        for row in rows:
            if row.get("kind") == "outcome" or row.get("session_id") != session_id:
                continue
            if row.get("point") not in points or row["decision_id"] in answered:
                continue
            if row.get("sampled"):
                continue
            answered.add(row["decision_id"])
            if observe(row["decision_id"], outcome, row.get("point") or "", session_id,
                       target, now):
                closed += 1
        return closed
    except Exception:
        _ERRORS[0] += 1
        return 0

# SPDX-License-Identifier: MIT
"""A person's presence at this Mac, proved out of band, before a Studio spend or apply.

A file, a flag, an environment variable or a terminal answer is something any program running as
the user can produce, so none of them shows that a person agreed. macOS LocalAuthentication can:
`confirm` asks the system for Touch ID or the login password (policy
`LAPolicyDeviceOwnerAuthentication`), in a dialog no agent can answer, and passes only when the
system says the owner authenticated. A cancelled, failed, timed-out or unavailable check refuses.

`confirm` is the only function the CLI and the Studio ask, and it reads nothing an agent can set
to make it say yes. It asks through the Model Citizen helper app (`presence_helper`), built and
ad-hoc signed on this Mac and verified against its recorded signature hash before each use, so the
dialog names Model Citizen; where that helper cannot be built, through `/usr/bin/osascript` by
absolute path. Either runs with a fixed environment, and only its `present` answer passes. The
helper is told the action as a verb phrase, since macOS words its dialog as that app "trying to"
do it; `osascript`, which macOS names, is told the whole sentence. Inputs can only make it
refuse: a host other than macOS, a session with no GUI (`launchctl managername` other than
`Aqua`), a Codex sandbox, or `OFF` set, which the test suite sets so no real dialog is ever raised
under it. Where presence cannot be checked the CLI refuses
and points to the Studio, whose own run would ask the same way.

This raises the bar from "anything the agent writes" to "a person at the machine"; it does not
stop an agent that runs its own Python against the run library with this module replaced. The
boundary for that is the runtime's sandbox, as `docs/runtime-controls.md` says of the grader.
"""
from __future__ import annotations

import json
import os
import platform
import shlex
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, NamedTuple, Optional, Sequence

from harness_core import presence_helper

REFUSED = "person_confirmation_required"
# Interpreter variables that load code into a Python process before it runs: a CLI or Studio
# started with one set could be running a replaced `confirm`, so it refuses instead.
CONSENT_LEDGER = "confirmations.jsonl"
LOADER_VARIABLES = ("PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME", "PYTHONINSPECT")
OFF = "MODEL_CITIZEN_PRESENCE_OFF"
OSASCRIPT = "/usr/bin/osascript"
LAUNCHCTL = "/bin/launchctl"
TIMEOUT = 180
REASON_LIMIT = 480
# The requester the reason line names; macOS names the calling program in the dialog's title.
CLI_NAME = "Model Citizen"
STUDIO_NAME = "Model Citizen Studio"
# Set only by Codex's sandboxed shell; tighten-only, since it can only make the check refuse.
CODEX_VARIABLES = ("CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED")
PRESENT = "present"
SCRIPT = r"""
ObjC.import('LocalAuthentication');
ObjC.import('Foundation');
function run(argv) {
  var context = $.LAContext.alloc.init;
  var finished = false, granted = false;
  if (!context.canEvaluatePolicyError(2, Ref())) { return 'unavailable'; }
  context.evaluatePolicyLocalizedReasonReply(2, argv[0], function (success, error) {
    granted = (success === true); finished = true; });
  var until = $.NSDate.dateWithTimeIntervalSinceNow(%d);
  while (!finished && $.NSDate.date.compare(until) < 0) {
    $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(0.1));
  }
  return finished && granted ? 'present' : 'declined';
}
""" % (TIMEOUT - 10)
STUDIO_HINT = ("Start it from the Studio's own dialog on this Mac's desktop session, which asks "
               "the same way.")


def loader_variables() -> list:
    """The `LOADER_VARIABLES` set in this process's environment."""
    return [name for name in LOADER_VARIABLES if os.environ.get(name)]


def unavailable() -> Optional[str]:
    """Why presence cannot be checked here, or None. Never a reason to pass."""
    loaded = loader_variables()
    if loaded:
        return "%s %s set, which can load other code into this process; unset %s and run it again" % (
            " and ".join(loaded), "is" if len(loaded) == 1 else "are",
            "it" if len(loaded) == 1 else "them")
    if os.environ.get(OFF):
        return "presence checks are switched off in this process"
    if platform.system() != "Darwin":
        return "presence is checked only on macOS"
    if any(os.environ.get(name) for name in CODEX_VARIABLES):
        return "a Codex sandbox cannot show the presence dialog"
    if not os.access(OSASCRIPT, os.X_OK):
        return "this Mac has no " + OSASCRIPT
    try:
        manager = subprocess.run([LAUNCHCTL, "managername"], capture_output=True, text=True,
                                 timeout=10, env={"PATH": "/usr/bin:/bin"})
    except (OSError, subprocess.SubprocessError):
        return "the login session could not be read"
    if manager.stdout.strip() != "Aqua":
        return "no desktop session is attached to this shell"
    return None


class Reason(NamedTuple):
    """What the dialog asks for: who asks, the action as a verb phrase, and why."""
    requester: str
    action: str
    purpose: str


class Answer(NamedTuple):
    """What the check answered and which program asked: `helper`, `osascript`, or `none` where
    presence cannot be checked; `cdhash` is the helper's code-signing hash when it asked."""
    present: bool
    program: str
    cdhash: str


# The last answer on this thread, for `record`: the confirmation row names who answered.
_LAST = threading.local()


def _clip(head: str, action: str, tail: str) -> str:
    """`head + action + tail` within `REASON_LIMIT`, shortening only the action."""
    room = REASON_LIMIT - len(head) - len(tail)
    if len(action) > room:
        action = action[:max(room - 3, 0)].rstrip() + "..."
    return (head + action + tail)[:REASON_LIMIT]


def phrase(action: str, purpose: str) -> str:
    """The helper's reason, "<action>, so <purpose>": a verb phrase, since macOS titles the
    dialog with the app's name and words it as that app trying to do this."""
    return _clip("", action, ", so " + purpose)


def sentence(requester: str, action: str, purpose: str) -> str:
    """The whole sentence, "<requester> wants to <action>, so <purpose>.": the `osascript`
    fallback's reason, where macOS names osascript, and the words Activity records."""
    return _clip(requester + " wants to ", action, ", so " + purpose + ".")


def _ask(argv: Sequence[str], program: str, cdhash: str = "") -> Answer:
    try:
        done = subprocess.run(list(argv), capture_output=True, text=True, timeout=TIMEOUT,
                              env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"},
                              stdin=subprocess.DEVNULL, cwd="/")
    except (OSError, subprocess.SubprocessError):
        return Answer(False, program, cdhash)
    return Answer(done.returncode == 0 and done.stdout.strip() == PRESENT, program, cdhash)


def check(requester: str, action: str, purpose: str) -> Answer:
    """Whether the Mac's owner authenticated just now for this action, and which program asked.

    The Model Citizen helper asks when it is built and verified, holding it under a shared lock
    so no build replaces it meanwhile (`presence_helper.ready`); otherwise `osascript`. A decline
    from either is a refusal: nothing is asked a second way."""
    answer = Answer(False, "none", "")
    if unavailable() is None:
        with presence_helper.ready() as found:
            if found is not None:
                answer = _ask([str(found.path), phrase(action, purpose)], "helper", found.cdhash)
        if answer.program == "none":
            answer = _ask([OSASCRIPT, "-l", "JavaScript", "-e", SCRIPT,
                           sentence(requester, action, purpose)], "osascript")
    _LAST.answer = answer
    return answer


def confirm(requester: str, action: str, purpose: str) -> bool:
    """Whether the Mac's owner authenticated, just now, for this action. The one yes; see the
    module."""
    return check(requester, action, purpose).present


def requester(via_studio: bool) -> str:
    """Who the dialog says is asking: the Studio, or the CLI."""
    return STUDIO_NAME if via_studio else CLI_NAME


def ask(who: str, action: str, purpose: str) -> Reason:
    """The `Reason` a caller passes to `confirm` as `confirm(*reason)`."""
    return Reason(who, action, purpose)


def spend_reason(checked: Dict[str, Any], targets: Optional[Sequence[Dict[str, Any]]] = None,
                 who: str = CLI_NAME, purpose: str = "") -> Reason:
    """The dialog's words for a paid start, from `RunSupervisor.check_start`: the suite, its
    targets, the estimate and the caps, so a yes covers only the spend it names."""
    named = targets or [{"kind": checked.get("target_kind"), "ref": checked.get("target_ref")}]
    where = " vs ".join("%s %s" % (item.get("kind"), item.get("ref")) for item in named)
    estimate = checked.get("estimate_usd")
    action = ("spend up to $%s on a paid %s run of %s case(s) against %s (estimated %s; spend "
              "cap $%s, %s pricing)" % (
                  checked.get("max_budget_usd"), checked.get("suite_id"), checked.get("case_count"),
                  where, "unknown, no history" if estimate is None else "$%s" % estimate,
                  checked.get("spend_cap_usd"), checked.get("pricing_source")))
    return ask(who, action, purpose or "it can run the %s suite you previewed" % checked.get(
        "suite_id"))


def refusal(reason: Reason) -> str:
    """The sentence a refused spend or apply prints, from its action: "<requester> did not
    <action>: it needs a person's confirmation ..."."""
    why = unavailable() or "the presence check was declined, cancelled or failed"
    return ("%s did not %s: it needs a person's confirmation at this Mac (Touch ID or the login "
            "password), and none was given: %s. A confirmation token, a revision, a flag, an "
            "environment variable, a file or a typed answer does not confirm it. %s" % (
                reason.requester, reason.action, why, STUDIO_HINT))


def state_root() -> Path:
    home = os.environ.get("HARNESS_HOME") or os.environ.get("HOME") or str(Path.home())
    return Path(home) / ".local" / "state" / "agent-harness"


def _decisions_on() -> bool:
    """`telemetry.decisions`, read as the decision log reads it: on unless set to false."""
    try:
        config = json.loads((Path(os.environ.get("HARNESS_HOME") or os.environ.get("HOME")
                                  or str(Path.home())) / ".config" / "agent-harness"
                             / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    telemetry = config.get("telemetry") if isinstance(config, dict) else None
    return not (isinstance(telemetry, dict) and telemetry.get("decisions") is False)


def record(actor: str, words: Sequence[str], reason: Reason) -> None:
    """Append one `studio.person-confirmed` event, which Activity shows.

    The row names the program that answered on this thread (`check`), and for the helper its
    code-signing hash, in its detail and in the words Activity shows: a helper that was replaced
    outside git shows up here as a hash that changed without a source change.

    It goes to the decision ledger, or, with `telemetry.decisions` off and so no ledger written, to
    `CONSENT_LEDGER` beside it: the record of a person's consent is kept whatever telemetry says.
    Activity pages both as one stream. Anything running as the user can write either file, so a
    row is a record, not proof. A failed write is not raised."""
    answer = getattr(_LAST, "answer", None) or Answer(True, "unknown", "")
    _LAST.answer = None
    asked_by = {"helper": "the Model Citizen helper's dialog (signature %s)" % answer.cdhash,
                "osascript": "osascript's dialog"}.get(answer.program, "a dialog not recorded")
    row = {"kind": "event", "event": "studio.person-confirmed", "id": uuid.uuid4().hex[:24],
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "detail": {"actor": actor, "outcome": "completed", "confirmed_via": "presence",
                      "program": answer.program, "helper_cdhash": answer.cdhash,
                      "command": " ".join(shlex.quote(str(w)) for w in words),
                      "reason": "A person at this Mac confirmed with Touch ID or the login "
                                "password, in %s: %s" % (asked_by, sentence(*reason))}}
    try:
        path = state_root() / ("decisions.jsonl" if _decisions_on() else CONSENT_LEDGER)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (json.dumps(row, sort_keys=True) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except OSError:
        pass

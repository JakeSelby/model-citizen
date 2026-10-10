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
absolute path. Either runs with a fixed environment, and only its `present` answer passes. Inputs can only make it refuse: a host other than macOS, a session with no GUI
(`launchctl managername` other than `Aqua`), a Codex sandbox, or `OFF` set, which the test suite
sets so no real dialog is ever raised under it. Where presence cannot be checked the CLI refuses
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
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

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


def helper() -> Optional[Path]:
    """The verified Model Citizen helper, built first if need be, or None to ask through
    `osascript`. Any failure preparing it is a fallback, never an error."""
    try:
        return presence_helper.executable()
    except Exception:  # noqa: BLE001 - the osascript path still asks the person
        return None


def check(reason: str) -> Tuple[bool, str]:
    """(whether the Mac's owner authenticated just now for `reason`, which program asked:
    `helper`, `osascript`, or `none` where presence cannot be checked). A decline from the helper
    is a refusal; nothing is asked a second way."""
    if unavailable() is not None:
        return False, "none"
    text = str(reason)[:REASON_LIMIT]
    found = helper()
    if found is not None:
        argv, path = [str(found), text], "helper"
    else:
        argv, path = [OSASCRIPT, "-l", "JavaScript", "-e", SCRIPT, text], "osascript"
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT,
                              env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"},
                              stdin=subprocess.DEVNULL, cwd="/")
    except (OSError, subprocess.SubprocessError):
        return False, path
    return done.returncode == 0 and done.stdout.strip() == PRESENT, path


def confirm(reason: str) -> bool:
    """Whether the Mac's owner authenticated, just now, for `reason`. The one yes; see the module."""
    return check(reason)[0]


def requester(via_studio: bool) -> str:
    """Who the dialog says is asking: the Studio, or the CLI."""
    return STUDIO_NAME if via_studio else CLI_NAME


def ask(who: str, action: str, purpose: str) -> str:
    """The dialog's reason line, "<who> wants to <action>, so <purpose>.", within `REASON_LIMIT`.

    Only the action is shortened when it must be, so the requester and the purpose always show."""
    head, tail = who + " wants to ", ", so " + purpose + "."
    room = REASON_LIMIT - len(head) - len(tail)
    if len(action) > room:
        action = action[:max(room - 3, 0)].rstrip() + "..."
    return (head + action + tail)[:REASON_LIMIT]


def spend_reason(checked: Dict[str, Any], targets: Optional[Sequence[Dict[str, Any]]] = None,
                 who: str = CLI_NAME, purpose: str = "") -> str:
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


def refusal(what: str) -> str:
    """The sentence a refused spend or apply prints."""
    why = unavailable() or "the presence check was declined, cancelled or failed"
    return ("%s needs a person's confirmation at this Mac (Touch ID or the login password), and "
            "none was given: %s. A confirmation token, a revision, a flag, an environment "
            "variable, a file or a typed answer does not confirm it. %s" % (what, why, STUDIO_HINT))


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


def record(actor: str, words: Sequence[str], what: str) -> None:
    """Append one `studio.person-confirmed` event, which Activity shows.

    It goes to the decision ledger, or, with `telemetry.decisions` off and so no ledger written, to
    `CONSENT_LEDGER` beside it: the record of a person's consent is kept whatever telemetry says.
    Activity pages both as one stream. Anything running as the user can write either file, so a
    row is a record, not proof. A failed write is not raised."""
    row = {"kind": "event", "event": "studio.person-confirmed", "id": uuid.uuid4().hex[:24],
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "detail": {"actor": actor, "outcome": "completed", "confirmed_via": "presence",
                      "command": " ".join(shlex.quote(str(w)) for w in words),
                      "reason": "A person at this Mac confirmed with Touch ID or the login "
                                "password: %s" % what}}
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

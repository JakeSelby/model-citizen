"""Registering the observation entry point, and the bare-arm install that carries only it (AD-23).

The recorder itself is `harness_core.observer`. Its registration reads which events each runtime
raises from `lifecycle.EVENTS`, the one declaration `hook.py` is registered from too, so the two
entry points cannot disagree on events. Nothing here routes through the dispatcher.
"""
import json
import shlex
import shutil
from copy import deepcopy
from pathlib import Path

from harness_core import lifecycle, observer

MARKER = "# harness:observe-"
TIMEOUT = 5
# The runtime file each bare install writes its registration into.
SETTINGS_FILE = {"claude-code": "settings.json", "codex": "hooks.json"}
ENTRY = "observe.py"


def enabled(config):
    """Observation is an explicit boolean opt-in, never a truthy string."""
    block = config.get("observation", {})
    if not isinstance(block, dict) or type(block.get("enabled", False)) is not bool:
        raise ValueError("observation.enabled must be true or false")
    return block.get("enabled", False)


def with_observation(base, root, runtime, config):
    """Add the separate recorder only for an opted-in install."""
    result = deepcopy(base)
    if enabled(config):
        for event, entries in registration(root, runtime)["hooks"].items():
            result.setdefault("hooks", {}).setdefault(event, []).extend(entries)
    return result


def events(runtime):
    return lifecycle.EVENTS.get(runtime, lifecycle.BASE_EVENTS)


def hooks_for(command, runtime):
    return {event: [{"hooks": [{"type": "command", "command": command + " " + MARKER + event.lower(),
                                "timeout": TIMEOUT}]}]
            for event in events(runtime)}


def registration(root, runtime):
    """The hooks block that registers `adapters/<runtime>/observe.py` for every event it raises."""
    if runtime not in SETTINGS_FILE:
        raise ValueError("unknown runtime")
    script = Path(root) / "adapters" / runtime / "observe.py"
    return {"hooks": hooks_for("python3 " + shlex.quote(str(script)), runtime)}


def bare_install(dest, runtime="claude-code"):
    """Write the bare arm's whole harness footprint into `dest` and return the settings path.

    The footprint is the recorder, copied alone as `observe.py`, and the runtime's hook file
    registering it with `--profile bare`. Nothing else from the harness is present: no dispatcher,
    no policy, no rule, stance or skill.
    """
    if runtime not in SETTINGS_FILE:
        raise ValueError("unknown runtime")
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    entry = dest / ENTRY
    shutil.copyfile(observer.__file__, str(entry))
    command = " ".join(["python3", shlex.quote(str(entry)), "--runtime", runtime, "--profile", "bare"])
    settings = dest / SETTINGS_FILE[runtime]
    settings.write_text(json.dumps({"hooks": hooks_for(command, runtime)}, indent=2, sort_keys=True) + "\n")
    return settings


def install_files(dest):
    """Every file under `dest`, relative to it, sorted."""
    dest = Path(dest)
    return sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file())

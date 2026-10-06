#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""PreToolUse hook: apply the delegation stance to every subagent spawn, whoever wrote the brief.

The stance's tiers travel as frontmatter on the agents the harness ships, so they reach any
spawn that names one. A spawn that names nothing — no `subagent_type`, or `general-purpose`,
and no `model` — is what a planning framework or a plugin produces when its skill text says
"launch a subagent", and nothing else in the harness reaches it. This hook does. A call that
names an agent definition or passes `model` is left as it was, with one exception: the
strongest class is reached through a role that declares it, never by request. A spawn that
asks for it by `model` gets the model its agent definition names instead, or the class below
when there is no definition to read — so neither an orchestrator nor a framework's skill text
("run reviewers at the session's capability") can put ad-hoc work on the scarcest tier. The
request is rewritten, never removed: a rewrite survives composition with other hooks.

What a bare spawn gets depends on the `delegation` stance, which `posture.py` resolves for
every hook alike:

    tiered         route it to the cost variant's default band worker, on that band's class;
                   with no default band, rewrite `model` to one tier below the session model,
                   the weakest class on the ladder being the floor; refuse the top tier by request
    session-model  leave it alone
    off            deny every spawn, named or not, as the stance says; the lifecycle denies
                   first and never invokes this hook, so this branch decides only a direct run

The band workers exist because the `Agent` tool has no effort input: a spawn that names nothing
inherits the session's effort, and only an agent definition can carry the posture's. So a spawn
with no `subagent_type`, or `general-purpose`, is rewritten to `worker-a`, `worker-b` or
`worker-c` — the variant's `default_band` — and the orchestrator that wanted a different band
spawns that worker by name. A machine whose worker definitions are not installed is not routed
at all: a `subagent_type` the tool cannot resolve would fail the spawn. Neither is a session
that started before they were installed and has not been told about them since — so the reroute
asks the session registry `posture.sessions_dir` describes and the reload the transcript
announces, not the disk, and a reroute never turns a spawn that would have worked into one
that fails.

The ladder is the adapter's `bindings.json` class table, strongest class first, matched as
substrings of the model ids a transcript records; no model name is written here.

A repository that carries a planning framework is tiered like any other. The framework keeps
its personas, prompts and review structure; model and effort are the harness's to choose, and
the framework's override templates name the harness's roles where the recipe allows, which is
what carries tools and effort.

The session model is read from the newest main-line assistant record in the transcript, which
Claude Code writes once a response has started executing tools, so a spawn in a session's very
first response is left alone: nothing else says what the session runs on (the `model` key in
settings is a default the session may not be using). A session model the ladder does not know
is left untouched rather than guessed, and the hook says so, because a new model name would
otherwise switch tiering off without a sound. Never fails: every error falls through and the
call runs as written.

Test: printf '%s' '{"tool_name":"Agent","tool_input":{"prompt":"x"}}' | HARNESS_STANCE_DELEGATION=off python3 tier-agent-spawns.py
"""
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
DEFAULT_STANCE = "tiered"
TAIL_BYTES = 1 << 20
HOOK = "tier-agent-spawns hook"
AGENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
# The two notices a session hears once rather than on every spawn: where an unnamed spawn goes,
# and that the worker is on disk but this session's registry predates it. Both describe the
# standing arrangement, so repeating them on each spawn is noise. A notice about something the
# caller asked for being changed or refused stays per-occurrence.
ROUTED_NOTICE = "routed-to-band"
UNRESOLVABLE_NOTICE = "worker-unresolvable"
_LOADED = {}


def sibling(name):
    """A module beside this hook, or None. A hook must never stop a spawn because an import failed."""
    try:
        spec = importlib.util.spec_from_file_location(
            "harness_" + name.replace("-", "_"), str(HOOKS / (name + ".py")))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def posture_module():
    """The shared sibling, loaded at most once a run: every question here asks the same copy."""
    if "posture" not in _LOADED:
        _LOADED["posture"] = sibling("posture")
    return _LOADED["posture"]


def log_spawn(payload, tool_input, answer, reroute, updated=None):
    """Record one spawn this hook changed or refused. See `decisions.py`; never raises.

    `answer` is the band a routed spawn went to, as it always was, or the kind of change:
    `demoted` for a top-class request moved down, `one-rung` for a bare spawn moved below the
    session, `deny` for delegation off. `reroute` names which rule fired; the row also carries the
    model and role asked for and the ones the spawn runs with, so every reroute is countable.
    """
    try:
        if "decisions" not in _LOADED:
            _LOADED["decisions"] = sibling("decisions")
        module = _LOADED["decisions"]
        if module is None:
            return
        prompt = tool_input.get("prompt")
        after = updated if isinstance(updated, dict) else tool_input
        fields = {"reroute": reroute,
                  "model_requested": tool_input.get("model"),
                  "model": after.get("model"),
                  "subagent_type_requested": tool_input.get("subagent_type"),
                  "subagent_type": after.get("subagent_type")}
        module.record("tier-agent-spawns", answer, prompt if isinstance(prompt, str) else "",
                      payload if isinstance(payload, dict) else {}, fields=fields)
    except Exception:
        pass


def log_route(payload, tool_input, route, updated=None):
    """Record which band an unnamed spawn was routed to."""
    log_spawn(payload, tool_input, route["worker"], "band", updated)


def notice_once(session, key):
    """Whether to say `key` in this session now; the session record is what remembers it.

    Nothing that cannot be remembered is said, because a hook is a process per event and a
    notice nobody records is a notice repeated on every spawn.
    """
    module = posture_module()
    try:
        return bool(module.note_once(session, key))
    except Exception:
        return False


def tier_of(model, ladder):
    """The ladder name inside a model id or alias, or None for anything the ladder lacks."""
    if not isinstance(model, str):
        return None
    low = model.lower()
    for name in ladder:
        if name in low:
            return name
    return None


def transcript_model(path):
    """The model on the newest main-line assistant record, reading only the transcript's tail."""
    if not path:
        return None
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - TAIL_BYTES))
            tail = fh.read().decode("utf-8", "replace")
    except Exception:
        return None
    for line in reversed(tail.splitlines()):
        if '"assistant"' not in line:
            continue
        try:
            record = json.loads(line)
        except Exception:
            continue
        if not isinstance(record, dict) or record.get("type") != "assistant" or record.get("isSidechain"):
            continue
        message = record.get("message")
        model = message.get("model") if isinstance(message, dict) else None
        # Placeholder records ("<synthetic>") name no model; anything else is the session's, known or not.
        if isinstance(model, str) and model and not model.startswith("<"):
            return model
    return None


def agents_dirs(cwd):
    """`(project directories, the user's)` where Claude Code resolves an agent definition.

    Project before user, which is the tool's own precedence. `posture.user_agents_dir` holds the
    user directory's rule, so this hook and the SessionStart policy read one directory; the same
    expression stands in for the run where that sibling would not import.
    """
    module = posture_module()
    if module is not None:
        user = module.user_agents_dir(os.environ)
    else:
        config = os.environ.get("CLAUDE_CONFIG_DIR")
        user = (Path(config) if config else Path.home() / ".claude") / "agents"
    return ([Path(cwd) / ".claude" / "agents"] if isinstance(cwd, str) and cwd else []), user


def definition(kind, cwd):
    """The frontmatter of the definition a spawn of this type would resolve, or None.

    One reader for every question this hook asks of an agent definition, so "which file would
    the tool use" is answered once. A file that exists but will not parse ends the search the
    way it always has: the tool would resolve it, so no weaker root stands in for it.
    """
    if not isinstance(kind, str) or not AGENT_NAME.fullmatch(kind):
        return None
    project, user = agents_dirs(cwd)
    for root in project + [user]:
        try:
            header = (root / (kind + ".md")).read_text(encoding="utf-8").split("---", 2)[1]
        except Exception:
            continue
        fields = {}
        for line in header.splitlines():
            key, sep, value = line.partition(":")
            if sep:
                fields.setdefault(key.strip(), value.strip())
        return fields
    return None


def defined_tier(kind, cwd, ladder):
    """The ladder name an agent definition's `model:` line carries, project before user, or None."""
    fields = definition(kind, cwd)
    return tier_of(fields.get("model"), ladder) if fields else None


def is_unnamed(tool_input):
    """A spawn that named no agent definition, whatever model it asked for."""
    kind = tool_input.get("subagent_type")
    return not kind or kind == "general-purpose"


def is_bare(tool_input):
    return not tool_input.get("model") and is_unnamed(tool_input)


def announced(module, session, transcript, kind):
    """Whether this session was told, after it started, that it resolves `kind`.

    The runtime's own statement outranks the record written at session start, because it is
    later and it is about this session: a session that reloaded nothing announces nothing, so
    this can only ever widen what routes. The transcript is the discovery path and the record
    is the memory — the tail read is bounded, so what it found is kept where the next spawn can
    read it without the delta still being in the tail. An older `posture.py` beside this hook
    answers no, which is the conservative gate.
    """
    if module is None:
        return False
    reader = getattr(module, "transcript_agents", None)
    keeper = getattr(module, "remember_agents", None)
    remembered = getattr(module, "session_announced", None)
    try:
        names = reader(transcript) if reader else None
        if names is None:
            names = remembered(session) if remembered else None
        elif keeper:
            keeper(session, names)
        return kind in (names or ())
    except Exception:
        return False


def routable(kind, cwd, session=None, announce=False, transcript=None):
    """`(the user's definition, notice)` for a worker a reroute would name; one of them is None.

    A reroute must land on the definition the harness synced and on no other. A project-level
    `.claude/agents/<worker>.md` outranks the user's, so a repository that ships one would put
    its own instructions on every unnamed spawn of anyone who cloned it: that file is a reason
    to route nothing, named out loud. A machine that has not synced the workers is the same
    answer for the plainer reason that the tool could not resolve the type at all.

    A file on disk is not enough: a session resolves the registry it loaded, so this session
    must also have recorded the worker at its own start (`posture.sessions_dir`) or have been
    told about it since (`posture.transcript_agents`). With neither the spawn is left as it
    was, because a `subagent_type` this session cannot resolve fails the call outright.
    `announce` is the caller that speaks — the spawn hook, not the pricing one — and only it
    spends the once-per-session memory on that notice.
    """
    if not isinstance(kind, str) or not AGENT_NAME.fullmatch(kind):
        return None, None
    project, user = agents_dirs(cwd)
    for root in project:
        path = root / (kind + ".md")
        if path.is_file():
            return None, ("this repository ships " + str(path) + ", which would outrank the "
                          "harness's " + kind + ", so this spawn is not routed to the variant's "
                          "default band")
    if not (user / (kind + ".md")).is_file():
        return None, ("no " + kind + " definition is installed, so this spawn is not routed to "
                      "the variant's default band; run `citizen sync`")
    module = posture_module()
    known = module.session_agents(session) if module else None
    if known is not None:
        # Reading the record is evidence this session is alive, which keeps a long-running one
        # out of another session's sweep.
        module.refresh_session_record(session)
    if known is None or kind not in known:
        if announced(module, session, transcript, kind):
            return definition(kind, cwd) or {}, None
        notice = (kind + " is installed but this session started before it was; start a new "
                  "session to route unnamed spawns")
        if not (announce and notice_once(session, UNRESOLVABLE_NOTICE)):
            notice = None
        return None, notice
    return definition(kind, cwd) or {}, None


def switched_off(posture, role):
    """Whether the selection in force switches `role` off; a `posture.py` that cannot say means no.

    Sync withholds an `off` role's definition, but a session or project layer can switch one off
    without a sync, and the file the last sync wrote is still on disk. The selection decides.
    """
    reader = getattr(posture, "selection", None)
    if reader is None:
        return False
    try:
        return (reader(strict=False).get("roles") or {}).get(role) == "off"
    except Exception:
        return False


def band_route(posture, models, cwd, table=None, session=None, announce=False, transcript=None):
    """`(route, notice)` for a spawn that named nothing; a route is None when nothing routes it.

    The cost table is read here and nowhere else in this hook, so a spawn that named a role
    never pays for it. A variant with no `default_band` — and a table that would not resolve —
    routes nothing, which is what keeps 0.10.0 behaviour byte for byte.

    `brief-guard` calls this to price a spawn by the worker it is about to be routed to, and
    passes the table it has already built rather than making this build a second one; one
    answer to "where does an unnamed spawn go" is the point of the shared function.

    The effort a rerouted spawn actually runs at is the installed definition's, because effort
    is written at sync and the `Agent` tool takes none; the row's is what the selected variant
    would write at the next sync. The route carries both so the notice can name the difference.
    """
    try:
        table = posture.cost_table() if table is None else table
    except Exception:
        return None, None
    band = table.get("default_band")
    if band not in getattr(posture, "BANDS", ()):
        return None, None
    worker = posture.BAND_ROLES[band]
    if switched_off(posture, worker):
        return None, (worker + " is switched off in the selection, so this spawn is not routed to "
                      "the variant's default band")
    fields, notice = routable(worker, cwd, session, announce, transcript)
    if fields is None:
        return None, notice
    row = posture.row_for(table, worker) or {}
    return {"worker": worker, "row": row,
            "model": models.get(row.get("class")) if table.get("class_applies") else None,
            "effort": fields.get("effort") or row.get("effort"),
            "stale": bool(row.get("effort")) and fields.get("effort") != row.get("effort")}, None


def one_rung(payload, ladder):
    """Today's rule for a spawn that named nothing: `(model one class below, message)`.

    A model of None with a message is a spawn this hook decided not to move and said why; both
    None is a spawn it has nothing to say about — the session is already on the weakest class,
    or the transcript does not yet name a model.
    """
    session = transcript_model(payload.get("transcript_path"))
    current = tier_of(session, ladder)
    if session and current is None:
        # A lineup change the ladder has not caught up with must not pass for "nothing to do".
        return None, (f"the session model {session} is not on the ladder "
                      f"({', '.join(ladder)}), so this bare subagent stays on it; name a model or a role")
    if current is None or current == ladder[-1]:
        return None, None
    below = ladder[ladder.index(current) + 1]
    return below, f"bare subagent runs on {below}, one tier below the session's {current}"


def routed_message(route, model, requested):
    """Where the spawn went, on what, and how to choose next time; the hook's prefix is the caller's."""
    detail = [("model " + requested + " as asked") if requested
              else (route["row"].get("class") or model)]
    if route.get("effort"):
        detail.append(route["effort"] + " effort")
    shown = ", ".join(part for part in detail if part)
    return (f"unnamed subagent routed to {route['worker']}" + (f" ({shown})" if shown else "") +
            "; spawn worker-a, worker-b or worker-c to choose the band" +
            (" · the installed definition's effort is not the selected variant's; run "
             "`citizen sync` to apply the selected posture" if route.get("stale") else ""))


def emit(fields, system_message=None):
    fields["hookEventName"] = "PreToolUse"
    out = {"hookSpecificOutput": fields}
    if system_message:
        out["systemMessage"] = system_message
    print(json.dumps(out))


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    if not isinstance(payload, dict) or payload.get("tool_name") != "Agent":
        return
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return
    posture = posture_module()
    variant = posture.selected("delegation", DEFAULT_STANCE, strict=False) if posture else DEFAULT_STANCE
    if variant == "off":
        log_spawn(payload, tool_input, "deny", "delegation-off")
        emit({
            "permissionDecision": "deny",
            "permissionDecisionReason": f"Delegation is off; perform the work inline or change the selected stance. ({HOOK})",
        })
        return
    if variant != "tiered":
        return
    # Strongest class first, from the adapter's bindings. Without it there is no tier to move a
    # spawn to, so the call runs as written and says why, exactly as an unknown model does.
    models = posture.tier_models() if posture and hasattr(posture, "tier_models") else {}
    ladder = list(models.values())
    if len(ladder) < 2:
        # Only a call whose model this hook would have decided — a bare spawn, or one asking for
        # a class by name — is worth a notice; a named role with its own model is not this hook's.
        if is_bare(tool_input) or tool_input.get("model"):
            print(json.dumps({"systemMessage": f"{HOOK}: the adapter's class table names no tier to move a "
                              "spawn to, so this one runs as written; check the harness installation"}))
        return
    # Where a spawn that named nothing goes, which only the cost table knows. Built here and
    # only here, so a spawn naming a role never reads a sidecar.
    route = notice = None
    if posture and is_unnamed(tool_input) and hasattr(posture, "row_for"):
        try:
            route, notice = band_route(posture, models, payload.get("cwd"), None,
                                       payload.get("session_id"), True,
                                       payload.get("transcript_path"))
        except Exception:
            # An older `posture.py` beside a newer hook answers none of this. The whole routing
            # decision is one all-or-nothing question, and the safe answer is the behaviour
            # this hook had before bands existed: do not route, say nothing about it. An
            # exception escaping here reaches the coordinator, which denies the spawn.
            route = notice = None
    top = tier_of(tool_input.get("model"), ladder) == ladder[0]
    if top and not route:
        kind = tool_input.get("subagent_type")
        named = bool(kind) and kind != "general-purpose"
        declared = defined_tier(kind, payload.get("cwd"), ladder) if named else None
        if declared == ladder[0]:
            return  # the role declares the top class itself; the request only repeats it
        updated = dict(tool_input, model=declared or ladder[1])
        log_spawn(payload, tool_input, "demoted", "top-class", updated)
        emit({"updatedInput": updated},
             system_message=f"{HOOK}: {ladder[0]} is reached through a role that declares it, not by request; "
                            f"{kind if named else 'this spawn'} runs on {updated['model']}"
                            + (" · " + notice if notice else ""))
        return
    if route:
        updated = dict(tool_input, subagent_type=route["worker"])
        # A request for the top class is not a model this spawn named: it is a request the hook
        # refuses, and refusing it by demoting one rung would let an unnamed spawn beat a band
        # priced below that rung. So the band's own class decides, exactly as if none were asked.
        requested = None if top else tool_input.get("model")
        message = None
        if not requested:
            updated.pop("model", None)
            if route["model"]:
                updated["model"] = route["model"]
            else:
                # The band names no class this adapter maps, so the spawn falls to today's rule.
                fallback, message = one_rung(payload, ladder)
                if fallback:
                    updated["model"] = fallback
        # Where an unnamed spawn goes is the standing arrangement, said once a session. What the
        # caller asked for and did not get is said every time it happens.
        parts = []
        if notice_once(payload.get("session_id"), ROUTED_NOTICE):
            parts.append(routed_message(route, updated.get("model"), requested))
        if message:
            parts.append(message)
        if top:
            parts.append(f"{ladder[0]} is reached through a role that declares it, not by request")
        log_route(payload, tool_input, route, updated)
        emit({"updatedInput": updated},
             system_message=(f"{HOOK}: " + " · ".join(parts)) if parts else None)
        return
    if not is_bare(tool_input):
        return
    below, message = one_rung(payload, ladder)
    if below is None:
        if message:
            print(json.dumps({"systemMessage": f"{HOOK}: {message}"
                              + (" · " + notice if notice else "")}))
        return
    updated = dict(tool_input, model=below)
    log_spawn(payload, tool_input, "one-rung", "bare", updated)
    emit({"updatedInput": updated},
         system_message=f"{HOOK}: {message}" + (" · " + notice if notice else ""))


if __name__ == "__main__":
    main()

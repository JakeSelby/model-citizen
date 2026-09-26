"""Normalize lifecycle events and compose shared policies before native encoding."""
import contextlib
import difflib
import fnmatch
import importlib.util
import io
import json
import os
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICIES = ROOT / "policy" / "hooks"
ALIASES = {"exec_command": "Bash", "shell_command": "Bash", "shell": "Bash",
           "spawn_agent": "Agent", "write_file": "Write", "edit_file": "Edit"}
BASE_EVENTS = ("PreToolUse", "PostToolUse", "SessionStart", "Stop", "SessionEnd")
# The usage feed's own events. Only Claude Code carries them; `adapters/codex/capabilities.json`
# declares the gap rather than registering an event that runtime does not raise.
FEED_EVENTS = ("UserPromptSubmit", "SubagentStart", "SubagentStop")
EVENTS = {"claude-code": BASE_EVENTS + FEED_EVENTS, "codex": BASE_EVENTS}
# The tools that write a file by path, after `ALIASES`; `apply_patch` names its paths in the patch.
FILE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch")
ROLE_NAME = re.compile(r"[a-z][a-z0-9-]*")
# A brief may declare the role it belongs to. The line stands alone so the declaration cannot be
# produced by prose that happens to mention a role, and it travels with the text: a brief pasted
# into an unnamed spawn still carries it, which is the whole point.
ROLE_MARKER = re.compile(r"^[ \t]*harness-role:[ \t]*([a-z][a-z0-9-]*)[ \t]*$", re.M)
# What a session remembers about a spawn it refused, and how a later spawn is matched against it.
# Bounded on both axes: 32 entries of 2,000 normalised characters is far past any real fan-out,
# and a session record is not a place to accumulate transcript.
DENIED_KEY = "denied_spawns"
NOTICED_KEY = "session_notices"
DENIED_MAX = 32
FINGERPRINT_MAX = 2000
PREFIX_MATCH = 400
SIMILARITY = 0.85


def load(name):
    spec = importlib.util.spec_from_file_location("harness_" + name.replace("-", "_"), POLICIES / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_DECISIONS = []


def decisions():
    """The decision log, or None when it cannot be loaded. Loaded once per process.

    Every caller treats None as "this decision is not logged" and carries on: the log records
    what the harness decided and must never be able to change it.
    """
    if not _DECISIONS:
        try:
            _DECISIONS.append(load("decisions"))
        except Exception:
            _DECISIONS.append(None)
    return _DECISIONS[0]


def normalize(payload):
    event = dict(payload)
    name = str(event.get("tool_name", "")).rsplit(".", 1)[-1]
    event["tool_name"] = ALIASES.get(name, name)
    inputs = event.get("tool_input") or {}
    if not isinstance(inputs, dict):
        raise ValueError("tool_input must be an object")
    inputs = dict(inputs)
    if event["tool_name"] == "Bash":
        command = inputs.get("command", inputs.get("cmd"))
        if isinstance(command, list):
            command = shlex.join(command)
        if not isinstance(command, str):
            raise ValueError("shell command is missing")
        inputs["command"] = command
    if event["tool_name"] == "Agent":
        inputs["prompt"] = inputs.get("prompt", inputs.get("message", ""))
        inputs["subagent_type"] = inputs.get("subagent_type", inputs.get("agent_type"))
    event["tool_input"] = inputs
    return event


# The hooks selection for the dispatch in progress, so one event resolves the ladder once.
_SWITCHES = []


def switches():
    """`{hook id: "on"|"off"}` from the selection, resolved as a hook resolves it: not strictly.

    A selection that will not resolve leaves every hook on, and a core hook switched off without
    its acknowledgement resolves `on` (`posture.core_refusals`), so a broken file never turns
    enforcement off.
    """
    if _SWITCHES:
        return _SWITCHES[-1]
    try:
        return load("posture").selection(strict=False).get("hooks") or {}
    except Exception:
        return {}


def enabled(name):
    """Whether hook id `name` is on. Ids are `catalog.HOOK_IDS`, the same on every runtime."""
    return switches().get(name) != "off"


def invoke(name, event):
    """Run policy module `name` on `event`; `{}`, without loading it, when its id is off."""
    if not enabled(name):
        return {}
    module = load(name)
    output = io.StringIO()
    old = sys.stdin
    try:
        sys.stdin = io.StringIO(json.dumps(event))
        with contextlib.redirect_stdout(output):
            module.main()
    finally:
        sys.stdin = old
    text = output.getvalue().strip()
    return json.loads(text) if text else {}


def selected(name, fallback):
    """One dimension's variant, resolved by the same file the policy hooks load."""
    return load("posture").selected(name, fallback)


def investigating(runtime, event):
    """Whether this call is plan-mode investigation the selected posture already authorises.

    Plan mode exists to force a plan, questions and a wait before anything is executed. It is not
    a reason to drop research below the permission posture the user chose for every other mode,
    so under `bypass` or `auto` the harness answers for the commands native plan mode would
    otherwise prompt on. Under `manual` and `inherit` it answers nothing new, and Codex is left
    alone because its client rejects `allow` outright.

    Fails closed: a config that will not open is not a posture anybody selected.
    """
    if runtime != "claude-code" or event.get("permission_mode") != "plan":
        return False
    try:
        module = load("posture")
        return module.permissions() in module.OPEN_POSTURES
    except Exception:
        return False


def plan_allowed_tool(tool):
    """Whether `tool` matches a glob the user listed under `plan_allow_tools`.

    Nothing is inferred from the tool name itself: a PreToolUse payload says nothing about
    whether an MCP tool reads or writes, so the list is empty until the user fills it.
    """
    if not isinstance(tool, str) or not tool:
        return False
    try:
        patterns = load("posture").plan_allow_tools()
    except Exception:
        return False
    return any(fnmatch.fnmatchcase(tool, pattern) for pattern in patterns)


def constrained_role(name):
    """The contract of `name` when it is a shared role an isolated worker must run, else None."""
    if not (isinstance(name, str) and ROLE_NAME.fullmatch(name)):
        return None
    if not (ROOT / "primitives/roles" / (name + ".md")).is_file():
        return None
    from . import catalog
    try:
        fields, _ = catalog.role_contract(ROOT, name)
    except ValueError:
        # A shipped contract that will not load is the one case the guard cannot judge, so it
        # judges against itself. Refusing a native spawn of a role whose own file is broken costs
        # a message; allowing one runs a constrained role unconfined, which is the defect this
        # guard exists for. `UNRESOLVED` says so, and carries no class to bind a model with.
        return dict(UNRESOLVED, name=name)
    return fields if fields["authority"] in ("read-only", "artifact-write") else None


# The authority is the safe assumption, not a reading of the file: nothing here came from one.
UNRESOLVED = {"authority": "read-only", "unresolved": True}


# A worker that may both read a workspace and reach the network can carry what it read back out,
# so the isolated adapters hold every role to Read/Grep/Glob. Only `gatherer` is routinely asked
# for online evidence, so only its refusal has somewhere else to send that half of the work.
OFFLINE_NOTE = {"gatherer": "An isolated gatherer is offline — Read, Grep and Glob, no WebFetch or "
                            "WebSearch — so send a file or repository dimension to the worker and a web "
                            "dimension to an in-session band worker (worker-a, worker-b or worker-c)."}


# The one sentence the refusal, the `delegation` stance and the shared role descriptions all
# carry, word for word, so a session that follows the stance is never surprised by the refusal
# (issue #304). `tests/test_role_refusal_matches_the_stance.py` holds the three copies together.
CONFINEMENT_SENTENCE = ("A read-only role runs through `citizen role run <role>`: confinement is "
                        "read roots and return shape, not the absence of write tools, so `builder` "
                        "needs neither and spawns natively.")


def harness_command():
    """The CLI by the absolute path of this checkout, quoted for a shell.

    No documented install puts `harness` on `PATH`, so a bare name in the refusal is a command the
    refused client cannot run (issue #761). The checkout the hook runs from is the one that answers.
    """
    return shlex.quote(str(ROOT / "bin" / "harness"))


def role_instruction(runtime, name, fields):
    """How this role is actually run, ending in the sentence the stance and the roles also carry."""
    from . import catalog
    # The role's class picks the model; the session's is the fallback, never the default.
    mapped = (fields is not None and not fields.get("unresolved")
              and "model" in catalog.role_binding(ROOT, runtime, fields))
    return ("Use " + harness_command() + " role run " + name + " --runtime " + runtime
            + ("" if mapped else " --model <session-model>")
            + " --workspace <repo> --prompt-file <brief-file>. "
            "Planner workers also require --artifact <new-plan.md>. " + CONFINEMENT_SENTENCE
            + (" " + OFFLINE_NOTE[name] if name in OFFLINE_NOTE else ""))


def role_deny(runtime, name, fields, origin=None):
    """The refusal a constrained role's spawn gets. `origin` says what the harness recognised."""
    reason = ("This constrained harness role requires an isolated worker. "
              + role_instruction(runtime, name, fields))
    return {"hookSpecificOutput": {"permissionDecision": "deny",
            "permissionDecisionReason": origin + " " + reason if origin else reason}}


def confinement_deny(runtime, session_id, name, fields, prompt, recognised):
    """`role_deny`, with the decision-log row every confinement refusal writes.

    `recognised` is what named the role: the spawn's `subagent_type`, or a `harness-role:` line
    in its brief. The row's input leads with the role and that signal, then the brief's
    fingerprint, so a refusal is countable by role without a second field on the row, and a
    refused spawn is never mistaken for a spawn that ran: the usage ledger's own rule for that
    is in `usage-log.py`.
    """
    module = decisions()
    if module is not None:
        module.record("role-confinement", "deny",
                      name + " (" + recognised + "): " + fingerprint(prompt),
                      {"session_id": session_id}, runtime)
    return role_deny(runtime, name, fields)


def marker_role(prompt):
    """`(name, fields)` for a brief that declares its role on a `harness-role:` line, else None.

    A marker naming something that is not a constrained shared role says nothing: the guard is a
    declaration the harness can verify, not a word the model can use to refuse arbitrary work.
    """
    if not isinstance(prompt, str):
        return None
    for name in ROLE_MARKER.findall(prompt):
        fields = constrained_role(name)
        if fields is not None:
            return name, fields
    return None


# A workflow script's `agent()` calls never reach the `Agent` hooks, so the launch is the one call
# the harness sees (docs/spikes/2026-09-22-workflow-tool-band-routing-and-ledger.md). The script
# is JavaScript: a role is named as a quoted `agentType` value, and a brief's `harness-role:` line
# usually sits inside a string literal, bounded by a quote or a `\n` escape rather than a newline.
WORKFLOW_POINT = "workflow-launch"
WORKFLOW_AGENT_TYPE = re.compile(r"\bagentType\b")
# A literal counts only when it is the whole value: `'worker-a' && 'reviewer'` is computed.
WORKFLOW_AGENT_VALUE = re.compile(r"""['"]?\s*[:=]\s*(['"`])([^'"`\\\n]*)\1(?=\s*(?:[,;)\]}]|$))""")
WORKFLOW_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
WORKFLOW_LITERAL = re.compile(r"""(['"`])([a-z][a-z0-9-]*)\1""")
# A literal with an escape in it, such as `'re\u0076iewer'`, which evaluates to a role name.
WORKFLOW_ESCAPED = re.compile(r"""(['"`])((?:(?!\1)[^\\\n])*\\.(?:(?!\1)[^\\\n]|\\.)*)\1""")
WORKFLOW_ESCAPE = re.compile(r"""\\(?:u\{([0-9A-Fa-f]{1,6})\}|u([0-9A-Fa-f]{4})|x([0-9A-Fa-f]{2})|([nrtvfb0])|(.))""",
                             re.S)
WORKFLOW_CONTROL = {"n": "\n", "r": "\r", "t": "\t", "v": "\v", "f": "\f", "b": "\b", "0": "\0"}
WORKFLOW_MARKER = re.compile(r"""(?:^|\\n|['"`])[ \t]*harness-role:[ \t]*([a-z][a-z0-9-]*)[ \t]*"""
                             r"""(?=$|\\n|\\r|['"`])""", re.M)
WORKFLOW_SCRIPT_MAX = 1024 * 1024
# A file longer than the read limit runs in full but cannot be judged in full, so it is refused.
WORKFLOW_TOO_LARGE = object()


def workflow_script(event):
    """The text a `Workflow` launch will run, None when this hook cannot read it, or
    `WORKFLOW_TOO_LARGE` for a file past `WORKFLOW_SCRIPT_MAX` characters.

    The runtime takes `scriptPath` over `script` over `name`; a name resolves to a file under a
    `.claude/workflows/` directory, project first. A built-in workflow and a resume by run id
    carry no text here: the first is the runtime's own script, and the second re-runs one whose
    launch this hook already judged.
    """
    inputs = event.get("tool_input") or {}
    cwd = Path(event.get("cwd") or os.getcwd())
    candidates = []
    if isinstance(inputs.get("scriptPath"), str) and inputs["scriptPath"]:
        candidates.append(cwd / Path(inputs["scriptPath"]).expanduser())
    elif isinstance(inputs.get("script"), str):
        return inputs["script"]
    elif isinstance(inputs.get("name"), str) and WORKFLOW_NAME.fullmatch(inputs["name"]):
        for base in (cwd, Path(os.environ.get("HOME") or Path.home())):
            for suffix in (".js", ".mjs", ".ts"):
                candidates.append(base / ".claude" / "workflows" / (inputs["name"] + suffix))
    for candidate in candidates:
        try:
            if candidate.is_file():
                with open(str(candidate), encoding="utf-8", errors="replace") as stream:
                    text = stream.read(WORKFLOW_SCRIPT_MAX + 1)
                return WORKFLOW_TOO_LARGE if len(text) > WORKFLOW_SCRIPT_MAX else text
        except OSError:
            continue
    return None


def workflow_literal(text, keep_quoting=False):
    """`text` with its JavaScript escapes decoded: a literal's body, or with `keep_quoting` a
    whole script, where a quote or backslash escape stays escaped so literals keep their bounds."""
    def decode(match):
        code = match.group(1) or match.group(2) or match.group(3)
        if code is not None:
            return chr(min(int(code, 16), 0x10FFFF))
        if match.group(4) is not None:
            return WORKFLOW_CONTROL[match.group(4)]
        return match.group(0) if keep_quoting else match.group(5)
    return WORKFLOW_ESCAPE.sub(decode, text)


def workflow_role(script):
    """`(name, fields, how)` for the first constrained role a workflow script names, else None.

    The script is read twice, as written and with its escapes decoded, because an escaped key
    (`agent\\u0054ype`) or an escaped newline around a `harness-role:` line reads as the plain
    form once JavaScript evaluates it. See `workflow_role_in` for one reading.
    """
    if not isinstance(script, str):
        return None
    named = workflow_role_in(script)
    decoded = workflow_literal(script, keep_quoting=True)
    if named is None and decoded != script:
        named = workflow_role_in(decoded)
    return named


def workflow_role_in(script):
    """The constrained role one reading of a workflow script names, as `workflow_role` returns it.

    A quoted `agentType` value is read directly. Any other mention of `agentType` — a computed
    value, a shorthand property — cannot be, so then any whole string literal naming a
    constrained role counts: a script that picks its agent type at run time from a list holding
    `'reviewer'` names that role as surely as one that writes it inline. That errs towards
    refusing, as `constrained_role` does for a contract it cannot load. A marker is matched as `marker_role` matches one, a standalone
    declaration naming a constrained shared role.
    """
    computed = False
    for mention in WORKFLOW_AGENT_TYPE.finditer(script):
        match = WORKFLOW_AGENT_VALUE.match(script, mention.end())
        if match is None or "${" in match.group(2):
            computed = True
            continue
        fields = constrained_role(match.group(2))
        if fields is not None:
            return match.group(2), fields, "names `" + match.group(2) + "` in agentType"
    for name in WORKFLOW_MARKER.findall(script):
        fields = constrained_role(name)
        if fields is not None:
            return name, fields, "carries a `harness-role: " + name + "` marker"
    if computed:
        literals = [m.group(2) for m in WORKFLOW_LITERAL.finditer(script)]
        literals += [workflow_literal(m.group(2)) for m in WORKFLOW_ESCAPED.finditer(script)]
        for value in literals:
            fields = constrained_role(value) if re.fullmatch(r"[a-z][a-z0-9-]*", value) else None
            if fields is not None:
                return (value, fields, "computes agentType and names `" + value
                        + "` in a string literal")
    return None


def workflow_results(runtime, event):
    """The answers to a `Workflow` launch, with its decision row written.

    Every launch is a row, allowed ones included, because a launch is a batch of spawns no other
    row accounts for. The row's input is the script as judged, or the tool input when the script
    could not be read.
    """
    results = []
    script = workflow_script(event)
    if script is WORKFLOW_TOO_LARGE:
        results.append({"hookSpecificOutput": {"permissionDecision": "deny",
            "permissionDecisionReason": "This workflow script is longer than the "
            + str(WORKFLOW_SCRIPT_MAX) + " characters the guard reads, so the constrained roles "
            "it names cannot be checked; split it or send it inline."}})
    elif selected("delegation", "tiered") == "off":
        results.append({"hookSpecificOutput": {"permissionDecision": "deny",
            "permissionDecisionReason": "Delegation is off, and every agent() call in a workflow "
            "script is a spawn; perform the work inline or change the selected stance."}})
    else:
        named = workflow_role(script)
        if named is not None:
            results.append(role_deny(runtime, named[0], named[1],
                                     "This workflow script " + named[2] + ", and a script's "
                                     "agent() calls run in session, past every spawn guard."))
    if not results and enabled("allow-readonly-bash") and investigating(runtime, event) \
            and plan_allowed_tool("Workflow"):
        results.append({"hookSpecificOutput": {"permissionDecision": "allow",
            "permissionDecisionReason": "Plan-mode research tool named by plan_allow_tools, "
            "run at the permission posture you selected."}})
    module = decisions()
    if module is not None:
        denied = any(r["hookSpecificOutput"].get("permissionDecision") == "deny" for r in results)
        text = script if isinstance(script, str) else json.dumps(event.get("tool_input") or {}, sort_keys=True)
        module.record(WORKFLOW_POINT, "deny" if denied else "allow", text, event, runtime)
    return results


def framework_deny(runtime, session_id, prompt, subagent_type):
    """The refusal a declared integration's spawn gets, or None when this call is not one.

    The classification is the descriptor's, not the model's: `subagent_type` is one signal among
    several and carries no more weight than the rest. Only a role the guard would constrain is
    ever matched, so a mapping cannot be used to refuse work the harness does not confine.

    This refusal is deliberately not remembered. A remembered one is matched by prefix or
    similarity for the rest of the session, so one wrong classification would go on refusing the
    corrected brief too; the descriptor answers each spawn on its own evidence instead. See
    `frameworks.py`.
    """
    try:
        from . import frameworks
        match = frameworks.classify(prompt, subagent_type, accept=lambda role: constrained_role(role) is not None)
    except Exception:
        return None
    if match is None:
        return None
    name = match["role"]
    fields = constrained_role(name)
    if fields is None:
        return None
    module = decisions()
    if module is not None:
        module.record("framework-spawn", "deny", fingerprint(prompt),
                      {"session_id": session_id}, runtime)
    return role_deny(runtime, name, fields, frameworks.origin(match))


def descriptor_notice(session_id):
    """One `systemMessage` naming every integration descriptor the loader could not use.

    Said once per session, because a descriptor nobody can load is enforcement that stopped, and
    the only place a user would otherwise see that is a refusal that never came.
    """
    try:
        from . import frameworks
        broken = frameworks.ignored()
    except Exception:
        return None
    if not broken or not notice_once(session_id, "integration-descriptors"):
        return None
    module = decisions()
    for name, reason in broken:
        if module is not None:
            module.record("integration-descriptor", "ignored", name + ": " + reason,
                          {"session_id": session_id})
    return {"systemMessage": "harness:integrations: ignored " + "; ".join(
        name + " (" + reason + ")" for name, reason in broken)
        + ". Spawns that descriptor would have confined are not being classified."}


def notice_once(session_id, key):
    """Whether this session has yet to be told `key`. Records that it now has. Never raises."""
    try:
        posture = load("posture")
        record = posture.read_session_record(session_id) or {}
        said = [k for k in record.get(NOTICED_KEY, []) if isinstance(k, str)]
        if key in said:
            return False
        posture.write_session_record(session_id, dict(record, **{NOTICED_KEY: (said + [key])[-DENIED_MAX:]}))
        return True
    except Exception:
        return False


def fingerprint(prompt):
    """A brief reduced to what a re-spawn cannot vary: whitespace, case and length all removed."""
    return " ".join(prompt.split()).casefold()[:FINGERPRINT_MAX] if isinstance(prompt, str) else ""


def same_work(left, right):
    """Whether two fingerprints are the same brief. Equality, containment, then similarity.

    Containment is tested only on a prefix long enough to be evidence; a short brief that happens
    to appear inside a longer unrelated one is a false refusal, and a refusal nobody can explain
    is worse than the evasion it prevents.
    """
    if not left or not right:
        return False
    if left == right:
        return True
    for a, b in ((left, right), (right, left)):
        if len(a) >= PREFIX_MATCH and a[:PREFIX_MATCH] in b:
            return True
    return difflib.SequenceMatcher(None, left, right).ratio() >= SIMILARITY


def denied_spawns(session_id):
    """What this session has already refused as a constrained-role spawn; `[]` for anything else.

    State a hook cannot read is state that does not exist. The guard then behaves exactly as it
    did before it was written, because a spawn hook that raises is worse than one that forgets.
    """
    try:
        record = load("posture").read_session_record(session_id)
        entries = (record or {}).get(DENIED_KEY)
        return [e for e in entries if isinstance(e, dict) and isinstance(e.get("prompt"), str)] \
            if isinstance(entries, list) else []
    except Exception:
        return []


def remember_denial(session_id, name, prompt):
    """Add one refusal to the session's memory, newest last. Best effort, never raises."""
    text = fingerprint(prompt)
    if not text:
        return False
    try:
        posture = load("posture")
        record = posture.read_session_record(session_id) or {}
        entries = [e for e in denied_spawns(session_id) if e.get("prompt") != text]
        entries.append({"role": name, "prompt": text})
        return bool(posture.write_session_record(session_id, dict(record, **{DENIED_KEY: entries[-DENIED_MAX:]})))
    except Exception:
        return False


def evasion_deny(runtime, session_id, prompt):
    """The refusal a re-spawn of already-refused work gets, or None when this is not that."""
    text = fingerprint(prompt)
    for entry in reversed(denied_spawns(session_id)):
        if same_work(text, entry["prompt"]):
            name = entry.get("role") if isinstance(entry.get("role"), str) else ""
            module = decisions()
            if module is not None:
                # The fingerprint, not the brief: it is what the comparison actually ran on,
                # and a matched refusal is the one judgment here worth a label.
                module.record("evasion-deny", "deny", text, {"session_id": session_id}, runtime)
            return {"hookSpecificOutput": {"permissionDecision": "deny",
                    "permissionDecisionReason": "This work was refused as a native " + name
                    + " spawn in this session; dropping or changing the role name does not change that. "
                    + role_instruction(runtime, name, constrained_role(name))}}
    return None


def log_bash_decision(runtime, event, results, command=None, confirmed=False):
    """Record the permission answer the harness gave this command, when it gave one.

    Only `ask` and `deny` are graded rows. An approval is the harness declining to interrupt,
    and "it ran" says nothing about whether declining was right; a refusal or a prompt is the
    judgment a later label can grade. The row is written here rather than in `grade-bash.py`
    because this is where the answer is composed: the grader's threshold, the permission mode
    and plan-mode investigation all fold together into one answer, and only one is given.

    An allowed command goes to `record_allowed`, which keeps one in twenty of them as an
    ungraded negative — but only where the harness gave the allow *and the runtime was told*.
    A command the harness said nothing about is the runtime's own to answer and may still be
    prompted on or refused, and on Codex a plain approval is dropped from the output for the
    reason `_encode_pre` gives, so neither is evidence that anything was allowed. A confirmed
    command is not one either: it reached here because the user answered a prompt the harness
    raised, so it belongs to the earlier `ask` row.
    """
    module = decisions()
    if module is None:
        return
    answers = [r.get("hookSpecificOutput", {}).get("permissionDecision") for r in results]
    answer = next((choice for choice in ("deny", "ask", "allow") if choice in answers), None)
    if answer not in ("deny", "ask"):
        if answer == "allow" and not confirmed and runtime != "codex":
            module.record_allowed(command if command is not None
                                  else event["tool_input"]["command"], event, runtime)
        return
    command = event["tool_input"]["command"]
    module.record("grade-bash", answer, command, event, runtime,
                  key=module.match_key(event, command))


def log_bash_outcome(runtime, event):
    """Join `ran` to the decision this completed command belongs to, when there was one.

    The tool ran, so whatever the harness asked, the user let it through. A command nothing was
    asked about has no decision in the log and gets no record; a command that was asked about
    and never came back is closed as `not_run` at SessionEnd, because an outright refusal and
    an interrupted turn look identical from here.
    """
    module = decisions()
    if module is None:
        return
    command = (event.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not command:
        return
    identity = module.decision_id("grade-bash", module.match_key(event, command))
    module.observe_if_logged(identity, module.RAN, "grade-bash", event.get("session_id") or "")


def encode_pre(runtime, original, normalized, results):
    encoded = _encode_pre(runtime, original, normalized, results)
    # A policy's notice is the only trace of a rewrite the user would otherwise never see.
    notices = [r["systemMessage"] for r in results if isinstance(r.get("systemMessage"), str) and r["systemMessage"]]
    if notices and runtime == "claude-code":
        encoded = dict(encoded, systemMessage="\n".join(notices))
    # Context for the agent, such as an overlap warning that decides nothing on its own.
    contexts = [r.get("hookSpecificOutput", {}).get("additionalContext") for r in results]
    contexts = [c for c in contexts if isinstance(c, str) and c]
    if contexts and runtime == "claude-code":
        fields = dict(encoded.get("hookSpecificOutput") or {"hookEventName": "PreToolUse"})
        fields["additionalContext"] = "\n".join(contexts)
        encoded = dict(encoded, hookSpecificOutput=fields)
    return encoded


def _encode_pre(runtime, original, normalized, results):
    decisions = [r.get("hookSpecificOutput", {}).get("permissionDecision") for r in results]
    strongest = next((choice for choice in ("deny", "ask", "allow") if choice in decisions), None)
    reasons = [r.get("hookSpecificOutput", {}).get("permissionDecisionReason", "") for r in results]
    reason = "\n".join(x for x in reasons if x)
    fields = {"hookEventName": "PreToolUse"}
    # A Codex client rejects the whole hook output when it carries an unsupported `allow`, so a
    # plain approval says nothing and lets that runtime's own default stand.
    if strongest and not (runtime == "codex" and strongest == "allow"):
        fields["permissionDecision"] = "deny" if runtime == "codex" and strongest == "ask" else strongest
        fields["permissionDecisionReason"] = reason
    if strongest in ("deny", "ask"):
        return {"hookSpecificOutput": fields}
    changes = {}
    for result in results:
        changes.update({key: value for key, value in result.get("hookSpecificOutput", {}).get("updatedInput", {}).items()
                        if normalized["tool_input"].get(key) != value})
    if changes:
        updated = dict(original.get("tool_input") or {})
        before = normalized["tool_input"]
        for key, value in changes.items():
            if before.get(key) == value:
                continue
            native_key = key
            if key == "command" and "cmd" in updated:
                native_key = "cmd"
            elif key == "prompt" and "message" in updated:
                native_key = "message"
            elif key == "subagent_type" and "agent_type" in updated:
                native_key = "agent_type"
            updated[native_key] = value
        # Codex requires allow for rewrites. Do not manufacture an approval to format output.
        can_rewrite = runtime == "claude-code" or strongest == "allow" or normalized["tool_name"] == "Agent"
        if can_rewrite and updated != original.get("tool_input"):
            fields["updatedInput"] = updated
            if runtime == "codex":
                fields["permissionDecision"] = "allow"
                if reason:
                    fields["permissionDecisionReason"] = reason
    return {"hookSpecificOutput": fields} if len(fields) > 1 else {}


def policy_file_result(runtime, event):
    """The answer to a file-tool write to a governance policy file, or None.

    Asked about in a prompting mode. Where nothing can prompt it is refused, and in Claude
    Code's auto mode the refusal names an approval code for that exact edit, which the user's
    `approve <code>` reply lets through once, as it does a Bash command `grade-bash` refused.
    """
    grader = load("grade-bash")
    guarded = grader.govern_file(event["tool_name"], event["tool_input"], patch_paths(event),
                                 event, runtime)
    if guarded is None:
        return None
    subject, sentence = guarded
    mode, session = event.get("permission_mode"), event.get("session_id")
    if runtime != "codex" and mode not in grader.DENY_MODES:
        return {"hookSpecificOutput": {"permissionDecision": "ask",
                                       "permissionDecisionReason": sentence}}
    code = grader.approval_code(mode, session, subject) if runtime == "claude-code" else None
    if code is not None and grader.approved(mode, session, subject):
        return None
    tail = grader.FILE_APPROVAL_TAIL % code if code else grader.FILE_DENY_TAIL
    return {"hookSpecificOutput": {"permissionDecision": "deny",
                                   "permissionDecisionReason": sentence + tail}}


def patch_paths(event):
    inputs = event["tool_input"]
    paths = [inputs.get("file_path"), inputs.get("path")]
    response = event.get("tool_response")
    if isinstance(response, dict):
        paths.append(response.get("filePath"))
    if event["tool_name"] == "apply_patch":
        patch = inputs.get("command", inputs.get("patch", ""))
        if isinstance(patch, str):
            # Every path a patch touches, deletions included: a delete-only patch that named no
            # path would otherwise pass every guard on a file it removes.
            paths.extend(re.findall(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$",
                                    patch, re.M))
    return sorted(set(str(Path(event.get("cwd") or os.getcwd()) / p) for p in paths if p))


def store_write_deny(paths):
    """The store guard without `approvals.py`, for when that module cannot load.

    Every file-tool call would otherwise fail on the load and be denied as unverified; this
    refuses only a write under the approvals directory and lets the rest through. The path is
    the one `approvals.store_dir` names, resolved here from the same environment."""
    home = os.environ.get("HARNESS_HOME") or os.environ.get("HOME") or str(Path.home())
    root = os.path.realpath(os.path.join(home, ".local", "state", "agent-harness", "approvals"))
    for path in paths:
        target = os.path.realpath(os.path.expanduser(str(path)))
        if target == root or target.startswith(root + os.sep):
            return {"hookSpecificOutput": {"permissionDecision": "deny",
                    "permissionDecisionReason": "The approvals store is written only from the user's "
                    "own prompt, so no tool may write to it."}}
    return None


def dispatch(runtime, payload):
    if runtime not in ("claude-code", "codex"):
        raise ValueError("unknown runtime")
    _SWITCHES.append(switches())
    try:
        return _dispatch(runtime, payload)
    finally:
        _SWITCHES.pop()


def _dispatch(runtime, payload):
    """Compose the policies for one event. Logic that is not an `invoke` checks its owning id:
    Bash grading, its ask and its decision log are `grade-bash`; plan-mode and read-only allows
    are `allow-readonly-bash`; the integration notice is `tier-agent-spawns`. Role confinement,
    by name, marker, framework mapping or evasion, has no id and runs with every hook off, as the
    Workflow launch guard does: a switch routes spawns, it never unconfines a role."""
    event = normalize(payload)
    kind, tool = event.get("hook_event_name"), event.get("tool_name")
    if kind == "PreToolUse":
        results = []
        # The store of approvals the user typed is the user's alone; `grade-bash` consumes it, so
        # it guards it too. A Bash write to it is graded, a file-tool write is refused here.
        if tool in FILE_TOOLS and enabled("grade-bash"):
            paths = patch_paths(event)
            try:
                forged = load("approvals").file_write_deny(paths)
            except Exception:
                forged = store_write_deny(paths)
            if forged is not None:
                results.append(forged)
            if forged is None:
                # A governance policy file is edited only with the user's yes, each time: the
                # provider reads it, so the agent it governs must not grant itself a level.
                guarded = policy_file_result(runtime, event)
                if guarded is not None:
                    results.append(guarded)
        # Only a rewrite: the plan-mode approval below still answers for this tool.
        if tool == "SendUserFile" and runtime == "claude-code":
            results.append(invoke("stage-user-files", event))
        # A live sibling's claim on the path: policy/hooks/intent-overlap.py.
        if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            results.append(invoke("intent-overlap", event))
        if tool == "Bash":
            grading, readonly = enabled("grade-bash"), enabled("allow-readonly-bash")
            grader = load("grade-bash") if grading or readonly else None
            if grader is not None and grader.ro is None:
                raise RuntimeError("command classifier unavailable")
            # Shared stance resolution includes explicit project and session selections.
            variant = selected("autonomy", "execute")
            raw = command = event["tool_input"]["command"]
            confirmed = False
            grade = verb = target = family = None
            if grader is not None:
                command, confirmed = grader.strip_marker(command)
                grade, verb, target, family = grader.grade_text(command, event.get("cwd", ""))
            asked = grading and bool(grade) and not confirmed and grade >= grader.THRESHOLDS.get(variant, 1)
            # The decision provider, when one is configured, is asked only about what the stance
            # lets through, so it can add a prompt and never remove one.
            governed = None
            if grading and bool(grade) and not confirmed and not asked:
                governed = grader.govern(command, event.get("cwd", ""), grade, variant, event, runtime)
                asked = governed is not None
            if asked and governed is not None and governed[0] == "deny":
                results.append({"hookSpecificOutput": {"permissionDecision": "deny",
                    "permissionDecisionReason": grader.reason(grade, verb, target, family, variant)
                    + " " + governed[1]}})
            elif asked:
                mode, session = event.get("permission_mode"), event.get("session_id")
                decision = "deny" if runtime == "codex" or mode in grader.DENY_MODES else "ask"
                # Codex raises no UserPromptSubmit, so only Claude Code's auto mode can carry an
                # approval the user typed; `grade-bash.py` owns the channel and its wording.
                channel = runtime == "claude-code" and decision == "deny"
                if channel and grader.approved(mode, session, raw):
                    asked, confirmed = False, True
                else:
                    why = grader.reason(grade, verb, target, family, variant)
                    if governed is not None:
                        why += " " + governed[1]
                    code = grader.approval_code(mode, session, raw) if channel else None
                    results.append({"hookSpecificOutput": {"permissionDecision": decision,
                        "permissionDecisionReason": why + (grader.APPROVAL_TAIL % code if code else "")}})
            # Grade 0 is proved read-only, so it is approved in every mode. Grades 1 and 2 are the
            # ones native plan mode prompts on: a script the grammar cannot read through, a
            # scratch redirect, a test run. Under an open posture the first is investigation and
            # the second is not, and the autonomy stance still outranks both when it already asked.
            plan = readonly and investigating(runtime, event)
            if readonly and grade == 0:
                results.append({"hookSpecificOutput": {"permissionDecision": "allow"}})
            elif plan and not asked and grade == 1:
                results.append({"hookSpecificOutput": {"permissionDecision": "allow",
                    "permissionDecisionReason": "Plan-mode investigation, run at the permission posture you selected."}})
            elif plan and not asked and not confirmed and grade == 2:
                results.append({"hookSpecificOutput": {"permissionDecision": "ask",
                    "permissionDecisionReason": "This reaches past the workspace, so it is execution rather than "
                    "planning. Plan mode widens investigation, not the build. "
                    + grader.reason(grade, verb, target, family, variant)}})
            results.append(invoke("filter-output", event))
            if grading:
                log_bash_decision(runtime, event, results, command, confirmed)
        elif tool == "Agent":
            delegation = selected("delegation", "tiered")
            inputs = event["tool_input"]
            role_name, prompt = inputs.get("subagent_type"), inputs.get("prompt")
            session = event.get("session_id")
            fields = constrained_role(role_name)
            if fields is not None:
                results.append(confinement_deny(runtime, session, role_name, fields, prompt,
                                                "subagent_type"))
            # Refusing the named spawn only moves the work: the same brief comes back with the role
            # name dropped, and nothing sees it. So a spawn is classified by what it carries as well
            # as by what it called itself — a `harness-role:` line, then a declared framework
            # integration's own mapping. A refusal the spawn declared, by role name or marker, is
            # remembered for the session so the next rewording is refused too; a refusal the
            # classifier inferred is not, because a wrong inference remembered is a session that
            # cannot get the corrected brief through. None of this runs where the stance already
            # denies every spawn.
            if delegation != "off":
                if fields is not None:
                    remember_denial(session, role_name, prompt)
                else:
                    marked = marker_role(prompt)
                    if marked is not None:
                        results.append(confinement_deny(runtime, session, marked[0], marked[1],
                                                        prompt, "harness-role marker"))
                        remember_denial(session, marked[0], prompt)
                    else:
                        framed = framework_deny(runtime, session, prompt, role_name)
                        evaded = framed or evasion_deny(runtime, session, prompt)
                        if evaded is not None:
                            results.append(evaded)
                notice = descriptor_notice(session) if enabled("tier-agent-spawns") else None
                if notice is not None:
                    results.append(notice)
            if delegation == "off":
                results.append({"hookSpecificOutput": {"permissionDecision": "deny",
                    "permissionDecisionReason": "Delegation is off; perform the work inline or change the selected stance."}})
            else:
                if runtime == "claude-code":
                    results.append(invoke("tier-agent-spawns", event))
                results.append(invoke("brief-guard", event))
        elif tool == "Workflow":
            results.extend(workflow_results(runtime, event))
        elif tool == "WebFetch":
            results.append(invoke("allow-plan-webfetch", event))
        elif enabled("allow-readonly-bash") and investigating(runtime, event) and plan_allowed_tool(tool):
            results.append({"hookSpecificOutput": {"permissionDecision": "allow",
                "permissionDecisionReason": "Plan-mode research tool named by plan_allow_tools, "
                "run at the permission posture you selected."}})
        return encode_pre(runtime, payload, event, results)
    if kind == "PostToolUse":
        contexts = []
        if tool == "Bash" and enabled("grade-bash"):
            log_bash_outcome(runtime, event)
        if selected("plan-ceremony", "review-card") == "review-card":
            for path in patch_paths(event):
                result = invoke("validate-plan-card", dict(event, tool_input={"file_path": path},
                                                          tool_response={"filePath": path}))
                context = result.get("hookSpecificOutput", {}).get("additionalContext")
                if context:
                    contexts.append(context)
        warning = invoke("neutralize-tool-output", event)
        if warning:
            contexts.append(warning.get("hookSpecificOutput", {}).get("additionalContext") or warning.get("systemMessage", ""))
        if runtime == "claude-code" and tool == "Agent":
            feed = invoke("usage-feed", event).get("hookSpecificOutput", {}).get("additionalContext")
            if feed:
                contexts.append(feed)
        return {"hookSpecificOutput": {"hookEventName": kind, "additionalContext": "\n".join(contexts)}} if any(contexts) else {}
    if kind in FEED_EVENTS:
        # A feed never denies, never blocks and never speaks for another policy, so it answers
        # its own two events alone. The approvals recorder speaks for nothing either: it only
        # keeps the `approve <code>` replies in the user's prompt.
        if runtime != "claude-code":
            return {}
        if kind == "UserPromptSubmit":
            invoke("approvals", event)
        return invoke("usage-feed", event)
    if kind == "SessionStart":
        return invoke("harness-session", event)
    if kind == "Stop":
        return invoke("stop-gate", event)
    if kind == "SessionEnd":
        # Nothing will arrive for this session again, so an ask with no PostToolUse is settled:
        # the command did not run. Done before the usage worker is spawned, and bounded by the
        # session's own rows, so the 1.5-second SessionEnd budget pays for one read of a file
        # that only a permission prompt writes to.
        log = decisions()
        if log is not None:
            log.close_session(event.get("session_id") or "")
        if not enabled("usage-log"):
            return {}
        module = load("usage-log")
        old = sys.stdin
        try:
            sys.stdin = io.StringIO(json.dumps(event))
            module.main([])
        finally:
            sys.stdin = old
    return {}


# The workspace block has a SessionStart entry of its own, because the runtime caps each hook's
# output separately and the start-up block already fills most of one cap.
WORKSPACE_ARG = "workspace"
WORKSPACE_MARKER = "runtime-sessionstart-workspace"


def registration(root, runtime):
    command = "python3 " + shlex.quote(str(root / "adapters" / runtime / "hook.py"))
    hooks = {event: [{"hooks": [{"type": "command", "command": command + " # harness:runtime-" + event.lower(),
                                 "timeout": 300 if event == "Stop" else 2 if event == "SessionEnd" else 10}]}]
             for event in EVENTS.get(runtime, BASE_EVENTS)}
    hooks["SessionStart"].append({"hooks": [{"type": "command", "timeout": 10,
                                             "command": command + " " + WORKSPACE_ARG + " # harness:" + WORKSPACE_MARKER}]})
    return {"hooks": hooks}


def workspace(runtime, payload):
    """The workspace entry's answer: `workspace-session` alone, `{}` on any other event."""
    if runtime not in ("claude-code", "codex") or payload.get("hook_event_name") != "SessionStart":
        return {}
    _SWITCHES.append(switches())
    try:
        return invoke("workspace-session", payload)
    finally:
        _SWITCHES.pop()


def main(runtime, argv=None):
    os.environ["HARNESS_RUNTIME"] = runtime
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == [WORKSPACE_ARG]:
        # A workspace block that fails, bad input included, is left out: it prints `{}` and
        # never speaks for policy or blocks.
        try:
            payload = json.load(sys.stdin)
            result = workspace(runtime, payload) if isinstance(payload, dict) else {}
        except Exception:
            result = {}
        print(json.dumps(result or {}))
        return
    kind = ""
    try:
        payload = json.load(sys.stdin)
        kind = payload.get("hook_event_name", "")
        result = dispatch(runtime, payload)
    except Exception as exc:
        message = "Harness policy is unverified: " + type(exc).__name__ + ": " + str(exc)
        if kind == "PreToolUse":
            result = {"hookSpecificOutput": {"hookEventName": kind, "permissionDecision": "deny", "permissionDecisionReason": message}}
        elif kind == "Stop":
            result = {"decision": "block", "reason": message}
        else:
            result = {"systemMessage": message}
    if result:
        print(json.dumps(result))

#!/usr/bin/env python3
"""What the spawn hooks did to each spawn of a replayed run, read from its saved `-p` stream, and
a launcher that ends a trial once its first wave of spawns is out.

Three readings, each null when the stream cannot answer it, never a guess:

- **Brief budget per spawn** (`spawn_briefs`). Whether the brief a subagent received carried the
  return bound and the soft budget `brief-guard` appends, judged by the patterns the hook itself
  uses (`WORD_CAP_RE` and `BUDGET_RE` in `policy/hooks/rule-detectors.py`). A transcript records
  the brief the model wrote, not the one a hook rewrote (#324), so the received brief is read, in
  order, from the subagent's own first message, then from the `updatedInput` of the PreToolUse
  `hook_response` attributed to the call. The written brief stands in only when the stream shows
  no hook rewrote it, or when the caller says no spawn hook was installed; a bound or budget the
  written brief already states counts either way, since a hook only appends.
- **Workflow launches against the spawn hooks** (`workflow_launch_hooks`). A `Workflow`
  script's `agent()` calls may never reach `tier-agent-spawns` or `brief-guard`. Per launch, the
  PreToolUse and PostToolUse `hook_response` events for a spawn tool inside the launch's window,
  beyond those the window's own spawn calls explain, are the hooks its agents passed.
- **The first wave** (`FirstWave`, `first_wave_launch`). A fan-out task measured for its first
  wave stops once the main thread's first spawning turn is out and each of its spawns has shown
  the model it runs on, or been refused; the task's `max_turns` and the run cap stay the bounds,
  and `ended_by` names which one ended the trial.

`python3 scripts/replay_spawns.py [--no-spawn-hooks] <raw stream>...` prints one JSON row per spawn and per
Workflow launch. Reading and limits: docs/benchmarks.md. Standard library only; nothing here
calls a model.
"""
import importlib.util
import json
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DETECTORS_PATH = ROOT / "policy" / "hooks" / "rule-detectors.py"
# Older Claude Code named the spawn tool `Task`.
SPAWN_TOOLS = ("Agent", "Task")
WORKFLOW_TOOLS = ("Workflow",)
HOOK_EVENTS = ("PreToolUse", "PostToolUse")
# What ended a trial: the first-wave stop, the CLI's own turn or spend bound, a finish, a timeout.
FIRST_WAVE, MAX_TURNS, RUN_CAP, FINISHED, TIMEOUT = "first-wave", "max-turns", "run-cap", "finished", "timeout"
RESULT_BOUNDS = {"error_max_turns": MAX_TURNS, "error_max_budget_usd": RUN_CAP, "success": FINISHED}
# Every field a replay row gains from this module, unknown until a stream says otherwise.
ROW_DEFAULTS = {"spawn_briefs": None, "workflow_launch_hooks": None, "first_wave_spawns": None,
                "ended_by": None, "required_skills_loaded": None}
_PATTERNS = []


def load_patterns(path=DETECTORS_PATH):
    """`(bound pattern, budget pattern)` from the rule detectors, or `(None, None)` when they will
    not load, which leaves every brief's reading unknown rather than judged by a second copy."""
    if not _PATTERNS or path != DETECTORS_PATH:
        try:
            spec = importlib.util.spec_from_file_location("replay_spawns_detectors", str(path))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            found = (getattr(module, "WORD_CAP_RE", None), getattr(module, "BUDGET_RE", None))
        except Exception:
            found = (None, None)
        if path != DETECTORS_PATH:
            return found
        _PATTERNS.append(found)
    return _PATTERNS[0]


def _dicts(messages):
    return [m for m in messages or () if isinstance(m, dict)]


def _tool_uses(message):
    if message.get("type") != "assistant":
        return []
    content = (message.get("message") or {}).get("content")
    return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"] \
        if isinstance(content, list) else []


def _tool_results(message):
    content = (message.get("message") or {}).get("content")
    return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"] \
        if isinstance(content, list) else []


def _user_text(message):
    """A user message's text when it is a prompt rather than tool results, else None."""
    if message.get("type") != "user":
        return None
    content = (message.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list) or not content:
        return None
    if any(not isinstance(b, dict) or b.get("type") != "text" for b in content):
        return None
    return "".join(str(b.get("text") or "") for b in content)


def hook_tool(event):
    """The tool a PreToolUse or PostToolUse `hook_response` ran for, from its `hook_name`
    (`PreToolUse:Agent`), or its `tool_name` when the event carries one; None otherwise."""
    if event.get("type") != "system" or event.get("subtype") != "hook_response" \
            or event.get("hook_event") not in HOOK_EVENTS:
        return None
    if isinstance(event.get("tool_name"), str):
        return event["tool_name"]
    name = event.get("hook_name")
    return name.split(":", 1)[1] if isinstance(name, str) and ":" in name else None


def updated_prompt(event):
    """`(read, prompt)`: whether the hook's stdout parsed, and the brief its `updatedInput` set,
    or None when it set none."""
    for key in ("stdout", "output"):
        text = str(event.get(key) or "").strip()
        if not text:
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        specific = data.get("hookSpecificOutput")
        updated = specific.get("updatedInput") if isinstance(specific, dict) else None
        prompt = updated.get("prompt") if isinstance(updated, dict) else None
        return True, prompt if isinstance(prompt, str) else None
    # Empty stdout is a hook that changed nothing; anything unparsed is unknown.
    return (not any(str(event.get(k) or "").strip() for k in ("stdout", "output"))), None


def _spawn_hooks(messages, calls):
    """`{call id: PreToolUse hook_response}`. An event naming its `tool_use_id` is matched to it;
    otherwise the spawn-tool events are matched to the spawn calls in order, and only when the
    two counts agree, since a stray event would shift every match after it."""
    events = [m for m in messages if hook_tool(m) in SPAWN_TOOLS and m.get("hook_event") == "PreToolUse"]
    ids = [c["id"] for c in calls]
    named = {e["tool_use_id"]: e for e in events if isinstance(e.get("tool_use_id"), str)}
    if named:
        return {k: v for k, v in named.items() if k in ids}
    return dict(zip(ids, events)) if len(events) == len(ids) else {}


def _judge(text, pattern):
    return None if pattern is None or not isinstance(text, str) else bool(pattern.search(text))


def spawn_briefs(messages, patterns=None, hooks=None):
    """One row per spawn call in the stream, in order: `{index, thread, tool, requested_type,
    requested_model, model, brief_source, brief_bound, brief_budget}`.

    `thread` is None for a main-thread spawn. `model` is the first model the spawn's own thread
    reported. `brief_source` is where the received brief was read: `thread`, `hook`, `written`,
    or None when it could not be read, in which case a bound or budget the written brief lacks
    is None. `hooks=False` says the arm installs no spawn hook, so the written brief is the one
    received. No brief text reaches a row."""
    messages = _dicts(messages)
    bound_re, budget_re = patterns if patterns is not None else load_patterns()
    calls = []
    for message in messages:
        for block in _tool_uses(message):
            if block.get("name") in SPAWN_TOOLS:
                given = block.get("input") if isinstance(block.get("input"), dict) else {}
                calls.append({"id": block.get("id"), "thread": message.get("parent_tool_use_id"),
                              "tool": block.get("name"), "input": given})
    models, briefs = {}, {}
    for message in messages:
        thread = message.get("parent_tool_use_id")
        if not isinstance(thread, str):
            continue
        body = message.get("message") or {}
        if message.get("type") == "assistant" and thread not in models and body.get("model"):
            models[thread] = body["model"]
        text = _user_text(message)
        if text is not None and thread not in briefs:
            briefs[thread] = text
    hooked = _spawn_hooks(messages, calls)
    streamed_hooks = any(m.get("type") == "system" and m.get("subtype") == "hook_response" for m in messages)
    spawn_hooked = any(hook_tool(m) in SPAWN_TOOLS for m in messages)
    rows = []
    for index, call in enumerate(calls):
        written = call["input"].get("prompt")
        received, source = None, None
        if isinstance(written, str):
            if call["id"] in briefs:
                received, source = briefs[call["id"]], "thread"
            elif call["id"] in hooked:
                read, prompt = updated_prompt(hooked[call["id"]])
                if prompt is not None:
                    received, source = prompt, "hook"
                elif read:
                    received, source = written, "written"
            elif hooks is False or (hooks is None and streamed_hooks and not spawn_hooked):
                # No spawn hook ran on any call of a stream that carries hook events: unchanged.
                received, source = written, "written"
        judged = {}
        for field, pattern in (("brief_bound", bound_re), ("brief_budget", budget_re)):
            value = _judge(received, pattern)
            if value is None and _judge(written, pattern):
                value = True  # a hook only appends, so what the model wrote survives
            judged[field] = value
        rows.append(dict({"index": index, "thread": call["thread"], "tool": call["tool"],
                          "requested_type": call["input"].get("subagent_type"),
                          "requested_model": call["input"].get("model"),
                          "model": models.get(call["id"]), "brief_source": source}, **judged))
    return rows


def workflow_launch_hooks(messages):
    """One row per `Workflow` launch, in order: `{index, pre, post, activity, passed}`.

    `pre` and `post` are the spawn-tool PreToolUse and PostToolUse `hook_response` events inside
    the launch's window, from its call to its result, beyond the window's own spawn calls; an
    event naming a `tool_use_id` counts only when that id is no spawn call of the stream.
    `activity` is whether the stream shows anything the launch ran. `passed` is True when its
    agents met a spawn hook, False when the launch finished, ran something, and met none, and
    None when the stream carries no hook events, the launch never finished, ran nothing visible,
    or the window's own spawn calls leave its events unattributable."""
    messages = _dicts(messages)
    streamed_hooks = any(m.get("type") == "system" and m.get("subtype") == "hook_response" for m in messages)
    spawn_ids = {b.get("id") for m in messages for b in _tool_uses(m) if b.get("name") in SPAWN_TOOLS}
    rows = []
    for start, message in enumerate(messages):
        for block in _tool_uses(message):
            if block.get("name") not in WORKFLOW_TOOLS:
                continue
            launch = block.get("id")
            end = next((i for i in range(start + 1, len(messages))
                        if any(r.get("tool_use_id") == launch for r in _tool_results(messages[i]))), None)
            window = messages[start + 1:end] if end is not None else messages[start + 1:]
            activity = any(m.get("parent_tool_use_id") == launch or
                           (m.get("type") == "system" and m.get("tool_use_id") == launch) for m in window)
            own_calls = sum(1 for m in window for b in _tool_uses(m) if b.get("name") in SPAWN_TOOLS)
            counts, named = {}, False
            for event in HOOK_EVENTS:
                found = [m for m in window if hook_tool(m) in SPAWN_TOOLS and m.get("hook_event") == event]
                if any(isinstance(m.get("tool_use_id"), str) for m in found):
                    named = True
                    counts[event] = sum(1 for m in found if m.get("tool_use_id") not in spawn_ids)
                else:
                    counts[event] = len(found) - own_calls if len(found) >= own_calls else None
            if not streamed_hooks or end is None or None in counts.values():
                passed = None
            elif any(counts.values()):
                passed = True
            else:
                # Spawn calls of its own in the window and no ids: their events and the launch's
                # cannot be told apart, so none left over is not proof that none was the launch's.
                passed = False if activity and (named or not own_calls) else None
            rows.append({"index": len(rows), "pre": counts["PreToolUse"], "post": counts["PostToolUse"],
                         "activity": activity, "passed": passed})
    return rows


def ended_by(messages, stopped=False, timed_out=False):
    """Which bound ended a trial: `first-wave` when the launcher stopped it, `timeout`, then the
    CLI's own result (`max-turns`, `run-cap`, `finished`), its subtype when it is another, or None
    when the stream holds no result."""
    if stopped:
        return FIRST_WAVE
    if timed_out:
        return TIMEOUT
    results = [m for m in _dicts(messages) if m.get("type") == "result"]
    if not results:
        return None
    subtype = results[-1].get("subtype")
    return RESULT_BOUNDS.get(subtype, subtype if isinstance(subtype, str) else None)


def skills_loaded(messages, names):
    """Whether the first `init` event listed every skill in `names`; None with no names, no init
    event, or no skills list in it."""
    if not names:
        return None
    init = next((m for m in _dicts(messages) if m.get("type") == "system" and m.get("subtype") == "init"), None)
    skills = init.get("skills") if init else None
    if not isinstance(skills, list):
        return None
    listed = {s if isinstance(s, str) else (s or {}).get("name") for s in skills if isinstance(s, (str, dict))}
    return all(name in listed for name in names)


class FirstWave:
    """Fed a stream one message at a time; `done` once the first wave of spawns is out.

    The wave is the spawn and `Workflow` calls of the main thread's first assistant turn that
    makes any: every main-thread message with that turn's message id, since the CLI streams each
    content block of a turn as its own message. It is out when each spawn's thread has reported
    the model it runs on, each `Workflow` launch has shown any activity, or a call has its result,
    which a refused spawn gets at once."""

    def __init__(self):
        self.turn, self.calls, self.workflows, self.seen, self.done = None, [], set(), set(), False

    def _take(self, message):
        for block in _tool_uses(message):
            if block.get("name") in SPAWN_TOOLS + WORKFLOW_TOOLS:
                self.calls.append(block.get("id"))
                if block.get("name") in WORKFLOW_TOOLS:
                    self.workflows.add(block.get("id"))

    def feed(self, message):
        if self.done or not isinstance(message, dict):
            return self.done
        main = message.get("parent_tool_use_id") is None
        turn = (message.get("message") or {}).get("id") if message.get("type") == "assistant" else None
        if self.turn is None:
            if main and any(b.get("name") in SPAWN_TOOLS + WORKFLOW_TOOLS for b in _tool_uses(message)):
                self.turn = turn or object()
                self._take(message)
            return False
        if main and turn is not None and turn == self.turn:
            self._take(message)
        thread = message.get("parent_tool_use_id")
        if thread in self.calls:
            if thread in self.workflows or (message.get("type") == "assistant"
                                            and (message.get("message") or {}).get("model")):
                self.seen.add(thread)
        if message.get("type") == "system" and message.get("tool_use_id") in self.workflows:
            self.seen.add(message["tool_use_id"])
        for result in _tool_results(message):
            if result.get("tool_use_id") in self.calls:
                self.seen.add(result["tool_use_id"])
        self.done = bool(self.calls) and all(call in self.seen for call in self.calls)
        return self.done

    def feed_line(self, line):
        try:
            message = json.loads(line)
        except ValueError:
            return self.done
        return self.feed(message)


def _container(command):
    return command[command.index("--name") + 1] if "--name" in command[:-1] else None


def first_wave_launch(base=subprocess.run, popen=subprocess.Popen, stop=None):
    """A `launch` with `subprocess.run`'s signature for one first-wave trial.

    A `docker run` is read line by line; once `FirstWave` says the wave is out, the container is
    stopped by name (`stop(name)`, `docker kill` by default) and its output up to then is
    returned with `first_wave_stopped` set on the completed process. Every other command, the
    removal of a kept container included, goes to `base` unchanged. A timeout stops the container
    too and raises `TimeoutExpired` carrying the partial output, as `subprocess.run` would."""
    stop = stop or (lambda name: ["docker", "kill", name])

    def launch(command, timeout=None, **kwargs):
        if list(command[:2]) != ["docker", "run"]:
            return base(command, timeout=timeout, **kwargs)
        for key in ("stdout", "stderr", "universal_newlines", "text"):
            kwargs.pop(key, None)
        name, env = _container(command), kwargs.get("env")
        proc = popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, **kwargs)
        errors, expired = [], []

        def halt():
            if name:
                base(stop(name), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            proc.kill()

        def expire():
            expired.append(True)
            halt()

        reader = threading.Thread(target=lambda: errors.append(proc.stderr.read()))
        reader.daemon = True
        reader.start()
        timer = threading.Timer(timeout, expire) if timeout else None
        if timer:
            timer.daemon = True
            timer.start()
        watcher, lines, stopped = FirstWave(), [], False
        try:
            for line in proc.stdout:
                lines.append(line)
                if not stopped and watcher.feed_line(line):
                    stopped = True
                    halt()
            proc.wait()
        finally:
            if timer:
                timer.cancel()
            reader.join(5)
        out, err = "".join(lines), "".join(e for e in errors if e)
        if expired and not stopped:
            raise subprocess.TimeoutExpired(command, timeout, output=out, stderr=err)
        done = subprocess.CompletedProcess(command, proc.returncode, out, err)
        done.first_wave_stopped = stopped
        done.first_wave_spawns = len(watcher.calls) if watcher.turn is not None else 0
        return done

    return launch


def row_fields(messages, task, stopped=False, timed_out=False, hooks=None):
    """The fields a replay row gains from its stream: `spawn_briefs`, `workflow_launch_hooks`,
    `ended_by`, `required_skills_loaded`, and, for a first-wave task, `first_wave_spawns`."""
    messages = _dicts(messages)
    fields = dict(ROW_DEFAULTS)
    if not messages:
        return fields
    fields.update(spawn_briefs=spawn_briefs(messages, hooks=hooks),
                  workflow_launch_hooks=workflow_launch_hooks(messages),
                  ended_by=ended_by(messages, stopped, timed_out),
                  required_skills_loaded=skills_loaded(messages, task.get("requires_skills")))
    if task.get("first_wave"):
        watcher = FirstWave()
        for message in messages:
            watcher.feed(message)
        fields["first_wave_spawns"] = len(watcher.calls)
    return fields


def parse_stream(text):
    """The messages of one stream's text: one JSON message per line, or one JSON document."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        data = None
    if isinstance(data, (list, dict)):
        return _dicts(data if isinstance(data, list) else [data])
    out = []
    for line in (text or "").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return _dicts(out)


def read_stream(path):
    """The messages of one saved stream (`parse_stream`)."""
    return parse_stream(Path(path).read_text(encoding="utf-8", errors="replace"))


def main(argv=None):
    """Print one JSON row per spawn and per `Workflow` launch of each saved stream named.
    `--no-spawn-hooks` says the streams come from an arm with no spawn hook, the bare arm."""
    paths = list(sys.argv[1:] if argv is None else argv)
    hooks = None
    if "--no-spawn-hooks" in paths:
        paths.remove("--no-spawn-hooks")
        hooks = False
    if not paths:
        print("usage: replay_spawns.py [--no-spawn-hooks] <raw stream>...", file=sys.stderr)
        return 2
    for path in paths:
        messages = read_stream(path)
        for row in spawn_briefs(messages, hooks=hooks):
            print(json.dumps(dict(row, stream=Path(path).name, kind="spawn"), sort_keys=True))
        for row in workflow_launch_hooks(messages):
            print(json.dumps(dict(row, stream=Path(path).name, kind="workflow"), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

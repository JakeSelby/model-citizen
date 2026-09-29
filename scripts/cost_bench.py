#!/usr/bin/env python3
"""Measure what the harness costs against Claude Code with no harness at all.

`static` counts, without calling a model, what the harness adds to every session. A bare session
loads none of the files counted, so the total is the harness's standing overhead. Tokens are an
estimate from characters (`CHARS_PER_TOKEN`), good for a trend between versions and not for
billing; dollars come from `policy/prices.json`. Codex is not counted: its instructions are
rendered at sync time.

`replay` runs pinned tasks headlessly in two fresh containers, a bare arm and the harness at a
pinned git ref of this repository, one history row per `--tag`. Nothing from the machine running
it reaches either arm (`replay_arms.py`). It reads cost from the CLI's own JSON result and scores
each run with a held-back check, itself run in a fresh container. It calls a model and spends real
usage. `arms` builds and checks the arm images without calling a model. Reading and limits:
docs/benchmarks.md.
"""
import argparse
import datetime
import hashlib
import importlib.util
import itertools
import json
import os
import platform
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from harness_core import cache_prefix  # noqa: E402  the ledger's miss ratio, one definition
from harness_core import catalog  # noqa: E402  the resolver the hooks load, for the profile fingerprint
from harness_core import observation  # noqa: E402  the shared observer registration
from harness_core import observer  # noqa: E402  benchmark-owned destination variables
sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_arms as arms  # noqa: E402  the containers every arm and every check runs in
import experiment_protocol  # noqa: E402  the pre-registration gate; docs/evidence-standard.md
import replay_stats  # noqa: E402  SM-2's analysis of the saved rows
import delegation_verdict  # noqa: E402  whether the delegation stance fired, per task (#429)

CHARS_PER_TOKEN = 4.0
GROWTH_LIMIT = 0.05
# What each counted group covers, carried in the written figure so a reader of the file alone
# knows which set a number is over. The caps `harness lint` prints are a narrower set.
SCOPES = {
    "always_loaded": "claude/CLAUDE.md, claude/rules/, claude/output-styles/ and the stance "
                     "variant config.example.json selects, for the default selection",
    "listings": "one description line per agent, skill and command the session lists",
    "worst_case_est_tokens": "the same files with the longest variant of every stance dimension",
    "files": "each always-loaded and listed file on its own, the same set `total` sums; its "
             "est_tokens are rounded per file, so they sum to the total within rounding",
    "note": "`harness lint` counts a narrower set against its caps: instructions, rules and the "
            "longest stance variant, with no output style and no listings",
}
STATIC = Path("benchmarks") / "static.json"
ALLOW = Path("benchmarks") / "allow.json"
TASKS = Path("benchmarks") / "tasks.json"
ORACLES = Path("benchmarks") / "oracles"
HISTORY = Path("benchmarks") / "history.jsonl"
HISTORY_MD = Path("benchmarks") / "history.md"
ARMS = arms.ARMS
SNAPSHOT_BRANCH = "main"
# This repository's own gate, as AGENTS.md names it: a snapshot must pass it before any arm runs.
# Each command runs in a container of the bare arm, where `python3` is the image's own.
GATE_COMMANDS = (["python3", "bin/harness", "lint"], ["python3", "-m", "unittest", "discover", "-s", "tests"])
RUN_CAP_USD = 2.0
PREFLIGHT_CAP_USD = 0.25
DEFAULT_REPS = 5  # SM-2: five or more trials per task and arm
# One full default set, 7 tasks x 5 trials x 2 arms at the per-run cap, plus one preflight per arm.
SPEND_CAP_USD = 7 * DEFAULT_REPS * 2 * RUN_CAP_USD + 2 * PREFLIGHT_CAP_USD
THRESHOLD = 0.85
RUN_TIMEOUT = 1800
CHECK_TIMEOUT = 900
KEPT_ENV = ("HOME", "USER", "PATH", "TERM")
TOKEN_KINDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
MODEL_USAGE_KEYS = ("inputTokens", "outputTokens", "cacheCreationInputTokens", "cacheReadInputTokens")
# The web tools run in the CLI's own process and would reach the web through the egress proxy's
# refusal only as an error, so both arms are denied them outright; a deny rule outranks any allow
# the harness arm's settings carry.
NO_WEB = ("WebFetch", "WebSearch")
OBSERVER_COMMAND = arms.OBSERVER_COMMAND
ARM_SETTINGS = arms.observer_settings()
# The container is the fence: no host path but the snapshot, no way out but the model API. Inside
# it the agent acts without prompts, as a headless run cannot answer one.
PERMISSION_MODE = "bypassPermissions"
# The gate this repository's AGENTS.md names, run inside the fence and reported as its own last line.
# The suite's own stdout is block-buffered under a pipe and lands after unittest's stderr summary,
# so the last line of `2>&1` is noise, not the verdict. Filter to the verdict lines and judge the
# tool's output directly rather than whatever the model chose to relay.
# The bar the prompts set, and no more: lint inside the arm's own container. The full suite is
# profile-dependent at every snapshot commit (`claude_dir()` lets CLAUDE_CONFIG_DIR override the
# tests' isolation), so demanding it here measures the profile, not the harness.
PREFLIGHT_PROMPT = "Run exactly this and reply with its output: `python3 bin/harness lint`"
PREFLIGHT_TURNS = 3
PREFLIGHT_RED = re.compile(r"PermissionError|Operation not permitted", re.M)
INHERITED = "inherited"
# What a profile directory loads, for `backfill` of rows from before arms were containers.
CONFIG_GLOBS = ("CLAUDE.md", "CLAUDE.personal.md", "rules/**/*.md", "skills/*/SKILL.md",
                "agents/*.md", "output-styles/*.md")
SPAWN_TOOLS = ("Task", "Agent")
# Absorbable calls and Workflow launches, one definition each: `delegation_verdict`.
GATHER_TOOLS = delegation_verdict.GATHER_TOOLS
WORKFLOW_TOOLS = delegation_verdict.WORKFLOW_TOOLS
# The loaded surface: the CLI's own `init` event, counted. Each list's length becomes the row's
# `init_<key>`, so two runs of one arm can be compared on what their sessions loaded.
SURFACE_KEYS = ("skills", "agents", "slash_commands", "tools", "mcp_servers", "memory_paths")
SURFACE_FIELDS = tuple("init_" + key for key in SURFACE_KEYS)
SURFACE_HASH_FIELDS = tuple(field + "_sha256" for field in SURFACE_FIELDS)
SURFACE_SOURCE = "cli-init"
SURFACE_SOURCE_FIELD = "init_surface_source"
# Diagnostic fields `parse_result` reads out of the stream; `backfill` derives the same ones.
STREAM_FIELDS = ("first_call_cache_write", "first_call_context", "tool_counts", "spawns", "spawn_offered",
                 "gather_calls", "absorbed_calls", "workflow_launches", "stop_hooks", "hook_blocks",
                 "cache_miss_ratio", "installed_checkout_reads",
                 "observed_effort", SURFACE_SOURCE_FIELD) \
                + SURFACE_FIELDS + SURFACE_HASH_FIELDS
INSTALLED_CHECKOUT = "/opt/model-citizen"
CONTAMINATION_CONTROL = "installed-checkout-oracle-and-transcript-v1"
RESULTS = "results.jsonl"
ENRICHED = "results.enriched.jsonl"


def _description(path):
    """The frontmatter `description`, folded lines included; the part a session lists unasked."""
    lines = path.read_text(encoding="utf-8").splitlines()
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
    return " ".join(part for part in out if part not in (">", "|", ">-", "|-"))


def _group(root, paths, text=None):
    rows = []
    for path in paths:
        body = text(path) if text else path.read_text(encoding="utf-8")
        rows.append({"path": path.relative_to(root).as_posix(), "lines": body.count("\n") + bool(body),
                     "chars": len(body)})
    return rows


def _sum(rows):
    chars = sum(row["chars"] for row in rows)
    return {"files": len(rows), "lines": sum(row["lines"] for row in rows), "chars": chars,
            "est_tokens": int(round(chars / CHARS_PER_TOKEN))}


def _variants(root, selected):
    """One file per stance dimension: the selected variant, or the longest when none is named."""
    out = []
    stances = root / "claude" / "stances"
    for folder in sorted(p for p in stances.iterdir() if p.is_dir()) if stances.is_dir() else []:
        options = sorted(folder.glob("*.md"))
        if not options:
            continue
        named = folder / (str((selected or {}).get(folder.name)) + ".md")
        out.append(named if selected is not None and named in options
                   else max(options, key=lambda p: (len(p.read_text(encoding="utf-8")), p.name)))
    return out


def measure(root=ROOT):
    """The static figure for one checkout: default selection, worst case, listings and dollars."""
    claude = root / "claude"
    example = root / "config.example.json"
    selected = json.loads(example.read_text(encoding="utf-8")).get("stances", {}) if example.is_file() else {}
    fixed = [p for p in [claude / "CLAUDE.md"] if p.is_file()]
    fixed += sorted((claude / "rules").glob("*.md")) + sorted((claude / "output-styles").glob("*.md"))
    always = _group(root, fixed + _variants(root, selected))
    worst = _group(root, fixed + _variants(root, None))
    listed = sorted((claude / "agents").glob("*.md")) + sorted((claude / "skills").glob("*/SKILL.md"))
    listed += sorted((claude / "commands").glob("*.md"))
    listings = _group(root, listed, _description)
    total = _sum(always + listings)
    version = root / "VERSION"
    models = _cache_rates(root)
    files = {}
    for group, rows in (("always_loaded", always), ("listings", listings)):
        for row in rows:
            tokens = row["chars"] / CHARS_PER_TOKEN
            files[row["path"]] = {"group": group, "chars": row["chars"],
                                  "est_tokens": int(round(tokens)), "usd": price(root, tokens, models)}
    return {
        "schema_version": 2,
        "harness_version": version.read_text(encoding="utf-8").strip() if version.is_file() else "",
        "chars_per_token": CHARS_PER_TOKEN,
        "scopes": SCOPES,
        "always_loaded": _sum(always),
        "listings": _sum(listings),
        "total": total,
        "worst_case_est_tokens": _sum(worst + listings)["est_tokens"],
        "largest": sorted(always + listings, key=lambda r: (-r["chars"], r["path"]))[:5],
        "usd": price(root, total["est_tokens"], models),
        "files": files,
    }


def _cache_rates(root):
    """The models the static figure is priced on: Anthropic ones with both cache rates."""
    table = root / "policy" / "prices.json"
    models = json.loads(table.read_text(encoding="utf-8")).get("models", {}) if table.is_file() else {}
    return {name: row for name, row in sorted(models.items())
            if name.startswith("claude-") and "cache_write" in row and "cache_read" in row}


def price(root, tokens, models=None):
    """USD the counted layer costs per model: written to the cache once, then read every turn."""
    models = _cache_rates(root) if models is None else models
    return {name: {"session_start": round(tokens * row["cache_write"] / 1e6, 6),
                   "later_turn": round(tokens * row["cache_read"] / 1e6, 6)}
            for name, row in sorted(models.items())}


def file_deltas(root=ROOT, now=None):
    """One line per file whose estimate moved since the committed figure, largest move first.

    Dollars are priced on the model with the highest cache-read rate, named on each line, so a
    line states the most a change can cost per turn rather than an average over models.
    """
    committed = root / STATIC
    if not committed.is_file():
        return []
    before = json.loads(committed.read_text(encoding="utf-8")).get("files")
    if before is None:
        return ["%s has no per-file figures; run `scripts/cost_bench.py static --write` at the next "
                "release to record them" % STATIC.as_posix()]
    now = measure(root) if now is None else now
    after = now["files"]
    moved = []
    for path in sorted(set(before) | set(after)):
        delta = (after.get(path, {}).get("est_tokens", 0)
                 - before.get(path, {}).get("est_tokens", 0))
        if delta:
            moved.append((path, delta))
    if not moved:
        return []
    models = _cache_rates(root)
    name = max(models, key=lambda m: (models[m]["cache_read"], m)) if models else ""
    lines = []
    for path, delta in sorted(moved, key=lambda item: (-abs(item[1]), item[0])):
        state = " (new)" if path not in before else " (removed)" if path not in after else ""
        line = "%s%s %+d tokens" % (path, state, delta)
        if name:
            row = models[name]
            line += ", %+.6f USD per session start, %+.6f USD per later turn on %s" % (
                delta * row["cache_write"] / 1e6, delta * row["cache_read"] / 1e6, name)
        lines.append(line)
    return lines


def check(root=ROOT, now=None):
    """Errors when the estimate has grown past the limit over the committed figure, unexplained."""
    committed = root / STATIC
    if not committed.is_file():
        return ["%s is missing; run `scripts/cost_bench.py static --write`" % STATIC.as_posix()]
    before = json.loads(committed.read_text(encoding="utf-8"))["total"]["est_tokens"]
    now = measure(root) if now is None else now
    after = now["total"]["est_tokens"]
    if after <= before * (1 + GROWTH_LIMIT):
        return []
    allow = root / ALLOW
    entries = json.loads(allow.read_text(encoding="utf-8")) if allow.is_file() else []
    if any(e.get("harness_version") == now["harness_version"] and e.get("est_tokens") == after
           and str(e.get("reason", "")).strip() for e in entries):
        return []
    return ["static context grew from %d to %d estimated tokens (+%.1f%%, limit %.0f%%); trim it, or "
            "record harness_version, est_tokens and a reason in %s"
            % (before, after, (after / before - 1) * 100 if before else 100.0, GROWTH_LIMIT * 100,
               ALLOW.as_posix())]


# --------------------------------------------------------------------------- replay


def load_tasks(path):
    """The manifest's tasks, or SystemExit naming the first malformed one."""
    tasks = json.loads(Path(path).read_text(encoding="utf-8"))["tasks"]
    for task in tasks:
        missing = [k for k in ("id", "kind", "parent_sha", "good_sha", "prompt", "tests", "max_turns")
                   if k not in task]
        if missing or task["kind"] not in ("issue", "synthetic"):
            raise SystemExit("task %r is malformed: missing %s" % (task.get("id"), missing or "a known kind"))
        if not isinstance(task.get("long", False), bool):
            raise SystemExit("task %r is malformed: long must be true or false" % task.get("id"))
    return tasks


def prompt_of(task):
    return "\n".join(task["prompt"]) if isinstance(task["prompt"], list) else task["prompt"]


def scrubbed_env(extra=None, base=None):
    """A shell inside an agent session carries that session's variables; git and the checks get none of them."""
    base = os.environ if base is None else base
    env = {name: base[name] for name in KEPT_ENV if name in base}
    env.setdefault("TERM", "dumb")
    env.update(extra or {})
    return env


def arm_env(arm, stance_cost=None, proxy=None, observation_run=None):
    """The variables one arm's container is given by value (`replay_arms.arm_env`): the same for
    both arms, bar the harness arm's stance override. The container's HOME is the image's own,
    and the credential goes by name alone, so neither is here."""
    env = arms.arm_env(proxy, stance_cost if arm != "bare" else None)
    if observation_run:
        env.update({observer.LEDGER_ENV: observation_run["container_ledger"],
                    observer.ERRORS_ENV: observation_run["container_errors"]})
        if observation_run["profile"] is not None:
            env[observer.PROFILE_ENV] = observation_run["profile"]
    return env


def prepare_observation_dir(out):
    """Create this replay tag's new run-owned observation directory before a launch."""
    root = Path(out).resolve()
    target = root / "observations"
    root.mkdir(parents=True, exist_ok=True)
    try:  # one atomic create, so a concurrent run with the same tag is refused the same way
        target.mkdir(mode=0o700)
    except FileExistsError:
        raise SystemExit("cost-bench: refusing existing observation output %s" % target)
    (target / arms.OBSERVATION_MARKER).write_text("cost-bench\n", encoding="utf-8")
    return target


def observation_stem(name):
    """A file-safe stem for one session name, injective: a name that needed rewriting carries
    a hash of the original, so `a/b` and `a-b` never share observation files."""
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "-", name)
    if safe == name:
        return safe
    return "%s-%s" % (safe, hashlib.sha256(name.encode("utf-8")).hexdigest()[:10])


def refuse_observation_collisions(tasks, opts):
    """Refuse, before any probe or model call, a plan whose sessions would share a stem."""
    if not opts.get("observation_dir"):
        return
    names = ["preflight-%s" % arm for arm in ARMS]
    names += ["%s-%s-%d" % (task["id"], arm, rep) for task, rep, arm in schedule(tasks, opts["reps"])]
    seen = {}
    for name in names:
        stem = observation_stem(name)
        if stem in seen:
            raise SystemExit("cost-bench: refusing the replay: sessions %r and %r share observation files %s"
                             % (seen[stem], name, stem))
        seen[stem] = name


def discard_observation(run):
    """Remove one session's staging directory; its retained copy is already in the archive."""
    if run:
        shutil.rmtree(str(run["private"]), ignore_errors=True)


def observation_run(opts, name, profile):
    """The fresh host and container files for one real native session."""
    root = opts.get("observation_dir")
    if not root:
        return None
    stem = observation_stem(name)
    archive = Path(root)
    if any((archive / (stem + suffix)).exists() for suffix in (".jsonl", ".errors.jsonl")):
        raise SystemExit("cost-bench: refusing existing observation files for %s" % stem)
    try:
        (archive / (stem + ".reserved")).touch(exist_ok=False)
    except FileExistsError:
        raise SystemExit("cost-bench: refusing repeated observation session %s" % stem)
    # Only this session's empty output directory enters the container. The retained result
    # directory may be in the host checkout, but no arm can read or alter it or another run.
    # The stage is open to every user so the image's user can write it whatever its uid, as
    # `replay_arms.open_for_image` does for the snapshot; it sits inside a private (0o700)
    # parent, so no other host user can reach it, and `discard_observation` removes both.
    private = Path(tempfile.mkdtemp(prefix="cost-observation-", dir=opts.get("tmp")))
    stage = private / "out"
    stage.mkdir()
    (stage / arms.OBSERVATION_MARKER).write_text("cost-bench\n", encoding="utf-8")
    os.chmod(str(stage), 0o777)
    ledger, errors = stage / (stem + ".jsonl"), stage / (stem + ".errors.jsonl")
    for path in (ledger, errors):
        path.touch(mode=0o666, exist_ok=False)
        os.chmod(str(path), 0o666)
    return {"ledger": ledger, "errors": errors, "archive": archive, "mount": stage,
            "private": private, "relative": "observations/" + ledger.name,
            "container_ledger": arms.OBSERVATION_MOUNT + "/" + ledger.name,
            "container_errors": arms.OBSERVATION_MOUNT + "/" + errors.name,
            "profile": profile}


def observation_result(run):
    """Validate and retain each native stream independently, including collector failures."""
    if not run:
        return {"observation_ledger": None, "observation_rows": None,
                "observation_errors": None}, None
    counts, problems = {}, []
    # The container has exited: close the stage before reading, then accept only regular files
    # still owned by this user, which the observer's append into the pre-created file keeps.
    os.chmod(str(run["mount"]), 0o700)
    for key in ("ledger", "errors"):
        path = run[key]
        try:
            info = os.lstat(str(path))
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("collector output is not a regular file")
            if info.st_uid != os.getuid():
                raise ValueError("collector output is not owned by the invoking user")
            raw = path.read_bytes()
            # Retain malformed output as evidence too; validation never rewrites its contents.
            with (run["archive"] / path.name).open("xb") as archive:
                archive.write(raw)
            lines = raw.decode("utf-8").splitlines()
            for line in lines:
                row = json.loads(line)
                valid = isinstance(row, dict) and row.get("runtime") == "claude-code" \
                    and isinstance(row.get("ts"), str) and bool(row["ts"])
                if key == "ledger":
                    valid = valid and row.get("schema_version") == observer.SCHEMA_VERSION \
                        and type(row.get("schema_version")) is int \
                        and row.get("event") in observation.events("claude-code") \
                        and "session_id" in row \
                        and (row["session_id"] is None or isinstance(row["session_id"], str)) \
                        and "profile_fingerprint" in row \
                        and row["profile_fingerprint"] == run["profile"]
                else:
                    valid = valid and isinstance(row.get("error"), str) and bool(row["error"]) \
                        and isinstance(row.get("detail"), str)
                if not valid:
                    raise ValueError("invalid observer row")
            counts[key] = len(lines)
        except (OSError, ValueError, UnicodeError) as exc:
            counts[key] = None
            problems.append("observation: unreadable %s output (%s)" % (key, type(exc).__name__))
    if counts["errors"]:
        problems.append("observation: %d collector error(s)" % counts["errors"])
    if counts["ledger"] == 0:
        problems.append("observation: no native events recorded")
    return {"observation_ledger": run["relative"], "observation_rows": counts["ledger"],
            "observation_errors": counts["errors"]}, "; ".join(problems) or None


def arm_profile(arm, env, opts):
    """The profile fingerprint a row of this arm carries: `bare`, or the harness arm's profile.

    The bare arm loads no harness, so it has no profile to digest and says so by name rather than
    by a null a reader would take for a row from before the field. The harness arm's is resolved
    by this checkout's resolver over a clone of the commit the arm's image was built from, with
    `home` an empty directory standing in for the image's, which holds no configuration of the
    user's either. The arm's manifest digest is the complete record; this is the per-rule view.
    None when it cannot be resolved."""
    try:
        module = catalog.posture_module(ROOT)
        if arm == "bare":
            return module.BARE_FINGERPRINT
        home = opts.get("home")
        return module.fingerprint(dict(env, HOME=str(home)) if home else env,
                                  root=Path(opts.get("profile_root") or ROOT))
    except Exception:
        return None


def arm_attribution(arm, env, opts):
    """The per-module context tokens a row of this arm carries, resolved as `arm_profile` resolves
    the arm's fingerprint: a soft estimate with its method, and no module at all for the bare arm.
    None when it cannot be resolved."""
    try:
        module = catalog.posture_module(ROOT)
        if arm == "bare":
            return {"estimand": module.SOFT_ESTIMATE, "method": module.ATTRIBUTION_METHOD, "modules": {}}
        home = opts.get("home")
        return module.context_attribution(dict(env, HOME=str(home)) if home else env,
                                          root=Path(opts.get("profile_root") or ROOT))
    except Exception:
        return None


def config_label(config_dir, home=None):
    """The config directory as a backfilled row records it: `inherited`, or the path with `$HOME`
    as `~`, so no literal home path reaches a file the repository's lint reads."""
    if not config_dir:
        return INHERITED
    text, prefix = str(config_dir), str(Path(home) if home else Path.home())
    if text == prefix or text.startswith(prefix + os.sep):
        return "~" + text[len(prefix):]
    return text


def config_fingerprint(config_dir, home=None):
    """What an instruction layer was, as sizes: `{sha, rules, skills, agents, personal_bytes}`.

    Used by `backfill` for rows written before arms were containers, which named a profile
    directory. The sha is over the sorted `(relative path, byte size)` pairs of CONFIG_GLOBS under
    the directory. Sizes and relative paths only: no content and no home-directory path reaches a
    row. `INHERITED` means the run read `$HOME/.claude`."""
    root = Path(home or Path.home()) / ".claude" if config_dir in (None, "", INHERITED) \
        else Path(config_dir).expanduser()
    entries = set()
    for pattern in CONFIG_GLOBS:
        for path in root.glob(pattern):
            if path.is_file():
                entries.add((path.relative_to(root).as_posix(), path.stat().st_size))
    listed = sorted(entries)
    sha = hashlib.sha256("\n".join("%s %d" % pair for pair in listed).encode("utf-8")).hexdigest()[:8]
    under = lambda folder: sum(1 for name, _ in listed if name.startswith(folder + "/"))
    return {"sha": sha, "rules": under("rules"), "skills": under("skills"), "agents": under("agents"),
            "personal_bytes": dict(listed).get("CLAUDE.personal.md", 0)}


def arm_command(claude, model, prompt, run_cap=RUN_CAP_USD, max_turns=None, effort=arms.DEFAULT_EFFORT):
    """One command line for every arm, run inside its container: the arms differ by image and by
    nothing else.

    The container is the fence, so the permission mode lets the agent act without prompts, and
    the only settings passed deny the web tools, which run in the CLI's own process (`NO_WEB`).
    The output is `stream-json` with hook events, because hook lifecycle events are the only
    place a Stop hook's decision appears and the CLI emits them in no other format. `max_turns`
    is the task's own cap; without it a run is bounded only by the soft budget and the timeout.
    `effort` is the arm's pinned reasoning effort, passed as `--effort` on every launch so no run
    takes the model's default, which differs by model."""
    if effort not in arms.EFFORT_LEVELS:
        raise SystemExit("cost-bench: effort %r is not one of %s" % (effort, ", ".join(arms.EFFORT_LEVELS)))
    turns = ["--max-turns", str(int(max_turns))] if max_turns else []
    return [claude, "-p", prompt, "--model", model, "--effort", effort, "--output-format", "stream-json",
            "--include-hook-events", "--verbose", "--strict-mcp-config", "--no-session-persistence",
            "--max-budget-usd", "%g" % run_cap, "--permission-mode", PERMISSION_MODE] + turns + [
            "--settings", json.dumps(ARM_SETTINGS)]


_NAMES = itertools.count(1)


def container_name(*parts):
    """A container name unique to this process, so one that times out can be stopped by name."""
    text = "-".join(str(p) for p in parts + (next(_NAMES),))
    return "%srun-%d-%s" % (arms.IMAGE_PREFIX, os.getpid(), re.sub(r"[^a-zA-Z0-9_.-]", "-", text))


def run_check(launch, image, workdir, argv, env=None, name=None, **kwargs):
    """A check or gate command in a fresh, named container of `image` (`replay_arms.check_command`).
    On a timeout the container is removed by name before the timeout is raised on: killing the
    Docker client alone would leave it running the code it was checking."""
    name = name or container_name("check")
    command = arms.check_command(image, workdir, argv, env, name, stdin="input" in kwargs)
    client = arms.client_env()
    try:
        return launch(command, env=client, timeout=CHECK_TIMEOUT, stdout=subprocess.PIPE,
                      stderr=subprocess.STDOUT, universal_newlines=True, **kwargs)
    except subprocess.TimeoutExpired:
        launch(arms.kill_command(name), env=client, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
               universal_newlines=True)
        raise


def mounted_snapshot(repo, sha, dest):
    """`snapshot`, opened so the image's user can write it (`replay_arms.open_for_image`)."""
    return arms.open_for_image(snapshot(repo, sha, dest))


def launch_arm(record, workdir, argv, opts, name, launch=subprocess.run, observation_run=None):
    """Run `argv` in a fresh container of the arm in `record`, the snapshot at `workdir` mounted.
    On a timeout the container is removed before the timeout is raised on, so nothing keeps
    running or spending after the row is written."""
    if arms.digest(ARM_SETTINGS) != record["declaration"].get("observer_settings_sha256"):
        raise SystemExit("cost-bench: native observer settings differ from the declared inputs")
    env = arm_env(record["arm"], opts.get("stance_cost"), opts.get("proxy"), observation_run)
    command = arms.run_command(record["image"], workdir, argv, opts.get("network") or "none", env, name,
                               observation_dir=observation_run["mount"] if observation_run else None)
    client = opts.get("client_env") or arms.client_env()
    try:
        return launch(command, env=client, timeout=RUN_TIMEOUT, stdout=subprocess.PIPE,
                      stderr=subprocess.PIPE, universal_newlines=True)
    except subprocess.TimeoutExpired:
        launch(arms.kill_command(name), env=client, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
               universal_newlines=True)
        raise


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo)] + list(args), env=scrubbed_env(),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)


def _git_required(repo, *args):
    """A Git command whose failure makes the requested snapshot unsafe."""
    done = _git(repo, *args)
    if done.returncode:
        detail = done.stdout.strip() or "exit %d" % done.returncode
        raise RuntimeError("git %s failed in %s: %s" % (" ".join(args), repo, detail))
    return done


def _git_is_ancestor(repo, ancestor, descendant):
    """True/False for ancestry; an operational Git error is neither and fails closed."""
    done = _git(repo, "merge-base", "--is-ancestor", ancestor, descendant)
    if done.returncode == 0:
        return True
    if done.returncode == 1:
        return False
    detail = done.stdout.strip() or "exit %d" % done.returncode
    raise RuntimeError("git ancestry query failed in %s: %s" % (repo, detail))


def snapshot(repo, sha, dest):
    """The repository rewound to `sha`, with real history and no way forward to the fix.

    A tree with no history is not the repository an agent is asked to work in: this project's own
    gate reads its git history, so an archive of the files alone fails the gate before the agent has
    touched anything, and both arms then spend turns proving the failure was already there. The
    clone keeps every ancestor and the tags among them, and drops every ref ahead of `sha` before
    pruning, so the commit that solved the task is not reachable and not present."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    wanted = _git_required(repo, "rev-parse", "--verify", "%s^{commit}" % sha).stdout.strip()
    if len(wanted) != 40:
        raise RuntimeError("git rev-parse returned no full commit for %s" % sha)
    env = scrubbed_env()
    try:
        subprocess.run(["git", "clone", "--quiet", "--local", "--no-hardlinks", "--no-checkout",
                        str(repo), str(dest)], check=True, env=env)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("git clone failed while creating snapshot of %s" % sha) from exc
    _git_required(dest, "checkout", "--quiet", "-B", SNAPSHOT_BRANCH, wanted)
    for ref in _git_required(dest, "for-each-ref", "--format=%(refname)").stdout.split():
        ancestor = ref.startswith("refs/tags/") and _git_is_ancestor(dest, ref, "HEAD")
        if ref != "refs/heads/" + SNAPSHOT_BRANCH and not ancestor:
            _git_required(dest, "update-ref", "-d", ref)
    _git_required(dest, "remote", "remove", "origin")
    _git_required(dest, "reflog", "expire", "--expire=now", "--all")
    _git_required(dest, "gc", "--quiet", "--prune=now")
    landed = _git_required(dest, "rev-parse", "--verify", "--quiet", "HEAD").stdout.strip()
    if landed != wanted:
        raise RuntimeError("snapshot of %s did not land on that commit" % sha)
    return dest


def reaches(repo, sha):
    """Whether `sha` is present in the snapshot at all: the guard that the fix stayed hidden."""
    return _git(repo, "cat-file", "-e", sha).returncode == 0


def resolve_tag(repo, ref):
    """The commit a `--tag` names in this repository, as a full sha.

    A ref that does not resolve is a named error rather than a skipped tag: a run asked for two
    tags and given one row, with nothing in the file saying which one is missing, reads as a
    result. Every ref is resolved before the first launch, so a typo costs nothing."""
    done = _git(repo, "rev-parse", "--verify", "--quiet", "%s^{commit}" % ref)
    sha = done.stdout.strip()
    if done.returncode or len(sha) != 40:
        raise SystemExit("cost-bench: --tag %s does not name a commit in %s" % (ref, repo))
    return sha


def cli_messages(stdout):
    """(messages, streamed): the CLI's output as a list of messages. ValueError when none parse.

    The runner reads `stream-json`, one message per line. A raw file kept before that is one JSON
    document, the `json --verbose` array or a lone result, and still reads here, with `streamed`
    False so a field only the stream can carry stays unknown for it. A line that does not parse,
    such as the last one of a run cut off mid-write, is skipped rather than failing the run."""
    try:
        data = json.loads(stdout)
    except (TypeError, ValueError):
        data = None
    else:
        if isinstance(data, (list, dict)):
            return (data if isinstance(data, list) else [data]), False
    messages = []
    for line in (stdout or "").splitlines() if isinstance(stdout, str) else ():
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict):
            messages.append(message)
    if not messages:
        raise ValueError("the CLI did not return JSON")
    return messages, True


def _hook_blocked(event):
    """Whether one `hook_response` event refused the stop: a `block` decision on stdout, or the
    exit code 2 that feeds stderr back to the model."""
    if event.get("exit_code") == 2:
        return True
    for key in ("stdout", "output"):
        try:
            decision = json.loads(str(event.get(key) or "").strip() or "null")
        except ValueError:
            continue
        if isinstance(decision, dict) and decision.get("decision") == "block":
            return True
    return False


def stop_hook_counts(messages, streamed):
    """(stop hook runs, of which blocked), or (None, None) for output that cannot carry them.

    Counted from the `hook_response` lifecycle events of the `Stop` hook only. Text in the
    transcript is not a substitute: the block reason also appears in the prompt and in files the
    agent reads."""
    if not streamed:
        return None, None
    stops = [m for m in messages if m.get("type") == "system" and m.get("subtype") == "hook_response"
             and m.get("hook_event") == "Stop"]
    return len(stops), sum(1 for m in stops if _hook_blocked(m))


def installed_checkout_reads(messages):
    """Observed tool inputs that name the installed checkout or a lexical equivalent.

    This catches canonical paths and spellings such as `/opt/./model-citizen`; it cannot identify
    an unknown pre-existing symlink or a copied file. Keep the record to the tool and root rather
    than copying arbitrary command text into the ledger.
    """
    reads = []

    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)

    def installed_path(value):
        for match in re.finditer(r"/(?:[^\s\"'`;|&<>()])+", value):
            candidate = match.group(0).rstrip(".,:")
            normal = posixpath.normpath(candidate)
            if normal == INSTALLED_CHECKOUT or normal.startswith(INSTALLED_CHECKOUT + "/"):
                return True
        return False

    for message in messages:
        body = message.get("message") if isinstance(message, dict) else None
        for block in (body or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if any(installed_path(value) for value in strings(block.get("input") or {})):
                reads.append("%s:%s" % (str(block.get("name") or "unknown"), INSTALLED_CHECKOUT))
    return reads


def loaded_surface(messages):
    """Counts and content hashes from the first `system`/`init` event, plus observed effort.

    Each hash covers the CLI-reported members, with list order normalised. It detects a same-count
    replacement without claiming an identity the event did not provide. Missing keys stay unknown.
    """
    init = next((m for m in messages if isinstance(m, dict) and m.get("type") == "system"
                 and m.get("subtype") == "init"), None)
    if init is None:
        empty = dict.fromkeys(SURFACE_FIELDS + SURFACE_HASH_FIELDS)
        empty[SURFACE_SOURCE_FIELD] = None
        return empty, None
    surface = {SURFACE_SOURCE_FIELD: SURFACE_SOURCE}
    for key, field, hash_field in zip(SURFACE_KEYS, SURFACE_FIELDS, SURFACE_HASH_FIELDS):
        value = init.get(key)
        surface[field] = len(value) if isinstance(value, (list, dict)) else None
        if isinstance(value, list):
            value = sorted(value, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
        surface[hash_field] = arms.digest(value) if isinstance(value, (list, dict)) else None
    effort = init.get("effort")
    return surface, effort if isinstance(effort, str) and effort else None


def spawn_offered(messages):
    """Whether the first `system`/`init` event offered a spawn tool; None when there is no such
    event or its `tools` is not a list. False means the session could not have spawned at all."""
    init = next((m for m in messages if isinstance(m, dict) and m.get("type") == "system"
                 and m.get("subtype") == "init"), None)
    tools = init.get("tools") if init is not None else None
    if not isinstance(tools, list):
        return None
    return any(isinstance(name, str) and name in SPAWN_TOOLS for name in tools)


def failed_tool_uses(messages):
    """The ids of tool calls whose `tool_result` is an error: denied, hook-blocked or failed."""
    failed = set()
    for message in messages:
        content = (message.get("message") or {}).get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else []:
            if (isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error")
                    and isinstance(block.get("tool_use_id"), str)):
                failed.add(block["tool_use_id"])
    return failed


def counted_spawns(calls, failed):
    """The spawn calls that count, from `calls`, every spawn-tool call as (id, thread), and the
    threads those spawns started.

    A spawn counts when its result is not an error and it was made on the main thread or inside a
    thread that a counted spawn started; one made inside a `Workflow` agent's thread does not. A
    call with no id counts where it sits but names no thread."""
    counted, threads, grew = [], set(), True
    while grew:
        grew = False
        for index, (use_id, thread) in enumerate(calls):
            if index in counted or use_id in failed or not (thread is None or thread in threads):
                continue
            counted.append(index)
            if use_id is not None:
                threads.add(use_id)
            grew = True
    return [calls[index] for index in counted], threads


def surface_of(row):
    """A row's observed surface, or None when its stream carried no `init` event."""
    if row.get(SURFACE_SOURCE_FIELD) != SURFACE_SOURCE:
        return None
    fields = SURFACE_FIELDS + SURFACE_HASH_FIELDS + ("observed_effort",)
    return {field: row.get(field) for field in fields}


def surface_drift(first, now):
    """Each reported count, content hash or effort that moved between two runs of one arm."""
    fields = SURFACE_FIELDS + SURFACE_HASH_FIELDS + ("observed_effort",)
    return ["%s: %s -> %s" % (field, first.get(field), now.get(field)) for field in fields
            if first.get(field) != now.get(field)]


def _stream_diagnostics(messages, streamed):
    """Fields observable before a stream's final result, including a partial or timed-out run."""
    surface, effort = loaded_surface(messages)
    stops, blocks = stop_hook_counts(messages, streamed)
    first_turns, seen, first_write, first_context, tools = [], set(), None, None, {}
    assistant, gathers, spawn_calls = False, [], []
    cache = {"cache_read": 0, "cache_write": 0, "turns": 0, "known": True}
    for message in messages:
        if not isinstance(message, dict) or message.get("type") != "assistant":
            continue
        assistant = True
        thread = message.get("parent_tool_use_id")
        body = message.get("message") or {}
        for block in body.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = str(block.get("name") or "")
                tools[name] = tools.get(name, 0) + 1
                if name in GATHER_TOOLS:
                    gathers.append(thread)
                elif name in SPAWN_TOOLS:
                    spawn_calls.append((block.get("id"), thread))
        if not isinstance(body.get("usage"), dict):
            continue
        if first_write is None:
            first_write = int(body["usage"].get("cache_creation_input_tokens") or 0)
            first_context = sum(int(body["usage"].get(key) or 0) for key in
                                ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        for field, key in (("cache_read", "cache_read_input_tokens"),
                           ("cache_write", "cache_creation_input_tokens")):
            if key not in body["usage"]:
                cache["known"] = False
            cache[field] += int(body["usage"].get(key) or 0)
        cache["turns"] += 1
        if thread in seen:
            continue
        seen.add(thread)
        first_turns.append({"model": body.get("model") or "",
                            "cache_read": int(body["usage"].get("cache_read_input_tokens") or 0)})
    # Output with no assistant message, such as a lone result, cannot show a call: unknown, not 0.
    count = lambda value: value if assistant else None
    spawned, spawn_thread_ids = counted_spawns(spawn_calls, failed_tool_uses(messages))
    return {"first_turns": first_turns, "first_call_cache_write": first_write,
            "first_call_context": first_context, "tool_counts": tools,
            "spawns": count(len(spawned)),
            "spawn_offered": spawn_offered(messages), "gather_calls": count(len(gathers)),
            "absorbed_calls": count(sum(1 for thread in gathers if thread in spawn_thread_ids)),
            "workflow_launches": count(sum(tools.get(name, 0) for name in WORKFLOW_TOOLS)), "stop_hooks": stops,
            "hook_blocks": blocks, "cache_miss_ratio": run_miss_ratio(cache),
            "installed_checkout_reads": installed_checkout_reads(messages),
            "observed_effort": effort, **surface}


def parse_diagnostics(stdout):
    """Diagnostic fields present in any readable CLI stream, whether or not it finished."""
    messages, streamed = cli_messages(stdout)
    return _stream_diagnostics(messages, streamed)


def parse_result(stdout):
    """Cost, tokens, turns and the diagnostic fields, from the CLI's output. ValueError when there
    is no priced final result to read.

    With `--verbose` the output is every message, which also gives each thread's first turn; without
    it the output is the result alone and the cache-normalised cost cannot be computed.

    `first_call_cache_write` is the standing prefix: the cache write of the first assistant message
    carrying a usage block, which is what the session paid to put its instruction layer in the
    cache, as against the run's total writes. `first_call_context` is that message's whole input,
    `input + cache_creation + cache_read`: the write alone moves with how warm the cache was, the
    total does not, so compare runs on the total and read the pair for warmth. `tool_counts` counts every `tool_use` content block
    by name; `spawns` counts only the spawn calls `counted_spawns` accepts, so a denied spawn or one
    inside a `Workflow` agent is in `tool_counts` and not in `spawns`.

    `spawn_offered` is whether the `init` event listed a spawn tool. `gather_calls` counts
    `GATHER_TOOLS` calls in every thread, `absorbed_calls` those made inside a counted spawn's thread,
    and `workflow_launches` the `Workflow` calls, which are not spawns (`delegation_verdict`).
    With no assistant message the four counts are None, never zero; `tool_counts` stays `{}`.

    `stop_hooks` and `hook_blocks` are how often the Stop hook ran and how often it refused the
    stop. Hook lifecycle events carry them, and the CLI emits those only under
    `--include-hook-events`, which works only with `--output-format=stream-json`; for output kept
    in the older single-document form both are None, never zero. See `stop_hook_counts`.

    The `init_*` counts and `observed_effort` come from the `init` event (`loaded_surface`)."""
    messages, streamed = cli_messages(stdout)
    diagnostics = _stream_diagnostics(messages, streamed)
    results = [m for m in messages if isinstance(m, dict) and m.get("type") == "result"]
    if not results or not isinstance(results[-1].get("total_cost_usd"), (int, float)):
        raise ValueError("the CLI returned no result with total_cost_usd")
    result = results[-1]
    per_model = [u for u in (result.get("modelUsage") or {}).values() if isinstance(u, dict)]
    if per_model:  # includes subagents, which the top-level usage block may not
        tokens = {kind: sum(int(u.get(key) or 0) for u in per_model)
                  for kind, key in zip(TOKEN_KINDS, MODEL_USAGE_KEYS)}
    else:
        tokens = {kind: int((result.get("usage") or {}).get(kind) or 0) for kind in TOKEN_KINDS}
    return {"cost_usd": float(result["total_cost_usd"]), "tokens": tokens,
            "turns": int(result.get("num_turns") or 0), "is_error": bool(result.get("is_error")),
            "subtype": str(result.get("subtype") or ""), **diagnostics}


def run_miss_ratio(cache):
    """The share of a run's prefix the provider re-wrote: `write / (read + write)`, or None.

    The ledger's figure, taken from `harness_core.cache_prefix` so the replay and
    `harness usage --by prefix` cannot drift apart. Unlike the ledger's, this one counts every
    thread the run opened, subagents included: a fan-out writes a fresh prefix, and here that is
    part of what the run cost rather than something to subtract.

    None when the CLI output carried no per-turn usage at all, when a turn's usage block omits
    either cache field, and when the turns it did carry report neither reads nor writes. Zero is
    a run that served its whole prefix from cache, and a run that cannot say must never be read
    as that one: a silent turn counted as two zeroes would be averaged in as a held prefix."""
    if not cache["turns"] or not cache["known"]:
        return None
    ratio = cache_prefix.miss_ratio(cache, "")
    return None if ratio is None else round(ratio, 4)


def _rates(prices, model):
    names = [name for name in prices if model == name or model.startswith(name + "-")]
    return prices[max(names, key=len)] if names else None


def normalised_cost(cost, first_turns, prices):
    """Reported cost with each thread's first-turn cache reads repriced as cache writes.

    Whether a run finds its prefix already cached depends on what ran before it, so run order moves
    the reported dollars. None when the first turns or a model's rates are unknown: never a guess."""
    if not first_turns:
        return None
    extra = 0.0
    for turn in first_turns:
        rates = _rates(prices, turn["model"])
        if not rates or "cache_write" not in rates or "cache_read" not in rates:
            return None
        extra += turn["cache_read"] * (rates["cache_write"] - rates["cache_read"]) / 1e6
    return round(cost + extra, 6)


def _oracle(repo, name):
    spec = importlib.util.spec_from_file_location("oracle_" + name, str(Path(repo) / ORACLES / (name + ".py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The last line a check container prints for a synthetic task: the oracle's errors as JSON.
ORACLE_MARK = "cost-bench-oracle-errors: "
ORACLE_DRIVER = """
import json as _json
from pathlib import Path as _Path
print(%r + _json.dumps(check(_Path(%r))))
"""


def _copy_held_back(task, workdir, repo):
    """Write the task's held-back test files into `workdir`, from this repository's own history."""
    for name in task["tests"]["copy"]:
        blob = subprocess.run(["git", "-C", str(repo), "show", "%s:%s" % (task["good_sha"], name)],
                              check=True, stdout=subprocess.PIPE).stdout
        (workdir / name).parent.mkdir(parents=True, exist_ok=True)
        (workdir / name).write_bytes(blob)


def _unittest_command(task, python):
    tests = task["tests"]
    command = [python, "-m", "unittest", "discover", "-s", "tests", "-p", tests["pattern"]]
    for select in tests.get("select", []):
        command += ["-k", select]
    return command


def _ran(done):
    ran = re.search(r"^Ran (\d+) tests? in", done.stdout or "", re.M)
    count = int(ran.group(1)) if ran else 0
    # Python 3.9 exits 0 when a filter matches nothing, so no tests run is a failure here.
    return (done.returncode == 0 and count > 0, "ran %d, exit %d" % (count, done.returncode))


def score(task, workdir, repo, image, launch=subprocess.run, name=None):
    """(passed, detail), with the check run in a fresh container of `image`, never on this machine.

    Both kinds of check execute the tree they score: a unit test imports it, and an oracle may
    compile or load it. An agent's tree is code nobody reviewed, and even this repository's own
    older trees read the configuration under whatever HOME they are given, so a check on this
    machine would score the owner's live profile along with the task. So every check runs in a
    container of the bare image, with the tree mounted as the only path, the image's own HOME, no
    network and no credential. The held-back test files are written into the tree from this
    repository's history first; an oracle is sent on stdin, so nothing else is mounted."""
    workdir = Path(workdir)
    tests = task["tests"]
    name = name or container_name("check", task["id"])
    if task["kind"] == "synthetic":
        source = (Path(repo) / ORACLES / (tests["oracle"] + ".py")).read_text(encoding="utf-8")
        stdin = source + ORACLE_DRIVER % (ORACLE_MARK, arms.WORKDIR)
        done = run_check(launch, image, workdir, ["python3", "-"], {}, name, input=stdin)
        marks = [line for line in (done.stdout or "").splitlines() if line.startswith(ORACLE_MARK)]
        if done.returncode or not marks:
            raise RuntimeError("the oracle did not report (exit %s)" % done.returncode)
        errors = json.loads(marks[-1][len(ORACLE_MARK):])
        return (not errors, "; ".join(errors[:3]))
    _copy_held_back(task, workdir, repo)
    env = {"PYTHONPATH": ":".join("%s/%s" % (arms.WORKDIR, p) for p in tests.get("pythonpath", []))}
    done = run_check(launch, image, workdir, _unittest_command(task, "python3"), env, name)
    return _ran(done)


def repo_gate(workdir, commands, image, launch=subprocess.run):
    """Exit codes for the repository's own gate, run in `workdir`, each command in a fresh
    container of `image` exactly as `score` runs a check: the tree the only mount, the image's
    own HOME, no network. A benchmark fixture whose gate is already red charges both arms for
    failures the agent did not cause, and one run on this machine would read the owner's live
    configuration and go red for that instead."""
    out = []
    for command in commands:
        done = run_check(launch, image, workdir, command, {"PYTHONPATH": arms.WORKDIR + "/lib"},
                         container_name("gate"))
        out.append((" ".join(command), done.returncode))
    return out


def verify_tasks(tasks, repo, parent, image, gate=None, launch=subprocess.run):
    """Errors for every task whose fixture or check does not hold before the agent runs. The gate
    and every check run in containers of `image`, the bare arm, as the replay's checks do."""
    errors = []
    for task in tasks:
        before = mounted_snapshot(repo, task["parent_sha"], Path(parent) / (task["id"] + "-parent"))
        if task["kind"] == "issue" and reaches(before, task["good_sha"]):
            errors.append("%s: the commit that solved it is present in the snapshot" % task["id"])
        for command, code in repo_gate(before, gate or [], image, launch):
            if code:
                errors.append("%s: `%s` already fails in a clean snapshot (exit %d)"
                              % (task["id"], command, code))
        if score(task, before, repo, image, launch)[0]:
            errors.append("%s: the check already passes at the parent sha" % task["id"])
        if task["kind"] == "issue":
            after = mounted_snapshot(repo, task["good_sha"], Path(parent) / (task["id"] + "-good"))
        else:
            after = before
            _oracle(repo, task["tests"]["oracle"]).solve(after)
        passed, detail = score(task, after, repo, image, launch)
        if not passed:
            errors.append("%s: the check fails on the known-good tree (%s)" % (task["id"], detail))
    return errors


def contamination_errors(tasks, repo, harness_commit, tmp=None):
    """Tasks whose answer is present in the checkout installed in the harness image.

    An issue task mined from this repository is refused while the installed image contains this
    repository: ancestry cannot rule out a cherry-pick, squash or equivalent implementation in
    its files. A same-repository synthetic task is refused too: an arbitrary oracle failure does
    not prove the answer absent, and its installed oracle source can expose `solve`. This check is
    local and deterministic, and therefore runs before the first model call.
    """
    parent = Path(tempfile.mkdtemp(prefix="cost-contamination-", dir=tmp))
    try:
        errors = []
        checkout = snapshot(repo, harness_commit, parent / "checkout") if tasks else None
        for task in tasks:
            if task["kind"] == "issue":
                errors.append("%s: same-repository issue task cannot prove its fixed files are "
                              "absent from the installed checkout" % task["id"])
                continue
            oracle_path = checkout / ORACLES / (task["tests"]["oracle"] + ".py")
            if oracle_path.is_file():
                errors.append("%s: installed checkout exposes the held-back oracle source and "
                              "its reference solution" % task["id"])
            else:
                errors.append("%s: answer absence cannot be established for this same-repository "
                              "synthetic task" % task["id"])
        return errors
    finally:
        shutil.rmtree(str(parent), ignore_errors=True)


def schedule(tasks, reps):
    """Arms interleaved inside each task and rep, the leading arm alternating so neither always
    runs on the other's warm cache."""
    out = []
    for task in tasks:
        for rep in range(1, reps + 1):
            order = ARMS if rep % 2 else ARMS[::-1]
            out += [(task, rep, arm) for arm in order]
    return out


def arm_stamp(record):
    """What every row of one arm records about the container it ran in: the image and its id,
    the digests of the arm's declaration and manifest, and the harness ref and full commit, both
    None for the bare arm. The printed label (`harness@<ref>`) stays out of rows: with a dotted
    ref it reads as an email address to the repository's lint, and the ref is here already."""
    return {"arm_image": record["image"], "arm_image_id": record["image_id"],
            "arm_declaration_sha256": record["declaration_sha256"],
            "arm_manifest_sha256": record["manifest_sha256"],
            "arm_base_image": record["declaration"]["base_image"],
            "harness_ref": record["harness_ref"], "harness_commit": record["harness_commit"]}


def _scorer(opts, launch):
    if opts.get("scorer"):
        return opts["scorer"]
    image = opts["arms"]["bare"]["image"]
    return lambda task, workdir, repo: score(task, workdir, repo, image, launch)


def run_one(task, rep, arm, opts, launch=subprocess.run):
    """One row. An errored run is `error: true` with `passed: null`, so it stays countable apart;
    its `outcome` is `fail`, and `summarise` and `replay_stats` count it as a failed attempt with
    its cost (intention to treat). Every row names its task, arm, trial (`rep`), outcome, cost and
    the task's long mark, so each figure re-derives from the rows alone."""
    row = _attempt(task, rep, arm, opts, launch)
    return dict(row, outcome="pass" if row["passed"] and not row["error"] else "fail")


def _partial_diagnostics(row, stdout):
    """Copy what a readable stream established even when its priced result never arrived."""
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    try:
        parsed = parse_diagnostics(stdout)
    except ValueError:
        return row
    row.update({field: parsed[field] for field in STREAM_FIELDS})
    return row


def _attempt(task, rep, arm, opts, launch):
    record = opts["arms"][arm]
    effort = record["declaration"]["effort"]
    env = arm_env(arm, opts.get("stance_cost"), opts.get("proxy"))
    profile = arm_profile(arm, env, opts)
    profile = arm_profile(arm, env, opts)
    row = dict(opts["stamp"], task=task["id"], task_long=bool(task.get("long")), arm=arm, tag=opts["tag"],
               rep=rep, passed=None, error=False,
               error_kind="", cost_usd=None, cost_normalised_usd=None, turns=None, wall_seconds=None,
               first_call_cache_write=None, first_call_context=None, tool_counts={}, spawns=None,
               spawn_offered=None, gather_calls=None, absorbed_calls=None, workflow_launches=None,
               stop_hooks=None, hook_blocks=None, cache_miss_ratio=None, effort=effort, observed_effort=None,
               init_surface_source=None, installed_checkout_reads=[],
               contamination_control=CONTAMINATION_CONTROL,
               surface_drift=[],
               observation_ledger=None, observation_rows=None, observation_errors=None,
               change_note=opts.get("change_note", ""), preflight=opts.get("preflight", "skipped"),
               profile_fingerprint=profile,
               context_attribution=arm_attribution(arm, env, opts),
               **dict(arm_stamp(record), **{kind: None for kind in TOKEN_KINDS},
                      **{field: None for field in SURFACE_FIELDS + SURFACE_HASH_FIELDS}))
    workdir = Path(tempfile.mkdtemp(prefix="cost-replay-", dir=opts.get("tmp"))) / "repo"
    observed = None

    def finish(value):
        fields, problem = observation_result(observed)
        value.update(fields)
        if problem:
            prior = value.get("error_kind")
            value.update(error=True, passed=None, error_kind="; ".join(x for x in (prior, problem) if x),
                         cache_miss_ratio=None)
        return value

    started = time.time()
    try:
        observed = observation_run(opts, "%s-%s-%d" % (task["id"], arm, rep), profile)
        mounted_snapshot(opts["repo"], task["parent_sha"], workdir)
        try:
            done = launch_arm(record, workdir, arm_command("claude", opts["model"], prompt_of(task),
                                                           opts["run_cap"], task["max_turns"], effort),
                              opts, container_name(task["id"], arm, rep), launch, observed)
        except subprocess.TimeoutExpired as exc:
            partial = getattr(exc, "stdout", None)
            partial = partial if partial is not None else getattr(exc, "output", None)
            _partial_diagnostics(row, partial)
            return finish(dict(row, error=True, error_kind="timeout", cost_usd=opts["run_cap"],
                               wall_seconds=round(time.time() - started, 1)))
        row["wall_seconds"] = round(time.time() - started, 1)
        if opts.get("raw"):
            Path(opts["raw"]).mkdir(parents=True, exist_ok=True)
            (Path(opts["raw"]) / ("%s-%s-%d.json" % (task["id"], arm, rep))).write_text(done.stdout or "",
                                                                                     encoding="utf-8")
        try:
            parsed = parse_result(done.stdout)
        except ValueError as exc:
            _partial_diagnostics(row, done.stdout)
            return finish(dict(row, error=True, error_kind="exit %s: %s" % (done.returncode, exc)))
        row.update(parsed["tokens"], cost_usd=parsed["cost_usd"], turns=parsed["turns"],
                   cost_normalised_usd=normalised_cost(parsed["cost_usd"], parsed["first_turns"], opts["prices"]),
                   **{field: parsed[field] for field in STREAM_FIELDS})
        if parsed["installed_checkout_reads"]:
            return finish(dict(row, error=True, error_kind="installed-checkout-read"))
        if parsed["observed_effort"] is not None and parsed["observed_effort"] != effort:
            # The stream says the run went at another effort than the one pinned: not this arm.
            return finish(dict(row, error=True, cache_miss_ratio=None,
                               error_kind="effort: observed %s, pinned %s"
                               % (parsed["observed_effort"], effort)))
        if parsed["is_error"] or done.returncode:
            # The other stream fields diagnose an errored run; a miss ratio only describes one
            # that finished, and an aborted run's turns are not the spend it would have had.
            return finish(dict(row, error=True, cache_miss_ratio=None,
                               error_kind=parsed["subtype"] or "exit %s" % done.returncode))
        try:
            row["passed"] = bool(_scorer(opts, launch)(task, workdir, opts["repo"])[0])
        except Exception as exc:  # a check that cannot run says nothing about the agent's work
            return finish(dict(row, error=True, error_kind="check: %s" % type(exc).__name__))
        return finish(row)
    finally:
        shutil.rmtree(str(workdir.parent), ignore_errors=True)  # removed, never reset
        discard_observation(observed)


def gate_output(stdout):
    """Every tool result in a `-p` stream, joined: the gate's own output, not the model's relay."""
    try:
        messages = cli_messages(stdout)[0]
    except ValueError:
        return ""
    parts = []
    for ev in messages:
        content = ((ev.get("message") or {}).get("content") if isinstance(ev, dict) else None) or []
        for blk in content if isinstance(content, list) else []:
            if isinstance(blk, dict) and blk.get("type") == "tool_result":
                text = blk.get("content")
                parts.append(text if isinstance(text, str) else json.dumps(text))
    return "\n".join(parts)


def gate_passed(stdout):
    """Green means lint reported no findings and nothing was refused. A suite verdict in the
    output is ignored either way: it is not the bar, and on a bench profile it is red for reasons
    that are not the arm's."""
    out = gate_output(stdout)
    return "lint: 0 finding(s)" in out and PREFLIGHT_RED.search(out) is None


def reply_text(stdout):
    """The final text of a `-p` run: the `result` field of the CLI's last result message, or ""."""
    try:
        messages = cli_messages(stdout)[0]
    except ValueError:
        return ""
    texts = [m.get("result") for m in messages if isinstance(m, dict) and m.get("type") == "result"]
    return str(texts[-1] or "").strip() if texts else ""


def preflight(tasks, opts, launch=subprocess.run):
    """([{arm, passed, reply, cost_usd, effort, observed_effort}], spent).

    Each runs in the arm's own container, so this asks the question the scored runs depend on:
    can an agent in this arm make the repository's own gate pass at all? An arm that cannot
    spends its turns on that instead of on the task, and the comparison measures the runner rather
    than the harness. The verdict is read from the gate's own output (`gate_passed`)."""
    checks, spent = [], 0.0
    for arm in ARMS:
        workdir = Path(tempfile.mkdtemp(prefix="cost-preflight-", dir=opts.get("tmp"))) / "repo"
        collector = None
        try:
            mounted_snapshot(opts["repo"], tasks[0]["parent_sha"], workdir)
            env = arm_env(arm, opts.get("stance_cost"), opts.get("proxy"))
            collector = observation_run(opts, "preflight-%s" % arm, arm_profile(arm, env, opts))
            command = arm_command("claude", opts["model"], PREFLIGHT_PROMPT, PREFLIGHT_CAP_USD,
                                  PREFLIGHT_TURNS, opts["arms"][arm]["declaration"]["effort"])
            try:
                done = launch_arm(opts["arms"][arm], workdir, command, opts,
                                  container_name("preflight", arm), launch, collector)
            except subprocess.TimeoutExpired:
                spent += PREFLIGHT_CAP_USD
                fields, problem = observation_result(collector)
                checks.append({"arm": arm, "passed": False, "reply": "timeout", "cost_usd": None,
                               "effort": opts["arms"][arm]["declaration"]["effort"],
                               "observed_effort": None, **fields})
                continue
            if opts.get("raw"):
                raw = Path(opts["raw"]); raw.mkdir(parents=True, exist_ok=True)
                (raw / ("preflight-%s.json" % arm)).write_text(done.stdout or "", encoding="utf-8")
            reply = reply_text(done.stdout)
            try:
                parsed = parse_result(done.stdout)
                cost, observed_effort = parsed["cost_usd"], parsed["observed_effort"]
            except ValueError:
                cost, observed_effort = None, None
            effort = opts["arms"][arm]["declaration"]["effort"]
            effort_matches = observed_effort is None or observed_effort == effort
            fields, observation_problem = observation_result(collector)
            spent += PREFLIGHT_CAP_USD if cost is None else cost
            # Every reason a preflight is red is named: an effort mismatch never hides a
            # collector failure behind it.
            problems = ([] if effort_matches else
                        ["observed effort %s, pinned %s" % (observed_effort, effort)])
            problems += [observation_problem] if observation_problem else []
            checks.append({"arm": arm, "passed": gate_passed(done.stdout) and effort_matches
                           and not observation_problem,
                           "reply": "; ".join(problems) if problems else reply,
                           "cost_usd": cost, "effort": effort, "observed_effort": observed_effort, **fields})
        finally:
            shutil.rmtree(str(workdir.parent), ignore_errors=True)
            discard_observation(collector)
    return checks, spent


def probe_workdirs(tasks, opts, launch=subprocess.run):
    """Refuse the replay unless each arm's user can write a snapshot mounted as a run mounts it and
    git will use it there (`replay_arms.probe_workdir`), before anything is spent."""
    parent = Path(tempfile.mkdtemp(prefix="cost-probe-", dir=opts.get("tmp")))
    try:
        workdir = mounted_snapshot(opts["repo"], tasks[0]["parent_sha"], parent / "repo")
        for arm in ARMS:
            arms.probe_workdir(opts["arms"][arm], workdir, launch, container_name("probe", arm))
    finally:
        shutil.rmtree(str(parent), ignore_errors=True)


def replay(tasks, opts, launch=subprocess.run, out=None):
    """(rows, stopped). Stops before a launch that could take reported spend past the cap; the
    per-run cap is soft, so a run with no readable cost is counted at the full run cap.

    Every arm passes `replay_arms.admit` first: the one place an arm is refused before anything
    of it launches. Each arm's user must then be able to write a mounted snapshot. A red pre-flight then refuses the whole replay with exit 2 before any scored
    run launches, since spending on arms that cannot pass the gate buys a number nobody can read.
    Its own cost counts against the same cumulative cap.

    The pair must differ by the harness alone (`replay_arms.admit_pair`). During the set, each
    row's loaded surface (`SURFACE_FIELDS`) is compared with its arm's first run that reported
    one; a difference is recorded on the row as `surface_drift` and stops the set once that row
    is written, unless the stamp says `surface_drift_allowed`. A run whose stream reports another
    effort than the pinned one stops the set whatever the stamp says.

    A saved result is created exclusively before probes or model calls, so an existing path can
    never mix attempts from two cohorts."""
    if not tasks:
        raise SystemExit("cost-bench: no contamination-safe replay tasks are eligible")
    if out is None:
        return _replay(tasks, opts, launch, None)
    try:
        sink = open(str(out), "x", encoding="utf-8")
    except FileExistsError:
        raise SystemExit("cost-bench: refusing to append to existing saved results: %s" % out)
    with sink:
        return _replay(tasks, opts, launch, sink)


def _replay(tasks, opts, launch, sink):
    refuse_observation_collisions(tasks, opts)
    for arm in ARMS:
        arms.admit(dict(opts["arms"][arm], protocol=opts["stamp"]))
    arms.admit_pair(opts["arms"]["bare"], opts["arms"]["harness"])
    check_contamination = opts.get("contamination_checker", contamination_errors)
    contaminated = check_contamination(tasks, opts["repo"],
                                       opts["arms"]["harness"]["harness_commit"], opts.get("tmp"))
    if contaminated:
        for error in contaminated:
            print("cost-bench: contamination: %s" % error, file=sys.stderr)
        print("cost-bench: refusing the replay before any model call", file=sys.stderr)
        raise SystemExit(2)
    probe_workdirs(tasks, opts, launch)
    rows, spent = [], 0.0
    if not opts.get("skip_preflight"):
        checks, spent = preflight(tasks, opts, launch)
        red = [c for c in checks if not c["passed"]]
        for check in red:
            print("cost-bench: the %s arm's gate is red in its own container: %s"
                  % (check["arm"], check["reply"] or "no reply"), file=sys.stderr)
        if red:
            print("cost-bench: refusing the replay; no scored run launched", file=sys.stderr)
            raise SystemExit(2)
        opts = dict(opts, preflight="passed")
    firsts, allowed = {}, bool(opts["stamp"].get("surface_drift_allowed"))
    for task, rep, arm in schedule(tasks, opts["reps"]):
        if spent + opts["run_cap"] > opts["spend_cap"]:
            return rows, True
        row = run_one(task, rep, arm, opts, launch)
        surface = surface_of(row)
        if surface is not None:
            row["surface_drift"] = surface_drift(firsts.setdefault(arm, surface), surface)
        spent += opts["run_cap"] if row["cost_usd"] is None else row["cost_usd"]
        rows.append(row)
        if sink is not None:
            sink.write(json.dumps(row, sort_keys=True) + "\n")
            sink.flush()
        where = "the %s arm's run of %s rep %d" % (arm, task["id"], rep)
        if row.get("observed_effort") is not None and row["observed_effort"] != row["effort"]:
            raise SystemExit("cost-bench: stopping the set: %s ran at effort %s, pinned %s"
                             % (where, row["observed_effort"], row["effort"]))
        if row["surface_drift"] and not allowed:
            raise SystemExit("cost-bench: stopping the set: %s loaded a different surface from the arm's "
                             "first run:\n  %s\nthe %d row(s) so far are written; --allow-surface-drift "
                             "runs on and stamps every row" % (where, "\n  ".join(row["surface_drift"]), len(rows)))
    return rows, False


def _mean(values):
    return sum(values) / len(values) if values else None


def summarise(rows, field="cost_usd"):
    """Per arm: cost per passed task and passes, each the mean of reps. By intention to treat every
    attempt counts: an errored, crashed or timed-out run is a failed attempt whose cost is in the
    figure, and `errors` still counts them apart. A rep in which an arm passed nothing, or holds a
    non-timeout run with no readable cost, has no cost per passed task. A timeout is recorded at
    the run cap before it reaches this function."""
    out = {}
    for arm in ARMS:
        mine = [r for r in rows if r["arm"] == arm]
        scored = [r for r in mine if not r["error"]]
        per_rep, passes = [], []
        for rep in sorted({r["rep"] for r in mine}):
            runs = [r for r in mine if r["rep"] == rep]
            won = sum(1 for r in runs if r["passed"])
            passes.append(won)
            costs = [r[field] for r in runs]
            per_rep.append(sum(costs) / won if won and None not in costs else None)
        out[arm] = {"runs": len(mine), "errors": len(mine) - len(scored), "passed": _mean(passes),
                    "cost_per_passed": None if None in per_rep or not per_rep else round(_mean(per_rep), 6)}
    return out


def per_task(rows, field="cost_usd"):
    """One cell per task: each arm's mean cost, the ratio between them, and each arm's spread.

    Spread is max over min priced cost in the cell, so a cell whose two reps differ by half is
    visible as 1.5 and the aggregate ratio above it is read with that in mind. None when the cell
    holds fewer than two priced runs, because one run has no spread to report."""
    out = {}
    for task in sorted({r.get("task") or "" for r in rows}):
        cell, reps = {}, set()
        for arm in ARMS:
            mine = [r for r in rows if (r.get("task") or "") == task and r["arm"] == arm]
            reps.update(r["rep"] for r in mine)
            costs = [r[field] for r in mine if r.get(field) is not None]  # errored runs too
            cell[arm] = round(_mean(costs), 6) if costs else None
            cell[arm + "_spread"] = round(max(costs) / min(costs), 4) if len(costs) > 1 and min(costs) else None
        ratio = round(cell["harness"] / cell["bare"], 4) if cell["bare"] and cell["harness"] is not None else None
        out[task] = dict(cell, ratio=ratio, n=len(reps))
    return out


def cache_miss(rows):
    """Per arm: the mean of the per-run miss ratios, over the runs that reported one.

    None rather than zero when no scored run in the arm reported a figure, matching the per-run
    rule. It sits beside the cache-normalised ratio because the two answer different halves of
    one question: the normalised cost says what the run would have cost with a cold prefix, and
    this says how much of its prefix it actually re-bought."""
    out = {}
    for arm in ARMS:
        known = [r["cache_miss_ratio"] for r in rows if r["arm"] == arm and not r["error"]
                 and r.get("cache_miss_ratio") is not None]
        out[arm] = round(_mean(known), 4) if known else None
    return out


def verdict(summary):
    """The publishable threshold, fixed before the run: at most 85% of bare per passed task, passing
    no fewer than bare minus one."""
    bare, harness = summary["bare"], summary["harness"]
    known = bare["cost_per_passed"] and harness["cost_per_passed"] is not None
    ratio = round(harness["cost_per_passed"] / bare["cost_per_passed"], 4) if known else None
    if None not in (bare["passed"], harness["passed"]) and harness["passed"] < bare["passed"] - 1:
        return ratio, "failed"  # passing too few fails whatever the dollars say
    if ratio is None:
        return None, "inconclusive"
    return ratio, "passed" if ratio <= THRESHOLD else "failed"


def history_row(rows, series, break_even=delegation_verdict.BREAK_EVEN_CALLS):
    """One line for `history.jsonl`: a harness version against bare on the same day and model.

    It carries the per-task breakdown as well as the aggregate, because one task moving is the
    usual shape of a regression and the aggregate alone cannot tell that from a broad one. Its
    `delegation` key is the per-task firing verdict (`delegation_verdict.report`), an adherence
    reading beside SM-2 rather than part of it; a reader of older lines finds no such key."""
    first = rows[0]
    reported, normalised = summarise(rows), summarise(rows, "cost_normalised_usd")
    ratio, status = verdict(reported)
    return {"date": first["date"], "series": series, "bucket": first.get("bucket", ""),
            "predicted_ratio": first.get("predicted_ratio"),
            "harness_version": first["harness_version"],
            "harness_sha": first["harness_sha"], "tag": first["tag"], "model": first["model"],
            "cli_version": first["cli_version"], "reps": max(r["rep"] for r in rows), "runs": len(rows),
            "change_note": first.get("change_note", ""), "per_task": per_task(rows),
            "bare": reported["bare"], "harness": reported["harness"], "ratio": ratio,
            "ratio_cache_normalised": verdict(normalised)[0], "cache_miss": cache_miss(rows),
            "threshold": THRESHOLD, "status": status, "arms": arm_records(rows), "sm2": sm2(rows),
            "delegation": delegation_verdict.report(rows, break_even)}


def sm2(rows, seed=replay_stats.SEED, resamples=replay_stats.RESAMPLES):
    """SM-2's result for the rows (`replay_stats.analyse`), or `{"unavailable": reason}` for a set
    it cannot derive from, such as rows that saved no pass or fail."""
    try:
        return replay_stats.analyse(rows, seed, resamples)
    except ValueError as exc:
        return {"unavailable": str(exc)}


def arm_records(rows):
    """Per arm, the container its rows ran in, from the first row of that arm that names one:
    `{image_id, manifest_sha256, harness_ref, harness_commit}`. An arm with no such row,
    as in a row set from before arms were containers, is left out."""
    out = {}
    for arm in ARMS:
        named = [r for r in rows if r["arm"] == arm and r.get("arm_image_id")]
        if named:
            first = named[0]
            out[arm] = {"image_id": first["arm_image_id"],
                        "manifest_sha256": first.get("arm_manifest_sha256"),
                        "harness_ref": first.get("harness_ref"), "harness_commit": first.get("harness_commit")}
    return out


def upsert_history(path, row):
    """Append, replacing an earlier line for the same version, commit, day, series and bucket.

    The bucket is part of the key: a programme that changes one thing at a time measures several
    buckets at one sha on one day, and without it each row would overwrite the last."""
    path = Path(path)
    key = lambda r: (r["date"], r["series"], r["harness_version"], r["harness_sha"], r.get("bucket", ""))
    old = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] \
        if path.is_file() else []
    kept = [r for r in old if key(r) != key(row)] + [row]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in kept), encoding="utf-8")
    return kept


def render_history(rows):
    """The ledger as text: the aggregate table, and under each of its lines the per-task detail.

    The detail is indented plain text rather than more table rows, since a task line answers a
    different question from the columns above it and would need none of them."""
    usd = lambda v: "n/a" if v is None else "%.3f" % v
    lines = ["# Cost per passed task, harness against bare Claude Code", "",
             "Dollars are list-price equivalents reported by the CLI, not money charged. Compare ratios"
             " across days, never dollars. A new series means the task set or the model changed.", "",
             "| Date | Series | Bucket | Harness | Model | Bare USD per pass | Harness USD per pass |"
             " Ratio | Predicted | Cache-normalised ratio | Cache miss, bare | Cache miss, harness |"
             " Passed, bare | Passed, harness | Errors | Threshold %.2f |" % THRESHOLD,
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"
             " --- | --- |"]
    for r in rows:
        miss = r.get("cache_miss") or {}
        lines.append("| %s | %s | %s | %s @ %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |"
                     " %d | %s |" % (
            r["date"], r["series"], r.get("bucket") or "n/a", r["harness_version"], r["harness_sha"][:7],
            r["model"], usd(r["bare"]["cost_per_passed"]), usd(r["harness"]["cost_per_passed"]),
            usd(r["ratio"]), usd(r.get("predicted_ratio")), usd(r["ratio_cache_normalised"]),
            usd(miss.get("bare")), usd(miss.get("harness")),
            usd(r["bare"]["passed"]), usd(r["harness"]["passed"]),
            r["bare"]["errors"] + r["harness"]["errors"], r["status"]))
        if r.get("change_note"):
            lines.append("    note: %s" % r["change_note"])
        result = r.get("sm2") or {}
        if result.get("unavailable"):
            lines.append("    SM-2: unavailable, %s" % result["unavailable"])
        elif result:
            lines.append("    SM-2: %s, ratio %s %s, difference %s %s%s" % (
                result["verdict"], usd(result["ratio"]), _span(result["ratio_interval"]),
                usd(result["difference"]), _span(result["difference_interval"]),
                ", claim: %s" % result["claim"] if result["claim"] else ""))
        for task, cell in sorted((r.get("per_task") or {}).items()):
            lines.append("    %s: bare %s, harness %s, ratio %s, spread bare %s / harness %s, n %d"
                         % (task or "n/a", usd(cell.get("bare")), usd(cell.get("harness")),
                            usd(cell.get("ratio")), usd(cell.get("bare_spread")),
                            usd(cell.get("harness_spread")), cell.get("n") or 0))
        block = r.get("delegation")
        if block:
            lines.append("    delegation: " + delegation_verdict.heading(block))
            for task, cell in sorted((block.get("tasks") or {}).items()):
                lines.append("    delegation: " + delegation_verdict.task_line(task, cell,
                                                                            block.get("registered", False)))
    return "\n".join(lines) + "\n"


def _span(interval):
    return "[undefined]" if not interval else "[%s]" % ", ".join(
        "undefined" if v is None else "%.3f" % v for v in interval)


def cmd_summarise(args):
    """SM-2's report from a saved `results.jsonl` alone, then the delegation verdict per task
    (`delegation_verdict`), which SM-2's analysis never reads; calls no model."""
    path = Path(args.results).expanduser()
    path = path / RESULTS if path.is_dir() else path
    if not path.is_file():
        raise SystemExit("cost-bench: %s does not exist" % path)
    try:
        rows = read_jsonl(path)
        result = replay_stats.analyse(rows, args.seed, args.resamples)
    except ValueError as exc:
        raise SystemExit("cost-bench: cannot derive SM-2 from %s: %s" % (path, exc))
    if args.plot:
        plot = Path(args.plot).expanduser()
        same_file = plot.resolve() == path.resolve()
        if plot.exists():
            same_file = same_file or plot.samefile(path)
        if same_file:
            raise SystemExit("cost-bench: plot output must differ from the saved rows")
        plot.write_text(replay_stats.pareto_svg(result), encoding="utf-8")
    delegation = delegation_verdict.report(rows, args.break_even)
    sys.stdout.write(json.dumps(dict(result, delegation=delegation), indent=2, sort_keys=True) + "\n"
                     if args.json else replay_stats.render(result) + delegation_verdict.render(delegation))
    return 0


def raw_path(raw_dir, row):
    """Where `--raw` kept the CLI output for one row: the runner's `<task>-<arm>-<rep>.json`."""
    return Path(raw_dir) / ("%s-%s-%s.json" % (row.get("task"), row.get("arm"), row.get("rep")))


def backfill_rows(rows, raw_dir, config_dir=None, home=None):
    """(enriched rows, missing raw files). The diagnostic fields, derived after the fact.

    The launch environment is gone by now, so the fingerprint is of the directory named on the
    command line and the row says `fingerprint_source: backfill`: it is the caller's claim about
    which profile ran, not something the run recorded. A row whose raw output is missing or
    unreadable keeps its stream fields empty rather than borrowing another row's."""
    fingerprint = config_fingerprint(config_dir, home)
    label = config_label(None if config_dir in (None, "", INHERITED) else config_dir, home)
    out, missing = [], []
    for row in rows:
        new = dict(row, arm_config_dir=label, arm_fingerprint=fingerprint, fingerprint_source="backfill")
        new.setdefault("change_note", "")
        path = raw_path(raw_dir, row)
        try:
            parsed = parse_result(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            missing.append(path.name)
            for field in STREAM_FIELDS:
                new.setdefault(field, {} if field == "tool_counts" else None)
            out.append(new)
            continue
        new.update({field: parsed[field] for field in STREAM_FIELDS})
        if new.get("error"):
            new["cache_miss_ratio"] = None  # the same rule `run_one` applies to an errored run
        out.append(new)
    return out, missing


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path, rows):
    Path(path).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")


def cmd_backfill(args):
    results = Path(args.results).expanduser() / RESULTS
    if not results.is_file():
        raise SystemExit("cost-bench: %s does not exist" % results)
    config = Path(args.config_dir).expanduser() if args.config_dir else None
    rows, missing = backfill_rows(read_jsonl(results), Path(args.raw).expanduser(), config)
    write_jsonl(results.parent / ENRICHED, rows)
    if args.in_place:
        write_jsonl(results, rows)
    for name in missing:
        print("cost-bench: no raw output for %s" % name, file=sys.stderr)
    print("enriched %d row(s), %d without raw output, into %s"
          % (len(rows), len(missing), results.parent / ENRICHED))
    return 1 if missing else 0


def _text(command, **kwargs):
    return subprocess.run(command, check=True, stdout=subprocess.PIPE, universal_newlines=True,
                          **kwargs).stdout.strip()


def tag_version(repo, commit, ref):
    """The VERSION file at `commit`, or the ref itself when that commit has none."""
    done = _git(repo, "show", "%s:VERSION" % commit)
    return done.stdout.strip() if not done.returncode and done.stdout.strip() else ref


def declarations(tags, repo=None, effort=arms.DEFAULT_EFFORT):
    """`(bare declaration, [(tag, harness declaration)])` for a replay, each ref resolved to its
    full commit first, so a typo costs nothing and a moved tag is a new declaration. Every arm
    declares the one pinned `effort` it launches at."""
    repo, inputs = repo or ROOT, arms.qualification_inputs()
    harness = [(tag, arms.declaration("harness", inputs, {"ref": tag, "commit": resolve_tag(repo, tag)},
                                      effort=effort))
               for tag in tags]
    return arms.declaration("bare", inputs, effort=effort), harness


def refuse_candidate(tags):
    """The installed harness is a host profile, which no arm may read. A pre-release is named by
    its full commit instead, which builds into an image like any tag."""
    if not tags:
        raise SystemExit("cost-bench: name the harness with --tag: a release tag, or a full commit "
                         "for a pre-release candidate")
    if "candidate" in tags:
        raise SystemExit("cost-bench: --tag candidate ran the installed harness from the host profile, "
                         "which no arm may read now; name the commit to measure instead")


def verify_command(args, tasks):
    """`--verify-tasks`: every check proved in the bare arm's container, which is built first
    unless `--check-image` names one already built. Calls no model and needs no credential."""
    image = args.check_image
    if not image:
        bare = arms.build_arm(arms.declaration("bare", arms.qualification_inputs()),
                              Path(args.arms_dir) if args.arms_dir else Path(tempfile.mkdtemp(prefix="model-citizen-arms-")),
                              snapshot, tmp=args.tmp)
        image = bare["image"]
    parent = Path(tempfile.mkdtemp(prefix="cost-replay-verify-", dir=args.tmp))
    try:
        errors = verify_tasks(tasks, ROOT, parent, image, GATE_COMMANDS)
    finally:
        shutil.rmtree(str(parent), ignore_errors=True)
    for error in errors:
        print("cost-bench: " + error, file=sys.stderr)
    print("verified %d task(s) in %s, %d error(s)" % (len(tasks), image, len(errors)))
    return 1 if errors else 0


def cmd_replay(args):
    tasks = load_tasks(args.tasks)
    if args.task:
        tasks = [t for t in tasks if t["id"] in args.task]
    if not tasks:
        raise SystemExit("cost-bench: no contamination-safe replay tasks are eligible")
    if args.verify_tasks:
        return verify_command(args, tasks)
    protocol = experiment_protocol.admit(args.pre_registration, args.exploratory, ROOT, "cost-bench")
    tags = args.tag or []
    refuse_candidate(tags)
    if not args.model:
        raise SystemExit("cost-bench: --model is required, and every arm gets the same one")
    bare_decl, harness_decls = declarations(tags, effort=args.effort)  # every ref resolves before anything is built
    plan = schedule(tasks, args.reps)
    print("%d run(s) per tag, %d tag(s) (%s): %d task(s) x %s x %d rep(s), model %s at effort %s, "
          "%g USD per run, stop at %g USD reported per tag"
          % (len(plan), len(tags), ", ".join(tags), len(tasks), " + ".join(ARMS), args.reps,
             args.model, args.effort, args.run_cap, args.spend_cap))
    if args.allow_surface_drift:
        print("cost-bench: --allow-surface-drift: a set whose loaded surface moves runs on, and every "
              "row says surface_drift_allowed")
    if args.dry_run:  # nothing is built and nothing is spent
        print("  arm %s: %s" % (arms.label(bare_decl), arms.image_name(bare_decl)))
        for tag, decl in harness_decls:
            print("  tag %s: arm %s at %s: %s" % (tag, arms.label(decl), decl["harness"]["commit"],
                                                   arms.image_name(decl)))
            for task, rep, arm in plan:
                print("    %s rep %d %s" % (task["id"], rep, arm))
        return 0
    if not os.environ.get(arms.CREDENTIAL):
        raise SystemExit("cost-bench: %s is not set; every arm authenticates with it, passed by name"
                         % arms.CREDENTIAL)
    series_out = Path(args.out) if args.out else None
    arms_dir = Path(args.arms_dir) if args.arms_dir else Path(tempfile.mkdtemp(prefix="model-citizen-arms-"))
    print("cost-bench: arm declarations and manifests go in %s" % arms_dir)
    bare = arms.build_arm(bare_decl, arms_dir, snapshot, tmp=args.tmp)
    common = {"tasks": tasks, "plan": plan, "bare": bare, "arms_dir": arms_dir, "out": series_out,
              "prices": json.loads((ROOT / "policy" / "prices.json").read_text(encoding="utf-8")).get("models", {}),
              "cli_version": bare["manifest"].get("claude_code_version") or bare_decl["claude_code_version"],
              "client_env": arms.client_env({arms.CREDENTIAL: os.environ[arms.CREDENTIAL]}),
              "protocol": protocol}
    status = 0
    with arms.egress(bare["image"]) as net:
        for tag, decl in harness_decls:
            harness = arms.build_arm(decl, arms_dir, snapshot, tmp=args.tmp)
            status = max(status, replay_tag(tag, args, dict(common, network=net["network"], proxy=net["url"]),
                                            harness))
    return status


def replay_tag(tag, args, common, harness):
    """One tag's whole schedule, its results file and its history row. 1 when it stopped early.

    `harness` is the harness arm's record from `replay_arms.build_arm`. The per-rule fingerprint
    and attribution are resolved over a clone of that commit with an empty home, as the image has
    no configuration of the user's; see `arm_profile`."""
    tasks, commit = common["tasks"], harness["harness_commit"]
    version = tag_version(ROOT, commit, tag)
    # The arms are part of what is compared, so they rotate the series: a container run is not
    # comparable with one whose harness arm read a host profile.
    series = hashlib.sha256(Path(args.tasks).read_bytes() + args.model.encode()
                            + b"|container").hexdigest()[:8]
    out = (common["out"] or ROOT / "benchmarks" / version) / tag
    parent = Path(tempfile.mkdtemp(prefix="cost-profile-", dir=args.tmp))
    try:
        (parent / "home").mkdir()
        profile_root = snapshot(ROOT, commit, parent / "checkout")
        opts = {"repo": ROOT, "home": parent / "home", "profile_root": profile_root, "model": args.model,
                "tag": tag, "reps": args.reps, "run_cap": args.run_cap, "spend_cap": args.spend_cap,
                "prices": common["prices"], "arms": {"bare": common["bare"], "harness": harness},
                "network": common["network"], "proxy": common["proxy"], "client_env": common["client_env"],
                "stance_cost": args.stance_cost, "raw": args.raw, "tmp": args.tmp,
                "change_note": args.change_note or "", "skip_preflight": args.skip_preflight,
                "stamp": {"date": datetime.date.today().isoformat(), "model": args.model,
                          "cli_version": common["cli_version"],
                          "bucket": args.bucket, "predicted_ratio": args.predicted_ratio,
                          "harness_version": version, "harness_sha": commit,
                          "surface_drift_allowed": bool(args.allow_surface_drift),
                          "os": "linux container on %s %s" % (platform.system(), platform.release()),
                          **common["protocol"]}}
        out.mkdir(parents=True, exist_ok=True)
        opts["observation_dir"] = prepare_observation_dir(out)
        rows, stopped = replay(tasks, opts, out=out / RESULTS)
    finally:
        shutil.rmtree(str(parent), ignore_errors=True)
    if stopped:
        print("cost-bench: tag %s stopped at the spend cap after %d of %d run(s)"
              % (tag, len(rows), len(common["plan"])), file=sys.stderr)
    if rows and not experiment_protocol.writes_history(rows):
        print("cost-bench: an exploratory run is not a history row; results are in %s" % out, file=sys.stderr)
    elif rows and len(tasks) == len(load_tasks(args.tasks)) and not stopped:
        home_dir = Path(args.history_dir) if args.history_dir else ROOT / "benchmarks"
        home_dir.mkdir(parents=True, exist_ok=True)
        break_even = getattr(args, "break_even", delegation_verdict.BREAK_EVEN_CALLS)
        kept = upsert_history(home_dir / HISTORY.name, history_row(rows, series, break_even))
        (home_dir / HISTORY_MD.name).write_text(render_history(kept), encoding="utf-8")
        print(json.dumps(kept[-1], indent=2))
    else:
        print("cost-bench: a partial set is not a history row; results are in %s" % out, file=sys.stderr)
    return 1 if stopped else 0


def cmd_arms(args):
    """Build the arms, check that two builds of each agree, or prove the egress rule. No model call
    and no credential: the credential is never read here."""
    if args.action == "probe-egress":
        if not args.image:
            raise SystemExit("cost-bench: probe-egress needs --image, an arm image already built")
        lines, passed = arms.egress_probe(args.image)
        for line in lines:
            print(line)
        return 0 if passed else 1
    wanted = ARMS if args.arm == "both" else (args.arm,)
    if "harness" in wanted and not args.tag:
        raise SystemExit("cost-bench: the harness arm needs --tag, a release tag or a full commit")
    inputs = arms.qualification_inputs()
    decls = [arms.declaration("bare", inputs) if arm == "bare" else
             arms.declaration("harness", inputs, {"ref": args.tag, "commit": resolve_tag(ROOT, args.tag)})
             for arm in wanted]
    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="model-citizen-arms-"))
    status = 0
    for decl in decls:
        if args.action == "check":
            if not arms.two_build_check(decl, out, snapshot, dry_run=args.dry_run, tmp=args.tmp):
                status = 1
            continue
        if args.dry_run:
            print(" ".join(arms.build_command(decl, "<context>", arms.image_name(decl))))
            continue
        record = arms.build_arm(decl, out, snapshot, tmp=args.tmp)
        print("%s: %s %s, declaration %s, manifest %s"
              % (record["label"], record["image"], record["image_id"], record["declaration_sha256"],
                 record["manifest_sha256"]))
    if not args.dry_run:
        print("declarations and manifests in %s" % out)
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    static = sub.add_parser("static", help="count the always-loaded layer and the session listings")
    mode = static.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="refresh %s" % STATIC.as_posix())
    mode.add_argument("--check", action="store_true", help="print each file's delta and fail on "
                      "unexplained growth of the total")
    run = sub.add_parser("replay", help="run the pinned tasks in a bare and a harness container; "
                         "spends usage")
    run.add_argument("--tasks", default=str(ROOT / TASKS))
    run.add_argument("--task", action="append", help="run only this task id; repeatable")
    run.add_argument("--tag", action="append", help="the harness ref the harness arm is built from: a "
                     "release tag, or a full commit for a pre-release candidate; repeatable, each tag "
                     "writes its own history row. Required")
    run.add_argument("--model", help="the one model id every arm runs")
    run.add_argument("--reps", type=int, default=DEFAULT_REPS, help="trials per task and arm")
    run.add_argument("--effort", choices=arms.EFFORT_LEVELS, default=arms.DEFAULT_EFFORT,
                     help="the reasoning effort every arm launches at, passed as --effort and recorded "
                     "in each arm's declaration and on every row; default %(default)s")
    run.add_argument("--allow-surface-drift", action="store_true", help="run on when a run's loaded "
                     "surface differs from its arm's first run, instead of stopping the set; every "
                     "row of the set says surface_drift_allowed")
    run.add_argument("--run-cap", type=float, default=RUN_CAP_USD, help="--max-budget-usd per run; soft")
    run.add_argument("--spend-cap", type=float, default=SPEND_CAP_USD, help="stop before passing "
                     "this; it applies to each tag's schedule on its own")
    run.add_argument("--stance-cost", help="HARNESS_STANCE_COST for the harness arm")
    run.add_argument("--bucket", default="", help="the one change this run measures, e.g. A; names the "
                     "history row so several buckets can share a day and a commit")
    run.add_argument("--predicted-ratio", type=float, help="the ratio the plan predicts for this bucket; "
                     "stored beside the measured one so a miss is visible in the file")
    run.add_argument("--break-even", type=float, default=delegation_verdict.BREAK_EVEN_CALLS,
                     help="absorbed calls above which a task should delegate, for the history row's "
                     "delegation verdict; default FR-34's %(default)s, hypothetical")
    run.add_argument("--history-dir", help="directory for history.jsonl and history.md; "
                     "default benchmarks/")
    run.add_argument("--change-note", default="", help="what changed since the last run of this "
                     "bucket; stored on every row and on the history row")
    run.add_argument("--out", help="results directory, one subdirectory per tag; default "
                     "benchmarks/<harness version>")
    run.add_argument("--arms-dir", help="where each arm's declaration and manifest are written; "
                     "default a new temporary directory, named when the run starts")
    run.add_argument("--raw", help="keep each run's raw CLI output here; never commit it")
    run.add_argument("--tmp", help="parent for the throwaway clones and build contexts; Docker must "
                     "be able to mount it")
    run.add_argument("--verify-tasks", action="store_true", help="prove every check in the bare arm's "
                     "container, building it first; calls no model")
    run.add_argument("--check-image", help="with --verify-tasks, an arm image already built to run the "
                     "gate and the checks in, instead of building the bare arm")
    run.add_argument("--skip-preflight", action="store_true", help="do not run each arm's gate in "
                     "its own container first; rows then say preflight: skipped")
    run.add_argument("--dry-run", action="store_true", help="print the arms and the schedule and stop; "
                     "builds nothing")
    evidence = run.add_mutually_exclusive_group()
    evidence.add_argument("--pre-registration", help="the committed, dated plan this run answers, "
                          "filled from docs/pre-registration-template.md; required unless --exploratory")
    evidence.add_argument("--exploratory", action="store_true", help="run without a pre-registration; "
                          "every row is labelled exploratory and no history row is written")
    build = sub.add_parser("arms", help="build the replay arms, check two builds agree, or prove the "
                           "egress rule; calls no model")
    build.add_argument("action", choices=("build", "check", "probe-egress"))
    build.add_argument("--arm", choices=ARMS + ("both",), default="both")
    build.add_argument("--tag", help="the harness ref for the harness arm")
    build.add_argument("--image", help="for probe-egress: the arm image to probe from")
    build.add_argument("--out", help="where declarations and manifests go; default a new temporary directory")
    build.add_argument("--tmp", help="parent for the build contexts")
    build.add_argument("--dry-run", action="store_true", help="print the commands and build nothing")
    summ = sub.add_parser("summarise", help="SM-2's verdict, intervals and Pareto view from saved rows; "
                          "calls no model")
    summ.add_argument("--results", required=True, help="a %s, or the directory holding one" % RESULTS)
    summ.add_argument("--seed", type=int, default=replay_stats.SEED, help="the bootstrap's seed")
    summ.add_argument("--resamples", type=int, default=replay_stats.RESAMPLES, help="bootstrap resamples")
    summ.add_argument("--json", action="store_true", help="print the result as JSON")
    summ.add_argument("--break-even", type=float, default=delegation_verdict.BREAK_EVEN_CALLS,
                      help="absorbed calls above which a task should delegate; default FR-34's "
                      "%(default)s, hypothetical")
    summ.add_argument("--plot", metavar="SVG", help="write the cost-versus-pass-rate plot as a standalone SVG")
    back = sub.add_parser("backfill", help="derive the diagnostic fields for rows already written")
    back.add_argument("--results", required=True, help="directory holding %s" % RESULTS)
    back.add_argument("--raw", required=True, help="directory of the runs' raw CLI output")
    where = back.add_mutually_exclusive_group()
    where.add_argument("--config-dir", help="the profile those runs used; its files are measured now")
    where.add_argument("--inherited", action="store_true", help="those runs inherited ~/.claude (default)")
    back.add_argument("--in-place", action="store_true", help="also rewrite %s" % RESULTS)
    args = parser.parse_args(argv)
    if args.command == "replay":
        return cmd_replay(args)
    if args.command == "backfill":
        return cmd_backfill(args)
    if args.command == "summarise":
        return cmd_summarise(args)
    if args.command == "arms":
        return cmd_arms(args)
    if args.check:
        now = measure(ROOT)
        for line in file_deltas(ROOT, now):
            print("cost-bench: " + line)
        errors = check(ROOT, now)
        for error in errors:
            print("cost-bench: " + error, file=sys.stderr)
        return 1 if errors else 0
    text = json.dumps(measure(ROOT), indent=2) + "\n"
    if args.write:
        (ROOT / STATIC).parent.mkdir(exist_ok=True)
        (ROOT / STATIC).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

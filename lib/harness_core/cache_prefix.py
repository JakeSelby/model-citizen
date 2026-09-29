"""Cache-prefix stability, read back out of the usage ledger.

`primitives/rules/cache-hygiene.md` asks a session to hold its cached prefix mid-task, and
nothing reported whether it did. A change to the tool set, the MCP list, the model or the
effort dial turns the next turn's cache reads into cache writes, and the only visible symptom
is a larger bill. The figure here is `cache_write / (cache_read + cache_write)` over a
session's **own** context: the share of its prefix the provider had to re-write rather than
serve.

It measures and does not enforce. Nothing in this module denies, warns or blocks a prefix
change; `harness usage --by prefix` is retrospective and read-only.

Four things this figure refuses to guess at:

- **A subagent's tokens are not the session's prefix.** A Claude Code session row folds its
  subagents' tokens into its own (`claude/hooks/usage-log.py`, `daily`), and every fan-out
  writes a fresh prefix of its own. Left folded in, a session that held its context perfectly
  across six spawns reads as one that re-bought a quarter of it. So the subagent rows' cache
  fields are subtracted from the session's before the ratio is taken, and a session whose
  `subagents` count is larger than the rows found for it, or whose subtraction goes negative,
  reports `unknown` rather than a corrected-looking number.
- **The step is not measured through folded tokens either.** The `days` slices fold the same
  subagent tokens in per day, and a subagent row carries no `days` map to subtract, so a
  session that spawned anything reports no step at all rather than the spike its fan-out day
  would fabricate.
- **Zero is never used for unknown.** A row carrying no cache fields, or two zeroes, is
  reported as `unknown`, and so is every row from a runtime that exports no cache-write figure
  at all (`adapters/codex/capabilities.json`). A 0% miss ratio would read as a session that
  held its prefix perfectly rather than as a runtime that cannot say. On a runtime that does
  report writes, reads against zero writes are exactly that: a day that held its prefix.
- **The step is located no finer than the ledger records.** The only within-session slicing an
  existing row carries is its `days` map, which holds the same token fields and that day's turn
  count. So the step is found between day slices and named by the turn index the stepping slice
  opens on, which is the finest index the ledger can honestly support without a new event.
"""

# The rise in miss ratio between two consecutive slices that counts as a step rather than
# drift. A held prefix wanders by a few points as turns differ in size; twenty points is the
# shape of a prefix that was re-written, not one that grew.
MISS_STEP = 0.2

# Runtimes that report cached reads but no cache-write figure at all, so no ratio over their
# rows means anything. Codex writes `cache_read` from `cached_input_tokens` and leaves
# `cache_write` unreported; where it lands as a zero rather than as null, a ratio taken over
# the pair would read as a perfectly held prefix.
NO_CACHE_WRITES = ("codex",)


def _count(row, name):
    """A row's token field as a non-negative int, or None when it is absent or unreadable."""
    value = row.get(name)
    if value is None:
        return None
    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return max(value, 0)


def _ratio(read, write, runtime=""):
    """`write / (read + write)`, or None when the pair cannot state one."""
    if runtime in NO_CACHE_WRITES:
        return None
    if read is None or write is None:
        return None
    served = read + write
    return write / served if served else None


def miss_ratio(row, runtime=None):
    """The miss ratio over a row's cache fields **as they stand**, or None.

    Used directly on a `days` slice, which is why `runtime` can be passed in: a slice carries
    no runtime of its own and inherits its row's. A session row's own fields include its
    subagents', so `figure` corrects them through `own_cache` first rather than calling this.
    """
    if runtime is None:
        runtime = row.get("runtime") or ""
    return _ratio(_count(row, "cache_read"), _count(row, "cache_write"), runtime)


def folds_subagents(row):
    """Whether this row's totals already hold its subagents' tokens.

    The same rule `pricing.session_children` prices by: a Claude Code session row folds them
    in, a Codex session row does not, and a delegated run's row holds only its own.
    """
    return (row.get("kind") or "session") == "session" and row.get("runtime") != "codex"


def own_cache(row, children=()):
    """A session's own cache figures, with folded subagent tokens taken back out.

    Returns `(None, None)` when the correction cannot be made honestly: a field missing on the
    session or on any child, fewer child rows than the session says it spawned, or a
    subtraction that goes negative. Each of those means the remainder is not the session's
    prefix, and a number that looked corrected would be worse than none.
    """
    read, write = _count(row, "cache_read"), _count(row, "cache_write")
    if read is None or write is None or not folds_subagents(row):
        return read, write
    spawned = row.get("subagents") if isinstance(row.get("subagents"), int) else 0
    if max(spawned, 0) > len(children):
        return None, None
    for child in children:
        child_read, child_write = _count(child, "cache_read"), _count(child, "cache_write")
        if child_read is None or child_write is None:
            return None, None
        read -= child_read
        write -= child_write
    if read < 0 or write < 0:
        return None, None
    return read, write


def step(row, children=()):
    """The sharpest rise in miss ratio between consecutive day slices, or None.

    Returns `{"turn", "day", "before", "after"}`, where `turn` is the 1-based index of the
    first turn of the slice whose ratio rose, and the two ratios are the stepping slice's and
    that of the slice before it. When a session stepped more than once the sharpest rise is
    the one reported.

    A slice that cannot state a ratio breaks the chain rather than being compared through: the
    printed `before -> after` reads as one slice against the slice before it, so comparing
    across a gap would name two days that were never adjacent in the figure.

    A session that spawned anything gets no step. Its slices fold its subagents' tokens in per
    day and a subagent row carries no `days` map to subtract, so the only step those slices
    could show is the fan-out.
    """
    days = row.get("days")
    if not isinstance(days, dict):
        return None
    spawned = row.get("subagents") if isinstance(row.get("subagents"), int) else 0
    if folds_subagents(row) and (max(spawned, 0) or children):
        return None
    runtime = row.get("runtime") or ""
    found, turn, previous = None, 1, None
    for day in sorted(days):
        slice_ = days[day]
        ratio = miss_ratio(slice_, runtime) if isinstance(slice_, dict) else None
        if ratio is None:
            previous = None
        else:
            if previous is not None and ratio - previous >= MISS_STEP:
                rise = ratio - previous
                if found is None or rise > found["rise"]:
                    found = {"turn": turn, "day": day, "before": previous,
                             "after": ratio, "rise": rise}
            previous = ratio
        if isinstance(slice_, dict):
            turn += _count(slice_, "turns") or 0
    if found is not None:
        found.pop("rise", None)
    return found


def figure(row, children=()):
    """One session's cache-prefix line: identity, its own cache totals, ratio and step."""
    models = row.get("models") if isinstance(row.get("models"), list) else []
    if not models and row.get("model"):
        models = [row["model"]]
    read, write = own_cache(row, children)
    spawned = row.get("subagents") if isinstance(row.get("subagents"), int) else 0
    return {
        "session_id": row.get("session_id") or "(unknown)",
        "repo": row.get("repo") or "(no repo)",
        "models": [name for name in models if isinstance(name, str) and name],
        "ended": row.get("ended") or "",
        "turns": _count(row, "turns"),
        "subagents": max(spawned, 0),
        "cache_read": read,
        "cache_write": write,
        "ratio": _ratio(read, write, row.get("runtime") or ""),
        "step": step(row, children),
        # Said rather than inferred from an empty step: no step found and no step measurable
        # are different answers, and the report prints them differently.
        "step_blocked": folds_subagents(row) and bool(max(spawned, 0) or children),
    }


def figures(ledger, cutoff=""):
    """The per-session figures for the rows in the window, oldest session first.

    Only session rows are reported, and the whole ledger is indexed for children first: a
    subagent row outside the window is still folded into its parent's totals and still has to
    come back out.
    """
    children = {}
    for row in ledger:
        if not isinstance(row, dict):
            continue
        if row.get("kind") == "subagent" and row.get("runtime") != "codex" and row.get("session_id"):
            children.setdefault(row["session_id"], []).append(row)
    found = []
    for row in ledger:
        if not isinstance(row, dict) or (row.get("kind") or "session") != "session":
            continue
        if (row.get("ended") or "") < cutoff:
            continue
        found.append(figure(row, children.get(row.get("session_id") or "", ())))
    found.sort(key=lambda item: (item["ended"], item["session_id"]))
    return found


def _cell(value, width, unknown="unknown"):
    return "{:>{}}".format(unknown if value is None else "{:,}".format(value), width)


def summary(ledger, cutoff=""):
    """Structured prefix figures and totals, shared by text and JSON renderers."""
    found = figures(ledger, cutoff)
    return {
        "groups": found,
        "totals": {
            "sessions": len(found),
            "stepped": sum(1 for item in found if item["step"] is not None),
            "unknown": sum(1 for item in found if item["ratio"] is None),
        },
    }


def report(ledger, cutoff, days, say):
    """`harness usage --by prefix`: the miss ratio per session, and where it jumped."""
    data = summary(ledger, cutoff)
    found = data["groups"]
    if not found:
        say("no sessions recorded in the last {} day(s)".format(days))
        return 0
    head = ("{:<20}{:<16}{:<22}{:>7}{:>14}{:>14}{:>9}  {}"
            .format("session", "repo", "models", "turns", "own_read", "own_write",
                    "miss", "step"))
    say("The miss ratio measures cache-prefix stability; nothing here denies a prefix change.")
    say("Subagent tokens are subtracted: the figure is the session's own prefix.")
    say(head)
    say("-" * len(head))
    for item in found:
        if item["ratio"] is None:
            ratio = "{:>9}".format("unknown")
        else:
            ratio = "{:>9.0%}".format(item["ratio"])
        jump = item["step"]
        if jump is not None:
            detail = ("turn {} ({}): {:.0%} -> {:.0%}"
                      .format(jump["turn"], jump["day"], jump["before"], jump["after"]))
        else:
            detail = "not measurable (subagents)" if item["step_blocked"] else "-"
        say("{:<20}{:<16}{:<22}{}{}{}{}  {}"
            .format(item["session_id"][:19], item["repo"][:15],
                    ("+".join(item["models"]) or "(unknown)")[:21],
                    _cell(item["turns"], 7, "?"), _cell(item["cache_read"], 14),
                    _cell(item["cache_write"], 14), ratio, detail))
    say("-" * len(head))
    say("{} session(s), {} with a mid-session step, {} reporting no cache figures"
        .format(data["totals"]["sessions"], data["totals"]["stepped"],
                data["totals"]["unknown"]))
    return 0

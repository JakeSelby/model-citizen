#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""List prices for ledger rows: the one code path `harness usage` and the exporter both use.

The dollar figure a report prints and the dollar figure an exported row carries have to be the
same number, so the rates, the id normalisation and the session/subagent join live here and are
called from both sides rather than implemented twice.

This module sits beside `usage-log.py` for the reason `telemetry.py` does: the hook is also a
standalone script reached through `~/.claude/hooks/harness`, from which nothing above the hook
directory resolves. `bin/harness` loads it the way it loads the detectors, and both entry points
land on the same `policy/prices.json` — the CLI from its checkout root, this module from its own
real path, which follows the `claude/hooks -> ../policy/hooks` symlink back to the checkout.

Nothing here raises for a price it cannot find: an unpriceable row is `None`, never zero. An
understated figure is worse than an absent one, because nothing on the line says it is short.
"""
import json
import os
import re
import time
from pathlib import Path

# The four columns every entry must carry to price anything at all; a rate missing one of them
# leaves the model unpriced rather than charging the part it knows about.
RATE_FIELDS = ("input", "output", "cache_read", "cache_write")
TOKEN_TIERS = ("cache_write_5m", "cache_write_1h")
# A price read a quarter ago is a guess. `doctor` says so rather than a report quietly drifting.
PRICE_STALE_DAYS = 90
# A release suffix names when a model shipped, not which model it is: a date stamp, or a cloud
# reseller's version tag. Only these are dropped on the way to a table key. A word after the
# family name — `-pro`, `-mini` — is a different model at a different price and stays.
RELEASE_SUFFIX = re.compile(r"(?:[-@](?:20\d{6}|20\d{2}-\d{2}-\d{2})|-v\d+(?::\d+)?)$")


def prices_path():
    """`policy/prices.json` in the checkout this file really lives in.

    Resolved from the real path so the hook, reached through two symlinks, reads the same file
    the CLI reads from its checkout root.
    """
    return Path(os.path.realpath(__file__)).parent.parent / "prices.json"


def shipped_prices(path=None):
    """The price file as written, or an empty table when it is missing or unreadable.

    A broken price file costs the report its dollar column and nothing else: `usage` still
    counts tokens, which is what it did before prices existed.
    """
    try:
        with open(str(prices_path() if path is None else path), encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        return {}
    models = data.get("models") if isinstance(data, dict) else None
    return models if isinstance(models, dict) else {}


def load_prices(cfg, path=None):
    """The shipped table with the user's `prices` block merged over it, field by field.

    Merged per model id rather than wholesale, so an override that names one rate keeps the
    shipped `as_of` and `source` for the rest of the entry and a new model can be added
    outright. Ids are normalised on both sides, so an override spelled in mixed case, with a
    reseller prefix or with a context-window suffix still reaches the entry it means to replace.
    """
    table = dict((normalise_model(key), value) for key, value in shipped_prices(path).items()
                 if isinstance(value, dict))
    overrides = cfg.get("prices") if isinstance(cfg, dict) and isinstance(cfg.get("prices"), dict) else {}
    for key, value in overrides.items():
        if not isinstance(value, dict):
            continue
        name = normalise_model(key)
        merged = dict(table.get(name) or {})
        merged.update(value)
        table[name] = merged
    return table


def normalise_model(model):
    """One spelling of a model id: lower case, no vendor prefix, no window or release suffix.

    A ledger holds the id each runtime reported, and the same model arrives several ways:
    bare, as `<vendor>.<family>-<date>-v1:0` on a cloud reseller, as `<family>@<date>` on
    another, and with a context-window suffix in brackets in a long-context session. The
    window suffix is dropped because the provider prices the larger window at the standard
    rate, and the release suffix because a date stamp names when a model shipped rather than
    which model it is. What is left is matched exactly, never by prefix: see `price_key`. No
    model name is written here: `policy/prices.json` holds the ids.
    """
    name = (model or "").strip().lower()
    name = re.sub(r"\[[^\]]*\]", "", name).split("/")[-1]
    while True:
        head, dot, rest = name.partition(".")
        if not (dot and rest and head.isalpha()):
            break
        name = rest
    while True:
        trimmed = RELEASE_SUFFIX.sub("", name)
        if trimmed == name:
            break
        name = trimmed
    return name.strip("-").strip()


def usable_rate(entry):
    """An entry's four rates as floats, or None when any of them is missing or not a number."""
    if not isinstance(entry, dict):
        return None
    rate = {}
    for field in RATE_FIELDS + TOKEN_TIERS:
        value = entry.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            if field in RATE_FIELDS:
                return None
            continue
        rate[field] = float(value)
    return rate


def price_key(table, model):
    """The table key governing one model id: its normalised form, listed exactly, or "".

    Nothing resolves by prefix. A variant of a listed family is a different model at a
    different price — `gpt-5.5-pro` is $30/$180 where `gpt-5.5` is $5/$30 — so an id the
    table does not name is unpriced rather than billed at its nearest listed relative. To
    price one, add it to `policy/prices.json` or override it under `prices` in `config.json`.
    """
    name = normalise_model(model)
    return name if name in table else ""


def price_for(table, model):
    """The rate governing one model id, or None when no entry carries a usable one."""
    key = price_key(table, model)
    return usable_rate(table.get(key)) if key else None


def row_tokens(row, rate=None):
    """A row's four token counts, or None when the row cannot be priced from them.

    A row marked `partial` — a transcript read to a budget, or a Codex snapshot carrying only a
    total — is unpriced, never priced at what was readable: an understated dollar figure is
    worse than an absent one, because nothing on the line says it is short.

    An unreported count against a rate of zero is read as zero rather than as unknown, because
    it cannot change the bill either way. That is what makes a Codex row priceable: its rollout
    names no cache-write figure at all, and OpenAI charges nothing for one.
    """
    if row.get("partial"):
        return None
    tokens = {}
    for name in RATE_FIELDS:
        value = row.get(name)
        if value is None and rate is not None and not rate.get(name):
            value = 0
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        tokens[name] = max(value, 0)
    for name in TOKEN_TIERS:
        value = row.get(name)
        if not isinstance(value, bool) and isinstance(value, int):
            tokens[name] = max(value, 0)
    return tokens


def tokens_cost(tokens, rate):
    """USD for one set of token counts at one rate.

    Cache writes are charged tier by tier only when the row's tiers add up to its cache-write
    total: a row that reports no split, or whose split disagrees with its total, is charged
    whole at `cache_write`. Checking the sum rather than the keys' presence means a row whose
    tiers are both zero because the runtime reported none is not read as writes at the 5-minute
    rate.
    """
    total = (tokens["input"] * rate["input"] + tokens["output"] * rate["output"]
             + tokens["cache_read"] * rate["cache_read"])
    write = tokens["cache_write"]
    tiers = dict((name, tokens.get(name) or 0) for name in TOKEN_TIERS)
    if write and sum(tiers.values()) == write:
        for name in TOKEN_TIERS:
            total += tiers[name] * rate.get(name, rate["cache_write"])
    else:
        total += write * rate["cache_write"]
    return total / 1000000


def breakdown_cost(row, table):
    """USD for a row from its per-model breakdown, or None when the map cannot carry it.

    The largest sessions are the ones that switched models, and their totals alone name several
    rates and no split between them. `usage-log.py` records the split as `by_model`, checked
    against the row's own totals before it is written, so this is a sum of parts and not an
    allocation. Any part that cannot be priced leaves the whole row unpriced.

    A part named for a harness-generated turn — `<synthetic>`, and anything else the transcript
    brackets — is no model and has no rate. It is skipped when it spent nothing, which is the
    only case where the real models still account for every token.
    """
    parts = row.get("by_model")
    if not isinstance(parts, dict) or not parts:
        return None
    total = 0.0
    for name, part in parts.items():
        if not isinstance(name, str) or not isinstance(part, dict):
            return None
        if name.startswith("<"):
            if any(part.get(field) for field in RATE_FIELDS):
                return None
            continue
        rate = price_for(table, name)
        if rate is None:
            return None
        tokens = row_tokens(dict(part, partial=row.get("partial")), rate)
        if tokens is None:
            return None
        total += tokens_cost(tokens, rate)
    return total


def row_model(row, excluded=None):
    """The one model a row's tokens were spent on, or None when the row names several.

    A session row names every model it ran on and carries one set of totals, so a session that
    switched models mid-way cannot be split and is unpriced. Its subagents' models are taken off
    the list first, since their tokens are being priced separately — unless that would empty it,
    which is the session that ran the same model its subagent did.
    """
    models = [m for m in (row.get("models") or []) if isinstance(m, str) and m]
    if excluded:
        models = [m for m in models if normalise_model(m) not in excluded] or models
    if not models and isinstance(row.get("model"), str) and row["model"]:
        models = [row["model"]]
    return models[0] if len(models) == 1 else None


def row_cost(row, table, children=None):
    """USD for one ledger row, or None when it cannot be priced.

    A row that carries a per-model breakdown is priced from it and from nothing else, which is
    the only way a session that switched models can be priced at all; see `breakdown_cost`.
    What follows is for the rows written before that map existed.

    A Claude Code session's totals already include its subagents', which ran on their own models
    at their own rates — the oracle session priced in the tests spent 85% of its dollars on
    1-hour cache writes at the parent's rate and the rest on a subagent's 5-minute writes at
    another. So the children's tokens come off the parent's totals, each child is priced at its
    own model, and the two are added. A child that cannot be priced leaves the whole session
    unpriced, because the remainder alone would read as the session's bill.

    A row whose totals do not cover its children — an old row written before subagent tokens
    were captured — is priced alone, exactly as its tokens are reported alone. Coverage is
    decided on the counts, before anything is priced, so an unpriceable child does not turn a
    parent that never held its tokens into an unpriced row.
    """
    if row.get("partial"):
        return None
    # A breakdown is the whole bill: for Claude Code it is cut from the same map the totals are
    # summed over, subagent records included, so nothing is added to it.
    # A breakdown with an unpriced part is unpriced: falling back to the totals would charge
    # that part at whichever single model the row names.
    if isinstance(row.get("by_model"), dict) and row["by_model"]:
        return breakdown_cost(row, table)
    joined = None
    child_cost = 0.0
    excluded = set()
    base = row_tokens(row) if children else None
    parts = [row_tokens(child) for child in children or []]
    if base is not None and all(part is not None for part in parts) \
            and all(base[name] >= sum(part[name] for part in parts) for name in RATE_FIELDS):
        joined = dict(base)
        for part in parts:
            for name in RATE_FIELDS + TOKEN_TIERS:
                if name in joined and name in part:
                    joined[name] = max(joined[name] - part[name], 0)
        for child in children or []:
            cost = row_cost(child, table)
            if cost is None:
                return None
            child_cost += cost
        excluded = set(normalise_model(c.get("model") or "") for c in children or [])
    rate = price_for(table, row_model(row, excluded) or "")
    if rate is None:
        return None
    tokens = joined if joined is not None else row_tokens(row, rate)
    if tokens is None:
        return None
    return tokens_cost(tokens, rate) + child_cost


def session_children(rows):
    """Claude Code subagent rows keyed by the session whose totals already hold their tokens."""
    children = {}
    for row in rows:
        if row.get("kind") == "subagent" and row.get("runtime") != "codex" and row.get("session_id"):
            children.setdefault(row["session_id"], []).append(row)
    return children


def row_children(row, children):
    """The subagent rows that belong inside this row's bill, or None when none can.

    The rule the report applies: only a Claude Code session's totals already hold its subagents'
    tokens. A Codex subagent row and a role-run worker row are each priced alone, exactly as
    they are reported alone.
    """
    if (row.get("kind") or "session") != "session" or row.get("runtime") == "codex":
        return None
    return children.get(row.get("session_id") or "")


def newest_as_of(table):
    """The most recent `as_of` in the table, or "" when no entry carries one."""
    dates = [e["as_of"] for e in table.values()
             if isinstance(e, dict) and isinstance(e.get("as_of"), str) and e["as_of"]]
    return max(dates) if dates else ""


def row_models(row, children=None):
    """Every model id a row was priced through, its own and its children's."""
    names = []
    parts = row.get("by_model")
    if isinstance(parts, dict):
        names.extend(name for name in parts if isinstance(name, str) and not name.startswith("<"))
    names.extend(m for m in (row.get("models") or []) if isinstance(m, str))
    if isinstance(row.get("model"), str):
        names.append(row["model"])
    for child in children or []:
        names.extend(row_models(child))
    return [name for name in names if name]


def row_as_of(row, table, children=None):
    """The newest `as_of` among the entries that priced this row, or "" when none carries one.

    The date belongs to the figure, not to the file: a row priced from one family's entry is
    stamped with that entry's date even when a newer entry for another model sits beside it.
    """
    dates = []
    for name in row_models(row, children):
        entry = table.get(price_key(table, name))
        if isinstance(entry, dict) and isinstance(entry.get("as_of"), str) and entry["as_of"]:
            dates.append(entry["as_of"])
    return max(dates) if dates else ""


def priced(rows, table):
    """`(usd, as_of)` per row, in the order given: the figures `harness usage` reports.

    A row that cannot be priced is `(None, "")` — never `(0.0, …)`. Sessions are priced with
    their subagent rows in hand, over the whole list, so the figure on a Claude Code session row
    already includes its subagents and the two must never be summed.
    """
    rows = [r for r in rows if isinstance(r, dict)]
    children = session_children(rows)
    out = []
    for row in rows:
        kids = row_children(row, children)
        cost = row_cost(row, table, kids)
        out.append((cost, row_as_of(row, table, kids) if cost is not None else ""))
    return out


def price_age_days(as_of, now=None):
    """Whole days between an `as_of` date and today, or None when the date will not parse."""
    try:
        stamp = time.mktime(time.strptime(as_of, "%Y-%m-%d"))
    except (ValueError, TypeError):
        return None
    return int((time.time() if now is None else now) - stamp) // 86400

"""The adherence report: how often each recommendation is followed, and what following it saves.

Two figures per recommendation kind, never pooled (AD-12):

- **The adherence rate**, labelled measured: followed over judged (followed plus not followed)
  from the adherence ledger (AD-23), with a 95% Wilson interval, per kind and profile
  fingerprint. A row with no fingerprint also carries the unattributed qualifier.
- **The if-followed saving**, labelled soft estimate, from a named estimator. It is computed only
  for emissions the ledger answers `not_followed`, so the ledger keeps the one definition of
  following. A kind with no estimator is unmeasured.

**Carried-context reprice**, the estimator for `fresh-session`. For an emission in session S at
prompt t, `C` is the context size of S's last call at or before prompt t, `B` that of S's first
call, and `K = max(0, C - B)` the context carried past a fresh start. Nothing measures a handoff,
so the fresh session starts from S's own first-call context and the saving reads "at most". The
horizon is S's calls after prompt t, up to its first compaction, after which both paths hold a
similar context. Each horizon call keeps its output and uncached input and is repriced: the first
is a cold start that writes what it would have read less `K` and reads nothing; a later call that
is not a cache rebuild reads `K` fewer tokens; a rebuild writes `min(K, rewritten)` fewer, keeping
its tier split. The saving is actual minus counterfactual, signed and never clipped: a short
remainder can cost more after a reset than it saves. An unpriced model anywhere in the horizon
makes the emission unpriced, counted apart and never priced at zero (FR-24), and an emission whose
session calls cannot be read is counted as transcript missing, neither dropped nor priced.

Every printed or exported number is a `Figure` carrying its label; the renderer refuses a bare
number and `total` refuses to add figures with different labels. The module is read-only: it never
settles an emission, so a pending one stays pending. Hook modules (`adherence`, `pricing`) are
passed in by the caller, which reaches them the way `bin/harness` reaches every hook module.
"""

MEASURED = "measured"
SOFT_ESTIMATE = "soft estimate"
UNMEASURED = "unmeasured"
UNATTRIBUTED = "unattributed"
LABELS = (MEASURED, SOFT_ESTIMATE, UNMEASURED)

ESTIMATOR = "carried-context reprice"
ASSUMPTION = ("assumes the same later turns in a session reset at the nudge, with no handoff; "
              "a saving of at most this")
# The recommendation kinds an estimator prices, by kind. A kind absent here is unmeasured.
ESTIMATORS = {"fresh-session": ESTIMATOR}
HANDOFF_TOKENS = 0  # the counterfactual session's extra starting context; nothing measures one
COMPACTION = "compaction"  # the rebuild cause that ends an estimator's horizon
COUNTS = ("emitted", "followed", "not_followed", "unknown", "pending")
FOOTER = ("Host sessions are exploratory under docs/evidence-standard.md item 11 and cannot be "
          "cited as evidence.")


class MixedLabels(ValueError):
    """Figures with different estimand labels were combined."""


class Figure(object):
    """One reported number and the estimand it estimates.

    `value` None is unknown, never zero. A measured figure names the fingerprint it covers; a soft
    estimate names its estimator; `interval` is `(low, high)` or None.
    """

    __slots__ = ("value", "label", "n", "interval", "fingerprint", "estimator", "qualifiers",
                 "note")

    def __init__(self, value, label, n=None, interval=None, fingerprint=None, estimator=None,
                 qualifiers=(), note=None):
        if label not in LABELS:
            raise ValueError("unknown estimand label %r" % (label,))
        if label == SOFT_ESTIMATE and not estimator:
            raise ValueError("a soft estimate names its estimator")
        self.value = value
        self.label = label
        self.n = n
        self.interval = tuple(interval) if interval is not None else None
        self.fingerprint = fingerprint
        self.estimator = estimator
        self.qualifiers = tuple(qualifiers)
        self.note = note

    def as_dict(self):
        low, high = self.interval if self.interval is not None else (None, None)
        return {"value": self.value, "label": self.label, "n": self.n,
                "interval": None if self.interval is None else {"low": low, "high": high},
                "fingerprint": self.fingerprint, "estimator": self.estimator,
                "qualifiers": list(self.qualifiers), "note": self.note}


def total(figures, estimator=None):
    """The sum of figures sharing one label; MixedLabels when they do not.

    An unknown value makes the sum unknown. Soft estimates must also share their estimator.
    """
    figures = list(figures)
    labels = set(figure.label for figure in figures)
    if len(labels) > 1:
        raise MixedLabels("cannot add %s figures" % " and ".join(sorted(labels)))
    if not figures:
        raise ValueError("an empty total has no label")
    label = figures[0].label
    estimators = set(figure.estimator for figure in figures)
    if label == SOFT_ESTIMATE and len(estimators) > 1:
        raise MixedLabels("cannot add soft estimates from different estimators")
    values = [figure.value for figure in figures]
    value = None if any(v is None for v in values) else sum(values)
    counts = [figure.n for figure in figures]
    n = None if any(c is None for c in counts) else sum(counts)
    return Figure(value, label, n=n, estimator=figures[0].estimator)


def _measured(value, fingerprint, n=None, interval=None):
    return Figure(value, MEASURED, n=n, interval=interval, fingerprint=fingerprint or None,
                  qualifiers=() if fingerprint else (UNATTRIBUTED,))


def _emissions(rows, cutoff=None):
    """Emitted rows with an id, at or after `cutoff` (an ISO `...Z` stamp) when one is given."""
    return [row for row in rows
            if row.get("kind") == "emitted" and isinstance(row.get("adherence_id"), str)
            and (cutoff is None or str(row.get("ts") or "") >= cutoff)]


def _outcome(row, answered, outcomes):
    answer = answered.get(row["adherence_id"])
    outcome = answer.get("outcome") if answer else None
    return outcome if outcome in outcomes else "pending"


def adherence_groups(rows, adherence, intervals, cutoff=None):
    """One group per (kind, fingerprint): measured counts and the rate with its Wilson interval.

    The counts are the ones `adherence.rates` computes; the rate is None, never 0, while no
    emission is judged.
    """
    answered = adherence.responses(rows)
    counts = {}
    for row in _emissions(rows, cutoff):
        key = (str(row.get("recommendation")), str(row.get(adherence.FINGERPRINT_KEY) or ""))
        entry = counts.setdefault(key, dict((name, 0) for name in COUNTS))
        entry["emitted"] += 1
        entry[_outcome(row, answered, adherence.OUTCOMES)] += 1
    groups = []
    for (kind, fingerprint), entry in sorted(counts.items()):
        judged = entry["followed"] + entry["not_followed"]
        rate = entry["followed"] / float(judged) if judged else None
        interval = intervals.wilson(entry["followed"], judged) if judged else None
        figures = dict((name, _measured(entry[name], fingerprint)) for name in COUNTS)
        figures["rate"] = _measured(rate, fingerprint, n=judged, interval=interval)
        if not judged:
            figures["rate"].note = "no judged emissions"
        groups.append({"kind": kind, "fingerprint": fingerprint or None, "figures": figures})
    return groups


def context_of(call):
    """A call record's usage in the transcript's own field names, for the feed's `_context`."""
    return {"input_tokens": call["input"], "cache_read_input_tokens": call["cache_read"],
            "cache_creation_input_tokens": call["cache_write"]}


def anchor(calls, turn):
    """The last call at or before prompt `turn`, or None when the session has none."""
    found = None
    for call in calls:
        if call["prompt"] <= turn:
            found = call
    return found


def horizon(calls, turn):
    """The calls after prompt `turn`, up to (not including) the first compaction rebuild."""
    out = []
    for call in calls:
        if call["prompt"] <= turn:
            continue
        if call.get("cause") == COMPACTION:
            break
        out.append(call)
    return out


def _tokens(call, cache_read, cache_write):
    """Token counts for `tokens_cost`, keeping the call's write tier split in proportion."""
    tokens = {"input": call["input"], "output": call["output"], "cache_read": cache_read,
              "cache_write": cache_write}
    one_hour = call.get("cache_write_1h")
    if one_hour is not None and call.get("cache_write_5m") is not None:
        writes = call["cache_write"]
        hour = int(round(cache_write * one_hour / float(writes))) if writes else 0
        tokens.update(cache_write_5m=cache_write - hour, cache_write_1h=hour)
    return tokens


def reprice(calls, turn, table, pricing, context):
    """Carried-context reprice for one emission at prompt `turn` of a session's calls.

    `calls` are the session's main calls in order, each with `prompt` (the ordinal of the user
    prompt it answers), `model`, `input`, `output`, `cache_read`, `cache_write`,
    `cache_write_5m` and `cache_write_1h` (None when unsplit), `rewritten` (the tokens a cache
    rebuild rewrote; None or 0 when the call is not one) and `cause`. `context` is the usage
    feed's `_context` over `context_of(call)`.

    Returns `{"status": "priced", "actual", "counterfactual", "saving"}`, or a status of
    `unpriced` or `missing` with no dollars.
    """
    if not calls:
        return {"status": "missing"}
    last = anchor(calls, turn)
    if last is None:
        return {"status": "missing"}
    carried = max(0, context(context_of(last)) - context(context_of(calls[0])) - HANDOFF_TOKENS)
    actual = counterfactual = 0.0
    for index, call in enumerate(horizon(calls, turn)):
        rate = pricing.price_for(table, call["model"])
        if rate is None:
            return {"status": "unpriced"}
        read, write = call["cache_read"], call["cache_write"]
        actual += pricing.tokens_cost(_tokens(call, read, write), rate)
        if index == 0:
            read, write = 0, write + max(0, read - carried)
        elif call.get("rewritten"):
            write = write - min(carried, call["rewritten"])
        else:
            read = max(0, read - carried)
        counterfactual += pricing.tokens_cost(_tokens(call, read, write), rate)
    return {"status": "priced", "actual": actual, "counterfactual": counterfactual,
            "saving": actual - counterfactual}


def estimate_groups(rows, adherence, calls_for, table, pricing, context, cutoff=None):
    """One group per kind: the if-followed saving, labelled soft estimate, or unmeasured.

    `calls_for(session_id)` returns the session's call records, or None when its transcript
    cannot be read. Only emissions the ledger answers `not_followed` are priced.
    """
    answered = adherence.responses(rows)
    kinds = set(adherence.KINDS)
    chosen = {}
    for row in _emissions(rows, cutoff):
        kind = str(row.get("recommendation"))
        kinds.add(kind)
        if _outcome(row, answered, adherence.OUTCOMES) == "not_followed":
            chosen.setdefault(kind, []).append(row)
    groups = []
    for kind in sorted(kinds):
        estimator = ESTIMATORS.get(kind)
        if estimator is None:
            groups.append({"kind": kind, "estimator": None, "assumption": None, "figures": {
                "saving": Figure(None, UNMEASURED, note="no estimator")}})
            continue
        tally = {"priced": 0, "unpriced": 0, "missing": 0}
        savings = []
        for row in chosen.get(kind, []):
            calls = calls_for(row.get("session_id"))
            turn = row.get("turn")
            if calls is None or not isinstance(turn, int) or isinstance(turn, bool):
                result = {"status": "missing"}
            else:
                result = reprice(calls, turn, table, pricing, context)
            tally[result["status"]] += 1
            if result["status"] == "priced":
                savings.append(Figure(result["saving"], SOFT_ESTIMATE, n=1, estimator=estimator))
        figures = {"priced": Figure(tally["priced"], MEASURED),
                   "unpriced": Figure(tally["unpriced"], MEASURED),
                   "transcript_missing": Figure(tally["missing"], MEASURED)}
        if savings:
            saving = total(savings)
            figures["saving"] = saving
            figures["mean_saving"] = Figure(saving.value / saving.n, SOFT_ESTIMATE, n=saving.n,
                                            estimator=estimator)
        else:
            figures["saving"] = Figure(None, SOFT_ESTIMATE, n=0, estimator=estimator,
                                       note="no priced not-followed emissions")
            figures["mean_saving"] = Figure(None, SOFT_ESTIMATE, n=0, estimator=estimator)
        groups.append({"kind": kind, "estimator": estimator, "assumption": ASSUMPTION,
                       "figures": figures})
    return groups


def report(rows, adherence, intervals, calls_for, table, pricing, context, cutoff=None):
    """Both sections, structured; no output and no writes."""
    return {"adherence": adherence_groups(rows, adherence, intervals, cutoff),
            "if_followed": estimate_groups(rows, adherence, calls_for, table, pricing, context,
                                           cutoff)}


def summary(result):
    """The report as `usage_json` groups: every figure a labelled object, unknowns null."""
    groups = []
    for group in result["adherence"]:
        groups.append({"section": "adherence", "kind": group["kind"],
                       "fingerprint": group["fingerprint"], "estimator": None,
                       "assumption": None, "figures": dict(
                           (name, figure.as_dict()) for name, figure in group["figures"].items())})
    for group in result["if_followed"]:
        groups.append({"section": "if_followed", "kind": group["kind"], "fingerprint": None,
                       "estimator": group["estimator"], "assumption": group["assumption"],
                       "figures": dict((name, figure.as_dict())
                                       for name, figure in group["figures"].items())})
    return {"groups": groups, "labels": list(LABELS), "footer": FOOTER}


def show(figure, unit="count"):
    """One figure as text with its label; a bare number is refused."""
    if not isinstance(figure, Figure):
        raise TypeError("the report prints only labelled figures, not %r" % (figure,))
    qualifiers = "".join(", " + q for q in figure.qualifiers)
    tag = "[%s%s]" % (figure.label, qualifiers)
    if figure.value is None:
        return "%s %s" % (figure.note or "unknown", tag)
    if unit == "rate":
        text = "%.0f%%" % (figure.value * 100)
        if figure.interval is not None:
            text += " (95%% CI %.0f-%.0f%%)" % (figure.interval[0] * 100, figure.interval[1] * 100)
    elif unit == "usd":
        text = "%s$%.2f" % ("-" if figure.value < 0 else "", abs(figure.value))
    else:
        text = str(figure.value)
    if figure.n is not None and unit != "count":
        text += " n=%d" % figure.n
    return "%s %s" % (text, tag)


def render(result, say):
    """Print both sections through `say`; no grand total spans them."""
    say("Adherence (Measured)")
    if not result["adherence"]:
        say("  no emissions recorded")
    for group in result["adherence"]:
        figures = group["figures"]
        say("  %s  fingerprint %s" % (group["kind"], group["fingerprint"] or "(none)"))
        say("    " + "  ".join("%s %s" % (name.replace("_", " "), show(figures[name]))
                               for name in COUNTS))
        say("    rate " + show(figures["rate"], "rate"))
    say("")
    say("If followed (Soft estimate: %s)" % ESTIMATOR)
    for group in result["if_followed"]:
        figures = group["figures"]
        if group["estimator"] is None:
            say("  %s  %s" % (group["kind"], show(figures["saving"])))
            continue
        say("  %s  estimator %s" % (group["kind"], group["estimator"]))
        say("    " + group["assumption"])
        say("    priced %s  unpriced %s  transcript missing %s" % (
            show(figures["priced"]), show(figures["unpriced"]),
            show(figures["transcript_missing"])))
        say("    saving " + show(figures["saving"], "usd"))
        say("    mean per priced emission " + show(figures["mean_saving"], "usd"))
    say("")
    say(FOOTER)
    return 0

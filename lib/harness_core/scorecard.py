# SPDX-License-Identifier: MIT
"""The module scorecard: one row per module in the selection, with what is known about it.

`citizen scorecard` prints this. Each row names the module (`kind/unit`), its state, the surface
its manifest declares, how it is measured (its instruments, or `unmeasured`), its rule-coverage
state for a rule or stance, its resident tokens as a soft estimate, and its measured effect from an
ablation run that removed or set it, or `unmeasured`. A module with no instrument, or with no run
that toggled it, reads `unmeasured`; nothing here ever says a module has no effect, because an
absent measurement and a null result are different findings, and a null result reads
`inconclusive` with its interval.

The first line is the share of modules with at least one instrument, floored to a whole percent
as `rule_coverage.percent` floors it, so one uninstrumented module never reads 100%.

Nothing here reads configuration or results: the caller hands in the resolved selection, the
manifests, the attribution, the coverage states and the effects, so the rows are testable alone.
"""

UNMEASURED = "unmeasured"
SOFT_ESTIMATE = "soft estimate"
NOT_LOADED = "not loaded"
FIELDS = ("module", "state", "surface", "measurement", "coverage", "tokens", "effect")


def measurement(manifest):
    """`measured by <instruments>` or `unmeasured`, never `no effect`; as `posture.measurement`."""
    instruments = (manifest or {}).get("instruments") or []
    return "measured by " + ", ".join(instruments) if instruments else UNMEASURED


def rows(selection, kinds, manifests, attribution=None, coverage=None, effects=None):
    """One dict per module of every kind in `selection` (`posture.selection`'s document).

    `kinds` is `posture.selection_kinds`, `manifests` the `{kind: {unit: manifest}}` half of
    `posture.manifests`, `attribution` `posture.context_attribution`'s document, `coverage`
    `{module: state}` and `effects` `{module: effect}` as `effects_from` builds it."""
    modules = (attribution or {}).get("modules") or {}
    coverage, effects = coverage or {}, effects or {}
    out = []
    for kind in kinds:
        for unit, value in sorted((selection.get(kind) or {}).items()):
            key = "%s/%s" % (kind, unit)
            manifest = (manifests.get(kind) or {}).get(unit)
            off = value in (None, "off")
            if key in modules:
                tokens = {"tokens": modules[key], "estimand": (attribution or {}).get("estimand") or SOFT_ESTIMATE}
            elif off:
                tokens = NOT_LOADED
            else:
                tokens = UNMEASURED
            out.append({"module": key, "state": value, "surface": (manifest or {}).get("surface"),
                        "instruments": list((manifest or {}).get("instruments") or []),
                        "measurement": measurement(manifest), "coverage": coverage.get(key),
                        "tokens": tokens, "effect": effects.get(key, UNMEASURED)})
    return out


def instrumented_share(found):
    """`(instrumented, total, percent text)`, floored; `<1%` when the floor would hide one."""
    total = len(found)
    measured = sum(1 for row in found if row["instruments"])
    if not total:
        return 0, 0, ""
    whole = 100 * measured // total
    return measured, total, "<1%" if not whole and measured else "%d%%" % whole


def effects_from(result):
    """`{module: effect}` from `replay_stats.compare`'s result: each arm's cost effect against
    control, with its interval, n and reading, labelled measured. Only arms that name the module
    they removed or set are used; the rest of the scorecard stays `unmeasured`."""
    out = {}
    for arm in (result or {}).get("arms") or []:
        module = arm.get("removed")
        cost = (arm.get("measures") or {}).get("cost_usd") or {}
        if not module:
            continue
        out[module] = {"estimand": "measured", "arm": arm.get("arm"), "measure": "cost",
                       "effect": cost.get("effect"), "interval": cost.get("interval"), "n": cost.get("n"),
                       "reading": cost.get("reading"), "exploratory": arm.get("exploratory")}
    return out


def _tokens(value):
    if isinstance(value, dict):
        return "{:,} ({})".format(value["tokens"], value["estimand"])
    return value


def _effect(value):
    if not isinstance(value, dict):
        return value
    interval = value.get("interval")
    span = "undefined" if not interval else "[%+.1f%%, %+.1f%%]" % (100 * interval[0], 100 * interval[1])
    effect = "undefined" if value.get("effect") is None else "%+.1f%%" % (100 * value["effect"])
    return "cost %s %s, n %s, %s%s (measured, arm %s)" % (
        effect, span, value.get("n"), value.get("reading"), ", exploratory" if value.get("exploratory") else "",
        value.get("arm"))


def render(found):
    measured, total, percent = instrumented_share(found)
    lines = ["scorecard: %d of %d module(s) have an instrument (%s)" % (measured, total, percent or "0%")]
    width = max([24] + [len(row["module"]) + 1 for row in found])
    for row in found:
        lines.append("  %-*s %-10s %-26s tokens %s; effect %s%s" % (
            width, row["module"], row["state"], row["measurement"], _tokens(row["tokens"]),
            _effect(row["effect"]), "; coverage %s" % row["coverage"] if row["coverage"] else ""))
    return lines

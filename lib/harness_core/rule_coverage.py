# SPDX-License-Identifier: MIT
"""Every rule in the loaded instruction surface, as measured, dark or unmeasured.

`usage --rules` prints this block under its detector table. The states and the block's shape
are standalone `ruleprobe`'s under `--rules`, so the two reports read the same:

- **measured**: a detector the harness runs names the rule, from the registry in
  `rule-detectors.py` or from the repository's `.ruleprobe/detectors.yaml`, and is enabled
  under the selected stances;
- **dark**: nothing measures it, on purpose, with the reason: an `OPT_OUT` entry, or an
  `opt_out:` key in the rule file's front matter;
- **unmeasured**: neither, listed with the reason, so the gap is visible rather than assumed.

The share measured counts dark rules in its denominator, and is floored to a whole percent so
one unmeasured rule never reads 100%. A rule is a file: its name is its front matter's `rule:`
key, else its stem, and a stance is named by its dimension, which is the name its detectors
carry. A detector names a rule, not a file, so when two files share a name the first in the
surface holds the name's detectors and opt-out, and the later one is unmeasured, saying so.

A `detector:` block in a rule file's front matter is not run by the harness, whose session
hook reads only the registry and the detector file, so such a rule is unmeasured with that
reason rather than reported measured by a detector that never fires.

Nothing here reads configuration: the caller hands in the surface, the detectors and the
engine's front-matter reader, so the classification is testable on its own.
"""
import os
from collections import namedtuple

STATES = ("measured", "dark", "unmeasured")

#: One rule: its name, its file, its state, the reason when there is one, the detector ids.
Rule = namedtuple("Rule", "rule path state reason detectors")
#: A detector file or rule file that could not be read in full: the file, the line, and why.
Finding = namedtuple("Finding", "path line reason")

NO_DETECTOR = "no detector names it and it has no opt-out"
GATED_OFF = "its detectors do not run under the selected stances"
SHADOWED = "its name's detectors measure %s, the first rule of that name"
FRONT_MATTER_DETECTOR = ("its front-matter detector is not run by the harness; "
                         "move it to .ruleprobe/detectors.yaml")


def classify(surface, detectors, opt_out, stances, read_rule_file):
    """`(rules, findings)` for `surface`, a list of `(name, path)`.

    `detectors` are every detector the session hook runs, each with `id`, `rule` and
    `enabled(stances)`; `opt_out` is the registry's `{rule: reason}`; `read_rule_file` is the
    engine's, returning `(detectors, entry, findings)` for one markdown file.
    """
    rules, findings, owner = [], [], {}
    for name, path in surface:
        path = str(path)
        compiled, entry, problems = read_rule_file(path)
        findings.extend(Finding(p.path, p.line, p.reason) for p in problems)
        stem = os.path.splitext(os.path.basename(path))[0]
        rule = entry.rule if entry.rule != stem else name
        if rule in owner:
            # A detector names a rule, not a file; the first file of that name holds it.
            rules.append(Rule(rule, path, "unmeasured", SHADOWED % short(owner[rule]), []))
            continue
        owner[rule] = path
        named = [d for d in detectors if d.rule == rule]
        live = [d.id for d in named if d.enabled(stances)]
        if live:
            rules.append(Rule(rule, path, "measured", "", sorted(live)))
        elif named:
            rules.append(Rule(rule, path, "unmeasured", GATED_OFF, []))
        elif entry.state == "dark":
            rules.append(Rule(rule, path, "dark", entry.reason, []))
        elif rule in opt_out:
            rules.append(Rule(rule, path, "dark", opt_out[rule], []))
        elif compiled or entry.state == "measured":
            rules.append(Rule(rule, path, "unmeasured", FRONT_MATTER_DETECTOR, []))
        else:
            rules.append(Rule(rule, path, "unmeasured", entry.reason or NO_DETECTOR, []))
    return rules, findings


def counts(rules):
    out = dict((state, 0) for state in STATES)
    for rule in rules:
        out[rule.state] = out.get(rule.state, 0) + 1
    return out


def share(rules):
    """Measured rules over all rules, dark ones included; `None` when there are none."""
    return counts(rules)["measured"] / float(len(rules)) if rules else None


def percent(rules):
    """The share as the `rules:` line prints it: floored, and `<1%` when the floor would hide
    a measured rule."""
    if not rules:
        return ""
    measured = counts(rules)["measured"]
    whole = 100 * measured // len(rules)
    if not whole and measured:
        return " (<1% measured)"
    return " (%d%% measured)" % whole


def short(path, relative_to=None, home=None):
    """A path to print: relative to `relative_to` when under it, else with the home directory
    as `~`, so a pasted report does not carry it."""
    relative_to = relative_to or os.getcwd()
    try:
        relative = os.path.relpath(path, relative_to)
    except ValueError:  # pragma: no cover - a different drive on Windows
        relative = path
    if not relative.startswith(".."):
        return relative
    home = home or os.path.expanduser("~")
    if home and (path == home or path.startswith(home.rstrip(os.sep) + os.sep)):
        return "~" + path[len(home.rstrip(os.sep)):]
    return path


def summary(rules, findings, relative_to=None, home=None):
    """The coverage block, in `ruleprobe`'s layout, as a list of lines."""
    lines = []
    if rules:
        c = counts(rules)
        lines.append("rules: %d measured, %d dark, %d unmeasured%s"
                     % (c["measured"], c["dark"], c["unmeasured"], percent(rules)))
        width = max([28] + [len(r.rule) + 1 for r in rules])
        for r in rules:
            note = ": " + r.reason if r.reason else ""
            lines.append("  %-11s%-*s%s%s" % (r.state, width, r.rule,
                                              short(r.path, relative_to, home), note))
    if findings:
        lines.append("findings: %d (everything else still loaded)" % len(findings))
        for f in findings:
            lines.append("  %s:%d  %s" % (short(f.path, relative_to, home), f.line, f.reason))
    return lines

"""Several models in one registered run, each its own stratum.

A single-model result may not generalise, so `cost_bench.py replay --model A,B` (or `--model A
--model B`) runs the whole design once per model: each stratum has its own schedule, spend cap,
preflight, results file and history row, and every row it writes carries `stratum`, the model id.
`summarise` reports every section per stratum. It pools strata only when the run's
pre-registration names a pooled analysis in its **Pooled analysis** field under Run; the pooled
rows keep each task-and-model pair as its own cluster, so a task is never paired across models.
Standard library only.
"""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import experiment_protocol  # noqa: E402

KEY = "stratum"
POOLED = "pooled"
FIELD = ("Run", "Pooled analysis")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def parse_models(values):
    """The models `--model` names, in order and without repeats: a comma list, a repeated flag or both.

    `values` is a string, a list of strings or None, so older callers that set one string keep
    working. An empty item is refused, as is the same model twice, since a stratum is a model."""
    if values is None:
        return []
    items = [values] if isinstance(values, str) else list(values)
    models = []
    for item in items:
        for part in item.split(","):
            model = part.strip()
            if not model:
                raise SystemExit("cost-bench: --model %r names an empty model" % item)
            if model in models:
                raise SystemExit("cost-bench: --model names %s twice; each model is one stratum" % model)
            models.append(model)
    return models


def directory(stratum):
    """The folder a stratum's results go in, under its tag's: the model id with path-unsafe characters replaced."""
    return _UNSAFE.sub("_", stratum)


def groups(rows):
    """`{stratum: rows}` in first-seen order; a row without a stratum is in the `None` group."""
    out = {}
    for row in rows:
        out.setdefault(row.get(KEY), []).append(row)
    return out


def is_stratified(rows):
    return len(groups(rows)) > 1


def pooled_field(text):
    """The plan's **Pooled analysis** value, or None when it is absent, a placeholder or "none"."""
    section, name = FIELD
    value = experiment_protocol.fields(experiment_protocol.sections(text).get(section, "")).get(name, "")
    if not value or experiment_protocol.PLACEHOLDER.search(value) or value.lower().startswith("none"):
        return None
    return value


def registered_plan(rows, root):
    """The text of the plan every row names, at the commit that registered it; SystemExit otherwise."""
    identities = {(r.get("evidence"), r.get("pre_registration"), r.get("pre_registration_commit")) for r in rows}
    if len(identities) != 1:
        raise SystemExit("cost-bench: refusing to pool: the rows name %d different plans" % len(identities))
    evidence, path, commit = identities.pop()
    if evidence != experiment_protocol.PREREGISTERED or not path or not commit:
        raise SystemExit("cost-bench: refusing to pool: the rows are not pre-registered, and only a "
                         "pre-registration can name a pooled analysis")
    done = subprocess.run(["git", "-C", str(root), "show", "%s:%s" % (commit, path)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    if done.returncode:
        raise SystemExit("cost-bench: refusing to pool: cannot read %s at %s: %s"
                         % (path, commit, done.stderr.strip()))
    return path, done.stdout


def pool(rows, root):
    """`(analysis, rows)` relabelled for one pooled analysis, or SystemExit when the plan names none.

    Each pooled row's task becomes `<stratum>/<task>`, so the bootstrap resamples task-and-model
    clusters, and its stratum becomes `pooled`."""
    path, text = registered_plan(rows, root)
    analysis = pooled_field(text)
    if not analysis:
        raise SystemExit("cost-bench: refusing to pool strata: %s names no pooled analysis; fill its "
                         "\"Pooled analysis\" field under Run before the first trial, or read the strata "
                         "apart" % path)
    pooled = [dict(row, task="%s/%s" % (row.get(KEY), row.get("task")), **{KEY: POOLED}) for row in rows]
    return analysis, pooled


def ceiling_usd(runs, run_cap, preflights, preflight_cap):
    """The most one stratum can report: every run and every preflight at its cap."""
    return runs * run_cap + preflights * preflight_cap


def out_dir(base, stratum):
    return Path(base) / directory(stratum) if stratum else Path(base)

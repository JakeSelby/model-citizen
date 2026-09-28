# Measuring without the harness

The measurement report reads one file: the session ledger at
`~/.local/state/agent-harness/usage.jsonl`, written by the `usage-log` hook. Nothing in
`citizen usage` needs a projected primitive, a merged settings file or a synced runtime, so
the report is useful to someone who wants the numbers and not the way of working.

## When the whole harness is the answer

- You want the rules the report scores. `--rules` counts detector hits against
  `claude/hooks/rule-detectors.py`, and a detector only means something when the rule it is
  named for is actually loaded into the session. Hit counts without the prose they measure are
  a share of nothing. See [usage.md](usage.md#which-rules-fired).
- You want stance groupings. `--by stance` reads the `dimension: variant` map the CLI resolves,
  so a checkout with no config has one variant per dimension and one group per report.
- You are already running `citizen sync`. Then the hook is installed and the ledger fills
  without another step.

## When the report alone is the answer

- You want spend per repository, model, role or day, and you run Claude Code or Codex as they
  come. Install the `usage-log` hook, leave everything else alone, and read
  [usage.md](usage.md) for what each grouping means.
- You are evaluating the measurement before adopting the primitives. The ledger is local, it
  holds no message text, and removing the hook stops it.
- You are feeding an observability backend. `citizen usage export` replays the ledger; see
  [telemetry.md](telemetry.md).

## The packaged path

Installing the hook by hand from this repository is the only route today. A standalone package
that carries the hook, the detectors and the report without the rest of the harness is planned
in [issue #453](https://github.com/JakeSelby/model-citizen/issues/453). Until it lands, a
standalone reader copies two files out of this checkout and pins the version they came from.

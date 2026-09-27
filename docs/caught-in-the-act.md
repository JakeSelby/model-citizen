# Caught in the act

Two features shipped here and did not do the thing shipping them assumed. Neither was found by
reading the code. One was found by a cost benchmark that counts tool calls, the other by the rule
telemetry in [usage.md](usage.md), and in both cases the code was doing exactly what it said.

## A stance measured as never firing

**What shipped.** The `delegation` stance tells a session to gather with subagents without waiting
to be asked, and it is where the harness claims its cost saving: work removed from the thread, not
context trimmed off the front of it. It is standing context in every session that selects it.

**What the instrument measured.** Zero. Across 19 headless runs of the replay benchmark — the
first live set plus two later probes — there were no `Agent` tool calls in either arm. On
`hook-inventory`, the pinned many-file read-and-summarize task that is the case the stance names
by description, the harness arm read all 18 files itself in 10 API calls against the bare arm's
11. The stance was loaded, resolved and never acted on.

**Reproducing it.** The replay calls a model and spends real usage, so it is run by hand:

```sh
python3 scripts/cost_bench.py replay --model <id> --tag <ref> --exploratory --raw /tmp/replay-raw   # 7 tasks x 2 arms x 5 reps
grep -o '"name": *"Agent"' /tmp/replay-raw/*.json | wc -l                 # spawns, both arms
```

The per-run `turns` field in `benchmarks/<harness version>/results.jsonl` is the API-call count the
10-against-11 figure comes from. See [benchmarks.md](benchmarks.md) for what the arms share and
what the replay fakes.

**What was done about it.** Nothing to the stance yet, which is the honest part.
[#429](https://github.com/JakeSelby/model-citizen/issues/429) is open and frames the measurement as
a question with two answers rather than as a defect: either the stance does not bind under
`claude -p`, or the four pinned tasks are too small for delegation to be worth it and an agent
declining to delegate on an 11-call task is correct. The issue closes on a run pair showing a spawn
and its effect on cost, or on a recorded finding that declining is correct at this size plus the
size at which it should fire. A benchmark that cannot separate those two is the first thing to fix.

**Without the instrument.** A stance that never fires reads exactly like a stance that fires and
helps: the prose is in context, the runs pass, the tasks get done. Noticing would have meant
opening the transcript of every run and searching it for a spawn that was never there.

## A hook whose metric did not move

**What shipped.** `brief-guard`, a `PreToolUse` hook that appends a return bound to every subagent
brief, backed by the `delegation` rule line that says to bound the brief.

**What the instrument measured.** The `transcript-hygiene/brief-without-cap` detector fired at the
same rate before, during and after the hook existed: **3.2 hits per 100 turns before, 3.6 during,
3.2 after**, on one machine's ledger.

**Reproducing it.** Hits come from the local ledger, and a rescan rebuilds them from transcripts:

```sh
bin/harness usage --rescan --days 30 --rules   # backfill, then hits per detector
bin/harness usage --rules --by stance          # the same, per dimension=variant
```

The detector is `transcript-hygiene/model-wrote-no-cap` today; `--rules` folds the old id into the
new one as it reads, so the series does not split at the rename.

**What was done about it.** The cause was established from a recorded transcript, and it was
neither of the two guesses in [#324](https://github.com/JakeSelby/model-citizen/issues/324). The
hook was registered and firing. Claude Code records a tool call's input as the model wrote it, and
a `PreToolUse` hook's `updatedInput` lands in a separate entry the ledger scan never reads — so
capping a brief could not move a number derived from the pre-hook text, whatever the hook did. The
metric was renamed to say what it measures: `model-wrote-no-cap` counts briefs the hook went on to
cap, and a hit is the orchestrator's omission, not an uncapped brief reaching a subagent. That
makes the report's `promote?` flag mean something again. `rule-detectors.RENAMED` folds the old id
at read time, the ledger file is not rewritten, and `tests/test_brief_cap_metric.py` pins the
behaviour. Measuring the delivered brief is still possible from the hook entries, but it needs the
ledger scan to read hook output, which is a ledger change and not a detector one.

**Without the instrument.** The hook worked. Nothing errored, no test failed, and every brief did
go out capped. The only visible symptom was a number that should have fallen and did not, which
exists only because the number was being kept before the hook shipped.

## What the instrument cannot show yet

- **Detector validity is measured for nineteen detectors of nineteen, against a synthetic
  corpus.** The vendored `ruleprobe` wheel labels the detectors the engine ships and
  `tests/fixtures/detector-corpus/` labels the ones written for this repository's own rules, and
  the `corpus` job runs `scripts/detector_corpus.py --floor 0.9` over both on every pull request
  ([#522](https://github.com/JakeSelby/model-citizen/issues/522)). What that buys is a detector
  measured against what its author says it should find; `secrets/git-add-secret-file` and
  `autonomy/denied-by-grade` are recorded under the floor at p=0.83 rather than the floor being
  moved to meet them. A rate over real
  sessions is still a rate of behaviour only as far as the corpus resembles them.
- **Per-variant rates are observational.** `--rules --by stance` groups sessions by the variant
  that happened to be selected, chosen by one person for reasons the ledger does not record.
  It says what was seen under a variant, never what the variant caused.
- **Opted-out rules are dark.** A rule with nothing a transcript can decide opts out by name, and
  those rules have no hit rate at all — absence there is silence, not compliance.

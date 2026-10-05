# The Studio

The Studio is a local browser view over the same functions the CLI runs. It shows what the harness
has installed and decided, lets you change it inside a draft, and runs the repository's suites and
benchmarks against a version you name. Every page shows the `citizen` command that does the same
thing, so anything you do in the Studio can be repeated in a terminal or by an agent.

What it is not: a hosted service, a second source of configuration, or a way to change the live
harness without review. Edits stay in a draft until you apply one, an apply runs the governed
`citizen sync` path, and a run keeps its engine's native result files as its record. The Studio
measures nothing itself; every figure it shows is read from the engine that produced it.

## Start it

```sh
citizen studio              # start, then open a signed-in browser tab
citizen studio --detach     # keep running after the terminal closes
citizen studio --no-open    # start without opening a browser
citizen studio status       # print the URL, or exit 1 when none is running
citizen studio stop         # stop it and remove its state file
```

There is one Studio per user. Running `citizen studio` while one is running reuses it, prints its
URL and opens a new signed-in tab. `--port PORT` asks for a port; when it is taken, the Studio picks
a free one and says so. `--json` prints one result object with the URL, port and process id, and
never a credential. State lives under `~/.local/state/agent-harness/studio/`.

The Studio routes pages after a `#`: the server serves the page only at `/`, so a page's address
is the Studio's URL, which ends in `/`, followed by `#/` and the page path, such as
`/#/reports/rules`. An address without the `#`, such as `/reports/rules`, answers 404 `not_found`
even when signed in.

Which browsers and platforms are supported, and the release evidence behind that, is in
[the compatibility catalog](compatibility.md#studio-browser-and-platform-support).

## Security model

The Studio binds `127.0.0.1` only; no flag binds it anywhere else. The browser reaches it at a
random `*.localhost` host name, unique to the running instance.

- **Only the launcher signs you in.** `citizen studio` writes a one-use sign-in form to a private
  file in the state folder and opens it. The form posts a single-use token, which the server
  exchanges for an `HttpOnly`, `SameSite=Strict` session cookie. No secret appears in a URL, and a
  copied URL alone opens nothing. To sign in again, run `citizen studio` again.
- **Every `/api` route needs the session cookie** and answers 401 without it. A request for another
  host name answers 403.
- **Every change needs a CSRF token and the Studio's own `Origin`**, or it gets 403 and nothing is
  written.
- **Responses carry a content security policy** with no inline script and
  `frame-ancestors 'none'`, plus `nosniff` and `no-referrer`, and no CORS header.
- **Secrets stay out.** A secret or keychain reference in your configuration is shown as its source
  and name, never its value, and file access is refused outside the Studio's known roots.

## The pages

- **Hub** (`/#/`): the installed version, a newer release on `stable` with its changelog link, each
  failing `citizen doctor` check with the doctor's own repair command, projection drift from
  `citizen diff`, and recent runs. It updates and syncs nothing by itself: a drift fix goes through
  a reviewed `citizen sync --dry-run` first. Its cards link to the usage and trends reports.
- **Configure** (`/#/configure`): the effective selection, each value with the layer that set it and
  the values it overrode, as `citizen selection` reports it. Changes are made in a draft, below.
- **Library** (`/#/library`): every module with its root, manifest, state, the layer that switched it
  on or off, the files it projects to for Claude Code and Codex, and its token cost from the static
  context figure. A module whose name collides with a core module is flagged, and a fork shows the
  core module it came from. It reads the same catalog as `citizen catalog`.
- **Experiments** (`/#/experiments`): suites to run, run history and the comparison of two runs;
  a run's detail is at `/#/experiments/runs/RUN_ID`.
- **Activity** (`/#/activity`): hook decisions and governed changes, newest first, paged, with their
  session, repository, hook and source. It is the same query as `citizen activity`. An apply's entry
  is where it is rolled back.
- **Reports**: rule health (`/#/reports/rules`), spend and usage (`/#/reports/usage`) and trends
  (`/#/reports/trends`). The Reports tab itself is still a placeholder; reach the reports from the
  Hub's cards, the Library, or their addresses.

Pages refresh within two seconds of a change made from the CLI, an agent or an editor. The header
can pause updates; resuming shows how many changes arrived meanwhile.

## Drafts

A draft is a managed Git worktree of the harness checkout, at `~/worktrees/<repo>/draft-<name>` on
branch `draft/<name>`, with its own copy of your configuration. Nothing in it is live until it is
applied. Each save is a checkpoint commit; a save made against an old revision is refused rather
than merged, and an interrupted save is recovered: completed when its commit exists, otherwise
undone.

```sh
citizen draft create NAME [--base installed|vX.Y.Z]
citizen draft list           # drafts, and whether the installed version has moved since
citizen draft diff NAME      # source and private configuration changes
citizen draft rebase NAME    # onto the installed revision; stops at a Git conflict
citizen draft discard NAME   # refuses a dirty or conflicted draft
```

In Configure, pick a draft with **Drafts** in the header, then change it:

- **Mode, stance variants and switches.** Each change is previewed by running `citizen config set`
  in a throwaway home, with the before and after selection, stance text and context budget. A
  change that breaks a dependency, or that turns off a core hook without the acknowledgement it
  asks for, is refused and nothing is saved. CLI: `citizen draft selection read|preview|save`.
- **Identity, preferences and integrations** through forms built from the configuration schema.
  CLI: `citizen draft settings schema|read|preview|save`.
- **Hand-edit a rule, skill or stance** with live lint, the context budget and the runtime
  projection, then save an explicit checkpoint. CLI: `citizen draft module read|preview|save`.

Every `save` needs `--base-revision` (the revision you read or previewed) and `--idempotency-key`,
so a retried save is applied once. `citizen draft selection save` and `citizen draft settings save`
also need `--changes FILE`, a JSON file of the changes, and `citizen draft module save NAME MODULE`
needs `--content FILE`, the new source, and `--source-digest`, the digest of the source you read:

```sh
citizen draft selection save NAME --base-revision REV --idempotency-key KEY --changes FILE
citizen draft settings save NAME --base-revision REV --idempotency-key KEY --changes FILE
citizen draft module save NAME MODULE --base-revision REV --source-digest DIGEST \
  --idempotency-key KEY --content FILE
```

### Templates and forks

Add your own module from a template, or fork a core module into your personal root. With no
personal root, the draft offers to create one and registers it in `primitive_roots`. The new
module's manifest and the whole selection are checked before the checkpoint exists. A fork switches
the core original off in the draft; when an update later changes the original, the fork's Library
page shows the upstream diff.

```sh
citizen draft module templates NAME             # personal root, templates, forkable modules
citizen draft module plan NAME --request FILE   # check it and show what it would write
citizen draft module add NAME --request FILE --base-revision REV --idempotency-key KEY
citizen draft module library NAME               # the library as the draft resolves it
```

`FILE` is a JSON object: `action` (`add` or `fork`), `kind`, `name`, `description`, `source` and
`create_root`. Lint's always-loaded cap counts a switched-off core rule too, so forking a long core
rule can be refused with lint's own finding.

### Review and apply

**Review this draft** shows every file and configuration key the draft changes, the result of
`citizen lint` and strict resolution run in the draft, the context-budget delta, and the exact
commands an apply runs. Apply then runs those commands under the sync and configuration locks: it
fast-forwards your personal root, sets each changed key with `citizen config set`, runs
`citizen sync`, then the doctor checks, whose result shows on the Hub.

```sh
citizen draft review NAME
citizen draft apply NAME --revision REV   # REV: the revision you reviewed
```

Apply refuses a draft with lint findings, a draft changed since the reviewed revision, and a draft
that edits a core module in place; for that one it offers a fork, or a branch with the draft's
commits and the commands to open a pull request. When another process holds the sync lock, apply
names the holder and does not interleave.

### Roll back and recover

Every apply first journals the configuration and file contents it is about to replace, and reports
an apply id. Rolling it back puts exactly those keys and files back, then runs `citizen sync` and
the doctor. When nothing else changed since, the configuration and personal root return byte for
byte. A rollback is refused, naming the later change, when a later apply or edit touched the same
keys or files. A rollback is journalled like an apply, so it can be rolled back too.

```sh
citizen draft rollback APPLY_ID --preview   # what would be restored, or why not
citizen draft rollback APPLY_ID [--draft NAME]
citizen draft recover [--draft NAME]        # restore after an interrupted apply or rollback
citizen draft recover --abandon             # close it and keep what it wrote
```

In the Studio, roll back from the apply's entry in Activity. An interrupted apply or rollback shows
an alert in the apply panel offering the same two recover choices.

## First run

On a fresh install the Hub offers **Set up Model Citizen** (`/#/setup`), which walks one draft named
`first-run` to an applied, doctor-checked harness: check the install, start a draft, say who you
are, pick a stance for each dimension, run a free check (`citizen lint`), review and apply, and
confirm the doctor checks. Leaving halfway changes nothing live, and the draft is kept, so setup
resumes where it stopped. Its last page lists the CLI commands that reproduce the same setup;
`citizen draft first-run [NAME]` prints where it stands and those commands.

## Runs

Every run is built against a target you choose, in a profile isolated from your live home:

| Target | Reference |
|---|---|
| `installed` | the installed checkout |
| `release` | a `v*` tag |
| `branch` | a branch or commit |
| `worktree` | a worktree path; uncommitted changes are snapshotted, never touched |
| `draft` | a draft name; its checkpoint and configuration |

The run records the resolved commit and the configuration digest, and a rerun refuses when either
has drifted.

### Free suites

Experiments lists the free local suites with the exact command for each: `lint`, `unit-tests`
(all of them, or one module, class or test), `lifecycle-acceptance`, `static-context` and
`detector-corpus`. From the CLI:

```sh
citizen runs catalog
citizen runs start SUITE --target-kind KIND --target-ref REF [--param NAME=VALUE]
citizen runs list
citizen runs show RUN_ID
citizen runs cancel RUN_ID
```

### Spend guard

A suite that spends model usage starts nothing until you confirm the estimate and its caps: a
per-run ceiling and a whole-set cap, in dollars, and a pricing basis (API credit or subscription
limits) that says how to read them. A confirmation is single-use and bound to the request it was
shown for. A run that reaches its cap stops, keeps its finished cases and marks the rest not run; a
client usage-limit error stops it as a limit, not a failure. Its spend is recorded in the usage
ledger under the run's id.

The CLI applies the same guard. The paid suites in the catalog (`native-acceptance`,
`live-replay`, `micro-tier` and `unit-eval`) start only through their own commands, which run the
Studio's route handlers and so resolve targets and refuse exactly as the Studio does;
`citizen runs start` refuses them and names the command to use. Each is a preview, then a start:

```sh
citizen runs replay preview --request FILE --json      # live replay
citizen runs eval preview --request FILE --json        # micro tier and unit eval
citizen runs native preview --request FILE --json      # native acceptance
citizen runs draft-test plan --request FILE --json     # a draft's paired test
```

`FILE` (or `-` for standard input) is the JSON body the Studio would send. The preview answers
with the estimate, the per-run ceiling (`max_budget_usd`), the whole-set cap (`spend_cap_usd`),
the pricing basis, the resolved request and a one-use `confirmation_token`; a draft test's plan
carries them under `preview`. Nothing has started.
To start, send the same group's `start` a body carrying the preview's resolved request, unchanged,
and its token, for example `citizen runs replay start --request FILE --json`. A changed request
needs a new preview, and a token is never reused. The bodies, with an example of each, are in the
studio-loop skill's [request reference](../primitives/skills/studio-loop/requests.md).

`citizen runs start` also defines `--max-budget-usd`, `--spend-cap`, `--pricing-source` and
`--confirm-spend`, but every suite in today's catalog that spends usage is refused there before
its spend guard runs, so those flags apply to none of them; a free suite starts at once.

### Native acceptance

Pick the cases a change touches; only those run, against the target, and each verdict shows with
its evidence. A rerun at the same commit skips cases that already have a verdict, an interrupted
run keeps the verdicts in its partial log, and failed cases can be retried into a fresh log. Paid
launches are Claude Code only: Codex has no in-flight dollar ceiling for the Studio to enforce.
The cases are the qualification cases described in [the compatibility catalog](compatibility.md).

### Live replay

Runs the [live replay benchmark](benchmarks.md#live-replay) with two explicit targets, each built
into its own profile, under one spend cap. A replay with no explicit target is refused, as the
engine refuses one. You choose the evaluator pack, tasks, model and repetitions; a pre-registration
is required when either target is a release, and only a release target enters benchmark history.
The result is a per-task table with cost per passed task and pass rate for each arm.

### Evaluation tiers

- **Offline rule detection** runs every rule detector over a saved replay's streams, and the
  **hook replay matrix** runs every hook under every stance variant against the recorded calls,
  with a verdict per hook, variant and call. Both are free.
- **Micro tier** checks whether each claimed mechanism fires, on the cheap model the micro manifest
  pins. **Test this rule** runs the [unit eval](benchmarks.md#unit-evals-the-two-by-two) for one
  rule against the chosen target. Both spend usage.

A tier whose engine is not in the target checkout is absent from the list rather than broken.

### Plugin eval import

`citizen runs reindex` imports `claude plugin eval` results found under the repository's `evals/`
directory or a plugin's own eval directory, as runs with their cases, both arms and scores. A
run's detail page links the original HTML report. A result in a schema version other than 1 is
skipped and reported, never guessed at.

Each paid group also reads its results: `citizen runs replay result`, `citizen runs eval result`
and `citizen runs native progress`, each with `--request FILE`. `citizen runs eval run` starts a
free tier (offline rule detection or the hook replay matrix), and `citizen runs native retry`
retries one failed case into a fresh log.

### History and detail

Experiments keeps a filterable history (suite, target, status, date, cost and duration), each
run's detail with its cases, lineage, exact command and bounded logs, and a rerun that reuses the
original target and parameters and links back to it. A test with different outcomes in two runs at
one commit is marked flaky in both runs and in its own history.

```sh
citizen runs history [--suite S] [--target T] [--status S] [--created-from D] [--created-to D]
citizen runs detail RUN_ID
citizen runs case-history CASE_ID
citizen runs evidence RUN_ID ARTIFACT
citizen runs rerun RUN_ID
citizen runs reindex         # rebuild history from the durable result files
```

## Test a draft

**Test this draft** in Configure runs a live replay whose first target is the commit the draft was
based on and whose second is the draft's checkpoint, so both run as one pair on the same tasks,
model, trials, evaluator pack and caps. Before the run it says when the trial count is too low to
detect the stated effect, and offers the number needed. The verdict is the comparison below, kept
per checkpoint, linked to that comparison, and marked stale once the draft changes.

A draft test is exploratory by default, so its readings carry no direction. To let it read
**helped** or **worse**, register it before running it:

```sh
citizen draft test NAME --register --model MODEL --repetitions N --task TASK [--task TASK ...] \
  --pack PACK --pack-digest DIGEST --effect 0.10
citizen draft test NAME      # each checkpoint's verdict, whether it is stale, and its comparison
```

Registration writes a plan in the [pre-registration template](pre-registration-template.md)'s
format, naming the draft's commit, its base, the task set and pack, the trials, the model and the
smallest effect worth detecting (at most 0.15). It is refused, with the engine's minimum detectable
effect and the fewest trials that would do, when the trial count cannot detect that effect. A
registration cannot be edited after it is written, and a draft edit makes it stale. Only a run
that matches its registration exactly can read helped or worse, under
[the evidence standard's decision rule](evidence-standard.md#1-a-pre-registered-plan); any other
run is labelled exploratory. The Studio's **Register this test** uses the same path.

## Compare two runs

Compares two finished replay targets, paired by task, with the replay engine's intervals:

```sh
citizen runs compare BASE_RUN:1 CANDIDATE_RUN:2   # RUN_ID:TARGET, base first
```

Both sides must come from succeeded runs that finished every task and trial and did not stop at a
cap, on the same tasks, model, trials, evaluator pack, per-trial cap and environment stamps;
otherwise the comparison is refused and each difference is named. No figure shows without its
interval and trial count, and a ratio whose interval spans 1.0 reads inconclusive whatever its
point estimate.

## Trends

Each benchmark series plotted by version, with its change notes and the intervals its engine
stored, beside the project's proof set and its `citizen evidence verify` status. Ratios are
compared across days, never dollars. History appears after `citizen runs reindex`; with none
indexed, the page says so. `citizen reports trends --json` prints the same report.

## Rule health

Every loaded rule with the status and reason `citizen usage --rules` gives it (measured, dark or
unmeasured), its detector hits over 7, 30 and 90 days, and its context cost. Hits come from your
own sessions, so they are labelled exploratory, and an adherence figure from a detector under its
precision floor is marked unreliable. **Try without it** opens a draft with the rule switched off
and its test ready to run; from the CLI it is `citizen draft try-without rules.NAME`. What each detector looks for is in
[rule telemetry](usage.md#rule-telemetry).

## Spend and usage

The local usage ledger as `citizen usage --json` prints it, for the same window, so the two never
disagree. Every dollar figure carries its list-price label and pricing date, and a Studio run,
having no session, is grouped under its run id. The ledger itself is described in
[usage telemetry](usage.md).

## From the CLI

Every page shows its CLI equivalents, and every command on this page runs headless, so an agent
can create a draft, edit it, review and apply it, roll it back, run free suites, preview and start
paid runs, read history and trends, compare replays and register and run a draft test without the
browser. The commands that answer for a Studio route (`citizen runs replay|eval|native|draft-test`,
`citizen reports trends` and `citizen draft try-without`) call that route's own handler, so they
print the JSON the Studio would receive.

The [studio-loop skill](../primitives/skills/studio-loop/SKILL.md) is the agent's guide to that
loop: draft, test and apply with `--json` throughout, then open the Studio with
`citizen studio --detach --json` and hand over its `url` when the user has to see or decide
something. It holds the agent to the same limits as the Studio: a paid run starts only on the
user's go for the exact preview shown, an apply only on their go for the reviewed revision, and a
refusal is reported, never worked around.

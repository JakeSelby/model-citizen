---
name: studio-loop
description: Draft, test and apply a change to the harness from the `citizen` CLI, the same loop the Studio runs, and open the Studio for the user when they should see the result. Use when asked to tune, try out or measure a rule, stance, setting or module change, to compare a draft against its base, to roll an apply back, or when the user wants to see a draft, a review or a comparison.
---

# The Studio loop, headless

Every Studio action has a `citizen` command, and the Studio shows it beside the action. They share
one core, so the command answers what the Studio would: the same checks, refusals and JSON. Pass
`--json` to every command and read the object it prints; do not scrape the human output.

## The loop

```bash
citizen draft create NAME --json                    # an isolated draft of the installed harness
citizen draft settings read NAME --json             # current values, then preview and save
citizen draft settings save NAME --base-revision REV --idempotency-key KEY --changes changes.json --json
citizen draft selection save NAME --base-revision REV --idempotency-key KEY --changes changes.json --json
citizen draft module save NAME MODULE --base-revision REV --source-digest DIGEST --idempotency-key KEY --content source.md --json
citizen draft test NAME --register --model MODEL --repetitions 3 --task ID --pack PACK --pack-digest DIGEST --effect 0.1 --json
citizen runs draft-test plan --request request.json --json      # power and the spend estimate
citizen runs draft-test start --request request.json --json     # with the confirmation token
citizen draft test NAME --json                      # each checkpoint's verdict against its base
citizen runs compare RUN_ID:1 RUN_ID:2 --json
citizen draft review NAME --json                    # files, keys, checks and the commands apply runs
citizen draft apply NAME --revision REV --json      # REV is the revision the review showed
citizen draft rollback APPLY_ID --preview --json
citizen draft rollback APPLY_ID --draft NAME --json
citizen draft recover --draft NAME --json           # only after an interrupted apply
```

Each save takes the revision the last read or save returned and a fresh idempotency key; a stale
revision is refused, so read again rather than retrying blind. Free checks run with
`citizen runs start SUITE --target-kind installed --target-ref REPO --param root=REPO --json`;
`citizen runs catalog --json` lists them. Evals, replays and native acceptance have their own
`citizen runs eval|replay|native` actions. `--request FILE` (or `-` for stdin) takes the JSON body
the Studio sends; [requests.md](requests.md) gives the shape of each, with an example. A start
sends back the `request` its preview or plan returned, unchanged, with that preview's
`confirmation_token`, never the body you previewed with.
`citizen usage --json`, `citizen activity --json` and `citizen runs evidence RUN_ID ARTIFACT --json`
read what a run spent, what changed and what a run left behind.

## What the CLI will not skip, and neither do you

- **Spend is the user's call.** A paid preview prints the estimate, the caps and a one-use
  confirmation token. Show the user the estimate and the caps, and start only on their go for that
  exact preview. A changed request needs a new preview; a token is never reused or guessed.
- **Apply only a reviewed revision, on the user's go.** `draft apply` takes the revision
  `draft review` showed and refuses a draft changed since. Applying changes the live harness every
  later session runs under: show the review's files, keys and checks and wait for an explicit go.
- **A refusal is an answer.** A refused check, a stale revision, a busy target or an underpowered
  test is the result to report. Never work around it with another command, a hand edit of a draft
  or of the configuration, or `--via-studio`, which only the Studio passes.
- **A test verdict is exploratory unless it was registered first.** Report it with the evidence
  note the JSON carries.

## When to open the Studio for the user

Open it when the user has to see or decide something: a review before apply, a comparison or a
test verdict, a spend estimate, or a draft they asked to look at. Run

```bash
citizen studio --detach --json
```

It opens the Studio in the user's browser, or reuses the one running, and prints an object whose
`url` is the address. Hand over that `url` and say which page to look at, such as the draft's
review. Do not send a screenshot or a description of the page in its place; the page is the
current state and a description goes stale. `citizen studio status --json` reports whether one is
running and its `url`; `citizen studio stop --json` stops it.

Stay headless when nobody needs to look: a loop of saves, checks and free runs, or work the user
asked to see only as a result.

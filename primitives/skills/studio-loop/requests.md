# What `--request` holds

Each `citizen runs GROUP ACTION --request FILE --json` takes the body its Studio route takes: an
object with exactly the keys shown, no more. A test checks every example here against the route
that answers it. Values in angle brackets come from an earlier answer; copy them unchanged.

## Replays

`citizen runs replay catalog --json` takes no request; it lists tasks, packs and target kinds.

`citizen runs replay preview --request FILE --json`, two targets and the run's size and caps:

```json
{"request": {"targets": [{"kind": "branch", "ref": "main"}, {"kind": "draft", "ref": "my-draft"}],
             "model": "claude-test", "repetitions": 1, "tasks": ["link-alias"],
             "max_budget_usd": "2", "spend_cap_usd": "20", "pre_registration": null}}
```

`"pack": {"name": NAME, "digest": DIGEST}` from the catalog may be added to the inner request. A
`release` target needs `pre_registration` to name the committed pre-registration it runs under.

`citizen runs replay start --request FILE --json`, the preview's resolved request and its token:

```json
{"request": "<the preview's request object>", "confirmation_token": "<the preview's confirmation_token>"}
```

`citizen runs replay result --request FILE --json`:

```json
{"run_id": "<the start's run_id>"}
```

## Eval tiers

`citizen runs eval catalog --json` takes no request; it lists the free and paid tiers.

`citizen runs eval run --request FILE --json`, a free tier; `raw` is only for `rule-detection`:

```json
{"suite": "rule-detection", "raw": "/absolute/path/to/a/saved/raw/directory"}
```

`citizen runs eval preview --request FILE --json`, a paid tier; `unit` is only for `unit-eval`:

```json
{"request": {"suite": "unit-eval", "target": {"kind": "draft", "ref": "my-draft"},
             "unit": "rules.cache-hygiene", "max_budget_usd": "1", "spend_cap_usd": "5"}}
```

`citizen runs eval start --request FILE --json`:

```json
{"request": "<the preview's request object>", "confirmation_token": "<the preview's confirmation_token>"}
```

`citizen runs eval result --request FILE --json`:

```json
{"run_id": "<the start's run_id>"}
```

## Native acceptance

`citizen runs native catalog --json` takes no request; its `initial` object is a selection to start
from. Choose the client, exactly three cases and the model; leave `source_commit` empty, as the Studio does: the
target's commit is resolved at launch, and the start's answer carries it for progress and retry.

`citizen runs native preview --request FILE --json`:

```json
{"selection": {"cases": ["installation", "stance-switch", "custom-stance"],
               "client": "claude-code-cli-macos",
               "model": "claude-haiku-4-5", "progress_id": "<the catalog's initial progress_id>",
               "retry_case": "", "retry_source": "", "source_commit": "",
               "target_kind": "installed", "target_ref": "current"},
 "spend": {"max_budget_usd": "1", "spend_cap_usd": "5", "pricing_source": "api_credit"}}
```

`citizen runs native start --request FILE --json`, the previewed selection and spend:

```json
{"selection": "<the previewed selection>", "spend": "<the previewed spend>",
 "confirmation_token": "<the preview's confirmation_token>"}
```

`citizen runs native progress --request FILE --json`:

```json
{"selection": "<the started selection>"}
```

`citizen runs native retry --request FILE --json`, one failed case:

```json
{"selection": "<the started selection>", "case": "installation"}
```

## Draft tests

`citizen runs draft-test plan --request FILE --json`, the draft, the form and the effect to detect;
add `"registration": "<the id citizen draft test --register returned>"` to run as registered:

```json
{"draft": "my-draft",
 "request": {"model": "claude-test", "repetitions": 3, "tasks": ["link-alias"],
             "max_budget_usd": "2", "spend_cap_usd": "20",
             "pack": {"name": "<pack name>", "digest": "<pack digest>"}},
 "effect": 0.1, "cv": null}
```

`citizen runs draft-test start --request FILE --json`, the plan's `preview.request` and token, and
the same `registration` if the plan named one:

```json
{"draft": "my-draft", "request": "<the plan's preview.request object>",
 "confirmation_token": "<the plan's preview.confirmation_token>", "effect": 0.1, "cv": null}
```

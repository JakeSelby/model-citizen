# Exporting the ledger

The usage ledger is a local JSONL file and that is deliberate: it is written before anything is
sent anywhere, it survives a backend being down, and it can be re-read. Export is a copy of it,
for dashboards, SQL and a team view. It is **off by default**; with it off no network code runs
and the hook behaves exactly as it did before this existed.

**The model, in one line: the ledger is the record, a backend is a rebuildable copy of it.** Most
backends expire data by default — the reference endpoint this was tested against ships a 30-day
TTL — so history outside the retention window lives in the ledger and is put back by replay.

What is recorded, and where it comes from, is [usage.md](usage.md).

## Turning it on

```json
{
  "telemetry": {
    "export": "otlp",
    "endpoint": "http://localhost:4318",
    "headers_env": "HARNESS_OTLP_HEADERS",
    "headers_file": "~/.config/agent-harness/otlp-headers",
    "labels": { "deployment.environment": "laptop" }
  }
}
```

- `export` is `off` or `otlp`. Any other value stops `bin/citizen sync` rather than silently
  exporting nothing.
- `endpoint` is the base URL of **any OTLP/HTTP endpoint**; `/v1/logs` is appended. No vendor is
  required and none is named in the code.
- `labels` are attributes added to every record — the place for an environment or a host name.
- `native` asks a runtime to export **its own** telemetry to the same endpoint. `true` is every
  runtime, `false` is none, and a list — `["claude-code"]`, `["codex"]` — names the ones it
  applies to; any other runtime name stops `bin/citizen sync` with the names it knows. Off by
  default, and a separate decision from `export`: see [Native pass-through](#native-pass-through)
  before turning it on, because the runtimes attach identifiers the ledger does not, and because
  only Claude Code can reach an endpoint that requires a header.

`bin/citizen doctor` prints one line for this: the mode, the endpoint's scheme and host, and the
**names** of the headers it resolved.

## The decision log switch

One more key lives in the same block and sends nothing anywhere:

```json
{ "telemetry": { "decisions": false } }
```

`decisions` governs `~/.local/state/agent-harness/decisions.jsonl`, the local record of what
each hook decided and how it turned out — [usage.md](usage.md#the-decision-log) describes the
rows and the report. It defaults to **on**, like the usage ledger beside it, because a label
is only worth having from the day the hook starts writing it; set it to `false` and no row, no
file and no directory is written. It is no part of `export`: decision rows are never sent to an
endpoint, whatever `export` is set to, and turning export on does not turn this on or off.

Unlike a ledger row, a decision row holds the text the hook judged — a command, or the head of
a brief — capped at 2 KiB, which is the one reason to turn it off on a shared machine.

### Retention

```json
{ "telemetry": { "decision_log_max_bytes": 8388608, "decision_log_keep": 3 } }
```

The log rotates by size. When a write finds `decisions.jsonl` at `decision_log_max_bytes` (8 MiB
by default) or past it, the file becomes `decisions.jsonl.1`, each older one moves up a number,
and the one past `decision_log_keep` (3 by default) is deleted. A rotated file is never
rewritten, and the reports that read the log (`usage --by decision`, `usage --conflicts` and the
decision evaluation) read the kept files too. `decision_log_max_bytes: 0` turns rotation off;
`decision_log_keep: 0` keeps no rotated file. Lowering `decision_log_keep` deletes the files
numbered past it at the next rotation.
What each hook writes to the log is listed in
[runtime-controls.md](runtime-controls.md#what-each-hook-logs).

## The allowed-command sample

```json
{ "telemetry": { "allow_sample_rate": 0 } }
```

`allow_sample_rate` is the one-in-how-many: **20 by default**, so one distinct command in twenty
of those the harness allows is written to the decision log as an ungraded negative, and `0`
writes none of them. [usage.md](usage.md#sampled-allows) describes the row, and why the sample
is of distinct commands rather than of invocations.

It is on because the graded rows are all prompts, and a check that may only turn an allow into
an ask cannot be measured for false alarms against prompts alone. The sample is drawn from each
command's own hash rather than from a random draw, so the same commands are sampled on every
machine and a measurement over these rows is reproducible. Only an allow the harness itself
gave is sampled, never a command it left to the runtime to answer. The text of a sampled row is redacted first —
assignment values, credential flags, every secret shape the rule detectors match and the home
directory as `~` — because it is text nobody was prompted about, and the row's hash is over the
redacted text so nothing removed from it can be recovered; a graded row still holds the command
as the user saw it.

Set it to `0` on a machine where a log of commands nobody approved is unwelcome.
`decisions: false` turns it off along with the rest of the log, and nothing here is exported:
a decision row reaches no endpoint whatever `export` is set to.

## The completion claim switch

```json
{ "telemetry": { "completion_claim": true } }
```

This one defaults to **off**. It adds `completion_claim` to a `stop-gate` decision row: the last
2 KiB of the turn's final assistant message, read from the transcript at Stop, with the hash
over the uncapped message — or a null claim beside the reason there is none.
[usage.md](usage.md#the-completion-claim) describes the fields and the reasons. It is what lets
a stop claim be read against the gate result sitting on the same row.

It is off because it is the only place the decision log holds assistant prose, and that is a
different thing to keep on a shared machine from a log of commands. `decisions: false` turns it
off too, since there is no row to put it on. Nothing here is exported either: a decision row
reaches no endpoint whatever `export` is set to.

## Credentials

Headers are read from the named environment variable or the named file and from nowhere else. A
header value written into `config.json` is refused by name, because a configuration file is
backed up, synced and read by every tool that reads the config.

```sh
export HARNESS_OTLP_HEADERS='authorization=<token>,x-scope-orgid=<tenant>'
```

Both forms are accepted, in the variable and in the file: `name=value` per line, or the
comma-separated `name=value` list that `OTEL_EXPORTER_OTLP_HEADERS` uses.

A `headers_file` is refused, with the reason, when it is **inside a git work tree** — one
`git add -A` from being published — or when it is **readable by other users**. Create it at mode
600 outside every repository:

```sh
install -m 600 /dev/null ~/.config/agent-harness/otlp-headers
```

No header value is ever printed, logged or written to an error record. A failure record names
only the endpoint's scheme and host, since a path or a query string can itself carry a token.

## What is sent

One OTLP log record per ledger row, `POST <endpoint>/v1/logs`, `Content-Type: application/json`,
using the standard library only. Logs rather than metrics: a row is an after-the-fact summary
that carries its own timestamps.

- `timeUnixNano` is the row's `ended`, `observedTimeUnixNano` is the moment it was sent. Both are
  decimal **strings**, as the OTLP/JSON mapping requires of a 64-bit integer — so an integer
  attribute travels as `{"intValue": "200"}`, not as a JSON number.
- The **body** is the row as a JSON string, so nothing is lost in translation — everything the
  ledger holds except the `stances` map, which travels as attributes instead. A backend that
  parses a JSON body flattens a nested map into dotted keys of its own, and a body carrying
  `stances` landed a second copy of every stance beside the attributes below.
- **Attributes** are the row's flat scalar fields — a null is omitted rather than sent as empty —
  plus `harness.row_key`, `harness.exported_at`, `harness.version`, `harness.usd` and
  `harness.price_as_of` on a priced
  row, one `harness.<dimension>` per recorded stance, and any configured labels. A stance is
  exported **once**. A nested map (`days`, `by_model`, `rules`, `counts`) stays in the body:
  attribute sets are flat, and a hundred per-day slices would be a hundred columns.
- A **`kind: "decision"` row** — one decision-provider call, see [usage](usage.md) — carries its
  own fields under `harness.decision.*`, its price included as `harness.decision.usd`. It
  measures what the harness spent asking a question rather than what a session spent, and
  exported bare its `input`, `output` and `usd` would land in the same columns a session's do,
  where anything summing them would count the question as session spend.
- Resource attributes are `service.name=agent-harness` and the harness version.

`harness.row_key` is the row's identity — session id, runtime, kind, agent id — and is stable
across replays. It is what a reader de-duplicates on. `harness.exported_at` is the moment the
record was sent, as a fixed-width RFC 3339 UTC string — six fractional digits, always `Z` — so
that a backend holding attributes as strings still orders two records for one key correctly.
It is the only difference between a record and its replay, and the rows of one batch may share
one stamp.

### The dollar figure

`harness.usd` is a double and `harness.price_as_of` is the newest `as_of` date among the price
entries that row was priced through. Both come from `policy/prices.json` and your own `prices`
overrides, through the same code `bin/citizen usage` prices with — `policy/hooks/pricing.py`, which
the CLI and the hook each load rather than either one reimplementing it.

- **A list-price API equivalent, fixed at export time.** It is what the tokens would cost at the
  published rates on the date stamped beside them — not an invoice, and not what a subscription
  charged. A replay after a price change re-stamps both attributes at the new rates, so read the
  newest record per key rather than an average across replays.
- **An unpriced row carries neither attribute** — never a zero. A row with an unknown model, a
  session that switched models with no `by_model` breakdown, or a `partial` row is priced by
  nobody, and a zero would say it was free.
- **A Claude Code session's figure already includes its subagents**, exactly as `bin/citizen usage`
  reports it: the subagents' tokens are priced at their own models and added to the parent's.
  Their rows are exported priced too, for per-role reporting, so **never sum a session row and
  its subagent rows** — filter on `kind` first. A Codex subagent row and a role-run worker row
  are each priced alone, because no session row holds their tokens.
- Pricing never costs the export anything: a missing price file or a malformed override leaves
  the row exported without dollars and changes neither the hook's exit status nor the session's.

## Where it runs, and what a dead endpoint costs

Export happens in the **detached worker** the `SessionEnd` hook spawns, after the row is already
in the ledger. One attempt, a two-second timeout, no retry. A collector that is down, slow or
misconfigured costs one line in `~/.local/state/agent-harness/usage.errors.jsonl` — the time, the
error class, the row count and the endpoint's host — and changes neither the hook's exit status
nor the session's.

A collector that is not listening at all, a refused or unreachable connection, also opens a
backoff window in `~/.local/state/agent-harness/export.backoff.json`: a minute after the first
failure, doubling with each one after it up to six hours. Until it closes, a session end tries
nothing and writes no error line; the rows stay in the ledger for a [replay](#replay), which is
never held back. Any answer from the endpoint, even an error status, clears the window.

## Replay

```sh
bin/citizen usage export --since 2026-09-01                    # everything since that day
bin/citizen usage export --since 2026-09-01 --until 2026-09-07 # one week
bin/citizen usage export --since 2026-09-01 --dry-run          # count it, connect to nothing
```

Rows are re-sent in batches; the command prints how many were sent and how many failed, and
exits non-zero if any batch failed. Failures stay in the errors file and the same window can be
re-run. This is how a backend is backfilled after it is created, rebuilt after it is lost, and
repaired after an outage — the capability a live runtime telemetry stream does not have.

## Native pass-through

The ledger is one row per session, written after the fact. Each runtime can also export its own
live telemetry — Claude Code counts tokens by type and reports a dollar figure per API call;
Codex counts tokens, turn cost, tool calls and API calls. `"native": true` makes `bin/citizen sync`
write that configuration, pointing both runtimes at the same `endpoint`. It is off by default
and turning it on is a decision to read the two warnings below first.

```json
{ "telemetry": { "export": "otlp", "endpoint": "http://localhost:4318",
                 "headers_file": "~/.config/agent-harness/otlp-headers", "native": true } }
```

### Which runtimes can reach an authenticated endpoint

**Claude Code can; Codex cannot.** Claude Code resolves its headers by running the
`otelHeadersHelper` script at run time, so a collector that requires an `authorization` header —
ClickStack takes a bare one — is reachable with no credential in any file. Codex takes header
values in `config.toml` only as literals and offers no environment or command indirection for
them, so the harness writes none; its native export needs an endpoint that accepts this machine
unauthenticated, and against an authenticating collector every Codex session is rejected.

Name the runtimes the endpoint can actually serve:

```json
{ "telemetry": { "export": "otlp", "endpoint": "https://collector.example:4318",
                 "headers_file": "~/.config/agent-harness/otlp-headers",
                 "native": ["claude-code"] } }
```

`sync` then leaves the Codex `[otel]` table exactly as it found it — restoring what was there
before if a previous sync wrote one — and `["codex"]` likewise leaves `~/.claude/settings.json`
untouched. `true` still means both, which is what an unauthenticated local collector wants.

**Read this before pointing it at a hosted endpoint.** Both runtimes attach identifiers the
ledger export never sends. Claude Code puts `user.email`, `user.account_uuid`, `user.account_id`,
`user.id`, `organization.id` and `session.id` on its datapoints; its own switches trim some of
them — `OTEL_METRICS_INCLUDE_ACCOUNT_UUID` and `OTEL_METRICS_INCLUDE_SESSION_ID` both default to
`true`, `OTEL_METRICS_INCLUDE_VERSION`, `OTEL_METRICS_INCLUDE_ENTRYPOINT` and
`OTEL_METRICS_INCLUDE_REPOSITORY` to `false`. The harness sets none of them, so adding one to
`env` yourself is yours to keep: `sync` owns the six variables below and no others. Prompt and
response logging stays at each runtime's default, which is off.

### What `sync` writes

**Claude Code**, in `env` in `~/.claude/settings.json`: `CLAUDE_CODE_ENABLE_TELEMETRY=1`,
`OTEL_METRICS_EXPORTER=otlp`, `OTEL_LOGS_EXPORTER=otlp`,
`OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`, `OTEL_EXPORTER_OTLP_ENDPOINT` set to the base URL,
and `OTEL_RESOURCE_ATTRIBUTES` carrying `harness.version` and one `harness.<dimension>` per
resolved stance, plus the configured `labels`. With a header source configured it also sets
`otelHeadersHelper` to `~/.claude/hooks/harness/otel-headers.py`, which reads the same file or
variable the exporter reads and prints a JSON object of headers; the runtime re-runs it about
every 29 minutes. No header value is written into the settings file. A variable reaches the
helper only if it reached the runtime that spawned it, so `headers_file` is the source that
works for a desktop-launched client.

**Codex**, in `[otel]` in `~/.codex/config.toml`: `exporter` and `metrics_exporter`, both
`otlp-http` with `protocol = "binary"` and the signal-specific URLs `<endpoint>/v1/logs` and
`<endpoint>/v1/metrics`. Both are set on purpose. `metrics_exporter` defaults to Codex's own
first-party sink, and its source keeps an exact-name list of metrics that sink drops
client-side — token usage, turn cost, tool calls, API calls — under the comment *"Metrics
intentionally not sent through Codex's built-in Statsig route. Keep this as an exact-name list
so custom OTLP exporters still receive them."* Setting only `exporter` therefore sends no token
metrics anywhere.

**Codex gets no headers and no labels, and this is a real gap.** `[otel]` takes a literal header
map in `config.toml` and offers no environment or command indirection for it, so writing one
would put a credential in a configuration file — exactly what the rules above refuse. Native
Codex export needs an endpoint that accepts unauthenticated traffic from this machine, or one
fronted by something the user authenticates. There is also no config key for metric labels:
Codex builds its resource with the SDK's default builder, so `OTEL_RESOURCE_ATTRIBUTES` from the
process environment is honoured, but `sync` cannot put it there, and a Codex launched from a
desktop or an editor may inherit no shell environment at all. *Unverified:* both statements come
from the configuration reference and a source read, not from a run.

### Labels are frozen at sync time

They are computed when `sync` runs, not per session. Switch a stance without re-syncing and the
native stream is labelled with the old variant until the next `sync` — **the ledger row is still
right**, because it records the stances the session actually ran under. `bin/citizen doctor` says
whether the written labels match the current version and stances. A label whose name or value
carries a comma, an equals sign or whitespace cannot travel in `OTEL_RESOURCE_ATTRIBUTES`, which
has no escape the runtimes agree on: it is left off the native labels, named in the output of
`sync` and `doctor`, and still sent with the ledger export.

### What ownership means here

`sync` records every key it writes, so it can update it and take it back out.

- **A variable you set is never touched.** Ownership is per variable, not over `env`.
- **A managed key already holding something the harness did not write is left alone and
  reported**, in `sync` and in `doctor`. Remove it to let `sync` manage it. A key already holding
  exactly what the harness would write is taken over, since there is nothing to lose.
- **`"native": false` puts back what each key held before**, and removes the rest. An empty
  `"env": {}` can remain where the harness created the map; it is inert. Dropping one runtime
  from the list is the same operation for that runtime alone and leaves the other in place.
- `bin/citizen sync --dry-run` prints the pass-through lines only when the key is on.

## De-duplicating an at-least-once stream

A replay re-sends rows the backend may already hold, so read the newest record per key rather
than counting rows. Order on `harness.exported_at` and on nothing else: the OTLP observed time
is **dropped on ingest** by the ClickHouse exporter, and `Timestamp` is the row's own `ended`,
which is identical across replays. In a ClickHouse-style schema, where OTLP log attributes land
in a `LogAttributes` map:

```sql
SELECT
    LogAttributes['harness.row_key']                                  AS row_key,
    argMax(Body, LogAttributes['harness.exported_at'])                AS row,
    argMax(toFloat64OrNull(LogAttributes['harness.usd']),
           LogAttributes['harness.exported_at'])                      AS usd
FROM otel_logs
WHERE ServiceName = 'agent-harness'
  AND Timestamp >= now() - INTERVAL 30 DAY
  AND NOT (LogAttributes['kind'] = 'subagent' AND LogAttributes['runtime'] != 'codex')
GROUP BY row_key
```

`LogAttributes` is a `Map(String, String)`, so every attribute read out of it is text: the
`argMax` above compares the export stamps lexically, which is exactly why they are fixed width,
and `harness.usd` needs `toFloat64OrNull` before it can be summed — an unpriced row has no such
key, and the empty string it yields becomes a null rather than a zero.

The same shape works anywhere: group by `harness.row_key`, keep the record with the greatest
export time. Because a replay carries the row as it stands in the ledger **now**, the newest
copy is also the corrected one when a `--rescan` has since improved it — and the newest
`harness.usd` is the one priced at the rates in force when it was last sent.

The `kind` filter is the other half of not double counting: a Claude Code session's dollars
already contain its subagents', so a total over every row would bill them twice. A Codex
subagent is the other way round — its tokens are in no row but its own — which is why the filter
names the runtime too. This is the rule `bin/citizen usage` applies; [usage.md](usage.md) says why.
Spend by role is the same query kept to the subagent and worker rows instead.

## Reference recipe: ClickStack

`endpoint` takes **any** OTLP/HTTP endpoint. This is one backend that was set up and measured
end to end, written down so the first person to point the exporter somewhere real does not have
to rediscover the setup steps. It is an example, not a requirement and not an endorsement:
anything that speaks OTLP/HTTP works, and nothing in the harness names a vendor.

ClickStack is the ClickHouse observability stack — a HyperDX UI over ClickHouse, fed by an
OpenTelemetry Collector. The all-in-one image runs the three of them in one container, which is
why it suits a laptop.

### Run it

```bash
docker run -d --name clickstack -p 8080:8080 -p 4317:4317 -p 4318:4318 -v clickstack-db:/data/db -v clickstack-ch:/var/lib/clickhouse -v clickstack-chlogs:/var/log/clickhouse-server clickhouse/clickstack-all-in-one
```

8080 is the UI and the HTTP API, 4317 is OTLP/gRPC and 4318 is OTLP/HTTP — the port the
`endpoint` above points at. The three volumes are the whole of the state: `/data/db` is the
MongoDB that holds the user, the team and the ingestion key, and the other two are ClickHouse's
data and logs. **The volumes are what persists**, not the container: after a `docker restart`,
and again after the container was removed and a fresh one run on the same three volumes, the
data was intact and the same ingestion key was still accepted, because the account and the key
live in `/data/db`. Without them a recreated container starts empty and the key is regenerated.
Measured idle on one laptop: about 795 MiB resident, about 1.5 GiB shortly after ingest.

This recipe deliberately contains no command that creates an account, writes down a password, or
removes a container or a volume. The first is a browser step, the second belongs in a file only
you can read, and the third is destructive and yours to type.

### Two manual steps before any data is accepted

**First, create the first user** in the UI at `http://localhost:8080`. The OTLP receivers on
4317 and 4318 stay closed until that account exists, because the collector is waiting on its
configuration from the app. Until then an exporter sees a connection that refuses to talk, and
nothing in the harness's error record will explain why.

**Second, send the team's ingestion key** — shown in the UI under the team settings — as a bare
`authorization` header on every request. It is the key on its own, with no `Bearer` prefix.
Without it the receiver answers:

```text
401 missing or empty authorization header: Authorization
```

So `headers_file` is not optional for this backend. One mode-600 file outside every repository
serves both directions of the integration: the ledger exporter reads it as `headers_file`, and
with `"native": true` the `otelHeadersHelper` script reads the same file for Claude Code.

```bash
printf 'authorization=%s\n' '<ingestion-key>' > ~/.config/agent-harness/otlp-headers
```

Create the file at mode 600 first, as under [Credentials](#credentials); the shell redirect above
does not change an existing file's mode.

**Codex cannot use this backend through `sync`.** `[otel]` takes header values only as literals
in `config.toml`, so there is no way to give Codex the key without writing it into a
configuration file — see [Native pass-through](#native-pass-through), which states that gap and
what it costs. Set `"native": ["claude-code"]` so `sync` writes no Codex `[otel]` table against
a backend that would reject every request it makes. Claude Code's native export is unaffected,
and so is the ledger exporter.

Native Claude Code export also carries `user.email`, the account and organization ids and the
session id on every datapoint. On a container bound to localhost that is your own machine
talking to itself; read the warning under [Native pass-through](#native-pass-through) before
pointing the same configuration at anything hosted.

### Retention is 30 days by default

Every OpenTelemetry table the collector creates ships with its own 30-day TTL. Ten tables
carried one on the image measured: the logs and traces tables, the five metrics tables, the two
`*_kv_rollup_15m` rollups and `hyperdx_sessions`. This is the ledger-as-record argument made
concrete: the backend forgets, and `bin/citizen usage export --since <date>` puts the window back.

List what is actually there, with the TTL each table carries, rather than trusting a list in a
document:

```sql
SELECT name, engine FROM system.tables WHERE database = 'default' AND create_table_query LIKE '%TTL%' ORDER BY name
```

Then raise each one you care about. The TTL expression names that table's own time column — the
log and trace tables use `Timestamp`, the metric tables use `TimeUnix` — so copy the expression
out of the table's own `create_table_query` and change only the interval:

```sql
ALTER TABLE default.otel_logs MODIFY TTL toDateTime(Timestamp) + toIntervalDay(365)
```

A `MODIFY TTL` on a table that already holds data schedules a materialization; it does not
resurrect parts that have already expired.

### The dashboard

[`telemetry/clickstack-dashboard-native-cost.json`](telemetry/clickstack-dashboard-native-cost.json)
is ten tiles of raw SQL over `otel_metrics_sum`, reading the native Claude Code cost and token
metrics: spend, sessions, the subagent share of spend, cache hit rate, spend over time by model
and by harness version, spend by agent × model × effort, spend by cost variant and delegation
stance, tokens by type, and the most expensive sessions. The SQL is this repository's own, over
the standard OpenTelemetry tables; no dashboard, query or documentation is copied from the
upstream project.

The HTTP API answers under `/api/api/v2/` on the **UI** port, not the OTLP port — the doubled
`api` is not a typo. Send `/api/v2/...` instead and the proxy strips one `/api`, leaving the
backend to answer `404 Cannot GET /v2/dashboards`.

**The API key and the ingestion key are two different secrets.** The ingestion key is sent as a
bare `authorization` header to the OTLP ports; the HTTP API takes a *personal API key*, created
separately in the UI, as `Authorization: Bearer <key>` on the UI port. Neither works in the
other's place. Keep the API key in its own mode-600 file outside every repository and read it
inside the header argument rather than exporting it into the environment.

Each tile carries a `connectionId`, which is instance-specific and ships as the placeholder
`REPLACE_WITH_CONNECTION_ID`. Look yours up:

```bash
curl -s http://localhost:8080/api/api/v2/connections -H "Authorization: Bearer $(cat ~/.config/agent-harness/clickstack-api-key)"
```

Substitute it, keeping the original file intact:

```bash
sed 's/REPLACE_WITH_CONNECTION_ID/<connection-id>/g' docs/telemetry/clickstack-dashboard-native-cost.json > "$HOME/clickstack-dashboard.json"
```

Dry-run it before creating anything; a good definition comes back as
`{"valid": true, "errors": [], "normalized": …}`:

```bash
curl -s -X POST http://localhost:8080/api/api/v2/dashboards/validate -H "Authorization: Bearer $(cat ~/.config/agent-harness/clickstack-api-key)" -H 'content-type: application/json' --data-binary "@$HOME/clickstack-dashboard.json"
```

Then create it:

```bash
curl -s -X POST http://localhost:8080/api/api/v2/dashboards -H "Authorization: Bearer $(cat ~/.config/agent-harness/clickstack-api-key)" -H 'content-type: application/json' --data-binary "@$HOME/clickstack-dashboard.json"
```

### Licences, and what this repository ships

The HyperDX app is MIT. ClickHouse and the OpenTelemetry Collector are Apache-2.0. The all-in-one
image also contains MongoDB, which is under the SSPL — a licence this project would not ship
under, and does not need to, because you download the image from its publisher. This repository
distributes none of it, vendors none of it, and copies none of its dashboards or documentation.
Read the image's own terms before running it anywhere but your own machine.

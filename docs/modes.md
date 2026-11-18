# Modes

A mode is a named selection bundle: one file that sets many units at once, so the harness can be
put into a known shape without setting each unit by hand. Any key you typed yourself still wins.
The selection document a mode is written in is described in
[preferences.md](preferences.md#the-selection-document).

## Selecting a mode

```sh
citizen config set mode minimal     # every session, from ~/.config/agent-harness/config.json
citizen sync
HARNESS_MODE=minimal claude          # one session, without changing the stored selection
```

A project or session file may also name a `mode`; the highest layer that names one picks it.
`citizen selection` prints `mode=<name> (<layer>)`, reports each unit the mode set with the
source `mode:<name>`, and lists every mode key a higher layer overrode as `shadowed`. Having no
mode is the same as selecting `full`.

## Precedence

Lowest first; each layer overrides the ones before it:

1. `default`: the built-in stance variants, and `on` for every switch.
2. `init`: a stance `citizen init` wrote as a default, which you accepted with Enter.
3. `mode:<name>`: the selected mode.
4. `user`: every other key in `config.json`, the ones you typed.
5. `project`, then 6. `session`: the files `HARNESS_PROJECT_CONFIG` and
   `HARNESS_SESSION_CONFIG` name, then `HARNESS_MODE` and `HARNESS_STANCE_*`.

The `init` layer is how a mode takes effect after a default `citizen init`. `config.json` records
the stances init chose for you, with the value it wrote, under `init_defaults`. A stance counts as
init's only while it still holds that value: `citizen config set stances.NAME`, or editing the
value in `config.json`, makes it yours, above any mode. A config written before modes existed
has no `init_defaults`, so every stance in it counts as typed: delete a stance from `stances`, or
from both lists, to let a mode set it.

## Shipped modes

### `full`

Everything the harness ships, at its defaults. It changes nothing.

### `minimal`

Enforcement without ceremony. Every hook stays on, and the `cost`, `delegation`, `autonomy` and
`decisions` stances keep their values, since how a question reaches you is not ceremony. It
changes:

- Stances: `licensing` off, `build-vs-buy` off, `commits` off, `plan-ceremony` light,
  `testing` off, `voice` off.
- Workflows: `build` off, `close-out` off, `handoff` off, `land` off, `plan` off,
  `research` off, `review` off.

`sync` projects both: each stance links its chosen variant, and a workflow that is `off` has no
command installed.

## Writing a mode

A mode is `modes/<name>.json` in `primitives/` or in a root listed in `primitive_roots`:

```json
{"schema_version": 1,
 "description": "What this mode is for, in one sentence.",
 "stances": {"testing": "pragmatic"},
 "rules": {"decisions-and-plans": "off"}}
```

Beside `schema_version` and `description` it may carry selection kinds only: no `mode`,
`sources`, identity, permissions or other user-owned key. Each unit it names must be installed.
A name two roots both define is refused. `citizen config set mode`, `citizen selection` and
`citizen sync` all refuse a mode that breaks one of these rules before anything is written; a hook
runs without it. A mode may switch off a core hook (`brief-guard`, `grade-bash`,
`neutralize-tool-output` or `stop-gate`) only when `config.json` sets
`core_switches_acknowledged` to `true`, as any layer may. With it set, the mode applies with that
core hook off. Without it, those three commands refuse the mode, and a hook applies the rest of the
mode with that core hook still on.

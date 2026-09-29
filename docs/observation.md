# Observation recorder

Observation is off by default. To register the separate recorder for the native hook events in
each managed client, run:

```sh
citizen config set observation.enabled true
citizen sync
```

Accept native hook trust again if the client asks after its hook configuration changes. The
recorder adds one process per event. It emits no context, decisions or output to the client; its
rows contain event identifiers and a profile fingerprint, never prompt or tool bodies.

For ordinary sessions, rows go to the local `observation.jsonl` ledger beside the other local
usage ledgers. Recorder failures go to `observation.errors.jsonl` and do not block the session.

Live cost replays are separate. Both benchmark arms contain the same observer bytes and native
hook registration. The runner mounts a fresh output directory for each preflight and scored session, outside
protected host paths. After the session, it retains both streams under the tag's `observations/`
directory; no arm can read or change another session's files. It records the real row and
error counts on the result; missing or errored collection invalidates that attempt. These files
never use or append to the ordinary local ledger.

To remove only the recorder registrations, set `observation.enabled` to `false` and sync again.
Your own hooks remain. Uninstall also removes the managed recorder registrations.

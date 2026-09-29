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

To remove only the recorder registrations, set `observation.enabled` to `false` and sync again.
Your own hooks remain. Uninstall also removes the managed recorder registrations.

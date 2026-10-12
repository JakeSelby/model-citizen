# Streams for the context-reset case

Five `claude -p --output-format stream-json --verbose` streams that `case_context_reset` reads.
`python3 make_streams.py --from <recorded stream>` writes every file here; edit it, never a stream.

**Derived from a recorded run.** `passing` is the AH-SP020 spike's own stream (Claude Code
2.1.280, `claude-sonnet-5`, a twenty-item checklist, six automatic compactions near 56,500 tokens),
with its event order, types, subtypes, hook names and compaction metadata kept as recorded. Every
id, uuid, request id, timestamp, path, signature, file text, summary and hook output is rewritten;
each item's `Read` is renamed to its ledger and each item's write to a `printf` into its done file;
the SessionStart(compact) hook output carries the harness's reseed line. The recorded stream holds
the spike's own content and is not committed.

**Hand-made edits of `passing`**, one each, written by the same script:

- `thrash` — cut after the second compaction and ended with Claude Code's "Autocompact is
  thrashing" error result.
- `refusal` — the final reply refuses, with `stop_reason: refusal`.
- `duplicate` — item 09's write made again right after the second compaction.
- `no-compaction` — every compaction boundary, status, SessionStart(compact) hook event and summary
  taken out.

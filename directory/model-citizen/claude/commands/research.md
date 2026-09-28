---
description: Answer a research question with parallel read-only gatherers and one synthesized digest.
argument-hint: <question>
---

# Research

The question: $ARGUMENTS

Load the `transcript-hygiene` and `api-verification` skills in full before you spawn anything.
They carry the return caps and the shared search budget this command depends on.

1. **Split the question into at most three independent dimensions.** Independent means one
   gatherer's answer never changes another's brief. Fewer is better. If the question is one
   dependent chain, answer it inline and spawn nothing.
2. **Route each dimension by where its evidence lives.** A dimension answered from files or a
   repository goes to an isolated `gatherer` worker — `citizen role run gatherer --workspace
   <repo> --prompt-file <brief>` — which is offline by design and holds only `Read`, `Grep` and
   `Glob`; grant extra input directories with `--read-dir`. A dimension that needs the live web
   goes to an in-session band worker instead (`worker-a` for one search or one fetch, `worker-b`
   when the finding has to be weighed), spawned in parallel in a single message. Never hand a
   web dimension to a `gatherer`: it has no web tools and will return the brief unstarted.
   Every brief, either way, names the exact question for that dimension, the files or sources to
   start from, a 400-word return cap, the rule that detail goes to a scratchpad file while only
   the verdict and the decision-changing findings come back, that dimension's share of the
   session search budget, and what the worker must not decide.
3. **Synthesize in your own words.** Never paste, quote or lightly edit a report. A finding
   that does not change the answer does not appear at all. Where two dimensions disagree,
   adjudicate it yourself and say which source won and why.
4. **Verify anything load-bearing** before you rely on it: prove a filter bites, spot-check one
   returned record, count swallowed errors separately from empty results.

Report as a brief: two or three sentences of bottom line, then up to five findings with the numbers
behind them and what they mean for the reader. When workers ran, end with the scratchpad file
paths they wrote and the worker ids `citizen role status` will show, one per line. An
isolated `gatherer` writes nothing itself: save its returned detail to a scratchpad file yourself
before you synthesize.

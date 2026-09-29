---
description: Review the branch's diff in two fresh contexts — scope against the spec, then quality — and report findings only.
argument-hint: "[base ref, default main] [optional spec: issue number, plan path or PR number]"
---

# Review

Read $ARGUMENTS as a base ref and a spec source. The base is a ref, `main` when absent. The spec
is an issue number, a path to a plan file, or a pull request number; when none is given, use
the branch name and the last commit message as inferred scope.

1. **Establish the diff, before spawning anything.** Run `git diff --stat <base>...HEAD` and
   `git diff <base>...HEAD`. If the base does not resolve, say so and stop rather than review
   against the wrong tree. Save the full patch to a review artifact outside tracked source and
   record the repository path and resolved base/head SHAs alongside it. Outside a git repository there is no diff at all: say so, and offer to
   review named files or a pasted patch instead.
2. **Materialize the spec:** fetch an issue or PR body into a local input file; retain an existing plan-file path. For the inferred-scope fallback, write the branch name and last commit message into a local input file, explicitly labelled inferred scope. Pass that file, never an `infer` sentinel.
   **Spawn `spec-reviewer` first**, in its own fresh context, with that input, the absolute diff-artifact path, repository path and base/head SHAs.
   It reports only what the diff does that nothing asked for, what was asked for and is missing,
   and which stated acceptance criteria the diff does not prove. Tell it to leave every
   correctness, style and test-quality question to the second pass.
3. **Then spawn `reviewer`**, in a second fresh context that has seen neither the work nor the
   scope pass. Pass the same absolute diff artifact, repository path and base/head SHAs. Brief it for findings only: no restatement of what it read, no summary of the
   change, no praise. Each finding carries a severity, a `file:line`, what is wrong and why it
   matters. Tell it to treat a missing test or an untested error path as a finding.
4. **Relay nothing verbatim.** Check every finding from either pass against the code yourself
   before it reaches the user and drop the ones that do not hold. Where you disagree, say so
   with your reasoning rather than passing the finding through.
5. **Report two sections** — **Scope** first, then **Quality** — each a list ranked by severity,
   each item a bold-led bullet carrying its `file:line`. State the verdict first. A section with
   nothing surviving is a single line saying so.

Two contexts is the point: a reviewer that has just read the code for bugs rationalizes scope
creep, so the scope pass runs before it and never sees what it found.

Make no edits. This command reads, it does not fix. Hand the findings over and let the user
decide which ones to act on — `/build` is where changes happen.

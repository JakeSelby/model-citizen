---
description: Write the repository handoff file for the next session, and record any durable learning.
argument-hint: [note to carry into the next session]
---

# Handoff

Note to carry over: $ARGUMENTS

1. **Overwrite `.agent-harness/progress.md`** at the repository root, or here when there is no
   repository; never append: session start reads its first eighty lines. Use these headings:

   ```markdown
   # Handoff <ISO date>

   ## Done
   ## Open
   ## Next command
   ## Decisions needed
   ## Learnings
   ```

   Fill them from this session, not the file you replace. **Done** and **Open** are short bullets
   naming files and commands; **Next command** is one runnable line; **Decisions needed** is what
   only the user can settle; **Learnings** is what you wish you had known at the start.
2. **Save the shared task contract.** After `citizen task show`, save a JSON input of `objective`,
   `next_steps`, `decisions` and `artifacts` (plus a planning framework's checkout and baseline)
   with `citizen task save --input <file> --runtime <runtime> --revision <current-revision>`. The
   next runtime rechecks the tree; a handoff never transfers an approval.
3. **Promote anything durable** (a fix that generalizes, a trap, a working command) as a dated
   bullet of a sentence or two in `docs/solutions/<yyyy-mm-dd>-<slug>.md` under the repository root,
   or `.agent-harness/` when there is no repository; nothing durable, no file.

4. **Task end, from `/land` or `/close-out`.** Skip it in a `/land` run by `/close-out`, from any
   step. Run steps 1 and 2 unless `/close-out` step 5 just ran them, then clear only if all hold:
   a Claude desktop session (`clear_session` available) inside a repository; both writes succeeded
   (`task save` exited 0); zero open items: no review thread, pending approval or release card,
   unmerged pull request, refused or dirty worktree, stale checkout, parked decision, unanswered
   question, mid-task user work or `/close-out` archive. Say in one line that the context will be
   cleared and the handoff reseeds the next prompt, then call `clear_session` with session_id
   `self` as the turn's final action. It clears at turn end, nothing from before carries over, a
   queued user message cancels it, and the app asks for approval unless auto or bypass mode is on.
   Otherwise, CLI and Codex included, say why in one line and stop.

End the turn with the two paths, the progress file and any solutions file, before any step 4 clear.
